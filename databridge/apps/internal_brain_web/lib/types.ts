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
  channel?: string | null;
  synthetic_replay?: boolean;
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

/** GET /ib/galaxy?limit=N: the overview (the GET /ib/graph?limit=N body) and the map (the GET /ib/map
 *  body plus `complete`) from ONE live pass (galaxy.py). `map.complete` is false when any check of
 *  that pass was undecided; it is a flag only: no count of undecided items exists anywhere. */
export interface KnowledgeGalaxy {
  overview: KnowledgeGraph;
  map: KnowledgeMap & { complete: boolean };
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

// ---- analytics ----
// backend_v1.md shapes for Mission control, the request journey, analytics and the receipt. Every
// field the backend may still be changing is optional: pages render what is present and say
// "not reported" for the rest.

/** Stages timed in `answer.released.stage_ms` (stats.py STAGES; v1 adds `release`). */
export type StageV1 = 'accept' | 'gate1' | 'gate2' | 'seal' | 'compose' | 'release';
export const STAGES_V1: StageV1[] = ['accept', 'gate1', 'gate2', 'seal', 'compose', 'release'];

export type PctMs = { p50: number | null; p95: number | null; count: number };

export interface RequestsFlow {
  asked: number;
  answered: number;
  refused: number;
  failed: number;
}

export interface DocumentsFlow {
  considered: number;
  gate2_checked: number;
  gate2_allowed: number;
  gate2_denied: number;
  released: number;
  cited: number;
}

/** GET /audit/stats (v1): two flows with different units; `funnel` is gone. */
export interface AuditStatsV1 {
  window: { since: string; until: string; days: number };
  ledger: { events: number; first_seq: number | null; last_seq: number | null; head: string | null };
  totals: Partial<AuditStats['totals']> & {
    requests: number;
    answered: number;
    refused: number;
    failed: number;
    answer_rate: number | null;
    graph_views?: number;
    doc_views?: number;
    trace_views?: number;
  };
  requests_flow?: RequestsFlow;
  documents_flow?: DocumentsFlow;
  /** Legacy (pre-v1) shape, read only when the flows are absent. */
  funnel?: AuditStats['funnel'];
  gate2?: { verdicts: Record<string, number>; reasons: Record<string, number> };
  latency_ms?: { count: number; p50: number | null; p95: number | null; max: number | null; mean: number | null };
  stages_ms?: Partial<Record<StageV1, PctMs>>;
  models?: Record<string, number>;
  channels?: Record<string, number>;
  platforms?: Partial<Record<Platform | 'other', { considered?: number; released?: number; gate2_checked?: number }>>;
  people?: { sub: string; asked?: number; answered?: number; refused?: number }[];
  days?: { day: string; asked?: number; answered?: number; refused?: number; failed?: number }[];
  kinds?: Record<string, number>;
}

/** A sealed record in the compliance journey (global seq). */
export interface JourneyRecord {
  seq: number;
  kind: string;
  hash: string;
  event_time?: string | null;
  recorded_at?: string | null;
}

/** GET /audit/journey/{req} (compliance). Same shape as `Journey`, with the v1 `release` stage. */
export interface JourneyV1 extends Omit<Journey, 'stage_ms' | 'events'> {
  stage_ms: Partial<Record<StageV1, number>>;
  events: JourneyRecord[];
}

/** A cited document the asker can STILL open (re-authorised live by /ib/trace). */
export interface ReceiptCitation {
  doc_id: string;
  platform?: string | null;
  kind?: string | null;
  container?: string | null;
  container_name?: string | null;
  title?: string | null;
  version?: string | null;
  updated_at?: string | null;
  deep_link?: string | null;
  cited_version?: string | null;
}

/** GET /ib/trace/{req} (v1): the asker's public-safe receipt. No seq, no ms per stage, no denials. */
export interface TraceReceipt {
  req_id: string;
  question: string | null;
  channel: string | null;
  event_time: string | null;
  synthetic_replay?: boolean;
  status: RequestStatus;
  policy_version: string | null;
  model: ModelInfo | null;
  prompt_version: string | null;
  latency_ms_rounded: number | null;
  stages: { name: string; status: string }[];
  dropped_sentences_count: number | null;
  citations: ReceiptCitation[];
  answer_sha256: string | null;
  records: { ordinal: number; kind: string; hash: string }[];
}

/** GET /ib/status: model chain and ledger health (no secrets). */
export interface IbStatus {
  models: {
    provider: string | null;
    chain: string[];
    active: boolean;
    active_chain: string[];
    fallback: { provider: string; model: string };
  };
  key_configured: boolean;
  force_offline: boolean;
  ledger: { ok: boolean | null };
}

/** GET /ib/history (v1 adds channel + synthetic_replay). */
export interface HistoryItemV1 extends HistoryItem {
  channel?: string | null;
  synthetic_replay?: boolean;
}

/** One `request.accepted` row from GET /audit/events?kind=request.accepted (compliance). */
export interface RequestRow {
  req_id: string;
  sub: string | null;
  event_time: string | null;
  question: string | null;
  channel: string | null;
  synthetic_replay: boolean;
}

/** /eval_summary.json written by scripts/eval_summary.py (a static file, not the API). */
export interface EvalSummary {
  generated_at?: string;
  source?: string;
  git_sha?: string;
  stale?: boolean;
  note?: string;
  run_date?: string;
  full_run?: boolean;
  limit?: number;
  leaks?: number;
  leak_requests?: number;
  requests?: number;
  requests_per_mode?: number;
  upper95?: number;
  payload_requests?: number;
  payload_upper95?: number;
  violations?: number;
  differential?: { pairs?: number; mismatches?: number; control_mismatches?: number };
  injection?: { requests?: number; hard_failures?: number };
  recall?: { released?: Record<string, number>; released_all_modes?: { min?: number; max?: number } };
  cited_recall?: { per_mode?: Record<string, number>; range?: { min?: number; max?: number } };
  quality_findings?: number;
  canaries_valid?: boolean;
  runtime_s?: number;
  per_mode?: Record<string, { requests?: number; with_leak?: number; upper95?: number; with_model_payload?: number; refused?: number }>;
  tests?: { backend?: number; e2e?: number };
  smoke?: { run_date?: string; leaks?: number; requests?: number } | null;
}

// ---- audit ----
/** One sealed ledger record behind an audit row (from /audit/events: seq + hash per ledger event). */
export interface AuditRecordRef {
  seq: number;
  kind: string;
  hash: string;
}

/** An audit row (one request) that keeps its sealed records, so the console can show the record hash. */
export interface AuditRow extends AuditEvent {
  records?: AuditRecordRef[];
}

/**
 * POST /audit/tamper-demo (compliance; backend_v1.md v2). Runs the tamper scenarios on a temporary
 * COPY of the live ledger and returns one verify report per scenario. The real chain is only read (D-19).
 */
export interface TamperScenario {
  id: string;
  /** Short name, e.g. "Edit one old record". */
  title: string;
  /** What was done to the copy, in plain words. */
  what_we_did?: string;
  /** "flagged" or "not flagged". */
  expected: string;
  expected_detected: boolean;
  /** Verification flagged the copy (`verify_ok` false). */
  detected: boolean;
  /** The outcome matched the expectation. */
  ok: boolean;
  verify_ok?: boolean;
  internal_ok?: boolean | null;
  external_head_match?: boolean | null;
  records?: number | null;
  problems: string[];
  /** Only when the chain check names a record: the first record that fails. */
  first_bad_seq?: number | null;
  /** The 20 s budget ran out before this scenario started. */
  skipped?: boolean;
}

export interface TamperDemoResult {
  /** false when every byte the live files held at copy time is unchanged after the run. */
  real_log_modified: boolean;
  /** First 12 hex characters of the live head that was copied. */
  head_before?: string | null;
  records_copied?: number | null;
  external_head_copied?: boolean | null;
  elapsed_ms?: number | null;
  scenarios: TamperScenario[];
}

/** POST /audit/ask with the v2 parser fields. */
export interface AuditAskV2 {
  filter: Record<string, unknown>;
  events: AuditRow[];
  parser?: 'llm' | 'deterministic' | string;
  parser_model?: string | null;
  parser_fallback?: boolean | null;
  notes?: string | string[] | null;
}

// ---- act ----
/** POST /ib/act/draft: a reply (or message) draft built only from the cited sentences of one of
 *  the asker's own released answers. Never sent by the Brain. */
export interface ActCitation {
  tag: string;
  doc_id: string;
  platform: Platform;
  kind: string;
  container: string;
  container_name: string;
  title: string;
  version: string;
  updated_at: string;
  deep_link: string;
}
export interface ActDraft {
  action_id: string;
  kind: 'reply' | 'message';
  status: 'draft';
  label: string;
  req_id: string;
  greeting: string;
  opener: string;
  body: { text: string; tags: string[] }[];
  closing: string;
  /** The whole draft as plain text, without citation tags (what Copy puts on the clipboard). */
  text: string;
  citations: ActCitation[];
  model: ModelInfo;
  prompt_version: string;
  sent: false;
  scope_note: string;
  as_of: string;
}
/** POST /ib/act/ticket/propose: nothing is created until the person confirms. */
export interface TicketProposal {
  proposal_id: string;
  action_id: string;
  kind: 'ticket';
  status: 'proposed';
  req_id: string;
  project: { key: string; name: string };
  /** Only projects the person may create issues in (checked live at the source). */
  projects: { key: string; name: string }[];
  issue_type: string;
  summary: string;
  summary_max: number;
  description: string;
  citations: ActCitation[];
  expires_at: string;
  scope_note: string;
  as_of: string;
}
/** POST /ib/act/ticket/confirm. */
export interface TicketCreated {
  status: 'created';
  action_id: string;
  req_id: string;
  project: { key: string; name: string };
  summary: string;
  issue: { key: string; url: string; doc_id: string };
  as_of: string;
}

// ---- live-growth ----
// Demo-proof features (D-24): mounted on the server only when IB_DEMO_FEATURES=1 (never deployed).
/** GET /ib/status also says whether the demo-proof routes are mounted (declaration merging). */
export interface IbStatus {
  demo_features?: boolean;
}
/** GET /ib/graph/changes?since=<cursor>: only documents the caller can open now, in /ib/graph shapes.
 *  `count`, `degree_hidden_safe` and `more` are null (keep the numbers you already have). */
export interface GraphChanges {
  cursor: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}
/** POST /sim/add-document: a note posted AS the signed-in person (no author field, ever). */
export interface AddDocumentBody {
  platform: 'slack';
  target: string;
  text: string;
}
export interface AddDocumentResult {
  ok: boolean;
  doc_id: string;
  platform: 'slack' | 'confluence';
  container: string;
  at: string;
}
/** POST /sim/upload-file (JSON with base64 content; the proxy allows 7 MB for this route). */
export interface UploadFileBody {
  filename: string;
  content_b64: string;
  folder: string;
  title?: string;
}
/** GET /sim/upload-folders: ONLY folders the person may upload into now, sorted by path (D-34). */
export interface UploadFolder {
  id: string;
  path: string;
}
export interface UploadFileResult {
  ok: boolean;
  doc_id: string;
  container: string;
  kind: string;
  at: string;
}
/** POST /sim/engine-refresh and /sim/engine-status: the engine snapshot rebuild (no counts). */
export interface EngineRefreshStatus {
  state: 'idle' | 'running' | 'done' | 'failed';
  step: 'building' | 'publishing' | 'reloading' | null;
  started_at: string | null;
  finished_at: string | null;
  snapshot_at: string | null;
  reloaded: boolean | null;
}

// ---- deliverables (POST /ib/deliverables; IB_DELIVERABLES=1, D-25) ----
/** GET /ib/status says which deliverable kinds this server offers (declaration merging). */
export interface IbStatus {
  deliverables?: DeliverablesStatus;
}
export type DeliverableKind = 'table' | 'chart' | 'analysis';
export interface DeliverablesStatus {
  enabled: boolean;
  /** a model is configured; without one, only table and chart (copied from the sources) */
  model: boolean;
  kinds: DeliverableKind[];
  exports: { csv: boolean; pptx: boolean };
}
export interface DeliverCitation extends ActCitation {
  /** the number this source has in the answer's own citation list */
  n: number;
  cited_version: string;
  changed_since_answer: boolean;
  checked_at: string;
}
export interface DeliverCheck {
  values_proposed: number;
  values_kept: number;
  values_dropped: number;
  items_proposed: number;
  items_kept: number;
  items_dropped: number;
  names_dropped: number;
}
export interface DeliverSentence {
  text: string;
  tags: string[];
}
export interface DeliverCell {
  text: string;
  tags: string[];
  numeric: boolean;
}
export interface DeliverTable {
  source: 'model' | 'source_table' | 'cited_facts';
  from_tag?: string;
  columns: string[];
  rows: DeliverCell[][];
  truncated: boolean;
}
export interface DeliverPoint {
  x: string;
  value: number;
  /** the number exactly as written in the source */
  raw: string;
  tag: string;
}
export interface DeliverChart {
  type: 'bar' | 'line';
  drawn_as_bar: boolean;
  x_label: string;
  y_label: string;
  unit: 'percent' | 'currency' | 'days' | 'hours' | 'number';
  currency: string | null;
  series: { name: string; points: DeliverPoint[] }[];
  from_tag?: string;
}
export interface DeliverAnalysis {
  sections: { heading: 'Key facts' | 'Trends' | 'Risks' | 'What changed'; sentences: DeliverSentence[] }[];
}
export interface Deliverable {
  deliverable_id: string;
  req_id: string;
  kind: DeliverableKind;
  status: 'ready' | 'needs_model' | 'insufficient';
  label: string;
  title: string;
  generated: 'model' | 'deterministic' | 'none';
  model: ModelInfo | null;
  check: DeliverCheck;
  content: DeliverTable | DeliverChart | DeliverAnalysis | null;
  citations: DeliverCitation[];
  exports: ('csv' | 'pptx')[];
  fallback: string;
  prompt_version: string;
  scope_note: string;
  as_of: string;
}
