"use client";
/**
 * Relation (edge) kinds, rendered as RELATIONS: a source kind, a labelled arrow, a target kind —
 * not a table row with a `from → to` string cell.
 *
 * Each card shows how many edges of it the graph holds, and how many extractions were refused
 * because the endpoints did not fit (what the closed vocabulary enforces). The owner can change the
 * endpoints right on the card — picked from the pack's entity kinds, never typed — rename it, or
 * remove it (deprecate).
 *
 * Identity-bearing relations (bound to a claim or determination meta-kind, computed by the gateway
 * from the meta-model) get a distinct treatment and a one-line rule in words. They can still be
 * changed, but only through the explicit identity confirmation the page asks for.
 */
import * as React from "react";
import { ArrowRight, PencilLine, RotateCcw, ShieldAlert, SlidersHorizontal, Trash2 } from "lucide-react";
import type { EdgeKindView, MetaKindCatalog, NodeKindView } from "@/lib/ontology-api";
import { DeprecatedBadge, MetaKindBadge, SourceBadge } from "./badges";
import { KindPicker } from "./kind-picker";
import { CountTag } from "./node-kind-grid";

function Chip({ value }: { value: string }) {
  return (
    <span className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-2xs text-fg">
      {value === "*" ? "any kind" : value}
    </span>
  );
}

function ChipList({ values }: { values: string[] }) {
  if (!values.length) {
    return <span className="text-2xs italic text-subtle">unspecified</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {values.map((v) => (
        <Chip key={v} value={v} />
      ))}
    </div>
  );
}

