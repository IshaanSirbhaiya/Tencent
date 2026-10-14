'use client';
import { CheckCircle2, FilePenLine, Lock, RotateCcw, UserMinus, XCircle, type LucideIcon } from 'lucide-react';
import { useState } from 'react';
import { AppShell } from '@/components/AppShell';
import { PlatformIcon } from '@/components/PlatformIcon';
import { Badge, Button } from '@/components/ui';
import { api, type SimAction } from '@/lib/api';
import { fmtTime } from '@/lib/format';

interface Action {
  id: SimAction;
  label: string;
  platform: string;
  icon: LucideIcon;
  what: string;
  then: string;
}

const ACTIONS: Action[] = [
  {
    id: 'runbook-update',
    label: 'Edit failover runbook (new version)',
    platform: 'confluence',
    icon: FilePenLine,
    what: 'Adds step 4b to "Payment service failover runbook" in Confluence. The platform fires a signed webhook; the Internal Brain re-reads the page.',
    then: 'Then, as Priya: "What\'s the latest runbook for the payment-service incident?" — the citation shows version 8.',
  },
  {
    id: 'revoke-channel',
    label: 'Remove jdoe from #db-migration',
    platform: 'slack',
    icon: UserMinus,
    what: 'Removes Jordan Doe from the private Slack channel. Access is re-checked live on the next question.',
    then: 'Then, as Jordan: "What backfill throttle are we using for the ledger copy job?" — the answer no longer uses that channel.',
  },
  {
    id: 'restrict-page',
    label: "Restrict 'Settlement keys rotation' page",
    platform: 'confluence',
    icon: Lock,
    what: 'Adds a read restriction (payments leads only) to the Confluence page.',
    then: 'Then, as Jordan: "When is the next settlement key rotation window?" — the answer no longer uses that channel.',
  },
  {
    id: 'reset',
    label: 'Reset demo',
    platform: '',
    icon: RotateCcw,
    what: 'Restores the demo data on every demo platform. The audit log is never reset.',
    then: 'Use before each demo run.',
  },
];

interface LogRow {
  key: number;
  label: string;
  ok: boolean;
  action?: string;
  at: string;
  detail?: string;
}

function SimView() {
  const [busy, setBusy] = useState<SimAction | null>(null);
  const [log, setLog] = useState<LogRow[]>([]);

  async function run(a: Action) {
    setBusy(a.id);
    try {
      const r = await api.sim(a.id);
      setLog((l) => [{ key: Date.now(), label: a.label, ok: !!r.ok, action: r.action, at: r.at }, ...l]);
    } catch (e) {
      setLog((l) => [{ key: Date.now(), label: a.label, ok: false, at: new Date().toISOString(), detail: e instanceof Error ? e.message : 'failed' }, ...l]);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Platform simulator</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Change the source systems the way an administrator would. Changes reach the Internal Brain only through each platform&apos;s signed webhook or poller, never by writing to its index. The IT admin has no access to the audit console.
        </p>
      </div>

      <ul className="grid gap-4 md:grid-cols-2">
        {ACTIONS.map((a) => (
          <li key={a.id} className="flex flex-col rounded-lg border border-line bg-surface p-4" data-tour={a.id === 'revoke-channel' ? 'sim-revoke' : undefined}>
            <div className="flex items-center justify-between gap-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <a.icon aria-hidden className="h-4 w-4 text-muted" /> {a.label}
              </h2>
              {a.platform ? <PlatformIcon platform={a.platform} className="text-xs text-muted" /> : null}
            </div>
            <p className="mt-2 text-xs text-muted">{a.what}</p>
            <p className="mt-2 text-xs">{a.then}</p>
            <div className="mt-4">
              <Button onClick={() => void run(a)} busy={busy === a.id} disabled={busy !== null} variant={a.id === 'reset' ? 'secondary' : 'primary'} icon={a.icon}>
                {a.label}
              </Button>
            </div>
          </li>
        ))}
      </ul>

      <section aria-labelledby="log-heading" className="rounded-lg border border-line bg-surface">
        <h2 id="log-heading" className="border-b border-line px-4 py-3 text-sm font-semibold">
          Actions this session
        </h2>
        <div aria-live="polite" className="px-4 py-3">
          {log.length === 0 ? (
            <p className="text-xs text-muted">No actions yet.</p>
          ) : (
            <ol className="space-y-2" data-testid="sim-log">
              {log.map((r) => (
                <li key={r.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                  {r.ok ? (
                    <Badge tone="ok" icon={CheckCircle2}>
                      Done
                    </Badge>
                  ) : (
                    <Badge tone="bad" icon={XCircle}>
                      Failed
                    </Badge>
                  )}
                  <span className="font-medium">{r.label}</span>
                  {r.action ? <span className="font-mono text-2xs text-muted">{r.action}</span> : null}
                  {r.detail ? <span className="text-2xs text-muted">{r.detail}</span> : null}
                  <span className="tabular text-muted">{fmtTime(r.at)}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </section>
    </div>
  );
}

export default function SimPage() {
  return (
    <AppShell requireRole="admin_it">
      <SimView />
    </AppShell>
  );
}
