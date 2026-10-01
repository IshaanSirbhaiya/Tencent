"use client";
/**
 * `/graph` — the knowledge graph itself, laid out visually and TRAVERSABLE.
 *
 * The canvas is an ACCUMULATION, not a single query result:
 *
 *  - a BASE fetch (`GET /graph/neighborhood` — the hub-seeded default view, a kind/label/source
 *    filter, or a seed's BFS) REPLACES the canvas and bumps `layoutEpoch`, so the layout starts
 *    fresh;
 *  - an EXPAND (double-click a node, the panel's "Expand", an incident edge, or "+" on a search
 *    hit) fetches that node's bounded neighbourhood and MERGES it in, deduplicated by id; the
 *    layout pins everything already drawn, so the reader keeps their place;
 *  - a PATH (`GET /graph/path` between the two nodes the reader marked "from" / "to") merges the
 *    path's nodes and edges in and highlights them; when there is no path the reason is shown
 *    verbatim — out of hops, disconnected, or out of budget (unknown) are different answers.
 *
 *  - a TRAIL (`trail.tsx`) records every expand, search seed/add and path hop as breadcrumbs —
 *    node → edge kind → node — highlighted in the accent (the path keeps its amber), with
 *    back/forward, click-to-focus, clear and copy;
 *  - an AGENT TRAVERSAL (`/graph?run=<agent>/<run_id>`, `agent-traversal.tsx`) draws a run's graph
 *    tool calls as a numbered, highlighted walk, each step with its `produced_by`, and says plainly
 *    what the run record does not keep.
 *
 * Nothing here is invented: an empty store renders "No data yet — connect a source", not a
 * sample graph; every count is the gateway's.
 */
import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Plug, Route, X } from "lucide-react";
import {
  describeGraphError,
  graphApi,
  type GraphEdgeSummary,
  type GraphNodeSummary,
  type GraphPath,
  nodeLabelText,
} from "@/lib/graph-api";
import { isRefusal } from "@/lib/intake-types";
import { fetchPackRegistry } from "@/lib/packs";
import { RefusalCard } from "@/components/refusal-card";
import { ResizeHandle, useBottomPanelHeight } from "@/components/brain/resize-handle";
import { AskPanel } from "@/components/orchestrator/ask-panel";
import { FilterBar, type GraphFilters } from "./filter-bar";
import { GraphCanvas, type GraphSelection } from "./graph-canvas";
import { buildKindPalette } from "./kind-colors";
import { KindLegend } from "./kind-legend";
import { KindPaletteProvider } from "./kind-palette";
import { ProvenancePanel, type PathEnd } from "./node-panel";
import { SeedSearch } from "./seed-search";
import {
  EMPTY_TRAIL,
  TrailBar,
  pushTrail,
  resolveLinks,
  trailHighlight,
  type TrailAction,
  type TrailState,
  type TrailStep,
} from "./trail";
import { AgentTraversalPanel, agentHighlight, loadAgentTraversal } from "./agent-traversal";

const DEFAULT_FILTERS: GraphFilters = {
  kind: "",
  source: "",
  q: "",
  depth: 2,
  limit: 60,
  includeClosed: false,
};

const PATH_DEPTHS = [4, 6, 8, 10];

interface Canvas {
  nodes: Map<string, GraphNodeSummary>;
  edges: Map<string, GraphEdgeSummary>;
}

const EMPTY_CANVAS: Canvas = { nodes: new Map(), edges: new Map() };

function mergeInto(
  canvas: Canvas,
  nodes: GraphNodeSummary[],
  edges: GraphEdgeSummary[],
): { next: Canvas; addedNodes: number; addedEdges: number } {
  const n = new Map(canvas.nodes);
  const e = new Map(canvas.edges);
  let addedNodes = 0;
  let addedEdges = 0;
  for (const node of nodes) {
    if (!n.has(node.id)) addedNodes += 1;
    n.set(node.id, node);
  }
  for (const edge of edges) {
    if (!e.has(edge.id)) addedEdges += 1;
    e.set(edge.id, edge);
  }
  return { next: { nodes: n, edges: e }, addedNodes, addedEdges };
}

