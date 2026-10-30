# Stage QA, 30 Oct 2026 (presenter path, B clips, keys, grow walk-up, preflight, absence proof)

What was tested: the stack the coordinator restarted with `demo_day.ps1` (stop → start -Rebuild -NoBrowser → reset -Fresh -NoBrowser) on branch `final_v1.2`. The web build on 3100 is `.next/BUILD_ID` from 16:02 SGT. All three services answered 200 and stayed up from 16:07 (8200), 16:08 (8100) and 16:03 (3100). `var/demo-day/preflight.json` was rewritten at 16:11:49. QA ran 16:12 to 16:40 SGT.

How: headless only, with Playwright 1.48 from `databridge/apps/internal_brain_web` at 1920 x 1080, and a fresh browser context for every run, opened signed out at `/present?present=1`, as the stage browser opens. No visible windows and no sounds. No Act ticket was confirmed. The scripts are throwaway files in the session scratchpad and are not in the repo.

**Browser used for every result below: headless Google Chrome (`channel: 'chrome'`, 156).** Playwright's own Chromium (130) cannot decode H.264. There, every clip shows "Clip not recorded yet" (see §2). That is an artefact of the test browser, not a product fault: `demo_day.ps1` `Find-Browser` opens real Chrome or Edge, and both play the clips.

> **Note: the code changed during this QA.** HEAD moved from `1076517` to `d89f55a`, and `components/presenter/{controller.ts,PresenterBar.tsx,PresenterRoot.tsx,deliver.ts}` now have uncommitted edits, saved at 16:31 (the "app mode / mouse guided demo" work). The results below describe the **16:02 build**. `file:line` references are given for `1076517`, and for the current working tree where the line moved. Run this QA again once the app-mode work is rebuilt.

## 1. Default path: three timed runs (P, then Space through step 10)

"Settle" is the time from the key press until the room sees the finished result: both answers, the identical-refusal marker, the sources sheet, the waterfall, the Verify result, the tamper result, the audit table or the slide. "Bar" is the time until the HUD leaves "Working". Between presses the script waited 0.5 s after the step settled. At step 03 it pressed `Esc` to close the sheet, as Dev does.

| Step | Id | Settle s (run 1 / 2 / 3) | Bar s (1 / 2 / 3) | > 4 s? |
|---|---|---|---|---|
| 00 | title (`P`) | 2.07 / 1.77 / 1.94 | 2.00 / 1.70 / 1.88 | no (but see risk 6: 4.7 to 6.5 s in other runs) |
| 01 | compare-people | 1.87 / 1.53 / 1.87 | 1.85 / 1.51 / 1.85 | no |
| 02 | compare-person (Ken) | 1.41 / 1.40 / 1.66 | 1.39 / 1.39 / 1.64 | no |
| 03 | citation (sheet opens) | 0.33 / 0.34 / 1.01 | 0.10 / 0.11 / 0.13 | no |
| 04 | journey (as Maya) | 0.89 / 0.72 / 1.11 | 0.87 / 0.71 / 1.09 | no |
| 05 | verify | 0.94 / 0.95 / 0.96 | 0.07 / 0.08 / 0.02 | no (but the bar reads ready about 0.9 s before the result) |
| 06 | tamper | **3.20 / 2.55 / 3.69** | 0.10 / 0.14 / 0.19 | no, closest to 4 s (and the bar reads ready about 3 s before the result) |
| 07 | workbuddy (`/dev/audit`) | 0.15 / 0.13 / 0.12 | 0.13 / 0.11 / 0.09 | no |
| 08 | proof | 0.15 / 0.10 / 0.14 | 0.12 / 0.08 / 0.10 | no |
| 09 | business | 0.09 / 0.08 / 0.08 | 0.07 / 0.07 / 0.07 | no |
| 10 | close | 0.09 / 0.09 / 0.08 | 0.07 / 0.07 / 0.07 | no |
| | **Total machine time** | **11.2 / 9.7 / 12.7 s** | | |

- Every step finished `ready`, with no failed step in any of the 4 default walks (3 timed runs and the B walk). The page load before `P` took 0.3 s.
- Over a 5:00 talk the click path costs at most about 13 s of machine time, so the run sheet's spoken timing holds. No step went over 4 s. The tamper step is the slowest at 2.5 to 3.7 s; the run sheet gives it 7 to 15 s of speech, so this is fine.
- Step 06 shows "Chain broken at entry 1,103" in run 1. The number changes as the ledger grows, so read it off the screen ([ENTRY]). Screenshot: `screens/stage_qa_30oct/run1_06_tamper.png`.
- Step 04 opened the journey as `maya.compliance` with the waterfall in view. Step 05 showed "Chain intact".
- The proof slide shows 7,374 / 400 / 1,152 and no tester line, which is correct while there is no FIX_LOG row.

