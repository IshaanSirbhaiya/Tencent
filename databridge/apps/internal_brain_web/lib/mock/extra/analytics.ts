// Mock handlers owned by the "analytics" page agent (Mission control, request journey, analytics).
// Server-side only. Serves the backend_v1.md shapes of:
//   GET /audit/stats, GET /audit/journey/{req}, GET /audit/events?kind=request.accepted,
//   GET /ib/trace/{req} (the public-safe receipt), GET /ib/status, GET /ib/history.
//
// One synthetic, hash-chained ledger feeds all of them, so the numbers agree with each other:
//   1. a deterministic "seeded demo history" (channel `replay`, `synthetic_replay: true`, spread over
//      the last 30 days, like scripts/seed_demo_activity.py), built from the Company A fixtures and the
//      permission-correct mock map (a request only ever releases documents its asker can open);
//   2. the shared mock store's own requests (the 13 fixture rows plus every live ask in this session);
//   3. governance events (syncs, sign-ins, audit queries, reveals, access changes, views).
// Stats are computed exactly like audit/stats.py over that ledger. Synthetic data only.
import { createHash } from 'node:crypto';
import { NextResponse } from 'next/server';
import type { AuditEvent, ModelInfo } from '../../types';
import { DEEPSEEK, DOCS, EXTRACTIVE, HY3, MOCK_MODEL_LABELS, SCOPE_NOTE, type DocKey, type MockState } from '../fixtures';
import { buildMap } from '../map';
import type { MockHandler } from './types';

/* ------------------------------------------------------------------------------------------------ */
/* basics                                                                                            */

const sha = (s: string) => createHash('sha256').update(s).digest('hex');
const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const notFound = () => json({ detail: 'not found' }, 404);
const DAY = 86400_000;
const STAGES = ['accept', 'gate1', 'gate2', 'seal', 'compose', 'release'] as const;
type StageName = (typeof STAGES)[number];
const PLATFORMS = ['confluence', 'jira', 'slack', 'drive'] as const;
const ALLOWED = new Set(['verified', 'drift_material']);
const POLICY_VERSION = 'ib-policy-1';
const PROMPT_VERSION = 'ib-prompt-2026-10-13.1';

