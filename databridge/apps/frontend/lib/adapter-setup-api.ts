/**
 * `/adapter-setup/*` — set up a source through its adapter agent (FEAT8). Mirrors, field for field,
 * `apps/gateway/databridge_gateway/adapter_setup_routes.py`; when they disagree the routes are right.
 *
 * Its own file because several workstreams edit this frontend at once. Two rules it keeps:
 *   - **Who decides is the signed-in principal.** Approvals send no name; the gateway records the
 *     authenticated principal.
 *   - **A refusal is shown, not swallowed.** `{refusal: {code, message}}` is thrown as an
 *     `AgentsApiError` with the gateway's code and message verbatim.
 */
import { GATEWAY, authHeaders } from "./api";
import { AgentsApiError, agentsApiErrorFrom } from "./agents-api";

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
    throw agentsApiErrorFrom(res.status, (body as { refusal: SetupRefusal }).refusal);
  }
  if (!res.ok) throw new AgentsApiError(res.status, `HTTP_${res.status}`, `${path} → ${res.status}`);
  return body as T;
}
/** The common refusal envelope (R-028): the standard `code`, the former specific `reason_code`,
 * and a failure's `error_ref` (the gateway never sends the exception's text). */
export interface SetupRefusal {
  code: string;
  message: string;
  reason_code?: string;
  error_ref?: string;
}

