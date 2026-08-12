"use client";
/**
 * The evidence chain, rendered as a chain (T-37): source -> pointer -> extracted value -> graph
 * node/edge -> committed ledger row. Matches the visual language of `card-sections.tsx` and
 * `/impact` — neutral surface cards, the `2xs`/`tnum` type scale, the same status-pill palette —
 * rather than inventing a new look for the one page whose whole job is to be believed.
 */
import * as React from "react";
import { AlertTriangle, FileStack, GitCommitHorizontal, Link2, ShieldCheck } from "lucide-react";
import type { EvidenceLedgerEntry, EvidenceSource, EvidenceTrace } from "@/lib/evidence-api";
import { cn } from "@/lib/utils";
import { PointerJson } from "./pointer-json";
import { RoutePill } from "./route-pill";

function Stage({
  n,
  title,
  icon: Icon,
  last = false,
  children,
}: {
  n: number;
  title: string;
  icon: React.ElementType;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="relative pb-6 pl-9 last:pb-0">
      {!last && <div className="absolute left-[15px] top-8 bottom-0 w-px bg-border" />}
      <div className="absolute left-0 top-0 flex h-8 w-8 items-center justify-center rounded-full border border-border bg-surface">
        <Icon className="h-3.5 w-3.5 text-subtle" />
      </div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-2xs font-semibold uppercase tracking-wide text-subtle">
          Step {n}
        </span>
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function EntityBadge({ trace }: { trace: EvidenceTrace }) {
  const label =
    trace.entity_type === "node"
      ? `graph node · ${trace.node?.kind}`
      : trace.entity_type === "edge"
        ? `graph edge · ${trace.edge?.kind}`
        : "committed ledger row";
  return (
    <span className="rounded bg-accent/15 px-1.5 py-0.5 text-2xs font-medium text-accent">
      {label}
    </span>
  );
}

function KeyValueGrid({ pairs }: { pairs: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      {pairs.map(([k, v]) => (
        <div className="contents" key={k}>
          <dt className="text-subtle">{k}</dt>
          <dd className="tnum break-all">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function SourceCard({ source, index }: { source: EvidenceSource; index: number }) {
  return (
    <div
      data-testid="evidence-source"
      className="mb-3 rounded-lg border border-border bg-surface p-3 last:mb-0"
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="text-2xs font-semibold text-subtle">Pointer {index + 1}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-2xs font-medium text-fg">
          {source.pointer.kind}
        </span>
        <RoutePill route={source.ingestion_route} />
        {source.intake_id && (
          <span className="text-2xs text-subtle">
            intake <code className="text-fg">{source.intake_id}</code>
          </span>
        )}
      </div>

      <KeyValueGrid
        pairs={[
          ["source_id", source.pointer.source_id],
          [
            "document",
            source.document ? (
              <>
                {source.document.label}
                {source.document.document_type && (
                  <span className="ml-1.5 text-subtle">({source.document.document_type})</span>
                )}
              </>
            ) : source.document_id ? (
              <span className="text-subtle">{source.document_id} (not a graph node)</span>
            ) : (
              <span className="text-subtle">
                not document-shaped — this pointer cites {source.pointer.source_id}
              </span>
            ),
          ],
        ]}
      />

      <p className="mb-1 mt-2 text-2xs font-medium uppercase tracking-wide text-subtle">
        The pointer, unedited
      </p>
      <PointerJson pointer={source.pointer} />
    </div>
  );
}

function LedgerRowCard({ entry }: { entry: EvidenceLedgerEntry }) {
  return (
    <div
      data-testid="evidence-ledger-row"
      className="mb-3 rounded-lg border border-border bg-surface p-3 last:mb-0"
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="rounded bg-band-auto/15 px-1.5 py-0.5 text-2xs font-medium text-band-auto">
          committed
        </span>
        <span className="text-2xs text-subtle">
          row <code className="text-fg">#{entry.rowid}</code>
        </span>
        {entry.supersedes !== null && (
          <span className="rounded bg-band-spot/15 px-1.5 py-0.5 text-2xs text-band-spot">
            supersedes #{entry.supersedes}
          </span>
        )}
      </div>
      <KeyValueGrid
        pairs={[
          ["field", entry.field_path],
          ["value", JSON.stringify(entry.value)],
          ["intake", entry.intake_id],
          [
            "confirmed by",
            <span key="c" className="font-medium">
              {entry.confirmed_by}
            </span>,
          ],
          ["confirmed at", entry.confirmed_at],
          ...(entry.selected_patient_id
            ? ([["selected patient", entry.selected_patient_id]] as [string, React.ReactNode][])
            : []),
        ]}
      />
      <p className="mb-1 mt-2 text-2xs font-medium uppercase tracking-wide text-subtle">
        This row&rsquo;s own pointer
      </p>
      <PointerJson pointer={entry.pointer} />
    </div>
  );
}

export function EvidenceTraceView({ trace }: { trace: EvidenceTrace }) {
  return (
    <div className="flex flex-col gap-1">
      <Stage n={1} title="Entity" icon={GitCommitHorizontal}>
        <div className="rounded-lg border border-border bg-surface p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <code className="text-xs font-medium">{trace.entity_id}</code>
            <EntityBadge trace={trace} />
          </div>
          {trace.node && (
            <KeyValueGrid
              pairs={[
                ["label", trace.node.label],
                ["canonical_id", trace.node.canonical_id ?? "—"],
                ["valid_from", trace.node.valid_from ?? "—"],
                ["valid_to", trace.node.valid_to ?? "still open"],
                ["system_from", trace.node.system_from ?? "—"],
                ["system_to", trace.node.system_to ?? "still believed"],
              ]}
            />
          )}
          {trace.edge && (
            <KeyValueGrid
              pairs={[
                ["src", trace.edge.src_id],
                ["dst", trace.edge.dst_id],
                ["valid_from", trace.edge.valid_from ?? "—"],
                ["valid_to", trace.edge.valid_to ?? "still open"],
              ]}
            />
          )}
          {trace.entity_type === "ledger_entry" && trace.ledger_entries[0] && (
            <p className="text-xs text-subtle">
              A row committed to the evidence ledger — the extracted value staff attested to and
              this system wrote down.
            </p>
          )}
        </div>
      </Stage>

      <Stage n={2} title={`Source & pointer (${trace.sources.length})`} icon={FileStack}>
        {trace.sources.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-subtle">
            This entity carries no SourcePointer, which should be impossible — the graph refuses
            to construct a node or edge without one (CLAUDE.md hard constraint 2).
          </p>
        ) : (
          trace.sources.map((s, i) => <SourceCard key={i} source={s} index={i} />)
        )}
      </Stage>

      <Stage n={3} title="Committed ledger rows" icon={ShieldCheck} last>
        {trace.ledger_entries.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-subtle">
            Nothing has been committed for this entity yet. A value only reaches the ledger after
            a named staff member attests to an in-person identity check and confirms — this is not
            a missing feature, it is the gate (CLAUDE.md hard constraints 1 &amp; 4) doing its job.
          </p>
        ) : (
          trace.ledger_entries.map((e) => <LedgerRowCard key={e.rowid} entry={e} />)
        )}
      </Stage>

      <div
        data-testid="evidence-limitation"
        className={cn(
          "mt-2 flex items-start gap-2 rounded-lg border border-band-spot/40 bg-band-spot/[0.06] px-3 py-2 text-2xs text-band-spot",
        )}
      >
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {trace.limitation}
      </div>
    </div>
  );
}

export function EvidenceEmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-16 text-center">
      <Link2 className="h-6 w-6 text-subtle" />
      <p className="text-sm font-medium">Trace a fact to its source</p>
      <p className="max-w-md text-xs text-subtle">
        Paste a graph node id (<code>patient:pat:…</code>, <code>document:chit_…</code>), a graph
        edge id (<code>document:…|for_patient|patient:…</code>), or a ledger row id (
        <code>ledger:12</code>) to see the chain: source → pointer → extracted value → graph node
        → committed ledger row.
      </p>
    </div>
  );
}
