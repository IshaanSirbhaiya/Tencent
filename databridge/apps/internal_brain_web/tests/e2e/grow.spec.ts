import { expect, test, type Page } from '@playwright/test';
import { computeLayout, discRadius } from '../../components/graph/layout-core';
import { buildGalaxy, layoutInput, mergeDelta } from '../../components/graph/model';
import type { GraphNode, KnowledgeGraph } from '../../lib/types';
import { expectAccessible, login, quietTourOffer } from './helpers';

/** Positions the PRE-refactor computeLayout (git HEAD before docSlot/discRadius) gave for the unit
 *  galaxy below; the refactored one must give the same (also checked equal on a 656-node input). */
const LAYOUT_FIXTURE = { c1_0: [22.47, 2.9] as [number, number], c2: [11.62, 20.25] as [number, number] };

// "Watch the brain grow" (demo-proof, D-24) in mock mode. The mock server has demo features ON; the
// controls still stay hidden until the viewer turns "Demo features" on in Settings.
// Run: NEXT_PUBLIC_IB_MOCK=1 NEXT_DIST_DIR=.next-grow npx next dev -H 127.0.0.1 -p 3127
//      npx playwright test -c tests/e2e/grow.config.ts

const NOTE =
  'Update on DBMIG-12: replica tuning on ledger-shard-3 is done; the copy job resumes Thursday 5 Nov 2026, 21:00 SGT from batch 1,812. On-call for the restart: priya.sre.';

