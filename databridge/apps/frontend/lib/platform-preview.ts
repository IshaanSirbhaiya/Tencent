/**
 * The platform surfaces' data source — one seam, four pages.
 *
 * `/trace`, `/builder`, `/ontology` and `/materialise` are specified in
 * `Foundational Research/design/DEMO_AND_FRONTEND.md` and are wired to real gateway routes in
 * Phase 5. They are built NOW, against `config/platform-preview.json`, which carries the exact
 * shapes those routes will return. When the routes land, this module fetches instead of importing
 * and the pages do not change.
 *
 * Every page renders `<PreviewNotice>` so nobody mistakes a shape for a measurement.
 */
import preview from "@/config/platform-preview.json";

export type Tone = "good" | "warn" | "bad" | "neutral";

export interface TraceRow {
  label: string;
  value: string;
  tone: Tone;
}

export interface TraceBranch {
  kind: string;
  title: string;
  subtitle: string;
  rows: TraceRow[];
}

/** The participants in one request. Each is a place a message can be sent to or come from. */
export type TraceNodeKind =
  | "agent"
  | "orchestrator"
  | "adapter"
  | "graph"
  | "capture"
  | "ledger"
  | "human"
  | "answer";

/** What kind of message an edge carries. Drives the arrow's colour and its label prefix. */
export type TraceEdgeKind = "request" | "policy" | "ledger" | "rows" | "answer" | "human";

/** What was asked for. Named exactly as `CapabilityRequest` names it, so the panel and the wire
 *  format cannot drift apart. */
export interface TraceRequested {
  capability: string;
  projection: string[];
  predicate: string | null;
  purpose: string;
  principal: string;
  as_of: string | null;
}

/** What the policy layer decided. `withheld` and `refused` always carry a reason code — a decision
 *  without one is indistinguishable from a gap, which is the confusion this surface exists to end. */
export interface TraceDecided {
  decision: "allowed" | "withheld" | "refused";
  reason_code: string | null;
  note: string;
}

/** What actually came back. */
export interface TracePulled {
  rows: number;
  cells: number;
  tier: string;
  from_history: number;
  from_source: number;
  from_capture: number;
  verdicts: { verdict: string; count: number; tone: Tone }[];
}

export interface TraceDetail {
  summary: string;
  requested: TraceRequested | null;
  decided: TraceDecided | null;
  pulled: TracePulled | null;
  /** Null on either dimension means nobody measured it. A zero would be a measurement, and the
   *  recorder is explicit that it never invents one. */
  cost: { tokens: number | null; ms: number | null } | null;
  notes: string[];
}

export interface TraceNode {
  id: string;
  kind: TraceNodeKind;
  label: string;
  sublabel: string;
  /** Deterministic lane layout. Same request draws the same picture twice — no physics. */
  col: number;
  row: number;
  detail: TraceDetail;
}

export interface TraceEdge {
  id: string;
  /** 1-based position in the sequence. The timeline control replays in this order. */
  step: number;
  from: string;
  to: string;
  kind: TraceEdgeKind;
  label: string;
  detail: TraceDetail;
}

/** One field the answer asked for, in the state it ended up in.
 *
 *  `withheld` and `absent` are DIFFERENT states and are never merged: withheld means the field
 *  exists and policy did not release it (and says why); absent means no source held it, and it is
 *  named rather than guessed. */
export interface TraceCell {
  path: string;
  state: "grounded" | "withheld" | "absent";
  trust: "trusted" | "untrusted" | null;
  /** `graph_history` / `live_source` / `capture` in the preview; a live trace names the adapter's
   *  own `source_id`, so this is an open string rather than a closed union. */
  source: string | null;
  tier: string | null;
  verdict: string | null;
  reason_code: string | null;
  note: string | null;
}

export interface TracePointerProvenance {
  total: number;
  from_history: number;
  needed_source: number;
  /** Optional because the recorder does not classify a capture read separately yet. Absent is
   *  absent — it is never rendered as a zero. */
  from_capture?: number;
  withheld?: number;
  absent?: number;
}

export interface TracePreview {
  endpoint: string;
  request_id?: string;
  request: {
    agent: string;
    version: string;
    /** Null where the recorder captured no measurement. Null is "nobody measured"; 0 would be a
     *  measurement, and the recorder is explicit that it never invents one. */
    purpose: string | null;
    on_behalf_of: string | null;
    grant: string | null;
    budget_used: number | null;
    budget_total: number | null;
  };
  pointer_provenance?: TracePointerProvenance;
  nodes?: TraceNode[];
  edges?: TraceEdge[];
  cells?: TraceCell[];
  cells_note?: string;
  branches: TraceBranch[];
  results: { kind: string; count: number; detail: string; tone: Tone }[];
  answer: {
    citations: number;
    certificate_risk: number | null;
    risk_budget: number | null;
    admitted: boolean | null;
    /** The recorder's own words on why a field is null. Rendered rather than paraphrased. */
    note?: string | null;
  };
  /** Present on a live trace: is this record complete, and if not, which steps failed to record. */
  integrity?: { complete: boolean; defect_count: number; defect_codes: string[] };
  defects?: Record<string, unknown>[];
}

