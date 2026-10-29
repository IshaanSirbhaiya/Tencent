import { expect, test } from '@playwright/test';
import { login } from './helpers';

// /dev/audit integrity: Verify chain goes green, and the sandbox tamper demo shows a broken entry
// while the real log stays untouched. Mock mode (lib/mock/extra/audit.ts serves the tamper demo).

test('verify chain: the chain visual goes green with the record count, head and external match', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  const panel = page.getByTestId('verify-panel');
  await expect(panel).toContainText('Not verified in this session yet.');
  await page.getByRole('button', { name: 'Verify audit chain' }).click();
  await expect(page.getByTestId('verify-status')).toContainText('Chain intact');
  await expect(panel).toHaveAttribute('data-state', 'ok');
  await expect(page.getByTestId('verify-records')).toHaveText(/^\d[\d,]*$/);
  await expect(panel).toContainText('Matches published head');
  await expect(panel.getByRole('button', { name: 'Copy head hash' })).toBeVisible();
});

test('?verify=1 runs Verify on arrival (Presenter mode), and /audit redirects keep the query', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/audit?verify=1');
  await expect(page).toHaveURL(/\/dev\/audit\?verify=1$/);
  await expect(page.getByTestId('verify-status')).toContainText('Chain intact');
});

test('tamper demo: a broken entry in red on the copy, and the real log was not modified', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  const panel = page.getByTestId('tamper-panel');
  await page.getByRole('button', { name: 'Run the tamper demo on a copy' }).click();
  await expect(panel.getByTestId('tamper-safe')).toContainText('Your real audit log was not modified.');
  await expect(panel.getByTestId('tamper-safe')).toContainText(/Copied head hash: [0-9a-f]{12}/);
  // the real log is re-verified right after the sandbox run
  await expect(panel.getByTestId('tamper-real-check')).toContainText('Chain intact');

  // the edited record: detected, with the entry number the backend reported
  const edit = panel.getByTestId('tamper-row-edit-record');
  await expect(edit).toHaveAttribute('data-detected', 'true');
  await expect(edit).toContainText(/Chain broken at entry \d+/);
  await expect(edit).toContainText('Flagged');
  const bad = edit.getByText(/Chain broken at entry \d+/);
  const color = await bad.evaluate((el) => getComputedStyle(el).color);
  const badToken = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bad').trim().split(/\s+/).join(', '));
  expect(color).toBe(`rgb(${badToken})`);

  // the untouched copy stays intact (green), and every row matches what was expected
  const base = panel.getByTestId('tamper-row-baseline');
  await expect(base).toHaveAttribute('data-detected', 'false');
  await expect(base).toContainText('Intact');
  await expect(panel.getByText('Unexpected')).toHaveCount(0);
  // the stated limit is shown honestly, not hidden
  await expect(panel.getByTestId('tamper-row-rollback-after-publication')).toContainText('Not detectable: the stated window');

  // the real chain still verifies afterwards
  await page.getByRole('button', { name: 'Verify audit chain' }).click();
  await expect(page.getByTestId('verify-status')).toContainText('Chain intact');
});

test('?tamper=1 opens and runs the tamper panel', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit?tamper=1');
  await expect(page.getByTestId('tamper-safe')).toBeVisible();
  await expect(page.getByTestId('tamper-panel')).toBeInViewport();
});

test('row → drawer → Open journey; parsed filter chips can be removed', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  await page.getByRole('button', { name: 'Show every denied retrieval last week.' }).click();
  const parsed = page.getByTestId('parsed-filter');
  await expect(parsed).toContainText('Decision:');
  await expect(parsed.getByTestId('audit-parser')).toHaveText('Parsed by rules');
  await parsed.getByRole('button', { name: /^Remove filter Decision/ }).click();
  await expect(parsed).not.toContainText('Decision:');
  await expect(parsed).toContainText('Since:');

  const table = page.getByTestId('audit-table');
  const first = table.locator('tbody tr').first();
  await expect(first.getByRole('button', { name: /^Copy record \d+ hash$/ })).toBeVisible();
  await first.click();
  const drawer = page.getByTestId('audit-drawer');
  await expect(drawer).toContainText('Sealed records');
  const journey = drawer.getByRole('link', { name: 'Open journey' });
  await expect(journey).toHaveAttribute('href', /^\/dev\/journey\/[^/]+$/);
});

test('employees and IT admins get the lock card, and the tamper endpoint is compliance-only', async ({ page }) => {
  await login(page, 'jdoe');
  await page.goto('/dev/audit');
  await expect(page.getByTestId('role-lock')).toBeVisible();
  const r = await page.request.post('/api/ib/audit/tamper-demo', { data: {} });
  expect(r.status()).toBe(403);
});

test('no horizontal page overflow on /dev/audit at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  await expect(page.getByTestId('audit-table').locator('tbody tr').first()).toBeVisible();
  await page.getByRole('button', { name: 'Run the tamper demo on a copy' }).click();
  await expect(page.getByTestId('tamper-safe')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('keyboard only on /dev/audit: search, open a row, close, verify', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  await expect(page.getByTestId('audit-table').locator('tbody tr').first()).toBeVisible();
  const tabTo = async (sel: string, max = 80) => {
    for (let i = 0; i < max; i++) {
      await page.keyboard.press('Tab');
      if (await page.evaluate((s) => !!document.activeElement?.matches(s), sel)) return;
    }
    throw new Error(`could not reach ${sel} with Tab`);
  };
  await tabTo('#nl-question');
  await page.keyboard.type('What did jdoe access in the payment-gateway space over the last 30 days?');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('parsed-filter')).toContainText('jdoe');
  await expect(page.getByTestId('parsed-filter')).toContainText('payment-gateway');

  await tabTo('tr[data-row]');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('audit-drawer')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('audit-drawer')).toHaveCount(0);

  await tabTo('button[aria-label^="Details for request"]');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('audit-drawer')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('button[aria-label^="Details for request"]').first()).toBeFocused();

  await tabTo('[data-testid="verify-panel"] button[aria-busy], [data-testid="verify-panel"] button:not([aria-label])');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('verify-status')).toContainText('Chain intact');
});