## 2. `B` at every step

The clips, fetched as a `<video>` element fetches them (GET with `Range: bytes=0-1023`):

| Clip | Range GET | Full GET |
|---|---|---|
| `clipA_compare_amy_rachel_ken.mp4` | 206, `Content-Range: bytes 0-1023/3160780`, `Accept-Ranges: bytes`, `video/mp4` | 200, 3,160,780 bytes, 0.02 s |
| `clipB_journey_audit_tamper.mp4` | 206, `0-1023/3338880`, `video/mp4` | 200, 3,338,880 bytes |
| `clipC_workbuddy_jordan.mp4` | 206, `0-1023/1885734`, `video/mp4` | 200, 1,885,734 bytes |

All three are H.264 (`avc1`), 1920 x 1080 and muted. In headless Chrome and Edge, `canPlayType` returns "probably" and playback starts. In Playwright Chromium, `canPlayType` returns "" and playback fails with `MEDIA_ERR_SRC_NOT_SUPPORTED`.

| Step | Expected (`script.ts` `clip`) | Result in headless Chrome | Overlay opens / video playing | `B` again closes |
|---|---|---|---|---|
| 00 title | A | plays clipA (23.5 s) | 54 ms / 390 ms | yes |
| 01 compare-people | A | plays clipA | 25 / 354 ms | yes |
| 02 compare-person | A | plays clipA | 25 / 351 ms | yes |
| 03 citation, **sheet open** | A | **nothing happens: `B` is ignored while the sources sheet is open** | none | n/a |
| 03 citation, after `Esc` | A | plays clipA | 20 / 255 ms | yes |
| 04 journey | B | plays clipB (14.1 s) | 32 / 366 ms | yes |
| 05 verify | B | plays clipB | 79 / 428 ms | yes |
| 06 tamper | B | plays clipB | 97 / 439 ms | yes |
| 07 workbuddy | C | plays clipC (25.4 s) | 115 / 490 ms | yes |
| 08 proof | none | "This step has no recording. Slides are part of the app." | 14 ms | yes |
| 09 business | none | same message | 14 ms | yes |
| 10 close | none | same message | 27 ms | yes |

Apart from step 03 with the sheet open, the clip for every step matches `script.ts`, starts in under 0.5 s (the target is 3 s) and carries the "RECORDING · NOT LIVE" badge.

What the clips actually show (frames checked):
- **Clip C is not WorkBuddy.** It shows Jordan signing in to *our web app* and asking the WorkBuddy question there, with the answer labelled "Quoted from sources (no model)" (`screens/stage_qa_30oct/B_07_clipC_frame.png`). The handoff's item 4 ("a real clip C") is still open.
- Clip B was recorded with the presenter HUD on screen, and that HUD reads **"Model unknown · Ledger unknown"** (frame at about 1.5 s). If `B` is used, the room sees "unknown".

## 3. HUD after `P`

- Before `P`: "— / 10 · Press P to start", with no dots. The `/ib/status` call returns 401 while nobody is signed in, which is expected.
- After `P` (1.8 to 2.1 s in the timed runs): **"API ok · Model quoting (amber) · Ledger ok"**. It stays like that on every step from 00 to 10 in all runs, never "unknown".
- Model line: Hy3 is not shown as live. The dot is amber and says "quoting", and every answer footer says "Quoted from sources (no model)". Preflight says "Model: extractive fallback (quoted from the sources) — IB_FORCE_OFFLINE=1". This is honest, but "quoting" on its own is terse (see risk 7).
- "Demo data: synthetic Company A" shows on every step.

## 4. Keys and the grow walk-up

### Keys (`PRESENTER_KEYS.md` §2)

| Check | Result |
|---|---|
| Before `P`: `Esc` hides the HUD, `Esc` shows it again | pass / pass |
| After `P`, nothing open: `Esc` keeps the HUD | pass |
| `H` hides the HUD; `Esc` brings it back; `H` twice toggles it back | pass / pass / pass |
| `ArrowLeft` from 02 to 01: restores Amy and Rachel without asking again (0 new `/ib/ask`), 66 ms | pass |
| Forward again to 02 after going back: 0 new asks (restored) | pass |
| Step 03: `Space` while the sheet is open is ignored (stays 03); `Esc` closes the sheet and keeps the HUD | pass (as specified) |
| `D` at step 04: Leo's Deliverables panel opens in 0.83 s; whoami becomes `leo.ops` (Table, "Copied cell by cell from [2], no model") | pass (`keys_D_deliverables.png`) |
| `Esc` after `D`: the panel closes, whoami returns to `maya.compliance`, the URL stays on the journey, the waterfall is visible | pass |
| `ArrowLeft` from 05 to 04: journey as `maya.compliance`, waterfall visible, no "Request not found" | pass |

