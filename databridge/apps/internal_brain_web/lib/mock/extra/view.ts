// Mock handler for "Make this a page" (NEXT_PUBLIC_IB_MOCK=1). Server-side only. Mirrors
// databridge_ib/views/ and its route:
//
//   POST ib/views   {req_id, instruction}   → a just-in-time view of MY answer (A2UI v0.9 messages)
//   GET  ib/status                          → + views {enabled, model, blocks}
//
// The same planner rules as views/plan.py (trigger words, in the order they appear; none: the
// automatic layout; sources last). The mock has no document text, so blocks are filled from the
// answer's own tagged sentences, the cited documents' records and, for a cited pricing document,
// the synthetic SG/HK/ID pricing sheet. One 404 for someone else's, unknown and refused requests.
import { randomUUID } from 'node:crypto';
import { NextResponse } from 'next/server';
import type { AuditEvent } from '../../types';
import { DOCS, SCOPE_NOTE, type MockDoc } from '../fixtures';
import { handle as deliver } from './deliver';
import type { MockCtx, MockHandler } from './types';
import { extraDocMeta } from './user';

const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const notFound = () => json({ detail: 'not found' }, 404);
const ID_RE = /^[A-Za-z0-9_-]{1,200}$/;
const CATALOG_ID = 'https://walnut.sg/a2ui/catalogs/view/v1.json';
const BLOCKS = ['facts', 'table', 'chart', 'steps', 'timeline', 'conversation', 'sections', 'sources'] as const;
const AUTO = ['facts', 'table', 'chart', 'steps', 'timeline', 'conversation', 'sources'];
const COMPONENT: Record<string, string> = {
  facts: 'WalnutFacts', table: 'WalnutTable', chart: 'WalnutChart', steps: 'WalnutSteps', timeline: 'WalnutTimeline',
  conversation: 'WalnutConversation', sections: 'WalnutSections', sources: 'WalnutSources', missing: 'WalnutMissing',
};
const TRIGGERS: Record<string, string[]> = {
  facts: ['summary', 'summarise', 'summarize', 'overview', 'key facts', 'key points', 'highlights', 'brief', 'tl;dr', 'tldr', 'bullet', 'bullets', 'takeaways', 'main points', '摘要', '总结', '要点', '概述'],
  table: ['table', 'tables', 'tabular', 'column', 'columns', 'rows', 'grid', 'spreadsheet', 'compare', 'comparison', 'matrix', '表格', '对比', '表'],
  chart: ['chart', 'charts', 'graph', 'graphs', 'plot', 'bar', 'bars', 'visual', 'visualise', 'visualize', 'trend', '图表', '图'],
  steps: ['steps', 'step by step', 'checklist', 'procedure', 'how to', 'runbook', 'process', 'instructions', '步骤', '流程', '清单'],
  timeline: ['timeline', 'chronology', 'chronological', 'dates', 'date', 'when', 'history', 'sequence', 'schedule', 'deadline', 'deadlines', '时间线', '时间', '日期'],
  conversation: ['conversation', 'conversations', 'convo', 'convos', 'chat', 'chats', 'thread', 'threads', 'discussion', 'discussions', 'messages', 'who said', 'slack', '对话', '聊天', '讨论'],
  sections: ['by source', 'by document', 'each document', 'per document', 'each source', 'every document', 'all documents', 'in detail', 'details', 'detailed', 'sections', 'full report', 'everything', '分节', '详细', '所有文件'],
  sources: ['sources', 'references', 'citations', 'links', '来源', '引用'],
};
const SHEET = {
  columns: ['market', 'plan', 'product', 'availability', 'price'],
  rows: [
    ['SG', 'Growth', 'multi-currency account', 'available', '0.35% FX margin'],
    ['HK', 'Growth', 'multi-currency account', 'available', '0.35% FX margin'],
    ['ID', 'Growth', 'multi-currency account', 'available (see product policy)', '0.4% FX margin'],
    ['ID', 'Starter', 'multi-currency account', 'not offered', '-'],
  ],
};

