# Internal Brain — Security Review (read-only)

**Scope.** `databridge/apps/internal_brain/databridge_ib/`: `app.py`, `auth.py`, `pipeline.py`,
`gates.py`, `narrate.py`, and the `audit/` package (`__init__`, `anchor.py`, `content.py`,
`ledger.py`, `nl_filter.py`, `query.py`, `routes.py`). Supporting modules read for context:
`index.py`, `acl.py`, `directory.py`, `connectors/base.py`, `connectors/_http.py`,
`connectors/slack.py`, `sensitive.py`. **No existing file was modified.** Synthetic data only
(Company A).

**Threat model.** Three questions, exactly as posed:
- (a) a user reads content they are not permitted to read;
- (b) a user learns that restricted content exists;
- (c) a user tampers with the audit trail without detection.

All access-control logic is evaluated against the designed controls: Gate 1 (`Index.readable` +
`acl.allows`), Gate 2 (source `check_access`), the drift read-through, and the sealed/hash-chained
access ledger plus its external anchor.

---

## Findings, ranked by severity

| # | Severity | Category | Location | Title |
|---|----------|----------|----------|-------|
| 1 | **Critical** | (a)+(c) | `app.py:91-106` | `demo-login` with no/blank passcode = impersonate any persona (incl. `compliance_officer`, `admin_it`) |
| 2 | **High** | (c) | `audit/ledger.py:319-355`, `audit/anchor.py:141-169` | Compromise of `IB_LEDGER_KEY` allows undetectable forgery/rewrite of records after the last external publication |
| 3 | **High** | (c) | `audit/content.py:70-96`, `audit/routes.py:199-216` | Answer content store is not hash-chained and is not covered by `verify`; edits to unrevealed answers evade detection |
| 4 | **Medium** | (a)+(c) | `app.py:164-181`, `connectors/_http.py:219-258` | `/webhooks/{platform}` has no app-layer auth and no rate limit; trust rests entirely on the connector signature |
| 5 | **Low** | (c) | `app.py:133` | Ask `channel` is attacker-controllable via the `x-ib-channel` header (self-attribution tampering) |
| 6 | **Low** | (b) | `app.py:150-161` | `/ib/sources` discloses connected platforms, sync lag and SLO (minor system-topology disclosure) |
| 7 | **Low** | (a) | `app.py:104-105` | Session cookie `secure` flag set only when `deployed`; plaintext cookies in dev / over HTTP |

---

### 1. Critical — `demo-login` with no/blank passcode impersonates any persona  *(a)+(c)*
**File / line.** `databridge_ib/app.py:91-106` (logic at `:95-106`).

**Attack scenario.** The demo sign-in is gated only by `IB_DEMO_LOGIN=1`. When it is on, the
passcode check at `:97-99` is *skipped entirely* if `IB_DEMO_PASSCODE` is unset or empty
(`if passcode and not hmac.compare_digest(...)`). The handler then resolves any string the caller
supplies as `persona` straight from the directory (`:100-106`) and mints a valid 8-hour token
carrying that user's real roles. An attacker on a public/internal URL can therefore:
- become `compliance_officer` and read the entire audit trail, including `/audit/reveal` of any
  answer (category c exposure + cross-user content reads → (a));
- become `admin_it` and any employee, bypassing the intended identity source entirely.

Even *with* a passcode set, the endpoint lets anyone who knows the passcode assume any directory
identity — there is no step-up and no SSO binding. Because roles are taken from the directory (not
the token) this is the only way to obtain a privileged `sub`, so it is the single most important
exposure.

**Suggested fix.** Require `IB_DEMO_PASSCODE` to be non-empty whenever `IB_DEMO_LOGIN=1` and refuse
to boot otherwise (fail closed): `if svc.cfg.demo_login and not passcode: raise RuntimeError(...)`.
Better, remove demo-login from any non-ephemeral environment and bind sign-in to the real SSO/IdP
assertion rather than a free-text persona. Rate-limit the endpoint and audit every successful
demo-login as an `auth.*` event.

