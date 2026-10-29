import { expect, test } from '@playwright/test';
import path from 'node:path';
import { S1, ask, login, resetDemo } from './helpers';

// Captures demo screenshots (mock mode) into ./screenshots. Not an assertion-heavy test.
const OUT = path.join(__dirname, '..', '..', 'screenshots');
const shot = (name: string) => path.join(OUT, `${name}.png`);

for (const vp of [
  { name: '1280', width: 1280, height: 900 },
  { name: '390', width: 390, height: 844 },
]) {
  test.describe(`screenshots @ ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test('capture', async ({ page }) => {
      await resetDemo(page);
      await page.goto('/login');
      await page.screenshot({ path: shot(`01-login-${vp.name}`), fullPage: true });

      await login(page, 'jdoe');
      await ask(page, S1);
      await page.screenshot({ path: shot(`02-ask-answer-${vp.name}`), fullPage: true });
      await ask(page, 'Show me the security incident report from the Q3 breach.');
      await page.screenshot({ path: shot(`03-ask-refusal-${vp.name}`), fullPage: true });

      await login(page, 'maya.compliance');
      await page.goto('/dev/audit');
      await expect(page.getByTestId('audit-table')).toBeVisible();
      await page.getByRole('button', { name: 'Show every denied retrieval last week.' }).click();
      await expect(page.getByTestId('parsed-filter')).toBeVisible();
      await page.getByRole('button', { name: 'Verify audit chain' }).click();
      await expect(page.getByTestId('verify-status')).toBeVisible();
      await page.screenshot({ path: shot(`04-audit-${vp.name}`), fullPage: true });
      await page.getByRole('button', { name: /Details for request/ }).first().click();
      await page.getByLabel('Reason for revealing (required)').fill('Investigating GRC-14: confirm what was disclosed');
      await page.getByRole('button', { name: 'Reveal answer' }).click();
      await expect(page.getByTestId('revealed-answer')).toBeVisible();
      await page.screenshot({ path: shot(`05-audit-drawer-${vp.name}`) });

      await login(page, 'admin.it');
      await page.goto('/dev/sim');
      await page.getByRole('button', { name: 'Edit failover runbook (new version)' }).click();
      await expect(page.getByTestId('sim-log')).toContainText('Done');
      await page.screenshot({ path: shot(`06-sim-${vp.name}`), fullPage: true });

      await login(page, 'priya.sre');
      await ask(page, "What's the latest runbook for the payment-service incident?");
      await page.screenshot({ path: shot(`07-ask-live-reread-${vp.name}`), fullPage: true });
      await resetDemo(page);
    });
  });
}
