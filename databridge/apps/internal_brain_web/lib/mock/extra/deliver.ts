// Mock handlers for "Turn this answer into" (NEXT_PUBLIC_IB_MOCK=1). Server-side only. Mirrors
// databridge_ib/deliverables/ and its routes:
//
//   POST ib/deliverables         {req_id, kind}            → a table, chart or analysis of MY answer
//   POST ib/deliverables/export  {deliverable_id, format}  → the CSV, with the fixed-shape file name
//   GET  ib/status                                          → + deliverables {enabled, model, kinds}
//
// One 404 for someone else's, unknown and refused requests. The mock has no document text, so a
// cited "pricing" document stands for the synthetic SG/HK/ID pricing sheet, and the analysis shows
// one removed sentence so the pill's removal state can be seen. Synthetic data only.
import { randomUUID } from 'node:crypto';
import { NextResponse } from 'next/server';
import type { AuditEvent } from '../../types';
import { DOCS, MOCK_MODEL_LABELS, SCOPE_NOTE, type MockDoc } from '../fixtures';
import { handle as graph } from './graph';
import type { MockCtx, MockHandler } from './types';
import { extraDocMeta } from './user';

const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const notFound = () => json({ detail: 'not found' }, 404);
const ID_RE = /^[A-Za-z0-9_-]{1,200}$/;
const KINDS = ['table', 'chart', 'analysis'] as const;
type Kind = (typeof KINDS)[number];
const SHEET = {
  columns: ['market', 'plan', 'product', 'availability', 'price'],
  rows: [
    ['SG', 'Growth', 'multi-currency account', 'available', '0.35% FX margin'],
    ['HK', 'Growth', 'multi-currency account', 'available', '0.35% FX margin'],
    ['ID', 'Growth', 'multi-currency account', 'available (see product policy)', '0.4% FX margin'],
    ['ID', 'Starter', 'multi-currency account', 'not offered', '-'],
  ],
};

interface Stored {
  sub: string;
  req: string;
  body: Record<string, unknown>;
}
const g = globalThis as unknown as {
  __ibDeliver?: Map<string, Stored>;
  __ibMock?: { ledger: AuditEvent[]; answers: Map<string, string> };
};
const store = () => (g.__ibDeliver ??= new Map());

