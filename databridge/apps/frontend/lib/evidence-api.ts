// Gateway client for `GET /evidence/trace/{entity_id}` (T-37). Fetch and render only — the whole
// chain (sources, ingestion route, ledger rows) is assembled server-side; the browser never
// re-derives it.
import { GATEWAY } from "./api";
import type { Refusal } from "./intake-types";
import type { SourcePointer } from "./types";

export interface EvidenceDocumentSummary {
  id: string;
  label: string;
  document_type: string | null;
  valid_from: string | null;
  valid_to: string | null;
}

/** One SourcePointer this entity is grounded in, plus where it says the pointer's document came
 *  from. `ingestion_route` is the closed vocabulary from CLAUDE.md: "upload" | "email_forward" |
 *  "walk_in" | a named connector | "config" (static app configuration, not a patient document) |
 *  "unknown" — never an assumed default. */
export interface EvidenceSource {
  pointer: SourcePointer;
  document_id: string | null;
  document: EvidenceDocumentSummary | null;
  ingestion_route: string;
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
  selected_patient_id: string | null;
  supersedes: number | null;
}

export interface EvidenceNode {
  id: string;
  kind: string;
  label: string;
  canonical_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  attrs: Record<string, unknown>;
}

export interface EvidenceEdge {
  id: string;
  kind: string;
  src_id: string;
  dst_id: string;
  valid_from: string | null;
  valid_to: string | null;
  attrs: Record<string, unknown>;
}

export interface EvidenceTrace {
  entity_id: string;
  entity_type: "node" | "edge" | "ledger_entry";
  found: true;
  node: EvidenceNode | null;
  edge: EvidenceEdge | null;
  sources: EvidenceSource[];
  ledger_entries: EvidenceLedgerEntry[];
  limitation: string;
}

async function call<T>(path: string): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, { cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  // UNKNOWN_ENTITY / BAD_ENTITY_ID are expected, normal responses — pass them through so the page
  // can render the reason instead of a stack trace, exactly like the rest of the app's clients.
  if (!res.ok && !("refusal" in body)) {
    throw new Error(`${path} → ${res.status}`);
  }
  return body as T;
}

export const evidenceApi = {
  trace: (entityId: string) =>
    call<EvidenceTrace | Refusal>(`/evidence/trace/${encodeURIComponent(entityId)}`),
};
