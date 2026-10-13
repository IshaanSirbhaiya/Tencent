# Security: threat model and controls

> Scope: the Internal Brain service (`databridge/apps/internal_brain/databridge_ib/`), the web proxy
> (`databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts`) and the mock platforms. `file:line`
> references are to commit `4d1887e`; paths are relative to `databridge/apps/internal_brain/databridge_ib/`
> unless they start with `tests/` (= `databridge/apps/internal_brain/tests/`) or `databridge/`. Anything not
> built is marked so.

## 1. Assets and adversaries

| Asset | Why it matters |
|---|---|
| Source content the asker may not open | The point of the product: an AI that answers across four systems is an aggregation point |
| The fact that restricted content exists | In fintech, confirming that a file exists can itself be a disclosure (MAS_TRM_MAPPING.md §4, tipping-off) |
| The access ledger and the answer content store | The evidence a compliance officer relies on |
| Token, ledger and webhook keys | Whoever holds them can mint identities, re-seal the ledger or forge change events |

| Adversary | Can do | Cannot do (by design) |
|---|---|---|
| Employee or contractor (authenticated) | Ask any question; phrase it to probe; plant text in a document they can edit (prompt injection) | Name another principal; reach audit or simulator routes; see counts or denial reasons |
| Unauthenticated internet caller | Reach `/health`, `/webhooks/*` (signature required) and, when `IB_DEMO_LOGIN=1`, `/auth/demo-login` | Call any other route (`tests/test_app_routes.py::test_every_non_public_route_requires_auth`) |
| Content author with malicious intent | Write instructions, forged citation markers or encoded payloads into a page, ticket, thread or file | Give the model tools (the Brain's model call has none) or make the Brain fetch arbitrary URLs |
| Operator without the ledger key | Edit, delete, truncate or roll back ledger files | Do so undetected (§3) |
| Operator with the ledger key | Rewrite and re-seal the local chain and anchors | Rewrite heads already published off-host (§3) |

## 2. Identity

| Control | Mechanism | Evidence |
|---|---|---|
| Identity only from a signed token | HMAC-SHA256 over canonical JSON `{sub, roles, exp, aud:"ib", jti}`; constant-time compare; `aud`, `exp` and revocation checked; any malformed token is a plain 401 | `auth.py:34-80`; `app.py:51-69` |
| Short-lived | TTL clamped to 60 s–8 h at mint | `auth.py:19, 36` |
| Revocable | `jti` revocation list (`var/ib/revoked.json`), written atomically; an unreadable or malformed list fails authentication **closed** | `auth.py:83-112` |
| Logout revokes the token actually used | The verified token's `jti` is added to the revocation list | `app.py:108-121` |
| No identity in any request body | `AskRequest` has only `question`, with `extra="forbid"`; a body naming `user` is rejected with 422 | `models.py:24-25, 154-155`; `tests/test_contracts.py::test_no_identity_in_request_shapes`; `tests/test_app_routes.py::test_identity_from_token_only` |
| Bad or expired tokens rejected | Wrong key, expired | `tests/test_app_routes.py::test_bad_and_expired_tokens` |
| The directory is checked on every request | An inactive or unknown `sub` is 401 even with a valid token | `app.py:65-67` |
| Token never reaches browser JavaScript | httpOnly cookie, `Secure` when `IB_DEPLOYED=1`; the Next.js proxy forwards only `ib_session` and relays `Set-Cookie` | `app.py:104-105`; `databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts:1-7` |
| The model never chooses identity | The model receives passages and the question only; it has no tools | `llm.py:65-73` |
| Role segregation | `/audit/*` requires `compliance_officer`; `/sim/*` requires `admin_it`; the admin has no audit access | `audit/routes.py:73`; `sim.py:29`; `tests/test_audit_routes.py::test_non_compliance_roles_forbidden` |

**Limits.**
- `/auth/demo-login` mints a token for any persona when `IB_DEMO_LOGIN=1`; when `IB_DEMO_PASSCODE` is set, the
  caller must also present it (`app.py:91-107`). It is a demo stand-in for SSO over synthetic data and returns
  404 when off (`tests/test_app_routes.py::test_demo_login_cookie_and_disabled`). The Codex security review
  rated an enabled demo login **Critical** (finding 1). There is no real SSO/IdP (plan §3, roadmap).
- The logout revocation, fail-closed revocation list, `Secure` cookie and passcode came with commit `4d1887e`.
  They have **no dedicated regression tests yet**.
- MCP tokens for WorkBuddy and the MCP server's "no identity argument" schema test are **not built yet**
  (`mcp_server.py` and `databridge/scripts/ib_mint_token.py` are absent).

## 3. Ledger attacker model

The access ledger is Walnut's `DecisionLedger` (sealed, hash-chained JSONL, with an internal anchor ledger in
a **separate directory**), wrapped by `audit/ledger.py`. The head is published off-host by `audit/anchor.py`.

