'use client';
import { ArrowRight, CircleCheck, CircleSlash, Database, KeyRound } from 'lucide-react';
import { Badge, PlatformIcon, Skeleton, StatusBadge } from '@/components/ui';
import { cn } from '@/lib/cn';
import { fmtAgo, fmtDuration, PLATFORM_LABEL } from '@/lib/format';
import type { IbStatus, SourceStatus } from '@/lib/types';
import { int } from './format';

/** Per-platform freshness: lag since the last sync against the SLO (from /ib/sources). */
export function Freshness({ sources, now }: { sources: SourceStatus[] | null; now: number }) {
  if (!sources) {
    return (
      <div role="status" className="space-y-2">
        <span className="sr-only">Loading source freshness</span>
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-9" />
        ))}
      </div>
    );
  }
  if (!sources.length) return <p className="text-body-sm text-muted">No sources reported.</p>;
  return (
    <ul className="divide-y divide-line" data-testid="freshness">
      {sources.map((s) => {
        const age = s.last_sync_at ? Math.max(0, Math.round((now - Date.parse(s.last_sync_at)) / 1000)) : null;
        const lag = typeof s.lag_s === 'number' ? s.lag_s : age;
        const within = s.within_slo ?? (lag !== null ? lag <= s.slo_s : null);
        const w = lag !== null && s.slo_s > 0 ? Math.min(100, (lag / (s.slo_s * 2)) * 100) : 0;
        return (
          <li key={s.platform} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-2 sm:grid-cols-[9rem_minmax(0,1fr)_auto]">
            <PlatformIcon platform={String(s.platform)} tone="platform" className="text-body-sm text-fg" />
            <div aria-hidden className="relative order-3 col-span-2 h-1.5 rounded-full bg-raised sm:order-none sm:col-span-1">
              <span className={cn('absolute inset-y-0 left-0 rounded-full', within === false ? 'bg-warn' : 'bg-accent/70')} style={{ width: `${w}%` }} />
              <span className="absolute -inset-y-1 left-1/2 w-px bg-fg/60" title="SLO" />
            </div>
            <div className="flex items-center justify-end gap-2 text-right">
              <span className="text-caption tabular-nums text-muted">
                {s.last_sync_at ? fmtAgo(s.last_sync_at, now) : 'never'} <span className="text-subtle">/ SLO {fmtDuration(s.slo_s)}</span>
              </span>
              {within === null ? (
                <StatusBadge status="neutral">Unknown</StatusBadge>
              ) : within ? (
                <StatusBadge status="ok">In SLO</StatusBadge>
              ) : (
                <StatusBadge status="warn">Behind</StatusBadge>
              )}
            </div>
            <span className="sr-only">
              {PLATFORM_LABEL[String(s.platform)] ?? s.platform}: last sync {s.last_sync_at ? fmtAgo(s.last_sync_at, now) : 'never'}, SLO {fmtDuration(s.slo_s)}, {within ? 'within SLO' : 'behind SLO'}
              {s.state ? `, mode ${s.state}` : ''}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

const MODEL_LABEL: Record<string, string> = { hy3: 'Hy3', 'deepseek-v4.1-flash': 'DeepSeek V4.1 Flash' };

/** The model chain and ledger health from /ib/status. Unknown fields render as "not reported". */
export function ModelStatus({ status, unavailable }: { status: IbStatus | null; unavailable?: boolean }) {
  if (unavailable) return <p className="text-body-sm text-muted">Model status is not reported by this backend.</p>;
  if (!status) {
    return (
      <div role="status" className="space-y-2">
        <span className="sr-only">Loading model status</span>
        <Skeleton className="h-8" />
        <Skeleton className="h-16" />
      </div>
    );
  }
  const chain = status.models?.active_chain?.length ? status.models.active_chain : status.models?.chain ?? [];
  const fb = status.models?.fallback;
  return (
    <div className="space-y-3" data-testid="model-status">
      <div className="flex flex-wrap items-center gap-2">
        {status.models?.active ? (
          <StatusBadge status="ok">Live models</StatusBadge>
        ) : (
          <StatusBadge status="warn">Extractive only</StatusBadge>
        )}
        {status.force_offline ? <Badge tone="warn">Forced offline</Badge> : null}
        <Badge tone={status.key_configured ? 'neutral' : 'warn'} icon={KeyRound}>
          {status.key_configured ? 'Key configured' : 'No key'}
        </Badge>
        {status.ledger?.ok === true ? (
          <Badge tone="ok" icon={Database}>
            Ledger verified
          </Badge>
        ) : status.ledger?.ok === false ? (
          <Badge tone="bad" icon={Database}>
            Ledger check failed
          </Badge>
        ) : (
          <Badge tone="neutral" icon={Database}>
            Ledger not checked
          </Badge>
        )}
      </div>
      <ol className="flex flex-wrap items-center gap-1.5" aria-label="Model chain, in order of use">
        {chain.map((m) => (
          <li key={m} className="flex items-center gap-1.5">
            <span className={cn('inline-flex flex-col rounded-md border px-2.5 py-1.5', status.models?.active ? 'border-line-strong bg-surface' : 'border-dashed border-line bg-bg/40 text-subtle')}>
              <span className="text-body-sm font-medium text-fg">{MODEL_LABEL[m] ?? m}</span>
              <span className="font-mono text-micro text-subtle">
                {status.models?.provider ?? '—'} · {m}
              </span>
            </span>
            <ArrowRight aria-hidden className="h-3.5 w-3.5 text-subtle" />
          </li>
        ))}
        {fb ? (
          <li className="inline-flex flex-col rounded-md border border-line-strong bg-surface px-2.5 py-1.5">
            <span className="text-body-sm font-medium text-fg">Extractive (no model)</span>
            <span className="font-mono text-micro text-subtle">
              {fb.provider} · {fb.model}
            </span>
          </li>
        ) : null}
      </ol>
      <p className="flex items-start gap-1.5 text-caption text-muted">
        {status.models?.active ? <CircleCheck aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" /> : <CircleSlash aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />}
        {status.models?.active
          ? `Answers come from ${int(chain.length)} model${chain.length === 1 ? '' : 's'} in order, then the deterministic quote if all fail. Every answer is labelled with its model.`
          : 'No model is active: every answer is the deterministic extractive quote of permitted passages, labelled as such.'}
      </p>
    </div>
  );
}
