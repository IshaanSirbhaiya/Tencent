import { expect, test } from '@playwright/test';
import { login } from './helpers';

test('trust centre: seven controls, crosswalk counts from FRAMEWORK_CROSSWALK.md, honest wording', async ({ page }) => {
  await login(page, 'ken.ext'); // any signed-in person
  await page.goto('/dev/trust');
  await expect(page.getByRole('heading', { name: 'Trust centre', level: 1 })).toBeVisible();
  await expect(page.getByTestId('trust-controls').locator(':scope > li')).toHaveCount(7);
  await expect(page.getByTestId('control-shape')).toContainText('CDSA s.57');
  await expect(page.getByTestId('control-ledger')).toContainText('test_audit_tamper.py');

  const ai = page.getByTestId('fw-ai-verify');
  await expect(ai.locator('[data-col="met"]')).toHaveText('6');
  await expect(ai.locator('[data-col="partial"]')).toHaveText('5');
  await expect(ai.locator('[data-col="gap"]')).toHaveText('0');
  const pdpa = page.getByTestId('fw-pdpa');
  await expect(pdpa.locator('[data-col="gap"]')).toHaveText('2');
  for (const id of ['imda-genai', 'mas-feat', 'mas-airg']) await expect(page.getByTestId(`fw-${id}`)).toBeVisible();
  await expect(page.getByTestId('owasp').locator('tbody tr')).toHaveCount(10);

  await expect(page.getByText('Self-assessed', { exact: true })).toBeVisible();
  await expect(page.getByTestId('never-claims')).toContainText('Certified or audited');
  // "compliant" and "certified" appear only inside the list of claims we never make
  const main = (await page.locator('main').innerText()).replace(/\s+/g, ' ');
  const never = (await page.getByTestId('never-claims').innerText()).replace(/\s+/g, ' ');
  const outside = main.replace(never, '');
  expect(outside).not.toMatch(/\b(compliant|certified)\b/i);
});
