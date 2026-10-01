"""DataBridge graph — the typed, bi-temporal operational graph.

ADR-0003 temporal model; ADR-0005 flat-graph-first; ADR-0009 closed vocabulary — over SQLite, with
grounded NL query (every answer value traces to a graph node/edge SourcePointer — P4).

The vocabulary is closed but no longer written in code: `meta.py` holds the domain-free
meta-model (spine §4 Layer 0), `packs.py` loads and validates the domain packs under
`packs/<name>/ontology.yaml` (Layer 1) and computes the enabled union (Layer 2), and `kinds.py`
exposes it as `NodeKind` / `EdgeKind`.

`clinic_seed` and `clinic_history` are the healthcare pack's own modules and still live here
pending Phase 1B, which moves the vertical out of `packages/` entirely. They are the reason
`test_no_domain_branching` carries a quarantine list; nothing else in this package names a domain.
"""
from __future__ import annotations

from .attestation import (
    Attestation,
    AttestationError,
    enforce_attestation,
    is_determination_kind,
    record_determination,
    subject_kind_of_node_id,
)
from .clinic_seed import (
    SEED_SYSTEM_TIME,
    build_seed,
    build_seed_edges,
    build_seed_nodes,
    claimed_patient_node_ids,
    fresh_clinic_store,
    load_clinic_seed,
    orphan_node_ids,
    patient_census,
)
from .estate_seed import (
    BuildReport,
    SeedMapError,
    build_pack_graph,
    load_pack_graph,
    load_seed_map,
)
from .extractor import GraphExtractionResult, extract_graph, gold_extraction_handler
from .government_seed import (
    build_government_graph,
    government_seed_map,
    load_government_seed,
)
from .growth import (
    STRUCTURAL_ESCALATIONS,
    GrowthDecision,
    GrowthEntry,
    GrowthError,
    GrowthLedger,
    GrowthPolicy,
    GrowthProposal,
    Outcome,
    ProposalAxis,
    SimilarityReport,
    Trigger,
    TriggerFinding,
    evaluate_proposal,
    ontology_fragment,
    similarity_report,
)
from .identity import (
    EntityMatch,
    IDENTITY_CLAIMED_ON_DOCUMENT,
    IDENTITY_REGISTRY_ONLY,
    ambiguous_refusal,
    identity_caveat,
    resolve_entity,
    same_label_node_ids,
)
from .kinds import edge_kinds_binding_to, node_kinds_binding_to
from .merge import (
    UpsertOutcome,
    never_fuzzy_merge_kinds,
    token_set_ratio,
    upsert_edge,
    upsert_node,
)
from .meta import (
    ATTESTED_META_KINDS,
    CLAIM_META_KINDS,
    SUBJECT_ELIGIBLE_META_KINDS,
    EdgeMetaKind,
    NodeMetaKind,
)
from .packs import (
    Pack,
    PackError,
    SubjectKind,
    UnknownKind,
    Vocabulary,
    active_vocabulary,
    available_packs,
    bind_deployment,
    build_vocabulary,
    deployment,
    load_pack,
)
from .query import (
    AnswerValue,
    Citation,
    GraphPath,
    GroundedAnswer,
    Snapshot,
    Subgraph,
    as_of,
    link_entities,
    neighbors,
    nl_query,
    path,
    timeline,
)
from .schema import (
    IDENTITY_BEARING_FIELDS,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    node_id,
)
from .ptr_index import (
    AffectedEmission,
    AffectedEntity,
    Answer,
    BackfillReport,
    CascadeReport,
    INVALIDATING_VERDICTS,
    ImpactReport,
    KNOWN_VERDICTS,
    ReverseIndex,
    locator_key,
    normalise_verdict,
    pointer_identity,
)
from .store import GraphStore, row_to_edge, row_to_node

__all__ = [
    # schema
    "NodeKind", "EdgeKind", "GraphNode", "GraphEdge", "node_id", "IDENTITY_BEARING_FIELDS",
    # meta-model (spine §4 Layer 0) — domain-free
    "NodeMetaKind", "EdgeMetaKind", "node_kinds_binding_to", "edge_kinds_binding_to",
    "ATTESTED_META_KINDS", "CLAIM_META_KINDS", "SUBJECT_ELIGIBLE_META_KINDS",
    # attestation (CLAUDE.md constraint 1 — the wall that makes a determination need a human)
    "Attestation", "AttestationError", "enforce_attestation", "is_determination_kind",
    "record_determination", "subject_kind_of_node_id",
    # packs (Layer 1) + deployment binding (Layer 2)
    "Pack", "PackError", "SubjectKind", "UnknownKind", "Vocabulary", "active_vocabulary",
    "available_packs", "bind_deployment", "build_vocabulary", "deployment", "load_pack",
    "never_fuzzy_merge_kinds",
    # store
    "GraphStore", "row_to_node", "row_to_edge",
    # the reverse pointer index (SYSTEM_DESIGN §5) — source_id -> pointers -> nodes/edges
    # -> emitted records, maintained on write and honest about what it never saw
    "ReverseIndex", "Answer", "ImpactReport", "CascadeReport", "BackfillReport",
    "AffectedEntity", "AffectedEmission", "INVALIDATING_VERDICTS", "KNOWN_VERDICTS",
    "locator_key", "normalise_verdict", "pointer_identity",
    # merge
    "upsert_node", "upsert_edge", "UpsertOutcome", "token_set_ratio",
    # query
    "as_of", "neighbors", "path", "timeline", "nl_query", "link_entities",
    "Snapshot", "Subgraph", "GraphPath", "GroundedAnswer", "Citation", "AnswerValue",
    # extractor
    "extract_graph", "GraphExtractionResult", "gold_extraction_handler",
    # the graph-growth protocol (PHASE_PLAN Phase 4) — how a closed vocabulary widens, and who
    # decides. Deterministic; a determination or subject-touching proposal ALWAYS escalates.
    "GrowthProposal", "GrowthPolicy", "GrowthDecision", "GrowthEntry", "GrowthLedger",
    "GrowthError", "Outcome", "ProposalAxis", "SimilarityReport", "Trigger", "TriggerFinding",
    "evaluate_proposal", "ontology_fragment", "similarity_report", "STRUCTURAL_ESCALATIONS",
    # the map-driven estate builder, and the government pack's entry point over it
    "build_pack_graph", "load_pack_graph", "load_seed_map", "BuildReport", "SeedMapError",
    "build_government_graph", "load_government_seed", "government_seed_map",
    # identity (CLAUDE.md constraint 1 — the caveat, and the refusal to pick between namesakes)
    "EntityMatch", "resolve_entity", "same_label_node_ids", "ambiguous_refusal",
    "identity_caveat", "IDENTITY_CLAIMED_ON_DOCUMENT", "IDENTITY_REGISTRY_ONLY",
    # clinic seed
    "load_clinic_seed", "fresh_clinic_store", "build_seed", "build_seed_nodes",
    "build_seed_edges", "orphan_node_ids", "patient_census", "claimed_patient_node_ids",
    "SEED_SYSTEM_TIME",
]
