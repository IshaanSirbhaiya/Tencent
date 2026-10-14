// Record the demo video's screen scenes, one clip per scene, against the RUNNING local stack:
//   web app http://localhost:3100 (-> Brain :8100 -> mock platforms :8200)
//   original Walnut screens http://localhost:3300 (gateway :8300)
//   node docs/tencent/video/record_scenes.cjs [scene ...]      (no args = all scenes)
// Clips land in submission/05_video/clips/<scene>.webm, plus <scene>.marks.json: when each on-screen
// label should appear, and which slow waits to cut, in seconds from the clip's start (assemble.py
// applies both). Every scene starts
// from a demo reset, so scenes can be re-shot in any order. Nothing is faked: each clip is the real
// app answering.
//
// How it is made to look like a person at the keyboard (VIDEO_SCRIPT.md, "Pacing"):
// - a visible cursor that moves on eased, slightly curved paths and shows a ring on each click;
// - typing with an uneven rhythm (longer after spaces and punctuation, the odd short hesitation);
// - scrolling with the wheel in small steps, never jumping;
// - each scene is planned to roughly the length of its narration, so the edit rarely has to speed a
//   clip up or hold a frozen frame.
// The randomness is seeded, so a re-shoot moves the same way.
const fs = require('fs');
const path = require('path');
const repo = path.resolve(__dirname, '../../..');
const { chromium } = require(path.join(repo, 'databridge/apps/internal_brain_web/node_modules/playwright'));

const WEB = process.env.IB_WEB || 'http://localhost:3100';
const WALNUT = process.env.WALNUT_WEB || 'http://localhost:3300';
const OUT = path.join(repo, 'submission/05_video/clips');
// The page is laid out at 1536x864 (a common laptop size) and assemble.py scales it to 1080p, so text
// is about 25% larger on video than on a full-HD desktop.
const SIZE = { width: 1536, height: 864 };
const VIEW = SIZE;
const S1 = "What's the status of the database migration project and were there any blockers raised in Slack last week?";
const pause = (p, s) => p.waitForTimeout(s * 1000);

// seeded randomness (mulberry32) so re-shoots are repeatable
let seed = 20261015;
function rnd() {
  seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const jitter = (a, b) => a + (b - a) * rnd();

// A drawn cursor (headless recordings have none) that follows real mouse events, remembers its
// position across page loads, and shows a soft ring on each click.
function installCursor() {
  const KEY = '__cursor_pos';
  const add = () => {
    if (document.getElementById('__cur')) return;
    const c = document.createElement('div');
    c.id = '__cur';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M5 3 L5 21 L10 16.5 L13.4 23.5 L16.6 22 L13.2 15 L20 15 Z" fill="#ffffff" stroke="#0b1020" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: 'fixed', left: '0', top: '0', zIndex: '2147483647', pointerEvents: 'none',
      filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.45))', willChange: 'transform' });
    const ring = document.createElement('div');
    ring.id = '__ring';
    Object.assign(ring.style, { position: 'fixed', left: '0', top: '0', width: '34px', height: '34px', borderRadius: '50%',
      border: '2px solid rgba(125,170,255,.95)', zIndex: '2147483646', pointerEvents: 'none', opacity: '0' });
    document.documentElement.appendChild(ring);
    document.documentElement.appendChild(c);
    const put = (x, y) => { c.style.transform = `translate(${x - 5}px, ${y - 3}px)`; };
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) {}
    put(saved ? saved[0] : 770, saved ? saved[1] : 500);
    addEventListener('mousemove', (e) => {
      put(e.clientX, e.clientY);
      try { sessionStorage.setItem(KEY, JSON.stringify([e.clientX, e.clientY])); } catch (err) {}
    }, true);
    addEventListener('mousedown', (e) => {
      ring.style.transition = 'none';
      ring.style.transform = `translate(${e.clientX - 17}px, ${e.clientY - 17}px) scale(.4)`;
      ring.style.opacity = '1';
      requestAnimationFrame(() => requestAnimationFrame(() => {
        ring.style.transition = 'transform .45s ease-out, opacity .45s ease-out';
        ring.style.transform = `translate(${e.clientX - 17}px, ${e.clientY - 17}px) scale(1.25)`;
        ring.style.opacity = '0';
      }));
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
}

// ---- human-like input -----------------------------------------------------------------------
const POS = new WeakMap();
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

async function moveTo(page, x, y) {
  const [x0, y0] = POS.get(page) || [770, 500];
  const dist = Math.hypot(x - x0, y - y0);
  if (dist < 2) return;
  const steps = Math.max(12, Math.min(48, Math.round(dist / 22)));
  const bow = jitter(-0.12, 0.12) * dist;                       // a slight arc, as a wrist makes
  const nx = -(y - y0) / dist, ny = (x - x0) / dist;
  for (let i = 1; i <= steps; i++) {
    const t = ease(i / steps);
    const arc = Math.sin(Math.PI * t) * bow;
    await page.mouse.move(x0 + (x - x0) * t + nx * arc, y0 + (y - y0) * t + ny * arc);
    await page.waitForTimeout(9);
  }
  POS.set(page, [x, y]);
}

async function bringIntoView(page, loc) {
  await loc.waitFor({ state: 'attached', timeout: 30000 });
  const b = await loc.boundingBox();
  if (!b || (b.y >= 0 && b.y + Math.min(b.height, 300) <= VIEW.height - 20)) return;
  // a smooth scroll of whichever area holds the element (the page or an inner panel); not the wheel,
  // which would zoom a graph canvas under the pointer
  await loc.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'smooth' })).catch(() => {});
  await page.waitForTimeout(650);
}

