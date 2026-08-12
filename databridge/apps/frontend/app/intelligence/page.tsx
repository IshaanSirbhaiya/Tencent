"use client";
/**
 * `/intelligence` — the Business Intelligence surface (PHASE_7 T-42).
 *
 * Not another dashboard: the place where the clinic graph is *interpreted*. Phase 8 puts an agent
 * on top of this; what is here is the read-only foundation it will sit on.
 *
 * Three rules decide everything on this screen.
 *
 * 1. **These are counts of structural facts, never claims about outcomes.** "2 of 10 documents
 *    name more than one candidate" is a fact about the graph. "We saved N hours" is a claim about
 *    the world, and the only version of that claim we are entitled to make is Phase 5's
 *    per-intake, stage-completed model — which lives on `/impact` and is not restated here. A
 *    structural count rendered beside a performance claim reads as one, and that is how a
 *    dashboard stops being believable.
 *
 * 2. **Every figure carries its denominator, and links to the traversal behind it.** Nothing on
 *    this page is a literal: every number comes from `databridge_graph.analytics.compute` walking
 *    edges, and clicking it lists the exact ids it counted, each expandable into the subgraph and
 *    the raw `SourcePointer`s grounding it.
 *
 * 3. **It reports identity work; it never does any.** The queue block says how many registry
 *    candidates a document matched and that a human must choose. It never names one, orders them,
 *    or implies a decision (CLAUDE.md §Hard constraints 1).
 *
 * The `as of` control is the honest use of the bi-temporal axis: the graph figures rewind to what
 * was true on a date, the live intake queue does not — `app.state.intakes` is not bi-temporal, and
 * rewinding it would put a fabricated number beside a real one. The page says so rather than
 * quietly showing both.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { Topbar } from "@/components/topbar";
import { AskPanel } from "@/components/intelligence/ask-panel";
import { FigureCard } from "@/components/intelligence/figure-card";
import { IssuerMix } from "@/components/intelligence/issuer-mix";
import { BlockedQueue } from "@/components/intelligence/blocked-queue";
import { SubgraphPanel } from "@/components/intelligence/subgraph-panel";
import { intelligenceApi } from "@/lib/intelligence-api";

function Unavailable({ what, reason }: { what: string; reason: string }) {
  // A failed aggregate states itself. Rendering `0` here would be a claim an outage is not
  // entitled to make, and is the failure mode fail-open exists to avoid.
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

export default function IntelligencePage() {
  const [asOf, setAsOf] = React.useState<string>("");
  const [selection, setSelection] = React.useState<{ title: string; members: string[] } | null>(
    null,
  );

  const q = useQuery({
    queryKey: ["intelligence", asOf],
    queryFn: () => intelligenceApi.summary(asOf || null),
    refetchInterval: 10_000,
  });

  const onSelect = React.useCallback((members: string[], title: string) => {
    setSelection((prev) => (prev?.title === title ? null : { title, members }));
  }, []);

  if (q.isError) {
    return (
      <>
        <Topbar title="Intelligence" />
        <p className="p-6 text-xs text-band-review" role="alert">
          Could not reach the gateway: {(q.error as Error).message}
        </p>
      </>
    );
  }
  // Guard on the DATA, not on `isLoading` — a query paused by react-query's `networkMode: "online"`
  // reports `isPending` without `isError`, and destructuring undefined white-screens the page.
  if (!q.data) {
    return (
      <>
        <Topbar title="Intelligence" />
        <p className="p-6 text-xs text-subtle">Walking the graph…</p>
      </>
    );
  }

  const { graph, graph_unavailable, queue, queue_unavailable, note } = q.data;
  const selectedIssuer =
    graph?.issuer_mix.find((r) => selection?.title === `documents issued by ${r.issuer}`)
      ?.issuer_id ?? null;

  return (
    <>
      <Topbar title="Intelligence" />
      <div className="flex flex-col gap-5 p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-3xl">
            <h1 className="text-lg font-semibold">What the clinic graph says</h1>
            <p className="text-xs text-subtle">{note}</p>
          </div>
          <label className="flex items-center gap-2 text-2xs text-subtle">
            as of
            <input
              type="date"
              value={asOf}
              onChange={(e) => {
                setAsOf(e.target.value);
                setSelection(null);
              }}
              className="rounded border border-border bg-surface px-2 py-1 text-xs text-fg"
              aria-label="Show the graph as it was on this date"
            />
            {asOf && (
              <button
                type="button"
                onClick={() => {
                  setAsOf("");
                  setSelection(null);
                }}
                className="text-accent hover:underline"
              >
                now
              </button>
            )}
          </label>
        </header>

        {asOf && (
          <p className="text-2xs text-subtle">
            Graph figures are rewound to <span className="tnum text-fg">{asOf}</span> — facts whose
            validity had not begun by then are excluded, and so are their relationships. The intake
            queue below is <em>not</em> rewound: it is the live queue, and pretending otherwise
            would put a fabricated number next to a real one.
          </p>
        )}

        {graph_unavailable && <Unavailable what="Graph figures" reason={graph_unavailable} />}

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-5">
            {/* The agent sits beside the figures, not instead of them. It reads the same
                traversal `analytics.compute` renders above, so a line it returns and a card on
                this page cannot disagree — and every line it returns opens in the same subgraph
                drawer on the right that the cards open. */}
            <AskPanel asOf={asOf} onCite={onSelect} />

            {graph && (
              <>
                <section>
                  <h2 className="mb-2 text-sm font-semibold">What needs a person</h2>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <FigureCard
                      figure={graph.ambiguity}
                      caveat="counted over documents that matched at least one registry row; the count says how many, never which"
                      accent="warn"
                      selected={selection?.title === graph.ambiguity?.label}
                      onSelect={onSelect}
                    />
                    <FigureCard
                      figure={graph.unlinked_documents}
                      caveat="no registry row matched at all — these are registered from scratch at the counter"
                      accent="bad"
                      selected={selection?.title === graph.unlinked_documents?.label}
                      onSelect={onSelect}
                    />
                    <FigureCard
                      figure={graph.conflicts}
                      caveat="the paperwork disagrees on who is paying; whether that applies to a given visit is decided per intake, in config"
                      accent="warn"
                      selected={selection?.title === graph.conflicts?.label}
                      onSelect={onSelect}
                    />
                  </div>
                </section>

                <section>
                  <h2 className="mb-1 text-sm font-semibold">Where the paperwork comes from</h2>
                  <p className="mb-2 text-2xs text-subtle">
                    One row per organisation with an <code>issued</code> edge to a document,
                    counted over {graph.documents} document{graph.documents === 1 ? "" : "s"} in the
                    graph. Adding an insurer is a <code>coverage_rules.yaml</code> change — there is
                    no issuer named anywhere in this page&rsquo;s code.
                  </p>
                  <IssuerMix
                    rows={graph.issuer_mix}
                    total={graph.documents}
                    selected={selectedIssuer}
                    onSelect={onSelect}
                  />
                </section>

                <section>
                  <h2 className="mb-1 text-sm font-semibold">Coverage gaps</h2>
                  <p className="mb-2 text-2xs text-subtle">
                    Tests a document asks for that no package in the catalogue includes — walked as{" "}
                    <code>requests_test</code> minus <code>includes_test</code>. This is what a
                    patient may end up paying for, found before they arrive.
                  </p>
                  {graph.tests_outside_any_package.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-2xs text-subtle">
                      Every test requested on file is included by some package.
                    </p>
                  ) : (
                    <button
                      type="button"
                      data-testid="coverage-gaps"
                      aria-pressed={selection?.title === "tests no package includes"}
                      onClick={() =>
                        onSelect(
                          graph.tests_outside_any_package.map((t) => t.id),
                          "tests no package includes",
                        )
                      }
                      className="flex w-full flex-wrap items-baseline gap-x-2 gap-y-1 rounded-lg border border-border bg-surface px-4 py-3 text-left hover:border-accent/60"
                    >
                      <span className="tnum text-2xl font-semibold leading-none text-band-spot">
                        {graph.tests_outside_any_package.length}
                      </span>
                      <span className="tnum text-sm text-subtle">
                        of {graph.tests_in_catalogue} catalogue tests
                      </span>
                      <span className="w-full text-xs text-fg">
                        {graph.tests_outside_any_package.map((t) => t.label).join(", ")}
                      </span>
                    </button>
                  )}
                </section>
              </>
            )}

            <section>
              <h2 className="mb-1 text-sm font-semibold">Waiting on a human</h2>
              <p className="mb-2 text-2xs text-subtle">
                Read from the confirm gate itself, not re-derived. Identity is verified in person by
                staff and is never automated — so an intake sitting here is the system working, not
                failing.
              </p>
              {queue_unavailable && <Unavailable what="The intake queue" reason={queue_unavailable} />}
              {queue && <BlockedQueue queue={queue} />}
            </section>
          </div>

          <SubgraphPanel
            graph={graph}
            title={selection?.title ?? null}
            members={selection?.members ?? []}
          />
        </div>

        <p className="max-w-3xl text-2xs text-subtle">
          Every figure above is a count of something structurally true in the graph, with the
          population it was counted over. None of them is a statement about time, money or
          outcomes: the only saving this system claims is measured per intake, per completed stage,
          on <code>/impact</code>. Mixing the two is how a dashboard stops being believable.
        </p>
      </div>
    </>
  );
}
