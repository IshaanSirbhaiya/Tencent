"""The **TraceRecorder** — what actually happened on one request, recorded as it happens.

`/trace` is the audit surface (`DEMO_AND_FRONTEND.md` §2.2). It shows who asked, under what
authority, what policy decided, which pointers the graph could answer and which needed the live
source, what came back, and what was withheld. This module is the recorder behind it. It is not a
logger: a log is prose about a request, and this is a **structured, ordered record of the request's
authorisation path**, with the properties an auditor needs and a log does not.

Four rules the implementation exists to hold
--------------------------------------------

**1. Nothing is seeded.** There are no example traces in this module and no demo trace anywhere in
the API. A trace exists because a request ran and recorded one. With no activity, `GET /trace`
returns an empty list — which is the true answer, and the frontend renders it as one.

**2. A trace that omits a step lies, so an unrecordable step is recorded as unrecordable.**
Every method that cannot record what it was asked to record appends a `defect` naming the fact it
could not capture and why. The trace is never silently thinner than the request. `integrity` on
every trace says whether any defect was recorded, so a consumer never has to notice by eye.

**3. Order is the record.** Steps carry a monotonic `seq` assigned under the lock, and `seq` — not
the timestamp — is the ordering authority: two steps in the same millisecond must still be ordered,
and a clock that steps backwards must not reorder an audit trail.

**4. The ledger append precedes the value it authorised** (`AGENT_PLANE.md` §7, property 3: *the
audit cannot lag the disclosure*). :meth:`TraceHandle.released` refuses to record a release that no
earlier `ledger_append` step covers — refuses by recording a `LEDGER_APPEND_NOT_RECORDED` defect
*and* recording the release anyway, because hiding the disclosure would be the worse failure. The
check is on the recorded steps, so it cannot be satisfied by a promise.

What a trace holds
------------------

`principal` (actor, on-behalf-of, purpose, grant), every `CapabilityRequest` issued, each policy
decision with its reason code, per-pointer origin (`graph` vs `live_source`), verdicts, cells split
into grounded / withheld / absent, trust labels, cost, and any escalation to a human.

Where a number cannot be derived from recorded steps it is `None` — never zero. Zero is a
measurement; `None` is "nobody measured". A risk certificate, for one, is Phase 6 work, so
`answer.certificate_risk` is `None` here and says so in `answer.note`.

Threading: FastAPI runs sync handlers on a threadpool, so every mutation takes an `RLock`.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets
import threading
from collections import Counter, deque
from typing import Any, Iterable, Mapping, Optional

#: The closed step vocabulary. A step kind absent here cannot be recorded — the vocabulary is the
#: schema of the audit surface, and an open one would let a call site invent an audit category.
STEP_KINDS = (
    "principal",        # who asked, on whose behalf, why
    "budget",           # the admission budget this request ran under
    "capability",       # a CapabilityRequest issued to an adapter
    "policy",           # one policy decision, permit or deny, with its reason code
    "pointer",          # one pointer resolved, and whether the graph or the live source answered
    "verdict",          # a verify() outcome
    "cell",             # one returned cell: grounded | withheld | absent
    "ledger_append",    # an append to the evidence ledger
    "released",         # a value disclosed to the caller — must follow its ledger append
    "escalation",       # handed to a human
    "cost",             # tokens / wall-ms / rows / bytes
    "note",             # a code-authored observation, addressed by key
    "unrecordable",     # a step this call site could NOT capture, and why
    "closed",           # the request finished
)

#: Where a pointer was answered from. Closed: "we are not sure" is not one of the options, because
#: the claim "41 answered from history, 6 needed the source" is only worth making if it is exact.
POINTER_ORIGINS = ("graph", "live_source")

#: The three states a cell may be in (spine D4). Never a fourth, never a degenerate value.
CELL_STATES = ("grounded", "withheld", "absent")

#: How many traces are retained in memory. Oldest are evicted, and the eviction is REPORTED in the
#: list response — an audit surface that silently forgot would be worse than one that says it did.
DEFAULT_CAPACITY = 200

#: Steps one trace may hold (Codex f1 #6: "a trace detail can contain unlimited steps"). Past it,
#: further steps are COUNTED, not stored, and one `STEP_LIMIT_REACHED` defect says so — so a
#: runaway request bounds the recorder's memory and `GET /trace/{id}`'s response without the trace
#: pretending to be complete.
MAX_STEPS_PER_TRACE = 5000
#: Defects one trace may hold, for the same reason. Past it they are counted in
#: `integrity.defects_dropped`.
MAX_DEFECTS_PER_TRACE = 1000
#: Bounds on ONE step's detail (Codex f2 #7: a bounded number of steps is not a bounded trace when
#: one step can carry an arbitrarily large payload). A string longer than `MAX_DETAIL_STR` is cut;
#: a list longer than `MAX_DETAIL_ITEMS` is cut; a nested mapping is not stored at all. Each cut is
#: listed in the step's ``bounded`` and recorded as a `STEP_DETAIL_BOUNDED` defect, so the trace
#: never looks complete when it is not.
MAX_DETAIL_STR = 2000
MAX_DETAIL_ITEMS = 100
#: Serialised bytes one trace may hold across all its steps and defects. Past it, further steps
#: are counted (as `steps_dropped`), not stored, and a `TRACE_BYTES_LIMIT_REACHED` defect says so.
MAX_TRACE_BYTES = 2 * 1024 * 1024
#: Serialised bytes one defect may hold; a larger detail is replaced by a note saying so. With
#: `MAX_DEFECTS_PER_TRACE` this bounds what defects can add to a trace.
MAX_DEFECT_BYTES = 8 * 1024

#: The detail keys each step kind may carry (Codex f2 #9: an allow-list, not a pass-through). These
#: are the parameters of the `TraceHandle` method that records the kind; a key not named here is
#: not stored, and its name is listed in the step's ``masked_fields`` (a `STEP_DETAIL_BOUNDED`
#: defect says the step was not stored whole).
STEP_FIELDS: dict[str, frozenset[str]] = {
    "principal": frozenset({"actor", "on_behalf_of", "purpose", "grant_id"}),
    "budget": frozenset({"max_llm_tokens", "max_wall_ms", "max_rows", "max_steps",
                         "max_tool_calls", "admission_id"}),
    "capability": frozenset({"source_id", "capability", "entity", "fields", "read_hash",
                             "as_of"}),
    "policy": frozenset({"field_path", "withheld", "policy_id", "reason_code", "appealable",
                         "source_id"}),
    "pointer": frozenset({"origin", "pointer_id", "source_id", "kind", "trust", "as_of"}),
    "verdict": frozenset({"verdict", "pointer_id", "source_id"}),
    "cell": frozenset({"state", "field_path", "trust", "reason_code", "policy_id", "source_id"}),
    "ledger_append": frozenset({"entry_id", "covers", "source_id", "confirmed_by"}),
    "released": frozenset({"field_path", "source_id", "ledger_covered", "covered_by",
                           "pointer_id", "pointer_recorded"}),
    "escalation": frozenset({"to", "reason", "blocking"}),
    "cost": frozenset({"llm_tokens", "wall_ms", "rows", "bytes_egressed", "model_calls"}),
    "note": frozenset({"key", "text"}),
    "unrecordable": frozenset({"what", "because"}),
    "closed": frozenset({"outcome"}),
}


def _bounded_value(value: Any, cuts: list[str], key: str) -> tuple[bool, Any]:
    """``(stored, value)`` for one detail value: a scalar (strings cut), an enum's scalar value, or
    a list of those (cut). A mapping, or a list holding one, is NOT stored (``stored`` False)."""
    v = getattr(value, "value", value) if not isinstance(value, (str, int, float, bool)) else value
    if v is None or isinstance(v, (bool, int, float)):
        return True, v
    if isinstance(v, str):
        if len(v) > MAX_DETAIL_STR:
            cuts.append(f"{key} (cut to {MAX_DETAIL_STR} characters)")
            return True, v[:MAX_DETAIL_STR]
        return True, v
    if isinstance(v, (list, tuple, set, frozenset)):
        items = list(v)
        if len(items) > MAX_DETAIL_ITEMS:
            cuts.append(f"{key} (cut to {MAX_DETAIL_ITEMS} items)")
            items = items[:MAX_DETAIL_ITEMS]
        out = []
        for i, x in enumerate(items):
            ok, got = _bounded_value(x, cuts, f"{key}[{i}]")
            if not ok or isinstance(got, list):
                cuts.append(f"{key}[{i}] (not a scalar; not stored)")
                continue
            out.append(got)
        return True, out
    if isinstance(v, Mapping):
        return False, None
    text = str(v)
    if len(text) > MAX_DETAIL_STR:
        cuts.append(f"{key} (cut to {MAX_DETAIL_STR} characters)")
    return True, text[:MAX_DETAIL_STR]


def bound_detail(detail: Mapping[str, Any],
                 allowed: Optional[frozenset[str]] = None) -> tuple[dict[str, Any], list[str]]:
    """``(stored detail, what was cut or masked)``. ``allowed`` None: any key, values bounded."""
    out: dict[str, Any] = {}
    cuts: list[str] = []
    for k, v in detail.items():
        key = str(k)[:200]
        if allowed is not None and key not in allowed:
            cuts.append(f"{key} (not in this step kind's fields; not stored)")
            continue
        ok, got = _bounded_value(v, cuts, key)
        if not ok:
            cuts.append(f"{key} (a nested mapping; not stored)")
            continue
        out[key] = got
    return out, cuts


def _size(obj: Any) -> int:
    return len(json.dumps(obj, default=str, ensure_ascii=False))


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).isoformat()


class TraceStep:
    """One recorded step. Immutable after construction."""

    __slots__ = ("seq", "at", "kind", "summary", "detail")

    def __init__(self, seq: int, at: str, kind: str, summary: str,
                 detail: Mapping[str, Any]) -> None:
        if kind not in STEP_KINDS:
            raise ValueError(f"unknown trace step kind {kind!r}; the vocabulary is closed: "
                             f"{list(STEP_KINDS)}")
        self.seq, self.at, self.kind, self.summary = seq, at, kind, summary
        self.detail = dict(detail)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "at": self.at, "kind": self.kind,
                "summary": self.summary, "detail": self.detail}


class Trace:
    """The record of one request. Built only by :class:`TraceRecorder`."""

    def __init__(self, request_id: str, started_at: dt.datetime,
                 surface: str, agent: str, version: str,
                 owner: Optional[str] = None) -> None:
        self.request_id = request_id
        self.started_at = started_at
        self.ended_at: Optional[dt.datetime] = None
        self.surface = surface
        self.agent = agent
        self.version = version
        self.outcome: Optional[str] = None
        self.steps: list[TraceStep] = []
        self.defects: list[dict[str, Any]] = []
        #: Steps / defects that arrived after the per-trace bound and were counted, not stored.
        self.steps_dropped = 0
        self.defects_dropped = 0
        #: Serialised bytes held by stored steps and defects (bounded by `MAX_TRACE_BYTES`).
        self.bytes_held = 0
        # Ownership (Codex f1 #1). `_declared_owner` is what the call site passed to `begin`;
        # `_on_behalf_of` collects every `on_behalf_of` a principal step recorded. See `owner`.
        self._declared_owner: Optional[str] = owner or None
        self._on_behalf_of: set[Optional[str]] = set()

    # --- ownership ---------------------------------------------------------------------
    @property
    def owner(self) -> Optional[str]:
        """The ONE authenticated principal this trace belongs to, or `None` if that is not known.

        Every gateway surface that opens a trace records the authenticated principal as the
        principal step's `on_behalf_of` (R-015: it comes from `auth.require_principal`, never
        from a request body). A call site may also name the owner at `begin(owner=...)`.

        Fail closed: the owner is known only when every source of it agrees on exactly one
        non-empty principal. No principal step, a `None` on_behalf_of (a scheduled run), two
        different on_behalf_of values, or a declared owner the steps contradict all give `None`
        — and a trace with no owner is readable only by an explicit `trace_admin`
        (`trace_routes`). "Owner unknown" is never treated as "public".
        """
        named = set(self._on_behalf_of)
        if self._declared_owner is not None:
            named.add(self._declared_owner)
        if len(named) != 1:
            return None
        (only,) = tuple(named)
        return only or None

    # --- projections -------------------------------------------------------------------
    def _of(self, kind: str) -> list[TraceStep]:
        return [s for s in self.steps if s.kind == kind]

    @property
    def principal(self) -> Optional[dict[str, Any]]:
        found = self._of("principal")
        return dict(found[-1].detail) if found else None

    @property
    def counts_exact(self) -> bool:
        """Are counts over the stored steps the request's totals? Not once a step was dropped
        (Codex f2 #4): they are then only the counts of the stored prefix."""
        return self.steps_dropped == 0

    def _total(self, n: int) -> Optional[int]:
        """A count served as a TOTAL only when no step was dropped; else ``None`` (not recorded
        in full), with the stored count beside it as an explicitly labelled lower bound."""
        return n if self.counts_exact else None

    def summary(self) -> dict[str, Any]:
        """The list-row shape. Cheap: counts only, no step bodies.

        After the step bound dropped steps, every count is ``None`` and its stored figure moves to
        ``at_least`` — a prefix count is never presented as the request's total (Codex f2 #4)."""
        cells = self._of("cell")
        counted = {
            "capabilities": len(self._of("capability")),
            "withheld": sum(1 for s in cells if s.detail.get("state") == "withheld"),
            "absent": sum(1 for s in cells if s.detail.get("state") == "absent"),
            "grounded": sum(1 for s in cells if s.detail.get("state") == "grounded"),
            "escalations": len(self._of("escalation")),
        }
        return {
            "request_id": self.request_id,
            "surface": self.surface,
            "agent": self.agent,
            "version": self.version,
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at) if self.ended_at else None,
            "outcome": self.outcome,
            "principal": self.principal,
            "steps": len(self.steps) + self.steps_dropped,
            "steps_stored": len(self.steps),
            **{k: self._total(v) for k, v in counted.items()},
            "at_least": None if self.counts_exact else counted,
            "integrity": self.integrity(),
        }

    def integrity(self) -> dict[str, Any]:
        """Is this trace complete, and if not, what is missing? Always present, never inferred
        by the reader from the absence of a `defects` key."""
        return {
            "complete": not self.defects and not self.defects_dropped,
            "defect_count": len(self.defects) + self.defects_dropped,
            "defect_codes": sorted({str(d.get("code")) for d in self.defects}),
            "steps_dropped": self.steps_dropped,
            "defects_dropped": self.defects_dropped,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(),
                "step_list": [s.to_dict() for s in self.steps],
                "defect_list": list(self.defects),
                "view": self.view()}

    # --- the /trace view (DEMO_AND_FRONTEND §2.2) --------------------------------------
    def view(self) -> dict[str, Any]:
        """The rendered authorisation path. Every row is DERIVED from recorded steps.

        When steps were dropped, ``counts_exact`` is False and every count below is labelled a
        lower bound ("at least N", ``lower_bound: true``), never a total (Codex f2 #4).

        Absent evidence produces an absent row, never a zero: a branch nothing was recorded for
        does not appear at all, because an empty ADAPTER box reads as "the adapter returned
        nothing" when the truth is "no adapter ran".
        """
        return {
            "counts_exact": self.counts_exact,
            "request": self._view_request(),
            "branches": self._view_branches(),
            "results": self._view_results(),
            "answer": self._view_answer(),
        }

    def _n(self, n: int) -> str:
        """A count rendered for a view row: exact, or "at least N" after steps were dropped."""
        return str(n) if self.counts_exact else f"at least {n}"

    def _lb(self, text: str) -> str:
        return text if self.counts_exact else (f"at least: {text} (steps were dropped; these are "
                                               f"counts of the stored steps only)")

    def _view_request(self) -> dict[str, Any]:
        principal = self.principal or {}
        budget = self._of("budget")
        # Summed over the cost steps that actually CARRIED a token count. A cost step that
        # recorded wall time and nothing else must not contribute a zero — "0 tokens used" is a
        # measurement, and this call site made none.
        measured = [s for s in self._of("cost") if s.detail.get("llm_tokens") is not None]
        used_tokens = sum(int(s.detail["llm_tokens"]) for s in measured) if measured else None
        if not self.counts_exact:
            used_tokens = None              # a dropped cost step may have carried tokens
        row: dict[str, Any] = {
            "agent": self.agent,
            "version": self.version,
            "surface": self.surface,
            "purpose": principal.get("purpose"),
            "on_behalf_of": principal.get("on_behalf_of"),
            "actor": principal.get("actor"),
            "grant": principal.get("grant_id"),
            "budget_used": used_tokens,
            "budget_total": (budget[-1].detail.get("max_llm_tokens") if budget else None),
        }
        return row

    def _view_branches(self) -> list[dict[str, Any]]:
        branches: list[dict[str, Any]] = []
        pointers = self._of("pointer")
        if pointers:
            as_of = next((s.detail.get("as_of") for s in pointers if s.detail.get("as_of")), None)
            from_graph = sum(1 for s in pointers if s.detail.get("origin") == "graph")
            from_source = sum(1 for s in pointers if s.detail.get("origin") == "live_source")
            branches.append({
                "kind": "graph",
                "title": "GRAPH",
                "subtitle": f"as_of {as_of}" if as_of else "as_of not recorded",
                "rows": [
                    {"label": "pointers resolved", "value": self._n(len(pointers)),
                     "tone": "neutral"},
                    # The graph stores references, so this is a structural zero, not a count that
                    # happened to come out zero — it is what D2 means.
                    {"label": "values stored", "value": "0", "tone": "neutral"},
                    {"label": "answered from history", "value": self._n(from_graph),
                     "tone": "good" if from_graph else "neutral"},
                    {"label": "needed live bytes", "value": self._n(from_source),
                     "tone": "warn" if from_source else "neutral"},
                ],
            })
        by_source: dict[str, list[TraceStep]] = {}
        for step in self.steps:
            source = step.detail.get("source_id")
            if step.kind in ("capability", "policy", "ledger_append", "cell") and source:
                by_source.setdefault(str(source), []).append(step)
        for source_id, steps in sorted(by_source.items()):
            rows: list[dict[str, Any]] = []
            for step in steps:
                if step.kind == "policy":
                    withheld = bool(step.detail.get("withheld"))
                    field = step.detail.get("field_path") or step.detail.get("decision") or ""
                    value = (f"withheld · {step.detail.get('reason_code')}" if withheld else "✓")
                    rows.append({"label": f"POLICY {field}".strip(),
                                 "value": value, "tone": "bad" if withheld else "good"})
                elif step.kind == "ledger_append":
                    rows.append({"label": "LEDGER",
                                 "value": f"appended {step.detail.get('entry_id')}",
                                 "tone": "neutral"})
                elif step.kind == "capability":
                    rows.append({"label": "CAPABILITY",
                                 "value": str(step.detail.get("capability") or ""),
                                 "tone": "neutral"})
            cells = [s for s in steps if s.kind == "cell"]
            if cells:
                trusts = sorted({str(s.detail.get("trust")) for s in cells
                                 if s.detail.get("trust")})
                rows.append({"label": "CORE",
                             "value": f"{self._n(len(cells))} cells" + (f" · {'/'.join(trusts)}"
                                                               if trusts else ""),
                             "tone": "neutral"})
            branches.append({"kind": "adapter", "title": "ADAPTER", "subtitle": source_id,
                             "rows": rows})
        return branches

    def _view_results(self) -> list[dict[str, Any]]:
        cells = self._of("cell")
        results: list[dict[str, Any]] = []
        grounded = [s for s in cells if s.detail.get("state") == "grounded"]
        if grounded:
            trusted = sum(1 for s in grounded if s.detail.get("trust") == "trusted")
            untrusted = sum(1 for s in grounded if s.detail.get("trust") == "untrusted")
            unlabelled = len(grounded) - trusted - untrusted
            parts = [f"{trusted} trusted", f"{untrusted} untrusted"]
            if unlabelled:
                # An unlabelled cell is reported as unlabelled, not folded into "trusted". The
                # label is stamped by an adapter Core; a cell that never got one has not been
                # judged, and "trusted" is a judgement.
                parts.append(f"{unlabelled} unlabelled")
            results.append({"kind": "grounded", "count": len(grounded),
                            "lower_bound": not self.counts_exact,
                            "detail": self._lb(" · ".join(parts)),
                            "tone": "bad" if untrusted else "good"})
        withheld = [s for s in cells if s.detail.get("state") == "withheld"]
        if withheld:
            codes = sorted({str(s.detail.get("reason_code")) for s in withheld})
            results.append({"kind": "withheld", "count": len(withheld),
                            "lower_bound": not self.counts_exact,
                            "detail": self._lb(", ".join(codes)), "tone": "bad"})
        absent = [s for s in cells if s.detail.get("state") == "absent"]
        if absent:
            # Withheld and absent are DIFFERENT facts and are rendered as different rows
            # (DEMO_AND_FRONTEND §2.2). Merging them would hide a policy denial inside "no data".
            results.append({"kind": "absent", "count": len(absent),
                            "lower_bound": not self.counts_exact,
                            "detail": self._lb("no value at the source"), "tone": "warn"})
        verdicts = self._of("verdict")
        if verdicts:
            counts: dict[str, int] = {}
            for step in verdicts:
                key = str(step.detail.get("verdict"))
                counts[key] = counts.get(key, 0) + 1
            results.append({"kind": "verdicts", "count": len(verdicts),
                            "lower_bound": not self.counts_exact,
                            "detail": self._lb(" · ".join(f"{k} {v}"
                                                          for k, v in sorted(counts.items()))),
                            "tone": "neutral"})
        return results

    def _view_answer(self) -> dict[str, Any]:
        released = self._of("released")
        return {
            "citations": self._total(len(released)),
            "citations_at_least": None if self.counts_exact else len(released),
            # Phase 6 owns risk certificates. `None` says nobody measured; a `0.0` here would be a
            # measurement this system has not made.
            "certificate_risk": None,
            "risk_budget": None,
            "admitted": self.outcome == "answered" if self.outcome else None,
            "note": ("certificate_risk and risk_budget are not recorded: no component computes a "
                     "calibrated risk certificate yet (PHASE_PLAN Phase 6). They are null rather "
                     "than zero because zero would be a measurement."),
        }


