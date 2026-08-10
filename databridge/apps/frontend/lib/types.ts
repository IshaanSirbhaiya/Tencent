// TypeScript mirror of the gateway Pydantic response models (D-FE-7). These are the wire
// shapes; the frontend renders them, never computes from them (D-FE-2).

export interface SourcePointer {
  source_id: string;
  // one of the closed SourcePointer kinds (databridge_core.provenance): table_cell | doc_span |
  // transcript_span | code_node | derived | query_cell | workbook_cell | ocr_bbox | email_part |
  // generated_claim
  kind: string;
  table?: string | null;
  column?: string | null;
  row_id?: string | null;
  derivation?: string | null; // for kind="derived" — the SQL/rule (SQL-as-provenance)
  predicate?: string | null;
  row_count?: number | null;
  rows_digest?: string | null;
  snapshot?: Record<string, unknown> | null;
  // doc_span
  document_id?: string | null;
  char_start?: number | null;
  char_end?: number | null;
  // workbook_cell (C16)
  workbook_sha?: string | null;
  sheet?: string | null;
  cell?: string | null;
  cell_range?: string | null;
  // ocr_bbox
  media_id?: string | null;
  page?: number | null;
  ocr_conf?: number | null;
  // email_part
  message_id?: string | null;
  part?: string | null;
  [k: string]: unknown;
}

export interface ColumnAnnotation {
  column: string;
  semantic_type: string;
  description: string;
  is_pii: boolean;
  pii_kind: string;
  ml_role: string;
  confidence: number;
  confidence_sources: Record<string, unknown>;
  provenance: string; // "deterministic" | "llm" | "llm+vote" | "human"
}

export interface TableAnnotation {
  table: string;
  concept: string;
  role: string;
  columns: ColumnAnnotation[];
  summary: string;
}

export interface RelationshipAnnotation {
  src_table: string;
  dst_table: string;
  via: string;
  name: string;
  cardinality: string;
  declared: boolean;
  confidence: number;
}

export interface AnnotatedSchema {
  source_id: string;
  version: number;
  tables: TableAnnotation[];
  relationships: RelationshipAnnotation[];
  label_candidates: unknown[];
  domain: string;
  created_at?: string | null;
  confirmed_by?: string | null;
}

export interface QueueCounts {
  review: number;
  spot_check: number;
  auto: number;
}

export interface Refusal {
  code: string;
  message: string;
  nearest_alternative?: string | null;
}

export interface Decision {
  table: string;
  column: string;
  accepted_type: string;
}

export interface AskResponse {
  sql: string | null;
  result: Record<string, unknown>[];
  provenance: SourcePointer | null;
  linked_tables: string[];
  refusal: Refusal | null;
  replayed: boolean;
}

export interface QuarantineView {
  quarantined: number;
  by_reason: Record<string, number>;
  rate: number;
  over_threshold: boolean;
  records: { reason_code: string; detail: string; payload: Record<string, unknown> }[];
}

export interface ConvertResponse {
  result_b64: string;
  quarantine: QuarantineView;
  artifact_id: string;
}

export interface ArtifactSummary {
  artifact_id: string;
  direction: string;
  transform_language: string;
  created_at: string | null;
  checksum: string;
}

export interface E1Result {
  suite: string;
  metric: string;
  value: number;
  role_accuracy: number;
  correct: number;
  total: number;
  per_provenance: Record<string, { accuracy: number | null; n: number }>;
  per_table: Record<string, { accuracy: number | null; n: number }>;
  reliability: Record<string, { accuracy: number | null; n: number }>;
  model_map: Record<string, string>;
  mistakes: { column: string; gold: string; predicted: string; provenance: string; confidence: number }[];
}

/** band from a confidence float — the LAST-RESORT fallback only. Prefer the server band via
 *  bandOfColumn(); the gate is always server-side. Thresholds mirror the backend confidence_band(). */
export function bandOf(confidence: number): "auto" | "spot_check" | "review" {
  return confidence >= 0.95 ? "auto" : confidence >= 0.8 ? "spot_check" : "review";
}

/** The SERVER-authoritative band for a column: the gateway stamps `confidence_sources.band`
 *  (computed by databridge_semantic.confidence_band) so the UI renders the backend's band and
 *  never drifts from it (review L1/L7/L8). Falls back to the client threshold only if absent. */
export function bandOfColumn(col: ColumnAnnotation): "auto" | "spot_check" | "review" {
  const b = col.confidence_sources?.band;
  if (b === "auto" || b === "spot_check" || b === "review") return b;
  return bandOf(col.confidence);
}
