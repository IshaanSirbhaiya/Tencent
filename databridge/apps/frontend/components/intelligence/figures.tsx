"use client";
import { AlertTriangle } from "lucide-react";
import { FigureCard } from "@/components/intelligence/figure-card";
import { IssuerMix } from "@/components/intelligence/issuer-mix";
import { BlockedQueue } from "@/components/intelligence/blocked-queue";
import { KindChip } from "@/components/intelligence/kind-chip";
import {
  labelOf,
  kindOf,
  type GraphAnalytics,
  type IngestionSummary,
  type IntelligenceResponse,
} from "@/lib/intelligence-api";
import { useNouns } from "@/lib/pack-context";
import { cn } from "@/lib/utils";

/**
 * The graph's figures — **one possible answer**, not the content of the page.
 *
 * Every figure is computed by the gateway over the SHARED graph, reasoning about meta-kinds and
 * the bound packs' subject kinds; this component renders whatever comes back and names no kind,
 * no source and no organisation of its own. Three rules hold throughout:
 *
 * 1. **Counts of structural facts, never claims about outcomes.** No minutes, no money.
 * 2. **Every figure carries its denominator and links to the traversal behind it** — clicking a
 *    figure lists the exact ids it counted.
 * 3. **It reports identity work; it never does any.** A figure counting subject rows is shown
 *    without its members, and says why.
 *
 * A figure the bound vocabulary cannot support arrives as `null` and is left out with a sentence
 * saying so — never rendered as a zero.
 */
export function Figures({
  data,
  selectedTitle,
  onSelect,
}: {
  data: IntelligenceResponse;
  selectedTitle: string | null;
  onSelect: (members: string[], title: string) => void;
}) {
  const nouns = useNouns();
  const { graph, graph_unavailable, queue, queue_unavailable } = data;
  const selectedIssuer =
    graph?.issuer_mix.find((r) => selectedTitle === `documents issued by ${r.issuer}`)
      ?.issuer_id ?? null;

  return (
    <div className="flex flex-col gap-5" data-testid="figures">
      {graph_unavailable && <Unavailable what="Graph figures" reason={graph_unavailable} />}

      {graph && graph.nodes === 0 && (
        <p
          data-testid="figures-empty"
          className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-xs text-subtle"
        >
          The graph is empty — nothing has been ingested yet. Connect a source and run an
          ingestion; every figure here is computed from what the graph holds, so there is nothing
          to show until it holds something.
        </p>
      )}

      {graph && graph.nodes > 0 && (
        <>
          <WhatTheGraphHolds graph={graph} />

          <section>
            <h2 className="mb-2 text-sm font-semibold">Provenance</h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FigureCard
                figure={graph.cross_source}
                caveat="cited by two or more distinct sources — where separate sources corroborate the same thing"
                selected={selectedTitle === graph.cross_source?.label}
                onSelect={onSelect}
              />
              <FigureCard
                figure={graph.orphans}
                caveat="no relationship to anything else in the graph yet"
                accent="warn"
                selected={selectedTitle === graph.orphans?.label}
                onSelect={onSelect}
              />
            </div>
          </section>

          <RankedLists graph={graph} selectedTitle={selectedTitle} onSelect={onSelect} />

          <section>
            <h2 className="mb-1 text-sm font-semibold">Documents by source</h2>
            <p className="mb-2 text-2xs text-subtle">
              A source is what the documents&rsquo; own pointers cite. A source holding a document
              that carries a {nouns.subject}&rsquo;s information is shown by an opaque handle.
            </p>
            {graph.documents_by_source.filter((s) => s.documents > 0).length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-4 py-4 text-center text-2xs text-subtle">
                No document-shaped entity in the graph yet.
              </p>
            ) : (
              <ul className="flex flex-col gap-1" data-testid="documents-by-source">
                {graph.documents_by_source
                  .filter((s) => s.documents > 0)
                  .map((s) => {
                    const title = `documents from ${s.source}`;
                    return (
                      <li key={s.source}>
                        <button
                          type="button"
                          aria-pressed={selectedTitle === title}
                          disabled={s.members.length === 0}
                          onClick={() => onSelect(s.members, title)}
                          className={cn(
                            "flex w-full items-center gap-3 rounded border px-3 py-1.5 text-left",
                            selectedTitle === title
                              ? "border-accent bg-muted/40"
                              : "border-transparent hover:bg-muted/40",
                          )}
                        >
                          <code className="min-w-0 flex-1 truncate text-xs">{s.source}</code>
                          <span className="tnum shrink-0 text-2xs text-subtle">
                            {s.documents} of {graph.documents} documents · {s.entities} entities
                          </span>
                        </button>
                      </li>
                    );
                  })}
              </ul>
            )}
          </section>

          {graph.subjects ? (
            <section>
              <h2 className="mb-2 text-sm font-semibold">What needs a person</h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <FigureCard
                  figure={graph.subjects}
                  caveat={`${nouns.subject_plural} some document claims, out of those on file — a claim is not an identification`}
                  selected={selectedTitle === graph.subjects.label}
                  onSelect={onSelect}
                />
                <FigureCard
                  figure={graph.ambiguity}
                  caveat="the count says how many candidates, never which one"
                  accent="warn"
                  selected={selectedTitle === graph.ambiguity?.label}
                  onSelect={onSelect}
                />
                <FigureCard
                  figure={graph.unlinked_documents}
                  caveat={`no ${nouns.subject} row matched at all — settled by a named person`}
                  accent="bad"
                  selected={selectedTitle === graph.unlinked_documents?.label}
                  onSelect={onSelect}
                />
                <FigureCard
                  figure={graph.conflicts}
                  caveat="documents from different issuers disagree; deciding which applies is a person's call, per record"
                  accent="warn"
                  selected={selectedTitle === graph.conflicts?.label}
                  onSelect={onSelect}
                />
              </div>
            </section>
          ) : (
            <p className="text-2xs text-subtle" data-testid="identity-figures-absent">
              No bound pack declares a subject kind with a claim edge, so there is no identity work
              to count — those figures are not applicable here, not zero.
            </p>
          )}

          {graph.issuer_mix.length > 0 && (
            <section>
              <h2 className="mb-1 text-sm font-semibold">Who issued the documents</h2>
              <p className="mb-2 text-2xs text-subtle">
                One row per entity with an <code>issued</code>-shaped relationship to a document,
                counted over the documents in the graph.
              </p>
              <IssuerMix
                rows={graph.issuer_mix}
                total={graph.documents}
                selected={selectedIssuer}
                onSelect={onSelect}
              />
            </section>
          )}

          {graph.uncovered_references && (
            <section>
              <h2 className="mb-1 text-sm font-semibold">Coverage gaps</h2>
              <p className="mb-2 text-2xs text-subtle">
                Referenced by a document and included by no agreement in the graph — walked as
                references from a document minus part-of from an agreement.
              </p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <FigureCard
                  figure={graph.uncovered_references}
                  caveat="structural only — whether anyone is charged is decided elsewhere, per record"
                  accent="warn"
                  selected={selectedTitle === graph.uncovered_references.label}
                  onSelect={onSelect}
                />
              </div>
            </section>
          )}
        </>
      )}

      <IngestionCoverage
        ingestion={data.ingestion ?? null}
        unavailable={data.ingestion_unavailable ?? null}
      />

      {(queue || queue_unavailable) && (
        <section>
          <h2 className="mb-1 text-sm font-semibold">Waiting on a human</h2>
          <p className="mb-2 text-2xs text-subtle">
            Read from the confirm gate itself, not re-derived. Identity is verified in person and is
            never automated — an intake sitting here is the system working, not failing.
          </p>
          {queue_unavailable && <Unavailable what="The intake queue" reason={queue_unavailable} />}
          {queue && <BlockedQueue queue={queue} />}
        </section>
      )}

      <p className="max-w-3xl text-2xs text-subtle">{data.note}</p>
    </div>
  );
}

