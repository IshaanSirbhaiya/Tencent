'use client';
// Side panel of the knowledge graph: the selected node (GET /graph/node/{id}) or link
// (GET /graph/edge/{id}). Each attribute value is shown beside the pointer that grounds THAT value;
// a value with no pointer of its own is named as withheld, never shown as a fact.
import { ArrowRight, Expand, GitBranch, MousePointerClick, Route } from 'lucide-react';
import { Badge, Button, CodeHash, EmptyState, SkeletonText } from '@/components/ui';
import {
  engineApi,
  formatDate,
  formatValue,
  labelText,
  type GraphEdgeDetail,
  type GraphNodeDetail,
  type SourcePointer,
} from '@/lib/engine-api';
import type { Selection } from './EngineCanvas';
import type { Palette } from './palette';
import { EngineFailure, KindBadge, PointerLine } from './parts';
import { useEngine } from './use-engine';

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-2 py-1">
      <dt className="text-caption text-subtle">{label}</dt>
      <dd className="min-w-0 text-body-sm text-fg">{children}</dd>
    </div>
  );
}

function Attributes({ attrs, sources }: { attrs: Record<string, unknown>; sources: Record<string, SourcePointer> }) {
  const entries = Object.entries(attrs ?? {});
  if (!entries.length) return <p className="text-caption text-subtle">No attributes.</p>;
  return (
    <ul className="divide-y divide-line rounded-md border border-line">
      {entries.map(([field, value]) => {
        const p = sources?.[field];
        const empty = value === null || value === undefined;
        return (
          <li key={field} className="px-2.5 py-2">
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-mono text-caption text-subtle">{field}</span>
              {empty ? (
                <span className="text-caption text-subtle">empty</span>
              ) : p ? (
                <span className="min-w-0 truncate text-right text-body-sm tabular-nums text-fg" title={formatValue(value)}>
                  {formatValue(value)}
                </span>
              ) : (
                <Badge tone="warn">withheld: no pointer</Badge>
              )}
            </div>
            {p && !empty ? <PointerLine pointer={p} className="mt-1" /> : null}
          </li>
        );
      })}
    </ul>
  );
}

function Sources({ sources, total }: { sources: SourcePointer[]; total?: number }) {
  return (
    <div>
      <h4 className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">
        Entity citations <span className="tabular-nums">({(total ?? sources.length).toLocaleString('en-SG')})</span>
      </h4>
      <ul className="space-y-1">
        {sources.slice(0, 8).map((p, i) => (
          <li key={i}>
            <PointerLine pointer={p} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DetailPanel({
  selection,
  palette,
  labelOf,
  onExpand,
  onTrace,
  onOpenBrain,
  onSelect,
}: {
  selection: Selection;
  palette: Palette;
  labelOf: (id: string) => string | null;
  onExpand: (id: string) => void;
  onTrace: (id: string) => void;
  onOpenBrain: (id: string) => void;
  onSelect: (s: Selection) => void;
}) {
  const node = useEngine<GraphNodeDetail>(selection?.type === 'node' ? `n:${selection.id}` : null, (s) => engineApi.node(selection!.id, s));
  const edge = useEngine<GraphEdgeDetail>(selection?.type === 'edge' ? `e:${selection.id}` : null, (s) => engineApi.edge(selection!.id, s));

  if (!selection) {
    return (
      <EmptyState
        compact
        icon={MousePointerClick}
        title="Select a node or a link"
        description="Click, or Tab to it and press Enter. Double-click a node (or use Expand) to load its neighbours."
      />
    );
  }
  const q = selection.type === 'node' ? node : edge;
  if (q.loading) return <SkeletonText lines={6} label="Loading details" />;
  if (q.error) return <EngineFailure error={q.error} onRetry={q.reload} compact />;

  if (selection.type === 'node' && node.data) {
    const d = node.data;
    return (
      <div className="space-y-4" data-testid="engine-node-panel">
        <div>
          <KindBadge kind={d.kind} palette={palette} />
          <h3 className="mt-1.5 break-words text-heading-sm text-fg">{labelText(d.label)}</h3>
          <dl className="mt-2">
            <Row label="Id">
              <CodeHash value={d.id} head={24} tail={10} label="node id" />
            </Row>
            {d.canonical_id ? <Row label="Canonical id"><span className="break-all font-mono text-caption">{d.canonical_id}</span></Row> : null}
            <Row label="True from">{formatDate(d.valid_from)}{d.valid_to ? ` to ${formatDate(d.valid_to)}` : ' (still current)'}</Row>
            <Row label="Recorded">{formatDate(d.system_from, true)}</Row>
            <Row label="Links">
              <span className="tabular-nums">{d.edges.matched_total.toLocaleString('en-SG')}</span>
            </Row>
          </dl>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" icon={Expand} onClick={() => onExpand(d.id)}>Expand</Button>
          <Button size="sm" icon={Route} onClick={() => onTrace(d.id)}>Trace provenance</Button>
          <Button size="sm" variant="ghost" icon={GitBranch} onClick={() => onOpenBrain(d.id)}>Evidence sheet</Button>
        </div>
        <div>
          <h4 className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">Attributes, each with its own pointer</h4>
          <Attributes attrs={d.attrs} sources={d.attribute_sources} />
          {d.unresolved.length ? <p className="mt-1 text-caption text-subtle">Asked for and not found: {d.unresolved.join(', ')}</p> : null}
        </div>
        <Sources sources={d.sources} total={d.sources_total} />
      </div>
    );
  }

  if (selection.type === 'edge' && edge.data) {
    const d = edge.data;
    const end = (id: string) => (
      <button type="button" className="min-w-0 truncate text-left text-accent underline decoration-dotted underline-offset-2 hover:decoration-solid" onClick={() => onSelect({ type: 'node', id })}>
        {labelOf(id) ?? id}
      </button>
    );
    return (
      <div className="space-y-4" data-testid="engine-edge-panel">
        <div>
          <Badge tone="accent">{palette.edgeLabel(d.kind)}</Badge>
          <p className="mt-2 flex min-w-0 flex-wrap items-center gap-1.5 text-body-sm">
            {end(d.src_id)}
            <ArrowRight aria-label="to" className="h-3.5 w-3.5 shrink-0 text-subtle" />
            {end(d.dst_id)}
          </p>
          <dl className="mt-2">
            <Row label="Kind"><span className="font-mono text-caption">{d.kind}</span></Row>
            <Row label="True from">{formatDate(d.valid_from)}{d.valid_to ? ` to ${formatDate(d.valid_to)}` : ' (still current)'}</Row>
            <Row label="Recorded">{formatDate(d.system_from, true)}</Row>
            <Row label="Attestation">{d.has_attestation ? 'Attested by a person' : 'None (read from the source)'}</Row>
          </dl>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" icon={Route} onClick={() => onTrace(d.id)}>Trace provenance</Button>
        </div>
        {Object.keys(d.attrs ?? {}).length ? (
          <div>
            <h4 className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">Attributes</h4>
            <Attributes attrs={d.attrs} sources={d.attribute_sources} />
          </div>
        ) : null}
        <Sources sources={d.sources} total={d.sources_total} />
      </div>
    );
  }
  return null;
}
