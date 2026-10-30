# Round-3 judge-impact review (Fable), 30 Oct 2026, ~13:00 SGT

Reviewer: Fable 5.1 (cloud session). Read-only; this file is the only output. Branch `final_v1.2`. The
review started at `7fa6319` with the web-fix pass uncommitted; during it, `896013c` / `7e05cfa` (round-3
Haiku claims and fixes) and `19603c8` (round-3 Sonnet UX fixes: deliverables sheet, live feed, Add sheet,
engine refresh) landed, so the web code described here is HEAD `19603c8`. Still uncommitted at the end:
26 backend files (deliverables, uploads, live growth, `sim.py`, their tests, three STATUS/contract docs).

What I looked at: the web app in **mock mode** (`NEXT_PUBLIC_IB_MOCK=1`, `next dev` on 127.0.0.1:3161,
headless Chromium 1280 x 800 and 390 x 844) driven by the presenter keys and the user-mode pages; the
run sheet, `PRESENTER_KEYS.md`, `script.ts`, `controller.ts`, `deliver.ts`, `PresenterRoot.tsx`,
`GraphExplorer.tsx`, `AddDocumentSheet.tsx`, `DeliverSheet.tsx`, `ActSheet.tsx`, `answerText.ts`,
`personas.ts`; `LIVE_INGEST_KIT.md`, `deliverables/STATUS.md`, `live_growth/STATUS.md`, `QA_BANK.md`,
`REHEARSAL_CHECKLIST.md`, `BACKUP_PLAN.md`, `DEMO_DAY_RUNBOOK.md`, `LEAVE_BEHIND.md`, the three handout
HTML sources, `slides.json`, `fixlog.json`, `FIX_LOG.md`, `ORGANISER_UPDATE.md`, `STATUS.md`, `README.md`,
`INTERNAL_BRAIN.md`, `FEATURE_CATALOGUE.md`, `round2_dispositions.md`, `git log`.

**Mock mode is not the live stack.** Answers are fixtures, there is no Slack-handle prefix in Rachel's
fixture (so round-2 item 5 cannot be checked here), `/sim/upload-file` does not exist in the mock, and
the dev server compiles pages on first visit, so the step timings below are upper bounds, not stage
numbers. The first full run (§2.1 to §2.3, `D`, the upload) was on the working tree before `19603c8`; the
grow runs (§2.4) straddled it (the dev server hot-reloads); the code sites cited are re-checked at HEAD.
Hy3 is not live anywhere; nothing in this review assumes it is.

## 0. Verdict in five lines

1. The round-2 stage-breaker is gone from the 5:00: with no `?grow=1` the HUD counts `/ 10`, `P` then
   ten `Space` presses ran steps 00 to 10 with no failed step, and the dots read **API ok · Model quoting ·
   Ledger ok** from step 00 (the "Model unknown" HUD is fixed, in mock at least). The proof slide reads
   0 / 7,374, 0 / 400, 0 / 1,152 from `results.json`, no tester line, "Nine CodeBuddy sessions"; the
   handouts and the cheat sheet say the same.
2. **The grow walk-up is still not drivable by `Space` alone.** The `grow` step leaves the "Add a document"
   sheet open with focus on its Close button; the presenter ignores every key while a dialog is open
   (`controller.ts` `openDialog()`), so the next `Space` closes the sheet instead of advancing, the one after
   runs the post (which failed in one of my two runs and re-opened the sheet), and an `Esc` pressed when
   the sheet has already gone **hides the HUD**. This is the same "did not advance" you saw live in round 2,
   now with the cause. `19603c8` changed the Add button into a real sheet trigger and moved focus after a
   post, but not the guard or the Esc semantics. It is one hour to fix and it is item 1.
3. **`D` leaves Leo signed in.** After the Deliverables beat, `ArrowLeft` back to the journey (the page the
   run sheet says to leave up in Q&A) shows **"Request not found. There is no request with this id in your
   history."** as Leo. Item 2, 30 minutes.
4. The numbers are one set for the evals but **not for the tests**: the leave-behind and judge cards say
   "1040 backend and 124 browser tests"; `STATUS.md`, `README.md`, `INTERNAL_BRAIN.md`, `FEATURE_CATALOGUE.md`,
   `rubric/README.md` and the organiser email say **754** (commit 7bf3246). The organiser email, due
   tonight, also links `final_v1.0` while quoting the `final_v1.2` eval. Same class of problem as round 2,
   smaller, and in front of the same CodeBuddy judges.
