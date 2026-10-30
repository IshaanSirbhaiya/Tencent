import { type Page, expect, test } from '@playwright/test';
import { ask, expectAccessible, login } from './helpers';

// "Turn this answer into" (mock mode): the row under a released answer, the wide panel with its
// Table / Chart / Analysis tabs, the one metadata line and the neutral pill, the chart's value
// labels and data table, the CSV download name, the states (needs the model, nothing, error), and
// Esc returning focus to the chip that opened the panel. Scratch config: deliverables.config.ts.

const Q = 'Can we offer multi-currency accounts to an Indonesian SME?';
const SHOTS = '../../../docs/finals/screens/deliverables';

async function quiet(page: Page, theme: 'dark' | 'light' = 'dark') {
  await page.addInitScript((th) => {
    try {
      localStorage.setItem('ib-tour-offer', 'dismissed');
      localStorage.setItem('ib-theme', th);
    } catch {
      /* storage blocked */
    }
  }, theme);
}

async function leoAnswer(page: Page, theme: 'dark' | 'light' = 'dark') {
  await quiet(page, theme);
  await login(page, 'leo.ops');
  await ask(page, Q);
  const answer = page.getByTestId('answer');
  await expect(answer.getByTestId('deliver-row')).toBeVisible();
  return answer;
}

test('Leo: table copied cell by cell, chart with value labels, analysis needs the model', async ({ page }) => {
  const answer = await leoAnswer(page);
  const row = answer.getByTestId('deliver-row');
  await expect(row.getByRole('heading', { name: 'Turn this answer into' })).toBeVisible();
  await expect(row.getByRole('button')).toHaveText(['Table', 'Chart', 'Analysis']);

  // Table: copied from the source, no model, no pill
  await row.getByTestId('deliver-table').click();
  const sheet = page.getByTestId('deliver-sheet');
  await expect(sheet).toBeVisible();
  const view = sheet.getByTestId('deliver-view');
  await expect(view).toHaveAttribute('data-kind', 'table');
  await expect(view.getByTestId('deliver-by')).toHaveText('Copied cell by cell from [1], no model');
  await expect(view.getByTestId('deliver-pill')).toHaveCount(0);
  await expect(view.getByTestId('deliver-label')).toHaveText('Draft for your review');
  const table = view.getByTestId('deliver-table');
  await expect(table.getByRole('columnheader')).toHaveText(['market', 'plan', 'product', 'availability', 'price']);
  await expect(table.getByRole('row')).toHaveCount(5);
  await expect(table).toContainText('0.4% FX margin');
  await expect(view.getByTestId('deliver-recorded')).toContainText('Recorded in the audit trail before it was shown.');
  await expectAccessible(page, 'deliverables table (dark)');
  await page.screenshot({ path: `${SHOTS}/mock-1-table-dark.png` });

  // CSV: the server's fixed-shape file name
  const [dl] = await Promise.all([page.waitForEvent('download'), view.getByTestId('deliver-csv').click()]);
  expect(dl.suggestedFilename()).toMatch(/^walnut-table-[0-9a-f]{8}-\d{8}\.csv$/);

  // Chart: value labels on the bars, chip beside each x label, a real data table
  await sheet.getByTestId('deliver-tab-chart').click();
  const chart = sheet.getByTestId('deliver-panel-chart').getByTestId('deliver-view');
  await expect(chart.getByTestId('deliver-title')).toHaveText('FX margin by market');
  await expect(chart.getByTestId('deliver-by')).toHaveText('Charted from the table in [1], no model');
  const fig = chart.getByTestId('deliver-chart');
  await expect(fig.getByRole('img')).toHaveAttribute('aria-label', /3 bars, from 0\.35% to 0\.4%/);
  await expect(fig.locator('.recharts-label-list').first()).toContainText('0.35%');
  const data = fig.getByTestId('deliver-chart-table');
  await expect(data.getByRole('row')).toHaveCount(4);
  await expect(data).toContainText('0.4% FX margin');
  await expectAccessible(page, 'deliverables chart (dark)');
  await page.screenshot({ path: `${SHOTS}/mock-2-chart-dark.png` });

  // Analysis needs the model. TokenHub is not live, so the mock (like the real server without a model)
  // returns 'needs_model' unless built with NEXT_PUBLIC_IB_MOCK_MODEL=hy3 (rule 8: no "Written by hy3").
  await sheet.getByTestId('deliver-tab-analysis').click();
  const an = sheet.getByTestId('deliver-panel-analysis');
  await expect(an.getByTestId('deliver-needs-model')).toBeVisible();
  await expect(an.getByTestId('deliver-by')).toHaveCount(0); // nothing claims to be written by a model
  await expectAccessible(page, 'deliverables analysis (dark)');
  await page.screenshot({ path: `${SHOTS}/mock-3-analysis-dark.png` });

  // Esc closes and returns focus to the chip that opened the panel
  await page.keyboard.press('Escape');
  await expect(sheet).toBeHidden();
  await expect(row.getByTestId('deliver-table')).toBeFocused();
});

