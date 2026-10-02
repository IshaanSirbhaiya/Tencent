/**
 * `/builder` — the gateway client for agents, adapter agents, the approval inbox and the trigger
 * engine.
 *
 * Mirrors, field for field:
 *   apps/gateway/databridge_gateway/agents_routes.py          /agents/*
 *   apps/gateway/databridge_gateway/adapter_agents_routes.py  /adapter-agents/*
 *   apps/gateway/databridge_gateway/automation_routes.py      /automation/*, /platform/catalogue
 * When this file and those routes disagree, the routes are right and this file is the one to fix.
 *
 * Its own file (not an addition to `lib/api.ts`) because several workstreams edit this frontend
 * at once.
 *
 * Two rules this client keeps:
 *   - **Who acts is the signed-in principal.** Approve / reject / reverse send only `reason` /
 *     `note`; the gateway attributes the decision to the authenticated principal and ignores any
 *     `by`. This client never sends one.
 *   - **A refusal is shown, not swallowed.** Every `{refusal: {code, message}}` body is thrown as an
 *     `AgentsApiError` carrying the gateway's code and message verbatim.
 */
import { GATEWAY, authHeaders } from "./api";

// ============================================================================================
// errors + transport
// ============================================================================================
export class AgentsApiError extends Error {
  code: string;
  status: number;
  /** R-028: the former specific code when `code` is a standard one (as the other clients carry it). */
  reason_code?: string;
  /** R-028: the server-log reference a failure carries instead of its exception text. */
  error_ref?: string;
  constructor(status: number, code: string, message: string, extra?: { reason_code?: string; error_ref?: string }) {
    super(message);
    this.name = "AgentsApiError";
    this.status = status;
    this.code = code;
    if (extra?.reason_code) this.reason_code = extra.reason_code;
    if (extra?.error_ref) this.error_ref = extra.error_ref;
  }
}

/** The common refusal envelope (R-028): standard `code`, optional `reason_code` and `error_ref`. */
export interface RefusalEnvelope {
  code: string;
  message: string;
  reason_code?: string;
  error_ref?: string;
}

/** The ONE way a client turns `{refusal: {...}}` into an `AgentsApiError` (R-028 TIDY #6): the
 *  gateway's message verbatim, with the reference a failure carries so it can be quoted. */
export function agentsApiErrorFrom(status: number, r: RefusalEnvelope): AgentsApiError {
  const message = r.error_ref ? `${r.message} (reference ${r.error_ref})` : r.message;
  return new AgentsApiError(status, r.code, message, { reason_code: r.reason_code, error_ref: r.error_ref });
}

