"use client";
/**
 * The ontology as a picture: every entity kind a box, every relation an arrow from each allowed
 * source kind to each allowed target kind. It is the same data as the cards below, drawn so the
 * shape of what the graph CAN say is visible at a glance.
 *
 * Built on React Flow, like `components/schema-graph.tsx` (the table-schema view), with the same
 * rule: a deterministic layout (kinds on a circle, alphabetical), no physics — the same ontology
 * renders the same picture twice. Relations allowing "any kind" (`*`) are listed under the canvas
 * rather than drawn to every box, which would bury everything else.
 */
import * as React from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";
import type { EdgeKindView, NodeKindView } from "@/lib/ontology-api";

function KindNode({ data }: { data: { kind: string; meta: string; subject: boolean; count: number | null } }) {
  return (
    <div
      className={`min-w-[120px] rounded border px-2.5 py-1.5 shadow-sm ${
        data.subject ? "border-band-review/60 bg-band-review/[0.06]" : "border-border bg-elevated"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-border" />
      <div className="font-mono text-xs font-semibold text-fg">{data.kind}</div>
      <div className="flex items-center gap-2 text-2xs text-subtle">
        <span>{data.meta}</span>
        {data.subject ? <span className="text-band-review">subject</span> : null}
        {data.count !== null ? <span>{data.count} in graph</span> : null}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-border" />
    </div>
  );
}

const nodeTypes = { kind: KindNode };

export function SchemaView({ nodes, edges }: { nodes: NodeKindView[]; edges: EdgeKindView[] }) {
  const live = nodes.filter((n) => !n.deprecated);
  const liveEdges = edges.filter((e) => !e.deprecated);
  const wildcard = liveEdges.filter((e) => e.from.includes("*") || e.to.includes("*"));

  const graph = React.useMemo(() => {
    const n = live.length;
    const radius = Math.max(160, n * 34);
    const rfNodes: Node[] = live.map((k, i) => ({
      id: k.kind,
      type: "kind",
      position: {
        x: radius + radius * Math.cos((2 * Math.PI * i) / Math.max(n, 1)),
        y: radius + radius * Math.sin((2 * Math.PI * i) / Math.max(n, 1)),
      },
      data: { kind: k.kind, meta: k.binds_to, subject: k.subject, count: k.count },
    }));
    const ids = new Set(live.map((k) => k.kind));
    const rfEdges: Edge[] = [];
    for (const e of liveEdges) {
      for (const s of e.from) {
        for (const t of e.to) {
          if (!ids.has(s) || !ids.has(t)) continue;
          rfEdges.push({
            id: `${e.kind}:${s}->${t}`,
            source: s,
            target: t,
            label: e.kind,
            markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
            style: {
              stroke: e.carries_identity_rule ? "hsl(var(--band-review))" : "hsl(var(--subtle))",
              strokeDasharray: e.count ? undefined : "4 3",
            },
            labelStyle: { fontSize: 9, fill: "hsl(var(--subtle))" },
          });
        }
      }
    }
    return { rfNodes, rfEdges };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

  if (!live.length) {
    return (
      <p className="rounded border border-dashed border-border px-3 py-6 text-center text-xs text-subtle">
        No entity kinds are defined yet, so there is nothing to draw. Define the first entity kind
        below.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="h-[420px] w-full rounded border border-border bg-surface" data-testid="ontology-schema-view">
        <ReactFlow
          nodes={graph.rfNodes}
          edges={graph.rfEdges}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          nodesDraggable
          nodesConnectable={false}
        >
          <Background color="hsl(var(--border))" gap={18} />
          <Controls showInteractive={false} className="!bg-elevated !border-border" />
        </ReactFlow>
      </div>
      <p className="text-2xs text-subtle">
        Solid arrows: the relation has data in the graph. Dashed: defined, no data yet. Red:
        identity-bearing.
        {wildcard.length
          ? ` Relations that accept any kind (not drawn): ${wildcard.map((e) => e.kind).join(", ")}.`
          : ""}
      </p>
    </div>
  );
}
