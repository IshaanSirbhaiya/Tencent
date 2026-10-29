// Mock-mode handlers for the finals endpoints: /ib/trace, /audit/journey, /audit/stats, /ib/graph,
// /ib/doc. Server-side only (imported by lib/mock/server.ts). Everything is derived from the same
// in-memory synthetic ledger and the same permission-correct map the other mock routes use, so the
// numbers agree with the audit console. Shapes mirror the Python handlers (see lib/types.ts).
import { createHash } from 'node:crypto';
import type {
  AuditEvent,
  AuditStats,
  DocView,
  GraphEdge,
  GraphNode,
  Journey,
  KnowledgeGraph,
  LedgerRecordRef,
  MapItem,
  Platform,
  Stage,
  Trace,
} from '../types';
import { ALLOW_VERDICTS, STAGES } from '../types';
import { DOCS, ROLES, SCOPE_NOTE, type MockState } from './fixtures';
import { buildMap } from './map';

const sha = (s: string) => createHash('sha256').update(s).digest('hex');
const KINDS = ['request.accepted', 'retrieval.decided', 'answer.released'] as const;
const PROMPT_VERSION = 'ib-answer-v3';
const POLICY_VERSION = 'gate-policy-2026.10';

function docMeta(docId: string) {
  return Object.values(DOCS).find((d) => d.doc_id === docId);
}

/** Deterministic per-stage split of the request's latency (the real ledger seals measured values). */
export function stageMs(e: AuditEvent): Record<Stage, number> {
  const total = e.latency_ms ?? 1200;
  const n = parseInt(sha(e.req_id).slice(0, 6), 16);
  const accept = 3 + (n % 5);
  const gate1 = 18 + (n % 23);
  const gate2 = 140 + (n % 260);
  const seal = 6 + (n % 9);
  const compose = Math.max(40, total - accept - gate1 - gate2 - seal);
  return { accept, gate1, gate2, seal, compose };
}

function records(e: AuditEvent, index: number): LedgerRecordRef[] {
  const kinds = e.refused === null ? KINDS.slice(0, 2) : KINDS;
  let prev = sha(`genesis:${index}`);
  return kinds.map((kind, k) => {
    const hash = sha(prev + e.req_id + kind);
    prev = hash;
    const t = new Date(Date.parse(e.event_time) + k * 400).toISOString();
    return { seq: 1000 + index * 3 + k, kind, hash, event_time: t, recorded_at: t };
  });
}

function citationsOf(e: AuditEvent) {
  if (e.refused) return [];
  return (e.released ?? []).map((id) => {
    const d = docMeta(id);
    return { doc_id: id, version: d?.version ?? '1', content_checked_at: e.event_time, authz_checked_at: e.event_time, title: d?.title ?? null };
  });
}

function released(e: AuditEvent) {
  return (e.released ?? []).map((id) => ({ doc_id: id, title: docMeta(id)?.title ?? null, container: docMeta(id)?.container ?? null }));
}

const status = (e: AuditEvent) => (e.refused === null || e.refused === undefined ? 'in_progress' : e.refused ? 'refused' : 'released');

/** GET /ib/trace/{req}: own requests only; never the considered set or Gate-2 reasons. */
export function mockTrace(ledger: AuditEvent[], sub: string, reqId: string): Trace | null {
  const i = ledger.findIndex((e) => e.req_id === reqId && e.kind === 'request');
  const e = ledger[i];
  if (!e || e.sub !== sub) return null;
  return {
    req_id: e.req_id,
    question: e.question ?? null,
    channel: e.channel ?? null,
    event_time: e.event_time,
    status: status(e),
    policy_version: POLICY_VERSION,
    model: e.model ?? null,
    prompt_version: PROMPT_VERSION,
    latency_ms: e.latency_ms ?? null,
    stage_ms: stageMs(e),
    dropped_sentences_count: 0,
    released: released(e),
    citations: citationsOf(e),
    answer_sha256: sha(`answer:${e.req_id}`),
    events: records(e, i).map(({ seq, kind, hash, recorded_at }) => ({ seq, kind, hash, recorded_at })),
  };
}

