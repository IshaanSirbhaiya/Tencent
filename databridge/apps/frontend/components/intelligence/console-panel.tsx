"use client";
import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, CornerDownLeft, EyeOff, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { intakeApi } from "@/lib/intake-api";
import { useStaffSession } from "@/lib/staff-session";
import {
  citedEntity,
  consoleApi,
  type AgentAsk,
  type AnswerValue,
  type ConsoleResponse,
} from "@/lib/intelligence-api";
import type { ActionCard as ActionCardData, ExecutionResult } from "@/lib/outbox-api";
import { ActionCard } from "@/components/intelligence/action-card";
import { TryChips } from "@/components/try-chips";
import { INTELLIGENCE_TRIALS } from "@/components/try-prompts";

/**
 * The request box — the whole point of this page.
 *
 * ## Three answers to one box
 *
 * A person types what they want. The gateway decides, **deterministically**, which of two things
 * that was, and returns one response describing everything that happened:
 *
 * 1. **an answer** — grounded values, each beside the pointer it came from, each walkable to the
 *    subgraph behind it;
 * 2. **proposed actions** — drafts in the outbox, each with what it would do, to whom, why, and the
 *    evidence behind every field. Nothing has been sent, and nothing on this page can send;
 * 3. **figures**, when the request asked for figures — one possible answer among the three, not the
 *    permanent content of the screen.
 *
 * The router is `databridge_agent.screen_question`: the same pattern match that already refuses a
 * request to send. A request it calls send-shaped is exactly a request that implies an act, so it
 * goes to the propose path. No model decides which half runs, and nothing a document could write
 * influences it.
 *
 * ## Why there is a registration field beside the box
 *
 * The agent may never choose who a message is about — that decision *is* an identity
 * determination. So a request that implies an act is acted on for the registration a **person**
 * named, and with none named the console drafts nothing and says why. The field is not a
 * convenience; it is where the constraint lives on this screen.
 *
 * No client-side computation, per the design law: every string below is this file's own copy or a
 * field the gateway rendered.
 */
