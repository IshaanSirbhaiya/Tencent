"use client";
/**
 * `/evidence` — the pointer inspector, over the shared graph.
 *
 * **Trace** points at any fact — a graph node, a graph edge, or a committed ledger row — and reads
 * the whole chain: each value, the pointer it carries, the captured characters it was read from
 * (highlighted), the source (uri, retrieved at, content hash), a re-verification against the
 * captured bytes, and the ingestion run that brought it in with everything that source excluded.
 * `GET /evidence/trace/{id}` is read-only.
 *
 * **Browse** lists the most recently ingested entities the page may release, and the ingestion
 * sources on disk with their exclusion registers — so the page is usable without knowing an id,
 * and says so plainly when nothing has been ingested yet.
 *
 * **Verify** re-executes ONE pointer against the source adapter it came from (`VerifyPanel`).
 */
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { evidenceApi, type EvidenceRecent, type EvidenceSources } from "@/lib/evidence-api";
import { isRefusal } from "@/lib/intake-types";
import { EvidenceEmptyState, EvidenceTraceView } from "@/components/evidence/trace-chain";
import { TryChips } from "@/components/try-chips";
import { usePackTrials } from "@/components/try-prompts";
import { useNouns } from "@/lib/pack-context";
import { VerifyPanel } from "@/components/evidence/verify-panel";

function RecentList({ data, onPick }: { data: EvidenceRecent; onPick: (id: string) => void }) {
  if (data.entities.length === 0) {
    return (
      <p
        data-testid="evidence-recent-empty"
        className="rounded-lg border border-dashed border-border px-4 py-4 text-2xs text-subtle"
      >
        {data.total === 0
          ? "The graph is empty — nothing has been ingested yet, so there is nothing to trace."
          : `None of the ${data.examined} newest entities examined can be listed — each carries a subject's information. Trace one by id and the confirmation gate answers.`}
      </p>
    );
  }
  return (
    <div>
      <ul className="flex flex-wrap gap-1.5" data-testid="evidence-recent">
        {data.entities.map((e) => (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => onPick(e.id)}
              className="rounded border border-border bg-surface px-2 py-1 text-left text-2xs hover:border-accent/60"
              title={e.id}
            >
              <span className="text-subtle">{e.kind}</span> · {e.label}
            </button>
          </li>
        ))}
      </ul>
      {data.withheld > 0 && (
        <p className="mt-1.5 text-2xs text-subtle">
          <span className="tnum">{data.withheld}</span> of the{" "}
          <span className="tnum">{data.examined}</span> newest entities examined (of{" "}
          <span className="tnum">{data.total}</span>) are not listed: {data.withheld_reason}
        </p>
      )}
    </div>
  );
}

