"use client";
/**
 * Pick a starting point anywhere in the store — `GET /graph/search` over label, id and canonical
 * id, best-connected first. Choosing a hit RE-SEEDS the canvas from it; "add" MERGES its
 * neighbourhood into what is already drawn (the way to bring two distant regions onto one canvas
 * before asking for the path between them).
 *
 * Distinct from the canvas's own "Find on canvas" box, which only highlights what is already drawn.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import {
  describeGraphError,
  graphApi,
  nodeLabelText,
} from "@/lib/graph-api";
import { isRefusal } from "@/lib/intake-types";
import { Input } from "@/components/ui/input";
import { KindDot } from "./filter-bar";
import { useKindPalette } from "./kind-palette";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function SeedSearch({
  onSeed,
  onAdd,
}: {
  onSeed: (id: string) => void;
  onAdd: (id: string) => void;
}) {
  const palette = useKindPalette();
  const [text, setText] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const q = useDebounced(text.trim(), 250);
  const searchQ = useQuery({
    queryKey: ["graph-search", q],
    queryFn: () => graphApi.search(q, undefined, 12),
    enabled: q.length > 0,
  });
  const data = searchQ.data && !isRefusal(searchQ.data) ? searchQ.data : null;
  const refusal = searchQ.data && isRefusal(searchQ.data) ? searchQ.data.refusal : null;

  const rootRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const pick = (id: string, merge: boolean) => {
    (merge ? onAdd : onSeed)(id);
    setOpen(false);
  };

  return (
    <div ref={rootRef} className="relative w-full max-w-md" data-testid="graph-seed-search">
      <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
      <Input
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && data && data.nodes.length > 0) pick(data.nodes[0].id, false);
        }}
        placeholder="Start from a node — search by name or id"
        aria-label="Search the graph for a starting node"
        className="h-9 pl-7"
      />
      {open && q.length > 0 && (
        <div
          className="absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-y-auto rounded border border-border bg-surface shadow-md"
          role="listbox"
        >
          {searchQ.isLoading && <p className="px-3 py-2 text-2xs text-subtle">Searching…</p>}
          {searchQ.isError && (
            <p className="px-3 py-2 text-2xs text-band-review">
              {describeGraphError(searchQ.error)}
            </p>
          )}
          {refusal && <p className="px-3 py-2 text-2xs text-band-review">{refusal.message}</p>}
          {data && data.nodes.length === 0 && (
            <p className="px-3 py-2 text-2xs text-subtle">No node matches “{q}”.</p>
          )}
          {data?.nodes.map((n) => (
            <div
              key={n.id}
              role="option"
              aria-selected={false}
              className="flex items-center gap-2 border-b border-border/60 px-3 py-1.5 last:border-0 hover:bg-muted"
            >
              <button
                type="button"
                onClick={() => pick(n.id, false)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                title={`Start from ${n.id}`}
              >
                <KindDot kind={n.kind} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-fg">{nodeLabelText(n.label)}</span>
                  <span className="block truncate font-mono text-2xs text-subtle">
                    {palette.node(n.kind).label} · {n.id}
                  </span>
                </span>
                <span className="tnum shrink-0 text-2xs text-subtle">{n.degree} links</span>
              </button>
              <button
                type="button"
                onClick={() => pick(n.id, true)}
                className="shrink-0 rounded p-1 text-subtle hover:bg-surface hover:text-accent"
                aria-label={`Add ${nodeLabelText(n.label)} to the canvas`}
                title="Add to the canvas without clearing it"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          {data?.truncated && (
            <p className="px-3 py-1.5 text-2xs text-subtle">
              Showing {data.returned} of {data.matched_total} — type more to narrow.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
