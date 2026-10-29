# Responsible AI evidence pack

Walnut DataBridge (Internal Brain) · Tencent Cloud AI CAN DO IT Hackathon Singapore 2026, FinTech Track ·
Demo Day 3 Nov 2026.

Every claim in these files links to a file and line, a test or an eval in this repo. Everything is
self-assessed by the team, on synthetic data. Eval numbers come from
[RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md) (run of 14 Oct). The headline numbers were re-run
on the finals code on 30 Oct and match `eval_summary.json`; latency and per-role figures are from the 14 Oct run.

## Index

| File | What it answers | Start here if a judge asks… |
|---|---|---|
| [SYSTEM_CARD.md](SYSTEM_CARD.md) | Purpose, users, model chain, data, controls, limits; a MAS AI inventory record and risk materiality | "What is this system, and how risky is it?" |
| [OWASP_LLM_REDTEAM.md](OWASP_LLM_REDTEAM.md) | Each OWASP LLM Top 10 (2025) risk: control, test, result, residual; what broke and how we fixed it | "Did you attack it?" |
| [FRAMEWORK_CROSSWALK.md](FRAMEWORK_CROSSWALK.md) | AI Verify, IMDA GenAI framework, MAS FEAT, MAS AI guidelines, PDPA, ISO/IEC 42001, NIST AI RMF, EU AI Act: met / partial / gap | "How does this map to MAS / AI Verify?" |
| [AI_VERIFY_SELF_ASSESSMENT.md](AI_VERIFY_SELF_ASSESSMENT.md) | The 11 AI Verify principles, one by one | "Have you run AI Verify?" |
| [DATA_CARD_COMPANY_A.md](DATA_CARD_COMPANY_A.md) | The synthetic world: 32 people, 1,143 documents, 49 planted facts; generator and seed | "Where did your data come from?" |
| [HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md](HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md) | Who oversees what; automatic escalation; five incident procedures | "What if something goes wrong?" |
| [PRIVACY_IMPACT_SUMMARY.md](PRIVACY_IMPACT_SUMMARY.md) | What personal data is held, where, who sees it, what leaves; PDPA table | "What goes to Tencent's model?" |
| [EXPLAINABILITY.md](EXPLAINABILITY.md) | What each audience can see about an answer, and why we never explain a refusal | "Can a user tell why they got this answer?" |
| [FAIRNESS_NOTE.md](FAIRNESS_NOTE.md) | What fairness means for a permission-aware assistant; per-role quality; inclusion gaps | "What about bias?" |

Background: [research/D_technical_responsible_ai.md](../research/D_technical_responsible_ai.md) (the spec
for these files) and the original docs in `docs/tencent/` (SECURITY, RESPONSIBLE_AI, MAS_TRM_MAPPING,
ARCHITECTURE, DATA_FLOWS). Technical evidence: [../technical/](../technical/README.md).

## Five facts to remember

1. **0 leaks in 5,958 adversarial requests** (95% upper bound 0.10% per request per path), with canaries that prove the test can fail. Re-run pending. (`RESULTS.md:9-19`)
2. **Restricted and non-existent look byte-identical:** 0 of 400 pairs differ. Re-run pending. (`RESULTS.md:16`)
3. **The model has no tools** and never chooses an identity. (`databridge/apps/internal_brain/databridge_ib/llm.py:91-92`)
4. **Every answer is logged before it is shown;** if the log fails, there is no answer. (`databridge/apps/internal_brain/tests/test_app_routes.py::test_ledger_failure_is_503`)
5. **Hy3 has not run live yet.** Every number comes from a capturing fake model or the extractive path.

## Claims we never make

| Never say | Say instead | Why |
|---|---|---|
| "Compliant with MAS / PDPA / AI Verify / ISO 42001" | "Mapped to", with the gap list | No regulator or auditor has assessed it; it runs on synthetic data |
| "Certified" or "audited" | "Self-assessed", "reviewed by AI models and the team" | Every review so far is by AI models (Codex, CodeBuddy, Claude) or by us; no human third party |
| "Zero risk", "unhackable", "leak-proof" | "0 in 5,958, with a 95% upper bound of 0.10% per request" | Absence of observed leaks is not proof of absence |
| "Runs on Hy3" / "Hy3 answers" | "Hy3 is wired in and tested with a capturing model; the live run waits on Tencent account verification" | No live Hy3 call has succeeded (`docs/finals/PLAN.md` §3.4) |
| "Tested on real Slack / Confluence / Jira / Drive" | "Mock platforms with the real APIs' shapes; same connector code, injected base URL" | No live tenant (`INTERNAL_BRAIN.md:245-246`) |
| "Bias-free" or "fair" | "Same rules for every role; quality per role published, uneven in places" | See [FAIRNESS_NOTE.md](FAIRNESS_NOTE.md) |
| "Explains every decision to the user" | "Explains every answer; never explains a refusal, by design" | Explaining a denial leaks existence ([EXPLAINABILITY.md](EXPLAINABILITY.md) §3) |
| "Human in the loop" | "Humans stay the authority on access; compliance can reconstruct and reveal; no review queue yet" | No review queue is built |
| "Production-ready" | "Prototype with a deploy kit; no live deployment" | `docs/tencent/ARCHITECTURE.md:42` |
| "The graph shows the whole company" | "The graph shows only what the signed-in person can open, checked live" | `/ib/graph` and `/ib/doc` build an induced graph of live-authorised documents only (`databridge_ib/graph.py`, `authz.py`; `tests/test_graph_doc.py`, incl. paired-world differential tests) |
| Current eval numbers without the caveat | "From the 14 Oct run" for headline numbers | They were re-run on 30 Oct; only latency and per-role figures are 14 Oct |
| Any Aspire customer count or licence status | — | Not verified (`../research/D_technical_responsible_ai.md:419`) |
