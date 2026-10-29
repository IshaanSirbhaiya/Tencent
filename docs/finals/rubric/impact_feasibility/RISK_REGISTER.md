# Risk register (14 risks)

If a judge asks "what breaks first?": **"Connector quotas, not the model."**

Likelihood and impact are our judgement. "Built" means code and a test exist; the test is named.
Tests are under `databridge/apps/internal_brain/tests/` unless shown otherwise.

| # | Risk | Likelihood / impact | Mitigation | Status and evidence |
|---|---|---|---|---|
| R1 | **Slack API terms bar indexing and LLM use of API data** ([Slack changelog](https://docs.slack.dev/changelog/2025/05/29/tos-updates)) | High / High | Federated Slack through the Real-time Search API as the user; deploy as Aspire's internal app; legal review | Design ready, not built. Gate 2 already works this way for the other sources ([SCALE_ARCHITECTURE.md](SCALE_ARCHITECTURE.md) §1) |
| R2 | **Connector quotas cap Gate 2 throughput** (Atlassian points, Slack per-method tiers) | Medium / High | Gate 2 on top-k only; bulk Jira check; per-source budgets and circuit breakers; webhook-driven ACL recompile | Partly designed. 429 already means deny: `test_mock_permission_check_needs_admin_and_429_denies` |
| R3 | Gate 2 adds latency | Medium / Medium | Parallel checks with one 2 s budget per batch that fails closed (`databridge_ib/gates.py`); measure p95 in shadow mode; any cache bounded, default 0 | Timeout built; p95 on real APIs not measured |
| R4 | Privileged service accounts (Confluence and Jira admin) | Medium / High | Secrets in KMS; scoped tokens; per-user OAuth where possible; alerting on service-account use | Gap documented against TRM 9.2.2 (`docs/tencent/MAS_TRM_MAPPING.md`) |
| R5 | Identity mismatch across platforms | Medium / High | SSO + SCIM; unmapped identity = deny; admin report | Deny path built: `test_gate2_inactive_or_unmapped*` (Confluence, Drive, Jira), `test_gate2_no_platform_identity_or_inactive` (Slack) |
| R6 | Ungrounded or invented answers | Medium / Medium | Grounding wall; extractive fallback; citations; refusal without a model call | Built: `test_llm_chain.py::test_ungrounded_number_refused_by_grounding_wall`, `::test_both_fail_pipeline_serves_extractive` |
| R7 | Prompt injection through documents | Medium / High | The answering model has no tools; passages are data; citation tags in sources are neutralised | Built: 0 failures in 976 injection-relevant requests (`RESULTS.md`); `test_pipeline_core.py::test_model_answer_that_is_only_injection_falls_back_to_extractive`. Re-test with live Hy3 |
| R8 | Model outage, price change or deprecation | Low / Medium | Chain Hy3 → DeepSeek → extractive; global kill switch `IB_FORCE_OFFLINE=1` | Built: `test_hy3_raises_falls_back_to_deepseek_flash`, `test_model_config.py::test_force_offline_wins_over_any_key`. Per-source kill switch is a gap |
| R9 | Cross-border transfer (PDPA s.26) and residency | Medium / Medium | Only Gate-2-released passages for one question reach the model; Singapore endpoint; self-hosted Hy3 option | TokenHub data terms not yet read by us [verify] |
| R10 | Never tested on a live tenant | Certain / High | Shadow mode in pilot weeks 2-4 re-runs the leak harness on real permissions before any user | Pilot step ([PILOT_PLAN.md](PILOT_PLAN.md)) |
| R11 | Unsupported content (PDF, slides, images skipped) | Certain / Medium | Clearly scoped; roadmap: text extraction under the same ACL rules | Documented; skipped types tested (`test_connector_drive.py::test_unsupported_mime_folder_missing_trashed_skip`) |
| R12 | Adoption: people keep old habits | Medium / High | Front doors where people already work (WorkBuddy now; Slack later); weekly pilot metrics | WorkBuddy built (`test_mcp_server.py`, `docs/tencent/workbuddy/README.md`) |
| R13 | A small team maintaining a security product | High / High | Narrow scope; 733 backend tests (30 Oct, commit 8582f4b) and the leak harness on every change; a design-partner contract; the certification path | Honest answer in Q&A |
| R14 | Ledger growth and retention | Low / Low | Retention policy per MAS record-keeping needs; external anchor; answer text kept outside the ledger | Partly built (`docs/tencent/DATA_FLOWS.md`; `test_audit_tamper.py`) |

Also stated in `INTERNAL_BRAIN.md` "Honest limits": the ledger has a key-holder window beyond the last
off-host published head (`docs/tencent/SECURITY.md` §3), and the grounding wall checks values and names, not
the relations between them.
