"use client";
/**
 * An agent run's traversal of the graph, drawn on `/graph` (`/graph?run=<agent>/<run_id>`).
 *
 * Source: the run record (`GET /agents/{agent}/runs/{run_id}`, §5.7/§5.8). Each step carries the
 * tool name, its ARGUMENTS, the pointers it produced and `produced_by` — but NOT the node ids the
 * tool returned (the observation is shown to the planner and not persisted). So this view draws
 * exactly what the record lets it draw, and says what it cannot:
 *
 *  - `graph.node` / `graph.neighbors` — the node id is an argument: drawn, numbered by step order.
 *    For `neighbors` the neighbourhood is read from the store AS IT IS NOW (said on the step).
 *  - `graph.path` — the two ends are arguments; the path between them is RECOMPUTED now against the
 *    current store and labelled as recomputed, because the record does not say which edges the
 *    agent walked.
 *  - `graph.search` — only the query is recorded. The hits are not, so nothing is drawn for it and
 *    the step says so. Re-running the search today would draw today's answer under the agent's name.
 *
 * Every id and label on screen comes from a gateway response; nothing is inferred from a pointer.
 */
import * as React from "react";
import Link from "next/link";
import { AlertTriangle, Bot, X } from "lucide-react";
import {
  describeGraphError,
  graphApi,
  readAgentSteps,
  stepMeasures,
  type AgentRunDetail,
  type AgentRunStep,
  type GraphEdgeSummary,
  type GraphNodeDetail,
  type GraphNodeSummary,
  type ProducedBy,
} from "@/lib/graph-api";
import { isRefusal } from "@/lib/intake-types";
import { resolveLinks, type TrailHighlight, type TrailStep } from "./trail";

export interface AgentGraphStep {
  /** 1-based position among the run's GRAPH steps — the number drawn on the canvas. */
  ordinal: number;
  /** 1-based position among ALL the run's steps. */
  runIndex: number;
  tool: string;
  op: string;
  args: Record<string, unknown>;
  nodeIds: string[];
  edgeIds: string[];
  producedBy: ProducedBy | null;
  /** null = the record does not carry it (unknown), never 0 by default. */
  ms: number | null;
  pointers: number | null;
  /** What this step could not show, stated. */
  note: string | null;
}

export interface AgentTraversal {
  agent: string;
  runId: string;
  status: string;
  started: string;
  modelsUsed: string[];
  totalSteps: number;
  otherTools: string[];
  steps: AgentGraphStep[];
  nodes: GraphNodeSummary[];
  edges: GraphEdgeSummary[];
}

export type AgentTraversalResult =
  { ok: true; traversal: AgentTraversal } | { ok: false; message: string };

const NEIGHBOR_LIMIT = 60;

function str(v: unknown): string {
  return typeof v === "string"
    ? v
    : v === undefined || v === null
      ? ""
      : String(v);
}

function summaryOf(d: GraphNodeDetail): GraphNodeSummary {
  return {
    id: d.id,
    kind: d.kind,
    label: d.label,
    canonical_id: d.canonical_id,
    valid_from: d.valid_from,
    valid_to: d.valid_to,
    system_from: d.system_from,
    system_to: d.system_to,
    is_open: d.is_open,
    attr_count: Object.keys(d.attrs).length,
    unresolved_count: d.unresolved.length,
  };
}

/** `agent/run_id` → the two parts, or null. */
export function parseRunParam(
  run: string,
): { agent: string; runId: string } | null {
  const i = run.indexOf("/");
  if (i <= 0 || i === run.length - 1) return null;
  return { agent: run.slice(0, i), runId: run.slice(i + 1) };
}

