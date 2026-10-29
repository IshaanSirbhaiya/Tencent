// Shapes mirror databridge/apps/internal_brain/databridge_ib/models.py and app.py (CONTRACTS.md §3).
// The frontend renders these verbatim; it never computes or infers answer content.

export type Platform = 'slack' | 'confluence' | 'jira' | 'drive';

export interface Citation {
  tag: string; // "C1"
  doc_id: string;
  platform: Platform;
  container_name: string;
  title: string;
  deep_link: string;
  version: string;
  updated_at: string;
  content_checked_at: string;
  authz_checked_at: string;
  live_read?: boolean;
  stale?: boolean;
}

export interface ModelInfo {
  provider: string; // "tencent-tokenhub" | "deterministic"
  model: string; // "hy3" | "deepseek-v4.1-flash" | "extractive-v1"
  fallback_used?: boolean;
}

export interface AskResponse {
  request_id: string;
  answer: string;
  citations: Citation[];
  scope_note: string;
  model: ModelInfo;
  as_of: string;
}

export interface WhoAmI {
  sub: string;
  name: string;
  roles: string[];
  employment?: string;
}

export interface SourceStatus {
  platform: Platform | string;
  last_sync_at: string | null;
  last_event_at?: string | null;
  lag_s?: number | null;
  slo_s: number;
  state: string | null;
  within_slo?: boolean | null;
}

export interface HistoryItem {
  req_id: string;
  question: string;
  event_time: string;
}

export interface Gate2Decision {
  doc_id: string;
  verdict: string;
  reason_code: string;
}

export interface ConsideredMeta {
  doc_id: string;
  platform?: string;
  container?: string;
  title?: string;
}

/** One audit row = one request, joined across its ledger events (request/retrieval/answer). */
export interface AuditEvent {
  req_id: string;
  event_time: string;
  sub: string;
  channel?: string;
  question?: string;
  kind?: string;
  considered?: string[];
  considered_meta?: ConsideredMeta[];
  gate2?: Gate2Decision[];
  released?: string[];
  model?: ModelInfo | null;
  refused?: boolean | null;
  synthetic_replay?: boolean;
  latency_ms?: number | null;
}

export type AuditFilter = Partial<
  Record<'user' | 'container' | 'doc' | 'since' | 'until' | 'decision', string>
>;

export interface AuditAskResponse {
  filter: Record<string, unknown>;
  events: AuditEvent[];
}

export interface VerifyResult {
  ok: boolean;
  records: number;
  head: string;
  external_head_match: boolean | null;
  problems: string[];
}

export interface RevealResult {
  req_id?: string;
  answer: string;
}

export interface SimResult {
  ok: boolean;
  action: string;
  at: string;
}

export const ALLOW_VERDICTS = new Set(['verified', 'drift_material']);

/** GET /ib/map: everything the signed-in person may open, grouped platform → container → item.
 *  Restricted items are never listed or counted, so nothing here can hint that more exists. */
export interface MapItem {
  doc_id: string;
  title: string;
  kind: 'page' | 'issue' | 'thread' | 'file' | string;
  version: string;
  updated_at: string;
  deep_link: string;
}

export interface MapContainer {
  container: string;
  name: string;
  count: number;
  items: MapItem[];
}

export interface MapPlatform {
  platform: Platform;
  count: number;
  containers: MapContainer[];
}

export interface KnowledgeMap {
  as_of: string;
  scope_note: string;
  sub: string;
  totals: { items: number; containers: number; platforms: number };
  platforms: MapPlatform[];
  freshness_note: string;
}

// ---------------------------------------------------------------------------------------------
// Finals endpoints (docs/finals/contracts/ui_foundation.md §7). Shapes mirror the Python handlers:
//   /ib/trace/{req}      app.py `trace`                 (own requests only; no considered set, no Gate-2 reasons)
//   /audit/journey/{req} audit/query.py `reconstruct`   (compliance; never the answer text)
//   /audit/stats?days=   audit/stats.py `compute_stats` (compliance; counts and timings only)
//   /ib/graph, /ib/doc   graph.py / docview.py: PROVISIONAL until docs/finals/contracts/backend_v1.md lands
// ---------------------------------------------------------------------------------------------

/** Pipeline stages timed in `answer.released.stage_ms` (stats.py STAGES). */
export type Stage = 'accept' | 'gate1' | 'gate2' | 'seal' | 'compose';
export const STAGES: Stage[] = ['accept', 'gate1', 'gate2', 'seal', 'compose'];

export type RequestStatus = 'in_progress' | 'released' | 'refused' | 'failed';

/** A sealed ledger record of one request (hash-chained). */
export interface LedgerRecordRef {
  seq: number;
  kind: string;
  hash: string;
  event_time?: string | null;
  recorded_at: string | null;
}

/** Citation as sealed in answer.released (+ title joined by the projection). */
export interface SealedCitation {
  doc_id: string;
  version?: string;
  content_checked_at?: string;
  authz_checked_at?: string;
  title?: string | null;
  [k: string]: unknown;
}

export interface ReleasedDoc {
  doc_id: string;
  title?: string | null;
  container?: string | null;
}

/** GET /ib/trace/{req_id}: how one of MY answers was made. */
export interface Trace {
  req_id: string;
  question: string | null;
  channel: string | null;
  event_time: string | null;
  status: RequestStatus;
  policy_version: string | null;
  model: ModelInfo | null;
  prompt_version: string | null;
  latency_ms: number | null;
  stage_ms: Partial<Record<Stage, number>>;
  dropped_sentences_count: number | null;
  released: ReleasedDoc[];
  citations: SealedCitation[];
  answer_sha256: string | null;
  events: LedgerRecordRef[];
}

