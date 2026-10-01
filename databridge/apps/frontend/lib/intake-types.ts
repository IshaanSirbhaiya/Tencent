// TypeScript mirror of the gateway /intake/* response shapes (pipeline.summary_card).
// Wire shapes only — the frontend renders them and never computes from them. Every number and
// every flag on the summary card is decided server-side, so the browser cannot disagree with the
// ledger about what was authorised, who pays, or whether identity has been verified.
import type { SourcePointer } from "./types";

/** A registry row a document CLAIMS to be about. Never a determination.
 *
 *  `score` and `matched_on` exist so staff can see WHY a row is on screen — a name-only match at
 *  0.15 must not look like an NRIC match at 0.97. `conflicts` is the other half: a candidate whose
 *  date of birth disagrees with the document is still shown, flagged, never silently dropped. */
export interface IntakeCandidate {
  patient_id: string;
  name: string;
  id_number: string;
  date_of_birth: string;
  score: number;
  matched_on: string[];
  conflicts: string[];
}

export interface IntakeIdentity {
  /** Always true. There is no server response in which this is false. */
  requires_human_selection: true;
  /** Always false. The system never verifies identity; staff do, in person. */
  verified: false;
  status:
    | "single_candidate"
    | "multiple_candidates"
    | "no_candidate"
    | "insufficient_identifiers";
  notes: string[];
  candidates: IntakeCandidate[];
}

export interface AuthorisedTest {
  as_printed: string;
  canonical: string | null;
  in_package: boolean;
  /** Currency-neutral price; its currency is `billing.currency`. Optional: older gateways omit it. */
  list_price?: string | null;
  /** @deprecated currency baked into the name; read `list_price`. */
  list_price_sgd: string | null;
  source_field: string | null;
}

/** Something the GRAPH knows that this document does not say.
 *
 *  Kept separate from `reasoning` because the provenance differs in kind: `reasoning` traces this
 *  document against the config table, whereas a finding is the product of a traversal over other
 *  documents. Presenting them together would misattribute the evidence. */
export interface HistoryFinding {
  kind: string;
  message: string;
  document_id: string;
  patient_id: string;
  sources: SourcePointer[];
}

export interface IntakeCoverage {
  status: "determined" | "partial" | "undetermined";
  issuer_code: string | null;
  package_code: string | null;
  package_name: string | null;
  matched_rule_id: string | null;
  questionnaire: string;
  eligibility_flags: string[];
  unmatched_tests: string[];
  /** The rule-firing trace. Rendered verbatim: it is what makes the determination arguable. */
  reasoning: string[];
  /** Graph-derived findings, each carrying the pointers of the traversal behind it. */
  history_findings: HistoryFinding[];
  authorised_tests: AuthorisedTest[];
}

export interface BillingDraft {
  billed_to: string;
  covered_items: Record<string, unknown>[];
  patient_payable_items: Record<string, unknown>[];
  /** ISO 4217 code the pack's rules table declares (SWEEP #15). null = none declared: show the
   *  bare number, never a guessed unit. Optional because a gateway older than HC3 omits it. */
  currency?: string | null;
  /** The estimated total, currency-neutral. Read with `currency`. */
  estimated_patient_payable?: string | null;
  // What DID price, when the total could not be completed. Named separately so it can never be
  // rendered as the amount owed.
  known_subtotal?: string | null;
  /** @deprecated currency baked into the name; read `estimated_patient_payable` + `currency`. */
  estimated_patient_payable_sgd?: string | null;
  /** @deprecated currency baked into the name; read `known_subtotal` + `currency`. */
  known_subtotal_sgd?: string | null;
  tpa_claim_required: boolean;
  caveats: string[];
  /** Literal "draft". There is no other value. */
  status: "draft";
}

export interface PrefilledField {
  field_id: string;
  label: string;
  status: "prefilled" | "patient_must_answer" | "not_applicable";
  value: unknown;
  requires_confirmation: boolean;
}

