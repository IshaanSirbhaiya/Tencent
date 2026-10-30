// Mock handlers owned by the User-mode agent (NEXT_PUBLIC_IB_MOCK=1). Server-side only.
//
//   POST ib/ask      a realistic wait (so the staged status line shows), then the FinCrime /
//                    market / fee / masking questions from the fact manifest
//                    (fixtures/enterprise/generate.py build_fintech + build_fintech_questions).
//                    Anything else falls through to the built-in scenarios (return null).
//   GET  ib/history  the backend_v1 shape: `channel` and `synthetic_replay` on every row.
//   GET  ib/doc/<id> the same access as the built-in route, plus a realistic passage that contains
//                    the cited sentences (so the Sources sheet can highlight them), and the
//                    FinCrime / product / dispute documents below.
//
// Everything is synthetic "Company A" data. Restricted and unknown get the same refusal and the
// same 404, exactly like the real service.
import { randomUUID } from 'node:crypto';
import { NextResponse } from 'next/server';
import type { AuditEvent, DocView, ModelInfo } from '../../types';
import { DOCS, EXTRACTIVE, HY3, REFUSAL_TEXT, ROLES, SCOPE_NOTE, citation, type MockDoc, type MockState } from '../fixtures';
import { mockDoc } from '../finals';
import type { MockHandler } from './types';

/* ------------------------------------------------------------------ shared mock store */

interface Store {
  state: MockState;
  ledger: AuditEvent[];
  answers: Map<string, string>;
  syncAt: Record<string, number>;
}
/** The built-in mock keeps its in-memory ledger here (lib/mock/server.ts). */
const store = () => (globalThis as unknown as { __ibMock?: Store }).__ibMock;

const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
/** Total mock latency for an ask is about 1.9 s (this wait + the built-in 350 ms). */
const ASK_WAIT_MS = 1550;

/* ------------------------------------------------------------------ extra documents */

interface ExtraDoc extends MockDoc {
  kind: 'page' | 'issue' | 'thread' | 'file';
  readers: string[];
  author: string;
  created: string;
  excerpt: string;
}

const SYNTH = 'SYNTHETIC CASE FILE: Velquarry Trading Pte Ltd is a fictional customer invented for this demonstration estate; every name and number is made up.';