### `?grow=1`

- **Space never lands on a grow step without `?grow=1`.** In all 4 default walks the HUD read `/ 10` and the step ids were exactly title, compare-people, compare-person, citation, journey, verify, tamper, workbuddy, proof, business, close. That is true even though demo features are on, which is why `?grow=1` shows `/ 13`.
- Grow walk-up: a separate context, signed in as `jdoe` first, then `/present?present=1&grow=1`. The HUD read `/ 13` both before and after `P`. Two attempts:

| Step | Attempt 1 | Attempt 2 |
|---|---|---|
| 01, 02 | ready (2.5 s, 1.7 s) | ready (4.0 s, 3.8 s) |
| 03 `grow` | **FAILED after 20.4 s** ("graph not ready"); `Space` retried it and it was ready in about 3 s | **FAILED after 20.7 s**; `Space` retry ready in 3.1 s. The sheet is presenter-owned, the note is pre-filled and the focus is on the sheet (`grow_03_addsheet.png`) |
| 04 `grow-post` | ready (the script spent its 5th `Space` on the retry) | ready in 2.6 s; the sheet closed by itself; "Just added" has 1 item on Jordan's side and **0 on Ken's** (`grow_04_posted_ken_empty.png`) |
| 05 `grow-ask` | not reached | ready in 3.9 s. Jordan: "Quoted from sources (no model) · Jordan Doe said Update on DBMIG-12 … the copy job resumes Thursday 5 Nov 2026, 21:00 SGT …", cited. **Ken: the constant refusal** (`grow_05_ask_jdoe_vs_ken.png`) |

- Reset after each attempt: Simulator **Reset demo** as `admin.it` at `/dev/sim`, which logged "Done Reset demo /admin/reset" (36.0 s and 35.5 s, done at 16:29:41; `grow_sim_reset_done.png`). Afterwards `jdoe` asking "When does the ledger-shard-3 copy job resume?" gets only the original world's blocker message, not the note. The `--sim` index count of 0 for that phrase (§6) confirms the note left the index. **The stage state is clean** (reset, then read-only checks only).
- Before the grow attempt I warmed `/dev/graph` as `jdoe` in the same context (25 s for the canvas). The grow step still timed out, because `P` signs Rachel in and the grow step then signs `jdoe` in again.

## 5. `/present/preflight`

Verdict: **READY.** Every item is green except these:

| Item | Status | Detail |
|---|---|---|
| Model: extractive fallback (quoted from the sources) | **amber (warn)** | `IB_FORCE_OFFLINE=1`; "say 'quoted from the sources, with the same checks'". Expected while TokenHub is not live |
| Model now | info | "sign in to see" (the board was opened signed out) |

Green: web, IB :8100 (9 ms), mock :8200, engine :8300, Simulator reset 16.4 s, Verify as Maya (intact, 1,130 records), seeded history (328 requests), Amy, Rachel, Rachel citation [2], Ken V, Ken B, Ken identical, Jordan, Maya journey, screen 1920 x 1080, zoom 100 %, UTC+8, clock skew 0.3 s, clips A, B and C.

Notes:
- The report goes amber ("Start report is N minutes old") after **45 minutes** (`PreflightBoard.tsx:33`), so run Reset within 45 minutes of going on.
- The clip check only fetches the file. It would pass in a browser that cannot play H.264. This does not matter with Chrome or Edge.
- Screenshot: `preflight_board.png`.

## 6. Absence proof

- `python docs/finals/live_kit/check_absent.py --all` (the documented step 3 of `LIVE_INGEST_KIT.md` §0), run against the fresh stack after the Simulator reset: **12/12 PASS, "RESULT: all checks passed"**. **`docs/finals/live_kit/ABSENCE_PROOF.txt` was rewritten by the script** (16:33:53, same format). Only the header changed: `world.json sha256` moved from `e05050c35f51f40a` to `e3381d7122570db7`, so the world did change. Every check line is identical to the previous run.
- `--all --sim` (documented in §7 as the in-index count): **10 samples PASS** (`documents_containing=0 of 1204 indexed docs`). **The last 2 FAIL with HTTP 429**, because `/sim/check-absent` allows 10 calls per person per minute (`databridge_ib/sim.py:93` `CHECK_RATE_LIMIT = 10`) and there are 12 samples. Run on their own after 75 s, `up-vendor-csv --sim` and `up-buddy-md --sim` both PASS with count 0. So all 12 are absent from the index, but **`--all --sim` can never pass as written**. The committed file is the `--all` run; the `--sim` evidence is described here.

## 7. Stage risks, ranked, with suggested fixes (not fixed)

