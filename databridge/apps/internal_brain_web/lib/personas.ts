export interface Persona {
  id: string;
  name: string;
  /**
   * The directory's full name (fixtures/enterprise/generate.py, the synthetic world's people), for
   * headers that must not change when the signed-in identity comes back (Compare columns).
   */
  fullName: string;
  role: string;
  note: string;
  suggestions: string[];
}

// Six demo personas plus the platform simulator operator (plan §5.1). There is no persona-list
// endpoint, so these are fixed. Suggested questions come from the fact manifest's canonical
// questions (fixtures/enterprise/generate.py). Rule 4 (restricted = non-existent): the login page shows
// every note to everyone before sign-in, and starters are offered to the signed-in person, so a note says
// only what that person CAN open (no private channel names, never "no access to X"), and no starter asks
// for something the person cannot open. Refusal questions live in the presenter script and the e2e only.
// The presenter's questions (Amy's two, presenter.spec.ts) are unchanged.
export const PERSONAS: Persona[] = [
  {
    // Project Atlas onboarding (fixtures/scenarios/atlas_onboarding; signs in only on a stack
    // started with that world, e.g. IB_WORLD=…/atlas_onboarding/world.json)
    id: 'alex.product',
    name: 'Alex Morgan',
    fullName: 'Alex Morgan',
    role: 'Product lead, Project Atlas',
    note: 'Project Atlas spaces, tickets, channels and files',
    suggestions: [
      "Hi, I'm Alex Morgan, the new product lead for Project Atlas. Bring me up to speed with an onboarding brief: purpose, status, team table and contributions, decisions, blockers, timeline and first-week checklist. Use all connected sources, cite every claim and flag outdated or missing information.",
      'What is the current pilot plan for Project Atlas?',
      'Who owns the remaining callback fixes on Atlas, and when are they due?',
    ],
  },
  {
    id: 'jdoe',
    name: 'Jordan Doe',
    fullName: 'Jordan Doe',
    role: 'Backend engineer',
    note: 'Payments engineering spaces and team channels',
    suggestions: [
      "What's the status of the database migration project and were there any blockers raised in Slack last week?",
      'What was the root cause of the payment outage last quarter, and what follow-up tickets were created?',
      'When is the next settlement key rotation window?',
      'What backfill throttle are we using for the ledger copy job?',
    ],
  },
  {
    id: 'priya.sre',
    name: 'Priya',
    fullName: 'Priya Raman',
    role: 'On-call SRE',
    note: 'Runbooks space, #incidents, DB migration leads',
    suggestions: [
      "What's the latest runbook for the payment-service incident?",
      "What's the status of the database migration project and were there any blockers raised in Slack last week?",
      'When can the break-glass database role be used and who revokes it?',
    ],
  },
  {
    id: 'ken.ext',
    name: 'Ken',
    fullName: 'Ken Adeyemi',
    role: 'External contractor',
    note: 'Slack Connect guest: #vendor-acme and the vendor share only',
    suggestions: [
      'When will the OCR model update reach production?',
      'What is the support SLA for the KYC OCR service?',
    ],
  },
  {
    id: 'sara.sec',
    name: 'Sara',
    fullName: 'Sara Lindqvist',
    role: 'Security',
    note: 'Security team spaces and incident channels',
    suggestions: [
      'How many merchant accounts were affected in the Q3 breach?',
      'What is the status of the Q3 incident containment?',
      'Which dependency CVEs are still open?',
      'When is the next settlement key rotation window?',
    ],
  },
  {
    id: 'maya.compliance',
    name: 'Maya',
    fullName: 'Maya Fernandes',
    role: 'Compliance officer',
    note: 'Audit console access; reveals are reasoned and logged',
    suggestions: [
      'When is the technology risk audit evidence pack due?',
      "What's the status of the database migration project?",
    ],
  },
  {
    id: 'leo.ops',
    name: 'Leo',
    fullName: 'Leo Hartono',
    role: 'Product & ops',
    note: 'Product space, Drive "Pricing & Offers"',
    suggestions: [
      'Can we offer multi-currency accounts to a Singapore SME?',
      'Can we offer multi-currency accounts to an Indonesian SME?',
      'What is the SG FX conversion fee?',
      'What card details did the merchant paste into the duplicate charge dispute?',
      'What was the root cause of the payment outage last quarter, and what follow-up tickets were created?',
    ],
  },
  {
    id: 'amy.am',
    name: 'Amy',
    fullName: 'Amy Lim',
    role: 'Account manager',
    note: 'Customer and product spaces',
    suggestions: [
      "Why has Velquarry Trading's account been restricted, and what should I tell the customer?",
      "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?",
    ],
  },
  {
    id: 'mlro.tan',
    name: 'Rachel',
    fullName: 'Rachel Tan',
    role: 'MLRO (FinCrime)',
    note: 'FinCrime team spaces, need-to-know',
    suggestions: [
      'What did the review of Velquarry Trading Pte Ltd find, and has a suspicious transaction report been filed?',
      'What does the tipping-off handling guidance say?',
      "What's the status of the database migration project?",
    ],
  },
  {
    id: 'admin.it',
    name: 'IT admin',
    fullName: 'Alex Mercer',
    role: 'Platform simulator',
    note: 'Operates the demo platforms',
    suggestions: [
      "What's the latest runbook for the payment-service incident?",
    ],
  },
];

/** The demo's headline question (used by the tour and the compare page). */
export const SAMPLE_QUESTION = "What's the status of the database migration project and were there any blockers raised in Slack last week?";

export const AUDIT_SUGGESTIONS = [
  // the Velquarry audit story (docs/finals/workflows/WORKFLOWS.md, workflow 3)
  'Who accessed the Velquarry STR decision record in the last 30 days?',
  'Show me everything amy.am accessed in the last 7 days',
  'Show every denied retrieval last week.',
];

export function personaById(id: string | undefined | null): Persona | undefined {
  return PERSONAS.find((p) => p.id === id);
}
