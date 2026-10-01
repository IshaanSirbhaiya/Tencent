"""`TriggerEngine` — the in-process asyncio service that fires agents and adapter agents.

Three trigger kinds (§5.6 `trigger` / `sync`):

* **schedule** — five-field cron in an IANA timezone. The engine checks every `poll_interval`
  seconds; a slot fires once. Slots that passed while the engine was not running (or that are
  older than `grace` when first seen) are **skipped and recorded** — the missed-run policy is
  "skip, write it down", never "catch up in a burst". A long outage is written down EXACTLY: the
  first `missed_record_limit` slots one line each, the rest as one aggregate line with the exact
  count and window (`CronSchedule.count_between`).
* **event** — tails the §5.3 event log (`WALNUT_EVENTS_PATH`) by byte offset. The offset is
  persisted per consumer in `var/events/offsets/<consumer>.json` together with the log's
  IDENTITY (path, file id, a digest of its first bytes), so a rotated, replaced or re-pointed log
  is re-read from 0 rather than resumed at a stale offset. A line that is not a valid event is
  recorded (`rejected_event`, with its byte range) before the offset passes it.
* **manual** — `await engine.fire(spec_id)`, for "run now".

**Only an `active` spec ever runs** — on every path, manual included. An adapter agent runs only
when every connection's crawl plan in its spec is `approved` (the manager re-checks the plan on
disk at sync time too). A workflow agent's declaration is compiled by the agent runtime at run
time (`compile_agent`, no override), so an uncompiled agent cannot run even if it is active.

**Exactly once per trigger, never silently lost** (R-017 #7-#9):

* An idempotency key is CLAIMED before anything runs, with an ``O_CREAT|O_EXCL`` marker file under
  `var/automation/claims/` — atomic across coroutines, engines and processes. Only the claimer
  dispatches. Attempt *n* of a key is its own marker, so a retry is claimed exactly once too.
* The claim is followed by a durable ``pending`` intent line in `firings.jsonl` BEFORE the schedule
  state or the event offset advances. A process that dies mid-dispatch leaves the intent behind;
  after `claim_lease` any engine recovers it and dispatches it again under the SAME key (the
  runner receives `idempotency_key` and `attempt`, so it can dedupe — at-least-once delivery with
  an idempotent key).
* A failed attempt does not consume the trigger: it is retried with exponential backoff up to
  `max_attempts`; only then is it **dead-lettered** — a `dead_lettered` line in `firings.jsonl`
  and a copy in `dead_letter.jsonl`. Success, `no_runner` and dead-lettering are the only terminal
  outcomes.

**Idempotency keys**:

    schedule   <spec_id>:<spec_digest>:schedule:<slot UTC ISO>
    event      <spec_id>:event:<event_id>
    run event  <spec_id>:run:<run_id>:<type>     (run_* events can be emitted by both mcp_ingest
                                                  and adapter_agents for one run — §12)
    manual     <spec_id>:manual:<caller key or uuid>

`spec_digest` is taken over the spec with `status` removed, so pausing and resuming does not
re-fire a slot that already ran, while an edited spec is a new spec.

Dispatch goes through an injectable ``runner(spec, trigger_info)`` (sync or async); the gateway
passes the agent runtime's run function for agents and the adapter-agent manager's sync for
adapter agents.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import inspect
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional, Union

from databridge_workflow_spec import AdapterAgentSpec, SpecError, WorkflowSpec, load_spec

from . import paths
from .cron import CronError, CronSchedule

Spec = Union[WorkflowSpec, AdapterAgentSpec]
Runner = Callable[[Spec, dict], Union[Any, Awaitable[Any]]]

#: Outcomes after which a key never runs again.
TERMINAL = frozenset({"succeeded", "no_runner", "dead_lettered", "coalesced"})
#: Statuses that meant "seen" before retries existed (a line without an `attempt` field).
_LEGACY_SEEN = frozenset({"succeeded", "failed", "dispatched", "no_runner"})
#: Statuses of a firing that is still owed an outcome.
_OPEN = frozenset({"pending", "failed"})
_HEAD_BYTES = 4096


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_us(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def spec_id(spec: Spec) -> str:
    return spec.agent if isinstance(spec, WorkflowSpec) else spec.id


def spec_kind(spec: Spec) -> str:
    return "agent" if isinstance(spec, WorkflowSpec) else "adapter_agent"


def spec_trigger(spec: Spec):
    return spec.trigger if isinstance(spec, WorkflowSpec) else spec.sync


def spec_digest(spec: Spec) -> str:
    data = spec.model_dump(mode="json")
    data.pop("status", None)
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def admission_refusal(spec: Spec) -> Optional[str]:
    """Why ``spec`` may not run now, or None. The single gate every trigger path goes through."""
    if spec.status != "active":
        return f"status is {spec.status}"
    if isinstance(spec, AdapterAgentSpec):
        unapproved = [c for c in spec.connections if spec.plans.get(c) != "approved"]
        if unapproved:
            return ("crawl plan not approved for " + ", ".join(unapproved)
                    + "; an adapter agent syncs only approved plans (P5)")
    return None


# --------------------------------------------------------------------------------------------
# globs and filters
# --------------------------------------------------------------------------------------------
def glob_to_regex(pattern: str) -> re.Pattern:
    """`**` crosses folders, `*` and `?` do not; case-insensitive (folder names are typed by
    people). `**/X/**` matches X at any depth, including the root."""
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append(r"(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif c == "*":
            out.append(r"[^/]*")
            i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile(r"^" + "".join(out) + r"$", re.I)


def _uri_path(uri: str) -> str:
    u = uri.replace("\\", "/")
    u = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", u, flags=re.I)
    return u.lstrip("/")


def event_matches(trigger_event, event: dict) -> bool:
    """Whether a §5.3 event satisfies an `EventTrigger` (source, type, filter)."""
    if event.get("source_id") != trigger_event.source:
        return False
    if event.get("type") not in trigger_event.on:
        return False
    attrs = event.get("attrs") or {}
    for key, want in (trigger_event.filter or {}).items():
        if key.endswith("_glob"):
            field_name = key[: -len("_glob")]
            value = event.get(field_name, attrs.get(field_name))
            if value is None:
                return False
            target = _uri_path(str(value)) if field_name == "uri" else str(value)
            if not glob_to_regex(want).match(target):
                return False
        else:
            value = event.get(key, attrs.get(key))
            if value is None or not fnmatch.fnmatchcase(str(value).lower(), str(want).lower()):
                return False
    return True


# --------------------------------------------------------------------------------------------
# durable state: the firing log, the claim store
# --------------------------------------------------------------------------------------------
def _append_line(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, default=str).encode("utf-8") + b"\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0),
                 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


class FiringLog:
    """Append-only `firings.jsonl`: one line per firing event, one `write()` each.

    Indexed on load and on append: `keys` holds keys that reached a terminal outcome (plus legacy
    lines written before retries existed); `latest` holds each key's most recent line, from which
    the engine finds firings still owed an outcome (`open_firings`)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.keys: set[str] = set()
        self.latest: dict[str, dict] = {}
        self._load()

    def _index(self, rec: dict) -> None:
        key = rec.get("idempotency_key")
        status = rec.get("status")
        if not key or not isinstance(key, str):
            return
        if status in TERMINAL or (status in _LEGACY_SEEN and "attempt" not in rec):
            self.keys.add(key)
        if status in TERMINAL:
            self.latest.pop(key, None)                  # settled: nothing left to recover
        elif status in _OPEN and "attempt" in rec:
            prev = self.latest.get(key) or {}
            if "trigger_info" not in rec and "trigger_info" in prev:
                rec = {**rec, "trigger_info": prev["trigger_info"]}   # what a retry re-sends
            self.latest[key] = rec

    def _load(self) -> None:
        if not self.path.is_file():
            return
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    self._index(rec)

    def seen(self, key: str) -> bool:
        return key in self.keys

    def append(self, record: dict) -> None:
        _append_line(self.path, record)
        self._index(record)

    def open_firings(self) -> list[dict]:
        return [r for k, r in self.latest.items() if k not in self.keys and r.get("status") in _OPEN]

    def records(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out


class ClaimStore:
    """Exclusive, durable, cross-process claims: one ``O_CREAT|O_EXCL`` marker file per
    (key, attempt), and one ``.done`` marker per terminal key. Creating a file exclusively is
    atomic on every filesystem this runs on, so of any number of racing engines exactly one wins."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _stem(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]

    def _create(self, path: Path, body: dict) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_BINARY", 0), 0o644)
        except FileExistsError:
            return False
        try:
            os.write(fd, json.dumps(body, sort_keys=True, default=str).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def claim(self, key: str, attempt: int, **meta: Any) -> bool:
        return self._create(self.root / f"{self._stem(key)}.{attempt}.claim",
                            {"idempotency_key": key, "attempt": attempt, **meta})

    def held(self, key: str) -> bool:
        return (self.root / f"{self._stem(key)}.1.claim").exists()

    def finish(self, key: str, **meta: Any) -> bool:
        return self._create(self.root / f"{self._stem(key)}.done", {"idempotency_key": key, **meta})

    def is_done(self, key: str) -> bool:
        return (self.root / f"{self._stem(key)}.done").exists()

    def claims(self) -> list[dict]:
        """Every attempt claim on disk with its body (the durable intent `_begin` wrote into it),
        highest attempt per key only. Unreadable bodies are skipped."""
        if not self.root.is_dir():
            return []
        best: dict[str, dict] = {}
        for path in self.root.glob("*.claim"):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = body.get("idempotency_key") if isinstance(body, dict) else None
            if not isinstance(key, str):
                continue
            attempt = int(body.get("attempt") or 1)
            if key not in best or attempt > int(best[key].get("attempt") or 1):
                best[key] = body
        return list(best.values())


# --------------------------------------------------------------------------------------------
# spec loading
# --------------------------------------------------------------------------------------------
def load_specs_from_dirs(agents_dir: Optional[Path] = None,
                         adapter_agents_dir: Optional[Path] = None
                         ) -> tuple[list[Spec], list[str]]:
    """Every spec on disk (§5.6 files), plus a problem line per file that failed to load."""
    specs: list[Spec] = []
    problems: list[str] = []
    a = agents_dir or paths.agents_dir()
    if a.is_dir():
        for p in sorted(a.glob("*.workflow.yaml")):
            try:
                specs.append(load_spec(p))
            except (SpecError, OSError) as exc:
                problems.append(f"{p.name}: {exc}")
    b = adapter_agents_dir or paths.adapter_agents_dir()
    if b.is_dir():
        for p in sorted(b.glob("*.yaml")):
            try:
                specs.append(load_spec(p))
            except (SpecError, OSError) as exc:
                problems.append(f"{p.name}: {exc}")
    return specs, problems


# --------------------------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------------------------
@dataclass
class _Slot:
    spec_id: str
    digest: str
    cron: CronSchedule
    last_checked: datetime


def _file_id(st: os.stat_result) -> Optional[str]:
    return f"{st.st_dev}:{st.st_ino}" if getattr(st, "st_ino", 0) else None


class TriggerEngine:
    def __init__(self, runner: Optional[Runner] = None, *,
                 specs: Iterable[Spec] = (),
                 spec_loader: Optional[Callable[[], Iterable[Spec]]] = None,
                 events_path: Optional[Path] = None,
                 offsets_dir: Optional[Path] = None,
                 firings_path: Optional[Path] = None,
                 state_path: Optional[Path] = None,
                 claims_dir: Optional[Path] = None,
                 consumer: str = "trigger_engine",
                 clock: Callable[[], datetime] = now_utc,
                 poll_interval: float = 5.0,
                 grace: timedelta = timedelta(minutes=2),
                 missed_record_limit: int = 50,
                 max_attempts: int = 3,
                 retry_backoff: timedelta = timedelta(minutes=1),
                 claim_lease: timedelta = timedelta(hours=1)) -> None:
        if not re.match(r"^[a-z0-9][a-z0-9_.-]{0,63}$", consumer):
            raise ValueError(f"consumer {consumer!r} must be a short lowercase name (it names a file)")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.runner = runner
        self.spec_loader = spec_loader
        self.events_path = events_path or paths.events_path()
        self.offsets_dir = offsets_dir or paths.offsets_dir()
        self.firings = FiringLog(firings_path or paths.firings_path())
        self.state_path = state_path or (self.firings.path.parent / "schedule_state.json")
        self.claims = ClaimStore(claims_dir or (self.firings.path.parent / "claims"))
        self.dead_letter_path = self.firings.path.parent / "dead_letter.jsonl"
        self.consumer = consumer
        self.clock = clock
        self.poll_interval = poll_interval
        self.grace = grace
        self.missed_record_limit = missed_record_limit
        self.max_attempts = max_attempts
        self.retry_backoff = retry_backoff
        self.claim_lease = claim_lease
        self._specs: dict[str, Spec] = {}
        self._slots: dict[str, _Slot] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._inflight: set[str] = set()
        self._queued: dict[str, str] = {}                  # spec id -> the slot key waiting
        self._lost_race: set[tuple[str, int]] = set()
        self._loop_task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None          # created in start()
        self._state = self._load_state()
        self.problems: list[str] = []
        for s in specs:
            self.register(s)

    # -- specs --------------------------------------------------------------------------------
    def register(self, spec: Spec) -> None:
        sid = spec_id(spec)
        self._specs[sid] = spec
        trig = spec_trigger(spec)
        self._slots.pop(sid, None)
        if trig.type == "schedule" and trig.schedule is not None:
            try:
                cron = CronSchedule.parse(trig.schedule.cron, trig.schedule.timezone)
            except CronError as exc:
                self.problems.append(f"{sid}: {exc}")
                return
            digest = spec_digest(spec)
            saved = self._state.get(sid) or {}
            last = None
            if saved.get("digest") == digest and saved.get("last_checked"):
                last = _parse(saved["last_checked"])
            self._slots[sid] = _Slot(sid, digest, cron, last or self.clock())

    def unregister(self, sid: str) -> None:
        self._specs.pop(sid, None)
        self._slots.pop(sid, None)

    def reload(self) -> None:
        """Re-read specs from `spec_loader` (the gateway calls this after a save)."""
        if self.spec_loader is None:
            return
        loaded = list(self.spec_loader())
        counts: dict[str, int] = {}
        for s_ in loaded:
            counts[spec_id(s_)] = counts.get(spec_id(s_), 0) + 1
        dupes = sorted(k for k, n in counts.items() if n > 1)
        # two specs with one id: neither is registered — running either could be the wrong one
        # (Codex batch 2 #10). The problem is reported until the ids are made unique.
        self.problems = [p for p in self.problems if not p.endswith("(duplicate id)")]
        for d in dupes:
            self.problems.append(f"{d}: {counts[d]} specs share this id; none is registered "
                                 f"(duplicate id)")
        fresh = {spec_id(s_): s_ for s_ in loaded if spec_id(s_) not in dupes}
        for sid in list(self._specs):
            if sid not in fresh:
                self.unregister(sid)
        for sid, s in fresh.items():
            old = self._specs.get(sid)
            if old is None or spec_digest(old) != spec_digest(s) or old.status != s.status:
                self.register(s)

    @property
    def specs(self) -> dict[str, Spec]:
        return dict(self._specs)

    def next_fire(self, sid: str, after: Optional[datetime] = None) -> Optional[datetime]:
        slot = self._slots.get(sid)
        if slot is None:
            return None
        return slot.cron.next_fire(after or self.clock())

    # -- state --------------------------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        data = {sid: {"digest": s.digest, "last_checked": _iso_us(s.last_checked)}
                for sid, s in self._slots.items()}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _offset_path(self) -> Path:
        return self.offsets_dir / f"{self.consumer}.json"

    def _load_offset_state(self) -> dict:
        try:
            data = json.loads(self._offset_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _load_offset(self) -> int:
        try:
            return int(self._load_offset_state().get("offset", 0))
        except (TypeError, ValueError):
            return 0

    def _save_offset(self, offset: int, *, file_id: Optional[str] = None,
                     prefix: bytes = b"") -> None:
        p = self._offset_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        head = prefix[:min(offset, _HEAD_BYTES)]
        tmp = p.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps({
            "offset": offset, "path": os.path.abspath(str(self.events_path)),
            "file_id": file_id, "head_len": len(head),
            "head": "sha256:" + hashlib.sha256(head).hexdigest(),
            "updated": _iso(self.clock())}), encoding="utf-8")
        os.replace(tmp, p)

    # -- records ------------------------------------------------------------------------------
    def _record(self, spec: Spec, info: dict, status: str, **extra: Any) -> dict:
        rec = {"firing_id": info["firing_id"], "idempotency_key": info["idempotency_key"],
               "spec_id": spec_id(spec), "spec_kind": spec_kind(spec),
               "spec_digest": info["spec_digest"], "trigger": info["type"],
               "scheduled_for": info.get("scheduled_for"),
               "event_id": (info.get("event") or {}).get("event_id"),
               "fired_at": info["fired_at"], "recorded_at": _iso_us(self.clock()),
               "status": status}
        if "attempt" in info:
            rec["attempt"] = info["attempt"]
        rec.update(extra)
        self.firings.append(rec)
        return rec

    def _info(self, spec: Spec, kind: str, key: str, **extra: Any) -> dict:
        info = {"firing_id": str(uuid.uuid4()), "idempotency_key": key, "type": kind,
                "spec_id": spec_id(spec), "spec_kind": spec_kind(spec),
                "spec_digest": spec_digest(spec), "fired_at": _iso(self.clock())}
        info.update(extra)
        return info

    def _dead_letter(self, rec: dict) -> None:
        self.firings.append(rec)
        _append_line(self.dead_letter_path, rec)

    # -- claiming and dispatch ----------------------------------------------------------------
    def _begin(self, spec: Spec, info: dict, attempt: int = 1) -> bool:
        """Claim (key, attempt) exclusively and write the durable ``pending`` intent. False when
        another coroutine, engine or process already owns it — the caller then does nothing."""
        key = info["idempotency_key"]
        if attempt == 1 and self.firings.seen(key):
            return False
        portable = {k: v for k, v in info.items() if k != "attempt"}
        # the claim IS the durable intent (Codex batch 2 #6): it carries everything a retry needs,
        # so a process that dies between claiming and journalling leaves a recoverable claim
        if not self.claims.claim(key, attempt, firing_id=info["firing_id"],
                                 claimed_at=_iso_us(self.clock()), consumer=self.consumer,
                                 spec_id=info.get("spec_id"), spec_digest=info.get("spec_digest"),
                                 trigger_info=portable):
            return False
        info["attempt"] = attempt
        self._record(spec, info, "pending", trigger_info=portable)
        return True

    async def _run(self, spec: Spec, info: dict) -> dict:
        key = info["idempotency_key"]
        self._inflight.add(key)
        try:
            return await self._dispatch(spec, info)
        finally:
            self._inflight.discard(key)

    async def _dispatch(self, spec: Spec, info: dict) -> dict:
        key = info["idempotency_key"]
        if self.runner is None:
            rec = self._record(spec, info, "no_runner",
                               reason="no runner is attached; the firing is recorded only")
            self.claims.finish(key, status="no_runner")
            return rec
        try:
            out = self.runner(spec, info)
            if inspect.isawaitable(out):
                out = await out
        except Exception as exc:                                 # noqa: BLE001 - recorded
            return self._failed(spec, info, f"{type(exc).__name__}: {exc}")
        summary = out if isinstance(out, (str, int, float, bool)) or out is None else \
            (out.get("run_id") if isinstance(out, dict) else getattr(out, "run_id", None))
        rec = self._record(spec, info, "succeeded", result=summary)
        self.claims.finish(key, status="succeeded")
        return rec

    def _failed(self, spec: Spec, info: dict, error: str) -> dict:
        attempt = int(info.get("attempt") or 1)
        if attempt >= self.max_attempts:
            rec = self._record(spec, info, "failed", error=error, final=True)
            self._dead_letter({**rec, "status": "dead_lettered", "attempts": attempt,
                               "recorded_at": _iso_us(self.clock()),
                               "reason": f"failed {attempt} time(s); no retries left"})
            self.claims.finish(info["idempotency_key"], status="dead_lettered")
            return rec
        nxt = self.clock() + self.retry_backoff * (2 ** (attempt - 1))
        return self._record(spec, info, "failed", error=error, next_attempt_at=_iso_us(nxt))

    def _spawn_after(self, sid: str, spec: Spec, info: dict, prev: asyncio.Task) -> asyncio.Task:
        async def after() -> dict:
            try:
                await asyncio.shield(prev)
            except BaseException:                            # noqa: BLE001 - its own record
                pass
            self._queued.pop(sid, None)
            return await self._run(spec, info)

        task = asyncio.ensure_future(after())
        self._running[sid] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _spawn(self, sid: str, spec: Spec, info: dict) -> asyncio.Task:
        task = asyncio.ensure_future(self._run(spec, info))
        self._running[sid] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- recovery: retries, and intents a dead process left behind ---------------------------
    def recover(self, now: Optional[datetime] = None) -> list[dict]:
        """Re-dispatch every firing still owed an outcome: failed attempts whose backoff has
        elapsed, and ``pending`` intents older than `claim_lease` (their process stopped before
        recording an outcome). Same key, next attempt; a spec that changed or vanished in the
        meantime is dead-lettered, a paused one waits."""
        now = now or self.clock()
        out: list[dict] = []
        for rec in self.firings.open_firings() + self._orphan_claims():
            key = rec["idempotency_key"]
            attempt = int(rec.get("attempt") or 1)
            if key in self._inflight or (key, attempt + 1) in self._lost_race:
                continue
            if rec["status"] == "failed":
                due = _parse(rec.get("next_attempt_at"))
                if due is None or due > now:
                    continue
            else:                                                  # pending
                since = _parse(rec.get("recorded_at"))
                if since is not None and now - since < self.claim_lease:
                    continue
            spec = self._specs.get(str(rec.get("spec_id")))
            info = dict(rec.get("trigger_info") or {})
            if spec is None or not info or spec_digest(spec) != rec.get("spec_digest"):
                if self.claims.finish(key, status="dead_lettered"):
                    dl = {**rec, "status": "dead_lettered", "attempts": attempt,
                          "recorded_at": _iso_us(now),
                          "reason": "the spec was removed or edited before this firing could "
                                    "be retried"}
                    dl.pop("trigger_info", None)
                    self._dead_letter(dl)
                    out.append(dl)
                continue
            if admission_refusal(spec):
                continue                                     # paused / plan revoked: it waits
            if rec["status"] == "pending" and attempt >= self.max_attempts:
                if self.claims.finish(key, status="dead_lettered"):
                    dl = {**rec, "status": "dead_lettered", "attempts": attempt,
                          "recorded_at": _iso_us(now),
                          "reason": f"attempt {attempt} was abandoned (the process stopped) and "
                                    f"no retries are left"}
                    dl.pop("trigger_info", None)
                    self._dead_letter(dl)
                    out.append(dl)
                continue
            if not self._begin(spec, info, attempt + 1):
                self._lost_race.add((key, attempt + 1))
                continue
            self._spawn(str(rec["spec_id"]), spec, info)
            out.append({"idempotency_key": key, "status": "retrying", "attempt": attempt + 1,
                        "spec_id": rec["spec_id"]})
        return out

    def _orphan_claims(self) -> list[dict]:
        """Claims whose key has NO journal line at all: the claimer died after the exclusive
        create and before `pending` was written. Rebuilt as a pending intent from the claim's own
        body (`recorded_at` = its claim time), so `recover` treats it like any abandoned intent —
        after `claim_lease`, same key, next attempt. An alias claim (no `trigger_info`) is not an
        intent and is skipped."""
        out = []
        for body in self.claims.claims():
            key = body["idempotency_key"]
            if self.firings.seen(key) or key in self.firings.latest or self.claims.is_done(key):
                continue
            info = body.get("trigger_info")
            if not isinstance(info, dict) or not info:
                continue
            out.append({"idempotency_key": key, "attempt": int(body.get("attempt") or 1),
                        "status": "pending", "spec_id": body.get("spec_id") or info.get("spec_id"),
                        "spec_digest": body.get("spec_digest") or info.get("spec_digest"),
                        "recorded_at": body.get("claimed_at"), "trigger_info": info,
                        "firing_id": body.get("firing_id"), "recovered_from": "orphan_claim"})
        return out

    # -- manual -------------------------------------------------------------------------------
    async def fire(self, sid: str, *, idempotency_key: Optional[str] = None,
                   payload: Optional[dict] = None, by: Optional[str] = None) -> dict:
        """Manual trigger ("run now"). Refused for an unknown spec; recorded as skipped for any
        spec that is not active (a draft never runs), for an adapter agent without approved
        plans, and for a key that already fired."""
        spec = self._specs.get(sid)
        if spec is None:
            raise KeyError(f"no agent or adapter agent {sid!r} is registered")
        key = f"{sid}:manual:{idempotency_key or uuid.uuid4()}"
        info = self._info(spec, "manual", key, payload=dict(payload or {}), by=by)
        refusal = admission_refusal(spec)
        if refusal:
            return self._record(spec, info, "skipped", reason=refusal)
        if not self._begin(spec, info):
            return self._record(spec, info, "skipped", reason="duplicate idempotency key")
        return await self._run(spec, info)

    # -- schedules ----------------------------------------------------------------------------
    def _skip_record(self, spec: Spec, sid: str, digest: str, when: datetime, reason: str,
                     **extra: Any) -> dict:
        return self._record(spec, self._info(spec, "schedule",
                                             f"{sid}:{digest}:schedule:{_iso(when)}",
                                             scheduled_for=_iso(when)), "skipped",
                            reason=reason, **extra)

    async def tick(self, now: Optional[datetime] = None) -> list[dict]:
        """Evaluate every schedule once. Returns the records written by this tick (the firings
        themselves run as tasks; `drain()` waits for them)."""
        now = now or self.clock()
        out: list[dict] = []
        for sid, slot in list(self._slots.items()):
            spec = self._specs.get(sid)
            if spec is None:
                continue
            start = slot.last_checked
            if spec.status == "draft" or now <= start:
                slot.last_checked = max(start, now)
                continue                                  # a draft is not scheduled at all
            # a slot fires only while now - slot <= grace; older ones are missed
            edge = max(start, now - self.grace - timedelta(microseconds=1))
            recent = list(slot.cron.fires_between(edge, now, limit=100_000))
            latest = recent[-1] if recent else None
            recent_missed = recent[:-1]
            listed = list(slot.cron.fires_between(start, edge, limit=self.missed_record_limit)) \
                if edge > start else []
            # fewer than the limit means fires_between returned them ALL; only a truncated
            # listing needs the exact (day-wise) count
            old_total = slot.cron.count_between(start, edge) \
                if len(listed) >= self.missed_record_limit else len(listed)
            for m in recent_missed:
                if len(listed) >= self.missed_record_limit:
                    break
                listed.append(m)
            total = old_total + len(recent_missed)
            for m in listed:
                out.append(self._skip_record(spec, sid, slot.digest, m,
                                             "missed (the engine was not running at this time)"))
            rest = total - len(listed)
            if rest > 0:
                w_from = listed[-1] if listed else start
                w_to = recent_missed[-1] if recent_missed else edge
                info = self._info(spec, "schedule",
                                  f"{sid}:{slot.digest}:missed:{_iso_us(w_from)}:{_iso_us(w_to)}")
                out.append(self._record(
                    spec, info, "skipped", missed_count=rest, missed_after=_iso_us(w_from),
                    missed_through=_iso_us(w_to),
                    reason=f"missed; {rest} further slots in ({_iso(w_from)}, {_iso(w_to)}] "
                           f"not listed one by one (the engine was not running)"))
            slot.last_checked = now
            if latest is None:
                continue
            key = f"{sid}:{slot.digest}:schedule:{_iso(latest)}"
            info = self._info(spec, "schedule", key, scheduled_for=_iso(latest))
            refusal = admission_refusal(spec)
            if refusal:
                out.append(self._record(spec, info, "skipped", reason=refusal))
                continue
            if self.firings.seen(key) or self.claims.held(key):
                continue
            running = self._running.get(sid)
            if running is not None and not running.done():
                # never overlap two runs of one spec, and never drop a slot silently (Codex batch
                # 2 #9): the slot is claimed and QUEUED behind the running one. At most one slot
                # waits per spec; a further slot while one waits is COALESCED into it — a
                # terminal, audited outcome of its own, not "missed".
                if sid in self._queued:
                    out.append(self._record(spec, info, "coalesced",
                                            reason=f"a queued run ({self._queued[sid]}) already "
                                                   f"covers this slot; runs never overlap"))
                    self.claims.finish(key, status="coalesced")
                    continue
                if not self._begin(spec, info):
                    continue
                self._queued[sid] = key
                self._spawn_after(sid, spec, info, running)
                out.append({"idempotency_key": key, "status": "queued", "spec_id": sid,
                            "behind": "the previous run, still running"})
                continue
            if not self._begin(spec, info):               # another engine owns this slot
                continue
            self._spawn(sid, spec, info)                  # intent is on disk before state moves
            out.append({"idempotency_key": key, "status": "dispatched", "spec_id": sid})
        self._save_state()
        return out

    # -- events -------------------------------------------------------------------------------
    def _event_keys(self, sid: str, event: dict) -> list[str]:
        keys = [f"{sid}:event:{event.get('event_id')}"]
        if str(event.get("type", "")).startswith("run_") and event.get("run_id"):
            keys.append(f"{sid}:run:{event['run_id']}:{event['type']}")
        return keys

    def _log_note(self, status: str, **extra: Any) -> dict:
        rec = {"status": status, "consumer": self.consumer,
               "events_path": os.path.abspath(str(self.events_path)),
               "recorded_at": _iso_us(self.clock()), **extra}
        self.firings.append(rec)
        return rec

    async def poll_events(self) -> list[dict]:
        """Read every complete new line of the event log once and fire matching specs."""
        out: list[dict] = []
        path = self.events_path
        if not path.is_file():
            return out
        state = self._load_offset_state()
        try:
            offset = int(state.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        st = path.stat()
        fid = _file_id(st)
        with open(path, "rb") as fh:
            prefix = fh.read(_HEAD_BYTES)
        reason = None
        saved_path = state.get("path")
        if offset and saved_path and os.path.normcase(os.path.abspath(str(saved_path))) != \
                os.path.normcase(os.path.abspath(str(path))):
            reason = f"the event log moved from {saved_path} to {path}"
        elif offset and state.get("file_id") and fid and state["file_id"] != fid:
            reason = "the event log was replaced by a different file (rotation)"
        elif st.st_size < offset:
            reason = f"the event log shrank from {offset} to {st.st_size} bytes"
        elif offset and state.get("head") and state.get("head_len") is not None:
            hl = int(state["head_len"])
            if "sha256:" + hashlib.sha256(prefix[:hl]).hexdigest() != state["head"]:
                reason = "the start of the event log changed (rewritten in place)"
        if reason:
            out.append(self._log_note("offset_reset", reason=reason + "; re-reading from 0 "
                                      "(idempotency keys stop re-fires)", previous_offset=offset))
            offset = 0
        size = st.st_size
        if size == offset:
            if reason:
                self._save_offset(offset, file_id=fid, prefix=prefix)
            return out
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read(size - offset)
        pos = 0
        while True:
            nl = chunk.find(b"\n", pos)
            if nl < 0:
                break                                   # a partial trailing line waits
            line = chunk[pos:nl]
            start = offset + pos
            pos = nl + 1
            if line.strip():
                event, why = None, None
                try:
                    event = json.loads(line.decode("utf-8"))
                except UnicodeDecodeError:
                    why = "not UTF-8"
                except json.JSONDecodeError as exc:
                    why = f"not JSON ({exc.msg})"
                if why is None and not (isinstance(event, dict) and event.get("event_id")):
                    why = "not an event object with an event_id"
                if why is None:
                    out.extend(await self._handle_event(event))
                else:
                    out.append(self._log_note(
                        "rejected_event", reason=why, byte_range=[start, offset + nl],
                        file_id=fid, line_sha256=hashlib.sha256(line).hexdigest()))
            self._save_offset(offset + pos, file_id=fid, prefix=prefix)
        return out

    async def _handle_event(self, event: dict) -> list[dict]:
        out: list[dict] = []
        jobs = []
        for sid, spec in list(self._specs.items()):
            trig = spec_trigger(spec)
            if trig.type != "event" or trig.event is None:
                continue
            if not event_matches(trig.event, event):
                continue
            keys = self._event_keys(sid, event)
            info = self._info(spec, "event", keys[0], event=event, dedupe_keys=keys)
            if spec.status == "draft":
                continue
            refusal = admission_refusal(spec)
            if refusal:
                out.append(self._record(spec, info, "skipped", reason=refusal))
                continue
            if any(self.firings.seen(k) for k in keys):
                continue
            # claim the run alias first: a sibling event for the same run is then a no-op
            if not all(self.claims.claim(k, 1, alias_of=keys[0], firing_id=info["firing_id"])
                       for k in keys[1:]):
                continue
            if not self._begin(spec, info):
                continue
            jobs.append((spec, info, keys))
        results = await asyncio.gather(*(self._run(s, i) for s, i, _ in jobs))
        for (spec, info, keys), rec in zip(jobs, results):
            for k in keys[1:]:
                self.firings.append({"idempotency_key": k, "alias_of": keys[0],
                                     "spec_id": spec_id(spec), "status": "dispatched",
                                     "firing_id": info["firing_id"],
                                     "recorded_at": _iso_us(self.clock())})
            out.append(rec)
        return out

    # -- lifecycle ----------------------------------------------------------------------------
    async def run_once(self, now: Optional[datetime] = None) -> list[dict]:
        if self.spec_loader is not None:
            self.reload()
        out = self.recover(now)
        out += await self.poll_events()
        out += await self.tick(now)
        return out

    async def _loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:                           # noqa: BLE001 - keep serving
                self.problems.append(f"{_iso(self.clock())}: {type(exc).__name__}: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._stop = asyncio.Event()
        self._loop_task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._loop_task is not None:
            await self._loop_task
            self._loop_task = None
        await self.drain()

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def status(self) -> dict:
        now = self.clock()
        return {"consumer": self.consumer, "events_path": str(self.events_path),
                "offset": self._load_offset(), "problems": list(self.problems[-20:]),
                "open_firings": len(self.firings.open_firings()),
                "specs": [{"id": sid, "kind": spec_kind(s), "status": s.status,
                           "trigger": spec_trigger(s).type,
                           "next_fire": (_iso(n) if (n := self.next_fire(sid, now)) else None)}
                          for sid, s in sorted(self._specs.items())]}


__all__ = ["TriggerEngine", "FiringLog", "ClaimStore", "event_matches", "glob_to_regex",
           "spec_digest", "spec_id", "load_specs_from_dirs", "admission_refusal", "TERMINAL"]
