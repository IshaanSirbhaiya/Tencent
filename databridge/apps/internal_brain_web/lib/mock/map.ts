// Mock GET /ib/map (NEXT_PUBLIC_IB_MOCK=1). Server-side only. Synthetic "Company A" estate.
//
// Mirrors the real route's contract: the response lists ONLY what the signed-in person may open,
// grouped platform → container → item. Restricted items are never listed or counted. The container
// sets and counts for jdoe (867 items / 17 places / 4 platforms) and ken.ext (24 / 2 / 2) match the
// live demo estate; item titles are generated deterministically from per-container topics.
import type { KnowledgeMap, MapContainer, MapItem, MapPlatform, Platform } from '../types';
import { DOCS, type DocKey, type MockDoc, type MockState } from './fixtures';

const FRESHNESS_NOTE = 'Each item was checked with its source just now.';
const ORDER: Platform[] = ['confluence', 'jira', 'slack', 'drive'];

interface Place {
  platform: Platform;
  container: string;
  name: string;
  count: number;
  topics: string[];
  pinned?: DocKey[];
}

const P = (platform: Platform, container: string, name: string, count: number, topics: string[], pinned: DocKey[] = []): Place => ({
  platform,
  container,
  name,
  count,
  topics,
  pinned,
});

const PLACES: Record<string, Place> = {
  // Confluence spaces
  'cf.pg': P('confluence', 'payment-gateway', 'Payment Gateway', 50, ['Retry policy', 'Refund worker', 'Settlement batch', 'FX quote cache', 'Webhook signing', 'Idempotency keys', 'Gateway timeouts', 'Chargeback flow', '3DS challenge', 'Card tokenisation'], ['rca', 'keys']),
  'cf.sre': P('confluence', 'sre-runbooks', 'SRE Runbooks', 37, ['Region failover', 'Node pool upgrade', 'Certificate rotation', 'DNS cutover', 'Kafka consumer lag', 'Postgres vacuum', 'Redis eviction', 'On-call handover'], ['runbook', 'k8sDrain', 'burnRate']),
  'cf.sec': P('confluence', 'SEC', 'Security', 28, ['Access review', 'Vulnerability triage', 'Pen test scope', 'Key management', 'Incident response', 'Phishing drill'], ['breach', 'secretRotation']),
  'cf.product': P('confluence', 'product', 'Product', 33, ['Multi-currency wallets', 'Expense cards', 'Plan limits', 'Onboarding flow', 'Bill pay', 'Pricing experiments'], ['idPolicy', 'cardTiers']),
  'cf.compliance': P('confluence', 'compliance', 'Compliance', 22, ['Outsourcing register', 'Policy attestation', 'Regulatory calendar', 'Complaints handling', 'Record keeping'], ['retention']),
  'cf.it': P('confluence', 'it', 'IT', 18, ['Laptop setup', 'SSO onboarding', 'VPN access', 'Software requests', 'Device loss'], ['itOncall']),
  'cf.cx': P('confluence', 'cx', 'Customer Experience', 12, ['Customer account review', 'Escalation matrix', 'Support macros', 'Refund requests']),
  'cf.fincrime': P('confluence', 'fincrime', 'FinCrime', 15, ['Tipping-off handling', 'Case review template', 'Sanctions screening', 'Transaction monitoring rules']),
  // Jira projects
  'jira.auth': P('jira', 'AUTH', 'Auth Service', 56, ['MFA adoption alerting', 'session token rotation', 'API key service', 'SSO SAML metadata', 'login rate limits', 'password reset flow']),
  'jira.dbmig': P('jira', 'DBMIG', 'Database Migration', 40, ['shard copy job', 'replica lag alerting', 'cutover rehearsal', 'partition pruning', 'read replicas', 'rollback plan'], ['dbmigEpic']),
  'jira.pay': P('jira', 'PAY', 'Payments', 111, ['FX quote cache', 'settlement retries', 'refund worker', 'certificate expiry alerting', 'payout batching', 'card BIN lookup', 'webhook redelivery']),
  'jira.sec': P('jira', 'SEC', 'Security', 24, ['dependency scanning', 'secrets in CI', 'WAF rules', 'audit log retention'], ['containment', 'cves']),
  'jira.grc': P('jira', 'GRC', 'Governance, Risk & Compliance', 19, ['risk register', 'control testing', 'vendor due diligence', 'policy review'], ['auditPack']),
  'jira.ops': P('jira', 'OPS', 'Product Ops', 27, ['plan limit change', 'pricing page copy', 'onboarding checklist', 'partner referral']),
  'jira.fcc': P('jira', 'FCC', 'FinCrime Cases', 11, ['case review', 'enhanced due diligence', 'alert disposition']),
  // Slack channels
  'sl.authsd': P('slack', 'C0AUTHSD', 'auth-service-design', 30, ['post-deploy check: login success rate looks normal', 'proposal: shorter refresh-token lifetime', 'SAML metadata refresh is flaky again']),
  'sl.dbmig': P('slack', 'C0DBMIG', 'db-migration', 43, ['reminder: partition pruning review is Thursday at 3pm', 'shard 3 copy finished overnight', 'replica lag spiked during rehearsal'], ['dbmigThread']),
  'sl.eng': P('slack', 'C0ENG', 'engineering', 63, ['quick poll: should we move the runbook coverage review', 'new linting rules land Monday', 'who owns the flaky integration test']),
  'sl.general': P('slack', 'C0GENERAL', 'general', 55, ['the handbook page on meeting-free Wednesdays has been updated', 'office closed on Deepavali', 'welcome our new joiners']),
  'sl.incid': P('slack', 'C0INCID', 'incidents', 54, ['heads up: deploying the Terraform state to staging', 'sev-3 resolved: delayed payout emails', 'status page updated']),
  'sl.payeng': P('slack', 'C0PAYENG', 'payments-eng', 84, ['post-deploy check: settlement lag looks normal so far', 'FX cache hit rate dipped', 'refund worker backlog cleared'], ['followups']),
  'sl.product': P('slack', 'C0PRODUCT', 'product', 64, ['can we get a decision on customer interviews', 'plan limits copy review', 'roadmap sync moved to Friday']),
  'sl.random': P('slack', 'C0RANDOM', 'random', 41, ['Priya is organising the onboarding buddy programme', 'lunch recommendations near the office', 'photos from the offsite']),
  'sl.releases': P('slack', 'C0RELEAS', 'releases', 37, ['webhook delivery rate dipped this morning, looking into it', 'mobile 6.12 is in review', 'release train cut at 4pm']),
  'sl.sreoc': P('slack', 'C0SREOC', 'sre-oncall', 62, ['FYI the old bastion host is finally gone', 'handover: two open alerts on the ledger', 'pager test at 10am']),
  'sl.dblead': P('slack', 'C0DBLEAD', 'db-migration-leads', 14, ['copy-tool licence renewal is pending', 'cutover go/no-go criteria draft'], ['dbmigLeads']),
  'sl.vendor': P('slack', 'C0VENDACME', 'vendor-acme', 16, ['support tickets about card limits again this week', 'OCR shadow run results', 'next sync moved to Tuesday'], ['ocr']),
  'sl.secq3': P('slack', 'C0SECQ3', 'sec-incident-q3', 21, ['containment checklist updated', 'evidence index shared', 'regulator notification draft ready for review']),
  'sl.custvoice': P('slack', 'C0CUSTVOICE', 'customer-voice', 26, ['customers asking for higher transfer limits', 'NPS comments from SMEs', 'feature request: bulk payouts'], ['planLimits']),
  'sl.fincrime': P('slack', 'C0FINCRIME', 'fincrime-cases', 18, ['case review scheduled', 'alert threshold tuning', 'EDD request sent']),
  'sl.ithelp': P('slack', 'C0ITHELP', 'it-helpdesk', 33, ['VPN certificate renewal', 'new laptop requests', 'SSO outage resolved']),
  // Google Drive shared drives
  'dr.company': P('drive', '0ACompany', 'Company shared', 9, ['Benefits portal', 'Referral programme', 'Office move', 'All-hands']),
  'dr.eng': P('drive', '0AEng', 'Engineering', 31, ['PagerDuty rotation', 'Architecture review', 'Capacity planning', 'Hiring loop'], ['postmortem']),
  'dr.pricing': P('drive', '0APricing', 'Pricing & Offers', 17, ['SME pricing', 'FX fee schedule', 'Partner offers'], ['pricing']),
  'dr.secr': P('drive', '0ASecRestricted', 'Security restricted', 6, ['Pen test report', 'Key ceremony'], ['breachEvidence']),
  'dr.vendor': P('drive', '0AVendor', 'Vendor share', 8, ['Acme statement of work', 'OCR integration'], ['ocrSla']),
  'dr.cases': P('drive', '0ACases', 'FinCrime case files', 12, ['Case file', 'EDD memo']),
};

