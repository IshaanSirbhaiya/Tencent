"use client";
/**
 * Automations: each trigger (schedule / event / manual), its firings (idempotency key, time), and
 * the run and approvals each firing produced — from `GET /activity/automations`, which folds the
 * trigger engine's `firings.jsonl` with the run records. When the engine is not attached to this
 * gateway, triggers are shown from their recorded firings only, and the page says so.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Inbox } from "lucide-react";
import {
  decisionRefusalText,
  fetchAutomations,
  formatDuration,
  itemTone,
  type ActivityItem,
  type AutomationFiring,
  type AutomationSpec,
} from "@/lib/activity-api";
import { Maybe, StatusPill, TONE_BORDER, when } from "./activity-bits";

export function AutomationsView({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (item: ActivityItem) => void;
}) {
  const q = useQuery({
    queryKey: ["activity-automations"],
    queryFn: ({ signal }) => fetchAutomations(signal),
    retry: false,
    staleTime: 15_000,
  });
  if (q.isLoading) {
    return <p className="text-xs text-subtle" data-testid="automations-loading">Reading the firing log…</p>;
  }
  if (q.isError) {
    return (
      <div
        role="alert"
        data-testid="automations-error"
        className="flex items-start gap-2 rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-xs text-band-review"
      >
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          Could not load <code>GET /activity/automations</code>: {(q.error as Error).message}.
        </span>
      </div>
    );
  }
  const view = q.data!;
  return (
    <div className="flex flex-col gap-3" data-testid="automations-view">
      {view.engine.note ? (
        <p className="text-2xs text-band-spot" data-testid="automations-engine-note">{view.engine.note}</p>
      ) : null}
      {view.problems.length ? (
        <p className="text-2xs text-band-spot">
          {view.problems.length} record(s) could not be read and are not shown.
        </p>
      ) : null}
      {view.automations.length === 0 ? (
        <div
          data-testid="automations-empty"
          className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-surface px-6 py-12 text-center"
        >
          <Inbox className="h-6 w-6 text-subtle" />
          <div className="text-sm font-semibold text-fg">No automations you can see have fired</div>
          <p className="max-w-md text-xs text-subtle">
            A trigger appears here once it is registered with the engine or has fired at least once.
          </p>
        </div>
      ) : (
        view.automations.map((s) => (
          <SpecCard key={s.spec_id} spec={s} selected={selected} onSelect={onSelect} />
        ))
      )}
    </div>
  );
}

function SpecCard({
  spec,
  selected,
  onSelect,
}: {
  spec: AutomationSpec;
  selected: string | null;
  onSelect: (item: ActivityItem) => void;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface" data-testid="automation-spec">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-3 py-2">
        <span className="font-mono text-xs font-semibold text-fg">{spec.spec_id}</span>
        <span className="text-2xs text-subtle">{spec.spec_kind ?? "kind not recorded"}</span>
        <span className="text-2xs text-subtle">
          trigger:{" "}
          <Maybe value={spec.trigger?.trigger ?? (spec.trigger_types_seen.join(", ") || null)} />
        </span>
        {spec.trigger?.status ? <span className="text-2xs text-subtle">engine: {spec.trigger.status}</span> : null}
        {spec.trigger?.next_fire ? (
          <span className="text-2xs text-subtle">next: {when(spec.trigger.next_fire)}</span>
        ) : null}
        <span className="ml-auto text-2xs text-subtle">
          {spec.firings.length} of{" "}
          {spec.firing_count ?? "an unknown number of (the firing log was not read whole)"} firing(s) shown
        </span>
      </header>
      {spec.firings.length ? (
        <ol className="flex flex-col divide-y divide-border">
          {spec.firings.map((f) => (
            <FiringRow key={f.id} f={f} selected={selected} onSelect={onSelect} />
          ))}
        </ol>
      ) : (
        <p className="px-3 py-2 text-2xs italic text-subtle/80">no recorded firing</p>
      )}
    </section>
  );
}

function FiringRow({
  f,
  selected,
  onSelect,
}: {
  f: AutomationFiring;
  selected: string | null;
  onSelect: (item: ActivityItem) => void;
}) {
  const tone = itemTone(f);
  return (
    <li className={`px-3 py-2 ${f.loud ? `border-l-4 ${TONE_BORDER[tone]}` : ""}`} data-testid="automation-firing">
      <button
        type="button"
        onClick={() => onSelect(f)}
        className={`flex w-full flex-wrap items-center gap-2 text-left ${selected === f.id ? "text-accent" : ""}`}
      >
        <StatusPill status={f.status} tone={tone} />
        <span className="text-2xs text-subtle">{when(f.at)}</span>
        <span className="font-mono text-2xs text-fg">{f.idempotency_key}</span>
        {f.started_by?.type === "person" ? (
          <span className="text-2xs text-subtle">fired by {f.started_by.id}</span>
        ) : null}
      </button>
      <div className="mt-1 flex flex-col gap-1 pl-4 text-2xs">
        {f.run ? (
          <button type="button" onClick={() => onSelect(f.run!)} className="text-left hover:underline">
            run <span className="font-mono text-fg">{f.run.run_id}</span> —{" "}
            <span className={itemTone(f.run) === "bad" ? "text-band-review" : ""}>
              {f.run.status ?? "status not recorded"}
            </span>{" "}
            · {formatDuration(f.run.duration_ms)}
          </button>
        ) : f.run_note ? (
          <span className="italic text-subtle/80">{f.run_note}</span>
        ) : (
          <span className="italic text-subtle/80">this firing produced no run</span>
        )}
        {f.approvals.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => onSelect(a)}
            className="text-left hover:underline"
            data-testid="automation-approval"
          >
            approval <span className="font-mono text-fg">{a.name ?? a.action_id}</span> —{" "}
            <span className={a.outcome === "execution_failed" || a.outcome === "refused" ? "text-band-review" : "text-fg"}>{a.outcome}</span>
            {a.refusal ? ` — ${decisionRefusalText(a.refusal)}` : a.decided_by ? ` by ${a.decided_by}` : " · no decision recorded"}
          </button>
        ))}
      </div>
    </li>
  );
}
