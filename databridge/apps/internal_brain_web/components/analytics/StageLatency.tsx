'use client';
import type { PctMs, StageV1 } from '@/lib/types';
import { STAGES_V1 } from '@/lib/types';
import { MiniTable } from './ChartCard';
import { STAGE_HINT, STAGE_LABEL, int, ms } from './format';

/**
 * Per-stage latency in pipeline order: a p50 bar with a p95 whisker on one linear scale (the
 * largest p95). Small stages stay small: that is the truth, and every value is printed.
 */
export function StageLatency({ stages }: { stages: Partial<Record<StageV1, PctMs>> }) {
  const present = STAGES_V1.filter((s) => stages[s]);
  const top = Math.max(1, ...present.map((s) => stages[s]?.p95 ?? stages[s]?.p50 ?? 0));
  return (
    <div>
      <ul className="space-y-2.5" data-testid="stage-latency">
        {present.map((s) => {
          const v = stages[s]!;
          const p50 = v.p50 ?? 0;
          const p95 = v.p95 ?? p50;
          return (
            <li key={s} className="grid grid-cols-[5.5rem_minmax(0,1fr)_auto] items-center gap-x-3" title={`${STAGE_LABEL[s]}: p50 ${ms(v.p50)}, p95 ${ms(v.p95)} over ${int(v.count)} requests`}>
              <div className="min-w-0">
                <p className="text-body-sm font-medium text-fg">{STAGE_LABEL[s]}</p>
              </div>
              <div aria-hidden className="relative h-4">
                <div className="absolute inset-y-[7px] left-0 right-0 rounded-full bg-line" />
                <div className="absolute inset-y-[7px] left-0 rounded-full bg-accent/35" style={{ width: `${(p95 / top) * 100}%` }} />
                <div className="absolute inset-y-[2px] left-0 rounded-sm bg-accent" style={{ width: `max(3px, ${(p50 / top) * 100}%)` }} />
                <div className="absolute inset-y-0 w-px bg-accent" style={{ left: `calc(${(p95 / top) * 100}% - 1px)` }} />
              </div>
              <p className="w-32 text-right text-body-sm tabular-nums text-fg">
                {ms(v.p50)} <span className="text-subtle">/ {int(v.p95)}</span>
              </p>
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2.5 w-4 rounded-sm bg-accent" /> p50
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-1 w-4 rounded-full bg-accent/35" />
          <span aria-hidden className="-ml-1.5 h-2.5 w-px bg-accent" /> p95
        </span>
        <span className="text-subtle">Stages run in sequence; compose includes the grounding check.</span>
      </div>
    </div>
  );
}

export function StageLatencyTable({ stages }: { stages: Partial<Record<StageV1, PctMs>> }) {
  return (
    <MiniTable
      caption="Latency per stage, p50 and p95 in milliseconds"
      head={['Stage', 'What it does', 'p50 (ms)', 'p95 (ms)', 'Requests']}
      numeric={[2, 3, 4]}
      rows={STAGES_V1.filter((s) => stages[s]).map((s) => [STAGE_LABEL[s], STAGE_HINT[s], int(stages[s]!.p50), int(stages[s]!.p95), int(stages[s]!.count)])}
    />
  );
}
