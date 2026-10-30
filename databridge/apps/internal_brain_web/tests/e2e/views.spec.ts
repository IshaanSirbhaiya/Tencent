import { type Page, expect, test } from '@playwright/test';
import { ask, expectAccessible, login } from './helpers';

// Pages beside the chat (just-in-time views, mock mode). After each answer the Brain is asked
// "page or chat?" (the mock decides by the rules, like the server without Claude): a simple answer
// stays in the chat; a question that asks for structure opens a page in the canvas on the right,
// the chat moves left and the sidebar shrinks to its icons. "Open as page" builds one on request;
// "Change this page" and a short request in the chat ("make it a table") rebuild the open page
// without asking the Brain again. Below 1024 px the page is a full-screen sheet. Nothing in a page
// renders as HTML. Scratch config: views.config.ts (a dev server on :3100 in mock mode).

const Q = 'Can we offer multi-currency accounts to an Indonesian SME?';
const SHOTS = '../../../docs/finals/screens/views';

async function quiet(page: Page, theme: 'dark' | 'light' = 'dark') {
  await page.addInitScript((th) => {
    try {
      localStorage.setItem('ib-tour-offer', 'dismissed');
      localStorage.setItem('ib-theme', th);
      // the remembered sidebar choice starts open, once per test (not again on a reload)
      if (!sessionStorage.getItem('views-test')) {
        localStorage.removeItem('ib-nav');
        sessionStorage.setItem('views-test', '1');
      }
    } catch {
      /* storage blocked */
    }
  }, theme);
}

async function leoAnswer(page: Page, question = Q, theme: 'dark' | 'light' = 'dark') {
  await quiet(page, theme);
  await login(page, 'leo.ops');
  await ask(page, question);
  return page.getByTestId('answer');
}

test('a simple answer stays in the chat; "Open as page" opens it beside the chat', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  const answer = await leoAnswer(page);
  const split = page.locator('.ask-split');
  await page.waitForTimeout(1200); // the decision ran: no page for a simple question
  await expect(split).toHaveAttribute('data-canvas', 'closed');
  await expect(page.locator('html')).not.toHaveAttribute('data-nav', 'rail');

  await answer.getByTestId('view-open-page').click();
  await expect(split).toHaveAttribute('data-canvas', 'open');
  await expect(page.locator('html')).toHaveAttribute('data-nav', 'rail'); // the sidebar shrinks to icons
  const canvas = page.getByTestId('view-canvas');
  const surface = canvas.getByTestId('view-surface');
  await expect(surface).toHaveAttribute('data-layout', /^facts table chart .*sources$/);
  await expect(answer.getByTestId('view-in-canvas')).toHaveText('Showing on the right');
  await expect(surface.getByTestId('view-by')).toHaveText('Laid out by rules from your request, no model');
  await expect(surface.getByTestId('deliver-table').getByRole('columnheader')).toHaveText(['market', 'plan', 'product', 'availability', 'price']);
  await expect(canvas.getByTestId('view-recorded')).toContainText('Recorded in the audit trail before it was shown');
  await expectAccessible(page, 'view canvas (dark)');
  await page.screenshot({ path: `${SHOTS}/mock-1-canvas-dark.png` });

  // a chip moves focus to that source in the page's own list (no second modal)
  await surface.getByTestId('citation-chip').first().click();
  await expect(surface.getByTestId('view-sources').locator('li:focus')).toHaveCount(1);

  // "Change this page": the person's own words rebuild it
  await canvas.getByTestId('view-change-input').fill('just a timeline please');
  await canvas.getByTestId('view-change-send').click();
  await expect(surface).toHaveAttribute('data-layout', 'timeline sources');

  // full screen, close (the chat comes back to the middle, the sidebar opens), show again
  await canvas.getByTestId('view-open').click();
  await expect(page.getByTestId('view-sheet').getByTestId('view-surface')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('view-sheet')).toHaveCount(0);
  await canvas.getByTestId('view-canvas-close').click();
  await expect(split).toHaveAttribute('data-canvas', 'closed');
  await expect(page.locator('html')).not.toHaveAttribute('data-nav', 'rail');
  await answer.getByTestId('view-show').click();
  await expect(split).toHaveAttribute('data-canvas', 'open');
  await expect(canvas.getByTestId('view-surface')).toHaveAttribute('data-layout', 'timeline sources');
  // known and not from this feature: Recharts 2.12's defaultProps warning (also on the deliverables
  // chart) and /ib/status before sign-in (401)
  expect(errors.filter((e) => !/favicon|Download the React DevTools|Support for defaultProps|status of 401/.test(e))).toEqual([]);
});

