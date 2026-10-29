// Pure helpers for User-mode answers. Nothing here invents content: they only re-shape what the
// API returned (the answer text, its citations) or the visitor's own question.
import type { AskResponse, Citation, ModelInfo, Platform } from '@/lib/types';
import type { UserKey } from '@/lib/i18n/user';

/** "[C1]" or "[C1, C2]" in the answer text. */
export const TAG_RE = /\[(C\d+(?:\s*,\s*C\d+)*)\]/g;

/** A refusal is the one response shape with no citations (the text is the API's constant). */
export function isRefusal(r: AskResponse): boolean {
  return r.citations.length === 0;
}

export type ModelKind = 'hy3' | 'deepseek' | 'extractive' | 'other';

export function modelKind(m: ModelInfo | null | undefined): ModelKind {
  if (!m) return 'other';
  const id = m.model.toLowerCase();
  if (m.provider === 'deterministic' || id.startsWith('extractive')) return 'extractive';
  if (id.includes('hy3') || id.includes('hunyuan')) return 'hy3';
  if (id.includes('deepseek')) return 'deepseek';
  return 'other';
}

export const MODEL_KEY: Record<Exclude<ModelKind, 'other'>, UserKey> = {
  hy3: 'answer.model.hy3',
  deepseek: 'answer.model.deepseek',
  extractive: 'answer.model.extractive',
};

/** 1-based position of a citation tag ("C2" → 2), as shown on the chip. */
export function citeNumber(citations: Citation[], tag: string): number {
  return citations.findIndex((c) => c.tag === tag) + 1;
}

/** The answer with "[C1]" written as "[1]", for copying. */
export function plainAnswer(r: AskResponse): string {
  const text = r.answer.replace(TAG_RE, (m, tags: string) => {
    const ns = tags.split(',').map((t) => citeNumber(r.citations, t.trim()));
    return ns.every((n) => n > 0) ? `[${ns.join(', ')}]` : m;
  });
  if (!r.citations.length) return text;
  const lines = r.citations.map((c, i) => `[${i + 1}] ${c.title} (${c.container_name})${c.deep_link ? ` ${c.deep_link}` : ''}`);
  return `${text}\n\n${lines.join('\n')}`;
}

/** The sentences of the answer that cite `tag`, with their tags removed (for the passage match). */
export function sentencesFor(answer: string, tag: string): string[] {
  const out: string[] = [];
  // Split after sentence ends that are followed by a tag group or whitespace.
  const parts = answer.split(/(?<=[.!?。！？](?:\s*\[[^\]]+\])*)\s+/);
  for (const p of parts) {
    const tags = [...p.matchAll(TAG_RE)].flatMap((m) => m[1].split(',').map((t) => t.trim()));
    if (tags.includes(tag)) out.push(p.replace(TAG_RE, '').replace(/\s+/g, ' ').trim());
  }
  return out;
}

/* ------------------------------------------------------------------ passage highlight */

interface Tok {
  w: string;
  start: number;
  end: number;
}

