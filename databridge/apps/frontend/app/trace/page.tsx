"use client";
/**
 * `/trace` — Activity & trace. Three views over what the agents did:
 *
 *   - **Per request** (`?view=request`, the default) — the authorisation path of one request,
 *     below, unchanged (`DEMO_AND_FRONTEND.md` §2.2).
 *   - **Timeline** (`?view=timeline`) — every agent run, delegated question, adapter-agent sync,
 *     automation firing, approval decision and drift / ontology proposal the viewer may see, newest
 *     first, from `GET /activity`. A delegated question opens its delegation tree.
 *   - **Automations** (`?view=automations`) — each trigger, its firings, and the runs and approvals
 *     each firing produced, from `GET /activity/automations`.
 *
 * The per-request view:
 *
 * Not a node cloud. This is the communication graph between the orchestrator, each adapter agent,
 * the graph, the capture store, the ledger and the human gate — with the actual messages on the
 * edges, in the order they were sent, each one clickable for what was asked, what policy decided,
 * what came back, and what it cost.
 *
 * Three things are deliberately loud, because each is a claim a value-copying system structurally
 * cannot make:
 *
 *   1. **answered from history vs needed the source** — the graph is doing work; this is not a slow
 *      proxy in front of a database.
 *   2. **`Withheld` with a reason code, beside `Absent`** — visibly different states, never merged.
 *   3. **the untrusted cell, greyed out of the decision path** — quotable, not actionable.
 *
 * Data comes ONLY from the gateway's recorder (`GET /trace`, then `GET /trace/{request_id}`), which
 * never seeds a trace. An empty recorder renders "No requests recorded yet"; an unreachable or
 * refusing gateway renders that error. There is no example trace — the old fallback to
 * `config/platform-preview.json` drew a request nobody made, and is gone (ROADMAP §3 item 18).
 * The recorder's nulls stay null — zero would be a measurement nobody made.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, Inbox, ShieldAlert } from "lucide-react";
import { Topbar } from "@/components/topbar";
import { Panel, SourceNotice } from "@/components/platform/preview-notice";
import {
  TraceGraph,
  type TraceSelection,
} from "@/components/platform/trace-graph";
import {
  CellLedger,
  PointerProvenance,
  TraceInspector,
  TraceTimeline,
} from "@/components/platform/trace-panels";
import {
  fetchTrace,
  normaliseTrace,
  TONE_TEXT,
  type TraceListRow,
} from "@/lib/platform-api";
import type { TracePreview } from "@/lib/platform-preview";
import { layoutTrace } from "@/lib/trace-layout";
import {
  fetchActivityRun,
  viewFromParam,
  type ActivityItem,
  type ActivityView,
} from "@/lib/activity-api";
import { ActivityTimeline } from "@/components/activity/activity-timeline";
import { ActivityInspector } from "@/components/activity/activity-inspector";
import { AutomationsView } from "@/components/activity/automations-view";

const VIEW_TABS: { id: ActivityView; label: string; blurb: string }[] = [
  {
    id: "request",
    label: "Per request",
    blurb:
      "One request, end to end: who asked, under what grant and for what purpose, how much of it the graph could answer from what it already held, what the adapter's policy layer refused, and what the answer was allowed to cite. Click any participant or any message.",
  },
  {
    id: "timeline",
    label: "Timeline",
    blurb:
      "Everything the agents did that you may see — runs, delegated questions, adapter-agent syncs, automation firings, approvals and proposals — newest first, with which agent and which model did each step. Click a row for its detail; a delegated question shows its delegation tree.",
  },
  {
    id: "automations",
    label: "Automations",
    blurb:
      "Each trigger, every time it fired, and the run and approvals each firing produced — who approved, and what happened when it executed.",
  },
];

const RESULT_STYLE: Record<string, string> = {
  grounded: "border-band-auto/40",
  withheld: "border-band-review/40",
  absent: "border-dashed border-border",
};

/** A measurement, or the fact that nobody made one. Never a zero standing in for either. */
function Measured({
  value,
  suffix,
}: {
  value: number | null;
  suffix?: string;
}) {
  if (value === null) {
    return <span className="italic text-subtle/80">not recorded</span>;
  }
  return (
    <span className="tnum font-mono text-fg">
      {value.toLocaleString()}
      {suffix ?? ""}
    </span>
  );
}

export default function TracePage() {
  return (
    <React.Suspense fallback={<p className="p-6 text-xs text-subtle">Loading…</p>}>
      <ActivityAndTrace />
    </React.Suspense>
  );
}

