/**
 * `/ontology` — the gateway client for the vocabulary: entity kinds, relation kinds, the growth
 * queue, owner edits, removal and the audit trail.
 *
 * This module is its own file rather than an addition to `lib/api.ts` or `lib/platform-api.ts`:
 * several workstreams edit this frontend at once. Every type and function below mirrors
 * `apps/gateway/databridge_gateway/ontology_routes.py` field-for-field; when the two disagree,
 * `ontology_routes.py` is right and this file is the one to fix.
 *
 * Two write paths, on purpose:
 *   - OWNER edits (`defineKind`, `renameKind`, `setEndpoints`) apply directly and are audited —
 *     the founder's rule is that the owner defines the vocabulary and the system maps only to it;
 *   - AGENT suggestions (`proposeKind` with a non-owner origin) are proposals a human decides.
 * Identity-protected edits (subjects, relations touching subjects, determination/claim relations)
 * are refused with `IDENTITY_CONFIRMATION_REQUIRED` until re-sent with `confirm_identity: true`.
 *
 * All decisions live on the gateway. This file fetches and shapes; it never re-derives one.
 */
import { GATEWAY, authHeaders } from "./api";

export const ONTOLOGY_GATEWAY = GATEWAY;

// --- shapes, mirrored from ontology_routes.py -------------------------------------------------

/** One row of the closed meta-model (`databridge_graph.meta`), served so the frontend never keeps
 *  its own copy of which meta-kinds are identity-bearing, or what they mean. */
export interface NodeMetaKindInfo {
  value: string;
  /** Whether a pack may declare a node kind bound to this meta-kind a SUBJECT (spine D4b). */
  subject_eligible: boolean;
  /** Plain-English meaning, read from `databridge_graph.meta`'s own docstring. */
  explanation: string | null;
}

export interface EdgeMetaKindInfo {
  value: string;
  /** Binds a DETERMINATION — written only behind a named human attestation. */
  attested: boolean;
  /** Binds a CLAIM about which subject an artefact concerns. */
  claim: boolean;
  explanation: string | null;
}

export interface MetaKindCatalog {
  node: NodeMetaKindInfo[];
  edge: EdgeMetaKindInfo[];
}

export interface NodeKindView {
  kind: string;
  binds_to: string;
  description: string;
  cardinality: string;
  identity: string;
  source: "predefined" | "agent-proposed" | string;
  subject: boolean;
  attestation: string | null;
  identifiers: string[];
  grant_type: string | null;
  editable: boolean;
  /** Subject kinds: every change needs an explicit identity confirmation. */
  identity_protected: boolean;
  editable_via: string | null;
  pack: string;
  deprecated: boolean;
  deprecated_by: string | null;
  deprecated_at: string | null;
  deprecated_reason: string | null;
  /** Current nodes of this kind in the shared store; `null` when no store is attached. */
  count: number | null;
}

export interface EdgeKindView {
  kind: string;
  binds_to: string;
  from: string[];
  to: string[];
  description: string;
  source: "predefined" | "agent-proposed" | string;
  editable: boolean;
  identity_protected: boolean;
  editable_via: string | null;
  carries_identity_rule: boolean;
  pack: string;
  deprecated: boolean;
  deprecated_by: string | null;
  deprecated_at: string | null;
  deprecated_reason: string | null;
  count: number | null;
  /** Extractions refused because the endpoints did not fit; `null` when no ingest run exists. */
  refused_off_ontology: number | null;
}

export type ProposalStatus = "pending" | "escalated" | "approved" | "rejected" | "write_failed";

export interface OntologyProposalRecord {
  id: string;
  pack: string;
  axis: "node" | "edge";
  name: string;
  binds_to: string;
  src: string[];
  dst: string[];
  description: string;
  declares_subject: boolean;
  proposed_by: string;
  proposed_at: string;
  auto_approvable: boolean;
  guardrail_classes: string[];
  escalation_reason: string | null;
  decided_by: string | null;
  decided_at: string | null;
  status: ProposalStatus;
  origin?: string;
  auto_approve_bypassed?: string;
  auto_approve_was_enabled?: boolean;
  written_to?: string;
  write_error?: string;
  decision_reason?: string | null;
}

export interface AutoApproveState {
  enabled: boolean;
  set_by: string | null;
  set_at: string | null;
}

export interface IngestRefusals {
  available: boolean;
  runs: string[];
  edge_endpoint_refusals: Record<string, number>;
  unknown_node_kinds: Record<string, number>;
  unknown_edge_kinds: Record<string, number>;
  total: number;
}

export interface OntologyData {
  store: string | null;
  store_available: boolean;
  nodes_total: number;
  edges_total: number;
  kinds_without_data: string[];
  undeclared_in_store: { nodes: Record<string, number>; edges: Record<string, number> };
  ingest: IngestRefusals;
}