function mulberry32(a: number) {
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const DOC_BY_ID = new Map(Object.values(DOCS).map((d) => [d.doc_id, d]));
const platformOf = (id: string) => {
  const h = id.split(':', 1)[0];
  return (PLATFORMS as readonly string[]).includes(h) ? h : 'other';
};

/** The shared mock store (lib/mock/server.ts keeps it on globalThis). Read-only here, except for
 *  appending `audit.query` rows exactly as the built-in audit routes do. */
interface SharedStore {
  state: MockState;
  ledger: AuditEvent[];
}
function shared(): SharedStore | null {
  const s = (globalThis as unknown as { __ibMock?: SharedStore }).__ibMock;
  return s && Array.isArray(s.ledger) ? s : null;
}

/* ------------------------------------------------------------------------------------------------ */
/* requests                                                                                          */

type Status = 'released' | 'refused' | 'failed' | 'in_progress';

interface Req {
  req_id: string;
  sub: string;
  channel: string;
  question: string;
  t: number;
  replay: boolean;
  status: Status;
  model: ModelInfo | null;
  considered: string[];
  gate2: { doc_id: string; verdict: string; reason_code: string }[];
  released: string[];
  cited: string[];
  stage_ms: Partial<Record<StageName, number>>;
  latency_ms: number | null;
  dropped: number;
  failure: { stage: string; error_code: string } | null;
}

interface Gov {
  t: number;
  kind: string;
  sub: string;
  target?: string;
  reason?: string;
}

const TEMPLATES: { q: string; docs: DocKey[] }[] = [
  { q: 'What is the payment service failover procedure?', docs: ['runbook'] },
  { q: 'What was the root cause of the July payment outage, and which follow-ups were raised?', docs: ['rca', 'postmortem', 'followups'] },
  { q: 'When is the next settlement key rotation window?', docs: ['keys'] },
  { q: 'What is the status of the ledger database migration?', docs: ['dbmigEpic', 'dbmigThread'] },
  { q: 'Was the copy-tool licence renewal approved?', docs: ['dbmigLeads'] },
  { q: 'How do we drain a Kubernetes node pool safely?', docs: ['k8sDrain'] },
  { q: 'How is SLO burn-rate alerting configured for payments?', docs: ['burnRate', 'runbook'] },
  { q: 'Summarise the Q3 breach incident report.', docs: ['breach', 'breachEvidence', 'containment'] },
  { q: 'Which dependency CVEs are open in the October review?', docs: ['cves'] },
  { q: 'When is the next secret rotation?', docs: ['secretRotation'] },
  { q: 'What are the corporate card pricing tiers?', docs: ['cardTiers'] },
  { q: 'Can we offer multi-currency accounts to an Indonesian SME customer?', docs: ['idPolicy', 'pricing'] },
  { q: 'What are customers saying about plan limits?', docs: ['planLimits'] },
  { q: 'When will the OCR model v2.4 reach production?', docs: ['ocr', 'ocrSla'] },
  { q: 'How long do we keep audit logs?', docs: ['retention'] },
  { q: 'What goes into the technology risk audit evidence pack?', docs: ['auditPack'] },
  { q: 'Who is on IT on-call this week?', docs: ['itOncall'] },
  { q: 'What is the Velquarry onboarding exception process?', docs: [] },
  { q: 'Show me the board minutes about the acquisition.', docs: [] },
];

const PERSONAS: [string, number][] = [
  ['jdoe', 22],
  ['priya.sre', 16],
  ['leo.ops', 12],
  ['sara.sec', 10],
  ['amy.am', 10],
  ['ken.ext', 9],
  ['mlro.tan', 8],
  ['maya.compliance', 7],
  ['admin.it', 6],
];

const openCache = new Map<string, Set<string>>();
/** Fixture documents `sub` can open now (the mock map is permission-correct). */
function openable(sub: string, state: MockState): Set<string> {
  const key = `${sub}|${state.runbookV8 ? 1 : 0}${state.jdoeRemoved ? 1 : 0}${state.keysRestricted ? 1 : 0}`;
  let s = openCache.get(key);
  if (!s) {
    const ids = new Set<string>();
    for (const p of buildMap(sub, state, SCOPE_NOTE).platforms) for (const c of p.containers) for (const it of c.items) if (DOC_BY_ID.has(it.doc_id)) ids.add(it.doc_id);
    s = ids;
    openCache.set(key, s);
  }
  return s;
}

/** Deterministic stage timings; latency is their sum (they end before the final append). */
function timings(rnd: () => number, model: ModelInfo | null, checked: number, refused: boolean): Partial<Record<StageName, number>> {
  const r = (a: number, b: number) => Math.round(a + rnd() * (b - a));
  const compose = refused
    ? r(3, 14)
    : model?.model === 'hy3'
      ? r(1050, 2700)
      : model?.model?.startsWith('deepseek')
        ? r(2300, 4300)
        : r(22, 95);
  return {
    accept: r(2, 6),
    gate1: r(11, 42),
    gate2: r(70, 120) + checked * r(28, 95),
    seal: r(3, 11),
    compose,
    release: r(2, 8),
  };
}

const sum = (s: Partial<Record<StageName, number>>) => STAGES.reduce((a, k) => a + (s[k] ?? 0), 0);

const BASE_STATE: MockState = { runbookV8: false, jdoeRemoved: false, keysRestricted: false };

interface Seeded {
  anchor: number;
  reqs: Req[];
  gov: Gov[];
}

/** The seeded demo history, generated once per server process relative to its start. */
function seeded(): Seeded {
  const g = globalThis as unknown as { __ibAnalytics?: Seeded };
  if (g.__ibAnalytics) return g.__ibAnalytics;
  const anchor = Date.now();
  const rnd = mulberry32(20261103);
  const pick = <T,>(xs: T[]) => xs[Math.floor(rnd() * xs.length)];
  const weighted = () => {
    const total = PERSONAS.reduce((a, [, w]) => a + w, 0);
    let x = rnd() * total;
    for (const [p, w] of PERSONAS) if ((x -= w) < 0) return p;
    return PERSONAS[0][0];
  };
  const allDocs = Object.values(DOCS).map((d) => d.doc_id);
  const reqs: Req[] = [];
  const gov: Gov[] = [];
  for (let day = 29; day >= 0; day -= 1) {
    const date = new Date(anchor - day * DAY);
    const weekend = date.getUTCDay() === 0 || date.getUTCDay() === 6;
    const n = weekend ? 2 + Math.floor(rnd() * 3) : 8 + Math.floor(rnd() * 7);
    for (let k = 0; k < n; k += 1) {
      const sub = weighted();
      const open = openable(sub, BASE_STATE);
      const usable = TEMPLATES.filter((t) => t.docs.some((d) => open.has(DOCS[d].doc_id)));
      const tpl = usable.length && rnd() < 0.72 ? pick(usable) : pick(TEMPLATES);
      const hourSgt = 9 + Math.floor(rnd() * 10);
      const t0 = new Date(date);
      t0.setUTCHours(hourSgt - 8, Math.floor(rnd() * 60), Math.floor(rnd() * 60), Math.floor(rnd() * 1000));
      let t = t0.getTime();
      if (t >= anchor) t = anchor - Math.floor(rnd() * 3_600_000) - 60_000;
      // channel: seeded replay, plus rehearsal traffic (UI and WorkBuddy) in the last ten days
      let channel = 'replay';
      let replay = true;
      if (day <= 9 && rnd() < 0.28) {
        channel = rnd() < 0.3 ? 'workbuddy-mcp' : 'ui';
        replay = false;
      }
      const relevant = tpl.docs.map((d) => DOCS[d].doc_id).filter((id) => open.has(id));
      const stale = tpl.docs.map((d) => DOCS[d].doc_id).filter((id) => !open.has(id) && rnd() < 0.35);
      const others = allDocs.filter((id) => open.has(id) && !relevant.includes(id));
      const noise: string[] = [];
      const nNoise = rnd() < 0.55 ? 1 : rnd() < 0.3 ? 2 : 0;
      for (let i = 0; i < nNoise && others.length; i += 1) {
        const x = pick(others);
        if (!noise.includes(x)) noise.push(x);
      }
      const rankedOut = others.filter((x) => !noise.includes(x));
      const extra = rnd() < 0.3 && rankedOut.length ? [pick(rankedOut)] : [];
      const considered = [...relevant, ...noise, ...stale, ...extra];
      const checked = [...relevant, ...stale, ...noise.filter(() => rnd() < 0.5)];
      const gate2 = checked.map((doc_id) => {
        if (!open.has(doc_id)) {
          const pf = platformOf(doc_id);
          if (rnd() < 0.08) return { doc_id, verdict: 'vanished', reason_code: pf === 'slack' ? 'thread_gone' : pf === 'jira' ? 'issue_gone' : 'page_gone' };
          return { doc_id, verdict: 'forbidden', reason_code: pf === 'slack' ? 'not_member' : 'not_permitted' };
        }
        const x = rnd();
        if (x < 0.035) return { doc_id, verdict: 'forbidden', reason_code: 'source_error' };
        if (x < 0.11) return { doc_id, verdict: 'drift_material', reason_code: 'updated_since_indexed' };
        return { doc_id, verdict: 'verified', reason_code: 'ok' };
      });
      const allowed = gate2.filter((g) => ALLOWED.has(g.verdict)).map((g) => g.doc_id);
      const answerable = allowed.filter((id) => relevant.includes(id));
      const req_id = `req_${sha(`seed:${day}:${k}:${sub}`).slice(0, 16)}`;
      const failed = rnd() < 0.018;
      if (failed) {
        const stage = rnd() < 0.5 ? 'seal' : 'compose';
        reqs.push({
          req_id, sub, channel, question: tpl.q, t, replay, status: 'failed', model: null,
          considered, gate2, released: [], cited: [], stage_ms: {}, latency_ms: null, dropped: 0,
          failure: { stage, error_code: stage === 'seal' ? 'ledger_unavailable' : 'internal_error' },
        });
        continue;
      }
      const refused = answerable.length === 0;
      let model: ModelInfo = EXTRACTIVE;
      if (!refused) {
        const m = rnd();
        model = m < 0.72 ? HY3 : m < 0.79 ? DEEPSEEK : EXTRACTIVE;
      }
      const released = refused ? [] : allowed;
      let cited = refused ? [] : [...answerable];
      if (cited.length > 1 && rnd() < 0.18) cited = cited.slice(0, -1);
      const dropped = !refused && model !== EXTRACTIVE && rnd() < 0.08 ? 1 + Math.floor(rnd() * 2) : 0;
      const stage_ms = timings(rnd, model, checked.length, refused);
      reqs.push({
        req_id, sub, channel, question: tpl.q, t, replay, status: refused ? 'refused' : 'released', model,
        considered, gate2, released, cited, stage_ms, latency_ms: sum(stage_ms), dropped, failure: null,
      });
    }
    // governance and platform activity for the day (not replay-labelled: real calls at seed time)
    const at = (h: number) => {
      const x = new Date(date);
      x.setUTCHours(h - 8, Math.floor(rnd() * 60), Math.floor(rnd() * 60), 0);
      return Math.min(x.getTime(), anchor - 30_000);
    };
    for (const p of PLATFORMS) for (let i = 0; i < 4; i += 1) gov.push({ t: at(8 + i * 3), kind: 'ingest.sync', sub: `connector:${p}` });
    for (let i = 0; i < (weekend ? 2 : 7); i += 1) gov.push({ t: at(9 + Math.floor(rnd() * 9)), kind: 'auth.login', sub: weighted() });
    for (let i = 0; i < (weekend ? 0 : 3); i += 1) gov.push({ t: at(10 + Math.floor(rnd() * 8)), kind: 'graph.viewed', sub: weighted() });
    for (let i = 0; i < (weekend ? 1 : 5); i += 1) gov.push({ t: at(10 + Math.floor(rnd() * 8)), kind: 'doc.viewed', sub: weighted() });
    for (let i = 0; i < (weekend ? 0 : 2); i += 1) gov.push({ t: at(11 + Math.floor(rnd() * 7)), kind: 'trace.viewed', sub: weighted() });
    if (!weekend) gov.push({ t: at(14), kind: 'map.viewed', sub: weighted() });
    if (!weekend) for (let i = 0; i < 2; i += 1) gov.push({ t: at(10 + Math.floor(rnd() * 8)), kind: 'audit.query', sub: 'maya.compliance' });
    if (rnd() < 0.3) gov.push({ t: at(12), kind: rnd() < 0.7 ? 'acl.changed' : 'membership.changed', sub: 'admin.it' });
  }
  // a handful of reveals, each naming the request it opened, with its reason
  const reasons = ['Customer complaint review, ticket CX-2291', 'Quarterly access review sample', 'Regulator information request, ref MAS-TRM-14', 'Incident follow-up for SEC-88'];
  reqs
    .filter((r) => r.status === 'released' || r.status === 'refused')
    .filter((_, i) => i % 61 === 7)
    .slice(0, 4)
    .forEach((r, i) => gov.push({ t: Math.min(r.t + 2 * DAY, anchor - 90_000), kind: 'audit.reveal', sub: 'maya.compliance', target: r.req_id, reason: reasons[i] }));
  g.__ibAnalytics = { anchor, reqs, gov };
  return g.__ibAnalytics;
}

const REAL_REASON: Record<string, string> = {
  'confluence.page_restricted': 'not_permitted',
  'jira.no_browse_permission': 'not_permitted',
  'slack.not_channel_member': 'not_member',
};

/** The shared store's request rows, as Reqs (stage timings: a deterministic split, as finals.ts). */
function fromStore(s: SharedStore | null): { reqs: Req[]; gov: Gov[] } {
  if (!s) return { reqs: [], gov: [] };
  const reqs: Req[] = [];
  const gov: Gov[] = [];
  for (const e of s.ledger) {
    const t = Date.parse(e.event_time);
    if (Number.isNaN(t)) continue;
    if (e.kind === 'request') {
      const rnd = mulberry32(parseInt(sha(e.req_id).slice(0, 8), 16));
      const status: Status = e.refused === null || e.refused === undefined ? 'in_progress' : e.refused ? 'refused' : 'released';
      const checked = (e.gate2 ?? []).length;
      const stage_ms = status === 'in_progress' ? {} : timings(rnd, e.model ?? null, checked, status === 'refused');
      const released = status === 'released' ? e.released ?? [] : [];
      reqs.push({
        req_id: e.req_id,
        sub: e.sub,
        channel: e.channel ?? 'ui',
        question: e.question ?? '',
        t,
        replay: !!e.synthetic_replay,
        status,
        model: e.model ?? null,
        considered: e.considered ?? [],
        // the fixture rows use descriptive codes; map them to the connectors' real reason codes
        gate2: (e.gate2 ?? []).map((g) => ({ ...g, reason_code: REAL_REASON[g.reason_code] ?? g.reason_code })),
        released,
        cited: released,
        stage_ms,
        latency_ms: status === 'in_progress' ? null : sum(stage_ms),
        dropped: 0,
        failure: null,
      });
    } else if (e.kind === 'audit.reveal') {
      const m = /^reveal (\S+): ([\s\S]*)$/.exec(e.question ?? '');
      gov.push({ t, kind: 'audit.reveal', sub: e.sub, target: m?.[1], reason: m?.[2] });
    } else if (e.kind) {
      gov.push({ t, kind: e.kind, sub: e.sub });
    }
  }
  return { reqs, gov };
}

/* ------------------------------------------------------------------------------------------------ */
/* the ledger                                                                                        */

interface Rec {
  seq: number;
  kind: string;
  hash: string;
  t: number;
  replay: boolean;
  req?: Req;
  gov?: Gov;
}

interface World {
  reqs: Req[];
  byId: Map<string, Req>;
  recs: Rec[];
  recsByReq: Map<string, Rec[]>;
  reveals: Gov[];
}

/** One time-ordered, hash-chained ledger over everything (rebuilt per call; about 2,000 records). */
function world(): World {
  const s = shared();
  const seed = seeded();
  const live = fromStore(s);
  const reqs = [...seed.reqs, ...live.reqs];
  const items: { t: number; kind: string; replay: boolean; req?: Req; gov?: Gov }[] = [];
  for (const r of reqs) {
    items.push({ t: r.t, kind: 'request.accepted', replay: r.replay, req: r });
    const st = r.stage_ms;
    const decidedAt = r.t + (st.accept ?? 3) + (st.gate1 ?? 20) + (st.gate2 ?? 150) + (st.seal ?? 6);
    if (r.status === 'failed') {
      if (r.failure?.stage === 'compose') items.push({ t: r.t + 260, kind: 'retrieval.decided', replay: r.replay, req: r });
      items.push({ t: r.t + 420, kind: 'request.failed', replay: r.replay, req: r });
      continue;
    }
    items.push({ t: decidedAt, kind: 'retrieval.decided', replay: r.replay, req: r });
    if (r.status !== 'in_progress') items.push({ t: r.t + (r.latency_ms ?? 0), kind: 'answer.released', replay: r.replay, req: r });
  }
  for (const g of [...seed.gov, ...live.gov]) items.push({ t: g.t, kind: g.kind, replay: false, gov: g });
  items.sort((a, b) => a.t - b.t || (a.req?.req_id ?? '').localeCompare(b.req?.req_id ?? ''));
  let prev = '0'.repeat(64);
  const recs: Rec[] = items.map((it, i) => {
    const hash = sha(`${prev}|${i + 1}|${it.kind}|${it.req?.req_id ?? it.gov?.sub ?? ''}|${it.t}`);
    prev = hash;
    return { seq: i + 1, kind: it.kind, hash, t: it.t, replay: it.replay, req: it.req, gov: it.gov };
  });
  const recsByReq = new Map<string, Rec[]>();
  for (const r of recs) if (r.req) (recsByReq.get(r.req.req_id) ?? recsByReq.set(r.req.req_id, []).get(r.req.req_id)!).push(r);
  return {
    reqs,
    byId: new Map(reqs.map((r) => [r.req_id, r])),
    recs,
    recsByReq,
    reveals: [...seed.gov, ...live.gov].filter((g) => g.kind === 'audit.reveal'),
  };
}

function ledgerQuery(sub: string, what: string) {
  const s = shared();
  if (!s) return;
  s.ledger.push({ req_id: `aud_an_${s.ledger.length}`, event_time: new Date().toISOString(), sub, kind: 'audit.query', question: what, channel: 'ui', considered: [], gate2: [], released: [], model: null, refused: null, synthetic_replay: false });
}

/* ------------------------------------------------------------------------------------------------ */
/* /audit/stats: the same aggregation as audit/stats.py                                               */

function pct(values: number[], p: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.max(0, Math.min(s.length - 1, Math.round(p * (s.length - 1))))];
}

