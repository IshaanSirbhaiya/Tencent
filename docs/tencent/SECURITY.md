# Security: threat model and controls

> Scope: the Internal Brain service (`databridge/apps/internal_brain/databridge_ib/`), the web proxy
> (`databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts`) and the mock platforms. `file:line`
> references are to commit `d69ab97`; paths are relative to `databridge/apps/internal_brain/databridge_ib/`
> unless they start with `tests/` (= `databridge/apps/internal_brain/tests/`) or `databridge/`. Anything not
> built is marked so.

## At a glance

Seven guarantees, each with one test or eval that backs it. Details and limits are in the sections below.

| # | Guarantee | Evidence |
|---|---|---|
| 1 | Restricted content never reaches the model. | `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt` |
| 2 | "Restricted" looks the same as "does not exist". The leak eval found 0 mismatches in 400 comparisons. | `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted`; `databridge/evals/internal_brain/RESULTS.md` |
| 3 | Identity comes only from the signed token. A body that names a user is rejected. | `tests/test_app_routes.py::test_identity_from_token_only` |
| 4 | Every answer is logged before it is returned. If the ledger is down, the answer is a 503. | `tests/test_app_routes.py::test_ledger_failure_is_503` |
| 5 | Edits, deletions and rollbacks of the audit log are detected, and so are edited answer texts. | `tests/test_audit_tamper.py::test_edit_detected`; `tests/test_audit_verify_content.py::test_edited_answer_is_detected_without_a_reveal` |
| 6 | A revocation in the source is honoured on the next question, even if the webhook was lost. | `tests/test_scenarios_e2e.py::test_s4_revocation_without_webhook` |
| 7 | Text planted in a document is treated as data. The final eval run had 0 injection hard failures in 976 injection-relevant requests. | `databridge/evals/internal_brain/RESULTS.md` (section "Injection") |
| 8 | The web proxy refuses cross-origin POSTs, non-JSON bodies and path tricks. | `databridge/apps/internal_brain_web/tests/e2e/proxy.spec.ts` |

Reviewed by Codex (tracks 1-7), a CodeBuddy security review, and two Claude review rounds (Opus, Sonnet,
Haiku). See sections 9-14.

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
- The logout revocation, fail-closed revocation list, `Secure` cookie and passcode came with commit `d69ab97`.
  They have **no dedicated regression tests yet**.
- MCP tokens for WorkBuddy are built (`databridge/scripts/ib_mint_token.py`, `tests/test_mint_token_cli.py`), and the
  MCP servers (`databridge_ib/mcp_server.py`, `databridge_ib/audit_mcp_server.py`) have "no identity argument"
  schema tests (`tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument`,
  `::test_audit_tools_have_no_identity_argument`).

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
| 16 | Medium | Invalid NL constraints silently broadened the query; an invalid model filter fell back to a broad one | **Fixed** | `NlFilterError` carries the unparsed constraint; the route answers 422 `{"error", "unparsed"}` and ledgers the rejection; a configured model that fails or does not validate is also 422; `tests/test_audit_nl_filter.py::test_invalid_constraint_is_not_dropped`, `::test_llm_output_is_validated_and_fails_closed`, `tests/test_audit_routes.py::test_ask_invalid_constraint_is_422_with_the_constraint`. Residual: a question in which the rules recognise nothing, with no model configured, runs the default 365-day query; the returned `filter`, `window` and `notes` show this. **Changed in v2:** the wired Hy3 parser falls back to the rules instead of a 422 (§15.8) |
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
| Audit NL filter | Output is a closed schema; any model sees only the question and the clock (the Hy3 parser: §15.8) | `tests/test_audit_nl_filter.py::test_injection_text_cannot_escape_the_schema`, `::test_model_receives_only_question_and_clock` |

**Injection fixtures on all four platforms** are planted in the synthetic world
(`databridge/fixtures/enterprise/generate.py`, facts below):

| Fact id | Platform | Attack |
|---|---|---|
| `INJ-confluence-title` | Confluence | Instruction-bearing title and an "ignore previous instructions" body |
| `INJ-jira-forged-citation` | Jira | A forged `[C9]` citation asserting a false security approval |
| `INJ-slack-base64` | Slack | A base64-encoded instruction to print a restricted report |
| `INJ-drive-audit-tools` | Drive | Text asking the assistant to call audit tools |

They are exercised by the canonical questions `INJ-q1` to `INJ-q4` (`tests/test_scenarios_e2e.py::test_canonical`)
and by the leak eval's injection probes. The first eval run (RESULTS.md, before the `8b5eb57`/`d69ab97` fixes)
recorded **0 leaks** and **6 injection hard failures**, all forged citations: a literal `[C9]` in a source bound
to a real passage tag (an integrity failure, not a disclosure). The tag neutralisation above is the fix;
the final run (`databridge/evals/internal_brain/RESULTS.md`, 976 injection-relevant requests) records
**0 injection hard failures**.

## 5. Side channels

| Channel | Control | Evidence | Residual |
|---|---|---|---|
| Existence via refusal wording | One constant refusal text, no counts, no reasons, constant `scope_note` | `models.py:21, 179-185`; `tests/test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical` | — |
| Existence via ranking statistics | BM25 statistics over the asker's permitted corpus only; anchor terms judged on permitted df only; Gate-2-denied docs removed from the ranking pool | `index.py:1-14, 311-321`; `pipeline.py:134-136`; `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted`; RESULTS.md: 0 of 400 differential comparisons mismatched | Eval figure predates `d69ab97` |
| Existence via linked titles or keys | Links resolve only within the readable set and then pass Gate 2 before anything about them is rendered | `pipeline.py:260-281` | A raw key written inside a permitted text is shown as written, never resolved (plan §4.6) |
| Per-query source contribution | `/ib/sources` reports per-platform sync status only (time since last successful sync) | `app.py:150-161` | It is global, not caller-scoped (Codex security review r1 finding 12) |
| Error envelopes | Any pipeline failure returns the same 503 body | `app.py:134-138` | — |
| Timing | **Not controlled** | — | stated limit |

## 6. Secrets handling

| Secret | Source | Control | Limit |
|---|---|---|---|
| `IB_TOKEN_KEY`, `IB_LEDGER_KEY` | Environment | With `IB_DEPLOYED=1` the app refuses to start on the dev defaults or on keys shorter than 32 characters (`config.py:48-56`). The ledger key is handed to `DecisionLedger` as bytes, never as a file inside the log directory (`audit/ledger.py:18-22`). | The dev defaults are in the public repo by design (`config.py:23-24`) |
| `MOCK_SVC_TOKEN`, `MOCK_ADMIN_TOKEN`, `MOCK_WEBHOOK_SECRET` | Environment; dev defaults `svc-dev`, `admin-dev`, `whsec-dev` | — | `assert_deployable` does **not** check these three (`config.py:39-41, 48-56`); a deployment must set them |
| `IB_DEMO_PASSCODE` | Environment | Constant-time compare (`app.py:97-99`) | Optional; unset means no passcode |
| TokenHub key | `TOKENHUB_API_KEY`, or `OPENAI_API_KEY` + `OPENAI_API_BASE` | Read from env only (`llm.py:28-45`); model errors are logged by exception type, not message (`llm.py:80`) | — |
| Real workspace ids (live track) | `.env` only (plan §5.3) | Planned pre-commit check | Live track not built |
| Git history | — | A pattern scan of the full public history (16 Oct) for API keys, cloud keys, GitHub and Slack tokens and private-key blocks: the only matches are planted fakes in the masking tests and `$VARIABLE` references in build logs; no real key | gitleaks itself was not run |
| Logs | — | `LedgerUnavailable` messages are public text, never a key or an OS exception string (`audit/ledger.py:60-68`); ingest and ask failures log the exception class only (`ingest.py:86`, `app.py:137`) | — |

