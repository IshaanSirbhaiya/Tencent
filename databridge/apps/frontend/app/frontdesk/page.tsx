"use client";
/**
 * `/frontdesk` — the queue.
 *
 * What a front-desk supervisor needs at a glance is not "how many intakes" but "how many are
 * BLOCKED, and on what". So the columns are the gate state and the conflicts, and the two rows
 * that need a human decision (ambiguous identity, undetermined coverage) are the ones that stand
 * out — not the ones that processed cleanly.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Users } from "lucide-react";
import { intakeApi } from "@/lib/intake-api";
import type { QueueRow } from "@/lib/intake-types";
import { cn } from "@/lib/utils";

function IdentityCell({ row }: { row: QueueRow }) {
  if (row.identity_verified) {
    // "attested by staff", NOT "verified". The system records that a named person said they
    // checked; it does not and cannot assert that the check was correct. The first draft of this
    // cell read "verified in person" with a green tick — the exact claim the whole architecture
    // exists to avoid making, in the one place a supervisor scans fastest.
    return (
      <span className="inline-flex items-center gap-1 text-fg">
        <CheckCircle2 className="h-3.5 w-3.5 text-accent" /> attested by staff
      </span>
    );
  }
  if (row.identity_status === "multiple_candidates") {
    return (
      <span className="inline-flex items-center gap-1 text-band-review">
        <Users className="h-3.5 w-3.5" /> {row.candidate_count} candidates — staff must choose
      </span>
    );
  }
  if (row.identity_status === "insufficient_identifiers" || row.candidate_count === 0) {
    return <span className="text-subtle">no registry match — register at counter</span>;
  }
  return <span className="text-subtle">1 candidate — awaiting in-person check</span>;
}

export default function FrontDeskQueuePage() {
  const q = useQuery({ queryKey: ["intake-queue"], queryFn: intakeApi.queue, refetchInterval: 4000 });
  const rows = q.data?.intakes ?? [];
  // "Still loading" and "nothing to do" are different answers; so is "the gateway is down".
  const emptyBecause = q.isLoading
    ? "Loading the queue…"
    : q.isError
      ? `Could not reach the gateway: ${(q.error as Error).message}`
      : null;
  // The label says "awaiting identity check", so count intakes with NO attestation — not merely
  // uncommitted ones. An intake where staff already checked the ID and selected a candidate is
  // waiting on a click, not on an identity check, and conflating them tells a supervisor that
  // identified patients are unidentified.
  const awaitingIdentity = rows.filter((r) => !r.identity_verified).length;
  const uncommitted = rows.filter((r) => !r.committed).length;
  const conflicts = rows.filter((r) => r.eligibility_flags.length > 0).length;

  return (
    <div className="flex flex-col gap-4 p-6">
      <header>
        <h1 className="text-lg font-semibold">Front desk queue</h1>
        <p className="text-xs text-subtle">
          Patients whose documents have been read. Identity is verified in person at the counter —
          nothing here is confirmed until a staff member attests to that check.
        </p>
      </header>

      <div className="flex gap-3">
        <div className="rounded-lg border border-border bg-surface px-4 py-2">
          <div className="tnum text-lg font-semibold">{rows.length}</div>
          <div className="text-2xs text-subtle">intakes read</div>
        </div>
        <div className="rounded-lg border border-border bg-surface px-4 py-2">
          <div className="tnum text-lg font-semibold text-band-review">{awaitingIdentity}</div>
          <div className="text-2xs text-subtle">awaiting identity check</div>
        </div>
        <div className="rounded-lg border border-border bg-surface px-4 py-2">
          <div className="tnum text-lg font-semibold">{uncommitted}</div>
          <div className="text-2xs text-subtle">not yet committed</div>
        </div>
        <div className="rounded-lg border border-border bg-surface px-4 py-2">
          <div className="tnum text-lg font-semibold text-band-spot">{conflicts}</div>
          <div className="text-2xs text-subtle">with coverage conflicts</div>
        </div>
      </div>

      {emptyBecause ? (
        <p
          role={q.isError ? "alert" : undefined}
          className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-xs text-subtle"
        >
          {emptyBecause}
        </p>
      ) : rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-xs text-subtle">
          No intakes yet. Upload a referring document from{" "}
          <Link href="/intake" className="text-accent hover:underline">
            /intake
          </Link>
          .
        </p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-2xs uppercase tracking-wide text-subtle">
            <tr className="border-b border-border">
              <th className="px-2 py-1.5 font-medium">Document</th>
              <th className="px-2 py-1.5 font-medium">Type</th>
              <th className="px-2 py-1.5 font-medium">Identity</th>
              <th className="px-2 py-1.5 font-medium">Coverage</th>
              <th className="px-2 py-1.5 font-medium">Flags</th>
            </tr>
          </thead>
          <tbody data-testid="queue-rows">
            {rows.map((row) => (
              <tr key={row.intake_id} className="border-b border-border/60 hover:bg-muted/40">
                <td className="px-2 py-2">
                  <Link
                    href={`/frontdesk/${encodeURIComponent(row.intake_id)}`}
                    className="font-medium text-accent hover:underline"
                    data-testid={`queue-link-${row.intake_id}`}
                  >
                    {row.document_id}
                  </Link>
                </td>
                <td className="px-2 py-2 text-subtle">{row.document_type.replace(/_/g, " ")}</td>
                <td className="px-2 py-2">
                  <IdentityCell row={row} />
                </td>
                <td className="px-2 py-2">
                  <span
                    className={cn(
                      row.coverage_status === "determined" ? "text-band-auto" : "text-band-review",
                    )}
                  >
                    {row.coverage_status}
                  </span>
                </td>
                <td className="px-2 py-2">
                  {row.eligibility_flags.length === 0 ? (
                    <span className="text-subtle">—</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-band-review">
                      <AlertTriangle className="h-3 w-3" />
                      {row.eligibility_flags.map((f) => f.replace(/_/g, " ")).join(", ")}
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
