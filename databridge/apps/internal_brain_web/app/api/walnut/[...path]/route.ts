// Read-only proxy for the Walnut engine view (/dev/brain): `/api/walnut/<path>` → the Walnut gateway
// on loopback :8300 (DECISIONS D-09, D-21; reviews/plan_opus.md finding 2; plan_haiku.md 1–2).
//
// The gateway has ~86 write routes (MCP server registration, model calls, ingest, reload) and no
// per-user access checks: its graph was built at sync time as ONE person's view (jdoe). A proxy that
// adds the gateway token server-side would remove the gateway's only gate, so this one is closed:
//
//  1. Off unless WALNUT_PROXY=1, and always off when IB_DEPLOYED=1. Off answers the same 404 as an
//     unknown path, so nothing tells a caller the route exists.
//  2. GET only (every other method is 405), on an exact allowlist of the read paths the screens use.
//     Never /brain/query, /brain/ingest, /graph/reload, /mcp/*, /intelligence/*, /federated/* or
//     any POST. Query parameters are allowlisted per path and rebuilt, never passed through raw.
//  3. The Host must be localhost / 127.0.0.1 / [::1] (DNS rebinding), and a browser fetch must be
//     same-origin (Sec-Fetch-Site).
//  4. A valid Internal Brain session is required: the IB's GET /auth/whoami is called server-side
//     with the ib_session cookie. Only the persona the graph was built for (WALNUT_GRAPH_PERSONA,
//     default jdoe) may read the snapshot (not live-checked, reads not ledgered); everyone else gets 403.
//  5. The upstream is fixed to http://127.0.0.1:8300. The token comes from the server-only
//     WALNUT_GATEWAY_TOKEN (never NEXT_PUBLIC_*). The browser's Cookie and Authorization headers are
//     never forwarded, and no upstream header (Set-Cookie included) is relayed.
//  6. Same segment cleaning as the IB proxy; 10 s deadline; 5 MB response cap; JSON only; the
//     local file paths the gateway puts in `graph_source` / `packs_root` / `graph_db_path` are
//     dropped; upstream 5xx and credential failures become one fixed 503 envelope.
//  7. In mock mode (NEXT_PUBLIC_IB_MOCK=1 or IB_MOCK=1) every check above still runs, and the
//     answer comes from components/engine/mock.ts instead of the gateway.
import { NextRequest, NextResponse } from 'next/server';
import { engineMock } from '@/components/engine/mock';
import { handleMock } from '@/lib/mock/server';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const UPSTREAM = 'http://127.0.0.1:8300';
const DEADLINE_MS = 10_000;
const WHOAMI_DEADLINE_MS = 5_000;
const MAX_RESPONSE = 5 * 1024 * 1024;
const COOKIE = 'ib_session';

/** Node and edge ids: `knowledge_page:cf-1001`, `colleague:nina-k|wrote_item|knowledge_page:cf-1001`. */
const ID = '[A-Za-z0-9_.:|-]{1,200}';
const INT = /^\d{1,4}$/;
const ISO = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?)?$/;
const ID_RE = new RegExp(`^${ID}$`);

type ParamRule = (v: string) => boolean;
const intIn = (lo: number, hi: number): ParamRule => (v) => INT.test(v) && Number(v) >= lo && Number(v) <= hi;
const searchText: ParamRule = (v) => v.trim().length > 0 && v.length <= 100 && !/[\u0000-\u001f\u007f]/.test(v);

/** The closed allowlist: exact read paths, each with the only query parameters it may carry. */
const ALLOWED: { re: RegExp; params: Record<string, ParamRule> }[] = [
  { re: /^graph\/facets$/, params: {} },
  { re: /^graph\/neighborhood$/, params: { node: (v) => ID_RE.test(v), depth: intIn(1, 2), limit: intIn(1, 200) } },
  { re: /^graph\/search$/, params: { q: searchText, limit: intIn(1, 25) } },
  { re: new RegExp(`^graph\\/node\\/${ID}$`), params: {} },
  { re: new RegExp(`^graph\\/edge\\/${ID}$`), params: {} },
  { re: /^brain\/graph$/, params: { limit: intIn(1, 300) } },
  { re: new RegExp(`^brain\\/timeline\\/${ID}$`), params: {} },
  { re: new RegExp(`^brain\\/node\\/${ID}$`), params: {} },
  { re: new RegExp(`^brain\\/node\\/${ID}\\/sheet$`), params: { as_of: (v) => ISO.test(v) } },
  { re: new RegExp(`^evidence\\/trace\\/${ID}$`), params: {} },
  { re: /^ontology$/, params: {} },
  { re: /^ontology\/packs$/, params: {} },
  // The pack registry: the graph's node colours and captions are pack data (graph_vocabulary).
  { re: /^packs$/, params: {} },
];

