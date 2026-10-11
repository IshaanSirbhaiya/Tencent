"""The materialiser the gateway attaches at boot (ROADMAP Phase F, work items 1–2).

`PlatformMaterialiser` is the service behind `/materialise`: it builds a `PlatformReader` over the
live stores for each run, a `DataAgent` catalogue from what those stores actually hold, and runs
either pathway —

* ``{"plan": {...}}`` — a `MaterialisationPlan` the caller wrote: compiled, executed, receipted.
  ``shaped_by: plan``; re-executing it against the same pinned stores reproduces ``content_hash``.
* ``{"request": "..."}`` — English. The data agent authors a plan against the live catalogue
  (a model through the harness when one is configured, otherwise the deterministic gold planner),
  the compiler accepts or refuses it, the executor builds it. ``shaped_by: agent``.

Outputs land in ``<WALNUT_MATERIALISE_DIR or var/materialise>/<plan_hash>/``: the dataset file,
``receipt.json`` (the `Receipt` plus a platform block: produced_by, sources pinned, pointer
check), ``plan.yaml`` and, on the plan pathway, the Croissant file.

**No shape is built here.** The shapes offered are exactly the executor's builders; a shape whose
builder refuses (``graph_tensor``, ``conversational``) is listed as NOT built, with the refusal's
own first line as the reason, and a request for it refuses by name.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, get_args

from .agent import AgentCatalogue, DataAgent, EntityCatalogue, PLAN_TASK_CLASS, gold_harness
from .errors import (
    MaterialisationError,
    PlanNeedsInput,
    ShapeNotImplemented,
    SourcePinViolated,
)
from .executor import _BUILDERS, EXECUTOR_VERSION, materialise
from .plan import MaterialisationPlan, ShapeKind
from .platform_reader import DATABRIDGE_ROOT, PlatformReader
from .questions import answers_problem, clean_answers, question_items
from .shapes.chunked import CHUNK_COLUMNS

#: The backend string the agent stamps into `determinism.executor.backend`.
PLATFORM_BACKEND = "walnut-platform/sqlite"
DEFAULT_PURPOSE = "dataset_materialisation"
DEFAULT_LAWFUL_BASIS = "legitimate_interests"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

_log = logging.getLogger("databridge.materialise")


def _error_ref(context: str, exc: BaseException) -> str:
    """R-028 TIDY #4 (CONS_BRIEF standard 4): an opaque reference for a failure whose text is
    never shown. The exception goes to the server log under the reference; the public surface
    carries a FIXED message and the reference."""
    ref = f"ERR-{uuid.uuid4().hex[:10]}"
    _log.warning("[%s] %s", ref, context, exc_info=(type(exc), exc, exc.__traceback__))
    return ref


def _model_failed_note(exc: BaseException) -> str:
    ref = _error_ref("the model could not author a plan", exc)
    return (f"the model could not author a plan (reference {ref}); the offline gold planner "
            f"authored it instead")
_FILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,255}$")

#: The chunk corpus's own coordinate columns (`shapes/chunked.CHUNK_COLUMNS` minus the text).
#: Each is MATCHED against what its pointer encodes (`_chunk_expectations`), never exempted.
CHUNK_COORDINATES = frozenset(c for c in CHUNK_COLUMNS if c != "text")


def materialise_root() -> Path:
    v = os.environ.get("WALNUT_MATERIALISE_DIR")
    return Path(v) if v else DATABRIDGE_ROOT / "var" / "materialise"


def shape_status() -> list[dict]:
    """Every shape the plan vocabulary names, and whether this build PRODUCES it.

    Derived from the executor's own builder table: a builder that lives in the `unimplemented`
    module refuses by design, and its docstring's first line is the reason. Nothing is listed by
    hand, so adding a builder flips its entry with no change here.
    """
    out = []
    for kind in get_args(ShapeKind):
        builder = _BUILDERS.get(kind)
        if builder is None:
            out.append({"shape": kind, "built": False,
                        "reason": "no builder in this package"})
            continue
        refuses = builder.__module__.endswith(".unimplemented")
        doc = (builder.__doc__ or "").strip().splitlines()
        out.append({"shape": kind, "built": not refuses,
                    "reason": (doc[0] if doc else "refuses by name") if refuses else None})
    return out


def build_catalogue(reader: PlatformReader, *, principal: str,
                    purpose: str = DEFAULT_PURPOSE,
                    lawful_basis: str = DEFAULT_LAWFUL_BASIS) -> AgentCatalogue:
    """The data agent's view of the LIVE platform: every entity the reader can read, with the axes
    it can answer. `system_time_field` is whatever the store genuinely records — never a date
    column nominated because it was nearby."""
    entities = []
    for e in reader.entities().values():
        entities.append(EntityCatalogue(
            name=e.name, fields=e.fields, key=e.key, valid_time_field=e.valid_time_field,
            system_time_field=e.system_time_field, label_candidates=e.label_candidates,
            feature_candidates=e.feature_candidates, text_field=e.text_field))
    try:
        packs = tuple(reader.vocabulary().packs)
    except Exception:                                            # noqa: BLE001
        packs = ()
    return AgentCatalogue(source_id=reader.source_id, entities=tuple(entities), packs=packs,
                          purpose=purpose, lawful_basis=lawful_basis, principal_actor=principal)


class RunRefused(Exception):
    """A run refused before or during compilation/execution, with a machine-readable code."""

    def __init__(self, code: str, message: str, *,
                 question_items: Optional[list[dict]] = None,
                 error_ref: Optional[str] = None, reason_code: Optional[str] = None,
                 nearest_alternative: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        #: E2E5 W-11: the package's specific code when ``code`` is the standard one for the cause
        self.reason_code = reason_code
        #: E2E5 W-11: the flow that DOES build what was asked for, when this one cannot
        self.nearest_alternative = nearest_alternative
        #: R-028 TIDY #4: set when the cause was a failure whose text is only in the server log.
        self.error_ref = error_ref
        #: E2E run 3, P-10: the planner's questions as ``[{id, text}]``. Answer them by posting
        #: the SAME request with ``answers: {id: text}``.
        self.question_items = list(question_items or [])

    def refusal(self) -> dict:
        """The machine-readable refusal: code, message and — when the planner asked — the
        questions with their ids."""
        out: dict = {"code": self.code, "message": self.message}
        if self.error_ref:
            out["error_ref"] = self.error_ref
        if self.reason_code:
            out["reason_code"] = self.reason_code
        if self.nearest_alternative:
            out["nearest_alternative"] = self.nearest_alternative
        if self.question_items:
            out["question_items"] = self.question_items
            out["answer_with"] = {"answers": {q["id"]: "" for q in self.question_items},
                                  "note": "post the same `request` again with `answers` "
                                          "{question id: your answer}"}
        return out


#: E2E5 W-11: the planner's refusals answer with a STANDARD code (upper snake case, like every
#: other refusal of this API); the exception class survives as ``reason_code``.
PLANNER_CODES = {"PlanNeedsInput": "PLAN_NEEDS_INPUT",
                 "ShapeNotImplemented": "SHAPE_NOT_IMPLEMENTED"}
#: ...and names the flow that builds a plain, unlabelled table, which this planner does not:
#: a raw TABLE DESIGN (`delegated.py`), built by delegation with a pointer per cell.
RAW_DESIGN_ALTERNATIVE = (
    "a raw table design: Data → Raw dataset by delegation (POST /materialise/designs) builds one "
    "row per source row with every cell cited, and needs no label or decision instant")
#: The same, said in the refusal's sentence (no route path: a public sentence names no path).
RAW_DESIGN_SENTENCE = (
    "For a plain table without a label, use a raw table design (Data → Raw dataset by "
    "delegation): one row per source row, every cell cited, no label or decision instant needed.")


def _asked(exc: Exception, message: Optional[str] = None) -> RunRefused:
    """A planner refusal as a `RunRefused`, carrying any question it asked with its id, a standard
    code, and the raw-design flow as the nearest alternative (E2E5 W-11)."""
    qs = list(getattr(exc, "questions", ()) or ())
    name = type(exc).__name__
    text = message or str(exc)
    return RunRefused(PLANNER_CODES.get(name, name),
                      f"{text} {RAW_DESIGN_SENTENCE}",
                      question_items=question_items(qs) if qs else None,
                      reason_code=name, nearest_alternative=RAW_DESIGN_ALTERNATIVE)


class PlatformMaterialiser:
    """`plans()` + `run()` (the gateway's `Materialiser` protocol) over the live platform."""

    def __init__(self, *, store_provider: Callable[[], Any] = lambda: None,
                 harness: Any = None, out_root: Optional[Path] = None,
                 facts_root: Optional[Path] = None, passages_root: Optional[Path] = None,
                 capture_root: Optional[Path] = None, vocabulary: Any = None,
                 purpose: str = DEFAULT_PURPOSE, lawful_basis: str = DEFAULT_LAWFUL_BASIS,
                 verify_sample: int = 64, register_emissions: bool = True,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 identifier_disclosure_approver: Optional[Callable[..., Optional[str]]] = None
                 ) -> None:
        # `identifier_disclosure_approver(principal=, purpose=, lawful_basis=, grant_id=,
        # plan_hash=) -> approving principal id | None`: the server-side authority for
        # `pii.direct_identifiers: retain` (R-021 Finding 2). None -> every `retain` refuses.
        self._identifier_approver = identifier_disclosure_approver
        self._store_provider = store_provider
        self._harness = harness
        self._out_root = Path(out_root) if out_root else None
        self._facts_root, self._passages_root = facts_root, passages_root
        self._capture_root = capture_root
        self._vocabulary = vocabulary
        self._purpose, self._lawful_basis = purpose, lawful_basis
        self._verify_sample = verify_sample
        self._register = register_emissions
        self._clock = clock

    # --- configuration ------------------------------------------------------------------
    @property
    def out_root(self) -> Path:
        return self._out_root or materialise_root()

    def reader(self, *, system_as_of: Optional[datetime] = None, identifiers: str = "suppress",
               principal: str = "materialiser", purpose: str = "") -> PlatformReader:
        return PlatformReader(store=self._store_provider(), vocabulary=self._vocabulary,
                              facts_root=self._facts_root, passages_root=self._passages_root,
                              capture_root=self._capture_root, system_as_of=system_as_of,
                              identifiers=identifiers, principal=principal,
                              purpose=purpose or self._purpose)

    def pins_for(self, plan: MaterialisationPlan) -> tuple:
        """The live pins for exactly the sources ``plan`` reads, taken at the instant the plan
        is read at — what a caller puts in ``temporal.source_snapshots`` (round 2, Finding 3)."""
        as_of, _ = self._reader_instant(plan)
        reader = self.reader(system_as_of=as_of)
        needed = set(reader.sources_for(plan_entities(plan)).values())
        return tuple(s for s in reader.snapshots() if s.source_id in needed)

    # --- the protocol -------------------------------------------------------------------
    def plans(self, *, by: Optional[str] = None) -> list[dict]:
        """The shapes, marked built / not built, plus the receipts on disk — only ``by``'s own
        when a principal is given (R-021 Finding 1; the gateway also filters)."""
        shapes = [{"id": f"shape:{s['shape']}", **s} for s in shape_status()]
        return shapes + [{"id": r["plan_hash"], "shape": r.get("shape"), "built": True,
                          "receipt": True, **r} for r in self.receipts(by=by)]

    def catalogue(self, *, principal: str = "materialiser",
                  as_of: Optional[datetime] = None) -> dict:
        """What can be planned against, read at ``as_of`` (default: now), with the live SOURCE
        PINS at that instant: a submitted plan must pin every source it reads (R-021 round 2,
        Finding 3), and these are the pins a plan built at ``as_of`` carries."""
        reader = self.reader(principal=principal, system_as_of=as_of)
        entities = reader.entities()
        return {"system_as_of": reader.system_as_of.isoformat(),
                "entities": [e.as_dict() for e in entities.values()],
                "excluded": reader.excluded(),
                "sources": [s.model_dump(mode="json") for s in reader.snapshots()],
                "shapes": shape_status()}

    def request_problem(self, body: dict) -> Optional[str]:
        """Why ``body`` is refused before any job starts, or None — so a malformed ``answers``
        is a synchronous 422 at the edge, not a job that refuses (E2E run 3, P-10)."""
        body = body or {}
        answers = body.get("answers")
        if answers is not None and body.get("plan") is not None:
            return ("`answers` answer the planner's questions about an English `request`; a "
                    "submitted `plan` asks none")
        return answers_problem(answers)

    def run(self, body: dict, *, by: str = "materialiser",
            progress: Optional[Callable[[dict], None]] = None) -> dict:
        """Run one materialisation synchronously. The gateway calls this on a worker thread."""
        emit = progress or (lambda _e: None)
        body = dict(body or {})
        request = body.get("request")
        plan_body = body.get("plan")
        if bool(request) == bool(plan_body):
            raise RunRefused("BAD_REQUEST", "send exactly one of `request` (English) or `plan` "
                                            "(a MaterialisationPlan document)")
        if request is not None and not isinstance(request, str):
            raise RunRefused("BAD_REQUEST", "`request` must be a string")
        answers = body.get("answers")
        bad = self.request_problem(body)
        if bad:
            raise RunRefused("BAD_REQUEST", bad)
        emit({"stage": "reading_catalogue", "at": _now()})
        try:
            if plan_body is not None:
                return self._run_plan(plan_body, by=by, emit=emit)
            return self._run_request(str(request), by=by, emit=emit,
                                     answers=clean_answers(answers))
        except RunRefused:
            raise
        except MaterialisationError as exc:
            raise _asked(exc) from exc

    # --- guards (R-021) ------------------------------------------------------------------
    def _authorise_identifiers(self, plan: MaterialisationPlan, by: str) -> Optional[str]:
        """R-021 Finding 2: a plan's ``pii.direct_identifiers: retain`` is a REQUEST, not
        authority. Disclosure needs a server-side approver who is not the caller.

        Who may approve is not yet defined for this deployment (REAL-deferred in R-021), so with
        no approver configured every ``retain`` is refused — fail closed. An approver, when one is
        wired, is asked with the caller's principal, purpose, lawful basis and grant, and must
        name a principal OTHER than the caller: nobody authorises their own disclosure.
        """
        if plan.policy.pii.direct_identifiers != "retain":
            return None
        why = ("a caller cannot authorise their own direct-identifier disclosure: "
               "policy.pii.direct_identifiers=retain needs an approval from a separate, "
               "authorised principal")
        if self._identifier_approver is None:
            raise RunRefused("IDENTIFIER_DISCLOSURE_NOT_AUTHORISED",
                             f"{why}, and this deployment defines no approver. Re-submit with "
                             f"direct_identifiers=suppress (identifiers are then Withheld, by name)")
        try:
            approver = self._identifier_approver(
                principal=by, purpose=plan.policy.purpose,
                lawful_basis=plan.policy.lawful_basis,
                grant_id=plan.policy.principal.grant_id, plan_hash=plan.plan_hash)
        except Exception as exc:                                 # noqa: BLE001
            ref = _error_ref("identifier-disclosure approver failed", exc)
            raise RunRefused("IDENTIFIER_DISCLOSURE_NOT_AUTHORISED",
                             f"{why}; the approver could not be asked (reference {ref})",
                             error_ref=ref) from exc
        if not approver or str(approver) == by:
            raise RunRefused("IDENTIFIER_DISCLOSURE_NOT_AUTHORISED",
                             f"{why}; the approver returned "
                             f"{'no approval' if not approver else 'the caller themself'}")
        return str(approver)

    def _reader_instant(self, plan: MaterialisationPlan) -> tuple[datetime, list[str]]:
        """R-021 Finding 10: the instant the stores are read at, chosen by SHAPE.

        A chunk corpus is read at ``corpus_as_of``; a tabular build at ``build_time``. The other
        field, when also given, must agree — two different cuts in one plan are refused, not
        resolved by precedence. A fixed feature instant later than the read instant would ask
        for beliefs the read cannot see, and is refused too.
        """
        t = plan.temporal
        if plan.shape.kind == "chunk_corpus":
            primary, other = ("corpus_as_of", t.corpus_as_of), ("build_time", t.build_time)
        else:
            primary, other = ("build_time", t.build_time), ("corpus_as_of", t.corpus_as_of)
        if primary[1] is not None and other[1] is not None and primary[1] != other[1]:
            raise RunRefused(
                "PLAN_TEMPORAL_INCONSISTENT",
                f"a {plan.shape.kind} plan is read at temporal.{primary[0]}={primary[1]}, and it "
                f"also declares temporal.{other[0]}={other[1]}. One plan, one read instant")
        as_of = primary[1] or other[1] or t.feature_as_of_fixed
        notes: list[str] = []
        if as_of is None:
            as_of = self._clock()
            notes.append(f"the plan pins no build instant; the stores were read as of {as_of}")
        if t.feature_as_of_fixed is not None and t.feature_as_of_fixed > as_of:
            raise RunRefused(
                "PLAN_TEMPORAL_INCONSISTENT",
                f"temporal.feature_as_of_fixed={t.feature_as_of_fixed} is later than the read "
                f"instant {as_of}: features cannot be cut at an instant the read cannot see")
        return as_of, notes

    # --- pathways -----------------------------------------------------------------------
    def _run_plan(self, plan_body: dict, *, by: str, emit) -> dict:
        try:
            plan = MaterialisationPlan.model_validate(plan_body)
        except Exception as exc:                                 # noqa: BLE001
            ref = _error_ref("submitted plan failed validation", exc)
            raise RunRefused("PLAN_INVALID", f"the submitted plan is not a valid "
                                             f"MaterialisationPlan (reference {ref})",
                             error_ref=ref) from exc
        if plan.policy.principal.actor != by:
            # A plan names the principal its reads run as. Running it as somebody else would put
            # one person's name on another person's disclosure decisions.
            raise RunRefused(
                "PRINCIPAL_MISMATCH",
                f"the plan's policy.principal.actor is {plan.policy.principal.actor!r} and the "
                f"signed-in principal is {by!r}. A plan runs as the principal it names, or not "
                f"at all")
        as_of, notes = self._reader_instant(plan)
        approved_by = self._authorise_identifiers(plan, by)
        reader = self.reader(system_as_of=as_of, identifiers=plan.policy.pii.direct_identifiers,
                             principal=by, purpose=plan.policy.purpose)
        # R-021 Finding 3: the pins bind. Compared BEFORE anything is read — and (round 2,
        # Finding 3) they must COVER every source the plan reads: an unpinned source is refused,
        # not read.
        pinned = reader.snapshots()
        try:
            required = reader.sources_for(plan_entities(plan))
        except KeyError as exc:
            ref = _error_ref("plan names an entity the platform cannot read", exc)
            raise RunRefused("PLAN_INVALID", f"the plan reads an entity this platform does not "
                                             f"hold (reference {ref})", error_ref=ref) from exc
        check_pins(plan.temporal.source_snapshots, pinned, required=required)
        emit({"stage": "materialising", "shape": plan.shape.kind, "plan_hash": plan.plan_hash,
              "at": _now()})
        staging = self._staging()
        try:
            built = materialise(plan, reader, shaped_by="plan", out_dir=staging,
                                verify_sample=self._verify_sample,
                                extra_notes=tuple(notes))
            # ... and AGAIN before publication: a store that moved mid-run refuses the run.
            check_unchanged(pinned, reader.snapshots())
            produced_by = {"agent": "materialiser", "agent_version": EXECUTOR_VERSION,
                           "task_class": None, "model": None, "prompt_version": None,
                           "run_id": staging.name.replace(".staging-", ""), "at": _now()}
            return self._finish(built, reader, staging, produced_by=produced_by, by=by,
                                request=None, emit=emit, sources=pinned,
                                approved_by=approved_by)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _run_request(self, request: str, *, by: str, emit,
                     answers: Optional[dict[str, str]] = None) -> dict:
        build_time = self._clock().replace(microsecond=0)
        reader = self.reader(system_as_of=build_time, principal=by)
        catalogue = build_catalogue(reader, principal=by, purpose=self._purpose,
                                    lawful_basis=self._lawful_basis)
        if not catalogue.entities:
            raise RunRefused("EMPTY_CATALOGUE",
                             "the platform holds nothing the materialiser can read yet — no "
                             "graph kinds with rows, no facts tables, no passage stores")
        snapshots = reader.snapshots()
        notes: list[str] = []
        harness, planner = self._harness, "model"
        if harness is None:
            notes.append("no model is configured; the deterministic offline gold planner "
                         "authored this plan (a rule over the live catalogue, not a recorded "
                         "answer)")
            harness, planner = gold_harness(), "offline gold planner"
        agent = DataAgent(harness, catalogue, build_time=build_time, snapshots=snapshots,
                          backend=PLATFORM_BACKEND)
        emit({"stage": "authoring_plan", "planner": planner, "at": _now()})
        answers = answers or {}
        try:
            authored = agent.author(request, answers=answers)
        except (PlanNeedsInput, ShapeNotImplemented) as exc:
            # The planner ASKED (E2E run 2, N-07), or the request names a shape this build does
            # not produce. Either is an answer to the requester, not a planner failure, so it is
            # never papered over by a second planner: the refusal carries the question — with an
            # id the requester answers it by (E2E run 3, P-10).
            raise _asked(exc) from exc
        except Exception as exc:                                 # noqa: BLE001
            if planner != "model":
                ref = _error_ref("the offline planner produced no usable draft", exc)
                raise RunRefused("PLAN_NOT_AUTHORED", f"the planner produced no usable draft "
                                                      f"(reference {ref})",
                                 error_ref=ref) from exc
            # The model could not be asked (or answered off-schema). Say so and let the offline
            # planner author instead — the compiler gates its plan exactly as it gates a model's.
            notes.append(_model_failed_note(exc))
            harness, planner = gold_harness(), "offline gold planner"
            agent = DataAgent(harness, catalogue, build_time=build_time, snapshots=snapshots,
                              backend=PLATFORM_BACKEND)
            try:
                authored = agent.author(request, answers=answers)
            except (PlanNeedsInput, ShapeNotImplemented) as asked:
                raise _asked(asked, f"{asked} (the model could not author a plan)") from asked
        # The agent's plan carries the catalogue's reader policy, which is always `suppress`; a
        # model asking for `retain` meets the same gate a submitted plan does (Finding 2).
        approved_by = self._authorise_identifiers(authored.plan, by)
        if authored.plan.policy.pii.direct_identifiers != reader.identifiers:
            reader = self.reader(system_as_of=build_time,
                                 identifiers=authored.plan.policy.pii.direct_identifiers,
                                 principal=by)
        staging = self._staging()
        emit({"stage": "materialising", "planner": planner, "shape": authored.plan.shape.kind,
              "plan_hash": authored.plan.plan_hash, "at": _now()})
        try:
            built = agent.materialise(request, reader, out_dir=staging, authored=authored,
                                      verify_sample=self._verify_sample)
            # R-021 Finding 3: the stores the plan was authored against (its pins) are the stores
            # the build read — checked after the build, before publication.
            check_unchanged(snapshots, reader.snapshots())
            event = getattr(agent, "last_event", None)
            produced_by = {"agent": "data_agent", "agent_version": EXECUTOR_VERSION,
                           "task_class": PLAN_TASK_CLASS,
                           # E2E5 W-10: the harness records the routed model as `model_used`
                           "model": ((getattr(event, "model_used", None)
                                      or getattr(event, "model", None)) if planner == "model"
                                     else "offline/gold-planner"),
                           "prompt_version": "data_agent/_PROMPT",
                           "run_id": staging.name.replace(".staging-", ""), "at": _now()}
            if notes:
                receipt = built.receipt.model_copy(
                    update={"notes": built.receipt.notes + tuple(notes)})
                built = type(built)(compiled=built.compiled, dataset=built.dataset,
                                    receipt=receipt)
            return self._finish(built, reader, staging, produced_by=produced_by, by=by,
                                request=request, emit=emit, planner=planner, sources=snapshots,
                                approved_by=approved_by, answers=answers)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # --- output -------------------------------------------------------------------------
    def _staging(self) -> Path:
        root = self.out_root
        root.mkdir(parents=True, exist_ok=True)
        path = root / f".staging-{uuid.uuid4().hex[:12]}"
        path.mkdir()
        return path

    def _finish(self, built, reader: PlatformReader, staging: Path, *, produced_by: dict,
                by: str, request: Optional[str], emit, planner: Optional[str] = None,
                sources=(), approved_by: Optional[str] = None,
                answers: Optional[dict[str, str]] = None) -> dict:
        receipt = built.receipt
        # What the READ found (e.g. passages withheld as O1b transformed disclosures) is only
        # known after the build ran, so it joins the receipt here rather than as a pre-run note.
        read_notes = tuple(n for n in dict.fromkeys(reader.notes) if n not in receipt.notes)
        if read_notes:
            receipt = receipt.model_copy(update={"notes": receipt.notes + read_notes})
        plan = built.plan
        # R-029 TIDY2: a facts read writes nothing; this BUILD publishes the query artefact of
        # every query_cell cell it ships, so the receipt's pointers re-read in a later request
        _persist_artefacts(built.dataset, reader.answers, reader._roots()[0])
        emit({"stage": "checking_pointers", "at": _now()})
        check = pointer_check(built.dataset, reader, plan=built.plan)
        if not (check["all_resolved"] and check["all_match"]):
            # R-021 Finding 4: a full pointer check that fails BLOCKS publication. Nothing is
            # renamed into place, so there is no receipt and no file to download.
            raise RunRefused(
                "POINTER_CHECK_FAILED",
                f"of {check['grounded_cells']} grounded cell(s), "
                f"{check['grounded_cells'] - check['pointers_resolved']} pointer(s) did not "
                f"resolve and {check['grounded_cells'] - check['values_match']} value(s) did not "
                f"match their source; nothing was published. First failures: "
                f"{check['failures'][:5]}")
        leak = leak_check_problem(receipt.missingness_leak_check)
        if leak:
            # The executor refuses a failed check itself; this is the publication gate's own
            # reading of the receipt, so an absent, incomplete or self-contradicting result is
            # never published as a success either.
            raise RunRefused("LEAK_CHECK_FAILED", f"{leak}; nothing was published")
        (staging / "plan.yaml").write_text(plan.to_yaml(), encoding="utf-8")
        # E2E run 2, N-08: the per-cell pointers ship as a downloadable sidecar, so a downloader
        # can verify a cell without access to the platform's stores. The receipt's manifest names
        # it (`pointer_manifest.uri`) and the platform block records its own digest.
        pointer_file = write_pointer_sidecar(built.dataset, plan, staging)
        receipt = receipt.model_copy(update={"pointer_manifest": receipt.pointer_manifest
                                             .model_copy(update={"uri": pointer_file["name"]})})
        final = self.out_root / receipt.plan_hash
        platform = {
            "shape": plan.shape.kind,
            "entity": plan.selection.entity,
            "request": request,
            # E2E run 3, P-10: the answers the plan was authored with — part of what was asked
            "answers": dict(answers or {}),
            "planner": planner,
            "run_by": by,
            "produced_by": produced_by,
            "reproducible": receipt.reproducible,
            "honest_summary": receipt.honest_summary,
            "system_as_of": reader.system_as_of.isoformat(),
            "identifiers": reader.identifiers,
            "identifier_disclosure_approved_by": approved_by,
            # The pins the run READ (taken before the read, re-checked equal after it) — never a
            # later observation of the stores (R-021 Finding 3).
            "sources": [s.model_dump(mode="json") for s in sources],
            "pointer_check": check,
            "pointer_file": pointer_file,
            "unresolved": {"cells": receipt.cells.unresolved,
                           "columns_fully_absent": list(receipt.columns_fully_absent),
                           # E2E run 4, Q-07: what the T_d cut emptied, said as that
                           "columns_refused": list(receipt.columns_refused),
                           "features_unknown_at_decision":
                               dict(receipt.features_unknown_at_decision)},
            "columns": list(built.dataset.columns),
            # FIN-13: why rows went unlabelled/immature, and what would label them
            "label_diagnosis": receipt.label_diagnosis,
        }
        # Paths in the receipt are rewritten to the FINAL directory and made relative to it: the
        # staging directory is gone the moment this returns.
        rel = {}
        for key in ("output_uri", "croissant_uri"):
            value = getattr(receipt, key)
            if value:
                rel[key] = Path(value).name
        receipt = receipt.model_copy(update=rel)
        doc = {"receipt": receipt.model_dump(mode="json"), "platform": platform}
        (staging / "receipt.json").write_text(
            json.dumps(doc, indent=2, sort_keys=True, default=str), encoding="utf-8")
        if not self._publish(staging, final, doc):
            # An identical dataset is already published under this plan_hash: the FIRST receipt
            # stands, untouched (R-021 Finding 8), and its index registration was made then.
            # Registration is idempotent (non-ledger-bound emissions de-duplicate their
            # citations), so a re-run RETRIES it (R-021 round 2, Finding 10): a publication whose
            # registration failed — or whose process died between rename and registration —
            # is recovered by running the identical plan again, and the result says so.
            index = self._register_emission(built.dataset, receipt, final)
            emit({"stage": "done", "plan_hash": receipt.plan_hash, "reused": True, "at": _now()})
            prior = self.receipt(receipt.plan_hash) or doc
            return {**summary_from_doc(prior, files=self.files(receipt.plan_hash)),
                    "reused_prior_run": True, "index_registered": index,
                    "index_registration": self.index_registration(receipt.plan_hash)}
        index = self._register_emission(built.dataset, receipt, final)
        emit({"stage": "done", "plan_hash": receipt.plan_hash, "at": _now()})
        return {**summary(receipt, platform, files=self.files(receipt.plan_hash)),
                "index_registered": index}

    def _publish(self, staging: Path, final: Path, doc: dict) -> bool:
        """Move a staged run into place. **Never deletes a published run** (R-021 Finding 8).

        ``True`` when this run is now published. ``False`` when an identical run (same content,
        shaping and principal) already is — the prior receipt stands. A DIFFERENT dataset under
        an existing ``plan_hash`` is refused: one identity, one dataset.
        """
        if not final.exists():
            try:
                staging.rename(final)
                return True
            except OSError:
                if not final.exists():
                    raise
        prior = self.receipt(final.name) or {}
        pr, pp = prior.get("receipt") or {}, prior.get("platform") or {}
        new_r, new_p = doc["receipt"], doc["platform"]
        same = (pr.get("content_hash") == new_r.get("content_hash")
                and list(pr.get("shaping_directives") or ()) == list(
                    new_r.get("shaping_directives") or ())
                and pp.get("run_by") == new_p.get("run_by"))
        if new_p.get("mode") == RAW_MODE:
            # R-027 f2 #5: a raw run is reused only when its POINTERS are the same too
            same = same and (pp.get("pointer_file") or {}).get("sha256") == (
                new_p.get("pointer_file") or {}).get("sha256") and pp.get(
                "sources_pinned") == new_p.get("sources_pinned")
        if same:
            return False
        raise RunRefused(
            "CONTENT_COLLISION",
            f"plan_hash {final.name} is already published with content_hash "
            f"{pr.get('content_hash')!r} (run by {pp.get('run_by')!r}); this run produced "
            f"{new_r.get('content_hash')!r}. A plan_hash identifies exactly one dataset, and the "
            f"published run is never overwritten")

    def _register_emission(self, dataset, receipt, final: Path) -> dict:
        """Register the SHIPPED pointer set with the reverse index, AFTER publication (R-021
        Finding 7) — so an index entry never names an artefact that does not exist. If the
        registration itself fails, the published run gains an additive
        ``index_registration.json`` saying so (the recoverable half: a re-register can find it),
        and the result reports ``registered: false``; the receipt is not rewritten."""
        store = self._store_provider() if self._register else None
        sink = getattr(store, "reverse_index", None) if store is not None else None
        if sink is None:
            return {"registered": False, "reason": "no reverse index is attached"}
        try:
            sink.record_emission(kind="materialised_dataset", ref=receipt.plan_hash,
                                 pointers=dataset.pointers(), plan_hash=receipt.plan_hash,
                                 ledger_bound=False, emitted_at=receipt.executed_at)
            record = {"registered": True, "at": _now()}
        except Exception as exc:                                 # noqa: BLE001
            ref = _error_ref("reverse-index registration failed", exc)
            record = {"registered": False, "error": "the reverse index refused the registration",
                      "error_ref": ref, "at": _now()}
        # Every attempt, success or failure, is recorded beside the run (round 2, Finding 10), so
        # "never registered" (no record: the process died after publication), "failed" and
        # "recovered" are all visible on the receipt listing — the receipt itself is untouched.
        path = final / "index_registration.json"
        try:
            prior = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            prior = {}
        attempts = list(prior.get("attempts") or ([prior] if prior.get("at") else []))
        attempts.append(record)
        try:
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps({**record, "attempts": attempts}, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
        return record

    def index_registration(self, plan_hash: str) -> dict:
        """Whether a published run's pointers are in the reverse index, as far as this run's
        own record says: ``registered`` true / false, or ``None`` when no attempt is on record
        (published, then the process died before registering — re-run the plan to recover)."""
        if not _HASH_RE.match(plan_hash or ""):
            return {"registered": None}
        path = self.out_root / plan_hash / "index_registration.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            return {"registered": None, "reason": "no registration attempt is on record; "
                                                  "re-running the identical plan registers it"}
        return {"registered": doc.get("registered"), "at": doc.get("at"),
                **({"error": doc["error"]} if doc.get("error") else {})}

    # --- reading back -------------------------------------------------------------------
    def receipts(self, *, by: Optional[str] = None) -> list[dict]:
        """Every run on disk — or, given ``by``, only the runs that principal made (R-021
        Finding 1: the gateway always passes it)."""
        root = self.out_root
        if not root.is_dir():
            return []
        out = []
        for d in sorted(root.iterdir()):
            if not (d.is_dir() and _HASH_RE.match(d.name)):
                continue
            doc = self.receipt(d.name)
            if doc is None:
                continue
            if by is not None and (doc.get("platform") or {}).get("run_by") != by:
                continue
            out.append({**summary_from_doc(doc, files=self.files(d.name)),
                        "index_registration": self.index_registration(d.name)})
        out.sort(key=lambda r: r.get("executed_at") or "", reverse=True)
        return out

    def receipt(self, plan_hash: str) -> Optional[dict]:
        if not _HASH_RE.match(plan_hash or ""):
            return None
        path = self.out_root / plan_hash / "receipt.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None

    def run_by(self, plan_hash: str) -> Optional[str]:
        """The principal a run on disk executed as, or `None` (no such run)."""
        doc = self.receipt(plan_hash)
        return (doc.get("platform") or {}).get("run_by") if doc else None

    def files(self, plan_hash: str) -> list[str]:
        if not _HASH_RE.match(plan_hash or ""):
            return []
        d = self.out_root / plan_hash
        return sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []

    def file_roles(self, plan_hash: str) -> dict[str, str]:
        doc = self.receipt(plan_hash) or {}
        return file_roles(doc, self.files(plan_hash))

    def file_withheld(self, plan_hash: str, name: str) -> Optional[str]:
        """Why a listed file of a run is NOT served, or `None`. A dataset file is served only
        when the run's full pointer check is on record and passed (R-021 Data-page #1): a run
        on disk from before that gate, or one whose check failed, keeps its receipt and plan
        readable and its data withheld."""
        if name not in self.files(plan_hash):
            return None
        role = self.file_roles(plan_hash).get(name)
        if role in ("receipt", "plan"):
            return None
        doc = self.receipt(plan_hash) or {}
        check = (doc.get("platform") or {}).get("pointer_check")
        if not isinstance(check, dict):
            return "the run has no pointer check on record"
        if not (check.get("all_resolved") is True and check.get("all_match") is True):
            return "the run's pointer check did not pass"
        if (doc.get("platform") or {}).get("mode") == RAW_MODE:
            # DSD: a RAW extract says plainly it is not leakage-filtered (its receipt's
            # `statement`); leakage decisions are the engineer's, so there is no leak gate here
            return None
        return leak_check_problem((doc.get("receipt") or {}).get("missingness_leak_check"))

    def file_path(self, plan_hash: str, name: str) -> Optional[Path]:
        """A file of one run, or `None`. The name must be one the directory LISTS — never a path
        joined from input — so there is no traversal to guard against. A dataset file of a run
        whose pointer check is absent or failed is not served (`file_withheld`)."""
        if not _FILE_RE.match(name or "") or name not in self.files(plan_hash):
            return None
        if self.file_withheld(plan_hash, name) is not None:
            return None
        return self.out_root / plan_hash / name

    # --- DSD: table designs and RAW builds through delegation -----------------------------
    def design_store(self):
        from .delegated import DesignStore
        return DesignStore(self.out_root)

    def design_catalogue(self, service: Any, *, allowed: Optional[set] = None):
        """What a design may name: the federated service's measured catalogue, the facts stores,
        the APPROVED mappings and the pack ontology — only the ``allowed`` connections (the
        caller's grant; ``None`` = every registered one)."""
        from .delegated import build_design_catalogue
        return build_design_catalogue(service, vocab=self._vocabulary, facts_dir=self._facts_root,
                                      allowed=allowed)

    def propose_design(self, request: str, *, by: str, service: Any,
                       answers: Optional[dict] = None, allowed: Optional[set] = None) -> dict:
        """English in, a stored PROPOSED table design out (the model through the harness when one
        is configured, else rules), validated against the measured catalogue."""
        from .delegated import propose_design
        if not isinstance(request, str) or not request.strip():
            raise RunRefused("BAD_REQUEST", "describe the dataset you need")
        cat = self.design_catalogue(service, allowed=allowed)
        if not cat.tables:
            raise RunRefused("EMPTY_CATALOGUE", "no adapter agent holds a readable table yet; "
                                                "a design needs at least one")
        design, produced = propose_design(request.strip(), cat, harness=self._harness,
                                          answers=answers)
        return self.design_store().create(design, owner=by, produced_by=produced)

    def designs(self, *, by: str) -> list[dict]:
        return self.design_store().list(owner=by)

    def design(self, design_id: str, *, by: str) -> Optional[dict]:
        doc = self.design_store().get(design_id)
        return doc if doc is not None and doc.get("owner") == by else None

    def edit_design(self, design_id: str, edit: dict, *, by: str, service: Any,
                    rev: int, allowed: Optional[set] = None) -> dict:
        """A person's edit of revision ``rev``, re-validated; it becomes a NEW immutable revision
        (compare-and-set: an edit of a superseded revision is refused) and clears the approval."""
        from .delegated import DesignRefused, apply_edit, design_of
        store = self.design_store()
        if not isinstance(edit, dict):
            raise DesignRefused("BAD_REQUEST", "an edit is an object", 422)
        _events, doc = store._current(design_id, by)
        if doc["rev"] != rev:
            raise DesignRefused("STALE_REVISION", f"the design is at revision {doc['rev']}, this "
                                                  f"edit was made on {rev}; reload it", 409)
        design = apply_edit(design_of(doc), edit,
                            self.design_catalogue(service, allowed=allowed))
        return store.replace(design_id, design, by=by, expected_rev=rev)

    def approve_design(self, design_id: str, *, digest: str, principal: Any, by: str,
                       rev: Optional[int] = None) -> dict:
        """``principal`` is the AUTHENTICATED principal object (never a name)."""
        from .delegated import approve
        return approve(self.design_store(), design_id, principal=principal, digest=digest,
                       owner=by, rev=rev)

    def build_design(self, design_id: str, *, digest: str, by: str, service: Any,
                     admission: Any, progress: Optional[Callable[[dict], None]] = None,
                     allowed: Optional[set] = None, actor: Optional[dict] = None) -> dict:
        """Build the APPROVED design at exactly ``digest`` by delegation; publish the raw table,
        its receipt and the per-cell pointer sidecar after a full live pointer check. ``actor``
        is the common actor object of the signed-in person who asked for the build (CONS_BRIEF
        §1), recorded on the sealed ``built`` event."""
        import hashlib
        from types import SimpleNamespace

        from .delegated import (MODE, RAW_STATEMENT, DesignDraft, DesignRefused,
                                approved_design, build_raw, validate_design)
        emit = progress or (lambda _e: None)
        # R-029 codex r1 #4: the actor object is REQUIRED, and checked BEFORE anything is read or
        # published (the sealed `built` event records it last)
        from databridge_core.decision_ledger import DecisionRefused as _DecisionRefused
        from databridge_core.decision_ledger import actor_of as _actor_of
        if not actor:
            raise DesignRefused("APPROVER_NOT_AUTHENTICATED", "a build names the signed-in person "
                                                              "running it (its actor object)", 403)
        try:
            actor = _actor_of(actor)
        except _DecisionRefused:
            raise DesignRefused("APPROVER_NOT_AUTHENTICATED", "a build names the signed-in person "
                                                              "running it (its actor object)",
                                403) from None
        if actor["principal_id"] != by:
            raise DesignRefused("APPROVER_NOT_AUTHENTICATED", "the build's actor is not the "
                                                              "person running it", 403)
        store = self.design_store()
        doc, design = approved_design(store, design_id, digest=digest, owner=by)
        approval = dict(doc["approval"])

        def still_holds() -> Any:
            """The EXACT approval (same sealed event, revision, digest) is still the latest, and
            the catalogue — links, approvals, columns — still validates the design unchanged."""
            now, _d = approved_design(store, design_id, digest=digest, owner=by)
            if now["rev"] != doc["rev"] or (now.get("approval") or {}).get("record_digest") != \
                    approval.get("record_digest"):
                raise DesignRefused("DESIGN_NOT_APPROVED", "the design changed or was re-approved "
                                                           "while it was being built; nothing was "
                                                           "published")
            c = self.design_catalogue(service, allowed=allowed)
            body = design.model_dump(mode="json")
            body["label"] = design.label.model_dump() if design.label else None
            # the approved design's own filters are the APPLIED ones (a time scope included)
            if validate_design(DesignDraft(**body), c, request=design.request,
                               time_scope=True).digest != design.digest:
                raise DesignRefused("DESIGN_STALE", "the catalogue no longer supports the "
                                                    "approved design exactly (a link, column or "
                                                    "approval changed); open it, review and "
                                                    "approve again")
            return c
        cat = still_holds()
        emit({"stage": "delegating", "design": design_id, "at": _now()})
        with _capturing(service) as answers:
            built = build_raw(design, cat, service, admission=admission, by=by,
                              facts_dir=self._facts_root, emit=emit)
        # The facts store's reads write nothing (R-029 TIDY2: `count_query` is a pure read); a
        # BUILD is a write, and the query artefacts its SHIPPED cells resolve through are
        # published by it, explicitly, before any pointer is checked
        _persist_artefacts(built.dataset, answers, self._facts_root)
        # R-027 f2 #3: the source snapshots this build READ (each facts store's content hash, as
        # its delegate's statements were bound to it); publication re-checks them
        # R-029 codex r1 #1: the build's OWN pins — one snapshot per connection, proven by
        # `build_raw` (never "the last snapshot seen" per connection)
        pins = dict(built.pins)

        def check_pointers() -> dict:
            got = pointer_check(built.dataset, self.reader(principal=by))
            if not (got["all_resolved"] and got["all_match"]):
                raise RunRefused(
                    "POINTER_CHECK_FAILED",
                    f"of {got['grounded_cells']} grounded cell(s), "
                    f"{got['grounded_cells'] - got['pointers_resolved']} did not re-read and "
                    f"{got['grounded_cells'] - got['values_match']} did not match; nothing was "
                    f"published. First failures: {got['failures'][:5]}")
            return got

        def check_pins() -> None:
            from databridge_mcp_ingest.tabular.facts import facts_path, sha256_file
            moved = sorted(c for c, h in pins.items()
                           if not facts_path(c, self._facts_root).is_file()
                           or sha256_file(facts_path(c, self._facts_root)) != h)
            if moved:
                raise RunRefused("SOURCE_MOVED", f"source(s) {moved} changed while the build ran; "
                                                 f"its pointers would name content that no "
                                                 f"longer exists, so nothing was published")
        emit({"stage": "checking_pointers", "at": _now()})
        check = check_pointers()
        stem = re.sub(r"[^A-Za-z0-9_]", "_", f"raw_{design.grain.entity}")[:60]
        staging = self._staging()
        try:
            csv_name, pq_name = f"{stem}.csv", f"{stem}.parquet"
            write_raw_csv(built.dataset, staging / csv_name)
            outputs = [csv_name]
            pq_note = write_raw_parquet(built.dataset, staging / pq_name)
            if pq_note is None:
                outputs.append(pq_name)
            shim = SimpleNamespace(output_stem=stem, determinism=SimpleNamespace(
                row_order=(design.columns[0].name,)))
            pointer_file = write_pointer_sidecar(built.dataset, shim, staging)
            (staging / "design.json").write_text(json.dumps(
                {"design_id": design_id, "digest": design.digest,
                 "approval": doc.get("approval"), "design": design.model_dump(mode="json")},
                indent=2, sort_keys=True, default=str), encoding="utf-8")
            # R-027 f2 #5: the build's identity covers its POINTER provenance — the sidecar's
            # digest and the source snapshots — not only its values
            content = built.receipt["content_hash"]
            build_id = hashlib.sha256(json.dumps(
                {"design": design.digest, "content": content, "by": by,
                 "pointers": pointer_file["sha256"], "sources": pins},
                sort_keys=True).encode()).hexdigest()
            final = self.out_root / build_id
            # R-027 f2 #2/#3: ONE transaction under the design's cross-process lock — the exact
            # approval re-checked, the sources' pins and EVERY pointer re-read against the staged
            # files, the publication and its `built` event. An edit waits for it.
            with store.transaction(design_id):
                still_holds()
                check_pins()
                check = check_pointers()
                check_pins()
                executed = _now()
                receipt = {**built.receipt, "plan_hash": build_id, "shaped_by": "design",
                           "executed_at": executed, "output_uri": csv_name,
                           "output_files": outputs,
                           "pointer_manifest": {"uri": pointer_file["name"],
                                                "sha256": pointer_file["sha256"],
                                                "pointers": check["grounded_cells"]},
                           "sources_pinned": pins,
                           "notes": [RAW_STATEMENT, *([pq_note] if pq_note else [])],
                           "design_id": design_id, "design_rev": doc["rev"],
                           "approved_by": (doc.get("approval") or {}).get("principal_id")}
                cells = built.receipt["cells"]
                honest = (f"RAW extract ({RAW_STATEMENT}): {built.dataset.n_rows} row(s), one "
                          f"per {design.grain.entity} row; every grounded cell re-read live at "
                          f"publication ({check['values_match']}/{check['grounded_cells']}); "
                          f"{cells.get('policy_suppressed', 0)} withheld, "
                          f"{cells.get('unresolved', 0)} absent")
                platform = {"mode": MODE, "shape": "raw_table", "entity": design.grain.entity,
                            "request": design.request, "run_by": by, "design_id": design_id,
                            "design_digest": design.digest,
                            "produced_by": {"agent": "main_agent", "by": "rules",
                                            "method": "delegation", "model": None,
                                            "design_produced_by": doc.get("produced_by"),
                                            "at": executed},
                            "reproducible": False, "honest_summary": honest,
                            "pointer_check": check, "pointer_file": pointer_file,
                            "sources_pinned": pins,
                            "columns": list(built.dataset.columns),
                            "unresolved": {"cells": cells.get("unresolved", 0)}}
                out = {"receipt": receipt, "platform": platform}
                (staging / "receipt.json").write_text(json.dumps(
                    out, indent=2, sort_keys=True, default=str), encoding="utf-8")
                published = self._publish(staging, final, out)
                store.record_build(design_id, by=by, build_id=build_id, rev=doc["rev"],
                                   digest=digest, actor=actor)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        # E2E5 W-10: the index takes the build INSTANT (a datetime), not its text
        index = self._register_emission(built.dataset, SimpleNamespace(
            plan_hash=build_id, executed_at=datetime.fromisoformat(executed)), final)
        emit({"stage": "done", "plan_hash": build_id, "at": _now()})
        prior = self.receipt(build_id) or out
        return {**summary_from_doc(prior, files=self.files(build_id)), "design_id": design_id,
                "reused_prior_run": not published, "index_registered": index}

    def plan_verification(self, plan_hash: str) -> Optional[dict]:
        """Server-side check that a run's ``plan.yaml`` IS the plan with this ``plan_hash``
        (R-021 Data-page #5): the file is parsed back into a `MaterialisationPlan` and its
        canonical hash recomputed — the browser cannot do that. `None` when there is no run."""
        if not _HASH_RE.match(plan_hash or ""):
            return None
        path = self.out_root / plan_hash / "plan.yaml"
        if not path.is_file():
            return None
        import yaml

        try:
            body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            declared = ((body.pop("hashes", None) or {}).get("plan_hash"))
            body.pop("id", None)
            recomputed = MaterialisationPlan.model_validate(body).plan_hash
        except Exception as exc:                                 # noqa: BLE001
            ref = _error_ref("published plan.yaml could not be re-read", exc)
            return {"plan_hash": plan_hash, "recomputed": None, "matches": False,
                    "error": "the published plan could not be read back as a plan",
                    "error_ref": ref}
        out = {"plan_hash": plan_hash, "recomputed": recomputed,
               "matches": recomputed == plan_hash and declared in (None, plan_hash)}
        if declared not in (None, plan_hash):
            out["declared"] = declared
        return out


@contextlib.contextmanager
def _capturing(service: Any):
    """For the duration of one build, the federated ``service``'s facts store also hands back
    every answer it gave (the answers carry the query artefacts their pointers need). The store is
    restored afterwards. (The gateway builds each design on its own service; were one shared and
    built twice at once, a read missed here leaves its pointers unresolvable, and the publication's
    pointer check refuses — it never ships them.)"""
    answers: list = []
    facts = getattr(service, "facts", None)
    if facts is None:
        yield answers
        return

    class _Capture:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def query(self, connection: str, sql: str, **kw: Any) -> Any:
            ans = self._inner.query(connection, sql, **kw)
            answers.append(ans)
            return ans

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)
    service.facts = _Capture(facts)
    try:
        yield answers
    finally:
        service.facts = facts


