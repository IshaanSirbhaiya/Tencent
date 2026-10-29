'use client';
import { Activity, Filter, Gauge, History, RotateCw, Sparkles, Timer, Waypoints } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { BarList, MiniBar, type BarRow } from '@/components/analytics/BarList';
import { ChartCard, ChartSkeleton, MiniTable, SourceChip } from '@/components/analytics/ChartCard';
import { DailyOutcomes, fillDays, fmtDay } from '@/components/analytics/DailyOutcomes';
import { EvalPanel } from '@/components/analytics/EvalPanel';
import { channelLabel, int, modelName, ms, pct, rate, reasonLabel, VERDICT_ALLOW } from '@/components/analytics/format';
import { RequestSankey } from '@/components/analytics/RequestSankey';
import { StageLatency, StageLatencyTable } from '@/components/analytics/StageLatency';
import { toLoadError, Unavailable, type LoadError } from '@/components/analytics/States';
import { Page } from '@/components/shell/Page';
import { LockCard, RequireRole } from '@/components/shell/RoleGate';
import { Button, Card, CodeHash, DataTable, KpiTile, PlatformIcon, SegmentedControl, type ColumnDef } from '@/components/ui';
import { Sparkline } from '@/components/ui/sparkline';
import { analyticsApi } from '@/lib/api';
import type { AuditStatsV1, DocumentsFlow, EvalSummary, RequestsFlow } from '@/lib/types';

type Days = '7' | '30' | '90';
type Replay = 'include' | 'exclude';

/** v1 flows, or the pre-v1 `funnel` mapped field by field when a backend has not moved yet. */
function flows(s: AuditStatsV1): { req: RequestsFlow; doc: DocumentsFlow | null } {
  const req = s.requests_flow ?? { asked: s.totals.requests, answered: s.totals.answered, refused: s.totals.refused, failed: s.totals.failed };
  const f = s.funnel;
  const doc =
    s.documents_flow ??
    (f ? { considered: f.considered_docs, gate2_checked: f.gate2_checked, gate2_allowed: f.gate2_allowed, gate2_denied: f.gate2_denied, released: f.released_docs, cited: f.cited } : null);
  return { req, doc };
}

