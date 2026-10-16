# databridge: the code

The engine and product code for Walnut. It is a Python workspace (`packages/*`, `apps/*`) plus a Next.js
web app. The Internal Brain is built on top of the engine without changing it.

- What the hackathon entry is, and how to judge it: [../INTERNAL_BRAIN.md](../INTERNAL_BRAIN.md)
- Where the build stands: [../STATUS.md](../STATUS.md)
- The whole system and every package: [../README.md](../README.md)

## What is where (Internal Brain)

| Path | What it is |
|---|---|
| `apps/internal_brain/` | The Internal Brain service (`databridge_ib`), port 8100: access checks, grounding, audit ledger, MCP servers |
| `apps/internal_brain_web/` | The Internal Brain web app (Next.js), port 3100: ask, map, compare, audit, simulator, tour |
| `apps/mock_platforms/` | Synthetic Slack, Confluence, Jira and Drive with real-shaped APIs, port 8200 |
| `apps/frontend/` | The original Walnut screens (graph, brain, evidence, ontology), running on Company A |
| `evals/internal_brain/` | The leak, injection and utility evaluation, with `RESULTS.md` |
| `fixtures/enterprise/` | The synthetic "Company A" world (`world.json` and its fact manifest) |
| `packages/core/` | Walnut core: `GroundedValue` and `SourcePointer` (provenance), shared types |
| `packages/harness/` | The only place that talks to model vendors |
| `packages/retrieval/` | Retrieval used by the Brain |
| `packages/adapter/` | Source adapters the connectors build on |

Other folders under `apps/` (`cli`, `dispatcher`, `gateway`, `mcp_server`) and `packages/` are the Walnut
engine. The Internal Brain uses them by import and does not modify them.

## Set up

Python 3.13 and the locked dependencies:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
```

## Run the tests

```powershell
# Internal Brain (634 tests)
.venv\Scripts\python -m pytest apps\internal_brain\tests -q

# Mock platforms
.venv\Scripts\python -m pytest apps\mock_platforms\tests -q

# Web app end-to-end (38 tests, Playwright)
cd apps\internal_brain_web; npm install; npm run e2e

# Any other engine folder, one folder at a time
.venv\Scripts\python -m pytest packages\core -m "not integration"
```

Run the legacy suites one directory at a time and always with `-m "not integration"`. Without the marker a
test that expects a local Ollama instance will hang.

The leak evaluation takes about 10 minutes and writes `RESULTS.md` and `results.json`:

```powershell
.venv\Scripts\python.exe -m evals.internal_brain.run
```

## Run the app

Three shells. Full steps are in [../INTERNAL_BRAIN.md](../INTERNAL_BRAIN.md).

```powershell
$env:PYTHONPATH="apps/mock_platforms"; .venv\Scripts\python -m mock_platforms --port 8200
$env:PYTHONPATH="apps/internal_brain;packages/core;packages/harness;packages/adapter;packages/retrieval"
$env:IB_DEMO_LOGIN="1"; .venv\Scripts\python -m databridge_ib
cd apps\internal_brain_web; npm run dev          # http://localhost:3100
```

## Invariants (enforced by tests)

1. No vendor SDK imports (`import anthropic`, `import openai`) outside `packages/harness`.
2. Every emitted value is a `GroundedValue` with a `SourcePointer`. It is refused at construction otherwise.
3. No per-domain branching in the engine (`test_no_domain_branching`). Closed vocabularies live in pack YAML.
4. Filter before the model: nothing the asker cannot open in the source system enters a model request.
5. Identity comes only from the signed token. No tool argument names a person.
6. Write-ahead audit: `request.accepted`, `retrieval.decided`, `answer.released`. A ledger failure returns 503.
7. One response shape. "Restricted" looks the same as "does not exist".
8. Synthetic data only.
