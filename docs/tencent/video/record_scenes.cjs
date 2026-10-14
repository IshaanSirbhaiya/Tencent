// Record the demo video's screen scenes, one clip per scene, against the RUNNING local stack:
//   web app http://localhost:3100 (-> Brain :8100 -> mock platforms :8200)
//   original Walnut screens http://localhost:3300 (gateway :8300)
//   node docs/tencent/video/record_scenes.cjs [scene ...]      (no args = all scenes)
// Clips land in submission/05_video/clips/<scene>.webm. Every scene starts from a demo reset, so
// scenes can be re-shot in any order. Nothing is faked: each clip is the real app answering.
const fs = require('fs');
const path = require('path');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'databridge/apps/internal_brain_web/node_modules/playwright'));

const WEB = process.env.IB_WEB || 'http://localhost:3100';
const WALNUT = process.env.WALNUT_WEB || 'http://localhost:3300';
const OUT = path.join(repo, 'submission/05_video/clips');
const SIZE = { width: 1920, height: 1080 };
const S1 = "What's the status of the database migration project and were there any blockers raised in Slack last week?";
const pause = (p, s) => p.waitForTimeout(s * 1000);

async function login(page, persona) {
  const r = await page.request.post(`${WEB}/api/ib/auth/demo-login`, { data: { persona } });
  if (!r.ok()) throw new Error(`login ${persona}: ${r.status()}`);
}
async function reset(ctx) {
  const p = await ctx.newPage();
  await login(p, 'admin.it');
  await p.request.post(`${WEB}/api/ib/sim/reset`);
  await p.close();
}
async function ask(page, q, wait = 4) {
  const box = page.getByLabel('Your question');
  await box.click();
  await box.fill('');
  await box.pressSequentially(q, { delay: 28 });
  await pause(page, 0.6);
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.getByTestId('answer').or(page.getByTestId('answer-error')).first().waitFor({ timeout: 60000 });
  await pause(page, wait);
}
async function glide(page, testid, wait = 2.5) {
  const el = page.getByTestId(testid).first();
  if (await el.count()) { await el.scrollIntoViewIfNeeded(); await el.hover().catch(() => {}); await pause(page, wait); }
}

