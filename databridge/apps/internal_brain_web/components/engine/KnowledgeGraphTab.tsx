'use client';
// Tab 1: the entity-level knowledge graph. Starts on the gateway's default view (the best-connected
// nodes and their neighbours), focuses on a searched entity, expands a node's neighbours in place.
// Every count in the status line is the gateway's own (`returned`, `matched_total`, `edges_returned`).
import { Network, RotateCcw, Table2 } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { Badge, Button, Card, SegmentedControl, SkeletonText } from '@/components/ui';
import { engineApi, labelText, type GraphEdgeSummary, type GraphFacets, type GraphNeighborhood, type GraphNodeSummary, EngineError } from '@/lib/engine-api';
import { DetailPanel } from './DetailPanel';
import { EngineCanvas, type CanvasEdge, type CanvasNode, type Selection } from './EngineCanvas';
import { GraphTables } from './GraphTables';
import type { Palette } from './palette';
import { EngineFailure, EntitySearch, KindLegend } from './parts';

type View = 'canvas' | 'table';
type Depth = '1' | '2';

interface Drawn {
  nodes: Map<string, GraphNodeSummary>;
  edges: Map<string, GraphEdgeSummary>;
  last: GraphNeighborhood;
  expanded: boolean;
}

function fromResponse(r: GraphNeighborhood): Drawn {
  return { nodes: new Map(r.nodes.map((n) => [n.id, n])), edges: new Map(r.edges.map((e) => [e.id, e])), last: r, expanded: false };
}

export function KnowledgeGraphTab({
  hubs,
  facets,
  palette,
  onTrace,
  onOpenBrain,
}: {
  hubs: GraphNeighborhood;
  facets: GraphFacets | null;
  palette: Palette;
  onTrace: (id: string) => void;
  onOpenBrain: (id: string) => void;
}) {
  const [drawn, setDrawn] = useState<Drawn>(() => fromResponse(hubs));
  const [selection, setSelection] = useState<Selection>(null);
  const [view, setView] = useState<View>('canvas');
  const [depth, setDepth] = useState<Depth>('1');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<EngineError | null>(null);

  const load = useCallback(async (seed: string, d: number, merge: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const r = await engineApi.neighborhood(seed, d, 80);
      setDrawn((prev) => {
        if (!merge) return fromResponse(r);
        const nodes = new Map(prev.nodes);
        const edges = new Map(prev.edges);
        r.nodes.forEach((n) => nodes.set(n.id, n));
        r.edges.forEach((e) => edges.set(e.id, e));
        return { nodes, edges, last: r, expanded: true };
      });
      setSelection({ type: 'node', id: seed });
    } catch (e) {
      setError(e instanceof EngineError ? e : new EngineError(0, 'bad', String(e)));
    } finally {
      setBusy(false);
    }
  }, []);

  const focus = (id: string) => void load(id, Number(depth), false);
  const expand = (id: string) => void load(id, 1, true);
  const reset = () => {
    setDrawn(fromResponse(hubs));
    setSelection(null);
    setError(null);
  };

  const nodes: CanvasNode[] = useMemo(() => [...drawn.nodes.values()].map((n) => ({ id: n.id, kind: n.kind, label: labelText(n.label) })), [drawn]);
  const edges: CanvasEdge[] = useMemo(
    () => [...drawn.edges.values()].map((e) => ({ id: e.id, source: e.src_id, target: e.dst_id, kind: e.kind })),
    [drawn],
  );
  const labelOf = useCallback((id: string) => {
    const n = drawn.nodes.get(id);
    return n ? labelText(n.label) : null;
  }, [drawn]);
  const validFrom = useCallback((id: string) => drawn.nodes.get(id)?.valid_from ?? drawn.edges.get(id)?.valid_from ?? null, [drawn]);

  const last = drawn.last;
  const seedLabel = last.seed ? labelOf(last.seed) ?? last.seed : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <EntitySearch onPick={focus} palette={palette} />
        <SegmentedControl<Depth>
          label="Hops from the focused entity"
          size="sm"
          value={depth}
          onChange={setDepth}
          options={[
            { value: '1', label: '1 hop' },
            { value: '2', label: '2 hops' },
          ]}
        />
        <Button size="sm" variant="ghost" icon={RotateCcw} onClick={reset} disabled={busy}>
          Best-connected view
        </Button>
        <div className="ml-auto">
          <SegmentedControl<View>
            label="Show the graph as"
            size="sm"
            value={view}
            onChange={setView}
            testId="engine-graph-view"
            options={[
              { value: 'canvas', label: 'Canvas', icon: Network },
              { value: 'table', label: 'Table', icon: Table2 },
            ]}
          />
        </div>
      </div>

      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted" aria-live="polite" data-testid="engine-graph-status">
        {busy ? <span>Loading the neighbourhood…</span> : null}
        {!busy && last.mode === 'hubs' && !drawn.expanded ? (
          <span>
            The best-connected entities and their neighbours:{' '}
            <span className="tabular-nums text-fg">{last.returned.toLocaleString('en-SG')}</span> of{' '}
            {last.matched_total !== null ? <span className="tabular-nums text-fg">{last.matched_total.toLocaleString('en-SG')}</span> : 'an unknown number of'} nodes
            {last.edges_returned !== undefined ? (
              <>
                {', '}
                <span className="tabular-nums text-fg">{last.edges_returned.toLocaleString('en-SG')}</span> links
              </>
            ) : null}
            .
          </span>
        ) : null}
        {!busy && (last.mode === 'seed' || drawn.expanded) ? (
          <span>
            {drawn.expanded ? 'Expanded around ' : `${last.depth ?? 1}-hop neighbourhood of `}
            <span className="font-medium text-fg">{seedLabel}</span>: this call returned{' '}
            <span className="tabular-nums text-fg">{last.returned.toLocaleString('en-SG')}</span>
            {last.matched_total !== null ? (
              <>
                {' '}of <span className="tabular-nums text-fg">{last.matched_total.toLocaleString('en-SG')}</span>
              </>
            ) : ' (total unknown)'}{' '}
            nodes.
          </span>
        ) : null}
        {!busy && last.truncated ? <Badge tone="warn">Windowed subgraph</Badge> : null}
        <span className="text-subtle">Subgraph only; the whole store is never drawn.</span>
      </p>

      {error ? <EngineFailure error={error} compact /> : null}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-2">
          {view === 'canvas' ? (
            <EngineCanvas
              nodes={nodes}
              edges={edges}
              palette={palette}
              selection={selection}
              onSelect={setSelection}
              onExpand={expand}
              keepPositions={drawn.expanded}
              label="Knowledge graph canvas. Tab to a node or link and press Enter to select it."
              testId="engine-graph-canvas"
            />
          ) : (
            <GraphTables nodes={nodes} edges={edges} palette={palette} selection={selection} onSelect={setSelection} validFrom={validFrom} caption="Knowledge graph" />
          )}
          {facets ? <KindLegend kinds={facets.node_kinds} palette={palette} /> : null}
        </div>
        <Card as="aside" className="min-w-0 self-start p-3 lg:sticky lg:top-[calc(var(--topbar-h)+1rem)] lg:max-h-[calc(100vh-var(--topbar-h)-2rem)] lg:overflow-auto" aria-label="Selection details">
          {busy && !selection ? <SkeletonText lines={4} label="Loading" /> : null}
          <DetailPanel
            selection={selection}
            palette={palette}
            labelOf={labelOf}
            onExpand={expand}
            onTrace={onTrace}
            onOpenBrain={onOpenBrain}
            onSelect={setSelection}
          />
        </Card>
      </div>
    </div>
  );
}