function WhatTheGraphHolds({ graph }: { graph: GraphAnalytics }) {
  const undeclared = graph.kinds.filter((k) => !k.declared);
  return (
    <section>
      <h2 className="mb-1 text-sm font-semibold">What the graph holds</h2>
      <p className="mb-2 text-2xs text-subtle">
        <span className="tnum text-fg">{graph.nodes}</span> entities ·{" "}
        <span className="tnum text-fg">{graph.edges}</span> relationships ·{" "}
        <span className="tnum text-fg">{graph.documents}</span> documents
        {graph.vocabulary.packs && graph.vocabulary.packs.length > 0 && (
          <> · vocabulary from {graph.vocabulary.packs.join(", ")}</>
        )}
      </p>
      <ul className="flex flex-wrap gap-1.5" data-testid="kind-counts">
        {graph.kinds.map((k) => (
          <li key={k.kind}>
            <KindChip kind={k.kind} className={k.declared ? undefined : "border-band-spot/60"}>
              <span className="tnum font-medium text-fg">{k.n}</span>
              {k.subject && <span className="text-band-review">subject</span>}
            </KindChip>
          </li>
        ))}
      </ul>
      {undeclared.length > 0 && (
        <p className="mt-1.5 text-2xs text-band-spot">
          {undeclared.map((k) => k.kind).join(", ")}: stored in the graph but declared by no
          bound pack, so they are counted and never shown.
        </p>
      )}
    </section>
  );
}

