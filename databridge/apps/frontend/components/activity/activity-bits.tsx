"use client";
/**
 * Small shared pieces of the Activity surface. Every value is rendered as TEXT (React escapes it);
 * model and source text is never injected as HTML.
 */
import * as React from "react";
import {
  NOT_RECORDED,
  producerText,
  type ProducedBy,
  type Tone,
} from "@/lib/activity-api";

export const TONE_TEXT: Record<Tone, string> = {
  good: "text-band-auto",
  warn: "text-band-spot",
  bad: "text-band-review",
  neutral: "text-subtle",
};

export const TONE_BORDER: Record<Tone, string> = {
  good: "border-band-auto/40",
  warn: "border-band-spot/50",
  bad: "border-band-review/60",
  neutral: "border-border",
};

export function StatusPill({ status, tone }: { status: string | null | undefined; tone: Tone }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider ${TONE_BORDER[tone]} ${TONE_TEXT[tone]}`}
    >
      {status ?? NOT_RECORDED}
    </span>
  );
}

/** A value, or "not recorded" in the muted italic every surface uses for a missing measurement. */
export function Maybe({ value, mono = true }: { value: React.ReactNode; mono?: boolean }) {
  if (value === null || value === undefined || value === "") {
    return <span className="italic text-subtle/80">{NOT_RECORDED}</span>;
  }
  return <span className={mono ? "font-mono text-fg" : "text-fg"}>{value}</span>;
}

export function Producer({ pb }: { pb: ProducedBy | null | undefined }) {
  const text = producerText(pb);
  return text === NOT_RECORDED ? (
    <span className="italic text-subtle/80">{text}</span>
  ) : (
    <span className="font-mono text-fg" title={pb?.prompt_version ?? undefined}>
      {text}
    </span>
  );
}

export function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7.5rem_1fr] gap-2 py-0.5 text-2xs">
      <dt className="uppercase tracking-wider text-subtle/70">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/** Verbatim text from a model or a source: preformatted, escaped, never interpreted. */
export function Verbatim({ text, max = 6000 }: { text: string | null | undefined; max?: number }) {
  if (!text) return <span className="italic text-subtle/80">{NOT_RECORDED}</span>;
  const clipped = text.length > max;
  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed text-fg">
      {clipped ? `${text.slice(0, max)}\n… (${text.length - max} more characters)` : text}
    </pre>
  );
}

export function when(iso: string | null | undefined): string {
  if (!iso) return NOT_RECORDED;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
