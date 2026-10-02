"""The decision producers, projected into `/activity` (R-028 CONS #8).

Every human decision family the platform records becomes rows of the ONE feed, each with the
common actor object (`activity_actor`), an explicit kind, and an ``integrity`` that says what was
verified about the record — never more:

    plan_approval      <sources>/<sid>.plan.approvals.jsonl     THE decision ledger (sealed,
    mapping_approval   <sources>/<sid>.mapping.approvals.jsonl  chained, anchored, verified)
    link_decision      (the same mapping ledger: link_approve / link_reject)
    setup_documents    <aa>/_setup/<sid>/decisions.jsonl        the ledger once its producer
    setup_handoff      <aa>/_setup/<sid>/handoffs.jsonl         seals it; plain JSONL until then
    design_approval    <materialise>/designs/<id>/events.jsonl  sealed + anchored (`DesignStore`)
    design_build       (the same sealed log)
    outbox_decision    the outbox SQLite file (read-only)       append-only rows (DB triggers)
    one_time_task      <one_time>/<task_id>/task.json           a task that failed before its run
                                                                was recorded (a run that WAS
                                                                recorded is read with the runs)
    decision_log       a log above that did NOT verify: one loud row, code
                       ``LEDGER_INTEGRITY_FAILED``; nothing in that log is shown as a decision

**Read-only.** Nothing is created: a store that does not exist is not read (the outbox is opened
``mode=ro``; a design store is read only when a design directory exists).

**Who sees what** — the standard rule (CONS standard 6): the record's OWNER, or an operator
(`agent_ownership.is_operator`). The owner of a source-scoped decision is the creator of the
source's keeper agent (Set up's ``keeper-<source>``), and the person who decided it sees their
own decision; a design's owner is its proposer; an outbox draft's owner is who filed it; a
one-time task's owner is who ran it. A record whose owner is not recorded is operator-only. A
diagnostic names a file only to someone who may see that file's records.

**Bounded.** At most `MAX_OWNER_DIRS` logs per family, each at most `MAX_JSONL_BYTES`, at most
`MAX_BYTES_PER_KIND` per family; past a bound the family is marked incomplete (its count is not
served as a total). Values are scalars from named fields only; free text is withheld in place.
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .activity_actor import (
    FREE_TEXT,
    actor_from_record,
    actor_pid,
    make_actor,
    withheld,
)
from .activity_routes import (
    MAX_BYTES_PER_KIND,
    MAX_DIR_ENTRIES,
    MAX_JSONL_BYTES,
    MAX_OWNER_DIRS,
    MAX_RECORD_BYTES,
    ActivitySources,
    Problems,
    Viewer,
    _base,
    _read_jsonl,
    _scalar,
    safe_name,
)

#: The public refusal code of a decision log that does not verify (CONS standard 3).
LEDGER_INTEGRITY_FAILED = "LEDGER_INTEGRITY_FAILED"
#: ``integrity`` — what was verified about a decision row, and nothing more.
SEALED = "sealed_and_anchored"      # HMAC-sealed, hash-chained, anchored outside the log
APPEND_ONLY = "append_only_store"   # rows of a store whose database refuses UPDATE/DELETE
UNVERIFIED = "unverified"           # a plain record: shown as recorded, nothing proven
FAILED = "failed"

PLAN_SUFFIX = ".plan.approvals.jsonl"
MAPPING_LOG_SUFFIX = ".mapping.approvals.jsonl"


def _no_run(**rest: Any) -> dict:
    """The run-shaped fields every row carries; a decision row has none of them."""
    return {"produced_by": None, "models_used": None, "tokens": None, "duration_ms": None,
            "run_id": None, **rest}


# ================================================================================================
# who owns a source-scoped decision
# ================================================================================================
def keeper_owner(viewer: Viewer, source_id: str) -> Optional[str]:
    """The creator of the source's keeper agent (Set up's ``keeper-<source>``), or None."""
    try:
        from databridge_adapter_agents.setup import keeper_id               # noqa: PLC0415
        agent = keeper_id(source_id)
    except Exception:                                                     # noqa: BLE001
        return None
    return viewer.adapter_agent_owner(agent) if safe_name(agent) else None


def may_see(viewer: Viewer, *owners: Optional[str]) -> bool:
    """Owner OR operator — the one platform rule. No recorded owner: operator-only."""
    return viewer.operator or viewer.pid in {o for o in owners if o}


# ================================================================================================
# bounded listing
# ================================================================================================
def _listed(root: Optional[Path], pattern: str, kind: str, incomplete: set[str], *,
            dirs: bool = False) -> list[Path]:
    """Entries of ``root`` matching ``pattern``, sorted; at most `MAX_DIR_ENTRIES` looked at and
    `MAX_OWNER_DIRS` kept (past either, ``kind`` is incomplete). Nothing is opened."""
    if root is None or not Path(root).is_dir():
        return []
    out: list[Path] = []
    for n, p in enumerate(Path(root).iterdir()):
        if n >= MAX_DIR_ENTRIES or len(out) >= MAX_OWNER_DIRS:
            incomplete.add(kind)
            break
        if p.name.startswith(".") or not fnmatch.fnmatch(p.name, pattern):
            continue
        if (p.is_dir() if dirs else p.is_file()):
            out.append(p)
    return sorted(out)


def _size(path: Path) -> Optional[int]:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return None


class _Budget:
    """Bytes one family may read in one request (`MAX_BYTES_PER_KIND`)."""

    def __init__(self) -> None:
        self.spent = 0

    def take(self, n: int) -> bool:
        if self.spent + n > MAX_BYTES_PER_KIND:
            return False
        self.spent += n
        return True


def _admit(path: Path, kind: str, budget: _Budget, problems: Problems, incomplete: set[str], *,
           visible: bool, cap: int = MAX_JSONL_BYTES) -> bool:
    """May ``path`` be read whole? Too large or over the family budget: no (and said so)."""
    size = _size(path)
    if size is None or size > cap:
        incomplete.add(kind)
        problems.report(visible, {"source": kind, "file": path.name,
                                  "error": f"larger than {cap} bytes (or unreadable); not read"})
        return False
    if not budget.take(size):
        incomplete.add(kind)
        problems.report(visible, {"source": kind, "file": path.name,
                                  "error": f"not read: this request already read "
                                           f"{MAX_BYTES_PER_KIND} bytes of {kind} records"})
        return False
    return True


def integrity_row(log: str, namespace: str, at: Any, *, family: str,
                  why_code: str = LEDGER_INTEGRITY_FAILED, **subject: Any) -> dict:
    """ONE loud row for a decision log that does not verify. Nothing in it is shown as a decision
    (fail closed); the row says which log, never the producer's error text."""
    return _base("decision_log", f"{log}/{namespace}", at,
                 status="integrity_failed", integrity=FAILED, log=log, family=family,
                 refusal={"code": why_code,
                          "message": "this decision log does not verify; no decision in it is "
                                     "shown or honoured until it is repaired"},
                 actor=make_actor(None, None), title=f"{log} for {namespace} does not verify",
                 loud=True, **_no_run(**subject))