---

### 2. High — sealing-key compromise permits undetectable post-publication ledger forgery  *(c)*
**File / line.** `databridge_ib/audit/ledger.py:319-355` (`_verify`) and
`databridge_ib/audit/anchor.py:141-169` (`publish_head`/`verify`).

**Attack scenario.** The whole access ledger is sealed with `IB_LEDGER_KEY` and chained; the only
control that detects a *whole-var-directory rollback* or a *rewrite by a key holder* is the head
published to an off-host sink (a separate git repo / Slack channel). `anchor.py` itself states the
limit: records appended **after** the last publication are *not* covered — a key holder can rewrite
or delete them, re-seal the chain and regenerate the internal anchor ledger, and the log will still
verify internally. A rollback to a pre-publication state is caught only if an external head exists
and is queried. If `IB_LEDGER_KEY` leaks (it is a single env secret, the same process also holds
`IB_TOKEN_KEY`), the attacker can forge `audit.reveal`, `answer.released`, or `request.accepted`
events and, for anything after the last anchor, do so *with no detection at all*.

This is a documented design limitation, but it is the crux of "tamper without detection", so it is
flagged rather than assumed safe.

**Suggested fix.** Treat `IB_LEDGER_KEY` as the crown jewel: store it in a KMS/HSM, never co-locate
it with the application runtime where feasible, and rotate. Shorten the external-publication
interval (anchor every few minutes, not 15) and pin the sink so it is genuinely attacker-inaccessible
(append-only, off-host, integrity-protected). Consider per-event signatures or a Merkle/tree
structure so that a rewrite of any prefix also breaks later records, shrinking the blind window.
Alert on `external_head_match == False` and on a missing external sink when one is configured.

---

### 3. High — answer content store is not tamper-evident and not covered by `verify`  *(c)*
**File / line.** `databridge_ib/audit/content.py:70-96` (`AnswerStore.put/get`) and
`databridge_ib/audit/routes.py:199-216` (`audit_reveal`).

**Attack scenario.** Answer text lives in `var/ib/content/answers.sqlite`, keyed by a random
`answer_ref`. It is *write-once* at the application layer (`INSERT`, no `UPDATE` of the answer), but
it is **not part of the sealed hash chain** and `AccessLedger.verify()` never inspects it. The
integrity check exists only at reveal time: `routes.py:211-216` compares `sha256_text(stored.answer)`
against the sealed `answer_sha256` and returns 409 on mismatch. Consequently:
- anyone with filesystem write access who edits an answer that is **never revealed** leaves no trace
  (the mismatch is only ever computed on reveal);
- `verify` can report `ok=True` while the content store has been silently altered.

For a system whose entire value proposition is a tamper-evident audit trail, an out-of-band,
unsealed copy of the most sensitive field (the answer text) is a meaningful gap.

