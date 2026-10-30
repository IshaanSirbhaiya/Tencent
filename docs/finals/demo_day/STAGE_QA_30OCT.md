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
