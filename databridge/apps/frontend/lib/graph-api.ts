// Gateway client for `/graph` — the knowledge graph itself, made visible (T-graph).
//
// Fetch and render only (D-FE-2): every count, every truncation flag, and the reverse-index
// coverage verdict are computed server-side. This file owns no logic beyond shaping the wire
// response into TypeScript and building query strings — the same contract `evidence-api.ts` and
// `intelligence-api.ts` already follow.
//
// A new module rather than an edit to a shared `lib/*.ts`: two other agents are working elsewhere
// in this frontend, and `/graph`'s gateway surface (`apps/gateway/databridge_gateway/graph_routes.py`)
// is this workstream's alone.
import { GATEWAY, authHeaders } from "./api";
import type { Refusal } from "./intake-types";
import type { SourcePointer } from "./types";

export interface GraphCounts {
  nodes: number;
  edges: number;
  sources: number;
  attribute_sources: number;
}

export interface KindFacet {
  kind: string;
  count: number;
  /** Open edges touching a node of this kind (an edge counts once at each end). Absent on the
   *  on-screen legend's locally-computed facets. */
  incident_edges?: number;
}

/** Connected components over OPEN edges, treated as undirected. `computed: false` means the graph
 *  was over the server's cap and the numbers are UNKNOWN (null), never estimated. */
export interface ComponentStats {
  computed: boolean;
  count: number | null;
  largest: number | null;
  isolated_nodes: number;
  connected_components?: number;
  reason: string | null;
}

export interface SourceFacetItem {
  source_id: string;
  pointer_count: number;
}

export interface SourceFacets {
  items: SourceFacetItem[];
  total_distinct: number;
  returned: number;
  truncated: boolean;
  left_out: number;
}

export interface ReverseIndexCoverage {
  answer: "known" | "unknown";
  reason: string;
  unindexed_pointers: number;
}

export interface GraphFacets {
  graph_source: string | null;
  counts: GraphCounts;
  node_kinds: KindFacet[];
  edge_kinds: KindFacet[];
  sources: SourceFacets;
  reverse_index_coverage: ReverseIndexCoverage;
  components: ComponentStats;
}

/** What a node's label reads as on screen. The gateway sends `null` for a label no pointer
 *  grounds (withheld, P4) — it is said so, never rendered blank or replaced by the id. */
export const LABEL_WITHHELD = "label withheld (no pointer)";

export function nodeLabelText(label: string | null | undefined): string {
  return label === null || label === undefined ? LABEL_WITHHELD : label;
}

export interface GraphNodeSummary {
  id: string;
  kind: string;
  /** null when the gateway withheld it: the label had no source pointer. Render with
   *  `nodeLabelText`, never as an empty string. */
  label: string | null;
  canonical_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  is_open: boolean;
  attr_count: number;
  unresolved_count: number;
}

export interface GraphEdgeSummary {
  id: string;
  kind: string;
  src_id: string;
  dst_id: string;
  weight: number;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  is_open: boolean;
}

export interface NeighborhoodCoverage {
  answer: "known" | "unknown";
  reason: string;
  unindexed_pointers: number;
  impact_truncated: boolean;
  impact_binding_count: number;
}

export interface GraphNeighborhood {
  graph_source: string | null;
  seed: string | null;
  depth: number | null;
  filters: { kind: string | null; source: string | null; q: string | null };
  nodes: GraphNodeSummary[];
  edges: GraphEdgeSummary[];
  returned: number;
  /** `null` means UNKNOWN — a bounded walk stopped before it could count the rest. Never treat
   *  `null` as zero; that is exactly the "known vs unknown" distinction the reverse index makes
   *  (`ptr_index.py`), applied here to a BFS that can itself run out of budget. */
  matched_total: number | null;
  left_out: number | null;
  truncated: boolean;
  window: number;
  coverage: NeighborhoodCoverage | null;
  note: string | null;
  /** `hubs` = the default view (best-connected nodes and their neighbours, isolated nodes never
   *  drawn); `filter` = kind/label browse; `source` = reverse-index browse; `seed` = BFS. */
  mode?: "hubs" | "filter" | "source" | "seed";
  /** Only in `hubs` mode: what the default view deliberately did not draw, counted. */
  omitted?: { nodes_not_drawn: number; isolated_nodes: number; hubs_seeded: number } | null;
}

