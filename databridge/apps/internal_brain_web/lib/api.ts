// Browser-side client. Every call is same-origin `/api/ib/*` with the httpOnly session cookie;
// this module never sees, stores or sends a token.
import type {
  AskResponse,
  AuditAskResponse,
  AuditEvent,
  AuditFilter,
  HistoryItem,
  KnowledgeMap,
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

/**
 * Session slots for the "Same question, two people" page. Slot `a` is the main session
 * (cookie `ib_session`). Slots `b` and `c` are separate signed sessions (`ib_session_b`,
 * `ib_session_c`) that the proxy selects from the `x-ib-slot` header. Identity still comes only
 * from a signed token minted by /auth/demo-login; the header only picks WHICH cookie to forward.
 */
export type Slot = 'a' | 'b' | 'c';

// A little longer than the proxy's own 90 s deadline, so the proxy's 503 normally arrives first.
const CLIENT_TIMEOUT_MS = 95_000;

function deadline(): AbortSignal | undefined {
  return typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(CLIENT_TIMEOUT_MS) : undefined;
}

async function call<T>(path: string, init?: { method?: 'GET' | 'POST'; body?: unknown; slot?: Slot }): Promise<T> {
  let res: Response;
  const headers: Record<string, string> = {};
  if (init?.body !== undefined) headers['content-type'] = 'application/json';
  if (init?.slot && init.slot !== 'a') headers['x-ib-slot'] = init.slot;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: init?.method ?? 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: deadline(),
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
  try {
    return (await res.json()) as T;
  } catch {
    // The body was cut off (timeout or dropped connection) or was not JSON.
    throw new ApiError(0, 'network');
  }
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

const PASSCODE_KEY = 'ib-demo-passcode';
function storedPasscode(): string | undefined {
  try {
    return window.sessionStorage.getItem(PASSCODE_KEY) || undefined;
  } catch {
    return undefined;
  }
}
function rememberPasscode(passcode?: string) {
  try {
    if (passcode) window.sessionStorage.setItem(PASSCODE_KEY, passcode);
    else window.sessionStorage.removeItem(PASSCODE_KEY);
  } catch {
    /* storage unavailable */
  }
}

/** The one demo sign-in call, for the main session or a compare slot. */
function demoLogin(persona: string, passcode: string | undefined, slot?: Slot) {
  return call<{ sub: string; roles: string[]; name: string }>('/auth/demo-login', {
    method: 'POST',
    body: passcode ? { persona, passcode } : { persona },
    slot,
  });
}

let mapInFlight: Promise<KnowledgeMap> | null = null;

export const api = {
  login: async (persona: string, passcode?: string) => {
    const r = await demoLogin(persona, passcode);
    if (passcode) rememberPasscode(passcode);
    return r;
  },
  /** Switch the main session to another persona with the passcode given at sign-in (the tour). */
  switchTo: (persona: string) => demoLogin(persona, storedPasscode()),
  logout: () => call<{ ok: boolean }>('/auth/logout', { method: 'POST', body: {} }),
  whoami: () => call<WhoAmI>('/auth/whoami'),
  ask: (question: string) => call<AskResponse>('/ib/ask', { method: 'POST', body: { question } }),
  history: () => call<{ items: HistoryItem[] }>('/ib/history'),
  // The IB checks every item live before listing it (several seconds for a big map), so concurrent
  // callers (e.g. a remount) share one in-flight request instead of starting another.
  map: (): Promise<KnowledgeMap> => {
    if (!mapInFlight) mapInFlight = call<KnowledgeMap>('/ib/map').finally(() => (mapInFlight = null));
    return mapInFlight;
  },
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

/** The same calls, bound to a separate compare slot. Never touches the main session cookie. */
export function slotApi(slot: Exclude<Slot, 'a'>) {
  return {
    login: (persona: string) => demoLogin(persona, storedPasscode(), slot),
    whoami: () => call<WhoAmI>('/auth/whoami', { slot }),
    ask: (question: string) => call<AskResponse>('/ib/ask', { method: 'POST', body: { question }, slot }),
    map: () => call<KnowledgeMap>('/ib/map', { slot }),
  };
}

/**
 * Run `fn` while holding the compare-slot locks, so two Compare tabs (which share the slot
 * cookies) cannot sign a slot in as someone else halfway through the other's login → ask → map.
 * Locks are taken in a fixed order, so two tabs cannot deadlock. Without the Web Locks API (e.g. a
 * non-secure context) it simply runs; the compare page still checks who each answer belongs to.
 */
export async function withSlotLocks<T>(slots: Exclude<Slot, 'a'>[], fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
  const [first, ...rest] = [...slots].sort();
  if (!first || !locks || typeof locks.request !== 'function') return fn();
  return locks.request(`ib-compare-slot-${first}`, () => withSlotLocks(rest, fn));
}

export type SimAction = 'runbook-update' | 'revoke-channel' | 'restrict-page' | 'reset';
