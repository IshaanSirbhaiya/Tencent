'use client';
// Small pieces shared by the explorer: reduced-motion hook, copy-id button, map controls, legend.
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, Copy, Maximize, Minus, Plus } from 'lucide-react';
import { useEffect, useState } from 'react';
import { IconButton } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import type { Platform } from '@/lib/types';
import { fmtCount, platformName } from './format';

/** prefers-reduced-motion OR the app's own Reduce motion setting (html[data-motion="reduce"]). */
export function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const read = () => setReduce(mq.matches || document.documentElement.getAttribute('data-motion') === 'reduce');
    read();
    mq.addEventListener('change', read);
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-motion'] });
    return () => {
      mq.removeEventListener('change', read);
      mo.disconnect();
    };
  }, []);
  return reduce;
}

export function CopyId({ value, label, className }: { value: string; label?: string; className?: string }) {
  const t = useMessages(graphMessages);
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!done) return;
    const id = setTimeout(() => setDone(false), 1400);
    return () => clearTimeout(id);
  }, [done]);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(value).then(
          () => setDone(true),
          () => setDone(false),
        );
      }}
      className={cn(
        'inline-flex h-6 shrink-0 items-center gap-1 rounded border border-line px-1.5 text-caption text-muted transition-colors duration-fast hover:border-line-strong hover:text-fg',
        className,
      )}
      aria-label={`${label ?? t('panel.copy')}: ${value}`}
      title={value}
    >
      {done ? <Check aria-hidden className="h-3 w-3 text-ok" /> : <Copy aria-hidden className="h-3 w-3" />}
      <span aria-live="polite">{done ? t('panel.copied') : t('panel.copy')}</span>
    </button>
  );
}

export function MapControls({
  onZoom,
  onFit,
  onPan,
  compact,
}: {
  onZoom: (d: 1 | -1) => void;
  onFit: () => void;
  onPan: (dx: number, dy: number) => void;
  compact?: boolean;
}) {
  const t = useMessages(graphMessages);
  const btn = 'bg-surface/95 border border-line';
  return (
    <div className="pointer-events-auto flex flex-col items-end gap-1.5" role="group" aria-label="Map controls">
      <div className="flex flex-col overflow-hidden rounded-md border border-line bg-surface/95">
        <IconButton icon={Plus} label={t('ctl.zoomIn')} size="sm" onClick={() => onZoom(1)} className="rounded-none" />
        <span aria-hidden className="h-px bg-line" />
        <IconButton icon={Minus} label={t('ctl.zoomOut')} size="sm" onClick={() => onZoom(-1)} className="rounded-none" />
        <span aria-hidden className="h-px bg-line" />
        <IconButton icon={Maximize} label={t('ctl.fit')} size="sm" onClick={onFit} className="rounded-none" data-testid="graph-fit" />
      </div>
      {compact ? null : (
        <div className="grid grid-cols-3 gap-0.5" aria-label="Pan">
          <span />
          <IconButton icon={ArrowUp} label={t('ctl.up')} size="sm" onClick={() => onPan(0, 1)} className={btn} />
          <span />
          <IconButton icon={ArrowLeft} label={t('ctl.left')} size="sm" onClick={() => onPan(-1, 0)} className={btn} />
          <span />
          <IconButton icon={ArrowRight} label={t('ctl.right')} size="sm" onClick={() => onPan(1, 0)} className={btn} />
          <span />
          <IconButton icon={ArrowDown} label={t('ctl.down')} size="sm" onClick={() => onPan(0, -1)} className={btn} />
          <span />
        </div>
      )}
    </div>
  );
}

export interface LegendPlatform {
  platform: Platform;
  count: number | null;
}

export function Legend({
  platforms,
  hiddenPlatforms,
  hiddenKinds,
  onTogglePlatform,
  onToggleKind,
  totals,
  compact,
}: {
  platforms: LegendPlatform[];
  hiddenPlatforms: ReadonlySet<string>;
  hiddenKinds: ReadonlySet<string>;
  onTogglePlatform: (p: string) => void;
  onToggleKind: (k: string) => void;
  totals: { containers: number | null; items: number | null };
  compact?: boolean;
}) {
  const t = useMessages(graphMessages);
  const [open, setOpen] = useState(false);
  // open by default on a roomy screen; collapsed in split view and on phones
  useEffect(() => setOpen(!compact && window.innerWidth >= 768), [compact]);
  const row =
    'flex h-7 w-full items-center gap-2 rounded px-1.5 text-left text-caption transition-colors duration-fast hover:bg-raised aria-[pressed=false]:text-subtle';
  return (
    <section aria-label={t('legend.title')} className="pointer-events-auto w-48 rounded-lg border border-line bg-surface/95 p-1.5 text-fg" data-testid="graph-legend">
      <button
        type="button"
        className="flex h-6 w-full items-center justify-between rounded px-1.5 text-micro font-semibold uppercase tracking-wide text-muted hover:text-fg"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {t('legend.title')}
        <span aria-hidden className="text-subtle">{open ? '−' : '+'}</span>
      </button>
      {open ? (
        <div className="mt-0.5 space-y-0.5">
          <p className="px-1.5 pb-0.5 text-[10.5px] text-subtle">{t('legend.hint')}</p>
          {platforms.map((p) => {
            const on = !hiddenPlatforms.has(p.platform);
            return (
              <button key={p.platform} type="button" aria-pressed={on} className={row} onClick={() => onTogglePlatform(p.platform)} data-testid={`legend-${p.platform}`}>
                <span aria-hidden className={cn('h-2.5 w-2.5 shrink-0 rounded-full', on ? '' : 'opacity-30')} style={{ background: `rgb(var(--pf-${p.platform}))` }} />
                <span className="flex-1 truncate">{platformName(p.platform)}</span>
                {p.count !== null ? <span className="tabular-nums text-muted">{fmtCount(p.count)}</span> : null}
              </button>
            );
          })}
          <span aria-hidden className="mx-1.5 my-1 block h-px bg-line" />
          <button type="button" aria-pressed={!hiddenKinds.has('container')} className={row} onClick={() => onToggleKind('container')}>
            <span aria-hidden className="h-3 w-3 shrink-0 rounded-full border-2 border-fg/70" />
            <span className="flex-1">{t('legend.places')}</span>
            {totals.containers !== null ? <span className="tabular-nums text-muted">{fmtCount(totals.containers)}</span> : null}
          </button>
          <button type="button" aria-pressed={!hiddenKinds.has('person')} className={row} onClick={() => onToggleKind('person')}>
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full bg-muted" />
            <span className="flex-1">{t('legend.people')}</span>
          </button>
          <button type="button" aria-pressed={!hiddenKinds.has('doc')} className={row} onClick={() => onToggleKind('doc')}>
            <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-fg/60" />
            <span className="flex-1">{t('legend.docs')}</span>
            {totals.items !== null ? <span className="tabular-nums text-muted">{fmtCount(totals.items)}</span> : null}
          </button>
          <div className="flex h-6 items-center gap-2 px-1.5 text-caption text-muted">
            <span aria-hidden className="h-px w-3 shrink-0 bg-fg/50" />
            {t('legend.links')}
          </div>
        </div>
      ) : null}
    </section>
  );
}
