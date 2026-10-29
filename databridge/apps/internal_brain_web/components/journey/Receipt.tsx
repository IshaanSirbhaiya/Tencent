'use client';
import { Check, Minus, ReceiptText } from 'lucide-react';
import { channelLabel, int, modelInfoName } from '@/components/analytics/format';
import { Card, CodeHash, Pill, Stat, StatGrid } from '@/components/ui';
import { cn } from '@/lib/cn';
import { fmtDate, fmtTime, shortVersion } from '@/lib/format';
import type { TraceReceipt } from '@/lib/types';
import { DocLine, ReplayBadge, RequestStatusBadge, Section } from './bits';
import { LedgerChain } from './LedgerChain';

const STEP_LABEL: Record<string, string> = {
  accept: 'Accepted',
  gate1: 'Gate 1',
  gate2: 'Gate 2',
  seal: 'Sealed',
  compose: 'Composed',
  release: 'Released',
};

/** Coarse stages as a stepper. A step is ✓ when its sealed record exists (never from timings). */
function Stepper({ stages }: { stages: TraceReceipt['stages'] }) {
  return (
    <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="receipt-stepper">
      {stages.map((s, i) => {
        const ok = s.status === 'ok';
        return (
          <li key={s.name} className={cn('flex items-center gap-2 rounded-md border px-2.5 py-2', ok ? 'border-line-strong bg-surface' : 'border-dashed border-line bg-bg/30')}>
            <span
              aria-hidden
              className={cn('inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]', ok ? 'border-ok/50 bg-ok/10 text-ok' : 'border-line text-subtle')}
            >
              {ok ? <Check className="h-3 w-3" /> : <Minus className="h-3 w-3" />}
            </span>
            <span className="min-w-0">
              <span className="block text-micro tabular-nums text-subtle">Step {i + 1}</span>
              <span className="block truncate text-body-sm font-medium text-fg">{STEP_LABEL[s.name] ?? s.name}</span>
              <span className="sr-only">{ok ? ', done' : ', skipped'}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The asker's own receipt (GET /ib/trace): what they were shown and the proof it was sealed. No
 * per-stage timings, no global seq, and nothing at all about other documents or denials.
 */
export function Receipt({ r }: { r: TraceReceipt }) {
  return (
    <div className="space-y-4" data-testid="journey-receipt" data-tour="journey-receipt">
      <Card className="px-4 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <RequestStatusBadge status={r.status} />
          <Pill tone="accent" icon={ReceiptText}>
            Your receipt
          </Pill>
          <ReplayBadge show={r.synthetic_replay} />
        </div>
        <h2 className="mt-3 text-heading text-fg">
          <span className="sr-only">Question: </span>
          {r.question ? <>“{r.question}”</> : <span className="text-muted">Question not recorded</span>}
        </h2>
        <StatGrid cols={4} className="mt-4">
          <Stat label="Request" value={<CodeHash value={r.req_id} head={10} tail={4} label="request id" />} />
          <Stat label="When" value={r.event_time ? `${fmtDate(r.event_time)} · ${fmtTime(r.event_time)}` : '—'} />
          <Stat label="Channel" value={channelLabel(r.channel)} />
          <Stat label="Total time" value={typeof r.latency_ms_rounded === 'number' ? `≈ ${int(r.latency_ms_rounded)} ms` : '—'} />
          <Stat label="Model" value={modelInfoName(r.model)} />
          <Stat label="Fallback used" value={r.model ? (r.model.fallback_used ? 'Yes' : 'No') : '—'} />
          <Stat label="Prompt" value={<span className="font-mono text-caption">{r.prompt_version ?? '—'}</span>} />
          <Stat label="Policy" value={<span className="font-mono text-caption">{r.policy_version ?? '—'}</span>} />
        </StatGrid>
      </Card>

      <Section title="How it was answered" description="Each step is ticked when its record exists in the sealed ledger.">
        <Stepper stages={r.stages ?? []} />
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Sources cited" description="Each source is re-checked with its system when you open this receipt.">
          {r.citations?.length ? (
            <ul className="space-y-2.5" data-testid="receipt-citations">
              {r.citations.map((c, i) => (
                <li key={c.doc_id} className="flex items-start gap-2">
                  <span className="mt-0.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border border-line-strong bg-raised px-1 text-micro tabular-nums text-muted">{i + 1}</span>
                  <DocLine docId={c.doc_id} title={c.title} platform={c.platform} container={c.container_name ?? c.container} href={c.deep_link} />
                  {c.cited_version && shortVersion(c.cited_version).text ? (
                    <span className="ml-auto shrink-0 text-micro tabular-nums text-subtle" title={shortVersion(c.cited_version).full}>
                      {shortVersion(c.cited_version).text}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-body-sm text-muted">No sources to show.</p>
          )}
        </Section>
        <Section title="Grounding" description="Sentences without a cited source are removed before you see the answer.">
          <p className="text-kpi tabular-nums text-fg">{int(r.dropped_sentences_count)}</p>
          <p className="text-caption text-muted">
            {r.dropped_sentences_count === null || r.dropped_sentences_count === undefined ? 'Not reported.' : 'sentences removed by the grounding check.'}
          </p>
          <div className="mt-2 flex items-center gap-2 text-caption text-muted">
            Answer hash <CodeHash value={r.answer_sha256} head={8} tail={4} label="answer hash" />
          </div>
        </Section>
      </div>

      <Section title="Sealed records" description="Your request's records in the hash-chained ledger, in order.">
        <LedgerChain items={(r.records ?? []).map((x) => ({ label: `Record ${x.ordinal}`, kind: x.kind, hash: x.hash }))} testId="receipt-chain" />
      </Section>
    </div>
  );
}