async function enableDemo(page: Page) {
  await page.goto('/settings');
  const card = page.getByTestId('settings-demo');
  await expect(card).toBeVisible();
  await card.getByRole('switch').click();
  await expect(card.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
}

function countChanges(page: Page) {
  const seen = { n: 0 };
  page.on('request', (r) => {
    if (r.url().includes('/api/ib/ib/graph/changes')) seen.n += 1;
  });
  return seen;
}

test.beforeEach(async ({ page }) => {
  await quietTourOffer(page);
});

// ---- unit (no browser): the incremental layout of PLAN §5 --------------------------------------
test.describe('mergeDelta (unit)', () => {
  const node = (id: string, kind: GraphNode['kind'], extra: Partial<GraphNode> = {}): GraphNode => ({
    id,
    kind,
    platform: 'slack',
    label: id,
    container: null,
    degree_hidden_safe: null,
    more: null,
    ...extra,
  });
  function galaxy() {
    const docs = Array.from({ length: 9 }, (_, i) => node(`slack:C1:${i}`, 'doc', { container: 'container:slack:C1', container_name: 'one', version: '1' }));
    const nodes = [node('platform:slack', 'platform'), node('container:slack:C1', 'container', { count: 9 }), node('container:slack:C2', 'container', { count: 0 }), node('person:a', 'person', { platform: null }), ...docs];
    const edges = [
      { source: 'platform:slack', target: 'container:slack:C1', kind: 'contains' as const },
      { source: 'platform:slack', target: 'container:slack:C2', kind: 'contains' as const },
      ...docs.map((d) => ({ source: 'container:slack:C1', target: d.id, kind: 'contains' as const })),
      { source: 'person:a', target: 'container:slack:C1', kind: 'authored' as const, weight: 3 },
    ];
    const g = buildGalaxy({ as_of: '', scope_note: '', sub: 'a', mode: 'overview', focus: null, query: null, limit: 300, truncated: false, nodes, edges } as unknown as KnowledgeGraph, null);
    const pos = computeLayout(...(Object.values(layoutInput(g)) as [never, never]));
    g.forEachNode((id) => g.mergeNodeAttributes(id, { x: pos[id][0], y: pos[id][1] }));
    return g;
  }
  const delta = {
    nodes: [
      node('slack:C1:new', 'doc', { container: 'container:slack:C1', container_name: 'one', version: '1' }),
      node('container:slack:C1', 'container', { count: null }),
      node('slack:C3:first', 'doc', { container: 'container:slack:C3', container_name: 'three', version: '1' }),
      node('container:slack:C3', 'container', { count: null }),
      node('person:b', 'person', { platform: null }),
      node('slack:C1:3', 'doc', { container: 'container:slack:C1', container_name: 'one', version: '2' }),
    ],
    edges: [
      { source: 'container:slack:C1', target: 'slack:C1:new', kind: 'contains' as const },
      { source: 'container:slack:C3', target: 'slack:C3:first', kind: 'contains' as const },
      { source: 'platform:slack', target: 'container:slack:C3', kind: 'contains' as const },
      { source: 'person:b', target: 'slack:C1:new', kind: 'authored' as const },
    ],
  };

  test('existing nodes keep their exact positions; the new doc lies inside its disc; counts survive', () => {
    const g = galaxy();
    const before = new Map(g.mapNodes((id, a) => [id, [a.x, a.y]] as const));
    const { added, updated } = mergeDelta(g, delta, 1000);
    for (const [id, [x, y]] of before) {
      expect(g.getNodeAttribute(id, 'x'), id).toBe(x);
      expect(g.getNodeAttribute(id, 'y'), id).toBe(y);
    }
    expect(added.sort()).toEqual(['container:slack:C3', 'person:b', 'slack:C1:new', 'slack:C3:first']);
    expect(updated).toEqual(['slack:C1:3']);
    const c = g.getNodeAttributes('container:slack:C1');
    const d = g.getNodeAttributes('slack:C1:new');
    expect(Math.hypot(d.x - c.x, d.y - c.y)).toBeLessThan(discRadius(9));
    expect(g.getNodeAttribute('container:slack:C1', 'count')).toBe(9); // a null in the delta never overwrites
    expect(g.getNodeAttribute('slack:C1:new', 'fresh')).toEqual({ at: 1000, kind: 'new' });
    expect(g.getNodeAttribute('slack:C1:3', 'fresh')).toEqual({ at: 1000, kind: 'updated' });
    // a new place does not overlap an existing disc
    const c3 = g.getNodeAttributes('container:slack:C3');
    for (const id of ['container:slack:C1', 'container:slack:C2']) {
      const o = g.getNodeAttributes(id);
      expect(Math.hypot(o.x - c3.x, o.y - c3.y)).toBeGreaterThanOrEqual(discRadius(id.endsWith('C1') ? 10 : 0) + discRadius(1));
    }
  });

  test('deterministic: the same graph plus the same delta gives the same positions', () => {
    const a = galaxy();
    const b = galaxy();
    mergeDelta(a, delta, 5);
    mergeDelta(b, delta, 5);
    a.forEachNode((id, at) => {
      expect(b.getNodeAttribute(id, 'x')).toBe(at.x);
      expect(b.getNodeAttribute(id, 'y')).toBe(at.y);
    });
  });

  test('computeLayout is unchanged by the docSlot / discRadius refactor (stored fixture)', () => {
    const g = galaxy();
    expect(g.getNodeAttribute('slack:C1:0', 'x')).toBe(LAYOUT_FIXTURE.c1_0[0]);
    expect(g.getNodeAttribute('slack:C1:0', 'y')).toBe(LAYOUT_FIXTURE.c1_0[1]);
    expect(g.getNodeAttribute('container:slack:C2', 'x')).toBe(LAYOUT_FIXTURE.c2[0]);
    expect(g.getNodeAttribute('container:slack:C2', 'y')).toBe(LAYOUT_FIXTURE.c2[1]);
  });
});

test('hidden by default: no feed, no Add button and no polling until the viewer turns it on', async ({ page }) => {
  await login(page, 'jdoe');
  const polls = countChanges(page);
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 45_000 });
  await expect(page.getByTestId('graph-main-add')).toHaveCount(0);
  await expect(page.getByTestId('graph-main-live')).toHaveCount(0);
  await page.waitForTimeout(4000);
  expect(polls.n).toBe(0);
});

