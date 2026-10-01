"use client";
/**
 * Pieces every Builder pane shares: the English box, the refusal line, derivation badges, the
 * collapsible YAML, and the plain-word renderings of triggers and approval modes.
 *
 * Nothing here decides anything. The wording functions translate a backend value into the words a
 * person reads ("asks you first") — they never infer a value the backend did not send.
 */
import * as React from "react";
import { ChevronDown, ChevronRight, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { AgentsApiError, type AutomationMode, type Derivation, type Trigger } from "@/lib/agents-api";

// ============================================================================================
// errors
// ============================================================================================
export function ErrorLine({ error, testId }: { error: unknown; testId?: string }) {
  if (!error) return null;
  const code = error instanceof AgentsApiError ? error.code : "ERROR";
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div
      data-testid={testId ?? "builder-error"}
      className="flex items-start gap-2 rounded border border-band-review/40 bg-band-review/10 p-2.5"
    >
      <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
      <div className="min-w-0">
        <div className="font-mono text-2xs font-semibold text-band-review">{code}</div>
        <div className="mt-0.5 whitespace-pre-wrap break-words text-xs text-fg">{message}</div>
      </div>
    </div>
  );
}

// ============================================================================================
// layout primitives
// ============================================================================================
export function Section({
  title,
  aside,
  children,
  testId,
  className,
}: {
  title: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <section data-testid={testId} className={cn("rounded border border-border bg-surface", className)}>
      <header className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
        <h2 className="text-xs font-semibold tracking-tight text-fg">{title}</h2>
        {aside}
      </header>
      <div className="p-3">{children}</div>
    </section>
  );
}

export function Row({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div data-testid={testId} className="grid grid-cols-[9rem_1fr] gap-3 border-b border-border py-1.5 last:border-0">
      <dt className="text-2xs uppercase tracking-wide text-subtle">{label}</dt>
      <dd className="min-w-0 text-xs text-fg">{children}</dd>
    </div>
  );
}

export function Collapsible({
  title,
  children,
  testId,
  defaultOpen = false,
}: {
  title: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <div data-testid={testId} className="rounded border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-2xs text-subtle hover:text-fg"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {title}
      </button>
      {open && <div className="border-t border-border">{children}</div>}
    </div>
  );
}

export function Yaml({ text, testId }: { text: string; testId?: string }) {
  return (
    <pre
      data-testid={testId}
      className="max-h-80 overflow-auto p-2.5 font-mono text-2xs leading-relaxed text-fg"
    >
      {text}
    </pre>
  );
}

export function StatusPill({ status, testId }: { status: string; testId?: string }) {
  const tone =
    status === "active" || status === "healthy" || status === "completed" || status === "executed"
      ? "border-band-auto/40 text-band-auto"
      : status === "failing" || status === "failed" || status === "refused" || status === "rejected"
        ? "border-band-review/40 text-band-review"
        : status === "paused" || status === "stale" || status === "draft" || status === "proposed" ||
            status === "never_synced" || status === "awaiting_approval" || status === "stopped"
          ? "border-band-spot/40 text-band-spot"
          : "border-border text-subtle";
  return (
    <span
      data-testid={testId}
      className={cn("inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-2xs", tone)}
    >
      {status}
    </span>
  );
}

// ============================================================================================
// the English box
// ============================================================================================
export function EnglishBox({
  value,
  onChange,
  onSubmit,
  examples,
  placeholder,
  busy,
  testId,
  submitLabel = "Understand this",
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  examples: { label: string; text: string }[];
  placeholder: string;
  busy: boolean;
  testId: string;
  submitLabel?: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <textarea
        data-testid={`${testId}-text`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit();
        }}
        rows={4}
        placeholder={placeholder}
        className="w-full resize-y rounded border border-border bg-bg px-2.5 py-2 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
      />
      <div className="flex flex-wrap items-center gap-2">
        {examples.map((ex, i) => (
          <button
            key={ex.label}
            type="button"
            data-testid={`${testId}-example-${i}`}
            onClick={() => onChange(ex.text)}
            title={ex.text}
            className="rounded border border-border px-2 py-0.5 text-2xs text-subtle hover:border-accent/50 hover:text-fg"
          >
            Example: {ex.label}
          </button>
        ))}
        <span className="flex-1" />
        <button
          type="button"
          data-testid={`${testId}-submit`}
          onClick={onSubmit}
          disabled={busy || !value.trim()}
          className="h-7 rounded bg-accent px-3 text-xs font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-40"
        >
          {busy ? "Reading…" : submitLabel}
        </button>
      </div>
    </div>
  );
}