async function pointAt(page, loc, { dx = 0.5, spread = 0.18 } = {}) {
  await bringIntoView(page, loc);
  const b = await loc.boundingBox();
  if (!b) return null;
  const x = b.x + b.width * Math.min(0.92, Math.max(0.08, dx + jitter(-spread, spread)));
  const y = b.y + Math.min(b.height, 60) * jitter(0.38, 0.62);
  await moveTo(page, x, y);
  return [x, y];
}

async function hover(page, loc, dwell = 1.2) {
  if (!(await loc.count())) return;
  await pointAt(page, loc.first());
  await pause(page, dwell);
}

async function click(page, loc, after = 0.4) {
  loc = loc.first();
  await pointAt(page, loc);
  await page.waitForTimeout(jitter(110, 230));
  await page.mouse.down();
  await page.waitForTimeout(jitter(55, 95));
  await page.mouse.up();
  await pause(page, after);
}

async function type(page, text) {
  for (const ch of text) {
    await page.keyboard.type(ch);
    let d = jitter(22, 62);
    if (ch === ' ') d += jitter(10, 55);
    if (',.?'.includes(ch)) d += jitter(90, 180);
    if (rnd() < 0.025) d += jitter(180, 340);                    // the odd hesitation
    await page.waitForTimeout(d);
  }
}

/** A slow pass of the pointer across a block of text, the way people follow what they read. */
async function read(page, loc, seconds = 2) {
  if (!(await loc.count())) return;
  loc = loc.first();
  await bringIntoView(page, loc);
  const b = await loc.boundingBox();
  if (!b) return;
  const lines = Math.max(1, Math.min(3, Math.round(seconds / 1.6)));
  for (let i = 0; i < lines; i++) {
    const y = b.y + Math.min(b.height, 260) * ((i + 0.6) / (lines + 0.4));
    await moveTo(page, b.x + b.width * jitter(0.06, 0.14), y);
    await moveTo(page, b.x + b.width * jitter(0.55, 0.8), y + jitter(-4, 4));
    await pause(page, seconds / lines - 0.5);
  }
}

// ---- on-screen labels -----------------------------------------------------------------------
const MARKS = new WeakMap();
function mark(page, text, seconds = 3.5) {
  const m = MARKS.get(page);
  if (m) m.list.push({ at: +((Date.now() - m.t0) / 1000).toFixed(2), seconds, text });
}
/** Wait for something slow (a page load, an answer); a wait longer than ~1.5 s is marked as a cut,
 *  keeping its first half-second and last 0.4 s, so the edit can trim the dead time like a person
 *  editing a screen recording would. */
async function slow(page, promise) {
  const m = MARKS.get(page);
  const a = Date.now();
  const r = await promise;
  const b = Date.now();
  if (m && b - a > 1500) m.list.push({ cut: [+((a - m.t0) / 1000 + 0.5).toFixed(2), +((b - m.t0) / 1000 - 0.4).toFixed(2)] });
  return r;
}

