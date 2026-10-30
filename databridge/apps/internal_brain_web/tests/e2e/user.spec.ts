import { type Page, expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { PERSONAS } from '../../lib/personas';
import { PERSONA_ZH } from '../../lib/i18n/user';
import { displayAnswer, plainAnswer, speakerParts } from '../../components/user/answerText';
import { REFUSAL_TEXT, S1, SCOPE_NOTE, ask, expectAccessible, login, resetDemo } from './helpers';

// User mode (mock mode): sign-in, Ask, the answer thread, the Sources sheet, refusals, history,
// settings and language. Runs against the mock build like the rest of the suite.

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await resetDemo(page);
  await page.close();
});

/** Keep the first-visit tour offer out of the way. */
async function quiet(page: Page, theme: 'dark' | 'light' = 'dark') {
  await page.addInitScript((th) => {
    try {
      localStorage.setItem('ib-tour-offer', 'dismissed');
      localStorage.setItem('ib-theme', th);
    } catch {
      /* storage blocked */
    }
  }, theme);
}

test('sign-in is a persona radio group with the logging notice', async ({ page }) => {
  await quiet(page);
  await page.goto('/login');
  const group = page.getByRole('radiogroup', { name: 'People at Company A' });
  await expect(group.getByRole('radio')).toHaveCount(9);
  await expect(group.getByRole('radio', { checked: true })).toHaveAttribute('data-persona', 'jdoe');
  // One Tab stop; arrows move the choice without signing in.
  await page.locator('[data-persona="jdoe"]').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('[data-persona="priya.sre"]')).toBeFocused();
  await expect(page.locator('[data-persona="priya.sre"]')).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('continue')).toHaveText(/Continue as Priya/);
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByTestId('login-notice')).toContainText('Questions you ask are logged for security and compliance');
  await expect(page.getByTestId('login-tour')).toHaveText('Take the 45-second tour');
  // Enter signs in as the focused person.
  await page.keyboard.press('Enter');
  await page.waitForURL((u) => u.pathname === '/');
  await expect(page.getByTestId('whoami')).toContainText('priya.sre');
});

