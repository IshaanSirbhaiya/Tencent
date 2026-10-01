"use client";
/**
 * Kind → colour key, ported from `app/brain/page.tsx`'s `Legend` — same position (absolute,
 * top-left over the canvas), same box, same dot-with-glow, same `pointer-events-none` (it is a
 * key, not a control; the `kind` `<select>` in `FilterBar` is the actual filter). It lists the kinds
 * actually DRAWN, coloured by the same pack-data palette (`kind-palette.tsx`) the canvas uses,
 * with pack-declared kinds first.
 */
import * as React from "react";
import type { KindFacet } from "@/lib/graph-api";
import { useKindPalette } from "./kind-palette";

export function KindLegend({ kinds }: { kinds: KindFacet[] }) {
  const palette = useKindPalette();
  const entries = React.useMemo(() => {
    return kinds
      .map((k) => ({ kind: k.kind, count: k.count, style: palette.node(k.kind) }))
      .sort((a, b) => Number(b.style.fromPack) - Number(a.style.fromPack) || b.count - a.count);
  }, [kinds, palette]);

  if (entries.length === 0) return null;

  return (
    <div
      className="pointer-events-none absolute left-3 top-3 z-10 flex max-h-[85%] flex-col gap-1 overflow-y-auto rounded border border-graph-border bg-graph-surface/90 px-2.5 py-2"
      data-testid="graph-legend"
    >
      {entries.map(({ kind, count, style }) => (
        <div key={kind} className="flex items-center gap-1.5 text-2xs text-graph-subtle">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ background: style.color, boxShadow: `0 0 5px ${style.color}` }}
          />
          {style.label}
          <span className="tnum opacity-70">{count}</span>
        </div>
      ))}
    </div>
  );
}
