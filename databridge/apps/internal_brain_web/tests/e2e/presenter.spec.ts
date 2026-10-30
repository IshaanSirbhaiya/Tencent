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
async function press(page: Page, key: 'Space' | 'ArrowRight' | 'ArrowLeft' = 'Space') {
  await page.waitForTimeout(450); // the bar debounces a double press (400 ms)
  // A step served from the warm-up cache can go busy -> ready faster than a poll samples it, so an
  // observer armed before the key press records that the step started (flaked once in the 30 Oct run).
  await page.evaluate(() => {
    const w = window as unknown as { __barWentBusy?: boolean; __barObs?: MutationObserver };
    w.__barObs?.disconnect();
    w.__barWentBusy = false;
    const el = document.querySelector('[data-testid="presenter-bar"]');
    if (!el) return;
    w.__barObs = new MutationObserver(() => {
      if (el.getAttribute('data-state') === 'busy') w.__barWentBusy = true;
    });
    w.__barObs.observe(el, { attributes: true, attributeFilter: ['data-state'] });
  });
  await page.keyboard.press(key);
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __barWentBusy?: boolean }).__barWentBusy === true))
    .toBe(true);
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
  // Each is ready only once its result is on screen (stage QA 30 Oct #3).
  expect(await press(page)).toBe('ready');
  await expect(page).toHaveURL(/\/dev\/audit\?verify=1$/);
  await expect(page.getByTestId('verify-status')).toBeVisible();
  expect(await press(page)).toBe('ready');
  await expect(page).toHaveURL(/\/dev\/audit\?tamper=1$/);
  await expect(page.getByTestId('tamper-panel')).toHaveAttribute('data-state', 'done');

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

