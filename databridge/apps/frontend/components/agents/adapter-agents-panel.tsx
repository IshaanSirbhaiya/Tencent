"use client";
/**
 * The Adapter agents tab: describe a source manager in English, see its health per connection,
 * sync it now with live job status, and handle what it puts to a person — drift adaptations and
 * ontology proposals.
 *
 * Every figure is the gateway's (`AdapterAgentManager.health`). A count the gateway does not know is
 * `null` and is shown as "unknown", never as 0.
 */
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  adaptationDecisionState,
  adapterControls,
  agentsApi,
  freshnessOf,
  isTerminalJobStatus,
  jobGate,
  type AdapterAgentRow,
  type AdapterHealth,
  type ConnectionHealth,
  type HeldAdaptation,
  type Job,
} from "@/lib/agents-api";
import { Composer } from "./composer";
import { SourceSetupPanel, SourcesToSetUp } from "./source-setup";
import { AdaptPolicyInWords } from "./draft-card";
import { Collapsible, ErrorLine, Row, Section, StatusPill, TriggerInWords, fmtTime } from "./shared";

export const ADAPTER_EXAMPLES = [
  {
    label: "Workspace sources (hourly, on change)",
    text:
      "Keep the Contracts folder in Drive and the CRM database in the graph, check hourly, re-ingest anything that changes.",
  },
];

const n = (v: number | null | undefined) => (v === null || v === undefined ? "unknown" : v.toLocaleString());

