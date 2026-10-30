# Evidence card: every headline number, its proof, and how to reproduce it

> **Status.** 30 Oct 2026. Commands run from `databridge/` after the one-time setup in
> [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) §1. `PY` is `.venv\Scripts\python` (Windows) or `.venv/bin/python`
> (macOS/Linux). `tests/…` = `databridge/apps/internal_brain/tests/…`.
>
> **Eval numbers** are from [RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md), generated
> 14 Oct (`RESULTS.md:3`). The headline numbers were re-run on the finals code on 30 Oct and match `eval_summary.json`; latency is from the 14 Oct run. Machine-readable
> twin: `databridge/evals/internal_brain/results.json`.

## Set-up for the test commands

```bash
# PowerShell: $env:PYTHONPATH="apps/internal_brain;packages/core;packages/harness;packages/adapter;packages/retrieval"
export PYTHONPATH="apps/internal_brain:packages/core:packages/harness:packages/adapter:packages/retrieval"
```

(`CLAUDE.md:83`. The test tree's `conftest.py` also adds these paths itself:
`databridge/apps/internal_brain/tests/conftest.py`.)

## Headline numbers

| # | Claim | Number | Proof | Reproduce |
|---|---|---|---|---|
| 1 | Leaks in adversarial requests | **0 / 7,374** (3,687 probes × 2 paths) (re-run 30 Oct) | `RESULTS.md:9-13` | `PY -m evals.internal_brain.run` (1-10 min; exits 1 on any leak, `databridge/evals/internal_brain/run.py:237-240`) |
| 2 | 95% upper bound on per-request leak rate | **0.0814%** per path (rule of three, 3/N) (re-run 30 Oct) | `RESULTS.md:11` | same |
| 3 | Requests where the model received passages | **1,047**, payload leak bound 0.2865% (re-run 30 Oct) | `RESULTS.md:12` | same |
| 4 | Restricted vs non-existent (P2) | **0 mismatches / 400**; control rebuild 0 (re-run 30 Oct) | `RESULTS.md:16, 101-108` | same; unit: `PY -m pytest tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted` |
| 5 | Injection hard failures | **0 / 1,152** injection-relevant requests (re-run 30 Oct) | `RESULTS.md:17, 110-116` | same |
| 6 | Released recall on positive probes | **100%** both paths (re-run 30 Oct) | `RESULTS.md:18` | same |
| 7 | Cited recall | **97.1%** model path, **93.2%** extractive (re-run 30 Oct) | `RESULTS.md:131, 141` | same |
| 8 | Eval canaries valid | Leaky config caught **18 / 20**; deny-all **0%** utility (re-run 30 Oct) | `RESULTS.md:143-146` | same; unit: `PY -m pytest tests/test_eval_smoke.py::test_eval_canary_detects_a_leaky_pipeline` |
| 9 | Requests needing no model call | **2,640 / 3,687** on the model path (re-run 30 Oct) | `RESULTS.md:49` | same |
| 10 | Mean pipeline latency, model excluded | **19.8 ms** (model path), **19.0 ms** (extractive) (14 Oct run; not re-measured) | `RESULTS.md:49-50` | same |
| 11 | Backend tests (Internal Brain) | **1,274 passed**, 0 failed, 4 skipped, run 30 Oct evening in the cloud from a clean checkout (Linux, Python 3.13, `IB_FORCE_OFFLINE=1`), branch `final_v1.2-cloud` (639 at submission, `INTERNAL_BRAIN.md`) | `databridge/apps/internal_brain/tests/` | `PY -m pytest apps/internal_brain/tests -q` |
| 12 | Synthetic-world generator tests | **47 passed**, run 30 Oct in the cloud, commit 831297e | `databridge/fixtures/enterprise/tests/` | `PY -m pytest fixtures/enterprise/tests -q` |
| 13 | Web end-to-end tests (Playwright, incl. axe at desktop and phone width) | **185 passed**, 0 failed, 42 skipped (mock mode, every spec `playwright.config.ts` runs; skipped = need the live stack, the engine proxy or Hy3), 30 Oct evening, cloud clean checkout, branch `final_v1.2-cloud`; 38 at submission | [TEST_REPORT.md](../../TEST_REPORT.md); `databridge/apps/internal_brain_web/tests/e2e/` | `cd apps/internal_brain_web && npx playwright install chromium && npm run e2e` |
| 14 | Ledger tamper attacks detected | **6 of 6**; the 7th (rollback after the last publication) is the stated window | `docs/tencent/SECURITY.md:74-90`; `tests/test_audit_tamper.py` | `PY scripts/tamper_demo.py`; `PY -m pytest tests/test_audit_tamper.py` |
| 15 | Questions with a known expected outcome | **464** over **49** planted facts | `databridge/fixtures/enterprise/manifest.json:21-44` | `PY fixtures/enterprise/generate.py --seed 7 --now 2026-10-13T09:00:00+08:00 --scale demo` (byte-identical, `fixtures/enterprise/tests/test_generator.py::test_two_runs_are_byte_identical`) |
| 16 | What `jdoe` and `ken.ext` can open | **867** items on 4 platforms vs **24** on 2 | `INTERNAL_BRAIN.md:43`; `RESULTS.md:36` (867 and 24 docs indexed in the B-world shards) | Open **Map** as each persona |
| 17 | Answer citing no permitted passage | **0** in the final run (102 in the first run, fixed) | `docs/tencent/RESPONSIBLE_AI.md:37` | eval, as row 1 |

