# Connect WorkBuddy to the Internal Brain

This guide is for a judge. It shows how to ask the Internal Brain from Tencent WorkBuddy (desktop app 5.7.x) as
different people, and what you should see. Screenshots from our own run are in
[`submission/02_workbuddy/`](../../../submission/02_workbuddy/).

## How it works

```
WorkBuddy  --stdio-->  databridge_ib.mcp_server  --HTTP-->  Internal Brain (127.0.0.1:8100)
            IB_TOKEN = a minted token for one person
```

- **Identity is the token.** The MCP server reads `IB_TOKEN` from its environment and sends it as a bearer
  header. No tool argument or model output names a person.
- **One token per person.** A token is signed, and lasts 8 hours at most. Never commit one.
- The token is never logged or printed by the server.

## Before you start

1. The Internal Brain is running on `http://127.0.0.1:8100` (see [INTERNAL_BRAIN.md](../../../INTERNAL_BRAIN.md)).
2. The venv exists at `databridge/.venv/` (Python 3.13, `requirements-lock.txt`).
3. WorkBuddy is installed.

## Way 1: one workspace per person (default)

From the repo root:

```bash
databridge/.venv/Scripts/python.exe databridge/scripts/workbuddy_setup.py
```

This writes three workspace folders next to the repo, for `jdoe` (engineer), `ken.ext` (contractor) and
`maya.compliance` (compliance officer). Each has its own `.workbuddy/mcp.json` and its own token. You paste
nothing. Open one folder in WorkBuddy as a project, and ask a question. Each project is one person.

## Way 2: all three in one chat (`--user-config`)

```bash
databridge/.venv/Scripts/python.exe databridge/scripts/workbuddy_setup.py --user-config
```

This also writes three connectors into WorkBuddy's own list, `~/.workbuddy-ai/mcp.json`: `ib-as-jdoe`,
`ib-as-ken` and `ib-compliance`. Other servers already in that file are kept. Then:

1. Restart WorkBuddy.
2. Open **Experts · Skills · Connectors**, then **Custom connectors**.
3. Press **Trust** on each of the three.
4. In a new task, ask: *"Ask the Internal Brain the same question with the ib-as-jdoe connector and with the
   ib-as-ken connector: ..."*

This is how our screenshots were taken.

## What you should see

- **Same question, two people.** The engineer's connector returns an answer with cited sources. The
  contractor's connector returns the constant scope note, with no counts and no reason. A restricted
  document looks the same as one that does not exist.
- **Compliance connector.** It has three extra tools: `audit_events`, `audit_doc_accessors` and
  `audit_verify`. Ask it who accessed a document, then ask it to verify the audit chain. It should report the
  chain as intact.
- The connector panel lists all three connectors as enabled.

## Doing it by hand

Mint a token (from `databridge/`, default and maximum 8 hours):

```bash
.venv/Scripts/python.exe scripts/ib_mint_token.py jdoe --hours 8 --print
```

The script reads `IB_TOKEN_KEY` from `databridge/.env.ib.local` and looks the person up in
`fixtures/enterprise/world.json`. An unknown or inactive person exits with an error and prints no token.

Then create `.workbuddy/mcp.json` in the project root:

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

For a compliance officer, use `databridge_ib.audit_mcp_server` in `args`. It exposes `audit_events`,
`audit_doc_accessors` and `audit_verify`, plus `ask_internal_brain`.

Ready-to-edit examples (each has a placeholder token):

| File | Person | Server module |
|---|---|---|
| [`examples/jdoe.mcp.json`](./examples/jdoe.mcp.json) | `jdoe`, payments engineer | `databridge_ib.mcp_server` |
| [`examples/ken.ext.mcp.json`](./examples/ken.ext.mcp.json) | `ken.ext`, external contractor | `databridge_ib.mcp_server` |
| [`examples/maya.compliance.mcp.json`](./examples/maya.compliance.mcp.json) | `maya.compliance`, compliance officer | `databridge_ib.audit_mcp_server` |

## Tokens

Tokens last 8 hours at most. When one expires, calls return an auth error. Mint a new one, replace
`IB_TOKEN`, and restart the project. Tokens hold access, so keep them out of git, chat and screenshots.
