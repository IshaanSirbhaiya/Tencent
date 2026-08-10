import { Badge } from "@/components/ui/badge";
import { bandBg } from "@/lib/utils";
import { bandOf } from "@/lib/types";
import { cn } from "@/lib/utils";

// The single 3-stop confidence scale (D-FE-3). Used identically on cards, graph edges, chips.
const LABEL = { auto: "auto", spot_check: "spot-check", review: "review" } as const;

export function ConfidenceBadge({ confidence, band }: { confidence: number; band?: string }) {
  const b = (band as keyof typeof LABEL) ?? bandOf(confidence);
  return (
    <Badge className={cn("tnum", bandBg(b))}>
      <span className="font-semibold">{LABEL[b]}</span>
      <span className="opacity-70">{confidence.toFixed(2)}</span>
    </Badge>
  );
}

export function ConfidenceBar({ confidence, band }: { confidence: number; band?: string }) {
  const b = (band as "auto" | "spot_check" | "review") ?? bandOf(confidence);
  const color =
    b === "auto" ? "bg-band-auto" : b === "spot_check" ? "bg-band-spot" : "bg-band-review";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${Math.round(confidence * 100)}%` }} />
      </div>
      <span className="tnum w-8 text-2xs text-subtle">{confidence.toFixed(2)}</span>
    </div>
  );
}
