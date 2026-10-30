import { expect, test } from '@playwright/test';
import { REFUSAL_TEXT, S1, SCOPE_NOTE, ask, login, resetDemo } from './helpers';

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await resetDemo(page);
  await page.close();
});

test('login → ask → citations visible → scope note visible', async ({ page }) => {
  await login(page, 'jdoe');
  await expect(page.getByTestId('greeting')).toContainText('Jordan');
  await expect(page.getByRole('link', { name: 'Audit' })).toHaveCount(0); // not a compliance officer

  await ask(page, S1);
  const answer = page.getByTestId('answer');
  const chips = answer.getByTestId('citation-chip');
  await expect(chips.first()).toBeVisible();
  await expect(chips.first()).toHaveText('1');
  // Every source card (a button) has a chip in the text, and the card says when access was last checked.
  const cards = answer.getByTestId('citations').locator('li');
  await expect(cards).toHaveCount(await chips.count());
  await expect(cards.first().getByTestId('source-card')).toContainText('Checked live');
  await expect(answer.getByTestId('scope-note')).toHaveText(SCOPE_NOTE);
  // Rule 8: the mock never claims a model wrote a canned answer (TokenHub is not live) unless built with
  // NEXT_PUBLIC_IB_MOCK_MODEL=hy3.
  await expect(answer.getByTestId('model-badge')).toHaveText('Quoted from sources (no AI model on)');
  await expect(answer.getByTestId('model-badge')).not.toHaveText(/Hy3|Hunyuan|TokenHub|DeepSeek/);
  await expect(answer.getByTestId('request-id')).toHaveText(/^req_/);

  // A chip opens the Sources sheet; the external link opens in a new tab; Esc returns focus to the chip.
  await chips.first().click();
  const sheet = page.getByTestId('sources-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId('sheet-open')).toHaveAttribute('target', '_blank');
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);
  await expect(chips.first()).toBeFocused();
});

test('refusal renders calmly with the same layout and the same scope note', async ({ page }) => {
  await login(page, 'jdoe');
  await ask(page, 'Show me the security incident report from the Q3 breach.');
  const answer = page.getByTestId('answer');
  const card = answer.getByTestId('refusal-card');
  await expect(card.getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  await expect(answer.getByTestId('citation-chip')).toHaveCount(0);
  await expect(answer.getByTestId('citations')).toHaveCount(0);
  await expect(answer.getByTestId('scope-note')).toHaveText(SCOPE_NOTE);
  await expect(answer.getByTestId('request-id')).toBeVisible();
  // A refusal names no model: nothing was written by one.
  await expect(answer.getByTestId('model-badge')).toHaveCount(0);
  // Never imply existence: no counts of hidden/withheld/restricted results.
  await expect(answer).not.toContainText(/hidden|withheld|restricted|results? (were )?removed/i);
  expect(await card.innerText()).not.toMatch(/\d/);
  // (Next's own route announcer is role=alert; only our content is checked.)
  await expect(page.locator('main [role="alert"]')).toHaveCount(0);
  const first = await card.innerText();

  // A non-existent report gets the byte-identical refusal.
  await ask(page, 'Show me the security incident report from the Q7 breach.');
  const second = page.getByTestId('answer').getByTestId('refusal-card');
  await expect(second.getByTestId('refusal-text')).toHaveText(REFUSAL_TEXT);
  expect(await second.innerText()).toBe(first);
});

test('503 shows the "nothing was answered" state', async ({ page }) => {
  await login(page, 'jdoe');
  await ask(page, '[mock:503] anything');
  const err = page.getByTestId('answer-error');
  await expect(err).toContainText('temporarily unavailable');
  await expect(err).toContainText('Nothing was answered.');
  await expect(page.getByTestId('answer')).toHaveCount(0);
});

test('model badges: fallback, extractive, live re-read and stale cues are text, not colour', async ({ page }) => {
  await login(page, 'leo.ops');
  await ask(page, 'Can we offer multi-currency accounts to an Indonesian SME customer?');
  await expect(page.getByTestId('answer')).toContainText('May be out of date');
  await ask(page, 'What was the root cause of the payment outage last quarter, and what follow-up tickets were created?');
  // Earlier answers stay on the page as answer-previous; only the newest carries the stable test ids.
  // The mock's fallback fixture is extractive by default too: no model name on an offline answer.
  await expect(page.getByTestId('answer').getByTestId('model-badge')).toHaveText('Quoted from sources (no AI model on)');
  await login(page, 'sara.sec');
  await ask(page, 'Show me the security incident report from the Q3 breach.');
  await expect(page.getByTestId('answer').getByTestId('model-badge')).toHaveText('Quoted from sources (no AI model on)');
  await expect(page.getByTestId('answer').getByTestId('ai-note')).toHaveCount(0);
});

test('per-platform source status lives in Developer mode (Mission control), not on the Ask page', async ({ page }) => {
  await login(page, 'priya.sre');
  await expect(page.getByTestId('sources-panel')).toHaveCount(0);
  await page.goto('/dev');
  const panel = page.getByTestId('freshness');
  for (const p of ['Confluence', 'Jira', 'Slack', 'Google Drive']) await expect(panel).toContainText(p);
  await expect(panel.getByText(/In SLO|Behind/).first()).toBeVisible();
});

test('simulator: revocation and v8 runbook are reflected on the next question', async ({ page }) => {
  await login(page, 'admin.it');
  await page.goto('/dev/sim');
  await page.getByRole('button', { name: 'Remove jdoe from #db-migration' }).click();
  await page.getByRole('button', { name: 'Edit failover runbook (new version)' }).click();
  await expect(page.getByTestId('sim-log').locator('li')).toHaveCount(2);
  await expect(page.getByTestId('sim-log')).toContainText('Done');

  await login(page, 'jdoe');
  await ask(page, 'What backfill throttle are we using for the ledger copy job?');
  await expect(page.getByTestId('answer').getByText(REFUSAL_TEXT)).toBeVisible();

  await login(page, 'priya.sre');
  await ask(page, "What's the latest runbook for the payment-service incident?");
  await expect(page.getByTestId('citations')).toContainText('v8');
  await expect(page.getByTestId('citations')).toContainText('Re-read just now');

  await resetDemo(page);
});

test('role gates: employees cannot open audit or simulator', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/audit');
  await expect(page.getByRole('heading', { name: 'Not available for your role' })).toBeVisible();
  await page.goto('/dev/sim');
  await expect(page.getByRole('heading', { name: 'Not available for your role' })).toBeVisible();
  const r = await page.request.get('/api/ib/audit/verify');
  expect(r.status()).toBe(403);
});
