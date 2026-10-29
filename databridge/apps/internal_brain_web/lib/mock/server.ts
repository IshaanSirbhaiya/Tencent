// Mock IB API (NEXT_PUBLIC_IB_MOCK=1). Server-side only. Mirrors the real routes, status codes and
// response shapes closely enough that the UI cannot tell the difference — including role checks,
// the constant refusal, the constant scope note, and a hash-chained in-memory ledger for /audit.
import { createHash, randomUUID } from 'node:crypto';
import { NextResponse, type NextRequest } from 'next/server';
import type { AuditEvent, ModelInfo } from '../types';
import { ALLOW_VERDICTS } from '../types';
import {
  DOCS,
  type DocKey,
  type MockState,
  REFUSAL_TEXT,
  ROLES,
  SCENARIOS,
  SCOPE_NOTE,
  SEED_HISTORY,
  EXTRACTIVE,
  citation,
} from './fixtures';
import { mockDoc, mockGraph, mockJourney, mockStats, mockTrace } from './finals';
import { buildMap } from './map';

const COOKIE = 'ib_session';
const SLO_S = 300;

interface Store {
  state: MockState;
  ledger: AuditEvent[];
  answers: Map<string, string>;
  reveals: number;
  queries: number;
  syncAt: Record<string, number>;
}

const g = globalThis as unknown as { __ibMock?: Store };

function fresh(): Store {
  const now = Date.now();
  const ledger: AuditEvent[] = [];
  const answers = new Map<string, string>();
  SEED_HISTORY.forEach((h, i) => {
    const t = new Date(now - h.daysAgo * 86400_000);
    t.setUTCHours(h.hour - 8, 7 + i * 3, 0, 0);
    const req_id = `req_${(0x5e1d00 + i).toString(16)}${'a1b2c3d4'.slice(i % 4, (i % 4) + 4)}`;
    const ev = event(req_id, t.toISOString(), h.sub, h.channel ?? 'replay', h.q, h.docs, h.deny ?? [], !!h.refused, h.model, h.replay);
    ledger.push(ev);
    answers.set(req_id, h.refused ? REFUSAL_TEXT : `(Synthetic replay answer for ${req_id}, citing ${h.docs.map((d) => DOCS[d].title).join('; ')}.)`);
  });
  return {
    state: { runbookV8: false, jdoeRemoved: false, keysRestricted: false },
    ledger,
    answers,
    reveals: 0,
    queries: 0,
    syncAt: { confluence: now - 42_000, jira: now - 95_000, slack: now - 12_000, drive: now - 610_000 },
  };
}

function store(): Store {
  if (!g.__ibMock) g.__ibMock = fresh();
  return g.__ibMock;
}

function event(
  req_id: string,
  at: string,
  sub: string,
  channel: string,
  question: string,
  docs: DocKey[],
  deny: { doc: DocKey; reason: string }[],
  refused: boolean,
  model: ModelInfo,
  replay: boolean,
): AuditEvent {
  const considered = [...docs, ...deny.map((d) => d.doc)];
  return {
    req_id,
    event_time: at,
    sub,
    channel,
    question,
    kind: 'request',
    considered: considered.map((k) => DOCS[k].doc_id),
    considered_meta: considered.map((k) => ({ doc_id: DOCS[k].doc_id, platform: DOCS[k].platform, container: DOCS[k].container, title: DOCS[k].title })),
    gate2: [
      ...docs.map((k) => ({ doc_id: DOCS[k].doc_id, verdict: 'verified', reason_code: 'ok' })),
      ...deny.map((d) => ({ doc_id: DOCS[d.doc].doc_id, verdict: 'forbidden', reason_code: d.reason })),
    ],
    released: refused ? [] : docs.map((k) => DOCS[k].doc_id),
    model,
    refused,
    synthetic_replay: replay,
    latency_ms: 900 + ((req_id.charCodeAt(req_id.length - 1) * 37) % 1400),
  };
}

const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const err = (status: number, detail: string) => json({ detail }, status);

function principal(v: string | undefined): string | null {
  if (!v || !v.startsWith('mock.')) return null;
  const sub = v.slice(5);
  return ROLES[sub] ? sub : null;
}

