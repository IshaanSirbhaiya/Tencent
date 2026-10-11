"""`/brain/query` → delegation: which questions leave the graph path, and how (E2E run 2, N-14).

A question that needs several sources, or arithmetic over a set of rows, is not one the graph
narrator can answer: it sees a 1-hop neighbourhood, and a model asked to add up what it sees
invents totals (or, correctly, declines). Those questions go to the SAME in-process service
``POST /federated/ask`` uses (`databridge_federated`): rules or the planner split the question,
each adapter agent's delegate reads its own sources under the CALLER's grant, a deterministic
engine combines, and every number in the returned table is a derived value with its pointer.

**Routing is decided in two stages, rules first.**

1. ``route_by_rules`` — deterministic, over the question's words and the MEASURED catalogue
   (table names and the documents channel), never a list of one domain's nouns:

   * an aggregate (``how many``, ``number of``, ``sum``, ``average``, a ranking such as
     ``highest`` / ``top 5``) or a comparison (``compared with``, ``versus``) → **federated**;
   * ``total`` → federated, unless the question names ONE record by an identifier
     (``INV-2026-0041``-shaped) and nothing else aggregates: a single record's total is a field
     of that record, which the graph holds;
   * a set question — ``<plural noun> who|that|which … and …``, or ``which|all|list <table>`` —
     → **federated**;
   * a period (``this quarter``) over a catalogue table → **federated**;
   * a catalogue table named in the PLURAL (a set of rows) beside a document word or a second
     table, or a period alone, with none of the above → **undecided** (a table named in the
     singular is one record: a lookup);
   * anything else → **graph**.

2. Only an undecided question reaches the harness router (``complete_structured`` with the
   brain's own task class); a router that is unavailable or fails leaves it on the graph path,
   which is the conservative side (it computes nothing).

**Who may read what is the caller's operator grant** (``WALNUT_FEDERATED_GRANTS``, the same
function ``/federated/ask`` uses). No grant → the question stays on the graph path and the route
says why; nothing is read through delegation.

**When delegation reads nothing** — the planner asked a question instead of guessing a name, or the
federation itself is unavailable — the graph path answers and the delegation attempt (its run id,
its questions) rides along, so the caller sees both. When delegation DID read and could not
compute (a refused source, a failed link), its reduced-coverage answer is returned as-is: a graph
narration of the same question would be a total computed from half the evidence.

**Except an aggregate the RULES routed** (E2E run 5, W-03/W-04): a count, total, statistic,
ranking, comparison or row set. The graph path computes none of those, so when the planner asks
instead of reading, its question IS the answer (``answer_kind: needs_clarification``, with
``questions`` and ``question_items``) — never a graph narration that the data "does not contain"
what the planner only needed a word about. A question the router (a model) routed keeps the
graph fallback: it was not shown to be an aggregate.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("databridge.gateway.brain_delegation")

ROUTE_FEDERATED = "federated"
ROUTE_GRAPH = "graph"
ROUTE_UNDECIDED = "undecided"
#: The router uses the brain's own task class: routing a /brain question is part of answering it,
#: and it keeps the model choice in `config/models.yaml` (no model name here).
#: INT5 (BRAIN cross-track): its OWN task class now, so routing can be given a smaller or faster
#: model than answering without touching the answer's route. It maps to the same alias as
#: `brain_query` in `config/models.yaml` today.
ROUTER_TASK_CLASS = "brain_routing"
#: Where the Activity page shows one run (the founder's link shape).
ACTIVITY_PATH = "/trace"
#: The rules whose questions the graph path cannot answer at all (it computes nothing): when the
#: planner asks instead of reading, the question goes back to the asker (E2E run 5, W-03).
AGGREGATE_RULES = frozenset({"count", "statistic", "ranking", "comparison", "total",
                             "set_intersection", "set_of_rows", "period_over_table"})

# --- the generic lexicon: English function words and quantifiers, never a domain noun ----------
_COUNT = re.compile(r"\b(?:how many|number of|count (?:of|the|all|every))\b")
_STAT = re.compile(r"\b(?:sum|average|avg|mean|median)\b")
_TOTAL = re.compile(r"\btotals?\b")
_RANK = re.compile(r"\b(?:top \d+|highest|lowest|largest|smallest|biggest|most|fewest|least)\b")
#: "most recent" / "at most" / "at least" rank nothing.
_NOT_RANK = re.compile(r"\b(?:most recent(?:ly)?|at most|at least|least recent(?:ly)?)\b")
_COMPARE = re.compile(r"\b(?:compared? (?:with|to)|versus|vs\.?|against last|"
                      r"(?:change|growth|increase|decrease|difference) (?:from|between|since|over))\b")
_PERIOD = re.compile(r"\b(?:(?:this|last|previous|prior|next) (?:quarter|month|year|week)|"
                     r"q[1-4]|year[- ]to[- ]date|ytd)\b")
#: "<plural noun> who|that|which … and …": a set built from two conditions.
_SET_INTERSECTION = re.compile(r"\b[a-z][a-z_]{2,}s (?:who|that|which|whose|with) \S.* and \S")
#: Words naming the documents channel — generic English, never a pack's kind.
_DOC_WORDS = re.compile(r"\b(?:documents?|files?|notes?|emails?|mentioned|mentions?|"
                        r"meetings?|transcripts?|minutes)\b")
#: One record named by an identifier: letters, an optional joiner, then a digit run
#: (``INV-2026-0041``, ``C-1001``, ``PRJ-101``) — or a graph id (``kind:…``).
_RECORD_ID = re.compile(r"\b[A-Za-z]{1,8}[-_]?\d[\w-]*\b|\b[a-z_]+:[^\s]+")


#: A period token (``Q1``, ``H2``, ``FY2025``, ``FY25``): identifier-SHAPED, but it names a span
#: of time, never one record (Codex R-029 r1 #8).
_PERIOD_TOKEN = re.compile(r"^(?:q[1-4]|h[12]|fy\d{2}(?:\d{2})?|\d{4}|\d{4}-\d{2}(?:-\d{2})?)$",
                           re.I)


def _record_tokens(question: str) -> list[str]:
    """Identifier-shaped tokens that are not periods."""
    return [m.group(0) for m in _RECORD_ID.finditer(question or "")
            if not _PERIOD_TOKEN.match(m.group(0))]


def _norm(question: str) -> str:
    return re.sub(r"\s+", " ", (question or "").strip().lower())


def _noun_forms(name: str) -> tuple[str, str]:
    """A table name as (singular, plural) phrases: ``follow_up_tasks`` → ``follow up task(s)``."""
    words = name.lower().replace("_", " ").strip()
    singular = words[:-1] if words.endswith("s") and not words.endswith("ss") else words
    if singular.endswith("y") and len(singular) > 2 and singular[-2] not in "aeiou":
        return singular, singular[:-1] + "ies"           # facility -> facilities (E2E run 5)
    if singular.endswith(("ss", "x", "z", "ch", "sh")):
        return singular, singular + "es"                 # address -> addresses
    return singular, singular + "s"


def catalogue_tables(catalogue: Any) -> list[str]:
    """Every facts-table name the measured catalogue holds (tabular connections only)."""
    out: list[str] = []
    for aa in getattr(catalogue, "agents", {}).values():
        for conn in getattr(aa, "connections", []) or []:
            if getattr(conn, "channel", None) != "tabular":
                continue
            for t in getattr(conn, "tables", []) or []:
                if t.name not in out:
                    out.append(t.name)
    return out


def catalogue_has_documents(catalogue: Any) -> bool:
    return any(getattr(c, "channel", None) == "documents"
               for aa in getattr(catalogue, "agents", {}).values()
               for c in (getattr(aa, "connections", []) or []))


def _tables_named(q: str, tables: list[str]) -> list[str]:
    named = []
    for t in tables:
        singular, plural = _noun_forms(t)
        if re.search(rf"\b(?:{re.escape(singular)}|{re.escape(plural)})\b", q):
            named.append(t)
    return named


def _plural_tables_named(q: str, tables: list[str]) -> list[str]:
    """Tables named in their PLURAL form: a set of rows, not one record."""
    return [t for t in tables if re.search(rf"\b{re.escape(_noun_forms(t)[1])}\b", q)]


def route_by_rules(question: str, catalogue: Any = None, *, names_record: Any = None) -> dict:
    """``{route: federated|graph|undecided, rule, reason, signals}``. Deterministic: the same
    question over the same catalogue always routes the same way, with no model reached.

    ``names_record(question) -> bool`` (Codex R-029 r1 #8): when given, an identifier-shaped
    token counts as naming ONE record only if it is an actual record; a period (``Q1``) never
    does."""
    q = _norm(question)
    signals: list[str] = []
    record = bool(_record_tokens(question))
    if record and names_record is not None and not names_record(question):
        record = False
    if record:
        signals.append("record_identifier")
    rank = bool(_RANK.search(q)) and not _NOT_RANK.search(q)
    strong = [name for name, hit in (("count", _COUNT.search(q)), ("statistic", _STAT.search(q)),
                                     ("ranking", rank), ("comparison", _COMPARE.search(q)))
              if hit]
    signals += strong
    if strong:
        return {"route": ROUTE_FEDERATED, "rule": strong[0], "signals": signals,
                "reason": f"the question asks for a {strong[0]} over a set of rows, which the "
                          f"delegated engine computes exactly (Decimal, every input cited)"}
    if _TOTAL.search(q):
        signals.append("total")
        if record:
            return {"route": ROUTE_GRAPH, "rule": "total_of_one_record", "signals": signals,
                    "reason": "the question names one record by its identifier, so its total is "
                              "a field of that record, read from the graph"}
        return {"route": ROUTE_FEDERATED, "rule": "total", "signals": signals,
                "reason": "the question asks for a total over a set of rows, which the "
                          "delegated engine computes exactly"}
    if _SET_INTERSECTION.search(q):
        signals.append("set_intersection")
        return {"route": ROUTE_FEDERATED, "rule": "set_intersection", "signals": signals,
                "reason": "the question selects a set by two conditions, which the delegated "
                          "engine joins exactly"}
    period = bool(_PERIOD.search(q))
    if period:
        signals.append("period")
    tables = catalogue_tables(catalogue) if catalogue is not None else []
    named = _tables_named(q, tables)
    if named:
        signals.append("tables:" + ",".join(named))
    plural = _plural_tables_named(q, tables)
    if plural and re.search(r"\b(?:which|all|every|list|show)(?: of)?(?: (?:the|our|their|its|"
                            r"his|her|my))?(?: [a-z]+){0,3} (?:" + "|".join(
                                re.escape(_noun_forms(t)[1]) for t in plural) + r")\b", q):
        signals.append("set_of_rows")
        return {"route": ROUTE_FEDERATED, "rule": "set_of_rows", "signals": signals,
                "reason": f"the question asks for a set of {', '.join(plural)} rows, which the "
                          f"delegated engine reads exactly from the facts store"}
    if period and named:
        return {"route": ROUTE_FEDERATED, "rule": "period_over_table", "signals": signals,
                "reason": "the question windows a table by a period, which the delegated engine "
                          "resolves from its clock and reads exactly"}
    docs = bool(_DOC_WORDS.search(q)) and (catalogue is None or catalogue_has_documents(catalogue))
    if docs:
        signals.append("documents")
    # A table named in the SINGULAR is one record ("their project", "their contact"): a lookup
    # the graph answers, however many tables the question names. A PLURAL is a set of rows, and
    # a set beside documents or a second table may need a join — the router decides.
    if (plural and (docs or len(named) >= 2)) or period:
        return {"route": ROUTE_UNDECIDED, "rule": "cross_source_words", "signals": signals,
                "reason": "the question names a set of rows beside another source; the router "
                          "decides"}
    return {"route": ROUTE_GRAPH, "rule": "lookup", "signals": signals,
            "reason": "a lookup about named entities, answered from the graph and its passages"}


class RouteDecision(BaseModel):
    """What the harness router may return — two routes and a sentence, nothing else."""

    route: Literal["federated", "graph"]
    reason: str = Field(default="", max_length=400)


def _router_prompt(catalogue: Any) -> str:
    tables = catalogue_tables(catalogue) if catalogue is not None else []
    return "\n".join([
        "You route one question to one of two answering paths. You do not answer it.",
        "graph: facts about named entities and their relationships, read from a knowledge graph "
        "and verbatim passages (who, what, where, which document, what a record says).",
        "federated: anything that needs rows from more than one source joined exactly, a set of "
        "rows selected by conditions, or arithmetic over rows (counts, totals, averages, "
        "comparisons across periods).",
        "Facts tables available to the federated path: " + (", ".join(tables) or "(none)"),
        "Return JSON {route: \"graph\" | \"federated\", reason: one short sentence}.",
    ])


def route_question(question: str, *, catalogue: Any = None, router: Any = None,
                   names_record: Any = None) -> dict:
    """Rules first; the harness router only for an undecided question. Never raises."""
    decision = dict(route_by_rules(question, catalogue, names_record=names_record))
    decision["decided_by"] = "rules"
    if decision["route"] != ROUTE_UNDECIDED:
        return decision
    if router is None:
        decision.update(route=ROUTE_GRAPH, decided_by="default",
                        reason=decision["reason"] + "; no router is configured, so the graph "
                                                    "path (which computes nothing) answers")
        return decision
    try:
        res = router.complete_structured(ROUTER_TASK_CLASS, [
            {"role": "system", "content": _router_prompt(catalogue)},
            {"role": "user", "content": f"Question (verbatim): {question}"}], RouteDecision)
        parsed: RouteDecision = res.parsed
        ev = getattr(res, "event", None)
        decision.update(route=parsed.route, decided_by="router",
                        reason=parsed.reason or decision["reason"],
                        router={"task_class": ROUTER_TASK_CLASS,
                                "model": getattr(ev, "model_used", None)})
    except Exception as exc:                                         # noqa: BLE001
        log.warning("brain router failed (%s); the graph path answers", type(exc).__name__)
        decision.update(route=ROUTE_GRAPH, decided_by="default",
                        reason=f"{decision['reason']}; the router could not be asked "
                               f"({type(exc).__name__}), so the graph path answers")
    return decision


# ------------------------------------------------------------------------------------------------
# the delegation call
# ------------------------------------------------------------------------------------------------
def _service(app: Any) -> Any:
    from .federated_routes import _ensure_importable, _factory      # noqa: PLC0415
    _ensure_importable()
    return _factory(app)()


def activity_url(run_id: Optional[str], request_id: Optional[str] = None) -> Optional[str]:
    """The Activity page for one run: ``/trace?run=<run_id>`` (plus the trace request id, which
    the per-request view opens directly)."""
    if not run_id:
        return None
    from urllib.parse import urlencode                                # noqa: PLC0415
    q = {"run": run_id}
    if request_id:
        q["request"] = request_id
    return f"{ACTIVITY_PATH}?{urlencode(q)}"


def coverage_summary(coverage: dict) -> dict:
    """The answer's coverage with an explicit ``reduced`` flag: anything skipped, refused,
    truncated, non-exhaustive or excluded is reduced coverage, said at the top, never silent."""
    cov = dict(coverage or {})
    skipped = list(cov.get("skipped") or [])
    reasons = list(cov.get("reasons") or [])
    cov["skipped"], cov["reasons"] = skipped, reasons
    cov["searched"] = list(cov.get("searched") or [])
    # Codex R-029 r1 #4: every signal of an incomplete read counts — and an upstream "reduced"
    # is never overwritten with False
    partial_codes = {"truncated", "non_exhaustive", "delegate_skipped", "delegate_failed",
                     "combine_failed", "excluded", "uncertain"}
    cov["partial"] = bool(
        cov.get("partial") or cov.get("truncated") or cov.get("exhaustive") is False
        or any(isinstance(s, dict) and s.get("reason_code") in partial_codes for s in skipped)
        or any("time budget" in str(x) or "partial" in str(x).lower()
               for x in [*reasons, *(cov.get("excluded") or [])]))
    cov["reduced"] = bool(cov.get("reduced") or cov.get("status") == "reduced" or skipped
                          or reasons or cov.get("excluded") or cov.get("uncertain")
                          or cov["partial"])
    return cov


def candidate_flags(answer: dict) -> list[dict]:
    """Every candidate the delegated answer carries, structurally: a table row admitted through a
    person (a subject kind) and every claim that concerns one. Decided by the federated service
    from the table — never from wording — and repeated here so a client can gate on one list."""
    out: list[dict] = []
    for i, row in enumerate((answer.get("table") or {}).get("rows") or []):
        if row.get("candidate") or row.get("requires_human_selection"):
            out.append({"where": "table_row", "row": i, "requires_human_selection": True})
    for i, claim in enumerate(answer.get("claims") or []):
        if claim.get("requires_human_selection") or claim.get("subject_candidate"):
            out.append({"where": "claim", "claim": i, "text": claim.get("text"),
                        "requires_human_selection": True})
    return out


def federated_payload(answer: dict, route: dict, *, request_id: Optional[str]) -> dict:
    """The `/brain/query` body for a delegated answer: the federated answer's own fields, plus
    the routing marker and the brain fields a client already reads (``answer``, ``grounded``,
    ``citations``, ``matched_entities``)."""
    table = answer.get("table") or {}
    rows = table.get("rows") or []
    # E2E3 P-01: a table whose every cell is null (a link matched nothing) STATES nothing: it is
    # not a grounded answer, and its refusal says why rather than reading as a computed zero
    stated = any(isinstance(r, dict) and any(c for c in (r.get("cells") or {}).values())
                 for r in rows)
    computed = stated and answer.get("status") == "completed"
    run_id = answer.get("run_id")
    coverage = coverage_summary(answer.get("coverage") or {})
    md = answer.get("answer_markdown") or ""
    if coverage["partial"] and "partial" not in md[:400].lower():
        # r1 #4: a partial result is said FIRST, not only in the coverage block
        md = ("> **Partial result.** Some of what this answer needs was not read completely; "
              "the figures below may be incomplete.\n\n" + md)
    return {
        "question": answer.get("question"),
        "routed_to": ROUTE_FEDERATED,
        "route": route,
        "answer": md,
        "partial": coverage["partial"],
        # grounded = a computed table whose every cell carries its derived pointer; an answer
        # with no table (stopped, or nothing computable) is not a grounded answer
        "grounded": computed,
        "refusal": ("" if computed else
                    "no value could be stated: " + "; ".join(str(a) for a in table["absent"])
                    if rows and table.get("absent") else
                    "no table could be computed for this question"),
        "is_real_llm": bool(((answer.get("produced_by") or {}).get("narrator") or {})
                            .get("model")),
        "matched_entities": [],
        "status": answer.get("status"),
        "table": table,
        "claims": list(answer.get("claims") or []),
        "citations": list(answer.get("citations") or []),
        "unsupported_claims": list(answer.get("unsupported_claims") or []),
        "coverage": coverage,
        "candidate_flags": candidate_flags(answer),
        "requires_human_selection": bool(answer.get("requires_human_selection")),
        "questions": list(answer.get("questions") or []),
        "plan": answer.get("plan"),
        "timings": answer.get("timings") or {},
        "produced_by": answer.get("produced_by") or {},
        "run_id": run_id,
        "trace_request_id": request_id,
        "activity_url": activity_url(run_id, request_id),
    }


def clarification_payload(answer: dict, route: dict, *, request_id: Optional[str]) -> dict:
    """The `/brain/query` body when a RULE-routed aggregate was planned and the planner asked
    instead of reading (E2E run 5, W-03): the `/federated/ask` needs-input shape (``questions``,
    ``question_items`` with their ``fed:`` ids), the run and its Activity link. Nothing was read,
    so nothing is grounded and nothing is said to be absent."""
    run_id = answer.get("run_id")
    questions = list(answer.get("questions") or [])
    items = list(answer.get("question_items") or []) or [{"id": None, "text": q}
                                                          for q in questions]
    return {
        "question": answer.get("question"),
        "routed_to": ROUTE_FEDERATED,
        "route": route,
        "answer": answer.get("answer_markdown") or "",
        "answer_kind": "needs_clarification",
        "needs_clarification": True,
        "grounded": False,
        "refusal": "",
        "is_real_llm": bool(((answer.get("produced_by") or {}).get("narrator") or {})
                            .get("model")),
        "matched_entities": [],
        "status": answer.get("status"),
        "table": {},
        "claims": [],
        "citations": [],
        "questions": questions,
        "question_items": items,
        "coverage": coverage_summary(answer.get("coverage") or {}),
        "plan": answer.get("plan"),
        "timings": answer.get("timings") or {},
        "run_id": run_id,
        "trace_request_id": request_id,
        "activity_url": activity_url(run_id, request_id),
    }


def _error_ref() -> str:
    import uuid                                                      # noqa: PLC0415
    return "brain-" + uuid.uuid4().hex[:12]


def _names_a_record(app: Any, question: str) -> bool:
    """True when an identifier token of the question is EXACTLY one open non-subject record's
    canonical id or label in the graph (`brain_server.record_id_matches`)."""
    store = getattr(getattr(getattr(app, "state", None), "brain", None), "store", None)
    if store is None:
        return False
    try:
        from brain_server import record_id_matches                   # noqa: PLC0415
        tiers = record_id_matches(store, " ".join(_record_tokens(question)))
    except Exception:                                                # noqa: BLE001 - unknown
        return False
    return bool(tiers[0] or tiers[1])


def delegate(app: Any, question: str, principal: Any, *, router: Any = None) -> dict:
    """Decide the route and, for a delegated question, ask the federated service.

    Returns ``{"route": decision}`` alone when the graph path should answer, plus
    ``"payload"`` (the full `/brain/query` body) when the delegated answer is served, or
    ``"attempt"`` (run id, status, questions) when delegation read nothing and the graph path
    answers with the attempt attached. Never raises: a failure is a route reason.
    """
    from .federated_routes import (_NOT_GRANTED, admitted_scope,    # noqa: PLC0415
                                   granted_connections)

    # The lexicon first, with no catalogue: most questions are decided by words alone, and the
    # catalogue (which measures every facts table) is built only when it can matter.
    q = _norm(question)
    names = (lambda text: _names_a_record(app, text))  # noqa: E731 - r1 #8
    pre = {**route_by_rules(question, names_record=names), "decided_by": "rules"}
    if pre["route"] == ROUTE_GRAPH and not (
            pre["rule"] == "lookup" and (_DOC_WORDS.search(q) or _PERIOD.search(q) or re.search(
                r"\b(?:which|all|every|list|show)\b", q))):
        # decided by words alone, and no catalogue table could change it
        return {"route": pre}
    granted = granted_connections(principal)
    if granted is _NOT_GRANTED:
        # Nothing could be delegated for this caller, so nothing is measured or asked: the graph
        # answers, and the route says why when the words alone asked for delegation.
        route = {**pre, "route": ROUTE_GRAPH}
        if pre["route"] != ROUTE_GRAPH:
            route["delegation_refused"] = {
                "code": "FEDERATED_NOT_GRANTED",
                "message": f"principal {principal.principal_id!r} holds no federated read grant "
                           f"(configured by the operator, never by a request), so this question "
                           f"was answered from the graph instead of being delegated"}
        return {"route": route}
    try:
        svc = _service(app)
    except Exception:                                                # noqa: BLE001
        # no exception text in a public payload (CONS standard 4): a fixed message, and the
        # details in the server log under an opaque reference
        ref = _error_ref()
        log.exception("federated service unavailable for /brain (error_ref %s)", ref)
        return {"route": {**pre, "route": ROUTE_GRAPH,
                          "delegation_unavailable": "the delegated query service could not be "
                                                    "started; the graph path answered",
                          "error_ref": ref}}
    decision = route_question(question, catalogue=svc.catalogue, router=router,
                              names_record=names)
    if getattr(svc.catalogue, "refreshing", None) and decision["route"] != ROUTE_FEDERATED:
        # E2E4 Q-01: a facts store exists but could not be read, so its tables are missing from
        # the catalogue this route was decided over; the route says so instead of implying the
        # tables do not exist
        decision = {**decision, "catalogue_refreshing": svc.refreshing_note()}
    if decision["route"] != ROUTE_FEDERATED:
        return {"route": decision}

    scope = admitted_scope(granted, None)

    from databridge_federated import CatalogueAdmission              # noqa: PLC0415

    from .trace_recorder import recorder_for                          # noqa: PLC0415
    trace = recorder_for(app).begin(surface="brain", agent="federated",
                                    owner=principal.principal_id)
    request_id = getattr(trace, "request_id", None)
    try:
        ans = svc.answer(question, admission=CatalogueAdmission(
            principal.principal_id, set(scope) if scope is not None else None), trace=trace)
        answer = ans.model_dump(mode="json")
    except Exception:                                                # noqa: BLE001
        ref = _error_ref()
        log.exception("delegated /brain question failed (error_ref %s)", ref)
        return {"route": {**decision, "route": ROUTE_GRAPH,
                          "delegation_failed": "the delegated query failed; the graph path "
                                               "answered",
                          "error_ref": ref}}
    cov = answer.get("coverage") or {}
    read_nothing = not (cov.get("searched") or cov.get("skipped"))
    if not (answer.get("table") or {}).get("rows") and read_nothing:
        if (answer.get("questions") and decision.get("decided_by") == "rules"
                and decision.get("rule") in AGGREGATE_RULES):
            # W-03: an aggregate the rules routed, and the planning model asked instead of
            # reading. The graph computes no aggregate, so its narration could only say the data
            # is missing; the planner's question is returned instead.
            return {"route": decision, "payload": clarification_payload(
                answer, decision, request_id=request_id)}
        # The planner asked instead of guessing (or nothing could be planned): nothing was read,
        # so the graph path answers what it can, and the attempt travels with it.
        return {"route": {**decision, "route": ROUTE_GRAPH, "fallback_from": ROUTE_FEDERATED},
                "attempt": {"run_id": answer.get("run_id"), "status": answer.get("status"),
                            "questions": list(answer.get("questions") or []),
                            "trace_request_id": request_id,
                            "activity_url": activity_url(answer.get("run_id"), request_id)}}
    return {"route": decision, "payload": federated_payload(answer, decision,
                                                            request_id=request_id)}


__all__ = ["route_by_rules", "route_question", "delegate", "federated_payload",
           "clarification_payload", "AGGREGATE_RULES",
           "coverage_summary", "candidate_flags", "activity_url", "RouteDecision",
           "ROUTE_FEDERATED", "ROUTE_GRAPH", "ROUTE_UNDECIDED", "ROUTER_TASK_CLASS"]
