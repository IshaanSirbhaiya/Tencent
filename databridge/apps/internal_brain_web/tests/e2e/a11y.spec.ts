import { expect, test } from '@playwright/test';
import { S1, ask, expectAccessible, login } from './helpers';

// axe-core on the five screens, in their populated states, at desktop and phone widths.
for (const vp of [
  { name: 'desktop', width: 1280, height: 860 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test.describe(`axe @ ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test('1 login', async ({ page }) => {
      await page.goto('/login');
      await expect(page.getByTestId('synthetic-label')).toBeVisible();
      await expectAccessible(page, `login/${vp.name}`);
    });

    test('2 ask with answer + 3 sources panel', async ({ page }) => {
      await login(page, 'jdoe');
      await ask(page, S1);
      await expect(page.getByTestId('sources-panel')).toContainText('Confluence');
      await expectAccessible(page, `ask/${vp.name}`);
      // refusal state too
      await ask(page, 'Show me the security incident report from the Q3 breach.');
      await expectAccessible(page, `ask-refusal/${vp.name}`);
    });

    test('4 audit with verify result and drawer', async ({ page }) => {
      await login(page, 'maya.compliance');
      await page.goto('/audit');
      await expect(page.getByTestId('audit-table')).toBeVisible();
      await page.getByRole('button', { name: 'Show every denied retrieval last week.' }).click();
      await expect(page.getByTestId('parsed-filter')).toBeVisible();
      await page.getByRole('button', { name: 'Verify audit chain' }).click();
      await expect(page.getByTestId('verify-status')).toBeVisible();
      await expectAccessible(page, `audit/${vp.name}`);
      await page.getByRole('button', { name: /Details for request/ }).first().click();
      await expect(page.getByTestId('audit-drawer')).toBeVisible();
      await expectAccessible(page, `audit-drawer/${vp.name}`);
    });

    test('5 simulator', async ({ page }) => {
      await login(page, 'admin.it');
      await page.goto('/sim');
      await page.getByRole('button', { name: 'Reset demo' }).click();
      await expect(page.getByTestId('sim-log')).toContainText('Done');
      await expectAccessible(page, `sim/${vp.name}`);
    });
  });
}

test('light theme: ask and audit stay AA', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('ib-theme', 'light'));
  await login(page, 'maya.compliance');
  await ask(page, 'When is the technology risk audit evidence pack due?');
  await expectAccessible(page, 'ask/light');
  await page.goto('/audit');
  await expect(page.getByTestId('audit-table')).toBeVisible();
  await expectAccessible(page, 'audit/light');
});

test('no horizontal page overflow at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'jdoe');
  await ask(page, S1);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