export type PathReason = "no_path_within_max_depth" | "disconnected" | "search_budget_exhausted";

export interface GraphPath {
  graph_source: string | null;
  from: string;
  to: string;
  max_depth: number;
  found: boolean;
  /** Why nothing was found. `search_budget_exhausted` means UNKNOWN — not "no path". */
  reason: PathReason | null;
  explored: number;
  hops: number | null;
  /** In path order, `from` first. */
  nodes: GraphNodeSummary[];
  /** `edges[i]` joins `nodes[i]` and `nodes[i+1]` (in either direction). */
  edges: GraphEdgeSummary[];
  message: string | null;
}

export interface GraphSearchHit extends GraphNodeSummary {
  degree: number;
}

export interface GraphSearch {
  graph_source: string | null;
  q: string;
  kind: string | null;
  nodes: GraphSearchHit[];
  returned: number;
  matched_total: number;
  truncated: boolean;
  window: number;
}

export interface GraphReload {
  reloaded: boolean;
  graph_source: string | null;
  counts: GraphCounts;
}

export interface GraphVersion {
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  /** null when withheld (no pointer) — render with `nodeLabelText`. */
  label?: string | null;
  is_open: boolean;
}

export interface IncidentEdge {
  id: string;
  kind: string;
  other_id: string;
  attrs: Record<string, unknown>;
  unresolved: string[];
  valid_from: string | null;
  valid_to: string | null;
  is_open: boolean;
}

export interface GraphNodeDetail {
  graph_source: string | null;
  id: string;
  kind: string;
  /** null when the gateway withheld it: the label had no source pointer. Render with
   *  `nodeLabelText`, never as an empty string. */
  label: string | null;
  canonical_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  is_open: boolean;
  attrs: Record<string, unknown>;
  /** Per-value pointer — never pooled under one entity-level citation (CLAUDE.md §Hard
   *  constraints 2: a citation must not appear to vouch for a value it never saw). */
  attribute_sources: Record<string, SourcePointer>;
  /** Grounds the ENTITY — that this node exists and was read from somewhere. */
  sources: SourcePointer[];
  /** Per-field pointers for STRUCTURAL values (label, kind, canonical_id, temporal fields) — the
   *  additive contract the gateway is adding (FX5, graph_routes). Optional: absent today. */
  field_sources?: Record<string, SourcePointer>;
  /** Attributes asked for and not found. Never guessed, never defaulted. */
  unresolved: string[];
  versions: GraphVersion[];
  edges: {
    outgoing: IncidentEdge[];
    incoming: IncidentEdge[];
    returned: number;
    matched_total: number;
    truncated: boolean;
    window: number;
  };
  evidence_trace_url: string;
  /** Existing subject nodes this node MIGHT be the same person as (`extractor._subject_candidates`:
   *  exact identifier or exact label only). Optional: no gateway route exposes it today. Never
   *  acted on here — `requires_human_selection` is always true, and deciding "same person" is a
   *  staff determination with an attestation (CLAUDE.md hard constraint 1). */
  subject_candidates?: SubjectCandidate[];
}

export interface SubjectCandidate {
  extracted: string;
  candidate: string;
  basis: string;
  requires_human_selection: true;
}

export interface GraphEdgeDetail {
  graph_source: string | null;
  id: string;
  kind: string;
  src_id: string;
  dst_id: string;
  weight: number;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  is_open: boolean;
  attrs: Record<string, unknown>;
  attribute_sources: Record<string, SourcePointer>;
  sources: SourcePointer[];
  /** Per-field pointers for structural values (kind, endpoints, temporal fields); see the node type. */
  field_sources?: Record<string, SourcePointer>;
  unresolved: string[];
  has_attestation: boolean;
  versions: GraphVersion[];
  evidence_trace_url: string;
}

export interface NeighborhoodParams {
  node?: string;
  kind?: string;
  source?: string;
  q?: string;
  depth?: number;
  limit?: number;
  includeClosed?: boolean;
}

// ---- agent runs (read-only, for drawing an agent's traversal on the canvas) --------------------

/** `steps[].produced_by` (§5.8): the harness result of the planner turn that produced the step,
 *  or `null` for a step no model produced. */
export interface ProducedBy {
  agent?: string | null;
  agent_version?: string | null;
  task_class?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  run_id?: string | null;
  at?: string | null;
  /** `rules` · `model` · `user` · `unknown` where the producer records one. */
  by?: string | null;
}

