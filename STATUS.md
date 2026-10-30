# Status

**Date:** 30 Oct 2026. Branch `final_v1.3`. Team Walnut is a FinTech finalist; Demo Day is Tue 3 Nov 2026.
Product: **Walnut DataBridge**, with **Internal Brain** as the ask product inside it.
Start at [INTERNAL_BRAIN.md](INTERNAL_BRAIN.md). The finals plan is [docs/finals/PLAN.md](docs/finals/PLAN.md).
Every feature and its test: [FEATURE_CATALOGUE.md](docs/finals/FEATURE_CATALOGUE.md). Every change since
the submission, and what is in progress: [CHANGELOG.md](docs/finals/CHANGELOG.md). As of branch `final_v1.3` (30 Oct, cloud)
plus the laptop's evening work on `final_v1.2` (below; the round-4 fixes are now committed on `final_v1.2-cloud`,
now locked as `final_v1.3`, and the full test re-run on a clean checkout in the cloud on the evening of 30 Oct covers them).

More 30 Oct evening documents: [MERGE_PLAN.md](docs/finals/MERGE_PLAN.md) (the teammate's three branches; the first, `final_mehulupdates`, is merged on `final_v1.4` only, not on the lock `final_v1.3`) ·
[app_mode/PLAN_v2.md](docs/finals/app_mode/PLAN_v2.md) (the "Demo story" app mode, planned, not built) ·
[STAGE_QA_30OCT.md](docs/finals/demo_day/STAGE_QA_30OCT.md) · video [READINESS_30OCT.md](docs/finals/video/READINESS_30OCT.md) and
[review_fable_script.md](docs/finals/video/review_fable_script.md) · round-4 reviews
[code review](docs/finals/reviews/round4_codex_cloud_work.md), [web and kit review](docs/finals/reviews/round4_opus_web_kit.md),
[live UX review](docs/finals/reviews/round4_sonnet_ux_live.md), [dispositions](docs/finals/reviews/round4_dispositions.md).

## 1. Built

| Area | State | Where |
|---|---|---|
| Internal Brain service: two permission gates, grounding, write-ahead sealed log, one response shape | Built (unchanged rules since submission) | `databridge/apps/internal_brain/` |
| Developer endpoints: `/ib/graph`, `/ib/graph/openable`, `/ib/doc/{id}`, `/ib/trace/{req}`, `/ib/status`, `/ib/ask/stream` (SSE stages), `/audit/stats`, `/audit/journey/{req}`, `/audit/tamper-demo` | Built | `databridge_ib/app.py`, `databridge_ib/audit/routes.py`; contract [backend_v1.md](docs/finals/contracts/backend_v1.md) |
| Seeded demo history: about 300 replay-labelled questions through the real pipeline, with 2 real Gate-2 denials | Built | `databridge/scripts/seed_demo_activity.py` |
| Web app, User mode: sign-in, Ask with live stage events, sources sheet, History, Settings (English / 简体中文, accessibility), Compare, Value | Built | `databridge/apps/internal_brain_web/` |
| Voice dictation in the Ask composer (browser speech, English / 简体中文, never auto-sends, hidden when unsupported) | Built (`1bc6b4c`) | `components/user/Composer.tsx`, `useDictation.ts`; `tests/e2e/voice.spec.ts` |
| Web app, Developer mode: Mission control, Journey, Knowledge graph, Walnut engine, Analytics, Audit with sandbox tamper demo, Simulator, Trust centre | Built | same |
| Presenter mouse controls (evening): clickable Next, Back, Skip, Start, Recording, Reset, Hide and Exit on the bar; mouse side buttons as Back and Next; browser Back steps the script back from the previous step's page. Keyboard-driven by default; the controls are the fallback | Built and committed (`751a687`); `presenter.spec.ts` (23 tests) is part of the 185-pass clean-checkout cloud run | [PRESENTER_KEYS.md section 2a](docs/finals/rubric/demo_story/PRESENTER_KEYS.md), `components/presenter/` |
| Presenter mode (11-step keyboard script, HUD 00 to 10; Stage theme, slides) and tours for both modes. The three live-growth steps are added only in a tab opened with `?grow=1` (14 steps), never on the 5:00 Space path | Built | `components/presenter/`, `components/tour/` |
| Backup clips A, B, C for the presenter `B` key | Interim recordings (`baf8a07`); re-record after the Monday freeze; C is an in-app stand-in for WorkBuddy | `databridge/apps/internal_brain_web/public/clips/`, [BACKUP_PLAN.md](docs/finals/rubric/demo_story/BACKUP_PLAN.md) |
| Security from the final reviews: Host allowlist on the Brain and the mock, proxy refuses foreign Host and cross-site GETs, SSE gate events held until sealed, engine snapshot readable only by its own persona | Built (`bc8414c`, `7bf3246`) | [SECURITY.md §15](docs/tencent/SECURITY.md#15-developer-views-and-the-finals-v2-additions-30-oct), [FEATURE_CATALOGUE §7](docs/finals/FEATURE_CATALOGUE.md#7-security-controls) |
| One-command launcher | Built | [`databridge/scripts/demo_up.sh`](databridge/scripts/demo_up.sh) |
| Hy3 readiness check; the launcher turns live Hy3 on by itself when TokenHub answers | Built (`8b69cbd`) | [`databridge/scripts/hy3_check.py`](databridge/scripts/hy3_check.py) |
| Rubric evidence pack, one deliverable set per criterion | Written | [docs/finals/rubric/README.md](docs/finals/rubric/README.md) |
| WorkBuddy connectors (ask as a person; compliance audit) | Built; new MCP tools added 30 Oct (answer receipt, what I can open, org summary, tamper demo; `9afd1bd`); live WorkBuddy test 33/33. Source text is framed as data; the compliance connector is never written into the shared WorkBuddy config (`--compliance-user-config` on its own seat, or its own workspace; D-30, `2b339c8`) | `docs/tencent/workbuddy/` |
| Act: draft a reply or a follow-up ticket, created only after the person confirms | Built and tested (`test_act.py` 21, `test_act_e2e.py` 6 passed; e2e `act.spec.ts` 8 tests); committed in `8312864`; v3.1 after the round-2 and 2b reviews (per-person rate limit and cap, gated source links, confirm re-check, draft grounding 80 %: `ca014cd`, `7fa6319`, D-28, D-31). **v3.2 (evening, committed in `cb7b1a1`)** after the round-4 Codex review: `action.requested` written before the source check, the model call and a replay; draft floor also checks negation, numbers and names; one create per answer; `/ib/open` returns one 404 for any access-check error and is rate limited ([round4_dispositions.md](docs/finals/reviews/round4_dispositions.md), tests `test_act_round4.py`, `test_open_route.py`) | `databridge_ib/actions.py`, `components/user/ActSheet.tsx`; [backend_v1.md](docs/finals/contracts/backend_v1.md) |
| Live graph growth and add-document (demo-only: `IB_DEMO_FEATURES=1` and the Settings switch) | Built and tested; committed in `8312864` (proxy route `1079bd2`); round-3 review fixes in `19603c8`, `7a8f709`, `d8f8de7`. Presenter grow steps only in a `?grow=1` tab (D-26) | [live_growth/STATUS.md](docs/finals/live_growth/STATUS.md) |
| Deliverables from an answer (kill switch `IB_DELIVERABLES`, D-25): Table and Chart with no model, Analysis ("Needs the model" until Hy3 is live), Slides `.pptx` export with the formula guard. Summary not built (D-29) | Built and tested offline (`1701515`, round-3 fixes `7a8f709`); model path tested with a fake chain only. Evening round-4 fixes, committed in `cb7b1a1` and `751a687` (B1 to B6, B12, B14: export ledgered first, `.pptx` formula guard order, number and label binding, capped body read, honest speaker notes; new wording and a "Download slides (.pptx)" button) | [deliverables/STATUS.md](docs/finals/deliverables/STATUS.md), FEATURE_CATALOGUE §12 |
| File uploads into Drive (Word, Excel, PowerPoint, PDF, CSV, Markdown, WhatsApp export; demo-only, `/sim/upload-file` mounted only with demo features and demo sign-in) | Built and tested (`8312864`, round-3 fixes `7a8f709`). **Evening (D-34, committed in `2a2b90f` and `751a687`): a folder picker** lists, by name, only the Drive folders the person may upload into (`GET /sim/upload-folders`); the typed folder id is a collapsed fallback; the write permission is re-checked right before the write (`test_upload_folders.py`, `test_uploads.py`) | [contracts/uploads.md](docs/finals/contracts/uploads.md) |
| Everyday-company world (append-only: 34 people, 1,207 documents, 72 facts, 546 questions) | Built and verified (`8312864`); 464 earlier questions answer byte-identically; eval 0 / 7,374 | [WORLD_EXPANSION_STATUS.md](docs/finals/WORLD_EXPANSION_STATUS.md) |
| One-click demo kit (`Start/Stop/Reset Walnut Demo.cmd`, `demo_day.ps1`, `demo_preflight.py`) | Built (`a1ccc6c`) | repo root, `databridge/scripts/` |
| CodeBuddy sessions CB7 to CB9 on the finals code | Done (`9afd1bd`) | [TENCENT_TOOLS_REPORT.md](docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md) |
| Live-ingestion kit | Written (`6c868bb`) | [LIVE_INGEST_KIT.md](docs/finals/live_kit/LIVE_INGEST_KIT.md) |
| Printable handouts | Built (`f0f84bd`) | `submission/finals/handouts_src/` |
| Round-4 reviews of the cloud work (three independent AI review seats: 0 critical / 15 high / 9 medium / 2 low; 0 / 0 / 4 / 9; 12 open, 11 verified fixed) and their fixes | Reviews committed (`c37b141`, `df9365d`, `de94005`); fixes committed (`2a2b90f`, `cb7b1a1`, `3b9e0ca`, `751a687`) and covered by the clean-checkout cloud run of 30 Oct evening | [round4_dispositions.md](docs/finals/reviews/round4_dispositions.md) |
| Stage QA 30 Oct: 4 default walks, `B` at every step, grow walk-up, preflight, absence proof 12/12 | Done (`1141cfb`); 11 risks listed, the presenter ones fixed and committed (`751a687`); real WorkBuddy clip C and re-recorded A and B still open | [STAGE_QA_30OCT.md](docs/finals/demo_day/STAGE_QA_30OCT.md) |
| Q&A bank Q26 to Q48 and judge click paths N1 to N7 | Written (`5dba9c8`) | [QA_BANK.md](docs/finals/rubric/demo_story/QA_BANK.md) |
| Teammate merge plan (voice, just-in-time UI, dots) and the read-only `merge_precheck.py` | Written (`ab01042`); the first branch (`final_mehulupdates`, pages beside the chat) is merged on `final_v1.4` only (`cddd878`, `e47069a`); voice and dots have not arrived | [MERGE_PLAN.md](docs/finals/MERGE_PLAN.md) |
| "Demo story" app mode (mouse-driven, for the booth and hands-on time; the 5:00 stays on the keyboard presenter) | **Planned, not built.** Plan v2 after four reviews (`adfc568`); build window Sat 31 Oct or Sun 1 Nov | [app_mode/PLAN_v2.md](docs/finals/app_mode/PLAN_v2.md) |
| Video tooling (script, recorder, assembler) | Built (`215e31c`); evening: take safety (`6e58274`), script on the 16 Oct spine beat by beat with cuts short/main/full (`2669d40`, `360d72b`, `54c80fb`). The video is still on hold; nothing recorded | `docs/finals/video/`, `submission/finals_video/` |
| Persona suggestion chips: starter questions verified against the live pipeline | Built (`ec502cd`) | `databridge/apps/internal_brain_web/lib/personas.ts` |

## 2. Tested

| Check | Result | Where |
|---|---|---|
| Backend tests (Internal Brain) | **1,274 passed**, 0 failed, 4 skipped (30 Oct 2026, evening, cloud clean checkout: Linux, Python 3.13, `IB_FORCE_OFFLINE=1`, branch `final_v1.2-cloud`, now locked as `final_v1.3`) | `databridge/apps/internal_brain/tests/` |
| Mock platforms, fixtures, web typecheck | Mock platforms 159 passed; fixtures (enterprise) 47 passed; web `tsc --noEmit` clean (30 Oct 2026, evening, cloud clean checkout: Linux, branch `final_v1.2-cloud`) | `databridge/apps/mock_platforms/tests/`, `databridge/fixtures/`, `databridge/apps/internal_brain_web/` |
| Leak evaluation (re-run on the finals pipeline, 30 Oct 11:27 SGT; re-run again in the cloud on the evening of 30 Oct, unchanged) | 0 leaks in 7,374 requests; 0 of 400 pairs differ; 0 injection hard failures in 1,152; recall 100% | [RESULTS.md](databridge/evals/internal_brain/RESULTS.md), [eval_summary.json](databridge/apps/internal_brain_web/public/eval_summary.json) |
| Live QA of every page against the real backend | 32 checks; 11 bugs fixed; 4 open issues | [QA_LIVE.md](docs/finals/QA_LIVE.md) |
| Web end-to-end tests (Playwright, axe) | 185 passed, 0 failed, 42 skipped (the skipped specs need the live stack, the engine proxy or Hy3); mock mode, every spec `playwright.config.ts` runs, including `act`, `grow`, `deliverables` and `voice` (30 Oct 2026, evening, cloud clean checkout: Linux, branch `final_v1.2-cloud`; [TEST_REPORT](docs/finals/TEST_REPORT.md)) | `databridge/apps/internal_brain_web/tests/e2e/` |
| CI on GitHub (backend tests on Python 3.13 and web typecheck on Node 20) | Green: 17 successful GitHub Actions runs, 29–30 Oct, 0 failed (superseded pushes were cancelled, not failed); latest [run 40990413962](https://github.com/IshaanSirbhaiya/Tencent/actions/runs/40990413962). The workflow also runs on `final_v1.2-cloud`, `final_v1.3` and `final_v1.4` | `.github/workflows/ci.yml` |

## 3. Pending

| Item | Who | Note |
|---|---|---|
| **Hy3 live through TokenHub** | Ishaan (activation in the TokenHub console) | Wired and tested with a capturing test model; **live run pending**. Every model call returns HTTP 402 with code 401006. Until it runs, answers are quoted from the sources, with the same checks. Steps: [MORNING_BRIEF.md](docs/finals/MORNING_BRIEF.md) |
| **Hallway test** (5 people × 20 min) | Team, Sat 31 Oct | The only human-centered-design evidence we cannot produce ourselves. Protocol: [HALLWAY_TEST_PROTOCOL.md](docs/finals/rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md). The fix log is empty until then: [FIX_LOG.md](docs/finals/rubric/hcd_ux/FIX_LOG.md) |
| **Venue rehearsal** | Team, Mon 2 Nov 16:00–17:00 (mandatory) | Run sheet: [RUN_SHEET.md](docs/finals/rubric/demo_story/RUN_SHEET.md); checklist: [VENUE_TECH_CHECKLIST.md](docs/finals/rubric/demo_story/VENUE_TECH_CHECKLIST.md) |
| The new demo video (on hold; tooling committed, raw clips not) | Build team | Listed in [CHANGELOG.md → In progress](docs/finals/CHANGELOG.md#in-progress) |
| **WorkBuddy GUI retest** in the real app | Ishaan's laptop (needs his OK for screen control) | [WORKBUDDY_GUI_TEST.md](docs/finals/rubric/ai_innovation/WORKBUDDY_GUI_TEST.md); prompts 5–6 run from the compliance workspace or seat (D-30) |
| **Confirm cloud decisions D-26 to D-34** (D-34 is the upload folder picker) | Ishaan | [DECISIONS.md](docs/finals/DECISIONS.md) |
| Re-record the backup clips | Build team, after the Monday freeze | Entry numbers and timings can change |
| **Codex review** of the cloud session's work | Done in part (round 4, 2 runs by budget) | Items 5 to 7 of the scope were left to the other review seats. [round4_codex_cloud_work.md](docs/finals/reviews/round4_codex_cloud_work.md) |
| ~~Commit the round-4 fixes and run everything again~~ | Done 30 Oct evening | Committed, then a clean-checkout cloud run: backend 1,274 passed, e2e 185 passed, eval 0 / 7,374; counts updated in every doc |
| Build the "Demo story" app mode, then the rest of the teammate merge (voice, dots) | Build team, Sat 31 Oct to Sun 1 Nov | Stop rule Sun 1 Nov 18:00 for the app mode; merge order voice, then just-in-time UI, then dots |
| ~~`demo_day.ps1` run once~~ | Done 30 Oct evening (Ishaan's laptop) | Run for real on the laptop; a port bug was found and fixed; the `--fresh` reset passed ([FOUNDER_TODO.md](docs/finals/FOUNDER_TODO.md)) |
| Lighthouse server | Tencent (sold out) | Optional. The local run is the documented path |

## 4. At submission (16 Oct), for the record

639 Internal Brain tests and 38 web end-to-end tests passed; the leak evaluation gave the same headline
numbers; deck, video, cover and CodeBuddy evidence are in [submission/](submission/README.md).
