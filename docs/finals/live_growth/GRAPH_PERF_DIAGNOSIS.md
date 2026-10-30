# Why Jordan's graph takes about 25 s after each sign-in

Read-only diagnosis, 30 Oct 2026, branch `final_v1.2` (working tree). Source issue: `docs/finals/demo_day/STAGE_QA_30OCT.md` §4 `?grow=1` and §7 risk 1. No product code was changed. The shared stack (3100/8100/8200/8300) was not started, stopped or called. Every number below comes from an in-process run (real mock platforms app, real connectors, real IB app, `TestClient`, no sockets). The scripts are in the session scratchpad (`graphperf/measure.py`, `measure2.py`, `measure3.py`).

## 1. The answer in one paragraph

Each time the graph page mounts, the browser asks for **two** views at once, `/ib/graph?limit=300` (the overview) and `/ib/map`. **Each view checks live, at the source, every document Jordan's stored permissions allow: 930 documents, so 1,860 Gate 2 checks per page load.** A Slack thread check costs 4 HTTP calls, and 3 of them fetch facts that are the same for every thread (who Jordan is, the channel, its members). In total that is **5,956 HTTP calls to the mock platforms for one page load.** Nothing is reused between page loads, and by design Gate 2 must not be. Signing in again remounts the page, so the full cost comes back every time. Warming Jordan's graph earlier does nothing for the later mount. The cost is **not** a per-session-token cache problem. Gate 1 is already cached per principal and ACL version, and it takes 6 ms.

## 2. The cause, with file:line

1. **The overview checks the whole readable set, then shows no documents.** In `graph.py:146-148`, overview mode sets `universe = g1.recent(g1.rows)`, which is every Gate 1 document, with `cap = MAX_CHECKS_OVERVIEW` = 1,500 (`graph.py:55`). The loop at `graph.py:206-214` runs `authz.authorize` over all of them. For Jordan the result has 54 nodes (platforms, containers, people) and **0 document nodes**: the 930 checks are only there to make the counts and the "who wrote where" exact.
2. **The map checks the same 930 documents again.** `knowledge_map.py:41-46` calls `authz.authorize(user, sorted(readable), ...)` on its own pool (`_MAP_POOL`, 8 workers, 45 s budget, `knowledge_map.py:33-35`).
3. **The browser runs both at the same time on every mount.** `components/graph/GraphExplorer.tsx:135-144` uses `Promise.all([src.graph({ limit: OVERVIEW_LIMIT }), src.map()])` with `OVERVIEW_LIMIT = 300` (`:32`). The effect re-runs whenever `src` changes, which includes a new sign-in (`:177`). Compare mode does the same for the right-hand person (`CompareView.tsx:51`). For Ken this costs little, because he can read 24 documents.
4. **A Slack check repeats per-principal and per-channel facts for every thread.** `connectors/slack.py:331-394` calls `users.info` for the asker (`:346`), `conversations.info` (`:359`), `conversations.members` (`:367`, paged), and then `conversations.replies` for the thread (`:375`). Only the last call is about the thread itself. Jordan has 559 Slack threads, so that is 2,236 calls per pass, of which 1,677 repeat what earlier calls already fetched.
5. **Gate 2 is uncached, and that is correct.** `gates.py:3-4` says: "there is no cache on this path (revocation is honoured on the next query)". `authz.py:41` (`VIEW_POOL`, 6 workers) and `authz.py:45` (`VIEW_CHECK_TIMEOUT_S = 20.0`) bound the overview.
6. **Why the slow part comes back after each fresh sign-in.** There is no per-token cache to lose. Every mount pays the full cost. The grow step signs `jdoe` in again (`components/presenter/script.ts`, grow step `ctx.signInMain('jdoe')` then `ctx.go(GROW_URL)`), so the explorer remounts and pays again. The only client cache, the layout, is cleared on identity change (`components/graph/layout.ts:10`), but the layout takes about 10 ms (`layout.ts` comment at the worker fallback), so losing it does not matter.