test('R asks first, then signs every slot out and returns to step 0; H hides the bar, Esc never does once started', async ({ page }) => {
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

  // The Esc rule (round-3 Fable #1): once started, an Esc with nothing open never hides the HUD;
  // H hides it on purpose and Esc (or H) brings it back.
  await page.keyboard.press('Escape');
  await expect(bar(page)).toBeVisible();
  await page.keyboard.press('h');
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
  await expect(page.getByTestId('proof-codebuddy')).toContainText('Nine CodeBuddy sessions, transcripts in the repo.');
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

test('the grow beat stays off the Space path unless the tab opts in with ?grow=1 (round-2 Fable #1)', async ({ page }) => {
  // The mock always reports demo_features: true, and an armed presenter counts as the viewer's
  // opt-in; even so the default script is the run sheet's 11 steps (00 to 10).
  await startPresenting(page);
  await expect(bar(page)).toContainText('/ 10');
  // ?grow=1 puts the three grow steps after the Ken beat for this tab only.
  await page.goto('/present?grow=1');
  await expect(bar(page)).toContainText('/ 13');
  await page.goto('/present?grow=0');
  await expect(bar(page)).toContainText('/ 10');
});

test('P from signed out: the HUD dots read the status after the sign-in, never "unknown" (round-2 Fable #4)', async ({ page }) => {
  // /ib/status needs a session: a fetch made before step 0 signs someone in is a 401.
  await page.goto('/present?present=1');
  await expect(bar(page)).toBeVisible();
  await page.keyboard.press('p');
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  const dots = page.getByTestId('presenter-status');
  await expect(dots).toContainText(/Model\s+(quoting|live)/);
  await expect(dots).toContainText(/Ledger\s+ok/);
  await expect(dots).not.toContainText('unknown');
});

test('signing out from the shell drops the cached deliverables answer (round-2 Opus #9)', async ({ page }) => {
  await login(page, 'jdoe');
  await page.evaluate(() => sessionStorage.setItem('ib.presenter.deliver', JSON.stringify({ at: Date.now(), response: {}, deliverables: {}, kinds: [] })));
  await page.locator('button:has([data-testid="whoami"])').first().click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await page.waitForURL((u) => u.pathname.startsWith('/login'));
  expect(await page.evaluate(() => sessionStorage.getItem('ib.presenter.deliver'))).toBeNull();
});

test('/present/preflight is served on the loopback stage laptop (the presenter flow keeps working)', async ({ page }) => {
  // Loopback AND a session the IB confirms (round-4 Opus #2): signed out it is the one 404.
  expect((await page.request.get('/present/preflight')).status()).toBe(404);
  await login(page, 'jdoe');
  const r = await page.goto('/present/preflight');
  expect(r?.status()).toBe(200);
  // From any other host name, even signed in: one 404 (health, ledger and per-persona counts stay local).
  const away = await page.request.get('/present/preflight', { headers: { host: 'demo.example' } });
  expect(away.status()).toBe(404);
});

test('D then Esc puts the session back: ← to the journey shows the waterfall, not "Request not found" (round-3 Fable #2)', async ({ page }) => {
  test.setTimeout(180_000);
  await startPresenting(page);
  expect(await press(page)).toBe('ready'); // 01
  expect(await press(page)).toBe('ready'); // 02
  expect(await press(page)).toBe('ready'); // 03: the sources sheet
  // B over the sheet: the sheet closes and the recording plays; B again returns (stage QA 30 Oct #2).
  await expect(page.getByTestId('sources-sheet')).toBeVisible();
  await page.keyboard.press('b');
  await expect(page.getByTestId('clip-overlay')).toBeVisible();
  await expect(page.getByTestId('sources-sheet')).toHaveCount(0);
  await page.keyboard.press('b');
  await expect(page.getByTestId('clip-overlay')).toHaveCount(0);
  // Space over step 03's sources sheet closes it and goes on (stage QA re-run): open it again with 2.
  await page.waitForTimeout(450);
  await page.keyboard.press('2');
  await expect(page.getByTestId('sources-sheet')).toBeVisible();
  expect(await press(page)).toBe('ready'); // 04: the journey, as Maya
  await expect(page.getByTestId('sources-sheet')).toHaveCount(0);
  await expect(stepNo(page)).toHaveText('04');
  await expect(page.getByTestId('journey-waterfall')).toBeVisible();
  expect(await press(page)).toBe('ready'); // 05: Verify
  await expect(stepNo(page)).toHaveText('05');

  // D: Leo's answer and its deliverables, from the warm-up cache; the main session is Leo meanwhile.
  await page.waitForTimeout(450);
  await page.keyboard.press('d');
  await expect(page.getByTestId('presenter-deliver-answer')).toBeVisible({ timeout: 60_000 });
  await expect.poll(() => mainSub(page)).toBe('leo.ops');
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('presenter-deliver-answer')).toHaveCount(0);
  await expect(bar(page)).toBeVisible();
  // Esc put back whoever was signed in before D
  await expect.poll(() => mainSub(page)).toBe('maya.compliance');

  expect(await press(page, 'ArrowLeft')).toBe('ready');
  await expect(stepNo(page)).toHaveText('04');
  await expect(page).toHaveURL(/\/dev\/journey\/[^/]+$/);
  await expect(page.getByTestId('journey-waterfall')).toBeVisible();
  await expect(page.locator('main')).not.toContainText('Request not found');

  // ← to the journey signs Maya in again even when something else changed the main session.
  expect(await press(page)).toBe('ready'); // 05
  await page.request.post('/api/ib/auth/demo-login', { data: { persona: 'leo.ops' } });
  expect(await press(page, 'ArrowLeft')).toBe('ready');
  await expect(page.getByTestId('journey-waterfall')).toBeVisible();
  expect(await mainSub(page)).toBe('maya.compliance');
  await expect(page.locator('main')).not.toContainText('Request not found');
});

test('the ?grow=1 walk-up runs on Space alone: P, then Space through grow, grow-post and grow-ask, no Esc (round-3 Fable #1)', async ({ page }) => {
  test.setTimeout(240_000);
  await login(page, 'jdoe');
  // compile the graph page once so the grow step does not wait on the dev server
  await page.goto('/dev/graph');
  await expect(page.getByTestId('graph-main-canvas')).toBeVisible({ timeout: 90_000 });
  await page.goto('/present?present=1&grow=1');
  await expect(bar(page)).toContainText('/ 13');
  await page.keyboard.press('p');
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  expect(await press(page)).toBe('ready'); // 01
  expect(await press(page)).toBe('ready'); // 02

  // 03 grow: the note is in Jordan's Add sheet; focus is on the sheet, not its Close button.
  expect(await press(page)).toBe('ready');
  await expect(stepNo(page)).toHaveText('03');
  const sheet = page.locator('[data-testid$="-addsheet"]');
  await expect(sheet).toBeVisible();
  await expect(sheet).toHaveAttribute('data-presenter-owned', '');
  await expect(sheet.locator('textarea')).toHaveValue(/^Update on DBMIG-12/);
  await expect(sheet.getByRole('button', { name: 'Close' })).not.toBeFocused();
  await expect(sheet).toBeFocused();

  // 04 grow-post: Space posts (it does not close the sheet first); then the sheet closes by itself.
  expect(await press(page)).toBe('ready');
  await expect(stepNo(page)).toHaveText('04');
  await expect(page.locator('[data-testid$="-addsheet"]')).toHaveCount(0, { timeout: 10_000 });
  await expect(page.locator('[data-testid$="-live-item"]')).toHaveCount(1, { timeout: 15_000 });
  const owners = await page.locator('[data-testid$="-live-item"]').evaluateAll((els) => els.map((e) => e.closest('[data-sub]')?.getAttribute('data-sub')));
  expect(owners).toEqual(['jdoe']); // Ken's side: nothing
  await expect(bar(page)).toBeVisible();

  // 05 grow-ask: both of them asked on Compare.
  expect(await press(page)).toBe('ready');
  await expect(stepNo(page)).toHaveText('05');
  await expect(page).toHaveURL(/\/compare\?.*a=jdoe&b=ken\.ext/);
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();
  await expect(page.getByTestId('compare-c').getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  await expect(bar(page)).toBeVisible();
});

test('a ?grow=1 tab opened signed out picks up the grow steps once P signs someone in (round-3 Fable #1)', async ({ page }) => {
  await page.goto('/present?present=1&grow=1');
  await expect(bar(page)).toBeVisible();
  await expect(bar(page)).toContainText('/ 10'); // /ib/status is 401 before the sign-in
  await page.keyboard.press('p');
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  await expect(bar(page)).toContainText('/ 13');
});

test('a shell sign-out during the P warm-up stays signed out: the warm-up never signs anyone back in (round-4 Opus #1)', async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, 'jdoe');
  await page.goto('/compare');
  await expect(page.getByLabel('Question for both')).toBeVisible();
  // Hold the warm-up's ask (it runs as Leo), so the sign-out lands in the middle of it.
  let release!: () => void;
  const held = new Promise<void>((r) => (release = r));
  let asked = false;
  await page.route('**/api/ib/ib/ask', async (route) => {
    // only the warm-up's question (Q_DELIVER); Compare's asks go straight through
    if ((route.request().postData() ?? '').includes('Indonesian SME')) {
      asked = true;
      await held;
    }
    await route.continue();
  });
  await page.locator('body').click({ position: { x: 5, y: 300 } });
  await page.keyboard.press('Control+Shift+P');
  await expect(bar(page)).toBeVisible();
  await page.keyboard.press('p');
  // The title card comes first (stage QA 30 Oct #6); the warm-up waits until step 01's Compare has
  // settled, so it never competes with the two live Compare asks (round-5 Opus cross-check #3).
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page)).toBe('ready');
  await page.waitForTimeout(1_000);
  expect(asked).toBe(false);
  await next(page);
  await expect(page).toHaveURL(/a=amy\.am&b=mlro\.tan/);
  await expect.poll(() => barState(page), { timeout: 30_000 }).not.toBe('busy');
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();
  await expect.poll(() => asked, { timeout: 30_000 }).toBe(true);
  await page.keyboard.press('s'); // Stage off: the shell's account menu shows again
  await page.locator('button:has([data-testid="whoami"])').first().click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await page.waitForTimeout(300);
  release();
  await expect(page).toHaveURL(/\/login/, { timeout: 30_000 });
  // Give the warm-up, its deliverables and step 0 every chance to finish, then check nobody is signed in.
  await page.waitForTimeout(4_000);
  expect((await page.request.get('/api/ib/auth/whoami')).status()).toBe(401);
  await expect(page).toHaveURL(/\/login/);
  expect(await page.evaluate(() => sessionStorage.getItem('ib.presenter.deliver'))).toBeNull();
});

