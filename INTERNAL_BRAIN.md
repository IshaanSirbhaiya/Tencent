# Walnut Internal Brain: judge's guide

**Case study: FinTech Track — Aspire: "The Internal Brain"** (Building a Context-Aware Enterprise
Knowledge System with RBAC, Security Logging & Audit Trail). Tencent Cloud AI CAN DO IT Hackathon
Singapore 2026.

## What it is

Walnut Internal Brain answers natural-language questions across Confluence, Jira, Slack and Google Drive using
**only the content the asker may open in each source system**. Before any model call, every candidate passage
passes two checks: a pre-rank filter on source permissions (Gate 1), then a live check against the source itself
(Gate 2), where any error, timeout or rate limit means deny. The answer goes through Walnut's grounding wall and
cites each source with its deep link, version and check times. If the model's answer fails grounding, a
deterministic extractive answer is served instead. Every request is written to a sealed, hash-chained ledger
**before** anything is released, and a compliance officer can query, verify and reconstruct it. The Brain runs on
the Walnut engine, reused by import, against a fully synthetic fintech called "Company A".

> **Status, 13 Oct 2026** (`file:line` references are to commit `4d1887e`). Built and tested: the API, both gates,
> four connectors over four mock platforms, freshness by webhook and poll, the write-ahead ledger with external
> head and tamper suite, the audit console, the web UI and a leak eval. **Not yet built:** the WorkBuddy MCP front
> door, the Lighthouse deployment, the live (real Slack and Confluence) track and a real-model (hy3) eval run.
> **In progress, uncommitted:** fintech features F1 (FinCrime compartment) and F4 (card masking). Anything not yet
> measured says **TBD (measured in RESULTS.md)**.

## Run it in 3 commands

From `databridge/`, after `py -3.13 -m venv .venv; .venv\Scripts\python -m pip install -r requirements-lock.txt`
(PowerShell; each command in its own shell):

```powershell
# 1. Mock Slack / Confluence / Jira / Drive on :8200 (webhooks delivered to the Brain)
$env:PYTHONPATH="apps/mock_platforms"; $env:MOCK_WEBHOOK_URL="http://127.0.0.1:8100"; .venv\Scripts\python -m mock_platforms

# 2. The Internal Brain API on :8100 (indexes the world on start; demo login on)
$env:PYTHONPATH="apps/internal_brain;packages/core;packages/harness;packages/adapter;packages/retrieval"; $env:IB_DEMO_LOGIN="1"; .venv\Scripts\python -m databridge_ib

# 3. The web UI on http://localhost:3100
cd apps/internal_brain_web; npm install; npm run dev
```

- Start the mock first: on start the Brain crawls all four platforms and indexes the whole world before it serves
  (about a minute on our dev box; not benchmarked). Ports 8100, 8200 and 3100 must be free.
- Without a TokenHub key every answer comes from the deterministic extractive answerer, so the demo works offline.
  With `TOKENHUB_API_KEY` set, the chain is `hy3` → `deepseek-v4-flash` → extractive
  (`databridge/apps/internal_brain/databridge_ib/llm.py:24-25, 55-81`).
- On a shared URL, also set `IB_DEMO_PASSCODE` so only people given the passcode can pick a persona.
- UI-only preview with no backend: `npm run dev:mock` (fixtures; `apps/internal_brain_web/package.json`).
- Tests: `.venv\Scripts\python -m pytest apps/internal_brain/tests apps/mock_platforms/tests fixtures/enterprise/tests -q`.
- Leak eval: `.venv\Scripts\python -m evals.internal_brain.run` (writes `evals/internal_brain/RESULTS.md`).
- Tamper demo: `.venv\Scripts\python scripts/tamper_demo.py`.

Personas (all synthetic): `jdoe` backend engineer, `priya.sre` on-call SRE, `ken.ext` contractor (Slack Connect
guest), `sara.sec` security, `leo.ops` product/ops, `maya.compliance` compliance officer, `admin.it` platform admin
(simulator only).

## The five scenarios: where to see them, and what proves them

Test paths are relative to `databridge/apps/internal_brain/tests/`. The e2e tests run the **real** mock platforms
app, the **real** connectors (with signed webhooks) and the pipeline over the generated world, and derive every
expectation from the world's fact manifest.

