"use client";
import * as React from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";
import { Search } from "lucide-react";
import type { BrainEdge, BrainNode } from "@/lib/brain-types";
import { ARROW_KINDS, NODE_STYLE, computeLayout, degreeMap, nodeRadius } from "@/lib/brain-layout";
import { cn } from "@/lib/utils";

// The hero knowledge graph — Obsidian style (16.5d Part 3). Near-black canvas; small glowing nodes
// sized by connection count; thin barely-visible edges that light amber on an active path; labels
// hidden until hover (permanent for hubs). Deterministic d3-force layout (settles, never jitters).
// Query/search dims the rest and glows the relevant nodes — backend truth, visually encoded (D-FE-2).

const HUB_DEGREE = 8;
const KIND_ORDER: Record<string, number> = { document: 0, person: 1, org: 2, decision: 3, project: 4, fact: 5 };

interface NodeData {
  label: string;
  color: string;
  radius: number;
  dim: boolean;
  emphasize: boolean;
  hub: boolean;
  staggerIdx: number;
}

function ObsidianNode({ data }: { data: NodeData }) {
  const size = data.radius * 2;
  const blur = data.emphasize ? 22 : data.color === NODE_STYLE.document.color ? 4 : 8;
  return (
    <div
      className="group relative flex items-center justify-center"
      style={{
        width: size,
        height: size,
        opacity: data.dim ? 0.16 : 1,
        transition: "opacity 250ms ease",
        animation: `node-in 240ms ease-out ${data.staggerIdx * 16}ms both`,
      }}
    >
      <Handle type="target" position={Position.Top} className="!h-0 !w-0 !min-w-0 !border-0 !bg-transparent" />
      <Handle type="source" position={Position.Bottom} className="!h-0 !w-0 !min-w-0 !border-0 !bg-transparent" />
      <div
        className="rounded-full transition-all duration-200 group-hover:brightness-150"
        style={{
          width: size,
          height: size,
          background: data.color,
          boxShadow: `0 0 ${blur}px ${data.color}`,
          border: data.emphasize ? "1.5px solid rgba(255,255,255,0.85)" : "none",
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

const nodeTypes = { brain: ObsidianNode };

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
    for (const n of graph.nodes) if (visibleAt(n.valid_from, n.valid_to)) s.add(n.id);
    return s;
  }, [graph.nodes, visibleAt]);

  const q = search.trim().toLowerCase();
  const byId = React.useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const colorOf = React.useCallback(
    (id: string) => {
      const n = byId.get(id);
      return n ? NODE_STYLE[n.kind].color : "#6b7280";
    },
    [byId],
  );

  const nodes: Node<NodeData>[] = React.useMemo(() => {
    // ingestion stagger order: documents first, then people/orgs/decisions/projects/facts (Part 4)
    const order = [...graph.nodes].sort(
      (a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.id.localeCompare(b.id),
    );
    const staggerOf = new Map(order.map((n, i) => [n.id, i]));
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
        ariaLabel: `${NODE_STYLE[n.kind].label}: ${n.label}`, // screen-reader name for the node
        data: {
          label: n.label,
          color: NODE_STYLE[n.kind].color,
          radius: nodeRadius(deg.get(n.id) ?? 0),
          dim: anyFocus && !emphasize,
          emphasize,
          hub: (deg.get(n.id) ?? 0) > HUB_DEGREE,
          staggerIdx: staggerOf.get(n.id) ?? 0,
        },
      };
    });
  }, [graph.nodes, layout, deg, highlightNodes, visibleNodeIds, q]);

  const edges: Edge[] = React.useMemo(
    () =>
      graph.edges.map((e) => {
        const visible =
          visibleAt(e.valid_from, e.valid_to) && visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target);
        const hl = highlightEdges.has(e.id);
        const arrow = ARROW_KINDS.has(e.kind);
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          hidden: !visible,
          animated: hl,
          markerEnd: arrow
            ? { type: MarkerType.ArrowClosed, width: 12, height: 12, color: hl ? "#f59e0b" : "rgba(255,255,255,0.2)" }
            : undefined,
          style: {
            stroke: hl ? "#f59e0b" : "rgba(255,255,255,0.12)",
            strokeWidth: hl ? 2 : e.weight > 1 ? 2 : 1,
            opacity: highlightEdges.size > 0 && !hl ? 0.06 : 1,
            transition: "stroke 200ms, opacity 200ms",
          },
        };
      }),
    [graph.edges, highlightEdges, visibleAt, visibleNodeIds],
  );

  React.useEffect(() => {
    if (!focusNode) return;
    const p = layout.get(focusNode);
    if (p) rf.setCenter(p.x, p.y, { zoom: 1.4, duration: 600 });
  }, [focusNode, layout, rf]);

  return (
    <div className="relative h-full w-full" style={{ background: "#0d0d0d" }}>
      {/* node search (Obsidian-style) — type to glow matching nodes, dim the rest */}
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
