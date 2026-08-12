"use client";
import { cn } from "@/lib/utils";
import type { IssuerRow } from "@/lib/intelligence-api";

/**
 * Who sent the clinic its paperwork — one row per organisation with an `ISSUED` edge to a
 * document, counted by walking those edges.
 *
 * There is no per-issuer anything in this component: the rows come from the graph, the bar widths
 * come from the counts, and adding an insurer is a `coverage_rules.yaml` change that lands here
 * with no code change at all (CLAUDE.md §Hard constraints 3). If you ever find yourself wanting to
 * special-case a name here, that is the constraint being violated.
 */
export function IssuerMix({
  rows,
  total,
  selected,
  onSelect,
}: {
  rows: IssuerRow[];
  /** documents in the graph — the denominator every row is a share of. */
  total: number;
  selected: string | null;
  onSelect: (members: string[], title: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-2xs text-subtle">
        No document in the graph carries an issuer yet.
      </p>
    );
  }
  const widest = Math.max(1, ...rows.map((r) => r.documents));

  return (
    <ul className="flex flex-col gap-1" data-testid="issuer-mix">
      {rows.map((row) => (
        <li key={row.issuer_id}>
          <button
            type="button"
            aria-pressed={selected === row.issuer_id}
            onClick={() => onSelect(row.members, `documents issued by ${row.issuer}`)}
            className={cn(
              "flex w-full items-center gap-3 rounded border px-3 py-1.5 text-left transition-colors",
              selected === row.issuer_id
                ? "border-accent bg-muted/40"
                : "border-transparent hover:bg-muted/40",
            )}
          >
            <span className="min-w-0 flex-1 truncate text-xs">{row.issuer}</span>
            <span className="h-2 w-24 shrink-0 rounded-sm bg-muted/60">
              <span
                className="block h-2 rounded-sm bg-accent"
                style={{ width: `${(row.documents / widest) * 100}%` }}
              />
            </span>
            <span className="tnum w-16 shrink-0 text-right text-2xs text-subtle">
              {row.documents} of {total}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
