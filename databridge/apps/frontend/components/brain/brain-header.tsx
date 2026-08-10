"use client";
import * as React from "react";
import { Boxes, ChevronDown, Database, Loader2 } from "lucide-react";

// Part 1 top bar: logo + "Company Brain", the Acme Corp selector, Load Demo button, connection
// status. Token-styled (no gradients). The source count is the backend's real number.
export function BrainHeader({
  connected,
  sourceCount,
  seeding,
  onLoadDemo,
}: {
  connected: boolean;
  sourceCount: number;
  seeding: boolean;
  onLoadDemo: () => void;
}) {
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-surface px-5">
      <div className="flex items-center gap-2.5">
        <Boxes className="h-5 w-5 text-accent" />
        <span className="text-sm font-semibold tracking-tight">DataBridge</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-2xs font-medium text-subtle">
          Company Brain
        </span>
      </div>

      <button
        aria-label="Company: Acme Corp"
        aria-haspopup="listbox"
        className="flex items-center gap-1.5 rounded border border-border bg-bg px-3 py-1.5 text-xs font-medium text-fg hover:border-accent/40"
      >
        <Database className="h-3.5 w-3.5 text-subtle" />
        Acme Corp
        <ChevronDown className="h-3.5 w-3.5 text-subtle" />
      </button>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5 text-2xs text-subtle">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-band-auto" : "bg-band-review"}`} />
          <span className="tnum">{sourceCount}</span> source{sourceCount === 1 ? "" : "s"} connected
        </div>
        {/* amber CTA (prompt-specified "amber, prominent"); dark text on amber meets AA contrast */}
        <button
          onClick={onLoadDemo}
          disabled={seeding}
          className="flex items-center gap-1.5 rounded bg-band-spot px-3 py-1.5 text-xs font-semibold text-[#1a1206] transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {seeding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {seeding ? "Loading…" : "Load Demo Data"}
        </button>
      </div>
    </header>
  );
}