const X = {
  cxScript: {
    doc_id: 'confluence:cf-7101',
    platform: 'confluence',
    kind: 'page',
    container: 'cx',
    container_name: 'Customer Experience',
    title: 'Customer account review: approved customer script',
    deep_link: 'https://companya.atlassian.net/wiki/spaces/cx/pages/cf-7101',
    version: '5',
    updated_at: '2026-09-27T14:00:00+08:00',
    created: '2026-05-03T10:00:00+08:00',
    author: 'Joel S',
    readers: ['amy.am'],
    excerpt:
      'Purpose\n\nAccount managers should use this script for any customer whose account is restricted or under review, whatever the reason.\n\nWhat to say\n\nTell the customer only that their account is undergoing a routine review in line with our regulatory obligations, that we cannot share further details, and that we will contact them if we need anything else.\n\nWhat not to say\n\nDo not give or guess a reason for the review, do not name any internal team, and do not confirm or deny any internal process. Use the same words for every customer.\n\nEscalation\n\nLog the customer’s question with the account review desk through the standard form; the desk replies within two business days.',
  },
  fcCase: {
    doc_id: 'confluence:cf-7201',
    platform: 'confluence',
    kind: 'page',
    container: 'fincrime',
    container_name: 'FinCrime Review',
    title: 'FC-2026-0147 Velquarry Trading Pte Ltd: unusual activity review',
    deep_link: 'https://companya.atlassian.net/wiki/spaces/fincrime/pages/cf-7201',
    version: '4',
    updated_at: '2026-10-24T15:00:00+08:00',
    created: '2026-10-04T10:00:00+08:00',
    author: 'Noor A',
    readers: ['mlro.tan'],
    excerpt: `${SYNTH}\n\nTrigger\n\nTransaction monitoring flagged Velquarry Trading Pte Ltd for 37 inbound transfers of just under SGD 20,000 each from unrelated third parties within nine days, and the account was restricted pending enhanced review.\n\nReview scope\n\nThe enhanced due diligence review of Velquarry Trading Pte Ltd covers the third-party deposits, the director’s stated source of funds and the onward transfers to Hong Kong.\n\nHandling\n\nThis case is confidential to the FinCrime review team. Account managers answer the customer with the approved customer account review script only.`,
  },
  fcStr: {
    doc_id: 'confluence:cf-7202',
    platform: 'confluence',
    kind: 'page',
    container: 'fincrime',
    container_name: 'FinCrime Review',
    title: 'FC-2026-0147 Velquarry Trading Pte Ltd: STR decision record',
    deep_link: 'https://companya.atlassian.net/wiki/spaces/fincrime/pages/cf-7202',
    version: '1',
    updated_at: '2026-10-21T17:00:00+08:00',
    created: '2026-10-21T17:00:00+08:00',
    author: 'Rachel Tan',
    readers: ['mlro.tan'],
    excerpt: `${SYNTH}\n\nDecision\n\nThe MLRO approved filing a suspicious transaction report on Velquarry Trading Pte Ltd, and it was lodged with STRO under the synthetic reference STRO-SYN-2026-0912.\n\nConfidentiality\n\nThe existence of this report must not be disclosed to the customer or to anyone outside the FinCrime review team.`,
  },
  fcSlack: {
    doc_id: 'slack:C0FINCRIME:1792120800.000200',
    platform: 'slack',
    kind: 'thread',
    container: 'C0FINCRIME',
    container_name: '#fincrime-cases',
    title: 'Thread: Velquarry Trading Pte Ltd review update',
    deep_link: 'https://companya.slack.com/archives/C0FINCRIME/p1792120800000200',
    version: '1792120800.000200',
    updated_at: '2026-10-23T11:20:00+08:00',
    created: '2026-10-23T11:20:00+08:00',
    author: 'Rachel Tan',
    readers: ['mlro.tan'],
    excerpt:
      'Rachel Tan: Velquarry Trading Pte Ltd review update (synthetic case FC-2026-0147): Velquarry Trading’s director could not explain the source of the third-party deposits on the enhanced due diligence call, so the restriction on the account stays in place.\n\nNoor A: Noted. The call notes are in the FinCrime case files folder.\n\nDaniel G: Account management asked why the account is restricted; I pointed them to the approved customer account review script and nothing else.',
  },
  fcDrive: {
    doc_id: 'drive:df-fc-0147-tx',
    platform: 'drive',
    kind: 'file',
    container: '0ACases',
    container_name: 'FinCrime case files',
    title: 'FC-2026-0147 Velquarry Trading Pte Ltd - transaction analysis',
    deep_link: 'https://drive.google.com/file/d/df-fc-0147-tx/view',
    version: '4',
    updated_at: '2026-10-22T16:00:00+08:00',
    created: '2026-10-18T10:00:00+08:00',
    author: 'Noor A',
    readers: ['mlro.tan'],
    excerpt: `${SYNTH}\n\nFindings\n\nThe transaction analysis for Velquarry Trading Pte Ltd links 11 of the 37 remitting accounts to a single device fingerprint used in another customer’s onboarding.\n\nNext steps\n\nRefresh the network analysis weekly while the review of Velquarry Trading Pte Ltd stays open.`,
  },
  tipping: {
    doc_id: 'confluence:cf-7210',
    platform: 'confluence',
    kind: 'page',
    container: 'fincrime',
    container_name: 'FinCrime Review',
    title: 'Case handling: tipping-off safeguards',
    deep_link: 'https://companya.atlassian.net/wiki/spaces/fincrime/pages/cf-7210',
    version: '3',
    updated_at: '2026-09-20T11:00:00+08:00',
    created: '2026-04-03T10:00:00+08:00',
    author: 'Rachel Tan',
    readers: ['mlro.tan'],
    excerpt:
      'Rule\n\nDisclosing that a suspicious transaction report has been filed, or that a review is under way, can itself be a criminal offence (tipping-off; Singapore CDSA section 57, referenced by MAS Notice PSN01).\n\nPractice\n\nCase notes live only in this space, the private case channel and the FinCrime case files folder. Customer-facing staff use the approved customer account review script, whatever the reason for a review.',
  },
  sgMca: {
    doc_id: 'confluence:cf-2230',
    platform: 'confluence',
    kind: 'page',
    container: 'product',
    container_name: 'Product',
    title: 'Multi-currency accounts for Singapore SMEs',
    deep_link: 'https://companya.atlassian.net/wiki/spaces/product/pages/cf-2230',
    version: '4',
    updated_at: '2026-10-14T11:00:00+08:00',
    created: '2026-06-12T10:00:00+08:00',
    author: 'Leo Kumar',
    readers: ['leo.ops', 'amy.am'],
    excerpt:
      'Markets: SG\n\nOffer\n\nSingapore SME customers on every plan can open a multi-currency account with Company A Singapore after standard KYB, with SGD, USD, EUR, GBP, AUD and HKD wallets and a daily transfer limit of SGD 500,000.\n\nOnboarding\n\nStandard KYB is usually completed within one business day for Singapore-incorporated companies.',
  },
  feeV3: {
    doc_id: 'confluence:cf-2240',
    platform: 'confluence',
    kind: 'page',
    container: 'product',
    container_name: 'Product',
    title: 'Fee schedule: SG (v3)',
    deep_link: 'https://companya.atlassian.net/wiki/spaces/product/pages/cf-2240',
    version: '3',
    updated_at: '2026-07-18T10:00:00+08:00',
    created: '2026-04-13T10:00:00+08:00',
    author: 'Hana Y',
    readers: ['leo.ops', 'amy.am'],
    excerpt:
      'Status\n\nApproved, effective 1 July 2026.\n\nFX\n\nUnder the approved fee schedule v3, the SG FX conversion fee is 0.35% of the converted amount for every SME plan, with no minimum charge.\n\nTransfers\n\nLocal SGD transfers are free; SWIFT transfers cost SGD 10 each.',
  },
  dispute: {
    doc_id: 'jira:PAY-312',
    platform: 'jira',
    kind: 'issue',
    container: 'PAY',
    container_name: 'Payments',
    title: 'PAY-312 Chargeback dispute: duplicate charge on a corporate card',
    deep_link: 'https://companya.atlassian.net/browse/PAY-312',
    version: '2026-10-25T14:05:00+08:00',
    updated_at: '2026-10-25T14:05:00+08:00',
    created: '2026-10-24T14:05:00+08:00',
    author: 'Raj M',
    readers: ['leo.ops', 'jdoe', 'priya.sre'],
    excerpt:
      'Escalated from merchant support. The cardholder disputed a duplicate charge of SGD 1,240.00 that the merchant’s terminal submitted twice within a minute. The merchant pasted the card details straight into the dispute form. Card number 411111••••••1111, CVV: •••, expiry ••/••.\n\nComment (Raj M): Finance confirmed that the duplicate charge goes back to the merchant’s bank in the next payout run. Payout IBAN on file: GB82••••5432.',
  },
} satisfies Record<string, ExtraDoc>;

