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
import type { Refusal } from "./types";

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

async function call<T>(path: string): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, { cache: "no-store" });
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
};

/** The label the graph carries for an id, or the id itself. Never a guess, never a prettified
 *  reconstruction — if the backend did not send a label, the raw id is the honest thing to show. */
export function labelOf(graph: GraphAnalytics | null, id: string): string {
  return graph?.entities?.[id]?.label ?? id;
}

export function kindOf(graph: GraphAnalytics | null, id: string): string | null {
  return graph?.entities?.[id]?.kind ?? null;
}