async function body(req: NextRequest): Promise<Record<string, unknown>> {
  try {
    return (await req.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function head(ledger: AuditEvent[]): string {
  let h = '0'.repeat(64);
  for (const e of ledger) h = createHash('sha256').update(h + JSON.stringify(e)).digest('hex');
  return h;
}

function filterEvents(all: AuditEvent[], f: Record<string, string | undefined>): AuditEvent[] {
  return all
    .filter((e) => !f.user || e.sub === f.user)
    .filter((e) => !f.container || (e.considered_meta ?? []).some((m) => m.container === f.container || (m.container ?? '').toLowerCase() === f.container!.toLowerCase()))
    .filter((e) => !f.doc || (e.considered ?? []).includes(f.doc) || (e.considered_meta ?? []).some((m) => (m.title ?? '').toLowerCase().includes(f.doc!.toLowerCase())))
    .filter((e) => !f.since || e.event_time >= new Date(f.since).toISOString())
    .filter((e) => !f.until || e.event_time <= new Date(f.until).toISOString())
    .filter((e) => {
      if (!f.decision) return true;
      const g2 = e.gate2 ?? [];
      return f.decision === 'deny' ? g2.some((d) => !ALLOW_VERDICTS.has(d.verdict)) : g2.some((d) => ALLOW_VERDICTS.has(d.verdict));
    })
    .sort((a, b) => (a.event_time < b.event_time ? 1 : -1));
}

/** Deterministic NL → closed typed filter (mirrors the IB's "deterministic parser first"). */
function parseAuditQuestion(q: string): Record<string, string> {
  const f: Record<string, string> = {};
  const lower = q.toLowerCase();
  for (const id of Object.keys(ROLES)) if (lower.includes(id)) f.user = id;
  const container = lower.match(/([a-z0-9-]+) space/);
  if (container && container[1] !== 'the') f.container = container[1];
  const now = Date.now();
  const days = lower.match(/last (\d+) days/);
  if (days) f.since = new Date(now - Number(days[1]) * 86400_000).toISOString();
  else if (lower.includes('last week')) f.since = new Date(now - 7 * 86400_000).toISOString();
  else if (lower.includes('yesterday')) f.since = new Date(now - 86400_000).toISOString();
  if (/\bden(ied|y|ials?)\b/.test(lower)) f.decision = 'deny';
  if (/breach report/.test(lower)) f.doc = DOCS.breach.doc_id;
  return f;
}

/** `session` is the value of whichever session cookie the route picked for this request's slot. */
export async function handleMock(req: NextRequest, path: string, session?: string): Promise<NextResponse> {
  const s = store();
  const method = req.method.toUpperCase();

  if (path === 'health') return json({ ok: true, mock: true });

  if (path === 'auth/demo-login' && method === 'POST') {
    const persona = String((await body(req)).persona ?? '');
    const u = ROLES[persona];
    if (!u) return err(400, 'unknown persona');
    const res = json({ sub: persona, roles: u.roles, name: u.name });
    res.cookies.set(COOKIE, `mock.${persona}`, { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 8 * 3600 });
    return res;
  }

  const sub = principal(session);
  if (!sub) return err(401, 'authentication required');
  const me = ROLES[sub];

  if (path === 'auth/logout' && method === 'POST') {
    const res = json({ ok: true });
    res.cookies.delete(COOKIE);
    return res;
  }
  if (path === 'auth/whoami') return json({ sub, name: me.name, roles: me.roles, employment: me.employment });

  if (path === 'ib/ask' && method === 'POST') {
    const question = String((await body(req)).question ?? '').trim();
    if (!question || question.length > 2000) return err(422, 'invalid question');
    if (/\[mock:503\]/i.test(question)) return json({ error: 'temporarily unavailable' }, 503);
    await new Promise((r) => setTimeout(r, 350));
    const now = new Date();
    const scenario = SCENARIOS.find((sc) => sc.match.test(question));
    const out = scenario?.answer(sub, s.state) ?? null;
    const request_id = `req_${randomUUID().replace(/-/g, '').slice(0, 16)}`;
    let answer = REFUSAL_TEXT;
    let citations: ReturnType<typeof citation>[] = [];
    let model: ModelInfo = EXTRACTIVE;
    if (out) {
      answer = out.text;
      model = out.model;
      citations = out.cites.map((k, i) => {
        const live = out.liveRead?.includes(k) ?? false;
        const v8 = k === 'runbook' && s.state.runbookV8;
        return citation(`C${i + 1}`, DOCS[k], now, { liveRead: live, version: v8 ? '8' : undefined, updated: v8 ? new Date(now.getTime() - 60_000).toISOString() : undefined });
      });
    }
    s.ledger.push(event(request_id, now.toISOString(), sub, 'ui', question, out?.cites ?? [], out?.denied ?? [], !out, model, false));
    s.answers.set(request_id, answer);
    return json({ request_id, answer, citations, scope_note: SCOPE_NOTE, model, as_of: now.toISOString() });
  }

  if (path === 'ib/history') {
    const items = s.ledger
      .filter((e) => e.sub === sub && !e.synthetic_replay)
      .sort((a, b) => (a.event_time < b.event_time ? 1 : -1))
      .slice(0, 50)
      .map((e) => ({ req_id: e.req_id, question: e.question ?? '', event_time: e.event_time }));
    return json({ items });
  }

  if (path === 'ib/map') {
    // The real route confirms every item live with its source; simulate that wait.
    await new Promise((r) => setTimeout(r, 1500));
    return json(buildMap(sub, s.state, SCOPE_NOTE));
  }

  if (path === 'ib/sources') {
    const now = Date.now();
    return json({
      sources: (['confluence', 'jira', 'slack', 'drive'] as const).map((p) => {
        const at = s.syncAt[p];
        const lag = Math.round((now - at) / 1000);
        return { platform: p, last_sync_at: new Date(at).toISOString(), last_event_at: new Date(at).toISOString(), slo_s: SLO_S, state: 'webhook', within_slo: lag <= SLO_S };
      }),
    });
  }

  if (path.startsWith('ib/trace/')) {
    const t = mockTrace(s.ledger, sub, decodeURIComponent(path.slice('ib/trace/'.length)));
    return t ? json(t) : err(404, 'not found');
  }

  if (path === 'ib/graph') {
    const p = req.nextUrl.searchParams;
    const limit = p.get('limit');
    const g = mockGraph(sub, s.state, { focus: p.get('focus'), q: p.get('q'), limit: limit ? Number(limit) : undefined });
    return g ? json(g) : err(404, 'not found');
  }

  if (path.startsWith('ib/doc/')) {
    const d = mockDoc(sub, s.state, decodeURIComponent(path.slice('ib/doc/'.length)), s.syncAt);
    return d ? json(d) : err(404, 'not found');
  }

  if (path.startsWith('audit/')) {
    if (!me.roles.includes('compliance_officer')) return err(403, 'forbidden');
    if (path === 'audit/stats') {
      const p = req.nextUrl.searchParams;
      const days = Number(p.get('days') ?? 30);
      const replay = p.get('replay');
      if (!Number.isInteger(days) || days < 1 || days > 3650) return err(422, 'invalid stats query: days must be 1..3650');
      if (replay !== null && replay !== 'include' && replay !== 'exclude') return err(422, 'invalid stats query: replay must be include or exclude');
      return json(mockStats(s.ledger, days, replay !== 'exclude'));
    }
    if (path.startsWith('audit/journey/')) {
      const j = mockJourney(s.ledger, decodeURIComponent(path.slice('audit/journey/'.length)));
      return j ? json(j) : err(404, 'no such request');
    }
    const ledgerQuery = (filter: Record<string, unknown>, n: number) => {
      s.queries += 1;
      s.ledger.push({ req_id: `aud_${s.queries}`, event_time: new Date().toISOString(), sub, kind: 'audit.query', question: `filter ${JSON.stringify(filter)} → ${n} rows`, channel: 'ui', considered: [], gate2: [], released: [], model: null, refused: null, synthetic_replay: false });
    };
    if (path === 'audit/events') {
      const p = req.nextUrl.searchParams;
      const f = Object.fromEntries(['user', 'container', 'doc', 'since', 'until', 'decision'].map((k) => [k, p.get(k) ?? undefined]));
      const events = filterEvents(s.ledger.filter((e) => e.kind === 'request'), f);
      ledgerQuery(f, events.length);
      return json({ events });
    }
    if (path === 'audit/ask' && method === 'POST') {
      const q = String((await body(req)).question ?? '');
      const filter = parseAuditQuestion(q);
      const events = filterEvents(s.ledger.filter((e) => e.kind === 'request'), filter);
      ledgerQuery(filter, events.length);
      return json({ filter, events });
    }
    if (path === 'audit/reveal' && method === 'POST') {
      const b = await body(req);
      const req_id = String(b.req_id ?? '');
      const reason = String(b.reason ?? '').trim();
      if (reason.length < 10) return err(422, 'a reason of at least 10 characters is required');
      const answer = s.answers.get(req_id);
      if (answer === undefined) return err(404, 'not found');
      s.reveals += 1;
      s.ledger.push({ req_id: `rev_${s.reveals}`, event_time: new Date().toISOString(), sub, kind: 'audit.reveal', question: `reveal ${req_id}: ${reason}`, channel: 'ui', considered: [], gate2: [], released: [], model: null, refused: null, synthetic_replay: false });
      return json({ req_id, answer });
    }
    if (path === 'audit/verify') {
      return json({ ok: true, records: s.ledger.length * 3, head: head(s.ledger), external_head_match: true, problems: [] });
    }
    return err(404, 'not found');
  }

  if (path.startsWith('sim/') && method === 'POST') {
    if (!me.roles.includes('admin_it')) return err(403, 'forbidden');
    const action = path.slice(4);
    const now = Date.now();
    if (action === 'runbook-update') {
      s.state.runbookV8 = true;
      s.syncAt.confluence = now;
      return json({ ok: true, action: '/admin/confluence/pages/cf-1042/edit', at: new Date(now).toISOString() });
    }
    if (action === 'revoke-channel') {
      s.state.jdoeRemoved = true;
      s.syncAt.slack = now;
      return json({ ok: true, action: '/admin/slack/channels/C0DBMIG/members/remove', at: new Date(now).toISOString() });
    }
    if (action === 'restrict-page') {
      s.state.keysRestricted = true;
      s.syncAt.confluence = now;
      return json({ ok: true, action: '/admin/confluence/pages/cf-1077/restrict', at: new Date(now).toISOString() });
    }
    if (action === 'reset') {
      const keep = s.ledger;
      g.__ibMock = { ...fresh(), ledger: keep, answers: s.answers };
      return json({ ok: true, action: '/admin/reset', at: new Date(now).toISOString() });
    }
    return err(404, 'not found');
  }

  return err(404, 'not found');
}