export async function loadAgentTraversal(
  run: string,
): Promise<AgentTraversalResult> {
  const parts = parseRunParam(run);
  if (!parts) {
    return {
      ok: false,
      message: `"${run}" is not a run reference — expected <agent>/<run_id>.`,
    };
  }
  let detail: AgentRunDetail;
  try {
    const res = await graphApi.agentRun(parts.agent, parts.runId);
    if (isRefusal(res))
      return {
        ok: false,
        message: `${res.refusal.code}: ${res.refusal.message}`,
      };
    detail = res;
  } catch (err) {
    return { ok: false, message: describeGraphError(err) };
  }
  const record = detail.record;
  const read = readAgentSteps(record);
  if (!read.ok) return { ok: false, message: `${parts.agent}/${parts.runId}: ${read.message}` };
  const allSteps: AgentRunStep[] = read.steps;
  const nodes = new Map<string, GraphNodeSummary>();
  const edges = new Map<string, GraphEdgeSummary>();
  const steps: AgentGraphStep[] = [];
  const otherTools: string[] = [];

  const loadNode = async (id: string): Promise<string | null> => {
    if (nodes.has(id)) return null;
    const res = await graphApi.node(id);
    if (isRefusal(res)) return `${id}: ${res.refusal.message}`;
    nodes.set(res.id, summaryOf(res));
    return null;
  };

  let ordinal = 0;
  for (let i = 0; i < allSteps.length; i += 1) {
    const s = allSteps[i];
    const [family, op = ""] = String(s.tool).split(".", 2);
    if (family !== "graph") {
      otherTools.push(s.tool);
      continue;
    }
    ordinal += 1;
    const args = s.args ?? {};
    const step: AgentGraphStep = {
      ordinal,
      runIndex: i + 1,
      tool: s.tool,
      op,
      args,
      nodeIds: [],
      edgeIds: [],
      producedBy: s.produced_by ?? null,
      ...stepMeasures(s),
      note: null,
    };
    try {
      if (op === "node" || op === "neighbors") {
        const id = str(args.node_id ?? args.id);
        if (!id) {
          step.note = "the step recorded no node id";
        } else if (op === "node") {
          const missing = await loadNode(id);
          if (missing) step.note = `not in the store now — ${missing}`;
          else step.nodeIds = [id];
        } else {
          const depth = Math.max(1, Math.min(Number(args.depth ?? 1) || 1, 2));
          const res = await graphApi.neighborhood({
            node: id,
            depth,
            limit: NEIGHBOR_LIMIT,
          });
          if (isRefusal(res)) {
            step.note = `not in the store now — ${res.refusal.message}`;
          } else {
            res.nodes.forEach((n) => nodes.set(n.id, n));
            res.edges.forEach((e) => edges.set(e.id, e));
            step.nodeIds = [id];
            step.note =
              "the neighbourhood is read from the store as it is now; the run record keeps the " +
              "node asked about, not what came back" +
              (res.truncated ? " (cut at the window)" : "");
          }
        }
      } else if (op === "path") {
        const src = str(args.src ?? args.src_id);
        const dst = str(args.dst ?? args.dst_id);
        if (!src || !dst) {
          step.note = "the step recorded no path ends";
        } else {
          const res = await graphApi.path(src, dst);
          if (isRefusal(res)) {
            step.note = res.refusal.message;
          } else if (!res.found) {
            await loadNode(src);
            await loadNode(dst);
            step.nodeIds = [src, dst].filter((id) => nodes.has(id));
            step.note = `recomputed now: ${res.message ?? res.reason ?? "no path"}`;
          } else {
            res.nodes.forEach((n) => nodes.set(n.id, n));
            res.edges.forEach((e) => edges.set(e.id, e));
            step.nodeIds = res.nodes.map((n) => n.id);
            step.edgeIds = res.edges.map((e) => e.id);
            step.note =
              "path RECOMPUTED now against the current store — the run record keeps the two ends, " +
              "not the edges the agent walked";
          }
        }
      } else if (op === "search") {
        step.note =
          "only the query is recorded — the run record does not keep which nodes the search " +
          "returned, so none are drawn for this step";
      } else {
        step.note = `graph.${op} is not a graph operation this view knows`;
      }
    } catch (err) {
      step.note = describeGraphError(err);
    }
    steps.push(step);
  }

  return {
    ok: true,
    traversal: {
      agent: detail.agent,
      runId: record.run_id,
      status: record.status,
      started: record.started,
      modelsUsed: record.models_used ?? [],
      totalSteps: allSteps.length,
      otherTools,
      steps,
      nodes: Array.from(nodes.values()),
      edges: Array.from(edges.values()),
    },
  };
}

/** The agent's visits as a trail, numbered by GRAPH-step order (a path's hops share its number).
 *  Only edges a step itself named (a recomputed path's hops) are highlighted; between steps no edge
 *  is inferred from canvas adjacency (`resolveTrailLinks`). */
export function agentHighlight(
  t: AgentTraversal,
  canvasEdges: Map<string, GraphEdgeSummary>,
): TrailHighlight {
  const visits: TrailStep[] = [];
  const ordinals: number[] = [];
  for (const s of t.steps) {
    s.nodeIds.forEach((id, j) => {
      visits.push({
        nodeId: id,
        action: "path",
        edgeId: j > 0 ? s.edgeIds[j - 1] : undefined,
      });
      ordinals.push(s.ordinal);
    });
  }
  const links = resolveLinks(visits, canvasEdges);
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  const numbers = new Map<string, number[]>();
  visits.forEach((v, i) => {
    nodeIds.add(v.nodeId);
    const prev = numbers.get(v.nodeId) ?? [];
    if (!prev.includes(ordinals[i]))
      numbers.set(v.nodeId, [...prev, ordinals[i]]);
    if (i > 0 && links[i - 1]?.edgeId)
      edgeIds.add(links[i - 1].edgeId as string);
  });
  return { nodeIds, edgeIds, numbers };
}

