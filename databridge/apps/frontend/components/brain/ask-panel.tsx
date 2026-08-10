"use client";
import * as React from "react";
import { ArrowRight, Search, ShieldAlert } from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import type { BrainAnswer } from "@/lib/brain-types";
import { Grounded } from "@/components/grounded";
import { cn } from "@/lib/utils";

const SUGGESTED = [
  "What did Sarah decide about BigClient?",
  "Who owns Project Atlas?",
  "What's blocking the Data Migration?",
];

// Column 1 — Ask the Brain (§Part 1). On answer, the parent highlights the cited nodes + animates
// the connecting edges. Every value in the answer is grounded (claim_check on the backend); an
// ungrounded / no-match question is REFUSED (the gateway returns a refusal object) and shown as
// such — never a fabricated answer.
export function AskPanel({
  onAnswer,
  autoAsk,
}: {
  onAnswer: (a: BrainAnswer | null) => void;
  autoAsk?: { q: string; n: number }; // q + nonce so the demo can re-ask the same question
}) {
  const [q, setQ] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [answer, setAnswer] = React.useState<BrainAnswer | null>(null);
  const [refusal, setRefusal] = React.useState<string | null>(null);

  async function ask(question: string) {
    if (!question.trim() || loading) return;
    setQ(question);
    setLoading(true);
    setRefusal(null);
    try {
      const res = await brainApi.query(question);
      // grounded answers come back with grounded=true; a refusal comes back as {refusal:{message}}
      const a = res as BrainAnswer;
      const refusalRaw = (res as { refusal?: unknown }).refusal;
      const refusalMsg = typeof refusalRaw === "string" ? refusalRaw : (refusalRaw as { message?: string })?.message;
      if (a.grounded && a.answer) {
        setAnswer(a);
        onAnswer(a);
      } else {
        setAnswer(null);
        setRefusal(refusalMsg || "No grounded answer for that question.");
        onAnswer(null);
      }
    } catch {
      setRefusal("Query failed — is the gateway running?");
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
          placeholder="Ask anything about Acme Corp…"
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

      <div className="flex flex-wrap gap-1.5">
        {SUGGESTED.map((s) => (
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
        ) : refusal !== null ? (
          <div className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
            <span className="text-xs text-fg">{refusal || "No grounded answer for that question."}</span>
          </div>
        ) : answer ? (
          <div className="space-y-2">
            <p className="text-sm leading-relaxed text-fg">{answer.answer}</p>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-2xs text-subtle">Cited:</span>
              {answer.citations.map((c) => (
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
          </div>
        ) : (
          <div className="text-xs text-subtle">
            Ask a question or pick one above — answers cite the exact source.
          </div>
        )}
      </div>
    </div>
  );
}
