"use client";
/**
 * The ingestion-route pill. Deliberately three-toned, not two:
 *
 *   - a NAMED route (upload / email_forward / walk_in / a connector) reads as settled fact,
 *   - "config" reads as neutral — it says plainly that this pointer cites static app
 *     configuration (`coverage_rules.yaml`), never a patient document, so it must not be
 *     confused with an ingestion channel,
 *   - "unknown" is loud (the same review tone the rest of the app uses for "needs a human"),
 *     because CLAUDE.md is explicit: an unknown route is never quietly defaulted to "upload".
 */
import { cn } from "@/lib/utils";

const KNOWN_CHANNELS = new Set(["upload", "email_forward", "walk_in"]);

export function RoutePill({ route }: { route: string }) {
  const tone =
    route === "unknown"
      ? "bg-band-review/15 text-band-review"
      : route === "config"
        ? "bg-muted text-subtle"
        : KNOWN_CHANNELS.has(route)
          ? "bg-band-auto/15 text-band-auto"
          : "bg-accent/15 text-accent"; // a named MCP/connector route not in the base three
  return (
    <span
      data-testid="ingestion-route"
      className={cn("rounded px-1.5 py-0.5 text-2xs font-medium", tone)}
    >
      {route.replace(/_/g, " ")}
    </span>
  );
}