// ---- app steps ------------------------------------------------------------------------------
async function login(page, persona) {
  const r = await page.request.post(`${WEB}/api/ib/auth/demo-login`, { data: { persona } });
  if (!r.ok()) throw new Error(`login ${persona}: ${r.status()}`);
}
async function reset(browser) {                       // its own context: no stray recording
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await login(p, 'admin.it');
  await p.request.post(`${WEB}/api/ib/sim/reset`);
  await ctx.close();
}
async function waitAnswer(page) {
  await slow(page, page.getByTestId('answer').or(page.getByTestId('answer-error')).first().waitFor({ timeout: 60000 }));
}
async function ask(page, q) {
  const box = page.getByLabel('Your question');
  await click(page, box, 0.25);
  await page.keyboard.press('Control+A'); await page.keyboard.press('Backspace');
  await type(page, q);
  await pause(page, jitter(0.35, 0.6));
  await click(page, page.getByRole('button', { name: 'Ask', exact: true }), 0);
  await waitAnswer(page);
  await pause(page, 0.6);
}
async function suggest(page, text) {                 // click one of the "Suggested for ..." chips
  await click(page, page.getByRole('button', { name: text }), 0);
  await waitAnswer(page);
  await pause(page, 0.6);
}
async function goto(page, url, settle = 1.2) {
  await slow(page, page.goto(url).then(() => page.waitForLoadState('networkidle').catch(() => {})));
  await pause(page, settle);
}

