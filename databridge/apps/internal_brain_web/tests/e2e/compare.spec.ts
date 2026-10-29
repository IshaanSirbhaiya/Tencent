import { expect, test, type Page } from '@playwright/test';
import { REFUSAL_TEXT, expectAccessible, login } from './helpers';

// Compare: each column is a separate SIGNED session (slots b and c); the main session (slot a) is
// never touched. Two modes: same question for two people, and the same person with two questions.

const QV = "Why has Velquarry Trading's account been restricted, and what should I tell the customer?";
const QB = "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?";
const people = (a: string, b: string, q: string) => `/compare?${new URLSearchParams({ a, b, q })}`;
const person = (p: string, q1: string, q2: string) => `/compare?${new URLSearchParams({ mode: 'person', p, q1, q2 })}`;

async function askBoth(page: Page) {
  await page.getByRole('button', { name: 'Ask both' }).click();
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();
  await expect(page.getByTestId('compare-c').getByTestId('answer')).toBeVisible();
}

test('same question, two people: deep link, header strips, cited answers, main session untouched', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto(people('amy.am', 'mlro.tan', QV));
  await expect(page.getByRole('heading', { level: 1, name: 'Same question, two people' })).toBeVisible();
  await expect(page.getByLabel('First person')).toHaveValue('amy.am');
  await expect(page.getByLabel('Second person')).toHaveValue('mlro.tan');
  await expect(page.getByLabel('Question for both')).toHaveValue(QV);
  // Full names from the start: the headers do not change when the answers land.
  await expect(page.getByTestId('compare-b').getByTestId('compare-name')).toHaveText('Amy Lim');
  await expect(page.getByTestId('compare-c').getByTestId('compare-name')).toHaveText('Rachel Tan');
  await askBoth(page);

  const left = page.getByTestId('compare-b');
  const right = page.getByTestId('compare-c');
  await expect(left.getByTestId('compare-name')).toHaveText('Amy Lim');
  await expect(right.getByTestId('compare-name')).toHaveText('Rachel Tan');
  await expect(left.getByTestId('compare-role')).toHaveText('Account manager');
  await expect(right.getByTestId('compare-role')).toHaveText('MLRO (FinCrime)');
  await expect(left.getByTestId('compare-name')).toContainText('Amy');
  await expect(right.getByTestId('compare-name')).toContainText('Rachel');
  await expect(left.getByTestId('citation-chip').first()).toBeVisible();
  expect(await right.getByTestId('citation-chip').count()).toBeGreaterThanOrEqual(2);
  const a = await left.getByTestId('answer-text').innerText();
  const b = await right.getByTestId('answer-text').innerText();
  expect(a).not.toEqual(b);
  // Amy's answer never mentions the case (tipping-off): only the approved script.
  expect(a.toLowerCase()).not.toContain('velquarry');
  await expect(page.getByTestId('identical-marker')).toHaveCount(0);

  // The slot sessions did not overwrite the main one, and slots never reach the audit console.
  const main = await (await page.request.get('/api/ib/auth/whoami')).json();
  expect(main.sub).toBe('maya.compliance');
  const sb = await (await page.request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': 'b' } })).json();
  const sc = await (await page.request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': 'c' } })).json();
  expect(sb.sub).toBe('amy.am');
  expect(sc.sub).toBe('mlro.tan');
  expect((await page.request.get('/api/ib/audit/verify', { headers: { 'x-ib-slot': 'b' } })).status()).toBe(404);

  await expectAccessible(page, 'compare/people');
});

test('same person, two questions: a restricted and a made-up company give identical refusals, and the marker says so', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto(person('ken.ext', QV, QB));
  await expect(page.getByRole('heading', { level: 1, name: 'Same person, two questions' })).toBeVisible();
  await expect(page.getByRole('radio', { name: 'Same person · two questions' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByLabel('Left question')).toHaveValue(QV);
  await expect(page.getByLabel('Right question')).toHaveValue(QB);
  await askBoth(page);

  const left = page.getByTestId('compare-b');
  const right = page.getByTestId('compare-c');
  await expect(left.getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  await expect(right.getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  await expect(left.getByTestId('compare-question')).toContainText(QV);
  await expect(right.getByTestId('compare-question')).toContainText(QB);
  // Each column is Ken, in its own signed session.
  await expect(left.getByTestId('compare-name')).toContainText('Ken');
  await expect(right.getByTestId('compare-name')).toContainText('Ken');

  // The two visible responses are byte-identical (text + scope note), so the marker shows.
  const html = async (side: typeof left) => side.getByTestId('refusal-card').innerHTML();
  expect(await html(left)).toEqual(await html(right));
  expect(await left.getByTestId('scope-note').innerText()).toEqual(await right.getByTestId('scope-note').innerText());
  await expect(page.getByTestId('identical-marker')).toBeVisible();
  await expect(page.getByTestId('identical-marker')).toContainText('Identical responses');
  await expect(page.getByTestId('compare-announce')).toContainText('identical');
  // Never a hint that anything exists: no counts, no "hidden", no reasons.
  await expect(page.getByTestId('compare-results')).not.toContainText(/hidden|withheld|restricted document|not allowed|denied/i);

  await expectAccessible(page, 'compare/person');
});

test('the marker is absent when the responses differ', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto(people('amy.am', 'ken.ext', QV));
  await askBoth(page);
  await expect(page.getByTestId('compare-c').getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  await expect(page.getByTestId('compare-b').getByTestId('citation-chip').first()).toBeVisible();
  await expect(page.getByTestId('identical-marker')).toHaveCount(0);
});

test('the mode toggle switches the form, and asking writes the deep link', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/compare');
  await expect(page.getByLabel('First person')).toHaveValue('jdoe');
  await expect(page.getByLabel('Second person')).toHaveValue('ken.ext');
  const mode = page.getByRole('radiogroup', { name: 'What to compare' });
  await mode.getByRole('radio', { name: 'Same person · two questions' }).click();
  await expect(page.getByLabel('Person', { exact: true })).toBeVisible();
  await page.getByLabel('Person', { exact: true }).selectOption('ken.ext');
  await page.getByLabel('Left question').fill(QV);
  await page.getByLabel('Right question').fill(QB);
  await askBoth(page);
  await expect(page).toHaveURL(/mode=person/);
  await expect(page).toHaveURL(/p=ken\.ext/);
  await expect(page.getByTestId('identical-marker')).toBeVisible();
  // Arrow keys move the radio group (one Tab stop), as everywhere else.
  await mode.getByRole('radio', { name: 'Same person · two questions' }).focus();
  await page.keyboard.press('ArrowLeft');
  await expect(mode.getByRole('radio', { name: 'Same question · two people' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByLabel('Question for both')).toBeVisible();
});

test("a source chip opens the sheet; Esc closes it and focus returns to the chip", async ({ page }) => {
  await login(page, 'mlro.tan');
  await page.goto(people('amy.am', 'mlro.tan', QV));
  await askBoth(page);
  const chip = page.getByTestId('compare-c').getByTestId('citation-chip').nth(1);
  await chip.focus();
  await page.keyboard.press('Enter');
  const sheet = page.getByTestId('sources-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText('Source 2');
  // Rachel's own source, opened in Rachel's main session: the live passage is shown.
  await expect(sheet.getByTestId('sheet-excerpt')).toBeVisible();
  await expectAccessible(page, 'compare/sheet');
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);
  await expect(chip).toBeFocused();
});

test('a slot-b demo-login sets ib_session_b only', async ({ page }) => {
  await login(page, 'jdoe');
  const r = await page.request.post('/api/ib/auth/demo-login', {
    headers: { 'x-ib-slot': 'b', 'content-type': 'application/json' },
    data: { persona: 'ken.ext' },
  });
  expect(r.ok()).toBeTruthy();
  const set = r
    .headersArray()
    .filter((h) => h.name.toLowerCase() === 'set-cookie')
    .map((h) => h.value);
  expect(set.length).toBeGreaterThan(0);
  for (const c of set) expect(c.startsWith('ib_session_b=')).toBeTruthy();
  const main = await (await page.request.get('/api/ib/auth/whoami')).json();
  expect(main.sub).toBe('jdoe');
});

test('compare @ 390px: accessible, no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'jdoe');
  await page.goto(person('ken.ext', QV, QB));
  await askBoth(page);
  await expect(page.getByTestId('identical-marker')).toBeVisible();
  await expectAccessible(page, 'compare/mobile');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('a column whose slot turns out to be someone else shows the error, not their data', async ({ page }) => {
  // Stand in for a second Compare tab re-signing slot c between the ask and the check.
  await page.route('**/api/ib/auth/whoami', async (route) => {
    if (route.request().headers()['x-ib-slot'] !== 'c') return route.fallback();
    await route.fulfill({ json: { sub: 'maya.compliance', name: 'Maya', roles: ['employee'] } });
  });
  await login(page, 'jdoe');
  await page.goto('/compare');
  await page.getByRole('button', { name: 'Ask both' }).click();
  const left = page.getByTestId('compare-b');
  const right = page.getByTestId('compare-c');
  await expect(right.getByRole('alert')).toContainText('This answer did not come through.');
  await expect(right.getByTestId('answer')).toHaveCount(0);
  await expect(left.getByTestId('answer')).toBeVisible();
});

/** What the presenter remembers per persona (sessionStorage, this tab only). */
const remembered = (page: Page) =>
  page.evaluate(() => {
    try {
      return (JSON.parse(sessionStorage.getItem('ib.presenter') ?? '{}').reqs ?? {}) as Record<string, string>;
    } catch {
      return {} as Record<string, string>;
    }
  });

test('a failed re-run forgets the earlier answers: no stale request id, nothing restored', async ({ page }) => {
  // Presenter mode armed (remembered request ids are kept only then); keys are not used here.
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib.presenter', '1');
    } catch {
      /* storage blocked */
    }
  });
  await login(page, 'mlro.tan');
  const url = people('amy.am', 'mlro.tan', QV);
  await page.goto(url);
  await askBoth(page);
  const oldReq = await page.getByTestId('compare-c').getByTestId('request-id').innerText();
  expect((await remembered(page))['mlro.tan']).toBe(oldReq);

  // Same comparison again, and Rachel's slot cannot sign in this time.
  await page.route('**/api/ib/auth/demo-login', (route) => {
    if (route.request().headers()['x-ib-slot'] === 'c') return route.fulfill({ status: 403, json: { detail: 'forbidden' } });
    return route.fallback();
  });
  await page.getByRole('button', { name: 'Ask both' }).click();
  await expect(page.getByTestId('compare-c').getByRole('alert')).toContainText("This person couldn't be signed in.");
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();
  await expect(page.getByTestId('compare-announce')).toHaveText('One or both answers did not come through.');
  const reqs = await remembered(page);
  expect(reqs['mlro.tan']).toBeUndefined();
  expect(reqs['amy.am']).toBeUndefined();
});
