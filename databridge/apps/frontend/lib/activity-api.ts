/**
 * `/activity` — the gateway's read-only feed of what the agents did (`activity_routes.py`).
 *
 * Every row is a record some producer already wrote (agent runs, federated runs, adapter-agent
 * syncs, automation firings, approval decisions, drift and ontology proposals). This module only
 * fetches and projects; it never fills a gap. A `null` from the gateway is "not recorded" on the
 * page — never 0, never a guess. The pure helpers at the bottom are covered by `@unit` specs in
 * `e2e/activity.spec.ts`.
 */
import { GATEWAY, authHeaders } from "./api";
import { adaptGatewayTrace, type GatewayTrace } from "./trace-adapt";
import type { TracePreview } from "./platform-preview";

// ---------------------------------------------------------------------------------------------
// contracts (mirroring activity_routes.py)
// ---------------------------------------------------------------------------------------------
export const ACTIVITY_KINDS = [
  "agent_run",
  "federated_run",
  "adapter_sync",
  "firing",
  "approval",
  "drift_proposal",
  "ontology_proposal",
] as const;
export type ActivityKind = (typeof ACTIVITY_KINDS)[number];

export type ActorType = "agent" | "adapter_agent" | "automation" | "person";
export interface Actor {
  type: ActorType;
  id: string | null;
  /** FIN-16: a person authenticated with the public development token, not a real sign-in. */
  dev_only?: boolean;
}

/** The plain words shown after a person who acted under the development token (FIN-16). */
export const DEV_TOKEN_NOTE = "dev token, not a real sign-in";

/** A decider's name, marked when the decision event records the development token. */
export function deciderText(by: string | null | undefined, devOnly: boolean | null | undefined): string {
  if (!by) return NOT_RECORDED;
  return devOnly ? `${by} (${DEV_TOKEN_NOTE})` : by;
}

/** §5.8 — who produced a thing. Every key may be absent or null. */
export interface ProducedBy {
  agent?: string | null;
  agent_version?: string | number | null;
  task_class?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  run_id?: string | null;
  at?: string | null;
  by?: string | null;
  method?: string | null;
  adapter_agent?: string | null;
  mode?: string | null;
  model_calls?: number | null;
}

export interface HistoryEvent {
  event?: string;
  status?: string;
  by?: string | null;
  at?: string | null;
  detail?: string;
  reason?: string;
  note?: string;
  outcome?: string;
  recorded_at?: string;
  attempt?: number;
  error?: string;
  /** A firing's error: only the exception TYPE is served; its text is not (Codex f2 #9). */
  error_type?: string | null;
  error_withheld?: string;
  reason_withheld?: string;
  // `decision_refused` (agent_runtime `_log_refusal`)
  decision?: string;
  reason_code?: string;
  claimed_by?: string | null;
}

/** One refused approve / reject / reverse. `by` is set only for a verified principal; an
 *  unverified asker is recorded as `claimed_by`, never as who decided. */
export interface DecisionRefusal {
  decision: string | null;
  reason_code: string | null;
  /** No longer served (Codex f2 #9): `reason_code` says why. */
  detail?: string | null;
  by: string | null;
  claimed_by: string | null;
  at: string | null;
}

export type RunKind = "agent" | "federated" | "adapter_agent";

export interface ActivityItem {
  id: string;
  kind: ActivityKind;
  at: string | null;
  sort_key: string;
  cursor: string;
  title: string;
  status: string | null;
  actor: Actor;
  started_by?: Actor | null;
  produced_by: ProducedBy | null;
  models_used: string[] | null;
  tokens: number | null;
  /** The run header's metered cost (EF4); null when nothing measured it. */
  cost_usd?: number | null;
  /** Where `tokens`/`cost_usd` came from: the run record's header, the trace, or nowhere. */
  usage_source?: "run_record" | "trace" | null;
  duration_ms: number | null;
  run_id?: string | null;
  /** The run's namespace (Codex f1 #7): a run id is unique only within its kind + owner directory. */
  run_kind?: RunKind | null;
  run_owner?: string | null;
  loud: boolean;
  // runs
  started?: string | null;
  ended?: string | null;
  steps?: number | null;
  actions?: Record<string, number> | null;
  delegations?: number | null;
  refused_delegations?: number | null;
  coverage_reduced?: boolean | null;
  trace_request_id?: string | null;
  reason?: string | null;
  trigger?: Record<string, unknown>;
  // firings
  idempotency_key?: string;
  spec_id?: string | null;
  spec_kind?: string | null;
  trigger_type?: string | null;
  scheduled_for?: string | null;
  events?: HistoryEvent[];
  /** Lines of the same firing that contradicted it (another firing id, fire time …): not merged. */
  conflicts?: { recorded_at: string | null; fields: string[] }[] | null;
  // approvals
  action_id?: string;
  agent?: string;
  name?: string | null;
  tool?: string | null;
  automation_mode?: string | null;
  outcome?: string;
  decided_by?: string | null;
  /** FIN-16: the decision event recorded `dev_only: true` (the development token). */
  decided_by_dev_only?: boolean | null;
  decided_by_authenticated_by?: string | null;
  history?: HistoryEvent[];
  /** Every refused decision on this action, oldest first. */
  refusals?: DecisionRefusal[];
  /** The latest refusal when it IS the row's outcome (`outcome === "refused"`), else null. */
  refusal?: DecisionRefusal | null;
  // proposals
  adapter_agent?: string | null;
  connection?: string | null;
  drifts?: { kind?: string; unit?: string; subject_touching?: boolean | null }[] | null;
  /** How many problems the drift proposal recorded (their text is not served). */
  problem_count?: number | null;
  asked?: number | null;
  action?: string | null;
  axis?: string | null;
  occurrences?: number | null;
  document_count?: number | null;
  touches_subject?: boolean | null;
  forwarded?: Record<string, unknown> | null;
}