1. **The grow walk-up fails at step 03 the first time, every time (2 of 2): about 20 s of "Working", then "Step failed".** One more `Space` makes it work (3 s). Cause: `graph.addNote` is called with a 20 s timeout (`components/presenter/script.ts:230`), and the handler declines until Jordan's graph is ready (`components/graph/GraphExplorer.tsx:406-407`). A fresh `jdoe` sign-in takes about 25 s to build that graph. Fix: raise the timeout to 45 to 60 s, and/or warm `jdoe`'s graph during `P` when the tab opted in to grow (as `warmDeliverables` does for Leo). Until then, add to `PRESENTER_KEYS.md` §6: "if 03 says failed, press Space once more". This only affects the Q&A walk-up, not the 5:00.
2. **The code changed under the QA.** HEAD moved to `d89f55a`, and the presenter controller, bar, root and deliver code now have about 320 lines of uncommitted edits (app mode, mouse buttons and `advance()`) that are not in the 16:02 build that was tested. Fix: once that work lands, rebuild (`demo_day.ps1 start -Rebuild`) and re-run this QA. The scratch scripts can be re-run in about 10 minutes.
3. **Clip C is not a WorkBuddy recording.** It shows Jordan asking in our own web app (`B_07_clipC_frame.png`). If WorkBuddy fails, the room sees our app, not WorkBuddy. Fix: record the real WorkBuddy clip C (handoff item 4). Until then, the recovery line must say "the same question as Jordan, in our app, recorded this morning", and `BACKUP_PLAN.md` line 42 should say what clip C really shows.
4. **`B` does nothing at step 03 while the sources sheet is open.** The run sheet says "App stalls: … Dev presses B". At step 03 the driver must press `Esc` first, then `B`. Cause: `controller.ts:268` (`1076517`), `:334` (working tree): with a dialog open, only `B` *closing* a clip is handled. Fix: let `B` open the clip over a non-presenter dialog (close the sheet first), or write "step 03: Esc, then B" into `PRESENTER_KEYS.md` §2 and the run sheet's recovery lines.
5. **At steps 05 and 06 the HUD says ready before the result is on screen** (Verify about 0.9 s early, tamper about 3 s early). A quick `Space` during the tamper leaves before "Chain broken at entry N" appears. Fix: in `script.ts:164` and `script.ts:172`, make the action `await ctx.reveal('[data-testid="verify-status"]', 10_000)` and `ctx.reveal('[data-testid="tamper-safe"]', 15_000)` so the bar says "Working" until the result lands. Or rehearse: "Space only after the red row is visible".
6. **The `P` warm-up time varies: 1.7 to 2.1 s in the timed runs, 4.7 to 6.5 s in four other fresh contexts** (Leo's deliverables warm-up runs first and is serialised ahead of Rachel's sign-in, `controller.ts` `start()`). `P` is pressed before the hook, so it is not stage time, but Ishaan must not start until the HUD shows `00` and the dots. Fix: put "press P at least 10 s before the first word" in `RUN_SHEET.md` §0b, or start Leo's warm-up after step 0's sign-in.
7. **The HUD model text "quoting" is terse.** It is not wrong, and it is honest that Hy3 is not live, but a reader might not tie it to "Quoted from sources (no model)". Fix: change `'pr.status.quoting'` to "quoting (no model)" (`lib/i18n/presenter.ts:101` at `1076517`, `:115` now).
8. **`check_absent.py --all --sim` always fails with 429 on samples 11 and 12** (10 per minute limit, `sim.py:93`). Fix: in `docs/finals/live_kit/check_absent.py:165`, pace the `/sim/check-absent` calls (about 6.5 s apart, or wait and retry once on 429), or ask for it per sample. Document the limit in `LIVE_INGEST_KIT.md` §7.
9. **Clip B (and likely A) was recorded from an older build:** its HUD reads "Model unknown · Ledger unknown". Fix: re-record clips A and B on the final build (also handoff item 7).
10. **`B` on a slide (steps 08 to 10) blacks out the slide** behind a "This step has no recording" card with the "RECORDING · NOT LIVE" badge. An accidental `B` during the business or close slide hides it. Fix: when `clip` is null, have `B` announce instead of opening the overlay (`controller.ts` `case 'b'`, `ClipOverlay.tsx`).
11. Minor: with every run, the console shows 401s from `/ib/status` and `/auth/whoami` before `P` (expected) and one 404 (probably the missing favicon; `public/` has no icon). Nothing is visible on screen.

## 8. Artefacts

