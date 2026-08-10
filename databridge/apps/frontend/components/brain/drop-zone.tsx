"use client";
import * as React from "react";
import { Upload, Mail, FileText, MessageSquare, FileSignature, Loader2 } from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import type { IngestResult } from "@/lib/brain-types";

// Part 2 — drag-and-drop ingest zone (above the graph). Dropping (or clicking a fixture) runs the
// REAL grounded extraction + bi-temporal upsert on the backend and the new nodes animate into the
// graph. The 3 demo fixtures live in /public/fixtures/brain_demo and resolve to registered sources
// server-side; an unknown text file is read client-side and sent inline.

type Phase = "idle" | "uploading" | "extracting" | "done";

const FIXTURES = [
  { file: "email_001.eml", label: "Email", icon: Mail },
  { file: "meeting_001.txt", label: "Meeting", icon: MessageSquare },
  { file: "contract_001.pdf", label: "Contract", icon: FileSignature },
];
const KNOWN = new Set(FIXTURES.map((f) => f.file));

export function DropZone({ onIngested }: { onIngested: (r: IngestResult) => void }) {
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [progress, setProgress] = React.useState(0);
  const [active, setActive] = React.useState(false);
  const [current, setCurrent] = React.useState<string>("");

  async function ingest(sourcePath: string, inlineText?: string | null) {
    if (phase !== "idle") return;
    setCurrent(sourcePath);
    setPhase("uploading");
    // simulated upload bar (~0.8s) then "extracting" (~1.2s) → ~2s total before nodes appear
    setProgress(0);
    await new Promise<void>((resolve) => {
      let p = 0;
      const id = setInterval(() => {
        p += 12;
        setProgress(Math.min(p, 100));
        if (p >= 100) {
          clearInterval(id);
          resolve();
        }
      }, 90);
    });
    setPhase("extracting");
    try {
      const res = await brainApi.ingest(sourcePath, inlineText ?? null);
      await new Promise((r) => setTimeout(r, 900)); // let the spinner read as "thinking"
      setPhase("done");
      if (!("refusal" in res)) onIngested(res as IngestResult);
    } catch {
      // fail quietly back to idle (gateway offline)
    } finally {
      setTimeout(() => {
        setPhase("idle");
        setProgress(0);
        setCurrent("");
      }, 1200);
    }
  }

  async function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setActive(false);
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    if (KNOWN.has(f.name)) {
      ingest(f.name); // registered fixture → deterministic server-side text
    } else {
      const text = await f.text().catch(() => "");
      ingest(f.name, text); // unknown file → send its text inline
    }
  }

  const busy = phase === "uploading" || phase === "extracting";

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setActive(true);
      }}
      onDragLeave={() => setActive(false)}
      onDrop={onDrop}
      className={`flex items-center gap-4 border-b border-border px-5 py-2.5 transition-colors ${
        active ? "bg-accent/[0.06]" : "bg-surface"
      }`}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        {busy ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" />
        ) : (
          <Upload className="h-4 w-4 shrink-0 text-subtle" />
        )}
        <div className="min-w-0 flex-1">
          {phase === "idle" && (
            <>
              <div className="text-xs font-medium text-fg">Drop files here to add to the Company Brain</div>
              <div className="text-2xs text-subtle">Supports PDF, DOCX, .eml, .txt, Slack export</div>
            </>
          )}
          {phase === "uploading" && (
            <>
              <div className="text-xs font-medium text-fg">Uploading {current}…</div>
              <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-accent transition-all duration-100" style={{ width: `${progress}%` }} />
              </div>
            </>
          )}
          {phase === "extracting" && (
            <div className="text-xs font-medium text-accent">Extracting entities from {current}…</div>
          )}
          {phase === "done" && <div className="text-xs font-medium text-band-auto">Added to the brain ✓</div>}
        </div>
      </div>

      {/* quick-ingest the 3 demo fixtures (also draggable from /public/fixtures/brain_demo) */}
      <div className="flex shrink-0 items-center gap-1.5">
        {FIXTURES.map((f) => {
          const Icon = f.icon;
          return (
            <button
              key={f.file}
              onClick={() => ingest(f.file)}
              disabled={busy}
              title={`Ingest ${f.file}`}
              className="flex items-center gap-1.5 rounded border border-border bg-bg px-2 py-1 text-2xs text-subtle transition-colors hover:border-accent/40 hover:text-fg disabled:opacity-50"
            >
              <Icon className="h-3 w-3" />
              {f.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
