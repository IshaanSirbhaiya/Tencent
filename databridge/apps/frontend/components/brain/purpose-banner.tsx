"use client";
import * as React from "react";
import { Info, X } from "lucide-react";

// The WHY banner (§Part 4). Answers "how did this graph appear?" before an investor asks. Slim,
// dismissable, token-styled (no gradient/hero — D-FE-3).
export function PurposeBanner() {
  const [open, setOpen] = React.useState(true);
  if (!open) return null;
  return (
    <div className="flex items-start gap-3 border-b border-accent/30 bg-accent/[0.06] px-5 py-2.5">
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
      <p className="flex-1 text-xs leading-relaxed text-fg">
        DataBridge built this knowledge graph from <strong>3 documents</strong> — an email, a meeting
        transcript, and a contract. Every connection is cited.{" "}
        <span className="font-medium text-accent">Nothing is made up.</span>
      </p>
      <button
        onClick={() => setOpen(false)}
        aria-label="Dismiss"
        className="shrink-0 rounded p-0.5 text-subtle transition-colors hover:bg-muted hover:text-fg"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