- Screenshots (`docs/finals/screens/stage_qa_30oct/`): `run1_01_compare-people.png`, `run1_02_compare-person.png`, `run1_03_citation.png`, `run1_04_journey.png`, `run1_06_tamper.png`, `run1_08_proof.png`, `B_07_clipC_frame.png`, `keys_D_deliverables.png`, `grow_03_addsheet.png`, `grow_04_posted_ken_empty.png`, `grow_05_ask_jdoe_vs_ken.png`, `grow_sim_reset_done.png`, `preflight_board.png`.
- `docs/finals/live_kit/ABSENCE_PROOF.txt` was rewritten (12/12 PASS, 16:33:53).
- Nothing was committed. No services were started, stopped or restarted. `.env.ib.local` was not read.


---

## Re-run after round-4 fixes (30 Oct 2026, 17:25 to 17:41 SGT)

What was tested: the stack the coordinator restarted with `demo_day.ps1` (stop, then start -Rebuild -NoBrowser) on branch `final_v1.2` at `54c80fb` plus the uncommitted presenter, preflight, persona and graph edits. The web build is `.next/BUILD_ID` from 17:22:36 and `var/demo-day/preflight.json` from 17:22:57. All three services answered 200 from 17:23:12 and stayed up; QA started at 17:25:38.

How: same as above. Headless Google Chrome (`channel: 'chrome'`) at 1920 x 1080, a fresh context for every run, opened signed out at `/present?present=1`. No visible windows and no sounds. No ticket was confirmed and nothing was sent. The scripts are throwaway files in the session scratchpad (`walk2.js`, `mouse.js`, `exitnav.js`, `keys2.js`, `grow2.js`, `preflight2.js`, `starters.js`, `hallway.js`, `tamper.js`).

### R1. Pass/fail

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Two timed keyboard walks (`P`, then `Space` to 10) | **PASS.** Every step was `ready`, with no failed step in 4 keyboard walks and 1 mouse walk | R2 |
| 2 | `P` shows the title card immediately | **PASS.** The title appeared 53 to 183 ms after `P` (by mouse Start: 175 ms). The bar was ready in 0.34 to 0.70 s, down from 1.7 to 6.5 s | `r2_run1_00_title.png` |
| 3 | Mouse walk using the bar buttons only, 00 to 10 | **PASS, with one dead click.** At step 03, ▶ Next does nothing while the sources sheet is open (issue N1). After the sheet's X, Next works | R3 |
| 4 | Back / Next / Start / Recording / Reset / Hide / Exit buttons | **PASS** | R3 |
| 5 | Side buttons 3 and 4 (real CDP input) | **PASS.** 06 → 05 → 06; the page URL followed the step and the browser did not navigate on its own | R3 |
| 6 | Browser Back | **PASS.** `history.back()` from step 08 went to step 07 (`/dev/audit`), and ▶ returned to 08 | R3 |
| 7 | Exit presenter, then a normal nav click | **PASS.** The bar was gone, Stage was off, `Space` and `P` did nothing, and clicking the nav link "Walnut DataBridge" went to `/dev`. On a slide, see N6 | `r2_mouse_exit_navclick.png` |
| 8 | `B` at every step (00 to 07) | **PASS.** The right clip played at each step (A, A, A, A, B, B, B, C). The overlay opened in 32 to 103 ms and playback started in 0.38 to 0.84 s; `B` again closed it and the HUD stayed | R4 |
| 9 | `B` over the sources sheet at step 03 | **PASS (fixed).** The sheet closed, clipA played, and after `B` the sheet stayed closed | `r2_B_03_citation.png` |
| 10 | `B` on a slide (08, 09, 10) | **PASS (fixed).** "No recording for this step" showed for about 2.5 s, there was no overlay, and the slide stayed up | `r2_B_09_business.png` |
| 11 | Steps 05 and 06 ready only after the result | **PASS, mostly.** In all 5 walks the result was in the page when the bar turned ready. But at 05 it is still below the fold at that moment (issue N3) | R2, `r2_step05_at2s.png`, `r2_step06_at2s.png` |
| 12 | HUD says "quoting (no model)" | **PASS.** "API ok · Model quoting (no model) · Ledger ok" on every step of every walk | `r2_B_09_business.png` |
| 13 | Keys per `PRESENTER_KEYS.md` (Esc before and after `P`, H, ←, Space with the sheet open, D and Esc) | **PASS**, 18 of 18, same as before. `D` opened in 0.40 s (was 0.83 s) | R5, `r2_keys_D_deliverables.png` |
| 14 | Grow walk-up `?grow=1`, first try within 60 s | **PASS (fixed).** Step 03 was ready at the first attempt in **18.1 s**, with no graph warm-up. Steps 04 and 05 also passed | R6 |
| 15 | Simulator reset as `admin.it` afterwards | **PASS.** "Done Reset demo /admin/reset 17:32:24 SGT" in 15.9 s, and Jordan gets only the original blocker message again | `r2_grow_sim_reset_done.png` |
| 16 | Preflight is gated (sign in first) | **PASS.** Signed out the board returns HTTP 404; signed in as `jdoe` it returns 200 | R7 |
| 17 | Preflight is all green except the model line | **PASS.** READY. The only two amber rows are both about the model: "Model: extractive fallback" and "Model now" | R7, `r2_preflight_board.png` |
| 18 | Preflight fits at 390 px | **PASS.** `scrollWidth` is 390 and no element overflows | `r2_preflight_390.png` |
| 19 | `check_absent.py --all --sim` | **PASS 12/12** in 94 s (it waited out the 429 once). `ABSENCE_PROOF.txt` was rewritten at 17:35:08 | R8 |
| 20 | Login chips are role-only | **PASS** for the content: no chip names a channel, space, project, drive or folder. The label is wrong (issue N4) | `r2_login_chips.png` |
| 21 | New starters get a cited answer | **PASS.** Each of the 4 new starters gets an answer with citations. Leo's new one is never shown (issue N5) | R9 |
| 22 | Hallway task cards 1 to 6 (coordinator add-on) | **PASS, 6 of 6** | `hallway_kit/TASK_CARDS.md` "Hallway task check", `r2_hallway_*.png` |