def _persist_artefacts(dataset: Any, answers: list, facts_root: Optional[Path]) -> int:
    """Publish the query artefact of every answer a SHIPPED ``query_cell`` cell cites (and only
    those). An answer that already persisted its own (older stores) needs nothing."""
    from databridge_core import GroundedValue
    shipped = {cell.pointer.query_hash for row in dataset.rows for cell in row
               if isinstance(cell, GroundedValue) and cell.pointer.kind == "query_cell"}
    try:
        from databridge_mcp_ingest.tabular.count import persist_query_artefact
    except ImportError:                                 # a store that persists on read
        return 0
    n = 0
    for ans in answers:
        body = getattr(ans, "query_artefact", None)
        if body and not getattr(ans, "query_ref", "") and body.get("query_hash") in shipped:
            persist_query_artefact(ans, facts_dir=facts_root)
            n += 1
    return n


def plan_entities(plan: MaterialisationPlan) -> tuple[str, ...]:
    """Every entity a plan's builders read: the selection, each column's source, the label's
    source and a corpus's text field. Computed from the plan, so a new relation that names an
    entity is covered by the same walk."""
    names = [plan.selection.entity]
    names += [c.source.entity for c in plan.shape.columns]
    if plan.shape.label is not None:
        names.append(plan.shape.label.source.entity)
    if plan.shape.text_field is not None:
        names.append(plan.shape.text_field.entity)
    return tuple(dict.fromkeys(names))


