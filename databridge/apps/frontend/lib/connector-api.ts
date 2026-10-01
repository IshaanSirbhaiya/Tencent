// `/connectors/*` gateway client — the Connector Builder.
//
// A separate module from `mcp-api.ts` and `verify-api.ts` on purpose (three frontend workstreams
// share this app). This one is the configure-by-search flow: search a vendored catalogue, choose
// read or write, attach Skill selections, add per-source extraction context — then compile the
// result through the SAME loader `/builder` uses. Every function returns a `Result<T>` for a GET
// and a plain compile result for the POST, matching the shape `mcp-api.ts` already established:
// **"cannot reach the gateway" must never render as "nothing found"** — a caller cannot
// accidentally collapse the two, because there is no shape here that lets it.
import { GATEWAY, authHeaders } from "./api";
import type { Refusal } from "./types";

export type CatalogueStatus = "vendored" | "not_yet_vendored";

export interface CatalogueEntry {
  id: string;
  name: string;
  vendor: string;
  summary: string;
  reads: string[];
  pack: string | null;
  status: CatalogueStatus;
  tags: string[];
  /** Present only when `status === "vendored"`. */
  vendored_path?: string;
  template?: string;
  /** Present only when `status === "not_yet_vendored"`. */
  not_vendored_reason?: string;
  /** Read off the REVIEWED template, not hand-typed — what the MCP config modal shows. Empty /
   *  null for `not_yet_vendored`, honestly, since there is no template to read yet. */
  tools_exposed: string[];
  transport: string | null;
  sandbox: string | null;
  digest?: string | null;
  declaration_digest?: string | null;
}

export interface CatalogueResponse {
  servers: CatalogueEntry[];
  counts: { vendored: number; not_yet_vendored: number; total: number };
}

export interface Skill {
  id: string;
  skill: string;
  purpose: string;
}

export interface SkillsResponse {
  skills: Skill[];
  note: string;
}

export type Mode = "read" | "write";

export interface ComposeRequest {
  server_id: string;
  mode: Mode;
  skills: string[];
  extraction_context: string;
  /** Step 3, the MCP config modal's env/credential profile — NAMES only, never a value. Lands on
   *  the real, validated `mcp.env` field. */
  env_names?: string[];
  /** Step 7 — declared `AdapterShell` configuration. Recorded, not wired to a live Shell. */
  agent_row_limit?: number;
  agent_named_queries?: string;
  name?: string;
}

/** The four headings `/builder`'s adapter pane already renders — reused so the two surfaces speak
 *  one vocabulary about what a compiled adapter IS. */
export interface CompiledConnector {
  source_id: string;
  title: string | null;
  estate: string | null;
  grants: { read: string[]; write: string[]; note: string };
  sink_policy: { egress_allow: string[]; egress_class: string | null; note: string };
  pinning: { tier: string; capture: unknown; note: string | null; deletes_detectable: string | null };
  projection: { entity: string | null; key: string[]; fields: string[] };
  trust: { field: string; trust: string; justification: string | null; note: string | null; redaction: string[] }[];
  server: Record<string, string | null>;
  verification: unknown;
}

export interface CompileRefusal {
  code: string;
  kind: string;
  message: string;
  stage: "catalogue" | "compile" | string;
}

export interface CompileResult {
  compiled: boolean;
  declaration: string | null;
  refusal: CompileRefusal | null;
  compiles_to?: CompiledConnector;
  connector?: {
    mode_requested: Mode;
    skills: string[];
    extraction_context: string;
    env_names?: string[];
    agent?: { row_limit: number; named_queries: string };
  };
}

/** Step 1 — "see the adapters that already exist." Shipped declarations, `/builder`'s own list. */
export interface ExistingAdapter {
  id: string;
  path: string;
  bytes: number;
  pack: string;
}

export interface ExistingAdaptersResponse {
  declarations: ExistingAdapter[];
  default: string | null;
}

export type Result<T> = { ok: true; data: T } | { ok: false; refusal: Refusal };

function isRefusal(value: unknown): value is { refusal: Refusal } {
  return typeof value === "object" && value !== null && "refusal" in value;
}

async function get<T>(path: string): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, { cache: "no-store", headers: authHeaders() });
  } catch (err) {
    return {
      ok: false,
      refusal: {
        code: "GATEWAY_UNREACHABLE",
        message: `The gateway at ${GATEWAY} did not answer (${(err as Error).message}).`,
        nearest_alternative: "start it with databridge/start-databridge.ps1",
      },
    };
  }
  const body = await res.json().catch(() => null);
  if (isRefusal(body)) return { ok: false, refusal: body.refusal };
  if (!res.ok) return { ok: false, refusal: { code: `HTTP_${res.status}`, message: `${path} → ${res.status}` } };
  return { ok: true, data: body as T };
}

/**
 * Compile a connector. **Always resolves**, never throws: a refusal — whether a pre-validation
 * one (422) or the loader's own (200, `compiled: false`) — is rendered by the caller, not caught
 * as an exception. Only a transport failure (the gateway itself unreachable) produces the
 * synthetic `GATEWAY_UNREACHABLE` shape, packaged into the same `CompileResult` so the page has
 * exactly one branch to render instead of two.
 */
export async function compileConnector(req: ComposeRequest): Promise<CompileResult> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}/connectors/compile`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(req),
      cache: "no-store",
    });
  } catch (err) {
    return {
      compiled: false,
      declaration: null,
      refusal: {
        code: "GATEWAY_UNREACHABLE",
        kind: "TransportError",
        stage: "compile",
        message: `The gateway at ${GATEWAY} did not answer (${(err as Error).message}).`,
      },
    };
  }
  const body = await res.json().catch(() => ({}));
  if (isRefusal(body) && !("compiled" in body)) {
    // A pre-validation refusal (unknown server, unknown skill, oversized context): the route
    // answers 404/422 with `{refusal: {...}}` rather than the compile envelope, because it never
    // reached the loader at all.
    const r = body.refusal as { code: string; message: string };
    return {
      compiled: false,
      declaration: null,
      refusal: { code: r.code, kind: "RequestRefused", stage: "request", message: r.message },
    };
  }
  return body as CompileResult;
}

export const connectorApi = {
  catalogue: (q = "") => get<CatalogueResponse>(`/connectors/catalogue${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  entry: (id: string) => get<CatalogueEntry>(`/connectors/catalogue/${encodeURIComponent(id)}`),
  skills: () => get<SkillsResponse>("/connectors/skills"),
  compile: compileConnector,
  /** Step 1's "adapters that already exist" — the SAME shipped-declaration list `/builder`'s
   *  Adapter Builder pane opens with, reused so the two surfaces agree on what an adapter is. */
  existingAdapters: () => get<ExistingAdaptersResponse>("/builder/adapter/declarations"),
};
