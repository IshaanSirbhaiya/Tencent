import { expect, test, type Page } from '@playwright/test';
import { expectAccessible, login } from './helpers';

// /dev/brain, the Walnut engine view, in MOCK mode with the proxy ON (the fixture in
// components/engine/mock.ts). Needs a server started with NEXT_PUBLIC_IB_MOCK=1 WALNUT_PROXY=1;
// run with ENGINE_PROXY=on. ENGINE_SCREENS=1 also writes the screenshots to docs/finals/screens/engine/.
const ON = (process.env.ENGINE_PROXY ?? 'off').toLowerCase() === 'on';
const SHOTS = process.env.ENGINE_SCREENS === '1';
const SHOT_DIR = '../../../docs/finals/screens/engine';

async function setTheme(page: Page, theme: 'dark' | 'light') {
  await page.evaluate((t) => {
    localStorage.setItem('ib-theme', t);
    document.documentElement.setAttribute('data-theme', t);
  }, theme);
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: true });
}

async function openEngine(page: Page, persona = 'jdoe', query = '') {
  await login(page, persona);
  await page.goto(`/dev/brain${query}`);
}

test.describe('engine view', () => {
  test.skip(!ON, 'needs a server with WALNUT_PROXY=1 (ENGINE_PROXY=on)');

  test('jdoe sees the snapshot banner, the graph, its legend and a node panel', async ({ page }) => {
    await openEngine(page);
    const banner = page.getByTestId('engine-snapshot-banner');
    await expect(banner).toContainText('Snapshot: Company A as Jordan sees it, built');
    await expect(banner).toContainText("The Internal Brain's live permission checks apply to answers");
    await expect(page.getByRole('heading', { level: 1, name: 'Walnut engine' })).toBeVisible();
    await expect(page.getByTestId('engine-graph-canvas')).toBeVisible();
    await expect(page.getByTestId('engine-graph-status')).toContainText('nodes');
    await expect(page.locator('.react-flow__node').first()).toBeVisible();
    await page.getByRole('group', { name: /Knowledge graph canvas/ }).locator('.react-flow__node[aria-label*="DB Migration Plan"]').click();
    await expect(page.getByTestId('engine-node-panel')).toContainText('DB Migration Plan');
    await expect(page.getByTestId('engine-node-panel')).toContainText('pages.json');
    await shot(page, '01-graph-dark-1280');
  });

  test('the table alternative lists the same nodes and opens a link panel', async ({ page }) => {
    await openEngine(page);
    await page.getByTestId('engine-graph-view').getByRole('radio', { name: 'Table' }).click();
    const table = page.getByTestId('engine-graph-table');
    await expect(table).toContainText('DB Migration Plan');
    await table.getByRole('row', { name: /wrote DB Migration Plan/ }).first().click();
    await expect(page.getByTestId('engine-edge-panel')).toContainText('wrote');
    await shot(page, '02-graph-table-dark-1280');
  });

  test('search focuses an entity', async ({ page }) => {
    await openEngine(page);
    await page.getByRole('combobox', { name: 'Find an entity' }).fill('Nina');
    await page.getByRole('option', { name: /Nina/ }).first().click();
    await expect(page.getByTestId('engine-graph-status')).toContainText('Nina');
    await expect(page.getByTestId('engine-node-panel')).toContainText('Nina');
  });

  test('company brain: the time slider fades what was not yet true, and the sheet renders', async ({ page }) => {
    await openEngine(page, 'jdoe', '?tab=brain&focus=knowledge_page%3Acf-1001');
    await expect(page.getByTestId('engine-asof')).toHaveText('now');
    const count = page.getByTestId('engine-asof-count');
    await expect(count).toContainText('24');
    const slider = page.getByLabel('As of');
    await slider.fill('0');
    await expect(page.getByTestId('engine-asof')).not.toHaveText('now');
    await expect(page.getByTestId('engine-brain-panel')).toContainText('DB Migration Plan');
    await shot(page, '03-brain-rewound-dark-1280');
    await page.getByRole('button', { name: 'Now' }).click();
    await expect(page.getByTestId('engine-asof')).toHaveText('now');
  });

  test('provenance: the DB Migration Plan value chain ends at the file and row', async ({ page }) => {
    await openEngine(page, 'jdoe', '?tab=provenance');
    await expect(page.getByTestId('engine-trace-id')).toHaveText('knowledge_page:cf-1001');
    await page.getByRole('button', { name: /^version/ }).click();
    const chain = page.getByTestId('engine-chain');
    await expect(chain).toContainText('6');
    await expect(chain).toContainText('cf-1001');
    await expect(chain).toContainText('confluence.pages');
    await expect(chain).toContainText('pages.json');
    await shot(page, '04-provenance-dark-1280');
  });

  test('ontology lists node and link kinds with counts', async ({ page }) => {
    await openEngine(page, 'jdoe', '?tab=ontology');
    const o = page.getByTestId('engine-ontology');
    await expect(o).toContainText('Enterprise knowledge');
    await expect(o.getByRole('table', { name: 'Node kinds' })).toContainText('533');
    await expect(o.getByRole('table', { name: 'Link kinds' })).toContainText('867');
    await shot(page, '05-ontology-dark-1280');
  });

  test('compliance may explore; anyone else sees the lock card and no data', async ({ page }) => {
    await openEngine(page, 'maya.compliance');
    await expect(page.getByTestId('engine-snapshot-banner')).toBeVisible();
    await openEngine(page, 'ken.ext');
    await expect(page.getByTestId('engine-lock')).toContainText("built as Jordan's view at sync time");
    await expect(page.locator('.react-flow__node')).toHaveCount(0);
    await shot(page, '06-locked-ken-dark-1280');
  });

  test('keyboard: Tab reaches a node and Enter opens its panel', async ({ page }) => {
    await openEngine(page);
    await expect(page.locator('.react-flow__node').first()).toBeVisible();
    await page.locator('.react-flow__node').first().focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('engine-node-panel')).toBeVisible();
    await page.getByRole('button', { name: 'Zoom in' }).focus();
    await page.keyboard.press('Enter');
  });

  for (const theme of ['dark', 'light'] as const) {
    for (const width of [1280, 390]) {
      test(`axe: no serious or critical issues (${theme}, ${width} px)`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await login(page, 'jdoe');
        await setTheme(page, theme);
        for (const tab of ['graph', 'brain', 'provenance', 'ontology']) {
          await page.goto(`/dev/brain?tab=${tab}`);
          await expect(page.getByTestId('engine-snapshot-banner')).toBeVisible();
          await page.waitForTimeout(400);
          await expectAccessible(page, `/dev/brain ${tab} ${theme} ${width}`);
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
          expect(overflow, `${tab} horizontal overflow at ${width}`).toBeLessThanOrEqual(0);
          if (tab !== 'graph' || theme === 'light' || width === 390) await shot(page, `${tab}-${theme}-${width}`);
        }
      });
    }
  }
});

