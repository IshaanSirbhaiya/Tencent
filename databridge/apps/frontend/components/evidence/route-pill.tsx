"use client";
/**
 * The ingestion-route pill. Four tones, because the routes are four different facts:
 *
 *   - an INTAKE channel (upload / email_forward / walk_in) reads as settled fact,
 *   - "ingest" — the bytes were captured by an ingestion run — reads as settled fact too, in the
 *     accent tone, because the capture store can re-verify it,
 *   - "table" reads as neutral — a cell of a structured source, addressed by table/column/row,
 *   - "unknown" is loud (the review tone the app uses for "needs a human"): an unknown route is
 *     never quietly defaulted to one of the others.
 */
import { cn } from "@/lib/utils";

const INTAKE_CHANNELS = new Set(["upload", "email_forward", "walk_in"]);

export function RoutePill({ route }: { route: string }) {
  const tone =
    route === "unknown"
      ? "bg-band-review/15 text-band-review"
      : route === "table"
        ? "bg-muted text-subtle"
        : INTAKE_CHANNELS.has(route)
          ? "bg-band-auto/15 text-band-auto"
          : "bg-accent/15 text-accent";
  return (
    <span
      data-testid="ingestion-route"
      className={cn("rounded px-1.5 py-0.5 text-2xs font-medium", tone)}
    >
      {route.replace(/_/g, " ")}
    </span>
  );
}