export function ConsolePanel({
  asOf,
  onCite,
  onFigures,
}: {
  asOf: string;
  /** Hand a cited entity to the page's subgraph drawer — the same drill-down the figures use. */
  onCite: (members: string[], title: string) => void;
  /** Hand the figures back up, so the page renders them in the same place the button does. */
  onFigures: (analytics: ConsoleResponse["analytics"]) => void;
}) {
  const { staff } = useStaffSession();
  const [request, setRequest] = React.useState("");
  const [intakeId, setIntakeId] = React.useState("");
  const [typedAddress, setTypedAddress] = React.useState("");
  const [cards, setCards] = React.useState<ActionCardData[]>([]);
  const [outcome, setOutcome] = React.useState<ExecutionResult | null>(null);

  // The queue, so naming a registration is a choice from a list rather than a string to retype.
  // Read-only, and it carries no candidate names — the same payload `/frontdesk` renders.
  const queue = useQuery({
    queryKey: ["intake-queue-for-console"],
    queryFn: () => intakeApi.queue(),
    refetchInterval: 15_000,
  });

  const ask = useMutation({
    mutationFn: (text: string) =>
      consoleApi.request({
        request: text,
        as_of: asOf || null,
        intake_id: intakeId.trim() || null,
        typed_address: typedAddress.trim() || null,
        // Door (b) of the recipient rule needs a NAME, not just an address: an arbitrary address
        // is fine as long as a person owns having entered it, and the record then says who did.
        typed_by: staff ? `${staff.name} (${staff.staffId})` : null,
      }),
    onSuccess: (result) => {
      setCards(result.proposed ?? []);
      setOutcome(null);
      onFigures(result.analytics ?? null);
    },
  });

  const result = ask.data as ConsoleResponse | undefined;
  const needsIntake = Boolean(result?.notes?.some((n) => n.key === "needs_an_intake"));

  // One path in, whether a person typed it or picked it. A chip that only filled the box would be
  // a hint, not a trial — the box would still need a second click nobody knows to make.
  function run(text: string) {
    const trimmed = text.trim();
    if (!trimmed || ask.isPending) return;
    setRequest(trimmed);
    ask.mutate(trimmed);
  }

  const onCardChanged = React.useCallback(
    (updated: ActionCardData, executed?: ExecutionResult) => {
      setCards((prev) => prev.map((c) => (c.draft_id === updated.draft_id ? updated : c)));
      if (executed) setOutcome(executed);
    },
    [],
  );

  return (
    <section className="flex flex-col gap-4">
      <form
        className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-4"
        onSubmit={(e) => {
          e.preventDefault();
          run(request);
        }}
      >
        <label className="text-sm font-semibold" htmlFor="console-request">
          Ask for something
        </label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
            <input
              id="console-request"
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              maxLength={500}
              placeholder="Which issuers sent documents, and what is still blocked?"
              data-testid="console-input"
              className="w-full rounded border border-border bg-bg py-1.5 pl-8 pr-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
            />
          </div>
          <button
            type="submit"
            disabled={ask.isPending || !request.trim()}
            data-testid="console-submit"
            className="flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-accent/60 disabled:opacity-40"
          >
            <CornerDownLeft className="h-3.5 w-3.5" />
            {ask.isPending ? "Working…" : "Go"}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="text-2xs text-subtle" htmlFor="console-intake">
            about registration
          </label>
          <select
            id="console-intake"
            value={intakeId}
            onChange={(e) => setIntakeId(e.target.value)}
            data-testid="console-intake"
            className={cn(
              "rounded border bg-bg px-2 py-1 font-mono text-2xs text-fg focus:outline-none",
              needsIntake ? "border-band-spot" : "border-border",
            )}
          >
            <option value="">— none —</option>
            {(queue.data?.intakes ?? []).map((row) => (
              <option key={row.intake_id} value={row.intake_id}>
                {row.intake_id} · {row.document_type}
              </option>
            ))}
          </select>
          <input
            value={typedAddress}
            onChange={(e) => setTypedAddress(e.target.value)}
            placeholder="or type an address, if a clinic rule asks a human to"
            aria-label="An address a member of staff types"
            data-testid="console-typed-address"
            className="flex-1 rounded border border-border bg-bg px-2 py-1 text-2xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
          />
        </div>

        {/* The three tiers, as three labelled rows — the same pills `/brain` uses. Unlike `/brain`
            these stay on screen after an answer: this form sits above the results rather than
            sharing a fixed-height panel with them, and the whole point of the row is that a visitor
            clicks one from each tier and sees three different behaviours out of one box. */}
        <TryChips
          groups={INTELLIGENCE_TRIALS}
          onPick={(chip) => run(chip.payload)}
          disabled={ask.isPending}
          testId="console-try-chips"
          className="pt-1"
        />

        <p className="text-2xs text-subtle">
          Reading happens immediately. Anything that would act on the world becomes a{" "}
          <span className="text-fg">draft</span> a named person approves — the agent proposes and
          performs nothing, which is what makes it safe enough to point at a clinic. Naming the
          registration is required for that half: choosing which patient a message concerns is an
          identity determination, and this system does not make one.
        </p>
      </form>

      {ask.isError && (
        <p className="text-2xs text-band-review" role="alert">
          Could not reach the gateway: {(ask.error as Error).message}
        </p>
      )}

      {result?.refusal && <Refused code={result.refusal.code} message={result.refusal.message} />}

      {result?.answer?.refused && (
        <Refused code={result.answer.refusal_code} message={result.answer.answer} />
      )}

      {result?.answer && !result.answer.refused && (
        <Answer result={result.answer} onCite={onCite} />
      )}

      {cards.length > 0 && (
        <div className="flex flex-col gap-3" data-testid="proposed-actions">
          <h2 className="text-sm font-semibold">Proposed &mdash; nothing has happened yet</h2>
          {cards.map((card) => (
            <ActionCard key={card.draft_id} card={card} onChanged={onCardChanged} />
          ))}
        </div>
      )}

      {outcome && (
        <div
          className="flex flex-col gap-1 rounded-lg border border-accent/50 bg-surface px-4 py-3"
          data-testid="console-outcome"
        >
          <h2 className="text-sm font-semibold">It really happened</h2>
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5">
            <dt className="text-2xs uppercase tracking-wide text-subtle">to</dt>
            <dd className="tnum text-xs text-fg">{outcome.recipient_masked}</dd>
            <dt className="text-2xs uppercase tracking-wide text-subtle">message id</dt>
            <dd className="break-all font-mono text-2xs text-fg">{outcome.message_id}</dd>
            <dt className="text-2xs uppercase tracking-wide text-subtle">at</dt>
            <dd className="tnum text-xs text-fg">{outcome.sent_at}</dd>
            <dt className="text-2xs uppercase tracking-wide text-subtle">approved by</dt>
            <dd className="text-xs text-fg">{outcome.approved_by}</dd>
            <dt className="text-2xs uppercase tracking-wide text-subtle">performed by</dt>
            <dd className="font-mono text-2xs text-fg">{outcome.executed_by}</dd>
          </dl>
          <p className="text-2xs text-subtle">{outcome.note}</p>
        </div>
      )}

      {(result?.blocked ?? []).length > 0 && (
        <div className="flex flex-col gap-2" data-testid="blocked-proposals">
          <h2 className="text-sm font-semibold">Not drafted, and why</h2>
          {result!.blocked.map((blocked, i) => (
            <div
              key={`${blocked.code}-${blocked.playbook_id}-${i}`}
              className="rounded-lg border border-dashed border-border px-4 py-3"
            >
              <p className="font-mono text-2xs uppercase tracking-wide text-subtle">
                {blocked.code}
                {blocked.playbook_id ? ` · ${blocked.playbook_id}` : ""}
              </p>
              {blocked.title && <p className="mt-0.5 text-xs text-fg">{blocked.title}</p>}
              <p className="mt-0.5 text-2xs text-subtle">{blocked.message}</p>
            </div>
          ))}
        </div>
      )}

      {(result?.notes ?? []).map((note) => (
        <p key={note.key} className="text-2xs text-subtle" data-testid={`console-note-${note.key}`}>
          {note.text}
        </p>
      ))}
    </section>
  );
}

