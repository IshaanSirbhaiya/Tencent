# Final judge-impression review (Fable), 30 Oct 2026

Reviewer: Fable 5.1, as design director and pitch coach. Read-only; this file is the only output.

What I looked at: the production build at http://127.0.0.1:3100 against the live backend (jdoe Ask and
Developer pages; Amy, Rachel and Ken through Compare; Maya on journey, audit Verify, the sandbox tamper
and the slides; the presenter path `/compare?present=1`, `P`, then `Space` through all ten steps, plus
`2`, `Esc`, `L`, `S`, `B`); 14 of the 97 live screenshots in `docs/finals/screens/live/`; `RUN_SHEET.md`,
`PRESENTER_KEYS.md`, `QA_BANK.md`, `BACKUP_PLAN.md`, `REHEARSAL_CHECKLIST.md`, `LEAVE_BEHIND.md`,
`QA_LIVE.md`, `MORNING_BRIEF.md`, `DECISIONS.md`; `rubric/README.md`, `README.md`, `INTERNAL_BRAIN.md`;
`evals/internal_brain/RESULTS.md`, `hcd_ux/FIX_LOG.md`, `public/finals/*.json`; and my own
`plan_fable.md` from this morning.

One caveat on method. The desktop app's browser profile was shared with another agent while I tested,
so cookies and `localStorage` changed under me twice (the main session flipped to `jdoe`, then `ken.ext`;
the theme flipped to light). I re-ran anything that could have been affected. Where a finding depends
on session state I say so, because the same thing can happen on stage with a second window.

## 0. The verdict in five lines

1. The plan was followed. Every P0 from `plan_fable.md` §8 exists and runs live: the story opens on
   Aspire's question, Ken's two refusals are byte-identical with a marker, the sheet shows the passage
   checked live, the journey has real milliseconds and three seals, the tamper runs on a copy and
   breaks at an exact entry, the slides are app-rendered with a QR, and the whole thing is driven by
   `P` and `Space`. The design is one system: one accent, Inter, tabular figures, no template chrome.
