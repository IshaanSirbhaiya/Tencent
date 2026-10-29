// The Demo Day script as data (docs/finals/rubric/demo_story/RUN_SHEET.md, PRESENTER_KEYS.md).
// One step per Space press. `action` runs on the way forward; going back only re-opens `url`
// (Compare restores its earlier results instead of asking again).
//
// Questions are the exact strings from lib/personas.ts (Amy's suggestions); the e2e spec checks
// that they are still there, so the script cannot drift from the personas.
import { peopleUrl, personUrl, type CompareSpec } from '@/components/compare/params';
import { SAMPLE_QUESTION } from '@/lib/personas';
import type { PresenterKey } from '@/lib/i18n/presenter';

export const Q_VELQUARRY = "Why has Velquarry Trading's account been restricted, and what should I tell the customer?";
export const Q_BAYFRONT = "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?";
/** What Aaruni asks in WorkBuddy as Jordan (Jordan's canonical question in lib/personas.ts). */
export const Q_WORKBUDDY = SAMPLE_QUESTION;

export const SPEC_PEOPLE: CompareSpec = { mode: 'people', left: 'amy.am', right: 'mlro.tan', q1: Q_VELQUARRY, q2: Q_VELQUARRY };
export const SPEC_KEN: CompareSpec = { mode: 'person', left: 'ken.ext', right: 'ken.ext', q1: Q_VELQUARRY, q2: Q_BAYFRONT };

/** Backup recordings (docs/finals/rubric/demo_story/BACKUP_PLAN.md), served from public/clips/. */
export const CLIPS = {
  A: 'clipA_compare_amy_rachel_ken.mp4',
  B: 'clipB_journey_audit_tamper.mp4',
  C: 'clipC_workbuddy_jordan.mp4',
} as const;

export interface StepCtx {
  /** Client-side navigation; resolves once the route change was requested. */
  go: (url: string) => Promise<void>;
  /** Call a page handler (components/presenter/bus.ts); false when it never accepted. */
  call: (name: string, arg?: unknown, timeoutMs?: number) => Promise<boolean>;
  /** Make the MAIN session this persona (demo sign-in; identity still comes from the signed token). */
  signInMain: (persona: string) => Promise<void>;
  /** The latest request id a Compare column produced for this persona, if any. */
  req: (persona: string) => string | null;
}

export type StepId =
  | 'title'
  | 'compare-people'
  | 'compare-person'
  | 'citation'
  | 'journey'
  | 'verify'
  | 'tamper'
  | 'workbuddy'
  | 'proof'
  | 'business'
  | 'close';

export interface PresenterStep {
  id: StepId;
  /** Short English title (the bar shows the translated `step.<id>`). */
  title: string;
  /** Speaker cue: who speaks and the first words of the line. Never shown in the Stage theme. */
  say: string;
  /** Where the step lives; a function when it depends on earlier steps. */
  url: string | ((ctx: StepCtx) => string);
  /** File in public/clips/ that `B` plays, or null (slides need no recording). */
  clip: string | null;
  /** The step happens outside the app (the bar shows a card instead). */
  offApp?: boolean;
  action: (ctx: StepCtx) => Promise<void>;
}

const fail = (what: string) => {
  throw new Error(what);
};

const journeyUrl = (ctx: StepCtx) => {
  const req = ctx.req('mlro.tan');
  return req ? `/dev/journey/${encodeURIComponent(req)}` : '/dev/journey';
};

