// Same-origin gateway for the browser: `/api/ib/<path>` → IB API `<IB_API_URL>/<path>`.
//
// - IB_API_URL is read at runtime on the server; it is never exposed to the browser bundle.
// - Only the IB session cookie is forwarded upstream, and Set-Cookie (httpOnly) is relayed back,
//   so no token is ever visible to JavaScript.
// - A closed path allowlist: the browser can reach only the routes the UI uses (no /webhooks).
// - In mock mode (NEXT_PUBLIC_IB_MOCK=1 or IB_MOCK=1) fixtures are served instead.
import { type NextRequest, NextResponse } from 'next/server';
import { handleMock } from '@/lib/mock/server';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const ALLOWED: { method: 'GET' | 'POST'; re: RegExp }[] = [
  { method: 'GET', re: /^health$/ },
  { method: 'POST', re: /^auth\/(demo-login|logout)$/ },
  { method: 'GET', re: /^auth\/whoami$/ },
  { method: 'POST', re: /^ib\/ask$/ },
  { method: 'GET', re: /^ib\/(history|sources)$/ },
  { method: 'GET', re: /^audit\/(events|verify)$/ },
  { method: 'GET', re: /^audit\/doc\/[^/]+\/accessors$/ },
  { method: 'POST', re: /^audit\/(ask|reveal)$/ },
  { method: 'POST', re: /^sim\/[a-z][a-z-]*$/ },
];

const COOKIE = 'ib_session';

function isMock(): boolean {
  return process.env.NEXT_PUBLIC_IB_MOCK === '1' || process.env.IB_MOCK === '1';
}

async function handle(req: NextRequest, ctx: { params: { path: string[] } }): Promise<NextResponse> {
  const segments = ctx.params.path ?? [];
  const path = segments.join('/');
  const method = req.method.toUpperCase();
  if (!ALLOWED.some((a) => a.method === method && a.re.test(path))) {
    return NextResponse.json({ detail: 'not found' }, { status: 404 });
  }
  if (isMock()) return handleMock(req, path);

  const base = (process.env.IB_API_URL || 'http://127.0.0.1:8100').replace(/\/+$/, '');
  const url = `${base}/${segments.map(encodeURIComponent).join('/')}${req.nextUrl.search}`;
  const headers = new Headers({ accept: 'application/json' });
  const ct = req.headers.get('content-type');
  if (ct) headers.set('content-type', ct);
  const session = req.cookies.get(COOKIE)?.value;
  if (session) headers.set('cookie', `${COOKIE}=${session}`);

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method,
      headers,
      body: method === 'GET' ? undefined : await req.arrayBuffer(),
      cache: 'no-store',
      redirect: 'manual',
      signal: AbortSignal.timeout(90_000),
    });
  } catch {
    // Upstream unreachable: the same envelope the IB itself uses for a fail-closed 503.
    return NextResponse.json({ error: 'temporarily unavailable' }, { status: 503 });
  }

  const out = new NextResponse(await upstream.arrayBuffer(), { status: upstream.status });
  const uct = upstream.headers.get('content-type');
  if (uct) out.headers.set('content-type', uct);
  out.headers.set('cache-control', 'no-store');
  for (const c of upstream.headers.getSetCookie()) out.headers.append('set-cookie', c);
  return out;
}

export { handle as GET, handle as POST };
