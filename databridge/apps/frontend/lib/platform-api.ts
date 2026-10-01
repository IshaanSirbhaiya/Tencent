/**
 * The seam between `/trace` and the gateway's trace recorder.
 *
 * There is NO offline fallback. This module used to fall back to `config/platform-preview.json`
 * when the route was unreachable or held nothing, and render that invented trace under a notice.
 * A plausible trace is still a trace nobody ran, so the fallback is gone (ROADMAP §3 item 18):
 * an empty recorder is `state: "empty"`, an unreachable or refusing gateway is `state: "error"`,
 * and only `state: "live"` carries a trace. The preview JSON stays on disk, unimported here.
 *
 * The ontology half that lived here (`fetchOntology` / `previewOntology`, with the same fallback)
 * was already superseded by `lib/ontology-api.ts` and had no importer; it is removed.
 */
import { GATEWAY, authHeaders } from "./api";
import type {
  Tone,
  TraceEdge,
  TraceNode,
  TracePointerProvenance,
  TracePreview,
} from "./platform-preview";
import { adaptGatewayTrace, type GatewayTrace, type GatewayTraceSummary } from "./trace-adapt";

/** Tailwind text colour per tone — the same values as `platform-preview.ts`'s `TONE_TEXT`, held
 *  here so `/trace` does not import the module that carries the preview JSON. */
export const TONE_TEXT: Record<Tone, string> = {
  good: "text-band-auto",
  warn: "text-band-spot",
  bad: "text-band-review",
  neutral: "text-fg",
};

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, { cache: "no-store", signal, headers: authHeaders() });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const refusal = (body as { refusal?: { code?: string; message?: string } }).refusal;
    throw new Error(refusal?.code ? `${refusal.code}: ${refusal.message ?? ""}` : `${path} → ${res.status}`);
  }
  return body as T;
}

// ---------------------------------------------------------------------------------------------
// /trace
// ---------------------------------------------------------------------------------------------

/**
 * The communication graph, when the response carried counts but no diagram.
 *
 * Only used for a recorder payload that carries counts but no nodes/edges (`adaptGatewayTrace`
 * normally projects the steps itself). The sequence is the fixed one in
 * `AGENT_PLANE.md` §7; the numbers on it are read from the payload and never guessed.
 */
function deriveTraceGraph(t: TracePreview): { nodes: TraceNode[]; edges: TraceEdge[] } {
  const graphBranch = t.branches.find((b) => b.kind === "graph");
  const adapterBranch = t.branches.find((b) => b.kind === "adapter");
  const grounded = t.results.find((r) => r.kind === "grounded");
  const withheld = t.results.find((r) => r.kind === "withheld");

  const row = (kind: string, label: string) =>
    (kind === "graph" ? graphBranch : adapterBranch)?.rows.find((r) => r.label === label)?.value ??
    null;

  const blank = (summary: string) => ({
    summary,
    requested: null,
    decided: null,
    pulled: null,
    cost: null,
    notes: [] as string[],
  });

  const nodes: TraceNode[] = [
    {
      id: "agent",
      kind: "agent",
      label: t.request.agent,
      sublabel: t.request.version,
      col: 1,
      row: 0,
      detail: blank("The declared agent that asked."),
    },
    {
      id: "orchestrator",
      kind: "orchestrator",
      label: "ORCHESTRATOR",
      sublabel: "main agent",
      col: 1,
      row: 1,
      detail: blank("Checks the grant covers the capability and the purpose, then admits against budget."),
    },
    {
      id: "graph",
      kind: "graph",
      label: "GRAPH",
      sublabel: graphBranch?.subtitle ?? "",
      col: 0,
      row: 2,
      detail: blank("Stores pointers, never values."),
    },
    {
      id: "adapter",
      kind: "adapter",
      label: "ADAPTER",
      sublabel: adapterBranch?.subtitle ?? "",
      col: 2,
      row: 2,
      detail: blank("Policy, then ledger, then core — in that order."),
    },
    {
      id: "answer",
      kind: "answer",
      label: "ANSWER",
      sublabel: `${t.answer.citations} citations`,
      col: 1,
      row: 3,
      detail: blank("Per-field citations and a calibrated risk certificate."),
    },
  ];

  const edges: TraceEdge[] = [
    {
      id: "d1",
      step: 1,
      from: "agent",
      to: "orchestrator",
      kind: "request",
      label: "task + Principal",
      detail: blank(
        `purpose ${t.request.purpose ?? "not recorded"} · on_behalf_of ${t.request.on_behalf_of ?? "not recorded"}`,
      ),
    },
    {
      id: "d2",
      step: 2,
      from: "orchestrator",
      to: "graph",
      kind: "request",
      label: graphBranch?.subtitle ?? "as_of(valid, system)",
      detail: blank("Ask the graph first."),
    },
    {
      id: "d3",
      step: 3,
      from: "graph",
      to: "orchestrator",
      kind: "rows",
      label: `${row("graph", "pointers resolved") ?? "?"} pointers · ${
        row("graph", "values stored") ?? "?"
      } values`,
      detail: blank(
        `${row("graph", "answered from history") ?? "?"} answered from history · ${
          row("graph", "needed live bytes") ?? "?"
        } needed live bytes`,
      ),
    },
    {
      id: "d4",
      step: 4,
      from: "orchestrator",
      to: "adapter",
      kind: "request",
      label: "CapabilityRequest(resolve)",
      detail: blank("Only what history could not answer is sent, and it is sent as pointers."),
    },
    {
      id: "d5",
      step: 5,
      from: "adapter",
      to: "adapter",
      kind: "policy",
      label: "POLICY screen",
      detail: blank(row("adapter", "POLICY applicant_notes") ?? "grant · purpose · budget · egress"),
    },
    {
      id: "d6",
      step: 6,
      from: "adapter",
      to: "orchestrator",
      kind: "rows",
      label: `${grounded?.count ?? 0} grounded + ${withheld?.count ?? 0} withheld`,
      detail: blank(
        [grounded?.detail, withheld ? `withheld: ${withheld.detail}` : null]
          .filter(Boolean)
          .join(" · "),
      ),
    },
    {
      id: "d7",
      step: 7,
      from: "orchestrator",
      to: "answer",
      kind: "answer",
      label: `${t.answer.citations} citations`,
      detail: blank(
        t.answer.admitted === null
          ? "Admission not recorded."
          : t.answer.admitted
            ? "Inside the risk budget — admitted."
            : "Outside the risk budget — refused.",
      ),
    },
  ];

  return { nodes, edges };
}

