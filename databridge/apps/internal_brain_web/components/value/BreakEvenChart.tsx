'use client';
import { useEffect, useRef, useState } from 'react';
import { usdCompact } from './model';

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, w };
}

const niceStep = (span: number, n: number) => {
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
};

/**
 * The break-even line: value of time saved per year (a straight line in minutes saved) against the
 * annual cost (flat). They cross at break-even. Decorative SVG; the caption states every number.
 */
export function BreakEvenChart({
  valuePerMinute,
  cost,
  minutes,
  breakEven,
  labels,
}: {
  /** US$ per year for each minute saved per person per week (people × weeks × cost/h ÷ 60). */
  valuePerMinute: number;
  cost: number;
  minutes: number;
  breakEven: number;
  labels: { value: string; cost: string; you: string; x: string; breakEven: string };
}) {
  const { ref, w } = useWidth<HTMLDivElement>();
  const h = 240;
  const m = { l: 70, r: 16, t: 14, b: 36 };
  const X = Math.min(480, Math.max(60, Math.ceil((Math.max(minutes, Number.isFinite(breakEven) ? breakEven : 0) * 1.35) / 30) * 30));
  const yMax = Math.max(cost * 1.25, valuePerMinute * X * 1.05, 1);
  const sx = (v: number) => m.l + (v / X) * (w - m.l - m.r);
  const sy = (v: number) => h - m.b - (v / yMax) * (h - m.t - m.b);
  const xStep = niceStep(X, w < 420 ? 3 : 6);
  const yStep = niceStep(yMax, 4);
  const xt: number[] = [];
  for (let v = 0; v <= X + 1e-9; v += xStep) xt.push(v);
  const yt: number[] = [];
  for (let v = 0; v <= yMax + 1e-9; v += yStep) yt.push(v);
  const youV = valuePerMinute * minutes;
  const be = Number.isFinite(breakEven) && breakEven <= X ? breakEven : null;
  return (
    <div ref={ref} className="w-full overflow-hidden">
      <svg width={w} height={h} aria-hidden className="block">
        {yt.map((v) => (
          <g key={`y${v}`}>
            <line x1={m.l} x2={w - m.r} y1={sy(v)} y2={sy(v)} stroke="rgb(var(--line))" />
            <text x={m.l - 8} y={sy(v)} dy="0.32em" textAnchor="end" fontSize={11} fill="rgb(var(--subtle))" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {usdCompact(v)}
            </text>
          </g>
        ))}
        {xt.map((v) => (
          <text key={`x${v}`} x={sx(v)} y={h - m.b + 16} textAnchor="middle" fontSize={11} fill="rgb(var(--subtle))" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {Math.round(v)}
          </text>
        ))}
        <text x={(m.l + w - m.r) / 2} y={h - 4} textAnchor="middle" fontSize={11} fill="rgb(var(--muted))">
          {labels.x}
        </text>
        <line x1={m.l} x2={w - m.r} y1={h - m.b} y2={h - m.b} stroke="rgb(var(--line-strong))" />
        {/* the annual cost */}
        <line x1={m.l} x2={w - m.r} y1={sy(cost)} y2={sy(cost)} stroke="rgb(var(--muted))" strokeWidth={1.5} strokeDasharray="5 4" />
        <text x={w - m.r} y={sy(cost) - 6} textAnchor="end" fontSize={11} fill="rgb(var(--muted))">
          {labels.cost} {usdCompact(cost)}
        </text>
        {/* value of time saved */}
        <line x1={sx(0)} y1={sy(0)} x2={sx(X)} y2={sy(valuePerMinute * X)} stroke="rgb(var(--accent))" strokeWidth={2} />
        {be !== null ? (
          <g>
            <line x1={sx(be)} x2={sx(be)} y1={sy(0)} y2={sy(cost)} stroke="rgb(var(--fg))" strokeOpacity={0.5} strokeDasharray="2 3" />
            <circle cx={sx(be)} cy={sy(cost)} r={4} fill="rgb(var(--surface))" stroke="rgb(var(--fg))" strokeWidth={1.5} />
            <text x={sx(be) + 7} y={sy(cost) + 16} fontSize={11} fill="rgb(var(--fg))" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {labels.breakEven}
            </text>
          </g>
        ) : null}
        <circle cx={sx(minutes)} cy={sy(youV)} r={5} fill="rgb(var(--accent))" stroke="rgb(var(--surface))" strokeWidth={2} />
        <text
          x={sx(minutes) + (sx(minutes) > w - 120 ? -8 : 8)}
          y={sy(youV) + 18}
          textAnchor={sx(minutes) > w - 120 ? 'end' : 'start'}
          fontSize={11}
          fontWeight={600}
          fill="rgb(var(--accent))"
          style={{ fontVariantNumeric: 'tabular-nums' }}
        >
          {labels.you}
        </text>
      </svg>
      <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-caption text-muted" aria-hidden>
        <li className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-accent" /> {labels.value}
        </li>
        <li className="inline-flex items-center gap-1.5">
          <span className="h-0 w-4 border-t-[1.5px] border-dashed border-muted" /> {labels.cost}
        </li>
      </ul>
    </div>
  );
}
