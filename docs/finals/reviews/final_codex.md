# Finals correctness review

Reviewed 2026-10-30 on `final_v1.0`, HEAD `8582f4b35bdf95ac236b1ae6413910786fdc3191`, against `4403694`. Paths and line numbers below refer to current HEAD. Reviewed the requested diff, concentrating on the named backend modules, IB proxy, Compare, presenter, and answer/progress flow. Existing screenshot/test working-tree changes were left untouched; reviewed source paths had no working-tree differences.

## Ranked findings

1. **high — `databridge/apps/internal_brain/databridge_ib/graph.py:193` — Graph edges disclose relationships removed from the authorized document version.**

   **Finding:** `_Gate1View` reads indexed links before live checks (`graph.py:87`, `:123`). Authorization can fetch and return a newer, masked document with different links (`authz.py:98`, `:109`), but graph construction still uses `g1.out` for every document-to-document edge. Consequently a response can advertise version 2 while exposing a relationship present only in version 1. Authorization of both endpoints does not authorize obsolete relationship evidence. The stale neighborhood also drives focus selection and exactness (`graph.py:200`, `:312`).

   **Repro:** Index readable documents A and B, with A version 1 linking to B. Change A at the source to version 2 with that link removed, without ingesting the change. Request `/ib/graph?focus=A`; let both documents pass live checks and A complete read-through. The response still includes A → B. An isolated execution of the actual `build_graph` function with old index links and authorized version-2 snapshots with empty links returned `jira:A → jira:B`, kind `mentions`.

   **Fix (minimal):** Build output link edges from each `AuthorizedDoc.links`, resolving targets only within the authorized snapshots. Derive counts/exactness from that graph; omit exact counts when newly discovered neighbors have not been checked. Do not reuse pre-check relationship evidence for output.

2. **high — `databridge/apps/internal_brain/databridge_ib/app.py:269` — Public SSE restores the fine-grained denial timing channel removed from trace.**

   **Finding:** Stage callbacks are immediately queued and streamed. `pipeline.py:155` emits `gate1` before source checks and `pipeline.py:178` emits `gate2` after them. A no-candidate query skips the checks (`pipeline.py:161`), whereas a stale indexed candidate that is now forbidden performs them. A browser can measure the interval between these events even though both answers and all stage names are identical. The proxy deliberately passes the events through unbuffered (`app/api/ib/[...path]/route.ts:242`). Rounding trace latency does not protect this newer route.

   **Repro:** In paired worlds, keep a unique document in the index but revoke source access in one world; remove the document entirely in the other. Ask its unique identifier through `/ib/ask/stream`. With a controlled source-check delay, record the `gate1`→`gate2` interval: only the stale-denied world incurs that delay. **Hypothesis:** the separation is statistically exploitable with the actual demo connectors/network; no live timing-distribution experiment was performed. The stage-specific timing disclosure itself follows directly from the code.

   **Fix (minimal):** Stop emitting permission-sensitive completion boundaries to ordinary callers; use fixed-schedule public progress. Keep real stage timing compliance-only. Separately test and normalize denied-versus-absent total response timing; removing these events alone does not prove timing equivalence.

3. **medium — `databridge/apps/internal_brain/databridge_ib/docview.py:65` — Live-denied links consume the public link budget.**

   **Finding:** The first 12 Gate-1-readable targets are selected before live authorization. Targets that subsequently fail Gate 2 are removed without replacement. Thus stale restricted objects change the visible link list. Graph selection has the same issue at its work cap (`graph.py:157`): candidates denied live can crowd permitted neighbors out of the checked universe, and `truncated` only describes the later output order (`graph.py:260`). This contradicts the promised restricted-equals-absent behavior and can hide a valid neighbor without a useful pagination signal.

   **Repro:** A readable document links, in order, to D1…D12 and B. Leave all targets readable in the index, revoke D1…D12 at their sources, and keep B allowed. `/ib/doc/A` returns no links. Remove D1…D12 from the readable universe and repeat: it returns B. For graph focus, place more than 400 stale-denied neighbors ahead of an allowed neighbor.

   **Fix (minimal):** Apply the public result cap after live authorization, continuing bounded batches to fill it. If the available budget cannot support the non-existence guarantee, omit the affected optional relationship list/count rather than implying a complete visible neighborhood. A larger fixed pre-check cap alone does not fix the invariant.

