// The Internal Brain request pipeline as drawn on Mission control. Text only; every live figure is
// read from /audit/stats `stages_ms` (compliance) at render time.
import type { PctMs, StageV1 } from '@/lib/types';
import { ms } from './format';

export interface Timing {
  /** p50 for the node, or null when the viewer has no org numbers. */
  value: string | null;
  note?: string;
}

export type PipelineKind = 'identity' | 'ledger' | 'gate' | 'model' | 'check' | 'answer';

export interface PipelineStep {
  id: string;
  title: string;
  kind: PipelineKind;
  /** Short static line shown on the node when live timings are not available to the viewer. */
  short: string;
  /** The stage whose p50 is shown on the node. */
  stage?: StageV1 | 'total';
  /** When the timing is shared with another step, say so instead of repeating the number. */
  sharedWith?: string;
  explain: string;
  guarantee: string;
}

export const PIPELINE: PipelineStep[] = [
  {
    id: 'identity',
    title: 'Signed identity',
    kind: 'identity',
    short: 'Token verified',
    explain:
      'Who is asking comes only from the signed session cookie, or from the bearer token the WorkBuddy connector presents. No request field, tool argument or model output can name a person, so nobody can ask "as" someone else.',
    guarantee: 'Identity comes only from the signed token.',
  },
  {
    id: 'ledger-accepted',
    title: 'Ledger: accepted',
    kind: 'ledger',
    short: 'request.accepted',
    stage: 'accept',
    explain:
      'Before anything is searched, a request.accepted record is appended to the hash-chained access ledger. If the ledger cannot record it, the request stops with a 503 and nothing else happens.',
    guarantee: 'Write-ahead audit: no record, no retrieval.',
  },
  {
    id: 'gate1',
    title: 'Gate 1: permitted index',
    kind: 'gate',
    short: 'Search only what you can open',
    stage: 'gate1',
    explain:
      'Search runs only over index entries whose stored permissions include this person. A document they cannot open is never a candidate, so it cannot be ranked, summarised or counted.',
    guarantee: 'Filter before the model, step one.',
  },
  {
    id: 'relevance',
    title: 'Relevance gate',
    kind: 'gate',
    short: 'Weak matches dropped',
    stage: 'gate1',
    sharedWith: 'timed with Gate 1',
    explain:
      'Candidates are ranked and weak matches are dropped, so an unrelated or made-up question is refused instead of being answered from noise. A restricted question and a made-up one end in the same refusal.',
    guarantee: 'Restricted and non-existent look identical.',
  },
  {
    id: 'gate2',
    title: 'Gate 2: live source check',
    kind: 'gate',
    short: 'Re-checked with each source now',
    stage: 'gate2',
    explain:
      'Each remaining document is re-checked with its own system (Confluence, Jira, Slack or Google Drive) at the moment of the question. Any error, timeout or rate limit means deny, so a permission revoked a minute ago already counts.',
    guarantee: 'Fail closed: any doubt means deny.',
  },
  {
    id: 'ledger-decided',
    title: 'Ledger: decided',
    kind: 'ledger',
    short: 'retrieval.decided',
    stage: 'seal',
    explain:
      'What was considered, what Gate 2 allowed and the reason for every denial are sealed in a retrieval.decided record before any model is called. Those reasons are visible to the compliance role only.',
    guarantee: 'No passage reaches a model without a sealed decision.',
  },
  {
    id: 'model',
    title: 'Model chain',
    kind: 'model',
    short: 'Configured model → extractive',
    stage: 'compose',
    explain:
      'Only Gate-2-allowed passages are sent. The configured model answers: Hy3 (Tencent Hunyuan, through TokenHub) with DeepSeek as its fallback when the TokenHub key is set, otherwise an optional stand-in AI model when one is switched on. If no model is configured or none answers, a deterministic extractive answer quotes the permitted passages with no model at all. Every answer is labelled with what wrote it.',
    guarantee: 'The system still answers safely without a model.',
  },
  {
    id: 'grounding',
    title: 'Grounding check',
    kind: 'check',
    short: 'Every sentence needs a citation',
    stage: 'compose',
    sharedWith: 'timed with compose',
    explain:
      'Every sentence must be supported by a cited, permitted passage. Unsupported sentences are dropped (the count is sealed); if what remains fails, the extractive answer is served instead. No text is shown before this check passes.',
    guarantee: 'No value without a pointer.',
  },
  {
    id: 'ledger-released',
    title: 'Ledger: released',
    kind: 'ledger',
    short: 'answer.released',
    stage: 'release',
    explain:
      'The model used, the citations, the stage timings and a hash of the answer are sealed in answer.released before the response leaves the server. Compliance can verify the whole chain at any time.',
    guarantee: 'Write-ahead audit: no record, no answer.',
  },
  {
    id: 'answer',
    title: 'Answer',
    kind: 'answer',
    short: 'Cited answer or one refusal',
    stage: 'total',
    explain:
      'The asker sees the verified answer with [n] citation chips and a constant scope note. A refusal reads exactly the same whether the evidence is restricted or does not exist, and no count of hidden items is ever shown.',
    guarantee: 'One response shape for every asker.',
  },
];

/** Live p50 per node from /audit/stats stages_ms (null stats: the static description is shown). */
export function timingsFrom(stages: Partial<Record<StageV1, PctMs>> | null | undefined, totalP50: number | null | undefined): Record<string, Timing> {
  const out: Record<string, Timing> = {};
  for (const s of PIPELINE) {
    if (!stages || !s.stage) {
      out[s.id] = { value: null };
      continue;
    }
    const v = s.stage === 'total' ? totalP50 : stages[s.stage]?.p50;
    out[s.id] = typeof v === 'number' ? { value: ms(v), note: s.sharedWith ?? (s.stage === 'total' ? 'end to end' : undefined) } : { value: null };
  }
  return out;
}