type XKey = keyof typeof X;
const EXTRA_BY_ID = new Map<string, ExtraDoc>(Object.values(X).map((d) => [d.doc_id, d as ExtraDoc]));

/* ------------------------------------------------------------------ passages for the built-in documents */

/** Realistic passages (synthetic) for the built-in fixture documents, containing the cited sentences. */
function passage(docId: string, state: MockState | undefined): string | null {
  const v8 = state?.runbookV8;
  const P: Record<string, string> = {
    [DOCS.dbmigEpic.doc_id]:
      'Epic: migrate the ledger database to Postgres 16.\n\nStatus\n\nThe ledger database migration (DBMIG-12) is In Progress: 3 of 5 shards are copied and cutover is planned for 21 October 2026.\n\nRollback\n\nThe old primary stays read-only for seven days after cutover. Go/no-go is decided at the cutover review the day before.',
    [DOCS.dbmigThread.doc_id]:
      'Priya Raman: Blocker from the shard 4 cutover rehearsal: replica lag exceeded 90 seconds during the shard 4 cutover rehearsal, so we stopped at step 6.\n\nRaj M: Looking at it now. Until we know more, the ledger copy job backfill is throttled to 400 rows per second per shard.\n\nTom B: Logged as DBMIG-31 and linked to DBMIG-12.',
    [DOCS.dbmigLeads.doc_id]:
      'Raj M: Second blocker for the migration: the copy-tool licence expires on 17 October 2026 and renewal is pending with procurement.\n\nPriya Raman: If it lapses we cannot run the shard 5 copy. Escalating to the engineering manager today.',
    [DOCS.runbook.doc_id]:
      'When to use\n\nUse the payment service failover runbook when payment-service is degraded in the primary region.\n\nSteps\n\nStep 1: page the payments on-call and open an incident channel.\nStep 2: freeze deploys to payment-service.\nStep 3: confirm the secondary region is healthy.\nStep 4: run failover-ctl promote --region secondary and confirm the health endpoint returns ready.' +
      (v8 ? '\nStep 4b, added in version 8: drain in-flight settlement batches before promoting the secondary region.' : '') +
      '\nStep 5: post a status update every 30 minutes.',
    [DOCS.rca.doc_id]:
      'Summary\n\nThe 18 July 2026 payment outage was caused by an expired intermediate certificate; the failover path used the same certificate chain, so failover did not help.\n\nImpact\n\nCard payments failed for 47 minutes between 09:12 and 09:59 SGT.\n\nDetection\n\nThe first alert came from merchant complaints, not from monitoring.',
    [DOCS.postmortem.doc_id]:
      'Postmortem: payment outage of 18 July.\n\nActions\n\nThe postmortem records three follow-up tickets: PAY-214 (certificate expiry alerting), PAY-215 (separate chain for failover) and PAY-219 (game-day drill). Owners and due dates are tracked in Jira.',
    [DOCS.followups.doc_id]:
      'Raj M: Confirming the outage follow-ups are all in Jira: PAY-214, PAY-215 and PAY-219.\n\nTom B: Thanks. PAY-214 is in review; the other two start next sprint.',
    [DOCS.keys.doc_id]:
      'Schedule\n\nThe next settlement key rotation window is 24 October 2026, 01:00 to 03:00 SGT, owned by the payments platform team.\n\nProcedure\n\nRotate the signing key first, then the encryption key; keep the previous keys valid for 24 hours.',
    [DOCS.breach.doc_id]:
      'Summary\n\nOn 2 September 2026 an exposed staging API key was used to read 1,204 synthetic test merchant records.\n\nContainment\n\nContainment completed on 3 September 2026 at 14:20 SGT, and the key was revoked and rotated.\n\nRoot cause\n\nThe key was committed to a test configuration file in a public fork.',
    [DOCS.breachEvidence.doc_id]:
      'Evidence index (Q3 breach)\n\nThe evidence index lists the access logs, the key-rotation ticket SEC-88 and the regulator notification draft. Each item has an owner and a retention date.',
    [DOCS.containment.doc_id]:
      'Tracker for the Q3 incident.\n\nContainment of the Q3 incident is complete; two hardening actions remain open with a due date of 31 October 2026.',
    [DOCS.cves.doc_id]:
      'October dependency review.\n\nTwo dependency CVEs remain open: CVE-2026-3141 in the PDF renderer and CVE-2026-2977 in the YAML parser, both scheduled for the 20 October patch release.',
    [DOCS.secretRotation.doc_id]:
      'Calendar\n\nThe next secret rotation is due on 1 November 2026 for the payments service accounts. Database credentials follow on 15 November.',
    [DOCS.pricing.doc_id]:
      'Indonesia (partner bank)\n\nMulti-currency accounts can be offered to Indonesian SME customers on the Business Plus tier only, with IDR, SGD and USD wallets.\n\nNote: this sheet syncs weekly; check the Product space for policy changes.',
    [DOCS.idPolicy.doc_id]:
      'Policy\n\nOnboarding requires an enhanced due-diligence check for cross-border accounts. The partner bank sets the limits for Indonesian customers.',
    [DOCS.cardTiers.doc_id]:
      'Tiers\n\nCorporate cards come in three tiers: Starter, Growth and Enterprise; Enterprise includes custom spend controls.',
    [DOCS.planLimits.doc_id]:
      'Farah N: Summary of this week’s feedback: customers in the thread asked for higher monthly transfer limits on the Growth plan and clearer upgrade prompts.',
    [DOCS.ocr.doc_id]:
      'Acme PM: Update on the rollout. OCR model v2.4 is scheduled to reach production on 20 October 2026, after the shadow run completes.\n\nKen Ito: Thanks, we will watch the shadow-run metrics.',
    [DOCS.ocrSla.doc_id]:
      'Service levels\n\nThe KYC OCR service has a 99.5% monthly availability target and a 4-hour response time for severity-1 tickets.',
    [DOCS.k8sDrain.doc_id]:
      'Procedure\n\nCordon the pool, then drain nodes one at a time with a 5-minute pod disruption budget wait between nodes. Never drain two nodes of the same zone at once.',
    [DOCS.burnRate.doc_id]:
      'Thresholds\n\nPage on a 14.4x burn rate over 1 hour, and open a ticket on a 6x burn rate over 6 hours.',
    [DOCS.auditPack.doc_id]:
      'Deliverable\n\nThe technology risk audit evidence pack is due on 30 October 2026. Evidence owners upload to the GRC folder.',
    [DOCS.retention.doc_id]:
      'Schedule\n\nThe data retention schedule is maintained in the Compliance space; transaction records are kept for 5 years.',
    [DOCS.itOncall.doc_id]:
      'Rotation\n\nThis week the IT on-call primary is Alex Mercer and the secondary is the service desk lead.',
  };
  return P[docId] ?? null;
}

