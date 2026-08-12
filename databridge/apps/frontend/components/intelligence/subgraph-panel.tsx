"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  intelligenceApi,
  kindOf,
  labelOf,
  type GraphAnalytics,
  type SubgraphResponse,
} from "@/lib/intelligence-api";

/**
 * The traversal behind a figure — the reason this page is not a dashboard.
 *
 * A number here is only worth as much as the walk that produced it, so every figure hands its
 * `members` (the exact ids it counted) to this panel, and each member expands into the subgraph
 * around it: the nodes, the typed edges, and the raw `SourcePointer` grounding every node. The
 * pointers are shown as JSON rather than summarised, because "showcase the pointer" is literal —
 * a reader should be able to check the claim, not be told it was checked.
 *
 * Read-only in every sense: it calls the same traversal the `/brain` canvas uses and writes
 * nothing.
 */
export function SubgraphPanel({
  graph,
  title,
  members,
  asOf,
}: {
  graph: GraphAnalytics | null;
  title: string | null;
  members: string[];
  /** The window the figures and the answer were read in. Passed down so a drill-down cannot show
   *  the present graph under a rewound heading. */
  asOf: string;
}) {
  const [open, setOpen] = React.useState<string | null>(null);

  // Reset the expansion whenever the selected figure changes — an open subgraph from the previous
  // figure sitting under a new heading is a caption lying about its picture. Keyed on the joined
  // ids, not the array: `members` is a fresh array on every parent render.
  const memberKey = members.join("|");
  React.useEffect(() => {
    setOpen(memberKey ? memberKey.split("|")[0] : null);
  }, [title, memberKey]);

  if (!title) {
    return (
      <aside className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-2xs text-subtle">
        Pick any figure on the left. Its members appear here, and each one expands into the
        subgraph the number was counted from.
      </aside>
    );
  }

  return (
    <aside className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-4">
      <header>
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="text-2xs text-subtle">
          {members.length} {members.length === 1 ? "entity" : "entities"}, each shown with the
          edges and source pointers behind it.
        </p>
      </header>
      <ul className="flex flex-col gap-1" data-testid="subgraph-members">
        {members.map((id) => (
          <li key={id}>
            <button
              type="button"
              aria-expanded={open === id}
              onClick={() => setOpen(open === id ? null : id)}
              className="flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-xs hover:bg-muted/40"
            >
              <ChevronRight
                className={cn("h-3.5 w-3.5 shrink-0 text-subtle transition-transform",
                  open === id && "rotate-90")}
              />
              <span className="min-w-0 flex-1 truncate">{labelOf(graph, id)}</span>
              <span className="shrink-0 text-2xs text-subtle">{kindOf(graph, id) ?? "entity"}</span>
            </button>
            {open === id && <Traversal entityId={id} graph={graph} asOf={asOf} />}
          </li>
        ))}
      </ul>
    </aside>
  );
}

function Traversal({
  entityId,
  graph,
  asOf,
}: {
  entityId: string;
  graph: GraphAnalytics | null;
  asOf: string;
}) {
  const q = useQuery({
    // `asOf` is in the key as well as in the call: a cached traversal from the live window
    // rendered under a rewound heading is the same lie as not passing it at all.
    queryKey: ["intelligence-subgraph", entityId, asOf],
    queryFn: () => intelligenceApi.subgraph(entityId, 1, asOf || null),
  });

  if (q.isError) {
    return (
      <p className="px-2 pb-2 text-2xs text-band-review" role="alert">
        Could not load the subgraph: {(q.error as Error).message}
      </p>
    );
  }
  if (!q.data) return <p className="px-2 pb-2 text-2xs text-subtle">Walking the graph…</p>;
  const sub = q.data as SubgraphResponse;
  if (sub.refusal) {
    return (
      <p className="px-2 pb-2 text-2xs text-subtle">
        {sub.refusal.code}: {sub.refusal.message}
      </p>
    );
  }

  const label = (id: string) =>
    sub.nodes.find((n) => n.id === id)?.label ?? labelOf(graph, id);

  return (
    <div className="mb-2 ml-5 flex flex-col gap-2 border-l border-border pl-3">
      <div>
        <div className="text-2xs uppercase tracking-wide text-subtle">
          {sub.edges.length} relationship{sub.edges.length === 1 ? "" : "s"} at depth {sub.depth}
        </div>
        <ul className="mt-1 flex flex-col gap-0.5">
          {sub.edges.map((e) => (
            <li key={e.id} className="text-2xs">
              <span className="text-fg">{label(e.source)}</span>
              <span className="mx-1 font-mono text-accent">{e.kind}</span>
              <span className="text-fg">{label(e.target)}</span>
              <span className="ml-1 text-subtle">
                {/* valid_from is the date the RELATIONSHIP became true — after T-30 it can no
                    longer precede either endpoint, which is what makes an `as of` figure
                    defensible rather than decorative. */}
                valid from {e.valid_from?.slice(0, 10) ?? "unknown"} · {e.n_sources} pointer
                {e.n_sources === 1 ? "" : "s"}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <div className="text-2xs uppercase tracking-wide text-subtle">
          source pointers ({sub.nodes.length} node{sub.nodes.length === 1 ? "" : "s"})
        </div>
        <ul className="mt-1 flex flex-col gap-1">
          {sub.nodes.map((n) => (
            <li key={n.id} className="text-2xs">
              <span className="text-fg">{n.label}</span>{" "}
              <span className="text-subtle">({n.kind})</span>
              <pre className="mt-0.5 overflow-x-auto rounded bg-muted/40 p-1.5 font-mono text-[10px] leading-tight text-subtle">
                {JSON.stringify(sub.sources[n.id] ?? [], null, 1)}
              </pre>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
