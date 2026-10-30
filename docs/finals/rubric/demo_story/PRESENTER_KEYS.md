# Presenter mode spec (for the UI agent)

Build target: `databridge/apps/internal_brain_web`. Purpose: one person (Dev) runs the whole live demo from the keyboard, repeatably, on pre-warmed data. Run sheet: `RUN_SHEET.md`. Priority P0 (D-15), build Saturday morning.

Items marked **PROPOSED** are names or query strings chosen in this spec because the repo has no existing one. Change them freely, but keep the behaviour, and tell the coordinator.

**Stage rules:** only ONE browser window is used on stage (no second pre-opened window); `R` is the recovery key.

## 1. Enabling, and why (WCAG 2.1.4)

Single-key shortcuts are a WCAG 2.1.4 risk. So:
- Presenter keys are **off by default**. They are active only when presenter mode is armed: `NEXT_PUBLIC_IB_PRESENTER=1` at build or start, or `localStorage["ib.presenter"]="1"` (built; `components/presenter/controller.ts`).
- They are ignored whenever focus is in an input, textarea, select or contenteditable, and whenever Ctrl, Alt or Meta is held.
- A small visible HUD (bottom-right, outside the 5% safe margin) shows the armed state, the step number and three status dots: API, model, ledger (`refreshHealth` in `controller.ts`). HUD text is also in the accessibility tree.
- Normal users never see the HUD or any shortcut.

## 2. Keys

