// Same-origin gateway for the browser: `/api/ib/<path>` → IB API `<IB_API_URL>/<path>`.
//
// - IB_API_URL is read at runtime on the server; it is never exposed to the browser bundle.
// - Only the IB session cookie is forwarded upstream, and only that cookie is relayed back
//   (httpOnly, Path=/, no Domain), so no token is ever visible to JavaScript.
// - A closed path allowlist: the browser can reach only the routes the UI uses (no /webhooks).
//   Segments must be plain names (no "", ".", "..", "/" or "\"), and the outbound URL is checked
//   again after the URL parser has had its say, so normalisation cannot reach another route.
// - POSTs must come from this site: a browser-sent Origin must match our own host, and
//   Sec-Fetch-Site (when sent) must be same-origin. A body must be JSON and at most 64 KiB.
// - Upstream 5xx bodies are replaced by a small fixed envelope (the IB's own 503 JSON is kept).
// - In mock mode (NEXT_PUBLIC_IB_MOCK=1 or IB_MOCK=1) fixtures are served instead.
// - Compare slots: the "Same question, two people" page signs two personas in side by side without
//   touching the main session. A request with `x-ib-slot: b` (or `c`) forwards cookie
//   `ib_session_b` (or `ib_session_c`) INSTEAD of `ib_session`, and a demo-login made in that slot
//   has its Set-Cookie renamed to the slot cookie. The header only chooses which signed cookie to
//   forward; identity still comes solely from the token the IB minted. Slots reach only the few
//   routes the compare page needs, never /audit or /sim. Any other header value is a 400.
import { NextRequest, NextResponse } from 'next/server';
import { handleMock } from '@/lib/mock/server';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

// Document ids look like `confluence:123`, `jira:DBMIG-7`, `slack:C0123:1712345678.000100`.
const DOC_ID = '[A-Za-z0-9._:-]+';

const ALLOWED: { method: 'GET' | 'POST'; re: RegExp }[] = [
  { method: 'GET', re: /^health$/ },
  { method: 'POST', re: /^auth\/(demo-login|logout)$/ },
  { method: 'GET', re: /^auth\/whoami$/ },
  { method: 'POST', re: /^ib\/ask(\/stream)?$/ },
  { method: 'GET', re: /^ib\/(history|sources|map|status)$/ },
  { method: 'GET', re: /^ib\/trace\/[A-Za-z0-9_-]{1,200}$/ },
  { method: 'GET', re: /^ib\/graph$/ },
  { method: 'GET', re: /^ib\/graph\/changes$/ },
  { method: 'POST', re: /^ib\/graph\/openable$/ },
  // Act on an answer: main session only (never in a compare slot)
  { method: 'POST', re: /^ib\/act\/(draft|ticket\/(propose|confirm|cancel))$/ },
  // A ticket's opaque source link (Act v3.1): main session only
  { method: 'GET', re: /^ib\/open\/[0-9a-f]{32}$/ },
  // Deliverables from an answer (IB_DELIVERABLES=1): main session only (never in a compare slot)
  { method: 'POST', re: /^ib\/deliverables(\/export)?$/ },
  // Just-in-time views of an answer (same switch as deliverables): main session only
  { method: 'POST', re: /^ib\/views$/ },
  // A follow-up rewritten into a standalone search question (the model; main session only)
  { method: 'POST', re: /^ib\/rewrite$/ },
  { method: 'GET', re: new RegExp(`^ib\\/doc\\/${DOC_ID}$`) },
  { method: 'GET', re: /^audit\/(events|verify|stats)$/ },
  { method: 'GET', re: /^audit\/journey\/[A-Za-z0-9_-]{1,200}$/ },
  { method: 'GET', re: new RegExp(`^audit\\/doc\\/${DOC_ID}\\/accessors$`) },
  { method: 'POST', re: /^audit\/(ask|reveal|tamper-demo)$/ },
  { method: 'POST', re: /^sim\/[a-z][a-z-]*$/ },
  // The upload sheet's folder picker (D-34): main session only, like sim/upload-file
  { method: 'GET', re: /^sim\/upload-folders$/ },
];

const COOKIE = 'ib_session';
const MAX_BODY = 64 * 1024;
// The one exception: a demo file upload (JSON with base64 content, a 5 MB file is ~6.7 MB encoded).
const MAX_UPLOAD_BODY = 7 * 1024 * 1024;
const UPLOAD_PATH = 'sim/upload-file';
const DEADLINE_MS = 90_000;
const UNAVAILABLE = 'The service is unavailable. Please try again.';