export interface ActivityPage {
  items: ActivityItem[];
  next_cursor: string | null;
  returned: number;
  /** null when a kind was not read to its end: a partial count is not served as a total. */
  total_visible: number | null;
  counts: Partial<Record<ActivityKind, number>>;
  counts_incomplete?: ActivityKind[];
  viewer: string;
  problems: { source: string; file: string | null; error: string }[];
  visibility: string;
}

export interface SubtaskNode {
  id: string | null;
  adapter_agent: string | null;
  connection: string | null;
  connection_basis: string;
  /** null: the step recorded no args (not an empty request). */
  request: Record<string, unknown> | null;
  decision: string | null;
  method: string | null;
  pushed_down: boolean | null;
  query: string | null;
  delegate_note: string | null;
  rows_returned: number | null;
  rows_basis: string;
  pointers_recorded: number | null;
  /** null: the record kept no skipped list, so what this sub-task skipped is not recorded. */
  skipped: { connection?: string; channel?: string; reason?: string; reason_code?: string }[] | null;
  /** `not_recorded`: no skipped list was recorded, so "answered in full" is not a claim it makes. */
  status: "answered" | "refused" | "failed" | "reduced" | "not_recorded";
  ms: number | null;
  produced_by: ProducedBy | null;
}

export interface DelegationTree {
  run_id: string;
  question: string | null;
  asked_by: string | null;
  status: string | null;
  started: string | null;
  ended: string | null;
  duration_ms: number | null;
  models_used: string[] | null;
  plan: {
    method: string | null;
    /** the questions the plan asked (E2E3 P-08); null when the record keeps none */
    questions: string[] | null;
    questions_text: string | null;
    questions_note: string | null;
    assumptions: string[] | null;
    assumptions_note: string | null;
    ms: number | null;
    produced_by: ProducedBy | null;
  } | null;
  subtasks: SubtaskNode[];
  combine: {
    spec: Record<string, unknown> | null;
    steps: Record<string, unknown>[] | null;
    failed: string | null;
    ms: number | null;
    pointers_recorded: number | null;
    produced_by: ProducedBy | null;
  } | null;
  narrate: { ms: number | null; method: string | null; produced_by: ProducedBy | null } | null;
  answer: {
    markdown: string | null;
    /** null: the record carried no list (not recorded), never an empty one standing in. */
    citations: unknown[] | null;
    unsupported_claims: unknown[] | null;
    produced_by: ProducedBy | null;
  };
  coverage: {
    searched: string[] | null;
    skipped: { tool?: string; connection?: string; reason?: string; reason_code?: string }[] | null;
    reasons: string[] | null;
    /** null when the record carried no coverage: "not reduced" is a claim it did not make. */
    reduced: boolean | null;
  };
  refused_subtasks: number;
  /** Paths inside the tree that the allow-list did not serve. */
  masked_fields?: string[];
}

export interface RunStep {
  tool: string;
  ms?: number;
  policy_decision?: { decision?: string; reason_code?: string; detail?: string } | string;
  produced_by?: ProducedBy | null;
  status?: string;
  reason?: string | null;
}

export interface ActivityRun {
  kind: RunKind;
  owner: string;
  item: ActivityItem;
  record: {
    run_id: string;
    status: string;
    steps?: RunStep[];
    coverage?: { searched?: string[]; skipped?: unknown[]; reasons?: unknown };
    answer?: { markdown?: string; produced_by?: ProducedBy | null } | null;
    /** Paths in the producer's record that the allow-list did not serve (Codex f1 #5). */
    masked_fields: string[];
    [k: string]: unknown;
  };
  action_history: HistoryEvent[];
  approvals: ActivityItem[];
  delegation_tree: DelegationTree | null;
}

