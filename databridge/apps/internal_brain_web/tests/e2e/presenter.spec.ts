import { expect, test, type Page } from '@playwright/test';
import { PERSONAS } from '../../lib/personas';
import { REFUSAL_TEXT, expectAccessible, login } from './helpers';

// Presenter mode: the Demo Day script driven from the keyboard alone (PRESENTER_KEYS.md).
// Keys are off unless presenter mode is armed (WCAG 2.1.4).

const QV = "Why has Velquarry Trading's account been restricted, and what should I tell the customer?";
const QB = "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?";

const bar = (page: Page) => page.getByTestId('presenter-bar');
const stepNo = (page: Page) => page.getByTestId('presenter-step-number');

/** Press Space once the previous step has finished (the bar stops saying "Working…"). */
async function next(page: Page) {
  await page.waitForTimeout(450); // the bar debounces a double press (400 ms)
  await page.keyboard.press('Space');
}

test('the script uses the exact persona questions', () => {
  // lib/personas.ts is the source of the questions; the script must not drift from it.
  const amy = PERSONAS.find((p) => p.id === 'amy.am')!;
  expect(amy.suggestions).toContain(QV);
  expect(amy.suggestions).toContain(QB);
});

test('not armed: presenter keys do nothing', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/compare');
  for (const k of ['p', 'Space', 'b', 'r', 'l', 'ArrowRight']) await page.keyboard.press(k);
  await expect(bar(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/compare$/);
  await expect(page.locator('html')).not.toHaveAttribute('data-stage', /.*/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});

test('armed: keys are ignored while typing, and Ctrl+Shift+P disarms', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/compare?present=1');
  await expect(bar(page)).toBeVisible();
  await expect(page.getByTestId('presenter-step-title')).toHaveText('Press P to start');
  const q = page.getByLabel('Question for both');
  await q.fill('');
  await q.press('p');
  await q.press('l');
  await expect(q).toHaveValue('pl');
  await expect(page).toHaveURL(/\/compare/);
  await page.locator('body').click({ position: { x: 5, y: 300 } });
  await page.keyboard.press('Control+Shift+P');
  await expect(bar(page)).toHaveCount(0);
  await page.keyboard.press('p');
  await expect(page).toHaveURL(/\/compare/);
});

test('P then Space walks the whole script from the keyboard', async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, 'jdoe');
  await page.goto('/compare?present=1');
  await expect(bar(page)).toBeVisible();

  // 0: title card, Stage on, main session = Rachel (her sources open with the live passage).
  await page.keyboard.press('p');
  await expect(page).toHaveURL(/\/present$/);
  await expect(stepNo(page)).toHaveText('00');
  await expect(page.locator('html')).toHaveAttribute('data-stage', '1');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.getByRole('heading', { level: 1, name: 'Walnut DataBridge' })).toBeVisible();
  await expect.poll(async () => (await (await page.request.get('/api/ib/auth/whoami')).json()).sub).toBe('mlro.tan');

  // 1: Amy vs Rachel on Velquarry, asked at once.
  await next(page);
  await expect(page).toHaveURL(/a=amy\.am&b=mlro\.tan/);
  await expect(page.getByTestId('compare-c').getByTestId('answer')).toBeVisible();
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();
  await expect(stepNo(page)).toHaveText('01');
  const rachelReq = await page.getByTestId('compare-c').getByTestId('request-id').innerText();
  await expect(page.getByTestId('compare-shared-question')).toBeVisible(); // Stage shows the question large
  await expectAccessible(page, 'presenter/stage-compare');

  // 2: Ken, Velquarry vs Bayfront: identical refusals.
  let asks = 0;
  page.on('request', (r) => {
    if (r.url().endsWith('/api/ib/ib/ask')) asks += 1;
  });
  await next(page);
  await expect(page).toHaveURL(/mode=person/);
  await expect(page.getByTestId('identical-marker')).toBeVisible();
  await expect(page.getByTestId('compare-b').getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  await expect(page.getByTestId('compare-c').getByTestId('compare-question')).toContainText(QB);
  await expect(stepNo(page)).toHaveText('02');
  expect(asks).toBe(2);

  // 3: back to Amy and Rachel (restored, not re-asked), source [2] on Rachel's answer.
  await next(page);
  const sheet = page.getByTestId('sources-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText('Source 2');
  expect(asks).toBe(2);
  await expect(page.getByTestId('compare-c').getByTestId('request-id')).toHaveText(rachelReq);
  await page.keyboard.press('Escape'); // closes the sheet, not the bar
  await expect(sheet).toHaveCount(0);
  await expect(page.getByTestId('compare-c').getByTestId('citation-chip').nth(1)).toBeFocused();
  await expect(bar(page)).toBeVisible();

  // 4: Rachel's request, as Maya (the main session switches).
  await next(page);
  await expect(page).toHaveURL(new RegExp(`/dev/journey/${rachelReq}$`));
  await expect.poll(async () => (await (await page.request.get('/api/ib/auth/whoami')).json()).sub).toBe('maya.compliance');

  // 5, 6: audit Verify, then the sandbox tamper. The keys keep working on pages without the bar.
  await next(page);
  await expect(page).toHaveURL(/\/dev\/audit\?verify=1$/);
  await next(page);
  await expect(page).toHaveURL(/\/dev\/audit\?tamper=1$/);

  // 7: WorkBuddy is off-app: the app goes back to the real ledger view.
  await next(page);
  await expect(page).toHaveURL(/\/dev\/audit$/);

  // 8, 9, 10: the slides.
  await next(page);
  await expect(page).toHaveURL(/\/present\/proof$/);
  await expect(page.getByTestId('proof-leaks')).toBeVisible();
  await expectAccessible(page, 'presenter/proof');
  await next(page);
  await expect(page).toHaveURL(/\/present\/business$/);
  await expect(page.getByTestId('business-estimate')).toHaveText('Estimate');
  await expect(page.getByTestId('business-value')).toContainText('US$1.28M'); // 600 × 1.5 h × 46 × US$31
  await expect(page.getByTestId('business-break-even')).toContainText('7.0');
  await next(page);
  await expect(page).toHaveURL(/\/present\/close$/);
  await expect(page.getByTestId('close-tagline')).toHaveText('Answers you can prove.');
  await expect(page.getByTestId('close-ask')).toHaveText('One pilot team at one regulated company.');
  await expect(page.getByRole('img', { name: /QR code for github\.com\/IshaanSirbhaiya\/Tencent/ })).toBeVisible();
  await expect(stepNo(page)).toHaveText('10');
  await expectAccessible(page, 'presenter/close');

  // ← goes back one step without re-running anything.
  await page.keyboard.press('ArrowLeft');
  await expect(page).toHaveURL(/\/present\/business$/);
  await expect(stepNo(page)).toHaveText('09');
});

