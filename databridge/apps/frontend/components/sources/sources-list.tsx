"use client";
/**
 * Every registered source, one row each: title, route, tools, plan status, last run, health.
 *
 * Tools come from `/platform/catalogue` (the backend's own tool list per connection); a source the
 * catalogue does not list shows "—" rather than a guessed number. Health is `/sources/{id}/health`
 * per row — the gateway's verdict, not recomputed here.
 */
import * as React from "react";
import { useQueries } from "@tanstack/react-query";
import { sourcesApi, unwrap, type CatalogueConnection, type SourceSummary } from "@/lib/sources-api";
import { cn } from "@/lib/utils";
import { StatusBadge, ago, n } from "./common";

export function routeLabel(via: string, transport: string | null): string {
  if (via === "mcpjungle") return "MCPJungle";
  if (transport === "stdio") return "Direct · command";
  if (transport === "http") return "Direct · URL";
  return "Direct";
}

export function SourcesList({
  sources,
  catalogue,
  selected,
  onSelect,
}: {
  sources: SourceSummary[];
  catalogue: Map<string, CatalogueConnection> | null;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const health = useQueries({
    queries: sources.map((s) => ({
      queryKey: ["source-health", s.id],
      queryFn: () => unwrap(sourcesApi.health(s.id)),
      refetchInterval: s.running ? 3000 : 30_000,
      retry: false,
    })),
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs" data-testid="sources-table">
        <thead className="border-b border-border text-2xs uppercase tracking-wider text-subtle">
          <tr>
            <th className="px-3 py-2 font-medium">Source</th>
            <th className="px-3 py-2 font-medium">Route</th>
            <th className="px-3 py-2 text-right font-medium">Tools</th>
            <th className="px-3 py-2 font-medium">Plan</th>
            <th className="px-3 py-2 font-medium">Last run</th>
            <th className="px-3 py-2 font-medium">Health</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s, i) => {
            const cat = catalogue?.get(s.id);
            const h = health[i];
            const lastSuccess = h?.data?.last_success?.finished_at ?? null;
            return (
              <tr
                key={s.id}
                data-testid={`source-row-${s.id}`}
                onClick={() => onSelect(s.id)}
                className={cn(
                  "cursor-pointer border-b border-border last:border-0 hover:bg-muted",
                  selected === s.id && "bg-muted",
                )}
              >
                <td className="px-3 py-2">
                  <div className="font-medium text-fg">{s.title}</div>
                  <div className="font-mono text-2xs text-subtle">{s.id}</div>
                </td>
                <td className="px-3 py-2 text-subtle" data-testid={`source-route-${s.id}`}>
                  {routeLabel(s.via, s.transport)}
                </td>
                <td className="tnum px-3 py-2 text-right" data-testid={`source-tools-${s.id}`}>
                  {cat ? cat.tools.length : "—"}
                </td>
                <td className="px-3 py-2">
                  <StatusBadge status={s.plan_status ?? "none"} testId={`source-plan-${s.id}`} />
                </td>
                <td className="px-3 py-2" data-testid={`source-last-run-${s.id}`}>
                  {s.running ? (
                    <StatusBadge status="running" />
                  ) : s.last_run ? (
                    <div className="space-y-0.5">
                      <StatusBadge status={s.last_run.status} />
                      <div className="tnum text-2xs text-subtle">
                        {n(s.last_run.counts?.inventory)} seen · {n(s.last_run.counts?.built)} built
                      </div>
                    </div>
                  ) : (
                    <span className="text-subtle">never run</span>
                  )}
                </td>
                <td className="px-3 py-2" data-testid={`source-health-${s.id}`}>
                  {h?.data ? (
                    <div className="space-y-0.5">
                      <StatusBadge status={h.data.overall} />
                      <div className="text-2xs text-subtle">
                        {lastSuccess ? `synced ${ago(lastSuccess)}` : "no successful sync"}
                      </div>
                    </div>
                  ) : h?.isError ? (
                    <span className="text-2xs text-band-review">unavailable</span>
                  ) : (
                    <span className="text-2xs text-subtle">…</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
