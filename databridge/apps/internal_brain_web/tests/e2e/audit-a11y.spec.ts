import { expect, test } from '@playwright/test';
import { expectAccessible, login, quietTourOffer } from './helpers';

// axe on the governance pages in their populated states: dark and light, desktop and phone.
for (const theme of ['dark', 'light'] as const) {
  for (const vp of [
    { name: 'desktop', width: 1280, height: 860 },
    { name: 'mobile', width: 390, height: 844 },
  ]) {
    test.describe(`governance axe @ ${theme}/${vp.name}`, () => {
      test.use({ viewport: { width: vp.width, height: vp.height } });
      test.beforeEach(async ({ page }) => {
        await page.addInitScript((t) => localStorage.setItem('ib-theme', t), theme);
        // Developer pages offer the Developer tour on first entry (D-36); its toast would cover page controls.
        await quietTourOffer(page);
      });

      test('audit: verified, tamper results, drawer', async ({ page }) => {
        await login(page, 'maya.compliance');
        await page.goto('/dev/audit?verify=1');
        await expect(page.getByTestId('verify-status')).toContainText('Chain intact');
        await page.getByRole('button', { name: 'Run the tamper demo on a copy' }).click();
        await expect(page.getByTestId('tamper-safe')).toBeVisible();
        // the scenario rows fade in one after another; measure contrast once they have settled
        await expect(page.locator('[data-testid^="tamper-row-"]').last()).toHaveCSS('opacity', '1');
        await expectAccessible(page, `dev-audit/${theme}/${vp.name}`);
        await page.getByRole('button', { name: /Details for request/ }).first().click();
        await expect(page.getByTestId('audit-drawer')).toBeVisible();
        await expectAccessible(page, `dev-audit-drawer/${theme}/${vp.name}`);
      });

      test('simulator', async ({ page }) => {
        await login(page, 'admin.it');
        await page.goto('/dev/sim');
        await page.getByRole('button', { name: 'Reset demo' }).click();
        await expect(page.getByTestId('sim-log')).toContainText('Done');
        await expectAccessible(page, `dev-sim/${theme}/${vp.name}`);
      });

      test('trust centre', async ({ page }) => {
        await login(page, 'jdoe');
        await page.goto('/dev/trust');
        await expect(page.getByTestId('crosswalk')).toBeVisible();
        await expectAccessible(page, `dev-trust/${theme}/${vp.name}`);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow).toBeLessThanOrEqual(0);
      });
    });
  }
}
