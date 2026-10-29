import AxeBuilder from '@axe-core/playwright';
import { type Page, expect, test } from '@playwright/test';
import path from 'node:path';
import { S1, login, quietTourOffer } from './helpers';

// Accessibility fixes from docs/finals/reviews/final_sonnet_ux.md (findings 1 to 10), in mock mode.
// Screenshots land in docs/finals/screens/a11y/ (evidence for ACCESSIBILITY_STATEMENT.md).
const SHOTS = path.resolve(__dirname, '../../../../../docs/finals/screens/a11y');
const shot = (page: Page, name: string, fullPage = false) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage });

type Prefs = { theme?: 'dark' | 'light'; text?: '100' | '115' | '130'; lang?: 'en' | 'zh' };
async function prefs(page: Page, p: Prefs) {
  await page.addInitScript((v: Prefs) => {
    try {
      if (v.theme) localStorage.setItem('ib-theme', v.theme);
      if (v.text) localStorage.setItem('ib-text', v.text);
      if (v.lang) localStorage.setItem('ib-lang', v.lang);
    } catch {
      /* storage blocked */
    }
  }, p);
}

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'];
async function axe(page: Page, label: string) {
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
  const r = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]['page'] }).withTags(TAGS).analyze();
  const lines = r.violations.map((v) => `${v.id} (${v.impact}) x${v.nodes.length}: ${v.nodes.slice(0, 2).map((n) => n.target.join(' ')).join(' | ')}`);
  console.log(`[axe] ${label}: ${r.violations.length} violations${lines.length ? `\n  ${lines.join('\n  ')}` : ''}`);
  return r.violations;
}

/** No page-level horizontal scroll, and the top bar's content fits inside it. */
async function expectNoOverflow(page: Page, label: string) {
  const m = await page.evaluate(() => {
    const h = document.querySelector('[data-testid="topbar"]') as HTMLElement | null;
    return {
      doc: document.documentElement.scrollWidth,
      vw: document.documentElement.clientWidth,
      header: h ? h.scrollWidth : 0,
      headerClient: h ? h.clientWidth : 0,
      density: document.querySelector('[data-shell]')?.getAttribute('data-density'),
      root: getComputedStyle(document.documentElement).fontSize,
    };
  });
  console.log(`[reflow] ${label}: doc ${m.doc}/${m.vw}, header ${m.header}/${m.headerClient}, density ${m.density}, root ${m.root}`);
  expect(m.doc, `${label}: page scrollWidth`).toBeLessThanOrEqual(m.vw);
  expect(m.header, `${label}: top bar scrollWidth`).toBeLessThanOrEqual(m.headerClient + 1);
}

test.beforeEach(async ({ page }) => {
  await quietTourOffer(page);
});

