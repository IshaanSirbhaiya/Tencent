'use client';
// Tab 4: the ontology of the active pack (GET /ontology, GET /ontology/packs): which node kinds and
// link kinds exist, what each binds to in the engine's closed meta-vocabulary, how they connect, and
// how many the store holds. Counts are the gateway's own.
import { BookOpen } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Badge, Card, CardBody, CardHeader, CardTitle, DataTable, SkeletonText, type ColumnDef } from '@/components/ui';
import { engineApi, type Ontology, type OntologyEdgeKind, type OntologyNodeKind, type OntologyPacks } from '@/lib/engine-api';
import { EngineCanvas, type Selection } from './EngineCanvas';
import type { Palette } from './palette';
import { EngineFailure } from './parts';
import { useEngine } from './use-engine';

export function OntologyTab({ palette }: { palette: Palette }) {
  const onto = useEngine<Ontology>('ontology', (s) => engineApi.ontology(s));
  const packs = useEngine<OntologyPacks>('ontology-packs', (s) => engineApi.ontologyPacks(s));
  const [selection, setSelection] = useState<Selection>(null);
  const o = onto.data;

  const schema = useMemo(() => {
    if (!o) return { nodes: [], edges: [] };
    const nodes = o.node_kinds.map((k) => ({ id: k.kind, kind: k.kind, label: palette.node(k.kind).label }));
    const edges = o.edge_kinds.flatMap((e) => e.from.flatMap((f) => e.to.map((t) => ({ id: `${e.kind}:${f}>${t}`, source: f, target: t, kind: e.kind }))));
    return { nodes, edges };
  }, [o, palette]);

  if (onto.loading) return <SkeletonText lines={6} label="Loading the ontology" />;
  if (onto.error) return <EngineFailure error={onto.error} onRetry={onto.reload} />;
  if (!o) return null;

  const nodeCols: ColumnDef<OntologyNodeKind, unknown>[] = [
    {
      id: 'kind',
      header: 'Node kind',
      accessorKey: 'kind',
      cell: ({ row }) => (
        <span className="inline-flex items-start gap-2">
          <span aria-hidden className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full border border-fg/25" style={{ background: palette.node(row.original.kind).color }} />
          <span className="inline-flex flex-col">
            <span className="text-fg">{palette.node(row.original.kind).label}</span>
            <span className="font-mono text-caption text-subtle">{row.original.kind}</span>
          </span>
        </span>
      ),
    },
    { id: 'binds', header: 'Binds to', accessorKey: 'binds_to', cell: ({ getValue }) => <Badge>{String(getValue())}</Badge>, meta: { className: 'hidden md:table-cell' } },
    { id: 'desc', header: 'Meaning', accessorKey: 'description', meta: { className: 'hidden lg:table-cell' } },
    { id: 'count', header: 'In store', accessorKey: 'count', cell: ({ getValue }) => Number(getValue()).toLocaleString('en-SG'), meta: { numeric: true } },
  ];
  const edgeCols: ColumnDef<OntologyEdgeKind, unknown>[] = [
    {
      id: 'kind',
      header: 'Link kind',
      accessorKey: 'kind',
      cell: ({ row }) => (
        <span className="inline-flex flex-col">
          <span className="text-fg">{palette.edgeLabel(row.original.kind)}</span>
          <span className="font-mono text-caption text-subtle">{row.original.kind}</span>
        </span>
      ),
    },
    {
      id: 'ends',
      header: 'From → to',
      accessorFn: (r) => `${r.from.join(', ')} → ${r.to.join(', ')}`,
      cell: ({ row }) => (
        <span className="text-caption text-muted">
          {row.original.from.map((k) => palette.node(k).label).join(', ')} <span aria-label="to">→</span> {row.original.to.map((k) => palette.node(k).label).join(', ')}
        </span>
      ),
    },
    { id: 'desc', header: 'Meaning', accessorKey: 'description', meta: { className: 'hidden lg:table-cell' } },
    { id: 'count', header: 'In store', accessorKey: 'count', cell: ({ getValue }) => Number(getValue()).toLocaleString('en-SG'), meta: { numeric: true } },
  ];

  return (
    <div className="space-y-3" data-testid="engine-ontology">
      <Card>
        <CardHeader
          actions={
            packs.data ? (
              <span className="flex flex-wrap items-center gap-1.5 text-caption text-muted">
                Enabled packs:
                {packs.data.enabled.map((p) => (
                  <Badge key={p} tone={p === packs.data!.default ? 'accent' : 'neutral'}>
                    {p}
                  </Badge>
                ))}
                <span className="text-subtle">of {packs.data.packs.length} installed</span>
              </span>
            ) : null
          }
        >
          <CardTitle as="h2">
            <span className="inline-flex items-center gap-2">
              <BookOpen aria-hidden className="h-4 w-4 text-subtle" /> {o.title}
            </span>
          </CardTitle>
          <p className="mt-0.5 text-caption text-muted">
            Pack <span className="font-mono">{o.pack}</span> · version <span className="tabular-nums">{o.version}</span> ·{' '}
            <span className="tabular-nums text-fg">{o.node_kinds.length}</span> node kinds ·{' '}
            <span className="tabular-nums text-fg">{o.edge_kinds.length}</span> link kinds. The engine itself knows no domain: these kinds are pack data.
          </p>
        </CardHeader>
        <CardBody>
          <EngineCanvas
            nodes={schema.nodes}
            edges={schema.edges}
            palette={palette}
            selection={selection}
            onSelect={setSelection}
            allLabels
            layout={{ charge: -900, distance: 170 }}
            label="Ontology schema: node kinds and the link kinds between them"
            className="h-[380px] sm:h-[420px]"
            testId="engine-ontology-canvas"
          />
        </CardBody>
      </Card>
      <div className="space-y-3">
        <DataTable caption="Node kinds" captionVisible columns={nodeCols} data={o.node_kinds} getRowId={(r) => r.kind} density="compact" initialSorting={[{ id: 'count', desc: true }]} />
        <DataTable caption="Link kinds" captionVisible columns={edgeCols} data={o.edge_kinds} getRowId={(r) => r.kind} density="compact" initialSorting={[{ id: 'count', desc: true }]} />
      </div>
    </div>
  );
}
