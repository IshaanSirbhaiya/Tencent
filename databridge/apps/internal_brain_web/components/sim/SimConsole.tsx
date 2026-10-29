'use client';
// /dev/sim (admin_it): change the source systems the way an administrator would, then ask again.
// Each change reaches the Internal Brain only through the platform's signed webhook or poller.
import { ArrowRight, CheckCircle2, FilePenLine, FlaskConical, Lock, MessageSquareQuote, RotateCcw, UserMinus, XCircle, type LucideIcon } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { Page } from '@/components/shell/Page';
import { Badge, Button, PlatformIcon } from '@/components/ui';
import { api, type SimAction } from '@/lib/api';
import { fmtTime } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { simMessages } from '@/lib/i18n/audit';

interface Action {
  id: Exclude<SimAction, 'reset'>;
  label: string;
  platform: string;
  icon: LucideIcon;
  what: string;
  proves: { scenario: string; claim: string; tests: string[] };
  ask: { as: string; asName: string; vs: string; vsName: string; question: string; expect: string };
}

const ACTIONS: Action[] = [
  {
    id: 'revoke-channel',
    label: 'Remove jdoe from #db-migration',
    platform: 'slack',
    icon: UserMinus,
    what: 'Removes Jordan Doe from the private #db-migration Slack channel. Gate 2 re-checks membership live with Slack on the next question, even if the webhook is lost.',
    proves: {
      scenario: 'Scenario 4 · Revocation',
      claim: 'Access removed at the source is honoured on the very next question.',
      tests: ['test_scenarios_e2e.py::test_s4_revocation_live', 'test_scenarios_e2e.py::test_s4_revocation_without_webhook'],
    },
    ask: {
      as: 'jdoe',
      asName: 'Jordan',
      vs: 'priya.sre',
      vsName: 'Priya',
      question: 'What backfill throttle are we using for the ledger copy job?',
      expect: 'Jordan now gets the constant refusal; Priya, still a member, gets the cited answer.',
    },
  },
  {
    id: 'runbook-update',
    label: 'Edit failover runbook (new version)',
    platform: 'confluence',
    icon: FilePenLine,
    what: 'Adds step 4b to the "Payment service failover runbook" page in Confluence. The platform fires a signed webhook and the Internal Brain re-reads the page.',
    proves: {
      scenario: 'Scenario 2 · Freshness',
      claim: 'New content is answerable at once, citing the new version.',
      tests: ['test_scenarios_e2e.py::test_s2_freshness_via_webhook', 'test_pipeline_core.py::test_drift_read_through'],
    },
    ask: {
      as: 'priya.sre',
      asName: 'Priya',
      vs: 'jdoe',
      vsName: 'Jordan',
      question: "What's the latest runbook for the payment-service incident?",
      expect: 'The citation shows version 8 and a live re-read.',
    },
  },
  {
    id: 'restrict-page',
    label: "Restrict 'Settlement keys rotation' page",
    platform: 'confluence',
    icon: Lock,
    what: 'Adds a read restriction (payments leads only) to the Confluence page. Gate 2 asks Confluence live before the page can reach any model.',
    proves: {
      scenario: 'Scenario 4 · Revocation',
      claim: 'A page restricted at the source stops appearing in answers, with no hint that it exists.',
      tests: ['test_scenarios_e2e.py::test_s4_revocation_live[s4_restrict_page-S4-q2]'],
    },
    ask: {
      as: 'jdoe',
      asName: 'Jordan',
      vs: 'priya.sre',
      vsName: 'Priya',
      question: 'When is the next settlement key rotation window?',
      expect: 'Jordan gets the constant refusal; Priya still gets the cited answer.',
    },
  },
];

/** PROPOSED query string for /compare (PRESENTER_KEYS §6); the Compare page reads it when supported. */
function compareHref(a: Action['ask']) {
  const q = new URLSearchParams({ mode: 'people', left: a.as, right: a.vs, q: a.question });
  return `/compare?${q.toString()}`;
}

interface LogRow {
  key: number;
  label: string;
  ok: boolean;
  action?: string;
  at: string;
  detail?: string;
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="text-micro font-semibold uppercase tracking-wide text-subtle">{children}</p>;
}

