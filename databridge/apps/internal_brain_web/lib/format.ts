const TZ = 'Asia/Singapore';

function d(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const x = new Date(iso);
  return Number.isNaN(x.getTime()) ? null : x;
}

export function fmtDate(iso: string | null | undefined): string {
  const x = d(iso);
  if (!x) return '—';
  return new Intl.DateTimeFormat('en-SG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: TZ }).format(x);
}

export function fmtTime(iso: string | null | undefined): string {
  const x = d(iso);
  if (!x) return '—';
  return (
    new Intl.DateTimeFormat('en-SG', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: TZ }).format(x) +
    ' SGT'
  );
}

export function fmtDateTime(iso: string | null | undefined): string {
  const x = d(iso);
  if (!x) return '—';
  return (
    new Intl.DateTimeFormat('en-SG', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: TZ,
    }).format(x) + ' SGT'
  );
}

export function fmtAgo(iso: string | null | undefined, now = Date.now()): string {
  const x = d(iso);
  if (!x) return 'never';
  const s = Math.max(0, Math.round((now - x.getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

export function fmtDuration(s: number): string {
  if (s < 60) return `${s} s`;
  if (s % 60 === 0) return `${s / 60} min`;
  return `${Math.round(s / 60)} min`;
}

export const PLATFORM_LABEL: Record<string, string> = {
  confluence: 'Confluence',
  jira: 'Jira',
  slack: 'Slack',
  drive: 'Google Drive',
};

export function modelLabel(m: { provider: string; model: string; fallback_used?: boolean } | null | undefined): string {
  if (!m) return '—';
  const id = m.model.toLowerCase();
  if (m.provider === 'deterministic' || id.startsWith('extractive')) return 'Deterministic extractive';
  if (id.includes('hy3') || id.includes('hunyuan')) return 'Tencent Hunyuan hy3';
  if (id.includes('deepseek')) return 'DeepSeek (fallback)';
  return `${m.provider} · ${m.model}`;
}

export function truncHash(h: string, n = 12): string {
  if (!h) return '—';
  const s = h.replace(/^sha256:/, '');
  return s.length <= n * 2 ? s : `${s.slice(0, n)}…${s.slice(-6)}`;
}

/** How a container is named on screen: Slack channels get their '#'. The name itself is the IB's. */
export function placeLabel(platform: string, name: string): string {
  if (platform === 'slack' && name && !name.startsWith('#')) return `#${name}`;
  return name;
}

export const PLACE_NOUN: Record<string, [string, string]> = {
  confluence: ['space', 'spaces'],
  jira: ['project', 'projects'],
  slack: ['channel', 'channels'],
  drive: ['shared drive', 'shared drives'],
};

export const KIND_LABEL: Record<string, string> = { page: 'Page', issue: 'Issue', thread: 'Thread', file: 'File' };

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** Short, honest version text: a number is a version, an ISO time is a revision date, else an opaque revision id. */
export function shortVersion(v: string): { text: string; full: string } {
  if (!v) return { text: '—', full: '' };
  if (/^\d+$/.test(v)) return { text: `v${v}`, full: `Version ${v}` };
  // Jira's version is its last-updated time, already shown as "Updated …".
  if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return { text: '', full: `Revision of ${v}` };
  const head = v.split('|')[0];
  return { text: `rev ${head.length > 10 ? `${head.slice(0, 10)}…` : head}`, full: `Revision ${v}` };
}