def check_pins(pins, live, *, required: Optional[dict] = None) -> None:
    """R-021 Finding 3: every source a plan pins must be read exactly as pinned — present, same
    content hash, same schema hash (when pinned), same as-of instant — or the run is refused.

    Round 2, Finding 3: the pins must also COVER every source the plan reads (``required``:
    ``{entity: source_id}``), and a source may be pinned once. Pins that name only unrelated
    sources, or none, no longer let an unpinned read through: the refusal names each missing
    pin, so the caller can add it (`GET /materialise/catalogue?as_of=` lists the live pins)."""
    live_by = {s.source_id: s for s in live}
    bad = []
    seen: dict[str, int] = {}
    for pin in pins:
        seen[pin.source_id] = seen.get(pin.source_id, 0) + 1
    for sid, n in sorted(seen.items()):
        if n > 1:
            bad.append(f"{sid}: pinned {n} times (one pin per source)")
    for entity, sid in sorted((required or {}).items()):
        if sid not in seen:
            bad.append(f"{sid}: read (entity {entity!r}), not pinned")
    for pin in pins:
        got = live_by.get(pin.source_id)
        if got is None:
            bad.append(f"{pin.source_id}: pinned, not present")
        elif got.content_hash != pin.content_hash:
            bad.append(f"{pin.source_id}: content {got.content_hash[:12]}… != pinned "
                       f"{pin.content_hash[:12]}…")
        elif pin.schema_hash and got.schema_hash != pin.schema_hash:
            bad.append(f"{pin.source_id}: schema {got.schema_hash[:12]}… != pinned "
                       f"{pin.schema_hash[:12]}…")
        elif got.as_of != pin.as_of:
            bad.append(f"{pin.source_id}: read as of {got.as_of}, pinned as of {pin.as_of}")
    if bad:
        raise SourcePinViolated(
            "the stores this run would read are not the stores its plan pins — "
            + "; ".join(dict.fromkeys(bad)) + ". A plan_hash identifies exactly one dataset, so "
            "an unpinned or changed source refuses the run instead of emitting unidentified "
            "bytes under that identity")


