"use client";
/**
 * Pending growth proposals — what a human is waiting on. Shown above the library itself: a queue
 * buried under two card grids is a queue that stops being read.
 */
import * as React from "react";
import { AlertTriangle, Check, X } from "lucide-react";
import type { OntologyProposalRecord } from "@/lib/ontology-api";

export function ProposalQueue({
  proposals,
  decidedBy,
  onDecide,
  busyId,
}: {
  proposals: OntologyProposalRecord[];
  decidedBy: string;
  onDecide: (proposal: OntologyProposalRecord, approve: boolean) => void;
  busyId: string | null;
}) {
  if (!proposals.length) {
    return <p className="text-2xs text-subtle">Nothing pending.</p>;
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="ontology-proposals">
      {proposals.map((p) => {
        const escalated = p.status === "escalated";
        return (
          <li
            key={p.id}
            className={`rounded border px-3 py-2 ${
              escalated ? "border-band-review/40 bg-band-review/[0.05]" : "border-border"
            }`}
            data-testid={escalated ? "proposal-escalated" : "proposal-pending"}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-xs text-fg">{p.name}</span>
                  <span className="text-[10px] uppercase tracking-wider text-subtle">
                    {p.axis} · binds to <code className="text-fg">{p.binds_to}</code>
                  </span>
                  {p.src.length || p.dst.length ? (
                    <span className="font-mono text-[10px] text-subtle">
                      {p.src.join(", ")} → {p.dst.join(", ")}
                    </span>
                  ) : null}
                </div>
                <div className="mt-1 text-2xs text-subtle">
                  proposed by <span className="text-fg">{p.proposed_by}</span>
                  {p.description ? <> — {p.description}</> : null}
                </div>

                {escalated ? (
                  <div className="mt-2 flex items-start gap-1.5">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
                    <div className="text-2xs text-band-review">
                      <span className="font-medium uppercase tracking-wider">escalated</span>{" "}
                      — {p.escalation_reason ?? "touches a determination, a claim or a declared subject"}
                      {p.guardrail_classes.length ? (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {p.guardrail_classes.map((c) => (
                            <span key={c} className="rounded border border-band-review/30 px-1 py-px text-[10px]">
                              {c}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <div className="mt-1 text-2xs text-subtle">
                    In policy — waiting for a decision.
                  </div>
                )}
              </div>

              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  disabled={!decidedBy || busyId === p.id}
                  onClick={() => onDecide(p, true)}
                  className="inline-flex items-center gap-1 rounded border border-band-auto/50 px-2 py-1 text-2xs text-band-auto hover:bg-band-auto/10 disabled:opacity-40"
                >
                  <Check className="h-3 w-3" /> Approve
                </button>
                <button
                  type="button"
                  disabled={!decidedBy || busyId === p.id}
                  onClick={() => onDecide(p, false)}
                  className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg disabled:opacity-40"
                >
                  <X className="h-3 w-3" /> Reject
                </button>
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
