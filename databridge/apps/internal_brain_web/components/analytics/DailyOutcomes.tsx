'use client';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from 'recharts';
import { int } from './format';

export interface DayRow {
  day: string;
  answered: number;
  refused: number;
  failed: number;
}

export const OUTCOME_SERIES = [
  { key: 'answered', label: 'Answered', color: 'rgb(var(--accent))', swatch: 'bg-accent' },
  { key: 'refused', label: 'Refused (no permitted evidence)', color: 'rgb(var(--subtle))', swatch: 'bg-subtle' },
  { key: 'failed', label: 'Failed (503)', color: 'rgb(var(--bad))', swatch: 'bg-bad' },
] as const;

const dayFmt = new Intl.DateTimeFormat('en-SG', { day: 'numeric', month: 'short', timeZone: 'UTC' });
export const fmtDay = (d: string) => dayFmt.format(new Date(`${d}T00:00:00Z`));

/**
 * Every day of the window, oldest first, with zero for days the backend did not list (the backend
 * lists only days with events). No other arithmetic.
 */
export function fillDays(days: { day: string; answered?: number; refused?: number; failed?: number }[], since: string, until: string): DayRow[] {
  const by = new Map(days.map((d) => [d.day, d]));
  const out: DayRow[] = [];
  const start = new Date(since);
  const end = new Date(until);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return days.map((d) => ({ day: d.day, answered: d.answered ?? 0, refused: d.refused ?? 0, failed: d.failed ?? 0 }));
  }
  const cur = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
  while (cur <= end) {
    const key = cur.toISOString().slice(0, 10);
    const d = by.get(key);
    out.push({ day: key, answered: d?.answered ?? 0, refused: d?.refused ?? 0, failed: d?.failed ?? 0 });
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return out;
}

function Tip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as DayRow;
  return (
    <div className="rounded-md border border-line-strong bg-overlay px-3 py-2 text-caption shadow-pop">
      <p className="mb-1 font-medium text-fg">{fmtDay(String(label))}</p>
      {OUTCOME_SERIES.map((s) => (
        <p key={s.key} className="flex items-center justify-between gap-4 text-muted">
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className={`h-2 w-2 rounded-sm ${s.swatch}`} />
            {s.label}
          </span>
          <span className="tabular-nums text-fg">{int(row[s.key])}</span>
        </p>
      ))}
    </div>
  );
}

export function Legend() {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-caption text-muted" aria-label="Legend">
      {OUTCOME_SERIES.map((s) => (
        <li key={s.key} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={`h-2.5 w-2.5 rounded-sm ${s.swatch}`} />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

/** Requests per day, stacked answered / refused / failed. Decorative SVG; the table toggle carries the data. */
export function DailyOutcomes({ data }: { data: DayRow[] }) {
  return (
    <div>
      <div aria-hidden className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -18 }} barCategoryGap={data.length > 45 ? 1 : 3}>
            <CartesianGrid vertical={false} stroke="rgb(var(--line))" />
            <XAxis
              dataKey="day"
              tickFormatter={fmtDay}
              tick={{ fill: 'rgb(var(--subtle))', fontSize: 11 }}
              axisLine={{ stroke: 'rgb(var(--line-strong))' }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis allowDecimals={false} tick={{ fill: 'rgb(var(--subtle))', fontSize: 11 }} axisLine={false} tickLine={false} width={44} />
            <Tooltip content={<Tip />} cursor={{ fill: 'rgb(var(--raised))' }} isAnimationActive={false} />
            {OUTCOME_SERIES.map((s, i) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                stackId="o"
                fill={s.color}
                stroke="rgb(var(--surface))"
                strokeWidth={1}
                radius={i === OUTCOME_SERIES.length - 1 ? [2, 2, 0, 0] : 0}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2">
        <Legend />
      </div>
    </div>
  );
}