| Key | Action |
|---|---|
| `P` | Arm and start. Applies the Stage theme and shows step 00 (the title card) at once; Leo's deliverables warm-up for `D` runs in the background once step 01's Compare has settled, so it never competes with the two live Compare asks (stage QA 30 Oct #6: it used to run first and P took 4.7 to 6.5 s; round-5 Opus cross-check #3). Pressed again: returns to step 00 without resetting the sandbox |
| `Space` | Run the next step. The HUD shows what the next step will do. Debounce: ignore a second press within 400 ms |
| `B` | Fallback: full-screen overlay plays the local clip for the current step (A, B or C), muted, from a local file; `B` again returns to live. No network. Target under 300 ms to start, under 3 s end to end. Over an open sheet (the sources sheet in step 03, `D`'s panel) `B` closes the sheet and plays the clip; it never closes the reset confirm. On a step with no clip (the slides, 08 to 10, and the grow steps) `B` leaves the slide up and shows "No recording for this step" for 2.5 s, announced to screen readers (stage QA 30 Oct #2, #4). That includes Q&A: at step 10 with `D`'s panel open, `B` shows the note and leaves the panel open (round-5 Opus cross-check #4) |
| `R` | Reset: restores the tamper sandbox copy, clears the ask boxes, re-warms the model, returns to step 00. Never edits the real ledger |
| `Esc` | **The Esc rule** (round-3 Fable #1, `controller.ts`). With a sheet or dialog open, `Esc` closes it through its own handler (sources sheet, reset confirm, `D`'s panel, the grow step's Add sheet) and never touches the HUD. Once `P` has started the script, `Esc` with nothing open **never hides the HUD**; it only brings a hidden HUD back. Before `P`, `Esc` hides or shows the HUD as before. While a dialog is open the other presenter keys are ignored, except over the grow step's Add sheet, which the script opened itself (`data-presenter-owned`): `Space` keeps driving the script there |
| `H` | Hides or shows the HUD on purpose, at any time |
| `ArrowLeft` | Back one step. `ArrowRight` skips a failed step |
| `L` | Toggles English and 简体中文. Not used in the 5:00 (the flip was cut, round-3 Fable #8: Developer pages stay English) |
| `S` | Stage theme on or off |
| `D` | Q&A: signs Leo in and opens his **pre-built** Deliverables panel (built by the `P` warm-up, kept 12 minutes; after that `D` builds a fresh one, round-4 Opus #7). `Esc` closes it and signs back in whoever was signed in before `D` (round-3 Fable #2), unless a step, `R` or the shell signed someone else in meanwhile (round-4 Opus #13). If the shell signs out or switches persona while `D` is still building, `D` stops: it never signs Leo in afterwards (round-5 Codex #9), so `←` to the journey shows the waterfall; the journey step's `←` also signs Maya in again on its own |
| `2` | Opens citation [2] on the current Compare page |
| `?` | Key legend |

Everything on the run sheet must be reachable by these keys alone.

The HUD's model dot reads "quoting (no model)" (引用模式（无模型）) when no model is live (stage QA 30 Oct #5).
When the stand-in model is on: the model dot reads "live" (在线), never "quoting (no model)"; it shows
whether *a* model is active, not which one. Hy3 and TokenHub appear on the slides and the preflight board
only when `/ib/status` reports `active_provider: "tencent-tokenhub"`; with the stand-in they do not, and
the preflight says "AI model active". Say "an AI model", never Hy3. If the dot drops back to "quoting (no
model)" mid-run (spend cap reached or the stand-in switched off), use the default lines.

Steps 05 (Verify) and 06 (tamper) are ready only once their result is on screen: the Verify result, or "Chain broken at entry N" (stage QA 30 Oct #3).

**One session queue (round-4 Opus #1).** Every change of who is signed in to the main session (a step's sign-in, `P`'s warm-up, `D` and its `Esc`, `R`, and the shell's Sign out and persona switch) runs one at a time through `components/presenter/session.ts`. A Sign out or persona switch from the shell wins: work already queued stops and never signs anyone back in over it.

## 2a. Mouse

Everything the run sheet needs can also be done with the mouse. Each control calls the same action as its key (`controller.ts`: `advance`, `goBack`, `startOnce`, `toggleClip`, `askReset`, `toggleHidden`, `exitPresenter`), so the busy, debounce and failed-step rules are the same.

| Control | Same as | Notes |
|---|---|---|
| ▶ Next | `Space` | Disabled (`aria-disabled`) while a step runs and at the last step. On a failed step it tries again, like `Space`. Over step 03's sources sheet, ▶ Next, `Space` and `→` close the sheet and go on (the bar sits above that sheet; stage QA re-run) |
| Skip this step | `→` | Shown only on a failed step |
| ◀ Back | `←` | Disabled while a step runs and at step 00 |
| ▷ Start | `P` | Shown before `P` |
| Recording | `B` | Shown only when the step has a clip |
| Reset the demo | `R` | Opens the same confirm |
| Hide the bar | `H` | A small "Show the presenter bar" handle stays bottom-right; it brings the bar back (so does `H`). After the script, on pages the bar leaves alone, the same handle brings it back for that page |
| Exit presenter | `Ctrl+Shift+P` | Keys, side buttons and Stage off; the app is an ordinary website again. On a slide (00, 08 to 10, which have no links) it also opens the app home (stage QA re-run) |
| More controls | `?` | **Stage only.** The Stage bar is one row, so it shows only "More"; More opens the key-legend row with all the controls above, and it stays open until closed. Off Stage (rehearsal) the controls sit at the end of the first row |
| Mouse back / forward side buttons (`MouseEvent.button` 3 and 4) | `←` / `Space` | While armed only. The browser does not also go back or forward (`preventDefault` on mousedown, mouseup and auxclick; the step moves on mouseup). Ignored while a dialog has the input, like the keys |

Every control has a tooltip with its key (for example "Next (Space)"), an accessible name, `aria-keyshortcuts`, the global focus ring and a target of at least 32 px; strings are in English and 简体中文. Space on a focused bar button still means Next (its keyup is swallowed), so a click and a key press never both fire; Enter or a click activates the button itself.

**Ordinary clicks are never taken over.** Presenter mode listens only for the two side buttons; links, the app's own navigation, answer cards and every other click work as on any website, armed or not (`presenter.spec.ts` "armed: ordinary clicks stay ordinary").

**Browser Back and Forward** (toolbar, `Alt+←`, or a side button the browser handled anyway). Rule: when the browser goes back to the **previous step's page** (the same path and every query parameter that step's URL sets, so `/dev/audit` is not step 06's `/dev/audit?tamper=1`; round-5 Opus cross-check #1), that is Back: the script steps back with it (re-opening that page, and its session where the step needs one), so the bar and the page agree. Any other history move (Forward, or Back to a page the driver opened by clicking around) is left to the browser: the step number stays, and the next ▶ or `Space` opens its own page as usual. It is ignored while a step runs (the step owns the page; at worst it says "did not finish" and `Space` retries) and for one second after a side button. Why not "sync the step to the page": several steps share a page (01, 03 and the grow-ask step are all `/compare`; 05 to 07 are all `/dev/audit`), so the page alone cannot name the step, and running a step's action from a history move could ask or sign someone in by surprise.

## 3. Stage theme

- **Light** theme, **150%** scale (root font size x1.5; body text at least 24 px; answers at least 40 px as the plan asks for projector legibility).
- Navigation hidden; content inside a 1280 x 720 safe area, nothing in the outer 5%.
- Colours keep AA contrast (check with the contrast test). No gradients, no animation beyond a short fade. Respect `prefers-reduced-motion`.
- Footer label always visible: "Demo data: synthetic Company A". When the answer was composed by WorkBuddy, show "composed by WorkBuddy (Hy3) from the Brain's sealed answer" [ONLY IF HY3 LIVE; default: omit the label].
- Theme is applied by a class on `<html>` (PROPOSED: `data-theme="stage"`); `R` and leaving presenter mode remove it.
- The dark theme stays available for the rehearsal test on the venue projector; decide on 2 Nov.

## 4. Pre-minted demo sessions

Presenter mode switches identity by minting a signed demo token for each persona (`IB_DEMO_LOGIN=1`, as the repo does today). Identity comes only from the signed token (repo rule 2). The presenter must not allow typing a name. Personas used: `amy.am`, `mlro.tan` (Rachel), `ken.ext`, `maya.compliance`, `jdoe`.

## 5. Warm-up on `P`

1. One throwaway ask per persona used, to warm caches and the model call.
2. Check the three status dots: API, model, ledger. WorkBuddy has no dot; check it by hand before the run.
3. Ask timeout: if the model call exceeds 4 seconds, serve the deterministic extractive answer (the product's own behaviour, repo rule 5). No dead air.
4. No token streaming (D-08). The staged status line may show (D-20 SSE stage events if built): accepted, checked, composing, verified. Text appears only after grounding passes.

Acceptance test: from a cold browser, `P` then `Space` x N (N = steps in §6 that are in-app) completes the in-app part in under 100 seconds with no typing and no mouse, and `B` at any step plays that step's clip within 3 seconds.

## 6. Steps: exact app action and URL

Route names for Developer pages come from the plan (`/dev/journey/<req>`, `/dev/audit`). The Compare route `/compare` exists today.

Questions (exact strings, from `lib/personas.ts`):
- Q-V: `Why has Velquarry Trading's account been restricted, and what should I tell the customer?`
- Q-B: `Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?`

This is the live order. Step ids and URLs are copied from `components/presenter/script.ts` (`STEPS`, 11 entries); the HUD numbers them 00 to 10.

| Step | Id (`script.ts`) | Trigger | Run sheet time | URL | App action |
|---|---|---|---|---|---|
| 00 | `title` | `P` | 0:00 | `/present` | Stage theme on; main session signed in as Rachel (`mlro.tan`). HUD ready |
| 01 | `compare-people` | `Space` | 0:07 | `/compare?mode=people` (Amy left, Rachel right, Q-V) | Both columns submit Q-V at once, each with its own persona token. Staged status line, then both verified answers. There is no classification pill in the app. Rachel's `req_id` is kept for step 04 |
| 02 | `compare-person` | `Space` | 0:45 | `/compare?mode=person` (Ken; Q-V left, Q-B right) | Both columns are Ken. Both show the same refusal card. Nothing is said or shown about hidden documents (D-07). Hold |
| 03 | `citation` | `Space` | 1:10 | `/compare?mode=people` (as step 01) | Returns to step 01's results and opens citation [2] on Rachel's answer (the **sources sheet**). `Esc` closes it (the sheet's own handler) |
| 04 | `journey` | `Space` | 1:40 | `/dev/journey/<req>` (Rachel's `req_id`) | Session switches to `maya.compliance`. Opens on the waterfall: real milliseconds per stage, model name, three ledger records with sequence and hash (D-16) |
| 05 | `verify` | `Space` | 2:10 | `/dev/audit?verify=1` | **Verify** on the live ledger. Green: intact |
| 06 | `tamper` | `Space` | 2:20 | `/dev/audit?tamper=1` | **Sandbox tamper, then Verify**: one Gate-2 decision changes in a temporary **copy** of the log, 'forbidden' to 'verified' (spoken: "denied to allowed"), and the copy is verified. Red: "broken at entry N". The real chain is never edited (D-19) |
| 07 | `workbuddy` | `Space` | 2:35 | `/dev/audit` | **Off-app** (see §7). The bar shows the WorkBuddy card; Dev switches window (Alt+Tab). Back in the app, the newest row, channel `workbuddy-mcp` (Jordan), appears when the window regains focus, not on a key. No language flip (cut, round-3 Fable #8) |
| 08 | `proof` | `Space` | 3:20 | `/present/proof` | **Proof slide** |
| 09 | `business` | `Space` | 3:50 | `/present/business` | **Business slide** |
| 10 | `close` | `Space` | 4:30 | `/present/close` | **Close slide** |
| Q&A | none | none | 5:00 | `/dev/journey/<req>` | Driver goes back to the journey page (`ArrowLeft`) and leaves it up |

**Live growth is never on the Space path.** The three grow steps (`grow`, `grow-post`, `grow-ask`,
after the Ken beat) are added only when demo features are on (D-24) **and** the tab was opened with
`?grow=1` (stored for this tab; `?grow=0` turns it off). Without `?grow=1` the HUD counts `/ 10`;
with it, `/ 13`. Use it for a Q&A walk-up only (round-2 Fable #1; test in `presenter.spec.ts`).

The grow walk-up, keys only (round-3 Fable #1; `presenter.spec.ts` "the ?grow=1 walk-up runs on Space
alone"):
1. **Open the `?grow=1` tab after a sign-in exists** (`/present?present=1&grow=1`): `/ib/status` needs a
   session. A tab opened signed out keeps the opt-in and picks the grow steps up once `P` signs someone in
   (`components/sim/demo.ts` asks again after a sign-in), but check that the HUD reads `/ 13` before Q&A.
   Reset and `docs/finals/live_kit/check_absent.py --all` first.
2. `P`, then `Space` x 5, no `Esc`: 01 and 02 as usual; 03 `grow` (it waits up to 60 s for Jordan's graph,
   which takes about 25 s after a fresh sign-in; stage QA 30 Oct #1: 20 s failed 2 of 2) opens Jordan's Add sheet with the note
   filled and the focus on the sheet itself (not its Close button), and `Space` still advances; 04
   `grow-post` posts, shows "Posted to Slack" for about 1.5 s, then closes the sheet so the room sees the
   "Just added" feed (Jordan's side; Ken's stays empty); 05 `grow-ask` asks both on Compare.
3. A failed step (for example the post) leaves the sheet open with the error; `Space` tries it again.
   `Esc` closes the sheet but never the HUD.

Note: the run sheet's Ken step happens before the passage step. The tamper in step 06 writes only to the sandbox copy, so the WorkBuddy beat in step 07 writes a real record to the real chain afterwards (D-19), which the audit page shows when Dev returns to the app.

## 7. The WorkBuddy beat (off-app)

- Not controlled by the app. WorkBuddy is a separate window, already open, warm, configured as Jordan with the Internal Brain connector (`scripts/workbuddy_setup.py`).
- Aaruni starts the action by pasting the one exact prompt used everywhere (it is Jordan's canonical question in `personas.ts`): `What's the status of the database migration project and were there any blockers raised in Slack last week?`
- Expected: a cited answer stitched from Jira, Slack and Confluence.
- Back in the app (still step 07), the audit page shows the new ledger row. This is the live proof that WorkBuddy used the same gates.
- If the network fails: `B` plays clip C.

## 8. Slides

Three full-screen slides rendered by the app so the Stage theme and fonts match. Space advances; `B` is not needed. The content is in `RUN_SHEET.md` and `LEAVE_BEHIND.md`.

**Proof slide** (`/present/proof`):
- Three large numbers, each with its source in small type:
  - `7,374` adversarial requests, `0` leaks (`databridge/evals/internal_brain/RESULTS.md`).
  - `400` paired questions, `0` differences (same file).
  - A tester line, taken verbatim from `FIX_LOG.md`. If no real row exists, **do not render this line**.
- One CodeBuddy line, only the facts in the repo.
- Numbers are read from `results.json` at build time if possible, so they cannot drift from the evidence. If not, hard-code and re-check on 3 Nov morning.

**Business slide** (`/present/business`):
- One number: about US$1.3M a year, labelled "estimate".
- Four inputs on the slide (default case in the repo research: 600 staff, 90 minutes saved per person per week, US$31 an hour, 46 weeks). Confirm against the one-pager.
- One Tencent stack line [ONLY IF HY3 LIVE for the Hy3 part; default: "WorkBuddy, CodeBuddy, TokenHub (Hy3 wired, live run pending); a Lighthouse deploy kit, no live instance"]. State only what is live ([HY3] check). When the stand-in model is on: the same default line; the stand-in is not a Tencent product and is not named.

**Close slide** (`/present/close`):
- The tagline in large type: "Answers you can prove."
- "Walnut DataBridge" and the team: Ishaan, Dev, Mehul, Aaruni.
- A QR code to the GitHub repo: `https://github.com/IshaanSirbhaiya/Tencent` (the repo's `origin`). Check that it is public on 2 Nov and that the QR scans from 3 metres. Add the short URL in text under the QR (accessibility).
- The ask: "One pilot team at one regulated company."

## 9. Guardrails (do not violate)

- No hidden-count UI and no "something exists but is hidden" text, anywhere in presenter mode (D-07).
- Refusal cards are the real product response, not a presenter-written string.
- Recorded clips are labelled "recording" in the HUD and in the overlay corner.
- English and 简体中文 only (D-22).
- Presenter mode never writes to the real ledger except through ordinary asks.
- Presenter state (current step, `req_id`) is client state only; nothing about it goes to the server.

## 10. Test list for the UI agent

1. Armed off: pressing P, B, R, L or Space does nothing special.
2. In a text field: keys type normally.
3. Steps 00 to 10 in order, using only keyboard.
4. `R` after step 06 restores a clean sandbox; step 06 repeats.
5. `B` at each step plays the right clip and returns.
6. Step 04 opens in under 2 seconds with the correct `req_id`.
7. Axe and contrast pass on the Stage theme.
8. With the model timeout forced, the extractive answer appears and the run is not blocked.
