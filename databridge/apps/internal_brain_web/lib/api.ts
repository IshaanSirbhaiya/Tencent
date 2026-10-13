// Browser-side client. Every call is same-origin `/api/ib/*` with the httpOnly session cookie;
// this module never sees, stores or sends a token.
import type {
  AskResponse,
  AuditAskResponse,
  AuditEvent,
  AuditFilter,
  HistoryItem,
  RevealResult,
  SimResult,
  SourceStatus,
  VerifyResult,
  WhoAmI,
} from './types';

const BASE = '/api/ib';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(path: string, init?: { method?: 'GET' | 'POST'; body?: unknown }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: init?.method ?? 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: init?.body !== undefined ? { 'content-type': 'application/json' } : undefined,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError(0, 'network');
  }
  if (!res.ok) {
    let detail = '';
    try {
      const j = await res.json();
      detail = typeof j?.detail === 'string' ? j.detail : typeof j?.error === 'string' ? j.error : '';
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  return (await res.json()) as T;
}

function qs(params: Record<string, string | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) u.set(k, v);
  const s = u.toString();
  return s ? `?${s}` : '';
}

// The IB audit API returns one row per LEDGER EVENT ({kind, req_id, event: {...}}); the UI shows
// one row per REQUEST. Merge request.accepted + retrieval.decided + answer.released by req_id.
// Rows already in request shape (mock mode) pass through unchanged.
type LedgerRow = { kind?: string; req_id?: string; event_time?: string; sub?: string; synthetic_replay?: boolean; event?: Record<string, unknown>; [k: string]: unknown };
export function groupByRequest(rows: LedgerRow[]): AuditEvent[] {
  if (!rows.length || !rows.some((r) => typeof r.kind === 'string' && r.event)) return rows as unknown as AuditEvent[];
  const byReq = new Map<string, Record<string, unknown>>();
  const order: string[] = [];
  for (const r of rows) {
    const id = r.req_id;
    if (!id) continue;
    if (!byReq.has(id)) {
      byReq.set(id, { req_id: id, sub: r.sub, event_time: r.event_time, synthetic_replay: r.synthetic_replay });
      order.push(id);
    }
    const acc = byReq.get(id)!;
    const e = (r.event ?? {}) as Record<string, unknown>;
    if (r.kind === 'request.accepted') Object.assign(acc, { question: e.question, channel: e.channel, event_time: e.event_time ?? acc.event_time });
    else if (r.kind === 'retrieval.decided') Object.assign(acc, { considered: e.considered, considered_meta: e.considered_meta, gate2: e.gate2, released: e.released });
    else if (r.kind === 'answer.released') Object.assign(acc, { model: e.model, refused: e.refused, latency_ms: e.latency_ms });
  }
  return order.map((id) => byReq.get(id)!) as unknown as AuditEvent[];
}

export const api = {
  login: (persona: string) => call<{ sub: string; roles: string[]; name: string }>('/auth/demo-login', { method: 'POST', body: { persona } }),
  logout: () => call<{ ok: boolean }>('/auth/logout', { method: 'POST', body: {} }),
  whoami: () => call<WhoAmI>('/auth/whoami'),
  ask: (question: string) => call<AskResponse>('/ib/ask', { method: 'POST', body: { question } }),
  history: () => call<{ items: HistoryItem[] }>('/ib/history'),
  sources: async (): Promise<SourceStatus[]> => {
    const r = await call<{ sources: SourceStatus[] } | SourceStatus[]>('/ib/sources');
    return Array.isArray(r) ? r : r.sources ?? [];
  },
  auditEvents: async (f: AuditFilter): Promise<AuditEvent[]> => {
    const r = await call<{ events: LedgerRow[] } | LedgerRow[]>(`/audit/events${qs(f)}`);
    return groupByRequest(Array.isArray(r) ? r : r.events ?? []);
  },
  auditAsk: async (question: string): Promise<AuditAskResponse> => {
    const r = await call<AuditAskResponse>('/audit/ask', { method: 'POST', body: { question } });
    return { ...r, events: groupByRequest((r.events ?? []) as unknown as LedgerRow[]) };
  },
  reveal: async (req_id: string, reason: string): Promise<RevealResult> => {
    const r = await call<RevealResult | { text: string } | string>('/audit/reveal', { method: 'POST', body: { req_id, reason } });
    if (typeof r === 'string') return { req_id, answer: r };
    if ('answer' in r) return r;
    return { req_id, answer: (r as { text: string }).text };
  },
  verify: () => call<VerifyResult>('/audit/verify'),
  sim: (action: SimAction) => call<SimResult>(`/sim/${action}`, { method: 'POST', body: {} }),
};

export type SimAction = 'runbook-update' | 'revoke-channel' | 'restrict-page' | 'reset';