## 7. Security review

A Codex security review of the gates, pipeline, ledger and identity code is recorded in
`docs/tencent/reviews/security-r1/codex_track1.md`: 35 findings (1 Critical, 10 High, 19 Medium, 5 Low). Commit
`d69ab97` changed the code for several of them (from its message and diff: read-through re-checks, denied docs
leaving ranking, relevance before top-k, readable-only link resolution, fail-closed and atomic revocation, logout
revocation, `Secure` cookie, demo passcode, enforced allowlist, webhook size cap and threadpool, time-phrase
stripping, `request.failed`, sanitised logs). A written disposition of all 35 findings, and regression tests for
those fixes, are **not in the repo yet**. Findings still open by our reading include 3 (Gate 2 not bound to a
content snapshot), 9 (membership changes do not refresh principals), 11 (remote embedder boundary), 12 (global
source status) and 15 (one valid citation accepts a model answer).

## 8. Not built (roadmap)

Real SSO; envelope-encrypted content and crypto-shredding (described only; plan review disposition); COS Object
Lock WORM for the ledger; monitoring of connector service-account use (TRM §9.2.2); rate limits and the Caddy
allowlist on the public edge (deploy kit in `deploy/lighthouse/`, not deployed); timing parity.

## 9. Codex security review r1 track 3 disposition

Track 3 covered the connectors and the masking module in two Codex passes: **3a** (Slack, Confluence,
`connectors/_http.py`; 13 findings) and **3b** (Jira, Drive, `sensitive.py`, `connectors/_http.py`;
21 findings). The rule applied throughout is **fail closed**: missing, malformed or incomplete source
evidence never grants (Gate 1 compiles to `nobody`) and never verifies (Gate 2 returns `forbidden`).
Paths below are relative to `databridge/apps/internal_brain/` unless they start with `databridge/`.
Tests are in `tests/` (internal brain) or `databridge/apps/mock_platforms/tests/`.

**Shared mechanisms**

- **`PageGuard`** (`databridge_ib/connectors/_http.py:95`). Every paged loop in every connector uses it.
  Hitting the 10,000-page cap with pages remaining, or seeing a cursor repeat, raises `ConnectorError` at
  ingest (the batch and its cursor are not committed) and `SourceFailure("source_incomplete")` at Gate 2
  (deny). A partial listing is never returned as complete.
- **`event_user`** (`_http.py:72`). A webhook's `user` field is resolved through
  `directory.by_platform_id` first. A value that looks like a native id for that platform but is not
  mapped stays unresolved (`None`). Any other value is accepted as an IB id only if the directory knows
  it. Unknown values are no longer passed through.
- **Webhook MAC.** The signature is now `HMAC-SHA256(secret, f"{timestamp}.{delivery_id}.{body}")`, on
  the receiver (`_http.py:204`, `WebhookVerifier.verify`) and on the sender (`databridge/apps/mock_platforms/mock_platforms/webhooks.py`, `sign`).

