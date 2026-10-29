'use client';
// The table alternative to a canvas: the same nodes and links, sortable and keyboard-operable.
import { DataTable, type ColumnDef } from '@/components/ui';
import { formatDate } from '@/lib/engine-api';
import type { CanvasEdge, CanvasNode, Selection } from './EngineCanvas';
import type { Palette } from './palette';

interface NodeRow extends CanvasNode {
  validFrom: string | null;
}
interface EdgeRow extends CanvasEdge {
  from: string;
  to: string;
  validFrom: string | null;
}

export function GraphTables({
  nodes,
  edges,
  palette,
  selection,
  onSelect,
  validFrom,
  caption,
}: {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  palette: Palette;
  selection: Selection;
  onSelect: (s: Selection) => void;
  validFrom: (id: string) => string | null;
  caption: string;
}) {
  const label = new Map(nodes.map((n) => [n.id, n.label]));
  const nodeRows: NodeRow[] = nodes.map((n) => ({ ...n, validFrom: validFrom(n.id) }));
  const edgeRows: EdgeRow[] = edges
    .filter((e) => label.has(e.source) && label.has(e.target))
    .map((e) => ({ ...e, from: label.get(e.source)!, to: label.get(e.target)!, validFrom: validFrom(e.id) }));

  const dimNote = (dim?: boolean) => (dim ? <span className="ml-1 text-caption text-subtle">(later)</span> : null);

  const nodeCols: ColumnDef<NodeRow, unknown>[] = [
    {
      id: 'label',
      header: 'Entity',
      accessorKey: 'label',
      cell: ({ row }) => (
        <span className="inline-flex min-w-0 items-center gap-2">
          <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: palette.node(row.original.kind).color }} />
          <span className="truncate text-fg">{row.original.label}</span>
          {dimNote(row.original.dim)}
        </span>
      ),
    },
    { id: 'kind', header: 'Kind', accessorFn: (r) => palette.node(r.kind).label, meta: { className: 'hidden sm:table-cell' } },
    { id: 'from', header: 'True from', accessorKey: 'validFrom', cell: ({ row }) => <span className="tabular-nums">{formatDate(row.original.validFrom)}</span> },
  ];
  const edgeCols: ColumnDef<EdgeRow, unknown>[] = [
    { id: 'from', header: 'From', accessorKey: 'from', cell: ({ row }) => <span className="text-fg">{row.original.from}{dimNote(row.original.dim)}</span> },
    { id: 'kind', header: 'Link', accessorFn: (r) => palette.edgeLabel(r.kind) },
    { id: 'to', header: 'To', accessorKey: 'to', cell: ({ row }) => <span className="text-fg">{row.original.to}</span> },
  ];

  return (
    <div className="grid gap-4 xl:grid-cols-2" data-testid="engine-graph-table">
      <DataTable
        caption={`${caption}: nodes (${nodeRows.length})`}
        captionVisible
        columns={nodeCols}
        data={nodeRows}
        getRowId={(r) => r.id}
        onRowClick={(r) => onSelect({ type: 'node', id: r.id })}
        rowLabel={(r) => `${palette.node(r.kind).label}: ${r.label}`}
        isRowSelected={(r) => selection?.type === 'node' && selection.id === r.id}
        density="compact"
        maxHeightClass="max-h-[420px]"
      />
      <DataTable
        caption={`${caption}: links (${edgeRows.length})`}
        captionVisible
        columns={edgeCols}
        data={edgeRows}
        getRowId={(r) => r.id}
        onRowClick={(r) => onSelect({ type: 'edge', id: r.id })}
        rowLabel={(r) => `${r.from} ${palette.edgeLabel(r.kind)} ${r.to}`}
        isRowSelected={(r) => selection?.type === 'edge' && selection.id === r.id}
        density="compact"
        maxHeightClass="max-h-[420px]"
      />
    </div>
  );
}