const g = globalThis as unknown as { __ibMock?: { ledger: AuditEvent[]; answers: Map<string, string> } };
const meta = (docId: string): MockDoc | null => Object.values(DOCS).find((d) => d.doc_id === docId) ?? extraDocMeta(docId);

function plan(text: string): [string[], boolean] {
  const s = ' ' + text.toLowerCase().split(/\s+/).join(' ').slice(0, 300) + ' ';
  const first = new Map<string, number>();
  for (const [kind, words] of Object.entries(TRIGGERS)) {
    for (const w of words) {
      const re = /[㐀-鿿]/.test(w) ? new RegExp(w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g') : new RegExp(`(?<![a-z0-9])${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![a-z0-9])`, 'g');
      for (const m of s.matchAll(re)) first.set(kind, Math.min(first.get(kind) ?? Infinity, m.index ?? 0));
    }
  }
  const named = [...first.keys()].filter((k) => k !== 'sources').sort((a, b) => first.get(a)! - first.get(b)! || BLOCKS.indexOf(a as never) - BLOCKS.indexOf(b as never));
  if (!named.length) return [[...AUTO], false];
  // a "report" word with named blocks: the key facts open it (views/plan.py)
  if (!named.includes('facts') && /(?<![a-z0-9])(report|brief me|briefing|one-pager|dashboard)(?![a-z0-9])|报告|简报/.test(s)) named.unshift('facts');
  return [[...named, 'sources'], true];
}

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
  return { req: e, cites, facts, question: String(e.question ?? '') };
}

function citation(tag: string, d: MockDoc, n: number) {
  const kind = { confluence: 'page', jira: 'issue', slack: 'thread', drive: 'file' }[d.platform];
  return {
    tag, n, doc_id: d.doc_id, platform: d.platform, kind, container: d.container, container_name: d.container_name,
    title: d.title, version: d.version, updated_at: d.updated_at, deep_link: d.deep_link, cited_version: d.version,
    changed_since_answer: false, checked_at: new Date().toISOString(),
  };
}

type Ctx = NonNullable<ReturnType<typeof context>>;
const SPEAKER = /^([a-z0-9][a-z0-9._-]{1,40}):\s+(\S.*)$/;

