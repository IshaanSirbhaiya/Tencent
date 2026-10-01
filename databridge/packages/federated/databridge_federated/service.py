"""`FederatedService` — the main agent sees, delegates, and understands.

    question ─ plan (rules first, else harness task_class "agent") ─ validate against catalogue
             ─ SubTasks dispatched IN PARALLEL (bounded pool, per-sub-task timeout)
             ─ each delegate answers over its own sources, under the CALLER's admission
             ─ deterministic combine (no model arithmetic) ─ table of GroundedValues
             ─ the model NARRATES the table (or rules do) ─ answer contract (P15) checks it
             ─ run record (§5.7) with produced_by on every step (§5.8)

It also implements the agent runtime's `Federation` protocol, so a workflow agent's
``delegate.ask:<adapter agent>`` tool reaches the same delegates under the agent's own grants.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from databridge_agent_runtime import FinalAnswer, PointerRegistry, check_answer
from databridge_agent_runtime.answer import Claim, Clarification, render_markdown
from databridge_agent_runtime.runs import (
    AnswerBlock,
    Coverage,
    RunRecord,
    Step,
    iso,
    run_path,
    utcnow,
    write_run,
)

from .catalogue import Catalogue
from .combine import CombinedTable, CombineError, combine, table_from_result
from .contracts import (
    DelegateCoverage,
    DelegationPlan,
    SkippedRead,
    SubResult,
    SubTask,
    resolve_symbols,
)
from .delegates import DelegateError, run_documents, run_live, run_tabular, to_decimal
from .planner import (
    PlanOutcome,
    PlanQuestion,
    choose_connection,
    compare_window,
    plan_question,
    question_id,
    remember_questions,
    validate_plan,
)

RUN_KIND = "federated"
NARRATE_TASK_CLASS = "agent"
NARRATOR_PROMPT_VERSION = "federated.narrator/1"
MAX_PARALLEL = 8
SUBTASK_TIMEOUT_S = 30.0
#: Every claim about a candidate row opens with this, whoever narrated it (R-021 r2 #3).
CANDIDATE_PREFIX = "Candidate — requires human selection: "
#: A string cell shorter than this is not treated as identifying a candidate row in claim text.
_MIN_IDENT = 3


# ================================================================================================
# admission for callers that are not a workflow agent (the gateway's /federated/ask)
# ================================================================================================
class CatalogueAdmission:
    """The signed-in principal may read the given connections (default: every connection of every
    registered, non-paused adapter agent — the same data /brain already shows them). Live reads
    are refused unless ``allow_live``: freshness through a live source is a narrower grant."""

    def __init__(self, principal: str, connections: Optional[set[str]] = None, *,
                 allow_live: bool = False, purpose: Optional[str] = None) -> None:
        self.principal = principal
        self.connections = connections
        self.allow_live = allow_live
        self.purpose = purpose

    def admit_read(self, connection: str, channel: str) -> tuple[bool, str]:
        if self.connections is not None and connection not in self.connections:
            return False, f"{self.principal} is not granted connection {connection!r}"
        if channel == "live" and not self.allow_live:
            return False, "a live read needs an explicit live grant; the stored copy was used"
        return True, f"granted to {self.principal}"


# ================================================================================================
# the answer
# ================================================================================================
class FederatedAnswer(BaseModel):
    run_id: str
    question: str
    answer_markdown: str
    table: dict = Field(default_factory=dict)
    citations: list[dict] = Field(default_factory=list)
    unsupported_claims: list[dict] = Field(default_factory=list)
    #: every claim, machine-readable: ``{text, citations, supported, reason, subject_candidate,
    #: requires_human_selection}``. ``requires_human_selection`` is decided by this service from
    #: the table (not by the narrator's wording), so a reader can gate on it (R-021 r2 #3).
    claims: list[dict] = Field(default_factory=list)
    #: True when ANY claim or table row concerns a candidate identity.
    requires_human_selection: bool = False
    coverage: dict = Field(default_factory=dict)
    plan: Optional[dict] = None
    questions: list[str] = Field(default_factory=list)
    #: the same questions with their ids (``fed:<sha1>``, E2E N-03): answer one by posting
    #: ``answers: {id: text}`` with the same question
    question_items: list[dict] = Field(default_factory=list)
    #: the answers this run was planned with, ``{id: text}``
    answers: dict[str, str] = Field(default_factory=dict)
    timings: dict = Field(default_factory=dict)
    produced_by: dict = Field(default_factory=dict)
    status: str = "completed"


def _md_line(text: str) -> str:
    """Engine text (it may quote stored values) as ONE markdown line: newlines folded, markdown
    punctuation escaped — the same rule the answer renderer applies to untrusted text."""
    return re.sub(r"([\\*\[\]`])", r"\\\1", " / ".join(str(text).splitlines()))


def _fmt(v: Any) -> str:
    if isinstance(v, Decimal):
        return format(v.normalize() if v == v.to_integral_value() else v, "f")
    return str(v)


@dataclass
class FederatedService:
    """Everything a delegate may read is injected; nothing here opens a source by itself."""

    catalogue: Catalogue
    facts: Any = None
    store: Any = None
    retriever: Any = None
    gateway_factory: Optional[Callable[[], Any]] = None
    harness: Any = None
    runs_dir: Optional[Path] = None
    capture_root: Optional[Path] = None
    max_parallel: int = MAX_PARALLEL
    subtask_timeout_s: float = SUBTASK_TIMEOUT_S
    clock: Callable[[], dt.datetime] = field(default=lambda: dt.datetime.now(dt.timezone.utc))

    # ---- the runtime's Federation protocol -------------------------------------------------
    def available(self, adapter_agent: str) -> bool:
        return self.catalogue.agent(adapter_agent) is not None

    def ask_delegate(self, adapter_agent: str, subtask: dict, *, admission: Any,
                     run_id: Optional[str] = None) -> SubResult:
        data = dict(subtask)
        data["adapter_agent"] = adapter_agent
        try:
            task = SubTask.model_validate(data)
        except Exception as e:                               # noqa: BLE001 - plain words
            return SubResult(subtask_id=str(data.get("id") or "t1"), adapter_agent=adapter_agent,
                             error=f"the sub-task is not well formed: {e}")
        if self.catalogue.refreshing:
            return SubResult(subtask_id=task.id, adapter_agent=adapter_agent,
                             error=self.refreshing_note())
        problems = validate_plan(DelegationPlan(subtasks=(task,)), self.catalogue)
        if problems:
            return SubResult(subtask_id=task.id, adapter_agent=adapter_agent,
                             error="; ".join(problems))
        return self.run_subtask(task, admission=admission, run_id=run_id)

    def refreshing_note(self) -> str:
        """E2E4 Q-01: why nothing is planned while a facts store cannot be read."""
        named = "; ".join(f"{c} ({why})" for c, why in sorted(self.catalogue.refreshing.items()))
        return (f"the catalogue is being refreshed: the facts store of {named} exists but could "
                f"not be read just now (a sync may be republishing it). Its tables are unknown, "
                f"not absent, so nothing was planned against a partial catalogue; ask again in "
                f"a moment")

    # ---- one delegate ------------------------------------------------------------------------
    def admits(self, admission: Any) -> Callable[[str], bool]:
        """The caller's read scope over graph provenance (nodes, edges, attribute sources): a
        source id is visible only when it is a connection in the MEASURED catalogue AND the
        caller's admission admits reading it as documents. Anything else is invisible (fail
        closed), including evidence from a connection nobody registered."""
        known = {c.connection for a in self.catalogue.agents.values() for c in a.connections}

        def _admits(source_id: str) -> bool:
            if source_id not in known:
                return False
            try:
                return bool(admission.admit_read(source_id, "documents")[0])
            except Exception:                                # noqa: BLE001 - fail closed
                return False
        return _admits

    def run_subtask(self, task: SubTask, *, admission: Any, run_id: Optional[str] = None,
                    cancel: Optional[threading.Event] = None,
                    resolve_names: bool = False) -> SubResult:
        """Run one sub-task under ``admission``. Never raises: failures are the result. A set
        ``cancel`` stops the delegate before its next read (its deadline passed).
        ``resolve_names`` lets an empty facts read retry a text filter as the ONE stored value
        holding all its words (E2E3 P-01; never for a sub-task linked through a person kind)."""
        t0 = time.perf_counter()
        entry, q = choose_connection(task, self.catalogue)
        if entry is None:
            return SubResult(subtask_id=task.id, adapter_agent=task.adapter_agent, error=q)
        channel = "live" if task.freshness == "live" else (
            "facts" if entry.channel == "tabular" else "documents")
        ok, detail = admission.admit_read(entry.connection, channel)
        if not ok:
            return SubResult(
                subtask_id=task.id, adapter_agent=task.adapter_agent,
                connection=entry.connection, method="refused",
                error=f"not admitted: {detail}",
                coverage=DelegateCoverage(skipped=(SkippedRead(
                    adapter_agent=task.adapter_agent, connection=entry.connection,
                    channel=channel, reason=detail),)),
                produced_by={"agent": admission.principal, "adapter_agent": task.adapter_agent,
                             "model": None, "by": "rules", "method": "refused"},
                timings={"ms": int((time.perf_counter() - t0) * 1000)})
        today = self.clock().date()
        try:
            if channel == "live":
                gw = self.gateway_factory() if self.gateway_factory else None
                try:
                    return run_live(task, entry, gateway=gw, today=today,
                                    principal=admission.principal, run_id=run_id,
                                    capture_root=self.capture_root, cancel=cancel)
                finally:
                    close = getattr(gw, "close", None)
                    if callable(close):
                        close()
            if channel == "facts":
                return run_tabular(task, entry, facts=self.facts, today=today,
                                   principal=admission.principal, run_id=run_id, cancel=cancel,
                                   resolve_names=resolve_names)
            return run_documents(task, entry, store=self.store, retriever=self.retriever,
                                 catalogue=self.catalogue, today=today,
                                 principal=admission.principal, run_id=run_id,
                                 admits=self.admits(admission), cancel=cancel)
        except (DelegateError, Exception) as e:             # noqa: BLE001 - reported as coverage
            why = str(e) if isinstance(e, DelegateError) else f"{type(e).__name__}: {e}"
            return SubResult(subtask_id=task.id, adapter_agent=task.adapter_agent,
                             connection=entry.connection, error=why,
                             coverage=DelegateCoverage(skipped=(SkippedRead(
                                 adapter_agent=task.adapter_agent, connection=entry.connection,
                                 channel=channel, reason=why),)),
                             timings={"ms": int((time.perf_counter() - t0) * 1000)})

    # ---- parallel dispatch -----------------------------------------------------------------
    def dispatch(self, tasks: list[SubTask], *, admission: Any,
                 run_id: Optional[str] = None,
                 resolve_names: frozenset[str] = frozenset()) -> dict[str, SubResult]:
        """Every sub-task at once (bounded pool). Each sub-task's deadline runs from when IT
        started (a queued task is not charged for its wait); a task that never started is
        cancelled once every wave could have run. At its deadline a task's ``cancel`` event is
        set, so its delegate stops before any further read or capture, and whatever it returns
        late is DISCARDED: the timeout, not a late partial result, is what the answer reports."""
        out: dict[str, SubResult] = {}
        if not tasks:
            return out
        width = max(1, min(self.max_parallel, len(tasks)))
        pool = cf.ThreadPoolExecutor(max_workers=width, thread_name_prefix="fed-delegate")
        limit = self.subtask_timeout_s
        cancels = {t.id: threading.Event() for t in tasks}
        begun: dict[str, float] = {}

        def run(t: SubTask) -> SubResult:
            begun[t.id] = time.monotonic()
            return self.run_subtask(t, admission=admission, run_id=run_id, cancel=cancels[t.id],
                                    resolve_names=t.id in resolve_names)

        def timed_out(t: SubTask, why: str) -> SubResult:
            cancels[t.id].set()
            return SubResult(subtask_id=t.id, adapter_agent=t.adapter_agent, error=why,
                             coverage=DelegateCoverage(skipped=(SkippedRead(
                                 adapter_agent=t.adapter_agent, channel="delegate",
                                 reason=why),)))
        try:
            cap = time.monotonic() + limit * math.ceil(len(tasks) / width)
            pending = {pool.submit(run, t): t for t in tasks}
            while pending:
                now = time.monotonic()
                for fut, t in list(pending.items()):
                    if fut.done():
                        del pending[fut]
                        exc = fut.exception()
                        out[t.id] = fut.result() if exc is None else SubResult(
                            subtask_id=t.id, adapter_agent=t.adapter_agent,
                            error=f"{type(exc).__name__}: {exc}")
                        continue
                    st = begun.get(t.id)
                    if st is not None and now >= st + limit:
                        del pending[fut]
                        out[t.id] = timed_out(t, f"timed out after {limit:g}s; the delegate was "
                                                 f"cancelled and any late result is discarded")
                    elif st is None and now >= cap:
                        del pending[fut]
                        fut.cancel()
                        out[t.id] = timed_out(t, "not started before the dispatch deadline; "
                                                 "cancelled before reading anything")
                if not pending:
                    break
                ends = [begun[t.id] + limit for t in pending.values() if t.id in begun] + [cap]
                cf.wait(list(pending), timeout=max(0.0, min(0.25, min(ends) - now)),
                        return_when=cf.FIRST_COMPLETED)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return {t.id: out[t.id] for t in tasks}

    # ---- the whole question ------------------------------------------------------------------
    def answer(self, question: str, *, admission: Any, plan: Optional[DelegationPlan] = None,
               trace: Any = None, answers: Optional[dict[str, str]] = None) -> FederatedAnswer:
        """``answers`` maps a question id from an earlier answer (``fed:<sha1>``) to the user's
        reply; the planner is shown each with its question (E2E N-03)."""
        t_start = time.perf_counter()
        started = utcnow()
        run_id = f"fed-{started.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        timings: dict[str, Any] = {}
        steps: list[Step] = []
        models: list[str] = []
        usage = {"tokens": 0, "cost_usd": 0.0}
        answers = {str(k): str(v).strip() for k, v in (answers or {}).items()
                   if str(v).strip()}

        if self.catalogue.refreshing:
            # E2E4 Q-01: a plan over a catalogue missing a store's tables would tell the asker
            # those tables do not exist. Nothing is planned or read; the refresh is said instead.
            note = self.refreshing_note()
            steps.append(Step(tool="federated.plan", args={"question": question},
                              policy_decision={"decision": "refuse",
                                               "reason_code": "catalogue_refreshing",
                                               "detail": note},
                              produced_by={"agent": "federated.planner", "model": None,
                                           "by": "rules", "task_class": None, "run_id": run_id,
                                           "at": iso(utcnow())}))
            md = "\n".join([f"**Question (verbatim):** {question}", "",
                            f"**Not answered yet: {note[0].upper()}{note[1:]}.**", "",
                            "_Nothing was read._"])
            return self._finish(run_id, started, question, md, steps, models, status="stopped",
                                coverage={"searched": [], "skipped": [{
                                    "tool": "federated.plan", "reason_code": "catalogue_refreshing",
                                    "reason": note}], "reasons": [note]},
                                plan=None, questions=[], timings=timings, t_start=t_start,
                                admission=admission, trace=trace, usage=usage, answers=answers)

        t = time.perf_counter()
        outcome: PlanOutcome = plan_question(question, self.catalogue, harness=self.harness,
                                             plan=plan, answers=answers)
        timings["plan_ms"] = int((time.perf_counter() - t) * 1000)
        usage["tokens"] += outcome.tokens
        usage["cost_usd"] += outcome.cost_usd
        if outcome.produced_by.get("model"):
            models.append(outcome.produced_by["model"])
        remember_questions(outcome.questions)
        steps.append(Step(tool="federated.plan", args={"question": question,
                                                       **({"answers": answers} if answers
                                                          else {})},
                          policy_decision={"decision": "permit", "reason_code": outcome.method,
                                           "detail": "; ".join(outcome.questions)},
                          ms=timings["plan_ms"], produced_by={**outcome.produced_by,
                                                              "run_id": run_id}))
        plan_json = outcome.plan.model_dump(mode="json", by_alias=True) if outcome.plan else None
        if outcome.questions or outcome.plan is None:
            md = "\n".join([f"**Question (verbatim):** {question}", "",
                            "**Before I read anything, I need to know:**"]
                           + [f"- {q}" for q in outcome.questions]
                           + ["", "_Nothing was read: an unknown name is a question, never a "
                                  "guess._"])
            return self._finish(run_id, started, question, md, steps, models, status="stopped",
                                coverage={"searched": [], "skipped": [], "reasons": []},
                                plan=plan_json, questions=outcome.questions, timings=timings,
                                t_start=t_start, admission=admission, trace=trace,
                                usage=usage, answers=answers)

        today = self.clock().date()
        try:
            the_plan = compare_window(resolve_symbols(outcome.plan, today), today)
        except PlanQuestion as pq:
            # a period would be computed from a partial read: ask, read nothing
            md = "\n".join([f"**Question (verbatim):** {question}", "",
                            "**Before I read anything, I need to know:**", f"- {pq}"])
            remember_questions([str(pq)])
            return self._finish(run_id, started, question, md, steps, models, status="stopped",
                                coverage={"searched": [], "skipped": [], "reasons": []},
                                plan=plan_json, questions=[str(pq)], timings=timings,
                                t_start=t_start, admission=admission, trace=trace,
                                usage=usage, answers=answers)
        plan_json = the_plan.model_dump(mode="json", by_alias=True)

        t = time.perf_counter()
        results = self.dispatch(list(the_plan.subtasks), admission=admission, run_id=run_id,
                                resolve_names=name_resolvable(the_plan, self.catalogue))
        timings["dispatch_ms"] = int((time.perf_counter() - t) * 1000)
        timings["subtasks"] = {k: {"ms": (v.timings or {}).get("ms"), "method": v.method,
                                   "queries": v.queries, "rows": len(v.rows)}
                               for k, v in results.items()}
        searched: list[str] = []
        skipped: list[dict] = []
        reasons: list[str] = []
        resolved: list[str] = []
        delegate_uncertain: list[str] = []
        for task in the_plan.subtasks:
            r = results[task.id]
            searched.extend(s for s in r.coverage.sources_read if s not in searched)
            for sk in r.coverage.skipped:
                skipped.append({"tool": f"delegate.ask:{task.adapter_agent}",
                                "reason_code": "delegate_skipped", **sk.model_dump()})
                reasons.append(f"{task.adapter_agent}"
                               f"{'/' + sk.connection if sk.connection else ''} "
                               f"({sk.channel or 'delegate'}) was not read: {sk.reason}")
            if r.error and not r.coverage.skipped:
                skipped.append({"tool": f"delegate.ask:{task.adapter_agent}",
                                "reason_code": "delegate_failed", "reason": r.error})
                reasons.append(f"{task.adapter_agent} could not answer: {r.error}")
            if r.coverage.truncated:
                skipped.append({"tool": f"delegate.ask:{task.adapter_agent}",
                                "reason_code": "truncated", "reason": "result capped"})
                reasons.append(f"{task.adapter_agent}'s result was capped; totals may be "
                               f"incomplete")
            if not r.error and not r.coverage.exhaustive:
                skipped.append({"tool": f"delegate.ask:{task.adapter_agent}",
                                "reason_code": "non_exhaustive",
                                "reason": "ranked passage search, not an exhaustive scan"})
                reasons.append(f"{task.adapter_agent}'s rows come from a ranked passage search, "
                               f"not an exhaustive scan: a match it did not return was never "
                               f"considered, so counts and totals built on it may be incomplete")
            for ex in r.coverage.excluded:
                skipped.append({"tool": f"delegate.ask:{task.adapter_agent}",
                                "reason_code": "excluded", "reason": ex})
                reasons.append(f"read but not used: {ex}")
            resolved.extend(r.coverage.resolved)
            delegate_uncertain.extend(r.coverage.uncertain)
            ptrs = [gv.pointer.model_dump(mode="json") for row in r.rows[:50]
                    for gv in row.cells.values()]
            steps.append(Step(
                tool=f"delegate.ask:{task.adapter_agent}",
                args=task.model_dump(mode="json", by_alias=True),
                result_pointers=ptrs,
                policy_decision={"decision": "refuse" if r.method == "refused" else "permit",
                                 "reason_code": r.method or ("failed" if r.error else ""),
                                 "detail": r.error or "; ".join(r.coverage.notes)},
                ms=int((r.timings or {}).get("ms") or 0),
                produced_by={**(r.produced_by or {"agent": "delegate", "model": None,
                                                  "by": "rules"}), "run_id": run_id}))

        t = time.perf_counter()
        table: Optional[CombinedTable] = None
        combine_error = None
        try:
            if the_plan.combine is not None:
                table = combine(the_plan.combine, results, today=today,
                                catalogue=self.catalogue, store=self.store,
                                admits=self.admits(admission))
            else:
                only = results[the_plan.subtasks[0].id]
                table = table_from_result(only) if not only.error else None
                if only.error:
                    combine_error = only.error
        except CombineError as e:
            combine_error = str(e)
        timings["combine_ms"] = int((time.perf_counter() - t) * 1000)
        for ex in (table.excluded if table is not None else []):
            skipped.append({"tool": "federated.combine", "reason_code": "excluded", "reason": ex})
            reasons.append(f"not counted: {ex}")
        uncertain = [*delegate_uncertain, *(table.uncertain if table is not None else [])]
        if resolved:
            # a filter read as a stored value is an INTERPRETATION: shown with the assumptions
            the_plan = the_plan.model_copy(update={"assumptions": (*the_plan.assumptions,
                                                                   *resolved)})
            plan_json = the_plan.model_dump(mode="json", by_alias=True)
        base_id = the_plan.combine.base if the_plan.combine is not None else \
            the_plan.subtasks[0].id
        base_task = next(x for x in the_plan.subtasks if x.id == base_id)
        base_res = results.get(base_id)
        if base_res is not None and not base_res.error and not base_res.rows and \
                (base_task.filters or base_task.time_window is not None):
            # an empty read under a filter is reported: a value that never occurs in the column
            # (status = 'overdue' where the column holds 'open'/'paid') looks exactly like this
            uncertain.append(
                f"sub-task {base_id!r} read no rows under "
                f"{[f.describe() for f in base_task.filters]}"
                f"{' within ' + base_task.time_window.label() if base_task.time_window else ''}; "
                f"an empty answer may mean the filter value does not occur in the data")
        for why in uncertain:
            skipped.append({"tool": "federated.combine", "reason_code": "uncertain",
                            "reason": why})
            reasons.append(f"uncertain: {why}")
        if combine_error:
            skipped.append({"tool": "federated.combine", "reason_code": "combine_failed",
                            "reason": combine_error})
            reasons.append(f"no table could be computed: {combine_error}")
        steps.append(Step(tool="federated.combine",
                          args=(the_plan.combine.model_dump(mode="json")
                                if the_plan.combine else {"single_source": True}),
                          result_pointers=[gv.pointer.model_dump(mode="json")
                                           for r in (table.rows if table else [])
                                           for gv in r.cells.values() if gv is not None][:200],
                          policy_decision={"decision": "permit", "reason_code": "rules",
                                           "detail": json.dumps(table.steps if table else [],
                                                                default=str)[:4000]},
                          ms=timings["combine_ms"],
                          produced_by={"agent": "federated.combine", "model": None,
                                       "by": "rules", "task_class": None, "run_id": run_id,
                                       "at": iso(utcnow())}))

        coverage = {"searched": searched, "skipped": skipped, "reasons": reasons}
        if table is not None and table.notes:
            # E2E4 Q-06: said with the coverage, and not a reason: it does not reduce it
            coverage["notes"] = list(table.notes)
        t = time.perf_counter()
        md, checked, narr_by = self._narrate(question, table, the_plan, coverage, run_id,
                                             usage=usage)
        timings["narrate_ms"] = int((time.perf_counter() - t) * 1000)
        if narr_by.get("model"):
            models.append(narr_by["model"])
        steps.append(Step(tool="federated.narrate", args={},
                          policy_decision={"decision": "permit", "reason_code": narr_by["by"],
                                           "detail": ""},
                          ms=timings["narrate_ms"], produced_by={**narr_by, "run_id": run_id}))
        return self._finish(run_id, started, question, md, steps, models,
                            status="completed" if table is not None else "stopped",
                            coverage=coverage, plan=plan_json, questions=[], timings=timings,
                            t_start=t_start, admission=admission, table=table, checked=checked,
                            answer_by=narr_by, trace=trace, usage=usage, answers=answers)

    # ---- narration: the model sees the table, never the rows ----------------------------------
    def _registry(self, table: Optional[CombinedTable]) -> tuple[PointerRegistry, list[dict]]:
        registry = PointerRegistry()
        view: list[dict] = []
        if table is None:
            return registry, view
        for r in table.rows:
            row_view: dict[str, Any] = {}
            for c in table.columns:
                gv = r.cells.get(c)
                if gv is None:
                    row_view[c] = {"value": None, "ref": None}
                    continue
                ref = registry.add(gv.pointer, f"{c} = {_fmt(gv.value)}",
                                   subject_candidate=r.candidate)
                row_view[c] = {"value": _fmt(gv.value), "ref": ref}
            if r.candidate:
                row_view["_candidate"] = True
            view.append(row_view)
        return registry, view

    def _narrate(self, question: str, table: Optional[CombinedTable], plan: DelegationPlan,
                 coverage: dict, run_id: str, usage: Optional[dict] = None
                 ) -> tuple[str, Any, dict]:
        registry, view = self._registry(table)
        assumptions = [Clarification(assumption=a) for a in plan.assumptions]
        final: Optional[FinalAnswer] = None
        by: dict = {"agent": "federated.narrator", "by": "rules", "model": None,
                    "task_class": None, "prompt_version": None,
                    "at": iso(utcnow())}
        # E2E3 P-01: a table that states NO value (a link matched nothing, an empty average) is
        # not narrated by a model, which could word the gap as "0" or "none"; it is reported
        stated = table is not None and any(gv is not None for r in table.rows
                                           for gv in r.cells.values())
        if stated and self.harness is not None:
            system = ("You narrate a table that a deterministic engine computed. Use ONLY the "
                      "values in it, exactly as written, and cite the ref of every value you "
                      "state (e.g. ['p3']). Never compute, round, add or compare numbers "
                      "yourself — if a difference matters, it is in the table's change column. "
                      "Rows marked _candidate concern people found in the data: say they are "
                      "candidates (every claim about such a row is also marked a candidate "
                      "requiring human selection by the engine, whatever you write). Return JSON "
                      "{claims:[{text, citations}], clarifications:[]}.")
            pv = (f"{NARRATOR_PROMPT_VERSION}+sha256:"
                  f"{hashlib.sha256(system.encode('utf-8')).hexdigest()[:12]}")
            payload = {"question": question, "columns": table.columns, "rows": view,
                       "absent": table.absent}
            try:
                res = self.harness.complete_structured(
                    NARRATE_TASK_CLASS,
                    [{"role": "system", "content": system},
                     {"role": "user", "content": "TABLE:\n" + json.dumps(payload, default=str)}],
                    FinalAnswer)
                final = res.parsed
                ev = getattr(res, "event", None)
                if usage is not None:                 # E2E N-19: metered, as agent runs are
                    usage["tokens"] += int(getattr(ev, "tokens", 0) or 0)
                    usage["cost_usd"] += float(getattr(ev, "cost_usd", 0.0) or 0.0)
                by = {"agent": "federated.narrator", "by": "model",
                      "model": getattr(ev, "model_used", None),
                      "task_class": getattr(ev, "task_class", None) or NARRATE_TASK_CLASS,
                      "prompt_version": pv, "at": iso(utcnow())}
            except Exception as e:                           # noqa: BLE001 - fall back to rules
                coverage["reasons"].append(f"the narrator could not be asked ({type(e).__name__}"
                                           f"); the table is described by rules")
        if final is None and table is not None:
            claims = []
            for r, rv in zip(table.rows, view):
                parts, refs = [], []
                for c in table.columns:
                    cell = rv.get(c) or {}
                    if cell.get("ref"):
                        parts.append(f"{c} {cell['value']}")
                        refs.append(cell["ref"])
                if parts:
                    claims.append(Claim(text="; ".join(parts), citations=refs))
            final = FinalAnswer(claims=claims)
        flags: list[bool] = []
        if final is not None and table is not None:
            # candidate status is STRUCTURAL: decided from the table, never left to wording
            final = enforce_candidates(final, table, view)
            flags = [c.text.startswith(CANDIDATE_PREFIX) for c in final.claims]
        if final is not None:
            final = FinalAnswer(claims=final.claims,
                                clarifications=[*final.clarifications, *assumptions])
        # the facts snapshots' extraction dates are run context the planner was shown
        extracted = [str(c.extract_date)[:10] for t in plan.subtasks
                     for c in [choose_connection(t, self.catalogue)[0]]
                     if c is not None and c.extract_date]
        literals = "\n".join([scope_literals(question, plan), *dict.fromkeys(extracted)])
        checked = (check_answer(final, registry, literals=literals)
                   if final is not None else None)
        if checked is not None:
            checked.extra["candidate_flags"] = flags
        audit = (f"Computed by rules from {sum(1 for _ in (table.rows if table else []))} table "
                 f"row(s); {len(registry)} pointer(s); arithmetic in Decimal, no model; "
                 f"narration by {by['by']}{' (' + str(by['model']) + ')' if by.get('model') else ''}.")
        md = render_markdown(heading="Question", verbatim=question, checked=checked,
                             registry=registry, coverage=coverage,
                             stop_reason=None if table is not None else
                             "no table could be computed", audit=audit, actions=[])
        if table is not None and not stated:
            gaps = table.absent or ["the computed table holds no value"]
            md += ("\n\n**No value is stated** (this does not show that nothing qualifies):\n"
                   + "\n".join(f"- {_md_line(a)}" for a in gaps))
        if table is not None and table.rows:
            md += "\n\n" + self._table_md(table, view)
        return md, checked, by

    @staticmethod
    def _table_md(table: CombinedTable, view: list[dict]) -> str:
        head = "| " + " | ".join(table.columns) + " |"
        sep = "|" + "---|" * len(table.columns)
        lines = [head, sep]
        for rv in view:
            cells = []
            for c in table.columns:
                cell = rv.get(c) or {}
                cells.append("—" if cell.get("value") is None else
                             f"{cell['value']} [{cell['ref']}]")
            lines.append("| " + " | ".join(cells) + " |" + (" _candidate_" if
                                                             rv.get("_candidate") else ""))
        return "\n".join(lines)

    # ---- run record ------------------------------------------------------------------------
    def _finish(self, run_id: str, started: dt.datetime, question: str, md: str,
                steps: list[Step], models: list[str], *, status: str, coverage: dict,
                plan: Optional[dict], questions: list[str], timings: dict, t_start: float,
                admission: Any, table: Optional[CombinedTable] = None, checked: Any = None,
                answer_by: Optional[dict] = None, trace: Any = None,
                usage: Optional[dict] = None,
                answers: Optional[dict[str, str]] = None) -> FederatedAnswer:
        timings["total_ms"] = int((time.perf_counter() - t_start) * 1000)
        usage = usage or {"tokens": 0, "cost_usd": 0.0}
        # complete | reduced (something was not read or not counted) | uncertain (the result
        # may be emptier than the truth: E2E N-02). Never "complete" with a reason on record.
        coverage["status"] = ("uncertain" if any(s.get("reason_code") == "uncertain"
                                                 for s in coverage.get("skipped") or [])
                              else "reduced" if (coverage.get("skipped") or
                                                 coverage.get("reasons")) else "complete")
        # the plan step keeps the plan's assumptions and the questions asked (additive keys in
        # the step's free-form args; `RunRecord` itself is closed) so /activity can show them
        for st in steps:
            if st.tool == "federated.plan":
                st.args = {**st.args,
                           "assumptions": list((plan or {}).get("assumptions") or []),
                           "questions": list(questions)}
                break
        _record_trace(trace, run_id=run_id, steps=steps, coverage=coverage, status=status,
                      wall_ms=timings["total_ms"], principal=getattr(admission, "principal", None),
                      purpose=getattr(admission, "purpose", None), tokens=int(usage["tokens"]))
        principal = str(getattr(admission, "principal", "anonymous"))
        who = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in principal)[:64] or "x"
        digest = "sha256:" + hashlib.sha256(json.dumps(plan or {}, sort_keys=True,
                                                       default=str).encode()).hexdigest()
        record = RunRecord(
            run_id=run_id, spec_digest=digest,
            trigger={"type": "manual", "by": principal, "question": question,
                     "fired_at": iso(started)},
            started=iso(started), ended=iso(utcnow()),
            status=status if status in ("completed", "stopped", "failed", "refused")
            else "completed",
            models_used=list(dict.fromkeys(m for m in models if m)), steps=steps,
            tokens=int(usage["tokens"]), cost_usd=round(float(usage["cost_usd"]), 6),
            answer=AnswerBlock(markdown=md,
                               citations=checked.citations if checked else [],
                               unsupported_claims=checked.unsupported if checked else [],
                               produced_by=answer_by),
            coverage=Coverage(searched=coverage["searched"], skipped=coverage["skipped"],
                              reasons=coverage["reasons"]))
        try:
            write_run(record, run_path(run_id, agent=who, kind=RUN_KIND, runs_dir=self.runs_dir))
        except OSError as e:                                 # a run never vanishes silently
            coverage["reasons"].append(f"the run record could not be written: {e}")
        flags = list((checked.extra or {}).get("candidate_flags") or []) if checked else []
        claims = []
        for i, c in enumerate(checked.claims if checked else []):
            cand = bool(c.subject_candidate or (flags[i] if i < len(flags) else False))
            claims.append({"text": c.text, "citations": list(c.refs), "supported": c.supported,
                           "reason": c.reason, "subject_candidate": cand,
                           "requires_human_selection": cand})
        needs_human = any(c["requires_human_selection"] for c in claims) or \
            any(r.candidate for r in (table.rows if table is not None else []))
        return FederatedAnswer(
            run_id=run_id, question=question, answer_markdown=md,
            table=table.as_json() if table is not None else {},
            citations=checked.citations if checked else [],
            unsupported_claims=checked.unsupported if checked else [],
            claims=claims, requires_human_selection=needs_human,
            coverage=coverage, plan=plan, questions=questions,
            question_items=[{"id": question_id(q), "text": q} for q in questions],
            answers=dict(answers or {}), timings=timings,
            produced_by={"planner": steps[0].produced_by if steps else None,
                         "narrator": answer_by}, status=status)


_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
#: A DATE the asker wrote: ``1 October 2026``, ``October 1, 2026``, ``October 2026``,
#: ``2026-10-01``, ``01/10/2026``, ``Q4 2026``. Its numbers name the question's scope.
_DATE_PHRASE = re.compile(
    rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+\d{{4}}|{_MONTH}\.?\s+\d{{1,2}}"
    rf"(?:st|nd|rd|th)?,?\s+\d{{4}}|{_MONTH}\.?\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|"
    rf"\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|q[1-4]\s+\d{{4}})\b", re.IGNORECASE)
#: An IDENTIFIER token: letters AND digits in one token (``C-1003``, ``INV-2026-0063``, ``LA-007``).
_ID_TOKEN = re.compile(r"(?<![\w-])(?=[\w-]*[A-Za-z])(?=[\w-]*\d)[A-Za-z0-9][\w-]*")


def scope_literals(question: str, plan: Optional[DelegationPlan]) -> str:
    """The text whose numbers a narrated claim may RESTATE without citing them (E2E3 P-12): the
    dates and identifiers that SCOPE the question — written by the asker in the question, or
    present in the plan's query arguments (filter values, resolved time-window bounds) that every
    cited value was read under. Only dates and identifier tokens qualify: a bare number in the
    question ("do we have 5 open invoices?") could be the very answer, so it never grounds a
    claim; the stated value must still come from the cited evidence."""
    parts = [m.group(0) for m in _DATE_PHRASE.finditer(question or "")]
    parts += [m.group(0) for m in _ID_TOKEN.finditer(question or "")]
    if plan is not None:
        filters = [f for t in plan.subtasks for f in t.filters]
        filters += list(plan.combine.filters) if plan.combine is not None else []
        for f in filters:
            vals = f.value if isinstance(f.value, (list, tuple)) else [f.value]
            for v in vals:
                if v is None or isinstance(v, bool):
                    continue
                text = str(v)
                parts += [m.group(0) for m in _DATE_PHRASE.finditer(text)]
                parts += [m.group(0) for m in _ID_TOKEN.finditer(text)]
        for t in plan.subtasks:
            tw = t.time_window
            for d in ((getattr(tw, "from_", None), getattr(tw, "to", None)) if tw else ()):
                if isinstance(d, (dt.date, str)) and _DATE_PHRASE.fullmatch(str(d)[:10]):
                    parts.append(str(d)[:10])
    return "\n".join(dict.fromkeys(parts))


def name_resolvable(plan: DelegationPlan, catalogue: Catalogue) -> frozenset[str]:
    """The sub-tasks whose empty text filter may be read as the one stored value holding all its
    words (E2E3 P-01). Only a sub-task whose rows are KNOWN to denote a non-person kind: the other
    side of a field link whose ``key_kind`` is a loaded ontology kind that is not a subject kind
    (a customer table joined on an organisation id). A sub-task touched by any link through a
    subject kind, and any sub-task whose kind is unknown, is never resolved: reading 'Dana Lee'
    as 'Dana Lee-Smith' would be deciding who someone is (hard constraint 1)."""
    c = plan.combine
    if c is None or not catalogue.vocabulary_loaded:
        return frozenset()
    subjecty: set[str] = set()
    organised: set[str] = set()
    for link in c.links:
        kinds = [k for k in (link.key_kind, link.entity_kind) if k is not None]
        if any(catalogue.is_subject(k) for k in kinds):
            subjecty |= {link.subtask, c.base}
        elif link.other_field is not None and link.key_kind is not None and \
                link.key_kind in catalogue.node_kinds:
            organised.add(link.subtask)
    return frozenset(organised - subjecty)


def enforce_candidates(final: FinalAnswer, table: CombinedTable, view: list[dict]
                       ) -> FinalAnswer:
    """Make candidate status part of every claim that concerns a candidate row, whatever the
    narrator wrote (R-021 r2 #3). A claim concerns a candidate row when it CITES one of that row's
    refs, or its text NAMES one of that row's identifying (non-numeric) values. Such a claim:

    * opens with `CANDIDATE_PREFIX` (any wording the narrator used is kept after it);
    * cites at least one ref of every candidate row it concerns, so the answer contract
      (`check_answer`) marks it ``subject_candidate`` from the registry — not from its words.

    Other claims are unchanged."""
    rows: list[tuple[set[str], list[tuple[str, str]]]] = []
    for r, rv in zip(table.rows, view):
        if not r.candidate:
            continue
        refs = {cell["ref"] for c in table.columns
                if isinstance(cell := rv.get(c), dict) and cell.get("ref")}
        idents = []
        for c in table.columns:
            gv = r.cells.get(c)
            cell = rv.get(c) if isinstance(rv.get(c), dict) else {}
            if gv is None or not cell.get("ref") or not isinstance(gv.value, str):
                continue
            v = gv.value.strip()
            if len(v) >= _MIN_IDENT and to_decimal(v) is None:
                idents.append((v.lower(), cell["ref"]))
        rows.append((refs, idents))
    if not rows:
        return final
    out = []
    for claim in final.claims:
        cited = {str(x).strip().strip("[]^") for x in claim.citations}
        low = claim.text.lower()
        add: list[str] = []
        concerns = False
        for refs, idents in rows:
            hit = bool(cited & refs)
            named = [ref for v, ref in idents if v in low]
            if hit or named:
                concerns = True
                if not hit:
                    add.append(named[0])
        if not concerns:
            out.append(claim)
            continue
        text = claim.text
        if not text.startswith(CANDIDATE_PREFIX):
            text = CANDIDATE_PREFIX + text
        out.append(Claim(text=text, citations=[*claim.citations,
                                               *(a for a in add if a not in cited)]))
    return FinalAnswer(claims=out, clarifications=list(final.clarifications))


def _record_trace(trace: Any, *, run_id: str, steps: list[Step], coverage: dict, status: str,
                  wall_ms: int, principal: Optional[str], purpose: Optional[str],
                  tokens: int = 0) -> None:
    """Mirror the run into a gateway `TraceHandle` (§5.7: `/trace` shows it). Best effort: a trace
    that cannot be written never changes the answer."""
    if trace is None:
        return
    try:
        from databridge_core import SourcePointer                   # noqa: PLC0415
        trace.principal(actor="agent:federated", on_behalf_of=principal, purpose=purpose)
        trace.note(key="federated_run", text=run_id)
        for st in steps:
            by = st.produced_by or {}
            trace.note(key=st.tool, text=f"{st.policy_decision.get('decision')} "
                                         f"{st.policy_decision.get('reason_code')} {st.ms}ms "
                                         f"by={by.get('by')} model={by.get('model')}")
            for p in st.result_pointers[:20]:
                trace.from_source_pointer(SourcePointer.model_validate(p), origin="graph")  # trust-gate: exempt — re-validates a serialised pointer; its label travels in it
        for reason in coverage.get("reasons") or []:
            trace.note(key="reduced_coverage", text=reason)
        for note in coverage.get("notes") or []:
            trace.note(key="coverage_note", text=note)
        trace.cost(llm_tokens=tokens, wall_ms=wall_ms)
        trace.close({"completed": "answered"}.get(status, status))
    except Exception:                                                # noqa: BLE001 - best effort
        pass


__all__ = ["FederatedService", "FederatedAnswer", "CatalogueAdmission", "RUN_KIND",
           "name_resolvable", "scope_literals",
           "MAX_PARALLEL", "SUBTASK_TIMEOUT_S", "CANDIDATE_PREFIX", "enforce_candidates"]
