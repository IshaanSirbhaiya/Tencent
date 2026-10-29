import { type Page, expect, test } from '@playwright/test';
import { login } from './helpers';

// Evidence screenshots for the knowledge graph (docs/finals/screens/graph/). Skipped unless
// GRAPH_SCREENS=1, so the normal suite never rewrites the evidence folder.
const OUT = process.env.GRAPH_SCREENS_DIR ?? '../../../docs/finals/screens/graph';

test.skip(!process.env.GRAPH_SCREENS, 'set GRAPH_SCREENS=1 to capture the graph screenshots');
test.use({ viewport: { width: 1440, height: 900 } });

async function ready(page: Page, id = 'graph-main') {
  await expect(page.getByTestId(`${id}-canvas`)).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1200);
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

test('galaxy, focus, side panel, table (dark)', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph');
  await ready(page);
  await shot(page, '01-galaxy-dark');

  await page.getByTestId('graph-main-search').fill('Ledger database');
  await page.getByTestId('graph-search-option').first().click();
  await expect(page.getByTestId('graph-main-panel')).toBeVisible();
  await page.waitForTimeout(900);
  await shot(page, '02-focus-panel-overview');

  await page.getByRole('tab', { name: 'Passage' }).click();
  await expect(page.getByTestId('graph-passage')).toBeVisible();
  await shot(page, '03-panel-passage');
  await page.getByRole('tab', { name: 'Lineage' }).click();
  await expect(page.getByTestId('graph-lineage')).toBeVisible();
  await shot(page, '04-panel-lineage');

  await page.getByTestId('graph-expand').click();
  await expect(page.getByTestId('graph-expand')).toContainText('Expanded');
  await page.waitForTimeout(700);
  await shot(page, '05-expanded');

  await page.getByTestId('graph-main-display').getByRole('radio', { name: 'Table' }).click();
  await expect(page.getByTestId('graph-main-table')).toBeVisible();
  await shot(page, '06-table-view');
});

test('zoomed in: semantic labels', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph');
  await ready(page);
  const canvas = page.getByTestId('graph-main-canvas');
  await canvas.focus();
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('+');
    await page.waitForTimeout(260);
  }
  await page.waitForTimeout(500);
  await shot(page, '07-zoomed-labels');
});

test('compare two people', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph?compare=ken.ext');
  await expect(page.getByTestId('compare-summary')).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId('compare-shared')).not.toHaveText('…');
  await page.waitForTimeout(1200);
  await shot(page, '08-compare-jdoe-ken');
});

test('compare with an overlapping person (shared items marked on the left)', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/graph?compare=priya.sre');
  await expect(page.getByTestId('compare-summary')).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId('compare-shared')).toContainText('can also open');
  await page.waitForTimeout(1200);
  await shot(page, '08b-compare-jdoe-priya-shared');
});

test('light theme', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('ib-theme', 'light'));
  await login(page, 'jdoe');
  await page.goto('/dev/graph');
  await ready(page);
  await shot(page, '09-galaxy-light');
  await page.getByTestId('graph-main-search').fill('#payments-eng');
  await page.getByTestId('graph-search-option').first().click();
  await page.waitForTimeout(900);
  await shot(page, '10-focus-light');
});

test('phone width', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'jdoe');
  await page.goto('/dev/graph');
  await ready(page);
  await shot(page, '11-mobile');
});