// ============================================================================================
// derivations — which parts came from rules, the model, or you
// ============================================================================================
/** The compiler's `by` words that mean a deterministic rule — the workflow contract's own table
 *  (`databridge_workflow_spec.BY_TO_PRODUCED_BY`, the words mapped to `rules`). */
const RULE_WORDS = new Set(["rule", "rules", "default", "config", "catalogue"]);

/** The compiler's `by` → who, in the page's words. `model+answer` is both. A word the contract
 *  does not map (including `unknown`) is `unknown` — never presented as a deterministic rule. */
export function whoProduced(by: string | null | undefined): ("rules" | "model" | "you" | "unknown")[] {
  const w = (by ?? "").trim();
  const out: ("rules" | "model" | "you" | "unknown")[] = [];
  if (w.split("+").includes("model")) out.push("model");
  if (w.split("+").includes("answer") || w === "user") out.push("you");
  if (!out.length && RULE_WORDS.has(w)) out.push("rules");
  if (!out.length) out.push("unknown");
  return out;
}

/** Derivations for one field: the exact field, or anything under it (`actions[x].tool`). */
export function derivationsFor(all: Derivation[], field: string): Derivation[] {
  return all.filter(
    (d) => d.field === field || d.field.startsWith(`${field}.`) || d.field.startsWith(`${field}[`),
  );
}

export function DerivationBadges({ derivations, testId }: { derivations: Derivation[]; testId?: string }) {
  const who = Array.from(new Set(derivations.flatMap((d) => whoProduced(d.by))));
  if (!who.length) return null;
  const title = derivations
    .map((d) => `${d.field}: ${d.by}${d.model ? ` (${d.model})` : ""}${d.evidence ? ` — ${d.evidence}` : ""}`)
    .join("\n");
  return (
    <span data-testid={testId} className="ml-1.5 inline-flex gap-1 align-middle" title={title}>
      {who.map((w) => (
        <span
          key={w}
          data-testid={testId ? `${testId}-${w}` : undefined}
          className={cn(
            "rounded-sm px-1 font-mono text-[10px] leading-4",
            w === "model"
              ? "bg-band-spot/15 text-band-spot"
              : w === "you"
                ? "bg-accent/15 text-accent"
                : w === "unknown"
                  ? "border border-dashed border-border text-subtle"
                  : "bg-muted text-subtle",
          )}
        >
          {w}
        </span>
      ))}
    </span>
  );
}

