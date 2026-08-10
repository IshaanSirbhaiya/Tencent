import { cn } from "@/lib/utils";

// Exact backend figures, tabular numerals (D-FE-3). No traffic-light-only summaries.
export function MetricTile({
  label,
  value,
  unit,
  sub,
  accent,
}: {
  label: string;
  value: string | number;
  unit?: string;
  sub?: string;
  accent?: "ok" | "warn" | "bad" | "neutral";
}) {
  const color =
    accent === "ok"
      ? "text-band-auto"
      : accent === "warn"
        ? "text-band-spot"
        : accent === "bad"
          ? "text-band-review"
          : "text-fg";
  return (
    <div className="rounded border border-border bg-surface px-4 py-3">
      <div className="text-2xs uppercase tracking-wide text-subtle">{label}</div>
      <div className={cn("tnum mt-1 text-2xl font-semibold leading-none", color)}>
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-subtle">{unit}</span>}
      </div>
      {sub && <div className="mt-1 text-2xs text-subtle">{sub}</div>}
    </div>
  );
}