export interface AutomationFiring extends ActivityItem {
  run: ActivityItem | null;
  run_note: string | null;
  approvals: ActivityItem[];
}

export interface AutomationSpec {
  spec_id: string;
  spec_kind: string | null;
  trigger: { trigger?: string; status?: string; next_fire?: string | null; kind?: string } | null;
  firings: AutomationFiring[];
  /** null when the firing log was not read whole: a partial count is not a total. */
  firing_count: number | null;
  trigger_types_seen: string[];
}

export interface AutomationsView {
  automations: AutomationSpec[];
  /** false: the firing log was not read whole (counts are null). */
  firings_complete?: boolean;
  engine: { available: boolean; note: string | null };
  viewer: string;
  problems: { source: string; file: string | null; error: string }[];
}

// ---------------------------------------------------------------------------------------------
// fetchers — a refusal is thrown with its code, so the page can say exactly what was refused
// ---------------------------------------------------------------------------------------------
export class ActivityError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, { cache: "no-store", signal, headers: authHeaders() });
  } catch (err) {
    if ((err as Error)?.name === "AbortError") throw err;
    throw new ActivityError(`the gateway at ${GATEWAY} could not be reached`, 0, null);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ActivityError(refusalText(res.status, body), res.status,
      (body as { refusal?: { code?: string } }).refusal?.code ?? null);
  }
  return body as T;
}

export function fetchActivity(
  opts: { limit?: number; before?: string | null; kinds?: ActivityKind[] },
  signal?: AbortSignal,
): Promise<ActivityPage> {
  const q = new URLSearchParams();
  q.set("limit", String(opts.limit ?? 50));
  if (opts.before) q.set("before", opts.before);
  if (opts.kinds && opts.kinds.length) q.set("kinds", opts.kinds.join(","));
  return getJson<ActivityPage>(`/activity?${q.toString()}`, signal);
}

/** One run. Pass the row's namespace (`run_kind` / `run_owner`) when it has one: without it the
 *  gateway answers 409 AMBIGUOUS_RUN if the id exists in more than one namespace you may see. */
export function fetchActivityRun(
  runId: string,
  signal?: AbortSignal,
  ns?: { kind?: RunKind | null; owner?: string | null },
): Promise<ActivityRun> {
  const q = new URLSearchParams();
  if (ns?.kind) q.set("kind", ns.kind);
  if (ns?.owner) q.set("owner", ns.owner);
  const qs = q.toString();
  return getJson<ActivityRun>(`/activity/runs/${encodeURIComponent(runId)}${qs ? `?${qs}` : ""}`, signal);
}

export function fetchAutomations(signal?: AbortSignal): Promise<AutomationsView> {
  return getJson<AutomationsView>("/activity/automations", signal);
}

/** One recorded request, by id — for "open the per-request trace" from an activity row. The
 *  recorder is in-memory and bounded, so a 404 means it is no longer held, and says so. */
export async function fetchTraceById(requestId: string, signal?: AbortSignal): Promise<TracePreview> {
  const detail = await getJson<GatewayTrace>(`/trace/${encodeURIComponent(requestId)}`, signal);
  return adaptGatewayTrace(detail, "GET /trace/{request_id}");
}

// ---------------------------------------------------------------------------------------------
// pure helpers (unit-tested)
// ---------------------------------------------------------------------------------------------
export const KIND_LABEL: Record<ActivityKind, string> = {
  agent_run: "agent run",
  federated_run: "delegated question",
  adapter_sync: "adapter-agent sync",
  firing: "automation firing",
  approval: "approval",
  drift_proposal: "drift proposal",
  ontology_proposal: "ontology proposal",
};

export const NOT_RECORDED = "not recorded";

export function refusalText(status: number, body: unknown): string {
  const r = (body as { refusal?: { code?: string; message?: string } } | null)?.refusal;
  if (status === 401) {
    return "the gateway refused this page's credential (every /activity route needs a signed-in principal; is NEXT_PUBLIC_WALNUT_TOKEN set for this build?)";
  }
  if (r?.code) return `${r.code}: ${r.message ?? ""}`.trim();
  return `the gateway answered ${status}`;
}

/** "agent · model", "rules", or "not recorded". Never invents a model for a rules step. */
export function producerText(pb: ProducedBy | null | undefined): string {
  if (!pb) return NOT_RECORDED;
  const who = pb.agent ?? pb.adapter_agent ?? null;
  const model = pb.model ?? null;
  if (model) return who ? `${who} · ${model}` : model;
  // `unknown` means nobody recorded who produced it — it is said as unknown, never as rules
  if (pb.by === "unknown" || pb.mode === "unknown") return who ? `${who} · unknown` : "unknown";
  if (pb.by === "rules" || pb.mode === "rules" || pb.agent === "rules") {
    return who && who !== "rules" ? `${who} · rules (no model)` : "rules (no model)";
  }
  return who ? `${who} · model ${NOT_RECORDED}` : NOT_RECORDED;
}

