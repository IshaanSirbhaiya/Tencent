'use client';
import { CircleCheck, CircleSlash, Eye, Minus, ShieldCheck, ShieldX } from 'lucide-react';
import Link from 'next/link';
import { ChartCard } from '@/components/analytics/ChartCard';
import { channelLabel, int, modelInfoName, ms, reasonLabel, VERDICT_ALLOW } from '@/components/analytics/format';
import { Badge, Card, CodeHash, DataTable, Pill, Stat, StatGrid, type ColumnDef } from '@/components/ui';
import { fmtDate, fmtTime } from '@/lib/format';
import type { ConsideredDoc, JourneyV1 } from '@/lib/types';
import { DocLine, ReplayBadge, RequestStatusBadge, Section } from './bits';
import { LedgerChain } from './LedgerChain';
import { Waterfall, WaterfallTable } from './Waterfall';

function Yes({ on, label }: { on: boolean | undefined; label: string }) {
  return on ? (
    <span className="inline-flex items-center gap-1 text-ok">
      <CircleCheck aria-hidden className="h-3.5 w-3.5" />
      <span className="sr-only">{label}</span>
      <span aria-hidden className="text-caption">yes</span>
    </span>
  ) : (
    <span className="text-subtle">
      <Minus aria-hidden className="inline h-3.5 w-3.5" />
      <span className="sr-only">not {label}</span>
    </span>
  );
}

const COLUMNS: ColumnDef<ConsideredDoc>[] = [
  {
    id: 'doc',
    header: 'Considered document',
    accessorFn: (d) => d.title ?? d.doc_id,
    cell: ({ row }) => <DocLine docId={row.original.doc_id} title={row.original.title} platform={row.original.platform} container={row.original.container_name ?? row.original.container} />,
    meta: { className: 'min-w-[16rem]' },
  },
  {
    id: 'verdict',
    header: 'Gate 2',
    accessorFn: (d) => d.verdict ?? '',
    cell: ({ row }) => {
      const v = row.original.verdict;
      if (!v) return <span className="text-caption text-subtle">not checked</span>;
      const allow = VERDICT_ALLOW.has(v);
      return (
        <Badge tone={allow ? 'ok' : 'bad'} icon={allow ? ShieldCheck : ShieldX}>
          {allow ? 'Allowed' : 'Denied'} · {v}
        </Badge>
      );
    },
  },
  {
    id: 'reason',
    header: 'Reason code',
    accessorFn: (d) => d.reason_code ?? '',
    cell: ({ row }) =>
      row.original.reason_code ? (
        <span className="flex flex-col">
          <span className="font-mono text-caption text-fg">{row.original.reason_code}</span>
          <span className="text-micro text-subtle">{reasonLabel(row.original.reason_code)}</span>
        </span>
      ) : (
        <span className="text-subtle">—</span>
      ),
  },
  { id: 'released', header: 'Released', accessorFn: (d) => (d.released ? 1 : 0), cell: ({ row }) => <Yes on={row.original.released} label="released" /> },
  { id: 'cited', header: 'Cited', accessorFn: (d) => (d.cited ? 1 : 0), cell: ({ row }) => <Yes on={row.original.cited} label="cited" /> },
];

