// Gateway client for `/evidence/*`. Fetch and render only — the whole chain (pointers, captured
// snippets, re-verification, ingestion runs, ledger rows) is assembled server-side; the browser
// never re-derives it, and never decides what may be released.
import { GATEWAY, authHeaders } from "./api";
import type { Refusal } from "./intake-types";
import type { SourcePointer } from "./types";

export interface EvidenceDocumentSummary {
  id: string;
  kind: string;
  label: string;
  valid_from: string | null;
  valid_to: string | null;
}

/** What the capture store says about a pointer's source: the cited characters with a little
 *  context, the source's own meta, and a re-verification against the captured bytes. */
export interface EvidenceSnippet {
  before: string;
  /** Exactly the cited span — the part to highlight, and nothing else. */
  match: string;
  after: string;
  char_start: number;
  char_end: number;
  truncated_before: boolean;
  truncated_after: boolean;
}

export interface EvidenceCaptureSource {
  source?: string;
  uri?: string;
  mime?: string;
  title?: string;
  retrieved_at?: string;
}

/** `verified` · `hash_mismatch` · `value_not_in_span` · `capture_missing` · `span_out_of_range` ·
 *  `no_text` · `no_bytes` · `no_capture_binding`. Rendered as-is; never collapsed into pass/fail. */
export interface EvidenceVerify {
  status: string;
  detail?: string;
  hash_matches?: boolean | null;
  value_in_span?: boolean | null;
  recomputed_digest?: string | null;
}

export interface EvidenceCapture {
  capture_digest: string;
  source: EvidenceCaptureSource;
  snippet: EvidenceSnippet | null;
  verify: EvidenceVerify;
}

export interface EvidenceTableCell {
  source: string;
  table: string | null;
  column: string | null;
  row: string | null;
}

/** One pointer and what the disk says about it. */
export interface EvidencePointerEvidence {
  pointer: SourcePointer;
  capture: EvidenceCapture | null;
  table_cell: EvidenceTableCell | null;
  verify: EvidenceVerify;
}

/** One SourcePointer grounding the entity itself. `ingestion_route` is closed vocabulary:
 *  "upload" | "email_forward" | "walk_in" (an intake channel) | "ingest" (captured by an ingestion
 *  run) | "table" (a structured-source cell) | "unknown" — never an assumed default. */
export interface EvidenceSource extends EvidencePointerEvidence {
  document_id: string | null;
  document: EvidenceDocumentSummary | null;
  ingestion_route: string;
  ingest_source: string | null;
  intake_id: string | null;
}

export interface EvidenceLedgerEntry {
  rowid: number;
  intake_id: string;
  field_path: string;
  value: unknown;
  pointer: SourcePointer;
  extracted_at: string;
  confirmed_by: string;
  confirmed_at: string;
  /** The subject row a human selected in person (wire name kept for the ledger's schema). */
  selected_patient_id: string | null;
  supersedes: number | null;
}

export interface EvidenceAttribute extends EvidencePointerEvidence {
  value: unknown;
}

export interface EvidenceNode {
  id: string;
  kind: string;
  meta_kind: string | null;
  label: string;
  canonical_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  attrs: Record<string, unknown>;
  /** Per VALUE, never pooled — a citation must not appear to vouch for values it never saw. */
  attribute_sources: Record<string, SourcePointer>;
  /** Per value: its pointer, its captured snippet and a re-verify of THAT value. */
  attribute_evidence: Record<string, EvidenceAttribute>;
  /** Asked for and not found in any source. Named, never guessed or defaulted. */
  unresolved: string[];
}

export interface EvidenceEdge {
  id: string;
  kind: string;
  src_id: string;
  dst_id: string;
  valid_from: string | null;
  valid_to: string | null;
  attrs: Record<string, unknown>;
  attribute_sources: Record<string, SourcePointer>;
  attribute_evidence?: Record<string, EvidenceAttribute>;
  unresolved: string[];
  /** True only when nothing about it is an unsettled identity claim. See `EvidenceClaim`. */
  established: boolean;
  established_by: number[];
}