/** Top-level keys that carry this machine's file paths; never shown in the browser. */
const LOCAL_PATH_KEYS = ['graph_source', 'packs_root', 'graph_db_path'];

const NO_STORE = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };
const json = (body: unknown, status: number) => NextResponse.json(body, { status, headers: NO_STORE });
const notFound = () => json({ detail: 'not found' }, 404);
const unavailable = () => json({ error: 'engine unavailable' }, 503);

function enabled(): boolean {
  return process.env.WALNUT_PROXY === '1' && process.env.IB_DEPLOYED !== '1';
}

function isMock(): boolean {
  return process.env.NEXT_PUBLIC_IB_MOCK === '1' || process.env.IB_MOCK === '1';
}

/** Plain path names only: nothing the URL parser would fold away or split. */
function cleanSegments(segments: string[]): boolean {
  return segments.length > 0 && segments.every((s) => s !== '' && s !== '.' && s !== '..' && !/[/\\]/.test(s));
}

/** The Host the browser used must be this machine's loopback name (defeats DNS rebinding). */
function loopbackHost(req: NextRequest): boolean {
  const host = (req.headers.get('host') ?? '').trim().toLowerCase();
  const name = host.startsWith('[') ? host.slice(0, host.indexOf(']') + 1) : host.split(':')[0];
  return name === 'localhost' || name === '127.0.0.1' || name === '[::1]';
}

/** A browser fetch from another site (or a sibling site) is refused; curl and tests send no header. */
function sameSite(req: NextRequest): boolean {
  const site = req.headers.get('sec-fetch-site');
  return site === null || site === 'same-origin' || site === 'none';
}

/** Validate and rebuild the query string. Unknown, repeated or malformed parameters are refused. */
function cleanQuery(req: NextRequest, rules: Record<string, ParamRule>): URLSearchParams | null {
  const out = new URLSearchParams();
  const seen = new Set<string>();
  for (const [k, v] of req.nextUrl.searchParams) {
    const rule = rules[k];
    if (!rule || seen.has(k) || !rule(v)) return null;
    seen.add(k);
    out.set(k, v);
  }
  return out;
}

interface Who {
  sub: string;
  roles: string[];
}

/** Who is signed in to the Internal Brain, per the IB itself. null = no valid session; 'down' = IB unreachable. */
async function whoami(req: NextRequest, session: string): Promise<Who | null | 'down'> {
  try {
    let status: number;
    let body: unknown;
    if (isMock()) {
      const mreq = new NextRequest(new URL('/api/ib/auth/whoami', req.nextUrl.origin), { method: 'GET' });
      const r = await handleMock(mreq, 'auth/whoami', session);
      status = r.status;
      body = await r.json().catch(() => null);
    } else {
      const base = (process.env.IB_API_URL || 'http://127.0.0.1:8100').replace(/\/+$/, '');
      const r = await fetch(`${base}/auth/whoami`, {
        method: 'GET',
        headers: { accept: 'application/json', cookie: `${COOKIE}=${session}` },
        cache: 'no-store',
        redirect: 'manual',
        signal: AbortSignal.timeout(WHOAMI_DEADLINE_MS),
      });
      status = r.status;
      body = await r.json().catch(() => null);
    }
    if (status === 401 || status === 403) return null;
    if (status !== 200 || !body || typeof body !== 'object') return 'down';
    const b = body as { sub?: unknown; roles?: unknown };
    if (typeof b.sub !== 'string' || !Array.isArray(b.roles)) return 'down';
    return { sub: b.sub, roles: b.roles.filter((r): r is string => typeof r === 'string') };
  } catch {
    return 'down';
  }
}

