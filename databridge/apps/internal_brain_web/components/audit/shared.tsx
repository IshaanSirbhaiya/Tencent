'use client';
// Small pieces shared by the audit console's table and drawer. Compliance-only (Developer mode):
// Gate-2 decisions and reason codes are shown here on purpose (AGENT_RULES §2, D-05/D-06).
import { Ban, CheckCircle2, CircleDashed, ShieldX } from 'lucide-react';
import { Badge } from '@/components/ui';
import { ALLOW_VERDICTS, type AuditEvent, type Gate2Decision } from '@/lib/types';

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
  const allow = isAllow(d);
  const code = allow ? d.verdict : d.reason_code || d.verdict;
  return (
    <span className="inline-flex items-center gap-1" title={code}>
      {allow ? (
        <Badge tone="ok" icon={CheckCircle2}>
          Allow
        </Badge>
      ) : (
        <Badge tone="locked" icon={ShieldX}>
          Deny
        </Badge>
      )}
      <span className="text-2xs text-subtle">{plainReason(code)}</span>
    </span>
  );
}

export function OutcomeBadge({ e }: { e: AuditEvent }) {
  if (e.refused === true)
    return (
      <Badge icon={Ban} tone="neutral">
        Refused
      </Badge>
    );
  if (e.refused === false)
    return (
      <Badge icon={CheckCircle2} tone="accent">
        Answered
      </Badge>
    );
  return <Badge icon={CircleDashed}>In progress</Badge>;
}
