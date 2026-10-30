// Browser-side client. Every call is same-origin `/api/ib/*` with the httpOnly session cookie;
// this module never sees, stores or sends a token.
import type {
  AskResponse,
  AuditAskResponse,
  AuditEvent,
  AuditFilter,
  AuditStats,
  DocView,
  GraphQuery,
  HistoryItem,
  Journey,
  KnowledgeGraph,
  KnowledgeMap,
  RevealResult,
  SimResult,
  SourceStatus,
  Trace,
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

function deadline(): AbortSignal {
  if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(CLIENT_TIMEOUT_MS);
  // older browsers: the same deadline by hand, so a stalled request can never hang the UI
  const c = new AbortController();
  setTimeout(() => c.abort(), CLIENT_TIMEOUT_MS);
  return c.signal;
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

  // ---- finals endpoints (types in lib/types.ts; contract in docs/finals/contracts/ui_foundation.md §7)
  /** How one of MY answers was made. Someone else's request, or an unknown id: the same 404. */
  trace: (reqId: string) => call<Trace>(`/ib/trace/${encodeURIComponent(reqId)}`),
  /** Aggregates over the verified ledger (compliance_officer). `days` 1..3650, default 30. */
  auditStats: (opts?: { days?: number; replay?: 'include' | 'exclude' }) =>
    call<AuditStats>(`/audit/stats${qs({ days: opts?.days ? String(opts.days) : undefined, replay: opts?.replay })}`),
  /** One request end to end with Gate-2 verdicts and reason codes (compliance_officer). */
  journey: (reqId: string) => call<Journey>(`/audit/journey/${encodeURIComponent(reqId)}`),
  /** PROVISIONAL: the permission-aware subgraph (overview, `q` search, or one-hop `focus`). */
  graph: (query: GraphQuery = {}) =>
    call<KnowledgeGraph>(`/ib/graph${qs({ focus: query.focus, q: query.q, limit: query.limit ? String(query.limit) : undefined })}`),
  /** PROVISIONAL: one document I can open (excerpt, links, lineage). Restricted and unknown: the same 404. */
  doc: (docId: string, q?: string) => call<DocView>(`/ib/doc/${encodeURIComponent(docId)}${qs({ q })}`),
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
 * Locks are taken in a fixed order (and each once), so two tabs cannot deadlock. Without the Web
 * Locks API (a non-secure context, i.e. plain http on a non-localhost address) it refuses to run:
 * comparing safely needs the lock, so the page shows its normal error instead.
 */
export async function withSlotLocks<T>(slots: Exclude<Slot, 'a'>[], fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
  if (!locks || typeof locks.request !== 'function') throw new ApiError(0, 'no-locks');
  const take = async (rest: Exclude<Slot, 'a'>[]): Promise<T> => {
    const [first, ...more] = rest;
    return first ? locks.request(`ib-compare-slot-${first}`, () => take(more)) : fn();
  };
  return take([...new Set(slots)].sort());
}

export type SimAction = 'runbook-update' | 'revoke-channel' | 'restrict-page' | 'reset';

// ---- analytics ----
// Mission control, request journey, analytics (backend_v1.md shapes). The eval file is read from
// /eval_summary.json (public/), never through the IB proxy.
type RawRequestRow = {
  req_id?: string;
  sub?: string | null;
  event_time?: string | null;
  question?: string | null;
  channel?: string | null;
  synthetic_replay?: boolean;
  event?: Record<string, unknown>;
};
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

export const analyticsApi = {
  /** GET /audit/stats?days= (compliance_officer). */
  stats: (days: number, replay?: 'include' | 'exclude') =>
    call<import('./types').AuditStatsV1>(`/audit/stats${qs({ days: String(days), replay })}`),
  /** GET /audit/journey/{req} (compliance_officer). */
  journey: (reqId: string) => call<import('./types').JourneyV1>(`/audit/journey/${encodeURIComponent(reqId)}`),
  /** GET /ib/trace/{req}: the asker's receipt (own requests only; others and unknown: the same 404). */
  receipt: (reqId: string) => call<import('./types').TraceReceipt>(`/ib/trace/${encodeURIComponent(reqId)}`),
  /** GET /ib/status (any employee). */
  status: () => call<import('./types').IbStatus>('/ib/status'),
  /** GET /ib/history (own requests). */
  history: () => call<{ items: import('./types').HistoryItemV1[] }>('/ib/history'),
  /** GET /audit/events?kind=request.accepted (compliance): the most recent requests, one row each. */
  recentRequests: async (limit = 100): Promise<import('./types').RequestRow[]> => {
    const r = await call<{ events: RawRequestRow[] } | RawRequestRow[]>(`/audit/events${qs({ kind: 'request.accepted', limit: String(limit) })}`);
    const rows = Array.isArray(r) ? r : r.events ?? [];
    return rows
      .filter((x) => typeof x.req_id === 'string' && x.req_id !== '')
      .map((x) => {
        const e = x.event ?? {};
        return {
          req_id: x.req_id as string,
          sub: x.sub ?? str(e.sub),
          event_time: x.event_time ?? str(e.event_time),
          question: x.question ?? str(e.question),
          channel: x.channel ?? str(e.channel),
          synthetic_replay: !!x.synthetic_replay,
        };
      });
  },
  /** The evaluation summary shipped with the app (public/eval_summary.json). */
  evalSummary: async (): Promise<import('./types').EvalSummary> => {
    let res: Response;
    try {
      res = await fetch('/eval_summary.json', { cache: 'no-store' });
    } catch {
      throw new ApiError(0, 'network');
    }
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return (await res.json()) as import('./types').EvalSummary;
  },
};

// ---- user ----
// POST /ib/ask/stream (SSE, B12). Stage events arrive while the request runs; the answer arrives
// only after grounding (D-08), with exactly the /ib/ask JSON. EventSource cannot POST, so the
// stream is read with fetch + ReadableStream. Returns null when streaming is not available here
// (404/405, or the reply is not text/event-stream): the caller then uses api.ask.
export type AskStreamStage = 'accepted' | 'gate1' | 'gate2' | 'sealed' | 'composing' | 'grounded' | 'released';

export async function askStream(
  question: string,
  opts: { onStage?: (name: AskStreamStage) => void; slot?: Slot; signal?: AbortSignal } = {},
): Promise<AskResponse | null> {
  const headers: Record<string, string> = { 'content-type': 'application/json', accept: 'text/event-stream' };
  if (opts.slot && opts.slot !== 'a') headers['x-ib-slot'] = opts.slot;
  let res: Response;
  try {
    res = await fetch(`${BASE}/ib/ask/stream`, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers,
      body: JSON.stringify({ question }),
      signal: opts.signal ?? deadline(),
    });
  } catch {
    throw new ApiError(0, 'network');
  }
  if (res.status === 404 || res.status === 405) return null;
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
  if (!(res.headers.get('content-type') ?? '').includes('text/event-stream') || !res.body) return null;

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  const out: { answer: AskResponse | null } = { answer: null };
  const handle = (block: string): 'done' | void => {
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(':')) continue;
      const i = line.indexOf(':');
      const field = i === -1 ? line : line.slice(0, i);
      const value = i === -1 ? '' : line.slice(i + 1).replace(/^ /, '');
      if (field === 'event') event = value;
      else if (field === 'data') data.push(value);
    }
    let payload: unknown = null;
    try {
      payload = data.length ? JSON.parse(data.join('\n')) : null;
    } catch {
      payload = null;
    }
    if (event === 'stage') {
      const name = (payload as { name?: string } | null)?.name;
      if (name) opts.onStage?.(name as AskStreamStage);
    } else if (event === 'answer') {
      if (!payload || typeof payload !== 'object') throw new ApiError(0, 'network');
      out.answer = payload as AskResponse;
    } else if (event === 'error') {
      throw new ApiError(503, 'temporarily unavailable');
    } else if (event === 'done') {
      return 'done';
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buf += decoder.decode(value, { stream: true });
      let cut: RegExpExecArray | null;
      while ((cut = /\r?\n\r?\n/.exec(buf))) {
        const block = buf.slice(0, cut.index);
        buf = buf.slice(cut.index + cut[0].length);
        if (block.trim() && handle(block) === 'done') return finish(out.answer);
      }
      if (done) break;
    }
    if (buf.trim()) handle(buf);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, 'network');
  } finally {
    reader.cancel().catch(() => undefined);
  }
  return finish(out.answer);
}

