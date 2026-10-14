# Connecting WorkBuddy to the Internal Brain

WorkBuddy (desktop app 5.7.x) can talk to the Internal Brain through its stdio MCP
server. This guide shows how to wire one WorkBuddy project per **persona** — each
project gets its own short-lived bearer token so the Internal Brain always knows
who is asking, without the model or any tool argument ever carrying an identity
field (P8).

## How it works

```
WorkBuddy project  ──stdio──>  databridge_ib.mcp_server  ──HTTP──>  Internal Brain
        (per-persona)               IB_TOKEN = <minted token>           (127.0.0.1:8100)
```

- **Identity is the token, never an argument.** The Internal Brain MCP server
  reads `IB_TOKEN` from its environment and sends it as a `Bearer` header on
  every HTTP call. No tool argument, prompt, or model output carries a user /
  persona / sub / principal field.
- **One token per persona.** Tokens are short-lived (max 8 h) HMAC-signed
  principals. You mint one with `ib_mint_token.py` and paste it into the
  project-level `.workbuddy/mcp.json`.
- **The token is never logged.** The MCP server and the Internal Brain never
  print the token; the CLI prints only the token string to stdout.

## Prerequisites

1. **Internal Brain running** on `http://127.0.0.1:8100` (the default). If you
   need a different URL, set `IB_URL` in the env block below.

2. **The venv** at `databridge/.venv/`. The MCP server runs as a module inside
   that venv so all dependencies are available.

3. **A minted token** for the persona. From the `databridge/` folder:

   ```bash
   .venv/Scripts/python.exe scripts/ib_mint_token.py jdoe --hours 8
   ```

   This prints only the token. Copy it — you will not be able to see it again.

## Project setup (per persona)

For each WorkBuddy project that should act as a given persona, create a
**project-level** MCP config at `.workbuddy/mcp.json` inside the project root:

```json
{
  "mcpServers": {
    "internal-brain": {
      "command": "<absolute path to databridge>/.venv/Scripts/python.exe",
      "args": ["-m", "databridge_ib.mcp_server"],
      "cwd": "<absolute path to databridge>",
      "env": {
        "PYTHONPATH": "apps/internal_brain",
        "IB_URL": "http://127.0.0.1:8100",
        "IB_TOKEN": "<paste token from ib_mint_token.py>"
      }
    }
  }
}
```

Key points:

| Field | Value | Why |
|---|---|---|
| `command` | `.venv/Scripts/python.exe` (absolute) | The venv has all deps (httpx, mcp, etc.) |
| `args` | `["-m", "databridge_ib.mcp_server"]` | Runs the stdio MCP server as a module |
| `cwd` | `databridge/` (absolute) | So `databridge_ib` resolves on `sys.path` via `PYTHONPATH` |
| `PYTHONPATH` | `apps/internal_brain` | Puts the IB package on `sys.path` (no editable install needed) |
| `IB_URL` | `http://127.0.0.1:8100` | Where the Internal Brain HTTP API is listening |
| `IB_TOKEN` | token from `ib_mint_token.py` | The persona's short-lived bearer token |

> **Compliance officer persona:** If the persona has the `compliance_officer`
> role (e.g. `maya.compliance`), use `databridge_ib.audit_mcp_server` in
> `args` instead. That server exposes the audit tools
> (`audit_events`, `audit_reveal`) in addition to `ask_internal_brain`.

## Minting a token

From the `databridge/` folder:

```bash
# Default 8 h lifetime (the maximum):
.venv/Scripts/python.exe scripts/ib_mint_token.py jdoe

# Shorter, 2 h:
.venv/Scripts/python.exe scripts/ib_mint_token.py jdoe --hours 2
```

- The script reads `IB_TOKEN_KEY` from `databridge/.env.ib.local` (parsed in
  code; the file is never opened or printed by the tool while it works).
- It looks the persona up in `databridge/fixtures/enterprise/world.json`.
- Unknown or inactive personas exit non-zero — no token is printed.
- `--hours` is capped at 8 (the same cap enforced by `auth.MAX_TTL_S`).

## Example configs

Ready-to-edit examples live in [`examples/`](./examples/). Each has
`<paste token from ib_mint_token.py>` as the `IB_TOKEN` placeholder — replace it
with a freshly minted token before use.

| File | Persona | Server module |
|---|---|---|
| [`examples/jdoe.mcp.json`](./examples/jdoe.mcp.json) | `jdoe` (payments engineer) | `databridge_ib.mcp_server` |
| [`examples/ken.ext.mcp.json`](./examples/ken.ext.mcp.json) | `ken.ext` (external contractor) | `databridge_ib.mcp_server` |
| [`examples/maya.compliance.mcp.json`](./examples/maya.compliance.mcp.json) | `maya.compliance` (compliance officer) | `databridge_ib.audit_mcp_server` |

## Rotating tokens

Tokens expire (max 8 h). When a token expires, WorkBuddy's MCP calls will start
returning an auth error. Re-mint and replace the `IB_TOKEN` value in the
project's `.workbuddy/mcp.json`, then restart the project (or let WorkBuddy
reconnect the stdio server).
