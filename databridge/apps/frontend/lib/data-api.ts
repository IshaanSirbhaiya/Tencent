// Client for the Data page (Layer 3): the data agent (`/materialise/*`) and the direct converter
// (`/convert`). Every type here mirrors what `apps/gateway/databridge_gateway/materialise_routes.py`
// and `packages/materialise/databridge_materialise/platform.py` actually return — nothing on the
// page is computed from anything but these responses.
import { GATEWAY, authHeaders, api } from "./api";

export interface Refusal {
  code: string;
  message: string;
  nearest_alternative?: string | null;
  missing?: string[];
  job_id?: string;
  /** The planner's questions with their ids (E2E run 3, P-10): answer one by posting the SAME
   * request with `answers: {id: text}`. */
  question_items?: QuestionItem[];
}

/** One planner question, answerable by id (`mat:<sha1 of the question>`). */
export interface QuestionItem {
  id: string;
  text: string;
}

/** A shape row from `GET /materialise/plans` (receipt rows are filtered out by the caller). */
export interface ShapeRow {
  id: string;
  shape: string;
  built: boolean;
  reason: string | null;
  receipt?: boolean;
}

export interface PlansResponse {
  available: boolean;
  plans: (ShapeRow & Record<string, unknown>)[];
  shapes: string[];
  shapes_not_built?: { shape: string; reason: string | null }[];
  missing: string[];
  note: string | null;
}

export interface CatalogueEntity {
  name: string;
  origin: string;
  source_id: string;
  fields: string[];
  key: string[];
  valid_time_field: string | null;
  system_time_field: string | null;
  label_candidates: string[];
  feature_candidates: string[];
  text_field: string | null;
  rows: number | null;
  subject: boolean;
  identifier_fields: string[];
}

export interface CatalogueResponse {
  system_as_of: string;
  entities: CatalogueEntity[];
  excluded: Record<string, unknown>[];
  shapes: { shape: string; built: boolean; reason: string | null }[];
}

export interface PointerCheck {
  grounded_cells: number;
  pointers_resolved: number;
  values_match: number;
  all_resolved: boolean;
  all_match: boolean;
  failures: { row: number; column: string; pointer_kind: string; resolves: boolean; matches: boolean }[];
}

export interface ProducedBy {
  agent: string;
  agent_version: string;
  task_class: string | null;
  model: string | null;
  prompt_version: string | null;
  run_id: string;
  at: string;
}

/** `summary_from_doc` — the job result and each row of `GET /materialise/receipts`. */
export interface RunSummary {
  plan_hash: string;
  content_hash: string;
  shaped_by: "agent" | "plan";
  reproducible: boolean | null;
  executed_at: string;
  shape: string | null;
  entity: string | null;
  request: string | null;
  /** The answers to the planner's questions this run was authored with, `{id: text}`. */
  answers?: Record<string, string>;
  run_by: string | null;
  rows: { emitted: number; rejected_by_policy: number; rejected_by_maturity: number; rejected_incomplete: number } | null;
  cells: { grounded: number; unresolved: number; policy_suppressed: number; grounding_dropped: number } | null;
  verdicts: Record<string, number> | null;
  unresolved: { cells: number; columns_fully_absent: string[] } | null;
  pointer_check: PointerCheck | null;
  produced_by: ProducedBy | null;
  honest_summary: string | null;
  notes: string[] | null;
  files: string[];
  file_roles?: Record<string, string>;
}

export interface SourceSnapshot {
  source_id: string;
  tier: string;
  as_of: string;
  content_hash: string;
  schema_hash?: string;
  version_id?: string | null;
  capture_ref?: string | null;
}

