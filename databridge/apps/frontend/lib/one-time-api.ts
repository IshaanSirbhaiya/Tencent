/**
 * `/intelligence` one-time tasks (FEAT8) — the gateway client and the pure decisions the panel
 * renders from.
 *
 * Mirrors, field for field, apps/gateway/databridge_gateway/one_time_routes.py:
 *   POST /intelligence/one-time/compile                      English -> plan (or questions)
 *   POST /intelligence/one-time/run          {draft_id}      run the previewed plan ONCE
 *   GET  /intelligence/one-time/{task_id}
 *   POST /intelligence/one-time/{task_id}/actions/{action_id}/approve | /reject
 * When this file and those routes disagree, the routes are right.
 *
 * Rules this client keeps:
 *   - **Never "done" while a write is held.** `taskHeadline` reads the gateway's `state`, and a
 *     task with any held write says it is waiting, whatever else the record says.
 *   - **Who decides is the signed-in principal.** Approve / reject send only a `reason`.
 *   - **A refusal is shown, not swallowed** (`AgentsApiError` carries the gateway's code).
 */
import { GATEWAY, authHeaders } from "./api";
import {
  AgentsApiError,
  agentsApiErrorFrom,
  type OpenQuestion,
  type PointerJson,
  type RefusalEnvelope,
} from "./agents-api";
import { isValueState, type ValueState } from "./value-state";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, {
      ...init,
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
  if (!res.ok) throw new AgentsApiError(res.status, `HTTP_${res.status}`, `${path} → ${res.status}`);
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// ============================================================================================
// shapes
// ============================================================================================
export interface PlanRead {
  tool: string;
  family: string | null;
  connection: string | null;
}

export interface PlanWrite {
  name: string;
  tool: string;
  connection: string | null;
  automation_mode: string | null;
  description: string;
  reversal: string | null;
}

export interface PlanGrants {
  purpose: string;
  capabilities: { source_id: string; capability: string; max_trust: string }[];
  golden_set: string;
  golden_set_records: number;
  citation: string;
  tokens_per_run: number;
}

export interface OneTimePlan {
  task_id: string;
  title: string;
  mode: "read_only" | "read_write";
  trigger: string;
  sources_read: string[];
  reads: PlanRead[];
  writes: PlanWrite[];
  instructions: string;
  grants: PlanGrants | null;
}

export interface OneTimeDraft {
  draft_id: string;
  runnable: boolean;
  created_from: string;
  understood: string;
  open_questions: OpenQuestion[];
  assumptions: string[];
  notes: string[];
  compilation: { outcome: string; refusals: { code?: string; detail?: string }[]; reason: string } | null;
  model_used: boolean;
  plan: OneTimePlan | null;
}

export interface OneTimeAction {
  action_id: string;
  name: string;
  status: "proposed" | "approved" | "executed" | "rejected" | "reversed" | string;
  by: string | null;
  automation_mode: string;
  tool: string | null;
  /** The write's arguments, or the standard withheld envelope (R-028 #12) when no valid source
   *  pointers stand behind them — on either the run-record or the action-log path. */
  args: Record<string, unknown> | ValueState;
  reversal: string | null;
  derived_from: PointerJson[];
  ungrounded_values: unknown[] | ValueState;
  history: Record<string, unknown>[];
}

export interface RunOutcome {
  reason_code: string;
  human_confirmation_required: boolean;
  coverage_status: "complete" | "reduced";
  candidates: Record<string, unknown>[];
  withheld: Record<string, unknown>[];
  not_connected: string[];
}

export type TaskState =
  | "not_run"
  | "running"
  | "waiting_for_approval"
  | "done"
  | "failed"
  | "refused"
  | string;

export interface OneTimeTask {
  task_id: string;
  owner: string | null;
  created_from: string | null;
  created_at: string | null;
  run_id: string | null;
  kind: "one_time_task";
  state: TaskState;
  /** Set when the run failed: the reference the server log carries the details under. */
  error_ref?: string | null;
  attempts?: number;
  held: number;
  executed?: number;
  decided_by?: string;
  run: {
    status: string;
    started: string | null;
    ended: string | null;
    trigger: Record<string, unknown>;
    answer: { markdown: string; citations: PointerJson[] } & Record<string, unknown>;
    outcome: RunOutcome | null;
    coverage: { searched: string[]; skipped: Record<string, unknown>[]; reasons: string[] } | null;
    models_used: string[];
    steps: number;
  } | null;
  actions: OneTimeAction[];
}

export const oneTimeApi = {
  compile: (text: string, answers: Record<string, string>) =>
    post<OneTimeDraft>("/intelligence/one-time/compile", { text, answers }),
  run: (draft_id: string) => post<OneTimeTask>("/intelligence/one-time/run", { draft_id }),
  get: (taskId: string) => call<OneTimeTask>(`/intelligence/one-time/${encodeURIComponent(taskId)}`),
  approve: (taskId: string, actionId: string) =>
    post<OneTimeTask>(
      `/intelligence/one-time/${encodeURIComponent(taskId)}/actions/${encodeURIComponent(actionId)}/approve`,
      {},
    ),
  reject: (taskId: string, actionId: string, reason: string) =>
    post<OneTimeTask>(
      `/intelligence/one-time/${encodeURIComponent(taskId)}/actions/${encodeURIComponent(actionId)}/reject`,
      { reason },
    ),
  /** Re-run a FAILED task's kept plan. The gateway refuses unless the failed attempt left no write. */
  retry: (taskId: string) => post<OneTimeTask>(`/intelligence/one-time/${encodeURIComponent(taskId)}/retry`, {}),
};

// ============================================================================================
// pure decisions (unit-tested in e2e/intelligence.spec.ts, @unit)
// ============================================================================================
const HELD = new Set(["proposed", "approved"]);

/** Writes still waiting on a person, counted from the ACTIONS (not trusted from a summary field). */
/** The envelope in place of a write's values, or null when they are shown (with their pointers).
 *  A withheld write carries no pointers, so an args object that merely looks like an envelope
 *  but arrives WITH pointers is a value, not a withholding. */
export function withheldArgs(action: Pick<OneTimeAction, "args" | "derived_from">): ValueState | null {
  return action.derived_from.length === 0 && isValueState(action.args) ? action.args : null;
}

export function heldWrites(task: Pick<OneTimeTask, "actions">): OneTimeAction[] {
  return task.actions.filter((a) => HELD.has(a.status));
}

/** The one line the panel leads with. A held write always wins over a "done" anywhere else. */
export function taskHeadline(task: Pick<OneTimeTask, "state" | "actions"> & { error_ref?: string | null }): {
  tone: "waiting" | "done" | "failed";
  text: string;
} {
  const held = heldWrites(task).length;
  if (task.state === "failed") {
    const ref = task.error_ref ? ` (reference ${task.error_ref})` : "";
    // Counted from the actions: a write proposed before the failure may since have been decided.
    const ex = task.actions.filter((a) => a.status === "executed").length;
    const rj = task.actions.filter((a) => a.status === "rejected").length;
    const parts = [
      held ? `${held} write${held === 1 ? "" : "s"} it proposed ${held === 1 ? "is" : "are"} waiting for your decision` : "",
      ex ? `${ex} write${ex === 1 ? "" : "s"} executed` : "",
      rj ? `${rj} rejected` : "",
    ].filter(Boolean);
    const after = ex ? ` ${parts.join(", ")}.` : ` ${[...parts, "Nothing was written"].join(". ")}.`;
    return { tone: "failed", text: `The run failed${ref}.${after}` };
  }
  if (task.state === "running") return { tone: "waiting", text: "Running: not done yet." };
  if (held > 0 || task.state === "waiting_for_approval") {
    const n = Math.max(held, 1);
    return {
      tone: "waiting",
      text: `Not done: ${n} write${n === 1 ? "" : "s"} waiting for your approval. Nothing has been written yet${
        task.actions.some((a) => a.status === "executed") ? " except what is marked executed" : ""
      }.`,
    };
  }
  if (task.state === "done") {
    const ex = task.actions.filter((a) => a.status === "executed").length;
    const rj = task.actions.filter((a) => a.status === "rejected").length;
    const parts = [ex ? `${ex} write${ex === 1 ? "" : "s"} executed` : "", rj ? `${rj} rejected` : ""].filter(Boolean);
    return { tone: "done", text: parts.length ? `Done: ${parts.join(", ")}.` : "Done: read-only, nothing was written." };
  }
  return { tone: "failed", text: `The run ended ${task.state}: see the answer for why.` };
}

/** Retry is offered only for a failed task whose attempt proposed nothing (the gateway's own rule:
 *  a retry after a proposal would propose that write again). */
export function canRetry(task: Pick<OneTimeTask, "state" | "actions">): boolean {
  return task.state === "failed" && task.actions.length === 0;
}

/** Why the Run button is off, or null when it may be pressed. Reads only what the gateway sent. */
export function runBlocker(draft: Pick<OneTimeDraft, "runnable" | "open_questions" | "compilation" | "plan">): string | null {
  if (draft.open_questions.length > 0)
    return `${draft.open_questions.length} question${draft.open_questions.length === 1 ? "" : "s"} to answer first`;
  if (!draft.plan) return "no plan was compiled";
  if (draft.compilation && draft.compilation.outcome !== "compiled")
    return `the plan did not compile: ${
      draft.compilation.refusals.map((r) => r.detail || r.code).filter(Boolean).join("; ") ||
      draft.compilation.reason || draft.compilation.outcome
    }`;
  if (!draft.runnable) return "the gateway says this plan cannot run";
  return null;
}

/** The answers to send back: only questions the person actually answered, trimmed. */
export function answeredOnly(answers: Record<string, string>, questions: OpenQuestion[]): Record<string, string> {
  const ids = new Set(questions.map((q) => q.id));
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(answers)) if (ids.has(k) && v.trim()) out[k] = v.trim();
  return out;
}

/** Sources grouped for "what it will read": each connection once, internal tools listed by name. */
export function readsByConnection(reads: PlanRead[]): { connection: string | null; tools: string[] }[] {
  const order: (string | null)[] = [];
  const by = new Map<string | null, string[]>();
  for (const r of reads) {
    const key = r.family === "source" || r.family === "facts" || r.family === "delegate" ? r.connection : null;
    if (!by.has(key)) {
      by.set(key, []);
      order.push(key);
    }
    by.get(key)!.push(r.tool);
  }
  return order.map((c) => ({ connection: c, tools: by.get(c)! }));
}
