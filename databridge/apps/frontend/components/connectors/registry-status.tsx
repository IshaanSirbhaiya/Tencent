"use client";
/**
 * The two processes this page depends on, stated plainly.
 *
 * `MCP_RUNBOOK.md` §1: the aggregator (MCPJungle) and our read-only clinic server are separate
 * processes, and either can be down without the other noticing. The aggregator caches its tool
 * list in its own database, so "four tools are registered" stays true after the reader dies — which
 * is exactly why the reader gets its own probe (a real MCP `initialize`) rather than being inferred
 * from the registry listing.
 *
 * A down process shows its URL and the reason. "Not reachable" with no address is an alert nobody
 * can act on.
 */
import { CircleCheck, CircleX, RefreshCw } from "lucide-react";
import type { StatusResponse } from "@/lib/mcp-api";
import { cn } from "@/lib/utils";

function Light({ up }: { up: boolean }) {
  return up ? (
    <CircleCheck className="h-3.5 w-3.5 shrink-0 text-band-auto" aria-hidden />
  ) : (
    <CircleX className="h-3.5 w-3.5 shrink-0 text-band-review" aria-hidden />
  );
}

function Row({
  label,
  url,
  up,
  detail,
  note,
}: {
  label: string;
  url: string;
  up: boolean;
  detail: string | null;
  note: string;
}) {
  return (
    <div className="flex min-w-0 flex-1 items-start gap-2.5 px-4 py-3">
      <Light up={up} />
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-fg">{label}</span>
          <span className={cn("text-2xs font-medium", up ? "text-band-auto" : "text-band-review")}>
            {up ? "reachable" : "not reachable"}
          </span>
        </div>
        <div className="mt-0.5 truncate font-mono text-2xs text-subtle" title={url}>
          {url}
        </div>
        <div className="mt-0.5 text-2xs text-subtle">{up ? note : (detail ?? "no response")}</div>
      </div>
    </div>
  );
}

export function RegistryStatus({
  status,
  onRefresh,
  refreshing,
}: {
  status: StatusResponse;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const readerName = status.reader.server_info?.name;
  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="text-2xs font-medium uppercase tracking-wide text-subtle">
          Infrastructure
        </span>
        <button
          onClick={onRefresh}
          className="flex items-center gap-1.5 rounded px-2 py-1 text-2xs text-subtle transition-colors hover:bg-muted hover:text-fg focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} aria-hidden />
          Re-check
        </button>
      </div>
      <div className="flex flex-col divide-y divide-border sm:flex-row sm:divide-x sm:divide-y-0">
        <Row
          label="MCP aggregator"
          url={status.registry.url}
          up={status.registry.reachable}
          detail={status.registry.detail}
          note={`status: ${status.registry.status ?? "ok"} · the agent's whole callable surface`}
        />
        <Row
          label="Clinic read server"
          url={status.reader.url}
          up={status.reader.reachable}
          detail={status.reader.detail}
          note={readerName ? `${readerName} · answered an MCP initialize` : "answered an MCP initialize"}
        />
      </div>
    </div>
  );
}
