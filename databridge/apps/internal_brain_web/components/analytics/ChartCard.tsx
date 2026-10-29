'use client';
import { BarChart3, Table2 } from 'lucide-react';
import { useId, useState } from 'react';
import { Button, Card } from '@/components/ui';
import { cn } from '@/lib/cn';

/** Small provenance chip: where a figure came from ("ledger · 30 d", "eval · 14 Oct"). */
export function SourceChip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn('inline-flex items-center whitespace-nowrap rounded border border-line bg-raised px-1.5 py-px font-mono text-[0.6875rem] leading-4 text-subtle', className)}>
      {children}
    </span>
  );
}

/**
 * A chart panel: a title that states the takeaway, a provenance chip, the chart, and a
 * "View as table" toggle that swaps in an accessible table with the same numbers.
 */
export function ChartCard({
  title,
  takeaway,
  source,
  table,
  children,
  className,
  bodyClassName,
  actions,
  testId,
}: {
  title: string;
  takeaway?: React.ReactNode;
  source?: React.ReactNode;
  /** The same data as a table. When given, a toggle appears. */
  table?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  actions?: React.ReactNode;
  testId?: string;
}) {
  const [asTable, setAsTable] = useState(false);
  const id = useId();
  return (
    <Card className={cn('flex min-w-0 flex-col', className)} aria-labelledby={`${id}-t`} data-testid={testId}>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1 basis-56">
          <h2 id={`${id}-t`} className="text-heading-sm text-fg">
            {title}
          </h2>
          {takeaway ? <p className="mt-0.5 text-caption text-muted">{takeaway}</p> : null}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {source ? <SourceChip>{source}</SourceChip> : null}
          {actions}
          {table ? (
            <Button
              size="xs"
              variant="ghost"
              icon={asTable ? BarChart3 : Table2}
              aria-pressed={asTable}
              onClick={() => setAsTable((v) => !v)}
              data-testid="view-as-table"
            >
              {asTable ? 'View as chart' : 'View as table'}
            </Button>
          ) : null}
        </div>
      </div>
      <div className={cn('min-w-0 flex-1 px-4 py-3', bodyClassName)}>{asTable && table ? table : children}</div>
    </Card>
  );
}

/** A compact, right-aligned numeric table used as the accessible twin of every chart. */
export function MiniTable({
  caption,
  head,
  rows,
  numeric,
}: {
  caption: string;
  head: string[];
  rows: (React.ReactNode | string | number)[][];
  /** Column indexes that hold numbers (right-aligned, tabular). Defaults to every column but the first. */
  numeric?: number[];
}) {
  const isNum = (i: number) => (numeric ? numeric.includes(i) : i > 0);
  return (
    // focusable, so a keyboard user can scroll a table that is wider than a phone
    <div className="ds-scroll relative max-w-full overflow-x-auto" tabIndex={0} role="region" aria-label={caption}>
      <table className="w-full border-collapse text-left text-body-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line">
            {head.map((h, i) => (
              <th key={h + i} scope="col" className={cn('whitespace-nowrap px-2 py-1.5 text-micro font-semibold uppercase tracking-wide text-subtle', isNum(i) && 'text-right')}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className="border-b border-line last:border-b-0">
              {r.map((c, ci) =>
                ci === 0 ? (
                  <th key={ci} scope="row" className="px-2 py-1.5 text-left font-normal text-fg">
                    {c}
                  </th>
                ) : (
                  <td key={ci} className={cn('px-2 py-1.5 text-fg', isNum(ci) && 'text-right tabular-nums')}>
                    {c}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Skeleton for a chart panel while its data loads (matches the final layout). */
export function ChartSkeleton({ className, rows = 5 }: { className?: string; rows?: number }) {
  return (
    <Card className={cn('p-4', className)}>
      <div role="status" className="space-y-3">
        <span className="sr-only">Loading chart</span>
        <div aria-hidden className="ds-skeleton h-4 w-40 rounded bg-raised" />
        <div aria-hidden className="ds-skeleton h-3 w-64 max-w-full rounded bg-raised" />
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} aria-hidden className="ds-skeleton h-5 rounded bg-raised" style={{ width: `${90 - i * 12}%` }} />
        ))}
      </div>
    </Card>
  );
}