4. **medium — `databridge/apps/internal_brain/databridge_ib/authz.py:148` — The view timeout does not bound read-through work.**

   **Finding:** `timeout_s` bounds only `gates.check_all` (`authz.py:138`). Afterwards snapshots are processed serially, and every drifted document performs a synchronous `fetch` plus ACL lookup (`authz.py:98`, `:102`) without a shared remaining deadline. Graph overview can authorize 1,500 candidates (`graph.py:44`). Doc view can additionally run a separate linked-object authorization batch (`docview.py:67`). The proxy aborts the whole request at 90 seconds, so a valid graph/source request can fail in the UI while backend work continues.

   **Repro:** Have 100 readable candidates immediately report version drift; make each otherwise successful fetch/ACL pair take one second. Call graph overview with `timeout_s=20`. The post-check loop alone takes about 100 seconds, exceeding both the advertised check budget and the proxy deadline. **Hypothesis:** enough concurrent drift occurs during a real demo to trigger this; no live load test was run.

   **Fix (minimal):** Carry one monotonic deadline through checks and read-through, bound connector I/O by its remaining budget, and use a bounded pool for snapshot fetches. Return no unchecked content when the budget expires.

5. **medium — `databridge/apps/internal_brain_web/components/compare/CompareView.tsx:153` — Failed Compare runs are reported as successful presenter steps.**

   **Finding:** `askSide` converts login, ask, and identity-check failures into error sides (`:44`). Lock failure also produces error sides (`:132`). Nevertheless `run` always resolves `true`, including when neither answer succeeded. The presenter handler forwards that result (`:164`), so `script.ts:95` accepts the step and `controller.ts:63` clears busy without setting failed. Subsequent citation/journey steps may use request IDs left over from an earlier rehearsal because failed runs do not replace or invalidate them (`CompareView.tsx:142`).

   **Repro:** Start presenting and make one slot's login return 403 or its stream return an error event. Advance to Amy/Rachel Compare. The column shows an error, but the presenter reports the step complete and permits advancing as if it succeeded. After an earlier successful rehearsal, the remembered Rachel request can still refer to that earlier answer.

   **Fix (minimal):** Distinguish “handler not ready” from “run completed unsuccessfully.” Propagate a completed-run failure to the controller without making the bus automatically retry it; invalidate affected remembered request IDs at run start. Return success only when both sides completed successfully.

6. **medium — `databridge/apps/internal_brain_web/components/presenter/controller.ts:60` — Superseded presenter actions can still navigate or change sessions.**

   **Finding:** `runId` guards only the final state update, not the action itself. `ctx().go`, `signInMain`, and page calls have no generation/cancellation check (`:30`). P can call `start()` while another step is busy (`:175`), and reset increments `runId` but does not cancel earlier actions (`:79`). An earlier action awaiting login or a page handler can resume after reset/new navigation. Reset also logs slots out without acquiring the locks held by active Compare requests (`:81`).

   **Repro:** Delay the first title step's `whoami`/sign-in, restart or begin a newer step, then release the earlier operation. An in-memory execution of the actual controller/script modules ran step 0 with delayed identity resolution, completed step 8, then released step 0: store remained at step 8 while navigation was `['/present/proof', '/present']`. In a UI rehearsal, reset during a pending sign-in or Compare run can similarly leave the page/session inconsistent with presenter state.

   **Fix (minimal):** Bind every action context to its generation and check it after each await and before navigation/session/page mutations. Serialize session-changing actions; coordinate reset with Compare slot locks and invalidate pending Compare results. Disabling restart/reset while busy is a smaller interim mitigation.