// ============================================================================================
// health
// ============================================================================================
function ConnectionRow({ c, maxAge }: { c: ConnectionHealth; maxAge: number }) {
  const rules = Object.entries(c.exclusions_by_rule ?? {});
  const fresh = freshnessOf(c);
  return (
    <>
      <tr data-testid={`health-conn-${c.connection}`} className="border-b border-border align-top">
        <td className="py-1.5 pr-2">
          <div className="font-mono text-fg">{c.connection}</div>
          <div className="text-2xs text-subtle">
            plan {c.plan.spec ?? "—"}
            {c.plan.ingest && c.plan.ingest !== c.plan.spec ? ` (ingest: ${c.plan.ingest})` : ""}
            {!c.runnable && <span className="text-band-spot"> · not runnable</span>}
          </div>
        </td>
        <td className="py-1.5 pr-2 text-2xs">
          {c.last_run ? (
            <>
              <StatusPill status={c.last_run.status} />
              <div className="text-subtle">{fmtTime(c.last_run.at)}</div>
            </>
          ) : (
            <span className="text-subtle">never</span>
          )}
        </td>
        <td className="py-1.5 pr-2 text-2xs tabular-nums" data-testid={`health-conn-${c.connection}-counts`}>
          {Object.entries(c.counts).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2">
              <span className="text-subtle">{k}</span>
              <span className="text-fg">{n(v)}</span>
            </div>
          ))}
        </td>
        <td className="py-1.5 pr-2 text-2xs" data-testid={`health-conn-${c.connection}-freshness`}>
          <span
            data-testid={`health-conn-${c.connection}-freshness-label`}
            className={
              fresh.label === "fresh" ? "text-band-auto" : fresh.label === "invalid" ? "text-band-review" : "text-band-spot"
            }
          >
            {fresh.label}
          </span>
          <div className="text-subtle">
            {fresh.ageText} · target {maxAge}h
          </div>
          {fresh.reasons.length > 0 && (
            <ul className="mt-0.5 text-band-spot">
              {fresh.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </td>
        <td className={cn("py-1.5 text-2xs tabular-nums", c.consecutive_failures ? "text-band-review" : "text-subtle")}>
          {c.consecutive_failures} in a row
        </td>
      </tr>
      {(rules.length > 0 || c.issues.length > 0) && (
        <tr className="border-b border-border">
          <td colSpan={5} className="pb-2 pl-2 text-2xs">
            {c.issues.length > 0 && (
              <ul className="list-disc pl-4 text-band-spot">
                {c.issues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            )}
            {rules.length > 0 && (
              <div data-testid={`health-conn-${c.connection}-exclusions`} className="mt-1 text-subtle">
                Excluded by rule:{" "}
                {rules.map(([rule, counts]) => (
                  <span key={rule} className="mr-3 font-mono">
                    {rule}: {Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(", ")}
                  </span>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function HealthTable({ health }: { health: AdapterHealth }) {
  return (
    <div data-testid="adapter-health">
      <p className="mb-2 text-xs">
        overall <StatusPill status={health.overall} testId="adapter-health-overall" />{" "}
        <span className="text-2xs text-subtle">as of {fmtTime(health.generated_at)}</span>
        {health.last_sync && (
          <span className="ml-2 text-2xs text-subtle">
            last sync <span className="font-mono">{health.last_sync.run_id}</span> {health.last_sync.status}
          </span>
        )}
      </p>
      <table className="w-full text-left text-xs">
        <thead className="text-2xs text-subtle">
          <tr className="border-b border-border">
            <th className="py-1 pr-2 font-medium">Connection</th>
            <th className="py-1 pr-2 font-medium">Last run</th>
            <th className="py-1 pr-2 font-medium">Counts (last good run)</th>
            <th className="py-1 pr-2 font-medium">Freshness</th>
            <th className="py-1 font-medium">Failures</th>
          </tr>
        </thead>
        <tbody>
          {health.connections.map((c) => (
            <ConnectionRow key={c.connection} c={c} maxAge={health.freshness.max_age_hours} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================================================
// sync now — a background job, polled
// ============================================================================================
function SyncNow({ id }: { id: string }) {
  const qc = useQueryClient();
  const [jobId, setJobId] = React.useState<string | null>(null);
  const start = useMutation({ mutationFn: () => agentsApi.adapterSync(id), onSuccess: (r) => setJobId(r.job_id) });
  const job = useQuery({
    queryKey: ["adapter-job", id, jobId],
    queryFn: ({ signal }) => agentsApi.adapterJob(id, jobId!, signal),
    enabled: !!jobId,
    refetchInterval: (q) => (isTerminalJobStatus((q.state.data as Job | undefined)?.status) ? false : 1000),
  });
  const j = job.data;
  // A started sync holds the button until a terminal record is CONFIRMED — through the first fetch
  // and through polling failures (R-019 round 3, B6).
  const gate = jobGate(jobId, j, !!job.error);
  const inFlight = gate.outstanding;
  React.useEffect(() => {
    if (j && isTerminalJobStatus(j.status)) {
      qc.invalidateQueries({ queryKey: ["adapter-agent", id] });
      qc.invalidateQueries({ queryKey: ["adapter-runs", id] });
      qc.invalidateQueries({ queryKey: ["adapter-agents"] });
      qc.invalidateQueries({ queryKey: ["adapter-proposals"] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [j?.status]);
  const result = j?.result as Record<string, unknown> | null | undefined;
  return (
    <div data-testid="sync-now" className="flex flex-col gap-1.5">
      <div>
        <button
          type="button"
          data-testid="sync-now-button"
          disabled={start.isPending || !gate.canStart}
          onClick={() => start.mutate()}
          className="inline-flex h-7 items-center gap-1.5 rounded bg-accent px-3 text-xs font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-40"
        >
          <RefreshCw className={cn("h-3 w-3", inFlight && "animate-spin")} /> Sync now
        </button>
      </div>
      {gate.outstanding && !j && !job.error && (
        <p data-testid="sync-job-pending" className="text-2xs text-subtle">
          Sync job <span className="font-mono">{jobId}</span> started; waiting for its first status.
        </p>
      )}
      {gate.unverified && (
        <p data-testid="sync-job-unverified" className="text-2xs text-band-review">
          The status of sync job <span className="font-mono">{jobId}</span> could not be read, so it may still be running.
          Sync now stays disabled until it is confirmed finished.{" "}
          <button
            type="button"
            data-testid="sync-job-abandon"
            onClick={() => setJobId(null)}
            className="underline hover:text-fg"
          >
            Stop watching it (it may still be running)
          </button>
        </p>
      )}
      <ErrorLine error={start.error} testId="sync-error" />
      <ErrorLine error={job.error} />
      {j && (
        <div data-testid="sync-job" className="rounded border border-border p-2 text-2xs">
          <p>
            job <span className="font-mono">{j.job_id}</span>:{" "}
            <span data-testid="sync-job-status" className="font-mono text-fg">{j.status}</span>{" "}
            <span className="text-subtle">started {fmtTime(j.started)}{j.ended && ` · ended ${fmtTime(j.ended)}`}</span>
          </p>
          {j.progress != null && <pre className="mt-1 font-mono text-subtle">{JSON.stringify(j.progress)}</pre>}
          {j.events.length > 0 && (
            <ul className="mt-1 max-h-32 overflow-auto font-mono text-subtle">
              {j.events.map((e, i) => (
                <li key={i}>{typeof e === "string" ? e : JSON.stringify(e)}</li>
              ))}
            </ul>
          )}
          {j.error && <p className="mt-1 text-band-review">{j.error}</p>}
          {result && (
            <p className="mt-1 text-fg">
              sync <span className="font-mono">{String(result.status)}</span>
              {result.reason ? <> — {String(result.reason)}</> : null}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================================
// held changes — drift adaptations the agent held for a person (INT4)
// ============================================================================================
function HeldChangeItem({
  agentId,
  a,
  mayDecide,
  owner,
}: {
  agentId: string;
  a: HeldAdaptation;
  mayDecide: boolean;
  owner: string | null;
}) {
  const qc = useQueryClient();
  const [reason, setReason] = React.useState("");
  const decide = useMutation({
    // never a `by`: the gateway records the signed-in principal as the decider
    mutationFn: (what: "approve" | "reject") =>
      what === "approve"
        ? agentsApi.approveAdaptation(agentId, a.adaptation_id, reason)
        : agentsApi.rejectAdaptation(agentId, a.adaptation_id, reason),
    onSettled: () => {
      // a refusal (409: decided elsewhere) also means the list we show may be stale
      qc.invalidateQueries({ queryKey: ["adapter-held", agentId] });
      qc.invalidateQueries({ queryKey: ["adapter-agent", agentId] });
      qc.invalidateQueries({ queryKey: ["adapter-agents"] });
    },
  });
  const state = adaptationDecisionState({ status: a.status, mayDecide, owner, reason, busy: decide.isPending });
  const tid = `held-${a.adaptation_id}`;
  return (
    <li data-testid={tid} className="rounded border border-border p-2.5 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {a.kinds.map((k) => (
          <span
            key={k}
            className={cn(
              "rounded border px-1 font-mono text-2xs",
              k === "subject_touching" ? "border-band-review/40 text-band-review" : "border-border text-fg",
            )}
          >
            {k}
          </span>
        ))}
        <span className="font-mono text-2xs text-subtle">{a.connection ?? "?"}</span>
        <span className="flex-1" />
        <span className="text-2xs text-subtle">held {a.created_at ? fmtTime(a.created_at) : "(time not recorded)"}</span>
      </div>
      {a.changed.length > 0 && (
        <div className="mt-1.5">
          <div className="text-2xs text-subtle">What changed</div>
          <ul data-testid={`${tid}-changed`} className="list-disc pl-4">
            {a.changed.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-1.5">
        <div className="text-2xs text-subtle">Why it was held</div>
        {a.held_because.length > 0 || a.problems.length > 0 ? (
          <ul data-testid={`${tid}-why`} className="list-disc pl-4 text-band-spot">
            {a.held_because.map((w) => (
              <li key={w}>{w}</li>
            ))}
            {a.problems.map((w) => (
              <li key={`p-${w}`}>{w}</li>
            ))}
          </ul>
        ) : (
          <p className="text-2xs text-subtle">The record states no reason.</p>
        )}
      </div>
      {a.action && <p className="mt-1.5 text-2xs text-subtle">Proposed: {a.action}</p>}
      <div className="mt-2 flex flex-col gap-1.5">
        <input
          data-testid={`${tid}-reason`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={!mayDecide || a.status !== "pending"}
          placeholder="Reason (required to reject, optional to approve)"
          className="h-7 rounded border border-border bg-bg px-2 text-xs disabled:opacity-40"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid={`${tid}-approve`}
            disabled={!state.canApprove}
            onClick={() => decide.mutate("approve")}
            className="h-7 rounded bg-accent px-3 text-xs font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-40"
          >
            Approve
          </button>
          <button
            type="button"
            data-testid={`${tid}-reject`}
            disabled={!state.canReject}
            onClick={() => decide.mutate("reject")}
            className="h-7 rounded border border-border px-3 text-xs hover:bg-muted disabled:opacity-40"
          >
            Reject
          </button>
          {decide.data && (
            <span data-testid={`${tid}-decided`} className="text-2xs text-band-auto">
              {decide.data.adaptation.status} by {decide.data.adaptation.decided_by?.principal_id ?? decide.data.by}
              {decide.data.adaptation.status === "rejected" && " — the affected part stays held"}
            </span>
          )}
        </div>
        {state.blockers.length > 0 && (
          <ul data-testid={`${tid}-blockers`} className="list-disc pl-4 text-2xs text-subtle">
            {state.blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        )}
        <ErrorLine error={decide.error} testId={`${tid}-error`} />
      </div>
    </li>
  );
}

function HeldChanges({ agentId }: { agentId: string }) {
  const q = useQuery({
    queryKey: ["adapter-held", agentId],
    queryFn: ({ signal }) => agentsApi.heldAdaptations(agentId, "pending", signal),
    refetchInterval: 30_000,
  });
  const d = q.data;
  return (
    <Section
      title={
        <>
          Held changes <span className="ml-1 font-mono text-subtle">{d ? d.adaptations.length : "…"}</span>
        </>
      }
      testId="held-changes"
    >
      <p className="mb-2 text-2xs text-subtle">
        Source-format changes this agent would not adapt to on its own. The affected part stays out of the graph until
        its owner approves (the proposed change is applied and the hold released) or rejects (it stays held).
        {d && (
          <span data-testid="held-changes-owner">
            {" "}
            Owner: <span className="font-mono">{d.owner ?? "not recorded"}</span>
            {d.may_decide ? " (you)" : ""}.
          </span>
        )}
      </p>
      <ErrorLine error={q.error} testId="held-changes-error" />
      {d && d.adaptations.length === 0 && (
        <p data-testid="held-changes-empty" className="text-xs text-subtle">
          No source-format change is waiting on a person.
        </p>
      )}
      {d && d.adaptations.length > 0 && (
        <ul className="flex flex-col gap-2">
          {d.adaptations.map((a) => (
            <HeldChangeItem key={a.adaptation_id} agentId={agentId} a={a} mayDecide={d.may_decide} owner={d.owner} />
          ))}
        </ul>
      )}
    </Section>
  );
}

// ============================================================================================
// ontology proposals
// ============================================================================================
function Proposals({ agentId }: { agentId: string | null }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = React.useState<string | null>(null);
  const q = useQuery({ queryKey: ["adapter-proposals"], queryFn: ({ signal }) => agentsApi.proposals(signal) });
  const forward = useMutation({
    mutationFn: (pid: string) => agentsApi.forwardProposal(pid),
    onSuccess: () => {
      setConfirm(null);
      qc.invalidateQueries({ queryKey: ["adapter-proposals"] });
    },
  });
  const all = q.data?.proposals ?? [];
  const rows = agentId ? all.filter((p) => p.adapter_agent === agentId) : all;
  return (
    <Section
      title={
        <>
          Ontology proposals <span className="ml-1 font-mono text-subtle">{q.data ? rows.length : "…"}</span>
          {agentId && <span className="ml-1 text-2xs font-normal text-subtle">from {agentId}</span>}
        </>
      }
      testId="adapter-proposals"
    >
      <ErrorLine error={q.error} />
      <p className="mb-2 text-2xs text-subtle">
        Kinds the agent met but could not map. Forwarding files them on the ontology&rsquo;s review queue — they never
        apply on their own.
      </p>
      {q.data && rows.length === 0 && <p data-testid="adapter-proposals-empty" className="text-xs text-subtle">No proposals.</p>}
      <ul className="flex flex-col gap-2">
        {rows.map((p) => (
          <li key={p.proposal_id} data-testid={`proposal-${p.proposal_id}`} className="rounded border border-border p-2.5 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-medium">{p.name}</span>
              <span className="text-2xs text-subtle">
                {p.axis} kind · binds to {p.binds_to} · pack {p.pack}
              </span>
              {p.touches_subject && (
                <span className="rounded border border-band-review/40 px-1 text-2xs text-band-review">people-related</span>
              )}
              <span className="flex-1" />
              <span className="text-2xs text-subtle">
                {p.occurrences ?? "?"} occurrence(s) in {p.document_count ?? "?"} document(s) · {p.proposed_by}
              </span>
            </div>
            {p.subject_reason && <p className="mt-1 text-2xs text-band-review">{p.subject_reason}</p>}
            {p.raw_names && p.raw_names.length > 0 && (
              <p className="mt-1 text-2xs text-subtle">
                seen as <span className="font-mono">{p.raw_names.join(", ")}</span>
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {p.forwarded ? (
                <span data-testid={`proposal-${p.proposal_id}-forwarded`} className="text-2xs text-band-auto">
                  forwarded by {p.forwarded.by} {fmtTime(p.forwarded.at)} as{" "}
                  <span className="font-mono">{p.forwarded.ontology_proposal_id ?? "?"}</span> ({p.forwarded.ontology_status ?? "?"})
                </span>
              ) : confirm === p.proposal_id ? (
                <>
                  <span className="text-2xs">Forward to the ontology review queue?</span>
                  <button
                    type="button"
                    data-testid={`proposal-${p.proposal_id}-forward-confirm`}
                    disabled={forward.isPending}
                    onClick={() => forward.mutate(p.proposal_id)}
                    className="h-6 rounded bg-accent px-2 text-2xs font-medium text-accent-fg disabled:opacity-40"
                  >
                    Confirm forward
                  </button>
                  <button type="button" onClick={() => setConfirm(null)} className="h-6 px-1 text-2xs text-subtle">
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  data-testid={`proposal-${p.proposal_id}-forward`}
                  onClick={() => setConfirm(p.proposal_id)}
                  className="h-6 rounded border border-border px-2 text-2xs text-fg hover:bg-muted"
                >
                  Forward to ontology…
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-2">
        <ErrorLine error={forward.error} testId="forward-error" />
      </div>
    </Section>
  );
}

// ============================================================================================
// one adapter agent
// ============================================================================================
function AdapterDetail({ id }: { id: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["adapter-agent", id],
    queryFn: ({ signal }) => agentsApi.adapterAgent(id, signal),
    refetchInterval: 30_000,
  });
  const runs = useQuery({ queryKey: ["adapter-runs", id], queryFn: ({ signal }) => agentsApi.adapterRuns(id, signal) });
  const act = useMutation({
    mutationFn: (what: "pause" | "resume" | "refresh") =>
      what === "pause" ? agentsApi.adapterPause(id) : what === "resume" ? agentsApi.adapterResume(id) : agentsApi.adapterRefreshPlans(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["adapter-agent", id] });
      qc.invalidateQueries({ queryKey: ["adapter-agents"] });
    },
  });
  if (q.isLoading) return <p className="text-xs text-subtle">Loading {id}…</p>;
  if (q.error) return <ErrorLine error={q.error} testId="adapter-detail-error" />;
  const d = q.data!;
  const spec = d.spec;
  const health = "connections" in d.health ? (d.health as AdapterHealth) : null;
  const runList = [...(runs.data?.runs ?? [])].reverse();
  const controls = adapterControls(spec, d.deleted);
  return (
    <div data-testid="adapter-detail" data-adapter={id} className="flex flex-col gap-3">
      <Section
        title={
          <>
            {spec.title} <span className="ml-1 font-mono text-2xs text-subtle">{spec.id}</span>
          </>
        }
        aside={<StatusPill status={d.deleted ? "deleted" : spec.status} testId="adapter-detail-status" />}
      >
        <dl>
          <Row label="Syncs">
            <TriggerInWords trigger={spec.sync} nextFire={d.next_fire} />
          </Row>
          <Row label="Freshness">stale after {spec.freshness.max_age_hours}h</Row>
          <Row label="Format changes">
            <AdaptPolicyInWords spec={spec} />
          </Row>
        </dl>
        <div className="mt-3 flex flex-wrap items-start gap-2">
          {controls.canSync && <SyncNow id={id} />}
          {controls.canPause && (
            <button type="button" data-testid="adapter-pause" onClick={() => act.mutate("pause")} className="h-7 rounded border border-border px-3 text-xs hover:bg-muted">
              Pause
            </button>
          )}
          {controls.canResume && (
            <button type="button" data-testid="adapter-resume" onClick={() => act.mutate("resume")} className="h-7 rounded border border-border px-3 text-xs hover:bg-muted">
              Resume
            </button>
          )}
          {!d.deleted && spec.status !== "draft" && controls.blockers.length > 0 && (
            <div data-testid="adapter-gate-blocked" className="basis-full text-2xs text-band-spot">
              <p>
                {spec.status === "active" ? "Sync now is" : "Resume is"} not offered: the same gate activation needs no longer
                holds.
              </p>
              <ul className="list-disc pl-4">
                {controls.blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </div>
          )}
          {!d.deleted && spec.status === "draft" && (
            <p data-testid="adapter-draft-not-runnable" className="basis-full text-2xs text-subtle">
              A draft does not sync and cannot be resumed from here. Describe it again above and activate the compiled
              draft once every crawl plan it names is approved.
            </p>
          )}
          <button
            type="button"
            data-testid="adapter-refresh-plans"
            onClick={() => act.mutate("refresh")}
            title="Re-read each connection's crawl-plan status from disk"
            className="h-7 rounded border border-border px-3 text-xs hover:bg-muted"
          >
            Refresh plan status
          </button>
        </div>
        <div className="mt-2">
          <ErrorLine error={act.error} />
        </div>
      </Section>

      <Section title="Health" testId="adapter-health-section">
        {health ? (
          <HealthTable health={health} />
        ) : (
          <div className="text-xs">
            <StatusPill status={d.health.overall} />
            <ul className="mt-1 list-disc pl-4 text-band-review">
              {d.health.issues.map((i) => (
                <li key={i}>{i}</li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <HeldChanges agentId={id} />
      <Proposals agentId={id} />

      <Section title={`Sync runs (${runList.length})`} testId="adapter-runs">
        <ErrorLine error={runs.error} />
        {runList.length === 0 && !runs.isLoading && <p className="text-xs text-subtle">No syncs yet.</p>}
        <ul className="flex flex-col gap-1">
          {runList.slice(0, 20).map((r) => (
            <li key={r.run_id} className="flex flex-wrap items-center gap-2 text-2xs">
              <StatusPill status={r.status} />
              <span className="font-mono">{r.run_id}</span>
              <span className="text-subtle">{fmtTime(r.started)}</span>
              {r.reason && <span className="text-subtle">— {r.reason}</span>}
              {r.totals && (
                <span className="tabular-nums text-subtle">
                  {Object.entries(r.totals)
                    .map(([k, v]) => `${k} ${n(v)}`)
                    .join(" · ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      </Section>

      {d.history.length > 0 && (
        <Collapsible title={`History (${d.history.length})`}>
          <ul className="p-2.5 font-mono text-2xs text-subtle">
            {d.history.map((h, i) => (
              <li key={i}>{JSON.stringify(h)}</li>
            ))}
          </ul>
        </Collapsible>
      )}
    </div>
  );
}

// ============================================================================================
// the tab
// ============================================================================================
function AdapterList({
  rows,
  selected,
  onSelect,
}: {
  rows: AdapterAgentRow[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  if (!rows.length) {
    return (
      <p data-testid="adapter-agents-empty" className="text-xs text-subtle">
        No adapter agents yet. Describe one above, then save it.
      </p>
    );
  }
  return (
    <table data-testid="adapter-agents-table" className="w-full text-left text-xs">
      <thead className="text-2xs text-subtle">
        <tr className="border-b border-border">
          <th className="py-1 pr-2 font-medium">Adapter agent</th>
          <th className="py-1 pr-2 font-medium">Status</th>
          <th className="py-1 pr-2 font-medium">Health</th>
          <th className="py-1 font-medium">Last sync</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.spec.id}
            data-testid={`adapter-row-${r.spec.id}`}
            onClick={() => onSelect(r.spec.id)}
            className={cn("cursor-pointer border-b border-border last:border-0 hover:bg-muted", selected === r.spec.id && "bg-muted")}
          >
            <td className="py-1.5 pr-2">
              <div className="font-medium">{r.spec.title}</div>
              <div className="font-mono text-2xs text-subtle">
                {r.spec.id} · {r.spec.connections.join(", ")}
              </div>
            </td>
            <td className="py-1.5 pr-2">
              <StatusPill status={r.deleted ? "deleted" : r.spec.status} />
            </td>
            <td className="py-1.5 pr-2">
              <StatusPill status={r.overall} testId={`adapter-${r.spec.id}-overall`} />
              {r.issues.length > 0 && <div className="text-2xs text-subtle">{r.issues.length} issue(s)</div>}
            </td>
            <td className="py-1.5 text-2xs text-subtle">
              {r.last_sync ? `${r.last_sync.status} · ${fmtTime(r.last_sync.ended ?? r.last_sync.started)}` : "never"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AdapterAgentsPanel({
  adapterId,
  select,
  setupId = null,
  selectSetup,
}: {
  adapterId: string | null;
  select: (id: string | null) => void;
  /** FEAT8: the source being set up through its adapter agent (`?setup=`). */
  setupId?: string | null;
  selectSetup?: (sourceId: string | null) => void;
}) {
  const [showDeleted, setShowDeleted] = React.useState(false);
  const list = useQuery({ queryKey: ["adapter-agents"], queryFn: ({ signal }) => agentsApi.adapterAgents(signal) });
  const rows = (list.data?.adapter_agents ?? []).filter((r) => showDeleted || !r.deleted);
  const problems = Object.entries(list.data?.problems ?? {});
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="flex flex-col gap-4">
        {selectSetup && <SourcesToSetUp selected={setupId} onSelect={(sid) => selectSetup(sid)} />}
        <Section
          title="Describe an adapter agent"
          testId="adapter-composer-section"
          aside={
            <Link data-testid="adapter-add-source" href="/connectors" className="text-2xs text-accent hover:underline">
              Add a source on Sources →
            </Link>
          }
        >
          <p className="mb-2 text-2xs text-subtle">
            An adapter agent manages sources that are already connected. To connect a new one (register an MCP server,
            approve its crawl plan), use Sources; then name it here.
          </p>
          <Composer
            testId="adapter-composer"
            expectKind="adapter_agent"
            examples={ADAPTER_EXAMPLES}
            placeholder="Which sources should be kept in the graph, how often, and how fresh? (e.g. keep the Contracts folder and the CRM database in the graph, check hourly)"
            onSaved={(r) => r.kind === "adapter_agent" && select(r.id)}
          />
        </Section>
        <Section
          title="Adapter agents"
          testId="adapter-agents-list"
          aside={
            <label className="flex items-center gap-1 text-2xs text-subtle">
              <input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} /> show deleted
            </label>
          }
        >
          <ErrorLine error={list.error} testId="adapter-agents-error" />
          {problems.length > 0 && (
            <ul className="mb-2 list-disc pl-4 text-2xs text-band-review">
              {problems.map(([k, v]) => (
                <li key={k}>
                  <span className="font-mono">{k}</span>: {v}
                </li>
              ))}
            </ul>
          )}
          {list.data && <AdapterList rows={rows} selected={adapterId} onSelect={select} />}
        </Section>
        {!adapterId && <Proposals agentId={null} />}
      </div>
      <div>
        {setupId && selectSetup ? (
          <SourceSetupPanel id={setupId} openAgent={(aid) => select(aid)} />
        ) : adapterId ? (
          <AdapterDetail id={adapterId} />
        ) : (
          <div className="rounded border border-dashed border-border p-6 text-center text-xs text-subtle">
            Select an adapter agent to see its health, sync it, and handle what it asks.
          </div>
        )}
      </div>
    </div>
  );
}
