// Record the finals demo video's screen scenes, one clip per scene, against the RUNNING local stack
// (web app http://127.0.0.1:3100 -> Internal Brain :8100 -> mock platforms; engine gateway :8300).
//
//   node docs/finals/video/record_finals.cjs                 all scenes, then the cards
//   node docs/finals/video/record_finals.cjs 08_audit        one scene (any order)
//   node docs/finals/video/record_finals.cjs cards           only the still cards and slides
//   node docs/finals/video/record_finals.cjs --self-test     check the take handling (no browser, no stack)
//
// Every take is kept in its own folder, submission/finals_video/clips/takes/<scene>/<take>/: the video
// (<scene>.webm, or <scene>.FAILED.webm) and take.json, which holds the marks: when each on-screen label
// should appear, and which slow waits to cut, in seconds from the clip's start (assemble_finals.py applies
// both). Only a take that finished and passes checkTake (every label of the scene, one start mark,
// durations > 0, the video on disk) becomes the current good take: clips/<scene>.marks.json, replaced in
// one rename, names that take's video and carries its marks. A failed or rejected take never touches it,
// and the process exits 1. Cards and slides land in submission/finals_video/cards/*.png at 1920x1080.
// Nothing is faked: each clip is the real app answering, live.
//
// Adapted from docs/tencent/video/record_scenes.cjs (the first video). The same rules make it look like a
// person at the keyboard (VIDEO_SCRIPT_FINALS.md, "Pacing"):
// - a drawn cursor that moves on eased, slightly curved paths and shows a ring on each click;
// - typing with an uneven rhythm (longer after spaces and punctuation, the odd short hesitation);
// - smooth scrolling in small steps, never a jump;
// - each scene is planned to about the length of its narration.
// The randomness is seeded per scene, so a re-shoot moves the same way.
//
// The app is shown in its normal dark theme (not the Stage theme). Scenes sign in with the demo
// sign-in, exactly as the sign-in page does; they never reset the simulator (other people may be using
// the same stack), and the tamper demo only ever touches a sandbox copy of the ledger.
const fs = require('fs');
const path = require('path');
const repo = path.resolve(__dirname, '../../..');
const webDir = path.join(repo, 'databridge/apps/internal_brain_web');
let chromium;                                         // loaded when recording, not for --self-test

const WEB = process.env.IB_WEB || 'http://127.0.0.1:3100';
const OUTV = path.join(repo, 'submission/finals_video');
const OUT = path.join(OUTV, 'clips');
const CARDS = path.join(OUTV, 'cards');
// Laid out at 1536x864 (a common laptop size); assemble_finals.py scales it to 1080p, so text is about
// 25% larger on video than on a full-HD desktop.
const SIZE = { width: 1536, height: 864 };
const VIEW = SIZE;
const QV = "Why has Velquarry Trading's account been restricted, and what should I tell the customer?";
const QB = "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?";
// Facts that are not read from the live app: docs/finals/video/video_constants.json (CodeBuddy session
// count and so on). Test counts and eval numbers are NOT kept here: they come from
// databridge/apps/internal_brain_web/public/eval_summary.json (assemble_finals.py reads them).
const CONST = JSON.parse(fs.readFileSync(path.join(__dirname, 'video_constants.json'), 'utf8'));
const NUM_WORD = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];
const cap = (w) => w.charAt(0).toUpperCase() + w.slice(1);
const pause = (p, s) => p.waitForTimeout(s * 1000);

// seeded randomness (mulberry32) so re-shoots are repeatable
let seed = 20261103;
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
  if (!b || (b.y >= 60 && b.y + Math.min(b.height, 300) <= VIEW.height - 20)) return;
  // a smooth scroll of whichever area holds the element (the page or an inner panel); not the wheel,
  // which would zoom a graph canvas under the pointer
  await loc.evaluate((el) => el.scrollIntoView({ block: 'center', behavior: 'smooth' })).catch(() => {});
  await page.waitForTimeout(700);
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

async function hover(page, loc, dwell = 1.2, opts) {
  if (!(await loc.count())) return;
  await pointAt(page, loc.first(), opts);
  await pause(page, dwell);
}

async function click(page, loc, after = 0.4, opts) {
  loc = loc.first();
  await pointAt(page, loc, opts);
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
  const top = Math.max(b.y, 70), h = Math.min(b.y + b.height, VIEW.height - 30) - top;
  for (let i = 0; i < lines; i++) {
    const y = top + Math.min(h, 260) * ((i + 0.6) / (lines + 0.4));
    await moveTo(page, b.x + b.width * jitter(0.06, 0.14), y);
    await moveTo(page, b.x + b.width * jitter(0.55, 0.8), y + jitter(-4, 4));
    await pause(page, Math.max(0.2, seconds / lines - 0.5));
  }
}

/** Smooth scroll of the page (or the app's main scroll area) by dy pixels, in small eased steps. */
async function scrollBy(page, dy, seconds = 0.9) {
  await page.evaluate(([dy, ms]) => new Promise((done) => {
    const cands = [document.scrollingElement, ...document.querySelectorAll('main, [data-scroll-root], .overflow-auto, .overflow-y-auto')];
    const el = cands.find((e) => e && e.scrollHeight > e.clientHeight + 4) || document.scrollingElement;
    const from = el.scrollTop, t0 = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - t0) / ms);
      el.scrollTop = from + dy * (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
      if (t < 1) requestAnimationFrame(step); else done();
    };
    requestAnimationFrame(step);
  }), [dy, seconds * 1000]);
  await pause(page, 0.25);
}

