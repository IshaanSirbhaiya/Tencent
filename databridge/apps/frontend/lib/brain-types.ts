// TypeScript mirror of the gateway /brain/* response shapes (brain_server.py serialisation).
// Wire shapes only — the frontend renders them, never computes from them (D-FE-2).
import type { SourcePointer } from "./types";

export type NodeKind =
  | "patient"
  | "org"
  | "package"
  | "test"
  | "visit"
  | "clinic"
  | "coverage_rule"
  | "document";

export interface BrainNode {
  id: string;
  kind: NodeKind;
  label: string;
  canonical_id: string | null;
  attrs: Record<string, unknown>;
  valid_from: string | null; // ISO — when the fact became true in the world
  valid_to: string | null; // null = still true
  system_from: string | null; // when DataBridge learned it
  system_to: string | null;
  n_sources: number;
}

export interface BrainEdge {
  id: string;
  source: string; // React-Flow shape (src node id)
  target: string;
  kind: string;
  weight: number;
  attrs: Record<string, unknown>;
  valid_from: string | null;
  valid_to: string | null;
  n_sources: number;
}

export interface BrainGraph {
  nodes: BrainNode[];
  edges: BrainEdge[];
  stats: { nodes: number; edges: number; sources: number; provenance_pct: number };
  provenance_by_source: Record<string, number>;
}

export interface Citation {
  entity_id: string;
  label: string;
  kind: string;
  pointers: SourcePointer[];
}

export interface BrainAnswer {
  question: string;
  answer: string;
  grounded: boolean;
  refusal: string;
  matched_entities: string[];
  citations: Citation[];
  as_of?: string | null;
}

export interface Insight {
  text: string;
  focus: string; // node id to zoom to
  kind: string;
  detail?: string;
  citations: SourcePointer[];
}

export interface TimelineEvent {
  kind: "node" | "edge";
  label: string;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
}

export interface BrainPathStep {
  edge_kind: string;
  to: string;
  to_label: string;
  pointers: SourcePointer[];
}

export interface Connection {
  edge_kind: string;
  direction: "in" | "out";
  other_id: string;
  other_label: string;
  other_kind: string;
  pointers: SourcePointer[];
}

export interface NodeDetail {
  node: BrainNode;
  sources: SourcePointer[];
  connections: Connection[];
  // Candidate patients a DOCUMENT claims to be about. Never a determination: `candidate_count`
  // is how many the resolver returned, and every one still needs an in-person check.
  claims: {
    id: string;
    label: string;
    match_score: number | null;
    matched_on: string | null;
    candidate_count: number | null;
    requires_human_verification: boolean;
  }[];
}

export interface IngestResult {
  source_path: string;
  entities_added: number;
  new_entities: string[]; // labels of genuinely new nodes (not corroborated existing ones)
  relations_added: number;
  refused: string[];
  stats: { nodes: number; edges: number; sources: number };
}