export function SimConsole() {
  const t = useMessages(simMessages);
  const [busy, setBusy] = useState<SimAction | null>(null);
  const [log, setLog] = useState<LogRow[]>([]);
  const [done, setDone] = useState<Set<SimAction>>(new Set());

  async function run(id: SimAction, label: string) {
    setBusy(id);
    try {
      const r = await api.sim(id);
      setLog((l) => [{ key: Date.now(), label, ok: !!r.ok, action: r.action, at: r.at }, ...l]);
      setDone((d) => (id === 'reset' ? new Set() : new Set(d).add(id)));
    } catch (e) {
      setLog((l) => [{ key: Date.now(), label, ok: false, at: new Date().toISOString(), detail: e instanceof Error && e.message ? e.message : 'temporarily unavailable' }, ...l]);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Page eyebrow={t('sim.eyebrow')} title={t('sim.title')} description={t('sim.description')} width="wide">
      <div className="space-y-6">
        <ul className="grid gap-4 lg:grid-cols-3">
          {ACTIONS.map((a) => {
            const ran = done.has(a.id);
            return (
              <li
                key={a.id}
                className="flex flex-col rounded-xl border border-line bg-surface"
                data-tour={a.id === 'revoke-channel' ? 'sim-revoke' : undefined}
                data-testid={`sim-card-${a.id}`}
              >
                <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
                  <h2 className="flex items-start gap-2 text-heading-sm text-fg">
                    <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-muted">
                      <a.icon aria-hidden className="h-4 w-4" />
                    </span>
                    <span>{a.label}</span>
                  </h2>
                  <PlatformIcon platform={a.platform} showLabel tone="platform" className="shrink-0 text-xs" />
                </div>

                <div className="flex flex-1 flex-col gap-4 px-4 py-4">
                  <div>
                    <Label>{t('sim.what')}</Label>
                    <p className="mt-1 text-body-sm text-muted">{a.what}</p>
                  </div>
                  <div>
                    <Label>{t('sim.proves')}</Label>
                    <p className="mt-1 text-body-sm text-fg">
                      <span className="font-medium">{a.proves.scenario}.</span> {a.proves.claim}
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {a.proves.tests.map((x) => (
                        <li key={x} className="break-all font-mono text-2xs text-subtle">
                          {x}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-lg border border-line bg-raised/50 p-3">
                    <Label>{t('sim.thenAsk', { name: a.ask.asName })}</Label>
                    <p className="mt-1 flex gap-1.5 text-body-sm text-fg">
                      <MessageSquareQuote aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
                      <span>&ldquo;{a.ask.question}&rdquo;</span>
                    </p>
                    <p className="mt-1 text-caption text-muted">{a.ask.expect}</p>
                    <Link
                      href={compareHref(a.ask)}
                      className="mt-2 inline-flex min-h-[1.5rem] items-center gap-1 text-caption font-medium text-accent underline-offset-2 hover:underline"
                    >
                      {t('sim.compare', { a: a.ask.asName, b: a.ask.vsName })}
                      <ArrowRight aria-hidden className="h-3.5 w-3.5" />
                    </Link>
                  </div>
                  <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
                    <Button onClick={() => void run(a.id, a.label)} busy={busy === a.id} disabled={busy !== null} variant="primary" icon={a.icon} className="h-auto min-h-[2.25rem] max-w-full whitespace-normal py-2 text-left">
                      {a.label}
                    </Button>
                    {ran ? (
                      <Badge tone="ok" icon={CheckCircle2}>
                        {t('sim.applied')}
                      </Badge>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>

        <div className="grid gap-4 lg:grid-cols-3">
          <section aria-labelledby="reset-heading" className="flex flex-col rounded-xl border border-line bg-surface p-4">
            <h2 id="reset-heading" className="flex items-center gap-2 text-heading-sm text-fg">
              <RotateCcw aria-hidden className="h-4 w-4 text-muted" /> {t('sim.resetTitle')}
            </h2>
            <p className="mt-1 text-body-sm text-muted">{t('sim.resetBody')}</p>
            <div className="mt-3">
              <Button onClick={() => void run('reset', 'Reset demo')} busy={busy === 'reset'} disabled={busy !== null} icon={RotateCcw}>
                Reset demo
              </Button>
            </div>
          </section>

          <section aria-labelledby="log-heading" className="rounded-xl border border-line bg-surface lg:col-span-2">
            <h2 id="log-heading" className="flex items-center gap-2 border-b border-line px-4 py-3 text-heading-sm text-fg">
              <FlaskConical aria-hidden className="h-4 w-4 text-muted" /> {t('sim.log')}
            </h2>
            <div aria-live="polite" className="px-4 py-3">
              {log.length === 0 ? (
                <p className="text-body-sm text-muted">{t('sim.logEmpty')}</p>
              ) : (
                <ol className="divide-y divide-line" data-testid="sim-log">
                  {log.map((r) => (
                    <li key={r.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-body-sm">
                      {r.ok ? (
                        <Badge tone="ok" icon={CheckCircle2}>
                          Done
                        </Badge>
                      ) : (
                        <Badge tone="neutral" icon={XCircle}>
                          Failed
                        </Badge>
                      )}
                      <span className="font-medium">{r.label}</span>
                      {r.action ? <span className="font-mono text-2xs text-subtle">{r.action}</span> : null}
                      {r.detail ? <span className="text-2xs text-muted">{r.detail}</span> : null}
                      <span className="ml-auto text-caption text-muted tabular-nums">{fmtTime(r.at)}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </section>
        </div>
      </div>
    </Page>
  );
}