function SourceRegister({ data }: { data: EvidenceSources }) {
  if (data.sources.length === 0) {
    return (
      <p
        data-testid="evidence-sources-empty"
        className="rounded-lg border border-dashed border-border px-4 py-4 text-2xs text-subtle"
      >
        {data.note}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="evidence-sources">
      {data.sources.map((s) => (
        <li key={s.source} className="rounded-lg border border-border bg-surface px-3 py-2 text-xs">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <code className="font-medium">{s.source}</code>
            <span className="text-2xs text-subtle">
              <span className="tnum">{s.runs}</span> run(s) · latest{" "}
              <code>{s.latest.run_id}</code>
            </span>
            {Object.entries(s.latest.counts).map(([family, n]) => (
              <span key={family} className="text-2xs text-subtle">
                {family.replace(/_/g, " ")} <span className="tnum text-fg">{n}</span>
              </span>
            ))}
          </div>
          {s.latest.excluded > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-2xs text-subtle">
              {s.latest.exclusion_reasons.map((r) => (
                <li key={r.reason}>
                  <span className="tnum text-fg">{r.n}</span> · {r.reason}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-2xs text-subtle">Nothing excluded in the latest run.</p>
          )}
          {s.latest.truncated && (
            <p className="mt-1 text-2xs text-band-spot">
              A ledger in this run was larger than the page reads; the counts above cover its
              first rows only.
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

function EvidencePageInner() {
  const router = useRouter();
  const nouns = useNouns();
  const trials = usePackTrials("evidence");
  const params = useSearchParams();
  const fromUrl = params.get("entity") ?? "";
  const [input, setInput] = useState(fromUrl);
  const [entityId, setEntityId] = useState(fromUrl || null);

  // Deep-linkable: `/evidence?entity=<id>` is the URL any "trace this" link elsewhere points at.
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
  const recent = useQuery({ queryKey: ["evidence-recent"], queryFn: () => evidenceApi.recent() });
  const sources = useQuery({ queryKey: ["evidence-sources"], queryFn: () => evidenceApi.sources() });

  function trace(id: string) {
    const trimmed = id.trim();
    if (!trimmed) return;
    setInput(trimmed);
    setEntityId(trimmed);
    router.replace(`/evidence?entity=${encodeURIComponent(trimmed)}`);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    trace(input);
  }

  return (
    <div className="flex flex-col gap-5 p-6">
      <header>
        <h1 className="text-lg font-semibold">Evidence trace</h1>
        <p className="max-w-2xl text-xs text-subtle">
          For any graph node, graph edge, or committed ledger row: each value, the pointer it
          carries, the exact characters it was read from, the source and its content hash, whether
          it still re-verifies, and the ingestion run that brought it in — with everything that
          source excluded. Read-only; nothing here writes.
        </p>
        <p className="mt-1 max-w-2xl text-xs text-subtle">
          Anything carrying a {nouns.subject}&rsquo;s information is released only after a named
          person has checked identity in person and confirmed. Everything else is shown by its
          pointers. A claim that a document concerns a {nouns.subject} is shown as an{" "}
          <span className="text-band-review">unresolved identity claim</span> until a human
          settles it — counted, never asserted.
        </p>
      </header>

      <form onSubmit={submit} className="flex items-center gap-2">
        <div className="relative max-w-xl flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="a node id (kind:key), an edge id, or ledger:<row>"
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

      <TryChips
        groups={trials}
        onPick={(chip) => trace(chip.payload)}
        testId="evidence-try-chips"
        className="max-w-3xl"
      />

      {!entityId && <EvidenceEmptyState />}

      {entityId && q.isLoading && <p className="text-xs text-subtle">Tracing {entityId}…</p>}

      {entityId && q.isError && (
        <p className="text-xs text-band-review" role="alert">
          Could not reach the gateway: {(q.error as Error).message}
        </p>
      )}

      {entityId && q.data && isRefusal(q.data) && (
        <div
          data-testid="evidence-refusal"
          data-refusal-code={q.data.refusal.code}
          className="rounded-lg border border-band-review/40 bg-band-review/[0.06] px-4 py-3 text-xs"
        >
          <p className="font-medium text-band-review">{q.data.refusal.code}</p>
          <p className="mt-1 text-subtle">{q.data.refusal.message}</p>
          {q.data.refusal.code === "AWAITING_HUMAN_CONFIRMATION" && (
            // "Not yet confirmed" is a different fact from "does not exist": the gate working.
            <p className="mt-2 text-subtle">
              This is the confirmation gate, not an error. The entity exists; its evidence becomes
              readable the moment a named person checks identity in person, selects a row and
              confirms.
            </p>
          )}
        </div>
      )}

      {entityId && q.data && !isRefusal(q.data) && <EvidenceTraceView trace={q.data} />}

      <section className="grid grid-cols-1 gap-5 border-t border-border pt-4 lg:grid-cols-2">
        <div>
          <h2 className="mb-2 text-sm font-semibold">Recently ingested</h2>
          {recent.isError && (
            <p className="text-2xs text-band-review" role="alert">
              Could not reach the gateway: {(recent.error as Error).message}
            </p>
          )}
          {recent.data && isRefusal(recent.data) && (
            <p className="text-2xs text-band-review">{recent.data.refusal.message}</p>
          )}
          {recent.data && !isRefusal(recent.data) && (
            <RecentList data={recent.data} onPick={trace} />
          )}
        </div>
        <div>
          <h2 className="mb-2 text-sm font-semibold">Sources &amp; exclusions</h2>
          {sources.isError && (
            <p className="text-2xs text-band-review" role="alert">
              Could not reach the gateway: {(sources.error as Error).message}
            </p>
          )}
          {sources.data && isRefusal(sources.data) && (
            <p className="text-2xs text-band-review">{sources.data.refusal.message}</p>
          )}
          {sources.data && !isRefusal(sources.data) && <SourceRegister data={sources.data} />}
        </div>
      </section>

      <div className="border-t border-border pt-4">
        <VerifyPanel />
      </div>
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
