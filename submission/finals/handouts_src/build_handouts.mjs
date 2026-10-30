// Re-render after numbers change:  node build_handouts.mjs
// Needs node_modules of databridge/apps/internal_brain_web (react, qrcode.react, playwright).
// Numbers are read from eval_summary.json, STATUS.md (browser tests) and ROI_MODEL.md; none are hard-coded.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
const web = path.join(repo, 'databridge/apps/internal_brain_web');
const req = createRequire(path.join(web, 'package.json'));
const React = req('react');
const { renderToStaticMarkup } = req('react-dom/server');
const { QRCodeSVG } = req('qrcode.react');
const { chromium } = req('playwright');

const outDir = path.resolve(here, '..');
const REPO_URL = 'https://github.com/IshaanSirbhaiya/Tencent/tree/final_v1.0';
const fmt = (n) => Number(n).toLocaleString('en-US');

const ev = JSON.parse(fs.readFileSync(path.join(web, 'public/eval_summary.json'), 'utf8'));
const status = fs.readFileSync(path.join(repo, 'STATUS.md'), 'utf8');
const m = status.match(/Playwright, axe\)\s*\|\s*(\d+) passed/);
const browserTests = m ? Number(m[1]) : null; // eval_summary.tests.e2e (38) counts gated skips, so STATUS.md is used
const roi = fs.readFileSync(path.join(repo, 'docs/finals/rubric/impact_feasibility/ROI_MODEL.md'), 'utf8');
const base = roi.match(/Base \| 1\.5 \| US\$31 \| 600 × 1\.5 × 46 × 31 = \*\*US\$([\d,]+)\*\*/);
const baseVal = base ? Number(base[1].replace(/,/g, '')) : null;
const be = roi.match(/= (\d+\.\d) minutes per person per week/);
const N = {
  leaks: ev.leaks, req: fmt(ev.requests), pairs: ev.differential.pairs, mism: ev.differential.mismatches,
  backend: ev.tests.backend, browser: browserTests ?? 'TODO',
  value: baseVal ? '≈ US$' + (baseVal / 1e6).toFixed(1) + 'M' : 'TODO',
  be: be ? '~' + Math.round(Number(be[1])) : 'TODO',
  date: '30 Oct 2026',
};

const css = fs.readFileSync(path.join(here, 'handout.css'), 'utf8')
  .replace('__FONT__', pathToFileURL(path.join(web, 'node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2')).href);
