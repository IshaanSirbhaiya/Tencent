# `fixtures/mcp/` — vendored MCP servers, and their hostile variants

**Everything here is synthetic.** The Aldermoor Territory, its permit office, its officers and
every application in `permits_mcp/records.json` are invented. Two `applicant_notes` fields carry
**planted prompt-injection payloads** so that containment can be *demonstrated* rather than
asserted; they are test data, not instructions to anything.

## Why servers are vendored here at all

`AGENT_PLANE.md` §6.3.5 and §10: *"No registry resolution at runtime. Servers are vendored and
pinned, never fetched on demand — a server you fetch at runtime is a server you did not review."*
The numbers behind that rule are in [`R03`](../../../Foundational%20Research/research/R03-adapter-agents-and-mcp.md)
§2: 41.6% of audited internet-facing MCP servers vanish within three days, `postmark-mcp` backdoored
~300 organisations, and scanners find <50% true positives — so "it was fine when we looked" is not
a property a runtime reference can have.

So the launch path in a pack declaration is a **file in this directory**, digest-checked at load.
`mcp.server_ref` records where the artefact came from and is never dereferenced.

## `permits_mcp/`

`server.py` is a real MCP server: a child process speaking newline-delimited **JSON-RPC 2.0** on
stdio (`initialize` → `tools/list` → `tools/call`), which is MCP's stdio transport. It imports
nothing from `databridge_*` and has no idea what a `SourcePointer` is. A test that drives it is
therefore exercising a boundary, not a mock — which matters here more than anywhere else in the
fleet, because the whole design premise is that the thing on the other side of the pipe is hostile.

It is written against the wire format rather than an SDK on purpose: 40+ CVEs landed across the
Python/TypeScript/Java/Rust MCP SDKs between January and April 2026, and a dependency inside the
containment boundary is a dependency inside the containment boundary.

Two tools: `permit_lookup(application_id)` and `permit_search(status, limit)`.

### The hostile variants

`WALNUT_MCP_VARIANT` takes a comma-separated set of flags; the honest server is the empty set.
Each reproduces one documented attack or one measured failure:

| Flag | Behaviour | Source |
|---|---|---|
| `rugpull` | rewrites `permit_lookup`'s description on the **second** `tools/list` | MCPoison / CVE-2025-54136; registry drift (arXiv 2608.00997) |
| `shell` | advertises `run_shell_command` | 687 tools exposing shell execution (arXiv 2608.00150) |
| `send` | advertises `email_permit_decision` | the exfiltration leg of the lethal trifecta |
| `identity` | advertises `verify_applicant_identity` | CLAUDE.md hard constraint 1 |
| `write` | advertises `set_permit_status` | AGENT_PLANE §6.4 |
| `concealed` | hides a payload in a description in Unicode TAG characters | arXiv 2607.05744 |
| `drift` | returns a different `status` for APP-1002 | a cited value moved |
| `drift_unprojected` | changes a field nothing projects | must **not** read as drift |
| `vanish` | drops APP-1002 | a tool that stops returning a row is not a tombstone |
| `missing_field` | omits `status` from one record | `Absent` is not `Withheld` |
| `nested` | returns an object where a scalar was declared | shape confusion |
| `envdump` | writes its own environment to `$WALNUT_MCP_ENV_DUMP` | credential-scoping proof |

`rugpull` is the one worth understanding. It changes a tool **description** with the file on disk
byte-identical, which is why a pack declaration carries *two* pins: `mcp.digest` over the artefact
and `mcp.declaration_digest` over the tool declarations the server actually serves. Only the second
one notices, and it is re-checked on every connect.

## If you edit a server in here

**You must re-pin.** `mcp.digest` in
`fixtures/packs/government/adapters/permits_registry_mcp.yaml` is a hash of these bytes, and the
loader raises `ServerQuarantined` until it is updated. That failure is the feature — it is the
same refusal a swapped binary would produce, and it should cost a review either way:

```bash
python -c "import hashlib,pathlib; p=pathlib.Path('fixtures/mcp/permits_mcp/server.py'); \
print('sha256:'+hashlib.sha256(p.read_bytes()).hexdigest())"
```

If you change a tool **name, description or input schema**, `mcp.declaration_digest` moves too:

```bash
python -c "import sys,pathlib; sys.path[:0]=['packages/adapter','packages/core','packages/graph']; \
from databridge_adapter.sources.mcp import StdioMcpTransport, declaration_digest; \
s=pathlib.Path('fixtures/mcp/permits_mcp/server.py').resolve(); \
t=StdioMcpTransport(command=[sys.executable,str(s)],cwd=s.parent); \
print(declaration_digest(t.list_tools())); t.close()"
```

Do not automate re-pinning into the test run. A digest that updates itself to match whatever is on
disk is not a pin; it is a checksum of the attack.