export interface IntakeQuestionnaire {
  kind: string;
  prefilled: number;
  patient_must_answer: number;
  fields: PrefilledField[];
}

export interface ConfirmationView {
  identity_checked_in_person_by: string | null;
  identity_checked_at: string | null;
  selected_patient_id: string | null;
  committed: boolean;
  ready_to_commit: boolean;
  /** Why the gate is closed, in words the counter can act on. */
  blocked_because: string;
}

/** How an uploaded FILE became text. Present only on `/intake/upload-file` responses.
 *
 *  Kept beside the card rather than folded into it because it describes the *reading*, not the
 *  document: which engine ran, which pages needed OCR, and — the part that matters — what could
 *  not be recovered. A scan whose checkbox marks were lost is not a footnote, it is the reason
 *  `authorisation.package_code` is in `unresolved`, and a silent OCR result that looks identical
 *  to typed text is exactly the failure mode this field exists to prevent. */
export interface ExtractionReport {
  engine: string;
  media_type: string;
  page_count: number;
  /** 1-based page numbers that went through recognition. Empty for text and text-layer PDFs. */
  ocr_pages: number[];
  /** How many recognised regions back the pointers — each `ocr_bbox` comes from one of these. */
  regions: number;
  warnings: string[];
  loss_report: unknown[];
  /** The field paths whose value describes a SELECTION on a checkbox form, when the gateway sends
   *  them (the server's `extract.CHECKBOX_SENSITIVE`). Sent since HC3; optional because an older
   *  gateway omits it, and then the report lists every unresolved field (the conservative notice). */
  selection_paths?: string[];
}

export interface SummaryCard {
  intake_id: string;
  document: {
    document_id: string;
    document_type: string;
    confidence: number;
    channel: string;
  };
  identity: IntakeIdentity;
  coverage: IntakeCoverage;
  billing: BillingDraft;
  questionnaire: IntakeQuestionnaire;
  /** Dotted paths the document does not carry — shown as "not on document", not hidden. */
  unresolved: string[];
  /** Values the grounding wall dropped, with reasons. A caught fabrication is worth showing. */
  refused: string[];
  confirmation?: ConfirmationView;
  /** Only on a file upload. Absent for a paste — there was no extraction to report. */
  extraction?: ExtractionReport;
  /** How the values were read (HC1, SWEEP #1). `true`: no extraction model is configured, so the
   *  values were RECALLED from the bundled offline sample for a known fixture — not read from this
   *  document. `false`: a live model read it. `null`/absent: the gateway did not record it. The
   *  card must say which, visibly; see `ReadSourceBanner`. */
  offline_gold?: boolean | null;
  extraction_backend?: ExtractionBackend;
}

export type ExtractionBackend = "live" | "offline_gold" | "unrecorded";

export interface QueueRow {
  intake_id: string;
  document_id: string;
  document_type: string;
  identity_status: string;
  candidate_count: number;
  coverage_status: string;
  eligibility_flags: string[];
  committed: boolean;
  identity_verified: boolean;
  /** As on `SummaryCard`: `true` means the row's values are an offline sample, not a live read. */
  offline_gold?: boolean | null;
}

export interface EvidenceView {
  field_path: string;
  value: unknown;
  confidence: number;
  pointer: SourcePointer;
  /** The exact characters the value was read from. Null for non-document pointers. */
  source_text: string | null;
}

export interface LedgerRow {
  rowid: number;
  field_path: string;
  value: string;
  confirmed_by: string;
  confirmed_at: string;
  selected_patient_id: string | null;
  supersedes: number | null;
  pointer: SourcePointer;
}

export interface Refusal {
  refusal: { code: string; message: string; nearest_alternative: string | null };
}

export function isRefusal(x: unknown): x is Refusal {
  return typeof x === "object" && x !== null && "refusal" in x;
}
