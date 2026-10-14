'use client';
import { clsx } from 'clsx';
import { Info, X } from 'lucide-react';
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

/**
 * A small (i) "toggletip": a real button that shows one or two plain sentences about the panel next
 * to it. Accessible without a popover library:
 * - the trigger is a <button> with aria-expanded / aria-controls and a descriptive name;
 * - the text is rendered into a polite live region when opened, so screen readers read it out;
 * - Esc or a click elsewhere closes it and focus stays on (or returns to) the trigger;
 * - it is positioned in the viewport (fixed) and clamped so it never causes horizontal scroll.
 */
export function InfoTip({ title, children, className, testId }: { title: string; children: React.ReactNode; className?: string; testId?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const id = useId();
  const tipId = `infotip-${id.replace(/:/g, '')}`;

  const place = useCallback(() => {
    const b = btn.current;
    if (!b) return;
    const r = b.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    const width = Math.min(300, vw - 16);
    const left = Math.max(8, Math.min(r.left + r.width / 2 - width / 2, vw - width - 8));
    const h = tip.current?.offsetHeight ?? 120;
    const below = r.bottom + 8;
    const top = below + h > vh - 8 && r.top - 8 - h > 8 ? r.top - 8 - h : below;
    setPos({ top, left, width });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (btn.current?.contains(t) || tip.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setOpen(false);
        btn.current?.focus();
      }
    };
    window.addEventListener('pointerdown', onDown, true);
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open, place]);

  return (
    <span className={clsx('relative inline-flex', className)}>
      <button
        ref={btn}
        type="button"
        aria-expanded={open}
        aria-controls={tipId}
        aria-label={`About ${title}`}
        title={`About ${title}`}
        onClick={() => setOpen((o) => !o)}
        data-testid={testId}
        className={clsx(
          'inline-flex h-6 w-6 items-center justify-center rounded-full text-muted transition-colors duration-150 hover:bg-raised hover:text-fg',
          open && 'bg-raised text-fg',
        )}
      >
        <Info aria-hidden className="h-3.5 w-3.5" />
      </button>
      <span role="status" aria-live="polite" id={tipId} className="contents">
        {open ? (
          <span
            ref={tip}
            data-testid="infotip"
            className="fixed z-[70] block rounded-lg border border-line-strong bg-surface p-3 text-left shadow-lg shadow-black/20"
            style={pos ? { top: pos.top, left: pos.left, width: pos.width } : { visibility: 'hidden', top: 0, left: 0 }}
          >
            <span className="flex items-start justify-between gap-2">
              <span className="text-xs font-semibold text-fg">{title}</span>
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  btn.current?.focus();
                }}
                className="-mr-1 -mt-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted hover:bg-raised hover:text-fg"
                aria-label="Close"
              >
                <X aria-hidden className="h-3.5 w-3.5" />
              </button>
            </span>
            <span className="mt-1 block text-xs font-normal normal-case leading-5 tracking-normal text-muted">{children}</span>
          </span>
        ) : null}
      </span>
    </span>
  );
}
