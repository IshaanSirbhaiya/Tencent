# Round-2 judge-impact review (Fable), 30 Oct 2026, 11:40 SGT

Reviewer: Fable 5.1. Read-only; this file is the only output. Written under a 10-minute cut-off (the
laptop goes offline), so §2 and §6 are measured, the rest is read from the repo and the screenshots.

What I looked at: the running production build at http://127.0.0.1:3100 with headless Playwright
(`/present?present=1`, `P`, then `Space`; `D`, `L`, `B`, `S`, `Esc`; `/present/preflight`), the Act,
grow and deliverables screenshots in `docs/finals/screens/`, the finals cover, the three handouts
(`submission/finals/handouts_src/html/`), `RUN_SHEET.md`, `script.ts`, `controller.ts`, `deliver.ts`,
`DEMO_DAY_RUNBOOK.md`, `LIVE_INGEST_KIT.md`, `TENCENT_TOOLS_REPORT.md`, `CHANGELOG.md`, `DECISIONS.md`,
`FOUNDER_TODO.md`, `QA_BANK.md`, `FIX_LOG.md`, `fixlog.json`, `slides.json`, `eval_summary.json`,
`RESULTS.md`, `var/demo-day/preflight.json`, and my own `final_fable.md`.

## 0. Verdict in five lines

1. The fifteen fixes landed and show: Rachel's hero answer no longer carries "SYNTHETIC CASE FILE",
   step 3 re-signs Rachel in, the journey opens on the waterfall, the HUD is compact, `/present?present=1`
   lands on "Press P to start", full names from the start, clips A/B/C exist and the preflight board
   serves them. Act is the best new thing: a draft "built only from the sources this answer cited",
   "Not sent", the ticket needs a confirm, and `action.proposed / confirmed / executed` appear as sealed
   records in the journey chain. That is a real agentic step done the Walnut way.
2. **One new stage-breaker.** With demo features on, the presenter script is now **13 steps**: after Ken,
   `Space` goes to "The brain grows" on `/dev/graph?compare=ken.ext`. In my run that step took **18.6 s**
   to settle, and the next `Space` did **not** advance (HUD stayed on 03 / 13). The run sheet has no slot
   for it. If the stage laptop starts with the toggle on, the 5:00 dies at 1:10.
3. The numbers disagree across the materials: the run sheet, Q&A bank and `eval_summary.json` say
   **7,374** adversarial requests; the leave-behind, judge cards, cheat sheet and CHANGELOG say **0 / 5,958**.
   `slides.json` says "Six CodeBuddy sessions"; `TENCENT_TOOLS_REPORT.md` says nine. The printed cheat
   sheet says `S` = "Slides"; `S` is the Stage theme. With WorkBuddy/CodeBuddy experts judging, a
   number that differs between the slide and the handout costs more than the number earns.
4. Still missing the same three points as last time: Hy3 live (every footer still "Quoted from sources
   (no model)"; HUD dot now reads **"Model unknown · Ledger unknown"** after `P`, which is worse than
   "Model quoting"), the hallway test (`FIX_LOG.md` still a template, `tester_lines: []`), and clip C is an
   in-app stand-in, not WorkBuddy.
5. Score now about **77 / 100** (was 72). With the top-10 below and the in-progress items landing
   cleanly: **about 83**. With Hy3 live and one real tester line as well: **87 to 89**.

## 1. Scores by criterion

