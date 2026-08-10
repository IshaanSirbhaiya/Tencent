"use client";
import * as React from "react";
import { Clock } from "lucide-react";

// Part 2 — the bi-temporal time slider. Dragging left fades out nodes/edges that weren't valid yet;
// the graph rebuilds live (CSS transitions, not janky jumps). "You're viewing Acme Corp as of …"
// updates as you drag. No competitor shows this. The slider only changes VISIBILITY of backend
// bi-temporal truth (valid_from/valid_to come from the backend) — it derives nothing (D-FE-2).

const MIN = new Date("2024-01-01T00:00:00Z").getTime();
const MAX = new Date("2024-06-01T00:00:00Z").getTime();

function fmt(ts: number): string {
  return new Date(ts).toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

export function TimeSlider({ onChange }: { onChange: (asOf: Date | null) => void }) {
  const [pct, setPct] = React.useState(100); // 100 = today (show everything)
  const ts = MIN + ((MAX - MIN) * pct) / 100;
  const atToday = pct >= 100;

  React.useEffect(() => {
    onChange(atToday ? null : new Date(ts));
  }, [pct]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex items-center gap-3 border-t border-border bg-surface px-5 py-2.5">
      <Clock className="h-4 w-4 shrink-0 text-subtle" />
      <span className="w-16 shrink-0 text-2xs text-subtle">Jan 2024</span>
      <input
        type="range"
        min={0}
        max={100}
        value={pct}
        onChange={(e) => setPct(Number(e.target.value))}
        aria-label="Time slider — view the graph as of a date"
        className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-muted accent-accent"
      />
      <span className="w-12 shrink-0 text-2xs text-subtle">Today</span>
      <div className="w-44 shrink-0 text-right text-2xs">
        <span className="text-subtle">Viewing as of </span>
        <span className="font-medium text-accent">{atToday ? "Today" : fmt(ts)}</span>
      </div>
    </div>
  );
}