type Slot = 'a' | 'b' | 'c';
const SLOT_COOKIE: Record<Slot, string> = { a: COOKIE, b: `${COOKIE}_b`, c: `${COOKIE}_c` };
const SLOT_ALLOWED: { method: 'GET' | 'POST'; re: RegExp }[] = [
  { method: 'POST', re: /^auth\/(demo-login|logout)$/ },
  { method: 'GET', re: /^auth\/whoami$/ },
  { method: 'POST', re: /^ib\/ask(\/stream)?$/ },
  { method: 'GET', re: /^ib\/map$/ },
  { method: 'GET', re: /^ib\/graph$/ },
  { method: 'GET', re: /^ib\/graph\/changes$/ },
  { method: 'POST', re: /^ib\/graph\/openable$/ },
];

const fail = (status: number, detail: string) => NextResponse.json({ detail }, { status, headers: { 'cache-control': 'no-store' } });

function allowed(list: typeof ALLOWED, method: string, path: string): boolean {
  return list.some((a) => a.method === method && a.re.test(path));
}

/** Plain path names only: nothing the URL parser would fold away or split. */
function cleanSegments(segments: string[]): boolean {
  return segments.length > 0 && segments.every((s) => s !== '' && s !== '.' && s !== '..' && !/[/\\]/.test(s));
}

/** Absent header → the main session. Anything but a, b or c is refused rather than guessed. */
function slotOf(req: NextRequest): Slot | null {
  const raw = req.headers.get('x-ib-slot');
  if (raw === null) return 'a';
  const v = raw.trim().toLowerCase();
  return v === 'a' || v === 'b' || v === 'c' ? v : null;
}

/**
 * CSRF guard for POSTs. Browsers always send Origin on a cross-origin POST and Sec-Fetch-Site on
 * every request, so a different Origin or a cross-site / same-site fetch is refused. Tools that
 * send neither (curl, Playwright's request API) are not browsers carrying someone's cookies.
 * The host is compared rather than the full origin so a TLS-terminating proxy cannot cause a
 * scheme mismatch for real users.
 */
function sameOrigin(req: NextRequest): boolean {
  const site = req.headers.get('sec-fetch-site');
  if (site !== null && site !== 'same-origin' && site !== 'none') return false;
  const origin = req.headers.get('origin');
  if (origin === null) return true;
  let given: string;
  try {
    given = new URL(origin).origin.toLowerCase();
  } catch {
    return false; // includes "null"
  }
  // This site's own origin: the scheme it is served on plus the Host the browser used. Behind a TLS
  // proxy set IB_PUBLIC_ORIGIN (e.g. https://brain.example.com). Forwarded headers are never used.
  const host = req.headers.get('host');
  const own = (process.env.IB_PUBLIC_ORIGIN || (host ? `${req.nextUrl.protocol}//${host}` : ''))
    .replace(/\/+$/, '')
    .toLowerCase();
  return own !== '' && given === own;
}

class TooLarge extends Error {}

/** Read the request body, at most MAX_BODY bytes, giving up when the deadline fires. */
async function readBody(req: NextRequest, signal: AbortSignal, max: number = MAX_BODY): Promise<Uint8Array> {
  if (Number(req.headers.get('content-length') ?? 0) > max) throw new TooLarge();
  if (!req.body) return new Uint8Array(0);
  const reader = req.body.getReader();
  const stop = () => void reader.cancel().catch(() => undefined);
  signal.addEventListener('abort', stop, { once: true });
  const parts: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > max) {
        stop();
        throw new TooLarge();
      }
      parts.push(value);
    }
  } finally {
    signal.removeEventListener('abort', stop);
  }
  if (signal.aborted) throw new Error('deadline');
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.byteLength;
  }
  return out;
}

const KEEP_ATTR = /^(httponly|secure|samesite|max-age|expires|partitioned)$/i;

/**
 * Re-emit only the IB session cookie, renamed for the slot. Any other cookie name is dropped;
 * Domain and Path are replaced by this site's `Path=/`; expiry and security flags are kept.
 */
function relayCookies(from: string[], to: NextResponse, slot: Slot) {
  for (const c of from) {
    const [pair, ...attrs] = c.split(';');
    const eq = pair.indexOf('=');
    if (eq < 0 || pair.slice(0, eq).trim() !== COOKIE) continue;
    const kept = attrs.map((a) => a.trim()).filter((a) => KEEP_ATTR.test(a.split('=')[0].trim()) && !/^httponly$/i.test(a));
    to.headers.append('set-cookie', [`${SLOT_COOKIE[slot]}=${pair.slice(eq + 1).trim()}`, 'Path=/', 'HttpOnly', ...kept].join('; '));
  }
}

/** The IB's own fail-closed 503 messages, the only 5xx texts the browser may see. */
const KNOWN_503 = new Set(['temporarily unavailable', 'sign-out could not be recorded; try again']);

