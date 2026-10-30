// The two tours as data. Each step names where it lives (route), what to highlight (targets, in
// priority order: the first one on screen wins, so a step's highlight follows the visitor's
// progress, e.g. the starter card, then the pending question, then the answer), what "Do it for
// me" does, and when the step counts as done. The overlay (Tour.tsx) runs them.
//
// Anchors are `data-tour="…"` attributes on the real pages (plus a few stable test ids).
import { personUrl } from '@/components/compare/params';
import { Q_BAYFRONT, Q_VELQUARRY } from '@/components/presenter/script';
import { analyticsApi } from '@/lib/api';
import type { TourKey } from '@/lib/i18n/tour';
import { TOUR_LENGTH, emitTourAction, lastRequest, type ChecklistItem, type TourId } from '@/lib/tour';
import type { WhoAmI } from '@/lib/types';

export interface TourCtx {
  me: WhoAmI | null;
  compliance: boolean;
  pathname: string;
  /** Client-side navigation. */
  go: (url: string) => void;
  /** Make the MAIN session this persona (the normal demo sign-in) and refresh the shell. */
  signIn: (persona: string) => Promise<void>;
  /** Wait for a visible element (null on timeout or when the step was left). */
  waitFor: (selector: string, ms?: number) => Promise<HTMLElement | null>;
}

export interface Target {
  sel: string;
  /** Accessible name of the highlighted region. */
  spot: TourKey;
}

export interface StepAction {
  /** What it does, completing "Do it for me: …" (the accessible name). */
  what: TourKey;
  run: (c: TourCtx) => Promise<void>;
}

export interface TourStep {
  id: string;
  title: (c: TourCtx) => TourKey;
  body: (c: TourCtx) => TourKey;
  /** Body once the step is done (defaults to body). */
  doneBody?: (c: TourCtx) => TourKey;
  /** Where the step lives; null = stay on the current page. Resolved once when the step opens. */
  route?: (c: TourCtx) => string | null | Promise<string | null>;
  targets: (c: TourCtx) => Target[];
  /** "Do it for me", when the step needs an action (null when there is nothing left to do). */
  act?: (c: TourCtx) => StepAction | null;
  /** Polled while the step is open. */
  done?: (c: TourCtx) => boolean;
  /** A click inside this selector completes the step. */
  doneOnClick?: string;
  /** Move on by itself when it becomes done (only for steps the visitor completes by leaving the page). */
  autoAdvance?: boolean;
  /** No highlight is expected on some screens: centre the card with the normal body, no "missing" note. */
  targetOptional?: (c: TourCtx) => boolean;
  /** Re-open the step (route, targets) when the signed-in person changes. */
  reenterOnPersona?: boolean;
  /** Prefer placing the card beside the highlight (e.g. to the left of a side sheet). */
  preferSide?: boolean;
  /** Checklist item ticked when the step is done. */
  check?: ChecklistItem;
}