// ---- on-screen labels -----------------------------------------------------------------------
const MARKS = new WeakMap();
function mark(page, text, seconds = 3.5) {
  const m = MARKS.get(page);
  if (m) m.list.push({ at: +((Date.now() - m.t0) / 1000).toFixed(2), seconds, text });
}
/** Wait for something slow (a page load, an answer); a wait longer than ~1.5 s is marked as a cut,
 *  keeping its first half-second and last 0.4 s, so the edit can trim the dead time. */
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
  for (let i = 0; i < 24; i++) {                      // the IB may be restarted briefly: retry ~2 min
    const r = await page.request.post(`${WEB}/api/ib/auth/demo-login`, { data: { persona }, headers: { origin: WEB } }).catch(() => null);
    if (r && r.ok()) return;
    await pause(page, 5);
  }
  throw new Error(`login ${persona} failed`);
}
async function goto(page, url, settle = 1.0) {
  const m = MARKS.get(page);
  const load = page.goto(url).then(() => page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {}));
  if (m && !m.started) {                    // the clip starts once the first page is up: no blank frames
    await load;
    // wait until the app shell has checked the session and drawn the page
    await page.waitForFunction(() => !/Checking your session/i.test(document.body.innerText) && document.body.innerText.length > 200,
      null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(250);
    m.started = true;
    m.list.push({ start: +((Date.now() - m.t0) / 1000 + 0.15).toFixed(2) });
  } else {
    await slow(page, load);
  }
  await pause(page, settle);
}
async function waitAnswer(page) {
  await slow(page, page.getByTestId('answer').or(page.getByTestId('answer-error')).first().waitFor({ timeout: 90000 }));
}
/** Both Compare columns finished (an answer or a refusal in each). */
async function waitCompare(page) {
  await slow(page, page.waitForFunction(() => {
    const cols = [...document.querySelectorAll('[data-compare-col]')];
    return cols.length === 2 && cols.every((c) => c.getAttribute('aria-busy') !== 'true'
      && c.querySelector('[data-testid=answer-text], [data-testid=refusal-card], [data-testid=compare-error]'));
  }, null, { timeout: 120000 }));
}
const col = (page, i) => page.locator('[data-compare-col]').nth(i);

// Each scene is sized to its narration (VIDEO_SCRIPT_FINALS.md); the target length is in the comment.
// `mode` is the app mode the scene starts in ('user' or 'dev'); `who` the persona signed in first.
// Round 2b finding 7: the Hy3 overlay follows what this backend's /ib/status reports, never a fixed
// string. Live = the active chain includes a hy3 model on Tencent TokenHub (configured and switched on;
// the status does not prove a call succeeded). Unknown (status unreadable) gets a neutral line.
// FINALS_HY3=1 or 0, when set, must agree with the status, or the take is refused.
async function hy3Live(page) {
  const r = await page.request.get(`${WEB}/api/ib/ib/status`).catch(() => null);
  const st = r && r.ok() ? await r.json().catch(() => null) : null;
  const m = st && st.models;
  const live = m ? !!(m.active && m.provider === 'tencent-tokenhub' &&
    (m.active_chain || []).some((x) => /^hy3/i.test(String(x)))) : null;
  const want = process.env.FINALS_HY3;
  if ((want === '1' || want === '0') && live !== (want === '1')) {
    throw new Error(`FINALS_HY3=${want} but /ib/status says Hy3 live=${live}; not recording 13_hy3_status`);
  }
  return live;
}
const hy3Mark = (live) => (live === true
  ? 'Hy3 switched on via TokenHub: answers still pass the same source checks'
  : live === false
    ? 'Hy3 not switched on yet: answers are quoted from the sources'
    : 'The model chain as this backend reports it');

