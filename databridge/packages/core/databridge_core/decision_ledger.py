"""The ONE decision ledger, the shared actor object and the standard refusal codes (R-028).

Every human decision log in the platform — crawl-plan approvals, mapping and link approvals, the
setup documents decisions and hand-offs, and dataset-design events — is written and read through
`DecisionLedger`. It is the standard of R-028 (Codex consistency review, findings #2, #4):

* **the deployment HMAC approval key** (`approval_key`): ``WALNUT_APPROVAL_KEY`` (>= 32 chars), else
  the key file ``WALNUT_APPROVAL_KEY_FILE`` (default ``~/.walnut/keys/approval.key``), provisioned
  once by an explicit step (`provision_approval_key`) and NEVER created implicitly: a read never
  writes, and a write with no key is refused (``APPROVAL_KEY_UNAVAILABLE``).
  A key that lies inside the directory of the log it seals is refused: whoever could write a
  record could then also seal it;
* **canonical JSON** (`canonical`): sorted keys, no whitespace, ``default=str``;
* **a hash chain**: every record carries ``seq`` (1, 2, ...), ``prev`` (the previous record's
  digest, or the genesis marker) and its own digest, and a ``seal`` (HMAC of the digest);
* **an external anchor** (`AnchorLedger`): after every append the log's ``(seq, head)`` is written
  to a sealed, chained anchor ledger. A log must EQUAL its latest anchor — same length, same head.
  A log shorter than its anchor was rolled back; a log longer than its anchor has an unanchored
  tail, which is never trusted;
* **a cross-process append lock** (`file_lock`, re-entrant within a thread);
* **full verification before every append and every read**.

Recovery of an interrupted append (#4). An append writes the record, then the anchor. A crash in
between leaves exactly ONE validly sealed record past the anchor; a crash mid-write leaves a torn,
unterminated last line. A READ never repairs anything: it waits for the append lock (a writer may be
between its two writes), re-checks, and refuses (`LEDGER_INTEGRITY_FAILED`) if the tail is still
pending. The next APPEND (or an operator's `recover()`) completes it explicitly, under the lock:
the sealed pending record is anchored, a torn fragment is moved aside to ``<log>.torn-<sha>``, and
a ``ledger_event: recovered`` record says what was done. Nothing else is ever repaired.

Logs written before this ledger existed (R-028 decision: REFUSE, then supersede). A log with no
sealed record and no anchor is a LEGACY log. It is never evidence: every read refuses with
`LEDGER_INTEGRITY_FAILED` and a message telling the person to decide again. The next decision
APPENDED to it supersedes it: the legacy file is kept, renamed ``<log>.legacy-<sha12>``, and the new
sealed log opens with a ``ledger_event: superseded_legacy`` record naming it and its sha256.
Migrating legacy records under the key was rejected: an unkeyed log can be rewritten by anyone who
can write the directory, so sealing it would launder a forged approval into a sealed one.

The ledger's own events (``ledger_event``) are returned by `read(raw=True)` only; `read()` returns
the decision records.

The actor (#7). Every decision record carries the actor fields — ``principal_id``,
``authenticated_by``, ``principal_type``, ``dev_only`` and, for a delegated decision,
``delegation`` — built by `actor_of`. ``principal_id`` is never decorated (no "(dev token)"
suffix: ``dev_only`` says that). A machine principal is refused wherever a person is required
(`require_person`).

Refusal codes (#11): `APPROVER_NOT_AUTHENTICATED`, `MACHINE_ACTOR`, `DECISION_STALE`,
`DECISION_ALREADY_FINAL`, `LEDGER_INTEGRITY_FAILED`, in the envelope ``{"refusal": {"code",
"message"}}`` (`refusal`). Older codes that tests or the UI still read map onto them
(`standard_code`).

Lock order (global, R-028 standard 8): source lock -> ingest-run lock -> plan lock -> mapping lock
-> design lock -> ledger append lock (the log's lock, then its anchor ledger's lock).

Stdlib + pydantic only: this module is importable on its own (core is the dependency root).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, field_validator

# =================================================================================================
# refusal codes
# =================================================================================================
APPROVER_NOT_AUTHENTICATED = "APPROVER_NOT_AUTHENTICATED"
MACHINE_ACTOR = "MACHINE_ACTOR"
DECISION_STALE = "DECISION_STALE"
DECISION_ALREADY_FINAL = "DECISION_ALREADY_FINAL"
LEDGER_INTEGRITY_FAILED = "LEDGER_INTEGRITY_FAILED"
REFUSAL_CODES = frozenset({APPROVER_NOT_AUTHENTICATED, MACHINE_ACTOR, DECISION_STALE,
                           DECISION_ALREADY_FINAL, LEDGER_INTEGRITY_FAILED})

#: Older codes still read by tests or the UI -> the standard code they mean.
LEGACY_CODES: dict[str, str] = {
    "NOT_A_PERSON": MACHINE_ACTOR,
    "APPROVER_REQUIRED": APPROVER_NOT_AUTHENTICATED,
    "PLAN_CHANGED": DECISION_STALE,
    "PLAN_STALE": DECISION_STALE,
    "STALE_REVISION": DECISION_STALE,
    "DIGEST_MISMATCH": DECISION_STALE,
    "DESIGN_INTEGRITY_FAILED": LEDGER_INTEGRITY_FAILED,
}

#: Not an approval refusal: the deployment cannot seal or verify anything (a server condition).
APPROVAL_KEY_UNAVAILABLE = "APPROVAL_KEY_UNAVAILABLE"


def standard_code(code: str) -> str:
    """The standard code ``code`` means (itself when it is one, or has no standard meaning)."""
    return LEGACY_CODES.get(str(code), str(code))


def refusal(code: str, message: str) -> dict:
    """The common refusal envelope: ``{"refusal": {"code", "message"}}``. ``message`` is fixed,
    public text — never an exception's string."""
    return {"refusal": {"code": str(code), "message": str(message)}}


