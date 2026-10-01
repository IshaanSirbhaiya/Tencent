"""**databridge_agentdef** - agent declarations, and the compiler that turns them into grants.

AGENT_PLANE D10/D11, in one line:

    an agent declaration is pack YAML -> the compiler produces FOUR artefacts or refuses ->
    an agent that will not compile cannot run, and there is no override flag.

Before this package, `CAPABILITY_GRANT` was a declared meta-kind that nothing minted and no agent
declaration existed anywhere, so D11's central claim - *"an agent that lacks a grant cannot run"* -
was a sentence rather than a control. This package makes it a control: `compile_agent` is the only
thing that produces a `CompiledAgent`, `GrantedAuthority` is the only runtime handle, and a
capability outside the declaration cannot be requested through either.

## The four artefacts (§3)

| # | Artefact | Type | Bound to |
|---|---|---|---|
| 1 | capability grant set | `CapabilityGrantSet` of `CapabilityGrant` | a specific adapter **and** purpose |
| 2 | sink policy | `SinkPolicy` | `databridge_adapter.TRUSTED_SINKS`, in full |
| 3 | budget admission record | `BudgetAdmission` | the harness that must enforce it |
| 4 | eval binding | `EvalBinding` | a golden set, pinned by digest |

## What is NOT built here, and what it would take

**D10's graph persistence - an agent as a bi-temporal node - is out of scope.** Nothing in this
package writes to the graph. Making it real needs, concretely:

1. **Meta-model kinds.** `NodeMetaKind` gains `AGENT`, `CAPABILITY_GRANT`, `ACTION_TYPE`,
   `ADAPTER`, `CERTIFICATE`; `EdgeMetaKind` gains `GRANTED_CAPABILITY`, `MAY_INVOKE`,
   `BOUND_TO_PACK`, `RESOLVED_THROUGH`, `SUPERSEDES`. `databridge_graph.meta` is closed and
   changes only by ADR, so this is an ADR first and a code change second - and the ADR has to say
   whether any of them is subject-eligible (they are not: an agent is not a person).
2. **A `SourcePointer` per node.** `GraphNode` refuses construction without a source.
   `CompiledAgent` already carries `declared_in` and `declaration_digest`, which is what such a
   pointer is built from: `SourcePointer(source_id="pack", file_path=declared_in,
   trust=Trust.TRUSTED, ...)` - and "which pointer kind describes a whole reviewed YAML file" is
   an open question the pointer vocabulary does not currently answer cleanly.
3. **A pack that declares the kinds.** The vocabulary is closed to the ENABLED packs, so an
   `AGENT` node kind declared by nobody is skipped, never coined. Either every pack declares them
   or they become meta-level kinds available to all - which is a design decision, not a detail.
4. **Bi-temporal semantics for `SUPERSEDES`.** "Replacing invalidates, never deletes" means v3's
   `system_from` opens as v2's `system_to` closes, in one transaction. `AgentDeclaration.supersedes`
   records the intent; nothing enforces the transaction because nothing writes yet.
5. **Grant revocation.** A grant is an edge with its own bi-temporal history, so revoking one is
   closing `valid_to` - and the runtime `GrantedAuthority` is currently an in-memory snapshot with
   no notion of a grant that stopped being valid mid-run. That gap is real and is not papered over
   here.

**Nothing enforces the budget.** `BudgetAdmission` is a record whose `enforced_by` field names
`databridge_harness`; wiring it is the harness's change, not this package's.

**The eval binding is not run.** `EvalBinding.check` scores a result mapping a caller supplies.
Running the golden set needs the harness and a model, and a compiler that called a model would
produce a different grant on Tuesday.
"""
from __future__ import annotations

from .artefacts import (
    BudgetAdmission,
    CapabilityGrant,
    CapabilityGrantSet,
    CompiledAgent,
    EvalBinding,
    SinkPolicy,
    build_sink_policy,
)
from .authority import GrantedAuthority
from .catalogue import AdapterAdvert, AdapterCatalogue, load_catalogue
from .compiler import (
    ABSTENTION_GATE,
    GROUNDING_GATE,
    STRUCTURAL_REFUSALS,
    CompilationDecision,
    GoldenSet,
    Outcome,
    Refusal,
    RefusalCode,
    catalogue_digest,
    compile_agent,
    compile_all,
    compile_or_raise,
    read_golden_set,
)
from .declaration import (
    AUTOMATION_MODES,
    CITATION_RULES,
    TRUST_CEILINGS,
    AbstentionDecl,
    ActionDecl,
    AgentDeclaration,
    BudgetDecl,
    EvalsDecl,
    iter_declarations,
    load_declaration,
    load_pack_agents,
    parse_declaration,
)
from .errors import (
    AgentDefError,
    AmbientAuthorityRefused,
    CatalogueError,
    CompilationRefused,
    DeclarationError,
)
from .feasibility import (
    KOTTE_CITATION,
    KNOWN_TASK_CLASSES,
    UNCERTIFIABLE_ALPHA,
    FeasibilityReport,
    assess,
    minimum_abstention,
)
from .paths import databridge_root, estates_root, packs_root

__all__ = [
    # declarations
    "AgentDeclaration", "ActionDecl", "AbstentionDecl", "BudgetDecl", "EvalsDecl",
    "load_declaration", "load_pack_agents", "iter_declarations", "parse_declaration",
    "CITATION_RULES", "TRUST_CEILINGS", "AUTOMATION_MODES",
    # what adapters advertise
    "AdapterAdvert", "AdapterCatalogue", "load_catalogue",
    # the compiler
    "compile_agent", "compile_or_raise", "compile_all", "CompilationDecision", "Outcome",
    "Refusal", "RefusalCode", "STRUCTURAL_REFUSALS", "GROUNDING_GATE", "ABSTENTION_GATE",
    "read_golden_set", "GoldenSet", "catalogue_digest",
    # the four artefacts
    "CapabilityGrant", "CapabilityGrantSet", "SinkPolicy", "BudgetAdmission", "EvalBinding",
    "CompiledAgent", "build_sink_policy",
    # the runtime handle
    "GrantedAuthority",
    # the feasibility test
    "assess", "minimum_abstention", "FeasibilityReport", "UNCERTIFIABLE_ALPHA",
    "KNOWN_TASK_CLASSES", "KOTTE_CITATION",
    # errors
    "AgentDefError", "DeclarationError", "CatalogueError", "CompilationRefused",
    "AmbientAuthorityRefused",
    # paths
    "packs_root", "estates_root", "databridge_root",
]
