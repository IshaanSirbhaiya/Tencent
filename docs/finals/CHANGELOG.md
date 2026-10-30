# Walnut DataBridge: changelog

**Answers you can prove.**

Every change since the 16 Oct 2026 submission, newest first, grouped by area. Each line links its commit
(the Tencent repo; branch `final_v1.3`, cut from `final_v1.2` at `1076517`; `final_v1.0` frozen). What each feature does and how it is tested: [FEATURE_CATALOGUE.md](FEATURE_CATALOGUE.md).

All 93 commits after the submission (`acb18f0`), up to `1076517`, were made on 30 Oct 2026 (SGT). Times are SGT.

Current evaluation numbers (run 30 Oct 11:27 SGT, final world, `eval_summary.json`; re-run on the evening of 30 Oct, unchanged): **0 leaks in 7,374 adversarial
requests**, 0 of 400 differential pairs, 0 injection hard failures in 1,152, recall 100% on both paths. A commit row
below quotes the numbers of the run made at that commit.

## In progress

Not finished yet. Each row says what is committed (everything below is on `final_v1.3`, `1076517`); work not yet
committed is **not counted in any number on this page and not claimed anywhere else.**
Each line moves to the right area below when it lands. Landed on 30 Oct and reviewed (rounds 2, 2b and 3; dispositions
in `reviews/round2_dispositions.md` and `reviews/round3_dispositions.md`): Act, Deliverables (Table, Chart, Analysis,
Slides .pptx; Summary not built, D-29), live growth and add-document (demo-only), uploads (demo-only) and the
everyday-company world. Their rows are in the areas below.

