import http from 'node:http';
import { expect, test } from '@playwright/test';
import { S1, ask, login } from './helpers';

// The same-origin proxy's own guards (app/api/ib/[...path]/route.ts), plus the link guard in the UI.

/** A GET with the path sent byte-for-byte (Playwright's URL parser would fold `%2e%2e` away). */
function rawGet(baseURL: string, path: string): Promise<{ status: number; body: string }> {
  const u = new URL(baseURL);
  return new Promise((resolve, reject) => {
    const r = http.request({ host: u.hostname, port: u.port, path, method: 'GET' }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => resolve({ status: res.statusCode ?? 0, body }));
    });
    r.on('error', reject);
    r.end();
  });
}

test('dot segments and odd document ids never reach a route', async ({ baseURL }) => {
  for (const p of ['/api/ib/audit/doc/%2e%2e/accessors', '/api/ib/audit/doc/%2E/accessors', '/api/ib/audit/doc/a%2Fb/accessors', '/api/ib/audit/doc/a%5Cb/accessors']) {
    const r = await rawGet(baseURL!, p);
    expect([400, 404], p).toContain(r.status);
    expect(r.body, p).not.toContain('accessors');
  }
});

test('a POST from another origin, or a non-JSON body, is refused', async ({ request, baseURL }) => {
  const data = { persona: 'jdoe' };
  const evil = await request.post('/api/ib/auth/demo-login', { headers: { origin: 'https://evil.example' }, data });
  expect(evil.status()).toBe(403);
  expect(evil.headers()['set-cookie']).toBeUndefined();
  const sibling = await request.post('/api/ib/auth/demo-login', { headers: { 'sec-fetch-site': 'same-site' }, data });
  expect(sibling.status()).toBe(403);
  const form = await request.post('/api/ib/auth/demo-login', { headers: { 'content-type': 'text/plain' }, data: JSON.stringify(data) });
  expect(form.status()).toBe(415);
  const big = await request.post('/api/ib/ib/ask', { data: { question: 'x'.repeat(70_000) } });
  expect(big.status()).toBe(413);

  const own = await request.post('/api/ib/auth/demo-login', { headers: { origin: new URL(baseURL!).origin, 'sec-fetch-site': 'same-origin' }, data });
  expect(own.status()).toBe(200);
  // Only the session cookie comes back, scoped to this whole site.
  const set = own.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie').map((h) => h.value);
  expect(set.length).toBe(1);
  expect(set[0]).toMatch(/^ib_session=/);
  expect(set[0]).toMatch(/; Path=\/(;|$)/);
  expect(set[0]).toMatch(/; HttpOnly(;|$)/);
  expect(set[0]).not.toMatch(/domain=/i);
});

test('an unknown x-ib-slot value is a 400, not the main session', async ({ request }) => {
  for (const v of ['b,c', 'z', 'main']) {
    expect((await request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': v } })).status(), v).toBe(400);
    const r = await request.post('/api/ib/auth/demo-login', { headers: { 'x-ib-slot': v }, data: { persona: 'jdoe' } });
    expect(r.status(), v).toBe(400);
    expect(r.headers()['set-cookie']).toBeUndefined();
  }
});

test('a source link that is not http(s) is shown as text, not a link', async ({ page }) => {
  await page.route('**/api/ib/ib/map', async (route) => {
    const res = await route.fetch();
    const map = await res.json();
    for (const p of map.platforms) for (const c of p.containers) for (const it of c.items) it.deep_link = 'javascript:alert(1)';
    await route.fulfill({ response: res, json: map });
  });
  await page.route('**/api/ib/ib/ask', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    for (const c of body.citations ?? []) c.deep_link = 'javascript:alert(1)';
    await route.fulfill({ response: res, json: body });
  });

  await login(page, 'jdoe');
  await ask(page, S1);
  await expect(page.getByTestId('citations')).toBeVisible();
  await expect(page.locator('[data-testid="citations"] a')).toHaveCount(0);

  await page.goto('/map');
  await page.locator('[data-testid="map-place"][data-place="DBMIG"]').click();
  const panel = page.getByTestId('map-panel');
  await expect(panel.getByTestId('map-item').first()).toBeVisible();
  await expect(panel.getByTestId('map-item-nolink').first()).toHaveText('In Jira');
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
});
