"use client";
import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Check, EyeOff, Send, ShieldCheck, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { describeValueState, isValueState } from "@/lib/value-state";
import { DevTokenMark, useWhoAmI } from "@/components/auth-chip";
import {
  citationText,
  flattenCitations,
  outboxApi,
  type ActionCard as ActionCardData,
  type ExecutionResult,
} from "@/lib/outbox-api";

/**
 * One proposed action, and the two decisions a human may make about it.
 *
 * ## The beat this card exists for
 *
 * The agent proposed something. Nothing has happened. A named person reads it, and *then* it
 * happens. Every design choice below serves that sequence being legible:
 *
 * * **The state is the loudest thing on the card.** `drafted` / `approved` / `rejected` / `sent`
 *   is a chip at the top, not a subtitle, because the whole safety argument is which of those four
 *   a row is in.
 * * **The message is shown in full before either button.** A body behind a "show more" is a body
 *   nobody read, and an approval on an unread body is a signature on a blank page.
 * * **The evidence sits under the message, not behind a hover.** `because` is the load-bearing
 *   field of the whole outbox design: it is why this draft exists, and it is what makes the
 *   proposal arguable rather than oracular.
 * * **The recipient is a masked mailbox plus a sentence about how it was established.** That
 *   sentence is the part that matters — "the address already on file for the subject a
 *   named member of staff selected in person" is a claim somebody can be held to, and a mailbox
 *   printed on a shared screen is not something this page is entitled to do
 *   (`intelligence_surface`). The full address is on the intake, where the person is.
 *
 * ## What the buttons do not do
 *
 * **Approve does not send.** It records that a named person agreed, in an append-only store a
 * different process reads. **Execute** starts that process; the gateway holds no mail credentials
 * and imports no transport. So the two buttons are two different acts by two different actors, and
 * the card says which is which rather than collapsing them into one "confirm".
 *
 * **The approver is the signed-in principal, minted by the gateway.** This card sends no name: the
 * gateway records the principal it authenticated the request as, and refuses a body that names an
 * approver (R-027 ONE, Codex round 1). The name shown beside the buttons is read from
 * `GET /auth/whoami`, the same source the gateway uses, and a dev-token sign-in is marked as such.
 */