async function bodyOf(ctx: MockCtx): Promise<Record<string, unknown> | null> {
  try {
    const b = (await ctx.req.clone().json()) as unknown;
    return b && typeof b === 'object' && !Array.isArray(b) ? (b as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
const onlyKeys = (b: Record<string, unknown>, keys: string[]) => Object.keys(b).every((k) => keys.includes(k));
const meta = (docId: string): MockDoc | null => Object.values(DOCS).find((d) => d.doc_id === docId) ?? extraDocMeta(docId);

function context(sub: string, reqId: unknown) {
  if (typeof reqId !== 'string' || !ID_RE.test(reqId)) return null;
  const s = g.__ibMock;
  const e = s?.ledger.find((x) => x.req_id === reqId && x.kind === 'request');
  if (!s || !e || e.sub !== sub || e.refused) return null;
  const cites = new Map<string, MockDoc>();
  (e.released ?? []).forEach((id, i) => {
    const m = meta(id);
    if (m) cites.set(`C${i + 1}`, m);
  });
  if (!cites.size) return null;
  const facts = (s.answers.get(reqId) ?? '')
    .split(/(?<=[.!?])\s+/)
    .map((x) => ({ text: x.replace(/\s*\[C\d+\]/g, '').trim(), tags: Array.from(x.matchAll(/\[(C\d+)\]/g), (m) => m[1]) }))
    .filter((f) => f.text && f.tags.length && f.tags.every((t) => cites.has(t)));
  return { req: e, cites, facts };
}

function citation(tag: string, d: MockDoc, n: number) {
  const kind = { confluence: 'page', jira: 'issue', slack: 'thread', drive: 'file' }[d.platform];
  return {
    tag, n, doc_id: d.doc_id, platform: d.platform, kind, container: d.container, container_name: d.container_name,
    title: d.title, version: d.version, updated_at: d.updated_at, deep_link: d.deep_link, cited_version: d.version,
    changed_since_answer: false, checked_at: new Date().toISOString(),
  };
}

const ZERO = { values_proposed: 0, values_kept: 0, values_dropped: 0, items_proposed: 0, items_kept: 0, items_dropped: 0, names_dropped: 0 };

function build(sub: string, b: Record<string, unknown>) {
  if (!onlyKeys(b, ['req_id', 'kind', 'chart']) || !KINDS.includes(b.kind as Kind)) return json({ detail: 'invalid' }, 422);
  const kind = b.kind as Kind;
  const ctx = context(sub, b.req_id);
  if (!ctx) return notFound();
  const tags = [...ctx.cites.keys()];
  const pricing = tags.find((t) => /pricing/i.test(ctx.cites.get(t)!.doc_id + ' ' + ctx.cites.get(t)!.title));
  const nOf = (t: string) => tags.indexOf(t) + 1;
  let content: Record<string, unknown> | null = null;
  let title = '';
  let model: Record<string, unknown> | null = null;
  let generated = 'none';
  let check = ZERO;
  let used: string[] = [];
  if (kind === 'table') {
    generated = 'deterministic';
    if (pricing) {
      title = 'Table from your sources';
      model = { provider: 'deterministic', model: 'source-table-v1', fallback_used: false };
      content = {
        source: 'source_table', from_tag: pricing, columns: SHEET.columns, truncated: false,
        rows: SHEET.rows.map((r) => r.map((text) => ({ text, tags: [pricing], numeric: /\d/.test(text) }))),
      };
      used = [pricing];
    } else {
      title = 'Quoted from your sources';
      model = { provider: 'deterministic', model: 'quoted-table-v1', fallback_used: false };
      content = {
        source: 'cited_facts', columns: ['What it says', 'Values'], truncated: false,
        rows: ctx.facts.map((f) => [
          { text: f.text, tags: [f.tags[0]], numeric: false },
          { text: (f.text.match(/[A-Z]{3}\s?[\d,.]+|[\d.,]+%|\d[\d,.]*/g) ?? []).join('; '), tags: [f.tags[0]], numeric: false },
        ]),
      };
      used = ctx.facts.map((f) => f.tags[0]);
    }
  } else if (kind === 'chart' && pricing) {
    generated = 'deterministic';
    title = 'FX margin by market';
    model = { provider: 'deterministic', model: 'source-chart-v1', fallback_used: false };
    content = {
      type: 'bar', drawn_as_bar: false, x_label: 'market', y_label: 'FX margin', unit: 'percent', currency: null, from_tag: pricing,
      series: [{ name: 'FX margin', points: [['SG', 0.35], ['HK', 0.35], ['ID', 0.4]].map(([x, v]) => ({ x, value: v, raw: `${v}% FX margin`, tag: pricing })) }],
    };
    used = [pricing];
  } else if (kind === 'analysis' && ctx.facts.length && MOCK_MODEL_LABELS) {
    // Only when the build explicitly asks for model labels (NEXT_PUBLIC_IB_MOCK_MODEL=hy3): TokenHub is not
    // live, so by default the mock analysis is 'needs_model', exactly like the real server without a model.
    generated = 'model';
    title = 'What this answer means';
    model = { provider: 'tencent-tokenhub', model: 'hy3', fallback_used: false };
    content = { sections: [{ heading: 'Key facts', sentences: ctx.facts.slice(0, 3).map((x) => ({ text: x.text, tags: x.tags })) }] };
    check = { ...ZERO, items_proposed: Math.min(3, ctx.facts.length) + 1, items_kept: Math.min(3, ctx.facts.length), items_dropped: 1, values_proposed: 1, values_dropped: 1 };
    used = [...new Set(ctx.facts.slice(0, 3).flatMap((x) => x.tags))];
  }
  const status = content ? 'ready' : kind === 'analysis' ? 'needs_model' : 'insufficient';
  const deliverable_id = randomUUID().replace(/-/g, '');
  const body = {
    deliverable_id, req_id: ctx.req.req_id, kind, status, label: 'Draft for your review', title: title || 'Chart from your sources',
    generated: content ? generated : 'none', model: content ? model : null, check, content,
    citations: [...new Set(used)].map((t) => citation(t, ctx.cites.get(t)!, nOf(t))),
    exports: content && kind !== 'analysis' ? ['csv'] : [], fallback: 'none', prompt_version: 'ib-deliver-2026-10-31.1',
    scope_note: SCOPE_NOTE, as_of: new Date().toISOString(),
  };
  if (content) store().set(deliverable_id, { sub, req: ctx.req.req_id, body });
  return json(body);
}

const guard = (s: string) => (/^[\s ]*[=+\-@\t\r\n]/.test(s) && !/^-?\d[\d,]*(\.\d+)?%?$/.test(s.trim()) ? `'${s}` : s);
const cell = (s: unknown) => {
  const v = guard(String(s ?? ''));
  return /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
};

function exportFile(sub: string, b: Record<string, unknown>) {
  if (!onlyKeys(b, ['deliverable_id', 'format']) || (b.format !== 'csv' && b.format !== 'pptx') || typeof b.deliverable_id !== 'string')
    return json({ detail: 'invalid' }, 422);
  const e = store().get(b.deliverable_id);
  if (!e || e.sub !== sub) return notFound(); // ownership before the format
  if (!(e.body.exports as string[]).includes(b.format)) return json({ detail: 'invalid' }, 422);
  const d = e.body as { kind: Kind; content: Record<string, unknown>; citations: { tag: string; n: number; title: string; platform: string; container_name: string; version: string; checked_at: string; deep_link: string }[] };
  const n = new Map(d.citations.map((c) => [c.tag, c.n]));
  const lines: string[][] = [];
  if (d.kind === 'table') {
    const tb = d.content as { columns: string[]; rows: { text: string; tags: string[] }[][] };
    lines.push([...tb.columns, 'Sources']);
    for (const r of tb.rows) lines.push([...r.map((c) => c.text), [...new Set(r.flatMap((c) => c.tags))].map((t) => `[${n.get(t)}]`).join(' ')]);
  } else {
    const ch = d.content as { unit: string; series: { name: string; points: { x: string; raw: string; value: number; tag: string }[] }[] };
    lines.push(['Series', 'Label', 'Value as written', 'Value', 'Unit', 'Source']);
    for (const s of ch.series) for (const p of s.points) lines.push([s.name, p.x, p.raw, String(p.value), ch.unit, `[${n.get(p.tag)}]`]);
  }
  lines.push([]);
  lines.push(['Source', 'Title', 'Platform', 'Place', 'Version', 'Checked at', 'Link']);
  for (const c of d.citations) lines.push([`[${c.n}]`, c.title, c.platform, c.container_name, c.version, c.checked_at, /^https?:\/\//.test(c.deep_link) ? c.deep_link : '']);
  const text = '﻿' + lines.map((r) => r.map(cell).join(',')).join('\r\n') + '\r\n';
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const head = e.req.slice(0, 8).toLowerCase();
  const name = `walnut-${d.kind}-${/^[0-9a-f]{8}$/.test(head) ? head : '00000000'}-${day}.csv`;
  return new NextResponse(text, {
    status: 200,
    headers: {
      'content-type': 'text/csv; charset=utf-8',
      'content-disposition': `attachment; filename="${name}"`,
      'x-content-type-options': 'nosniff',
      'cache-control': 'no-store',
    },
  });
}

export const handle: MockHandler = async (ctx) => {
  const { path, method, sub } = ctx;
  if (path === 'ib/status' && method === 'GET') {
    const r = await graph(ctx);
    if (!r || r.status !== 200) return r;
    const body = (await r.json()) as Record<string, unknown>;
    return json({ ...body, deliverables: { enabled: true, model: MOCK_MODEL_LABELS, kinds: [...KINDS], exports: { csv: true, pptx: false } } });
  }
  if (method !== 'POST' || (path !== 'ib/deliverables' && path !== 'ib/deliverables/export')) return null;
  if (!ctx.roles.some((r) => ['employee', 'compliance_officer', 'admin_it'].includes(r))) return json({ detail: 'forbidden' }, 403);
  const b = await bodyOf(ctx);
  if (!b) return json({ detail: 'invalid' }, 422);
  return path === 'ib/deliverables' ? build(sub, b) : exportFile(sub, b);
};
