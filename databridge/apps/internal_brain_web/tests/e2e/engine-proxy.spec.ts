import http from 'node:http';
import { expect, test, type APIRequestContext } from '@playwright/test';

// Guards of the read-only Walnut engine proxy (app/api/walnut/[...path]/route.ts; D-21, Opus #2).
//
// The proxy is OFF unless the server runs with WALNUT_PROXY=1. Run this file against a server
// started in the matching mode and tell the spec which one with ENGINE_PROXY:
//   ENGINE_PROXY=on   server: NEXT_PUBLIC_IB_MOCK=1 WALNUT_PROXY=1 next dev   (mock fixture)
//   ENGINE_PROXY=off  server: NEXT_PUBLIC_IB_MOCK=1 next dev                   (no WALNUT_PROXY)
// The default suite build (build:mock, no WALNUT_PROXY) is the "off" case.
const MODE = (process.env.ENGINE_PROXY ?? 'off').toLowerCase();

/** A GET sent byte-for-byte with chosen headers (Playwright folds dot segments and sets Host). */
function rawGet(baseURL: string, path: string, headers: Record<string, string> = {}): Promise<{ status: number; body: string }> {
  const u = new URL(baseURL);
  return new Promise((resolve, reject) => {
    const r = http.request({ host: u.hostname, port: u.port, path, method: 'GET', headers }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => resolve({ status: res.statusCode ?? 0, body }));
    });
    r.on('error', reject);
    r.end();
  });
}

async function signIn(request: APIRequestContext, persona: string) {
  const r = await request.post('/api/ib/auth/demo-login', { data: { persona } });
  expect(r.status(), `demo-login ${persona}`).toBe(200);
}

const READS = [
  'graph/facets',
  'graph/neighborhood?limit=10',
  'graph/search?q=migration',
  'graph/node/knowledge_page:cf-1001',
  'brain/graph?limit=50',
  'brain/timeline/knowledge_page:cf-1001',
  'brain/node/knowledge_page:cf-1001/sheet',
  'evidence/trace/knowledge_page:cf-1001',
  'ontology',
  'ontology/packs',
  'packs',
];

test.describe('engine proxy: off', () => {
  test.skip(MODE !== 'off', 'server runs with WALNUT_PROXY=1');

  test('every path is the same 404, even for an allowed persona', async ({ request }) => {
    await signIn(request, 'jdoe');
    for (const p of READS) {
      const r = await request.get(`/api/walnut/${p}`);
      expect(r.status(), p).toBe(404);
      expect(await r.json(), p).toEqual({ detail: 'not found' });
    }
  });
});

test.describe('engine proxy: on', () => {
  test.skip(MODE !== 'on', 'server runs without WALNUT_PROXY=1');

  test('the snapshot persona reads every allowlisted path', async ({ request }) => {
    await signIn(request, 'jdoe');
    for (const p of READS) {
      const r = await request.get(`/api/walnut/${p}`);
      expect(r.status(), p).toBe(200);
      const body = await r.json();
      expect(JSON.stringify(body), `${p} leaks a local path`).not.toMatch(/graph_source|packs_root|graph_db_path/);
    }
  });

  test("compliance may NOT read the snapshot (it is jdoe's view, not live-checked, not ledgered)", async ({ request }) => {
    await signIn(request, 'maya.compliance');
    expect((await request.get('/api/walnut/graph/facets')).status()).toBe(403);
  });

  test('any other persona gets 403', async ({ request }) => {
    for (const persona of ['ken.ext', 'priya.sre', 'admin.it', 'maya.compliance']) {
      await signIn(request, persona);
      const r = await request.get('/api/walnut/graph/facets');
      expect(r.status(), persona).toBe(403);
      expect(await r.text(), persona).not.toContain('node_kinds');
    }
  });

  test('no session gets 401', async ({ baseURL }) => {
    const r = await rawGet(baseURL!, '/api/walnut/graph/facets');
    expect(r.status).toBe(401);
  });

  test('paths outside the allowlist are 404, including every write and ask route', async ({ request }) => {
    await signIn(request, 'jdoe');
    for (const p of [
      'brain/query',
      'brain/ingest',
      'brain/insights',
      'brain/path',
      'brain/export/sheets',
      'graph/reload',
      'graph/path?from=a&to=b',
      'mcp/servers',
      'mcp/tools/x/invoke',
      'intelligence/ask',
      'federated/ask',
      'evidence/recent',
      'agents',
      'convert',
      'health',
      'ontology/audit',
      'graph/node/a/b',
    ]) {
      const r = await request.get(`/api/walnut/${p}`);
      expect(r.status(), p).toBe(404);
    }
  });

  test('POST and other write methods are refused, never forwarded', async ({ request }) => {
    await signIn(request, 'jdoe');
    for (const p of ['brain/query', 'brain/ingest', 'graph/reload', 'graph/facets']) {
      const r = await request.post(`/api/walnut/${p}`, { data: { question: 'x' } });
      expect([404, 405], p).toContain(r.status());
    }
    expect([404, 405]).toContain((await request.delete('/api/walnut/graph/facets')).status());
    expect([404, 405]).toContain((await request.put('/api/walnut/graph/facets', { data: {} })).status());
  });

  test('dot segments, encoded slashes and odd ids never reach a route', async ({ request, baseURL }) => {
    await signIn(request, 'jdoe');
    const state = await request.storageState();
    const cookie = state.cookies.find((c) => c.name === 'ib_session');
    const headers = cookie ? { cookie: `ib_session=${cookie.value}` } : undefined;
    for (const p of [
      '/api/walnut/graph/node/%2e%2e',
      '/api/walnut/graph/node/%2E',
      '/api/walnut/graph/node/a%2Fb',
      '/api/walnut/graph/node/a%5Cb',
      '/api/walnut/graph/node/a%3Cscript%3E',
      '/api/walnut/graph/%2e%2e/brain/query',
    ]) {
      const r = await rawGet(baseURL!, p, headers);
      expect([400, 404], p).toContain(r.status);
    }
  });

  test('query parameters are allowlisted and validated per path', async ({ request }) => {
    await signIn(request, 'jdoe');
    expect((await request.get('/api/walnut/graph/facets?x=1')).status()).toBe(400);
    expect((await request.get('/api/walnut/graph/neighborhood?limit=99999')).status()).toBe(400);
    expect((await request.get('/api/walnut/graph/neighborhood?depth=9')).status()).toBe(400);
    expect((await request.get('/api/walnut/brain/node/knowledge_page:cf-1001/sheet?as_of=yesterday')).status()).toBe(400);
  });

  test('a foreign Host (DNS rebinding) or a cross-site fetch is refused', async ({ request, baseURL }) => {
    await signIn(request, 'jdoe');
    const state = await request.storageState();
    const cookie = state.cookies.find((c) => c.name === 'ib_session')!;
    const port = new URL(baseURL!).port;
    const evil = await rawGet(baseURL!, '/api/walnut/graph/facets', { host: `evil.example:${port}`, cookie: `ib_session=${cookie.value}` });
    expect(evil.status).toBe(403);
    const cross = await rawGet(baseURL!, '/api/walnut/graph/facets', { 'sec-fetch-site': 'cross-site', cookie: `ib_session=${cookie.value}` });
    expect(cross.status).toBe(403);
  });

  test('the browser Authorization header is not what authenticates', async ({ baseURL }) => {
    const r = await rawGet(baseURL!, '/api/walnut/graph/facets', { authorization: 'Bearer dev-local-only' });
    expect(r.status).toBe(401);
  });
});
