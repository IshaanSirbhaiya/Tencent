// Mock handlers for "Act on this answer" (NEXT_PUBLIC_IB_MOCK=1). Server-side only. Mirrors
// databridge_ib/actions.py and the routes in app.py:
//
//   POST ib/act/draft            {req_id, kind}  → a draft from the cited sentences of MY answer
//   POST ib/act/ticket/propose   {req_id}        → a proposal in a project I may create issues in
//   POST ib/act/ticket/confirm   {proposal_id, summary?, project?} → the created (mock) issue
//   POST ib/act/ticket/cancel    {proposal_id}
//
// One 404 for someone else's, unknown and refused requests; one polite 409 when a ticket can't be
// made. Action records join the request's sealed chain: GET audit/journey/{req} and
// GET ib/trace/{req} are answered by the analytics mock and then extended with them here.
// Synthetic data only.
import { createHash, randomUUID } from 'node:crypto';
import { NextResponse } from 'next/server';
import type { AuditEvent } from '../../types';
import { DOCS, ROLES, SCOPE_NOTE, type MockDoc } from '../fixtures';
import { handle as analytics } from './analytics';
import type { MockCtx, MockHandler } from './types';
import { extraDocMeta } from './user';

const sha = (s: string) => createHash('sha256').update(s).digest('hex');
const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const notFound = () => json({ detail: 'not found' }, 404);
const UNAVAILABLE = "A follow-up ticket can't be created from this answer. Nothing was changed.";
const ID_RE = /^[A-Za-z0-9_-]{1,200}$/;
const SUMMARY_MAX = 200;

/** Who may create issues in which mock Jira project (the live check of the real service). */
const CREATE: Record<string, string[]> = {
  jdoe: ['DBMIG', 'PAY', 'AUTH'],
  'priya.sre': ['DBMIG'],
  'sara.sec': ['SEC'],
};
const PROJECT_NAME: Record<string, string> = { DBMIG: 'Database Migration', PAY: 'Payments', AUTH: 'Auth Service', SEC: 'Security' };

interface Rec {
  kind: string;
  hash: string;
  t: number;
}
interface Proposal {
  id: string;
  sub: string;
  req: string;
  project: string;
  options: string[];
  summary: string;
  description: string;
  used: boolean;
}
interface ActStore {
  recs: Map<string, Rec[]>;
  proposals: Map<string, Proposal>;
  next: Record<string, number>;
}
const g = globalThis as unknown as { __ibAct?: ActStore; __ibMock?: { ledger: AuditEvent[]; answers: Map<string, string> } };
function act(): ActStore {
  if (!g.__ibAct) g.__ibAct = { recs: new Map(), proposals: new Map(), next: {} };
  return g.__ibAct;
}
function record(reqId: string, kind: string) {
  const s = act();
  const list = s.recs.get(reqId) ?? [];
  const prev = list.length ? list[list.length - 1].hash : sha(`req:${reqId}`);
  list.push({ kind, hash: sha(prev + kind + list.length), t: Date.now() });
  s.recs.set(reqId, list);
}

