"use client";
/**
 * The detail of one Activity row. The row's own fields come from the feed; when the row names a
 * run, the run is read from `GET /activity/runs/{id}` — its steps, its action history, its approvals
 * and (for a delegated question) its delegation tree. A run the viewer may not see answers 404 and
 * is reported as such, never filled in. "Open the per-request trace" hands the recorder's
 * `request_id` to the existing per-request view rather than drawing a second one.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import {
  DECISION_KINDS,
  INTEGRITY_TEXT,
  KIND_LABEL,
  actorText,
  envelopeText,
  isEnvelope,
  valueText,
  decisionRefusalText,
  decisionText,
  fetchActivityRun,
  formatCost,
  formatDuration,
  formatTokens,
  itemTone,
  runIdOf,
  type ActivityItem,
  type HistoryEvent,
} from "@/lib/activity-api";
import { KV, Maybe, Producer, StatusPill, when } from "./activity-bits";
import { DelegationTree } from "./delegation-tree";

export function ActivityInspector({
  item,
  onOpenTrace,
  onSelect,
}: {
  item: ActivityItem | null;
  onOpenTrace: (requestId: string) => void;
  onSelect?: (item: ActivityItem) => void;
}) {
  if (!item) {
    return (
      <p className="px-4 py-6 text-xs text-subtle" data-testid="activity-inspector-empty">
        Click any row to see who did it, which model produced it, and what it led to.
      </p>
    );
  }
  const runId = runIdOf(item);
  return (
    <div className="flex flex-col gap-3 px-4 py-3" data-testid="activity-inspector">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-muted px-1.5 py-px text-[10px] uppercase tracking-wider text-subtle">
          {KIND_LABEL[item.kind]}
        </span>
        <StatusPill status={item.status} tone={itemTone(item)} />
      </div>
      <p className="break-words text-xs font-medium text-fg">{item.title}</p>
      <dl>
        <KV label="when">{when(item.at)}</KV>
        <KV label="actor">{actorText(item.actor)}</KV>
        {/* null = the record names no starter: said as "not recorded", never guessed as automation */}
        {item.started_by !== undefined ? <KV label="started by">{actorText(item.started_by)}</KV> : null}
        <KV label="produced by"><Producer pb={item.produced_by} /></KV>
        <KV label="models"><Maybe value={item.models_used && item.models_used.length ? item.models_used.join(", ") : null} /></KV>
        <KV label="duration">{formatDuration(item.duration_ms)}</KV>
        <KV label="tokens">
          {formatTokens(item.tokens)}
          {item.usage_source ? <span className="text-subtle"> · from the {item.usage_source === "run_record" ? "run record" : "trace"}</span> : null}
        </KV>
        {item.cost_usd !== undefined ? <KV label="cost">{formatCost(item.cost_usd)}</KV> : null}
        {item.kind === "approval" ? (
          <>
            <KV label="action"><Maybe value={item.name ?? item.action_id} /></KV>
            <KV label="tool"><Maybe value={item.tool} /></KV>
            <KV label="mode"><Maybe value={item.automation_mode} /></KV>
            <KV label="decided by"><Maybe value={item.decided_by ? actorText(item.actor) : item.decided_by} /></KV>
            <KV label="outcome"><Maybe value={item.outcome} /></KV>
            {item.refusal ? (
              <KV label="refused">
                <span className="text-band-review" data-testid="activity-refusal">{decisionRefusalText(item.refusal)}</span>
              </KV>
            ) : null}
            {item.refusals && item.refusals.length && !item.refusal ? (
              <KV label="refused attempts">
                <span className="text-subtle">{item.refusals.map(decisionRefusalText).join("; ")}</span>
              </KV>
            ) : null}
          </>
        ) : null}
        {item.kind === "firing" ? (
          <>
            <KV label="automation"><Maybe value={item.spec_id} /></KV>
            <KV label="trigger"><Maybe value={item.trigger_type} /></KV>
            <KV label="idempotency"><Maybe value={item.idempotency_key} /></KV>
            <KV label="scheduled for"><Maybe value={item.scheduled_for} /></KV>
          </>
        ) : null}
        {item.kind === "drift_proposal" || item.kind === "ontology_proposal" ? (
          <>
            <KV label="adapter agent"><Maybe value={item.adapter_agent} /></KV>
            {item.connection !== undefined ? <KV label="connection"><Maybe value={item.connection} /></KV> : null}
            {item.axis !== undefined ? <KV label="axis"><Maybe value={item.axis} /></KV> : null}
            {item.occurrences !== undefined ? <KV label="occurrences"><Maybe value={item.occurrences} /></KV> : null}
            {item.touches_subject ? (
              <KV label="subject"><span className="text-band-spot">touches a person — a human must decide</span></KV>
            ) : null}
          </>
        ) : null}
        {DECISION_KIND_SET.has(item.kind) || item.kind === "one_time_task" || item.kind === "adapter_setup" ? (
          <DecisionFields item={item} />
        ) : null}
        {item.reason ? (
          <KV label="reason">
            {isEnvelope(item.reason) ? (
              <span className="italic text-subtle/80" data-testid="activity-withheld">{envelopeText(item.reason)}</span>
            ) : (
              <Maybe value={item.reason} mono={false} />
            )}
          </KV>
        ) : null}
      </dl>

      {item.drifts && item.drifts.length ? (
        <Section title={`drift (${item.drifts.length})`}>
          <ul className="list-disc pl-4 text-2xs">
            {item.drifts.map((d, i) => (
              <li key={i}>
                <span className="font-mono">{d.kind ?? "?"}</span> on <span className="font-mono">{d.unit ?? "?"}</span>
                {d.subject_touching ? <span className="text-band-spot"> (touches a person)</span> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {item.events && item.events.length ? (
        <Section title="firing history"><History events={item.events} /></Section>
      ) : null}
      {item.history && item.history.length ? (
        <Section title="history"><History events={item.history} /></Section>
      ) : null}

      {item.trace_request_id ? (
        <button
          type="button"
          onClick={() => onOpenTrace(item.trace_request_id as string)}
          data-testid="activity-open-trace"
          className="self-start rounded border border-accent/50 px-2 py-1 text-2xs text-accent hover:bg-accent/10"
        >
          Open the per-request trace
        </button>
      ) : null}

      {runId ? <RunDetail runId={runId} self={item} onOpenTrace={onOpenTrace} onSelect={onSelect} /> : null}
    </div>
  );
}

const DECISION_KIND_SET: ReadonlySet<string> = new Set(DECISION_KINDS);

/** The fields of a decision row (R-028 #8): what was decided, on what, and what was verified about
 *  the record. Every value is the row's own; an unrecorded one says so. */
function DecisionFields({ item }: { item: ActivityItem }) {
  const rows: [string, unknown][] = [
    ["decision", item.decision],
    ["source", item.source_id],
    ["table", item.table],
    ["link", item.column ? `${item.column} → ${item.target ?? "?"}` : undefined],
    ["design", item.design_id],
    ["revision", item.rev],
    ["build", item.build_id],
    ["draft", item.draft_id],
    ["playbook", item.playbook_id],
    ["recipient from", item.recipient_origin],
    ["agent", item.adapter_agent],
    ["step", item.step],
    ["task", item.task_id],
    ["held item", item.item_id],
    ["content digest", item.digest],
    ["pack", item.pack],
    ["node kind", item.node_kind],
    ["identity rule", item.rule],
    ["error ref", item.error_ref],
  ];
  return (
    <>
      {rows
        .filter(([, v]) => v !== undefined && v !== null)
        .map(([k, v]) => (
          <KV key={k} label={k}>
            <Maybe value={valueText(v)} />
          </KV>
        ))}
      {item.actor?.delegation ? (
        <KV label="delegation"><Maybe value={valueText(item.actor.delegation)} /></KV>
      ) : null}
      {item.integrity ? (
        <KV label="integrity">
          <span className={item.integrity === "failed" ? "text-band-review" : "text-fg"} data-testid="activity-integrity">
            {INTEGRITY_TEXT[item.integrity] ?? item.integrity}
          </span>
        </KV>
      ) : null}
      {item.kind === "decision_log" && item.refusal ? (
        <KV label="refused">
          <span className="text-band-review" data-testid="activity-refusal">{decisionRefusalText(item.refusal)}</span>
        </KV>
      ) : null}
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-subtle">{title}</div>
      {children}
    </div>
  );
}

function History({ events }: { events: HistoryEvent[] }) {
  return (
    <ol className="flex flex-col gap-0.5 text-2xs">
      {events.map((e, i) => (
        <li key={i} className="flex flex-wrap gap-x-2">
          <span className="font-mono text-fg">{e.event ?? e.status ?? "?"}</span>
          {e.by ? <span>by {e.by}</span> : e.claimed_by ? <span>claimed by {e.claimed_by} (not verified)</span> : null}
          {e.event === "decision_refused" ? (
            <span className="text-band-review">{e.decision ?? "decision"} refused — {e.reason_code ?? "reason not recorded"}</span>
          ) : null}
          <span className="text-subtle">{when(e.at ?? e.recorded_at)}</span>
          {e.reason || e.error_type || e.note ? (
            <span className="text-subtle">— {valueText(e.reason ?? e.error_type ?? e.note)}</span>
          ) : null}
          {isEnvelope(e.error) ? <span className="italic text-subtle/80">({envelopeText(e.error)})</span> : null}
        </li>
      ))}
    </ol>
  );
}

function RunDetail({
  runId,
  self,
  onOpenTrace,
  onSelect,
}: {
  runId: string;
  self: ActivityItem;
  onOpenTrace: (requestId: string) => void;
  onSelect?: (item: ActivityItem) => void;
}) {
  const q = useQuery({
    queryKey: ["activity-run", self.run_kind ?? null, self.run_owner ?? null, runId],
    queryFn: ({ signal }) =>
      fetchActivityRun(runId, signal, { kind: self.run_kind ?? null, owner: self.run_owner ?? null }),
    retry: false,
    staleTime: 15_000,
  });
  if (q.isLoading) return <p className="text-2xs text-subtle">Reading run {runId}…</p>;
  if (q.isError) {
    return (
      <div role="alert" data-testid="activity-run-error" className="flex items-start gap-2 text-2xs text-band-review">
        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          Run <code>{runId}</code> could not be read: {(q.error as Error).message}
        </span>
      </div>
    );
  }
  const run = q.data!;
  const isSelf = run.item.id === self.id;
  const steps = run.record.steps ?? [];
  return (
    <div className="flex flex-col gap-3 border-t border-border pt-3" data-testid="activity-run">
      {!isSelf ? (
        <button
          type="button"
          onClick={() => onSelect?.(run.item)}
          className="self-start text-left text-2xs text-accent hover:underline"
        >
          the run: {run.item.title} ({run.item.status ?? "status not recorded"})
        </button>
      ) : null}
      {!isSelf && run.item.trace_request_id && !self.trace_request_id ? (
        <button
          type="button"
          onClick={() => onOpenTrace(run.item.trace_request_id as string)}
          className="self-start rounded border border-accent/50 px-2 py-1 text-2xs text-accent hover:bg-accent/10"
        >
          Open the run&apos;s per-request trace
        </button>
      ) : null}

      {run.delegation_tree ? (
        <Section title="delegation tree"><DelegationTree tree={run.delegation_tree} /></Section>
      ) : null}

      {run.approvals.length && self.kind !== "approval" ? (
        <Section title={`approvals (${run.approvals.length})`}>
          <ul className="flex flex-col gap-1 text-2xs">
            {run.approvals.map((a) => (
              <li key={a.id}>
                <button type="button" className="text-left hover:underline" onClick={() => onSelect?.(a)}>
                  <span className="font-mono text-fg">{a.name ?? a.action_id}</span> —{" "}
                  <span className={a.outcome === "execution_failed" || a.outcome === "refused" ? "text-band-review" : ""}>{a.outcome}</span>
                  {a.refusal ? ` — ${decisionRefusalText(a.refusal)}` : a.decided_by ? ` by ${actorText(a.actor)}` : " (no decision recorded)"}
                </button>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {steps.length ? (
        <Section title={`steps (${steps.length})`}>
          <ol className="flex flex-col gap-1 text-2xs" data-testid="activity-run-steps">
            {steps.map((s, i) => (
              <li key={i} className="rounded border border-border px-2 py-1">
                <div className="flex flex-wrap gap-x-2">
                  <span className="font-mono text-fg">{s.tool}</span>
                  <span className="text-subtle">{decisionText(s.policy_decision)}</span>
                  <span className="text-subtle">{formatDuration(s.ms ?? null)}</span>
                </div>
                <div className="text-subtle">by <Producer pb={s.produced_by} /></div>
              </li>
            ))}
          </ol>
        </Section>
      ) : (
        <p className="text-2xs italic text-subtle/80">this run record holds no steps</p>
      )}

      {run.record.masked_fields?.length ? (
        <p className="text-2xs italic text-subtle/80" data-testid="activity-run-masked">
          not shown here (outside this page&apos;s allow-list): {run.record.masked_fields.join(", ")}
        </p>
      ) : null}
    </div>
  );
}
