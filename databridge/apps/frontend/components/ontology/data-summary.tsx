"use client";
/**
 * What the data says about this vocabulary: how much of the graph uses it, which kinds have no data
 * yet, and what ingestion REFUSED because it did not fit — the owner's signal for what to define
 * next. Every number comes from `GET /ontology`'s `data` block (the shared store and the latest
 * ingestion run per source); when either is missing, this says so instead of showing zeros.
 */
import * as React from "react";
import { Plus } from "lucide-react";
import type { OntologyData } from "@/lib/ontology-api";

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="min-w-[110px] rounded border border-border bg-surface px-3 py-2" title={hint}>
      <div className="text-2xs uppercase tracking-wider text-subtle">{label}</div>
      <div className="mt-0.5 font-mono text-sm text-fg">{value}</div>
    </div>
  );
}

export function DataSummary({
  data,
  onDefineNode,
  onDefineEdge,
}: {
  data: OntologyData;
  onDefineNode: (name: string) => void;
  onDefineEdge: (name: string) => void;
}) {
  const unknownNodes = Object.entries(data.ingest.unknown_node_kinds).sort((a, b) => b[1] - a[1]);
  const unknownEdges = Object.entries(data.ingest.unknown_edge_kinds).sort((a, b) => b[1] - a[1]);
  const endpointRefusals = Object.values(data.ingest.edge_endpoint_refusals).reduce((a, b) => a + b, 0);

  return (
    <section className="flex flex-col gap-3" data-testid="ontology-data-summary">
      <div className="flex flex-wrap gap-2">
        <Stat
          label="nodes in graph"
          value={data.store_available ? data.nodes_total : "—"}
          hint={data.store ?? "no graph store attached"}
        />
        <Stat label="edges in graph" value={data.store_available ? data.edges_total : "—"} />
        <Stat
          label="kinds with no data"
          value={data.store_available ? data.kinds_without_data.length : "—"}
          hint={data.kinds_without_data.join(", ")}
        />
        <Stat
          label="refused (off-ontology)"
          value={data.ingest.available ? data.ingest.total : "—"}
          hint={data.ingest.runs.length ? `latest runs: ${data.ingest.runs.join(", ")}` : undefined}
        />
      </div>

      {!data.store_available ? (
        <p className="text-2xs text-subtle">
          No graph store is attached to this gateway, so per-kind counts are unavailable — not zero.
        </p>
      ) : null}
      {!data.ingest.available ? (
        <p className="text-2xs text-subtle">
          No ingestion runs yet. After the first run, anything extraction refused because it was not
          in this ontology is counted here.
        </p>
      ) : null}

      {unknownNodes.length || unknownEdges.length ? (
        <div className="rounded border border-band-spot/40 bg-band-spot/[0.05] p-3">
          <p className="mb-2 text-2xs leading-relaxed text-fg">
            Ingestion saw things this ontology has no kind for, and refused them. Define one to let
            the next run map it.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {unknownNodes.map(([name, n]) => (
              <button
                key={`n-${name}`}
                type="button"
                onClick={() => onDefineNode(name)}
                className="inline-flex items-center gap-1 rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-2xs text-fg hover:border-accent"
              >
                <Plus className="h-3 w-3" /> {name} <span className="text-subtle">entity · {n}×</span>
              </button>
            ))}
            {unknownEdges.map(([name, n]) => (
              <button
                key={`e-${name}`}
                type="button"
                onClick={() => onDefineEdge(name)}
                className="inline-flex items-center gap-1 rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-2xs text-fg hover:border-accent"
              >
                <Plus className="h-3 w-3" /> {name} <span className="text-subtle">relation · {n}×</span>
              </button>
            ))}
          </div>
          {endpointRefusals ? (
            <p className="mt-2 text-2xs text-subtle">
              {endpointRefusals} relation{endpointRefusals === 1 ? " was" : "s were"} refused because
              the endpoints did not fit — see the per-relation counts below.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
