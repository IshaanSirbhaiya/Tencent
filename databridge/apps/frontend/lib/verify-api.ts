/**
 * The seam for `/evidence`'s `Verify` and for `/builder`.
 *
 * Unlike `platform-api.ts`, **nothing here falls back to a preview shape.** A preview trace is a
 * layout with illustrative numbers in it and says so; a preview *verdict* would be a claim that a
 * citation was checked when nothing was checked, and there is no notice big enough to make that
 * safe. So every function in this file either returns what the gateway said or throws, and the
 * pages render the error.
 *
 * The one exception is the verdict LATTICE — the eight states and their meanings, which is
 * vocabulary rather than measurement. When the gateway cannot be reached the page falls back to
 * `config/platform-preview.json`'s copy of it and flags that it is doing so, because a screen with
 * no vocabulary on it teaches a reader nothing at all.
 */
import { GATEWAY, authHeaders } from "./api";
import { PLATFORM_PREVIEW, type Tone } from "./platform-preview";
import type { SourcePointer } from "./types";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, { cache: "no-store", signal, headers: authHeaders() });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(refusalText(body) ?? `${path} → ${res.status}`);
  return body as T;
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(refusalText(body) ?? `${path} → ${res.status}`);
  return body as T;
}

/** A gateway refusal, rendered verbatim. These routes refuse for reasons worth reading. */
function refusalText(body: unknown): string | null {
  const refusal = (body as { refusal?: { code?: string; message?: string } })?.refusal;
  if (!refusal) return null;
  return [refusal.code, refusal.message].filter(Boolean).join(": ") || null;
}

// ---------------------------------------------------------------------------------------------
// the verdict lattice
// ---------------------------------------------------------------------------------------------
export interface VerdictSpec {
  verdict: string;
  name: string;
  meaning: string;
  tone: Tone;
  must_escalate: boolean;
  is_positive: boolean;
  requires_advertised_capability: boolean;
}

export interface LatticeResult {
  verdicts: VerdictSpec[];
  live: boolean;
  reason: string | null;
  note: string | null;
}

/** The eight states. Served by the gateway so the frontend does not keep its own copy of a closed
 *  vocabulary — a second copy is how a UI keeps rendering eight after the enum grows a ninth. */
export async function fetchLattice(signal?: AbortSignal): Promise<LatticeResult> {
  try {
    const body = await get<{ verdicts: VerdictSpec[]; note: string }>(
      "/evidence/verify/lattice",
      signal,
    );
    return { verdicts: body.verdicts, live: true, reason: null, note: body.note };
  } catch (err) {
    // The bundled copy is vocabulary, not measurement — but it is still a copy, so say so.
    return {
      verdicts: PLATFORM_PREVIEW.evidence.verdicts.map((v) => ({
        verdict: v.verdict.toLowerCase(),
        name: v.verdict,
        meaning: v.meaning,
        tone: v.tone,
        must_escalate: v.verdict.toUpperCase() === "DRIFT_MATERIAL",
        is_positive: v.verdict.toUpperCase().startsWith("VERIFIED"),
        requires_advertised_capability: v.verdict.toUpperCase() === "DRIFT_BENIGN",
      })),
      live: false,
      reason: err instanceof Error ? err.message : "gateway unreachable",
      note: null,
    };
  }
}

// ---------------------------------------------------------------------------------------------
// the fleet, and the citations it has served
// ---------------------------------------------------------------------------------------------
export interface FleetSource {
  source_id: string;
  pinning_tier: string | null;
  egress_class: string | null;
  deletes_detectable: string | null;
  detects_benign_drift: boolean;
  reachable_verdicts: string[];
  entities: string[];
  working_copy: string | null;
}

export interface FleetMutation {
  at: string;
  source_id: string;
  entity: string;
  column: string;
  row_id: string;
  /** What the source held immediately before this write — read at write time, not recalled. */
  from: unknown;
  /** What the citation vouches for. Differs from `from` on a second mutation of the same cell. */
  as_cited: unknown;
  to: unknown;
  wrote_to: string;
}

