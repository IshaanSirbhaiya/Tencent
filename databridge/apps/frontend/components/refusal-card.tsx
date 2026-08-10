import { ShieldAlert } from "lucide-react";
import type { Refusal } from "@/lib/types";

// Typed Refusal rendered verbatim — code + message, never a stack trace (PRD-08 §5, REVIEW_2 L6).
export function RefusalCard({ refusal }: { refusal: Refusal }) {
  return (
    <div className="flex items-start gap-3 rounded border border-band-review/40 bg-band-review/10 p-3">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
      <div className="min-w-0">
        <div className="font-mono text-xs font-semibold text-band-review">{refusal.code}</div>
        <div className="mt-0.5 text-xs text-fg">{refusal.message}</div>
        {refusal.nearest_alternative && (
          <div className="mt-1 text-2xs text-subtle">
            nearest alternative: {refusal.nearest_alternative}
          </div>
        )}
      </div>
    </div>
  );
}
