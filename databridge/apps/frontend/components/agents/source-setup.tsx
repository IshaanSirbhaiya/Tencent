"use client";
/**
 * FEAT8 — set up a source through its adapter agent, from the Builder's Adapter agents tab.
 *
 * Every registered read source is listed as "ready to set up" (read from `/adapter-setup/sources`,
 * which reads the Sources registry — nothing is registered here). One **Set up** runs the existing
 * pipeline as the agent's steps, with progress for each:
 *
 *   plan proposed → a person approves it (and vouches, inline) → a capture-only discovery reads and
 *   screens the source, builds nothing → the mapper proposes → the agent explains (advisory: every
 *   sentence is a fact the agent computed; a fact of the captured table cites its cells with a pointer,
 *   a proposal cites nothing and says so) → a person approves tables against that explanation, and
 *   links / holds / identity rules on the existing routes → hand-off: the person activates the agent,
 *   which builds and then keeps the source.
 *
 * The page advances on its own only through steps that are not decisions (`nextAutoStep`). Every
 * decision is a button the signed-in person presses; the gateway records them, and the agent's own
 * principal is refused by every approval gate.
 */
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, CircleDot, Hand, Loader2, Lock, Play } from "lucide-react";
import { cn } from "@/lib/utils";
import { agentsApi, isTerminalJobStatus, type Job } from "@/lib/agents-api";
import {
  CADENCES,
  approvableTables,
  basisWords,
  canRediscover,
  citedEvidence,
  documentsDecisionOffered,
  handoffOffered,
  liveStatus,
  mappingApprovalOffered,
  nextAutoStep,
  personWords,
  pointerText,
  setupApi,
  type Cadence,
  type Claim,
  type Evidence,
  type SetupState,
  type SetupStep,
  type TableExplanation,
} from "@/lib/adapter-setup-api";
import {
  planApprovalBlockers,
  planToolAudit,
  sourcesApi,
  unwrap,
  vouchableTools,
  type CrawlPlan,
  type Connection,
} from "@/lib/sources-api";
import { PlanView } from "@/components/sources/plan-view";
import { KindSplitView, LinksPanel } from "@/components/sources/mapping-review";
import { ScreenHoldsSection } from "@/components/sources/screen-holds";
import { IdentityRulesSection } from "@/components/sources/identity-rules";
import { ErrorLine, Section, StatusPill, fmtTime } from "./shared";

const btn =
  "inline-flex h-7 items-center gap-1.5 rounded px-3 text-xs font-medium disabled:opacity-40";

