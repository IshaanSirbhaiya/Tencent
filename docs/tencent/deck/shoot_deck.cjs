// Render submission/07_deck/html/slideNN.html to 1920x1080 PNGs in submission/07_deck/png/.
const fs = require('fs');
const path = require('path');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'databridge/apps/internal_brain_web/node_modules/playwright'));
(async () => {
  const src = path.join(repo, 'submission/07_deck/html');
  const out = path.join(repo, 'submission/07_deck/png');
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  for (const f of fs.readdirSync(src).filter((x) => x.endsWith('.html')).sort()) {
    await page.goto('file:///' + path.join(src, f).replace(/\\/g, '/'));
    await page.waitForLoadState('load');
    await page.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 30000 });
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(out, f.replace('.html', '.png')) });
    console.log('slide', f);
  }
  await browser.close();
})();