/** GET /audit/journey/{req}: compliance view, with Gate-2 verdicts and reason codes; no answer text. */
export function mockJourney(ledger: AuditEvent[], reqId: string): Journey | null {
  const i = ledger.findIndex((e) => e.req_id === reqId && e.kind === 'request');
  const e = ledger[i];
  if (!e) return null;
  const g2 = new Map((e.gate2 ?? []).map((g) => [g.doc_id, g]));
  const rel = new Set(e.released ?? []);
  return {
    req_id: e.req_id,
    sub: e.sub,
    channel: e.channel ?? null,
    question: e.question ?? null,
    question_sha256: sha(`q:${e.question ?? ''}`),
    event_time: e.event_time,
    recorded_at: e.event_time,
    synthetic_replay: !!e.synthetic_replay,
    policy_version: POLICY_VERSION,
    directory_epoch: 7,
    gate1_policy_version: POLICY_VERSION,
    time_window: null,
    considered: (e.considered_meta ?? []).map((m) => {
      const d = docMeta(m.doc_id);
      const g = g2.get(m.doc_id);
      return {
        doc_id: m.doc_id,
        platform: m.platform ?? null,
        container: m.container ?? null,
        container_name: d?.container_name ?? null,
        title: m.title ?? null,
        verdict: g?.verdict ?? null,
        reason_code: g?.reason_code ?? null,
        decision: g ? (ALLOW_VERDICTS.has(g.verdict) ? 'allow' : 'deny') : null,
        considered: true,
        released: rel.has(m.doc_id),
        cited: rel.has(m.doc_id) && !e.refused,
      };
    }),
    gate2: e.gate2 ?? [],
    released: released(e),
    citations: citationsOf(e),
    model: e.model ?? null,
    prompt_version: PROMPT_VERSION,
    answer_ref: `ans_${e.req_id}`,
    answer_sha256: sha(`answer:${e.req_id}`),
    refused: e.refused ?? null,
    latency_ms: e.latency_ms ?? null,
    dropped_sentences_count: 0,
    stage_ms: stageMs(e),
    failure: null,
    status: status(e),
    reveals: [],
    events: records(e, i),
  };
}

function pct(values: number[], p: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.max(0, Math.min(s.length - 1, Math.round(p * (s.length - 1))))];
}

const platformOf = (id: string) => {
  const h = id.split(':', 1)[0];
  return h === 'confluence' || h === 'jira' || h === 'slack' || h === 'drive' ? h : 'other';
};