const SCENES = {
  '02_compare_amy_rachel': { who: 'amy.am', mode: 'user', async run(page) {               // ~40 s
    await goto(page, `${WEB}/compare?a=amy.am&b=mlro.tan`, 1.0);
    mark(page, "Aspire's question · Amy (account manager) and Rachel (MLRO)", 4.5);
    await hover(page, page.locator('#cmp-a'), 0.7);
    await hover(page, page.locator('#cmp-b'), 0.7);
    const q = page.locator('#cmp-q');
    await click(page, q, 0.25);
    await page.keyboard.press('Control+A'); await page.keyboard.press('Backspace');
    await type(page, QV);
    await pause(page, jitter(0.4, 0.7));
    await click(page, page.getByRole('button', { name: 'Ask both' }), 0);
    await waitCompare(page);
    await pause(page, 0.6);
    await scrollBy(page, 430, 1.1);
    mark(page, 'Amy · account manager: the approved script, cited, nothing else', 4.5);
    await read(page, col(page, 0).getByTestId('answer-text'), 7);
    mark(page, 'Rachel · MLRO: the case itself', 4);
    await read(page, col(page, 1).getByTestId('answer-text'), 7.5);
    await hover(page, col(page, 1).getByTestId('citations').or(col(page, 1).getByTestId('source-card')).first(), 2.5, { dx: 0.3 });
    await hover(page, col(page, 0).getByTestId('ai-note'), 1.2);
    await hover(page, col(page, 0).getByTestId('answer-text'), 3.5, { dx: 0.4 });
  } },
  '03_ken_identical': { who: 'ken.ext', mode: 'user', async run(page) {                  // ~24 s
    await goto(page, `${WEB}/compare?mode=person&p=ken.ext&q1=${encodeURIComponent(QV)}&q2=${encodeURIComponent(QB)}`, 1.0);
    mark(page, 'Ken · outside contractor: a real customer vs a made-up one', 4.5);
    await read(page, page.locator('#cmp-q1'), 1.6);
    await read(page, page.locator('#cmp-q2'), 1.6);
    await click(page, page.getByRole('button', { name: 'Ask both' }), 0);
    await waitCompare(page);
    await pause(page, 0.5);
    await scrollBy(page, 430, 1.1);
    await read(page, col(page, 0).getByTestId('refusal-card'), 3);
    await read(page, col(page, 1).getByTestId('refusal-card'), 3);
    mark(page, 'Identical, word for word: restricted looks like nothing', 4.5);
    await hover(page, page.getByTestId('identical-marker'), 5);
  } },
  '04_source_live': { who: 'mlro.tan', mode: 'user', async run(page) {                   // ~24 s
    await goto(page, WEB, 0.8);
    mark(page, "Rachel · MLRO: her own screen", 3.5);
    const starter = page.getByTestId('starter').filter({ hasText: 'Velquarry' });
    await hover(page, starter, 0.6);
    await click(page, starter, 0);
    await waitAnswer(page);
    await pause(page, 0.5);
    await read(page, page.getByTestId('answer-text'), 3.5);
    const chips = page.getByTestId('answer').getByTestId('citation-chip');
    const n = await chips.count();
    await click(page, chips.nth(Math.min(1, Math.max(0, n - 1))), 0);
    await slow(page, page.getByTestId('sources-sheet').waitFor({ timeout: 15000 }).catch(() => {}));
    await pause(page, 0.6);
    mark(page, 'The exact passage, its version, checked live seconds ago', 4.5);
    await hover(page, page.getByTestId('sheet-cited').or(page.getByTestId('sheet-highlight')).first(), 1.5);
    await read(page, page.getByTestId('sheet-highlight').first(), 3);
    await hover(page, page.getByText('Checked live at', { exact: false }).first(), 1.8);
    await page.keyboard.press('Escape'); await pause(page, 0.8);
    const lang = page.getByRole('button', { name: /^Language:/ });
    await click(page, lang, 0.5);
    await click(page, page.getByRole('menuitemradio', { name: '简体中文' }), 0.2);
    mark(page, '简体中文: the user screens, one click', 3);
    await pause(page, 2.2);
    await click(page, page.getByRole('button', { name: /^(Language|语言):/ }), 0.5);
    await click(page, page.getByRole('menuitemradio', { name: 'English' }), 1.0);
  } },
  '05_journey': { who: 'maya.compliance', mode: 'user', async run(page) {                // ~30 s
    await goto(page, WEB, 0.8);
    mark(page, 'Maya · compliance officer: Developer mode', 3.5);
    await click(page, page.getByTestId('mode-switch').getByText('Developer'), 0);
    await slow(page, page.waitForURL((u) => u.pathname.startsWith('/dev'), { timeout: 20000 }).catch(() => {}));
    await pause(page, 1.2);
    await click(page, page.getByTestId('side-nav').getByRole('link', { name: 'Journey' }), 0);
    await slow(page, page.getByTestId('recent-requests').waitFor({ timeout: 30000 }));
    await pause(page, 0.8);
    const row = page.getByTestId('recent-requests').locator('tr, a, [role=row]').filter({ hasText: 'mlro.tan' }).filter({ hasText: 'restricted' }).first();
    await hover(page, row, 0.8);
    await click(page, row, 0);
    await slow(page, page.getByTestId('journey-waterfall').or(page.getByTestId('waterfall')).first().waitFor({ timeout: 30000 }));
    await pause(page, 0.8);
    mark(page, "Rachel's request: every stage, real milliseconds", 4);
    await read(page, page.getByTestId('waterfall').or(page.getByTestId('journey-waterfall')).first(), 5);
    mark(page, 'Three sealed, chained records, before she saw a word', 4);
    await hover(page, page.getByTestId('ledger-strip').or(page.getByText('Ledger seals')).first(), 3);
    await scrollBy(page, 600, 1.2);
    mark(page, 'Every document considered: the verdict and the reason, never the answer', 4.5);
    await read(page, page.getByText('Considered → Gate 2').locator('xpath=ancestor::section[1]').or(page.getByTestId('journey-compliance')).first(), 6);
  } },
  '06_graph': { who: 'jdoe', mode: 'dev', async run(page) {                               // ~26 s
    await goto(page, `${WEB}/dev/graph`, 0);
    await slow(page, page.getByTestId('graph-legend').waitFor({ timeout: 90000 }));
    await pause(page, 1.2);
    mark(page, 'Jordan · backend engineer: everything he can open', 4);
    await hover(page, page.getByText(/\d[\d,]* items|items · /).first(), 2);   // the count is whatever the world has now
    await hover(page, page.getByTestId('graph-legend'), 1.5);
    const search = page.getByTestId('graph-main-search').locator('input').or(page.getByPlaceholder(/Search titles/)).first();
    if (await search.count()) {
      await click(page, search, 0.2);
      await type(page, 'migration');
      await pause(page, 0.8);
      const opt = page.getByTestId('graph-search-option').first();
      if (await opt.waitFor({ timeout: 6000 }).then(() => true, () => false)) {
        await click(page, opt, 0);
        await slow(page, page.getByTestId('graph-main-panel').waitFor({ timeout: 15000 }).catch(() => {}));
        await pause(page, 0.6);
        await read(page, page.getByTestId('graph-passage').or(page.getByTestId('graph-main-panel')).first(), 3);
      }
      await page.keyboard.press('Escape').catch(() => {});
    }
    await click(page, page.getByRole('radio', { name: 'Compare two people' }).or(page.getByRole('tab', { name: 'Compare two people' })).or(page.getByText('Compare two people')).first(), 0);
    await slow(page, page.waitForFunction(() => /can open\s+\d+\s+items[\s\S]*can open\s+\d+\s+items/i.test(document.body.innerText)
      && !/Checking each item/i.test(document.body.innerText), null, { timeout: 120000 }).catch(() => {}));
    await pause(page, 1.5);
    mark(page, 'Ken, signed in separately: a small corner, no greyed-out boxes', 4.5);
    await hover(page, page.getByText(/Ken can open \d+/).first(), 2.2);
    await hover(page, page.getByText('SECOND PERSON', { exact: false }).first(), 2.5, { dx: 0.3 });
  } },
  // OPTIONAL, being built (30 Oct): Jordan posts a Slack note from /dev/graph?add=1, it appears in his
  // graph, and he asks about it and gets it back with a citation. The feature has landed (needs demo features
  // on, D-24; it WRITES a real note into the mock Slack of the stack); if the "add" form is not there the scene fails cleanly, is saved as
  // 06b_brain_grow.FAILED.webm, and assemble_finals.py leaves the optional segment out.
  '06b_brain_grow': { who: 'jdoe', mode: 'dev', async run(page) {                       // ~28 s
    const NOTE = 'Heads-up: the ledger cutover dry run moves to Thursday 9am, owner Priya.';
    await goto(page, `${WEB}/dev/graph?add=1`, 0);
    await slow(page, page.getByTestId('graph-legend').waitFor({ timeout: 90000 }));
    await pause(page, 1.0);
    // the Add sheet (components/sim/AddDocumentSheet.tsx, testId graph-main-addsheet): note textarea + 'Post to Slack'
    const box = page.getByTestId('graph-main-addsheet-text');
    if (!(await box.waitFor({ timeout: 10000 }).then(() => true, () => false))) throw new Error('the "add a Slack note" form is not in this build yet');
    mark(page, 'Jordan posts a Slack note: watch the brain grow', 4);
    await click(page, box, 0.2);
    await type(page, NOTE);
    await pause(page, 0.4);
    await click(page, page.getByTestId('graph-main-addsheet-post'), 0);
    await slow(page, page.getByText(/Posted to Slack/).first().waitFor({ timeout: 20000 }).catch(() => {}));
    await pause(page, 1.2);
    await page.keyboard.press('Escape'); await pause(page, 0.6);          // close the sheet before the mode switch
    await slow(page, page.waitForFunction(() => /cutover dry run/i.test(document.body.innerText), null, { timeout: 30000 }).catch(() => {}));
    await pause(page, 1.2);
    mark(page, 'The new message is in his graph, checked with Slack', 4);
    await hover(page, page.getByText(/cutover dry run/i).first(), 2.5);
    await click(page, page.getByTestId('mode-switch').getByText('User'), 0);
    await slow(page, page.waitForURL((u) => !u.pathname.startsWith('/dev'), { timeout: 20000 }).catch(() => {}));
    await pause(page, 0.8);
    const ask = page.getByRole('textbox').first();
    await click(page, ask, 0.2);
    await type(page, 'When is the ledger cutover dry run?');
    await page.keyboard.press('Enter');
    await waitAnswer(page);
    await pause(page, 0.5);
    mark(page, 'Asked straight away: answered from the new note, cited', 4);
    await read(page, page.getByTestId('answer-text'), 3);
    await hover(page, page.getByTestId('answer').getByTestId('citation-chip').first(), 2);
  } },
  '07_analytics': { who: 'maya.compliance', mode: 'dev', async run(page) {               // ~17 s
    await goto(page, `${WEB}/dev/analytics`, 0);
    await slow(page, page.getByTestId('analytics-kpis').waitFor({ timeout: 60000 }));
    await pause(page, 1);
    mark(page, 'Maya · analytics, computed from the verified ledger', 4);
    await read(page, page.getByTestId('analytics-kpis'), 5);
    await hover(page, page.getByTestId('replay-notice'), 1.6, { dx: 0.3 });
    await click(page, page.getByRole('radio', { name: 'Live only' }).or(page.getByRole('button', { name: 'Live only' })).or(page.getByText('Live only', { exact: true })).first(), 1.2);
    mark(page, 'Live only: seeded history left out', 3);
    await read(page, page.getByTestId('analytics-kpis'), 3.5);
  } },
  '08_audit': { who: 'maya.compliance', mode: 'dev', async run(page) {                   // ~40 s
    await goto(page, `${WEB}/dev/audit`, 0.8);
    mark(page, 'Maya · asks the audit log in plain English', 4);
    const box = page.locator('#nl-question');
    await click(page, box, 0.2);
    await type(page, 'Everything mlro.tan accessed in the last 7 days');
    await pause(page, 0.4);
    await click(page, page.getByRole('button', { name: 'Interpret' }), 0);
    await slow(page, page.getByTestId('parsed-filter').or(page.getByTestId('audit-parser')).first().waitFor({ timeout: 30000 }).catch(() => {}));
    await pause(page, 0.6);
    await hover(page, page.getByTestId('parsed-filter').or(page.getByTestId('audit-parser')).first(), 1.8);
    await read(page, page.getByTestId('audit-table'), 2.5);
    const verify = page.getByRole('button', { name: 'Verify audit chain' });
    await click(page, verify, 0);
    await slow(page, page.getByTestId('verify-status').waitFor({ timeout: 30000 }).catch(() => {}));
    await pause(page, 0.6);
    mark(page, 'Verify: every link of the hash chain re-checked', 3.5);
    await hover(page, page.getByTestId('verify-panel'), 2.5);
    const run = page.getByRole('button', { name: /Run the tamper demo on a copy|Run again/ });
    await click(page, run, 0);
    mark(page, 'Tamper demo: attacks a temporary copy, never the real log', 4);
    await slow(page, page.getByText('Your real audit log was not modified.').waitFor({ timeout: 90000 }));
    await pause(page, 0.6);
    await hover(page, page.getByText('Your real audit log was not modified.'), 1.8);
    const broken = page.getByText(/Chain broken at entry \d+/).first();
    await bringIntoView(page, broken);
    mark(page, "One decision changed 'forbidden' → 'verified': caught at that entry", 5);
    await hover(page, page.getByText('Edit one old record').first(), 1.5, { dx: 0.3 });
    await hover(page, broken, 2.5);
    await read(page, page.getByText('Tampering detected').first(), 2.5);
  } },
  '09_trust': { who: 'maya.compliance', mode: 'dev', async run(page) {                   // ~15 s
    await goto(page, `${WEB}/dev/trust`, 0.8);
    mark(page, 'Trust centre: seven controls, each proved by a named test', 4);
    const cards = page.getByTestId('trust-controls').locator(':scope > *');
    await read(page, cards.nth(0), 3);
    await hover(page, page.getByText('PROVED BY', { exact: false }).first(), 1.5);
    await read(page, cards.nth(2), 2);
    await scrollBy(page, 560, 1.4);
    await pause(page, 0.4);
    await read(page, cards.nth(4), 3);
  } },
  '10_engine': { who: 'jdoe', mode: 'dev', async run(page) {                             // ~26 s
    await goto(page, `${WEB}/dev/brain`, 0);
    await slow(page, page.getByTestId('engine-snapshot-banner').waitFor({ timeout: 60000 }));
    await pause(page, 1.2);
    mark(page, 'The Walnut engine: Company A as Jordan sees it', 4);
    await hover(page, page.getByText(/\d[\d,]*\s*entities/).first(), 2.2);
    await click(page, page.getByTestId('engine-tab-brain'), 0);
    await slow(page, page.waitForFunction(() => /\d+ of the \d+/.test(document.querySelector('[data-testid=engine-asof-count]')?.textContent || '')
      && document.querySelector('[data-testid=engine-time-slider] input[type=range]'), null, { timeout: 60000 }).catch(() => {}));
    await pause(page, 1.8);
    const slider = page.getByTestId('engine-time-slider').locator('input[type=range]');
    const box = (await slider.count()) ? await slider.boundingBox() : null;
    if (box) {                                                     // drag the time slider back, then forward
      mark(page, 'Rewind the company knowledge in time', 3.5);
      const y = box.y + box.height / 2;
      const drag = async (from, to) => {                           // few, larger steps: each re-draws
        for (let i = 1; i <= 6; i++) {
          await page.mouse.move(box.x + box.width * (from + (to - from) * ease(i / 6)), y);
          await pause(page, 0.14);
        }
      };
      await moveTo(page, box.x + box.width - 4, y); await page.mouse.down();
      await drag(0.995, 0.45); await pause(page, 1.6);
      await drag(0.45, 0.995); await page.mouse.up(); await pause(page, 0.8);
      POS.set(page, [box.x + box.width * 0.995, y]);
    }
    await click(page, page.getByTestId('engine-tab-provenance'), 0);
    await slow(page, page.getByTestId('engine-chain').waitFor({ timeout: 30000 }).catch(() => {}));
    await pause(page, 0.8);
    mark(page, 'One value traced to the exact row in the exact file', 4);
    await read(page, page.getByTestId('engine-chain'), 4.5);
  } },
  '13_hy3_status': { who: 'maya.compliance', mode: 'dev', async run(page) {              // ~13 s
    await goto(page, `${WEB}/dev`, 0);
    await slow(page, page.getByTestId('model-status').waitFor({ timeout: 60000 }));
    await pause(page, 0.8);
    const ms = page.getByTestId('model-status');
    await bringIntoView(page, ms);
    mark(page, 'Tencent Hy3 via TokenHub → DeepSeek → quoted fallback', 4.5);
    await read(page, ms.getByRole('list'), 4);
    mark(page, hy3Mark(await hy3Live(page)), 5);
    await hover(page, ms.locator('p').last(), 3);
    await hover(page, ms.getByText(/Extractive only|Live models/).first(), 2.5);
  } },
};

