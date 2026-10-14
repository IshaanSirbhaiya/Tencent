'use client';
import { clsx } from 'clsx';
import { ArrowLeft, ArrowRight, Compass, Loader2, X } from 'lucide-react';
import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { TOUR_STEPS, closeTour, dismissOffer, emitTourAction, goToStep, offerDismissed, startTour, useTour } from '@/lib/tour';

type Rect = { top: number; left: number; width: number; height: number };

const PAD = 6;
const GAP = 12;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const find = (anchor: string) => document.querySelector<HTMLElement>(`[data-tour="${anchor}"]`);

async function waitFor(anchor: string, ms: number, alive: () => boolean): Promise<HTMLElement | null> {
  const end = Date.now() + ms;
  while (alive() && Date.now() < end) {
    const el = find(anchor);
    if (el && el.getBoundingClientRect().height > 0) return el;
    await sleep(120);
  }
  return null;
}

function reducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * The guided tour overlay. Mounted once in the root layout so it survives navigation. For each
 * step it signs in as the step's persona if needed (the normal demo login), navigates, waits for
 * the real element, optionally asks the page to act (ask, compare, verify), then spotlights that
 * element with an anchored, keyboard-operable dialog.
 */
export function TourOverlay() {
  const tour = useTour();
  const router = useRouter();
  const pathname = usePathname();
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const [rect, setRect] = useState<Rect | null>(null);
  const [phase, setPhase] = useState<'preparing' | 'shown' | 'missing'>('preparing');
  const [error, setError] = useState<string | null>(null);
  const [box, setBox] = useState<{ top: number; left: number; width: number } | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const anchorEl = useRef<HTMLElement | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  const step = TOUR_STEPS[tour.step];

  // Remember where focus was when the tour opened; give it back when the tour closes.
  useEffect(() => {
    if (tour.open && !wasOpen.current) returnFocus.current = document.activeElement as HTMLElement | null;
    if (!tour.open && wasOpen.current) {
      const back = returnFocus.current?.isConnected ? returnFocus.current : document.querySelector<HTMLElement>('[data-tour-launch]');
      back?.focus();
    }
    wasOpen.current = tour.open;
  }, [tour.open]);

  // Prepare each step: persona → route → (action) → anchor.
  useEffect(() => {
    if (!tour.open || !step) return;
    let alive = true;
    const isAlive = () => alive;
    setPhase('preparing');
    setError(null);
    setRect(null);
    anchorEl.current = null;
    (async () => {
      if (step.persona) {
        let sub: string | null = null;
        try {
          sub = (await api.whoami()).sub;
        } catch {
          sub = null;
        }
        if (!alive) return;
        if (sub !== step.persona) {
          try {
            await api.login(step.persona);
          } catch {
            if (!alive) return;
            setError('Sign-in for this step did not work. You can sign in from the sign-in page and carry on.');
            setPhase('missing');
            return;
          }
          if (!alive) return;
          if (pathRef.current === step.route) {
            // Same page, new person: reload so the page fetches its data as that person.
            window.location.assign(step.route);
            return;
          }
        }
      }
      if (pathRef.current !== step.route) router.push(step.route);
      if (step.action) {
        const ready = await waitFor(step.ready ?? step.anchor, 15_000, isAlive);
        if (!alive) return;
        await sleep(150); // let the page attach its action listener
        const already = find(step.anchor);
        const needs = step.action === 'verify' ? !document.querySelector('[data-testid="verify-status"]') : !already;
        if (ready && needs) emitTourAction(step.action);
      }
      const el = await waitFor(step.anchor, 60_000, isAlive);
      if (!alive) return;
      if (!el) {
        setPhase('missing');
        return;
      }
      anchorEl.current = el;
      el.scrollIntoView({ block: el.offsetHeight > window.innerHeight * 0.6 ? 'start' : 'center', behavior: reducedMotion() ? 'auto' : 'smooth' });
      await sleep(reducedMotion() ? 50 : 350);
      if (!alive) return;
      setPhase('shown');
    })();
    return () => {
      alive = false;
    };
  }, [tour.open, tour.step, step, router]);

  // Track the anchor while shown (it can grow, scroll or reflow).
  useEffect(() => {
    if (!tour.open || phase !== 'shown') return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const el = anchorEl.current?.isConnected ? anchorEl.current : step ? find(step.anchor) : null;
        anchorEl.current = el;
        if (!el) return setRect(null);
        const r = el.getBoundingClientRect();
        setRect((old) =>
          old && Math.abs(old.top - r.top) < 0.5 && Math.abs(old.left - r.left) < 0.5 && Math.abs(old.width - r.width) < 0.5 && Math.abs(old.height - r.height) < 0.5
            ? old
            : { top: r.top, left: r.left, width: r.width, height: r.height },
        );
      });
    };
    measure();
    const t = setInterval(measure, 250);
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      cancelAnimationFrame(frame);
      clearInterval(t);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [tour.open, phase, step]);

  // Place the dialog next to the spotlight: below, above, beside, or docked at the bottom.
  useLayoutEffect(() => {
    if (!tour.open) return;
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    const width = Math.min(360, vw - 32);
    const h = dialog.current?.offsetHeight ?? 200;
    const dock = { top: vh - h - 16, left: Math.max(16, (vw - width) / 2), width };
    if (!rect || phase !== 'shown') return setBox(dock);
    const clampX = (x: number) => Math.max(16, Math.min(x, vw - width - 16));
    const below = rect.top + rect.height + PAD + GAP;
    const above = rect.top - PAD - GAP - h;
    if (below + h <= vh - 16) return setBox({ top: below, left: clampX(rect.left), width });
    if (above >= 16) return setBox({ top: above, left: clampX(rect.left), width });
    const right = rect.left + rect.width + PAD + GAP;
    if (right + width <= vw - 16) return setBox({ top: Math.max(16, Math.min(rect.top, vh - h - 16)), left: right, width });
    const left = rect.left - PAD - GAP - width;
    if (left >= 16) return setBox({ top: Math.max(16, Math.min(rect.top, vh - h - 16)), left, width });
    setBox(dock);
  }, [tour.open, rect, phase, tour.step, error]);

  // Move focus into the dialog for each new step, so screen readers announce it.
  useEffect(() => {
    if (tour.open && phase !== 'preparing') dialog.current?.focus({ preventScroll: true });
  }, [tour.open, phase, tour.step]);

  const next = useCallback(() => goToStep(tour.step + 1), [tour.step]);
  const back = useCallback(() => goToStep(tour.step - 1), [tour.step]);

  // Keyboard: Esc closes; arrows move (but never while typing in a field).
  useEffect(() => {
    if (!tour.open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (e.key === 'Escape') {
        if (document.querySelector('[role="dialog"][data-state="open"]')) return; // a drawer handles its own Esc
        e.preventDefault();
        closeTour();
      } else if (!typing && e.key === 'ArrowRight') {
        e.preventDefault();
        next();
      } else if (!typing && e.key === 'ArrowLeft') {
        e.preventDefault();
        back();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tour.open, next, back]);

  if (!tour.open || !step) return null;
  const total = TOUR_STEPS.length;
  const last = tour.step === total - 1;
  const motion = 'motion-safe:transition-[top,left,width,height] motion-safe:duration-200 motion-safe:ease-out';

  return (
    <>
      {phase === 'shown' && rect ? (
        <div
          aria-hidden
          data-testid="tour-spotlight"
          className={clsx('pointer-events-none fixed z-[80] rounded-lg ring-2 ring-accent', motion)}
          style={{
            top: rect.top - PAD,
            left: rect.left - PAD,
            width: rect.width + PAD * 2,
            height: rect.height + PAD * 2,
            boxShadow: '0 0 0 200vmax rgb(var(--tour-scrim) / var(--tour-scrim-alpha))',
          }}
        />
      ) : null}
      <div
        ref={dialog}
        role="dialog"
        aria-modal="false"
        aria-labelledby="tour-title"
        aria-describedby="tour-body"
        tabIndex={-1}
        data-testid="tour"
        className={clsx('fixed z-[90] rounded-lg border border-line-strong bg-surface p-4 shadow-xl shadow-black/30 focus:outline-none', motion)}
        style={box ? { top: box.top, left: box.left, width: box.width } : { bottom: 16, left: 16, width: 'min(360px, calc(100vw - 32px))' }}
      >
        <div className="flex items-center justify-between gap-2">
          <p className="text-2xs font-semibold uppercase tracking-wide text-accent tabular" data-testid="tour-counter">
            Tour · step {tour.step + 1} of {total}
          </p>
          <button
            type="button"
            onClick={closeTour}
            className="-mr-1 inline-flex h-7 w-7 items-center justify-center rounded text-muted hover:bg-raised hover:text-fg"
            aria-label="Close tour"
          >
            <X aria-hidden className="h-4 w-4" />
          </button>
        </div>
        <h2 id="tour-title" className="mt-1 text-sm font-semibold text-fg">
          {step.title}
        </h2>
        <div id="tour-body" className="mt-1 text-sm leading-6 text-muted">
          {phase === 'preparing' ? (
            <p className="flex items-center gap-2" role="status">
              <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" />
              {step.waiting ?? 'Getting the next screen ready…'}
            </p>
          ) : phase === 'missing' ? (
            <p role="status">{error ?? "This part of the screen isn't showing right now. You can go on to the next step."}</p>
          ) : (
            <p>{step.body}</p>
          )}
        </div>
        <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-raised" aria-hidden>
          <div className={clsx('h-full rounded-full bg-accent', motion)} style={{ width: `${((tour.step + 1) / total) * 100}%` }} />
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <button type="button" onClick={closeTour} className="h-8 rounded-md px-2 text-xs text-muted hover:bg-raised hover:text-fg">
            Skip tour
          </button>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={back}
              disabled={tour.step === 0}
              className="inline-flex h-8 items-center gap-1 rounded-md border border-line-strong px-2.5 text-xs font-medium text-fg hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ArrowLeft aria-hidden className="h-3.5 w-3.5" /> Back
            </button>
            <button
              type="button"
              onClick={last ? closeTour : next}
              className="inline-flex h-8 items-center gap-1 rounded-md bg-accent-solid px-3 text-xs font-medium text-accent-fg hover:bg-accent-solid/90"
            >
              {step.next ?? (last ? 'Finish' : 'Next')}
              {last ? null : <ArrowRight aria-hidden className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>
        <p className="mt-2 hidden text-2xs text-muted sm:block">Arrow keys move between steps · Esc closes</p>
      </div>
    </>
  );
}

/** Header button that starts the tour from the beginning. */
export function TourButton({ className }: { className?: string }) {
  return (
    <button
      type="button"
      data-tour-launch
      onClick={() => startTour(0)}
      className={clsx('inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong px-2.5 text-xs font-medium text-fg hover:bg-raised', className)}
    >
      <Compass aria-hidden className="h-3.5 w-3.5" />
      <span className="sm:hidden">Tour</span>
      <span className="hidden sm:inline">Take the tour</span>
    </button>
  );
}

/** A one-time, inline (never covering anything) offer. Dismissal is remembered in localStorage. */
export function TourOffer({ className }: { className?: string }) {
  const tour = useTour();
  const [show, setShow] = useState(false);
  useEffect(() => {
    setShow(!offerDismissed());
  }, []);
  if (!show || tour.open) return null;
  return (
    <aside
      aria-label="Guided tour"
      data-testid="tour-offer"
      className={clsx('flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent/40 bg-accent/5 px-4 py-3', className)}
    >
      <p className="flex min-w-0 items-start gap-2 text-sm text-fg">
        <Compass aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        <span>
          <span className="font-medium">New here?</span> A six-step tour shows what each person can open, how answers stay within that, and where every question is recorded.
        </span>
      </p>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={() => {
            dismissOffer();
            setShow(false);
          }}
          className="h-8 rounded-md px-2.5 text-xs text-muted hover:bg-raised hover:text-fg"
        >
          Not now
        </button>
        <button
          type="button"
          data-tour-launch
          onClick={() => startTour(0)}
          className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent-solid px-3 text-xs font-medium text-accent-fg hover:bg-accent-solid/90"
        >
          Take the tour
        </button>
      </div>
    </aside>
  );
}
