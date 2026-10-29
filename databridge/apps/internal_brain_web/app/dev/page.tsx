'use client';
import { Activity, Database, FlaskConical, Gauge, RotateCw, ShieldCheck, Timer, Waypoints } from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { SourceChip } from '@/components/analytics/ChartCard';
import { fillDays } from '@/components/analytics/DailyOutcomes';
import { int, ms, rate } from '@/components/analytics/format';
import { PIPELINE, timingsFrom } from '@/components/analytics/pipeline';

import { OrgNumbersLock, toLoadError, Unavailable, type LoadError } from '@/components/analytics/States';
import { Freshness, ModelStatus } from '@/components/analytics/SystemStatus';
import { RecentRequests, type RecentRow } from '@/components/journey/RecentRequests';
import { Page } from '@/components/shell/Page';
import { useSession } from '@/components/shell/Providers';
import { Button, Card, CardBody, CardHeader, CardTitle, CodeHash, KpiTile, Skeleton, SkeletonText } from '@/components/ui';
import { Sparkline } from '@/components/ui/sparkline';
import { analyticsApi, api } from '@/lib/api';
import { fmtDate } from '@/lib/format';
import type { AuditStatsV1, EvalSummary, IbStatus, SourceStatus } from '@/lib/types';

const PipelineDiagram = dynamic(() => import('@/components/analytics/PipelineDiagram'), {
  ssr: false,
  loading: () => <Skeleton className="h-[230px] w-full" />,
});

const WINDOW_DAYS = 30;

function StepDetail({ id, live }: { id: string; live: string | null }) {
  const step = PIPELINE.find((s) => s.id === id) ?? PIPELINE[0];
  const n = PIPELINE.indexOf(step) + 1;
  return (
    <div className="grid gap-4 border-t border-line px-4 py-4 md:grid-cols-[minmax(0,1fr)_17rem]" aria-live="polite" data-testid="pipeline-detail">
      <div className="min-w-0">
        <p className="text-micro font-semibold uppercase tracking-wider text-subtle">
          Step {n} of {PIPELINE.length}
        </p>
        <h3 className="mt-0.5 text-heading-sm text-fg">{step.title}</h3>
        <p className="mt-1.5 max-w-prose text-body-sm text-muted">{step.explain}</p>
      </div>
      <div className="space-y-2">
        <div className="rounded-md border border-accent/40 bg-accent/[0.06] px-3 py-2">
          <p className="flex items-center gap-1.5 text-micro font-semibold uppercase tracking-wide text-accent">
            <ShieldCheck aria-hidden className="h-3.5 w-3.5" /> Guarantee
          </p>
          <p className="mt-0.5 text-body-sm text-fg">{step.guarantee}</p>
        </div>
        <div className="rounded-md border border-line px-3 py-2">
          <p className="text-micro font-semibold uppercase tracking-wide text-subtle">Live p50</p>
          <p className="mt-0.5 text-body-sm tabular-nums text-fg">
            {live ?? <span className="text-muted">{step.stage ? 'Visible to the compliance role' : 'Not a timed stage'}</span>}
            {live && step.sharedWith ? <span className="text-subtle"> · {step.sharedWith}</span> : null}
          </p>
        </div>
      </div>
    </div>
  );
}