class TraceHandle:
    """The per-request recording API. Obtained from :meth:`TraceRecorder.begin`.

    Every method returns ``self`` so a call site can chain, and none of them raises on bad input:
    a recorder that threw would turn an audit gap into a request failure, and the call site would
    grow a ``try/except`` that swallowed the trace. Bad input is recorded as a defect instead.
    """

    def __init__(self, recorder: "TraceRecorder", trace: Trace) -> None:
        self._recorder = recorder
        self._trace = trace

    # --- internals ---------------------------------------------------------------------
    @property
    def request_id(self) -> str:
        return self._trace.request_id

    @property
    def trace(self) -> Trace:
        return self._trace

    # `kind`, `summary`, `code` and `because` are POSITIONAL-ONLY (`/`). A step's detail legally
    # contains a key called `kind` (a pointer kind), and with ordinary parameters that collided
    # with the step kind and raised inside the recorder — turning an audit record into a request
    # failure. Positional-only means no detail key can ever shadow a recorder parameter, whatever
    # a future call site names its fields.
    def _append(self, kind: str, summary: str, /, **detail: Any) -> "TraceHandle":
        self._recorder._append(self._trace, kind, summary, detail)
        return self

    def _defect(self, code: str, because: str, /, **detail: Any) -> "TraceHandle":
        self._recorder._defect(self._trace, code, because, detail)
        return self

    # --- the recording surface ---------------------------------------------------------
    def principal(self, *, actor: Optional[str], on_behalf_of: Optional[str],
                  purpose: Optional[str], grant_id: Optional[str] = None) -> "TraceHandle":
        """Who asked, on whose behalf, and why (spine D5).

        `on_behalf_of=None` is a legitimate recorded value — a scheduled build-time crawl has no
        human behind it. A missing `actor` or `purpose` is not: an unattributable read is not an
        auditable one, and a purpose is required of every read. Either being `None` is recorded as
        a defect *and* recorded as `None` in the step, so the trace shows the gap rather than
        hiding it behind a plausible default.
        """
        if not actor:
            self._defect("PRINCIPAL_ACTOR_NOT_RECORDED",
                         "the call site did not name the service identity making this read; an "
                         "unattributable read is not an auditable one")
        if not purpose:
            self._defect("PURPOSE_NOT_RECORDED",
                         "the call site declared no purpose. Every read carries (who, on-behalf-"
                         "of, WHY) (spine D5); this surface does not yet construct a Principal, "
                         "so the why is missing rather than defaulted")
        return self._append("principal", "principal declared",
                            actor=actor, on_behalf_of=on_behalf_of,
                            purpose=purpose, grant_id=grant_id)

    def from_principal(self, principal: Any) -> "TraceHandle":
        """Record a `databridge_adapter.Principal` without this module importing it."""
        return self.principal(
            actor=getattr(principal, "actor", None),
            on_behalf_of=getattr(principal, "on_behalf_of", None),
            purpose=getattr(principal, "purpose", None),
            grant_id=getattr(principal, "grant_id", None))

    def budget(self, *, max_llm_tokens: Optional[int] = None,
               max_wall_ms: Optional[int] = None, max_rows: Optional[int] = None,
               **rest: Any) -> "TraceHandle":
        return self._append("budget", "budget admitted", max_llm_tokens=max_llm_tokens,
                            max_wall_ms=max_wall_ms, max_rows=max_rows, **rest)

    def capability(self, request: Any = None, *, source_id: Optional[str] = None,
                   capability: Optional[str] = None, entity: Optional[str] = None,
                   fields: Iterable[str] = (), read_hash: Optional[str] = None,
                   as_of: Optional[str] = None) -> "TraceHandle":
        """One `CapabilityRequest`. Pass the object, or the fields when there is no object yet.

        Duck-typed on purpose: the gateway does not import `databridge_adapter` to record, so a
        surface that has no adapter behind it yet can still record the request it made.
        """
        if request is not None:
            source_id = source_id or getattr(request, "source_id", None)
            capability = capability or getattr(request, "capability", None)
            projection = getattr(request, "projection", None)
            entity = entity or getattr(projection, "entity", None)
            fields = tuple(fields) or tuple(getattr(projection, "fields", ()) or ())
            read_hash = read_hash or getattr(request, "read_hash", None)
            when = getattr(request, "as_of", None)
            as_of = as_of or (when.isoformat() if hasattr(when, "isoformat") else when)
            principal = getattr(request, "principal", None)
            if principal is not None and not self._trace._of("principal"):
                self.from_principal(principal)
        if not source_id or not capability:
            self._defect("CAPABILITY_NOT_FULLY_RECORDED",
                         "a capability request was issued without a recordable source_id and "
                         "capability name, so the trace cannot say what was asked of whom",
                         source_id=source_id, capability=capability)
        return self._append("capability", f"{capability} on {source_id}",
                            source_id=source_id, capability=capability, entity=entity,
                            fields=list(fields), read_hash=read_hash, as_of=as_of)

    def policy(self, decision: Any = None, *, field_path: Optional[str] = None,
               withheld: Optional[bool] = None, policy_id: Optional[str] = None,
               reason_code: Optional[str] = None, appealable: Optional[bool] = None,
               source_id: Optional[str] = None) -> "TraceHandle":
        """One policy decision. A denial without a reason code is a defect, not a decision."""
        if decision is not None:
            withheld = getattr(decision, "withheld", withheld)
            policy_id = policy_id or getattr(decision, "policy_id", None)
            reason_code = reason_code or getattr(decision, "reason_code", None)
            if appealable is None:
                appealable = getattr(decision, "appealable", None)
        if withheld is None:
            self._defect("POLICY_DECISION_NOT_RECORDED",
                         "a policy step was recorded without saying whether it permitted or "
                         "denied; a decision nobody can read is not a decision",
                         field_path=field_path)
        elif withheld and not (policy_id and reason_code):
            self._defect("DENIAL_NOT_ATTRIBUTABLE",
                         "a denial was recorded without both the policy that made it and its "
                         "machine-readable reason code; that is a missing value with extra steps",
                         field_path=field_path)
        verb = "withheld" if withheld else "permitted"
        return self._append("policy", f"{verb} {field_path or ''}".strip(),
                            field_path=field_path, withheld=withheld, policy_id=policy_id,
                            reason_code=reason_code, appealable=appealable, source_id=source_id)

    def pointer(self, *, origin: str, pointer_id: Optional[str] = None,
                source_id: Optional[str] = None, kind: Optional[str] = None,
                trust: Optional[str] = None, as_of: Optional[str] = None) -> "TraceHandle":
        """One pointer resolved, and — the claim the surface exists to make — whether the graph
        answered it from history or the live source had to be read."""
        if origin not in POINTER_ORIGINS:
            self._defect("POINTER_ORIGIN_NOT_RECORDED",
                         f"origin {origin!r} is not one of {list(POINTER_ORIGINS)}. 'answered "
                         f"from history' is only worth claiming if it is exact",
                         pointer_id=pointer_id)
            origin = None                                            # type: ignore[assignment]
        return self._append("pointer", f"pointer from {origin}", origin=origin,
                            pointer_id=pointer_id, source_id=source_id, kind=kind,
                            trust=trust, as_of=as_of)

    def from_source_pointer(self, pointer: Any, *, origin: str,
                            as_of: Optional[str] = None) -> "TraceHandle":
        """Record a `databridge_core.SourcePointer` without importing it."""
        trust = getattr(pointer, "trust", None)
        return self.pointer(
            origin=origin,
            pointer_id=getattr(pointer, "pointer_id", None) or getattr(pointer, "id", None),
            source_id=getattr(pointer, "source_id", None),
            kind=str(getattr(pointer, "kind", "") or "") or None,
            trust=getattr(trust, "value", trust),
            as_of=as_of)

    def verdict(self, verdict: Any, *, pointer_id: Optional[str] = None,
                source_id: Optional[str] = None) -> "TraceHandle":
        value = getattr(verdict, "value", verdict)
        return self._append("verdict", f"verify → {value}", verdict=str(value),
                            pointer_id=pointer_id, source_id=source_id)

    def cell(self, *, state: str, field_path: Optional[str] = None,
             trust: Optional[str] = None, reason_code: Optional[str] = None,
             policy_id: Optional[str] = None, source_id: Optional[str] = None) -> "TraceHandle":
        """One returned cell. `withheld` and `absent` are different facts and stay different."""
        if state not in CELL_STATES:
            self._defect("CELL_STATE_NOT_RECORDED",
                         f"state {state!r} is not one of {list(CELL_STATES)}; a cell is "
                         f"GroundedValue | Withheld | Absent and nothing else",
                         field_path=field_path)
        if state == "withheld" and not reason_code:
            self._defect("WITHHOLDING_REASON_NOT_RECORDED",
                         "a withheld cell was recorded with no reason code, which makes it "
                         "indistinguishable on the page from an absent one",
                         field_path=field_path)
        return self._append("cell", f"{state} {field_path or ''}".strip(), state=state,
                            field_path=field_path, trust=trust, reason_code=reason_code,
                            policy_id=policy_id, source_id=source_id)

    def ledger_append(self, *, entry_id: str, covers: Iterable[str] = (),
                      source_id: Optional[str] = None,
                      confirmed_by: Optional[str] = None) -> "TraceHandle":
        """An append to the evidence ledger. `covers` names the field paths this row authorises,
        FOR `source_id`; :meth:`released` is checked against both.

        Codex f1 #4: an empty `covers` authorises NOTHING. Only the explicit `"*"` covers every
        field -- and only of this row's own source. An append that names no entry id or no source
        is recorded (it happened) but cannot authorise any release, and says so as a defect.
        """
        covered = sorted({str(c) for c in covers if str(c)})
        if not entry_id or not source_id:
            self._defect("LEDGER_APPEND_NOT_ATTRIBUTABLE",
                         "a ledger append was recorded without both its entry id and the source it "
                         "covers, so it cannot authorise any release",
                         entry_id=entry_id or None, source_id=source_id)
        return self._append("ledger_append", f"appended {entry_id}", entry_id=entry_id or None,
                            covers=covered, source_id=source_id, confirmed_by=confirmed_by)

    def released(self, *, field_path: str, source_id: Optional[str] = None,
                 pointer_id: Optional[str] = None) -> "TraceHandle":
        """A value disclosed to the caller.

        **The ordering invariant** (`AGENT_PLANE.md` section 7, property 3): the ledger row
        precedes the value, so the audit cannot lag the disclosure. This checks the recorded steps
        for an earlier `ledger_append` that EXPLICITLY covers this release:

        * the same, recorded `source_id` on both (an append for source A never covers source B,
          and a release or append with no source cannot be matched to anything);
        * a recorded entry id;
        * `field_path` named in its `covers`, or `"*"` in them. An empty `covers` covers nothing
          (Codex f1 #4 -- it used to be treated as a wildcard).

        The covering entry is recorded as `covered_by`, so the trace names the exact row it relies
        on rather than asserting coverage. If none is found the release is still recorded, with a
        defect beside it: a trace that hid the disclosure to keep itself clean would be the exact
        failure the invariant exists to prevent.

        `pointer_id` names the SourcePointer the released value came from (hard constraint: no value
        without a pointer). Codex f2 #2: it is REQUIRED, and it must match a `pointer` step
        recorded earlier with the SAME pointer id AND the same recorded `source_id` as this
        release -- a pointer of source A never vouches for a value of source B. A release with no
        `pointer_id`, or whose pointer was not recorded for its source, gets
        `pointer_recorded: False` and a `RELEASE_POINTER_NOT_RECORDED` defect; the release itself
        is still recorded, for the same reason as above.
        """
        covered_by: Optional[str] = None
        for step in self._trace.steps:
            if step.kind != "ledger_append":
                continue
            entry = step.detail.get("entry_id")
            append_source = step.detail.get("source_id")
            if not entry or not source_id or append_source != source_id:
                continue
            covers = step.detail.get("covers") or []
            if "*" in covers or field_path in covers:
                covered_by = str(entry)
                break
        covered = covered_by is not None
        if not covered:
            self._defect("LEDGER_APPEND_NOT_RECORDED",
                         "a value was released with no ledger append recorded before it that "
                         "explicitly covers this field of this source. The ledger row precedes "
                         "the value so the audit cannot lag the disclosure (AGENT_PLANE section "
                         "7); either the append did not happen or it was not recorded, and the "
                         "trace cannot tell those apart",
                         field_path=field_path, source_id=source_id)
        pointer_recorded = bool(pointer_id) and bool(source_id) and any(
            s.kind == "pointer" and s.detail.get("pointer_id") == pointer_id
            and s.detail.get("source_id") == source_id
            for s in self._trace.steps)
        if not pointer_recorded:
            self._defect("RELEASE_POINTER_NOT_RECORDED",
                         ("a value was released naming no SourcePointer, so the trace cannot show "
                          "where it came from" if not pointer_id else
                          "a value was released naming a SourcePointer that no earlier pointer "
                          "step recorded for this release's source"),
                         field_path=field_path, pointer_id=pointer_id, source_id=source_id)
        return self._append("released", f"released {field_path}", field_path=field_path,
                            source_id=source_id, ledger_covered=covered, covered_by=covered_by,
                            pointer_id=pointer_id, pointer_recorded=pointer_recorded)

    def escalation(self, *, to: str, reason: str, blocking: bool = True) -> "TraceHandle":
        """Handed to a human. `blocking` says whether the request stopped here."""
        return self._append("escalation", f"escalated to {to}", to=to, reason=reason,
                            blocking=blocking)

    def cost(self, *, llm_tokens: Optional[int] = None, wall_ms: Optional[int] = None,
             rows: Optional[int] = None, bytes_egressed: Optional[int] = None,
             model_calls: Optional[int] = None) -> "TraceHandle":
        """What the request cost. Every dimension defaults to `None` — *not measured* — because a
        default of `0` would put a measurement nobody made into an audit record."""
        def _int(v: Optional[int]) -> Optional[int]:
            return None if v is None else int(v)

        return self._append("cost", "cost", llm_tokens=_int(llm_tokens), wall_ms=_int(wall_ms),
                            rows=_int(rows), bytes_egressed=_int(bytes_egressed),
                            model_calls=_int(model_calls))

    def note(self, *, key: str, text: str) -> "TraceHandle":
        return self._append("note", key, key=key, text=text)

    def unrecordable(self, *, what: str, because: str) -> "TraceHandle":
        """Rule 2, made explicit for a call site that knows it cannot capture something."""
        self._defect("STEP_UNRECORDABLE", because, what=what)
        return self._append("unrecordable", what, what=what, because=because)

    def close(self, outcome: str) -> Trace:
        """Finish the trace. `outcome` is the call site's own word: `answered`, `refused`,
        `escalated`, `failed`."""
        return self._recorder._close(self._trace, outcome)

    # Context-manager sugar: an exception closes the trace as `failed` rather than leaving it open
    # forever, which would make a crashed request invisible on the audit surface.
    def __enter__(self) -> "TraceHandle":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._trace.ended_at is None:
            if exc_type is not None:
                self.unrecordable(
                    what="the remainder of this request",
                    because=f"the handler raised {exc_type.__name__} before closing the trace")
                self.close("failed")
            else:
                self.close("closed_without_outcome")
        return False


