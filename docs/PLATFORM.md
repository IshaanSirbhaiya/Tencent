# The Walnut platform: what's in this repo

Walnut DataBridge is two things in one repository. **Internal Brain** (`databridge/apps/internal_brain`
and `databridge/apps/internal_brain_web`) is the ask product judged at the finals. Underneath it sits
**Walnut**, the data platform the team built before the hackathon: 21 Python packages under
`databridge/packages/` that connect sources (MCP servers, databases, files), ingest and screen what they
hold, build a typed, time-aware knowledge graph in which every value points back to its source, answer
questions across sources by delegating to per-source agents, convert and materialise data, and run
user-defined agents under compiled permissions. The original Walnut web app
(`databridge/apps/frontend`, served on port 3300 by `demo_up.sh`) shows these capabilities on screen.
The Internal Brain reuses some of them directly (section 4) and keeps its own code for the rest, for
reasons stated in that code. Everything below cites a file or a test; every count was measured on
branch `docs/platform-overview` (from `final_v1.2-cloud`, 30 Oct 2026).

Data in this repository is synthetic only.

## 1. Capability table

"Tests" is the number of `test_*.py` files in the package's `tests/` folder, then the result of
`python -m pytest packages/<name>/tests -q -m "not integration"` with the repo's Python 3.13 venv, run
package by package with a 2-minute cap. "Used by the Internal Brain?" means imported by
`databridge/apps/internal_brain/databridge_ib` (checked with `grep`, section 4): **yes** = on the answer
or audit path; **shown** = visible in the finals app or the original screens but not used to answer;
**no** = a platform capability that the Internal Brain does not use.

