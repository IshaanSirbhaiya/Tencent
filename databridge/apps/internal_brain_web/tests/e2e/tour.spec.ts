import { type Page, expect, test } from '@playwright/test';
import { expectAccessible, login } from './helpers';

// The guided tours (components/tour/*), in mock mode.
//   User "Try it": sign in as Jordan → ask → citation [1] → Ken's two refusals → checklist.
//   Developer: Mission control → journey → graph → Trust centre → analytics + audit verify.
// Every step is driven with "Do it for me", so the real pages do the work.

const card = (page: Page) => page.getByTestId('tour');
const counter = (page: Page) => page.getByTestId('tour-counter');

async function doIt(page: Page) {
  await card(page).getByTestId('tour-do-it').click();
}

async function next(page: Page) {
  await card(page).getByTestId('tour-next').click();
}

test('user tour runs end to end with "Do it for me", from the sign-in link', async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto('/login');
  await page.getByTestId('login-tour').click();

  // 1 → 2: the link signed in as Jordan and moved on by itself.
  await page.waitForURL((u) => u.pathname === '/');
  await expect(page.getByTestId('whoami')).toContainText('jdoe');
  await expect(counter(page)).toContainText('Step 2 of 4');
  await expect(card(page).getByRole('heading', { name: 'Ask the suggested question' })).toBeVisible();
  await expect(page.getByTestId('tour-spotlight')).toBeVisible();
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', /^Highlighted: /);
  await expect(card(page)).toBeFocused();
  await expectAccessible(page, 'tour/user-step2');

  // 2: the real ask runs.
  await doIt(page);
  await expect(page.getByTestId('answer')).toBeVisible({ timeout: 60_000 });
  await expect(card(page).getByTestId('tour-step-done')).toBeVisible();
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The answer');
  await next(page);

  // 3: citation [1] opens the sources sheet; the card moves into the sheet (inside its focus trap).
  await expect(counter(page)).toContainText('Step 3 of 4');
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: Citation 1');
  await doIt(page);
  const sheet = page.getByTestId('sources-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId('tour')).toBeVisible();
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', /Highlighted: The (cited passage|source)/);
  await expect(card(page).getByTestId('tour-step-done')).toBeVisible();
  await expectAccessible(page, 'tour/user-step3-sheet');
  await next(page);

  // 4: Compare, same person two questions: Ken on a real-but-restricted company vs a made-up one.
  await expect(counter(page)).toContainText('Step 4 of 4');
  await page.waitForURL((u) => u.pathname === '/compare' && u.searchParams.get('mode') === 'person' && u.searchParams.get('p') === 'ken.ext');
  await expect(sheet).toHaveCount(0);
  await doIt(page);
  await expect(page.getByTestId('identical-marker')).toBeVisible({ timeout: 60_000 });
  await expect(card(page).getByTestId('tour-step-done')).toBeVisible();
  await next(page);

  // The checklist card ticks itself off.
  const list = page.getByTestId('tour-checklist');
  await expect(list).toBeVisible();
  for (const k of ['asked', 'source', 'identical']) await expect(list.locator(`[data-check="${k}"]`)).toHaveAttribute('data-checked', 'true');
  await expect(list.locator('[data-check="dev"]')).toHaveAttribute('data-checked', 'false');
  await expect(page.getByTestId('tour-checklist-progress')).toHaveText('3 of 4 done');
  await expectAccessible(page, 'tour/user-checklist');
  await page.getByTestId('tour-switch-dev').click();
  await page.waitForURL((u) => u.pathname === '/dev');
  await expect(list.locator('[data-check="dev"]')).toHaveAttribute('data-checked', 'true');
  await expect(page.getByTestId('tour-dev-start')).toBeVisible();
  const done = await page.evaluate(() => JSON.parse(localStorage.getItem('ib-tour-done') ?? '{}'));
  expect(done.user).toBe(true);
  await page.getByTestId('tour-end-close').click();
  await expect(card(page)).toHaveCount(0);
});

test('Skip closes the tour and retires the first-visit offer (light theme, axe)', async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-theme', 'light');
    } catch {
      /* storage blocked */
    }
  });
  await login(page, 'jdoe');
  await expect(page.getByTestId('tour-offer')).toBeVisible();
  await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();
  await expect(card(page)).toBeVisible();
  // Already Jordan: the tour starts at the ask step.
  await expect(counter(page)).toContainText('Step 2 of 4');
  await expect(page.getByTestId('tour-spotlight')).toBeVisible();
  await expectAccessible(page, 'tour/user-step2-light');
  await card(page).getByRole('button', { name: 'Skip tour' }).click();
  await expect(card(page)).toHaveCount(0);
  await expect(page.getByTestId('tour-spotlight')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId('whoami')).toContainText('jdoe');
  await expect(page.getByTestId('tour-offer')).toHaveCount(0);
});

