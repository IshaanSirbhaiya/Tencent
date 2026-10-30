// Record the screen scenes of the finals video v4 (C6, the company brain), one clip
// per scene, against the RUNNING local stack (web http://127.0.0.1:3100 -> Internal Brain :8100 -> mock
// platforms). VIDEO_PLAN_V3_FINALS.md has the story, the timings and the recording rules.
//
//   node docs/finals/video/record_v3.cjs --list                  the scenes, their checks and labels
//   node docs/finals/video/record_v3.cjs --dry-run C6            run the C6 scenes, check every expected state,
//                                                                 save nothing (no video, no take)
//   node docs/finals/video/record_v3.cjs C6                      record C6 (or scene names)
//
// Atlas (C4) and The Call (C5) are NOT recorded here: Atlas is Dev's film as recorded (founder's decision,
// option A) and The Call is recorded by the team; atlas_film.py blurs the model-label line in both and
// assemble_v3.py puts them in. WorkBuddy is recorded live on the laptop (the C6_workbuddy slot). The company
// stays "Company A".
//   node docs/finals/video/record_v3.cjs --self-test             check the take handling (no browser, no stack)
//
// What it shares with record_finals.cjs (required from there, not copied): the drawn cursor, the human-like
// pointer, typing and scrolling, the label marks, the wait cuts, and the take safety. Every take is filed in
// its own folder, <v3>/clips/takes/<scene>/<take>/, and becomes the current good take
// (<v3>/clips/<scene>.marks.json, replaced in one rename) only if it finished, every label was shown and
// EVERY EXPECTED-STATE CHECK PASSED. A check that fails throws at once: the take is kept as FAILED, the
// current take is untouched, and the process exits 1. Wrong takes are never used (assemble_v3.py checks
// the same things again before it uses a take).
//
// Differences from the 16 Oct / 31 Oct recorder (founder's v3 rules, VIDEO_PLAN_V3_FINALS.md section 2):
// - light mode, a 1440 x 810 window, real Chrome (channel 'chrome'), headless;
// - data-testid selectors only (plus structure under a test id: rows of a test-id table, the button of a
//   test-id panel). No selector and no check ever matches the company's name: another branch renames it;
// - expected-state checks per scene (check()); each is written into the take as {check, ok, detail}, and
//   the scene's list of checks as {expect_checks: [...]}, so the assembler can verify them too;
// - the model labels on screen are checked against an allowlist of neutral labels ("AI-generated ...",
//   "Quoted from sources ...", "Laid out by ..."): a take that shows any other model label is refused;
// - a scene marked model: true needs the model ON (checked first; none of the C6 scenes asks for it).
//
// Where the media lives: <v3> = $WALNUT_V3_DIR, or <repo>/submission/finals_video/v3 (git-ignored).
// Nothing this writes is ever committed: no takes, no answers, no screenshots.
const fs = require('fs');
const path = require('path');
const shared = require('./record_finals.cjs');
const { installCursor, moveTo, bringIntoView, hover, click, type, read, scrollBy, pause, mark, slow, oneOf,
  MARKS, POS, finishTake, takeId, expectedLabels, reseed, setView } = shared;

const repo = path.resolve(__dirname, '../../..');
const webDir = path.join(repo, 'databridge/apps/internal_brain_web');
const WEB = process.env.IB_WEB || 'http://127.0.0.1:3100';
const WALNUT = process.env.WALNUT_ORIGINAL_WEB || 'http://localhost:3300';     // the original platform screens
const V3 = process.env.WALNUT_V3_DIR || path.join(repo, 'submission/finals_video/v3');
const OUT = path.join(V3, 'clips');
const SIZE = { width: 1440, height: 810 };
setView(SIZE.width, SIZE.height);

