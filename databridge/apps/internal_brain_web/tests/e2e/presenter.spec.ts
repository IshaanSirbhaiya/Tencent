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

/** The bar's state: ready | busy | failed | resetting. */
const barState = (page: Page) => bar(page).getAttribute('data-state');

/** Press `key` for a step, wait until that step has started and finished; returns the bar state. */
async function press(page: Page, key: 'Space' | 'ArrowRight' = 'Space') {
  await page.waitForTimeout(450); // the bar debounces a double press (400 ms)
  await page.keyboard.press(key);
  await expect.poll(() => barState(page)).toBe('busy');
  await expect.poll(() => barState(page), { timeout: 30_000 }).not.toBe('busy');
  return barState(page);
}

/** What the presenter remembers per persona (sessionStorage, this tab only). */
const remembered = (page: Page) =>
  page.evaluate(() => {
    try {
      return (JSON.parse(sessionStorage.getItem('ib.presenter') ?? '{}').reqs ?? {}) as Record<string, string>;
    } catch {
      return {} as Record<string, string>;
    }
  });

const slotWhoami = (page: Page, slot: 'b' | 'c') => page.request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': slot } });
const mainSub = async (page: Page) => (await (await page.request.get('/api/ib/auth/whoami')).json()).sub;

/** Start presenting from the title slide (?present=1 lands there before P). */
async function startPresenting(page: Page, persona = 'jdoe') {
  await login(page, persona);
  await page.goto('/compare?present=1');
  await expect(page).toHaveURL(/\/present$/);
  await expect(bar(page)).toBeVisible();
  await page.keyboard.press('p');
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page)).toBe('ready');
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
  await page.goto('/compare');
  await expect(page.getByLabel('Question for both')).toBeVisible();
  await page.locator('body').click({ position: { x: 5, y: 300 } });
  await page.keyboard.press('Control+Shift+P');
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
  // Before P the room sees the title slide in Stage, not an idle Compare page.
  await expect(page).toHaveURL(/\/present$/);
  await expect(page.locator('html')).toHaveAttribute('data-stage', '1');
  await expect(bar(page)).toBeVisible();
  await expect(page.getByTestId('presenter-step-title')).toHaveText('Press P to start');

  // 0: title card, Stage on, main session = Rachel (her sources open with the live passage).
  await page.keyboard.press('p');
  await expect(page).toHaveURL(/\/present$/);
  await expect(stepNo(page)).toHaveText('00');
  // Stage: one row; the key legend shows for 3 s after P, then again on ?.
  await expect(bar(page)).toHaveAttribute('data-compact', 'true');
  await expect(page.getByTestId('presenter-keys')).toBeVisible();
  await expect(page.getByTestId('presenter-keys')).toHaveCount(0, { timeout: 6_000 });
  await page.keyboard.press('?');
  await expect(page.getByTestId('presenter-keys')).toBeVisible();
  await page.keyboard.press('?');
  await expect(page.getByTestId('presenter-keys')).toHaveCount(0);
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
  // Stage on Compare: no User/Developer switch, no "who am I" chip (each column says who it is).
  await expect(page.getByTestId('mode-switch').first()).toBeHidden();
  await expect(page.getByTestId('whoami')).toBeHidden();
  await expect(page.getByTestId('compare-c').getByTestId('compare-name')).toHaveText('Rachel Tan');
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

  // 3: back to Amy and Rachel (restored, not re-asked), source [2] on Rachel's answer. Even if
  // something changed the main session meanwhile, the step signs Rachel in again: live passage.
  await page.request.post('/api/ib/auth/demo-login', { data: { persona: 'jdoe' } });
  await next(page);
  const sheet = page.getByTestId('sources-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText('Source 2');
  await expect(sheet.getByTestId('sheet-excerpt')).toBeVisible();
  expect(await mainSub(page)).toBe('mlro.tan');
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
  // It opens on the waterfall; off Compare the who-am-I chip is back (Maya is the point here).
  await expect(page.getByTestId('journey-waterfall')).toBeInViewport();
  await expect(page.getByTestId('whoami')).toBeVisible();

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
  // Big: what is spoken. Small: the exact value (600 × 1.5 h × 46 × US$31).
  await expect(page.getByTestId('business-value-rounded')).toHaveText(/≈\s*US\$1\.3M/);
  await expect(page.getByTestId('business-value-exact')).toContainText('US$1,283,400');
  await expect(page.getByTestId('business-value-exact')).toContainText('US$1.28M');
  await expect(page.getByTestId('business-break-even-rounded')).toHaveText(/about\s*7$/);
  await expect(page.getByTestId('business-break-even-exact')).toContainText('7.0');
  // Hy3 and TokenHub are named only while /ib/status says the model is live (read at view time).
  const live = (await (await page.request.get('/api/ib/ib/status')).json())?.models?.active === true;
  await expect(page.getByTestId('business-stack')).toHaveText(
    live ? 'Tencent stack: Hunyuan Hy3 through TokenHub · WorkBuddy · CodeBuddy' : 'Tencent stack: WorkBuddy · CodeBuddy',
  );
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

  // Once the script is over, the bar stays out of the way on other pages (and is back on the slides).
  await next(page);
  await expect(stepNo(page)).toHaveText('10');
  await expect.poll(() => barState(page)).toBe('ready');
  await page.goto('/dev/graph');
  await expect(bar(page)).toHaveCount(0);
  await page.goto('/present/close');
  await expect(bar(page)).toBeVisible();
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
  await startPresenting(page);
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

test('slides name Hy3 and TokenHub only while the model is live', async ({ page }) => {
  await login(page, 'jdoe');
  let active = false;
  await page.route('**/api/ib/ib/status', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: { ...body, models: { ...body.models, active } } });
  });
  await page.goto('/present/proof');
  await expect(page.getByTestId('proof-codebuddy')).toContainText('Six CodeBuddy sessions, transcripts in the repo.');
  await expect(page.getByTestId('proof-codebuddy')).not.toContainText(/hy3|TokenHub|Every session transcript/i);
  await page.goto('/present/business');
  await expect(page.getByTestId('business-stack')).toHaveText('Tencent stack: WorkBuddy · CodeBuddy');
  active = true;
  await page.goto('/present/business');
  await expect(page.getByTestId('business-stack')).toHaveText('Tencent stack: Hunyuan Hy3 through TokenHub · WorkBuddy · CodeBuddy');
  await page.goto('/present/proof');
  await expect(page.getByTestId('proof-codebuddy')).toContainText('security review on Tencent Hunyuan Hy3.');
});