type Notice = { tone: "info" | "warn"; text: string } | null;

export function GraphView() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  // `?source=<connection_id>` — linked from a connection's run (C1). A pointer's `source_id` IS the
  // connection id (`mcp_ingest.build.source_ref`), so this is the gateway's own reverse-index
  // browse (`/graph/neighborhood?source=`): the nodes that source grounds.
  const sourceParam = searchParams.get("source") ?? "";
  const [filters, setFilters] = React.useState<GraphFilters>(() => ({
    ...DEFAULT_FILTERS,
    source: sourceParam,
  }));
  React.useEffect(() => {
    if (sourceParam) setFilters((prev) => ({ ...prev, source: sourceParam }));
  }, [sourceParam]);
  const [seed, setSeed] = React.useState<string | null>(null);
  const [selection, setSelection] = React.useState<GraphSelection>(null);
  const [canvas, setCanvas] = React.useState<Canvas>(EMPTY_CANVAS);
  const [layoutEpoch, setLayoutEpoch] = React.useState(0);
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const [expanding, setExpanding] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<Notice>(null);
  const [pathEnds, setPathEnds] = React.useState<{ from: string | null; to: string | null }>({
    from: null,
    to: null,
  });
  const [pathDepth, setPathDepth] = React.useState(6);
  const [bottomHeight, setBottomHeight] = useBottomPanelHeight();
  const canvasRef = React.useRef<Canvas>(canvas);
  canvasRef.current = canvas;
  const [trail, setTrail] = React.useState<TrailState>(EMPTY_TRAIL);
  const record = React.useCallback(
    (steps: TrailStep[]) => setTrail((prev) => pushTrail(prev, steps)),
    [],
  );

  // ---- agent traversal (`?run=<agent>/<run_id>`) ------------------------------------------------
  const runParam = searchParams.get("run");
  const agentQ = useQuery({
    queryKey: ["graph-agent-traversal", runParam],
    queryFn: () => loadAgentTraversal(runParam as string),
    enabled: !!runParam,
    retry: false,
  });
  const agentTraversal = agentQ.data?.ok ? agentQ.data.traversal : null;
  // While true the agent's traversal owns the canvas: the base browse does not overwrite it. Any
  // explicit re-base by the reader (new seed, new filter) hands the canvas back.
  const agentOwnsCanvas = React.useRef(false);
  React.useEffect(() => {
    if (!agentTraversal) return;
    agentOwnsCanvas.current = true;
    setCanvas(mergeInto(EMPTY_CANVAS, agentTraversal.nodes, agentTraversal.edges).next);
    setExpanded(new Set());
    setLayoutEpoch((x) => x + 1);
    setNotice(null);
  }, [agentTraversal]);

  const facetsQ = useQuery({ queryKey: ["graph-facets"], queryFn: graphApi.facets });
  const registryQ = useQuery({
    queryKey: ["pack-registry"],
    queryFn: fetchPackRegistry,
    staleTime: Infinity,
  });

  // The BASE query. Depth matters only when there is a seed; leaving it out of the browse key
  // means changing "expand depth" does not throw away a canvas the reader has built up.
  const neighborhoodQ = useQuery({
    queryKey: [
      "graph-neighborhood",
      seed,
      filters.kind,
      filters.source,
      filters.q,
      filters.limit,
      filters.includeClosed,
      seed ? filters.depth : null,
    ],
    queryFn: () =>
      graphApi.neighborhood({
        node: seed ?? undefined,
        kind: filters.kind || undefined,
        source: filters.source || undefined,
        q: filters.q || undefined,
        depth: seed ? filters.depth : undefined,
        limit: filters.limit,
        includeClosed: filters.includeClosed,
      }),
  });

  const facets = facetsQ.data && !isRefusal(facetsQ.data) ? facetsQ.data : null;
  const facetsRefusal = facetsQ.data && isRefusal(facetsQ.data) ? facetsQ.data : null;
  const nb = neighborhoodQ.data && !isRefusal(neighborhoodQ.data) ? neighborhoodQ.data : null;
  const nbRefusal =
    neighborhoodQ.data && isRefusal(neighborhoodQ.data) ? neighborhoodQ.data : null;

  // A new base result REPLACES the canvas (unless an agent run's traversal is on it).
  React.useEffect(() => {
    if (!runParam) agentOwnsCanvas.current = false; // left the agent view: the browse is back
    if (!nb || agentOwnsCanvas.current) return;
    setCanvas(mergeInto(EMPTY_CANVAS, nb.nodes, nb.edges).next);
    setExpanded(new Set(nb.seed ? [nb.seed] : []));
    setLayoutEpoch((x) => x + 1);
    setNotice(null);
  }, [nb, runParam]);

  const palette = React.useMemo(
    () =>
      buildKindPalette(
        registryQ.data ?? null,
        (facets?.node_kinds ?? []).map((k) => k.kind),
      ),
    [registryQ.data, facets],
  );

  // ---- expand (merge) --------------------------------------------------------------------------
  const expand = React.useCallback(
    async (id: string, action: TrailAction | null = "expand") => {
      setExpanding(id);
      setSelection({ type: "node", id });
      try {
        const res = await graphApi.neighborhood({
          node: id,
          depth: filters.depth,
          limit: filters.limit,
          includeClosed: filters.includeClosed,
        });
        if (isRefusal(res)) {
          setNotice({ tone: "warn", text: res.refusal.message });
          return;
        }
        // Counted against the canvas as last rendered; the merge itself goes through the updater
        // so two expands in flight cannot overwrite each other.
        const added = mergeInto(canvasRef.current, res.nodes, res.edges);
        setCanvas((prev) => mergeInto(prev, res.nodes, res.edges).next);
        setExpanded((prev) => new Set(prev).add(id));
        if (action) record([{ nodeId: id, action }]);
        const base = `Expanded ${id} (${res.depth ?? filters.depth} hop${
          (res.depth ?? filters.depth) === 1 ? "" : "s"
        }): ${added.addedNodes} new node(s), ${added.addedEdges} new edge(s).`;
        setNotice(
          res.truncated
            ? { tone: "warn", text: `${base} ${res.note ?? "The neighbourhood was cut at the window."}` }
            : { tone: "info", text: base },
        );
      } catch (err) {
        setNotice({ tone: "warn", text: describeGraphError(err) });
      } finally {
        setExpanding(null);
      }
    },
    [filters.depth, filters.limit, filters.includeClosed, record],
  );

  const handleSeed = React.useCallback(
    (id: string) => {
      agentOwnsCanvas.current = false;
      setSeed(id);
      setSelection({ type: "node", id });
      record([{ nodeId: id, action: "seed" }]);
    },
    [record],
  );

  const changeFilters = React.useCallback((next: GraphFilters) => {
    agentOwnsCanvas.current = false;
    setFilters(next);
  }, []);

  // ---- path ------------------------------------------------------------------------------------
  const pathQ = useQuery({
    queryKey: ["graph-path", pathEnds.from, pathEnds.to, pathDepth],
    queryFn: () => graphApi.path(pathEnds.from as string, pathEnds.to as string, pathDepth),
    enabled: !!pathEnds.from && !!pathEnds.to,
  });
  const path: GraphPath | null = pathQ.data && !isRefusal(pathQ.data) ? pathQ.data : null;
  const pathRefusal = pathQ.data && isRefusal(pathQ.data) ? pathQ.data : null;

  // A found path is merged into the canvas so every hop is drawn, even ones never expanded.
  React.useEffect(() => {
    if (!path?.found) return;
    setCanvas((prev) => mergeInto(prev, path.nodes, path.edges).next);
    // Every hop joins the trail, each with the edge the path response named.
    record(
      path.nodes.map((n, i) => ({
        nodeId: n.id,
        action: "path" as const,
        edgeId: i > 0 ? path.edges[i - 1]?.id : undefined,
      })),
    );
  }, [path, record]);

  const pathNodeIds = React.useMemo(
    () => (path?.found ? new Set(path.nodes.map((n) => n.id)) : undefined),
    [path],
  );
  const pathEdgeIds = React.useMemo(
    () => (path?.found ? new Set(path.edges.map((e) => e.id)) : undefined),
    [path],
  );

  const setPathEnd = React.useCallback((id: string, end: PathEnd) => {
    setPathEnds((prev) => ({ ...prev, [end]: id }));
  }, []);

  // ---- reload ----------------------------------------------------------------------------------
  const reloadM = useMutation({
    mutationFn: graphApi.reload,
    onSuccess: (res) => {
      if (isRefusal(res)) {
        setNotice({ tone: "warn", text: res.refusal.message });
        return;
      }
      setNotice({ tone: "info", text: `Store reloaded: ${res.counts.nodes} nodes, ${res.counts.edges} edges.` });
      void queryClient.invalidateQueries({ queryKey: ["graph-facets"] });
      void queryClient.invalidateQueries({ queryKey: ["graph-neighborhood"] });
    },
    onError: (err) =>
      setNotice({ tone: "warn", text: describeGraphError(err) }),
  });

  // ---- derived ---------------------------------------------------------------------------------
  const canvasNodes = React.useMemo(() => Array.from(canvas.nodes.values()), [canvas]);
  const canvasEdges = React.useMemo(
    () =>
      Array.from(canvas.edges.values()).filter(
        (e) => canvas.nodes.has(e.src_id) && canvas.nodes.has(e.dst_id),
      ),
    [canvas],
  );

  // The legend keys what is ON SCREEN — the accumulated canvas, not the whole store.
  const visibleKindFacets = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of canvasNodes) counts.set(n.kind, (counts.get(n.kind) ?? 0) + 1);
    return Array.from(counts.entries())
      .map(([kind, count]) => ({ kind, count }))
      .sort((a, b) => b.count - a.count || a.kind.localeCompare(b.kind));
  }, [canvasNodes]);

  const labelOf = (id: string | null) =>
    id ? (canvas.nodes.has(id) ? nodeLabelText(canvas.nodes.get(id)?.label) : id) : "—";

  // ---- trail -----------------------------------------------------------------------------------
  const trailLinks = React.useMemo(
    () => resolveLinks(trail.steps, canvas.edges),
    [trail.steps, canvas.edges],
  );
  const userTrailHighlight = React.useMemo(
    () => trailHighlight(trail.steps, trailLinks, trail.cursor),
    [trail.steps, trailLinks, trail.cursor],
  );
  const agentTrailHighlight = React.useMemo(
    () => (agentTraversal ? agentHighlight(agentTraversal, canvas.edges) : null),
    [agentTraversal, canvas.edges],
  );

  /** Select a node; if it is not drawn (the canvas was re-based since), merge it back in first. */
  const focusNode = React.useCallback(
    (id: string) => {
      setSelection({ type: "node", id });
      if (!canvasRef.current.nodes.has(id)) void expand(id, null);
    },
    [expand],
  );
  const focusCrumb = React.useCallback(
    (index: number) => {
      const step = trail.steps[index];
      if (!step) return;
      setTrail((prev) => ({ ...prev, cursor: index }));
      focusNode(step.nodeId);
    },
    [trail.steps, focusNode],
  );

  const storeEmpty = !!facets && facets.counts.nodes === 0;

  return (
    <KindPaletteProvider palette={palette}>
      <div className="flex flex-col gap-4 p-6">
        <header>
          <h1 className="text-lg font-semibold">Knowledge graph</h1>
          <p className="max-w-3xl text-xs text-subtle">
            The bi-temporal graph this system builds — every node and edge grounded in a pointer
            back to the source that produced it. It opens on the best-connected nodes; search for a
            starting point, double-click a node to expand it into the canvas, or mark two nodes to
            find the path between them. Click anything for its provenance.
          </p>
        </header>

        {facetsRefusal && <RefusalCard refusal={facetsRefusal.refusal} />}

        {storeEmpty ? (
          <div
            className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-surface px-6 py-16 text-center"
            data-testid="graph-empty-state"
          >
            <Plug className="h-6 w-6 text-subtle" />
            <div className="text-sm font-semibold text-fg">No data yet — connect a source</div>
            <p className="max-w-md text-xs text-subtle">
              The graph store{facets?.graph_source ? ` (${facets.graph_source})` : ""} holds no
              nodes. Connect a source and ingest it; what it grounds appears here with a pointer
              back to where each value came from.
            </p>
            <div className="flex items-center gap-2">
              <Link
                href="/connectors"
                className="rounded border border-accent px-3 py-1.5 text-xs text-accent hover:bg-accent/10"
              >
                Go to connectors
              </Link>
              <button
                type="button"
                onClick={() => reloadM.mutate()}
                disabled={reloadM.isPending}
                className="rounded border border-border px-3 py-1.5 text-xs text-subtle hover:text-fg disabled:opacity-50"
              >
                {reloadM.isPending ? "Reloading…" : "Reload store"}
              </button>
            </div>
            {notice && <p className="text-2xs text-subtle">{notice.text}</p>}
          </div>
        ) : (
          <>
            <FilterBar
              facets={facets}
              filters={filters}
              onChange={changeFilters}
              seed={seed}
              onClearSeed={() => {
                agentOwnsCanvas.current = false;
                setSeed(null);
              }}
              onReload={() => reloadM.mutate()}
              reloading={reloadM.isPending}
            />

            <div className="flex flex-wrap items-center gap-3">
              <SeedSearch onSeed={handleSeed} onAdd={(id) => void expand(id, "search-add")} />
              {(pathEnds.from || pathEnds.to) && (
                <div
                  className="flex min-w-0 flex-1 flex-wrap items-center gap-2 rounded border border-border bg-surface px-3 py-1.5 text-2xs"
                  data-testid="graph-path-bar"
                >
                  <Route className="h-3.5 w-3.5 text-subtle" />
                  <span className="text-subtle">path</span>
                  <span className="max-w-[14rem] truncate font-mono text-fg" title={pathEnds.from ?? ""}>
                    {labelOf(pathEnds.from)}
                  </span>
                  <span className="text-subtle">→</span>
                  <span className="max-w-[14rem] truncate font-mono text-fg" title={pathEnds.to ?? ""}>
                    {labelOf(pathEnds.to)}
                  </span>
                  <label className="flex items-center gap-1 text-subtle">
                    within
                    <select
                      value={pathDepth}
                      onChange={(e) => setPathDepth(Number(e.target.value))}
                      className="h-6 rounded border border-border bg-surface px-1 text-2xs text-fg"
                    >
                      {PATH_DEPTHS.map((d) => (
                        <option key={d} value={d}>
                          {d} hops
                        </option>
                      ))}
                    </select>
                  </label>
                  <span className="text-subtle" data-testid="graph-path-result">
                    {!pathEnds.from || !pathEnds.to
                      ? `select a node and choose “Path ${pathEnds.from ? "to" : "from"} here”`
                      : pathQ.isLoading
                        ? "searching…"
                        : pathQ.isError
                          ? describeGraphError(pathQ.error)
                          : pathRefusal
                            ? pathRefusal.refusal.message
                            : path?.found
                              ? `${path.hops} hop${path.hops === 1 ? "" : "s"} — highlighted`
                              : (path?.message ?? "")}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPathEnds({ from: null, to: null })}
                    className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-subtle hover:bg-muted hover:text-fg"
                  >
                    <X className="h-3 w-3" /> clear path
                  </button>
                </div>
              )}
            </div>

            {sourceParam && filters.source === sourceParam && !agentTraversal && (
              <div
                className="flex items-center gap-2 rounded border border-border bg-surface px-3 py-1.5 text-2xs text-subtle"
                data-testid="graph-source-filter"
              >
                <span>
                  Showing what source <code className="text-fg">{sourceParam}</code> grounds
                  {nb && nb.mode === "source"
                    ? ` — ${nb.returned} node(s)${nb.matched_total === null ? " (total unknown)" : ` of ${nb.matched_total}`}`
                    : ""}
                  . Clear the source filter to see the whole graph.
                </span>
              </div>
            )}

            {runParam && (
              <AgentTraversalPanel
                run={runParam}
                result={agentQ.data}
                loading={agentQ.isLoading}
                labelOf={(id) => labelOf(id)}
                onFocus={focusNode}
              />
            )}

            <TrailBar
              trail={trail}
              links={trailLinks}
              nodes={canvas.nodes}
              onFocus={focusCrumb}
              onBack={() => focusCrumb(trail.cursor - 1)}
              onForward={() => focusCrumb(trail.cursor + 1)}
              onClear={() => setTrail(EMPTY_TRAIL)}
            />

            {!agentTraversal && nb && (nb.truncated || nb.matched_total === null) && (
              <div className="flex items-start gap-2 rounded border border-band-spot/40 bg-band-spot/[0.06] px-3 py-2 text-2xs text-subtle">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
                <span>
                  <span className="font-medium text-fg">
                    Showing {nb.returned} of{" "}
                    {nb.matched_total === null ? "an unknown number —" : `${nb.matched_total}`}
                  </span>{" "}
                  {nb.mode === "hubs" && nb.omitted
                    ? `The best-connected nodes and their neighbours. ${nb.omitted.nodes_not_drawn} node(s) not drawn, of which ${nb.omitted.isolated_nodes} have no edges at all. Search or double-click to go further.`
                    : (nb.note ??
                      `${nb.left_out ?? 0} more match this view and are not drawn. Narrow the filter, or raise the window, to see more.`)}
                </span>
              </div>
            )}

            {notice && (
              <div
                className={
                  notice.tone === "warn"
                    ? "flex items-center gap-2 rounded border border-band-spot/40 bg-band-spot/[0.06] px-3 py-1.5 text-2xs text-subtle"
                    : "flex items-center gap-2 rounded border border-border bg-surface px-3 py-1.5 text-2xs text-subtle"
                }
                data-testid="graph-notice"
              >
                <span className="min-w-0 flex-1">{notice.text}</span>
                <button
                  type="button"
                  onClick={() => setNotice(null)}
                  className="rounded p-0.5 hover:bg-muted hover:text-fg"
                  aria-label="Dismiss"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
              <div className="relative">
                {neighborhoodQ.isLoading && (
                  <p className="text-xs text-subtle">Loading neighbourhood…</p>
                )}
                {neighborhoodQ.isError && (
                  <p className="text-xs text-band-review" role="alert">
                    {describeGraphError(neighborhoodQ.error)}
                  </p>
                )}
                {nbRefusal && <RefusalCard refusal={nbRefusal.refusal} />}
                {(nb || agentTraversal) && (
                  <>
                    <KindLegend kinds={visibleKindFacets} />
                    {expanding && (
                      <div className="pointer-events-none absolute right-3 top-3 z-10 rounded border border-graph-border bg-graph-surface/90 px-2 py-1 text-2xs text-graph-subtle">
                        expanding…
                      </div>
                    )}
                    <GraphCanvas
                      nodes={canvasNodes}
                      edges={canvasEdges}
                      selection={selection}
                      onSelect={setSelection}
                      onExpand={(id) => void expand(id)}
                      pathNodeIds={pathNodeIds}
                      pathEdgeIds={pathEdgeIds}
                      expandedIds={expanded}
                      trail={agentTrailHighlight ?? userTrailHighlight}
                      layoutEpoch={layoutEpoch}
                      emptyMessage={
                        agentTraversal
                          ? "This run's graph steps name no node that is in the store now."
                          : (nb?.note ?? undefined)
                      }
                    />
                  </>
                )}
              </div>

              <aside className="min-h-[24rem] rounded-lg border border-border bg-surface xl:min-h-0">
                <ProvenancePanel
                  selection={selection}
                  onClose={() => setSelection(null)}
                  onSeed={handleSeed}
                  onExpand={(id) => void expand(id)}
                  onFocus={focusNode}
                  onPathEnd={setPathEnd}
                  pathEnds={pathEnds}
                />
              </aside>
            </div>
          </>
        )}

        {/* Same slot `/brain` gives its ask panel: a resizable bottom row, the panel at half width.
            `components/orchestrator/ask-panel.tsx` is a different agent's file (imported, not
            edited). */}
        <ResizeHandle height={bottomHeight} onResize={setBottomHeight} />
        <div
          className="grid shrink-0 grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border"
          style={{ height: bottomHeight }}
        >
          <div className="overflow-hidden bg-bg p-4">
            <AskPanel />
          </div>
          <div className="overflow-hidden bg-bg" />
        </div>
      </div>
    </KindPaletteProvider>
  );
}
