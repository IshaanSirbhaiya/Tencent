"use client";
/**
 * The graph itself — ported from `components/brain/brain-graph.tsx` verbatim, not redesigned. The
 * founder's ask was specific: `/graph` should look and feel exactly like the earlier `/brain`
 * rebuild ("Obsidian-type... each node based on kind had a colour"), just fed by the real adapter
 * fleet's bounded queries instead of a small in-memory demo graph. So this file keeps `/brain`'s
 * actual rendering code — `ObsidianNode`'s glow-halo circles, `FloatingEdge`'s rim-trimmed
 * straight lines, the same ReactFlow `Controls`/`MiniMap`/`Background`, the same floating search
 * box, the same ONE camera effect, the same ATTENTION amber, the same `nodesDraggable={false}` (a
 * force layout you can drag out of place is not "alive", it's broken) — and changes only what has
 * to change to sit on top of a 46,346-node estate instead of a 358-node demo:
 *
 *  - Data comes in as `GraphNodeSummary`/`GraphEdgeSummary` (`lib/graph-api.ts`), the shape the
 *    bounded `/graph/neighborhood` endpoint returns, not `BrainNode`/`BrainEdge`.
 *  - Colour resolution adds a generated fallback (`kind-colors.ts`) for kinds no pack has declared
 *    — the government estate has dozens, `packs.json` only hand-tunes the high-volume ones — in
 *    the SAME muted band as the declared hexes, so a viewer cannot tell which is which.
 *  - Two honesty markers that predate this restyle survive unchanged: a dashed ring on a node
 *    carrying an unresolved field, and a dashed/dimmed treatment for a superseded (`!is_open`)
 *    node or edge. Neither existed in `/brain` (a small demo graph has no bi-temporal history to
 *    show), so neither changes how a normal node looks — they only ever ADD to one.
 *
 * Traversal (this file's share of it): double-click a node to EXPAND it — `graph-view.tsx` merges
 * the fetched neighbourhood into the canvas — and the layout PINS every node already on screen
 * (`layout.ts`), so an expansion adds nodes around the reader's place instead of reshuffling the
 * picture. A found path (`pathNodeIds` / `pathEdgeIds`) is drawn in the attention amber with
 * everything else dimmed. Colour and captions come from the pack-data palette
 * (`kind-palette.tsx`); this file names no kind.
 */
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
import type { GraphEdgeSummary, GraphNodeSummary } from "@/lib/graph-api";
import { nodeLabelText } from "@/lib/graph-api";
import { hasArrow } from "@/lib/brain-layout";
import { computeGraphLayout, degreeMap, nodeRadius, type Pos } from "./layout";
import { useKindPalette } from "./kind-palette";
import type { TrailHighlight } from "./trail";
import { cn } from "@/lib/utils";

export type GraphSelection = { type: "node"; id: string } | { type: "edge"; id: string } | null;

const HUB_DEGREE = 8;

const HIGHLIGHT = "#f59e0b";
/** The traversal trail — the single accent, deliberately NOT the path amber, so a found path and
 *  the reader's own trail can be told apart when both are on screen. */
const TRAIL = "hsl(var(--accent))";
const EDGE_REST = "rgba(255,255,255,0.14)";

