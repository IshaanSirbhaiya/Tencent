/**
 * `/activity` — the gateway's read-only feed of what the agents did (`activity_routes.py`).
 *
 * Every row is a record some producer already wrote (agent runs, federated runs, adapter-agent
 * syncs and set-up steps, one-time tasks, automation firings, approval decisions, drift and
 * ontology proposals, and — R-028 — every human decision family: plan, mapping and link
 * approvals, set-up documents decisions and hand-offs, dataset design approvals and builds,
 * outbox decisions). Each names its actor with the common actor object. This module only
 * fetches and projects; it never fills a gap. A `null` from the gateway is "not recorded" on the
 * page — never 0, never a guess; a withheld or absent value is the `{status, reason}` envelope and
 * is shown as such. The pure helpers at the bottom are covered by `@unit` specs in
 * `e2e/activity.spec.ts`.
 */
import { GATEWAY, authHeaders } from "./api";
import { adaptGatewayTrace, type GatewayTrace } from "./trace-adapt";
import type { TracePreview } from "./platform-preview";
import { describeValueState, isValueState, type ValueState } from "./value-state";

// ---------------------------------------------------------------------------------------------
// contracts (mirroring activity_routes.py / activity_decisions.py / activity_actor.py)
// ---------------------------------------------------------------------------------------------
/** The run and proposal producers. */
export const RUN_FEED_KINDS = [
  "agent_run",
  "federated_run",
  "adapter_sync",
  "adapter_setup",
  "one_time_task",
  "firing",
  "approval",
  "drift_proposal",
  "ontology_proposal",
] as const;
/** R-028 CONS #8: every human-decision family is its own kind. */
export const DECISION_KINDS = [
  "plan_approval",
  "mapping_approval",
  "link_decision",
  "setup_documents",
  "setup_handoff",
  "design_approval",
  "design_build",
  "outbox_decision",
  // E2E run 5 F-12: the two decisions that WIDEN what the graph holds
  "screen_override",
  "identity_rule_decision",
  "decision_log",
] as const;
export const ACTIVITY_KINDS = [...RUN_FEED_KINDS, ...DECISION_KINDS] as const;
export type ActivityKind = (typeof ACTIVITY_KINDS)[number];

/**
 * R-028 CONS #12 — a value the feed does not serve (`withheld`) or that its producer did not
 * record (`absent`), with a closed reason code. It stands IN PLACE of the value: never a bare
 * null meaning "hidden", never a dropped key.
 */
export type ValueEnvelope = ValueState;

/** The shared, strict check (`lib/value-state.ts`): exactly the envelope's keys. */
export function isEnvelope(v: unknown): v is ValueEnvelope {
  return isValueState(v);
}

/** Plain words for the closed reason codes the gateway uses. */
export const ENVELOPE_REASON: Record<string, string> = {
  not_recorded_by_producer: "not recorded",
  machine_actor: "does not apply to a machine",
  not_in_allow_list: "outside this page's allow-list",
  sensitive_field: "sensitive; not shown here",
  free_text: "free text; not shown here",
  error_text: "error text; in the server log",
  too_deep: "nested too deeply to show",
  unexpected_shape: "not in the recorded shape",
  sql_with_literals: "SQL holding a value; not shown",
};

/** "withheld — free text; not shown here" / "not recorded"; a reason this page does not name is
 *  said by the shared describer (`describeValueState`), never dropped. */
export function envelopeText(v: ValueEnvelope): string {
  const why = ENVELOPE_REASON[v.reason];
  if (!why) return describeValueState(v);
  return v.status === "absent" ? why : `withheld — ${why}`;
}