function stats(w: World, days: number, includeReplay: boolean) {
  const now = Date.now();
  const since = now - days * DAY;
  const inc = (o: Record<string, number>, k: string, n = 1) => (o[k] = (o[k] ?? 0) + n);
  const kinds: Record<string, number> = {};
  const channels: Record<string, number> = {};
  const models: Record<string, number> = {};
  const verdicts: Record<string, number> = {};
  const reasons: Record<string, number> = {};
  const platforms: Record<string, Record<string, number>> = {};
  const people: Record<string, Record<string, number>> = {};
  const perDay: Record<string, Record<string, number>> = {};
  const stageVals: Record<string, number[]> = {};
  const lat: number[] = [];
  const t: Record<string, number> = {};
  const releasedBy = new Map<string, Set<string>>();
  const citedBy = new Map<string, Set<string>>();
  for (const r of w.recs) {
    if (r.t < since || r.t >= now) continue;
    if (r.replay && !includeReplay) continue;
    inc(kinds, r.kind);
    const day = new Date(r.t).toISOString().slice(0, 10);
    const q = r.req;
    if (r.kind === 'request.accepted' && q) {
      inc(t, 'requests');
      inc(channels, q.channel);
      inc((people[q.sub] ??= {}), 'asked');
      inc((perDay[day] ??= {}), 'asked');
    } else if (r.kind === 'retrieval.decided' && q) {
      inc(t, 'considered', q.considered.length);
      inc(t, 'released_docs', q.released.length);
      for (const d of q.considered) inc((platforms[platformOf(d)] ??= {}), 'considered');
      for (const d of q.released) inc((platforms[platformOf(d)] ??= {}), 'released');
      releasedBy.set(q.req_id, new Set(q.released));
      for (const g of q.gate2) {
        inc(verdicts, g.verdict);
        inc(reasons, g.reason_code);
        inc((platforms[platformOf(g.doc_id)] ??= {}), 'gate2_checked');
      }
    } else if (r.kind === 'answer.released' && q) {
      const refused = q.status === 'refused';
      const k = refused ? 'refused' : 'answered';
      inc(t, k);
      inc((people[q.sub] ??= {}), k);
      inc((perDay[day] ??= {}), k);
      if (q.model) inc(models, `${q.model.provider}/${q.model.model}`);
      if (q.model?.fallback_used) inc(t, 'fallback_used');
      inc(t, 'citations', q.cited.length);
      citedBy.set(q.req_id, new Set(q.cited));
      inc(t, 'dropped_sentences', q.dropped);
      if (typeof q.latency_ms === 'number') lat.push(q.latency_ms);
      for (const st of STAGES) if (typeof q.stage_ms[st] === 'number') (stageVals[st] ??= []).push(q.stage_ms[st]!);
    } else if (r.kind === 'request.failed') {
      inc(t, 'failed');
      inc((perDay[day] ??= {}), 'failed');
    } else if (r.kind === 'audit.reveal') inc(t, 'reveals');
    else if (r.kind === 'audit.query') inc(t, 'audit_queries');
    else if (r.kind === 'acl.changed' || r.kind === 'membership.changed' || r.kind === 'directory.changed') inc(t, 'access_changes');
    else if (r.kind === 'ingest.sync') inc(t, 'syncs');
    else if (r.kind === 'map.viewed') inc(t, 'map_views');
    else if (r.kind === 'graph.viewed') inc(t, 'graph_views');
    else if (r.kind === 'doc.viewed') inc(t, 'doc_views');
    else if (r.kind === 'trace.viewed') inc(t, 'trace_views');
    else if (r.kind === 'auth.login') inc(t, 'logins');
  }
  const n = (k: string) => t[k] ?? 0;
  const g2total = Object.values(verdicts).reduce((a, b) => a + b, 0);
  const allowed = Object.entries(verdicts).reduce((a, [k, v]) => a + (ALLOWED.has(k) ? v : 0), 0);
  let cited = 0;
  for (const [rid, c] of citedBy) {
    const rel = releasedBy.get(rid) ?? new Set();
    c.forEach((d) => (cited += rel.has(d) ? 1 : 0));
  }
  const finished = n('answered') + n('refused');
  const last = w.recs[w.recs.length - 1];
  return {
    window: { since: new Date(since).toISOString(), until: new Date(now).toISOString(), days },
    ledger: { events: w.recs.length, first_seq: w.recs.length ? 1 : null, last_seq: last?.seq ?? null, head: last?.hash ?? null },
    totals: {
      requests: n('requests'),
      answered: n('answered'),
      refused: n('refused'),
      failed: n('failed'),
      answer_rate: finished ? Math.round((n('answered') / finished) * 10000) / 10000 : null,
      citations: n('citations'),
      dropped_sentences: n('dropped_sentences'),
      fallback_used: n('fallback_used'),
      reveals: n('reveals'),
      audit_queries: n('audit_queries'),
      access_changes: n('access_changes'),
      syncs: n('syncs'),
      map_views: n('map_views'),
      logins: n('logins'),
      graph_views: n('graph_views'),
      doc_views: n('doc_views'),
      trace_views: n('trace_views'),
    },
    requests_flow: { asked: n('requests'), answered: n('answered'), refused: n('refused'), failed: n('failed') },
    documents_flow: {
      considered: n('considered'),
      gate2_checked: g2total,
      gate2_allowed: allowed,
      gate2_denied: g2total - allowed,
      released: n('released_docs'),
      cited,
    },
    gate2: { verdicts, reasons },
    latency_ms: {
      count: lat.length,
      p50: pct(lat, 0.5),
      p95: pct(lat, 0.95),
      max: lat.length ? Math.max(...lat) : null,
      mean: lat.length ? Math.round(lat.reduce((a, b) => a + b, 0) / lat.length) : null,
    },
    stages_ms: Object.fromEntries(STAGES.map((st) => [st, { p50: pct(stageVals[st] ?? [], 0.5), p95: pct(stageVals[st] ?? [], 0.95), count: (stageVals[st] ?? []).length }])),
    models,
    channels,
    platforms: Object.fromEntries([...PLATFORMS, 'other'].filter((p) => platforms[p]).map((p) => [p, platforms[p]])),
    people: Object.entries(people)
      .filter(([sub]) => sub)
      .map(([sub, c]): { sub: string; asked?: number } => ({ sub, ...c }))
      .sort((a, b) => (b.asked ?? 0) - (a.asked ?? 0) || a.sub.localeCompare(b.sub)),
    days: Object.keys(perDay)
      .sort()
      .map((day) => ({ day, ...perDay[day] })),
    kinds,
  };
}

