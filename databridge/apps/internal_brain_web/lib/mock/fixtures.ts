// Mock-mode fixtures (NEXT_PUBLIC_IB_MOCK=1). Server-side only: imported by the /api/ib route
// handler, never by a client component. Content is synthetic "Company A" demo data, shaped exactly
// like the IB API so the UI can be built and screenshotted without the backend.
import type { Citation, ModelInfo, Platform } from '../types';

export const SCOPE_NOTE =
  'Answers draw only on content you can access in Confluence, Jira, Slack and Google Drive.';
export const REFUSAL_TEXT = "I couldn't find an answer to that in the sources you can access.";

export const ROLES: Record<string, { name: string; roles: string[]; employment: string }> = {
  jdoe: { name: 'Jordan Doe', roles: ['employee'], employment: 'employee' },
  'priya.sre': { name: 'Priya Raman', roles: ['employee'], employment: 'employee' },
  'ken.ext': { name: 'Ken Ito', roles: ['employee'], employment: 'contractor' },
  'sara.sec': { name: 'Sara Lim', roles: ['employee'], employment: 'employee' },
  'maya.compliance': { name: 'Maya Tan', roles: ['employee', 'compliance_officer'], employment: 'employee' },
  'leo.ops': { name: 'Leo Kumar', roles: ['employee'], employment: 'employee' },
  'amy.am': { name: 'Amy Lim', roles: ['employee'], employment: 'employee' },
  'mlro.tan': { name: 'Rachel Tan', roles: ['employee'], employment: 'employee' },
  'admin.it': { name: 'Alex Mercer', roles: ['employee', 'admin_it'], employment: 'employee' },
};

export interface MockDoc {
  doc_id: string;
  platform: Platform;
  container: string;
  container_name: string;
  title: string;
  deep_link: string;
  version: string;
  updated_at: string;
  stale?: boolean;
}

const cf = (id: string, space: string, spaceName: string, title: string, version: string, updated: string): MockDoc => ({
  doc_id: `confluence:${id}`,
  platform: 'confluence',
  container: space,
  container_name: spaceName,
  title,
  deep_link: `https://companya.atlassian.net/wiki/spaces/${space}/pages/${id}`,
  version,
  updated_at: updated,
});
const jira = (key: string, project: string, projectName: string, title: string, updated: string): MockDoc => ({
  doc_id: `jira:${key}`,
  platform: 'jira',
  container: project,
  container_name: projectName,
  title: `${key} ${title}`,
  deep_link: `https://companya.atlassian.net/browse/${key}`,
  version: updated,
  updated_at: updated,
});
const slack = (channelId: string, channel: string, ts: string, title: string, updated: string): MockDoc => ({
  doc_id: `slack:${channelId}:${ts}`,
  platform: 'slack',
  container: channelId,
  container_name: `#${channel}`,
  title,
  deep_link: `https://companya.slack.com/archives/${channelId}/p${ts.replace('.', '')}`,
  version: ts,
  updated_at: updated,
});
const drive = (id: string, driveName: string, title: string, version: string, updated: string): MockDoc => ({
  doc_id: `drive:${id}`,
  platform: 'drive',
  container: '0AEng',
  container_name: driveName,
  title,
  deep_link: `https://drive.google.com/file/d/${id}/view`,
  version,
  updated_at: updated,
});

