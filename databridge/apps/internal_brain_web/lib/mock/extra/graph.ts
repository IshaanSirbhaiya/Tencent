// Mock handlers owned by the "graph" page agent: GET /ib/graph, POST /ib/graph/openable and
// GET /ib/doc/{id}. Server-side only. Return null for paths this area does not handle.
//
// The world is the same permission-correct synthetic estate the /ib/map mock serves (jdoe 867 items
// in 17 places, ken.ext 24 in 2), so the graph and the map always agree. On top of it this file adds,
// deterministically (seeded by document id, never Math.random):
//   - an author per document, from each place's regular contributors (fictional staff),
//   - document → document links ("links" via a URL, "mentions" via a Jira key), generated once over
//     the WHOLE world and then induced on what the signed-in person can open (the real graph.py does
//     the same: edges exist only between confirmed documents),
//   - a short synthetic passage per document for the side panel.
// Selection, counts and `degree_hidden_safe` follow databridge_ib/graph.py step by step.
import { NextResponse } from 'next/server';
import type { DocView, GraphEdge, GraphNode, KnowledgeGraph, MapItem, Platform } from '../../types';
import { DOCS, ROLES, SCOPE_NOTE, type DocKey, type MockState } from '../fixtures';
import { buildMap } from '../map';
import type { MockHandler } from './types';
import { handle as analytics } from './analytics';

const FRESHNESS = 'Every document shown was checked with its source just now.';
const PLATFORM_ORDER: Platform[] = ['confluence', 'jira', 'slack', 'drive'];
const DEFAULT_LIMIT = 120;
const MAX_LIMIT = 300;
const MAX_OPENABLE = 300;

/* ------------------------------------------------------------------ seeded helpers */

function seeded(s: string): () => number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}
const pick = <T,>(rnd: () => number, xs: readonly T[]): T => xs[Math.floor(rnd() * xs.length) % xs.length];

/* ------------------------------------------------------------------ people (fictional) */

const STAFF: Record<string, string> = {
  'raj.m': 'Raj Menon',
  'weiling.c': 'Wei Ling Chua',
  'arjun.p': 'Arjun Pillai',
  'siti.r': 'Siti Rahman',
  'daniel.k': 'Daniel Koh',
  'mei.t': 'Mei Tan',
  'hafiz.i': 'Hafiz Ismail',
  'grace.o': 'Grace Ong',
  'vikram.n': 'Vikram Nair',
  'aisyah.n': 'Nur Aisyah',
  'ethan.l': 'Ethan Lim',
  'chloe.t': 'Chloe Teo',
  'marcus.g': 'Marcus Goh',
  'farah.a': 'Farah Aziz',
  'kenji.s': 'Kenji Sato',
  'lina.w': 'Lina Wong',
  'omar.y': 'Omar Yusof',
  'huimin.l': 'Hui Min Lee',
  'sanjay.r': 'Sanjay Rao',
  'joanne.t': 'Joanne Tay',
  'ben.c': 'Ben Cheong',
  'aileen.n': 'Aileen Ng',
  'acme.liu': 'Liu Yang (Acme)',
  'acme.santos': 'Rita Santos (Acme)',
};
const nameOf = (id: string) => STAFF[id] ?? ROLES[id]?.name ?? id;

/** Regular contributors per place (`platform:container`), most active first. */
const REGULARS: Record<string, string[]> = {
  'confluence:payment-gateway': ['raj.m', 'jdoe', 'weiling.c', 'arjun.p', 'grace.o'],
  'confluence:sre-runbooks': ['priya.sre', 'daniel.k', 'hafiz.i', 'vikram.n'],
  'confluence:SEC': ['sara.sec', 'omar.y', 'farah.a'],
  'confluence:product': ['leo.ops', 'chloe.t', 'amy.am', 'lina.w'],
  'confluence:compliance': ['maya.compliance', 'joanne.t', 'aileen.n'],
  'confluence:it': ['admin.it', 'ben.c'],
  'confluence:cx': ['amy.am', 'lina.w'],
  'confluence:fincrime': ['mlro.tan', 'aileen.n'],
  'jira:AUTH': ['ethan.l', 'mei.t', 'jdoe', 'kenji.s', 'sanjay.r'],
  'jira:DBMIG': ['jdoe', 'priya.sre', 'daniel.k', 'siti.r'],
  'jira:PAY': ['raj.m', 'jdoe', 'arjun.p', 'weiling.c', 'grace.o', 'marcus.g'],
  'jira:SEC': ['sara.sec', 'omar.y', 'farah.a'],
  'jira:GRC': ['maya.compliance', 'joanne.t'],
  'jira:OPS': ['leo.ops', 'chloe.t', 'huimin.l'],
  'jira:FCC': ['mlro.tan', 'aileen.n'],
  'slack:C0AUTHSD': ['ethan.l', 'mei.t', 'kenji.s', 'jdoe'],
  'slack:C0DBMIG': ['jdoe', 'priya.sre', 'siti.r', 'daniel.k'],
  'slack:C0ENG': ['marcus.g', 'ethan.l', 'raj.m', 'jdoe', 'sanjay.r', 'vikram.n'],
  'slack:C0GENERAL': ['huimin.l', 'joanne.t', 'grace.o', 'ben.c', 'leo.ops', 'amy.am'],
  'slack:C0INCID': ['priya.sre', 'hafiz.i', 'daniel.k', 'vikram.n'],
  'slack:C0PAYENG': ['raj.m', 'jdoe', 'arjun.p', 'weiling.c', 'marcus.g'],
  'slack:C0PRODUCT': ['leo.ops', 'chloe.t', 'lina.w', 'huimin.l'],
  'slack:C0RANDOM': ['grace.o', 'ben.c', 'mei.t', 'siti.r'],
  'slack:C0RELEAS': ['sanjay.r', 'marcus.g', 'kenji.s'],
  'slack:C0SREOC': ['priya.sre', 'hafiz.i', 'vikram.n', 'daniel.k'],
  'slack:C0DBLEAD': ['priya.sre', 'daniel.k'],
  'slack:C0VENDACME': ['acme.liu', 'ken.ext', 'acme.santos'],
  'slack:C0SECQ3': ['sara.sec', 'omar.y'],
  'slack:C0CUSTVOICE': ['amy.am', 'lina.w', 'leo.ops'],
  'slack:C0FINCRIME': ['mlro.tan', 'aileen.n'],
  'slack:C0ITHELP': ['admin.it', 'ben.c'],
  'drive:0ACompany': ['huimin.l', 'joanne.t'],
  'drive:0AEng': ['marcus.g', 'daniel.k', 'priya.sre'],
  'drive:0APricing': ['leo.ops', 'chloe.t'],
  'drive:0ASecRestricted': ['sara.sec'],
  'drive:0AVendor': ['acme.santos', 'ken.ext'],
  'drive:0ACases': ['mlro.tan'],
};

