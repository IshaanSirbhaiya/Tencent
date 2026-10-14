import { expect, test } from '@playwright/test';
import { REFUSAL_TEXT, S1, SCOPE_NOTE, ask, login, resetDemo } from './helpers';

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await resetDemo(page);
  await page.close();
});

test('login → ask → citations visible → scope note visible', async ({ page }) => {
  await login(page, 'jdoe');
  await expect(page.getByRole('heading', { name: 'Ask the Internal Brain' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Audit' })).toHaveCount(0); // not a compliance officer

  await ask(page, S1);
  const answer = page.getByTestId('answer');
  const chips = answer.getByTestId('citation-chip');
  await expect(chips.first()).toBeVisible();
  const items = answer.getByTestId('citations').locator('li');
  await expect(items).toHaveCount(await chips.count());
  await expect(items.first()).toContainText('Checked live');
  await expect(items.first().getByRole('link')).toHaveAttribute('target', '_blank');
  await expect(answer.getByTestId('scope-note')).toHaveText(SCOPE_NOTE);
  await expect(answer.getByTestId('model-badge')).toHaveText('Tencent Hunyuan hy3');
  await expect(answer.getByTestId('request-id')).toHaveText(/^req_/);

  // A chip moves focus to its citation.
  await chips.first().click();
  await expect(page.locator('#cite-C1')).toBeFocused();
});

test('refusal renders calmly with the same layout and the same scope note', async ({ page }) => {
  await login(page, 'jdoe');
  await ask(page, 'Show me the security incident report from the Q3 breach.');
  const answer = page.getByTestId('answer');
  await expect(answer.getByText(REFUSAL_TEXT)).toBeVisible();
  await expect(answer.getByText('No sources were cited for this answer.')).toBeVisible();
  await expect(answer.getByTestId('scope-note')).toHaveText(SCOPE_NOTE);
  await expect(answer.getByTestId('model-badge')).toBeVisible();
  await expect(answer.getByTestId('request-id')).toBeVisible();
  // Never imply existence: no counts of hidden/withheld/restricted results.
  await expect(answer).not.toContainText(/hidden|withheld|restricted|results? (were )?removed/i);
  // (Next's own route announcer is role=alert; only our content is checked.)
  await expect(page.locator('main [role="alert"]')).toHaveCount(0);

  // A non-existent report gets the byte-identical answer text.
  await ask(page, 'Show me the security incident report from the Q7 breach.');
  await expect(page.getByTestId('answer').getByText(REFUSAL_TEXT)).toBeVisible();
});

test('503 shows the "nothing was answered" state', async ({ page }) => {
  await login(page, 'jdoe');
  await ask(page, '[mock:503] anything');
  await expect(page.getByTestId('answer-error')).toContainText('The Internal Brain is unavailable right now. Nothing was answered. Try again in a moment.');
  await expect(page.getByTestId('answer')).toHaveCount(0);
});

test('model badges: fallback, extractive, live re-read and stale cues are text, not colour', async ({ page }) => {
  await login(page, 'leo.ops');
  await ask(page, 'Can we offer multi-currency accounts to an Indonesian SME customer?');
  await expect(page.getByTestId('answer')).toContainText('May be out of date');
  await ask(page, 'What was the root cause of the payment outage last quarter, and what follow-up tickets were created?');
  await expect(page.getByTestId('model-badge')).toHaveText('DeepSeek (fallback)');
  await login(page, 'sara.sec');
  await ask(page, 'Show me the security incident report from the Q3 breach.');
  await expect(page.getByTestId('model-badge')).toHaveText('Deterministic extractive');
});

test('sources panel shows global per-platform status', async ({ page }) => {
  await login(page, 'priya.sre');
  const panel = page.getByTestId('sources-panel');
  for (const p of ['Confluence', 'Jira', 'Slack', 'Google Drive']) await expect(panel).toContainText(p);
  await expect(panel.getByText(/Within target|Behind target/).first()).toBeVisible();
});

test('simulator: revocation and v8 runbook are reflected on the next question', async ({ page }) => {
  await login(page, 'admin.it');
  await page.goto('/sim');
  await page.getByRole('button', { name: 'Remove jdoe from #db-migration' }).click();
  await page.getByRole('button', { name: 'Edit failover runbook (new version)' }).click();
  await expect(page.getByTestId('sim-log').locator('li')).toHaveCount(2);
  await expect(page.getByTestId('sim-log')).toContainText('Done');

  await login(page, 'jdoe');
  await ask(page, 'What backfill throttle are we using for the ledger copy job?');
  await expect(page.getByTestId('answer').getByText(REFUSAL_TEXT)).toBeVisible();

  await login(page, 'priya.sre');
  await ask(page, "What's the latest runbook for the payment-service incident?");
  await expect(page.getByTestId('citations')).toContainText('Version 8');
  await expect(page.getByTestId('citations')).toContainText('Live re-read');

  await resetDemo(page);
});

test('role gates: employees cannot open audit or simulator', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/audit');
  await expect(page.getByRole('heading', { name: 'Not available for your role' })).toBeVisible();
  await page.goto('/sim');
  await expect(page.getByRole('heading', { name: 'Not available for your role' })).toBeVisible();
  const r = await page.request.get('/api/ib/audit/verify');
  expect(r.status()).toBe(403);
});
