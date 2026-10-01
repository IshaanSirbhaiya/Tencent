"""The **agent declaration** (AGENT_PLANE D11) - pack YAML, versioned and reviewed like any other
pack artefact.

    packs/<pack>/agents/<agent>.yaml

## What this module does, and the line it does not cross

It reads the file and refuses anything that is not a *declaration*: unknown top-level keys, a
version that is not a positive integer, an action that is not a single-keyed mapping, a trust
ceiling that names something the trust lattice does not have. Those are `DeclarationError` -
malformed, not a governance question, and escalating a typo to a reviewer teaches them to click
through (the distinction `growth.py` draws between REFUSED and ESCALATED).

It does **not** decide whether the declaration may run. Absence of a citation rule, a purpose the
pack never declared, a capability the adapter does not advertise, an abstention budget nobody
tested - each of those loads fine here and is refused by `compiler.compile_agent`, which is the
only thing that gets to say "compiled". Keeping the loader dumb is what makes that claim
meaningful: if the loader could reject on governance grounds there would be two authorities and a
reader would not know which one had spoken.

## The two shapes worth explaining

**Actions are single-keyed mappings**, matching AGENT_PLANE §3 exactly::

    actions:
      - draft_credit_memo:
          automation:
            mode: human_required

§3's illustration writes ``automation: human_required`` as a scalar; §4 defines the field as
``automation.mode``. This build requires the §4 mapping form and refuses the scalar, because the
scalar has no room for the compensating-reversal declaration that an autonomous action needs
(§6.4) and a format that grows a second form later is a format with two parsers.

**Capabilities are ``<source_id>.<capability>``.** The prefix names *which adapter*, and therefore
which credentials and which egress class; the suffix names one entry from that adapter's own
advertised closed set. AGENT_PLANE §3 writes ``core_banking.account_read`` illustratively; the
real declarations in this tree name capabilities the estate actually advertises, because a
capability screen against names the declaring author invented is not a screen.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import yaml

from ._yaml_strict import strict_safe_load
from .errors import DeclarationError
from .paths import AGENTS_DIR, packs_root, relative_to_root

#: An agent name, an action name, a task class, a gate name. Lowercase snake_case: these become
#: parts of a grant id and of a ledger row, and case ambiguity in either is a defect generator -
#: the same rule `databridge_adapter.contract` applies to everything that steers a read.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: A capability reference: ``<source_id>.<capability>``. Exactly two segments - a bare capability
#: would not say which adapter it reaches, and a three-segment one would hide the boundary.
_CAPABILITY_REF = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)$")

#: Closed top-level key set. An unknown key is a refusal, not a shrug: `databridge_graph.packs`
#: learned this the hard way when a misspelled ``subject_kind:`` loaded a pack with no subject
#: protections and no complaint. The security-relevant typo is exactly the one that must fail.
TOP_LEVEL_KEYS = frozenset({
    "agent", "version", "pack", "title", "description", "purpose", "capabilities",
    "trust_ceiling", "citation", "abstention", "actions", "budget", "evals", "supersedes",
})

#: Citation rules the compiler knows. ``none`` is spellable **on purpose**: an author who means
#: "this agent cites nothing" must write it down, and the compiler then refuses it by name rather
#: than by absence. A rule that can only be omitted is a rule nobody ever has to argue for.
CITATION_RULES = frozenset({"required_per_field", "required_per_answer", "none"})

#: The trust lattice's two labels (`databridge_core.Trust`), as a ceiling on what an agent may
#: READ. Kept as strings here so the declaration format does not depend on an enum's repr.
TRUST_CEILINGS = frozenset({"trusted", "untrusted"})

#: D12's closed automation modes. No fourth mode, and no per-deployment extension: an
#: organisation inventing ``mode: supervised`` would be inventing its own write path.
AUTOMATION_MODES = frozenset({"human_required", "human_notified", "autonomous"})

_ABSTENTION_KEYS = frozenset({
    "abstain_when", "target_alpha", "max_abstention_rate", "task_class",
    "measured_base_risk", "measured_on", "measured_by", "golden_set_digest",
})
_BUDGET_KEYS = frozenset({"tokens_per_run", "escalations_per_day", "cost_ceiling_usd_per_day"})
_EVALS_KEYS = frozenset({"golden_set", "gates"})
_ACTION_KEYS = frozenset({"automation", "touches_subject", "reversal", "description"})


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _mapping(value: Any, where: str, what: str) -> dict:
    if not isinstance(value, dict):
        raise DeclarationError(f"{where}: {what} must be a mapping, got "
                               f"{type(value).__name__}")
    return value


def _closed(value: Mapping[str, Any], allowed: frozenset[str], where: str, what: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DeclarationError(
            f"{where}: {what} carries key(s) {unknown} that this build does not declare. Allowed: "
            f"{sorted(allowed)}. An unknown key is refused rather than ignored - a misspelled "
            f"governance key that loads silently is the failure mode this closure exists for")


def _name(value: Any, where: str, what: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise DeclarationError(
            f"{where}: {what} {value!r} must be a lowercase snake_case identifier - it becomes "
            f"part of a grant id and of a ledger row")
    return value


def _number(value: Any, where: str, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeclarationError(f"{where}: {what} must be a number, got {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise DeclarationError(
            f"{where}: {what} must be a finite number, got {value!r}. `.inf` and `.nan` are the "
            f"two ways a declaration could claim 'no limit' or 'no reading' without saying so in "
            f"words a reviewer would catch - an infinite budget is an unbounded one wearing a "
            f"number, and a NaN compares false against every threshold in both directions")
    return out


def normalised_text(path: Path) -> str:
    """The file's text with line endings normalised to ``\\n``.

    The declaration digest is taken over this rather than over raw bytes so that a checkout under
    ``core.autocrlf`` does not produce a different digest for the same reviewed artefact. What was
    reviewed is the text, not the platform's newline convention.
    """
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# the parts
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ActionDecl:
    """One `ActionType` this agent may propose (D12). An action is the ONLY write path.

    ``touches_subject`` is declared, never inferred. An action that decides, records or narrows
    *which subject* something concerns is the determination CLAUDE.md's first hard constraint
    forbids automating, and the compiler refuses any automation mode but ``human_required`` for
    one - structurally, with no setting that reaches it.
    """

    name: str
    automation_mode: Optional[str]
    """``None`` means the declaration carried an ``automation:`` block with no ``mode``, or no
    block at all. Loaded rather than rejected so the compiler can refuse it *by name*, which is
    what the operator has to act on."""

    automation_declared: bool
    """Whether an ``automation:`` key was present at all. Distinguishes "you forgot the block"
    from "you wrote the block and left the mode out", which are different mistakes."""

    touches_subject: bool = False
    reversal: Optional[str] = None
    description: str = ""

    def as_record(self) -> dict:
        return {"name": self.name, "automation_mode": self.automation_mode,
                "touches_subject": self.touches_subject, "reversal": self.reversal}


@dataclass(frozen=True)
class AbstentionDecl:
    """The risk budget, plus the measurement that would make it a budget rather than a wish.

    Every measurement field is `Optional` here and required by the compiler. That asymmetry is
    deliberate: `ABSTENTION_NOT_FEASIBILITY_TESTED` must be a refusal a person reads, with the
    name of the missing field in it, not a YAML parse error three layers down.
    """

    abstain_when: str
    target_alpha: Optional[float] = None
    max_abstention_rate: Optional[float] = None
    task_class: Optional[str] = None
    measured_base_risk: Optional[float] = None
    measured_on: Optional[str] = None
    measured_by: Optional[str] = None
    golden_set_digest: Optional[str] = None

    @property
    def measured(self) -> bool:
        return (self.target_alpha is not None and self.max_abstention_rate is not None
                and self.task_class is not None and self.measured_base_risk is not None
                and bool((self.golden_set_digest or "").strip())
                and bool((self.measured_by or "").strip()))

    def missing_measurement_fields(self) -> tuple[str, ...]:
        missing = []
        if self.target_alpha is None:
            missing.append("target_alpha")
        if self.max_abstention_rate is None:
            missing.append("max_abstention_rate")
        if self.task_class is None:
            missing.append("task_class")
        if self.measured_base_risk is None:
            missing.append("measured_base_risk")
        if not (self.golden_set_digest or "").strip():
            missing.append("golden_set_digest")
        if not (self.measured_by or "").strip():
            # High 10 (R-013): an anonymous, unauditable base-risk claim is not a measurement -
            # `ABSTENTION_NOT_FEASIBILITY_TESTED`'s own detail text has always told the author to
            # "record it as `measured_base_risk` with the run in `measured_on`/`measured_by`";
            # this is that promise enforced rather than merely written.
            missing.append("measured_by")
        return tuple(missing)

    def as_record(self) -> dict:
        return {"abstain_when": self.abstain_when, "target_alpha": self.target_alpha,
                "max_abstention_rate": self.max_abstention_rate, "task_class": self.task_class,
                "measured_base_risk": self.measured_base_risk, "measured_on": self.measured_on,
                "measured_by": self.measured_by, "golden_set_digest": self.golden_set_digest}


@dataclass(frozen=True)
class BudgetDecl:
    """The three ceilings a budget admission record is made of. All three required by the
    compiler; all three `Optional` here for the same reason `AbstentionDecl`'s are."""

    tokens_per_run: Optional[int] = None
    escalations_per_day: Optional[int] = None
    cost_ceiling_usd_per_day: Optional[float] = None

    def missing_fields(self) -> tuple[str, ...]:
        return tuple(k for k, v in (("tokens_per_run", self.tokens_per_run),
                                    ("escalations_per_day", self.escalations_per_day),
                                    ("cost_ceiling_usd_per_day", self.cost_ceiling_usd_per_day))
                     if v is None)

    def as_record(self) -> dict:
        return {"tokens_per_run": self.tokens_per_run,
                "escalations_per_day": self.escalations_per_day,
                "cost_ceiling_usd_per_day": self.cost_ceiling_usd_per_day}