/** Named fixture documents keep a fixed author. */
const NAMED_AUTHOR: Partial<Record<DocKey, string>> = {
  dbmigEpic: 'jdoe',
  dbmigThread: 'siti.r',
  dbmigLeads: 'priya.sre',
  runbook: 'priya.sre',
  rca: 'raj.m',
  postmortem: 'daniel.k',
  followups: 'arjun.p',
  keys: 'weiling.c',
  ocr: 'acme.liu',
  ocrSla: 'acme.santos',
  pricing: 'leo.ops',
};

/** Places whose documents tend to link into each other (a thread mentions a ticket, a page links a runbook). */
const RELATED: Record<string, string[]> = {
  'slack:C0DBMIG': ['jira:DBMIG'],
  'slack:C0DBLEAD': ['jira:DBMIG', 'slack:C0DBMIG'],
  'jira:DBMIG': ['confluence:sre-runbooks'],
  'slack:C0PAYENG': ['jira:PAY', 'confluence:payment-gateway'],
  'confluence:payment-gateway': ['jira:PAY', 'confluence:sre-runbooks'],
  'jira:PAY': ['confluence:payment-gateway'],
  'slack:C0AUTHSD': ['jira:AUTH'],
  'jira:AUTH': ['confluence:payment-gateway'],
  'confluence:sre-runbooks': ['slack:C0SREOC', 'drive:0AEng'],
  'slack:C0INCID': ['confluence:sre-runbooks', 'jira:PAY'],
  'slack:C0SREOC': ['confluence:sre-runbooks', 'slack:C0INCID'],
  'slack:C0ENG': ['jira:AUTH', 'jira:PAY', 'drive:0AEng'],
  'slack:C0RELEAS': ['jira:PAY', 'jira:AUTH'],
  'slack:C0PRODUCT': ['confluence:product', 'jira:OPS'],
  'drive:0AEng': ['confluence:sre-runbooks'],
  'confluence:SEC': ['jira:SEC'],
  'slack:C0SECQ3': ['jira:SEC', 'confluence:SEC'],
  'drive:0ASecRestricted': ['confluence:SEC'],
  'confluence:compliance': ['jira:GRC'],
  'slack:C0VENDACME': ['drive:0AVendor'],
  'drive:0AVendor': ['slack:C0VENDACME'],
  'slack:C0CUSTVOICE': ['confluence:product', 'jira:OPS'],
  'drive:0APricing': ['confluence:product'],
  'slack:C0FINCRIME': ['jira:FCC'],
  'drive:0ACases': ['jira:FCC'],
  'confluence:fincrime': ['jira:FCC'],
  'confluence:it': ['slack:C0ITHELP'],
  'slack:C0ITHELP': ['confluence:it'],
  'slack:C0GENERAL': ['drive:0ACompany'],
};
const LINK_RATE: Record<Platform, number> = { slack: 0.34, confluence: 0.5, jira: 0.3, drive: 0.28 };
const FIXED_LINKS: [DocKey, DocKey, 'mentions' | 'links'][] = [
  ['dbmigThread', 'dbmigEpic', 'mentions'],
  ['dbmigLeads', 'dbmigEpic', 'mentions'],
  ['rca', 'postmortem', 'links'],
  ['followups', 'rca', 'links'],
  ['breach', 'breachEvidence', 'links'],
  ['containment', 'breach', 'links'],
  ['runbook', 'rca', 'links'],
  ['ocr', 'ocrSla', 'links'],
  ['keys', 'runbook', 'links'],
];

