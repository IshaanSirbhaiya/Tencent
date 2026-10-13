export interface Persona {
  id: string;
  name: string;
  role: string;
  note: string;
  suggestions: string[];
}

// Six demo personas plus the platform simulator operator (plan §5.1). There is no persona-list
// endpoint, so these are fixed. Suggested questions come from the fact manifest's canonical
// questions (fixtures/enterprise/generate.py) so they exercise both answers and refusals.
export const PERSONAS: Persona[] = [
  {
    id: 'jdoe',
    name: 'Jordan Doe',
    role: 'Backend engineer',
    note: 'Payments engineering, member of private #db-migration',
    suggestions: [
      "What's the status of the database migration project and were there any blockers raised in Slack last week?",
      'What was the root cause of the payment outage last quarter, and what follow-up tickets were created?',
      'When is the next settlement key rotation window?',
      'Show me the security incident report from the Q3 breach.',
    ],
  },
  {
    id: 'priya.sre',
    name: 'Priya',
    role: 'On-call SRE',
    note: 'Runbooks space, #incidents, DB migration leads',
    suggestions: [
      "What's the latest runbook for the payment-service incident?",
      "What's the status of the database migration project and were there any blockers raised in Slack last week?",
      'How do we drain a Kubernetes node pool safely?',
      'What are the current SLO burn-rate alert thresholds?',
    ],
  },
  {
    id: 'ken.ext',
    name: 'Ken',
    role: 'External contractor',
    note: 'Slack Connect guest: #vendor-acme and the vendor share only',
    suggestions: [
      'When will the OCR model update reach production?',
      'What is the support SLA for the KYC OCR service?',
      'Show me the security incident report from the Q3 breach.',
      'Show me the security incident report from the Q7 breach.',
    ],
  },
  {
    id: 'sara.sec',
    name: 'Sara',
    role: 'Security',
    note: 'SEC space, Jira security level, #sec-incident-q3',
    suggestions: [
      'Show me the security incident report from the Q3 breach.',
      'What is the status of the Q3 incident containment?',
      'Which dependency CVEs are still open?',
      'When is the next secret rotation due?',
    ],
  },
  {
    id: 'maya.compliance',
    name: 'Maya',
    role: 'Compliance officer',
    note: 'Audit console access; reveals are reasoned and logged',
    suggestions: [
      'When is the technology risk audit evidence pack due?',
      'Where is the data retention schedule?',
      'Show me the security incident report from the Q3 breach.',
      "What's the status of the database migration project?",
    ],
  },
  {
    id: 'leo.ops',
    name: 'Leo',
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
    role: 'Account manager',
    note: 'Customer & product spaces; no FinCrime case access',
    suggestions: [
      "Why has Harbourline Trading's account been restricted, and what should I tell the customer?",
      "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?",
      'Has a suspicious transaction report been filed on Harbourline Trading?',
      'Can we offer multi-currency accounts to a Singapore SME?',
    ],
  },
  {
    id: 'mlro.tan',
    name: 'Rachel',
    role: 'MLRO (FinCrime)',
    note: 'FinCrime space, #fincrime-cases, case files — need-to-know only',
    suggestions: [
      'What did the review of Harbourline Trading Pte Ltd find, and has a suspicious transaction report been filed?',
      'What does the tipping-off handling guidance say?',
      'Show me the security incident report from the Q3 breach.',
      "What's the status of the database migration project?",
    ],
  },
  {
    id: 'admin.it',
    name: 'IT admin',
    role: 'Platform simulator',
    note: 'Operates the demo platforms; no audit access',
    suggestions: [
      'Who is on the IT on-call rotation this week?',
      "What's the latest runbook for the payment-service incident?",
      'Where is the data retention schedule?',
      'What are the current SLO burn-rate alert thresholds?',
    ],
  },
];

export const AUDIT_SUGGESTIONS = [
  'What did jdoe access in the payment-gateway space over the last 30 days?',
  'Who retrieved the Q3 breach report?',
  'Show every denied retrieval last week.',
];

export function personaById(id: string | undefined | null): Persona | undefined {
  return PERSONAS.find((p) => p.id === id);
}