/** GET /audit/stats?days=: the same aggregation as audit/stats.py, over the mock ledger. */
export function mockStats(all: AuditEvent[], days: number, includeReplay: boolean): AuditStats {
  const now = new Date();
  const since = new Date(now.getTime() - days * 86400_000);
  const reqs = all.filter((e) => e.kind === 'request' && new Date(e.event_time) >= since && (includeReplay || !e.synthetic_replay));
  const other = all.filter((e) => e.kind !== 'request' && new Date(e.event_time) >= since);
  const inc = (o: Record<string, number>, k: string, n = 1) => (o[k] = (o[k] ?? 0) + n);
  const channels: Record<string, number> = {};
  const models: Record<string, number> = {};
  const verdicts: Record<string, number> = {};
  const reasons: Record<string, number> = {};
  const platforms: Record<string, Record<string, number>> = {};
  const people: Record<string, Record<string, number>> = {};
  const perDay: Record<string, Record<string, number>> = {};
  const lat: number[] = [];
  const stageVals: Record<Stage, number[]> = { accept: [], gate1: [], gate2: [], seal: [], compose: [] };
  let answered = 0;
  let refused = 0;
  let considered = 0;
  let releasedDocs = 0;
  let citations = 0;
  let fallback = 0;
  for (const e of reqs) {
    const day = e.event_time.slice(0, 10);
    inc(channels, e.channel ?? 'ui');
    people[e.sub] ??= {};
    perDay[day] ??= {};
    inc(people[e.sub], 'asked');
    inc(perDay[day], 'asked');
    for (const d of e.considered ?? []) inc((platforms[platformOf(d)] ??= {}), 'considered');
    for (const d of e.released ?? []) inc((platforms[platformOf(d)] ??= {}), 'released');
    considered += (e.considered ?? []).length;
    releasedDocs += (e.released ?? []).length;
    for (const g of e.gate2 ?? []) {
      inc(verdicts, g.verdict);
      inc(reasons, g.reason_code);
      inc((platforms[platformOf(g.doc_id)] ??= {}), 'gate2_checked');
    }
    const r = e.refused ? 'refused' : 'answered';
    if (e.refused) refused += 1;
    else answered += 1;
    inc(people[e.sub], r);
    inc(perDay[day], r);
    if (e.model) inc(models, `${e.model.provider}/${e.model.model}`);
    if (e.model?.fallback_used) fallback += 1;
    if (!e.refused) citations += (e.released ?? []).length;
    if (typeof e.latency_ms === 'number') lat.push(e.latency_ms);
    const st = stageMs(e);
    for (const s of STAGES) stageVals[s].push(st[s]);
  }
  const gate2Total = Object.values(verdicts).reduce((a, b) => a + b, 0);
  const allowed = Object.entries(verdicts).reduce((a, [k, v]) => a + (ALLOW_VERDICTS.has(k) ? v : 0), 0);
  const finished = answered + refused;
  const kinds: Record<string, number> = {};
  for (const e of reqs) {
    inc(kinds, 'request.accepted');
    inc(kinds, 'retrieval.decided');
    inc(kinds, 'answer.released');
  }
  for (const e of other) inc(kinds, e.kind ?? 'other');
  const ledgerEvents = all.length * 3;
  return {
    window: { since: since.toISOString(), until: now.toISOString(), days },
    ledger: { events: ledgerEvents, first_seq: 1, last_seq: ledgerEvents, head: sha(`head:${ledgerEvents}`) },
    totals: {
      requests: reqs.length,
      answered,
      refused,
      failed: 0,
      answer_rate: finished ? Math.round((answered / finished) * 10000) / 10000 : null,
      citations,
      dropped_sentences: 0,
      fallback_used: fallback,
      reveals: other.filter((e) => e.kind === 'audit.reveal').length,
      audit_queries: other.filter((e) => e.kind === 'audit.query').length,
      access_changes: 0,
      syncs: 0,
      map_views: 0,
      logins: 0,
    },
    funnel: {
      asked: reqs.length,
      considered_docs: considered,
      gate2_checked: gate2Total,
      gate2_allowed: allowed,
      gate2_denied: gate2Total - allowed,
      released_docs: releasedDocs,
      cited: citations,
      answered,
    },
    gate2: { verdicts, reasons },
    latency_ms: {
      count: lat.length,
      p50: pct(lat, 0.5),
      p95: pct(lat, 0.95),
      max: lat.length ? Math.max(...lat) : null,
      mean: lat.length ? Math.round(lat.reduce((a, b) => a + b, 0) / lat.length) : null,
    },
    stages_ms: Object.fromEntries(STAGES.map((s) => [s, { p50: pct(stageVals[s], 0.5), p95: pct(stageVals[s], 0.95), count: stageVals[s].length }])) as AuditStats['stages_ms'],
    models,
    channels,
    platforms: platforms as AuditStats['platforms'],
    people: Object.entries(people)
      .map(([sub, c]): AuditStats['people'][number] => ({ sub, ...c }))
      .sort((a, b) => (b.asked ?? 0) - (a.asked ?? 0) || a.sub.localeCompare(b.sub)),
    days: Object.keys(perDay)
      .sort()
      .map((day) => ({ day, ...perDay[day] })),
    kinds,
  };
}