const SCENES = {
  async '01_login'(page) {
    await page.goto(`${WEB}/login`); await pause(page, 3);
    await page.locator('[data-persona="jdoe"]').hover(); await pause(page, 1.5);
    await page.locator('[data-persona="ken.ext"]').hover(); await pause(page, 1.5);
    await page.locator('[data-persona="maya.compliance"]').hover(); await pause(page, 1.5);
    await page.locator('[data-persona="jdoe"]').click();
    await page.waitForURL((u) => !u.pathname.startsWith('/login')); await pause(page, 3);
  },
  async '03_ask_jdoe'(page) {
    await login(page, 'jdoe'); await page.goto(WEB); await pause(page, 2);
    await ask(page, S1, 3);
    for (const c of await page.getByTestId('citation-chip').all()) { await c.hover().catch(() => {}); await pause(page, 1.2); }
    await glide(page, 'checks-strip', 4); await glide(page, 'sources-panel', 5);
  },
  async '04_map'(page) {
    await login(page, 'jdoe'); await page.goto(`${WEB}/map`); await pause(page, 4);
    await glide(page, 'map-totals', 3);
    const place = page.getByTestId('map-place').first();
    if (await place.count()) { await place.click(); await pause(page, 4); }
    await login(page, 'ken.ext'); await page.goto(`${WEB}/map`); await pause(page, 5);
    await glide(page, 'map-totals', 4);
  },
  async '05_compare'(page) {
    await login(page, 'jdoe'); await page.goto(`${WEB}/compare`); await pause(page, 3);
    await page.getByRole('button', { name: /compare/i }).last().click();
    await page.getByTestId('compare-results').waitFor({ timeout: 90000 }); await pause(page, 6);
    await glide(page, 'compare-why', 5);
  },
  async '06_restricted_vs_nonexistent'(page) {
    await login(page, 'ken.ext'); await page.goto(WEB); await pause(page, 2);
    await ask(page, 'Show me the security incident report from the Q3 breach.', 4);
    await ask(page, 'Show me the security incident report from the Q7 breach.', 5);
  },
  async '07_fintech'(page) {
    await login(page, 'leo.ops'); await page.goto(WEB); await pause(page, 2);
    await ask(page, 'Can we offer multi-currency accounts to an Indonesian SME customer?', 5);
    await glide(page, 'sources-panel', 3);
    await ask(page, 'What FX margin do we charge Indonesian SMEs on multi-currency accounts?', 5);
  },
  async '08_revocation_freshness'(page) {
    await login(page, 'admin.it'); await page.goto(`${WEB}/sim`); await pause(page, 3);
    await page.getByRole('button', { name: /remove jdoe|revoke/i }).first().click(); await pause(page, 4);
    await login(page, 'jdoe'); await page.goto(WEB); await pause(page, 1.5);
    await ask(page, S1, 5);
    await login(page, 'admin.it'); await page.goto(`${WEB}/sim`); await pause(page, 2);
    await page.getByRole('button', { name: /runbook/i }).first().click(); await pause(page, 4);
    await login(page, 'priya.sre'); await page.goto(WEB); await pause(page, 1.5);
    await ask(page, "What's the latest runbook for the payment-service incident?", 3);
    await glide(page, 'sources-panel', 5);
  },
  async '09_audit'(page) {
    await login(page, 'maya.compliance'); await page.goto(`${WEB}/audit`); await pause(page, 3);
    const box = page.getByRole('textbox').first();
    await box.click(); await box.fill('');
    await box.pressSequentially('Show me everything jdoe accessed related to the payment-gateway space in the last 30 days', { delay: 25 });
    await box.press('Enter'); await pause(page, 4);
    await glide(page, 'parsed-filter', 3); await glide(page, 'audit-table', 4);
    const details = page.getByRole('button', { name: /Details for request/ }).first();
    if (await details.count()) {
      await details.click(); await pause(page, 3);
      const reason = page.getByLabel(/reason/i).first();
      if (await reason.count()) {
        await reason.pressSequentially('Quarterly access review of the payment gateway space', { delay: 25 });
        await page.getByRole('button', { name: /reveal/i }).first().click(); await pause(page, 4);
      }
      await page.keyboard.press('Escape'); await pause(page, 1);
    }
    await page.getByRole('button', { name: 'Verify audit chain' }).click(); await pause(page, 5);
    await glide(page, 'verify-panel', 4);
  },
  async '11_walnut_original'(page) {
    await page.goto(`${WALNUT}/login`).catch(() => {}); await pause(page, 1);
    const pick = page.getByText('Jordan Doe').first();
    if (await pick.count()) { await pick.click().catch(() => {}); await pause(page, 2); }
    await page.goto(`${WALNUT}/graph`); await pause(page, 6);
    const node = page.locator('.react-flow__node').first();
    if (await node.count()) { await node.click().catch(() => {}); await pause(page, 5); }
    await page.goto(`${WALNUT}/brain`); await pause(page, 6);
    const slider = page.getByRole('slider').first();
    if (await slider.count()) {
      await slider.focus();
      for (let i = 0; i < 12; i++) { await page.keyboard.press('ArrowLeft'); await pause(page, 0.25); }
      await pause(page, 3);
      for (let i = 0; i < 12; i++) { await page.keyboard.press('ArrowRight'); await pause(page, 0.15); }
      await pause(page, 2);
    }
    await page.goto(`${WALNUT}/evidence?entity=knowledge_page%3Acf-1001`); await pause(page, 6);
    await page.goto(`${WALNUT}/ontology`); await pause(page, 5);
  },
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const want = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SCENES);
  const browser = await chromium.launch();
  const needsBrain = want.some((n) => !n.startsWith('11_'));   // scene 11 is the original screens only
  if (want.some((n) => n.startsWith('11_'))) {            // warm the original screens too
    const wc = await browser.newContext({ viewport: SIZE }); const w = await wc.newPage();
    for (const u of ['/graph', '/brain', '/evidence?entity=knowledge_page%3Acf-1001', '/ontology']) {
      await w.goto(WALNUT + u).catch(() => {}); await w.waitForTimeout(4000);
    }
    await wc.close();
  }
  // warm the dev server so compile pauses never appear on camera
  if (needsBrain) {
    const warm = await browser.newContext({ viewport: SIZE });
    const wp = await warm.newPage();
    await login(wp, 'jdoe');
    for (const u of ['/', '/map', '/compare', '/audit', '/sim', '/login']) await wp.goto(WEB + u).catch(() => {});
    await warm.close();
  }
  for (const name of want) {
    const ctx = await browser.newContext({ viewport: SIZE, recordVideo: { dir: OUT, size: SIZE },
                                           colorScheme: 'dark', deviceScaleFactor: 1 });
    if (!name.startsWith('11_')) await reset(ctx);
    const page = await ctx.newPage();
    const t0 = Date.now();
    try { await SCENES[name](page); console.log(`ok   ${name} ${((Date.now() - t0) / 1000).toFixed(1)}s`); }
    catch (e) { console.log(`FAIL ${name}: ${e.message.split('\n')[0]}`); }
    const vid = page.video();
    await ctx.close();
    if (vid) fs.renameSync(await vid.path(), path.join(OUT, `${name}.webm`));
  }
  if (needsBrain) { const ctx = await browser.newContext(); await reset(ctx); await ctx.close(); }
  await browser.close();
})();