function RelationCard({
  edge,
  catalog,
  nodeKinds,
  onRemove,
  onReactivate,
  onRename,
  onSetEndpoints,
}: {
  edge: EdgeKindView;
  catalog: MetaKindCatalog;
  nodeKinds: NodeKindView[];
  onRemove: () => void;
  onReactivate: () => void;
  onRename: () => void;
  onSetEndpoints: (src: string[], dst: string[]) => Promise<boolean>;
}) {
  const meta = catalog.edge.find((m) => m.value === edge.binds_to);
  const identityBearing = edge.carries_identity_rule;
  const [editing, setEditing] = React.useState(false);
  const [src, setSrc] = React.useState<string[]>(edge.from);
  const [dst, setDst] = React.useState<string[]>(edge.to);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const btn =
    "inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg";

  React.useEffect(() => {
    if (!editing) {
      setSrc(edge.from);
      setDst(edge.to);
    }
  }, [edge.from, edge.to, editing]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (await onSetEndpoints(src, dst)) setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "the gateway refused the change");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid={`edge-kind-${edge.kind}`}
      className={`flex flex-col gap-3 rounded-lg border p-3 ${
        identityBearing
          ? "border-band-review/40 bg-band-review/[0.04]"
          : edge.deprecated
            ? "border-border bg-muted/40 opacity-70"
            : "border-border bg-surface"
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-xs font-medium text-fg">{edge.kind}</span>
        <MetaKindBadge value={edge.binds_to} info={meta} />
        <SourceBadge source={edge.source} />
        {edge.deprecated ? <DeprecatedBadge by={edge.deprecated_by} at={edge.deprecated_at} /> : null}
      </div>

      {editing ? (
        <div className="flex flex-col gap-2 rounded border border-accent/40 bg-bg px-2.5 py-2">
          <span className="text-2xs uppercase tracking-wider text-subtle">from</span>
          <KindPicker kinds={nodeKinds} value={src} onChange={setSrc} testId={`endpoints-src-${edge.kind}`} />
          <span className="text-2xs uppercase tracking-wider text-subtle">to</span>
          <KindPicker kinds={nodeKinds} value={dst} onChange={setDst} testId={`endpoints-dst-${edge.kind}`} />
          {error ? <p className="text-2xs text-band-review">Refused: {error}</p> : null}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy || !src.length || !dst.length}
              onClick={save}
              data-testid={`endpoints-save-${edge.kind}`}
              className="rounded border border-accent bg-accent px-2.5 py-1 text-2xs font-medium text-accent-fg disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save endpoints"}
            </button>
            <button type="button" onClick={() => setEditing(false)} className={btn}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 rounded border border-border/60 bg-bg px-2.5 py-2">
          <ChipList values={edge.from} />
          <span className="flex items-center gap-1 text-2xs text-subtle">
            <span className="h-px w-3 bg-border" />
            <ArrowRight className="h-3.5 w-3.5 shrink-0" />
          </span>
          <ChipList values={edge.to} />
        </div>
      )}

      <p className="text-2xs leading-relaxed text-subtle">{edge.description || "No description."}</p>

      {identityBearing ? (
        <div className="flex items-start gap-1.5 rounded border border-band-review/30 bg-band-review/[0.06] px-2 py-1.5">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
          <p className="text-2xs leading-relaxed text-band-review">
            {meta?.attested
              ? "A DETERMINATION — written only behind a named human's attestation. Extraction never proposes one."
              : "A CLAIM about which subject an artefact concerns, never a determination. Two candidates yield two of these edges, and neither is an answer."}
          </p>
        </div>
      ) : null}

      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1">
        <div className="flex flex-wrap items-center gap-3">
          <CountTag count={edge.count} noun="edge" />
          {edge.refused_off_ontology ? (
            <span
              className="text-2xs text-band-spot"
              title="Extractions of this relation refused because the source or target kind was not allowed"
            >
              {edge.refused_off_ontology} refused (endpoints off-ontology)
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          {edge.identity_protected ? (
            <span title="Identity-protected: changes need an explicit confirmation" className="text-band-review">
              <ShieldAlert className="h-3.5 w-3.5" />
            </span>
          ) : null}
          {edge.deprecated ? (
            <button type="button" onClick={onReactivate} className={btn}>
              <RotateCcw className="h-3 w-3" /> Reactivate
            </button>
          ) : (
            <>
              {!editing ? (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className={btn}
                  data-testid={`endpoints-edit-${edge.kind}`}
                >
                  <SlidersHorizontal className="h-3 w-3" /> Endpoints
                </button>
              ) : null}
              <button type="button" onClick={onRename} className={btn}>
                <PencilLine className="h-3 w-3" /> Rename
              </button>
              <button
                type="button"
                onClick={onRemove}
                title="Remove this relation from what's offered for new work"
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

export function RelationList({
  edges,
  catalog,
  nodeKinds,
  onRemove,
  onReactivate,
  onRename,
  onSetEndpoints,
}: {
  edges: EdgeKindView[];
  catalog: MetaKindCatalog;
  nodeKinds: NodeKindView[];
  onRemove: (edge: EdgeKindView) => void;
  onReactivate: (edge: EdgeKindView) => void;
  onRename: (edge: EdgeKindView) => void;
  onSetEndpoints: (edge: EdgeKindView, src: string[], dst: string[]) => Promise<boolean>;
}) {
  const identity = edges.filter((e) => e.carries_identity_rule);
  const ordinary = edges.filter((e) => !e.carries_identity_rule);

  if (!edges.length) {
    return (
      <p className="rounded border border-dashed border-border px-3 py-6 text-center text-xs text-subtle">
        No relations are defined yet, so extraction can find things but cannot connect them. Define
        a relation between two entity kinds above.
      </p>
    );
  }

  const card = (e: EdgeKindView) => (
    <RelationCard
      key={e.kind}
      edge={e}
      catalog={catalog}
      nodeKinds={nodeKinds}
      onRemove={() => onRemove(e)}
      onReactivate={() => onReactivate(e)}
      onRename={() => onRename(e)}
      onSetEndpoints={(src, dst) => onSetEndpoints(e, src, dst)}
    />
  );

  return (
    <div className="flex flex-col gap-4">
      {identity.length ? (
        <div>
          <h3 className="mb-2 text-2xs font-medium uppercase tracking-wider text-band-review">
            Identity-bearing
          </h3>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">{identity.map(card)}</div>
        </div>
      ) : null}
      <div>
        {identity.length ? (
          <h3 className="mb-2 text-2xs font-medium uppercase tracking-wider text-subtle">General</h3>
        ) : null}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">{ordinary.map(card)}</div>
      </div>
    </div>
  );
}