/* ------------------------------------------------------------------ the world */

interface WDoc {
  item: MapItem;
  platform: Platform;
  place: string; // platform:container
  container: string; // container key
  containerName: string;
  author: string;
}
interface World {
  docs: Map<string, WDoc>;
  out: Map<string, [string, 'links' | 'mentions'][]>;
}

const DEFAULT_STATE: MockState = { runbookV8: false, jdoeRemoved: false, keysRestricted: false };
const NAMED = new Map<string, DocKey>(Object.entries(DOCS).map(([k, d]) => [d.doc_id, k as DocKey]));
let WORLD: World | null = null;

function authorFor(docId: string, place: string): string {
  const named = NAMED.get(docId);
  if (named && NAMED_AUTHOR[named]) return NAMED_AUTHOR[named]!;
  const regs = REGULARS[place] ?? ['marcus.g'];
  const r = seeded(`author:${docId}`)();
  // Skewed toward the first regulars: real places have one or two heavy writers.
  return regs[Math.min(regs.length - 1, Math.floor(r * r * regs.length * 1.4))];
}

/** Every document in the world (the union of every persona's map), with links generated once. */
function world(): World {
  if (WORLD) return WORLD;
  const docs = new Map<string, WDoc>();
  const byPlace = new Map<string, string[]>();
  for (const sub of Object.keys(ROLES)) {
    const m = buildMap(sub, DEFAULT_STATE, SCOPE_NOTE);
    for (const p of m.platforms)
      for (const c of p.containers) {
        const place = `${p.platform}:${c.container}`;
        for (const it of c.items) {
          if (docs.has(it.doc_id)) continue;
          docs.set(it.doc_id, { item: it, platform: p.platform, place, container: c.container, containerName: c.name, author: authorFor(it.doc_id, place) });
          (byPlace.get(place) ?? byPlace.set(place, []).get(place)!).push(it.doc_id);
        }
      }
  }
  const out = new Map<string, [string, 'links' | 'mentions'][]>();
  const add = (a: string, b: string, rel: 'links' | 'mentions') => {
    if (a === b || !docs.has(a) || !docs.has(b)) return;
    const l = out.get(a) ?? out.set(a, []).get(a)!;
    if (!l.some(([t]) => t === b)) l.push([b, rel]);
  };
  for (const id of [...docs.keys()].sort()) {
    const d = docs.get(id)!;
    const rnd = seeded(`links:${id}`);
    const n = rnd() < LINK_RATE[d.platform] ? (rnd() < 0.25 ? 2 : 1) : 0;
    for (let k = 0; k < n; k++) {
      const rel = RELATED[d.place];
      const place = rel && rnd() < 0.62 ? pick(rnd, rel) : d.place;
      const pool = byPlace.get(place);
      if (!pool?.length) continue;
      const t = pool[Math.floor(rnd() * pool.length) % pool.length];
      add(id, t, docs.get(t)!.platform === 'jira' && d.platform !== 'jira' ? 'mentions' : 'links');
    }
  }
  for (const [a, b, rel] of FIXED_LINKS) add(DOCS[a].doc_id, DOCS[b].doc_id, rel);
  WORLD = { docs, out };
  return WORLD;
}

/* ------------------------------------------------------------------ one person's confirmed graph */

interface Built {
  nodes: Map<string, GraphNode>;
  nbrs: Map<string, Set<string>>;
  edges: Map<string, GraphEdge>;
  docIds: string[];
  recent: (ids: Iterable<string>) => string[];
  docsUnder: (id: string) => string[];
}

function mockState(): MockState {
  const s = (globalThis as unknown as { __ibMock?: { state?: MockState } }).__ibMock?.state;
  return s ?? DEFAULT_STATE;
}
function syncAt(): Record<string, number> {
  const s = (globalThis as unknown as { __ibMock?: { syncAt?: Record<string, number> } }).__ibMock?.syncAt;
  return s ?? {};
}

