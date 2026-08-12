// `/mcp/*` gateway client — the Connectors surface (T-44).
//
// This file is the reason `/connectors` stopped being a wall of logos. Every call here lands on a
// real MCPJungle aggregator through the gateway, so the page renders what is actually registered,
// and connecting a server actually registers it.
//
// The one distinction the types are shaped to protect: a **READ** server joins the aggregator the
// agent queries; an **ACT** server is recorded for the model-free dispatcher and is never
// registered there at all (`docs/hackathon/AGENT_CAPABILITIES.md`). `agent_reachable` is therefore
// a field the backend sends, never something this file infers from `kind` — if those two ever
// disagree, the page must show the disagreement rather than paper over it with a derived boolean.
//
// The second thing the types protect: **"cannot reach the registry" is not "nothing is
// connected"**. Every read returns `Ok<T> | Refusal`, so a caller cannot accidentally render a
// down aggregator as an empty list — there is no shape here that lets it.
import { GATEWAY } from "./api";
import type { Refusal } from "./types";

export type ServerKind = "read" | "act";

export interface McpTool {
  name: string; // fully qualified: `<server>__<tool>`
  server: string | null;
  tool: string; // the bare name the model sees
  description: string;
  input_schema: Record<string, unknown>;
  enabled: boolean;
  kind: ServerKind;
  /** Whether the "try it" button may call this. False for ACT and for writer-process tools. */
  invocable: boolean;
}

export interface McpServer {
  name: string;
  kind: ServerKind;
  url: string;
  description: string;
  transport: string;
  enabled: boolean;
  /** Sent by the backend. READ servers are in the aggregator; ACT servers never are. */
  agent_reachable: boolean;
  in_aggregator: boolean;
  /** False for ACT servers: we never open a session with an act-capable endpoint from here. */
  tools_known: boolean;
  tools: McpTool[];
  recorded_at: string | null;
  /** ACT-recorded AND present in the aggregator — the boundary broken out-of-band. */
  boundary_violation: boolean;
}

export interface ServersResponse {
  registry: { url: string; reachable: boolean };
  servers: McpServer[];
  counts: { read: number; act: number; tools: number };
  boundary_violations: string[];
  /** The consequence of each choice, in the backend's own words — rendered verbatim by the form. */
  kind_consequence: Record<ServerKind, string>;
  checked_at: string;
}

export interface ToolsResponse {
  registry: { url: string; reachable: boolean };
  tools: McpTool[];
  note: string;
  checked_at: string;
}

export interface StatusResponse {
  registry: { url: string; reachable: boolean; status: string | null; detail: string | null };
  reader: {
    url: string;
    reachable: boolean;
    server_info: { name?: string; version?: string } | null;
    detail: string | null;
  };
  act_recorded: number;
  checked_at: string;
}

export interface ConnectResponse {
  server: McpServer;
  kind: ServerKind;
  registered_into_aggregator: boolean;
  consequence: string;
}

export interface InvokeResponse {
  tool: string;
  server: string | null;
  arguments: Record<string, unknown>;
  /** The aggregator's own verdict, relayed. A tool that rejected its arguments answers HTTP 200
   *  with this set — flattening it into a success would be the page lying about the call. */
  is_error: boolean;
  content: { type?: string; text?: string }[];
  text: string;
  raw: unknown;
  duration_ms: number;
  invoked_at: string;
}

/** Either the answer, or a typed refusal rendered verbatim. Never an ambiguous empty result. */
export type Result<T> = { ok: true; data: T } | { ok: false; refusal: Refusal };

export function isRefusal(value: unknown): value is { refusal: Refusal } {
  return typeof value === "object" && value !== null && "refusal" in value;
}

async function call<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch (err) {
    // The gateway itself is down — distinct again from the aggregator being down, and said so.
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
  if (!res.ok) {
    return {
      ok: false,
      refusal: { code: `HTTP_${res.status}`, message: `${path} → ${res.status}` },
    };
  }
  return { ok: true, data: body as T };
}

export interface ConnectRequest {
  name: string;
  url: string;
  description: string;
  kind: ServerKind;
}

export const mcpApi = {
  status: () => call<StatusResponse>("/mcp/status"),
  servers: () => call<ServersResponse>("/mcp/servers"),
  tools: () => call<ToolsResponse>("/mcp/tools"),
  connect: (body: ConnectRequest) =>
    call<ConnectResponse>("/mcp/servers", { method: "POST", body: JSON.stringify(body) }),
  disconnect: (name: string) =>
    call<{ disconnected: string; kind: ServerKind; was_in_aggregator: boolean }>(
      `/mcp/servers/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  invoke: (name: string, args: Record<string, unknown>) =>
    call<InvokeResponse>(`/mcp/tools/${encodeURIComponent(name)}/invoke`, {
      method: "POST",
      body: JSON.stringify(args),
    }),
};

/** The required argument names out of a tool's JSON Schema, for the "try it" form.
 *  Read from the schema the aggregator published — never guessed from the tool name. */
export function schemaFields(schema: Record<string, unknown>): {
  name: string;
  type: string;
  required: boolean;
}[] {
  const properties = (schema?.properties ?? {}) as Record<string, { type?: string; anyOf?: { type?: string }[] }>;
  const required = new Set((schema?.required as string[] | undefined) ?? []);
  return Object.entries(properties).map(([name, spec]) => ({
    name,
    type: spec?.type ?? spec?.anyOf?.find((a) => a.type && a.type !== "null")?.type ?? "string",
    required: required.has(name),
  }));
}
