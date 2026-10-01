"use client";
import * as React from "react";
import { Upload, Loader2 } from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import type { IngestResult } from "@/lib/brain-types";

// Drag-and-drop ingest zone (above the graph). Dropping a file sends ITS text to `/brain/ingest`,
// which runs the real grounded extraction + bi-temporal upsert; the new nodes then appear in the
// graph.
//
// What this used to show, and why it no longer does (2026-10-01, ROADMAP §3 item 17):
//   - a simulated upload bar (setInterval to 100%) and a 900 ms "thinking" pause — progress that
//     measured nothing. The only honest states are "sending", "done" and "failed".
//   - a hard-coded "Referral" chip that ingested `demo_sample.txt`, a source the gateway registers
//     only when the clinic seed is opted in. The fixture stays on disk; it is no longer wired in.
//   - a silent catch that fell back to idle. A failed ingest now says why.

type Phase = "idle" | "sending" | "done" | "error";

export function DropZone({ onIngested }: { onIngested: (r: IngestResult) => void }) {
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [active, setActive] = React.useState(false);
  const [current, setCurrent] = React.useState<string>("");
  const [message, setMessage] = React.useState<string>("");

  async function ingest(sourcePath: string, inlineText: string) {
    if (phase === "sending") return;
    setCurrent(sourcePath);
    setMessage("");
    setPhase("sending");
    try {
      const res = (await brainApi.ingest(sourcePath, inlineText)) as IngestResult & {
        refusal?: { code: string; message: string };
      };
      if (res.refusal) {
        setPhase("error");
        setMessage(`${res.refusal.code}: ${res.refusal.message}`);
        return;
      }
      setPhase("done");
      onIngested(res);
    } catch (e) {
      setPhase("error");
      setMessage(e instanceof Error ? e.message : "ingest failed (is the gateway running?)");
    }
  }

  async function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setActive(false);
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    let text: string;
    try {
      text = await f.text();
    } catch {
      setCurrent(f.name);
      setPhase("error");
      setMessage(`could not read ${f.name} in the browser`);
      return;
    }
    ingest(f.name, text);
  }

  const busy = phase === "sending";

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
              <div className="text-2xs text-subtle">The file is read as text in the browser (.txt, .eml, .md); binary formats are not parsed here</div>
            </>
          )}
          {phase === "sending" && (
            <div className="text-xs font-medium text-accent">Extracting entities from {current}…</div>
          )}
          {phase === "done" && (
            <div className="text-xs font-medium text-band-auto">
              {current} added to the brain ✓{" "}
              <button onClick={() => setPhase("idle")} className="ml-2 text-2xs text-subtle underline">dismiss</button>
            </div>
          )}
          {phase === "error" && (
            <div className="text-xs font-medium text-band-review" role="alert">
              Could not ingest {current}: {message}{" "}
              <button onClick={() => setPhase("idle")} className="ml-2 text-2xs text-subtle underline">dismiss</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
