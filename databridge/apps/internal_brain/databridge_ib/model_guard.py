"""The stand-in model's guardrails: one spend ledger and one set of caps for every model call.

The answer backend (claude.py), the page composer and ``/ib/rewrite`` (views/composer.py) share ONE
guard per process (``ModelGuard.shared``), so the caps hold across all three.

* **A persistent spend ledger outside the demo state.** ``databridge/var/model_guard/spend.json``
  (git-ignored), or the directory in ``IB_MODEL_GUARD_DIR``. It is not under any IB var root, so
  ``demo_up.sh --fresh``, ``demo_day.ps1 reset`` and ``/sim/reset`` never touch it. Append-style:
  ``usd`` and ``calls`` only go up, and every call adds an entry (UTC time, estimated cost, a
  persona hash ``sha256(sub)[:12]``, kind answer/page/rewrite/decide). Written atomically (tmp +
  ``os.replace``).
* **Fail closed.** A missing file reads as zero ONLY when the guard directory itself is new (it is
  then created with an empty ledger). A guard directory without a ledger, or a corrupt, unreadable
  or unwritable ledger blocks every call, and a bad ledger is never overwritten.
* **Caps read from the environment once, at start-up** (``Caps.from_env``): a total cap
  (``IB_MODEL_CAP_USD``, default 11; ``IB_CLAUDE_BUDGET_USD`` is an alias, the lower wins), a daily
  cap (``IB_MODEL_DAILY_CAP_USD``, default 5, per UTC day from the ledger entries), a service-wide
  rate (``IB_MODEL_RPM``, default 20 a minute) and a per-person rate (``IB_MODEL_USER_RPM``, default
  6 a minute; ``IB_CLAUDE_USER_LIMIT`` is an alias, the lower wins). A value that is not a
  non-negative number is 0 (closed). There is no API or UI that changes a cap or the spend: only a
  restart with new settings, or a manual file step (private checklist).

Any cap hit means no model call; the caller falls back (a quoted answer, a page from the rules)
and the user sees the same neutral fallback as for any other model failure.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("databridge_ib.model_guard")

DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]          # .../databridge
DEFAULT_GUARD_DIR = DATABRIDGE_ROOT / "var" / "model_guard"
LEDGER_NAME = "spend.json"
WINDOW_S = 60.0
KINDS = ("answer", "page", "rewrite", "decide")
#: the one message for a refused call (a log line or an exception text; never the provider)
REFUSED = "AI model unavailable: a usage limit was reached"
#: the neutral label the preflight board and /ib/status show the numbers under
LABEL = "Model budget"


def guard_dir() -> Path:
    """``IB_MODEL_GUARD_DIR``, else ``databridge/var/model_guard`` (outside every IB var root)."""
    d = os.environ.get("IB_MODEL_GUARD_DIR")
    return Path(d) if d else DEFAULT_GUARD_DIR


def persona_hash(sub: str) -> str:
    return hashlib.sha256(str(sub or "-").encode("utf-8")).hexdigest()[:12]


def _num(name: str, default: Optional[float]) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        v = float(raw)
    except ValueError:
        log.error("%s is not a number: the cap is closed", name)
        return 0.0
    if not math.isfinite(v) or v < 0:
        log.error("%s is not a non-negative number: the cap is closed", name)
        return 0.0
    return v


def _lowest(*vals: Optional[float], default: float) -> float:
    got = [v for v in vals if v is not None]
    return min(got) if got else default


@dataclass(frozen=True)
class Caps:
    total_usd: float = 11.0
    daily_usd: float = 5.0
    rpm: int = 20
    user_rpm: int = 6

    @classmethod
    def from_env(cls) -> "Caps":
        return cls(
            total_usd=_lowest(_num("IB_MODEL_CAP_USD", None), _num("IB_CLAUDE_BUDGET_USD", None),
                              default=11.0),
            daily_usd=_lowest(_num("IB_MODEL_DAILY_CAP_USD", None), default=5.0),
            rpm=int(_lowest(_num("IB_MODEL_RPM", None), default=20)),
            user_rpm=int(_lowest(_num("IB_MODEL_USER_RPM", None), _num("IB_CLAUDE_USER_LIMIT", None),
                                 default=6)))

    def with_total(self, total_usd: float) -> "Caps":
        return replace(self, total_usd=float(total_usd))


class _Window:
    """At most ``n`` hits per ``window`` seconds per key (in memory)."""

    def __init__(self, n: int, window: float = WINDOW_S, clock: Callable[[], float] = time.monotonic):
        self.n, self.window, self.clock = n, window, clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.n:
                return False
            q.append(now)
            return True


@dataclass
class Spend:
    usd: float
    calls: int
    today_usd: float


@dataclass
class Ticket:
    worst: float
    who: str
    kind: str


class ModelGuard:
    """Caps, rates and the spend ledger. ``admit`` before a call (a ``Ticket``, or a refusal
    code); ``settle`` after it, in a ``finally``."""

    _shared: dict[str, "ModelGuard"] = {}
    _shared_lock = threading.Lock()

    @classmethod
    def shared(cls) -> "ModelGuard":
        """The process's guard for ``guard_dir()``; its caps are read from the environment when it
        is first built (start-up), never again."""
        d = guard_dir()
        key = str(d.resolve())
        with cls._shared_lock:
            g = cls._shared.get(key)
            if g is None:
                g = cls._shared[key] = cls(d, Caps.from_env())
            return g

    def __init__(self, directory: Path, caps: Optional[Caps] = None, *,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic):
        self.dir = Path(directory)
        self.path = self.dir / LEDGER_NAME
        self.caps = caps or Caps.from_env()
        self.now = now
        self._lock = threading.Lock()
        self._reserved = 0.0
        self._global = _Window(self.caps.rpm, WINDOW_S, monotonic)
        self._user = _Window(self.caps.user_rpm, WINDOW_S, monotonic)
        #: a spend that could not be written, or a guard dir that could not be set up: closed
        #: until a restart finds a good ledger
        self._broken = False
        self._init_dir()

    # -- the ledger --------------------------------------------------------------------------
    def _init_dir(self) -> None:
        """A NEW guard dir is created with an empty ledger; an existing one is left as it is."""
        if self.dir.exists():
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=False)
            self._write({"usd": 0.0, "calls": 0, "entries": []})
        except Exception as exc:                                    # noqa: BLE001
            log.error("model guard dir could not be set up: %s", type(exc).__name__)
            self._broken = True

    def _read(self) -> Optional[dict[str, Any]]:
        """The ledger, or None when it is missing, unreadable or corrupt (the guard then fails
        closed and never overwrites it)."""
        try:
            raw = self.path.read_text(encoding="utf-8")
            d = json.loads(raw)
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict) or not isinstance(d.get("entries"), list):
            return None
        try:
            usd, calls = float(d["usd"]), d["calls"]
        except (KeyError, TypeError, ValueError):
            return None
        if (not math.isfinite(usd) or usd < 0 or isinstance(calls, bool)
                or not isinstance(calls, int) or calls < 0):
            return None
        for e in d["entries"]:
            if not (isinstance(e, dict) and isinstance(e.get("at"), str)
                    and isinstance(e.get("usd"), (int, float)) and not isinstance(e.get("usd"), bool)):
                return None
        return d

    def _write(self, d: dict[str, Any]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    def _spend(self, d: dict[str, Any]) -> Spend:
        day = self.now().astimezone(timezone.utc).date().isoformat()
        today = sum(float(e["usd"]) for e in d["entries"] if str(e["at"])[:10] == day)
        return Spend(float(d["usd"]), int(d["calls"]), today)

    # -- the calls ---------------------------------------------------------------------------
    def admit(self, sub: str, kind: str, worst_usd: float) -> tuple[Optional[Ticket], str]:
        """``(ticket, "none")``, or ``(None, code)`` with code ``rate_person``, ``rate_global``,
        ``cap_total``, ``cap_daily`` or ``closed`` (the ledger is bad). No ticket, no call."""
        who = persona_hash(sub)
        if self._broken:                        # closed until a restart: no rate slot is used
            return None, "closed"
        if not self._user.allow(who):
            return None, "rate_person"
        if not self._global.allow("*"):
            return None, "rate_global"
        with self._lock:
            d = None if self._broken else self._read()
            if d is None:
                return None, "closed"
            s = self._spend(d)
            if s.usd + self._reserved + worst_usd > self.caps.total_usd:
                return None, "cap_total"
            if s.today_usd + self._reserved + worst_usd > self.caps.daily_usd:
                return None, "cap_daily"
            self._reserved += worst_usd
            return Ticket(worst_usd, who, kind if kind in KINDS else "answer"), "none"

    def settle(self, ticket: Ticket, actual_usd: float) -> None:
        """Record one call. Raises when the ledger cannot be written (and the guard closes)."""
        with self._lock:
            self._reserved = max(0.0, self._reserved - ticket.worst)
            d = None if self._broken else self._read()
            if d is None:
                log.error("model spend ledger unreadable: the guard stays closed")
                return
            cost = max(0.0, float(actual_usd or 0.0))
            d["usd"] = round(float(d["usd"]) + cost, 6)
            d["calls"] = int(d["calls"]) + 1
            d["entries"].append({"at": self.now().astimezone(timezone.utc).isoformat(timespec="seconds"),
                                 "usd": round(cost, 6), "who": ticket.who, "kind": ticket.kind})
            try:
                self._write(d)
            except Exception:
                self._broken = True             # a spend we could not record: fail closed
                raise

    # -- status (numbers only) ---------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        with self._lock:
            d = None if self._broken else self._read()
            s = self._spend(d) if d is not None else None
        out: dict[str, Any] = {
            "label": LABEL, "total_cap_usd": self.caps.total_usd,
            "daily_cap_usd": self.caps.daily_usd, "rpm": self.caps.rpm,
            "user_rpm": self.caps.user_rpm, "ok": s is not None}
        if s is None:
            out.update(spent_usd=None, today_usd=None, calls=None, available=False)
        else:
            out.update(spent_usd=round(s.usd, 4), today_usd=round(s.today_usd, 4), calls=s.calls,
                       available=s.usd < self.caps.total_usd and s.today_usd < self.caps.daily_usd)
        return out


def budget_status() -> dict[str, Any]:
    """The numbers for /ib/status (admin and compliance only)."""
    return ModelGuard.shared().status()


__all__ = ["Caps", "ModelGuard", "Ticket", "guard_dir", "persona_hash", "budget_status", "REFUSED",
           "LABEL", "DEFAULT_GUARD_DIR"]