test('light theme chart passes axe', async ({ page }) => {
  const answer = await leoAnswer(page, 'light');
  await answer.getByTestId('deliver-chart').click();
  const view = page.getByTestId('deliver-sheet').getByTestId('deliver-view');
  await expect(view.getByTestId('deliver-chart')).toBeVisible();
  await expectAccessible(page, 'deliverables chart (light)');
  await page.screenshot({ path: `${SHOTS}/mock-4-chart-light.png` });
});

test('states: needs the model, nothing to build, error with retry', async ({ page }) => {
  const answer = await leoAnswer(page);
  let mode: 'needs' | 'nothing' | 'error' | 'pass' = 'needs';
  await page.route('**/api/ib/ib/deliverables', async (route) => {
    if (mode === 'pass') return route.continue();
    if (mode === 'nothing') return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' });
    if (mode === 'error') return route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"temporarily unavailable"}' });
    const res = await route.fetch();
    const body = await res.json();
    return route.fulfill({ response: res, json: { ...body, status: 'needs_model', content: null, generated: 'none', exports: [] } });
  });
  await answer.getByTestId('deliver-analysis').click();
  const sheet = page.getByTestId('deliver-sheet');
  await expect(sheet.getByTestId('deliver-needs-model')).toBeVisible();
  mode = 'pass';
  await sheet.getByTestId('deliver-show-table').click();
  await expect(sheet.getByTestId('deliver-panel-table').getByTestId('deliver-table')).toBeVisible();
  await page.keyboard.press('Escape');

  mode = 'nothing';
  await answer.getByTestId('deliver-chart').click();
  await expect(sheet.getByTestId('deliver-nothing')).toHaveText(/There’s nothing to build from this answer\./);
  await page.keyboard.press('Escape');

  mode = 'error';
  await answer.getByTestId('deliver-chart').click();
  await expect(sheet.getByTestId('deliver-error')).toContainText('This is temporarily unavailable.');
  await expectAccessible(page, 'deliverables error');
  mode = 'pass';
  await sheet.getByRole('button', { name: 'Try again' }).click();
  await expect(sheet.getByTestId('deliver-chart')).toBeVisible();
});

test('the row is not shown on a refusal or in Compare', async ({ page }) => {
  await quiet(page);
  await login(page, 'leo.ops');
  await ask(page, 'zebra quantum orchid budget');
  await expect(page.getByTestId('answer').getByTestId('deliver-row')).toHaveCount(0);
  await page.goto(`/compare?mode=person&p=leo.ops&q1=${encodeURIComponent(Q)}&q2=${encodeURIComponent(Q)}`);
  await page.waitForTimeout(1500);
  await expect(page.getByTestId('deliver-row')).toHaveCount(0);
});
