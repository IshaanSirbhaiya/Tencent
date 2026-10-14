# Trust-boundary diagram — Walnut Internal Brain

`trust_boundaries.mmd` is a Mermaid `flowchart` of the Walnut Internal Brain for the
fictional fintech **"Company A"** (synthetic data only). It shows the clients, the
Internal Brain gateway, Tencent TokenHub, the four source platforms, the compliance
audit console, the external ledger-head sink, and the Lighthouse public edge (Caddy),
with the five trust boundaries **TB1–TB5** marked on the crossing edges.

Render it on GitHub by embedding the `.mmd` in a fenced ` ```mermaid ` block, or with
any Mermaid-aware viewer.

## Legend — TB1 to TB5

| Boundary | Between | What crosses it | Control |
|---|---|---|---|
| **TB1** | Employee (web) / WorkBuddy clients → IB gateway (via Lighthouse Caddy) | Identity token (HMAC `sub, roles, exp ≤ 8h, aud=ib, jti`) + the question text only | Signed token with revocation list (fails closed); closed request schema; enforced route allowlist; Caddy forwards only the `ib_session` cookie / `Authorization` bearer over an allowlisted path set. No identity is ever taken from a model or tool argument. |
| **TB2** | IB → source platforms (ingest) | Service-token reads of **all** content and ACLs (a super-reader) | Service client used for ingest reads; ingest retries (Gate 2 does not). Correctness depends on the platform's own permission model (supported subset only; unsupported ⇒ fail-closed). |
| **TB3** | IB → source platforms (Gate 2, at answer time) | A `(user, doc)` permission question, one attempt per doc, 2 s batch budget | Any error / timeout / 429 ⇒ DENY. No cache on this path; the source decides *now*. Verdict mapped onto the existing lattice (`VERIFIED \| DRIFT_MATERIAL \| VANISHED \| FORBIDDEN \| UNVERIFIABLE`). |
| **TB4** | IB → Tencent TokenHub (generation) | System prompt + the **Gate-2-released passages only** + the question, per request | Only released passages are sent; the model has no tools. Chain `hy3 → deepseek-v4.1-flash → deterministic extractive` on error/timeout/429. Provider-side retention is not verified by us (see DATA_FLOWS.md). |
| **TB5** | IB → external ledger-head sink | Ledger head hash only: `{log_id, seq, head, at}` (no content) | Publishes only a *verified* ledger; a sink failure records nothing. The head is posted off-host (separate git repo / compliance Slack) so a key-holding insider cannot rewrite externally published heads. |

## Supporting crossings (not the five core boundaries)

- **Signed webhooks (platforms → IB):** the four platforms notify the IB webhook
  receiver with a **signed webhook** — HMAC-SHA256 over `timestamp.delivery_id.body`, a ±300 s
  window, replayed `X-IB-Delivery` ids rejected, 256 KiB cap. Events carry **ids only
  and only invalidate**; the connector re-fetches the document and its ACL. (This is
  the ingest counterpart to TB2/TB3 in the architecture's fuller TB6.)
- **Lighthouse public edge (Caddy):** the public internet reaches only the web app and
  its same-origin `/api/ib/*` proxy, behind TLS and security headers (no rate limiter in
  stock Caddy; see `deploy/lighthouse/Caddyfile`). The IB, mock and web services publish
  no host ports, and the public URL never connects to real workspaces.
- **WorkBuddy and its model provider** sit **outside** the Brain: Walnut's ledger
  covers Walnut's answer only; WorkBuddy's own model/memory are out of scope.
- **Compliance audit console:** a `compliance_officer` reads metadata and may request a
  logged, reasoned reveal of answer text; every audit call is itself ledgered.

## Node colour key

- Red = outside the Brain (untrusted clients, external sink, compliance officer)
- Yellow = Tencent-controlled (Lighthouse/Caddy edge, TokenHub, embedder)
- Plain = inside the Walnut Internal Brain process