# ================================================================================================
# plan approvals + mapping approvals (+ link decisions kept on the mapping)
# ================================================================================================
def read_ledger(path: Path, what: str) -> tuple[Optional[list[dict]], str]:
    """``(decision records, integrity)`` of a decision log, verified by THE decision ledger
    (`databridge_core.decision_ledger.DecisionLedger`: seal, chain and anchor, the anchor equal to
    the log) exactly as its producer reads it. ``(None, FAILED)`` when it does not verify — a
    tampered, rolled-back or unanchored log, and a legacy unsealed log (never evidence, R-028)."""
    try:
        from databridge_core.decision_ledger import DecisionLedger         # noqa: PLC0415
    except Exception:                                                     # noqa: BLE001
        return None, FAILED
    try:
        return list(DecisionLedger(path, what=what).read()), SEALED
    except Exception:                                                     # noqa: BLE001
        return None, FAILED


def _source_logs(viewer: Viewer, src: ActivitySources, kind: str, suffix: str, what: str,
                 problems: Problems, incomplete: set[str], out: list[dict]):
    """Yield ``(source_id, keeper owner, verified records)`` per log; a log that does not verify
    becomes ONE integrity row (for those who may see the source's decisions) instead."""
    budget = _Budget()
    for path in _listed(src.sources_dir, f"*{suffix}", kind, incomplete):
        sid = path.name[: -len(suffix)]
        keeper = keeper_owner(viewer, sid)
        if not _admit(path, kind, budget, problems, incomplete, visible=may_see(viewer, keeper)):
            continue
        recs, _integrity = read_ledger(path, what)
        if recs is None:
            if may_see(viewer, keeper):
                out.append(integrity_row(what, sid, None, family=kind, source_id=sid))
            continue
        yield sid, keeper, recs