// ---- still cards (title, CodeBuddy) and the app's own slides -------------------------------------
function fontCss() {
  const f = path.join(webDir, 'node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2');
  const b64 = fs.readFileSync(f).toString('base64');
  return `@font-face{font-family:Inter;font-weight:100 900;src:url(data:font/woff2;base64,${b64}) format('woff2')}`;
}
const imgData = (p) => `data:image/png;base64,${fs.readFileSync(p).toString('base64')}`;
const BASE = `*{box-sizing:border-box;margin:0}html,body{width:1920px;height:1080px;background:#0a0b0d;color:#ededef;
  font-family:Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
  .wrap{position:absolute;inset:0;padding:110px 150px}
  .brand{display:flex;align-items:center;gap:14px;font-size:30px;font-weight:600;letter-spacing:-.01em}
  .brand i{font-style:normal;font-weight:400;color:#a1a1aa}
  .mark{width:46px;height:46px;border:2px solid #3f3f46;border-radius:11px;display:grid;place-items:center;font:600 20px Inter;color:#ededef;background:#16171b}
  .eyebrow{font-size:22px;letter-spacing:.18em;text-transform:uppercase;color:#8b8bf5;font-weight:600}
  .muted{color:#a1a1aa}.foot{position:absolute;left:150px;right:150px;bottom:90px;display:flex;justify-content:space-between;font-size:22px;color:#71717a}`;