/** The gateway's message verbatim, with the reference a failure carries, so it can be quoted. */
export function refusalMessage(r: SetupRefusal): string {
  return r.error_ref ? `${r.message} (reference ${r.error_ref})` : r.message;
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const enc = encodeURIComponent;

// ============================================================================================
// shapes
// ============================================================================================
export type StepStatus = "todo" | "doing" | "done" | "waiting_for_person" | "blocked";
export type StepId = "plan" | "approve_plan" | "discover" | "propose_mapping" | "explain" | "approve_mapping" | "approve_documents" | "handoff";

/** A decision the build would otherwise leave out silently; the hand-off names each as deferred. */
export interface PendingDecision {
  key: string;
  kind: "link" | "hold" | "table" | "identity";
  table: string | null;
  what: string;
  deferral: "defer" | "keep held";
}

/** The documents the latest discovery captured, and whether a person decided on them: `allowed` are
 *  built at the hand-off; `kept` were already built by an earlier run and are kept as they are (W-05). */
export interface SetupDocuments {
  captured: number;
  allowed: number;
  /** Already built by an earlier run, bytes unchanged: the keeper keeps them, nothing is rebuilt. */
  kept?: number;
  held: number;
  digest: string | null;
  decided: boolean;
  discovery_run_id: string | null;
}

export interface SetupStep {
  id: StepId;
  label: string;
  status: StepStatus;
  detail: string;
}

export interface SetupActivity {
  run_id: string;
  at: string | null;
  status: string | null;
  reason: string | null;
  by: string | null;
  step: string | null;
}

export interface SetupState {
  source_id: string;
  purpose: string;
  transport: string | null;
  phase: "ready" | "in_setup" | "kept";
  steps: SetupStep[];
  running: Record<string, unknown> | null;
  plan_status: string | null;
  plan_stale: string | null;
  runs: number;
  last_run: { run_id: string; status: string } | null;
  /** The latest capture-only discovery: its status is the run's own report (`discovered` = success). */
  discovery: { run_id: string | null; status: string; message: string; finished_at: string | null } | null;
  mapping_counts: { proposed: number; approved: number; unmapped: number };
  explanation_fresh: boolean;
  /** The current explanation's digest — sent back with a mapping approval (null while it is not current). */
  explanation_digest: string | null;
  handoff_problems: string[];
  pending_decisions: PendingDecision[];
  documents: SetupDocuments;
  keeper: { id: string; status: string; title: string } | null;
  activity: SetupActivity[];
}

/** A `SourcePointer` (kind `table_cell`) as JSON: where the cited value sits in the captured bytes. */
export interface CellPointer {
  source_id: string;
  kind: string;
  table?: string;
  column?: string;
  row_id?: string;
  capture_digest?: string;
}

export interface Evidence {
  id: string;
  /** The ONE fact this cell supports. */
  fact: string;
  what: string;
  column: string;
  note: string;
  row_id: string | null;
  value: string | null;
  masked: boolean;
  pointer: CellPointer;
}

/** Where a fact comes from: the captured table (cites cells of its own column), the mapper's proposal or
 *  the vocabulary's declaration (both cite nothing — they are for a person to approve, not evidence). */
export type FactBasis = "capture" | "mapper" | "vocabulary";

/** A predeclared fact, rendered by the agent's own template. The model only chooses which to show. */
export interface Fact {
  id: string;
  type: string;
  text: string;
  basis: FactBasis;
  evidence: string[];
}

export interface Claim {
  text: string;
  evidence: string[];
  fact?: string;
  type?: string;
  basis?: FactBasis;
}

export interface Relationship {
  edge_kind: string | null;
  column: string | null;
  fk: string | null;
  target_table: string | null;
  from_kind: string | null;
  to_kind: string | null;
  declared: boolean;
  direction: string;
}

export interface TableExplanation {
  table: string;
  status: "proposed" | "approved" | "unmapped";
  kind: string | null;
  kind_description: string | null;
  is_person: boolean;
  is_subject: boolean;
  association: boolean;
  keys: { primary_key: string[]; canonical_id: string | null; candidate_identifier: string | null; label: string | null };
  relationships: Relationship[];
  split: { column: string; kinds: Record<string, string>; default: string } | null;
  columns: string[];
  rows_captured: number;
  mapping_digest: string;
  /** The capture digest the discovery recorded for this table; the evidence points into it. */
  capture_sha: string | null;
  capture_problem: string | null;
  facts: Fact[];
  evidence: Evidence[];
  summary: Claim;
  reasons: Claim[];
  explained_by: "rules" | "model";
  model_dropped: number;
  sent_to_model: boolean;
  not_sent_reason: string | null;
}

export interface Explanation {
  source_id: string;
  generated_at: string;
  advisory: true;
  note: string;
  produced_by: { agent: string; task_class: string; model: string | null; model_error: string | null; mode: "model" | "rules" };
  /** The discovery the explanation was written from. */
  discovery: { run_id: string; plan_digest: string } | null;
  mapping_digest: string;
  tables: TableExplanation[];
}

export interface SetupDetail {
  state: SetupState;
  connection: Record<string, unknown>;
  plan: Record<string, unknown> | null;
  /** Digest of the plan shown; an approval names it (the Sources route refuses a plan that changed). */
  plan_digest: string | null;
  mapping: { tables: Record<string, { status: string; approval?: { principal_id?: string } | null }> } | null;
  explanation: Explanation | null;
}

export const CADENCES = ["daily", "hourly", "manual"] as const;
export type Cadence = (typeof CADENCES)[number];

// ============================================================================================
// pure helpers (unit-tested)
// ============================================================================================
/** The step the page advances on its own — never an approval. `discover` once a person approved the
 *  plan and nothing has run; `explain` once a mapping exists and its explanation is missing or
 *  stale. Nothing while a run is in flight. */
export function nextAutoStep(s: Pick<SetupState, "steps" | "running">): "discover" | "explain" | null {
  if (s.running) return null;
  const st = (id: StepId) => s.steps.find((x) => x.id === id)?.status;
  if (st("approve_plan") === "done" && st("discover") === "todo") return "discover";
  if (st("propose_mapping") === "done" && st("explain") === "todo") return "explain";
  return null;
}

/** Whether discovery may be started again by a person: it ran under this plan and did not succeed. */
export function canRediscover(s: Pick<SetupState, "steps" | "running"> | null | undefined): boolean {
  return !!s && !s.running && s.steps.find((x) => x.id === "discover")?.status === "blocked";
}

/** How a claim is labelled: cited cells, or what it is when it cites none (never shown as evidenced). */
export function basisWords(c: Pick<Claim, "basis" | "evidence">): string {
  if (c.basis === "capture" && c.evidence.length) return "";
  if (c.basis === "vocabulary") return "the vocabulary says";
  return "proposed by the mapper";
}

/** The mapping approval may be offered only against the CURRENT explanation (its digest is sent back). */
export function mappingApprovalOffered(s: Pick<SetupState, "explanation_fresh" | "explanation_digest"> | null | undefined): boolean {
  return !!s && s.explanation_fresh && !!s.explanation_digest;
}

/** The evidence a claim cites, in order. An id the explanation does not carry is dropped, so a
 *  sentence is never shown as grounded on something that is not there. */
export function citedEvidence(claim: Claim, evidence: readonly Evidence[]): Evidence[] {
  const byId = new Map(evidence.map((e) => [e.id, e]));
  return claim.evidence.map((id) => byId.get(id)).filter((e): e is Evidence => !!e);
}

/** "table · column · row r · capture abcd1234" — where a cited value sits. */
export function pointerText(p: CellPointer): string {
  const parts = [p.table, p.column, p.row_id ? `row ${p.row_id}` : null, p.capture_digest ? `capture ${p.capture_digest.slice(0, 12)}` : null];
  return parts.filter(Boolean).join(" · ");
}

/** Whether the hand-off may be offered: the gateway's own gate is empty and the agent is not yet the
 *  keeper. Fails closed on a missing state. */
export function handoffOffered(
  s: Pick<SetupState, "handoff_problems" | "keeper"> & Partial<Pick<SetupState, "pending_decisions">> | null | undefined,
  deferred: readonly string[] = [],
): boolean {
  return !!s && s.handoff_problems.length === 0 && s.keeper?.status !== "active" && allDeferred(s.pending_decisions ?? [], deferred);
}

/** Every pending decision is named as deferred by the person — exactly the gateway's own rule. */
export function allDeferred(pending: readonly Pick<PendingDecision, "key">[], deferred: readonly string[]): boolean {
  const want = new Set(pending.map((p) => p.key));
  const got = new Set(deferred);
  return want.size === got.size && [...want].every((k) => got.has(k));
}

/** The documents decision is offered when the latest discovery captured documents nobody decided on yet. */
export function documentsDecisionOffered(d: SetupDocuments | null | undefined): boolean {
  return !!d && !!d.digest && !!d.discovery_run_id && !d.decided;
}

/** The documents button's words: build the new ones and/or keep the already-built ones (W-05). */
export function documentsDecisionLabel(d: Pick<SetupDocuments, "allowed" | "kept">): string {
  const kept = d.kept ?? 0;
  if (d.allowed > 0 && kept > 0) return `Build ${d.allowed} and keep ${kept} already-built document(s)`;
  if (kept > 0) return `Keep these ${kept} already-built document(s)`;
  return `Build these ${d.allowed} document(s)`;
}

/** "people" / "a protected subject" / "not people" — the person line, from the vocabulary's flags. */
export function personWords(t: Pick<TableExplanation, "is_person" | "is_subject" | "kind">): string {
  if (!t.kind) return "no kind";
  if (t.is_person) return "people — never merged";
  if (t.is_subject) return "a protected subject — never merged";
  return "not people";
}

/** A table's status NOW: the live mapping's when it lists the table, else the status the explanation
 *  was written against (an approval after the explanation must not leave the table approvable). */
export function liveStatus(t: Pick<TableExplanation, "table" | "status">, live?: Record<string, { status: string }> | null): string {
  return live?.[t.table]?.status ?? t.status;
}

/** The tables a person may approve here: those proposed now. */
export function approvableTables(
  ex: Pick<Explanation, "tables"> | null | undefined,
  live?: Record<string, { status: string }> | null,
): string[] {
  return (ex?.tables ?? []).filter((t) => liveStatus(t, live) === "proposed").map((t) => t.table);
}

// ============================================================================================
// routes
// ============================================================================================
export const setupApi = {
  sources: (signal?: AbortSignal) => call<{ sources: SetupState[]; problems: string[] }>("/adapter-setup/sources", undefined, signal),
  get: (id: string, signal?: AbortSignal) => call<SetupDetail>(`/adapter-setup/${enc(id)}`, undefined, signal),
  start: (id: string) => post<{ agent: string; plan: Record<string, unknown>; state: SetupState }>(`/adapter-setup/${enc(id)}/start`),
  approvePlan: (id: string, vouch: readonly string[], planDigest: string | null) =>
    post<{ state: SetupState }>(`/adapter-setup/${enc(id)}/plan/approve`, { vouch_read_tools: [...vouch], plan_digest: planDigest }),
  discover: (id: string) => post<{ run_id: string; job_id: string; status: string; mode: "discover" }>(`/adapter-setup/${enc(id)}/discover`),
  explain: (id: string) => post<{ explanation: Explanation; state: SetupState }>(`/adapter-setup/${enc(id)}/explain`),
  approveMapping: (id: string, tables: string[], explanationDigest: string) =>
    post<{ state: SetupState }>(`/adapter-setup/${enc(id)}/mapping/approve`, { tables, explanation_digest: explanationDigest }),
  handoff: (id: string, cadence: Cadence, deferred: readonly string[]) =>
    post<{ agent: string; job_id: string; status: string }>(`/adapter-setup/${enc(id)}/handoff`, { cadence, deferred: [...deferred] }),
  approveDocuments: (id: string, discoveryRunId: string, documentsDigest: string) =>
    post<{ state: SetupState }>(`/adapter-setup/${enc(id)}/documents/approve`, {
      discovery_run_id: discoveryRunId,
      documents_digest: documentsDigest,
    }),
};
