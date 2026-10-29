# 03 · Developer mode: graph, trace and analytics research

| | |
|---|---|
| For | Walnut DataBridge finals build (Demo Day 3 Nov 2026) |
| Scope | Developer mode for technical judges: graph explorer, "brain" request view, provenance, permission overlays, audit trail, end-to-end analysis page |
| Method | Web research (sources linked inline and listed at the end) + npm registry checks run on 30 Oct 2026 + a read of our own code (`apps/internal_brain_web`, `databridge_ib/audit/stats.py`, `databridge_ib/app.py`, `evals/internal_brain/results.json`) |
| Binding house rules | `databridge/.claude/skills/databridge-frontend-dashboards/SKILL.md` (shadcn + Radix + Tailwind, Recharts with visx for 1–2 hero charts, React Flow with Sigma at scale, TanStack Table, dark default, no neon, provenance on every value) and the hard constraints in `Tencent/CLAUDE.md` |

---

## 0. The answer on one page

| Question | Pick | Why, in one line |
|---|---|---|
| Graph explorer (1k–5k nodes) | **sigma 3.0.3 + graphology 0.26.0 + @react-sigma/core 5.0.6** | WebGL, small (26 KB + 13 KB gzip), built for exactly this size, and the reducer API gives us ego-network, search-to-focus and time filtering cheaply |
| Small diagrams (pipeline, provenance chain) | **@xyflow/react 12.12.0** | Custom React nodes look premium, but every node is a DOM element, so keep it to diagrams under ~100 nodes |
| Request "journey" view | Waterfall (Datadog/Langfuse style) + gate stepper, built by hand with Tailwind and divs | Five fixed stages do not need a tracing library; hand-built is sharper and fully accessible |
| Charts | **Recharts 3.10.1** (via shadcn chart pattern) + **@visx/sankey 4.0.0** for the one hero Sankey | Matches house rules; Recharts has the best shadcn/dark-mode story; visx gives full control of the hero chart |
| Components | shadcn/ui **v3 docs** (Tailwind 3), copied in by hand, Radix pinned to the Oct-2024 release train we already use | We are on Tailwind 3.4.13 and `@radix-ui/react-dialog` 1.1.2; the current shadcn CLI targets Tailwind 4 |
| Motion | **framer-motion 12.43.0** with `LazyMotion` + `MotionConfig reducedMotion="user"` | Supports React 18; v13/v14 are weeks old; LazyMotion keeps the first load small |
| Command palette | **cmdk 1.1.1** | The de-facto standard, 17 KB gzip, React 18 peer |
| Mode switch | Header segmented control "User / Developer" + backtick shortcut, URL under `/dev/*`, same identity and same permissions | Figma Dev Mode (same file, different lens) + Stripe Workbench (tilde opens dev tools anywhere) |

**Ten findings that drive the spec**