/** A served value as text: itself, its envelope in words, or "not recorded" for null. */
export function valueText(v: unknown): string {
  if (v === null || v === undefined || v === "") return NOT_RECORDED;
  if (isEnvelope(v)) return envelopeText(v);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** The string a field holds, or null when it holds an envelope / nothing. */
export function served(v: string | ValueEnvelope | null | undefined): string | null {
  return typeof v === "string" ? v : null;
}

export type ActorField<T> = T | ValueEnvelope;

/**
 * R-028 CONS #7 — the common actor object, as every Activity row carries it. `principal_id` is
 * never decorated: a decision made with the development token says so in `dev_only`.
 */
export interface Actor {
  principal_id: ActorField<string>;
  authenticated_by: ActorField<string>;
  principal_type: ActorField<string>;
  dev_only: ActorField<boolean>;
  delegation?: Record<string, unknown> | null;
}

/** Principal types that are machines, never people (`activity_actor.MACHINE_TYPES`). */
export const MACHINE_TYPES = ["agent", "adapter_agent", "automation"] as const;
export type ActorType = (typeof MACHINE_TYPES)[number] | "person";

/** What an actor is: one of the machine types, or a person (any other recorded type). */
export function actorType(a: Actor | null | undefined): ActorType | null {
  if (!a) return null;
  const t = a.principal_type;
  if (typeof t === "string" && (MACHINE_TYPES as readonly string[]).includes(t)) {
    return t as ActorType;
  }
  return "person";
}

export function isPerson(a: Actor | null | undefined): boolean {
  return actorType(a) === "person";
}

/** The actor's principal id, or null when the producer did not record one. */
export function actorId(a: Actor | null | undefined): string | null {
  return a && typeof a.principal_id === "string" ? a.principal_id : null;
}

/** The plain words shown after a person who acted under the development token (FIN-16). */
export const DEV_TOKEN_NOTE = "dev token, not a real sign-in";

/** A decider's name, marked when the decision event records the development token. */
export function deciderText(by: string | null | undefined, devOnly: boolean | null | undefined): string {
  if (!by) return NOT_RECORDED;
  return devOnly ? `${by} (${DEV_TOKEN_NOTE})` : by;
}

/** How a person's decision was authenticated, in words: the dev token is said plainly; a sign-in
 *  the producer did not record is said as not recorded — never presented as a real sign-in. */
export function authText(a: Actor | null | undefined): string | null {
  if (!a || !isPerson(a)) return null;
  if (a.dev_only === true) return DEV_TOKEN_NOTE;
  if (isEnvelope(a.dev_only) || isEnvelope(a.authenticated_by)) {
    return "how they signed in was not recorded";
  }
  return null;
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
  /** A code, or the withheld envelope standing in for free text (R-028 #12). */
  reason?: string | ValueEnvelope;
  note?: string;
  outcome?: string;
  recorded_at?: string;
  attempt?: number;
  /** A firing's error text is never served: it is the withheld envelope (R-028 #12). */
  error?: string | ValueEnvelope;
  /** A firing's error: only the exception TYPE is served; its text is not (Codex f2 #9). */
  error_type?: string | null;
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
  /** A code or sentence the producer wrote, or the withheld envelope for free text. */
  reason?: string | ValueEnvelope | null;
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
  /** The latest refusal when it IS the row's outcome (`outcome === "refused"`), else null; on a
   *  `decision_log` row, the standard refusal (`LEDGER_INTEGRITY_FAILED`). */
  refusal?: DecisionRefusal | LedgerRefusal | null;
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
  // R-028 CONS #8 — decision rows (activity_decisions.py)
  /** approved | rejected | built | handed_off | sent … */
  decision?: string | null;
  /** What was verified about the record: sealed_and_anchored · sealed_and_chained ·
   *  append_only_store · unverified ·
   *  failed. Never more than what was checked. */
  integrity?: string | null;
  source_id?: string | null;
  table?: string | null;
  column?: string | null;
  target?: string | null;
  target_kind?: string | null;
  plan_digest?: string | null;
  mapping_digest?: string | null;
  design_id?: string | null;
  design_digest?: string | null;
  rev?: number | null;
  build_id?: string | null;
  draft_id?: string | null;
  playbook_id?: string | null;
  channel?: string | null;
  recipient_origin?: string | null;
  discovery_run_id?: string | null;
  documents_digest?: string | null;
  tables_bound?: number | null;
  deferred?: number | null;
  delegated?: boolean | null;
  task_id?: string | null;
  error_ref?: string | null;
  step?: string | null;
  decided_by_person?: boolean | null;
  /** screen_override (E2E run 5 F-12): the held item and the content digest the lift covers. */
  item_id?: string | null;
  digest?: string | null;
  /** identity_rule_decision (F-12): which declared rule, for which kind, in which pack. */
  pack?: string | null;
  node_kind?: string | null;
  rule?: string | null;
  /** decision_log: the family whose log did not verify, and the standard refusal. */
  family?: string | null;
  log?: string | null;
}

/** `integrity` in words: what was verified about a decision record — and only that. */
export const INTEGRITY_TEXT: Record<string, string> = {
  sealed_and_anchored: "sealed, chained and anchored — verified",
  sealed_and_chained: "sealed and chained — verified (not anchored: a removed tail is not detected)",
  append_only_store: "an append-only store (no seal)",
  unverified: "recorded, not verified (a plain record)",
  failed: "does not verify — nothing in this log is trusted",
};

/** The standard refusal envelope a `decision_log` row carries (`LEDGER_INTEGRITY_FAILED`). */
export interface LedgerRefusal {
  code: string;
  message: string;
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
  /** null: no SQL recorded; the withheld envelope: SQL holding a value (R-028 #12). */
  query: string | ValueEnvelope | null;
  /** The delegate's sentence is never served: the withheld envelope when one was recorded. */
  delegate_note: string | ValueEnvelope | null;
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
    questions_text: string | ValueEnvelope | null;
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
  adapter_setup: "set-up step",
  one_time_task: "one-time task",
  firing: "automation firing",
  approval: "approval",
  drift_proposal: "drift proposal",
  ontology_proposal: "ontology proposal",
  plan_approval: "plan approval",
  mapping_approval: "mapping approval",
  link_decision: "link decision",
  setup_documents: "documents decision",
  setup_handoff: "hand-off",
  design_approval: "design approval",
  design_build: "design build",
  outbox_decision: "outbox decision",
  screen_override: "screen hold lifted",
  identity_rule_decision: "identity rule decision",
  decision_log: "decision log integrity",
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
export function decisionRefusalText(r: DecisionRefusal | LedgerRefusal): string {
  if (isLedgerRefusal(r)) return `${r.code}: ${r.message}`;
  const who = r.by ? ` (asked by ${r.by})` : r.claimed_by ? ` (claimed by ${r.claimed_by}, not verified)` : "";
  return `${r.decision ?? "decision"} refused — ${r.reason_code ?? NOT_RECORDED}${who}`;
}

/** Who acted, from the structured actor object (R-028 #7). A person's dev-token decision is said
 *  plainly; an authentication the producer did not record is said as such; a delegated decision
 *  names its delegation. Never reads identity out of a decorated id. */
export function actorText(a: Actor | null | undefined): string {
  if (!a) return NOT_RECORDED;
  const id = actorId(a) ?? NOT_RECORDED;
  switch (actorType(a)) {
    case "adapter_agent":
      return `adapter agent ${id}`;
    case "automation":
      return `automation (${id})`;
    case "agent":
      return `agent ${id}`;
    default: {
      const how = authText(a);
      const via = a.delegation ? " (through a delegation)" : "";
      return `person ${id}${via}${how ? ` (${how})` : ""}`;
    }
  }
}

/** True for a `decision_log` row's refusal (the standard envelope), not an approval refusal. */
export function isLedgerRefusal(r: DecisionRefusal | LedgerRefusal | null | undefined): r is LedgerRefusal {
  return !!r && typeof (r as LedgerRefusal).code === "string" && !("reason_code" in r);
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

/** Kinds that never open a run: proposals filed outside a run, and every decision family. */
export const RUNLESS_KINDS: readonly ActivityKind[] = ["ontology_proposal", ...DECISION_KINDS];

/** Which run an item opens in the inspector (runs, approvals, firings and drift carry one). */
export function runIdOf(item: ActivityItem): string | null {
  if (RUNLESS_KINDS.includes(item.kind)) return null;
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
