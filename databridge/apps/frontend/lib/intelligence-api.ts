// `/intelligence` gateway client — the Business Intelligence surface (PHASE_7 T-42).
//
// Read-only, and shaped so the page CANNOT render a number without its denominator: a figure is a
// `Count` with `n`, `of` and the `members` it was counted over, and there is no type here that
// carries a bare total. That is the constraint expressed in the type system rather than in a
// comment — the JSX has nothing to hardcode because the shape gives it nothing to hardcode.
//
// Nothing in this file computes. Every figure is computed server-side by
// `databridge_graph.analytics.compute` (a traversal) or by the confirm gate itself; the browser
// only renders and links.
import { GATEWAY } from "./api";
import type { Refusal, SourcePointer } from "./types";

/** A count and the population it was counted over, plus the ids behind it. Never one without
 *  the others — `members` is what lets a reader walk from the number to the traversal. */
export interface Count {
  label: string;
  n: number;
  of: number;
  pct: number;
  members: string[];
}

export interface Entity {
  id: string;
  kind: string;
  label: string;
}

export interface IssuerRow {
  issuer_id: string;
  issuer: string;
  documents: number;
  members: string[];
}

export interface GraphAnalytics {
  documents: number;
  patients: number;
  packages: number;
  tests_in_catalogue: number;
  nodes: number;
  edges: number;
  issuer_mix: IssuerRow[];
  ambiguity: Count | null;
  unlinked_documents: Count | null;
  conflicts: Count | null;
  tests_outside_any_package: Entity[];
  as_of: string | null;
  entities: Record<string, Entity>;
}

/** An intake the counter cannot commit yet, and what it is waiting on.
 *
 *  `candidate_count` is how MANY registry rows the document matched. There is deliberately no
 *  field naming or ordering them: a screen that ranked candidates would be doing identity
 *  verification with extra steps (CLAUDE.md §Hard constraints 1). */
export interface BlockedIntake {
  intake_id: string;
  document_id: string;
  document_type: string;
  candidate_count: number;
  coverage_status: string;
  eligibility_flags: string[];
  blocked_because: string;
  identity_verified: boolean;
}

export interface QueueSummary {
  intakes: number;
  committed: number;
  blocked: BlockedIntake[];
  blocked_count: number;
}

export interface IntelligenceResponse {
  as_of: string | null;
  graph: GraphAnalytics | null;
  /** Why the graph figures are missing. Fail-open: a failed aggregate is a stated reason, never
   *  a silent zero — a zero is a claim, and an outage is not entitled to make one. */
  graph_unavailable: string | null;
  queue: QueueSummary | null;
  queue_unavailable: string | null;
  note: string;
}

export interface SubgraphNode {
  id: string;
  kind: string;
  label: string;
  canonical_id: string | null;
  valid_from: string | null;
  n_sources: number;
}

export interface SubgraphEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  valid_from: string | null;
  n_sources: number;
}

export interface SubgraphResponse {
  root: string;
  depth: number;
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
  /** node id → the SourcePointers grounding it, shown raw. */
  sources: Record<string, Record<string, unknown>[]>;
  refusal?: Refusal;
}

/** One value the agent reported, already RENDERED by the backend.
 *
 *  `text`, not `value`: what this page prints is what the backend's renderer produced. An
 *  instruction-shaped value arrives as a fixed redaction line with `redacted: true` and its
 *  citation intact — so a withheld line still walks to its evidence, and must be styled as
 *  deliberate rather than as a hole. */
export interface AnswerValue {
  key: string;
  tool: string;
  row: number;
  field: string;
  scope: string;
  text: string;
  redacted: boolean;
  citation: string;
  source: SourcePointer;
}

/** A code-authored sentence, addressed by key. Both halves come from a closed table in the
 *  backend (`databridge_agent.render.NOTES`) — never from a model. */
export interface AgentNote {
  key: string;
  text: string;
}

/** One tool call, reduced to what is safe to publish: which read, and how it went. */
export interface AgentRead {
  tool: string;
  status: string;
}

/** The ONLY shape `POST /intelligence/ask` returns.
 *
 *  Note what is absent, and deliberately so: the model's plan, its reasons, its arguments, the
 *  raw text of any value, and any exception message. The backend projects its audit record onto
 *  this type precisely so a page cannot render what the renderer withheld. */
export interface AgentAsk {
  question: string;
  /** The rendered report, as one block of text. This page renders `values` instead and keeps
   *  this for copy-out — the answer is a list with citations, not a paragraph. */
  answer: string;
  refused: boolean;
  refusal_code: string;
  requires_human_selection: boolean;
  values: AnswerValue[];
  unresolved: string[];
  notes: AgentNote[];
  reads: AgentRead[];
  model_calls: number;
  /** Present only when the surface could not run at all — no graph, no model route, nothing
   *  grounded. Distinct from `refused`, which is the agent's own answer to the question. */
  refusal?: Refusal;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => ({}));
  // Refusals ride in the body with a 4xx (house convention) — surface them, do not throw.
  if (!res.ok && !("refusal" in body)) throw new Error(`${path} → ${res.status}`);
  return body as T;
}

export const intelligenceApi = {
  summary: (asOf?: string | null) =>
    call<IntelligenceResponse>(
      `/intelligence${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`,
    ),
  subgraph: (entityId: string, depth = 1) =>
    call<SubgraphResponse>(
      `/intelligence/subgraph/${encodeURIComponent(entityId)}?depth=${depth}`,
    ),
  ask: (question: string, asOf?: string | null) =>
    call<AgentAsk>("/intelligence/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, as_of: asOf || null }),
    }),
};

/** The graph entity a value's pointer names, or null.
 *
 *  Every pointer the agent's tools emit is `kind: "derived"` with a graph entity id in
 *  `source_id` — which is what makes a citation walkable to `/intelligence/subgraph/{id}`. Any
 *  other shape is not linked rather than linked hopefully: a citation that 404s is worse than a
 *  citation rendered as plain text. */
export function citedEntity(value: AnswerValue): string | null {
  const id = value.source?.source_id;
  return value.source?.kind === "derived" && typeof id === "string" && id.includes(":")
    ? id
    : null;
}

/** The label the graph carries for an id, or the id itself. Never a guess, never a prettified
 *  reconstruction — if the backend did not send a label, the raw id is the honest thing to show. */
export function labelOf(graph: GraphAnalytics | null, id: string): string {
  return graph?.entities?.[id]?.label ?? id;
}

export function kindOf(graph: GraphAnalytics | null, id: string): string | null {
  return graph?.entities?.[id]?.kind ?? null;
}
