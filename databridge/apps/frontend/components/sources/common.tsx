"use client";
/** Small shared pieces for the Sources surface: failure notices, status badges, time labels. */
import * as React from "react";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import type { SourcesFailure } from "@/lib/sources-api";
import { cn } from "@/lib/utils";

/** A failed call, rendered as plain English plus the gateway's own code and message verbatim. */
export function FailureNotice({ failure, testId }: { failure: SourcesFailure; testId?: string }) {
  return (
    <div
      data-testid={testId ?? "sources-failure"}
      data-status={failure.status}
      data-code={failure.code}
      className="flex items-start gap-3 rounded border border-band-review/40 bg-band-review/10 p-3"
    >
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
      <div className="min-w-0 space-y-1">
        <div className="text-xs font-medium text-fg">{failure.plain}</div>
        <div className="break-words font-mono text-2xs text-subtle">
          {failure.code}
          {failure.status ? ` · HTTP ${failure.status}` : ""}
          {failure.message ? ` — ${failure.message}` : ""}
        </div>
      </div>
    </div>
  );
}

export function Warning({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className="flex items-start gap-2 rounded border border-band-spot/40 bg-band-spot/10 p-3 text-xs text-fg"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-band-spot" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

const TONE: Record<string, "good" | "warn" | "bad" | "info" | "neutral"> = {
  ok: "good",
  healthy: "good",
  approved: "good",
  completed: "good",
  empty: "warn",
  unverified: "warn",
  proposed: "warn",
  awaiting_approval: "warn",
  never_synced: "neutral",
  incomplete: "warn",
  unmapped: "neutral",
  running: "info",
  failed: "bad",
  failing: "bad",
};

const TONE_CLASS = {
  good: "border-band-auto/30 bg-band-auto/10 text-band-auto",
  warn: "border-band-spot/30 bg-band-spot/10 text-band-spot",
  bad: "border-band-review/30 bg-band-review/10 text-band-review",
  info: "border-accent/30 bg-accent/10 text-accent",
  neutral: "border-border bg-muted text-subtle",
};

/** A backend status word, shown verbatim (underscores as spaces), coloured by the 3-stop scale. */
export function StatusBadge({ status, testId }: { status: string | null | undefined; testId?: string }) {
  const s = status ?? "none";
  const tone = TONE[s] ?? "neutral";
  return (
    <span
      data-testid={testId}
      data-status={s}
      className={cn("inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-medium", TONE_CLASS[tone])}
    >
      {s === "none" ? "—" : s.replace(/_/g, " ")}
    </span>
  );
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** "3 h ago" — a display of the backend timestamp's age, not a derived metric. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** A count that may be absent. Absent is "—", never 0. */
export function n(v: unknown): string {
  return typeof v === "number" ? v.toLocaleString() : "—";
}

export function Section({
  title,
  description,
  actions,
  children,
  testId,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <section data-testid={testId} className="rounded border border-border bg-surface">
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
          {description && <p className="mt-0.5 text-xs text-subtle">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