/* ------------------------------------------------------------------ scenarios (fact manifest FC, MK, FEE, SENS) */

interface Out {
  text: string;
  cites: XKey[];
  model: ModelInfo;
}
interface Scenario {
  id: string;
  match: (q: string) => boolean;
  answer: (sub: string, q: string) => Out | null;
  /** Documents that a refused asker would have been denied (journey only; never shown to them). */
  denied?: XKey[];
}

const FINCRIME: XKey[] = ['fcCase', 'fcStr', 'fcSlack', 'fcDrive'];
const has = (re: RegExp) => (q: string) => re.test(q);

const SCENARIOS: Scenario[] = [
  {
    id: 'FC-q3',
    match: (q) => /velquarry/i.test(q) && /\b(review|find|found|findings)\b/i.test(q) && !/restricted/i.test(q),
    answer: (sub) =>
      sub === 'mlro.tan'
        ? {
            text:
              'Transaction monitoring flagged Velquarry Trading Pte Ltd for 37 inbound transfers of just under SGD 20,000 each from unrelated third parties within nine days, and the account was restricted pending enhanced review [C1]. ' +
              'The transaction analysis links 11 of the 37 remitting accounts to a single device fingerprint used in another customer’s onboarding [C2]. ' +
              'The director could not explain the source of the third-party deposits on the enhanced due diligence call, so the restriction stays in place [C3]. ' +
              'The MLRO approved filing a suspicious transaction report, and it was lodged with STRO under the synthetic reference STRO-SYN-2026-0912 [C4].',
            cites: ['fcCase', 'fcDrive', 'fcSlack', 'fcStr'],
            model: HY3,
          }
        : null,
    denied: FINCRIME,
  },
  {
    id: 'FC-q4',
    match: has(/suspicious transaction report|\bSTR\b/i),
    answer: (sub, q) =>
      sub === 'mlro.tan' && /velquarry/i.test(q)
        ? {
            text: 'Yes. The MLRO approved filing a suspicious transaction report on Velquarry Trading Pte Ltd, and it was lodged with STRO under the synthetic reference STRO-SYN-2026-0912 [C1].',
            cites: ['fcStr'],
            model: HY3,
          }
        : null,
    denied: FINCRIME,
  },
  {
    id: 'FC-q1',
    match: (q) => /restricted/i.test(q) && /account/i.test(q),
    answer: (sub, q) => {
      if (sub === 'amy.am') {
        // The same approved script for every customer, real or not (FC-q1 = FC-q2).
        return {
          text:
            'Use the approved customer account review script. Tell the customer only that their account is undergoing a routine review in line with our regulatory obligations, that we cannot share further details, and that we will contact them if we need anything else [C1]. ' +
            'Do not give or guess a reason for the review, do not name any internal team, and use the same words for every customer [C1].',
          cites: ['cxScript'],
          model: HY3,
        };
      }
      if (sub === 'mlro.tan' && /velquarry/i.test(q)) {
        return {
          text:
            'Transaction monitoring flagged Velquarry Trading Pte Ltd for 37 inbound transfers of just under SGD 20,000 each from unrelated third parties within nine days, and the account was restricted pending enhanced review [C1]. ' +
            'The restriction stays in place because the director could not explain the source of the third-party deposits [C2]. ' +
            'Account managers answer the customer with the approved customer account review script only [C1].',
          cites: ['fcCase', 'fcSlack'],
          model: HY3,
        };
      }
      return null;
    },
    denied: FINCRIME,
  },
  {
    id: 'FC-tipping',
    match: has(/tipping[- ]off/i),
    answer: (sub) =>
      sub === 'mlro.tan'
        ? {
            text:
              'Disclosing that a suspicious transaction report has been filed, or that a review is under way, can itself be a criminal offence (tipping-off; Singapore CDSA section 57, referenced by MAS Notice PSN01) [C1]. ' +
              'Case notes live only in the FinCrime space, the private case channel and the case files folder, and customer-facing staff use the approved customer account review script, whatever the reason for a review [C1].',
            cites: ['tipping'],
            model: HY3,
          }
        : null,
    denied: ['tipping'],
  },
  {
    id: 'MK-q1',
    match: (q) => /multi-currency/i.test(q) && /(singapore|\bSG\b)/i.test(q),
    answer: (sub) =>
      sub === 'leo.ops' || sub === 'amy.am'
        ? {
            text:
              'Yes. Singapore SME customers on every plan can open a multi-currency account with Company A Singapore after standard KYB, with SGD, USD, EUR, GBP, AUD and HKD wallets and a daily transfer limit of SGD 500,000 [C1]. ' +
              'Standard KYB is usually completed within one business day for Singapore-incorporated companies [C1].',
            cites: ['sgMca'],
            model: HY3,
          }
        : null,
  },
  {
    id: 'FEE-q1',
    match: has(/conversion fee|fx fee/i),
    answer: (sub) =>
      sub === 'leo.ops' || sub === 'amy.am'
        ? {
            text: 'Under the approved fee schedule v3, the SG FX conversion fee is 0.35% of the converted amount for every SME plan, with no minimum charge [C1]. The schedule has been effective since 1 July 2026 [C1].',
            cites: ['feeV3'],
            model: HY3,
          }
        : null,
  },
  {
    id: 'SENS-q1',
    match: has(/card details|duplicate charge|chargeback/i),
    answer: (sub) =>
      ['leo.ops', 'jdoe', 'priya.sre'].includes(sub)
        ? {
            text:
              'The cardholder disputed a duplicate charge of SGD 1,240.00 that the merchant’s terminal submitted twice within a minute [C1]. ' +
              'The merchant pasted the card details straight into the dispute form; the ticket shows them only in masked form, card number 411111••••••1111 and CVV: ••• [C1].',
            cites: ['dispute'],
            model: HY3,
          }
        : null,
    denied: ['dispute'],
  },
  // A made-up customer: nothing to cite, for anyone. Same refusal as a restricted subject.
  { id: 'FC-unknown', match: has(/bayfront/i), answer: () => null },
];