function Refused({ code, message }: { code: string; message: string }) {
  // A refusal is an ANSWER — full width, in its own words, not a red toast in a corner. The code
  // is shown because a refusal nobody can name is a refusal nobody can check.
  return (
    <div
      role="status"
      data-testid="console-refusal"
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

/**
 * The grounded answer — a list of values with their citations, never a paragraph.
 *
 * The backend's agent writes no sentence: it returns references, and the server prints each value
 * beside the `SourcePointer` that value itself carries. So this renders rows, and each citation is
 * a button, because a citation you cannot walk to is indistinguishable from a literal.
 *
 * A withheld value — the backend replaces instruction-shaped text with a fixed line and keeps the
 * citation — is styled as deliberate, with a working link. Not as an empty cell.
 */
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
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4" data-testid="console-answer">
      <ul className="flex flex-col divide-y divide-border/60" data-testid="console-values">
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
                      value.redacted ? "flex items-start gap-1 italic text-band-spot" : "text-fg",
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
          <ul className="mt-1 flex flex-col gap-0.5" data-testid="console-unresolved">
            {result.unresolved.map((name) => (
              <li key={name} className="font-mono text-2xs text-band-spot">
                {name}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.notes.map((note) => (
        <p key={note.key} className="text-2xs text-subtle" data-testid={`answer-note-${note.key}`}>
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
            data-testid="console-cite-all"
          >
            open every entity this answer cited
          </button>
        )}
      </footer>
    </div>
  );
}

/** The values of one tool row, in the order the backend reported them. Grouping only — no value is
 *  combined, recomputed or re-derived. */
function groupByRead(values: AnswerValue[]) {
  const groups: { key: string; tool: string; firstOfTool: boolean; values: AnswerValue[] }[] = [];
  for (const value of values) {
    const key = `${value.tool}[${value.row}]`;
    const last = groups[groups.length - 1];
    if (last?.key === key) last.values.push(value);
    else
      groups.push({ key, tool: value.tool, firstOfTool: last?.tool !== value.tool, values: [value] });
  }
  return groups;
}

/** The evidence for one row, as buttons into the same subgraph drawer the figures use. A pointer of
 *  any other shape is printed as text rather than linked hopefully — a citation that 404s is worse
 *  than one that never claimed to be a link. */
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
          data-testid={`console-citation-${entity}`}
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