function ActivityAndTrace() {
  const router = useRouter();
  const params = useSearchParams();
  const view = viewFromParam(params.get("view"));
  const current = VIEW_TABS.find((t) => t.id === view)!;
  const go = (next: ActivityView, request?: string) => {
    const q = new URLSearchParams();
    if (next !== "request") q.set("view", next);
    if (request) q.set("request", request);
    router.replace(`/trace${q.toString() ? `?${q}` : ""}`, { scroll: false });
  };

  return (
    <>
      <Topbar title="Activity" />
      <div className="flex flex-col gap-5 p-6">
        <header className="max-w-3xl">
          <h1 className="text-lg font-semibold">Activity &amp; trace</h1>
          <p className="text-xs text-subtle" data-testid="activity-blurb">{current.blurb}</p>
        </header>
        <div role="tablist" className="flex gap-1 border-b border-border">
          {VIEW_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={t.id === view}
              data-testid={`activity-tab-${t.id}`}
              onClick={() => go(t.id)}
              className={`-mb-px border-b-2 px-3 py-1.5 text-xs ${
                t.id === view ? "border-accent font-medium text-fg" : "border-transparent text-subtle hover:text-fg"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {view === "request" ? (
          <RunOrRequest run={params.get("run")} request={params.get("request")} />
        ) : (
          <ActivityViews view={view} onOpenTrace={(id) => go("request", id)} />
        )}
      </div>
    </>
  );
}

/** `?run=<id>` (the link shape `activity_url` hands out) resolves to the run's recorded trace
 *  request; `?request=` wins when both are given. A run with no trace you may read says so and
 *  falls back to the newest request, instead of silently showing another one. (INT5, BRAIN
 *  cross-track: the page used to ignore `?run=`.) */
function RunOrRequest({ run, request }: { run: string | null; request: string | null }) {
  const needsRun = !request && !!run;
  const resolved = useQuery({
    queryKey: ["trace-run-request", run],
    queryFn: ({ signal }) => fetchActivityRun(run as string, signal),
    enabled: needsRun,
    retry: false,
  });
  if (!needsRun) return <PerRequest requested={request ?? undefined} />;
  if (resolved.isLoading) {
    return (
      <p className="text-xs text-subtle" data-testid="trace-run-resolving">
        Finding the request run <span className="font-mono">{run}</span> recorded…
      </p>
    );
  }
  const found = resolved.data?.item?.trace_request_id ?? undefined;
  return (
    <>
      {found ? null : (
        <p className="text-xs text-subtle" data-testid="trace-run-unresolved">
          Run <span className="font-mono">{run}</span> has no recorded request you may read
          {resolved.error ? ` (${(resolved.error as Error).message})` : ""}; showing the newest
          request instead.
        </p>
      )}
      <PerRequest requested={found} />
    </>
  );
}

function ActivityViews({
  view,
  onOpenTrace,
}: {
  view: Exclude<ActivityView, "request">;
  onOpenTrace: (requestId: string) => void;
}) {
  const [item, setItem] = React.useState<ActivityItem | null>(null);
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]">
      <div className="min-w-0">
        {view === "timeline" ? (
          <ActivityTimeline selected={item?.id ?? null} onSelect={setItem} />
        ) : (
          <AutomationsView selected={item?.id ?? null} onSelect={setItem} />
        )}
      </div>
      <Panel title="Detail" className="xl:sticky xl:top-4 xl:self-start" hint="click a row">
        <div className="-mx-4 -my-4 max-h-[80vh] overflow-auto">
          <ActivityInspector item={item} onOpenTrace={onOpenTrace} onSelect={setItem} />
        </div>
      </Panel>
    </div>
  );
}

function PerRequest({ requested }: { requested?: string }) {
  const [wanted, setWanted] = React.useState<string | undefined>(requested);
  React.useEffect(() => setWanted(requested), [requested]);

  const query = useQuery({
    queryKey: ["trace", wanted ?? "latest"],
    queryFn: ({ signal }) => fetchTrace(wanted, signal),
    staleTime: 15_000,
    retry: false,
  });
  const result = query.data;

  return (
    <>
      <div className="flex flex-col gap-5">

        {query.isLoading ? (
          <p className="text-xs text-subtle" data-testid="trace-loading">
            Asking the gateway&apos;s recorder…
          </p>
        ) : null}

        {result?.state === "empty" ? (
          <div
            className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-surface px-6 py-14 text-center"
            data-testid="trace-empty"
          >
            <Inbox className="h-6 w-6 text-subtle" />
            <div className="text-sm font-semibold text-fg">
              No requests recorded yet
            </div>
            <p className="max-w-md text-xs text-subtle">
              A trace is recorded when a request runs through the gateway — ask
              something on{" "}
              <a href="/graph" className="text-accent hover:underline">
                the graph
              </a>{" "}
              or run an agent, then come back. Nothing is shown until something
              has happened.
            </p>
            <p className="text-2xs text-subtle/80">
              <code>{result.endpoint}</code> — {result.reason}
            </p>
          </div>
        ) : null}

        {result?.state === "error" ? (
          <div
            className="flex items-start gap-2 rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-xs text-band-review"
            role="alert"
            data-testid="trace-error"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              Could not load a trace from <code>{result.endpoint}</code>:{" "}
              {result.reason}. No trace is shown — this page draws only what the
              recorder returns.
            </span>
          </div>
        ) : null}

        {result?.state === "live" && wanted && result.trace.request_id !== wanted ? (
          <p className="text-2xs text-band-spot" data-testid="trace-not-held">
            Request <code>{wanted}</code> is not among the newest 25 requests recorded for you —
            it may have been evicted (the recorder holds a bounded number in memory), or it is not
            one you may read — so the newest request recorded for you is shown instead.
          </p>
        ) : null}

        {result?.state === "live" ? (
          <LiveTrace
            trace={result.trace}
            endpoint={result.endpoint}
            available={result.available}
            onPick={setWanted}
          />
        ) : null}
      </div>
    </>
  );
}

function LiveTrace({
  trace,
  endpoint,
  available,
  onPick,
}: {
  trace: TracePreview;
  endpoint: string;
  available: TraceListRow[];
  onPick: (requestId: string) => void;
}) {
  const model = React.useMemo(() => normaliseTrace(trace), [trace]);
  const layout = React.useMemo(
    () => layoutTrace(model.nodes, model.edges),
    [model.nodes, model.edges],
  );

  const lastStep = layout.edges.length
    ? layout.edges[layout.edges.length - 1].step
    : 0;
  const [step, setStep] = React.useState(lastStep);
  const [selection, setSelection] = React.useState<TraceSelection>(null);

  // A new payload is a new sequence: show it whole rather than stranding the reader mid-replay.
  React.useEffect(() => {
    setStep(lastStep);
    setSelection(null);
  }, [lastStep, trace]);

  const { request, answer } = model.trace;
  const budgetPct =
    request.budget_used !== null && request.budget_total
      ? Math.round((request.budget_used / request.budget_total) * 100)
      : null;
  const cells = model.trace.cells ?? [];
  const integrity = model.trace.integrity;

  return (
    <>
      <SourceNotice live endpoint={endpoint} />

      {/* More than one recorded request: pick which. The list is newest first. */}
      {available.length > 1 ? (
        <label className="flex flex-wrap items-center gap-2 text-2xs text-subtle">
          <span className="uppercase tracking-wider">recorded request</span>
          <select
            value={model.trace.request_id ?? available[0].request_id}
            onChange={(e) => onPick(e.target.value)}
            className="rounded border border-border bg-surface px-2 py-1 font-mono text-2xs text-fg outline-none focus:border-accent"
          >
            {available.map((r) => (
              <option key={r.request_id} value={r.request_id}>
                {r.request_id} · {r.surface} · {r.outcome ?? "in flight"}
                {r.complete ? "" : " · incomplete"}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {integrity && !integrity.complete ? (
        <div
          className="flex items-start gap-2 rounded border border-band-spot/40 bg-band-spot/[0.06] px-3 py-2 text-2xs text-subtle"
          data-testid="trace-integrity"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
          <span>
            <span className="font-medium text-fg">
              This trace is incomplete, and says so.
            </span>{" "}
            {integrity.defect_count} step(s) could not be recorded:{" "}
            <code className="text-band-spot">
              {integrity.defect_codes.join(", ")}
            </code>
            . The recorder reports what it could not capture rather than
            presenting a clean record of a request it only partly saw.
          </span>
        </div>
      ) : null}

      {model.derived ? (
        <p
          className="text-2xs text-band-spot"
          data-testid="trace-derived-notice"
        >
          The response carried counts but no <code>nodes</code>/
          <code>edges</code>, so the sequence below was reconstructed from those
          counts, following the fixed order in AGENT_PLANE §7. Nothing is
          invented — anything the payload does not say is not drawn.
        </p>
      ) : null}

      {/* The ask. Purpose and on-behalf-of are principal, not metadata: they are what the policy
            layer decides against, so they sit at the top of the path rather than in a tooltip. */}
      <Panel title="Request" hint="purpose-as-principal">
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
          {[
            { k: "agent", v: `${request.agent} ${request.version}`.trim() },
            { k: "purpose", v: request.purpose },
            { k: "on_behalf_of", v: request.on_behalf_of },
            { k: "grant", v: request.grant },
          ].map((f) => (
            <div key={f.k}>
              <div className="text-2xs uppercase tracking-wider text-subtle/70">
                {f.k}
              </div>
              <div className="font-mono text-xs text-fg">
                {f.v ?? (
                  <span className="italic text-subtle/80">not recorded</span>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-3">
          <div className="flex items-baseline justify-between text-2xs text-subtle">
            <span>budget</span>
            <span>
              <Measured value={request.budget_used} /> /{" "}
              <Measured value={request.budget_total} suffix=" tokens" />
            </span>
          </div>
          {budgetPct !== null ? (
            <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-accent"
                style={{ width: `${budgetPct}%` }}
              />
            </div>
          ) : null}
        </div>
      </Panel>

      {/* The graph and its inspector. Two columns on a wide screen, stacked below it — the panel
            is where the detail lives, so it must never be off-screen when a node is clicked. */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Panel
          title="Communication graph"
          hint={`${layout.edges.length} messages`}
        >
          <TraceTimeline edges={layout.edges} step={step} setStep={setStep} />
          <div className="mt-3">
            <TraceGraph
              layout={layout}
              step={step}
              selection={selection}
              onSelect={setSelection}
            />
          </div>
        </Panel>

        <Panel
          title="Inspector"
          className="xl:sticky xl:top-4 xl:self-start"
          hint="click to fill"
        >
          <div className="-mx-4 -my-4">
            <TraceInspector
              selection={selection}
              nodes={model.nodes}
              edges={layout.edges}
            />
          </div>
        </Panel>
      </div>

      {model.provenance ? (
        <Panel title="Where the answers came from" hint="pointers, not values">
          <PointerProvenance p={model.provenance} />
        </Panel>
      ) : null}

      {cells.length ? (
        <Panel
          title="Every field the answer asked for"
          hint="grounded · withheld · absent"
        >
          <CellLedger cells={cells} note={model.trace.cells_note} />
        </Panel>
      ) : null}

      {/* What came back, split by STATE. `Withheld` is not a missing value and must never render
            as one — a reason code is the difference between a policy decision and a gap. */}
      {model.trace.results.length ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {model.trace.results.map((r) => (
            <div
              key={r.kind}
              data-testid={`trace-result-${r.kind}`}
              className={`flex items-start gap-3 rounded-lg border bg-surface px-4 py-3 ${
                RESULT_STYLE[r.kind] ?? "border-border"
              }`}
            >
              <CheckCircle2
                className={`mt-0.5 h-4 w-4 shrink-0 ${TONE_TEXT[r.tone]}`}
              />
              <div>
                <div className="text-sm font-medium text-fg">
                  <span className="tnum">{r.count}</span>{" "}
                  <span className="uppercase tracking-wide">{r.kind}</span>
                </div>
                <div className={`text-2xs ${TONE_TEXT[r.tone]}`}>
                  {r.detail}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <Panel
        title="Answer"
        hint="admission decided against the certificate budget"
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
          <span className="text-subtle">
            citations{" "}
            <Measured value={Number.isFinite(answer.citations) ? answer.citations : null} />
          </span>
          <span className="text-subtle">
            certificate risk <Measured value={answer.certificate_risk} />
          </span>
          <span className="text-subtle">
            budget <Measured value={answer.risk_budget} />
          </span>
          {answer.admitted === null ? (
            <span className="italic text-subtle/80">
              admission not recorded
            </span>
          ) : (
            <span
              className={
                answer.admitted
                  ? "font-medium text-band-auto"
                  : "font-medium text-band-review"
              }
            >
              {answer.admitted ? "ADMITTED" : "REFUSED"}
            </span>
          )}
        </div>
        {answer.note ? (
          <p className="mt-2 text-2xs text-subtle">{answer.note}</p>
        ) : null}
        <p className="mt-3 flex items-start gap-1.5 text-2xs text-subtle">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
          <span>
            An untrusted cell is quotable, never actionable: it may appear in
            the answer with its pointer and is excluded from any value the
            decision turns on. Trust propagates by least-upper-bound, so one
            untrusted input taints everything derived from it.
          </span>
        </p>
      </Panel>
    </>
  );
}