/* ------------------------------------------------------------------------------------------------ */
/* /audit/journey and /ib/trace                                                                       */

const iso = (t: number) => new Date(t).toISOString();

function citationOf(r: Req, id: string) {
  const d = DOC_BY_ID.get(id);
  return { doc_id: id, version: d?.version ?? '1', content_checked_at: iso(r.t + 200), authz_checked_at: iso(r.t + 180), title: d?.title ?? null };
}

function journey(w: World, r: Req) {
  const g2 = new Map(r.gate2.map((g) => [g.doc_id, g]));
  const rel = new Set(r.released);
  const cit = new Set(r.cited);
  const recs = w.recsByReq.get(r.req_id) ?? [];
  const finished = r.status === 'released' || r.status === 'refused';
  return {
    req_id: r.req_id,
    sub: r.sub,
    channel: r.channel,
    question: r.question,
    question_sha256: sha(`q:${r.question}`),
    event_time: iso(r.t),
    recorded_at: iso(r.t + 1),
    synthetic_replay: r.replay,
    policy_version: POLICY_VERSION,
    directory_epoch: 7,
    gate1_policy_version: POLICY_VERSION,
    time_window: null,
    considered: r.considered.map((id) => {
      const d = DOC_BY_ID.get(id);
      const g = g2.get(id);
      return {
        doc_id: id,
        platform: d?.platform ?? platformOf(id),
        container: d?.container ?? null,
        container_name: d?.container_name ?? null,
        title: d?.title ?? null,
        verdict: g?.verdict ?? null,
        reason_code: g?.reason_code ?? null,
        decision: g ? (ALLOWED.has(g.verdict) ? 'allow' : 'deny') : null,
        considered: true,
        released: rel.has(id),
        cited: cit.has(id),
      };
    }),
    gate2: r.gate2,
    released: r.released.map((id) => ({ doc_id: id, title: DOC_BY_ID.get(id)?.title ?? null, container: DOC_BY_ID.get(id)?.container ?? null })),
    citations: r.cited.map((id) => citationOf(r, id)),
    model: finished ? r.model : null,
    prompt_version: finished ? PROMPT_VERSION : null,
    answer_ref: finished ? `ans_${r.req_id}` : null,
    answer_sha256: finished ? sha(`answer:${r.req_id}`) : null,
    refused: finished ? r.status === 'refused' : null,
    latency_ms: r.latency_ms,
    dropped_sentences_count: finished ? r.dropped : null,
    stage_ms: r.stage_ms,
    failure: r.failure,
    status: r.status,
    reveals: w.reveals
      .filter((g) => g.target === r.req_id)
      .map((g) => {
        const rec = w.recs.find((x) => x.gov === g);
        return { seq: rec?.seq ?? 0, by: g.sub, reason: g.reason ?? null, event_time: iso(g.t), recorded_at: iso(g.t) };
      }),
    events: recs.map((x) => ({ seq: x.seq, kind: x.kind, hash: x.hash, event_time: iso(x.t), recorded_at: iso(x.t) })),
  };
}