### R2. Timed walks: settle time in seconds (key press until the room sees the result)

| Step | Run 1 | Run 2 | Run 3 (15 s after `P`) | B walk | Mouse | Before (16:02 build) |
|---|---|---|---|---|---|---|
| 00 title (`P`) | 0.34 | 0.70 | 0.56 | 0.53 | 0.68 | 1.8 to 2.1 |
| 01 compare-people | 3.47 | 3.78 | 2.73 | **5.76** | 2.92 | 1.5 to 1.9 |
| 02 compare-person | 1.21 | 1.96 | 2.29 | 2.12 | 2.81 | 1.4 to 1.7 |
| 03 citation | 1.10 | 0.25 | 0.42 | 0.44 | 1.18 | 0.3 to 1.0 |
| 04 journey (Maya) | 0.67 | 2.29 | 1.12 | 0.97 | 1.27 | 0.7 to 1.1 |
| 05 verify | 0.75 | 1.50 | 0.83 | 0.98 | 2.03 | 0.9 to 1.0 |
| 06 tamper | 3.11 | **5.86** | 3.64 | **4.74** | **5.58** | 2.5 to 3.7 |
| 07 to 10 | 0.07 to 0.11 | 0.09 to 0.16 | 0.09 to 0.15 | 0.08 to 0.15 | 0.07 to 0.13 | 0.08 to 0.15 |
| **Total** | **11.0** | **16.8** | **12.1** | **15.9** | | 9.7 to 12.7 |

- In every walk the bar reached `ready` within 50 ms of the result settling, so "Working" now covers the whole wait at 05 and 06. Step 06 now counts the full tamper time (3.1 to 5.9 s). The run sheet gives it 7 to 15 s of speech, so that is fine.
- Step 01 is slower than in the 16:02 build: 2.7 to 3.8 s, and 5.8 s once, against 1.5 to 1.9 s before. Waiting 15 s after `P` (run 3) did not bring it back down. See issue N2.
- Verify read "Chain intact". The tamper read "Chain broken at entry 1,112" (`r2_step06_at2s.png`); the entry number still changes as the ledger grows. The journey was opened as `maya.compliance` with the waterfall in view.
- Console in every run: three 401 errors (`/ib/status`, `/auth/whoami` before `P`) and one 404 (favicon), as before. Nothing shows on screen.

### R3. Mouse walk (bar buttons only, real mouse clicks)

- Before `P`, Start, Reset, Hide and Exit are visible. After Start, the legend row stays open in Stage, so More, Back, Next, Recording, Reset, Hide and Exit are on screen; More was never needed. Back is disabled at 00, and Next is disabled at 10.
- **Back** at 02 went to 01 in 69 ms, with Amy and Rachel restored and not asked again. Next went back to 02.
- **Step 03:** ▶ Next with the sources sheet open: the step stayed `03` and the sheet stayed open, so the click did nothing at all (N1). The sheet's X closed it, and Next then went to 04.
- **Recording** at 04 played clipB (t = 1.1 s after 1.2 s). The overlay's own close button closed it, and the step stayed 04. Recording is not shown on the slides (08 to 10).
- **Hide** at 05 hid the bar and showed the "Show the presenter bar" handle; clicking the handle brought the bar back.
- **Side buttons** (CDP `back` and `forward`) at 06 went 06 → 05 (`/dev/audit?verify=1`) and then 05 → 06 (`/dev/audit?tamper=1`).
- **Browser Back** at 08 (`/present/proof`) went to step 07 at `/dev/audit` in 0.2 s; ▶ went back to 08.
- **Reset** at 10 opened the confirm; Cancel closed it and the step stayed at 10. The reset was not confirmed.
- **Exit presenter** at 10: the bar was gone, `data-theme` was back to dark, and `Space` and `P` did nothing. But `/present/close` has no links (N6). Repeated by exiting at step 07 (`/dev/audit`): the 11 nav links were visible, and clicking "Walnut DataBridge" went to `/dev` with no bar.

