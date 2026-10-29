// Client for the Walnut engine view (/dev/brain). The browser talks only to the same-origin
// read-only proxy `app/api/walnut/[...path]/route.ts`, which holds the gateway token server-side,
// checks the Internal Brain session and allows a closed list of GET paths. This file only shapes
// wire responses into types and builds query strings: every count, flag and pointer comes from the
// gateway (the same contract as the original frontend's lib/graph-api.ts, lib/brain-api.ts and
// lib/evidence-api.ts, whose shapes these mirror).

/** The persona the engine graph was built for (scripts/build_enterprise_graph.py, default --as jdoe).
 *  The proxy enforces it (WALNUT_GRAPH_PERSONA); the page uses it only to show the lock card early. */
export const ENGINE_PERSONA = 'jdoe';
export const ENGINE_PERSONA_NAME = 'Jordan';
export const DEFAULT_PROVENANCE_ENTITY = 'knowledge_page:cf-1001';
export const ENGINE_START_COMMAND = 'bash databridge/scripts/run_walnut_original.sh start';

/** A SourcePointer as the engine serialises it. Only the fields this view reads are named. */
export interface SourcePointer {
  source_id: string;
  kind: string;
  trust?: string;
  table?: string | null;
  column?: string | null;
  row_id?: string | null;
  page?: number | null;
  line?: number | null;
  char_start?: number | null;
  char_end?: number | null;
  basis?: string;
  grounds?: string[];
  [k: string]: unknown;
}

export interface Refusal {
  code: string;
  message: string;
  nearest_alternative?: string | null;
}

// ---------------------------------------------------------------- /graph/*

export interface KindFacet {
  kind: string;
  count: number;
  incident_edges?: number;
}

export interface GraphFacets {
  counts: { nodes: number; edges: number; sources: number; attribute_sources: number };
  node_kinds: KindFacet[];
  edge_kinds: KindFacet[];
  sources: { items: { source_id: string; pointer_count: number }[]; total_distinct: number };
  components: { computed: boolean; count: number | null; largest: number | null; isolated_nodes: number };
}

export interface GraphNodeSummary {
  id: string;
  kind: string;
  /** null = withheld by the gateway (no pointer grounds the label). */
  label: string | null;
  canonical_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  system_to: string | null;
  is_open: boolean;
  attr_count: number;
  unresolved_count: number;
  degree?: number;
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

export interface GraphNeighborhood {
  seed: string | null;
  depth: number | null;
  nodes: GraphNodeSummary[];
  edges: GraphEdgeSummary[];
  returned: number;
  matched_total: number | null;
  left_out: number | null;
  truncated: boolean;
  edges_returned?: number;
  window: number;
  note: string | null;
  mode?: 'hubs' | 'filter' | 'source' | 'seed';
  omitted?: { nodes_not_drawn: number; isolated_nodes: number; hubs_seeded: number } | null;
}

export interface GraphSearch {
  q: string;
  nodes: (GraphNodeSummary & { degree: number })[];
  returned: number;
  matched_total: number;
  truncated: boolean;
}

export interface IncidentEdge {
  id: string;
  kind: string;
  other_id: string;
  valid_from: string | null;
  valid_to: string | null;
  is_open: boolean;
}

export interface GraphNodeDetail {
  id: string;
  kind: string;
  label: string | null;
  canonical_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  is_open: boolean;
  attrs: Record<string, unknown>;
  attribute_sources: Record<string, SourcePointer>;
  sources: SourcePointer[];
  sources_total?: number;
  unresolved: string[];
  edges: { outgoing: IncidentEdge[]; incoming: IncidentEdge[]; returned: number; matched_total: number; truncated: boolean };
}

export interface GraphEdgeDetail {
  id: string;
  kind: string;
  src_id: string;
  dst_id: string;
  weight: number;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  is_open: boolean;
  attrs: Record<string, unknown>;
  attribute_sources: Record<string, SourcePointer>;
  sources: SourcePointer[];
  sources_total?: number;
  unresolved: string[];
  has_attestation: boolean;
}

// ---------------------------------------------------------------- /brain/*

export interface BrainNode {
  id: string;
  kind: string;
  label: string;
  canonical_id: string | null;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  n_sources: number;
}

export interface BrainEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  valid_from: string | null;
  valid_to: string | null;
  n_sources: number;
}