| # | Finding | Disposition | Where / regression test |
|---|---|---|---|
| 3a-1 | Slack public grant without proof of workspace membership | **Documented (residual).** Gate 1 grants a public channel only to directory `kind=member` users; the directory is the tenant identity source. Gate 2 now requires `deleted`, `is_restricted`, `is_ultra_restricted` and `is_stranger` to be present booleans for the user it asked about, and denies a guest or stranger who is not a channel member. There is **no `team_id` check**, because no workspace id is configured. | `connectors/slack.py` `check_access`; `test_connector_slack.py::test_gate2_missing_user_flag_denies`, `::test_gate2_users_info_about_someone_else_denies` |
| 3a-2 | Incomplete Slack responses default to "public" or "full member" | **Fixed.** A channel missing (or holding a non-boolean) `is_im`, `is_mpim`, `is_private` or `is_ext_shared` is `unclassified`: `nobody`, Gate 2 denies, and it is never crawled. A missing or non-boolean user flag, or a `users.info` / `conversations.info` answer about a different id, denies. | `slack.py:81` `_channel_kind`; `test_connector_slack.py::test_missing_classification_flag_is_unsupported_not_public`, `::test_non_boolean_classification_flag_is_unsupported`, `::test_gate2_missing_user_flag_denies`, `::test_gate2_empty_user_object_denies`, `::test_gate2_users_info_about_someone_else_denies` |
| 3a-3 | DM detection loses to the private/shared flags | **Fixed.** `is_im` / `is_mpim` are checked first, in fetch, ACL, crawl and Gate 2. | `slack.py:81`; `test_connector_slack.py::test_dm_flags_win_over_private_and_shared` |
| 3a-4 | Confluence ancestors read from the first page only | **Fixed.** Ancestors are paged to the end through `PageGuard`. A cap or cycle raises. | `connectors/confluence.py` `acl` / `_paged`; `test_connector_confluence.py::test_ancestors_are_paged_to_the_end`, `::test_ancestor_paging_cap_raises_not_partial` |
| 3a-5 | Unreadable restriction structures become "unrestricted" | **Fixed.** `restrictions.user.results` and `restrictions.group.results` must both be lists of well-formed entries of a known principal type, and must not be truncated (`size`, `totalSize` or `_links.next`). Anything else is `nobody`, as is a malformed ancestors payload, a non-`page` ancestor, or a malformed space-permission entry. Only an explicitly empty restriction means "unrestricted". | `confluence.py:258` `_parse_restriction`; `test_connector_confluence.py::test_malformed_or_unknown_restriction_is_nobody` (13 shapes), `::test_malformed_ancestors_are_nobody`, `::test_malformed_space_permission_entry_is_nobody`, `::test_explicitly_empty_restriction_is_unrestricted` |
| 3a-6 | Deleting an older Slack reply leaves the version unchanged | **Fixed.** The thread version is the string `"<latest ts>\|<message count>\|<digest>"`. The digest covers every message's ts, edit ts, author and text, so deleting or editing any message changes it and Gate 2 reports `drift_material`. The crawler also re-fetches when the parent's `reply_count` differs from the stored count. | `slack.py:237` `_version`; `test_connector_slack.py::test_deleting_a_middle_reply_changes_the_version`, `::test_editing_an_older_reply_changes_the_version` |
| 3a-7, 3b-3 | Native group ids assumed to share the directory's namespace (Confluence and Jira default to the identity mapping; Drive maps an email's local part) | **Documented.** The synthetic world uses one group namespace, so the defaults are correct for it. A real deployment must inject a tenant-scoped `group_resolver`. Drive's default only maps groups on the company domain (`drive.py:134`). | `confluence.py:115`, `jira.py:154`, `drive.py:134` |
| 3a-8, 3b-11 | The delivery id is outside the HMAC, so a delivery can be replayed under fresh ids | **Fixed** on both receiver and sender (see "Webhook MAC" above). | `test_connector_slack.py::test_webhook_delivery_id_is_inside_the_signature`; `test_connector_confluence.py::test_webhook_delivery_id_swap_rejected`; `mock_platforms/tests/test_mock_admin_webhooks.py::test_signature_binds_the_delivery_id`, `::test_sign_matches_contract_formula` |
| 3a-9, 3b-12 | Replay memory is per process and is lost on restart | **Documented (residual).** The replay set lives in one `WebhookVerifier`. A second worker, or a restart, can accept the same signed delivery once more inside the ±300 s window. Impact is bounded: events only invalidate, and the connector re-reads the source, so a replay costs work and never grants access. A shared atomic replay store is needed before running more than one worker. | `_http.py:219` |
| 3a-10, 3b-13 | Webhook identities mix native and IB namespaces | **Fixed** (`event_user`). Native-id formats: Slack `^[UWB][A-Z0-9]{2,}$`; Atlassian 24-hex, `<n>:<uuid>` or the synthetic `acc-…`; Drive any value containing `@`. | `test_connector_slack.py::test_event_user_resolves_native_first_and_never_guesses`; `test_connector_confluence.py`, `test_connector_jira.py` and `test_connector_drive.py` `::test_event_user_native_first_and_unresolved_stays_none` |
| 3a-11, 3b-14 | Identity-mapping changes do not change ACL fingerprints | **Documented (residual).** The poller runs a full reconciliation on every 10th tick (`freshness/poller.py:15-36`, `full_every=10`). A full sync calls `list_changes(None)`, which re-emits every document, and recompiles each ACL with `conn.acl()` against the current directory (`ingest.py:52-70`). Mapping changes therefore land within at most N polls. Gate 2 resolves identity from the current principal on every answer. | `freshness/poller.py`, `ingest.py` |
| 3a-12, 3b-6 | Pagination limits silently accept incomplete evidence | **Fixed** in every connector (`PageGuard`). | `test_connector_slack.py::test_pagination_cap_denies_gate2_and_fails_ingest`, `::test_repeating_cursor_is_rejected`; `test_connector_confluence.py::test_ancestor_paging_cap_raises_not_partial`; `test_connector_jira.py::test_search_pagination_cap_raises`, `::test_search_token_cycle_raises`; `test_connector_drive.py::test_permission_listing_cycle_or_cap_is_never_partial` |
| 3a-13 | Confluence Gate 2 accepts a missing status or version | **Fixed.** Gate 2 requires the page id it asked about, a non-empty `status` equal to `current`, and an integer `version.number` of at least 1. Otherwise it returns `forbidden` (`source_bad_payload` or `source_ambiguous`). A missing status is no longer treated as `current` in fetch, ACL or crawl. | `confluence.py` `check_access`; `test_connector_confluence.py::test_gate2_incomplete_status_or_version_denies` (10 shapes × 3 `known_version` values), `::test_missing_status_is_not_current_for_fetch_and_acl` |
| 3b-1 | Restricted Jira comments inherit the issue's broader ACL | **Fixed (unsupported subset, dropped).** A comment with any `visibility` is not indexed. Restricted comments are therefore never answerable. | `jira.py:244`; `test_connector_jira.py::test_restricted_comment_is_never_indexed` |
| 3b-2 | Drive Gate 2 can authorise revoked group members | **Fixed, plus a documented residual.** Gate 2 evaluates the freshly read permissions against `directory.get(user.id)` at check time, not the principal object the request started with. It denies a user the directory no longer knows, no longer lists as active, or now maps to a different Drive identity. **Residual:** the directory is a cache of the identity source, so a Google-group removal reaches Gate 2 only when the directory reloads. | `drive.py:404`; `test_connector_drive.py::test_gate2_uses_the_directorys_current_group_membership`, `::test_gate2_deactivated_or_remapped_in_the_directory_denies` |
| 3b-4 | A missing Jira `security` field becomes "unrestricted" | **Fixed.** Only an explicit `"security": null` means "no level". The field is always requested (`LIST_FIELDS` / `*all`), so an absent or malformed value (no id, a string, a boolean id, a list) is `SEC_UNREADABLE` → `nobody("jira-security-field-unreadable")`. That value is also fingerprinted in the crawl cursor. | `jira.py:395` `_level_of`; `test_connector_jira.py::test_absent_or_malformed_security_field_is_nobody`, `::test_explicit_null_security_is_browse_only`, `::test_crawl_with_absent_security_field_fingerprints_and_fails_closed` |
| 3b-5 | Malformed Drive permission entries are dropped silently | **Fixed.** Every entry is kept. A non-object entry, a user or group grant without an email, a domain grant without a domain, or a non-boolean `deleted` makes the **whole file** `nobody`. | `drive.py:311` `compile_acl`; `test_connector_drive.py::test_malformed_permission_entry_makes_the_whole_file_nobody` (9 shapes) |
| 3b-7 | In adjacent card numbers, only the first is masked | **Fixed.** `_runs` scans left to right and masks every accepted, non-overlapping run within a candidate. | `sensitive.py:323`; `test_sensitive.py::test_adjacent_cards_are_all_masked_in_one_pass` |
| 3b-8 | NBSP, wrappers or adjacent labels hide a PAN | **Fixed.** Matching runs on a same-length normalised view: Unicode spaces, tabs, newlines and zero-width characters become a space; Unicode dashes become `-`; full-width digits become ASCII. The PAN boundary is now "not next to another digit", so `card_…`, `-…`, `ORD-…`, quotes and brackets no longer hide a card. Findings keep their offsets into the original text. | `sensitive.py:88` `normalise`; `test_sensitive.py::test_unicode_spacing_and_wrappers_do_not_hide_a_pan` (12 shapes), `::test_findings_offsets_survive_normalisation` |
| 3b-9 | Cubic `_best_run` | **Fixed.** Windows are bounded by `max_len` (at most 256), so the scan is O(n · max_len²) per candidate. Claimed spans are tracked in a bytearray. | `sensitive.py:323`; `test_sensitive.py::test_masking_work_is_linear_not_cubic` |
| 3b-10 | A config can disable masking or keep the whole secret | **Fixed.** Missing or empty `rules` falls back to the built-ins (never zero rules). A built-in kind the file omits is appended from the defaults. A rule is rejected unless `keep_head + keep_tail` is less than an explicit `min_len`; negative keeps, an empty fill, a bad capture group and out-of-range lengths are also rejected. | `sensitive.py:292` `load_rules`, `compile_rules`; `test_sensitive.py::test_config_without_rules_uses_the_builtins`, `::test_config_omitting_a_builtin_kind_gets_it_back`, `::test_unsafe_rules_are_rejected` |
| 3b-15 | A failed folder expansion turns into a bogus `drive:<folder>` id | **Fixed.** A failed expansion adds no doc id. The event invalidates the whole container instead: the drive named in the signed payload, else the folder's drive from the last crawl's tree, else every known drive. If none is known, `parse_webhook` raises, and the next changes-feed poll repairs the state. **Residual:** descendants that moved away before the webhook are found by the crawler's old-and-new-tree diff, not by the webhook path. | `drive.py:607` `_expand_acl_ids`, `drive.py:642` `_containers_for`; `test_connector_drive.py::test_webhook_expansion_failure_invalidates_the_container`, `::test_expansion_failure_without_payload_container_uses_the_known_tree`, `::test_expansion_failure_with_nothing_known_raises` |
| 3b-16 | Maestro ranges (50, 56–59) are not masked | **Fixed.** The IIN prefix is now `5[0-9]`; length and Luhn still apply. | `sensitive.py:63`, `databridge/packs/enterprise/sensitive.yaml`; `test_sensitive.py::test_maestro_ranges_are_masked`, `::test_maestro_prefix_still_needs_luhn_and_length` |
| 3b-17 | Lowercase or NBSP-formatted IBAN and NRIC escape detection | **Fixed.** The IBAN and NRIC rules are `(?i)` and run on the normalised view. | `test_sensitive.py::test_lowercase_and_nbsp_iban_and_nric_are_masked` |
| 3b-18 | A configured regex can backtrack catastrophically | **Fixed (restricted language).** A pattern or prefix may be at most 512 characters, and no repetition may contain another repetition with max > 1 or an alternation. The check runs on the parsed regex at load. **Residual:** this is a structural heuristic, with no runtime timeout on `re`. | `sensitive.py:213` `_check_regex`; `test_sensitive.py::test_unsafe_rules_are_rejected` |
| 3b-19 | `mask_doc` leaves other string fields unmasked | **Fixed.** Every string field of `SourceDoc` is masked except the identifiers in `MASK_EXEMPT_FIELDS`: `doc_id`, `platform`, `kind`, `container`, `version`, `deep_link`, `author` (and the `links` tuple). | `sensitive.py:393-397`; `test_sensitive.py::test_mask_doc_masks_every_free_text_field_but_not_identifiers` |
| 3b-20 | Gate 2 has no hard overall deadline | **Documented.** The answer path enforces one monotonic deadline for the whole Gate 2 batch (`gates.py:40`, `pipeline.py:80` `gate2_timeout_s=2.0`): a check still running at the deadline is a deny (`timeout`). Each connector call also has its own 2 s transport timeout. **Residual:** a timed-out check's worker thread keeps running in the pool until its own calls end. | `gates.py:28-49` |
| 3b-21 | Weak semantic validation causes false positives | **Documented (deliberately conservative).** NRIC is matched on shape only, IBAN on mod-97 without a country-length table, and PAN on IIN + length + Luhn. Over-masking is the preferred error. The masked output of the generated world is unchanged by this track: a before/after diff over every string in `databridge/fixtures/enterprise/world.json` was identical. | `test_sensitive.py::test_no_false_positives`, `::test_world_sens_facts_mask_to_their_recorded_form` |