export const DOCS = {
  dbmigEpic: jira('DBMIG-12', 'DBMIG', 'Database Migration', 'Ledger database migration to Postgres 16', '2026-10-09T17:42:00+08:00'),
  dbmigThread: slack('C0DBMIG', 'db-migration', '1759900000.000100', 'Thread: shard 4 cutover rehearsal', '2026-10-08T11:06:40+08:00'),
  dbmigLeads: slack('C0DBLEAD', 'db-migration-leads', '1759915200.000200', 'Thread: copy-tool licence renewal', '2026-10-08T15:20:00+08:00'),
  runbook: cf('cf-1042', 'sre-runbooks', 'SRE Runbooks', 'Payment service failover runbook', '7', '2026-09-28T10:15:00+08:00'),
  rca: cf('cf-1019', 'payment-gateway', 'Payment Gateway', 'RCA: payment outage 18 July 2026', '4', '2026-07-24T16:30:00+08:00'),
  postmortem: drive('df-pm-0718', 'Engineering › Postmortems', 'Postmortem — payment outage 18 July', '3', '2026-07-25T09:10:00+08:00'),
  followups: slack('C0PAYENG', 'payments-eng', '1753430400.000300', 'Thread: outage follow-up tickets', '2026-07-25T16:00:00+08:00'),
  keys: cf('cf-1077', 'payment-gateway', 'Payment Gateway', 'Settlement keys rotation', '3', '2026-10-02T14:00:00+08:00'),
  breach: cf('cf-3301', 'SEC', 'Security', 'Q3 breach — incident report', '5', '2026-09-12T18:45:00+08:00'),
  breachEvidence: drive('df-sec-q3', 'Engineering › Security restricted', 'Q3 breach — evidence index', '2', '2026-09-11T12:00:00+08:00'),
  containment: jira('SEC-88', 'SEC', 'Security', 'Q3 incident containment tracker', '2026-10-06T10:20:00+08:00'),
  cves: jira('SEC-91', 'SEC', 'Security', 'Open dependency CVEs — October review', '2026-10-10T09:00:00+08:00'),
  secretRotation: cf('cf-3310', 'SEC', 'Security', 'Secret rotation calendar', '9', '2026-10-01T09:00:00+08:00'),
  pricing: { ...drive('df-pricing-sme', 'Pricing & Offers', 'SME multi-currency pricing sheet', '6', '2026-10-12T17:30:00+08:00'), stale: true },
  idPolicy: cf('cf-2204', 'product', 'Product', 'Cross-border accounts policy — Indonesia', '2', '2026-08-30T11:00:00+08:00'),
  cardTiers: cf('cf-2210', 'product', 'Product', 'Corporate card pricing tiers', '5', '2026-09-18T15:00:00+08:00'),
  planLimits: slack('C0CUSTVOICE', 'customer-voice', '1759300000.000400', 'Thread: feedback on plan limits', '2026-10-01T14:26:40+08:00'),
  ocr: slack('C0VENDACME', 'vendor-acme', '1759820000.000500', 'Thread: OCR model v2.4 rollout', '2026-10-07T14:53:20+08:00'),
  ocrSla: drive('df-vendor-sla', 'Vendor share', 'KYC OCR service — support SLA', '1', '2026-08-14T10:00:00+08:00'),
  k8sDrain: cf('cf-1050', 'sre-runbooks', 'SRE Runbooks', 'Draining a Kubernetes node pool', '3', '2026-08-20T13:00:00+08:00'),
  burnRate: cf('cf-1055', 'sre-runbooks', 'SRE Runbooks', 'SLO burn-rate alerting', '6', '2026-09-30T09:30:00+08:00'),
  auditPack: jira('GRC-14', 'GRC', 'Governance, Risk & Compliance', 'Technology risk audit evidence pack', '2026-10-05T12:00:00+08:00'),
  retention: cf('cf-4002', 'compliance', 'Compliance', 'Data retention schedule', '11', '2026-09-02T10:00:00+08:00'),
  itOncall: cf('cf-5003', 'it', 'IT', 'IT on-call rotation', '42', '2026-10-12T08:00:00+08:00'),
} satisfies Record<string, MockDoc>;

export type DocKey = keyof typeof DOCS;

// Honesty (rule 8): TokenHub is not activated, so the mock never labels a canned answer as written by a
// model unless the build explicitly asks for it with NEXT_PUBLIC_IB_MOCK_MODEL=hy3 (UI work on the model
// footer). The default everywhere (tests, dev, the offline stage fallback) is "Quoted from sources (no model)".
export const MOCK_MODEL_LABELS = process.env.NEXT_PUBLIC_IB_MOCK_MODEL === 'hy3';
export const HY3: ModelInfo = MOCK_MODEL_LABELS
  ? { provider: 'tencent-tokenhub', model: 'hy3', fallback_used: false }
  : { provider: 'deterministic', model: 'extractive-v1', fallback_used: false };
