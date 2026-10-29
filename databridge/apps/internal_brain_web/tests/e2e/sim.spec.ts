import { expect, test } from '@playwright/test';
import { login, resetDemo } from './helpers';

test('simulator: each action says what it proves and links to a prefilled Compare', async ({ page }) => {
  await login(page, 'admin.it');
  await page.goto('/sim');
  await expect(page).toHaveURL(/\/dev\/sim$/);
  await expect(page.getByRole('heading', { name: 'Platform simulator', level: 1 })).toBeVisible();
  const card = page.getByTestId('sim-card-revoke-channel');
  await expect(card).toContainText('Scenario 4');
  await expect(card).toContainText('test_s4_revocation_without_webhook');
  await expect(card).toContainText('Then ask this as Jordan');
  const link = card.getByRole('link', { name: 'Ask as Jordan and Priya side by side' });
  const href = await link.getAttribute('href');
  const u = new URL(href ?? '', 'http://x');
  expect(u.pathname).toBe('/compare');
  expect(u.searchParams.get('left')).toBe('jdoe');
  expect(u.searchParams.get('right')).toBe('priya.sre');
  expect(u.searchParams.get('q')).toBe('What backfill throttle are we using for the ledger copy job?');

  await page.getByRole('button', { name: 'Remove jdoe from #db-migration' }).click();
  await expect(page.getByTestId('sim-log')).toContainText('Done');
  await expect(card).toContainText('Applied');
  await resetDemo(page);
});

test('simulator is IT-admin only', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/sim');
  await expect(page.getByTestId('role-lock')).toBeVisible();
});