function asCitations(keys: XKey[], now: Date) {
  return keys.map((k, i) => citation(`C${i + 1}`, X[k], now, {}));
}

function ledgerEvent(reqId: string, at: string, sub: string, question: string, out: Out | null, denied: XKey[]): AuditEvent {
  const allowed = out?.cites ?? [];
  const deny = out ? [] : denied.filter((k) => !X[k].readers.includes(sub));
  const considered = [...allowed, ...deny];
  return {
    req_id: reqId,
    event_time: at,
    sub,
    channel: 'ui',
    question,
    kind: 'request',
    considered: considered.map((k) => X[k].doc_id),
    considered_meta: considered.map((k) => ({ doc_id: X[k].doc_id, platform: X[k].platform, container: X[k].container, title: X[k].title })),
    gate2: [
      ...allowed.map((k) => ({ doc_id: X[k].doc_id, verdict: 'verified', reason_code: 'ok' })),
      ...deny.map((k) => ({ doc_id: X[k].doc_id, verdict: 'forbidden', reason_code: `${X[k].platform}.not_permitted` })),
    ],
    released: out ? allowed.map((k) => X[k].doc_id) : [],
    model: out?.model ?? EXTRACTIVE,
    refused: !out,
    synthetic_replay: false,
    latency_ms: 1700 + (reqId.charCodeAt(reqId.length - 1) % 400),
  };
}

