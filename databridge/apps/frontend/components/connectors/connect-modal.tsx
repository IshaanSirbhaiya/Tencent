"use client";
import * as React from "react";
import { X, CheckCircle2, Loader2 } from "lucide-react";

// Part 1 — the Connect flow modal (VISUAL ONLY, demo-mode). Slides up, shows what the connector
// would extract, then a (non-functional) OAuth-style button → a "Connected!" success state. Honest:
// it shows the vision, not fake live functionality (the page carries a "Demo mode" badge).

export interface ConnectorInfo {
  name: string;
  extracts: string; // "People, Decisions, Projects, Facts"
}

export function ConnectModal({
  connector,
  onClose,
  onConnected,
}: {
  connector: ConnectorInfo | null;
  onClose: () => void;
  onConnected: (name: string) => void;
}) {
  const [phase, setPhase] = React.useState<"intro" | "connecting" | "done">("intro");
  const open = !!connector;
  const dialogRef = React.useRef<HTMLDivElement>(null);

  // reset whenever a new connector opens, and move focus into the dialog (a11y)
  React.useEffect(() => {
    if (connector) {
      setPhase("intro");
      dialogRef.current?.focus();
    }
  }, [connector]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  function connect() {
    setPhase("connecting");
    setTimeout(() => {
      setPhase("done");
      if (connector) onConnected(connector.name);
    }, 1100);
  }

  if (!connector) return null;
  const bullets = connector.extracts.split(",").map((s) => s.trim());

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Connect ${connector.name}`}
        tabIndex={-1}
        className="fixed left-1/2 top-1/2 z-50 w-[420px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface shadow-lg outline-none"
        style={{ animation: "count-in 180ms ease-out" }}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="text-sm font-semibold text-fg">Connect {connector.name}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded text-subtle hover:text-fg focus:outline-none focus:ring-2 focus:ring-accent">
            <X className="h-4 w-4" />
          </button>
        </div>

        {phase === "done" ? (
          <div className="flex flex-col items-center gap-2 px-5 py-8 text-center">
            <CheckCircle2 className="h-9 w-9 text-band-auto" />
            <div className="text-sm font-semibold text-fg">Connected!</div>
            <div className="text-2xs text-subtle">
              {connector.name} would now enrich the Company Brain on every sync.
            </div>
            <button onClick={onClose} className="mt-2 rounded bg-muted px-3 py-1.5 text-xs font-medium text-fg hover:bg-elevated">
              Done
            </button>
          </div>
        ) : (
          <div className="px-5 py-4">
            <p className="text-xs leading-relaxed text-subtle">
              DataBridge will read your {connector.name.toLowerCase()} data and extract:
            </p>
            <ul className="mt-2 space-y-1">
              {bullets.map((b) => (
                <li key={b} className="flex items-center gap-2 text-xs text-fg">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                  {b}
                </li>
              ))}
            </ul>
            <div className="mt-5 flex items-center gap-2">
              <button
                onClick={connect}
                disabled={phase === "connecting"}
                className="flex flex-1 items-center justify-center gap-2 rounded bg-accent px-3 py-2 text-xs font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                {phase === "connecting" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                {phase === "connecting" ? "Connecting…" : `Connect with ${connector.name.split(" ")[0]}`}
              </button>
              <button onClick={onClose} className="rounded border border-border px-3 py-2 text-xs text-subtle hover:text-fg">
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
