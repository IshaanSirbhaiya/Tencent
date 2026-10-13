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
| Roll back to a point **after** the last publication | Either | **Not detected.** A later publication anchors the surviving branch; it does not recover or reveal the erased records | `::test_rollback_after_last_publication_is_the_stated_window` |
| Tamper with the external head copy | Either | Mismatch reported | `::test_external_head_tampered` |
| Delete the external head while the log says it published | Either | "external head missing" | `::test_external_head_missing` |
| Withhold the newest external head (the sink returns an older, authentic one) | Either | "external head stale": the log records a later `anchor.published` | `::test_stale_external_head_fails_despite_later_publication` |
| Make the sink unreadable, or plant a malformed or foreign-log head | Either | `ok=false`, never an exception, with or without a publication marker in the log | `::test_sink_exception_fails_closed_even_without_a_publication`, `::test_malformed_or_foreign_external_head_fails`, `::test_corrupt_external_file_fails_closed` |
| Publish the head of an already-tampered log (laundering) | Either | `publish_head` refuses a ledger that does not verify internally (a key holder's re-sealed log does verify: see disposition #1) | `::test_publish_refuses_broken_ledger` |
| Short, missing or wrong key | — | Every append refuses (fail closed) | `tests/test_audit_ledger.py::test_missing_key_fails_closed`, `::test_short_key_fails_closed`, `::test_wrong_key_cannot_read_or_extend` |

All scenarios can be replayed with `python scripts/tamper_demo.py` from `databridge/`
(`databridge/scripts/tamper_demo.py:1-23`; `tests/test_audit_tamper.py::test_tamper_demo_script_scenarios`).

**What makes the head "external".** `publish_head` writes `{log_id, seq, head, at}` to a sink and then ledgers
`anchor.published` (`audit/anchor.py`, `publish_head`). The sinks are `FileSink`, an append-only JSONL meant to
live in a **separate git repo that is pushed**, and `SlackWebhookSink`, a compliance-only channel with a file
mirror for reading back. Publication runs at startup and every `IB_ANCHOR_INTERVAL_S`, default 900 s
(`bootstrap.py:100-137`, `config.py:46`). Verification (`audit/ledger.py`, `AccessLedger.verify` and
`compare_external`; `audit/anchor.py::verify` is the same code path) accepts an external head only if it is a
record for this log (`log_id == "ib-access"`) with a positive integer `seq` and a 64-hex `head`, and is not older
than the newest `anchor.published` in the log. A sink that raises, a missing, stale, malformed or foreign head is
`ok: false` with a problem; `verify` never raises.

**Limits.**
- The default sink is `<var>/external_anchor/heads.jsonl` on the same host (`bootstrap.py:85-91`). That is
  **not** off-host. The guarantee holds only when `IB_ANCHOR_FILE` points into a separately pushed repo, or
  `IB_ANCHOR_SLACK_WEBHOOK` is set (and even then see dispositions #1, #4).
- With no sink configured, `external_head_match` is `null` and `ok` reflects internal verification only.
- Records appended after the last publication are unprotected against a rollback or a key holder (#21).
- The answer content store is not sealed. The ledger seals `answer_sha256`; a reveal re-hashes the stored text
  against the digest in the **verified ledger** and returns **409 with no text** on a mismatch (#7).
- WORM storage (COS Object Lock) is roadmap (plan §3).
- Every audit call is ledgered before it returns, including failed ones: an invalid filter, a blank question
  or reason, an unparseable question, an unknown reveal target or a query failure appends `audit.query` with
  `outcome` `rejected`/`error` (a 503 if that append fails) (`audit/routes.py`, `_refuse`;
  `tests/test_audit_routes.py::test_events_query_is_ledgered`, `::test_reveal_requires_reason_and_is_ledgered`,
  `::test_failed_audit_calls_are_ledgered`, `::test_rejection_on_a_broken_ledger_is_503`). Requests rejected
  before a handler runs (401/403 from the auth dependency, a body that is not JSON or exceeds a length cap) are
  **not** ledgered.
- **Deliberate exception: `GET /audit/verify`.** When the ledger refuses its `audit.query` append, the route
  still returns the report, with `"ledgered": false` (`tests/test_audit_routes.py::test_broken_ledger_fails_closed`).
  The report holds counts, digests and problem strings only (no content, no subjects), and a broken ledger is
  exactly when the compliance officer needs it. Production would also write such calls to an independent
  durable channel (the SIEM) (#10).

**Codex security review r1, track 2 disposition** (23 findings on `audit/{ledger,content,query,nl_filter,anchor,routes}.py`;
tests are in `tests/test_audit_*.py`). "Residual" means accepted for the demo, with the production remedy named.

| # | Sev | Finding | Disposition | Evidence / production remedy |
|---|---|---|---|---|
| 1 | High | A key holder can rewrite published history, extend past the last published seq and publish a new head that supersedes the old one (`latest()` takes the highest seq) | **Residual** | Earlier heads stay in the sink and contradict the rewrite, but `verify` compares only the newest. Production: before publishing and when verifying, check that the log still contains **every** previously published `(seq, head)`, not just the latest; keep the sink in a separately administered repo with signed commits and no force-push, or an RFC 3161 timestamp authority, so earlier checkpoints cannot be removed |
| 2 | High | Projection rows are trusted without authenticating them against the ledger | **Mitigated / residual** | Reveal no longer trusts the projection: `answer_ref` and `answer_sha256` come from the verified ledger (`routes.py`, `audit_reveal`). Search/accessor rows still come from the projection, which `bootstrap.py` keeps **in process memory** (`AuditProjection(cfg, ledger)`, no file). Production: the projection is a cache rebuilt from the verified ledger at start and on any seq/hash disagreement, never a shared writable file (or each row carries a MAC under a key the projection writer does not hold) |
| 3 | High | External rollback detection is not enforced on appends, projection sync or publication | **Residual** | Only `/audit/verify` compares with the external head. Production: verify against the trusted external checkpoint at startup and before each publication, refuse appends and audit reads (503) when the log does not extend it, and re-check per verified snapshot |
| 4 | High | The supplied sinks are not an independently protected authority (`FileSink` is a local append; Slack verification reads a local mirror) | **Residual / limit** | Stated above. Production: push `FileSink` to a separate repo with signed commits and branch protection administered by another team, or COS Object Lock (WORM), or an RFC 3161 TSA; verify against the remote copy and treat local files as caches; read Slack back through its API, not a mirror |
| 5 | High | A stale external checkpoint passed despite a later recorded publication | **Fixed** | `compare_external` fails when the sink's latest seq is older than the newest `anchor.published`; `tests/test_audit_tamper.py::test_stale_external_head_fails_despite_later_publication` |
| 6 | High | External verification could fail open (sink exception with no publication marker gave `ok: true`; two code paths differed) | **Fixed** | A sink exception is `ok: false` regardless of markers; `anchor.verify` delegates to `AccessLedger.verify(sink=...)`; `verify` catches everything; `::test_sink_exception_fails_closed_even_without_a_publication`, `tests/test_audit_routes.py::test_verify_route_fails_closed_on_sink_error`. Residual: with no sink configured the match is `null` (limit above) |
| 7 | High | Reveal returned text even when its hash differed from the ledger digest; no request binding | **Fixed** | Text is released only if the stored row's `req_id` is the target and its SHA-256 equals the ledger's `answer_sha256`; otherwise 409 `{"error":"content does not match the sealed ledger digest"}`, no text, and an `audit.query` with `outcome: mismatch` after the write-ahead `audit.reveal`; `tests/test_audit_routes.py::test_reveal_refuses_content_that_does_not_match_the_ledger` |
| 8 | High | Each audit call re-verifies the whole ledger and appends (O(records)); repeated calls are a shared-ledger DoS | **Residual** | Bounded today only by `lock_timeout` (10 s, then 503 fail closed). Production: incremental verification from an in-memory checkpoint `(seq, hash)` verified once, so each call verifies only new records; per-officer rate limit and a concurrency cap on `/audit/*`; append cost mitigated by the same checkpoint inside `DecisionLedger` |
| 9 | Medium | Failed audit calls were not ledgered | **Fixed (handlers)** | `_refuse` ledgers `audit.query` with `outcome` `rejected`/`error`; query parameters now arrive as text so malformed dates/limits reach the handler; `::test_failed_audit_calls_are_ledgered`, `::test_rejection_on_a_broken_ledger_is_503`. Residual: framework-level rejections (above) |
| 10 | Medium | `/audit/verify` returns 200 without ledgering when the append fails | **Deliberate exception** | See "Deliberate exception" above |
| 11 | Medium | A failed projection update could persist as a partial record | **Fixed** | `apply`, `sync` and `rebuild` run in one transaction and roll back on any exception; `tests/test_audit_query.py::test_failed_apply_leaves_no_partial_record` |
| 12 | Medium | Concurrent subscriber callbacks could apply events out of ledger order | **Fixed (in process)** | `apply` ignores an event older than the newest applied; `sync` rebuilds in ledger order when a missing event is older than one it holds; `::test_out_of_order_apply_is_repaired_in_ledger_order` |
| 13 | Medium | Structured `/audit/events` filters were not closed or range-capped | **Fixed** | `AuditFilter.windowed`: no dates = last 365 days, until-only = 365 days before it, since-only = until now; both ends validated and capped; the response states the `window`; `::test_structured_window_is_always_closed_and_capped`, `tests/test_audit_routes.py::test_events_window_is_capped_and_stated` |
| 14 | Medium | Limits do not bound document expansion, reconstruction or accessor history | **Residual** | Each retrieval row's doc list is the post-Gate-1 top-K plus depth-1 links (`pipeline.py:146`), small by construction; accessors are now capped and disclose truncation (#22); reveal no longer calls `reconstruct`. Production: bound doc expansion and response bytes, paginate reconstruction and accessor history |
| 15 | Medium | `doc_events` keyed by `(req_id, doc_id)` loses distinctions if a request has several retrieval decisions | **Residual** | The pipeline writes one `retrieval.decided` per request (its single append, `pipeline.py:149`), but nothing in the ledger enforces it. Production: key document decisions by retrieval-event seq, or refuse a second `retrieval.decided` for a request before append |
| 16 | Medium | Invalid NL constraints silently broadened the query; an invalid model filter fell back to a broad one | **Fixed** | `NlFilterError` carries the unparsed constraint; the route answers 422 `{"error", "unparsed"}` and ledgers the rejection; a configured model that fails or does not validate is also 422; `tests/test_audit_nl_filter.py::test_invalid_constraint_is_not_dropped`, `::test_llm_output_is_validated_and_fails_closed`, `tests/test_audit_routes.py::test_ask_invalid_constraint_is_422_with_the_constraint`. Residual: a question in which the rules recognise nothing, with no model configured, runs the default 365-day query; the returned `filter`, `window` and `notes` show this |
| 17 | Medium | Impossible dates and huge periods raised uncaught exceptions (500) | **Fixed** | Dates are validated, periods are clamped before subtraction, reversed ranges are ordered before capping, and any parser exception is a ledgered 422; `::test_parser_errors_are_nl_filter_errors`, `::test_huge_and_reversed_periods_are_capped_not_crashing` |
| 18 | Medium | Calendar upper bounds were inclusive | **Fixed** | Every range is half-open, `since <= event_time < until`, in `nl_filter.parse_time` and `AuditProjection.search`; `::test_calendar_bounds_are_half_open`, `tests/test_audit_query.py::test_search_upper_bound_is_exclusive` |
| 19 | Medium | Publication can succeed remotely while the local `anchor.published` append fails | **Residual** | The sink being ahead of the log is tolerated by verification (#5 checks only the opposite direction). Production: stable publication ids (`log_id`+`seq`), idempotent remote writes, and startup reconciliation that records a receipt for any remote head the log does not mention |
| 20 | Medium | External-head validation was incomplete; "never raises" was not guaranteed | **Fixed** | Strict `log_id`, positive-int `seq` and 64-hex `head`; malformed publication markers are problems, not exceptions; a corrupt `FileSink` line fails verification instead of being skipped; `::test_malformed_or_foreign_external_head_fails`, `::test_corrupt_external_file_fails_closed` |
| 21 | Medium | Rollback after the last publication is undetectable and a later publication does not reveal it | **Limit stated** | Wording corrected here and in `audit/anchor.py`. Production: obtain an external receipt before an answer is released where zero loss is required, or enforce a maximum publication age (alert when the newest head is older than the interval) |
| 22 | Low | Accessor results did not disclose truncation | **Fixed** | `truncated` and `limit` in the response and in the ledgered query; `tests/test_audit_query.py::test_accessors_limit_clamped_and_truncation_disclosed` |
| 23 | Low | A negative accessor limit disabled the SQL limit | **Fixed** | Clamped to `[1, 500]`; same test |

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