Two behaviour changes follow from 3b-8 and 3b-17. `ORD-4111111111111111` (a Luhn-valid card number
behind an id prefix) is now masked, and so is a lowercase NRIC such as `s1234567d`. The negative cases in
`test_sensitive.py::test_no_false_positives` were updated to match.

## 10. Reviews of the WorkBuddy connectors: Codex track 4 and CodeBuddy (CB5)

Two more independent reviews ran on 14 Oct: **Codex track 4** on the MCP connectors, the token CLI and the
setup scripts (`reviews/security-r1/codex_track4_mcp_tokens.md`, 10 findings) and **CodeBuddy on Tencent
Hunyuan `hy3`** on the app, auth, pipeline, gates and audit (`reviews/codebuddy_security_review.md`,
7 findings). CodeBuddy fixed its own connector code in session CB6; the rest was fixed by Claude. Each
fix has a regression test; tests are in `databridge/apps/internal_brain/tests/`.

| # | Finding | Disposition | Where / regression test |
|---|---|---|---|
| T4-1 | Token CLI prints a usable token | **Fixed (CB6).** Prints only with `--print`; `--out FILE` writes an owner-only file; setup imports `mint_for` so no token passes through stdout. | `databridge/scripts/ib_mint_token.py`; `test_mint_token_cli.py` |
| T4-2 | Token files can land inside the repo, readable by others | **Fixed.** `workbuddy_setup.py` refuses an output folder inside the repository and restricts each `mcp.json` to the current user (icacls / chmod 600). | `databridge/scripts/workbuddy_setup.py` |
| T4-3 | Non-JSON or malformed responses crash a tool | **Fixed (CB6).** Shape-validated, fixed sanitised error; a bad `whoami` keeps the answer. | `mcp_server.py`, `audit_mcp_server.py`; `test_mcp_server.py` |
| T4-4 | `doc_id` can redirect the audit request (`../verify#`) | **Fixed (CB6).** Percent-encoded single path segment; `.`/`..`/empty rejected. | `audit_mcp_server.py`; `test_mcp_server.py` |
| T4-5 | Huge `since_days` crashes | **Fixed (CB6).** Accepted only in 1..3650. | `test_mcp_server.py` |
| T4-6 | Errors echo URLs and queries | **Fixed (CB6).** Errors carry only the operation and status; credentials/query/fragment in `IB_URL` rejected. | `test_mcp_server.py` |
| T4-7 | Bearer token over plain HTTP to a remote host | **Fixed (CB6).** Plain HTTP allowed only to loopback. | `test_mcp_server.py` |
| T4-8 | Concurrent processes lose revocations | **Fixed.** Cross-process lock file + unique temp file around the read-modify-replace. | `databridge_ib/auth.py` `revoke`; `test_revoke_concurrency.py` |
| T4-9 | Tests bypassed the real HTTP helpers; no audit authorisation tests | **Fixed (CB6).** Tests patch only the client factory; employee-forbidden and officer-allowed on every audit tool; the officer stays the acting subject when filtering another user. | `test_mcp_server.py` |
| T4-10 | Launcher's `PYTHONPATH` separator was Windows-only | **Fixed.** Uses `os.pathsep`. | `databridge/scripts/run_local_demo.sh` |
| CB5-1 | Demo sign-in without a passcode lets anyone pick any persona | **Fixed for deployments.** A deployed build refuses to start with demo sign-in on and no passcode of 8+ characters. Locally it stays open by design (synthetic data, one user). | `databridge_ib/config.py` `assert_deployable`; `test_config_deployable.py` |
| CB5-2 | A holder of the ledger key can rewrite records after the last published head | **Documented (residual),** as in §3: detection beyond the last off-host head needs the key kept away from the app (KMS/HSM) and a shorter anchor interval. | §3 |
| CB5-3 | The answer store sits outside the sealed chain; `verify` didn't check it | **Fixed.** `/audit/verify` re-hashes every stored answer against the digest sealed in `answer.released` and fails on any mismatch or missing answer, without needing a reveal. | `databridge_ib/audit/routes.py` `_verify_content`; `test_audit_verify_content.py` |
| CB5-4 | Webhook route relies on the signature alone; replay cache is per process; no rate limit | **Documented.** The signature (with delivery id and ±300 s window) is the control by design; the demo runs one Brain process; stock Caddy has no rate limiter. Events only trigger a re-fetch through both gates, so a replay costs work, not access. | `connectors/_http.py` `WebhookVerifier` |
| CB5-5 | A browser user could label their question as coming from WorkBuddy | **Fixed.** The `workbuddy-mcp` label is honoured only on bearer-token calls. | `databridge_ib/app.py` `ask`; `test_channel_label.py` |
| CB5-6 | `/ib/sources` shows platform freshness to any employee | **Accepted.** Operational metadata only; it is what makes the freshness promise checkable. | |
| CB5-7 | Session cookie is `Secure` only when deployed | **Accepted.** Deployed builds set `Secure`; local runs use plain `http://localhost`. | `app.py` `demo_login` |

