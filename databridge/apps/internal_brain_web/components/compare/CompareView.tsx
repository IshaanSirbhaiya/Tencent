'use client';
// Compare: the same question for two people, or the same person with two questions, side by side.
//
// Each column is a SEPARATE signed session in its own compare slot (b, c): the proxy forwards
// cookie ib_session_b / ib_session_c instead of the main ib_session when the request carries
// x-ib-slot. The header only picks which signed cookie is forwarded; identity still comes solely
// from the token the IB minted at demo sign-in. Both slots are held with Web Locks for the whole
// sign-in → ask → who-am-I check, so a second Compare tab waits its turn instead of re-signing a
// slot halfway through; every result is checked against the person just signed in.
//
// A run succeeds only when BOTH columns came back verified. Each run has a generation: the
// Presenter's Reset (handler 'compare-cancel') and unmount invalidate it, abort its asks, and its
// late results are dropped. At the start of every run the remembered request ids and the cached
// result for those people are forgotten, so a later Presenter step never reuses an older answer.
import { Users } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DEMO_COMPARE, Page, useSession } from '@/components/shell';
import { usePresenterHandler, type HandlerResult } from '@/components/presenter/bus';
import { forgetRequests, rememberRequest } from '@/components/presenter/store';
import { Button, Field, SegmentedControl, inputClass } from '@/components/ui';
import { ApiError, slotApi, withSlotLocks } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { presenterMessages } from '@/lib/i18n/presenter';
import { personaById } from '@/lib/personas';
import { useTourAction } from '@/lib/tour';
import type { AskResponse } from '@/lib/types';
import { useAskProgress } from '@/components/user';
import { SourcesSheet, isRefusal, sameResponse } from './answerAdapter';
import { cacheResult, cachedResult, forgetResult } from './cache';
import { CompareColumn, useRole, type Side, type SlotId } from './CompareColumn';
import { CompareSourceSheet } from './CompareSourceSheet';
import { EqualityMarker } from './EqualityMarker';
import { CHOICES, parseSpec, sameSpec, specUrl, type CompareMode, type CompareSpec } from './params';

const MAX_Q = 2000;

/**
 * Sign the slot in, ask, check who answered. `live` is checked after every await: once the run is
 * superseded nothing more is sent on this slot (the lock is then released quickly).
 */
async function askSide(
  slot: SlotId,
  persona: string,
  question: string,
  ask: (q: string) => Promise<AskResponse>,
  live: () => boolean,
): Promise<Side> {
  const s = slotApi(slot);
  const check = () => {
    if (!live()) throw new ApiError(0, 'cancelled');
  };
  try {
    check();
    const who = await s.login(persona);
    check();
    if (who.sub !== persona) throw new ApiError(0, 'identity');
    const res = await ask(question);
    check();
    // The answer names nobody, so ask the slot who it was signed in as.
    if ((await s.whoami()).sub !== who.sub) throw new ApiError(0, 'identity');
    check();
    return { kind: 'done', persona, sub: who.sub, name: who.name || personaById(persona)?.name || persona, question, res };
  } catch (e) {
    return { kind: 'error', persona, question, status: e instanceof ApiError ? e.status : 0 };
  }
}

const idle = (persona: string, question: string): Side => ({ kind: 'idle', persona, question });

/**
 * How a run ended: 'ok' both columns verified; 'failed' it ran and at least one column did not
 * come through (or the question was not askable); 'busy' another run is in progress (not started);
 * 'cancelled' superseded by Reset or unmount (its results were dropped).
 */
type RunOutcome = 'ok' | 'failed' | 'busy' | 'cancelled';

/** Presenter handler answer: busy is "not ready yet"; everything else is final. */
const toHandler = (o: RunOutcome): HandlerResult => (o === 'ok' ? true : o === 'busy' ? false : 'failed');

interface SheetState {
  slot: SlotId;
  index: number;
  opener: HTMLElement | null;
}