### R4. `B` per step (headless Chrome)

| Step | Result | Overlay open / playing | `B` again closes, HUD kept |
|---|---|---|---|
| 00, 01, 02 | clipA plays, muted | 103 / 835, 40 / 382, 32 / 377 ms | yes |
| 03 **with the sheet open** | **the sheet closes, clipA plays** | 47 / 385 ms | yes; the sheet stays closed |
| 04, 05, 06 | clipB plays | 39 / 371, 49 / 508, 73 / 403 ms | yes |
| 07 | clipC plays | 103 / 564 ms | yes |
| 08, 09, 10 | "No recording for this step" for about 2.9 s; no overlay; the slide stays visible | note shown in under 100 ms | n/a |

### R5. Keys

Pass: `Esc` before `P` hides and then shows the HUD. After `P`, `Esc` keeps it. `H` hides it, `Esc` brings it back, and `H H` toggles it. `←` 02 → 01 takes 62 ms with 0 new asks, and going forward again also makes 0 asks. `Space` with the sheet open is ignored (stays 03). `Esc` closes the sheet and keeps the HUD. `D` at 04 opens Leo's panel in 0.40 s, as `leo.ops`, built "Copied cell by cell from source [2]". `Esc` closes it, signs `maya.compliance` back in and keeps the waterfall. `←` 05 → 04 returns to the journey as Maya with the waterfall and no "Request not found".

### R6. Grow walk-up (`?grow=1`, signed in as `jdoe` first, no graph warm-up)

| Step | Result |
|---|---|
| Before and after `P` | HUD `/ 13` |
| 01, 02 | ready, 2.5 s and 2.9 s |
| 03 `grow` | **ready at the first attempt in 18.1 s** (was "failed after 20 s" 2 of 2). The sheet is open and focused, and the note is pre-filled (`r2_grow_03_addsheet.png`) |
| 04 `grow-post` | ready in 1.1 s. The sheet closed by itself; "Just added" has 1 item on Jordan's side and 0 on Ken's |
| 05 `grow-ask` | ready in 2.5 s. Jordan gets the note quoted and cited ("the copy job resumes Thursday 5 Nov 2026, 21:00 SGT"); Ken gets the constant refusal (`r2_grow_05_ask_jdoe_vs_ken.png`) |

18.1 s is inside the new 60 s limit, but it is still 18 s of "Working" in front of a judge (issue N7). After the reset, the stage is clean: the reset finished at 17:32:24, `check_absent --sim` reports 0 indexed documents for every sample phrase, and every later check only asks questions (they add audit records, as any use does).

### R7. Preflight (`/present/preflight`)