/** The known message, if the body is exactly one of them; it is re-sent as fresh JSON, never relayed. */
function knownUnavailable(body: ArrayBuffer): string | null {
  try {
    const j = JSON.parse(new TextDecoder().decode(body));
    return j && typeof j.error === 'string' && Object.keys(j).length === 1 && KNOWN_503.has(j.error) ? j.error : null;
  } catch {
    return null;
  }
}

function isMock(): boolean {
  return process.env.NEXT_PUBLIC_IB_MOCK === '1' || process.env.IB_MOCK === '1';
}

/**
 * DNS-rebinding guard: the Host must be this machine (or IB_PUBLIC_ORIGIN's host when deployed), so a page
 * from another site that rebinds its name to 127.0.0.1 is refused before anything is routed.
 */
function allowedHost(req: NextRequest): boolean {
  const host = (req.headers.get('host') ?? '').trim().toLowerCase();
  const name = host.startsWith('[') ? host.slice(0, host.indexOf(']') + 1) : host.split(':')[0];
  if (name === 'localhost' || name === '127.0.0.1' || name === '[::1]') return true;
  const pub = process.env.IB_PUBLIC_ORIGIN;
  if (!pub) return false;
  try {
    return new URL(pub).hostname.toLowerCase() === name;
  } catch {
    return false;
  }
}

/** GETs can write view records (doc.viewed, graph.viewed): refuse cross-site and same-site browser fetches. */
function getFromThisSite(req: NextRequest): boolean {
  const site = req.headers.get('sec-fetch-site');
  return site === null || site === 'same-origin' || site === 'none';
}

// A ticket's opaque source link is clicked from the Jira tab (another site), so it is the one GET
// allowed from elsewhere: a top-level navigation only. Its only effects are a doc.viewed record and a
// 303 to a source the signed-in person can open now (Gate 1 + Gate 2 in the IB); anything else is
// the IB's one 404.
// Round-4 Opus #5: when the browser sends Sec-Fetch headers, the request must be a top-level document
// navigation the person clicked (Dest document, Mode navigate, User ?1). A script's `location = …`,
// window.open without a click, an iframe, an <img> or a fetch gets the open route's one 404 instead.
// No Sec-Fetch headers at all (curl, tests, an old browser): left to the IB, as before.
const OPEN_LINK = /^ib\/open\/[0-9a-f]{32}$/;
function openNavigation(req: NextRequest): boolean {
  const dest = req.headers.get('sec-fetch-dest');
  const mode = req.headers.get('sec-fetch-mode');
  const site = req.headers.get('sec-fetch-site');
  const user = req.headers.get('sec-fetch-user');
  if (dest === null && mode === null && site === null && user === null) return true;
  return dest === 'document' && mode === 'navigate' && user === '?1';
}

/** The IB's 303 for an open link: relayed only on that route, only to an http(s) URL. */
function openLocation(path: string, upstream: Response): string | null {
  if (!OPEN_LINK.test(path) || upstream.status !== 303) return null;
  const loc = upstream.headers.get('location') ?? '';
  try {
    const u = new URL(loc);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null;
  } catch {
    return null;
  }
}