test('D, then a shell Sign out while D is still building: nobody is signed back in (round-5 Codex #9)', async ({ page }) => {
  test.setTimeout(120_000);
  await login(page, 'jdoe');
  await page.goto('/compare');
  await expect(page.getByLabel('Question for both')).toBeVisible();
  await page.evaluate(() => sessionStorage.removeItem('ib.presenter.deliver')); // D must build now
  // Hold D's warm-up at its last step, putting jdoe back after building Leo's deliverables, so the
  // Sign out lands while D is still running (and before D's own switch to Leo).
  let release!: () => void;
  const held = new Promise<void>((r) => (release = r));
  let asked = false;
  let holding = false;
  await page.route('**/api/ib/ib/ask', async (route) => {
    if ((route.request().postData() ?? '').includes('Indonesian SME')) asked = true;
    await route.continue();
  });
  await page.route('**/api/ib/auth/demo-login', async (route) => {
    if (asked && (route.request().postData() ?? '').includes('"jdoe"')) {
      holding = true;
      await held;
    }
    await route.continue();
  });
  await page.locator('body').click({ position: { x: 5, y: 300 } });
  await page.keyboard.press('Control+Shift+P');
  await expect(bar(page)).toBeVisible();
  await page.keyboard.press('d');
  await expect.poll(() => holding, { timeout: 60_000 }).toBe(true);
  await page.locator('button:has([data-testid="whoami"])').first().click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await page.waitForTimeout(300);
  release();
  await expect(page).toHaveURL(/\/login/, { timeout: 30_000 });
  // Give D every chance to finish, then check: still signed out, still on /login, no panel.
  await page.waitForTimeout(4_000);
  expect((await page.request.get('/api/ib/auth/whoami')).status()).toBe(401);
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByTestId('presenter-deliver-answer')).toHaveCount(0);
});

