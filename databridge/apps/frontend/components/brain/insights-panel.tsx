"use client";
import * as React from "react";
import { Lightbulb } from "lucide-react";
import type { Insight } from "@/lib/brain-types";
import { Grounded } from "@/components/grounded";

// Column 2 — Recent Insights (§Part 1). Each insight is GENERATED SERVER-SIDE and grounded; clicking
// zooms the graph to its focus subgraph. The "receipt" chip shows the backing SourcePointer(s).
export function InsightsPanel({
  insights,
  onFocus,
}: {
  insights: Insight[] | undefined;
  onFocus: (nodeId: string) => void;
}) {
  return (
    <div className="flex h-full flex-col gap-3">
      <h3 className="text-2xs font-semibold uppercase tracking-wider text-subtle">Recent insights</h3>
      <div className="flex flex-col gap-1.5 overflow-y-auto">
        {(insights ?? []).map((ins, i) => (
          // row is a div (not a button) so the Grounded provenance chip isn't nested in a button
          <div
            key={i}
            className="group flex items-start gap-2.5 rounded border border-border bg-surface px-3 py-2 transition-colors hover:border-accent/40"
          >
            <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
            <div className="min-w-0 flex-1">
              <button
                onClick={() => onFocus(ins.focus)}
                className="block w-full text-left text-xs leading-snug text-fg hover:text-accent"
              >
                {ins.text}
              </button>
              {ins.detail ? <div className="mt-0.5 truncate text-2xs text-subtle">{ins.detail}</div> : null}
              <div className="mt-1 flex items-center gap-1.5">
                <Grounded pointer={ins.citations[0] ?? null}>
                  <span className="text-2xs text-accent">
                    <span className="tnum">{ins.citations.length}</span> source
                    {ins.citations.length === 1 ? "" : "s"}
                  </span>
                </Grounded>
              </div>
            </div>
          </div>
        ))}
        {insights && insights.length === 0 ? (
          <div className="rounded border border-dashed border-border px-3 py-4 text-center text-2xs text-subtle">
            No insights yet — load the demo data.
          </div>
        ) : null}
      </div>
    </div>
  );
}