export const STEPS: PresenterStep[] = [
  {
    id: 'title',
    title: 'Title',
    say: 'Ishaan: "Two people at a fintech ask their company AI the same question: why has Velquarry Trading\'s account been restricted?" Then "Watch."',
    url: '/present',
    clip: CLIPS.A,
    // The main session is Rachel, so her sources open with the live passage in step 3.
    action: async (ctx) => {
      await ctx.signInMain('mlro.tan');
      await ctx.go('/present');
    },
  },
  {
    id: 'compare-people',
    title: 'Compare: Amy vs Rachel on Velquarry',
    say: 'Ishaan: "Amy gets the approved customer script, cited, and nothing else. Rachel, the money-laundering officer, gets the case."',
    url: peopleUrl(SPEC_PEOPLE.left, SPEC_PEOPLE.right, SPEC_PEOPLE.q1),
    clip: CLIPS.A,
    action: async (ctx) => {
      await ctx.go(peopleUrl(SPEC_PEOPLE.left, SPEC_PEOPLE.right, SPEC_PEOPLE.q1));
      if (!(await ctx.call('compare-run', SPEC_PEOPLE, 15_000))) fail('compare-run');
    },
  },
  {
    id: 'compare-person',
    title: 'Ken: Velquarry vs Bayfront',
    say: 'Ishaan: "Now Ken, a contractor. Velquarry on the left. On the right, a company that does not exist." Then "Identical. To the byte."',
    url: personUrl(SPEC_KEN.left, SPEC_KEN.q1, SPEC_KEN.q2),
    clip: CLIPS.A,
    action: async (ctx) => {
      await ctx.go(personUrl(SPEC_KEN.left, SPEC_KEN.q1, SPEC_KEN.q2));
      if (!(await ctx.call('compare-run', SPEC_KEN, 15_000))) fail('compare-run');
    },
  },
  {
    id: 'citation',
    title: "Open citation [2] on Rachel's answer",
    say: 'Ishaan: "Every sentence points at a passage the asker can open right now." Esc closes the sheet.',
    url: peopleUrl(SPEC_PEOPLE.left, SPEC_PEOPLE.right, SPEC_PEOPLE.q1),
    clip: CLIPS.A,
    action: async (ctx) => {
      await ctx.go(peopleUrl(SPEC_PEOPLE.left, SPEC_PEOPLE.right, SPEC_PEOPLE.q1));
      if (!(await ctx.call('compare-open-citation', { spec: SPEC_PEOPLE, slot: 'c', n: 2 }, 15_000))) fail('open-citation');
    },
  },
  {
    id: 'journey',
    title: "Journey of Rachel's request, as Maya",
    say: 'Ishaan: "Mehul, over to the compliance officer\'s chair." Mehul: "Here is what happened inside, for that one request."',
    url: journeyUrl,
    clip: CLIPS.B,
    action: async (ctx) => {
      await ctx.signInMain('maya.compliance');
      await ctx.go(journeyUrl(ctx));
    },
  },
  {
    id: 'verify',
    title: 'Audit: Verify the chain',
    say: 'Mehul: "I am the compliance officer. Verify: intact."',
    url: '/dev/audit?verify=1',
    clip: CLIPS.B,
    action: async (ctx) => ctx.go('/dev/audit?verify=1'),
  },
  {
    id: 'tamper',
    title: 'Sandbox tamper, then Verify',
    say: 'Mehul: "Now I change one word in a copy of the log, denied to allowed. Verify." Read the entry number off the screen.',
    url: '/dev/audit?tamper=1',
    clip: CLIPS.B,
    action: async (ctx) => ctx.go('/dev/audit?tamper=1'),
  },
  {
    id: 'workbuddy',
    title: 'WorkBuddy (off-app)',
    say: 'Mehul: "Aaruni, Tencent." Aaruni: "Tencent is inside the product. Jordan is on call at 3 a.m. and asks from WorkBuddy."',
    url: '/dev/audit',
    clip: CLIPS.C,
    offApp: true,
    // Back on the real ledger, so the WorkBuddy record shows when Dev returns to this window.
    action: async (ctx) => ctx.go('/dev/audit'),
  },
  {
    id: 'proof',
    title: 'Proof',
    say: 'Aaruni: "We did not just demo it. We attacked it." Read the numbers on the slide.',
    url: '/present/proof',
    clip: null,
    action: async (ctx) => ctx.go('/present/proof'),
  },
  {
    id: 'business',
    title: 'Business',
    say: 'Ishaan: "Aspire\'s team told us \'what can we offer this customer\' takes time today…" Say "estimate".',
    url: '/present/business',
    clip: null,
    action: async (ctx) => ctx.go('/present/business'),
  },
  {
    id: 'close',
    title: 'Close',
    say: 'Ishaan: "A company\'s AI will be asked to prove itself. Walnut DataBridge. Answers you can prove." Stop.',
    url: '/present/close',
    clip: null,
    action: async (ctx) => ctx.go('/present/close'),
  },
];

export const STEP_KEY: Record<StepId, PresenterKey> = {
  title: 'step.title',
  'compare-people': 'step.compare-people',
  'compare-person': 'step.compare-person',
  citation: 'step.citation',
  journey: 'step.journey',
  verify: 'step.verify',
  tamper: 'step.tamper',
  workbuddy: 'step.workbuddy',
  proof: 'step.proof',
  business: 'step.business',
  close: 'step.close',
};

export function stepUrl(step: PresenterStep, ctx: StepCtx): string {
  return typeof step.url === 'function' ? step.url(ctx) : step.url;
}