const ACCESS: Record<string, string[]> = {
  jdoe: ['cf.pg', 'cf.sre', 'jira.auth', 'jira.dbmig', 'jira.pay', 'sl.authsd', 'sl.dbmig', 'sl.eng', 'sl.general', 'sl.incid', 'sl.payeng', 'sl.product', 'sl.random', 'sl.releases', 'sl.sreoc', 'dr.company', 'dr.eng'],
  'ken.ext': ['sl.vendor', 'dr.vendor'],
  'priya.sre': ['cf.sre', 'cf.pg', 'jira.dbmig', 'jira.pay', 'sl.dbmig', 'sl.dblead', 'sl.incid', 'sl.sreoc', 'sl.eng', 'sl.general', 'sl.releases', 'dr.eng'],
  'sara.sec': ['cf.sec', 'jira.sec', 'sl.secq3', 'sl.eng', 'sl.general', 'sl.incid', 'dr.secr', 'dr.eng'],
  'maya.compliance': ['cf.compliance', 'jira.grc', 'sl.general', 'sl.random', 'dr.company'],
  'leo.ops': ['cf.product', 'jira.ops', 'sl.product', 'sl.custvoice', 'sl.general', 'dr.pricing', 'dr.company'],
  'amy.am': ['cf.cx', 'cf.product', 'sl.product', 'sl.custvoice', 'sl.general', 'sl.random', 'dr.company'],
  'mlro.tan': ['cf.fincrime', 'jira.fcc', 'sl.fincrime', 'sl.general', 'dr.cases'],
  'admin.it': ['cf.it', 'cf.sre', 'sl.ithelp', 'sl.general', 'sl.sreoc', 'dr.company'],
};

