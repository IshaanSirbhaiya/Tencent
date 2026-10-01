"use client";
import * as React from "react";
import type { BrainGraph } from "@/lib/brain-types";
import { useCountUp } from "./use-count-up";

// Column 3 — Brain Stats (§Part 1). Live counters animate up on load; the figures are the EXACT
// backend numbers (D-FE-2) — never the prompt's illustrative values. "100% provenance" is the key
// differentiator. Below: a provenance mini-bar (source → entities), computed server-side.

// `value` undefined = UNKNOWN (graph not loaded, or the backend did not report it) and renders "—".
// It used to default to 0 entities and 100% provenance, which with the backend down is the worst
// possible claim on a governance panel.
function Counter({ value, label, accent }: { value: number | undefined; label: string; accent?: boolean }) {
  const n = useCountUp(value ?? 0);
  return (
    <div className="rounded border border-border bg-surface px-3 py-2.5">
      <div className={`tnum text-2xl font-semibold leading-none ${accent && value != null ? "text-band-auto" : "text-fg"}`}>
        {value == null ? "—" : n}
        {accent && value != null ? "%" : ""}
      </div>
      <div className="mt-1 text-2xs uppercase tracking-wide text-subtle">{label}</div>
    </div>
  );
}

export function StatsPanel({ graph }: { graph: BrainGraph | undefined }) {
  const stats = graph?.stats;
  const prov = graph?.provenance_by_source ?? {};
  const maxProv = Math.max(1, ...Object.values(prov));

  return (
    <div className="flex h-full flex-col gap-3">
      <h3 className="text-2xs font-semibold uppercase tracking-wider text-subtle">Brain stats</h3>
      <div className="grid grid-cols-2 gap-2">
        <Counter value={stats?.nodes} label="entities" />
        <Counter value={stats?.edges} label="connections" />
        <Counter value={graph ? Object.keys(prov).length : undefined} label="source docs" />
        {/* provenance % is the backend-measured figure (stats.provenance_pct); unknown is "—" */}
        <Counter value={stats?.provenance_pct ?? undefined} label="provenance" accent />
      </div>

      <div className="mt-1">
        <div className="mb-1.5 text-2xs uppercase tracking-wide text-subtle">
          Provenance — entities per source
        </div>
        <div className="space-y-1.5">
          {Object.entries(prov).map(([src, n]) => (
            <div key={src} className="flex items-center gap-2">
              <span className="w-28 shrink-0 truncate text-2xs text-subtle" title={src}>
                {/* The source id as the backend reported it. A filename→label table keyed to old
                    demo fixtures used to sit here (SWEEP #18); it recalled, it did not read. */}
                {src}
              </span>
              <div className="h-3 flex-1 overflow-hidden rounded-sm bg-muted">
                <div
                  className="h-full rounded-sm bg-accent/70 transition-all duration-700"
                  style={{ width: `${(n / maxProv) * 100}%` }}
                />
              </div>
              <span className="tnum w-6 shrink-0 text-right text-2xs text-fg">{n}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
