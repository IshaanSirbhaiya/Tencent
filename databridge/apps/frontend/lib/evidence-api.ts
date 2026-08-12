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
  /** Per VALUE, never pooled — a citation must not appear to vouch for values it never saw. */
  attribute_sources: Record<string, SourcePointer>;
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
  unresolved: string[];
  /** True only when a committed ledger row stands behind this edge. See `EvidenceClaim`. */
  established: boolean;
  /** The ledger row ids that established it. */
  established_by: number[];
}

/**
 * A `for_patient` edge with NO committed ledger row behind it — what the DOCUMENT claims, not
 * what the clinic knows (R-26). The gateway hands these back in their own list and never as an
 * `EvidenceEdge`, so nothing here can be rendered as an established relationship by accident.
 * Identity is checked in person by staff; this system never determines it (CLAUDE.md hard
 * constraint 1).
 */
export interface EvidenceClaim extends EvidenceEdge {
  established: false;
  /** The graph's own wording for how many candidates this claim is one of. */
  candidate_phrase: string;
  document_id?: string | null;
  patient_id?: string | null;
}

/** One edge incident to the traced node, as the gateway classified it. */
export interface EvidenceIncidentEdge extends EvidenceEdge {
  direction: "outgoing" | "incoming";
  other_id: string;
  other_label: string | null;
  sources: SourcePointer[];
  candidate_phrase: string | null;
}

/** Why this entity's evidence was releasable at all (R-28) — never absent from a 200. */
export interface EvidenceAuthorisation {
  basis: "committed_ledger_row" | "static_configuration";
  committed_rows: number;
  why: string;
}

export interface EvidenceTrace {
  entity_id: string;
  entity_type: "node" | "edge" | "identity_claim" | "ledger_entry";
  found: true;
  node: EvidenceNode | null;
  edge: EvidenceEdge | null;
  claim: EvidenceClaim | null;
  /** Established relationships only. A `for_patient` edge reaches this list only once a human
   *  selected that patient for this document and confirmed. */
  relationships: EvidenceIncidentEdge[];
  /** Unsettled identity claims — shown, never hidden, never asserted. */
  identity_claims: EvidenceIncidentEdge[];
  /** Present exactly when there is an unsettled claim on this page. Rendered from the response
   *  rather than copied into this file, so `/evidence` and the graph's own evidence sheet cannot
   *  describe the same fact differently. */
  identity_caveat: string | null;
  authorisation: EvidenceAuthorisation;
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