function build(sub: string): Built {
  const w = world();
  const map = buildMap(sub, mockState(), SCOPE_NOTE);
  const nodes = new Map<string, GraphNode>();
  const nbrs = new Map<string, Set<string>>();
  const edges = new Map<string, GraphEdge>();
  const base = { degree_hidden_safe: null, more: null } as const;
  const link = (a: string, b: string, kind: GraphEdge['kind']) => {
    edges.set(`${a}|${b}|${kind}`, { source: a, target: b, kind });
    (nbrs.get(a) ?? nbrs.set(a, new Set()).get(a)!).add(b);
    (nbrs.get(b) ?? nbrs.set(b, new Set()).get(b)!).add(a);
  };
  const docIds: string[] = [];
  const updated = new Map<string, string>();
  for (const p of map.platforms) {
    const plid = `platform:${p.platform}`;
    for (const c of p.containers) {
      const cid = `container:${p.platform}:${c.container}`;
      for (const it of c.items) {
        const wd = w.docs.get(it.doc_id);
        docIds.push(it.doc_id);
        updated.set(it.doc_id, it.updated_at);
        nodes.set(it.doc_id, {
          ...base,
          id: it.doc_id,
          kind: 'doc',
          platform: p.platform,
          label: it.title,
          container: cid,
          container_name: c.name,
          doc_kind: it.kind,
          version: it.version,
          updated_at: it.updated_at,
          deep_link: it.deep_link,
        });
        if (!nodes.has(cid)) nodes.set(cid, { ...base, id: cid, kind: 'container', platform: p.platform, label: c.name, container: null });
        if (!nodes.has(plid)) nodes.set(plid, { ...base, id: plid, kind: 'platform', platform: p.platform, label: p.platform, container: null });
        link(cid, it.doc_id, 'contains');
        link(plid, cid, 'contains');
        const author = wd?.author ?? authorFor(it.doc_id, `${p.platform}:${c.container}`);
        const aid = `person:${author}`;
        if (!nodes.has(aid)) nodes.set(aid, { ...base, id: aid, kind: 'person', platform: null, label: nameOf(author), container: null });
        link(aid, it.doc_id, 'authored');
      }
    }
  }
  const readable = new Set(docIds);
  for (const d of docIds) for (const [t, rel] of w.out.get(d) ?? []) if (readable.has(t)) link(d, t, rel);
  const recent = (ids: Iterable<string>) =>
    [...ids].sort((a, b) => {
      const ua = updated.get(a) ?? '';
      const ub = updated.get(b) ?? '';
      return ua === ub ? (a < b ? 1 : -1) : ua < ub ? 1 : -1;
    });
  const docsUnder = (id: string) => [...(nbrs.get(id) ?? [])].filter((x) => nodes.get(x)?.kind === 'doc');
  return { nodes, nbrs, edges, docIds, recent, docsUnder };
}

/* ------------------------------------------------------------------ search (token overlap over titles) */

const STOP = new Set(['the', 'a', 'an', 'of', 'to', 'in', 'on', 'for', 'and', 'or', 'is', 'are', 'was', 'what', 'whats', 'how', 'with', 'about', 'any', 'me', 'show', 'my']);
const tokens = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9#\- ]+/g, ' ')
    .split(/\s+/)
    .filter((t) => t.length > 1 && !STOP.has(t));

function search(b: Built, q: string): string[] {
  const qt = tokens(q);
  if (!qt.length) return [];
  const scored: [string, number][] = [];
  for (const id of b.docIds) {
    const n = b.nodes.get(id)!;
    const title = tokens(n.label);
    const place = tokens(n.container_name ?? '');
    let score = 0;
    for (const t of qt) {
      if (title.some((x) => x === t)) score += 2;
      else if (title.some((x) => x.startsWith(t) || t.startsWith(x))) score += 1;
      if (place.includes(t)) score += 0.5;
    }
    if (score >= Math.min(2, qt.length)) scored.push([id, score]);
  }
  scored.sort((a, b2) => b2[1] - a[1] || (b.nodes.get(b2[0])!.updated_at ?? '').localeCompare(b.nodes.get(a[0])!.updated_at ?? ''));
  return scored.slice(0, 12).map(([id]) => id);
}

/* ------------------------------------------------------------------ GET /ib/graph (mirrors graph.py) */