| Work | What it will be | What exists in the tree today |
|---|---|---|
| **WorkBuddy GUI retest** | The finals MCP tools shown in the real WorkBuddy app | CB7 to CB9 and the new MCP tools are committed ([9afd1bd](https://github.com/IshaanSirbhaiya/Tencent/commit/9afd1bd)). The GUI retest is open (needs the stage laptop): [WORKBUDDY_GUI_TEST.md](rubric/ai_innovation/WORKBUDDY_GUI_TEST.md). The compliance connector is set up apart from the employee connectors (D-30) |
| **New demo video** | The finals walk-through, narrated by the team | **On hold** until the founder says so. Script, recorder and assembler are committed in [video/](video/VIDEO_SCRIPT_FINALS.md); raw clips and the test cut in `submission/finals_video/` are not committed (74 MB) |
| **Live Hy3** | Hy3 answers through TokenHub | Wired, **not live**: TokenHub answers 402 until activation (a person). `demo_up.sh` will switch it on by itself once [`hy3_check.py`](../../databridge/scripts/hy3_check.py) gets an answer. The launchers pass that result to the Brain as `IB_TOKENHUB_LIVE`: a TokenHub key that is set but not live is never tried per ask (the optional stand-in model or quoted answers serve instead), and the preflight, the board and the slides say Hy3/Hunyuan LIVE only when the active chain is TokenHub and `IB_TOKENHUB_LIVE=1` (`final_v1.4` only) |
| **Demo story (app mode)** | A separate, mouse-driven way through the 11 steps for the booth and judges' hands-on time; the 5:00 stays on the keyboard presenter | **Plan only, nothing built.** [app_mode/PLAN_v2.md](app_mode/PLAN_v2.md); build window Sat 31 Oct or Sun 1 Nov |
| **Teammate merge** (voice, just-in-time UI, dots) | Three feature branches merged one at a time into new branches | The first branch (`final_mehulupdates`, pages beside the chat) is merged on `final_v1.4` only; voice and dots have not arrived. [MERGE_PLAN.md](MERGE_PLAN.md) |
| **Pages beside the chat (just-in-time views)** | After each answer, page or chat is decided (by the optional stand-in model when it is on); a page ("everything about the project, its people and what each did") opens in a canvas beside the chat, composed by the stand-in model with every value checked against its source, rendered from A2UI v0.9 messages; changes in plain words ("make the table black"); the Xcode-like light Ask, the icon-rail sidebar (D-37, D-38, D-39) | Merged on `final_v1.4` only (`cddd878`, `e47069a`; not on the lock `final_v1.3`), from branch `final_mehulupdates`: `databridge_ib/views/` (`composer.py`, `pagespec.py`, `pagecheck.py`, `service.py`, `plan.py`, `blocks.py`, `a2ui.py`), `tests/test_views.py` (25) and `tests/test_views_composer.py` (14), web `components/views/`, `components/user/ViewCanvas.tsx`, `lib/navRail.ts`, `tests/e2e/views.spec.ts` (7, mock). Tried live with the stand-in model on the generated world: decision 3 to 4 s, project page about 29 s, about US$0.05 a page. The stand-in model also writes the chat answers while Hy3 is not live (D-40: grounding wall unchanged, 3.5 to 6 s), and follow-ups are rewritten into standalone questions. Full IB suite on the branch: 1,179 passed. Details: [FEATURE_CATALOGUE §14](FEATURE_CATALOGUE.md#14-pages-beside-the-chat-just-in-time-views-d-37-d-38-d-39) |
| **Stand-in model guardrails** (D-42) | One spend ledger and one set of caps for every stand-in call, kept outside the demo state | On `final_v1.4` only: `databridge_ib/model_guard.py`, `scripts/wipe_guard.sh`, `tests/test_model_guard.py`; total, daily, service-wide and per-person limits, fail closed, untouched by every reset; "Model budget" numbers on the preflight board and in `/ib/status` (admin and compliance) |
| **Hallway test** | 5 people × 20 min; results into the fix log and the proof slide | Protocol ready ([HALLWAY_TEST_PROTOCOL.md](rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md)); Sat 31 Oct |

## 30 Oct 2026, late night (Atlas onboarding scenario, branch `feat/atlas-workflow`)

Teammate Dev's Workflow 1 ("Your first day leading Project Atlas"), integrated. Branch `feat/atlas-workflow`, from `final_v1.4-wip` (`1b6500c`), with `origin/feat/story1-fixes` (and through it `final_v1.4`, `e72b18b`) merged in.

| Area | Change |
|---|---|
| Scenario | `fixtures/scenarios/atlas_onboarding/`: the additive world (Company A plus Atlas), sources, personas, `build.py`, `run_demo.py`, `verify.py`, the teammate's offline proof. Windows fixes: UTF-8 reads and writes, a locked SQLite file no longer fails the clean-up, re-runs write their proof to `var/` (D-45) |
| Brain | `databridge_ib/subject.py`: a briefing on a named project is scoped to it (search by section, the project's own documents only, the rules page reads more of them through both gates). `pipeline.py` and `views/service.py` call it; every other question is unchanged (D-45) |
| Demo | `IB_WORLD=atlas bash scripts/demo_up.sh start` runs the main stack on the Atlas world with its own state (`var/ib-atlas`); it refuses to reuse a stack already running. Default unchanged |
| Eval | `IB_EVAL_WORLD=<path>` runs the leak eval on another world. Atlas world: 0 leaks in 7,374 requests, 0 differential mismatches |
| Tests | `tests/test_subject_scope.py` (15). Full IB suite: after the merge, on the Windows laptop, 1,494 passed and 13 failed: 10 also fail on the base commit on this machine (bash-run script tests), 1 was fixed in c06daf8 (demo_up.sh keeps its STATE line), and 2 are feat/story1-fixes tests not yet updated for that branch (the daily-cap reason code, the AI badge string). Default-world leak eval: 0 leaks in 7,374 requests, 0 differential mismatches (unchanged) |

## 30 Oct 2026, late night: Story 1 workflow-test fixes (branch `feat/story1-fixes`)

From the Story 1 ("The Call") workflow test and the model-on stage QA. Fake models only in the tests; the live
re-run of Story 1 is still to do (laptop). The run's own output is not kept in the repo.

**Test results** (Windows, Python 3.13, `IB_FORCE_OFFLINE=1`, 30 Oct late night): IB backend 1,480 passed, 2 skipped, 12 failed in the full run (54 min, on a loaded laptop). Two were tests pinning the old answer badge and the old fallback `model` object; they are updated, and their files re-run green (`test_model_guard.py`, `test_standin_labels.py`, `test_story1_fixes.py`: 101 passed, the bash tests deselected). The other 10 are the `demo_up.sh`/`wipe_guard.sh` bash tests: on this laptop `bash` with `PATH=/usr/bin:/bin` resolves to WSL and fails with an RPC error before the script runs; those tests and scripts are unchanged from `1b6500c`; mock
platforms 159 passed; fixtures 47 passed; vendor-import guard ok; web `tsc` clean; `next build` (separate
distDir) ok; web e2e (Playwright, mock, the affected specs: model-label, ask, user, compare, presenter,
preflight, views, analytics, audit-trust) 87 passed, 0 failed. Leak eval: 0 leaks in 7,374 requests, 0 of 400
P2 pairs differ, 0 injection hard failures in 1,152. The fake-capture mode now has 4 answers quoted without
a model call (D-43), each re-checked by the eval.

| Area | Change |
|---|---|
| Answers | The approved-script skip (D-43, from `feat/model-tuning`): approved wording is quoted, never re-worded by a model |
| Answers | The prompt asks for every status fact the passages give (filings, decisions, counts); grounding wall unchanged (D-44, `ib-prompt-2026-10-30.1`) |
| Answers | `model.reason_code` on a quoted fallback: `model_rejected_evidence` or `model_unavailable` (backend_v1 v5) |
| User mode | True labels for quoted answers, EN and 简体中文, no provider name; "AI-generated" shown once |
| Pages | The page-or-chat prompt sends case and status overviews to a page; "Open as page" stays the stage step |
| Page check | Value counters balance: proposed = kept + dropped |
| Security | HTTP client loggers at WARNING: no endpoint host in `ib.log` (SECURITY §15.16) |
| Model | `IB_MODEL_ANSWER_TIMEOUT_S` (default 12 s, no SDK retry) for the stand-in answer call |
| Presenter | The D warm-up starts at the proof step; a step's sign-in waits at most 8 s; business headline "Projected"; the proof slide says the eval used a test model |
| Compare, journey | A "Different answers" line; the journey's model card shows "AI model" for an unnamed model |
| Preflight | 8 s quoted / 15 s with a model; rows say "AI model"; a WARN when Rachel's answer leaves out one of her three stage facts |
| Docs | D-43, D-44; backend_v1 v5; SECURITY §15.16; RUN_SHEET bridge line for step 01 and the "Open as page" step; PRESENTER_KEYS |

## 30 Oct 2026, night (cloud session 2)

Branch `final_v1.2-cloud` (now locked as `final_v1.3`). The round-4 fixes from the laptop section below were finished
and tested here.

**Test results** (clean-checkout run in the cloud, Fri 30 Oct evening; Linux, Python 3.13, `IB_FORCE_OFFLINE=1`):
IB backend **1,274 passed, 0 failed, 4 skipped**; web e2e (Playwright, mock) **185 passed, 0
failed, 42 skipped**; mock platforms 159 passed; fixtures 47 passed; web `tsc` clean. Leak eval re-run
this evening, unchanged: 0 leaks / 7,374, 0/400 pairs differ, 0 injection hard failures in 1,152, recall 100%
([TEST_REPORT.md](TEST_REPORT.md)).

| Area | Change |
|---|---|
| Tours | Tours split (D-36) done |
| Tests | Presenter test hardened; the a11y simulator tests now quiet the Developer tour offer |
| Backend | `/ib/map`: the in-flight build is keyed by session |
| Branches | `final_v1.3` moved to the verified commit |
| Graph galaxy | Finished on `feat/graph-galaxy` at `d4ae49e`; **not merged** |
| Stand-in model ON (`final_v1.4`) | Neutral labels when the stand-in model answers: slides name Hy3/TokenHub only when `active_provider` is TokenHub; deliverable and draft by-lines say "AI-generated from your cited sources" (en + 简体中文); `demo_up.sh` keeps the stand-in on when TokenHub is not live and no TokenHub key is set; new `test_standin_labels.py`. Docs: "Model ON" variants of the honest-limits line (INTERNAL_BRAIN.md, README.md), QA_BANK section D, PRESENTER_KEYS HUD note, ORGANISER_UPDATE alternative paragraph (only with a verified `final_v1.4` link) |
| Teammate merge | `final_mehulupdates` is merged on `final_v1.4` (`cddd878`, `e47069a`); the Playwright run on `final_v1.4` refreshed the act and deliverables screens (`75a0aa8`, `2c5aae3`), but no pass count for `final_v1.4` is recorded in these docs. The verified lock is `final_v1.3` |

## 30 Oct 2026, evening (laptop)

Written on the laptop after the cloud session. Branch `final_v1.2` (the cloud's `final_v1.3` pointer stays at
`1076517`). Two kinds of rows: **committed** (a link) and **in the working tree, not committed yet** (no link; not in
any commit until the coordinator commits them). **Test counts elsewhere in the docs (1,131 backend, 166 web) are from
the cloud run at `831297e` and are unchanged here: re-run pending (final integration).** The new tests below are named
so they can be found; they are not counted.

### Round 4 reviews and their fixes

Three reviews of the cloud session's work (`8fff33c..1076517`), then the fixes. Every finding and its test:
[reviews/round4_dispositions.md](reviews/round4_dispositions.md).

| Status | Change |
|---|---|
| Committed ([c37b141](https://github.com/IshaanSirbhaiya/Tencent/commit/c37b141)) | Codex (partial, 2 runs by budget): 0 critical, 15 high, 9 medium, 2 low. [round4_codex_cloud_work.md](reviews/round4_codex_cloud_work.md) |
| Committed ([df9365d](https://github.com/IshaanSirbhaiya/Tencent/commit/df9365d)) | Opus on the web proxy, presenter, demo kit and secrets: 0 critical, 0 high, 4 medium, 9 low. [round4_opus_web_kit.md](reviews/round4_opus_web_kit.md) |
| Committed ([de94005](https://github.com/IshaanSirbhaiya/Tencent/commit/de94005)) | Sonnet live UX and accessibility re-check on the live stack: 12 open, 11 verified fixed. [round4_sonnet_ux_live.md](reviews/round4_sonnet_ux_live.md) |
| Working tree | **Act v3.2** (Codex run A). New ledger kind `action.requested` is written before the live source check, before the model call and before a replayed result (stages `accepted`, `decided`, `replayed`); a ledger failure is the 503 before Gate 2. The 80 % draft floor now also fails a negation that differs from its source, a number not in its anchor sentence and a capitalised word not in its cited facts. Two proposals confirmed together can never both create (one locked step; the second is `409 outcome_unknown`). The store cap holds under concurrent proposals; unknown-outcome holds are swept. An empty Jira `create` list grants nobody. A re-propose after an unknown outcome says so. Only the first over-limit call per person per window is ledgered. Tests: `test_act_round4.py`, `test_mock_jira_create.py`, `act.spec.ts` |
| Working tree | **`/ib/open/<token>`**: an access-check error is the same 404 as an unknown token (no existence oracle); 30 a minute per person, one ledger row per window, no `doc.viewed` when refused; the token map is built once per index version. Tests: `test_open_route.py` |
| Working tree | **Deliverables B1 to B6, B12, B14.** The `.pptx` writer strips control characters before the formula guard and writes every chart-workbook cell as a string (no `{=...}` array formula); the export writes `deliverable.requested` (new `export_format`) before Gate 2 and the source read; a model title is re-checked after a revoked source and after any model fallback; each analysis number must sit in one row or clause with its names; a table or chart label must be its column header or clause; the rate limit and a capped 4 KB read come before the body is parsed; speaker notes claim only what each slide type checked. `/audit/stats` counts exports apart from requests. Tests: `test_deliverables.py::test_r4_*`, `test_deliverables_verify.py::test_r4_*` |
| Working tree | **Sim, live poll, extraction, Drive, WorkBuddy token.** `write_ahead(None, ...)` fails closed (B8). A live poll is ledgered (`graph.viewed`, `outcome="checking"`) before its live check; Ken's poll still makes no source call (B2 live half). A sparse `.xlsx` is measured from its XML and refused over 1,000,000 cells before any reader runs (B11). A Drive download is capped while streaming (B13). The WorkBuddy setup writes the token only to a new private file whose ACL was restricted and verified, refuses a junction or symlink target and renames atomically (B7, B10). Tests: `test_sim_add_document.py`, `test_live_growth.py`, `test_extract_files.py`, `test_connector_drive.py`, `test_workbuddy_setup_private.py` |
| Working tree | **Opus web and kit fixes.** `/present/preflight` is served only on a loopback Host **and** a session the Brain confirms (the one 404 otherwise, and always when `IB_DEPLOYED=1`, which the Lighthouse compose file now sets); it fits 390 px. Login chips are **role-only** in English and 简体中文 (no channel, space, project or folder name), with a test that reads the world file. The key file's secrets go only to the Brain's own subshell; the mock platforms, engine and web never get the TokenHub key or the ledger key. The proxy lets `ib/open/<32 hex>` through only for a real click (Sec-Fetch navigate, document, user). `hy3_check.py` refuses a non-HTTPS base before sending the key. `fill_template.py` has the server's CSV guard. `demo_day.ps1` builds its JSON properly, and `--fresh` refuses to wipe while a port is still held. `check_absent.py --sim` waits and retries once on a 429. Engine nodes are 24 px targets. Tests: `preflight.spec.ts`, `user.spec.ts`, `act.spec.ts`, `engine.spec.ts`, `test_round2_kit.py` |
| Working tree | **Session queue** (`components/presenter/session.ts`): every change of who is signed in (a step, the `P` warm-up, `D`, `R`, and the shell's Sign out and persona switch) runs one at a time, and a shell Sign out wins over queued work. `D`'s cached panel lives 12 minutes. Test: `presenter.spec.ts` "a shell sign-out during the P warm-up stays signed out" |
| Working tree | **Act and Deliverables wording** (Sonnet): the ticket sheet says "You can't create a follow-up ticket from this answer. You can still draft a reply." and offers the draft; the draft sheet is "Reply draft" and shows persona names for chat handles; a 429 reads "Too many requests in a short time. Try again in a minute."; the Deliverables by-line is "Copied cell by cell from source [2]" or "Charted from the table in source [2]" with an info line "No AI model was used: this was built by code, straight from your sources."; the button is "Download slides (.pptx)"; the four fixed titles are translated; source chips have a screen-reader name and a skip link to Copy |
| Working tree | The Add button at 390 px shows a short label ("Add" / "添加") instead of an icon and "Demo" |

### Upload folder picker (D-34)

| Status | Change |
|---|---|
| Working tree | The upload tab lists Drive folders by name from `GET /sim/upload-folders`: only folders the person may upload into now, each checked live with the upload's own write check, as `[{id, path}]` sorted by path (anyone else gets `[]`); mounted under the same switches as `/sim/upload-file`; 10 a minute; ledgered as `graph.viewed` `mode="upload_folders"` with counts only. The typed folder ID stays as a collapsed "Advanced" fallback, only when the list is empty or fails. The old `fd-7` hint, which told everyone a vendor folder exists, is gone. The upload re-checks WRITE right before the write-ahead record and the Drive POST, and the mock Drive refuses an uploader without WRITE (Codex B9). Tests: `test_upload_folders.py`, `test_uploads.py`, `test_mock_drive_upload.py`, `grow.spec.ts`. Decision [D-34](DECISIONS.md) |

### Presenter mouse controls and the stage QA

| Status | Change |
|---|---|
| Working tree | **Presenter mouse controls.** The bar has clickable Next, Skip this step, Back, Start, Recording, Reset, Hide the bar and Exit presenter (each calls the same action as its key, so the busy, debounce and failed-step rules are shared); on Stage the one-row bar shows "More" for the rest. The mouse's side buttons are Back and Next while armed (the browser does not also move). Browser Back to the previous step's page steps the script back with it; any other history move is left to the browser. Ordinary clicks are never taken over. `presenter.spec.ts` 23/23. Keys: [PRESENTER_KEYS.md section 2a](rubric/demo_story/PRESENTER_KEYS.md). The keyboard is still how the 5:00 is driven |
| Committed ([1141cfb](https://github.com/IshaanSirbhaiya/Tencent/commit/1141cfb)) | **Stage QA 30 Oct** (headless Chrome, 4 default walks, `B` at every step, grow walk-up, preflight, absence proof 12 of 12): no failed step; the slowest step (tamper) settles in 2.5 to 3.7 s. [STAGE_QA_30OCT.md](demo_day/STAGE_QA_30OCT.md) lists 11 risks |
| Working tree | Stage QA fixes: the grow step waits up to 60 s for Jordan's graph (it timed out at 20 s, 2 of 2); `P` shows step 00 at once and Leo's warm-up runs after it; steps 05 and 06 stay "Working" until the Verify result or "Chain broken at entry N" is on screen; `B` on a slide shows "No recording for this step" instead of blacking the slide; the HUD model line reads "quoting (no model)"; `check_absent.py --all --sim` waits out the rate limit. Clip C is described as the in-app stand-in it is ([6ade8cf](https://github.com/IshaanSirbhaiya/Tencent/commit/6ade8cf)). Still open: record the real WorkBuddy clip C; re-record A and B on the final build (clip B shows "Model unknown · Ledger unknown") |
| Committed ([8ae0f1f](https://github.com/IshaanSirbhaiya/Tencent/commit/8ae0f1f)) | Demo kit: `demo_up.sh` ignores Windows' PID 0 placeholder when it checks whether a port is held (it had read as "something is listening") |

### Video tooling

| Commit | Change |
|---|---|
| [6e58274](https://github.com/IshaanSirbhaiya/Tencent/commit/6e58274) | A failed re-record never overwrites the last good take (Codex 2b #3) |
| [d89f55a](https://github.com/IshaanSirbhaiya/Tencent/commit/d89f55a) | Readiness pass: current numbers, nine CodeBuddy sessions, every number read from the evidence; longer-cut timeline plan. [READINESS_30OCT.md](video/READINESS_30OCT.md) |
| [2669d40](https://github.com/IshaanSirbhaiya/Tencent/commit/2669d40) | Full script, scenes 17 to 21 (Act, Deliverables, upload, Miora, extras); `--cut short\|main\|full`; slots for teammate-recorded scenes |
| [360d72b](https://github.com/IshaanSirbhaiya/Tencent/commit/360d72b) | Fable storytelling pass: hook, tagline at the tamper, new close, truth fixes, trims. [review_fable_script.md](video/review_fable_script.md) |
| [54c80fb](https://github.com/IshaanSirbhaiya/Tencent/commit/54c80fb) | The script follows the **16 Oct video's spine beat by beat**, with each new feature attached to the beat it strengthens ("and now it also..."); criteria table; Tencent in the title. Main cut 7:56, short 6:18, full 9:43 ([VIDEO_PLAN.md](video/VIDEO_PLAN.md)). The video is still on hold; nothing was recorded |

### Q&A banks, merge plan, app mode

| Commit | Change |
|---|---|
| [5dba9c8](https://github.com/IshaanSirbhaiya/Tencent/commit/5dba9c8) | Q&A bank Q26 to Q48 (the new features and hard judge questions); judge click paths N1 to N7 ([QA_BANK.md](rubric/demo_story/QA_BANK.md), [JUDGE_QUESTION_BANK.md](rubric/demo_story/JUDGE_QUESTION_BANK.md)) |
| [ab01042](https://github.com/IshaanSirbhaiya/Tencent/commit/ab01042) | **Merge plan** for the teammate's three branches (voice, just-in-time UI, dots) in that order, each into its own new branch, with hard-rule checklists, and a read-only `databridge/scripts/merge_precheck.py`. Nothing is merged. [MERGE_PLAN.md](MERGE_PLAN.md) |
| [adfc568](https://github.com/IshaanSirbhaiya/Tencent/commit/adfc568) | **App mode, plan v2: "Demo story"** (a separate mouse-driven way through the same 11 steps, for the booth and judges' hands-on time; the 5:00 stays on the keyboard presenter). Plan v1 ([0c29e01](https://github.com/IshaanSirbhaiya/Tencent/commit/0c29e01)) and four reviews ([1717a4c](https://github.com/IshaanSirbhaiya/Tencent/commit/1717a4c) Opus, [ebe43dc](https://github.com/IshaanSirbhaiya/Tencent/commit/ebe43dc) Haiku and Fable, [1141cfb](https://github.com/IshaanSirbhaiya/Tencent/commit/1141cfb) Sonnet) are dispositioned in [PLAN_v2.md](app_mode/PLAN_v2.md). **Planned, not built:** build window Sat 31 Oct or Sun 1 Nov, stop rule Sun 18:00; no D-35 is written yet |

Docs: the cross-cutting docs were brought up to date in this pass ([reviews/docs_pass_30oct_evening.md](reviews/docs_pass_30oct_evening.md)); the earlier evening pass is [60c0226](https://github.com/IshaanSirbhaiya/Tencent/commit/60c0226).

## 30 Oct 2026: the finals build

### Demo and Presenter

| Commit | Time | Change |
|---|---|---|
| [d8f8de7](https://github.com/IshaanSirbhaiya/Tencent/commit/d8f8de7) | 13:15 | Presenter (round-3 Fable): once `P` has started, `Esc` never hides the HUD and `H` does (D-33); `Space` drives the grow walk-up in a `?grow=1` tab; `Esc` on the `D` panel signs the previous person back in; Analysis says "Needs the model (Hy3)" with no model; the 简体中文 `L` flip cut from the 5:00 (D-32); handout PDFs rebuilt |
| [90393b2](https://github.com/IshaanSirbhaiya/Tencent/commit/90393b2) | 11:56 | Presenter grow steps need `?grow=1` on the tab (never on the Space path; demo features alone no longer add them); `opencv-python-headless` pinned so a fresh install keeps PDF and PPTX uploads |
| [f0f84bd](https://github.com/IshaanSirbhaiya/Tencent/commit/f0f84bd) | 10:16 | Printable handouts in `submission/finals/`: A4 leave-behind with QR, judge cards per rubric criterion, presenter cheat-sheet, with a generator that reads the numbers from the repo; `eval_summary.json` now records 124 e2e tests (it said 38) |
| [baf8a07](https://github.com/IshaanSirbhaiya/Tencent/commit/baf8a07) | 09:52 | Backup clips A (Compare, Ken, source), B (journey, Verify, tamper) and C (in-app stand-in for WorkBuddy) in `public/clips/` for the **B** key; status table in [BACKUP_PLAN.md](rubric/demo_story/BACKUP_PLAN.md#recorded-clips-status-30-oct-2026) |
| [82c12e7](https://github.com/IshaanSirbhaiya/Tencent/commit/82c12e7) | 06:24 | Proof slide: source paths wrap instead of overlapping |
| [edb0dee](https://github.com/IshaanSirbhaiya/Tencent/commit/edb0dee) | 06:05 | Bulletproof demo path: generation-bound presenter actions, serialised session changes, safe reset, Compare runs report real success, stale request ids invalidated, cancellable asks, honest SSE status line; Stage polish (one-row HUD, hidden chrome, `/present` landing, full names, rounded slide figures, Hy3 lines only when live, tamper progress) |
| [708d902](https://github.com/IshaanSirbhaiya/Tencent/commit/708d902) | 04:11 | Compare (two people; one person, two questions; "Identical responses" marker), Presenter mode with the Stage theme and keyboard script, `/present` slides |

### User mode

| Commit | Time | Change |
|---|---|---|
| [19603c8](https://github.com/IshaanSirbhaiya/Tencent/commit/19603c8) | 12:51 | Round-3 web UX fixes for Deliverables, live growth and the engine refresh: focus return, no duplicate post, step-only announcements, `lang=en` on server text in 简体中文, 390 px layout, "Live updates stopped" state |
| [6de5cd6](https://github.com/IshaanSirbhaiya/Tencent/commit/6de5cd6) | 12:25 | Round-2 web UX and honesty fixes: mock labels default to "Quoted from sources (no model)" everywhere; login notes and starters no longer hint at hidden content (rule 4); HUD re-reads status after sign-in; Act sheet fixes (zh `lang=en` on server text, sticky confirm at 390 px, `outcome_unknown` and 503 after Confirm worded honestly); Leo cache cleared on sign-out |
| [1bc6b4c](https://github.com/IshaanSirbhaiya/Tencent/commit/1bc6b4c) | 09:47 | Voice dictation in the Ask composer: browser speech, English / 简体中文, never auto-sends, accessible, hidden when unsupported (`voice.spec.ts`) |
| [ac94931](https://github.com/IshaanSirbhaiya/Tencent/commit/ac94931) | 04:42 | Interactive tours: User "Try it" (sign in, ask, open a source, Ken's identical refusals, checklist) and Developer (pipeline, journey, graph, trust, analytics / Verify); spotlight and "Do it for me"; English / 简体中文 |
| [b14f101](https://github.com/IshaanSirbhaiya/Tencent/commit/b14f101) | 03:45 | User mode: persona sign-in, Ask with live stage events, cited answers, sources sheet, honest refusals, History, Settings, English / 简体中文 |
| [8fe6b68](https://github.com/IshaanSirbhaiya/Tencent/commit/8fe6b68) | 03:04 | Foundation: tokens, primitives, shell with User / Developer modes, i18n, route skeletons, API client, graph and QR dependencies |

### Developer mode

| Commit | Time | Change |
|---|---|---|
| [ca72003](https://github.com/IshaanSirbhaiya/Tencent/commit/ca72003) | 04:45 | Live QA fixes: robust graph layout fallback, readable Slack / Jira versions, citation attachment for live answers, audit `?latest=1` and full window, waterfall seal ticks, Compare live-region status |
| [71456de](https://github.com/IshaanSirbhaiya/Tencent/commit/71456de) | 04:04 | Mission control (live pipeline), request journey (compliance waterfall and employee receipt), end-to-end Analytics, `/value` ROI calculator |
| [06bc7c7](https://github.com/IshaanSirbhaiya/Tencent/commit/06bc7c7) | 03:52 | Mock: the document panel takes authors and links from the graph mock, so both agree |
| [70b6fdc](https://github.com/IshaanSirbhaiya/Tencent/commit/70b6fdc) | 03:52 | `/dev/graph`: permission-aware WebGL knowledge graph (Sigma): explore, search to focus, one-hop expand, node panel with passage and lineage, table view, compare two people |
| [a1c0547](https://github.com/IshaanSirbhaiya/Tencent/commit/a1c0547) | 03:48 | Audit console (plain-English search, hash-chain Verify, sandbox tamper demo), Simulator, Trust centre; `/audit` and `/sim` redirect to `/dev` |
| [7041b4d](https://github.com/IshaanSirbhaiya/Tencent/commit/7041b4d) | 03:43 | `/dev/brain` Walnut engine view (graph, time slider, provenance, ontology) behind a locked-down read-only proxy; dev and start bind to 127.0.0.1 |
| [c140561](https://github.com/IshaanSirbhaiya/Tencent/commit/c140561) | 03:05 | Per-area mock handler registry; Wave-1 agent rules ([AGENT_RULES.md](contracts/AGENT_RULES.md)) |

### Backend (Internal Brain)

| Commit | Time | Change |
|---|---|---|
| [7a8f709](https://github.com/IshaanSirbhaiya/Tencent/commit/7a8f709) | 13:03 | Round-3 Opus fixes: the `.pptx` formula guard also quotes array formulas and link prefixes; `/sim/upload-file` mounted only with demo features and demo sign-in; add-document and upload write ledger records before and after the source call (503 if the ledger is down); route-auth test walks included routers |
| [2b339c8](https://github.com/IshaanSirbhaiya/Tencent/commit/2b339c8) | 12:22 | Round-2b fixes: WorkBuddy MCP tools frame answers, titles, citations and container names as data, never instructions, and neutralise forged markers (tested that the frame is there, not that an agent obeys it); compliance MCP config kept apart; `check_absent` and `demo_preflight` refuse non-loopback hosts unless `--allow-remote`; mock Jira checks the caller like real Jira |
| [7fa6319](https://github.com/IshaanSirbhaiya/Tencent/commit/7fa6319) | 12:20 | Act draft grounding: a model draft sentence must take 80% of its content words from its own cited sources, else it is dropped; with nothing left the draft falls back to the cited sentences (round-2b Opus #1) |
| [ca014cd](https://github.com/IshaanSirbhaiya/Tencent/commit/ca014cd) | 12:19 | Act v3.1: per-person rate limit and pending-proposal cap, opaque gated source links in tickets (`/ib/open/<hmac>` runs Gate 1 + Gate 2 for the clicker), confirm re-checks the cited sources, Jira timeouts and 5xx are `outcome_unknown` (never "nothing changed"); kit fixes |
| [1701515](https://github.com/IshaanSirbhaiya/Tencent/commit/1701515) | 12:08 | **Deliverables from an answer** (table, chart, analysis; `.csv` and `.pptx` export; kill switch `IB_DELIVERABLES`, D-25). P0 confirmed and P1 Slides: `.pptx` export of a ready table or chart, no model, same live re-check as the CSV, every string through the formula guard (T15), offered only when `python-pptx` and `XlsxWriter` are installed. Built and tested offline (model path with a fake chain only). Analysis says "Needs the model" until Hy3 is live; Summary not built. Live UI run not done. First landed in `8312864`; see `git log -- databridge/apps/internal_brain/databridge_ib/deliverables` |
| [8312864](https://github.com/IshaanSirbhaiya/Tencent/commit/8312864) | 11:39 | `final_v1.2` checkpoint: **Act** (draft a reply or a follow-up Jira ticket, created only after the person confirms; `actions.py`), Deliverables P0, **live graph growth and add-document** (demo-only, `IB_DEMO_FEATURES=1` plus the Settings switch; `live.py`, `engine_refresh.py`), **file uploads** (demo-only; `uploads.py`, `extract_files.py`) and the **everyday-company world** (append-only: 34 people, 1,207 docs, 72 facts, 546 questions; `fixtures/enterprise/workplace.py`), plus the `narrate.py` tie-break fix. All reviewed afterwards in rounds 2, 2b and 3 |
| [7bf3246](https://github.com/IshaanSirbhaiya/Tencent/commit/7bf3246) | 06:19 | Final-review fixes: graph edges from authorised current versions; SSE gate events held until sealed; caps after live auth; one deadline through read-through; tamper lock safety; map via the authz helper; trace failure ledgered; Host allowlist on the Brain and the mock; trace sync TTL; graph re-search after denials; edit-record flips a decision; neutral fixture disclaimer. Fresh full eval 0 / 5,958 and 0 / 400. **754 tests pass** (`test_final_review_fixes.py`) |
| [8582f4b](https://github.com/IshaanSirbhaiya/Tencent/commit/8582f4b) | 05:03 | Simulator reset runs a full reconciliation (revocations undone at once); offline plain-English audit search resolves document titles; plain-English window notes; `limit` no longer echoed as a filter |
| [e9ce768](https://github.com/IshaanSirbhaiya/Tencent/commit/e9ce768) | 03:36 | Round 2: sandbox tamper-demo endpoint, SSE stage events (`/ib/ask/stream`), Hy3 for plain-English audit search with rule fallback, silent-revocation seeding, fresh full eval, SECURITY §15 |
| [9826de0](https://github.com/IshaanSirbhaiya/Tencent/commit/9826de0) | 03:08 | Web proxy: unbuffered pass-through for `/ib/ask/stream` |
| [405d34b](https://github.com/IshaanSirbhaiya/Tencent/commit/405d34b) | 03:07 | Permission-aware `/ib/graph` (+ `openable`), `/ib/doc`, `/ib/status`, trace as a public receipt, version-bound authz helper, stats flows / window / cache, ledgered failures, replay seeder, eval summary, proxy allowlist |
| [e5406b9](https://github.com/IshaanSirbhaiya/Tencent/commit/e5406b9) | 02:26 | Stage timings, `/audit/stats`, `/audit/journey`, own-request `/ib/trace` for Developer mode |

### Security

| Commit | Time | Change |
|---|---|---|
| [8e53a0b](https://github.com/IshaanSirbhaiya/Tencent/commit/8e53a0b) | 10:13 | Proxy: a 7 MB body cap for `POST sim/upload-file` only (for the live file ingest demo, in progress); every other route keeps 64 KiB |
| [1079bd2](https://github.com/IshaanSirbhaiya/Tencent/commit/1079bd2) | 10:07 | Proxy allowlist: `GET /ib/graph/changes` for the main session and Compare slots (for live growth, in progress) |
| [7bf3246](https://github.com/IshaanSirbhaiya/Tencent/commit/7bf3246) | 06:19 | Host allowlist (`TrustedHostMiddleware`) on the Brain and the mock platforms; SSE gate events held (see Backend) |
| [bc8414c](https://github.com/IshaanSirbhaiya/Tencent/commit/bc8414c) | 05:23 | Web proxy refuses a non-loopback Host (DNS rebinding) and cross-site GETs; the engine snapshot is readable only by its own persona (compliance no longer) |

### Accessibility and i18n

| Commit | Time | Change |
|---|---|---|
| [ca7a2ff](https://github.com/IshaanSirbhaiya/Tencent/commit/ca7a2ff) | 06:06 | English starter questions tagged `lang="en"` in 简体中文 (WCAG 3.1.2) |
| [01d8ac7](https://github.com/IshaanSirbhaiya/Tencent/commit/01d8ac7) | 06:05 | From the final reviews: top bar reflow at 130% text; 简体中文 skip link and roles; Developer-mode English note; 24 px chips; unique landmarks; one live region; safe `?next` deep links with a session timeout; readable journey seals; audit year, i18n and tamper banner; speaker labels; `safeHref`; graph layout cleared on persona change |

### Tests and evaluation

| Commit | Time | Change |
|---|---|---|
| [1076517](https://github.com/IshaanSirbhaiya/Tencent/commit/1076517) | 13:32 | Cloud test run written into every doc: 1,131 backend passed, 0 failed; e2e 166 passed, 0 failed, 41 skipped; mock platforms 154; fixtures 47; `tsc` clean |
| [9819855](https://github.com/IshaanSirbhaiya/Tencent/commit/9819855) | 13:31 | Full eval re-run on the tree after the day's backend changes: unchanged (0 leaks / 7,374, 0 / 400 pairs, 0 hard failures in 1,152 injection-relevant requests) |
| [ddadaa1](https://github.com/IshaanSirbhaiya/Tencent/commit/ddadaa1) | 06:40 | e2e results written up: [TEST_REPORT.md](TEST_REPORT.md) |
| [810ddbb](https://github.com/IshaanSirbhaiya/Tencent/commit/810ddbb) | 06:40 | Full e2e suite ported to the finals UI: **124 passed, 0 failed**, 38 env-gated skips (three consecutive runs); tour offer moves to the top on phones; dead `AnswerPanel` / `SourcesPanel` removed |

### Scripts

| Commit | Time | Change |
|---|---|---|
| [831297e](https://github.com/IshaanSirbhaiya/Tencent/commit/831297e) | 13:16 | Live kit: a `/sim` 404 is reported as "one of the three demo switches is wrong" |
| [9afd1bd](https://github.com/IshaanSirbhaiya/Tencent/commit/9afd1bd) | 11:12 | Tencent tools: CodeBuddy CB7 (finals security review on Hy3), CB8 (WorkBuddy connector upgrade), CB9 (Walnut WorkBuddy skill); new MCP tools (answer receipt, what I can open, org summary, tamper demo); live WorkBuddy MCP test 33/33 |
| [a1ccc6c](https://github.com/IshaanSirbhaiya/Tencent/commit/a1ccc6c) | 10:55 | One-click demo kit: Start/Stop/Reset/offline launchers, preflight board with pre-warm, duplicate-safe `demo_up` |
| [8b69cbd](https://github.com/IshaanSirbhaiya/Tencent/commit/8b69cbd) | 09:43 | `hy3_check.py` (is Hy3 live on TokenHub?); `demo_up.sh` turns live Hy3 on by itself when it answers |
| [dda470b](https://github.com/IshaanSirbhaiya/Tencent/commit/dda470b) | 03:46 | `demo_up.sh` starts the whole Demo Day stack (mock, Brain, seed, engine, web) on localhost |

### Docs, rubric and plan

| Commit | Time | Change |
|---|---|---|
| [b74e010](https://github.com/IshaanSirbhaiya/Tencent/commit/b74e010) | 12:26 | Decisions D-26 to D-31 from the cloud session (D-32, D-33 in [d8f8de7](https://github.com/IshaanSirbhaiya/Tencent/commit/d8f8de7)) |
| [d7e461e](https://github.com/IshaanSirbhaiya/Tencent/commit/d7e461e) | 12:06 | One number set for current claims: 0 leaks / 7,374, 0 / 400 differential pairs, 0 / 1,152 injection; dated history keeps its old numbers; handouts rebuilt |
| [fdbdec2](https://github.com/IshaanSirbhaiya/Tencent/commit/fdbdec2) | 09:47 | Miora prompt for the new cover ([COVER_MIORA_PROMPT.md](COVER_MIORA_PROMPT.md)); the brief lists the cover and narration tasks |
| [1da9dae](https://github.com/IshaanSirbhaiya/Tencent/commit/1da9dae) | 06:24 | Morning brief rewritten for the finished build |
| [4c79687](https://github.com/IshaanSirbhaiya/Tencent/commit/4c79687) | 06:19 | Backend test count 754 in the docs |
| [fcb6cab](https://github.com/IshaanSirbhaiya/Tencent/commit/fcb6cab) | 05:22 | Claims-consistency pass from the final Haiku review (built vs pending, honest Hy3 and deployment wording, broken paths, demo cast, run sheet aligned with the app) |
| [d090131](https://github.com/IshaanSirbhaiya/Tencent/commit/d090131) | 04:57 | Docs for judges: README quick start, INTERNAL_BRAIN 10-minute walk-through, STATUS, ARCHITECTURE §8, finals index, organiser update |
| [cb619d5](https://github.com/IshaanSirbhaiya/Tencent/commit/cb619d5) | 04:43 | Morning brief progress update |
| [8923ebd](https://github.com/IshaanSirbhaiya/Tencent/commit/8923ebd) | 03:49 | Rubric: graph / document claim row updated now both are built and tested |
| [ba20db3](https://github.com/IshaanSirbhaiya/Tencent/commit/ba20db3) | 03:09 | Morning brief and organiser update draft |
| [4403694](https://github.com/IshaanSirbhaiya/Tencent/commit/4403694) | 02:52 | Rubric index mapping each criterion to its deliverables ([rubric/README.md](rubric/README.md)) |
| [bf4a1d3](https://github.com/IshaanSirbhaiya/Tencent/commit/bf4a1d3) | 02:52 | Responsible-AI pack (system card, OWASP LLM red team, framework crosswalk and more), technical pack, CI workflow |
| [f174b6c](https://github.com/IshaanSirbhaiya/Tencent/commit/f174b6c) | 02:50 | Impact, feasibility, ROI calculator spec, AI usage map, innovation one-pager |
| [9a731e1](https://github.com/IshaanSirbhaiya/Tencent/commit/9a731e1) | 02:47 | HCD / UX evidence templates, demo story pack, competitor recon, Opus plan review |
| [8e479bc](https://github.com/IshaanSirbhaiya/Tencent/commit/8e479bc) | 02:39 | Research, rubric research, five plan reviews, plan v2 and decisions ([PLAN.md](PLAN.md), [DECISIONS.md](DECISIONS.md)) |

## 16 Oct 2026: the submission (baseline)

Last submission commit: [acb18f0](https://github.com/IshaanSirbhaiya/Tencent/commit/acb18f0) (one-file
conversation history PDF). At submission: 639 Internal Brain tests and 38 web e2e tests passed; the
leak evaluation gave 0 leaks; deck, video, cover and CodeBuddy evidence are in
[submission/](../../submission/README.md). The backend rules (two gates, identity from the token,
write-ahead ledger, one response shape) are unchanged since then.