/**
 * A claim edge (the `about` meta-kind) with NO committed ledger row behind it — what the DOCUMENT
 * claims, not what anyone knows (R-26). Handed back in its own list and never as an
 * `EvidenceEdge`, so nothing here can be rendered as an established relationship by accident.
 */
export interface EvidenceClaim extends EvidenceEdge {
  established: false;
  candidate_phrase: string;
  document_id?: string | null;
  /** The claimed SUBJECT's key (wire name kept for the contract). */
  patient_id?: string | null;
}

export interface EvidenceIncidentEdge extends EvidenceEdge {
  direction: "outgoing" | "incoming";
  other_id: string;
  other_kind: string | null;
  /** Null when the neighbour carries a subject's details this page may not release. */
  other_label: string | null;
  sources: SourcePointer[];
  candidate_phrase: string | null;
}

/** Why this entity's evidence was releasable at all — never absent from a 200. */
export interface EvidenceAuthorisation {
  basis: "pointer_provenance" | "committed_ledger_row";
  committed_rows: number;
  why: string;
  policy?: { subject_kinds: string[]; claim_capable_kinds: string[]; claim_edge_kinds?: string[] };
}

export interface EvidenceExclusionReason {
  reason: string;
  n: number;
}

export interface EvidenceIngestion {
  runs: { source: string; run_id: string }[];
  sources: {
    source: string;
    latest_run: string;
    excluded: number;
    exclusion_reasons: EvidenceExclusionReason[];
    counts: Record<string, number>;
  }[];
  note: string | null;
}

export interface EvidenceTrace {
  entity_id: string;
  entity_type: "node" | "edge" | "identity_claim" | "ledger_entry";
  found: true;
  node: EvidenceNode | null;
  edge: EvidenceEdge | null;
  claim: EvidenceClaim | null;
  relationships: EvidenceIncidentEdge[];
  identity_claims: EvidenceIncidentEdge[];
  /** Present exactly when there is an unsettled claim on this page. */
  identity_caveat: string | null;
  authorisation: EvidenceAuthorisation;
  sources: EvidenceSource[];
  ledger_entries: EvidenceLedgerEntry[];
  ingestion: EvidenceIngestion | null;
  limitation: string;
}

export interface EvidenceRecent {
  entities: { id: string; kind: string; label: string; system_from: string | null }[];
  total: number;
  /** How many of the newest entities were examined; `withheld` is counted over these. */
  examined: number;
  withheld: number;
  withheld_reason: string | null;
}

export interface EvidenceRunSummary {
  source: string;
  run_id: string;
  ledger_files: string[];
  counts: Record<string, number>;
  excluded: number;
  exclusion_reasons: EvidenceExclusionReason[];
  report: Record<string, unknown>;
  truncated: boolean;
}

export interface EvidenceSources {
  sources: { source: string; runs: number; run_ids: string[]; latest: EvidenceRunSummary }[];
  note: string | null;
}

async function call<T>(path: string): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, { cache: "no-store", headers: authHeaders() });
  const body = await res.json().catch(() => ({}));
  // Refusals (UNKNOWN_ENTITY, AWAITING_HUMAN_CONFIRMATION, …) are normal responses — pass them
  // through so the page renders the reason instead of a stack trace.
  if (!res.ok && !("refusal" in body)) {
    throw new Error(`${path} → ${res.status}`);
  }
  return body as T;
}

export const evidenceApi = {
  trace: (entityId: string) =>
    call<EvidenceTrace | Refusal>(`/evidence/trace/${encodeURIComponent(entityId)}`),
  recent: (limit = 25) => call<EvidenceRecent | Refusal>(`/evidence/recent?limit=${limit}`),
  sources: () => call<EvidenceSources | Refusal>("/evidence/sources"),
};
