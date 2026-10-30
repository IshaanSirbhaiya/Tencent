# Status

**Date:** 30 Oct 2026. Branch `final_v1.2`. Team Walnut is a FinTech finalist; Demo Day is Tue 3 Nov 2026.
Product: **Walnut DataBridge**, with **Internal Brain** as the ask product inside it.
Start at [INTERNAL_BRAIN.md](INTERNAL_BRAIN.md). The finals plan is [docs/finals/PLAN.md](docs/finals/PLAN.md).
Every feature and its test: [FEATURE_CATALOGUE.md](docs/finals/FEATURE_CATALOGUE.md). Every change since
the submission, and what is in progress: [CHANGELOG.md](docs/finals/CHANGELOG.md). As of branch `final_v1.2` (30 Oct).

## 1. Built

| Area | State | Where |
|---|---|---|
| Internal Brain service: two permission gates, grounding, write-ahead sealed log, one response shape | Built (unchanged rules since submission) | `databridge/apps/internal_brain/` |
| Developer endpoints: `/ib/graph`, `/ib/graph/openable`, `/ib/doc/{id}`, `/ib/trace/{req}`, `/ib/status`, `/ib/ask/stream` (SSE stages), `/audit/stats`, `/audit/journey/{req}`, `/audit/tamper-demo` | Built | `databridge_ib/app.py`, `databridge_ib/audit/routes.py`; contract [backend_v1.md](docs/finals/contracts/backend_v1.md) |
| Seeded demo history: about 300 replay-labelled questions through the real pipeline, with 2 real Gate-2 denials | Built | `databridge/scripts/seed_demo_activity.py` |
| Web app, User mode: sign-in, Ask with live stage events, sources sheet, History, Settings (English / 简体中文, accessibility), Compare, Value | Built | `databridge/apps/internal_brain_web/` |
| Voice dictation in the Ask composer (browser speech, English / 简体中文, never auto-sends, hidden when unsupported) | Built (`1bc6b4c`) | `components/user/Composer.tsx`, `useDictation.ts`; `tests/e2e/voice.spec.ts` |
| Web app, Developer mode: Mission control, Journey, Knowledge graph, Walnut engine, Analytics, Audit with sandbox tamper demo, Simulator, Trust centre | Built | same |
| Presenter mode (11-step keyboard script, HUD 00 to 10; Stage theme, slides) and tours for both modes. The three live-growth steps are added only in a tab opened with `?grow=1` (14 steps), never on the 5:00 Space path | Built | `components/presenter/`, `components/tour/` |
| Backup clips A, B, C for the presenter `B` key | Interim recordings (`baf8a07`); re-record after the Monday freeze; C is an in-app stand-in for WorkBuddy | `databridge/apps/internal_brain_web/public/clips/`, [BACKUP_PLAN.md](docs/finals/rubric/demo_story/BACKUP_PLAN.md) |
| Security from the final reviews: Host allowlist on the Brain and the mock, proxy refuses foreign Host and cross-site GETs, SSE gate events held until sealed, engine snapshot readable only by its own persona | Built (`bc8414c`, `7bf3246`) | [SECURITY.md §15](docs/tencent/SECURITY.md#15-developer-views-and-the-finals-v2-additions-30-oct), [FEATURE_CATALOGUE §7](docs/finals/FEATURE_CATALOGUE.md#7-security-controls) |
| One-command launcher | Built | [`databridge/scripts/demo_up.sh`](databridge/scripts/demo_up.sh) |
| Hy3 readiness check; the launcher turns live Hy3 on by itself when TokenHub answers | Built (`8b69cbd`) | [`databridge/scripts/hy3_check.py`](databridge/scripts/hy3_check.py) |
| Rubric evidence pack, one deliverable set per criterion | Written | [docs/finals/rubric/README.md](docs/finals/rubric/README.md) |
| WorkBuddy connectors (ask as a person; compliance audit) | Built; new MCP tools added 30 Oct (answer receipt, what I can open, org summary, tamper demo; `9afd1bd`); live WorkBuddy test 33/33 | `docs/tencent/workbuddy/` |
| Act: draft a reply or a follow-up ticket, created only after the person confirms | Built and tested (`test_act.py` 21, `test_act_e2e.py` 6 passed; e2e `act.spec.ts` 8 tests); committed in `8312864` | `databridge_ib/actions.py`, `components/user/ActSheet.tsx`; [backend_v1.md](docs/finals/contracts/backend_v1.md) |
| Live graph growth and add-document (demo-only: `IB_DEMO_FEATURES=1` and the Settings switch) | Built and tested; committed in `8312864` (proxy route `1079bd2`) | [live_growth/STATUS.md](docs/finals/live_growth/STATUS.md) |
| One-click demo kit (`Start/Stop/Reset Walnut Demo.cmd`, `demo_day.ps1`, `demo_preflight.py`) | Built (`a1ccc6c`) | repo root, `databridge/scripts/` |
| CodeBuddy sessions CB7 to CB9 on the finals code | Done (`9afd1bd`) | [TENCENT_TOOLS_REPORT.md](docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md) |
| Live-ingestion kit | Written (`6c868bb`) | [LIVE_INGEST_KIT.md](docs/finals/live_kit/LIVE_INGEST_KIT.md) |
| Printable handouts | Built (`f0f84bd`) | `submission/finals/handouts_src/` |
| Video tooling (script, recorder, assembler) | Built (`215e31c`); the video is on hold | `submission/finals_video/` |
| Persona suggestion chips: starter questions verified against the live pipeline | Built (`ec502cd`) | `databridge/apps/internal_brain_web/lib/personas.ts` |

## 2. Tested

| Check | Result | Where |
|---|---|---|
| Backend tests (Internal Brain) | **754 passed**, commit 7bf3246, 30 Oct 2026, Windows, Python 3.13 | `databridge/apps/internal_brain/tests/` |
| Leak evaluation (re-run on the finals pipeline, 30 Oct 11:27 SGT) | 0 leaks in 7,374 requests; 0 of 400 pairs differ; 0 injection hard failures in 1,152 | [RESULTS.md](databridge/evals/internal_brain/RESULTS.md), [eval_summary.json](databridge/apps/internal_brain_web/public/eval_summary.json) |
| Live QA of every page against the real backend | 32 checks; 11 bugs fixed; 4 open issues | [QA_LIVE.md](docs/finals/QA_LIVE.md) |
| Web end-to-end tests (Playwright, axe) | 124 passed, 0 failed, 38 environment-gated skips (3 consecutive full runs, 30 Oct; [TEST_REPORT](docs/finals/TEST_REPORT.md)). `voice.spec.ts` (2 tests) was added afterwards and is not in the count; `act.spec.ts` (8), `grow.spec.ts` (6) and `deliverables*.spec.ts` (5) came after that run too and are not in the count; re-run pending | `databridge/apps/internal_brain_web/tests/e2e/` |

## 3. Pending

| Item | Who | Note |
|---|---|---|
| **Hy3 live through TokenHub** | Ishaan (activation in the TokenHub console) | Wired and tested with a capturing test model; **live run pending**. Every model call returns HTTP 402 with code 401006. Until it runs, answers are quoted from the sources, with the same checks. Steps: [MORNING_BRIEF.md](docs/finals/MORNING_BRIEF.md) |
| **Hallway test** (5 people × 20 min) | Team, Sat 31 Oct | The only human-centered-design evidence we cannot produce ourselves. Protocol: [HALLWAY_TEST_PROTOCOL.md](docs/finals/rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md). The fix log is empty until then: [FIX_LOG.md](docs/finals/rubric/hcd_ux/FIX_LOG.md) |
| **Venue rehearsal** | Team, Mon 2 Nov 16:00–17:00 (mandatory) | Run sheet: [RUN_SHEET.md](docs/finals/rubric/demo_story/RUN_SHEET.md); checklist: [VENUE_TECH_CHECKLIST.md](docs/finals/rubric/demo_story/VENUE_TECH_CHECKLIST.md) |
| In progress, not committed: the new demo video (on hold) | Build team | Listed in [CHANGELOG.md → In progress](docs/finals/CHANGELOG.md#in-progress) |
| Re-record the backup clips | Build team, after the Monday freeze | Entry numbers and timings can change |
| Lighthouse server | Tencent (sold out) | Optional. The local run is the documented path |

## 4. At submission (16 Oct), for the record

639 Internal Brain tests and 38 web end-to-end tests passed; the leak evaluation gave the same headline
numbers; deck, video, cover and CodeBuddy evidence are in [submission/](submission/README.md).
