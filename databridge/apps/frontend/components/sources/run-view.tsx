"use client";
/**
 * One ingestion run, live while it runs and as a report once it has finished.
 *
 * Progress is the run's own: `GET /sources/{id}/runs/{run_id}` is polled while the gateway says the
 * run is `running`, and every figure on screen comes from that response — the runner's progress
 * events (`{stage, done, total}`), `stages.json` (which stages finished, and how long each took), and
 * `report.json` / `verify.json` (the measured counts). There is no timer standing in for progress:
 * a stage the runner has not reported on shows as waiting.
 *
 * Exclusions (excluded / blocked / skipped, each with its rule) come from the run ledgers via
 * `/evidence/sources/{id}`, which summarises every run's ledger rows by reason.
 */
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Circle, Loader2, Network, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  exclusionsVerdict,
  failureOf,
  isTerminalRunStatus,
  pickCounts,
  sourcesApi,
  stageCompleted,
  stageOrder,
  unwrap,
  type ProgressEvent,
  type RunCounts,
  type RunDetail,
} from "@/lib/sources-api";
import { cn } from "@/lib/utils";
import { FailureNotice, StatusBadge, fmtTime, n } from "./common";

/** The runner's stage order (databridge_mcp_ingest.runner.run_ingest). Any stage the backend
 *  reports that is not listed here is shown after these, never dropped. */
const STAGES: { id: string; label: string; what: string }[] = [
  { id: "collect", label: "Inventory & capture", what: "every item in scope is listed and its bytes captured" },
  { id: "extract", label: "Extract", what: "text is extracted from each captured item" },
  { id: "screen", label: "Screen", what: "each item is screened: allow, review or block" },
  { id: "build", label: "Build", what: "allowed items become graph entities and relations" },
  { id: "index", label: "Index", what: "passages are indexed for retrieval" },
  { id: "verify", label: "Verify", what: "counts are reconciled and every pointer re-resolved" },
];

type StageState = "done" | "active" | "waiting" | "failed";

function lastEventFor(events: ProgressEvent[], stage: string): ProgressEvent | null {
  for (let i = events.length - 1; i >= 0; i--) if (events[i].stage === stage) return events[i];
  return null;
}

