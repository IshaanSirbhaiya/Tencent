// Mock engine for NEXT_PUBLIC_IB_MOCK=1: the /api/walnut proxy answers from this fixture instead
// of the gateway, so /dev/brain renders without :8300. SERVER-SIDE ONLY (imported by the route).
//
// The fixture (mock-fixture.json) was captured from the live gateway on 30 Oct 2026: the 2-hop
// neighbourhood of knowledge_page:cf-1001 (24 nodes, 31 links), each node's detail, timeline and
// sheet (sheets cut to their first ~2,400 characters), five provenance traces, the ontology and the
// pack vocabulary. The only edits are trimming and dropping local file paths. Nothing is invented:
// an id that is not in the fixture gets the gateway's own UNKNOWN_ENTITY refusal shape.
import fixture from './mock-fixture.json';

type Json = Record<string, unknown>;
interface MockNode {
  id: string;
  kind: string;
  label: string | null;
  [k: string]: unknown;
}
interface MockEdge {
  id: string;
  src_id: string;
  dst_id: string;
  [k: string]: unknown;
}

const F = fixture as unknown as {
  facets: Json;
  ontology: Json;
  ontologyPacks: Json;
  packs: Json;
  neighborhood: Json & { nodes: MockNode[]; edges: MockEdge[] };
  nodes: Record<string, Json>;
  edges: Record<string, Json>;
  timelines: Record<string, Json>;
  sheets: Record<string, Json>;
  traces: Record<string, Json>;
  brain: Json;
};

export interface MockResult {
  status: number;
  body: unknown;
}

const ok = (body: unknown): MockResult => ({ status: 200, body });
const unknown = (what: string): MockResult => ({
  status: 404,
  body: { refusal: { code: 'UNKNOWN_ENTITY', message: `no open node or edge '${what}' in the mock fixture`, nearest_alternative: null } },
});

function neighborhood(seed: string | null, limit: number): MockResult {
  const all = F.neighborhood;
  if (!seed) return ok({ ...all, mode: 'hubs', seed: null, depth: null, matched_total: all.nodes.length, left_out: 0, truncated: false, omitted: null });
  if (!all.nodes.some((n) => n.id === seed)) return unknown(seed);
  const near = new Set<string>([seed]);
  for (const e of all.edges) {
    if (e.src_id === seed) near.add(e.dst_id);
    if (e.dst_id === seed) near.add(e.src_id);
  }
  const nodes = all.nodes.filter((n) => near.has(n.id)).slice(0, limit);
  const kept = new Set(nodes.map((n) => n.id));
  const edges = all.edges.filter((e) => kept.has(e.src_id) && kept.has(e.dst_id));
  return ok({
    ...all,
    mode: 'seed',
    seed,
    depth: 1,
    nodes,
    edges,
    returned: nodes.length,
    matched_total: nodes.length,
    left_out: 0,
    truncated: false,
    edges_returned: edges.length,
    window: limit,
    omitted: null,
  });
}

/** `path` is the already-validated gateway path (segments decoded), `search` the validated query. */
export function engineMock(path: string, search: URLSearchParams): MockResult {
  const seg = path.split('/');
  if (path === 'graph/facets') return ok(F.facets);
  if (path === 'packs') return ok(F.packs);
  if (path === 'ontology') return ok(F.ontology);
  if (path === 'ontology/packs') return ok(F.ontologyPacks);
  if (path === 'brain/graph') return ok(F.brain);
  if (path === 'graph/neighborhood') return neighborhood(search.get('node'), Number(search.get('limit') ?? 60));
  if (path === 'graph/search') {
    const q = (search.get('q') ?? '').trim().toLowerCase();
    if (!q) return { status: 422, body: { refusal: { code: 'SEARCH_NEEDS_TEXT', message: "'q' must not be empty" } } };
    const hits = F.neighborhood.nodes.filter((n) => `${n.label ?? ''} ${n.id}`.toLowerCase().includes(q));
    const limit = Number(search.get('limit') ?? 8);
    return ok({ q, kind: null, nodes: hits.slice(0, limit).map((n) => ({ ...n, degree: 0 })), returned: Math.min(hits.length, limit), matched_total: hits.length, truncated: hits.length > limit, window: limit });
  }
  if (seg[0] === 'graph' && seg[1] === 'node') return F.nodes[seg[2]] ? ok(F.nodes[seg[2]]) : unknown(seg[2]);
  if (seg[0] === 'graph' && seg[1] === 'edge') return F.edges[seg[2]] ? ok(F.edges[seg[2]]) : unknown(seg[2]);
  if (seg[0] === 'brain' && seg[1] === 'timeline') return F.timelines[seg[2]] ? ok(F.timelines[seg[2]]) : unknown(seg[2]);
  if (seg[0] === 'brain' && seg[1] === 'node' && seg[3] === 'sheet') {
    const s = F.sheets[seg[2]];
    return s ? ok({ ...s, as_of: search.get('as_of') }) : unknown(seg[2]);
  }
  if (seg[0] === 'brain' && seg[1] === 'node') return F.nodes[seg[2]] ? ok({ node: F.nodes[seg[2]] }) : unknown(seg[2]);
  if (seg[0] === 'evidence' && seg[1] === 'trace') return F.traces[seg[2]] ? ok(F.traces[seg[2]]) : unknown(seg[2]);
  return { status: 404, body: { detail: 'not found' } };
}
