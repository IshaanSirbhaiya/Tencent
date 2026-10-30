'use client';
// The guided tours (lib/tour.ts store, components/tour/steps.ts steps).
//
//   <TourOverlay/>  mounted once in app/layout.tsx: the spotlight, the step card, the first-visit
//                   toast and the checklist watcher. Survives navigation.
//   <TourButton/>   the header button (shell): starts the tour for the CURRENT mode.
//   <TourOffer/>    kept for the shell's import; the offer is now a toast drawn by the overlay.
//
// The two tours are separate (docs/finals/DECISIONS.md D-36). Each mode offers its own tour the first
// time a signed-in person is in it (User mode: /, /compare, /history, /settings; Developer mode: any
// /dev page), with its own "answered" flag. Neither tour hands over to the other, and a tour left
// open is closed when the page moves to the other mode, so a mode switch never resumes it.
//
// Mechanics: a positioned ring whose huge box-shadow dims the page around a cut-out (no library,
// pointer-events none, so the real page stays usable underneath); a non-modal card with the step
// counter, Back / Do it for me / Next / Skip and ← → Esc; the card is portalled into an open side
// sheet so it stays inside Radix's focus trap; a missing anchor centres the card with a note.
import { ArrowLeft, ArrowRight, Check, Compass, Loader2, X } from 'lucide-react';
import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { usePresenter } from '@/components/presenter/store';
import { usePrefs, useSession } from '@/components/shell/Providers';
import { Button, IconButton } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { tourMessages, type TourKey } from '@/lib/i18n/tour';
import {
  CHECKLIST,
  TOUR_LENGTH,
  closeTour,
  consumeEntry,
  dismissOffer,
  goToStep,
  markStepDone,
  markTourDone,
  rememberRequest,
  startTour,
  tick,
  useChecklist,
  useOfferOpen,
  useTour,
  type TourId,
} from '@/lib/tour';
import { STEPS, type Target, type TourCtx, type TourStep } from './steps';

type Rect = { top: number; left: number; width: number; height: number };
type Layout = 'anchored' | 'dock' | 'center';

const PAD = 6;
const GAP = 12;
const EDGE = 16;
const MISSING_AFTER_MS = 10_000;
const ACT_WAIT_MS = 30_000;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const q = (sel: string) => document.querySelector<HTMLElement>(sel);

/** The mode a page belongs to by its route alone: /dev/* is Developer mode; /, /compare, /history,
 *  /settings are User mode. Others (/login, /present, the legacy /map, /audit, /sim) belong to neither. */
function routeMode(path: string): TourId | null {
  if (path === '/dev' || path.startsWith('/dev/')) return 'dev';
  if (/^\/(compare|history|settings)?(\/.*)?$/.test(path)) return 'user';
  return null;
}

function visible(el: Element | null): el is HTMLElement {
  if (!el || !(el instanceof HTMLElement)) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

/** Radix modal dialogs and sheets (not popovers, not the tour card), open for long enough that
 *  their entrance transform has finished (a fixed child of a transformed box would be misplaced). */
const openSince = new WeakMap<Element, number>();
function openDialog(): HTMLElement | null {
  const all = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][data-state="open"]')).filter(
    (d) => !d.hasAttribute('data-tour-card') && !d.closest('[data-radix-popper-content-wrapper]') && getComputedStyle(d).position === 'fixed',
  );
  const d = all[all.length - 1] ?? null;
  if (!d) return null;
  const now = Date.now();
  if (!openSince.has(d)) openSince.set(d, now);
  return now - (openSince.get(d) ?? now) >= 260 ? d : null;
}

function samePlace(route: string): boolean {
  const u = new URL(route, window.location.origin);
  return u.pathname === window.location.pathname && (!u.search || u.search === window.location.search);
}

function routePath(route: string): string {
  return new URL(route, window.location.origin).pathname;
}

/** Keys the page itself uses: never steal arrows from these. */
function ownsArrows(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return true;
  return !!t.closest('[role="radiogroup"],[role="radio"],[role="application"],[role="tablist"],[role="listbox"],[role="combobox"],[role="slider"],[role="menu"],[role="grid"],[role="tree"],[role="treegrid"]');
}