const qr = (size) => renderToStaticMarkup(React.createElement(QRCodeSVG, { value: REPO_URL, size, level: 'M', marginSize: 0, fgColor: '#0f1115', bgColor: '#ffffff' }));
const page = (title, body) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title}</title><style>${css}@page{size:A4;margin:0}</style></head><body>${body}</body></html>`;
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');

// ---- 1. leave-behind ----
const leave = page('Walnut DataBridge leave-behind', `
<main class="lb">
<header><div class="brand"><span class="dot"></span>Walnut DataBridge</div><div class="meta">Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 · FinTech finalist · Demo Day 3 Nov</div></header>
<h1>Answers you can prove.</h1>
<p class="lede">At a regulated company, the same question must get different answers for different people, and nobody should be able to tell what they were not allowed to see.</p>
<section class="cols">
<div><h2>What it does</h2><ul>
<li><b>Answers only from what the asker can open</b> across Confluence, Jira, Slack and Drive, and cites the exact passage.</li>
<li><b>Two permission gates before the model</b>, plus a re-check at the source just before release.</li>
<li><b>Keeps a sealed, hash-chained log</b> of every request, so any change shows up.</li></ul></div>
<div><h2>Two proofs</h2>
<div class="proof"><b>Restricted looks exactly like non-existent.</b> Ken asks about a real customer he may not see and about a company that does not exist: the two refusals are identical, byte for byte. <span class="k">${N.mism} differences in ${N.pairs} paired questions.</span></div>
<div class="proof"><b>Tamper-evident audit.</b> Change one decision in a copy of the log and Verify breaks at that exact entry.</div></div>
</section>
<section class="nums">
<div class="n"><span>${N.leaks} / ${N.req}</span>adversarial requests leaked</div>
<div class="n"><span>${N.mism} / ${N.pairs}</span>restricted vs non-existent differences</div>
<div class="n"><span>${N.backend}</span>backend tests</div>
<div class="n"><span>${N.browser}</span>browser tests (axe included)</div>
</section>
<p class="fine">Evidence re-run ${N.date}. Synthetic data; Confluence, Jira, Slack and Drive are mock servers with real API shapes. Answers are currently quoted from the sources with the same checks; the Hunyuan Hy3 live run is pending.</p>
<section class="cols">
<div><h2>Built with Tencent</h2><ul class="tight">
<li><b>CodeBuddy</b> built and reviewed the code.</li>
<li><b>WorkBuddy</b> asks through our MCP connector, a second front door.</li>
<li><b>Hunyuan Hy3 via TokenHub</b> is wired; the live run is pending.</li>
<li><b>Miora</b> made the cover art.</li></ul></div>
<div><h2>Business value <i>(estimate)</i></h2>
<p class="big">${N.value}<small> a year, base case</small></p>
<p class="small">600-person fintech, 90 min saved per person per week, US$31/h, 46 weeks. Break-even is about ${N.be.replace('~', '')} minutes per person per week. A projection, not a measurement; a pilot would measure it.</p></div>
</section>
<footer>
<div class="ask"><h2>The ask</h2><p>One pilot team at one regulated company.</p>
<p class="team"><b>Team Walnut</b> · Ishaan · Dev · Mehul · Aaruni</p></div>
<div class="qr">${qr(104)}<div><b>Try it</b><br><span class="url">github.com/IshaanSirbhaiya/Tencent</span><br><span class="url">branch final_v1.0</span></div></div>
</footer>
</main>`);

// ---- 2. judge cards ----
const cards = [
 ['1','Impact & Relevance','A customer account is restricted and the company AI must help the account manager without tipping off. Break-even about ' + N.be.replace('~','') + ' min per person per week; base case ' + N.value + ' a year (estimate).','/value','docs/finals/rubric/impact_feasibility/IMPACT_SHEET.md · ROI_MODEL.md'],
 ['2','Human-Centered Design','Persona-first sign-in, plain-language answers, a source sheet that opens the passage. The hallway-test report is still pending.','/login · /settings · /history','docs/finals/rubric/hcd_ux/PERSONAS_AND_JOURNEYS.md · FIX_LOG.md'],
 ['3','AI Interaction','Cited answer behind two permission gates; the same question asked from WorkBuddy through MCP. Hy3 is wired, live run pending: answers are quoted from the sources with the same checks.','/dev/journey/<req>','docs/finals/rubric/ai_innovation/AI_USAGE_MAP.md'],
 ['4','Technical Execution','Per-stage timings and three hash-chained records per request. ' + N.backend + ' backend and ' + N.browser + ' browser tests; ' + N.leaks + ' leaks in ' + N.req + ' adversarial requests.','/dev · /dev/journey/<req> · /dev/graph','docs/finals/rubric/technical/ARCHITECTURE_ONE_PAGER.md · EVIDENCE_CARD.md'],
 ['5','Feasibility','Break-even about ' + N.be.replace('~','') + ' min a week and model cost about US$1k a year (both estimates). Tencent Cloud deploy kit, no live instance. One-team pilot.','/value · /dev/analytics','docs/finals/rubric/impact_feasibility/PILOT_PLAN.md · SCALE_ARCHITECTURE.md · COST_PER_ANSWER.md'],
 ['6','Demo & Storytelling','Five minutes, two proofs, the tagline three times, driven from the keyboard in Presenter mode.','/compare · press P (Presenter)','docs/finals/rubric/demo_story/RUN_SHEET.md · PRESENTER_KEYS.md'],
 ['7','Innovation & Creativity','Restricted is indistinguishable from non-existent (' + N.mism + ' differences in ' + N.pairs + ' pairs), and the log breaks at the exact tampered entry.','/compare · /dev/audit','docs/finals/rubric/ai_innovation/INNOVATION_ONE_PAGER.md'],
 ['8','UX & Accessibility','Keyboard-only demo. Text size, reduce motion, high contrast, theme and 简体中文 in settings; axe checks inside the browser tests.','/settings','docs/finals/rubric/hcd_ux/ACCESSIBILITY_STATEMENT.md'],
 ['9','Responsible AI & Ethics','No tipping-off; identical refusals; write-ahead sealed log; revealing needs a reason. Crosswalk to AI Verify, MAS FEAT, PDPA, NIST.','/dev/trust','docs/finals/rubric/responsible_ai/SYSTEM_CARD.md · FRAMEWORK_CROSSWALK.md'],
 ['10','Overall Quality','One story, honest numbers dated ' + N.date + ', synthetic data and mock platforms said out loud, a clear ask.','/present/close','docs/finals/rubric/demo_story/LEAVE_BEHIND.md · technical/DEMO_RUNBOOK.md'],
];
const cardHtml = (c) => `<article class="card"><div class="no">${c[0]}<small>/10</small></div><h3>${esc(c[1])}</h3>
<p class="proofl">${esc(c[2])}</p>
<dl><dt>See it in the app</dt><dd class="mono">${esc(c[3])}</dd><dt>Read it in the repo</dt><dd class="mono">${esc(c[4])}</dd></dl>
<div class="cf">Walnut DataBridge · numbers ${N.date} · synthetic data</div></article>`;
const sheets = [];
for (let i = 0; i < cards.length; i += 4) sheets.push(`<section class="sheet">${cards.slice(i, i + 4).map(cardHtml).join('')}</section>`);
const judge = page('Judge cards', sheets.join(''));

// ---- 3. presenter cheat sheet ----
const keys = [['P','Arm presenter mode, Stage theme, go to step 00'],['Space','Run the next step'],['←','Back one step'],['B','Play fallback clip (again = back to live)'],['R','Reset: restore tamper copy, clear boxes, step 00'],['L','Flip English / 简体中文'],['S','Stage theme on / off'],['?','Show key help'],['Esc','Close the source sheet; with no sheet open, hide / show the HUD']];
// Mirrors STEPS in databridge/apps/internal_brain_web/components/presenter/script.ts (HUD 00 to 10;
// P gives 00, the title). The grow steps (?grow=1) are a Q&A walk-up, never on this path.
const steps = [
 ['01','0:07','Same question, two truths','"Watch."  Both asks run.  "Amy gets the approved script. Rachel gets the case. Same brain."'],
 ['02','0:45','Ken, identical refusals','"Now Ken, a contractor. Velquarry on the left; a company that does not exist on the right."'],
 ['03','1:10','Passage, checked live','"Every sentence points at a passage the asker can open right now."'],
 ['04','1:40','Journey','"Mehul, over to the compliance officer\'s chair."'],
 ['05','2:10','Verify: intact','"I am the compliance officer. Verify: intact."'],
 ['06','2:20','Tamper a copy','"Now I change one word in a copy of the log, denied to allowed. Verify."  Read the entry number off the screen.'],
 ['07','2:35','WorkBuddy (off-app)','"Aaruni, Tencent."  Alt+Tab to WorkBuddy; paste the one exact question. At 3:12 Alt+Tab back: the newest audit row appears; L flips language and back.'],
 ['08','3:20','Proof','"' + N.req + ' adversarial requests, zero leaks. ' + N.pairs + ' paired questions, zero differences."'],
 ['09','3:50','Business','"About 1.3 million US dollars a year, an estimate."'],
 ['10','4:30','Close','"Answers you can prove. We want one pilot team. Scan to try it." Stop.'],
];
const ladder = [
 ['App stalls','Ishaan says the next line\'s first four words; Dev presses B (clip plays).'],
 ['Wrong state','Press R: restores the tamper copy, clears boxes, back to step 00. Never edits the real ledger.'],
 ['Network fails in the WorkBuddy beat','Play clip C and say in one clause that it is a recording.'],
 ['Running long','Cut in order: language flip, CodeBuddy line, Hy3 open-weight line, audit-row cut. Never cut the tamper or the identical refusals.'],
 ['Hy3 not live','Keep the default line: "quoted from the sources, with the same checks".'],
];
const cheat = page('Presenter cheat sheet', `<main class="ch">
<header><div class="brand"><span class="dot"></span>Presenter cheat sheet</div><div class="meta">Keys work only when armed (P) and not inside text fields. Source: RUN_SHEET.md, PRESENTER_KEYS.md</div></header>
<section class="chtop"><div><h2>Keys</h2><table class="keys">${keys.map(k=>`<tr><td><kbd>${esc(k[0])}</kbd></td><td>${esc(k[1])}</td></tr>`).join('')}</table></div>
<div><h2>Recovery ladder</h2><ol class="lad">${ladder.map(l=>`<li><b>${esc(l[0])}.</b> ${esc(l[1])}</li>`).join('')}</ol>
<p class="fine">A recording is never presented as live. If a judge interrupts: one sentence, then "let me finish this step, then I will come back."</p></div></section>
<h2>Steps 01 to 10 (Space for each; P gives step 00, the title)</h2>
<table class="steps"><tr><th>#</th><th>Time</th><th>Beat</th><th>Spoken cue</th></tr>${steps.map(s=>`<tr><td>${s[0]}</td><td>${s[1]}</td><td><b>${esc(s[2])}</b></td><td>${esc(s[3])}</td></tr>`).join('')}</table>
<p class="fine">Morning checks: re-run the proof numbers; Hy3 is wired but the live run is pending, so say "quoted from the sources"; platforms are mocks; data is synthetic. The grow beat (?grow=1) is a Q&amp;A walk-up only, never on the Space path. WorkBuddy question: What's the status of the database migration project and were there any blockers raised in Slack last week?</p>
</main>`);

fs.mkdirSync(path.join(here, 'html'), { recursive: true });
const jobs = [['leave_behind_A4', leave], ['judge_cards', judge], ['presenter_cheatsheet', cheat]];
// CHROMIUM_PATH (optional): use this Chromium binary instead of Playwright's own download
// (e.g. a container with a preinstalled browser). Unset = the previous behaviour.
const launchOpts = process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {};
const browser = await chromium.launch(launchOpts);
for (const [name, html] of jobs) {
  const f = path.join(here, 'html', name + '.html');
  fs.writeFileSync(f, html);
  const p = await browser.newPage();
  await p.goto(pathToFileURL(f).href);
  await p.evaluate(() => document.fonts.ready);
  await p.pdf({ path: path.join(outDir, name + '.pdf'), format: 'A4', printBackground: true, preferCSSPageSize: true });
  await p.close();
  console.log('wrote', name + '.pdf');
}
await browser.close();
console.log(JSON.stringify(N));
