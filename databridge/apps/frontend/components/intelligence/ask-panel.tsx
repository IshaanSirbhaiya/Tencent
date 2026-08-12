"use client";
import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, CornerDownLeft, EyeOff, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  citedEntity,
  intelligenceApi,
  type AgentAsk,
  type AnswerValue,
} from "@/lib/intelligence-api";

/**
 * Ask the graph a question — the agent behind this page (`REMEDIATION.md` §3.1).
 *
 * **The answer is a list, not a paragraph, and that is the product decision.** The backend's agent
 * never writes a sentence: it returns references, and the server prints each value beside the
 * `SourcePointer` that value itself carries. So this panel renders rows — value, then citation —
 * and the citation is a button, because a citation you cannot walk to is indistinguishable from a
 * literal. Clicking one drives the same subgraph drawer the figures on the left drive, which is
 * how a number on this page has always been checked.
 *
 * Three states this panel has to render *deliberately*, because each of them looks like a bug if
 * it is rendered carelessly:
 *
 * 1. **A withheld value.** The backend replaces an instruction-shaped value — a document talking
 *    to an automated reader — with a fixed line, and keeps the citation. That row is styled as a
 *    withheld value with a working link, not as an empty cell: the evidence is one click away and
 *    the payload is not reprinted in the clinic's own UI.
 * 2. **A refusal.** `refused` is the agent's own answer (an identity question, a request to send
 *    something) and arrives with a 200. It renders as the answer it is — full width, its own
 *    text — never as an error toast.
 * 3. **Asked for, not found.** Names, never values. A blank where a figure goes is the failure
 *    mode this whole surface exists not to have.
 *
 * No client-side computation, per the design law: every string below is either this file's own
 * copy or a field the gateway rendered.
 */
export function AskPanel({
  asOf,
  onCite,
}: {
  asOf: string;
  /** Hand a cited entity to the page's subgraph drawer — the same drill-down the figures use. */
  onCite: (members: string[], title: string) => void;
}) {
  const [question, setQuestion] = React.useState("");
  const ask = useMutation({
    mutationFn: (q: string) => intelligenceApi.ask(q, asOf || null),
  });
  const result = ask.data as AgentAsk | undefined;

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
      <header>
        <h2 className="text-sm font-semibold">Ask the graph</h2>
        <p className="text-2xs text-subtle">
          A read-only agent with three structural tools and no way to send anything, name a
          patient, or state a value the graph did not return. Every line it gives back carries the
          pointer it came from — click one to open the traversal behind it.
        </p>
      </header>

      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const q = question.trim();
          if (q) ask.mutate(q);
        }}
      >
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={500}
            placeholder="Which issuers sent the most documents, and what is still blocked?"
            aria-label="Ask the clinic graph a question"
            data-testid="ask-input"
            className="w-full rounded border border-border bg-bg py-1.5 pl-8 pr-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
          />
        </div>
        <button
          type="submit"
          disabled={ask.isPending || !question.trim()}
          data-testid="ask-submit"
          className="flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-accent/60 disabled:opacity-40"
        >
          <CornerDownLeft className="h-3.5 w-3.5" />
          {ask.isPending ? "Reading the graph…" : "Ask"}
        </button>
      </form>

      {ask.isError && (
        <p className="text-2xs text-band-review" role="alert">
          Could not reach the gateway: {(ask.error as Error).message}
        </p>
      )}

      {result?.refusal && <Refused code={result.refusal.code} message={result.refusal.message} />}

      {result && !result.refusal && result.refused && (
        <Refused code={result.refusal_code} message={result.answer} />
      )}

      {result && !result.refusal && !result.refused && (
        <Answer result={result} onCite={onCite} />
      )}

      <p className="text-2xs text-subtle">
        These reads have no date window: they count over every document the graph holds. Use the{" "}
        <span className="text-fg">as of</span> control above to rewind them, and read a question
        about &ldquo;this week&rdquo; accordingly. Nothing here claims minutes or money — that is{" "}
        <code>/impact</code>, measured separately.
      </p>
    </section>
  );
}

function Refused({ code, message }: { code: string; message: string }) {
  // A refusal is an ANSWER — full width, in its own words, not a red toast in a corner. The code
  // is shown because a refusal nobody can name is a refusal nobody can check.
  return (
    <div
      role="status"
      data-testid="ask-refusal"
      className="flex flex-col gap-1 rounded-lg border border-dashed border-band-spot/50 px-4 py-3"
    >
      <span className="flex items-center gap-1.5 font-mono text-2xs uppercase tracking-wide text-band-spot">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        {code}
      </span>
      <p className="whitespace-pre-line text-xs text-fg">{message}</p>
    </div>
  );
}