def leak_check_problem(leak: Any) -> Optional[str]:
    """Why a receipt's missingness leak check does NOT clear the dataset, or `None`.

    Cleared only when the check is on record and complete (numeric observed delta and threshold),
    says ``passed: true``, AND the observed delta is within the threshold — a ``passed`` that
    contradicts its own numbers is not a pass (R-021 Data-page hand-over)."""
    if hasattr(leak, "model_dump"):
        leak = leak.model_dump(mode="json")
    if not isinstance(leak, dict):
        return "the run has no missingness leak check on record"
    observed, threshold = leak.get("max_class_delta_observed"), leak.get("threshold")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (observed, threshold)):
        return "the run's missingness leak check is incomplete"
    if leak.get("passed") is not True:
        return "the run's missingness leak check did not pass"
    if observed > threshold:
        return (f"the run's missingness leak check reports passed while its observed delta "
                f"{observed} exceeds its threshold {threshold}")
    return None


def check_unchanged(before, after) -> None:
    """The stores read are the stores pinned at the start: re-taken before publication."""
    if tuple(before) != tuple(after):
        was = {s.source_id: s.content_hash for s in before}
        now = {s.source_id: s.content_hash for s in after}
        moved = sorted(k for k in set(was) | set(now) if was.get(k) != now.get(k))
        raise SourcePinViolated(
            f"source(s) {moved} changed while the run was in flight; the dataset would mix two "
            f"states of the platform, so it is refused and nothing is published")