test('a question that asks for structure opens its page by itself; the chat keeps the keyboard', async ({ page }) => {
  await leoAnswer(page, 'Show the SME pricing for multi-currency accounts as a table and a chart');
  const surface = page.getByTestId('view-canvas').getByTestId('view-surface');
  await expect(surface).toHaveAttribute('data-layout', 'table chart sources');
  await expect(page.getByTestId('view-canvas').locator(':focus')).toHaveCount(0);
});

test('with a page open, a short request in the chat changes the page, not the Brain', async ({ page }) => {
  const answer = await leoAnswer(page, 'Show the SME pricing for multi-currency accounts as a table and a chart');
  const surface = page.getByTestId('view-canvas').getByTestId('view-surface');
  await expect(surface).toHaveAttribute('data-layout', 'table chart sources');
  const asks: string[] = [];
  page.on('request', (r) => r.url().includes('/ib/ask') && asks.push(r.url()));
  const box = page.locator('textarea').first();
  await box.fill('make it a timeline');
  await box.press('Enter');
  await expect(page.getByTestId('page-edit')).toContainText('updated');
  await expect(surface).toHaveAttribute('data-layout', 'timeline sources');
  expect(asks).toEqual([]);
  // still the one answer (now above the edit): no new question was asked
  await expect(page.getByTestId('answer-previous')).toHaveCount(1);
  await expect(answer).toHaveCount(0);
});

test('the sidebar can shrink to its icons and open again (remembered)', async ({ page }) => {
  await quiet(page);
  await login(page, 'leo.ops');
  await page.getByTestId('nav-rail-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-nav', 'rail');
  await expect(page.locator('aside[data-sidebar]')).toHaveCSS('width', '60px');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-nav', 'rail');
  await page.getByTestId('nav-rail-toggle').click();
  await expect(page.locator('html')).not.toHaveAttribute('data-nav', 'rail');
});

test('light: the Xcode-like Ask, and a page beside it', async ({ page }) => {
  await leoAnswer(page, 'Show the SME pricing for multi-currency accounts as a table and a chart', 'light');
  await expect(page.getByTestId('view-canvas').getByTestId('view-surface')).toHaveAttribute('data-layout', 'table chart sources');
  await expectAccessible(page, 'view canvas (light)');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${SHOTS}/light-1-canvas.png` });
});

test('below 1024 px the page opens as a full-screen sheet', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const answer = await leoAnswer(page);
  await answer.getByTestId('view-open-page').click();
  const sheet = page.getByTestId('view-canvas-sheet');
  await expect(sheet.getByTestId('view-surface')).toHaveAttribute('data-layout', /^facts table chart/);
  await expect(page.locator('.ask-split')).toHaveAttribute('data-canvas', 'closed');
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);
});

test('nothing in a page renders as HTML, and 简体中文 translates the chrome only', async ({ page }) => {
  await quiet(page);
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-lang', 'zh');
    } catch {
      /* storage blocked */
    }
  });
  await login(page, 'leo.ops');
  const box = page.locator('textarea').first(); // the composer (its label is translated)
  await box.fill(Q);
  await box.press('Enter');
  const answer = page.getByTestId('answer');
  await expect(answer).toBeVisible();
  await answer.getByTestId('view-open-page').click();
  const canvas = page.getByTestId('view-canvas');
  await expect(canvas.getByTestId('view-surface')).toBeVisible();
  await canvas.getByTestId('view-change-input').fill('<img src=x onerror="window.__xss=1"> 表格');
  await canvas.getByTestId('view-change-send').click();
  const surface = canvas.getByTestId('view-surface');
  await expect(surface).toHaveAttribute('data-layout', 'table sources');
  await expect(surface.getByTestId('view-asked')).toContainText('<img src=x');
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
  await expect(surface.locator('img')).toHaveCount(0);
  await expect(surface.getByTestId('view-by')).toHaveText('根据您的要求按规则排版，未使用模型');
  await expect(surface.getByTestId('view-title')).toHaveAttribute('lang', 'en');
  await expectAccessible(page, 'view: zh');
});
