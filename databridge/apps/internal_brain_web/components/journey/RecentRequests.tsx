'use client';
import { useRouter } from 'next/navigation';
import { channelLabel } from '@/components/analytics/format';
import { DataTable, type ColumnDef } from '@/components/ui';
import { fmtDate, fmtTime } from '@/lib/format';
import { ReplayBadge } from './bits';

export interface RecentRow {
  req_id: string;
  question: string | null;
  event_time: string | null;
  channel?: string | null;
  sub?: string | null;
  synthetic_replay?: boolean;
}

/** Recent requests; a row opens its journey (keyboard: Tab to a row, Enter). */
export function RecentRequests({ rows, showAsker, caption, maxHeightClass, testId = 'recent-requests' }: { rows: RecentRow[]; showAsker: boolean; caption: string; maxHeightClass?: string; testId?: string }) {
  const router = useRouter();
  const columns: ColumnDef<RecentRow>[] = [
    {
      id: 'when',
      header: 'When',
      accessorFn: (r) => r.event_time ?? '',
      cell: ({ row }) =>
        row.original.event_time ? (
          <span className="flex flex-col whitespace-nowrap tabular-nums sm:flex-row sm:gap-1.5">
            <span className="text-fg">{fmtDate(row.original.event_time)}</span>
            <span className="text-subtle">{fmtTime(row.original.event_time).replace(' SGT', '')}</span>
          </span>
        ) : (
          '—'
        ),
      meta: { className: 'w-[6.5rem] sm:w-[10.5rem]' },
    },
    ...(showAsker
      ? [
          {
            id: 'sub',
            header: 'Asked by',
            accessorFn: (r: RecentRow) => r.sub ?? '',
            cell: ({ row }: { row: { original: RecentRow } }) => <span className="font-mono text-caption">{row.original.sub ?? '—'}</span>,
            meta: { className: 'hidden md:table-cell w-[9rem]' },
          } as ColumnDef<RecentRow>,
        ]
      : []),
    {
      id: 'question',
      header: 'Question',
      accessorFn: (r) => r.question ?? '',
      cell: ({ row }) => (
        <span className="flex min-w-0 flex-col gap-1">
          <span className="line-clamp-2 text-fg">{row.original.question || <span className="text-subtle">Question not recorded</span>}</span>
          <span className="font-mono text-micro text-subtle">{row.original.req_id}</span>
        </span>
      ),
      enableSorting: false,
    },
    {
      id: 'channel',
      header: 'Channel',
      accessorFn: (r) => r.channel ?? '',
      cell: ({ row }) => (
        <span className="flex flex-col items-start gap-1">
          <span className="whitespace-nowrap text-muted">{channelLabel(row.original.channel)}</span>
          {row.original.synthetic_replay && row.original.channel !== 'replay' ? <ReplayBadge show /> : null}
        </span>
      ),
      meta: { className: 'hidden sm:table-cell w-[9rem]' },
    },
  ];
  return (
    <DataTable
      columns={columns}
      data={rows}
      caption={caption}
      getRowId={(r) => r.req_id}
      onRowClick={(r) => router.push(`/dev/journey/${encodeURIComponent(r.req_id)}`)}
      rowLabel={(r) => `Open the journey of: ${r.question ?? r.req_id}`}
      density="compact"
      maxHeightClass={maxHeightClass}
      initialSorting={[{ id: 'when', desc: true }]}
      empty="No requests yet. Ask a question in User mode and it appears here."
      testId={testId}
    />
  );
}