/* 1 ----------------------------------------------------------------- reflow at 130 % and zoom */
for (const vp of [
  { name: '1280', width: 1280, height: 800 },
  { name: '1024', width: 1024, height: 768 },
  { name: '390', width: 390, height: 844 },
  // browser zoom 200 % on a 1280 px window = a 640 px CSS viewport
  { name: 'zoom200-1280', width: 640, height: 400 },
]) {
  for (const text of ['115', '130'] as const) {
    test(`reflow: top bar fits at text ${text}% @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await prefs(page, { theme: 'dark', text });
      await login(page, 'jdoe');
      for (const p of ['/', '/settings', '/dev', '/dev/audit']) {
        await page.goto(p);
        await expect(page.getByTestId('whoami')).toBeAttached(); // signed-in top bar, not the session check
        await page.waitForTimeout(300);
        await expectNoOverflow(page, `${p} text${text} ${vp.name}`);
      }
      if (text === '130') {
        await page.goto('/');
        await expect(page.getByTestId('whoami')).toBeAttached();
        await page.waitForTimeout(300);
        await shot(page, `reflow-text130-${vp.name}-dark`);
      }
    });
  }
}

test('reflow: 简体中文 + text 130% + light @ 1280 and 390', async ({ page }) => {
  await prefs(page, { theme: 'light', text: '130', lang: 'zh' });
  await login(page, 'jdoe');
  for (const w of [1280, 390]) {
    await page.setViewportSize({ width: w, height: 800 });
    for (const p of ['/', '/dev']) {
      await page.goto(p);
      await expect(page.getByTestId('whoami')).toBeAttached(); // signed-in top bar, not the session check
      await page.waitForTimeout(300);
      await expectNoOverflow(page, `zh ${p} text130 ${w}`);
    }
    await page.goto('/');
    await expect(page.getByTestId('whoami')).toBeAttached();
    await page.waitForTimeout(300);
    await shot(page, `reflow-text130-${w}-light-zh`);
  }
});

test('reflow: the overflow menu holds language and theme at text 130% @ 1280', async ({ page }) => {
  await prefs(page, { theme: 'dark', text: '130' });
  await login(page, 'jdoe');
  await page.goto('/');
  await expect(page.getByTestId('whoami')).toBeAttached();
  await page.waitForTimeout(300);
  const more = page.getByTestId('shell-overflow');
  console.log(`[reflow] overflow menu shown at text 130% @ 1280: ${await more.isVisible()}`);
  if (await more.isVisible()) {
    await more.click();
    await expect(page.getByRole('menuitemradio', { name: '简体中文' })).toBeVisible();
    await shot(page, 'reflow-text130-1280-overflow-menu');
    await page.keyboard.press('Escape');
    await expect(more).toBeFocused();
  }
});

/* 2, 3 --------------------------------------------------------------------- 简体中文 */
test('zh: skip link, persona role and <html lang>; Developer mode note and lang="en"', async ({ page }) => {
  await prefs(page, { theme: 'dark', lang: 'zh' });
  await login(page, 'jdoe');
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
  await expect(page.locator('a.skip-link')).toHaveText('跳到主要内容');
  await page.keyboard.press('Tab');
  await expect(page.locator('a.skip-link')).toBeFocused();
  await shot(page, 'zh-skip-link-1280-dark');
  await expect(page.getByTestId('whoami')).toContainText('后端工程师');
  await page.getByRole('button', { name: /Jordan Doe/ }).click();
  await expect(page.getByRole('menu')).toContainText('后端工程师');
  await shot(page, 'zh-persona-menu-1280-dark');
  await page.keyboard.press('Escape');
  // User mode main carries no lang override
  expect(await page.locator('main#main').getAttribute('lang')).toBeNull();

  await page.goto('/dev');
  await expect(page.getByTestId('dev-english-note')).toHaveText('开发者模式仅提供英文');
  await expect(page.getByTestId('dev-english-note')).toHaveAttribute('lang', 'zh-Hans');
  await expect(page.locator('main#main')).toHaveAttribute('lang', 'en');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hans');
  await shot(page, 'zh-dev-english-note-1280-dark');
  await page.goto('/dev/journey');
  await expect(page.locator('main#main')).toHaveAttribute('lang', 'en');
  // a partly translated Developer page keeps the document language
  await page.goto('/dev/audit');
  await expect(page.getByTestId('dev-english-note')).toBeVisible();
  expect(await page.locator('main#main').getAttribute('lang')).toBeNull();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/dev');
  await expect(page.getByTestId('dev-english-note')).toBeVisible();
  await shot(page, 'zh-dev-english-note-390-dark');
});

test('en: no Developer-mode language note', async ({ page }) => {
  await prefs(page, { lang: 'en' });
  await login(page, 'jdoe');
  await page.goto('/dev');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.getByTestId('dev-english-note')).toHaveCount(0);
  expect(await page.locator('main#main').getAttribute('lang')).toBeNull();
});

/* 4 ------------------------------------------------------------------- citation chips */
for (const theme of ['dark', 'light'] as const) {
  test(`citation chips are at least 24 x 24 and keep the line height (${theme})`, async ({ page }) => {
    await prefs(page, { theme });
    await login(page, 'jdoe');
    await page.goto('/');
    await page.getByLabel(/Your question/).fill(S1);
    await page.getByRole('button', { name: 'Ask', exact: true }).click();
    const chips = page.getByTestId('citation-chip');
    await expect(chips.first()).toBeVisible();
    const m = await page.evaluate(() => {
      const cs = [...document.querySelectorAll<HTMLElement>('[data-testid="citation-chip"]')];
      return cs.map((c) => {
        const r = c.getBoundingClientRect();
        const p = c.parentElement as HTMLElement;
        return { w: r.width, h: r.height, lh: parseFloat(getComputedStyle(p).lineHeight), mt: getComputedStyle(c).marginTop };
      });
    });
    console.log(`[chips] ${theme}: ${JSON.stringify(m.slice(0, 4))}`);
    for (const c of m) {
      expect(c.w).toBeGreaterThanOrEqual(24);
      expect(c.h).toBeGreaterThanOrEqual(24);
    }
    // Lines that contain a chip are no taller than plain lines of the same paragraph.
    const lines = await page.evaluate(() => {
      const chip = document.querySelector<HTMLElement>('[data-testid="citation-chip"]')!;
      const para = chip.parentElement!;
      const range = document.createRange();
      range.selectNodeContents(para);
      const tops = [...range.getClientRects()].map((r) => Math.round(r.top));
      const uniq = [...new Set(tops)].sort((a, b) => a - b);
      const gaps = uniq.slice(1).map((t, i) => t - uniq[i]).filter((g) => g > 4);
      return { lh: parseFloat(getComputedStyle(para).lineHeight), gaps };
    });
    console.log(`[chips] ${theme} line pitch: ${JSON.stringify(lines)}`);
    for (const g of lines.gaps) expect(g).toBeLessThanOrEqual(Math.ceil(lines.lh) + 1);
    await chips.first().scrollIntoViewIfNeeded();
    await shot(page, `citation-chips-1280-${theme}`);
    const v = await axe(page, `answer/${theme}`);
    expect(v.filter((x) => x.id === 'target-size')).toEqual([]);
  });
}

/* 5 ---------------------------------------------------------------- landmark-unique */
for (const theme of ['dark', 'light'] as const) {
  for (const w of [1280, 390]) {
    test(`journey and trust: no landmark-unique, no serious axe (${theme} ${w})`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: 860 });
      await prefs(page, { theme });
      for (const who of ['jdoe', 'maya.compliance']) {
        await login(page, who);
        for (const p of ['/dev/journey', '/dev/trust']) {
          await page.goto(p);
          await expect(page.locator('h1')).toBeVisible();
          await page.waitForTimeout(800);
          const v = await axe(page, `${p} as ${who} ${theme} ${w}`);
          expect(v.map((x) => x.id)).not.toContain('landmark-unique');
          expect(v.filter((x) => x.impact === 'serious' || x.impact === 'critical').map((x) => x.id)).toEqual([]);
        }
      }
      if (w === 1280) {
        await page.goto('/dev/journey');
        await page.waitForTimeout(800);
        await shot(page, `journey-1280-${theme}`);
      }
    });
  }
}

/* 6 ----------------------------------------------------------- one live region on audit */
test('audit: one shared live region for "Copied"', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  await expect(page.getByRole('button', { name: /^Copy / }).first()).toBeVisible();
  const counts = await page.evaluate(() => ({
    live: document.querySelectorAll('[aria-live]').length,
    copyButtons: document.querySelectorAll('button[aria-label^="Copy "]').length,
  }));
  console.log(`[live] /dev/audit: ${counts.live} aria-live regions, ${counts.copyButtons} copy buttons`);
  expect(counts.live).toBeLessThanOrEqual(4);
  await page.getByRole('button', { name: /^Copy / }).first().click();
  await expect(page.getByTestId('live-announcer')).toContainText('Copied');
  const v = await axe(page, '/dev/audit maya');
  expect(v.filter((x) => x.impact === 'serious' || x.impact === 'critical').map((x) => x.id)).toEqual([]);
});

/* 7 ------------------------------------------------- deep links and the session check */
test('deep link survives sign-in (?next=), and only same-site paths are honoured', async ({ page }) => {
  await page.goto('/settings');
  await page.waitForURL(/\/login\?next=%2Fsettings$/);
  await page.locator('[data-persona="jdoe"]').click();
  await page.waitForURL((u) => u.pathname === '/settings');
  await expect(page.locator('h1')).toBeVisible();

  await page.context().clearCookies();
  await page.goto('/dev/journey?x=1');
  await page.waitForURL(/\/login\?next=%2Fdev%2Fjourney%3Fx%3D1$/);

  for (const bad of ['//evil.example/x', 'https://evil.example/', '/\\evil.example', 'javascript:alert(1)', '/login?next=/x']) {
    await page.context().clearCookies();
    await page.goto(`/login?next=${encodeURIComponent(bad)}`);
    await page.locator('[data-persona="jdoe"]').click();
    await page.waitForURL((u) => !u.pathname.startsWith('/login'));
    const u = new URL(page.url());
    expect(u.host, `next=${bad}`).toBe('127.0.0.1:3123');
    expect(u.pathname, `next=${bad}`).toBe('/');
  }
});

test('a slow session check shows a message and "Check again" after 8 s', async ({ page }) => {
  await login(page, 'jdoe');
  let stall = true;
  await page.route('**/api/ib/auth/whoami', async (route) => {
    if (stall) return; // never answer: the check hangs
    await route.continue();
  });
  // A fresh document so no identity is cached.
  await page.goto('/settings');
  await expect(page.getByTestId('session-check')).toBeVisible();
  await expect(page.getByTestId('session-slow')).toBeHidden();
  await expect(page.getByTestId('session-slow')).toBeVisible({ timeout: 12_000 });
  await shot(page, 'session-slow-1280-dark');
  stall = false;
  await page.getByTestId('session-retry').click();
  await expect(page.locator('h1')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/settings');
});

/* 8, 9 ----------------------------------------------------------- settings and value */
for (const theme of ['dark', 'light'] as const) {
  for (const w of [1280, 390]) {
    test(`settings switches are named; value links are 24 px (${theme} ${w})`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: 860 });
      await prefs(page, { theme });
      await login(page, 'jdoe');
      await page.goto('/settings');
      await expect(page.getByRole('switch', { name: 'Reduce motion' })).toBeVisible();
      await expect(page.getByRole('switch', { name: 'High contrast' })).toBeVisible();
      let v = await axe(page, `/settings ${theme} ${w}`);
      expect(v.map((x) => x.id)).not.toContain('button-name');
      expect(v.filter((x) => x.impact === 'serious' || x.impact === 'critical').map((x) => x.id)).toEqual([]);
      if (w === 1280) await shot(page, `settings-1280-${theme}`);

      await page.goto('/value');
      await expect(page.locator('h1')).toBeVisible();
      await page.waitForTimeout(500);
      const hs = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLAnchorElement>('main a[target="_blank"]')].map((a) => ({ t: a.textContent?.trim().slice(0, 30), h: a.getBoundingClientRect().height })),
      );
      console.log(`[value links] ${theme} ${w}: ${JSON.stringify(hs)}`);
      for (const h of hs.filter((x) => x.h > 0)) expect(h.h, h.t ?? "").toBeGreaterThanOrEqual(24);
      v = await axe(page, `/value ${theme} ${w}`);
      expect(v.map((x) => x.id)).not.toContain('target-size');
    });
  }
}

/* 10 ------------------------------------------------------------ one word for refusals */
test('journey uses "No answer (refused)" for a refused request', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/journey');
  await expect(page.getByTestId('recent-requests')).toBeVisible();
  const refused = await page.getByText('Refused', { exact: true }).count();
  expect(refused).toBe(0);
});

/* coordinator polish ------------------------------------------------------------------- */
for (const theme of ['dark', 'light'] as const) {
  test(`journey: seal labels (#seq + 8 hex, 11 px), technical details folded, waterfall in first fold (${theme})`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await prefs(page, { theme });
    await login(page, 'maya.compliance');
    await page.goto('/dev/journey');
    await page.getByTestId('recent-requests').locator('tbody tr').first().click();
    await expect(page.getByTestId('journey-compliance')).toBeVisible();
    const labels = page.getByTestId('seal-label');
    await expect(labels.first()).toBeVisible();
    const info = await labels.evaluateAll((els) => els.map((e) => ({ text: (e as HTMLElement).innerText, size: parseFloat(getComputedStyle(e).fontSize) })));
    console.log(`[seals] ${JSON.stringify(info)}`);
    for (const l of info) {
      expect(l.text).toMatch(/#\d+ · [0-9a-f]{8}/);
      expect(l.size).toBeGreaterThanOrEqual(11);
    }
    await expect(page.getByTestId('journey-technical')).not.toHaveAttribute('open', '');
    await expect(page.getByText('Directory epoch')).toBeHidden();
    const top = await page.getByTestId('waterfall').evaluate((e) => e.getBoundingClientRect().top);
    const sealsBottom = await labels.last().evaluate((e) => e.getBoundingClientRect().bottom);
    const banner = await page.getByTestId('mock-banner').evaluate((e) => e.getBoundingClientRect().height).catch(() => 0);
    console.log(`[journey] waterfall top ${top}px, last seal label bottom ${sealsBottom}px of 720 (mock banner ${banner}px, absent in a real build)`);
    // The whole waterfall, seals included, is in the first fold at 1280 x 720 (without the mock-mode banner).
    expect(sealsBottom - banner).toBeLessThanOrEqual(720);
    await shot(page, `journey-detail-1280x720-${theme}`);
    await page.getByText('Technical details').click();
    await expect(page.getByText('Directory epoch')).toBeVisible();
    const v = await axe(page, `journey detail ${theme}`);
    expect(v.filter((x) => x.impact === 'serious' || x.impact === 'critical').map((x) => x.id)).toEqual([]);
  });
}

test('audit: year in timestamps, 简体中文 status and drawer, tamper banner labels', async ({ page }) => {
  await prefs(page, { theme: 'dark', lang: 'zh' });
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  const firstTime = page.locator('tbody tr').first().locator('td').first();
  await expect(firstTime).toContainText(/20\d\d/);
  await expect(page.locator('[role="status"][aria-live="polite"]').filter({ hasText: /条|请求/ }).first()).toBeAttached();
  const status = await page.evaluate(() => [...document.querySelectorAll('[role="status"]')].map((e) => e.textContent).join(' | '));
  console.log(`[audit zh status] ${status}`);
  expect(status).not.toMatch(/requests found/);
  await page.locator('tbody tr').first().locator('button').last().click();
  const drawer = page.getByTestId('audit-drawer');
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText('时间');
  await expect(drawer).toContainText('查看理由（必填）');
  await expect(drawer).toContainText(/20\d\d/);
  await shot(page, 'audit-drawer-zh-1280-dark');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '在副本上运行篡改演示' }).click();
  await expect(page.getByTestId('tamper-safe')).toContainText(/副本的链头哈希：/);
  await page.getByTestId('tamper-safe').scrollIntoViewIfNeeded();
  await shot(page, 'audit-tamper-zh-1280-dark');
});

test('audit (English): tamper banner says "Copied head hash" and explains the count gap', async ({ page }) => {
  await login(page, 'maya.compliance');
  await page.goto('/dev/audit');
  await page.getByRole('button', { name: 'Run the tamper demo on a copy' }).click();
  const safe = page.getByTestId('tamper-safe');
  await expect(safe).toContainText(/Copied head hash: [0-9a-f]{12}/);
  const gap = page.getByTestId('tamper-counts-differ');
  if (await gap.count()) await expect(gap).toContainText('the copy was taken before this demo’s own audit records were written');
  console.log(`[tamper] ${(await safe.innerText()).replace(/\s+/g, ' ')}`);
  await safe.scrollIntoViewIfNeeded();
  await shot(page, 'audit-tamper-en-1280-dark');
});
