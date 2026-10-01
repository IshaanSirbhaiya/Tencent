// Gateway client. All business logic is server-side (D-FE-2); this only fetches + renders.
import type { ArtifactSummary, ConvertResponse, E1Result } from "./types";

/** `/evals/e1` is a PLUMBING PROOF: the annotator scored against a deterministic gold backend,
 *  not a model. The gateway says so on the response and the page must render that label. */
export type E1PlumbingProof = E1Result & { kind?: string; backend?: string; note?: string };

export const GATEWAY =
  process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8000";

// R-015: the gateway's disclosing routes (/graph, /trace, /evidence/*, /intelligence,
// /orchestrate) refuse any caller without `Authorization: Bearer <token>`. The token is read from
// NEXT_PUBLIC_WALNUT_TOKEN, which Next inlines at BUILD time — so it is set by
// start-databridge.ps1 for the local demo build only, and is absent (no header sent, the gateway
// refuses) everywhere else. There is deliberately no fallback token in this file.
const WALNUT_TOKEN = process.env.NEXT_PUBLIC_WALNUT_TOKEN;

export function authHeaders(): Record<string, string> {
  return WALNUT_TOKEN ? { Authorization: `Bearer ${WALNUT_TOKEN}` } : {};
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    ...init,
    // INT1: /convert and /artifacts* are gated (R-019 FX1-c) — every call carries the credential
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok && !("refusal" in body)) {
    throw new Error(`${path} → ${res.status}`);
  }
  return body as T;
}

// Removed 2026-10-01 (legacy fakes): `ingest` (/ingest ignored its input), `schema`, `annotate`,
// `confirm`, `finalize` (a boot-built canned draft, confirmed as "demo@databridge.io"),
// `convertSample` (hard-coded rows), `ask`/`suggested` (a boot-built replay dict). The real
// capabilities: connect a source in Builder (-> /sources), ask the graph via /brain/query.
export const api = {
  health: () => call<{ status: string }>("/health"),
  convert: (body: {
    source_b64: string;
    source_format: string;
    target_format: string;
    transform_spec: Record<string, unknown>;
  }) => call<ConvertResponse>("/convert", { method: "POST", body: JSON.stringify(body) }),
  artifacts: () => call<{ artifacts: ArtifactSummary[] }>("/artifacts"),
  artifact: (id: string) => call<Record<string, unknown>>(`/artifacts/${id}`),
  replayArtifact: (id: string) =>
    call<{ result_b64: string; deterministic: boolean; checksum_ok: boolean }>(
      `/artifacts/${id}/replay`,
      { method: "POST" },
    ),
  evalsE1: () => call<E1PlumbingProof>("/evals/e1"),
};