function Answer({
  result,
  onCite,
}: {
  result: AgentAsk;
  onCite: (members: string[], title: string) => void;
}) {
  const cited = result.values.map(citedEntity).filter((id): id is string => Boolean(id));
  const unique = Array.from(new Set(cited));

  return (
    <div className="flex flex-col gap-3" data-testid="ask-answer">
      <ul className="flex flex-col divide-y divide-border/60" data-testid="ask-values">
        {groupByRead(result.values).map((group) => (
          <li key={group.key} className="py-2">
            {group.firstOfTool && (
              <div className="mb-1 font-mono text-2xs uppercase tracking-wide text-subtle">
                {group.tool.replace(/_/g, " ")}
              </div>
            )}
            <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
              {group.values.map((value) => (
                <React.Fragment key={value.key}>
                  <dt className="font-mono text-2xs text-subtle">{value.field}</dt>
                  <dd
                    className={cn(
                      "tnum text-xs",
                      value.redacted
                        ? "flex items-start gap-1 italic text-band-spot"
                        : "text-fg",
                    )}
                  >
                    {value.redacted && <EyeOff className="mt-0.5 h-3 w-3 shrink-0" />}
                    {value.text}
                  </dd>
                </React.Fragment>
              ))}
            </dl>
            <Citations values={group.values} onCite={onCite} />
          </li>
        ))}
      </ul>

      {result.unresolved.length > 0 && (
        <div>
          <h3 className="text-2xs uppercase tracking-wide text-subtle">Asked for, not found</h3>
          <ul className="mt-1 flex flex-col gap-0.5" data-testid="ask-unresolved">
            {result.unresolved.map((name) => (
              <li key={name} className="font-mono text-2xs text-band-spot">
                {name}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.notes.map((note) => (
        <p key={note.key} className="text-2xs text-subtle" data-testid={`ask-note-${note.key}`}>
          {note.text}
        </p>
      ))}

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/60 pt-2 text-2xs text-subtle">
        <span>
          read{" "}
          {result.reads.map((r) => (
            <code key={r.tool} className="mr-1 text-fg">
              {r.tool}
              {r.status === "ok" ? "" : `:${r.status}`}
            </code>
          ))}
        </span>
        {unique.length > 0 && (
          <button
            type="button"
            onClick={() => onCite(unique, "everything this answer cited")}
            className="text-accent hover:underline"
            data-testid="ask-cite-all"
          >
            open every entity this answer cited
          </button>
        )}
      </footer>
    </div>
  );
}

/** The values of one tool row, in the order the backend reported them.
 *
 *  Grouping only — no value is combined, recomputed or re-derived. It is here because the backend
 *  emits a flat list of `tool[row].field` triples, and a reader needs "this issuer, this count"
 *  side by side rather than two lines apart. */
function groupByRead(values: AnswerValue[]) {
  const groups: {
    key: string;
    tool: string;
    /** Print the tool's name once per run of rows, not once per row. */
    firstOfTool: boolean;
    values: AnswerValue[];
  }[] = [];
  for (const value of values) {
    const key = `${value.tool}[${value.row}]`;
    const last = groups[groups.length - 1];
    if (last?.key === key) last.values.push(value);
    else
      groups.push({
        key,
        tool: value.tool,
        firstOfTool: last?.tool !== value.tool,
        values: [value],
      });
  }
  return groups;
}

/** The evidence for one row, as buttons into the same subgraph drawer the figures use.
 *
 *  Every pointer these tools emit names a graph entity, so a citation is walkable. A pointer of
 *  any other shape is printed as text rather than linked hopefully — a citation that 404s is
 *  worse than one that never claimed to be a link. */
function Citations({
  values,
  onCite,
}: {
  values: AnswerValue[];
  onCite: (members: string[], title: string) => void;
}) {
  const seen = new Map<string, AnswerValue>();
  const plain: AnswerValue[] = [];
  for (const value of values) {
    const entity = citedEntity(value);
    if (entity) {
      if (!seen.has(entity)) seen.set(entity, value);
    } else plain.push(value);
  }
  return (
    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5">
      {Array.from(seen.entries()).map(([entity, value]) => (
        <button
          key={entity}
          type="button"
          onClick={() => onCite([entity], entity)}
          title={value.citation}
          className="text-left font-mono text-2xs text-accent hover:underline"
          data-testid={`ask-citation-${entity}`}
        >
          {entity}
        </button>
      ))}
      {plain.map((value) => (
        <span key={value.key} className="font-mono text-2xs text-subtle">
          {value.citation}
        </span>
      ))}
    </div>
  );
}