/** The compliance lens on one request (GET /audit/journey): everything the ledger sealed. */
export function ComplianceJourney({ j }: { j: JourneyV1 }) {
  const stageMs = j.stage_ms ?? {};
  const hasStages = Object.values(stageMs).some((v) => typeof v === 'number');
  const considered = j.considered ?? [];
  const citedIds = new Set((j.citations ?? []).map((c) => c.doc_id));
  return (
    <div className="space-y-4" data-testid="journey-compliance">
      <Card className="px-4 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <RequestStatusBadge status={j.status} />
          <Pill tone="accent" icon={Eye}>
            Compliance journey
          </Pill>
          <ReplayBadge show={j.synthetic_replay} />
        </div>
        <h2 className="mt-3 text-heading text-fg">
          <span className="sr-only">Question: </span>
          {j.question ? <>“{j.question}”</> :<span className="text-muted">Question not recorded</span>}
        </h2>
        <StatGrid cols={4} className="mt-4">
          <Stat label="Request" value={<CodeHash value={j.req_id} head={10} tail={4} label="request id" />} />
          <Stat label="Asked by" value={<span className="font-mono text-caption">{j.sub ?? '—'}</span>} />
          <Stat label="Channel" value={channelLabel(j.channel)} />
          <Stat label="When" value={j.event_time ? `${fmtDate(j.event_time)} · ${fmtTime(j.event_time)}` : '—'} />
          <Stat label="Latency" value={ms(j.latency_ms)} />
          <Stat label="Policy" value={<span className="font-mono text-caption">{j.policy_version ?? '—'}</span>} />
          <Stat label="Directory epoch" value={int(j.directory_epoch)} />
          <Stat label="Gate-1 policy" value={<span className="font-mono text-caption">{j.gate1_policy_version ?? '—'}</span>} />
        </StatGrid>
      </Card>

      {hasStages ? (
        <ChartCard
          title="Waterfall"
          takeaway="Each bar is a sealed stage timing; the three ledger seals sit on the same axis."
          source="answer.released · stage_ms"
          table={<WaterfallTable stageMs={stageMs} records={j.events ?? []} />}
          testId="journey-waterfall"
        >
          <Waterfall stageMs={stageMs} records={j.events ?? []} />
        </ChartCard>
      ) : (
        <Section title="Waterfall" description="No stage timings were sealed for this request.">
          <p className="text-body-sm text-muted">
            {j.failure
              ? `The request failed closed at ${j.failure.stage ?? 'an unknown stage'} (${j.failure.error_code ?? 'no code'}). Nothing was shown to the asker.`
              : j.status === 'in_progress'
                ? 'The request has no answer.released record yet.'
                : 'Timings are not reported for this request.'}
          </p>
        </Section>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="Model" description="The path that produced the answer.">
          <p className="text-heading tabular-nums text-fg" data-testid="journey-model">
            {modelInfoName(j.model)}
          </p>
          <dl className="mt-2 space-y-1.5 text-body-sm">
            {(
              [
                ['Provider', j.model?.provider],
                ['Model id', j.model?.model],
                ['Fallback used', j.model ? (j.model.fallback_used ? 'Yes' : 'No') : null],
                ['Prompt', j.prompt_version],
              ] as const
            ).map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-3 border-b border-line pb-1.5 last:border-b-0">
                <dt className="shrink-0 text-caption text-muted">{k}</dt>
                <dd className="min-w-0 break-all text-right font-mono text-caption text-fg">{v ?? '—'}</dd>
              </div>
            ))}
          </dl>
        </Section>
        <Section title="Grounding" description="Sentences without a cited, permitted passage are dropped.">
          <p className="text-kpi tabular-nums text-fg" data-testid="journey-dropped">
            {int(j.dropped_sentences_count)}
          </p>
          <p className="text-caption text-muted">
            {j.dropped_sentences_count === null || j.dropped_sentences_count === undefined
              ? 'Not reported (no answer was released).'
              : j.dropped_sentences_count === 0
                ? 'sentences dropped: every sentence was supported.'
                : 'sentences dropped before the answer was shown.'}
          </p>
          <div className="mt-2 flex items-center gap-2 text-caption text-muted">
            Answer hash <CodeHash value={j.answer_sha256} head={8} tail={4} label="answer hash" />
          </div>
        </Section>
        <Section title="Reveal status" description="Answer text is never in the journey; a reveal needs a reason and is ledgered.">
          {j.reveals?.length ? (
            <ul className="space-y-2">
              {j.reveals.map((r) => (
                <li key={r.seq} className="rounded-md border border-line px-2.5 py-1.5 text-caption">
                  <p className="text-fg">
                    Revealed by <span className="font-mono">{r.by ?? '—'}</span> <span className="font-mono text-subtle">#{r.seq}</span>
                  </p>
                  <p className="text-muted">{r.reason ?? 'No reason recorded'}</p>
                  <p className="tabular-nums text-subtle">{r.event_time ? `${fmtDate(r.event_time)} · ${fmtTime(r.event_time)}` : ''}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="flex items-center gap-2 text-body-sm text-muted" data-testid="journey-reveals-none">
              <CircleSlash aria-hidden className="h-4 w-4" /> Never revealed.
            </p>
          )}
          <Link href="/dev/audit" className="mt-2 inline-block text-caption text-accent underline-offset-2 hover:underline">
            Reveal from the audit console
          </Link>
        </Section>
      </div>

      <Section
        title="Considered → Gate 2"
        description="Every document Gate 1 surfaced, with the live Gate-2 verdict and its reason code. Visible to the compliance role only."
        testId="journey-verdicts"
      >
        <DataTable
          columns={COLUMNS}
          data={considered}
          caption="Documents considered for this request, with the Gate-2 verdict and reason code"
          getRowId={(d) => d.doc_id}
          density="compact"
          empty="No documents were considered."
          className="border-0"
        />
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Released and cited" description="Released = sent to the model; cited = used in the answer.">
          {j.released?.length ? (
            <ul className="space-y-2.5">
              {j.released.map((d) => (
                <li key={d.doc_id} className="flex items-start justify-between gap-3">
                  <DocLine docId={d.doc_id} title={d.title} container={d.container} />
                  {citedIds.has(d.doc_id) ? (
                    <Badge tone="accent">cited</Badge>
                  ) : (
                    <Badge tone="neutral">released</Badge>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-body-sm text-muted">Nothing was released to a model.</p>
          )}
        </Section>
        <Section title="Sealed records" description="This request's records in the hash-chained access ledger.">
          <LedgerChain items={(j.events ?? []).map((e) => ({ label: `#${e.seq}`, kind: e.kind, hash: e.hash, at: e.recorded_at ?? e.event_time }))} />
        </Section>
      </div>
    </div>
  );
}
