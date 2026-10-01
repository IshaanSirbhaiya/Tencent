"""The **Shell** — thin, agentic, and structurally unable to reach a source (`ADAPTER_AGENT_SPEC` §1, §9).

The Shell is the only layer with a model in it, and its whole job is to turn a task into **one
validated `CapabilityRequest`, or an escalation**. It never executes anything. It never sees a
row. It holds no credential and no driver handle.

## What makes "holds no credentials" a structural claim rather than a promise

§1 is careful about this and so is this module. Three things are true here, and a fourth is not:

1. **This module imports no driver.** `test_the_shell_imports_no_driver` is an AST scan in the
   shape of 3A's `test_the_contract_package_does_no_io`. A Core lives under
   `databridge_adapter/sources/`, imports SQLAlchemy, and is not importable from here.
2. **No constructor parameter can carry a credential.** `test_the_shell_takes_no_connection`
   inspects `__init__`'s signature for anything that could hold a URL, a DSN, an engine or a
   secret. A Shell that could be *handed* a connection would make the boundary a convention.
3. **No attribute of a live Shell holds one.** `test_a_live_shell_holds_no_secret_material` walks
   the instance and refuses anything that looks like a DSN or is a SQLAlchemy `Engine`.
4. **It is NOT a proof that a compromised Shell process cannot reach the network.** §1 says this
   in as many words: process isolation removes the Shell's *authorised* access, not its ability to
   open a socket — only the egress firewall does the latter. The claim to make in public is that
   the system is **architecturally missing the exfiltration leg**, not that any process is
   incapable of one.

## Fail closed, and never a retry loop

§10: *"Shell proposes an unadvertised capability → reject; log; do not escalate to a model."*
A proposal is validated **once** against the advertised closed set — the capability, the entity,
the fields, the ordering, the key. If it fails, `EscalationRequired` is raised and a human adds a
named query to the pack. It is never fed back to the model to try again, because a screen whose
rejection is a prompt is a screen that eventually passes anything (§6's C3→C0 promotion is the
slower path, and it is the correct one).

## The token budget is a design requirement, not an optimisation

§9a: tool-selection accuracy degrades past 30–50 tools, which a fleet reaches at about a dozen
sources with five verbs each, and Anthropic documents ~55,000 tokens of MCP tool definitions
before any work happens. So a Shell sees **its own adapter's description and nothing else** — the
process split already enforces that physically — and `describe_for_model` renders it into a bounded
brief that `test_the_shell_brief_stays_within_its_token_budget` gates in CI, because this number
only ever grows and nobody notices until the bill does.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .contract import CapabilityRequest, Principal, Projection
from .describe import SourceDescription
from .errors import CapabilityNotAdvertised, EscalationRequired

#: Every task class this module may ask for. A `task_class`, never a model name (repo rule): the
#: routing table in `config/models.yaml` decides the model, so swapping the whole deployment to
#: local models is a config change.
DEFAULT_TASK_CLASS = "agentic_orchestration"

#: Ceiling on the rendered capability brief, in characters. ~4 chars/token, so this is roughly a
#: 1,500-token budget per adapter — an order of magnitude under the 55k a naive fleet-wide tool
#: catalogue costs, and it fails in CI rather than on an invoice.
BRIEF_BUDGET_CHARS = 6000


class ProposedRead(BaseModel):
    """What the model is asked to produce. Deliberately tiny and entirely structural.

    There is no free-text field, no `sql`, no `predicate`, and no `reason` the system acts on. The
    model chooses among things the adapter already advertises; everything else is refused. That is
    what keeps a wrong proposal a REJECTION rather than a plausible answer — a semantic layer fails
    explicitly, text-to-SQL fails plausibly (§6).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    entity: str
    fields: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ()
    limit: int = Field(default=200, ge=1, le=100_000)
    #: The model's own account of why it cannot answer inside the advertised set. Recorded on the
    #: escalation, never acted on.
    escalate: bool = False
    escalation_reason: str = ""