async function call<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, {
      ...init,
      signal,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch (e) {
    throw new AgentsApiError(0, "GATEWAY_UNREACHABLE", `${path}: ${(e as Error).message}`);
  }
  const body: unknown = await res.json().catch(() => ({}));
  if (body && typeof body === "object" && "refusal" in body) {
    throw agentsApiErrorFrom(res.status, (body as { refusal: RefusalEnvelope }).refusal);
  }
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? JSON.stringify((body as { detail: unknown }).detail)
        : `${res.status}`;
    throw new AgentsApiError(res.status, `HTTP_${res.status}`, `${path} → ${detail}`);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// ============================================================================================
// specs (databridge_workflow_spec.models, as model_dump(mode="json"))
// ============================================================================================
export type AutomationMode = "human_required" | "human_notified" | "autonomous";

export interface ScheduleTrigger {
  cron: string;
  timezone: string;
}
export interface EventTrigger {
  source: string;
  on: string[];
  filter: Record<string, string>;
  uri_glob?: string | null;
}
export interface Trigger {
  type: "manual" | "schedule" | "event";
  schedule: ScheduleTrigger | null;
  event: EventTrigger | null;
}

export interface ActionSpec {
  name: string;
  tool: string;
  automation_mode: AutomationMode;
  reversal: string | null;
  description: string;
}

export interface SpecDerivation {
  field: string;
  /** `unknown`: nobody recorded who — shown as unknown, never as rules. */
  produced_by: "rules" | "model" | "user" | "unknown";
  basis: string;
  model: string | null;
}

export interface WorkflowSpec {
  spec: "workflow/v1";
  agent: string;
  title: string;
  created_from: string;
  mode: "read_only" | "read_write";
  sources: string[];
  tools: string[];
  actions: ActionSpec[];
  trigger: Trigger;
  instructions: string;
  outputs: { format: string; save_to: string | null };
  limits: { max_steps: number; max_tool_calls: number; tokens_per_run: number };
  status: "draft" | "active" | "paused";
  derivations: SpecDerivation[];
}

export type DriftKind =
  | "removed"
  | "renamed"
  | "type_changed"
  | "tool_signature_changed"
  | "new_write_tool"
  | "subject_touching";

export interface AdaptPolicy {
  policy: "auto_additive" | "ask_all" | "off";
  watch: { on_sync: boolean; on_list_changed: boolean };
  always_ask: DriftKind[];
}

export interface AdapterAgentSpec {
  spec: "adapter_agent/v1";
  id: string;
  title: string;
  created_from: string;
  connections: string[];
  plans: Record<string, "proposed" | "approved">;
  sync: Trigger;
  extract: { pack: string; prose: boolean; tabular: boolean; passages: boolean };
  screen: Record<string, string>;
  freshness: { max_age_hours: number };
  /** Optional in the type because an older gateway may serve a spec from before the `adapt` block. */
  adapt?: AdaptPolicy;
  propose_ontology_additions: boolean;
  status: "draft" | "active" | "paused";
  derivations: SpecDerivation[];
}

// ============================================================================================
// the English box — POST /agents/compile, POST /agents
// ============================================================================================
export interface OpenQuestion {
  id: string;
  text: string;
  options: string[];
}

/** DraftResult.derivations — the compiler's fine-grained `by`: rule, model, answer, model+answer,
 *  config, catalogue, default. */
export interface Derivation {
  field: string;
  value: unknown;
  by: string;
  span: [number, number] | null;
  evidence: string;
  model: string | null;
  task_class?: string | null;
  prompt_version?: string | null;
}

export interface DraftResult {
  draft_id: string;
  kind: "agent" | "adapter_agent";
  created_from: string;
  understood: string;
  open_questions: OpenQuestion[];
  assumptions: string[];
  spec_id: string | null;
  spec: WorkflowSpec | AdapterAgentSpec | null;
  spec_yaml: string | null;
  declaration_yaml: string | null;
  declaration_source: string | null;
  compilation: { outcome: string; refusals: Record<string, unknown>[]; reason: string } | null;
  activatable: boolean;
  model_used: boolean;
  notes: string[];
  derivations: Derivation[];
}

export interface SaveResult {
  kind: "agent" | "adapter_agent";
  id: string;
  status: string;
  saved_by: string;
  files?: string[];
}

export function isAgentSpec(s: WorkflowSpec | AdapterAgentSpec | null): s is WorkflowSpec {
  return !!s && s.spec === "workflow/v1";
}
export function isAdapterAgentSpec(
  s: WorkflowSpec | AdapterAgentSpec | null,
): s is AdapterAgentSpec {
  return !!s && s.spec === "adapter_agent/v1";
}

// ============================================================================================
// agents — list, detail, runs, jobs
// ============================================================================================
export interface AgentRow {
  agent: string;
  title: string;
  mode: "read_only" | "read_write";
  status: "draft" | "active" | "paused";
  trigger: "manual" | "schedule" | "event";
  has_declaration: boolean;
}

export interface AgentDetail {
  spec: WorkflowSpec;
  declaration_yaml: string;
  runs: string[];
  next_fire: string | null;
}

export interface ProducedBy {
  agent?: string | null;
  agent_version?: string | null;
  task_class?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  run_id?: string | null;
  at?: string | null;
  /** `rules` · `model` · `user` · `unknown` where the producer records one. */
  by?: string | null;
}

export interface PolicyDecision {
  decision?: string;
  reason_code?: string;
  detail?: string;
  capability?: string | null;
  grant_id?: string | null;
}

/** A SourcePointer as JSON — every key optional here because the kinds carry different fields. */
export interface PointerJson {
  source_id: string;
  kind: string;
  capture_digest?: string | null;
  table?: string | null;
  column?: string | null;
  row_id?: string | null;
  document_id?: string | null;
  char_start?: number | null;
  char_end?: number | null;
  file_path?: string | null;
  message_id?: string | null;
  [key: string]: unknown;
}

export interface RunStep {
  tool: string;
  args: Record<string, unknown>;
  result_pointers: PointerJson[];
  policy_decision: PolicyDecision;
  ms: number;
  produced_by: ProducedBy | null;
}

export interface RunAction {
  name: string;
  automation_mode: AutomationMode;
  status: "proposed" | "approved" | "executed" | "rejected" | "reversed";
  by: string | null;
  pointers: PointerJson[];
}

export interface UnsupportedClaim {
  text: string;
  reason: string;
  citations: string[];
  field?: string;
}

export interface RunRecord {
  run_id: string;
  spec_digest: string;
  trigger: Record<string, unknown>;
  started: string;
  ended: string | null;
  status: "completed" | "stopped" | "failed" | "refused";
  models_used: string[];
  steps: RunStep[];
  actions: RunAction[];
  answer: {
    markdown: string;
    citations: PointerJson[];
    unsupported_claims: UnsupportedClaim[];
    produced_by: ProducedBy | null;
    /** The runtime's explicit ref → pointer map (additive, agent_runtime EF1). When present it is the
     *  only pairing used; see `structuredCitationMap` for the names it is read under. */
    citations_by_ref?: Record<string, PointerJson>;
    /** EF1 as landed in agent_runtime answer.py: the explicit `{ref, pointer}` list, in ref order.
     *  Preferred over every other pairing (`resolveCitations`). */
    citation_refs?: { ref: string; pointer: PointerJson }[];
    [key: string]: unknown;
  };
  coverage: { searched: string[]; skipped: Record<string, unknown>[]; reasons: string[] };
}

export interface RunSummaryRow {
  run_id: string;
  status: string;
  started?: string;
  ended?: string | null;
  trigger?: Record<string, unknown>;
  actions?: RunAction[];
  error?: string;
}

export interface Job {
  job_id: string;
  kind: string;
  key: string;
  status: "running" | "completed" | "failed" | string;
  by: string | null;
  started: string;
  ended: string | null;
  progress: unknown;
  events: unknown[];
  error: string | null;
  result: Record<string, unknown> | null;
  run_id?: string;
  graph_counts?: unknown;
}

// ============================================================================================
// approvals
// ============================================================================================
export interface PendingAction {
  run_id: string;
  action_id: string;
  agent: string | null;
  name: string;
  automation_mode: AutomationMode;
  status: "proposed" | "approved";
  tool: string | null;
  args: Record<string, unknown> | null;
  derived_from: PointerJson[];
  reversal: string | null;
  proposed_at: string | null;
  ungrounded_values: string[];
  history: Record<string, unknown>[];
}

export interface DecisionResult {
  run_id: string;
  action_id: string;
  decided_by: string;
  record: RunRecord;
}

// ============================================================================================
// adapter agents
// ============================================================================================
export interface ConnectionHealth {
  connection: string;
  runnable: boolean;
  skip_reason: string | null;
  plan: { spec: string | null; ingest: string | null };
  last_run: { at: string | null; status: string; ingest_run_id: string | null; reason: string | null } | null;
  last_success_at: string | null;
  age_hours: number | null;
  stale: boolean;
  future_timestamp: boolean;
  consecutive_failures: number;
  counts: Record<string, number | null>;
  exclusions_by_rule: Record<string, Record<string, number>>;
  incomplete_runs: string[];
  issues: string[];
}

export interface AdapterHealth {
  agent_id: string;
  title: string;
  status: string;
  deleted: boolean;
  overall: string;
  generated_at: string;
  freshness: { max_age_hours: number };
  sync: Trigger;
  last_sync: { run_id: string; status: string; started: string; ended: string } | null;
  connections: ConnectionHealth[];
  totals: Record<string, number | null>;
  issues: string[];
  /** Not served by `health()`; held adaptations come from `agentsApi.heldAdaptations` (INT4). */
  pending_adaptations?: Record<string, unknown>[];
}

export interface AdapterAgentRow {
  spec: AdapterAgentSpec;
  deleted: boolean;
  overall: string;
  last_sync?: AdapterHealth["last_sync"];
  issues: string[];
}

export interface AdapterAgentDetail {
  spec: AdapterAgentSpec;
  deleted: boolean;
  state: unknown;
  history: Record<string, unknown>[];
  health: AdapterHealth | { overall: string; issues: string[] };
  next_fire: string | null;
}

export interface AdapterRunRecord {
  run_id: string;
  status: string;
  started: string;
  ended: string;
  reason: string | null;
  totals?: Record<string, number | null>;
  warnings?: string[];
  [key: string]: unknown;
}

export interface AdapterProposal {
  proposal_id: string;
  filed_at?: string;
  status?: string;
  pack?: string;
  axis?: string;
  name?: string;
  proposed_by?: string;
  adapter_agent?: string;
  binds_to?: string;
  touches_subject?: boolean;
  subject_reason?: string | null;
  occurrences?: number;
  document_count?: number;
  examples?: unknown;
  evidence?: unknown[];
  raw_names?: string[];
  forwarded?: {
    at: string;
    by: string;
    ontology_proposal_id: string | null;
    ontology_status: string | null;
  };
}

/** A held drift adaptation, as `GET /adapter-agents/{id}/adaptations` serves it (INT4). Every field is
 *  read from the adaptation engine's record; nothing is restated by the gateway. */
export interface HeldAdaptation {
  adaptation_id: string;
  status: string;
  connection: string | null;
  unit: string | null;
  /** drift kinds, plus `subject_touching` when a drift touches a subject */
  kinds: string[];
  /** what changed, one line per drift */
  changed: string[];
  /** why it was held for a person (the policy's reason per asked drift) */
  held_because: string[];
  problems: string[];
  hold: { level: string | null; why: string | null } | null;
  action: string | null;
  proposal_artifact: string | null;
  created_at: string | null;
}

export interface HeldAdaptationsList {
  agent_id: string;
  status: string;
  /** the `by` of the agent's create history entry; null when none is recorded */
  owner: string | null;
  /** whether the signed-in principal may approve / reject (the gateway decides; this only informs) */
  may_decide: boolean;
  adaptations: HeldAdaptation[];
}

export interface AdaptationDecisionResult {
  adaptation: HeldAdaptation & {
    decided_by: { principal_id: string; authenticated_by: string } | null;
    decided_at: string | null;
    note: string | null;
    version: unknown;
  };
  by: string;
}

export interface AutomationStatus {
  running: boolean;
  enabled: boolean;
  enable_with: string;
  specs: { id: string; kind: string; status: string; trigger: string; next_fire: string | null }[];
  recent_firings: Record<string, unknown>[];
  platform_problems: string[];
}

// ============================================================================================
// pure decision helpers — what the UI may offer and claim, derived from the record (R-019)
// ============================================================================================

/** Job statuses the gateway's job table ends in. Anything else — `running`, or a state this client
 *  does not know yet — is still in flight: keep polling, never run completion effects. */
export const JOB_TERMINAL_STATUSES = ["completed", "failed", "refused", "stopped", "succeeded", "cancelled"] as const;
export function isTerminalJobStatus(status: string | null | undefined): boolean {
  return !!status && (JOB_TERMINAL_STATUSES as readonly string[]).includes(status);
}

/** The drift kinds the spec schema requires in every `adapt.always_ask`
 *  (`databridge_workflow_spec.models.ALWAYS_ASKED`). The UI states them as asked only when the
 *  received policy actually lists them. */
export const ALWAYS_ASKED_DRIFT: DriftKind[] = ["new_write_tool", "subject_touching"];
export function missingAlwaysAsked(alwaysAsk: readonly string[]): DriftKind[] {
  return ALWAYS_ASKED_DRIFT.filter((k) => !alwaysAsk.includes(k));
}

/** Why an adapter spec's drift policy cannot be trusted. A MISSING `adapt` block is as invalid as
 *  one missing a schema-required `always_ask` entry: the page cannot establish that write-tool and
 *  subject-touching changes are put to a person (R-019 round 3, B4). Empty = valid. */
export function adaptProblems(spec: Pick<AdapterAgentSpec, "adapt">): string[] {
  const a = spec.adapt;
  if (!a || !Array.isArray(a.always_ask)) {
    return ["the spec carries no adapt block, so this page cannot establish which source changes are put to a person — this spec is invalid"];
  }
  const missing = missingAlwaysAsked(a.always_ask);
  return missing.length
    ? [`the spec's adapt.always_ask is missing ${missing.join(", ")} — the schema requires them; this spec is invalid`]
    : [];
}

/** The request body for approving / rejecting a held adaptation. It NEVER carries `by`: the gateway
 *  takes the decider from the request's authentication (hard constraint 4). A blank reason is
 *  omitted rather than sent as "". */
export function adaptationDecisionBody(reason: string | null | undefined): { reason?: string } {
  const r = (reason ?? "").trim();
  return r ? { reason: r } : {};
}

/** Whether the Approve / Reject buttons are offered for a held adaptation, and why not. The gateway
 *  enforces every one of these itself; this only stops the page offering a click it will refuse. */
export function adaptationDecisionState(s: {
  status: string;
  mayDecide: boolean;
  owner: string | null;
  reason: string;
  busy: boolean;
}): { canApprove: boolean; canReject: boolean; blockers: string[] } {
  const blockers: string[] = [];
  if (!s.mayDecide) {
    blockers.push(
      s.owner
        ? `Only this adapter agent's owner (${s.owner}) can decide its held changes.`
        : "No owner is recorded for this adapter agent, so nobody can decide its held changes from the app.",
    );
  }
  if (s.status !== "pending") blockers.push(`This change is already ${s.status || "decided"}.`);
  const open = blockers.length === 0 && !s.busy;
  const hasReason = s.reason.trim().length > 0;
  if (open && !hasReason) blockers.push("A rejection needs a reason; it is recorded.");
  return { canApprove: open, canReject: open && hasReason, blockers };
}

/** Which lifecycle controls an adapter agent may show. A draft is activated ONLY through an
 *  activatable compiled draft (the draft card), never by "Resume"; only an active agent syncs.
 *  Resume and Sync also need the same gate activation needs — every named connection's crawl plan
 *  human-approved and a valid adapt policy — because a plan can become unapproved after activation
 *  (R-019 round 3, B2). Pause is always offered to a live active agent: stopping never needs a gate.
 *  The gateway enforces its own gate on resume/sync (adapter_agents, deferred to FX4). */
export function adapterControls(
  spec: Pick<AdapterAgentSpec, "status" | "connections" | "plans" | "adapt">,
  deleted: boolean,
): { canSync: boolean; canPause: boolean; canResume: boolean; blockers: string[] } {
  const live = !deleted;
  const blockers: string[] = [];
  const connections = Array.isArray(spec.connections) ? spec.connections : [];
  if (connections.length === 0) blockers.push("the spec names no connection");
  for (const c of connections) {
    if (spec.plans?.[c] !== "approved") {
      blockers.push(`the crawl plan for ${c} is ${spec.plans?.[c] ?? "not proposed"} — approve it on Sources, then Refresh plan status`);
    }
  }
  blockers.push(...adaptProblems(spec));
  const gated = blockers.length === 0;
  return {
    canSync: live && spec.status === "active" && gated,
    canPause: live && spec.status === "active",
    canResume: live && spec.status === "paused" && gated,
    blockers,
  };
}

/** Why a pending action must NOT be offered for approval. A value no cited evidence contains
 *  (`ungrounded_values`), or argument values with no evidence pointer at all, would be committed
 *  without a SourcePointer (CLAUDE.md hard constraint 2) — a warning plus a click does not fix that.
 *  Missing fields fail closed. Reject stays available. Empty = approvable (R-019 round 3, B1). The
 *  gateway's own refusal is agent_runtime's (deferred to EF1). */
export function approvalBlockers(a: PendingAction): string[] {
  const out: string[] = [];
  if (!Array.isArray(a.ungrounded_values)) {
    out.push("the record does not say which argument values are grounded, so none can be trusted");
  } else if (a.ungrounded_values.length > 0) {
    out.push(`not found in any cited evidence: ${a.ungrounded_values.join(", ")}`);
  }
  if (a.args === null || a.args === undefined || typeof a.args !== "object") {
    out.push("the proposed arguments were not recorded, so there is nothing to check before approving");
  } else {
    const hasValues = Object.values(a.args).some((v) => v !== null && v !== undefined && v !== "");
    const pointers = Array.isArray(a.derived_from) ? a.derived_from.length : 0;
    if (hasValues && pointers === 0) out.push("the arguments carry values but the action cites no evidence pointer");
  }
  return out;
}

/** What a compiled draft's Save / Activate may do right now. A draft compiled from words the user
 *  has since edited is stale, and nothing may be saved while a compile or a save is in flight — so a
 *  previous `draft_id` can never be saved for new words (R-019 round 3, B5). */
export function draftActionState(s: {
  compiledText: string;
  text: string;
  compiling: boolean;
  saving: boolean;
  hasSpec: boolean;
  activatable: boolean;
}): { canSave: boolean; canActivate: boolean; stale: boolean } {
  const stale = s.text !== s.compiledText;
  const idle = !s.compiling && !s.saving && !stale;
  return { canSave: idle && s.hasSpec, canActivate: idle && s.hasSpec && s.activatable, stale };
}

/** A background job's hold on its Start button. Once a job id is assigned it is outstanding until a
 *  terminal record is CONFIRMED — the gap before the first fetch and a failed poll both keep it
 *  outstanding (`unverified` when polling failed: only an explicit person's choice abandons it). */
export function jobGate(
  jobId: string | null,
  job: { status: string } | undefined,
  pollFailed: boolean,
): { outstanding: boolean; canStart: boolean; unverified: boolean } {
  if (!jobId) return { outstanding: false, canStart: true, unverified: false };
  const done = !!job && isTerminalJobStatus(job.status);
  return { outstanding: !done, canStart: done, unverified: !done && pollFailed };
}

/** Words for a job status. Only the documented `running` state is described as work in progress;
 *  any other non-terminal status is shown verbatim and neutrally. */
export function jobStatusWords(status: string | null | undefined): string {
  if (isTerminalJobStatus(status)) return "";
  if (status === "running") return "the agent is working; this updates live";
  return `status "${status ?? "none"}" — not a state this page knows; waiting for a terminal update`;
}

const REF_RE = /^p(\d+)$/;

/** The runtime's structured ref → pointer map, when the record carries one. The additive field
 *  agent_runtime is adding (EF1) is read under each name it may take; the first well-formed one
 *  wins. A map with any non-`pN` key or non-pointer value is NOT used. */
export const CITATION_MAP_KEYS = ["citations_by_ref", "citation_map", "cited_refs", "ref_pointers"] as const;
export function structuredCitationMap(answer: Record<string, unknown>): Record<string, PointerJson> | null {
  for (const k of CITATION_MAP_KEYS) {
    const m = answer[k];
    if (!m || typeof m !== "object" || Array.isArray(m)) continue;
    const entries = Object.entries(m as Record<string, unknown>);
    const wellFormed = entries.every(
      ([ref, p]) => REF_RE.test(ref) && !!p && typeof p === "object" && typeof (p as PointerJson).source_id === "string",
    );
    if (wellFormed) return Object.fromEntries(entries) as Record<string, PointerJson>;
  }
  return null;
}

/** ref → pointer for an answer. A structured map from the runtime is used whenever present
 *  (`basis: "structured"`). Otherwise the pairing falls back to the answer's text, which is labelled
 *  unverified (`basis: "text"`) and accepted only under the strict terminal-block rule of
 *  `pairCitations`; anything else pairs nothing (`basis: "none"`). */
export function resolveCitations(answer: { markdown: string; citations: PointerJson[] } & Record<string, unknown>): {
  byRef: Record<string, PointerJson>;
  basis: "structured" | "text" | "none";
} {
  // EF1 as landed: `citation_refs: [{ref, pointer}]`. A non-empty list is authoritative; a malformed
  // one (non-pN ref, pointer without source_id, duplicate ref) pairs NOTHING rather than falling back
  // to text. An empty list is what a record written before EF1 defaults to, so it counts as absent.
  const list = answer.citation_refs;
  if (Array.isArray(list) && list.length > 0) {
    const byRef: Record<string, PointerJson> = {};
    for (const e of list as unknown[]) {
      const ref = (e as { ref?: unknown })?.ref;
      const p = (e as { pointer?: unknown })?.pointer as PointerJson | undefined;
      const ok = typeof ref === "string" && REF_RE.test(ref) && !(ref in byRef) && !!p && typeof p === "object" && typeof p.source_id === "string";
      if (!ok) return { byRef: {}, basis: "none" };
      byRef[ref as string] = p as PointerJson;
    }
    return { byRef, basis: "structured" };
  }
  const map = structuredCitationMap(answer);
  if (map) return { byRef: map, basis: "structured" };
  const { byRef, aligned } = pairCitations(answer.markdown, answer.citations ?? []);
  return aligned ? { byRef, basis: "text" } : { byRef: {}, basis: "none" };
}

/** ref → pointer, read from the answer's own **Sources:** block. Accepted ONLY when there is exactly
 *  one such block and it is terminal — the runtime writes it last, followed by nothing but blank
 *  lines and the single italic audit line — so a model-authored block earlier in the answer can
 *  never be the one read (R-019 round 3, B3). The runtime lists the cited refs unique and in
 *  ascending order, one per recorded citation; any list that is not exactly that (a duplicate, an
 *  out-of-order or foreign ref, a count mismatch) pairs NOTHING. */
export function pairCitations(markdown: string, citations: PointerJson[]): {
  byRef: Record<string, PointerJson>;
  aligned: boolean;
} {
  const lines = markdown.split("\n");
  const starts = lines.flatMap((l, i) => (l.trim() === "**Sources:**" ? [i] : []));
  const refs: string[] = [];
  let terminal = false;
  if (starts.length === 1) {
    let i = starts[0] + 1;
    for (; i < lines.length; i += 1) {
      const m = /^- \[(p\d+)\]/.exec(lines[i].trim());
      if (!m) break;
      refs.push(m[1]);
    }
    const rest = lines.slice(i).filter((l) => l.trim() !== "");
    terminal = rest.length === 0 || (rest.length === 1 && /^_[^_].*_$/.test(rest[0].trim()));
  }
  let aligned = terminal && refs.length === citations.length && refs.length > 0;
  for (let i = 0; aligned && i < refs.length; i += 1) {
    const n = Number(REF_RE.exec(refs[i])?.[1]);
    const prev = i > 0 ? Number(REF_RE.exec(refs[i - 1])?.[1]) : 0;
    if (!Number.isInteger(n) || n < 1 || n <= prev) aligned = false;
  }
  const byRef: Record<string, PointerJson> = {};
  if (aligned) refs.forEach((r, i) => (byRef[r] = citations[i]));
  return { byRef, aligned };
}

/** A rendered link is a citation chip only when it is `#cite-pN` AND its visible text is exactly
 *  `pN` — so answer text cannot relabel a pointer (`[supported](#cite-p1)`). */
export function citationRefFromLink(href: string | undefined, text: string): string | null {
  if (!href || !href.startsWith("#cite-")) return null;
  const ref = href.slice(6);
  return REF_RE.test(ref) && text === ref ? ref : null;
}

/** How many answer lines carry the platform's own CLOSED flag prefix (`- **UNSUPPORTED**` …); an
 *  unclosed `- **UNSUPPORTED` does not render as a marker and is not counted. */
export function platformFlagCount(markdown: string, flag: "UNSUPPORTED" | "CANDIDATE"): number {
  const re = flag === "UNSUPPORTED" ? /^- \*\*UNSUPPORTED\*\* / : /^- \*\*CANDIDATE[^*]*\*\* /;
  return markdown.split("\n").filter((l) => re.test(l.trim())).length;
}

/** The runtime's `_one_line`: newlines (and U+2028/U+2029) folded to " / ". */
function oneLine(text: string): string {
  return String(text ?? "")
    .split(/[\r\n\u2028\u2029]+/)
    .map((p) => p.trim())
    .filter(Boolean)
    .join(" / ");
}

/** Which answer lines are UNSUPPORTED markers that correspond, one to one, to the structured
 *  `unsupported_claims` — matched by reason AND claim text, never by count (R-019 round 3, B7).
 *  `lines` are 1-based markdown line numbers, and are returned only when EVERY structured entry and
 *  EVERY marker line pair up (`verified`); otherwise nothing is styled as a verified marker. A
 *  clarification entry's text is not what the runtime renders, so it is matched on its reason and
 *  the `Assumed:` shape. */
export function verifiedFlagLines(
  markdown: string,
  unsupported: UnsupportedClaim[],
): { verified: boolean; lines: number[] } {
  const markers: { line: number; body: string }[] = [];
  markdown.split("\n").forEach((l, i) => {
    const t = l.trim();
    if (t.startsWith("- **UNSUPPORTED**")) markers.push({ line: i + 1, body: t });
  });
  const used = new Set<number>();
  const lines: number[] = [];
  for (const c of unsupported ?? []) {
    const head = `- **UNSUPPORTED** (${c.reason}): `;
    const want = c.field === "clarification" ? `${head}Assumed: ` : `${head}${oneLine(c.text)}`;
    const hit = markers.find(
      (m) =>
        !used.has(m.line) &&
        (m.body === want || m.body.startsWith(c.field === "clarification" ? want : `${want} [`)),
    );
    if (!hit) return { verified: false, lines: [] };
    used.add(hit.line);
    lines.push(hit.line);
  }
  if (used.size !== markers.length) return { verified: false, lines: [] };
  return { verified: true, lines: lines.sort((a, b) => a - b) };
}

/** `produced_by` in words. `null` means the record did not capture it — unknown, not "no model". */
export function producerText(p: ProducedBy | null | undefined): string {
  if (!p) return "producer not recorded";
  if (p.by === "unknown" && !p.model) {
    return [p.agent, "unknown"].filter(Boolean).join(" · ");
  }
  const who = [p.agent, p.agent_version ? `v${p.agent_version}` : null].filter(Boolean).join(" ");
  return [who || "agent not recorded", p.model ? `model ${p.model}` : "model not recorded", p.task_class]
    .filter(Boolean)
    .join(" · ");
}

/** A connection's freshness, fail-closed: "fresh" only for a valid successful timestamp inside the
 *  limit. Every gateway reason that qualifies it is returned for display. */
export function freshnessOf(c: ConnectionHealth): {
  label: "fresh" | "stale" | "unknown" | "invalid";
  reasons: string[];
  /** The age in words, from the validated values only — never "undefinedh old". */
  ageText: string;
} {
  const reasons: string[] = [];
  if (c.future_timestamp) reasons.push("the last success is timestamped in the future — the clock or record is wrong");
  if (c.incomplete_runs?.length) reasons.push(`incomplete run(s): ${c.incomplete_runs.join(", ")}`);
  if (c.skip_reason) reasons.push(`skipped: ${c.skip_reason}`);
  if (c.last_run?.reason) reasons.push(`last run: ${c.last_run.reason}`);
  const hasTs = typeof c.last_success_at === "string" && c.last_success_at.length > 0;
  const tsValid = hasTs && Number.isFinite(Date.parse(c.last_success_at as string));
  const hasAge = c.age_hours !== null && c.age_hours !== undefined;
  const ageValid = hasAge && typeof c.age_hours === "number" && Number.isFinite(c.age_hours) && c.age_hours >= 0;
  if (hasTs && !tsValid) reasons.push(`the last-success timestamp "${c.last_success_at}" is not a valid time`);
  if (hasAge && !ageValid) reasons.push(`the recorded age "${String(c.age_hours)}" is not a valid age`);
  if (!hasTs && hasAge) reasons.push("an age is recorded without a last-success time");
  let label: "fresh" | "stale" | "unknown" | "invalid";
  if (c.future_timestamp || (hasTs && !tsValid) || (hasAge && !ageValid) || (!hasTs && hasAge)) label = "invalid";
  else if (!hasTs || !hasAge) label = "unknown";
  else if (c.stale) label = "stale";
  else label = "fresh";
  const ageText =
    label === "invalid" ? "age invalid" : !hasTs ? "no successful sync" : !hasAge ? "age not recorded" : `${c.age_hours}h old`;
  return { label, reasons, ageText };
}

// ============================================================================================
// the client
// ============================================================================================
export const agentsApi = {
  // the English box (both kinds)
  compile: (text: string, answers: Record<string, string>) =>
    post<DraftResult>("/agents/compile", { text, answers }),
  save: (draft_id: string, activate: boolean) => post<SaveResult>("/agents", { draft_id, activate }),

  // agents
  list: (signal?: AbortSignal) =>
    call<{ agents: AgentRow[]; problems: string[] }>("/agents", undefined, signal),
  get: (id: string, signal?: AbortSignal) =>
    call<AgentDetail>(`/agents/${encodeURIComponent(id)}`, undefined, signal),
  run: (id: string, question: string | null) =>
    post<{ job_id: string; status: string; started_by: string }>(
      `/agents/${encodeURIComponent(id)}/run`,
      { question: question || null, wait: false },
    ),
  job: (id: string, jobId: string, signal?: AbortSignal) =>
    call<Job>(`/agents/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobId)}`, undefined, signal),
  runs: (id: string, signal?: AbortSignal) =>
    call<{ agent: string; runs: RunSummaryRow[] }>(
      `/agents/${encodeURIComponent(id)}/runs`,
      undefined,
      signal,
    ),
  runRecord: (id: string, runId: string, signal?: AbortSignal) =>
    call<{ agent: string; record: RunRecord; action_history: Record<string, unknown>[] }>(
      `/agents/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}`,
      undefined,
      signal,
    ),

  // approvals — never a `by`: the gateway attributes the decision to the signed-in principal
  approvals: (signal?: AbortSignal) =>
    call<{ pending: PendingAction[] }>("/agents/approvals", undefined, signal),
  approve: (runId: string, actionId: string) =>
    post<DecisionResult>(
      `/agents/approvals/${encodeURIComponent(runId)}/${encodeURIComponent(actionId)}/approve`,
      {},
    ),
  reject: (runId: string, actionId: string, reason: string) =>
    post<DecisionResult>(
      `/agents/approvals/${encodeURIComponent(runId)}/${encodeURIComponent(actionId)}/reject`,
      { reason },
    ),
  reverse: (runId: string, actionId: string, note: string) =>
    post<DecisionResult>(
      `/agents/approvals/${encodeURIComponent(runId)}/${encodeURIComponent(actionId)}/reverse`,
      { note },
    ),

  // adapter agents
  adapterAgents: (signal?: AbortSignal) =>
    call<{ adapter_agents: AdapterAgentRow[]; problems: Record<string, string> }>(
      "/adapter-agents",
      undefined,
      signal,
    ),
  adapterAgent: (id: string, signal?: AbortSignal) =>
    call<AdapterAgentDetail>(`/adapter-agents/${encodeURIComponent(id)}`, undefined, signal),
  adapterHealth: (id: string, signal?: AbortSignal) =>
    call<AdapterHealth>(`/adapter-agents/${encodeURIComponent(id)}/health`, undefined, signal),
  adapterRuns: (id: string, signal?: AbortSignal) =>
    call<{ agent_id: string; runs: AdapterRunRecord[]; in_flight: Job[] }>(
      `/adapter-agents/${encodeURIComponent(id)}/runs`,
      undefined,
      signal,
    ),
  adapterPause: (id: string) => post<{ adapter_agent: AdapterAgentSpec }>(`/adapter-agents/${encodeURIComponent(id)}/pause`),
  adapterResume: (id: string) => post<{ adapter_agent: AdapterAgentSpec }>(`/adapter-agents/${encodeURIComponent(id)}/resume`),
  adapterSync: (id: string) =>
    post<{ job_id: string; status: string; started_by: string }>(
      `/adapter-agents/${encodeURIComponent(id)}/sync`,
    ),
  adapterJob: (id: string, jobId: string, signal?: AbortSignal) =>
    call<Job>(
      `/adapter-agents/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobId)}`,
      undefined,
      signal,
    ),
  adapterRefreshPlans: (id: string) =>
    post<{ adapter_agent: AdapterAgentSpec }>(`/adapter-agents/${encodeURIComponent(id)}/refresh-plans`),
  // held drift adaptations — never a `by`: the gateway decides as the signed-in principal
  heldAdaptations: (id: string, status: string = "pending", signal?: AbortSignal) =>
    call<HeldAdaptationsList>(
      `/adapter-agents/${encodeURIComponent(id)}/adaptations?status=${encodeURIComponent(status)}`,
      undefined,
      signal,
    ),
  approveAdaptation: (id: string, adaptationId: string, reason: string) =>
    post<AdaptationDecisionResult>(
      `/adapter-agents/${encodeURIComponent(id)}/adaptations/${encodeURIComponent(adaptationId)}/approve`,
      adaptationDecisionBody(reason),
    ),
  rejectAdaptation: (id: string, adaptationId: string, reason: string) =>
    post<AdaptationDecisionResult>(
      `/adapter-agents/${encodeURIComponent(id)}/adaptations/${encodeURIComponent(adaptationId)}/reject`,
      adaptationDecisionBody(reason),
    ),
  proposals: (signal?: AbortSignal) =>
    call<{ proposals: AdapterProposal[] }>("/adapter-agents/proposals", undefined, signal),
  forwardProposal: (pid: string) =>
    post<{ proposal_id: string; forwarded_by: string; ontology_proposal: Record<string, unknown> }>(
      `/adapter-agents/proposals/${encodeURIComponent(pid)}/forward`,
    ),

  // the trigger engine
  automationStatus: (signal?: AbortSignal) =>
    call<AutomationStatus>("/automation/status", undefined, signal),
};