export interface FleetStatus {
  available: boolean;
  reason: string | null;
  estate: string;
  sources: FleetSource[];
  read_purpose: string;
  verify_purpose: string;
  mutations: FleetMutation[];
}

export interface Citation {
  citation_id: string;
  source_id: string;
  entity: string;
  column: string;
  row_id: string | null;
  value_at_read: unknown;
  read_at: string;
  trust: string | null;
  pointer: SourcePointer;
}

export interface CitationsResult {
  citations: Citation[];
  fleet: FleetStatus;
}

export function fetchCitations(refresh = false, signal?: AbortSignal): Promise<CitationsResult> {
  return get<CitationsResult>(`/evidence/verify/citations?refresh=${refresh}`, signal);
}

// ---------------------------------------------------------------------------------------------
// verification, and the reverse index
// ---------------------------------------------------------------------------------------------
/**
 * What relies on a pointer.
 *
 * `answer` has THREE values and the difference between two of them is the whole point of the
 * panel: `unavailable` and `unknown` both mean *nobody can tell you*, and only `known` with an
 * empty `records` means *nothing is affected*. Rendering the first two as the third makes a drift
 * alarm falsely reassuring, which is worse than having no alarm.
 */
export interface ImpactAnswer {
  answer: "known" | "unknown" | "unavailable";
  reason: string;
  summary?: string;
  records: {
    entity_kind: "node" | "edge" | string;
    entity_id: string;
    kind: string | null;
    label: string | null;
    field: string | null;
    value: unknown;
    is_open: boolean;
  }[];
  emissions: {
    kind: string;
    ref: string;
    plan_hash: string | null;
    ledger_bound: boolean;
    emitted_at: string;
    stale_since: string | null;
    stale_cause: string | null;
  }[];
  open_versions?: number;
  ledger_bound?: number;
  unindexed_pointers?: number;
  pointers_in_closure?: number;
  coverage: Record<string, number> | null;
}

export interface VerifyResult {
  verdict: string;
  name: string;
  tone: Tone;
  must_escalate: boolean;
  is_positive: boolean;
  meaning: string;
  note: string | null;
  verified_by: string | null;
  verified_at: string;
  pointer: SourcePointer;
  cited_value: unknown;
  cited_value_known: boolean;
  current_value: unknown;
  current_value_known: boolean;
  impact: ImpactAnswer;
}

export function verifyPointer(pointer: SourcePointer): Promise<VerifyResult> {
  return post<VerifyResult>("/evidence/verify", { pointer });
}

export function fetchImpact(pointer: SourcePointer): Promise<{ impact: ImpactAnswer }> {
  return post<{ impact: ImpactAnswer }>("/evidence/impact", { pointer });
}

/** Demo step 2 — change the underlying row. The gateway refuses any cell it has not served. */
export function mutateCell(
  pointer: SourcePointer,
  newValue: string,
): Promise<{ changed: FleetMutation; note: string }> {
  return post("/evidence/verify/mutate", { pointer, new_value: newValue });
}

// ---------------------------------------------------------------------------------------------
// /builder
// ---------------------------------------------------------------------------------------------
export interface DeclarationRef {
  id: string;
  path: string;
  bytes: number;
  pack: string;
}

export interface CompiledAdapter {
  source_id: string;
  title: string | null;
  estate: string | null;
  grants: { read: string[]; write: string[]; note: string };
  sink_policy: { egress_allow: string[]; egress_class: string | null; note: string };
  pinning: {
    tier: string;
    capture: unknown;
    note: string | null;
    deletes_detectable: string | null;
  };
  projection: { entity: string | null; key: string[]; fields: string[] };
  trust: {
    field: string;
    trust: string;
    justification: string | null;
    note: string | null;
    redaction: string[];
  }[];
  server: Record<string, string | null>;
  verification: unknown;
}

export interface CompileRefusal {
  code: string;
  kind: string;
  message: string;
  stage: "parse" | "containment" | "compile" | string;
}