**Found while wiring the connector, not by a reviewer:** every `/audit/*` call through the real app answered
401, because the routes read the user's id from `sub` while the app's user object carries `id`. Fixed, with
a test through the real app (`test_audit_routes_real_principal.py`).


## 11. Codex track 5: the knowledge map and audit content check

`reviews/security-r1/codex_track5_map_audit.md` (15 Oct, 9 findings + 1 out-of-bundle note).

| # | Finding | Disposition | Where / regression test |
|---|---|---|---|
| 1 | Map listed items from synced permissions; a revocation with a lost webhook showed until the next poll | **Fixed.** Every map item is now confirmed live with its source (Gate 2, one batch budget, error/timeout = left out) before it is listed or counted. | `databridge_ib/knowledge_map.py`; `test_knowledge_map.py::test_revocation_with_a_lost_webhook_is_honoured_by_the_map` |
| 2 | `/audit/verify` returned answer references even when its own audit append failed | **Fixed.** The content report carries counts only, never references. The documented write-ahead exception for verify (§3) now discloses only integrity counts and digests. | `audit/routes.py` `_verify_content`; `test_audit_verify_content.py` (asserts no reference appears) |
| 3 | A release without a reference was silently skipped | **Fixed.** Counted as an unreadable record (a problem). | same |
| 4 | Chain check and content check are separate passes | **Documented.** Both run in one request against an append-only log; a release appended between the two passes is checked by the content pass and reported next time by the chain pass. | |
| 5 | Problem list not bounded | **Fixed.** Three counters; the report is constant-size. | same |
| 6 | A corrupt stored record could crash verify | **Fixed.** Any error reading one record counts as a problem; only an unavailable store stops the check (reported, not raised). | same |
| 7 | MCP clients followed redirects | **Fixed.** Redirects are not followed; a 3xx is a sanitised error. | `mcp_server.py`, `audit_mcp_server.py` |
| 8 | Map audit event conflated counted and returned items | **Fixed.** `map.viewed` records `docs_listed` and `items_returned`. | `test_knowledge_map.py::test_map_event_records_items_actually_returned` |
| 9 | Test gaps | **Partly fixed** with the tests above; an ASGI send-order probe and large-ledger load tests are not written. | |
| 10 | Channel rule, revocation lock and passcode guard were outside the bundle | Covered by §10 (T4-8, CB5-1, CB5-5) and their tests. | |


## 12. Final review round (15 Oct): Opus security, Sonnet accuracy, Haiku first-run, Sonnet UX

| # | Finding | Disposition |
|---|---|---|
| O-1 | The original-screens app offered nine people but showed jdoe's graph to all of them | **Fixed.** Its sign-in offers only Jordan and says the graph is Jordan's view at build time; the web server binds to 127.0.0.1. Rebuild `--as <persona>` for another view. |
| O-2 | The eval command exited 0 even with leaks | **Fixed.** It exits 1 on any leak, violation, P2 mismatch or invalid canary. |
| O-3 | Logout could report success when the revocation list was unavailable | **Fixed.** 503, the session is not reported as ended. |
| O-4 | Compare's second-person sign-in ignored the demo passcode | **Fixed** in the web app (the passcode entered at sign-in is reused for the comparison slots). |
| O-5 | A deployed build accepted the mock platforms' public dev secrets and a shared signing key | **Fixed.** `assert_deployable` refuses both (`test_config_deployable.py`). |
| O-6 | Map and citations show a shared drive's name to someone given one file directly | **Accepted.** The drive name is what the platform shows that person too. |
| O-7 | The map's check pool is shared, so a heavy map can make another person's map leave items out | **Accepted.** Fail-closed (never shows extra); the pool exists so answers are never starved. |
| O-8 | Sentences removed as addressed-to-an-AI were recorded as 0; sign-ins were not in the ledger | **Fixed.** The exact count is recorded; demo sign-in writes `auth.login` before the session is issued (ledger down = no sign-in). |
| O-9 | Breaking a stale revocation lock can race | **Accepted, low.** Needs a 30 s stale lock plus two simultaneous waiters; the worst case is one lost revocation write, and the next logout retries. |
| O-10 | A Windows user name and a personal email in tracked files | **Fixed** in the current files. Older commits can still show them; neither is a credential. |

Accuracy and first-run findings (stale counts, Hy3 wording, the tamper "8/8" claim, setup steps, macOS
install) were fixed in the documents themselves.


## 13. Codex track 6: the web app (proxy, compare slots, map, tour)

Review: [codex_track6_web.md](reviews/security-r1/codex_track6_web.md). All ten findings fixed in the web
app; no backend change. Tests: `tests/e2e/proxy.spec.ts` (new) and `tests/e2e/compare.spec.ts`.

| # | Finding | Disposition |
|---|---|---|
| 1 (high) | Two Compare tabs share the slot cookies, so one tab could show another person's answer under the wrong name | **Fixed.** Each slot's sign-in → ask → map runs under a cross-tab Web Lock, and every answer and map is checked against the person just signed in; a mismatch shows the normal error, not the data. |
| 2 | `%2e%2e` path segments slipped past the allowlist | **Fixed.** Empty, `.` and `..` segments are refused (400), document ids have a strict format, and the final outbound URL is re-checked against the allowlist. |
| 3 | No CSRF check on POSTs | **Fixed.** A POST from another origin or site is refused (403); request bodies must be JSON (415). |
| 4 | Source links accepted any URL scheme | **Fixed.** Links render as links only when they are http(s); anything else is plain text (map and citations). |
| 5 | Tour sign-ins could overlap and leave the wrong person signed in | **Fixed.** Tour sign-ins run one at a time; steps cannot move, and closing waits, while one is in flight. |
| 6 | Unbounded request body; upstream body errors escaped the handler | **Fixed.** Bodies are capped at 64 KiB; the 90 s deadline covers the whole exchange; the browser has a 95 s timeout. |
| 7 | Any upstream cookie was relayed, with its own Domain/Path | **Fixed.** Only the IB session cookie is relayed, scoped to `Path=/`, no Domain, always HttpOnly. |
| 8 | Upstream 5xx bodies reached the browser verbatim | **Fixed.** Replaced by a fixed message; only the IB's own short 503 message (e.g. "sign-out could not be recorded") passes through. |
| 9 (low) | An unknown `x-ib-slot` value fell back to the main session | **Fixed.** 400. |
| 10 (low) | Tour persona changes ignored the demo passcode | **Fixed.** They send the passcode remembered at sign-in. |


## 14. Final review round 2 (15 Oct): Codex fix verification, Opus security, Sonnet accuracy, Haiku first-run, Sonnet judging

Codex re-checked the §13 fixes ([codex_track7_fix_verification.md](reviews/security-r1/codex_track7_fix_verification.md)).
Opus re-read the backend for side channels a contractor's token could use and found none.