export default function MissionControl() {
  const { me, hasRole } = useSession();
  const compliance = hasRole('compliance_officer');
  const [stats, setStats] = useState<AuditStatsV1 | null>(null);
  const [statsErr, setStatsErr] = useState<LoadError | null>(null);
  const [evalSum, setEvalSum] = useState<EvalSummary | null>(null);
  const [sources, setSources] = useState<SourceStatus[] | null>(null);
  const [sourcesErr, setSourcesErr] = useState(false);
  const [status, setStatus] = useState<IbStatus | null>(null);
  const [statusErr, setStatusErr] = useState(false);
  const [recent, setRecent] = useState<RecentRow[] | null>(null);
  const [recentErr, setRecentErr] = useState(false);
  const [selected, setSelected] = useState('gate2');
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(() => {
    setNow(Date.now());
    setSources(null);
    setSourcesErr(false);
    api.sources().then(setSources).catch(() => setSourcesErr(true));
    setStatus(null);
    setStatusErr(false);
    analyticsApi.status().then(setStatus).catch(() => setStatusErr(true));
    setRecent(null);
    setRecentErr(false);
    const rp: Promise<RecentRow[]> = compliance ? analyticsApi.recentRequests(8) : analyticsApi.history().then((h) => h.items ?? []);
    rp.then((r) => setRecent(r.slice(0, 8))).catch(() => setRecentErr(true));
    if (compliance) {
      setStats(null);
      setStatsErr(null);
      analyticsApi.stats(WINDOW_DAYS).then(setStats).catch((e) => setStatsErr(toLoadError(e)));
      analyticsApi.evalSummary().then(setEvalSum).catch(() => setEvalSum(null));
    }
  }, [compliance]);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  const timings = useMemo(() => timingsFrom(compliance ? stats?.stages_ms : null, stats?.latency_ms?.p50), [compliance, stats]);
  const onSelect = useCallback((id: string) => setSelected(id), []);
  const days = stats ? fillDays(stats.days ?? [], stats.window.since, stats.window.until) : [];
  const inSlo = sources ? sources.filter((s) => s.within_slo === true).length : null;

  return (
    <Page
      width="wide"
      eyebrow="Observe"
      title="Mission control"
      description="How every question moves through the Internal Brain: two permission gates, a sealed ledger, a model chain with a safe fallback, and a grounding check."
      actions={
        <>
          {compliance ? <SourceChip>ledger · last {WINDOW_DAYS} d</SourceChip> : null}
          <Button size="sm" variant="ghost" icon={RotateCw} onClick={load}>
            Refresh
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {compliance ? (
          statsErr ? (
            <Unavailable what="Organisation numbers" onRetry={load} />
          ) : (
            <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 md:grid-cols-3" data-testid="mission-kpis">
              <KpiTile
                label="Requests"
                icon={Activity}
                loading={!stats}
                value={int(stats?.totals.requests)}
                sparkline={stats && days.length > 1 ? <Sparkline values={days.map((d) => d.answered + d.refused + d.failed)} /> : undefined}
                footnote={`request.accepted · ${WINDOW_DAYS} d`}
                testId="kpi-requests"
              />
              <KpiTile
                label="Answer rate"
                icon={Gauge}
                loading={!stats}
                value={rate(stats?.totals.answer_rate)}
                footnote={stats ? `${int(stats.totals.answered)} answered · ${int(stats.totals.refused)} refused` : undefined}
                testId="kpi-answer-rate"
              />
              <KpiTile
                label="Latency p50 / p95"
                icon={Timer}
                loading={!stats}
                value={
                  <>
                    {int(stats?.latency_ms?.p50)}
                    <span className="text-heading text-muted"> / {int(stats?.latency_ms?.p95)}</span>
                  </>
                }
                unit="ms"
                footnote={`answer.released · ${int(stats?.latency_ms?.count)} timed`}
                testId="kpi-latency"
              />
              <KpiTile
                label="Leaks in eval"
                icon={FlaskConical}
                loading={!evalSum}
                value={
                  <>
                    {int(evalSum?.leaks)}
                    <span className="text-heading text-muted"> / {int(evalSum?.requests)}</span>
                  </>
                }
                footnote={evalSum ? `eval · ${evalSum.run_date ? fmtDate(evalSum.run_date) : '—'}${evalSum.stale ? ' · re-run pending' : ''}` : 'eval_summary.json'}
                testId="kpi-leaks"
              />
              <KpiTile
                label="Ledger head"
                icon={Database}
                loading={!stats}
                value={<CodeHash value={stats?.ledger.head} head={8} tail={4} label="ledger head hash" className="align-middle text-sm" />}
                footnote={stats ? `seq ${int(stats.ledger.last_seq)} · ${int(stats.ledger.events)} records` : undefined}
                testId="kpi-ledger"
              />
              <KpiTile
                label="Sources in SLO"
                icon={Waypoints}
                loading={!sources && !sourcesErr}
                value={
                  sources ? (
                    <>
                      {int(inSlo)}
                      <span className="text-heading text-muted"> / {int(sources.length)}</span>
                    </>
                  ) : (
                    '—'
                  )
                }
                footnote="/ib/sources · live"
                testId="kpi-slo"
              />
            </div>
          )
        ) : (
          <OrgNumbersLock />
        )}

        <Card aria-labelledby="pipe-h" data-testid="mission-pipeline" data-tour="mission-pipeline">
          <CardHeader actions={<SourceChip>{compliance ? 'stages_ms · p50' : 'static'}</SourceChip>}>
            <CardTitle id="pipe-h">The pipeline</CardTitle>
            <p className="mt-0.5 text-caption text-muted">
              {compliance
                ? `Each step shows its live p50 over the last ${WINDOW_DAYS} days, from sealed timings. Select a step to read what it guarantees.`
                : 'Ten steps every question takes. Select a step to read what it guarantees.'}
            </p>
          </CardHeader>
          <div className="px-2 py-3 sm:px-4">
            <PipelineDiagram selected={selected} onSelect={onSelect} timings={timings} />
          </div>
          <StepDetail id={selected} live={compliance ? timings[selected]?.value ?? null : null} />
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card aria-labelledby="fresh-h">
            <CardHeader actions={<SourceChip>/ib/sources</SourceChip>}>
              <CardTitle id="fresh-h">Source freshness</CardTitle>
              <p className="mt-0.5 text-caption text-muted">Time since each connector last synced, against its SLO. Answers re-check permissions live regardless.</p>
            </CardHeader>
            <CardBody>{sourcesErr ? <p className="text-body-sm text-muted">Source freshness is temporarily unavailable.</p> : <Freshness sources={sources} now={now} />}</CardBody>
          </Card>
          <Card aria-labelledby="model-h">
            <CardHeader actions={<SourceChip>/ib/status</SourceChip>}>
              <CardTitle id="model-h">Model status</CardTitle>
              <p className="mt-0.5 text-caption text-muted">The model chain in order of use, the safe fallback and the ledger check.</p>
            </CardHeader>
            <CardBody>
              <ModelStatus status={status} unavailable={statusErr} />
            </CardBody>
          </Card>
        </div>

        <Card aria-labelledby="recent-h">
          <CardHeader
            actions={
              <Button asChild size="xs" variant="ghost">
                <Link href="/dev/journey">All requests</Link>
              </Button>
            }
          >
            <CardTitle id="recent-h">{compliance ? 'Recent requests' : 'Your recent requests'}</CardTitle>
            <p className="mt-0.5 text-caption text-muted">{compliance ? 'Open any request to see its full journey.' : 'Open one to see its receipt: the steps, sources and sealed records.'}</p>
          </CardHeader>
          <CardBody>
            {recentErr ? (
              <p className="text-body-sm text-muted">Recent requests are temporarily unavailable.</p>
            ) : !recent ? (
              <SkeletonText lines={5} label="Loading recent requests" />
            ) : (
              <RecentRequests rows={recent} showAsker={compliance} caption={compliance ? 'Recent requests' : 'Your recent requests'} testId="mission-recent" />
            )}
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}
