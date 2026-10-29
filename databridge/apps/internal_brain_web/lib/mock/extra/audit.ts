// Mock handlers owned by the "audit" page agent (Audit console, Simulator, Trust centre).
//
// - GET  audit/events, POST audit/ask: the REAL backend's shape (one row per sealed ledger event:
//   seq, hash, kind, req_id, event), so the console's own grouping (lib/api.ts `groupAuditRows`) is
//   exercised in mock mode too. Record seq and hashes use the same scheme as lib/mock/finals.ts, so
//   a row's record hash here equals the hash the journey page shows for that request.
// - POST audit/tamper-demo (backend_v1.md v2, B11): the scenarios of
//   databridge/scripts/tamper_demo.py run on a COPY of the mock ledger. Problem strings are the
//   ledger's own wording (captured from a real run of the script on 30 Oct). The mock ledger itself
//   is never edited; the call is ledgered like any other audit call.
//
// Everything reads the shared in-memory mock store (lib/mock/server.ts keeps it on globalThis).
import { createHash } from 'node:crypto';
import { NextResponse } from 'next/server';
import type { AuditEvent, TamperDemoResult, TamperScenario } from '../../types';
import { ALLOW_VERDICTS } from '../../types';
import { DOCS, ROLES } from '../fixtures';
import type { MockHandler } from './types';

interface Store {
  ledger: AuditEvent[];
  queries: number;
}

const sha = (s: string) => createHash('sha256').update(s).digest('hex');
const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const err = (status: number, detail: string) => json({ detail }, status);
const KINDS = ['request.accepted', 'retrieval.decided', 'answer.released'] as const;

function store(): Store | null {
  return (globalThis as unknown as { __ibMock?: Store }).__ibMock ?? null;
}

/** Same scheme as lib/mock/finals.ts `records()`: seq 1000 + index*3 + k, a per-request hash chain. */
function sealed(e: AuditEvent, index: number) {
  const kinds = e.refused === null ? KINDS.slice(0, 2) : KINDS;
  let prev = sha(`genesis:${index}`);
  return kinds.map((kind, k) => {
    const hash = sha(prev + e.req_id + kind);
    prev = hash;
    return { seq: 1000 + index * 3 + k, kind, hash, recorded_at: new Date(Date.parse(e.event_time) + k * 400).toISOString() };
  });
}

/** One request → its three ledger rows, shaped like audit/query.py `_row`. */
function ledgerRows(e: AuditEvent, index: number) {
  const base = { req_id: e.req_id, sub: e.sub, event_time: e.event_time, synthetic_replay: !!e.synthetic_replay };
  return sealed(e, index).map((r) => {
    const common = { seq: r.seq, hash: r.hash, kind: r.kind, recorded_at: r.recorded_at, ...base };
    if (r.kind === 'request.accepted')
      return { ...common, question: e.question, channel: e.channel, event: { kind: r.kind, ...base, channel: e.channel, question: e.question } };
    if (r.kind === 'retrieval.decided')
      return { ...common, event: { kind: r.kind, ...base, considered: e.considered ?? [], considered_meta: e.considered_meta ?? [], gate2: e.gate2 ?? [], released: e.released ?? [] } };
    return { ...common, event: { kind: r.kind, ...base, model: e.model, refused: e.refused, latency_ms: e.latency_ms } };
  });
}

function filterRequests(ledger: AuditEvent[], f: Record<string, string | undefined>) {
  return ledger
    .map((e, index) => ({ e, index }))
    .filter(({ e }) => e.kind === 'request')
    .filter(({ e }) => !f.user || e.sub === f.user)
    .filter(({ e }) => !f.container || (e.considered_meta ?? []).some((m) => (m.container ?? '').toLowerCase() === f.container!.toLowerCase()))
    .filter(({ e }) => !f.doc || (e.considered ?? []).includes(f.doc) || (e.considered_meta ?? []).some((m) => (m.title ?? '').toLowerCase().includes(f.doc!.toLowerCase())))
    .filter(({ e }) => !f.since || e.event_time >= new Date(f.since).toISOString())
    .filter(({ e }) => !f.until || e.event_time <= new Date(f.until).toISOString())
    .filter(({ e }) => {
      if (!f.decision) return true;
      const g2 = e.gate2 ?? [];
      return f.decision === 'deny' ? g2.some((d) => !ALLOW_VERDICTS.has(d.verdict)) : g2.some((d) => ALLOW_VERDICTS.has(d.verdict));
    })
    .sort((a, b) => (a.e.event_time < b.e.event_time ? 1 : -1));
}

/** Deterministic NL → closed typed filter (mirrors the built-in mock and the IB's parser-first rule). */
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

function ledgerQuery(s: Store, sub: string, question: string) {
  s.queries += 1;
  s.ledger.push({ req_id: `aud_${s.queries}`, event_time: new Date().toISOString(), sub, kind: 'audit.query', question, channel: 'ui', considered: [], gate2: [], released: [], model: null, refused: null, synthetic_replay: false });
}

