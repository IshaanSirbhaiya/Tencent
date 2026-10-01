"""The planner's turn shape, and the answer contract (P15) applied to what it returns.

The model proposes claims, each citing pointer refs (``p1`` …) the tool layer issued. This module
decides — deterministically, with no model — which claims are SUPPORTED:

* a claim with no citation is ``unsupported`` (``no_citation``);
* a claim citing a ref the run never issued is ``unsupported`` (``unknown_citation``) — a ref the
  model invented is not a pointer;
* a claim stating a number that appears in none of its cited evidence is ``unsupported``
  (``number_not_in_cited_evidence``) — the grounding wall's rule, applied to answers.

Clarifications get the same check (R-017 t02 finding 6): a clarification may cite refs; one that
states a number its cited evidence does not hold, or cites a ref the run never issued, is listed
in ``unsupported_claims`` (``field: clarification``) and flagged in the markdown; one that cites
nothing is shown as an *uncited interpretation*, never as a finding.

Numbers are compared by VALUE, not by spelling (E2E F-06): ``12,750``, ``12750.00`` and ``12750``
are one number, as are the ``09`` of ``2026-09-15`` and the ``9`` of "9 September"; ``12751`` is
still a different number and still unsupported.

A claim that says a declared action HAPPENED ("I have saved the draft INV-7.md") is checked against
what the run's action log says happened to it (`check_action_claims`, E2E F-07): a refused, failed
or still-proposed action stated as done is ``unsupported`` (``action_not_executed``).

Unsupported claims are FLAGGED, never silently dropped: they stay in the markdown, marked, and are
listed in ``answer.unsupported_claims``. Every planner string is rendered on ONE line, so a newline
cannot start an unflagged bullet (finding 10). A claim that cites a subject-kind (person) pointer
is marked a CANDIDATE on its own line (finding 5) — the agent can find a person, never decide who
they are. Planner and source text is ESCAPED before it is rendered, so a literal ``[p7]`` or
``**UNSUPPORTED**`` inside it can neither forge a citation nor a flag; the answer also carries an
explicit ``citation_refs`` list (``{ref, pointer}``) so no reader has to pair refs with pointers by
their order. The markdown also carries the question verbatim, reduced coverage at the top, a
clarification block straight after the answer, and a measured audit line.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

from .tools import PointerRegistry


# --------------------------------------------------------------------------------------------
# what the planner returns each turn (harness structured output, task_class "agent")
# --------------------------------------------------------------------------------------------
class ToolCallRequest(BaseModel):
    tool: str = Field(..., description="a tool name exactly as listed, e.g. graph.search")
    args: dict[str, Any] = Field(default_factory=dict)


_Scalar = Union[str, int, float]


class Derivation(BaseModel):
    """A value COMPUTED from evidence, with its derivation (E2E run 2 N-06 / N-13): the runtime
    replays ``rule`` over ``inputs`` (each found in the cited evidence or the run context) and
    accepts ``value`` only when the replay gives exactly it. See `derive.RULES`."""

    value: _Scalar = Field(..., description="the computed value, exactly as written")
    rule: str = Field(..., description="sum | difference | count | days_between | add_days | "
                                       "concat")
    inputs: list[_Scalar] = Field(default_factory=list,
                                  description="the values it is computed from, each as it "
                                              "appears in the cited evidence or run context")
    args: dict[str, _Scalar] = Field(default_factory=dict,
                                     description="add_days: {days}; concat: {sep}")


class ActionRequest(BaseModel):
    name: str = Field(..., description="a declared action name")
    args: dict[str, Any] = Field(default_factory=dict)
    derived_from: list[str] = Field(default_factory=list,
                                    description="pointer refs (p1, p2 …) this action is based on")
    derivations: list[Derivation] = Field(
        default_factory=list, description="every computed value in args, with its derivation")


#: What can have become of a requested action — the action log's own statuses (R-021 #11).
ACTION_REPORT_STATUSES = ("executed", "proposed", "execution_failed", "refused")


class ActionReport(BaseModel):
    """A STRUCTURED statement about what happened to one action (R-021 #11). Checked against the
    run's action log directly — never inferred from the wording of the claim."""

    action_id: Optional[str] = Field(None, description="the action id an observation returned "
                                                       "(a1, a2 …)")
    name: Optional[str] = Field(None, description="the action name, when it has no id (refused)")
    status: str = Field(..., description="executed | proposed | execution_failed | refused")


class Claim(BaseModel):
    text: str
    citations: list[str] = Field(default_factory=list, description="pointer refs, e.g. ['p1']")
    actions: list[ActionReport] = Field(
        default_factory=list, description="every action this claim reports on, with the status "
                                          "the claim states for it")
    derivations: list[Derivation] = Field(
        default_factory=list, description="every computed value the claim states")


class Clarification(BaseModel):
    assumption: str = Field(..., description="the reading the answer used")
    alternatives: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list,
                                 description="pointer refs any fact in this clarification rests on")
    actions: list[ActionReport] = Field(default_factory=list)
    derivations: list[Derivation] = Field(default_factory=list)


#: What a run's answer amounts to (E2E run 2 N-12; packs/workspace/evals/agent_contract.jsonl
#: ``outcome.reason_code`` plus ``answered`` and the runtime's own ``no_answer``).
OUTCOME_REASON_CODES = ("answered", "value_absent", "identity_candidate", "withheld_by_screen",
                        "source_not_connected")


