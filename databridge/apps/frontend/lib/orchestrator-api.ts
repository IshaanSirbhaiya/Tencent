// Orchestrator gateway client (`/orchestrate/*`) — the ask-the-fleet surface's OWN api module.
// Deliberately separate from lib/api.ts and lib/brain-api.ts: this talks to a different backend
// loop (databridge_orchestrator.Orchestrator, packages/orchestrator) with its own response shape,
// and the two existing modules belong to other surfaces this workstream does not touch.
//
// Render layer only (D-FE-2): every decision — which sources get asked, what gets withheld, what
// counts as "answered" — is made server-side. This module fetches and types the response; it does
// not compute anything the gateway didn't already decide.
import { GATEWAY, authHeaders } from "./api";

export type CellState = "grounded" | "withheld" | "absent";
export type SourceStatus = "answered" | "refused" | "escalated";

/** A `SourcePointer` as the gateway serialises it (`pointer.model_dump(mode="json")`). Open-ended
 *  on purpose — which fields are present depends on `pointer.kind` (table_cell vs email_part vs
 *  derived, …), the same reason `lib/types.ts`'s `SourcePointer` is open-ended. */
export interface OrchestratorPointer {
  source_id: string;
  kind: string;
  [k: string]: unknown;
}

/** One cell, exactly as `databridge_orchestrator.GroundedCell` holds it — a pointer with a trust
 *  label, or a denial with a reason code, or a named absence. Never a sentence. */
export interface OrchestratorCell {
  column: string;
  state: CellState;
  row_id: string | null;
  value: unknown;
  trust: string | null;
  pointer: OrchestratorPointer | null;
  reason_code: string | null;
  policy_id: string | null;
  appealable: boolean | null;
}

/** A whole-source refusal — `databridge_orchestrator.SourceOutcome.denial`, a real `Withheld`
 *  projected to JSON. Present only when `status === "refused"`. */
export interface OrchestratorDenial {
  field_path: string;
  policy_id: string;
  reason_code: string;
  principal: string;
  purpose: string | null;
  appealable: boolean | null;
}

export interface OrchestratorSourceOutcome {
  source_id: string;
  status: SourceStatus;
  message: string;
  capability: string | null;
  entity: string | null;
  fields: string[];
  cells: OrchestratorCell[];
  denial: OrchestratorDenial | null;
}

export interface OrchestratorAskResult {
  question: string;
  principal: { actor: string; on_behalf_of: string | null; purpose: string };
  sources_considered: string[];
  selection_note: string;
  answered: boolean;
  outcome: SourceStatus;
  trace_id: string | null;
  sources: OrchestratorSourceOutcome[];
}

export interface Refusal {
  code: string;
  message: string;
  nearest_alternative?: unknown;
  available?: string[];
}

export interface OrchestratorFleetSource {
  source_id: string;
  kind: string | null;
  pinning_tier: string | null;
  egress_class: string | null;
  capabilities: string[];
  entities: string[];
}

export interface OrchestratorFleetStatus {
  available: boolean;
  reason: string | null;
  estate: string;
  sources: OrchestratorFleetSource[];
  principals: Record<string, string>;
  default_principal: string;
  llm_backend: string | null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  // a refusal rides in the body with a 4xx/5xx — surfaced, not thrown, so a denial renders as a
  // denial rather than an error boundary.
  if (!res.ok && !("refusal" in body)) throw new Error(`${path} → ${res.status}`);
  return body as T;
}

export const orchestratorApi = {
  fleet: () => call<OrchestratorFleetStatus>("/orchestrate/fleet"),
  // The asker is the signed-in principal (server side); the body carries only the question —
  // the gateway rejects `on_behalf_of` with 422.
  ask: (question: string) =>
    call<OrchestratorAskResult | { refusal: Refusal }>("/orchestrate/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
};

export function isRefusal(
  x: OrchestratorAskResult | { refusal: Refusal },
): x is { refusal: Refusal } {
  return (x as { refusal?: Refusal }).refusal !== undefined;
}