/* ---------------------------------------------------------------- graph + doc */

const AUTHORS = Object.keys(ROLES).filter((s) => s !== 'admin.it');
const authorOf = (docId: string) => AUTHORS[parseInt(sha(docId).slice(0, 4), 16) % AUTHORS.length];
const PER_CONTAINER = 6;
const LINKS: [string, string, 'mentions' | 'links'][] = [
  [DOCS.dbmigThread.doc_id, DOCS.dbmigEpic.doc_id, 'mentions'],
  [DOCS.dbmigLeads.doc_id, DOCS.dbmigEpic.doc_id, 'mentions'],
  [DOCS.rca.doc_id, DOCS.postmortem.doc_id, 'links'],
  [DOCS.followups.doc_id, DOCS.rca.doc_id, 'links'],
  [DOCS.breach.doc_id, DOCS.breachEvidence.doc_id, 'links'],
  [DOCS.containment.doc_id, DOCS.breach.doc_id, 'links'],
  [DOCS.runbook.doc_id, DOCS.rca.doc_id, 'links'],
  [DOCS.ocr.doc_id, DOCS.ocrSla.doc_id, 'links'],
];

interface Universe {
  docs: Map<string, { item: MapItem; platform: Platform; container: string; containerName: string }>;
  nodes: Map<string, GraphNode>;
  edges: GraphEdge[];
}

/** The induced graph over what `sub` can open (the mock map is already permission-correct). */
function universe(sub: string, state: MockState): Universe {
  const map = buildMap(sub, state, SCOPE_NOTE);
  const docs: Universe['docs'] = new Map();
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  const base = { degree_hidden_safe: null, more: null } as const;
  for (const p of map.platforms) {
    const pid = `platform:${p.platform}`;
    nodes.set(pid, { ...base, id: pid, kind: 'platform', platform: p.platform, label: p.platform, container: null, count: p.containers.length });
    for (const c of p.containers) {
      const cid = `container:${p.platform}:${c.container}`;
      nodes.set(cid, { ...base, id: cid, kind: 'container', platform: p.platform, label: c.name, container: null, count: c.count });
      edges.push({ source: pid, target: cid, kind: 'contains' });
      // Named fixture docs first, then the most recent: a readable sample, like the real cap.
      const named = new Set(Object.values(DOCS).map((d) => d.doc_id));
      const items = [...c.items.filter((i) => named.has(i.doc_id)), ...c.items.filter((i) => !named.has(i.doc_id))].slice(0, PER_CONTAINER);
      for (const it of items) {
        docs.set(it.doc_id, { item: it, platform: p.platform, container: cid, containerName: c.name });
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
        edges.push({ source: cid, target: it.doc_id, kind: 'contains' });
        const a = authorOf(it.doc_id);
        const aid = `person:${a}`;
        if (!nodes.has(aid)) nodes.set(aid, { ...base, id: aid, kind: 'person', platform: null, label: ROLES[a]?.name ?? a, container: null, count: 0 });
        nodes.get(aid)!.count = (nodes.get(aid)!.count ?? 0) + 1;
        edges.push({ source: aid, target: it.doc_id, kind: 'authored' });
      }
    }
  }
  for (const [a, b, kind] of LINKS) if (docs.has(a) && docs.has(b)) edges.push({ source: a, target: b, kind });
  return { docs, nodes, edges };
}