| # | Criterion | Was | Now | Likely once in-progress lands | Why it moved / what moves it |
|---|---|---|---|---|---|
| 1 | Impact & Relevance | 8 | 8 | 9 | Act answers Aspire's "what can we offer this customer" with the approved script as a ready reply. Deliverables (cited table from the pricing sheet) is the Aspire-shaped artefact; only if the Table kind ships clean |
| 2 | Human-Centered Design | 6 | 6 | 8 | Nothing human yet. The hallway test on Saturday plus one real `fixlog.json` line is still the cheapest 2 points in the review |
| 3 | AI Interaction | 5 | 6 | 7 (8 with Hy3) | Act with confirm and voice dictation (never auto-sends) are real interaction design. Still no model on any footer; HUD "Model unknown" |
| 4 | Technical Execution | 8 | 9 | 9 | Action records sealed in the same chain (`#1727 action.proposed … #1729 action.executed`); 1,040 backend tests in `eval_summary.json`; preflight board; write-ahead on deliverables designed one step stricter than Act |
| 5 | Feasibility | 7 | 8 | 8 | One-click kit, preflight, offline rung, Lighthouse kit. Cost-per-answer line is on the judge card (US$1k/yr) but I did not see it on the business slide |
| 6 | Demo & Storytelling | 7 | 7 | 8 | Clips exist and `B` is wired, but: 13-step path (§0.2), number mismatch (§0.3), clip C is not WorkBuddy, `B` at the title step showed "no recording" in my headless run (§2) |
| 7 | Innovation & Creativity | 8 | 8 | 9 | Live growth with "Ken's side: nothing" is the most original beat you have, as a Q&A walk-up; identical refusals and the tamper remain the two proofs |
| 8 | UX & Accessibility | 7 | 8 | 8 | One-row HUD with the key legend below, full names, `L` left no English on the page I flipped, Stage at 150 % reads well. "Ken can open none of the items on the left" on the grow page is a count sentence in the Stage view |
| 9 | Responsible AI & Ethics | 8 | 9 | 9 | Disclaimer out of the answer body; "Not sent. Copy it, or edit it here first"; "Recorded in the audit trail before it was shown"; demo-only features labelled "Demo" and off on deployments (D-24) |
| 10 | Overall Quality & Judge's Impression | 7 | 8 | 8 | One design system holds across Act, deliverables and grow. Loses a point to the inconsistencies in §5 |
| | **Total** | **72** | **77** | **83** | |

## 2. What the headless run measured (1280 x 800, Stage theme, live backend, 11:3x SGT)

| Step | Key | Time to settle | Seen |
|---|---|---|---|
| pre-P | `/present?present=1` | 1.2 s | "Press P to start", HUD "Presenter keys are on" (fix 9 landed) |
| 0 | `P` | 1.1 s | Title. HUD dots: **API ok · Model unknown · Ledger unknown** (two `401` on `/api/ib/ib/status` and one on `/auth/whoami` before sign-in). Last review this read "Model quoting · Ledger ok" |
| 1 | `Space` | **4.1 s** | Amy and Rachel, full names, "Quoted from sources (no model)". Rachel's first sentence now opens with a chip **"mlro.tan said"** then "Velquarry Trading Pte Ltd review update (synthetic case FC-2026-0147): …". No "SYNTHETIC CASE FILE" sentence (fix 2 landed), but the username chip and the "(synthetic case …)" parenthesis are still in the hero sentence |
| 2 | `Space` | 3.3 s | Ken, "Identical responses" marker present |
| 3 | `Space` | **18.6 s** | **"The brain grows: a new note"** on `/dev/graph?compare=ken.ext` (grow steps are on: HUD says 03 / 13). First fold: "Jordan can open 930 items · Ken can open 24", "Ken can open none of the items on the left"; the graph canvas sits below the HUD |
| 4 | `Space` | 1.0 s | **Did not advance**: HUD still 03 / 13, no `presenter-failed` text. Space on a failed step retries; `→` skips. On stage this is dead air with no message |
| `D` | | 0.9 s | No deliverables panel appeared (`presenter-deliver-answer` absent); the server flag or Leo's warm cache is not on in this stack |
| `←` x4 | | | Landed on `/present` with no HUD at all (state lost after the stuck step) |
| `B` | at that page | | Overlay opened with **"This step has no recording"** (`clip-missing`), though the preflight board says all three clips are served (HEAD requests were aborted). Check `B` on the stage laptop at every step; do not trust the board alone |
| `L` | | <1 s | No English strings left in the text I scanned |
| Preflight board | | | "NOT READY: 1 to fix" = screen 1280 x 800 (expected headless); 2 ambers = model extractive. Chain intact, 2,163 records, 0.4 s; every scripted ask 0.5 to 0.7 s |

Console: 3 errors (the 401s above), the rest WebGL software-fallback warnings from the graph (headless
only). Steps 1 and 2 are each about 1 s slower than on 30 Oct 05:00; the machine was busy with other agents.

## 3. Top 10 changes for the remaining two days, ranked by points per hour

