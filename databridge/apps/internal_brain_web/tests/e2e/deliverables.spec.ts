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
  await expect(view.getByTestId('deliver-by')).toHaveText('Copied cell by cell from source [1]');
  // round 4 UX F6: "no model" is a quiet info line, not the by-line
  await expect(view.getByTestId('deliver-nomodel')).toHaveText('No AI model was used: this was built by code, straight from your sources.');
  // round 4 UX F12: one Tab from the focused title reaches the skip link, which lands on Copy
  await view.getByTestId('deliver-title').focus();
  await page.keyboard.press('Tab');
  await expect(view.getByTestId('deliver-skip')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(view.getByTestId('deliver-copy')).toBeFocused();
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
  await expect(chart.getByTestId('deliver-by')).toHaveText('Charted from the table in source [1]');
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
  // fallback 'no_model' (no model connected): never reads as "a model ran and failed" (round-3 Fable #5)
  await expect(an.getByTestId('deliver-needs-model')).toContainText('Needs the model (Hy3). It is not connected on this build; the table and chart are built from the sources.');
  await expect(an.getByTestId('deliver-needs-model')).not.toContainText('passed the source check');
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
    return route.fulfill({ response: res, json: { ...body, status: 'needs_model', content: null, generated: 'none', exports: [], fallback: 'nothing_passed' } });
  });
  await answer.getByTestId('deliver-analysis').click();
  const sheet = page.getByTestId('deliver-sheet');
  await expect(sheet.getByTestId('deliver-needs-model')).toBeVisible();
  // a model ran and nothing passed the source check: the original sentence
  await expect(sheet.getByTestId('deliver-needs-model')).toContainText('It didn’t produce anything that passed the source check, so nothing was written.');
  mode = 'pass';
  await sheet.getByTestId('deliver-show-table').click();
  await expect(sheet.getByTestId('deliver-panel-table').getByTestId('deliver-table')).toBeVisible();
  await page.keyboard.press('Escape');

  mode = 'nothing';
  await answer.getByTestId('deliver-chart').click();
  await expect(sheet.getByTestId('deliver-nothing')).toHaveText(/There’s nothing to build from this answer\./);
  await expect(sheet.getByTestId('deliver-nothing')).not.toContainText(/can (still )?open/);
  await page.keyboard.press('Escape');

  mode = 'error';
  await answer.getByTestId('deliver-chart').click();
  await expect(sheet.getByTestId('deliver-error')).toContainText('This is temporarily unavailable.');
  await expectAccessible(page, 'deliverables error');
  mode = 'pass';
  await sheet.getByRole('button', { name: 'Try again' }).click();
  await expect(sheet.getByTestId('deliver-chart')).toBeVisible();
  await expect(sheet.getByTestId('deliver-panel-chart').getByTestId('deliver-title')).toBeFocused(); // M6
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

// ---- round-3 UX review (M1-M6, L1) ---------------------------------------------------------------

test('table chips: one per row when the row cites one source; a chip points inside the panel, no second modal', async ({ page }) => {
  const answer = await leoAnswer(page, 'light');
  await answer.getByTestId('deliver-table').click();
  const sheet = page.getByTestId('deliver-sheet');
  const table = sheet.getByTestId('deliver-panel-table').getByTestId('deliver-table');
  await expect(table).toBeVisible();
  // M2: every mock row cites [1] in every cell, so each row shows its chip once (4 tab stops, not 20)
  const rows = table.locator('tbody tr');
  const n = await rows.count();
  expect(n).toBeGreaterThan(1);
  await expect(table.locator('tbody tr[data-chips="row"]')).toHaveCount(n);
  await expect(table.getByTestId('citation-chip')).toHaveCount(n);
  // M3: the chip moves focus to "Cited in this table"; the Deliver sheet is the only dialog
  await table.getByTestId('citation-chip').first().click();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(sheet.getByTestId('deliver-sources').locator('li').first()).toBeFocused();
  // L1: no "you can still open" hint in the panel description
  await expect(sheet).not.toContainText(/still open|can open/);
  await expectAccessible(page, 'deliverables table chips (light)');
});

test('Regenerate keeps keyboard focus: the loading line, then the new title (M6)', async ({ page }) => {
  const answer = await leoAnswer(page);
  await answer.getByTestId('deliver-table').click();
  const sheet = page.getByTestId('deliver-sheet');
  await expect(sheet.getByTestId('deliver-view')).toBeVisible();
  let release: () => void = () => undefined;
  const gate = new Promise<void>((r) => (release = r));
  await page.route('**/api/ib/ib/deliverables', async (route) => {
    await gate;
    await route.continue();
  });
  await sheet.getByTestId('deliver-regenerate').focus();
  await page.keyboard.press('Enter');
  await expect(sheet.getByTestId('deliver-loading')).toBeFocused();
  release();
  await expect(sheet.getByTestId('deliver-title')).toBeFocused();
});

test('zh: server text is marked lang="en", the chart name and the copy text are Chinese (M1, M4, M5)', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-lang', 'zh');
    } catch {
      /* storage blocked */
    }
  });
  await quiet(page, 'light');
  await login(page, 'leo.ops');
  const box = page.locator('textarea').first();
  await box.fill(Q);
  await box.press('Enter');
  const answer = page.getByTestId('answer');
  await expect(answer.getByTestId('deliver-row')).toBeVisible();
  await answer.getByTestId('deliver-table').click();
  const sheet = page.getByTestId('deliver-sheet');
  const view = sheet.getByTestId('deliver-panel-table').getByTestId('deliver-view');
  // round 4 UX F5: the server's fixed title is UI text, so it is translated (lang zh); a
  // source-worded title stays English with lang="en"
  await expect(view.getByTestId('deliver-title')).toHaveText('根据你的来源生成的表格');
  await expect(view.getByTestId('deliver-title')).toHaveAttribute('lang', 'zh');
  await expect(view.getByTestId('deliver-label')).toHaveText('草稿，供你审阅');
  await expect(view.getByTestId('deliver-table').locator('table')).toHaveAttribute('lang', 'en');
  await expect(view.getByTestId('deliver-scope')).toHaveAttribute('lang', 'en');
  await view.getByTestId('deliver-copy').click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain('\t来源');
  expect(copied).not.toMatch(/\bSources\b/);
  await expectAccessible(page, 'deliverables table (zh)');

  await sheet.getByTestId('deliver-tab-chart').click();
  const fig = sheet.getByTestId('deliver-panel-chart').getByTestId('deliver-chart');
  await expect(fig.getByRole('img')).toHaveAttribute('aria-label', /^柱状图，/);
  await expect(fig.getByRole('img')).not.toHaveAttribute('aria-label', /Bar/);
});
