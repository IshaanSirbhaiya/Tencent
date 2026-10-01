"use client";
import Link from "next/link";
import { Lock } from "lucide-react";
import type { QueueSummary } from "@/lib/intelligence-api";
import { useNouns } from "@/lib/pack-context";

/**
 * What the intake queue is waiting on, and on what. Shown only when an intake queue is mounted.
 *
 * This is the one block on the page that is not a graph traversal: it is counted over the intakes
 * actually held by the gateway, and `blocked_because` is read straight from the confirm gate
 * (`ConfirmationState.is_ready_to_commit`) rather than re-derived — a second definition of
 * "blocked" would drift from the gate, and the gate is the one that decides.
 *
 * **It reports a queue; it never works it.** `candidate_count` says how many rows a
 * document matched. There is no name, no ordering and no "most likely" here, because the backend
 * does not send one: staff check identity in person, and a dashboard that pre-selected for them
 * would be automating exactly the step this system refuses to automate
 * (CLAUDE.md §Hard constraints 1).
 */
export function BlockedQueue({ queue }: { queue: QueueSummary }) {
  const nouns = useNouns();
  if (queue.intakes === 0) {
    return (
      <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-xs text-subtle">
        No intakes read yet. Read a document from <code>/intake</code> and this queue fills from it
        — there is nothing hardcoded here to show in the meantime.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-2xs text-subtle">
        <span className="tnum font-medium text-fg">{queue.blocked_count}</span> of{" "}
        <span className="tnum">{queue.intakes}</span> intake
        {queue.intakes === 1 ? "" : "s"} cannot commit yet;{" "}
        <span className="tnum">{queue.committed}</span> committed.
      </p>
      {queue.blocked.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-2xs text-subtle">
          Nothing is waiting on a human right now.
        </p>
      ) : (
        <table className="w-full text-left text-xs" data-testid="blocked-queue">
          <thead className="text-2xs uppercase tracking-wide text-subtle">
            <tr className="border-b border-border">
              <th className="py-1.5 pr-2 font-medium">Document</th>
              <th className="px-2 py-1.5 font-medium">Waiting on</th>
              <th className="px-2 py-1.5 font-medium">Candidates in the {nouns.registry}</th>
              <th className="px-2 py-1.5 font-medium">Coverage</th>
            </tr>
          </thead>
          <tbody>
            {queue.blocked.map((b) => (
              <tr key={b.intake_id} className="border-b border-border/60">
                <td className="py-2 pr-2">
                  <Link
                    href={`/frontdesk/${encodeURIComponent(b.intake_id)}`}
                    className="text-accent hover:underline"
                  >
                    {b.document_id}
                  </Link>
                  <span className="block text-2xs text-subtle">
                    {b.document_type.replace(/_/g, " ")}
                  </span>
                </td>
                <td className="px-2 py-2">
                  <span className="flex items-start gap-1.5 text-band-review">
                    <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {b.blocked_because}
                  </span>
                </td>
                <td className="tnum px-2 py-2">
                  {/* HOW MANY, never WHICH. */}
                  {b.candidate_count}
                  <span className="ml-1 text-2xs text-subtle">
                    {b.candidate_count === 1 ? "candidate" : "candidates"} — a person selects, in person
                  </span>
                </td>
                <td className="px-2 py-2 text-subtle">
                  {b.coverage_status.replace(/_/g, " ")}
                  {b.eligibility_flags.length > 0 && (
                    <span className="block text-2xs text-band-spot">
                      {b.eligibility_flags.map((f) => f.replace(/_/g, " ")).join(", ")}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
