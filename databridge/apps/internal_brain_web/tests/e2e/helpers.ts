import { type Page, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Constants copied from databridge_ib/models.py — the UI must render them verbatim.
export const SCOPE_NOTE = 'Answers draw only on content you can access in Confluence, Jira, Slack and Google Drive.';
export const REFUSAL_TEXT = "I couldn't find an answer to that in the sources you can access.";

export const S1 = "What's the status of the database migration project and were there any blockers raised in Slack last week?";

export async function login(page: Page, persona: string) {
  await page.goto('/login');
  await page.locator(`[data-persona="${persona}"]`).click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'));
  await expect(page.getByTestId('whoami')).toContainText(persona);
}

export async function resetDemo(page: Page) {
  await login(page, 'admin.it');
  const r = await page.request.post('/api/ib/sim/reset');
  expect(r.ok()).toBeTruthy();
}

export async function ask(page: Page, question: string) {
  await page.getByLabel('Your question').fill(question);
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.getByTestId('answer').or(page.getByTestId('answer-error'))).toBeVisible();
}

/** Zero serious/critical axe violations (WCAG 2.0/2.1 A + AA rules). */
export async function expectAccessible(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const bad = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const summary = bad.map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
  console.log(`[axe] ${label}: ${results.violations.length} violations total, ${bad.length} serious/critical, ${results.passes.length} rules passed`);
  expect(summary, `${label} serious/critical axe violations`).toEqual([]);
}
