"use client";
import * as React from "react";
import { Clock, RotateCcw } from "lucide-react";
import type { BrainGraph } from "@/lib/brain-types";
import { ExportSheetsButton } from "./export-sheets-button";

/**
 * The bi-temporal time slider: "show me the world as the graph knew it on <date>".
 *
 * It only changes the VISIBILITY of bi-temporal truth the backend already sent — `valid_from` /
 * `valid_to` come from the graph, and this derives nothing (D-FE-2).
 *
 * ## The bug this file used to be
 *
 * The range was hardcoded `MIN = 2024-01-01`, `MAX = 2024-06-01`, left over from the Acme seed. The
 * graph's earliest `valid_from` is **2026-01-01**. So every position on the slider except the
 * far right resolved to a date about two years before anything in the graph existed, every node
 * failed the `valid_from <= t` test, and the canvas went **completely blank**. Dragging it looked
 * like a broken control rather than like time travel.
 *
 * The domain is now derived from the data. A slider whose range is a constant, filtering data loaded
 * at runtime, is always one reseed away from this.
 */

/** Bi-temporal bounds of a graph, in epoch ms. */
function domainOf(graph: BrainGraph | undefined): { min: number; max: number } | null {
  if (!graph || graph.nodes.length === 0) return null;
  const stamps: number[] = [];
  for (const n of graph.nodes) {
    if (n.valid_from) stamps.push(new Date(n.valid_from).getTime());
    if (n.valid_to) stamps.push(new Date(n.valid_to).getTime());
  }
  for (const e of graph.edges) {
    if (e.valid_from) stamps.push(new Date(e.valid_from).getTime());
    if (e.valid_to) stamps.push(new Date(e.valid_to).getTime());
  }
  const usable = stamps.filter((t) => Number.isFinite(t));
  if (usable.length === 0) return null;
  const min = Math.min(...usable);
  const max = Math.max(...usable);
  // A graph whose facts all share one timestamp has nothing to scrub through. Widen FORWARD, never
  // backward: widening backward puts every slider position below 100% before the sole `valid_from`,
  // so every node fails the visibility test and the canvas empties — which is precisely the
  // blank-graph bug this file was rewritten to fix, reintroduced by the degenerate case.
  return max > min ? { min, max } : { min, max: max + 86_400_000 };
}

function fmt(ts: number): string {
  return new Date(ts).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function fmtShort(ts: number): string {
  return new Date(ts).toLocaleDateString("en-GB", { month: "short", year: "numeric" });
}

export function TimeSlider({
  graph,
  onChange,
}: {
  graph: BrainGraph | undefined;
  onChange: (asOf: Date | null) => void;
}) {
  const domain = React.useMemo(() => domainOf(graph), [graph]);
  const [pct, setPct] = React.useState(100); // 100 = now (show everything)
  const atNow = pct >= 100;
  const ts = domain ? domain.min + ((domain.max - domain.min) * pct) / 100 : null;

  // Report the absolute instant, not the percentage: the parent filters on a date, and the domain
  // changes under us when the graph is reseeded.
  //
  // At 100% this used to emit `null`, and `visibleAt` reads a null `asOf` as "show everything" — so
  // the position labelled "now" actually meant "ignore temporal validity entirely", and a rule that
  // had EXPIRED would still be displayed as current. Emitting the real instant makes the label
  // true. (Latent today because every `valid_to` in the current seed is null, but the slider is the
  // bi-temporal claim we make to judges; it should not be quietly lying at its default position.)
  React.useEffect(() => {
    if (ts === null) {
      onChange(null);                     // no temporal data at all — nothing to filter on
      return;
    }
    onChange(atNow ? new Date() : new Date(ts));
  }, [ts, atNow, onChange]);

  const visibleCount = React.useMemo(() => {
    if (!graph) return 0;
    if (ts === null) return graph.nodes.length;
    const at = atNow ? Date.now() : ts;
    return graph.nodes.filter((n) => {
      if (!n.valid_from) return true;
      const from = new Date(n.valid_from).getTime();
      const to = n.valid_to ? new Date(n.valid_to).getTime() : Infinity;
      return from <= at && at < to;
    }).length;
  }, [graph, ts, atNow]);

  return (
    <div className="flex items-center gap-3 border-t border-border bg-surface px-5 py-2.5">
      <Clock className="h-4 w-4 shrink-0 text-subtle" />
      <span className="w-20 shrink-0 text-2xs text-subtle">{domain ? fmtShort(domain.min) : "—"}</span>
      <input
        type="range"
        min={0}
        max={100}
        step={0.5}
        value={pct}
        disabled={domain === null}
        onChange={(e) => setPct(Number(e.target.value))}
        aria-label="Time slider — view the graph as it was known on a date"
        data-testid="time-slider"
        className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-muted accent-accent disabled:cursor-not-allowed disabled:opacity-40"
      />
      <span className="w-10 shrink-0 text-2xs text-subtle">Now</span>

      <div className="flex w-72 shrink-0 items-center justify-end gap-2 text-2xs">
        <span className="text-subtle">Viewing as of</span>
        <span className="font-medium text-accent" data-testid="time-slider-asof">
          {atNow || ts === null ? "now" : fmt(ts)}
        </span>
        {/* The count is the proof the control did something. Without it a rewind that happens to
            change nothing looks identical to a rewind that is broken — which is how the 2024 bug
            survived as long as it did. */}
        <span className="tnum rounded bg-muted px-1.5 py-0.5 text-subtle" data-testid="time-slider-count">
          {visibleCount}/{graph?.nodes.length ?? 0} nodes
        </span>
        {!atNow ? (
          <button
            type="button"
            onClick={() => setPct(100)}
            title="Back to now"
            className="rounded p-0.5 text-subtle transition-colors hover:bg-muted hover:text-fg"
          >
            <RotateCcw className="h-3 w-3" />
          </button>
        ) : null}
      </div>

      {/* The export lives HERE, beside the instant it uses. `null` at "now" means the archive is
          the current graph; rewound, it carries the same instant the label above reports, so the
          folder that lands in Downloads is the graph on screen rather than today's. */}
      <ExportSheetsButton asOf={atNow || ts === null ? null : new Date(ts).toISOString()} />
    </div>
  );
}