function graphResponse(sub: string, opts: { focus: string | null; q: string | null; limit: number }): KnowledgeGraph | null {
  const b = build(sub);
  const { nodes, nbrs } = b;
  const focus = opts.focus?.trim() || null;
  const q = opts.q?.trim() || null;
  const mode: KnowledgeGraph['mode'] = focus ? 'focus' : q ? 'search' : 'overview';
  const limit = opts.limit;
  let order: string[] = [];
  let ranked: string[] = [];
  if (mode === 'overview') {
    const plats = [...nodes.values()].filter((n) => n.kind === 'platform').sort((a, c) => PLATFORM_ORDER.indexOf(a.platform!) - PLATFORM_ORDER.indexOf(c.platform!)).map((n) => n.id);
    const bySize = (kind: 'container' | 'person') =>
      [...nodes.values()]
        .filter((n) => n.kind === kind)
        .sort((a, c) => b.docsUnder(c.id).length - b.docsUnder(a.id).length || a.label.toLowerCase().localeCompare(c.label.toLowerCase()) || a.id.localeCompare(c.id))
        .map((n) => n.id);
    const conts = bySize('container');
    const people = bySize('person');
    order = [...plats, ...conts.slice(0, Math.max(0, limit - plats.length - Math.min(people.length, Math.floor(limit / 3))))];
    order.push(...people.slice(0, Math.max(0, limit - order.length)));
    order.push(...conts.filter((c) => !order.includes(c)));
  } else if (mode === 'search') {
    ranked = search(b, q!);
    order = [...ranked, ...ranked.map((d) => nodes.get(d)!.container!), ...ranked.flatMap((d) => [...(nbrs.get(d) ?? [])].filter((x) => nodes.get(x)?.kind === 'person'))];
  } else {
    const f = nodes.get(focus!);
    if (!f) return null;
    order = [focus!];
    const around = [...(nbrs.get(focus!) ?? [])];
    if (f.kind === 'doc') {
      order.push(...around.filter((x) => ['container', 'person'].includes(nodes.get(x)!.kind)));
      order.push(...b.recent(around.filter((x) => nodes.get(x)!.kind === 'doc')));
      order.push(`platform:${f.platform}`);
    } else if (f.kind === 'container') {
      const docs = b.recent(b.docsUnder(focus!));
      const first = docs.slice(0, Math.max(1, Math.floor((limit * 2) / 3)));
      order.push(`platform:${f.platform}`, ...first);
      order.push(...[...new Set(first.flatMap((d) => [...nbrs.get(d)!].filter((a) => nodes.get(a)!.kind === 'person')))].sort());
      const firstSet = new Set(docs);
      order.push(...b.recent(new Set(first.flatMap((d) => [...nbrs.get(d)!].filter((t) => nodes.get(t)!.kind === 'doc' && !firstSet.has(t))))));
      order.push(...docs.slice(first.length));
    } else if (f.kind === 'person') {
      const docs = b.recent(b.docsUnder(focus!));
      const first = docs.slice(0, Math.max(1, Math.floor((limit * 2) / 3)));
      order.push(...first, ...new Set(first.map((d) => nodes.get(d)!.container!)));
      order.push(...docs.slice(first.length));
    } else {
      order.push(
        ...around
          .filter((x) => nodes.get(x)!.kind === 'container')
          .sort((a, c) => b.docsUnder(c).length - b.docsUnder(a).length || nodes.get(a)!.label.localeCompare(nodes.get(c)!.label)),
      );
    }
  }
  order = [...new Set(order)].filter((n) => nodes.has(n));
  const shown = order.slice(0, limit);
  const S = new Set(shown);
  const summary: GraphEdge[] = [];
  if (mode === 'overview') {
    const weight = new Map<string, number>();
    for (const n of shown) {
      if (nodes.get(n)!.kind !== 'person') continue;
      for (const d of b.docsUnder(n)) {
        const c = nodes.get(d)!.container!;
        if (S.has(c)) weight.set(`${n}|${c}`, (weight.get(`${n}|${c}`) ?? 0) + 1);
      }
    }
    for (const [k, w] of [...weight.entries()].sort()) {
      const [a, c] = k.split('|');
      summary.push({ source: a, target: c, kind: 'authored', weight: w });
    }
  }
  const hitRank = ranked.filter((d) => S.has(d));
  const outNodes: GraphNode[] = shown.map((n) => {
    const node: GraphNode = { ...nodes.get(n)! };
    // Every mock document is "checked" in the same call, so every count is exact (as for the demo
    // estate live: the widest persona is under graph.py's MAX_CHECKS_OVERVIEW).
    const hidden = [...(nbrs.get(n) ?? [])].filter((x) => !S.has(x)).length;
    node.degree_hidden_safe = hidden;
    node.more = hidden > 0;
    if (node.kind === 'container' || node.kind === 'person') node.count = b.docsUnder(n).length;
    else if (node.kind === 'platform') node.count = [...(nbrs.get(n) ?? [])].filter((x) => nodes.get(x)!.kind === 'container').length;
    if (mode === 'search' && hitRank.includes(n)) node.rank = hitRank.indexOf(n) + 1;
    return node;
  });
  const outEdges = [...b.edges.entries()]
    .sort(([a], [c]) => (a < c ? -1 : a > c ? 1 : 0))
    .map(([, e]) => e)
    .filter((e) => S.has(e.source) && S.has(e.target));
  return {
    as_of: new Date().toISOString(),
    scope_note: SCOPE_NOTE,
    sub,
    mode,
    focus: mode === 'focus' ? focus : null,
    query: mode === 'search' ? q : null,
    limit,
    truncated: order.length > limit,
    nodes: outNodes,
    edges: [...outEdges, ...summary],
    freshness_note: FRESHNESS,
  };
}

/* ------------------------------------------------------------------ GET /ib/doc/{id} */

const NAMED_TEXT: Partial<Record<DocKey, string>> = {
  dbmigEpic:
    'Epic: migrate the ledger database to Postgres 16. Status: In Progress. 3 of 5 shards are copied; shard 4 is in rehearsal. Cutover is planned for 21 October 2026 in the Saturday maintenance window. Rollback: re-point the writers to the old primary; the copy job keeps the old cluster in sync until sign-off. Linked: #db-migration rehearsal thread, SRE runbook for replica promotion.',
  dbmigThread:
    'Siti: shard 4 cutover rehearsal done, but replica lag exceeded 90 seconds during the switch. Flagging as a blocker for the 21 October cutover. Jordan: agreed, raising it on DBMIG-12. The ledger copy job backfill is throttled to 400 rows per second per shard; we can lift it to 600 after the rehearsal. Priya: let us rerun with the throttle lifted on Thursday.',
  dbmigLeads: 'Priya: the copy-tool licence expires on 17 October 2026 and the renewal is still pending with procurement. Daniel: if it lapses we lose the incremental sync, so this blocks DBMIG-12 cutover.',
  runbook:
    'Use this runbook when payment-service is degraded in the primary region. Step 1: confirm the alert in #incidents and page the payments on-call. Step 2: freeze deploys. Step 3: check the settlement queue depth. Step 4: run failover-ctl promote --region secondary and confirm the health endpoint returns ready. Step 5: post a status page update.',
  rca: 'Root cause: an expired intermediate certificate on the primary payment gateway. The failover path used the same certificate chain, so failover did not help. Impact: card payments failed for 47 minutes on 18 July 2026. Follow-ups: PAY-214 certificate expiry alerting, PAY-215 separate chain for failover, PAY-219 game-day drill.',
  keys: 'The next settlement key rotation window is 24 October 2026, 01:00 to 03:00 SGT, owned by the payments platform team. Keys are rotated in the HSM first, then in the settlement service; both old and new keys are accepted for 48 hours.',
  followups: 'Arjun: confirming the outage follow-up tickets: PAY-214 (certificate expiry alerting), PAY-215 (separate chain for failover), PAY-219 (game-day drill). Raj: owners assigned, review in two weeks.',
  postmortem: 'Postmortem for the 18 July payment outage. Timeline, contributing factors and actions. The expired intermediate certificate was not covered by expiry alerting. Actions are tracked as PAY-214, PAY-215 and PAY-219.',
  ocr: 'Liu: OCR model v2.4 rollout to production is planned for next Tuesday after the shadow run. Ken: shadow results look good on the passport set; the driver-licence set needs one more pass.',
  ocrSla: 'Support SLA for the KYC OCR service: P1 response within 30 minutes, 24x7; P2 within 4 business hours. Monthly availability target 99.9 %. Escalation path: Acme support desk, then the account manager.',
};

