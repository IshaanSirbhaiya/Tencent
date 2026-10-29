import { type Page, expect, test } from '@playwright/test';
import { S1, SCOPE_NOTE, keyboardLogin, quietTourOffer } from './helpers';

async function tabTo(page: Page, predicate: string, max = 40) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press('Tab');
    const hit = await page.evaluate((sel) => !!document.activeElement && document.activeElement.matches(sel), predicate);
    if (hit) return;
  }
  throw new Error(`could not reach ${predicate} with Tab`);
}

test('keyboard only: sign in, ask, follow a citation chip', async ({ page }) => {
  await quietTourOffer(page);
  await page.goto('/login');
  // Skip link is the first stop.
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();

  // The persona cards are one radio group: a single Tab stop, arrows move, Enter signs in.
  await keyboardLogin(page, 'jdoe');

  await tabTo(page, '#question');
  await page.keyboard.type(S1);
  await page.keyboard.press('Enter');

  // Focus moves to the answer heading when the answer arrives.
  await expect(page.locator('#answer-heading')).toBeFocused();
  await expect(page.getByTestId('scope-note')).toHaveText(SCOPE_NOTE);

  // A chip is a real button: Enter opens the Sources sheet, Esc closes it and returns focus to the chip.
  await tabTo(page, '[data-testid="citation-chip"]');
  await page.keyboard.press('Enter');
  const sheet = page.getByTestId('sources-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId('sheet-excerpt')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);
  await expect(page.locator('[data-testid="citation-chip"]').first()).toBeFocused();

  // Source cards are buttons too, and show a visible focus indicator (outline or ring, not removed).
  await tabTo(page, '[data-testid="source-card"]');
  const focusStyle = await page.evaluate(() => {
    const cs = getComputedStyle(document.activeElement as Element);
    return { outline: cs.outlineStyle, width: cs.outlineWidth, shadow: cs.boxShadow };
  });
  const indicator = focusStyle.outline !== 'none' && focusStyle.width !== '0px' ? 'outline' : focusStyle.shadow !== 'none' ? 'ring' : 'none';
  expect(indicator).not.toBe('none');
});

test('keyboard only: audit filters, verify and drawer', async ({ page }) => {
  await quietTourOffer(page);
  await keyboardLogin(page, 'maya.compliance');
  await page.goto('/dev/audit');
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
