"""Operator ontology state the engine must honour — today: which kinds are currently DEPRECATED.

The `/ontology` page never deletes a kind from `ontology.yaml` (existing graph rows read their kind
back through the vocabulary, so deleting a declaration would make them unreadable). "Remove"
therefore DEPRECATES: an append-only record in the ontology state file. Until this module existed
only the gateway read that record, so extraction kept offering a kind the owner had removed — the
system did not "only map to" the owner's ontology. `extraction_vocabulary` now withholds any kind
that is currently deprecated in a bound pack.

The state file is written by `apps/gateway/.../ontology_routes.py`, which resolves its path through
`state_path()` below, so the writer and this reader cannot drift apart.

Durability and failure (review R-017, finding 8)
------------------------------------------------
* **Writes are atomic.** `atomic_write_text` writes a same-directory temporary file, flushes and
  fsyncs it, then `os.replace`s it over the target. A crash or a full disk leaves either the old
  file or the new one, never half of either.
* **Writers are serialised across processes.** `file_lock` is an OS-level exclusive lock on a
  sibling ``.lock`` file (``msvcrt`` on Windows, ``fcntl`` elsewhere), so two gateway workers doing
  read-modify-write on the state cannot lose each other's records.
* **An unreadable state file FAILS CLOSED.** It used to read as empty state: extraction stopped
  honouring every deprecation, and the next gateway write overwrote the damaged file from an empty
  baseline, destroying the proposal and deprecation history. It now raises `OntologyStateError`;
  extraction refuses to run against a vocabulary it cannot establish, and the gateway refuses
  reads and writes with ``ONTOLOGY_STATE_UNREADABLE`` until an operator repairs the file. An ABSENT
  file is still empty state — that is a deployment that has never made a decision.

Review R-019 (Codex batch 2, track ontology-r2)
-----------------------------------------------
* **Validation is the whole schema, not the top-level shape** (finding 6). ``"enabled": "false"``
  used to pass and ``bool("false")`` switched auto-approve ON; a malformed deprecation record was
  silently skipped, which REACTIVATED that kind for extraction. `validate_state` now checks every
  record (exact booleans, required fields, known axes/statuses, unique ids) and any violation
  raises `OntologyStateError` — nothing is skipped.
* **Atomic replacement keeps the file's metadata** (finding 8): the temporary file is given the
  target's permission bits (and, where the process may, its owner) before ``os.replace``, so a
  ``0644`` pack does not silently become ``0600`` and unreadable to another service account.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

log = logging.getLogger(__name__)

STATE_ENV = "WALNUT_ONTOLOGY_STATE_PATH"

#: How long a writer waits for another process's lock before refusing (it never proceeds unlocked).
LOCK_TIMEOUT_S = 10.0


class OntologyStateError(RuntimeError):
    """The ontology state file exists but cannot be trusted (unreadable, not JSON, wrong shape), or
    it could not be written safely. Raised rather than treated as empty state: fail closed."""


def _repo_root() -> Path:
    """The project root: the parent of the code tree (the directory with `packages/` and `apps/`).
    Found structurally, never by a domain folder name (`test_no_domain_branching`)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "packages").is_dir() and (parent / "apps").is_dir():
            return parent.parent
    return Path.cwd()                                                # pragma: no cover


def state_path() -> Path:
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override)
    return _repo_root() / ".databridge" / "ontology_state.json"


# --- durable primitives ----------------------------------------------------------------------


def atomic_write_text(path: Path, text: str, *, newline: Optional[str] = None) -> None:
    """Replace ``path`` with ``text`` atomically: temp file in the same directory, flush, fsync,
    ``os.replace``. On any failure the temp file is removed and ``path`` is untouched.

    ``newline`` is passed to ``open`` — pass ``"\\r\\n"`` or ``"\\n"`` to reproduce a file's own line
    endings byte-for-byte (``None`` is the platform default, as ``Path.write_text`` used)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        copy_metadata(path, tmp)
        # Windows refuses `os.replace` while another process holds the target open for reading;
        # a short bounded retry covers a concurrent reader without ever writing in place.
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def copy_metadata(src: Path, dst: Path) -> None:
    """Give ``dst`` the permission bits (and, where this process may, the owner and group) of
    ``src``, when ``src`` exists. `tempfile.mkstemp` creates ``0600``; replacing a ``0644`` file
    with it would make the file unreadable to any other account (review R-019 finding 8). A
    permission error on ``chown`` is expected for an unprivileged process that does not own the
    file and is ignored — the bits are still copied. On Windows ``chmod`` carries only the
    read-only flag; the temp file inherits the directory's ACL as the original did."""
    try:
        st = os.stat(src)
    except FileNotFoundError:
        return
    os.chmod(dst, stat.S_IMODE(st.st_mode))
    if os.name != "nt" and hasattr(os, "chown"):                      # pragma: no cover - posix
        with contextlib.suppress(OSError):
            os.chown(dst, st.st_uid, st.st_gid)


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_DEPTH = threading.local()