POINTER_SIDECAR_SUFFIX = ".pointers.jsonl"


def write_pointer_sidecar(dataset, plan, out_dir: Path) -> dict:
    """Write ``<output_stem>.pointers.jsonl``: one line per CELL of the shipped dataset, keyed by
    ``row`` (the 0-based line of the dataset file, which is written in the same order) and
    ``column``, with the row's ``key`` (its row-order columns' values) for joining. A grounded cell
    carries its full `SourcePointer`; a withheld one its policy and reason; an absent one the
    field and snapshot the question was asked of. No value appears here that the dataset does not
    already hold (E2E run 2, N-08). Returns ``{name, sha256, lines, grounded}``."""
    import hashlib

    from databridge_core import Absent, GroundedValue, Withheld

    name = f"{plan.output_stem}{POINTER_SIDECAR_SUFFIX}"
    order = [c for c in (plan.determinism.row_order or ()) if c in dataset.columns]
    idx = [dataset.index(c) for c in order]
    lines = grounded = 0
    with (out_dir / name).open("w", encoding="utf-8", newline="\n") as fh:
        for r, row in enumerate(dataset.rows):
            key = {c: (row[i].value if isinstance(row[i], GroundedValue) else None)
                   for c, i in zip(order, idx)}
            for column, cell in zip(dataset.columns, row):
                rec: dict = {"row": r, "column": column, "key": key}
                if isinstance(cell, GroundedValue):
                    grounded += 1
                    rec.update(state="grounded",
                               pointer=cell.pointer.model_dump(mode="json", exclude_none=True))
                elif isinstance(cell, Withheld):
                    rec.update(state="withheld", reason_code=cell.record.reason_code,
                               policy_id=cell.record.policy_id)
                elif isinstance(cell, Absent):
                    rec.update(state="absent", field_path=cell.field_path,
                               snapshot=(cell.snapshot.model_dump(mode="json", exclude_none=True)
                                         if cell.snapshot is not None else None))
                fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=True, default=str) + "\n")
                lines += 1
    digest = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
    return {"name": name, "sha256": digest, "lines": lines, "grounded": grounded}


