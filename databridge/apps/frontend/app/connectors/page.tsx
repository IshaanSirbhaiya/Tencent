"use client";
/**
 * `/connectors` — the MCP management surface (T-44).
 *
 * This page used to be twelve logos and a modal that resolved after 1.1 seconds. Everything on it
 * now comes from a live MCPJungle aggregator via `GET /mcp/*`, and connecting a server here
 * genuinely changes what the agent can call — which is the only version of this screen worth
 * showing anyone.
 *
 * **The distinction the whole page is built around** (`docs/hackathon/AGENT_CAPABILITIES.md`):
 * a READ server joins the aggregator the agent queries; an ACT server is recorded for the
 * model-free dispatcher and is never registered there at all. That is not a flag on a row — the
 * two live in different registries, and the page shows both so an operator can see what the agent
 * can reach and what it deliberately cannot.
 *
 * **A down aggregator is a refusal, not an empty list.** `/mcp/servers` answers 503 with a typed
 * `REGISTRY_UNREACHABLE`, and this page renders it verbatim. "Nothing is connected" and "I cannot
 * see the registry" look nothing alike here, because conflating them tells an operator the system
 * is fine while it is down.
 *
 * No figure on this page is computed in the browser (D-FE-2 / design law): counts, capability
 * labels and the consequence text beside the READ/ACT choice all arrive from the gateway.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plug } from "lucide-react";
import { mcpApi, type ConnectRequest, type ServersResponse, type StatusResponse } from "@/lib/mcp-api";
import type { Refusal } from "@/lib/types";
import { RefusalCard } from "@/components/refusal-card";
import { ConnectForm } from "@/components/connectors/connect-form";
import { RegistryStatus } from "@/components/connectors/registry-status";
import { ServerCard } from "@/components/connectors/server-card";

/** The consequence text is normally sent by the gateway. These stand in only while the very first
 *  `/mcp/servers` request is still in flight or has refused, so the form can still explain itself. */
const FALLBACK_CONSEQUENCE = {
  read: "READ tools are available to the agent immediately.",
  act: "ACT tools can only run after a human approves a drafted action.",
} as const;

export default function ConnectorsPage() {
  const queryClient = useQueryClient();
  const [connectRefusal, setConnectRefusal] = React.useState<Refusal | null>(null);
  const [actionRefusal, setActionRefusal] = React.useState<Refusal | null>(null);

  const statusQ = useQuery({
    queryKey: ["mcp-status"],
    queryFn: mcpApi.status,
    refetchInterval: 15_000,
  });
  const serversQ = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: mcpApi.servers,
    refetchInterval: 15_000,
  });

  function refetchAll() {
    void statusQ.refetch();
    void serversQ.refetch();
  }

  const connect = useMutation({
    mutationFn: (body: ConnectRequest) => mcpApi.connect(body),
    onSuccess: (res) => {
      setConnectRefusal(res.ok ? null : res.refusal);
      if (res.ok) void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    },
  });

  const disconnect = useMutation({
    mutationFn: (name: string) => mcpApi.disconnect(name),
    onSuccess: (res) => {
      setActionRefusal(res.ok ? null : res.refusal);
      if (res.ok) void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    },
  });

  const status: StatusResponse | null =
    statusQ.data && statusQ.data.ok ? statusQ.data.data : null;
  const servers: ServersResponse | null =
    serversQ.data && serversQ.data.ok ? serversQ.data.data : null;
  const serversRefusal: Refusal | null =
    serversQ.data && !serversQ.data.ok ? serversQ.data.refusal : null;

  const consequence = servers?.kind_consequence ?? FALLBACK_CONSEQUENCE;
  const registryDown = serversRefusal !== null || status?.registry.reachable === false;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-surface px-5">
        <h1 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <Plug className="h-4 w-4 text-subtle" aria-hidden />
          Connectors
        </h1>
        {servers ? (
          <span className="text-2xs text-subtle">
            <span className="tnum font-medium text-band-auto">{servers.counts.read}</span> read
            <span className="mx-1.5">·</span>
            <span className="tnum font-medium text-band-spot">{servers.counts.act}</span> act
            <span className="mx-1.5">·</span>
            <span className="tnum text-fg">{servers.counts.tools}</span> tools reachable by the
            agent
          </span>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-5">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-fg">
              What the agent can reach
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-subtle">
              Servers registered here join a self-hosted MCP aggregator, so nothing leaves this
              environment. A server is declared READ or ACT when it is connected, and that choice
              decides which process fronts it — the agent queries the aggregator and cannot reach an
              ACT server at all.
            </p>
          </div>

          {statusQ.data && !statusQ.data.ok ? (
            <RefusalCard refusal={statusQ.data.refusal} />
          ) : null}
          {status ? (
            <RegistryStatus
              status={status}
              onRefresh={refetchAll}
              refreshing={statusQ.isFetching || serversQ.isFetching}
            />
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-4 py-3 text-2xs text-subtle">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Checking the aggregator and the clinic read server…
            </div>
          )}

          {/* The refusal, verbatim. Never a silently empty list. */}
          {serversRefusal ? (
            <div className="space-y-2">
              <RefusalCard refusal={serversRefusal} />
              <p className="text-2xs text-subtle">
                Nothing is listed above because the registry could not be read — not because nothing
                is connected. Those are different facts and this page will not merge them.
              </p>
            </div>
          ) : null}

          {actionRefusal ? <RefusalCard refusal={actionRefusal} /> : null}

          {servers ? (
            <section className="space-y-3">
              <h3 className="text-2xs font-medium uppercase tracking-wide text-subtle">
                Connected servers
              </h3>
              {servers.servers.length === 0 ? (
                <p className="rounded-lg border border-border bg-surface px-4 py-6 text-center text-xs text-subtle">
                  The aggregator is reachable and has no servers registered. Connect one below.
                </p>
              ) : (
                servers.servers.map((server) => (
                  <ServerCard
                    key={`${server.kind}:${server.name}`}
                    server={server}
                    onDisconnect={(name) => {
                      setActionRefusal(null);
                      disconnect.mutate(name);
                    }}
                    disconnecting={disconnect.isPending && disconnect.variables === server.name}
                  />
                ))
              )}
            </section>
          ) : null}

          <ConnectForm
            consequence={consequence}
            busy={connect.isPending}
            refusal={connectRefusal}
            disabled={registryDown}
            onConnect={(body) => {
              setConnectRefusal(null);
              connect.mutate(body);
            }}
          />

          <p className="pb-4 text-2xs text-subtle">
            Synthetic data only. The aggregator, the clinic read server and the mail sink all run
            locally; no patient data leaves this environment.
          </p>
        </div>
      </div>
    </div>
  );
}