export interface AdapterCompileResult {
  compiled: boolean;
  refusal: CompileRefusal | null;
  compiles_to?: CompiledAdapter;
}

export function fetchDeclarations(
  signal?: AbortSignal,
): Promise<{ declarations: DeclarationRef[]; default: string | null }> {
  return get("/builder/adapter/declarations", signal);
}

export function fetchDeclaration(
  id: string,
  signal?: AbortSignal,
): Promise<{ id: string; declaration: string }> {
  return get(`/builder/adapter/declaration?id=${encodeURIComponent(id)}`, signal);
}

export function compileAdapter(declaration: string): Promise<AdapterCompileResult> {
  return post<AdapterCompileResult>("/builder/adapter/compile", { declaration });
}

export interface AgentBuilderStatus {
  available: boolean;
  endpoint: string;
  missing: string[];
  note: string;
}

export function fetchAgentStatus(signal?: AbortSignal): Promise<AgentBuilderStatus> {
  return get<AgentBuilderStatus>("/builder/agent/status", signal);
}

/** One reason a declaration did not compile, with the comparison that produced it. */
export interface AgentRefusal {
  code: string;
  detail: string;
  structural?: boolean;
}

/** The four artefacts a passing agent declaration compiles into (`AGENT_PLANE.md` D13). */
export interface CompiledAgent {
  compiled_id: string;
  agent: string;
  version: number;
  pack: string;
  purpose: string;
  declared_in: string;
  declaration_digest: string;
  compiled_at: string;
  grants: {
    grants: {
      grant_id: string;
      source_id: string;
      capability: string;
      purpose: string;
      max_trust: string;
      resolved_through: string;
    }[];
  };
  sink_policy: {
    read_ceiling: string;
    forbidden_sinks: string[];
    adapter_enforced: string[];
    orchestrator_enforced: string[];
    action_sinks: string[];
    requires_field_level_filter: string[];
    reasoning: string;
  };
  budget: {
    admission_id: string;
    tokens_per_run: number;
    escalations_per_day: number;
    cost_ceiling_usd_per_day: number;
    enforced_by: string;
  };
  evals: {
    binding_id: string;
    golden_set: string;
    golden_set_digest: string;
    records: number;
    gates: [string, number][];
    feasibility: {
      task_class: string;
      target_alpha: number;
      base_risk: number;
      minimum_abstention: number;
      declared_max_abstention: number;
      certifiable: boolean;
      feasible: boolean;
      citation: string;
      reasoning: string;
    } | null;
  };
}

export interface AgentCompileResult {
  compiled: boolean;
  outcome?: "compiled" | "refused";
  stage: "parse" | "compile" | string;
  agent?: string;
  version?: number;
  pack?: string;
  reasoning: string | null;
  refusals: AgentRefusal[];
  declaration_digest?: string;
  catalogue_digest?: string;
  vocabulary_packs?: string[];
  compiles_to?: CompiledAgent | null;
}

export function fetchAgentDeclarations(
  signal?: AbortSignal,
): Promise<{ declarations: DeclarationRef[]; default: string | null }> {
  return get("/builder/agent/declarations", signal);
}

export function fetchAgentDeclaration(
  id: string,
  signal?: AbortSignal,
): Promise<{ id: string; declaration: string }> {
  return get(`/builder/agent/declaration?id=${encodeURIComponent(id)}`, signal);
}

/**
 * Compile an agent declaration.
 *
 * A REFUSAL comes back as a 200 with `compiled: false`, because it is the result of the compile
 * and not a transport failure. Only a 501 — the compiler package absent from the deployment —
 * throws, and the pane then renders what is missing rather than a fabricated refusal.
 */
export function compileAgent(
  declaration: string,
  declaredIn?: string,
): Promise<AgentCompileResult> {
  return post<AgentCompileResult>("/builder/agent/compile", {
    declaration,
    declared_in: declaredIn ?? null,
  });
}
