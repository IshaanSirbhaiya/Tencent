"""DataBridge graph — the Company Brain (Pillar 1, T08).

A typed, bi-temporal knowledge graph (ADR-0003 temporal model; ADR-0005 flat-graph-first;
ADR-0008 closed vocab) over SQLite, with grounded NL query (every answer value traces to a
graph node/edge SourcePointer — P4) and a pre-built investor-demo seed (Acme Corp).
"""
from __future__ import annotations

from .demo_seed import (
    SEED_SYSTEM_TIME,
    build_seed_edges,
    build_seed_nodes,
    fresh_demo_store,
    load_demo_seed,
)
from .extractor import GraphExtractionResult, extract_graph, gold_extraction_handler
from .merge import UpsertOutcome, token_set_ratio, upsert_edge, upsert_node
from .query import (
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
    "Snapshot", "Subgraph", "GraphPath", "GroundedAnswer",
    # extractor
    "extract_graph", "GraphExtractionResult", "gold_extraction_handler",
    # demo seed
    "load_demo_seed", "fresh_demo_store", "build_seed_nodes", "build_seed_edges",
    "SEED_SYSTEM_TIME",
]
