"use client";
/**
 * The approval inbox — every action, across every agent, still waiting on a person.
 *
 * A person sees what they are approving before they approve it: the proposed arguments, the
 * evidence pointers each came from, any value in the arguments that NO cited evidence contains
 * (the runtime's `ungrounded_values`), and — for an autonomous-capable action — how it is undone.
 * Each decision takes two clicks (choose, then confirm). The decision is attributed by the gateway
 * to the signed-in principal; this component never sends a name.
 */
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { agentsApi, approvalBlockers, type DecisionResult, type PendingAction } from "@/lib/agents-api";
import { ErrorLine, Section, StatusPill, approvalInWords, fmtTime } from "./shared";
import { evidenceHref, pointerWhere } from "./run-view";

type Pending = { kind: "approve" | "reject"; key: string } | null;

function ActionCard({
  a,
  onDecided,
}: {
  a: PendingAction;
  onDecided: (r: DecisionResult) => void;
}) {
  const key = `${a.run_id}/${a.action_id}`;
  const [confirm, setConfirm] = React.useState<Pending>(null);
  const [reason, setReason] = React.useState("");
  const decide = useMutation({
    mutationFn: (kind: "approve" | "reject") =>
      kind === "approve" ? agentsApi.approve(a.run_id, a.action_id) : agentsApi.reject(a.run_id, a.action_id, reason),
    onSuccess: (r) => {
      setConfirm(null);
      onDecided(r);
    },
  });
  const tid = `approval-${a.run_id}-${a.action_id}`;
  // Fail closed: an action carrying a value no cited evidence contains, or argument values with no
  // pointer, is never offered for approval (hard constraint 2). It can still be rejected.
  const blockers = approvalBlockers(a);
  const ungrounded = Array.isArray(a.ungrounded_values) ? a.ungrounded_values : [];
  const pointers = Array.isArray(a.derived_from) ? a.derived_from : [];
  return (
    <li data-testid={tid} className="rounded border border-border p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono font-medium text-fg">{a.name}</span>
        <StatusPill status={a.status} />
        <span className="text-subtle">
          {approvalInWords(a.automation_mode, a.reversal)}{" "}
          <span className="font-mono text-2xs">({a.automation_mode})</span>
        </span>
        <span className="flex-1" />
        <span className="text-2xs text-subtle">
          agent <span className="font-mono text-fg">{a.agent ?? "?"}</span> · run{" "}
          <Link
            href={`/builder?tab=agents&agent=${encodeURIComponent(a.agent ?? "")}&run=${encodeURIComponent(a.run_id)}`}
            className="font-mono text-accent hover:underline"
          >
            {a.run_id}
          </Link>{" "}
          · {a.action_id} · proposed {fmtTime(a.proposed_at)}
        </span>
      </div>
      {a.tool && <p className="mt-1 font-mono text-2xs text-subtle">{a.tool}</p>}

      <div className="mt-2">
        <p className="text-2xs font-semibold uppercase tracking-wide text-subtle">Proposed arguments</p>
        <pre data-testid={`${tid}-args`} className="mt-1 max-h-56 overflow-auto rounded bg-muted p-2 font-mono text-2xs text-fg">
          {a.args ? JSON.stringify(a.args, null, 2) : "(none recorded)"}
        </pre>
      </div>

      {ungrounded.length > 0 && (
        <p data-testid={`${tid}-ungrounded`} className="mt-2 rounded border border-band-review/40 bg-band-review/10 p-2 text-2xs text-band-review">
          Not found in any cited evidence: <span className="font-mono">{ungrounded.join(", ")}</span>. A value without a
          source pointer cannot be committed, so this action cannot be approved — reject it.
        </p>
      )}

      <div className="mt-2">
        <p className="text-2xs font-semibold uppercase tracking-wide text-subtle">
          Based on ({pointers.length} pointer{pointers.length === 1 ? "" : "s"})
        </p>
        {pointers.length ? (
          <ul data-testid={`${tid}-pointers`} className="mt-1 flex flex-col gap-0.5 text-2xs">
            {pointers.map((p, i) => (
              <li key={i}>
                <Link href={evidenceHref(p)} className="font-mono text-accent hover:underline">
                  {pointerWhere(p)}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-2xs text-band-spot">This action cites no evidence.</p>
        )}
      </div>

      {a.reversal && (
        <p className="mt-2 text-2xs text-subtle">
          How it is undone: <span className="text-fg">{a.reversal}</span>
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-2">
        {confirm === null && (
          <>
            {a.status === "proposed" && blockers.length > 0 && (
              <div data-testid={`${tid}-approve-blocked`} className="basis-full text-2xs text-band-review">
                <p className="font-medium">Not approvable:</p>
                <ul className="list-disc pl-4">
                  {blockers.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </div>
            )}
            {a.status === "proposed" && blockers.length === 0 && (
              <button
                type="button"
                data-testid={`${tid}-approve`}
                onClick={() => setConfirm({ kind: "approve", key })}
                className="h-7 rounded bg-accent px-3 text-xs font-medium text-accent-fg hover:bg-accent/90"
              >
                Approve…
              </button>
            )}
            <button
              type="button"
              data-testid={`${tid}-reject`}
              onClick={() => setConfirm({ kind: "reject", key })}
              className="h-7 rounded border border-border px-3 text-xs text-fg hover:bg-muted"
            >
              Reject…
            </button>
            {a.status === "approved" && (
              <span className="text-2xs text-subtle">approved, not yet executed</span>
            )}
          </>
        )}
        {confirm?.kind === "approve" && blockers.length === 0 && (
          <>
            <span className="text-xs text-fg">
              Approve <span className="font-mono">{a.name}</span>? It will run with the arguments above.
            </span>
            <button
              type="button"
              data-testid={`${tid}-approve-confirm`}
              disabled={decide.isPending}
              onClick={() => decide.mutate("approve")}
              className="h-7 rounded bg-accent px-3 text-xs font-medium text-accent-fg disabled:opacity-40"
            >
              {decide.isPending ? "Approving…" : "Confirm approve"}
            </button>
            <button type="button" onClick={() => setConfirm(null)} className="h-7 px-2 text-xs text-subtle">
              Cancel
            </button>
          </>
        )}
        {confirm?.kind === "reject" && (
          <>
            <input
              data-testid={`${tid}-reject-reason`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="reason (recorded)"
              className="h-7 w-64 rounded border border-border bg-bg px-2 text-xs"
            />
            <button
              type="button"
              data-testid={`${tid}-reject-confirm`}
              disabled={decide.isPending}
              onClick={() => decide.mutate("reject")}
              className="h-7 rounded border border-band-review/40 bg-band-review/15 px-3 text-xs text-band-review disabled:opacity-40"
            >
              {decide.isPending ? "Rejecting…" : "Confirm reject"}
            </button>
            <button type="button" onClick={() => setConfirm(null)} className="h-7 px-2 text-xs text-subtle">
              Cancel
            </button>
          </>
        )}
      </div>
      <div className="mt-2">
        <ErrorLine error={decide.error} testId={`${tid}-error`} />
      </div>
    </li>
  );
}

export function ApprovalInbox() {
  const qc = useQueryClient();
  const [last, setLast] = React.useState<DecisionResult | null>(null);
  const q = useQuery({
    queryKey: ["approvals"],
    queryFn: ({ signal }) => agentsApi.approvals(signal),
    refetchInterval: 10_000,
  });
  const pending = q.data?.pending ?? [];
  function decided(r: DecisionResult) {
    setLast(r);
    qc.invalidateQueries({ queryKey: ["approvals"] });
    qc.invalidateQueries({ queryKey: ["agent-runs"] });
    qc.invalidateQueries({ queryKey: ["agent-run"] });
  }
  const lastAction = last ? last.record.actions[Number(last.action_id.slice(1)) - 1] : undefined;
  return (
    <Section
      title={
        <>
          Approval inbox{" "}
          <span data-testid="approvals-count" className="ml-1 font-mono text-subtle">
            {q.data ? pending.length : "…"}
          </span>
        </>
      }
      testId="approval-inbox"
    >
      <ErrorLine error={q.error} testId="approvals-error" />
      {last && (
        <p data-testid="approval-result" className="mb-2 text-xs text-fg">
          <span className="font-mono">{last.run_id}/{last.action_id}</span> is now{" "}
          <span className="font-mono font-medium">{lastAction?.status ?? "?"}</span>{" "}
          <span className="text-subtle">(decided by {last.decided_by})</span>
        </p>
      )}
      {q.data && pending.length === 0 && (
        <p data-testid="approvals-empty" className="text-xs text-subtle">
          Nothing is waiting on you. Actions that ask first appear here when an agent proposes them.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {pending.map((a) => (
          <ActionCard key={`${a.run_id}/${a.action_id}`} a={a} onDecided={decided} />
        ))}
      </ul>
    </Section>
  );
}
