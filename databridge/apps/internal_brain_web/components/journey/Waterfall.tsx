'use client';
import { Database } from 'lucide-react';
import { STAGE_HINT, STAGE_LABEL, int, ms, shortHash } from '@/components/analytics/format';
import { MiniTable } from '@/components/analytics/ChartCard';
import { cn } from '@/lib/cn';
import type { JourneyRecord, StageV1 } from '@/lib/types';
import { STAGES_V1 } from '@/lib/types';

/** Which sealed record closes which stage (write-ahead: the record exists before the next stage). */
const SEAL_AFTER: Partial<Record<StageV1, string>> = {
  accept: 'request.accepted',
  seal: 'retrieval.decided',
  release: 'answer.released',
};

const STAGE_FILL: Record<StageV1, string> = {
  accept: 'bg-muted',
  gate1: 'bg-accent/70',
  gate2: 'bg-accent',
  seal: 'bg-muted',
  compose: 'bg-accent/55',
  release: 'bg-muted',
};

/** Round axis ticks (1, 2 or 5 × 10^n) inside [0, total]. */
function axisTicks(total: number): number[] {
  const raw = total / 4;
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
  const f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = 0; v <= total + 1e-9; v += step) out.push(v);
  return out;
}

/**
 * OpenTelemetry/LangSmith-style waterfall of one request (compliance journey only). Stages are
 * sequential, so each bar starts where the previous one ended; widths are proportional to the
 * sealed `stage_ms`. Ledger seals are ticks on the same time axis.
 */