export interface OntologyView {
  pack: string;
  title: string;
  version: string;
  node_kinds: NodeKindView[];
  edge_kinds: EdgeKindView[];
  data: OntologyData;
  pending_proposals: OntologyProposalRecord[];
  auto_approve: AutoApproveState;
  meta_kinds: MetaKindCatalog;
  /** The attestation modes a subject may declare (from the pack loader). */
  attestation_modes: string[];
  auto_approve_never_covers: string[];
}

export interface OntologyPacksList {
  packs: string[];
  enabled: string[];
  default: string | null;
}

export interface EvidencePointerInput {
  source_id: string;
  kind: string;
  trust?: "trusted" | "untrusted";
  [key: string]: unknown;
}

export interface ProposeKindInput {
  pack?: string;
  axis: "node" | "edge";
  name: string;
  binds_to: string;
  src?: string[];
  dst?: string[];
  description?: string;
  proposed_by: string;
  declares_subject?: boolean;
  evidence?: EvidencePointerInput[];
  /** Anything but "owner" is never auto-applied (founder's rule). */
  origin?: "owner" | "agent" | "adapter_agent";
}

export interface DeprecateKindInput {
  pack?: string;
  axis: "node" | "edge";
  kind: string;
  by: string;
  reason?: string;
  confirm_identity?: boolean;
}

interface RefusalBody {
  refusal: {
    code: string;
    message: string;
    nearest_alternative?: string | null;
    [key: string]: unknown;
  };
}

/** Thrown for every non-2xx response. Carries the gateway's own refusal code and message verbatim
 *  — a refusal is shown, not swallowed into "something went wrong". */
export class OntologyApiError extends Error {
  code: string;
  status: number;
  /** Everything else the refusal carried (e.g. `identity_classes`). */
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "OntologyApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function call<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${ONTOLOGY_GATEWAY}${path}`, {
    ...init,
    signal,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body: unknown = await res.json().catch(() => ({}));
  if (body && typeof body === "object" && "refusal" in body) {
    const r = (body as RefusalBody).refusal;
    throw new OntologyApiError(res.status, r.code, r.message, r);
  }
  if (!res.ok) {
    throw new OntologyApiError(res.status, "UNKNOWN", `${path} → ${res.status}`);
  }
  return body as T;
}

// --- reads ---------------------------------------------------------------------------------

export function fetchOntology(pack: string, signal?: AbortSignal): Promise<OntologyView> {
  const qs = pack ? `?pack=${encodeURIComponent(pack)}` : "";
  return call<OntologyView>(`/ontology${qs}`, undefined, signal);
}

export function fetchOntologyPacks(signal?: AbortSignal): Promise<OntologyPacksList> {
  return call<OntologyPacksList>("/ontology/packs", undefined, signal);
}

// --- growth: propose, approve, reject ------------------------------------------------------

export function proposeKind(input: ProposeKindInput): Promise<OntologyProposalRecord> {
  return call<OntologyProposalRecord>("/ontology/proposals", {
    method: "POST",
    body: JSON.stringify({
      pack: input.pack,
      axis: input.axis,
      name: input.name,
      binds_to: input.binds_to,
      src: input.src ?? [],
      dst: input.dst ?? [],
      description: input.description ?? "",
      proposed_by: input.proposed_by,
      declares_subject: input.declares_subject ?? false,
      evidence: input.evidence ?? [],
      origin: input.origin ?? "agent",
    }),
  });
}

export function approveProposal(
  id: string,
  decided_by: string,
  reason?: string,
): Promise<OntologyProposalRecord> {
  return call<OntologyProposalRecord>(`/ontology/proposals/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({ decided_by, reason: reason ?? "" }),
  });
}

export function rejectProposal(
  id: string,
  decided_by: string,
  reason?: string,
): Promise<OntologyProposalRecord> {
  return call<OntologyProposalRecord>(`/ontology/proposals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ decided_by, reason: reason ?? "" }),
  });
}

export function setAutoApprove(
  enabled: boolean,
  set_by: string,
): Promise<{ auto_approve: AutoApproveState; never_covers: string[] }> {
  return call("/ontology/auto_approve", {
    method: "PUT",
    body: JSON.stringify({ enabled, set_by }),
  });
}

// --- removal: deprecate / reactivate --------------------------------------------------------

export interface DeprecateResult {
  pack: string;
  axis: "node" | "edge";
  kind: string;
  deprecated: boolean;
  deprecated_by?: string;
  deprecated_at?: string;
  reactivated_by?: string;
  reactivated_at?: string;
  note?: string;
}