| # | Change | Effort | Points | Where |
|---|---|---|---|---|
| 1 | **Keep the grow steps out of the Space path unless explicitly armed.** `setGrowSteps(demo.on)` fires whenever the server reports demo features. Gate it on `?grow=1` (or a presenter key, say `G`, that opens the grow beat on demand in Q&A) so the 5:00 is always the 10 steps the run sheet has. Also show a visible failure line when a step does not advance (I saw none) | 30 min | saves the demo | `components/presenter/PresenterRoot.tsx:63`, `script.ts setGrowSteps`, `controller.ts` |
| 2 | **One number set everywhere.** Decide 7,374 (all modes, `eval_summary.json`) or 5,958 and make the run sheet, Q&A bank Q1, leave-behind, judge cards, cheat sheet, CHANGELOG and proof slide agree. Fix `slides.json` "Six CodeBuddy sessions" to nine. Fix the cheat sheet's `S` row (Stage theme, not slides) and its step 8 wording. Rebuild the PDFs with `build_handouts.mjs` | 45 min | +1 (6, 10); avoids −2 | `RUN_SHEET.md:33,62`, `QA_BANK.md:15`, `public/finals/slides.json`, `submission/finals/handouts_src/html/*` |
| 3 | **Hallway test Saturday, one real line into `fixlog.json`.** Five people, 20 min each, protocol exists. Delete the [TESTER] line if it does not happen; never soften it | 2.5 h | +2 (2) | `HALLWAY_TEST_PROTOCOL.md`, `FIX_LOG.md`, `public/finals/fixlog.json` |
| 4 | **HUD dots must never say "unknown" on stage.** Fetch `/ib/status` after `signInMain` in `P`, or read the model and ledger state from the preflight JSON the board already uses. "Model unknown" next to "Quoted from sources (no model)" is the sentence a WorkBuddy judge will quote | 30 min | +1 (3, 6) | `PresenterBar.tsx:129`, `controller.ts start()` |
| 5 | **Rachel's hero sentence.** Render the Slack author as the person's name ("Rachel Tan said"), not `mlro.tan`; strip "(synthetic case FC-2026-0147)" from the first sentence (the footer already says synthetic); and make thread-first messages consistent (grow screenshot 07 shows a raw "jdoe:" prefix next to a styled "priya.sre said" chip) | 45 min | +1 (10) | `components/user/answerText.ts`, `components/compare/answerAdapter.tsx`, fixture `generate.py` |
| 6 | **WorkBuddy GUI retest on the stage laptop and record the real clip C.** The judges are WorkBuddy experts; W7 is the one open gap in `TENCENT_TOOLS_REPORT.md`. Replace the in-app stand-in; put the eight screenshots in `submission/02_workbuddy/finals/` | 1.5 h | +1 (3, 6) | `WORKBUDDY_GUI_TEST.md`, `public/clips/clipC_*.mp4`, `BACKUP_PLAN.md` |
| 7 | **Press `B` at every step on the stage laptop, on the demo browser profile.** My headless run got "no recording" on a step that maps to clip A. If it is the aborted HEAD check, make the overlay play without the HEAD. Then re-record A and B after the freeze (entry number changes) | 45 min | insurance | `components/presenter/ClipOverlay.tsx` |
| 8 | **Act as a prepared Q&A beat, not a 5:00 step** (see §4). Pre-warm Amy's Velquarry answer in `P`, add key `A` that signs Amy in, opens `/` with the cached answer and the Act row in view, then "Draft the reply". Say: "built only from the sources this answer cited; not sent" | 1.5 h | +1 (1, 3) | `controller.ts`, `deliver.ts` pattern, `components/user/ActSheet.tsx` |
| 9 | **Deliverables: ship the Table kind only (no model), behind `D`.** The mock-1 table with per-cell chips and "Copied cell by cell from [1], no model" is honest and finished-looking. Chart/Analysis/Summary/Slides need Hy3; leave them "Needs the model" rather than half-built. Make sure `IB_DELIVERABLES=1` is set by `demo_up.sh` (today `D` did nothing) | 2 h | +1 (1) | `docs/finals/deliverables/PLAN.md` §9, `scripts/demo_up.sh` |
| 10 | **Founder items that cost no engineering:** Hy3 activation (then `hy3_check.py`, eval re-run, every footer flips); fill Q&A Q23 (business model) or route it to Q24; `QA_BANK.md` Q7 Hy3 status; print the handouts after item 2 | 1 h + wait | +2 to +4 if Hy3 lands | `FOUNDER_TODO.md`, `MORNING_BRIEF.md` |

