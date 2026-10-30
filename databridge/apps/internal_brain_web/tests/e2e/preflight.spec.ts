import { expect, test } from '@playwright/test';

// The Demo Day preflight board (/present/preflight): read-only, large status rows. Runs against the
// mock build (playwright.config.ts), so the services row says "offline mock" and the clips are
// checked through this web app.

test('preflight board shows the web app, the clips, the clock and the screen checks', async ({ page }) => {
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
  const writes: string[] = [];
  page.on('request', (r) => {
    if (r.method() !== 'GET' && r.method() !== 'HEAD') writes.push(`${r.method()} ${r.url()}`);
  });
  await page.goto('/present/preflight');
  await page.getByRole('button', { name: 'Re-check' }).click();
  await expect(page.getByTestId('pf-clip-A')).toHaveAttribute('data-status', 'pass');
  expect(writes.filter((w) => w.includes('/api/ib/'))).toEqual([]);
});