| # | Capability | Package | What it does | Where you can see it | Tests | Used by the Internal Brain? |
|---|---|---|---|---|---|---|
| 1 | Shared types, provenance, decision ledger | `core` | The dependency root: source pointers, grounded / withheld / absent cell states, value parsing, and a hash-chained, sealed decision ledger. No model or vendor imports. | Every screen that shows a pointer; the Internal Brain audit log (`/dev/audit`) | 13 files; 276 passed, 1 failed (note a) | **Yes**: the audit ledger (`audit/ledger.py:34`), tamper checks (`audit/tamper.py:46`), number checks in deliverables (`deliverables/verify.py:55`, `deliverables/build.py:11`) |
| 2 | Grounding wall | `apps/gateway/databridge_gateway/llm.py` (not a package) | Binds each sentence of a model answer to the cited context and drops what it cannot bind (`ground_narration` :1105, `bind_sentences` :1273, `unsupported_named_terms` :1260, `GroundedNarrator` :1332). | Every Internal Brain answer; the Journey view (`/dev/journey`) | Covered by the Internal Brain's `test_narrate*.py` and the gateway tests | **Yes**: loaded by file, without the legacy gateway's routes (`databridge_ib/grounding.py:1-37`), used by `narrate.py:14` and `deliverables/verify.py:58` |
| 3 | Model harness | `harness` | The only package allowed to import a vendor SDK; LiteLLM wrapper, airgapped-profile refusal, budgets, structured output, and an offline hashing embedder. | `/dev/analytics` (model fallback counts) | 7 files; 36 passed | **Yes**: the model chain (`databridge_ib/llm.py:81`) and the offline embedder for the index (`databridge_ib/index.py:160`) |
| 4 | Adapter contract and port | `adapter` | The no-I/O contract between an orchestrator and a source: `CapabilityRequest` (`contract.py:217`), `AdapterShell` (`shell.py:87`), the `PolicyEngine` (`policy.py:437`) and the `Adapter` protocol (`port.py:33`). | `/graph` ask panel, through the orchestrator (row 13) | 26 files; result pending | **No** (on the test path, `tests/conftest.py:13`, but not imported by `databridge_ib`) |
| 5 | MCP ingest ("connect any MCP server") | `mcp_ingest` | Declare a connection, propose and approve a crawl plan, capture raw bytes, extract, screen deterministically before any model (`screen.py`, e.g. Luhn check :118), build closed-vocabulary graph rows with pointers, reconcile and report (`plans.py:1363` `propose_plan`, `:1766` `approve_plan`, `runner.py:792` `run_ingest`). | `/connectors` (Sources) in the original app | 56 files; result pending | **No**. The Internal Brain has its own Confluence, Jira, Slack and Drive connectors (`databridge_ib/connectors/`) |
| 6 | Adapter agents | `adapter_agents` | A managed group of MCP connections: crawl plans, sync schedule, screening policy, freshness target, health, and proposals for ontology kinds it cannot store (`AdapterAgentManager`, `manager.py:165`; `sync` :667; `health` :1044). | `/builder` (adapter-agents panel, `components/agents/adapter-agents-panel.tsx`); `/trace` (Activity) | 23 files; result pending | **No** |
| 7 | Schema ingest | `ingest` | `SchemaExtract` models, a fixture loader (`fixtures.py:18`) and a lazy PostgreSQL schema reader (`postgres.py:110`). | No screen today (the `/schema` page was removed on 1 Oct, `apps/frontend/app/schema/page.tsx:1`) | 3 files; 10 passed | **No** |
| 8 | Semantic annotation | `semantic` | A deterministic schema graph (`schema_graph.py:107` `build_digraph`), a model annotator (`annotator.py:224`), confidence blending and a human confirmation session. | No screen today (same removal as row 7) | 6 files; 32 passed | **No** |
| 9 | Knowledge graph | `graph` | The typed, bi-temporal graph over SQLite (`GraphStore`, `store.py:172`; `as_of`, `query.py:76`), loaded from domain packs (`packs/*/ontology.yaml`) with every node and edge citing its source row (`estate_seed.py:1133` `load_pack_graph`). | `/dev/brain` (Walnut engine page, `jdoe` only) and, on port 3300, `/graph`, `/brain`, `/ontology`, `/evidence` (`apps/frontend/README.md`) | 45 files; result pending | **Shown**. The `/dev/brain` snapshot is built by `scripts/build_enterprise_graph.py` with `load_pack_graph`; it is not re-checked live, so it does not answer questions. The Internal Brain's own map, `/dev/graph`, is its own code (`databridge_ib/graph.py`) |
| 10 | Passage retrieval | `retrieval` | Hybrid, routed passage search over captured documents (`search.py:551` `search`, `:557` `deep_search`; `indexer.py:951` `index_capture`, `:183` `BuildSession`). | Used by agents (`agent_runtime/tools.py`), materialise and MCP ingest | 7 files; 75 passed | **No**. The Internal Brain keeps its own index so that ranking statistics come only from documents the asker may open (`databridge_ib/index.py:1-13`); it borrows the harness embedder (row 3) |
| 11 | Convert | `convert` | A conversion router (`router/router.py:29` `route`, `:84` `convert`) over readers and writers for CSV, Excel, JSON, JSONL, Parquet, Arrow, XML, YAML, HTML and Markdown tables, and document sources (PDF text and OCR, DOCX, PPTX, email, images), plus rule-based coercion, validators and quarantine. | `/data#convert` (`components/data/converter.tsx`) | 37 files; result pending | **Yes**: every uploaded or Drive file is read by a `databridge_convert` reader (`databridge_ib/extract_files.py:356-363`) |
| 12 | Materialise | `materialise` | A dataset is a plan: `MaterialisationPlan` (`plan.py:459`), a compiler that refuses leaky or unhashed plans (`compiler.py:553`), an executor (`executor.py:278`) and a data agent (`agent.py:945`) that write receipts with hashes. | `/data#agent` (`components/data/data-agent.tsx`, `design-builder.tsx`) | 28 files; result pending | **No** |
| 13 | Orchestrator (main agent over adapters) | `orchestrator` | Picks the adapters that can answer from their declared capabilities, briefs each `AdapterShell`, and lets each adapter's own policy decide what comes back (`Orchestrator.ask`, `orchestrator.py:238`). | `/graph` ask panel (`components/orchestrator/ask-panel.tsx`); gateway `POST /orchestrate/ask` | 1 file; 12 passed | **No** |
| 14 | Federated engine | `federated` | Splits a cross-source question into sub-tasks (rules first, then the harness, validated against a catalogue: `planner.py:711`), lets each adapter agent answer read-only (`delegates.py`), and combines the results with exact decimal arithmetic and no model arithmetic (`combine.py:412`; `FederatedService.answer`, `service.py:324`). | `/brain` ask panel (`components/brain/delegated-answer.tsx`); `/trace` delegation tree; gateway `POST /federated/ask` | 11 files; 138 passed, 4 skipped | **No** |
| 15 | Agent declarations and compiler | `agentdef` | Turns an agent declaration (pack YAML) into four artefacts, including a capability grant set, or refuses; an agent that will not compile cannot run (`compiler.py:278` `compile_agent`). | `/builder` | 6 files; 128 passed | **No** |
| 16 | Workflow spec | `workflow_spec` | The frozen contract for user-defined agents (`WorkflowSpec`, `models.py:489`) and adapter agents (`AdapterAgentSpec`, `models.py:636`). | `/builder` | 3 files; 129 passed | **No** |
| 17 | Agent runtime | `agent_runtime` | Runs a user-defined agent under its compiled grants with graph, retrieval and source tools; checks every claim cites a pointer; turns writes into proposals that a named human approves, rejects or reverses (`runtime.py:1139` `run_agent`, `:1938` `approve`). | `/builder` (run view), approval inbox (`components/agents/approval-inbox.tsx`) | 14 files; 272 passed, 6 skipped, 2 failed (note b) | **No** |
| 18 | Automation | `automation` | Plain English in, a governed automation out: rules first, then the model, then validation against the live catalogue (`compiler.py:630` `compile_english`); and a trigger engine with cron, event tailing and idempotency (`engine.py:369` `TriggerEngine`). | `/builder` (composer); gateway `/automation/*` | 15 files; 239 passed, 2 skipped | **No** |
| 19 | Outbox (send gate) | `outbox` | The agent drafts, a human approves, a separate model-free process sends; this package has no transport and no model, and a test checks both (`tests/test_outbox_cannot_send.py`; `OutboxStore`, `store.py:236`). | `/intelligence` action cards (`lib/outbox-api.ts`) | 8 files; 142 passed | **No** |
| 20 | Read-only clinic agent | `agent` | Walnut's earlier healthcare vertical: answers from sealed, read-only adapters with citations; it cannot send or decide who a patient is (`sealed.py`, `agent.py:297` `ClinicAgent`). | `/intelligence` (screening, `apps/gateway/databridge_gateway/intelligence_routes.py:95`) | 12 files; 324 passed | **No** |
| 21 | Intake | `intake` | Walnut's Hack4Health front-desk pipeline: classify, extract, ground every field to a source span, match registry candidates (never confirm), pre-fill, draft billing (`pipeline.py:60` `run_intake`). | `/intake`, `/frontdesk` | 26 files; result pending | **No** |
| 22 | Evals | `evals` | Schema-annotation (E1), SQL, intake and retrieval evaluations (`e1.py:55`, `e_retrieval.py:70`). | `/evals` | 6 files; 23 passed | **No**. The Internal Brain's leak and injection evaluation is separate (`databridge/evals/internal_brain/RESULTS.md`) |