class AdapterShell:
    """Turns a task into a validated `CapabilityRequest`, or escalates. Executes nothing.

    Constructed with a `SourceDescription` (metadata; no data, no credentials, no connection
    string) and a `Harness`. It has no `resolve`, no `run`, and no reference to a Core — the
    orchestrator takes the request the Shell produced and gives it to Policy.
    """

    def __init__(self, *, description: SourceDescription, harness: Any,
                 task_class: str = DEFAULT_TASK_CLASS,
                 named_queries: Optional[Mapping[str, str]] = None) -> None:
        self._description = description
        self._harness = harness
        self._task_class = task_class
        self._named_queries = dict(named_queries or {})

    @property
    def source_id(self) -> str:
        return self._description.source_id

    @property
    def description(self) -> SourceDescription:
        return self._description

    # --- the brief -------------------------------------------------------------------
    def describe_for_model(self) -> str:
        """The bounded, data-free brief. **One adapter's capabilities, never the fleet's.**

        Contains: the source id, the advertised capability set, and each entity's columns with
        their pack-level type, their trust class and whether the contract calls them sensitive.
        Contains no values, no counts, no sample rows, and no connection details.
        """
        entities = {
            e.name: {
                "key": list(e.key),
                "fields": {f.name: f"{f.type}/{f.trust.value}"
                           + ("/sensitive" if f.sensitive else "")
                           for f in e.fields},
            }
            for e in self._description.entities
        }
        brief = {
            "source_id": self._description.source_id,
            "kind": self._description.kind,
            "capabilities": sorted(self._description.capabilities),
            "pinning_tier": self._description.pinning_tier,
            "egress_class": self._description.egress_class,
            "entities": entities,
            "named_queries": sorted(self._named_queries),
            "rules": [
                "Choose one capability, one entity, and a subset of that entity's fields.",
                "order_by must end with the entity's key so row ordinals are stable.",
                "You may not write SQL, a filter expression, or any free text that is executed.",
                "If the task cannot be answered inside this set, set escalate=true.",
            ],
        }
        return json.dumps(brief, indent=1, sort_keys=True)

    # --- the one call ----------------------------------------------------------------
    def propose(self, task: str, *, principal: Principal, limit: int = 200,
                seed: Optional[int] = None) -> CapabilityRequest:
        """One model call, one validation, then a request or an escalation. No retry loop."""
        messages = [
            {"role": "system",
             "content": ("You plan READS for exactly one data source. You never write SQL and you "
                         "never see data. Return only a plan that stays inside the advertised "
                         "capability set below; if the task needs anything outside it, escalate.\n"
                         + self.describe_for_model())},
            {"role": "user", "content": task},
        ]
        result = self._harness.complete_structured(
            self._task_class, messages, ProposedRead, seed=seed, source_id=self.source_id)
        return self.validate(result.parsed, principal=principal, limit=limit, task=task)

    def validate(self, proposal: ProposedRead, *, principal: Principal, limit: int = 200,
                 task: str = "") -> CapabilityRequest:
        """Screen a proposal against the ADVERTISED set. Public, so it is testable without a model.

        Every branch here either returns a request or raises. There is deliberately no branch that
        repairs a proposal: a screen that quietly fixes what a model got wrong is a screen whose
        rejections nobody ever sees, and the C3→C0 promotion in §9 depends on a human seeing them.
        """
        if proposal.escalate:
            raise EscalationRequired(
                f"{self.source_id}: the planner could not answer inside the advertised capability "
                f"set. Reason given (recorded, not acted on): "
                f"{proposal.escalation_reason.strip() or '(none)'}. Task: {task!r}. The fix is a "
                f"pack-declared named query reviewed once and deterministic forever (§6, §9)")

        if proposal.capability not in self._description.capabilities:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} does not advertise {proposal.capability!r}; advertised: "
                f"{sorted(self._description.capabilities)}. Rejected and logged — never handed "
                f"back to a model to reconsider (§10)")

        try:
            entity = self._description.entity(proposal.entity)
        except KeyError:
            raise EscalationRequired(
                f"{self.source_id!r} describes no entity {proposal.entity!r}; described: "
                f"{sorted(e.name for e in self._description.entities)}. A planner that names an "
                f"entity the source does not have has not made a smaller mistake than one that "
                f"writes bad SQL — it has made the same mistake in a form that fails explicitly, "
                f"which is the point") from None

        described = [f.name for f in entity.fields]
        fields = tuple(proposal.fields) or tuple(described)
        unknown = [f for f in fields if f not in described]
        if unknown:
            raise EscalationRequired(
                f"{self.source_id}.{entity.name} has no field(s) {unknown}; described: "
                f"{described}. Refused rather than dropped: a projection silently narrowed to the "
                f"fields that happened to exist would answer a different question than the one "
                f"asked, and the citation would look just as good")
        if not entity.key:
            raise EscalationRequired(
                f"{self.source_id}.{entity.name} declares no key, so no ordering over it is total "
                f"and no pointer into it would be a stable citation (§7)")

        # The key is always projected and the ordering always ends with it. This is not the model's
        # decision to make: a pointer cites the key, so a plan that omitted it would produce
        # uncitable rows, and asking a model to remember that every time is asking for the one
        # thing it will eventually forget.
        fields = tuple(dict.fromkeys((*fields, *entity.key)))
        order_by = tuple(t for t in proposal.order_by if t.lstrip("-") not in entity.key)
        order_by = tuple(dict.fromkeys((*order_by, *entity.key)))
        unknown_order = [t for t in order_by if t.lstrip("-") not in fields]
        if unknown_order:
            raise EscalationRequired(
                f"{self.source_id}.{entity.name}: order_by names {unknown_order}, which is not "
                f"projected. You cannot order by what you did not read (D6)")

        return CapabilityRequest(
            source_id=self.source_id,
            capability=proposal.capability,
            principal=principal,
            projection=Projection(entity=entity.name, fields=fields, key=tuple(entity.key)),
            order_by=order_by,
            limit=min(int(proposal.limit), int(limit)),
        )