export function TourOverlay() {
  const tour = useTour();
  const router = useRouter();
  const pathname = usePathname() ?? '/';
  const { me, status, switchPersona } = useSession();
  const { mode } = usePrefs();
  const t = useMessages(tourMessages);
  const checks = useChecklist();
  const userOfferOpen = useOfferOpen('user');
  const devOfferOpen = useOfferOpen('dev');
  const dialogOpen = useAnyDialogOpen();
  const presenterArmed = usePresenter().armed;

  const steps = STEPS[tour.tour];
  const isEnd = tour.tour === 'user' && tour.step === TOUR_LENGTH.user;
  const step: TourStep | undefined = isEnd ? undefined : steps[tour.step];

  const [mounted, setMounted] = useState(false);
  const [target, setTarget] = useState<{ el: HTMLElement; rect: Rect; spot: TourKey } | null>(null);
  const [host, setHost] = useState<HTMLElement | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [box, setBox] = useState<{ top: number; left: number; width: number } | null>(null);

  const card = useRef<HTMLDivElement>(null);
  const primary = useRef<HTMLButtonElement>(null);
  const visit = useRef(0);
  const enteredAt = useRef(0);
  const expectPath = useRef<string | null>(null);
  const routed = useRef(false);
  const doneAtEntry = useRef(false);
  const actRan = useRef(false);
  const lastEl = useRef<HTMLElement | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);

  // Latest values for async work and the poll (they run outside React's render).
  const signIn = useCallback(
    async (persona: string) => {
      await switchPersona(persona);
    },
    [switchPersona],
  );
  // An action that navigates moves the step's expected page with it.
  const go = useCallback(
    (url: string) => {
      expectPath.current = routePath(url);
      router.push(url);
    },
    [router],
  );
  const waitFor = useCallback(async (sel: string, ms = 10_000) => {
    const v = visit.current;
    const end = Date.now() + ms;
    while (visit.current === v && Date.now() < end) {
      const el = q(sel);
      if (visible(el)) return el;
      await sleep(100);
    }
    return null;
  }, []);
  const ctx: TourCtx = {
    me,
    compliance: !!me?.roles.includes('compliance_officer'),
    pathname,
    go,
    signIn,
    waitFor,
  };
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  const stepRef = useRef(step);
  stepRef.current = step;
  const tourRef = useRef(tour);
  tourRef.current = tour;
  const statusRef = useRef(status);
  statusRef.current = status;
  const isEndRef = useRef(isEnd);
  isEndRef.current = isEnd;

  const isDone = useCallback((s: TourStep | undefined) => !!s && (tourRef.current.done.includes(s.id) || !!s.done?.(ctxRef.current)), []);

  useEffect(() => setMounted(true), []);

  /* ---------------------------------------------------------------- focus in / focus back */
  useEffect(() => {
    if (tour.open && !wasOpen.current) {
      const a = document.activeElement as HTMLElement | null;
      returnFocus.current = a && a !== document.body && !a.closest('[data-tour-card]') ? a : null;
    }
    if (!tour.open && wasOpen.current) {
      const back = returnFocus.current?.isConnected ? returnFocus.current : document.querySelector<HTMLElement>('[data-tour-launch]');
      back?.focus({ preventScroll: true });
    }
    wasOpen.current = tour.open;
  }, [tour.open]);

  // A new step (or the checklist) moves focus into the card, so screen readers announce it.
  useEffect(() => {
    if (!tour.open) return;
    const id = window.setTimeout(() => card.current?.focus({ preventScroll: true }), 30);
    return () => window.clearTimeout(id);
  }, [tour.open, tour.tour, tour.step]);

  /* ---------------------------------------------------------------- entering a step */
  const personaKey = step?.reenterOnPersona ? me?.sub ?? '' : '';
  useEffect(() => {
    if (!tour.open) return;
    const v = ++visit.current;
    setTarget(null);
    setMissing(false);
    setBusy(false);
    setFailed(false);
    actRan.current = false;
    lastEl.current = null;
    routed.current = false;
    expectPath.current = null;
    enteredAt.current = Date.now();
    if (isEnd) {
      markTourDone('user');
      consumeEntry();
      return;
    }
    const s = stepRef.current;
    if (!s) return;
    (async () => {
      // Give the session a moment to load, so "who is signed in" is known before routing.
      for (let i = 0; i < 30 && statusRef.current === 'loading'; i++) await sleep(100);
      if (visit.current !== v) return;
      const c = ctxRef.current;
      const entry = tourRef.current.entry;
      // A fresh start skips a first step that is already satisfied (already signed in as Jordan).
      if (entry === 'start' && tourRef.current.step === 0 && s.done?.(c)) {
        markStepDone(s.id);
        goToStep(1);
        return;
      }
      doneAtEntry.current = isDone(s);
      let route: string | null = null;
      try {
        route = s.route ? await s.route(c) : null;
      } catch {
        route = null;
      }
      if (visit.current !== v) return;
      if (route) {
        expectPath.current = routePath(route);
        if (!samePlace(route)) router.push(route);
      }
      routed.current = true;
      enteredAt.current = Date.now();
      if (entry) consumeEntry();
      if (entry === 'signin') void runAct();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tour.open, tour.tour, tour.step, personaKey]);

  /* ---------------------------------------------------------------- "Do it for me" */
  const runAct = useCallback(async () => {
    const s = stepRef.current;
    const a = s?.act?.(ctxRef.current);
    if (!s || !a) return;
    const v = visit.current;
    actRan.current = true;
    setBusy(true);
    setFailed(false);
    try {
      await a.run(ctxRef.current);
      // Stay busy until there is nothing left to do (or the step is done), up to a limit.
      const end = Date.now() + ACT_WAIT_MS;
      while (visit.current === v && Date.now() < end) {
        const now = stepRef.current?.act?.(ctxRef.current);
        if (isDone(stepRef.current) || !now || now.what !== a.what) break;
        await sleep(150);
      }
    } catch {
      if (visit.current === v) setFailed(true);
    } finally {
      if (visit.current === v) setBusy(false);
    }
  }, [isDone]);

  /* ---------------------------------------------------------------- the poll: target, host, done */
  useEffect(() => {
    if (!tour.open) return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const s = stepRef.current;
        const c = ctxRef.current;
        const dlg = openDialog();
        setHost((h) => (h === dlg ? h : dlg));
        if (!s) {
          setTarget(null);
          return;
        }
        // done → checklist tick, remember, maybe move on
        if (s.done?.(c)) {
          if (!tourRef.current.done.includes(s.id)) markStepDone(s.id);
          if (s.check) tick(s.check);
          if (s.autoAdvance && routed.current && !doneAtEntry.current) {
            goToStep(tourRef.current.step + 1);
            return;
          }
        }
        let found: { el: HTMLElement; spot: TourKey } | null = null;
        const onPage = routed.current && (!expectPath.current || window.location.pathname === expectPath.current);
        if (onPage) {
          for (const tg of s.targets(c) as Target[]) {
            const el = q(tg.sel);
            if (visible(el)) {
              found = { el, spot: tg.spot };
              break;
            }
          }
        }
        if (found) {
          if (found.el !== lastEl.current) {
            lastEl.current = found.el;
            reveal(found.el);
          }
          const r = found.el.getBoundingClientRect();
          const rect = { top: r.top, left: r.left, width: r.width, height: r.height };
          setTarget((old) =>
            old && old.el === found!.el && old.spot === found!.spot && Math.abs(old.rect.top - rect.top) < 0.5 && Math.abs(old.rect.left - rect.left) < 0.5 && Math.abs(old.rect.width - rect.width) < 0.5 && Math.abs(old.rect.height - rect.height) < 0.5
              ? old
              : { el: found!.el, rect, spot: found!.spot },
          );
          setMissing(false);
        } else {
          setTarget(null);
          setMissing(onPage && Date.now() - enteredAt.current > MISSING_AFTER_MS);
        }
      });
    };
    measure();
    const id = window.setInterval(measure, 200);
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      cancelAnimationFrame(frame);
      window.clearInterval(id);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [tour.open, tour.tour, tour.step]);

  // A click on the step's own control completes it (e.g. Gate 2 on the pipeline).
  useEffect(() => {
    const sel = tour.open ? step?.doneOnClick : undefined;
    if (!sel || !step) return;
    const id = step.id;
    const on = (e: MouseEvent) => {
      if (e.target instanceof Element && e.target.closest(sel)) markStepDone(id);
    };
    document.addEventListener('click', on, true);
    return () => document.removeEventListener('click', on, true);
  }, [tour.open, step]);

  // When "Do it for me" finishes the step, put focus on the way forward.
  const stepDone = isDone(step);
  const prevDone = useRef(stepDone);
  useEffect(() => {
    if (stepDone && !prevDone.current && actRan.current) {
      window.setTimeout(() => primary.current?.focus({ preventScroll: true }), 60);
    }
    prevDone.current = stepDone;
  }, [stepDone]);

  // Moving the card into (or out of) a sheet re-creates its DOM: put focus back if it fell to <body>.
  useEffect(() => {
    if (!tour.open) return;
    const id = window.setTimeout(() => {
      const a = document.activeElement;
      if (!a || a === document.body) (primary.current ?? card.current)?.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(id);
  }, [host, tour.open]);

  /* ---------------------------------------------------------------- the checklist watcher */
  const meRef = useRef(me);
  meRef.current = me;
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  useEffect(() => {
    let frame = 0;
    let lastReq = '';
    const check = () => {
      frame = 0;
      if (q('[data-turn="done"]')) tick('asked');
      if (q('[data-testid="sources-sheet"]')) tick('source');
      if (q('[data-testid="identical-marker"]')) tick('identical');
      const a = q('[data-tour="answer"][data-request]');
      const req = a?.getAttribute('data-request');
      const sub = meRef.current?.sub;
      if (req && sub && pathRef.current === '/' && `${sub}:${req}` !== lastReq) {
        lastReq = `${sub}:${req}`;
        rememberRequest(req, sub);
      }
    };
    const mo = new MutationObserver(() => {
      if (!frame) frame = requestAnimationFrame(check);
    });
    mo.observe(document.body, { childList: true, subtree: true });
    check();
    return () => {
      mo.disconnect();
      cancelAnimationFrame(frame);
    };
  }, []);
  // The tours are separate: a page of the other mode closes a running tour (it is not resumed later).
  const pageMode = routeMode(pathname);
  useEffect(() => {
    if (tour.open && pageMode && pageMode !== tour.tour) closeTour();
  }, [tour.open, tour.tour, pageMode]);

  /* ---------------------------------------------------------------- moving */
  const total = TOUR_LENGTH[tour.tour];
  const lastStep = tour.step === total - 1;
  const next = useCallback(() => {
    const tr = tourRef.current;
    if (tr.tour === 'dev' && tr.step === TOUR_LENGTH.dev - 1) {
      markTourDone('dev');
      closeTour();
      return;
    }
    goToStep(tr.step + 1);
  }, []);
  const back = useCallback(() => {
    const tr = tourRef.current;
    if (tr.step > 0) goToStep(tr.step - 1);
  }, []);
  const skip = useCallback(() => {
    dismissOffer(tourRef.current.tour);
    closeTour();
  }, []);

  // Keyboard: Esc closes (an open sheet closes first); ← → move, never while the page uses arrows.
  useEffect(() => {
    if (!tour.open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === 'Escape') {
        const inCard = e.target instanceof Element && !!e.target.closest('[data-tour-card]');
        if (!inCard && (openDialogAny() || document.querySelector('[role="listbox"]'))) return;
        if (inCard && openDialogAny()) return; // Radix closes the sheet the card sits in
        e.preventDefault();
        skip();
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        if (ownsArrows(e.target)) return;
        e.preventDefault();
        if (e.key === 'ArrowRight') {
          if (!isEndRef.current) next();
        } else back();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tour.open, next, back, skip]);

  /* ---------------------------------------------------------------- placing the card */
  const layout: Layout = isEnd ? 'center' : target ? 'anchored' : missing || step?.targetOptional?.(ctx) ? 'center' : 'dock';
  useLayoutEffect(() => {
    if (!tour.open) return;
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    const width = Math.min(isEnd ? 400 : 360, vw - EDGE * 2);
    const h = card.current?.offsetHeight ?? 220;
    let next: { top: number; left: number; width: number };
    if (layout === 'center') next = { top: Math.max(EDGE, (vh - h) / 2), left: (vw - width) / 2, width };
    else if (layout === 'dock' || !target) next = { top: vh - h - EDGE, left: vw < 640 ? (vw - width) / 2 : vw - width - EDGE, width };
    else next = place(target.rect, width, h, vw, vh, !!step?.preferSide);
    setBox((old) => (old && Math.abs(old.top - next.top) < 0.5 && Math.abs(old.left - next.left) < 0.5 && old.width === next.width ? old : next));
  });

  /* ---------------------------------------------------------------- render */
  // The offer toast sits outside any dialog, so it hides while a dialog or sheet is open (it covered the Act
  // sheet's fields at 390 px and broke the modal focus model; WCAG 2.4.11). Each mode offers its own tour,
  // on that mode's own pages only; never while the presenter is armed (stage.ts also hides it under Stage).
  const offerFor: TourId | null =
    pageMode === 'dev' && mode === 'dev' && devOfferOpen ? 'dev' : pageMode === 'user' && mode === 'user' && userOfferOpen ? 'user' : null;
  const offer = !tour.open && mounted && !!me && status === 'ready' && !presenterArmed && !dialogOpen ? offerFor : null;

  if (!mounted) return null;
  if (!tour.open) return offer ? <OfferToast tour={offer} /> : null;
  if (!step && !isEnd) return null;

  const inDialog = !!host;
  const act = step && !stepDone ? step.act?.(ctx) ?? null : null;
  const title = isEnd ? t('end.title') : t(step!.title(ctx));
  const bodyKey = isEnd ? 'end.body' : stepDone && step!.doneBody ? step!.doneBody(ctx) : step!.body(ctx);
  const eyebrow = tour.tour === 'user' ? t('card.eyebrow.user') : t('card.eyebrow.dev');
  const counter = isEnd ? t('end.eyebrow') : t('card.counter', { i: tour.step + 1, n: total });
  const showMissing = layout === 'center' && missing && !isEnd && !step?.targetOptional?.(ctx);
  const nChecked = CHECKLIST.filter((k) => checks[k]).length;

  const spotlight =
    target && !isEnd ? (
      <div
        role="img"
        aria-label={t('spot.label', { name: t(target.spot) })}
        data-testid="tour-spotlight"
        className={cn('pointer-events-none fixed rounded-lg ring-2 ring-accent transition-[top,left,width,height] duration-200 ease-out', inDialog ? 'z-[60]' : 'z-[80]')}
        style={{
          top: target.rect.top - PAD,
          left: target.rect.left - PAD,
          width: target.rect.width + PAD * 2,
          height: target.rect.height + PAD * 2,
          // Inside a sheet, Radix's own scrim already dims the page: draw the ring only.
          boxShadow: inDialog ? undefined : '0 0 0 200vmax rgb(var(--tour-scrim) / var(--tour-scrim-alpha))',
        }}
      />
    ) : isEnd ? (
      <div aria-hidden className="ds-scrim pointer-events-none fixed inset-0 z-[80]" style={{ background: 'rgb(var(--tour-scrim) / calc(var(--tour-scrim-alpha) * 0.6))' }} data-state="open" />
    ) : null;

  const cardEl = (
    <div
      ref={card}
      role="dialog"
      aria-modal="false"
      aria-labelledby="tour-title"
      aria-describedby="tour-body"
      tabIndex={-1}
      data-testid="tour"
      data-tour-card=""
      data-tour-step={isEnd ? 'end' : step!.id}
      data-tour-layout={layout}
      className={cn(
        'pointer-events-auto fixed overflow-hidden rounded-xl border border-line-strong bg-overlay text-fg shadow-pop outline-none transition-[top,left] duration-200 ease-out',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus',
        inDialog ? 'z-[70]' : 'z-[90]',
      )}
      style={box ? { top: box.top, left: box.left, width: box.width } : { bottom: EDGE, right: EDGE, width: `min(360px, calc(100vw - ${EDGE * 2}px))` }}
    >
      <div key={`${tour.tour}-${tour.step}`} className="ds-pop px-4 pb-3 pt-3" data-state="open">
        <div className="flex items-center justify-between gap-2">
          <p className="min-w-0 truncate text-micro font-semibold uppercase tracking-wider text-accent tabular-nums" data-testid="tour-counter">
            {eyebrow}
            <span aria-hidden className="mx-1.5 text-subtle">
              ·
            </span>
            {counter}
          </p>
          <IconButton icon={X} label={t('card.close')} size="sm" onClick={skip} className="-mr-1.5" />
        </div>
        <h2 id="tour-title" className="mt-1 text-heading-sm text-fg">
          {title}
        </h2>
        <div id="tour-body" className="mt-1.5 text-body-sm leading-6 text-muted">
          <p>{t(bodyKey)}</p>
          {showMissing ? (
            <p role="status" className="mt-2 text-caption text-subtle" data-testid="tour-missing">
              {t('card.missing')}
            </p>
          ) : null}
          {busy ? (
            <p role="status" className="mt-2 flex items-center gap-1.5 text-caption text-muted">
              <Loader2 aria-hidden className="h-3.5 w-3.5 motion-safe:animate-spin" />
              {t('card.working')}
            </p>
          ) : !target && layout === 'dock' && !isEnd ? (
            <p role="status" className="mt-2 flex items-center gap-1.5 text-caption text-subtle">
              <Loader2 aria-hidden className="h-3.5 w-3.5 motion-safe:animate-spin" />
              {t('card.preparing')}
            </p>
          ) : null}
          {failed ? (
            <p role="alert" className="mt-2 text-caption text-fg">
              {t('card.failed')}
            </p>
          ) : null}
          {stepDone && step?.act && !isEnd ? (
            <p className="mt-2 inline-flex items-center gap-1.5 text-caption font-medium text-ok" data-testid="tour-step-done">
              <Check aria-hidden className="h-3.5 w-3.5" />
              {t('card.done')}
            </p>
          ) : null}
        </div>

        {isEnd ? (
          <EndChecklist checks={checks} n={nChecked} />
        ) : (
          <div aria-hidden className="mt-3 flex gap-1">
            {steps.map((s, i) => (
              <span
                key={s.id}
                className={cn(
                  'h-1 flex-1 rounded-full transition-colors duration-200',
                  i < tour.step || tour.done.includes(s.id) ? 'bg-accent' : i === tour.step ? 'bg-accent/45' : 'bg-line-strong/60',
                )}
              />
            ))}
          </div>
        )}
        <p className="mt-2.5 hidden text-caption text-subtle sm:block">{t('card.keys')}</p>
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-line bg-surface/60 px-3 py-2.5">
        {isEnd ? (
          // The user tour ends here, in User mode: no hand-over to the Developer tour (D-36).
          <Button ref={primary} variant="primary" size="sm" onClick={closeTour} className="ml-auto" data-testid="tour-end-close">
            {t('end.close')}
          </Button>
        ) : (
          <>
            <Button variant="ghost" size="sm" onClick={skip} className="-ml-1 px-2">
              {t('card.skip')}
            </Button>
            <div className="flex items-center gap-1.5">
              <IconButton icon={ArrowLeft} label={t('card.back')} variant="secondary" size="md" onClick={back} disabled={tour.step === 0} />
              {act ? (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => void runAct()}
                  busy={busy}
                  aria-label={t('card.doItName', { what: t(act.what) })}
                  data-testid="tour-do-it"
                >
                  {t('card.doIt')}
                </Button>
              ) : null}
              <Button
                ref={act ? undefined : primary}
                variant={act ? 'secondary' : 'primary'}
                size="sm"
                iconRight={tour.tour === 'dev' && lastStep ? undefined : ArrowRight}
                onClick={next}
                data-testid="tour-next"
              >
                {tour.tour === 'dev' && lastStep ? t('card.finish') : t('card.next')}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );

  return createPortal(
    <>
      {spotlight}
      {cardEl}
    </>,
    host ?? document.body,
  );
}

function openDialogAny(): boolean {
  return Array.from(document.querySelectorAll('[role="dialog"][data-state="open"]')).some(
    (d) => !d.hasAttribute('data-tour-card') && !d.closest('[data-radix-popper-content-wrapper]'),
  );
}

/** True while any modal dialog or sheet (not the tour card, not a popover) is open; follows the DOM. */
function useAnyDialogOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const check = () => setOpen(openDialogAny());
    check();
    const mo = new MutationObserver(check);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-state'] });
    return () => mo.disconnect();
  }, []);
  return open;
}

