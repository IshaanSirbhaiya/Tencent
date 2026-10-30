import { type Page, expect, test } from '@playwright/test';
import { S1, ask, expectAccessible, login, resetDemo } from './helpers';

// "Act on this answer" (mock mode): the reply draft (approved wording, never sent), the follow-up
// ticket (proposed, confirmed, created), the one polite message when a ticket can't be made, the
// one "nothing to act on" for someone else's request, and the action records in the journey.
// Run with the scratch config on your own dev server: tests/e2e/act.config.ts.

const VELQUARRY = "Why has Velquarry Trading's account been restricted, and what should I tell the customer?";

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await resetDemo(page);
  await page.close();
});

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

test('account manager: the reply draft is the approved wording, for review, never sent', async ({ page }) => {
  await quiet(page);
  await login(page, 'amy.am');
  await ask(page, VELQUARRY);
  const answer = page.getByTestId('answer');
  const row = answer.getByTestId('act-row');
  await expect(row).toBeVisible();
  await expect(row.getByRole('heading', { name: 'Act on this answer' })).toBeVisible();
  await row.getByTestId('act-draft').click();
  const sheet = page.getByTestId('act-draft-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole('heading', { name: 'Draft for your review' })).toBeVisible();
  await expect(sheet.getByTestId('act-draft-label')).toHaveText('Draft for your review');
  await expect(sheet.getByTestId('act-draft-how')).toHaveText('Uses the approved wording, word for word');
  const text = sheet.getByTestId('act-draft-text');
  await expect(text).toContainText('Your account is undergoing a routine review in line with our regulatory obligations.');
  await expect(text).toContainText('We cannot share further details.');
  await expect(text).toContainText('Kind regards,');
  await expect(text).not.toContainText(/velquarry|transaction monitoring|suspicious/i);
  await expect(sheet.getByTestId('act-cite').first()).toHaveText('1');
  await expect(sheet.getByTestId('act-sources').getByRole('listitem')).toHaveCount(1);
  await expectAccessible(page, 'act draft sheet (dark)');

  // Edit before sending stays local; the server draft is untouched
  await sheet.getByTestId('act-draft-edit').click();
  const editor = sheet.getByTestId('act-draft-editor');
  await expect(editor).toBeVisible();
  await editor.fill('Hello,\n\nEdited locally.');
  await expect(sheet.getByRole('button', { name: 'Undo my edits' })).toBeEnabled();
  await page.screenshot({ path: 'screenshots/act/act-draft-amy-dark.png' });

  // Escape closes and returns focus to the button that opened it
  await page.keyboard.press('Escape');
  await expect(sheet).toBeHidden();
  await expect(row.getByTestId('act-draft')).toBeFocused();

  // Amy holds no Jira create role: one polite sentence, no reason
  await row.getByTestId('act-ticket').click();
  const tsheet = page.getByTestId('act-ticket-sheet');
  await expect(tsheet.getByTestId('act-unavailable')).toHaveText("A follow-up ticket can’t be created from this answer. Nothing was changed.");
  await expectAccessible(page, 'act ticket unavailable');
});

test('engineer: a follow-up ticket is proposed, confirmed and created', async ({ page }) => {
  await quiet(page, 'light');
  await login(page, 'jdoe');
  await ask(page, S1);
  const answer = page.getByTestId('answer');
  const reqId = (await answer.getAttribute('data-request'))!;
  await answer.getByTestId('act-ticket').click();
  const sheet = page.getByTestId('act-ticket-sheet');
  await expect(sheet.getByTestId('act-ticket-view')).toBeVisible();
  await expect(sheet.getByTestId('act-ticket-project')).toBeVisible();
  const summary = sheet.getByTestId('act-ticket-summary');
  await expect(summary).toHaveValue(`Follow-up: ${S1}`);
  const desc = sheet.getByTestId('act-ticket-description');
  await expect(desc).toContainText(`(request ${reqId})`);
  await expect(desc).toContainText('https://');
  await expectAccessible(page, 'act ticket proposal (light)');
  await summary.fill('Chase the replica lag blocker before the wave 4 cutover');
  await page.screenshot({ path: 'screenshots/act/act-ticket-proposal-light.png' });
  await sheet.getByTestId('act-ticket-confirm').click();
  const done = sheet.getByTestId('act-created');
  await expect(done).toBeVisible();
  await expect(done).toContainText(/Ticket DBMIG-\d+ created/);
  await expect(done).toContainText('Chase the replica lag blocker before the wave 4 cutover');
  await expect(done.getByTestId('act-created-link')).toHaveAttribute('href', /^https:\/\/companya\.atlassian\.net\/browse\/DBMIG-\d+$/);
  await expectAccessible(page, 'act ticket created');
  await page.screenshot({ path: 'screenshots/act/act-ticket-created-light.png' });

  // the receipt carries the action records in the request's own chain
  const trace = await page.request.get(`/api/ib/ib/trace/${reqId}`);
  const kinds = ((await trace.json()).records as { kind: string }[]).map((r) => r.kind);
  expect(kinds.slice(-3)).toEqual(['action.proposed', 'action.confirmed', 'action.executed']);

  // the same proposal cannot be confirmed twice; someone else's request is "nothing to act on"
  await login(page, 'priya.sre');
  const other = await page.request.post('/api/ib/ib/act/draft', { data: { req_id: reqId } });
  const unknown = await page.request.post('/api/ib/ib/act/draft', { data: { req_id: 'req_0000000000000000' } });
  expect(other.status()).toBe(404);
  expect(await other.text()).toBe(await unknown.text());
  const sneaky = await page.request.post('/api/ib/ib/act/draft', { data: { req_id: reqId, user: 'jdoe' } });
  expect(sneaky.status()).toBe(422);
});

test('the compliance journey shows the action records in the sealed chain', async ({ page }) => {
  await quiet(page);
  await login(page, 'jdoe');
  await ask(page, S1);
  const reqId = (await page.getByTestId('answer').getAttribute('data-request'))!;
  const r = await page.request.post('/api/ib/ib/act/draft', { data: { req_id: reqId, kind: 'reply' } });
  expect(r.status()).toBe(200);
  expect((await r.json()).sent).toBe(false);
  await login(page, 'maya.compliance');
  await page.goto(`/dev/journey/${reqId}`);
  const chain = page.getByTestId('ledger-chain').first();
  await expect(chain).toContainText('action.proposed');
  await expect(chain).toContainText('before the draft or proposal was shown');
  await chain.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'screenshots/act/act-journey-chain.png', fullPage: true });
});

test('phone width: the row wraps and the sheet fits', async ({ page }) => {
  await quiet(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'amy.am');
  await ask(page, VELQUARRY);
  await page.getByTestId('answer').getByTestId('act-draft').click();
  const sheet = page.getByTestId('act-draft-sheet');
  await expect(sheet.getByTestId('act-draft-text')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: 'screenshots/act/act-draft-390.png' });
});

test('Chinese: the act row and sheet are translated', async ({ page }) => {
  await quiet(page);
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-lang', 'zh');
    } catch {
      /* storage blocked */
    }
  });
  await login(page, 'amy.am');
  await page.getByLabel(/Your question|你的问题/).fill(VELQUARRY);
  await page.getByRole('button', { name: /^(Ask|提问)$/ }).click();
  const row = page.getByTestId('answer').getByTestId('act-row');
  await expect(row.getByTestId('act-draft')).toHaveText('起草回复');
  await expect(row.getByTestId('act-ticket')).toHaveText('创建跟进工单');
  await row.getByTestId('act-draft').click();
  await expect(page.getByTestId('act-draft-sheet').getByRole('heading', { name: '草稿，请你审阅' })).toBeVisible();
});