export const DEEPSEEK: ModelInfo = MOCK_MODEL_LABELS
  ? { provider: 'tencent-tokenhub', model: 'deepseek-v4.1-flash', fallback_used: true }
  : { provider: 'deterministic', model: 'extractive-v1', fallback_used: false };
export const EXTRACTIVE: ModelInfo = { provider: 'deterministic', model: 'extractive-v1', fallback_used: false };

export interface MockState {
  runbookV8: boolean;
  jdoeRemoved: boolean;
  keysRestricted: boolean;
}

export interface Scenario {
  id: string;
  match: RegExp;
  /** returns null when the persona may not see any relevant content (=> constant refusal) */
  answer: (persona: string, s: MockState) => { text: string; cites: DocKey[]; model: ModelInfo; liveRead?: DocKey[]; denied?: { doc: DocKey; reason: string }[] } | null;
}

const only = (...ids: string[]) => (p: string) => ids.includes(p);

export const SCENARIOS: Scenario[] = [
  {
    id: 'S3-breach',
    match: /breach/i,
    answer: (p) => {
      // A report that does not exist (Q7) gets the identical refusal as a restricted one (P2).
      if (!only('sara.sec')(p)) return null;
      return {
        text:
          'On 2 September 2026 an exposed staging API key was used to read 1,204 synthetic test merchant records [C1]. ' +
          'Containment completed on 3 September 2026 at 14:20 SGT, and the key was revoked and rotated [C1]. ' +
          'The evidence index lists the access logs, the key-rotation ticket SEC-88 and the regulator notification draft [C2].',
        cites: ['breach', 'breachEvidence'],
        model: EXTRACTIVE,
      };
    },
  },
  {
    id: 'S1-dbmig',
    match: /database migration|db migration|migration project/i,
    answer: (p, s) => {
      if (p === 'priya.sre') {
        return {
          text:
            'The ledger database migration (DBMIG-12) is In Progress: 3 of 5 shards are copied and cutover is planned for 21 October 2026 [C1]. ' +
            'On 8 October a blocker was raised in #db-migration: replica lag exceeded 90 seconds during the shard 4 cutover rehearsal [C2]. ' +
            'A second blocker was raised in #db-migration-leads: the copy-tool licence expires on 17 October 2026 and renewal is pending [C3].',
          cites: ['dbmigEpic', 'dbmigThread', 'dbmigLeads'],
          model: HY3,
        };
      }
      if (p === 'jdoe') {
        if (s.jdoeRemoved) {
          return {
            text: 'The ledger database migration (DBMIG-12) is In Progress: 3 of 5 shards are copied and cutover is planned for 21 October 2026 [C1].',
            cites: ['dbmigEpic'],
            model: HY3,
            denied: [{ doc: 'dbmigThread', reason: 'slack.not_channel_member' }],
          };
        }
        return {
          text:
            'The ledger database migration (DBMIG-12) is In Progress: 3 of 5 shards are copied and cutover is planned for 21 October 2026 [C1]. ' +
            'On 8 October a blocker was raised in #db-migration: replica lag exceeded 90 seconds during the shard 4 cutover rehearsal [C2].',
          cites: ['dbmigEpic', 'dbmigThread'],
          model: HY3,
        };
      }
      return null;
    },
  },
  {
    id: 'S1b-outage',
    match: /root cause|payment outage/i,
    answer: (p) =>
      only('jdoe', 'priya.sre', 'leo.ops')(p)
        ? {
            text:
              'The 18 July 2026 payment outage was caused by an expired intermediate certificate; the failover path used the same certificate chain, so failover did not help [C1]. ' +
              'The postmortem records three follow-up tickets: PAY-214 (certificate expiry alerting), PAY-215 (separate chain for failover) and PAY-219 (game-day drill) [C2]. ' +
              'The tickets were confirmed in #payments-eng on 25 July [C3].',
            cites: ['rca', 'postmortem', 'followups'],
            model: DEEPSEEK,
          }
        : null,
  },
  {
    id: 'S2-runbook',
    match: /runbook/i,
    answer: (p, s) => {
      if (!only('priya.sre', 'admin.it')(p)) return null;
      const base =
        'Use the payment service failover runbook when payment-service is degraded in the primary region [C1]. ' +
        'Step 4: run failover-ctl promote --region secondary and confirm the health endpoint returns ready [C1].';
      return s.runbookV8
        ? {
            text: base + ' Step 4b, added in version 8: drain in-flight settlement batches before promoting the secondary region [C1].',
            cites: ['runbook'],
            model: HY3,
            liveRead: ['runbook'],
          }
        : { text: base, cites: ['runbook'], model: HY3 };
    },
  },
  {
    id: 'S4-keys',
    match: /settlement key/i,
    answer: (p, s) => {
      const ok = p === 'sara.sec' || p === 'priya.sre' || (p === 'jdoe' && !s.keysRestricted);
      if (!ok) return null;
      return {
        text: 'The next settlement key rotation window is 24 October 2026, 01:00 to 03:00 SGT, owned by the payments platform team [C1].',
        cites: ['keys'],
        model: HY3,
      };
    },
  },
  {
    id: 'S4-throttle',
    match: /backfill throttle|ledger copy/i,
    answer: (p, s) =>
      p === 'priya.sre' || (p === 'jdoe' && !s.jdoeRemoved)
        ? { text: 'The ledger copy job backfill is throttled to 400 rows per second per shard [C1].', cites: ['dbmigThread'], model: HY3 }
        : null,
  },
  {
    id: 'Ops',
    match: /multi-currency|indonesia/i,
    answer: (p) =>
      p === 'leo.ops'
        ? {
            text:
              'Multi-currency accounts can be offered to Indonesian SME customers on the Business Plus tier only, with IDR, SGD and USD wallets [C1]. ' +
              'Onboarding requires an enhanced due-diligence check for cross-border accounts [C2].',
            cites: ['pricing', 'idPolicy'],
            model: HY3,
          }
        : null,
  },
  simple('containment', /containment/i, ['sara.sec'], 'Containment of the Q3 incident is complete; two hardening actions remain open with a due date of 31 October 2026 [C1].', 'containment'),
  simple('cves', /cve/i, ['sara.sec'], 'Two dependency CVEs remain open: CVE-2026-3141 in the PDF renderer and CVE-2026-2977 in the YAML parser, both scheduled for the 20 October patch release [C1].', 'cves'),
  simple('secret-rotation', /secret rotation/i, ['sara.sec'], 'The next secret rotation is due on 1 November 2026 for the payments service accounts [C1].', 'secretRotation'),
  simple('card-tiers', /corporate card/i, ['leo.ops'], 'Corporate cards come in three tiers: Starter, Growth and Enterprise; Enterprise includes custom spend controls [C1].', 'cardTiers'),
  simple('plan-limits', /plan limits/i, ['leo.ops'], 'Customers in the thread asked for higher monthly transfer limits on the Growth plan and clearer upgrade prompts [C1].', 'planLimits'),
  simple('ocr', /ocr model/i, ['ken.ext'], 'OCR model v2.4 is scheduled to reach production on 20 October 2026, after the shadow run completes [C1].', 'ocr'),
  simple('ocr-sla', /sla/i, ['ken.ext'], 'The KYC OCR service has a 99.5% monthly availability target and a 4-hour response time for severity-1 tickets [C1].', 'ocrSla'),
  simple('k8s', /node pool/i, ['priya.sre'], 'Cordon the pool, then drain nodes one at a time with a 5-minute pod disruption budget wait between nodes [C1].', 'k8sDrain'),
  simple('burn', /burn-rate|burn rate/i, ['priya.sre', 'admin.it'], 'Page on a 14.4x burn rate over 1 hour, and open a ticket on a 6x burn rate over 6 hours [C1].', 'burnRate'),
  simple('audit-pack', /evidence pack/i, ['maya.compliance'], 'The technology risk audit evidence pack is due on 30 October 2026 [C1].', 'auditPack'),
  simple('retention', /retention schedule/i, ['maya.compliance', 'admin.it'], 'The data retention schedule is maintained in the Compliance space; transaction records are kept for 5 years [C1].', 'retention'),
  simple('it-oncall', /on-call rotation|on call rotation/i, ['admin.it'], 'This week the IT on-call primary is Alex Mercer and the secondary is the service desk lead [C1].', 'itOncall'),
];