5. Score now about **79 / 100** (was 77). Items 1 to 8 below landing cleanly: **about 83**. Plus a real
   hallway line: 85. Plus Hy3 live (founder only): 88 to 89.

## 1. Scores by criterion

| # | Criterion | Round 2 | Now | After items 1-8 | What moved it, what moves it |
|---|---|---|---|---|---|
| 1 | Impact & Relevance | 8 | 8 | 9 | The `D` table is the Aspire-shaped artefact and it looks finished (4 x 5 cells, a chip on every cell, "Copied cell by cell from [1], no model", Download .csv). It earns the point only once `D` then the journey works in Q&A without "Request not found" (item 2) |
| 2 | Human-Centered Design | 6 | 6 | 6 (8 with a real tester line) | `FIX_LOG.md` still "TEMPLATE, EMPTY", `fixlog.json` `tester_lines: []`, judge card 2 says "hallway-test report is still pending". Only the founder's Saturday changes this |
| 3 | AI Interaction | 6 | 7 | 7 (8 with Hy3) | Act v3.1 is honest end to end: "Not sent. Copy it, or edit it here first", the ticket needs a confirm that names the project ("Create ticket in DBMIG"), gated source links in the description, the unknown-outcome path. Every footer still "Quoted from sources (no model)"; the Analysis tab's wording implies a model ran (item 5) |
| 4 | Technical Execution | 9 | 9 | 9 | Unchanged strength: sealed `action.*` and `deliverable.*` records in the same chain, the tamper table in Stage reads well ("Chain broken at entry 35", "As expected") |
| 5 | Feasibility | 8 | 8 | 8 | Business slide now carries "US$100,000 a year to run" and "7.0 min" break-even with the formula; one-click kit unchanged |
| 6 | Demo & Storytelling | 7 | 8 | 9 | Grow off the Space path, one eval number set, HUD dots right, clip B plays from `B`. Loses to: the grow walk-up keys (item 1), `D` then "Request not found" (item 2), the 简体中文 flip on a Developer page that announces "开发者模式仅提供英文" (item 8), clip C still an in-app stand-in |
| 7 | Innovation & Creativity | 8 | 8 | 8 | Live growth with "Ken's side: nothing" is still the most original beat; in mock the note appeared "2.5 s after you posted" on Jordan's side and Ken's feed stayed empty. Unusable until item 1 |
| 8 | UX & Accessibility | 8 | 8 | 8 | 390 px Act sheet: sticky confirm row, no tour card over it, `lang="en"` on the server text, focusable description region. The upload tab asks for a Drive folder as an id ("fd-3"), which reads unfinished |
| 9 | Responsible AI & Ethics | 9 | 9 | 9 | Mock labels default to "no model" everywhere (`fixtures.ts`, `/ib/status`, deliverables); login notes no longer hint at restricted content; `/present/preflight` 404 off the loopback |
| 10 | Overall Quality & Judge's Impression | 8 | 8 | 9 | One design system holds across Act, Deliverables, grow and the slides. Costs: 754 vs 1040 tests, the organiser link, 26 backend files still uncommitted while the suites are the proof |
| | **Total** | **77** | **79** | **83** | |

## 2. What a judge sees in each beat, and its stage risk

### 2.1 The 5:00 path (default tab, no `?grow=1`), measured in mock