test('post a note as jdoe: it arrives in "Just added", Show selects it, pausing stops polling', async ({ page }) => {
  await login(page, 'jdoe');
  await enableDemo(page);
  const polls = countChanges(page);
  await page.goto('/dev/graph?add=1');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 45_000 });
  const feed = page.getByTestId('graph-main-live');
  await expect(feed).toBeVisible();
  await expect(feed.getByTestId('graph-main-live-empty')).toHaveText('Nothing new yet.'); // L1: no "you can open" hint

  const sheet = page.getByTestId('graph-main-addsheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId('graph-main-addsheet-channel')).toHaveValue('C0DBMIG');
  await sheet.getByTestId('graph-main-addsheet-text').fill(NOTE);
  await expectAccessible(page, 'grow/add-sheet');
  await sheet.getByTestId('graph-main-addsheet-post').click();
  await expect(sheet.getByTestId('graph-main-addsheet-status')).toHaveText('Posted to Slack · #db-migration');
  // H3: the form is cleared, so another Enter cannot post the same note twice; focus is on the field
  await expect(sheet.getByTestId('graph-main-addsheet-text')).toHaveValue('');
  await expect(sheet.getByTestId('graph-main-addsheet-post')).toBeDisabled();
  await expect(sheet.getByTestId('graph-main-addsheet-text')).toBeFocused();
  await expect(sheet.getByTestId('graph-main-addsheet-status-error')).toHaveAttribute('role', 'alert'); // L4
  await expect(sheet.getByTestId('graph-main-addsheet-status-error')).toHaveText('');
  await page.keyboard.press('Escape');
  // H1: closing the sheet returns focus to the "Add a document" button
  await expect(page.getByTestId('graph-main-add')).toBeFocused();

  const item = feed.getByTestId('graph-main-live-item').first();
  await expect(item).toBeVisible({ timeout: 10_000 });
  await expect(item).toContainText('Slack · #db-migration · new thread');
  await expect(feed.getByTestId('graph-main-live-after')).toContainText(/your note · appeared \d+(\.\d)? s after you posted/);
  await expect(item).toHaveAttribute('data-doc', /^slack:C0DBMIG:/);
  await page.screenshot({ path: 'test-results/grow-mock-feed.png' });

  // L5: the "Show" button is named after its item
  await expect(item.getByRole('button')).toHaveAccessibleName(/^Show Update on DBMIG-12/);
  await item.getByRole('button', { name: 'Show' }).click();
  await expect(page.getByTestId('graph-main-panel')).toBeVisible();
  await page.keyboard.press('Escape');

  // pausing stops polling (WCAG 2.2.2); turning it back on resumes
  await feed.getByTestId('graph-main-live-switch').click();
  await expect(feed).toHaveAttribute('data-state', 'paused');
  await page.waitForTimeout(500);
  const at = polls.n;
  await page.waitForTimeout(7000);
  expect(polls.n).toBe(at);
  await feed.getByTestId('graph-main-live-switch').click();
  await expect.poll(() => polls.n, { timeout: 10_000 }).toBeGreaterThan(at);
  await expectAccessible(page, 'grow/feed');
});

test('Compare: only the poster receives the note; upload tab and engine refresh sit behind the same toggle', async ({ page }) => {
  await login(page, 'jdoe');
  await enableDemo(page);
  await page.goto('/dev/graph?compare=ken.ext');
  await expect(page.locator('[data-testid$="-live"]')).toHaveCount(2, { timeout: 90_000 });
  const add = page.locator('[data-testid$="-add"]');
  await expect(add).toHaveCount(1); // the main session only, never Ken's slot
  await add.click();
  const sheet = page.locator('[data-testid$="-addsheet"]');
  await sheet.locator('textarea').fill('Compare check: the shard 3 copy job resumes Thursday.');
  await sheet.getByRole('button', { name: 'Post to Slack' }).click();
  await expect(sheet.getByRole('status')).toContainText('Posted to Slack');
  await sheet.getByRole('tab', { name: 'Upload a file' }).click();
  await expect(sheet.getByText('Sample files', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-testid$="-live-item"]')).toHaveCount(1, { timeout: 10_000 });
  const owners = await page.locator('[data-testid$="-live-item"]').evaluateAll((els) => els.map((e) => e.closest('[data-sub]')?.getAttribute('data-sub')));
  expect(owners).toEqual(['jdoe']);
  await page.screenshot({ path: 'test-results/grow-mock-compare.png' });

  // The engine refresh lives on /dev/brain, which renders only with WALNUT_PROXY=1 (engine.spec.ts).
  if (process.env.ENGINE_PROXY !== 'on') return;
  await page.goto('/dev/brain');
  const refresh = page.getByTestId('engine-refresh');
  await expect(refresh).toBeVisible({ timeout: 30_000 });
  await refresh.getByTestId('engine-refresh-button').click();
  await expect(refresh.getByTestId('engine-refresh-status')).toContainText('Rebuilding the snapshot');
  await expect(refresh.getByTestId('engine-refresh-status')).toContainText('Snapshot rebuilt', { timeout: 20_000 });
});

// ---- round-3 UX review (H1, M7, M8) ---------------------------------------------------------------

test('Add opened by keyboard: Esc returns focus to the button (H1)', async ({ page }) => {
  await login(page, 'jdoe');
  await enableDemo(page);
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 45_000 });
  const add = page.getByTestId('graph-main-add');
  await add.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('graph-main-addsheet')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('graph-main-addsheet')).toBeHidden();
  await expect(add).toBeFocused();
});