class TraceRecorder:
    """Process-wide store of traces. Bounded, ordered, and never pre-populated.

    Reads come in two kinds. :meth:`list` / :meth:`get` are the INTERNAL, unfiltered reads other
    gateway modules use to join (`activity_routes.trace_index`); they are never returned to an
    HTTP caller as-is. :meth:`list_for` / :meth:`get_for` are the viewer-scoped reads the
    `/trace` routes serve (Codex f1 #1): a trace is readable by its :attr:`Trace.owner`, or by a
    caller the route has established holds the explicit `trace_admin` role.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY,
                 clock=_utcnow) -> None:
        self._lock = threading.RLock()
        self._traces: "deque[Trace]" = deque(maxlen=max(1, int(capacity)))
        self._by_id: dict[str, Trace] = {}
        self._clock = clock
        self._n = 0
        self._seq = 0
        self.evicted = 0
        #: Evictions per owner (`None` = owner unknown), so a viewer is told how many of THEIR
        #: traces were forgotten without learning how busy anyone else is.
        self._evicted_by_owner: "Counter[Optional[str]]" = Counter()

    # --- writing -----------------------------------------------------------------------
    def begin(self, *, surface: str, agent: str = "", version: str = "",
              owner: Optional[str] = None) -> TraceHandle:
        """Open a trace.

        The id is an ordinal (so an operator can quote it and read the order off it) plus a
        random suffix (Codex f1 #1, defence in depth: ids are not enumerable). Visibility does
        not depend on the suffix -- :meth:`get_for` checks ownership -- it only means a guessed id
        is not even a probe.

        `owner` is the authenticated principal the request runs for, when the call site knows it;
        see :attr:`Trace.owner` for how it combines with the principal step.
        """
        with self._lock:
            self._n += 1
            request_id = f"req-{self._n:06d}-{secrets.token_hex(4)}"
            trace = Trace(request_id=request_id, started_at=self._clock(),
                          surface=surface, agent=agent or surface, version=version,
                          owner=owner)
            if len(self._traces) == self._traces.maxlen:
                dropped = self._traces[0]
                self._by_id.pop(dropped.request_id, None)
                self.evicted += 1
                self._evicted_by_owner[dropped.owner] += 1
            self._traces.append(trace)
            self._by_id[request_id] = trace
        return TraceHandle(self, trace)

    def _append(self, trace: Trace, kind: str, summary: str,
                detail: Mapping[str, Any]) -> None:
        with self._lock:
            if kind == "principal":
                # Ownership sees EVERY principal the request named, stored or not, so a dropped
                # step can never hide a second on_behalf_of and make an ambiguous trace look owned.
                obo = detail.get("on_behalf_of")
                trace._on_behalf_of.add(obo if isinstance(obo, str) and obo else None)
            if len(trace.steps) >= MAX_STEPS_PER_TRACE:
                # Bounded (Codex f1 #6), and said so once rather than silently truncated.
                if trace.steps_dropped == 0:
                    self._defect(trace, "STEP_LIMIT_REACHED",
                                 f"this trace reached {MAX_STEPS_PER_TRACE} steps; further steps "
                                 f"are counted in integrity.steps_dropped but not stored",
                                 {"limit": MAX_STEPS_PER_TRACE})
                trace.steps_dropped += 1
                return
            if kind not in STEP_KINDS:
                # An unknown step kind is itself a recording failure, and recording it as a defect
                # keeps rule 2: the trace says it could not capture the step.
                self._defect(trace, "STEP_KIND_UNKNOWN",
                             f"unknown trace step kind {str(kind)[:100]!r}; the vocabulary is "
                             f"closed: {list(STEP_KINDS)}", dict(detail))
                return
            # Codex f2 #7 / #9: the step stores only its kind's fields, each bounded in size.
            stored, cuts = bound_detail(detail, STEP_FIELDS[kind])
            if cuts:
                stored["bounded"] = cuts[:MAX_DETAIL_ITEMS]
            summary = str(summary)[:MAX_DETAIL_STR]
            self._seq += 1
            step = TraceStep(self._seq, _iso(self._clock()), kind, summary, stored)
            size = _size(step.to_dict())
            if trace.bytes_held + size > MAX_TRACE_BYTES:
                if trace.steps_dropped == 0:
                    self._defect(trace, "TRACE_BYTES_LIMIT_REACHED",
                                 f"this trace reached {MAX_TRACE_BYTES} bytes; further steps are "
                                 f"counted in integrity.steps_dropped but not stored",
                                 {"limit": MAX_TRACE_BYTES})
                trace.steps_dropped += 1
                return
            trace.bytes_held += size
            trace.steps.append(step)
            if cuts:
                self._defect(trace, "STEP_DETAIL_BOUNDED",
                             f"step {step.seq} ({kind}) was not stored whole: a field outside "
                             f"its kind's vocabulary, a nested value, or an oversized value was "
                             f"cut; `bounded` on the step names each one",
                             {"seq": step.seq, "kind": kind})

    def _defect(self, trace: Trace, code: str, because: str,
                detail: Mapping[str, Any]) -> None:
        with self._lock:
            if len(trace.defects) >= MAX_DEFECTS_PER_TRACE:
                trace.defects_dropped += 1
                return
            stored, cuts = bound_detail(detail)
            if cuts:
                stored["bounded"] = cuts[:MAX_DETAIL_ITEMS]
            entry = {"code": str(code)[:200], "because": str(because)[:MAX_DETAIL_STR],
                     "detail": stored}
            size = _size(entry)
            if size > MAX_DEFECT_BYTES:
                # A defect is never refused (it is how the trace says it is incomplete), but its
                # detail is: the code and reason are kept, the oversized detail is not.
                entry["detail"] = {"bounded": [f"detail larger than {MAX_DEFECT_BYTES} bytes; "
                                               f"not stored"]}
                size = _size(entry)
            trace.bytes_held += size
            trace.defects.append(entry)

    def _close(self, trace: Trace, outcome: str) -> Trace:
        with self._lock:
            if trace.ended_at is not None:
                self._defect(trace, "TRACE_CLOSED_TWICE",
                             f"close({outcome!r}) was called on a trace already closed as "
                             f"{trace.outcome!r}; the first outcome stands", {})
                return trace
            trace.ended_at = self._clock()
            trace.outcome = outcome
        self._append(trace, "closed", f"closed: {outcome}", {"outcome": outcome})
        return trace

    # --- reading: internal, unfiltered -------------------------------------------------
    def list(self, limit: int = 50) -> dict[str, Any]:
        """INTERNAL: every held trace, unfiltered. Never serve this to an HTTP caller."""
        with self._lock:
            traces = list(self._traces)[-max(1, int(limit)):][::-1]
            return {
                "traces": [t.summary() for t in traces],
                "total_held": len(self._traces),
                "capacity": self._traces.maxlen,
                "evicted": self.evicted,
            }

    def get(self, request_id: str) -> Optional[Trace]:
        """INTERNAL: one trace by id, unfiltered. Never serve this to an HTTP caller."""
        with self._lock:
            return self._by_id.get(request_id)

    # --- reading: viewer-scoped (what `/trace` serves) ---------------------------------
    @staticmethod
    def visible_to(trace: Trace, principal_id: Optional[str], *, admin: bool) -> bool:
        """Owner-only; an explicit admin sees everything. An ownerless trace is admin-only."""
        if admin:
            return True
        owner = trace.owner
        return owner is not None and bool(principal_id) and owner == principal_id

    def list_for(self, principal_id: Optional[str], *, admin: bool = False,
                 limit: int = 50) -> dict[str, Any]:
        """The traces this viewer may read, newest first.

        The counts are the VIEWER's: `total_held` is how many of the held traces they may read,
        and `evicted` how many of theirs were evicted. A non-admin is never told how many traces
        other principals have (Codex f1 #8).
        """
        with self._lock:
            mine = [t for t in self._traces if self.visible_to(t, principal_id, admin=admin)]
            page = mine[-max(1, int(limit)):][::-1]
            evicted = (self.evicted if admin
                       else (self._evicted_by_owner.get(principal_id, 0) if principal_id else 0))
            return {
                "traces": [t.summary() for t in page],
                "total_held": len(mine),
                "capacity": self._traces.maxlen,
                # Reported, not silent. An audit surface that forgot without saying so would be
                # worse than one that says it did.
                "evicted": evicted,
                "scope": "all" if admin else "own",
            }

    def get_for(self, request_id: str, principal_id: Optional[str], *,
                admin: bool = False) -> Optional[Trace]:
        """One trace, or `None` -- for "no such trace" AND "not yours" alike, so the route cannot
        tell a caller whether somebody else's trace exists (Codex f1 #8)."""
        with self._lock:
            trace = self._by_id.get(request_id)
            if trace is None or not self.visible_to(trace, principal_id, admin=admin):
                return None
            return trace


def recorder_for(app: Any) -> TraceRecorder:
    """The app's recorder, created on first use.

    A route may be mounted onto a bare `FastAPI` in a test without the gateway lifespan having
    run, so this must not depend on startup having created it.
    """
    existing = getattr(app.state, "traces", None)
    if isinstance(existing, TraceRecorder):
        return existing
    created = TraceRecorder()
    app.state.traces = created
    return created
