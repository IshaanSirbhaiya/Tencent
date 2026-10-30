import { expect, test } from '@playwright/test';
import { expectAccessible, login } from './helpers';

// /dev/graph in mock mode. The mock estate matches /ib/map (jdoe 867 items in 17 places on 4
// platforms; ken.ext 24 in 2 on 2). The graph may only ever show what each session's API returned.

test('jdoe: galaxy loads, search focuses a node, side panel tabs, expand, table view, axe', async ({ page }) => {
  test.setTimeout(120_000); // the 30 s canvas wait plus force-layout and axe on a loaded machine
  await login(page, 'jdoe');
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main-loading')).toBeVisible();
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('graph-main')).toHaveAttribute('data-sub', 'jdoe');
  await expect(page.getByTestId('graph-main-canvas')).toHaveAttribute('aria-label', /867 items · 17 places · 4 platforms/);
  await expect(page.getByTestId('legend-slack')).toContainText('533');
  await expect(page.getByTestId('legend-jira')).toContainText('207');

  // search → fly → ego highlight → panel
  await page.getByTestId('graph-main-search').fill('Ledger database');
  await page.getByTestId('graph-search-option').first().click();
  const panel = page.getByTestId('graph-main-panel');
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId('graph-panel-title')).toContainText('DBMIG-12');
  await expect(panel.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  await expect(page).toHaveURL(/node=jira%3ADBMIG-12/);

  await panel.getByRole('tab', { name: 'Passage' }).click();
  await expect(panel.getByTestId('graph-passage')).toBeVisible();
  await panel.getByRole('tab', { name: 'Lineage' }).click();
  await expect(panel.getByTestId('graph-lineage')).toContainText('Connector');
  await expect(panel.getByTestId('graph-lineage')).toContainText('Jira');
  await panel.getByRole('tab', { name: /Links/ }).click();
  await expect(panel.getByTestId('graph-links')).toBeVisible();
  const before = await panel.getByTestId('graph-links').getByRole('button').count();

  // one-hop growth merges the focus response in
  await panel.getByTestId('graph-expand').click();
  await expect(panel.getByTestId('graph-expand')).toContainText('Expanded');
  await expect.poll(async () => panel.getByTestId('graph-links').getByRole('button').count()).toBeGreaterThan(before);
  await expectAccessible(page, 'graph/panel');

  // server search (GET /ib/graph?q=)
  await page.getByTestId('graph-main-search').fill('replica lag');
  await page.getByTestId('graph-search-server').click();
  await expect(page.getByTestId('graph-search-option').first()).toBeVisible();

  // table view: the same nodes, keyboard reachable
  await page.getByTestId('graph-main-display').getByRole('radio', { name: 'Table' }).click();
  const table = page.getByTestId('graph-main-table');
  await expect(table).toBeVisible();
  await expect(table.getByRole('table')).toHaveAccessibleName(/Nodes on the canvas/);
  await expect(table.getByRole('row').nth(1)).toBeVisible();
  await expectAccessible(page, 'graph/table');
});

test('keyboard: canvas pans and zooms without a mouse, / focuses search, Esc clears', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph?node=container%3Ajira%3ADBMIG');
  await expect(page.getByTestId('graph-main-panel')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('graph-panel-title')).toHaveText('Database Migration');
  const canvas = page.getByTestId('graph-main-canvas');
  await canvas.focus();
  for (const k of ['+', '-', 'ArrowLeft', 'ArrowUp', '0']) await page.keyboard.press(k);
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('graph-main-panel')).toHaveCount(0);
  await page.keyboard.press('/');
  await expect(page.getByTestId('graph-main-search')).toBeFocused();
  // the pan/zoom buttons are an alternative to dragging (WCAG 2.5.7)
  await page.getByRole('button', { name: 'Zoom in' }).click();
  await page.getByRole('button', { name: 'Pan left' }).click();
  await page.getByTestId('graph-fit').click();
});

test('ken.ext: only his own places; nothing hints that more exists', async ({ page }) => {
  await login(page, 'ken.ext');
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('graph-main-canvas')).toHaveAttribute('aria-label', /24 items · 2 places · 2 platforms/);
  await expect(page.getByTestId('legend-confluence')).toHaveCount(0);
  await expect(page.getByTestId('legend-jira')).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText(/hidden|withheld|restricted|more exist|not allowed/i);
  await page.getByTestId('graph-main-display').getByRole('radio', { name: 'Table' }).click();
  await expect(page.getByTestId('graph-main-table')).not.toContainText(/Payments|db-migration|Pricing/);
  await expectAccessible(page, 'graph/ken');
});

test('a document the API will not return reads "Not available to you right now"', async ({ page }) => {
  await login(page, 'jdoe');
  await page.route('**/api/ib/ib/doc/**', (r) => r.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' }));
  await page.goto('/dev/graph?node=jira%3ADBMIG-12');
  const panel = page.getByTestId('graph-main-panel');
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await panel.getByRole('tab', { name: 'Passage' }).click();
  await expect(panel.getByTestId('graph-doc-unavailable')).toHaveText(/Not available to you right now/);
  await expect(panel).not.toContainText(/restricted|denied|permission/i);
});

test('503: designed error state, then retry recovers', async ({ page }) => {
  await login(page, 'jdoe');
  let fail = true;
  await page.route('**/api/ib/ib/galaxy?limit=300', (r) =>
    fail ? r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"temporarily unavailable"}' }) : r.continue(),
  );
  await page.goto('/dev/graph');
  await expect(page.getByText('The graph is temporarily unavailable')).toBeVisible({ timeout: 20_000 });
  await expectAccessible(page, 'graph/503');
  fail = false;
  await page.getByTestId('graph-retry').click();
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 30_000 });
});

test('compare: two separately signed-in sessions, counts per side, main session unchanged', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph?compare=ken.ext');
  await expect(page.getByTestId('compare-summary')).toHaveText('Jordan can open 867 items · Ken can open 24', { timeout: 40_000 });
  await expect(page.getByTestId('graph-left')).toHaveAttribute('data-sub', 'jdoe');
  await expect(page.getByTestId('graph-right')).toHaveAttribute('data-sub', 'ken.ext');
  await expect(page.getByTestId('compare-shared')).toHaveText('Ken can open none of the items on the left');
  await expect(page.locator('main')).not.toContainText(/hidden|withheld|restricted|more exist/i);
  await expectAccessible(page, 'graph/compare');
  // the main session is still Jordan
  await expect(page.getByTestId('whoami')).toContainText('jdoe');
});

test('compare in the Stage theme: no count comparisons on the projector (round-2 Fable §6)', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph?compare=ken.ext');
  await expect(page.getByTestId('compare-summary')).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId('compare-side-count').first()).toBeVisible();
  await page.keyboard.press('Control+Shift+P'); // arm the presenter keys
  await page.keyboard.press('s'); // Stage theme on
  await expect(page.locator('html')).toHaveAttribute('data-stage', /.*/);
  await expect(page.getByTestId('compare-summary')).toBeHidden();
  await expect(page.getByTestId('compare-shared-group')).toBeHidden();
  await expect(page.getByTestId('compare-side-count')).toHaveCount(2);
  for (const el of await page.getByTestId('compare-side-count').all()) await expect(el).toBeHidden();
  // innerText: what is rendered (textContent would include the display:none chrome)
  expect(await page.locator('main').innerText()).not.toMatch(/can open none|can also open \d|can open \d/);
});

test('390 px light theme: accessible, no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem('ib-theme', 'light'));
  await login(page, 'jdoe');
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 30_000 });
  await expectAccessible(page, 'graph/mobile-light');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