Ruled out:

- **Gate 1.** `Index.readable` (`index.py:289-298`) is already cached by `(user.id, active, kind, groups, email, directory_epoch, acl_version)`. That is per principal, not per token. The cache is cleared on every `_bump` (`index.py:271-274`: upsert, `set_acl`, delete). A cold computation takes 6 ms for Jordan.
- **Graph assembly.** The rest of the graph code, with Gate 2 answered from memory, takes 3 ms for the Gate 1 view, 18 ms for `build_graph` and 9 ms for `build_map`.
- **Client layout.** About 10 ms on the main thread. The worker fallback is capped at 3 s (`layout.ts` `WORKER_DEADLINE_MS`).
- **Live polling.** `/ib/graph/changes` checks only documents in the change window (`live.py:240-251`). Its cost is small and does not depend on the size of the readable set.

## 3. Measurements (in-process, Jordan = `jdoe`, 1,204 indexed documents)

| What | Time | Gate 2 checks | HTTP calls to mock |
|---|---|---|---|
| Gate 1 `readable` (cold) | 6 ms | 0 | 0 |
| `/ib/graph?limit=300` alone (2 runs) | 6.0 s, 7.7 s | 930 | 2,978 |
| `/ib/map` alone | 4.8 s | 930 | 2,978 |
| **graph + map at the same time (what the page does), 4 runs** | **11.0, 9.5, 9.2, 8.8 s** | **1,860** | **5,956** |
| Ken (`ken.ext`), readable set | n/a | 24 | small |

HTTP calls per page load, by endpoint (graph and map together):

| Endpoint | Calls |
|---|---|
| Slack `users.info` | 1,118 |
| Slack `conversations.info` | 1,118 |
| Slack `conversations.members` | 1,118 |
| Slack `conversations.replies` | 1,118 |
| Jira `issue/{key}` + `permissions/check` | 434 + 434 |
| Confluence `permission/check` + `pages/{id}` | 218 + 218 |
| Drive `files/{id}` + `permissions` | 90 + 90 |
| **Total** | **5,956** (Slack is 75 %) |

Every one of the 930 checks returned `verified/ok`, so the work is not caused by drift or read-through.

On stage, QA measured about 25 s. The stage uses real sockets and a separate mock process, and other work was running at the same time (Ken's compare pane, live polls, the presenter's warm-ups). If the stage cost is dominated by the mock, 25 s for 5,956 calls is about 4.2 ms per call. This is **derived, not measured**: I did not touch the stage. It is consistent with the in-process ratio. A second risk follows from it. The overview's deadline is 20 s (`authz.py:45`). On a slow stage it may already be running out, which leaves `complete: false` and null counts on Jordan's canvas. Confirm this by checking the `graph.viewed` ledger rows from the stage QA run for `docs_checked < 930`.

## 4. The fix

### Fix 1 (main): one live pass for the overview and the map

Add `GET /ib/galaxy` (or `GET /ib/graph?with=map`). It runs **one** `authz.authorize` over the Gate 1 set, under one deadline, on the map's 8-worker pool. It builds **both** existing response bodies from the same immutable `AuthorizedDoc` snapshots.