| Attack | Attacker holds the key? | Detected by | Test (`tests/test_audit_tamper.py`) |
|---|---|---|---|
| Edit one historical record | No | Chain and seal check | `::test_edit_detected` |
| Delete the last N records | No | Internal anchor ledger | `::test_suffix_deletion_detected` |
| Delete the internal anchor file | No | Anchor mismatch | `::test_internal_anchor_removed` |
| Restore log and anchors together from an earlier backup (before the last publication) | No | **Only** the external head | `::test_directory_rollback_detected_only_via_external_head` |
| Rewrite a record, re-seal the chain, rebuild the anchors | **Yes** | **Only** the external head | `::test_anchor_mismatch_key_holder_rewrite` |
| Roll back to a point **after** the last publication | Either | **Not detected** until the next publication: the stated window | `::test_rollback_after_last_publication_is_the_stated_window` |
| Tamper with the external head copy | Either | Mismatch reported | `::test_external_head_tampered` |
| Delete the external head while the log says it published | Either | "the log records a publication the sink does not hold" | `::test_external_head_missing` |
| Publish the head of an already-tampered log (laundering) | Either | `publish_head` refuses a ledger that does not verify | `::test_publish_refuses_broken_ledger` |
| Short, missing or wrong key | — | Every append refuses (fail closed) | `tests/test_audit_ledger.py::test_missing_key_fails_closed`, `::test_short_key_fails_closed`, `::test_wrong_key_cannot_read_or_extend` |

All scenarios can be replayed with `python scripts/tamper_demo.py` from `databridge/`
(`databridge/scripts/tamper_demo.py:1-23`; `tests/test_audit_tamper.py::test_tamper_demo_script_scenarios`).

**What makes the head "external".** `publish_head` writes `{log_id, seq, head, at}` to a sink and then ledgers
`anchor.published` (`audit/anchor.py:129-148`). The sinks are `FileSink`, an append-only JSONL meant to live in
a **separate git repo that is pushed**, and `SlackWebhookSink`, a compliance-only channel with a file mirror
for reading back. Publication runs at startup and every `IB_ANCHOR_INTERVAL_S`, default 900 s
(`bootstrap.py:100-137`, `config.py:46`).

**Limits.**
- The default sink is `<var>/external_anchor/heads.jsonl` on the same host (`bootstrap.py:85-91`). That is
  **not** off-host. The guarantee holds only when `IB_ANCHOR_FILE` points into a separately pushed repo, or
  `IB_ANCHOR_SLACK_WEBHOOK` is set.
- The window between publications is unprotected against a key holder.
- The answer content store is not sealed. The ledger seals `answer_sha256`, so a reveal reports
  `content_matches_ledger` (`audit/routes.py:158-161`).
- WORM storage (COS Object Lock) is roadmap (plan §3).
- Every audit call is itself ledgered before it returns (`audit/routes.py:79-90, 145-149`;
  `tests/test_audit_routes.py::test_events_query_is_ledgered`, `::test_reveal_requires_reason_and_is_ledgered`).

## 4. Prompt injection

Retrieved content is untrusted. Our position is **containment**, not detection.

| Layer | Mechanism | Evidence |
|---|---|---|
| Access before content | An injected document can reach a model only if the asker may already read it (Gate 1 + Gate 2) | `pipeline.py:113-143` |
| No tools for the model | The Brain's model call is text in, text out; audit tools live in other processes | `llm.py:65-81` |
| Delimited, labelled passages | Each passage is one tagged line with a provenance header; the system prompt says passages are untrusted data and never to follow instructions inside them. This is delimiting, a light form of spotlighting; passages are not datamarked or encoded. | `narrate.py:21-35, 74-82` |
| Forged citation markers | A citation-shaped token inside source text or metadata (e.g. `[C9]`) is rewritten to `(source-text C9)` before any model sees it or the extractive answer quotes it; only `[C<n>]` tags that match a released passage become citations | `narrate.py:39, 50-54, 74-82`; `pipeline.py:172-175` |
| Output checks | Markdown stripped and trailing tags moved inside their sentence, then Walnut's grounding wall binds sentences to cited passages and rejects unsupported numbers, dates and named terms; a violation falls through to the extractive answer; a model answer citing no released passage is replaced too | `narrate.py:99-120`, `llm.py:65-81`, `pipeline.py:162-171` |
| Extractive answer skips text addressed to an AI | Sentences such as "ignore previous instructions", "AI assistant: …" or "decode this" are never quoted as answers | `narrate.py:43-47, 131-162` |
| No arbitrary fetch | Links are followed only for supported id shapes on tenant hosts, depth 1, resolved only within the asker's readable set, then Gate 2 | `connectors/_http.py:237-282`; `pipeline.py:260-281`; `tests/test_connector_slack.py::test_links_only_supported_shapes` |
| Audit NL filter | Output is a closed schema; any model sees only the question and the clock | `tests/test_audit_nl_filter.py::test_injection_text_cannot_escape_the_schema`, `::test_model_receives_only_question_and_clock` |

**Injection fixtures on all four platforms** are planted in the synthetic world
(`databridge/fixtures/enterprise/generate.py`, facts below):