def plan_approval_items(viewer: Viewer, src: ActivitySources, problems: Problems,
                        incomplete: set[str]) -> list[dict]:
    kind = "plan_approval"
    out: list[dict] = []
    for sid, keeper, recs in _source_logs(viewer, src, kind, PLAN_SUFFIX, "plan approval log",
                                          problems, incomplete, out):
        unsaved = {r.get("approval_hash") for r in recs
                   if r.get("event") == "plan_approval_not_saved"}
        for r in recs:
            if r.get("event") != "plan_approved" or r.get("source_id") != sid:
                continue
            actor = actor_from_record(r)
            if not may_see(viewer, actor_pid(actor), keeper):
                continue
            saved = r.get("hash") not in unsaved
            vouched = r.get("vouched_read_tools")
            out.append(_base(
                kind, f"{sid}/{r.get('seq') or str(r.get('hash') or '')[:16]}", r.get("at"),
                status="approved" if saved else "not_saved", decision="approved",
                source_id=sid, plan_digest=_scalar(r.get("plan_digest")),
                vouched_read_tools=len(vouched) if isinstance(vouched, list) else None,
                actor=actor, integrity=SEALED,
                title=(f"crawl plan for {sid} approved" if saved else
                       f"crawl plan for {sid} approved, but the approved plan was not saved "
                       f"(nothing was approved)"),
                loud=not saved, **_no_run()))
    return out


#: Ledger fields that are the LOG's, not the decision record's (`mapping.logged_approvals`).
_LEDGER_FIELDS = ("seq", "prev", "hash", "seal")


def _mapping_line_ok(rec: dict, sid: str) -> bool:
    """The checks `mapping.logged_approvals` applies to one verified line: its own digest (when
    the line keeps one), its source, a principal and how it was authenticated. A line failing
    them is never evidence."""
    try:
        from databridge_mcp_ingest.mapping import _record_digest            # noqa: PLC0415
    except Exception:                                                     # noqa: BLE001
        return False
    body = {k: v for k, v in rec.items() if k not in _LEDGER_FIELDS}
    own = body.get("record_digest") is None or body.get("record_digest") == _record_digest(body)
    return (own and rec.get("source_id") == sid
            and bool(str(rec.get("principal_id") or "").strip())
            and bool(str(rec.get("authenticated_by") or "").strip()))