export function ActionCard({
  card,
  onChanged,
}: {
  card: ActionCardData;
  /** Hand the updated row back to the console so the list and the result view stay in step. */
  onChanged: (updated: ActionCardData, result?: ExecutionResult) => void;
}) {
  const me = useWhoAmI();
  const signedIn = me.data?.principal_id ?? null;
  const [reason, setReason] = React.useState("");
  const [rejecting, setRejecting] = React.useState(false);
  const [refusal, setRefusal] = React.useState<{ code: string; message: string } | null>(null);
  // R-028 #12: withheld text arrives as the standard envelope, never as a redaction string.
  const withheld = isValueState(card.subject) || isValueState(card.body);

  const approve = useMutation({
    mutationFn: () => outboxApi.approve(card.draft_id),
    onSuccess: (updated) => {
      // A refusal arrives in the body with a 4xx, not as a thrown error: "this has not been
      // approved" is an answer, and an answer belongs on the card rather than in a toast.
      if (updated.refusal) setRefusal(updated.refusal);
      else {
        setRefusal(null);
        onChanged(updated);
      }
    },
  });

  const reject = useMutation({
    mutationFn: () => outboxApi.reject(card.draft_id, reason.trim()),
    onSuccess: (updated) => {
      if (updated.refusal) setRefusal(updated.refusal);
      else {
        setRefusal(null);
        setRejecting(false);
        onChanged(updated);
      }
    },
  });

  const execute = useMutation({
    mutationFn: () => outboxApi.execute(card.draft_id),
    onSuccess: (out) => {
      if (out.refusal) setRefusal(out.refusal);
      else if (out.draft) {
        setRefusal(null);
        onChanged(out.draft, out.result);
      }
    },
  });

  const pending = approve.isPending || reject.isPending || execute.isPending;
  const citations = flattenCitations(card.because);

  return (
    <article
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4"
      data-testid={`action-card-${card.draft_id}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <StatusChip status={card.status} />
            <span className="font-mono text-2xs text-subtle">{card.playbook_id}</span>
          </div>
          <h3 className="mt-1 text-sm font-semibold">{card.title ?? card.playbook_id}</h3>
          {card.when && <p className="mt-0.5 max-w-2xl text-2xs text-subtle">{card.when}</p>}
        </div>
        {card.escalate_to && (
          <span className="rounded border border-border px-2 py-0.5 font-mono text-2xs text-subtle">
            {card.escalate_to}
          </span>
        )}
      </header>

      {/* WHO, and how that was decided. The sentence is the point; the mailbox is masked. */}
      <section className="rounded border border-border/70 bg-bg px-3 py-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-2xs uppercase tracking-wide text-subtle">to</span>
          <span className="tnum text-xs text-fg" data-testid="recipient-masked">
            {card.recipient.masked}
          </span>
          <span className="font-mono text-2xs text-subtle">{card.recipient.handle}</span>
          <span className="font-mono text-2xs text-accent">{card.recipient.origin}</span>
        </div>
        <p className="mt-1 text-2xs text-subtle">{card.recipient.established}</p>
        <p className="mt-1 text-2xs text-subtle">
          Established by <span className="text-fg">{card.recipient.chosen_by}</span>. The mailbox
          itself is masked here: this screen counts and proposes, and the address is visible on the
          intake, where the person is.
        </p>
      </section>

      {/* WHAT it would say — in full, above the buttons. */}
      <section className="flex flex-col gap-1">
        <p
          className={cn("text-xs font-medium", typeof card.subject === "string" ? "text-fg" : "italic text-band-spot")}
          data-testid="action-subject"
        >
          {typeof card.subject === "string" ? card.subject : describeValueState(card.subject)}
        </p>
        <p
          className={cn(
            "whitespace-pre-line text-xs",
            withheld ? "flex items-start gap-1 italic text-band-spot" : "text-subtle",
          )}
          data-testid="action-body"
        >
          {withheld && <EyeOff className="mt-0.5 h-3 w-3 shrink-0" />}
          {typeof card.body === "string" ? card.body : describeValueState(card.body)}
        </p>
        {withheld && (
          <p className="text-2xs text-band-spot">
            This draft&rsquo;s own text is addressed to an automated reader — an instruction inside
            a restricted document. It is not reproduced, and the citations below still resolve, so it
            can be read in place. Rejecting is the expected answer.
          </p>
        )}
      </section>

      {/* WHY it exists. `because` is the load-bearing field of the whole design. */}
      <details className="rounded border border-border/70 px-3 py-2">
        <summary className="cursor-pointer text-2xs uppercase tracking-wide text-subtle">
          why this draft exists
        </summary>
        <ul className="mt-1.5 flex flex-col gap-0.5" data-testid="action-because">
          {citations.map((citation, i) => (
            <li
              key={`${citation.kind}-${citation.source}-${i}`}
              className={cn(
                "font-mono text-2xs",
                citation.repo_config ? "text-accent" : "text-subtle",
              )}
            >
              {citationText(citation)}
            </li>
          ))}
        </ul>
        <p className="mt-1.5 text-2xs text-subtle">
          Lines in the accent colour are cells in a config file in this repository — the policy rule
          that fired, and the row it read. The rest are handles: a document is never named by its
          filename here, because a filename can carry a person&rsquo;s name.
        </p>
      </details>

      {refusal && (
        <p
          role="status"
          data-testid="action-refusal"
          className="flex flex-col gap-1 rounded border border-dashed border-band-spot/50 px-3 py-2"
        >
          <span className="flex items-center gap-1.5 font-mono text-2xs uppercase tracking-wide text-band-spot">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            {refusal.code}
          </span>
          <span className="text-xs text-fg">{refusal.message}</span>
        </p>
      )}

      <Outcome card={card} />

      {/* The two human decisions, and the one machine act that follows them. */}
      <footer className="flex flex-col gap-2 border-t border-border/60 pt-2">
        {!signedIn && !me.isLoading && (
          <p className="text-2xs text-band-spot">
            Sign in before approving. An approval is recorded against the signed-in principal.
          </p>
        )}
        {card.status === "drafted" && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={pending || !signedIn}
              onClick={() => approve.mutate()}
              data-testid="action-approve"
              className="flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-accent/60 disabled:opacity-40"
            >
              <Check className="h-3.5 w-3.5" />
              {approve.isPending ? "Recording…" : "Approve"}
            </button>
            <button
              type="button"
              disabled={pending || !signedIn}
              onClick={() => setRejecting((r) => !r)}
              data-testid="action-reject"
              className="flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-band-review/60 disabled:opacity-40"
            >
              <X className="h-3.5 w-3.5" />
              Reject
            </button>
            {signedIn && (
              <span className="text-2xs text-subtle" data-testid="action-recorded-as">
                will be recorded as <span className="font-mono text-fg">{signedIn}</span>
                {me.data?.dev_only && <DevTokenMark testId="action-dev-token" />}
              </span>
            )}
          </div>
        )}

        {rejecting && card.status === "drafted" && (
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (reason.trim()) reject.mutate();
            }}
          >
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why not? A rejection with no reason teaches the next draft nothing."
              aria-label="Reason for rejecting this draft"
              data-testid="action-reason"
              className="flex-1 rounded border border-border bg-bg px-2 py-1.5 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
            />
            <button
              type="submit"
              disabled={!reason.trim() || pending}
              className="rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-band-review/60 disabled:opacity-40"
            >
              Record rejection
            </button>
          </form>
        )}

        {card.status === "approved" && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={pending}
              onClick={() => execute.mutate()}
              data-testid="action-execute"
              className="flex items-center gap-1.5 rounded border border-accent/60 px-3 py-1.5 text-xs text-fg hover:border-accent disabled:opacity-40"
            >
              <Send className="h-3.5 w-3.5" />
              {execute.isPending ? "Handing to the dispatcher…" : "Hand to the dispatcher"}
            </button>
            <span className="text-2xs text-subtle">
              A separate process with no model in it performs this. Approving did not send it.
            </span>
          </div>
        )}

        <p className="flex items-start gap-1.5 text-2xs text-subtle">
          <ShieldCheck className="mt-0.5 h-3 w-3 shrink-0" />
          An approval is recorded against the principal this page is signed in as, never a name it
          sends. Only the draft&rsquo;s owner or an operator can approve, reject or hand it to the
          dispatcher.
        </p>
      </footer>
    </article>
  );
}

/** What actually happened, once something did. A demo whose send is invisible proves nothing. */
function Outcome({ card }: { card: ActionCardData }) {
  if (card.status === "sent") {
    return (
      <dl
        className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5 rounded border border-accent/40 bg-bg px-3 py-2"
        data-testid="action-outcome"
      >
        <dt className="text-2xs uppercase tracking-wide text-subtle">left at</dt>
        <dd className="tnum text-xs text-fg">{card.sent_at}</dd>
        <dt className="text-2xs uppercase tracking-wide text-subtle">message id</dt>
        <dd className="break-all font-mono text-2xs text-fg">{card.message_id}</dd>
        <dt className="text-2xs uppercase tracking-wide text-subtle">approved by</dt>
        <dd className="text-xs text-fg">{card.approved_by}</dd>
        <dt className="text-2xs uppercase tracking-wide text-subtle">approved at</dt>
        <dd className="tnum text-xs text-fg">{card.approved_at}</dd>
      </dl>
    );
  }
  if (card.status === "approved") {
    return (
      <p className="text-2xs text-subtle" data-testid="action-outcome">
        Approved by <span className="text-fg">{card.approved_by}</span> at{" "}
        <span className="tnum text-fg">{card.approved_at}</span>. Nothing has left the building yet.
      </p>
    );
  }
  if (card.status === "rejected") {
    return (
      <p className="text-2xs text-subtle" data-testid="action-outcome">
        Rejected by <span className="text-fg">{card.rejected_by}</span> at{" "}
        <span className="tnum text-fg">{card.rejected_at}</span> — {card.reason}. The draft stays in
        the record; nothing is deleted.
      </p>
    );
  }
  return null;
}

function StatusChip({ status }: { status: string }) {
  const tone =
    status === "sent"
      ? "border-accent/60 text-accent"
      : status === "approved"
        ? "border-band-spot/60 text-band-spot"
        : status === "rejected"
          ? "border-band-review/60 text-band-review"
          : "border-border text-subtle";
  return (
    <span
      className={cn("rounded border px-2 py-0.5 font-mono text-2xs uppercase tracking-wide", tone)}
      data-testid="action-status"
    >
      {status}
    </span>
  );
}
