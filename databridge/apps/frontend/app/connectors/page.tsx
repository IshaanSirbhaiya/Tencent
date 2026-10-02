"use client";
/**
 * `/connectors` — Sources: connect an MCP server, approve its crawl plan, run it, watch it.
 *
 * This page used to redirect to `/builder?tab=adapter`. That tab configures an MCP declaration for
 * an AGENT's tool use; it never registered a source for ingestion, so "Go to connectors" from an
 * empty graph led nowhere that could fill the graph. This page is now the one place a source is
 * connected (`/sources/*` on the gateway): the sources list, "Add source", the plan approval, the
 * live run and each source's run history. The route keeps its name so existing links land here.
 *
 * Above the list: "Propose plans for all", "Approve all pending plans" (one review dialog, then one
 * batch through the per-plan approval) and "Run all approved sources" (`components/sources/
 * batch-actions.tsx`, FEAT8).
 *
 * URL state: `?source=<id>` selects a source, `&run=<run_id>` opens a run, `?add=1` opens the form.
 */
import * as React from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Plus } from "lucide-react";
import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { sourcesApi, unwrap, failureOf, type CatalogueConnection } from "@/lib/sources-api";
import { FailureNotice, Section } from "@/components/sources/common";
import { SourcesList } from "@/components/sources/sources-list";
import { AddSourceForm } from "@/components/sources/add-source-form";
import { SourceDetailView } from "@/components/sources/source-detail";
import { BatchActions } from "@/components/sources/batch-actions";

function SourcesPage() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const selected = params.get("source");
  const runId = params.get("run");
  const adding = params.get("add") === "1";

  const setParams = React.useCallback(
    (next: { source?: string | null; run?: string | null; add?: boolean }) => {
      const p = new URLSearchParams();
      if (next.add) p.set("add", "1");
      if (next.source) p.set("source", next.source);
      if (next.run) p.set("run", next.run);
      const qs = p.toString();
      router.replace(qs ? `/connectors?${qs}` : "/connectors", { scroll: false });
    },
    [router],
  );

  const listQ = useQuery({
    queryKey: ["sources"],
    queryFn: () => unwrap(sourcesApi.list()),
    retry: false,
    refetchInterval: (q) => (q.state.data?.sources.some((s) => s.running) ? 3000 : 30_000),
  });
  const catalogueQ = useQuery({
    queryKey: ["platform-catalogue"],
    queryFn: () => unwrap(sourcesApi.catalogue()),
    retry: false,
  });
  const catalogue = React.useMemo(() => {
    if (!catalogueQ.data) return null;
    return new Map<string, CatalogueConnection>(catalogueQ.data.connections.map((c) => [c.id, c]));
  }, [catalogueQ.data]);

  const sources = listQ.data?.sources ?? [];
  const listFailure = listQ.isError ? failureOf(listQ.error) : null;

  return (
    <>
      <Topbar title="Sources" />
      <div className="mx-auto max-w-7xl space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-2xl">
            <h2 className="text-base font-semibold tracking-tight">Connect a source</h2>
            <p className="text-xs text-subtle">
              Register an MCP server, review the tools it offers and the crawl plan Walnut proposes, approve it, then
              run it into the knowledge graph. Every item the run sees is accounted for: captured, excluded, blocked or
              failed, each with its rule.
            </p>
          </div>
          <Button data-testid="add-source" onClick={() => setParams({ add: true, source: null })}>
            <Plus className="h-4 w-4" /> Add source
          </Button>
        </div>

        {/* Every source at once (FEAT8). Approving all opens one review first; each source's own
            approval and run stay on its page below, unchanged. */}
        {sources.length > 0 && <BatchActions />}

        <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <Section
            testId="sources-list"
            title="Sources"
            description={listQ.data ? `${sources.length} registered` : undefined}
          >
            {listQ.isLoading ? (
              <p className="text-xs text-subtle">Loading sources…</p>
            ) : listFailure ? (
              <FailureNotice failure={listFailure} testId="sources-list-failure" />
            ) : sources.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center" data-testid="sources-empty">
                <Plug className="h-5 w-5 text-subtle" />
                <div className="text-sm font-medium">No sources connected</div>
                <p className="max-w-xs text-xs text-subtle">
                  Add an MCP server by command, by URL, through MCPJungle, or import the servers this deployment declares.
                </p>
              </div>
            ) : (
              <SourcesList
                sources={sources}
                catalogue={catalogue}
                selected={selected}
                onSelect={(id) => setParams({ source: id })}
              />
            )}
            {listQ.data && listQ.data.problems.length > 0 && (
              <ul className="mt-3 list-disc space-y-0.5 pl-5 text-2xs text-band-review" data-testid="sources-problems">
                {listQ.data.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            )}
            {catalogueQ.isError && failureOf(catalogueQ.error) && (
              <p className="mt-3 text-2xs text-subtle" data-testid="catalogue-unavailable">
                Tool counts unavailable: {failureOf(catalogueQ.error)!.plain}
              </p>
            )}
          </Section>

          <div className="min-w-0">
            {adding ? (
              <Section testId="add-source-panel" title="Add a source" description="Choose how Walnut reaches the server.">
                <AddSourceForm
                  onCancel={() => setParams({ source: selected })}
                  onRegistered={(registered) => {
                    qc.invalidateQueries({ queryKey: ["sources"] });
                    qc.invalidateQueries({ queryKey: ["platform-catalogue"] });
                    setParams({ source: registered[0]?.id ?? null });
                  }}
                />
              </Section>
            ) : selected ? (
              <SourceDetailView
                key={selected}
                sourceId={selected}
                catalogue={catalogue?.get(selected) ?? null}
                runId={runId}
                onOpenRun={(r) => setParams({ source: selected, run: r })}
              />
            ) : (
              <div className="rounded border border-dashed border-border p-8 text-center text-xs text-subtle">
                Select a source to see its tools, plan and runs — or add one.
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

export default function ConnectorsPage() {
  return (
    <Suspense fallback={null}>
      <SourcesPage />
    </Suspense>
  );
}
