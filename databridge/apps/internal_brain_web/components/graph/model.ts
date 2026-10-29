// The explorer's data model: a graphology graph built ONLY from what the API returned.
//
// Galaxy = GET /ib/graph (overview: platforms, places with exact counts, people and who-writes-where)
//        + GET /ib/map   (the documents, each listed under its place).
// Nothing is inferred: a document → place edge exists because /ib/map listed the item under that
// place; every other edge is an edge from /ib/graph. Responses from search and one-hop focus are
// merged in later (mergeResponse), each new node placed next to the node it grew from.
import Graph from 'graphology';
import type { GraphEdgeKind, GraphNode, GraphNodeKind, KnowledgeGraph, KnowledgeMap, Platform } from '@/lib/types';

export interface NodeAttrs {
  label: string;
  kind: GraphNodeKind;
  platform: Platform | null;
  container: string | null;
  containerName: string | null;
  docKind: string | null;
  version: string | null;
  updatedAt: string | null;
  deepLink: string | null;
  /** Exact backend count (docs under a place/person, places under a platform); null = not exact. */
  count: number | null;
  /** Set when this node was expanded: what the focus response said about it. */
  expanded: { hidden: number | null; more: boolean | null; limit: number; truncated: boolean } | null;
  /** Search rank from the last server search (search mode only). */
  rank: number | null;
  x: number;
  y: number;
  size: number;
  /** ms timestamp when merged in (drives the short fade); 0 for the initial galaxy. */
  born: number;
}

export interface EdgeAttrs {
  kind: GraphEdgeKind;
  weight: number;
  born: number;
}

export type KGraph = Graph<NodeAttrs, EdgeAttrs>;

export interface LayoutInputNode {
  id: string;
  kind: GraphNodeKind;
  platform: Platform | null;
  container: string | null;
}
export interface LayoutInputEdge {
  s: string;
  t: string;
  w: number;
}

const blank = (n: Partial<NodeAttrs> & Pick<NodeAttrs, 'label' | 'kind'>): NodeAttrs => ({
  platform: null,
  container: null,
  containerName: null,
  docKind: null,
  version: null,
  updatedAt: null,
  deepLink: null,
  count: null,
  expanded: null,
  rank: null,
  x: 0,
  y: 0,
  size: 1,
  born: 0,
  ...n,
});

export function attrsOf(n: GraphNode): NodeAttrs {
  return blank({
    label: n.label,
    kind: n.kind,
    platform: n.platform,
    container: n.container ?? null,
    containerName: n.container_name ?? null,
    docKind: n.doc_kind ?? null,
    version: n.version ?? null,
    updatedAt: n.updated_at ?? null,
    deepLink: n.deep_link ?? null,
    count: n.count ?? null,
    rank: n.rank ?? null,
  });
}

export const edgeKey = (s: string, t: string, kind: string) => `${s}|${t}|${kind}`;

/** Assemble the galaxy from the overview and the map (positions are filled in by the layout). */
export function buildGalaxy(overview: KnowledgeGraph, map: KnowledgeMap | null): KGraph {
  const g: KGraph = new Graph<NodeAttrs, EdgeAttrs>({ type: 'directed', multi: false, allowSelfLoops: false });
  for (const n of overview.nodes) if (!g.hasNode(n.id)) g.addNode(n.id, attrsOf(n));
  if (map) {
    for (const p of map.platforms) {
      const plid = `platform:${p.platform}`;
      if (!g.hasNode(plid)) g.addNode(plid, blank({ label: p.platform, kind: 'platform', platform: p.platform }));
      for (const c of p.containers) {
        const cid = `container:${p.platform}:${c.container}`;
        if (!g.hasNode(cid)) g.addNode(cid, blank({ label: c.name, kind: 'container', platform: p.platform, count: c.count }));
        const k = edgeKey(plid, cid, 'contains');
        if (!g.hasEdge(k)) g.addEdgeWithKey(k, plid, cid, { kind: 'contains', weight: 1, born: 0 });
        for (const it of c.items) {
          if (!g.hasNode(it.doc_id))
            g.addNode(
              it.doc_id,
              blank({
                label: it.title,
                kind: 'doc',
                platform: p.platform,
                container: cid,
                containerName: c.name,
                docKind: it.kind,
                version: it.version,
                updatedAt: it.updated_at,
                deepLink: it.deep_link,
              }),
            );
          const e = edgeKey(cid, it.doc_id, 'contains');
          if (!g.hasEdge(e)) g.addEdgeWithKey(e, cid, it.doc_id, { kind: 'contains', weight: 1, born: 0 });
        }
      }
    }
  }
  for (const e of overview.edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    const k = edgeKey(e.source, e.target, e.kind);
    if (!g.hasEdge(k)) g.addEdgeWithKey(k, e.source, e.target, { kind: e.kind, weight: e.weight ?? 1, born: 0 });
  }
  resize(g);
  return g;
}

export function layoutInput(g: KGraph): { nodes: LayoutInputNode[]; edges: LayoutInputEdge[] } {
  const nodes: LayoutInputNode[] = [];
  g.forEachNode((id, a) => nodes.push({ id, kind: a.kind, platform: a.platform, container: a.container }));
  const edges: LayoutInputEdge[] = [];
  g.forEachEdge((_k, a, s, t) => edges.push({ s, t, w: a.kind === 'authored' ? Math.min(3, 0.4 + Math.log2(1 + a.weight) * 0.4) : a.kind === 'contains' ? 1 : 0.6 }));
  return { nodes, edges };
}

