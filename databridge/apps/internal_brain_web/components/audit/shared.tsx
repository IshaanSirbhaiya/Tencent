'use client';
// Small pieces shared by the audit console's table and drawer. Compliance-only (Developer mode):
// Gate-2 decisions and reason codes are shown here on purpose (AGENT_RULES §2, D-05/D-06).
import { Ban, CheckCircle2, CircleDashed, ShieldX } from 'lucide-react';
import { Badge } from '@/components/ui';
import { useMessages } from '@/lib/i18n';
import { auditMessages } from '@/lib/i18n/audit';
import { ALLOW_VERDICTS, type AuditEvent, type Gate2Decision } from '@/lib/types';

/** Ledger timestamp WITH the year, e.g. "30 Oct 2026, 05:09 SGT" (the table used to drop the year). */
export function fmtStamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const x = new Date(iso);
  if (Number.isNaN(x.getTime())) return '—';
  return (
    new Intl.DateTimeFormat('en-SG', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: 'Asia/Singapore',
    }).format(x) + ' SGT'
  );
}

export function titleFor(e: AuditEvent, docId: string): string {
  return e.considered_meta?.find((m) => m.doc_id === docId)?.title ?? docId;
}

const REASON_LABEL: Record<string, string> = {
  verified: 'checked',
  ok: 'checked',
  drift_material: 'changed, re-read',
  forbidden: 'not allowed',
  not_member: 'not allowed',
  timeout: 'source too slow',
  source_error: 'source error',
};

/** A plain-English label for a Gate-2 verdict or reason code; unknown codes are shown as written. */
export function plainReason(code: string): string {
  if (REASON_LABEL[code]) return REASON_LABEL[code];
  if (/not_channel_member|not_member/.test(code)) return 'not a member';
  if (/restricted/.test(code)) return 'page restricted';
  if (/no_browse|permission|forbidden/.test(code)) return 'not allowed';
  return code;
}

export const CHANNEL: Record<string, string> = { ui: 'Web', 'workbuddy-mcp': 'WorkBuddy', api: 'API', replay: 'Replay' };

export const isAllow = (d: Gate2Decision) => ALLOW_VERDICTS.has(d.verdict);

/** Deny first, so a denied source is always among the decisions shown in a short list. */
export function decisionsDenyFirst(e: AuditEvent): Gate2Decision[] {
  const g = e.gate2 ?? [];
  return [...g.filter((d) => !isAllow(d)), ...g.filter(isAllow)];
}

export function DecisionBadge({ d }: { d: Gate2Decision }) {
  const t = useMessages(auditMessages);
  const allow = isAllow(d);
  const code = allow ? d.verdict : d.reason_code || d.verdict;
  return (
    <span className="inline-flex items-center gap-1" title={code}>
      {allow ? (
        <Badge tone="ok" icon={CheckCircle2}>
          {t('audit.decision.allow')}
        </Badge>
      ) : (
        <Badge tone="locked" icon={ShieldX}>
          {t('audit.decision.deny')}
        </Badge>
      )}
      <span className="text-2xs text-subtle">{plainReason(code)}</span>
    </span>
  );
}

export function OutcomeBadge({ e }: { e: AuditEvent }) {
  const t = useMessages(auditMessages);
  // "No answer (refused)": the User-mode term first, the technical word in brackets.
  if (e.refused === true)
    return (
      <Badge icon={Ban} tone="neutral">
        {t('audit.table.refused')}
      </Badge>
    );
  if (e.refused === false)
    return (
      <Badge icon={CheckCircle2} tone="accent">
        {t('audit.table.answered')}
      </Badge>
    );
  return <Badge icon={CircleDashed}>{t('audit.table.inProgress')}</Badge>;
}