7. **medium — `databridge/apps/internal_brain/databridge_ib/audit/tamper.py:305` — Failure to create a temporary directory permanently locks the tamper demo.**

   **Finding:** `_RUN_LOCK` is acquired at `:302`, but `tempfile.mkdtemp` runs before the `try/finally` that releases it (`:306`, `:331`). A temporary-directory failure makes the first call fail and every later call report “already running,” even after storage recovers. The service must restart to recover the demo feature.

   **Repro:** Make `mkdtemp` raise `OSError` once (disk full, unavailable temp directory, or a test double), then call `run_on_live_copy` again. Isolated execution of the actual function reproduced first-call `OSError` with the lock held and second-call `TamperDemoBusy`. No temporary directory was created by this review.

   **Fix (minimal):** Enter the release-protecting `try/finally` immediately after lock acquisition. Initialize `tmp` to `None`, create it inside the try, and conditionally clean it up before releasing the lock.

## Plan review status

Mapping the ten numbered findings in `docs/finals/reviews/plan_codex.md`; “partial” means part of the original finding remains, not that the fix was untested.

| # | Earlier finding | Status | Current HEAD evidence |
|---|---|---|---|
| 1 | Own trace exposes retrieval documents and lacks current authorization | **fixed** | `databridge/apps/internal_brain/databridge_ib/app.py:347` selects actual citations, `:352` authorizes them live, `:355` emits only authorized metadata. `:343` checks ownership before reconstruction. Retrieval `released` is absent from the public DTO. |
| 2 | Locked-node comparison violates non-existence | **fixed** | `databridge/apps/internal_brain_web/components/graph/CompareView.tsx:44` loads an independently authenticated graph; `:80` checks only left-visible IDs for shared marking. No union/locked-placeholder graph is constructed. This fixes the specific proposed locked-node behavior, not the separate stale-edge issue above. |
| 3 | Fine-grained trace timings and global sequence numbers | **partial** | `databridge/apps/internal_brain/databridge_ib/app.py:364` rounds total latency; `:366` returns stage statuses and `:370` uses request-local ordinals. However the new real-time stage stream (`:269`, `:287`; `pipeline.py:155`, `:178`) restores stage-duration observability. Timing equivalence remains unverified. |
| 4 | Map-style authorization is not version-safe for graph/doc | **partial** | New `authz.py:94`–`:124` fetches drifted content, checks fresh ACL/version, and snapshots; `docview.py:45` uses it. But `knowledge_map.py:43` still accepts `.allowed` and emits index metadata at `:47`, while graph edges retain old evidence at `graph.py:193` (finding 1). |
| 5 | Stats/journey failures and trace reads bypass auditing | **partial** | `audit/routes.py:283` and `:297` ledger stats failures; `:309` uses `_sync_or_refuse` for journey. Trace success/not-found reads append `TraceViewed` (`app.py:333`, `:372`), but reconstruction/authorization exceptions reach the generic 503 at `:375` without a best-effort failure event, even when the ledger is writable. |
| 6 | Every refresh verifies/materializes the whole ledger | **open** | `audit/routes.py:279` still materializes strict events before cache lookup; `:287` keys cache by head, which the successful query append at `:298` changes. `audit/ledger.py:289` verifies the complete log; `audit/query.py:218` sorts all events and builds full sequence maps. Added cache does not eliminate repeated verification or reliably coalesce computations. Demo latency impact remains a load-test hypothesis. |
| 7 | Replay failures lose replay flag/event time | **fixed** | `databridge/apps/internal_brain/databridge_ib/pipeline.py:116` creates `RequestFailed` with the supplied event time at `:118` and `synthetic_replay=replay` at `:119`. |
| 8 | Stats window lacks an upper bound | **fixed** | `databridge/apps/internal_brain/databridge_ib/audit/stats.py:73` rejects events at or after `now`, implementing the documented half-open window. `_when` normalizes naive event timestamps at `:32`. |
| 9 | Missing graph/doc and Compare proxy contracts | **fixed** | `databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts:35`–`:37` allow main graph/doc; `:57`–`:58` allow slot graph/openable; `:55` allows slot streaming. Slot doc remains intentionally unavailable: `components/compare/CompareView.tsx:362` uses live doc only for matching main identity, otherwise `:377` disables passage fetching. This is a deliberate reduced contract rather than an active 404 integration bug. |
| 10 | Waterfall omits durable release work | **partial** | `databridge/apps/internal_brain/databridge_ib/pipeline.py:218` now stores the answer before timing capture and `:225` adds `release`. `:221` documents that timings stop before the final ledger append. `:232` still computes total latency before that append completes, so these values remain unsuitable as full response latency. |

