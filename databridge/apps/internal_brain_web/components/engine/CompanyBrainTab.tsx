'use client';
// Tab 2: the company brain. The gateway's bounded brain view (GET /brain/graph?limit=150, most
// connected first) with the bi-temporal time slider: drag back and everything that was not yet true
// on that date fades. The slider only changes VISIBILITY of valid_from / valid_to the gateway sent
// (the original time-slider.tsx contract). Click a node for its evidence sheet, rendered on the
// server for the same instant (?as_of=), and its timeline.
import { CalendarClock, History, Network, ScrollText, Table2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Card, EmptyState, SegmentedControl, SkeletonText, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui';
import { engineApi, formatDate, type BrainGraph, type NodeSheet, type Timeline } from '@/lib/engine-api';
import { EngineCanvas, type CanvasEdge, type CanvasNode, type Selection } from './EngineCanvas';
import { GraphTables } from './GraphTables';
import type { Palette } from './palette';
import { EngineFailure, KindBadge, PointerLine, SheetMarkdown } from './parts';
import { useEngine } from './use-engine';

const STEPS = 1000;
type View = 'canvas' | 'table';

function domainOf(g: BrainGraph | null): { min: number; max: number } | null {
  if (!g || g.nodes.length === 0) return null;
  const stamps: number[] = [];
  for (const x of [...g.nodes, ...g.edges]) {
    if (x.valid_from) stamps.push(Date.parse(x.valid_from));
    if (x.valid_to) stamps.push(Date.parse(x.valid_to));
  }
  const ok = stamps.filter(Number.isFinite);
  if (!ok.length) return null;
  const min = Math.min(...ok);
  const max = Math.max(...ok);
  return { min, max: max > min ? max : max + 86_400_000 };
}

function alive(x: { valid_from: string | null; valid_to: string | null }, at: number): boolean {
  if (!x.valid_from) return true;
  const from = Date.parse(x.valid_from);
  const to = x.valid_to ? Date.parse(x.valid_to) : Infinity;
  return from <= at && at < to;
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function NodeEvidence({ id, asOf, palette, kind, onEntity }: { id: string; asOf: string | null; palette: Palette; kind: string | null; onEntity: (id: string) => void }) {
  const sheet = useEngine<NodeSheet>(`s:${id}:${asOf ?? 'now'}`, (s) => engineApi.sheet(id, asOf, s));
  const timeline = useEngine<Timeline>(`t:${id}`, (s) => engineApi.timeline(id, s));
  return (
    <div className="space-y-2" data-testid="engine-brain-panel">
      {kind ? <KindBadge kind={kind} palette={palette} /> : null}
      <Tabs defaultValue="sheet">
        <TabsList aria-label="Node evidence">
          <TabsTrigger value="sheet">
            <ScrollText aria-hidden className="h-3.5 w-3.5" /> Evidence sheet
          </TabsTrigger>
          <TabsTrigger value="timeline">
            <History aria-hidden className="h-3.5 w-3.5" /> Timeline
          </TabsTrigger>
        </TabsList>
        <TabsContent value="sheet">
          {asOf ? <p className="mb-2 text-caption text-muted">Rendered by the engine as of <span className="text-fg">{formatDate(asOf)}</span>.</p> : null}
          {sheet.loading ? <SkeletonText lines={8} label="Loading the evidence sheet" /> : null}
          {sheet.error && asOf && sheet.error.refusal?.code === 'UNKNOWN_ENTITY' ? (
            <EmptyState
              compact
              icon={History}
              title={`Not yet in the graph on ${formatDate(asOf)}`}
              description={`The engine answered ${sheet.error.refusal.code}: ${sheet.error.refusal.message}. Move the slider later to see its sheet.`}
            />
          ) : sheet.error ? (
            <EngineFailure error={sheet.error} onRetry={sheet.reload} compact />
          ) : null}
          {sheet.data ? <SheetMarkdown markdown={sheet.data.markdown} onEntity={onEntity} /> : null}
        </TabsContent>
        <TabsContent value="timeline">
          {timeline.loading ? <SkeletonText lines={5} label="Loading the timeline" /> : null}
          {timeline.error ? <EngineFailure error={timeline.error} onRetry={timeline.reload} compact /> : null}
          {timeline.data ? (
            timeline.data.events.length ? (
              <ol className="relative space-y-3 border-l border-line pl-4">
                {timeline.data.events.map((ev, i) => (
                  <li key={i} className="relative">
                    <span aria-hidden className={`absolute -left-[1.3rem] top-1 h-2 w-2 rounded-full ${ev.kind === 'node' ? 'bg-accent' : 'bg-subtle'}`} />
                    <p className="text-caption tabular-nums text-subtle">
                      {formatDate(ev.valid_from)}
                      {ev.valid_to ? ` to ${formatDate(ev.valid_to)}` : ''} · {ev.kind === 'node' ? 'entity' : 'link'}
                    </p>
                    <p className="break-words font-mono text-caption text-fg">{ev.label}</p>
                    {ev.pointers?.[0] ? <PointerLine pointer={ev.pointers[0]} className="mt-0.5" /> : null}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-caption text-muted">No events recorded.</p>
            )
          ) : null}
        </TabsContent>
      </Tabs>
    </div>
  );
}

export function CompanyBrainTab({ palette, focusId }: { palette: Palette; focusId: string | null }) {
  const brain = useEngine<BrainGraph>('brain', (s) => engineApi.brainGraph(150, s));
  const [pos, setPos] = useState(STEPS);
  const [view, setView] = useState<View>('canvas');
  const [selection, setSelection] = useState<Selection>(focusId ? { type: 'node', id: focusId } : null);
  useEffect(() => {
    if (focusId) setSelection({ type: 'node', id: focusId });
  }, [focusId]);

  const g = brain.data;
  const domain = useMemo(() => domainOf(g), [g]);
  const atNow = pos >= STEPS;
  const at = domain ? (atNow ? Date.now() : domain.min + ((domain.max - domain.min) * pos) / STEPS) : Date.now();
  const asOfIso = atNow || !domain ? null : new Date(at).toISOString();
  const sheetAsOf = useDebounced(asOfIso, 300);

  const nodes: CanvasNode[] = useMemo(() => (g ? g.nodes.map((n) => ({ id: n.id, kind: n.kind, label: n.label, dim: !alive(n, at) })) : []), [g, at]);
  const edges: CanvasEdge[] = useMemo(() => (g ? g.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, kind: e.kind, dim: !alive(e, at) })) : []), [g, at]);
  const existed = nodes.filter((n) => !n.dim).length;

  if (brain.loading) return <SkeletonText lines={6} label="Loading the company brain" />;
  if (brain.error) return <EngineFailure error={brain.error} onRetry={brain.reload} />;
  if (!g || g.nodes.length === 0) return <EmptyState icon={Network} title="The engine graph is empty" description="Build it with scripts/build_enterprise_graph.py, then restart the engine." />;

  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const selectedNode = selection?.type === 'node' ? byId.get(selection.id) ?? null : null;
  const stepMonth = (dir: 1 | -1) => {
    if (!domain) return;
    const d = new Date(at);
    d.setMonth(d.getMonth() + dir);
    const p = Math.round(((d.getTime() - domain.min) / (domain.max - domain.min)) * STEPS);
    setPos(Math.max(0, Math.min(STEPS, p)));
  };

  return (
    <div className="space-y-3">
      <Card className="p-3" data-testid="engine-time-slider">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <CalendarClock aria-hidden className="h-4 w-4 shrink-0 text-subtle" />
          <label htmlFor="engine-asof" className="text-body-sm font-medium text-fg">
            As of
          </label>
          <span className="min-w-[7.5rem] text-body-sm font-semibold tabular-nums text-accent" data-testid="engine-asof">
            {atNow ? 'now' : formatDate(new Date(at).toISOString())}
          </span>
          <span className="text-caption text-muted" data-testid="engine-asof-count">
            <span className="tabular-nums text-fg">{existed.toLocaleString('en-SG')}</span> of the{' '}
            <span className="tabular-nums text-fg">{g.nodes.length.toLocaleString('en-SG')}</span> drawn entities were true on this date
          </span>
          <div className="ml-auto flex items-center gap-1">
            <Button size="xs" variant="ghost" onClick={() => stepMonth(-1)} disabled={pos <= 0}>
              −1 month
            </Button>
            <Button size="xs" variant="ghost" onClick={() => stepMonth(1)} disabled={atNow}>
              +1 month
            </Button>
            <Button size="xs" variant="secondary" onClick={() => setPos(STEPS)} disabled={atNow}>
              Now
            </Button>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-3">
          <span className="w-20 shrink-0 text-caption tabular-nums text-subtle">{domain ? formatDate(new Date(domain.min).toISOString()) : '—'}</span>
          <input
            id="engine-asof"
            type="range"
            min={0}
            max={STEPS}
            step={1}
            value={pos}
            disabled={!domain}
            onChange={(e) => setPos(Number(e.target.value))}
            aria-valuetext={atNow ? 'now' : formatDate(new Date(at).toISOString())}
            className="h-6 flex-1 cursor-pointer accent-[rgb(var(--accent))] disabled:cursor-not-allowed"
          />
          <span className="w-12 shrink-0 text-right text-caption text-subtle">Now</span>
        </div>
        <p className="mt-1.5 text-caption text-subtle">
          Faded = not yet true on that date (valid_from later, or valid_to earlier). The engine holds{' '}
          <span className="tabular-nums">{g.stats.nodes.toLocaleString('en-SG')}</span> entities and{' '}
          <span className="tabular-nums">{g.stats.edges.toLocaleString('en-SG')}</span> links; this view draws the{' '}
          <span className="tabular-nums">{g.nodes.length.toLocaleString('en-SG')}</span> best connected. Provenance coverage:{' '}
          <span className="tabular-nums text-fg">{g.stats.provenance_pct}%</span>.
        </p>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        {g.stats.truncated ? <Badge tone="neutral">Bounded view: most-connected first</Badge> : null}
        <div className="ml-auto">
          <SegmentedControl<View>
            label="Show the brain as"
            size="sm"
            value={view}
            onChange={setView}
            options={[
              { value: 'canvas', label: 'Canvas', icon: Network },
              { value: 'table', label: 'Table', icon: Table2 },
            ]}
          />
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0">
          {view === 'canvas' ? (
            <EngineCanvas
              nodes={nodes}
              edges={edges}
              palette={palette}
              selection={selection}
              onSelect={setSelection}
              label="Company brain canvas. Faded nodes were not yet true on the chosen date."
              layout={{ charge: -380, distance: 90 }}
              testId="engine-brain-canvas"
            />
          ) : (
            <GraphTables
              nodes={nodes}
              edges={edges}
              palette={palette}
              selection={selection}
              onSelect={setSelection}
              validFrom={(id) => byId.get(id)?.valid_from ?? g.edges.find((e) => e.id === id)?.valid_from ?? null}
              caption="Company brain"
            />
          )}
        </div>
        <Card as="aside" className="min-w-0 self-start p-3 lg:sticky lg:top-[calc(var(--topbar-h)+1rem)] lg:max-h-[calc(100vh-var(--topbar-h)-2rem)] lg:overflow-auto" aria-label="Node evidence">
          {selection?.type === 'node' ? (
            <NodeEvidence id={selection.id} asOf={sheetAsOf} palette={palette} kind={selectedNode?.kind ?? null} onEntity={(id) => setSelection({ type: 'node', id })} />
          ) : (
            <EmptyState compact icon={ScrollText} title="Select a node" description="Its evidence sheet: every value with the citation for that value alone, as of the slider's date." />
          )}
        </Card>
      </div>
    </div>
  );
}
