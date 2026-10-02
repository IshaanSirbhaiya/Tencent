"use client";
/**
 * Every source at once (FEAT8): propose all, approve all after ONE review, run all approved.
 *
 * "Approve all pending plans" never approves on a click. It opens one review that lists every
 * pending plan the gateway returned (`GET /source-batch/plans`): per source, each step's tool
 * calls and the class the plan records for each tool; each UNCLASSIFIED tool (e.g. a database's
 * `execute_sql`) with its own checkbox and the gateway's reason, unticked; and every plan the
 * batch cannot approve (a write tool, a problem, a stale plan) under "needs individual review".
 * Confirming sends exactly the reviewed plans, each with only the tools ticked for IT; the
 * gateway approves each through the same per-plan approval as the source's own page, and the
 * dialog then shows one result row per source, approved or not, with the reason.
 *
 * Individual approval and running stay on each source's page, unchanged.
 */
import * as React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCheck, ListChecks, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  batchApproveItems,
  callArgLines,
  sourcesApi,
  unvouched,
  type BatchApproveResult,
  type BatchResultRow,
  type BatchReview,
  type SourcesFailure,
} from "@/lib/sources-api";
import { DevTokenMark, useWhoAmI } from "@/components/auth-chip";
import { FailureNotice, StatusBadge } from "./common";

function Code({ children }: { children: React.ReactNode }) {
  return <code className="rounded bg-muted px-1 py-px font-mono text-2xs text-fg">{children}</code>;
}

