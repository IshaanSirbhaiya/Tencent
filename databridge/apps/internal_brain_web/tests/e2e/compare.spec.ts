import { expect, test } from '@playwright/test';
import { REFUSAL_TEXT, expectAccessible, login } from './helpers';

// Two people, one question. Each side is a separate SIGNED session (slots b and c); the main
// session (slot a) is never touched.

test('answers differ, maps differ, and the main session keeps its identity', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/compare');
  await expect(page.getByTestId('compare-why')).toHaveText(
    "Each answer uses only what that person can open. The contractor's answer doesn't hint that more exists.",
  );
  await expect(page.getByLabel('First person')).toHaveValue('jdoe');
  await expect(page.getByLabel('Second person')).toHaveValue('ken.ext');
  await page.getByRole('button', { name: 'Ask for both' }).click();

  const left = page.getByTestId('compare-b');
  const right = page.getByTestId('compare-c');
  await expect(left.getByTestId('answer')).toBeVisible();
  await expect(right.getByTestId('answer')).toBeVisible();
  await expect(left.getByTestId('citation-chip').first()).toBeVisible();
  await expect(right.getByText(REFUSAL_TEXT)).toBeVisible();
  const a = await left.getByTestId('answer').locator('p.whitespace-pre-wrap').innerText();
  const b = await right.getByTestId('answer').locator('p.whitespace-pre-wrap').innerText();
  expect(a).not.toEqual(b);
  await expect(left.getByTestId('checks-strip')).toContainText('Signed in as jdoe');
  await expect(right.getByTestId('checks-strip')).toContainText('Signed in as ken.ext');
  await expect(left.getByTestId('map-summary')).toContainText('867 items in 17 places across 4 platforms');
  await expect(right.getByTestId('map-summary')).toContainText('24 items in 2 places across 2 platforms');
  await expect(right).not.toContainText(/hidden|withheld|restricted/i);

  // Slot sessions did not overwrite the main one.
  const main = await (await page.request.get('/api/ib/auth/whoami')).json();
  expect(main.sub).toBe('maya.compliance');
  await expect(page.getByTestId('whoami')).toContainText('maya.compliance');
  const sb = await (await page.request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': 'b' } })).json();
  const sc = await (await page.request.get('/api/ib/auth/whoami', { headers: { 'x-ib-slot': 'c' } })).json();
  expect(sb.sub).toBe('jdoe');
  expect(sc.sub).toBe('ken.ext');
  // Slots reach only the compare routes, never the audit console.
  expect((await page.request.get('/api/ib/audit/verify', { headers: { 'x-ib-slot': 'b' } })).status()).toBe(404);
  expect((await page.request.get('/api/ib/audit/verify')).status()).toBe(200);

  await expectAccessible(page, 'compare/desktop');
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
  await page.goto('/compare');
  await page.getByRole('button', { name: 'Ask for both' }).click();
  await expect(page.getByTestId('compare-c').getByTestId('answer')).toBeVisible();
  await expect(page.getByTestId('compare-b').getByTestId('answer')).toBeVisible();
  await expectAccessible(page, 'compare/mobile');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
