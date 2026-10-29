# Morning brief for Ishaan (Fri 30 Oct)

Branch `final_v1.0`, pushed. All work is committed except what agents are writing right now.

## First: things only you can do

| # | What | Why | Time |
|---|---|---|---|
| 1 | **Activate Hy3 in TokenHub** (steps below) | Every model currently answers 402 (code 401006, "service ID does not exist"). Live Tencent AI is worth points on 3 criteria | 10 min |
| 2 | **Send the updated link and description to Cindy before 23:59 tonight** | Judges get the project link, description and video from the organisers. A draft is in [ORGANISER_UPDATE.md](ORGANISER_UPDATE.md), which I'll finalise this evening | 5 min |
| 3 | **Hallway test, Saturday**: 5 people × 20 min (team, friends) | The only Human-Centered Design evidence we can't produce ourselves. Protocol: [rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md](rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md) | 2 h |
| 4 | Ask Xin Yi about the extra 5,000 credits, and whether they work for TokenHub | Only if step 1 needs balance | 2 min |
| 5 | Decide who speaks and clicks on stage. The default is Ishaan speaking, Dev driving, Mehul as the compliance voice, Aaruni as the Tencent voice | [rubric/demo_story/RUN_SHEET.md](rubric/demo_story/RUN_SHEET.md) | — |

### Activating Hy3 on TokenHub (international console)

1. Open the Tencent Cloud console (international) → **TokenHub** (Model as a Service).
2. Find **Online Inference** / **Model services** (the error text says "Online Inference Service list").
3. **Activate** or create a service for **Hy3**, and also **DeepSeek V4 Flash** as the backup. Accept the pay-as-you-go terms if asked; a balance or voucher is needed.
4. If the console shows a **service ID** for the model, tell me. Our code calls the model id `hy3`; some accounts need the service id instead, and that is a one-line config change.
5. Tell me "activated", and I'll run a live test and record the first Hy3 answer immediately.

The key in `databridge/.env.ib.local` already authenticates (the model list call works). Only activation is missing.

## What was done overnight

| Area | Status |
|---|---|
| Research | 5 product studies (Palantir and Databricks, user-mode AI UX, developer-mode graph and analytics, current app inventory, competition and pitch) + 7 rubric studies (one per criterion pair, past winners, competitor) |
| Plan | Written, reviewed by **Sonnet, Haiku, Opus, Fable and Codex**, then v2 with every finding resolved ([PLAN.md](PLAN.md), [DECISIONS.md](DECISIONS.md) D-01…D-23) |
| Rubric folder | [rubric/README.md](rubric/README.md): every one of the 10 criteria has deliverables. Impact sheet, ROI model and calculator spec, cost per answer, scale architecture, pilot plan, risk register, pricing, certification path; personas and journeys, hallway protocol, accessibility statement; AI usage map, innovation one-pager; architecture one-pager, evidence card, completeness checklist, CI; system card, OWASP LLM red team, framework crosswalk (AI Verify, IMDA, MAS FEAT, MAS AI RMG, PDPA, ISO 42001, NIST), incident runbook, privacy, explainability, fairness, data card; run sheet, presenter keys, 25-question Q&A bank, backup plan, checklists, leave-behind |
| Backend | Developer-mode endpoints: stats, journey, a safe trace receipt, permission-aware graph and document view, model status; version-bound access helper; replay seeder (302 seeded questions); **680 tests pass**; full eval re-run on the new code: **0 leaks / 5,958, 0 / 400** |
| Web foundation | New "Walnut DataBridge" design system and shell with the **User / Developer mode switch**, command palette, English/简体中文, accessibility settings; 38/38 old e2e tests still pass |
| Building now | User mode (Ask, answers, sources), Compare + Presenter mode + slides, Knowledge graph (WebGL), Mission control + Journey + Analytics + ROI calculator, Audit + Simulator + Trust centre, the Walnut engine page, backend round 2 (sandbox tamper demo, live stage streaming, Hy3 for audit search, fresh eval) |

## Key decisions taken for you (change any)

- **Demo story:** opens live on Aspire's own question, why is Velquarry's account restricted, with Amy and Rachel side by side. Then Ken: a real company vs a made-up one, identical. Then a source checked live, the journey, a tamper on a copy, Tencent live through WorkBuddy, proof, business, close. Tagline: **"Answers you can prove."**
- **Cut line:** Sat 22:00. After that, only polish and tests.
- **Safety:** the new developer views never show anyone hidden counts or denial reasons; organisation-wide numbers are compliance-only.
