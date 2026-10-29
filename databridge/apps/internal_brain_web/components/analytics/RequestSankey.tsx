'use client';
import { cn } from '@/lib/cn';
import { int, pct } from './format';

interface Flow {
  key: string;
  label: string;
  value: number;
  color: string;
  swatch: string;
  hint: string;
}

/**
 * Request flow: asked → answered / refused / failed. Units are REQUESTS only (documents have their
 * own funnel). Hand-drawn SVG bands (decorative) with HTML labels carrying every number.
 */
export function RequestSankey({ asked, answered, refused, failed }: { asked: number; answered: number; refused: number; failed: number }) {
  const flows: Flow[] = [
    { key: 'answered', label: 'Answered', value: answered, color: 'rgb(var(--accent))', swatch: 'bg-accent', hint: 'grounded answer with citations' },
    { key: 'refused', label: 'Refused', value: refused, color: 'rgb(var(--subtle))', swatch: 'bg-subtle', hint: 'no permitted evidence: the safe outcome' },
    { key: 'failed', label: 'Failed', value: failed, color: 'rgb(var(--bad))', swatch: 'bg-bad', hint: 'fail closed (503), nothing shown' },
  ];
  const H = 200;
  const W = 400;
  const NODE = 10;
  const GAP = 14;
  const total = Math.max(1, asked);
  const usable = H - GAP * (flows.length - 1);
  const minH = 3;
  // right-hand node heights (min 3 px so a zero still has a mark), scaled to fit the column
  const raw = flows.map((f) => Math.max(minH, (f.value / total) * usable));
  const k = usable / raw.reduce((a, b) => a + b, 0);
  const hs = raw.map((h) => h * k);
  let y = 0;
  const right = hs.map((h) => {
    const r = { y0: y, y1: y + h };
    y += h + GAP;
    return r;
  });
  // left node: one block; each band leaves from its share of it
  const leftH = H;
  let ly = 0;
  const left = hs.map((h) => {
    const share = (h / hs.reduce((a, b) => a + b, 0)) * leftH;
    const r = { y0: ly, y1: ly + share };
    ly += share;
    return r;
  });
  const x0 = NODE;
  const x1 = W - NODE;
  const mid = (x0 + x1) / 2;
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)_minmax(8.5rem,auto)] items-stretch gap-x-3" data-testid="request-sankey">
      <div className="flex flex-col justify-center text-right">
        <p className="text-caption text-muted">Asked</p>
        <p className="text-heading tabular-nums text-fg">{int(asked)}</p>
        <p className="text-micro text-subtle">requests</p>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-52 w-full" aria-hidden>
        <rect x={0} y={0} width={NODE} height={H} rx={2} fill="rgb(var(--muted))" />
        {flows.map((f, i) => {
          const a = left[i];
          const b = right[i];
          const d = `M${x0},${a.y0} C${mid},${a.y0} ${mid},${b.y0} ${x1},${b.y0} L${x1},${b.y1} C${mid},${b.y1} ${mid},${a.y1} ${x0},${a.y1} Z`;
          return (
            <g key={f.key}>
              <path d={d} fill={f.color} fillOpacity={0.28} stroke="rgb(var(--surface))" strokeWidth={1}>
                <title>{`${f.label}: ${int(f.value)} of ${int(asked)} requests`}</title>
              </path>
              <rect x={x1} y={b.y0} width={NODE} height={Math.max(2, b.y1 - b.y0)} rx={2} fill={f.color} />
            </g>
          );
        })}
      </svg>
      <div className="relative h-52">
        {flows.map((f, i) => {
          const c = ((right[i].y0 + right[i].y1) / 2 / H) * 100;
          return (
            <div key={f.key} className="absolute left-0 right-0 -translate-y-1/2" style={{ top: `${c}%` }}>
              <p className="flex items-center gap-1.5 text-caption text-muted">
                <span aria-hidden className={cn('h-2 w-2 rounded-sm', f.swatch)} />
                {f.label}
              </p>
              <p className="text-body-sm tabular-nums text-fg">
                <span className="font-semibold">{int(f.value)}</span> <span className="text-subtle">· {pct(f.value, asked)}</span>
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export const FLOW_HINT: Record<string, string> = {
  answered: 'Grounded answer with citations',
  refused: 'No permitted evidence: the safe outcome',
  failed: 'Failed closed (503); nothing shown',
};