- **IB.** Split `knowledge_map.build_map` into a "check" part and a "shape from snapshots" part. Give `graph.build_graph` an optional pre-authorised `(ok, checked, undecided)` for overview mode. Its counts stay exact only when nothing is undecided, exactly as today (`graph.py:224`, `:264-277`). Append `map.viewed` and `graph.viewed` (mode `overview`) **before returning**. If either append fails, the route returns 503 with no body. Both audit event types stay unchanged, so audit stats and verify do not change. Keep `/ib/graph` and `/ib/map` as they are for other callers.
- **Web.** `GraphExplorer.tsx:135-144` and `CompareView.tsx:51` make one `galaxy()` call instead of the `Promise.all`. Add the matching mock-mode route next to `lib/mock/extra/graph.ts`.
- **Effect.** 1,860 checks become 930, and 5,956 HTTP calls become 2,978. Measured in-process: one `authorize` pass over the 930 documents took 5.4 s and 9.1 s (noisy machine), against 8.8 to 11.0 s for the current pair. **Expected on stage: about 12 s instead of about 25 s.**
- **Hard rules.** Gate 2 is still live, for every document, in the request that shows it. Any error, timeout or 429 is still a deny (unchanged `authorize`). Restricted ≡ non-existent still holds: only confirmed documents are shown or counted, in both bodies. Write-ahead holds: both ledger rows are written before the response, and a ledger failure gives a 503.
- **Risk: low.** This is a refactor of two existing builders onto one input. The main thing to get right is the deadline. One budget must serve both bodies: use about 30 s, the overview's `complete` flag must stay honest, and the map must leave out anything undecided, as it does today.

### Fix 2 (multiplier): batch-scoped facts for Slack Gate 2

Inside one `gates.check_all` call, fetch each principal-level and container-level fact **once**: `users.info(asker)`, `conversations.info(channel)` and `conversations.members(channel)`. Then reuse it for every thread of that channel in that batch. `conversations.replies` stays per thread, because it carries the version.

- **How.** `check_all` creates a fresh `BatchScope` (a lock plus a dict of futures, so concurrent workers share one in-flight call) and passes it to `check_access(..., scope=scope)`. The base signature in `connectors/base.py:50` gets an optional `scope=None`, so other connectors are unaffected. The scope is never stored, so it dies with the call. It is never shared between principals, calls or requests. The `users.info` key includes the native id.
- **Failure.** If a scoped fetch raises `SourceFailure`, the scope keeps the exception. Every thread in that channel is then denied with the same reason code. This is the same outcome as today, where each thread fails on its own.
- **Effect.** Slack falls from 4 calls per thread to about 1 per thread plus 2 per channel. Measured in-process with a scratch monkeypatch (no product change): graph and map at the same time took 6.6 s and 4.8 s with 2,640 calls. **Fix 1 and Fix 2 together took 3.4, 3.0, 2.9 and 2.4 s with about 1,440 calls.** Both bodies were byte-identical to the baseline apart from `as_of`. **Expected on stage with both fixes: about 6 s.**
- **Hard-rule question (needs a founder or reviewer decision).** This does not cache Gate 2: every fact is read from the source within the same request. It does change the staleness bound *inside* one batch, though. Today a membership change that lands halfway through a 930-document batch is seen by the threads checked after it. With the scope, the threads in that channel use the membership read at the start of the batch. That bound is at most the batch's own deadline (20 to 45 s), the same order as the spread of `checked_at` within one response today. If the founder reads "live source check" as "per document, per call", ship Fix 1 alone and keep Fix 2 off the ask path. An option is to enable the scope only for the developer views, through a `check_all(..., share_facts=True)` flag.
- **Risk: medium.** It touches the Gate 2 path in a connector. Run it through the usual 5-model review before merge.

### Fix 3 (already in the working tree): stage timeout

`components/presenter/script.ts:240-241` now waits 60 s for `graph.addNote` instead of 20 s, so the step no longer fails. It still shows "Working" for about 25 s until Fix 1 lands, and about 6 s with Fixes 1 and 2.

### What is safe and what is not

