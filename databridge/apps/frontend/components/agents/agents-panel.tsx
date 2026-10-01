"use client";
/**
 * The Agents tab: the English box, the approval inbox, every agent, and one agent's runs.
 *
 * Selection lives in the URL (`?agent=`, `?run=`) so a run is linkable — the approval inbox links
 * straight to the run that proposed an action.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { cn } from "@/lib/utils";
import { agentsApi, isTerminalJobStatus, jobGate, jobStatusWords, type AgentRow, type Job } from "@/lib/agents-api";
import { Composer } from "./composer";
import { ApprovalInbox } from "./approval-inbox";
import { RunView } from "./run-view";
import { Collapsible, ErrorLine, Row, Section, StatusPill, TriggerInWords, Yaml, approvalInWords, fmtTime } from "./shared";

/** BUILD_PLAN §6 X — offered as placeholders the user picks; never auto-run. */
export const AGENT_EXAMPLES = [
  // Platform page, every pack: the examples name no domain (SWEEP #23). They were a finance
  // estate's invoices; under a healthcare or government binding that read as this system's data.
  {
    label: "Record briefing (read-only, on demand)",
    text:
      "Brief me on a record before a meeting: its open items, recent activity, key contacts and what its governing documents say about deadlines.",
  },
  {
    label: "Past-due follow-up (automation)",
    text:
      "Every weekday at 9am, find items more than 14 days past their due date, look up the owner and their contact, draft a polite reminder into the Follow-ups folder, and log a follow-up task. Ask me before logging anything; just tell me after drafts are saved.",
  },
];

function LastRun({ agentId }: { agentId: string }) {
  const q = useQuery({
    queryKey: ["agent-runs", agentId],
    queryFn: ({ signal }) => agentsApi.runs(agentId, signal),
  });
  if (q.isLoading) return <span className="text-subtle">…</span>;
  if (q.error) return <span className="text-band-review">unreadable</span>;
  const runs = q.data?.runs ?? [];
  const last = runs[runs.length - 1];
  if (!last) return <span data-testid={`agent-${agentId}-last-run`} className="text-subtle">never run</span>;
  return (
    <span data-testid={`agent-${agentId}-last-run`} className="inline-flex items-center gap-1.5">
      <StatusPill status={last.status} />
      <span className="text-subtle">{fmtTime(last.started)}</span>
    </span>
  );
}

