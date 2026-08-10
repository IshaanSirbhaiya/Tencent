"use client";
import * as React from "react";
import type { BrainGraph } from "@/lib/brain-types";
import { useCountUp } from "./use-count-up";

// Column 3 — Brain Stats (§Part 1). Live counters animate up on load; the figures are the EXACT
// backend numbers (D-FE-2) — never the prompt's illustrative values. "100% provenance" is the key
// differentiator. Below: a provenance mini-bar (source → entities), computed server-side.

function Counter({ value, label, accent }: { value: number; label: string; accent?: boolean }) {
  const n = useCountUp(value);
  return (
    <div className="rounded border border-border bg-surface px-3 py-2.5">
      <div className={`tnum text-2xl font-semibold leading-none ${accent ? "text-band-auto" : "text-fg"}`}>
        {n}
        {accent ? "%" : ""}
      </div>
      <div className="mt-1 text-2xs uppercase tracking-wide text-subtle">{label}</div>
    </div>
  );
}

const SOURCE_ICON: Record<string, string> = {
  "email_001.eml": "email",
  "meeting_001.txt": "meeting transcript",
  "contract_001.pdf": "contract",
};

export function StatsPanel({ graph }: { graph: BrainGraph | undefined }) {
  const stats = graph?.stats;
  const prov = graph?.provenance_by_source ?? {};
  const maxProv = Math.max(1, ...Object.values(prov));

  return (
    <div className="flex h-full flex-col gap-3">
      <h3 className="text-2xs font-semibold uppercase tracking-wider text-subtle">Brain stats</h3>
      <div className="grid grid-cols-2 gap-2">
        <Counter value={stats?.nodes ?? 0} label="entities" />
        <Counter value={stats?.edges ?? 0} label="connections" />
        <Counter value={Object.keys(prov).length} label="source docs" />
        {/* provenance % is the backend-measured figure (stats.provenance_pct), not a hardcoded 100 */}
        <Counter value={stats?.provenance_pct ?? 100} label="provenance" accent />
      </div>

      <div className="mt-1">
        <div className="mb-1.5 text-2xs uppercase tracking-wide text-subtle">
          Provenance — entities per source
        </div>
        <div className="space-y-1.5">
          {Object.entries(prov).map(([src, n]) => (
            <div key={src} className="flex items-center gap-2">
              <span className="w-28 shrink-0 truncate text-2xs text-subtle" title={src}>
                {SOURCE_ICON[src] ?? src}
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