async function body(req: Request): Promise<Record<string, unknown>> {
  try {
    return (await req.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** The scenarios of scripts/tamper_demo.py on a copy of `n` records whose external head was published
 *  at `pub`, in the /audit/tamper-demo shape (backend_v1.md v2). */
export function tamperScenarios(n: number, pub: number): TamperScenario[] {
  const k = Math.max(1, Math.min(n - 1, Math.floor(pub / 2) || Math.floor(n / 2)));
  const back = Math.max(1, pub - 3);
  const at = new Date(Date.now() - 6 * 3600_000).toISOString().replace('Z', '+00:00');
  const flagged = (x: Omit<TamperScenario, 'expected' | 'expected_detected' | 'detected' | 'ok' | 'verify_ok'>): TamperScenario => ({
    ...x,
    expected: 'flagged',
    expected_detected: true,
    detected: true,
    ok: true,
    verify_ok: false,
  });
  return [
    {
      id: 'baseline',
      title: 'Untouched copy',
      what_we_did: 'Copied the log and changed nothing.',
      expected: 'not flagged',
      expected_detected: false,
      detected: false,
      ok: true,
      verify_ok: true,
      internal_ok: true,
      external_head_match: true,
      records: n,
      problems: [],
    },
    flagged({
      id: 'edit-record',
      title: 'Edit one old record',
      what_we_did: `Changed who made record ${k} (ib_event.sub) without re-sealing it.`,
      first_bad_seq: k,
      problems: [`record ${k}: its seal or digest does not verify`, `the external head (seq ${pub}) cannot be confirmed: the chain is broken at or before record ${k}`],
      records: k - 1,
      internal_ok: false,
      external_head_match: false,
    }),
    flagged({
      id: 'suffix-delete',
      title: 'Delete the newest records',
      what_we_did: 'Deleted the last 2 records.',
      problems: [`the log holds ${n - 2} record(s), the anchor ledger anchors ${n}: it was rolled back`],
      records: n - 2,
      internal_ok: false,
      external_head_match: true,
    }),
    flagged({
      id: 'directory-rollback',
      title: 'Restore an old backup',
      what_we_did: `Restored the log and its internal anchors from a backup at record ${back} (the external head was published at ${pub}).`,
      problems: [`rollback detected vs external head: the log holds ${back} record(s), but seq ${pub} was published externally at ${at}`],
      records: back,
      internal_ok: true,
      external_head_match: false,
    }),
    {
      id: 'rollback-after-publication',
      title: 'Roll back after the last publication',
      what_we_did: `Restored the log to record ${n - 1}, after the last external publication (record ${pub}): the stated window.`,
      expected: 'not flagged',
      expected_detected: false,
      detected: false,
      ok: true,
      verify_ok: true,
      internal_ok: true,
      external_head_match: true,
      records: n - 1,
      problems: [],
    },
    flagged({
      id: 'remove-internal-anchors',
      title: 'Delete the internal anchors',
      what_we_did: 'Deleted the internal anchor ledger file.',
      problems: [`the log holds ${n} record(s), the anchor ledger anchors 0 with another head: it was rewritten, or records were added outside the ledger`],
      records: n,
      internal_ok: false,
      external_head_match: true,
    }),
    flagged({
      id: 'external-head-missing',
      title: 'Lose the external head',
      what_we_did: 'Deleted the externally published head file.',
      problems: [`external head missing: the log records an external publication of seq ${pub}, but the external sink holds no head for it`],
      records: n,
      internal_ok: true,
      external_head_match: false,
    }),
    flagged({
      id: 'key-holder-rewrite',
      title: 'Rewrite history with the key',
      what_we_did: `Edited record ${k} as someone holding the ledger key, re-sealed the whole chain and regenerated the anchors.`,
      problems: [`history rewritten at or before seq ${pub}: the log no longer matches the head published externally at ${at}`],
      records: n,
      internal_ok: true,
      external_head_match: false,
    }),
  ];
}

export const handle: MockHandler = async ({ req, path, method, sub, roles }) => {
  if (path !== 'audit/events' && path !== 'audit/ask' && path !== 'audit/tamper-demo') return null;
  const s = store();
  if (!s) return null; // the built-in mock creates the store on first use; let it answer this one
  if (!roles.includes('compliance_officer')) return err(403, 'forbidden');

  if (path === 'audit/events' && method === 'GET') {
    const p = req.nextUrl.searchParams;
    const f = Object.fromEntries(['user', 'container', 'doc', 'since', 'until', 'decision'].map((k) => [k, p.get(k) ?? undefined]));
    const hits = filterRequests(s.ledger, f);
    const events = hits.flatMap(({ e, index }) => ledgerRows(e, index).reverse());
    ledgerQuery(s, sub, `filter ${JSON.stringify(f)} → ${hits.length} rows`);
    return json({ events });
  }

  if (path === 'audit/ask' && method === 'POST') {
    const q = String((await body(req)).question ?? '');
    const filter = parseAuditQuestion(q);
    const hits = filterRequests(s.ledger, filter);
    ledgerQuery(s, sub, `filter ${JSON.stringify(filter)} → ${hits.length} rows`);
    // the rules recognised something, or nothing (then the real backend may ask the model; the mock does not)
    return json({ filter, parser: 'deterministic', parser_model: null, parser_fallback: false, events: hits.flatMap(({ e, index }) => ledgerRows(e, index).reverse()) });
  }

  if (path === 'audit/tamper-demo' && method === 'POST') {
    // write-ahead "started", copy (the record count the built-in /audit/verify reports), run, "ok"
    ledgerQuery(s, sub, 'tamper-demo started on a copy');
    const n = s.ledger.length * 3;
    const pub = Math.max(2, n - 4);
    const t0 = Date.now();
    await new Promise((r) => setTimeout(r, 900));
    const result: TamperDemoResult = {
      real_log_modified: false,
      head_before: sha(`head:${n}`).slice(0, 12),
      records_copied: n,
      external_head_copied: true,
      elapsed_ms: Date.now() - t0,
      scenarios: tamperScenarios(n, pub),
    };
    ledgerQuery(s, sub, 'tamper-demo ok: real_log_modified=false');
    return json(result);
  }
  return err(404, 'not found');
};