/** GET /ib/graph: overview, `q` search or one-hop `focus`. Unknown or unopenable focus: null (404). */
export function mockGraph(sub: string, state: MockState, opts: { focus?: string | null; q?: string | null; limit?: number }): KnowledgeGraph | null {
  const u = universe(sub, state);
  const limit = Math.max(1, Math.min(300, opts.limit ?? 120));
  const nbrs = new Map<string, Set<string>>();
  for (const e of u.edges) {
    (nbrs.get(e.source) ?? nbrs.set(e.source, new Set()).get(e.source)!).add(e.target);
    (nbrs.get(e.target) ?? nbrs.set(e.target, new Set()).get(e.target)!).add(e.source);
  }
  let mode: KnowledgeGraph['mode'] = 'overview';
  let order: string[] = [];
  const q = opts.q?.trim();
  if (opts.focus) {
    if (!u.nodes.has(opts.focus)) return null;
    mode = 'focus';
    order = [opts.focus, ...(nbrs.get(opts.focus) ?? [])];
  } else if (q) {
    mode = 'search';
    const hits = [...u.docs.keys()].filter((id) => u.nodes.get(id)!.label.toLowerCase().includes(q.toLowerCase())).slice(0, 20);
    order = [...hits, ...hits.map((h) => u.nodes.get(h)!.container!), ...hits.map((h) => `person:${authorOf(h)}`)];
  } else {
    order = [...u.nodes.values()].filter((n) => n.kind !== 'doc').map((n) => n.id);
  }
  order = [...new Set(order)].filter((id) => u.nodes.has(id));
  const shown = order.slice(0, limit);
  const S = new Set(shown);
  const nodes = shown.map((id, i) => {
    const hidden = [...(nbrs.get(id) ?? [])].filter((x) => !S.has(x)).length;
    return { ...u.nodes.get(id)!, degree_hidden_safe: hidden, more: hidden > 0, ...(mode === 'search' && u.docs.has(id) ? { rank: i + 1 } : {}) };
  });
  return {
    as_of: new Date().toISOString(),
    scope_note: SCOPE_NOTE,
    sub,
    mode,
    focus: mode === 'focus' ? opts.focus ?? null : null,
    query: mode === 'search' ? q ?? null : null,
    limit,
    truncated: order.length > limit,
    nodes,
    edges: u.edges.filter((e) => S.has(e.source) && S.has(e.target)),
    freshness_note: 'Every document shown was checked with its source just now.',
  };
}

/** GET /ib/doc/{id}: a document `sub` can open, else null (the route answers the same 404). */
export function mockDoc(sub: string, state: MockState, docId: string, syncAt: Record<string, number>): DocView | null {
  const map = buildMap(sub, state, SCOPE_NOTE);
  for (const p of map.platforms) {
    for (const c of p.containers) {
      const it = c.items.find((i) => i.doc_id === docId);
      if (!it) continue;
      const now = new Date().toISOString();
      const a = authorOf(docId);
      const u = universe(sub, state);
      const links = LINKS.filter(([x, y]) => x === docId || y === docId)
        .map(([x, y, relation]) => ({ other: x === docId ? y : x, relation }))
        .filter((l) => u.docs.has(l.other))
        .map((l) => {
          const o = u.docs.get(l.other)!;
          return { doc_id: l.other, platform: o.platform, title: o.item.title, container_name: o.containerName, kind: o.item.kind, relation: l.relation };
        });
      return {
        doc_id: it.doc_id,
        platform: p.platform,
        kind: it.kind,
        container: c.container,
        container_name: c.name,
        title: it.title,
        version: it.version,
        updated_at: it.updated_at,
        deep_link: it.deep_link,
        created_at: it.updated_at,
        author: { id: a, name: ROLES[a]?.name ?? a },
        excerpt: `(Synthetic demo excerpt) ${it.title}. This passage is generated for mock mode so the document panel can be built without the backend; the live service returns the masked indexed text of the version checked just now.`,
        excerpt_kind: 'start',
        excerpt_truncated: false,
        links,
        lineage: {
          connector: p.platform,
          last_sync_at: new Date(syncAt[p.platform] ?? Date.now()).toISOString(),
          content_checked_at: now,
          authz_checked_at: now,
          live_read: false,
        },
        scope_note: SCOPE_NOTE,
        as_of: now,
      };
    }
  }
  return null;
}