| # | Scenario | Where it is demonstrated | Tests that prove it |
|---|---|---|---|
| 1 | **Unified query** with citations; the private-channel blocker is omitted | UI → Ask, as `jdoe`: "What's the status of the database migration project and were there any blockers raised in Slack last week?" | `test_scenarios_e2e.py::test_canonical` (S1 questions: the denied fact is never considered and never in the answer; must-cite facts are released); `test_pipeline_core.py::test_s1_permitted_answer_cites_only_permitted` |
| 2 | **Freshness**: an edit is answerable within the SLO, citing the new version | UI → Admin simulator (as `admin.it`) "runbook update" (`/sim/runbook-update`), then Ask as `priya.sre`; `GET /ib/sources` shows `lag_s` and `within_slo` | `test_scenarios_e2e.py::test_s2_freshness_via_webhook`; `test_pipeline_core.py::test_drift_read_through`; connector crawl suites (e.g. `test_connector_slack.py::test_full_crawl_then_incremental`, `test_connector_jira.py::test_crawl_jql_uses_overlap_window`). Measured lag against the 5-minute mock SLO (`databridge_ib/config.py:16`): **TBD (measured in RESULTS.md)** |
| 3 | **Negative case**: the contractor gets the constant refusal; restricted looks exactly like nonexistent | UI → Ask as `ken.ext`: the Q3 breach report vs a report that never existed | `test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical`; `test_pipeline_core.py::test_s3_contractor_refusal_is_the_constant`, `::test_p2_differential_world_with_and_without_restricted` |
| 4 | **Live revocation**: removed from a private channel, or a page restricted ⇒ the next query excludes it, even if the webhook is lost | UI → Admin simulator "revoke channel" / "restrict page", then Ask again | `test_scenarios_e2e.py::test_s4_revocation_live` (both actions), `::test_s4_revocation_without_webhook`; `test_pipeline_core.py::test_s4_revocation_at_source_wins_over_stale_index`; `test_connector_slack.py::test_gate2_revocation_is_immediate` |
| 5 | **Audit inquiry**: "everything `jdoe` accessed related to `payment-gateway` in the last 30 days", with decisions, timestamps and a reasoned reveal | UI → Audit console as `maya.compliance` (natural-language box, events table, reveal drawer, verify panel) | `test_audit_nl_filter.py::test_s5_question`; `test_audit_query.py::test_s5_jdoe_payment_gateway_last_30_days`, `::test_s5_reconstruction`; `test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered`, `::test_events_query_is_ledgered`; tamper: `test_audit_tamper.py`, `databridge/scripts/tamper_demo.py` |

S5 note: the audit tests use a synthetic history written through the real ledger (`test_audit_support.py`).
Seeding the demo's 30-day history by replaying the world's `audit_replay` questions through the pipeline
(supported by `Pipeline.ask(..., replay=True)`, labelled `synthetic_replay`) has **no script yet**.

## What we claim, and the evidence

Claim wording is from the plan (`docs/tencent/INTERNAL_BRAIN_PLAN.md` §2). Paths are relative to
`databridge/apps/internal_brain/`. "RESULTS.md" is `databridge/evals/internal_brain/RESULTS.md`, first run,
generated 2026-10-13T13:39Z with a capturing fake model and the extractive path (no real model), before the fixes
in commits `b4d72cf` and `4d1887e`.