function RankedLists({
  graph,
  selectedTitle,
  onSelect,
}: {
  graph: GraphAnalytics;
  selectedTitle: string | null;
  onSelect: (members: string[], title: string) => void;
}) {
  return (
    <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div>
        <h2 className="mb-1 text-sm font-semibold">Most connected</h2>
        <p className="mb-2 text-2xs text-subtle">Ranked by relationships. Subjects are never ranked.</p>
        <RankList
          rows={graph.most_connected.map((r) => ({ id: r.id, meta: `${r.degree} rel.` }))}
          graph={graph}
          title="most connected"
          selectedTitle={selectedTitle}
          onSelect={onSelect}
          empty="Nothing in the graph has a relationship yet."
        />
      </div>
      <div>
        <h2 className="mb-1 text-sm font-semibold">Recently ingested</h2>
        <p className="mb-2 text-2xs text-subtle">Newest first, by when the graph recorded them.</p>
        <RankList
          rows={graph.recently_ingested.map((r) => ({
            id: r.id,
            meta: r.system_from ? r.system_from.slice(0, 16).replace("T", " ") : "—",
          }))}
          graph={graph}
          title="recently ingested"
          selectedTitle={selectedTitle}
          onSelect={onSelect}
          empty="Nothing has been recorded yet."
        />
      </div>
    </section>
  );
}

function RankList({
  rows,
  graph,
  title,
  selectedTitle,
  onSelect,
  empty,
}: {
  rows: { id: string; meta: string }[];
  graph: GraphAnalytics;
  title: string;
  selectedTitle: string | null;
  onSelect: (members: string[], title: string) => void;
  empty: string;
}) {
  if (rows.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-border px-3 py-3 text-2xs text-subtle">
        {empty}
      </p>
    );
  }
  return (
    <ol className="flex flex-col gap-0.5" data-testid={`ranked-${title.replace(/\s+/g, "-")}`}>
      {rows.map((r) => {
        const rowTitle = `${title}: ${labelOf(graph, r.id)}`;
        const kind = kindOf(graph, r.id);
        return (
          <li key={r.id}>
            <button
              type="button"
              aria-pressed={selectedTitle === rowTitle}
              onClick={() => onSelect([r.id], rowTitle)}
              className={cn(
                "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-muted/40",
                selectedTitle === rowTitle && "bg-muted/40",
              )}
            >
              {kind && <KindChip kind={kind} />}
              <span className="min-w-0 flex-1 truncate">{labelOf(graph, r.id)}</span>
              <span className="tnum shrink-0 text-2xs text-subtle">{r.meta}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function IngestionCoverage({
  ingestion,
  unavailable,
}: {
  ingestion: IngestionSummary | null;
  unavailable: string | null;
}) {
  return (
    <section>
      <h2 className="mb-1 text-sm font-semibold">Ingestion coverage &amp; exclusions</h2>
      <p className="mb-2 text-2xs text-subtle">
        From the run ledgers each ingestion writes. What a source excluded is part of every answer
        built from it: anything counted here was counted without those items.
      </p>
      {unavailable && <Unavailable what="The ingestion register" reason={unavailable} />}
      {ingestion && ingestion.sources.length === 0 && (
        <p
          data-testid="ingestion-empty"
          className="rounded-lg border border-dashed border-border px-4 py-4 text-2xs text-subtle"
        >
          {ingestion.note}
        </p>
      )}
      {ingestion && ingestion.sources.length > 0 && (
        <ul className="flex flex-col gap-1.5" data-testid="ingestion-sources">
          {ingestion.sources.map((s) => (
            <li key={s.source} className="rounded border border-border px-3 py-2 text-xs">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <code className="font-medium">{s.source}</code>
                <span className="text-2xs text-subtle">
                  latest run <code>{s.latest.run_id}</code>
                </span>
                {Object.entries(s.latest.counts).map(([family, n]) => (
                  <span key={family} className="text-2xs text-subtle">
                    {family.replace(/_/g, " ")} <span className="tnum text-fg">{n}</span>
                  </span>
                ))}
              </div>
              {s.latest.exclusion_reasons.length > 0 ? (
                <p className="mt-0.5 text-2xs text-band-spot">
                  <span className="tnum">{s.latest.excluded}</span> excluded —{" "}
                  {s.latest.exclusion_reasons.map((r) => `${r.n} × ${r.reason}`).join("; ")}
                </p>
              ) : (
                <p className="mt-0.5 text-2xs text-subtle">Nothing excluded in the latest run.</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** A failed aggregate states itself. Rendering a zero here would be a claim an outage is not
 *  entitled to make. */
function Unavailable({ what, reason }: { what: string; reason: string }) {
  return (
    <p
      className="flex items-start gap-2 rounded-lg border border-dashed border-band-spot/50 px-4 py-3 text-2xs text-band-spot"
      role="status"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        {what} unavailable — {reason}. The rest of this page is unaffected; no figure has been
        replaced by a zero.
      </span>
    </p>
  );
}
