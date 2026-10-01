// TypeScript mirror of the gateway /brain/* response shapes (brain_server.py serialisation).
// Wire shapes only — the frontend renders them, never computes from them (D-FE-2).
import type { SourcePointer } from "./types";

/** A graph node kind, as the gateway serialises it.
 *
 *  Deliberately NOT a closed union any more: the vocabulary belongs to the active pack
 *  (`graph_vocabulary` in `config/packs.json`), so a frontend union would be one pack's ontology
 *  compiled into the platform — and would break the moment a second pack shipped a kind of its own.
 *  Unknown kinds degrade through `nodeStyle()` rather than failing to typecheck. */
export type NodeKind = string;

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
  /** Node ids the question could equally have meant. Non-empty ONLY on a refusal: the registry
   *  holds three "Loh Wei Ming", and both `/brain/query` branches now decline to pick one rather
   *  than returning whichever the ranker sorted first (CLAUDE.md hard constraint 1). It is the
   *  way FORWARD, not an error detail — asking again with one of these ids resolves cleanly, so
   *  a UI should offer them rather than swallow them. */
  candidates?: string[];
  matched_entities: string[];
  citations: Citation[];
  as_of?: string | null;
  /** Which path answered (E2E run 2, N-14): "graph" here; a delegated answer is `DelegatedAnswer`. */
  routed_to?: "graph";
  route?: BrainRoute;
  /** Delegation was tried and read nothing (the planner asked instead of guessing). */
  delegation_attempt?: DelegationAttempt;
}

/** How `/brain/query` decided where a question goes — rules first, the router second. */
export interface BrainRoute {
  route: string;
  rule?: string;
  reason?: string;
  decided_by?: string;
  signals?: string[];
  fallback_from?: string;
  delegation_refused?: { code: string; message: string };
  delegation_unavailable?: string;
  delegation_failed?: string;
}

export interface DelegationAttempt {
  run_id: string | null;
  status: string | null;
  questions: string[];
  trace_request_id?: string | null;
  activity_url?: string | null;
}

/** A combined value's pointer: `derived`, expanding to exactly the inputs it was computed from. */
export type DelegatedPointer = SourcePointer & {
  derived_from?: DelegatedPointer[];
  agg_window?: string | null;
  sample_row_ids?: string[];
};

export interface DelegatedCell {
  value: string | number | boolean | null;
  pointer: DelegatedPointer;
}

export interface DelegatedRow {
  cells: Record<string, DelegatedCell | null>;
  candidate: boolean;
  requires_human_selection?: boolean;
  link_evidence?: SourcePointer[];
}

export interface DelegatedTable {
  columns?: string[];
  rows?: DelegatedRow[];
  absent?: string[];
  excluded?: string[];
}

export interface DelegatedCoverage {
  searched: string[];
  skipped: Record<string, unknown>[];
  reasons: string[];
  reduced: boolean;
}

export interface CandidateFlag {
  where: "table_row" | "claim";
  row?: number;
  claim?: number;
  text?: string | null;
  requires_human_selection: boolean;
}

export interface DelegatedClaim {
  text: string;
  citations: string[];
  supported: boolean;
  reason?: string | null;
  subject_candidate: boolean;
  requires_human_selection: boolean;
}

/** `/brain/query` answered by delegation: the computed table, never a narrated guess. */
export interface DelegatedAnswer {
  routed_to: "federated";
  route: BrainRoute;
  question: string;
  answer: string;
  grounded: boolean;
  refusal: string;
  status: string | null;
  table: DelegatedTable;
  claims: DelegatedClaim[];
  citations: Record<string, unknown>[];
  coverage: DelegatedCoverage;
  candidate_flags: CandidateFlag[];
  requires_human_selection: boolean;
  questions: string[];
  run_id: string | null;
  trace_request_id?: string | null;
  activity_url: string | null;
  matched_entities: string[];
}

/** One record a refused reference could mean — presented, never chosen. */
export interface CandidateDetail {
  id: string;
  label?: string;
  kind?: string;
  source_ids?: string[];
  pointers?: SourcePointer[];
  subject?: boolean;
  requires_human_selection?: boolean;
}

export interface BrainRefusal {
  code: string;
  message: string;
  nearest_alternative?: string | null;
  candidates?: string[];
  candidate_details?: CandidateDetail[];
}

export interface BrainRefused {
  grounded?: false;
  refusal: BrainRefusal;
  routed_to?: string;
  route?: BrainRoute;
}

export type BrainQueryResult = BrainAnswer | DelegatedAnswer | BrainRefused;

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
