// Trust centre content, copied BY HAND from docs/finals/rubric/responsible_ai/ (30 Oct 2026):
//   FRAMEWORK_CROSSWALK.md §3-§7 (statuses and counts), OWASP_LLM_REDTEAM.md (summary table),
//   SYSTEM_CARD.md §5 (controls and their tests), README.md ("Claims we never make").
// Everything here is self-assessed by the team on synthetic data. Change the md first, then this file.
// Test names are relative to databridge/apps/internal_brain/tests/ unless they start with a folder.

export const REPO = 'https://github.com/IshaanSirbhaiya/Tencent';
export const BRANCH = 'final_v1.0';
export const RAI_DIR = `${REPO}/blob/${BRANCH}/docs/finals/rubric/responsible_ai`;

export type ControlIcon = 'filter' | 'key' | 'ledger' | 'shape' | 'ground' | 'reveal' | 'mask';

export interface Control {
  id: string;
  icon: ControlIcon;
  title: string;
  what: string;
  why: string;
  /** Frameworks this control is mapped to (FRAMEWORK_CROSSWALK.md §2). */
  maps: string[];
  proof: string[];
  live: { href: string; label: string; note?: string };
}

export const CONTROLS: Control[] = [
  {
    id: 'filter',
    icon: 'filter',
    title: 'Filter before the model (two gates)',
    what: 'Gate 1 ranks only documents the stored source permissions allow, with search statistics computed over those documents alone. Gate 2 then asks each source live, within one 2-second budget; any error, timeout or 429 means deny. Only what passes both gates can reach a model.',
    why: 'Restricted content never reaches a third-party model or an answer, so the protection obligation does not depend on model behaviour. Revoking access at the source works on the very next question.',
    maps: ['PDPA s.24', 'MAS AIRG §5.4(d)-(e)', 'OWASP LLM02, LLM08'],
    proof: [
      'test_pipeline_core.py::test_p1_restricted_never_in_any_prompt',
      'test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted',
      'test_scenarios_e2e.py::test_s4_revocation_without_webhook',
      'databridge/evals/internal_brain/run.py (leak eval with canaries; re-run pending)',
    ],
    live: { href: '/compare', label: 'Same question, two people' },
  },
  {
    id: 'identity',
    icon: 'key',
    title: 'Identity only from the signed token',
    what: 'Who is asking comes only from a signed, expiring session token. No request body, tool argument or model output can name a person, and the WorkBuddy tools take no identity argument.',
    why: 'A forged request or an injected instruction cannot ask “as” someone else, so every answer and every log line is attributable to the real person.',
    maps: ['MAS AIRG §5.23(d)', 'MAS FEAT Accountability', 'OWASP LLM06'],
    proof: [
      'test_app_routes.py::test_identity_from_token_only',
      'test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument',
      'test_contracts.py::test_no_identity_in_request_shapes',
    ],
    live: { href: '/dev/journey', label: 'Request journey' },
  },
  {
    id: 'ledger',
    icon: 'ledger',
    title: 'Write-ahead, sealed log',
    what: 'request.accepted is written before retrieval, retrieval.decided before any model call, answer.released before the response. Records are hash-chained and sealed, anchored internally and published off-host. If the log cannot write, there is no answer (503).',
    why: 'Compliance can reconstruct who saw what, and prove it was not edited afterwards. An unlogged answer is impossible by construction.',
    maps: ['AI Verify Accountability', 'MAS AIRG §5.23(d)', 'PDPA s.24'],
    proof: ['test_pipeline_core.py::test_write_ahead_fail_closed', 'test_app_routes.py::test_ledger_failure_is_503', 'test_audit_tamper.py'],
    live: { href: '/dev/audit?tamper=1', label: 'Verify and tamper demo', note: 'Compliance role' },
  },
  {
    id: 'shape',
    icon: 'shape',
    title: 'One response shape',
    what: 'One constant refusal and one constant scope note. No counts and no denial reasons reach the asker, so a restricted document and one that never existed look byte-for-byte the same.',
    why: 'Confirming that a suspicious-transaction case file exists can itself tip off a customer (CDSA s.57). An account manager gets the approved script, identical to the answer for a company that does not exist.',
    maps: ['MAS FEAT Ethics', 'PDPA s.24', 'OWASP LLM02'],
    proof: [
      'test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical',
      'test_fintech_scenarios.py::test_fc_real_vs_nonexistent_customer_is_byte_identical',
      'databridge/evals/internal_brain/run.py (P2 differential, 400 pairs; re-run pending)',
    ],
    live: { href: '/compare', label: 'Real vs made-up question' },
  },
  {
    id: 'grounding',
    icon: 'ground',
    title: 'Grounding check',
    what: 'Every number, date and name in a model answer must appear in the cited, permitted evidence. If not, the next model is tried, then a deterministic extractive answer that quotes the sources.',
    why: 'Staff act on what the assistant says about fees, deadlines and incidents; an unsupported figure is refused before anyone sees it. Text is shown only after the check passes.',
    maps: ['MAS AIRG §5.9(c)', 'AI Verify Safety', 'OWASP LLM09'],
    proof: ['test_llm_chain.py::test_ungrounded_number_refused_by_grounding_wall', 'test_llm_chain.py::test_both_fail_pipeline_serves_extractive'],
    live: { href: '/', label: 'Ask, with citations' },
  },
  {
    id: 'reveal',
    icon: 'reveal',
    title: 'Reveal needs a reason',
    what: 'The log keeps a fingerprint of each answer, not its text. A compliance officer can read an answer only by giving a reason, which is logged before the text is returned and re-hashed against the seal. Other roles cannot reach the audit routes.',
    why: 'Oversight without a standing window into everyone’s answers: every look is purposeful, attributable and itself auditable.',
    maps: ['AI Verify Accountability', 'MAS AIRG §5.23(d)', 'PDPA s.24'],
    proof: ['test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered', 'test_audit_routes.py::test_non_compliance_roles_forbidden'],
    live: { href: '/dev/audit', label: 'Audit console', note: 'Compliance role' },
  },
  {
    id: 'masking',
    icon: 'mask',
    title: 'Masking of card numbers and NRIC',
    what: 'Card numbers (first 6 and last 4 kept), card security codes, NRIC/FIN and IBANs are masked at ingest, before indexing, so the raw values never reach the index, an answer or a model.',
    why: 'Card data and national identifiers pasted into tickets and chats are the most common accidental exposure in a payments company.',
    maps: ['PDPA s.24', 'MAS AIRG §5.4(d)', 'OWASP LLM02'],
    proof: ['test_sensitive.py', 'test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index'],
    live: { href: '/', label: 'Ask as Leo', note: '“What card details did the merchant paste into the duplicate charge dispute?”' },
  },
];

