// Render the video cards (submission/05_video/cards/*.html) to 1920x1080 PNGs.
const fs = require('fs');
const path = require('path');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'databridge/apps/internal_brain_web/node_modules/playwright'));
(async () => {
  const dir = path.join(repo, 'submission/05_video/cards');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.html')).sort()) {
    await page.goto('file:///' + path.join(dir, f).replace(/\\/g, '/'));
    if (f.includes('trust')) { await page.waitForSelector('.mermaid svg', { timeout: 20000 }); await page.waitForTimeout(1200); }
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(dir, f.replace('.html', '.png')) });
    console.log('card', f);
  }
  await browser.close();
})();