def _depths() -> dict[str, int]:
    d = getattr(_DEPTH, "d", None)
    if d is None:
        d = _DEPTH.d = {}
    return d


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """An exclusive lock held across processes (OS file lock) and threads (a per-path RLock taken
    first, so the OS lock is never requested twice by one process). Times out CLOSED: a writer that
    cannot get the lock raises `OntologyStateError`, it never proceeds unlocked."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        tlock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    if not tlock.acquire(timeout=timeout):
        raise OntologyStateError(f"timed out waiting for {lock_path}")
    try:
        # Re-entrant within a thread: only the outermost holder takes the OS lock.
        depths = _depths()
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
                        import msvcrt                                 # noqa: PLC0415
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:                                             # pragma: no cover - posix
                        import fcntl                                  # noqa: PLC0415
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise OntologyStateError(
                            f"timed out waiting for the lock {lock_path}; another writer holds "
                            f"it. Nothing was written.") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    if os.name == "nt":
                        import msvcrt                                 # noqa: PLC0415
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:                                             # pragma: no cover - posix
                        import fcntl                                  # noqa: PLC0415
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
    finally:
        depths = _depths()
        depths[key] -= 1
        if not depths[key]:
            del depths[key]
        tlock.release()


def state_lock_path() -> Path:
    p = state_path()
    return p.with_name(p.name + ".lock")


#: Every status a stored proposal may carry. ``applying`` is the durable middle state of an
#: approval (R-019 finding 3): decided, pack write not yet confirmed.
PROPOSAL_STATUSES = frozenset({"pending", "escalated", "applying", "approved", "rejected",
                               "refused", "write_failed"})
_AXES = frozenset({"node", "edge"})


def _need(cond: bool, path: Path, what: str) -> None:
    if not cond:
        raise OntologyStateError(
            f"ontology state at {path}: {what}. Refusing to use it — a record that is skipped or "
            f"coerced changes what is deprecated or auto-approved. Repair or restore the file.")


def _str(v: Any) -> bool:
    return isinstance(v, str) and bool(v)


def validate_state(data: Any, path: Optional[Path] = None) -> dict[str, Any]:
    """The WHOLE state checked against its schema (review R-019 finding 6). Returns ``data``
    unchanged; raises `OntologyStateError` on the first violation. Unknown top-level keys and
    unknown fields inside a record are allowed (forward compatibility); a KNOWN field of the wrong
    type is not, and nothing is skipped or coerced."""
    path = path or state_path()
    _need(isinstance(data, dict), path, "not a JSON object")
    for key in ("deprecated_kinds", "proposals", "operations"):
        _need(key not in data or isinstance(data[key], list), path, f"{key!r} is not a list")
    if "auto_approve" in data:
        aa = data["auto_approve"]
        _need(isinstance(aa, dict), path, "'auto_approve' is not an object")
        # Exactly a JSON boolean: `bool("false")` is True, which is how a string turned it on.
        _need(type(aa.get("enabled", False)) is bool, path,
              f"'auto_approve.enabled' must be true or false, not {aa.get('enabled')!r}")
        for f in ("set_by", "set_at"):
            _need(aa.get(f) is None or isinstance(aa.get(f), str), path,
                  f"'auto_approve.{f}' must be a string or null")
    for i, rec in enumerate(data.get("deprecated_kinds") or []):
        where = f"deprecated_kinds[{i}]"
        _need(isinstance(rec, dict), path, f"{where} is not an object")
        _need(_str(rec.get("pack")), path, f"{where}.pack must be a non-empty string")
        _need(rec.get("axis") in _AXES, path, f"{where}.axis must be one of {sorted(_AXES)}")
        _need(_str(rec.get("kind")), path, f"{where}.kind must be a non-empty string")
        _need(type(rec.get("reactivated", False)) is bool, path,
              f"{where}.reactivated must be true or false")
    seen: set[str] = set()
    for i, rec in enumerate(data.get("proposals") or []):
        where = f"proposals[{i}]"
        _need(isinstance(rec, dict), path, f"{where} is not an object")
        _need(_str(rec.get("id")), path, f"{where}.id must be a non-empty string")
        _need(rec["id"] not in seen, path, f"{where}.id {rec['id']!r} is not unique")
        seen.add(rec["id"])
        _need(rec.get("status") in PROPOSAL_STATUSES, path,
              f"{where}.status {rec.get('status')!r} is not one of {sorted(PROPOSAL_STATUSES)}")
        _need(_str(rec.get("pack")), path, f"{where}.pack must be a non-empty string")
        _need(rec.get("axis") in _AXES, path, f"{where}.axis must be one of {sorted(_AXES)}")
        _need(_str(rec.get("name")), path, f"{where}.name must be a non-empty string")
        _need(isinstance(rec.get("binds_to"), str), path, f"{where}.binds_to must be a string")
        for f in ("src", "dst"):
            v = rec.get(f, [])
            _need(isinstance(v, list) and all(isinstance(x, str) for x in v), path,
                  f"{where}.{f} must be a list of strings")
        _need(type(rec.get("declares_subject", False)) is bool, path,
              f"{where}.declares_subject must be true or false")
        ev = rec.get("evidence", [])
        _need(isinstance(ev, list) and all(isinstance(x, dict) for x in ev), path,
              f"{where}.evidence must be a list of objects")
    _need("identity_rule_decisions" not in data
          or isinstance(data["identity_rule_decisions"], list), path,
          "'identity_rule_decisions' is not a list")
    for i, rec in enumerate(data.get("identity_rule_decisions") or []):
        where = f"identity_rule_decisions[{i}]"
        _need(isinstance(rec, dict), path, f"{where} is not an object")
        for f in ("pack", "kind", "principal", "at"):
            _need(_str(rec.get(f)) and rec[f].strip() == rec[f], path,
                  f"{where}.{f} must be a non-empty string")
        _need(rec.get("rule") in IDENTITY_RULE_TYPES, path,
              f"{where}.rule must be one of {sorted(IDENTITY_RULE_TYPES)}")
        _need(rec.get("decision") in IDENTITY_RULE_DECISIONS, path,
              f"{where}.decision must be one of {sorted(IDENTITY_RULE_DECISIONS)}")
    ops: set[str] = set()
    for i, rec in enumerate(data.get("operations") or []):
        where = f"operations[{i}]"
        _need(isinstance(rec, dict), path, f"{where} is not an object")
        _need(_str(rec.get("op_id")), path, f"{where}.op_id must be a non-empty string")
        _need(rec["op_id"] not in ops, path, f"{where}.op_id {rec['op_id']!r} is not unique")
        ops.add(rec["op_id"])
        _need(_str(rec.get("status")), path, f"{where}.status must be a non-empty string")
    return data


def read_state() -> dict[str, Any]:
    """The state file as a dict. Absent → ``{}``. Present but unreadable, not JSON, or failing
    `validate_state` anywhere → `OntologyStateError` (fail closed)."""
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                                         # noqa: BLE001
        raise OntologyStateError(
            f"ontology state at {path} is unreadable ({exc}). Refusing to treat it as empty — that "
            f"would silently lift every deprecation and let the next write erase the history. "
            f"Repair or restore the file.") from exc
    return validate_state(data, path)


def write_state(state: dict[str, Any]) -> None:
    """Atomically replace the state file. Callers doing read-modify-write hold `file_lock(
    state_lock_path())` around the whole transaction."""
    try:
        text = json.dumps(state, indent=2, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise OntologyStateError(f"ontology state is not serialisable: {exc}") from exc
    try:
        atomic_write_text(state_path(), text)
    except OSError as exc:
        raise OntologyStateError(
            f"the ontology state could not be written ({exc}); the previous state file is "
            f"unchanged") from exc


def _read_state() -> dict[str, Any]:
    return read_state()


def currently_deprecated(packs: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """`(node_kinds, edge_kinds)` currently deprecated in any of ``packs``.

    Last record per (pack, axis, kind) wins; a record carrying ``reactivated`` means the kind is
    offered again (the history stays in the list — same rule as the gateway's `_deprecation`).

    Raises `OntologyStateError` when the state file exists but cannot be read: extraction must not
    run against a vocabulary whose removals it cannot establish.
    """
    wanted = set(packs)
    last: dict[tuple[str, str, str], dict] = {}
    # Every record was validated by `read_state` (R-019 finding 6) — a malformed one raised there.
    # Nothing is skipped here: a skipped deprecation is a silent reactivation.
    for rec in _read_state().get("deprecated_kinds", []) or []:
        key = (rec["pack"], rec["axis"], rec["kind"])
        if key[0] in wanted:
            last[key] = rec
    nodes = frozenset(k for (_, axis, k), r in last.items() if axis == "node" and not r.get("reactivated"))
    edges = frozenset(k for (_, axis, k), r in last.items() if axis == "edge" and not r.get("reactivated"))
    return nodes, edges


# --- identity rules (FIN-04, wiki/20-decisions/D-004) -------------------------------------------
#
# A pack DECLARES which merge rules a non-subject kind may use (`packs.IdentityRule`); a named
# human APPROVES one for this deployment. The approval is a record here, append-only like the
# deprecations: the last decision per (pack, kind, rule) wins, and a revocation is a new record,
# never an edit. Nothing in the engine approves a rule, and an unapproved rule merges nothing.
#
# The principal is whatever authenticated identity the CALLER established. This module cannot
# authenticate anyone; it refuses a blank principal and records the one it is given. The HTTP
# route that calls it must take the principal from the authenticated session, never from the body.

#: Kept identical to `packs.IDENTITY_RULE_TYPES` (asserted by the tests) without importing packs
#: here, so this module stays importable on its own.
IDENTITY_RULE_TYPES = frozenset({"business_key", "exact_name"})
IDENTITY_RULE_DECISIONS = frozenset({"approved", "revoked"})

_RULE_CACHE: dict[tuple, frozenset] = {}
_RULE_CACHE_LOCK = threading.Lock()


def _state_identity() -> tuple:
    path = state_path()
    try:
        st = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), st.st_size, st.st_mtime_ns)


def approved_identity_rules(packs: Iterable[str]) -> frozenset[tuple[str, str]]:
    """`(kind, rule)` pairs a named human has approved, and not since revoked, in any of
    ``packs``. Last record per (pack, kind, rule) wins.

    Cached on the state file's (path, size, mtime): `merge.resolve_existing` asks this on the
    upsert path, and must not parse JSON per row. Raises `OntologyStateError` when the state file
    exists but cannot be read: a merge must not run on rules it cannot establish (fail closed).
    """
    wanted = tuple(sorted(set(packs)))
    key = (_state_identity(), wanted)
    with _RULE_CACHE_LOCK:
        hit = _RULE_CACHE.get(key)
    if hit is not None:
        return hit
    last: dict[tuple[str, str, str], dict] = {}
    for rec in _read_state().get("identity_rule_decisions", []) or []:
        if rec["pack"] in wanted:
            last[(rec["pack"], rec["kind"], rec["rule"])] = rec
    out = frozenset((k, r) for (_, k, r), rec in last.items() if rec["decision"] == "approved")
    with _RULE_CACHE_LOCK:
        if len(_RULE_CACHE) > 64:
            _RULE_CACHE.clear()
        _RULE_CACHE[key] = out
    return out


def record_identity_rule_decision(*, pack: str, kind: str, rule: str, principal: str,
                                  decision: str = "approved", vocab=None,
                                  at: Optional[str] = None) -> dict[str, Any]:
    """Append one human decision on a DECLARED identity rule. Returns the record written.

    Refused (`ValueError`) unless the bound vocabulary declares exactly this rule for this kind,
    in this pack: a decision can approve what a pack declared, never invent a rule, and never
    touch a subject kind (which can carry no rule at all). ``principal`` must be a non-blank,
    authenticated identity supplied by the caller.
    """
    if not isinstance(principal, str) or not principal.strip():
        raise ValueError("an identity rule decision needs a named, authenticated principal")
    if decision not in IDENTITY_RULE_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(IDENTITY_RULE_DECISIONS)}")
    if rule not in IDENTITY_RULE_TYPES:
        raise ValueError(f"rule must be one of {sorted(IDENTITY_RULE_TYPES)}")
    if vocab is None:
        from .packs import active_vocabulary                           # noqa: PLC0415
        vocab = active_vocabulary()
    declared = vocab.identity_rule_for(kind)
    if declared is None:
        raise ValueError(f"no bound pack declares identity rules for {kind!r} (a subject kind "
                         f"never has any)")
    if declared.pack != pack or pack not in vocab.packs:
        raise ValueError(f"the identity rule for {kind!r} is declared by pack {declared.pack!r}, "
                         f"not {pack!r}")
    if rule not in declared.rule_types:
        raise ValueError(f"pack {pack!r} does not declare a {rule!r} rule for {kind!r} "
                         f"(declared: {list(declared.rule_types)})")
    import datetime as _dt                                             # noqa: PLC0415
    rec = {"pack": pack, "kind": str(kind), "rule": rule, "decision": decision,
           "principal": principal.strip(),
           "at": at or _dt.datetime.now(_dt.timezone.utc).isoformat()}
    with file_lock(state_lock_path()):
        state = read_state()
        state.setdefault("identity_rule_decisions", []).append(rec)
        validate_state(state)
        write_state(state)
    return rec
