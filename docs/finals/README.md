# docs/finals: index

> **Ishaan's to-do list:** [FOUNDER_TODO.md](FOUNDER_TODO.md)

Everything written for the finals of the Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 (FinTech
track). Product: **Walnut DataBridge**; "Internal Brain" is the ask product inside it. Demo Day is
3 Nov 2026. Branch `final_v1.3` (the verified lock; `final_v1.0` is frozen; `final_v1.4` adds the teammate's pages beside the chat and an optional stand-in model for the answers (off by default); it is not on the lock.); the laptop's 30 Oct evening work is on `final_v1.2` (the round-4 fixes are now committed on `final_v1.2-cloud`, locked as `final_v1.3`, and covered by the clean-checkout cloud test run on the evening of 30 Oct). Judges should start at [../../INTERNAL_BRAIN.md](../../INTERNAL_BRAIN.md).

| Start here | What it is |
|---|---|
| [FEATURE_CATALOGUE.md](FEATURE_CATALOGUE.md) | Every user-visible feature and backend capability: who can use it, where, how, the rules it keeps, its tests and screenshots |
| [CHANGELOG.md](CHANGELOG.md) | Every change since the 16 Oct submission, newest first, by area, with commit links; plus what is in progress |
| [../PLATFORM.md](../PLATFORM.md) | The Walnut platform in this repo: all 21 packages, what each does, its tests and screens, and which ones the Internal Brain uses to answer |

## 1. Plan and decisions

| File | What it is |
|---|---|
| [PLAN.md](PLAN.md) | The finals build plan: the two modes and every page (§3), backend items, design system, demo story, risks; §9 holds the v2 changes after five plan reviews |
| [DECISIONS.md](DECISIONS.md) | Decisions D-01 to D-34, each with its reason (D-26 to D-33 were taken in the 30 Oct cloud session, D-34 (the upload folder picker) on the laptop that evening and wait for Ishaan's confirmation) |
| [MERGE_PLAN.md](MERGE_PLAN.md) | The teammate's three feature branches (voice, just-in-time UI, dots) merged one at a time into new branches, with hard-rule checklists. Written 30 Oct evening. The first branch (`final_mehulupdates`, pages beside the chat) is merged on `final_v1.4` only; voice and dots have not arrived |
| [app_mode/PLAN_v2.md](app_mode/PLAN_v2.md) | The "Demo story" app mode (a separate, mouse-driven way through the 11 steps for the booth and hands-on time; the 5:00 stays on the keyboard presenter). **Plan only, not built.** v1 ([PLAN.md](app_mode/PLAN.md)) and four reviews ([review 1](app_mode/review_opus.md), [review 2](app_mode/review_sonnet.md), [claims review](app_mode/review_haiku.md), [judge-impact review](app_mode/review_fable.md)) sit beside it |
| [CLOUD_HANDOFF.md](CLOUD_HANDOFF.md) | The 30 Oct cloud session: questions for Ishaan, verified test results, the hand-off state of every track |
| [WORLD_EXPANSION_STATUS.md](WORLD_EXPANSION_STATUS.md) | The everyday-company world (append-only): how it was verified and the new counts |
| [MORNING_BRIEF.md](MORNING_BRIEF.md) | 30 Oct brief for the team: what only a person can do (Hy3 activation, hallway test), what was built overnight |
| [ORGANISER_UPDATE.md](ORGANISER_UPDATE.md) | The update email to the organisers: project link, description, what shipped since submission |
| [../../submission/finals/](../../submission/README.md) | Printable handouts: A4 leave-behind with QR, judge cards per criterion, presenter cheat-sheet (PDF, with their generator) |
| [COVER_MIORA_PROMPT.md](COVER_MIORA_PROMPT.md) | The prompt for the new finals cover in Miora (Tencent's AI design agent) |
| [demo_day/](demo_day/LIVE_DEMO_RESEARCH.md) | Live-demo research, the Demo Day runbook and the founder checklist for the one-click demo kit, and [STAGE_QA_30OCT.md](demo_day/STAGE_QA_30OCT.md) (the presenter path timed in headless Chrome, `B` at every step, the grow walk-up, preflight and the absence proof; 11 risks) |
| [live_growth/](live_growth/RESEARCH.md) | "Watch the brain grow" (live graph growth, add a document; demo-only): research, plan and status |
| [deliverables/](deliverables/STATUS.md) | Deliverables from an answer (Table, Chart, Analysis, Slides .pptx; Summary not built, D-29): plan, reviews, status |
| [live_kit/](live_kit/LIVE_INGEST_KIT.md) | The live-ingestion kit: samples, absence proof, templates |
| [video/](video/VIDEO_SCRIPT_FINALS.md) | *On hold.* The finals video script (the 16 Oct spine beat by beat, new features attached; cuts short, main and full: [VIDEO_PLAN.md](video/VIDEO_PLAN.md)), recorder and assembler (committed); [READINESS_30OCT.md](video/READINESS_30OCT.md) (scene-by-scene readiness) and [review_fable_script.md](video/review_fable_script.md) (storytelling review); raw clips in `submission/finals_video/` (not committed) |

## 2. Research

| File | What it covers |
|---|---|
| [research/01_palantir_databricks.md](research/01_palantir_databricks.md) | How Palantir and Databricks present a "company brain" |
| [research/02_user_mode_ux.md](research/02_user_mode_ux.md) | User mode UX patterns for an ask product |
| [research/03_dev_mode_graph_analytics.md](research/03_dev_mode_graph_analytics.md) | Developer mode: graph, trace and analytics |
| [research/04_current_state.md](research/04_current_state.md) | Inventory of the product before the rebuild |
| [research/05_competition_pitch.md](research/05_competition_pitch.md) | Competition and the 5-minute pitch |
| [rubric/research/](rubric/README.md#the-research-behind-it) | Seven studies, one per rubric area, plus past winners and the other finalist |

## 3. Reviews of the plan

Five independent AI review seats: [UX](reviews/plan_sonnet.md) · [claims](reviews/plan_haiku.md) · [security](reviews/plan_opus.md) ·
[judge impact](reviews/plan_fable.md) · [code](reviews/plan_codex.md). Each accepted finding became a decision in
DECISIONS.md and a change in PLAN.md §9. The judge-impact seat's run sheet (§3.2) became the demo story (D-14).

Later review rounds of the build are in [reviews/](reviews/): the final reviews (`final_*.md`), round 2 and 2b
(Act, tools, kit; [dispositions](reviews/round2_dispositions.md)) and round 3 (Deliverables, live growth,
uploads; [dispositions](reviews/round3_dispositions.md)) and round 4 (30 Oct evening; the cloud work, the web proxy
and demo kit, and a live UX re-check: [code review](reviews/round4_codex_cloud_work.md),
[web and kit review](reviews/round4_opus_web_kit.md), [live UX review](reviews/round4_sonnet_ux_live.md),
[dispositions](reviews/round4_dispositions.md); this pass's change list:
[docs_pass_30oct_evening.md](reviews/docs_pass_30oct_evening.md)).

## 4. Rubric evidence

[rubric/README.md](rubric/README.md) maps each of the 10 criteria to what is seen live, what to open in
the app, and what to read. Folders:

| Folder | Criteria |
|---|---|
| [rubric/impact_feasibility/](rubric/impact_feasibility/README.md) | 1 Impact & Relevance, 5 Feasibility |
| [rubric/hcd_ux/](rubric/hcd_ux/README.md) | 2 Human-Centered Design, 8 UX & Accessibility |
| [rubric/ai_innovation/](rubric/ai_innovation/README.md) | 3 AI Interaction, 7 Innovation & Creativity |
| [rubric/technical/](rubric/technical/README.md) | 4 Technical Execution |
| [rubric/responsible_ai/](rubric/responsible_ai/README.md) | 9 Responsible AI & Ethics |
| [rubric/demo_story/](rubric/demo_story/README.md) | 6 Demo & Storytelling, 10 Overall Quality |

Pending a person: the hallway test results ([FIX_LOG.md](rubric/hcd_ux/FIX_LOG.md) is empty until then)
and the live Hy3 run.

## 5. Contracts (what the build agents built against)

| File | What it fixes |
|---|---|
| [contracts/backend_v1.md](contracts/backend_v1.md) | Every new endpoint: graph, doc, status, trace receipt, stats, journey (v1); tamper on a copy, SSE stages, Hy3 audit search, real Gate-2 denials (v2) |
| [contracts/ui_foundation.md](contracts/ui_foundation.md) | Design tokens, primitives, the shell and mode switch, i18n, routes, API client and mock, file ownership |
| [contracts/AGENT_RULES.md](contracts/AGENT_RULES.md) | Rules every page agent followed |

## 6. QA and tests

| File | What it holds |
|---|---|
| [TEST_REPORT.md](TEST_REPORT.md) | The Playwright e2e suite on the finals UI: 185 passed, 0 failed, 42 skipped (need the live stack, the engine proxy or Hy3) in the clean-checkout cloud run on the evening of 30 Oct (branch `final_v1.2-cloud`); the same run's backend, mock platform and fixture counts; the earlier runs (166 passed at `831297e`, 124 passed on `final_v1.0`) kept as history |
| [QA_LIVE.md](QA_LIVE.md) | Every page × persona run against the live backend: 32 checks, 11 bugs fixed, 4 open issues (§4; two more fixed in 8582f4b) |
| [../../databridge/evals/internal_brain/RESULTS.md](../../databridge/evals/internal_brain/RESULTS.md) | The leak and injection evaluation, re-run 30 Oct on the finals pipeline |

## 7. Screenshots

| Folder | Files | What they show |
|---|---|---|
| [screens/live/](screens/live/) | 97 | Every page against the real backend (`prod-*` from the production build, `presenter-step-*` in the Stage theme). The best 15 are listed in [INTERNAL_BRAIN.md → Screenshots](../../INTERNAL_BRAIN.md#screenshots) |
| [screens/user/](screens/user/) | 46 | User mode: sign-in, Ask, waiting, answer, sources sheet, refusal, error, history, settings, 简体中文; dark and light, 1280 and 390 px |
| [screens/analytics/](screens/analytics/) | 36 | Mission control, Journey, Analytics, Value; compliance and employee, dark and light, 1280 and 390 px |
| [screens/audit/](screens/audit/) | 29 | Audit search, Verify, tamper demo, Simulator, Trust centre |
| [screens/engine/](screens/engine/) | 28 | Walnut engine: graph, time slider, provenance, ontology, the locked and proxy-off states |
| [screens/graph/](screens/graph/) | 12 | Knowledge graph: galaxy, focus panel, compare two people, mobile |
| [screens/presenter/](screens/presenter/) | 22 | Presenter mode, every step in the Stage theme, slides |
| [screens/tour/](screens/tour/) | 36 | Both tours, step by step, dark and light |
| [screens/a11y/](screens/a11y/) | 22 | Accessibility fixes: reflow at 130% text and 200% zoom, 简体中文 skip link and Developer-mode note, 24 px citation chips, journey seals, settings, slow-session message |

The presenter's backup clips (A, B, C) are videos, not screenshots: they live in
`databridge/apps/internal_brain_web/public/clips/` ([BACKUP_PLAN.md](rubric/demo_story/BACKUP_PLAN.md#recorded-clips-status-30-oct-2026)).