function block(kind: string, ctx: Ctx, explicit: boolean): Record<string, unknown> | null {
  const tags = [...ctx.cites.keys()];
  const pricing = tags.find((t) => /pricing/i.test(ctx.cites.get(t)!.doc_id + ' ' + ctx.cites.get(t)!.title));
  if (kind === 'facts') return ctx.facts.length ? { type: 'facts', items: ctx.facts.slice(0, 6).map((f) => ({ text: f.text, tags: [f.tags[0]] })) } : null;
  if (kind === 'table') {
    if (pricing)
      return { type: 'table', table: { source: 'source_table', from_tag: pricing, columns: SHEET.columns, truncated: false, rows: SHEET.rows.map((r) => r.map((text) => ({ text, tags: [pricing], numeric: /\d/.test(text) }))) } };
    if (!explicit || !ctx.facts.length) return null;
    return {
      type: 'table',
      table: {
        source: 'cited_facts', columns: ['What it says', 'Values'], truncated: false,
        rows: ctx.facts.map((f) => [
          { text: f.text, tags: [f.tags[0]], numeric: false },
          { text: (f.text.match(/[A-Z]{3}\s?[\d,.]+|[\d.,]+%|\d[\d,.]*/g) ?? []).join('; '), tags: [f.tags[0]], numeric: false },
        ]),
      },
    };
  }
  if (kind === 'chart' && pricing)
    return {
      type: 'chart', title: 'FX margin by market',
      chart: { type: 'bar', drawn_as_bar: false, x_label: 'market', y_label: 'FX margin', unit: 'percent', currency: null, from_tag: pricing, series: [{ name: 'FX margin', points: [['SG', 0.35], ['HK', 0.35], ['ID', 0.4]].map(([x, v]) => ({ x, value: v, raw: `${v}% FX margin`, tag: pricing })) }] },
    };
  if (kind === 'steps') {
    const items = ctx.facts.filter((f) => /^(step\s*\d{1,2}\s*[:.)-]|\d{1,2}[.)]\s)/i.test(f.text)).map((f) => ({ text: f.text, tags: [f.tags[0]] }));
    return items.length >= 2 ? { type: 'steps', items } : null;
  }
  if (kind === 'timeline') {
    const items = tags
      .map((t) => {
        const d = ctx.cites.get(t)!;
        return { when: d.updated_at.slice(0, 10), dated_by: 'record', label: d.title, platform: d.platform, text: ctx.facts.find((f) => f.tags.includes(t))?.text ?? '', tags: [t] };
      })
      .sort((a, b) => a.when.localeCompare(b.when));
    return items.length >= 2 ? { type: 'timeline', items } : null;
  }
  if (kind === 'conversation') {
    const threads = tags
      .filter((t) => ctx.cites.get(t)!.platform === 'slack')
      .map((t) => {
        const d = ctx.cites.get(t)!;
        const messages = ctx.facts
          .filter((f) => f.tags.includes(t))
          .map((f) => {
            const m = SPEAKER.exec(f.text);
            return m ? { speaker: m[1], text: m[2], tags: [t] } : null;
          })
          .filter(Boolean);
        return messages.length ? { tag: t, title: d.title, platform: d.platform, place: d.container_name, messages } : null;
      })
      .filter(Boolean);
    return threads.length ? { type: 'conversation', threads } : null;
  }
  if (kind === 'sections') {
    const docs = tags
      .map((t) => {
        const d = ctx.cites.get(t)!;
        const items = ctx.facts.filter((f) => f.tags.includes(t)).map((f) => f.text);
        return items.length ? { tag: t, title: d.title, platform: d.platform, place: d.container_name, updated: d.updated_at.slice(0, 10), parts: [{ heading: '', items }], table: null } : null;
      })
      .filter(Boolean);
    return docs.length ? { type: 'sections', docs } : null;
  }
  return null;
}

const ZERO = { values_proposed: 0, values_kept: 0, values_dropped: 0, items_proposed: 0, items_kept: 0, items_dropped: 0, names_dropped: 0 };

function tagsOf(b: Record<string, unknown>): string[] {
  const s = JSON.stringify(b);
  return [...new Set(Array.from(s.matchAll(/"(C\d{1,2})"/g), (m) => m[1]))];
}

/** Mode "decide": the rules (views/plan.py wants_page) on the question as asked. */
const PAGE = /(?<![a-z0-9])(report|overview|briefing|brief me|dashboard|one-pager|everything|each (?:employee|person|member|engineer)|every (?:employee|person|member)|all (?:the )?(?:employees|people|members|staff|documents|tickets|issues)|who (?:did|worked|works)|table|tabular|chart|graph|timeline|checklist|compare|comparison|by document|in (?:black|white|blue|green|red|dark)|as a (?:page|list|grid))(?![a-z0-9])|报告|总览|概览|表格|图表|时间线|所有|每个|页面/;

function decide(sub: string, b: Record<string, unknown>) {
  const q = typeof b.instruction === 'string' ? b.instruction.split(/\s+/).join(' ').trim().toLowerCase() : '';
  if (!PAGE.test(q)) return json({ render: 'chat', title: '', decided_by: 'rules' });
  const ctx = context(sub, b.req_id);
  if (!ctx) return notFound();
  return json({ render: 'page', title: ctx.question.slice(0, 120), decided_by: 'rules', view_id: randomUUID().replace(/-/g, '') });
}