test('live updates that stop for good say so, and the switch reads off (M8)', async ({ page }) => {
  await login(page, 'jdoe');
  await enableDemo(page);
  await page.route('**/api/ib/ib/graph/changes**', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not found"}' }));
  await page.goto('/dev/graph');
  const feed = page.getByTestId('graph-main-live');
  await expect(feed).toHaveAttribute('data-state', 'stopped', { timeout: 45_000 });
  await expect(feed.getByTestId('graph-main-live-status')).toHaveText('Live updates stopped. Reload the page to restart them.');
  const sw = feed.getByTestId('graph-main-live-switch');
  await expect(sw).toHaveAttribute('aria-checked', 'false');
  await expect(sw).toBeDisabled();
});

test('390 px: the Add button is not clipped and the feed is one compact line (M7)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'jdoe');
  await enableDemo(page);
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 45_000 });
  const add = page.getByTestId('graph-main-add');
  await expect(add).toHaveAccessibleName(/^Add a document/);
  const clipped = await add.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
  expect(clipped).toBe(false);
  const box = await add.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390).toBe(true);
  const feed = page.getByTestId('graph-main-live');
  await expect(feed).toBeVisible();
  await expect(feed.getByTestId('graph-main-live-empty')).toHaveCount(0); // compact below sm
  const fb = await feed.boundingBox();
  expect(fb!.height).toBeLessThan(60);
  await expectAccessible(page, 'grow/390');
});

test('upload: pick a folder by NAME; too big is caught on pick; success clears the form (D-34, H3, L3)', async ({ page }) => {
  // leo.ops may write to two folders in the world (the product group's); the list holds only those
  await login(page, 'leo.ops');
  await enableDemo(page);
  const sent: { folder?: string }[] = [];
  await page.route('**/api/ib/sim/upload-file', (r) => {
    sent.push(JSON.parse(r.request().postData() ?? '{}'));
    return r.fulfill({ json: { ok: true, doc_id: 'drive:up-0123456789abcdef0123', platform: 'drive', container: '0ACompany', kind: 'md', at: new Date().toISOString() } });
  });
  await page.goto('/dev/graph?add=1');
  const sheet = page.getByTestId('graph-main-addsheet');
  await expect(sheet).toBeVisible({ timeout: 45_000 });
  await sheet.getByRole('tab', { name: 'Upload a file' }).click();
  const folder = sheet.getByLabel('Drive folder', { exact: true });
  await expect(folder).toBeVisible();
  const labels = (await folder.locator('option').allTextContents()).slice(1);
  expect(labels).toEqual(['Pricing & Offers', 'Vendor share']); // sorted, writable only
  await expect(folder).toHaveValue(''); // no default: the person chooses
  await expect(sheet.getByTestId('graph-main-addsheet-advanced')).toHaveCount(0); // no ID field while the list works
  const input = sheet.getByTestId('graph-main-addsheet-file');
  await input.setInputFiles({ name: 'big.txt', mimeType: 'text/plain', buffer: Buffer.alloc(5 * 1024 * 1024 + 10, 97) });
  await expect(sheet.getByTestId('graph-main-addsheet-upload-status-error')).toHaveText('That file is larger than 5 MB.');
  await expect(sheet.getByTestId('graph-main-addsheet-upload')).toBeDisabled();
  await input.setInputFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('# Shard 3\nCopy job resumes Thursday.\n') });
  await expect(sheet.getByTestId('graph-main-addsheet-picked')).toHaveText(/^Selected: notes\.md · \d+ B$/);
  await expect(sheet.getByTestId('graph-main-addsheet-upload-status-error')).toHaveText('');
  await expect(sheet.getByTestId('graph-main-addsheet-upload')).toBeDisabled(); // no folder chosen yet
  // keyboard: focus the list and choose by its name
  await folder.focus();
  await folder.selectOption({ label: 'Vendor share' });
  await expect(folder).toHaveValue('fd-7');
  await sheet.getByTestId('graph-main-addsheet-upload').click();
  await expect(sheet.getByTestId('graph-main-addsheet-upload-status')).toHaveText('Uploaded to Drive · notes.md');
  expect(sent.map((b) => b.folder)).toEqual(['fd-7']);
  await expect(input).toHaveValue('');
  await expect(sheet.getByTestId('graph-main-addsheet-picked')).toHaveText('');
  await expect(sheet.getByTestId('graph-main-addsheet-upload')).toBeDisabled();
  await expect(input).toBeFocused();
  await expectAccessible(page, 'grow/upload-picker');
});

