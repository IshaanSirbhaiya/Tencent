"use client";
import * as React from "react";
import { ArrowRight, Search, ShieldAlert } from "lucide-react";
import { GATEWAY, authHeaders } from "@/lib/api";
import { brainApi } from "@/lib/brain-api";
import type {
  BrainAnswer,
  BrainGraph,
  BrainRefused,
  BrainQueryResult,
  DelegatedAnswer,
} from "@/lib/brain-types";
import { DelegatedAnswerView, DelegationNote, RouteNote } from "@/components/brain/delegated-answer";
import { Grounded } from "@/components/grounded";
import { nodeStyle } from "@/lib/brain-layout";
import { cn } from "@/lib/utils";
import { usePack } from "@/lib/pack-context";

// The real list comes from `GET /brain/suggested`, which serves the assistant config — built so
// the demo's prompts are config rather than strings compiled into this bundle. The fallback, for
// when the gateway is unreachable, is the ACTIVE PACK's, for the same reason: a question about one
// vertical's data, hardcoded here, is wrong on every other pack and unanswerable on this one the
// moment the seed changes.

// Column 1 — Ask the Brain (§Part 1). `/brain/query` already retrieves from the graph BEFORE
// calling the model — `link_entities` (BM25, §6.5) → `build_context` → LLM — but until T-34 this
// panel showed only the prose answer, so the retrieval step was invisible and the answer read like
// the model talking unprompted. It now renders the pipeline as three stages, in the order they
// actually run: **matched entities** (named + highlighted on the canvas, not just colour), **the
// answer**, **its citations**. A decline is shown as a decline, not an empty box — `link_entities`
// returning nothing is a deliberate anti-hallucination feature (§6.5: it used to hand back the
// least-bad node instead of admitting no match), and hiding that behind a generic error message is
// what made it read as a bug in every demo instead of the feature it is.
export function AskPanel({
  onAnswer,
  autoAsk,
  graph,
}: {
  onAnswer: (a: BrainAnswer | null) => void;
  autoAsk?: { q: string; n: number }; // q + nonce so the demo can re-ask the same question
  /** For labelling `matched_entities` (bare node ids on the wire) in the panel. Optional so this
   *  component still works, degraded to ids, before the graph query has resolved. */
  graph?: BrainGraph;
}) {
  const [q, setQ] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  // The full answer/refusal object, kept even when `grounded` is false — a refusal after a match
  // (e.g. an ungrounded-quantity claim-check failure) still has `matched_entities` worth showing;
  // only a network/gateway failure has nothing to show at all (`fetchError`, below).
  const [result, setResult] = React.useState<BrainAnswer | null>(null);
  // N-14: a cross-source answer (the computed table) and a refusal with its candidates are their
  // own states — they used to be cast to `BrainAnswer`, and reading `matched_entities` off a
  // refusal threw, so every candidate list surfaced as "Query failed".
  const [delegated, setDelegated] = React.useState<DelegatedAnswer | null>(null);
  const [refused, setRefused] = React.useState<BrainRefused | null>(null);
  const [fetchError, setFetchError] = React.useState<string | null>(null);
  const { pack } = usePack();
  const [suggested, setSuggested] = React.useState<string[]>([...pack.brain_suggested_fallback]);
  const [scopeNote, setScopeNote] = React.useState<string>("");

  const nodeById = React.useMemo(
    () => new Map((graph?.nodes ?? []).map((n) => [n.id, n])),
    [graph],
  );

  // Config-driven, so the questions on screen and the assistant's persona cannot drift apart.
  React.useEffect(() => {
    let cancelled = false;
    fetch(`${GATEWAY}/brain/suggested`, { cache: "no-store", headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (cancelled || !body?.questions?.length) return;
        setSuggested(body.questions.slice(0, 4));
        setScopeNote(body.scope_note ?? "");
      })
      .catch(() => {/* keep the fallback — a linked page must not blank on a fetch failure */});
    return () => {
      cancelled = true;
    };
  }, []);

  async function ask(question: string) {
    if (!question.trim() || loading) return;
    setQ(question);
    setLoading(true);
    setFetchError(null);
    try {
      const res: BrainQueryResult = await brainApi.query(question);
      setDelegated(null);
      setRefused(null);
      if ("refusal" in res && typeof res.refusal === "object" && res.refusal !== null) {
        setResult(null);
        setRefused(res as BrainRefused);
        onAnswer(null);
        return;
      }
      if ((res as DelegatedAnswer).routed_to === "federated") {
        setResult(null);
        setDelegated(res as DelegatedAnswer);
        onAnswer(null);
        return;
      }
      const a = res as BrainAnswer;
      setResult(a);
      // Highlight whatever retrieval actually matched — including a match that then got refused
      // downstream (e.g. claim_check rejecting an ungrounded quantity). Only a true empty match
      // clears the canvas; showing a highlighted node next to "declined to answer" is the pipeline
      // being honest about where it stopped, not a bug.
      onAnswer(a.matched_entities.length > 0 ? a : null);
    } catch {
      setResult(null);
      setDelegated(null);
      setRefused(null);
      setFetchError("Query failed — is the gateway running?");
      onAnswer(null);
    } finally {
      setLoading(false);
    }
  }

  // /demo autoplay: when the overlay sets `autoAsk` (q + nonce), run that question automatically.
  React.useEffect(() => {
    if (autoAsk?.q) ask(autoAsk.q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoAsk?.n]);

  return (
    <div className="flex h-full flex-col gap-3">
      <h3 className="text-2xs font-semibold uppercase tracking-wider text-subtle">Ask the brain</h3>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(q);
        }}
        className="relative"
      >
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask about a record, a document, or a total across sources…"
          aria-label="Ask the brain"
          className="h-10 w-full rounded border border-border bg-bg pl-9 pr-10 text-sm text-fg outline-none transition-colors placeholder:text-subtle focus:border-accent"
        />
        <button
          type="submit"
          disabled={loading}
          aria-label="Ask"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded bg-accent p-1.5 text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <ArrowRight className="h-4 w-4" />
        </button>
      </form>

      {/* The suggestions are a way IN, not a permanent fixture. Once an answer exists they are
          costing the answer ~60px of the panel's height, which is how a correct reply ended up
          rendered into a two-line window. They come back the moment the panel is cleared. */}
      <div
        className={cn(
          "flex flex-wrap gap-1.5",
          (result !== null || delegated !== null || refused !== null) && "hidden",
        )}
      >
        {suggested.map((s) => (
          <button
            key={s}
            onClick={() => ask(s)}
            disabled={loading}
            className="rounded-full border border-border bg-surface px-2.5 py-1 text-2xs text-subtle transition-colors hover:border-accent/40 hover:text-fg disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="text-xs text-subtle">Querying the graph…</div>
        ) : fetchError !== null ? (
          <div className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
            <span className="text-xs text-fg">{fetchError}</span>
          </div>
        ) : delegated ? (
          <DelegatedAnswerView answer={delegated} />
        ) : refused ? (
          <div className="space-y-2" data-testid="brain-refusal">
            <div className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
              <span className="text-xs text-fg">{refused.refusal.message}</span>
            </div>
            {(refused.refusal.candidate_details ?? []).length > 0 ? (
              <div className="space-y-1">
                <span className="text-2xs uppercase tracking-wide text-subtle">
                  Records it could mean — nothing was chosen
                </span>
                <ul className="space-y-1">
                  {(refused.refusal.candidate_details ?? []).map((d) => (
                    <li
                      key={d.id}
                      className="flex flex-wrap items-center gap-2 rounded border border-border bg-surface px-2 py-1 text-2xs text-fg"
                    >
                      <Grounded pointer={d.pointers?.[0] ?? null}>
                        <span className="font-medium">{d.label ?? d.id}</span>
                      </Grounded>
                      {d.kind ? <span className="text-subtle">{d.kind}</span> : null}
                      {d.source_ids?.length ? (
                        <span className="text-subtle">from {d.source_ids.join(", ")}</span>
                      ) : null}
                      {d.subject ? (
                        <span className="text-band-review">candidate — a human selects</span>
                      ) : null}
                      <span className="font-mono text-subtle">{d.id}</span>
                      <button
                        type="button"
                        onClick={() => ask(`${q} (${d.id})`)}
                        disabled={loading}
                        className="ml-auto rounded border border-border px-1.5 py-0.5 text-2xs text-accent hover:border-accent/40 disabled:opacity-50"
                      >
                        Ask about this record
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (refused.refusal.candidates ?? []).length > 0 ? (
              <p className="text-2xs text-subtle">
                Candidates: {(refused.refusal.candidates ?? []).join(", ")}
              </p>
            ) : null}
            <RouteNote route={refused.route} />
          </div>
        ) : result ? (
          result.matched_entities.length === 0 ? (
            // `link_entities` found nothing above its BM25 floor — a deliberate decline (§6.5), not
            // an empty answer. It used to hand back the least-bad node rather than admit no match;
            // this is what showing the difference looks like.
            <div className="space-y-2">
              <div className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2">
                <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
                <span className="text-xs text-fg">No entity in this graph matches that question.</span>
              </div>
              <DelegationNote route={result.route} attempt={result.delegation_attempt} />
            </div>
          ) : (
            <div className="space-y-3">
              {/* Stage 1 — what retrieval matched, BEFORE the model saw anything. Named, not just
                  the coloured highlight on the canvas: a colour alone doesn't say "package" vs
                  "coverage rule", and this is the one place that legend actually gets read. */}
              <div className="space-y-1">
                <span className="text-2xs uppercase tracking-wide text-subtle">Matched</span>
                <div className="flex flex-wrap gap-1.5">
                  {result.matched_entities.map((id) => {
                    const n = nodeById.get(id);
                    const style = n ? nodeStyle(n.kind) : null;
                    return (
                      <span
                        key={id}
                        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2 py-0.5 text-2xs text-fg"
                      >
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full"
                          style={{ background: style?.color ?? "hsl(var(--subtle))" }}
                        />
                        {n?.label ?? id}
                      </span>
                    );
                  })}
                </div>
              </div>

              {/* Stage 2 — the answer, or the reason retrieval matched something but still declined
                  (e.g. a claim_check failure). Either way this is the model's turn, which only
                  happens after stage 1 above — that ordering is the point of this panel. */}
              {result.grounded && result.answer ? (
                <p className="text-sm leading-relaxed text-fg">{result.answer}</p>
              ) : (
                <div className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2">
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
                  <span className="text-xs text-fg">
                    {result.refusal || "No grounded answer for that question."}
                  </span>
                </div>
              )}

              <RouteNote route={result.route} />
              <DelegationNote route={result.route} attempt={result.delegation_attempt} />

              {/* Stage 3 — citations. */}
              {result.citations.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-2xs text-subtle">Cited:</span>
                  {result.citations.map((c) => (
                    // each chip = the source document; click → popover with the SourcePointer (D-FE-1)
                    <Grounded key={c.entity_id} pointer={c.pointers[0] ?? null}>
                      <span
                        className={cn(
                          "rounded border border-accent/30 bg-accent/[0.08] px-2 py-0.5 text-2xs text-accent",
                        )}
                      >
                        {c.label}
                      </span>
                    </Grounded>
                  ))}
                </div>
              ) : null}
            </div>
          )
        ) : (
          <div className="text-xs text-subtle">
            Ask a question or pick one above — answers cite the exact source.
          </div>
        )}
      </div>
    </div>
  );
}
