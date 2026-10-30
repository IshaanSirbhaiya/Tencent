// Formatting for the instrumentation pages. Format only: every figure comes from the backend; the
// only arithmetic here is a percentage of totals the backend returned (PLAN rule 5).

export const nf = new Intl.NumberFormat('en-SG');

export function int(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? nf.format(n) : '—';
}

export function ms(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isFinite(n) ? `${nf.format(Math.round(n))} ms` : '—';
}

/** `part` as a percentage of `whole` (both returned by the backend). */
export function pct(part: number | null | undefined, whole: number | null | undefined, dp = 1): string {
  if (typeof part !== 'number' || typeof whole !== 'number' || whole <= 0) return '—';
  return `${((part / whole) * 100).toFixed(dp)}%`;
}

/** A backend fraction (0.8123) as a percentage. */
export function rate(f: number | null | undefined, dp = 1): string {
  return typeof f === 'number' && Number.isFinite(f) ? `${(f * 100).toFixed(dp)}%` : '—';
}

export function shortHash(h: string | null | undefined, head = 6, tail = 4): string {
  if (!h) return '—';
  return h.length > head + tail + 1 ? `${h.slice(0, head)}…${h.slice(-tail)}` : h;
}

export const STAGE_LABEL: Record<string, string> = {
  accept: 'Accept',
  gate1: 'Gate 1',
  gate2: 'Gate 2',
  seal: 'Seal',
  compose: 'Compose',
  release: 'Release',
};

export const STAGE_HINT: Record<string, string> = {
  accept: 'Verify the signed identity and append request.accepted',
  gate1: 'Permitted-index search, then the relevance gate',
  gate2: 'Live permission check with each source',
  seal: 'Append retrieval.decided before any model call',
  compose: 'Model chain, then the grounding check',
  release: 'Store the answer, then append answer.released',
};

export const CHANNEL_LABEL: Record<string, string> = {
  ui: 'Web UI',
  'workbuddy-mcp': 'WorkBuddy (MCP)',
  api: 'API',
  replay: 'Seeded replay',
};

export function channelLabel(c: string | null | undefined): string {
  if (!c) return '—';
  return CHANNEL_LABEL[c] ?? c;
}

/** Honest model names, keyed by the backend's "provider/model" string. */
export function modelName(key: string): { label: string; detail: string; kind: 'primary' | 'fallback' | 'extractive' | 'other' } {
  const [provider, model = ''] = key.split('/');
  const m = model.toLowerCase();
  if (provider === 'deterministic' || m.startsWith('extractive')) return { label: 'Extractive (no model)', detail: key, kind: 'extractive' };
  if (m.includes('hy3') || m.includes('hunyuan')) return { label: 'Hy3', detail: key, kind: 'primary' };
  if (m.includes('deepseek')) return { label: 'DeepSeek (fallback)', detail: key, kind: 'fallback' };
  return { label: 'AI model', detail: 'AI model', kind: 'other' }; // never the provider or its id
}

export function modelInfoName(m: { provider: string; model: string } | null | undefined): string {
  return m ? modelName(`${m.provider}/${m.model}`).label : '—';
}

/** Plain-English gloss for Gate-2 reason codes (compliance views only). The code is always shown too. */
export const REASON_LABEL: Record<string, string> = {
  ok: 'Verified with the source',
  updated_since_indexed: 'Allowed; newer version re-read',
  not_permitted: 'Source says no access',
  not_member: 'Not a channel member',
  guest_not_member: 'Guest, not a member',
  source_error: 'Source error (fail closed)',
  source_timeout: 'Source timed out (fail closed)',
  source_bad_payload: 'Bad source response (fail closed)',
  source_ambiguous: 'Ambiguous source answer (fail closed)',
  deactivated: 'Account deactivated',
  identity_changed: 'Identity changed',
  no_platform_identity: 'No platform identity',
  unknown_user: 'Unknown user',
  page_gone: 'Page no longer exists',
  thread_gone: 'Thread no longer exists',
  issue_gone: 'Issue no longer exists',
  file_gone: 'File no longer exists',
  channel_gone: 'Channel no longer exists',
  issue_moved: 'Issue moved',
  no_admin_checker: 'Cannot be verified',
  unsupported_acl: 'Unsupported permission model',
};

export function reasonLabel(code: string): string {
  if (!code) return 'No reason recorded';
  return REASON_LABEL[code] ?? code.replace(/[._]/g, ' ');
}

export const VERDICT_ALLOW = new Set(['verified', 'drift_material']);