/** Scroll the highlight into view: centred if it fits, else its top just under the header. */
function reveal(el: HTMLElement) {
  const reduce = document.documentElement.getAttribute('data-motion') === 'reduce' || window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const behavior: ScrollBehavior = reduce ? 'auto' : 'smooth';
  if (el.closest('[role="dialog"]')) {
    el.scrollIntoView({ block: 'nearest', behavior });
    return;
  }
  const r = el.getBoundingClientRect();
  const header = document.querySelector('header')?.getBoundingClientRect().bottom ?? 0;
  const vh = window.innerHeight;
  const room = vh - header;
  if (r.top >= header && r.bottom <= vh - 140) return; // already comfortably on screen
  const top = r.height > room * 0.6 ? window.scrollY + r.top - header - 16 : window.scrollY + r.top - header - (room - r.height) / 2;
  window.scrollTo({ top: Math.max(0, top), behavior });
}

/** Below, above, beside, or docked; `side` tries beside first (e.g. left of a side sheet). */
function place(rect: Rect, width: number, h: number, vw: number, vh: number, side: boolean) {
  const clampX = (x: number) => Math.max(EDGE, Math.min(x, vw - width - EDGE));
  const clampY = (y: number) => Math.max(EDGE, Math.min(y, vh - h - EDGE));
  const beside = () => {
    const left = rect.left - PAD - GAP - width;
    if (left >= EDGE) return { top: clampY(rect.top), left, width };
    const right = rect.left + rect.width + PAD + GAP;
    if (right + width <= vw - EDGE) return { top: clampY(rect.top), left: right, width };
    return null;
  };
  if (side) {
    const b = beside();
    if (b) return b;
  }
  const below = rect.top + rect.height + PAD + GAP;
  if (below + h <= vh - EDGE) return { top: below, left: clampX(rect.left), width };
  const above = rect.top - PAD - GAP - h;
  if (above >= EDGE) return { top: above, left: clampX(rect.left), width };
  const b = beside();
  if (b) return b;
  return { top: vh - h - EDGE, left: vw < 640 ? (vw - width) / 2 : vw - width - EDGE, width };
}

