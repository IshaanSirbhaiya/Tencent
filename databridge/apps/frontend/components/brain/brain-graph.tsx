"use client";
import * as React from "react";
import ReactFlow, {
  BaseEdge,
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";
import { Search } from "lucide-react";
import type { BrainEdge, BrainNode } from "@/lib/brain-types";
import {
  NODE_STYLE,
  computeLayout,
  degreeMap,
  edgeLabel,
  hasArrow,
  nodeRadius,
  nodeStyle,
} from "@/lib/brain-layout";
import { cn } from "@/lib/utils";

/**
 * The hero knowledge graph. Near-black canvas, small nodes sized by connection count, thin edges
 * that light amber on an active path, labels on hover (permanent for hubs). The d3-force layout is
 * deterministic — same (nodes, edges) → identical picture, never jitter.
 *
 * ## Three bugs this file used to have
 *
 * 1. **Edges did not connect what they said they connected.** The source handle was pinned to
 *    `Position.Bottom` and the target handle to `Position.Top`, so every edge left the bottom of one
 *    node and entered the top of another — as a bezier. In a force layout the target is as often
 *    *above* the source as below, so the curve looped backwards around both nodes. With 114 edges
 *    the result was a hairball in which the lines visibly did not correspond to the relationships.
 *    Fixed by `FloatingEdge`: a straight line between node CENTRES, trimmed at each circle's
 *    radius, which is what a force-directed graph should draw.
 *
 * 2. **No edge had an arrowhead.** `ARROW_KINDS` was an allow-list holding `{"decided", "blocks"}`
 *    — kinds from the retired Acme vocabulary. ADR-0009 renamed every kind, the set matched nothing,
 *    and the whole graph rendered undirected. Direction is now a deny-list in `brain-layout.ts`
 *    (see the note there on why that shape is the safe one).
 *
 * 3. **The staged build reveal (T-31, removed).** A ~1.9s animation staged nodes in by kind
 *    (documents, then who/what they resolved to) via a `revealed` counter layered on top of
 *    `visibleNodeIds`. It shared state with the bi-temporal filter below — two different things
 *    could make a node appear, and only one of them (`visibleAt`) was real. That made the §1.1
 *    bi-temporal data bug (edges predating their own endpoints) invisible in the UI while it was
 *    live in the store. Removed; `visibleAt` and the both-endpoints-visible edge rule are the only
 *    thing deciding what is on screen now.
 */

const HUB_DEGREE = 8;

const HIGHLIGHT = "#f59e0b";
const EDGE_REST = "rgba(255,255,255,0.14)";

interface NodeData {
  label: string;
  kindLabel: string;
  color: string;
  radius: number;
  dim: boolean;
  emphasize: boolean;
  hub: boolean;
}

function ObsidianNode({ data }: { data: NodeData }) {
  const size = data.radius * 2;
  // Glow, not neon: a soft halo at ~40% of the radius reads as depth. The old values (8px flat,
  // 22px when emphasised) plus full-saturation fills made every node a light source, and with 70 of
  // them the halos merged into a wash that hid the edges entirely.
  const blur = data.emphasize ? Math.max(10, data.radius * 0.9) : Math.max(3, data.radius * 0.35);
  return (
    <div
      className="group relative flex items-center justify-center"
      style={{
        width: size,
        height: size,
        opacity: data.dim ? 0.14 : 1,
        transition: "opacity 250ms ease",
      }}
    >
      {/* Both handles sit at the node's CENTRE and are 1px. `FloatingEdge` computes its own
          geometry, but ReactFlow still needs handles to accept an edge at all — and a handle
          anywhere off-centre would make the fallback rendering path wrong in the same way the
          Top/Bottom pinning was. */}
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={false}
        style={{ left: "50%", top: "50%", width: 1, height: 1, minWidth: 1, minHeight: 1, border: 0, background: "transparent", transform: "translate(-50%,-50%)" }}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={false}
        style={{ left: "50%", top: "50%", width: 1, height: 1, minWidth: 1, minHeight: 1, border: 0, background: "transparent", transform: "translate(-50%,-50%)" }}
      />
      <div
        className="rounded-full transition-all duration-200 group-hover:brightness-125"
        style={{
          width: size,
          height: size,
          background: data.color,
          boxShadow: `0 0 ${blur}px ${data.color}66`,
          border: data.emphasize ? "1.5px solid rgba(255,255,255,0.9)" : "1px solid rgba(0,0,0,0.35)",
        }}
      />
      <span
        className={cn(
          "pointer-events-none absolute left-1/2 top-full mt-1 -translate-x-1/2 whitespace-nowrap font-mono text-[9px] leading-none text-white/90 transition-opacity duration-150",
          data.hub || data.emphasize ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        )}
        style={{ textShadow: "0 1px 3px #000" }}
      >
        {data.label}
      </span>
    </div>
  );
}

/**
 * A straight edge between two node centres, trimmed to each circle's radius.
 *
 * This is the fix for edges appearing wired to the wrong nodes. Trimming matters for more than
 * neatness: an arrowhead drawn at the target's centre is hidden underneath the target's own circle,
 * so direction stayed invisible even once the arrow kinds were corrected.
 */
function FloatingEdge({ id, source, target, markerEnd, style, data }: EdgeProps) {
  // `nodeInternals` carries measured width/height and absolute position — the only place the real
  // rendered size of a node is known, which is what the radius trim needs.
  const nodes = useStore((s) => s.nodeInternals);
  const s = nodes.get(source);
  const t = nodes.get(target);
  if (!s || !t) return null;

  const sr = (s.width ?? (s.data as NodeData | undefined)?.radius ?? 8) / 2 || 8;
  const tr = (t.width ?? (t.data as NodeData | undefined)?.radius ?? 8) / 2 || 8;
  const sx = (s.positionAbsolute?.x ?? s.position.x) + (s.width ?? sr * 2) / 2;
  const sy = (s.positionAbsolute?.y ?? s.position.y) + (s.height ?? sr * 2) / 2;
  const tx = (t.positionAbsolute?.x ?? t.position.x) + (t.width ?? tr * 2) / 2;
  const ty = (t.positionAbsolute?.y ?? t.position.y) + (t.height ?? tr * 2) / 2;

  const dx = tx - sx;
  const dy = ty - sy;
  const len = Math.hypot(dx, dy);
  // Two nodes at the same point would divide by zero and emit `M NaN,NaN`, which silently drops the
  // whole SVG path.
  if (len < 1) return null;
  const ux = dx / len;
  const uy = dy / len;
  // Stop 3px short of the target so the arrowhead sits just outside the circle rather than on it.
  const x1 = sx + ux * sr;
  const y1 = sy + uy * sr;
  const x2 = tx - ux * (tr + 3);
  const y2 = ty - uy * (tr + 3);
  // A trim longer than the gap between the two circles means they overlap; skip rather than draw a
  // backwards line with an arrowhead pointing the wrong way.
  if (sr + tr + 3 >= len) return null;

  const label = (data as { label?: string; showLabel?: boolean } | undefined);
  return (
    <>
      <BaseEdge id={id} path={`M ${x1},${y1} L ${x2},${y2}`} markerEnd={markerEnd} style={style} />
      {label?.showLabel && label.label ? (
        <text
          x={(x1 + x2) / 2}
          y={(y1 + y2) / 2 - 3}
          textAnchor="middle"
          className="pointer-events-none select-none"
          style={{ fill: HIGHLIGHT, fontSize: 8, fontFamily: "var(--font-mono, monospace)", paintOrder: "stroke", stroke: "#0d0d0d", strokeWidth: 3 }}
        >
          {label.label}
        </text>
      ) : null}
    </>
  );
}

const nodeTypes = { brain: ObsidianNode };
const edgeTypes = { floating: FloatingEdge };

function GraphInner({
  graph,
  highlightNodes,
  highlightEdges,
  asOf,
  onNodeClick,
  focusNode,
}: {
  graph: { nodes: BrainNode[]; edges: BrainEdge[] };
  highlightNodes: Set<string>;
  highlightEdges: Set<string>;
  asOf: Date | null;
  onNodeClick: (id: string) => void;
  focusNode: string | null;
}) {
  const rf = useReactFlow();
  const [search, setSearch] = React.useState("");
  const deg = React.useMemo(() => degreeMap(graph.edges), [graph.edges]);
  const layout = React.useMemo(() => computeLayout(graph.nodes, graph.edges), [graph.nodes, graph.edges]);

  // T-32: the canvas sits above a resizable ask panel now. ReactFlow measures its container but does
  // not re-pan/re-zoom on its own when that container shrinks — the viewport (pan + zoom) is state,
  // not a function of size — so dragging the panel taller would push nodes outside the visible area
  // instead of scaling the picture back to fit. Re-fitting on every resize is what keeps "the graph
  // re-fits rather than being clipped" (PHASE_7 §T-32) true regardless of who resized what.
  const containerRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => rf.fitView({ duration: 200, padding: 0.15 }));
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [rf]);

  // --- bi-temporal visibility -------------------------------------------------------------
  const visibleAt = React.useCallback(
    (validFrom: string | null, validTo: string | null) => {
      if (!asOf || !validFrom) return true;
      const from = new Date(validFrom).getTime();
      const to = validTo ? new Date(validTo).getTime() : Infinity;
      const t = asOf.getTime();
      return from <= t && t < to;
    },
    [asOf],
  );

  const visibleNodeIds = React.useMemo(() => {
    const s = new Set<string>();
    for (const n of graph.nodes) {
      if (!visibleAt(n.valid_from, n.valid_to)) continue;
      s.add(n.id);
    }
    return s;
  }, [graph.nodes, visibleAt]);

  const q = search.trim().toLowerCase();
  const byId = React.useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const colorOf = React.useCallback(
    (id: string) => {
      const n = byId.get(id);
      return n ? nodeStyle(n.kind).color : "#7d8492";
    },
    [byId],
  );

  const nodes: Node<NodeData>[] = React.useMemo(() => {
    const anyFocus = highlightNodes.size > 0 || q.length > 0;
    return graph.nodes.map((n) => {
      const p = layout.get(n.id) ?? { x: 0, y: 0 };
      const matchSearch = q.length > 0 && n.label.toLowerCase().includes(q);
      const emphasize = highlightNodes.has(n.id) || matchSearch;
      return {
        id: n.id,
        type: "brain",
        position: p,
        hidden: !visibleNodeIds.has(n.id),
        ariaLabel: `${nodeStyle(n.kind).label}: ${n.label}`,
        data: {
          label: n.label,
          kindLabel: nodeStyle(n.kind).label,
          color: nodeStyle(n.kind).color,
          radius: nodeRadius(deg.get(n.id) ?? 0),
          dim: anyFocus && !emphasize,
          emphasize,
          hub: (deg.get(n.id) ?? 0) > HUB_DEGREE,
        },
      };
    });
  }, [graph.nodes, layout, deg, highlightNodes, visibleNodeIds, q]);

  const edges: Edge[] = React.useMemo(
    () =>
      graph.edges.map((e) => {
        // An edge is only drawn once BOTH endpoints are on screen — during the build and when
        // rewound in time alike. Otherwise a line hangs off a node that is not there yet.
        const visible =
          visibleAt(e.valid_from, e.valid_to)
          && visibleNodeIds.has(e.source)
          && visibleNodeIds.has(e.target);
        const hl = highlightEdges.has(e.id);
        const dimmed = highlightEdges.size > 0 && !hl;
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: "floating",
          hidden: !visible,
          animated: hl,
          // Label only on the highlighted path. Captioning all 114 at once is illegible.
          data: { label: edgeLabel(e.kind), showLabel: hl },
          markerEnd: hasArrow(e.kind)
            ? {
                type: MarkerType.ArrowClosed,
                width: hl ? 14 : 10,
                height: hl ? 14 : 10,
                color: hl ? HIGHLIGHT : EDGE_REST,
              }
            : undefined,
          style: {
            stroke: hl ? HIGHLIGHT : EDGE_REST,
            strokeWidth: hl ? 2 : e.weight > 1 ? 1.5 : 1,
            opacity: dimmed ? 0.07 : 1,
            transition: "stroke 200ms, opacity 200ms",
          },
        };
      }),
    [graph.edges, highlightEdges, visibleAt, visibleNodeIds],
  );

  // ONE camera effect, deliberately.
  //
  // This was two: a highlight effect calling `fitView` and a focus effect calling `setCenter`. A
  // one-node answer sets BOTH `highlightNodes` and `focusNode`, so both fired in the same commit
  // with the same 600ms duration and fought each other — visible as a jitter or a nondeterministic
  // final viewport. Two effects driving one camera is a race by construction; there is now a single
  // owner that picks the mode.
  const highlightKey = React.useMemo(
    () => Array.from(highlightNodes).sort().join("|"),
    [highlightNodes],
  );
  React.useEffect(() => {
    const highlighted = Array.from(highlightNodes).filter((id) => visibleNodeIds.has(id));

    // Two or more matches: frame them all. Centring on the first was not enough of a signal that a
    // question had done anything — if the match was already on screen, nothing appeared to happen.
    if (highlighted.length > 1) {
      rf.fitView({
        nodes: highlighted.map((id) => ({ id })), duration: 600, padding: 0.45, maxZoom: 1.6,
      });
      return;
    }

    // Exactly one target — from a single-entity answer or a node click. Centre on it.
    const only = highlighted.length === 1 ? highlighted[0] : focusNode;
    if (!only || !visibleNodeIds.has(only)) return;
    const p = layout.get(only);
    if (p) rf.setCenter(p.x, p.y, { zoom: 1.4, duration: 600 });
    // `visibleNodeIds` is intentionally excluded: it changes on every slider tick, and re-framing
    // the camera while someone scrubs through time would fight their input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightKey, focusNode, layout, rf]);

  return (
    <div ref={containerRef} className="relative h-full w-full" style={{ background: "#0d0d0d" }}>
      <div className="absolute left-1/2 top-3 z-20 -translate-x-1/2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/40" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search nodes…"
            aria-label="Search graph nodes"
            className="h-8 w-56 rounded-full border border-white/10 bg-black/60 pl-8 pr-3 text-xs text-white/90 outline-none placeholder:text-white/30 focus:border-white/30"
          />
        </div>
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        minZoom={0.2}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_e, n) => onNodeClick(n.id)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background color="#1a1a1a" gap={22} size={1} />
        <Controls showInteractive={false} className="!border-white/10 !bg-black/70 [&_button]:!border-white/10 [&_button]:!bg-black/60 [&_button]:!fill-white/70" />
        <MiniMap
          pannable
          zoomable
          className="!bottom-3 !right-3 !border !border-white/10 !bg-black/70"
          style={{ width: 160, height: 100 }}
          maskColor="rgba(0,0,0,0.6)"
          nodeColor={(n) => colorOf(n.id)}
          nodeStrokeWidth={0}
        />
      </ReactFlow>
    </div>
  );
}

export function BrainGraph(props: React.ComponentProps<typeof GraphInner>) {
  return (
    <ReactFlowProvider>
      <GraphInner {...props} />
    </ReactFlowProvider>
  );
}