/** Size by degree, per kind (platform hubs > places > people > documents). */
export function resize(g: KGraph) {
  g.forEachNode((id, a) => {
    const d = g.degree(id);
    let size: number;
    if (a.kind === 'platform') size = 13;
    else if (a.kind === 'container') size = 4.5 + 0.95 * Math.sqrt(Math.max(d, a.count ?? 0));
    else if (a.kind === 'person') size = 3 + 0.7 * Math.sqrt(d);
    else size = 1.7 + 0.55 * Math.sqrt(d);
    g.setNodeAttribute(id, 'size', Math.round(size * 100) / 100);
  });
}

/** Stable hash of the dataset, so a layout is computed once per dataset and cached. */
export function datasetHash(input: { nodes: LayoutInputNode[]; edges: LayoutInputEdge[] }): string {
  let h1 = 0x811c9dc5;
  let h2 = 0x9e3779b9;
  const feed = (s: string) => {
    for (let i = 0; i < s.length; i++) {
      h1 = Math.imul(h1 ^ s.charCodeAt(i), 16777619);
      h2 = Math.imul(h2 ^ s.charCodeAt(i), 2246822507);
    }
  };
  for (const n of [...input.nodes].sort((a, b) => (a.id < b.id ? -1 : 1))) feed(`${n.id};${n.kind};${n.container ?? ''}\n`);
  for (const e of [...input.edges].sort((a, b) => (a.s + a.t < b.s + b.t ? -1 : 1))) feed(`${e.s}>${e.t}:${e.w}\n`);
  return `${(h1 >>> 0).toString(36)}${(h2 >>> 0).toString(36)}-${input.nodes.length}-${input.edges.length}`;
}

/**
 * Merge a search or focus response. Existing nodes keep their position (the mental map never jumps);
 * new nodes are placed on a small golden-angle ring around their anchor and fade in.
 * Returns the ids of the nodes in the response (new and existing).
 */
export function mergeResponse(g: KGraph, resp: KnowledgeGraph, anchorId: string | null, now: number): { ids: string[]; added: string[] } {
  const ids: string[] = [];
  const added: string[] = [];
  const unit = spacing(g);
  const ringCount = new Map<string, number>();
  const anchorOf = (n: GraphNode): string | null => {
    if (n.container && g.hasNode(n.container)) return n.container;
    if (anchorId && g.hasNode(anchorId)) return anchorId;
    const nb = resp.edges.find((e) => (e.source === n.id && g.hasNode(e.target)) || (e.target === n.id && g.hasNode(e.source)));
    return nb ? (nb.source === n.id ? nb.target : nb.source) : null;
  };
  // Containers before documents, so a new document can anchor on a container from the same response.
  const ordered = [...resp.nodes].sort((a, b) => rankKind(a.kind) - rankKind(b.kind));
  for (const n of ordered) {
    ids.push(n.id);
    if (g.hasNode(n.id)) {
      const cur = g.getNodeAttributes(n.id);
      g.mergeNodeAttributes(n.id, {
        label: n.label || cur.label,
        version: n.version ?? cur.version,
        updatedAt: n.updated_at ?? cur.updatedAt,
        deepLink: n.deep_link ?? cur.deepLink,
        containerName: n.container_name ?? cur.containerName,
        docKind: n.doc_kind ?? cur.docKind,
        count: n.count === undefined ? cur.count : n.count,
        rank: resp.mode === 'search' ? n.rank ?? null : cur.rank,
      });
      continue;
    }
    const a = attrsOf(n);
    const anc = anchorOf(n);
    const k = ringCount.get(anc ?? '') ?? 0;
    ringCount.set(anc ?? '', k + 1);
    const base = anc ? g.getNodeAttributes(anc) : { x: 0, y: 0 };
    const r = unit * (2.2 + 1.1 * Math.sqrt(k + 1));
    const ang = (k + 1) * 2.399963 + hashAngle(n.id);
    g.addNode(n.id, { ...a, x: base.x + r * Math.cos(ang), y: base.y + r * Math.sin(ang), born: now });
    added.push(n.id);
  }
  for (const e of resp.edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    const key = edgeKey(e.source, e.target, e.kind);
    if (!g.hasEdge(key)) g.addEdgeWithKey(key, e.source, e.target, { kind: e.kind, weight: e.weight ?? 1, born: now });
  }
  if (resp.mode === 'focus' && resp.focus && g.hasNode(resp.focus)) {
    const f = resp.nodes.find((n) => n.id === resp.focus);
    if (f) g.setNodeAttribute(resp.focus, 'expanded', { hidden: f.degree_hidden_safe, more: f.more, limit: resp.limit, truncated: resp.truncated });
  }
  resize(g);
  return { ids, added };
}

const rankKind = (k: GraphNodeKind) => (k === 'platform' ? 0 : k === 'container' ? 1 : k === 'person' ? 2 : 3);

function hashAngle(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return ((h >>> 0) % 628) / 100;
}

/** A typical spacing in layout units: the extent divided by sqrt(order), so merges look native. */
function spacing(g: KGraph): number {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  g.forEachNode((_id, a) => {
    minX = Math.min(minX, a.x);
    maxX = Math.max(maxX, a.x);
    minY = Math.min(minY, a.y);
    maxY = Math.max(maxY, a.y);
  });
  if (!Number.isFinite(minX)) return 1;
  const ext = Math.max(maxX - minX, maxY - minY, 1);
  return ext / Math.max(12, Math.sqrt(g.order) * 2.2);
}

export const KIND_ORDER: GraphNodeKind[] = ['platform', 'container', 'person', 'doc'];