// LIVE: a dev server WITHOUT mock mode, WALNUT_PROXY=1, a real IB (IB_DEMO_LOGIN=1) and the Walnut
// gateway on :8300. Run with ENGINE_LIVE=1 (ENGINE_SCREENS=1 writes live-*.png). Values are not
// asserted (they are the live store's); the shape and the gates are.
test.describe('engine view (live gateway)', () => {
  test.skip(process.env.ENGINE_LIVE !== '1', 'needs ENGINE_LIVE=1 and the live stack');

  test('each tab renders from the live engine for jdoe', async ({ page }) => {
    await openEngine(page);
    await expect(page.getByTestId('engine-snapshot-banner')).toContainText('Snapshot: Company A as Jordan sees it, built');
    await expect(page.locator('.react-flow__node').first()).toBeVisible();
    await page.getByRole('combobox', { name: 'Find an entity' }).fill('DB Migration Plan');
    await page.getByRole('option', { name: /DB Migration Plan/ }).first().click();
    await expect(page.getByTestId('engine-node-panel')).toContainText('DB Migration Plan');
    await shot(page, 'live-01-graph-dark-1280');
    await page.goto('/dev/brain?tab=brain&focus=knowledge_page%3Acf-1001');
    await expect(page.getByTestId('engine-brain-panel')).toContainText('DB Migration Plan');
    await page.getByLabel('As of').fill('700');
    await expect(page.getByTestId('engine-asof')).not.toHaveText('now');
    await page.waitForTimeout(800);
    await shot(page, 'live-02-brain-rewound-dark-1280');
    await page.goto('/dev/brain?tab=provenance');
    await page.getByRole('button', { name: /^version/ }).click();
    await expect(page.getByTestId('engine-chain')).toContainText('pages.json');
    await shot(page, 'live-03-provenance-dark-1280');
    await page.goto('/dev/brain?tab=ontology');
    await expect(page.getByTestId('engine-ontology')).toContainText('Enterprise knowledge');
    await page.waitForTimeout(500);
    await shot(page, 'live-04-ontology-dark-1280');
    await setTheme(page, 'light');
    await page.goto('/dev/brain?tab=provenance');
    await expect(page.getByTestId('engine-chain')).toBeVisible();
    await shot(page, 'live-05-provenance-light-1280');
  });

  test('ken.ext is locked out, by the page and by the proxy', async ({ page }) => {
    await openEngine(page, 'ken.ext');
    await expect(page.getByTestId('engine-lock')).toBeVisible();
    expect((await page.request.get('/api/walnut/graph/facets')).status()).toBe(403);
    await shot(page, 'live-06-locked-ken-dark-1280');
  });
});

test.describe('engine view (proxy off)', () => {
  test.skip(ON || process.env.ENGINE_LIVE === '1', 'server runs with WALNUT_PROXY=1');

  test('the page says the engine view is switched off, and shows no data', async ({ page }) => {
    await openEngine(page);
    await expect(page.getByText('The engine view is switched off on this server')).toBeVisible();
    await expect(page.locator('.react-flow__node')).toHaveCount(0);
    await shot(page, '07-proxy-off-dark-1280');
  });
});