class DecisionRefused(Exception):
    """A decision, or a read of a decision log, refused with a standard ``code``. ``message`` is
    public text (it never carries a key, a path outside the deployment or an exception string)."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)

    def envelope(self) -> dict:
        return refusal(self.code, self.message)


# =================================================================================================
# the actor object
# =================================================================================================
#: Principal-id prefixes of machine actors (the agent runtime, adapter agents, workflows, the
#: system, the rules). Kept identical to adapter_agents `adapt._MACHINE` (asserted by the tests).
MACHINE_PREFIXES: tuple[str, ...] = ("agent:", "adapter_agent:", "workflow:", "system", "rules")
#: A principal id decorated with how it signed in ("alice (dev token)"): ``dev_only`` and
#: ``authenticated_by`` say that; the id stays the canonical one.
_DECORATED = re.compile(r"\(\s*dev[\s_-]*(token|only|mode|login)\s*\)\s*$", re.IGNORECASE)
ACTOR_FIELDS = ("principal_id", "authenticated_by", "principal_type", "dev_only")


def is_machine_principal(principal_id: Any) -> bool:
    return str(principal_id or "").strip().lower().startswith(MACHINE_PREFIXES)


class Actor(BaseModel):
    """Who made a decision: ``{principal_id, authenticated_by, principal_type, dev_only,
    delegation?}``. Built only from what an authentication gate returned (`actor_of`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str
    authenticated_by: str
    principal_type: str
    dev_only: bool = False
    delegation: Optional[dict] = None

    @field_validator("principal_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        v = str(v or "").strip()
        if not v:
            raise ValueError("an actor names its principal_id")
        if _DECORATED.search(v):
            raise ValueError("a principal_id is never decorated (dev_only says how it signed in)")
        return v

    @field_validator("authenticated_by", "principal_type")
    @classmethod
    def _named(cls, v: str) -> str:
        v = str(v or "").strip()
        if not v:
            raise ValueError("an actor names how it was authenticated and its principal type")
        return v

    @property
    def is_machine(self) -> bool:
        return is_machine_principal(self.principal_id)

    @property
    def is_delegated(self) -> bool:
        return self.delegation is not None or self.authenticated_by.startswith("delegated:")

    def record(self) -> dict:
        """The actor as stored on a decision or Activity record (``delegation`` only if any)."""
        out = {"principal_id": self.principal_id, "authenticated_by": self.authenticated_by,
               "principal_type": self.principal_type, "dev_only": bool(self.dev_only)}
        if self.delegation is not None:
            out["delegation"] = dict(self.delegation)
        return out


def actor_of(who: Mapping[str, Any]) -> dict:
    """The actor object of what an authentication gate returned (mcp_ingest
    `require_authenticated_approver` / `require_delegated_approver`, or a gateway principal's
    fields). For a delegated decision (``authenticated_by: delegated:<policy digest>``) every
    other field the gate returned becomes ``delegation``. Raises `DecisionRefused`
    (`APPROVER_NOT_AUTHENTICATED`) when a field is missing or the id is decorated."""
    if isinstance(who, Actor):
        return who.record()
    w = dict(who or {})
    delegation = w.get("delegation")
    if delegation is None and str(w.get("authenticated_by") or "").startswith("delegated:"):
        delegation = {k: v for k, v in w.items() if k not in ACTOR_FIELDS and k != "delegation"}
    try:
        a = Actor(principal_id=w.get("principal_id") or "",
                  authenticated_by=w.get("authenticated_by") or "",
                  principal_type=w.get("principal_type") or "",
                  dev_only=bool(w.get("dev_only", False)), delegation=delegation)
    except Exception:                                                # noqa: BLE001 - fail closed
        raise DecisionRefused(APPROVER_NOT_AUTHENTICATED,
                              "the decision does not name an authenticated principal (its id, "
                              "authentication and principal type are all required, and the id "
                              "is never decorated)", 403) from None
    return a.record()


def require_person(who: Mapping[str, Any], *, what: str = "this decision") -> dict:
    """`actor_of`, refusing a machine principal and a delegation (`MACHINE_ACTOR`): a person
    decides ``what``."""
    a = actor_of(who)
    if is_machine_principal(a["principal_id"]) or a.get("delegation") is not None or \
            str(a["authenticated_by"]).startswith("delegated:"):
        raise DecisionRefused(MACHINE_ACTOR, f"{what} is made by a signed-in person, never by an "
                                             f"agent, a workflow or a delegation", 403)
    return a


# =================================================================================================
# canonical JSON, the approval key
# =================================================================================================
def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def digest_of(rec: Mapping[str, Any], *, exclude: Iterable[str]) -> str:
    skip = set(exclude)
    return hashlib.sha256(canonical({k: v for k, v in rec.items() if k not in skip})).hexdigest()


def seal_of(key: bytes, digest: str) -> str:
    return hmac.new(key, str(digest).encode("ascii"), hashlib.sha256).hexdigest()


APPROVAL_KEY_ENV = "WALNUT_APPROVAL_KEY"
APPROVAL_KEY_FILE_ENV = "WALNUT_APPROVAL_KEY_FILE"
_MIN_KEY_CHARS = 32


def approval_key_path() -> Path:
    raw = os.environ.get(APPROVAL_KEY_FILE_ENV, "").strip()
    return Path(raw) if raw else Path.home() / ".walnut" / "keys" / "approval.key"


def _inside(p: Path, d: Path) -> bool:
    return p == d or d in p.parents


#: The fixed, public reason a key is missing (no path: a refusal never names a server path).
_NO_KEY = ("no deployment approval key is provisioned, so nothing can be sealed or verified. An "
           "operator provisions it once (`provision_approval_key`, or WALNUT_APPROVAL_KEY / "
           "WALNUT_APPROVAL_KEY_FILE); it is never created implicitly")


def _key_file(protect: Iterable[Path]) -> Path:
    kp = approval_key_path().resolve()
    for d in protect:
        if _inside(kp, Path(d).resolve()):
            raise DecisionRefused(APPROVAL_KEY_UNAVAILABLE, "the approval key file lies inside a "
                                                            "directory whose decisions it seals; "
                                                            f"keep it elsewhere "
                                                            f"({APPROVAL_KEY_FILE_ENV})", 503)
    return kp


def approval_key(*, protect: Iterable[Path] = ()) -> bytes:
    """The deployment approval key (the same key `mcp_ingest.screen` seals overrides with).
    READ ONLY (R-029 TIDY2): it is never created here. A missing key refuses
    (`APPROVAL_KEY_UNAVAILABLE`): on a write path that is the explicit refusal, on a read path the
    caller reports the log as not readable. A key FILE inside any ``protect`` directory is refused.
    Provisioning is its own explicit step (`provision_approval_key`)."""
    raw = os.environ.get(APPROVAL_KEY_ENV, "")
    if raw:
        if len(raw.strip()) < _MIN_KEY_CHARS:
            raise DecisionRefused(APPROVAL_KEY_UNAVAILABLE, f"{APPROVAL_KEY_ENV} is shorter than "
                                                            f"{_MIN_KEY_CHARS} characters", 503)
        return raw.strip().encode("utf-8")
    kp = _key_file(protect)
    try:
        key = kp.read_text(encoding="utf-8").strip() if kp.is_file() else None
    except OSError:
        key = None
    if key is None:
        raise DecisionRefused(APPROVAL_KEY_UNAVAILABLE, _NO_KEY, 503)
    if len(key) < _MIN_KEY_CHARS:
        raise DecisionRefused(APPROVAL_KEY_UNAVAILABLE, f"the approval key file holds fewer than "
                                                        f"{_MIN_KEY_CHARS} characters", 503)
    return key.encode("utf-8")


def provision_approval_key(*, protect: Iterable[Path] = ()) -> Path:
    """The operator's EXPLICIT provisioning step: create the deployment key file with a random
    256-bit key if it does not exist (atomically; an existing key is never overwritten). Returns
    its path. Never called by a read, nor implicitly by a write."""
    kp = _key_file(protect)
    if not kp.is_file():
        kp.parent.mkdir(parents=True, exist_ok=True)
        tmp = kp.with_name(f".{kp.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        tmp.write_text(secrets.token_hex(32), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        try:
            os.link(tmp, kp)                 # atomic, never overwrites a key another writer made
        except FileExistsError:
            pass
        except OSError:
            with contextlib.suppress(OSError):
                os.rename(tmp, kp)
        tmp.unlink(missing_ok=True)
    return kp


# =================================================================================================
# the cross-process lock
# =================================================================================================
class LockTimeout(Exception):
    """The lock could not be taken in time. Nothing was written."""


_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_DEPTH = threading.local()


@contextlib.contextmanager
def file_lock(lock_path: Path, *, timeout: float = 30.0,
              on_timeout: Optional[Callable[[str], Exception]] = None):
    """An exclusive lock across PROCESSES (an OS lock on ``lock_path``) and threads, re-entrant
    within a thread. It times out CLOSED: ``on_timeout(message)`` (default `LockTimeout`) is
    raised and nothing is written. The OS releases it if the holder dies."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve())
    make = on_timeout or LockTimeout
    with _LOCKS_GUARD:
        tlock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    if not tlock.acquire(timeout=timeout):
        raise make(f"timed out waiting for the lock {lock_path.name}; another writer holds it. "
                   f"Nothing was written.")
    depths = getattr(_DEPTH, "d", None)
    if depths is None:
        depths = _DEPTH.d = {}
    try:
        depth = depths.get(key, 0)
        depths[key] = depth + 1
        if depth:
            yield
            return
        fh = open(lock_path, "a+b")
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt                                     # noqa: PLC0415
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:                                                 # pragma: no cover
                        import fcntl                                      # noqa: PLC0415
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise make(f"timed out waiting for the lock {lock_path.name}; another "
                                   f"process holds it. Nothing was written.") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    if os.name == "nt":
                        import msvcrt                                     # noqa: PLC0415
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:                                                 # pragma: no cover
                        import fcntl                                      # noqa: PLC0415
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
    finally:
        depths[key] -= 1
        if not depths[key]:
            depths.pop(key, None)
        tlock.release()


def lock_held(lock_path: Path) -> bool:
    """Does THIS thread hold ``lock_path`` (`file_lock`)?"""
    return bool((getattr(_DEPTH, "d", None) or {}).get(str(Path(lock_path).resolve())))


def atomic_write_text(path: Path, text: str) -> Path:
    """Write ``path`` whole or not at all: a same-directory temporary file, fsynced, then
    `os.replace`. A reader never sees half a file; a failed write leaves the old one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}."
                         f"{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()
    return path


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# =================================================================================================
# the anchor ledger
# =================================================================================================
ANCHORS_ENV = "WALNUT_DECISION_ANCHORS"
ANCHORS_NAME = "decision.anchors.jsonl"


class AnchorLedger:
    """One sealed, hash-chained line per log head: ``{log_id, seq, head, at, lseq, lprev,
    record_digest, seal}`` (a line written before R-028 names ``design_id`` instead of
    ``log_id``). Shared by every log anchored here; its own chain is ``lseq`` / ``lprev``."""

    def __init__(self, key: bytes, path: Path, *, lock_timeout: float = 120.0) -> None:
        self.key = key
        self.path = Path(path)
        self.lock_timeout = lock_timeout

    def _lines(self) -> tuple[list[dict], str]:
        if not self.path.is_file():
            return [], ""
        out: list[dict] = []
        head, seq = "", 0
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                return out, f"anchor line {n} is not a record"
            if not isinstance(rec, dict) or \
                    rec.get("record_digest") != digest_of(rec, exclude=("record_digest", "seal")) \
                    or not hmac.compare_digest(str(rec.get("seal") or ""),
                                               seal_of(self.key, str(rec.get("record_digest")))):
                return out, f"anchor line {n}: its seal or digest does not verify"
            if rec.get("lseq") != seq + 1 or rec.get("lprev") != head:
                return out, f"anchor line {n}: the anchor chain has a gap"
            seq, head = rec["lseq"], rec["record_digest"]
            out.append(rec)
        return out, ""

    @staticmethod
    def _id(rec: dict) -> Any:
        return rec.get("log_id", rec.get("design_id"))

    def latest(self, log_id: str) -> tuple[Optional[dict], str]:
        lines, why = self._lines()
        if why:
            return None, why
        mine = [r for r in lines if self._id(r) == log_id]
        return (mine[-1] if mine else None), ""

    def anchor(self, log_id: str, seq: int, head: str) -> dict:
        with file_lock(self.path.with_name(self.path.name + ".lock"), timeout=self.lock_timeout):
            lines, why = self._lines()
            if why:
                raise DecisionRefused(LEDGER_INTEGRITY_FAILED, f"the decision anchor ledger does "
                                                               f"not verify ({why}); it is not "
                                                               f"extended")
            last = lines[-1] if lines else None
            rec = {"log_id": log_id, "seq": int(seq), "head": head, "at": _now(),
                   "lseq": (last["lseq"] if last else 0) + 1,
                   "lprev": last["record_digest"] if last else ""}
            rec["record_digest"] = digest_of(rec, exclude=("record_digest", "seal"))
            rec["seal"] = seal_of(self.key, rec["record_digest"])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return rec


# =================================================================================================
# the decision ledger
# =================================================================================================
#: Envelope fields a record carries for the ledger (``digest_field`` is the third).
_ENVELOPE = ("seq", "prev", "seal")
LEDGER_EVENT = "ledger_event"


class _Scan:
    __slots__ = ("records", "state", "why", "valid_bytes", "torn", "anchor", "raw")

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.state = "ok"          # ok | empty | legacy | pending | torn | broken
        self.why = ""
        self.valid_bytes = 0
        self.torn = b""
        self.anchor: Optional[dict] = None
        self.raw = b""


class DecisionLedger:
    """An append-only, sealed, hash-chained, externally anchored decision log (see the module).

    ``path``         the JSONL log;
    ``key``          the approval key (default `approval_key`, refused inside the log's directory);
    ``anchors``      the anchor ledger (default ``WALNUT_DECISION_ANCHORS``, else
                     ``<log dir>/decision.anchors.jsonl``);
    ``log_id``       the log's id in the anchor ledger (default: the file name for a local anchor
                     ledger, the resolved path for a shared one);
    ``digest_field`` / ``genesis``  the chain's field names (``hash`` / ``"genesis"``; dataset
                     designs keep ``record_digest`` / ``""``, the format they were written in);
    ``what``         how the log is named in a refusal ("the plan approval log").
    """

    def __init__(self, path: Path, *, key: Optional[bytes] = None,
                 anchors: Optional[Path] = None, log_id: Optional[str] = None,
                 digest_field: str = "hash", genesis: str = "genesis",
                 what: str = "decision log", protect: Iterable[Path] = (),
                 lock_timeout: float = 30.0) -> None:
        self.path = Path(path)
        self._key = key
        self.protect = [self.path.parent, *[Path(p) for p in protect]]
        env = os.environ.get(ANCHORS_ENV, "").strip()
        if anchors is not None:
            self.anchors_path = Path(anchors)
        elif env:
            self.anchors_path = Path(env)
        else:
            self.anchors_path = self.path.parent / ANCHORS_NAME
        if log_id is None:
            log_id = self.path.name if self.anchors_path.parent.resolve() == \
                self.path.parent.resolve() else self.path.resolve().as_posix()
        self.log_id = str(log_id)
        self.digest_field = digest_field
        self.genesis = genesis
        self.what = what
        self.lock_timeout = lock_timeout

    # --- plumbing -------------------------------------------------------------------------------
    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = approval_key(protect=self.protect)
        return self._key

    @property
    def lock_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.lock")

    def lock(self, timeout: Optional[float] = None):
        """The log's cross-process append lock (re-entrant). Callers that read, decide and append
        as ONE step hold it across all three."""
        return file_lock(self.lock_path, timeout=self.lock_timeout if timeout is None else timeout,
                         on_timeout=lambda m: DecisionRefused("LEDGER_BUSY", m, 409))

    def _anchors(self) -> AnchorLedger:
        return AnchorLedger(self.key, self.anchors_path)

    def _wait_for_writer(self):
        """For a READ: wait for a writer that may be between its two writes, by taking the lock
        only when its file exists. A read never creates the lock file: with no lock file, no
        writer is mid-append (every writer creates it before it writes)."""
        return self.lock() if self.lock_path.is_file() else contextlib.nullcontext()

    def _read_scan(self) -> "_Scan":
        """`_scan` for a READ: a log (or anchor ledger) that exists but cannot be verified for want
        of a key is NOT READABLE (the refusal says so). A log that does not exist needs no key."""
        if not self.path.is_file() and not self.anchors_path.is_file():
            return _Scan()                                                # state "empty"
        try:
            self.key
        except DecisionRefused as exc:
            raise DecisionRefused(exc.code, f"the {self.what} is not readable: {exc.message}",
                                  exc.status) from None
        s = self._scan()
        if s.state in ("pending", "torn"):
            with self._wait_for_writer():
                s = self._scan()
        return s

    def _refuse(self, why: str) -> DecisionRefused:
        return DecisionRefused(LEDGER_INTEGRITY_FAILED,
                               f"the {self.what} does not verify ({why}): the append-only, sealed "
                               f"hash chain is broken or was rolled back, so no decision in it is "
                               f"trusted until a person decides again")

    def _digest(self, rec: Mapping[str, Any]) -> str:
        return digest_of(rec, exclude=(self.digest_field, "seal"))

    # --- verification -----------------------------------------------------------------------------
    def _scan(self) -> _Scan:
        s = _Scan()
        anchor, why = self._anchors().latest(self.log_id)
        if why:
            s.state, s.why = "broken", f"the anchor ledger does not verify: {why}"
            return s
        s.anchor = anchor
        if not self.path.is_file():
            if anchor is not None:
                s.state, s.why = "broken", (f"the log is missing, but the anchor ledger holds "
                                            f"{anchor.get('seq')} record(s) of it")
            else:
                s.state = "empty"
            return s
        raw = self.path.read_bytes()
        s.raw = raw
        if not raw.strip():
            if anchor is not None and anchor.get("seq"):
                s.state, s.why = "broken", (f"the log is empty, but the anchor ledger holds "
                                            f"{anchor.get('seq')} record(s) of it")
            else:
                s.state = "empty"
            return s
        chunks = raw.split(b"\n")
        terminated = raw.endswith(b"\n")
        if terminated:
            chunks = chunks[:-1]
        parsed: list[tuple[int, Any, bool]] = []          # (end offset, record, last chunk)
        offset = 0
        for i, chunk in enumerate(chunks):
            offset += len(chunk) + 1
            last = i == len(chunks) - 1
            if not chunk.strip():
                continue
            try:
                parsed.append((offset, json.loads(chunk), last))
            except ValueError:
                if last and not terminated:
                    s.torn = chunk
                    break
                s.state, s.why = "broken", f"line {i + 1} is not a record"
                return s
        if parsed and not any(isinstance(r, dict) and "seal" in r for _, r, _ in parsed):
            s.state = "broken" if anchor is not None else "legacy"
            s.why = ("it was written before decisions were sealed" if anchor is None else
                     "its records are not sealed, but the anchor ledger anchors it")
            return s
        head, seq = self.genesis, 0
        for n, (end, rec, last) in enumerate(parsed, start=1):
            if not isinstance(rec, dict) or rec.get(self.digest_field) != self._digest(rec) or \
                    not hmac.compare_digest(str(rec.get("seal") or ""),
                                            seal_of(self.key, str(rec.get(self.digest_field)))):
                if last and not terminated:
                    s.torn = chunks[-1]
                    break
                s.state, s.why = "broken", f"record {n}: its seal or digest does not verify"
                return s
            if rec.get("seq") != seq + 1 or rec.get("prev") != head:
                s.state, s.why = "broken", f"record {n}: the chain has a gap (seq or prev)"
                return s
            seq, head = rec["seq"], rec[self.digest_field]
            s.records.append(rec)
            s.valid_bytes = end if (terminated or not last) else len(raw)
        n = len(s.records)
        aseq = int(anchor["seq"]) if anchor is not None else 0
        ahead = anchor.get("head") if anchor is not None else None
        if s.torn:
            if aseq == n and (n == 0 or ahead == s.records[-1][self.digest_field]):
                s.state, s.why = "torn", "its last append was interrupted mid-write"
            else:
                s.state, s.why = "broken", "its last line is torn and the log does not match its " \
                                           "anchor"
            return s
        if aseq == n and (n == 0 or ahead == s.records[-1][self.digest_field]):
            s.state = "ok"
        elif aseq == n - 1 and (aseq == 0 or ahead == s.records[-2][self.digest_field]):
            s.state, s.why = "pending", (f"record {n} was appended but never anchored (an "
                                         f"interrupted append)")
        elif aseq > n:
            s.state, s.why = "broken", (f"the log holds {n} record(s), the anchor ledger anchors "
                                        f"{aseq}: it was rolled back")
        else:
            s.state, s.why = "broken", (f"the log holds {n} record(s), the anchor ledger anchors "
                                        f"{aseq} with another head: it was rewritten, or records "
                                        f"were added outside the ledger")
        return s

    def _visible(self, recs: list[dict], raw: bool) -> list[dict]:
        return list(recs) if raw else [r for r in recs if LEDGER_EVENT not in r]

    def read(self, *, raw: bool = False) -> list[dict]:
        """Every decision record, after verifying the WHOLE log and its anchor. Raises
        `DecisionRefused` (`LEDGER_INTEGRITY_FAILED`) otherwise. Never writes: an interrupted
        append is waited for (the lock, when one exists), and refused if it is still incomplete.
        Never creates anything: no key, no lock file (R-029 TIDY2)."""
        s = self._read_scan()
        if s.state in ("ok", "empty"):
            return self._visible(s.records, raw)
        if s.state == "legacy":
            raise DecisionRefused(LEDGER_INTEGRITY_FAILED,
                                  f"the {self.what} was written before decisions were sealed; "
                                  f"nothing in it is trusted. Decide again (re-approve): the next "
                                  f"decision starts a sealed log and keeps the old one aside")
        if s.state in ("pending", "torn"):
            raise DecisionRefused(LEDGER_INTEGRITY_FAILED,
                                  f"the {self.what} has an interrupted append ({s.why}); it is "
                                  f"completed by the next decision, or by an operator's recovery")
        raise self._refuse(s.why)

    def inspect(self) -> tuple[list[dict], str]:
        """(the verified PREFIX of decision records, why the log does not verify or ""). Never
        raises for a broken log and never writes: for a view that must still say whose a broken
        log was and why it is not honoured. Nothing in a log with a reason is evidence."""
        try:
            s = self._read_scan()
        except DecisionRefused as exc:
            return [], exc.message
        if s.state in ("ok", "empty"):
            return self._visible(s.records, False), ""
        if s.state == "legacy":
            why = "it was written before decisions were sealed; decide again"
        elif s.state in ("pending", "torn"):
            why = f"an append was interrupted ({s.why}); the next decision completes it"
        else:
            why = s.why
        return self._visible(s.records, False), why

    def interrupted(self) -> bool:
        """Is the log's last append incomplete (waiting for the lock first, when one exists)?"""
        if not self.path.is_file():
            return False
        with self._wait_for_writer():
            return self._scan().state in ("pending", "torn")

    def verify(self) -> tuple[bool, str]:
        """(verified, why not) without raising."""
        try:
            self.read()
        except DecisionRefused as exc:
            return False, exc.message
        return True, ""

    # --- writing ----------------------------------------------------------------------------------
    def _write(self, recs: list[dict], payload: Mapping[str, Any]) -> dict:
        last = recs[-1] if recs else None
        rec = {k: v for k, v in dict(payload).items()
               if k not in (*_ENVELOPE, self.digest_field)}
        rec["seq"] = (last["seq"] if last else 0) + 1
        rec["prev"] = last[self.digest_field] if last else self.genesis
        rec[self.digest_field] = self._digest(rec)
        rec["seal"] = seal_of(self.key, rec[self.digest_field])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lead = b""
        if self.path.is_file() and self.path.stat().st_size:
            with open(self.path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                lead = b"" if fh.read(1) == b"\n" else b"\n"
        with open(self.path, "ab") as fh:
            fh.write(lead + json.dumps(rec, sort_keys=True, default=str).encode("utf-8") + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        recs.append(rec)
        self._anchors().anchor(self.log_id, rec["seq"], rec[self.digest_field])
        return rec

    def _recover_locked(self, s: _Scan) -> _Scan:
        """Complete an interrupted append (the caller holds the lock and has re-scanned)."""
        if s.state == "pending":
            done = s.records[-1]
            self._anchors().anchor(self.log_id, done["seq"], done[self.digest_field])
            self._write(s.records, {LEDGER_EVENT: "recovered", "at": _now(),
                                    "completed_seq": done["seq"],
                                    "note": "an append was interrupted after its record was "
                                            "written and before it was anchored; the sealed "
                                            "record was anchored on recovery"})
        elif s.state == "torn":
            sha = hashlib.sha256(s.torn).hexdigest()
            side = self.path.with_name(f"{self.path.name}.torn-{sha[:12]}")
            side.write_bytes(s.torn)
            with open(self.path, "r+b") as fh:
                fh.truncate(s.valid_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            self._write(s.records, {LEDGER_EVENT: "recovered", "at": _now(),
                                    "torn_sha256": sha, "torn_file": side.name,
                                    "note": "an append was interrupted mid-write; the torn, "
                                            "unsealed fragment was moved aside"})
        return self._scan()

    def recover(self) -> list[dict]:
        """An operator's explicit recovery of an interrupted append (see the module). Returns the
        log as verified afterwards; a log broken in any other way is refused, never repaired."""
        with self.lock():
            s = self._scan()
            if s.state in ("pending", "torn"):
                s = self._recover_locked(s)
            if s.state not in ("ok", "empty"):
                raise self._refuse(s.why)
            return self._visible(s.records, True)

    def append(self, payload: Mapping[str, Any]) -> dict:
        """Verify the whole log and its anchor, then append ONE sealed record and anchor it, all
        under the log's lock. Returns the record as written. A broken log refuses
        (`LEDGER_INTEGRITY_FAILED`) and nothing is written; a legacy log is superseded. With no
        provisioned key it refuses (`APPROVAL_KEY_UNAVAILABLE`) before taking the lock, so nothing
        at all is written."""
        self.key                                     # an explicit refusal, before any write
        with self.lock():
            s = self._scan()
            if s.state in ("pending", "torn"):
                s = self._recover_locked(s)
            if s.state == "legacy":
                sha = hashlib.sha256(s.raw).hexdigest()
                aside = self.path.with_name(f"{self.path.name}.legacy-{sha[:12]}")
                os.replace(self.path, aside)
                s = self._scan()
                if s.state != "empty":
                    raise self._refuse(s.why or "the legacy log could not be set aside")
                self._write(s.records, {LEDGER_EVENT: "superseded_legacy", "at": _now(),
                                        "legacy_file": aside.name, "legacy_sha256": sha,
                                        "note": "the log written before decisions were sealed "
                                                "was set aside; nothing in it is evidence"})
            elif s.state not in ("ok", "empty"):
                raise self._refuse(s.why)
            return self._write(s.records, payload)


__all__ = [
    "APPROVER_NOT_AUTHENTICATED", "MACHINE_ACTOR", "DECISION_STALE", "DECISION_ALREADY_FINAL",
    "LEDGER_INTEGRITY_FAILED", "REFUSAL_CODES", "LEGACY_CODES", "APPROVAL_KEY_UNAVAILABLE",
    "standard_code", "refusal", "DecisionRefused",
    "MACHINE_PREFIXES", "ACTOR_FIELDS", "is_machine_principal", "Actor", "actor_of",
    "require_person",
    "canonical", "digest_of", "seal_of", "APPROVAL_KEY_ENV", "APPROVAL_KEY_FILE_ENV",
    "approval_key_path", "approval_key", "provision_approval_key",
    "LockTimeout", "file_lock", "lock_held", "atomic_write_text",
    "ANCHORS_ENV", "ANCHORS_NAME", "AnchorLedger", "LEDGER_EVENT", "DecisionLedger",
]