def file_roles(doc: dict, files: list[str]) -> dict[str, str]:
    """``{file: role}`` for one run: ``dataset`` (the receipt's ``output_uri``), ``pointers`` (the
    per-cell pointer sidecar the receipt's ``pointer_manifest.uri`` names), ``receipt``, ``plan``,
    ``metadata`` (the Croissant file, the index-registration record) or ``other``."""
    r = doc.get("receipt") or {}
    dataset = Path(r["output_uri"]).name if r.get("output_uri") else None
    datasets = {Path(str(x)).name for x in r.get("output_files") or []} | {dataset}
    croissant = Path(r["croissant_uri"]).name if r.get("croissant_uri") else None
    manifest = r.get("pointer_manifest") or {}
    pointers = Path(manifest["uri"]).name if manifest.get("uri") else None
    roles = {}
    for name in files:
        roles[name] = ("receipt" if name == "receipt.json" else
                       "plan" if name in ("plan.yaml", "design.json") else
                       "dataset" if name and name in datasets else
                       "pointers" if name == pointers else
                       "metadata" if name in (croissant, "index_registration.json") else
                       "other")
    return roles


def _slice_args(pointer) -> Optional[tuple[int, int]]:
    """``(start, end)`` of a ``text_slice@v1`` derived pointer, else `None`."""
    from databridge_adapter import parse_derivation

    if getattr(pointer, "kind", None) != "derived" or not pointer.derivation:
        return None
    try:
        call = parse_derivation(pointer.derivation)
        if call.name != "text_slice":
            return None
        args = dict(call.args)
        return int(args["start"]), int(args["end"])
    except (KeyError, ValueError, TypeError):
        return None