test('browser Back at step 07 stays on step 07: it never re-runs step 06\'s tamper demo (round-5 Opus cross-check #1)', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  // Step 07 (WorkBuddy), as if the script had walked there.
  await page.evaluate(() => sessionStorage.setItem('ib.presenter', JSON.stringify({ armed: true, started: true, step: 7, stage: true, reqs: {} })));
  await page.reload();
  await expect(stepNo(page)).toHaveText('07');
  await expect.poll(() => barState(page)).toBe('ready');
  // Coming back from WorkBuddy: the focus handler opens /dev/audit?latest=1 (a new history entry).
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page).toHaveURL(/\/dev\/audit\?latest=1$/);
  // Back: /dev/audit again. Same path as step 06, but not step 06's page (?tamper=1).
  await page.evaluate(() => history.back());
  await expect(page).toHaveURL(/\/dev\/audit$/);
  await page.waitForTimeout(1_500);
  await expect(stepNo(page)).toHaveText('07');
  expect(page.url()).not.toContain('tamper');
});

/* ---------------------------------------------------------------- mouse (PRESENTER_KEYS.md, "Mouse") */

const ctl = (page: Page, name: string) => page.getByTestId(`presenter-ctl-${name}`);

/** Dispatch a mouse side button (3 = back, 4 = forward); returns true when the page prevented the default. */
const sideButton = (page: Page, button: 3 | 4) =>
  page.evaluate((b) => {
    const target = document.querySelector('main') ?? document.body;
    target.dispatchEvent(new MouseEvent('mousedown', { button: b, bubbles: true, cancelable: true }));
    const ev = new MouseEvent('mouseup', { button: b, bubbles: true, cancelable: true });
    target.dispatchEvent(ev);
    return ev.defaultPrevented;
  }, button);

/** Click a bar control that moves the script, wait until the new step has finished; returns the bar state. */
async function click(page: Page, name: string) {
  await page.waitForTimeout(450); // the same 400 ms debounce as the keys
  const before = await stepNo(page).innerText();
  await ctl(page, name).click();
  // A Back to a slide is over in a few milliseconds: wait for the step number, then for the step.
  await expect(stepNo(page)).not.toHaveText(before);
  await expect.poll(() => barState(page), { timeout: 30_000 }).not.toBe('busy');
  return barState(page);
}