interface NodeData {
  /** null = withheld by the gateway (no pointer); rendered via `nodeLabelText`. */
  label: string | null;
  kindLabel: string;
  color: string;
  radius: number;
  dim: boolean;
  emphasize: boolean;
  hub: boolean;
  /** Predates this restyle — a node asked for a field and never found it (`unresolved`, per-node
   *  count from `GraphNodeSummary.unresolved_count`). Additive: a thin dashed amber ring outside
   *  the node's own circle, never a change to the circle itself. */
  unresolvedCount: number;
  /** Predates this restyle — `!is_open` means superseded (bi-temporal). Additive: dashed rim,
   *  reduced opacity. `/brain`'s small demo graph never had a closed node to render. */
  isOpen: boolean;
  /** On the currently highlighted path. */
  onPath: boolean;
  /** Already expanded by the reader. */
  expanded: boolean;
  /** On the current traversal trail (the reader's, or an agent run's). */
  onTrail: boolean;
  /** The trail step number(s) that visited this node, in order. */
  trailNumbers: number[];
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
        opacity: data.dim ? 0.14 : data.isOpen ? 1 : 0.45,
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
      {data.unresolvedCount > 0 && (
        <div
          className="absolute rounded-full"
          style={{
            width: size + 9,
            height: size + 9,
            border: `1px dashed ${HIGHLIGHT}cc`,
          }}
        />
      )}
      {data.onTrail && !data.onPath && (
        <div
          className="absolute rounded-full"
          style={{ width: size + 8, height: size + 8, border: `2px solid ${TRAIL}` }}
        />
      )}
      {data.trailNumbers.length > 0 && (
        <span
          className="pointer-events-none absolute -right-2 -top-2 z-10 rounded-full px-1 font-mono text-[8px] leading-[12px] text-white"
          style={{ background: TRAIL, minWidth: 12, textAlign: "center" }}
          data-testid="graph-trail-badge"
        >
          {data.trailNumbers.length > 3
            ? `${data.trailNumbers.slice(0, 3).join(",")}…`
            : data.trailNumbers.join(",")}
        </span>
      )}
      {data.onPath && (
        <div
          className="absolute rounded-full"
          style={{ width: size + 8, height: size + 8, border: `2px solid ${HIGHLIGHT}` }}
        />
      )}
      <div
        className="rounded-full transition-all duration-200 group-hover:brightness-125"
        style={{
          width: size,
          height: size,
          background: data.color,
          boxShadow: `0 0 ${blur}px ${data.color}66`,
          border: data.emphasize
            ? "1.5px solid hsl(var(--fg))"
            : `1px ${data.isOpen ? "solid" : "dashed"} hsl(var(--border))`,
          // an expanded node carries a thin inner ring — "you have been here"
          outline: data.expanded ? "1px solid hsl(var(--bg) / 0.8)" : undefined,
          outlineOffset: data.expanded ? -4 : undefined,
        }}
      />
      <span
        className={cn(
          "pointer-events-none absolute left-1/2 top-full mt-1 -translate-x-1/2 whitespace-nowrap font-mono text-[9px] leading-none text-fg transition-opacity duration-150",
          data.hub || data.emphasize ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        )}
        // A halo in the CANVAS colour rather than black: on a light ground a black halo is a smudge.
        style={{ textShadow: "0 1px 3px hsl(var(--bg))" }}
      >
        {nodeLabelText(data.label)}
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
          style={{ fill: HIGHLIGHT, fontSize: 8, fontFamily: "var(--font-mono, monospace)", paintOrder: "stroke", stroke: "hsl(var(--bg))", strokeWidth: 3 }}
        >
          {label.label}
        </text>
      ) : null}
    </>
  );
}

const nodeTypes = { brain: ObsidianNode };
const edgeTypes = { floating: FloatingEdge };

interface CanvasProps {
  nodes: GraphNodeSummary[];
  edges: GraphEdgeSummary[];
  selection: GraphSelection;
  onSelect: (s: GraphSelection) => void;
  /** Double-click (or the panel's button): merge this node's neighbourhood into the canvas. */
  onExpand?: (id: string) => void;
  /** A found path — drawn in amber, everything else dimmed. */
  pathNodeIds?: Set<string>;
  pathEdgeIds?: Set<string>;
  /** Nodes already expanded — drawn with a thin inner ring so the reader sees where they have been. */
  expandedIds?: Set<string>;
  /** The current traversal trail — ringed and numbered in the accent, distinct from the path amber. */
  trail?: TrailHighlight;
  /** Bumped whenever the canvas is REPLACED (new filter, new seed) rather than merged into; the
   *  layout drops its pins and starts fresh. */
  layoutEpoch: number;
}