- Signed out: HTTP **404**. Signed in (`jdoe`): HTTP 200.
- Verdict **READY**, "2 to check". The amber rows are "Model: extractive fallback (quoted from the sources), IB_FORCE_OFFLINE=1" and "Model now: extractive fallback answers are quoted from the sources; say so, never claim Hy3". Both are the model line, as expected.
- Green rows: web; IB :8100 (83 ms); mock :8200; engine :8300; Verify as Maya (intact, 2,316 records, 0.4 s); seeded history (505 requests); Amy; Rachel; Rachel citation [2]; Ken V; Ken B; Ken identical; Jordan; Maya journey; screen 1920 x 1080; zoom 100 %; UTC+8; skew 0.3 s; clips A, B and C. (There is no "Simulator reset" row this time, because the coordinator's start did not include `reset -Fresh`.)
- At 390 px: no horizontal scroll, and long labels wrap (`r2_preflight_390.png`).

### R8. Absence proof

`databridge/.venv/Scripts/python.exe docs/finals/live_kit/check_absent.py --all --sim` (17:33:34 to 17:35:08) printed "rate limited (10 a minute): waiting 60 s, then one retry" once, then **12 PASS, 0 FAIL, "RESULT: all checks passed"**, with `documents_containing=0 of 1204 indexed docs` for every sample. The script rewrote **`docs/finals/live_kit/ABSENCE_PROOF.txt`**, which now includes the `--sim` lines. The `world.json` sha256 is unchanged (`e3381d7122570db7`). Earlier risk 8 is **fixed**.

### R9. Login chips and starters

- Login chips: none of the 9 notes names a place, but each still starts with the label "Can open:" (N4).
- New starters, each asked through the API as its own persona: Maya "Do I need to declare a gift from a supplier?" (1 citation); Leo "Can we offer yield on idle balances to a Hong Kong SME?" (1); Rachel "What does a new joiner do in week 1?" (4); admin.it "Who approves spend above SGD 500?" (2). Every answer is cited.
- Home page starters: Maya shows 2 and Rachel and admin.it show their new ones. **Leo shows 4 of his 5**, and the new "yield" starter is the 5th, so it never shows (N5).

### R10. New and remaining issues, ranked

1. **N1. Mouse path: ▶ Next is a dead click at step 03 while the sources sheet is open.** The sheet stays open, the step stays 03, and nothing explains why. A mouse-only presenter must find the sheet's X first. (`Space` is ignored there too, as specified, but the keyboard run sheet says `Esc` first.) Fix: treat the step-03 sheet like the grow Add sheet (`data-presenter-owned`), so that Next or `Space` closes it and advances. Or add "close the sheet (X), then Next" to the mouse lines of `RUN_SHEET.md` and `PRESENTER_KEYS.md` §2a.
2. **N2. Step 01 (Amy vs Rachel) is about twice as slow as in the 16:02 build:** 2.7 to 3.8 s, and 5.8 s once (it was 1.5 to 1.9 s). It is the first live step after the hook. Waiting 15 s after `P` did not help, so it is not the background warm-up. The ledger has also grown from about 1,100 to about 2,900 records today. Worth a profile before Demo Day, and Rachel's line should cover up to 6 s.
3. **N3. Step 05's "ready" comes before the room can see the result.** When the bar turns ready, `verify-status` is in the page but below the fold (top at y = 1262 in a 1080 viewport). The page scrolls it into view 0.5 to 2 s later. At step 06 the "Chain broken" row is in view about 0.5 s after ready. A quick `Space` at 05 can still leave before "Chain intact" is seen. Fix: in `script.ts`, the verify and tamper actions should `ctx.reveal(...)` (scroll, then wait) rather than `ctx.waitFor(...)`.
4. **N4. The login chip label still says "Can open:"** in front of role-only text: "Can open: Platform SRE lead", "Can open: Partner manager, external vendor", "Can open: Leads financial crime reviews". This now reads wrong. Fix: rename `login.canOpen` (`app/login/page.tsx:161`) to "Role" or "About", or drop the label.
5. **N5. Leo's new starter never appears.** "Can we offer yield on idle balances to a Hong Kong SME?" is the 5th item in `lib/personas.ts`, and the home page shows 4. Fix: move it up or drop one.
6. **N6. Exit presenter on a slide (08 to 10) leaves a page with no links.** `/present/*` has no nav, so after Exit there is nothing to click and the driver needs the address bar. On the app's own pages, Exit followed by a nav click works. Fix (optional): the slides could show a small "Open the app" link once the presenter has exited.
7. **N7. Grow step 03 now passes, but takes 18 s of "Working" from a cold start** (it is building Jordan's graph). For a judge walk-up, press `P` early or open `/dev/graph` as `jdoe` beforehand, or warm `jdoe`'s graph during `P` when `?grow=1` is set.
8. Carried over, not re-checked in this run: clip C is the in-app stand-in (old risk 3); clip B's HUD reads "unknown" (old risk 9); the 401s before `P` and the favicon 404 (old risk 11).

Earlier risks now **fixed**: 1 (grow timeout), 2 (rebuilt and re-run), 4 (`B` over the sheet), 5 (05/06 ready early, apart from N3), 6 (`P` warm-up), 7 ("quoting (no model)"), 8 (`--sim` 429), and 10 (`B` on a slide).

### R11. Artefacts (this run)

- Screenshots in `docs/finals/screens/stage_qa_30oct/`: `r2_run1_00_title.png`, `r2_B_03_citation.png`, `r2_B_09_business.png`, `r2_step05_at2s.png`, `r2_step06_at2s.png`, `r2_mouse_00_bar.png`, `r2_mouse_10_more_row.png`, `r2_mouse_exit_navclick.png`, `r2_keys_D_deliverables.png`, `r2_grow_03_addsheet.png`, `r2_grow_04_posted_ken_empty.png`, `r2_grow_05_ask_jdoe_vs_ken.png`, `r2_grow_sim_reset_done.png`, `r2_preflight_board.png`, `r2_preflight_390.png`, `r2_login_chips.png`, `r2_hallway_1_jordan_spend.png` to `r2_hallway_6_leo_table.png`.
- `docs/finals/live_kit/ABSENCE_PROOF.txt` was rewritten by the script (12/12, 17:35:08).
- Nothing was committed. No services were started, stopped or restarted (apart from the Simulator's in-app Reset demo). `.env.ib.local` was not read.