| # | Finding | Disposition |
|---|---|---|
| C7-11 / Opus-4 | Compare ran without its cross-tab lock where the Web Locks API is missing | **Fixed.** Compare refuses to run without the lock (it needs https or localhost) and shows its normal error. |
| C7-12 / Opus-6 | The cross-site check compared hosts only, and trusted `X-Forwarded-Host` | **Fixed.** The full `Origin` must equal this site's own origin (its scheme plus the `Host` the browser used), or `IB_PUBLIC_ORIGIN` behind a TLS proxy (set by the Lighthouse compose file). Forwarded headers are not used. |
| C7-13 | Any one-field `{"error": …}` 503 body passed through, with its content type | **Fixed.** Only the IB's two known 503 messages pass, re-sent as fresh JSON; everything else gets the fixed message. |
| C7-14 | The `Partitioned` cookie attribute was dropped | **Fixed.** Kept (the IB does not set it today). |
| C7-15 | No browser timeout where `AbortSignal.timeout` is missing | **Fixed.** A manual 95 s abort timer. |
| C7-16 | Duplicate slot names could self-deadlock the lock helper | **Fixed.** Slots are de-duplicated before locking. |
| C7-6 (mock) | The mock branch has no deadline | **Accepted.** Mock mode serves in-process fixtures only. |
| Opus-1 | `--user-config` puts three identities in one WorkBuddy connector list | **Accepted, documented.** It exists to show two people side by side in one chat; each connector only ever answers as its own person (the IB enforces the token). The script says so and tokens expire within 8 h. |
| Opus-2/3 | Token files written before being restricted; config rewritten in place | **Fixed.** Written to a private temporary file, restricted, then swapped in; a failed restriction is reported; a malformed config is refused. |
| Opus-5 | `/ib/history` and `/ib/sources` skipped the employee-role check | **Fixed.** Same check as ask and map. |

The accuracy, first-run and judging reviews led to document fixes: latency (19–20 ms), tamper count
(6 of 6 attacks; the 7th is the stated window), the WorkBuddy audit tool names, the architecture status
lines, the CDSA edition note, what is real and simulated, how to tell when the stack is ready, and the
"honest limit" and break-even paragraphs in SUBMISSION.md.


## 15. Developer views and the finals v2 additions (30 Oct)

The finals plan (docs/finals/PLAN.md §9) added views for engineers and compliance, and four backend
items (B11 to B14). Contracts: `docs/finals/contracts/backend_v1.md` (v1 and the v2 section). Paths
are relative to `databridge/apps/internal_brain/databridge_ib/`; tests are in `tests/`. These
sections cite functions and tests, not line numbers (the `d69ab97` line references above predate them).

### 15.1 One authorisation rule for every read outside the ask

`/ib/graph`, `/ib/graph/openable`, `/ib/doc/{id}` and the citations on `/ib/trace/{req}` all go through
`authz.authorize`, a copy of the ask pipeline's rule: Gate 1 (stored source permissions), a live Gate 2
with the indexed version as the known version, read-through on drift (re-fetch, re-compile the ACL,
mask, re-index; admitted only if the fresh ACL still admits the person and the fetched version is the
one Gate 2 checked), then an immutable snapshot of whitelisted fields. Any error, timeout or 429 is a
deny. The views use their own small worker pool (`authz.VIEW_POOL`, 6 workers, 20 s per call), so a
heavy graph cannot starve an answer's Gate 2. `/ib/map` uses the same `authz.authorize` (its own
8-worker pool, 45 s): what it lists comes from the version-bound snapshot, not the index row
(`test_final_review_fixes.py::test_map_lists_the_checked_version_and_honours_a_drifted_acl`).