function finish(a: AskResponse | null): AskResponse {
  if (!a) throw new ApiError(0, 'network');
  return a;
}

// ---- audit ----
import type { AuditAskV2, AuditRecordRef, AuditRow, TamperDemoResult } from './types';

// The audit console keeps each request's sealed records (seq + hash), which `groupByRequest`
// drops, so it has its own grouping. Rows already in request shape pass through unchanged.
type AuditLedgerRow = LedgerRow & { seq?: number; hash?: string };

export function groupAuditRows(rows: AuditLedgerRow[]): AuditRow[] {
  if (!rows.length || !rows.some((r) => typeof r.kind === 'string' && r.event)) return rows as unknown as AuditRow[];
  const byReq = new Map<string, Record<string, unknown> & { records: AuditRecordRef[] }>();
  const order: string[] = [];
  for (const r of rows) {
    const id = r.req_id;
    if (!id) continue;
    if (!byReq.has(id)) {
      byReq.set(id, { req_id: id, sub: r.sub, event_time: r.event_time, synthetic_replay: r.synthetic_replay, records: [] });
      order.push(id);
    }
    const acc = byReq.get(id)!;
    const e = (r.event ?? {}) as Record<string, unknown>;
    if (typeof r.seq === 'number' && typeof r.hash === 'string' && typeof r.kind === 'string') acc.records.push({ seq: r.seq, kind: r.kind, hash: r.hash });
    if (r.kind === 'request.accepted') Object.assign(acc, { question: e.question ?? r.question, channel: e.channel ?? r.channel, event_time: e.event_time ?? acc.event_time });
    else if (r.kind === 'retrieval.decided') Object.assign(acc, { considered: e.considered, considered_meta: e.considered_meta, gate2: e.gate2, released: e.released });
    else if (r.kind === 'answer.released') Object.assign(acc, { model: e.model, refused: e.refused, latency_ms: e.latency_ms });
    else if (r.kind === 'request.failed') Object.assign(acc, { refused: true });
  }
  return order.map((id) => {
    const acc = byReq.get(id)!;
    acc.records.sort((a, b) => a.seq - b.seq);
    return acc as unknown as AuditRow;
  });
}

