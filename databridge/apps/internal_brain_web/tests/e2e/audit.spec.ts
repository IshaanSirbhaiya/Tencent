import { expect, test } from '@playwright/test';
import { login } from './helpers';

test('audit: NL question shows the parsed filter before results; manual filter; verify; reveal', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.getByRole('link', { name: 'Audit' }).click();
  await expect(page.getByRole('heading', { name: 'Audit console' })).toBeVisible();
  const table = page.getByTestId('audit-table');
  await expect(table.locator('tbody tr').first()).toBeVisible();
  await expect(table).toContainText('Synthetic replay');

  // Natural-language audit → parsed chips, then rows that all carry a Deny decision.
  await page.getByRole('button', { name: 'Show every denied retrieval last week.' }).click();
  const parsed = page.getByTestId('parsed-filter');
  await expect(parsed).toContainText('Decision:');
  await expect(parsed).toContainText('deny');
  await expect(parsed).toContainText('Since:');
  const rows = table.locator('tbody tr');
  const n = await rows.count();
  expect(n).toBeGreaterThan(0);
  for (let i = 0; i < n; i++) await expect(rows.nth(i)).toContainText('Deny');

  // Manual filter by user.
  await page.getByRole('button', { name: 'Clear' }).click();
  await page.getByLabel('User').selectOption('sara.sec');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect(rows.first()).toContainText('sara.sec');
  const m = await rows.count();
  for (let i = 0; i < m; i++) await expect(rows.nth(i).locator('td').nth(1)).toHaveText('sara.sec');

  // Verify the chain.
  await page.getByRole('button', { name: 'Verify audit chain' }).click();
  await expect(page.getByTestId('verify-status')).toContainText('Chain intact');
  await expect(page.getByTestId('verify-panel')).toContainText('Matches published head');
  await expect(page.getByTestId('verify-records')).toHaveText(/\d/);

  // Row drawer → reveal needs a reason.
  await rows.first().getByRole('button', { name: /Details for request/ }).click();
  const drawer = page.getByTestId('audit-drawer');
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText('Your reveal is logged under your name');
  await drawer.getByRole('button', { name: 'Reveal answer' }).click();
  await expect(drawer.getByText('Please give a fuller reason.')).toBeVisible();
  await expect(drawer.getByTestId('revealed-answer')).toHaveCount(0);
  await drawer.getByLabel('Reason for revealing (required)').fill('Investigating GRC-14: confirm what was disclosed');
  await drawer.getByRole('button', { name: 'Reveal answer' }).click();
  await expect(drawer.getByTestId('revealed-answer')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
});
