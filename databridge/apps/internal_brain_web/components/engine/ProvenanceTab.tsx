'use client';
// Tab 3: provenance. For one value of one entity, the chain the engine recorded when it read it:
// value ← field ← row (or line / page) ← table ← file ← source. All of it is GET /evidence/trace/{id}:
// the per-value pointer, the table cell it names, the re-verification status, the entity's own
// citations, its relationships and the gateway's stated limitation. Nothing is re-derived here.
import { ArrowLeft, Braces, CheckCircle2, CircleSlash, Route, ShieldQuestion, TriangleAlert } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge, Card, CardBody, CardHeader, CardTitle, DataTable, SkeletonText, type ColumnDef } from '@/components/ui';
import { cn } from '@/lib/cn';
import {
  engineApi,
  fixText,
  formatDate,
  formatValue,
  type EvidenceAttribute,
  type EvidenceRelationship,
  type EvidenceTrace,
  type EvidenceVerify,
  type SourcePointer,
} from '@/lib/engine-api';
import type { Palette } from './palette';
import { EngineFailure, EntitySearch, KindBadge, PointerLine } from './parts';
import { useEngine } from './use-engine';

function VerifyBadge({ verify }: { verify: EvidenceVerify }) {
  const s = verify.status;
  if (s === 'verified') return <Badge tone="ok" icon={CheckCircle2}>Re-verified against the captured bytes</Badge>;
  if (s === 'no_capture_binding' || s === 'no_bytes' || s === 'no_text')
    return <Badge tone="neutral" icon={ShieldQuestion}>{s}: shown as recorded</Badge>;
  return <Badge tone="bad" icon={TriangleAlert}>{s}</Badge>;
}

interface Step {
  key: string;
  label: string;
  value: string;
  hint?: string;
  mono?: boolean;
}

/** The chain, in the order the brief reads it: value ← field ← row/line/page ← table ← file ← source. */
function chainOf(field: string, ev: EvidenceAttribute, entityLabel: string, route: string | null): Step[] {
  const p: SourcePointer = ev.pointer;
  const cell = ev.table_cell;
  const steps: Step[] = [
    { key: 'value', label: 'Value', value: ev.value === null || ev.value === undefined ? 'empty' : formatValue(ev.value) },
    { key: 'field', label: 'Field', value: field, hint: `of ${entityLabel}`, mono: true },
  ];
  const row = cell?.row ?? p.row_id ?? null;
  if (row !== null) steps.push({ key: 'row', label: 'Row', value: String(row), hint: (cell?.column ?? p.column) ? `column ${cell?.column ?? p.column}` : undefined, mono: true });
  else {
    const where: string[] = [];
    if (p.page !== undefined && p.page !== null) where.push(`page ${p.page}`);
    if (p.line !== undefined && p.line !== null) where.push(`line ${p.line}`);
    if (p.char_start !== undefined && p.char_start !== null) where.push(`chars ${p.char_start}–${p.char_end ?? '?'}`);
    steps.push({ key: 'row', label: 'Location', value: where.join(', ') || p.kind, mono: true });
  }
  const table = cell?.table ?? p.table ?? null;
  if (table) steps.push({ key: 'table', label: 'Table', value: table, mono: true });
  steps.push({ key: 'file', label: 'File', value: cell?.source ?? p.source_id, hint: p.trust ? `${p.kind} · ${p.trust}` : p.kind, mono: true });
  // The source system is the namespace the engine's loader gave the table (e.g. "confluence" in
  // "confluence.pages"); the route is how the entity reached the graph (closed vocabulary).
  const system = table && table.includes('.') ? table.split('.')[0] : null;
  steps.push({ key: 'source', label: 'Source', value: system ?? p.source_id, hint: route ? `reached the graph by: ${route}` : undefined, mono: true });
  return steps;
}

