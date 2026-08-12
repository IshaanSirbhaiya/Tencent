"use client";
/**
 * `/evidence` — the evidence ledger, made visible end to end (T-37, `PHASE_7...` §3B).
 *
 * The Governance & Safety story is not new here — `GroundedValue`/`SourcePointer`, the grounding
 * wall, and the append-only ledger already enforce it everywhere. What was missing was ONE place
 * to point at any fact and read the whole chain: source → pointer → extracted value → graph node
 * → committed ledger row, with the raw `SourcePointer` JSON on screen rather than a summary.
 *
 * `GET /evidence/trace/{entity_id}` (`apps/gateway/databridge_gateway/evidence_routes.py`) is
 * read-only — this page can look, it can never write.
 */
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { evidenceApi } from "@/lib/evidence-api";
import { isRefusal } from "@/lib/intake-types";
import { EvidenceEmptyState, EvidenceTraceView } from "@/components/evidence/trace-chain";

const EXAMPLES = [
  "document:chit_01_meridian_referral.txt",
  "patient:pat:<registry id from a candidate list>",
];

function EvidencePageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const fromUrl = params.get("entity") ?? "";
  const [input, setInput] = useState(fromUrl);
  const [entityId, setEntityId] = useState(fromUrl || null);

  // Deep-linkable: `/evidence?entity=<id>` is the URL a future "trace this" link elsewhere in the
  // app (a /brain node, an intake card field) can point at without this page changing shape.
  useEffect(() => {
    if (fromUrl && fromUrl !== entityId) {
      setInput(fromUrl);
      setEntityId(fromUrl);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromUrl]);

  const q = useQuery({
    queryKey: ["evidence-trace", entityId],
    queryFn: () => evidenceApi.trace(entityId as string),
    enabled: !!entityId,
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    setEntityId(trimmed);
    router.replace(`/evidence?entity=${encodeURIComponent(trimmed)}`);
  }

  return (
    <div className="flex flex-col gap-5 p-6">
      <header>
        <h1 className="text-lg font-semibold">Evidence trace</h1>
        <p className="max-w-2xl text-xs text-subtle">
          For any graph node, graph edge, or committed ledger row: which document brought the fact
          in, by which route, the exact <code>SourcePointer</code> that grounds it, and — if
          staff have confirmed it — who attested and when. Read-only; nothing here writes.
        </p>
      </header>

      <form onSubmit={submit} className="flex items-center gap-2">
        <div className="relative flex-1 max-w-xl">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g. document:chit_01_meridian_referral.txt"
            data-testid="evidence-entity-input"
            className="w-full rounded border border-border bg-surface py-1.5 pl-8 pr-2 text-xs outline-none focus:border-accent"
          />
        </div>
        <button
          type="submit"
          className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:bg-accent/90"
        >
          Trace
        </button>
      </form>

      <p className="text-2xs text-subtle">
        Try: {EXAMPLES.map((e, i) => (
          <span key={e}>
            {i > 0 && " · "}
            <code>{e}</code>
          </span>
        ))}
      </p>

      {!entityId && <EvidenceEmptyState />}

      {entityId && q.isLoading && (
        <p className="text-xs text-subtle">Tracing {entityId}…</p>
      )}

      {entityId && q.isError && (
        <p className="text-xs text-band-review" role="alert">
          Could not reach the gateway: {(q.error as Error).message}
        </p>
      )}

      {entityId && q.data && isRefusal(q.data) && (
        <div
          data-testid="evidence-refusal"
          className="rounded-lg border border-band-review/40 bg-band-review/[0.06] px-4 py-3 text-xs"
        >
          <p className="font-medium text-band-review">{q.data.refusal.code}</p>
          <p className="mt-1 text-subtle">{q.data.refusal.message}</p>
        </div>
      )}

      {entityId && q.data && !isRefusal(q.data) && <EvidenceTraceView trace={q.data} />}
    </div>
  );
}

export default function EvidencePage() {
  return (
    <Suspense fallback={<p className="p-6 text-xs text-subtle">Loading…</p>}>
      <EvidencePageInner />
    </Suspense>
  );
}