def mapping_items(viewer: Viewer, src: ActivitySources, want: set[str], problems: Problems,
                  incomplete: set[str]) -> list[dict]:
    """Table approvals (``action: approve``) and link decisions (``link_approve`` /
    ``link_reject``) — both are decisions in the mapping's ONE decision ledger."""
    out: list[dict] = []
    kind = "mapping_approval" if "mapping_approval" in want else "link_decision"
    for sid, keeper, recs in _source_logs(viewer, src, kind, MAPPING_LOG_SUFFIX,
                                          "mapping approval log", problems, incomplete, out):
        for r in recs:
            action = r.get("action")
            if action == "approve" and not _mapping_line_ok(r, sid):
                continue
            if r.get("source_id") != sid:
                continue
            actor = actor_from_record(r)
            if actor is None or not may_see(viewer, actor_pid(actor), keeper):
                continue
            table = _scalar(r.get("table"))
            ident = f"{sid}/{table}/{r.get('seq') or str(r.get('hash') or '')[:16]}"
            delegated = bool(actor.get("delegation"))
            if action == "approve" and "mapping_approval" in want:
                out.append(_base(
                    "mapping_approval", ident, r.get("at"),
                    status="approved", decision="approved", source_id=sid, table=table,
                    mapping_digest=_scalar(r.get("digest")), actor=actor, integrity=SEALED,
                    delegated=delegated,
                    title=f"mapping of {sid}/{table} approved"
                          + (" (delegated)" if delegated else ""),
                    loud=False, **_no_run()))
            elif action in ("link_approve", "link_reject") and "link_decision" in want:
                lk = r.get("link") if isinstance(r.get("link"), dict) else {}
                decision = "approved" if action == "link_approve" else "rejected"
                target = f"{_scalar(lk.get('source'))}:{_scalar(lk.get('table'))}"
                column = _scalar(lk.get("column"))
                out.append(_base(
                    "link_decision", ident, r.get("at"),
                    status=decision, decision=decision, source_id=sid, table=table,
                    column=column, target=target, target_kind=_scalar(lk.get("target_kind")),
                    actor=actor, integrity=SEALED,
                    title=f"link {sid}/{table}.{column} -> {target} {decision}",
                    loud=False, **_no_run()))
    return out


# ================================================================================================
# setup: the documents decision and the hand-off authorisation
# ================================================================================================
def _is_sealed(path: Path) -> bool:
    """Was this log written by the decision ledger (its first record is sealed)?"""
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if raw.strip():
                    rec = json.loads(raw)
                    return isinstance(rec, dict) and "seal" in rec
    except (OSError, ValueError):
        return False
    return False


def _producer_on_ledger(module: str) -> bool:
    """Does the producer module write through the decision ledger yet (R-028 migration)?"""
    try:
        import importlib                                                  # noqa: PLC0415
        return "DecisionLedger" in vars(importlib.import_module(module))
    except Exception:                                                     # noqa: BLE001
        return False


def _setup_log(path: Path, kind: str, problems: Problems, incomplete: set[str], *,
               visible: bool) -> tuple[Optional[list[dict]], str]:
    """A setup decision log, read the way its producer reads it: through the decision ledger
    when it is sealed — or when the producer writes sealed logs (an unsealed one is then a legacy
    log, never evidence: ``None``); as plain append-only JSONL (``unverified``) while the producer
    still writes plain JSONL."""
    if _is_sealed(path) or _producer_on_ledger("databridge_adapter_agents.setup"):
        recs, integrity = read_ledger(path, f"setup {path.name}")
        return recs, integrity
    recs, lossy = _read_jsonl(path, problems, kind, visible=visible)
    if lossy:
        incomplete.add(kind)
    return recs, UNVERIFIED


