import { expect, test } from '@playwright/test';
import { ask, expectAccessible, login } from './helpers';

// LIVE check (not CI): the real proxy and a real Internal Brain with IB_DELIVERABLES=1.
//   NEXT_DIST_DIR=.next-deliv WALNUT_PROXY=1 IB_API_URL=http://127.0.0.1:<ib> npx next dev -H 127.0.0.1 -p 3128
//   DELIV_LIVE=1 npx playwright test -c tests/e2e/deliverables.config.ts deliverables.live
// Leo asks the FX question that cites the pricing sheet (drive:df-3), then Table, Chart, and the
// journey. Screenshots go to docs/finals/screens/deliverables/.
test.skip(process.env.DELIV_LIVE !== '1', 'live check only (DELIV_LIVE=1)');

const Q = 'Can we offer multi-currency accounts to an Indonesian SME?';
const SHOTS = '../../../docs/finals/screens/deliverables';

test('live: Leo turns the FX answer into a table and a chart; the proxy relays only the fixed file name', async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-tour-offer', 'dismissed');
      localStorage.setItem('ib-theme', 'dark');
    } catch {
      /* storage blocked */
    }
  });
  await login(page, 'leo.ops');
  await ask(page, Q);
  const answer = page.getByTestId('answer');
  const reqId = (await answer.getAttribute('data-request'))!;
  const row = answer.getByTestId('deliver-row');
  await expect(row).toBeVisible();
  const status = await (await page.request.get('/api/ib/ib/status')).json();
  const chips = status.deliverables.model ? ['Table', 'Chart', 'Analysis'] : ['Table', 'Chart'];
  await expect(row.getByRole('button')).toHaveText(chips);
  await row.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/live-0-answer-row.png` });

  await row.getByTestId('deliver-table').click();
  const sheet = page.getByTestId('deliver-sheet');
  const table = sheet.getByTestId('deliver-panel-table').getByTestId('deliver-view');
  await expect(table.getByTestId('deliver-table')).toContainText('0.4% FX margin');
  await expect(table.getByTestId('deliver-by')).toHaveText(/Copied cell by cell from source \[\d\]/);
  await expectAccessible(page, 'live table');
  await page.screenshot({ path: `${SHOTS}/live-1-table.png` });

  await sheet.getByTestId('deliver-tab-chart').click();
  const chart = sheet.getByTestId('deliver-panel-chart').getByTestId('deliver-view');
  await expect(chart.getByTestId('deliver-title')).toHaveText('FX margin by market');
  await expect(chart.getByTestId('deliver-chart-table').getByRole('row')).toHaveCount(4);
  await expectAccessible(page, 'live chart');
  await page.screenshot({ path: `${SHOTS}/live-2-chart.png` });

  // Download through the REAL proxy: the fixed-shape name and nosniff are relayed
  const [dl] = await Promise.all([page.waitForEvent('download'), chart.getByTestId('deliver-csv').click()]);
  expect(dl.suggestedFilename()).toMatch(/^walnut-chart-[0-9a-f]{8}-\d{8}\.csv$/);
  const did = await page.evaluate(async (rid) => {
    const r = await fetch('/api/ib/ib/deliverables', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ req_id: rid, kind: 'table' }) });
    return (await r.json()).deliverable_id as string;
  }, reqId);
  const exp = await page.request.post('/api/ib/ib/deliverables/export', { data: { deliverable_id: did, format: 'csv' }, headers: { origin: new URL(page.url()).origin } });
  expect(exp.status()).toBe(200);
  expect(exp.headers()['content-disposition']).toMatch(/^attachment; filename="walnut-table-[0-9a-f]{8}-\d{8}\.csv"$/);
  expect(exp.headers()['x-content-type-options']).toBe('nosniff');
  // an error carries no disposition; someone else's / unknown id is the one 404
  const miss = await page.request.post('/api/ib/ib/deliverables/export', { data: { deliverable_id: 'f'.repeat(32), format: 'csv' }, headers: { origin: new URL(page.url()).origin } });
  expect(miss.status()).toBe(404);
  expect(miss.headers()['content-disposition']).toBeUndefined();

  // Analysis: offered only with a model configured; without one the API answers "needs the model"
  if (!status.deliverables.model) {
    const r = await page.evaluate(async (rid) => {
      const x = await fetch('/api/ib/ib/deliverables', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ req_id: rid, kind: 'analysis' }) });
      return x.json();
    }, reqId);
    expect(r.status).toBe('needs_model');
  }

  await page.keyboard.press('Escape');
  await page.goto(`/dev/journey/${encodeURIComponent(reqId)}`);
  await expect(page.getByText('deliverable.created').first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/live-3-journey.png`, fullPage: true });
});
