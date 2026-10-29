'use client';
// The audit results: one row per request, dense, newest first. Datadog Audit Trail layout: time,
// person, action, object, decision, and the request's last sealed record hash.
import { History, PanelRightOpen, Sparkle } from 'lucide-react';
import { useMemo } from 'react';
import { Badge, CodeHash, DataTable, type ColumnDef } from '@/components/ui';
import { fmtDateTime } from '@/lib/format';
import { auditMessages } from '@/lib/i18n/audit';
import { useMessages } from '@/lib/i18n';
import type { AuditRow } from '@/lib/types';
import { CHANNEL, DecisionBadge, OutcomeBadge, decisionsDenyFirst } from './shared';

const stop = (e: React.SyntheticEvent) => e.stopPropagation();

export function AuditTable({ rows, onOpen, openId, latestId }: { rows: AuditRow[]; onOpen: (r: AuditRow) => void; openId?: string | null; latestId?: string | null }) {
  const t = useMessages(auditMessages);
  const columns = useMemo<ColumnDef<AuditRow, unknown>[]>(
    () => [
      {
        id: 'time',
        accessorKey: 'event_time',
        header: t('audit.table.time'),
        meta: { className: 'whitespace-nowrap' },
        cell: ({ row }) => (
          <div className="tabular-nums">
            <div>{fmtDateTime(row.original.event_time)}</div>
            {latestId && row.original.req_id === latestId ? (
              <Badge className="mt-1" tone="accent" icon={Sparkle} data-latest="" title="The newest request in the ledger">
                Newest
              </Badge>
            ) : null}
            {row.original.synthetic_replay ? (
              <Badge className="mt-1" icon={History} title="Seeded history: synthetic questions replayed through the real pipeline">
                {t('audit.table.replay')}
              </Badge>
            ) : null}
          </div>
        ),
      },
      {
        id: 'person',
        accessorKey: 'sub',
        header: t('audit.table.person'),
        enableSorting: false,
        meta: { className: 'whitespace-nowrap font-mono text-xs' },
        cell: ({ row }) => row.original.sub,
      },
      {
        id: 'action',
        header: t('audit.table.action'),
        enableSorting: false,
        meta: { className: 'whitespace-nowrap' },
        cell: ({ row }) => (
          <div>
            <div className="font-medium">{t('audit.table.asked')}</div>
            <div className="text-2xs text-subtle">via {CHANNEL[row.original.channel ?? ''] ?? row.original.channel ?? '—'}</div>
          </div>
        ),
      },
      {
        id: 'object',
        header: t('audit.table.object'),
        enableSorting: false,
        meta: { className: 'min-w-[11rem] max-w-[22rem]' },
        cell: ({ row }) => <span className="line-clamp-2">{row.original.question ?? '—'}</span>,
      },
      {
        id: 'decision',
        header: t('audit.table.decision'),
        enableSorting: false,
        meta: { className: 'min-w-[10rem]' },
        cell: ({ row }) => {
          const d = decisionsDenyFirst(row.original);
          return (
            <div className="space-y-1">
              <OutcomeBadge e={row.original} />
              {d.length ? (
                <ul className="space-y-0.5">
                  {d.slice(0, 3).map((x) => (
                    <li key={x.doc_id}>
                      <DecisionBadge d={x} />
                    </li>
                  ))}
                  {d.length > 3 ? <li className="text-2xs text-subtle">+{d.length - 3} more</li> : null}
                </ul>
              ) : null}
            </div>
          );
        },
      },
      {
        id: 'hash',
        header: t('audit.table.hash'),
        enableSorting: false,
        meta: { className: 'whitespace-nowrap' },
        cell: ({ row }) => {
          const last = row.original.records?.[row.original.records.length - 1];
          return last ? (
            // the copy button must not also open the row
            <span className="inline-flex flex-col items-start gap-0.5" onClick={stop} onKeyDown={stop}>
              <CodeHash value={last.hash} head={8} tail={4} label={`record ${last.seq} hash`} />
              <span className="font-mono text-2xs text-subtle tabular-nums">
                #{last.seq} · {last.kind}
              </span>
            </span>
          ) : (
            <span className="font-mono text-xs text-subtle">—</span>
          );
        },
      },
      {
        id: 'details',
        header: () => <span className="sr-only">{t('audit.table.details')}</span>,
        enableSorting: false,
        meta: { className: 'w-10 pl-0 text-right' },
        cell: ({ row }) => (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpen(row.original);
            }}
            onKeyDown={stop}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-line-strong text-muted hover:bg-raised hover:text-fg"
            aria-label={`Details for request ${row.original.req_id} by ${row.original.sub}`}
            title={t('audit.table.details')}
          >
            <PanelRightOpen aria-hidden className="h-3.5 w-3.5" />
          </button>
        ),
      },
    ],
    [t, onOpen, latestId],
  );

  return (
    <DataTable
      columns={columns}
      data={rows}
      caption={t('audit.table.caption')}
      getRowId={(r) => r.req_id}
      onRowClick={onOpen}
      rowLabel={(r) => `${fmtDateTime(r.event_time)}, ${r.sub}: ${r.question ?? r.req_id}`}
      isRowSelected={(r) => r.req_id === openId || (!!latestId && r.req_id === latestId)}
      density="compact"
      maxHeightClass="max-h-[34rem]"
      testId="audit-table"
      empty={t('audit.table.empty')}
    />
  );
}