/**
 * **No longer rendered.** `/builder` compiles for real against `POST /builder/agent/compile`
 * (`databridge_agentdef`) and `POST /builder/adapter/compile` (`load_mcp_declaration`), so the
 * illustrative refusal in `config/platform-preview.json` is dead data kept only so the payload
 * shape stays readable beside the others. Do not wire it back: on this surface the whole claim is
 * that a refusal is COMPUTED, and a hand-written one is a screenshot of the claim.
 */
export interface BuilderPreview {
  endpoint: string;
  declaration: string;
  verdict: string;
  summary: string;
  findings: { code: string; line: string; because: string }[];
  compiles_to: { label: string; value: string }[];
}

/** What a kind touches, and therefore what deciding on it costs.
 *
 *  `determination` and `identity-claim` are the two that make a proposal escalate no matter what
 *  the auto-approve switch says: they are the shapes an identity decision is written on, and that
 *  decision belongs to a named human, in person (CLAUDE.md hard constraint 1). */
export type OntologyTouch = "determination" | "identity-claim" | "subject";

export interface OntologyNodeKind {
  kind: string;
  /** The domain-free meta-kind the engine reasons about. The engine never reads `kind`. */
  binds_to: string;
  description: string | null;
  subject: boolean;
  source: string;
  locked: boolean;
  locked_reason: string | null;
  touches: OntologyTouch[];
}

export interface OntologyEdgeKind {
  kind: string;
  binds_to: string;
  from: string[];
  to: string[];
  description: string | null;
  source: string;
  locked: boolean;
  locked_reason: string | null;
  touches: OntologyTouch[];
}

/** A kind an adapter agent asked for and the library does not have. */
export interface OntologyProposal {
  id: string;
  target: "node" | "edge";
  kind: string;
  binds_to: string;
  from: string[];
  to: string[];
  proposed_by: string;
  proposed_at: string;
  rationale: string;
  status: "pending" | "escalated" | "approved" | "rejected";
  /** True when this proposal escalates REGARDLESS of the auto-approve setting. */
  escalates: boolean;
  escalation_reason: string | null;
  auto_approvable: boolean;
  auto_approvable_because: string | null;
  /** Which guardrail classes the server matched. Present on a live payload only. */
  touches?: OntologyTouch[];
}

export interface OntologyPack {
  pack: string;
  title: string;
  version: string;
  subject_kinds: string[];
  node_kinds: OntologyNodeKind[];
  edge_kinds: OntologyEdgeKind[];
  proposals: OntologyProposal[];
}

export interface OntologyPreview {
  endpoint: string;
  mutation_endpoints: Record<string, string>;
  auto_approve: boolean;
  escalation_policy: {
    always_escalates_meta_kinds: string[];
    always_escalates_when_touching_subject: boolean;
    note: string;
  };
  by_pack: Record<string, OntologyPack>;
}

export interface MaterialisePreview {
  endpoint: string;
  shapes: string[];
  selected_shape: string;
  plan: { label: string; value: string; note?: string }[];
  receipt: {
    plan_hash: string;
    rows: number;
    pointers: number;
    withheld: number;
    verdicts: { verdict: string; count: number; tone: Tone }[];
    outputs: string[];
  };
}

export interface EvidencePreview {
  endpoint: string;
  verdicts: { verdict: string; tone: Tone; meaning: string }[];
}

export interface PlatformPreview {
  trace: TracePreview;
  builder: BuilderPreview;
  ontology: OntologyPreview;
  materialise: MaterialisePreview;
  evidence: EvidencePreview;
}

export const PLATFORM_PREVIEW = preview as unknown as PlatformPreview;

/** Tailwind text colour per tone. One place, so five surfaces agree on what red means. */
export const TONE_TEXT: Record<Tone, string> = {
  good: "text-band-auto",
  warn: "text-band-spot",
  bad: "text-band-review",
  neutral: "text-fg",
};

export const TONE_BORDER: Record<Tone, string> = {
  good: "border-band-auto/40",
  warn: "border-band-spot/40",
  bad: "border-band-review/40",
  neutral: "border-border",
};
