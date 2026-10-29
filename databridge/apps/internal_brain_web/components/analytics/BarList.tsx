'use client';
import { cn } from '@/lib/cn';
import { int } from './format';

export interface BarRow {
  key: string;
  label: React.ReactNode;
  /** Plain-text name for screen readers and the hover title. */
  name: string;
  value: number | null;
  /** Secondary figure on the right, e.g. "86.0% of considered". */
  note?: React.ReactNode;
  /** Tailwind background class or a CSS colour for the bar. */
  fill?: string;
  /** Indent level (document funnel sub-steps). */
  indent?: boolean;
}

/**
 * Horizontal bar list (Tremor-style, by hand): exact numbers, right-aligned, tabular; the bar is
 * decorative (aria-hidden) and the list reads as text. Bars are proportional to `max` (default:
 * the largest value), anchored at zero.
 */
export function BarList({ rows, max, className, valueWidth = 'w-16', testId }: { rows: BarRow[]; max?: number; className?: string; valueWidth?: string; testId?: string }) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value ?? 0));
  return (
    <ul className={cn('space-y-1.5', className)} data-testid={testId}>
      {rows.map((r) => {
        const w = r.value && top > 0 ? Math.max(0.6, (r.value / top) * 100) : 0;
        const isClass = !r.fill || !/^(rgb|#|var)/.test(r.fill);
        return (
          <li key={r.key} className={cn('group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3', r.indent && 'pl-4')} title={`${r.name}: ${int(r.value)}`}>
            <div className="relative h-7 min-w-0 overflow-hidden rounded">
              <div
                aria-hidden
                className={cn('absolute inset-y-0 left-0 rounded opacity-25 transition-opacity duration-fast group-hover:opacity-40', isClass ? r.fill ?? 'bg-accent' : '')}
                style={{ width: `${w}%`, background: isClass ? undefined : r.fill }}
              />
              <div
                aria-hidden
                className={cn('absolute inset-y-0 left-0 w-[3px] rounded-l', isClass ? r.fill ?? 'bg-accent' : '')}
                style={{ background: isClass ? undefined : r.fill, display: w ? undefined : 'none' }}
              />
              <span className="relative flex h-full min-w-0 items-center gap-1.5 truncate px-2.5 text-body-sm text-fg">{r.label}</span>
            </div>
            <div className="flex items-baseline justify-end gap-2 text-right">
              {r.note ? <span className="hidden whitespace-nowrap text-caption tabular-nums text-subtle sm:inline">{r.note}</span> : null}
              <span className={cn('text-body-sm font-medium tabular-nums text-fg', valueWidth)}>{int(r.value)}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Thin inline meter for table cells (value against the column maximum). Decorative. */
export function MiniBar({ value, max, fill = 'bg-accent', className }: { value: number | null | undefined; max: number; fill?: string; className?: string }) {
  const w = value && max > 0 ? Math.max(2, (value / max) * 100) : 0;
  const isClass = !/^(rgb|#|var)/.test(fill);
  return (
    <span aria-hidden className={cn('relative inline-block h-1.5 w-16 overflow-hidden rounded-full bg-raised align-middle', className)}>
      <span className={cn('absolute inset-y-0 left-0 rounded-full', isClass ? fill : '')} style={{ width: `${w}%`, background: isClass ? undefined : fill }} />
    </span>
  );
}