| Idea | Verdict | Why |
|---|---|---|
| Gate 1 cache per principal + `Index.acl_version` (+ directory epoch) | **Safe, and already there** (`index.py:289-298`) | Gate 1 only *bounds* the candidates. Gate 2 still runs on each one. The cache is invalidated by `_bump` on every upsert, ACL change and delete (`index.py:271-274`): live growth's added note goes through the ingest upsert, and permission webhooks go through `set_acl`/upsert. Directory changes bump `directory.epoch` (`directory.py:96`, `ingest.py:106,135`). The key includes groups, kind, active and email, so a changed principal misses the cache. **Do not add a TTL:** it saves nothing (6 ms) and only adds staleness. |
| Caching the `_Gate1View` structure under the same key | Safe, but saves only about 3 ms | Same key and same invalidation. Not worth the code. |
| Caching Gate 2 verdicts or `AuthorizedDoc` snapshots per principal + `acl_version` (+ TTL) | **Not safe** | `acl_version` moves only when IB *learns* of a change. A revocation at the source whose webhook is lost or late (the case Gate 2 exists for: `tests/test_seed_silent_revocations.py`) leaves the key unchanged. A cached "allowed" would then show a revoked document's node or count. That breaks rule 1 (live check) and rule 4 (restricted ≡ non-existent). Any TTL above zero is a window of exactly that kind. |
| Caching per session token | **Not safe, and no help** | It has the same leak window as the row above, and it is lost on the re-sign-in that causes the slowness. |
| Client-side reuse of a warmed overview + map across a sign-in | Not recommended | The shell clears identity-scoped caches on `ib:identity-changed` on purpose. Reusing them would show a graph checked at an earlier `as_of` as if it were current. |
| Sharing one in-flight `authorize` between concurrent graph and map calls (single-flight) | Works, but Fix 1 is simpler | Fix 1 gives the same saving without a cross-request coordination primitive. |

## 5. Tests to add

Fix 1 (`apps/internal_brain/tests/test_dev_views.py` or a new `test_galaxy.py`, on the `e2e_world.E2E` harness):

1. `/ib/galaxy` equals `{overview: /ib/graph?limit=300, map: /ib/map}`, ignoring `as_of`, for `jdoe`, `ken.ext` and one guest.
2. Exactly one `check_access` per readable document per request (a counting wrapper, as in `test_live_growth.py:132`).
3. Write-ahead. With the ledger down the route returns 503 with no body and makes no source call after the failure. With the ledger up, both `map.viewed` and `graph.viewed` (`mode="overview"`) are appended before the response.
4. Silent revocation (webhook not delivered, `E2E.deliver = False`). The document, and its contribution to every count, is absent from both bodies.
5. P2 differential. Ken's galaxy is byte-identical in a world with a restricted document and in a world without it.
6. Deadline. `authorize` is patched to leave some documents undecided. The overview then has `complete: false` and null counts, and the map leaves those documents out.
7. Web. One network request per explorer mount (component test). Compare mode makes one request per side.

Fix 2 (`tests/test_connector_slack.py` plus a gates test):

1. Within one batch, `users.info`, `conversations.info` and `conversations.members` are called once per key, and `conversations.replies` once per thread.
2. **No reuse across batches.** Remove a member between two `check_all` calls, and the second call denies.
3. Failure fans out closed. A 500 or 429 on `conversations.members` denies every thread in that channel with the source reason code, and other channels are unaffected.
4. Single-flight. Two workers asking for the same key concurrently produce one HTTP call, and both get the result.
5. A new scope per `check_all`, never shared between principals: two users in back-to-back batches produce separate `users.info` calls.
6. The existing Slack connector suite, the leak eval (`databridge/evals/internal_brain/`) and the pipeline scenario tests all stay green, with the same verdicts.

## 6. Estimate

| Work | Effort |
|---|---|
| Fix 1, IB (refactor + route + tests 1 to 6) | 2 to 2.5 h |
| Fix 1, web (API, GraphExplorer, CompareView, mock route, test 7) | 1 to 1.5 h |
| Fix 2 (scope, Slack connector, tests, review round) | 2.5 to 3 h, plus a founder decision |
| Re-run the grow walk-up on stage, after `demo_day.ps1 start -Rebuild` | 15 min |

Recommendation: ship Fix 1 before Demo Day. It halves the wait, needs no change to any hard rule, and its tests are mostly comparisons with the two existing views. Do Fix 2 only if the founder agrees that "read once per batch, within the same request" still counts as a live source check. Keep the 60 s timeout either way.