const PHRASES: Record<Platform, string[]> = {
  confluence: [
    'This page describes {topic} for the {place} team and is the source of truth for the current process.',
    'Owner: {author}. Review cadence: quarterly, or after any incident that touches this area.',
    'Before you change anything here, check the linked tickets and announce the change in the team channel.',
    'Known limits: the numbers below assume the Singapore region; other regions follow the same steps with their own on-call.',
    'Decision: we keep the simpler design until load tests show a need for the alternative.',
    'Rollback is always the first option; record what you did in the incident timeline.',
  ],
  jira: [
    'Status: In Progress. Priority: P2. Assignee: {author}.',
    'Acceptance criteria: {topic} is covered by an alert, a dashboard panel and a runbook entry.',
    'Context: raised after the last review of {place}; see the linked thread for the discussion.',
    'Definition of done: change reviewed, deployed to staging, verified for one week, then promoted.',
    'Risk: low. Customer impact: none expected; the change is behind a feature flag.',
  ],
  slack: [
    '{author}: {topic}.',
    'Follow-up: I will post the numbers here once the job finishes.',
    'Reply: thanks, can you link the ticket so we can track it?',
    'Reply: added to the agenda for the weekly sync.',
    'Reply: no customer impact so far; keeping an eye on the dashboard.',
  ],
  drive: [
    '{topic}. Prepared by {author} for {place}.',
    'Summary: the key decisions and owners are listed on the first page; details follow by section.',
    'Open questions are marked in the margin; please comment rather than edit.',
    'Version history is kept in the file; the latest approved version is the one linked from the team page.',
  ],
};