/* ------------------------------------------------------------------ doc view */

function extraDocView(d: ExtraDoc, sub: string, syncAt: Record<string, number> | undefined, withQuery: boolean): DocView | null {
  if (!d.readers.includes(sub)) return null;
  const now = new Date().toISOString();
  const links = d.doc_id === X.fcCase.doc_id || d.doc_id === X.fcStr.doc_id
    ? [d.doc_id === X.fcCase.doc_id ? X.fcStr : X.fcCase].filter((o) => o.readers.includes(sub)).map((o) => ({
        doc_id: o.doc_id,
        platform: o.platform,
        title: o.title,
        container_name: o.container_name,
        kind: o.kind,
        relation: 'links' as const,
      }))
    : [];
  return {
    doc_id: d.doc_id,
    platform: d.platform,
    kind: d.kind,
    container: d.container,
    container_name: d.container_name,
    title: d.title,
    version: d.version,
    updated_at: d.updated_at,
    deep_link: d.deep_link,
    created_at: d.created,
    author: { id: d.author.toLowerCase().replace(/\s+/g, '.'), name: d.author },
    excerpt: d.excerpt,
    excerpt_kind: withQuery ? 'best_chunk' : 'start',
    excerpt_truncated: false,
    links,
    lineage: {
      connector: d.platform,
      last_sync_at: new Date(syncAt?.[d.platform] ?? Date.now()).toISOString(),
      content_checked_at: now,
      authz_checked_at: now,
      live_read: false,
    },
    scope_note: SCOPE_NOTE,
    as_of: now,
  };
}

