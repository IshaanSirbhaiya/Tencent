# databridge — code monorepo

The engine and product code for Walnut. `uv`-managed Python 3.11+ workspace (`packages/*`,
`apps/*`) plus a Next.js frontend under `apps/frontend`. This directory is no longer a scaffold —
see the root [`../README.md`](../README.md) for what the system is, the layout of every package,
and how to run it.

Read [`../CLAUDE.md`](../CLAUDE.md) before any session — it holds the hard constraints this code
must never violate — then [`../STATUS.md`](../STATUS.md) for where the build currently stands.

## Running the tests

```powershell
python -m pytest -m "not integration"
```

The marker is required: a plain run hangs on a test that expects a local Ollama instance.

## Running the app

```powershell
.\start-databridge.ps1        # frees ports 8000/3000, starts the gateway and the frontend
```

## Invariants (enforced, see `../CLAUDE.md` for the full list)
- No vendor SDK imports (`import anthropic` / `import openai`) outside `packages/harness`.
- Every emitted value is a `GroundedValue` carrying a `SourcePointer`
  (`packages/core/databridge_core/provenance.py`) — refused at construction otherwise.
- No per-domain branching in the engine — `packages/graph/tests/test_no_domain_branching.py`.
- Closed vocabularies live in pack YAML (`packs/`), not in engine code.
