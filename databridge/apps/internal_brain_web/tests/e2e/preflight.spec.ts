import { expect, test } from '@playwright/test';
import { login } from './helpers';

// The Demo Day preflight board (/present/preflight): read-only, large status rows. Runs against the
// mock build (playwright.config.ts), so the services row says "offline mock" and the clips are
// checked through this web app.

test('preflight board shows the web app, the clips, the clock and the screen checks', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/present/preflight');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByTestId('pf-web')).toHaveAttribute('data-status', 'pass');
  await expect(page.getByTestId('pf-mode')).toHaveAttribute('data-status', 'info');
  for (const c of ['A', 'B', 'C']) await expect(page.getByTestId(`pf-clip-${c}`)).toHaveAttribute('data-status', 'pass');
  await expect(page.getByTestId('pf-screen')).toBeVisible();
  await expect(page.getByTestId('pf-tz')).toBeVisible();
  await expect(page.getByTestId('pf-verdict')).not.toHaveText('Checking…');
  await expect(page.getByLabel('Laptop clock')).toHaveText(/^\d\d:\d\d:\d\d$/);
});

test('the board writes nothing: it only reads', async ({ page }) => {
  await login(page, 'jdoe');
  const writes: string[] = [];
  page.on('request', (r) => {
    if (r.method() !== 'GET' && r.method() !== 'HEAD') writes.push(`${r.method()} ${r.url()}`);
  });
  await page.goto('/present/preflight');
  await page.getByRole('button', { name: 'Re-check' }).click();
  await expect(page.getByTestId('pf-clip-A')).toHaveAttribute('data-status', 'pass');
  expect(writes.filter((w) => w.includes('/api/ib/'))).toEqual([]);
});

test('the board needs loopback AND a session the IB confirms (round-4 Opus #2)', async ({ page }) => {
  // Signed out on loopback: one 404.
  expect((await page.request.get('/present/preflight')).status()).toBe(404);
  // A cookie that is merely present (a DNS-rebinding page can set one on its own origin): one 404.
  const forged = await page.request.get('/present/preflight', { headers: { cookie: 'ib_session=x' } });
  expect(forged.status()).toBe(404);
  const forgedMock = await page.request.get('/present/preflight', { headers: { cookie: 'ib_session=mock.nobody' } });
  expect(forgedMock.status()).toBe(404);
  // A real session on loopback: served.
  await login(page, 'jdoe');
  expect((await page.request.get('/present/preflight')).status()).toBe(200);
  // The same real session from another host name: one 404.
  const away = await page.request.get('/present/preflight', { headers: { host: 'demo.example' } });
  expect(away.status()).toBe(404);
});

test('the board fits a 390 px phone and is marked English (round-4 Sonnet F1)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'jdoe');
  await page.goto('/present/preflight');
  await expect(page.getByTestId('pf-verdict')).not.toHaveText('Checking…');
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(scroll, 'no horizontal scroll').toBeLessThanOrEqual(client);
  // The board's own root says lang="en", so a zh-Hans page still reads it as English.
  await expect(page.locator('div[lang="en"]').filter({ has: page.getByTestId('pf-verdict') }).first()).toBeVisible();
});