_NO_EXPECTATION = object()


def _chunk_expectations(dataset, plan) -> dict:
    """``{(row, column): expected value}`` for a chunk corpus's coordinate columns, computed
    from each chunk's OWN pointer (R-021 round 2, Finding 1) — nothing is exempt from matching:

    * ``char_start`` / ``char_end`` are the ``text_slice@v1`` offsets the pointer carries;
    * ``chunk_id`` is ``<document_id>#<start // step>`` (the chunker's own boundary rule);
    * each coordinate must carry the SAME pointer as its row's ``text`` chunk when that column
      is present, so a coordinate cannot borrow another chunk's pointer.

    ``document_id`` is not here: it carries the row key's own pointer and is checked by
    re-reading it (see `pointer_check`)."""
    if plan is None or plan.shape.kind != "chunk_corpus" or plan.shape.chunking is None:
        return {}
    from databridge_core import GroundedValue

    step = plan.shape.chunking.max_chars - plan.shape.chunking.overlap_chars
    out: dict = {}
    for r, row in enumerate(dataset.rows):
        cells = dict(zip(dataset.columns, row))
        text = cells.get("text")
        for col in ("char_start", "char_end", "chunk_id"):
            cell = cells.get(col)
            if not isinstance(cell, GroundedValue):
                continue
            span = _slice_args(cell.pointer)
            if span is None or (isinstance(text, GroundedValue) and text.pointer != cell.pointer):
                out[(r, col)] = _NO_EXPECTATION
                continue
            start, end = span
            if col == "char_start":
                out[(r, col)] = start
            elif col == "char_end":
                out[(r, col)] = end
            else:
                doc = cells.get("document_id")
                out[(r, col)] = (f"{doc.value}#{start // step}"
                                 if isinstance(doc, GroundedValue) and step > 0
                                 and start % step == 0 else _NO_EXPECTATION)
    return out