// The question, word for word (story1-the-call-screen.md).
const QV = "Why has Velquarry Trading's account been restricted, and what should I tell the customer?";
const QB = "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?";
const EVAL = path.join(webDir, 'public/eval_summary.json');
// The only model labels that may be on screen (founder: the stand-in shows only as "AI model"; a
// quoted answer says so). An allowlist, so no other model's name has to be written here.
const MODEL_LABEL_OK = /^(AI-generated from your cited sources|Quoted from sources.*|Laid out by rules.*|Laid out by an AI model.*|The model’s layout did not pass.*|The model's layout did not pass.*|AI model.*|.*\bno model\b.*|—)$/i;

const tid = (id) => `[data-testid="${id}"]`;
const textOf = async (loc) => ((await loc.count()) ? (await loc.first().innerText().catch(() => '')) : '');

// ---- expected-state checks -------------------------------------------------------------------
class CheckFailed extends Error {}
/** Record an expected-state check in the take; a failed one stops the take (it is then refused). */
function check(page, name, ok, detail = '') {
  const m = MARKS.get(page);
  const at = m ? +((Date.now() - m.t0) / 1000).toFixed(2) : 0;
  if (m) m.list.push({ check: name, ok: !!ok, detail: String(detail).slice(0, 200), at });
  console.log(`     ${ok ? 'ok  ' : 'FAIL'} check ${name}${detail ? `: ${String(detail).slice(0, 120)}` : ''}`);
  if (!ok) throw new CheckFailed(`check ${name} failed${detail ? `: ${detail}` : ''}`);
}
/** Problems with a take's checks: every expected check recorded and passed. [] = fine. */
function checkProblems(marks, expected) {
  const bad = [];
  const got = new Map();
  for (const m of marks) if (m && 'check' in m) { if (!got.has(m.check) || !m.ok) got.set(m.check, m.ok); }
  for (const c of expected) {
    if (!got.has(c)) bad.push(`check ${c} never ran`);
    else if (got.get(c) !== true) bad.push(`check ${c} failed`);
  }
  for (const [c, ok] of got) if (ok !== true && !expected.includes(c)) bad.push(`check ${c} failed`);
  return bad;
}

/** Every model label on screen is a neutral one (badge, the page's "Laid out by", the journey's Model card). */
async function modelLabels(page) {
  const seen = [];
  for (const id of ['model-badge', 'view-by', 'journey-model']) {
    const loc = page.locator(tid(id));
    for (let i = 0; i < (await loc.count()); i++) {
      const t = (await loc.nth(i).innerText().catch(() => '')).trim();
      if (t) seen.push(t);
    }
  }
  const bad = seen.filter((t) => !MODEL_LABEL_OK.test(t));
  check(page, 'model-labels', !bad.length, bad.length ? `not a neutral label: ${bad.join(' | ')}` : `${seen.length} neutral`);
}

/** The screen for narration paragraph `n` of this part starts now (VIDEO_PLAN_V4 / the team's segment
 *  table). assemble_v3.py lines each paragraph of the read narration up with its seg mark. A fractional n
 *  (5.5) is a second shot inside paragraph 5. */
function seg(page, n) {
  const m = MARKS.get(page);
  if (m) m.list.push({ seg: n, at: +((Date.now() - m.t0) / 1000).toFixed(2) });
}
// ---- app steps ------------------------------------------------------------------------------
async function login(page, persona, tries = 24) {
  for (let i = 0; i < tries; i++) {              // the IB may be restarted briefly: retry ~2 min
    const r = await page.request.post(`${WEB}/api/ib/auth/demo-login`, { data: { persona }, headers: { origin: WEB } }).catch(() => null);
    if (r && r.ok()) return;
    if (r && r.status() >= 400 && r.status() < 500 && i >= 1) break;       // the persona is not in this world
    await pause(page, 5);
  }
  throw new Error(`demo login as ${persona} failed (is the Atlas world on? is the stack up?)`);
}
async function goto(page, url, settle = 1.0) {
  const m = MARKS.get(page);
  const load = page.goto(url).then(() => page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {}));
  if (m && !m.started) {                         // the clip starts once the first page is up: no blank frames
    await load;
    await page.waitForFunction(() => !document.querySelector('[data-testid="session-check"]')
      && document.body.innerText.length > 200, null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(250);
    m.started = true;
    m.list.push({ start: +((Date.now() - m.t0) / 1000 + 0.15).toFixed(2) });
  } else {
    await slow(page, load);
  }
  await pause(page, settle);
}
/** /ib/status: is the model on (the neutral stand-in in the active chain)? Never prints a model name. */
async function modelOn(page) {
  const r = await page.request.get(`${WEB}/api/ib/ib/status`).catch(() => null);
  const st = r && r.ok() ? await r.json().catch(() => null) : null;
  const m = st && st.models;
  if (!m) return null;
  return !!m.active && (/standin/i.test(String(m.provider || '')) || (m.active_chain || []).some((x) => /standin/i.test(String(x))));
}
function evalFacts() {
  const e = JSON.parse(fs.readFileSync(EVAL, 'utf8'));
  return { requests: e.requests, leaks: e.leaks, pairs: e.differential.pairs, mismatches: e.differential.mismatches, stale: !!e.stale };
}
const digits = (s) => String(s).replace(/[^\d]/g, ' ');

// ---- the scenes -------------------------------------------------------------------------------
// part: the video part (C6). who: the persona signed in first; mode: 'user' or 'dev'. model: needs the
// model ON. optional: the assembler leaves it out if it has no good take. checks: every expected state the
// scene must see (in check() calls); the take is refused if any is missing or failed. Target lengths are the
// narration's (assemble_v3.py fits the speed to the narration; waits are cut to about 1 s of "thinking").
const SCENES = {
  // C6 row 1 (B_C6a paragraph 1): the map of what each person can open, Rachel vs Ken.
  C6_1_graph: { part: 'C6', who: 'mlro.tan', mode: 'dev', checks: ['two-maps'],
    async run(page) {
      await goto(page, `${WEB}/dev/graph?compare=ken.ext`, 0);
      seg(page, 1);
      await slow(page, page.getByTestId('compare-side-count').waitFor({ timeout: 120000 }).catch(() => {}));
      await slow(page, page.waitForFunction(() => !document.querySelector('[data-testid="graph-left-loading"], [data-testid="graph-right-loading"]'),
        null, { timeout: 120000 }).catch(() => {}));
      const sides = await page.locator(`${tid('graph-left')}, ${tid('graph-left-canvas')}`).count()
        && await page.locator(`${tid('graph-right')}, ${tid('graph-right-canvas')}`).count();
      check(page, 'two-maps', !!sides && (await page.getByTestId('compare-side-count').count()) > 0, 'the two maps are not both drawn');
      await pause(page, 1.2);
      mark(page, 'Rachel (left) and Ken (right): a map of what each can open', 4.5);
      await hover(page, page.locator(`${tid('graph-left-canvas')}, ${tid('graph-left')}`).first(), 2.0);
      await hover(page, page.getByTestId('compare-side-count'), 1.6);
      mark(page, 'Ken · outside contractor: the case is not on his map', 4.5);
      await hover(page, page.locator(`${tid('graph-right-canvas')}, ${tid('graph-right')}`).first(), 3.0);
      await hover(page, page.getByTestId('compare-summary'), 1.5);
    } },

  // C6 row 2 (B_C6a paragraph 2): Ken asks about a real restricted company and a made-up one: identical.
  C6_2_ken: { part: 'C6', who: 'ken.ext', mode: 'user', checks: ['identical'],
    async run(page) {
      await goto(page, `${WEB}/compare?mode=person&p=ken.ext&q1=${encodeURIComponent(QV)}&q2=${encodeURIComponent(QB)}`, 1.0);
      seg(page, 2);
      mark(page, 'Ken · a restricted company in the fictional data vs a name we invented', 4.5);
      await read(page, page.getByTestId('compare-mode'), 1.2);
      await click(page, page.locator(`form:has(${tid('compare-mode')}) button[type="submit"]`), 0);
      await slow(page, page.locator(`${tid('compare-results')}[aria-busy="false"]`).waitFor({ timeout: 120000 }).catch(() => {}));
      await slow(page, page.getByTestId('identical-marker').waitFor({ timeout: 15000 }).catch(() => {}));
      const errors = await page.getByTestId('compare-error').count();
      check(page, 'identical', (await page.getByTestId('identical-marker').count()) > 0 && !errors,
        errors ? 'a column errored' : 'the two answers are not marked identical');
      await pause(page, 0.5);
      await bringIntoView(page, page.getByTestId('compare-results'));
      await read(page, page.getByTestId('compare-results').getByTestId('refusal-card').first(), 2.5);
      await read(page, page.getByTestId('compare-results').getByTestId('refusal-card').last(), 2.5);
      mark(page, 'Identical refusals · word for word', 5);
      await hover(page, page.getByTestId('identical-marker'), 4);
    } },

  // C6 row 3 (paragraph 3): the journey of Rachel's request (Rachel must have asked the Velquarry question on this stack first).
  C6_3_journey: { part: 'C6', who: 'maya.compliance', mode: 'dev', checks: ['journey', 'model-labels'],
    async run(page) {
      await goto(page, `${WEB}/dev/journey`, 0.6);
      seg(page, 3);
      await slow(page, page.getByTestId('recent-requests').waitFor({ timeout: 30000 }));
      // Rachel's own request: matched on her sign-in name and the question's customer (never the company)
      const row = page.getByTestId('recent-requests').locator('tbody tr, [role=row]')
        .filter({ hasText: 'mlro.tan' }).filter({ hasText: 'Velquarry' }).first();
      if (!(await row.count())) check(page, 'journey', false, 'no request by mlro.tan about Velquarry yet: Rachel must ask the question on this stack first (The Call)');
      mark(page, "Maya · compliance: the journey of Rachel's request", 4);
      await hover(page, row, 0.8);
      await click(page, row, 0);
      await slow(page, page.locator(`${tid('journey-waterfall')}, ${tid('waterfall')}`).first().waitFor({ timeout: 30000 }).catch(() => {}));
      check(page, 'journey', (await page.locator(`${tid('journey-waterfall')}, ${tid('waterfall')}`).count()) > 0, 'no waterfall');
      await modelLabels(page);
      await pause(page, 0.8);
      mark(page, 'Who asked, which sources were allowed, every check before the AI saw anything', 5);
      await read(page, page.locator(`${tid('waterfall')}, ${tid('journey-waterfall')}`).first(), 5);
      mark(page, 'Sealed, chained records, before she saw a word', 4);
      await hover(page, page.getByTestId('ledger-strip'), 2.5);
      const verdicts = page.locator(`${tid('journey-compliance')}, ${tid('journey-verdicts')}`).first();
      await bringIntoView(page, verdicts);
      await read(page, verdicts, 4);
    } },

  // C6 row 4 (paragraph 4): Verify, then the tamper demo on a copy; the real log is not modified.
  C6_4_audit: { part: 'C6', who: 'maya.compliance', mode: 'dev', checks: ['chain-intact', 'tamper-caught', 'real-log-untouched'],
    async run(page) {
      await goto(page, `${WEB}/dev/audit`, 0.8);
      seg(page, 4);
      const verify = page.getByTestId('verify-panel');
      await bringIntoView(page, verify);
      mark(page, 'Maya · compliance officer · the sealed audit log', 4);
      await click(page, verify.locator('button').first(), 0);
      await slow(page, page.locator(`${tid('verify-panel')}[data-state="ok"], ${tid('verify-panel')}[data-state="bad"], ${tid('verify-panel')}[data-state="error"]`).first().waitFor({ timeout: 60000 }).catch(() => {}));
      check(page, 'chain-intact', (await verify.getAttribute('data-state')) === 'ok', `state ${await verify.getAttribute('data-state')}`);
      await pause(page, 0.6);
      mark(page, 'Verify: every link of the hash chain re-checked · intact', 4);
      await hover(page, page.getByTestId('verify-status'), 2.0);
      const tamper = page.getByTestId('tamper-panel');
      await bringIntoView(page, tamper);
      await click(page, tamper.locator('button').first(), 0);    // runs on a temporary COPY of the ledger
      mark(page, 'We try to cheat · on a copy of the log, never the real one', 4);
      await slow(page, page.locator(`${tid('tamper-panel')}[data-state="done"], ${tid('tamper-panel')}[data-state="error"]`).first().waitFor({ timeout: 120000 }).catch(() => {}));
      const caught = await page.locator('[data-testid^="tamper-row-"]').count();
      check(page, 'tamper-caught', (await tamper.getAttribute('data-state')) === 'done' && caught > 0, `state ${await tamper.getAttribute('data-state')}, ${caught} rows`);
      check(page, 'real-log-untouched', (await page.getByTestId('tamper-safe').count()) > 0 && !(await page.getByTestId('tamper-real-changed').count())
        && (await page.getByTestId('tamper-real-check').count()) > 0, 'the real log check is missing or failed');
      await pause(page, 0.6);
      await bringIntoView(page, page.locator('[data-testid^="tamper-row-"]').first());
      mark(page, 'Caught: the chain breaks at the edited entry', 4.5);
      await read(page, page.locator('[data-testid^="tamper-row-"]').first(), 3);
      await bringIntoView(page, page.getByTestId('tamper-safe'));
      mark(page, 'Your real audit log was not modified', 4);
      await hover(page, page.getByTestId('tamper-safe'), 2.0);
      await hover(page, page.getByTestId('tamper-real-check'), 2.0);
    } },

  // C6 row 5 (paragraph 5): the Walnut engine view, with its time slider (jdoe: the persona the graph was built as).
  C6_5_engine: { part: 'C6', who: 'jdoe', mode: 'dev', checks: ['engine', 'time-slider'],
    async run(page) {
      await goto(page, `${WEB}/dev/brain`, 0);
      seg(page, 5);
      await slow(page, page.getByTestId('engine-snapshot-banner').waitFor({ timeout: 60000 }).catch(() => {}));
      check(page, 'engine', (await page.getByTestId('engine-snapshot-banner').count()) > 0, 'the engine view is not available (gateway :8300, WALNUT_PROXY=1)');
      await pause(page, 1.0);
      mark(page, 'The platform underneath: the Walnut engine and its evidence graph', 4);
      await hover(page, page.getByTestId('engine-snapshot-banner'), 1.5);
      await click(page, page.getByTestId('engine-tab-brain'), 0);
      await slow(page, page.locator(`${tid('engine-time-slider')} input[type=range]`).waitFor({ timeout: 60000 }).catch(() => {}));
      const slider = page.getByTestId('engine-time-slider').locator('input[type=range]');
      const box = (await slider.count()) ? await slider.boundingBox() : null;
      check(page, 'time-slider', !!box, 'no time slider');
      await pause(page, 1.2);
      mark(page, 'Rewind what the company knew to an earlier date', 4);
      const y = box.y + box.height / 2;
      const drag = async (from, to) => { for (let i = 1; i <= 6; i++) { await page.mouse.move(box.x + box.width * (from + (to - from) * i / 6), y); await pause(page, 0.14); } };
      await moveTo(page, box.x + box.width - 4, y); await page.mouse.down();
      await drag(0.995, 0.45); await pause(page, 1.4);
      await drag(0.45, 0.995); await page.mouse.up(); await pause(page, 0.8);
      POS.set(page, [box.x + box.width * 0.995, y]);
      await hover(page, page.getByTestId('engine-asof-count'), 1.5);
    } },

  // C6 row 5, optional: the ORIGINAL platform screens (databridge/apps/frontend on :3300, started separately;
  // they are not reachable from the Internal Brain app). Read-only: an example only fills the composer box.
  // ASSUMED test ids, from record_finals.cjs scene 10a: roster-jdoe, builder-tab-adapter,
  // adapter-composer-box-example-0, builder-tab-agents, agent-composer-box-text, sources-table / sources-empty.
  C6_5b_platform: { part: 'C6', who: null, mode: 'user', optional: true, checks: ['builder', 'connectors'],
    async run(page) {
      await page.goto(`${WALNUT}/builder?tab=adapter`).catch(() => {});
      const roster = page.getByTestId('roster-jdoe');
      if (await roster.waitFor({ timeout: 20000 }).then(() => true, () => false)) await roster.click();
      await goto(page, `${WALNUT}/builder?tab=adapter`, 0.6);
      seg(page, 5.5);                                            // second shot of paragraph 5
      await slow(page, page.getByTestId('builder-tab-adapter').waitFor({ timeout: 30000 }).catch(() => {}));
      check(page, 'builder', (await page.getByTestId('builder-tab-adapter').count()) > 0, 'the original screens are not running on :3300');
      mark(page, 'The platform: AI agents that build new adapters', 3);
      const ex = page.getByTestId('adapter-composer-box-example-0');
      if (await ex.count()) await click(page, ex, 0.9); else await hover(page, page.getByTestId('builder-tab-adapter'), 0.9);
      await click(page, page.getByTestId('builder-tab-agents'), 0.4);
      await hover(page, page.getByTestId('agent-composer-box-text'), 1.0);
      await goto(page, `${WALNUT}/connectors`, 0.4);
      const list = page.locator(`${tid('sources-table')}, ${tid('sources-empty')}`).first();
      check(page, 'connectors', (await list.count()) > 0, 'no connectors list');
      mark(page, 'The platform: a connector for each source', 3);
      await read(page, list, 1.6);
    } },

  // C6c (B_C6c): the proof slide, its numbers checked against eval_summary.json.
  C6_7_proof: { part: 'C6', who: 'jdoe', mode: 'user', checks: ['proof-numbers'],
    async run(page) {
      const f = evalFacts();
      await goto(page, `${WEB}/present/proof`, 0.5);
      seg(page, 1);                                              // B_C6c
      await slow(page, page.getByTestId('slide').first().waitFor({ timeout: 20000 }).catch(() => {}));
      const leaks = digits(await textOf(page.getByTestId('proof-leaks')));
      const diffs = digits(await textOf(page.getByTestId('proof-diffs')));
      const want = (s, n) => s.replace(/ (?=\d{3}\b)/g, '').includes(String(n));
      check(page, 'proof-numbers', !f.stale && f.leaks === 0 && f.mismatches === 0 && want(leaks, f.requests) && want(diffs, f.pairs),
        `leaks "${leaks.trim()}", diffs "${diffs.trim()}", eval ${f.requests}/${f.leaks}, ${f.pairs}/${f.mismatches}${f.stale ? ', stale' : ''}`);
      mark(page, `${f.requests.toLocaleString('en-GB')} attack questions · ${f.leaks} leaks · ${f.pairs} secret-vs-made-up pairs · ${f.mismatches} differences`, 7);
      await hover(page, page.getByTestId('proof-leaks'), 3.5);
      await hover(page, page.getByTestId('proof-diffs'), 3.5);
    } },
};
// C6 = the brain (B_C6a) and the proof (B_C6c). WorkBuddy is recorded live on the laptop (screen capture,
// outside this recorder): assemble_v3.py takes it as the C6_workbuddy slot.
const GROUPS = {
  C6: ['C6_1_graph', 'C6_2_ken', 'C6_3_journey', 'C6_4_audit', 'C6_5_engine', 'C6_5b_platform', 'C6_7_proof'] };

/** Labels and checks are counted in the scene's source, as record_finals.cjs does for labels. */
const declaredChecks = (sc) => [...new Set([...sc.run.toString().matchAll(/\bcheck\(page, '([\w-]+)'/g)].map((m) => m[1])
  .concat(/\bmodelLabels\(page\)/.test(sc.run.toString()) ? ['model-labels'] : [])
  .concat(/\bopenPage\(page/.test(sc.run.toString()) ? ['page'] : [])           // the helpers' own checks
  .concat(/\bopenCitation\(page/.test(sc.run.toString()) ? ['citation'] : []))];

// ---- one scene: a take (recorded) or a dry run (checked, nothing saved) -------------------------
async function runScene(browser, name, { dry }) {
  const sc = SCENES[name];
  reseed(20261103 + [...name].reduce((a, c) => a * 31 + c.charCodeAt(0), 7) % 100000);   // same moves on every re-shoot
  const take = takeId();
  const tdir = path.join(OUT, 'takes', name, take);
  if (!dry) fs.mkdirSync(tdir, { recursive: true });            // a fresh folder: never over a good take
  const ctx = await browser.newContext({ viewport: SIZE, colorScheme: 'light', deviceScaleFactor: 1,
    ...(dry ? {} : { recordVideo: { dir: tdir, size: SIZE } }) });
  await ctx.addInitScript((mode) => { try {
    localStorage.setItem('ib-theme', 'light'); localStorage.setItem('ib-mode', mode); localStorage.setItem('ib-lang', 'en');
    localStorage.setItem('ib-tour-offer', 'dismissed');
  } catch (e) {} }, sc.mode);
  await ctx.addInitScript(installCursor);
  const page = await ctx.newPage();
  const t0 = Date.now();
  MARKS.set(page, { t0, list: [{ expect_checks: sc.checks }] });
  POS.set(page, [720, 470]);
  let ok = true, error = null;
  try {
    if (sc.model) {
      const on = await modelOn(page);
      if (on !== true) throw new CheckFailed(`the model is not ON (status says ${on}); this scene is recorded with the model on`);
    }
    if (sc.who) await login(page, sc.who, 24);
    await sc.run(page);
  } catch (e) { ok = false; error = e.message.split('\n')[0]; }
  const vid = page.video();
  await ctx.close();
  const duration = (Date.now() - t0) / 1000;
  const marks = MARKS.get(page).list;
  const cp = ok ? checkProblems(marks, sc.checks) : [];
  if (cp.length) { ok = false; error = cp.join('; '); }
  if (dry) {
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${name} (dry run, ${duration.toFixed(1)}s, nothing saved)${ok ? '' : `: ${error}`}`);
    return ok;
  }
  const r = finishTake(OUT, { scene: name, take, ok, error, duration, expected: expectedLabels(sc), marks,
                              rawVideo: vid ? await vid.path() : null });
  if (r.published) console.log(`ok   ${name} ${duration.toFixed(1)}s (take ${take} is now current)`);
  else console.log(`FAIL ${name}: ${r.problems.join('; ')}\n     kept in ${r.dir}; the current take is unchanged`);
  return r.published;
}

// ---- --self-test: no browser, no stack ----------------------------------------------------------
function selfTest() {
  const os = require('os');
  const assert = require('assert');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'v3-takes-'));
  try {
    // every scene declares exactly the checks its code runs, shows labels, and the groups cover every scene
    for (const [n, sc] of Object.entries(SCENES)) {
      assert.deepStrictEqual([...sc.checks].sort(), declaredChecks(sc).sort(), `${n}: declared checks differ from check() calls`);
      assert.ok(expectedLabels(sc) > 0, `${n} has no labels`);
      assert.ok(!/getByText|getByRole|getByPlaceholder|getByLabel/.test(sc.run.toString()), `${n}: a selector that is not a test id`);
      assert.ok(!/Company A|Pandan/i.test(sc.run.toString()), `${n}: matches on the company's name`);
    }
    assert.deepStrictEqual(Object.values(GROUPS).flat().sort(), Object.keys(SCENES).sort());
    // the check bookkeeping: all passed / one failed / one never ran
    const exp = ['a', 'b'];
    assert.deepStrictEqual(checkProblems([{ check: 'a', ok: true }, { check: 'b', ok: true }], exp), []);
    assert.deepStrictEqual(checkProblems([{ check: 'a', ok: true }, { check: 'b', ok: false }], exp), ['check b failed']);
    assert.deepStrictEqual(checkProblems([{ check: 'a', ok: true }], exp), ['check b never ran']);
    assert.deepStrictEqual(checkProblems([{ check: 'a', ok: true }, { check: 'b', ok: true }, { check: 'b', ok: false }], exp), ['check b failed']);
    // model labels: only the neutral ones pass
    for (const ok of ['AI-generated from your cited sources', 'Quoted from sources (no model)', 'Laid out by rules from your request, no model', 'Laid out by an AI model']) assert.ok(MODEL_LABEL_OK.test(ok), ok);
    for (const bad of ['Tencent Hunyuan Hy3', 'Laid out by hy3-preview', 'standin-1 via TokenHub']) assert.ok(!MODEL_LABEL_OK.test(bad), bad);
    // a take with a failed check is refused by finishTake and never becomes current
    const raw = (b) => { const f = path.join(dir, `r${b}.webm`); fs.writeFileSync(f, Buffer.alloc(b, 1)); return f; };
    const good = [{ expect_checks: ['x'] }, { start: 1 }, { check: 'x', ok: true }, { at: 2, seconds: 3, text: 'one' }];
    let r = finishTake(dir, { scene: 'v', take: 'A', ok: true, duration: 10, expected: 1, marks: good, rawVideo: raw(400) });
    assert.ok(r.published);
    const failedCheck = [{ expect_checks: ['x'] }, { start: 1 }, { check: 'x', ok: false }];
    const cp = checkProblems(failedCheck, ['x']);
    r = finishTake(dir, { scene: 'v', take: 'B', ok: cp.length === 0, error: cp.join('; '), duration: 8, expected: 1, marks: failedCheck, rawVideo: raw(300) });
    assert.ok(!r.published && JSON.parse(fs.readFileSync(path.join(dir, 'v.marks.json'), 'utf8')).take === 'A');
    // the media folder is outside git
    const gi = fs.readFileSync(path.join(repo, '.gitignore'), 'utf8');
    assert.ok(/^\/?submission\/finals_video\/v3\/?$/m.test(gi), '.gitignore does not cover submission/finals_video/v3/');
    console.log(`self-test ok: ${Object.keys(SCENES).length} scenes, test-id selectors only; checks per scene: `
      + Object.entries(SCENES).map(([n, sc]) => `${n}=${sc.checks.length}`).join(' ')
      + `; labels: ${Object.entries(SCENES).map(([n, sc]) => `${n}=${expectedLabels(sc)}`).join(' ')}`);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

function list() {
  for (const [g, names] of Object.entries(GROUPS)) {
    console.log(g);
    for (const n of names) {
      const sc = SCENES[n];
      console.log(`  ${n.padEnd(15)} as ${String(sc.who).padEnd(16)} ${sc.optional ? 'optional ' : ''}${sc.model ? 'model ON ' : ''}checks: ${sc.checks.join(', ')}`);
    }
  }
}

if (require.main === module) (async () => {
  const args = process.argv.slice(2);
  if (args.includes('--self-test')) { selfTest(); return; }
  if (args.includes('--list')) { list(); return; }
  const dry = args.includes('--dry-run');
  const names = args.filter((a) => !a.startsWith('--')).flatMap((a) => GROUPS[a] || [a]);
  if (!names.length) { console.log('name a part (C6) or scenes; --dry-run to check without saving; --list'); process.exit(2); }
  for (const n of names) if (!SCENES[n]) { console.log(`unknown scene ${n}; known: ${Object.keys(SCENES).join(' ')}`); process.exit(2); }
  const { chromium } = require(path.join(webDir, 'node_modules/playwright'));
  const browser = await chromium.launch({ channel: 'chrome', headless: true });   // never opens a window
  if (!dry) {                                    // warm the pages (no questions asked) so no first load is filmed
    fs.mkdirSync(OUT, { recursive: true });
    const warm = await browser.newContext({ viewport: SIZE, colorScheme: 'light' });
    const wp = await warm.newPage();
    try {
      await login(wp, 'maya.compliance');
      for (const u of ['/', '/compare', '/dev/journey', '/dev/audit', '/dev/graph', '/dev/brain', '/present/proof']) {
        await wp.goto(WEB + u).catch(() => {}); await wp.waitForTimeout(1500);
      }
    } catch (e) { console.log(`warm-up skipped: ${e.message}`); }
    await warm.close();
  }
  const failed = [];
  for (const n of names) {
    console.log(`${dry ? 'dry run' : 'record'} ${n}`);
    if (!(await runScene(browser, n, { dry }))) failed.push(n);
  }
  await browser.close();
  if (failed.length) { console.log(`${dry ? 'failed checks' : 'not recorded'}: ${failed.join(' ')}`); process.exitCode = 1; }
})();

module.exports = { SCENES, GROUPS, checkProblems, MODEL_LABEL_OK };