// Each scene is sized to its narration (VIDEO_SCRIPT.md); the target length is in the comment.
const SCENES = {
  async '01_login'(page) {                                         // ~15 s
    await goto(page, `${WEB}/login`, 1.5);
    mark(page, 'Synthetic company "Company A": six teams, four platforms');
    for (const p of ['jdoe', 'ken.ext', 'maya.compliance']) await hover(page, page.locator(`[data-persona="${p}"]`), jitter(0.9, 1.3));
    await hover(page, page.locator('[data-persona="jdoe"]'), 0.6);
    await click(page, page.locator('[data-persona="jdoe"]'), 0);
    await page.waitForURL((u) => !u.pathname.startsWith('/login'));
    await pause(page, 2);
  },
  async '03_ask_jdoe'(page) {                                      // ~32 s
    await login(page, 'jdoe'); await goto(page, WEB, 1.2);
    mark(page, 'Jordan · backend engineer');
    await ask(page, S1);
    await read(page, page.getByTestId('answer'), 4);
    mark(page, 'Every sentence links to its source');
    for (const c of (await page.getByTestId('citation-chip').all()).slice(0, 4)) await hover(page, c, jitter(0.7, 1.0));
    mark(page, 'Only sources Jordan can open, each re-checked live');
    await hover(page, page.getByTestId('checks-strip'), 3.5);
    await hover(page, page.getByTestId('sources-panel'), 3);
  },
  async '04_map'(page) {                                           // ~18 s
    await login(page, 'jdoe'); await goto(page, `${WEB}/map`, 0);
    await slow(page, page.getByTestId('map-totals').waitFor({ timeout: 60000 })); await pause(page, 1);
    mark(page, "Jordan's map: everything he can open");
    await hover(page, page.getByTestId('map-totals'), 2);
    const place = page.getByTestId('map-place').first();
    if (await place.count()) { await click(page, place, 3); }
    await login(page, 'ken.ext'); await goto(page, `${WEB}/map`, 0);
    await slow(page, page.getByTestId('map-totals').waitFor({ timeout: 60000 })); await pause(page, 1);
    mark(page, "Ken's map (contractor): no \"hidden\" count anywhere");
    await hover(page, page.getByTestId('map-totals'), 3);
  },
  async '05_compare'(page) {                                       // ~18 s
    await login(page, 'jdoe'); await goto(page, `${WEB}/compare`, 1.5);
    mark(page, 'Same question, Jordan and Ken side by side');
    await click(page, page.getByRole('button', { name: 'Ask for both' }), 0);
    await slow(page, page.getByTestId('compare-results').waitFor({ timeout: 90000 }));
    await pause(page, 1);
    const cols = page.getByTestId('compare-results').locator(':scope > *');
    if (await cols.count()) { await read(page, cols.nth(0), 4); await read(page, cols.nth(1), 3); }
    await hover(page, page.getByTestId('compare-why'), 2);
  },
  async '10_tour'(page) {                                          // ~12 s: three steps of the tour
    await login(page, 'jdoe'); await goto(page, WEB, 1);
    await click(page, page.getByRole('button', { name: /tour/i }), 2);
    mark(page, 'A guided tour for anyone new');
    for (let i = 0; i < 2; i++) {
      const tour = page.getByTestId('tour');
      if (!(await tour.count())) break;
      await read(page, tour, 1.6);
      await click(page, tour.getByRole('button').last(), 0);
      await page.waitForLoadState('networkidle').catch(() => {});
      await pause(page, 1.6);
    }
  },
  async '06_restricted_vs_nonexistent'(page) {                     // ~18 s
    await login(page, 'ken.ext'); await goto(page, WEB, 1);
    mark(page, 'Ken asks about a real, restricted report');
    await suggest(page, 'Show me the security incident report from the Q3 breach.');
    await read(page, page.getByTestId('answer'), 1.6);
    mark(page, '…and about one that does not exist');
    await ask(page, 'Show me the security incident report from the Q7 breach.');
    await read(page, page.getByTestId('answer'), 2);
  },
  async '07_fintech'(page) {                                       // ~28 s
    await login(page, 'amy.am'); await goto(page, WEB, 1);
    mark(page, 'Amy · account manager (no FinCrime access)');
    await suggest(page, "Why has Velquarry Trading's account been restricted, and what should I tell the customer?");
    await read(page, page.getByTestId('answer'), 4);
    mark(page, 'A customer that does not exist: the same shape of answer');
    await suggest(page, "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?");
    await read(page, page.getByTestId('answer'), 3);
    await login(page, 'leo.ops'); await goto(page, WEB, 0.8);
    mark(page, 'Offer question: Indonesia gets its own, approved answer');
    await suggest(page, 'Can we offer multi-currency accounts to an Indonesian SME?');
    await read(page, page.getByTestId('answer'), 3.5);
  },
  async '08_revocation_freshness'(page) {                          // ~26 s
    await login(page, 'admin.it'); await goto(page, `${WEB}/sim`, 1);
    mark(page, 'IT removes Jordan from #db-migration');
    await click(page, page.getByRole('button', { name: /remove jdoe|revoke/i }), 1.5);
    await login(page, 'jdoe'); await goto(page, WEB, 0.6);
    mark(page, "Jordan's next answer no longer uses that channel");
    if (await page.getByRole('button', { name: S1, exact: true }).count()) await suggest(page, S1);
    else await ask(page, S1);
    await hover(page, page.getByTestId('sources-panel'), 2.5);
    await login(page, 'admin.it'); await goto(page, `${WEB}/sim`, 0.6);
    mark(page, 'The runbook owner publishes a new version');
    await click(page, page.getByRole('button', { name: /runbook/i }), 1.2);
    await login(page, 'priya.sre'); await goto(page, WEB, 0.6);
    mark(page, 'Priya sees the new version straight away');
    await ask(page, "What's the latest runbook for the payment-service incident?");
    await read(page, page.getByTestId('answer'), 2.5);
  },
  async '09_audit'(page) {                                         // ~36 s
    await login(page, 'maya.compliance'); await goto(page, `${WEB}/audit`, 1.2);
    mark(page, 'Maya · compliance: asks the audit log in plain English');
    const box = page.getByRole('textbox').first();
    await click(page, box, 0.2);
    await page.keyboard.press('Control+A'); await page.keyboard.press('Backspace');
    await type(page, 'Everything jdoe accessed in the payment-gateway space, last 30 days');
    await pause(page, 0.4);
    await page.keyboard.press('Enter');
    await slow(page, page.getByTestId('audit-table').waitFor({ timeout: 30000 }).catch(() => {}));
    await pause(page, 1);
    await hover(page, page.getByTestId('parsed-filter'), 2);
    await read(page, page.getByTestId('audit-table'), 3);
    const details = page.getByRole('button', { name: /Details for request/ }).first();
    if (await details.count()) {
      await click(page, details, 1.5);
      const reason = page.getByLabel(/reason/i).first();
      if (await reason.count()) {
        mark(page, 'Reading an answer needs a reason, and is logged too');
        await click(page, reason, 0.2);
        await type(page, 'Quarterly access review');
        await click(page, page.getByRole('button', { name: /reveal/i }), 2.5);
      }
      await page.keyboard.press('Escape'); await pause(page, 0.6);
    }
    mark(page, 'Re-checks every record and every stored answer');
    await click(page, page.getByRole('button', { name: 'Verify audit chain' }), 1.5);
    await hover(page, page.getByTestId('verify-panel'), 3);
  },
  async '11_walnut_original'(page) {                               // ~24 s
    // the same steps the original screens' showcase check takes (apps/frontend/tests/showcase)
    await slow(page, page.goto(`${WALNUT}/graph`));
    const roster = page.locator('[data-testid=roster-jdoe]');
    if (await roster.count().catch(() => 0) || await roster.waitFor({ timeout: 8000 }).then(() => true, () => false)) {
      await pause(page, 0.6); await click(page, roster, 0.5);
    }
    await slow(page, page.waitForSelector("[data-testid^='rf__node-']", { timeout: 30000 }));
    await pause(page, 1.2);
    mark(page, "Walnut's original screens: Company A's knowledge graph");
    for (const n of (await page.locator("[data-testid^='rf__node-']").all()).slice(2, 5)) await hover(page, n, 0.5);
    const search = page.locator('[data-testid=graph-seed-search] input');
    if (await search.count()) {
      await click(page, search, 0.2); await type(page, 'DB Migration Plan'); await page.keyboard.press('Enter');
      const node = page.locator("[data-testid='rf__node-knowledge_page:cf-1001']");
      await slow(page, node.waitFor({ timeout: 20000 }).catch(() => {}));
      await pause(page, 1.5);
      if (await node.count()) {
        await click(page, node, 0);
        await slow(page, page.locator('[data-testid=graph-attr-row]').first().waitFor({ timeout: 15000 }).catch(() => {}));
        await read(page, page.locator('[data-testid=graph-attr-row]').first(), 2);
      }
    }
    await slow(page, page.goto(`${WALNUT}/brain`).then(() => page.waitForFunction(
      () => /^[1-9]/.test(document.querySelector('[data-testid=time-slider-count]')?.textContent || '')
        && !/Loading the brain/i.test(document.body.innerText),
      null, { timeout: 60000 }).catch(() => {})));
    await pause(page, 1.5);
    const dismiss = page.locator('button[aria-label=Dismiss]');
    if (await dismiss.count()) await click(page, dismiss, 0.3);
    const slider = page.locator('[data-testid=time-slider]');
    const box = (await slider.count()) ? await slider.boundingBox() : null;
    if (box) {                                                     // drag the time slider back, then forward
      mark(page, 'Rewind the company brain in time');
      const y = box.y + box.height / 2;
      // few, larger steps: every step re-draws the whole brain
      const drag = async (from, to) => {
        for (let i = 1; i <= 5; i++) {
          await page.mouse.move(box.x + box.width * (from + (to - from) * ease(i / 5)), y);
          await pause(page, 0.12);
        }
      };
      await moveTo(page, box.x + box.width - 6, y); await page.mouse.down();
      await drag(0.99, 0.55); await pause(page, 1.4);
      await drag(0.55, 0.99); await page.mouse.up(); await pause(page, 0.8);
      POS.set(page, [box.x + box.width * 0.99, y]);
    }
    await slow(page, page.goto(`${WALNUT}/evidence?entity=${encodeURIComponent('knowledge_page:cf-1001')}`).then(() =>
      page.locator('[data-testid=evidence-attribute]').first().waitFor({ timeout: 30000 }).catch(() => {})));
    await pause(page, 0.8);
    mark(page, 'Every value carries a pointer back to its source');
    await read(page, page.locator('[data-testid=evidence-attribute]').first(), 3);
    await goto(page, `${WALNUT}/ontology`, 2.5);
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
    seed = 20261015 + name.length * 7919;                 // same moves on every re-shoot of a scene
    const ctx = await browser.newContext({ viewport: VIEW, recordVideo: { dir: OUT, size: SIZE },
                                           colorScheme: 'dark', deviceScaleFactor: 1 });
    // the one-time tour offer is shown in its own scene (the tour), not in every clip
    await ctx.addInitScript(() => { try { localStorage.setItem('ib-tour-offer', 'dismissed'); } catch (e) {} });
    await ctx.addInitScript(installCursor);
    if (!name.startsWith('11_')) await reset(browser);
    const page = await ctx.newPage();
    const t0 = Date.now();
    MARKS.set(page, { t0, list: [] });
    POS.set(page, [770, 500]);
    try { await SCENES[name](page); console.log(`ok   ${name} ${((Date.now() - t0) / 1000).toFixed(1)}s`); }
    catch (e) { console.log(`FAIL ${name}: ${e.message.split('\n')[0]}`); }
    const vid = page.video();
    await ctx.close();
    if (vid) fs.renameSync(await vid.path(), path.join(OUT, `${name}.webm`));
    fs.writeFileSync(path.join(OUT, `${name}.marks.json`), JSON.stringify(MARKS.get(page).list, null, 1));
  }
  if (needsBrain) await reset(browser);
  await browser.close();
})();