function titleHtml() {
  return `<html><head><style>${fontCss()}${BASE}
  h1{font-size:132px;line-height:1.02;letter-spacing:-.035em;font-weight:700;margin-top:34px}
  .lead{font-size:34px;line-height:1.5;max-width:1380px;margin-top:44px}
  .pills{display:flex;gap:16px;margin-top:56px}.pills span{border:1px solid #3f3f46;border-radius:999px;padding:12px 26px;font-size:24px;color:#d4d4d8;background:#131417}
  </style></head><body><div class="wrap">
  <div class="brand"><div class="mark"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ededef" stroke-width="2.2" stroke-linecap="round"><path d="M6 5v14M18 5v14M6 12h12"/></svg></div>Walnut <i>DataBridge</i></div>
  <p class="eyebrow" style="margin-top:120px">FinTech track · Aspire · Finalist</p>
  <h1>Answers you<br>can prove.</h1>
  <p class="lead muted">One question across Confluence, Jira, Slack and Google Drive, answered only from what the asker can open. Every source checked live, every step sealed in an audit log.</p>
  <div class="pills"><span>Two permission gates before any model</span><span>Sealed, hash-chained audit</span><span>Built with Tencent CodeBuddy</span></div>
  </div><div class="foot"><span>Tencent Cloud AI CAN DO IT Hackathon Singapore 2026</span><span>Made-up demo data for "Company A"</span></div></body></html>`;
}
function codebuddyHtml() {
  const img = imgData(path.join(repo, 'submission/01_codebuddy/cb5_session_overview.png'));
  return `<html><head><style>${fontCss()}${BASE}
  h2{font-size:72px;letter-spacing:-.03em;font-weight:700;margin-top:26px;line-height:1.05}
  .grid{display:grid;grid-template-columns:600px 1fr;gap:70px;margin-top:56px;align-items:start}
  ul{list-style:none;padding:0;display:flex;flex-direction:column;gap:26px}
  li{font-size:32px;line-height:1.35;padding-left:34px;position:relative}
  li:before{content:'';position:absolute;left:0;top:16px;width:14px;height:14px;border-radius:4px;background:#6e6ef0}
  .shot{border:1px solid #2e2f35;border-radius:16px;overflow:hidden;box-shadow:0 30px 80px rgba(0,0,0,.55);height:640px}
  .shot img{width:1357px;margin-left:-204px;margin-top:-10px;display:block}
  .note{font-size:22px;margin-top:40px}
  </style></head><body><div class="wrap" style="padding-top:96px">
  <p class="eyebrow">Tencent CodeBuddy</p>
  <h2>How we built it</h2>
  <div class="grid"><div><ul>
   <li>Built the WorkBuddy connector</li>
   <li>Built the token tool and the model-chain tests</li>
   <li>Ran a security review of the gates and the audit log, and found real issues</li>
  </ul><p class="note muted">${cap(NUM_WORD[CONST.codebuddy_sessions] || String(CONST.codebuddy_sessions))} CodeBuddy sessions. Every transcript is in the repo: docs/tencent/evidence/</p></div>
  <div class="shot"><img src="${img}"></div></div>
  </div></body></html>`;
}
async function makeCards(browser) {
  fs.mkdirSync(CARDS, { recursive: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  for (const [name, html] of [['01_title.png', titleHtml()], ['11_codebuddy.png', codebuddyHtml()]]) {
    await p.setContent(html, { waitUntil: 'load' });
    await p.evaluate(() => document.fonts.ready);
    await p.waitForTimeout(300);
    await p.screenshot({ path: path.join(CARDS, name) });
    console.log(`card ${name}`);
  }
  await ctx.close();
  // the app's own slides, laid out at 1536x864 and rendered at 1.25x = 1920x1080, dark theme
  const sctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1.25, colorScheme: 'dark' });
  await sctx.addInitScript(() => { try { localStorage.setItem('ib-tour-offer', 'dismissed'); } catch (e) {} });
  const s = await sctx.newPage();
  await login(s, 'jdoe');
  for (const [name, url] of [['14_proof.png', '/present/proof'], ['15_business.png', '/present/business'], ['16_close.png', '/present/close']]) {
    await s.goto(WEB + url); await s.waitForLoadState('networkidle').catch(() => {});
    await s.getByTestId('slide').first().waitFor({ timeout: 20000 }).catch(() => {});
    await s.waitForTimeout(1500);
    await s.screenshot({ path: path.join(CARDS, name) });
    console.log(`slide ${name}`);
  }
  await sctx.close();
}

