"use client";
import { cn } from "@/lib/utils";
import type { Count } from "@/lib/intelligence-api";

/**
 * One structural figure: `n of `of``, its label, and the way to the traversal behind it.
 *
 * The denominator is rendered at the same weight as the numerator, on purpose. "2 conflicts" is
 * not a fact; "2 of 10 documents" is. A card that could show a bare total would let this page
 * drift into the thing it is not — an outcome claim.
 *
 * The whole card is the button. `members` are the ids the backend counted, so "show the subgraph"
 * is never a second query the browser invents; it is the same list the count was computed from.
 */
export function FigureCard({
  figure,
  caveat,
  accent = "neutral",
  selected,
  onSelect,
}: {
  figure: Count | null;
  /** What the number does NOT say. Rendered always, not on hover — a caveat behind an
   *  interaction is a caveat nobody reads. */
  caveat: string;
  accent?: "neutral" | "warn" | "bad";
  selected: boolean;
  onSelect: (members: string[], title: string) => void;
}) {
  if (!figure) {
    return (
      <div className="rounded-lg border border-dashed border-border px-4 py-3 text-2xs text-subtle">
        not computed
      </div>
    );
  }
  const tone =
    figure.n === 0
      ? "text-fg"
      : accent === "bad"
        ? "text-band-review"
        : accent === "warn"
          ? "text-band-spot"
          : "text-fg";
  const clickable = figure.members.length > 0;

  return (
    <button
      type="button"
      data-testid={`figure-${slug(figure.label)}`}
      disabled={!clickable}
      aria-pressed={selected}
      onClick={() => onSelect(figure.members, figure.label)}
      className={cn(
        "flex w-full flex-col items-start gap-1 rounded-lg border bg-surface px-4 py-3 text-left transition-colors",
        selected ? "border-accent" : "border-border",
        clickable ? "hover:border-accent/60" : "cursor-default",
      )}
    >
      <div className="flex items-baseline gap-1.5">
        <span className={cn("tnum text-2xl font-semibold leading-none", tone)}>{figure.n}</span>
        <span className="tnum text-sm text-subtle">of {figure.of}</span>
        <span className="tnum text-2xs text-subtle">({figure.pct}%)</span>
      </div>
      <div className="text-xs text-fg">{figure.label}</div>
      <div className="text-2xs text-subtle">{caveat}</div>
      <div className="mt-0.5 text-2xs text-accent">
        {clickable
          ? `show the ${figure.members.length === 1 ? "subgraph" : `${figure.members.length} subgraphs`} behind this`
          : "nothing to show — the count is zero"}
      </div>
    </button>
  );
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}