function build(sub: string, b: Record<string, unknown>) {
  if (!Object.keys(b).every((k) => k === 'req_id' || k === 'instruction' || k === 'mode')) return json({ detail: 'invalid' }, 422);
  if (b.mode === 'decide') return decide(sub, b);
  const instruction = typeof b.instruction === 'string' ? b.instruction.split(/\s+/).join(' ').trim() : '';
  if (instruction.length > 300) return json({ detail: 'invalid' }, 422);
  const ctx = context(sub, b.req_id);
  if (!ctx) return notFound();
  const [kinds, explicit] = plan(instruction);
  const blocks: Record<string, unknown>[] = [];
  for (const k of kinds) {
    if (k === 'sources') continue;
    const got = block(k, ctx, explicit);
    if (got) blocks.push(got);
    else if (explicit) blocks.push({ type: 'missing', wanted: k });
  }
  const content = blocks.filter((x) => x.type !== 'missing');
  const order = [...ctx.cites.keys()];
  const used = [...new Set(content.flatMap(tagsOf))].filter((t) => ctx.cites.has(t));
  const citations = used.map((t) => citation(t, ctx.cites.get(t)!, order.indexOf(t) + 1));
  const viewBlocks = content.length ? [...blocks, { type: 'sources' }] : blocks;
  const view_id = randomUUID().replace(/-/g, '');
  const title = ctx.question.slice(0, 120) || 'Your page';
  const model = { provider: 'deterministic', model: 'view-rules-v1', fallback_used: false };
  const generated = content.length ? 'rules' : 'none';
  const as_of = new Date().toISOString();
  const metaBlock = { label: 'Draft for your review', generated, model, instruction, requested: explicit ? kinds : [], as_of, scope_note: SCOPE_NOTE };
  const components = [
    { id: 'root', component: 'WalnutPage', title: { path: '/title' }, meta: { path: '/meta' }, children: viewBlocks.map((_, i) => `b${i}`) },
    ...viewBlocks.map((x, i) => ({ id: `b${i}`, component: COMPONENT[String(x.type)], data: { path: `/blocks/${i}` }, sources: { path: '/sources' } })),
  ];
  return json({
    view_id, req_id: ctx.req.req_id, status: content.length ? 'ready' : 'insufficient', label: 'Draft for your review', title,
    generated, model, check: ZERO, layout: viewBlocks.map((x) => x.type),
    a2ui: [
      { version: 'v0.9', createSurface: { surfaceId: view_id, catalogId: CATALOG_ID } },
      { version: 'v0.9', updateComponents: { surfaceId: view_id, components } },
      { version: 'v0.9', updateDataModel: { surfaceId: view_id, path: '/', value: { title, meta: metaBlock, blocks: viewBlocks, sources: citations } } },
    ],
    citations, fallback: 'no_model', prompt_version: 'ib-view-2026-10-30.1', scope_note: SCOPE_NOTE, as_of,
  });
}

async function bodyOf(ctx: MockCtx): Promise<Record<string, unknown> | null> {
  try {
    const b = (await ctx.req.clone().json()) as unknown;
    return b && typeof b === 'object' && !Array.isArray(b) ? (b as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export const handle: MockHandler = async (ctx) => {
  const { path, method, sub } = ctx;
  if (path === 'ib/status' && method === 'GET') {
    const r = await deliver(ctx);
    if (!r || r.status !== 200) return r;
    const body = (await r.json()) as Record<string, unknown>;
    return json({ ...body, views: { enabled: true, model: false, blocks: [...BLOCKS] } });
  }
  if (method === 'POST' && path === 'ib/rewrite') {
    const b = await bodyOf(ctx);
    return json({ question: typeof b?.question === 'string' ? b.question : '', rewritten: false });
  }
  if (method !== 'POST' || path !== 'ib/views') return null;
  if (!ctx.roles.some((r) => ['employee', 'compliance_officer', 'admin_it'].includes(r))) return json({ detail: 'forbidden' }, 403);
  const b = await bodyOf(ctx);
  if (!b) return json({ detail: 'invalid' }, 422);
  return build(sub, b);
};
