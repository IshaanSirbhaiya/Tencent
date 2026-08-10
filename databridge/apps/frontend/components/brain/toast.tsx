"use client";
import * as React from "react";
import { CheckCircle2, X } from "lucide-react";

// Minimal success toast (no toast lib). Auto-dismisses; token-styled.
export function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  React.useEffect(() => {
    const t = setTimeout(onClose, 4500);
    return () => clearTimeout(t);
  }, [onClose]);
  return (
    <div
      role="status"
      className="fixed bottom-5 right-5 z-50 flex max-w-sm items-start gap-2.5 rounded border border-band-auto/40 bg-elevated px-4 py-3 shadow-lg"
      style={{ animation: "count-in 180ms ease-out" }}
    >
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-band-auto" />
      <span className="flex-1 text-xs leading-relaxed text-fg">{message}</span>
      <button onClick={onClose} aria-label="Dismiss" className="text-subtle hover:text-fg">
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
