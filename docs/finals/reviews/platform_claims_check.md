# Platform claims check (README "The platform underneath" and "Built with Tencent")

30 Oct 2026, cloud Linux checkout of `final_v1.2-cloud` (from `cf66efe` plus the `docs/platform-deep`
draft), `/home/user/venv` (Python 3.13). Package commands ran from `databridge/`, one at a time:
`python -m pytest packages/<name>/tests -q -m "not integration" -p no:cacheprovider`. Internal Brain
files ran with `PYTHONPATH=apps/internal_brain:packages/core:packages/harness:packages/adapter:packages/retrieval`.
Explanations of every test that does not pass: [docs/PLATFORM.md §7](../../PLATFORM.md#7-notes-on-the-test-runs).

## 1. Capability table

| # | README figure (cf66efe) | Command | Result here | Change |
|---|---|---|---|---|
| 1 | Connectors: 4 files, 183 tests | `pytest apps/internal_brain/tests/test_connector_*.py` | 314 passed (183 test functions; some parametrised) | Now "4 files; 314 passed" |
| 2 | Adapter: 26 files | `pytest packages/adapter/tests` | before fix: 619 passed, 48 errors, 1 failed; after: 667 passed; 1 fails on Linux only (file order, note d) | Re-pinned the MCP fixture digest (CRLF pin, note d); result added |
| 3 | Adapter agents: 23 files | `pytest packages/adapter_agents/tests` | 198 passed (5 min 30 s) | Result added |
| 4 | MCP ingest: 56 files | `pytest packages/mcp_ingest/tests` | 854 passed, 7 skipped, 2 failed (15 min) | Both fail on Linux only (note f); one shows a real gap: `CODE_RUNNERS` does not match a versioned interpreter name such as `python3.13` (in packages/, not fixed). Result added |
| 5 | Ingest, semantic, intake: 35 files; 752 passed | three runs | 10 + 32 + 710 = 752 passed, 9 skipped | "9 skipped" added |
| 6 | Graph: 45 files | `pytest packages/graph/tests` | before fix: 907 passed, 1 failed, 3 skipped; after: 908 passed, 3 skipped | conftest fix (note a); result added |
| 7 | Knowledge map: 2 files, 33 tests | `pytest test_knowledge_map.py test_graph_doc.py` | 33 passed | Wording only |
| 8 | Federated: 11 files; 138 passed | `pytest packages/federated/tests` | 138 passed, 4 skipped | "4 skipped" added |
| 9 | Orchestrator group: 39 files; 780 passed, 2 failed | orchestrator, agentdef, workflow_spec, agent_runtime, automation | 12 + 128 + 129 + 272 + 239 = 780 passed, 8 skipped, 2 failed | The 2 fail only on the 30th of a month (note b; proven by fixing the clock to 29 and 30 Oct). Stated as such |
| 10 | Convert and materialise: 65 files; convert 519 passed | `pytest packages/convert/tests`, `packages/materialise/tests` | convert 519 passed, 18 skipped, 2 failed; materialise 371 passed | The 2 need `readability-lxml` (not in any requirements list; pass when installed in a scratch folder, 23 of 23). Stated; materialise added |
| 11 | Grounding wall: 5 files | gateway `test_fin08_*`, `test_llm_codex_f1_r2`, `test_llm_codex_f2`; IB `test_contracts`, `test_llm_chain` | 44 + 10 = 54 passed | "54 passed" added |
| 12 | Ledger: 13 + 11 files | `pytest packages/core/tests`; IB `test_audit_*.py`, `test_tamper_demo_route.py` | core before fix: 275 passed, 2 failed; after: 276 passed; 1 fails on Linux only (pydantic recursion depth, note e). IB audit: 10 files (one, `test_audit_support.py`, holds helpers only), 124 passed | "11 files" was not reproducible; now "10 files: 124 passed"; core result stated |
| 13 | Harness: 7 files; 36 passed | `pytest packages/harness/tests` | 36 passed, 1 deselected (integration) | None |
| 14 | Evals: 6 files; 23 passed | `pytest packages/evals/tests` | 23 passed, 2 deselected | None |
| 15 | 21 packages, 353 test files | `ls packages`; `find packages -name 'test_*.py'` | 21; 353 | None |
| 16 | Internal Brain suite 1,274 passed | not re-run (another long run had the CPU) | STATUS.md line 57 | Kept, cited to STATUS.md |

## 2. Built with Tencent

| Claim | Evidence checked | Result | Change |
|---|---|---|---|
| CodeBuddy: nine sessions, CB1 to CB9 | `docs/tencent/DEV_PROCESS.md` lines 47-55; `docs/tencent/evidence/codebuddy_cb1.jsonl` to `codebuddy_cb9.jsonl` | 9 sessions, 9 transcripts (CB8 also part 2, part 3) | None |
| "18 session images" | `ls submission/01_codebuddy/*.png` | 20 (CB1 to CB9 overview and full, plus CB8 part 3) | Now "20 session images" |
| Hy3 ran in CB1, CB5, CB7, CB9 | DEV_PROCESS.md model column | `hy3` on exactly those four | None |
| WorkBuddy 33 of 33; 40 MCP tests | `workbuddy_live_test.md` (33 PASS rows, 0 FAIL); `pytest test_mcp_server.py test_mcp_finals_tools.py` | 33/33 in the doc; 40 passed | None |
| Miora finals cover | `ls submission/06_cover/finals/` | session screenshot, two raw outputs, 1920x1080, 380x216, thumbnail, README | None |
| Two earlier Miora sessions | `submission/06_cover/miora_session1.png`, `miora_session2.png` | present | None |
| Lighthouse kit | `ls deploy/lighthouse/`; README line 3 | Dockerfiles, compose, Caddyfile, deploy/upload/smoke scripts; "no live instance" | None |
| Hunyuan/TokenHub wired, not live | `databridge_ib/llm.py:30` `DEFAULT_CHAIN`; `test_llm_chain.py` + `test_model_config.py` 9 passed | wired and tested with a capturing model; not live | None |

The stand-in model's provider was named in README.md (two lines) and INTERNAL_BRAIN.md (two lines);
both now say "an optional stand-in model", per the founder rule.

## 3. Fixes made (none under `databridge/packages/`)

1. `databridge/conftest.py`: export the pytest `pythonpath` entries in `PYTHONPATH` so a child Python
   can import the packages. Fixes `core::test_the_append_lock_is_cross_process` and
   `graph::test_concurrent_writer_processes_lose_no_records`.
2. `databridge/fixtures/packs/government/adapters/permits_registry_mcp.yaml`: re-pinned `mcp.digest` to
   the LF bytes the repository stores. The old pin equals the sha256 of the same file with CRLF endings,
   so the reviewed content is unchanged (`declaration_digest` untouched).

Not fixed (they live in `databridge/packages/**`, which this pass does not modify): the date clash in
`agent_runtime` (note b), the `glob` order in `adapter/tests/test_document_adapter.py` (note d), the
pydantic recursion depth in `core/tests/test_trust_lattice_cannot_be_laundered.py` (note e), and the
undeclared `readability-lxml` dependency of `convert` (note c).

Also found (in `databridge/packages/`, not fixed): `mcp_ingest`'s runner check does not treat a
versioned interpreter name (`python3.13`) as a code runner (PLATFORM.md note f).

After the `conftest.py` change, the other tests that set or pass `PYTHONPATH` to a child still pass:
`apps/dispatcher/tests/test_dispatcher_is_model_free.py` (17), `apps/gateway/tests/test_sources_batch_routes.py`
(42), `packages/outbox/tests/test_outbox_cannot_send.py` (13), and the Internal Brain's MCP, token, demo
script, WorkBuddy setup, narration and file-reader tests (79 passed, 4 skipped; 27 passed).