test('upload: someone who may write nowhere sees no folder name, only a collapsed ID fallback (D-34)', async ({ page }) => {
  await login(page, 'jdoe'); // jdoe can read Engineering's folders but write to none
  await enableDemo(page);
  const sent: { folder?: string }[] = [];
  await page.route('**/api/ib/sim/upload-file', (r) => {
    sent.push(JSON.parse(r.request().postData() ?? '{}'));
    return r.fulfill({ json: { ok: true, doc_id: 'drive:up-0123456789abcdef0124', platform: 'drive', container: '0AEng', kind: 'md', at: new Date().toISOString() } });
  });
  await page.goto('/dev/graph?add=1');
  const sheet = page.getByTestId('graph-main-addsheet');
  await expect(sheet).toBeVisible({ timeout: 45_000 });
  await sheet.getByRole('tab', { name: 'Upload a file' }).click();
  await expect(sheet.getByTestId('graph-main-addsheet-folders-state')).toHaveText('No folder you can add files to was found.');
  await expect(sheet.getByTestId('graph-main-addsheet-folders')).toHaveCount(0);
  // no copy anywhere in the upload UI names a folder (round-4 Opus #4)
  const text = await sheet.innerText();
  expect(text).not.toMatch(/fd-\d|Vendor share|Pricing|Finance/);
  const id = sheet.getByTestId('graph-main-addsheet-folder');
  await expect(id).toBeHidden(); // collapsed
  await sheet.getByText('Advanced: folder ID').click();
  await expect(id).toBeVisible();
  expect(await id.getAttribute('placeholder')).toBeNull(); // no example id
  await id.fill('fd-3');
  await sheet.getByTestId('graph-main-addsheet-file').setInputFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('# Notes\n') });
  await sheet.getByTestId('graph-main-addsheet-upload').click();
  await expect(sheet.getByTestId('graph-main-addsheet-upload-status')).toHaveText('Uploaded to Drive · notes.md');
  expect(sent.map((b) => b.folder)).toEqual(['fd-3']);
});

test('upload: the folder list failing falls back to the ID field; works at 390 px (D-34)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'leo.ops');
  await enableDemo(page);
  await page.route('**/api/ib/sim/upload-folders', (r) => r.fulfill({ status: 503, json: { error: 'temporarily unavailable' } }));
  await page.goto('/dev/graph?add=1');
  const sheet = page.getByTestId('graph-main-addsheet');
  await expect(sheet).toBeVisible({ timeout: 45_000 });
  await sheet.getByRole('tab', { name: 'Upload a file' }).click();
  await expect(sheet.getByTestId('graph-main-addsheet-folders-state')).toHaveText('Could not load your folders.');
  await sheet.getByText('Advanced: folder ID').focus();
  await page.keyboard.press('Enter'); // a native <details>: keyboard opens it
  const id = sheet.getByTestId('graph-main-addsheet-folder');
  await expect(id).toBeVisible();
  const box = await id.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390).toBe(true);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(wide).toBe(false);
});

test('390 px: the picker list fits and the Add button shows a short label with a tooltip', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'leo.ops');
  await enableDemo(page);
  await page.goto('/dev/graph');
  const add = page.getByTestId('graph-main-add');
  await expect(add).toBeVisible({ timeout: 45_000 });
  await expect(add).toHaveAttribute('title', 'Add a document');
  await expect(add).toContainText('Add');
  await expect(add).toHaveAccessibleName(/^Add a document/);
  await add.click();
  const sheet = page.getByTestId('graph-main-addsheet');
  await sheet.getByRole('tab', { name: 'Upload a file' }).click();
  const folder = sheet.getByLabel('Drive folder', { exact: true });
  await expect(folder).toBeVisible();
  const box = await folder.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390).toBe(true);
  await expectAccessible(page, 'grow/upload-picker-390');
});
