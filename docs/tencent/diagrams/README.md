# Trust-boundary diagram: Walnut Internal Brain

[`trust_boundaries.mmd`](trust_boundaries.mmd) is a Mermaid flowchart of the Walnut Internal Brain for the
fictional fintech "Company A" (synthetic data only). It shows who talks to the Brain, what the Brain talks
to, and the five main trust boundaries, TB1 to TB5, marked on the edges that cross them. CodeBuddy drew
the first version in a Plan-mode session; labels were then corrected against the code and
`deploy/lighthouse/Caddyfile`.

To view it, paste the file into a fenced `mermaid` block on GitHub or open it in any Mermaid viewer. The
request path and the full list of nine boundaries (TB1 to TB9, with evidence and limits) are in
[ARCHITECTURE.md](../ARCHITECTURE.md), sections 2 and 5. What leaves the host is in
[DATA_FLOWS.md](../DATA_FLOWS.md).

## TB1 to TB5

| Boundary | Between | What crosses it | Control |
|---|---|---|---|
| TB1 | Clients → Internal Brain API | A signed session token (HMAC over `sub, roles, exp ≤ 8 h, aud=ib, jti`) and the question text | Revocation list that fails closed; closed request schema; enforced route allowlist. The browser goes through the public edge to the web app's same-origin proxy, which relays only the `ib_session` cookie over a closed path allowlist. The WorkBuddy MCP servers run beside the API and send a per-persona bearer token. No identity is ever taken from a model or tool argument. |
| TB2 | Brain → source platforms (ingest) | Service-token reads of all content and permissions (a super-reader) | The service client is used for ingest reads; ingest retries, Gate 2 does not. Each connector supports a documented subset of its platform's permission model; anything outside it fails closed. |
| TB3 | Brain → source platforms (Gate 2, at answer time) | A `(user, doc)` permission question: one attempt per document, one 2 s budget per batch | Any error, timeout or 429 is a deny. No cache on this path; the source decides now. The verdict is one of `verified`, `drift_material`, `vanished`, `forbidden`, `unverifiable`, and only the first two allow. |
| TB4 | Brain → Tencent TokenHub (generation) | System prompt + the Gate-2-released passages only + the question, per request | The model has no tools. Chain `hy3 → deepseek-v4.1-flash → deterministic extractive` on error, timeout, 429 or grounding failure. Hy3 is configured but not yet called live (account verification pending). Provider-side retention is not verified by us. |
| TB5 | Brain → external ledger-head sink | The ledger head only: `{log_id, seq, head, at}`, no content | Only a verified ledger is published, and a sink failure records nothing. Once the sink is a separately pushed git repo or a compliance Slack channel, a key-holding insider cannot rewrite heads already published. The default sink is a local file. |

## Other crossings shown in the diagram

- **Signed webhooks (platforms → Brain):** HMAC-SHA256 over `timestamp.delivery_id.body`, a ±300 s
  window, replayed `X-IB-Delivery` ids rejected, a 256 KiB cap. Events carry ids only and only
  invalidate: the connector re-fetches the document and its permissions. This is TB6 in ARCHITECTURE.md.
- **Lighthouse public edge (Caddy):** the internet reaches only the web app's pages, static assets and its
  same-origin `/api/ib/*` proxy, behind TLS and security headers. Stock Caddy has no rate limiter. The
  API, mock and web containers publish no host ports, and the deployed stack uses the mock platforms
  only. The kit is written but there is no live instance.
- **WorkBuddy and its model provider** sit outside the Brain. Walnut's ledger covers Walnut's answer
  only; WorkBuddy's own model and memory are out of scope.
- **Compliance audit console:** a `compliance_officer` reads metadata and may request a logged, reasoned
  reveal of answer text; every audit call is itself ledgered.
- **Embedder (ingest):** the offline hashing embedder runs locally, so no chunk leaves the host. A
  TokenHub embedding alias is defined but not wired into the index.

## Colour key

- Red: outside the Brain (untrusted clients, external sink, compliance officer)
- Yellow: Tencent services (Lighthouse/Caddy edge, TokenHub). The embedder is yellow because its
  optional alias is a TokenHub model; today it runs locally.
- Plain: inside the Walnut Internal Brain process