export function Waterfall({ stageMs, records }: { stageMs: Partial<Record<StageV1, number>>; records: JourneyRecord[] }) {
  const present = STAGES_V1.filter((s) => typeof stageMs[s] === 'number');
  const total = present.reduce((a, s) => a + (stageMs[s] ?? 0), 0);
  if (!present.length || total <= 0) return null;
  let at = 0;
  const bars = present.map((s) => {
    const v = stageMs[s] ?? 0;
    const b = { stage: s, start: at, dur: v };
    at += v;
    return b;
  });
  const byKind = new Map(records.map((r) => [r.kind, r]));
  const ticks = bars
    .filter((b) => SEAL_AFTER[b.stage])
    .map((b) => ({ at: b.start + b.dur, rec: byKind.get(SEAL_AFTER[b.stage]!), kind: SEAL_AFTER[b.stage]! }));
  const P = (v: number) => `${(v / total) * 100}%`;
  // Seals that land within 5% of the axis of each other (e.g. retrieval.decided at 228 ms and
  // answer.released at 229 ms) share one tick, so their labels never print over each other.
  const tickGroups: { at: number; items: typeof ticks }[] = [];
  for (const t of ticks) {
    const last = tickGroups[tickGroups.length - 1];
    if (last && t.at - last.at < total * 0.05) {
      last.items.push(t);
      last.at = t.at;
    } else tickGroups.push({ at: t.at, items: [t] });
  }
  return (
    <div data-testid="waterfall" data-tour="journey-waterfall">
      {/* axis */}
      <div className="grid grid-cols-[7.5rem_minmax(0,1fr)_4.5rem] gap-x-3 sm:grid-cols-[11rem_minmax(0,1fr)_5rem]" aria-hidden>
        <span className="text-micro font-semibold uppercase tracking-wide text-subtle">Stage</span>
        <div className="relative h-4 text-micro tabular-nums text-subtle">
          {axisTicks(total).map((v) => (
            <span key={v} className={cn('absolute top-0', v === 0 ? '' : v / total > 0.92 ? '-translate-x-full' : '-translate-x-1/2')} style={{ left: P(v) }}>
              {int(v)}
            </span>
          ))}
        </div>
        <span className="text-right text-micro font-semibold uppercase tracking-wide text-subtle">ms</span>
      </div>
      <ol className="mt-1 space-y-1" aria-label={`Stage timings, ${ms(total)} in total`}>
        {bars.map((b) => (
          <li key={b.stage} className="grid grid-cols-[7.5rem_minmax(0,1fr)_4.5rem] items-center gap-x-3 sm:grid-cols-[11rem_minmax(0,1fr)_5rem]" data-stage={b.stage}>
            <div className="min-w-0">
              <p className="truncate text-body-sm font-medium text-fg">{STAGE_LABEL[b.stage]}</p>
              <p className="hidden truncate text-micro text-subtle sm:block">{STAGE_HINT[b.stage]}</p>
            </div>
            <div aria-hidden className="relative h-6 rounded bg-raised/60">
              {axisTicks(total)
                .filter((v) => v > 0)
                .map((v) => (
                  <span key={v} className="absolute inset-y-0 w-px bg-line" style={{ left: P(v) }} />
                ))}
              <span
                className={cn('absolute inset-y-1 rounded-sm', STAGE_FILL[b.stage])}
                style={{ left: P(b.start), width: `max(2px, ${P(b.dur)})` }}
                title={`${STAGE_LABEL[b.stage]}: ${ms(b.dur)}, starting at ${ms(b.start)}`}
              />
            </div>
            <p className="text-right text-body-sm tabular-nums text-fg">
              <span className="sr-only">{STAGE_LABEL[b.stage]} took </span>
              {int(b.dur)}
            </p>
          </li>
        ))}
        {/* the ledger lane: seals on the same axis */}
        <li className="grid grid-cols-[7.5rem_minmax(0,1fr)_4.5rem] items-center gap-x-3 border-t border-line pt-2 sm:grid-cols-[11rem_minmax(0,1fr)_5rem]">
          <p className="flex items-center gap-1.5 text-body-sm font-medium text-fg">
            <Database aria-hidden className="h-3.5 w-3.5 text-muted" /> Ledger seals
          </p>
          <div className="relative h-8">
            {tickGroups.map((g) => (
              <span
                key={g.items.map((t) => t.kind).join('+')}
                className={cn(
                  'absolute inset-y-0 flex flex-col',
                  g.at / total < 0.06 ? 'items-start' : g.at / total > 0.94 ? '-translate-x-full items-end' : '-translate-x-1/2 items-center',
                )}
                style={{ left: P(g.at) }}
              >
                <span aria-hidden className="h-3 w-0.5 rounded bg-fg" />
                <span className="mt-0.5 whitespace-nowrap font-mono text-[10px] leading-3 text-muted">
                  {g.items.map((t, i) => (
                    <span key={t.kind}>
                      <span className="sr-only">{t.kind} sealed at {ms(t.at)}, </span>
                      {i > 0 ? <span aria-hidden> · </span> : null}
                      {t.rec ? `#${t.rec.seq}` : t.kind.split('.')[1]}
                    </span>
                  ))}
                </span>
              </span>
            ))}
          </div>
          <p className="text-right text-caption tabular-nums text-subtle">{ticks.length} seals</p>
        </li>
      </ol>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2 text-caption text-muted">
        <span>
          Total of sealed stage timings <span className="font-semibold tabular-nums text-fg">{ms(total)}</span>
        </span>
        <span className="text-subtle">Stages run in sequence; bars are laid end to end. Compose includes the model and the grounding check.</span>
      </div>
    </div>
  );
}

export function WaterfallTable({ stageMs, records }: { stageMs: Partial<Record<StageV1, number>>; records: JourneyRecord[] }) {
  const byKind = new Map(records.map((r) => [r.kind, r]));
  return (
    <MiniTable
      caption="Stage timings in milliseconds and the ledger record sealed after each stage"
      head={['Stage', 'What it does', 'ms', 'Sealed after']}
      numeric={[2]}
      rows={STAGES_V1.filter((s) => typeof stageMs[s] === 'number').map((s) => {
        const k = SEAL_AFTER[s];
        const rec = k ? byKind.get(k) : undefined;
        return [STAGE_LABEL[s], STAGE_HINT[s], int(stageMs[s]), k ? `${k}${rec ? ` #${rec.seq} ${shortHash(rec.hash)}` : ''}` : '—'];
      })}
    />
  );
}
