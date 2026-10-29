import { redirect } from 'next/navigation';

// The audit console moved to Developer mode. Query parameters (?verify=1, ?tamper=1) are kept.
export default function AuditRedirect({ searchParams }: { searchParams: Record<string, string | string[] | undefined> }) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(searchParams)) {
    if (typeof v === 'string') q.set(k, v);
    else if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
  }
  const s = q.toString();
  redirect(`/dev/audit${s ? `?${s}` : ''}`);
}