export const auditApi = {
  events: async (f: AuditFilter): Promise<AuditRow[]> => {
    // The limit counts LEDGER EVENTS, not requests. The live ledger interleaves many other kinds
    // (ingest.sync, auth.login, graph.viewed, doc.viewed…), so the server default of 100 events left
    // only ~9 requests in the table. Ask for the API maximum (500) so the table holds ~100 requests.
    const r = await call<{ events: AuditLedgerRow[] } | AuditLedgerRow[]>(`/audit/events${qs({ limit: '500', ...f })}`);
    return groupAuditRows(Array.isArray(r) ? r : r.events ?? []);
  },
  /** POST /audit/ask: the interpreted filter, its rows, and which parser wrote the filter. */
  ask: async (question: string): Promise<AuditAskV2> => {
    const r = await call<Omit<AuditAskV2, 'events'> & { events?: AuditLedgerRow[] }>('/audit/ask', { method: 'POST', body: { question } });
    return { ...r, filter: r.filter ?? {}, events: groupAuditRows(r.events ?? []) };
  },
  /** POST /audit/tamper-demo (B11): the tamper scenarios on a sandbox copy. Never edits the real chain. */
  tamperDemo: () => call<TamperDemoResult>('/audit/tamper-demo', { method: 'POST', body: {} }),
};

// ---- graph ----
/** POST /ib/graph/openable response: which of the posted ids the CALLER can open now (request order). */
export interface OpenableResult {
  sub: string;
  as_of: string;
  openable: string[];
}

/**
 * The knowledge-graph calls bound to a session slot. Slot `a` is the main session; `b`/`c` are the
 * separately signed compare slots (the proxy allows ib/map, ib/graph and POST ib/graph/openable there,
 * not ib/doc). Identity always comes from the slot's own signed cookie: no call names a person.
 */
export function graphApi(slot: Slot = 'a') {
  const s = slot === 'a' ? undefined : slot;
  return {
    login: (persona: string) => demoLogin(persona, storedPasscode(), s),
    whoami: () => call<WhoAmI>('/auth/whoami', { slot: s }),
    graph: (query: GraphQuery = {}) =>
      call<KnowledgeGraph>(`/ib/graph${qs({ focus: query.focus, q: query.q, limit: query.limit ? String(query.limit) : undefined })}`, { slot: s }),
    map: () => (slot === 'a' ? api.map() : call<KnowledgeMap>('/ib/map', { slot: s })),
    openable: (docIds: string[]) => call<OpenableResult>('/ib/graph/openable', { method: 'POST', body: { doc_ids: docIds }, slot: s }),
  };
}

// ---- presenter ----
/** Sign one compare slot out (Presenter reset). The proxy renames the cleared cookie for the slot. */
export function slotLogout(slot: Exclude<Slot, 'a'>) {
  return call<{ ok: boolean }>('/auth/logout', { method: 'POST', body: {}, slot });
}
/** GET /health: is the Internal Brain reachable at all (the Presenter status dot). */
export function ibHealth() {
  return call<{ ok: boolean }>('/health');
}

