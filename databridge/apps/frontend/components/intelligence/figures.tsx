"use client";
import { AlertTriangle } from "lucide-react";
import { FigureCard } from "@/components/intelligence/figure-card";
import { IssuerMix } from "@/components/intelligence/issuer-mix";
import { BlockedQueue } from "@/components/intelligence/blocked-queue";
import type { IntelligenceResponse } from "@/lib/intelligence-api";

/**
 * The clinic's figures — **one possible answer**, not the content of the page.
 *
 * These blocks used to be everything `/intelligence` was: a dashboard that loaded whether or not
 * anybody had asked for it. `AGENT_CAPABILITIES.md` says what the page is instead — a request box,
 * where analytics are one of three things a request can produce — so they moved into a component
 * that renders when a request asked for them, and when a person opens them deliberately.
 *
 * Nothing about the figures themselves changed, and the three rules they were built to still hold:
 *
 * 1. **Counts of structural facts, never claims about outcomes.** "2 of 10 documents name more
 *    than one candidate" is a fact about the graph. "We saved N hours" is a claim about the world,
 *    and the only version of that claim this system is entitled to make lives on `/impact`. A
 *    structural count rendered beside a performance claim reads as one.
 * 2. **Every figure carries its denominator and links to the traversal behind it.** Nothing here
 *    is a literal: every number comes from a traversal, and clicking it lists the exact ids it
 *    counted.
 * 3. **It reports identity work; it never does any.** The queue says how many registry candidates
 *    a document matched and that a human must choose. It never names one, orders them, or implies
 *    a decision (CLAUDE.md §Hard constraints 1).
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
  const { graph, graph_unavailable, queue, queue_unavailable } = data;
  const selectedIssuer =
    graph?.issuer_mix.find((r) => selectedTitle === `documents issued by ${r.issuer}`)
      ?.issuer_id ?? null;

  return (
    <div className="flex flex-col gap-5" data-testid="figures">
      {graph_unavailable && <Unavailable what="Graph figures" reason={graph_unavailable} />}

      {graph && (
        <>
          <section>
            <h2 className="mb-2 text-sm font-semibold">What needs a person</h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <FigureCard
                figure={graph.ambiguity}
                caveat="counted over documents that matched at least one registry row; the count says how many, never which"
                accent="warn"
                selected={selectedTitle === graph.ambiguity?.label}
                onSelect={onSelect}
              />
              <FigureCard
                figure={graph.unlinked_documents}
                caveat="no registry row matched at all — these are registered from scratch at the counter"
                accent="bad"
                selected={selectedTitle === graph.unlinked_documents?.label}
                onSelect={onSelect}
              />
              <FigureCard
                figure={graph.conflicts}
                caveat="the paperwork disagrees on who is paying; whether that applies to a given visit is decided per intake, in config"
                accent="warn"
                selected={selectedTitle === graph.conflicts?.label}
                onSelect={onSelect}
              />
            </div>
          </section>

          <section>
            <h2 className="mb-1 text-sm font-semibold">Where the paperwork comes from</h2>
            <p className="mb-2 text-2xs text-subtle">
              One row per organisation with an <code>issued</code> edge to a document, counted over
              the documents in the graph. Adding an insurer is a <code>coverage_rules.yaml</code>{" "}
              change — there is no issuer named anywhere in this page&rsquo;s code.
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
              <code>requests_test</code> minus <code>includes_test</code>. This is what a patient
              may end up paying for, found before they arrive.
            </p>
            {graph.tests_outside_any_package.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-2xs text-subtle">
                Every test requested on file is included by some package.
              </p>
            ) : (
              <button
                type="button"
                data-testid="coverage-gaps"
                aria-pressed={selectedTitle === "tests no package includes"}
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
          Read from the confirm gate itself, not re-derived. Identity is verified in person by staff
          and is never automated — so an intake sitting here is the system working, not failing.
        </p>
        {queue_unavailable && <Unavailable what="The intake queue" reason={queue_unavailable} />}
        {queue && <BlockedQueue queue={queue} />}
      </section>

      <p className="max-w-3xl text-2xs text-subtle">{data.note}</p>
    </div>
  );
}

/** A failed aggregate states itself. Rendering a zero here would be a claim an outage is not
 *  entitled to make, and is the failure mode fail-open exists to avoid. */
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