class OutcomeReport(BaseModel):
    """The planner's STRUCTURED statement of what its answer amounts to. Every part is checked
    by the runtime against structure (the withheld register, the question, the candidates the
    answer cites) before it reaches the run record's ``outcome``."""

    reason_code: Optional[str] = Field(None, description=" | ".join(OUTCOME_REASON_CODES))
    withheld: list[str] = Field(default_factory=list,
                                description="withheld items (as listed) the question needed")
    not_connected: list[str] = Field(default_factory=list,
                                     description="sources the question names that no tool of "
                                                 "yours reaches")


class FinalAnswer(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    outcome: Optional[OutcomeReport] = None


class PlannerTurn(BaseModel):
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    actions: list[ActionRequest] = Field(default_factory=list)
    final: Optional[FinalAnswer] = None


# --------------------------------------------------------------------------------------------
# checking and rendering
# --------------------------------------------------------------------------------------------
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _number_value(token: str) -> str:
    """One number token as its VALUE: ``12,750.00`` -> ``12750``; ``09`` -> ``9``.

    The SIGN is part of the value (R-021 #4): ``-500`` and ``500`` are different numbers. A date's
    separators never reach here as signs — `_NUMBER` does not start a token at a ``-`` that
    follows a digit or letter, so ``2026-09-15`` is ``2026``, ``09``, ``15``. ``-0`` is ``0``."""
    t = token.replace(",", "")
    try:
        d = Decimal(t).normalize()
    except (InvalidOperation, ValueError):
        return t
    if d == 0:
        d = Decimal(0)
    return format(d, "f")


def _numbers(text: str) -> set[str]:
    return {_number_value(m.group(0)) for m in _NUMBER.finditer(text or "")}


#: Characters that carry markdown meaning in the rendered answer: a citation (``[p1]``), a flag
#: (``**UNSUPPORTED**``), code. Escaped in every string the planner or a source supplied.
_MD_SPECIAL = re.compile(r"([\\*\[\]`])")


def _md(text: str) -> str:
    """Untrusted text for ONE markdown line: newlines folded, markdown punctuation escaped."""
    return _MD_SPECIAL.sub(r"\\\1", _one_line(text))


def _one_line(text: str) -> str:
    """Planner text for a single markdown line: newlines folded to `` / `` so nothing after them
    can render as a separate, unflagged bullet or heading."""
    return " / ".join(part.strip() for part in re.split(r"[\r\n  ]+", str(text or ""))
                      if part.strip())


@dataclass
class CheckedClaim:
    text: str
    refs: list[str]
    supported: bool
    reason: str = ""
    subject_candidate: bool = False
    action_reports: list = field(default_factory=list)
    #: ``[{value, derivation, inputs, pointer}]`` — computed values that replayed (N-06 / N-13)
    derivations: list = field(default_factory=list)
    #: ``[{value, subject_kind, refs}]`` — the candidates (people) this text names or cites
    candidates: list = field(default_factory=list)

    @property
    def requires_human_selection(self) -> bool:
        """A claim about a candidate (person) needs a person to select who it is — supported or
        not (FED answer contract item 2)."""
        return self.subject_candidate


@dataclass
class CheckedClarification:
    assumption: str
    alternatives: list[str]
    refs: list[str]
    supported: bool
    reason: str = ""
    subject_candidate: bool = False
    action_reports: list = field(default_factory=list)
    derivations: list = field(default_factory=list)
    candidates: list = field(default_factory=list)


@dataclass
class CheckedAnswer:
    claims: list[CheckedClaim]
    clarifications: list[CheckedClarification]
    citations: list[dict]                      # SourcePointer JSON, each cited pointer once
    unsupported: list[dict]
    cited_sources: list[str]
    subject_candidates: bool = False
    extra: dict = field(default_factory=dict)
    citation_refs: list[dict] = field(default_factory=list)
    """``[{ref, pointer, subject_candidate}]`` — each cited ref paired with ITS pointer, in the
    order ``citations`` lists them, and whether it grounds a CANDIDATE (a person a human must
    select). Readers pair by this, never by position in a rendered list."""


def _refs(citations: list[str]) -> list[str]:
    return [str(r).strip().strip("[]^") for r in citations if str(r).strip()]


#: The pointer locator fields that say WHERE a value lives (the row a cell is in, its table and
#: column, a sheet / cell, a file). They are part of what a pointer grounds: a cell of row
#: ``C-1001`` grounds the id ``C-1001`` even when its own text is ``city: Leeds`` (N-13).
_LOCATOR_FIELDS = ("row_id", "table", "column", "sheet", "cell", "file_path")


def _evidence_text(e: Any) -> str:
    """Everything one piece of evidence grounds: its text, then its pointer's locator."""
    text = str(getattr(e, "text", "") or "")
    p = getattr(e, "pointer", None)
    loc = [str(v) for v in (getattr(p, f, None) for f in _LOCATOR_FIELDS) if v not in (None, "")] \
        if p is not None else []
    return f"{text}\n{' '.join(loc)}" if loc else text


def _fold(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _value_in(value: str, text: str) -> bool:
    """``value`` is in ``text``: a number by VALUE, anything else as a whole token."""
    v = str(value).strip()
    if not v:
        return False
    if _NUMBER.fullmatch(v):
        return _number_value(v) in _numbers(text)
    return _in_pool(v, _fold(text))


@dataclass
class VerifiedDerivation:
    value: str
    derivation: str
    inputs: list
    pointer: Any                      # a `derived` SourcePointer over the inputs' evidence

    def to_json(self) -> dict:
        return {"value": self.value, "derivation": self.derivation, "inputs": list(self.inputs),
                "pointer": self.pointer.model_dump(mode="json")}


def _iso_date(value: str) -> bool:
    import datetime as _dt                                       # noqa: PLC0415
    try:
        _dt.date.fromisoformat(str(value).strip())
        return len(str(value).strip()) == 10
    except ValueError:
        return False


def verify_derivations(derivations: Any, evidence: list, *, literals: str = "",
                       clock: Any = None) -> tuple[list[VerifiedDerivation], list[str]]:
    """Replay each declared derivation (the derived-pointer rule, `derive`). ``(accepted,
    problems)``. An input is grounded when the cited evidence holds it (whole token / number by
    value) — or ``literals`` (the run context; the owner's declared text) or an earlier accepted
    derivation does. A derivation is accepted only when at least one input comes from cited
    evidence and the replay gives EXACTLY the declared value; it is then cited by a ``derived``
    pointer whose parents are those inputs' evidence pointers.

    ``clock`` (E2E run 4 Q-08) is the run's own context as captured evidence (``.text`` holding
    the run date the planner was told, ``.pointer`` its capture). A DATE input that is the run
    date is then a parent too, and a date rule (`derive.CLOCK_RULES`) over the run date alone -
    "today plus the 30 days the instruction states" - is accepted: the window bound is replayable
    from the captured clock and the declared arguments. A number rule never rests on the clock."""
    from databridge_core import SourcePointer                    # noqa: PLC0415
    from databridge_core.provenance import Trust                 # noqa: PLC0415

    from .derive import CLOCK_RULES, DerivationError, canonical, replay, same_value  # noqa: PLC0415

    clock_text = str(getattr(clock, "text", "") or "") if clock is not None else ""

    ok: list[VerifiedDerivation] = []
    problems: list[str] = []
    for d in derivations or []:
        rule = str(getattr(d, "rule", "") or "").strip().split("@")[0]
        value = str(getattr(d, "value", "")).strip()
        inputs = [str(x).strip() for x in (getattr(d, "inputs", None) or [])]
        args = {str(k).strip(): str(v).strip()
                for k, v in (getattr(d, "args", None) or {}).items()}
        parents: list = []
        on_clock: list = []
        missing: list[str] = []
        checked = [("input", x) for x in inputs] + [(f"args.{k}", v) for k, v in args.items()
                                                    if k != "sep"]
        for what, x in checked:
            hits = [e for e in evidence if _value_in(x, _evidence_text(e))]
            if hits:
                if what == "input":
                    parents += [getattr(e, "pointer", None) for e in hits]
                continue
            prior = next((p for p in ok if same_value(p.value, x)), None)
            if prior is not None:
                if what == "input":
                    parents.append(prior.pointer)
                continue
            if what == "input" and clock is not None and rule in CLOCK_RULES and \
                    _iso_date(x) and _value_in(x, clock_text):
                ptr = getattr(clock, "pointer", None)   # captured on first use
                if ptr is not None:
                    on_clock.append(ptr)                # Q-08: the run's own captured date
                    continue
            if not _value_in(x, literals):
                missing.append(x if what == "input" else f"{what}={x}")
        if missing:
            problems.append(f"{value!r}: {missing} appear in none of the cited evidence")
            continue
        parents = list({json.dumps(p.model_dump(mode="json"), sort_keys=True): p
                        for p in [*parents, *on_clock] if p is not None}.values())
        if not parents:
            problems.append(f"{value!r}: none of its inputs comes from the cited evidence; a "
                            f"derived value is computed from cited values")
            continue
        try:
            got = replay(rule, inputs, args)
        except DerivationError as e:
            problems.append(f"{value!r}: {e}")
            continue
        call = canonical(rule, args)
        if not same_value(got, value):
            problems.append(f"{value!r}: {call} over {inputs} gives {got!r}")
            continue
        sources = {getattr(p, "source_id", None) for p in parents}
        ptr = SourcePointer(source_id=sources.pop() if len(sources) == 1 else "derived",
                            kind="derived", derived_from=parents, derivation=call,
                            trust=Trust.UNTRUSTED)
        ok.append(VerifiedDerivation(value=value, derivation=call, inputs=inputs, pointer=ptr))
    return ok, problems


def _question_scope(question: str) -> str:
    """The question's scoping dates and identifiers (``databridge_federated.scope_literals``,
    imported lazily: that package builds on this one). Absent: nothing from the question grounds
    (fail closed)."""
    if not question:
        return ""
    try:
        from databridge_federated import scope_literals  # type: ignore  # noqa: PLC0415
    except Exception:                                        # noqa: BLE001 - fail closed
        return ""
    try:
        return str(scope_literals(question, None) or "")
    except Exception:                                        # noqa: BLE001 - fail closed
        return ""


def _rows_of(evidence: list, registry: PointerRegistry) -> list:
    """Q-05: the source rows behind each cited AGGREGATE result (`Evidence.row_refs`), not already
    cited. Citing a grouped count cites the rows it counted: they ground the claim and they are
    listed in its citations."""
    have = {getattr(e, "ref", None) for e in evidence}
    out: list = []
    for e in evidence:
        for r in getattr(e, "row_refs", ()) or ():
            if r in have:
                continue
            ev = registry.get(r)
            if ev is not None:
                have.add(r)
                out.append(ev)
    return out


def _check(text: str, refs: list[str], registry: PointerRegistry, *,
           require_citation: bool, literals: str = "", owner_text: str = "",
           derivations: Any = (), question: str = "", clock: Any = None) -> tuple[str, list, list]:
    """``(reason, evidence, accepted derivations)``; ``reason`` is empty when the text is
    supported. A number may come from the cited evidence (its text and its pointer's locator),
    the run context (``literals``: run id, date, the withheld register), or a declared derivation
    that replays (N-13)."""
    # P-12: the dates and identifiers that SCOPE the question (a date window's 1, 2026, 31) are
    # the user's own values; restating them is not a claim about the data. One rule, shared with
    # the federated narrator (`databridge_federated.scope_literals`): a BARE number in the question
    # never grounds — it could be the very answer.
    lit_numbers = _numbers(literals) | _numbers(_question_scope(question))
    if not refs:
        if require_citation:
            return "no_citation", [], []
        missing = sorted(_numbers(text) - lit_numbers)
        return (f"number_not_in_cited_evidence: {', '.join(missing)}" if missing else ""), [], []
    evidence = [registry.get(r) for r in refs]
    if any(e is None for e in evidence):
        return "unknown_citation", [], []
    evidence = evidence + _rows_of(evidence, registry)       # Q-05: an aggregate's own rows
    verified, problems = verify_derivations(derivations, evidence,
                                            literals=f"{literals}\n{owner_text}", clock=clock)
    # P-12: an id in the arguments of the query that produced a cited result (``customer_id =
    # 'C-1003'`` returned no rows) is what that result is ABOUT — the claim may name it.
    pool = set().union(*(_numbers(_evidence_text(e)) for e in evidence)) | lit_numbers
    for e in evidence:
        pool |= _numbers(" ".join(getattr(e, "query_ids", ()) or ()))
    for v in verified:
        pool |= _numbers(" ".join([v.value, *v.inputs, v.derivation]))
    missing = sorted(_numbers(text) - pool)
    why = []
    if problems:
        why.append(f"derivation_not_reproducible: {'; '.join(problems)}")
    if missing:
        why.append(f"number_not_in_cited_evidence: {', '.join(missing)}")
    return "; ".join(why), evidence, verified


def _named_candidates(text: str, registry: PointerRegistry) -> dict[str, set[str]]:
    """Identifiers of DISCLOSED candidates (a person's label, canonical id) that ``text`` names,
    each with the refs that ground that candidate."""
    find = getattr(registry, "candidates", None)
    if find is None:
        return {}
    low = " ".join(str(text or "").lower().split())
    out: dict[str, set[str]] = {}
    for ev in find():
        for ident in ev.identifiers:
            if len(ident) >= 3 and _in_pool(ident, low):
                out.setdefault(ident, set()).add(ev.ref)
    return out


def _kind_of(registry: PointerRegistry, refs: Any) -> Optional[str]:
    for r in sorted(refs):
        ev = registry.get(r)
        k = getattr(ev, "subject_kind", None) if ev is not None else None
        if k:
            return str(k)
    return None


def _candidate_check(text: str, refs: list[str], registry: PointerRegistry,
                     reason: str, evidence: list) -> tuple[str, bool, list]:
    """``(reason, candidate, candidates)``. FED answer contract: a text that NAMES a candidate
    must cite that candidate (item 4) — a ref of the candidate itself, or cited evidence that
    holds the candidate's name or id (the person's own row, N-13). Naming a person on the
    strength of, say, an organisation's row is an identity assertion with no candidate evidence.
    ``candidate`` is set whether or not the text is supported (item 1)."""
    named = _named_candidates(text, registry)
    by_text = {e.ref: _fold(_evidence_text(e)) for e in evidence}
    cands: list[dict] = []
    uncited = []
    for ident, grounding in sorted(named.items()):
        own = sorted(set(refs) & grounding) or sorted(r for r, t in by_text.items()
                                                      if _in_pool(ident, t))
        if not own:
            uncited.append(ident)
            continue
        cands.append({"value": ident, "subject_kind": _kind_of(registry, grounding | set(own)),
                      "refs": own})
    for e in evidence:                              # a cited candidate pointer, named or not
        if getattr(e, "subject_candidate", False) and getattr(e, "identifiers", ()) and \
                not any(e.ref in c["refs"] for c in cands):
            cands.append({"value": e.identifiers[0],
                          "subject_kind": getattr(e, "subject_kind", None), "refs": [e.ref]})
    if uncited:
        own = f"candidate_identity_uncited: {', '.join(uncited)}"
        reason = f"{own}; {reason}" if reason else own
    candidate = bool(named) or any(getattr(e, "subject_candidate", False) for e in evidence)
    return reason, candidate, cands


def _checked_text(c: Any, text: str, registry: PointerRegistry, *, require_citation: bool,
                  literals: str, owner_text: str, question: str = "", clock: Any = None):
    refs = _refs(c.citations)
    reason, evidence, verified = _check(text, refs, registry, require_citation=require_citation,
                                        literals=literals, owner_text=owner_text,
                                        derivations=getattr(c, "derivations", ()),
                                        question=question, clock=clock)
    reason, candidate, cands = _candidate_check(text, refs, registry, reason, evidence)
    return refs, reason, evidence, verified, candidate, cands


#: A sentence ends at . ! or ? followed by space and a capital, digit, quote, bracket or markup -
#: never inside a number (``15300.00``) or a dotted name (``public.invoices``).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(*\[])")


def _sentences(text: str) -> list[str]:
    return [t.strip() for t in _SENTENCE_END.split(str(text or "")) if t.strip()]


def check_answer(final: Optional[FinalAnswer], registry: PointerRegistry, *,
                 literals: str = "", owner_text: str = "", question: str = "",
                 clock: Any = None, by_sentence: bool = False) -> CheckedAnswer:
    """Check every claim and clarification (P15). ``literals`` is the run context the runtime
    itself gave the planner (run id, agent id, date, the withheld register's names and rule
    ids); ``owner_text`` is the owner's declared text, which may ground a derivation's INPUT
    only — never a stated value directly. ``question`` is the user's question: the dates and ids
    that scope it may be restated, never a bare number it states (P-12). An id in the arguments of the query behind a cited result
    (`Evidence.query_ids`) grounds a claim citing that result.

    ``by_sentence`` (Q-05): a flagged claim of several sentences is judged sentence by sentence and,
    when only some fail, returned as one claim per sentence. Off by default: a caller that pairs
    its own per-claim data with ``claims`` BY POSITION (the federated narrator's candidate flags)
    must not see the list change length."""
    claims: list[CheckedClaim] = []
    cited_keys: dict[str, dict] = {}
    unsupported: list[dict] = []
    subject = False
    for c in (final.claims if final else []):
        refs, reason, evidence, verified, candidate, cands = _checked_text(
            c, c.text, registry, require_citation=True, literals=literals, owner_text=owner_text,
            question=question, clock=clock)
        pieces = [(c.text, refs, reason, evidence, verified, candidate, cands)]
        if by_sentence and reason and not c.actions:
            # Q-05: a claim the planner wrote as several sentences is judged sentence by
            # sentence, each against the claim's own citations - so a correct, cited statement is
            # not flagged because a sentence beside it adds an uncited one. Only a MIXED result is
            # split; the same evidence decides every piece, so nothing is supported here that the
            # whole-claim check would refuse for a reason a piece also carries.
            split = [(t, *_checked_text(c, t, registry, require_citation=True,
                                        literals=literals, owner_text=owner_text,
                                        question=question, clock=clock))
                     for t in _sentences(c.text)]
            if len(split) > 1 and any(not p[2] for p in split) and any(p[2] for p in split):
                pieces = split
        for text, refs, reason, evidence, verified, candidate, cands in pieces:
            if not reason:
                for e in evidence:
                    cited_keys.setdefault(e.ref, e.pointer.model_dump(mode="json"))
            subject = subject or candidate
            claims.append(CheckedClaim(text=text, refs=refs, supported=not reason, reason=reason,
                                       subject_candidate=candidate,
                                       action_reports=[a.model_dump() for a in c.actions],
                                       derivations=[v.to_json() for v in verified],
                                       candidates=cands))
            if reason:
                unsupported.append({"text": text, "reason": reason, "citations": refs,
                                    "requires_human_selection": candidate})
    clars: list[CheckedClarification] = []
    for cl in (final.clarifications if final else []):
        text = " ".join([cl.assumption, *cl.alternatives])
        refs, reason, evidence, verified, candidate, cands = _checked_text(
            cl, text, registry, require_citation=False, literals=literals, owner_text=owner_text,
            question=question, clock=clock)
        if not reason:
            for e in evidence:
                cited_keys.setdefault(e.ref, e.pointer.model_dump(mode="json"))
        # R-021 #7: a clarification resting on a person pointer is a CANDIDATE reading too.
        subject = subject or candidate
        clars.append(CheckedClarification(assumption=cl.assumption,
                                          alternatives=list(cl.alternatives), refs=refs,
                                          supported=not reason, reason=reason,
                                          subject_candidate=candidate,
                                          action_reports=[a.model_dump() for a in cl.actions],
                                          derivations=[v.to_json() for v in verified],
                                          candidates=cands))
        if reason:
            unsupported.append({"text": text, "reason": reason, "citations": refs,
                                "field": "clarification", "requires_human_selection": candidate})
    order = sorted(cited_keys, key=lambda r: int(r[1:]))
    citations = [cited_keys[r] for r in order]
    sources = sorted({p["source_id"] for p in citations})
    cited_refs = {x for c in claims for x in c.refs} | {x for c in clars for x in c.refs}
    #: Q-05: the rows behind each cited aggregate - cited with it wherever it stays cited
    rows_by_ref = {r: [x.ref for x in _rows_of([e], registry)] for r in cited_refs
                   if (e := registry.get(r)) is not None and getattr(e, "row_refs", ())}
    cited_refs |= {x for rows in rows_by_ref.values() for x in rows}
    candidate_by_ref = {r: bool(getattr(registry.get(r), "subject_candidate", False))
                        for r in cited_refs if registry.get(r) is not None}
    return CheckedAnswer(claims=claims, clarifications=clars, citations=citations,
                         unsupported=unsupported, cited_sources=sources,
                         subject_candidates=subject,
                         citation_refs=[{"ref": r, "pointer": cited_keys[r],
                                         "subject_candidate": candidate_by_ref.get(r, False)}
                                        for r in order],
                         extra={"pointer_by_ref": {
                             e.ref: e.pointer.model_dump(mode="json")
                             for r in cited_refs if (e := registry.get(r)) is not None},
                             "candidate_by_ref": candidate_by_ref,
                             "rows_by_ref": rows_by_ref})


# --------------------------------------------------------------------------------------------
# claims about ACTIONS are checked against the action log (E2E F-07)
# --------------------------------------------------------------------------------------------
#: A clause that says something was DONE (or now EXISTS) ...
_DONE = re.compile(r"\b(saved|wrote|written|created|logged|sent|inserted|recorded|drafted|filed|"
                   r"updated|added|posted|submitted|executed|completed|stored|uploaded|emailed|"
                   r"notified|booked|scheduled|done|persisted|committed|generated|produced|"
                   r"delivered|placed|applied|put|made|dispatched|exists?|finished|wrote|"
                   r"registered|entered|issued|filed|archived|published|shared)\b", re.I)
#: ... unless a negation or a modal comes right BEFORE that verb ("was not saved", "will be
#: saved", "could not be saved") ...
_NEGATION_BEFORE = re.compile(r"\b(not|never|no|[a-z]+n't|cannot|can't|couldn't|unable|will|would|"
                              r"shall|should|could|might|may|if|once|until|unless|after|when|"
                              r"yet|to\s+be)\b", re.I)
#: ... or the clause says outright that it is waiting, refused or failed.
_PENDING = re.compile(r"\b(refused|failed|pending|awaiting|await|awaits|waiting|proposed|"
                      r"approval|unapproved|rejected)\b", re.I)
#: Clauses: sentence ends, semicolons, newlines, and the conjunctions that join two statements
#: ("I saved X and will notify you" is two clauses — R-021 #11).
_CLAUSE = re.compile(r"[.;!?](?=\s|$)|\n|,|\b(?:and|but|then|while|whereas|also)\b", re.I)
_PATHISH = re.compile(r"[/\\]|\.[A-Za-z][A-Za-z0-9]{0,4}$")     # a path, or a file extension


def _asserts_done(clause: str) -> bool:
    """The clause states that something HAPPENED: a done-verb with no negation / modal in the four
    words before it, and no waiting / refused / failed word anywhere in the clause."""
    if _PENDING.search(clause):
        return False
    for m in _DONE.finditer(clause):
        before = " ".join(re.findall(r"[A-Za-z']+", clause[:m.start()])[-4:])
        if not _NEGATION_BEFORE.search(before):
            return True
    return False


def _stem(word: str) -> str:
    w = word.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[A-Za-z0-9]+", text or "")]


def _identifiers(args: Any) -> set[str]:
    """Path-like argument values (a file an action writes): the full value and its basename."""
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str) and 3 <= len(v) <= 300 and "\n" not in v and \
                " " not in v.strip() and _PATHISH.search(v.strip()):
            val = v.strip()
            out.add(val.lower())
            base = re.split(r"[/\\]", val)[-1]
            if len(base) >= 3:
                out.add(base.lower())

    walk(args)
    return out


def _mentions_action_name(clause_words: list[str], name: str) -> bool:
    """Every word of the action's name after its verb (``save_followup_draft`` -> followup,
    draft) appears in the clause — adjacent clause words are also joined (``follow-up`` ->
    ``followup``)."""
    parts = [_stem(w) for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    need = [w for w in parts[1:] if len(w) >= 3] or [w for w in parts if len(w) >= 3]
    if not need:
        return False
    have = set(clause_words) | {a + b for a, b in zip(clause_words, clause_words[1:])}
    return all(w in have for w in need)


def _report_problems(reports: list, outcomes: list[dict]) -> list[str]:
    """Structured action reports vs the action log (R-021 #11): each report must name an action
    the run actually handled, with the status the log records for it."""
    out: list[str] = []
    for r in reports or []:
        aid, name, said = r.get("action_id"), r.get("name"), str(r.get("status") or "")
        if aid:
            hits = [o for o in outcomes if o.get("action_id") == aid]
        else:
            hits = [o for o in outcomes if name and o.get("name") == name]
        if not hits:
            out.append(f"{aid or name!r} is not an action this run handled")
            continue
        wrong = [o for o in hits if o.get("status") != said]
        if wrong:
            out.append("; ".join(f"{o.get('action_id') or o.get('name')} is {o.get('status')}, "
                                 f"not {said}" for o in wrong))
    return out


def check_action_claims(checked: CheckedAnswer, outcomes: list[dict]) -> CheckedAnswer:
    """Flag every claim or clarification whose statement about an action the run's own record
    contradicts (E2E F-07, R-021 #11).

    ``outcomes`` is one entry per action the planner asked for: ``{name, args, status, detail,
    action_id?}`` with status ``executed`` | ``proposed`` | ``execution_failed`` | ``refused``.

    1. **Structured** — a claim's ``actions`` reports (``{action_id | name, status}``) are compared
       with the log directly; an unknown action or a different status is ``action_status_mismatch``.
    2. **Prose backstop** — the model may still say it in words. A clause asserts completion when
       a done-verb has no negation or modal just before it and the clause says nothing is pending
       (`_asserts_done`); it concerns an outcome when it names the file the action writes (a
       path-like argument) or every word of the action's name. Anything but ``executed`` makes it
       ``action_not_executed``.

    Mutates and returns ``checked``."""
    idents = [(o, _identifiers(o.get("args") or {})) for o in outcomes]
    changed = False
    items = [(c, c.text or "", "claim") for c in checked.claims] + \
        [(cl, " ".join([cl.assumption, *cl.alternatives]), "clarification")
         for cl in checked.clarifications]
    for item, text, kind in items:
        why: list[str] = []
        mismatch = _report_problems(getattr(item, "action_reports", []), outcomes)
        if mismatch:
            why.append(f"action_status_mismatch: {'; '.join(mismatch)}")
        bad: list[dict] = []
        if outcomes:
            for clause in _CLAUSE.split(text):
                if not clause or not _asserts_done(clause):
                    continue
                low = clause.lower()
                by_file = [o for o, ids in idents if any(i in low for i in ids)]
                words = _words(clause)
                matched = by_file or [o for o in outcomes
                                      if _mentions_action_name(words, str(o.get("name") or ""))]
                bad += [o for o in matched if o.get("status") != "executed" and o not in bad]
        if bad:
            why.append("action_not_executed: " + "; ".join(
                f"{o.get('name')} was {o.get('status')}"
                + (f" ({o.get('detail')})" if o.get("detail") else "") for o in bad))
        if not why:
            continue
        reason = "; ".join(why)
        if item.supported:
            item.supported = False
            item.reason = reason
            entry = {"text": text, "reason": reason, "citations": item.refs,
                     "requires_human_selection": bool(item.subject_candidate)}
            if kind == "clarification":
                entry["field"] = "clarification"
            checked.unsupported.append(entry)
        else:
            item.reason = f"{item.reason}; {reason}"
            for u in checked.unsupported:
                if u.get("text") == text and (u.get("field") == "clarification") == \
                        (kind == "clarification"):
                    u["reason"] = item.reason
        changed = True
    if changed:                              # a flagged claim's refs are no longer "cited"
        by_ref = checked.extra.get("pointer_by_ref") or {}
        keep = {r for c in checked.claims if c.supported for r in c.refs} | \
            {r for c in checked.clarifications if c.supported for r in c.refs}
        rows = checked.extra.get("rows_by_ref") or {}
        keep |= {x for r in list(keep) for x in rows.get(r, ())}       # Q-05
        order = sorted((r for r in keep if r in by_ref), key=lambda r: int(r[1:]))
        checked.citations = [by_ref[r] for r in order]
        flags = checked.extra.get("candidate_by_ref") or {}
        checked.citation_refs = [{"ref": r, "pointer": by_ref[r],
                                  "subject_candidate": bool(flags.get(r, False))} for r in order]
        checked.cited_sources = sorted({p["source_id"] for p in checked.citations})
    return checked


#: An identifier inside drafted text: letters/digits joined by ``-`` or ``_`` (``INV-1042``,
#: ``C_1002``) or a run of letters and digits (``C1002``) — anything carrying a digit and a letter.
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")
#: A single-quoted SQL / prose literal (``''`` is an escaped quote inside it).
_QUOTED = re.compile(r"'((?:[^']|'')*)'")
_EXT = re.compile(r"^(.+)\.([A-Za-z0-9]{1,5})$")


def _is_identifier(tok: str) -> bool:
    return any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)


def _in_pool(value: str, pool_lower: str) -> bool:
    """``value`` occurs in the pool as a whole token (case-insensitive, whitespace folded)."""
    v = " ".join(value.lower().split())
    if not v:
        return True
    return re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", pool_lower) is not None


def ungrounded_values(args: Any, evidence: list, *, literals: str = "",
                      derived: Any = ()) -> list[str]:
    """Values in an action's arguments that NONE of its cited evidence holds (R-017 t02 finding 4,
    widened by R-021 #3). ``literals`` is the other text a value may come from: what the agent's
    OWNER declared (its English, instructions, action descriptions — reviewed and pinned by the
    spec digest) and this run's context (its run id and date). Nothing else grounds a value.

    Every leaf is checked by its COMPLETE canonical value:

    * a number (anywhere, any spelling) by value, sign included;
    * an e-mail address;
    * a string with no whitespace (an id, a status, a name, a path, a filename) as a WHOLE token —
      a path segment by segment (its extension aside), so an invented folder is a guess too;
    * inside drafted text (a string with whitespace — a message body, a SQL statement): every
      single-quoted literal as a whole, and every identifier-like token (digits and letters,
      ``INV-1042``). The prose around them is drafting and is allowed;
    * dict KEYS are structure (argument / column names), not values.

    What is returned is recorded on the proposal: an automatic action carrying any of them is
    refused, and a ``human_required`` one cannot be approved (the gate in `runtime.approve`).

    Evidence grounds its text AND its pointer's locator (the row a cell is in). ``derived`` are
    values a declared derivation REPLAYED from this evidence (`verify_derivations`, N-06): they are
    grounded too, and nothing else computed is."""
    pool_text = "\n".join([*(_evidence_text(e) for e in evidence), literals or "",
                           *(str(v) for v in derived or ())])
    pool_lower = " ".join(pool_text.lower().split())
    pool_numbers = _numbers(pool_text)
    pool_emails = {m.lower() for m in _EMAIL.findall(pool_text)}
    out: list[str] = []

    def flag(v: str) -> None:
        if v not in out:
            out.append(v)

    def numbers_of(text: str) -> None:
        for n in sorted(_numbers(text) - pool_numbers):
            flag(n)

    def whole(v: str) -> None:
        """A whitespace-free value: grounded as a whole token, or (a path) segment by segment."""
        if _EMAIL.fullmatch(v):
            if v.lower() not in pool_emails:
                flag(v)
            return
        if _NUMBER.fullmatch(v):
            numbers_of(v)
            return
        if _in_pool(v, pool_lower):
            return
        if "/" in v or "\\" in v or _EXT.match(v):
            # a path segment by segment — and a bare file name like a path's last segment: its
            # stem must be grounded, its extension aside (N-06: `INV-2026-0057.md`)
            segs = [x for x in re.split(r"[/\\]", v) if x]
            if segs:
                m = _EXT.match(segs[-1])
                if m:
                    segs[-1] = m.group(1)
            if segs and all(_in_pool(x, pool_lower) or (_NUMBER.fullmatch(x) and
                                                        not (_numbers(x) - pool_numbers))
                            for x in segs):
                return
        flag(v)

    def prose(v: str) -> None:
        for m in _EMAIL.findall(v):
            if m.lower() not in pool_emails:
                flag(m)
        rest = _EMAIL.sub(" ", v)
        for lit in _QUOTED.findall(rest):
            lit = lit.replace("''", "'").strip()
            if not lit:
                continue
            if any(ch.isspace() for ch in lit):
                prose(lit)
            else:
                whole(lit)
        rest = _QUOTED.sub(" ", rest)
        numbers_of(rest)
        for tok in _TOKEN.findall(rest):
            if _is_identifier(tok) and not _NUMBER.fullmatch(tok) and \
                    not _in_pool(tok, pool_lower):
                flag(tok)

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif v is None or isinstance(v, bool):
            return
        elif isinstance(v, str):
            s2 = v.strip()
            if not s2:
                return
            if any(ch.isspace() for ch in s2):
                prose(s2)
            else:
                whole(s2)
        else:
            numbers_of(json.dumps(v, default=str))

    walk(args)
    return out


def _cite_label(registry: PointerRegistry, ref: str) -> str:
    ev = registry.get(ref)
    if ev is None:
        return f"[{_md(ref)}] (not a pointer this run issued)"
    p = ev.pointer
    where = p.document_id or p.table or p.file_path or p.message_id or ""
    # every part but the ref itself is SOURCE text (an id, a path, a locator): escaped
    parts = [f"[{_md(ref)}]", _md(p.source_id), _md(str(p.kind))]
    if where:
        parts.append(_md(str(where)))
    if p.column and p.row_id:
        parts.append(_md(f"{p.column} @ row {p.row_id}"))
    if p.char_start is not None and p.char_end is not None:
        parts.append(f"chars {p.char_start}-{p.char_end}")
    if ev.locator:
        parts.append(f"approx. {_md(ev.locator)}" if ev.approx else _md(str(ev.locator)))
    if p.capture_digest:
        parts.append(f"sha256:{p.capture_digest[:12]}...")
    return " | ".join(parts)


def render_markdown(*, heading: str, verbatim: str, checked: Optional[CheckedAnswer],
                    registry: PointerRegistry, coverage: dict, stop_reason: Optional[str],
                    audit: str, actions: list[dict], coverage_status: Optional[str] = None,
                    gathered: Optional[list[str]] = None) -> str:
    """``coverage_status`` (the run's structural outcome, N-12): ``complete`` shows what was
    skipped as NOTES (e.g. pointers of sources outside the agent's declared scope), ``reduced`` -
    or no status, the federated default - as reduced coverage. ``gathered`` (a run that stopped
    without an answer): what WAS read, so the partial result is said honestly, with no finding."""
    lines: list[str] = []
    reduced = coverage_status == "reduced" or (coverage_status is None and
                                               coverage.get("skipped"))
    if reduced:
        lines.append("> **Reduced coverage.** This answer may be incomplete: some sources or "
                     "tools were not used:")
    elif coverage.get("reasons"):
        lines.append("> **Coverage notes** (every declared source was read):")
    if reduced or coverage.get("reasons"):
        for reason in coverage.get("reasons") or []:
            lines.append(f"> - {_md(reason)}")
        lines.append("")
    lines.append(f"**{heading} (verbatim):** {_md(verbatim)}")
    lines.append("")
    if stop_reason:
        lines.append(f"**The run stopped before finishing:** {_md(stop_reason)}")
        lines.append("")
        if gathered:
            lines.append("**Read before it stopped** (what was found; nothing here is a "
                         "checked answer):")
            lines += [f"- {_md(g)}" for g in gathered]
            lines.append("")
    if checked is None or not checked.claims:
        lines.append("_No answer was produced._" if not stop_reason else
                     "_No answer was produced before the run stopped._")
    else:
        for c in checked.claims:
            refs = "".join(f"[{_md(r)}]" for r in c.refs)
            text = _md(c.text)
            if not c.supported:
                lines.append(f"- **UNSUPPORTED** ({_md(c.reason)}): {text} {refs}".rstrip())
            elif c.subject_candidate:
                lines.append(f"- **CANDIDATE (not a confirmed identity):** {text} {refs}".rstrip())
            else:
                lines.append(f"- {text} {refs}".rstrip())
    if checked is not None and checked.clarifications:
        lines.append("")
        lines.append("**Clarification: the data could not settle this:**")
        for cl in checked.clarifications:
            alts = "; ".join(_md(a) for a in cl.alternatives) if cl.alternatives \
                else "none stated"
            body = f"Assumed: {_md(cl.assumption)}. Alternatives: {alts}."
            refs = "".join(f"[{_md(r)}]" for r in cl.refs)
            if not cl.supported:
                lines.append(f"- **UNSUPPORTED** ({_md(cl.reason)}): {body} {refs}".rstrip())
            elif cl.subject_candidate:
                lines.append(f"- **CANDIDATE (not a confirmed identity):** {body} {refs}")
            elif not cl.refs:
                lines.append(f"- _(uncited interpretation)_ {body}")
            else:
                lines.append(f"- {body} {refs}")
    if checked is not None and checked.subject_candidates:
        lines.append("")
        lines.append("_People named above are **candidates** found in the data. Who someone "
                     "is is confirmed by a person, never by this agent._")
    if actions:
        lines.append("")
        lines.append("**Actions:**")
        for a in actions:
            line = f"- `{a['name']}` ({a['automation_mode']}): **{a['status']}**"
            if a.get("status") == "refused" and a.get("detail"):
                line += f" — {_md(a['detail'])}"
            lines.append(line)
    if checked is not None and checked.citations:
        lines.append("")
        lines.append("**Sources:**")
        cited_refs = sorted({r for c in checked.claims if c.supported for r in c.refs}
                            | {r for c in checked.clarifications if c.supported for r in c.refs},
                            key=lambda r: int(r[1:]) if r[1:].isdigit() else 0)
        rows = checked.extra.get("rows_by_ref") or {}
        for r in cited_refs:
            lines.append(f"- {_cite_label(registry, r)}")
            for x in rows.get(r, ()):                        # Q-05: the rows an aggregate covers
                lines.append(f"  - {_cite_label(registry, x)} (a row [{_md(r)}] was computed "
                             f"over)")
    lines.append("")
    lines.append(f"_{audit}_")
    return "\n".join(lines)


__all__ = ["ACTION_REPORT_STATUSES", "OUTCOME_REASON_CODES", "OutcomeReport", "Derivation",
           "VerifiedDerivation", "verify_derivations", "ActionReport", "ToolCallRequest", "ActionRequest", "Claim", "Clarification", "FinalAnswer",
           "PlannerTurn", "CheckedClaim", "CheckedClarification", "CheckedAnswer", "check_answer",
           "check_action_claims", "ungrounded_values", "render_markdown"]