| ID | Claim | Evidence | Limits / not yet measured |
|---|---|---|---|
| **P1** | No passage the asker cannot access is placed in any LLM request made while answering their question. | `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt`; `tests/test_eval_smoke.py::test_no_leak_through_the_capturing_model_path`, `::test_eval_canary_detects_a_leaky_pipeline`; RESULTS.md: **0 leaks in 4,100 requests** (2,050 probes × 2 paths; 95% upper bound 0.146% per request per path), 0 payload leaks in 526 model calls; mechanism `databridge_ib/pipeline.py:113-161` | Real hy3 run: **TBD**. Re-run after `4d1887e`: **TBD (measured in RESULTS.md)**. Ingest embeddings are local today; a remote embedder would be a separate boundary (docs/tencent/DATA_FLOWS.md). |
| **P2** | Restricted content does not change the caller-visible response. | `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted`; `tests/test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical`; RESULTS.md: **400 differential comparisons, 0 mismatches**; mechanism: ranking statistics over the permitted corpus only (`databridge_ib/index.py:1-14, 300-321`) | Timing is excluded (TRADEOFFS R1). `/ib/sources` is global status (SECURITY.md §5). |
| **P3** | Access revoked in the source system is honoured on the next query. | The S4 tests above; uncached Gate 2 (`databridge_ib/gates.py:1-50`); per-connector `test_gate2_*` suites | Content delivered before a revocation cannot be retracted. Relies on the source's own permission API. |
| **P4** | Content created or updated is answerable within ≤ 5 min (mock, webhook) and ≤ 15 min (live, poll); each citation states its as-of. | `tests/test_scenarios_e2e.py::test_s2_freshness_via_webhook`; citations carry `version`, `updated_at`, `content_checked_at` and `authz_checked_at` (`databridge_ib/models.py:158-171`); poll fallback `databridge_ib/freshness/poller.py` (default 120 s, `databridge_ib/bootstrap.py:145`) | Measured lag: **TBD (measured in RESULTS.md)**. The live track is not built, so the 15-minute live figure is untested. |
| **P5** | Every answered, refused or failed request has a ledger record written before release. The chain detects edits, deletions, truncation and rollback by anyone who does not hold the sealing key. The chain head is published off-host. | `tests/test_pipeline_core.py::test_write_ahead_fail_closed`, `::test_ledger_events_order`; `tests/test_app_routes.py::test_ledger_failure_is_503`; `tests/test_audit_tamper.py` (edit, suffix delete, directory rollback, key-holder rewrite, external head tampered or missing); `tests/test_audit_ledger.py::test_missing_key_fails_closed`; failures append `request.failed` (`databridge_ib/pipeline.py:84-96`) | Off-host only when `IB_ANCHOR_FILE` (a separately pushed repo) or `IB_ANCHOR_SLACK_WEBHOOK` is configured; the default is a local file (`databridge_ib/bootstrap.py:85-91`). Unprotected window after the last publication (`test_rollback_after_last_publication_is_the_stated_window`). `request.failed` is best effort and has no end-to-end test yet. Append cost: **TBD**. |
| **P6** | A compliance officer can reconstruct who asked what, what was considered and released, the decisions, and (via a logged, reasoned reveal) what was answered. | `tests/test_audit_query.py::test_s5_reconstruction`, `::test_doc_filter_and_accessors`; `tests/test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered`, `::test_non_compliance_roles_forbidden` | One officer can reveal (no dual control). |
| **P7** | The answer text the user sees contains no number, date or named term absent from cited permitted evidence. **Relations between evidenced facts are not verified.** | Walnut's grounding wall, reused unchanged (`databridge_ib/grounding.py`, `databridge_ib/narrate.py:99-120`), with its existing tests (`databridge/apps/gateway/tests/test_llm_codex_f2.py`, `test_llm_codex_f1_r2.py`, `test_fin08_values_compared_by_value.py`); the extractive answer is built from released sentences (`databridge_ib/narrate.py:131-162`) | IB-specific relation, negation and date negatives (`test_grounding_negatives`): **not written yet**. A model answer citing at least one released passage is accepted (Codex security review r1 finding 15). The first eval run found 102 model-path answers without citations (RESULTS.md finding 2). hy3 grounding acceptance: **TBD**. |
| **P8** | Identity is never taken from a model or a tool argument. | `tests/test_contracts.py::test_no_identity_in_request_shapes`; `tests/test_app_routes.py::test_identity_from_token_only`, `::test_bad_and_expired_tokens`, `::test_every_non_public_route_requires_auth` | MCP tool-schema test: **TBD** (MCP server not built). Demo login lets a caller pick a persona when `IB_DEMO_LOGIN=1` (passcode-gated if `IB_DEMO_PASSCODE` is set). |

Other review evidence: the plan was reviewed by four models (`docs/tencent/PLAN_REVIEW_DISPOSITION.md`), and the
security-relevant code by Codex (`docs/tencent/reviews/security-r1/codex_track1.md`; SECURITY.md §7).

## Synthetic, mocked, or real?

| Component | What it is |
|---|---|
| Company A: its people, documents and customers | **Synthetic.** Deterministic generator `databridge/fixtures/enterprise/generate.py --seed 7`. At commit `4d1887e`: 27 users, 200 Confluence pages, 250 Jira issues, 600 Slack threads, 80 Drive files, 34 planted facts (`databridge/fixtures/enterprise/manifest.json`, `counts`). The F1/F4 work in progress adds to these. |
| Slack, Confluence, Jira, Drive | **Mocked**: real API paths and response shapes for a documented supported subset, native permission semantics, cursor paging, 429s and signed webhooks (`databridge/apps/mock_platforms/`). "A supported semantic subset, not a replica." |
| Connectors | **Real code**, on the same path a real tenant would use (base URL swap, `databridge_ib/bootstrap.py:39-51`). Real-tenant runs are **not done yet**. |
| Permission checks (Gate 2) | Real connector calls against the mock's permission endpoints, which follow the real APIs' shapes |
| Language model | **Real** TokenHub `hy3` / `deepseek-v4-flash` when a key is set; otherwise the deterministic extractive answerer. Tests and the first eval run use a capturing fake model and never call the network. |
| Embeddings | **Local offline hashing embedder**, which is not semantic; lexical BM25 carries ranking (`databridge_ib/index.py:43, 158-160`) |
| Ledger, tamper detection, audit | **Real** (Walnut `DecisionLedger`) on local disk; the external head sink is a local file unless configured |
| Identity | Signed HMAC tokens; a **demo login** instead of SSO |
| WorkBuddy, Lighthouse, live track | **Not built yet** |

