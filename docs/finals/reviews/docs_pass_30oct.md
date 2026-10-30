# Docs pass, Fri 30 Oct 2026 (evening): change log

Branch `final_v1.2` working tree on top of `1076517` (`origin/final_v1.3` is at `1076517`). Docs only;
nothing committed. Line numbers are `before → after` from `git diff -U0`.

## Facts checked against the repo before use

| Fact | Where checked |
|---|---|
| IB 1,131 / e2e 166 + 41 skipped / mock 154 / fixtures 47 / tsc clean | `STATUS.md` §2, `TEST_REPORT.md`, commit `1076517` message |
| 0 / 7,374; 0 / 400; 1,152; recall 100% | `public/eval_summary.json` (requests 7374, injection 1152, e2e 166), commit `9819855` |
| D-26..D-33 | `docs/finals/DECISIONS.md:33-40` |
| `--compliance-user-config` writes only `ib-compliance`, removes employee connectors; flags exclusive; workspace server name `internal-brain-audit` | `databridge/scripts/workbuddy_setup.py:11-19, 108-163` |
| `final_v1.3` exists and equals `1076517` | `git log origin/final_v1.3` |
| 93 commits after `acb18f0` up to `1076517`, all 30 Oct SGT | `git rev-list --count`, `git log` |
| Nine CodeBuddy sessions; Hy3 in CB1, CB5, CB7, CB9 | `TENCENT_TOOLS_REPORT.md:19-21, 43-46` |
| CB7 F2 (stream thread cap) still unapplied | no cap in `databridge_ib/app.py` (thread started at line 354) |

## Edits