function simple(id: string, match: RegExp, who: string[], text: string, doc: DocKey): Scenario {
  return { id, match, answer: (p) => (who.includes(p) ? { text, cites: [doc], model: HY3 } : null) };
}

export function citation(tag: string, d: MockDoc, now: Date, opts: { liveRead?: boolean; version?: string; updated?: string }): Citation {
  const checked = now.toISOString();
  return {
    tag,
    doc_id: d.doc_id,
    platform: d.platform,
    container_name: d.container_name,
    title: d.title,
    deep_link: d.deep_link,
    version: opts.version ?? d.version,
    updated_at: opts.updated ?? d.updated_at,
    content_checked_at: checked,
    authz_checked_at: checked,
    live_read: !!opts.liveRead,
    stale: !!d.stale,
  };
}

/** Seeded history for the audit console (plan §4.7: labelled as synthetic replay). */
export const SEED_HISTORY: { daysAgo: number; hour: number; sub: string; q: string; docs: DocKey[]; deny?: { doc: DocKey; reason: string }[]; refused?: boolean; model: ModelInfo; replay: boolean; channel?: string }[] = [
  { daysAgo: 27, hour: 10, sub: 'jdoe', q: 'What is the rollout plan for the payment-gateway retry policy?', docs: ['rca'], model: HY3, replay: true },
  { daysAgo: 23, hour: 15, sub: 'priya.sre', q: 'How do we drain a Kubernetes node pool safely?', docs: ['k8sDrain'], model: HY3, replay: true },
  { daysAgo: 21, hour: 9, sub: 'sara.sec', q: 'Show me the security incident report from the Q3 breach.', docs: ['breach', 'breachEvidence'], model: EXTRACTIVE, replay: true },
  { daysAgo: 19, hour: 11, sub: 'jdoe', q: 'When is the next settlement key rotation window?', docs: ['keys'], model: HY3, replay: true },
  { daysAgo: 16, hour: 14, sub: 'leo.ops', q: 'What are the pricing tiers for corporate cards?', docs: ['cardTiers'], model: HY3, replay: true },
  { daysAgo: 12, hour: 10, sub: 'ken.ext', q: 'Show me the security incident report from the Q3 breach.', docs: [], refused: true, model: EXTRACTIVE, replay: true },
  { daysAgo: 9, hour: 16, sub: 'jdoe', q: 'Summarize the payment-gateway design notes for the retry service.', docs: ['rca'], deny: [{ doc: 'keys', reason: 'confluence.page_restricted' }], model: HY3, replay: true },
  { daysAgo: 6, hour: 13, sub: 'jdoe', q: "What's the status of the database migration project and were there any blockers raised in Slack last week?", docs: ['dbmigEpic', 'dbmigThread'], model: HY3, replay: true },
  { daysAgo: 5, hour: 9, sub: 'priya.sre', q: "What's the latest runbook for the payment-service incident?", docs: ['runbook'], model: HY3, replay: true },
  { daysAgo: 4, hour: 17, sub: 'ken.ext', q: 'When will the OCR model update reach production?', docs: ['ocr'], deny: [{ doc: 'dbmigEpic', reason: 'jira.no_browse_permission' }], model: HY3, replay: true },
  { daysAgo: 3, hour: 11, sub: 'leo.ops', q: 'Can we offer multi-currency accounts to an Indonesian SME customer?', docs: ['pricing', 'idPolicy'], model: HY3, replay: true },
  { daysAgo: 2, hour: 15, sub: 'jdoe', q: 'What was the root cause of the payment outage last quarter, and what follow-up tickets were created?', docs: ['rca', 'postmortem', 'followups'], model: DEEPSEEK, replay: true },
  { daysAgo: 1, hour: 10, sub: 'jdoe', q: 'What backfill throttle are we using for the ledger copy job?', docs: [], deny: [{ doc: 'dbmigThread', reason: 'slack.not_channel_member' }], refused: true, model: EXTRACTIVE, replay: false, channel: 'workbuddy-mcp' },
];