export interface TraceGraphData {
  trace: TracePreview;
  nodes: TraceNode[];
  edges: TraceEdge[];
  provenance: TracePointerProvenance | null;
  /** True when the payload carried no nodes/edges and the sequence was reconstructed here. */
  derived: boolean;
}

/** Normalise whatever the endpoint returned into something the graph can draw. */
export function normaliseTrace(t: TracePreview): TraceGraphData {
  const hasGraph = Array.isArray(t.nodes) && t.nodes.length > 0 && Array.isArray(t.edges);
  const { nodes, edges } = hasGraph
    ? { nodes: t.nodes as TraceNode[], edges: t.edges as TraceEdge[] }
    : deriveTraceGraph(t);
  return {
    trace: t,
    nodes,
    edges: [...edges].sort((a, b) => a.step - b.step),
    provenance: t.pointer_provenance ?? null,
    derived: !hasGraph,
  };
}

export interface TraceListRow {
  request_id: string;
  surface: string;
  agent: string;
  started_at: string;
  outcome: string | null;
  complete: boolean;
}

/** What `/trace` can show. Only `live` carries a trace; the other two say why there is none. */
export type TraceResult =
  | {
      state: "live";
      trace: TracePreview;
      endpoint: string;
      /** Every trace the recorder currently holds, newest first. */
      available: TraceListRow[];
    }
  | { state: "empty"; endpoint: string; reason: string }
  | { state: "error"; endpoint: string; reason: string };

/**
 * Fetch one trace.
 *
 * `GET /trace` never seeds and returns `{"traces": []}` when nothing has run — the true answer,
 * rendered as "no requests recorded yet", never as an example trace.
 */
export async function fetchTrace(requestId?: string, signal?: AbortSignal): Promise<TraceResult> {
  const endpoint = "GET /trace/{request_id}";
  try {
    const list = await getJson<{ traces: GatewayTraceSummary[] }>("/trace?limit=25", signal);
    const rows: TraceListRow[] = (list.traces ?? []).map((t) => ({
      request_id: t.request_id,
      surface: t.surface,
      agent: t.agent,
      started_at: t.started_at,
      outcome: t.outcome,
      complete: t.integrity?.complete ?? true,
    }));
    if (!rows.length) {
      return {
        state: "empty",
        endpoint,
        reason:
          "the gateway is up and its recorder holds no traces — a trace is recorded when a request runs, and is never seeded",
      };
    }
    const wanted = requestId && rows.some((r) => r.request_id === requestId)
      ? requestId
      : rows[0].request_id;
    const detail = await getJson<GatewayTrace>(`/trace/${encodeURIComponent(wanted)}`, signal);
    return { state: "live", trace: adaptGatewayTrace(detail, endpoint), endpoint, available: rows };
  } catch (err) {
    if ((err as Error)?.name === "AbortError") throw err;
    return {
      state: "error",
      endpoint,
      reason: err instanceof Error ? err.message : "gateway unreachable",
    };
  }
}