/* ------------------------------------------------------------------ handler */

export const handle: MockHandler = async ({ req, path, method, sub }) => {
  if (path === 'ib/ask' && method === 'POST') {
    let question = '';
    try {
      const b = (await req.clone().json()) as { question?: unknown };
      question = String(b.question ?? '').trim();
    } catch {
      return null; // let the built-in route answer 422
    }
    if (!question || question.length > 2000 || /\[mock:503\]/i.test(question)) return null;
    await sleep(ASK_WAIT_MS);
    const sc = SCENARIOS.find((s) => s.match(question));
    if (!sc) return null; // the built-in scenarios answer (after their own 350 ms)
    await sleep(350);
    const out = sc.answer(sub, question);
    const now = new Date();
    const request_id = `req_${randomUUID().replace(/-/g, '').slice(0, 16)}`;
    const answer = out ? out.text : REFUSAL_TEXT;
    const s = store();
    if (s) {
      s.ledger.push(ledgerEvent(request_id, now.toISOString(), sub, question, out, sc.denied ?? []));
      s.answers.set(request_id, answer);
    }
    return json({
      request_id,
      answer,
      citations: out ? asCitations(out.cites, now) : [],
      scope_note: SCOPE_NOTE,
      model: out?.model ?? EXTRACTIVE,
      as_of: now.toISOString(),
    });
  }

  if (path === 'ib/history' && method === 'GET') {
    const s = store();
    if (!s) return null;
    const items = s.ledger
      .filter((e) => e.sub === sub && e.kind === 'request')
      .sort((a, b) => (a.event_time < b.event_time ? 1 : -1))
      .slice(0, 50)
      .map((e) => ({ req_id: e.req_id, question: e.question ?? '', event_time: e.event_time, channel: e.channel ?? 'ui', synthetic_replay: !!e.synthetic_replay }));
    return json({ items });
  }

  if (path.startsWith('ib/doc/') && method === 'GET') {
    let id: string;
    try {
      id = decodeURIComponent(path.slice('ib/doc/'.length));
    } catch {
      return json({ detail: 'not found' }, 404);
    }
    const q = req.nextUrl.searchParams.get('q');
    const s = store();
    const extra = EXTRA_BY_ID.get(id);
    if (extra) {
      const v = extraDocView(extra, sub, s?.syncAt, !!q);
      return v ? json(v) : json({ detail: 'not found' }, 404);
    }
    if (!s || !ROLES[sub]) return null;
    const base = mockDoc(sub, s.state, id, s.syncAt);
    if (!base) return json({ detail: 'not found' }, 404);
    const p = passage(id, s.state);
    return json(p ? { ...base, excerpt: p, excerpt_kind: q ? 'best_chunk' : 'start' } : base);
  }

  return null;
};

/* ------------------------------------------------------------------ for the Act mock (act.ts) */

/** Citation metadata of one of this area's extra documents (the Act mock cites them by id). */
export function extraDocMeta(docId: string): MockDoc | null {
  const d = EXTRA_BY_ID.get(docId);
  return d ? { ...d } : null;
}
