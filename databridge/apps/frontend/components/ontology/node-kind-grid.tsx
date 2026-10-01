"use client";
/**
 * Entity (node) kinds, as a card grid — not a table.
 *
 * Split into two groups on purpose: SUBJECTS first, because a subject is the one kind of thing this
 * whole product refuses to auto-confirm the identity of (CLAUDE.md §Hard constraints 1), and burying
 * it among ordinary rows is how that fact stops being visible. Every card shows how much of the
 * graph uses the kind (from the shared store), so "defined but no data yet" is visible too.
 *
 * Subject kinds can be renamed or removed, but only through the explicit identity confirmation
 * the page asks for — the lock icon says so rather than hiding the action.
 */
import * as React from "react";
import { PencilLine, RotateCcw, ShieldAlert, Trash2 } from "lucide-react";
import type { MetaKindCatalog, NodeKindView } from "@/lib/ontology-api";
import { DeprecatedBadge, MetaKindBadge, SourceBadge, SubjectBadge } from "./badges";

function metaInfo(catalog: MetaKindCatalog, value: string) {
  return catalog.node.find((m) => m.value === value);
}

export function CountTag({ count, noun }: { count: number | null; noun: string }) {
  if (count === null) return null;
  return count > 0 ? (
    <span className="text-2xs text-subtle">
      <span className="font-mono text-fg">{count}</span> {noun}
      {count === 1 ? "" : "s"} in graph
    </span>
  ) : (
    <span className="text-2xs italic text-subtle/80">no data yet</span>
  );
}

function NodeCard({
  node,
  catalog,
  onRemove,
  onReactivate,
  onRename,
}: {
  node: NodeKindView;
  catalog: MetaKindCatalog;
  onRemove: () => void;
  onReactivate: () => void;
  onRename: () => void;
}) {
  const btn =
    "inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg";
  return (
    <div
      data-testid={`node-kind-${node.kind}`}
      className={`flex flex-col gap-2 rounded-lg border p-3 ${
        node.subject
          ? "border-band-review/30 bg-band-review/[0.03]"
          : node.deprecated
            ? "border-border bg-muted/40 opacity-70"
            : "border-border bg-surface"
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-xs font-medium text-fg">{node.kind}</span>
        <MetaKindBadge value={node.binds_to} info={metaInfo(catalog, node.binds_to)} />
        {node.subject ? <SubjectBadge /> : null}
        <SourceBadge source={node.source} />
        {node.deprecated ? <DeprecatedBadge by={node.deprecated_by} at={node.deprecated_at} /> : null}
      </div>

      <p className="text-2xs leading-relaxed text-subtle">{node.description || "No description."}</p>

      <p className="text-2xs text-subtle">
        <span className="text-subtle/70">identity — </span>
        <span className={node.subject ? "text-band-review" : "text-fg"}>{node.identity}</span>
      </p>

      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1">
        <CountTag count={node.count} noun="node" />
        <div className="flex items-center gap-1">
          {node.identity_protected ? (
            <span title="Identity-protected: changes need an explicit confirmation" className="text-band-review">
              <ShieldAlert className="h-3.5 w-3.5" />
            </span>
          ) : null}
          {node.deprecated ? (
            <button type="button" onClick={onReactivate} className={btn} title="Offer this kind again">
              <RotateCcw className="h-3 w-3" /> Reactivate
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={onRename}
                className={btn}
                data-testid={`rename-node-${node.kind}`}
              >
                <PencilLine className="h-3 w-3" /> Rename
              </button>
              <button
                type="button"
                onClick={onRemove}
                title="Remove this kind from what's offered for new work"
                className={`${btn} hover:border-band-review/40 hover:text-band-review`}
              >
                <Trash2 className="h-3 w-3" /> Remove
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function NodeKindGrid({
  nodes,
  catalog,
  onRemove,
  onReactivate,
  onRename,
}: {
  nodes: NodeKindView[];
  catalog: MetaKindCatalog;
  onRemove: (node: NodeKindView) => void;
  onReactivate: (node: NodeKindView) => void;
  onRename: (node: NodeKindView) => void;
}) {
  const subjects = nodes.filter((n) => n.subject);
  const ordinary = nodes.filter((n) => !n.subject);

  if (!nodes.length) {
    return (
      <p className="rounded border border-dashed border-border px-3 py-6 text-center text-xs text-subtle">
        This pack defines no entity kinds yet. Until it does, extraction has nothing it may map to
        and will refuse everything — define the first one above.
      </p>
    );
  }

  const card = (n: NodeKindView) => (
    <NodeCard
      key={n.kind}
      node={n}
      catalog={catalog}
      onRemove={() => onRemove(n)}
      onReactivate={() => onReactivate(n)}
      onRename={() => onRename(n)}
    />
  );

  return (
    <div className="flex flex-col gap-4">
      {subjects.length ? (
        <div>
          <h3 className="mb-2 text-2xs font-medium uppercase tracking-wider text-band-review">
            Identity-protected (subjects)
          </h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">{subjects.map(card)}</div>
        </div>
      ) : null}
      <div>
        {subjects.length ? (
          <h3 className="mb-2 text-2xs font-medium uppercase tracking-wider text-subtle">General</h3>
        ) : null}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">{ordinary.map(card)}</div>
      </div>
    </div>
  );
}