def setup_items(viewer: Viewer, src: ActivitySources, want: set[str], problems: Problems,
                incomplete: set[str]) -> list[dict]:
    out: list[dict] = []
    root = Path(src.adapter_agents_dir) / "_setup"
    budget = _Budget()
    families = [(k, f) for k, f in (("setup_documents", "decisions.jsonl"),
                                    ("setup_handoff", "handoffs.jsonl")) if k in want]
    if not families:
        return out
    for d in _listed(root, "*", "setup_documents", incomplete, dirs=True):
        sid = d.name
        keeper = keeper_owner(viewer, sid)
        for kind, fname in families:
            path = d / fname
            if not path.is_file():
                continue
            if not _admit(path, kind, budget, problems, incomplete,
                          visible=may_see(viewer, keeper)):
                continue
            recs, integrity = _setup_log(path, kind, problems, incomplete,
                                         visible=may_see(viewer, keeper))
            if recs is None:
                if may_see(viewer, keeper):
                    out.append(integrity_row(f"setup {fname}", sid, None, family=kind,
                                             source_id=sid))
                continue
            for n, r in enumerate(recs):
                actor = actor_from_record(r)
                if kind == "setup_documents":
                    if r.get("kind") != "documents":
                        continue
                    if not may_see(viewer, actor_pid(actor), keeper):
                        continue
                    decision = _scalar(r.get("decision"))
                    out.append(_base(
                        kind, f"{sid}/{n}", r.get("at"), status=decision, decision=decision,
                        source_id=sid, discovery_run_id=_scalar(r.get("discovery_run_id")),
                        documents_digest=_scalar(r.get("documents_digest")),
                        actor=actor or make_actor(None, None), integrity=integrity,
                        title=f"documents of {sid}: {decision or 'decision not recorded'}",
                        loud=actor is None, **_no_run()))
                else:
                    agent = _scalar(r.get("agent_id"))
                    agent_owner = (viewer.adapter_agent_owner(agent)
                                   if isinstance(agent, str) and safe_name(agent) else None)
                    if not may_see(viewer, actor_pid(actor), keeper, agent_owner):
                        continue
                    binding = r.get("binding") if isinstance(r.get("binding"), dict) else {}
                    deferred = binding.get("deferred")
                    mapping = binding.get("mapping")
                    out.append(_base(
                        kind, f"{sid}/{n}", r.get("at"), status="authorised",
                        decision="handed_off", source_id=sid, adapter_agent=agent,
                        plan_digest=_scalar(binding.get("plan_digest")),
                        discovery_run_id=_scalar(binding.get("discovery_run_id")),
                        documents_digest=_scalar(binding.get("documents_digest")),
                        tables_bound=len(mapping) if isinstance(mapping, dict) else None,
                        deferred=len(deferred) if isinstance(deferred, list) else None,
                        actor=actor or make_actor(None, None), integrity=integrity,
                        title=f"hand-off of {agent or '(agent not recorded)'} for {sid}",
                        loud=actor is None, **_no_run()))
    return out


# ================================================================================================
# dataset designs: approvals and builds (the design's sealed, anchored log)
# ================================================================================================
def design_items(viewer: Viewer, src: ActivitySources, want: set[str], problems: Problems,
                 incomplete: set[str]) -> list[dict]:
    out: list[dict] = []
    root = src.materialise_root
    designs = _listed(Path(root) / "designs" if root else None, "dsg-*", "design_approval",
                      incomplete, dirs=True)
    if not designs:
        return out
    try:
        from databridge_materialise.delegated import DesignStore           # noqa: PLC0415
        store = DesignStore(Path(root))
    except Exception as exc:                                              # noqa: BLE001
        problems.add({"source": "designs", "file": None,
                      "error": f"design reader unavailable: {type(exc).__name__}"})
        incomplete.update({"design_approval", "design_build"})
        return out
    budget = _Budget()
    for d in designs:
        log = d / "events.jsonl"
        if not log.is_file():
            continue
        try:
            events, broken = store.verified_events(d.name)    # public; verifies everything
        except Exception as exc:                                          # noqa: BLE001
            # the deployment approval key is unavailable: nothing can be verified, so nothing
            # is shown (said once, naming no design)
            problems.add({"source": "designs", "file": None,
                          "error": f"designs cannot be verified: {type(exc).__name__}"})
            incomplete.update({"design_approval", "design_build"})
            return out
        owner = events[0].get("owner") if events else None
        if not may_see(viewer, owner if isinstance(owner, str) else None):
            continue
        if not _admit(log, "design_approval", budget, problems, incomplete, visible=True):
            continue
        if broken:
            out.append(integrity_row("dataset design log", d.name,
                                     events[-1].get("at") if events else None,
                                     family="design_approval", design_id=d.name))
            continue
        for e in events:
            action = e.get("action")
            if action == "approved" and "design_approval" in want:
                out.append(_base(
                    "design_approval", f"{d.name}/{e.get('seq')}", e.get("at"),
                    status="approved", decision="approved", design_id=d.name,
                    rev=_scalar(e.get("rev")), design_digest=_scalar(e.get("digest")),
                    actor=actor_from_record(e) or make_actor(None, None), integrity=SEALED,
                    title=f"dataset design {d.name} approved (revision {_scalar(e.get('rev'))})",
                    loud=False, **_no_run()))
            elif action == "built" and "design_build" in want:
                out.append(_base(
                    "design_build", f"{d.name}/{e.get('seq')}", e.get("at"),
                    status="built", decision="built", design_id=d.name,
                    rev=_scalar(e.get("rev")), design_digest=_scalar(e.get("digest")),
                    build_id=_scalar(e.get("build")),
                    actor=actor_from_record(e, pid_key="by") or make_actor(None, None),
                    integrity=SEALED,
                    title=f"dataset design {d.name} built (revision {_scalar(e.get('rev'))})",
                    loud=False, **_no_run()))
    return out