/** One considered document as the projection stores it (journey only). */
export interface ConsideredDoc {
  doc_id: string;
  platform?: string | null;
  container?: string | null;
  container_name?: string | null;
  title?: string | null;
  verdict?: string | null;
  reason_code?: string | null;
  decision?: string | null;
  considered?: boolean;
  released?: boolean;
  cited?: boolean;
}

/** GET /audit/journey/{req_id}: one request end to end (compliance_officer). */
export interface Journey {
  req_id: string;
  sub: string | null;
  channel: string | null;
  question: string | null;
  question_sha256: string | null;
  event_time: string | null;
  recorded_at: string | null;
  synthetic_replay: boolean;
  policy_version: string | null;
  directory_epoch: number | null;
  gate1_policy_version?: string | null;
  time_window: unknown;
  considered: ConsideredDoc[];
  gate2: Gate2Decision[];
  released: ReleasedDoc[];
  citations: SealedCitation[];
  model: ModelInfo | null;
  prompt_version: string | null;
  answer_ref: string | null;
  answer_sha256: string | null;
  refused: boolean | null;
  latency_ms: number | null;
  dropped_sentences_count: number | null;
  stage_ms: Partial<Record<Stage, number>>;
  failure: { stage: string | null; error_code: string | null } | null;
  status: RequestStatus;
  reveals: { seq: number; by: string | null; reason: string | null; event_time: string | null; recorded_at: string | null }[];
  events: LedgerRecordRef[];
}

type Pct = { p50: number | null; p95: number | null; count: number };

/** GET /audit/stats?days=: aggregates computed on the server over the verified ledger. */
export interface AuditStats {
  window: { since: string; until: string; days: number };
  ledger: { events: number; first_seq: number | null; last_seq: number | null; head: string | null };
  totals: {
    requests: number;
    answered: number;
    refused: number;
    failed: number;
    answer_rate: number | null;
    citations: number;
    dropped_sentences: number;
    fallback_used: number;
    reveals: number;
    audit_queries: number;
    access_changes: number;
    syncs: number;
    map_views: number;
    logins: number;
  };
  funnel: {
    asked: number;
    considered_docs: number;
    gate2_checked: number;
    gate2_allowed: number;
    gate2_denied: number;
    released_docs: number;
    cited: number;
    answered: number;
  };
  gate2: { verdicts: Record<string, number>; reasons: Record<string, number> };
  latency_ms: { count: number; p50: number | null; p95: number | null; max: number | null; mean: number | null };
  stages_ms: Record<Stage, Pct>;
  /** "provider/model" -> count */
  models: Record<string, number>;
  channels: Record<string, number>;
  platforms: Partial<Record<Platform | 'other', { considered?: number; released?: number; gate2_checked?: number }>>;
  people: { sub: string; asked?: number; answered?: number; refused?: number }[];
  days: { day: string; asked?: number; answered?: number; refused?: number; failed?: number }[];
  kinds: Record<string, number>;
}

/** GET /ib/graph (PROVISIONAL). Only documents confirmed live in this call; edges only between shown nodes. */
export type GraphNodeKind = 'doc' | 'container' | 'platform' | 'person';
export type GraphEdgeKind = 'contains' | 'authored' | 'mentions' | 'links';

export interface GraphNode {
  /** "confluence:cf-1042" | "container:slack:C0DBMIG" | "platform:jira" | "person:jdoe" */
  id: string;
  kind: GraphNodeKind;
  platform: Platform | null;
  label: string;
  /** Container node id, for docs. */
  container: string | null;
  container_name?: string | null;
  doc_kind?: string;
  version?: string;
  updated_at?: string;
  deep_link?: string;
  /** Confirmed neighbours left out of this response; null when not exactly known. */
  degree_hidden_safe: number | null;
  /** true = at least one confirmed neighbour left out; null = unknown. */
  more: boolean | null;
  /** Docs under a container/person, containers under a platform; null when not exact. */
  count?: number | null;
  /** Search rank (search mode). */
  rank?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: GraphEdgeKind;
  weight?: number;
}

export interface KnowledgeGraph {
  as_of: string;
  scope_note: string;
  sub: string;
  mode: 'overview' | 'search' | 'focus';
  focus: string | null;
  query: string | null;
  limit: number;
  truncated: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  freshness_note: string;
}

export interface GraphQuery {
  focus?: string;
  q?: string;
  limit?: number;
}

/** GET /ib/doc/{doc_id} (PROVISIONAL). The same 404 for restricted and unknown. */
export interface DocView {
  doc_id: string;
  platform: Platform;
  kind: string;
  container: string;
  container_name: string;
  title: string;
  version: string;
  updated_at: string;
  deep_link: string;
  created_at: string | null;
  author: { id: string; name: string } | null;
  excerpt: string;
  excerpt_kind: 'best_chunk' | 'start' | string;
  excerpt_truncated: boolean;
  links: { doc_id: string; platform: Platform; title: string; container_name: string; kind: string; relation: 'mentions' | 'links' }[];
  lineage: { connector: string; last_sync_at: string | null; content_checked_at: string; authz_checked_at: string; live_read: boolean };
  scope_note: string;
  as_of: string;
}
