"""One-time tasks on the Intelligence page (FEAT8, founder 2026-10-02).

    POST /intelligence/one-time/compile                    English -> the plan (or questions)
    POST /intelligence/one-time/run          {draft_id}    run the previewed plan ONCE
    GET  /intelligence/one-time/{task_id}                  the task, its answer, its writes
    POST /intelligence/one-time/{task_id}/actions/{action_id}/approve | /reject
    POST /intelligence/one-time/{task_id}/retry            re-run a FAILED task, when that is safe

A one-time task is a job described in English and run once, read-only or read-and-write, without
building an agent. **Nothing here is a parallel executor**:

* it is COMPILED by the agent compiler (`databridge_automation.compile_english(run_once=True)`):
  the same catalogue, grants, purpose, golden set, clarifying questions and honest refusals for an
  unconnected channel. ``run_once`` adds only: no trigger, every write ``human_required``, an
  ``once_…`` id;
* it is RUN by the agent runtime (`databridge_agent_runtime.run_agent`), which re-compiles the
  declaration before the first read exactly as for an agent; reads run, every write is PROPOSED;
* a held write is decided by the runtime's own `approve` / `reject`, with an `Approver` minted
  from the authenticated principal. `approve` re-checks the task's authority against the files
  kept for it (``agents_dir=<task dir>``), so a task edited after it ran is refused.

**No agent is persisted.** The compiled pair (``<id>.workflow.yaml`` + ``<id>.agent.yaml``) is
written to the task's own directory under `one_time_root()` — never ``WALNUT_AGENTS_DIR`` — only
as the record of what ran (and what an approval is re-checked against). The trigger engine, the
agent list and ``/agents/compile`` never read it. "Save as agent" is the Builder's normal path
(``POST /agents/compile`` + ``POST /agents``), from the page.

**Owner-only.** The owner is the principal who ran it (``task.json``, from the authenticated
principal, never a body). The run record lives where every agent run lives
(``WALNUT_RUNS_DIR/agent/<task_id>/``) with ``trigger = {type: one_time, by: <owner>}``, so
`/activity` lists it — to its owner and operators only, under Activity's own fail-closed rule — with
its action history and delegation tree. Another principal gets the same 404 a missing task gets.

**Never "done" while a write is held.** The task's ``state`` is ``waiting_for_approval`` while any
write is proposed (or approved but not executed); ``done`` only when every write has an outcome.

**What was previewed is what is run.** ``/compile`` keeps the `DraftResult` in memory under a
``draft_id`` (a store of its own: an agent save can never pick up a one-time draft); ``/run``
runs THAT object, byte for byte, and only for the principal who compiled it. Another principal's
draft and a missing one get the byte-identical 404. Drafts are capped PER PRINCIPAL
(:data:`MAX_DRAFTS_PER_PRINCIPAL`, evicting only that principal's oldest) and expire after
:data:`DRAFT_TTL_SECONDS`, so one caller cannot evict everyone else's reviewed plans.

**A failed run says so** (R-027 ONE, Codex round 1). ``task.json`` is ``running`` before the runtime
is called and ``failed`` if it raises, with an opaque ``error_ref`` that is also in the server log;
the caller gets a fixed message, never the exception's text. A run id the runtime had already
allocated is kept, so a write it proposed before failing can still be decided. ``/retry`` re-runs
the kept pair for the owner (or an operator) only when the task is ``failed`` AND the failed attempt
never proposed a write. **That refusal is permanent** (Codex round 2): deciding the write does not
make the task retryable, because a retry would propose it again; the user describes a new task. The
failed -> running claim is atomic (an ``O_EXCL`` lock file per task around read-check-write), so two
retries cannot both run it.

**No value without its pointers** (Codex round 2 + R-028 #1, CRITICAL). On BOTH paths — a run
record, or a write reconstructed from the action log when there is none — an action's ``args`` are
shown only with non-empty, wholly valid ``SourcePointer`` s behind them. With none (or any that does
not validate) ``args`` and ``ungrounded_values`` are the standard withheld envelope
``{status: "withheld", reason: "no_valid_pointer", pointers: []}`` (R-028 #12) — never a bare
``null`` with a flag beside it — and ``derived_from`` is ``[]``.

**Refusals are fixed text with standard codes** (R-028 #6, #11). A compile error, a run failure or
a refused decision returns a fixed message and an opaque ``error_ref``; the exception's own words
go to the server log. A refused decision's runtime reason is mapped onto the platform's codes
(:data:`DECISION_CODES`: ``APPROVER_NOT_AUTHENTICATED``, ``MACHINE_ACTOR``, ``DECISION_STALE``,
``DECISION_ALREADY_FINAL``); one with no standard equivalent stays ``ACTION_REFUSED``, and the
runtime's closed reason rides beside it as ``reason_code``.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from databridge_core import decision_ledger as _codes
from fastapi import Body, Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .agent_ownership import is_operator
from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import approver_for, close_gateway, platform_of, reload_graph

log = logging.getLogger("databridge.gateway.one_time")

#: Where one-time tasks keep their compiled pair and owner record. Default: beside the runs dir.
ONE_TIME_DIR_ENV = "WALNUT_ONE_TIME_DIR"
TASK_FILE = "task.json"
#: The run trigger type a one-time task's run record carries (Activity shows it as such).
TRIGGER_TYPE = "one_time"
#: Drafts one principal may hold at once; keeping one more evicts THAT principal's oldest only.
MAX_DRAFTS_PER_PRINCIPAL = 10
#: A previewed plan expires: it describes the catalogue as it was when it was compiled.
DRAFT_TTL_SECONDS = 60 * 60
#: The task states this module writes into ``task.json`` around a run.
RUNNING, FAILED = "running", "failed"
#: What a caller is told when a run raises. Fixed: an exception's text can name a connector, a
#: path or a credential, and it goes to the server log under ``error_ref`` instead.
RETRY_UNSAFE_MESSAGE = ("this task cannot be retried: its failed attempt proposed a write, and a "
                        "retry would propose that write again. That is permanent, whatever is "
                        "decided about the write. Decide it on this task, and describe a new task "
                        "to run the job again.")
#: A retry claim lock older than this belonged to a process that died mid-claim (the claim is a
#: read, a check and one write; it never spans a run).
RETRY_LOCK_STALE_SECONDS = 30
RUN_FAILED_MESSAGE = ("the task failed while it was running. Nothing more will happen on its own: "
                      "any write it proposed before failing is still waiting for your decision, "
                      "and the details are in the server log under the reference on the task.")
#: A task id names a directory: one segment, no separator, no leading dot.
_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
#: Action statuses that still wait on a person.
HELD = frozenset({"proposed", "approved"})
#: Why an action's values are withheld: no non-empty, wholly valid set of pointers stands behind
#: them (hard constraint 2).
NO_VALID_POINTER = "no_valid_pointer"
#: The agent runtime's closed refusal reasons -> the platform's standard refusal codes (R-028 #11).
DECISION_CODES = {
    "principal_not_authenticated": _codes.APPROVER_NOT_AUTHENTICATED,
    "principal_not_a_person": _codes.MACHINE_ACTOR,
    "authority_changed": _codes.DECISION_STALE,
    "not_claimable": _codes.DECISION_ALREADY_FINAL,
}
#: A compile that raised: fixed text; the detail is in the server log under ``error_ref``.
COMPILE_FAILED_MESSAGE = ("this task could not be compiled. Nothing ran. The details are in the "
                          "server log under the reference returned with this message.")


def one_time_root() -> Path:
    value = os.environ.get(ONE_TIME_DIR_ENV)
    if value:
        return Path(value)
    from databridge_agent_runtime.paths import runs_dir               # noqa: PLC0415
    return runs_dir().parent / "one_time"


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def withheld(reason: str) -> dict:
    """The standard withheld envelope (R-028 #12, the public form of the core ``Withheld``):
    ``{status, reason, pointers}``. A value is withheld here only because NO valid pointer stands
    behind it, so ``pointers`` is empty."""
    return {"status": "withheld", "reason": reason, "pointers": []}


def _not_found() -> JSONResponse:
    """One body for a missing task and another principal's task: nothing varies with the id."""
    return _refusal("UNKNOWN_TASK", "no one-time task with that id that you can see", 404)


def _unknown_draft() -> JSONResponse:
    """One body for a missing, expired, consumed or another principal's draft (Codex round 1 #4):
    the id is not echoed and nothing varies with whether it exists."""
    return _refusal("UNKNOWN_DRAFT", "no plan with that id that you can run; describe the task "
                                     "again to review and run it (plans are kept in memory, for a "
                                     "limited time)", 404)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class CompileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    answers: dict[str, str] = Field(default_factory=dict)


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str


class DecisionBody(BaseModel):
    #: a ``by`` in the body is ignored: who decides is the authenticated principal
    model_config = ConfigDict(extra="ignore")
    reason: str = ""


# ================================================================================================
# drafts (in memory, their own store)
# ================================================================================================
class _Drafts:
    """Previewed plans, in memory. Bounded PER PRINCIPAL and by age (Codex round 1 #5): a global
    cap let one caller's compilations evict every other principal's reviewed plans."""

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._items: "OrderedDict[str, dict]" = OrderedDict()
        self._lock = threading.Lock()
        self._clock = clock

    def _expire(self) -> None:
        cutoff = self._clock() - DRAFT_TTL_SECONDS
        for key in [k for k, v in self._items.items() if v["t"] < cutoff]:
            del self._items[key]

    def keep(self, result: Any, by: str) -> str:
        draft_id = f"once-draft-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._expire()
            self._items[draft_id] = {"result": result, "by": by, "at": _now(), "t": self._clock()}
            mine = [k for k, v in self._items.items() if v["by"] == by]
            for key in mine[:-MAX_DRAFTS_PER_PRINCIPAL]:               # oldest first, own only
                del self._items[key]
        return draft_id

    def get(self, draft_id: str) -> Optional[dict]:
        with self._lock:
            self._expire()
            return self._items.get(draft_id)

    def pop(self, draft_id: str) -> Optional[dict]:
        with self._lock:
            self._expire()
            return self._items.pop(draft_id, None)


def drafts_of(app: Any) -> _Drafts:
    d = getattr(app.state, "one_time_drafts", None)
    if not isinstance(d, _Drafts):
        d = _Drafts()
        app.state.one_time_drafts = d
    return d


# ================================================================================================
# the plan a person sees before anything runs
# ================================================================================================
def _connection_of(tool: str) -> Optional[str]:
    from databridge_workflow_spec import parse_tool                   # noqa: PLC0415
    try:
        conn = parse_tool(tool).get("connection")
    except Exception:                                                 # noqa: BLE001
        return None
    return str(conn) if conn else None


def _write_connections(spec: Any) -> set[str]:
    return {c for c in (_connection_of(a.tool) for a in spec.actions) if c}


def _grants(compilation: Any) -> Optional[dict]:
    """What the agent compiler granted, read off the compiled object (never re-derived)."""
    decision = getattr(compilation, "decision", None)
    compiled = getattr(decision, "compiled", None)
    if compiled is None:
        return None
    return {
        "purpose": compiled.purpose,
        "capabilities": [{"source_id": g.source_id, "capability": g.capability,
                          "max_trust": g.max_trust} for g in compiled.grants.grants],
        "golden_set": compiled.evals.golden_set,
        "golden_set_records": compiled.evals.records,
        "citation": compiled.citation,
        "tokens_per_run": compiled.budget.tokens_per_run,
    }


def plan_view(result: Any) -> dict:
    from databridge_workflow_spec import parse_tool                   # noqa: PLC0415

    spec = result.spec
    compilation = result.compilation
    runnable = bool(spec is not None and not result.open_questions and compilation is not None
                    and compilation.compiled)
    plan = None
    if spec is not None:
        writes_to = _write_connections(spec)
        reads = []
        for t in spec.tools:
            try:
                p = parse_tool(t)
            except Exception:                                         # noqa: BLE001
                p = {}
            reads.append({"tool": t, "family": p.get("family"), "connection": p.get("connection")})
        plan = {
            "task_id": spec.agent, "title": spec.title, "mode": spec.mode,
            "trigger": spec.trigger.type,
            "sources_read": [s for s in spec.sources if s not in writes_to],
            "reads": reads,
            "writes": [{"name": a.name, "tool": a.tool,
                        "connection": _connection_of(a.tool),
                        "automation_mode": a.automation_mode, "description": a.description,
                        "reversal": a.reversal} for a in spec.actions],
            "instructions": spec.instructions,
            "grants": _grants(compilation),
        }
    return {
        "runnable": runnable, "created_from": result.created_from,
        "understood": result.understood,
        "open_questions": [q.as_record() for q in result.open_questions],
        "assumptions": list(result.assumptions), "notes": list(result.notes),
        "compilation": compilation.as_record() if compilation is not None else None,
        "model_used": result.model_used, "plan": plan,
        "derivations": list(result.derivations),
    }


# ================================================================================================
# the task on disk + its view
# ================================================================================================
def task_dir(task_id: str) -> Optional[Path]:
    if not _ID_RE.match(task_id or "") or ".." in task_id:
        return None
    root = one_time_root()
    d = root / task_id
    try:
        if d.resolve().parent != root.resolve():
            return None
    except OSError:
        return None
    return d


def read_task(task_id: str) -> Optional[dict]:
    d = task_dir(task_id)
    if d is None or not (d / TASK_FILE).is_file():
        return None
    try:
        data = json.loads((d / TASK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.error("one-time task record %s is unreadable", d / TASK_FILE)
        return None
    return data if isinstance(data, dict) and data.get("task_id") == task_id else None


def _write_task(d: Path, data: dict) -> None:
    tmp = d / f".{TASK_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"     # unique per write
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, d / TASK_FILE)


def may_see(principal: Any, task: Optional[dict]) -> bool:
    """Owner-only (and operators, the same fail-closed rule `agent_ownership` applies)."""
    if task is None:
        return False
    owner = task.get("owner")
    return is_operator(principal) or (isinstance(owner, str) and owner
                                      and owner == principal.principal_id)


def task_state(run_status: Optional[str], statuses: list[str]) -> str:
    """``waiting_for_approval`` while ANY write still waits on a person — never ``done``."""
    if any(s in HELD for s in statuses):
        return "waiting_for_approval"
    if run_status in ("completed", "stopped"):
        return "done"
    return run_status or "unknown"


def task_view(task: dict) -> dict:
    from databridge_agent_runtime import ActionLog, read_run, run_path  # noqa: PLC0415
    from databridge_agent_runtime.runs import action_id_for, actions_path  # noqa: PLC0415

    out: dict = {"task_id": task["task_id"], "owner": task.get("owner"),
                 "created_from": task.get("created_from"), "created_at": task.get("created_at"),
                 "run_id": task.get("run_id"), "kind": "one_time_task",
                 "error_ref": task.get("error_ref"), "attempts": task.get("attempts", 1)}
    status = task.get("status")
    run_id = task.get("run_id")
    path = run_path(run_id, agent=task["task_id"]) if run_id else None
    if path is None or not path.is_file():
        # No run record: never run, running now, or failed before (or without) writing one. A
        # proposal the failed attempt logged is still listed, so it can still be decided.
        actions = _logged_actions(path) if path is not None else []
        held = sum(1 for a in actions if a["status"] in HELD)
        out.update(state=status if status in (RUNNING, FAILED) else "not_run", run=None,
                   actions=actions, held=held,
                   executed=sum(1 for a in actions if a["status"] == "executed"))
        return out
    record = read_run(path)
    log_ = ActionLog(actions_path(path))
    actions = []
    for i, a in enumerate(record.actions):
        aid = action_id_for(i)
        prop = log_.proposal(aid) or {}
        # R-028 #1: the record path validates exactly as the log path does (all or nothing).
        pointers = _logged_pointers([p.model_dump(mode="json") if hasattr(p, "model_dump")
                                     else p for p in a.pointers])
        actions.append({"action_id": aid, "name": a.name, "status": a.status, "by": a.by,
                        "automation_mode": a.automation_mode, "tool": prop.get("tool"),
                        "reversal": prop.get("reversal"),
                        **_grounded_values(prop, pointers),
                        "history": log_.events(aid)})
    statuses = [a["status"] for a in actions]
    held = sum(1 for s in statuses if s in HELD)
    rec = record.to_dict()
    # A failed task says FAILED even when its run proposed writes first; `held` still counts them.
    state = FAILED if status == FAILED else task_state(record.status, statuses)
    out.update(state=state, held=held,
               executed=sum(1 for s in statuses if s == "executed"),
               run={"status": record.status, "started": rec.get("started"),
                    "ended": rec.get("ended"), "trigger": rec.get("trigger"),
                    "answer": rec.get("answer"), "outcome": rec.get("outcome"),
                    "coverage": rec.get("coverage"), "models_used": rec.get("models_used"),
                    "steps": len(rec.get("steps") or [])},
               actions=actions)
    return out


def _logged_actions(record_path: Path) -> list[dict]:
    """Actions a run PROPOSED before it failed without writing its record, from its action log."""
    from databridge_agent_runtime import ActionLog                     # noqa: PLC0415
    from databridge_agent_runtime.runs import actions_path            # noqa: PLC0415

    log_ = ActionLog(actions_path(record_path))
    out, seen = [], []
    for ev in log_.events():
        aid = ev.get("action_id")
        if aid and aid not in seen:
            seen.append(aid)
    for aid in seen:
        events = log_.events(aid)
        last = events[-1].get("event") if events else None
        status = {"proposed": "proposed", "approved": "approved", "executed": "executed",
                  "rejected": "rejected"}.get(last or "", last or "unknown")
        prop = log_.proposal(aid) or {}
        out.append({"action_id": aid, "name": prop.get("action") or prop.get("name"),
                    "status": status, "by": events[-1].get("by") if events else None,
                    "automation_mode": prop.get("automation_mode"), "tool": prop.get("tool"),
                    "reversal": prop.get("reversal"),
                    **_grounded_values(prop, _logged_pointers(prop.get("derived_from"))),
                    "history": events})
    return out


def _grounded_values(prop: dict, pointers: list[dict]) -> dict:
    """An action's values, shown only with the pointers behind them (hard constraint 2). With no
    valid pointers, ``args`` and ``ungrounded_values`` are the standard withheld envelope."""
    if not pointers:
        return {"args": withheld(NO_VALID_POINTER), "derived_from": [],
                "ungrounded_values": withheld(NO_VALID_POINTER)}
    return {"args": prop.get("args"), "derived_from": pointers,
            "ungrounded_values": prop.get("ungrounded_values") or []}


def _logged_pointers(raw: Any) -> list[dict]:
    """The pointers an action log recorded for a proposal, each validated as a ``SourcePointer``.
    ``[]`` when there are none or ANY of them is not a valid pointer (all or nothing)."""
    from databridge_core import SourcePointer                          # noqa: PLC0415

    if not isinstance(raw, list) or not raw:
        return []
    out = []
    for p in raw:
        try:
            out.append(SourcePointer.model_validate(p).model_dump(mode="json"))
        except Exception:                                             # noqa: BLE001
            return []
    return out


def _allocated_run_id(task_id: str, before: set) -> Optional[str]:
    """The run id the runtime allocated during a call that then raised, if it left any trace (a
    run record or an action log) that was not there before the call."""
    from databridge_agent_runtime.paths import runs_dir                # noqa: PLC0415
    from databridge_agent_runtime.runs import RUN_KIND_AGENT          # noqa: PLC0415

    found = _run_traces(task_id, runs_dir() / RUN_KIND_AGENT / task_id) - before
    return sorted(found)[-1] if found else None


def _run_traces(task_id: str, base: Optional[Path] = None) -> set:
    if base is None:
        from databridge_agent_runtime.paths import runs_dir            # noqa: PLC0415
        from databridge_agent_runtime.runs import RUN_KIND_AGENT      # noqa: PLC0415
        base = runs_dir() / RUN_KIND_AGENT / task_id
    if not base.is_dir():
        return set()
    ids = set()
    for f in base.iterdir():
        name = f.name
        for suffix in (".actions.jsonl", ".json"):
            if name.endswith(suffix):
                ids.add(name[: -len(suffix)])
                break
    return ids


def _has_proposals(task: dict) -> bool:
    run_id = task.get("run_id")
    if not run_id:
        return False
    from databridge_agent_runtime import ActionLog, run_path          # noqa: PLC0415
    from databridge_agent_runtime.runs import actions_path            # noqa: PLC0415
    return bool(ActionLog(actions_path(run_path(run_id, agent=task["task_id"]))).events())


def _claim_retry(task_id: str, principal: Any):
    """Atomically move a failed task to ``running``: ``(task, None)`` or ``(None, refusal)``.

    The read, the checks and the ``running`` write happen under an ``O_EXCL`` lock file in the
    task's directory, so a second retry either sees ``running`` or finds the claim in progress.
    """
    d = task_dir(task_id)
    if d is None or read_task(task_id) is None:
        return None, _not_found()
    lock = d / ".retry.lock"
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > RETRY_LOCK_STALE_SECONDS:
            lock.unlink()                                  # a claim whose process died mid-claim
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None, _refusal("RETRY_IN_PROGRESS", "this task is already being retried", 409)
    try:
        task = read_task(task_id)                          # re-read UNDER the lock
        if not may_see(principal, task):
            return None, _not_found()
        if task.get("status") != FAILED:
            return None, _refusal("NOT_RETRYABLE", "only a task whose run failed can be retried",
                                  409)
        if _has_proposals(task):
            return None, _refusal("RETRY_UNSAFE", RETRY_UNSAFE_MESSAGE, 409)
        task["attempts"] = int(task.get("attempts") or 1) + 1
        task.setdefault("failed_attempts", []).append(
            {"run_id": task.get("run_id"), "error_ref": task.get("error_ref"),
             "failed_at": task.get("failed_at")})
        task.update(status=RUNNING, run_id=None, error_ref=None)
        _write_task(d, task)                               # the claim: failed -> running
        return task, None
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def _execute(app: Any, d: Path, task: dict, principal: Any):
    """Run the task's kept pair once. ``running`` before the runtime is called; ``failed`` (with
    an opaque ``error_ref``, and any run id the runtime had allocated) if it raises."""
    import databridge_agent_runtime as ar                              # noqa: PLC0415

    from .trace_recorder import recorder_for                           # noqa: PLC0415

    task_id = task["task_id"]
    task.update(status=RUNNING, run_id=None, error_ref=None)
    _write_task(d, task)
    before = _run_traces(task_id)
    rt = platform_of(app)
    gw = None
    try:
        spec, decl = ar.load_agent(task_id, d)           # exactly what `approve` re-checks
        gw = rt.new_gateway()
        trace = recorder_for(app).begin(surface="one_time", agent=task_id,
                                        owner=principal.principal_id)
        federation = None
        if getattr(spec, "delegates", None):
            from .federated_routes import _factory as _federated_factory  # noqa: PLC0415
            federation = _federated_factory(app)()
        trigger = {"type": TRIGGER_TYPE, "by": principal.principal_id, "question": None,
                   "task_id": task_id}
        record = ar.run_agent(spec, decl, store=getattr(app.state, "graph_store", None),
                              harness=rt.harness, gateway=gw, retriever=rt.new_retriever(),
                              trigger=trigger, trace=trace, federation=federation)
    except Exception:                                                 # noqa: BLE001
        ref = f"once-err-{uuid.uuid4().hex[:12]}"
        # The detail goes to the server log, under a reference the task and the caller both carry.
        log.exception("one-time task %s failed [%s]", task_id, ref)
        task.update(status=FAILED, error_ref=ref, failed_at=_now(),
                    run_id=_allocated_run_id(task_id, before))
        _write_task(d, task)
        return JSONResponse(status_code=500, content={
            "refusal": {"code": "RUN_FAILED", "message": RUN_FAILED_MESSAGE,
                        "nearest_alternative": None},
            "task_id": task_id, "error_ref": ref})
    finally:
        if gw is not None:
            close_gateway(gw)
        reload_graph(app)
    task.update(status=record.status, run_id=record.run_id)
    _write_task(d, task)
    log.info("one-time task %s run by %s: %s", task_id, principal.principal_id, record.status)
    return task_view(task)


# ================================================================================================
# routes
# ================================================================================================
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def _decision_refused(exc: Exception, decision: str, task_id: str, action_id: str) -> JSONResponse:
    """A refused approve/reject: fixed public text, the runtime's CLOSED reason code (only when it
    is shaped like one), and the exception's own words in the server log under ``error_ref``."""
    ref = f"once-err-{uuid.uuid4().hex[:12]}"
    code = getattr(exc, "code", None)
    reason = code if isinstance(code, str) and _REASON_CODE.match(code) else "refused"
    log.info("%s of %s/%s refused [%s] %s: %s", decision, task_id, action_id, ref, reason, exc)
    return JSONResponse(status_code=409, content={
        "refusal": {"code": DECISION_CODES.get(reason, "ACTION_REFUSED"),
                    "message": f"this action cannot be {decision}d now ({reason}). The details "
                               f"are in the server log under {ref}.",
                    "nearest_alternative": None},
        "reason_code": reason, "error_ref": ref})


def register_one_time_routes(app: FastAPI) -> None:
    install_auth_handler(app)

    @app.post("/intelligence/one-time/compile")
    def one_time_compile(body: CompileBody = Body(...),
                         principal: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_automation import compile_english              # noqa: PLC0415
        from databridge_workflow_spec import WorkflowSpec              # noqa: PLC0415

        from .agents_routes import current_catalogue                   # noqa: PLC0415

        if not body.text.strip():
            return _refusal("EMPTY_TEXT", "say what the task should do")
        rt = platform_of(app)
        try:
            result = compile_english(body.text, current_catalogue(), harness=rt.harness,
                                     answers=dict(body.answers) or None, run_once=True)
        except ValueError:
            # R-028 #6: never the exception's text (it can name a path, a connector or a query).
            ref = f"once-err-{uuid.uuid4().hex[:12]}"
            log.exception("one-time compile refused [%s]", ref)
            return JSONResponse(status_code=422, content={
                "refusal": {"code": "CANNOT_COMPILE", "message": COMPILE_FAILED_MESSAGE,
                            "nearest_alternative": None},
                "error_ref": ref})
        if result.kind != "agent" or (result.spec is not None
                                      and not isinstance(result.spec, WorkflowSpec)):
            return _refusal("NOT_A_ONE_TIME_TASK",
                            "this describes an adapter agent (keeping sources in the graph), "
                            "not a job to run once; build it in the Builder")
        draft_id = drafts_of(app).keep(result, principal.principal_id)
        return {"draft_id": draft_id, **plan_view(result)}

    @app.post("/intelligence/one-time/run")
    def one_time_run(body: RunBody = Body(...),
                     principal: AuthenticatedPrincipal = Depends(require_principal)):
        store = drafts_of(app)
        held = store.get(body.draft_id)
        if held is None or held.get("by") != principal.principal_id:
            return _unknown_draft()                       # identical: missing or someone else's
        result = held["result"]
        view = plan_view(result)
        if not view["runnable"]:
            why = [q.text for q in result.open_questions]
            if result.compilation is not None and not result.compilation.compiled:
                why += [r.get("detail", "") for r in result.compilation.refusals] or \
                       [result.compilation.reason]
            return _refusal("NOT_RUNNABLE", "this plan cannot run: "
                            + ("; ".join(w for w in why if w) or "it has no compiled plan"), 409)
        task_id = result.spec.agent
        d = task_dir(task_id)
        if d is None:
            return _refusal("BAD_TASK_ID", f"{task_id!r} cannot name a task", 422)
        try:
            d.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return _refusal("TASK_EXISTS", f"task {task_id!r} has already run", 409)
        store.pop(body.draft_id)                         # consumed: one draft, one run
        # The record of what ran — the previewed pair, byte for byte — and who owns it.
        (d / f"{task_id}.workflow.yaml").write_text(result.spec_yaml, encoding="utf-8")
        (d / f"{task_id}.agent.yaml").write_text(result.declaration_yaml, encoding="utf-8")
        task = {"task_id": task_id, "kind": "one_time_task", "owner": principal.principal_id,
                "created_from": result.created_from, "created_at": _now(), "run_id": None,
                "attempts": 1, "recorded_by": "POST /intelligence/one-time/run"}
        return _execute(app, d, task, principal)

    @app.post("/intelligence/one-time/{task_id}/retry")
    def one_time_retry(task_id: str,
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Re-run a FAILED task's kept pair, once. Owner or operator. Permanently refused once an
        attempt proposed a write (``RETRY_UNSAFE_MESSAGE``)."""
        if not may_see(principal, read_task(task_id)):
            return _not_found()
        task, refusal = _claim_retry(task_id, principal)
        if refusal is not None:
            return refusal
        return _execute(app, task_dir(task_id), task, principal)

    @app.get("/intelligence/one-time/{task_id}")
    def one_time_get(task_id: str,
                     principal: AuthenticatedPrincipal = Depends(require_principal)):
        task = read_task(task_id)
        if not may_see(principal, task):
            return _not_found()
        return task_view(task)

    def _decide(decision: str, task_id: str, action_id: str, body: Optional[DecisionBody],
                principal: AuthenticatedPrincipal):
        import databridge_agent_runtime as ar                          # noqa: PLC0415

        task = read_task(task_id)
        if not may_see(principal, task) or not task.get("run_id"):
            return _not_found()
        approver = approver_for(principal)               # the principal, never the body
        rt = platform_of(app)
        gw = None
        try:
            if decision == "approve":
                gw = rt.new_gateway()
                ar.approve(task["run_id"], action_id, approver, gateway=gw,
                           agents_dir=task_dir(task_id))
            else:
                ar.reject(task["run_id"], action_id, approver,
                          reason=(body.reason if body else ""))
        except (FileNotFoundError, KeyError, IndexError):
            # Fixed text: a FileNotFoundError's str() is a server path (Codex round 1, #3 sibling).
            log.info("unknown action %r on one-time task %s", action_id, task_id)
            return _refusal("UNKNOWN_ACTION", "no action with that id on this task", 404)
        except ValueError as exc:                        # ActionRefused is a ValueError
            return _decision_refused(exc, decision, task_id, action_id)
        finally:
            if gw is not None:
                close_gateway(gw)
                reload_graph(app)
        return {"decided_by": principal.principal_id, **task_view(task)}

    @app.post("/intelligence/one-time/{task_id}/actions/{action_id}/approve")
    def one_time_approve(task_id: str, action_id: str, body: Optional[DecisionBody] = Body(None),
                         principal: AuthenticatedPrincipal = Depends(require_principal)):
        return _decide("approve", task_id, action_id, body, principal)

    @app.post("/intelligence/one-time/{task_id}/actions/{action_id}/reject")
    def one_time_reject(task_id: str, action_id: str, body: Optional[DecisionBody] = Body(None),
                        principal: AuthenticatedPrincipal = Depends(require_principal)):
        return _decide("reject", task_id, action_id, body, principal)


__all__ = ["register_one_time_routes", "one_time_root", "plan_view", "task_view", "task_state",
           "ONE_TIME_DIR_ENV", "TRIGGER_TYPE", "MAX_DRAFTS_PER_PRINCIPAL", "DRAFT_TTL_SECONDS",
           "RUN_FAILED_MESSAGE", "DECISION_CODES", "NO_VALID_POINTER", "withheld"]