**One deadline (finals Codex review #4).** A single monotonic deadline per call bounds the live
checks AND the read-through: drifted documents are re-fetched on a separate 4-worker pool
(`authz.READ_POOL`) and waited for only until the deadline. A check or read-through not finished by
then is a deny, never unchecked content, and is reported as undecided so no count built on it is
called exact (`::test_read_through_is_bounded_by_the_same_deadline`,
`::test_checks_past_the_deadline_are_denied_and_undecided`). Connector calls still running at the
deadline finish in the background (each is bounded by the connector's own transport timeout).

**The graph is built from the authorised version (finals Codex review #1).** Document-to-document
edges come from each snapshot's CURRENT links, resolved only inside the set of documents confirmed in
the call; the index's older links only choose what to check. A link removed in the source's newer
version is not drawn, and a target the newer version adds is checked live in the same call
(`::test_graph_edge_removed_in_the_current_version_is_not_shown`,
`::test_graph_edge_added_in_the_current_version_is_checked_and_shown`).

**Caps after the live check (finals Codex review #3, Opus #8).** Candidates are checked in bounded
batches (`graph.CHECK_BATCH`, `docview.MAX_LINKS`) until the list is exhausted, the work cap
(`MAX_CHECKS_*`, `docview.MAX_LINK_CHECKS` = 240) is reached or the deadline expires; the public cap
(`limit`, 12 links) applies to confirmed documents only, so a live-denied (stale) document never takes
a visible slot. Graph search re-runs the search without the live-denied hits (up to
`graph.SEARCH_ROUNDS` = 4), as the ask pipeline does. Within the budget the response equals the
response of a world without the stale documents (`::test_doc_links_skip_live_denied_targets_d1_to_d12_plus_b`,
`::test_graph_focus_checks_past_denied_neighbours_within_budget`,
`::test_graph_search_rechecks_past_live_denied_hits`). When the budget runs out first, the graph says
`complete: false` and gives `null` for every count that depended on unchecked candidates, and the
document view omits its link list (`links: []`, `links_complete: false`) rather than showing a partial
list as the whole neighbourhood (`::test_graph_focus_past_the_work_cap_omits_counts`,
`::test_doc_links_are_omitted_when_the_budget_cannot_settle_them`). **Limit:** that
`complete: false` / `links_complete: false` signal itself can differ between a world with more than a
work cap of stale documents and one without them; it says only "the budget ran out", never which
documents or how many.

| View | Who | What it can never show | Evidence |
|---|---|---|---|
| `GET /ib/graph` | any employee | a document that failed Gate 2 in this call, by node, title, count or edge; a "hidden neighbours" number unless it is exact | `test_graph_doc.py::test_graph_is_identical_with_and_without_restricted_docs`, `::test_graph_hidden_counts_are_exact_or_absent`, `::test_graph_gate2_failure_and_revocation_drop_docs` |
| `POST /ib/graph/openable` | any employee | anything about ids the caller cannot open (they are simply absent, like unknown ids) | `::test_openable_returns_only_what_the_caller_can_open` |
| `GET /ib/doc/{id}` | any employee | ACLs, raw link ids, the full text; whether a refused id exists (restricted, unknown and a failed check are the same 404) | `::test_doc_restricted_and_unknown_are_the_same_404`, `::test_doc_is_identical_with_and_without_restricted_docs` |

"View as" (D-17) is two independent graphs, each drawn from its own signed-in session; there is no
union, diff or greyed placeholder for a document the other person cannot open.

Write-ahead: `graph.viewed` (counts only) before the graph is returned; `doc.viewed` BEFORE the
access decision, so a ledger outage is the same 503 for a permitted, a restricted and an unknown id
(`::test_graph_ledger_down_is_503`, `::test_doc_ledger_down_is_the_same_503_for_every_id`).

**Limits.** The graph overview checks every readable document live (capped at 1,500 checks,
`graph.MAX_CHECKS_OVERVIEW`); it costs about 3.4 s for jdoe on the synthetic world and is not rate
limited. `doc.viewed` records the id as requested (up to 300 characters), so the ledger holds the ids
people tried to open, including ones they could not.

### 15.2 The trace receipt (`GET /ib/trace/{req_id}`) is public-safe

The asker's receipt for one of their OWN requests (decision D-16). Another person's request and an
unknown id give the same 404. It shows stage names with `ok`/`skipped` (derived from which sealed
records exist, never from timings), the total latency rounded to 100 ms, the model, per-request record
ordinals and hashes (no global ledger sequence number) and only the cited documents the person can
STILL open now (Gate 1 + live Gate 2; the rest are left out with no count). It never shows the
considered or released sets, Gate 2 verdicts or reason codes. Ledgered as `trace.viewed`; a
reconstruction or authorisation failure is still recorded, best effort, as `trace.viewed`
`outcome: "error"` (counts only, no id or exception text) before the 503
(`test_final_review_fixes.py::test_trace_reconstruction_failure_is_ledgered_then_503`,
`::test_trace_authorisation_failure_is_ledgered_then_503`). The full projection catch-up (which
verifies the whole ledger, O(records)) runs at most every 15 s (`app.TRACE_SYNC_TTL_S`), so an
employee cannot make the service verify the ledger on every call; in between, the projection follows
this process's own appends, so a just-made request is visible at once
(`::test_trace_projection_catch_up_is_debounced`). The same applies to `/audit/events`,
`/audit/doc/{id}/accessors` and `/audit/ask`, which now catch up through `_sync_or_refuse`: a
projection failure there is a ledgered 500 with a fixed body
(`test_audit_routes.py::test_projection_failure_is_a_ledgered_500_on_every_query_route`).
Tests: `test_dev_views.py::test_trace_is_own_requests_only_and_hides_gate2_reasons`,
`test_graph_doc.py::test_trace_lists_only_cited_docs_still_openable`,
`::test_trace_restricted_question_equals_nonexistent_question`, `::test_trace_ledger_down_is_503`.

### 15.3 Journey and stats are compliance-only; stage timings live there

`GET /audit/journey/{req_id}` (one request end to end: considered and released documents, Gate 2
verdicts and reason codes, the model, the sealed records, the per-stage millisecond timings; never the
answer text) and `GET /audit/stats` (counts, timings and distributions) need `compliance_officer`,
like every `/audit/*` route, and every call is ledgered, including failures (a ledgered 500, or 503
when the ledger cannot record it). The stats window is half-open (`now - days <= event_time < now`),
and a cached result is only served for the verified ledger head it was computed on.
Tests: `test_dev_views.py::test_journey_is_compliance_only_and_has_no_answer_text`,
`::test_stats_needs_compliance_and_is_ledgered`, `test_dev_views_hardening.py` (all six).

**Stage timings.** `answer.released` carries `stage_ms` (accept, gate1, gate2, seal, compose, release),
measured with `time.perf_counter` and ending before the final append
(`test_dev_views.py::test_answer_release_records_stage_timings`). The millisecond waterfall is shown
only in the compliance journey and stats. Two honest notes: the gate2 time grows with the number of
documents checked live, so it says something about how many candidates passed Gate 1 (the asker's own
readable set, plus documents revoked since the last sync); and the asker can already time their own
request end to end (§5, "Timing": not controlled). §15.6 adds per-stage timing for the asker.

### 15.4 `GET /ib/status`

Any signed-in employee (not on the public `/health`). It returns model ids, the provider label,
booleans (`key_configured`, `force_offline`, `active`) and `ledger.ok`. Never a key, a base URL or a
ledger sequence number. `ledger.ok` is re-verified at most every 15 s, so an employee cannot make the
service verify the whole ledger on every call.
Test: `test_graph_doc.py::test_status_is_authenticated_and_never_returns_a_secret`.

**Limit.** It tells every employee whether the ledger currently verifies. We accept this: it is the
same fact the service acts on (a broken ledger is a 503 for everyone).

### 15.5 The replay switch (seeded demo history)

`X-IB-Replay-Key` lets the seeder record past-dated, replay-labelled requests (decision D-10). It is
honoured only when ALL hold, otherwise the call is a 403 and never silently recorded as live: the
server has `IB_REPLAY_KEY` of at least 24 characters, it is not deployed (`IB_DEPLOYED` unset), the
call uses a bearer token and comes from loopback, the key matches (constant-time), and
`X-IB-Replay-At` is within the last 400 days and not in the future (otherwise 422). Seeded requests
are `channel: "replay"`, `synthetic_replay: true` in the ledger and in the UI; a failure keeps the
label and the event time. The same switch applies to `/ib/ask/stream`.
Tests: `test_graph_doc.py::test_replay_switch_labels_the_request`,
`::test_replay_switch_refuses_every_unsafe_call`, `::test_deployed_config_refuses_a_replay_key`,
`::test_request_failed_keeps_the_replay_label_and_time`.

**Limit.** On a local run with the key set, anyone on the machine who has the key and a valid token
can write backdated (but labelled) history. `recorded_at` always holds the real append time.

### 15.6 SSE stage events (`POST /ib/ask/stream`, B12, D-20)

The same pipeline call as `/ib/ask` with a progress callback (`Pipeline.ask(on_stage=...)`). Stage
events carry only a name from a fixed list (`pipeline.STAGES`) and `ok: true`: no counts, ids, titles
or reasons. Every successful request sends all seven names in the same order whatever the outcome,
so a restricted question and a made-up one give identical event sequences. The answer text is sent
once, after `released` (D-08), and equals the `/ib/ask` JSON. Any failure, including the ledger, ends
with one `error` event and no answer. A callback error is ignored and cannot change what is retrieved,
ledgered or returned. The ledger records are the same three as `/ib/ask`.
Tests: `test_ask_stream.py` (event order, payloads, no answer before release, equal to `/ib/ask`,
restricted vs made-up, ledger down, failure at release, auth and roles, `on_stage` changes nothing).

**Gate events are held (finals Codex review #2).** The interval between `gate1` and `gate2` is the
live permission checks, and its length differs between a document revoked at the source but still
in the index (it is checked, then denied) and a document that does not exist (nothing to check). So
the stream does not send those boundaries in real time: `accepted` goes at once; `gate1` and `gate2`
are held and sent back to back only when the retrieval decision is sealed, immediately followed by
`sealed` (`app.HELD_STAGES`). `composing`, `grounded` and `released` follow as they happen, after all
permission work. The event order and payloads are unchanged. If the request fails before sealing, the
held events are dropped and the stream ends with `error`.
Tests: `test_final_review_fixes.py::test_gate1_and_gate2_are_held_until_the_checks_are_sealed`
(a connector delayed 0.4 s: only `accepted` has been handed to the stream while the checks run;
`gate1`, `gate2`, `sealed` follow within 0.15 s of each other after them),
`::test_held_stages_are_dropped_when_the_request_fails_before_sealing`.

**Limits.** The time from `accepted` to the `gate1`/`gate2`/`sealed` burst still covers retrieval and
the live checks. For the denied-versus-absent case that time is no finer than the total response time
`/ib/ask` already gives the asker (neither case calls the model: both are refusals), so the stream adds
no timing the plain endpoint lacks; it does not make the two totals equal. Timing parity of the total
response is not measured or normalised (§5, §8). The stream runs on its own thread: a client that
disconnects does not cancel the request, so a burst of streams costs the same as a burst of asks (no
rate limit, §8).

### 15.7 Tamper demo on a copy (`POST /audit/tamper-demo`, B11, D-19)

The scenarios of `scripts/tamper_demo.py` (now shared code in `audit/tamper.py`) run on a temporary
COPY of the live log, its anchor ledger and the external head file. The copy is taken under the
ledger's append lock, so it never holds a half-written append. The live files are only read. After the
run the endpoint re-checks that every byte the live files held at copy time is unchanged
(`real_log_modified: false`; the files may have grown, because other requests keep appending), and
deletes the temporary directory. Compliance only; ledgered write-ahead (`audit.query`
`op: "tamper-demo"`, `outcome: "started"`) before the copy, then `outcome: "ok"` with the result; a
failure is a ledgered 409 or 500, and a ledger that cannot record the call means 503 and no copy.
One run at a time per process (409 otherwise); the run has a 20 s budget (scenarios not started in
time are reported as skipped). Measured: under 1 s on 1,500 records.
Tests: `test_tamper_demo_route.py` (role check, write-ahead ledgering, the live log's old bytes
unchanged with exactly the endpoint's two records appended, the external head file byte-identical,
`edit-record` detected at the record it edited, the temporary directory removed, ledger down = 503
with nothing run, the runtime bound on 1,500 records). `edit-record` flips one DECISION, as the
presenter describes it: the Gate-2 `forbidden` verdict nearest the middle of the copy becomes
`verified` (with no denial on record, a refused answer becomes released; with neither, the record's
`sub` changes), and `what_we_did` says exactly which
(`::test_scenarios_report_and_edit_detected_at_the_right_seq`,
`::test_edit_record_falls_back_to_a_refusal_then_to_the_sub`). The run lock is released on every
path, including a failure to create the temporary directory
(`::test_a_failed_temp_directory_does_not_lock_the_demo`).

**Limits.** The copy contains whatever the ledger holds (questions, document ids) and sits in the
OS temporary directory for the length of the run; a crash mid-run can leave it there. The
`key-holder-rewrite` scenario uses the live ledger key in process to re-seal the COPY (the key never
leaves the process and is never returned). The demo shows what verification detects on a copy; it
does not test the live chain (that is `GET /audit/verify`).

### 15.8 Hy3 for the plain-English audit search (B13)

`POST /audit/ask` still runs the rules first. Only when they recognise nothing is a model asked: the
same chain as the answers (TokenHub Hy3, then DeepSeek), and only when a key is configured and
`IB_FORCE_OFFLINE` is not `1` (`bootstrap.audit_filter_parser`). The model receives a fixed prompt with
the closed filter vocabulary and a user message with the question and the current time. It never
receives a ledger row, event, document, user list or any other content: the parser has no access to
them. Its reply must be one JSON object with only the closed keys (`audit/nl_llm.py`,
`parse_filter_json`) and must then validate as `AuditFilter` (patterns, `extra="forbid"`, the 365-day
cap). Any failure (no JSON, an extra key, a bad value, a model error or timeout) falls back to the
rule parser's result, with a note and `parser_fallback: true`. The response and the ledgered
`audit.query` record `parser` (`llm` or `deterministic`) and `parser_model` (which model wrote the
filter). The model has no tools, and the filter is only ever a search over the projection the officer
may already query.
Tests: `test_audit_nl_llm.py` (valid JSON used and the model named, fall-through hy3 → DeepSeek,
six kinds of invalid output and two kinds of exception fall back to the rules, rules first, the
model's messages hold only the vocabulary, the question and the clock, the route records the parser,
the bootstrap wiring, `IB_FORCE_OFFLINE`).

**Change to disposition #16 (§3).** Before v2 no model was wired, and a configured model that failed
was a 422. The wired parser now falls back to the rules instead. When the rules recognised nothing,
the rule result is the default query (the last 365 days, nothing else narrowed), the same result as
with no model configured, and the response's `filter`, `window`, `notes` and `parser_fallback` say
so. A constraint the RULES name is still never dropped: an invalid one is still a 422
(`tests/test_audit_nl_filter.py::test_invalid_constraint_is_not_dropped`), and with no fallback the
old fail-closed contract holds (`test_audit_nl_llm.py::test_without_fallback_the_old_fail_closed_contract_holds`).
The officer's question text does leave the host for TokenHub when a key is configured; it is the
officer's own text, not ledger content.

### 15.9 Real Gate 2 denials in the seeded history (B14)

The mock platforms accept `?webhook=0` on any `/admin/*` mutation: the change is applied and no
webhook is sent or recorded (a lost webhook). The seeder uses it for the manifest's two S4 revocations,
asks the matching question as the affected person (replay-labelled), confirms the Gate 2 `forbidden`
decision in the officer's journey view, and restores the source with a normal call. The denials in
the analytics "Gate-2 reasons" panel are therefore real live-check refusals, not typed-in numbers.
Tests: `databridge/apps/mock_platforms/tests/test_mock_admin_webhooks.py::test_silent_change_applies_without_a_webhook`,
`tests/test_seed_silent_revocations.py` (both revocations are real Gate 2 denials, the denied document
is never released, only the restores send webhooks).

**Limit.** `?webhook=0` is a mock-platform feature guarded by the mock's admin token; real platforms
have no such switch. It is not reachable through `/sim/*`.

### 15.10 Host allowlist on the IB and the mock platforms (finals Opus review #1)

Both FastAPI apps refuse any request whose `Host` is not on an allowlist, with a 400 before any route,
authentication or ledger work (Starlette `TrustedHostMiddleware`). A DNS-rebinding page always sends
its own domain as the Host, so it cannot reach either service through the browser.

* IB (`IbConfig.trusted_hosts`): `127.0.0.1`, `localhost`, `[::1]`; when `IB_DEPLOYED` is unset,
  also `testserver` (the fixed Host of the in-process test transport used by the tests and the eval;
  a single-label name public DNS cannot give a page); when deployed, also the host of
  `IB_PUBLIC_ORIGIN`. Extra names (a LAN rehearsal) only through `IB_ALLOWED_HOSTS`.
* Mock platforms (`mock_platforms.app.trusted_hosts`): the same loopback names and `testserver`,
  plus `MOCK_ALLOWED_HOSTS`.

The seeder, `demo_up.sh` and the web proxy call `http://127.0.0.1:8100`, the IB calls the mock at
`http://127.0.0.1:8200`, so none of them changes.
Tests: `test_final_review_fixes.py::test_a_foreign_host_header_is_refused_before_any_route` (a
refused call never reaches the ledger), `::test_trusted_hosts_follow_deployment`,
`databridge/apps/mock_platforms/tests/test_mock_auth_ratelimit.py::test_a_foreign_host_header_is_refused`.

**Limit.** An IB bound to a LAN address (`IB_HOST=0.0.0.0`) answers only to the names above unless
`IB_ALLOWED_HOSTS` lists the LAN name or address.