# ================================================================================================
# the outbox: approvals, rejections, sends (read-only)
# ================================================================================================
def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _outbox_owners(db: Path) -> dict[str, str]:
    """``draft_id -> owner`` from the gateway's sidecar (`outbox_routes.DraftOwners`), read-only.
    Missing or unreadable: {} (every draft is then owner-unknown: operator-only)."""
    side = db.with_name(db.stem + ".owners.sqlite")
    if not side.is_file():
        return {}
    try:
        conn = _ro(side)
        try:
            return {r["draft_id"]: r["owner"] for r in
                    conn.execute("SELECT draft_id, owner FROM draft_owner")
                    if r["draft_id"] and r["owner"]}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def _outbox_actor(row: dict, role: str) -> Optional[dict]:
    """The decider of an outbox row: the structured actor the store records
    (``<role>_actor_json``, R-028), else the name it recorded (a legacy ``"x (dev token)"`` is
    read back as ``x`` with ``dev_only: true`` — never served decorated)."""
    raw = row.get(f"{role}_actor_json")
    if isinstance(raw, str) and raw:
        try:
            got = actor_from_record({"actor": json.loads(raw)})
        except ValueError:
            got = None
        if got is not None:
            return got
    return actor_from_record(row, pid_key=f"{role}_by", default_type=None)


def outbox_items(viewer: Viewer, src: ActivitySources, problems: Problems,
                 incomplete: set[str]) -> list[dict]:
    kind = "outbox_decision"
    out: list[dict] = []
    db = src.outbox_db
    if db is None or not Path(db).is_file():
        return out
    db = Path(db)
    owners = _outbox_owners(db)
    try:
        conn = _ro(db)
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM outbox WHERE status IN ('approved', 'rejected', 'sent') "
                "ORDER BY rowid_pk DESC LIMIT ?", (MAX_DIR_ENTRIES,))]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        incomplete.add(kind)
        problems.report(False, {"source": kind, "file": db.name,
                                "error": f"unreadable: {type(exc).__name__}"})
        return out
    if len(rows) >= MAX_DIR_ENTRIES:
        incomplete.add(kind)
    for r in rows:
        draft = _scalar(r.get("draft_id"))
        if not may_see(viewer, owners.get(draft)):
            continue
        status = r.get("status")
        if status == "approved":
            actor, at = _outbox_actor(r, "approved"), r.get("approved_at")
        elif status == "rejected":
            actor, at = _outbox_actor(r, "rejected"), r.get("rejected_at")
        else:
            actor, at = make_actor(r.get("recorded_by") or "dispatcher", "automation"), \
                r.get("sent_at")
        out.append(_base(
            kind, f"{draft}/{r.get('rowid_pk')}", at or r.get("recorded_at"),
            status=_scalar(status), decision=_scalar(status), draft_id=draft,
            playbook_id=_scalar(r.get("playbook_id")), channel=_scalar(r.get("channel")),
            recipient_origin=_scalar(r.get("recipient_origin")),
            # the rejection's reason is the decider's own sentence: withheld in place
            reason=withheld(FREE_TEXT) if r.get("reason") else None,
            actor=actor or make_actor(None, None), integrity=APPEND_ONLY,
            title=f"outbound draft {draft} {status}",
            loud=actor is None, **_no_run()))
    return out