/** `GET /materialise/receipts/{plan_hash}` — the whole receipt.json plus the file list. */
export interface ReceiptDoc {
  receipt: {
    plan_hash: string;
    content_hash: string;
    shaped_by: string;
    executed_at: string;
    columns_fully_absent: string[];
    columns_refused: string[];
    missingness_leak_check: { passed: boolean; max_class_delta_observed: number; threshold: number; detail: string; worst_column: string | null } | null;
    shaping_directives: string[];
    notes: string[];
    output_uri: string | null;
    croissant_uri: string | null;
    purpose: string;
    policy_applied: string;
    cells?: { grounded: number; unresolved: number; policy_suppressed: number; grounding_dropped: number } | null;
    rows?: { emitted: number; rejected_by_policy: number; rejected_by_maturity: number; rejected_incomplete: number } | null;
    [k: string]: unknown;
  };
  platform: {
    shape: string;
    entity: string;
    request: string | null;
    planner: string | null;
    run_by: string;
    produced_by: ProducedBy;
    reproducible: boolean;
    honest_summary: string;
    system_as_of: string;
    identifiers: string;
    sources: SourceSnapshot[];
    pointer_check: PointerCheck | null;
    unresolved: { cells: number; columns_fully_absent: string[] };
    columns: string[];
  };
  files: string[];
  /** Optional, additive (FXM): the gateway's own role for each file (receipt | plan | dataset |
   * pointers | metadata | other). Read only from a receipt that passed integrity; receipt/plan (or "evidence")
   * are evidence, every other role is gated like a dataset. Absent → only receipt.json and plan.yaml
   * are evidence (fail closed). */
  file_roles?: Record<string, string>;
}

export interface JobEvent {
  stage: string;
  at?: string;
  planner?: string;
  shape?: string;
  plan_hash?: string;
}

export interface MaterialiseJob {
  job_id: string;
  kind: string;
  status: "running" | "succeeded" | "refused" | "failed";
  by: string | null;
  started: string;
  ended: string | null;
  events: JobEvent[];
  progress: JobEvent | null;
  error: string | null;
  result: RunSummary | null;
  refusal?: Refusal;
  request?: string | null;
}

/** An answer from the gateway: the body, or the refusal it sent (a refusal is an answer). */
export type Answer<T> = { ok: true; status: number; body: T } | { ok: false; status: number; refusal: Refusal };

async function ask<T>(path: string, init?: RequestInit): Promise<Answer<T>> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    });
  } catch (e) {
    return {
      ok: false,
      status: 0,
      refusal: { code: "GATEWAY_UNREACHABLE", message: `could not reach the gateway at ${GATEWAY} (${e instanceof Error ? e.message : "network error"})` },
    };
  }
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (res.ok) return { ok: true, status: res.status, body: body as T };
  const refusal = body.refusal as Refusal | undefined;
  if (refusal?.code) return { ok: false, status: res.status, refusal };
  if (res.status === 401) {
    return {
      ok: false,
      status: 401,
      refusal: {
        code: "NOT_AUTHENTICATED",
        message: "the gateway refused this page's credential (every /materialise route needs a signed-in principal; is NEXT_PUBLIC_WALNUT_TOKEN set for this build?)",
      },
    };
  }
  const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? "");
  return { ok: false, status: res.status, refusal: { code: `HTTP_${res.status}`, message: detail || `${path} answered ${res.status}` } };
}

/** Throws the refusal as an Error — for react-query. */
async function must<T>(p: Promise<Answer<T>>): Promise<T> {
  const a = await p;
  if (a.ok) return a.body;
  throw new Error(`${a.refusal.code}: ${a.refusal.message}`);
}

export const dataApi = {
  plans: () => must(ask<PlansResponse>("/materialise/plans")),
  catalogue: () => ask<CatalogueResponse>("/materialise/catalogue"),
  receipts: () => must(ask<{ receipts: RunSummary[] }>("/materialise/receipts")),
  receipt: (planHash: string) => ask<ReceiptDoc>(`/materialise/receipts/${encodeURIComponent(planHash)}`),
  submit: (request: string, answers?: Record<string, string>) =>
    ask<{ job_id: string; status: string; poll: string }>("/materialise", {
      method: "POST",
      body: JSON.stringify(answers && Object.keys(answers).length > 0 ? { request, answers } : { request }),
    }),
  job: (jobId: string) => ask<MaterialiseJob>(`/materialise/jobs/${encodeURIComponent(jobId)}`),
  convert: api.convert,
};

/** Fetch one output file of a run WITH the credential (a bare <a href> would carry none). */
export async function fetchRunFile(planHash: string, name: string): Promise<Blob> {
  const res = await fetch(
    `${GATEWAY}/materialise/receipts/${encodeURIComponent(planHash)}/files/${encodeURIComponent(name)}`,
    { cache: "no-store", headers: authHeaders() },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { refusal?: Refusal };
    throw new Error(body.refusal ? `${body.refusal.code}: ${body.refusal.message}` : `download answered ${res.status}`);
  }
  return res.blob();
}

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
