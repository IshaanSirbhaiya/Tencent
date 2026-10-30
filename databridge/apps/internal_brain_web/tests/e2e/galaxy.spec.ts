import { expect, test, type Page, type Request } from '@playwright/test';
import { login } from './helpers';

// GET /ib/galaxy (docs/finals/live_growth/GRAPH_PERF_DIAGNOSIS.md, Fix 1; test 7 of §5): the graph page
// loads its overview AND its map from ONE live pass. One /ib/galaxy request per graph mount and one
// per compare side; the page never asks /ib/graph or /ib/map for its first load.

type Seen = { path: string; slot: string; search: string };

function record(page: Page): Seen[] {
  const seen: Seen[] = [];
  page.on('request', (r: Request) => {
    const u = new URL(r.url());
    // exact paths only: /ib/graph/changes (live feed) and POST /ib/graph/openable are other calls
    if (['/api/ib/ib/galaxy', '/api/ib/ib/graph', '/api/ib/ib/map'].includes(u.pathname))
      seen.push({ path: u.pathname.replace('/api/ib/ib/', ''), slot: r.headers()['x-ib-slot'] ?? 'a', search: u.search });
  });
  return seen;
}

test('one /ib/galaxy request per graph mount, no /ib/graph or /ib/map', async ({ page }) => {
  await login(page, 'jdoe');
  const seen = record(page);
  const res = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/ib/ib/galaxy');
  await page.goto('/dev/graph');
  const body = await (await res).json();
  expect(body.overview.sub).toBe('jdoe');
  expect(body.map.sub).toBe('jdoe');
  expect(typeof body.map.complete).toBe('boolean');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('graph-main-canvas')).toHaveAttribute('aria-label', /867 items · 17 places · 4 platforms/);
  await page.waitForTimeout(1000); // a late duplicate would show up here
  expect(seen).toEqual([{ path: 'galaxy', slot: 'a', search: '?limit=300' }]);

  // a second mount (reload) is a second single request
  seen.length = 0;
  await page.reload();
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1000);
  expect(seen).toEqual([{ path: 'galaxy', slot: 'a', search: '?limit=300' }]);
});

test('compare: one /ib/galaxy per side, each under its own session, no /ib/graph or /ib/map', async ({ page }) => {
  await login(page, 'jdoe');
  const seen = record(page);
  await page.goto('/dev/graph?compare=ken.ext');
  await expect(page.getByTestId('compare-summary')).toHaveText('Jordan can open 867 items · Ken can open 24', { timeout: 40_000 });
  await expect(page.getByTestId('graph-left')).toHaveAttribute('data-sub', 'jdoe');
  await expect(page.getByTestId('graph-right')).toHaveAttribute('data-sub', 'ken.ext');
  await page.waitForTimeout(1000);
  const sorted = [...seen].sort((x, y) => x.slot.localeCompare(y.slot));
  expect(sorted).toEqual([
    { path: 'galaxy', slot: 'a', search: '?limit=300' },
    { path: 'galaxy', slot: 'b', search: '?limit=300' },
  ]);
  await expect(page.getByTestId('whoami')).toContainText('jdoe');
});

test('a new sign-in never reuses the previous person\'s galaxy', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main')).toHaveAttribute('data-sub', 'jdoe', { timeout: 30_000 });
  await login(page, 'ken.ext');
  const seen = record(page);
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main')).toHaveAttribute('data-sub', 'ken.ext', { timeout: 30_000 });
  await expect(page.getByTestId('graph-main-canvas')).toHaveAttribute('aria-label', /24 items · 2 places · 2 platforms/);
  expect(seen.filter((s) => s.path === 'galaxy')).toHaveLength(1);
});