function EndChecklist({ checks, n }: { checks: Record<(typeof CHECKLIST)[number], boolean>; n: number }) {
  const t = useMessages(tourMessages);
  const label: Record<(typeof CHECKLIST)[number], TourKey> = { asked: 'check.asked', source: 'check.source', identical: 'check.identical' };
  return (
    <div className="mt-3">
      <p className="mb-1.5 text-caption tabular-nums text-subtle" data-testid="tour-checklist-progress">
        {t('end.progress', { n, total: CHECKLIST.length })}
      </p>
      <ul aria-label={t('check.label')} className="divide-y divide-line rounded-lg border border-line bg-surface" data-testid="tour-checklist">
        {CHECKLIST.map((k) => {
          const on = checks[k];
          return (
            <li key={k} className="flex min-h-9 items-center gap-2.5 px-3 py-2 text-body-sm" data-check={k} data-checked={on ? 'true' : 'false'}>
              <span
                aria-hidden
                className={cn(
                  'inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors duration-200',
                  on ? 'border-accent bg-accent-solid text-accent-fg' : 'border-line-strong bg-transparent',
                )}
              >
                {on ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
              </span>
              <span className={cn('min-w-0 flex-1', on ? 'text-fg' : 'text-muted')}>{t(label[k])}</span>
              <span className="sr-only">, {on ? t('check.isDone') : t('check.isTodo')}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The first-visit offer for one mode's tour: a small toast, never a modal, never timed. */
function OfferToast({ tour }: { tour: TourId }) {
  const t = useMessages(tourMessages);
  const dev = tour === 'dev';
  return (
    <aside
      aria-label={t(dev ? 'offer.devLabel' : 'offer.label')}
      data-testid="tour-offer"
      data-tour-offer={tour}
      data-stage-hide
      className="ds-pop fixed left-4 right-4 top-16 z-[70] rounded-xl sm:bottom-4 sm:top-auto border border-line-strong bg-overlay p-4 text-fg shadow-pop sm:right-auto sm:w-[22rem]"
      data-state="open"
    >
      <div className="flex items-start gap-3">
        <span aria-hidden className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
          <Compass className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-heading-sm text-fg">{t('offer.title')}</p>
          <p className="mt-0.5 text-body-sm text-muted">{t(dev ? 'offer.devBody' : 'offer.body')}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" data-tour-launch onClick={() => startTour(tour)}>
              {t('offer.start')}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => dismissOffer(tour)}>
              {t('offer.later')}
            </Button>
          </div>
        </div>
      </div>
    </aside>
  );
}

/** Header button: starts the tour for the current mode. */
export function TourButton({ className }: { className?: string }) {
  const t = useMessages(tourMessages);
  const { mode } = usePrefs();
  const tourFor: TourId = mode === 'dev' ? 'dev' : 'user';
  return (
    <Button variant="secondary" size="sm" icon={Compass} data-tour-launch onClick={() => startTour(tourFor)} className={className} data-testid="tour-launch">
      <span className="sm:hidden">{t('launch.short')}</span>
      <span className="hidden sm:inline">{t('launch.full')}</span>
    </Button>
  );
}

/** Kept for the shell's import. The offer is now a toast drawn by <TourOverlay/>. */
export function TourOffer(_props: { className?: string }) {
  return null;
}