# ================================================================================================
# one-time tasks that never recorded a run
# ================================================================================================
def one_time_task_items(viewer: Viewer, src: ActivitySources, problems: Problems,
                        incomplete: set[str]) -> list[dict]:
    """A task whose run was recorded is read with the runs (kind ``one_time_task`` too). This adds
    the task that failed (or is still starting) BEFORE any run record existed — otherwise the
    owner's failed task would simply not be in the feed."""
    kind = "one_time_task"
    out: list[dict] = []
    budget = _Budget()
    for d in _listed(src.one_time_root, "*", kind, incomplete, dirs=True):
        if not safe_name(d.name):
            continue
        path = d / "task.json"
        if not path.is_file():
            continue
        if not _admit(path, kind, budget, problems, incomplete, visible=viewer.operator,
                      cap=MAX_RECORD_BYTES):
            continue
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.report(False, {"source": kind, "file": f"{d.name}/task.json",
                                    "error": f"unreadable: {type(exc).__name__}"})
            continue
        if not isinstance(task, dict) or task.get("task_id") != d.name:
            continue
        owner = task.get("owner") if isinstance(task.get("owner"), str) else None
        if not may_see(viewer, owner) or task.get("run_id"):
            continue
        status = _scalar(task.get("status")) or "not_run"
        out.append(_base(
            kind, f"task/{d.name}", task.get("failed_at") or task.get("created_at"),
            status=status, task_id=d.name, run_kind="agent", run_owner=d.name,
            error_ref=_scalar(task.get("error_ref")),
            actor=actor_from_record(task, pid_key="owner", default_type="person")
            or make_actor(None, None),
            title=(_scalar(task.get("created_from")) or f"one-time task {d.name}")[:300],
            reason=("it failed before a run was recorded; any detail is in the server log under "
                    "the task's error reference" if status == "failed" else None),
            integrity=UNVERIFIED, loud=status in ("failed", "running"), **_no_run()))
    return out


# ================================================================================================
# the entry point `collect` calls
# ================================================================================================
def decision_items(viewer: Viewer, want: Iterable[str], problems: Problems,
                   incomplete: set[str]) -> list[dict]:
    """Every decision row of the requested kinds this viewer may see."""
    want = set(want)
    src = viewer.sources
    out: list[dict] = []
    if want & {"plan_approval", "decision_log"}:
        out += plan_approval_items(viewer, src, problems, incomplete)
    if want & {"mapping_approval", "link_decision", "decision_log"}:
        out += mapping_items(viewer, src, want | ({"mapping_approval"} if "decision_log" in want
                                                  else set()), problems, incomplete)
    if src.setup_decisions and want & {"setup_documents", "setup_handoff"}:
        out += setup_items(viewer, src, want, problems, incomplete)
    if want & {"design_approval", "design_build", "decision_log"}:
        out += design_items(viewer, src, want, problems, incomplete)
    if "outbox_decision" in want:
        out += outbox_items(viewer, src, problems, incomplete)
    if "one_time_task" in want:
        out += one_time_task_items(viewer, src, problems, incomplete)
    # a log that does not verify is shown with the family it belongs to, and on its own kind
    return [r for r in out if r["kind"] in want
            or (r["kind"] == "decision_log" and r.get("family") in want)]


__all__ = ["decision_items", "LEDGER_INTEGRITY_FAILED", "SEALED", "APPEND_ONLY",
           "UNVERIFIED", "FAILED", "keeper_owner", "may_see", "integrity_row"]