function Chain({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex flex-col gap-1.5 xl:flex-row xl:flex-wrap xl:items-stretch" aria-label="Provenance chain, from the value back to its source" data-testid="engine-chain">
      {steps.map((s, i) => (
        <li key={s.key} className="flex items-stretch gap-1.5 xl:items-center">
          {i > 0 ? (
            <span aria-hidden className="flex w-6 shrink-0 items-center justify-center text-subtle xl:w-auto">
              <ArrowLeft className="h-3.5 w-3.5 rotate-90 xl:rotate-0" />
            </span>
          ) : null}
          <div className={cn('min-w-0 flex-1 rounded-md border px-2.5 py-1.5 xl:flex-none', i === 0 ? 'border-accent/50 bg-accent/10' : 'border-line bg-raised')}>
            <p className="text-micro font-semibold uppercase tracking-wider text-muted">
              <span className="sr-only">{i === 0 ? '' : 'comes from '}</span>
              {s.label}
            </p>
            <p className={cn('break-all text-body-sm text-fg', s.mono ? 'font-mono' : 'font-semibold tabular-nums')}>{s.value}</p>
            {s.hint ? <p className="text-caption text-muted">{s.hint}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function ProvenanceTab({ entity, onEntity, palette }: { entity: string; onEntity: (id: string) => void; palette: Palette }) {
  const trace = useEngine<EvidenceTrace>(`trace:${entity}`, (s) => engineApi.trace(entity, s));
  const t = trace.data;
  const attrs = useMemo(() => Object.entries(t?.node?.attribute_evidence ?? t?.edge?.attribute_evidence ?? {}), [t]);
  const [field, setField] = useState<string | null>(null);
  useEffect(() => {
    const firstFilled = attrs.find(([, ev]) => ev.value !== null && ev.value !== undefined)?.[0] ?? attrs[0]?.[0] ?? null;
    setField(firstFilled);
  }, [attrs]);

  const label = t?.node?.label ?? (t?.edge ? `${t.edge.src_id} ${t.edge.kind} ${t.edge.dst_id}` : entity);
  const kind = t?.node?.kind ?? t?.edge?.kind ?? null;
  const route = t?.sources?.[0]?.ingestion_route ?? null;
  const ev = attrs.find(([f]) => f === field)?.[1] ?? null;

  const relCols: ColumnDef<EvidenceRelationship, unknown>[] = [
    { id: 'dir', header: 'Direction', accessorFn: (r) => (r.direction === 'outgoing' ? 'out' : 'in'), meta: { className: 'w-20' } },
    { id: 'kind', header: 'Link', accessorFn: (r) => palette.edgeLabel(r.kind) },
    {
      id: 'other',
      header: 'Other end',
      accessorFn: (r) => r.other_label ?? r.other_id,
      cell: ({ row }) => (
        <button type="button" className="text-left text-accent underline decoration-dotted underline-offset-2 hover:decoration-solid" onClick={() => onEntity(row.original.other_id)}>
          {row.original.other_label ?? row.original.other_id}
        </button>
      ),
    },
    {
      id: 'ptr',
      header: 'Evidence for the link',
      accessorFn: (r) => r.sources[0]?.source_id ?? '',
      cell: ({ row }) => (row.original.sources[0] ? <PointerLine pointer={row.original.sources[0]} /> : <span className="text-subtle">none</span>),
      meta: { className: 'hidden md:table-cell' },
    },
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <EntitySearch onPick={onEntity} palette={palette} label="Trace another entity" placeholder="Trace another entity" />
        <span className="text-caption text-muted">
          Tracing <span className="break-all font-mono text-fg" data-testid="engine-trace-id">{entity}</span>
        </span>
      </div>

      {trace.loading ? <SkeletonText lines={6} label="Loading the provenance trace" /> : null}
      {trace.error ? <EngineFailure error={trace.error} onRetry={trace.reload} /> : null}

      {t ? (
        <>
          <Card>
            <CardHeader actions={kind && t.node ? <KindBadge kind={kind} palette={palette} /> : <Badge>{t.entity_type}</Badge>}>
              <CardTitle as="h2">{label}</CardTitle>
              <p className="mt-0.5 text-caption text-muted">
                {t.node?.canonical_id ? <span className="font-mono">{t.node.canonical_id}</span> : null}
                {t.node?.valid_from ? <> · true from {formatDate(t.node.valid_from)}</> : null}
                {t.node?.system_from ? <> · recorded {formatDate(t.node.system_from, true)}</> : null}
              </p>
            </CardHeader>
            <CardBody className="space-y-4">
              {attrs.length ? (
                <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
                  <div role="group" aria-label="Pick a value to trace" className="flex flex-col gap-1">
                    {attrs.map(([f, a]) => (
                      <button
                        key={f}
                        type="button"
                        aria-pressed={f === field}
                        onClick={() => setField(f)}
                        className={cn(
                          'flex min-h-[2rem] items-center justify-between gap-2 rounded-md border px-2.5 py-1 text-left',
                          f === field ? 'border-accent/60 bg-accent/10' : 'border-line hover:bg-raised',
                        )}
                      >
                        <span className="font-mono text-caption text-muted">{f}</span>
                        <span className="min-w-0 truncate text-body-sm tabular-nums text-fg">{a.value === null || a.value === undefined ? '—' : formatValue(a.value)}</span>
                      </button>
                    ))}
                  </div>
                  <div className="min-w-0 space-y-3">
                    {ev && field ? (
                      <>
                        <Chain steps={chainOf(field, ev, label, route)} />
                        <div className="flex flex-wrap items-center gap-2">
                          <VerifyBadge verify={ev.verify} />
                          {ev.verify.detail ? <span className="text-caption text-muted">{fixText(ev.verify.detail)}</span> : null}
                        </div>
                        {ev.capture?.snippet ? (
                          <blockquote className="rounded-md border border-line bg-raised p-2 text-body-sm text-muted">
                            {ev.capture.snippet.before}
                            <mark className="rounded bg-accent/20 px-0.5 text-fg">{ev.capture.snippet.match}</mark>
                            {ev.capture.snippet.after}
                          </blockquote>
                        ) : null}
                        <details className="rounded-md border border-line">
                          <summary className="flex cursor-pointer items-center gap-1.5 px-2.5 py-1.5 text-caption text-muted">
                            <Braces aria-hidden className="h-3.5 w-3.5" /> The re-executable SourcePointer
                          </summary>
                          <pre className="overflow-x-auto border-t border-line p-2.5 font-mono text-caption text-fg">{JSON.stringify(ev.pointer, null, 2)}</pre>
                        </details>
                      </>
                    ) : null}
                  </div>
                </div>
              ) : (
                <p className="flex items-center gap-1.5 text-body-sm text-muted">
                  <CircleSlash aria-hidden className="h-4 w-4" /> This entity has no attribute values; its existence is grounded by the citations below.
                </p>
              )}

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <h3 className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">
                    Entity citations <span className="tabular-nums">({t.sources.length})</span>
                  </h3>
                  <p className="mb-1.5 text-caption text-muted">They ground that this entity exists and its title, not the values above.</p>
                  <ul className="space-y-1.5">
                    {t.sources.map((s, i) => (
                      <li key={i} className="flex flex-wrap items-center gap-2">
                        <PointerLine pointer={s.pointer} />
                        <Badge>{s.ingestion_route}</Badge>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h3 className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">Why this could be shown</h3>
                  <p className="text-body-sm text-fg">
                    <span className="font-mono text-caption text-muted">{t.authorisation.basis}</span>
                  </p>
                  <p className="mt-0.5 text-caption text-muted">{fixText(t.authorisation.why)}</p>
                </div>
              </div>
            </CardBody>
          </Card>

          {t.relationships.length ? (
            <DataTable
              caption={`Relationships of ${label}, each with its own evidence`}
              captionVisible
              columns={relCols}
              data={t.relationships}
              getRowId={(r) => r.id}
              density="compact"
            />
          ) : null}

          <p className="flex items-start gap-1.5 text-caption text-subtle">
            <Route aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              <span className="font-medium text-muted">Stated limitation (from the engine): </span>
              {fixText(t.limitation)}
            </span>
          </p>
        </>
      ) : null}
    </div>
  );
}