const TRACE_STAGES: [string, string][] = [
  ['accept', 'request.accepted'],
  ['gate1', 'retrieval.decided'],
  ['gate2', 'retrieval.decided'],
  ['seal', 'retrieval.decided'],
  ['compose', 'answer.released'],
  ['release', 'answer.released'],
];

/** The asker's receipt: coarse stages (ok when the sealed record exists), rounded latency, citations
 *  the asker can STILL open (no count of the rest), per-request ordinals (no global seq). */
function receipt(w: World, r: Req, state: MockState) {
  const recs = w.recsByReq.get(r.req_id) ?? [];
  const kinds = new Set(recs.map((x) => x.kind));
  const open = openable(r.sub, state);
  const finished = r.status === 'released' || r.status === 'refused';
  return {
    req_id: r.req_id,
    question: r.question,
    channel: r.channel,
    event_time: iso(r.t),
    synthetic_replay: r.replay,
    status: r.status,
    policy_version: POLICY_VERSION,
    model: finished ? r.model : null,
    prompt_version: finished ? PROMPT_VERSION : null,
    latency_ms_rounded: typeof r.latency_ms === 'number' ? Math.round(r.latency_ms / 100) * 100 : null,
    stages: TRACE_STAGES.map(([name, kind]) => ({ name, status: kinds.has(kind) ? 'ok' : 'skipped' })),
    dropped_sentences_count: finished ? r.dropped : null,
    citations: r.cited
      .filter((id) => open.has(id))
      .map((id) => {
        const d = DOC_BY_ID.get(id)!;
        return {
          doc_id: id,
          platform: d.platform,
          kind: { confluence: 'page', jira: 'issue', slack: 'thread', drive: 'file' }[d.platform],
          container: d.container,
          container_name: d.container_name,
          title: d.title,
          version: d.version,
          updated_at: d.updated_at,
          deep_link: d.deep_link,
          cited_version: d.version,
        };
      }),
    answer_sha256: finished ? sha(`answer:${r.req_id}`) : null,
    records: recs.map((x, i) => ({ ordinal: i + 1, kind: x.kind, hash: x.hash })),
  };
}