test('B plays the recording (or says it is not recorded yet) and B returns; L flips the language', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/present?present=1');
  await expect(bar(page)).toBeVisible(); // the keys are live once the page has hydrated
  await page.keyboard.press('p');
  await expect(stepNo(page)).toHaveText('00');
  await page.keyboard.press('b');
  const overlay = page.getByTestId('clip-overlay');
  await expect(overlay).toBeVisible();
  await expect(overlay).toContainText('Recording · not live');
  await expect(overlay.getByTestId('clip-missing').or(overlay.getByTestId('clip-video'))).toBeVisible();
  await page.keyboard.press('b');
  await expect(overlay).toHaveCount(0);

  await page.keyboard.press('l');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
  await expect(page.getByTestId('presenter-demo-data')).toHaveText('演示数据：虚构的 A 公司');
  await page.keyboard.press('l');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  // Stage stays light through the language flip.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('R asks first, then signs every slot out and returns to step 0; Esc hides the bar', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/compare?present=1');
  await expect(bar(page)).toBeVisible();
  await page.keyboard.press('p');
  await expect(page).toHaveURL(/\/present$/);
  await next(page);
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();

  await page.keyboard.press('r');
  const dialog = page.getByRole('dialog', { name: 'Reset the demo?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).first().click();
  await expect(dialog).toHaveCount(0);
  await expect(stepNo(page)).toHaveText('01');

  await page.keyboard.press('r');
  await page.getByTestId('presenter-reset-confirm').click();
  await expect(page).toHaveURL(/\/present$/);
  await expect(stepNo(page)).toHaveText('00');
  expect((await page.request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': 'b' } })).status()).toBe(401);
  expect((await page.request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': 'c' } })).status()).toBe(401);
  // Step 0 signs the main session back in.
  await expect.poll(async () => (await (await page.request.get('/api/ib/auth/whoami')).json()).sub).toBe('mlro.tan');

  await page.keyboard.press('Escape');
  await expect(bar(page)).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(bar(page)).toBeVisible();
});

test('slides read their numbers from data files; a missing eval summary says "re-run pending"', async ({ page }) => {
  await page.route('**/eval_summary.json', (r) => r.fulfill({ status: 404, body: '' }));
  await page.goto('/present/proof');
  await expect(page.getByTestId('proof-leaks')).toContainText('Re-run pending');
  await expect(page.getByTestId('proof-tester')).toHaveCount(0); // fixlog.json is empty
  await page.unroute('**/eval_summary.json');
  await page.goto('/present/proof');
  const ev = await (await page.request.get('/eval_summary.json')).json();
  await expect(page.getByTestId('proof-leaks')).toContainText(ev.requests.toLocaleString('en-SG'));
  await expect(page.getByTestId('proof-diffs')).toContainText(ev.differential.pairs.toLocaleString('en-SG'));
});
