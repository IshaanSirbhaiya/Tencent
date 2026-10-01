"use client";
import * as React from "react";
import { Boxes, Loader2 } from "lucide-react";
import { usePack } from "@/lib/pack-context";

// Top bar for /brain. The source count is the backend's real number.
//
// The tenant selector that used to sit here is gone rather than relabelled: one deployment serves
// one organisation, so a tenant switcher would be a control that does nothing. Which pack's
// vocabulary is in force IS a real choice, and it lives in the sidebar's pack switcher — this bar
// only reflects it.
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
  const { pack } = usePack();
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-surface px-5">
      <div className="flex items-center gap-2.5">
        <Boxes className="h-5 w-5 text-accent" />
        <span className="text-sm font-semibold tracking-tight">{pack.label}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-2xs font-medium text-subtle">
          {pack.nouns.workspace}
        </span>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5 text-2xs text-subtle">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-band-auto" : "bg-band-review"}`} />
          <span className="tnum">{sourceCount}</span> source{sourceCount === 1 ? "" : "s"} connected
        </div>
        {/* amber CTA (prompt-specified "amber, prominent"). The text colour is its own token
            because the two themes need opposite answers: near-black on the bright dark-mode amber,
            white on the darker light-mode one. Either way it clears AA. */}
        <button
          onClick={onLoadDemo}
          disabled={seeding}
          className="flex items-center gap-1.5 rounded bg-band-spot px-3 py-1.5 text-xs font-semibold text-[hsl(var(--on-band-spot))] transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {seeding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {seeding ? "Loading…" : `Reload ${pack.nouns.graph}`}
        </button>
      </div>
    </header>
  );
}