export interface BrainGraph {
  nodes: BrainNode[];
  edges: BrainEdge[];
  stats: {
    nodes: number;
    edges: number;
    sources: number;
    provenance_pct: number;
    shown_nodes?: number;
    shown_edges?: number;
    truncated?: boolean;
    node_limit?: number;
  };
}

export interface TimelineEvent {
  kind: 'node' | 'edge';
  label: string;
  valid_from: string | null;
  valid_to: string | null;
  system_from: string | null;
  pointers?: SourcePointer[];
}

export interface Timeline {
  entity?: string;
  events: TimelineEvent[];
}

export interface NodeSheet {
  entity_id: string;
  as_of: string | null;
  markdown: string;
}

// ---------------------------------------------------------------- /evidence/trace

export interface EvidenceVerify {
  status: string;
  detail?: string;
}

export interface EvidencePointerEvidence {
  pointer: SourcePointer;
  table_cell: { source: string; table: string | null; column: string | null; row: string | null } | null;
  capture: { capture_digest: string; source?: { title?: string; uri?: string }; snippet?: { before: string; match: string; after: string } | null } | null;
  verify: EvidenceVerify;
}

export interface EvidenceAttribute extends EvidencePointerEvidence {
  value: unknown;
}

export interface EvidenceSource extends EvidencePointerEvidence {
  ingestion_route: string;
}

export interface EvidenceRelationship {
  id: string;
  kind: string;
  direction: 'outgoing' | 'incoming';
  other_id: string;
  other_kind: string | null;
  other_label: string | null;
  valid_from: string | null;
  established: boolean;
  sources: SourcePointer[];
}

export interface EvidenceTrace {
  entity_id: string;
  entity_type: 'node' | 'edge' | 'identity_claim' | 'ledger_entry';
  found: true;
  node: {
    id: string;
    kind: string;
    meta_kind: string | null;
    label: string;
    canonical_id: string | null;
    valid_from: string | null;
    system_from: string | null;
    attrs: Record<string, unknown>;
    attribute_evidence: Record<string, EvidenceAttribute>;
    unresolved: string[];
  } | null;
  edge: {
    id: string;
    kind: string;
    src_id: string;
    dst_id: string;
    valid_from: string | null;
    attrs: Record<string, unknown>;
    attribute_evidence?: Record<string, EvidenceAttribute>;
    established: boolean;
  } | null;
  relationships: EvidenceRelationship[];
  authorisation: { basis: string; committed_rows: number; why: string };
  sources: EvidenceSource[];
  limitation: string;
}

// ---------------------------------------------------------------- /ontology, /packs

export interface OntologyNodeKind {
  kind: string;
  binds_to: string;
  description: string;
  source: string;
  deprecated: boolean;
  count: number;
}

export interface OntologyEdgeKind {
  kind: string;
  binds_to: string;
  from: string[];
  to: string[];
  description: string;
  deprecated: boolean;
  count: number;
}

export interface Ontology {
  pack: string;
  title: string;
  version: string;
  node_kinds: OntologyNodeKind[];
  edge_kinds: OntologyEdgeKind[];
}

export interface OntologyPacks {
  packs: string[];
  enabled: string[];
  default: string;
}

export interface PackVocabulary {
  node_kinds?: Record<string, { color?: string; label?: string }>;
  edge_labels?: Record<string, string>;
}

export interface PackRegistry {
  default_pack: string;
  enabled_packs: string[];
  packs: { id: string; graph_vocabulary?: PackVocabulary | null }[];
}

// ---------------------------------------------------------------- errors and fetch

/** Why the engine view cannot show something. The page renders each one as its own state. */
export type EngineFailure =
  | 'off' // the proxy is not enabled on this server (404 on every path)
  | 'locked' // signed in as someone the snapshot was not built for (403)
  | 'signed-out' // no Internal Brain session (401)
  | 'down' // the gateway is not running or failed (503 / 502)
  | 'refused' // the gateway answered with a typed refusal (e.g. UNKNOWN_ENTITY)
  | 'bad'; // anything else