async function bodyOf(ctx: MockCtx): Promise<Record<string, unknown> | null> {
  try {
    const b = (await ctx.req.clone().json()) as unknown;
    return b && typeof b === 'object' && !Array.isArray(b) ? (b as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
const onlyKeys = (b: Record<string, unknown>, keys: string[]) => Object.keys(b).every((k) => keys.includes(k));

function meta(docId: string): MockDoc | null {
  return Object.values(DOCS).find((d) => d.doc_id === docId) ?? extraDocMeta(docId);
}

/* ---------------------------------------------------------------- the action context */

interface Fact {
  text: string;
  tags: string[];
}
const TAG = /\[C\d+\]/g;
function splitFacts(text: string): Fact[] {
  // move trailing tags inside the sentence: "S. [C1]" → "S [C1]."
  const norm = text.replace(/([.!?])\s*((?:\[C\d+\]\s*)+)/g, (_m, p: string, tags: string) => ` ${tags.trim()}${p} `).trim();
  const out: Fact[] = [];
  let buf: string[] = [];
  for (const part of norm.split(/(?<=[.!?])\s+/)) {
    buf.push(part);
    if (TAG.test(part)) {
      TAG.lastIndex = 0;
      const seg = buf.join(' ');
      buf = [];
      const tags = Array.from(new Set((seg.match(TAG) ?? []).map((t) => t.slice(1, -1))));
      const clean = seg.replace(TAG, '').replace(/\s+([.,;:!?])/g, '$1').replace(/\s+/g, ' ').trim();
      if (clean) out.push({ text: clean, tags });
    }
    TAG.lastIndex = 0;
  }
  return out;
}
function scriptLines(facts: Fact[]): Fact[] {
  const out: Fact[] = [];
  for (const f of facts) {
    const m = f.text.match(/(?:^|(?<=[.!?])\s+)(?:please\s+)?(?:tell|inform|advise)\s+the\s+customer\s+(?:only\s+)?that\s+(.+?)[.!]?$/i);
    if (!m) continue;
    for (const clause of m[1].split(/,\s*(?:and\s+)?that\s+|\s+and\s+that\s+/i)) {
      let s = clause.replace(/^[\s,.;]+|[\s,.;]+$/g, '');
      s = s.replace(/\btheir\b/gi, 'your').replace(/\bthem\b/gi, 'you').replace(/\bthey\b/gi, 'you').replace(/\bthemselves\b/gi, 'yourself');
      if (s) out.push({ text: `${s[0].toUpperCase()}${s.slice(1)}.`, tags: f.tags });
    }
  }
  return out;
}

interface Ctx {
  req: AuditEvent;
  facts: Fact[];
  cites: Map<string, MockDoc>;
}
function context(sub: string, reqId: unknown): Ctx | null {
  if (typeof reqId !== 'string' || !ID_RE.test(reqId)) return null;
  const s = g.__ibMock;
  if (!s) return null;
  const e = s.ledger.find((x) => x.req_id === reqId && x.kind === 'request');
  // another person's request, an unknown id and a refused answer: the same "not found"
  if (!e || e.sub !== sub || e.refused) return null;
  const answer = s.answers.get(reqId) ?? '';
  const cites = new Map<string, MockDoc>();
  (e.released ?? []).forEach((id, i) => {
    const m = meta(id);
    if (m) cites.set(`C${i + 1}`, m);
  });
  const facts = splitFacts(answer).filter((f) => f.tags.length && f.tags.every((t) => cites.has(t)));
  return facts.length ? { req: e, facts: facts.slice(0, 6), cites } : null;
}
function citationsOf(ctx: Ctx, tags: string[]) {
  return Array.from(new Set(tags))
    .filter((t) => ctx.cites.has(t))
    .map((t) => {
      const d = ctx.cites.get(t)!;
      const kind = { confluence: 'page', jira: 'issue', slack: 'thread', drive: 'file' }[d.platform];
      return { tag: t, doc_id: d.doc_id, platform: d.platform, kind, container: d.container, container_name: d.container_name, title: d.title, version: d.version, updated_at: d.updated_at, deep_link: d.deep_link };
    });
}

/* ---------------------------------------------------------------- handlers */

function draft(sub: string, b: Record<string, unknown>) {
  const kind = b.kind === undefined ? 'reply' : b.kind;
  if (!onlyKeys(b, ['req_id', 'kind']) || (kind !== 'reply' && kind !== 'message')) return json({ detail: 'invalid' }, 422);
  const ctx = context(sub, b.req_id);
  if (!ctx) return notFound();
  const script = kind === 'reply' ? scriptLines(ctx.facts) : [];
  const body = script.length ? script : ctx.facts;
  const model = script.length
    ? { provider: 'deterministic', model: 'approved-script-v1', fallback_used: false }
    : { provider: 'deterministic', model: 'draft-template-v1', fallback_used: false };
  const greeting = kind === 'reply' ? 'Hello,' : 'Hi,';
  const opener = kind === 'reply' ? 'Thank you for getting in touch.' : 'Here is what our sources say.';
  const name = ROLES[sub]?.name ?? sub;
  const closing = kind === 'reply' ? `If you have any other questions, please let me know.\n\nKind regards,\n${name}` : `Thanks,\n${name}`;
  const text = [greeting, [opener, ...body.map((f) => f.text)].join(' '), closing].join('\n\n');
  const action_id = randomUUID().replace(/-/g, '');
  record(ctx.req.req_id, 'action.proposed');
  return json({
    action_id,
    kind,
    status: 'draft',
    label: 'Draft for your review',
    req_id: ctx.req.req_id,
    greeting,
    opener,
    body: body.map((f) => ({ text: f.text, tags: f.tags })),
    closing,
    text,
    citations: citationsOf(ctx, body.flatMap((f) => f.tags)),
    model,
    prompt_version: 'ib-draft-2026-10-30.1',
    sent: false,
    scope_note: SCOPE_NOTE,
    as_of: new Date().toISOString(),
  });
}

function propose(sub: string, b: Record<string, unknown>) {
  if (!onlyKeys(b, ['req_id'])) return json({ detail: 'invalid' }, 422);
  const ctx = context(sub, b.req_id);
  if (!ctx) return notFound();
  const allowed = CREATE[sub] ?? [];
  if (!allowed.length) {
    record(ctx.req.req_id, 'action.failed');
    return json({ error: 'unavailable', message: UNAVAILABLE }, 409);
  }
  const cited = Array.from(ctx.cites.values())
    .filter((d) => d.platform === 'jira')
    .map((d) => d.container);
  const options = Array.from(new Set([...cited, ...[...allowed].sort()])).filter((k) => allowed.includes(k));
  const citations = citationsOf(ctx, ctx.facts.flatMap((f) => f.tags));
  const links = Array.from(new Set(citations.map((c) => c.deep_link)));
  const q = (ctx.req.question ?? '').replace(/\s+/g, ' ').trim();
  const full = q ? `Follow-up: ${q}` : 'Follow-up';
  const summary = full.length <= 120 ? full : `${full.slice(0, 119).trimEnd()}…`;
  const description =
    `Follow-up created from an Internal Brain answer (request ${ctx.req.req_id}).\n\n` +
    'Sources (each link opens only for people the source system allows):\n\n' +
    links.map((u, i) => `[${i + 1}] ${u}`).join('\n');
  const id = randomUUID().replace(/-/g, '');
  act().proposals.set(id, { id, sub, req: ctx.req.req_id, project: options[0], options, summary, description, used: false });
  record(ctx.req.req_id, 'action.proposed');
  return json({
    proposal_id: id,
    action_id: id,
    kind: 'ticket',
    status: 'proposed',
    req_id: ctx.req.req_id,
    project: { key: options[0], name: PROJECT_NAME[options[0]] ?? options[0] },
    projects: options.map((k) => ({ key: k, name: PROJECT_NAME[k] ?? k })),
    issue_type: 'Task',
    summary,
    summary_max: SUMMARY_MAX,
    description,
    citations,
    expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    scope_note: SCOPE_NOTE,
    as_of: new Date().toISOString(),
  });
}

function take(sub: string, id: unknown): Proposal | null {
  if (typeof id !== 'string' || !ID_RE.test(id)) return null;
  const p = act().proposals.get(id);
  if (!p || p.sub !== sub || p.used) return null;
  p.used = true;
  return p;
}

function confirm(sub: string, b: Record<string, unknown>) {
  if (!onlyKeys(b, ['proposal_id', 'summary', 'project'])) return json({ detail: 'invalid' }, 422);
  const p = take(sub, b.proposal_id);
  if (!p) return notFound();
  const summary = typeof b.summary === 'string' ? b.summary.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim() : p.summary;
  const project = typeof b.project === 'string' ? b.project : p.project;
  if (!summary || summary.length > SUMMARY_MAX || !p.options.includes(project)) {
    p.used = false;
    return json({ detail: 'invalid' }, 422);
  }
  record(p.req, 'action.confirmed');
  const s = act();
  s.next[project] = (s.next[project] ?? 900) + 1;
  const key = `${project}-${s.next[project]}`;
  record(p.req, 'action.executed');
  return json({
    status: 'created',
    action_id: p.id,
    req_id: p.req,
    project: { key: project, name: PROJECT_NAME[project] ?? project },
    summary,
    issue: { key, url: `https://companya.atlassian.net/browse/${key}`, doc_id: `jira:${key}` },
    as_of: new Date().toISOString(),
  });
}

function cancel(sub: string, b: Record<string, unknown>) {
  if (!onlyKeys(b, ['proposal_id'])) return json({ detail: 'invalid' }, 422);
  const p = take(sub, b.proposal_id);
  if (!p) return notFound();
  record(p.req, 'action.rejected');
  return json({ status: 'cancelled', action_id: p.id });
}

/** The analytics mock's journey / receipt for a request, plus this request's action records. */
async function withActions(ctx: MockCtx, reqId: string, receipt: boolean): Promise<NextResponse | null> {
  const recs = act().recs.get(reqId);
  if (!recs?.length) return null;
  const res = await analytics(ctx);
  if (!res || res.status !== 200) return res;
  const body = (await res.json()) as { events?: unknown[]; records?: { ordinal: number; kind: string; hash: string }[] };
  if (receipt && Array.isArray(body.records)) {
    const n = body.records.length;
    body.records = [...body.records, ...recs.map((r, i) => ({ ordinal: n + i + 1, kind: r.kind, hash: r.hash }))];
  } else if (Array.isArray(body.events)) {
    const last = body.events[body.events.length - 1] as { seq?: number } | undefined;
    const base = (last?.seq ?? 0) + 1;
    body.events = [
      ...body.events,
      ...recs.map((r, i) => ({ seq: base + i, kind: r.kind, hash: r.hash, event_time: new Date(r.t).toISOString(), recorded_at: new Date(r.t).toISOString() })),
    ];
  }
  return json(body);
}

export const handle: MockHandler = async (ctx) => {
  const { path, method, sub, roles } = ctx;
  if (method === 'GET' && path.startsWith('audit/journey/') && roles.includes('compliance_officer')) {
    return withActions(ctx, decodeURIComponent(path.slice('audit/journey/'.length)), false);
  }
  if (method === 'GET' && path.startsWith('ib/trace/')) {
    return withActions(ctx, decodeURIComponent(path.slice('ib/trace/'.length)), true);
  }
  if (method !== 'POST' || !path.startsWith('ib/act/')) return null;
  if (!ROLES[sub]) return json({ detail: 'authentication required' }, 401);
  const b = await bodyOf(ctx);
  if (!b) return json({ detail: 'invalid' }, 422);
  if (path === 'ib/act/draft') return draft(sub, b);
  if (path === 'ib/act/ticket/propose') return propose(sub, b);
  if (path === 'ib/act/ticket/confirm') return confirm(sub, b);
  if (path === 'ib/act/ticket/cancel') return cancel(sub, b);
  return notFound();
};