/** Least privilege: only the persona the snapshot was built for. */
function mayRead(who: Who): boolean {
  const persona = (process.env.WALNUT_GRAPH_PERSONA || 'jdoe').trim();
  // Only the persona the snapshot was built for. The snapshot is never Gate-2 checked and engine reads are
  // not ledgered, so nobody else (compliance included) may read another person's view through it.
  return who.sub === persona;
}

/** Read at most MAX_RESPONSE bytes of the upstream body. */
async function readCapped(res: Response): Promise<Uint8Array | null> {
  if (Number(res.headers.get('content-length') ?? 0) > MAX_RESPONSE) return null;
  if (!res.body) return new Uint8Array(0);
  const reader = res.body.getReader();
  const parts: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_RESPONSE) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    parts.push(value);
  }
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.byteLength;
  }
  return out;
}

function dropLocalPaths(body: unknown): unknown {
  if (body && typeof body === 'object' && !Array.isArray(body)) {
    const copy = { ...(body as Record<string, unknown>) };
    for (const k of LOCAL_PATH_KEYS) delete copy[k];
    return copy;
  }
  return body;
}

async function handleGet(req: NextRequest, ctx: { params: { path: string[] } }): Promise<NextResponse> {
  if (!enabled()) return notFound();
  if (!loopbackHost(req) || !sameSite(req)) return json({ detail: 'forbidden' }, 403);

  const segments = ctx.params.path ?? [];
  if (!cleanSegments(segments)) return notFound();
  const path = segments.join('/');
  const rule = ALLOWED.find((a) => a.re.test(path));
  if (!rule) return notFound();
  const query = cleanQuery(req, rule.params);
  if (!query) return json({ detail: 'bad request' }, 400);

  // The browser's own Cookie header is read only for the IB session; it never goes upstream.
  const session = req.cookies.get(COOKIE)?.value;
  if (!session) return json({ detail: 'authentication required' }, 401);
  const who = await whoami(req, session);
  if (who === 'down') return json({ error: 'temporarily unavailable' }, 503);
  if (who === null) return json({ detail: 'authentication required' }, 401);
  if (!mayRead(who)) return json({ detail: 'forbidden' }, 403);

  if (isMock()) {
    const m = engineMock(path, query);
    return NextResponse.json(m.body, { status: m.status, headers: { ...NO_STORE, 'x-walnut-mock': '1' } });
  }

  const encoded = segments.map(encodeURIComponent).join('/');
  const search = query.toString();
  const target = new URL(`${UPSTREAM}/${encoded}${search ? `?${search}` : ''}`);
  // Check what will actually be fetched: same origin, same path we validated.
  if (target.origin !== UPSTREAM || target.pathname !== `/${encoded}`) return notFound();

  const token = process.env.WALNUT_GATEWAY_TOKEN || 'dev-local-only';
  let upstream: Response;
  let bytes: Uint8Array | null;
  try {
    upstream = await fetch(target, {
      method: 'GET',
      headers: { accept: 'application/json', authorization: `Bearer ${token}` },
      cache: 'no-store',
      redirect: 'manual',
      signal: AbortSignal.timeout(DEADLINE_MS),
    });
    bytes = await readCapped(upstream);
  } catch {
    return unavailable();
  }
  if (bytes === null) return json({ error: 'engine response too large' }, 502);
  // A gateway that refuses our own credential, fails, or redirects is "unavailable", never relayed.
  if (upstream.status >= 500 || upstream.status === 401 || upstream.status === 403 || (upstream.status >= 300 && upstream.status < 400)) {
    return unavailable();
  }
  const ct = (upstream.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
  if (ct !== 'application/json') return unavailable();
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return unavailable();
  }
  // 200s and the gateway's typed refusals (e.g. 404 UNKNOWN_ENTITY) pass through with their status.
  return NextResponse.json(dropLocalPaths(body), { status: upstream.status, headers: NO_STORE });
}

const methodNotAllowed = () => NextResponse.json({ detail: 'method not allowed' }, { status: 405, headers: { ...NO_STORE, allow: 'GET' } });

export { handleGet as GET };
export const POST = methodNotAllowed;
export const PUT = methodNotAllowed;
export const PATCH = methodNotAllowed;
export const DELETE = methodNotAllowed;
