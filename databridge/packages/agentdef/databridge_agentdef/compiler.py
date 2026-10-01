"""The **compiler** (AGENT_PLANE D11) - a declaration in, four artefacts out, or a refusal.

    an author WRITES a declaration -> this module DECIDES, deterministically -> the decision is
    recorded with its reasoning -> either four artefacts exist, or the agent cannot run.

It is modelled on `databridge_graph.growth.evaluate_proposal` and shares its three properties,
with one difference that matters.

**1. The decision is computed, never recalled.** `compile_agent` is a pure function of the
declaration, the adapter catalogue and the pack's purpose vocabulary. No model call, no learned
threshold, no memory of what compiled last week. Every refusal carries the comparison that
produced it - the capability and the advertised set, the alpha and the base risk and the bound -
so a reviewer can redo the arithmetic rather than trust the verdict.

**2. There is no waiver, at all.** `growth.py` has a `GrowthPolicy` whose `auto_approve` can waive
the non-structural triggers, and a `STRUCTURAL_ESCALATIONS` constant it cannot reach. This module
goes further: there is no policy object, no `force=`, no `allow_unsafe=`, no environment variable.
Every refusal is final and the only fix is to change the declaration. That is the difference
between widening a schema (recoverable, reviewable, sometimes routine) and running an agent
against a live estate without an authority for it.

`STRUCTURAL_REFUSALS` therefore means something slightly different here: not "the policy cannot
waive these" - nothing can waive anything - but **"these checks may not be removed from this
module"**. `tests/test_the_refusals_are_not_waivable.py` reads this file and fails if a structural
code stops having a live call site, which is the check that survives a future refactor deleting
one by accident.

**3. Refusal is a value, not an exception.** A `CompilationDecision` is what a UI renders and what
a ledger row would cite. `compile_or_raise` exists for callers that want control flow, and it
carries the whole decision on the exception.

## The order of the checks is the order a reviewer would use

Authority first (purpose, then capabilities), because a declaration with no lawful basis or no
resolvable adapter is not a governance judgement to be weighed against the rest. Then the evidence
obligations (citation, trust ceiling). Then the write path (actions). Then the economics (budget).
Then the gates (evals), and last the risk budget - which is checked last because its feasibility
test depends on the golden set the eval binding pins.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

from databridge_adapter.purposes import PurposeVocabulary, purpose_vocabulary

from . import feasibility as kotte
from .artefacts import (
    _COMPILER_MINT,
    BudgetAdmission,
    CapabilityGrant,
    CapabilityGrantSet,
    CompiledAgent,
    EvalBinding,
    SinkPolicy,
    build_sink_policy,
)
from .catalogue import AdapterCatalogue
from .declaration import AgentDeclaration
from .errors import CompilationRefused
from .paths import databridge_root, packs_root


class Outcome(str, Enum):
    """What `compile_agent` decided. Two values, deliberately.

    There is no `ESCALATED`. A declaration is a reviewed pack artefact that a human already wrote;
    escalating it to a human would be asking them to approve their own file. Either it compiles or
    it is sent back with the reason.
    """

    COMPILED = "compiled"
    REFUSED = "refused"


class RefusalCode(str, Enum):
    """Why a declaration did not compile. Each names a comparison a reviewer can redo."""

    # --- authority ---
    PURPOSE_NOT_PACK_DECLARED = "purpose_not_pack_declared"
    NO_CAPABILITIES_DECLARED = "no_capabilities_declared"
    ADAPTER_NOT_DECLARED = "adapter_not_declared"
    CAPABILITY_NOT_ADVERTISED = "capability_not_advertised"
    CAPABILITY_IS_A_WRITE = "capability_is_a_write"
    # --- evidence obligations ---
    NO_CITATION_RULE = "no_citation_rule"
    NO_TRUST_CEILING = "no_trust_ceiling"
    # --- the write path (D12) ---
    ACTION_WITHOUT_AUTOMATION_MODE = "action_without_automation_mode"
    SUBJECT_ACTION_NOT_HUMAN_REQUIRED = "subject_action_not_human_required"
    AUTONOMOUS_ACTION_NOT_COMPENSABLE = "autonomous_action_not_compensable"
    # --- economics ---
    NO_BUDGET = "no_budget"
    BUDGET_INCOMPLETE = "budget_incomplete"
    # --- gates ---
    NO_EVAL_BINDING = "no_eval_binding"
    GOLDEN_SET_NOT_DECLARED = "golden_set_not_declared"
    GOLDEN_SET_MISSING = "golden_set_missing"
    GOLDEN_SET_OUTSIDE_TREE = "golden_set_outside_tree"
    GOLDEN_SET_EMPTY = "golden_set_empty"
    GOLDEN_SET_UNREADABLE = "golden_set_unreadable"
    NO_EVAL_GATES = "no_eval_gates"
    GROUNDING_GATE_BELOW_CITATION_RULE = "grounding_gate_below_citation_rule"
    # --- the risk budget (D9, Kotte) ---
    ABSTENTION_GATE_WITHOUT_BUDGET = "abstention_gate_without_budget"
    ABSTENTION_NOT_FEASIBILITY_TESTED = "abstention_not_feasibility_tested"
    ABSTENTION_MEASURED_ON_ANOTHER_GOLDEN_SET = "abstention_measured_on_another_golden_set"
    ABSTENTION_UNCERTIFIABLE_FOR_TASK_CLASS = "abstention_uncertifiable_for_task_class"
    ABSTENTION_INFEASIBLE = "abstention_infeasible"


#: Checks that may not be removed from this module.
#:
#: Each one stands between a compiled grant and one of the system's load-bearing claims: that a
#: read has a declared lawful basis, that authority is bounded by what an adapter advertises, that
#: every emitted field carries a pointer, that the identity determination is never automated, and
#: that a risk budget is arithmetic rather than a wish. `growth.py`'s `STRUCTURAL_ESCALATIONS`
#: names triggers a POLICY cannot waive; there is no policy here, so this set names the checks a
#: REFACTOR may not drop - asserted against this file's own source, so the assertion survives a
#: rewrite that forgets the list.
STRUCTURAL_REFUSALS: frozenset[RefusalCode] = frozenset({
    RefusalCode.PURPOSE_NOT_PACK_DECLARED,
    RefusalCode.CAPABILITY_NOT_ADVERTISED,
    RefusalCode.CAPABILITY_IS_A_WRITE,
    RefusalCode.ADAPTER_NOT_DECLARED,
    RefusalCode.NO_CITATION_RULE,
    RefusalCode.ACTION_WITHOUT_AUTOMATION_MODE,
    RefusalCode.SUBJECT_ACTION_NOT_HUMAN_REQUIRED,
    RefusalCode.ABSTENTION_NOT_FEASIBILITY_TESTED,
    RefusalCode.ABSTENTION_UNCERTIFIABLE_FOR_TASK_CLASS,
    RefusalCode.ABSTENTION_INFEASIBLE,
})

#: The gate name a `citation: required_per_field` OR `citation: required_per_answer` rule is
#: checked against, and the only score that satisfies either. Not 0.99: "every emitted value
#: resolves to its source" has no acceptable failure rate whether the pointer is per field or per
#: answer, and a grounding gate below 1.0 is a declaration that some emitted value may cite
#: nothing. Both rules were checked against this gate as of finding 3 (R-013) - before that,
#: `required_per_answer` compiled with no coherence check at all.
GROUNDING_GATE = "grounding"
ABSTENTION_GATE = "abstention_calibration"


@dataclass(frozen=True)
class Refusal:
    """One reason, with the comparison that produced it.

    `detail` is written for the person who has to fix the file. It names the values compared and
    the edit that would resolve it - "capability 'account_read' is not advertised by 'core_banking',
    which offers [...]" rather than "capability not advertised".
    """

    code: RefusalCode
    detail: str

    @property
    def structural(self) -> bool:
        return self.code in STRUCTURAL_REFUSALS

    def as_record(self) -> dict:
        return {"code": self.code.value, "detail": self.detail, "structural": self.structural}


@dataclass(frozen=True)
class CompilationDecision:
    """What was decided, why, and against which catalogue and vocabulary.

    Carries the inputs' identities (`catalogue_digest`, `vocabulary_packs`) for the reason
    `GrowthDecision` carries `policy_digest`: without them a decision read back later says what
    was decided but not what the world looked like at the time, and "why did this compile in
    March?" becomes unanswerable the first time an estate changes.
    """

    agent: str
    version: int
    pack: str
    outcome: Outcome
    refusals: tuple[Refusal, ...]
    compiled: Optional[CompiledAgent]
    declaration_digest: str
    catalogue_digest: str
    vocabulary_packs: tuple[str, ...]
    decided_at: datetime
    reasoning: str

    def __post_init__(self) -> None:
        if self.outcome is Outcome.COMPILED and self.compiled is None:
            raise ValueError("a COMPILED decision must carry the compiled agent")
        if self.outcome is Outcome.COMPILED and self.refusals:
            raise ValueError(
                "a COMPILED decision cannot carry refusals. There is no 'compiled with "
                "reservations': that is the shape a waiver takes when nobody names it a waiver")
        if self.outcome is Outcome.REFUSED and not self.refusals:
            raise ValueError("a REFUSED decision must say why")
        if self.outcome is Outcome.REFUSED and self.compiled is not None:
            raise ValueError("a REFUSED decision must not carry a compiled agent")

    @property
    def structural_refusals(self) -> tuple[Refusal, ...]:
        return tuple(r for r in self.refusals if r.structural)

    @property
    def ref(self) -> str:
        return f"{self.pack}/{self.agent}@v{self.version}"

    def as_record(self) -> dict:
        return {
            "ref": self.ref, "agent": self.agent, "version": self.version, "pack": self.pack,
            "outcome": self.outcome.value,
            "refusals": [r.as_record() for r in self.refusals],
            "structural": [r.code.value for r in self.structural_refusals],
            "declaration_digest": self.declaration_digest,
            "catalogue_digest": self.catalogue_digest,
            "vocabulary_packs": list(self.vocabulary_packs),
            "decided_at": self.decided_at.isoformat(),
            "reasoning": self.reasoning,
            "compiled": (self.compiled.as_record() if self.compiled else None),
        }


# --------------------------------------------------------------------------------------
# reading the golden set
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GoldenSet:
    """A golden set as the compiler sees it: a path, a digest and a record count."""

    path: str
    digest: str
    records: int


def read_golden_set(path: Path) -> GoldenSet:
    """Digest and count a ``.jsonl`` golden set.

    Line endings are normalised before digesting, for the reason `declaration.normalised_text`
    gives: a checkout under ``core.autocrlf`` must not produce a different digest for the same
    reviewed file, or the abstention measurement would appear to have been made against another
    set on every Windows machine.

    Blank lines and ``#`` comments are not records. A file of nothing but comments therefore has
    zero records, and `EvalBinding` refuses it - an empty golden set passes every gate, which is
    worse than no gate because it reads as evidence.
    """
    from .declaration import normalised_text, text_digest              # noqa: PLC0415

    text = normalised_text(path)
    records = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: golden set line is not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{path}:{lineno}: each golden set record must be a JSON object")
        records += 1
    return GoldenSet(path=str(path), digest=text_digest(text), records=records)


def catalogue_digest(catalogue: AdapterCatalogue) -> str:
    payload = json.dumps([a.as_record() for a in
                          sorted(catalogue, key=lambda a: a.source_id)],
                         sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------------------
# the compilation
# --------------------------------------------------------------------------------------
def compile_agent(declaration: AgentDeclaration, *,
                  catalogue: AdapterCatalogue,
                  purposes: Optional[PurposeVocabulary] = None,
                  packs_dir: Optional[Path] = None,
                  repo_root: Optional[Path] = None,
                  now: Optional[Callable[[], datetime]] = None) -> CompilationDecision:
    """Compile ``declaration``, or refuse it with every reason at once.

    **Deterministic in the sense that matters**: same declaration, same catalogue, same
    vocabulary -> same VERDICT, on any machine, with no model in the loop. "Verdict" is `outcome`,
    `refusals` and every artefact's CONTENT - what `compiled_id`/`grant_id`/`admission_id`/
    `binding_id`/`policy_id` hash, and what `test_compilation_is_deterministic` checks with
    `as_record()`. It does not extend to `decided_at`/`compiled_at`: those are wall-clock audit
    timestamps, not part of the verdict, and nothing in this module hashes or compares them - no
    artefact id, no digest, no equality check a caller is meant to rely on depends on either field
    (finding 7, R-013, downgraded from a code finding to this paragraph: earlier wording said
    "same answer" without saying which parts of the record that covered). Pass `now=` for a
    byte-stable `CompilationDecision.as_record()` across runs, exactly as this package's own tests
    do; without it, two calls a millisecond apart differ ONLY in those two ISO timestamps.

    Every keyword here is an INPUT - which catalogue, which vocabulary, which tree. None of them
    can weaken a check, and none of them is optional in the sense of "skip this". `purposes`
    defaults to the declaring pack's own vocabulary rather than the deployment binding, so that a
    finance agent compiles in a checkout with only healthcare enabled; the grant records the pack,
    and a `Principal` built from it still refuses at runtime until the pack is enabled (D5). A
    `purposes` vocabulary that does not even cover the declaring pack is refused (finding 5,
    R-013): a vocabulary built for another pack cannot answer "did THIS pack declare this
    purpose", no matter what purposes it happens to contain.

    Every reason is collected, not just the first. A declaration that both names an unadvertised
    capability AND asserts an untested abstention budget must show both, or fixing the first makes
    the second appear as a surprise on the next attempt.
    """
    at = (now or (lambda: datetime.now(timezone.utc)))()
    root = Path(repo_root) if repo_root is not None else databridge_root()
    vocab = purposes if purposes is not None else purpose_vocabulary(
        [declaration.pack], Path(packs_dir) if packs_dir is not None else packs_root())
    refusals: list[Refusal] = []

    def refuse(code: RefusalCode, detail: str) -> None:
        refusals.append(Refusal(code=code, detail=detail))

    # --- 1. authority: the lawful basis ------------------------------------------------
    if declaration.pack not in vocab.packs:
        # Finding 5 (R-013). A vocabulary is per pack; `vocab.declares(purpose)` alone answers
        # "does SOME pack in this vocabulary declare this purpose", not "did the pack that
        # declares THIS AGENT declare it". A caller that passed a `purposes=` covering a
        # different pack's estate could otherwise inject a lawful basis the declaring pack never
        # reviewed - the injected vocabulary need not belong to the declaration's pack at all.
        refuse(RefusalCode.PURPOSE_NOT_PACK_DECLARED,
               f"purpose {declaration.purpose!r} was checked against a vocabulary covering pack(s) "
               f"{list(vocab.packs)}, which does not include the declaring pack "
               f"{declaration.pack!r}. A purpose is only a lawful basis if it was declared BY THE "
               f"PACK THAT DECLARES THIS AGENT (spine D5); a vocabulary that does not cover that "
               f"pack cannot answer the question, whatever purposes it happens to contain")
    elif not vocab.declares(declaration.purpose):
        known = ", ".join(sorted(vocab.purposes)) or "(none)"
        refuse(RefusalCode.PURPOSE_NOT_PACK_DECLARED,
               f"purpose {declaration.purpose!r} is not declared by pack {declaration.pack!r}. "
               f"Declared there: {known}. A purpose is a pack statement about lawful basis, never "
               f"an engine literal (spine D5), and it becomes part of the Principal on every read "
               f"this agent makes - so an undeclared one would put an unjustifiable basis into "
               f"the ledger. Declare it in packs/{declaration.pack}/purposes.yaml, with its "
               f"`subject_scoped` flag and its lawful basis, and have that reviewed.")
    elif vocab.require(declaration.purpose).pack != declaration.pack:
        # Still finding 5. A MULTI-pack vocabulary (e.g. `purposes=purpose_vocabulary(["a", "b"],
        # ...)`) passes the `declaration.pack not in vocab.packs` screen above whenever the
        # declaring pack is one of the several it covers, but `vocab.declares(...)` only checks
        # membership in the merged namespace - it does not check WHICH pack declared the purpose.
        # Without this, a declaration in pack `a` could compile under a purpose only pack `b` ever
        # reviewed and wrote a lawful basis for.
        actual = vocab.require(declaration.purpose).pack
        refuse(RefusalCode.PURPOSE_NOT_PACK_DECLARED,
               f"purpose {declaration.purpose!r} is declared by pack {actual!r}, not by the "
               f"declaring pack {declaration.pack!r}. Borrowing another pack's lawful basis is "
               f"not a lawful basis (spine D5) - declare an equivalent purpose in "
               f"packs/{declaration.pack}/purposes.yaml if this agent needs one")

    # --- 2. authority: what the adapters actually advertise -----------------------------
    grants: list[CapabilityGrant] = []
    untrusted_capable: list[str] = []
    if not declaration.capabilities:
        refuse(RefusalCode.NO_CAPABILITIES_DECLARED,
               f"declares no capabilities. An agent with no capability grant cannot read "
               f"anything, and compiling one would produce an empty grant set whose runtime "
               f"screen refuses every request - a silent no-op wearing the word 'deployed'. If "
               f"this agent is meant to be read-only over the graph alone, say so with a "
               f"capability for the adapter that serves it.")
    for source_id, capability in declaration.capability_parts():
        advert = catalogue.get(source_id)
        if advert is None:
            known = ", ".join(sorted(a.source_id for a in catalogue)) or "(none)"
            refuse(RefusalCode.ADAPTER_NOT_DECLARED,
                   f"capability {source_id}.{capability!r} names adapter {source_id!r}, which no "
                   f"estate of pack(s) {list(catalogue.packs)} advertises. Advertised: {known}. A "
                   f"grant resolves THROUGH an adapter (AGENT_PLANE's RESOLVED_THROUGH edge); one "
                   f"that names no adapter resolves through nothing and would be a permission "
                   f"with no boundary attached.")
            continue
        if advert.is_write(capability):
            refuse(RefusalCode.CAPABILITY_IS_A_WRITE,
                   f"{source_id}.{capability} is a WRITE capability on {advert.declared_in}. "
                   f"Writes are a separate capability class: they are never invoked by an adapter "
                   f"directly, only by an ActionType with an `automation.mode`, an audit "
                   f"obligation and a declared compensating reversal (AGENT_PLANE §6.4). Declare "
                   f"it under `actions:`, not under `capabilities:` - a read grant with a write's "
                   f"name would be a permission whose blast radius nobody reviewed.")
            continue
        if not advert.advertises(capability):
            offered = ", ".join(sorted(advert.reads))
            refuse(RefusalCode.CAPABILITY_NOT_ADVERTISED,
                   f"adapter {source_id!r} does not advertise capability {capability!r}. It "
                   f"advertises: {offered} ({advert.declared_in}). The screen is against the "
                   f"ESTATE's advertisement rather than against a list in this declaration, "
                   f"because a capability checked against names the declaring author also wrote "
                   f"is not checked at all.")
            continue
        if advert.max_trust == "untrusted":
            untrusted_capable.append(source_id)
        grants.append(CapabilityGrant(
            agent=declaration.agent, version=declaration.version, pack=declaration.pack,
            source_id=source_id, capability=capability, purpose=declaration.purpose,
            max_trust=(declaration.trust_ceiling or "trusted"),
            declaration_digest=declaration.declaration_digest,
            resolved_through=advert.declared_in))

    # --- 3. evidence obligations --------------------------------------------------------
    if declaration.citation is None:
        refuse(RefusalCode.NO_CITATION_RULE,
               "declares no `citation:` rule. Every emitted field must carry a SourcePointer "
               "(CLAUDE.md hard constraint 2); an agent with no citation rule is one whose output "
               "has no stated evidence obligation, and an unstated obligation is one no test can "
               "check and no reviewer can hold it to. Write `citation: required_per_field`.")
    elif declaration.citation == "none":
        refuse(RefusalCode.NO_CITATION_RULE,
               "declares `citation: none`. That is spellable so that it can be refused by name "
               "rather than by omission: an agent that emits values with no pointer is the exact "
               "failure the grounding wall, the required `SourcePointer` and the whole provenance "
               "model exist to prevent. There is no deployment in which it is permitted.")

    if declaration.trust_ceiling is None:
        refuse(RefusalCode.NO_TRUST_CEILING,
               "declares no `trust_ceiling:`. The ceiling is what the sink policy is derived from "
               "and what each grant carries, so an absent one would either default silently to "
               "`untrusted` (widening authority nobody granted) or to `trusted` (a promise the "
               "adapters below cannot keep). Say which, in the file (spine D3).")

    # --- 4. the write path (D12) ---------------------------------------------------------
    for action in declaration.actions:
        if action.automation_mode is None:
            where = ("carries an `automation:` block with no `mode`"
                     if action.automation_declared else "declares no `automation:` block")
            refuse(RefusalCode.ACTION_WITHOUT_AUTOMATION_MODE,
                   f"action {action.name!r} {where}. Actions are the only write path, and "
                   f"`automation.mode` is what the commit path checks - it is enforced "
                   f"structurally, not documented (D12). An action with no mode has no gate at "
                   f"all, which is not the same as a strict default: it is a write nobody decided "
                   f"the rules for. Write one of "
                   f"human_required | human_notified | autonomous.")
            continue
        if action.touches_subject and action.automation_mode != "human_required":
            refuse(RefusalCode.SUBJECT_ACTION_NOT_HUMAN_REQUIRED,
                   f"action {action.name!r} declares touches_subject: true and "
                   f"automation.mode: {action.automation_mode!r}. An action that decides, records "
                   f"or narrows WHICH SUBJECT something concerns is an identity determination, "
                   f"and the system never auto-confirms one: it presents candidates and a named "
                   f"human attests, in person (CLAUDE.md hard constraint 1, D12). "
                   f"`human_notified` is not a gate - it is a notification after the fact - and "
                   f"`autonomous` is the thing being refused. There is no setting anywhere in "
                   f"this build that changes this answer.")
        if action.automation_mode == "autonomous" and not (action.reversal or "").strip():
            refuse(RefusalCode.AUTONOMOUS_ACTION_NOT_COMPENSABLE,
                   f"action {action.name!r} is `autonomous` and declares no `reversal:`. An "
                   f"autonomous write must be compensable - a declared path back - because an "
                   f"agent that can write without a way back is an incident generator "
                   f"(AGENT_PLANE §6.4). Autonomy is earned per action type, with evidence; the "
                   f"reversal is part of that evidence.")

    # --- 5. economics --------------------------------------------------------------------
    budget: Optional[BudgetAdmission] = None
    if declaration.budget is None:
        refuse(RefusalCode.NO_BUDGET,
               "declares no `budget:`. One of the four compiled artefacts is a budget admission "
               "record enforced by the harness; with no declaration there is nothing to admit, "
               "and an agent with no ceiling on tokens, escalations or daily cost is one whose "
               "worst day is discovered from an invoice.")
    else:
        missing = declaration.budget.missing_fields()
        if missing:
            refuse(RefusalCode.BUDGET_INCOMPLETE,
                   f"budget omits {list(missing)}. All three ceilings are required: tokens bound "
                   f"one run, escalations bound how much human attention this agent may consume "
                   f"in a day, and the cost ceiling bounds the day itself. Omitting any one "
                   f"leaves an unbounded axis, and the unbounded axis is the one that fails.")
        else:
            budget = BudgetAdmission(
                agent=declaration.agent, version=declaration.version,
                tokens_per_run=int(declaration.budget.tokens_per_run),
                escalations_per_day=int(declaration.budget.escalations_per_day),
                cost_ceiling_usd_per_day=float(declaration.budget.cost_ceiling_usd_per_day))

    # --- 6. the gates --------------------------------------------------------------------
    golden: Optional[GoldenSet] = None
    gates: tuple[tuple[str, float], ...] = ()
    if declaration.evals is None:
        refuse(RefusalCode.NO_EVAL_BINDING,
               "declares no `evals:`. An eval binding is one of the four artefacts: a golden set "
               "that must pass before the agent may run. Without it, 'deployed' means somebody "
               "read the prompt and liked it.")
    else:
        gates = declaration.evals.gates
        if not gates:
            refuse(RefusalCode.NO_EVAL_GATES,
                   "declares a golden set with no `gates:`. A golden set with no threshold is a "
                   "file: it cannot be passed and it cannot be failed, so nothing is gated on it.")
        if not declaration.evals.golden_set:
            refuse(RefusalCode.GOLDEN_SET_NOT_DECLARED,
                   "declares `evals:` with no `golden_set:`. The gates need a set of examples to "
                   "be scored against; thresholds over nothing are numbers in a file.")
        else:
            candidate = (root / declaration.evals.golden_set).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                refuse(RefusalCode.GOLDEN_SET_OUTSIDE_TREE,
                       f"golden_set {declaration.evals.golden_set!r} resolves to {candidate}, "
                       f"outside the repo. A gate whose evidence lives outside the reviewed tree "
                       f"is a gate whose evidence was not reviewed - the same escape "
                       f"`databridge_graph.packs` closed when a pack name could be '../..'.")
            else:
                if not candidate.is_file():
                    refuse(RefusalCode.GOLDEN_SET_MISSING,
                           f"golden_set {declaration.evals.golden_set!r} does not exist "
                           f"({candidate}). The binding cannot be produced, and an eval binding "
                           f"that points at nothing is a gate that always passes.")
                else:
                    # A golden set that cannot be READ is a refusal, not a traceback. A compiler
                    # whose contract is "produce four artefacts or refuse" must not have a third
                    # outcome where it crashes: the caller's handling of a crash is not the
                    # caller's handling of a refusal, and an author who pointed `golden_set` at a
                    # .md file needs the sentence, not the stack.
                    try:
                        golden = read_golden_set(candidate)
                    except (ValueError, OSError, UnicodeDecodeError) as exc:
                        refuse(RefusalCode.GOLDEN_SET_UNREADABLE,
                               f"golden_set {declaration.evals.golden_set!r} exists but cannot be "
                               f"read as JSONL: {exc}. One JSON object per line; blank lines and "
                               f"'#' comments are not records. A gate whose evidence cannot be "
                               f"parsed is a gate nothing was ever scored against.")
                    else:
                        if golden.records < 1:
                            refuse(RefusalCode.GOLDEN_SET_EMPTY,
                                   f"golden_set {declaration.evals.golden_set!r} holds no "
                                   f"records. An empty golden set clears every gate, which is "
                                   f"worse than having no gate: it reads as evidence.")

        grounding = declaration.evals.gate_map.get(GROUNDING_GATE)
        # Finding 3 (R-013): this coherence check used to apply ONLY to `required_per_field`, so
        # `citation: required_per_answer` compiled with no gate requirement at all - an agent
        # whose individual emitted values needed no source pointers, with nothing in the compiler
        # refusing it. Both citation rules claim every emitted value is grounded (per field, or
        # per answer); "has no acceptable failure rate" is true of both claims, so both are held
        # to the same `grounding: 1.0` gate.
        if declaration.citation in ("required_per_field", "required_per_answer") and (
                grounding is None or grounding < 1.0):
            got = "no `grounding` gate at all" if grounding is None else f"grounding: {grounding}"
            refuse(RefusalCode.GROUNDING_GATE_BELOW_CITATION_RULE,
                   f"declares `citation: {declaration.citation}` but {got}. The two must agree: "
                   f"'every emitted value carries a SourcePointer' has no acceptable failure "
                   f"rate whether the pointer is per field or per answer, so the gate is "
                   f"`grounding: 1.0` and nothing below it. A declaration whose citation rule and "
                   f"whose gate disagree is worse than one that claims less, because a reader "
                   f"cannot tell which half is true (the coherence rule `SourceDescription` "
                   f"applies to its own capability matrix).")

    # --- 7. the risk budget (D9, Kotte) ---------------------------------------------------
    report: Optional[kotte.FeasibilityReport] = None
    abst = declaration.abstention
    if abst is None:
        if ABSTENTION_GATE in dict(gates):
            refuse(RefusalCode.ABSTENTION_GATE_WITHOUT_BUDGET,
                   f"gates on {ABSTENTION_GATE!r} but declares no `abstention:` block. "
                   f"Calibration is calibration OF something; with no target risk and no measured "
                   f"base risk there is nothing for the gate to be calibrated against, and the "
                   f"number would be scored against whatever the eval harness happened to assume.")
    else:
        missing = abst.missing_measurement_fields()
        if missing:
            refuse(RefusalCode.ABSTENTION_NOT_FEASIBILITY_TESTED,
                   f"asserts `abstain_when: {abst.abstain_when}` but the abstention block omits "
                   f"{list(missing)}. An abstention budget is arithmetic, not an intention: Kotte "
                   f"({kotte.KOTTE_CITATION}) shows that for base risk mu above target alpha, any "
                   f"distribution-free method must abstain on at least (mu-alpha)/(1-alpha) of "
                   f"examples, so a target with no measured mu cannot be shown to be reachable at "
                   f"all. Measure the base risk on the golden set this agent binds, record it as "
                   f"`measured_base_risk` with the run in `measured_on`/`measured_by` and the "
                   f"set's digest in `golden_set_digest`, and declare the abstention rate this "
                   f"deployment can carry as `max_abstention_rate`.")
        elif golden is not None and abst.golden_set_digest != golden.digest:
            refuse(RefusalCode.ABSTENTION_MEASURED_ON_ANOTHER_GOLDEN_SET,
                   f"the abstention block was measured against golden set digest "
                   f"{abst.golden_set_digest!r}, but the eval binding points at "
                   f"{declaration.evals.golden_set!r} whose digest is {golden.digest!r}. A base "
                   f"risk measured on one set and a gate enforced on another share only a "
                   f"filename. Either the set was edited after the measurement - in which case "
                   f"re-measure - or the wrong file is named.")
        elif golden is not None:
            try:
                report = kotte.assess(
                    task_class=str(abst.task_class), target_alpha=float(abst.target_alpha),
                    base_risk=float(abst.measured_base_risk),
                    max_abstention_rate=float(abst.max_abstention_rate),
                    measured_on=abst.measured_on, golden_set_digest=golden.digest)
            except ValueError as exc:
                refuse(RefusalCode.ABSTENTION_NOT_FEASIBILITY_TESTED,
                       f"the abstention block cannot be feasibility-tested as written: {exc}")
            else:
                if not report.certifiable:
                    refuse(RefusalCode.ABSTENTION_UNCERTIFIABLE_FOR_TASK_CLASS, report.reasoning)
                elif not report.feasible:
                    refuse(RefusalCode.ABSTENTION_INFEASIBLE, report.reasoning)

    # --- 8. the four artefacts, or the refusal -------------------------------------------
    cat_digest = catalogue_digest(catalogue)
    if refusals:
        return CompilationDecision(
            agent=declaration.agent, version=declaration.version, pack=declaration.pack,
            outcome=Outcome.REFUSED, refusals=tuple(refusals), compiled=None,
            declaration_digest=declaration.declaration_digest, catalogue_digest=cat_digest,
            vocabulary_packs=tuple(vocab.packs), decided_at=at,
            reasoning=_reasoning(declaration, Outcome.REFUSED, tuple(refusals), None))

    grant_set = CapabilityGrantSet(agent=declaration.agent, version=declaration.version,
                                   grants=tuple(grants))
    sink_policy: SinkPolicy = build_sink_policy(
        agent=declaration.agent, version=declaration.version,
        read_ceiling=str(declaration.trust_ceiling),
        action_names=[a.name for a in declaration.actions],
        untrusted_capable_sources=untrusted_capable)
    assert budget is not None and golden is not None and declaration.evals is not None
    eval_binding = EvalBinding(
        agent=declaration.agent, version=declaration.version,
        golden_set=declaration.evals.golden_set, golden_set_digest=golden.digest,
        records=golden.records, gates=gates, feasibility=report)
    compiled = CompiledAgent(
        agent=declaration.agent, version=declaration.version, pack=declaration.pack,
        purpose=declaration.purpose, grants=grant_set, sink_policy=sink_policy,
        budget=budget, evals=eval_binding, citation=str(declaration.citation),
        actions=declaration.actions, declared_in=declaration.declared_in,
        declaration_digest=declaration.declaration_digest, compiled_at=at,
        mint=_COMPILER_MINT)
    return CompilationDecision(
        agent=declaration.agent, version=declaration.version, pack=declaration.pack,
        outcome=Outcome.COMPILED, refusals=(), compiled=compiled,
        declaration_digest=declaration.declaration_digest, catalogue_digest=cat_digest,
        vocabulary_packs=tuple(vocab.packs), decided_at=at,
        reasoning=_reasoning(declaration, Outcome.COMPILED, (), compiled))


def compile_or_raise(declaration: AgentDeclaration, **kwargs) -> CompiledAgent:
    """`compile_agent`, as control flow. Raises `CompilationRefused` carrying the decision.

    Note what is absent: this is the *only* other entry point, and it takes no argument the
    refusal path could be steered by. `**kwargs` forwards the inputs of `compile_agent` - which
    catalogue, which vocabulary, which tree - and every one of them is a fact about the world, not
    a permission.
    """
    decision = compile_agent(declaration, **kwargs)
    if decision.outcome is not Outcome.COMPILED or decision.compiled is None:
        raise CompilationRefused(decision)
    return decision.compiled


def compile_all(declarations: Sequence[AgentDeclaration],
                **kwargs) -> tuple[CompilationDecision, ...]:
    """Compile a batch, in the order given. One refusal does not stop the rest: an operator fixing
    a pack wants every file's verdict, not the first one's."""
    return tuple(compile_agent(d, **kwargs) for d in declarations)


def _reasoning(declaration: AgentDeclaration, outcome: Outcome,
               refusals: tuple[Refusal, ...],
               compiled: Optional[CompiledAgent]) -> str:
    """The explanation, assembled from the same values the decision was computed from.

    One paragraph per reason rather than a code list, because it is read by whoever has to change
    the file, and "REFUSED: capability_not_advertised" tells them nothing they can act on.
    """
    head = (f"agent {declaration.agent!r} v{declaration.version} "
            f"(pack {declaration.pack!r}, purpose {declaration.purpose!r})")
    lines = [f"{outcome.value.upper()}: {head}."]

    if outcome is Outcome.COMPILED and compiled is not None:
        lines.append(
            f"Four artefacts produced. "
            f"(1) {len(compiled.grants)} capability grant(s), each bound to a specific adapter "
            f"and to purpose {compiled.purpose!r}: "
            f"{', '.join(sorted(compiled.grants.capabilities))}. "
            f"(2) A sink policy at read ceiling {compiled.sink_policy.read_ceiling!r} barring "
            f"this agent's untrusted values from all "
            f"{len(compiled.sink_policy.forbidden_sinks)} trusted-sink positions. "
            f"(3) A budget admission record: {compiled.budget.tokens_per_run} tokens/run, "
            f"{compiled.budget.escalations_per_day} escalations/day, "
            f"${compiled.budget.cost_ceiling_usd_per_day:.2f}/day. "
            f"(4) An eval binding to {compiled.evals.golden_set} "
            f"({compiled.evals.records} records, digest {compiled.evals.golden_set_digest[:19]}...) "
            f"gating on {', '.join(f'{n} >= {t:g}' for n, t in compiled.evals.gates)}.")
        if compiled.evals.feasibility is not None:
            lines.append(f"Abstention budget: {compiled.evals.feasibility.reasoning}")
        lines.append(
            "The agent may not run until an eval run clears every gate above "
            "(`EvalBinding.check`), and it may request no capability outside the grant set: there "
            "is no escalation path that adds one without a new declaration version.")
        return "\n".join(lines)

    structural = [r for r in refusals if r.structural]
    other = [r for r in refusals if not r.structural]
    lines.append(
        f"{len(refusals)} reason(s). This declaration produces no capability grant, so the agent "
        f"cannot run. There is no override: compilation has no `force` parameter, no policy "
        f"object and no environment variable, because that flag is how a governed system "
        f"eventually becomes ungoverned (AGENT_PLANE §3).")
    if structural:
        lines.append("Structural - these checks stand between a grant and one of the system's "
                     "load-bearing claims, and no configuration reaches them:")
        lines.extend(f"  - {r.code.value}: {r.detail}" for r in structural)
    if other:
        lines.append("Also:")
        lines.extend(f"  - {r.code.value}: {r.detail}" for r in other)
    return "\n".join(lines)