// ---- act ----
/** Act on an answer. Identity is the session: no call names a person. 404 = nothing to act on
 *  (one answer for every reason); 409 = a ticket can't be created (one polite message). */
export const actApi = {
  draft: (reqId: string, kind: 'reply' | 'message' = 'reply') =>
    call<import('./types').ActDraft>('/ib/act/draft', { method: 'POST', body: { req_id: reqId, kind } }),
  proposeTicket: (reqId: string) =>
    call<import('./types').TicketProposal>('/ib/act/ticket/propose', { method: 'POST', body: { req_id: reqId } }),
  confirmTicket: (proposalId: string, edits: { summary?: string; project?: string } = {}) =>
    call<import('./types').TicketCreated>('/ib/act/ticket/confirm', { method: 'POST', body: { proposal_id: proposalId, ...edits } }),
  cancelTicket: (proposalId: string) =>
    call<{ status: 'cancelled'; action_id: string }>('/ib/act/ticket/cancel', { method: 'POST', body: { proposal_id: proposalId } }),
};

// ---- live-growth ----
/** Demo-proof features (D-24). Every route here is mounted only when the server runs with
 *  IB_DEMO_FEATURES=1; off, each is an ordinary 404. Identity is the session: nothing names a person. */
export const liveApi = {
  /** The permission-filtered change feed; `slot` picks the Compare session (x-ib-slot). */
  changes: (since: string | null, slot: Slot = 'a') =>
    call<import('./types').GraphChanges>(`/ib/graph/changes${since ? `?since=${encodeURIComponent(since)}` : ''}`, { slot: slot === 'a' ? undefined : slot }),
  /** Post a Slack note as the signed-in person (main session only). 404 = refused (one answer), 429 = too many. */
  addDocument: (body: import('./types').AddDocumentBody) =>
    call<import('./types').AddDocumentResult>('/sim/add-document', { method: 'POST', body }),
  uploadFile: (body: import('./types').UploadFileBody) =>
    call<import('./types').UploadFileResult>('/sim/upload-file', { method: 'POST', body }),
  engineRefresh: () => call<import('./types').EngineRefreshStatus>('/sim/engine-refresh', { method: 'POST', body: {} }),
  engineStatus: () => call<import('./types').EngineRefreshStatus>('/sim/engine-status', { method: 'POST', body: {} }),
};

// ---- just-in-time views of an answer (POST /ib/views; main session only) ----
export const viewApi = {
  /** A one-page view of MY answer, laid out as `instruction` asks (empty: the automatic layout). */
  create: (reqId: string, instruction: string) =>
    call<import('./types').JitView>('/ib/views', { method: 'POST', body: { req_id: reqId, instruction } }),
  /** My message as a standalone search question (my previous question as context). */
  rewrite: (question: string, previous: string) =>
    call<{ question: string; rewritten: boolean }>('/ib/rewrite', { method: 'POST', body: { question: question.slice(0, 2000), previous: previous.slice(0, 2000) } }),
  /** Page or chat for MY answer: Claude decides when connected, else the rules (`question` as asked). */
  decide: (reqId: string, question: string) =>
    call<import('./types').JitDecision>('/ib/views', { method: 'POST', body: { req_id: reqId, instruction: question.slice(0, 300), mode: 'decide' } }),
};

// ---- deliverables from an answer (POST /ib/deliverables; main session only) ----
export const deliverApi = {
  /** A table, chart or analysis from one of MY released answers. 404 = nothing to build (one answer). */
  create: (reqId: string, kind: import('./types').DeliverableKind) =>
    call<import('./types').Deliverable>('/ib/deliverables', { method: 'POST', body: { req_id: reqId, kind } }),
  /** The file, built on the server from the stored, checked deliverable. Name from the server's
   *  fixed-shape `content-disposition`, else a fixed fallback. */
  exportFile: async (deliverableId: string, format: 'csv' | 'pptx'): Promise<{ blob: Blob; name: string }> => {
    let res: Response;
    try {
      res = await fetch(`${BASE}/ib/deliverables/export`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ deliverable_id: deliverableId, format }),
        signal: deadline(),
      });
    } catch {
      throw new ApiError(0, 'network');
    }
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    const m = /filename="(walnut-[a-z]+-[0-9a-f]{8}-\d{8}\.(?:pptx|csv))"/.exec(res.headers.get('content-disposition') ?? '');
    return { blob: await res.blob(), name: m ? m[1] : `walnut-deliverable.${format}` };
  },
};
