// Company-Brain gateway client (T08). Read-only render layer — all logic is server-side (D-FE-2).
import { GATEWAY, authHeaders } from "./api";
import type { Refusal } from "./types";
import type { BrainGraph, BrainQueryResult, IngestResult, Insight, NodeDetail, TimelineEvent } from "./brain-types";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    ...init,
    // INT1: every /brain route requires a principal (E2E F-25) — send the credential
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
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
    // POST: the seed WRITES the shared graph (R-019 main #6 — GET is kept only for old clients)
    call<{ stats: { nodes_created: number; edges_created: number; sources_attached: number } } & BrainGraph>(
      "/brain/graph/seed",
      { method: "POST" },
    ),
  insights: () => call<{ insights: Insight[] }>("/brain/insights"),
  // grounded answer (200) OR a refusal object (422) — a discriminated union, not a merge, so a
  // consumer can't mistake the refusal object for the string `BrainAnswer.refusal`.
  query: (question: string, as_of?: string) =>
    // N-14: a cross-source question comes back DELEGATED (`routed_to: "federated"`), with the
    // computed table, coverage and candidate flags; anything else is the graph answer.
    call<BrainQueryResult>("/brain/query", {
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
  /** EVERY node's evidence sheet, as a zip of markdown files.
   *
   * The same document `nodeSheet` returns, for every entity at once — a folder that opens in any
   * editor or Obsidian vault. It is the provenance claim made portable: a reviewer who will never
   * run this app can still read what the system knows about an entity and check each value against
   * the citation printed beside it.
   *
   * `asOf` is passed through so the download matches what is on screen: with the time slider
   * rewound, the sheets are the ones that instant would have produced, and an entity that did not
   * exist then is absent from the folder rather than present and empty.
   *
   * Fetched as a blob rather than navigated to, so a refusal (503 when the graph is not loaded)
   * arrives as a value this app can show instead of raw JSON replacing the page. */
  downloadSheets: async (asOf?: string | null): Promise<{ filename: string; blob: Blob }> => {
    const query = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
    const res = await fetch(`${GATEWAY}/brain/export/sheets${query}`, {
      cache: "no-store",
      headers: authHeaders(),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { refusal?: Refusal };
      throw new Error(body.refusal?.message ?? `Export failed (${res.status})`);
    }
    // The server names the archive (it knows the instant it actually rendered at); fall back only
    // if the header is absent — `Content-Disposition` is readable here because the route exposes it.
    const disposition = res.headers.get("content-disposition") ?? "";
    const match = /filename="([^"\\]+)"/.exec(disposition);
    return { filename: match?.[1] ?? "evidence-sheets.zip", blob: await res.blob() };
  },
  // drag-and-drop ingest (Part 2): a registered fixture filename (or inline text) → grounded
  // extraction → new graph nodes. Returns the genuinely NEW entity labels.
  ingest: (source_path: string, text?: string | null) =>
    call<IngestResult & { refusal?: Refusal }>("/brain/ingest", {
      method: "POST",
      body: JSON.stringify({ source_path, text: text ?? null }),
    }),
};