test('the mouse drives the script: Start, Next, Back, side buttons, browser Back, Hide and Show, Exit', async ({ page }) => {
  test.setTimeout(180_000);
  await login(page, 'jdoe');
  await page.goto('/compare?present=1');
  await expect(page).toHaveURL(/\/present$/);
  await expect(bar(page)).toBeVisible();

  // Before P: Start, Reset, Hide, Exit; each named, with its key in the tooltip and at least 32 px.
  for (const n of ['start', 'reset', 'hide', 'exit']) await expect(ctl(page, n)).toBeVisible();
  await expect(ctl(page, 'back')).toHaveCount(0);
  await expect(ctl(page, 'start')).toHaveAttribute('aria-label', 'Start');
  await expect(ctl(page, 'start')).toHaveAttribute('title', 'Start (P)');
  await expect(ctl(page, 'exit')).toHaveAttribute('title', 'Exit presenter (Ctrl+Shift+P)');
  for (const n of ['start', 'reset', 'hide', 'exit']) {
    const box = (await ctl(page, n).boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(32);
    expect(box.height).toBeGreaterThanOrEqual(32);
  }

  // Start = P.
  await ctl(page, 'start').click();
  await expect(stepNo(page)).toHaveText('00');
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  // Stage keeps one row: once the 3 s legend after P is gone, only More is on it; More opens the
  // controls row, which then stays open.
  await expect(page.getByTestId('presenter-keys')).toHaveCount(0, { timeout: 6_000 });
  await expect(ctl(page, 'next')).toHaveCount(0);
  await ctl(page, 'more').click();
  await expect(ctl(page, 'more')).toHaveAttribute('aria-expanded', 'true');
  await expect(ctl(page, 'back')).toHaveAttribute('aria-disabled', 'true'); // nothing before 00
  await expect(ctl(page, 'next')).toHaveAttribute('title', 'Next (Space)');
  await expect(ctl(page, 'clip')).toBeVisible(); // step 00 has a recording
  await expectAccessible(page, 'presenter/stage-mouse-controls');

  // Next, clicked twice quickly: one step (the busy and debounce rules of Space).
  await page.waitForTimeout(450);
  await ctl(page, 'next').click();
  await ctl(page, 'next').click({ force: true });
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  await expect(stepNo(page)).toHaveText('01');
  await expect(page).toHaveURL(/a=amy\.am&b=mlro\.tan/);
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();

  // Space with the Next button focused advances once: its keyup is swallowed, the button never fires too.
  await expect(ctl(page, 'next')).toBeFocused();
  expect(await press(page)).toBe('ready');
  await page.waitForTimeout(600);
  await expect(stepNo(page)).toHaveText('02');

  // Back = the left arrow.
  expect(await click(page, 'back')).toBe('ready');
  await expect(stepNo(page)).toHaveText('01');

  // Mouse side buttons: forward = Next, back = Back; the browser's own navigation is prevented.
  await page.waitForTimeout(450);
  expect(await sideButton(page, 4)).toBe(true);
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  await expect(stepNo(page)).toHaveText('02');
  expect(await sideButton(page, 3)).toBe(true);
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  await expect(stepNo(page)).toHaveText('01');

  // Browser Back to the previous step's page is Back: the bar and the page agree afterwards.
  await page.waitForTimeout(1_100); // outside the side-button window
  expect(await press(page)).toBe('ready');
  await expect(stepNo(page)).toHaveText('02');
  await page.evaluate(() => history.back());
  await expect(stepNo(page)).toHaveText('01');
  await expect.poll(() => barState(page), { timeout: 30_000 }).toBe('ready');
  await expect(page).toHaveURL(/a=amy\.am&b=mlro\.tan/);
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();

  // The More row stayed open through every step.
  await expect(ctl(page, 'more')).toHaveAttribute('aria-expanded', 'true');
  // Hide: the bar goes, a small handle stays (focused); the handle brings it back.
  await ctl(page, 'hide').click();
  await expect(bar(page)).toHaveCount(0);
  await expect(page.getByTestId('presenter-show')).toBeFocused();
  await expectAccessible(page, 'presenter/stage-hidden-handle');
  await page.getByTestId('presenter-show').click();
  await expect(bar(page)).toBeVisible();
  await expect(ctl(page, 'hide')).toBeFocused();

  // Reset opens the same confirm as R.
  await ctl(page, 'reset').click();
  const dialog = page.getByRole('dialog', { name: 'Reset the demo?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).first().click();
  await expect(stepNo(page)).toHaveText('01');

  // Exit = Ctrl+Shift+P: bar and Stage gone, keys and side buttons off, ordinary clicks navigate.
  await ctl(page, 'exit').click();
  await expect(bar(page)).toHaveCount(0);
  await expect(page.getByTestId('presenter-show')).toHaveCount(0);
  await expect(page.locator('html')).not.toHaveAttribute('data-stage', /.*/);
  const url = page.url();
  await page.keyboard.press('Space');
  expect(await sideButton(page, 3)).toBe(false);
  await page.waitForTimeout(500);
  expect(page.url()).toBe(url);
  await page.getByTestId('side-nav').locator('[data-nav="history"]').first().click();
  await expect(page).toHaveURL(/\/history$/);
});

test('armed: ordinary clicks stay ordinary, and only the side buttons are taken over', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/compare');
  await expect(page.getByLabel('Question for both')).toBeVisible();
  await page.locator('body').click({ position: { x: 5, y: 300 } });
  await page.keyboard.press('Control+Shift+P');
  await expect(bar(page)).toBeVisible();
  await expectAccessible(page, 'presenter/rehearsal-light-controls');
  // A left click on an ordinary element is never prevented while armed.
  const prevented = await page.evaluate(() => {
    const ev = new MouseEvent('mouseup', { button: 0, bubbles: true, cancelable: true });
    document.body.dispatchEvent(ev);
    return ev.defaultPrevented;
  });
  expect(prevented).toBe(false);
  // Side buttons before P: the browser does not navigate, and nothing starts.
  expect(await sideButton(page, 4)).toBe(true);
  await expect(page.getByTestId('presenter-step-title')).toHaveText('Press P to start');
  // The app's own navigation works with the mouse while armed.
  await page.getByTestId('side-nav').locator('[data-nav="history"]').first().click();
  await expect(page).toHaveURL(/\/history$/);
  await expect(bar(page)).toBeVisible();
});

test('the mouse controls pass axe in the dark theme and read in Chinese', async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-tour-offer', 'dismissed');
      localStorage.setItem('ib-theme', 'dark');
    } catch {
      /* storage blocked */
    }
  });
  await login(page, 'jdoe');
  await page.goto('/compare');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.locator('body').click({ position: { x: 5, y: 300 } });
  await page.keyboard.press('Control+Shift+P');
  await expect(bar(page)).toBeVisible();
  await expectAccessible(page, 'presenter/rehearsal-dark-controls');
  await page.keyboard.press('l');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
  await expect(ctl(page, 'start')).toHaveAttribute('title', '开始（P）');
  await expect(ctl(page, 'exit')).toHaveAttribute('aria-label', '退出演示模式');
  await page.keyboard.press('l');
});