function tokens(s: string): Tok[] {
  const out: Tok[] = [];
  for (const m of s.matchAll(/[\p{L}\p{N}][\p{L}\p{N}'’.,:%-]*/gu)) {
    const raw = m[0].replace(/[.,:]+$/, '');
    out.push({ w: raw.toLowerCase().replace(/’/g, "'"), start: m.index ?? 0, end: (m.index ?? 0) + raw.length });
  }
  return out;
}

/**
 * Best-effort highlight: the longest run of words the excerpt shares with any of `sentences`
 * (at least 4 words, or the whole sentence if shorter). Returns character offsets into `excerpt`.
 */
export function highlightRange(excerpt: string, sentences: string[]): { start: number; end: number } | null {
  const ex = tokens(excerpt);
  if (!ex.length) return null;
  let best = { len: 0, i: 0 };
  let need = 4;
  for (const s of sentences) {
    const st = tokens(s);
    if (!st.length) continue;
    need = Math.min(need, st.length);
    // Longest common substring over words, O(n·m) with a rolling row.
    let prev = new Array<number>(st.length + 1).fill(0);
    for (let i = 1; i <= ex.length; i++) {
      const row = new Array<number>(st.length + 1).fill(0);
      for (let j = 1; j <= st.length; j++) {
        if (ex[i - 1].w === st[j - 1].w) {
          row[j] = prev[j - 1] + 1;
          if (row[j] > best.len) best = { len: row[j], i };
        }
      }
      prev = row;
    }
  }
  if (best.len < Math.max(2, need)) return null;
  const first = ex[best.i - best.len];
  const last = ex[best.i - 1];
  return { start: first.start, end: last.end };
}

/* ------------------------------------------------------------------ follow-ups */

export type FollowUpKey = 'fu.jira' | 'fu.slack' | 'fu.confluence' | 'fu.drive' | 'fu.changed' | 'fu.next';

/**
 * Up to three follow-up chips from fixed templates. Inputs: the cited platforms and the visitor's
 * own question. Never derived from anything the visitor was not shown.
 */
export function followUps(question: string, citations: Citation[]): FollowUpKey[] {
  const q = question.toLowerCase();
  const platforms = new Set<Platform>(citations.map((c) => c.platform));
  const out: FollowUpKey[] = [];
  if (platforms.has('jira') && !/\b(jira|ticket|tickets)\b/.test(q)) out.push('fu.jira');
  if (platforms.has('slack') && !/\bslack\b/.test(q)) out.push('fu.slack');
  if (platforms.has('confluence') && !/\b(summar|page)/.test(q)) out.push('fu.confluence');
  if (platforms.has('drive') && !/\b(drive|file|files)\b/.test(q)) out.push('fu.drive');
  if (!/(last week|since|changed|recent)/.test(q)) out.push('fu.changed');
  if (!/(next step|todo|to do|action)/.test(q)) out.push('fu.next');
  return out.slice(0, 3);
}

/** The question a follow-up chip asks: the template plus the original question as context. */
export function followUpQuestion(label: string, original: string): string {
  return `${label} (${original.replace(/\s+/g, ' ').trim()})`.slice(0, 2000);
}

/* ------------------------------------------------------------------ starter-card hints */

/**
 * Platforms a starter question is likely to touch, from its own words only (decorative hint).
 */
export function inferPlatforms(question: string): Platform[] {
  const q = question.toLowerCase();
  const out: Platform[] = [];
  if (/(slack|channel|thread|said|raised|blocker)/.test(q)) out.push('slack');
  if (/(ticket|jira|cve|status|project|follow-up|tracker|containment|evidence pack|due)/.test(q)) out.push('jira');
  if (/(runbook|page|guidance|policy|report|schedule|rotation|threshold|sla|root cause|drain|review|restricted|offer)/.test(q)) out.push('confluence');
  if (/(sheet|file|pricing|share|drive|fee|dispute|card)/.test(q)) out.push('drive');
  if (!out.length) out.push('confluence');
  return out.slice(0, 2);
}

/* ------------------------------------------------------------------ time */

/** Seconds since an ISO time (never negative), or null. */
export function secondsSince(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((now - t) / 1000));
}

export function checkedAgoKey(s: number): { key: UserKey; n: number } {
  if (s < 5) return { key: 'answer.checkedNow', n: 0 };
  if (s < 60) return { key: 'answer.checkedS', n: s };
  const m = Math.round(s / 60);
  if (m < 60) return { key: 'answer.checkedM', n: m };
  const h = Math.round(m / 60);
  if (h < 48) return { key: 'answer.checkedH', n: h };
  return { key: 'answer.checkedD', n: Math.round(h / 24) };
}

const TZ = 'Asia/Singapore';

export function localeOf(lang: 'en' | 'zh'): string {
  return lang === 'zh' ? 'zh-CN' : 'en-SG';
}

export function fmtWhen(iso: string | null | undefined, lang: 'en' | 'zh', withDate = true): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const opts: Intl.DateTimeFormatOptions = withDate
    ? { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ }
    : { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ };
  return `${new Intl.DateTimeFormat(localeOf(lang), opts).format(d)} SGT`;
}

export function fmtDay(iso: string | null | undefined, lang: 'en' | 'zh'): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return new Intl.DateTimeFormat(localeOf(lang), { day: 'numeric', month: 'short', year: 'numeric', timeZone: TZ }).format(d);
}

/** YYYY-MM-DD of an instant in Singapore time. */
export function sgDay(t: number | string): string {
  const d = typeof t === 'number' ? new Date(t) : new Date(t);
  return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: TZ }).format(d);
}

/** A number is a version, an ISO time is a revision date, anything else an opaque revision. */
export function versionText(v: string, lang: 'en' | 'zh', t: (k: UserKey, vars?: Record<string, string | number>) => string, updated?: string): string {
  if (!v) return '—';
  if (/^\d+$/.test(v)) return t('answer.version', { v });
  if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return t('answer.revisionOf', { date: fmtDay(v, lang) });
  // A Slack message id is its epoch timestamp ("1759900000.000100").
  if (/^\d{9,11}\.\d+$/.test(v)) return t('answer.messageOf', { date: fmtDay(updated ?? new Date(Number(v.split('.')[0]) * 1000).toISOString(), lang) });
  const head = v.split('|')[0];
  return `rev ${head.length > 10 ? `${head.slice(0, 10)}…` : head}`;
}