function StageTimeline({ run }: { run: RunDetail }) {
  const stages = run.stages ?? {};
  const events = run.events ?? [];
  const current = run.progress?.stage ?? null;
  const running = !isTerminalRunStatus(run.status);
  const failed = run.status === "failed";
  // Extra stages come from stage records, events AND the live progress, so a stage the runner
  // reports before writing its record still appears (R-019 round 3, S7).
  const byId = new Map(STAGES.map((s) => [s.id, s]));
  const all = stageOrder(
    STAGES.map((s) => s.id),
    run,
  ).map((id) => byId.get(id) ?? { id, label: id, what: "" });
  // A stage is done only when its record carries `done_at`. A record without one is partial: on a
  // failed run it is where the run stopped; the first stage with no record at all is marked only
  // when no partial record exists.
  const partial = all.find((s) => stages[s.id] && !stageCompleted(stages[s.id]))?.id;
  const firstUndone = partial ?? all.find((s) => !stageCompleted(stages[s.id]))?.id;

  return (
    <ol className="space-y-1.5" data-testid="run-stages">
      {all.map((s) => {
        const rec = stages[s.id];
        const done = stageCompleted(rec);
        let state: StageState = done ? "done" : "waiting";
        if (!done && running && current === s.id) state = "active";
        if (!done && failed && firstUndone === s.id) state = "failed";
        const ev = lastEventFor(events, s.id);
        return (
          <li
            key={s.id}
            data-testid={`run-stage-${s.id}`}
            data-state={state}
            className="flex items-start gap-3 rounded border border-border px-3 py-2"
          >
            <span className="mt-0.5">
              {state === "done" ? (
                <Check className="h-4 w-4 text-band-auto" />
              ) : state === "active" ? (
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
              ) : state === "failed" ? (
                <X className="h-4 w-4 text-band-review" />
              ) : (
                <Circle className="h-4 w-4 text-border" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium">{s.label}</span>
                <span className="tnum text-2xs text-subtle">
                  {typeof rec?.seconds === "number" ? `${rec.seconds.toFixed(1)} s` : state === "active" ? "running" : ""}
                </span>
              </div>
              {s.what && <div className="text-2xs text-subtle">{s.what}</div>}
              {ev && (typeof ev.done === "number" || typeof ev.captured === "number") && (
                <div className="tnum mt-0.5 text-2xs text-fg" data-testid={`run-stage-progress-${s.id}`}>
                  {typeof ev.done === "number" && (
                    <>
                      {ev.done.toLocaleString()}
                      {typeof ev.total === "number" ? ` of ${ev.total.toLocaleString()}` : ""} item(s)
                    </>
                  )}
                  {typeof ev.captured === "number" && <> · {ev.captured.toLocaleString()} captured</>}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const COUNT_ROWS: { key: keyof RunCounts; label: string }[] = [
  { key: "inventory", label: "Items seen" },
  { key: "captured", label: "Captured" },
  { key: "unchanged", label: "Unchanged since last run" },
  { key: "excluded", label: "Excluded (out of scope)" },
  { key: "blocked", label: "Blocked" },
  { key: "failed", label: "Could not be read" },
  { key: "extracted", label: "Text extracted" },
  { key: "extract_skipped", label: "Extraction skipped" },
  { key: "built", label: "Built into the graph" },
  { key: "entities", label: "Entities" },
  { key: "relations", label: "Relations" },
  { key: "refused", label: "Extractions refused" },
  { key: "pointers", label: "Pointers minted" },
  { key: "pointers_resolved", label: "Pointers re-resolved" },
];

function Counts({ counts }: { counts: RunCounts }) {
  const gov = counts.governance ?? {};
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-3" data-testid="run-counts">
        {COUNT_ROWS.filter((r) => typeof counts[r.key] === "number").map((r) => (
          <div key={r.key} className="flex items-baseline justify-between gap-2 border-b border-border py-1">
            <dt className="text-subtle">{r.label}</dt>
            <dd className="tnum font-medium" data-testid={`run-count-${r.key}`}>
              {n(counts[r.key])}
            </dd>
          </div>
        ))}
      </dl>
      {Object.keys(gov).length > 0 && (
        <div data-testid="run-screen">
          <div className="mb-1 text-xs font-medium">Screening decisions</div>
          <div className="flex flex-wrap gap-2 text-xs">
            {Object.entries(gov).map(([decision, count]) => (
              <span key={decision} data-testid={`run-screen-${decision}`} className="flex items-center gap-1.5">
                <StatusBadge status={decision === "allow" ? "ok" : decision === "block" ? "failed" : "proposed"} />
                <span>
                  {decision}: <span className="tnum font-medium">{count}</span>
                </span>
              </span>
            ))}
          </div>
        </div>
      )}
      {counts.extract_skipped_by_reason && Object.keys(counts.extract_skipped_by_reason).length > 0 && (
        <div className="text-2xs text-subtle">
          Extraction skipped because:{" "}
          {Object.entries(counts.extract_skipped_by_reason)
            .map(([r, c]) => `${r} (${c})`)
            .join(", ")}
        </div>
      )}
    </div>
  );
}

function Exclusions({ sourceId, runId }: { sourceId: string; runId: string }) {
  const q = useQuery({
    queryKey: ["source-ledgers", sourceId],
    queryFn: () => unwrap(sourcesApi.ledgerRuns(sourceId)),
    retry: false,
  });
  if (q.isLoading) return <p className="text-xs text-subtle">Reading the run ledgers…</p>;
  if (q.isError) {
    const f = failureOf(q.error);
    return f ? <FailureNotice failure={f} testId="run-exclusions-failure" /> : null;
  }
  const summary = q.data?.runs.find((r) => r.run_id === runId);
  if (!summary) {
    return (
      <p className="text-xs text-subtle" data-testid="run-exclusions-missing">
        The ledgers for this run could not be found.
      </p>
    );
  }
  const verdict = exclusionsVerdict(summary);
  return (
    <div className="space-y-2" data-testid="run-exclusions" data-verdict={verdict}>
      {verdict === "none" ? (
        <p className="text-xs text-subtle" data-testid="run-exclusions-none">
          Nothing was excluded, blocked or skipped in this run.
        </p>
      ) : verdict === "none-in-examined" ? (
        <p className="text-xs text-band-spot" data-testid="run-exclusions-partial">
          No exclusions were found in the ledger rows examined, but the ledger was larger than the reader limit — whether
          the remainder excluded anything is unknown.
        </p>
      ) : verdict === "counted-without-reasons" ? (
        <p className="text-xs text-band-spot" data-testid="run-exclusions-unexplained">
          The ledger counts {summary.excluded.toLocaleString()} excluded item(s) but records no reason for them.
        </p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="border-b border-border text-2xs uppercase tracking-wider text-subtle">
            <tr>
              <th className="py-1.5 font-medium">Outcome</th>
              <th className="py-1.5 font-medium">Rule / reason</th>
              <th className="py-1.5 text-right font-medium">Items</th>
            </tr>
          </thead>
          <tbody>
            {summary.exclusion_reasons.map((r) => {
              const i = r.reason.indexOf(": ");
              const family = i > 0 ? r.reason.slice(0, i) : "";
              const rule = i > 0 ? r.reason.slice(i + 2) : r.reason;
              return (
                <tr key={r.reason} className="border-b border-border last:border-0" data-testid="run-exclusion-row">
                  <td className="py-1.5 pr-3">{family.replace(/_/g, " ")}</td>
                  <td className="py-1.5 pr-3 font-mono text-2xs">{rule}</td>
                  <td className="tnum py-1.5 text-right">{r.n}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {summary.truncated && <p className="text-2xs text-band-spot">The ledger was larger than the reader limit; these figures cover its first rows.</p>}
    </div>
  );
}

export function RunView({ sourceId, runId }: { sourceId: string; runId: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["source-run", sourceId, runId],
    queryFn: () => unwrap(sourcesApi.run(sourceId, runId)),
    // Poll until the runner reports a TERMINAL status. `running`, `null` and any status this page
    // does not know keep polling (slower when it is not plainly `running`).
    refetchInterval: (query) =>
      isTerminalRunStatus(query.state.data?.status) ? false : query.state.data?.status === "running" ? 1500 : 5000,
    retry: false,
  });

  // When the run leaves "running", refresh everything that summarises it.
  const status = q.data?.status;
  const prev = React.useRef(status);
  React.useEffect(() => {
    if (prev.current !== undefined && !isTerminalRunStatus(prev.current) && isTerminalRunStatus(status)) {
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: ["source", sourceId] });
      qc.invalidateQueries({ queryKey: ["source-runs", sourceId] });
      qc.invalidateQueries({ queryKey: ["source-health", sourceId] });
      qc.invalidateQueries({ queryKey: ["source-ledgers", sourceId] });
      qc.invalidateQueries({ queryKey: ["brain-graph"] });
    }
    prev.current = status;
  }, [status, qc, sourceId]);

  const verify = useMutation({
    mutationFn: () => unwrap(sourcesApi.verifyRun(sourceId, runId)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["source-run", sourceId, runId] }),
  });

  if (q.isLoading) return <p className="text-xs text-subtle">Loading run {runId}…</p>;
  if (q.isError) {
    const f = failureOf(q.error);
    return f ? <FailureNotice failure={f} testId="run-failure" /> : null;
  }
  const run = q.data!;
  const report = run.report;
  const finished = isTerminalRunStatus(run.status);
  const picked = pickCounts(run);
  const counts: RunCounts | null = picked.counts;
  const verifyRec = run.verify ?? report?.verify ?? null;

  return (
    <div className="space-y-4" data-testid="run-view" data-run-id={runId} data-status={run.status ?? ""}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs">{runId}</span>
            <StatusBadge status={run.status} testId="run-status" />
          </div>
          <div className="text-2xs text-subtle">
            {report?.started_at && <>started {fmtTime(report.started_at)} · </>}
            {report?.finished_at && <>finished {fmtTime(report.finished_at)} · </>}
            {run.started_by && <>by {run.started_by}</>}
          </div>
        </div>
        {finished && run.status !== "failed" && (
          <Button asChild variant="outline" size="sm" data-testid="run-graph-link">
            <Link href={`/graph?source=${encodeURIComponent(sourceId)}`}>
              <Network className="h-3.5 w-3.5" /> Open the graph for this source
            </Link>
          </Button>
        )}
      </div>

      {report?.message && (
        <p className={cn("text-xs", run.status === "failed" ? "text-band-review" : "text-fg")} data-testid="run-message">
          {report.message}
        </p>
      )}
      {run.error && (
        <p className="break-words font-mono text-2xs text-band-review" data-testid="run-error">
          {run.error}
        </p>
      )}

      <div>
        <div className="mb-2 text-xs font-medium">Stages</div>
        <StageTimeline run={run} />
        {!finished && run.events.length === 0 && (
          <p className="mt-2 text-2xs text-subtle">Waiting for the runner&apos;s first progress event…</p>
        )}
      </div>

      {counts ? (
        <div>
          <div className="mb-2 text-xs font-medium">
            Counts{" "}
            <span className="font-normal text-2xs text-subtle" data-testid="run-counts-from">
              {picked.from === "verify"
                ? `from the latest verification${run.verify?.checked_at ? ` (${fmtTime(run.verify.checked_at)})` : ""}`
                : picked.from === "report-verify"
                  ? `from the verification recorded in the run report${report?.verify?.checked_at ? ` (${fmtTime(report.verify.checked_at)})` : ""}`
                  : "from the run report's snapshot (not verified)"}
            </span>
          </div>
          <Counts counts={counts} />
        </div>
      ) : finished ? (
        <p className="text-xs text-subtle">This run recorded no counts.</p>
      ) : null}
      {!finished && run.status !== "running" && (
        <p className="text-2xs text-band-spot" data-testid="run-status-unknown">
          The gateway reports status <code className="font-mono">{String(run.status ?? "none")}</code>, which is not a
          finished state this page knows; it keeps checking.
        </p>
      )}

      {finished && (
        <div>
          <div className="mb-2 text-xs font-medium">Exclusions</div>
          <Exclusions sourceId={sourceId} runId={runId} />
        </div>
      )}

      {verifyRec && (
        <div data-testid="run-verify" data-ok={verifyRec.ok ? "true" : "false"}>
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium">Reconciliation</span>
            <Button variant="outline" size="sm" onClick={() => verify.mutate()} disabled={verify.isPending}>
              {verify.isPending ? "Re-verifying…" : "Re-verify"}
            </Button>
          </div>
          {verifyRec.ok ? (
            <p className="text-xs text-band-auto">Every item is accounted for and every pointer resolves.</p>
          ) : (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-band-review">
              {(verifyRec.problems ?? []).map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {verify.isError && failureOf(verify.error) && <FailureNotice failure={failureOf(verify.error)!} />}
          {verify.data && (
            <p className="mt-1 text-2xs text-subtle">
              Re-verified {fmtTime(verify.data.checked_at)}: {verify.data.ok ? "clean" : `${verify.data.problems.length} problem(s)`}.
            </p>
          )}
        </div>
      )}

      {report?.notes && report.notes.length > 0 && (
        <details className="rounded border border-border">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium">Run notes ({report.notes.length})</summary>
          <ul className="list-disc space-y-0.5 border-t border-border py-2 pl-8 pr-3 text-2xs text-subtle">
            {report.notes.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </details>
      )}
      {report && (
        <details className="rounded border border-border">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium">Raw report</summary>
          <pre className="max-h-80 overflow-auto border-t border-border p-3 font-mono text-2xs">{JSON.stringify(report, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