Rows 2 and 20 to 22 are included so the table covers every package and the grounding code the Internal
Brain loads. Rows 20 and 21 are from Walnut's healthcare work before this hackathon.

Notes on the test runs (30 Oct 2026, Windows, run from a git worktree):

- **(a)** `core`: the one failure is `test_decision_ledger.py::test_the_append_lock_is_cross_process`,
  which starts a child Python that must import `databridge_core`. The venv does not install the
  packages, so the child cannot find it; with `PYTHONPATH=packages/core` the same test passes
  (1 passed).
- **(b)** `agent_runtime`: both failures are the two cases of
  `test_e2e_run2_rtf2.py::test_n06_a_derivation_that_does_not_replay_from_cited_inputs_refuses`
  (the run made a `write_file` call that the test expects to be refused). Not investigated here;
  reported as found.

## 2. The layers

```
 Sources        Confluence · Jira · Slack · Drive (mock servers, apps/mock_platforms)
                MCP servers · PostgreSQL · files (CSV, Excel, PDF, DOCX, PPTX, ...)
                   │
 Connectors     adapter (contract, policy) · mcp_ingest · adapter_agents · ingest
 and adapters   Internal Brain: its own connectors (databridge_ib/connectors/)
                   │
 Ingest and     mcp_ingest screen + extract · convert (readers, coercion, quarantine)
 convert        semantic (schema annotation) · materialise (plans, receipts)
                   │
 Knowledge      graph (typed, bi-temporal, every value with a pointer) · retrieval (passages)
                Internal Brain: its own permission-aware index and map
                   │
 Federation     orchestrator · federated (plan, delegate, combine) · agent_runtime
 and agents     agentdef · workflow_spec · automation · outbox (send gate)
                   │
 Grounding      core (pointers, cell states, decision ledger) · grounding wall (gateway/llm.py)
 and ledger     harness (the one door to any model)
                   │
 Apps           Internal Brain (ports 8100 / 3100) · original Walnut screens (8300 / 3300)
                gateway · cli · mcp_server · dispatcher
```