// ---- takes: a fresh folder each time; the current good take is replaced only after a clean run ----
// Codex round 2b #3: a failed re-record used to overwrite the marks of the previous good take.
const takeId = () => new Date().toISOString().replace(/[-:]/g, '').replace('.', '-');   // 20261030T071530-123Z
/** How many labels a scene puts on screen: its mark(page, ...) calls. A take must show every one. */
const expectedLabels = (sc) => (sc.run.toString().match(/\bmark\(page,/g) || []).length;
/** Problems that keep a take from becoming the current good take (an empty list: it is fine).
 *  assemble_finals.py check_take() runs the same checks again before it uses a take. */
function checkTake(t, clipsDir) {
  const bad = [];
  if (!t.ok) bad.push(`the take failed${t.error ? `: ${t.error}` : ''}`);
  if (!(t.duration > 0)) bad.push(`duration ${t.duration}`);
  const video = path.join(clipsDir, t.video || '');
  if (!t.video || !fs.existsSync(video)) bad.push(`video ${t.video} missing`);
  else if (!(t.bytes > 0) || fs.statSync(video).size !== t.bytes) bad.push(`video ${t.video} is not ${t.bytes} bytes`);
  const marks = Array.isArray(t.marks) ? t.marks : [];
  const starts = marks.filter((m) => 'start' in m);
  if (starts.length !== 1 || !(starts[0].start >= 0 && starts[0].start < t.duration)) bad.push('no single start mark inside the clip');
  const labels = marks.filter((m) => 'text' in m);
  if (!(t.expected_labels > 0) || labels.length !== t.expected_labels) bad.push(`${labels.length} of ${t.expected_labels} labels`);
  for (const m of labels) {
    if (!(m.at >= 0 && m.at <= t.duration) || !(m.seconds > 0)) bad.push(`label "${m.text}" at ${m.at}s for ${m.seconds}s`);
  }
  for (const m of marks.filter((x) => 'cut' in x)) {
    const [a, b] = m.cut || [];
    if (!(a >= 0 && b > a && b <= t.duration + 0.5)) bad.push(`cut ${JSON.stringify(m.cut)}`);
  }
  return bad;
}
/** Write a file in one step: a temp file next to it, flushed, then renamed over the old one. */
function writeAtomic(file, text) {
  const tmp = `${file}.${process.pid}.tmp`;
  const fd = fs.openSync(tmp, 'w');
  try { fs.writeSync(fd, text); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(tmp, file);
}
/** File a finished take under clips/takes/<scene>/<take>/ and, only if it is good, make it current. */
function finishTake(clipsDir, { scene, take, ok, error, duration, expected, marks, rawVideo }) {
  const dir = path.join(clipsDir, 'takes', scene, take);
  fs.mkdirSync(dir, { recursive: true });
  const name = `${scene}${ok ? '' : '.FAILED'}.webm`;
  if (rawVideo && fs.existsSync(rawVideo)) fs.renameSync(rawVideo, path.join(dir, name));
  const video = path.posix.join('takes', scene, take, name);
  const file = path.join(clipsDir, video);
  const t = { scene, take, ok, error: error || null, recorded_at: new Date().toISOString(), duration: +duration.toFixed(2),
              expected_labels: expected, video, bytes: fs.existsSync(file) ? fs.statSync(file).size : 0, marks };
  const problems = checkTake(t, clipsDir);
  if (problems.length) t.rejected = problems;
  fs.writeFileSync(path.join(dir, 'take.json'), JSON.stringify(t, null, 1));
  if (!problems.length) writeAtomic(path.join(clipsDir, `${scene}.marks.json`), JSON.stringify(t, null, 1));
  return { published: !problems.length, problems, dir };
}

/** --self-test: simulate good, failed and incomplete takes in a temp folder; no browser, no stack. */
function selfTest() {
  const os = require('os');
  const assert = require('assert');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'finals-takes-'));
  let k = 0;
  const raw = (bytes) => { const f = path.join(dir, `raw${k++}.webm`); fs.writeFileSync(f, Buffer.alloc(bytes, 1)); return f; };
  const good = [{ start: 1.2 }, { at: 2, seconds: 4, text: 'one' }, { cut: [3, 6] }, { at: 7, seconds: 3.5, text: 'two' }];
  const cur = path.join(dir, 's1.marks.json');
  try {
    let r = finishTake(dir, { scene: 's1', take: 'A', ok: true, duration: 20, expected: 2, marks: good, rawVideo: raw(500) });
    assert.ok(r.published, `good take refused: ${r.problems}`);
    const before = fs.readFileSync(cur, 'utf8');
    assert.strictEqual(JSON.parse(before).take, 'A');
    // a failed re-record (an error after navigation and a slow wait): filed as FAILED, current untouched
    r = finishTake(dir, { scene: 's1', take: 'B', ok: false, error: 'timeout', duration: 9, expected: 2,
                          marks: [{ start: 0.8 }, { cut: [1, 8] }], rawVideo: raw(300) });
    assert.ok(!r.published && fs.existsSync(path.join(r.dir, 's1.FAILED.webm')) && fs.existsSync(path.join(r.dir, 'take.json')));
    assert.strictEqual(fs.readFileSync(cur, 'utf8'), before, 'a failed take changed the current marks');
    // a take that "finished" but skipped a label, has no length, no start mark or no video: rejected too
    for (const [why, bad] of [
      ['missing-label', { ok: true, duration: 20, expected: 2, marks: good.slice(0, 3), rawVideo: raw(500) }],
      ['zero-duration', { ok: true, duration: 0, expected: 2, marks: good, rawVideo: raw(500) }],
      ['no-start', { ok: true, duration: 20, expected: 2, marks: good.slice(1), rawVideo: raw(500) }],
      ['no-video', { ok: true, duration: 20, expected: 2, marks: good, rawVideo: null }],
    ]) {
      r = finishTake(dir, { scene: 's1', take: `C-${why}`, ...bad });
      assert.ok(!r.published, `${why}: published`);
      assert.strictEqual(fs.readFileSync(cur, 'utf8'), before, `${why}: changed the current marks`);
    }
    // the next good take replaces it in one rename and leaves no temp file behind
    r = finishTake(dir, { scene: 's1', take: 'D', ok: true, duration: 21, expected: 2, marks: good, rawVideo: raw(700) });
    assert.ok(r.published, `good take refused: ${r.problems}`);
    const now = JSON.parse(fs.readFileSync(cur, 'utf8'));
    assert.strictEqual(now.take, 'D'); assert.strictEqual(now.bytes, 700); assert.strictEqual(now.video, 'takes/s1/D/s1.webm');
    assert.deepStrictEqual(fs.readdirSync(dir).filter((f) => f.endsWith('.tmp')), []);
    // every real scene shows at least one label, so the label check is never vacuous
    for (const [n, sc] of Object.entries(SCENES)) assert.ok(expectedLabels(sc) > 0, `${n} has no labels`);
    console.log(`self-test ok: a failed or incomplete take leaves the current marks untouched; `
      + `labels per scene: ${Object.entries(SCENES).map(([n, sc]) => `${n}=${expectedLabels(sc)}`).join(' ')}`);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

(async () => {
  if (process.argv.includes('--self-test')) { selfTest(); return; }
  ({ chromium } = require(path.join(webDir, 'node_modules/playwright')));
  fs.mkdirSync(OUT, { recursive: true });
  const args = process.argv.slice(2);
  const want = args.length ? args.filter((a) => a !== 'cards') : Object.keys(SCENES);
  const cards = !args.length || args.includes('cards');
  for (const n of want) if (!SCENES[n]) { console.log(`unknown scene ${n}; known: ${Object.keys(SCENES).join(' ')}`); process.exit(2); }
  const browser = await chromium.launch({ headless: true });   // always headless: never opens a window
  if (want.length) {                                    // warm the server so no first-load pause is filmed
    const warm = await browser.newContext({ viewport: SIZE });
    const wp = await warm.newPage();
    try {
      await login(wp, 'maya.compliance');
      for (const u of ['/', '/compare', '/dev', '/dev/journey', '/dev/audit', '/dev/analytics', '/dev/trust', '/dev/graph', '/dev/brain']) {
        await wp.goto(WEB + u).catch(() => {}); await wp.waitForTimeout(1500);
      }
    } catch (e) { console.log(`warm-up skipped: ${e.message}`); }
    await warm.close();
  }
  const failed = [];
  for (const name of want) {
    const sc = SCENES[name];
    seed = 20261103 + name.length * 7919 + name.charCodeAt(1) * 31;   // same moves on every re-shoot
    const take = takeId();
    const tdir = path.join(OUT, 'takes', name, take);             // a fresh folder: never over a good take
    fs.mkdirSync(tdir, { recursive: true });
    const ctx = await browser.newContext({ viewport: VIEW, recordVideo: { dir: tdir, size: SIZE },
                                           colorScheme: 'dark', deviceScaleFactor: 1 });
    // no first-visit tour offer in the clips; start in the scene's mode; English UI
    await ctx.addInitScript((mode) => { try {
      localStorage.setItem('ib-tour-offer', 'dismissed'); localStorage.setItem('ib-mode', mode); localStorage.setItem('ib-lang', 'en');
    } catch (e) {} }, sc.mode);
    await ctx.addInitScript(installCursor);
    const page = await ctx.newPage();
    const t0 = Date.now();
    MARKS.set(page, { t0, list: [] });
    POS.set(page, [770, 500]);
    let ok = true, error = null;
    try { await login(page, sc.who); await sc.run(page); }
    catch (e) { ok = false; error = e.message.split('\n')[0]; }
    const vid = page.video();
    await ctx.close();
    const duration = (Date.now() - t0) / 1000;
    const r = finishTake(OUT, { scene: name, take, ok, error, duration, expected: expectedLabels(sc),
                                marks: MARKS.get(page).list, rawVideo: vid ? await vid.path() : null });
    if (r.published) console.log(`ok   ${name} ${duration.toFixed(1)}s (take ${take} is now current)`);
    else {
      failed.push(name);
      console.log(`FAIL ${name}: ${r.problems.join('; ')}\n     kept in ${path.relative(repo, r.dir)}; the current take is unchanged`);
    }
  }
  if (cards) await makeCards(browser);
  await browser.close();
  if (failed.length) { console.log(`not recorded: ${failed.join(' ')}`); process.exitCode = 1; }
})();