**Plan status totals: 5 fixed, 4 partial, 1 open.**

The earlier unnumbered scheduling risks are not established code defects: graph/doc and frontend implementations now exist; mock stats/journey/trace handlers are present (`lib/mock/server.ts:237`, `:256`, `:264`). Live Tencent activation, realistic load, and fresh end-to-end evidence were not verified in this read-only review.

## Checks and limits

- **SSE framing:** Executed the actual transpiled `askStream` in memory against one-byte chunks, LF frames, CRLF frames, multi-line JSON data, and a final answer without a terminating blank line. These passed. `lib/api.ts:363` preserves partial frames; `:370` handles the remainder; `:375` cancels the reader. No chunk-boundary or citation-index defect was established.
- **Citation attachment:** Executed `answerText.sentencesFor` on `First fact. [C1] Second fact. [C2]` and tags-before-punctuation variants. Each tag attached to its correct sentence. `AnswerCard.tsx:41` maps by tag to the actual citation-array index; `:54` displays index + 1 and passes the zero-based index to the sheet. Sparse tag numbering is therefore not an off-by-one bug.
- **Proxy:** Anchored method/path regexes, decoded-segment rejection, encoded outbound path revalidation, manual redirects, and slot cookie selection remain in place (`route.ts:63`, `:68`, `:185`, `:219`, `:225`, `:237`). No concrete allowlist traversal bypass was found. SSE returns the upstream body directly with the correct content type and buffering header; it does not forward stale content-length/content-encoding headers. It drops upstream `no-transform` (`:245`) and does not combine downstream cancellation with its deadline (`:177`, `:238`); deployment-specific compression/buffering and disconnect propagation were not tested, so these are not asserted as reproduced failures.
- **Cleanup:** `useAskProgress.ts:66` clears timers on unmount but does not abort its fetch or invalidate its generation; `:123` resets local progress only. The backend deliberately finishes/ledgers disconnected asks (`app.py:283`). This behavior can leave work in flight; cancellation must not be confused with rollback of an accepted request. No assertion that abort currently prevents backend completion is made.
- **Other named backend paths:** Read stats, title resolution, reconciliation and simulator reset. The stats future-window and replay fixes are present. `resolve_title` intentionally chooses one fuzzy match and reports alternatives (`audit/nl_filter.py:247`, `:252`, `:285`); ambiguity is not presented here as a new defect. `ingest.py:101` performs full sync, invalidates directory epoch at `:106`, and `sim.py:80` reports failed reconciliation instead of unconditional success. No additional confirmed regression was established in those paths.
- Validation used read-only Git commands, source inspection, Node transpilation/evaluation in memory, and Python `-B` AST-isolated executions with in-memory doubles. No full test suite, server, browser, live connector request, database, build, or benchmark was run. No file was modified except this report; no git state operation was performed.

**Finding totals: 7 — 0 critical, 2 high, 5 medium, 0 low.** The production exploitability of the timing channel and the likelihood of drift-driven timeouts are explicitly hypotheses; the underlying code paths are identified above.