## Pre-existing vs built during the hackathon (13–16 Oct 2026)

Base: Walnut commit **`4ab54d4`** (2026-10-11). Everything after it on branch `Tencent` was built during the
hackathon: 16 commits up to `4d1887e`, all dated 2026-10-13 at the time of writing (`git log 4ab54d4..HEAD`).

**The Walnut engine is reused by import, not modified.** `git diff --stat 4ab54d4 HEAD` shows no change to the
packages the Brain imports (`databridge/packages/core`, `harness`, `retrieval`, `adapter`) or to
`databridge/apps/gateway`. The only changes under `databridge/packages/` are in the healthcare intake package:
`packages/intake/databridge_intake/paths.py` (where the synthetic source CSVs live) and two of its tests, made when
the prior competition's folders were removed (commits `81a16a3`, `b4d72cf`). The Brain loads the gateway's
grounding functions by file path (`databridge_ib/grounding.py`), so no legacy route is imported.

| Path | Status | Commits after `4ab54d4` | Size |
|---|---|---|---|
| `databridge/apps/internal_brain/` (`databridge_ib` + tests) | **New** | 10, from `b9f58c5` to `4d1887e` | 53 files, ~10.8k lines added |
| `databridge/apps/mock_platforms/` | **New** | `b9f58c5`, `77f83a3` | 21 files, ~3.6k |
| `databridge/apps/internal_brain_web/` | **New** | `28e54c6`, `967f6c2` | 52 files, ~5.8k (including lockfile and screenshots) |
| `databridge/fixtures/enterprise/` | **New** | `d3f49e9`, `967f6c2` | 6 files (mostly the generated `world.json`) |
| `databridge/evals/internal_brain/` | **New** | `b4d72cf` | 9 files, ~3.7k (including `results.json`) |
| `databridge/packs/enterprise/ontology.yaml` | **New** | `d3f49e9` | 1 file |
| `databridge/scripts/tamper_demo.py` | **New** | `28e54c6` | 1 file |
| `databridge/requirements-lock.txt` | **New** | `f7ba478` | 1 file |
| `databridge/pyproject.toml` | Changed: `requires-python` `>=3.11` → `>=3.13` | `f7ba478` | 1 line |
| `docs/tencent/` | **New** | from `86ae46d` | plan, contracts, reviews, checklist, these docs |
| Root `CLAUDE.md`; `Hack4Health/`, `GOA Submission/`, `docs/hackathon/`, old video scripts | Rewritten / **removed** (prior competition's material); the healthcare CSVs moved to `databridge/fixtures/hackathon/source_data/` | `81a16a3` | — |
| `databridge/packages/intake/` | Path fix + 2 tests (see above) | `81a16a3`, `b4d72cf` | 3 files |
| All other `databridge/packages/**`, `apps/gateway`, `apps/frontend`, `apps/mcp_server`, `apps/cli`, `config/` | **Pre-existing, unchanged** | none | — |

## Documentation

| Doc | What it covers |
|---|---|
| [docs/tencent/ARCHITECTURE.md](docs/tencent/ARCHITECTURE.md) | Components, the 12-step request path and the ingestion path (Mermaid), trust boundaries |
| [docs/tencent/SECURITY.md](docs/tencent/SECURITY.md) | Threat model, identity, the ledger attacker model, prompt injection, side channels, secrets, security review |
| [docs/tencent/TRADEOFFS.md](docs/tencent/TRADEOFFS.md) | Design trade-offs and stated residual risks |
| [docs/tencent/MAS_TRM_MAPPING.md](docs/tencent/MAS_TRM_MAPPING.md) | MAS TRM, MAS AI Risk Management Guidelines, PDPA, PCI DSS and AML tipping-off: mechanism, evidence, limits |
| [docs/tencent/RESPONSIBLE_AI.md](docs/tencent/RESPONSIBLE_AI.md) | Grounding, abstention, citations, oversight, evaluation |
| [docs/tencent/DATA_FLOWS.md](docs/tencent/DATA_FLOWS.md) | What leaves the system, where state is kept, synthetic data |
| [databridge/evals/internal_brain/RESULTS.md](databridge/evals/internal_brain/RESULTS.md) | Leak and injection eval results |
| [docs/tencent/INTERNAL_BRAIN_PLAN.md](docs/tencent/INTERNAL_BRAIN_PLAN.md) | The build plan and claims P1–P8 |
| [docs/tencent/CONTRACTS.md](docs/tencent/CONTRACTS.md) | API, world and ledger contracts |
| [docs/tencent/PLAN_REVIEW_DISPOSITION.md](docs/tencent/PLAN_REVIEW_DISPOSITION.md) | The 4-model plan review and how each finding was handled |
| [README.md](README.md) | Walnut, the engine underneath |