Two rules hold the layers together: `core` is the dependency root and has no model or vendor imports
(`packages/core/databridge_core/__init__.py:3-4`), and `harness` is the only package allowed to import
a vendor SDK (`packages/harness/databridge_harness/__init__.py:3-4`).

## 3. Where to see each capability

1. **Finals app** (`bash databridge/scripts/demo_up.sh start`, port 3100): every answer uses rows 1 to 3,
   and uploaded and Drive files are read with row 11. `/dev/brain` shows the Walnut graph (row 9) as `jdoe` sees it.
2. **Original Walnut screens** (port 3300, started by the same script; details in
   [`databridge/apps/frontend/README.md`](../databridge/apps/frontend/README.md)): `/graph`, `/brain`,
   `/ontology`, `/evidence` and `/demo` on Company A. The sidebar also lists Sources (`/connectors`),
   Builder, Activity (`/trace`), Intelligence and Data (`apps/frontend/components/sidebar.tsx:48-88`). On
   Company A nothing is ingested live, so Sources, Builder and Activity start empty.
3. **Gateway routes** for each capability are in `databridge/apps/gateway/databridge_gateway/`:
   `federated_routes.py`, `adapter_agents_routes.py`, `builder_routes.py`, `agents_routes.py`,
   `automation_routes.py`, `orchestrator_routes.py`, `materialise_routes.py`, `mcp_routes.py`,
   `graph_routes.py`.
4. **Tests**: from `databridge/`, `.venv/Scripts/python -m pytest packages/<name>/tests -m "not integration"`.
   Keep the marker; one test expects a local Ollama instance.

## 4. How the Internal Brain uses the platform

Measured with `grep -rn "databridge_[a-z_]*" databridge/apps/internal_brain/databridge_ib` (tests
excluded). The Internal Brain imports four parts of Walnut and no others:

1. **`core`, the decision ledger.** Every request is written ahead to a sealed, hash-chained ledger
   built on `databridge_core.decision_ledger.DecisionLedger` (`audit/ledger.py:34`); the tamper checks use
   its `digest_of` and `seal_of` (`audit/tamper.py:46`). Deliverables check every number against the
   sources with `databridge_core.value_forms` (`deliverables/verify.py:55`).
2. **The grounding wall.** `databridge_ib/grounding.py` loads Walnut's `GroundedNarrator`,
   `ground_narration` and `bind_sentences` from `apps/gateway/databridge_gateway/llm.py` without importing
   the legacy gateway's routes, and `narrate.py:14` uses it on every model answer.
3. **`harness`.** The TokenHub model chain (Hy3, then DeepSeek V4 Flash) runs through
   `databridge_harness.Harness` (`databridge_ib/llm.py:81`); the index uses the harness's offline
   embedder (`databridge_ib/index.py:160`).
4. **`convert`.** Uploaded files and Drive files are read by `databridge_convert` readers for CSV, XLSX,
   DOCX, PPTX, PDF, Markdown and text (`databridge_ib/extract_files.py:356-363`).

What it does **not** use, and why:

- **Retrieval and the graph.** The Internal Brain keeps its own index and its own permission-aware map,
  so that no statistic depends on a document the asker cannot open
  (`databridge_ib/index.py:1-13`, `databridge_ib/graph.py:1-25`). The Walnut graph appears read-only on
  `/dev/brain` as one person's snapshot (`databridge_ib/engine_refresh.py:1-25`).
- **Connectors.** It talks to Confluence, Jira, Slack and Drive through its own connectors
  (`databridge_ib/connectors/`), which re-check access live at answer time.
- **Adapter agents, the adapter builder, the federated engine, the orchestrator, agents, automation,
  materialise and the outbox** are platform capabilities shown in the original Walnut screens. They do
  not answer Internal Brain questions.