function producedByText(p: ProducedBy | null): string {
  if (!p) return "producer not recorded";
  // `unknown`: nobody recorded who produced it — said as unknown, never as a rule
  if (p.by === "unknown" && !p.model) return [p.agent, "unknown"].filter(Boolean).join(" · ");
  const who = [p.agent, p.agent_version ? `v${p.agent_version}` : null]
    .filter(Boolean)
    .join(" ");
  return [who || "agent not recorded", p.model ?? "model not recorded"].join(
    " · ",
  );
}

function argsText(s: AgentGraphStep): string {
  if (s.op === "search") return `“${str(s.args.query ?? s.args.q)}”`;
  if (s.op === "path")
    return `${str(s.args.src ?? s.args.src_id)} → ${str(s.args.dst ?? s.args.dst_id)}`;
  return str(s.args.node_id ?? s.args.id);
}

export function AgentTraversalPanel({
  run,
  result,
  loading,
  labelOf,
  onFocus,
}: {
  run: string;
  result: AgentTraversalResult | undefined;
  loading: boolean;
  labelOf: (id: string) => string;
  onFocus: (id: string) => void;
}) {
  return (
    <div
      className="flex flex-col gap-2 rounded border border-accent/40 bg-accent/[0.04] px-3 py-2 text-2xs"
      data-testid="graph-agent-traversal"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Bot className="h-3.5 w-3.5 text-accent" />
        <span className="font-medium text-fg">Agent traversal</span>
        <span className="font-mono text-subtle">{run}</span>
        <Link
          href="/graph"
          className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-subtle hover:bg-muted hover:text-fg"
        >
          <X className="h-3 w-3" /> leave agent view
        </Link>
      </div>
      {loading && <p className="text-subtle">Loading the run record…</p>}
      {result && !result.ok && (
        <p
          className="flex items-start gap-1.5 text-band-review"
          role="alert"
          data-testid="graph-agent-error"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{" "}
          {result.message}
        </p>
      )}
      {result?.ok && (
        <>
          <p className="text-subtle" data-testid="graph-agent-summary">
            {result.traversal.agent} · run {result.traversal.runId} ·{" "}
            {result.traversal.status} · {result.traversal.steps.length} graph
            step(s) of {result.traversal.totalSteps}
            {result.traversal.modelsUsed.length
              ? ` · models: ${result.traversal.modelsUsed.join(", ")}`
              : ""}
            {result.traversal.otherTools.length
              ? ` · other tools (not drawn): ${Array.from(new Set(result.traversal.otherTools)).join(", ")}`
              : ""}
          </p>
          {result.traversal.steps.length === 0 ? (
            <p className="text-fg" data-testid="graph-agent-empty">
              This run made no graph tool calls, so there is no traversal to
              draw.
            </p>
          ) : (
            <ol className="flex flex-col gap-1">
              {result.traversal.steps.map((s) => (
                <li
                  key={s.runIndex}
                  className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded border border-border bg-surface px-2 py-1"
                  data-testid="graph-agent-step"
                >
                  <span className="tnum rounded-full bg-accent px-1.5 font-mono text-[10px] text-white">
                    {s.ordinal}
                  </span>
                  <span className="font-mono text-fg">{s.tool}</span>
                  <span
                    className="max-w-[20rem] truncate font-mono text-subtle"
                    title={argsText(s)}
                  >
                    {argsText(s)}
                  </span>
                  {s.nodeIds.length > 0 && (
                    <button
                      type="button"
                      onClick={() => onFocus(s.nodeIds[0])}
                      className="text-accent hover:underline"
                    >
                      {s.nodeIds.length === 1
                        ? labelOf(s.nodeIds[0])
                        : `${s.nodeIds.length} nodes`}
                    </button>
                  )}
                  <span className="text-subtle">
                    ·{" "}
                    {s.pointers === null
                      ? "pointers not recorded"
                      : `${s.pointers} pointer${s.pointers === 1 ? "" : "s"}`}{" "}
                    · {s.ms === null ? "— ms" : `${s.ms} ms`}
                  </span>
                  <span
                    className="ml-auto text-subtle"
                    data-testid="graph-agent-produced-by"
                  >
                    {producedByText(s.producedBy)}
                  </span>
                  {s.note && (
                    <span className="basis-full text-band-spot">{s.note}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  );
}