export function deprecateKind(input: DeprecateKindInput): Promise<DeprecateResult> {
  return call<DeprecateResult>("/ontology/kinds/deprecate", {
    method: "POST",
    body: JSON.stringify({
      pack: input.pack,
      axis: input.axis,
      kind: input.kind,
      by: input.by,
      reason: input.reason ?? "",
      confirm_identity: input.confirm_identity ?? false,
    }),
  });
}

export function reactivateKind(input: DeprecateKindInput): Promise<DeprecateResult> {
  return call<DeprecateResult>("/ontology/kinds/reactivate", {
    method: "POST",
    body: JSON.stringify({
      pack: input.pack,
      axis: input.axis,
      kind: input.kind,
      by: input.by,
      reason: input.reason ?? "",
    }),
  });
}

// --- owner edits: applied directly, audited -------------------------------------------------

export interface SubjectDeclarationInput {
  identifiers: string[];
  attestation: string;
  grant_type: string;
}

export interface DefineKindInput {
  pack?: string;
  axis: "node" | "edge";
  name: string;
  binds_to: string;
  description?: string;
  src?: string[];
  dst?: string[];
  subject?: SubjectDeclarationInput | null;
  by?: string;
  reason?: string;
  confirm_identity?: boolean;
}

export interface OwnerEditResult {
  pack: string;
  kind: string;
  identity_classes: string[];
  written_to: string;
  reloaded: string[];
  vocabulary_generation: number;
  pack_bound: boolean;
  in_active_vocabulary?: boolean;
  new_name?: string;
  relations_widened?: string[];
}

export function defineKind(input: DefineKindInput): Promise<OwnerEditResult> {
  return call<OwnerEditResult>("/ontology/kinds", {
    method: "POST",
    body: JSON.stringify({
      pack: input.pack,
      axis: input.axis,
      name: input.name,
      binds_to: input.binds_to,
      description: input.description ?? "",
      src: input.src ?? [],
      dst: input.dst ?? [],
      subject: input.subject ?? null,
      by: input.by ?? "",
      reason: input.reason ?? "",
      confirm_identity: input.confirm_identity ?? false,
    }),
  });
}

export interface RenameKindInput {
  pack?: string;
  axis: "node" | "edge";
  kind: string;
  new_name: string;
  carry_relations?: boolean;
  by?: string;
  reason?: string;
  confirm_identity?: boolean;
}

export function renameKind(input: RenameKindInput): Promise<OwnerEditResult> {
  return call<OwnerEditResult>("/ontology/kinds/rename", {
    method: "POST",
    body: JSON.stringify({
      pack: input.pack,
      axis: input.axis,
      kind: input.kind,
      new_name: input.new_name,
      carry_relations: input.carry_relations ?? true,
      by: input.by ?? "",
      reason: input.reason ?? "",
      confirm_identity: input.confirm_identity ?? false,
    }),
  });
}

export interface SetEndpointsInput {
  pack?: string;
  kind: string;
  src: string[];
  dst: string[];
  by?: string;
  reason?: string;
  confirm_identity?: boolean;
}

export function setEndpoints(input: SetEndpointsInput): Promise<OwnerEditResult> {
  return call<OwnerEditResult>("/ontology/kinds/endpoints", {
    method: "POST",
    body: JSON.stringify({
      pack: input.pack,
      kind: input.kind,
      src: input.src,
      dst: input.dst,
      by: input.by ?? "",
      reason: input.reason ?? "",
      confirm_identity: input.confirm_identity ?? false,
    }),
  });
}

export interface AuditEntry {
  at: string;
  action: string;
  pack: string | null;
  axis?: string;
  kind?: string;
  new_name?: string;
  principal: string;
  by?: string | null;
  reason?: string | null;
  identity_confirmed?: boolean;
  [key: string]: unknown;
}

export function fetchAudit(
  pack: string,
  signal?: AbortSignal,
): Promise<{ path: string; entries: AuditEntry[] }> {
  return call(`/ontology/audit?pack=${encodeURIComponent(pack)}&limit=50`, undefined, signal);
}

/** The identity classes an edit was refused for, when the refusal is the one an explicit human
 *  confirmation lifts; `null` for any other error. */
export function identityClassesOf(err: unknown): string[] | null {
  if (err instanceof OntologyApiError && err.code === "IDENTITY_CONFIRMATION_REQUIRED") {
    const c = err.details.identity_classes;
    return Array.isArray(c) ? c.map(String) : [];
  }
  return null;
}

// --- derived, read-only helpers used by the UI ----------------------------------------------

/** Whether this edge meta-kind is one of the identity-bearing ones. */
export function isIdentityBearingEdgeMeta(catalog: MetaKindCatalog, metaValue: string): boolean {
  const m = catalog.edge.find((e) => e.value === metaValue);
  return !!m && (m.attested || m.claim);
}
