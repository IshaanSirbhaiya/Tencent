"use client";
/**
 * `/intelligence` — the action console.
 *
 * ## What changed, and why the page is not a dashboard any more
 *
 * This screen used to load a dashboard: figures first, an ask box beside them. `AGENT_CAPABILITIES.md`
 * describes something else — *a request box. You ask for something in plain language and the agent
 * does it: reads the graph, explains a workflow, proposes an action, or — once a named human
 * approves — causes something to actually happen in the world. Analytics are one possible answer,
 * not the content of the page.*
 *
 * So the figures moved into `<Figures>`, which renders when a request asked for them or when a
 * person opens them deliberately. Nothing about them changed; what changed is that the page no
 * longer asserts them at somebody who came here to do something else.
 *
 * ## The one line the whole design turns on
 *
 * **The agent may propose anything and perform nothing.** Every act on the world goes through a
 * draft a human approves and a model-free dispatcher executes. That is not timidity — it is what
 * makes an agent safe enough to point at a real operation, and it is the demo beat: the draft
 * appears, a
 * human reads it, *then* it happens.
 *
 * The three rules this page has always been held to are unchanged and now apply to the new half
 * as well:
 *
 * 1. **Counts of structural facts, never claims about outcomes.** No minutes, no money; `/impact`
 *    owns the only saving this system claims.
 * 2. **Every value carries its provenance and links to the traversal behind it.** That includes a
 *    proposed action: its recipient, its subject and its body each carry the citations they were
 *    built from.
 * 3. **It reports identity work; it never does any.** A proposed action's recipient comes from a
 *    committed ledger row a named human attested to in person, or from a human typing it. The
 *    agent has no third door, and this page has no control that would open one.
 *
 * The `as of` control is the honest use of the bi-temporal axis: graph figures rewind to what was
 * true on a date, the live intake queue does not — `app.state.intakes` is not bi-temporal, and
 * rewinding it would put a fabricated number beside a real one.
 */
import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { Topbar } from "@/components/topbar";
import { ConsolePanel } from "@/components/intelligence/console-panel";
import { OneTimePanel } from "@/components/intelligence/one-time-panel";
import { Figures } from "@/components/intelligence/figures";
import { SubgraphPanel } from "@/components/intelligence/subgraph-panel";
import { intelligenceApi, type IntelligenceResponse } from "@/lib/intelligence-api";
import { useNouns } from "@/lib/pack-context";

export default function IntelligencePage() {
  const nouns = useNouns();
  const [asOf, setAsOf] = React.useState<string>("");
  const [figures, setFigures] = React.useState<IntelligenceResponse | null>(null);
  const [selection, setSelection] = React.useState<{ title: string; members: string[] } | null>(
    null,
  );

  // Figures are FETCHED, never mounted: this page does not load a dashboard on arrival. The
  // console hands them over when a request asked for them; this button is the deliberate way in.
  const openFigures = useMutation({
    mutationFn: () => intelligenceApi.summary(asOf || null),
    onSuccess: setFigures,
  });

  const onSelect = React.useCallback((members: string[], title: string) => {
    setSelection((prev) => (prev?.title === title ? null : { title, members }));
  }, []);

  return (
    <>
      <Topbar title="Intelligence" />
      <div className="flex flex-col gap-5 p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-3xl">
            <h1 className="text-lg font-semibold">Ask the {nouns.org} for something</h1>
            <p className="text-xs text-subtle">
              Reading the graph happens immediately, with a citation on every value. Put a name in
              quotes to ask about one entity &mdash; its neighbours, its sources, or the path
              between two. Anything that would act on the world becomes a draft a named person
              approves, which a separate process with no model in it then performs. Figures are
              one possible answer, not the content of this page.
            </p>
          </div>
          <label className="flex items-center gap-2 text-2xs text-subtle">
            as of
            <input
              type="date"
              value={asOf}
              onChange={(e) => {
                setAsOf(e.target.value);
                setSelection(null);
                setFigures(null);
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
                  setFigures(null);
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
            Graph reads are rewound to <span className="tnum text-fg">{asOf}</span> — facts whose
            validity had not begun by then are excluded, and so are their relationships. The intake
            queue and the ingestion register are <em>not</em> rewound: they are live, and
            pretending otherwise would put a fabricated number next to a real one.
          </p>
        )}

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-5">
            <ConsolePanel asOf={asOf} onCite={onSelect} onFigures={setFigures} />

            {/* FEAT8: a job described once and run once, through the agent compiler and runtime. */}
            <OneTimePanel />

            <section className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => (figures ? setFigures(null) : openFigures.mutate())}
                  data-testid="open-figures"
                  aria-expanded={Boolean(figures)}
                  className="flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-accent/60 disabled:opacity-40"
                  disabled={openFigures.isPending}
                >
                  <BarChart3 className="h-3.5 w-3.5" />
                  {figures
                    ? `Hide the ${nouns.org} figures`
                    : openFigures.isPending
                      ? "Walking the graph…"
                      : `Show the ${nouns.org} figures`}
                </button>
                <span className="text-2xs text-subtle">
                  or ask for them &mdash; a request that asks for counts returns them beside the
                  answer.
                </span>
              </div>

              {openFigures.isError && (
                <p className="text-2xs text-band-review" role="alert">
                  Could not reach the gateway: {(openFigures.error as Error).message}
                </p>
              )}

              {figures && (
                <Figures
                  data={figures}
                  selectedTitle={selection?.title ?? null}
                  onSelect={onSelect}
                />
              )}
            </section>
          </div>

          <SubgraphPanel
            graph={figures?.graph ?? null}
            title={selection?.title ?? null}
            members={selection?.members ?? []}
            asOf={asOf}
          />
        </div>

        <p className="max-w-3xl text-2xs text-subtle">
          Anything carrying a {nouns.subject}&rsquo;s information is shown by its kind and an opaque
          handle, never by its name: a document&rsquo;s filename can carry a person&rsquo;s name,
          and this page counts identity work without ever performing any. {nouns.subject_plural_title}{" "}
          themselves are counted and never listed. A proposed action inherits the same rule &mdash;
          its recipient is a masked mailbox plus a sentence saying how it was established. Open the
          entity on <code>/evidence</code> to see its source, once a person has confirmed it.
        </p>

        <p className="max-w-3xl text-2xs text-subtle">
          Every figure here is a count of something structurally true in the graph, with the
          population it was counted over. None of them is a statement about time, money or
          outcomes: the only saving this system claims is measured per intake, per completed stage,
          on <code>/impact</code>. Mixing the two is how a dashboard stops being believable.
        </p>
      </div>
    </>
  );
}