export type Status = 'met' | 'partial' | 'gap' | 'n/a';

export interface Framework {
  id: string;
  name: string;
  scope: string;
  section: string;
  /** Counted by hand from the section's table. */
  met: number;
  partial: number;
  gap: number;
  na?: number;
  items: { name: string; status: Status }[];
}

export const FRAMEWORKS: Framework[] = [
  {
    id: 'ai-verify',
    name: 'AI Verify',
    scope: '11 principles (IMDA / AI Verify Foundation)',
    section: '§3',
    met: 6,
    partial: 5,
    gap: 0,
    items: [
      { name: 'Transparency', status: 'partial' },
      { name: 'Explainability', status: 'met' },
      { name: 'Reproducibility', status: 'met' },
      { name: 'Safety', status: 'met' },
      { name: 'Security', status: 'met' },
      { name: 'Robustness', status: 'partial' },
      { name: 'Fairness', status: 'partial' },
      { name: 'Data governance', status: 'met' },
      { name: 'Accountability', status: 'met' },
      { name: 'Human agency and oversight', status: 'partial' },
      { name: 'Inclusive growth', status: 'partial' },
    ],
  },
  {
    id: 'imda-genai',
    name: 'IMDA Model AI Governance Framework for GenAI',
    scope: '9 dimensions (30 May 2024)',
    section: '§4',
    met: 4,
    partial: 4,
    gap: 0,
    na: 1,
    items: [
      { name: 'Accountability', status: 'met' },
      { name: 'Data', status: 'met' },
      { name: 'Trusted development and deployment', status: 'partial' },
      { name: 'Incident reporting', status: 'partial' },
      { name: 'Testing and assurance', status: 'met' },
      { name: 'Security', status: 'met' },
      { name: 'Content provenance', status: 'partial' },
      { name: 'Safety and alignment R&D', status: 'n/a' },
      { name: 'AI for public good', status: 'partial' },
    ],
  },
  {
    id: 'mas-feat',
    name: 'MAS FEAT',
    scope: '4 principles (Veritas)',
    section: '§5',
    met: 2,
    partial: 2,
    gap: 0,
    items: [
      { name: 'Fairness', status: 'partial' },
      { name: 'Ethics', status: 'met' },
      { name: 'Accountability', status: 'met' },
      { name: 'Transparency', status: 'partial' },
    ],
  },
  {
    id: 'mas-airg',
    name: 'MAS Guidelines on AI Risk Management',
    scope: '8 expectations (issued 7 Oct 2026)',
    section: '§6',
    met: 2,
    partial: 6,
    gap: 0,
    items: [
      { name: 'AI inventory (§4.7)', status: 'partial' },
      { name: 'Risk materiality', status: 'partial' },
      { name: 'Data and input/output security (§5.4(d)-(e))', status: 'met' },
      { name: 'Human review escalation (§5.9(c))', status: 'partial' },
      { name: 'Kill switch (§5.23(b))', status: 'partial' },
      { name: 'Logging prompts, responses, model versions (§5.23(d))', status: 'partial' },
      { name: 'Third-party AI', status: 'partial' },
      { name: 'Testing', status: 'met' },
    ],
  },
  {
    id: 'pdpa',
    name: 'PDPA (Singapore)',
    scope: '5 obligations',
    section: '§7',
    met: 1,
    partial: 2,
    gap: 2,
    items: [
      { name: 's.24 Protection (supports; does not discharge it)', status: 'met' },
      { name: 'ss.18, 20 Purpose and notification', status: 'partial' },
      { name: 'ss.21, 22 Access and correction', status: 'gap' },
      { name: 's.25 Retention limitation', status: 'gap' },
      { name: 's.26 Transfer limitation', status: 'partial' },
    ],
  },
];