**Suggested fix.** Either (i) bring the content store inside the verified ledger (e.g. keep the text
in the sealed chain or in a MAC'd blob keyed by `IB_LEDGER_KEY`), or (ii) have `verify` recompute
each stored answer's digest against the ledger's `answer_sha256` and surface a `content_intact`
boolean + problem list, so undetected tampering is visible without a reveal. At minimum, log a
periodic integrity scan.

---

### 4. Medium — `/webhooks/{platform}` has no app-layer auth and no rate limit  *(a)+(c)*
**File / line.** `databridge_ib/app.py:164-181`; signature logic in
`databridge_ib/connectors/_http.py:219-258` (`WebhookVerifier`) and `connectors/slack.py:509-519`.

**Attack scenario.** The webhook route performs **no authentication itself** — it is not wrapped in
`Depends(current)` and relies wholly on `conn.parse_webhook` raising `WebhookRejected` (app.py:176).
Protection therefore depends on every connector's `WebhookVerifier` having a *strong, non-empty
secret* and a correct HMAC check. The verifier is well built (delivery id inside the MAC, ±300 s
timestamp window, in-process replay set), which is good — but:
- the replay set is **per process** (`_http.py:251-257`): a second worker or a process restart can
  accept the same captured delivery again inside the 300 s window. Webhook events only *invalidate*,
  so a replay costs work rather than granting access, but it is still an integrity gap;
- there is **no rate limiting** on the unauthenticated endpoint, so a flood of valid (or rejected)
  deliveries can drive re-ingestion / DoS;
- if any connector is ever deployed with an empty/weak secret, `WebhookVerifier.__init__` raises
  (good, fail-closed at construction), but the boundary is implicit and easy to regress.

There is no direct content-injection path (events only trigger re-fetch from the authoritative
source, which then goes through Gate 1/2), so this is rated Medium, not High.

**Suggested fix.** Add a central, connector-agnostic guard: require the webhook secret to be present
at startup for every mounted connector (already partially done), and add per-IP / global rate
limiting and a shared (not per-process) replay cache (e.g. a short-TTL store) so replays are caught
across workers. Document that the webhook is unauthenticated at the app layer by design and that the
signature is the sole control.

---

### 5. Low — ask `channel` is attacker-controllable via `x-ib-channel`  *(c)*
**File / line.** `databridge_ib/app.py:133`.

**Attack scenario.** `channel = "workbuddy-mcp" if request.headers.get("x-ib-channel") == "workbuddy-mcp" else "ui"`.
A user on the UI can set the `x-ib-channel` request header to label their own request as coming from
the WorkBuddy/MCP channel. This tampers with audit metadata (the recorded `channel` of
`request.accepted`), potentially skewing compliance reporting about which surfaces are used. Impact
is limited to the caller's own requests, hence Low.

**Suggested fix.** Derive `channel` from the authenticated session/principal context (or a
server-side, role-bound trust), not from a caller-supplied header; if the MCP path legitimately sets
it, bind that to the MCP authentication flow rather than an open header.

---

### 6. Low — `/ib/sources` discloses platform topology and freshness  *(b)*
**File / line.** `databridge_ib/app.py:150-161`.

**Attack scenario.** Any employee can call `/ib/sources` and learn which external platforms are
connected (Slack, Jira, Drive, Confluence), their last-sync timestamps, current lag and the SLO.
This is metadata, not document content, and reveals nothing document-level, but it is a small
information disclosure about the estate's integrations and their operational state.

**Suggested fix.** Acceptable as-is for an internal tool, but consider restricting to a narrower role
or suppressing exact `last_sync_at`/`lag` for non-privileged callers if the integration map is
sensitive.

---

### 7. Low — session cookie `secure` only when `deployed`  *(a)*
**File / line.** `databridge_ib/app.py:104-105`.

**Attack scenario.** `set_cookie(..., secure=svc.cfg.deployed)`. In any non-`deployed` run the
`ib_session` cookie is sent over plaintext HTTP, so on a shared/non-TLS network the bearer token can
be sniffed, giving full impersonation of that user (category a). In production `secure=True` is set,
so this is a dev/test-hygiene issue, rated Low. Note `httponly=True` and `samesite="lax"` are set in
both modes, which is good (mitigates script/XSS exfil and most CSRF).

**Suggested fix.** Always set `secure=True` unless explicitly overridden for a localhost test, and
prefer `samesite="strict"` where the UX allows.

---

## What was checked and found correctly defended

**Access control over content (a) — strong.**
- **Gate 1** (`index.py:289-299` `readable`, `acl.py:36-54` `allows`): every doc is tested against
  the principal's policy; unknown/parse-failure ACLs resolve to `nobody` (fail closed); inactive
  users are denied. Statistics for ranking are computed over the *permitted* corpus only, so a
  restricted doc cannot change a permitted passage's score.
- **Gate 2** (`pipeline.py:194-198`, `gates.py:28-50`): every candidate is re-checked against the
  source with a hard batched timeout; any exception/timeout/429/unknown-connector → `forbidden`
  (fail closed). Connectors never consult the IB index.
- **Drift read-through** (`pipeline.py:200-232`): a doc whose source version moved is re-fetched,
  re-masked and re-checked; material drift with no version, a different fetched version, or a fresh
  ACL denial → omitted.
- **Linked objects** (`pipeline.py:263-300`) go through Gate 1 + Gate 2 again; only allowed linked
  docs reach passages.
- **Refusals leak nothing**: when `refused` is true, `citations` is empty (`pipeline.py:177`) and the
  answer is the generic `REFUSAL_TEXT`; the model only ever sees permitted passages, and the system
  prompt (`narrate.py:21-35`) forbids naming other documents/spaces.

**Existence of restricted content (b) — well contained.**
- Links are resolved **only within the asker's readable set** (`pipeline.py:275-284`), so a
  restricted object with the same identifier cannot change link resolution.
- The generic refusal is identical whether a relevant doc exists-but-is-denied or simply does not
  exist, so there is no denial/non-existence oracle for the asker. (Compliance officers *do* see
  considered-and-denied doc titles via the audit projection — that is intended oversight, scoped to
  `compliance_officer`.)

**Audit-trail integrity (c) — strong where it counts, with the gaps above.**
- The ledger is hash-chained and sealed; `verify()` checks chain + seals + internal anchor ledger +
  (when configured) the external published head, and **never fails open** (`ledger.py:304-355`).
- Every answer text is integrity-bound to the ledger via `answer_sha256`; `audit_reveal` writes the
  `audit.reveal` event **before** reading the text and returns 409 on digest mismatch
  (`routes.py:199-216`).
- Failed audit calls are themselves ledgered (`rejected`/`error`/`mismatch`) before the 4xx/5xx
  (`routes.py:97-103`), so the audit record of an audit attempt is complete.
- The audit **projection** is *derived and rebuildable*: every read re-syncs from the verified ledger
  (`routes.py:105-109`, `query.py:207-235`), and `sync()` rebuilds when any stored hash diverges —
  so tampering with `audit.sqlite` does not persist or fool the system.
- The deliberate non-ledgering of `/audit/verify` (`routes.py:221-235`) is safe: the response carries
  only counts and digests, and is exactly what an officer needs when the ledger is broken.

**Authentication & transport — solid.**
- Tokens are HMAC-SHA256 over canonical JSON with `aud`, `exp`, `jti`; `verify` bounds length/ASCII,
  checks audience, expiry and revocation, and uses constant-time comparison (`auth.py:34-80`).
- Revocation is atomic and serialised (`auth.py:103-113`); a `load_revoked` failure fails closed.
- The route allowlist is enforced at startup, not just documented (`app.py:187-189`).
- All SQL uses bound parameters (`query.py`, `content.py`, `ledger.py`), so there is no SQL injection
  in audit/content paths.
- Webhook signatures bind the delivery id *inside* the MAC and enforce a timestamp window + replay set
  (`_http.py:204-258`), preventing cross-id replay of a captured delivery.
- CSRF is mitigated by `SameSite=Lax` + `HttpOnly` on the session cookie; secrets are compared with
  `hmac.compare_digest` throughout (demo passcode, token, webhook).

---

*Note on residual trust.* The system's security reduces, at the root, to the confidentiality of two
env secrets — `IB_TOKEN_KEY` (forge any session) and `IB_LEDGER_KEY` (forge/rewrite the audit
trail) — plus the strength of each connector's webhook secret. Findings 1, 2 and 4 are all
expressions of how those secrets (or their absence) translate into (a)/(c) impact; tightening secret
handling and the demo-login gate closes the highest-severity paths.