function AgentList({
  rows,
  selected,
  onSelect,
  nextFire,
}: {
  rows: AgentRow[];
  selected: string | null;
  onSelect: (id: string) => void;
  nextFire: Record<string, string | null>;
}) {
  if (!rows.length) {
    return (
      <p data-testid="agents-empty" className="text-xs text-subtle">
        No agents yet. Describe one above, then save it.
      </p>
    );
  }
  return (
    <table data-testid="agents-table" className="w-full text-left text-xs">
      <thead className="text-2xs text-subtle">
        <tr className="border-b border-border">
          <th className="py-1 pr-2 font-medium">Agent</th>
          <th className="py-1 pr-2 font-medium">Status</th>
          <th className="py-1 pr-2 font-medium">Mode</th>
          <th className="py-1 pr-2 font-medium">Trigger</th>
          <th className="py-1 font-medium">Last run</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.agent}
            data-testid={`agent-row-${r.agent}`}
            onClick={() => onSelect(r.agent)}
            className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-muted", selected === r.agent && "bg-muted")}
          >
            <td className="py-1.5 pr-2">
              <div className="font-medium text-fg">{r.title}</div>
              <div className="font-mono text-2xs text-subtle">{r.agent}</div>
            </td>
            <td className="py-1.5 pr-2">
              <StatusPill status={r.status} testId={`agent-${r.agent}-status`} />
            </td>
            <td className="py-1.5 pr-2 text-subtle">{r.mode === "read_only" ? "read-only" : "read-write"}</td>
            <td className="py-1.5 pr-2 text-subtle">
              <span data-testid={`agent-${r.agent}-trigger`}>{r.trigger === "manual" ? "on demand" : r.trigger}</span>
              {nextFire[r.agent] && <div className="text-2xs">next {fmtTime(nextFire[r.agent])}</div>}
            </td>
            <td className="py-1.5 text-2xs">
              <LastRun agentId={r.agent} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RunNow({ agentId, onRun }: { agentId: string; onRun: (runId: string) => void }) {
  const qc = useQueryClient();
  const [question, setQuestion] = React.useState("");
  const [jobId, setJobId] = React.useState<string | null>(null);
  const start = useMutation({
    mutationFn: () => agentsApi.run(agentId, question.trim() || null),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const job = useQuery({
    queryKey: ["agent-job", agentId, jobId],
    queryFn: ({ signal }) => agentsApi.job(agentId, jobId!, signal),
    enabled: !!jobId,
    refetchInterval: (q) => (isTerminalJobStatus((q.state.data as Job | undefined)?.status) ? false : 1000),
  });
  const j = job.data;
  // A started run holds the button until a terminal record is CONFIRMED — through the first fetch
  // and through polling failures — so a second click cannot launch a duplicate run (R-019 round 3, B6).
  const gate = jobGate(jobId, j, !!job.error);
  React.useEffect(() => {
    if (j && isTerminalJobStatus(j.status)) {
      qc.invalidateQueries({ queryKey: ["agent-runs", agentId] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
      const rid = j.run_id ?? (j.result?.run_id as string | undefined);
      if (rid) onRun(rid);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [j?.status]);
  return (
    <div data-testid="run-now" className="flex flex-col gap-2">
      <div className="flex gap-2">
        <input
          data-testid="run-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Your question for this run (e.g. which customer to brief on)"
          className="h-8 flex-1 rounded border border-border bg-bg px-2 text-xs focus:border-accent focus:outline-none"
        />
        <button
          type="button"
          data-testid="run-now-button"
          disabled={start.isPending || !gate.canStart}
          onClick={() => start.mutate()}
          className="inline-flex h-8 items-center gap-1.5 rounded bg-accent px-3 text-xs font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-40"
        >
          <Play className="h-3 w-3" /> Run now
        </button>
      </div>
      <ErrorLine error={start.error} testId="run-now-error" />
      <ErrorLine error={job.error} />
      {gate.outstanding && !j && !job.error && (
        <p data-testid="run-job-pending" className="text-2xs text-subtle">
          Run job <span className="font-mono">{jobId}</span> started; waiting for its first status.
        </p>
      )}
      {j && (
        <p data-testid="run-job-status" className="text-2xs text-subtle">
          job <span className="font-mono">{j.job_id}</span>: <span className="font-mono text-fg">{j.status}</span>
          {jobStatusWords(j.status) && ` — ${jobStatusWords(j.status)}`}
          {j.error && <span className="text-band-review"> — {j.error}</span>}
        </p>
      )}
      {gate.unverified && (
        <p data-testid="run-job-unverified" className="text-2xs text-band-review">
          The status of run job <span className="font-mono">{jobId}</span> could not be read, so it may still be running.
          Run now stays disabled until it is confirmed finished.{" "}
          <button type="button" data-testid="run-job-abandon" onClick={() => setJobId(null)} className="underline hover:text-fg">
            Stop watching it (it may still be running)
          </button>
        </p>
      )}
    </div>
  );
}

function AgentDetail({
  agentId,
  runId,
  onRun,
}: {
  agentId: string;
  runId: string | null;
  onRun: (runId: string | null) => void;
}) {
  const detail = useQuery({
    queryKey: ["agent", agentId],
    queryFn: ({ signal }) => agentsApi.get(agentId, signal),
  });
  const runs = useQuery({
    queryKey: ["agent-runs", agentId],
    queryFn: ({ signal }) => agentsApi.runs(agentId, signal),
  });
  const d = detail.data;
  const spec = d?.spec;
  const list = [...(runs.data?.runs ?? [])].reverse();
  return (
    <div data-testid="agent-detail" data-agent={agentId} className="flex flex-col gap-3">
      {detail.isLoading && <p className="text-xs text-subtle">Loading {agentId}…</p>}
      {detail.error && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-fg">
            The gateway could not load <span className="font-mono">{agentId}</span>, so it cannot be shown or run. Its
            recorded runs are still listed below.
          </p>
          <ErrorLine error={detail.error} testId="agent-detail-error" />
        </div>
      )}
      {d && spec && (
      <Section
        title={
          <>
            {spec.title} <span className="ml-1 font-mono text-2xs text-subtle">{spec.agent}</span>
          </>
        }
        aside={<StatusPill status={spec.status} testId="agent-detail-status" />}
      >
        <dl>
          <Row label="Runs">
            <TriggerInWords trigger={spec.trigger} nextFire={d.next_fire} />
          </Row>
          <Row label="Mode">{spec.mode === "read_only" ? "read-only" : "read-write"}</Row>
          <Row label="Sources">
            <span className="font-mono text-2xs">{spec.sources.join(", ") || "graph and retrieval only"}</span>
          </Row>
          {spec.actions.length > 0 && (
            <Row label="Write actions">
              <ul>
                {spec.actions.map((a) => (
                  <li key={a.name}>
                    <span className="font-mono">{a.name}</span> — {approvalInWords(a.automation_mode, a.reversal)}
                  </li>
                ))}
              </ul>
            </Row>
          )}
        </dl>
        <div className="mt-3">
          {spec.status === "active" ? (
            <RunNow agentId={agentId} onRun={(r) => onRun(r)} />
          ) : (
            <p data-testid="run-now-unavailable" className="text-2xs text-subtle">
              A {spec.status} agent does not run — not from a trigger and not from &ldquo;Run now&rdquo;. Activate it
              from a compiled draft to run it.
            </p>
          )}
        </div>
        <div className="mt-3">
          <Collapsible title="Declaration (YAML)">
            <Yaml text={d.declaration_yaml} />
          </Collapsible>
        </div>
      </Section>
      )}

      <Section title={`Runs (${list.length})`} testId="agent-runs">
        <ErrorLine error={runs.error} />
        {list.length === 0 && !runs.isLoading && <p className="text-xs text-subtle">No runs yet.</p>}
        <ul className="flex flex-col">
          {list.map((r) => (
            <li key={r.run_id}>
              <button
                type="button"
                data-testid={`run-row-${r.run_id}`}
                onClick={() => onRun(runId === r.run_id ? null : r.run_id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-muted",
                  runId === r.run_id && "bg-muted",
                )}
              >
                <StatusPill status={r.status} />
                <span className="font-mono text-2xs text-fg">{r.run_id}</span>
                <span className="text-2xs text-subtle">{fmtTime(r.started)}</span>
                <span className="text-2xs text-subtle">
                  {String(r.trigger?.type ?? "")}
                  {r.actions && r.actions.length > 0 && ` · ${r.actions.length} action(s)`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Section>

      {runId && <RunView agentId={agentId} runId={runId} />}
    </div>
  );
}

export function AgentsPanel({
  agentId,
  runId,
  select,
}: {
  agentId: string | null;
  runId: string | null;
  select: (agent: string | null, run: string | null) => void;
}) {
  const list = useQuery({ queryKey: ["agents"], queryFn: ({ signal }) => agentsApi.list(signal) });
  const status = useQuery({
    queryKey: ["automation-status"],
    queryFn: ({ signal }) => agentsApi.automationStatus(signal),
    retry: false,
  });
  const nextFire = Object.fromEntries((status.data?.specs ?? []).map((s) => [s.id, s.next_fire]));
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="flex flex-col gap-4">
        <Section title="Describe an agent" testId="agent-composer-section">
          <Composer
            testId="agent-composer"
            expectKind="agent"
            examples={AGENT_EXAMPLES}
            placeholder="What should the agent do? Say when it runs, what it reads, and — if it writes — whether it asks you first, tells you after, or just does it."
            onSaved={(r) => r.kind === "agent" && select(r.id, null)}
          />
        </Section>
        <ApprovalInbox />
        <Section
          title="Agents"
          testId="agents-list"
          aside={
            status.data ? (
              <span data-testid="engine-status" className="text-2xs text-subtle">
                trigger engine {status.data.running ? "running" : `not running (enable with ${status.data.enable_with})`}
              </span>
            ) : status.error ? (
              <span data-testid="engine-status" className="text-2xs text-band-spot">
                trigger engine unavailable
              </span>
            ) : null
          }
        >
          <ErrorLine error={list.error} testId="agents-list-error" />
          {list.data && list.data.problems.length > 0 && (
            <ul className="mb-2 list-disc pl-4 text-2xs text-band-review">
              {list.data.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {list.data && (
            <AgentList rows={list.data.agents} selected={agentId} onSelect={(id) => select(id, null)} nextFire={nextFire} />
          )}
        </Section>
      </div>
      <div>
        {agentId ? (
          <AgentDetail agentId={agentId} runId={runId} onRun={(r) => select(agentId, r)} />
        ) : (
          <div className="rounded border border-dashed border-border p-6 text-center text-xs text-subtle">
            Select an agent to run it and see its runs.
          </div>
        )}
      </div>
    </div>
  );
}
