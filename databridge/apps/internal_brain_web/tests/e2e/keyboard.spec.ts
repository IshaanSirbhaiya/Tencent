import { type Page, expect, test } from '@playwright/test';
import { S1, SCOPE_NOTE } from './helpers';

async function tabTo(page: Page, predicate: string, max = 40) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press('Tab');
    const hit = await page.evaluate((sel) => !!document.activeElement && document.activeElement.matches(sel), predicate);
    if (hit) return;
  }
  throw new Error(`could not reach ${predicate} with Tab`);
}

test('keyboard only: sign in, ask, follow a citation chip', async ({ page }) => {
  await page.goto('/login');
  // Skip link is the first stop.
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();

  await tabTo(page, '[data-persona="jdoe"]');
  await page.keyboard.press('Enter');
  await page.waitForURL('**/');
  await expect(page.getByTestId('whoami')).toContainText('jdoe');

  await tabTo(page, '#question');
  await page.keyboard.type(S1);
  await page.keyboard.press('Enter');

  // Focus moves to the answer heading when the answer arrives.
  await expect(page.locator('#answer-heading')).toBeFocused();
  await expect(page.getByTestId('scope-note')).toHaveText(SCOPE_NOTE);

  await tabTo(page, '[data-testid="citation-chip"]');
  const tag = await page.evaluate(() => document.activeElement?.textContent);
  await page.keyboard.press('Enter');
  await expect(page.locator(`#cite-${tag}`)).toBeFocused();

  // Visible focus indicator is present (outline not removed).
  await tabTo(page, '[data-testid="citations"] a');
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe('none');
});

test('keyboard only: audit filters, verify and drawer', async ({ page }) => {
  await page.goto('/login');
  await tabTo(page, '[data-persona="maya.compliance"]');
  await page.keyboard.press('Enter');
  await page.waitForURL('**/');
  await page.goto('/audit');
  await expect(page.getByTestId('audit-table')).toBeVisible();

  await tabTo(page, '#nl-question');
  await page.keyboard.type('What did jdoe access in the payment-gateway space over the last 30 days?');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('parsed-filter')).toContainText('jdoe');
  await expect(page.getByTestId('parsed-filter')).toContainText('payment-gateway');

  await tabTo(page, 'button[aria-label^="Details for request"]', 80);
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('audit-drawer')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('audit-drawer')).toHaveCount(0);
});