export function DerivationTable({ derivations, testId }: { derivations: Derivation[]; testId?: string }) {
  if (!derivations.length) return <p className="p-2.5 text-2xs text-subtle">No derivations were recorded.</p>;
  return (
    <table data-testid={testId} className="w-full text-left text-2xs">
      <thead className="text-subtle">
        <tr className="border-b border-border">
          <th className="px-2.5 py-1 font-medium">field</th>
          <th className="px-2.5 py-1 font-medium">from</th>
          <th className="px-2.5 py-1 font-medium">basis</th>
        </tr>
      </thead>
      <tbody>
        {derivations.map((d) => (
          <tr key={d.field} className="border-b border-border last:border-0 align-top">
            <td className="px-2.5 py-1 font-mono text-fg">{d.field}</td>
            <td className="px-2.5 py-1">
              <DerivationBadges derivations={[d]} />
              <span className="ml-1 font-mono text-subtle">{d.by}</span>
            </td>
            <td className="px-2.5 py-1 text-subtle">
              {d.evidence || "—"}
              {d.model && <span className="ml-1 font-mono">· {d.model}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ============================================================================================
// plain words
// ============================================================================================
export function approvalInWords(mode: AutomationMode, reversal: string | null): string {
  if (mode === "human_required") return "asks you first";
  if (mode === "human_notified") return "tells you after";
  return `runs on its own — undo: ${reversal?.trim() || "(no reversal declared)"}`;
}

const DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

/** A handful of common cron shapes in words; anything else is shown as the cron itself. The cron
 *  and zone are always shown beside the words, so nothing rests on this reading alone. */
export function cronInWords(cron: string): string | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [min, hour, dom, mon, dow] = parts;
  const simple = /^\d+$/;
  if (dom !== "*" || mon !== "*") return null;
  if (min === "0" && hour === "*" && dow === "*") return "every hour, on the hour";
  if (/^\*\/\d+$/.test(min) && hour === "*" && dow === "*") return `every ${min.slice(2)} minutes`;
  if (simple.test(min) && hour === "*" && dow === "*") return `every hour at :${min.padStart(2, "0")}`;
  if (!simple.test(min) || !simple.test(hour)) return null;
  const at = `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (dow === "*") return `every day at ${at}`;
  if (dow === "1-5" || dow.toUpperCase() === "MON-FRI") return `every weekday (Mon–Fri) at ${at}`;
  if (/^\d$/.test(dow)) return `every ${DOW[Number(dow) % 7]} at ${at}`;
  return null;
}

export function TriggerInWords({ trigger, nextFire }: { trigger: Trigger; nextFire?: string | null }) {
  if (trigger.type === "manual") {
    return <span data-testid="trigger-words">on demand — runs when you ask it to</span>;
  }
  if (trigger.type === "schedule" && trigger.schedule) {
    const words = cronInWords(trigger.schedule.cron);
    return (
      <span data-testid="trigger-words">
        automation, on a schedule: {words ?? "cron"}{" "}
        <span className="font-mono text-subtle">
          ({trigger.schedule.cron}, {trigger.schedule.timezone})
        </span>
        {nextFire && <span className="ml-1 text-subtle">· next {fmtTime(nextFire)}</span>}
      </span>
    );
  }
  if (trigger.type === "event" && trigger.event) {
    const f = Object.entries(trigger.event.filter ?? {});
    return (
      <span data-testid="trigger-words">
        automation, on an event: when <span className="font-mono">{trigger.event.source}</span> reports{" "}
        {trigger.event.on.map((e) => e.replace(/_/g, " ")).join(" or ")}
        {f.length > 0 && (
          <span className="font-mono text-subtle"> ({f.map(([k, v]) => `${k}=${v}`).join(", ")})</span>
        )}
      </span>
    );
  }
  return <span data-testid="trigger-words">{trigger.type}</span>;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function OpenQuestions({
  questions,
  answers,
  onAnswer,
  busy,
}: {
  questions: { id: string; text: string; options: string[] }[];
  answers: Record<string, string>;
  onAnswer: (id: string, value: string) => void;
  busy: boolean;
}) {
  const [drafts, setDrafts] = React.useState<Record<string, string>>({});
  if (!questions.length) return null;
  return (
    <div data-testid="open-questions" className="flex flex-col gap-2">
      <p className="text-2xs font-semibold uppercase tracking-wide text-band-spot">
        {questions.length} question{questions.length === 1 ? "" : "s"} before this can be saved
      </p>
      {questions.map((q) => (
        <div
          key={q.id}
          data-testid={`question-${q.id}`}
          className="rounded border border-band-spot/30 bg-band-spot/[0.05] p-2.5"
        >
          <p className="text-xs text-fg">{q.text}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {q.options.map((opt) => (
              <button
                key={opt}
                type="button"
                disabled={busy}
                data-testid={`answer-${q.id}-${opt}`}
                onClick={() => onAnswer(q.id, opt)}
                className={cn(
                  "rounded border px-2 py-0.5 font-mono text-2xs",
                  answers[q.id] === opt
                    ? "border-accent bg-accent/15 text-fg"
                    : "border-border text-subtle hover:border-accent/50 hover:text-fg",
                )}
              >
                {opt}
              </button>
            ))}
            <form
              className="flex items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                const v = (drafts[q.id] ?? "").trim();
                if (v) onAnswer(q.id, v);
              }}
            >
              <input
                data-testid={`answer-${q.id}-text`}
                value={drafts[q.id] ?? ""}
                onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
                placeholder={q.options.length ? "or type an answer" : "your answer"}
                className="h-6 w-48 rounded border border-border bg-bg px-1.5 text-2xs text-fg focus:border-accent focus:outline-none"
              />
              <button
                type="submit"
                disabled={busy || !(drafts[q.id] ?? "").trim()}
                className="h-6 rounded border border-border px-2 text-2xs text-subtle hover:text-fg disabled:opacity-40"
              >
                Answer
              </button>
            </form>
          </div>
        </div>
      ))}
    </div>
  );
}