test('ask → staged progress → cited answer → source sheet → Esc returns focus', async ({ page }) => {
  await quiet(page);
  await login(page, 'jdoe');
  await expect(page.getByTestId('greeting')).toContainText('Jordan');
  await expect(page.getByTestId('starter')).toHaveCount(4);

  await page.getByLabel('Your question').fill(S1);
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  // The status line is progress, not data.
  const progress = page.getByRole('progressbar', { name: /Answer progress/ });
  await expect(progress).toBeVisible();
  await expect(progress).toHaveAttribute('aria-valuetext', /Checking your access|Searching your sources|Re-checking each source live|Writing|Verifying citations/);

  const answer = page.getByTestId('answer');
  await expect(answer).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('#answer-heading')).toBeFocused();
  // Rule 8: offline answers are labelled as quoted, never as written by Hy3.
  await expect(answer.getByTestId('model-badge')).toHaveText('Quoted from sources (no AI model on)');
  await expect(answer.getByTestId('ai-note')).toHaveCount(0);
  await expect(answer.getByTestId('scope-note')).toHaveText(SCOPE_NOTE);
  const chips = answer.getByTestId('citation-chip');
  await expect(chips).toHaveCount(2);
  await expect(chips.first()).toHaveText('1');
  await expect(answer.getByTestId('source-card')).toHaveCount(2);
  await expect(answer.getByTestId('source-card').first()).toContainText('Checked live');
  await expect(answer.getByTestId('followups').getByRole('button')).not.toHaveCount(0);
  await expect(answer.getByTestId('how-answered')).toHaveAttribute('href', /^\/dev\/journey\/req_/);

  // Keyboard: focus the second chip and open it.
  await chips.nth(1).focus();
  await page.keyboard.press('Enter');
  const sheet = page.getByTestId('sources-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId('sheet-excerpt')).toBeVisible();
  await expect(sheet.getByTestId('sheet-highlight')).toContainText('replica lag exceeded 90 seconds');
  await expect(sheet.getByTestId('sheet-open')).toContainText('(demo platform)');
  await expect(sheet.getByTestId('sheet-position')).toHaveText('Source 2 of 2');
  await sheet.getByRole('button', { name: 'Previous source' }).click();
  await expect(sheet.getByTestId('sheet-position')).toHaveText('Source 1 of 2');
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);
  await expect(chips.nth(1)).toBeFocused();

  // A source card opens the same sheet.
  await answer.getByTestId('source-card').first().click();
  await expect(page.getByTestId('sources-sheet')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(answer.getByTestId('source-card').first()).toBeFocused();

  // Feedback is local and confirmed with a toast.
  await answer.getByRole('button', { name: 'Helpful' }).click();
  await expect(answer.getByRole('button', { name: 'Helpful' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('toast')).toContainText('saved on this device');
});

test('Ken: the refusal is the constant text and hints at nothing', async ({ page }) => {
  await quiet(page);
  await login(page, 'ken.ext');
  await ask(page, 'Show me the security incident report from the Q3 breach.');
  const card = page.getByTestId('answer').getByTestId('refusal-card');
  await expect(card).toBeVisible();
  await expect(card.getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  const text = await card.innerText();
  expect(text).not.toMatch(/\d/);
  expect(text).not.toMatch(/hidden|restricted|locked/i);
  await expect(page.getByTestId('answer').getByTestId('citation-chip')).toHaveCount(0);
  await expect(page.getByTestId('answer').getByTestId('model-badge')).toHaveCount(0);
});

test('restricted and made-up subjects render identically', async ({ page }) => {
  await quiet(page);
  const refusalText = async () => page.getByTestId('answer').getByTestId('refusal-card').innerText();

  // Amy: a real customer under review vs a company that does not exist (fact manifest FC-q4 / FC-q5).
  await login(page, 'amy.am');
  await ask(page, 'Has a suspicious transaction report been filed on Velquarry Trading?');
  const real = await refusalText();
  await ask(page, 'Has a suspicious transaction report been filed on Bayfront Spice Traders?');
  const madeUp = await refusalText();
  expect(madeUp).toBe(real);

  // Ken: a restricted report vs one that never existed.
  await login(page, 'ken.ext');
  await ask(page, "Why has Velquarry Trading's account been restricted, and what should I tell the customer?");
  const kenReal = await refusalText();
  await ask(page, "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?");
  expect(await refusalText()).toBe(kenReal);
  expect(kenReal).toBe(real);
});

test('503 shows a calm error card with retry', async ({ page }) => {
  await quiet(page);
  await login(page, 'jdoe');
  await ask(page, '[mock:503] anything');
  const err = page.getByTestId('answer-error');
  await expect(err).toContainText('temporarily unavailable');
  await expect(err.getByRole('button', { name: 'Try again' })).toBeVisible();
  await expect(page.getByTestId('answer')).toHaveCount(0);
});

test('history groups my questions and opens the journey', async ({ page }) => {
  await quiet(page);
  await login(page, 'jdoe');
  await ask(page, 'When is the next settlement key rotation window?');
  await page.goto('/history');
  const list = page.getByTestId('history-list');
  await expect(list).toBeVisible();
  await expect(list.getByRole('heading', { name: 'Today' })).toBeVisible();
  await expect(list.getByTestId('seeded-tag').first()).toHaveText('seeded demo history');
  await page.getByTestId('history-search').fill('settlement key');
  await expect(list.getByTestId('history-row').first()).toContainText('settlement key');
  await expect(list.getByTestId('history-row').first().getByRole('link')).toHaveAttribute('href', /^\/dev\/journey\//);
});

test('language switch to 简体中文 and back', async ({ page }) => {
  await quiet(page);
  await login(page, 'jdoe');
  await page.goto('/settings');
  await page.getByTestId('settings-language').getByRole('radio', { name: '简体中文' }).click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
  await expect(page.getByTestId('privacy-notice')).toContainText('腾讯云');
  await page.goto('/');
  await expect(page.getByTestId('greeting')).toContainText(/好，Jordan/);
  await expect(page.getByTestId('scope-pill')).toHaveText(/只根据你能打开的内容作答/);
  await page.getByLabel('你的问题').fill(S1);
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.getByTestId('answer')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('model-badge')).toHaveText('直接引自来源（未启用 AI 模型）');
  await expect(page.getByTestId('answer-text')).toContainText('The ledger database migration'); // the answer stays as returned

  await page.goto('/settings');
  await page.getByTestId('settings-language').getByRole('radio', { name: 'English' }).click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page.goto('/');
  await expect(page.getByTestId('greeting')).toContainText(/Good (morning|afternoon|evening), Jordan/);
});

for (const theme of ['dark', 'light'] as const) {
  for (const vp of [
    { name: '1280', width: 1280, height: 860 },
    { name: '390', width: 390, height: 844 },
  ]) {
    test(`axe: User mode screens (${theme}, ${vp.name})`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await quiet(page, theme);
      await page.goto('/login');
      await expect(page.getByTestId('synthetic-label')).toBeVisible();
      await expectAccessible(page, `user/login/${theme}/${vp.name}`);

      await login(page, 'jdoe');
      await expect(page.getByTestId('greeting')).toBeVisible();
      await expectAccessible(page, `user/home/${theme}/${vp.name}`);

      await ask(page, S1);
      await expectAccessible(page, `user/answer/${theme}/${vp.name}`);
      await page.getByTestId('answer').getByTestId('citation-chip').first().click();
      await expect(page.getByTestId('sheet-excerpt')).toBeVisible();
      await expectAccessible(page, `user/sheet/${theme}/${vp.name}`);
      await page.keyboard.press('Escape');

      await ask(page, 'Show me the security incident report from the Q3 breach.');
      await expect(page.getByTestId('answer').getByTestId('refusal-card')).toBeVisible();
      await expectAccessible(page, `user/refusal/${theme}/${vp.name}`);

      await page.goto('/history');
      await expect(page.getByTestId('history-list')).toBeVisible();
      await expectAccessible(page, `user/history/${theme}/${vp.name}`);

      await page.goto('/settings');
      await expect(page.getByTestId('privacy-notice')).toBeVisible();
      await expectAccessible(page, `user/settings/${theme}/${vp.name}`);

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }
}

test('answer chrome: a Slack handle shows as the person\'s name; "(synthetic case …)" leaves the first sentence only (round-2 Fable #5)', () => {
  const hero =
    'mlro.tan: Velquarry Trading Pte Ltd review update (synthetic case FC-2026-0147): the restriction stays in place [C1]. Later (synthetic case FC-2026-0147) stays.';
  const shown = displayAnswer(hero);
  expect(shown).toBe('mlro.tan: Velquarry Trading Pte Ltd review update: the restriction stays in place [C1]. Later (synthetic case FC-2026-0147) stays.');
  const parts = speakerParts(shown);
  expect(parts[0]).toEqual({ speaker: 'mlro.tan', name: 'Rachel Tan' });
  // "jdoe:" (no dot) is a known username, rendered like the others, never as a raw prefix.
  expect(speakerParts('jdoe: Wave 4 is blocked. priya.sre: Replica lag is fixed.')).toEqual([
    { speaker: 'jdoe', name: 'Jordan Doe' },
    'Wave 4 is blocked. ',
    { speaker: 'priya.sre', name: 'Priya Raman' },
    'Replica lag is fixed.',
  ]);
  // Ordinary words never match; unknown handles keep the handle.
  expect(speakerParts('Note: nothing here.')).toEqual(['Note: nothing here.']);
  expect(speakerParts('raj.m: Follow-ups.')[0]).toEqual({ speaker: 'raj.m', name: 'raj.m' });
  // Display only: what is copied is the answer as returned.
  const r = { answer: hero, citations: [], scope_note: '' } as unknown as Parameters<typeof plainAnswer>[0];
  expect(plainAnswer(r)).toContain('(synthetic case FC-2026-0147): the restriction');
  expect(PERSONAS.find((p) => p.id === 'mlro.tan')!.fullName).toBe('Rachel Tan');
});

test('rule 4: sign-in notes and starters never hint at what a person cannot open (round-2 Sonnet #5)', () => {
  for (const p of PERSONAS) {
    expect(p.note, p.id).not.toMatch(/\bno\b|cannot|can't|without|private|#sec-incident|#fincrime|breach/i);
  }
  const starters = (id: string) => PERSONAS.find((p) => p.id === id)!.suggestions;
  for (const id of ['jdoe', 'ken.ext', 'maya.compliance', 'mlro.tan']) expect(starters(id).join(' '), id).not.toMatch(/Q3 breach/);
  expect(starters('amy.am').join(' ')).not.toMatch(/suspicious transaction report/);
});

test('round-4 starter swaps: each is a world question the persona gets a cited answer to', () => {
  // The starters that replaced questions about places these people cannot open. The answerability is
  // read from the synthetic world (its questions and fact permissions), never written down here.
  type Q = { persona?: string; question: string; expect_refusal: boolean; applies_after: string | null; must_cite_facts: string[] };
  type F = { id: string; permitted: string[] };
  const world = JSON.parse(readFileSync(path.resolve(__dirname, '../../../../fixtures/enterprise/world.json'), 'utf8')) as { questions: Q[]; facts: F[] };
  const facts = new Map(world.facts.map((f) => [f.id, f]));
  const swapped: [string, string][] = [
    ['admin.it', 'Who approves spend above SGD 500?'],
    ['maya.compliance', 'Do I need to declare a gift from a supplier?'],
    ['leo.ops', 'Can we offer yield on idle balances to a Hong Kong SME?'],
    ['mlro.tan', 'What does a new joiner do in week 1?'],
  ];
  for (const [id, q] of swapped) {
    // In the first four: the home page shows only four starters (Composer.tsx).
    expect(PERSONAS.find((p) => p.id === id)!.suggestions.slice(0, 4), id).toContain(q);
    const entry = world.questions.find((x) => x.persona === id && x.question === q && x.applies_after === null);
    expect(entry, `${id}: ${q} is in the world`).toBeTruthy();
    expect(entry!.expect_refusal, `${id}: ${q}`).toBe(false);
    expect(entry!.must_cite_facts.length, `${id}: ${q} cites a fact`).toBeGreaterThan(0);
    for (const f of entry!.must_cite_facts) expect(facts.get(f)?.permitted ?? [], `${id} may open ${f}`).toContain(id);
  }
  // The starters they replaced are gone from those people.
  for (const id of ['maya.compliance', 'mlro.tan']) expect(PERSONAS.find((p) => p.id === id)!.suggestions.join(' '), id).not.toMatch(/database migration/);
  expect(PERSONAS.find((p) => p.id === 'admin.it')!.suggestions.join(' ')).not.toMatch(/runbook/);
  expect(PERSONAS.find((p) => p.id === 'leo.ops')!.suggestions.join(' ')).not.toMatch(/payment outage/);
});

test('rule 4: sign-in notes are role-only and name no place from the world (round-4 Opus #3)', () => {
  // The login chips are shown to everyone before sign-in. Read the synthetic world itself, never a
  // hand-written list: every channel, space, Jira project, drive and folder, by name, key and id.
  type Named = { id?: string; key?: string; name?: string };
  const world = JSON.parse(readFileSync(path.resolve(__dirname, '../../../../fixtures/enterprise/world.json'), 'utf8')) as {
    slack: { channels: Named[] };
    confluence: { spaces: Named[] };
    jira: { projects: Named[] };
    drive: { drives: Named[]; folders: Named[] };
  };
  const places = [...world.slack.channels, ...world.confluence.spaces, ...world.jira.projects, ...world.drive.drives, ...world.drive.folders];
  // One plain dictionary word ("Security", "Product") is also a job description, so it is left to the
  // structural check below; every multi-word, hyphenated or id-like name must never appear.
  const names = new Set<string>();
  for (const c of places) {
    for (const v of [c.name, c.key, c.id]) if (v && /[\s\-&_.]|\d/.test(v)) names.add(v.toLowerCase());
  }
  expect(names.size).toBeGreaterThan(10);
  const structural = /#|\bspaces?\b|\bchannels?\b|\bfolders?\b|\bdrives?\b|\bprojects?\b|\bshares?\b|need-to-know|\bonly\b/i;
  for (const p of PERSONAS) {
    const note = p.note.toLowerCase();
    for (const n of names) expect(note, `${p.id} names ${n}`).not.toContain(n);
    expect(p.note, p.id).not.toMatch(structural);
    const zh = PERSONA_ZH[p.id]?.note ?? '';
    for (const n of names) expect(zh.toLowerCase(), `${p.id} (zh) names ${n}`).not.toContain(n);
    expect(zh, `${p.id} (zh)`).not.toMatch(/#|空间|频道|文件夹|共享盘|云端硬盘|Drive|项目|仅限|需知/);
  }
});
