import { type Page, expect, test } from '@playwright/test';
import path from 'node:path';
import { login } from './helpers';

// Screenshots of every tour step, dark and light, into docs/finals/screens/tour/.
// Opt-in (TOUR_SCREENS=1) so the shared suite never rewrites the docs images.
test.skip(!process.env.TOUR_SCREENS, 'set TOUR_SCREENS=1 to capture the tour screenshots');

const OUT = path.resolve(__dirname, '../../../../../docs/finals/screens/tour');
const card = (page: Page) => page.getByTestId('tour');

async function theme(page: Page, th: 'dark' | 'light') {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem('ib-theme', t);
    } catch {
      /* storage blocked */
    }
  }, th);
}

async function settle(page: Page) {
  await page.waitForTimeout(700); // the 200 ms moves and the scroll have finished
}

async function shot(page: Page, name: string, th: string) {
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}-${th}.png`) });
}

for (const th of ['dark', 'light'] as const) {
  test(`user tour screens (${th})`, async ({ page }) => {
    test.setTimeout(300_000);
    await theme(page, th);
    // first-visit toast
    await login(page, 'jdoe');
    await expect(page.getByTestId('tour-offer')).toBeVisible();
    await shot(page, '00-offer-toast', th);
    // step 1 on the sign-in page
    await page.goto('/login');
    await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', "Highlighted: Jordan's card");
    await shot(page, '01-user-step1-signin', th);
    await card(page).getByTestId('tour-do-it').click();
    await expect(page.getByTestId('tour-counter')).toContainText('Step 2 of 4');
    await expect(page.getByTestId('tour-spotlight')).toBeVisible();
    await shot(page, '02-user-step2-ask', th);
    await card(page).getByTestId('tour-do-it').click();
    await expect(page.getByTestId('answer')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The answer');
    await shot(page, '03-user-step2-answer', th);
    await card(page).getByTestId('tour-next').click();
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: Citation 1');
    await shot(page, '04-user-step3-citation', th);
    await card(page).getByTestId('tour-do-it').click();
    await expect(page.getByTestId('sources-sheet').getByTestId('tour')).toBeVisible();
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The cited passage');
    await shot(page, '05-user-step3-source', th);
    await card(page).getByTestId('tour-next').click();
    await expect(page.getByTestId('tour-counter')).toContainText('Step 4 of 4');
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', "Highlighted: Ken's two questions");
    await shot(page, '06-user-step4-ken', th);
    await card(page).getByTestId('tour-do-it').click();
    await expect(page.getByTestId('identical-marker')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The two answers');
    await shot(page, '07-user-step4-identical', th);
    await card(page).getByTestId('tour-next').click();
    await expect(page.getByTestId('tour-checklist')).toBeVisible();
    await shot(page, '08-user-checklist', th);
    await page.getByTestId('tour-switch-dev').click();
    await expect(page.getByTestId('tour-dev-start')).toBeVisible();
    await shot(page, '09-user-checklist-done', th);
  });

  test(`developer tour screens (${th})`, async ({ page }) => {
    test.setTimeout(300_000);
    await theme(page, th);
    await login(page, 'maya.compliance');
    await page.goto('/dev');
    await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The pipeline');
    await card(page).getByTestId('tour-do-it').click();
    await expect(card(page).getByTestId('tour-step-done')).toBeVisible();
    await shot(page, '11-dev-step1-mission', th);
    await card(page).getByTestId('tour-next').click();
    await page.waitForURL((u) => u.pathname.startsWith('/dev/journey'));
    await expect(page.getByTestId('tour-spotlight')).toBeVisible({ timeout: 60_000 });
    await shot(page, '12-dev-step2-journey', th);
    await card(page).getByTestId('tour-next').click();
    await page.waitForURL((u) => u.pathname === '/dev/graph');
    await card(page).getByTestId('tour-do-it').click();
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The focused node', { timeout: 60_000 });
    await page.waitForTimeout(1200); // camera flight
    await shot(page, '13-dev-step3-graph', th);
    await card(page).getByTestId('tour-next').click();
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The controls');
    await shot(page, '14-dev-step4-trust', th);
    await card(page).getByTestId('tour-next').click();
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: Analytics', { timeout: 60_000 });
    await shot(page, '15-dev-step5-analytics', th);
    await card(page).getByTestId('tour-do-it').click();
    await expect(page.getByTestId('verify-status')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: Verify audit chain');
    await shot(page, '16-dev-step5-verify', th);
  });

  test(`developer tour, no compliance role, and 390 px (${th})`, async ({ page }) => {
    test.setTimeout(180_000);
    await theme(page, th);
    await login(page, 'jdoe');
    await page.goto('/dev');
    await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();
    await expect(page.getByTestId('tour-counter')).toContainText('Step 1 of 5');
    for (let i = 0; i < 4; i++) await card(page).getByTestId('tour-next').click();
    await expect(card(page).getByRole('heading', { name: 'Analytics and audit are locked for you' })).toBeVisible();
    await shot(page, '17-dev-step5-locked', th);
    await card(page).getByRole('button', { name: 'Finish' }).click();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.getByRole('banner').getByRole('button', { name: 'Tour' }).click();
    await expect(page.getByTestId('tour-spotlight')).toBeVisible();
    await shot(page, '18-user-step2-390', th);
  });
}