/** One row per source, exactly as the gateway reported it. */
export function BatchResults({ rows, testId }: { rows: BatchResultRow[]; testId: string }) {
  if (rows.length === 0) return <p className="text-2xs text-subtle" data-testid={testId}>No sources.</p>;
  return (
    <ul className="divide-y divide-border rounded border border-border text-xs" data-testid={testId}>
      {rows.map((r, i) => (
        <li
          key={`${r.source_id}-${i}`}
          className="flex flex-wrap items-start justify-between gap-2 px-3 py-1.5"
          data-testid={`${testId}-${r.source_id}`}
          data-outcome={r.outcome}
        >
          <span className="font-mono text-2xs">{r.source_id}</span>
          <span className="flex min-w-0 flex-1 flex-col items-end gap-0.5 text-right">
            <StatusBadge status={r.outcome} />
            {r.approval?.dev_only === true && <DevTokenMark testId={`${testId}-${r.source_id}-dev`} />}
            {r.run_id && <span className="font-mono text-2xs text-subtle">run {r.run_id}</span>}
            {r.reason && (
              <span className="break-words text-2xs text-subtle">
                {r.code ? `${r.code}: ` : ""}
                {r.reason}
              </span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ApproveAllDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const me = useWhoAmI();
  const [review, setReview] = React.useState<BatchReview | null>(null);
  const [failure, setFailure] = React.useState<SourcesFailure | null>(null);
  const [ticked, setTicked] = React.useState<Record<string, string[]>>({});
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<BatchApproveResult | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setReview(null);
    setFailure(null);
    setTicked({});
    setResult(null);
    let live = true;
    sourcesApi.batchReview().then((r) => {
      if (!live) return;
      if (r.ok) setReview(r.data);
      else setFailure(r);
    });
    return () => {
      live = false;
    };
  }, [open]);

  const pending = review?.pending ?? [];
  const toggle = (sid: string, tool: string, on: boolean) =>
    setTicked((t) => {
      const mine = new Set(t[sid] ?? []);
      if (on) mine.add(tool);
      else mine.delete(tool);
      return { ...t, [sid]: Array.from(mine) };
    });
  const willApprove = pending.filter((p) => p.batch_approvable && unvouched(p, ticked).length === 0);

  const confirm = async () => {
    setBusy(true);
    const r = await sourcesApi.batchApprove(batchApproveItems(pending, ticked));
    setBusy(false);
    if (r.ok) setResult(r.data);
    else setFailure(r);
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: ["source"] });
    qc.invalidateQueries({ queryKey: ["source-health"] });
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          data-testid="batch-approve-dialog"
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-surface shadow-lg"
        >
          <div className="flex items-start justify-between gap-3 border-b border-border p-4">
            <div>
              <Dialog.Title className="text-sm font-semibold text-fg">Approve all pending crawl plans</Dialog.Title>
              <Dialog.Description className="mt-1 text-2xs leading-relaxed text-subtle">
                Review every plan below. Each is approved on its own, exactly as on its source&apos;s page, and recorded
                against{" "}
                {me.data ? (
                  <>
                    <span className="font-mono text-fg">{me.data.principal_id}</span>
                    {me.data.dev_only && <DevTokenMark testId="batch-approve-dev" />}
                  </>
                ) : (
                  "the signed-in principal"
                )}
                . A plan that calls a writing tool, or an unclassified tool you have not ticked, is not approved.
              </Dialog.Description>
            </div>
            <Dialog.Close className="rounded p-1 text-subtle hover:bg-muted hover:text-fg" aria-label="Close">
              <X className="h-3.5 w-3.5" />
            </Dialog.Close>
          </div>

          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-xs">
            {failure && <FailureNotice failure={failure} testId="batch-approve-failure" />}
            {!review && !failure && <p className="text-subtle">Loading the pending plans…</p>}
            {result ? (
              <div className="space-y-2">
                <p className="font-medium" data-testid="batch-approve-summary">
                  {Object.entries(result.counts)
                    .map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`)
                    .join(" · ")}
                </p>
                <BatchResults rows={result.results} testId="batch-approve-results" />
              </div>
            ) : review && pending.length === 0 ? (
              <p className="text-subtle" data-testid="batch-approve-empty">
                No plan is waiting for approval.
              </p>
            ) : (
              pending.map((p) => (
                <section
                  key={p.source_id}
                  className="rounded border border-border p-3"
                  data-testid={`batch-plan-${p.source_id}`}
                  data-approvable={p.batch_approvable ? "true" : "false"}
                >
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div>
                      <div className="font-medium text-fg">{p.title}</div>
                      <div className="font-mono text-2xs text-subtle">{p.source_id}</div>
                    </div>
                    <StatusBadge status={p.batch_approvable ? p.plan_status : "needs_individual_review"} />
                  </div>
                  <ol className="space-y-1" data-testid={`batch-plan-calls-${p.source_id}`}>
                    {p.steps.map((s) => (
                      <li key={s.step}>
                        <span className="text-2xs uppercase tracking-wider text-subtle">
                          {s.strategy} · <span className="font-mono normal-case">{s.step}</span>
                        </span>
                        <ul className="ml-4 list-disc">
                          {s.calls.map((c, j) => (
                            <li key={j} data-testid={`batch-call-${p.source_id}-${c.tool}`}>
                              {c.role}: <Code>{c.tool}</Code> <span className="font-mono text-2xs text-subtle">{c.class ?? "no class"}</span>
                              {callArgLines(c).length > 0 ? (
                                <ul className="ml-3 font-mono text-2xs text-fg" data-testid={`batch-call-args-${p.source_id}-${c.tool}`}>
                                  {callArgLines(c).map((l) => (
                                    <li key={l} className="break-all">
                                      {l}
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                <span className="ml-1 text-2xs text-subtle">(no fixed arguments)</span>
                              )}
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ol>
                  {p.vouchable.length > 0 && (
                    <div className="mt-2 space-y-1.5 rounded border border-band-spot/40 bg-band-spot/10 p-2">
                      <div className="text-2xs font-medium text-band-spot">
                        Needs your vouch: nothing classifies {p.vouchable.length === 1 ? "this tool" : "these tools"} as a read.
                      </div>
                      {p.vouchable.map((v) => (
                        <label key={v.tool} className="flex items-start gap-2" data-testid={`batch-vouch-${p.source_id}-${v.tool}`}>
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={(ticked[p.source_id] ?? []).includes(v.tool)}
                            onChange={(e) => toggle(p.source_id, v.tool, e.target.checked)}
                            data-testid={`batch-vouch-box-${p.source_id}-${v.tool}`}
                          />
                          <span>
                            I vouch that <Code>{v.tool}</Code> only reads, on <span className="font-mono">{p.source_id}</span>.
                            <span className="block text-2xs text-subtle">{v.reason}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                  {p.blocked.length > 0 && (
                    <div className="mt-2 rounded border border-band-review/40 bg-band-review/10 p-2" data-testid={`batch-blocked-${p.source_id}`}>
                      <div className="text-2xs font-medium text-band-review">
                        Needs individual review: the batch will not approve this plan.
                      </div>
                      <ul className="ml-4 list-disc text-2xs">
                        {p.blocked.map((b) => (
                          <li key={b}>{b}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>
              ))
            )}
            {review && !result && review.not_crawled.length > 0 && (
              <p className="text-2xs text-subtle" data-testid="batch-not-crawled">
                Not crawled (write connections): {review.not_crawled.map((x) => x.source_id).join(", ")}.
              </p>
            )}
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-border p-3">
            {result ? (
              <Button size="sm" variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            ) : (
              <>
                <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  data-testid="batch-approve-confirm"
                  disabled={busy || pending.length === 0}
                  onClick={confirm}
                >
                  <CheckCheck className="h-3.5 w-3.5" />
                  {busy
                    ? "Approving…"
                    : `Confirm: approve ${willApprove.length} of ${pending.length} plan${pending.length === 1 ? "" : "s"}`}
                </Button>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** The three page-level actions, with each one's per-source results shown below them. */
export function BatchActions() {
  const qc = useQueryClient();
  const [approveOpen, setApproveOpen] = React.useState(false);
  const [busy, setBusy] = React.useState<"propose" | "run" | null>(null);
  const [outcome, setOutcome] = React.useState<{ what: "propose" | "run"; rows: BatchResultRow[] } | null>(null);
  const [failure, setFailure] = React.useState<SourcesFailure | null>(null);

  const act = async (what: "propose" | "run") => {
    setBusy(what);
    setFailure(null);
    setOutcome(null);
    const r = what === "propose" ? await sourcesApi.batchPropose() : await sourcesApi.batchRuns();
    setBusy(null);
    if (r.ok) setOutcome({ what, rows: r.data.results });
    else setFailure(r);
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: ["source"] });
    qc.invalidateQueries({ queryKey: ["source-health"] });
    qc.invalidateQueries({ queryKey: ["source-runs"] });
  };

  return (
    <div className="space-y-2" data-testid="batch-actions">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" data-testid="batch-propose" disabled={busy !== null} onClick={() => act("propose")}>
          <ListChecks className="h-3.5 w-3.5" /> {busy === "propose" ? "Proposing…" : "Propose plans for all"}
        </Button>
        <Button size="sm" variant="outline" data-testid="batch-approve" disabled={busy !== null} onClick={() => setApproveOpen(true)}>
          <CheckCheck className="h-3.5 w-3.5" /> Approve all pending plans
        </Button>
        <Button size="sm" variant="outline" data-testid="batch-run" disabled={busy !== null} onClick={() => act("run")}>
          <Play className="h-3.5 w-3.5" /> {busy === "run" ? "Starting…" : "Run all approved sources"}
        </Button>
      </div>
      {failure && <FailureNotice failure={failure} testId="batch-failure" />}
      {outcome && (
        <div className="space-y-1">
          <div className="text-2xs font-medium text-subtle">
            {outcome.what === "propose" ? "Propose plans for all" : "Run all approved sources"}: one row per source
          </div>
          <BatchResults rows={outcome.rows} testId={outcome.what === "propose" ? "batch-propose-results" : "batch-run-results"} />
        </div>
      )}
      <ApproveAllDialog open={approveOpen} onOpenChange={setApproveOpen} />
    </div>
  );
}