test('B on a slide keeps the slide and says there is no recording (stage QA 30 Oct #4)', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/present/proof');
  // Step 08 (proof), as if the script had walked there.
  await page.evaluate(() => sessionStorage.setItem('ib.presenter', JSON.stringify({ armed: true, started: true, step: 8, stage: true, reqs: {} })));
  await page.reload();
  await expect(stepNo(page)).toHaveText('08');
  await expect(ctl(page, 'clip')).toHaveCount(0); // no recording button on a slide
  // The note is announced: it appears inside an always-mounted polite live region (round-5 Opus cross-check #2).
  const region = page.getByTestId('clip-note-region');
  await expect(region).toHaveAttribute('role', 'status');
  await expect(region).toHaveAttribute('aria-live', 'polite');
  await page.keyboard.press('b');
  await expect(region.getByTestId('clip-none-note')).toHaveText('No recording for this step');
  await expect(page.getByTestId('clip-overlay')).toHaveCount(0);
  await expect(page.getByTestId('proof-leaks')).toBeVisible();
  await expect(page.getByTestId('clip-none-note')).toHaveCount(0, { timeout: 5_000 });
  // Exit on a slide goes to the app, never a page with no links (stage QA re-run).
  await page.keyboard.press('Control+Shift+P');
  await expect(bar(page)).toHaveCount(0);
  await expect(page).not.toHaveURL(/\/present/);
  await expect(page.getByTestId('side-nav')).toBeVisible();
});
