'use client';
// DataTable on TanStack Table (headless): sortable headers (buttons with aria-sort), optional
// keyboard-activatable rows (Tab to the table, ArrowUp/ArrowDown between rows, Enter/Space opens),
// a caption for the accessible name, sticky header, and an empty state.
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type Row,
  type SortingState,
} from '@tanstack/react-table';
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react';
import { useRef, useState } from 'react';
import { cn } from '@/lib/cn';

export type { ColumnDef, SortingState };

declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    /** Right-align and use tabular numerals (numbers, durations, counts). */
    numeric?: boolean;
    /** Extra classes for the th and td of this column (e.g. widths, "hidden sm:table-cell"). */
    className?: string;
  }
}

export interface DataTableProps<T> {
  columns: ColumnDef<T, any>[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  data: T[];
  /** Accessible name of the table (rendered as a caption; visually hidden unless captionVisible). */
  caption: string;
  captionVisible?: boolean;
  /** Stable id per row (defaults to index). */
  getRowId?: (row: T, index: number) => string;
  /** Makes rows interactive. Also give `rowLabel` so each row has a name for screen readers. */
  onRowClick?: (row: T) => void;
  rowLabel?: (row: T) => string;
  /** Highlight a row (e.g. the open one). */
  isRowSelected?: (row: T) => boolean;
  initialSorting?: SortingState;
  /** Shown in place of the body when there are no rows. */
  empty?: React.ReactNode;
  density?: 'compact' | 'normal';
  /** Max height with a scrolling body and sticky header, e.g. "max-h-[480px]". */
  maxHeightClass?: string;
  className?: string;
  testId?: string;
}

export function DataTable<T>({
  columns,
  data,
  caption,
  captionVisible,
  getRowId,
  onRowClick,
  rowLabel,
  isRowSelected,
  initialSorting = [],
  empty,
  density = 'normal',
  maxHeightClass,
  className,
  testId,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: getRowId ? (r, i) => getRowId(r, i) : undefined,
  });
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const cellY = density === 'compact' ? 'py-1.5' : 'py-2.5';

  const focusRow = (from: HTMLElement, dir: 1 | -1) => {
    const rows = Array.from(bodyRef.current?.querySelectorAll<HTMLElement>('tr[data-row]') ?? []);
    const i = rows.indexOf(from);
    rows[Math.min(rows.length - 1, Math.max(0, i + dir))]?.focus();
  };

  const onKey = (e: React.KeyboardEvent<HTMLTableRowElement>, row: Row<T>) => {
    if (e.target !== e.currentTarget) return; // a control inside the row has focus
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onRowClick?.(row.original);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      focusRow(e.currentTarget, 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      focusRow(e.currentTarget, -1);
    }
  };

  const rows = table.getRowModel().rows;
  return (
    <div
      className={cn('ds-scroll relative w-full overflow-auto rounded-lg border border-line bg-surface', maxHeightClass, className)}
      data-testid={testId}
      // keyboard users must be able to scroll a scrollable region
      tabIndex={maxHeightClass ? 0 : undefined}
      role={maxHeightClass ? 'region' : undefined}
      aria-label={maxHeightClass ? caption : undefined}
    >
      <table className="w-full border-collapse text-left text-body-sm">
        <caption className={cn(captionVisible ? 'px-3 py-2 text-left text-caption text-muted' : 'sr-only')}>{caption}</caption>
        <thead className="sticky top-0 z-[1] bg-surface">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-line">
              {hg.headers.map((h) => {
                const meta = h.column.columnDef.meta;
                const sortable = h.column.getCanSort();
                const dir = h.column.getIsSorted();
                return (
                  <th
                    key={h.id}
                    scope="col"
                    aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : sortable ? 'none' : undefined}
                    className={cn('whitespace-nowrap px-3 py-2 text-micro font-semibold uppercase tracking-wide text-subtle', meta?.numeric && 'text-right', meta?.className)}
                  >
                    {h.isPlaceholder ? null : sortable ? (
                      <button
                        type="button"
                        onClick={h.column.getToggleSortingHandler()}
                        className={cn('-mx-1 inline-flex min-h-[1.5rem] items-center gap-1 rounded px-1 uppercase hover:text-fg', meta?.numeric && 'flex-row-reverse')}
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {dir === 'asc' ? (
                          <ArrowUp aria-hidden className="h-3 w-3" />
                        ) : dir === 'desc' ? (
                          <ArrowDown aria-hidden className="h-3 w-3" />
                        ) : (
                          <ChevronsUpDown aria-hidden className="h-3 w-3 opacity-60" />
                        )}
                      </button>
                    ) : (
                      flexRender(h.column.columnDef.header, h.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody ref={bodyRef}>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={table.getAllLeafColumns().length} className="px-3 py-8 text-center text-sm text-muted">
                {empty ?? 'Nothing to show.'}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const selected = isRowSelected?.(row.original) ?? false;
              const interactive = !!onRowClick;
              return (
                <tr
                  key={row.id}
                  data-row
                  data-selected={selected || undefined}
                  tabIndex={interactive ? 0 : undefined}
                  aria-label={interactive && rowLabel ? rowLabel(row.original) : undefined}
                  aria-current={selected ? 'true' : undefined}
                  onClick={interactive ? () => onRowClick(row.original) : undefined}
                  onKeyDown={interactive ? (e) => onKey(e, row) : undefined}
                  className={cn(
                    'border-b border-line last:border-b-0',
                    interactive && 'cursor-pointer hover:bg-raised/60 focus-visible:bg-raised/60 focus-visible:outline-offset-[-2px]',
                    selected && 'bg-accent/[0.07] shadow-[inset_2px_0_0_rgb(var(--accent))]',
                  )}
                >
                  {row.getVisibleCells().map((cell) => {
                    const meta = cell.column.columnDef.meta;
                    return (
                      <td key={cell.id} className={cn('px-3 align-top text-fg', cellY, meta?.numeric && 'text-right tabular-nums', meta?.className)}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    );
                  })}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