async function handle(req: NextRequest, ctx: { params: { path: string[] } }): Promise<NextResponse> {
  // One deadline for the whole exchange, started before the incoming body is read.
  const signal = AbortSignal.timeout(DEADLINE_MS);
  const segments = ctx.params.path ?? [];
  const path = segments.join('/');
  const method = req.method.toUpperCase();
  if (!cleanSegments(segments)) return fail(400, 'bad request');
  if (!allowedHost(req)) return fail(403, 'forbidden');
  if (method === 'GET' && OPEN_LINK.test(path)) {
    if (!openNavigation(req)) return fail(404, 'not found');
  } else if (method === 'GET' && !getFromThisSite(req)) return fail(403, 'forbidden');
  if (!allowed(ALLOWED, method, path)) return fail(404, 'not found');
  const slot = slotOf(req);
  if (slot === null) return fail(400, 'bad request');
  if (slot !== 'a' && !allowed(SLOT_ALLOWED, method, path)) return fail(404, 'not found');

  let body: Uint8Array | undefined;
  if (method === 'POST') {
    if (!sameOrigin(req)) return fail(403, 'forbidden');
    try {
      body = await readBody(req, signal, path === UPLOAD_PATH ? MAX_UPLOAD_BODY : MAX_BODY);
    } catch (e) {
      if (e instanceof TooLarge) return fail(413, 'too large');
      return NextResponse.json({ error: 'temporarily unavailable' }, { status: 503 });
    }
    const ct = (req.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
    if ((body.byteLength > 0 || ct) && ct !== 'application/json') return fail(415, 'unsupported media type');
  }
  const session = req.cookies.get(SLOT_COOKIE[slot])?.value;

  if (isMock()) {
    // The body has been read already, so the mock gets a fresh request carrying the same bytes.
    const mreq = body ? new NextRequest(req.url, { method, headers: req.headers, body }) : req;
    const mock = await handleMock(mreq, path, session);
    const out = new NextResponse(mock.body, { status: mock.status });
    mock.headers.forEach((v, k) => {
      if (k !== 'set-cookie') out.headers.set(k, v);
    });
    relayCookies(mock.headers.getSetCookie(), out, slot);
    return out;
  }

  const base = new URL((process.env.IB_API_URL || 'http://127.0.0.1:8100').replace(/\/+$/, ''));
  const basePath = base.pathname.replace(/\/+$/, '');
  const encoded = segments.map(encodeURIComponent).join('/');
  const target = new URL(`${base.origin}${basePath}/${encoded}${req.nextUrl.search}`);
  // Check what will actually be fetched: same host, same path we validated, still allowlisted.
  const outPath = target.pathname.startsWith(`${basePath}/`) ? target.pathname.slice(basePath.length + 1) : '';
  if (target.origin !== base.origin || outPath !== encoded || !allowed(ALLOWED, method, outPath.split('/').map(decodeURIComponent).join('/'))) {
    return fail(404, 'not found');
  }
  const headers = new Headers({ accept: 'application/json' });
  if (body?.byteLength) headers.set('content-type', 'application/json');
  // Upstream always sees the IB's own cookie name; which browser cookie fills it depends on the slot.
  if (session) headers.set('cookie', `${COOKIE}=${session}`);

  const streaming = path === 'ib/ask/stream';
  if (streaming) headers.set('accept', 'text/event-stream');
  let upstream: Response;
  let payload: ArrayBuffer;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body: body?.byteLength ? body : undefined,
      cache: 'no-store',
      redirect: 'manual',
      signal,
    });
    // Server-sent stage events: passed through unbuffered. The IB sends only stage names, then the
    // same grounded answer JSON /ib/ask returns (D-08, rule 4), so nothing needs rewriting here.
    if (streaming && upstream.ok && (upstream.headers.get('content-type') ?? '').startsWith('text/event-stream') && upstream.body) {
      const out = new NextResponse(upstream.body as ReadableStream, { status: 200 });
      out.headers.set('content-type', 'text/event-stream; charset=utf-8');
      out.headers.set('cache-control', 'no-store');
      out.headers.set('x-accel-buffering', 'no');
      relayCookies(upstream.headers.getSetCookie(), out, slot);
      return out;
    }
    payload = await upstream.arrayBuffer();
  } catch {
    // Upstream unreachable, too slow, or cut off mid-body: the same envelope the IB itself uses
    // for a fail-closed 503.
    return NextResponse.json({ error: 'temporarily unavailable' }, { status: 503 });
  }

  let out: NextResponse;
  const location = openLocation(path, upstream);
  if (location) {
    out = new NextResponse(null, { status: 303 });
    out.headers.set('location', location);
    out.headers.set('cache-control', 'no-store');
    return out;
  }
  if (upstream.status >= 500) {
    const known = upstream.status === 503 ? knownUnavailable(payload) : null;
    out = NextResponse.json({ error: known ?? UNAVAILABLE }, { status: upstream.status });
  } else {
    out = new NextResponse(payload, { status: upstream.status });
    const uct = upstream.headers.get('content-type');
    if (uct) out.headers.set('content-type', uct);
    // The deliverables export only: relay the file name the IB built from fixed parts, and only
    // when it is exactly that shape (nothing else from upstream; never on another route).
    const disposition = exportDisposition(path, upstream);
    if (disposition) {
      out.headers.set('content-disposition', disposition);
      out.headers.set('x-content-type-options', 'nosniff');
    }
  }
  out.headers.set('cache-control', 'no-store');
  relayCookies(upstream.headers.getSetCookie(), out, slot);
  return out;
}

/** The export route's `content-disposition`, accepted only in this exact fixed shape. */
const EXPORT_DISPOSITION = /^attachment; filename="walnut-[a-z]+-[0-9a-f]{8}-\d{8}\.(pptx|csv)"$/;

function exportDisposition(path: string, upstream: Response): string | null {
  if (path !== 'ib/deliverables/export' || upstream.status !== 200) return null;
  const v = upstream.headers.get('content-disposition');
  return v && EXPORT_DISPOSITION.test(v) ? v : null;
}

export { handle as GET, handle as POST };