/** OWASP_LLM_REDTEAM.md summary table: the residual-risk column, verbatim. Not scored met/partial/gap there. */
export const OWASP: { id: string; risk: string; control: string; residual: string }[] = [
  { id: 'LLM01', risk: 'Prompt injection', control: 'Access before content, no tools, untrusted-data labelling', residual: 'Medium' },
  { id: 'LLM02', risk: 'Sensitive information disclosure', control: 'Two gates before the model; masking; one response shape', residual: 'Low-Medium' },
  { id: 'LLM03', risk: 'Supply chain', control: 'Pinned Python lock and exact npm versions', residual: 'Medium' },
  { id: 'LLM04', risk: 'Data and model poisoning', control: 'No training; poisoned text reaches only its existing readers', residual: 'Low-Medium' },
  { id: 'LLM05', risk: 'Improper output handling', control: 'Markdown stripped; answers rendered as text', residual: 'Low' },
  { id: 'LLM06', risk: 'Excessive agency', control: 'The model has no tools; identity never from the model', residual: 'Low' },
  { id: 'LLM07', risk: 'System prompt leakage', control: 'The prompt holds no secrets or permissions', residual: 'Low' },
  { id: 'LLM08', risk: 'Vector and embedding weaknesses', control: 'Permission-aware index; local embedder', residual: 'Low' },
  { id: 'LLM09', risk: 'Misinformation', control: 'Grounding wall; citations; extractive fallback', residual: 'Medium' },
  { id: 'LLM10', risk: 'Unbounded consumption', control: 'No model call on refusal; caps on passages and body', residual: 'Medium' },
];

/** README.md "Claims we never make" (the row about the graph and doc view is left out: both are now built). */
export const NEVER: { never: string; instead: string; why: string }[] = [
  { never: 'Compliant with MAS / PDPA / AI Verify / ISO 42001', instead: 'Mapped to, with the gap list', why: 'No regulator or auditor has assessed it; it runs on synthetic data' },
  { never: 'Certified or audited', instead: 'Self-assessed; reviewed by AI models and the team', why: 'Every review so far is by AI models or by us; no human third party' },
  { never: 'Zero risk, unhackable, leak-proof', instead: '0 leaks in 5,958 requests, with a 95% upper bound of 0.10% per request', why: 'Absence of observed leaks is not proof of absence' },
  { never: 'Runs on Hy3', instead: 'Hy3 is wired in and tested with a capturing model; the live run waits on Tencent account verification', why: 'No live Hy3 call had succeeded when the evidence pack was written' },
  { never: 'Tested on real Slack, Confluence, Jira and Drive', instead: 'Mock platforms with the real APIs’ shapes; same connector code', why: 'There is no live tenant' },
  { never: 'Bias-free or fair', instead: 'Same rules for every role; quality per role published, uneven in places', why: 'See FAIRNESS_NOTE.md' },
  { never: 'Explains every decision to the user', instead: 'Explains every answer; never explains a refusal, by design', why: 'Explaining a denial leaks that something exists' },
  { never: 'Human in the loop', instead: 'Humans stay the authority on access; compliance can reconstruct and reveal; no review queue yet', why: 'No review queue is built' },
  { never: 'Production-ready', instead: 'Prototype with a deploy kit; no live deployment', why: 'Not deployed' },
  { never: 'Current eval numbers without the caveat', instead: 'From the 14 Oct run; re-run pending', why: 'They predate the 30 Oct pipeline edits' },
  { never: 'Any Aspire customer count or licence status', instead: '—', why: 'Not verified' },
];

export const DOCS: { file: string; what: string }[] = [
  { file: 'SYSTEM_CARD.md', what: 'Purpose, users, model chain, data, controls, limits; MAS AI inventory record' },
  { file: 'FRAMEWORK_CROSSWALK.md', what: 'AI Verify, IMDA GenAI, MAS FEAT, MAS AIRG, PDPA, ISO/IEC 42001, NIST AI RMF, EU AI Act' },
  { file: 'OWASP_LLM_REDTEAM.md', what: 'Each OWASP LLM Top 10 risk: control, test, result, residual; what broke and how we fixed it' },
  { file: 'AI_VERIFY_SELF_ASSESSMENT.md', what: 'The 11 AI Verify principles, one by one' },
  { file: 'PRIVACY_IMPACT_SUMMARY.md', what: 'What personal data is held, who sees it, what leaves; PDPA table' },
  { file: 'HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md', what: 'Who oversees what; escalation; incident procedures' },
  { file: 'EXPLAINABILITY.md', what: 'What each audience can see about an answer, and why a refusal is never explained' },
  { file: 'FAIRNESS_NOTE.md', what: 'Fairness for a permission-aware assistant; per-role quality' },
  { file: 'DATA_CARD_COMPANY_A.md', what: 'The synthetic world: generator, seed, planted facts' },
];