export class EngineError extends Error {
  status: number;
  failure: EngineFailure;
  refusal: Refusal | null;
  constructor(status: number, failure: EngineFailure, message: string, refusal: Refusal | null = null) {
    super(message);
    this.name = 'EngineError';
    this.status = status;
    this.failure = failure;
    this.refusal = refusal;
  }
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

const id = (s: string) => encodeURIComponent(s);

async function call<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/walnut/${path}`, { cache: 'no-store', credentials: 'same-origin', signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new EngineError(0, 'down', 'The engine proxy could not be reached.');
  }
  const body: unknown = await res.json().catch(() => null);
  const obj = body && typeof body === 'object' ? (body as Record<string, unknown>) : {};
  const refusal = obj.refusal && typeof obj.refusal === 'object' ? (obj.refusal as Refusal) : null;
  if (refusal) throw new EngineError(res.status, 'refused', refusal.message, refusal);
  if (res.ok) return body as T;
  if (res.status === 401) throw new EngineError(401, 'signed-out', 'Sign in to see the engine view.');
  if (res.status === 403) throw new EngineError(403, 'locked', 'This snapshot is not available to this persona.');
  if (res.status === 404) throw new EngineError(404, 'off', 'The engine view is not enabled on this server.');
  if (res.status >= 500) throw new EngineError(res.status, 'down', 'The engine is not running or did not answer.');
  throw new EngineError(res.status, 'bad', `The engine proxy answered ${res.status}.`);
}

export const engineApi = {
  facets: (s?: AbortSignal) => call<GraphFacets>('graph/facets', s),
  packs: (s?: AbortSignal) => call<PackRegistry>('packs', s),
  hubs: (limit = 60, s?: AbortSignal) => call<GraphNeighborhood>(`graph/neighborhood${qs({ limit })}`, s),
  neighborhood: (node: string, depth = 1, limit = 80, s?: AbortSignal) =>
    call<GraphNeighborhood>(`graph/neighborhood${qs({ node, depth, limit })}`, s),
  search: (q: string, limit = 8, s?: AbortSignal) => call<GraphSearch>(`graph/search${qs({ q, limit })}`, s),
  node: (nodeId: string, s?: AbortSignal) => call<GraphNodeDetail>(`graph/node/${id(nodeId)}`, s),
  edge: (edgeId: string, s?: AbortSignal) => call<GraphEdgeDetail>(`graph/edge/${id(edgeId)}`, s),
  brainGraph: (limit = 150, s?: AbortSignal) => call<BrainGraph>(`brain/graph${qs({ limit })}`, s),
  timeline: (entity: string, s?: AbortSignal) => call<Timeline>(`brain/timeline/${id(entity)}`, s),
  sheet: (entity: string, asOf?: string | null, s?: AbortSignal) =>
    call<NodeSheet>(`brain/node/${id(entity)}/sheet${qs({ as_of: asOf ?? undefined })}`, s),
  trace: (entity: string, s?: AbortSignal) => call<EvidenceTrace>(`evidence/trace/${id(entity)}`, s),
  ontology: (s?: AbortSignal) => call<Ontology>('ontology', s),
  ontologyPacks: (s?: AbortSignal) => call<OntologyPacks>('ontology/packs', s),
};

// ---------------------------------------------------------------- display helpers (no derivation)

/** The gateway double-encodes a few punctuation marks in its prose fields ("â€”" for "—").
 *  This repairs only those byte sequences so the text reads as the gateway meant it. */
export function fixText(s: string | null | undefined): string {
  if (!s) return '';
  return s
    .replace(/â€”/g, '—')
    .replace(/â€“/g, '–')
    .replace(/â€™/g, '’')
    .replace(/â€œ/g, '“')
    .replace(/â€\u009d/g, '”')
    .replace(/â†’/g, '→');
}

export const LABEL_WITHHELD = 'label withheld (no pointer)';

export function labelText(label: string | null | undefined): string {
  return label === null || label === undefined ? LABEL_WITHHELD : label;
}

export function humanizeKind(kind: string): string {
  return kind
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ');
}

/** "confluence.pages[cf-1001].version": a pointer written the way the engine's sheets write it. */
export function pointerPath(p: SourcePointer): string {
  if (p.table) return `${p.table}${p.row_id ? `[${p.row_id}]` : ''}${p.column ? `.${p.column}` : ''}`;
  const parts: string[] = [];
  if (p.page !== undefined && p.page !== null) parts.push(`page ${p.page}`);
  if (p.line !== undefined && p.line !== null) parts.push(`line ${p.line}`);
  if (p.char_start !== undefined && p.char_start !== null) parts.push(`chars ${p.char_start}–${p.char_end ?? '?'}`);
  return parts.join(', ') || p.kind;
}

export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-SG', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Singapore', timeZoneName: 'short' } : { timeZone: 'Asia/Singapore' }),
  });
}

export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

export const isMockEngine = process.env.NEXT_PUBLIC_IB_MOCK === '1';
