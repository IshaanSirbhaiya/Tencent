# Completeness checklist: enterprise RAG, 22 capabilities

> **Status.** Updated 30 Oct 2026 from the research table
> ([D_technical_responsible_ai.md §1.3](../research/D_technical_responsible_ai.md)). **Done** = built and
> tested. **Partial** = built with a stated limit. **Missing** = not built.
> `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`; `tests/…` =
> `databridge/apps/internal_brain/tests/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part. Eval numbers from
> RESULTS.md (14 Oct run); headline numbers re-run on the finals code, 30 Oct.

**Score: 10 Done, 8 Partial, 4 Missing.** One change since the research: CI moved from Missing to Partial
(the workflow exists; it has not yet run green on GitHub).

| # | Capability | Status | Evidence | Gap / next step |
|---|---|---|---|---|
| 1 | Connectors | **Partial** | Four connectors, real code, injected base URL (`databridge_ib/connectors/`; `databridge_ib/config.py:36-38`) | Never run against a real tenant |
| 2 | Sync and freshness | **Done** | Signed webhooks (HMAC over ts.delivery.body, ±300 s, replay check); poller every 120 s with full reconciliation every 10th tick; `/ib/sources` `lag_s` / `within_slo` (`docs/tencent/ARCHITECTURE.md:137-159`); `tests/test_scenarios_e2e.py::test_s2_freshness_via_webhook` | Replay memory is per process (`docs/tencent/SECURITY.md:258`) |
| 3 | ACL ingestion | **Done** | Compiled permission expressions; anything unsupported is `nobody`; 30+ fail-closed shape tests (`docs/tencent/SECURITY.md:226-281`) | Real tenants need a `group_resolver` (`docs/tencent/SECURITY.md:256`) |
| 4 | Query-time authorisation | **Done** | Gate 2, one 2 s batch budget, deny on error/timeout/429 (`databridge_ib/gates.py:28-54`); `tests/test_scenarios_e2e.py::test_s4_revocation_without_webhook` | Correct only if the platform's check API is (JRACLOUD-84541) |
| 5 | Identity | **Partial** | HMAC token ≤ 8 h, `jti` revocation failing closed (`databridge_ib/auth.py:19-63, 87-134`); no identity in any body (`tests/test_contracts.py::test_no_identity_in_request_shapes`) | Demo login stands in for SSO; no IdP |
| 6 | Indexing and retrieval | **Partial** | BM25 over the permitted corpus only; local hashing embedder; relevance gate (`databridge_ib/index.py:1-12, 39-42, 159-168`) | Lexical: 7 expected refusals per path answered from related pages (`RESULTS.md:24`). No semantic embedder or reranker |
| 7 | Generation | **Partial** | Hy3 → deepseek-v4.1-flash → extractive (`databridge_ib/llm.py:29-30, 88-104`); `tests/test_llm_chain.py` | **Hy3 never called live** (TokenHub 402 / 401006 on 30 Oct, `docs/finals/PLAN.md` §3.4) |
| 8 | Grounding and citations | **Done** | Walnut grounding wall; versioned citations with `authz_checked_at` (`databridge_ib/models.py:158-170`); 0 uncited answers in the final run | Relations between facts not verified; one valid citation is enough (Codex track 1 #15) |
| 9 | Evaluation harness | **Done** | `PY -m evals.internal_brain.run`; exits 1 on any leak (`databridge/evals/internal_brain/run.py:237-240`); canaries valid (`RESULTS.md:143-146`) | No real-model run |
| 10 | Security review | **Done** (one gap) | Codex tracks 1-7, CodeBuddy on Hy3, two Claude rounds (`docs/tencent/SECURITY.md:124-393`) | Track 1 (35 findings) has no written disposition; 3, 9, 11, 12, 15 open |
| 11 | Audit and observability | **Partial** | Write-ahead sealed ledger, external head, `/audit/verify`. **New 30 Oct:** per-stage timings (`databridge_ib/pipeline.py:185-188`), `/audit/stats`, `/audit/journey`, `/ib/trace` (`tests/test_dev_views.py`) | No alerting or tracing system; no service-account monitoring (TRM 9.2.2); timing not controlled |
| 12 | Deployment | **Partial** | `deploy/lighthouse/` (Caddy, Docker Compose, `smoke.sh`); `assert_deployable` refuses dev keys (`databridge_ib/config.py:48-66`) | No live instance; demo runs on the laptop (`docs/finals/DECISIONS.md` D-03) |
| 13 | CI | **Partial** (was Missing) | `.github/workflows/ci.yml`: backend pytest on Python 3.13 and web typecheck on Node 20, on push and PR to `final_v1.0` and `Tencent` | Not yet run green on GitHub; no eval or Playwright job yet |
| 14 | Cost control | **Partial** | Refusals make no model call (2,640 of 3,687, `RESULTS.md:49`); ≤ 14 passages, 2 per doc (`databridge_ib/pipeline.py:34-35`); fallback chain | No per-user rate limit or budget; no token accounting |
| 15 | Fallbacks and kill switch | **Done** | Extractive fallback; `IB_FORCE_OFFLINE=1` (`databridge_ib/llm.py:78-79`); ledger failure → 503 | Kill switch is global and needs a restart |
| 16 | Sensitive data | **Done** | Card, CVV, NRIC/FIN, IBAN masked before index and model (`databridge_ib/ingest.py:64, 102`); `tests/test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index` | Other personal data not redacted |
| 17 | Accessibility | **Done** (automated) | axe on 5 screens at desktop and phone width; keyboard tests (`databridge/apps/internal_brain_web/tests/e2e/a11y.spec.ts`, `keyboard.spec.ts`) | No assistive-technology testing; new UI's tests are being ported |
| 18 | Multilingual | **Missing** | English only; SG and ID market *policies* | UI English / 简体中文 built (header toggle and Settings); answers stay English |
| 19 | Feedback loop | **Missing** | — | "Helpful / Wrong source / Out of date" buttons planned, ledgered "if time" (`docs/finals/PLAN.md` §3.1) |
| 20 | Retention and deletion | **Missing** | — (`docs/tencent/DATA_FLOWS.md:36-41`) | Roadmap: WORM storage, crypto-shredding of answer text |
| 21 | Load and scale | **Missing** | Mean 19-20 ms pipeline latency in the eval, model excluded (`RESULTS.md:49-50`) | Large-ledger and concurrency load tests not written (`docs/tencent/SECURITY.md:330`) |
| 22 | Second front door | **Done** | WorkBuddy MCP servers, no identity argument (`tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument`) | WorkBuddy's own model sits outside our ledger (TB7) |

## Built this week (not counted above)

| Item | Status |
|---|---|
| `/ib/graph` permission-aware subgraph | Built (`tests/test_graph_doc.py`) |
| `/ib/doc/{id}` document view | Built (`tests/test_graph_doc.py`) |
| Seeded, labelled demo history | Built (about 300 replay-labelled questions) |
| `eval_summary.json` and eval re-run | Built (re-run 30 Oct) |
| Model status (`GET /ib/status`, authenticated; `/health` stays minimal) | Done (B10, `databridge_ib/app.py` `/ib/status`) |

## Where judges will probe

The Partial rows: 1 (connectors), 5 (identity), 6 (retrieval), 7 (Hy3), 11 (observability), 12
(deployment), 13 (CI), 14 (cost). The honest answers are in
[../research/D_technical_responsible_ai.md](../research/D_technical_responsible_ai.md) §1.6.