## Behaviour proofs (one test each)

| Behaviour | Test |
|---|---|
| Restricted content never in any prompt | `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt` |
| Identity only from the token | `tests/test_app_routes.py::test_identity_from_token_only` |
| Ledger down → 503 | `tests/test_app_routes.py::test_ledger_failure_is_503` |
| Write-ahead order | `tests/test_pipeline_core.py::test_ledger_events_order`, `::test_write_ahead_fail_closed` |
| Revocation honoured without the webhook | `tests/test_scenarios_e2e.py::test_s4_revocation_without_webhook` |
| Aspire scenario 1 (cross-platform, private channel omitted) | `tests/test_scenarios_e2e.py::test_canonical` |
| Aspire scenario 2 (freshness) | `tests/test_scenarios_e2e.py::test_s2_freshness_via_webhook` |
| Aspire scenario 3 (contractor, Q3 vs Q7) | `tests/test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical` |
| Aspire scenario 5 (30-day reconstruction) | `tests/test_audit_query.py::test_s5_reconstruction` |
| FinCrime anti-tipping-off | `tests/test_fintech_scenarios.py::test_fc_real_vs_nonexistent_customer_is_byte_identical` |
| Draft fee schedule never cited | `tests/test_fintech_scenarios.py::test_fee_draft_is_never_indexed_considered_or_released` |
| Card / NRIC / IBAN never in the index | `tests/test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index` |
| Model chain falls back | `tests/test_llm_chain.py::test_hy3_raises_falls_back_to_deepseek_flash`, `::test_both_fail_pipeline_serves_extractive` |
| Kill switch beats any key | `tests/test_model_config.py::test_force_offline_wins_over_any_key` |
| WorkBuddy tools take no identity | `tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument` |
| Own trace hides Gate 2 reasons | `tests/test_dev_views.py::test_trace_is_own_requests_only_and_hides_gate2_reasons` |
| Map identical with and without restricted docs | `tests/test_knowledge_map.py::test_map_is_identical_with_and_without_restricted_docs` |
| Deployed build refuses dev keys | `tests/test_config_deployable.py::test_deployed_dev_keys_refused` |

Run any one with `PY -m pytest "<path>::<name>" -q` (path under `apps/internal_brain/`).

## Reviews

| Review | Scope | Findings | Disposition | File |
|---|---|---|---|---|
| Codex track 1 | Gates, pipeline, ledger, identity | 35 | **Not written yet**; open by our reading: 3, 9, 11, 12, 15 | `docs/tencent/reviews/security-r1/codex_track1.md`; `docs/tencent/SECURITY.md:208-218` |
| Codex track 2 | Audit | 23 | Written | `docs/tencent/SECURITY.md:124-151` |
| Codex tracks 3a / 3b | Connectors, masking | 13 + 21 | Written | `docs/tencent/SECURITY.md:226-281` |
| Codex track 4 + CodeBuddy (Hy3) | MCP, tokens; app, auth, pipeline | 10 + 7 | Written | `docs/tencent/SECURITY.md:283-313`; `docs/tencent/reviews/codebuddy_security_review.md` |
| Codex track 5 | Map, audit content | 9 + 1 note | Written | `docs/tencent/SECURITY.md:316-331` |
| Codex track 6 | Web app | 10 | Written | `docs/tencent/SECURITY.md:353-369` |
| Codex track 7 + two further AI review rounds | Fix verification; security, accuracy, first run | — | Written | `docs/tencent/SECURITY.md:334-393` |

All reviewers are AI models or the team. No human third-party review.

## Not yet evidenced

| Gap | Status |
|---|---|
| Live Hy3 run | `RESULTS.md:30`: PENDING (no TokenHub key that works yet) |
| CI on GitHub | Workflow `.github/workflows/ci.yml` (backend tests on Python 3.13 + web typecheck): green: 17 successful runs on GitHub Actions, 29–30 Oct, 0 failed (backend tests + web typecheck); latest [run 40990413962](https://github.com/IshaanSirbhaiya/Tencent/actions/runs/40990413962) |
| Load test, large ledger | Not written (`docs/tencent/SECURITY.md:330`) |
