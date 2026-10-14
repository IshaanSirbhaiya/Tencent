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
