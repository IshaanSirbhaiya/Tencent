// Screenshot the rendered CodeBuddy session pages: one overview (first screen) + one full page each.
const path = require('path');
const { chromium } = require(path.join('E:/Tenecent Hack_Walnut/Tencent/databridge/apps/internal_brain_web/node_modules/playwright'));
(async () => {
  const dir = 'E:/Tenecent Hack_Walnut/Tencent/submission/01_codebuddy';
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const only = process.argv.slice(2);
  for (const s of (only.length ? only : ['cb1', 'cb2', 'cb3', 'cb4', 'cb5', 'cb6', 'cb7', 'cb8', 'cb9'])) {
    await page.goto('file:///' + dir + '/html/' + s + '.html');
    await page.screenshot({ path: `${dir}/${s}_session_overview.png` });
    await page.screenshot({ path: `${dir}/${s}_session_full.png`, fullPage: true });
    console.log('shot', s);
  }
  await browser.close();
})();
