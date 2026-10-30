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
// Round 4 (Opus #3, Sonnet): notes describe the person by role only. They name no channel, space,
// project, drive or folder at all, public or restricted (tests/e2e/user.spec.ts checks against the world).
// The presenter's questions (Amy's two, presenter.spec.ts) are unchanged. Round 4: the starters of
// admin.it, Maya, Leo and Rachel that asked about places they cannot open were replaced by manifest
// questions each of them gets a cited answer to (tests/e2e/user.spec.ts checks this against world.json).
export const PERSONAS: Persona[] = [
  {
    id: 'jdoe',
    name: 'Jordan Doe',
    fullName: 'Jordan Doe',
    role: 'Backend engineer',
    note: 'Payments engineering team',
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
    note: 'Platform SRE lead',
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
    note: 'Partner manager, external vendor',
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
    note: 'Security engineer, incident response',
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
      'Do I need to declare a gift from a supplier?',
    ],
  },
  {
    id: 'leo.ops',
    name: 'Leo',
    fullName: 'Leo Hartono',
    role: 'Product & ops',
    note: 'Product and operations manager',
    // The home page shows the first four. The Indonesian multi-currency question is the presenter's D
    // warm-up and the video's (components/presenter/deliver.ts), the card-details one is SENS-q1 (trust
    // page); the Singapore one, closest to the Indonesian, is fifth.
    suggestions: [
      'Can we offer multi-currency accounts to an Indonesian SME?',
      'Can we offer yield on idle balances to a Hong Kong SME?',
      'What is the SG FX conversion fee?',
      'What card details did the merchant paste into the duplicate charge dispute?',
      'Can we offer multi-currency accounts to a Singapore SME?',
    ],
  },
  {
    id: 'amy.am',
    name: 'Amy',
    fullName: 'Amy Lim',
    role: 'Account manager',
    note: 'Looks after SME customer accounts',
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
    note: 'Leads financial crime reviews',
    suggestions: [
      'What did the review of Velquarry Trading Pte Ltd find, and has a suspicious transaction report been filed?',
      'What does the tipping-off handling guidance say?',
      'What does a new joiner do in week 1?',
    ],
  },
  {
    id: 'admin.it',
    name: 'IT admin',
    fullName: 'Alex Mercer',
    role: 'Platform simulator',
    note: 'Operates the demo platforms',
    suggestions: [
      'Who approves spend above SGD 500?',
    ],
  },
];

/** The demo's headline question (used by the tour and the compare page). */
export const SAMPLE_QUESTION = "What's the status of the database migration project and were there any blockers raised in Slack last week?";

export const AUDIT_SUGGESTIONS = [
  'What did jdoe access in the payment-gateway space over the last 30 days?',
  'Who retrieved the Q3 breach report?',
  'Show every denied retrieval last week.',
];

export function personaById(id: string | undefined | null): Persona | undefined {
  return PERSONAS.find((p) => p.id === id);
}