test('keyboard only: Enter starts, Do it for me, arrows move, Esc closes and focus returns', async ({ page }) => {
  test.setTimeout(180_000);
  await login(page, 'jdoe');
  const launch = page.getByTestId('tour-launch');
  await launch.focus();
  await page.keyboard.press('Enter');
  await expect(card(page)).toBeFocused();
  await expect(counter(page)).toContainText('Step 2 of 4');

  // Tab to "Do it for me" (inside the card) and press Enter.
  for (let i = 0; i < 8; i++) {
    if (await page.getByTestId('tour-do-it').evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press('Tab');
  }
  await expect(page.getByTestId('tour-do-it')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('answer')).toBeVisible({ timeout: 60_000 });
  // Focus lands on the way forward.
  await expect(card(page).getByTestId('tour-next')).toBeFocused();

  await page.keyboard.press('ArrowRight');
  await expect(counter(page)).toContainText('Step 3 of 4');
  await page.keyboard.press('ArrowLeft');
  await expect(counter(page)).toContainText('Step 2 of 4');
  await page.keyboard.press('Escape');
  await expect(card(page)).toHaveCount(0);
  await expect(launch).toBeFocused();
});

test('developer tour runs as maya.compliance', async ({ page }) => {
  test.setTimeout(240_000);
  await login(page, 'maya.compliance');
  await page.goto('/dev');
  await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();

  // 1 Mission control: select Gate 2.
  await expect(counter(page)).toContainText('Developer tour');
  await expect(counter(page)).toContainText('Step 1 of 5');
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The pipeline');
  await expectAccessible(page, 'tour/dev-step1');
  await doIt(page);
  await expect(card(page).getByTestId('tour-step-done')).toBeVisible();
  await expect(page.getByTestId('pipeline-node-gate2')).toHaveAttribute('aria-pressed', 'true');
  await next(page);

  // 2 The journey of the last answer (or the request list when there is none).
  await expect(counter(page)).toContainText('Step 2 of 5');
  await page.waitForURL((u) => u.pathname.startsWith('/dev/journey'));
  await expect(page.getByTestId('tour-spotlight')).toBeVisible({ timeout: 60_000 });
  await next(page);

  // 3 Knowledge graph: search and focus a node.
  await expect(counter(page)).toContainText('Step 3 of 5');
  await page.waitForURL((u) => u.pathname === '/dev/graph');
  await doIt(page);
  await expect(page.getByTestId('graph-main-panel')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The focused node');
  await next(page);

  // 4 Trust centre.
  await expect(counter(page)).toContainText('Step 4 of 5');
  await page.waitForURL((u) => u.pathname === '/dev/trust');
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: The controls');
  await next(page);

  // 5 Compliance: analytics, then verify the audit chain.
  await expect(counter(page)).toContainText('Step 5 of 5');
  await page.waitForURL((u) => u.pathname === '/dev/analytics');
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: Analytics', { timeout: 60_000 });
  await doIt(page);
  await page.waitForURL((u) => u.pathname === '/dev/audit');
  await expect(page.getByTestId('verify-status')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('tour-spotlight')).toHaveAttribute('aria-label', 'Highlighted: Verify audit chain');
  await card(page).getByRole('button', { name: 'Finish' }).click();
  await expect(card(page)).toHaveCount(0);
  const done = await page.evaluate(() => JSON.parse(localStorage.getItem('ib-tour-done') ?? '{}'));
  expect(done.dev).toBe(true);
});

test('developer tour without the compliance role ends on the least-privilege note', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev');
  await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();
  await expect(counter(page)).toContainText('Step 1 of 5');
  for (let i = 0; i < 4; i++) await next(page);
  await expect(counter(page)).toContainText('Step 5 of 5');
  await expect(card(page).getByRole('heading', { name: 'Analytics and audit are locked for you' })).toBeVisible();
  await expect(card(page)).toContainText('compliance role only');
  await expect(card(page).getByRole('button', { name: /Do it for me: continue as Maya/ })).toBeVisible();
  await expect(card(page).getByTestId('tour-missing')).toHaveCount(0);
});

test('missing anchor: the card centres with a fallback note', async ({ page }) => {
  test.setTimeout(90_000);
  await login(page, 'jdoe');
  await page.getByRole('banner').getByRole('button', { name: 'Take the tour' }).click();
  await expect(counter(page)).toContainText('Step 2 of 4');
  // Step 3 with no answer on screen and nothing to click: hide every anchor it could use.
  await page.addStyleTag({ content: '[data-tour], [data-testid="starter"] { display: none !important; }' });
  await next(page);
  await expect(card(page).getByTestId('tour-missing')).toBeVisible({ timeout: 20_000 });
  await expect(card(page)).toHaveAttribute('data-tour-layout', 'center');
  await expect(page.getByTestId('tour-spotlight')).toHaveCount(0);
});

test('tour @ 390px fits the screen; 简体中文 strings', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-lang', 'zh');
    } catch {
      /* storage blocked */
    }
  });
  await login(page, 'jdoe');
  await page.getByRole('banner').getByRole('button', { name: '导览' }).click();
  await expect(card(page).getByRole('heading', { name: '提出建议的问题' })).toBeVisible();
  await expect(counter(page)).toContainText('第 2 步，共 4 步');
  await expect(page.getByTestId('tour-spotlight')).toBeVisible();
  const box = await card(page).boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expectAccessible(page, 'tour/mobile-zh');
});

test('"Not now" dismisses the first-visit toast for good', async ({ page }) => {
  await login(page, 'ken.ext');
  const offer = page.getByTestId('tour-offer');
  await expect(offer).toBeVisible();
  await expectAccessible(page, 'tour/offer');
  await offer.getByRole('button', { name: 'Not now' }).click();
  await expect(offer).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId('whoami')).toContainText('ken.ext');
  await expect(page.getByTestId('tour-offer')).toHaveCount(0);
});
