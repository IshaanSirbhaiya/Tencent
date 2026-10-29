# Framework crosswalk

> **Status.** Self-assessed, 30 Oct 2026. "Met" means a mechanism **plus** a test or eval in this repo.
> "Partial" means a mechanism with a stated limit. "Gap" means not built. This page **maps** our controls to
> frameworks. It does not claim compliance, certification or alignment of a management system.
> Synthetic data only.
>
> **References.** `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`; `tests/…` =
> `databridge/apps/internal_brain/tests/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part. Eval numbers from
> [RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md) (14 Oct run); headline numbers re-run on the finals code, 30 Oct (`eval_summary.json`).
> Framework descriptions come from the sources in
> [research/D_technical_responsible_ai.md](../research/D_technical_responsible_ai.md) §2 and §9. Items that
> research marked **[verify]** are marked here too.

## 1. Our controls (the rows every framework maps to)

| Code | Control | Main evidence |
|---|---|---|
| C1 | **Gate 1**: rank only permitted documents; statistics over permitted docs only | `databridge_ib/index.py:1-12, 289-316`; `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted` |
| C2 | **Gate 2**: live source check, 2 s budget, fail closed | `databridge_ib/gates.py:28-54`; `tests/test_scenarios_e2e.py::test_s4_revocation_without_webhook` |
| C3 | **Identity from a signed token only**; no identity in bodies or tools | `databridge_ib/auth.py:34-63`; `tests/test_app_routes.py::test_identity_from_token_only`; `tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument` |
| C4 | **Write-ahead sealed ledger**; tamper evidence; external head | `databridge_ib/pipeline.py:106-110, 151-158, 184-197`; `tests/test_audit_tamper.py`; `tests/test_app_routes.py::test_ledger_failure_is_503` |
| C5 | **Grounding wall + extractive fallback** | `databridge_ib/narrate.py:129-197`; `tests/test_llm_chain.py::test_ungrounded_number_refused_by_grounding_wall` |
| C6 | **One response shape** (restricted ≡ non-existent) | `databridge_ib/models.py:18-21, 179-185`; P2 0 / 400 (re-run 30 Oct) |
| C7 | **Masking at ingest** (card, CVV, NRIC/FIN, IBAN) | `databridge_ib/ingest.py:64, 102`; `tests/test_sensitive.py`; `tests/test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index` |
| C8 | **No tools for the model**; injection containment | `databridge_ib/llm.py:91-92`; `databridge_ib/narrate.py:31-67`; injection 0 / 976 (re-run 30 Oct) |
| C9 | **Kill switch** `IB_FORCE_OFFLINE=1` | `databridge_ib/llm.py:78-79`; `tests/test_model_config.py::test_force_offline_wins_over_any_key` |
| C10 | **Reasoned reveal + role-segregated audit** | `databridge_ib/audit/routes.py:46, 84, 183-222`; `tests/test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered`, `::test_non_compliance_roles_forbidden` |
| C11 | **Leak and injection eval with canaries** | `databridge/evals/internal_brain/run.py`; `RESULTS.md:143-146`; `tests/test_eval_smoke.py::test_eval_canary_detects_a_leaky_pipeline` |
| C12 | **Citations with version and check times** | `databridge_ib/models.py:158-170`; `tests/test_pipeline_core.py::test_drift_read_through` |
| C13 | **Synthetic data only** | [DATA_CARD_COMPANY_A.md](DATA_CARD_COMPANY_A.md); `databridge/fixtures/enterprise/tests/test_generator.py` |

## 2. Matrix: control × framework

| Control | AI Verify principle | IMDA MGF-GenAI dimension | MAS FEAT | MAS AIRG | PDPA | ISO/IEC 42001 | NIST AI RMF | EU AI Act | OWASP LLM 2025 |
|---|---|---|---|---|---|---|---|---|---|
| C1 Gate 1 | Security; Data governance | Data; Security | Ethics | §5.4(d)-(e) | s.24 | A.7 (data) | Measure, Manage | — | LLM08, LLM02 |
| C2 Gate 2 | Security; Robustness | Security | Ethics | §5.4(e) | s.24 | — | Manage | — | LLM02 |
| C3 Token identity | Security; Accountability | Accountability | Accountability | §5.23(d) | s.24 | — | Govern | — | LLM06 |
| C4 Ledger | Accountability; Transparency | Accountability; Incident reporting | Accountability | §5.23(d) | s.24 | 9.1 (monitoring) | Govern, Manage | Art. 12 (logging; high-risk only) | — |
| C5 Grounding | Safety; Robustness | Trusted development | Transparency | §5.9(c) | — | — | Measure (confabulation) | — | LLM09 |
| C6 One shape | Safety; Security | Security | Ethics | — | s.24 | — | Manage | — | LLM02 |
| C7 Masking | Data governance | Data | Ethics | §5.4(d) | s.24 | A.7 | Manage (data privacy) | — | LLM02 |
| C8 No tools | Safety; Human agency | Security | Ethics | — | — | — | Manage | — | LLM01, LLM06 |
| C9 Kill switch | Safety; Human agency | Incident reporting | Accountability | §5.23(b) | — | — | Manage | — | — |
| C10 Reveal | Accountability; Human agency | Accountability | Accountability | §5.23(d) | s.24 | — | Govern | — | — |
| C11 Eval | Reproducibility; Robustness | Testing and assurance | Accountability | Testing (§5) | — | 9.1 | Measure | — | all |
| C12 Citations | Transparency; Explainability | Content provenance | Transparency | — | — | — | Measure | Art. 50 (in part) | LLM09 |
| C13 Synthetic | Data governance | Data | Fairness; Ethics | §5.4(d) | (not personal data) | A.7 | Map | — | — |

ISO/IEC 42001 clause and Annex A numbers above are indicative; we hold no AI management system (section 8).

## 3. AI Verify (IMDA / AI Verify Foundation), 11 principles

Full self-assessment: [AI_VERIFY_SELF_ASSESSMENT.md](AI_VERIFY_SELF_ASSESSMENT.md).

| Principle | Status | Evidence | Gap |
|---|---|---|---|
| Transparency | Partial | Answers name the model (`databridge_ib/models.py:173-176`); citations; [SYSTEM_CARD.md](SYSTEM_CARD.md); `INTERNAL_BRAIN.md:243-261` | No "AI-generated" label on the answer in the current UI |
| Explainability | Met | Per-answer citations (C12); `/ib/trace` own-request journey (`databridge_ib/app.py:176-203`; `tests/test_dev_views.py::test_trace_is_own_requests_only_and_hides_gate2_reasons`); [EXPLAINABILITY.md](EXPLAINABILITY.md) | The "why this answer" UI panel is in progress |
| Reproducibility | Met | Deterministic gates, ranking and extractive answer; world sha256 and seed 7 (`RESULTS.md:32`); `PROMPT_VERSION` ledgered | Model ids are aliases |
| Safety | Met | C5, C6, C8; refusal with no model call | Relations between facts not verified |
| Security | Met | `docs/tencent/SECURITY.md`; C1-C3; reviews in `docs/tencent/reviews/` | Track 1 disposition missing; no rate limiter; timing |
| Robustness | Partial | Fail closed on every error (`databridge_ib/gates.py:49-53`; `databridge_ib/app.py:147-151`) | No load test; no real-model run |
| Fairness | Partial | [FAIRNESS_NOTE.md](FAIRNESS_NOTE.md); per-persona utility (`RESULTS.md:118-141`) | English only; uneven recall for some roles |
| Data governance | Met | C7, C13; ledger holds ids and hashes, not answer text (`tests/test_audit_ledger.py::test_answer_text_never_in_ledger`) | No retention period |
| Accountability | Met | C4, C10 | No dual control on reveal |
| Human agency and oversight | Partial | C9, C10; data owners stay the authority | No review queue or feedback routing |
| Inclusive growth | Partial | axe checks at desktop and phone width (`databridge/apps/internal_brain_web/tests/e2e/a11y.spec.ts`) | Multilingual answers; assistive-technology testing |

## 4. IMDA Model AI Governance Framework for Generative AI (30 May 2024)

The wording of dimensions 7-9 differs between summaries **[verify]**.

| Dimension | Status | Evidence | Gap |
|---|---|---|---|
| 1 Accountability | Met | C4, C10; `answer.released` records model, prompt version, answer hash (`databridge_ib/pipeline.py:190-197`) | No written split of responsibility between deployer and model provider |
| 2 Data | Met | C1, C7, C13 | — |
| 3 Trusted development and deployment | Partial | [SYSTEM_CARD.md](SYSTEM_CARD.md); `databridge_ib/config.py:48-66` refuses dev keys when deployed | No live deployment |
| 4 Incident reporting | Partial | [HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md](HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md); C9 | Runbook not rehearsed; no alerting |
| 5 Testing and assurance | Met | C11; recorded AI reviews (`docs/tencent/SECURITY.md:208-393`) | No independent human assurance; no Moonshot run |
| 6 Security | Met | [OWASP_LLM_REDTEAM.md](OWASP_LLM_REDTEAM.md) | See residuals there |
| 7 Content provenance | Partial | Every sentence cites a versioned source; answer names its model | No label or watermark on generated text |
| 8 Safety and alignment R&D | n/a | — | — |
| 9 AI for public good | Partial | Anti-tipping-off (C6) protects customers and investigations | — |

**Agentic framework (IMDA, 22 Jan 2026), first dimension (bound autonomy and access).** Met for our
boundary: the model has no tools, and an agent calling us through WorkBuddy is limited to the human's own
permissions, checked live (C3, C8; `tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument`).
Gap: WorkBuddy's own agent sits outside our ledger (TB7).

## 5. MAS FEAT principles (Veritas)

Veritas fairness metrics are built for decisions about customers. We make no such decisions.

| Principle | Status | Evidence | Gap |
|---|---|---|---|
| Fairness | Partial | Equal service across roles, measured per persona; denial mirrors source policy ([FAIRNESS_NOTE.md](FAIRNESS_NOTE.md)) | No subgroup analysis by language or market |
| Ethics | Met | C6 (anti-tipping-off); C7; the system never acts | — |
| Accountability | Met | C4, C10 | Dual control |
| Transparency | Partial | Citations; model named; [SYSTEM_CARD.md](SYSTEM_CARD.md) | No end-user notice that questions are logged on the sign-in screen yet |

## 6. MAS Guidelines on AI Risk Management (issued 7 Oct 2026)

Sections 3-4 apply from 7 Oct 2027 and Sections 5-6 by 7 Oct 2028 (`docs/tencent/MAS_TRM_MAPPING.md:29`).
Paragraph mapping is in `docs/tencent/MAS_TRM_MAPPING.md:31-37`.

| Expectation | Status | Evidence | Gap |
|---|---|---|---|
| AI inventory (§4.7) | Partial | [SYSTEM_CARD.md](SYSTEM_CARD.md) §9 is a draft inventory record; model and prompt version on every answer | Not generated from the system; model ids are aliases |
| Risk materiality | Partial | [SYSTEM_CARD.md](SYSTEM_CARD.md) §8 | Self-assessed; no institution sign-off |
| Data and input/output security (§5.4(d)-(e)) | Met | C1, C2, C5, C7 | No classification labels |
| Human review escalation (§5.9(c)) | Partial | Pre-defined triggers fall back to the extractive answer (`databridge_ib/pipeline.py:171-179`) | Fallback, not a human review queue |
| Kill switch (§5.23(b)) | Partial | C9 | Global, needs a restart |
| Logging prompts, responses, model versions (§5.23(d)) | Partial | Question, released ids and versions, model id, prompt version, answer hash | Prompt not stored verbatim; config changes not ledgered |
| Third-party AI | Partial | TokenHub receives released passages only (`docs/tencent/DATA_FLOWS.md:18`) | Provider retention terms not verified |
| Testing | Met | C11 | No real-model run |

## 7. PDPA (Singapore)

Details: [PRIVACY_IMPACT_SUMMARY.md](PRIVACY_IMPACT_SUMMARY.md).

| Obligation | Status | Evidence | Gap |
|---|---|---|---|
| s.24 Protection | Met (supports) | C1-C4, C7 | Supports the obligation; does not discharge it |
| ss.18, 20 Purpose and notification | Partial | Content is used for its existing purpose, for people who can already open it | No staff notice yet that questions are logged |
| ss.21, 22 Access and correction | Gap | Staff see their own history (`databridge_ib/app.py:165-174`) | No correction route |
| s.25 Retention limitation | Gap | — | No retention period (`docs/tencent/DATA_FLOWS.md:36-38`) |
| s.26 Transfer limitation | Partial | Only released passages go to TokenHub | Provider terms and hosting not verified |

## 8. ISO/IEC 42001:2023

We have **no AI management system**, so we claim nothing against this standard as a whole.

| Clause | Status | What we have |
|---|---|---|
| 6.1.4 AI system impact assessment | Partial | [SYSTEM_CARD.md](SYSTEM_CARD.md) §8 plus [PRIVACY_IMPACT_SUMMARY.md](PRIVACY_IMPACT_SUMMARY.md) and [FAIRNESS_NOTE.md](FAIRNESS_NOTE.md): the inputs an AIMS would need |
| 8.4 Repeat after major change | Gap | Re-run triggers listed in SYSTEM_CARD §8; no process |
| 9.1 Monitoring and measurement | Partial | Eval (C11), `/audit/stats`, `/audit/verify` |
| Annex A Statement of Applicability | Gap | None |

## 9. NIST AI RMF 1.0 and the GenAI Profile (NIST AI 600-1)

| Function | Status | Evidence | Gap |
|---|---|---|---|
| Govern | Partial | Hard rules in `CLAUDE.md:41-59`; review dispositions in `docs/tencent/SECURITY.md` | No organisational policy or roles |
| Map | Partial | [SYSTEM_CARD.md](SYSTEM_CARD.md) scope and materiality; trust boundaries TB1-TB9 (`docs/tencent/ARCHITECTURE.md:191-206`) | — |
| Measure | Met | Data privacy, information security and information integrity measured by the eval (C11) | Confabulation rate on a real model not measured |
| Manage | Partial | Fail closed; kill switch; runbook | No alerting; runbook not rehearsed |

## 10. EU AI Act

| Question | Our reading | Evidence |
|---|---|---|
| Annex III high-risk? | **No, as scoped.** It does not score credit, price insurance or make employment decisions. | Scope statement, `docs/tencent/RESPONSIBLE_AI.md:14-15` |
| When could it become high-risk? | If wired into decisions about individuals (e.g. HR or credit). That use would need reassessment. | [SYSTEM_CARD.md](SYSTEM_CARD.md) §1 |
| Article 50 transparency | Partial: answers name the model; no explicit "AI-generated" label yet. The Article 50 date under the Digital Omnibus is **[verify]**. | `databridge_ib/models.py:173-185` |

We are not an EU deployment. This row exists because judges may ask.