def pointer_check(dataset, reader: PlatformReader, *, plan=None) -> dict:
    """Re-read every grounded cell's pointer and report how many resolve and MATCH.

    Measured, per build, over EVERY grounded cell — not a sample: a facts query is re-run once per
    query hash and a capture text is read once per digest, so the cost is per source, not per
    cell. A cell this reader cannot re-read is counted as such, never as a pass. There is no
    resolve-only class of cell (R-021 round 2, Finding 1): a coordinate either re-reads to exactly
    its value from the record its pointer names, or (a chunk corpus's offsets and ids) equals
    what its own pointer encodes — ``plan`` supplies the chunking rule for the latter.
    """
    from databridge_core import GroundedValue

    expect = _chunk_expectations(dataset, plan)
    corpus = plan is not None and plan.shape.kind == "chunk_corpus"
    grounded = resolves = matches = 0
    failures: list[dict] = []
    for r, row in enumerate(dataset.rows):
        for column, cell in zip(dataset.columns, row):
            if not isinstance(cell, GroundedValue):
                continue
            grounded += 1
            if (r, column) in expect:
                ok = reader.resolve(cell.pointer) is not None
                want = expect[(r, column)]
                match = ok and want is not _NO_EXPECTATION and cell.value == want
            elif corpus and column == "document_id":
                got = reader.resolve(cell.pointer)
                ok = got is not None
                match = ok and str(got) == str(cell.value)
            else:
                ok, match = reader.cell_check(cell)
            resolves += ok
            matches += match
            if not (ok and match) and len(failures) < 20:
                failures.append({"row": r, "column": column, "pointer_kind": cell.pointer.kind,
                                 "resolves": ok, "matches": match})
    return {"grounded_cells": grounded, "pointers_resolved": resolves, "values_match": matches,
            "all_resolved": grounded == resolves, "all_match": grounded == matches,
            "failures": failures}


def summary(receipt, platform: dict, *, files: list[str]) -> dict:
    return summary_from_doc({"receipt": receipt.model_dump(mode="json"), "platform": platform},
                            files=files)


def summary_from_doc(doc: dict, *, files: list[str]) -> dict:
    r, p = doc.get("receipt") or {}, doc.get("platform") or {}
    return {
        "plan_hash": r.get("plan_hash"), "content_hash": r.get("content_hash"),
        "shaped_by": r.get("shaped_by"), "reproducible": p.get("reproducible"),
        "executed_at": r.get("executed_at"), "shape": p.get("shape"),
        "entity": p.get("entity"), "request": p.get("request"), "run_by": p.get("run_by"),
        "answers": p.get("answers") or {},
        "rows": r.get("rows"), "cells": r.get("cells"), "verdicts": r.get("verdicts"),
        "unresolved": p.get("unresolved"), "pointer_check": p.get("pointer_check"),
        "produced_by": p.get("produced_by"), "honest_summary": p.get("honest_summary"),
        "notes": r.get("notes"), "files": files,
        "label_diagnosis": r.get("label_diagnosis"),
        # DSD (additive): the raw delegated mode says what it is
        "mode": p.get("mode") or "labelled",
        "statement": r.get("statement"),
        "design_id": p.get("design_id"), "design_digest": p.get("design_digest"),
        # Additive (R-021 Data-page #1): what each file IS, so a client gates downloads on the
        # server's word rather than on a file-name guess.
        "file_roles": file_roles(doc, files),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: The raw delegated mode's name (`delegated.MODE`), here so the download gate needs no import.
RAW_MODE = "raw_delegated"


def _raw_text(value: Any) -> str:
    """A raw cell as CSV text: the stored value, unrounded (a Decimal is written as computed)."""
    from decimal import Decimal
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def write_raw_csv(dataset, path: Path) -> int:
    """The raw table as CSV: values only, in column order; a cell with no value is empty (its
    state and reason are in the pointer sidecar)."""
    import csv

    from databridge_core import GroundedValue
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(list(dataset.columns))
        for row in dataset.rows:
            w.writerow([_raw_text(c.value) if isinstance(c, GroundedValue) else "" for c in row])
    return dataset.n_rows


def write_raw_parquet(dataset, path: Path) -> Optional[str]:
    """The raw table as Parquet (int64 when every value is an int, float64 when every value is a
    plain int/float, text otherwise), or the reason it was not written."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return "Parquet was not written: pyarrow is not installed (the CSV is the dataset)"
    from databridge_core import GroundedValue
    cols = {}
    for i, name in enumerate(dataset.columns):
        vals = [r[i].value if isinstance(r[i], GroundedValue) else None for r in dataset.rows]
        present = [v for v in vals if v is not None]
        if present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
            cols[name] = pa.array(vals, type=pa.int64())
        elif present and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                             for v in present):
            cols[name] = pa.array([None if v is None else float(v) for v in vals],
                                  type=pa.float64())
        else:
            cols[name] = pa.array([None if v is None else _raw_text(v) for v in vals],
                                  type=pa.string())
    pq.write_table(pa.table(cols), str(path))
    return None


__all__ = ["PlatformMaterialiser", "RunRefused", "build_catalogue", "shape_status",
           "check_pins", "check_unchanged", "file_roles", "leak_check_problem",
           "plan_entities",
           "pointer_check", "materialise_root", "PLATFORM_BACKEND", "POINTER_SIDECAR_SUFFIX",
           "write_pointer_sidecar", "write_raw_csv", "write_raw_parquet", "RAW_MODE"]
