'use client';
// Data display: SectionHeader, EmptyState, KpiTile, Stat, CodeHash, PermissionBadge, StatusBadge.
// Every value shown here comes from the backend (PLAN rule 5): these components format, never compute.
import { ArrowDownRight, ArrowUpRight, Check, CircleAlert, CircleCheck, Copy, Lock, Minus, ShieldCheck, ShieldHalf, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/cn';
import { Badge } from './badge';

/* ---------------------------------------------------------------- SectionHeader */

/** Page or section title row: eyebrow, title, description, actions. `as` sets the heading level. */
export function SectionHeader({
  title,
  description,
  eyebrow,
  actions,
  as: Tag = 'h2',
  id,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  eyebrow?: React.ReactNode;
  actions?: React.ReactNode;
  as?: 'h1' | 'h2' | 'h3';
  id?: string;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-wrap items-end justify-between gap-x-4 gap-y-2', className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="mb-1 text-micro font-semibold uppercase tracking-wider text-subtle">{eyebrow}</p> : null}
        <Tag id={id} className={cn('text-fg', Tag === 'h1' ? 'text-title' : Tag === 'h2' ? 'text-heading' : 'text-heading-sm')}>
          {title}
        </Tag>
        {description ? <p className="mt-1 max-w-prose text-body-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/* ---------------------------------------------------------------- EmptyState */

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
  compact,
}: {
  icon?: LucideIcon;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center rounded-lg border border-dashed border-line-strong text-center', compact ? 'px-4 py-6' : 'px-6 py-14', className)}>
      {Icon ? (
        <span className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-raised text-muted">
          <Icon aria-hidden className="h-5 w-5" />
        </span>
      ) : null}
      <p className="text-heading-sm text-fg">{title}</p>
      {description ? <p className="mt-1 max-w-md text-body-sm text-muted">{description}</p> : null}
      {action ? <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}

/* ---------------------------------------------------------------- KpiTile / Stat */

export interface Delta {
  /** Display text, already formatted by the caller from a backend figure ("+12", "−3.1%"). */
  text: string;
  direction: 'up' | 'down' | 'flat';
  /** Whether this direction is good, bad or neutral (sets the tone; the arrow + text carry meaning). */
  sentiment?: 'good' | 'bad' | 'neutral';
  /** Comparison context for screen readers, e.g. "vs previous 7 days". */
  context?: string;
}

function DeltaChip({ delta }: { delta: Delta }) {
  const Icon = delta.direction === 'up' ? ArrowUpRight : delta.direction === 'down' ? ArrowDownRight : Minus;
  const tone = delta.sentiment === 'good' ? 'text-ok' : delta.sentiment === 'bad' ? 'text-bad' : 'text-muted';
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-x-0.5 text-caption font-medium tabular-nums', tone)}>
      <Icon aria-hidden className="h-3.5 w-3.5" />
      {delta.text}
      {delta.context ? <span className="ml-1 font-normal text-subtle">{delta.context}</span> : null}
    </span>
  );
}

/**
 * Metric tile: label, exact value (tabular numerals), optional unit, delta, footnote (provenance)
 * and a sparkline slot (pass a small Recharts chart or an SVG; it is decorative unless labelled).
 */
export function KpiTile({
  label,
  value,
  unit,
  delta,
  footnote,
  sparkline,
  icon: Icon,
  loading,
  className,
  testId,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  delta?: Delta;
  footnote?: React.ReactNode;
  sparkline?: React.ReactNode;
  icon?: LucideIcon;
  loading?: boolean;
  className?: string;
  testId?: string;
}) {
  return (
    <div className={cn('flex min-w-0 flex-col rounded-lg border border-line bg-surface px-4 py-3', className)} data-testid={testId}>
      <div className="flex items-center gap-1.5 text-caption font-medium text-muted">
        {Icon ? <Icon aria-hidden className="h-3.5 w-3.5" /> : null}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1.5 flex items-end justify-between gap-3">
        <div className="min-w-0 flex-1">
          {loading ? (
            <span className="ds-skeleton block h-7 w-20 rounded bg-raised" aria-label="Loading" role="status" />
          ) : (
            <p className="truncate text-kpi tabular-nums text-fg">
              {value}
              {unit ? <span className="ml-1 text-sm font-medium text-muted">{unit}</span> : null}
            </p>
          )}
        </div>
        {sparkline ? <div className="h-9 w-24 shrink-0 text-accent">{sparkline}</div> : null}
      </div>
      {delta && !loading ? (
        <div className="mt-1">
          <DeltaChip delta={delta} />
        </div>
      ) : null}
      {footnote ? <p className="mt-2 border-t border-line pt-2 text-micro text-subtle">{footnote}</p> : null}
    </div>
  );
}

/** Inline label/value pair for dense panels (definition-list semantics). */
export function Stat({ label, value, mono, className }: { label: string; value: React.ReactNode; mono?: boolean; className?: string }) {
  return (
    <div className={cn('min-w-0', className)}>
      <dt className="text-micro font-medium uppercase tracking-wide text-subtle">{label}</dt>
      <dd className={cn('mt-0.5 truncate text-sm tabular-nums text-fg', mono && 'font-mono text-xs')}>{value}</dd>
    </div>
  );
}

/** Wrap Stats in this so the dt/dd pairs are valid HTML. */
export function StatGrid({ children, className, cols = 3 }: { children: React.ReactNode; className?: string; cols?: 2 | 3 | 4 }) {
  return (
    <dl className={cn('grid gap-x-6 gap-y-3', cols === 2 && 'grid-cols-2', cols === 3 && 'grid-cols-2 sm:grid-cols-3', cols === 4 && 'grid-cols-2 sm:grid-cols-4', className)}>
      {children}
    </dl>
  );
}

/* ---------------------------------------------------------------- CodeHash */

/** Monospace truncated hash/id with a copy button. The full value is in the title and the sr text. */
export function CodeHash({ value, head = 8, tail = 6, label = 'hash', className }: { value: string | null | undefined; head?: number; tail?: number; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  if (!value) return <span className={cn('font-mono text-xs text-subtle', className)}>—</span>;
  const short = value.length > head + tail + 1 ? `${value.slice(0, head)}…${value.slice(-tail)}` : value;
  return (
    <span className={cn('inline-flex max-w-full items-center gap-1 rounded border border-line bg-raised pl-1.5 font-mono text-xs text-fg', className)}>
      <span title={value} className="truncate">
        <span aria-hidden>{short}</span>
        <span className="sr-only">{value}</span>
      </span>
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            clearTimeout(timer.current);
            timer.current = setTimeout(() => setCopied(false), 1200);
          } catch {
            /* clipboard blocked: the value is still selectable in the title */
          }
        }}
        aria-label={copied ? `Copied ${label}` : `Copy ${label}`}
        title={copied ? 'Copied' : `Copy ${label}`}
        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-r text-muted hover:bg-line hover:text-fg"
      >
        {copied ? <Check aria-hidden className="h-3.5 w-3.5 text-ok" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
      </button>
      <span className="sr-only" aria-live="polite">
        {copied ? 'Copied' : ''}
      </span>
    </span>
  );
}

/* ---------------------------------------------------------------- PermissionBadge / StatusBadge */

export type PermissionState = 'full' | 'partial' | 'locked';

const PERM: Record<PermissionState, { icon: LucideIcon; label: string; cls: string }> = {
  full: { icon: ShieldCheck, label: 'Can open', cls: 'border-perm-full/40 bg-perm-full/10 text-perm-full' },
  partial: { icon: ShieldHalf, label: 'Partly visible', cls: 'border-perm-partial/40 bg-perm-partial/10 text-perm-partial' },
  locked: { icon: Lock, label: 'Locked', cls: 'border-line-strong bg-raised text-perm-locked' },
};

/**
 * Permission state for DEVELOPER views (graph "View as", trust centre). Never use it in User mode
 * to hint that something hidden exists (PLAN rule 4, D-07).
 */
export function PermissionBadge({ state, label, className }: { state: PermissionState; label?: string; className?: string }) {
  const p = PERM[state];
  const Icon = p.icon;
  return (
    <span className={cn('inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-2xs font-medium', p.cls, className)} data-perm={state}>
      <Icon aria-hidden className="h-3 w-3" />
      {label ?? p.label}
    </span>
  );
}

export type Status = 'ok' | 'warn' | 'error' | 'neutral';
const STATUS_ICON: Record<Status, LucideIcon> = { ok: CircleCheck, warn: TriangleAlert, error: CircleAlert, neutral: Minus };

/** ok / warn / error with icon + text (never colour alone). */
export function StatusBadge({ status, children, className }: { status: Status; children: React.ReactNode; className?: string }) {
  const tone = status === 'error' ? 'bad' : status;
  return (
    <Badge tone={tone} icon={STATUS_ICON[status]} className={className}>
      {children}
    </Badge>
  );
}
