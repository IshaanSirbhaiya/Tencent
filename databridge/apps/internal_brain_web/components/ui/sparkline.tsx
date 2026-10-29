'use client';
import { Line, LineChart, ResponsiveContainer, YAxis } from 'recharts';

/**
 * Tiny trend line for KpiTile's sparkline slot. Decorative (aria-hidden): the tile's value and
 * delta carry the meaning. `values` must come from the backend (e.g. /audit/stats `days`).
 */
export function Sparkline({ values, color = 'rgb(var(--accent))' }: { values: number[]; color?: string }) {
  const data = values.map((v, i) => ({ i, v }));
  return (
    <div aria-hidden className="h-full w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={['dataMin', 'dataMax']} />
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