/* ---------------------------------------------------- failure, reset and stale results */

test('a slot sign-in failure fails the Compare step: it does not advance; Space retries, → skips', async ({ page }) => {
  test.setTimeout(90_000);
  await startPresenting(page);
  // Rachel's slot cannot sign in (stands in for a 403 from demo-login).
  let failLogin = true;
  await page.route('**/api/ib/auth/demo-login', (route) => {
    if (failLogin && route.request().headers()['x-ib-slot'] === 'c') return route.fulfill({ status: 403, json: { detail: 'forbidden' } });
    return route.fallback();
  });

  expect(await press(page)).toBe('failed');
  await expect(stepNo(page)).toHaveText('01');
  await expect(page.getByTestId('presenter-failed')).toContainText('Space tries again');
  await expect(page.getByTestId('compare-c').getByRole('alert')).toBeVisible();
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible(); // one side came through
  expect(await remembered(page)).toEqual({}); // nothing to carry into later steps

  // Space on a failed step tries it again; still failing, still step 1.
  expect(await press(page)).toBe('failed');
  await expect(stepNo(page)).toHaveText('01');

  // Fixed: Space retries and now succeeds, without moving on by itself.
  failLogin = false;
  expect(await press(page)).toBe('ready');
  await expect(stepNo(page)).toHaveText('01');
  await expect(page.getByTestId('compare-c').getByTestId('answer')).toBeVisible();
  const rachelReq = await page.getByTestId('compare-c').getByTestId('request-id').innerText();
  expect((await remembered(page))['mlro.tan']).toBe(rachelReq);

  // → skips a failed step on purpose.
  failLogin = true;
  await page.keyboard.press('ArrowLeft');
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page)).toBe('ready');
  expect(await press(page)).toBe('failed');
  await press(page, 'ArrowRight');
  await expect(stepNo(page)).toHaveText('02');
});

