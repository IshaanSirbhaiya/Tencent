"use client";
/**
 * Click a node or edge → its provenance. This panel is the whole point of the surface: it is
 * where a reader gets from a shape on screen to *why it is there* — the pointer that grounds each
 * attribute, which attributes were asked for and never found, and every bi-temporal version this
 * id has ever held. `/evidence` stays the deep pointer-inspector (verify a pointer live, see the
 * ledger row it fed); this panel is the graph's own lens on the same store and is never gated by
 * `/evidence`'s confirmation wall — see `graph_routes.py::graph_node` for why that wall does not
 * apply here.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, GitBranch, History, Route, UserSearch, Waypoints, X } from "lucide-react";
import Link from "next/link";
import {
  describeGraphError,
  graphApi,
  splitAttributes,
  structuralGrounding,
  type GraphEdgeDetail,
  type GraphNodeDetail,
  type IncidentEdge,
  type SubjectCandidate,
  nodeLabelText,
} from "@/lib/graph-api";
import { isRefusal } from "@/lib/intake-types";
import type { SourcePointer } from "@/lib/types";
import { PointerJson } from "@/components/evidence/pointer-json";
import { RefusalCard } from "@/components/refusal-card";
import { KindDot } from "./filter-bar";
import type { GraphSelection } from "./graph-canvas";
import { useKindPalette } from "./kind-palette";

export type PathEnd = "from" | "to";

/** A one-line, source-agnostic summary of where a pointer points: the source, then whichever
 *  location fields that pointer kind carries. Reads fields generically — never a per-kind branch. */
function pointerSummary(p: SourcePointer): string {
  const loc: string[] = [];
  if (p.table) loc.push(p.table);
  if (p.column) loc.push(p.column);
  if (p.row_id) loc.push(`row ${p.row_id}`);
  if (p.sheet) loc.push(p.sheet);
  if (p.cell ?? p.cell_range) loc.push(String(p.cell ?? p.cell_range));
  if (p.page !== null && p.page !== undefined) loc.push(`p.${p.page}`);
  if (p.char_start !== null && p.char_start !== undefined)
    loc.push(`chars ${p.char_start}–${p.char_end ?? "?"}`);
  return `${p.source_id}${loc.length ? ` · ${loc.join(" · ")}` : ""}`;
}

function evidenceHref(entityId: string): string {
  return `/evidence?entity=${encodeURIComponent(entityId)}`;
}