1. Our synthetic world is already in the hard zone for DOM-based graphs: `fixtures/enterprise/world.json` holds 209 pages, 251 issues, 602 threads, 81 files, 32 users, 16 groups, 7 spaces, 15 channels, 4 projects, 12 folders and 2 drives. That is about 1,230 nodes before entities, and several thousand edges. React Flow maintainers put the practical ceiling near 1,000 DOM nodes ([React Flow performance](https://reactflow.dev/learn/advanced-use/performance), [issue thread](https://lightrun.com/answers/wbkd-react-flow-performance-drop-when-panning-or-zooming)). We need WebGL.
2. **sigma 4.0.0 shipped on 8 Oct 2026 and 4.0.4 on 27 Oct; @react-sigma/core 6.0.0 on 14 Oct.** These are too fresh for a demo in four days. sigma 3.0.3 (30 Apr 2026) with @react-sigma/core 5.0.6 (1 Dec 2025) is the mature pair, and their peer ranges match (`sigma ^3.0.2`, `graphology ^0.26.0`).
3. Sigma's defaults are tuned for a light page. In the 3.0.3 tarball, `labelColor` defaults to `#000` and the default hover renderer draws a white label box. On a dark theme both must be overridden (section 1.4).
4. The backend already splits what each viewer may see. `GET /ib/trace/{req_id}` returns **my own** request's stages, model and sealed hashes, but "never the considered set, Gate-2 denials or reason codes" (`databridge_ib/app.py:176-203`). `GET /audit/journey/{req_id}` and `GET /audit/stats` return the full picture to the `compliance_officer` role only (`audit/routes.py:1,84`). Developer mode must keep this split, or it breaks hard constraint 4 ("no counts and no denial reasons to the asker").
5. `/audit/stats` already computes nearly every number the analysis page needs: totals, a doc-level funnel, Gate-2 verdicts and reason codes, latency p50/p95, per-stage timings for `accept, gate1, gate2, seal, compose`, model mix, fallback use, per-platform and per-person counts, and per-day series (`audit/stats.py:42-161`). Freshness comes from `GET /ib/sources` (`lag_s` against `slo_s`). Leak-eval evidence lives in `evals/internal_brain/results.json` (`headline.leaks = 0`, `n_requests_all_modes = 5958`, `p2_mismatches = 0` of 400).
6. A Sankey must not mix units. "Asked" counts requests, while "considered / allowed / released / cited" count documents. Draw two flows (requests, documents), not one. Section 3.3 explains why.
7. Every good trace UI uses the same core: a fit-to-screen waterfall, colour by step type, a detail panel, and focus on one span ([Datadog](https://docs.datadoghq.com/tracing/trace_explorer/trace_view/), [Langfuse](https://langfuse.com/changelog/2026-08-28-responsive-timeline), [Grafana critical path](https://grafana.com/docs/grafana/v13.2/visualizations/explore/trace-integration/)). Our addition is a **decision lane**: gates are allow/deny decisions, not just timings.
8. Every readable graph product uses the same four moves: style by rule, focus N hops on click, search then fly to the node, and filter by time ([Bloom](https://neo4j.com/blog/developer/integrating-graph-data-visualizations-no-code-workflows-bloom-2-0/), [Kumu focus](https://docs.kumu.io/guides/focus), [Obsidian](https://obsidian.md/help/plugins/graph), [Linkurious](https://linkurious.com/blog/time-filtering-linkurious/), [Vertex](https://palantir.com/docs/foundry/vertex/explore-object-relationships/)).
9. A loud "mode" banner (Stripe's orange sandbox banner) exists because the *data* differs between modes ([Stripe sandboxes](https://docs.stripe.com/test-mode)). Our Developer mode shows the same data with more instrumentation, so the right signal is quiet: a thin accent rule and a pill, not a colour flip.
10. Keep all dev-mode libraries behind the `/dev` route. The whole dev bundle is roughly 300 KB gzip (bundlephobia figures in section 3.4), loaded only when a judge opens it.

---

## 1. Graph visualisation in React

### 1.1 What we need from the library

- 1,200–5,000 nodes and 3,000–15,000 edges, smooth pan and zoom on the demo laptop.
- Nodes coloured by platform (Confluence, Jira, Slack, Drive) plus people, groups and containers.
- Ego-network on click, search-to-focus, a time slider, and permission overlays (show what person A sees against person B).
- A deterministic layout. House rule: "no nondeterministic graph layout", so the same graph renders the same way on every run.
- Works with React 18.3.1 and Next 14.2 App Router as a client-only component.

### 1.2 Comparison

Versions and dates come from the npm registry (checked 30 Oct 2026). Sizes are bundlephobia min+gzip for the whole package. Tree-shaking can lower them.

| Library | Renderer | Look out of the box | 1k–5k nodes | Clustering / communities | Focus + context | Animation | Accessibility | Gzip size | Licence | React 18 / Next 14 |
|---|---|---|---|---|---|---|---|---|---|---|
| **sigma.js + graphology** (sigma 3.0.3, graphology 0.26.0) | WebGL | Clean and minimal; needs theming for dark mode | Its strong point. The site promises "Render large graphs in the browser. Fast." ([sigmajs.org](https://www.sigmajs.org/)). A vendor comparison says sigma handles large graphs with default styles but slows with 5k icon nodes ([Ogma vs sigma](https://doc.linkurious.com/ogma/5.3/compare/sigmajs.html), vendor claim) | Louvain via `graphology-communities-louvain` 2.0.2; ForceAtlas2 via `graphology-layout-forceatlas2` 0.10.1 | `nodeReducer` / `edgeReducer` restyle or hide any node per frame without touching the data | Camera animations (`animate`, zoom duration 250 ms default); node moves need hand-written tweening | Canvas only. Needs a DOM twin (list or table) | 26 KB + 13 KB | MIT | `@react-sigma/core` 5.0.6 peers `react ^18 \|\| ^19`. Client-only; use `next/dynamic` with `ssr:false` |
| **React Flow** (@xyflow/react 12.12.0) | DOM/SVG | The most polished. Nodes are React components, so they can use our design system | Weak. The maintainer cites ~1,000 nodes as the limit, depending on node complexity ([thread](https://lightrun.com/answers/wbkd-react-flow-performance-drop-when-panning-or-zooming)). `onlyRenderVisibleElements` helps ([API](https://reactflow.dev/api-reference/react-flow)) | None built in (sub-flows or groups only) | Manual | Good (CSS and framer-motion on nodes, animated edges) | Best of the set. Nodes are focusable DOM elements, with built-in a11y props | 60 KB | MIT | Peers `react >=17`; needs `'use client'` and its CSS import |
| **Cytoscape.js** (3.34.4) | Canvas; WebGL in preview since 3.31 ([blog](https://blog.js.cytoscape.org/2025/01/13/webgl-preview/)) | Dated defaults; a CSS-like stylesheet can restyle it | Good on canvas to a few thousand. WebGL is still "preview" and "does not support all styles" | Rich algorithm library, plus compound nodes | Selectors and classes | Built-in `animate` | Canvas only | 137 KB | MIT | `react-cytoscapejs` 2.0.0 is a thin wrapper |
| **react-force-graph** (2D 1.29.2 / 3D 1.29.2) | Canvas (2D) / three.js WebGL (3D) | Eye-catching, but reads as "demo-ware", and 3D is hard to read | Fine at this size, but the live force simulation never fully settles, so the layout is not deterministic unless frozen | None built in (d3 forces only) | Manual (`nodeCanvasObject`) | Continuous physics | Canvas only | 60 KB (2D) / 420 KB (3D) | MIT | Peer `react: *` |
| **d3-force** (3.0.0) | Bring your own (SVG or canvas) | Whatever you draw | SVG struggles past a few thousand elements; canvas is fine | Bring your own | Bring your own | Physics ticks | Whatever you build | 6 KB | ISC | No React layer; all hand work |
| **G6 / Graphin** (@antv/g6 5.1.1, @antv/graphin 3.0.5) | Canvas, SVG or WebGL through the G engine ([README](https://github.com/antvis/g6/)) | Feature-rich, with a recognisable AntV look | Capable, but no independent benchmarks found | Many built-in layouts and combos | Built-in behaviours | Built-in | Canvas | 400 KB | MIT | Graphin 3 peers `react ^18 \|\| ^19.1` |

Other references: [cosmograph comparison](https://cosmograph.app/docs-lib/comparison/) (a vendor page, but it confirms sigma renders more than 1M points and react-force-graph-3d does not), and [a neutral library comparison](https://dev.to/timlrx/a-comparison-of-javascript-graph-network-visualisation-libraries-34a8).

### 1.3 Recommendation

**Use sigma.js + graphology for the knowledge-graph explorer and React Flow for every small diagram.** This is the escalation the house rule already describes ("Escalate to Sigma.js + graphology (WebGL) only when the knowledge-graph subgraph exceeds ~1–2k visible nodes"). Our world is at that size today.

Why not the others:
- **Cytoscape** has the best algorithm library, but its WebGL renderer is still a preview and its canvas renderer is the slow path at our size.
- **react-force-graph** looks impressive for ten seconds, but a moving force simulation is non-deterministic and hard for judges to read. 3D adds 420 KB and makes labels hard to read.
- **G6** is the heaviest (400 KB) and has a recognisable AntV house style, which the house rules forbid.
- **Plain d3** means building zoom, hit-testing and label collision ourselves, which costs days we do not have.

Gephi Lite is a credible proof that sigma + graphology scales to a full graph-analysis app with appearance rules and filters ([gephi.org/lite](https://gephi.org/lite/)). The same maintainer builds both libraries.

### 1.4 Exact packages and Next 14 notes

```bash
npm i -E sigma@3.0.3 graphology@0.26.0 @react-sigma/core@5.0.6 \
  @react-sigma/layout-forceatlas2@5.0.6 graphology-layout-forceatlas2@0.10.1 \
  graphology-layout@0.6.1 graphology-communities-louvain@2.0.2 \
  @sigma/node-border@3.0.0 @sigma/edge-curve@3.1.0
npm i -D -E graphology-types@0.24.8
npm i -E @xyflow/react@12.12.0 @dagrejs/dagre@3.0.0   # small DAGs only
```

| Note | Detail |
|---|---|
| Why not sigma 4 | 4.0.0 shipped 8 Oct 2026 and 4.0.4 on 27 Oct; `@react-sigma/core` 6.0.0 (14 Oct) requires `sigma ^4`. Upgrade after Demo Day |
| Client-only | Wrap the explorer in `next/dynamic(() => import('./GraphCanvas'), { ssr: false })`. Sigma touches `window` and WebGL at import time |
| Container height | `SigmaContainer` needs an explicit height (for example `h-[calc(100vh-112px)]`), or it renders at 0 px |
| Dark theme overrides | Defaults read from the 3.0.3 tarball: `labelColor {color:"#000"}`, `defaultEdgeColor "#ccc"`, `labelFont "Arial"`, `labelRenderedSizeThreshold 6`, `labelDensity 1`, `labelGridCellSize 100`, `hideEdgesOnMove false`, `enableEdgeEvents false`, `zIndex false`. Set `labelColor` to the `fg` token, `labelFont` to Inter, and replace `defaultDrawNodeHover` (it paints a white box) with a dark-surface version |
| Deterministic layout | Seed positions with `graphology-layout` `circular.assign` (deterministic), then run `forceAtlas2.assign(graph, { iterations: 300, settings: forceAtlas2.inferSettings(graph) })` once, synchronously. Better still, run it once offline and ship `x,y` in the payload. Do not run the FA2 web worker live in the demo |
| Performance switches | `hideEdgesOnMove: true` above ~5k edges; `zIndex: true` so focused nodes draw on top; keep `enableEdgeEvents: false` unless edge hover is needed |

### 1.5 How the professional tools make graphs readable, and how to copy each move

| Technique | Who does it, and how | How we build it in sigma |
|---|---|---|
| **Rule-based styling** | Neo4j Bloom "perspectives" set captions, default colours and rule-based styles; Bloom 2.0 added histograms to the style rules ([Bloom 2.0](https://neo4j.com/blog/developer/integrating-graph-data-visualizations-no-code-workflows-bloom-2-0/), [1.5.0 notes](https://neo4j.com/release-notes/bloom/bloom-1-5-0/)). Gephi Lite keeps appearance rules in the data model so visuals recompute when filters change ([Gephi Lite](https://gephi.org/lite/)) | One `styleFor(node)` function that maps `kind` and `platform` to colour and size. Colour by **platform** (4 muted hues) plus neutral grey for people and containers. That is a categorical scale of four, not a Louvain rainbow, so it meets the "no rainbow" rule |
| **Community colouring** | Kumu "clustering" creates a hub per field value and links elements to it ([Kumu clustering](https://docs.kumu.io/guides/clustering)). Obsidian "groups" colour nodes by a search query ([Obsidian graph](https://obsidian.md/help/plugins/graph)) | Run Louvain to *place* communities (FA2 pulls them together), but colour by platform. Offer "Colour by: platform / space / team / community" in a dropdown, with community as an option rather than the default |
| **Semantic zoom** | Obsidian's "text fade threshold" hides labels when zoomed out. Langfuse shows names only when zoomed in enough to read ([Langfuse timeline](https://langfuse.com/changelog/2026-08-28-responsive-timeline)) | Zoomed out: draw only containers (spaces, channels, projects, drives) as large nodes with counts, and hide documents through `nodeReducer` when `camera.ratio > 1.5`. Zoomed in: documents appear, and labels obey `labelRenderedSizeThreshold` (raise to 8) and `labelDensity` (0.7). Hubs (degree in the top 2%) use `forceLabel: true` |
| **Ego-network on click** | Kumu focus: click and hold, `+`/`-` widen or narrow by one degree, a number key sets the degree, `Esc` restores the map ([Kumu focus](https://docs.kumu.io/guides/focus)). Obsidian "local graph" uses a depth slider. Palantir Vertex "Search Around" adds related objects step by step with filters ([Vertex](https://palantir.com/docs/foundry/vertex/explore-object-relationships/)) | Click a node to compute its N-hop set with a BFS over graphology neighbours. The reducer dims everything else to 15% opacity, greys the edges, and sets `zIndex` higher for the focus set. Copy Kumu's keys exactly: `+`, `-`, `1`–`3`, `Esc`. The side drawer lists neighbours grouped by relation type |
| **Search to focus** | Bloom "search phrases" are saved queries users run from a search bar. Vertex deep-links can set a search and a time range from the URL ([Vertex overview](https://www.palantir.com/docs/foundry/vertex/overview)) | The `⌘K` palette searches node labels (we already hold them in graphology), then `camera.animate({x, y, ratio: 0.25}, {duration: 400})`, then select the node to trigger ego focus. Put `?node=…&hops=2&asOf=…` in the URL so every view can be shared and replayed |
| **Time slider** | Linkurious shows date properties as histograms and filters with a range slider ([Linkurious time filtering](https://linkurious.com/blog/time-filtering-linkurious/), [Ogma timeline plugin](https://doc.linkurious.com/ogma/latest/plugins/timeline.html)). Vertex has a time selector with an admin default window. Obsidian's "animate" replays notes by creation date | A histogram strip of `modified_at` under the canvas, with a two-thumb Radix `Slider`. The reducer hides nodes outside the window and fades edges whose evidence has expired. A "Play" button steps through weeks over 6 seconds. The house rule calls the `as_of` slider "a permanent fixture" |
| **Legend and caption** | Gephi Lite only allows styles it can explain in its caption | A fixed legend card (bottom left) that always states the active colour rule, the size rule, the filter, and "Showing N of M nodes (subgraph: seed + 2 hops)", as the house rule requires |

### 1.6 Accessibility for a WebGL graph

Canvas is invisible to screen readers, so follow the pattern Cambridge Intelligence recommends ([accessible graph apps](https://cambridge-intelligence.com/build-accessible-data-visualization-apps-with-keylines/)):

1. Give the canvas `role="img"` with an `aria-label` that summarises the graph ("Knowledge graph: 1,232 items across 4 platforms; 47 visible to you").
2. Add a **"List view" toggle** that renders the same filtered node set as a TanStack Table. This is also a judge-friendly way to show the data is real.
3. Keyboard: `/` or `⌘K` searches, `Tab` moves through the drawer, arrow keys step through neighbours, `+`/`-` change hops, and `Esc` clears focus.
4. Never use colour alone. Platform also shows as an icon in the drawer and a letter glyph on hover.
5. Respect `prefers-reduced-motion`: camera moves jump instead of animating.

---

## 2. Pipeline and trace visualisation

### 2.1 How the leading tools show one request

| Tool | Main view | Hierarchy | Timing | Colour | Detail panel | Worth copying |
|---|---|---|---|---|---|---|
| **LangSmith** | Run tree for one trace ([docs](https://docs.smith.langchain.com/tracing)) | Nested runs (chain, LLM, retriever, tool) | A waterfall shows per-step latency and separates parallel from sequential steps ([changelog](https://changelog.langchain.com/announcements/ann_t0pJcFOimB0OD)) | By run type | Inputs, outputs, parameters, latency, tokens, feedback | Root-run latency first; feedback attached to the trace |
| **Langfuse** | Tree / timeline toggle ([new trace view](https://langfuse.com/changelog/2025-03-19-new-trace-view)); agent graph view ([agent graphs](https://langfuse.com/docs/observability/features/agent-graphs)) | Observations: spans, generations, events | The 2026 responsive timeline fits any trace on one screen, zooms both axes, focuses on double-click, and selects a range by dragging ([timeline](https://langfuse.com/changelog/2026-08-28-responsive-timeline)) | By observation type, "so fan-outs are recognisable before reading labels" | Per-observation I/O, tokens, cost | Fit-to-view default; labels appear only when there is room; peek names without layout shift |
| **Arize Phoenix** | Trace = tree of spans with OpenInference span kinds ([Phoenix tracing](https://arize.com/blog/llm-tracing-and-observability-with-arize-phoenix), [traces](https://arize.com/docs/ax/instrument/what-are-traces)) | Chain, LLM, Retriever, Tool, Agent | Span durations | By span kind | **Retriever spans list the retrieved documents** | Show the documents on the retrieval span, the closest analogue to our gates |
| **Datadog APM** | Flame graph (default), span list, waterfall, map ([trace view](https://docs.datadoghq.com/tracing/trace_explorer/trace_view/)) | Spans grouped by service | Absolute and relative durations; % of execution time | By service; errored services get a red border | Bottom tabs: span info, logs, infrastructure, span links | "Focus on span" isolates its children; header shows trace ID, end-to-end time and a permalink |
| **OpenTelemetry / Grafana Tempo / Jaeger** | Timeline waterfall ([Grafana trace view](https://grafana.com/docs/grafana/v13.2/visualizations/explore/trace-integration/)) | Parent and child spans | A darker segment marks the **critical path**; "Show critical path only" filter | By service | Span attributes and events | Critical-path highlight |

### 2.2 Lessons for us

1. **One screen, fit to view.** Our request has 5–7 stages, so the whole journey always fits. No scrolling inside a trace.
2. **Colour by kind, not by status alone.** Kinds: identity, gate, ledger seal, model, grounding. Status (allowed, denied, failed) is a second channel: an icon and a border.
3. **Gates are decisions, not just durations.** No tracing tool has a "decision lane". We add one: under each gate bar, show what went in, what came out, and why.
4. **The ledger is the spine.** Every seal (`request.accepted`, `retrieval.decided`, `answer.released`) appears as a tick on the waterfall with its `seq` and short hash. This proves "write-ahead" visually: the `retrieval.decided` tick sits *before* the model bar starts.
5. **Header like Datadog:** request ID, asker, channel, end-to-end time, status, and a copy-link permalink.
6. **Phoenix's document list on the retrieval span** becomes our "Released documents" list on Gate 2 (and, in the compliance lens only, the considered and denied ones).

### 2.3 Design: the "Request journey" view

Two lenses on the same screen. The data source decides what each viewer sees.

| Lens | Who | Endpoint | Shows | Never shows |
|---|---|---|---|---|
| **My trace** | Any employee (for example `jdoe`) in Developer mode | `GET /ib/trace/{req_id}` | Stage timings (`stage_ms`), model, `prompt_version`, `policy_version`, latency, `dropped_sentences_count`, released docs, citations, `answer_sha256`, ledger events (seq, kind, hash) | Considered set, Gate-2 denials, reason codes, counts of restricted items (hard constraint 4) |
| **Compliance journey** | `maya.compliance` | `GET /audit/journey/{req_id}` | Everything above plus `considered`, `gate2` verdicts with `reason_code`, `gate1_policy_version`, `directory_epoch`, reveals | Answer text (that is `/audit/reveal`, ledgered with a reason) |

Wireframe (compliance lens; "My trace" hides the parts marked ◆):

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ Request 7f3a…c21  [copy link]     jdoe · ui · 30 Oct 14:02:11 SGT        ● Released   1,284 ms │
│ "Who approves payment-limit changes above SGD 50k?"                                            │
│ policy v3 · directory epoch 41 · prompt v2 · model hunyuan/hy3 · fallback no                   │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ STAGE              0 ms        250        500        750       1000      1250                 │
│ ● accepted         ▌ 4 ms                         ledger #1041 a91c…                          │
│ ◇ Gate 1 index     ▐██▌ 38 ms                     ◆ 12 considered                              │
│ ◇ Gate 2 live         ▐██████▌ 210 ms             ◆ 10 allowed · 2 denied (acl_revoked ×2)     │
│ ■ sealed              ▌ 6 ms                      ledger #1043 retrieval.decided  3e07…        │
│ ◆ model (Hy3)                ▐███████████████████▌ 980 ms                                      │
│ ▲ grounding                                       ▌ 1 sentence dropped (no permitted evidence)│
│ ■ released                                        ▌ ledger #1045 answer.released 9c1e…         │
│   critical path ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                         │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ [Documents]  [Citations]  [Ledger records]  [Raw JSON]                                         │
│ doc                         platform    Gate 2         reason          cited                  │
│ PAY-112 Limit policy         jira        ✓ verified     —               [1]                    │
│ Payments runbook v4          confluence  ✓ verified     —               [2]                    │
│ ◆ #fin-approvals thread      slack       ✕ denied       acl_revoked     —                      │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Field mapping (all fields exist today in `audit/query.py:461-520` and `app.py:194-203`):

| UI element | Field |
|---|---|
| Header | `req_id`, `sub`, `channel`, `event_time`, `status` (`released`/`refused`/`failed`/`in_progress`), `latency_ms` |
| Context line | `policy_version`, `directory_epoch`, `prompt_version`, `model.provider/model`, `model.fallback_used` |
| Stage bars | `stage_ms.accept/gate1/gate2/seal/compose` (the `STAGES` tuple in `stats.py:13`). Bars are laid end to end in that order. Label them "approximate start" unless the backend adds start offsets |
| Ledger ticks | `events[]` with `seq`, `kind`, `hash`, `recorded_at` |
| Gate 1 / Gate 2 decision lane ◆ | `considered[]`, `gate2[]` with `verdict`, `reason_code`, `doc_id` |
| Grounding | `dropped_sentences_count`. The grounding wall currently runs inside `compose`. Ask the backend for a separate `ground` timing (section 6.6) |
| Documents tab | `released[]` (title, container), `citations[]` |
| Failure state | `failure.stage`, `failure.error_code`. Red border on that stage; later stages greyed with "not reached" |

Refused answers are shown as **success of the safety system**, not as errors: a neutral "Refused, no permitted evidence" state with the ledger ticks still present.

### 2.4 The "brain" view (pipeline diagram)

A fixed-layout React Flow diagram above the waterfall (or as a tab). It explains the architecture once, then replays a chosen request.

```
[Question] → [Signed identity] → [Gate 1: index pre-filter] → [Gate 2: live source check] → [Ledger: retrieval.decided]
                                                                                                  ↓
[Answer + citations] ← [Ledger: answer.released] ← [Grounding wall] ← [Model: Hy3 | DeepSeek | extractive]
```

- Nodes are custom React components (shadcn card styling). Each node shows the per-request number in tabular figures: "12 → 10" on Gate 2, "980 ms" on the model, "1 dropped" on grounding.
- **Replay**: an edge pulse travels the path with each hop's duration scaled from `stage_ms` (total replay of about 3 s). Denied documents peel off at Gate 2 as small grey dots (compliance lens only). With reduced motion, the pulse is replaced by a step highlight.
- The model node shows which path ran: Hy3, DeepSeek or the extractive fallback (`model.fallback_used`). That makes the "the system still answers safely if the model misbehaves" claim visible.
- Positions are hand-set. No auto-layout library is needed for 9 nodes.

---

## 3. Analytics dashboard best practice

### 3.1 What the reference dashboards do

| Product | Pattern worth copying | Source |
|---|---|---|
| **Vercel Speed Insights** | P75 by default; P90/P95/P99 can be added to the time chart; filters for environment, device and time range sit at the top | [vercel.com/docs/speed-insights](https://vercel.com/docs/speed-insights) |
| **Linear Insights** | Measure (y) × Slice (x) × optional Segment (colour). Scatter plots show p25/p50/p75/p95 markers. **Clicking a bar or table cell filters the issue list**, so drill-down is native. Opens with `⌘⇧I` in a right sidebar | [linear.app/docs/insights](https://linear.app/docs/insights) |
| **Stripe Workbench** | Overview tab: API requests and webhook graphs with a period dropdown, plus "Recent errors" grouped by type with occurrences, path and source. Every error links to the request log. Opens anywhere with `~` | [docs.stripe.com/workbench/overview](https://docs.stripe.com/workbench/overview) |
| **Grafana** | Order rows to follow the data flow; rate and errors on the left, latency on the right (RED); consistent colour meaning with thresholds; avoid stacked graphs that hide data; every dashboard answers one question; link to drill-down dashboards | [Grafana best practices](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/) |
| **Datadog** | Depth on demand: a full-screen graph triggers "Watchdog Explains", which highlights the anomaly and lists the contributing tags in a side panel | [Watchdog Explains](https://docs.datadoghq.com/dashboards/graph_insights/watchdog_explains/) |

### 3.2 Rules for our analysis page

1. **Lay rows out in pipeline order** (Grafana): Asked → Gates → Model → Grounding → Released → Evidence. A judge reads the page left to right, top to bottom, the way a request flows.
2. **KPI tile anatomy:** label (12 px, muted) · value (28–32 px, tabular-nums) · delta against the previous window (only when one exists) · 7- or 30-day sparkline · a source chip ("ledger · 30 d", "eval · 14 Oct"). Every value goes through the `<Grounded>` popover that the house rule requires.
3. **Separate live numbers from eval numbers.** The ledger shows what happened in this demo. The eval file shows what was proven at scale. They get different chips ("live" against "eval run 2026-10-14, world sha 3599…") and never share an axis.
4. **Percentiles, not means:** show p50 and p95 for latency (Vercel, Linear). Means hide tails.
5. **Funnels as horizontal bar lists**, not trapezoid funnel charts. Bar lists are exact, readable and accessible.
6. **One Sankey, honest units** (section 3.3).
7. **Heatmaps as CSS grids** of `<div role="gridcell" aria-label="…">`. That needs no library, is accessible, and is crisp on a projector.
8. **Drill-down everywhere** (Linear): any bar, tile or cell opens the audit events list pre-filtered (`/audit/events?kind=&decision=&user=`). Any row there opens the request journey.
9. **A window control and a replay toggle** in the page header: `days` (1/7/30) and `replay=include|exclude`. Both are already parameters of `/audit/stats` (`routes.py:224-247`), so judges can see real traffic apart from seeded replay.
10. **Zero is a result.** Show "0 leaks" in full size with its denominator and its upper bound ("0 of 5,958 · 95% upper bound 0.10% per request"), never a green dot alone (house rule: no traffic-light-only summaries).
11. **No client-side maths** beyond formatting and ratios already returned. The house rule says derivations belong in the backend. Section 6.6 lists the three fields to add server-side.

### 3.3 The Sankey: two flows, not one

The tempting chain "asked → gate1 → gate2 → model → grounded → released" mixes units. One request considers many documents, so a single Sankey would show widths that mean nothing. Use two linked visuals:

- **Request flow (Sankey, hero chart):** `asked` → `answered` / `refused` / `failed`, with `answered` splitting into `model: Hy3`, `model: DeepSeek` and `extractive fallback`. Sources: `totals.requests`, `totals.answered`, `totals.refused`, `totals.failed`, `models{}`, `totals.fallback_used`.
- **Document flow (bar list or a second, smaller Sankey):** `considered_docs` → `gate2_checked` → `gate2_allowed` / `gate2_denied` → `released_docs` → `cited`. Source: the `funnel{}` block. If `considered_docs − gate2_checked > 0`, label the gap "not checked (ranked out)" only after the backend confirms what it means.

Library: `@visx/sankey` 4.0.0 (peer `react ^18 || ^19`). It is the house rule's "visx for 1–2 bespoke hero charts". ECharts would give adjacency-focus hover for free (`emphasis.focus: 'adjacency'`, [ECharts style](https://echarts.apache.org/handbook/en/concepts/style)), but it is 368 KB gzip and the house rule bans it.

### 3.4 Chart libraries compared

| Library | Version (date) | React 18 peer | Gzip (bundlephobia) | Licence | Dark-mode quality | Sankey / heatmap | Verdict |
|---|---|---|---|---|---|---|---|
| **Recharts** | 3.10.1 (25 Jul 2026) | `^16.8 … ^19` (also needs `react-is`) | 150 KB | MIT | Good via CSS variables. shadcn's chart docs cover the v3 upgrade: use `var(--chart-1)` without `hsl()`, and give `ChartContainer` a height or aspect class ([shadcn chart](https://ui.shadcn.com/docs/components/chart)) | Has a basic `Sankey`; no heatmap | **Pick** for tiles, sparklines, bars, lines and areas |
| **visx** | 4.0.0 (11 Jun 2026) | `^18 \|\| ^19` | 50 KB (xychart); sankey and heatmap are small modules | MIT | Whatever we draw, so excellent | `@visx/sankey`, `@visx/heatmap` | **Pick** for the hero Sankey only |
| **Tremor** | 3.18.7 (13 Jan 2025) | `react ^18.0.0` | n/a; pulls in Recharts 2 and Headless UI | Apache-2.0 | Good, but a recognisable look | Bar list, funnel | Skip. House rule says no; the npm package has not been published since Jan 2025, and the project moved to copy-paste after Vercel acquired it ([Vercel blog](https://vercel.com/blog/vercel-acquires-tremor)). Copy its *bar-list pattern* by hand |
| **ECharts** | 6.1.0 (19 May 2026), `echarts-for-react` 3.0.6 | any | 368 KB (full); less with `echarts/core` imports | Apache-2.0 | Built-in `dark` theme, strong | Best Sankey and heatmap interactivity | Skip (size, imposes its own look, house rule) |
| **Nivo** | 0.99.0 | `^16.14 … ^19` | 63 KB (`@nivo/sankey`) | MIT | Good theme object | `@nivo/sankey`, `@nivo/heatmap` | Fallback if visx Sankey slips |

Install:

```bash
npm i -E recharts@3.10.1 react-is@18.3.1 @visx/sankey@4.0.0 @visx/responsive@4.0.0
```

---

## 4. Design system and motion

### 4.1 shadcn/ui on Tailwind 3

- We are on Tailwind 3.4.13. Current shadcn docs target Tailwind 4; the Tailwind 3 docs live at [v3.shadcn.com](https://v3.shadcn.com/docs) ([legacy note](https://ui.shadcn.com/docs/legacy)). **Copy components by hand from the v3 docs** into `components/ui/`. Do not run the latest CLI: it would rewrite `tailwind.config.ts` and `globals.css` for v4.
- Map shadcn's CSS variables onto our existing tokens (`bg`, `surface`, `raised`, `line`, `line-strong`, `fg`, `muted`, `accent`, `accent-solid`, `ok`, `warn`, `bad`, `focus` in `tailwind.config.ts`). Add `chart-1..5` only: three neutrals, the accent, and `bad`.
- Pin Radix to the **same release train as the `@radix-ui/react-dialog@1.1.2` we already ship** (1 Oct 2024). That avoids duplicate copies of Radix internals:

```bash
npm i -E @radix-ui/react-tabs@1.1.1 @radix-ui/react-tooltip@1.1.3 @radix-ui/react-toggle-group@1.1.0 \
  @radix-ui/react-switch@1.1.1 @radix-ui/react-popover@1.1.2 @radix-ui/react-slider@1.2.1 \
  @radix-ui/react-hover-card@1.1.2 @radix-ui/react-scroll-area@1.2.0 @radix-ui/react-dropdown-menu@2.1.2
npm i -E @tanstack/react-table@8.21.3   # v9 is new (Aug 2026); 8.21.3 is the stable line
```

Note: `cmdk@1.1.1` brings its own newer `@radix-ui/react-dialog` (^1.1.6). That works, at the cost of a second small copy.

### 4.2 Motion

- Pin **framer-motion 12.43.0** (28 Jul 2026, peer `react ^18 || ^19`). Versions 13.x and 14.x shipped from August to October 2026 and change weekly. Wait until after Demo Day.
- Use `LazyMotion` with `domAnimation` and the `m` component. Motion's docs put the initial render at about 4.6 KB against about 34 KB for the full `motion` component ([reduce bundle size](https://motion.dev/docs/react-reduce-bundle-size), [LazyMotion](https://motion.dev/docs/react-lazy-motion)).
- Wrap the app in `<MotionConfig reducedMotion="user">`.
- **Animate only things that carry meaning:** the stage pulse in the brain view, KPI number count-up on first load (400 ms, once), drawer slide (160 ms), ego-focus dimming (200 ms), and the time-slider replay. **Do not animate** chart re-renders on filter change, hover states beyond 120 ms colour fades, or anything that loops forever.
- Durations 120–200 ms, ease-out (house rule). One spring for drawers. No bounce.

### 4.3 Command palette

`cmdk@1.1.1` (16.7 KB gzip, React 18 peer). Groups:

- **Go to:** Analysis, Graph, Journey, Access, Audit.
- **Find node:** live search over graph labels, then fly to the node.
- **Open request:** by request ID or by recent question (from `/ib/history`).
- **Switch persona** (demo only): jdoe, ken.ext, maya.compliance.
- **Actions:** Verify audit chain, Toggle mode, Toggle theme, Copy permalink.

Bind `⌘K`/`Ctrl K`, and `/` on the graph page.

### 4.4 Looking premium without clutter

From Linear's redesign (reduce visual noise, raise hierarchy and density; [Linear](https://linear.app/now/how-we-redesigned-the-linear-ui)), Vercel's Geist (gray ramp, Geist Mono for technical labels; [Geist](https://vercel.com/geist)) and our house rules:

1. One neutral gray ramp plus **one** accent. Status colours (`ok`, `warn`, `bad`) appear only on status.
2. Use the existing mono font for every ID, hash, seq number and timing. Truncate hashes to 4+4 characters (`a91c…3e07`) with copy-on-click.
3. `font-variant-numeric: tabular-nums` on every number, so digits do not jitter during count-up.
4. Borders, not shadows. Use 1 px `line` borders and 12–16 px radius on cards only.
5. Dense tables (32 px rows) inside generous sections (24–32 px padding).
6. Every chart has a one-sentence title that states the takeaway ("Gate 2 denied 14% of checked documents"), not just "Gate 2 verdicts".
7. Empty, loading and error states for every panel. Skeletons match the final layout.
8. Projector check: test both themes at 1280×720 with the browser zoomed to 125%.
9. Bundle fonts locally (no Google Fonts fetch at demo time).
10. No gradients, no glass, no emoji, no neon (house rule). "Restraint reads as professional; decoration reads as AI."

---

## 5. Mode switch: User mode / Developer mode

### 5.1 How other products separate the two

| Product | How it switches | What changes | Lesson for us |
|---|---|---|---|
| **Figma Dev Mode** | A toggle in the toolbar or `Shift D`; a Dev Mode link opens straight into it ([Figma help](https://help.figma.com/hc/en-us/articles/15023124644247)) | Same file, a developer lens: an Inspect panel with specs, code and links | **Same object, different lens.** The selection survives the switch |
| **Stripe sandbox / live** | Account picker; sandbox shows a test-data banner ([Stripe testing](https://docs.stripe.com/test-mode)) | **Different data.** Objects in one mode are not reachable from the other | A loud banner is right when data differs. Ours does not, so stay quiet |
| **Stripe Workbench** | `~` opens or minimises it anywhere; a resizable bottom pane that collapses to a taskbar; "Auto-inspect" follows the page you are on; "Copy link" on every view ([Workbench](https://docs.stripe.com/workbench/overview)) | Developer tools layered over the normal dashboard | **Keyboard toggle, in-context inspection, shareable links** |
| **Notion app vs API** | Integrations live in a separate developer area; each integration gets explicit capabilities and no page access by default ([Notion internal integrations](https://developers.notion.com/guides/get-started/internal-integrations)) | A separate surface with its own permissions | Developer power must never widen access |
| **Datadog** | No mode; depth on demand (flame graph by default, waterfall and span details on click; Watchdog Explains in full screen) | Progressive disclosure | Default to the simple view and let depth be one click away |
| **Grafana query editor** | Builder / Code tabs kept in sync; Builder is for less experienced users; "Explain" toggle ([Prometheus editor](https://grafana.com/docs/grafana/latest/datasources/prometheus/query-editor/)) | Same query, two views | Sync both modes: what you see in one maps to the other |

Background: progressive disclosure shows the important options first and defers specialised ones to a secondary display ([Nielsen](https://jakobnielsenphd.substack.com/p/progressive-disclosure)).

### 5.2 Recommended UX

1. **Control:** a Radix `ToggleGroup` in the header, right of the persona chip, labelled in text: `User | Developer`. Shortcut: backtick (`` ` ``), Stripe-style. Add `Shift D` as an alias for judges who know Figma.
2. **Routing:** Developer mode lives under `/dev/*`, so every view has a URL a judge can bookmark or we can put on a slide. The mode is remembered in `localStorage` (wrapped in try/catch), but the URL wins.
3. **Context carries over (Figma, Workbench auto-inspect):**
   - On the Ask page with an answer showing, switch to `/dev/journey/{req_id}` for that answer.
   - On the Map page with a place selected, switch to `/dev/graph?node={container}`.
   - On Compare, switch to `/dev/access?a=jdoe&b=ken.ext`.
   - On Audit, switch to `/dev/audit` with the same filter.
   Switching back returns to the user page you came from.
4. **Quiet signal:** a 2 px accent rule along the top of the shell plus a small `Developer` pill. The layout gets denser (a left rail with sections), and the theme does *not* change (finding 9).
5. **In-context drawer (P1):** on the Ask page in User mode, a small "How was this answered?" link opens a Workbench-style bottom drawer with the waterfall. That teaches users that Developer mode exists.
6. **First visit:** a three-step coach mark reusing `lib/tour.ts`: "Same person, same permissions; more instrumentation", "Every number links to the ledger", "Press ` to switch back".

### 5.3 The guardrail, stated in the UI

> **Developer mode adds instrumentation, never data.** You see the same documents you could see in User mode. Gate decisions on other people's requests and denial reasons are visible only to the compliance role.

This is hard constraint 4 turned into a product feature. It also gives judges the strongest possible line: "even our debug view can't leak".

---

## 6. Developer mode spec for Walnut DataBridge

### 6.1 Information architecture

```
/dev                         Analysis (end-to-end success page)          compliance lens; employees see "My activity"
/dev/graph                   Knowledge-graph explorer (sigma)             permitted subgraph of the signed-in person
/dev/graph?node=&hops=&asOf=&color=
/dev/journey                 Recent requests list (mine, or all for compliance)
/dev/journey/[reqId]         Request journey: brain diagram + waterfall + decision lane + ledger
/dev/provenance/[docId]      Provenance chain: answer sentence → citation → document → source pointer → ledger seal
/dev/access                  Permission overlay: person A against person B (demo logins via slots, like /compare)
/dev/audit                   Audit trail + chain verification (reuses components/audit/*)
```

Left rail order follows the pipeline: Analysis · Graph · Journey · Provenance · Access · Audit. Header: mode toggle, persona chip, `⌘K`, theme toggle.

### 6.2 Page-by-page wireframes

**A. `/dev`: Analysis (exact metrics in 6.3)**

```
┌ Developer · Analysis                  Window [1d|7d|30d]  Replay [incl|excl]  Lens: compliance ┐
│ ┌Questions──┐┌Answer rate┐┌Gate-2 deny┐┌p95 latency┐┌Grounding──┐┌Leaks (eval)───────────┐   │
│ │ 412  ▁▃▅▇ ││ 81.3 %    ││ 14.2 %    ││ 1,620 ms  ││ 9 drops   ││ 0 / 5,958 · ≤0.10 %  │   │
│ │ ledger·30d││ 335 / 412 ││ 58 / 409  ││ p50 940   ││ 0.03/ans  ││ eval 14 Oct · sha3599│   │
│ └───────────┘└───────────┘└───────────┘└───────────┘└───────────┘└──────────────────────┘   │
│ ┌ Request flow (Sankey) ─────────────────────────┐ ┌ Document flow (bar list) ──────────────┐│
│ │ asked ═╦═ answered ═╦═ Hy3                      │ │ considered   ████████████████  1,980   ││
│ │        ║            ╠═ DeepSeek                 │ │ Gate-2 check ██████████████    1,702   ││
│ │        ║            ╚═ extractive fallback      │ │ allowed      ████████████      1,461   ││
│ │        ╠═ refused                               │ │ denied       ██                  241   ││
│ │        ╚═ failed                                │ │ released     ██████████        1,210   ││
│ └─────────────────────────────────────────────────┘ │ cited        ██████              733   ││
│ ┌ Stage latency p50 ▮ / p95 ┤ ──────────────────┐ └────────────────────────────────────────┘│
│ │ accept ▮┤   gate1 ▮▮┤   gate2 ▮▮▮▮┤  seal ▮┤   compose ▮▮▮▮▮▮▮▮▮┤                      │
│ └──────────────────────────────────────────────────────────────────────────────────────────┘│
│ ┌ Requests per day (answered / refused / failed) ┐ ┌ Gate-2 reasons ─────┐ ┌ Model mix ────────┐│
│ └─────────────────────────────────────────────────┘ └────────────────────┘ └──────────────────┘│
│ ┌ Platform coverage + freshness ──────────────────────────────────────────────────────────────┐│
│ │ platform    considered  Gate-2 checked  released  last sync   lag / SLO     state          ││
│ │ confluence  …           …               …         14:01:52    9 s / 60 s    ● within SLO   ││
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘│
│ ┌ Per-person access (table + inline bars) ┐ ┌ Leak eval heatmap: persona × probe type ─────┐  │
│ └──────────────────────────────────────────┘ └──────────────────────────────────────────────┘  │
│ ┌ Ledger integrity: events 18,402 · seq 1–18,402 · head 7c2e…91ab · [Verify chain] ● intact ┐ │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

(The numbers in the wireframe are placeholders that show layout. Real values come from the endpoints.)

**B. `/dev/graph`: Knowledge-graph explorer**

```
┌ Graph · signed in as jdoe · showing 47 of 1,232 items you can open (subgraph: all permitted) ───┐
│ [⌘K search…]  Colour by [platform ▾]  Size by [degree ▾]  Hops [1|2|3]  [List view]  [Reset]   │
│ ┌──────────────────────────────────────────────────────────────┐ ┌ Node drawer ──────────────┐ │
│ │                                                              │ │ Payments runbook v4       │ │
│ │                 (sigma WebGL canvas)                         │ │ confluence · PAY space    │ │
│ │                                                              │ │ modified 12 Oct · v4      │ │
│ │                                                              │ │ Source pointer ↗ (copy)   │ │
│ │                                                              │ │ Neighbours (2 hops)       │ │
│ │                                                              │ │  cites → PAY-112 (jira)   │ │
│ │ ┌Legend──────────────┐                     [+][-][⤢]        │ │  discussed in #pay-ops    │ │
│ │ │● Confluence ● Jira │                                       │ │ Readers: Payments team    │ │
│ │ │● Slack ● Drive ○ person│                                   │ │ [Open provenance] [Ask]   │ │
│ └──────────────────────────────────────────────────────────────┘ └───────────────────────────┘ │
│ ▁▂▃▅▇▅▃▂▁▁▂▃▅▆▇  as of [══════●═══════●══]  1 Aug ─────── 30 Oct   [▶ Play]                    │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Data: a new `GET /ib/graph` that returns the **signed-in person's permitted** nodes and edges with precomputed `x,y` (section 6.6). The explorer never receives a node the person cannot open, so the graph *is* the permission proof.

**C. `/dev/journey/[reqId]`: Brain + request journey.** Brain diagram on top (section 2.4); waterfall and decision lane below (section 2.3 wireframe); tabs for Documents, Citations, Ledger records and Raw JSON. A lens chip in the header shows "My trace" or "Compliance journey". `/dev/journey` lists recent requests (TanStack Table: time, asker, status, latency, model, citations) with status filters.

**D. `/dev/provenance/[docId]`: Provenance chain** (React Flow + dagre, left to right):

```
[Answer sentence 2] → [Citation [2]] → [Document: Payments runbook v4 · confluence]
        → [SourcePointer: confluence://PAY/123456?v=4 · sha 5b1e…] → [Live check: Gate 2 verified 14:02:11]
        → [Ledger #1043 retrieval.decided 3e07…] → [Ledger #1045 answer.released 9c1e…]
```

Each node opens a popover with the raw pointer and a "Re-check now" button that calls the live source check again. That is Walnut's re-executable pointer, shown live.

**E. `/dev/access`: Permission overlay**

```
┌ Access · A: jdoe (Payments)   B: ken.ext (External contractor)   [swap]  [same question ▸] ┐
│ Overlay: ● both can open  ◐ only A  ◑ only B  (nodes nobody here can open are not drawn)   │
│ ┌ sigma canvas: same layout, union of both permitted sets, coloured by overlay class ─────┐ │
│ └───────────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ By platform: A / B / both ───────────┐ ┌ Same question, two answers (reuses /compare) ──┐ │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Each side signs in through its own demo slot (as `/compare` already does) and fetches its own `/ib/graph`. The overlay is a client-side set comparison of two independently authorised payloads. No endpoint ever takes a user argument (hard constraint 2). Label the page "demo: two signed-in sessions".

**F. `/dev/audit`: Audit trail.** Reuse `AuditTable`, `AuditDrawer` and `VerifyPanel`. Add:
- a **chain strip**: the last 30 ledger records as linked blocks (`seq · kind · hash₄`), where each block's `prev` arrow points to the previous hash;
- a "Tamper demo" explainer that links to `test_audit_tamper.py` rather than tampering live;
- a "Verify chain" button showing `records`, `ok`, and content-store `intact` from `/audit/verify`.

### 6.3 End-to-end analysis page: exact metrics and charts

Source key: **S** = `GET /audit/stats?days=&replay=` (compliance only) · **V** = `GET /audit/verify` · **F** = `GET /ib/sources` · **E** = `evals/internal_brain/results.json` (serve read-only, or import at build time with its `generated_at` and `config.world_sha256`) · **NEW** = needs a backend field (section 6.6).

| # | Metric | Definition | Field | Chart | Drill-down |
|---|---|---|---|---|---|
| 1 | Questions asked | `request.accepted` events in window | S `totals.requests` | KPI tile + 30-day sparkline (`days[].asked`) | Audit events, kind=request.accepted |
| 2 | Answered / refused / failed | Final outcome per request | S `totals.answered/refused/failed` | Tile shows answered; stacked bars per day (`days[]`) with the total labelled | Events filtered by outcome |
| 3 | Answer rate | answered ÷ (answered + refused) | S `totals.answer_rate` | KPI tile; percent with the raw counts under it | Same as 2 |
| 4 | Request flow | asked → answered / refused / failed; answered → model | S `totals.*`, `models{}`, `totals.fallback_used` | **Hero Sankey** (@visx/sankey) | Click a band to open the filtered request list |
| 5 | Document funnel | considered → Gate-2 checked → allowed / denied → released → cited | S `funnel{}` | Horizontal bar list with exact counts and percent of previous step | Bar opens events with decision filter |
| 6 | Gate-2 deny rate | denied ÷ checked | S `funnel.gate2_denied / gate2_checked` (ratio from the backend, NEW) | KPI tile | Gate-2 reasons |
| 7 | Gate-2 verdicts and reason codes | Count by `verdict` and `reason_code` | S `gate2.verdicts`, `gate2.reasons` | Two bar lists | Events by reason |
| 8 | Gate-1 pass and deny counts | Index pre-filter decisions | NEW (today only `considered_docs` is counted; Gate-1 removals are not) | Bar list beside Gate 2 | — |
| 9 | End-to-end latency | p50, p95, max, count | S `latency_ms` | KPI tile (p95, with p50 underneath) | Slowest 10 requests |
| 10 | Latency per stage | p50 and p95 for accept, gate1, gate2, seal, compose | S `stages_ms` | Horizontal bars (p50 bar, p95 whisker), in pipeline order | Journey of the p95 request |
| 11 | Model used | Count by provider/model; extractive fallback count | S `models{}`, `totals.fallback_used` | Bar list; fallback shown as its own bar, not hidden | Requests by model |
| 12 | Grounding drops | Sentences removed by the grounding wall; per answered request | S `totals.dropped_sentences` (per-answer ratio NEW) | KPI tile + per-day line (NEW `days[].dropped`) | Requests with drops > 0 |
| 13 | Citations | Total and per answered request | S `totals.citations` | Small stat under tile 3 | — |
| 14 | Leak eval | Leaks, requests, 95% upper bound, payload upper bound | E `headline.leaks`, `n_requests_all_modes` (5,958), `leak_rate_upper95`, `payload_leak_upper95` | **Evidence tile** with an "eval" chip, the generated_at date and the world hash | Link to `RESULTS.md` |
| 15 | Restricted = absent (P2) | Paired answers that differ | E `headline.p2_compared` (400), `p2_mismatches` (0) | Evidence tile | `RESULTS.md` |
| 16 | Injection | Hard failures across injection probes | E `headline.injection_hard_failures` (0) | Evidence tile | `RESULTS.md` |
| 17 | Leak eval by persona × probe type | Requests and leaks per cell | E `taxonomy` (11 personas) × `taxonomy_by_type` (16 types) | **Heatmap (CSS grid)**: shade = requests, red outline only if leaks > 0 (there are none) | Cell tooltip with counts |
| 18 | Freshness | Lag since last sync per platform against the SLO | F `sources[].lag_s`, `slo_s`, `within_slo`, `state` | Table column with a lag bar against an SLO tick | — |
| 19 | Per-platform coverage | considered, Gate-2 checked, released per platform | S `platforms{}` | Grouped table with inline bars (4 rows) | Events by platform |
| 20 | Per-person access | asked, answered, refused per person | S `people[]` | TanStack Table with inline bars, sorted by asked | Person's requests |
| 21 | Channels | UI against MCP | S `channels{}` | Two-segment bar | — |
| 22 | Ledger integrity | Events, first and last seq, head hash; verify ok; content intact | S `ledger{}`, V `ok`, `records`, `content.intact` | Integrity strip + "Verify chain" button | `/dev/audit` |
| 23 | Governance activity | reveals, audit queries, access changes, syncs | S `totals.reveals`, `audit_queries`, `access_changes`, `syncs` | Four small stats in a footer row | Events by kind |

Employees (not compliance) in Developer mode see a reduced "My activity" version: their own requests, latency and models from `/ib/history` + `/ib/trace`, freshness from `/ib/sources`, and the eval evidence tiles. No Gate-2 counts.

### 6.4 Library picks with pinned versions

All versions checked against the npm registry on 30 Oct 2026; each is more than two weeks old and declares React 18 support.

| Area | Package | Version | Published | Licence | Gzip |
|---|---|---|---|---|---|
| Graph render | `sigma` | 3.0.3 | 30 Apr 2026 | MIT | 26 KB |
| Graph model | `graphology` | 0.26.0 | — | MIT | 13 KB |
| React binding | `@react-sigma/core` | 5.0.6 | 1 Dec 2025 | MIT | small |
| Layout | `graphology-layout-forceatlas2` 0.10.1, `graphology-layout` 0.6.1, `@react-sigma/layout-forceatlas2` 5.0.6 | — | — | MIT | small |
| Communities | `graphology-communities-louvain` | 2.0.2 | — | MIT | small |
| Node and edge programs | `@sigma/node-border` 3.0.0, `@sigma/edge-curve` 3.1.0 | — | 2024 | MIT | small |
| Diagrams | `@xyflow/react` | 12.12.0 | 24 Sep 2026 | MIT | 60 KB |
| DAG layout | `@dagrejs/dagre` | 3.0.0 | 22 Mar 2026 | MIT | small |
| Charts | `recharts` + `react-is` | 3.10.1 + 18.3.1 | 25 Jul 2026 | MIT | 150 KB |
| Hero Sankey | `@visx/sankey`, `@visx/responsive` | 4.0.0 | 11 Jun 2026 | MIT | small |
| Tables | `@tanstack/react-table` | 8.21.3 | 14 Apr 2025 | MIT | small |
| Motion | `framer-motion` | 12.43.0 | 28 Jul 2026 | MIT | 61 KB full / ~5 KB lazy initial |
| Palette | `cmdk` | 1.1.1 | 14 Mar 2025 | MIT | 17 KB |
| Primitives | Radix train of 1 Oct 2024 (section 4.1) | see 4.1 | 2024 | MIT | small |
| Optional | `zustand` 5.0.12 (view state), `@tanstack/react-query` 5.99.2 | — | Mar–Apr 2026 | MIT | small |

Deliberately not chosen for now: sigma 4.x, @react-sigma 6.x, framer-motion 13–14, TanStack Table 9, the latest Radix 2026 train, Tremor, ECharts, G6, react-force-graph-3d.

### 6.5 Component list

| Component | Purpose | Built on | Lane |
|---|---|---|---|
| `DevShell` | Left rail, header, accent rule, `Developer` pill | Tailwind | A |
| `ModeToggle` | User / Developer switch, backtick shortcut, context-carry router | Radix ToggleGroup | A |
| `CommandPalette` | `⌘K` navigation, node search, request open, persona switch | cmdk | A |
| `Grounded` | Every value with a source popover (house rule) | Radix HoverCard | A |
| `KpiTile` | Label, value, delta, sparkline, source chip | Recharts `LineChart` (sparkline) | D |
| `SourceChip` | "ledger · 30 d" / "eval · 14 Oct · sha…" | Tailwind | A |
| `HashBadge` | `a91c…3e07`, mono, copy on click | Tailwind | A |
| `LensChip` | "My trace" / "Compliance journey" | Tailwind | C |
| `GraphCanvas` | Sigma container, reducers, camera, events | @react-sigma/core | B |
| `GraphControls` | Colour-by, size-by, hops, list view, zoom buttons | Radix Select, ToggleGroup | B |
| `GraphLegend` | Active rules + "N of M shown" caption | Tailwind | B |
| `NodeDrawer` | Node facts, pointer, neighbours, actions | Radix Dialog (side sheet) | B |
| `TimeSlider` | Histogram + range + play | Radix Slider + divs | B |
| `GraphListView` | Accessible table twin of the canvas | TanStack Table | B |
| `OverlayGraph` | A against B overlay classes | GraphCanvas | B |
| `PipelineDiagram` | Brain view with replay | @xyflow/react | C |
| `JourneyWaterfall` | Stage bars, ledger ticks, critical path | Tailwind divs | C |
| `DecisionLane` | Gate inputs, outputs and reasons per document | TanStack Table | C |
| `LedgerStrip` | Linked hash blocks | Tailwind + framer-motion | C/F |
| `ProvenanceChain` | Sentence to ledger DAG | @xyflow/react + dagre | C |
| `RequestSankey` | Hero request flow | @visx/sankey | D |
| `BarList` | Funnels, reasons, models | Tailwind divs | D |
| `StageLatency` | p50 bar + p95 whisker per stage | Recharts `BarChart` with `ErrorBar` | D |
| `DailyOutcomes` | Answered / refused / failed per day | Recharts `BarChart` | D |
| `EvalHeatmap` | Persona × probe type grid | CSS grid | D |
| `FreshnessTable` | Lag against SLO per platform | TanStack Table | D |
| `IntegrityStrip` | Ledger counts, head, Verify button | reuses `VerifyPanel` | F |

### 6.6 Small backend asks (coordinator-owned; all read-only)

1. **`GET /ib/graph`**: the signed-in person's permitted nodes and edges, with precomputed deterministic `x,y`, `platform`, `kind`, `container`, `modified_at` and `degree`. Same role rule as `/ib/map`; ledger a `map.viewed`-style event. This is the largest item.
2. **Stats additions** in `compute_stats`: `gate1` removed counts, `gate2_deny_rate`, `dropped_per_answer`, and `days[].dropped`. All are server-side ratios, so the frontend does no maths.
3. **A `ground` stage timing** split out of `compose` in `stage_ms`, so grounding gets its own bar.
4. **`GET /dev/eval`** (or a build-time import): returns `results.json` `headline`, `taxonomy`, `taxonomy_by_type`, `generated_at` and `world_sha256`.

### 6.7 Prioritised, buildable recommendations (about 3 days, 5 parallel lanes)

Lanes own disjoint files (house rule: parallel agents only on disjoint files). **Coordinator** owns `package.json`, `lib/types.ts`, `lib/api.ts`, `lib/mock/*`, `AppShell` navigation and the backend. **A** = shell and design system (`components/dev/shell/*`, `components/ui/*`, `app/dev/layout.tsx`). **B** = graph (`components/dev/graph/*`, `app/dev/graph`, `app/dev/access`). **C** = journey, brain and provenance (`components/dev/journey/*`, `app/dev/journey`, `app/dev/provenance`). **D** = analysis (`components/dev/analysis/*`, `app/dev/page.tsx`). **E** = tests and evidence (`tests/e2e/dev-*.spec.ts`, screenshots).

| # | Pri | Lane | Day | Recommendation | Done when |
|---|---|---|---|---|---|
| 1 | P0 | Coord | 1 | Install the pinned packages in one commit (sections 1.4, 3.4, 4.1, 4.2); add `lib/types.ts` for stats, journey, trace and graph payloads; add mock fixtures for each | `npm run typecheck` passes; `dev:mock` serves all dev payloads |
| 2 | P0 | A | 1 | `DevShell` + `/dev` layout + left rail in pipeline order + accent rule + `Developer` pill | Every `/dev/*` route renders the shell in both themes |
| 3 | P0 | A | 1 | `ModeToggle` with backtick and `Shift D`, URL-first state, context carry-over (Ask → journey, Map → graph, Compare → access, Audit → audit) | e2e: toggling from an answer opens its journey |
| 4 | P0 | A | 1 | Copy shadcn v3 primitives by hand (Button, Card, Tabs, Tooltip, Popover, HoverCard, Select, Slider, Sheet, Badge, Table), mapped to existing tokens | No new colour outside the tokens |
| 5 | P0 | A | 1 | `Grounded`, `SourceChip`, `HashBadge`; tabular-nums utility class | Every number on `/dev` sits in `Grounded` |
| 6 | P0 | D | 1–2 | KPI row (tiles 1, 3, 6, 9, 12, 14) from `/audit/stats` and eval evidence | Values match the API JSON exactly |
| 7 | P0 | D | 2 | Request-flow Sankey (`@visx/sankey`) and document-flow `BarList`, kept separate (section 3.3) | Clicking a band filters the request list |
| 8 | P0 | D | 2 | Stage latency p50/p95 chart in pipeline order, plus the model mix with fallback shown | Stage order matches `STAGES` |
| 9 | P0 | C | 1–2 | `JourneyWaterfall` + `DecisionLane` + ledger ticks, with both lenses (`/ib/trace` and `/audit/journey`) | As `jdoe`: no considered set or denial reasons in the DOM (e2e asserts) |
| 10 | P0 | C | 2 | `PipelineDiagram` brain view with replay scaled from `stage_ms`, and a reduced-motion fallback | Replay under 3.5 s; static under reduced motion |
| 11 | P0 | Coord | 1–2 | `GET /ib/graph` with a deterministic precomputed layout (FA2 run offline from a circular seed) + mock | Same coordinates on every call |
| 12 | P0 | B | 2 | `GraphCanvas` (sigma 3.0.3) with dark-theme overrides, platform colours, legend and "N of M" caption | 1,232-node mock pans smoothly on the demo laptop (measure FPS in Chrome DevTools, target ≥ 50) |
| 13 | P0 | B | 2 | Ego-network on click with Kumu keys (`+`, `-`, `1`–`3`, `Esc`) and `NodeDrawer` | Focus set dims the rest; drawer lists neighbours |
| 14 | P0 | B | 2–3 | Search-to-focus through `⌘K` and the URL (`?node=&hops=`) | A shared link reopens the same view |
| 15 | P0 | E | 3 | e2e + axe for every `/dev` page; a permission test proving `jdoe` never receives a node or reason they cannot open | `npm run e2e` green; axe has no serious issues |
| 16 | P1 | B | 3 | Permission overlay `/dev/access` (two slots, union layout, three overlay classes) | `jdoe` against `ken.ext` shows "only A" nodes |
| 17 | P1 | B | 3 | Time slider with histogram and Play (filters by `modified_at`) | Play steps through weeks in about 6 s |
| 18 | P1 | B | 3 | Semantic zoom: containers when zoomed out, documents when zoomed in; label thresholds tuned | No label overlap at the default zoom |
| 19 | P1 | C | 3 | Provenance chain page (React Flow + dagre) with "Re-check now" on the source pointer | Chain ends at a ledger seal |
| 20 | P1 | D | 2–3 | Freshness table from `/ib/sources`, platform coverage, per-person table | Lag bar shows the SLO tick |
| 21 | P1 | D | 3 | Eval heatmap (persona × probe type) from `results.json` | 11 × 16 grid, accessible cell labels |
| 22 | P1 | A | 2 | `CommandPalette` (navigation, node search, open request, switch persona, verify chain) | Every command reachable by keyboard |
| 23 | P1 | F (in A/C) | 3 | Audit chain strip + Verify button on `/dev/audit` (reuse existing components) | Verify shows records, ok and content intact |
| 24 | P1 | Coord | 2 | Stats additions (Gate-1 counts, deny rate, drops per answer, daily drops) and the `ground` stage | Tiles 6, 8 and 12 show backend values |
| 25 | P1 | A | 3 | Motion pass: LazyMotion, `MotionConfig reducedMotion="user"`, count-up on first load only | No animation loops; reduced motion respected |
| 26 | P2 | B | 3 | `GraphListView` table twin + canvas `aria-label` summary | A screen reader can reach every visible node |
| 27 | P2 | C | 3 | In-context "How was this answered?" bottom drawer on the Ask page (Workbench pattern) | Opens the waterfall without leaving the page |
| 28 | P2 | E | 3 | Screenshot every dev page in both themes into `Tencent/submission/` (organised) for the deck and video | Screenshots filed by page |

**Cut line if time runs short:** ship 1–15 (the P0s). They already cover the graph, brain view, journey, analysis and permission proof. Items 16, 17 and 23 are the highest-value P1s for judges.

### 6.8 Demo-day risks and checks

| Risk | Mitigation |
|---|---|
| WebGL context lost or a weak laptop GPU | Fall back to `GraphListView` automatically on `webglcontextlost`; keep the mock graph at its real size, not inflated |
| Layout looks different between runs | Ship precomputed `x,y`; never run the FA2 worker live |
| A dev view accidentally shows denial reasons to an asker | Lenses bound to endpoints, not to UI flags; e2e test in item 15 |
| Fonts or CDN unavailable offline | Bundle fonts locally; no CDN imports |
| Projector washes out a dark theme | Test at 1280×720, 125% zoom; raise `muted` contrast to at least 4.5:1 |
| Library churn | Exact pins (`-E`); lockfile committed; no upgrades after Day 2 |
| Next 14.2.15 is not the latest 14.2 patch (14.2.35 exists) | The app has no `middleware.ts`, so the 2025 middleware-bypass fix does not apply. Bump to the latest 14.2.x after Demo Day, not before |

---

## Sources

Graph libraries and tools
- [sigma.js](https://www.sigmajs.org/) · [@react-sigma/core on npm](https://www.npmjs.com/package/@react-sigma/core) · [react-sigma releases](https://github.com/sim51/react-sigma/releases)
- [React Flow performance guide](https://reactflow.dev/learn/advanced-use/performance) · [ReactFlow API (`onlyRenderVisibleElements`)](https://reactflow.dev/api-reference/react-flow) · [React Flow stress example](https://reactflow.dev/examples/stress) · [React Flow pan/zoom performance thread](https://lightrun.com/answers/wbkd-react-flow-performance-drop-when-panning-or-zooming)
- [Cytoscape.js WebGL preview](https://blog.js.cytoscape.org/2025/01/13/webgl-preview/) · [Cytoscape 3.31 release](https://blog.js.cytoscape.org/2025/01/13/3.31.0-release/)
- [AntV G6](https://github.com/antvis/g6/) · [G6 introduction](https://g6.antv.antgroup.com/en/manual/introduction)
- [Cosmograph library comparison (vendor)](https://cosmograph.app/docs-lib/comparison/) · [Ogma vs sigma (vendor)](https://doc.linkurious.com/ogma/5.3/compare/sigmajs.html) · [JS graph library comparison](https://dev.to/timlrx/a-comparison-of-javascript-graph-network-visualisation-libraries-34a8)
- [Gephi Lite](https://gephi.org/lite/)
- [Neo4j Bloom 2.0](https://neo4j.com/blog/developer/integrating-graph-data-visualizations-no-code-workflows-bloom-2-0/) · [Bloom 1.5.0 release notes](https://neo4j.com/release-notes/bloom/bloom-1-5-0/)
- [Linkurious time filtering](https://linkurious.com/blog/time-filtering-linkurious/) · [Ogma timeline plugin](https://doc.linkurious.com/ogma/latest/plugins/timeline.html)
- [Palantir Vertex: explore object relationships](https://palantir.com/docs/foundry/vertex/explore-object-relationships/) · [Vertex overview](https://www.palantir.com/docs/foundry/vertex/overview)
- [Kumu focus](https://docs.kumu.io/guides/focus) · [Kumu clustering](https://docs.kumu.io/guides/clustering)
- [Obsidian graph view](https://obsidian.md/help/plugins/graph)
- [Accessible graph visualisation (Cambridge Intelligence)](https://cambridge-intelligence.com/build-accessible-data-visualization-apps-with-keylines/)

Tracing
- [LangSmith tracing](https://docs.smith.langchain.com/tracing) · [LangSmith waterfall changelog](https://changelog.langchain.com/announcements/ann_t0pJcFOimB0OD)
- [Langfuse new trace view](https://langfuse.com/changelog/2025-03-19-new-trace-view) · [Langfuse responsive timeline](https://langfuse.com/changelog/2026-08-28-responsive-timeline) · [Langfuse agent graphs](https://langfuse.com/docs/observability/features/agent-graphs)
- [Arize Phoenix tracing](https://arize.com/blog/llm-tracing-and-observability-with-arize-phoenix) · [Arize: what are traces](https://arize.com/docs/ax/instrument/what-are-traces)
- [Datadog trace view](https://docs.datadoghq.com/tracing/trace_explorer/trace_view/)
- [Grafana trace integration and critical path](https://grafana.com/docs/grafana/v13.2/visualizations/explore/trace-integration/)

Dashboards and charts
- [Vercel Speed Insights](https://vercel.com/docs/speed-insights) · [Linear Insights](https://linear.app/docs/insights) · [Stripe Workbench](https://docs.stripe.com/workbench/overview) · [Grafana dashboard best practices](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/) · [Datadog Watchdog Explains](https://docs.datadoghq.com/dashboards/graph_insights/watchdog_explains/)
- [shadcn chart (Recharts v3 notes)](https://ui.shadcn.com/docs/components/chart) · [Vercel acquires Tremor](https://vercel.com/blog/vercel-acquires-tremor) · [ECharts style handbook](https://echarts.apache.org/handbook/en/concepts/style) · [bundlephobia](https://bundlephobia.com) (sizes queried 30 Oct 2026)

Design system, motion, modes
- [shadcn v3 docs](https://v3.shadcn.com/docs) · [shadcn legacy note](https://ui.shadcn.com/docs/legacy)
- [Motion: reduce bundle size](https://motion.dev/docs/react-reduce-bundle-size) · [Motion: LazyMotion](https://motion.dev/docs/react-lazy-motion)
- [How we redesigned the Linear UI](https://linear.app/now/how-we-redesigned-the-linear-ui) · [Vercel Geist](https://vercel.com/geist)
- [Figma Dev Mode guide](https://help.figma.com/hc/en-us/articles/15023124644247) · [Stripe sandboxes and test mode](https://docs.stripe.com/test-mode) · [Notion internal integrations](https://developers.notion.com/guides/get-started/internal-integrations) · [Grafana Prometheus query editor](https://grafana.com/docs/grafana/latest/datasources/prometheus/query-editor/) · [Progressive disclosure (Jakob Nielsen)](https://jakobnielsenphd.substack.com/p/progressive-disclosure)

Our own code and evidence (read on 30 Oct 2026)
- `databridge/apps/internal_brain/databridge_ib/audit/stats.py` (metrics), `audit/routes.py` (`/audit/stats`, `/audit/journey`, `/audit/verify`, compliance-only), `audit/query.py:461-520` (journey fields), `app.py:154-218` (`/ib/map`, `/ib/history`, `/ib/trace`, `/ib/sources`)
- `databridge/evals/internal_brain/results.json` (`headline`, generated 2026-10-14) and `RESULTS.md`
- `databridge/fixtures/enterprise/world.json` (entity counts)
- `databridge/apps/internal_brain_web/package.json`, `tailwind.config.ts`, `app/compare/page.tsx` (slot-based demo logins)
- `databridge/.claude/skills/databridge-frontend-dashboards/SKILL.md` (house rules)