export function CompareView() {
  const t = useMessages(presenterMessages);
  const role = useRole();
  const router = useRouter();
  const params = useSearchParams();
  const { me } = useSession();
  const paramsKey = params?.toString() ?? '';
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const urlSpec = useMemo(() => parseSpec(params ? new URLSearchParams(paramsKey) : null), [paramsKey]);

  const [draft, setDraft] = useState<CompareSpec>(urlSpec);
  const [asked, setAsked] = useState<CompareSpec | null>(null);
  const [left, setLeft] = useState<Side>(idle(urlSpec.left, urlSpec.q1));
  const [right, setRight] = useState<Side>(idle(urlSpec.right, urlSpec.q2));
  const [busy, setBusy] = useState(false);
  const [announce, setAnnounce] = useState('');
  const [sheet, setSheet] = useState<SheetState | null>(null);
  // One staged progress line per column, each on its own slot (stage events when the IB streams).
  const askB = useAskProgress({ slot: 'b' });
  const askC = useAskProgress({ slot: 'c' });
  const progB = useRef(askB);
  const progC = useRef(askC);
  progB.current = askB;
  progC.current = askC;
  const lastPeopleRight = useRef(urlSpec.mode === 'people' ? urlSpec.right : 'ken.ext');

  // Latest values for the Presenter handlers (they run outside React's render cycle).
  const busyRef = useRef(false);
  /** The current run's generation; bumped by every run, by cancel() and on unmount. */
  const runGen = useRef(0);
  const urlRef = useRef(urlSpec);
  const askedRef = useRef(asked);
  const sidesRef = useRef({ b: left, c: right });
  urlRef.current = urlSpec;
  askedRef.current = asked;
  sidesRef.current = { b: left, c: right };

  // The URL is the source of truth for what is being compared. Coming back to a comparison that
  // already finished in this tab restores its answers (nothing is asked again).
  useEffect(() => {
    setDraft(urlSpec);
    if (urlSpec.mode === 'people') lastPeopleRight.current = urlSpec.right;
    if (busyRef.current) return;
    const c = cachedResult(urlSpec);
    setSheet(null);
    if (c) {
      setAsked(urlSpec);
      setLeft(c.left);
      setRight(c.right);
      // The status line must describe THIS result, not the previous run (e.g. Ken's "identical").
      const same = c.left.kind === 'done' && c.right.kind === 'done' && isRefusal(c.left.res) && isRefusal(c.right.res) && sameResponse(c.left.res, c.right.res);
      setAnnounce(t(same ? 'cmp.announce.identical' : 'cmp.announce.done'));
    } else {
      setAsked(null);
      setAnnounce('');
    }
    // t is stable per language; re-announcing on a language flip is not wanted
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlSpec]);

  const run = useCallback(
    async (s: CompareSpec): Promise<RunOutcome> => {
      if (busyRef.current) return 'busy';
      const q1 = s.q1.trim();
      const q2 = s.q2.trim();
      if (!q1 || !q2 || q1.length > MAX_Q || q2.length > MAX_Q) return 'failed';
      const gen = ++runGen.current;
      const live = () => runGen.current === gen;
      busyRef.current = true;
      setBusy(true);
      setSheet(null);
      const spec = { ...s, q1, q2 };
      // Whatever an earlier run (or rehearsal) left for these people is no longer current.
      forgetRequests([spec.left, spec.right]);
      forgetResult(spec);
      setAsked(spec);
      if (!sameSpec(spec, urlRef.current)) router.replace(specUrl(spec), { scroll: false });
      setAnnounce(t('cmp.announce.asking'));
      const loadingL: Side = { kind: 'loading', persona: spec.left, question: q1 };
      const loadingR: Side = { kind: 'loading', persona: spec.right, question: q2 };
      setLeft(loadingL);
      setRight(loadingR);
      sidesRef.current = { b: loadingL, c: loadingR };
      let sides: [Side, Side];
      try {
        sides = await withSlotLocks(['b', 'c'], () =>
          Promise.all([askSide('b', spec.left, q1, progB.current.run, live), askSide('c', spec.right, q2, progC.current.run, live)]),
        );
      } catch {
        // Only the lock itself can throw here (no Web Locks: not a secure context).
        sides = [
          { kind: 'error', persona: spec.left, question: q1, status: 0 },
          { kind: 'error', persona: spec.right, question: q2, status: 0 },
        ];
      }
      // Superseded (Reset, unmount): cancel() already handed the page back; drop these results.
      if (!live()) return 'cancelled';
      const [l, r] = sides;
      setLeft(l);
      setRight(r);
      sidesRef.current = { b: l, c: r };
      const ok = l.kind === 'done' && r.kind === 'done';
      if (l.kind === 'done' && r.kind === 'done') {
        cacheResult(spec, l, r);
        rememberRequest(l.persona, l.res.request_id);
        rememberRequest(r.persona, r.res.request_id);
        const same = isRefusal(l.res) && isRefusal(r.res) && sameResponse(l.res, r.res);
        setAnnounce(t(same ? 'cmp.announce.identical' : 'cmp.announce.done'));
      } else {
        setAnnounce(t('cmp.announce.failed'));
      }
      busyRef.current = false;
      setBusy(false);
      return ok ? 'ok' : 'failed';
    },
    [router, t],
  );

  /** Let go of the running comparison: abort its asks, drop its results, free the page. */
  const cancel = useCallback(() => {
    runGen.current += 1;
    progB.current.reset();
    progC.current.reset();
    if (!busyRef.current) return;
    busyRef.current = false;
    setBusy(false);
    setSheet(null);
    setAsked(null);
    setAnnounce('');
  }, []);

  // Unmount: nothing a late answer could update is left (useAskProgress aborts its own fetch).
  useEffect(
    () => () => {
      runGen.current += 1;
      busyRef.current = false;
    },
    [],
  );

  useTourAction('compare-run', () => void run(draft));

  /* ---- Presenter hooks (components/presenter/script.ts) ---- */
  usePresenterHandler('compare-run', async (arg) => {
    const s = arg as CompareSpec;
    if (busyRef.current || !sameSpec(s, urlRef.current)) return false; // not ready yet
    return toHandler(await run(s));
  });

  // Presenter Reset: a pending run is superseded and its results are never shown or remembered.
  usePresenterHandler('compare-cancel', () => {
    cancel();
    return true;
  });

  const openCitation = useCallback((slot: SlotId, index: number): boolean => {
    const side = sidesRef.current[slot];
    if (side.kind !== 'done' || index < 0 || index >= side.res.citations.length) return false;
    const tag = side.res.citations[index].tag;
    const chip = document.querySelector<HTMLElement>(`[data-compare-col="${slot}"] [data-testid="citation-chip"][data-tag="${tag}"]`);
    setSheet({ slot, index, opener: chip });
    return true;
  }, []);

  usePresenterHandler('compare-open-citation', async (arg): Promise<HandlerResult> => {
    const a = (arg ?? {}) as { spec?: CompareSpec; slot?: SlotId; n?: number };
    if (busyRef.current) return false;
    if (a.spec) {
      if (!sameSpec(a.spec, urlRef.current)) return false;
      const shown = askedRef.current;
      const sides = sidesRef.current;
      const showsDone = !!shown && sameSpec(shown, a.spec) && sides.b.kind === 'done' && sides.c.kind === 'done';
      if (!showsDone) {
        // Being restored by the URL effect (a finished run of this comparison): try again.
        if (cachedResult(a.spec) && !(shown && sameSpec(shown, a.spec))) return false;
        // Not on screen, or an earlier run of it failed: ask now; a failure fails the step.
        const o = await run(a.spec);
        if (o !== 'ok') return toHandler(o);
      }
    }
    // The focused column wins (Enter on a chip works the same); else the one the step names.
    const focused = document.activeElement?.closest('[data-compare-col]')?.getAttribute('data-compare-col');
    const slot: SlotId = focused === 'b' || focused === 'c' ? focused : a.slot ?? 'c';
    const opened = openCitation(slot, (a.n ?? 2) - 1);
    // With a spec the answers are known to be on screen: a missing source [n] is a real failure.
    return opened ? true : a.spec ? 'failed' : false;
  });

  const setMode = (m: CompareMode) => {
    setDraft((d) => {
      if (m === d.mode) return d;
      if (m === 'person') {
        lastPeopleRight.current = d.right;
        return { mode: 'person', left: d.left, right: d.left, q1: d.q1, q2: d.q2 };
      }
      return { mode: 'people', left: d.left, right: lastPeopleRight.current, q1: d.q1, q2: d.q1 };
    });
  };

  if (!DEMO_COMPARE) {
    return (
      <Page title={t('cmp.title.people')} width="narrow">
        <div className="rounded-xl border border-line bg-surface p-6">
          <p className="text-body font-medium">{t('cmp.disabled.title')}</p>
          <p className="mt-1 text-body-sm text-muted">{t('cmp.disabled.body')}</p>
          <Button asChild size="sm" className="mt-3">
            <Link href="/">{t('cmp.disabled.cta')}</Link>
          </Button>
        </div>
      </Page>
    );
  }

  const shown: CompareSpec = asked ?? draft;
  const lSide: Side = asked ? left : idle(draft.left, draft.q1);
  const rSide: Side = asked ? right : idle(draft.right, draft.q2);
  const identical = lSide.kind === 'done' && rSide.kind === 'done' && isRefusal(lSide.res) && isRefusal(rSide.res) && sameResponse(lSide.res, rSide.res);
  const settled = !busy && (lSide.kind === 'done' || lSide.kind === 'error') && (rSide.kind === 'done' || rSide.kind === 'error');
  const label = (id: string) => `${personaById(id)?.name ?? id} — ${role(id)}`;
  const canAsk = !!draft.q1.trim() && !!draft.q2.trim();
  const sheetSide = sheet ? (sheet.slot === 'b' ? lSide : rSide) : null;
  const sheetDone = sheetSide && sheetSide.kind === 'done' ? sheetSide : null;

  return (
    <Page
      eyebrow={t('cmp.eyebrow')}
      title={t(shown.mode === 'person' ? 'cmp.title.person' : 'cmp.title.people')}
      description={<span data-stage-hide>{t(shown.mode === 'person' ? 'cmp.desc.person' : 'cmp.desc.people')}</span>}
      width="wide"
    >
      <form
        className="mb-8 space-y-4 rounded-xl border border-line bg-surface p-4 sm:p-5"
        aria-label={t('cmp.mode')}
        data-tour="compare-form"
        data-stage-hide
        onSubmit={(e) => {
          e.preventDefault();
          void run(draft);
        }}
      >
        <SegmentedControl<CompareMode>
          label={t('cmp.mode')}
          value={draft.mode}
          onChange={setMode}
          testId="compare-mode"
          options={[
            { value: 'people', label: t('cmp.mode.people') },
            { value: 'person', label: t('cmp.mode.person') },
          ]}
        />
        {draft.mode === 'people' ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field id="cmp-a" label={t('cmp.firstPerson')}>
                <select id="cmp-a" value={draft.left} onChange={(e) => setDraft({ ...draft, left: e.target.value })} className={inputClass} disabled={busy}>
                  {CHOICES.map((p) => (
                    <option key={p.id} value={p.id}>
                      {label(p.id)} ({p.id})
                    </option>
                  ))}
                </select>
              </Field>
              <Field id="cmp-b" label={t('cmp.secondPerson')}>
                <select id="cmp-b" value={draft.right} onChange={(e) => setDraft({ ...draft, right: e.target.value })} className={inputClass} disabled={busy}>
                  {CHOICES.map((p) => (
                    <option key={p.id} value={p.id}>
                      {label(p.id)} ({p.id})
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field id="cmp-q" label={t('cmp.question')}>
              <textarea
                id="cmp-q"
                value={draft.q1}
                onChange={(e) => setDraft({ ...draft, q1: e.target.value, q2: e.target.value })}
                rows={2}
                maxLength={MAX_Q}
                className="block w-full resize-y rounded-md border border-line-strong bg-bg px-3 py-2 text-body-sm leading-6 text-fg"
              />
            </Field>
          </>
        ) : (
          <>
            <div className="sm:max-w-[calc(50%-0.375rem)]">
              <Field id="cmp-p" label={t('cmp.person')}>
                <select id="cmp-p" value={draft.left} onChange={(e) => setDraft({ ...draft, left: e.target.value, right: e.target.value })} className={inputClass} disabled={busy}>
                  {CHOICES.map((p) => (
                    <option key={p.id} value={p.id}>
                      {label(p.id)} ({p.id})
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field id="cmp-q1" label={t('cmp.leftQuestion')}>
                <textarea
                  id="cmp-q1"
                  value={draft.q1}
                  onChange={(e) => setDraft({ ...draft, q1: e.target.value })}
                  rows={2}
                  maxLength={MAX_Q}
                  className="block w-full resize-y rounded-md border border-line-strong bg-bg px-3 py-2 text-body-sm leading-6 text-fg"
                />
              </Field>
              <Field id="cmp-q2" label={t('cmp.rightQuestion')}>
                <textarea
                  id="cmp-q2"
                  value={draft.q2}
                  onChange={(e) => setDraft({ ...draft, q2: e.target.value })}
                  rows={2}
                  maxLength={MAX_Q}
                  className="block w-full resize-y rounded-md border border-line-strong bg-bg px-3 py-2 text-body-sm leading-6 text-fg"
                />
              </Field>
            </div>
          </>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-caption text-muted">{t('cmp.ledgerNote')}</p>
          <Button type="submit" variant="primary" icon={Users} busy={busy} disabled={!canAsk}>
            {busy ? t('cmp.asking') : t('cmp.askBoth')}
          </Button>
        </div>
      </form>

      {/* Stage only: the shared question, large, where the form was. */}
      {shown.mode === 'people' ? (
        <div className="mb-8 hidden" data-stage-only data-testid="compare-shared-question">
          <p className="text-micro font-semibold uppercase tracking-wider text-subtle">{t('cmp.askedByBoth')}</p>
          <p className="mt-1 max-w-5xl text-title text-fg">“{shown.q1}”</p>
        </div>
      ) : null}

      <div role="status" aria-live="polite" className="sr-only" data-testid="compare-announce">
        {announce}
      </div>

      <div
        className={cn('grid gap-6 lg:gap-x-6', 'lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]')}
        data-testid="compare-results"
        data-tour={settled ? 'compare-results' : undefined}
        aria-busy={busy}
      >
        <CompareColumn slot="b" side={lSide} progress={askB.step} mode={shown.mode} onCite={(i, el) => setSheet({ slot: 'b', index: i, opener: el ?? null })} />
        {identical ? <EqualityMarker /> : <div aria-hidden className="hidden lg:block" />}
        <CompareColumn slot="c" side={rSide} progress={askC.step} mode={shown.mode} onCite={(i, el) => setSheet({ slot: 'c', index: i, opener: el ?? null })} />
      </div>

      {!asked ? (
        <p className="mt-6 text-body-sm text-muted" data-stage-hide data-testid="compare-empty">
          {t(draft.mode === 'person' ? 'cmp.empty.person' : 'cmp.empty.people')}
        </p>
      ) : null}

      {sheetDone && me && me.sub === sheetDone.sub ? (
        // The main session IS this column's person: the User-mode sheet (live passage via /ib/doc).
        <SourcesSheet
          response={sheetDone.res}
          index={sheet!.index}
          onIndexChange={(i) => setSheet((s) => (s ? { ...s, index: i } : s))}
          onClose={() => setSheet(null)}
          returnFocusTo={sheet?.opener ?? null}
        />
      ) : (
        // Someone else's column: never fetch under the main session; show what they were shown.
        <CompareSourceSheet
          response={sheetDone?.res ?? null}
          index={sheetDone ? sheet!.index : null}
          personName={sheetDone?.name ?? ''}
          canFetchPassage={false}
          onIndexChange={(i) => setSheet((s) => (s ? { ...s, index: i } : s))}
          onClose={() => setSheet(null)}
          returnFocusTo={sheet?.opener ?? null}
        />
      )}
    </Page>
  );
}