function GraphInner({
  nodes: rawNodes,
  edges: rawEdges,
  selection,
  onSelect,
  onExpand,
  pathNodeIds,
  pathEdgeIds,
  expandedIds,
  trail,
  layoutEpoch,
}: CanvasProps) {
  const rf = useReactFlow();
  const palette = useKindPalette();
  const [search, setSearch] = React.useState("");

  const edgeEnds = React.useMemo(
    () => rawEdges.map((e) => ({ source: e.src_id, target: e.dst_id })),
    [rawEdges],
  );
  const deg = React.useMemo(() => degreeMap(edgeEnds), [edgeEnds]);
  const nodeIds = React.useMemo(() => rawNodes.map((n) => n.id), [rawNodes]);
  // Positions from the previous render of THIS epoch are pinned, so a merge only places the
  // newcomers. A new epoch (the canvas was replaced) starts from nothing.
  const pinsRef = React.useRef<{ epoch: number; pos: Map<string, Pos> }>({
    epoch: layoutEpoch,
    pos: new Map(),
  });
  const layout = React.useMemo(() => {
    const pins =
      pinsRef.current.epoch === layoutEpoch ? pinsRef.current.pos : new Map<string, Pos>();
    const next = computeGraphLayout(nodeIds, edgeEnds, pins);
    pinsRef.current = { epoch: layoutEpoch, pos: next };
    return next;
  }, [nodeIds, edgeEnds, layoutEpoch]);

  // Re-fit whenever the container resizes — `/graph` has no resizable bottom panel today, but a
  // window resize (or a future one) should still re-frame rather than clip.
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

  const q = search.trim().toLowerCase();
  const byId = React.useMemo(() => new Map(rawNodes.map((n) => [n.id, n])), [rawNodes]);
  const colorOf = React.useCallback(
    (id: string) => {
      const n = byId.get(id);
      return n ? palette.node(n.kind).color : "hsl(var(--subtle))";
    },
    [byId, palette],
  );
  const pathActive = !!pathNodeIds && pathNodeIds.size > 0;

  const nodes: Node<NodeData>[] = React.useMemo(() => {
    const anyFocus = pathActive || selection?.type === "node" || q.length > 0;
    return rawNodes.map((n) => {
      const p = layout.get(n.id) ?? { x: 0, y: 0 };
      // a withheld label has no text to match — searching never reveals or invents one
      const matchSearch = q.length > 0 && (n.label ?? "").toLowerCase().includes(q);
      const isSelected = selection?.type === "node" && selection.id === n.id;
      const onPath = pathActive && (pathNodeIds?.has(n.id) ?? false);
      const emphasize = isSelected || matchSearch || onPath;
      const style = palette.node(n.kind);
      return {
        id: n.id,
        type: "brain",
        position: p,
        ariaLabel: `${style.label}: ${nodeLabelText(n.label)}`,
        data: {
          label: n.label,
          kindLabel: style.label,
          color: style.color,
          radius: nodeRadius(deg.get(n.id) ?? 0),
          dim: anyFocus && !emphasize,
          emphasize,
          hub: (deg.get(n.id) ?? 0) > HUB_DEGREE,
          unresolvedCount: n.unresolved_count,
          isOpen: n.is_open,
          onPath,
          expanded: expandedIds?.has(n.id) ?? false,
          onTrail: trail?.nodeIds.has(n.id) ?? false,
          trailNumbers: trail?.numbers.get(n.id) ?? [],
        },
      };
    });
  }, [rawNodes, layout, deg, selection, q, palette, pathActive, pathNodeIds, expandedIds, trail]);

  const edges: Edge[] = React.useMemo(
    () =>
      rawEdges.map((e) => {
        const onPath = pathActive && (pathEdgeIds?.has(e.id) ?? false);
        const hl = (selection?.type === "edge" && selection.id === e.id) || onPath;
        const onTrail = !hl && (trail?.edgeIds.has(e.id) ?? false);
        const dimmed = pathActive && !onPath && !onTrail;
        const stroke = hl ? HIGHLIGHT : onTrail ? TRAIL : EDGE_REST;
        return {
          id: e.id,
          source: e.src_id,
          target: e.dst_id,
          type: "floating",
          animated: hl,
          // Label only on the selected edge. Captioning every edge at once is illegible.
          data: { label: palette.edgeLabel(e.kind), showLabel: hl },
          className: onTrail ? "graph-trail-edge" : undefined,
          markerEnd: hasArrow(e.kind)
            ? {
                type: MarkerType.ArrowClosed,
                width: hl || onTrail ? 14 : 10,
                height: hl || onTrail ? 14 : 10,
                color: stroke,
              }
            : undefined,
          style: {
            stroke,
            strokeWidth: hl || onTrail ? 2 : e.weight > 1 ? 1.5 : 1,
            opacity: dimmed ? 0.15 : e.is_open ? 1 : 0.35,
            strokeDasharray: e.is_open ? undefined : "3 3",
            transition: "stroke 200ms, opacity 200ms",
          },
        };
      }),
    [rawEdges, selection, palette, pathActive, pathEdgeIds, trail],
  );

  // ONE camera effect, deliberately (the lesson `brain-graph.tsx` names: two effects driving one
  // camera is a race by construction). A search match frames every match; a click centres on the
  // one node clicked.
  const searchMatches = React.useMemo(() => {
    if (q.length === 0) return [];
    return rawNodes.filter((n) => (n.label ?? "").toLowerCase().includes(q)).map((n) => n.id);
  }, [rawNodes, q]);
  const matchKey = searchMatches.join("|");
  React.useEffect(() => {
    if (searchMatches.length > 1) {
      rf.fitView({ nodes: searchMatches.map((id) => ({ id })), duration: 600, padding: 0.45, maxZoom: 1.6 });
      return;
    }
    const only = searchMatches.length === 1 ? searchMatches[0] : selection?.type === "node" ? selection.id : null;
    if (!only) return;
    const p = layout.get(only);
    if (p) rf.setCenter(p.x, p.y, { zoom: 1.4, duration: 600 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchKey, selection, layout, rf]);

  // The node set changed (a replace OR a merge) — re-fit so everything is framed. A found path
  // takes precedence: frame the path itself.
  const nodeSetKey = nodeIds.join("|");
  const pathKey = pathNodeIds ? Array.from(pathNodeIds).join("\u0000") : "";
  React.useEffect(() => {
    if (pathKey) {
      const ids = pathKey.split("\u0000").map((id) => ({ id }));
      // wait one frame so newly merged path nodes are measured before framing them
      const raf = requestAnimationFrame(() =>
        rf.fitView({ nodes: ids, duration: 500, padding: 0.35, maxZoom: 1.6 }),
      );
      return () => cancelAnimationFrame(raf);
    }
    if (nodeIds.length > 0) rf.fitView({ duration: 300, padding: 0.15 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeSetKey, pathKey, rf]);

  return (
    <div ref={containerRef} className="relative h-full w-full bg-graph-bg">
      <div className="absolute left-1/2 top-3 z-20 -translate-x-1/2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-graph-subtle" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Find on canvas…"
            aria-label="Find a node on the canvas"
            className="h-8 w-56 rounded-full border border-graph-border bg-graph-surface pl-8 pr-3 text-xs text-graph-fg outline-none placeholder:text-graph-subtle focus:border-graph-fg/40"
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
        onNodeClick={(_e, n) => onSelect({ type: "node", id: n.id })}
        onNodeDoubleClick={(_e, n) => onExpand?.(n.id)}
        zoomOnDoubleClick={false}
        onEdgeClick={(_e, e) => onSelect({ type: "edge", id: e.id })}
        onPaneClick={() => onSelect(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
      >
        <Background color="hsl(var(--graph-border))" gap={22} size={1} />
        <Controls
          showInteractive={false}
          className="!border-graph-border !bg-graph-surface [&_button]:!border-graph-border [&_button]:!bg-graph-surface [&_button]:!fill-graph-fg"
        />
        <MiniMap
          pannable
          zoomable
          className="!bottom-3 !right-3 !border !border-graph-border !bg-graph-surface"
          style={{ width: 160, height: 100 }}
          maskColor="hsl(var(--graph-bg) / 0.6)"
          nodeColor={(n) => colorOf(n.id)}
          nodeStrokeWidth={0}
        />
      </ReactFlow>
    </div>
  );
}

export function GraphCanvas(props: CanvasProps & { emptyMessage?: React.ReactNode }) {
  const { emptyMessage, ...rest } = props;
  if (rest.nodes.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-graph-border bg-graph-bg px-6 text-center text-xs text-graph-subtle">
        {emptyMessage ??
          "Nothing matches this filter yet — try a different kind, source or search."}
      </div>
    );
  }
  return (
    <div className="h-[42rem] w-full overflow-hidden rounded-lg border border-graph-border" data-testid="graph-canvas">
      <ReactFlowProvider>
        <GraphInner {...rest} />
      </ReactFlowProvider>
    </div>
  );
}
