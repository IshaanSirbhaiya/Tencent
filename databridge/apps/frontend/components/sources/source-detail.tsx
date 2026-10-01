"use client";
/**
 * One source, top to bottom in the order an operator works it:
 *   1. the connection (what the gateway will launch or call) and its health
 *   2. tools + the proposed crawl plan → Approve
 *   3. the table mapping (database sources) → Approve
 *   4. Run → the live run view
 *   5. run history, each run's report one click away
 *
 * Every action is a real gateway call; a refusal (401 / 409 / 422 / 502 …) is shown in plain English
 * beside the gateway's own code and message.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  crawlAllowed,
  failureOf,
  humanApproved,
  isAutoApproval,
  mappingGate,
  planApprovalBlockers,
  planToolAudit,
  redactArgs,
  redactCommand,
  redactUrl,
  sourcesApi,
  unwrap,
  type CatalogueConnection,
  type SourceDetail,
  type SourcesFailure,
  type SourcesResult,
} from "@/lib/sources-api";
import { cn } from "@/lib/utils";
import { FailureNotice, Section, StatusBadge, Warning, ago, fmtTime, n } from "./common";
import { PlanView, ToolsView } from "./plan-view";
import { RunView } from "./run-view";
import { routeLabel } from "./sources-list";

function useAction<T>(fn: () => Promise<SourcesResult<T>>, onOk: (data: T) => void) {
  const [failure, setFailure] = React.useState<SourcesFailure | null>(null);
  const m = useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      if (r.ok) {
        setFailure(null);
        onOk(r.data);
      } else setFailure(r);
    },
  });
  return { run: () => m.mutate(), pending: m.isPending, failure, clear: () => setFailure(null) };
}

function ConnectionFacts({ detail }: { detail: SourceDetail }) {
  const c = detail.connection;
  const t = c.transport;
  return (
    <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2" data-testid="source-connection">
      <div className="flex gap-2">
        <dt className="w-28 shrink-0 text-subtle">Route</dt>
        <dd>{routeLabel(c.via, t.type)}</dd>
      </div>
      {c.via === "mcpjungle" && (
        <div className="flex gap-2">
          <dt className="w-28 shrink-0 text-subtle">MCPJungle name</dt>
          <dd className="font-mono text-2xs">{c.jungle_server ?? c.id}</dd>
        </div>
      )}
      {t.type === "stdio" ? (
        <>
          <div className="flex gap-2 sm:col-span-2">
            <dt className="w-28 shrink-0 text-subtle">Command</dt>
            <dd className="break-all font-mono text-2xs" data-testid="source-command">
              {[redactCommand(t.command ?? ""), ...redactArgs(t.args ?? [])].filter(Boolean).join(" ")}
            </dd>
          </div>
          {Object.keys(t.env_from ?? {}).length > 0 && (
            <div className="flex gap-2 sm:col-span-2">
              <dt className="w-28 shrink-0 text-subtle">Env (names)</dt>
              <dd className="font-mono text-2xs">
                {Object.entries(t.env_from).map(([k, v]) => `${k} ← $${v}`).join(", ")}
              </dd>
            </div>
          )}
          {Object.keys(t.env ?? {}).length > 0 && (
            <div className="flex gap-2 sm:col-span-2">
              <dt className="w-28 shrink-0 text-subtle">Env (declared)</dt>
              <dd className="font-mono text-2xs">{Object.keys(t.env).join(", ")}</dd>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="flex gap-2 sm:col-span-2">
            <dt className="w-28 shrink-0 text-subtle">URL</dt>
            <dd className="break-all font-mono text-2xs" data-testid="source-url">{redactUrl(t.url)}</dd>
          </div>
          {Object.keys(t.headers_from ?? {}).length > 0 && (
            <div className="flex gap-2 sm:col-span-2">
              <dt className="w-28 shrink-0 text-subtle">Headers (names)</dt>
              <dd className="font-mono text-2xs">
                {Object.entries(t.headers_from).map(([k, v]) => `${k} ← $${v}`).join(", ")}
              </dd>
            </div>
          )}
        </>
      )}
      <div className="flex gap-2">
        <dt className="w-28 shrink-0 text-subtle">Purpose</dt>
        <dd data-testid="source-purpose" data-purpose={String(c.purpose ?? "")}>
          {c.purpose === "ingest" ? (
            "ingest (read-only crawl)"
          ) : c.purpose === "agent_write" ? (
            "agent writes (never crawled)"
          ) : (
            <span className="text-band-review">
              <code className="font-mono">{String(c.purpose ?? "not stated")}</code> — not a purpose this page knows; it
              is treated as not crawlable
            </span>
          )}
        </dd>
      </div>
      <div className="flex gap-2">
        <dt className="w-28 shrink-0 text-subtle">Trust</dt>
        <dd>{c.trust}</dd>
      </div>
      {c.auto_approve && (
        <div className="sm:col-span-2">
          <Warning testId="source-auto-approve">
            This connection carries the <code className="font-mono">auto_approve</code> flag: the gateway marks a proposed
            plan or mapping approved without a person. This page does not count that as an approval — a plan approved
            that way must still be approved by you before a run is offered here.
          </Warning>
        </div>
      )}
    </dl>
  );
}

export function SourceDetailView({
  sourceId,
  catalogue,
  runId,
  onOpenRun,
}: {
  sourceId: string;
  catalogue: CatalogueConnection | null;
  runId: string | null;
  onOpenRun: (runId: string | null) => void;
}) {
  const qc = useQueryClient();
  const detailQ = useQuery({
    queryKey: ["source", sourceId],
    queryFn: () => unwrap(sourcesApi.get(sourceId)),
    retry: false,
  });
  const healthQ = useQuery({
    queryKey: ["source-health", sourceId],
    queryFn: () => unwrap(sourcesApi.health(sourceId)),
    retry: false,
  });
  const runsQ = useQuery({
    queryKey: ["source-runs", sourceId],
    queryFn: () => unwrap(sourcesApi.runs(sourceId)),
    retry: false,
    refetchInterval: (q) => ((q.state.data?.in_flight.length ?? 0) > 0 ? 3000 : false),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["source", sourceId] });
    qc.invalidateQueries({ queryKey: ["source-health", sourceId] });
    qc.invalidateQueries({ queryKey: ["source-runs", sourceId] });
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: ["platform-catalogue"] });
  };

  const propose = useAction(() => sourcesApi.proposePlan(sourceId), refresh);
  const approve = useAction(() => sourcesApi.approvePlan(sourceId), refresh);
  const approveMapping = useAction(() => sourcesApi.approveMapping(sourceId), refresh);
  const start = useAction(
    () => sourcesApi.startRun(sourceId),
    (d) => {
      refresh();
      onOpenRun(d.run_id);
    },
  );

  if (detailQ.isLoading) return <p className="p-4 text-xs text-subtle">Loading {sourceId}…</p>;
  if (detailQ.isError) {
    const f = failureOf(detailQ.error);
    return f ? <FailureNotice failure={f} testId="source-failure" /> : null;
  }
  const d = detailQ.data!;
  const plan = d.plan;
  const health = healthQ.data;
  const running = d.running ?? health?.running ?? null;
  const mappingTables = d.mapping ? Object.entries(d.mapping.tables) : [];
  const proposedTables = mappingTables.filter(([, t]) => t.status === "proposed").length;
  // Approved BY A PERSON. The connection's auto_approve flag records "auto_approve (connection
  // flag)" as the approver — that is not a human gate, so it never enables Run here.
  const planApproved = humanApproved(plan);
  const autoApproved = plan?.status === "approved" && isAutoApproval(plan.approved_by);
  // Fail closed: only an exact "ingest" purpose may propose / approve / run.
  const isWriteConn = !crawlAllowed(d.connection.purpose);
  const audit = planToolAudit(plan, catalogue);
  // Approval fails closed on anything the page cannot establish: a non-ingest purpose, plan
  // problems, a missing catalogue, a writing tool, or ANY unclassified tool (R-019 round 3, S3).
  const approveBlockers = planApprovalBlockers(plan, catalogue, d.connection.purpose);
  // A run builds from the table mapping: an approved mapping not approved BY A PERSON gates the run
  // until someone re-approves it (R-019 round 3, S2). The gateway must enforce the same (EF3).
  const mapGate = mappingGate(d.mapping);

  const runs = runsQ.data;
  const history = [
    ...(runs?.in_flight ?? []).map((r) => ({ run_id: r.run_id, status: "running", finished_at: null as string | null, message: "", counts: {} as Record<string, unknown> })),
    ...[...(runs?.runs ?? [])].reverse(),
  ];

  return (
    <div className="space-y-4" data-testid="source-detail" data-source-id={sourceId}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold tracking-tight">{d.connection.title || sourceId}</h2>
          <div className="font-mono text-2xs text-subtle">{sourceId}</div>
        </div>
        <div className="flex items-center gap-2">
          {health && <StatusBadge status={health.overall} testId="source-health" />}
          <Button variant="ghost" size="sm" onClick={refresh} aria-label="refresh">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {health && health.issues.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-subtle" data-testid="source-issues">
          {health.issues.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      )}
      {health?.last_success && (
        <p className="text-2xs text-subtle" data-testid="source-freshness">
          Last successful sync {ago(health.last_success.finished_at)} ({fmtTime(health.last_success.finished_at)}).
        </p>
      )}

      <Section title="Connection" description="What the gateway launches or calls for this source.">
        <ConnectionFacts detail={d} />
      </Section>

      {isWriteConn && (
        <Warning testId="source-write-conn">
          {d.connection.purpose === "agent_write"
            ? "This is a write connection for agents. It is never crawled: plans and runs refuse it. Ingest the same source through a separate read-only connection."
            : "This connection's purpose is not \"ingest\", so this page will not propose, approve or run a crawl for it."}
        </Warning>
      )}

      <Section
        testId="source-plan"
        title="Tools and crawl plan"
        description={
          plan
            ? "The tools the server offered and how Walnut proposes to crawl it. Nothing runs until the plan is approved."
            : "Proposing a plan connects to the server and lists its tools."
        }
        actions={
          <>
            {plan && <StatusBadge status={plan.status} testId="plan-status" />}
            <Button
              variant={plan ? "outline" : "default"}
              size="sm"
              data-testid="plan-propose"
              onClick={propose.run}
              disabled={propose.pending || isWriteConn}
            >
              {propose.pending ? "Connecting…" : plan ? "Propose again" : "Propose crawl plan"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {propose.failure && <FailureNotice failure={propose.failure} testId="plan-propose-failure" />}
          {d.connection.via === "direct" && d.connection.transport.type === "stdio" && (
            <Warning testId="source-command-warning">
              Proposing a plan or starting a run launches{" "}
              <code className="font-mono text-2xs">{redactCommand(d.connection.transport.command ?? "")}</code> on the
              gateway&apos;s host.
            </Warning>
          )}
          <div>
            <div className="mb-2 text-xs font-medium">Tools ({d.tools.length})</div>
            <ToolsView tools={d.tools} plan={plan} catalogue={catalogue} note={d.tools_note} />
          </div>
          {plan && (
            <div>
              <div className="mb-2 text-xs font-medium">Proposed plan</div>
              <PlanView plan={plan} connection={d.connection} audit={audit} />
            </div>
          )}
          {plan && (
            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
              {planApproved ? (
                <p className="text-xs text-band-auto" data-testid="plan-approved">
                  Approved by <strong>{plan.approved_by}</strong> {plan.approved_at && <>on {fmtTime(plan.approved_at)}</>}.
                </p>
              ) : (
                <>
                  {autoApproved && (
                    <p className="basis-full text-xs text-band-spot" data-testid="plan-auto-approved">
                      Marked approved by <code className="font-mono">{plan.approved_by}</code>, not by a person. Approve it
                      yourself to enable a run from this page.
                    </p>
                  )}
                  <Button
                    data-testid="plan-approve"
                    onClick={approve.run}
                    disabled={approve.pending || approveBlockers.length > 0}
                  >
                    {approve.pending ? "Approving…" : "Approve plan"}
                  </Button>
                  {approveBlockers.length === 0 ? (
                    <span className="text-2xs text-subtle">Approval is recorded against your signed-in principal.</span>
                  ) : (
                    <ul className="basis-full list-disc pl-5 text-2xs text-band-spot" data-testid="plan-approve-blocked">
                      {approveBlockers.map((b) => (
                        <li key={b}>{b}</li>
                      ))}
                    </ul>
                  )}
                  {audit.unclassified.length > 0 && (
                    <span className="basis-full text-2xs text-band-spot" data-testid="plan-unclassified-tools">
                      Not classified read or write by the tool catalogue: {audit.unclassified.join(", ")}. The plan
                      cannot be approved here until the catalogue classifies them as read-only.
                    </span>
                  )}
                </>
              )}
              {approve.failure && <FailureNotice failure={approve.failure} testId="plan-approve-failure" />}
            </div>
          )}
        </div>
      </Section>

      {d.mapping && mappingTables.length > 0 && (
        <Section
          testId="source-mapping"
          title="Table mapping"
          description="How each database table becomes graph entities. Only approved tables are built."
          actions={
            proposedTables > 0 ? (
              <Button
                size="sm"
                data-testid="mapping-approve"
                onClick={approveMapping.run}
                disabled={approveMapping.pending || isWriteConn}
              >
                {approveMapping.pending ? "Approving…" : `Approve ${proposedTables} proposed table(s)`}
              </Button>
            ) : mapGate.needsHuman ? (
              // Machine-approved (auto_approve flag) or unattributed: a person re-approves it, which
              // the gateway records against the signed-in principal.
              <span className="flex items-center gap-2">
                <span className="text-2xs text-band-spot" data-testid="mapping-auto-approved">
                  {d.mapping.approved_by ? <>marked approved by {d.mapping.approved_by} — not a person</> : "approver not recorded"}
                </span>
                <Button
                  size="sm"
                  data-testid="mapping-reapprove"
                  onClick={approveMapping.run}
                  disabled={approveMapping.pending || isWriteConn}
                >
                  {approveMapping.pending ? "Approving…" : "Approve mapping yourself"}
                </Button>
              </span>
            ) : d.mapping.approved_by ? (
              isAutoApproval(d.mapping.approved_by) ? (
                <span className="text-2xs text-band-spot" data-testid="mapping-auto-approved">
                  marked approved by {d.mapping.approved_by} — not a person
                </span>
              ) : (
                <span className="text-2xs text-subtle">approved by {d.mapping.approved_by}</span>
              )
            ) : null
          }
        >
          {approveMapping.failure && <FailureNotice failure={approveMapping.failure} testId="mapping-approve-failure" />}
          <table className="w-full text-left text-xs" data-testid="mapping-table">
            <thead className="border-b border-border text-2xs uppercase tracking-wider text-subtle">
              <tr>
                <th className="py-1.5 font-medium">Table</th>
                <th className="py-1.5 font-medium">Status</th>
                <th className="py-1.5 font-medium">Becomes</th>
                <th className="py-1.5 font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {mappingTables.map(([table, t]) => (
                <tr key={table} className="border-b border-border align-top last:border-0">
                  <td className="py-1.5 pr-3 font-mono text-2xs">{table}</td>
                  <td className="py-1.5 pr-3">
                    <StatusBadge status={t.status} />
                  </td>
                  <td className="py-1.5 pr-3 text-2xs">
                    {t.node && typeof t.node.kind === "string" ? <span className="font-mono">{t.node.kind}</span> : "—"}
                    {t.edges.length > 0 && <span className="text-subtle"> · {t.edges.length} relation(s)</span>}
                  </td>
                  <td className="py-1.5 text-2xs text-subtle">{t.rationale.join(" ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      <Section
        testId="source-run"
        title="Run"
        description="Collect, extract, screen and build this source into the graph."
        actions={
          <Button
            data-testid="run-start"
            onClick={start.run}
            disabled={start.pending || !!running || !planApproved || isWriteConn || mapGate.needsHuman}
            title={
              !planApproved
                ? "A person must approve the crawl plan first"
                : mapGate.needsHuman
                  ? "A person must approve the table mapping first"
                  : undefined
            }
          >
            <Play className="h-3.5 w-3.5" />
            {running ? "Running…" : start.pending ? "Starting…" : "Start run"}
          </Button>
        }
      >
        <div className="space-y-3">
          {!planApproved && !isWriteConn && (
            <p className="text-xs text-subtle" data-testid="run-needs-approval">
              A source runs only with a crawl plan approved by a person.
            </p>
          )}
          {planApproved && !isWriteConn && mapGate.needsHuman && (
            <p className="text-xs text-band-spot" data-testid="run-needs-mapping-approval">
              The table mapping was not approved by a person, and a run builds from it. Approve the mapping yourself
              above before starting a run.
            </p>
          )}
          {start.failure && <FailureNotice failure={start.failure} testId="run-start-failure" />}
          {running && runId !== running.run_id && (
            <button className="text-xs text-accent underline" onClick={() => onOpenRun(running.run_id)} data-testid="run-open-live">
              A run is in progress ({running.run_id}) — watch it
            </button>
          )}
          {runId ? (
            <RunView key={runId} sourceId={sourceId} runId={runId} />
          ) : (
            <p className="text-xs text-subtle">Start a run, or open one from the history below.</p>
          )}
        </div>
      </Section>

      <Section testId="source-history" title="Run history" description={`${n(d.runs)} run(s) on disk for this source.`}>
        {runsQ.isError && failureOf(runsQ.error) ? (
          <FailureNotice failure={failureOf(runsQ.error)!} />
        ) : history.length === 0 ? (
          <p className="text-xs text-subtle" data-testid="history-empty">
            This source has never run.
          </p>
        ) : (
          <table className="w-full text-left text-xs" data-testid="history-table">
            <thead className="border-b border-border text-2xs uppercase tracking-wider text-subtle">
              <tr>
                <th className="py-1.5 font-medium">Run</th>
                <th className="py-1.5 font-medium">Status</th>
                <th className="py-1.5 text-right font-medium">Seen</th>
                <th className="py-1.5 text-right font-medium">Built</th>
                <th className="py-1.5 font-medium">Finished</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => {
                const c = r.counts as Record<string, unknown>;
                return (
                  <tr
                    key={r.run_id}
                    data-testid={`history-run-${r.run_id}`}
                    onClick={() => onOpenRun(r.run_id)}
                    className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-muted", runId === r.run_id && "bg-muted")}
                  >
                    <td className="py-1.5 pr-3 font-mono text-2xs">{r.run_id}</td>
                    <td className="py-1.5 pr-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="tnum py-1.5 pr-3 text-right">{n(c?.inventory)}</td>
                    <td className="tnum py-1.5 pr-3 text-right">{n(c?.built)}</td>
                    <td className="py-1.5 text-2xs text-subtle">{r.finished_at ? fmtTime(r.finished_at) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}