export interface AgentRunStep {
  tool: string;
  args: Record<string, unknown>;
  result_pointers: SourcePointer[];
  policy_decision: Record<string, unknown>;
  ms: number;
  produced_by: ProducedBy | null;
}

export interface AgentRunRecord {
  run_id: string;
  trigger: Record<string, unknown>;
  started: string;
  ended: string | null;
  status: string;
  models_used: string[];
  steps: AgentRunStep[];
}

export interface AgentRunDetail {
  agent: string;
  record: AgentRunRecord;
}

/** A gateway failure that is NOT a refusal body. `status` 0 means the gateway could not be reached
 *  at all; any other status means it was reached and answered with that HTTP status. */
export class GraphApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "GraphApiError";
    this.status = status;
    this.code = code;
  }
}

/** What a graph request failure means, in words — reachability is claimed only for status 0. */
export function describeGraphError(e: unknown): string {
  if (e instanceof GraphApiError) {
    if (e.status === 0) return `Could not reach the gateway: ${e.message}`;
    if (e.status === 401) return `The gateway did not accept this terminal's credentials (401): ${e.message}`;
    if (e.status === 403) return `The signed-in principal is not allowed to read this (403): ${e.message}`;
    if (e.status === 422 || e.status === 400) return `The gateway refused the request as invalid (${e.status}): ${e.message}`;
    if (e.status >= 500) return `The gateway failed while handling this request (${e.status}): ${e.message}`;
    return `The gateway answered ${e.status}: ${e.message}`;
  }
  return `The request failed: ${e instanceof Error ? e.message : String(e)}`;
}

async function call<T>(path: string, method: "GET" | "POST" = "GET"): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, {
      method,
      cache: "no-store",
      headers: authHeaders(),
    });
  } catch (e) {
    throw new GraphApiError(0, "GATEWAY_UNREACHABLE", `${path}: ${e instanceof Error ? e.message : String(e)}`);
  }
  const body: unknown = await res.json().catch(() => ({}));
  const obj = body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  // Refusals (404/503) are expected, ordinary responses — pass them through so the caller can
  // render the reason, exactly like every other client in this app.
  if (!res.ok && !("refusal" in obj)) {
    const detail = "detail" in obj ? (typeof obj.detail === "string" ? obj.detail : JSON.stringify(obj.detail)) : "";
    throw new GraphApiError(res.status, `HTTP_${res.status}`, `${path} → ${res.status}${detail ? ` ${detail}` : ""}`);
  }
  return body as T;
}

// ---- pure decision helpers (R-019) -------------------------------------------------------------

/** Split attributes by grounding. A value with no per-attribute pointer is WITHHELD — only its
 *  field name survives, so it can never render as a fact (CLAUDE.md hard constraint 2). A null
 *  value is empty (unresolved), not withheld. */
export function splitAttributes(
  attrs: Record<string, unknown>,
  sources: Record<string, SourcePointer> | null | undefined,
): { grounded: { field: string; value: unknown; pointer: SourcePointer }[]; withheld: string[]; empty: string[] } {
  const grounded: { field: string; value: unknown; pointer: SourcePointer }[] = [];
  const withheld: string[] = [];
  const empty: string[] = [];
  for (const [field, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined) empty.push(field);
    else if (sources && sources[field]) grounded.push({ field, value, pointer: sources[field] });
    else withheld.push(field);
  }
  return { grounded, withheld, empty };
}

/** How a STRUCTURAL value (label, kind, endpoints, canonical id, temporal fields) may be shown
 *  (R-019 round 3, G4):
 *   - `cited`    — a per-field pointer grounds exactly this value;
 *   - `entity`   — no per-field pointer, but the entity itself is grounded: shown, labelled as
 *                  grounded at entity level and not individually cited;
 *   - `withheld` — nothing grounds it: the value is not rendered as a fact. */
export function structuralGrounding(
  detail: { sources?: SourcePointer[] | null; field_sources?: Record<string, SourcePointer> | null },
  field: string,
): { state: "cited" | "entity" | "withheld"; pointer: SourcePointer | null } {
  const own = detail.field_sources?.[field];
  if (own) return { state: "cited", pointer: own };
  if (Array.isArray(detail.sources) && detail.sources.length > 0) return { state: "entity", pointer: null };
  return { state: "withheld", pointer: null };
}