function topicOf(title: string): string {
  const t = title.replace(/^#[^:]+:\s*/, '').replace(/^[A-Z]+-\d+:\s*/, '');
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function passage(d: WDoc | undefined, node: GraphNode, author: string): string {
  const named = NAMED.get(node.id);
  if (named && NAMED_TEXT[named]) return NAMED_TEXT[named]!;
  const p = (node.platform ?? 'confluence') as Platform;
  const rnd = seeded(`passage:${node.id}`);
  const pool = PHRASES[p];
  const n = 3 + Math.floor(rnd() * 2);
  const picked = [pool[0], ...pool.slice(1).sort(() => (rnd() < 0.5 ? -1 : 1)).slice(0, n - 1)];
  return picked
    .map((s) => s.replace('{topic}', topicOf(node.label)).replace('{place}', d?.containerName ?? node.container_name ?? '').replace('{author}', nameOf(author)))
    .join(' ');
}

function bestChunk(text: string, q: string): string {
  const qt = tokens(q);
  const sentences = text.match(/[^.!?]+[.!?]+/g) ?? [text];
  let best = 0;
  let bestScore = -1;
  sentences.forEach((s, i) => {
    const st = tokens(s);
    const score = qt.filter((t) => st.some((x) => x.startsWith(t))).length;
    if (score > bestScore) {
      bestScore = score;
      best = i;
    }
  });
  return sentences.slice(best, best + 2).join(' ').trim();
}

function docResponse(sub: string, docId: string, q: string | null): DocView | null {
  const b = build(sub);
  const node = b.nodes.get(docId);
  if (!node || node.kind !== 'doc') return null;
  const wd = world().docs.get(docId);
  const authorId = [...(b.nbrs.get(docId) ?? [])].find((x) => b.nodes.get(x)?.kind === 'person')?.slice('person:'.length) ?? null;
  const full = passage(wd, node, authorId ?? '');
  const excerpt = q ? bestChunk(full, q) : full.slice(0, 1200);
  const links = [...(b.edges.values())]
    .filter((e) => (e.kind === 'links' || e.kind === 'mentions') && (e.source === docId || e.target === docId))
    .map((e) => {
      const other = b.nodes.get(e.source === docId ? e.target : e.source)!;
      return { doc_id: other.id, platform: other.platform as Platform, title: other.label, container_name: other.container_name ?? '', kind: other.doc_kind ?? '', relation: e.kind as 'links' | 'mentions' };
    });
  const now = new Date();
  const st = mockState();
  const sync = syncAt()[node.platform!] ?? now.getTime() - 60_000;
  return {
    doc_id: docId,
    platform: node.platform as Platform,
    kind: node.doc_kind ?? 'page',
    container: wd?.container ?? node.container?.split(':').slice(2).join(':') ?? '',
    container_name: node.container_name ?? '',
    title: node.label,
    version: node.version ?? '',
    updated_at: node.updated_at ?? now.toISOString(),
    deep_link: node.deep_link ?? '',
    created_at: node.updated_at ?? null,
    author: authorId ? { id: authorId, name: nameOf(authorId) } : null,
    excerpt,
    excerpt_kind: q ? 'best_chunk' : 'start',
    excerpt_truncated: !q && full.length > 1200,
    links,
    lineage: {
      connector: node.platform!,
      last_sync_at: new Date(sync).toISOString(),
      content_checked_at: now.toISOString(),
      authz_checked_at: now.toISOString(),
      live_read: docId === DOCS.runbook.doc_id && st.runbookV8,
    },
    scope_note: SCOPE_NOTE,
    as_of: now.toISOString(),
  };
}

/* ------------------------------------------------------------------ routes */

const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const notFound = () => json({ detail: 'not found' }, 404);
const unavailable = () => json({ error: 'temporarily unavailable' }, 503);
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const EMPLOYEE = ['employee', 'compliance_officer', 'admin_it'];

/* ------------------------------------------------------------------ live growth (demo-proof, D-24) */
// The mock server has demo features ON (it is never a deployment); the viewer's own Settings toggle
// still decides whether the controls show. A posted note is queued for the poster's next poll of
// GET /ib/graph/changes, once, like the real feed delivers it once.

interface MockLive {
  pending: Map<string, { nodes: GraphNode[]; edges: GraphEdge[] }[]>;
  seq: number;
  posts: Map<string, number[]>;
  engine: { state: 'idle' | 'running' | 'done' | 'failed'; started: number | null; finished: number | null; last: number | null };
}
const live: MockLive = ((globalThis as Record<string, unknown>).__ibMockLive as MockLive) ?? {
  pending: new Map(),
  seq: 0,
  posts: new Map(),
  engine: { state: 'idle', started: null, finished: null, last: null },
};
(globalThis as Record<string, unknown>).__ibMockLive = live;
const ENGINE_MS = 6000;

async function liveHandle({ req, path, method, sub, roles }: Parameters<MockHandler>[0]): Promise<NextResponse | null> {
  if (path === 'ib/status' && method === 'GET') {
    const r = await analytics({ req, path, method, sub, roles });
    if (!r) return null;
    const body = (await r.json()) as Record<string, unknown>;
    return json({ ...body, demo_features: true });
  }
  if (path === 'ib/graph/changes' && method === 'GET') {
    if (!roles.some((r) => EMPLOYEE.includes(r))) return json({ detail: 'forbidden' }, 403);
    await wait(80);
    const q = live.pending.get(sub) ?? [];
    live.pending.set(sub, []);
    const nodes = new Map<string, GraphNode>();
    const edges = new Map<string, GraphEdge>();
    for (const d of q) {
      d.nodes.forEach((n) => nodes.set(n.id, n));
      d.edges.forEach((e) => edges.set(`${e.source}|${e.target}|${e.kind}`, e));
    }
    return json({ cursor: `m.${sub.length}.${Date.now()}`, nodes: [...nodes.values()], edges: [...edges.values()] });
  }
  if (path === 'sim/add-document' && method === 'POST') {
    let body: { platform?: unknown; target?: unknown; text?: unknown } = {};
    try {
      body = await req.json();
    } catch {
      return json({ detail: 'invalid body' }, 422);
    }
    const text = typeof body.text === 'string' ? body.text.replace(/<[^>]{0,2000}>/g, '').trim() : '';
    const target = typeof body.target === 'string' ? body.target : '';
    if (body.platform !== 'slack' || !text || text.length > 2000 || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(target)) return json({ detail: 'invalid body' }, 422);
    const now = Date.now();
    const hits = (live.posts.get(sub) ?? []).filter((t) => now - t < 60_000);
    if (hits.length >= 5) return json({ detail: 'too many requests' }, 429);
    const map = buildMap(sub, mockState(), SCOPE_NOTE);
    const ch = map.platforms.find((p) => p.platform === 'slack')?.containers.find((c) => c.container === target);
    if (!ch) return notFound(); // not a channel this person can read: the one refusal
    live.posts.set(sub, [...hits, now]);
    await wait(240);
    const ts = `${Math.floor(now / 1000)}.${String(++live.seq).padStart(6, '0')}`;
    const id = `slack:${target}:${ts}`;
    const cid = `container:slack:${target}`;
    const base = { degree_hidden_safe: null, more: null } as const;
    const delta = {
      nodes: [
        { id, kind: 'doc', platform: 'slack', label: text.split('\n')[0].slice(0, 80), container: cid, container_name: ch.name, doc_kind: 'thread', version: '1', updated_at: new Date(now).toISOString(), deep_link: `https://companya.slack.example/archives/${target}/p${ts.replace('.', '')}`, ...base },
        { id: cid, kind: 'container', platform: 'slack', label: ch.name, container: null, count: null, ...base },
        { id: 'platform:slack', kind: 'platform', platform: 'slack', label: 'slack', container: null, count: null, ...base },
        { id: `person:${sub}`, kind: 'person', platform: null, label: STAFF[sub] ?? sub, container: null, count: null, ...base },
      ] as GraphNode[],
      edges: [
        { source: cid, target: id, kind: 'contains' },
        { source: 'platform:slack', target: cid, kind: 'contains' },
        { source: `person:${sub}`, target: id, kind: 'authored' },
      ] as GraphEdge[],
    };
    // delivered after a short "webhook" delay, to the poster's own feed only
    setTimeout(() => live.pending.set(sub, [...(live.pending.get(sub) ?? []), delta]), 700);
    return json({ ok: true, doc_id: id, platform: 'slack', container: target, at: new Date(now).toISOString() });
  }
  if ((path === 'sim/engine-refresh' || path === 'sim/engine-status') && method === 'POST') {
    if (!(roles.includes('admin_it') || sub === 'jdoe')) return notFound();
    const e = live.engine;
    const now = Date.now();
    if (e.state === 'running' && e.started && now - e.started >= ENGINE_MS) Object.assign(e, { state: 'done', finished: e.started + ENGINE_MS });
    if (path === 'sim/engine-refresh' && e.state !== 'running') {
      if (e.last && now - e.last < 120_000) return json({ detail: 'too many requests' }, 429);
      Object.assign(e, { state: 'running', started: now, finished: null, last: now });
    }
    const el = e.started ? now - e.started : 0;
    const iso = (t: number | null) => (t ? new Date(t).toISOString() : null);
    return json({
      state: e.state,
      step: e.state === 'running' ? (el < ENGINE_MS * 0.7 ? 'building' : el < ENGINE_MS * 0.9 ? 'publishing' : 'reloading') : null,
      started_at: iso(e.started),
      finished_at: iso(e.finished),
      snapshot_at: iso(e.finished ?? now - 3_600_000),
      reloaded: e.state === 'done' ? true : null,
    });
  }
  return null;
}

export const handle: MockHandler = async (ctx) => {
  const lr = await liveHandle(ctx);
  if (lr) return lr;
  return graphHandle(ctx);
};

const graphHandle: MockHandler = async ({ req, path, method, sub, roles }) => {
  const isGraph = path === 'ib/graph' && method === 'GET';
  const isOpenable = path === 'ib/graph/openable' && method === 'POST';
  const isDoc = path.startsWith('ib/doc/') && method === 'GET';
  if (!isGraph && !isOpenable && !isDoc) return null;
  if (!roles.some((r) => EMPLOYEE.includes(r))) return json({ detail: 'forbidden' }, 403);

  if (isGraph) {
    const p = req.nextUrl.searchParams;
    const focus = p.get('focus');
    const q = p.get('q');
    const rawLimit = p.get('limit');
    const limit = rawLimit === null ? DEFAULT_LIMIT : Number(rawLimit);
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT || (focus ?? '').length > 300 || (q ?? '').length > 500) return json({ detail: 'invalid parameters' }, 422);
    if (/\[mock:503\]/i.test(q ?? '')) return unavailable();
    // The live route confirms every document with its source: overview ~3.4 s, search/focus < 0.5 s.
    await wait(focus ? 260 : q ? 380 : 1300);
    const g = graphResponse(sub, { focus, q, limit });
    return g ? json(g) : notFound();
  }

  if (isOpenable) {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return json({ detail: 'invalid body' }, 422);
    }
    const ids = (body as { doc_ids?: unknown })?.doc_ids;
    const keys = body && typeof body === 'object' ? Object.keys(body) : [];
    if (!Array.isArray(ids) || keys.length !== 1 || ids.length > MAX_OPENABLE || ids.some((x) => typeof x !== 'string' || x.length > 300))
      return json({ detail: 'invalid body' }, 422);
    await wait(240);
    const mine = new Set(build(sub).docIds);
    const openable = [...new Set(ids as string[])].filter((id) => mine.has(id));
    return json({ sub, as_of: new Date().toISOString(), openable });
  }

  // GET /ib/doc/{id}
  const docId = decodeURIComponent(path.slice('ib/doc/'.length));
  const q = req.nextUrl.searchParams.get('q');
  if ((q ?? '').length > 500) return json({ detail: 'invalid parameters' }, 422);
  await wait(180);
  const d = docResponse(sub, docId, q?.trim() || null);
  return d ? json(d) : notFound();
};

/**
 * For lib/mock/finals.ts `mockDoc` (coordinator-owned): the author and openable links this graph mock
 * uses for a document, so GET /ib/doc and GET /ib/graph agree in mock mode. null = not openable.
 */
export function graphDocExtras(sub: string, docId: string): { author: { id: string; name: string } | null; links: DocView['links'] } | null {
  const d = docResponse(sub, docId, null);
  return d ? { author: d.author, links: d.links } : null;
}
