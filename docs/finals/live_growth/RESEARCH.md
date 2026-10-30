# Watch the brain grow: research notes

Written 30 Oct 2026 for `PLAN.md` in this folder. Each finding has a source; a line marked
**(our reasoning)** is our own inference, not a quote. Where a search found nothing, the note says so
rather than guessing.

## 1. How other tools show a graph or topology that changes

| # | Tool | What it does | What we take from it | Source |
|---|---|---|---|---|
| 1.1 | Kiali (Istio service graph) | The graph refreshes on the user's chosen interval, can be **paused** to look at one moment, and can be **replayed** over a past window. Traffic is animated (dots for requests, red diamonds for errors). | A refresh interval plus a visible pause control is normal for a live topology view. Pause is also what WCAG 2.2.2 asks for (§5). | [Kiali topology docs](https://v2-24.kiali.io/docs/features/topology), [Kiali features v1.38](https://pre-v1-41.kiali.io/documentation/v1.38/features) |
| 1.2 | Obsidian graph view | A force simulation positions the notes; a **time-lapse** replays how the vault grew, ordered by creation time. Users report that nodes shift when new notes appear, and there are plugins whose whole purpose is a *deterministic* graph view. | A force layout that re-settles on every change moves things the viewer already knows. Our galaxy layout is deterministic; new nodes must not move old ones. | [Obsidian help: Graph view](https://obsidian.md/help/plugins/graph), [forum: save node positions](https://forum.obsidian.md/t/save-node-positions-in-graph-view/1423?page=6), [Deterministic Graph View plugin](https://community.obsidian.md/plugins/deterministic-graph-view) |
| 1.3 | Neo4j Bloom | Highlighting is manual: select nodes to reveal relationships, or apply a filter that highlights the selection and greys out the rest. No automatic "new" marker was found. | Use the same visual vocabulary we already use for selection (accent colour, others dimmed). No source says Bloom marks new data. | [Bloom 1.9 post](https://neo4j.com/blog/graph-visualization/simplify-your-visualization-workflow-with-neo4j-bloom-1-9/), [Bloom autumn features](https://neo4j.com/blog/developer/autumn-is-bloom-ing-with-new-visualization-features/) |
| 1.4 | Linkurious Enterprise | New pattern matches arrive as **alerts**: scheduled queries create cases, duplicates for the same entity are merged, and email digests flag new cases. | A "what's new" list beside the graph (our "Just added" feed) is the established pattern; the graph itself stays the map. | [Linkurious 4.0](https://linkurious.com/blog/linkurious-enterprise-4-0/), [alerts manual 4.2](https://doc.linkurious.com/admin-manual/4.2/alerts) |
| 1.5 | Palantir, Kumu | Our searches found no public documentation of live-growth animation in either. | Not cited in the plan. | (searched, nothing found) |

**(our reasoning)** The common pattern across 1.1 to 1.4: the map stays put, change is shown by a
short highlight on the item and an entry in a side list, and the viewer can pause it.

## 2. Adding nodes without re-laying out the whole graph

| # | Finding | Source |
|---|---|---|
| 2.1 | Keeping the viewer's "mental map" when a graph changes is a named problem in graph drawing (Misue, Eades, Lai, Sugiyama 1995, "Layout adjustment and the mental map"). Local update schemes keep uninvolved nodes in place and in the same relative order. | [Diehl et al., GD 2002 (cites Misue et al.)](https://www.st.uni-trier.de/diehl/pubs/GD02_LNCS.pdf), [Visual navigation of compound graphs](https://www.academia.edu/77543041/Visual_Navigation_of_Compound_Graphs) |
| 2.2 | d3-force pins a node with `fx`/`fy`: after every tick its position is reset and its velocity zeroed. Adding nodes: pass the new array to `nodes()` and reheat gently with `alpha(0.3).restart()` instead of the default 1. | [d3-force simulation](https://d3js.org/d3-force/simulation) |
| 2.3 | graphology's ForceAtlas2 takes `iterations` and has `inferSettings`, but documents no "fixed node" attribute. | [graphology FA2](https://graphology.github.io/standard-library/layout-forceatlas2.html) |
| 2.4 | Cytoscape fCoSE supports incremental layout (`randomize: false`, every node must start with a position) and `fixedNodeConstraint` to pin nodes. | [cytoscape-fcose on npm](https://npmjs.com/package/cytoscape-fcose) |
| 2.5 | In sigma, `renderer.setCustomBBox(renderer.getBBox())` before adding nodes stops the view from re-normalising (rescaling) to the new extent; `setCustomBBox(null)` restores auto-fit. Seen in two real projects, not in the official docs. | [LightRAG GraphViewer](https://huggingface.co/spaces/rm-lht/lightrag/blame/e77f94e03734ef7f795a9174973cc68c5ede1985/lightrag_webui/src/GraphViewer.tsx), [sigma mouse-manipulations example](https://gitfront.io/r/user-1520005/3GEr6tq4S43t/ssi-patterns/blob/ssi-pattern-connections-visualization/examples/mouse-manipulations/index.ts) |

What this means for us (details in `PLAN.md` §5):

- ForceAtlas2 was already rejected for this graph (57 s for 909 nodes, comment in
  `components/graph/layout-core.ts`), and it cannot pin nodes (2.3). We do not need physics at all.
- The galaxy layout places a container's documents on a sunflower spiral (slot `k` at radius
  `2.1·sqrt(k+1)+2.2`) inside a disc of radius `2.1·sqrt(n+1)+4`. **(our reasoning)** The next free
  slot `k = n` lands at `2.1·sqrt(n+1)+2.2`, which is inside the existing disc, so a new document
  can take the next slot of its own container with no other node moving. That is the "local update"
  of 2.1 with zero adjustment.
- New containers or people are placed by the same deterministic spiral search the layout already
  uses, with every existing node treated as fixed (the `fx`/`fy` idea of 2.2).
- The camera must be frozen (2.5) so a node near the edge does not rescale the whole view. Verify
  `setCustomBBox` on sigma 3.0.3 before relying on it.

## 3. SSE, polling or WebSocket for a per-person, permission-filtered feed

| # | Finding | Source |
|---|---|---|
| 3.1 | `EventSource` has no option for custom request headers. It can send cookies (`withCredentials`), and on reconnect sends `Last-Event-ID`. | [WHATWG HTML: server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html) |
| 3.2 | Buffering reverse proxies break streaming unless buffering is turned off per route; idle timeouts need heartbeats for SSE and WebSockets. Long polling without a cursor can lose events between requests; a cursor closes the gap. | [AlgoMaster: long polling vs SSE vs WebSockets](https://algomaster.io/learn/computer-networks/long-polling-vs-sse-vs-websockets), [pkgpulse 2026 guide](https://www.pkgpulse.com/guides/sse-vs-websocket-vs-long-polling-real-time-2026) |
| 3.3 | Authorisation, replay and fan-out remain the application's job whichever transport is used. | same as 3.2 |
| 3.4 | Google Drive's change feed is plain polling with an opaque token: `getStartPageToken`, then `changes.list` with `pageToken`, store `newStartPageToken` for the next poll. Push notifications (`changes.watch`) "don't contain details about the changes", so the client polls anyway. | [Drive API: manage changes](https://developers.google.com/workspace/drive/api/guides/manage-changes) |
| 3.5 | Kubernetes watch: servers keep change history only for a limited time (5 minutes by default on etcd 3) and answer `410 Gone` for an older version; the client must re-list and start again. Resource versions "must be passed unmodified back to the server". | [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/) |

**(our reasoning) for this repo:**

- The Compare view picks the session by the `x-ib-slot` header (`app/api/ib/[...path]/route.ts`).
  `EventSource` cannot send that header (3.1), so SSE would need a fetch-based stream reader, a new
  streaming path through the Next proxy, heartbeats and reconnect logic (3.2).
- The IB routes are synchronous FastAPI handlers; a long-lived stream holds a worker thread per open
  graph (two per Compare view) for the whole demo.
- Polling reuses everything that is already tested: the principal dependency (identity from the
  token), `authz.authorize`, the write-ahead ledger and the proxy allowlist. Each response is a
  normal request that can be tested for an identical shape.
- The cost is latency: a webhook is applied in well under a second, so a 2 to 3 s poll makes a new
  note appear in about 1 to 4 s. That still matches the "within seconds" claim.
- Drive (3.4) shows that a big production change feed is a polled, opaque token. Kubernetes (3.5)
  shows the "expired cursor: start again" rule, which our "older than 10 minutes starts a new
  window" rule copies.

## 4. A "since cursor" endpoint that leaks nothing

| # | Finding | Source |
|---|---|---|
| 4.1 | Base64 is an encoding, not protection: sign or encrypt a cursor whose contents or integrity matter. A raw internal id as a cursor leaks implementation details and lets clients forge cursors that may bypass row-level security or enumerate ids. | [leyaa.ai: cursor pagination](https://leyaa.ai/codefly/learn/rest-api/part-1/rest-api-cursorbased-pagination/deep), [Gravitee pagination patterns](https://gravitee.io/corpus/gen-611/rest/pagination-patterns.html) |
| 4.2 | Opaque cursors let the server change its encoding without breaking clients; redact cursors in logs. | same |
| 4.3 | Resource versions are server-internal and must be passed back unmodified (Kubernetes). | [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/) |

**(our reasoning): the rules we adopt (they extend `docs/tencent/SECURITY.md` §15.1):**

1. **No global sequence numbers.** A counter that moves when *anyone's* document arrives tells a
   viewer how much happened that they cannot see. The cursor holds only the caller's own id and a
   time, signed with HMAC (4.1).
2. **The cursor's contents must not depend on hidden state.** The payload is signed, not encrypted,
   so anyone can base64-decode it. Its time must therefore be the server clock at reply time, never
   the timestamp of a candidate document (for example "the oldest one that timed out"), because
   that candidate may be one the caller cannot open.
3. **Filter before any source call.** Gate 1 (the stored ACL) removes documents in channels the
   caller is not in *before* any live check, so the reply time does not change either.
4. **No counts of filtered items, no "more" flag** derived from unfiltered candidates. A change the
   caller cannot open looks exactly like no change.
5. **Cap after the live check**, the same rule as `graph.py` (SECURITY.md §15.1), so a denied
   document never takes a visible slot.
6. **Expired, forged or someone else's cursor means a new window** with no back-fill (as Kubernetes'
   `410 Gone` does, 3.5), and the reply has the same shape as every other reply.

## 5. Accessibility of live updates

| # | Finding | Source |
|---|---|---|
| 5.1 | WCAG 2.2.2 (Pause, Stop, Hide): auto-updating content that starts by itself and is shown next to other content needs a way to pause, stop or hide it, or to set the update frequency. The 5-second exception applies only to moving, blinking or scrolling content, **not** to auto-updating content. | [W3C Understanding 2.2.2](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html) |
| 5.2 | WCAG 2.3.3 (AAA): motion triggered by interaction can be disabled; techniques C39/SCR40 honour `prefers-reduced-motion` in CSS and JavaScript. | [W3C Understanding 2.3.3](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html) |
| 5.3 | `role="log"`: new items go only at the end; implicit `aria-live="polite"` and `aria-atomic="false"`; it needs an accessible name. | [MDN: log role](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/log_role) |
| 5.4 | Polite is the default politeness; debounce frequent updates; create the live region empty at page load so its first content is announced. | [a11y-collective: aria-live](https://www.a11y-collective.com/blog/aria-live/), [UXPin: live regions](https://www.uxpin.com/studio/blog/aria-live-regions-for-dynamic-content/) |

What this means for us:

- A visible **Pause live updates** switch on the graph (5.1). Pausing stops the polling, not only
  the animation.
- Under reduced motion (the app's existing `useReducedMotion`, which reads the OS setting and
  `data-motion`), new nodes appear with a static accent highlight and a "New" label. There is no
  pulse and the camera does not fly (5.2).
- The visible feed is a list with the newest item first, so it is **not** a `role="log"` (which
  appends at the end, 5.3). Announcements go through the explorer's existing polite `sr-only` live
  region: one debounced sentence per poll that returned something (5.4), for example "1 new
  document: Slack, #db-migration, new thread."
