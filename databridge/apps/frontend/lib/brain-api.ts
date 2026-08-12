// Company-Brain gateway client (T08). Read-only render layer — all logic is server-side (D-FE-2).
import { GATEWAY } from "./api";
import type { Refusal } from "./types";
import type { BrainAnswer, BrainGraph, IngestResult, Insight, NodeDetail, TimelineEvent } from "./brain-types";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  // refusals (e.g. an ungrounded query) ride in the body with a 4xx — surface, don't throw.
  if (!res.ok && !("refusal" in body)) throw new Error(`${path} → ${res.status}`);
  return body as T;
}

export const brainApi = {
  graph: () => call<BrainGraph>("/brain/graph"),
  seed: () =>
    call<{ stats: { nodes_created: number; edges_created: number; sources_attached: number } } & BrainGraph>(
      "/brain/graph/seed",
    ),
  insights: () => call<{ insights: Insight[] }>("/brain/insights"),
  // grounded answer (200) OR a refusal object (422) — a discriminated union, not a merge, so a
  // consumer can't mistake the refusal object for the string `BrainAnswer.refusal`.
  query: (question: string, as_of?: string) =>
    call<BrainAnswer | { grounded?: false; refusal: Refusal }>("/brain/query", {
      method: "POST",
      body: JSON.stringify({ question, as_of: as_of ?? null }),
    }),
  timeline: (entityId: string) =>
    call<{ entity?: string; events: TimelineEvent[]; refusal?: Refusal }>(
      `/brain/timeline/${encodeURIComponent(entityId)}`,
    ),
  node: (entityId: string) =>
    call<NodeDetail & { refusal?: Refusal }>(`/brain/node/${encodeURIComponent(entityId)}`),
  /** The node's EVIDENCE SHEET, rendered server-side.
   *
   * The panel used to assemble its own markdown in the browser from a node-detail payload, which
   * meant the person reading a node and the model answering about it saw DIFFERENT text. That
   * defeats the point of grounding: an answer can only be checked against the evidence if both
   * sides were shown the same evidence. One renderer, on the server
   * (`packages/graph/databridge_graph/node_sheet.py`), feeds both. */
  nodeSheet: (entityId: string) =>
    call<{ entity_id: string; markdown: string } & { refusal?: Refusal }>(
      `/brain/node/${encodeURIComponent(entityId)}/sheet`,
    ),
  // drag-and-drop ingest (Part 2): a registered fixture filename (or inline text) → grounded
  // extraction → new graph nodes. Returns the genuinely NEW entity labels.
  ingest: (source_path: string, text?: string | null) =>
    call<IngestResult & { refusal?: Refusal }>("/brain/ingest", {
      method: "POST",
      body: JSON.stringify({ source_path, text: text ?? null }),
    }),
};
