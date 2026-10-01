"use client";
/**
 * Step 1 — search and pick a server.
 *
 * The founder's own framing: "like they just search for the MCP the way you can in Claude Desktop
 * or ChatGPT." The difference from those registries is the point of the whole surface: this list
 * is vendored and pinned, never fetched at runtime, so an entry with no reviewed artefact says so
 * instead of pretending to be configurable — `not_yet_vendored` is a real, selectable search
 * result, not a hidden row.
 */
import * as React from "react";
import { Check, PackageSearch, ShieldAlert } from "lucide-react";
import type { CatalogueEntry } from "@/lib/connector-api";
import { cn } from "@/lib/utils";

function StatusBadge({ entry }: { entry: CatalogueEntry }) {
  if (entry.status === "vendored") {
    return (
      <span className="flex items-center gap-1 rounded-full border border-band-auto/40 bg-band-auto/10 px-2 py-0.5 text-2xs font-medium text-band-auto">
        <Check className="h-3 w-3" aria-hidden />
        vendored &amp; pinned
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-2xs font-medium text-subtle">
      <ShieldAlert className="h-3 w-3" aria-hidden />
      not yet vendored
    </span>
  );
}

function ServerRow({
  entry,
  selected,
  onSelect,
}: {
  entry: CatalogueEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  const available = entry.status === "vendored";
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid={`catalogue-entry-${entry.id}`}
      className={cn(
        "flex w-full flex-col gap-1.5 rounded border px-3.5 py-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-accent",
        selected
          ? "border-accent bg-accent/5"
          : "border-border bg-bg hover:border-accent/40",
      )}
    >
      <span className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-fg">{entry.name}</span>
        <span className="text-2xs text-subtle">{entry.vendor}</span>
        <span className="ml-auto"><StatusBadge entry={entry} /></span>
      </span>
      <span className="text-2xs leading-relaxed text-subtle">{entry.summary}</span>
      <span className="flex flex-wrap gap-1.5">
        {entry.reads.slice(0, 6).map((field) => (
          <span
            key={field}
            className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-2xs text-subtle"
          >
            {field}
          </span>
        ))}
        {entry.reads.length > 6 ? (
          <span className="text-2xs text-subtle">+{entry.reads.length - 6} more</span>
        ) : null}
      </span>
      {!available ? (
        <span className="mt-0.5 text-2xs leading-relaxed text-subtle">
          {entry.not_vendored_reason}
        </span>
      ) : null}
    </button>
  );
}

export function CatalogueSearch({
  entries,
  loading,
  query,
  onQueryChange,
  selectedId,
  onSelect,
}: {
  entries: CatalogueEntry[];
  loading: boolean;
  query: string;
  onQueryChange: (q: string) => void;
  selectedId: string | null;
  onSelect: (entry: CatalogueEntry) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <label className="relative block">
        <PackageSearch
          className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle"
          aria-hidden
        />
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Search the vendored catalogue — try “permits”, “slack”, “drive”…"
          data-testid="catalogue-search-input"
          className="w-full rounded border border-border bg-bg py-2 pl-9 pr-3 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </label>

      <div className="flex flex-col gap-2" data-testid="catalogue-results">
        {loading ? (
          <p className="rounded border border-border bg-surface px-3.5 py-6 text-center text-2xs text-subtle">
            Loading the catalogue…
          </p>
        ) : entries.length === 0 ? (
          <p className="rounded border border-border bg-surface px-3.5 py-6 text-center text-2xs text-subtle">
            Nothing in the catalogue matches “{query}”.
          </p>
        ) : (
          entries.map((entry) => (
            <ServerRow
              key={entry.id}
              entry={entry}
              selected={entry.id === selectedId}
              onSelect={() => onSelect(entry)}
            />
          ))
        )}
      </div>

      <p className="text-2xs leading-relaxed text-subtle">
        Every server here is vendored into this repository and digest-pinned — nothing is fetched
        at runtime. A server fetched on demand is a server nobody reviewed, and 41.6% of audited
        public MCP servers vanish within three days.
      </p>
    </div>
  );
}
