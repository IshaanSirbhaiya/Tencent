# Backend contracts v1 (finals): graph, doc, status, trace receipt, stats

For the UI agents. Everything here is served by the Internal Brain (`databridge_ib`, :8100) and is
verified by tests in `databridge/apps/internal_brain/tests/` (file names below). Paths in the code column
are relative to `databridge/apps/internal_brain/databridge_ib/`.

General rules (unchanged):

- Identity comes only from the session (`ib_session` cookie or `Authorization: Bearer`). No body or
  query parameter names a person. There is no `view_as`.
- Every route here needs a signed-in employee (`employee`, `compliance_officer` or `admin_it`):
  401 without a session, 403 without one of those roles.
- **503 `{"error": "temporarily unavailable"}`** whenever the access ledger cannot record the call
  (write-ahead) or anything unexpected fails. The UI shows one generic "try again" state.
- **404 `{"detail": "not found"}`** is the ONE negative answer for "restricted", "unknown" and
  "the live check failed". Never render anything that distinguishes them.
- Times are ISO 8601 strings. Counts are integers or `null` (`null` = not exact, do not show a number).
- Values in the JSON examples are illustrative; the field names and types are the contract.

## Proxy allowlist (the coordinator owns `app/api/ib/[...path]/route.ts`)

Add exactly these (method, anchored pattern). `DOC_ID` = the existing strict doc-id segment pattern
(`<platform>:<id>`; Slack ids contain a second `:`), URL-encoded as one segment.

| Method | Path | Slot (compare/View-as) |
|---|---|---|
| GET | `/ib/graph` (query `focus`, `q`, `limit`) | yes (each slot uses its own cookie) |
| POST | `/ib/graph/openable` | **yes**: this is the View-as call |
| GET | `/ib/doc/{DOC_ID}` (query `q`) | no |
| GET | `/ib/status` | no |

`/ib/trace/{req}`, `/ib/history`, `/audit/stats`, `/audit/journey/{req}` are already allowlisted; their
shapes changed (below).

---

## GET /ib/graph: the signed-in person's permission-aware graph

Code: `graph.py`, `authz.py`. Tests: `tests/test_graph_doc.py`.

Query parameters (all optional):

| Param | Rule |
|---|---|
| `focus` | a node id (see below), max 300 chars. Wins over `q` |
| `q` | search text, max 500 chars. Same relevance search as the ask pipeline, over the person's readable set |
| `limit` | 1..300, default 120 (422 outside) |

Mode: `focus` given → `"focus"` (the node and its one-hop neighbours); else `q` given → `"search"`
(the hits and their containers and authors); else `"overview"` (platforms, containers with counts,
and the people who author in them).

Node ids:

| kind | id | example |
|---|---|---|
| `doc` | the document id | `jira:PAY-225`, `slack:C0DBMIG:1782379020.002812`, `confluence:cf-1001`, `drive:df-1` |
| `container` | `container:<platform>:<container key>` | `container:jira:PAY` |
| `person` | `person:<user id>` | `person:raj.m` |
| `platform` | `platform:<platform>` | `platform:slack` |

Response 200:

```json
{
  "as_of": "2026-10-30T02:59:39.153084+00:00",
  "scope_note": "Answers draw only on content you can access in Confluence, Jira, Slack and Google Drive.",
  "sub": "jdoe",
  "mode": "focus",
  "focus": "jira:PAY-225",
  "query": null,
  "limit": 120,
  "truncated": false,
  "nodes": [
    {"id": "jira:PAY-225", "kind": "doc", "platform": "jira",
     "label": "PAY-225: Refactor FX quote cache docs", "container": "container:jira:PAY",
     "container_name": "Payments", "doc_kind": "issue", "version": "2026-09-30T10:00:00+08:00",
     "updated_at": "2026-09-30T10:00:00+08:00", "deep_link": "https://...",
     "degree_hidden_safe": 0, "more": false},
    {"id": "container:jira:PAY", "kind": "container", "platform": "jira", "label": "Payments",
     "container": null, "count": 111, "degree_hidden_safe": 110, "more": true},
    {"id": "person:raj.m", "kind": "person", "platform": null, "label": "Raj M",
     "container": null, "count": 58, "degree_hidden_safe": 57, "more": true},
    {"id": "platform:jira", "kind": "platform", "platform": "jira", "label": "jira",
     "container": null, "count": null, "degree_hidden_safe": null, "more": true}
  ],
  "edges": [
    {"source": "container:jira:PAY", "target": "jira:PAY-225", "kind": "contains"},
    {"source": "person:raj.m", "target": "jira:PAY-225", "kind": "authored"},
    {"source": "platform:jira", "target": "container:jira:PAY", "kind": "contains"}
  ],
  "freshness_note": "Every document shown was checked with its source just now."
}
```

Node fields:

| Field | On | Meaning |
|---|---|---|
| `id`, `kind`, `platform`, `label` | all | `label` = title (doc), container name, person name, platform id |
| `container` | doc | the id of its container node; `null` on other kinds |
| `container_name`, `doc_kind` (`page`/`issue`/`thread`/`file`), `version`, `updated_at`, `deep_link` | doc | the CURRENT checked version only (no history exists) |
| `count` | container, person, platform | documents this person can open (container/person) or containers (platform), counted only over documents confirmed live in this call. **`null` when not exact: show no number** |
| `degree_hidden_safe` | all | how many of this node's confirmed neighbours are NOT in this response. `null` when not exact |
| `more` | all | `true` = at least one confirmed neighbour is not shown (show "more", with the number only when `degree_hidden_safe` is not null); `false` = nothing more; `null` = unknown (show nothing) |
| `rank` | doc, search mode | 1-based relevance rank of a hit |

Edge kinds: `contains` (platform → container, container → doc), `authored` (person → doc; in
overview mode person → container with an extra `"weight"`: how many of that person's documents in
that container), `links` (doc → doc via a supported URL), `mentions` (doc → doc via a Jira key
written in the text). Both endpoints of every edge are in `nodes`.

Guarantees (tested): only documents that pass Gate 1 and a live Gate 2 (plus version-bound
read-through) in the same call appear or are counted; containers and people are derived only from
them; a restricted document never appears by id, title, count or edge; the response for a world with
restricted documents equals the response for the same world without them; a focus the person cannot
open and an unknown focus give the same 404; an empty search is `200` with `nodes: []`, `edges: []`.

