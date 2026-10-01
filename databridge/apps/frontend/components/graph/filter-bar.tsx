"use client";
/**
 * Facets and controls for `/graph`. Everything a filter dropdown offers comes off
 * `GET /graph/facets` — kinds and sources are never a literal list in this file, so a pack that
 * adds a kind, or an estate build that adds a source, appears here with no code change.
 */
import * as React from "react";
import { Search, X } from "lucide-react";
import type { GraphFacets } from "@/lib/graph-api";
import { MetricTile } from "@/components/metric-tile";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useKindPalette } from "./kind-palette";

export function KindDot({ kind }: { kind: string }) {
  const palette = useKindPalette();
  return (
    <span
      className="inline-block h-2 w-2 rounded-full"
      style={{ background: palette.node(kind).color }}
    />
  );
}

export interface GraphFilters {
  kind: string;
  source: string;
  q: string;
  depth: number;
  limit: number;
  includeClosed: boolean;
}

export function FilterBar({
  facets,
  filters,
  onChange,
  seed,
  onClearSeed,
  onReload,
  reloading,
}: {
  facets: GraphFacets | null;
  filters: GraphFilters;
  onChange: (next: GraphFilters) => void;
  seed: string | null;
  onClearSeed: () => void;
  onReload?: () => void;
  reloading?: boolean;
}) {
  const palette = useKindPalette();
  const set = <K extends keyof GraphFilters>(key: K, value: GraphFilters[K]) =>
    onChange({ ...filters, [key]: value });
  const comps = facets?.components;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <MetricTile label="Nodes" value={facets?.counts.nodes ?? "—"} />
        <MetricTile label="Edges" value={facets?.counts.edges ?? "—"} />
        <MetricTile
          label="Components"
          value={comps ? (comps.computed ? (comps.count ?? "—") : "unknown") : "—"}
        />
        <MetricTile
          label="Largest component"
          value={comps ? (comps.computed ? (comps.largest ?? "—") : "unknown") : "—"}
        />
        <MetricTile label="Isolated nodes" value={comps ? comps.isolated_nodes : "—"} />
        <MetricTile label="Attribute pointers" value={facets?.counts.attribute_sources ?? "—"} />
      </div>

      {facets && (
        <div className="flex flex-wrap items-center gap-2 text-2xs text-subtle">
          <span>
            source: <span className="text-fg">{facets.graph_source ?? "none"}</span>
          </span>
          <span aria-hidden="true">·</span>
          <span>
            reverse-index coverage:{" "}
            <Badge
              className={
                facets.reverse_index_coverage.answer === "known"
                  ? "border-band-auto/40 bg-band-auto/10 text-band-auto"
                  : "border-band-spot/40 bg-band-spot/10 text-band-spot"
              }
            >
              {facets.reverse_index_coverage.answer}
            </Badge>
          </span>
          {facets.reverse_index_coverage.unindexed_pointers > 0 && (
            <span title={facets.reverse_index_coverage.reason}>
              ({facets.reverse_index_coverage.unindexed_pointers} pointer(s) never indexed)
            </span>
          )}
          <span aria-hidden="true">·</span>
          <span>{facets.counts.sources} entity pointer(s)</span>
          {comps && !comps.computed && comps.reason && (
            <span title={comps.reason}>(component stats not computed: over the size cap)</span>
          )}
          {onReload && (
            <button
              type="button"
              onClick={onReload}
              disabled={reloading}
              className="ml-auto rounded border border-border px-2 py-0.5 text-2xs text-subtle hover:border-accent hover:text-accent disabled:opacity-50"
              title="Re-open the shared graph store so rows written by an ingestion run appear"
            >
              {reloading ? "Reloading…" : "Reload store"}
            </button>
          )}
        </div>
      )}

      {seed && (
        <div className="flex items-center gap-2 rounded border border-accent/30 bg-accent/[0.06] px-3 py-1.5 text-xs">
          <span className="text-subtle">expanding from</span>
          <span className="truncate font-mono text-2xs text-fg">{seed}</span>
          <button
            type="button"
            onClick={onClearSeed}
            className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs text-subtle hover:bg-muted hover:text-fg"
          >
            <X className="h-3 w-3" /> clear
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-2xs text-subtle">
          kind
          <select
            value={filters.kind}
            onChange={(e) => set("kind", e.target.value)}
            className="h-9 rounded border border-border bg-surface px-2 text-sm text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          >
            <option value="">all kinds</option>
            {(facets?.node_kinds ?? []).map((k) => (
              <option key={k.kind} value={k.kind}>
                {palette.node(k.kind).label} ({k.count})
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-2xs text-subtle">
          source
          <select
            value={filters.source}
            onChange={(e) => set("source", e.target.value)}
            className="h-9 max-w-[16rem] rounded border border-border bg-surface px-2 text-sm text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          >
            <option value="">all sources</option>
            {(facets?.sources.items ?? []).map((s) => (
              <option key={s.source_id} value={s.source_id}>
                {s.source_id} ({s.pointer_count})
              </option>
            ))}
            {/* A source chosen by link (`/graph?source=…`) may sit outside the facet's top-N; show
                it as itself rather than letting the select silently display "all sources". */}
            {filters.source &&
            !(facets?.sources.items ?? []).some((s) => s.source_id === filters.source) ? (
              <option value={filters.source}>{filters.source}</option>
            ) : null}
          </select>
        </label>

        <label className="flex flex-1 min-w-[10rem] flex-col gap-1 text-2xs text-subtle">
          filter by label
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
            <Input
              value={filters.q}
              onChange={(e) => set("q", e.target.value)}
              placeholder="any part of a node's label"
              className="h-9 pl-7"
            />
          </div>
        </label>

        <label
          className="flex flex-col gap-1 text-2xs text-subtle"
          title="How many hops a seed or an expand reaches"
        >
          expand depth
          <select
              value={filters.depth}
              onChange={(e) => set("depth", Number(e.target.value))}
              className="h-9 rounded border border-border bg-surface px-2 text-sm text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              <option value={1}>1 hop</option>
              <option value={2}>2 hops</option>
              <option value={3}>3 hops</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 text-2xs text-subtle">
          window
          <select
            value={filters.limit}
            onChange={(e) => set("limit", Number(e.target.value))}
            className="h-9 rounded border border-border bg-surface px-2 text-sm text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
          >
            {[30, 60, 100, 150, 250].map((n) => (
              <option key={n} value={n}>
                {n} nodes
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-1.5 pb-2 text-2xs text-subtle">
          <input
            type="checkbox"
            checked={filters.includeClosed}
            onChange={(e) => set("includeClosed", e.target.checked)}
            className="h-3.5 w-3.5 rounded border-border accent-[hsl(var(--accent))]"
          />
          include superseded
        </label>

        {(filters.kind || filters.source || filters.q) && (
          <button
            type="button"
            onClick={() => onChange({ ...filters, kind: "", source: "", q: "" })}
            className="mb-2 flex items-center gap-1 rounded px-2 py-1 text-2xs text-subtle hover:bg-muted hover:text-fg"
          >
            <X className="h-3 w-3" /> clear filters
          </button>
        )}
      </div>
    </div>
  );
}
