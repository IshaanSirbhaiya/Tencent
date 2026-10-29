'use client';
// Table view: the same nodes the canvas draws (same filters, same working set), for keyboard and
// screen-reader users and for anyone who wants to read rather than look. Rows open the node panel.
import { useMemo } from 'react';
import { type ColumnDef, DataTable, PlatformIcon } from '@/components/ui';
import { fmtDate } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import type { CanvasView } from './GraphCanvas';
import { displayLabel, placeLabel, platformName, typeName } from './format';
import type { KGraph, NodeAttrs } from './model';

interface Row {
  id: string;
  name: string;
  type: string;
  kind: NodeAttrs['kind'];
  platform: string | null;
  place: string;
  links: number;
  updated: string | null;
}

const KIND_RANK = { platform: 0, container: 1, person: 2, doc: 3 } as const;

export function visibleIds(graph: KGraph, view: CanvasView): string[] {
  const ids: string[] = [];
  graph.forEachNode((id, a) => {
    if (view.scope && !view.scope.has(id)) return;
    if ((a.platform && view.hiddenPlatforms.has(a.platform)) || view.hiddenKinds.has(a.kind)) return;
    ids.push(id);
  });
  return ids;
}

export function GraphTable({
  graph,
  version,
  view,
  selected,
  onOpen,
  testId,
}: {
  graph: KGraph;
  version: number;
  view: CanvasView;
  selected: string | null;
  onOpen: (id: string) => void;
  testId?: string;
}) {
  const t = useMessages(graphMessages);
  const rows = useMemo<Row[]>(() => {
    return visibleIds(graph, view)
      .map((id) => {
        const a = graph.getNodeAttributes(id);
        return {
          id,
          name: displayLabel(a),
          type: typeName(a),
          kind: a.kind,
          platform: a.platform,
          place: a.kind === 'doc' ? placeLabel(a.containerName, a.platform) : '',
          links: graph.degree(id),
          updated: a.updatedAt,
        };
      })
      .sort((x, y) => KIND_RANK[x.kind] - KIND_RANK[y.kind] || x.name.localeCompare(y.name));
    // the graph mutates in place; `version` marks a merge
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, version, view]);

  const columns = useMemo<ColumnDef<Row, unknown>[]>(
    () => [
      {
        id: 'name',
        header: t('table.name'),
        accessorFn: (r) => r.name,
        cell: ({ row }) => <span className="block max-w-[11rem] truncate font-medium text-fg sm:max-w-[20rem]">{row.original.name}</span>,
      },
      { id: 'type', header: t('table.type'), accessorFn: (r) => r.type, cell: ({ getValue }) => <span className="whitespace-nowrap">{String(getValue())}</span> },
      {
        id: 'platform',
        header: t('table.platform'),
        accessorFn: (r) => platformName(r.platform),
        cell: ({ row }) => (row.original.platform ? <PlatformIcon platform={row.original.platform} showLabel tone="platform" size="sm" className="whitespace-nowrap" /> : <span className="text-subtle">—</span>),
      },
      { id: 'place', header: t('table.place'), accessorFn: (r) => r.place, meta: { className: 'hidden md:table-cell' }, cell: ({ getValue }) => <span className="block max-w-[12rem] truncate text-muted">{String(getValue() || '—')}</span> },
      { id: 'links', header: t('table.links'), accessorFn: (r) => r.links, meta: { numeric: true } },
      {
        id: 'updated',
        header: t('table.updated'),
        accessorFn: (r) => r.updated ?? '',
        meta: { className: 'hidden lg:table-cell' },
        cell: ({ row }) => <span className="tabular-nums text-muted">{row.original.updated ? fmtDate(row.original.updated) : '—'}</span>,
      },
    ],
    [t],
  );

  return (
    <DataTable
      columns={columns}
      data={rows}
      caption={`${t('table.caption')} (${rows.length.toLocaleString('en-SG')})`}
      getRowId={(r) => r.id}
      onRowClick={(r) => onOpen(r.id)}
      rowLabel={(r) => `${r.name}, ${r.type}${r.platform ? `, ${platformName(r.platform)}` : ''}`}
      isRowSelected={(r) => r.id === selected}
      density="compact"
      maxHeightClass="max-h-full"
      className="h-full"
      empty={<p className="px-4 py-8 text-center text-body-sm text-muted">{t('table.empty')}</p>}
      testId={testId}
    />
  );
}