| Step | Settle | Seen | Risk |
|---|---|---|---|
| pre-P | 3.2 s (first compile) | "Press P to start", "Presenter keys are on", title card with the four names | none |
| 00 `P` | 2.9 s | Title; HUD **API ok · Model quoting · Ledger ok** at once (round-2 #4 fixed); `/ 10` | none |
| 01 | 6.1 s (compile) / 2.7 s second run | Amy left, Rachel right, both "Quoted from sources (no model)". Rachel's fixture has no handle prefix, so the "Rachel Tan said" chip (round-2 #5) is unverified here; the code (`answerText.ts` `speakerName`, `displayAnswer`) is right and needs one look on the live stack | low |
| 02 | 2.2 s | Ken, "The two responses are identical." marker | none |
| 03 | 0.2 s | Sources sheet on [2], highlight "director could not explain the source of the third-party deposits"; `Esc` closes it and only it | none |
| 04 | 4.1 s (compile) | Journey as Maya, waterfall, request id, "LATENCY 336 ms"; `B` here plays clip B (video element present, no "no recording") | none in mock; re-check `B` at every step on the laptop (round-2 #7) |
| 05, 06 | 0.1 s each | Verify intact; tamper table: "Edit one old record … Chain broken at entry 35 … As expected". The mock shows the multi-attack table rather than one Gate-2 edit; the live stack's wording was reviewed in round 2 | none |
| 07 | 0.1 s | WorkBuddy off-app card with the exact question and "Copy question" | the real WorkBuddy and clip C are laptop-only (§4) |
| 08 | 0.1 s | Proof: 0 leaks in 7,374; 0 differences in 400; 0 hard failures in 1,152; sources "results.json · run 30 Oct 2026"; no tester line; "Nine CodeBuddy sessions" | none |
| 09 | 0.1 s | "≈ US$1.3M a year (estimate)", "Exact: US$1,283,400", break-even "7.0 min", inputs, "US$100,000 a year to run" | none |
| 10 | 0.1 s | Close: tagline, ask, QR, github URL in text, names | none |
| `L` at step 07 | <1 s | HUD, nav and dots flip; **the page shows "开发者模式仅提供英文" (Developer mode is English only) and stays English** | the spoken line at 3:12 is "the UI speaks the room's languages" on a page that says it does not. Item 8 |

### 2.2 Q&A walk-up `D` (Deliverables)

- `D` from step 10: overlay in **1.4 s** from the warm-up cache. Leo's answer behind, the sheet in front:
  "From this answer · Built only from this answer and the sources you can still open", tabs Table / Chart /
  Analysis, "Table from your sources · Draft for your review · Copied cell by cell from [1], no model", 20
  cells each with a `[1]` chip, Copy, Download .csv, Regenerate, "Cited in this table · SME multi-currency
  pricing sheet · Pricing & Offers", "Recorded in the audit trail before it was shown". This is the finished
  thing; do not touch it.
- Chart: "FX margin by market · Charted from the table in [1], no model", three bars 0.35 / 0.35 / 0.4 with
  the data table under it. Good.
- Analysis: **"This needs the model (Hy3). It didn't produce anything that passed the source check, so
  nothing was written."** The server returns `fallback: "no_model"` when no chain exists
  (`service.py:317`), but the sheet shows the same sentence for `no_model` and `nothing_passed`
  (`DeliverSheet.tsx:399`, `lib/i18n/deliver.ts:62,130`). With Hy3 not live a judge who clicks Analysis reads that a
  model ran and failed. Item 5. (Since `19603c8`, `D` with nothing cached announces "Deliverables are not
  ready for this answer." instead of doing nothing: good.)
- No "Download .pptx" in mock (the mock lists CSV only); the live button has not been clicked in a browser
  per `deliverables/STATUS.md` §5.3.
- `Esc` closes the overlay and the HUD is back on step 10. **Then the main session is Leo** (`openDeliverables`
  switches to `leo.ops` and nothing switches back). Six `ArrowLeft` to the journey step re-open its URL
  only (`script.ts` back path never re-signs Maya): **"Request not found"** with "Leo — 产品与运营" in the
  top bar (screenshot `A_zh.png` in my scratch). Item 2.

### 2.3 Q&A walk-up: Act (no key; user mode)

- Amy: `/login` → Amy → the Velquarry starter → Ask (2.9 s) → "Draft the reply" (1.2 s). Sheet: "Draft for
  your review · Built only from the sources this answer cited. It is not sent.", "Uses the approved wording,
  word for word", the three cited sentences each with `[1]`, "Copy / Edit before sending", "Not sent. Copy
  it, or edit it here first.", one source, "Recorded in the audit trail before it was shown". At 390 px the
  same, no tour card over it. "Create a ticket" as Amy: "A follow-up ticket can't be created from this
  answer. Nothing was changed." (one sentence, no reason: rule 4 kept).
- Jordan: the migration question → "Create a ticket": project select "Database Migration (DBMIG)", summary,
  type Task, description with "(each link opens only for people allowed to open that source; for anyone
  else it is not found)" and two `/api/ib/ib/open/<id>` links, two linked sources, **"Create ticket in
  DBMIG"**, "Created in your name. Recorded in the audit trail first." → "Ticket DBMIG-901 created … You
  are the reporter … Open DBMIG-901". Sticky confirm row at 390 px.
- Risk: it is four clicks and a persona switch from the presenter state (there is no `A` key; round-2 #8
  not built, fine). The HUD stays on the user page ("— / 10 Press P to start" bottom-left in my 1280 shot
  when not started). Budget 25 s, say "built only from the sources this answer cited; not sent", and have
  the Amy starter chip ready. Not in the 5:00.

### 2.4 Q&A walk-up: the brain grows (`?grow=1` tab)

Measured twice (signed in as Jordan first, then `/present?present=1&grow=1`, `P`, `Space` x 2):

| Press | Result |
|---|---|
| `Space` (03 grow) | 3.6 s / 6.1 s. `/dev/graph?compare=ken.ext`, HUD `03 / 13`, "The brain grows: a new note". The Add sheet opens on the right with the note filled and `#db-migration` chosen; **focus is on the sheet's Close button**; the graph page under it shows "Jordan can open 867 items · Ken can open 24" and, once loaded, "Ken can open none of the items on the left" |
| `Space` | **Does not advance.** `openDialog()` sees the sheet, the presenter ignores the key, the focused Close button takes it: the sheet closes, HUD stays 03 |
| `Space` | 04 grow-post runs. Run 1: "Posted to Slack · #db-migration", Jordan's feed "your note · appeared 2.5 s after you posted", Ken's feed empty (the beat works). Run 2 (while `19603c8` was hot-reloading in): **"This step did not finish. Space tries again, → skips, B plays the recording."** and the sheet re-opened with focus on Close (the post returned false; not the mock's 5-per-minute limit, which was not reached). Re-run it on HEAD before trusting either result |
| `Esc` after the sheet had already closed | **hides the HUD** (`controller.ts` `case 'escape'`): the driver now has no bar and no step number |
| fresh tab, nobody signed in, `?grow=1` | HUD `/ 10`, no grow steps, no message: `useDemoFeatures` reads `/ib/status`, which is 401 before sign-in, so `server` is null and the steps are never spliced (`demo.ts:26-29`, `PresenterRoot.tsx:63-65`) |

So the walk-up today needs: a signed-in browser before the tab is opened, `Space`, `Space` (closes), `Space`
(posts), `Space` (closes), `Space` (asks), and never `Esc` unless a sheet is visibly open. None of that is in
`PRESENTER_KEYS.md` §6 or the run sheet. The e2e tests do not catch it: `grow.spec.ts` clicks the buttons and
presses `Escape` itself; `presenter.spec.ts` only checks `/ 13` vs `/ 10`.

### 2.5 Q&A walk-up: an upload (sample from `LIVE_INGEST_KIT.md`)

- Mock: `/dev/graph?add=1` as Jordan opens the sheet in 3.9 s; "Upload a file" tab: file input, **"Drive
  folder" as a free-text id (`fd-3`)**, title, Upload, the six sample files as download links. Upload of
  `vendor-sla-tracker.csv` → **"The service is unavailable. Please try again."** (no `/sim/upload-file` in the
  mock). So the offline fallback build ("Start Walnut Demo (offline)") cannot do the upload beat at all;
  the Slack-note beat works offline.
- Live: the kit's own §8 says no "after" answer, pulse, time or reader extraction was verified. The best
  single-persona pick is **sample 11** (`leo.ops` uploads the CSV to Vendor share; Leo asks; Ken also gets the
  cited answer, so it is not "hide everything from Ken"); it also follows `D` (Leo is already signed in).
  Sample 7 needs `admin.it` to upload and Priya to ask (two sign-ins; skip on stage).
- Risk: unrehearsed reader path on stage, the folder-id field on the projector, and a 25 s budget the kit
  itself puts at 60 to 75 s. Keep it for a judge who asks "what about a file?", one sample, after a reset
  and `check_absent.py`, timed once in rehearsal.

## 3. Top 10 changes, ranked by points per hour

| # | Change | Effort | Points | Where |
|---|---|---|---|---|
| 1 | **Make the grow walk-up drivable by `Space`.** Either (a) exempt the Add sheet from the presenter's dialog guard while a grow step owns the page, or (b) have `grow` leave the note visible but focus the page (not the Close button) and have `grow-post` close the sheet after a success so the feed is what the room sees; and make `Esc` never hide the HUD while the script is started (give hide its own key or require `?`+`Esc`). Add a keys-only e2e: `P`, `Space` x 5 through `grow-ask` with no `Escape` press. Then write the key sequence into `PRESENTER_KEYS.md` §6 and say the tab must be opened after a sign-in exists | 1 h | saves the walk-up (+1 on 6, 7 when it is used) | `components/presenter/controller.ts:244` (`openDialog`), `:331` (`escape`), `components/graph/GraphExplorer.tsx` (`usePresenterHandler('graph.addNote')`), `components/sim/AddDocumentSheet.tsx`, `components/sim/demo.ts:20-33` (re-fetch after sign-in), `tests/e2e/grow.spec.ts`, `PRESENTER_KEYS.md` §6 |
| 2 | **`D` must put the session back.** `closeDeliverOverlay()` restores whoever was signed in before `D` (the warm-up already does this), or the journey step's back path re-runs `signInMain('maya.compliance')`. Test: `D`, `Esc`, `ArrowLeft` to the journey shows the waterfall, not "Request not found" | 30 min | +1 (6) | `components/presenter/deliver.ts:134-156`, `script.ts:136-149`, `tests/e2e/presenter.spec.ts` |
| 3 | **One test count, and the organiser link.** Decide 1,040 (cloud, Linux, this branch, `eval_summary.json`) or 754 (Windows, 7bf3246) and make `STATUS.md:39`, `README.md:163`, `INTERNAL_BRAIN.md:332`, `FEATURE_CATALOGUE.md:20,530`, `rubric/README.md:21`, `judge_cards.html:71`, `leave_behind_A4.html:78` and `ORGANISER_UPDATE.md:43` agree; point the email at the branch the judges will open (`final_v1.0` is not in this clone) with that branch's numbers. Rebuild the two PDFs if the handouts change | 30 min, due 23:59 tonight | avoids −1 (10) | files above, `submission/finals/handouts_src/build_handouts.mjs` |
| 4 | **Commit the backend tree and run the suites once.** The web-fix pass landed during this review (`7e05cfa`, `19603c8`); 26 backend files are still uncommitted (`deliverables/*.py`, `uploads.py`, `live.py`, `sim.py`, `engine_refresh.py`, `extract_files.py`, nine test files, `deliverables/STATUS.md`, `live_growth/STATUS.md`, `contracts/uploads.md`, `SECURITY.md`). The laptop's `git pull` gets none of it, and the "1,040 passed" claim is only as good as the suite on the committed tree | 30 min + the 13-minute suite | insurance for items already paid for | `git status` |
| 5 | **Deliverables "needs the model" wording when there is no model.** Branch on `fallback === 'no_model'`: "Needs the model (Hy3). It is not connected on this build; the table and chart are built from the sources." Keep the current sentence for `nothing_passed` | 20 min | +1 (3, 9) when a judge clicks Analysis | `components/user/DeliverSheet.tsx:399` (the `needs_model` notice; `:496` already knows `no_model`), `lib/i18n/deliver.ts:62,130`, `tests/e2e/deliverables.spec.ts` |
| 6 | **Write the three walk-ups down where the team reads.** `QA_BANK.md`: a trigger question for Act ("Can it do anything?" / "Is this just RAG?") and for grow ("What happens when something new is posted?") with the exact key or click sequence and a 25 s line each; Q10 already ends "Dev, D." `REHEARSAL_CHECKLIST.md`: "`D` then `R` once", "open the `?grow=1` tab after sign-in, before Q&A; reset and `check_absent.py` first", "Esc only when a sheet is visible". `DEMO_DAY_RUNBOOK.md` §4 keys table lacks `D`, `→` and `2` | 45 min | +1 (6) | `rubric/demo_story/QA_BANK.md`, `REHEARSAL_CHECKLIST.md`, `demo_day/DEMO_DAY_RUNBOOK.md:49-60` |
| 7 | **Hallway test Saturday, one real line into `fixlog.json`** (founder). Otherwise delete the [TESTER] sentence; never soften it | 2.5 h | +2 (2) | `rubric/hcd_ux/FIX_LOG.md`, `public/finals/fixlog.json`, `RUN_SHEET.md:65` |
| 8 | **Move or cut the 简体中文 flip.** On `/dev/audit` the page stays English and shows "开发者模式仅提供英文"; the line claims the opposite. Either cut it (it is already first in the cut order) or flip `L` on step 01/02 (`/compare`, a user page) and say the line there | 10 min | avoids a visible contradiction (6, 8) | `RUN_SHEET.md:63`, §4; `components/shell/AppShell.tsx:245-254` |
| 9 | **Upload beat: decide and rehearse once, live.** Sample 11 or nothing. Show the folder by name, not `fd-3` (a select from the person's own Drive map, like the channel list). Time it, write the number on the card, keep the offline build out of it | 1 h (laptop for the live half) | +0 to +1 (7) | `components/sim/AddDocumentSheet.tsx:187-230`, `LIVE_INGEST_KIT.md` §5 sample 11 |
| 10 | **Founder and laptop items, unchanged from round 2:** Hy3 activation then `hy3_check.py` and the eval re-run (every footer flips, Q7 fills); WorkBuddy GUI retest and a real clip C; `B` at every step on the stage profile; one look at Rachel's hero sentence live (chip reads "Rachel Tan said", no "(synthetic case …)"); QA Q23 | founder | +2 to +4 if Hy3 lands | `FOUNDER_TODO.md`, `CLOUD_HANDOFF.md` "Questions for Ishaan" |

Not worth the hours now: a presenter key for Act; the Summary deliverable; Tencent RTC/ASR; any change to the
world or the eval.

## 4. The three biggest stage risks now

1. **A walk-up that stalls in front of the judge.** Grow: `Space` closes the sheet, the post can fail, `Esc`
   removes the HUD, and a fresh tab silently has no grow steps. Mitigation until item 1 lands: do not offer
   the grow beat; if a judge asks, say it in one sentence and show the "Just added" feed from the rehearsal
   tab. `D` is safe to offer (1.4 s, cached) if item 2 lands or Dev presses `P` again afterwards instead of
   `ArrowLeft`.
2. **Claims that differ between the handout and the repo**: 1040 vs 754 tests; an organiser email that links
   one branch and quotes another's eval; clip C labelled WorkBuddy that is not. Items 3 and 10.
3. **The laptop does not have the whole build.** The web fixes are committed now; the backend changes
   behind the deliverables, uploads and live-growth claims are not, and neither is a suite run on the
   committed tree. Item 4.

## 5. What still looks unfinished or generated

- **Templates on show:** `FIX_LOG.md` "TEMPLATE, EMPTY" with five "TO FILL" rows; `fixlog.json` empty; QA Q7
  still ends "Hy3 live on TokenHub: TO FILL (yes or not yet)" after the Haiku rewrite, Q23 "TO FILL by the team"; `LEAVE_BEHIND.md` still has "TO FILL" for the
  screenshots, usability and the stack line while the built HTML has moved on (the markdown is now the
  stale twin of the handout).
- **Deliverables Analysis tab** says the model produced nothing that passed, with no model connected.
- **"Request not found"** after `D` on the page the run sheet leaves up for Q&A.
- **Upload sheet:** "Drive folder" typed as `fd-3`; "The service is unavailable" on the offline build.
- **Grow page in Stage:** "Jordan can open 867 items · Ken can open 24 … Ken can open none of the items on
  the left" is still a count comparison on the projector (round-2 §6; Developer mode, so defensible, but
  not for the room).
- **Developer pages in 简体中文** show the "English only" banner; the run sheet flips `L` there.
- **Clip C** is still the in-app stand-in labelled WorkBuddy in `BACKUP_PLAN.md`.
- **Finished, do not touch:** the Act draft and ticket sheets (copy, confirm button, created state, the
  one-sentence refusal for Amy), the deliverables table and chart, the proof and business slides, the HUD
  with its three dots and key legend, the tamper table, the login page notes after the rule-4 trim.