test('no stale request id after a failed run: later steps use the new answer, never an earlier rehearsal', async ({ page }) => {
  test.setTimeout(120_000);
  await startPresenting(page);
  // Rehearsal: step 1 succeeds and remembers Rachel's request.
  expect(await press(page)).toBe('ready');
  const oldReq = await page.getByTestId('compare-c').getByTestId('request-id').innerText();
  expect((await remembered(page))['mlro.tan']).toBe(oldReq);

  // Back to the title, then step 1 again, and this time Rachel's slot fails.
  await page.keyboard.press('ArrowLeft');
  await expect(stepNo(page)).toHaveText('00');
  let failLogin = true;
  await page.route('**/api/ib/auth/demo-login', (route) => {
    if (failLogin && route.request().headers()['x-ib-slot'] === 'c') return route.fulfill({ status: 403, json: { detail: 'forbidden' } });
    return route.fallback();
  });
  await expect.poll(() => barState(page)).toBe('ready');
  expect(await press(page)).toBe('failed');
  // The earlier rehearsal's ids are gone the moment the run started.
  const reqs = await remembered(page);
  expect(reqs['mlro.tan']).toBeUndefined();
  expect(reqs['amy.am']).toBeUndefined();

  // Skip on, then the citation step asks again (the failed comparison is not "restored").
  failLogin = false;
  expect(await press(page, 'ArrowRight')).toBe('ready');
  await expect(stepNo(page)).toHaveText('02');
  await next(page);
  await expect(page.getByTestId('sources-sheet')).toBeVisible();
  await expect(stepNo(page)).toHaveText('03');
  const newReq = await page.getByTestId('compare-c').getByTestId('request-id').innerText();
  expect(newReq).not.toBe(oldReq);
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('sources-sheet')).toHaveCount(0);

  // The journey opens Rachel's NEW request.
  await next(page);
  await expect(page).toHaveURL(new RegExp(`/dev/journey/${newReq}$`));
});

test('reset during a pending Compare step: the step is cancelled and everything is back at step 0', async ({ page }) => {
  test.setTimeout(90_000);
  await startPresenting(page);
  // Hold both columns' asks so step 1 is still running when R is confirmed.
  let holdAsks = true;
  await page.route('**/api/ib/ib/ask', async (route) => {
    if (holdAsks && route.request().headers()['x-ib-slot']) await new Promise((r) => setTimeout(r, 4_000));
    return route.fallback();
  });

  await next(page);
  await expect(page).toHaveURL(/a=amy\.am&b=mlro\.tan/);
  await expect(page.getByTestId('compare-b').getByTestId('compare-loading')).toBeVisible();
  await expect.poll(() => barState(page)).toBe('busy');

  // P while busy does not start a second run.
  await page.keyboard.press('p');
  await expect(stepNo(page)).toHaveText('01');
  await expect(page).toHaveURL(/a=amy\.am&b=mlro\.tan/);

  await page.keyboard.press('r');
  await page.getByTestId('presenter-reset-confirm').click();
  await expect(page).toHaveURL(/\/present$/);
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page)).toBe('ready');
  await expect.poll(() => mainSub(page)).toBe('mlro.tan');
  expect((await slotWhoami(page, 'b')).status()).toBe(401);
  expect((await slotWhoami(page, 'c')).status()).toBe(401);

  // The held asks now come back: nothing navigates, nothing is remembered, slots stay signed out.
  holdAsks = false;
  await page.waitForTimeout(5_000);
  await expect(page).toHaveURL(/\/present$/);
  await expect(stepNo(page)).toHaveText('00');
  expect(await barState(page)).toBe('ready');
  expect(await remembered(page)).toEqual({});
  expect((await slotWhoami(page, 'b')).status()).toBe(401);
  expect((await slotWhoami(page, 'c')).status()).toBe(401);

  // And the script runs normally from here.
  await next(page);
  await expect(page.getByTestId('compare-c').getByTestId('answer')).toBeVisible();
  await expect.poll(() => barState(page)).toBe('ready');
  await expect(stepNo(page)).toHaveText('01');
});
