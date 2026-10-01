"use client";
import * as React from "react";
import Link from "next/link";
import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { ArtifactSummary } from "@/lib/types";
import { CheckCircle2, Play, ShieldCheck } from "lucide-react";

export default function ArtifactsPage() {
  // null = not loaded yet; [] = loaded, none. The gateway no longer pre-builds a demo artifact at
  // boot (removed 2026-10-01): the list holds only what a real `/convert` run registered.
  const [artifacts, setArtifacts] = React.useState<ArtifactSummary[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [selected, setSelected] = React.useState<Record<string, unknown> | null>(null);
  const [replay, setReplay] = React.useState<Record<string, { deterministic: boolean; checksum_ok: boolean; error?: string }>>({});

  React.useEffect(() => {
    api.artifacts()
      .then((r) => setArtifacts(r.artifacts ?? []))
      .catch((e) => setLoadError(e instanceof Error ? e.message : "could not load artifacts"));
  }, []);

  async function doReplay(id: string) {
    try {
      // a checksum-mismatch refusal comes back as `{refusal}` with no deterministic/checksum_ok
      // fields — guard it so the card shows the refusal, not "EV-DET undefined" (review L2).
      const r = (await api.replayArtifact(id)) as {
        deterministic?: boolean; checksum_ok?: boolean;
        refusal?: { code: string; message: string };
      };
      setReplay((prev) => ({
        ...prev,
        [id]: r.refusal
          ? { deterministic: false, checksum_ok: false, error: r.refusal.code }
          : { deterministic: !!r.deterministic, checksum_ok: !!r.checksum_ok },
      }));
    } catch (e) {
      setReplay((prev) => ({
        ...prev,
        [id]: { deterministic: false, checksum_ok: false,
                error: e instanceof Error ? e.message : "replay failed" },
      }));
    }
  }

  return (
    <>
      <Topbar title="Artifact Browser" />
      <div className="min-w-0 flex-1 overflow-auto p-5">
        {loadError && (
          <div className="mb-3 rounded border border-band-review/40 bg-band-review/10 p-2 text-2xs text-band-review">
            {loadError}
          </div>
        )}
        {artifacts === null && !loadError && <div className="text-xs text-subtle">Loading…</div>}
        {artifacts !== null && artifacts.length === 0 && (
          <div className="rounded border border-border bg-surface p-4 text-xs text-subtle" data-testid="artifacts-empty">
            No artifacts yet. Every run on{" "}
            <Link href="/convert" className="text-accent underline">Convert</Link>{" "}
            registers a replayable, checksummed artifact here.
          </div>
        )}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {(artifacts ?? []).map((a) => {
            const rp = replay[a.artifact_id];
            return (
              <div key={a.artifact_id} className="rounded border border-border bg-surface p-4">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-sm font-semibold">{a.artifact_id}</span>
                  <Badge className="border-accent/40 text-accent">{a.direction}</Badge>
                </div>
                <div className="mt-2 space-y-1 text-2xs text-subtle">
                  <Row k="transform" v={a.transform_language} />
                  <Row k="created" v={a.created_at?.slice(0, 19).replace("T", " ") ?? "—"} />
                  <Row k="checksum" v={a.checksum.slice(0, 24) + "…"} />
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <Button size="sm" variant="outline" onClick={() => doReplay(a.artifact_id)}>
                    <Play className="h-3 w-3" /> Replay
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => api.artifact(a.artifact_id).then(setSelected)}>
                    Audit
                  </Button>
                  {rp && (rp.error ? (
                    <div className="ml-auto text-2xs text-band-review">{rp.error}</div>
                  ) : (
                    <div className="ml-auto flex items-center gap-2 text-2xs">
                      <span className="inline-flex items-center gap-1 text-band-auto">
                        <ShieldCheck className="h-3 w-3" /> checksum {rp.checksum_ok ? "ok" : "fail"}
                      </span>
                      <span className="inline-flex items-center gap-1 text-band-auto">
                        <CheckCircle2 className="h-3 w-3" /> EV-DET {rp.deterministic ? "deterministic" : "DRIFT"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {selected && (
          <div className="mt-5 rounded border border-border bg-surface p-4">
            <div className="mb-2 text-2xs uppercase tracking-wide text-subtle">audit — the transform that was executed</div>
            <pre className="max-h-80 overflow-auto rounded bg-bg p-3 font-mono text-2xs text-fg">
              {JSON.stringify(selected, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <span className="w-20 shrink-0">{k}</span>
      <span className="font-mono text-fg">{v}</span>
    </div>
  );
}