| File:line (before → after) | Before | After |
|---|---|---|
| `README.md:10 → 10` | branch `final_v1.0` | branch `final_v1.3` |
| `README.md:194 → 194` | CodeBuddy "Six build sessions" | Nine sessions (CB1–CB9), CB7–CB9 on the finals code |
| `README.md:195 → 195` | WorkBuddy: three connectors | + compliance connector never in the shared list (D-30) |
| `README.md:196 → 196` | Hy3 behind CB1 and CB5 | CB1, CB5, CB7 and CB9 |
| `INTERNAL_BRAIN.md:18 → 18` | branch `final_v1.0` | branch `final_v1.3` |
| `STATUS.md:25 → 25` | WorkBuddy row | + MCP data framing and D-30 (`2b339c8`) |
| `STATUS.md:26 → 26` | Act "committed in 8312864" | + v3.1 after round 2/2b (`ca014cd`, `7fa6319`, D-28, D-31) |
| `STATUS.md:27 → 27` | Live growth "committed in 8312864" | + round-3 fixes; grow steps only with `?grow=1` (D-26) |
| `STATUS.md:— → 28-30` | (missing) | New rows: Deliverables (D-25, D-29), uploads (demo-only, id field, no picker), everyday-company world |
| `STATUS.md:32 → 35` | video tooling in `submission/finals_video/` | `docs/finals/video/`, `submission/finals_video/` |
| `STATUS.md:52 → 55-57` | "In progress, not committed: the new demo video" | Video on hold (tooling committed, clips not); + WorkBuddy GUI retest row; + confirm D-26..D-33 row |
| `docs/finals/CHANGELOG.md:6 → 6` | branch `final_v1.2` | `final_v1.3` (cut at `1076517`) |
| `docs/finals/CHANGELOG.md:8 → 8` | "All 44 commits … up to `f0f84bd`" | "All 93 commits … up to `1076517`" |
| `docs/finals/CHANGELOG.md:16-18 → 16-21` | "Being built or under review now … checkpoint `8312864`" | Not finished yet; lists what landed and was reviewed (rounds 2, 2b, 3) |
| `docs/finals/CHANGELOG.md:22-28 → 25-27` | In-progress rows Act, Watch the brain grow, One-click kit, Tencent tools, Everyday content, video ("Uncommitted script and recorder"), Live Hy3 | Landed rows removed (moved below); "WorkBuddy GUI retest" (D-30); video "On hold", tooling committed; Live Hy3 "wired, not live (402)" |
| `docs/finals/CHANGELOG.md:→ 36, 48, 71, 76, 104-105, 113-115, 123` | (missing rows) | Rows for `d8f8de7`, `19603c8`, `7a8f709`, `8312864`, `1076517`, `9819855`, `831297e`, `9afd1bd`, `a1ccc6c`, `b74e010` |
| `docs/finals/CLOUD_HANDOFF.md:109 → 109` | "(on final_v1.1 / final_v1.2)" | "+ / final_v1.3" |
| `docs/finals/CLOUD_HANDOFF.md:114 → 114-115` | "0 leaks / 5,958, 0/400, 0 injection failures" | 0 / 7,374, 0/400, 0 in 1,152, recall 100% (`9819855`) |
| `docs/finals/CLOUD_HANDOFF.md:118 → 119-120` | "Presenter (10-step keyboard script …)" | 11-step (00–10); grow steps only with `?grow=1`, D-26 |
| `docs/finals/CLOUD_HANDOFF.md:— → 135-139` | WIP table with no update | Update note: all six tracks committed and reviewed; table kept as history |
| `docs/finals/CLOUD_HANDOFF.md:181, 183-184, 209, 261, 264 → 188, 190-191, 216, 268-269, 272-273` | Round-2 findings (four dots, 733/754, 10-step, Six CodeBuddy, "still say 754", replace 5,958/976) | Each marked *Fixed*/*done* with the current value; top sections untouched |
| `docs/finals/FEATURE_CATALOGUE.md:8 → 8` | Branch `final_v1.0`, as of `f0f84bd` | Branch `final_v1.3`, as of `1076517` |
| `docs/finals/README.md:7 → 7` | Branch `final_v1.0` | `final_v1.3` (`final_v1.0` frozen) |
| `docs/finals/README.md:19 → 19-21` | "Decisions D-01 to D-23" | D-01 to D-33 (D-26..D-33 to confirm); + CLOUD_HANDOFF and WORLD_EXPANSION_STATUS rows |
| `docs/finals/README.md:24-26 → 26-30` | demo_day, live_growth, video "*In progress, not committed.*" | Committed descriptions; video "*On hold*"; + deliverables/ and live_kit/ rows |
| `docs/finals/README.md:— → 49-52` | (no pointer) | Pointer to review rounds final, 2/2b, 3 and their dispositions |
| `docs/finals/README.md:74 → 82` | TEST_REPORT "124 passed, 0 failed, 38 … skips" | "166 passed, 0 failed, 41 skipped … at `831297e`; 124 run kept as history" |
| `docs/finals/WORLD_EXPANSION_STATUS.md:— → 6-12` | "Nothing here is committed" (no update) | Dated update: committed in `8312864`; §5 items 1, 2, 5, 6, 7 done; item 3 laptop-only |
| `docs/finals/deliverables/STATUS.md:4 → 4` | "nothing committed by this agent." | + "*(at the time; since committed in `8312864`, `1701515`, `7a8f709`)*" |
| `docs/finals/contracts/backend_v1.md:277 → 277-278` | eval_summary example (5958, 976) unlabelled | Labelled as the 14 Oct shape example; current values stated |
| `docs/finals/rubric/ai_innovation/AI_USAGE_MAP.md:39 → 39` | "Voice is planned." | Tencent voice planned; browser dictation built (`1bc6b4c`), no Tencent service |
| `docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md:55 → 55` | F2 "apply once the Act work lands" | Act has landed; patch still not applied |
| `docs/finals/rubric/ai_innovation/WORKBUDDY_GUI_TEST.md:28-32 → 28-46` | compliance via workspace "or `--compliance-user-config`" in one sentence | Separate set-up block (D-30): A = `IB compliance` workspace (`internal-brain-audit`), B = separate seat with `--compliance-user-config`; warning not to run B on the employee seat |
| `WORKBUDDY_GUI_TEST.md:40 → 54` | connector table `ib-compliance` | `internal-brain-audit` (A) or `ib-compliance` (B) |
| `WORKBUDDY_GUI_TEST.md:48, 51, 55 → 62-63, 66-67, 71-73` | Trust/tool list/S1 | Compliance trusted separately; new S1b screenshot |
| `WORKBUDDY_GUI_TEST.md:60-61, 69-70 → 78-82, 90-91` | prompts 5–6 "Using the ib-compliance connector" | Separate task; `internal-brain-audit` (A) or `ib-compliance` (B) |
| `WORKBUDDY_GUI_TEST.md:82, 96 → 103-104, 118-120` | "S1: three connectors trusted"; "Remove the three connectors" | S1 two + S1b compliance; remove per seat, workspace folders hold tokens |
| `docs/tencent/workbuddy/WALNUT_SKILL.md:14-15, 36, 67, 72 → 14-17, 38, 69-70, 75` | `ib-compliance` only | Both names (seat / workspace); 5–6 in a separate task |
| `docs/finals/FOUNDER_TODO.md:6-10 → 6-20` | Today list (Hy3, Xin Yi, email "link if final_v1.1 is ready") | Evening list: email (links `final_v1.3` at `1076517`; tonight's pass not on it), Hy3 (still 402), Xin Yi, confirm D-26..D-33 (one line each), upload-folder picker build/skip |
| `FOUNDER_TODO.md:15 → 25-26` | Hallway test | + no test means the `[TESTER]` line is deleted; + laptop checks item (`--fresh`, ABSENCE_PROOF, `demo_day.ps1`, `B` keys, Codex) |
| `FOUNDER_TODO.md:18 → 29` | WorkBuddy GUI test | + prompts 5–6 from the `IB compliance` workspace (D-30) |

## Left alone on purpose

- Dated history: CHANGELOG commit rows (`7bf3246` 5,958/754, `810ddbb` 124, `4c79687` 754, `f0f84bd` 124);
  `TEST_REPORT.md` earlier-run sections; `QA_LIVE.md` ("all 10 steps", a dated QA pass); `PLAN.md` and
  `research/*` (already carry a dated note); `docs/tencent/SUBMISSION.md` and the 16 Oct deck; the
  round-2 snapshot (§3b) of CLOUD_HANDOFF beyond the annotations; `TENCENT_TOOLS_REPORT.md:12`
  ("Until today, all six …" is correct in context).
- Owned by other agents: `docs/finals/video/*`, other `docs/finals/reviews/*`, `STAGE_QA_30OCT.md`,
  `live_kit/ABSENCE_PROOF.txt`.
- `submission/finals/handouts_src`: no stale values found (it already says 1,131 / 166 / 7,374, Nine, Esc/H);
  not edited, so **no handout rebuild is needed** from this pass.

## Found, not fixed (needs a check on the laptop)

- **Knowledge-graph counts may be stale after the world expansion.** Docs say Jordan can open **867** items
  (README.md:116, INTERNAL_BRAIN.md:67, FEATURE_CATALOGUE.md:174 and :293, EVIDENCE_CARD.md:40, video files).
  The eval oracle (`evals/internal_brain/oracle.py`, generator `Acl`) gives jdoe **930** readable docs on the
  current world and **867** on the world at `7bf3246`; Ken is 24 on both. So 867 matched the old world and is
  probably 930 now, but `/ib/map` was not run, and "17 places" and the engine numbers (932 entities, 2,246
  links, 4,710 pointers) were not checked. Verify after `Start Walnut Demo --fresh`, then update.

Addendum: `docs/finals/TEST_REPORT.md:3 → 3`: "branch `final_v1.0`" → "branch `final_v1.0` for the earlier run (the
current run below is on `final_v1.2`, now `final_v1.3`)".