function seed(s: string): () => number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}

const BASE = Date.parse('2026-10-12T18:00:00+08:00');
const CF_SUFFIX = ['', ' — design notes', ' (archive notes)', ' checklist', ' FAQ', ' — decision record', ' how-to', ' overview'];
const JIRA_VERB = ['Fix', 'Add', 'Migrate', 'Investigate', 'Document', 'Refactor', 'Upgrade', 'Alert on'];
const DRIVE_DOC = ['meeting minutes', 'plan', 'review deck', 'tracker', 'notes'];

function fromDoc(d: MockDoc, kind: string): MapItem {
  return { doc_id: d.doc_id, title: d.title, kind, version: d.version, updated_at: d.updated_at, deep_link: d.deep_link };
}

const KIND: Record<Platform, string> = { confluence: 'page', jira: 'issue', slack: 'thread', drive: 'file' };

function generate(key: string, pl: Place, n: number, drop: Set<DocKey>, state: MockState): MapItem[] {
  const rnd = seed(key);
  const items: MapItem[] = [];
  for (const k of pl.pinned ?? []) {
    if (drop.has(k)) continue;
    const d = DOCS[k];
    const it = fromDoc(d, KIND[pl.platform]);
    if (k === 'runbook' && state.runbookV8) Object.assign(it, { version: '8', updated_at: new Date(Date.now() - 60_000).toISOString() });
    items.push(it);
  }
  let i = 0;
  while (items.length < n) {
    const topic = pl.topics[i % pl.topics.length];
    const round = Math.floor(i / pl.topics.length);
    const when = new Date(BASE - Math.floor(rnd() * 120 * 86400_000) - i * 3_600_000);
    const iso = when.toISOString();
    if (pl.platform === 'confluence') {
      const id = `cf-${6000 + Math.abs(Math.floor(rnd() * 900)) * 10 + (i % 10)}`;
      items.push({
        doc_id: `confluence:${id}-${i}`,
        title: `${topic}${CF_SUFFIX[round % CF_SUFFIX.length]}${round >= CF_SUFFIX.length ? ` ${round}` : ''}`,
        kind: 'page',
        version: String(1 + Math.floor(rnd() * 12)),
        updated_at: iso,
        deep_link: `https://companya.atlassian.net/wiki/spaces/${pl.container}/pages/${id}`,
      });
    } else if (pl.platform === 'jira') {
      const num = 20 + i * 3 + Math.floor(rnd() * 3);
      const k = `${pl.container}-${num}`;
      items.push({
        doc_id: `jira:${k}`,
        title: `${k}: ${JIRA_VERB[(i + round) % JIRA_VERB.length]} ${topic}`,
        kind: 'issue',
        version: iso,
        updated_at: iso,
        deep_link: `https://companya.atlassian.net/browse/${k}`,
      });
    } else if (pl.platform === 'slack') {
      const ts = `${Math.floor(when.getTime() / 1000)}.${String(1000 + i).padStart(6, '0')}`;
      items.push({
        doc_id: `slack:${pl.container}:${ts}`,
        title: `#${pl.name}: ${topic}${round ? ` (${round + 1})` : ''}`,
        kind: 'thread',
        version: ts,
        updated_at: iso,
        deep_link: `https://companya.slack.com/archives/${pl.container}/p${ts.replace('.', '')}`,
      });
    } else {
      const id = `df-${pl.container.slice(2).toLowerCase()}-${i}`;
      items.push({
        doc_id: `drive:${id}`,
        title: `${topic} ${DRIVE_DOC[(i + round) % DRIVE_DOC.length]}${round ? ` ${round + 1}` : ''}`,
        kind: 'file',
        version: String(1 + Math.floor(rnd() * 9)),
        updated_at: iso,
        deep_link: `https://drive.google.com/file/d/${id}/view`,
      });
    }
    i += 1;
  }
  return items.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
}

