"""DataBridge graph — the clinic operational brain.

A typed, bi-temporal knowledge graph (ADR-0003 temporal model; ADR-0005 flat-graph-first;
ADR-0009 closed vocab) over SQLite, with grounded NL query (every answer value traces to a
graph node/edge SourcePointer — P4) and a seed built from the real clinic fixtures.
"""
from __future__ import annotations

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
from .extractor import GraphExtractionResult, extract_graph, gold_extraction_handler
from .identity import (
    EntityMatch,
    IDENTITY_CLAIMED_ON_DOCUMENT,
    IDENTITY_REGISTRY_ONLY,
    ambiguous_refusal,
    identity_caveat,
    resolve_entity,
    same_label_node_ids,
)
from .merge import UpsertOutcome, token_set_ratio, upsert_edge, upsert_node
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
from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind, node_id
from .store import GraphStore, row_to_edge, row_to_node

__all__ = [
    # schema
    "NodeKind", "EdgeKind", "GraphNode", "GraphEdge", "node_id",
    # store
    "GraphStore", "row_to_node", "row_to_edge",
    # merge
    "upsert_node", "upsert_edge", "UpsertOutcome", "token_set_ratio",
    # query
    "as_of", "neighbors", "path", "timeline", "nl_query", "link_entities",
    "Snapshot", "Subgraph", "GraphPath", "GroundedAnswer", "Citation", "AnswerValue",
    # extractor
    "extract_graph", "GraphExtractionResult", "gold_extraction_handler",
    # identity (CLAUDE.md constraint 1 — the caveat, and the refusal to pick between namesakes)
    "EntityMatch", "resolve_entity", "same_label_node_ids", "ambiguous_refusal",
    "identity_caveat", "IDENTITY_CLAIMED_ON_DOCUMENT", "IDENTITY_REGISTRY_ONLY",
    # clinic seed
    "load_clinic_seed", "fresh_clinic_store", "build_seed", "build_seed_nodes",
    "build_seed_edges", "orphan_node_ids", "patient_census", "claimed_patient_node_ids",
    "SEED_SYSTEM_TIME",
]
