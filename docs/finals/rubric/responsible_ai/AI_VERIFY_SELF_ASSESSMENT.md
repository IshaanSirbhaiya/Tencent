# AI Verify self-assessment

> **Self-assessed, not certified.** Written by the team on 30 Oct 2026. Not reviewed by IMDA, the AI Verify
> Foundation or any third party. Synthetic data only.
>
> **What this is and is not.** AI Verify assesses 11 principles through process checks (documentary
> evidence marked Yes / No / N/A) plus technical tests
> ([AI Verify Foundation](https://aiverifyfoundation.sg/what-is-ai-verify/)). Our research counted 106
> process checks across the 11 principles (`../research/D_technical_responsible_ai.md:135-141`) **[verify]**.
> **We have not yet filled in the official checklist item by item** (it needs the Process Checks Tool,
> which the founder must download). This page assesses each principle against the *outcome* AI Verify
> describes, and lists the evidence we would attach to the item-level checks.
>
> **References.** `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`; `tests/…` =
> `databridge/apps/internal_brain/tests/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part. Eval numbers from
> [RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md) (14 Oct run); headline numbers re-run on the finals code, 30 Oct (`eval_summary.json`).

## Summary

| # | Principle | Checks (per research, [verify]) | Our outcome | Evidence ready | Main gap |
|---|---|---|---|---|---|
| 1 | Transparency | 15 | Partial | Yes | No "AI-generated" label; no staff notice of logging |
| 2 | Explainability | 1 | Met | Yes | "How this was answered" link to the request journey (`/dev/journey`) |
| 3 | Reproducibility | 10 | Met | Yes | Model ids are aliases |
| 4 | Safety | 17 | Met | Yes | Relations between facts not verified |
| 5 | Security | 14 | Met | Yes | Track 1 disposition; rate limits; timing |
| 6 | Robustness | 10 | Partial | Partly | No load test; no real-model run |
| 7 | Fairness | 6 | Partial | Yes | English only; uneven recall for some roles |
| 8 | Data governance | 4 | Met | Yes | No retention period |
| 9 | Accountability | 14 | Met | Yes | No dual control on reveal |
| 10 | Human agency and oversight | 9 | Partial | Yes | No review queue or feedback loop |
| 11 | Inclusive growth | 6 | Partial | Partly | Multilingual; assistive-technology testing |

Six principles met, five partial, none with no mechanism at all.

## 1. Transparency

- **Outcome.** Partial.
- **Our process.** Every answer names the model that wrote it (`databridge_ib/models.py:173-176, 179-185`). Every sentence cites a source with version and check times (`databridge_ib/models.py:158-170`). Limits are published (`INTERNAL_BRAIN.md:243-261`). A system card exists ([SYSTEM_CARD.md](SYSTEM_CARD.md)).
- **Evidence to attach.** SYSTEM_CARD.md; `docs/tencent/RESPONSIBLE_AI.md`; `docs/tencent/DATA_FLOWS.md`.
- **Gap.** No explicit "AI-generated" label on answers. No sign-in notice that questions are logged ([PRIVACY_IMPACT_SUMMARY.md](PRIVACY_IMPACT_SUMMARY.md) §7 has the draft text).

## 2. Explainability

- **Outcome.** Met.
- **Our process.** Citations per sentence. Each asker can see how their own answer was made: stages, timings, model, grounding drops and the sealed record hashes, via `/ib/trace` (`databridge_ib/app.py:176-203`). Compliance sees the full decision trail, including Gate 2 verdicts, via `/audit/journey` (`databridge_ib/audit/routes.py:249-264`).
- **Evidence to attach.** [EXPLAINABILITY.md](EXPLAINABILITY.md); `tests/test_dev_views.py::test_trace_is_own_requests_only_and_hides_gate2_reasons` (line 93); `::test_journey_is_compliance_only_and_has_no_answer_text` (line 76).
- **Gap.** The user-facing "How this was answered" link opens the request journey (`/dev/journey`); a plain-language narration of it is not built.

## 3. Reproducibility

- **Outcome.** Met.
- **Our process.** Gates, ranking, time parsing and the extractive answer are deterministic. The world is generated from seed 7 and its sha256 is recorded (`RESULTS.md:32`; `databridge/fixtures/enterprise/tests/test_generator.py::test_two_runs_are_byte_identical`, line 158). `PROMPT_VERSION` and policy version are ledgered (`databridge_ib/pipeline.py:107-109, 190-191`). Python dependencies are locked (`databridge/requirements-lock.txt`).
- **Evidence to attach.** `RESULTS.md` configuration section; the eval command.
- **Gap.** Model ids are aliases, not pinned builds. TokenHub's seed handling is unverified.

## 4. Safety

- **Outcome.** Met.
- **Our process.** Constant refusal; no model call when nothing is relevant (`databridge_ib/pipeline.py:163`); grounding wall with extractive fallback (`databridge_ib/narrate.py:129-197`); no tools (`databridge_ib/llm.py:91-92`).
- **Evidence to attach.** `tests/test_llm_chain.py::test_ungrounded_number_refused_by_grounding_wall` (line 198); RESULTS.md: 0 uncited answers (re-run 30 Oct).
- **Gap.** Relations between facts are not verified; one valid citation is enough for a model answer.

## 5. Security

- **Outcome.** Met.
- **Our process.** Threat model and adversary table (`docs/tencent/SECURITY.md:27-42`). Codex tracks 1-7, CodeBuddy on Hy3, two Claude rounds, with dispositions for tracks 2-7 (`docs/tencent/SECURITY.md:124-393`).
- **Evidence to attach.** [OWASP_LLM_REDTEAM.md](OWASP_LLM_REDTEAM.md); RESULTS.md: 0 leaks / 5,958, 0 injection failures / 976 (re-run 30 Oct).
- **Gap.** Track 1 (35 findings) has no written disposition (`docs/tencent/SECURITY.md:208-218`). No rate limiter. Timing side channel not controlled.

## 6. Robustness

- **Outcome.** Partial.
- **Our process.** Fail closed on every error: Gate 2 timeouts and exceptions deny (`databridge_ib/gates.py:49-53`); paged connector loops refuse partial results (`docs/tencent/SECURITY.md:237-240`); any pipeline failure returns one 503 (`databridge_ib/app.py:147-151`).
- **Evidence to attach.** `tests/test_connector_slack.py::test_gate2_any_source_failure_denies_without_retry` (line 192); `tests/test_pipeline_core.py::test_write_ahead_fail_closed` (line 181).
- **Gap.** No load test or large-ledger test (`docs/tencent/SECURITY.md:330`). No real-model robustness run.

## 7. Fairness

- **Outcome.** Partial.
- **Our process.** The same gates for every role. Per-persona utility is published (`RESULTS.md:118-141`). See [FAIRNESS_NOTE.md](FAIRNESS_NOTE.md).
- **Gap.** English only. Cited recall is uneven for some roles (for example `mlro.tan` world must-cite 71.4% on the model path, `RESULTS.md:128`; per-role figure from the 14 Oct run, not re-checked).

## 8. Data governance

- **Outcome.** Met.
- **Our process.** Synthetic data ([DATA_CARD_COMPANY_A.md](DATA_CARD_COMPANY_A.md)). Masking at ingest (`databridge_ib/ingest.py:64, 102`). The ledger holds ids and hashes, not answer text (`tests/test_audit_ledger.py::test_answer_text_never_in_ledger`, line 103). Local embeddings (`databridge_ib/index.py:159-168`).
- **Gap.** No retention period (`docs/tencent/DATA_FLOWS.md:36-38`).

## 9. Accountability

- **Outcome.** Met.
- **Our process.** Write-ahead sealed ledger (`databridge_ib/pipeline.py:106-110, 151-158, 184-197`). Tamper detection: 6 of 6 attacks detected; the 7th is the stated window (`docs/tencent/SECURITY.md:74-90, 391`). Reasoned reveal, ledgered first (`databridge_ib/audit/routes.py:183-222`).
- **Evidence to attach.** `tests/test_audit_tamper.py`; `python scripts/tamper_demo.py`.
- **Gap.** One officer can reveal alone (no dual control).

## 10. Human agency and oversight

- **Outcome.** Partial.
- **Our process.** Source data owners stay the authority; revocation is honoured on the next question (`tests/test_scenarios_e2e.py::test_s4_revocation_without_webhook`, line 83). Compliance console and reveal. Kill switch (`databridge_ib/llm.py:78-79`). See [HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md](HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md).
- **Gap.** No human review queue for low-confidence answers; no feedback routing; kill switch needs a restart.

## 11. Inclusive growth, societal and environmental well-being

- **Outcome.** Partial.
- **Our process.** Aims to cut search time (Aspire's brief: about 30% of the week, `INTERNAL_BRAIN.md:53`). axe accessibility checks on five screens at desktop and phone width (`databridge/apps/internal_brain_web/tests/e2e/a11y.spec.ts`). Refusals make no model call, which saves compute (`RESULTS.md:49`).
- **Gap.** English answers only (Aspire works in SG, ID, TH, VN). No assistive-technology testing. The UI language switch (English / 简体中文) is built (header and Settings); answers stay English.

## Next steps

1. Download the AI Verify Process Checks Tool and mark each item Yes / No / N/A with the evidence above.
2. Run Project Moonshot against `/ib/ask` (optional, `../research/D_technical_responsible_ai.md` §4 R4b).
3. Done 30 Oct: the eval was re-run on the finals code and the headline numbers match `eval_summary.json`. Re-check per-role cited recall if it is quoted.
