"use client";
/**
 * One connected MCP server, expandable to the tools it exposes.
 *
 * The row's whole job is to make the READ / ACT split unmissable at a glance, because that is the
 * only thing on this page a reader can get wrong in a way that matters. So it is stated three
 * times, in three registers: a badge, a plain sentence, and the absence of a tool list.
 *
 * An ACT server has **no tool list here on purpose** — not because we failed to fetch one, but
 * because we never open a session with an act-capable endpoint from the process that also hosts
 * the agent. `tools_known: false` is the backend saying that, and the card says it in words rather
 * than rendering an empty list that reads like "this server exposes nothing".
 *
 * `boundary_violation` — ACT-recorded and yet present in the aggregator — is shown loudly. It can
 * only happen if somebody drove the aggregator by hand, and a surface that hid it would be worse
 * than one that never checked.
 */
import * as React from "react";
import { AlertTriangle, Bot, ChevronDown, ChevronRight, ShieldCheck, Trash2 } from "lucide-react";
import type { McpServer, McpTool } from "@/lib/mcp-api";
import { ToolRunner } from "./tool-runner";
import { cn } from "@/lib/utils";

function ToolRow({ tool }: { tool: McpTool }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="border-t border-border px-4 py-2.5 first:border-t-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-xs text-fg">{tool.tool}</div>
          <div className="mt-0.5 text-2xs text-subtle">
            {tool.description || (
              <span className="text-band-spot">
                no description — a model choosing this tool would have only its name
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!tool.enabled ? (
            <span className="rounded border border-border px-1.5 py-0.5 text-2xs text-subtle">
              disabled
            </span>
          ) : null}
          {tool.invocable ? (
            <button
              onClick={() => setOpen((v) => !v)}
              className="rounded border border-border px-2 py-0.5 text-2xs font-medium text-fg transition-colors hover:border-accent focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {open ? "Hide" : "Try it"}
            </button>
          ) : (
            <span className="text-2xs text-subtle">not invocable here</span>
          )}
        </div>
      </div>

      {open ? <ToolRunner tool={tool} /> : null}

      <details className="mt-1.5">
        <summary className="cursor-pointer text-2xs text-subtle hover:text-fg">
          input schema
        </summary>
        <pre className="mt-1 max-h-48 overflow-auto rounded border border-border bg-bg p-2 font-mono text-2xs text-subtle">
          {JSON.stringify(tool.input_schema, null, 2)}
        </pre>
      </details>
    </div>
  );
}

export function ServerCard({
  server,
  onDisconnect,
  disconnecting,
}: {
  server: McpServer;
  onDisconnect: (name: string) => void;
  disconnecting: boolean;
}) {
  const [open, setOpen] = React.useState(server.kind === "read");
  const isRead = server.kind === "read";
  const Icon = isRead ? Bot : ShieldCheck;
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface",
        server.boundary_violation
          ? "border-band-review"
          : isRead
            ? "border-border"
            : "border-band-spot/40",
      )}
    >
      <div className="flex items-start justify-between gap-3 px-4 py-3">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-start gap-2.5 text-left focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <Chevron className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm font-semibold text-fg">{server.name}</span>
              <span
                className={cn(
                  "flex items-center gap-1 rounded-full border px-2 py-0.5 text-2xs font-medium",
                  isRead
                    ? "border-band-auto/40 bg-band-auto/10 text-band-auto"
                    : "border-band-spot/40 bg-band-spot/10 text-band-spot",
                )}
              >
                <Icon className="h-3 w-3" aria-hidden />
                {isRead ? "READ" : "ACT"}
              </span>
              <span className="text-2xs text-subtle">
                {isRead ? `${server.tools.length} tools` : "dispatcher only"}
              </span>
            </span>
            <span className="mt-0.5 block truncate font-mono text-2xs text-subtle" title={server.url}>
              {server.url}
            </span>
            {server.description ? (
              <span className="mt-0.5 block text-2xs text-subtle">{server.description}</span>
            ) : null}
          </span>
        </button>
        <button
          onClick={() => onDisconnect(server.name)}
          disabled={disconnecting}
          className="flex shrink-0 items-center gap-1.5 rounded border border-border px-2 py-1 text-2xs font-medium text-subtle transition-colors hover:border-band-review hover:text-band-review disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <Trash2 className="h-3 w-3" aria-hidden />
          Disconnect
        </button>
      </div>

      {/* The capability sentence, on every row — the thing a reader must not get wrong. */}
      <div
        className={cn(
          "border-t px-4 py-2 text-2xs",
          isRead
            ? "border-border bg-band-auto/5 text-band-auto"
            : "border-band-spot/30 bg-band-spot/5 text-band-spot",
        )}
      >
        {isRead
          ? "Registered in the aggregator the agent queries — these tools are callable by the agent immediately."
          : "NOT registered in the aggregator. The agent cannot reach this server at all; its tools run only after a human approves a drafted action."}
      </div>

      {server.boundary_violation ? (
        <div className="flex items-start gap-2 border-t border-band-review/40 bg-band-review/10 px-4 py-2">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" aria-hidden />
          <div className="text-2xs text-band-review">
            <span className="font-semibold">Capability boundary violated.</span> This server is
            recorded as ACT, yet a server of the same name is present in the agent&apos;s
            aggregator. Something registered it outside this surface. Deregister it before relying
            on the split.
          </div>
        </div>
      ) : null}

      {open ? (
        <div className="border-t border-border">
          {server.tools_known ? (
            server.tools.length > 0 ? (
              server.tools.map((tool) => <ToolRow key={tool.name} tool={tool} />)
            ) : (
              <p className="px-4 py-3 text-2xs text-subtle">
                The aggregator lists no tools for this server.
              </p>
            )
          ) : (
            <p className="px-4 py-3 text-2xs text-subtle">
              Tools are not listed for an ACT server. We never open a session with an act-capable
              endpoint from this process — that is what keeps the boundary a process boundary
              rather than a label.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
