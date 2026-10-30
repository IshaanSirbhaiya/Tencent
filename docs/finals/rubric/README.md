# Rubric evidence: one folder, one deliverable set per criterion

Walnut DataBridge, Tencent Cloud AI CAN DO IT Hackathon Singapore 2026, FinTech finalist. Demo Day is
3 Nov 2026 (5 min + 1 min Q&A). Each criterion is worth 10 points. For each one this page lists what a
judge can **see in the demo**, **open in the app**, and **read in this folder**.

Status key:
- ✅ **built / done**: written or built, tested and committed on `final_v1.0`.
- ⏳ **pending**: waits on a person or an account (the hallway test, the live Hy3 run, evidence capture).

Nothing in this folder claims more than a test, file or measurement proves. Estimates are labelled.
The leak, differential and injection numbers were re-run on the finals code on 30 Oct (`eval_summary.json`). Latency figures are from the 14 Oct run and were not re-measured.

Every feature named below, with its tests and screenshots: [../FEATURE_CATALOGUE.md](../FEATURE_CATALOGUE.md). What changed and when: [../CHANGELOG.md](../CHANGELOG.md).

| # | Criterion | Seen live in the 5 minutes | In the app | In this folder |
|---|---|---|---|---|
| 1 | **Impact & Relevance** | Opens on Aspire's own question: why is a customer's account restricted? Amy gets the approved script, Rachel the case. Tipping-off is a crime | ✅ `/value` ROI calculator | ✅ [impact_feasibility/IMPACT_SHEET.md](impact_feasibility/IMPACT_SHEET.md) · [ROI_MODEL.md](impact_feasibility/ROI_MODEL.md) |
| 2 | **Human-Centered Design** | Persona-first sign-in; plain-language answers with a source sheet; one line on what testers changed | ✅ persona picker, sources sheet, history, settings; ✅ User and Developer tours with "Do it for me" ([screens/tour/](../screens/tour/)) | ✅ [hcd_ux/PERSONAS_AND_JOURNEYS.md](hcd_ux/PERSONAS_AND_JOURNEYS.md) · ✅ [HALLWAY_TEST_PROTOCOL.md](hcd_ux/HALLWAY_TEST_PROTOCOL.md) · ⏳ usability report and [FIX_LOG.md](hcd_ux/FIX_LOG.md) (Saturday test) |
| 3 | **AI Interaction** | Cited answer inside two permission gates (composed by Tencent Hunyuan Hy3 only if the live run works; otherwise quoted from the sources, same checks); the same question asked live from WorkBuddy through our MCP connector | ✅ journey view (model, grounding, sealed records); ✅ live stage events; ✅ plain-English audit search (rules first, Hy3 parser as fallback) | ✅ [ai_innovation/AI_USAGE_MAP.md](ai_innovation/AI_USAGE_MAP.md) · ✅ Hy3 readiness: [`hy3_check.py`](../../../databridge/scripts/hy3_check.py) checks TokenHub, and `demo_up.sh` turns live Hy3 on by itself the moment it answers ([FEATURE_CATALOGUE T1–T2](../FEATURE_CATALOGUE.md#9-tooling-and-scripts)) · ⏳ the live Hy3 run (TokenHub activation; 401006 today) |
| 4 | **Technical Execution** | Request journey with real per-stage timings and three hash-chained records; 1,131 backend tests passing (30 Oct, commit 831297e) | ✅ `/dev` Mission control, `/dev/journey`, `/dev/graph` | ✅ [technical/ARCHITECTURE_ONE_PAGER.md](technical/ARCHITECTURE_ONE_PAGER.md) · [EVIDENCE_CARD.md](technical/EVIDENCE_CARD.md) · [COMPLETENESS_CHECKLIST.md](technical/COMPLETENESS_CHECKLIST.md) · ✅ [TEST_REPORT.md](../TEST_REPORT.md) (124 e2e passed, 0 failed) · ✅ [QA_LIVE.md](../QA_LIVE.md) · ✅ [FEATURE_CATALOGUE.md](../FEATURE_CATALOGUE.md) · CI ([.github/workflows/ci.yml](../../../.github/workflows/ci.yml)) |
| 5 | **Feasibility** | Business beat: break-even, cost per answer, Tencent Cloud as the target host (deploy kit, no live instance), pilot ask | ✅ `/value`; ✅ `/dev/analytics` (live numbers from the sealed log) | ✅ [impact_feasibility/SCALE_ARCHITECTURE.md](impact_feasibility/SCALE_ARCHITECTURE.md) · [PILOT_PLAN.md](impact_feasibility/PILOT_PLAN.md) · [RISK_REGISTER.md](impact_feasibility/RISK_REGISTER.md) · [COST_PER_ANSWER.md](impact_feasibility/COST_PER_ANSWER.md) · [PRICING_GTM.md](impact_feasibility/PRICING_GTM.md) · [CERTIFICATION_PATH.md](impact_feasibility/CERTIFICATION_PATH.md) |
| 6 | **Demo & Storytelling** | The whole run sheet: a story with two proofs, tagline three times, Presenter mode, no mouse | ✅ Presenter mode + Stage theme; ✅ `B` plays the step's clip, labelled "Recording · not live" ([screen](../screens/presenter/stage-clip-overlay.png)) | ✅ [demo_story/RUN_SHEET.md](demo_story/RUN_SHEET.md) · [PRESENTER_KEYS.md](demo_story/PRESENTER_KEYS.md) · [QA_BANK.md](demo_story/QA_BANK.md) · [BACKUP_PLAN.md](demo_story/BACKUP_PLAN.md) · ✅ backup clips A, B, C for the `B` key (interim, [status](demo_story/BACKUP_PLAN.md#recorded-clips-status-30-oct-2026); re-record after the freeze) · [OPENERS.md](demo_story/OPENERS.md) · checklists |
| 7 | **Innovation & Creativity** | Restricted looks exactly like non-existent, side by side; the live tamper on a copy breaks the chain at one entry | ✅ Compare; ✅ sandbox tamper on `/dev/audit` | ✅ [ai_innovation/INNOVATION_ONE_PAGER.md](ai_innovation/INNOVATION_ONE_PAGER.md) · [AGENTIC_ROADMAP.md](ai_innovation/AGENTIC_ROADMAP.md) |
| 8 | **UX & Accessibility** | Keyboard-only driving; a three-second 简体中文 flip | ✅ settings (text size, reduce motion, high contrast, theme, language); axe in every e2e test; ✅ voice dictation in the Ask composer (English / 简体中文, never auto-sends; `voice.spec.ts`) | ✅ [hcd_ux/ACCESSIBILITY_STATEMENT.md](hcd_ux/ACCESSIBILITY_STATEMENT.md) · ✅ fixes from the final UX review with screenshots ([screens/a11y/](../screens/a11y/), [reviews/final_sonnet_ux.md](../reviews/final_sonnet_ux.md)) · ⏳ screen-reader evidence capture |
| 9 | **Responsible AI & Ethics** | No tipping-off; identical refusals; write-ahead sealed log; reveal needs a reason | ✅ `/dev/trust` Trust centre | ✅ [SECURITY.md §15](../../tencent/SECURITY.md#15-developer-views-and-the-finals-v2-additions-30-oct) and [reviews/final_opus_security.md](../reviews/final_opus_security.md) (Host allowlist, held gate events, engine snapshot only for its own persona) · ✅ [responsible_ai/SYSTEM_CARD.md](responsible_ai/SYSTEM_CARD.md) · [OWASP_LLM_REDTEAM.md](responsible_ai/OWASP_LLM_REDTEAM.md) · [FRAMEWORK_CROSSWALK.md](responsible_ai/FRAMEWORK_CROSSWALK.md) (AI Verify, IMDA GenAI, MAS FEAT, MAS AI RMG, PDPA, ISO 42001, NIST) · [HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md](responsible_ai/HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md) · [PRIVACY_IMPACT_SUMMARY.md](responsible_ai/PRIVACY_IMPACT_SUMMARY.md) · [EXPLAINABILITY.md](responsible_ai/EXPLAINABILITY.md) · [FAIRNESS_NOTE.md](responsible_ai/FAIRNESS_NOTE.md) · [DATA_CARD_COMPANY_A.md](responsible_ai/DATA_CARD_COMPANY_A.md) |
| 10 | **Overall Quality & Judge's Impression** | One story, tight timing, honest numbers dated the morning of the demo, a clean close and ask | ✅ the whole app in both themes | ✅ [demo_story/LEAVE_BEHIND.md](demo_story/LEAVE_BEHIND.md) · [demo_story/VENUE_TECH_CHECKLIST.md](demo_story/VENUE_TECH_CHECKLIST.md) · [technical/DEMO_RUNBOOK.md](technical/DEMO_RUNBOOK.md) · ✅ [FEATURE_CATALOGUE.md](../FEATURE_CATALOGUE.md) · [CHANGELOG.md](../CHANGELOG.md) · ⏳ the new demo video ([in progress](../CHANGELOG.md#in-progress)) |

## The research behind it

| File | What it covers |
|---|---|
| [research/A_impact_feasibility.md](research/A_impact_feasibility.md) | Problem size, Aspire facts, Slack/Atlassian API terms, cost, pricing, certification |
| [research/B_hcd_ux_accessibility.md](research/B_hcd_ux_accessibility.md) | HCD frameworks, WCAG 2.2 AA, hallway test, SUS |
| [research/C_ai_interaction_innovation.md](research/C_ai_interaction_innovation.md) | Depth of AI use, Tencent capabilities, agentic additions, state-of-the-art comparison |
| [research/D_technical_responsible_ai.md](research/D_technical_responsible_ai.md) | Technical judging, Singapore and international responsible-AI frameworks |
| [research/E_demo_story_overall.md](research/E_demo_story_overall.md) | 5-minute structure, live-demo risk, judge psychology |
| [research/F_past_winners.md](research/F_past_winners.md) | Winners of Tencent and Singapore AI and fintech events, 2024–2026 |
| [research/G_competitor_edgelab.md](research/G_competitor_edgelab.md) | The other FinTech finalist: nothing public found |

The product plan and every decision are in [../PLAN.md](../PLAN.md) and [../DECISIONS.md](../DECISIONS.md).