function fmt(iso: string | null): string {
  if (!iso) return "open";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function AttrRow({
  field,
  value,
  pointer,
  entityId,
}: {
  field: string;
  value: unknown;
  pointer?: SourcePointer;
  entityId: string;
}) {
  const [open, setOpen] = React.useState(false);
  const shown =
    value === null || value === undefined
      ? "—"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  return (
    <div className="border-b border-border/60 px-2 py-1.5 last:border-0" data-testid="graph-attr-row">
      <div className="flex w-full items-start justify-between gap-2">
        <span className="text-2xs text-subtle">{field}</span>
        <span className={pointer ? "grounded break-all text-right text-xs text-fg" : "break-all text-right text-xs text-fg"}>
          {shown}
        </span>
      </div>
      {pointer ? (
        <div className="mt-0.5 flex items-center gap-2 text-2xs text-subtle">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="min-w-0 flex-1 truncate text-left font-mono hover:text-fg"
            title={open ? "Hide the pointer" : "Show the full pointer"}
            aria-expanded={open}
          >
            ↳ {pointer.kind} · {pointerSummary(pointer)}
          </button>
          <Link
            href={evidenceHref(entityId)}
            className="shrink-0 text-accent hover:underline"
            title="Open this node's evidence trace"
          >
            evidence
          </Link>
        </div>
      ) : null}
      {open && pointer && <PointerJson pointer={pointer} />}
    </div>
  );
}

/** The attributes, fail-closed: a value is shown only beside its own pointer. A value the gateway
 *  sent WITHOUT one is withheld — its field is named, its value is not rendered — and the response
 *  is flagged as breaking the grounding contract (CLAUDE.md hard constraint 2). */
function AttributesSection({
  attrs,
  sources,
  entityId,
  emptyText,
}: {
  attrs: Record<string, unknown>;
  sources: Record<string, SourcePointer> | null | undefined;
  entityId: string;
  emptyText: string;
}) {
  const split = splitAttributes(attrs, sources);
  const total = split.grounded.length + split.withheld.length + split.empty.length;
  return (
    <section>
      <h3 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-subtle">Attributes ({total})</h3>
      {total === 0 ? (
        <p className="text-2xs text-subtle">{emptyText}</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {split.grounded.length + split.empty.length > 0 && (
            <div className="rounded border border-border">
              {split.grounded.map((g) => (
                <AttrRow key={g.field} field={g.field} value={g.value} pointer={g.pointer} entityId={entityId} />
              ))}
              {split.empty.map((f) => (
                <AttrRow key={f} field={f} value={null} entityId={entityId} />
              ))}
            </div>
          )}
          {split.withheld.length > 0 && (
            <div
              className="rounded border border-band-review/40 bg-band-review/[0.06] px-2 py-1.5 text-2xs"
              data-testid="graph-attr-withheld"
            >
              <p className="font-medium text-band-review">
                {split.withheld.length} value{split.withheld.length === 1 ? "" : "s"} withheld — no source pointer
              </p>
              <p className="mt-0.5 text-subtle">
                The gateway returned these fields without a per-attribute pointer, which breaks the grounding contract.
                Their values are not shown as facts; treat them as unknown.
              </p>
              <ul className="mt-1 flex flex-wrap gap-1">
                {split.withheld.map((f) => (
                  <li key={f} className="rounded bg-band-review/15 px-1.5 py-0.5 font-mono text-band-review">
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function VersionsList({ versions }: { versions: GraphNodeDetail["versions"] }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {versions.map((v, i) => (
        <li
          key={i}
          className="rounded border border-border bg-muted/40 px-2 py-1.5 text-2xs text-subtle"
        >
          <div className="flex items-center gap-1.5">
            <span className={v.is_open ? "text-band-auto" : "text-subtle"}>
              {v.is_open ? "believed now" : "superseded"}
            </span>
            {v.label !== undefined && v.label !== "" && (
              <span className="text-fg">— {nodeLabelText(v.label)}</span>
            )}
          </div>
          <div className="tnum mt-0.5">
            valid: {fmt(v.valid_from)} → {fmt(v.valid_to)}
          </div>
          <div className="tnum">
            learned: {fmt(v.system_from)} → {fmt(v.system_to)}
          </div>
        </li>
      ))}
    </ul>
  );
}

function IncidentEdgeRow({
  edge,
  direction,
  onSeed,
}: {
  edge: IncidentEdge;
  direction: "outgoing" | "incoming";
  onSeed: (id: string) => void;
}) {
  const palette = useKindPalette();
  return (
    <li className="flex items-center justify-between gap-2 rounded px-1.5 py-1 hover:bg-muted">
      <button
        type="button"
        onClick={() => onSeed(edge.other_id)}
        className="min-w-0 flex-1 truncate text-left text-2xs"
        title={edge.other_id}
      >
        <span className="text-subtle">{direction === "outgoing" ? "→" : "←"} </span>
        <span className="text-fg">{palette.edgeLabel(edge.kind)}</span>
        <span className="text-subtle"> {edge.other_id}</span>
      </button>
      {!edge.is_open && <span className="shrink-0 text-2xs text-band-review">superseded</span>}
    </li>
  );
}

/**
 * "Possibly the same person as …" — candidates the extractor listed (exact identifier or exact
 * label, never a similarity score). NOTHING here links them: whether two records are one person is
 * a staff determination recorded with an attestation (`attestation.record_determination` in the
 * engine), and the gateway exposes no route that records one. So the list is read-only and says so;
 * there is deliberately no button that would look like a confirmation.
 */
function SubjectCandidates({
  candidates,
  onFocus,
}: {
  candidates: SubjectCandidate[];
  onFocus: (id: string) => void;
}) {
  return (
    <section
      className="rounded border border-band-spot/40 bg-band-spot/[0.06] px-3 py-2"
      data-testid="graph-subject-candidates"
    >
      <h3 className="flex items-center gap-1 text-2xs font-semibold uppercase tracking-wider text-band-spot">
        <UserSearch className="h-3 w-3" /> Possibly the same person ({candidates.length})
      </h3>
      <p className="mt-1 text-2xs text-subtle">
        Candidates only — not linked, and never linked automatically. A person decides, in person.
      </p>
      <ul className="mt-1.5 flex flex-col gap-1">
        {candidates.map((c) => (
          <li key={`${c.extracted}|${c.candidate}`} className="flex items-baseline gap-2 text-2xs">
            <span className="text-subtle">possibly the same person as</span>
            <button
              type="button"
              onClick={() => onFocus(c.candidate)}
              className="min-w-0 truncate font-mono text-fg hover:text-accent"
              title={c.candidate}
              data-testid="graph-subject-candidate"
            >
              {c.candidate}
            </button>
            <span className="shrink-0 text-subtle">({c.basis})</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-2xs text-band-review" data-testid="graph-subject-confirm-unavailable">
        Confirmation requires a staff attestation route — not yet available.
      </p>
    </section>
  );
}

/** A structural value (label, kind, endpoints, temporal fields) rendered only as far as its
 *  grounding allows (R-019 round 3, G4): a per-field pointer cites it; entity-level grounding shows
 *  it but says it is not individually cited; nothing at all withholds it. */
function Structural({
  detail,
  field,
  children,
}: {
  detail: { sources?: GraphNodeDetail["sources"]; field_sources?: GraphNodeDetail["field_sources"] };
  field: string;
  children: React.ReactNode;
}) {
  const g = structuralGrounding(detail, field);
  if (g.state === "withheld") {
    return (
      <span className="italic text-band-review" data-testid={`graph-structural-withheld-${field}`}>
        withheld — no source grounds this entity
      </span>
    );
  }
  return (
    <span data-grounding={g.state} data-testid={`graph-structural-${field}`}>
      {children}
    </span>
  );
}

/** The one line that says how the structural values above are grounded. */
function StructuralNote({
  detail,
  fields,
}: {
  detail: { sources?: GraphNodeDetail["sources"]; field_sources?: GraphNodeDetail["field_sources"] };
  fields: string[];
}) {
  const states = fields.map((f) => structuralGrounding(detail, f).state);
  if (states.every((s) => s === "cited")) return null;
  if (states.every((s) => s === "withheld")) {
    return (
      <p className="text-2xs text-band-review" data-testid="graph-structural-note">
        No source grounds this entity, so its structural values are withheld.
      </p>
    );
  }
  return (
    <p className="text-2xs text-subtle" data-testid="graph-structural-note">
      {fields.filter((_, i) => states[i] === "entity").join(", ")}: grounded by the entity&rsquo;s sources below, not
      individually cited — the gateway records no per-field pointer for them.
    </p>
  );
}

function NodeDetailBody({
  detail,
  onSeed,
  onExpand,
  onPathEnd,
  pathEnds,
  onFocus,
}: {
  detail: GraphNodeDetail;
  onSeed: (id: string) => void;
  onExpand: (id: string) => void;
  onPathEnd: (id: string, end: PathEnd) => void;
  pathEnds: { from: string | null; to: string | null };
  onFocus: (id: string) => void;
}) {
  const palette = useKindPalette();
  // Only candidates the server marked for a human — the flag is the contract, not decoration.
  const candidates = (detail.subject_candidates ?? []).filter(
    (c) => c.requires_human_selection === true,
  );
  const btn =
    "flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:border-accent hover:text-accent disabled:opacity-40";
  return (
    <div className="flex flex-col gap-4 text-xs">
      <div>
        <div className="flex items-center gap-1.5">
          <KindDot kind={detail.kind} />
          <span className="text-2xs uppercase tracking-wide text-subtle">
            <Structural detail={detail} field="kind">
              {palette.node(detail.kind).label}
            </Structural>
          </span>
          {!detail.is_open && (
            <span className="rounded border border-band-review/40 px-1 py-px text-2xs text-band-review">
              superseded
            </span>
          )}
        </div>
        <div className="mt-1 break-words text-sm font-semibold text-fg">
          <Structural detail={detail} field="label">
            {nodeLabelText(detail.label)}
          </Structural>
        </div>
        <div className="mt-0.5 break-all font-mono text-2xs text-subtle">{detail.id}</div>
        <div className="mt-1">
          <StructuralNote detail={detail} fields={["label", "kind", "valid_from", "system_from"]} />
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => onExpand(detail.id)}
            className={btn}
            data-testid="graph-expand"
            title="Add this node's neighbourhood to the canvas (or double-click the node)"
          >
            <GitBranch className="h-3 w-3" /> Expand
          </button>
          <button
            type="button"
            onClick={() => onSeed(detail.id)}
            className={btn}
            title="Clear the canvas and start from this node"
          >
            <Waypoints className="h-3 w-3" /> Start here
          </button>
          <button
            type="button"
            onClick={() => onPathEnd(detail.id, "from")}
            className={btn}
            data-testid="graph-path-from"
            disabled={pathEnds.from === detail.id}
          >
            <Route className="h-3 w-3" /> Path from here
          </button>
          <button
            type="button"
            onClick={() => onPathEnd(detail.id, "to")}
            className={btn}
            data-testid="graph-path-to"
            disabled={pathEnds.to === detail.id}
          >
            <Route className="h-3 w-3" /> Path to here
          </button>
        </div>
      </div>

      <div className="tnum grid grid-cols-2 gap-2 text-2xs text-subtle">
        <div>
          <div className="uppercase tracking-wide">valid</div>
          <div className="text-fg">
            <Structural detail={detail} field="valid_from">
              {fmt(detail.valid_from)} → {fmt(detail.valid_to)}
            </Structural>
          </div>
        </div>
        <div>
          <div className="uppercase tracking-wide">learned</div>
          <div className="text-fg">
            <Structural detail={detail} field="system_from">
              {fmt(detail.system_from)} → {fmt(detail.system_to)}
            </Structural>
          </div>
        </div>
      </div>

      {candidates.length > 0 && <SubjectCandidates candidates={candidates} onFocus={onFocus} />}

      <AttributesSection
        attrs={detail.attrs}
        sources={detail.attribute_sources}
        entityId={detail.id}
        emptyText="This node carries no attributes beyond its label."
      />

      {detail.unresolved.length > 0 && (
        <section className="rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2">
          <h3 className="text-2xs font-semibold uppercase tracking-wider text-band-review">
            Unresolved ({detail.unresolved.length})
          </h3>
          <p className="mt-1 text-2xs text-subtle">
            Asked for and not found in any source — left blank on purpose rather than guessed.
          </p>
          <ul className="mt-1.5 flex flex-wrap gap-1">
            {detail.unresolved.map((f) => (
              <li key={f} className="rounded bg-band-review/15 px-1.5 py-0.5 font-mono text-2xs text-band-review">
                {f}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-subtle">
          Entity grounding ({detail.sources.length})
        </h3>
        <p className="mb-1 text-2xs text-subtle">
          That this node exists and was read from somewhere — not a citation for any one value
          above (those are per-attribute, shown inline).
        </p>
        <div className="flex flex-col gap-1.5">
          {detail.sources.map((p, i) => (
            <PointerJson key={i} pointer={p} />
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-1 flex items-center gap-1 text-2xs font-semibold uppercase tracking-wider text-subtle">
          <History className="h-3 w-3" /> Bi-temporal history ({detail.versions.length})
        </h3>
        <VersionsList versions={detail.versions} />
      </section>

      <section>
        <h3 className="mb-1 flex items-center gap-1 text-2xs font-semibold uppercase tracking-wider text-subtle">
          <GitBranch className="h-3 w-3" /> Edges ({detail.edges.matched_total}
          {detail.edges.truncated ? `, showing ${detail.edges.returned}` : ""})
        </h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div>
            <div className="text-2xs text-subtle">outgoing ({detail.edges.outgoing.length})</div>
            <ul>
              {detail.edges.outgoing.map((e) => (
                <IncidentEdgeRow key={e.id} edge={e} direction="outgoing" onSeed={onExpand} />
              ))}
            </ul>
          </div>
          <div>
            <div className="text-2xs text-subtle">incoming ({detail.edges.incoming.length})</div>
            <ul>
              {detail.edges.incoming.map((e) => (
                <IncidentEdgeRow key={e.id} edge={e} direction="incoming" onSeed={onExpand} />
              ))}
            </ul>
          </div>
        </div>
      </section>

      <Link
        href={evidenceHref(detail.id)}
        className="flex items-center gap-1.5 text-2xs text-accent hover:underline"
        data-testid="graph-evidence-link"
      >
        <ExternalLink className="h-3 w-3" /> Open full evidence trace
      </Link>
    </div>
  );
}

function EdgeDetailBody({ detail }: { detail: GraphEdgeDetail }) {
  const palette = useKindPalette();
  return (
    <div className="flex flex-col gap-4 text-xs">
      <div>
        <div className="flex items-center gap-1.5">
          <span className="text-2xs uppercase tracking-wide text-subtle">
            <Structural detail={detail} field="kind">
              {palette.edgeLabel(detail.kind)}
            </Structural>
          </span>
          {!detail.is_open && (
            <span className="rounded border border-band-review/40 px-1 py-px text-2xs text-band-review">
              superseded
            </span>
          )}
          {detail.has_attestation && (
            <span className="rounded border border-band-auto/40 px-1 py-px text-2xs text-band-auto">
              attested
            </span>
          )}
        </div>
        <div className="mt-1 break-all font-mono text-2xs text-fg">
          <Structural detail={detail} field="src_id">
            {detail.src_id} <span className="text-subtle">→</span> {detail.dst_id}
          </Structural>
        </div>
        <div className="mt-1">
          <StructuralNote detail={detail} fields={["kind", "src_id", "valid_from", "system_from"]} />
        </div>
      </div>

      <div className="tnum grid grid-cols-2 gap-2 text-2xs text-subtle">
        <div>
          <div className="uppercase tracking-wide">valid</div>
          <div className="text-fg">
            <Structural detail={detail} field="valid_from">
              {fmt(detail.valid_from)} → {fmt(detail.valid_to)}
            </Structural>
          </div>
        </div>
        <div>
          <div className="uppercase tracking-wide">learned</div>
          <div className="text-fg">
            <Structural detail={detail} field="system_from">
              {fmt(detail.system_from)} → {fmt(detail.system_to)}
            </Structural>
          </div>
        </div>
      </div>

      <AttributesSection
        attrs={detail.attrs}
        sources={detail.attribute_sources}
        entityId={detail.id}
        emptyText="This edge carries no attributes."
      />

      {detail.unresolved.length > 0 && (
        <section className="rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2">
          <h3 className="text-2xs font-semibold uppercase tracking-wider text-band-review">
            Unresolved ({detail.unresolved.length})
          </h3>
          <ul className="mt-1.5 flex flex-wrap gap-1">
            {detail.unresolved.map((f) => (
              <li key={f} className="rounded bg-band-review/15 px-1.5 py-0.5 font-mono text-2xs text-band-review">
                {f}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-subtle">
          Grounding ({detail.sources.length})
        </h3>
        <div className="flex flex-col gap-1.5">
          {detail.sources.map((p, i) => (
            <PointerJson key={i} pointer={p} />
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-1 flex items-center gap-1 text-2xs font-semibold uppercase tracking-wider text-subtle">
          <History className="h-3 w-3" /> Bi-temporal history ({detail.versions.length})
        </h3>
        <VersionsList versions={detail.versions} />
      </section>

      <Link
        href={evidenceHref(detail.id)}
        className="flex items-center gap-1.5 text-2xs text-accent hover:underline"
      >
        <ExternalLink className="h-3 w-3" /> Open full evidence trace
      </Link>
    </div>
  );
}

export function ProvenancePanel({
  selection,
  onClose,
  onSeed,
  onExpand,
  onPathEnd,
  pathEnds,
  onFocus,
}: {
  selection: GraphSelection;
  onClose: () => void;
  onSeed: (id: string) => void;
  onExpand: (id: string) => void;
  onPathEnd: (id: string, end: PathEnd) => void;
  pathEnds: { from: string | null; to: string | null };
  /** Select a node, merging it into the canvas first when it is not drawn. */
  onFocus: (id: string) => void;
}) {
  const isNode = selection?.type === "node";
  const nodeQ = useQuery({
    queryKey: ["graph-node", selection?.type === "node" ? selection.id : null],
    queryFn: () => graphApi.node((selection as { id: string }).id),
    enabled: isNode,
  });
  const edgeQ = useQuery({
    queryKey: ["graph-edge", selection?.type === "edge" ? selection.id : null],
    queryFn: () => graphApi.edge((selection as { id: string }).id),
    enabled: selection?.type === "edge",
  });

  if (!selection) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1.5 p-6 text-center text-2xs text-subtle">
        <p>Click a node or an edge to see its provenance —</p>
        <p>label, kind, attributes, and the pointer that put each one there.</p>
        <p className="mt-2">Double-click a node to expand its neighbourhood into the canvas.</p>
      </div>
    );
  }

  const query = isNode ? nodeQ : edgeQ;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-2xs font-semibold uppercase tracking-wider text-subtle">
          {isNode ? "Node" : "Edge"} provenance
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-subtle hover:bg-muted hover:text-fg"
          aria-label="Close panel"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {query.isLoading && <p className="text-2xs text-subtle">Loading…</p>}
        {query.isError && (
          <p className="text-2xs text-band-review">
            {describeGraphError(query.error)}
          </p>
        )}
        {query.data && isRefusal(query.data) && <RefusalCard refusal={query.data.refusal} />}
        {query.data &&
          !isRefusal(query.data) &&
          (isNode ? (
            <NodeDetailBody
              detail={query.data as GraphNodeDetail}
              onSeed={onSeed}
              onExpand={onExpand}
              onPathEnd={onPathEnd}
              pathEnds={pathEnds}
              onFocus={onFocus}
            />
          ) : (
            <EdgeDetailBody detail={query.data as GraphEdgeDetail} />
          ))}
      </div>
    </div>
  );
}