const q = (sel: string) => (typeof document === 'undefined' ? null : document.querySelector<HTMLElement>(sel));
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Set a React-controlled input's value as if typed. */
function typeInto(input: HTMLInputElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  input.focus();
  setter?.call(input, text);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

const ANSWER = '[data-tour="answer"]';
const FIRST_CHIP = `${ANSWER} [data-testid="citation-chip"]`;
const SHEET = '[data-testid="sources-sheet"]';
const CANVAS = '[data-testid="view-canvas"][data-open="true"]';
const CANVAS_SHEET = '[data-testid="view-canvas-sheet"]';
const OPEN_PAGE = '[data-testid="view-open-page"]';
const SHOW_PAGE = '[data-testid="view-show"]';
const CHANGE_FORM = '[data-testid="view-change"]';
const CHANGE_INPUT = '[data-testid="view-change-input"]';
const CHANGE_SEND = '[data-testid="view-change-send"]';
/** The change the "Change it in plain words" step types (a layout request; it adds no values). */
export const TOUR_PAGE_CHANGE = 'make the table black';
const KEN_URL = personUrl('ken.ext', Q_VELQUARRY, Q_BAYFRONT);
const GRAPH_TOPICS = ['migration', 'audit', 'policy', 'incident', 'runbook'];

/* ------------------------------------------------------------------ the page beside the chat */

/** The page is open: the canvas beside the chat (>= 1024 px) or its full-screen sheet (narrower). */
const canvasOpen = () => !!q(CANVAS) || !!q(CANVAS_SHEET);
const canvasBusy = () => !!q(`${CANVAS} [data-testid="view-loading"]`) || !!q(`${CANVAS_SHEET} [data-testid="view-loading"]`);

// A change sent from the canvas's change box in this page load (the tour's "change" step is done
// once it was sent and the rebuilt page has arrived). A change typed in the chat shows a page-edit row.
let pageChangeSent = false;
if (typeof document !== 'undefined') {
  document.addEventListener(
    'submit',
    (e) => {
      if (e.target instanceof Element && e.target.closest(CHANGE_FORM)) pageChangeSent = true;
    },
    true,
  );
}

/** Close the sources sheet left open by the previous step (its own close button), so the page shows. */
async function closeSourcesSheet() {
  const close = q(`${SHEET} > div:first-child > button:last-child`);
  if (!close) return;
  close.click();
  for (let i = 0; i < 20 && q(SHEET); i++) await sleep(50);
  await sleep(150);
}

/** Open the latest answer as a page (asking the sample question first when there is no answer). */
async function openPage(c: TourCtx) {
  if (canvasOpen()) return;
  await closeSourcesSheet();
  if (!q(ANSWER)) {
    if (!(await c.waitFor('[data-tour="ask-form"]', 10_000))) throw new Error('ask-form');
    await sleep(120);
    emitTourAction('ask-sample');
    if (!(await c.waitFor(ANSWER, 60_000))) throw new Error('answer');
  }
  if (canvasOpen()) return; // the answer may open its page by itself
  if (!(await c.waitFor(`${SHOW_PAGE}, ${OPEN_PAGE}`, 10_000))) throw new Error('open-page');
  // the newest answer's control (the last one on the page)
  const all = document.querySelectorAll<HTMLElement>(`${SHOW_PAGE}, ${OPEN_PAGE}`);
  pageChangeSent = false;
  all[all.length - 1]?.click();
  for (let i = 0; i < 40 && !canvasOpen(); i++) await sleep(50);
  if (!canvasOpen()) throw new Error('canvas');
}

/* ------------------------------------------------------------------ User mode: "Try it" */

export const USER_STEPS: TourStep[] = [
  {
    id: 'signin',
    title: () => 'u.signin.title',
    body: () => 'u.signin.body',
    doneBody: () => 'u.signin.doneBody',
    route: (c) => (c.me?.sub === 'jdoe' && c.pathname !== '/login' ? null : '/login'),
    targets: () => [
      { sel: '[data-persona="jdoe"]', spot: 'u.signin.spot' },
      { sel: 'button:has([data-testid="whoami"])', spot: 'u.signin.spotMenu' },
    ],
    act: (c) =>
      c.me?.sub === 'jdoe' && c.pathname !== '/login'
        ? null
        : {
            what: 'u.signin.act',
            run: async (cx) => {
              await cx.signIn('jdoe');
              cx.go('/');
            },
          },
    done: (c) => c.me?.sub === 'jdoe' && c.pathname !== '/login',
    autoAdvance: true,
  },
  {
    id: 'ask',
    title: () => 'u.ask.title',
    body: () => 'u.ask.body',
    doneBody: () => 'u.ask.doneBody',
    route: () => '/',
    targets: () => [
      { sel: ANSWER, spot: 'u.ask.spotAnswer' },
      { sel: '[data-turn="pending"]', spot: 'u.ask.spotPending' },
      { sel: '[data-testid="starter"]', spot: 'u.ask.spotStarter' },
      { sel: '[data-tour="ask-form"]', spot: 'u.ask.spotForm' },
    ],
    act: () =>
      q(ANSWER) || q('[data-turn="pending"]')
        ? null
        : {
            what: 'u.ask.act',
            run: async (c) => {
              if (!(await c.waitFor('[data-tour="ask-form"]', 10_000))) throw new Error('ask-form');
              await sleep(120); // let the page attach its action listener
              emitTourAction('ask-sample');
            },
          },
    done: () => !!q(ANSWER) || !!q('[data-testid="refusal-card"]'),
    check: 'asked',
  },
  {
    id: 'source',
    title: () => 'u.cite.title',
    body: () => 'u.cite.body',
    doneBody: () => 'u.cite.doneBody',
    route: () => '/',
    targets: () => [
      { sel: `${SHEET} [data-testid="sheet-excerpt"]`, spot: 'u.cite.spotPassage' },
      { sel: SHEET, spot: 'u.cite.spotSheet' },
      { sel: FIRST_CHIP, spot: 'u.cite.spotChip' },
      { sel: '[data-turn="pending"]', spot: 'u.ask.spotPending' },
      { sel: '[data-testid="starter"]', spot: 'u.ask.spotStarter' },
      { sel: '[data-tour="ask-form"]', spot: 'u.ask.spotForm' },
    ],
    act: () => {
      if (q(SHEET)) return null;
      if (q(FIRST_CHIP))
        return {
          what: 'u.cite.act',
          run: async () => {
            q(FIRST_CHIP)?.click();
          },
        };
      if (q('[data-turn="pending"]')) return null;
      // No answer on screen (the visitor skipped the ask): ask first, then the chip appears.
      return {
        what: 'u.ask.act',
        run: async (c) => {
          if (!(await c.waitFor('[data-tour="ask-form"]', 10_000))) throw new Error('ask-form');
          await sleep(120);
          emitTourAction('ask-sample');
        },
      };
    },
    done: () => !!q(SHEET),
    preferSide: true,
    check: 'source',
  },
  {
    id: 'page',
    title: () => 'u.page.title',
    body: () => 'u.page.body',
    doneBody: () => 'u.page.doneBody',
    route: () => '/',
    targets: () => [
      { sel: CANVAS, spot: 'u.page.spotCanvas' },
      { sel: CANVAS_SHEET, spot: 'u.page.spotCanvas' },
      { sel: OPEN_PAGE, spot: 'u.page.spotButton' },
      { sel: SHOW_PAGE, spot: 'u.page.spotButton' },
      { sel: ANSWER, spot: 'u.ask.spotAnswer' },
      { sel: '[data-tour="ask-form"]', spot: 'u.ask.spotForm' },
    ],
    act: () =>
      canvasOpen()
        ? null
        : {
            what: 'u.page.act',
            run: openPage,
          },
    done: canvasOpen,
  },
  {
    id: 'change',
    title: () => 'u.change.title',
    body: () => 'u.change.body',
    doneBody: () => 'u.change.doneBody',
    route: () => '/',
    targets: () => [
      { sel: CHANGE_FORM, spot: 'u.change.spotBox' },
      { sel: CANVAS, spot: 'u.page.spotCanvas' },
      { sel: CANVAS_SHEET, spot: 'u.page.spotCanvas' },
      { sel: OPEN_PAGE, spot: 'u.page.spotButton' },
      { sel: SHOW_PAGE, spot: 'u.page.spotButton' },
      { sel: '[data-tour="ask-form"]', spot: 'u.ask.spotForm' },
    ],
    act: () => ({
      what: 'u.change.act',
      run: async (c) => {
        await openPage(c);
        // wait for the page (the change box is disabled while it is being built)
        for (let i = 0; i < 600 && canvasBusy(); i++) await sleep(100);
        const input = (await c.waitFor(CHANGE_INPUT, 10_000)) as HTMLInputElement | null;
        if (!input || input.disabled) throw new Error('change-input');
        typeInto(input, TOUR_PAGE_CHANGE);
        await sleep(120); // let React enable the send button
        const send = q(CHANGE_SEND) as HTMLButtonElement | null;
        if (!send || send.disabled) throw new Error('change-send');
        send.click();
      },
    }),
    done: () => (pageChangeSent || !!q('[data-testid="page-edit"]')) && canvasOpen() && !canvasBusy(),
  },
  {
    id: 'ken',
    title: () => 'u.ken.title',
    body: () => 'u.ken.body',
    doneBody: () => 'u.ken.doneBody',
    route: () => KEN_URL,
    targets: () => [
      { sel: '[data-tour="compare-results"]', spot: 'u.ken.spotResults' },
      { sel: '[data-testid="compare-results"][aria-busy="true"]', spot: 'u.ken.spotResults' },
      { sel: '[data-tour="compare-form"]', spot: 'u.ken.spotForm' },
    ],
    act: () =>
      q('[data-tour="compare-results"]') || q('[data-testid="compare-results"][aria-busy="true"]')
        ? null
        : {
            what: 'u.ken.act',
            run: async (c) => {
              if (!(await c.waitFor('[data-tour="compare-form"]', 10_000))) throw new Error('compare-form');
              await sleep(200); // the form takes its values from the URL after mounting
              emitTourAction('compare-run');
            },
          },
    done: () => !!q('[data-testid="identical-marker"]'),
    check: 'identical',
  },
];

/* ------------------------------------------------------------------ Developer mode */

async function journeyRoute(c: TourCtx): Promise<string> {
  const mine = lastRequest();
  if (mine && c.me && (mine.sub === c.me.sub || c.compliance)) return `/dev/journey/${encodeURIComponent(mine.req)}`;
  try {
    const h = await analyticsApi.history();
    const first = h.items?.[0]?.req_id;
    if (first) return `/dev/journey/${encodeURIComponent(first)}`;
  } catch {
    /* fall back to the list */
  }
  return '/dev/journey';
}

export const DEV_STEPS: TourStep[] = [
  {
    id: 'mission',
    title: () => 'd.mission.title',
    body: () => 'd.mission.body',
    doneBody: () => 'd.mission.doneBody',
    route: () => '/dev',
    targets: () => [
      { sel: '[data-tour="mission-pipeline"]', spot: 'd.mission.spot' },
      { sel: '[data-testid="mission-pipeline"]', spot: 'd.mission.spot' },
    ],
    act: () => ({
      what: 'd.mission.act',
      run: async (c) => {
        const node = await c.waitFor('[data-testid="pipeline-node-gate2"]', 10_000);
        if (!node) throw new Error('gate2');
        node.click();
      },
    }),
    doneOnClick: '[data-testid="pipeline-node-gate2"]',
  },
  {
    id: 'journey',
    title: () => 'd.journey.title',
    body: (c) => (c.pathname.startsWith('/dev/journey/') ? 'd.journey.body' : 'd.journey.bodyList'),
    route: journeyRoute,
    targets: () => [
      { sel: '[data-tour="journey-waterfall"]', spot: 'd.journey.spot' },
      { sel: '[data-tour="journey-receipt"]', spot: 'd.journey.spot' },
      { sel: '[data-testid="journey-compliance"]', spot: 'd.journey.spot' },
      { sel: '[data-testid="journey-receipt"]', spot: 'd.journey.spot' },
      { sel: '[data-testid="recent-requests"]', spot: 'd.journey.spotList' },
    ],
  },
  {
    id: 'graph',
    title: () => 'd.graph.title',
    body: () => 'd.graph.body',
    doneBody: () => 'd.graph.doneBody',
    route: () => '/dev/graph',
    targets: () => [
      { sel: '[data-testid="graph-main-panel"]', spot: 'd.graph.spotPanel' },
      { sel: '[data-tour="graph-search"]', spot: 'd.graph.spotSearch' },
    ],
    act: () =>
      q('[data-testid="graph-main-panel"]')
        ? null
        : {
            what: 'd.graph.act',
            run: async (c) => {
              const input = (await c.waitFor('[data-testid="graph-main-search"]', 20_000)) as HTMLInputElement | null;
              if (!input) throw new Error('graph-search');
              // Topics in the synthetic estate; each person's graph holds only what they can open,
              // so try a few, first on the canvas, then with the server's own search.
              for (const topic of GRAPH_TOPICS) {
                typeInto(input, topic);
                let option = await c.waitFor('[data-testid="graph-search-option"]', 1_500);
                if (!option) {
                  const server = await c.waitFor('[data-testid="graph-search-server"]', 1_500);
                  if (server) {
                    server.click();
                    option = await c.waitFor('[data-testid="graph-search-option"]', 6_000);
                  }
                }
                if (option) {
                  option.click();
                  return;
                }
              }
              throw new Error('graph-option');
            },
          },
    done: () => !!q('[data-testid="graph-main-panel"]'),
  },
  {
    id: 'trust',
    title: () => 'd.trust.title',
    body: () => 'd.trust.body',
    route: () => '/dev/trust',
    targets: () => [
      { sel: '[data-tour="trust-controls"]', spot: 'd.trust.spot' },
      { sel: '[data-testid="trust-controls"]', spot: 'd.trust.spot' },
    ],
  },
  {
    id: 'compliance',
    title: (c) => (c.compliance ? 'd.comp.title' : 'd.locked.title'),
    body: (c) => (c.compliance ? 'd.comp.body' : 'd.locked.body'),
    doneBody: (c) => (c.compliance ? 'd.comp.doneBody' : 'd.locked.body'),
    route: (c) => (c.compliance ? '/dev/analytics' : null),
    targets: (c) =>
      c.compliance
        ? [
            { sel: '[data-testid="verify-status"]', spot: 'd.comp.spotVerify' },
            { sel: '[data-tour="verify-panel"]', spot: 'd.comp.spotVerify' },
            { sel: '[data-tour="analytics-kpis"]', spot: 'd.comp.spotKpis' },
            { sel: '[data-testid="analytics-kpis"]', spot: 'd.comp.spotKpis' },
          ]
        : [{ sel: 'nav [data-nav="dev-audit"]', spot: 'd.locked.spot' }],
    act: (c) =>
      c.compliance
        ? q('[data-testid="verify-status"]')
          ? null
          : {
              what: 'd.comp.act',
              run: async (cx) => {
                if (cx.pathname.startsWith('/dev/audit') && q('[data-tour="verify-panel"]')) emitTourAction('verify');
                else cx.go('/dev/audit?verify=1');
              },
            }
        : {
            what: 'd.locked.act',
            run: async (cx) => {
              await cx.signIn('maya.compliance');
            },
          },
    done: (c) => c.compliance && !!q('[data-testid="verify-status"]'),
    targetOptional: (c) => !c.compliance,
    reenterOnPersona: true,
  },
];

export const STEPS: Record<TourId, TourStep[]> = { user: USER_STEPS, dev: DEV_STEPS };

// The store and the step lists must agree on the lengths.
if (USER_STEPS.length !== TOUR_LENGTH.user || DEV_STEPS.length !== TOUR_LENGTH.dev) {
  throw new Error('components/tour/steps.ts and lib/tour.ts TOUR_LENGTH disagree');
}