2. Two things on the hero screens would make a judge frown: the second sentence of Rachel's answer is
   the fixture's own disclaimer ("SYNTHETIC CASE FILE: … every name and number is made up"), and the
   tamper on screen ("Changed who made record 729") is not what Mehul says ("one word, denied to
   allowed").
3. The slowest live step is the tamper: about 3.2 s on the server plus render, with the broken-entry row
   below the fold behind the HUD. Everything else lands in 1 to 3 s.
4. Three things the rubric will score that are not there yet: Hy3 live (every answer says "Quoted from
   sources (no model)"), the hallway test (`FIX_LOG.md` is a template, the slide's tester line is
   empty), and the three fallback clips (`public/clips/` does not exist; `B` shows "no recording").
   All three are already on the Friday to Monday list; they are the biggest points still on the table.
5. As it stands: about **72 / 100**. With the fifteen fixes below: about **80**. With Hy3 live and one
   real tester line as well: **85 to 87**.

## 1. Scores by criterion, as it stands now

| # | Criterion | Now | What I saw | What raises it 1 to 2 points by Monday |
|---|---|---|---|---|
| 1 | Impact & Relevance | 8 | Opens live on the Velquarry question; Amy gets the script, Rachel the case, both cited; the tipping-off line; business slide with inputs and "Estimate" | Say the number the slide shows ("1.28 million", not "1.3"); one spoken clause that Amy's answer *is* the approved customer script, so the judge connects it to Aspire's "what can we offer this customer" |
| 2 | Human-Centered Design | 6 | Persona cards with "Can open" lines; plain refusal with "You could try"; sheet with version and checked-live time; `FIX_LOG.md` is empty | Run the Saturday hallway test (5 people, 2 hours), fill one real row, paste its sentence into `public/finals/fixlog.json` → the proof slide renders it. This is the cheapest 2 points in the whole review |
| 3 | AI Interaction | 5 | Every answer footer reads "Quoted from sources (no model)"; journey model card "Extractive (no model) · Compose 4 ms"; HUD dot "Model quoting". Honest, but a judge scoring AI interaction sees no model | Activate TokenHub (MORNING_BRIEF step 1). If it stays down, WorkBuddy is the live Hy3 beat; say so once, and drop every "Hy3 wrote the text" line ([HY3]). Re-run the eval with Hy3 so the slide's "run date" is Monday |
| 4 | Technical Execution | 8 | Journey: real ms per stage, Gate-2 verdicts with reason codes, three seals with seq; audit Verify with external head; tamper with eight attacks, one honestly "Not detectable"; 0 console errors | Scroll the journey to the waterfall on open (fix 4); show the three record hashes next to the seal ticks; speed up or pre-warm the tamper call (fix 1) |
| 5 | Feasibility | 7 | Business slide computes from `value_defaults.json`; honest limits in `INTERNAL_BRAIN.md`; Trust centre names the tests | Add the cost-per-answer line to the business slide (`COST_PER_ANSWER.md` has it) and a three-step pilot line; both are already in the folder, just not on screen |
| 6 | Demo & Storytelling | 7 | Presenter mode works end to end with no mouse; slides are good; but the run sheet and the screen disagree in three places (fixes 1, 6) and no clips exist for `B` | Fix the three mismatches, record clips A, B, C on the frozen build, three timed runs. Each is in the checklist; the mismatches are not |
| 7 | Innovation & Creativity | 8 | Two identical refusal cards with "Identical responses · Same text, same sources"; tamper on a copy with the stated window named | Make the marker read as proof: "Same text, same sources, same length" or show the two response hashes equal. Two seconds of silence after "prove", as the sheet says |
| 8 | UX & Accessibility | 7 | Keyboard-only path; `L` flips to 简体中文 in under half a second; settings for size, motion, contrast; Stage theme at 150 % | Shrink the HUD (fix 8); hide the mode switch and whoami on Compare in Stage (fix 7); translate the strings that stay English after `L` (fix 14) |
| 9 | Responsible AI & Ethics | 8 | No counts, no hints, same shape; Trust centre with framework rows; "Demo data: synthetic Company A" in the HUD; limits stated | Move the synthetic disclaimer out of the cited answer body (fix 2); the Trust centre's eval row says "re-run pending": re-run and update |
| 10 | Overall Quality & Judge's Impression | 7 | One design system, calm, dense where it should be; the story is the right one | The fifteen fixes; most are copy and scroll positions, not features |

## 2. Run sheet timing reality (presenter path, production build, live backend)

Measured from the key press with `performance.now()` and screenshots at 1 s intervals. Viewport 1280 x 800
with the Stage theme (150 % root), which is what a 1920 x 1080 projector shows.

| Step | Key | What appears | Time to final state | Verdict |
|---|---|---|---|---|
| 0 | `P` | Sign-in as Rachel, title slide, HUD with three dots (API ok · Model quoting · Ledger ok) | about 2 s | OK. The spec's fourth dot (WorkBuddy) is not there |
| 1 | `Space` | Both columns: "Signing in as Amy and asking…", staged status line, then two cited answers | 2.5 to 3 s | OK |
| 2 | `Space` | Ken's two refusals, "Identical responses" marker | 2.5 to 3 s | OK |
| 3 | `Space` | Returns to Amy/Rachel from cache and opens source [2] itself (no `2` needed) | 1.2 to 2.1 s | OK **only if the main session is Rachel**. When it was not, the sheet said "This source isn't available to you right now" (see risk 2) |
| 4 | `Space` | Journey as Maya: skeleton at 1 s, data at 2.5 to 3.4 s | 2.5 to 3.4 s | Borderline (spec: under 2 s). First fold shows only the metadata card; waterfall and seals need two scrolls |
| 5 | `Space` | Audit, scrolls to Ledger integrity, Verify runs: "Chain intact · 1,591 records · Matches published head" | about 2.5 s | OK |
| 6 | `Space` | "Copying the ledger and attacking the copy…" for the whole wait; `POST /audit/tamper-demo · 3,178 ms` | about 4 s | **Slow.** The "Chain broken at entry 729" row is the second row of an eight-row table, below the fold behind the HUD |
| 7 | `Space` | "Switch to WorkBuddy" card with the question and "Copy question" | under 1 s | OK (WorkBuddy itself not tested here) |
| `L` | | Whole shell and audit page in 简体中文, back on second press | under 0.5 s | OK; some strings stay English (fix 14) |
| 8 to 10 | `Space` | Proof, Business, Close slides | under 0.5 s each | OK. QR renders with the URL in text under it |
| `B` | | Black overlay "RECORDING · NOT LIVE": "This step has no recording" | instant | **No clips exist yet** (`public/clips/` missing). Planned for Monday after the freeze |
| `S` | | Stage theme off and on | instant | OK |
| Q&A: `/dev/graph` | | "Checking each item with its source…" | 8 to 16 s to a drawn canvas (Ken's 24 items took 10 s) | **Slow.** Not in the five minutes, but if a judge asks for the graph this is dead air |

Total in-app time with no speech: about 25 s for steps 0 to 10, well inside the 100 s acceptance test.
The spoken script is the pacing, not the app, except at step 6.

## 3. The top 15 polish fixes, ranked by judge impact over effort

1. **Tamper step: make the screen match the line, and bring the break into view.** The run sheet has
   Mehul say "I change one word in a copy of the log, 'denied' to 'allowed'". The screen says "Edit one old
   record: Changed who made record 729 (ib_event.sub) without re-sealing it." Either change the
   `edit-record` scenario in `databridge/apps/internal_brain/databridge_ib/audit/tamper.py` (line 208) to
   flip a `retrieval.decided` verdict and describe it as "Changed one decision in record N from denied
   to allowed", or change the spoken line to "I change who made one record in a copy of the log". Then in
   presenter step `tamper`, scroll the `edit-record` row into view (it is the only row Mehul reads) and
   put the "Your real audit log was not modified" banner after the table, or make it one line. The
   3.2 s call: run the tamper once during `P`'s warm-up so the OS file cache is warm, and show a
   progress line that names the attack being run rather than one static sentence. Files:
   `components/presenter/script.ts`, `components/audit/*Tamper*`, `RUN_SHEET.md` 2:20 to 2:35.
2. **Take the synthetic disclaimer out of the hero answer.** Rachel's answer, sentence 2, cited [2]:
   "SYNTHETIC CASE FILE: Velquarry Trading Pte Ltd is a fictional customer invented for this demonstration
   estate; every name and number is made up." It is also the first line of the passage in the sources
   sheet at step 3. The footer already says "Demo data: synthetic Company A" (rule 7 stays satisfied).
   Move the disclaimer paragraph to the end of page `confluence:cf-1201` (and its siblings) in
   `databridge/fixtures/enterprise/generate.py`, regenerate `world.json`, restart with `--fresh`; or
   have the extractive narrator skip sentences that start with "SYNTHETIC". Also strip the raw Slack
   author prefix from the first sentence ("mlro.tan: Velquarry Trading Pte Ltd review update …"): the
   handle reads as a bug on stage. `components/user/answerText.ts`.
3. **Step 3 must not depend on the cookie.** `CompareSourceSheet.tsx` fetches the live passage only when
   the main session is the column's person; otherwise it shows "This source isn't available to you right
   now", which on stage looks like the product failing at its hero moment. The presenter signs Rachel in
   at step 0, but anything that changes the main cookie in between (a second window opened as Maya,
   a Sign out, a teammate's tab) breaks it. In `script.ts` step `citation`, call
   `ctx.signInMain('mlro.tan')` before `compare-open-citation` (idempotent, about 100 ms). And rewrite
   `BACKUP_PLAN.md` "a second browser window is pre-opened at each step": one window only, or a
   separate browser profile for the spare.
4. **Journey step opens on the waterfall.** At 150 % the first fold is the request card (Request, Asked
   by, Channel, When, Latency, Policy, Directory epoch, Gate-1 policy). Mehul's line is about the
   waterfall, the model and the three seals, which sit two scrolls down. In presenter step `journey`,
   scroll to the Waterfall card after load, or in Stage collapse the metadata card to two rows
   (question, asked by, latency). Also show the first 12 hex of each record hash beside the seal ticks
   (`#1505`, `#1508 · #1509` are 9 px labels now); the sheet promises "sequence and hash".
   `components/journey/Waterfall.tsx`, `script.ts`.
5. **Record clips A, B, C and test `B` on every step.** `public/clips/` does not exist; `B` shows the
   honest "This step has no recording" card. The plan says Monday after the freeze; keep that, and add
   to the rehearsal checklist "press `B` at steps 1, 4, 6 and 7 and watch the clip start under 3 s".
6. **Run sheet copy that the screen contradicts.** (a) 0:15 to 0:45 "Classification pill on Rachel's
   ('Internal · FinCrime')": there is no pill; delete the cue or add a `Badge` from the strictest cited
   label (`components/ui/badge.tsx` already describes this use). (b) 1:10 "Dev: 'Space, then two'":
   `Space` opens [2] by itself; say "Space". (c) 2:28 "[ENTRY]": the number will be 729-ish, not 2,117;
   fine, but rehearse reading it. (d) 3:12 the newest audit row appears on window focus
   (`?latest=1`), not on a key; say so in `PRESENTER_KEYS.md` §6 step 8. (e) The WorkBuddy question in
   the HUD card is the `personas.ts` string; `RUN_SHEET.md` 2:40 has the shorter one; pick the HUD's.
7. **Stage header on Compare.** During Amy vs Rachel the header shows "User | Developer" and
   "Rachel — MLRO (FinCrime) · mlro.tan" top right. A judge asks "so who is signed in?". Add
   `data-stage-hide` to the mode switch and to the whoami on `/compare` only (keep whoami on
   `/dev/journey` and `/dev/audit`, where "Maya — Compliance officer" is the point).
   `components/shell/ShellControls.tsx`, `components/presenter/stage.ts`.
8. **HUD height.** The bar is about 165 px of a 720 px stage (two rows: step and dots; then eight key
   hints and the footer). In Stage, collapse to one row (step number, title, next, three dots, "Demo
   data: synthetic Company A") and show the key legend only for 3 s after `P` or on `?`.
   `components/presenter/PresenterBar.tsx`.
9. **The screen before `P`.** `/compare?present=1` shows "Jordan Doe vs Ken" with the DB-migration
   question and the normal dark shell. If the projector is live before the first word, that is the
   first thing the room sees. Make `?present=1` land on `/present` (title slide, Stage on) with the HUD
   saying "Press P to start", or pre-fill `SPEC_PEOPLE`.
10. **Names that change mid-step.** Column headers read "Amy / Rachel / Ken" before the answer and
    "Amy Lim / Rachel Tan / Ken Adeyemi" after it arrives. Use the full name from `lib/personas.ts`
    from the start (`CompareColumn.tsx`).
11. **Slide copy.** Business: "US$1.28M" and "7.0 minutes" on screen; the script says "about 1.3
    million" and "about seven". Say what is shown, or format as "US$1.3M" and "about 7". Stack line
    "Hunyuan Hy3 through TokenHub" must lose "through TokenHub" unless Hy3 has answered live
    (`slides.json` already carries the note). Proof: "ran a security review on Tencent Hunyuan hy3"
    (lower-case) and "Every session transcript is kept" reads like a commit message; "Hy3", and end
    the line at "security review".
12. **Knowledge graph load (Q&A only).** 8 to 16 s to a drawn canvas, with a three-line checklist
    spinner. Pre-open `/dev/graph` in a second tab *as the same persona as the main session* before
    Q&A, or open the Table view first and let the canvas fill in. `QA_LIVE.md` row 1 measured 10 to
    13 s on prod; that is the number a judge would feel.
13. **Accept is the longest bar.** On Rachel's journey, Accept (verify identity, append
    `request.accepted`) took 549 ms and Seal 364 ms; the two gates took 13 and 340 ms. Mission control's
    p50 for the same stage is 64 ms, so this was a cold first ask. Warm every persona on `P` (the spec
    says so) and check the numbers on a warm run; if Accept still dominates, label the bar "Accept
    (write-ahead seal)" so the slow step reads as the guarantee, not as overhead.
14. **简体中文 leftovers on the page used for the flip.** After `L` on `/dev/audit`: "55 requests
    found.", the three search suggestion chips, question text and the tamper descriptions stay English.
    Either translate those strings in `lib/i18n/*` or do the 3 s flip on the Compare page, where the
    shell flips cleanly (QA row 14).
15. **Tamper banner numbers.** "Every attack ran on a temporary copy of 1,595 records" next to "Real log
    re-verified just now: Chain intact · 1,598 records" (three real records were written between the
    copy and the re-check). Add "(records written since the copy: 3)" or drop the copy count.
    "Head copied: 0ab6dde983a2" is twelve hex with no label of what it is; make it "Copied head hash".

## 4. What reads as AI-generated, cluttered, inconsistent or unpolished

- **Fixture text leaking into answers** (fix 2): the all-caps "SYNTHETIC CASE FILE" sentence, the
  "mlro.tan:" prefix, and "#fincrime-cases: Velquarry Trading Pte Ltd review update (synthetic case …"
  as a source title. These are the only places the UI looks generated, and they are on the hero screen.
- **Preachy micro-copy.** Lock card on `/dev` as Jordan: "This is least privilege working." Trust
  centre eyebrow "Self-assessed · Synthetic data only" is good; the lock card sentence is a sermon.
  Journey "Reveal status: Answer text is never in the journey; a reveal needs a reason and is ledgered"
  is fine for compliance, long for a card.
- **Jargon in the first fold of the journey:** "Directory epoch 2", "Gate-1 policy ib-policy-1",
  "Prompt ib-prompt-2026-10-13.1". Right for Maya, noise for a judge; push them below the waterfall.
- **Density where the eye should rest.** The tamper table has eight attacks with four columns each.
  The story needs one row. Collapse the other seven under "Seven more attacks, all as expected" with
  a disclosure, in Stage only.
- **Consistency, small:** source cards say "Checked live just now" then "Checked live 21 s ago"
  (good); the sheet says "CHECKED LIVE AT 30 Oct 2026, 05:08 SGT" (good); the audit table says
  "30 Oct, 05:09 SGT" (year dropped; chips have it). Pick one. "Request 60dcbfd1c5…5041" under each
  answer in Stage is fine.
- **What does not look AI-generated and should stay:** the one-sentence refusal with "You could try",
  the persona cards' "Can open" lines, the slide typography, the HUD's "Model quoting" honesty, the
  "Not detectable: the stated window" row.

## 5. Was `plan_fable.md` followed?

| Recommendation (plan §8) | Status |
|---|---|
| Rewrite the demo as the §3.2 story; build Presenter mode first | Done. `RUN_SHEET.md` is §3.2 nearly verbatim (D-14); presenter steps match it; Stage theme, slides, HUD, `B`, `R`, `L` exist |
| Hy3 live Friday; WorkBuddy as insurance; model named on every footer | Footer names the path ("Quoted from sources (no model)"); Hy3 still 402 (`STATUS.md`); WorkBuddy card and connector exist, not tested by me |
| Five-person hallway test Saturday; three-row fix log | Protocol written; `FIX_LOG.md` is a template; slide reads `fixlog.json` and hides the line when empty (correct) |
| Freeze Saturday 22:00; polish the five demo-path screens at 150 % | Stage theme at 150 %; the five screens are clean except items 1 to 4, 7 to 10 above |
| Re-run the eval with Hy3; tamper on a sandbox copy; off-host anchor in one sentence | Eval re-run 29 Oct with the fake-capture model (0 / 5,958, 0 / 400, 0 / 976); sandbox tamper done with the external head shown; Hy3 run still "PENDING" in `RESULTS.md` |

Two of my own calls were not taken and the result is fine: the classification pill was dropped (cheap,
but not needed; remove it from the run sheet) and the dark theme stays default off-stage (right).

## 6. The three biggest risks on stage, and mitigations

1. **Hy3 is not live, and three spoken lines assume it.** Every answer says "(no model)"; the journey
   says "Extractive (no model)"; the HUD says "Model quoting". A WorkBuddy judge or an AI-interaction
   judge will notice before anyone says a word. Mitigation: TokenHub activation is the first item on
   Friday's list; the moment it answers, re-run the eval (`--limit` first) so the proof slide's run
   date moves. If it does not answer by Monday noon, apply every [HY3] replacement in `RUN_SHEET.md`
   and `QA_BANK.md` Q7 and Q18, make the WorkBuddy beat the Tencent-live moment, and keep the honest
   footer: in Q&A, "the design does not depend on the model" is a strength only if it was said first.
2. **Session state during the live path.** Step 3's live passage and step 4's compliance journey both
   depend on which cookie is the main session (Rachel, then Maya). Anything that changes it between
   `P` and step 4 turns the hero sheet into "This source isn't available to you right now". I saw
   exactly this when the profile was shared. Mitigation: fix 3 in code (sign in again inside the step),
   one browser window on the demo laptop, nobody touches laptop A after `P`, and `R` rehearsed as the
   recovery (it re-signs and returns to step 0 without touching the real ledger).
3. **Dead air at the tamper and the WorkBuddy hand-off, with no clips to fall back on.** The tamper is
   3 to 4 s of a static sentence, then a scroll to find the red row; the WorkBuddy beat is an Alt-Tab
   to another window on a phone hotspot; and `B` currently has nothing to play. Mitigation: fix 1
   (warm-up, progress line, auto-scroll), the 8-second rule in `BACKUP_PLAN.md`, record clips A, B, C
   on Monday on the frozen build and play each once on the stage laptop, and have Mehul's line cover
   the wait ("Now I change one record in a copy of the log… Verify.") so the 3 s is speech, not silence.

## 7. Smaller notes, in passing

- The HUD stays on every page after the script ends (I saw "10 / 10 · Close" on `/login` and
  `/dev/graph`). `Esc` hides it; add that to the Q&A line in the run sheet so the journey page is clean
  when judges walk up.
- `PRESENTER_KEYS.md` §1 promises four status dots; the HUD has three (API, Model, Ledger). Either add
  the WorkBuddy reachability dot or edit the spec.
- Login in Stage theme rendered the unselected persona cards grey for a moment in one screenshot, then
  white; not reproducible, noting it only.
- `QA_BANK.md` Q23 (business model) is still "TO FILL"; decide it before Monday or answer with Q24.
- The leave-behind's three screenshots are "TO FILL"; `compare-amy-rachel.png`, `compare-ken-identical.png`
  and `dev-maya-audit-tamper.png` in `docs/finals/screens/live/` are the right three once fix 2 lands
  (the current Rachel screenshot carries the disclaimer sentence).
