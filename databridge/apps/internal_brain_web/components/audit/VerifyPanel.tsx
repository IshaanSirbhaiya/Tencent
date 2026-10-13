'use client';
import { CheckCircle2, CircleDashed, ShieldCheck, XCircle } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/lib/api';
import { fmtTime, truncHash } from '@/lib/format';
import type { VerifyResult } from '@/lib/types';
import { Badge, Button } from '../ui';

export function VerifyPanel() {
  const [res, setRes] = useState<VerifyResult | null>(null);
  const [at, setAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.verify();
      setRes(r);
      setAt(new Date().toISOString());
    } catch {
      setError('Verification could not run — the audit service is unavailable.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="verify-heading" className="rounded-lg border border-line bg-surface" data-testid="verify-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h2 id="verify-heading" className="text-sm font-semibold">
            Ledger integrity
          </h2>
          <p className="text-2xs text-muted">Re-checks every hash link, the seal, the anchor directory and the last externally published head.</p>
        </div>
        <Button onClick={run} busy={busy} icon={ShieldCheck}>
          Verify audit chain
        </Button>
      </div>
      <div className="px-4 py-3" aria-live="polite">
        {error ? (
          <p role="alert" className="text-sm">
            {error}
          </p>
        ) : !res ? (
          <p className="text-xs text-muted">Not verified in this session yet.</p>
        ) : (
          <div className="space-y-3">
            <p className="flex items-center gap-2 text-sm font-medium" data-testid="verify-status">
              {res.ok ? <CheckCircle2 aria-hidden className="h-4 w-4 text-ok" /> : <XCircle aria-hidden className="h-4 w-4 text-bad" />}
              {res.ok ? 'Chain intact' : 'Problems found'}
              <span className="text-2xs font-normal text-muted">checked {fmtTime(at)}</span>
            </p>
            <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
              <div>
                <dt className="text-muted">Records</dt>
                <dd className="tabular text-sm font-medium" data-testid="verify-records">
                  {res.records.toLocaleString()}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-muted">Head hash</dt>
                <dd className="truncate font-mono text-sm" title={res.head}>
                  {truncHash(res.head)}
                </dd>
              </div>
              <div>
                <dt className="text-muted">External head</dt>
                <dd className="mt-0.5">
                  {res.external_head_match === true ? (
                    <Badge tone="ok" icon={CheckCircle2}>
                      Matches published head
                    </Badge>
                  ) : res.external_head_match === false ? (
                    <Badge tone="bad" icon={XCircle}>
                      Does not match
                    </Badge>
                  ) : (
                    <Badge icon={CircleDashed}>No external head yet</Badge>
                  )}
                </dd>
              </div>
            </dl>
            <div>
              <h3 className="text-xs text-muted">Problems</h3>
              {res.problems.length === 0 ? (
                <p className="text-xs">None</p>
              ) : (
                <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs">
                  {res.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
