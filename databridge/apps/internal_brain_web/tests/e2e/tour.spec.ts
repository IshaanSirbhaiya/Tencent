import { expect, test } from '@playwright/test';
import { S1, expectAccessible, login } from './helpers';

test('tour opens, moves with Next and arrow keys, closes with Esc', async ({ page }) => {
  await login(page, 'jdoe');
  await expect(page.getByTestId('tour-offer')).toBeVisible();
  await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();

  const tour = page.getByTestId('tour');
  await expect(tour).toBeVisible();
  await expect(page.getByTestId('tour-counter')).toHaveText(/step 1 of 6/i);
  await page.waitForURL('**/login');
  await expect(page.getByTestId('tour-spotlight')).toBeVisible();
  await expect(tour.getByRole('heading', { name: 'Pick a person' })).toBeVisible();
  await expect(tour).toBeFocused();
  await expectAccessible(page, 'tour/step1');

  // Next signs in as Jordan, asks the sample question, and highlights the answer.
  await tour.getByRole('button', { name: 'Sign in as Jordan' }).click();
  await expect(page.getByTestId('tour-counter')).toHaveText(/step 2 of 6/i);
  await expect(page.getByTestId('answer')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('tour-spotlight')).toBeVisible({ timeout: 20_000 });
  await expect(tour).toContainText('checks line');

  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('tour-counter')).toHaveText(/step 3 of 6/i);
  await page.waitForURL('**/map');
  await expect(page.getByTestId('tour-spotlight')).toBeVisible({ timeout: 20_000 });
  await expectAccessible(page, 'tour/map');

  await page.keyboard.press('ArrowLeft');
  await expect(page.getByTestId('tour-counter')).toHaveText(/step 2 of 6/i);
  await page.waitForURL((u) => u.pathname === '/');

  await page.keyboard.press('Escape');
  await expect(tour).toHaveCount(0);
  await expect(page.getByTestId('tour-spotlight')).toHaveCount(0);
});

test('tour @ 390px fits the screen; starting it retires the first-visit offer', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'jdoe');
  await expect(page.getByTestId('tour-offer')).toBeVisible();
  await page.getByTestId('tour-offer').getByRole('button', { name: 'Take the tour' }).click();
  await expect(page.getByTestId('tour-spotlight')).toBeVisible();
  const box = await page.getByTestId('tour').boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expectAccessible(page, 'tour/mobile');
  await page.getByRole('button', { name: 'Skip tour' }).click();
  await expect(page.getByTestId('tour')).toHaveCount(0);

  await login(page, 'jdoe');
  await expect(page.getByTestId('tour-offer')).toHaveCount(0);
});

test('"Not now" dismisses the offer for good', async ({ page }) => {
  await login(page, 'ken.ext');
  await page.getByTestId('tour-offer').getByRole('button', { name: 'Not now' }).click();
  await expect(page.getByTestId('tour-offer')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId('whoami')).toContainText('ken.ext');
  await expect(page.getByTestId('tour-offer')).toHaveCount(0);
});

test('answer checks strip states who, the live checks and the audit request id', async ({ page }) => {
  await login(page, 'jdoe');
  await page.getByLabel('Your question').fill(S1);
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  const strip = page.getByTestId('checks-strip');
  await expect(strip).toContainText('Signed in as jdoe');
  await expect(strip).toContainText('Only sources you can open');
  await expect(strip).toContainText(/Every source checked live just before use \(2 of 2/);
  const id = await page.getByTestId('request-id').innerText();
  await expect(strip).toContainText(`Recorded in the audit log (request ${id})`);
});