// ============================================================================================
// the list: every read source, ready to set up
// ============================================================================================
export function SourcesToSetUp({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const list = useQuery({ queryKey: ["adapter-setup-sources"], queryFn: ({ signal }) => setupApi.sources(signal) });
  const rows = list.data?.sources ?? [];
  return (
    <Section
      title="Sources"
      testId="setup-sources"
      aside={
        <Link href="/connectors" className="text-2xs text-accent hover:underline">
          Add a source on Sources →
        </Link>
      }
    >
      <p className="mb-2 text-2xs text-subtle">
        Every source you have added appears here. Set up hands it to an adapter agent: it proposes how to read the
        source and what its tables mean, you approve, and it builds the source and keeps it in sync.
      </p>
      <ErrorLine error={list.error} testId="setup-sources-error" />
      {list.data && rows.length === 0 && (
        <p data-testid="setup-sources-empty" className="text-xs text-subtle">
          No read sources yet. Add one on Sources; it appears here.
        </p>
      )}
      <ul className="flex flex-col divide-y divide-border">
        {rows.map((s) => (
          <li key={s.source_id} data-testid={`setup-source-${s.source_id}`} className={cn("flex items-center gap-2 py-1.5", selected === s.source_id && "bg-muted")}>
            <div className="min-w-0 flex-1">
              <div className="font-mono text-xs">{s.source_id}</div>
              <div className="text-2xs text-subtle">
                {s.phase === "ready"
                  ? "ready to set up"
                  : s.phase === "kept"
                    ? `kept by ${s.keeper?.id}`
                    : `setting up · ${s.steps.filter((x) => x.status === "done").length}/${s.steps.length} steps`}
              </div>
            </div>
            <button
              type="button"
              data-testid={`setup-open-${s.source_id}`}
              onClick={() => onSelect(s.source_id)}
              className={cn(btn, s.phase === "ready" ? "bg-accent text-accent-fg hover:bg-accent/90" : "border border-border hover:bg-muted")}
            >
              {s.phase === "ready" ? "Set up" : s.phase === "kept" ? "Open" : "Continue"}
            </button>
          </li>
        ))}
      </ul>
      {(list.data?.problems ?? []).length > 0 && (
        <ul className="mt-2 list-disc pl-4 text-2xs text-band-review">
          {list.data!.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ============================================================================================
// progress
// ============================================================================================
function StepIcon({ status }: { status: SetupStep["status"] }) {
  if (status === "done") return <Check className="h-3.5 w-3.5 text-band-auto" aria-label="done" />;
  if (status === "doing") return <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" aria-label="in progress" />;
  if (status === "waiting_for_person") return <Hand className="h-3.5 w-3.5 text-band-spot" aria-label="waiting for you" />;
  if (status === "blocked") return <Lock className="h-3.5 w-3.5 text-band-review" aria-label="blocked" />;
  return <CircleDot className="h-3.5 w-3.5 text-subtle" aria-label="to do" />;
}

function Steps({ steps }: { steps: SetupStep[] }) {
  return (
    <ol data-testid="setup-steps" className="flex flex-col gap-1">
      {steps.map((s) => (
        <li key={s.id} data-testid={`setup-step-${s.id}`} data-status={s.status} className="flex items-start gap-2 text-xs">
          <StepIcon status={s.status} />
          <div className="min-w-0">
            <span className={cn(s.status === "todo" && "text-subtle")}>{s.label}</span>
            {s.detail && <span className="ml-2 text-2xs text-subtle">{s.detail}</span>}
          </div>
        </li>
      ))}
    </ol>
  );
}

// ============================================================================================
// the plan + vouch, inline (the Sources approval route, through the agent's wrapper)
// ============================================================================================
function PlanApproval({
  id,
  plan,
  planDigest,
  connection,
  onDone,
}: {
  id: string;
  plan: CrawlPlan;
  planDigest: string | null;
  connection: Connection;
  onDone: () => void;
}) {
  const cat = useQuery({ queryKey: ["catalogue"], queryFn: () => unwrap(sourcesApi.catalogue()) });
  const entry = cat.data?.connections.find((c) => c.id === id) ?? null;
  const [vouched, setVouched] = React.useState<string[]>([]);
  const vouchable = vouchableTools(plan, entry);
  const blockers = planApprovalBlockers(plan, entry, connection.purpose, vouched);
  const approve = useMutation({ mutationFn: () => setupApi.approvePlan(id, vouched, planDigest), onSuccess: onDone });
  return (
    <div data-testid="setup-plan" className="flex flex-col gap-2">
      <PlanView plan={plan} connection={connection} audit={planToolAudit(plan, entry)} />
      {vouchable.length > 0 && (
        <div data-testid="setup-vouch" className="rounded border border-band-spot/40 p-2 text-2xs">
          <p className="mb-1 font-medium">Vouch that a tool only reads</p>
          {vouchable.map((v) => (
            <label key={v.tool} className="flex items-start gap-1.5">
              <input
                type="checkbox"
                data-testid={`setup-vouch-${v.tool}`}
                checked={vouched.includes(v.tool)}
                onChange={(e) => setVouched((xs) => (e.target.checked ? [...xs, v.tool] : xs.filter((x) => x !== v.tool)))}
              />
              <span>
                <span className="font-mono">{v.tool}</span> — {v.reason}
              </span>
            </label>
          ))}
        </div>
      )}
      {plan.status === "proposed" && (
        <>
          {blockers.length > 0 && (
            <ul data-testid="setup-plan-blockers" className="list-disc pl-4 text-2xs text-band-spot">
              {blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          )}
          <div>
            <button
              type="button"
              data-testid="setup-approve-plan"
              disabled={blockers.length > 0 || approve.isPending}
              onClick={() => approve.mutate()}
              className={cn(btn, "bg-accent text-accent-fg hover:bg-accent/90")}
            >
              Approve the plan
            </button>
          </div>
        </>
      )}
      <ErrorLine error={approve.error} testId="setup-plan-error" />
    </div>
  );
}

// ============================================================================================
// the agent's explanation
// ============================================================================================
function EvidenceChip({ e }: { e: Evidence }) {
  return (
    <span
      data-testid={`setup-evidence-${e.id}`}
      title={pointerText(e.pointer)}
      className="mr-1 inline-flex items-center gap-1 rounded border border-border px-1 font-mono text-2xs text-subtle"
    >
      {e.column}
      {e.value !== null && <span className="text-fg">= {e.value}</span>}
      {e.masked && <span className="text-band-spot">masked</span>}
    </span>
  );
}

function ClaimLine({ claim, evidence, testId }: { claim: Claim; evidence: Evidence[]; testId?: string }) {
  const cited = citedEvidence(claim, evidence);
  const basis = basisWords(claim);
  return (
    <li data-testid={testId} className="text-xs">
      {claim.text}{" "}
      {basis && <span className="mr-1 text-2xs italic text-subtle">({basis})</span>}
      {cited.map((e) => (
        <EvidenceChip key={e.id} e={e} />
      ))}
    </li>
  );
}

function TableCard({ t, split }: { t: TableExplanation; split?: Parameters<typeof KindSplitView>[0]["split"] }) {
  return (
    <div data-testid={`setup-explain-${t.table}`} className="rounded border border-border p-2.5">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-medium">{t.table}</span>
        <StatusPill status={t.status} />
        <span className="text-2xs">
          becomes <span className="font-mono">{t.kind ?? "—"}</span> · {personWords(t)}
        </span>
        <span className="ml-auto text-2xs text-subtle">
          explained by {t.explained_by}
          {t.model_dropped > 0 && ` · ${t.model_dropped} unknown choice(s) dropped`}
        </span>
      </div>
      <ul className="flex flex-col gap-1">
        <ClaimLine claim={t.summary} evidence={t.evidence} testId={`setup-summary-${t.table}`} />
        {t.reasons.map((r, i) => (
          <ClaimLine key={i} claim={r} evidence={t.evidence} />
        ))}
      </ul>
      {(t.keys.primary_key.length > 0 || t.relationships.length > 0) && (
        <div className="mt-1.5 text-2xs text-subtle">
          {t.keys.primary_key.length > 0 && <>keys {t.keys.primary_key.join(", ")}</>}
          {t.keys.candidate_identifier && <> · candidate identifier {t.keys.candidate_identifier} (shown to a person, never merged)</>}
          {t.relationships.map((r, i) => (
            <span key={i}>
              {" "}
              · {r.column ?? "row"} → {r.target_table ?? "?"} as <span className="font-mono">{r.edge_kind}</span>
            </span>
          ))}
        </div>
      )}
      {split && <KindSplitView table={t.table} split={split} />}
      {t.capture_problem && <p className="mt-1 text-2xs text-band-review">{t.capture_problem}</p>}
      {t.not_sent_reason && <p className="mt-1 text-2xs text-subtle">{t.not_sent_reason}</p>}
    </div>
  );
}

// ============================================================================================
// the panel
// ============================================================================================
export function SourceSetupPanel({ id, openAgent }: { id: string; openAgent: (agentId: string) => void }) {
  const qc = useQueryClient();
  const detail = useQuery({
    queryKey: ["adapter-setup", id],
    queryFn: ({ signal }) => setupApi.get(id, signal),
    refetchInterval: (q) => ((q.state.data as { state?: SetupState } | undefined)?.state?.running ? 1500 : false),
  });
  const review = useQuery({ queryKey: ["adapter-setup-review", id], queryFn: () => unwrap(sourcesApi.mappingReview(id)) });
  const refresh = React.useCallback(() => {
    qc.invalidateQueries({ queryKey: ["adapter-setup", id] });
    qc.invalidateQueries({ queryKey: ["adapter-setup-sources"] });
    qc.invalidateQueries({ queryKey: ["adapter-setup-review", id] });
  }, [qc, id]);
  const start = useMutation({ mutationFn: () => setupApi.start(id), onSuccess: refresh });
  const discover = useMutation({ mutationFn: () => setupApi.discover(id), onSuccess: refresh });
  const explain = useMutation({ mutationFn: () => setupApi.explain(id), onSuccess: refresh });
  const [chosen, setChosen] = React.useState<string[] | null>(null);
  const approveMap = useMutation({
    mutationFn: ({ tables, digest }: { tables: string[]; digest: string }) => setupApi.approveMapping(id, tables, digest),
    onSuccess: refresh,
  });
  const [cadence, setCadence] = React.useState<Cadence>("daily");
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [deferred, setDeferred] = React.useState<string[]>([]);
  const handoff = useMutation({
    mutationFn: () => setupApi.handoff(id, cadence, deferred),
    onSuccess: (r) => (setJobId(r.job_id), refresh()),
  });
  const approveDocs = useMutation({
    mutationFn: ({ run, digest }: { run: string; digest: string }) => setupApi.approveDocuments(id, run, digest),
    onSuccess: refresh,
  });
  const d = detail.data;
  const state = d?.state;
  const job = useQuery({
    queryKey: ["adapter-setup-job", state?.keeper?.id, jobId],
    queryFn: ({ signal }) => agentsApi.adapterJob(state!.keeper!.id, jobId!, signal),
    enabled: !!jobId && !!state?.keeper,
    refetchInterval: (q) => (isTerminalJobStatus((q.state.data as Job | undefined)?.status) ? false : 1500),
  });

  // advance through the steps that are not decisions — once per state
  const auto = state ? nextAutoStep(state) : null;
  const fired = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!auto || discover.isPending || explain.isPending) return;
    const key = `${auto}:${state?.runs}:${state?.mapping_counts.proposed}:${state?.mapping_counts.approved}`;
    if (fired.current === key) return;
    fired.current = key;
    if (auto === "discover") discover.mutate();
    else explain.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, state?.runs, state?.mapping_counts.proposed, state?.mapping_counts.approved]);

  if (detail.isLoading) return <p className="text-xs text-subtle">Loading…</p>;
  if (!d || !state) return <ErrorLine error={detail.error} testId="setup-error" />;
  const plan = d.plan as unknown as CrawlPlan | null;
  const ex = d.explanation;
  const live = d.mapping?.tables ?? null;
  const approvable = approvableTables(ex, live);
  const picked = chosen ?? approvable;
  const splits = review.data?.splits ?? {};
  return (
    <div data-testid="setup-panel" className="flex flex-col gap-4">
      <Section
        title={
          <span className="flex items-center gap-1.5">
            <Bot className="h-3.5 w-3.5" /> Set up <span className="font-mono">{id}</span>
          </span>
        }
        testId="setup-progress"
        aside={state.keeper && <StatusPill status={state.keeper.status} testId="setup-keeper-status" />}
      >
        <Steps steps={state.steps} />
        {!state.keeper && (
          <button
            type="button"
            data-testid="setup-start"
            disabled={start.isPending}
            onClick={() => start.mutate()}
            className={cn(btn, "mt-2 bg-accent text-accent-fg hover:bg-accent/90")}
          >
            <Play className="h-3 w-3" /> Set up
          </button>
        )}
        {state.keeper && !plan && (
          <button type="button" data-testid="setup-propose" onClick={() => start.mutate()} className={cn(btn, "mt-2 border border-border hover:bg-muted")}>
            Propose the plan
          </button>
        )}
        {canRediscover(state) && (
          <button type="button" data-testid="setup-rediscover" disabled={discover.isPending} onClick={() => discover.mutate()} className={cn(btn, "mt-2 border border-border hover:bg-muted")}>
            Discover again
          </button>
        )}
        {state.plan_stale && (
          <button type="button" data-testid="setup-repropose" onClick={() => start.mutate()} className={cn(btn, "mt-2 border border-border hover:bg-muted")}>
            Propose the plan again
          </button>
        )}
        <div className="mt-2 flex flex-col gap-1">
          <ErrorLine error={start.error} testId="setup-start-error" />
          <ErrorLine error={discover.error} testId="setup-discover-error" />
          <ErrorLine error={explain.error} testId="setup-explain-error" />
        </div>
        {state.keeper && (
          <p className="mt-2 text-2xs text-subtle">
            Agent{" "}
            <button type="button" className="font-mono text-accent hover:underline" onClick={() => openAgent(state.keeper!.id)}>
              {state.keeper.id}
            </button>{" "}
            · it proposes; you decide. Discovery only reads and screens the source — no table or document is built until you
            approve the mapping and hand off.
          </p>
        )}
      </Section>

      {plan && state.steps.find((s) => s.id === "approve_plan")?.status !== "done" && (
        <Section title="The agent's crawl plan — approve it to let it read the source" testId="setup-plan-section">
          <PlanApproval id={id} plan={plan} planDigest={d.plan_digest} connection={d.connection as unknown as Connection} onDone={refresh} />
        </Section>
      )}

      {ex && (
        <Section
          title="What the agent thinks each table is"
          testId="setup-explanation"
          aside={
            <span className="text-2xs text-subtle">
              {ex.produced_by.mode === "model" ? `model ${ex.produced_by.model ?? ""}` : "rules"} · {fmtTime(ex.generated_at)}
            </span>
          }
        >
          <p className="mb-2 text-2xs text-subtle">
            Advice, not a decision. Every sentence is a fact the agent computed: a fact of the captured table cites its cells
            (hover a value for where it is); a proposal or a vocabulary rule cites nothing and says so. You approve the mapping
            below, against this explanation.
            {!state.explanation_fresh && " This explanation is not the current one (the mapping or the discovery changed); the agent explains it again."}
          </p>
          <div className="flex flex-col gap-2">
            {ex.tables.map((t) => (
              <div key={t.table} className="flex items-start gap-2">
                {liveStatus(t, live) === "proposed" && (
                  <input
                    type="checkbox"
                    aria-label={`approve ${t.table}`}
                    data-testid={`setup-pick-${t.table}`}
                    checked={picked.includes(t.table)}
                    onChange={(e) => setChosen(e.target.checked ? [...picked, t.table] : picked.filter((x) => x !== t.table))}
                    className="mt-3"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <TableCard t={{ ...t, status: liveStatus(t, live) as TableExplanation["status"] }} split={splits[t.table]} />
                </div>
              </div>
            ))}
          </div>
          {approvable.length > 0 && (
            <button
              type="button"
              data-testid="setup-approve-mapping"
              disabled={!picked.length || approveMap.isPending || !mappingApprovalOffered(state)}
              onClick={() => state.explanation_digest && approveMap.mutate({ tables: picked, digest: state.explanation_digest })}
              className={cn(btn, "mt-3 bg-accent text-accent-fg hover:bg-accent/90")}
            >
              Approve {picked.length} table(s)
            </button>
          )}
          <ErrorLine error={approveMap.error} testId="setup-approve-mapping-error" />
          {review.data && <LinksPanel sourceId={id} review={review.data} onDone={refresh} />}
        </Section>
      )}

      {state.documents.digest && (
        <Section title="Documents" testId="setup-documents">
          <p className="mb-2 text-2xs text-subtle">
            The discovery captured {state.documents.captured} document(s): {state.documents.allowed} the screen allowed
            {state.documents.held > 0 && `, ${state.documents.held} held for review`}. Nothing is built until you decide to build
            them.
          </p>
          {state.documents.decided ? (
            <p data-testid="setup-documents-decided" className="text-xs">
              You decided to build them; they are built at the hand-off.
            </p>
          ) : (
            <button
              type="button"
              data-testid="setup-approve-documents"
              disabled={!documentsDecisionOffered(state.documents) || approveDocs.isPending}
              onClick={() =>
                state.documents.digest &&
                state.documents.discovery_run_id &&
                approveDocs.mutate({ run: state.documents.discovery_run_id, digest: state.documents.digest })
              }
              className={cn(btn, "bg-accent text-accent-fg hover:bg-accent/90")}
            >
              Build these {state.documents.allowed} document(s)
            </button>
          )}
          <ErrorLine error={approveDocs.error} testId="setup-approve-documents-error" />
        </Section>
      )}

      {(ex || state.documents.digest) && <ScreenHoldsSection sourceId={id} />}
      {(ex || state.documents.digest) && <IdentityRulesSection />}

      {(ex || state.documents.digest) && (
        <Section title="Hand off to the agent" testId="setup-handoff">
          {state.keeper?.status === "active" ? (
            <p className="text-xs">
              <span className="font-mono">{state.keeper.id}</span> keeps this source: it syncs, watches for changes in shape and
              puts anything people-related to you.
            </p>
          ) : (
            <>
              <p className="mb-2 text-2xs text-subtle">
                You activate the agent as yourself. It builds what you approved, then keeps the source in sync; drift is
                handled as on any adapter agent.
              </p>
              {state.pending_decisions.length > 0 && (
                <div data-testid="setup-pending" className="mb-2 rounded border border-band-spot/40 p-2 text-2xs">
                  <p className="mb-1 font-medium">Not decided yet — tick each to hand off without it</p>
                  {state.pending_decisions.map((p) => (
                    <label key={p.key} className="flex items-start gap-1.5">
                      <input
                        type="checkbox"
                        data-testid={`setup-defer-${p.key}`}
                        checked={deferred.includes(p.key)}
                        onChange={(e) => setDeferred((xs) => (e.target.checked ? [...xs, p.key] : xs.filter((x) => x !== p.key)))}
                      />
                      <span>
                        <span className="font-medium">{p.deferral}</span> — {p.what}
                      </span>
                    </label>
                  ))}
                </div>
              )}
              {state.handoff_problems.length > 0 && (
                <ul data-testid="setup-handoff-blockers" className="mb-2 list-disc pl-4 text-2xs text-band-spot">
                  {state.handoff_problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              <div className="flex items-center gap-2">
                <select
                  data-testid="setup-cadence"
                  value={cadence}
                  onChange={(e) => setCadence(e.target.value as Cadence)}
                  className="h-7 rounded border border-border bg-surface px-2 text-xs"
                >
                  {CADENCES.map((c) => (
                    <option key={c} value={c}>
                      sync {c}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  data-testid="setup-handoff-button"
                  disabled={!handoffOffered(state, deferred) || handoff.isPending}
                  onClick={() => handoff.mutate()}
                  className={cn(btn, "bg-accent text-accent-fg hover:bg-accent/90")}
                >
                  Activate and build
                </button>
              </div>
            </>
          )}
          {job.data && (
            <p data-testid="setup-build-job" className="mt-2 text-2xs">
              build run <StatusPill status={job.data.status} />
            </p>
          )}
          <ErrorLine error={handoff.error} testId="setup-handoff-error" />
        </Section>
      )}

      {state.activity.length > 0 && (
        <Section title={`Activity (${state.activity.length})`} testId="setup-activity">
          <ul className="flex flex-col gap-1">
            {state.activity.map((a) => (
              <li key={a.run_id} className="text-2xs">
                <span className="text-subtle">{fmtTime(a.at)}</span> {a.reason}
              </li>
            ))}
          </ul>
          <Link href="/activity" className="mt-1 inline-block text-2xs text-accent hover:underline">
            All activity →
          </Link>
        </Section>
      )}
    </div>
  );
}