/* ------------------------------------------------------------------------------------------------ */
/* the handler                                                                                        */

export const handle: MockHandler = async ({ req, path, method, sub, roles }) => {
  if (method !== 'GET') return null;
  const compliance = roles.includes('compliance_officer');
  const p = req.nextUrl.searchParams;

  if (path === 'ib/status') {
    return json({
      // Not live unless the build explicitly asks for model labels (MOCK_MODEL_LABELS; rule 8).
      models: {
        provider: MOCK_MODEL_LABELS ? 'tencent-tokenhub' : null,
        chain: MOCK_MODEL_LABELS ? ['hy3', 'deepseek-v4.1-flash'] : [],
        active: MOCK_MODEL_LABELS,
        active_chain: MOCK_MODEL_LABELS ? ['hy3', 'deepseek-v4.1-flash'] : [],
        fallback: { provider: 'deterministic', model: 'extractive-v1' },
      },
      key_configured: MOCK_MODEL_LABELS,
      force_offline: false,
      ledger: { ok: true },
    });
  }

  if (path === 'ib/history') {
    const w = world();
    const items = w.reqs
      .filter((r) => r.sub === sub)
      .sort((a, b) => b.t - a.t)
      .slice(0, 50)
      .map((r) => ({ req_id: r.req_id, question: r.question, event_time: iso(r.t), channel: r.channel, synthetic_replay: r.replay }));
    return json({ items });
  }

  if (path.startsWith('ib/trace/')) {
    const id = decodeURIComponent(path.slice('ib/trace/'.length));
    const w = world();
    const r = w.byId.get(id);
    // another person's request and an unknown id: the same 404
    if (!r || r.sub !== sub) return notFound();
    return json(receipt(w, r, shared()?.state ?? BASE_STATE));
  }

  if (!path.startsWith('audit/')) return null;
  if (path !== 'audit/stats' && !path.startsWith('audit/journey/') && !(path === 'audit/events' && p.get('kind'))) return null;
  if (!compliance) return json({ detail: 'forbidden' }, 403);

  if (path === 'audit/stats') {
    const days = Number(p.get('days') ?? 30);
    const replay = p.get('replay');
    if (!Number.isInteger(days) || days < 1 || days > 3650) return json({ detail: 'invalid stats query: days must be 1..3650' }, 422);
    if (replay !== null && replay !== 'include' && replay !== 'exclude') return json({ detail: 'invalid stats query: replay must be include or exclude' }, 422);
    const out = stats(world(), days, replay !== 'exclude');
    ledgerQuery(sub, `stats days=${days}`);
    return json(out);
  }

  if (path.startsWith('audit/journey/')) {
    const w = world();
    const r = w.byId.get(decodeURIComponent(path.slice('audit/journey/'.length)));
    if (!r) return notFound();
    ledgerQuery(sub, `journey ${r.req_id}`);
    return json(journey(w, r));
  }

  // GET /audit/events?kind=request.accepted: ledger-shaped rows, most recent first
  const kind = p.get('kind');
  const limit = Math.max(1, Math.min(500, Number(p.get('limit') ?? 100) || 100));
  const w = world();
  const rows = w.recs
    .filter((x) => x.kind === kind && x.req)
    .sort((a, b) => b.t - a.t || b.seq - a.seq)
    .slice(0, limit)
    .map((x) => ({
      seq: x.seq,
      hash: x.hash,
      kind: x.kind,
      req_id: x.req!.req_id,
      sub: x.req!.sub,
      event_time: iso(x.t),
      recorded_at: iso(x.t),
      synthetic_replay: x.replay,
      event: { kind: x.kind, req_id: x.req!.req_id, sub: x.req!.sub, question: x.req!.question, channel: x.req!.channel },
      question: x.req!.question,
      channel: x.req!.channel,
    }));
  ledgerQuery(sub, `events kind=${kind}`);
  return json({ filter: { kind, limit }, events: rows });
};
