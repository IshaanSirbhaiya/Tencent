import { expect, test } from '@playwright/test';
import { expectAccessible, login } from './helpers';

// The knowledge map lists ONLY what the signed-in person can open (mock fixtures mirror the live
// estate: jdoe 867 / 17 / 4, ken.ext 24 / 2 / 2). Nothing may hint that more exists.

test('jdoe: totals, four platforms, a place opens its items, search narrows', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/map');
  await expect(page.getByTestId('map-totals')).toHaveText('You can open 867 items in 17 places across 4 platforms.');
  for (const p of ['confluence', 'jira', 'slack', 'drive']) await expect(page.getByTestId(`map-platform-${p}`)).toBeVisible();
  await expect(page.getByTestId('map-place')).toHaveCount(17);
  await expect(page.getByTestId('map-freshness')).toContainText('checked with its source');

  await page.locator('[data-testid="map-place"][data-place="DBMIG"]').click();
  const panel = page.getByTestId('map-panel');
  await expect(panel.getByRole('heading', { name: 'Database Migration' })).toBeVisible();
  await expect(panel).toContainText('Jira project · 40 items');
  await expect(panel.getByTestId('map-item').first()).toBeVisible();
  const link = panel.getByRole('link', { name: /in Jira \(new tab\)/ }).first();
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveText(/Open in Jira/);

  await page.getByTestId('map-search').fill('Ledger database migration');
  await expect(panel.getByTestId('map-item')).toHaveCount(1);
  await expect(panel.getByTestId('map-item')).toContainText('Ledger database migration to Postgres 16');

  // Keyboard: a node is a button, reachable and operable from the keyboard.
  await page.getByRole('button', { name: 'All places' }).click();
  await page.getByTestId('map-search').fill('');
  const node = page.getByRole('button', { name: /^#payments-eng, Slack: 84 items/ });
  await node.focus();
  await page.keyboard.press('Enter');
  await expect(panel.getByRole('heading', { name: '#payments-eng' })).toBeVisible();
  await expectAccessible(page, 'map/jdoe/desktop');

  await page.getByTestId('infotip-map').click();
  await expect(page.getByTestId('infotip')).toContainText('lists only what that person can open');
  await expectAccessible(page, 'map/infotip');
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('infotip')).toHaveCount(0);
  await expect(page.getByTestId('infotip-map')).toBeFocused();
});

test('ken.ext: only his two places; absent platforms simply do not appear', async ({ page }) => {
  await login(page, 'ken.ext');
  await page.goto('/map');
  await expect(page.getByTestId('map-totals')).toHaveText('You can open 24 items in 2 places across 2 platforms.');
  await expect(page.getByTestId('map-place')).toHaveCount(2);
  await expect(page.getByTestId('map-platform-confluence')).toHaveCount(0);
  await expect(page.getByTestId('map-platform-jira')).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText(/hidden|withheld|restricted|more exist|not allowed/i);
  await expectAccessible(page, 'map/ken/desktop');
});

test('map @ 390px: list view, accessible, no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'jdoe');
  await page.goto('/map');
  await expect(page.getByTestId('map-totals')).toBeVisible();
  await expect(page.getByTestId('map-place-row')).toHaveCount(17);
  await page.getByTestId('map-place-row').first().click();
  await expect(page.getByTestId('map-item').first()).toBeVisible();
  await expectAccessible(page, 'map/mobile');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('light theme: map with a place open and the tour stay AA', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('ib-theme', 'light'));
  await login(page, 'jdoe');
  await page.goto('/map');
  await expect(page.getByTestId('map-loading')).toBeVisible();
  await expect(page.getByTestId('map-svg')).toBeVisible();
  await page.locator('[data-testid="map-place"][data-place="C0PAYENG"]').click();
  await expect(page.getByTestId('map-item').first()).toBeVisible();
  await expectAccessible(page, 'map/light');
  await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();
  await expect(page.getByTestId('tour-spotlight')).toBeVisible();
  await expectAccessible(page, 'tour/light');
  await page.keyboard.press('Escape');
});
