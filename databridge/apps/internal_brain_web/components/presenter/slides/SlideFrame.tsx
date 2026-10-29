'use client';
import { useEffect, useState } from 'react';
import { Wordmark } from '@/components/Brand';
import { cn } from '@/lib/cn';

/**
 * A full-bleed slide: the wordmark top-left, an eyebrow top-right, content centred, and nothing in
 * the outer 5 % (projector safe area). The bottom keeps room for the presenter bar.
 */
export function SlideFrame({ eyebrow, children, footer, className }: { eyebrow?: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode; className?: string }) {
  return (
    <div className={cn('flex min-h-dvh flex-col bg-bg px-[6vw] pb-[calc(5vh+6.5rem)] pt-[5vh] text-fg', className)} data-testid="slide">
      <header className="flex items-center justify-between gap-4 text-caption text-muted">
        <Wordmark />
        {eyebrow ? <span className="text-micro font-semibold uppercase tracking-[0.14em] text-subtle">{eyebrow}</span> : null}
      </header>
      <div className="flex flex-1 flex-col justify-center py-[4vh]">{children}</div>
      {footer ? <footer className="text-caption text-subtle">{footer}</footer> : null}
    </div>
  );
}

export type Loaded<T> = { state: 'loading' } | { state: 'ok'; data: T } | { state: 'missing' };

/** Read a static JSON file from public/ (never the IB proxy). Missing or malformed → 'missing'. */
export function usePublicJson<T>(path: string): Loaded<T> {
  const [v, setV] = useState<Loaded<T>>({ state: 'loading' });
  useEffect(() => {
    let live = true;
    fetch(path, { cache: 'no-store' })
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        const data = (await r.json()) as T;
        if (live) setV({ state: 'ok', data });
      })
      .catch(() => live && setV({ state: 'missing' }));
    return () => {
      live = false;
    };
  }, [path]);
  return v;
}

/** The big-number type used on every slide (fluid, tabular). */
export const BIG = 'font-semibold tabular-nums leading-none tracking-[-0.03em] text-[clamp(3.5rem,8.5vw,8rem)]';