/** A duration, or the fact that nobody measured one. Never "0 ms" for a missing value. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return NOT_RECORDED;
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return s ? `${m} min ${s} s` : `${m} min`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return NOT_RECORDED;
  return `${n.toLocaleString()} tokens`;
}

/** A metered cost, or the fact that nothing measured one. Never "$0.00" for a missing value. */
export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined || !Number.isFinite(usd)) return NOT_RECORDED;
  return `$${usd < 0.01 ? usd.toPrecision(2) : usd.toFixed(2)}`;
}

/** "approve refused — principal_not_authenticated (claimed by x, not verified)". */
export function decisionRefusalText(r: DecisionRefusal): string {
  const who = r.by ? ` (asked by ${r.by})` : r.claimed_by ? ` (claimed by ${r.claimed_by}, not verified)` : "";
  return `${r.decision ?? "decision"} refused — ${r.reason_code ?? NOT_RECORDED}${who}`;
}

export function actorText(a: Actor | null | undefined): string {
  if (!a) return NOT_RECORDED;
  const id = a.id ?? NOT_RECORDED;
  switch (a.type) {
    case "person":
      return a.dev_only ? `person ${id} (${DEV_TOKEN_NOTE})` : `person ${id}`;
    case "adapter_agent":
      return `adapter agent ${id}`;
    case "automation":
      return `automation (${id})`;
    default:
      return `agent ${id}`;
  }
}

export type Tone = "good" | "warn" | "bad" | "neutral";

/** How loud a row is. The gateway's `loud` wins; a status that means "did not do what was
 *  asked" is at least a warning even if the flag were absent. */
export function itemTone(item: Pick<ActivityItem, "loud" | "status" | "kind">): Tone {
  const s = (item.status ?? "").toLowerCase();
  if (["failed", "refused", "execution_failed", "dead_lettered"].includes(s)) return "bad";
  if (item.loud) return "warn";
  if (["completed", "succeeded", "executed", "answered", "applied", "forwarded"].includes(s)) {
    return "good";
  }
  return "neutral";
}

export function subtaskTone(status: SubtaskNode["status"]): Tone {
  return status === "answered"
    ? "good"
    : status === "reduced" || status === "not_recorded"
      ? "warn"
      : "bad";
}

/** Merge a newer page into what is held, newest first, without duplicates. */
export function mergePages(held: ActivityItem[], more: ActivityItem[]): ActivityItem[] {
  const seen = new Set(held.map((i) => i.id));
  const out = [...held, ...more.filter((i) => !seen.has(i.id))];
  return out.sort((a, b) =>
    a.sort_key === b.sort_key ? (a.id < b.id ? 1 : a.id > b.id ? -1 : 0) : a.sort_key < b.sort_key ? 1 : -1,
  );
}

/** Which run an item opens in the inspector (runs, approvals, firings and drift carry one). */
export function runIdOf(item: ActivityItem): string | null {
  if (item.kind === "ontology_proposal") return null;
  return item.run_id ?? null;
}

/** The loud summary line of a delegation tree, or null when every sub-task answered in full. */
export function coverageWarning(tree: Pick<DelegationTree, "subtasks" | "coverage" | "combine">): string | null {
  const bad = tree.subtasks.filter((t) => t.status !== "answered");
  const parts: string[] = [];
  if (bad.length) {
    parts.push(
      `${bad.length} of ${tree.subtasks.length} sub-task(s) did not answer in full: ` +
        bad.map((t) => `${t.adapter_agent ?? "?"} ${t.status}`).join(", "),
    );
  }
  if (tree.combine?.failed) parts.push(`no table was computed: ${tree.combine.failed}`);
  if (!parts.length && tree.coverage.reduced) parts.push("coverage was reduced");
  return parts.length ? `Reduced coverage — ${parts.join("; ")}.` : null;
}

/** A policy decision is a dict on agent/federated steps and a sentence on adapter syncs. */
export function decisionText(pd: RunStep["policy_decision"]): string {
  if (!pd) return NOT_RECORDED;
  if (typeof pd === "string") return pd;
  return [pd.decision, pd.reason_code].filter(Boolean).join(" · ") || NOT_RECORDED;
}

/** The views of the Activity & trace page. `request` (the per-request authorisation path) stays
 *  the default for a bare `/trace`, so every existing link to it still lands where it did. */
export const ACTIVITY_VIEWS = ["request", "timeline", "automations"] as const;
export type ActivityView = (typeof ACTIVITY_VIEWS)[number];

export function viewFromParam(v: string | null | undefined): ActivityView {
  return (ACTIVITY_VIEWS as readonly string[]).includes(v ?? "") ? (v as ActivityView) : "request";
}