@dataclass(frozen=True)
class EvalsDecl:
    """Where the golden set is and what it must score. Paths are pack-relative to the repo root,
    exactly as written in the file, so a record can be read on another machine."""

    golden_set: Optional[str] = None
    gates: tuple[tuple[str, float], ...] = ()

    @property
    def gate_map(self) -> Mapping[str, float]:
        return dict(self.gates)

    def as_record(self) -> dict:
        return {"golden_set": self.golden_set, "gates": [list(g) for g in self.gates]}


# --------------------------------------------------------------------------------------
# the declaration
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentDeclaration:
    """One agent, as declared. Frozen; its identity is the digest of the reviewed file text.

    D10 says an agent is a first-class bi-temporal node with a `SourcePointer` to the file that
    declares it. This object carries `declared_in` and `declaration_digest`, which is what such a
    pointer would be built from; minting the node is out of scope here and named as such in the
    module `__init__`.
    """

    agent: str
    version: int
    pack: str
    purpose: str
    capabilities: tuple[str, ...]
    trust_ceiling: Optional[str]
    citation: Optional[str]
    abstention: Optional[AbstentionDecl]
    actions: tuple[ActionDecl, ...]
    budget: Optional[BudgetDecl]
    evals: Optional[EvalsDecl]
    declared_in: str
    declaration_digest: str
    title: str = ""
    description: str = ""
    supersedes: Optional[int] = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DeclarationError(
                f"agent {self.agent!r}: version must be a positive integer. Version 0 has no "
                f"predecessor to supersede and no successor that could supersede it")
        if self.supersedes is not None and self.supersedes >= self.version:
            raise DeclarationError(
                f"agent {self.agent!r} v{self.version} claims to supersede v{self.supersedes}. A "
                f"version supersedes an EARLIER one; replacing invalidates, never deletes (D10)")

    @property
    def ref(self) -> str:
        """``<pack>/<agent>@v<version>`` - how an agent is named in a record and in a message."""
        return f"{self.pack}/{self.agent}@v{self.version}"

    def capability_parts(self) -> Iterator[tuple[str, str]]:
        """``(source_id, capability)`` for each declared capability, in declaration order."""
        for ref in self.capabilities:
            m = _CAPABILITY_REF.match(ref)
            assert m is not None                        # validated at load
            yield m.group(1), m.group(2)

    def action(self, name: str) -> ActionDecl:
        for a in self.actions:
            if a.name == name:
                return a
        raise KeyError(f"agent {self.ref} declares no action {name!r}")

    def as_record(self) -> dict:
        return {
            "agent": self.agent, "version": self.version, "pack": self.pack,
            "purpose": self.purpose, "capabilities": list(self.capabilities),
            "trust_ceiling": self.trust_ceiling, "citation": self.citation,
            "abstention": (self.abstention.as_record() if self.abstention else None),
            "actions": [a.as_record() for a in self.actions],
            "budget": (self.budget.as_record() if self.budget else None),
            "evals": (self.evals.as_record() if self.evals else None),
            "declared_in": self.declared_in,
            "declaration_digest": self.declaration_digest,
            "supersedes": self.supersedes,
        }

    def content_digest(self) -> str:
        """A digest over the SEMANTIC content, ignoring comments and formatting.

        Distinct from `declaration_digest`, which covers the reviewed text. Two files that differ
        only in a comment are the same declaration to the compiler and different artefacts to the
        reviewer, and both facts are worth being able to state.

        Covers every governance-bearing field `as_record()` carries - including budget, evals and
        abstention (Medium 12, R-013). Two declarations that differ only in a budget ceiling, a
        gate threshold or an abstention target are different deployment policies, and a digest
        that could not tell them apart would let review or caching tooling treat them as the same
        artefact.
        """
        payload = json.dumps(self.as_record() | {"declaration_digest": None},
                             sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------------------
def parse_declaration(data: Any, *, where: str, declared_in: str,
                      declaration_digest: str) -> AgentDeclaration:
    """Build an `AgentDeclaration` from already-parsed YAML. Shape only; no governance."""
    raw = _mapping(data, where, "an agent declaration")
    _closed(raw, TOP_LEVEL_KEYS, where, "the declaration")

    agent = _name(raw.get("agent"), where, "agent name")

    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise DeclarationError(
            f"{where}: version must be an integer, got {version!r}. Versions are compared and "
            f"superseded; a string version sorts by lexicography, which puts v10 before v9")

    pack = raw.get("pack")
    if not isinstance(pack, str) or not pack.strip():
        raise DeclarationError(
            f"{where}: an agent must name the pack it belongs to. The pack decides the purpose "
            f"vocabulary and the estate, so an agent with no pack has no vocabulary to be "
            f"validated against (D5)")

    purpose = raw.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise DeclarationError(
            f"{where}: an agent must declare exactly one purpose, drawn from the pack's closed "
            f"vocabulary. Purpose is a field of the PRINCIPAL (spine D5) and there is no default: "
            f"a purpose that can be omitted is a purpose that will be omitted")

    caps_raw = raw.get("capabilities")
    if caps_raw is None:
        caps_raw = []
    if not isinstance(caps_raw, list):
        raise DeclarationError(f"{where}: capabilities must be a list of "
                               f"'<source_id>.<capability>' strings")
    capabilities: list[str] = []
    for entry in caps_raw:
        if not isinstance(entry, str) or not _CAPABILITY_REF.match(entry):
            raise DeclarationError(
                f"{where}: capability {entry!r} must be '<source_id>.<capability>' in lowercase "
                f"snake_case. The prefix names WHICH adapter - and therefore which credentials "
                f"and which egress class - so a bare capability name is not a grant anyone could "
                f"resolve")
        if entry in capabilities:
            raise DeclarationError(
                f"{where}: capability {entry!r} is listed twice. One grant per (adapter, "
                f"capability, purpose); a duplicate is either a mistake or two grants nobody can "
                f"tell apart in the ledger")
        capabilities.append(entry)

    trust_ceiling = raw.get("trust_ceiling")
    if trust_ceiling is not None:
        if not isinstance(trust_ceiling, str) or trust_ceiling not in TRUST_CEILINGS:
            raise DeclarationError(
                f"{where}: trust_ceiling {trust_ceiling!r} is not a label of the trust lattice "
                f"{sorted(TRUST_CEILINGS)} (spine D3). It is a ceiling on what the agent may "
                f"READ, not a claim about what it produces")

    citation = raw.get("citation")
    if citation is not None:
        if not isinstance(citation, str) or citation not in CITATION_RULES:
            raise DeclarationError(
                f"{where}: citation {citation!r} is not a known rule {sorted(CITATION_RULES)}. "
                f"'none' is spellable so that an agent which cites nothing has to say so and be "
                f"refused by name, rather than be refused for a missing key")

    abstention = _parse_abstention(raw.get("abstention"), where)
    actions = _parse_actions(raw.get("actions"), where)
    budget = _parse_budget(raw.get("budget"), where)
    evals = _parse_evals(raw.get("evals"), where)

    supersedes = raw.get("supersedes")
    if supersedes is not None and (isinstance(supersedes, bool)
                                   or not isinstance(supersedes, int)):
        raise DeclarationError(f"{where}: supersedes must be an integer version or absent")

    return AgentDeclaration(
        agent=agent, version=version, pack=pack.strip(), purpose=purpose.strip(),
        capabilities=tuple(capabilities), trust_ceiling=trust_ceiling, citation=citation,
        abstention=abstention, actions=actions, budget=budget, evals=evals,
        declared_in=declared_in, declaration_digest=declaration_digest,
        title=str(raw.get("title") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        supersedes=supersedes,
    )


def _parse_abstention(value: Any, where: str) -> Optional[AbstentionDecl]:
    if value is None:
        return None
    block = _mapping(value, where, "abstention")
    _closed(block, _ABSTENTION_KEYS, where, "abstention")
    when = block.get("abstain_when")
    if not isinstance(when, str) or not when.strip():
        raise DeclarationError(
            f"{where}: abstention must carry `abstain_when` - the condition under which the agent "
            f"declines to answer. A risk budget with no trigger is a number in a file")
    task_class = block.get("task_class")
    if task_class is not None:
        task_class = _name(task_class, where, "abstention task_class")
    digest = block.get("golden_set_digest")
    if digest is not None and (not isinstance(digest, str) or not digest.strip()):
        raise DeclarationError(f"{where}: abstention.golden_set_digest must be a non-blank string")
    return AbstentionDecl(
        abstain_when=when.strip(),
        target_alpha=(None if block.get("target_alpha") is None
                      else _number(block["target_alpha"], where, "abstention.target_alpha")),
        max_abstention_rate=(None if block.get("max_abstention_rate") is None else
                             _number(block["max_abstention_rate"], where,
                                     "abstention.max_abstention_rate")),
        task_class=task_class,
        measured_base_risk=(None if block.get("measured_base_risk") is None else
                            _number(block["measured_base_risk"], where,
                                    "abstention.measured_base_risk")),
        measured_on=(str(block["measured_on"]) if block.get("measured_on") else None),
        measured_by=(str(block["measured_by"]) if block.get("measured_by") else None),
        golden_set_digest=(digest.strip() if isinstance(digest, str) else None),
    )


def _parse_actions(value: Any, where: str) -> tuple[ActionDecl, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise DeclarationError(
            f"{where}: actions must be a list of single-keyed mappings, as AGENT_PLANE §3 writes "
            f"them: `- draft_credit_memo: {{automation: {{mode: human_required}}}}`")
    out: list[ActionDecl] = []
    seen: set[str] = set()
    for entry in value:
        body = _mapping(entry, where, "each actions entry")
        if len(body) != 1:
            raise DeclarationError(
                f"{where}: each actions entry is ONE action - a mapping with exactly one key, the "
                f"action name. Got keys {sorted(body)}")
        (name, spec), = body.items()
        name = _name(name, where, "action name")
        if name in seen:
            raise DeclarationError(f"{where}: action {name!r} is declared twice")
        seen.add(name)
        spec = _mapping(spec if spec is not None else {}, where, f"action {name!r}")
        _closed(spec, _ACTION_KEYS, where, f"action {name!r}")

        automation = spec.get("automation")
        declared = "automation" in spec
        mode: Optional[str] = None
        if automation is not None:
            if isinstance(automation, str):
                raise DeclarationError(
                    f"{where}: action {name!r} writes `automation: {automation!r}` as a scalar. "
                    f"D12 defines the field as `automation.mode`, and the mapping form is "
                    f"required because an autonomous action also has to declare its compensating "
                    f"`reversal` (AGENT_PLANE §6.4). Write:\n"
                    f"    {name}:\n      automation:\n        mode: {automation}")
            block = _mapping(automation, where, f"action {name!r} automation")
            unknown = sorted(set(block) - {"mode"})
            if unknown:
                raise DeclarationError(
                    f"{where}: action {name!r} automation carries key(s) {unknown}; the only key "
                    f"is `mode`")
            raw_mode = block.get("mode")
            if raw_mode is not None:
                if not isinstance(raw_mode, str) or raw_mode not in AUTOMATION_MODES:
                    raise DeclarationError(
                        f"{where}: action {name!r} automation.mode {raw_mode!r} is not one of "
                        f"{sorted(AUTOMATION_MODES)} (D12). The set is closed: an organisation "
                        f"inventing a fourth mode would be inventing its own write path")
                mode = raw_mode

        if "touches_subject" not in spec:
            # Finding 2 (R-013): `spec.get("touches_subject", False)` silently defaulted an
            # OMITTED flag to False - the non-sensitive answer - on exactly the axis CLAUDE.md's
            # first hard constraint says must never default open. The docstring on this error has
            # always said "explicitly"; the code did not enforce it. There is no safe default here
            # either way: defaulting to False can silently let an identity-narrowing action compile
            # as autonomous, and defaulting to True would silently force `human_required` onto an
            # action the author never meant to gate that way. Both are the compiler deciding a
            # governance question the author is required to answer in the file.
            raise DeclarationError(
                f"{where}: action {name!r} must declare `touches_subject: true` or "
                f"`touches_subject: false` explicitly. Whether an action decides, records or "
                f"narrows WHICH SUBJECT something concerns is the determination CLAUDE.md's first "
                f"hard constraint forbids automating - a flag that can be left out defaults FOR "
                f"the author on the one axis this build never defaults on")
        touches = spec["touches_subject"]
        if not isinstance(touches, bool):
            raise DeclarationError(
                f"{where}: action {name!r} touches_subject must be true or false, got {touches!r}")
        reversal = spec.get("reversal")
        if reversal is not None and (not isinstance(reversal, str) or not reversal.strip()):
            raise DeclarationError(
                f"{where}: action {name!r} reversal must be a non-blank description of the "
                f"compensating path, or absent")
        out.append(ActionDecl(
            name=name, automation_mode=mode, automation_declared=declared,
            touches_subject=bool(touches),
            reversal=(reversal.strip() if isinstance(reversal, str) else None),
            description=str(spec.get("description") or "").strip()))
    return tuple(out)


def _parse_budget(value: Any, where: str) -> Optional[BudgetDecl]:
    if value is None:
        return None
    block = _mapping(value, where, "budget")
    _closed(block, _BUDGET_KEYS, where, "budget")

    def _positive_int(key: str) -> Optional[int]:
        v = block.get(key)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            raise DeclarationError(f"{where}: budget.{key} must be an integer, got {v!r}")
        if v < 1:
            raise DeclarationError(
                f"{where}: budget.{key} is {v}. A ceiling of zero is not a budget, it is a "
                f"disabled agent - delete the declaration instead, so the record says so")
        return v

    cost = block.get("cost_ceiling_usd_per_day")
    if cost is not None:
        cost = _number(cost, where, "budget.cost_ceiling_usd_per_day")
        if cost <= 0:
            raise DeclarationError(
                f"{where}: budget.cost_ceiling_usd_per_day is {cost}. See budget.tokens_per_run")
    return BudgetDecl(tokens_per_run=_positive_int("tokens_per_run"),
                      escalations_per_day=_positive_int("escalations_per_day"),
                      cost_ceiling_usd_per_day=cost)


def _parse_evals(value: Any, where: str) -> Optional[EvalsDecl]:
    if value is None:
        return None
    block = _mapping(value, where, "evals")
    _closed(block, _EVALS_KEYS, where, "evals")
    golden = block.get("golden_set")
    if golden is not None and (not isinstance(golden, str) or not golden.strip()):
        raise DeclarationError(f"{where}: evals.golden_set must be a non-blank path or absent")
    gates_raw = block.get("gates")
    gates: list[tuple[str, float]] = []
    if gates_raw is not None:
        gates_map = _mapping(gates_raw, where, "evals.gates")
        for key in sorted(gates_map):
            _name(key, where, "eval gate name")
            score = _number(gates_map[key], where, f"eval gate {key!r}")
            if not (0.0 <= score <= 1.0):
                raise DeclarationError(
                    f"{where}: eval gate {key!r} threshold {score} is not in [0, 1]. Gates are "
                    f"rates; a gate outside the unit interval cannot be met or missed")
            gates.append((key, score))
    return EvalsDecl(golden_set=(golden.strip() if isinstance(golden, str) else None),
                     gates=tuple(gates))


def load_declaration(path: Path) -> AgentDeclaration:
    """Read one ``packs/<pack>/agents/<agent>.yaml``.

    The file name must match the declared agent name, for the reason `purposes.py` gives about a
    pack directory: the path is part of the artefact's identity, and a file whose name disagrees
    with its contents is a file two people will refer to by two different names.
    """
    p = Path(path).resolve()
    if not p.is_file():
        raise DeclarationError(f"{p}: no such agent declaration")
    text = normalised_text(p)
    try:
        # Finding 8 (R-013): `yaml.safe_load` silently keeps the LAST of a duplicate key - a
        # reviewed `citation: required_per_field` followed later in the same mapping by a
        # `citation: none` would load as the second value with no trace of the first. A
        # governance document that can disagree with itself and still load is a governance
        # document a review did not actually cover.
        data = strict_safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise DeclarationError(f"{p}: not valid YAML: {exc}") from exc
    decl = parse_declaration(data, where=str(p), declared_in=relative_to_root(p),
                             declaration_digest=text_digest(text))
    if p.stem != decl.agent:
        raise DeclarationError(
            f"{p}: declares agent {decl.agent!r} but the file is named {p.stem!r}. The path is "
            f"part of the artefact's identity and a SourcePointer will cite it (D10)")
    parent_pack = p.parent.parent.name
    if parent_pack != decl.pack:
        raise DeclarationError(
            f"{p}: declares pack {decl.pack!r} but sits under packs/{parent_pack}/. The directory "
            f"name is the pack's identity (`purposes.py` applies the same rule)")
    return decl


def load_pack_agents(pack: str, root: Optional[Path] = None) -> tuple[AgentDeclaration, ...]:
    """Every declaration under ``packs/<pack>/agents/``, sorted by name then version.

    A pack with no ``agents/`` directory declares no agents. Absence is not an error - most packs
    have none - but it is not a licence either: with no declaration there is nothing to compile,
    and with nothing compiled there is no grant, and with no grant nothing runs.
    """
    base = (Path(root) if root is not None else packs_root()) / pack / AGENTS_DIR
    if not base.is_dir():
        return ()
    found = [load_declaration(p) for p in sorted(base.glob("*.yaml"))]
    seen: dict[tuple[str, int], str] = {}
    for decl in found:
        key = (decl.agent, decl.version)
        if key in seen:
            raise DeclarationError(
                f"pack {pack!r} declares {decl.agent}@v{decl.version} twice "
                f"({seen[key]} and {decl.declared_in})")
        seen[key] = decl.declared_in
    return tuple(sorted(found, key=lambda d: (d.agent, d.version)))


def iter_declarations(packs: Sequence[str],
                      root: Optional[Path] = None) -> Iterator[AgentDeclaration]:
    for pack in packs:
        yield from load_pack_agents(pack, root)