/** The CURRENT trail — steps[0..cursor] and the links between them — which is what "copy trail"
 *  exports; forward history past the cursor is not part of it (R-019 round 3, G6). */
export function currentTrail<S, L>(steps: S[], links: L[], cursor: number): { steps: S[]; links: L[] } {
  const end = Math.max(-1, Math.min(cursor, steps.length - 1));
  return { steps: steps.slice(0, end + 1), links: links.slice(0, Math.max(0, end)) };
}

/** One crumb of a traversal trail. */
export interface TrailStepLike {
  nodeId: string;
  /** Set only when the response that produced this step named the edge walked. */
  edgeId?: string;
}

/** The link between two consecutive crumbs. `edgeId` is set ONLY when a response named it;
 *  `adjacent` counts canvas edges between the two nodes, for display — adjacency is not a walk. */
export interface TrailLink {
  edge: GraphEdgeSummary | null;
  edgeId: string | null;
  adjacent: number;
}

export function resolveTrailLinks(steps: TrailStepLike[], edges: Map<string, GraphEdgeSummary>): TrailLink[] {
  const links: TrailLink[] = [];
  for (let i = 1; i < steps.length; i += 1) {
    const a = steps[i - 1].nodeId;
    const b = steps[i].nodeId;
    let adjacent = 0;
    for (const e of edges.values()) {
      if ((e.src_id === a && e.dst_id === b) || (e.src_id === b && e.dst_id === a)) adjacent += 1;
    }
    const named = steps[i].edgeId;
    links.push(named ? { edge: edges.get(named) ?? null, edgeId: named, adjacent } : { edge: null, edgeId: null, adjacent });
  }
  return links;
}

/** A run step's measurements, or null where the record does not carry them — never 0 by default. */
export function stepMeasures(s: { ms?: unknown; result_pointers?: unknown }): { ms: number | null; pointers: number | null } {
  return {
    ms: typeof s.ms === "number" && Number.isFinite(s.ms) ? s.ms : null,
    pointers: Array.isArray(s.result_pointers) ? s.result_pointers.length : null,
  };
}

/** The run record's steps, or a malformed-record message. A missing `steps` array is NOT an empty
 *  run: the page must not conclude "no graph calls" from a record that does not say. */
export function readAgentSteps(
  record: unknown,
): { ok: true; steps: AgentRunStep[] } | { ok: false; message: string } {
  if (!record || typeof record !== "object") return { ok: false, message: "the run record is missing or malformed" };
  const steps = (record as { steps?: unknown }).steps;
  if (!Array.isArray(steps)) return { ok: false, message: "the run record has no steps array — it is malformed, so what the agent did is unknown" };
  return { ok: true, steps: steps as AgentRunStep[] };
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

export const graphApi = {
  facets: () => call<GraphFacets | Refusal>("/graph/facets"),
  neighborhood: (params: NeighborhoodParams) =>
    call<GraphNeighborhood | Refusal>(
      `/graph/neighborhood${qs({
        node: params.node,
        kind: params.kind,
        source: params.source,
        q: params.q,
        depth: params.depth,
        limit: params.limit,
        include_closed: params.includeClosed,
      })}`,
    ),
  node: (id: string, includeClosed?: boolean) =>
    call<GraphNodeDetail | Refusal>(
      `/graph/node/${encodeURIComponent(id)}${qs({ include_closed: includeClosed })}`,
    ),
  edge: (id: string) => call<GraphEdgeDetail | Refusal>(`/graph/edge/${encodeURIComponent(id)}`),
  path: (from: string, to: string, maxDepth?: number) =>
    call<GraphPath | Refusal>(`/graph/path${qs({ from, to, max_depth: maxDepth })}`),
  search: (q: string, kind?: string, limit?: number) =>
    call<GraphSearch | Refusal>(`/graph/search${qs({ q, kind, limit })}`),
  reload: () => call<GraphReload | Refusal>("/graph/reload", "POST"),
  /** `GET /agents/{agent}/runs/{run_id}` — the run record whose graph steps are drawn. */
  agentRun: (agent: string, runId: string) =>
    call<AgentRunDetail | Refusal>(
      `/agents/${encodeURIComponent(agent)}/runs/${encodeURIComponent(runId)}`,
    ),
};
