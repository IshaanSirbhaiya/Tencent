"use client";
import * as React from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";
import type { AnnotatedSchema } from "@/lib/types";
import { bandOfColumn } from "@/lib/types";
import { roleBg } from "@/lib/utils";

// Schema graph (React Flow). Deterministic layout (fixed seed positions per table index, no
// physics) — same query renders the same picture twice (REVIEW_1 FM2). Node count is bounded by
// table count (tens), so React Flow is the right tool here; Sigma is a Phase-B/KG concern.
function TableNode({ data }: { data: { table: string; role: string; nCols: number; review: number } }) {
  return (
    <div className="min-w-[150px] rounded border border-border bg-elevated px-3 py-2 shadow-sm">
      <Handle type="target" position={Position.Left} className="!bg-border" />
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs font-semibold text-fg">{data.table}</span>
        <span className={`rounded border px-1 text-2xs ${roleBg(data.role)}`}>{data.role}</span>
      </div>
      <div className="mt-1 flex items-center gap-2 text-2xs text-subtle">
        <span>{data.nCols} cols</span>
        {data.review > 0 && <span className="text-band-review">{data.review} review</span>}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-border" />
    </div>
  );
}

const nodeTypes = { table: TableNode };

export function SchemaGraph({
  schema,
  onSelect,
}: {
  schema: AnnotatedSchema;
  onSelect?: (table: string) => void;
}) {
  const { nodes, edges } = React.useMemo(() => {
    const cols = 3;
    const nodes: Node[] = schema.tables.map((t, i) => ({
      id: t.table,
      type: "table",
      position: { x: (i % cols) * 230, y: Math.floor(i / cols) * 130 },
      data: {
        table: t.table,
        role: t.role,
        nCols: t.columns.length,
        review: t.columns.filter((c) => bandOfColumn(c) === "review").length,
      },
    }));
    const edges: Edge[] = schema.relationships.map((r, i) => ({
      id: `e${i}`,
      source: r.src_table,
      target: r.dst_table,
      label: r.name,
      animated: false,
      style: {
        stroke: r.declared ? "hsl(var(--accent))" : "hsl(var(--subtle))",
        strokeDasharray: r.declared ? undefined : "4 3",
      },
      labelStyle: { fontSize: 9, fill: "hsl(var(--subtle))" },
    }));
    return { nodes, edges };
  }, [schema]);

  return (
    <div className="h-[360px] w-full rounded border border-border bg-surface">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_e, n) => onSelect?.(n.id)}
        nodesDraggable={false}
        nodesConnectable={false}
      >
        <Background color="hsl(var(--border))" gap={18} />
        <Controls showInteractive={false} className="!bg-elevated !border-border" />
      </ReactFlow>
    </div>
  );
}