| Fact id | Platform | Attack |
|---|---|---|
| `INJ-confluence-title` | Confluence | Instruction-bearing title and an "ignore previous instructions" body |
| `INJ-jira-forged-citation` | Jira | A forged `[C9]` citation asserting a false security approval |
| `INJ-slack-base64` | Slack | A base64-encoded instruction to print a restricted report |
| `INJ-drive-audit-tools` | Drive | Text asking the assistant to call audit tools |

They are exercised by the canonical questions `INJ-q1` to `INJ-q4` (`tests/test_scenarios_e2e.py::test_canonical`)
and by the leak eval's injection probes. The first eval run (RESULTS.md, before the `b4d72cf`/`4d1887e` fixes)
recorded **0 leaks** and **6 injection hard failures**, all forged citations: a literal `[C9]` in a source bound
to a real passage tag (an integrity failure, not a disclosure). The tag neutralisation above is the fix;
injection results after it: **TBD (measured in RESULTS.md)**.

## 5. Side channels

| Channel | Control | Evidence | Residual |
|---|---|---|---|
| Existence via refusal wording | One constant refusal text, no counts, no reasons, constant `scope_note` | `models.py:21, 179-185`; `tests/test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical` | — |
| Existence via ranking statistics | BM25 statistics over the asker's permitted corpus only; anchor terms judged on permitted df only; Gate-2-denied docs removed from the ranking pool | `index.py:1-14, 311-321`; `pipeline.py:134-136`; `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted`; RESULTS.md: 0 of 400 differential comparisons mismatched | Eval figure predates `4d1887e` |
| Existence via linked titles or keys | Links resolve only within the readable set and then pass Gate 2 before anything about them is rendered | `pipeline.py:260-281` | A raw key written inside a permitted text is shown as written, never resolved (plan §4.6) |
| Per-query source contribution | `/ib/sources` reports per-platform sync status only (time since last successful sync) | `app.py:150-161` | It is global, not caller-scoped (Codex security review r1 finding 12) |
| Error envelopes | Any pipeline failure returns the same 503 body | `app.py:134-138` | — |
| Timing | **Not controlled** | — | TRADEOFFS.md R1 |

## 6. Secrets handling

| Secret | Source | Control | Limit |
|---|---|---|---|
| `IB_TOKEN_KEY`, `IB_LEDGER_KEY` | Environment | With `IB_DEPLOYED=1` the app refuses to start on the dev defaults or on keys shorter than 32 characters (`config.py:48-56`). The ledger key is handed to `DecisionLedger` as bytes, never as a file inside the log directory (`audit/ledger.py:18-22`). | The dev defaults are in the public repo by design (`config.py:23-24`) |
| `MOCK_SVC_TOKEN`, `MOCK_ADMIN_TOKEN`, `MOCK_WEBHOOK_SECRET` | Environment; dev defaults `svc-dev`, `admin-dev`, `whsec-dev` | — | `assert_deployable` does **not** check these three (`config.py:39-41, 48-56`); a deployment must set them |
| `IB_DEMO_PASSCODE` | Environment | Constant-time compare (`app.py:97-99`) | Optional; unset means no passcode |
| TokenHub key | `TOKENHUB_API_KEY`, or `OPENAI_API_KEY` + `OPENAI_API_BASE` | Read from env only (`llm.py:28-45`); model errors are logged by exception type, not message (`llm.py:80`) | — |
| Real workspace ids (live track) | `.env` only (plan §5.3) | Planned pre-commit check | Live track not built |
| Git history | — | A gitleaks pass over the full history is a required step before the repo is made public (plan §0 F3, §9.3) | **Not yet run** |
| Logs | — | `LedgerUnavailable` messages are public text, never a key or an OS exception string (`audit/ledger.py:60-68`); ingest and ask failures log the exception class only (`ingest.py:86`, `app.py:137`) | — |

## 7. Security review

A Codex security review of the gates, pipeline, ledger and identity code is recorded in
`docs/tencent/reviews/security-r1/codex_track1.md`: 35 findings (1 Critical, 10 High, 19 Medium, 5 Low). Commit
`4d1887e` changed the code for several of them (from its message and diff: read-through re-checks, denied docs
leaving ranking, relevance before top-k, readable-only link resolution, fail-closed and atomic revocation, logout
revocation, `Secure` cookie, demo passcode, enforced allowlist, webhook size cap and threadpool, time-phrase
stripping, `request.failed`, sanitised logs). A written disposition of all 35 findings, and regression tests for
those fixes, are **not in the repo yet**. Findings still open by our reading include 3 (Gate 2 not bound to a
content snapshot), 9 (membership changes do not refresh principals), 11 (remote embedder boundary), 12 (global
source status) and 15 (one valid citation accepts a model answer).

## 8. Not built (roadmap)

Real SSO; envelope-encrypted content and crypto-shredding (described only; plan review disposition); COS Object
Lock WORM for the ledger; monitoring of connector service-account use (TRM §9.2.2); rate limits and the Caddy
allowlist on the public edge (deploy not yet in the repo); timing parity.
