'use client';
import { ExternalLink, History } from 'lucide-react';
import { Badge, PlatformIcon, StatusBadge } from '@/components/ui';
import { cn } from '@/lib/cn';
import type { RequestStatus } from '@/lib/types';

const STATUS: Record<string, { status: 'ok' | 'warn' | 'error' | 'neutral'; label: string }> = {
  released: { status: 'ok', label: 'Released' },
  refused: { status: 'neutral', label: 'Refused' },
  failed: { status: 'error', label: 'Failed' },
  in_progress: { status: 'warn', label: 'In progress' },
};

export function RequestStatusBadge({ status }: { status: RequestStatus | string | null | undefined }) {
  const s = STATUS[status ?? ''] ?? { status: 'neutral' as const, label: status ?? 'Unknown' };
  return (
    <StatusBadge status={s.status} className="text-caption">
      {s.label}
    </StatusBadge>
  );
}

export function ReplayBadge({ show }: { show: boolean | undefined }) {
  if (!show) return null;
  return (
    <Badge tone="neutral" icon={History} title="Seeded demo history: asked through the real pipeline by scripts/seed_demo_activity.py and labelled as replay">
      Seeded replay
    </Badge>
  );
}

/** One document line: platform mark, title (or id), container, and the id in mono. */
export function DocLine({
  docId,
  title,
  platform,
  container,
  href,
  className,
}: {
  docId: string;
  title?: string | null;
  platform?: string | null;
  container?: string | null;
  href?: string | null;
  className?: string;
}) {
  const pf = platform ?? docId.split(':', 1)[0];
  return (
    <div className={cn('flex min-w-0 items-start gap-2', className)}>
      <PlatformIcon platform={pf} showLabel={false} tone="platform" size="sm" className="mt-0.5" />
      <div className="min-w-0">
        <p className="truncate text-body-sm text-fg">
          {href ? (
            <a href={href} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:underline">
              {title || docId}
              <ExternalLink aria-hidden className="h-3 w-3 text-subtle" />
              <span className="sr-only">(opens the source in a new tab)</span>
            </a>
          ) : (
            title || docId
          )}
        </p>
        <p className="truncate text-micro text-subtle">
          {container ? <span>{container} · </span> : null}
          <span className="font-mono">{docId}</span>
        </p>
      </div>
    </div>
  );
}

export function Section({ title, description, children, actions, className, testId }: { title: string; description?: React.ReactNode; children: React.ReactNode; actions?: React.ReactNode; className?: string; testId?: string }) {
  return (
    <section className={cn('min-w-0 rounded-lg border border-line bg-surface', className)} data-testid={testId}>
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-heading-sm text-fg">{title}</h2>
          {description ? <p className="mt-0.5 text-caption text-muted">{description}</p> : null}
        </div>
        {actions}
      </div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}