export function buildMap(sub: string, state: MockState, scopeNote: string): KnowledgeMap {
  let keys = ACCESS[sub] ?? [];
  const drop = new Set<DocKey>();
  // The simulator's live changes show up here too: a revoked channel or restricted page simply
  // stops being listed, with no trace that it was ever there.
  if (sub === 'jdoe' && state.jdoeRemoved) keys = keys.filter((k) => k !== 'sl.dbmig');
  if (sub === 'jdoe' && state.keysRestricted) drop.add('keys');

  const byPlatform = new Map<Platform, MapContainer[]>();
  for (const k of keys) {
    const pl = PLACES[k];
    const n = pl.count - [...drop].filter((d) => pl.pinned?.includes(d)).length;
    const items = generate(`${k}`, pl, n, drop, state);
    const list = byPlatform.get(pl.platform) ?? [];
    list.push({ container: pl.container, name: pl.name, count: items.length, items });
    byPlatform.set(pl.platform, list);
  }
  const platforms: MapPlatform[] = ORDER.filter((p) => byPlatform.has(p)).map((p) => {
    const containers = byPlatform.get(p)!;
    return { platform: p, count: containers.reduce((a, c) => a + c.count, 0), containers };
  });
  return {
    as_of: new Date().toISOString(),
    scope_note: scopeNote,
    sub,
    totals: {
      items: platforms.reduce((a, p) => a + p.count, 0),
      containers: platforms.reduce((a, p) => a + p.containers.length, 0),
      platforms: platforms.length,
    },
    platforms,
    freshness_note: FRESHNESS_NOTE,
  };
}
