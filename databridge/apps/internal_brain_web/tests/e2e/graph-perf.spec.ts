import { expect, test } from '@playwright/test';
import { login } from './helpers';
// Frame-rate probe during continuous camera animation (zoom + pan). Skipped unless GRAPH_PERF=1.
test.skip(!process.env.GRAPH_PERF, 'set GRAPH_PERF=1 to measure frame rate');
for (const size of ['as served', '1,300 nodes']) test(`fps during pan/zoom (${size})`, async ({ page }) => {
  await login(page, 'jdoe');
  if (size !== 'as served') {
    // Rendering stress only: pad the map response with copies of real items (ids suffixed) to ~1,300 nodes.
    // The graph page reads the map from GET /ib/galaxy (one live pass for the overview and the map).
    await page.route('**/api/ib/ib/galaxy**', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      const map = body.map;
      for (const p of map.platforms) for (const c of p.containers) c.items = [...c.items, ...c.items.slice(0, Math.ceil(c.items.length * 0.5)).map((it: any) => ({ ...it, doc_id: `${it.doc_id}~x` }))];
      await route.fulfill({ response: res, json: body });
    });
  }
  await page.goto('/dev/graph');
  const canvas = page.getByTestId('graph-main-canvas');
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(800);
  await canvas.focus();
  await page.evaluate(() => {
    (window as any).__frames = [];
    const loop = (t: number) => { (window as any).__frames.push(t); if ((window as any).__frames.length < 100000) requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  });
  const t0 = Date.now();
  while (Date.now() - t0 < 4000) {
    for (const k of ['+', 'ArrowLeft', '-', 'ArrowUp', 'ArrowRight', 'ArrowDown']) {
      await page.keyboard.press(k);
      await page.waitForTimeout(90);
    }
  }
  const r = await page.evaluate(() => {
    const f: number[] = (window as any).__frames;
    const d = f.slice(1).map((t, i) => t - f[i]).sort((a, b) => a - b);
    const span = (f[f.length - 1] - f[0]) / 1000;
    return { fps: Math.round((f.length - 1) / span), p50: Math.round(d[Math.floor(d.length * 0.5)] * 10) / 10, p95: Math.round(d[Math.floor(d.length * 0.95)] * 10) / 10, max: Math.round(d[d.length - 1]) };
  });
  const gl = await page.evaluate(() => { const c = document.createElement('canvas').getContext('webgl'); const e = c?.getExtension('WEBGL_debug_renderer_info'); return e ? c!.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'unknown'; });
  const nodes = await canvas.getAttribute('aria-label');
  console.log('PERF', size, JSON.stringify(r), 'renderer', gl);
});