Errors: 404 (focus not openable or unknown), 422 (bad params), 503 (ledger down or failure).
Ledger: `graph.viewed` (counts only) before the response.
Measured (real synthetic world, mock platforms over HTTP, jdoe): overview about 3.4 s (it checks every
readable document live, like the map), search about 0.5 s, focus 0.3 to 0.5 s. Fetch the overview once
per view; do not poll.

## POST /ib/graph/openable: which of these can the CALLER open now?

For View-as: Jordan's graph is drawn from Jordan's session; the compare slot (Ken's own session)
posts Jordan's doc ids here and marks the ones it returns as shared (nothing is greyed out; D-17). Never draw a node that is not in
Jordan's own graph; never fetch titles under Ken's session for this.

Request: `{"doc_ids": ["slack:...", "jira:PAY-1"]}`, at most 300 ids, each at most 300 chars, no other
keys (422 otherwise).

Response 200 (constant shape):

```json
{"sub": "ken.ext", "as_of": "2026-10-30T03:00:00+00:00", "openable": ["jira:PAY-1"]}
```

`openable` keeps request order, drops duplicates, and leaves out every id that is restricted, unknown
or failed its live check (the same thing). Check `sub` against the slot's `whoami`. Ledger:
`graph.viewed` with `mode: "openable"`.

## GET /ib/doc/{doc_id}: one document the person can open

Code: `docview.py`. Query: `q` (optional, max 500): the excerpt becomes the best-matching chunk.

Response 200:

```json
{
  "doc_id": "slack:t1", "platform": "slack", "kind": "thread", "container": "db-migration",
  "container_name": "db-migration", "title": "migration status",
  "version": "1", "updated_at": "2026-10-10T09:00:00+08:00", "deep_link": "https://...",
  "created_at": "2026-10-10T09:00:00+08:00",
  "author": {"id": "priya", "name": "Priya ..."},
  "excerpt": "The database migration cutover is on Friday. ...",
  "excerpt_kind": "start",
  "excerpt_truncated": false,
  "links": [{"doc_id": "jira:PAY-1", "platform": "jira", "title": "Database migration epic",
             "container_name": "PAY", "kind": "issue", "relation": "mentions"}],
  "lineage": {"connector": "slack", "last_sync_at": "2026-10-30T02:51:54+00:00",
              "content_checked_at": "2026-10-30T02:51:54+08:00",
              "authz_checked_at": "2026-10-30T02:52:03+00:00", "live_read": false},
  "scope_note": "...", "as_of": "..."
}
```

- `author` may be `null`. `excerpt_kind` is `"start"` (first 1,200 chars of the masked text) or
  `"best_chunk"` (with `q`). Card numbers, NRIC and IBANs are already masked at ingest.
- `links` lists only linked documents the person can open now (`relation`: `links` or `mentions`).
- `lineage.live_read` is true when the source had a newer version and it was re-read just now.
- No "why I can see it" field: it is deliberately omitted (no ACL or group data leaves the server).

Errors: **404 identical** for restricted, unknown and failed checks (same bytes and headers); 503
when the ledger is down (the same 503 for every id, because `doc.viewed` is appended BEFORE the
access decision). Re-call this when the sheet opens, even if the graph showed the node.

## GET /ib/status: model and ledger status (signed-in employees; not on /health)

```json
{
  "models": {"provider": "tencent-tokenhub", "chain": ["hy3", "deepseek-v4.1-flash"],
             "active": true, "active_chain": ["hy3", "deepseek-v4.1-flash"],
             "fallback": {"provider": "deterministic", "model": "extractive-v1"}},
  "key_configured": true,
  "force_offline": false,
  "ledger": {"ok": true}
}
```

`provider` is `null` when no key is configured (`"openai-compatible"` for a non-TokenHub endpoint).
`active` is false when answers come from the deterministic quoting path (no key, or
`IB_FORCE_OFFLINE=1`). `ledger.ok` is re-verified at most every 15 s (`null` if not checkable). No
key, URL or ledger sequence number is ever returned. `/health` stays `{"ok": true}`.

## GET /ib/trace/{req_id}: the asker's receipt (CHANGED)

Own requests only; another person's request and an unknown id give the same 404.

```json
{
  "req_id": "030f2a7b...", "question": "What does the settlement keys rotation page say?",
  "channel": "replay", "event_time": "2026-10-29T18:57:02.614027Z", "synthetic_replay": true,
  "status": "released",
  "policy_version": "ib-policy-1",
  "model": {"provider": "deterministic", "model": "extractive-v1", "fallback_used": false},
  "prompt_version": "ib-prompt-2026-10-13.1",
  "latency_ms_rounded": 200,
  "stages": [{"name": "accept", "status": "ok"}, {"name": "gate1", "status": "ok"},
             {"name": "gate2", "status": "ok"}, {"name": "seal", "status": "ok"},
             {"name": "compose", "status": "ok"}, {"name": "release", "status": "ok"}],
  "dropped_sentences_count": 0,
  "citations": [{"doc_id": "confluence:cf-1004", "platform": "confluence", "kind": "page",
                 "container": "payment-gateway", "container_name": "Payment Gateway",
                 "title": "Settlement keys rotation", "version": "4",
                 "updated_at": "...", "deep_link": "...", "cited_version": "4"}],
  "answer_sha256": "…",
  "records": [{"ordinal": 1, "kind": "request.accepted", "hash": "…"},
              {"ordinal": 2, "kind": "retrieval.decided", "hash": "…"},
              {"ordinal": 3, "kind": "answer.released", "hash": "…"}]
}
```

Removed: `released`, `stage_ms`, `latency_ms`, `events` (with global `seq` and `recorded_at`).
`status` is `released`, `refused`, `failed` or `in_progress`. A stage is `ok` when its sealed record
exists, otherwise `skipped` (never derived from timings). `citations` lists only cited documents the
person can STILL open (Gate 1 + live Gate 2 now); the rest are left out with no count, so a citation
can disappear after a revocation. Ledger: `trace.viewed`.

## GET /ib/history (CHANGED: two fields added)

`{"items": [{"req_id", "question", "event_time", "channel", "synthetic_replay"}]}`. Label rows with
`synthetic_replay: true` as seeded replay (D-10); `channel` is `ui`, `workbuddy-mcp`, `api` or `replay`.

## GET /audit/stats (compliance; CHANGED)

- The window is half-open: `now - days <= event_time < now` (future-dated events are never counted).
- **`funnel` is removed.** Use two flows with different units:
  - `requests_flow`: `{asked, answered, refused, failed}` (requests);
  - `documents_flow`: `{considered, gate2_checked, gate2_allowed, gate2_denied, released, cited}`
    (documents). `cited` = distinct cited documents per request, counted only where that request
    released them, so `cited <= released <= gate2_allowed <= gate2_checked` (tested).
  - `totals.citations` is still the raw citation count (it can exceed `released`: two chunks of one
    document can both be cited).
- New totals: `graph_views`, `doc_views`, `trace_views`.
- `stages_ms` has a sixth stage, `release` (storing the answer). Stage timings end BEFORE the final
  `answer.released` append; `latency_ms` likewise.
- A failure computing the stats is a ledgered `500` (`audit.query` with `outcome: "error"`), or 503 if
  the ledger cannot record it. A result is cached per verified ledger head (seq + hash), parameters and
  minute; it is only consulted after the chain verified, and because every stats call appends its own
  `audit.query`, it helps only concurrent refreshes. Measured on a seeded ledger (941 records, over
  HTTP): about 340 to 420 ms per call, almost all chain verification and the call's own append.
  Fetch once per view, with a manual refresh; do not poll.

## GET /audit/journey/{req_id} (compliance)

Unchanged shape. A projection failure is now a ledgered 500 (it used to be an unledgered 500).

## Ledger event kinds (new)

| Kind | Fields (besides `kind`, `event_time`, `synthetic_replay`) |
|---|---|
| `graph.viewed` | `sub`, `mode` (`overview`/`focus`/`search`/`openable`), `outcome` (`ok`/`not_found`), `nodes_returned`, `docs_returned`, `edges_returned`, `docs_checked`, `policy_version` |
| `doc.viewed` | `sub`, `doc_id` (as requested, max 300 chars), `with_query`, `policy_version` (written before the access decision) |
| `trace.viewed` | `sub`, `outcome`, `citations_returned` |

They appear in `/audit/events` (filter `kind`) and in `/audit/stats` `kinds` and `totals`.
`request.failed` now carries the request's own `event_time` and `synthetic_replay`.

## Seeded demo history (B7) and the eval summary (B8)

- `databridge/scripts/seed_demo_activity.py` (usage at the top of the file). Seeded asks are
  `channel: "replay"`, `synthetic_replay: true`, spread over the last 30 days. The seeder's audit
  queries, reveals, Verify runs and simulator steps are real calls at seed time (not replay-labelled).
- `databridge/apps/internal_brain_web/public/eval_summary.json`, written by
  `databridge/scripts/eval_summary.py`:

```json
{
  "generated_at": "...", "source": "evals/internal_brain/results.json", "git_sha": "…",
  "stale": true, "note": "Full evaluation re-run pending: the Internal Brain changed after this run.",
  "run_date": "2026-10-14T20:12:33+00:00", "full_run": true, "limit": 0,
  "leaks": 0, "leak_requests": 0, "requests": 5958, "requests_per_mode": 2979,
  "upper95": 0.001007, "payload_requests": 683, "payload_upper95": 0.004392, "violations": 0,
  "differential": {"pairs": 400, "mismatches": 0, "control_mismatches": 0},
  "injection": {"requests": 976, "hard_failures": 0},
  "recall": {"released": {"fake-capture": 1.0, "extractive": 1.0},
             "released_all_modes": {"min": 1.0, "max": 1.0}},
  "cited_recall": {"per_mode": {"fake-capture": 0.964, "extractive": 0.878},
                   "range": {"min": 0.878, "max": 0.964}},
  "quality_findings": 14, "canaries_valid": true, "runtime_s": 61.9,
  "per_mode": {"fake-capture": {"requests": 2979, "with_leak": 0, "upper95": 0.001007,
                                "with_model_payload": 683, "refused": 2296}},
  "tests": {"backend": 680, "e2e": 38},
  "smoke": null
}
```

Show `note` whenever `stale` is true. `smoke`, when present, is a later run from a scratch directory
(`limit` 0 means a full run), with the same keys as the headline. Rates are fractions (0.001007 =
0.1007 %).


---

# v2 (30 Oct): tamper on a copy, SSE stage events, Hy3 audit search, real Gate 2 denials

Plan v2 items B11 to B15 (docs/finals/PLAN.md §9; decisions D-08, D-19, D-20). The general rules at the
top of this file still apply. Tests are in `databridge/apps/internal_brain/tests/` unless a path says
otherwise.

## Proxy allowlist (v2 additions)

| Method | Path | Slot (compare/View-as) |
|---|---|---|
| POST | `/ib/ask/stream` | yes (same rule as `/ib/ask`) |
| POST | `/audit/tamper-demo` | no |

## POST /ib/ask/stream: the ask, with live stage events (SSE)

Code: `app.py` (`ask_stream`), `pipeline.py` (`on_stage`, `STAGES`). Tests: `tests/test_ask_stream.py`.

Request: exactly `/ib/ask`'s body, `{"question": "..."}` (no other keys; 422 otherwise). The same
identity, roles (401/403), channel label (`workbuddy-mcp` only on bearer calls) and replay switch as
`/ib/ask`. Those errors are ordinary JSON responses, sent before any stream starts.

Response: `200`, `content-type: text/event-stream`, `cache-control: no-cache, no-transform`,
`x-accel-buffering: no`. Each event is `event: <name>\ndata: <one line of JSON>\n\n`:

```
event: stage
data: {"name":"accepted","ok":true}

event: stage
data: {"name":"gate1","ok":true}
... gate2, sealed, composing, grounded ...
event: stage
data: {"name":"released","ok":true}

event: answer
data: {"request_id":"...","answer":"...","citations":[...],"model":{...},"scope_note":"...","as_of":"..."}

event: done
data: {}
```

| Stage | Sent when this has COMPLETED |
|---|---|
| `accepted` | `request.accepted` is sealed in the ledger |
| `gate1` | the index pre-filter (what the person can read) and the ranking |
| `gate2` | the live source checks, read-through and linked objects |
| `sealed` | `retrieval.decided` is sealed (before any model call) |
| `composing` | an answer text exists (the model chain, whose grounding wall checks each sentence, or the extractive answerer) |
| `grounded` | the answer's citation tags were checked against the released passages and the citations bound |
| `released` | `answer.released` is sealed |

Rules for the UI:

- Stage events carry only `name` and `ok: true`. No counts, ids, titles or reasons, ever. Every
  successful request sends the same seven names in the same order, whether it answers or refuses, so
  the sequence says nothing about restricted content (tested: a restricted and a made-up question
  give identical event sequences).
- The answer text arrives only in `answer`, after `released` (D-08). Its `data` is exactly the JSON
  `/ib/ask` returns for the same question (tested equal apart from `request_id` and `as_of`). Do not
  show any answer text before it.
- `error` with `{"error": "temporarily unavailable"}` ends the stream on any failure, including the
  ledger (no `answer`, no `done` after it). Show the normal "try again" state. Example: the ledger is
  down → the only event is `error`; a failure at `answer.released` → stages up to `grounded`, then
  `error`.
- The ledger records are the same three as `/ib/ask`. A client that disconnects does not cancel the
  request (it runs to completion and is ledgered, as with `/ib/ask`).
- Stage events are real progress, not a timer (D-20). Their timing is not padded: how long each stage
  takes is visible to the asker, as the total latency of `/ib/ask` already is (SECURITY.md §5,
  "Timing": not controlled).

**What the Next proxy must do to stream** (`app/api/ib/[...path]/route.ts`, coordinator-owned):

1. Allow `POST ib/ask/stream` in `ALLOWED` and `SLOT_ALLOWED` (same as `ib/ask`).
2. Send `accept: text/event-stream` upstream.
3. When the upstream answer is `2xx` with `content-type: text/event-stream` and has a body, return
   `new NextResponse(upstream.body, {status: 200})` immediately: pass the `ReadableStream` through.
   Never `await upstream.arrayBuffer()` / `.text()` for this path (that buffers the whole stream and
   the stages arrive all at once).
4. Set `content-type: text/event-stream; charset=utf-8`, `cache-control: no-store` (or
   `no-cache, no-transform`) and `x-accel-buffering: no` on the response; relay the session cookie
   as for any other path. Do not compress or rewrite the body.
5. Any non-stream upstream answer (401/403/422/503 JSON) goes through the normal non-stream branch.
6. The route's 90 s deadline covers the whole stream; the browser reads it with `fetch` +
   `response.body.getReader()` (EventSource cannot POST) and splits on blank lines.

## POST /audit/tamper-demo: the tamper scenarios on a COPY of the live ledger (compliance)

Code: `audit/tamper.py` (`run_on_live_copy`, `run_scenarios`, shared with
`databridge/scripts/tamper_demo.py`), `audit/routes.py`. Tests: `tests/test_tamper_demo_route.py`.

No body. `compliance_officer` only (403 otherwise, 401 without a session).

What it does (decision D-19): appends `audit.query` `{op: "tamper-demo", outcome: "started",
target: "copy"}` (write-ahead), copies the live log, its anchor ledger and the external head file
into a temporary directory **under the ledger's append lock** (so no append is half-copied), runs
every scenario on its own copy of that snapshot, deletes the temporary directory, re-checks that
every byte the live files held at copy time is unchanged, then appends `{op: "tamper-demo", outcome:
"ok", real_log_modified, as_expected, head_before}`. The live ledger is only ever read.

Response 200:

```json
{
  "real_log_modified": false,
  "head_before": "3f9a0c1d2e4b",
  "records_copied": 1512,
  "external_head_copied": true,
  "elapsed_ms": 840,
  "scenarios": [
    {"id": "baseline", "title": "Untouched copy", "what_we_did": "Copied the log and changed nothing.",
     "expected": "not flagged", "expected_detected": false, "detected": false, "ok": true,
     "verify_ok": true, "internal_ok": true, "external_head_match": true, "records": 1512,
     "problems": []},
    {"id": "edit-record", "title": "Edit one old record",
     "what_we_did": "Changed who made record 754 (ib_event.sub) without re-sealing it.",
     "expected": "flagged", "expected_detected": true, "detected": true, "ok": true,
     "verify_ok": false, "internal_ok": false, "external_head_match": false, "records": 753,
     "first_bad_seq": 754,
     "problems": ["record 754: its seal or digest does not verify", "..."]}
  ]
}
```

| Field | Meaning |
|---|---|
| `real_log_modified` | `false` when every byte of the live log, anchor ledger and external head file at copy time is unchanged after the run (they may have grown: other requests keep appending) |
| `head_before` | the first 12 hex characters of the live head that was copied (it includes this call's own `started` record) |
| `scenarios[].id` | `baseline`, `edit-record`, `suffix-delete`, `directory-rollback`, `rollback-after-publication`, `remove-internal-anchors`, `external-head-missing`, `key-holder-rewrite`. The external-head scenarios are left out when nothing was published yet |
| `expected` / `expected_detected` | what verification must say. `rollback-after-publication` is `not flagged`: the stated window (SECURITY.md §3) |
| `detected` | verification flagged the copy (`verify_ok` false) |
| `ok` | the outcome matched the expectation (show a pass mark when true) |
| `first_bad_seq` | only when the chain check names a record: the first record that fails (`edit-record` names the record it edited) |
| `problems` | the verifier's own text; temporary paths are replaced by `<copy>` |
| `skipped` | present and `true` only if the 20 s time budget ran out before the scenario started (`ok` false) |

Errors: 409 (another tamper demo is running in this process), 500 (the run failed), 503 (the ledger
could not record the call: then nothing is copied or run). 409 and 500 are ledgered (`outcome:
rejected`/`error`). Measured: under 1 s on a 1,500-record ledger (`test_runtime_is_bounded_on_1500_records`
asserts < 5 s). It appends two records per call: do not poll; run it on a button press.

## POST /audit/ask (CHANGED: the Hy3 parser)

Code: `audit/nl_llm.py`, `audit/nl_filter.py`, `bootstrap.py` (`audit_filter_parser`), `llm.py`
(`ModelChain.complete`). Tests: `tests/test_audit_nl_llm.py`.

The rules still run first. Only when they recognise nothing in the question is the model asked: the
SAME chain as the answers (TokenHub Hy3, then DeepSeek), when a key is configured and
`IB_FORCE_OFFLINE` is not `1`. It gets a fixed prompt with the closed filter vocabulary and the
question plus the current time, nothing else, and must reply with one JSON object. Its reply must
validate as the closed `AuditFilter`; otherwise (no JSON, unknown key, bad value, model error) the
rule parser's result is used.

Response additions (all other fields unchanged):

| Field | When |
|---|---|
| `parser` | `"llm"` (the model's filter was used) or `"deterministic"` (the rules) |
| `parser_model` | with `parser: "llm"`: the model that wrote the filter, e.g. `"hy3"` or `"deepseek-v4.1-flash"` |
| `parser_fallback` | `true` when the model was asked but its filter was not usable (`notes` then says "the rule parser's result is shown") |

The ledgered `audit.query` (`op: "ask"`) carries the same `parser`, `parser_model` and
`parser_fallback`.

## Mock platforms: silent changes (`?webhook=0`)

Code: `databridge/apps/mock_platforms/mock_platforms/admin.py` (`silent`, `_emit`). Tests:
`databridge/apps/mock_platforms/tests/test_mock_admin_webhooks.py::test_silent_change_applies_without_a_webhook`,
`::test_silent_flag_values`.

Any `/admin/*` mutation with `?webhook=0` (also `false`, `off`, `no`) changes the platform but sends
and records no webhook; the response's `webhook` is `null`. It simulates a lost webhook. The `/sim/*`
routes are unchanged (they always send).

## Seeded demo history: real Gate 2 denials (B14)

`databridge/scripts/seed_demo_activity.py` now also runs the manifest's two S4 revocations
(`s4_remove_member`, `s4_restrict_page`) silently, asks the matching S4 question as the affected
person (replay-labelled), confirms the Gate 2 denial in the officer's journey view, and restores the
source with a normal (webhook-sending) admin call. Its summary gains `silent_revocations`,
`gate2_denials_confirmed` and `silent_restored`. On the analytics page, `/audit/stats`
`gate2.verdicts.forbidden` and `gate2.reasons` then hold real denials. Test:
`tests/test_seed_silent_revocations.py` (both revocations are real Gate 2 `forbidden` decisions; the
denied document is never released). Run the seeder right after the stack starts: a poll that lands
between the silent change and the question lets Gate 1 drop the document first (no denial then).

## Eval summary (B15)

`public/eval_summary.json` gains `ib_code_sha256`: the digest of
`apps/internal_brain/databridge_ib/**/*.py` the evaluation ran on (written by the eval into
`results.json` `config.ib_code_sha256`). `stale` is now `true` exactly when the current code's digest
differs from it, committed or not; older results without the digest keep the git rule. Show `note`
whenever `stale` is true.

## Ledger (v2)

No new event kinds. `audit.query` gains `op: "tamper-demo"` rows (`outcome`: `started`, `ok`,
`rejected`, `error`) and the parser fields above on `op: "ask"` rows.


---

# v3 (30 Oct): Act on an answer (draft a reply, create a follow-up ticket)

Code: `actions.py`, `app.py` (routes), `connectors/jira.py` (`projects`, `creatable`, `create_issue`),
`llm.py` (`ModelChain.draft`). Tests: `tests/test_act.py`, `tests/test_act_e2e.py`,
`databridge/apps/mock_platforms/tests/test_mock_jira_create.py`. Security: `docs/tencent/SECURITY.md`
§15.11. The general rules at the top of this file apply (401, 403, the one 404, the one 503).

## Proxy allowlist (v3 additions)

| Method | Path | Slot (compare/View-as) |
|---|---|---|
| POST | `/ib/act/draft` | **no** (main session only) |
| POST | `/ib/act/ticket/propose` | no |
| POST | `/ib/act/ticket/confirm` | no |
| POST | `/ib/act/ticket/cancel` | no |

One pattern in `app/api/ib/[...path]/route.ts`: `^ib\/act\/(draft|ticket\/(propose|confirm|cancel))$`.

## Status codes (all four routes)

| Status | Body | When |
|---|---|---|
| 200 | below | |
| 404 | `{"detail": "not found"}` | ONE answer for: someone else's request, an unknown id, a refused or failed answer, an answer whose cited documents the person can no longer open, an unknown / someone else's / used / expired proposal |
| 409 | `{"error": "unavailable", "message": "A follow-up ticket can't be created from this answer. Nothing was changed."}` | ONE polite message for every reason a ticket cannot be proposed or created (no create permission, a source error, a revoked permission, a failed create). Never a reason |
| 422 | | a body with an unknown key (any identity field), a bad `kind`, a blank or too long summary, a `project` outside the proposal's `projects` |
| 503 | `{"error": "temporarily unavailable"}` | the ledger could not record the call (nothing happened), or the stored answer does not match its sealed digest |

## POST /ib/act/draft

Request: `{"req_id": "<one of MY request ids>", "kind": "reply"}` (`kind`: `reply` (default) or `message`).

Response 200:

```json
{
  "action_id": "64f8ad25…", "kind": "reply", "status": "draft", "label": "Draft for your review",
  "req_id": "…",
  "greeting": "Hello,",
  "opener": "Thank you for getting in touch.",
  "body": [{"text": "Your account is undergoing a routine review in line with our regulatory obligations.",
            "tags": ["C1"]}],
  "closing": "If you have any other questions, please let me know.\n\nKind regards,\nAmy Lim",
  "text": "Hello,\n\nThank you for getting in touch. Your account is …\n\nIf you have …\n\nKind regards,\nAmy Lim",
  "citations": [{"tag": "C1", "doc_id": "confluence:cf-1204", "platform": "confluence", "kind": "page",
                 "container": "cx", "container_name": "Customer Experience", "title": "…",
                 "version": "5", "updated_at": "…", "deep_link": "…"}],
  "model": {"provider": "deterministic", "model": "approved-script-v1", "fallback_used": false},
  "prompt_version": "ib-draft-2026-10-30.1",
  "sent": false, "scope_note": "…", "as_of": "…"
}
```

- `body` sentences come only from the cited sentences of that answer whose documents pass Gate 1 and a live
  Gate 2 now. `tags` are the ANSWER's own tags (`C1` …): number them with the answer's `citations` order.
- `text` is the whole draft without tags (what Copy puts on the clipboard).
- `model.model`: `approved-script-v1` (approved wording used word for word; no model asked),
  `draft-template-v1` (the cited sentences; `fallback_used: true` when the model was asked and its draft
  was not usable), or the model that wrote it (`hy3`, `deepseek-v4.1-flash`).
- The Brain never sends a draft. Ledger: `action.proposed` before the response.

## POST /ib/act/ticket/propose

Request: `{"req_id": "…"}`. Nothing is created.

```json
{
  "proposal_id": "9a1c…", "action_id": "9a1c…", "kind": "ticket", "status": "proposed", "req_id": "…",
  "project": {"key": "DBMIG", "name": "Database Migration"},
  "projects": [{"key": "DBMIG", "name": "Database Migration"}, {"key": "AUTH", "name": "Auth Service"},
               {"key": "PAY", "name": "Payments"}],
  "issue_type": "Task",
  "summary": "Follow-up: What's the status of the database migration project …",
  "summary_max": 200,
  "description": "Follow-up created from an Internal Brain answer (request …).\n\nSources (each link opens only for people the source system allows):\n\n[1] https://…\n[2] https://…",
  "citations": [ … same shape as the draft's … ],
  "expires_at": "…", "scope_note": "…", "as_of": "…"
}
```

- `projects` = the projects where the person may create issues, checked live at the source (cited
  projects first); `project` is the first. Show a picker only when there is more than one.
- `description` is fixed (links only, no titles or text). Show it read-only, exactly as sent.
- The proposal is the caller's alone, usable once, and expires after 15 minutes (`expires_at`).
- Ledger: `action.proposed` (target, option count) before the response; a 409 writes `action.failed`.

## POST /ib/act/ticket/confirm

Request: `{"proposal_id": "…", "summary": "optional edit", "project": "optional key from projects"}`.
The summary is made one line (control characters become spaces), 1..200 characters.

```json
{
  "status": "created", "action_id": "9a1c…", "req_id": "…",
  "project": {"key": "DBMIG", "name": "Database Migration"},
  "summary": "Chase the replica lag blocker before the wave 4 cutover",
  "issue": {"key": "DBMIG-13", "url": "https://companya.atlassian.net/browse/DBMIG-13", "doc_id": "jira:DBMIG-13"},
  "as_of": "…"
}
```

Order: `action.confirmed` (write-ahead) → live "Create issues" re-check → one `POST /rest/api/3/issue`
with the person as reporter → `action.executed` (or `action.failed` and the 409). The mock Jira sends a
`content.changed` webhook, so the new issue is indexed and appears in the Brain for whoever may browse it.

## POST /ib/act/ticket/cancel

Request: `{"proposal_id": "…"}` → `{"status": "cancelled", "action_id": "…"}`. Ledger: `action.rejected`.

## Ledger event kinds (v3)

| Kind | Fields (besides `kind`, `req_id` = the answer's request, `event_time`, `synthetic_replay`) |
|---|---|
| `action.proposed` | `sub`, `action_id`, `action` (`draft.reply`/`draft.message`/`ticket.create`), `cited_docs`, `doc_ids`, `sentences`, `target`, `options`, `model`, `digest` (SHA-256 of the draft or proposal; never its text), `policy_version` |
| `action.confirmed` | `sub`, `action_id`, `action`, `target`, `summary_edited`, `digest` (of exactly what will be sent) |
| `action.executed` | `sub`, `action_id`, `action`, `target`, `result_ref` (the issue key) |
| `action.failed` | `sub`, `action_id?`, `action?`, `stage` (`lookup`/`propose`/`confirm`/`execute`), `reason_code` (ledger only). A failed lookup has `req_id: null` |
| `action.rejected` | `sub`, `action_id`, `action` |

They appear in `/audit/events` (filter `kind`), in the request's `/audit/journey/{req}` `events` and the
asker's `/ib/trace/{req}` `records`, and in `/audit/stats` `kinds` plus the new totals
`actions_proposed`, `actions_confirmed`, `actions_executed`, `actions_failed`, `actions_rejected`.
`answer.released.citations[]` now also carries each citation's `tag`.

## Mock platforms (v3)

- `POST /jira/rest/api/3/issue` (svc or admin token): Jira Cloud's create shape (`fields.project`,
  `summary` ≤ 255 and one line, `issuetype` Task/Story/Bug, `description` ADF, `reporter.accountId`,
  `labels`). 201 `{id, key, self}`; 400 `{"errorMessages": [], "errors": {"<field>": "…"}}`; 403 when the
  reporter may not create issues in the project. A `content.changed` webhook follows.
- `POST /jira/rest/api/3/permissions/check` accepts `CREATE_ISSUES` for `projects`: a project's `create`
  holders from `world.json`, by default its `Developers` role (Viewers browse, they do not create).

## Demo-proof features (v4, 30 Oct): live growth, add a document, absence check, engine refresh (D-24)

Plan: `docs/finals/live_growth/PLAN.md`. Security: `docs/tencent/SECURITY.md` §15.13. Code: `live.py`,
`sim.py`, `engine_refresh.py`, `bootstrap.py`. Tests: `test_live_growth.py`, `test_sim_add_document.py`,
`test_demo_features.py`.

**Toggle.** All five routes below exist only when the IB runs with `IB_DEMO_FEATURES=1` and NOT
`IB_DEPLOYED=1`. Off, they are not mounted: every call is the framework's unknown-route 404
(`{"detail": "Not Found"}`), byte-identical to any other unknown path, signed in or not. Only
`databridge/scripts/demo_up.sh` sets the flag (with `IB_ENGINE_URL`, `IB_ENGINE_TOKEN` for the engine
refresh). `POST /sim/upload-file` (contract `uploads.md`) follows the same two switches.

**`GET /ib/status`** gains `"demo_features": true | false` (true only when the routes are mounted). The web
app shows the demo controls only when this is true AND the viewer turned "Demo features" on in Settings
(localStorage `ib-demo-features=1`) or the Presenter is armed.

### GET /ib/graph/changes

Query: `since` (optional, opaque, at most 400 characters; longer is 422). Any employee role. Main session
and Compare slots (proxy: already in `ALLOWED` and `SLOT_ALLOWED`).

200, always exactly these keys:

```json
{"cursor": "<opaque>", "nodes": [GraphNode], "edges": [GraphEdge]}
```

- Nodes: a `doc` node per changed document the caller can open NOW (new in the index, or a new version),
  its `container` and `platform` nodes, its author's `person` node, and documents it links to or mentions
  that the caller can open now. Same shapes as `/ib/graph`; `count`, `degree_hidden_safe` and `more` are
  `null` (keep the numbers you already have).
- Edges: `contains` (platform → container, container → doc), `authored` (person → doc), `links` and
  `mentions` between nodes in this reply only.
- No cursor, or one that is malformed, forged, someone else's or older than 10 minutes: a NEW window,
  `nodes: []`, `edges: []`, a fresh cursor, nothing back-filled.
- Deletions and revocations are never sent; the next full `/ib/graph` load shows them.
- A change the caller cannot open is indistinguishable from no change (same keys, empty lists, the same
  cursor for the same clock). No counts, no sequence numbers, no reasons.
- Ledger: `graph.viewed` with `mode: "changes"` (counts only) when a window opens and for each non-empty
  reply, before the reply. 401 / 403 / 503 as usual; never a 404 while mounted.
- Client: poll every 2 s (Stage theme) or 3 s; stop on 401/403/404 and while the tab is hidden or the
  viewer paused; back off on 503 (x2 up to 30 s).

### POST /sim/add-document

Body (closed; no author field, ever):

```json
{"platform": "slack", "target": "C0DBMIG", "text": "1..2000 characters"}
```

(`platform: "confluence"` with a `title` ≤ 120 exists for `admin_it`; the stage UI offers Slack only.)
`target` matches `^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$`; HTML tags are stripped; empty after stripping is 422.

200: `{"ok": true, "doc_id": "slack:C0DBMIG:<ts>", "platform": "slack", "container": "C0DBMIG", "at": "<iso>"}`.

Refusals: every one (demo sign-in off, deployed, role, unknown or unreadable channel, not a member at the
source now, no platform account, source unreachable) is `404 {"detail": "not found"}`; more than 5 posts
per person per minute is `429 {"detail": "too many requests"}`. Reasons go to the ledger only
(`request.failed`, `stage: "sim.add-document"`). A successful post writes nothing to the access ledger:
the note reaches the index through the mock's signed webhook, masked, and every later read is ledgered by
its own read path. `/sim/reset` removes it from the source and the index.

### POST /sim/check-absent

Body `{"phrase": "3..80 characters"}`. `admin_it` only, demo sign-in on, 10 per minute.
200: `{"phrase", "documents_containing": <int>, "checked_at", "index_size": <int>}` over the WHOLE index
(ignores ACLs, hence operators only). Ledgered write-ahead as `audit.query` with
`filter: {"op": "sim.check-absent", "phrase_chars", "phrase_sha256"}`. Refusals: the same 404; 429.

### POST /sim/engine-refresh and POST /sim/engine-status

Body `{}`. `admin_it` or jdoe (the snapshot's person), demo sign-in on; others get the 404. Both are POSTs
so the proxy's existing `sim/<name>` rule covers them.

200, both routes:

```json
{"state": "idle | running | done | failed", "step": "building | publishing | reloading | null",
 "started_at": "<iso> | null", "finished_at": "<iso> | null", "snapshot_at": "<iso> | null",
 "reloaded": true | false | null}
```

- `engine-refresh` starts a background job (about a minute): `scripts/build_enterprise_graph.py --as jdoe`
  into a side file, then an in-place SQLite online-backup copy into `var/graph/enterprise.db`, then the
  IB calls the gateway's `POST /graph/reload` server-side. While a job runs it returns that job's status;
  a second start within 2 minutes of the last is `429 {"detail": "too many requests"}`. A start is
  ledgered write-ahead (`ingest.sync`, `detail.reason: "engine.refresh"`); a ledger failure is 503.
- `engine-status` reads the status. The UI polls it every 2 s while `state` is `running`.

# v4 (30 Oct): Deliverables from an answer (`/ib/deliverables*`, D-25)

Code: `databridge_ib/deliverables/` (`routes.py`, `service.py`, `verify.py`, `evidence.py`, `build.py`,
`export.py`, `model.py`, `spec.py`, `prompts.py`). Tests: `tests/test_deliverables.py`,
`tests/test_deliverables_verify.py`, `tests/test_deliverables_eval.py`. Security: `docs/tencent/SECURITY.md`
§15.14. Plan, reviews and status: `docs/finals/deliverables/`. The general rules at the top of this file
apply (401, 403, the one 404, the one 503).

**Flag.** `IB_DELIVERABLES=1` (kill switch; off by default in code, on in `scripts/demo_up.sh` and
`scripts/run_local_demo.sh`; read at start-up, so turning it off needs an IB restart). The routes are
always mounted. Off: both routes answer `404 {"detail": "not found"}` (the same bytes as not-found) after
the 401/403 checks, and nothing is recorded.

**`GET /ib/status`** gains:

```json
"deliverables": {"enabled": true, "model": false, "kinds": ["table", "chart"],
                 "exports": {"csv": true, "pptx": true}}
```

`kinds` lists `analysis` only when a model chain is configured; `[]` when off. `exports.pptx` is true only
when python-pptx and XlsxWriter are installed (not in the Lighthouse image).

## Proxy allowlist (v4 deliverables)

| Method | Path | Slot (compare/View-as) |
|---|---|---|
| POST | `/ib/deliverables` | **no** (main session only) |
| POST | `/ib/deliverables/export` | no |

One pattern: `^ib\/deliverables(\/export)?$`. For `ib/deliverables/export` with status 200 only, the
proxy relays `content-disposition` when it matches
`^attachment; filename="walnut-[a-z]+-[0-9a-f]{8}-\d{8}\.(pptx|csv)"$` exactly, plus `nosniff`.

## Status codes (both routes)

| Status | Body | When |
|---|---|---|
| 200 | below | |
| 401 / 403 | | no valid session / not an employee role |
| 404 | `{"detail": "not found"}` | ONE answer for: flag off, someone else's request, an unknown or malformed id, a refused or failed answer, an answer none of whose cited documents the person can open now, someone else's / unknown / expired deliverable (90 min), an export where nothing passes the re-check |
| 422 | | a body with an unknown key (any identity field), a bad `kind`/`chart`/`format`; a `format` the (owned) deliverable does not offer. The owner check runs first, so a stranger gets the 404 |
| 429 | `{"detail": "too many requests"}` | more than 10 creates or 20 exports per person per minute (recorded as `deliverable.failed`, stage `rate`, no ids) |
| 503 | `{"error": "temporarily unavailable"}` | the ledger could not record the call (nothing shown or exported), the sources could not be checked, a file over its cap, a `.pptx` build error |

## POST /ib/deliverables

Request: `{"req_id": "<one of MY request ids>", "kind": "table" | "chart" | "analysis", "chart": "auto" | "bar" | "line"}`
(`chart` optional, default `auto`, used only by the model path). Closed body (`extra="forbid"`).

Response 200 (an offline chart; abridged):

```json
{
  "deliverable_id": "9b0c…(uuid4 hex)", "req_id": "…", "kind": "chart", "status": "ready",
  "label": "Draft for your review", "title": "FX margin by market", "generated": "deterministic",
  "model": {"provider": "deterministic", "model": "source-chart-v1", "fallback_used": false},
  "check": {"values_proposed": 0, "values_kept": 0, "values_dropped": 0, "items_proposed": 0,
            "items_kept": 0, "items_dropped": 0, "names_dropped": 0},
  "content": {"type": "bar", "drawn_as_bar": false, "x_label": "market", "y_label": "FX margin",
              "unit": "percent", "currency": null, "from_tag": "C2",
              "series": [{"name": "FX margin", "points": [
                {"x": "SG", "value": 0.35, "raw": "0.35% FX margin", "tag": "C2"}, …]}]},
  "citations": [{"tag": "C2", "n": 2, "doc_id": "drive:df-3", "platform": "drive", "kind": "file",
                 "container": "…", "container_name": "…", "title": "…", "version": "3",
                 "updated_at": "…", "deep_link": "…", "cited_version": "3",
                 "changed_since_answer": false, "checked_at": "…"}],
  "exports": ["csv", "pptx"], "fallback": "no_model", "prompt_version": "ib-deliver-2026-10-31.1",
  "scope_note": "…", "as_of": "…"
}
```

- `status`: `ready`; `needs_model` (Analysis with no model, or nothing the model wrote passed);
  `insufficient` (Table or Chart: nothing in the cited sources to build one). `content` is `null` unless
  `ready`, and `exports` is `[]`.
- `generated`: `model`, `deterministic` or `none`. `model.model` for deterministic builds:
  `source-table-v1` (a CSV-shaped block of a cited source, `content.source: "source_table"`),
  `quoted-table-v1` (quoted source sentences, `content.source: "cited_facts"`), `source-chart-v1`.
  `fallback`: `none`, `no_model`, `timeout`, `busy`, `invalid_output`, `model_error`, `nothing_passed`.
- `content` per kind: table `{source, from_tag?, columns[], rows[][{text, tags[], numeric}], truncated}`
  (at most 6 columns, 20 rows); chart as above (`type` `bar`/`line`; a line whose x values are not all
  dates or periods is drawn as a bar, `drawn_as_bar: true`); analysis
  `{sections: [{heading, sentences: [{text, tags[]}]}]}`.
- Tags are the ANSWER's own tags; `n` is the answer's numbering. `check` counts what a MODEL proposed
  (all zero for deterministic builds).
- Ledger: `deliverable.requested` before the live source checks and any model call;
  `deliverable.created` before the response.

## POST /ib/deliverables/export

Request: `{"deliverable_id": "<one of MY deliverables>", "format": "csv" | "pptx"}`.

Response 200: the file bytes. Headers: `content-type` `text/csv; charset=utf-8` or
`application/vnd.openxmlformats-officedocument.presentationml.presentation`;
`content-disposition: attachment; filename="walnut-<kind>-<req_id[:8] hex, else 00000000>-<yyyymmdd>.<csv|pptx>"`;
`x-content-type-options: nosniff`; `cache-control: no-store`.

- Built on the server from the stored, checked deliverable after a live re-authorisation and re-check
  against the sources as they are now (items whose source was revoked or changed are removed). Never
  from anything the browser sends.
- CSV: UTF-8 with BOM, CRLF; the table (plus a `Sources` column of `[n]` markers) or the chart's data
  (`Series, Label, Value as written, Value, Unit, Source`), a blank line, then one row per source. Cap
  256 KB.
- `.pptx` (16:9): title slide, the table (10 rows per slide) or a native, editable chart, a Sources slide;
  speaker notes list the sources and what was checked. Cap 2 MB.
- Every cell and string passes the formula guard (leading `= + - @`, tab, CR, LF, full-width forms, also
  after Unicode spaces, get a `'`; plain numbers such as `-0.5%` do not).
- Ledger: `deliverable.exported` before the bytes.

## Ledger event kinds (v4 deliverables)

| Kind | Fields (besides `kind`, `req_id`, `event_time`, `synthetic_replay`) |
|---|---|
| `deliverable.requested` | `sub`, `deliverable_id`, `deliverable` (kind), `model_planned`, `policy_version` |
| `deliverable.created` | `sub`, `deliverable_id`, `deliverable`, `status`, `generated`, `model`, `prompt_version`, `cited_docs`, `doc_ids`, `context_chars`, `context_digest` (SHA-256 of what the model was shown), `items_*`/`values_*`/`names_dropped` counts, `fallback`, `digest` (SHA-256 of the canonical JSON of the response's `deliverable_id, kind, status, title, generated, model, check, content, citations`), `policy_version` |
| `deliverable.exported` | `sub`, `deliverable_id`, `deliverable`, `format` (`csv`/`pptx`), `bytes`, `digest` (SHA-256 of the file), `items_dropped_on_recheck` |
| `deliverable.failed` | `sub`, `deliverable_id?`, `deliverable?`, `stage` (`lookup`/`rate`/`generate`/`export`), `reason_code` (ledger only). A failed lookup or a rate refusal has no `req_id` and no `deliverable_id` |

Never a title, cell, label or sentence. They appear in `/audit/events`, the request's journey, the
asker's receipt and `/audit/stats` (totals `deliverables_requested`, `deliverables_created`,
`deliverables_exported`, `deliverables_failed`).