Not worth the hours now: Tencent RTC/ASR (one day, needs network on stage), file-upload ingest on stage
(unrehearsed reader path; keep it to Q&A with sample 1 or 5 of the kit), expanding the company world
(risks the eval numbers and the pre-warmed answers two days out).

## 4. Where Act, Deliverables and live growth belong

- **Act: Q&A, prepared, one key.** The run sheet is full and already loses its 10-s buffer to a 4-s
  step 1. Act is a strong answer to "can it do anything?" or "is this just RAG?": Amy, "Draft the reply",
  the approved script word for word, "Not sent", the three `action.*` seals in the journey. 15 s. Only put
  it in the 5:00 if a presenter step exists by Saturday 22:00 and three timed runs stay under 4:50.
- **Deliverables: Q&A, `D`, Table only.** Leo, "Can we offer multi-currency accounts to an Indonesian
  SME?", table with a chip per cell, Download .csv. It also answers the Aspire judge's business question.
- **Live growth: Q&A walk-up, never in the Space path.** It is your most original beat, but 18 s of graph
  load plus a post plus a Compare is 60 to 75 s by the kit's own estimate, and it needs the toggle on.
  Open `/dev/graph?compare=ken.ext` in the second tab *before* Q&A as Jordan (also the fix for the slow
  graph I flagged last time), run sample 1 (`shard3-resume`) only after a reset and `check_absent.py`.

**Best final run sheet:** the 10 steps as written, with these edits: delete the 简体中文 flip (3 s) and the
CodeBuddy sentence from 2:55 (the slide carries it); hold the Ken cards for the 2-s silence; say the
number on the screen for the entry; say "quoted from the sources, with the same checks" unless the
preflight board says Hy3 LIVE. Q&A order of prepared beats: Act (`A`) → Deliverables (`D`) → grow (tab 2).
Leave `/dev/journey/<req>` on screen between beats.

## 5. The three biggest stage risks now

1. **The 13-step script.** Demo features on → grow steps spliced after Ken → `Space` lands on a graph page
   that took 18.6 s here and then would not advance, with no failure text. Mitigation: item 1; and until it
   lands, Settings → Demo features **off** before `P`, and `→` rehearsed as the skip key.
2. **Claims that do not match across slide, handout and mouth, in front of Tencent-tool experts.**
   7,374 vs 5,958; six vs nine CodeBuddy sessions; `S` on the cheat sheet; "Model unknown" in the HUD next to
   "no model" footers; clip C "WorkBuddy" that is not WorkBuddy. Each is small; together they read as
   unfinished. Mitigation: items 2, 4, 6.
3. **Presenter state and the fallback keys.** After a stuck step, `←` dropped me on `/present` with no
   HUD; `B` showed "no recording" on a step that has a clip. On stage the recovery is `R` (confirm dialog),
   then `P`, which re-warms (1 to 2 s). Mitigation: items 1 and 7; rehearse `R` → `P` once per run; keep
   "Start Walnut Demo (offline)" built the night before.

## 6. What looks unfinished or AI-generated

- **Cover** (`cover_finals_1920x1080.png`): the brass padlocks and paper are good, but "Generated by Miora"
  is a large white watermark in the corner, the eyebrow says "DATABRIDGE BY WALNUT" (the product is
  "Walnut DataBridge" everywhere else), and the stage tagline "Answers you can prove." is not on it. Keep
  the watermark if the rules want Miora visible; fix the name.
- **Hero sentence:** "mlro.tan said" chip; "(synthetic case FC-2026-0147)" inline; raw "jdoe:" prefix on the
  grow answer. These are the only places the UI still looks generated.
- **Templates on show:** `FIX_LOG.md` "TEMPLATE, EMPTY", `tester_lines: []`, QA Q23 "TO FILL", Q7 "TO FILL".
- **Grow page in Stage:** "Jordan can open 930 items · Ken can open 24 … Ken can open none of the items on
  the left" is a count comparison on the stage screen; fine for Developer mode, not for the projector.
- **Cheat sheet:** `S` described as slides; step 8 "Back to the audit row"; otherwise the handouts are
  plain, sourced and dated, which is right.
- **What is finished and should not be touched:** the Act sheet copy ("Built only from the sources this
  answer cited. It is not sent."), the ticket confirm screen, the `action.*` seals in the journey, the
  deliverables table with a chip per cell, the preflight board's amber-with-reason rows, the "Just added ·
  Demo" feed with "appeared 2.8 s after you posted".