const sorted = (o: Record<string, number> | undefined) => Object.entries(o ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

type Person = NonNullable<AuditStatsV1['people']>[number];

function Analytics() {
  const [days, setDays] = useState<Days>('30');
  const [replay, setReplay] = useState<Replay>('include');
  const [stats, setStats] = useState<AuditStatsV1 | null>(null);
  const [error, setError] = useState<LoadError | null>(null);
  const [evalSum, setEvalSum] = useState<EvalSummary | null>(null);
  const [evalErr, setEvalErr] = useState(false);

  const load = useCallback(() => {
    setError(null);
    setStats(null);
    analyticsApi
      .stats(Number(days), replay)
      .then(setStats)
      .catch((e) => setError(toLoadError(e)));
  }, [days, replay]);

  useEffect(load, [load]);
  useEffect(() => {
    analyticsApi
      .evalSummary()
      .then(setEvalSum)
      .catch(() => setEvalErr(true));
  }, []);

  const replayCount = stats?.channels?.replay ?? 0;
  const win = `${days} d${replay === 'exclude' ? ' · no replay' : ''}`;

  return (
    <Page
      width="wide"
      eyebrow="Observe · compliance"
      title="End-to-end analytics"
      description="Every number is computed on the server from the verified access ledger; this page only formats it. Requests and documents are different units and never share a chart."
    >
      <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Analytics window and filters">
          <SegmentedControl<Days>
            label="Time window"
            value={days}
            onChange={setDays}
            options={[
              { value: '7', label: '7 days' },
              { value: '30', label: '30 days' },
              { value: '90', label: '90 days' },
            ]}
            testId="window-control"
          />
          <SegmentedControl<Replay>
            label="Seeded demo history"
            value={replay}
            onChange={setReplay}
            options={[
              { value: 'include', label: 'With replay' },
              { value: 'exclude', label: 'Live only' },
            ]}
            testId="replay-control"
          />
          <Button size="sm" variant="ghost" icon={RotateCw} onClick={load} aria-label="Refresh analytics">
            <span className="hidden sm:inline">Refresh</span>
          </Button>
      </div>
      {error ? (
        <Unavailable what="Analytics" onRetry={load} />
      ) : !stats ? (
        <div className="space-y-4" role="status" aria-label="Loading analytics">
          <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 md:grid-cols-3">
            {Array.from({ length: 6 }, (_, i) => (
              <KpiTile key={i} label="Loading" value="" loading />
            ))}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <ChartSkeleton />
            <ChartSkeleton />
          </div>
        </div>
      ) : (
        <Body stats={stats} win={win} replayCount={replayCount} replay={replay} evalSum={evalSum} evalErr={evalErr} />
      )}
    </Page>
  );
}

function Body({ stats, win, replayCount, replay, evalSum, evalErr }: { stats: AuditStatsV1; win: string; replayCount: number; replay: Replay; evalSum: EvalSummary | null; evalErr: boolean }) {
  const { req, doc } = flows(stats);
  const t = stats.totals;
  const daysData = fillDays(stats.days ?? [], stats.window.since, stats.window.until);
  const models = sorted(stats.models);
  const channels = sorted(stats.channels);
  const verdicts = sorted(stats.gate2?.verdicts);
  const reasons = sorted(stats.gate2?.reasons);
  const platforms = (['confluence', 'jira', 'slack', 'drive', 'other'] as const).filter((p) => stats.platforms?.[p]).map((p) => ({ p, ...stats.platforms![p]! }));
  const people = stats.people ?? [];
  const maxOf = (k: 'considered' | 'gate2_checked' | 'released') => Math.max(1, ...platforms.map((x) => x[k] ?? 0));
  const maxAsked = Math.max(1, ...people.map((p) => p.asked ?? 0));

  const docRows: BarRow[] = doc
    ? [
        { key: 'considered', name: 'Considered (Gate 1)', label: 'Considered (Gate 1 candidates)', value: doc.considered },
        { key: 'checked', name: 'Gate-2 checked', label: 'Gate-2 checked live', value: doc.gate2_checked, note: `${pct(doc.gate2_checked, doc.considered)} of considered` },
        { key: 'allowed', name: 'Allowed', label: 'Allowed by Gate 2', value: doc.gate2_allowed, note: `${pct(doc.gate2_allowed, doc.gate2_checked)} of checked`, indent: true },
        { key: 'denied', name: 'Denied', label: 'Denied by Gate 2', value: doc.gate2_denied, note: `${pct(doc.gate2_denied, doc.gate2_checked)} of checked`, indent: true, fill: 'bg-subtle' },
        { key: 'released', name: 'Released to a model', label: 'Released to a model', value: doc.released, note: `${pct(doc.released, doc.gate2_allowed)} of allowed` },
        { key: 'cited', name: 'Cited in an answer', label: 'Cited in an answer', value: doc.cited, note: `${pct(doc.cited, doc.released)} of released` },
      ]
    : [];

  const platformCols: ColumnDef<(typeof platforms)[number]>[] = [
    { id: 'p', header: 'Platform', accessorFn: (r) => r.p, cell: ({ row }) => (row.original.p === 'other' ? 'Other' : <PlatformIcon platform={row.original.p} tone="platform" />) },
    ...(['considered', 'gate2_checked', 'released'] as const).map(
      (k): ColumnDef<(typeof platforms)[number]> => ({
        id: k,
        header: k === 'gate2_checked' ? 'Gate-2 checked' : k === 'considered' ? 'Considered' : 'Released',
        accessorFn: (r) => r[k] ?? 0,
        cell: ({ row }) => (
          <span className="inline-flex items-center justify-end gap-2">
            <MiniBar value={row.original[k]} max={maxOf(k)} fill={row.original.p === 'other' ? 'bg-subtle' : `rgb(var(--pf-${row.original.p}))`} />
            <span className="w-12">{int(row.original[k])}</span>
          </span>
        ),
        meta: { numeric: true },
      }),
    ),
  ];

  const peopleCols: ColumnDef<Person>[] = [
    { id: 'sub', header: 'Asker', accessorFn: (r) => r.sub, cell: ({ row }) => <span className="font-mono text-caption">{row.original.sub}</span> },
    {
      id: 'asked',
      header: 'Asked',
      accessorFn: (r) => r.asked ?? 0,
      cell: ({ row }) => (
        <span className="inline-flex items-center justify-end gap-2">
          <MiniBar value={row.original.asked} max={maxAsked} className="hidden w-10 sm:inline-block" />
          <span className="w-8">{int(row.original.asked ?? 0)}</span>
        </span>
      ),
      meta: { numeric: true },
    },
    { id: 'answered', header: 'Answered', accessorFn: (r) => r.answered ?? 0, cell: ({ getValue }) => int(getValue() as number), meta: { numeric: true } },
    { id: 'refused', header: 'Refused', accessorFn: (r) => r.refused ?? 0, cell: ({ getValue }) => int(getValue() as number), meta: { numeric: true } },
  ];

  const governance: [string, number | undefined, string][] = [
    ['Access changes', t.access_changes, 'acl / membership / directory'],
    ['Reveals', t.reveals, 'answer text opened with a reason'],
    ['Audit queries', t.audit_queries, 'every audit read is ledgered'],
    ['Source syncs', t.syncs, 'ingest.sync'],
    ['Sign-ins', t.logins, 'auth.login'],
    ['Receipts viewed', t.trace_views, 'trace.viewed'],
    ['Graph views', t.graph_views, 'graph.viewed'],
    ['Document views', t.doc_views, 'doc.viewed'],
  ];

  return (
    <div className="space-y-4" data-testid="analytics-body">
      {replay === 'include' && replayCount > 0 ? (
        <p className="flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2 text-caption text-muted" data-testid="replay-notice">
          <History aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Includes seeded demo history: <span className="tabular-nums text-fg">{int(replayCount)}</span> requests on the <span className="font-mono">replay</span> channel were asked through the real
            pipeline by the seeding script and are labelled as replay in the ledger. Choose <span className="font-medium text-fg">Live only</span> to leave them out.
          </span>
        </p>
      ) : replay === 'exclude' ? (
        <p className="flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 text-caption text-muted" data-testid="replay-notice">
          <History aria-hidden className="h-3.5 w-3.5" /> Seeded demo history is excluded: live traffic only.
        </p>
      ) : null}

      {/* KPI row */}
      <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 md:grid-cols-3" data-testid="analytics-kpis">
        <KpiTile
          label="Requests"
          icon={Activity}
          value={int(req.asked)}
          sparkline={daysData.length > 1 ? <Sparkline values={daysData.map((d) => d.answered + d.refused + d.failed)} /> : undefined}
          footnote={`request.accepted · ${win}`}
        />
        <KpiTile label="Answer rate" icon={Gauge} value={rate(t.answer_rate)} footnote={`${int(req.answered)} answered · ${int(req.refused)} refused`} />
        <KpiTile
          label="Latency p50 / p95"
          icon={Timer}
          value={
            <>
              {int(stats.latency_ms?.p50)}
              <span className="text-heading text-muted"> / {int(stats.latency_ms?.p95)}</span>
            </>
          }
          unit="ms"
          footnote={`max ${ms(stats.latency_ms?.max)} · ${int(stats.latency_ms?.count)} timed`}
        />
        <KpiTile
          label="Gate-2 deny rate"
          icon={Filter}
          value={doc ? pct(doc.gate2_denied, doc.gate2_checked) : '—'}
          footnote={doc ? `${int(doc.gate2_denied)} of ${int(doc.gate2_checked)} documents` : 'documents_flow not reported'}
        />
        <KpiTile label="Grounding drops" icon={Waypoints} value={int(t.dropped_sentences)} unit="sentences" footnote={`across ${int(req.answered)} answers`} />
        <KpiTile label="Fallback used" icon={Sparkles} value={int(t.fallback_used)} unit="answers" footnote="DeepSeek after Hy3 failed" />
      </div>

      {/* Requests */}
      <div className="grid gap-4 lg:grid-cols-5">
        <ChartCard
          className="lg:col-span-3"
          title="Requests per day"
          takeaway="Answered, refused (no permitted evidence) and failed, by day of the event (UTC)."
          source={`ledger · ${win}`}
          testId="chart-daily"
          table={
            <MiniTable
              caption="Requests per day by outcome"
              head={['Day', 'Answered', 'Refused', 'Failed']}
              rows={daysData.filter((d) => d.answered + d.refused + d.failed > 0).map((d) => [fmtDay(d.day), int(d.answered), int(d.refused), int(d.failed)])}
            />
          }
        >
          <DailyOutcomes data={daysData} />
        </ChartCard>
        <ChartCard
          className="lg:col-span-2"
          title="Request flow"
          takeaway="Where each question ended. A refusal is the safety system working, not an error."
          source="requests_flow"
          testId="chart-sankey"
          table={
            <MiniTable
              caption="Request flow from asked to outcome"
              head={['Outcome', 'Requests', 'Share of asked']}
              rows={[
                ['Asked', int(req.asked), '100%'],
                ['Answered', int(req.answered), pct(req.answered, req.asked)],
                ['Refused', int(req.refused), pct(req.refused, req.asked)],
                ['Failed', int(req.failed), pct(req.failed, req.asked)],
              ]}
            />
          }
        >
          <RequestSankey asked={req.asked} answered={req.answered} refused={req.refused} failed={req.failed} />
        </ChartCard>
      </div>

      {/* Documents */}
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Document funnel"
          takeaway="Documents, not requests: what Gate 1 surfaced, what the live check allowed, and what the answer used."
          source="documents_flow"
          testId="chart-doc-funnel"
          table={<MiniTable caption="Document funnel" head={['Step', 'Documents', 'Share']} rows={docRows.map((r) => [r.name, int(r.value), r.note ?? '—'])} />}
        >
          {doc ? <BarList rows={docRows} testId="doc-funnel" /> : <p className="text-body-sm text-muted">The document flow is not reported by this backend.</p>}
        </ChartCard>
        <ChartCard
          title="Gate-2 verdicts by reason"
          takeaway="Every live check, by verdict and by the source's reason code. Compliance only."
          source="gate2.reasons"
          testId="chart-reasons"
          table={
            <MiniTable
              caption="Gate-2 verdicts and reason codes"
              head={['Verdict or reason', 'Kind', 'Checks']}
              numeric={[2]}
              rows={[...verdicts.map(([k, v]) => [k, 'verdict', int(v)]), ...reasons.map(([k, v]) => [`${k || '(none)'}: ${reasonLabel(k)}`, 'reason', int(v)])]}
            />
          }
        >
          <div className="grid gap-4 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <div>
              <h3 className="mb-1.5 text-micro font-semibold uppercase tracking-wide text-subtle">Verdict</h3>
              <BarList
                rows={verdicts.map(([k, v]) => ({ key: k, name: k, label: <span className="font-mono text-caption">{k}</span>, value: v, fill: VERDICT_ALLOW.has(k) ? 'bg-accent' : 'bg-subtle' }))}
                valueWidth="w-12"
              />
            </div>
            <div>
              <h3 className="mb-1.5 text-micro font-semibold uppercase tracking-wide text-subtle">Reason code</h3>
              <BarList
                rows={reasons.map(([k, v]) => ({
                  key: k || 'none',
                  name: `${k}: ${reasonLabel(k)}`,
                  label: (
                    <span className="truncate">
                      <span className="font-mono text-caption">{k || '(none)'}</span> <span className="text-subtle">{reasonLabel(k)}</span>
                    </span>
                  ),
                  value: v,
                  fill: k === 'ok' || k === 'updated_since_indexed' ? 'bg-accent' : 'bg-subtle',
                }))}
                valueWidth="w-12"
              />
            </div>
          </div>
        </ChartCard>
      </div>

      {/* Latency */}
      <ChartCard
        title="Latency"
        takeaway="End to end and per stage, p50 and p95, from the timings sealed in answer.released."
        source="latency_ms · stages_ms"
        testId="chart-latency"
        table={stats.stages_ms ? <StageLatencyTable stages={stats.stages_ms} /> : undefined}
      >
        <div className="grid gap-5 lg:grid-cols-[14rem_minmax(0,1fr)]">
          <div className="self-start">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
            {(
              [
                ['p50', stats.latency_ms?.p50],
                ['p95', stats.latency_ms?.p95],
                ['max', stats.latency_ms?.max],
                ['mean', stats.latency_ms?.mean],
              ] as const
            ).map(([k, v]) => (
              <div key={k}>
                <dt className="text-micro font-semibold uppercase tracking-wide text-subtle">{k}</dt>
                <dd className="text-heading tabular-nums text-fg">
                  {int(v)} <span className="text-caption text-muted">ms</span>
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-caption text-subtle">{int(stats.latency_ms?.count)} requests timed end to end.</p>
          </div>
          {stats.stages_ms ? <StageLatency stages={stats.stages_ms} /> : <p className="text-body-sm text-muted">Per-stage timings are not reported.</p>}
        </div>
      </ChartCard>

      {/* Models and channels */}
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Model mix"
          takeaway="Which path wrote each released answer or refusal. Extractive means no model was used."
          source="models"
          testId="chart-models"
          table={<MiniTable caption="Answers by model" head={['Model', 'provider/model', 'Answers']} numeric={[2]} rows={models.map(([k, v]) => [modelName(k).label, k, int(v)])} />}
        >
          <BarList
            rows={models.map(([k, v]) => {
              const m = modelName(k);
              return {
                key: k,
                name: `${m.label} (${k})`,
                label: (
                  <span className="truncate">
                    {m.label} <span className="font-mono text-micro text-subtle">{k}</span>
                  </span>
                ),
                value: v,
                fill: m.kind === 'primary' ? 'bg-accent' : m.kind === 'fallback' ? 'bg-accent/60' : 'bg-subtle',
              };
            })}
          />
          <p className="mt-3 text-caption text-muted">
            Fallback to DeepSeek: <span className="tabular-nums text-fg">{int(t.fallback_used)}</span> answers. Refusals are written by the deterministic path and count under extractive.
          </p>
        </ChartCard>
        <ChartCard
          title="Channels"
          takeaway="Where questions came from: the web UI, WorkBuddy over MCP, or the labelled seeded replay."
          source="channels"
          testId="chart-channels"
          table={<MiniTable caption="Requests by channel" head={['Channel', 'Requests', 'Share']} rows={channels.map(([k, v]) => [channelLabel(k), int(v), pct(v, req.asked)])} />}
        >
          <BarList
            rows={channels.map(([k, v]) => ({
              key: k,
              name: channelLabel(k),
              label: (
                <span className="truncate">
                  {channelLabel(k)} <span className="font-mono text-micro text-subtle">{k}</span>
                </span>
              ),
              value: v,
              note: pct(v, req.asked),
              fill: k === 'replay' ? 'bg-subtle' : 'bg-accent',
            }))}
          />
        </ChartCard>
      </div>

      {/* Platforms and people */}
      <div className="grid gap-4 lg:grid-cols-5">
        <Card aria-labelledby="pf-h" className="min-w-0 lg:col-span-3" data-testid="table-platforms">
          <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
            <div>
              <h2 id="pf-h" className="text-heading-sm text-fg">
                Per-platform coverage
              </h2>
              <p className="mt-0.5 text-caption text-muted">Documents per source system at each step.</p>
            </div>
            <SourceChip>platforms</SourceChip>
          </div>
          <div className="p-3">
            <DataTable
              columns={platformCols}
              data={platforms}
              caption="Documents per platform: considered, Gate-2 checked and released"
              getRowId={(r) => r.p}
              density="compact"
              className="border-0"
              empty="No documents in this window."
            />
          </div>
        </Card>
        <Card aria-labelledby="people-h" className="min-w-0 lg:col-span-2" data-testid="table-people">
          <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
            <div>
              <h2 id="people-h" className="text-heading-sm text-fg">
                Top askers
              </h2>
              <p className="mt-0.5 text-caption text-muted">Requests per person, most active first. Counts only; no question text.</p>
            </div>
            <SourceChip>people</SourceChip>
          </div>
          <div className="p-3">
            <DataTable
              columns={peopleCols}
              data={people}
              caption="Requests per person"
              getRowId={(r) => r.sub}
              density="compact"
              maxHeightClass="max-h-72"
              className="border-0"
              empty="Nobody asked anything in this window."
            />
          </div>
        </Card>
      </div>

      {/* Governance */}
      <Card aria-labelledby="gov-h" data-testid="governance">
        <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
          <div>
            <h2 id="gov-h" className="text-heading-sm text-fg">
              Governance activity
            </h2>
            <p className="mt-0.5 text-caption text-muted">Access changes, reveals and audit reads in the same window. Each one is itself a sealed ledger record.</p>
          </div>
          <SourceChip>totals · {win}</SourceChip>
        </div>
        <dl className="grid grid-cols-2 sm:grid-cols-4">
          {governance.map(([label, v, hint]) => (
            <div key={label} className="border-b border-r border-line px-4 py-3 [&:nth-child(2n)]:border-r-0 sm:[&:nth-child(2n)]:border-r sm:[&:nth-child(4n)]:border-r-0 [&:nth-last-child(-n+2)]:border-b-0 sm:[&:nth-last-child(-n+4)]:border-b-0">
              <dt className="text-caption text-muted">{label}</dt>
              <dd className="mt-0.5 text-heading tabular-nums text-fg">{int(v ?? null)}</dd>
              <dd className="font-mono text-micro text-subtle">{hint}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <EvalPanel data={evalSum} error={evalErr} />

      {/* Ledger */}
      <Card className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-caption text-muted" data-testid="ledger-strip">
        <span>
          Ledger <span className="tabular-nums text-fg">{int(stats.ledger.events)}</span> records
        </span>
        <span>
          seq <span className="tabular-nums text-fg">{int(stats.ledger.first_seq)}</span>–<span className="tabular-nums text-fg">{int(stats.ledger.last_seq)}</span>
        </span>
        <span className="inline-flex items-center gap-2">
          head <CodeHash value={stats.ledger.head} head={10} tail={6} label="ledger head hash" />
        </span>
        <span className="ml-auto text-subtle">
          Window {new Date(stats.window.since).toISOString().slice(0, 16).replace('T', ' ')} → {new Date(stats.window.until).toISOString().slice(0, 16).replace('T', ' ')} UTC · this read is itself
          ledgered
        </span>
      </Card>
    </div>
  );
}

export default function AnalyticsPage() {
  return (
    <RequireRole
      role="compliance_officer"
      fallback={
        <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
          <LockCard role="compliance_officer" title="Analytics needs the compliance role" body="Organisation numbers are visible to the compliance role. This is least privilege working." />
        </div>
      }
    >
      <Analytics />
    </RequireRole>
  );
}
