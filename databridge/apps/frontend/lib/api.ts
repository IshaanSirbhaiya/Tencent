// Gateway client. All business logic is server-side (D-FE-2); this only fetches + renders.
import type {
  AnnotatedSchema,
  ArtifactSummary,
  AskResponse,
  ConvertResponse,
  Decision,
  E1Result,
  QueueCounts,
} from "./types";

export const GATEWAY =
  process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8000";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok && !("refusal" in body)) {
    throw new Error(`${path} → ${res.status}`);
  }
  return body as T;
}

export const api = {
  health: () => call<{ status: string }>("/health"),
  ingest: (source_url: string, source_type = "fixture") =>
    call<{ job_id: string; source_id: string }>("/ingest", {
      method: "POST",
      body: JSON.stringify({ source_url, source_type }),
    }),
  schema: (jobId: string) => call<Record<string, unknown>>(`/schema/${jobId}`),
  annotate: () =>
    call<{ draft: AnnotatedSchema; queue_counts: QueueCounts }>("/annotate", {
      method: "POST",
      body: JSON.stringify({ schema_extract: {} }),
    }),
  confirm: (draft: AnnotatedSchema, decisions: Decision[]) =>
    call<{ draft: AnnotatedSchema; queue_counts: QueueCounts; remaining_mandatory: number }>(
      "/annotate/confirm",
      { method: "POST", body: JSON.stringify({ draft, decisions }) },
    ),
  finalize: (draft: AnnotatedSchema, confirmed_by: string, decisions: Decision[]) =>
    call<{ schema?: AnnotatedSchema; refusal?: { code: string; message: string } }>(
      "/annotate/finalize",
      { method: "POST", body: JSON.stringify({ draft, confirmed_by, decisions }) },
    ),
  convertSample: () =>
    call<{ source_b64: string; source_format: string; target_format: string; transform_spec: Record<string, unknown>; preview: string }>(
      "/convert/sample",
    ),
  convert: (body: {
    source_b64: string;
    source_format: string;
    target_format: string;
    transform_spec: Record<string, unknown>;
  }) => call<ConvertResponse>("/convert", { method: "POST", body: JSON.stringify(body) }),
  ask: (question: string) =>
    call<AskResponse>("/ask", {
      method: "POST",
      body: JSON.stringify({ question, confirmed_schema_id: "fintech" }),
    }),
  suggested: () => call<{ questions: string[]; injection: string }>("/ask/suggested"),
  artifacts: () => call<{ artifacts: ArtifactSummary[] }>("/artifacts"),
  artifact: (id: string) => call<Record<string, unknown>>(`/artifacts/${id}`),
  replayArtifact: (id: string) =>
    call<{ result_b64: string; deterministic: boolean; checksum_ok: boolean }>(
      `/artifacts/${id}/replay`,
      { method: "POST" },
    ),
  evalsE1: () => call<E1Result>("/evals/e1"),
};
