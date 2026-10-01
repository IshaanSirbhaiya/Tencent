"use client";
/**
 * The panels that sit around the `/trace` graph.
 *
 *  - `PointerProvenance` — how much of the request the graph answered from what it already held.
 *  - `CellLedger`        — every field the answer asked for, in the state it ended in.
 *  - `TraceInspector`    — what one node or one message was: asked, decided, pulled, cost.
 *  - `TraceTimeline`     — replay the sequence one message at a time.
 *
 * Three rules are enforced visually here rather than in prose, because each is a claim a system
 * that copies values cannot make:
 *
 *   1. answered-from-history sits beside needed-live-bytes, at the same size;
 *   2. `Withheld` (policy said no, and why) never renders like `Absent` (no source held it);
 *   3. an untrusted value is greyed and labelled out of the decision path — quotable, not
 *      actionable.
 */
import * as React from "react";
import {
  Ban,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  Clock,
  Coins,
  Pause,
  Play,
  ShieldAlert,
  SkipBack,
} from "lucide-react";
import type { RoutedEdge } from "@/lib/trace-layout";
import type {
  TraceCell,
  TraceDetail,
  TraceNode,
  TracePointerProvenance,
} from "@/lib/platform-preview";
import type { TraceSelection } from "./trace-graph";

// ---------------------------------------------------------------------------------------------

/** The ratio that proves the graph is doing work rather than proxying the source. */
export function PointerProvenance({ p }: { p: TracePointerProvenance }) {
  const total = p.total || 1;
  const historyPct = Math.round((p.from_history / total) * 100);

  return (
    <div data-testid="pointer-provenance">
      <div className="flex flex-wrap items-end gap-x-10 gap-y-3">
        <div>
          <div className="tnum text-3xl font-semibold leading-none text-band-auto">
            {p.from_history}
          </div>
          <div className="mt-1 text-2xs text-subtle">answered from history</div>
        </div>
        <div>
          <div className="tnum text-3xl font-semibold leading-none text-band-spot">
            {p.needed_source}
          </div>
          <div className="mt-1 text-2xs text-subtle">needed the source</div>
        </div>
        <div className="ml-auto text-right">
          <div className="tnum text-lg font-semibold leading-none text-fg">{p.total}</div>
          <div className="mt-1 text-2xs text-subtle">pointers · 0 values stored</div>
        </div>
      </div>

      <div className="mt-3 flex h-2 w-full overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-band-auto" style={{ width: `${historyPct}%` }} />
        <div className="h-full bg-band-spot" style={{ width: `${100 - historyPct}%` }} />
      </div>

      <p className="mt-2 text-2xs text-subtle">
        {historyPct}% of this request never reached a source. The graph holds pointers, so a value
        the organisation already asked for once is answered from its own history — no second
        disclosure, no second ledger row, no live load. A system that copies values cannot tell you
        this number, because for it the answer is always 0%.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------

const SOURCE_LABEL: Record<string, string> = {
  graph_history: "graph history",
  live_source: "live source",
  capture: "capture store",
};

/** One field, in the state it ended in. The three states never share a rendering. */
function CellRow({ cell }: { cell: TraceCell }) {
  const untrusted = cell.trust === "untrusted";

  if (cell.state === "withheld") {
    return (
      <tr className="border-l-2 border-l-band-review bg-band-review/[0.05]" data-testid="cell-withheld">
        <td className="py-2 pl-2 font-mono text-2xs text-fg">{cell.path}</td>
        <td className="py-2">
          <span className="inline-flex items-center gap-1 rounded border border-band-review/40 px-1.5 py-px text-[10px] font-medium uppercase tracking-wider text-band-review">
            <Ban className="h-3 w-3" /> withheld
          </span>
        </td>
        <td className="py-2 font-mono text-2xs text-band-review">{cell.reason_code}</td>
        <td className="py-2 text-2xs text-subtle">{cell.note}</td>
      </tr>
    );
  }

  if (cell.state === "absent") {
    return (
      <tr className="border-l-2 border-l-transparent" data-testid="cell-absent">
        <td className="py-2 pl-2 font-mono text-2xs text-subtle">{cell.path}</td>
        <td className="py-2">
          <span className="inline-flex items-center gap-1 rounded border border-dashed border-border px-1.5 py-px text-[10px] uppercase tracking-wider text-subtle">
            <CircleDashed className="h-3 w-3" /> absent
          </span>
        </td>
        <td className="py-2 font-mono text-2xs text-subtle">—</td>
        <td className="py-2 text-2xs text-subtle">{cell.note}</td>
      </tr>
    );
  }

  return (
    <tr
      className={`border-l-2 ${untrusted ? "border-l-band-spot bg-band-spot/[0.05]" : "border-l-band-auto/60"}`}
      data-testid={untrusted ? "cell-untrusted" : "cell-grounded"}
    >
      <td className={`py-2 pl-2 font-mono text-2xs ${untrusted ? "text-subtle line-through decoration-band-spot/50" : "text-fg"}`}>
        {cell.path}
      </td>
      <td className="py-2">
        {untrusted ? (
          <span
            className="inline-flex cursor-help items-center gap-1 rounded border border-band-spot/40 px-1.5 py-px text-[10px] font-medium uppercase tracking-wider text-band-spot"
            title="Quotable, not actionable. It may appear in the answer with its pointer and is excluded from every value the determination turns on."
          >
            <ShieldAlert className="h-3 w-3" /> untrusted
          </span>
        ) : cell.trust === "trusted" ? (
          <span className="rounded border border-band-auto/40 px-1.5 py-px text-[10px] font-medium uppercase tracking-wider text-band-auto">
            trusted
          </span>
        ) : (
          // No trust label was recorded. "Trusted" is a judgement an adapter Core stamps; a cell
          // that never got one has not been judged (Codex f1 #3), so it is not drawn as trusted.
          <span
            className="rounded border border-dashed border-border px-1.5 py-px text-[10px] uppercase tracking-wider text-subtle"
            data-testid="cell-unlabelled"
          >
            trust not recorded
          </span>
        )}
      </td>
      <td className="py-2 font-mono text-2xs text-subtle">
        {cell.verdict ?? "—"}
        {cell.tier ? <span className="ml-1 text-subtle/70">· {cell.tier}</span> : null}
      </td>
      <td className="py-2 text-2xs text-subtle">
        {cell.source ? SOURCE_LABEL[cell.source] ?? cell.source : "—"}
        {untrusted ? (
          <span className="ml-2 rounded bg-band-spot/10 px-1 py-px text-[10px] text-band-spot">
            out of the decision path
          </span>
        ) : null}
        {cell.note && !untrusted ? <span className="ml-2">{cell.note}</span> : null}
      </td>
    </tr>
  );
}

export function CellLedger({ cells, note }: { cells: TraceCell[]; note?: string }) {
  return (
    <div>
      <table className="w-full" data-testid="trace-cells">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider text-subtle/70">
            <th className="pb-1 pl-2 font-medium">field</th>
            <th className="pb-1 font-medium">state</th>
            <th className="pb-1 font-medium">verdict / reason</th>
            <th className="pb-1 font-medium">source</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {/* Keyed by index as well as path: a real trace can record the same field twice — once
              per source — and two rows with the same key would silently collapse into one. */}
          {cells.map((c, i) => (
            <CellRow key={`${c.path}#${i}`} cell={c} />
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-2xs text-subtle">
        <span className="font-medium text-fg">Withheld is not absent.</span> Withheld means the
        field exists and policy did not release it for this purpose, and it always carries the
        reason code. Absent means no source held it — it is named, never guessed and never
        defaulted. Collapsing the two into &ldquo;no data&rdquo; is how a system loses the ability
        to say why.
        {note ? <span className="mt-1 block">{note}</span> : null}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------

const DECISION_STYLE: Record<string, string> = {
  allowed: "border-band-auto/40 text-band-auto",
  withheld: "border-band-review/40 text-band-review",
  refused: "border-band-review/40 text-band-review",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2 py-1">
      <dt className="text-2xs uppercase tracking-wider text-subtle/70">{label}</dt>
      <dd className="break-words font-mono text-2xs text-fg">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-border px-4 py-3">
      <h3 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-subtle">{title}</h3>
      {children}
    </div>
  );
}

/** What one node or one message was. Sections that the payload does not carry are not drawn —
 *  an empty "Policy: —" row would read as "policy had no opinion", which is a different claim. */
export function TraceInspector({
  selection,
  nodes,
  edges,
}: {
  selection: TraceSelection;
  nodes: TraceNode[];
  edges: RoutedEdge[];
}) {
  const node = selection?.type === "node" ? nodes.find((n) => n.id === selection.id) : undefined;
  const edge = selection?.type === "edge" ? edges.find((e) => e.id === selection.id) : undefined;
  const detail: TraceDetail | undefined = node?.detail ?? edge?.detail;

  if (!detail) {
    return (
      <div className="flex h-full min-h-[16rem] flex-col items-center justify-center gap-2 px-6 text-center">
        <p className="text-xs text-subtle">
          Click any participant or any message in the path.
        </p>
        <p className="text-2xs text-subtle/80">
          Each one shows what was requested, what policy decided, what came back, and what it cost.
        </p>
      </div>
    );
  }

  const title = node ? node.label : `${edge!.step}. ${edge!.label}`;
  const subtitle = node
    ? node.sublabel
    : `${edge!.from} → ${edge!.to} · ${edge!.kind}`;

  return (
    <div data-testid="trace-inspector">
      <div className="px-4 py-3">
        <div className="text-sm font-semibold text-fg">{title}</div>
        <div className="font-mono text-2xs text-subtle">{subtitle}</div>
        <p className="mt-2 text-2xs leading-relaxed text-subtle">{detail.summary}</p>
      </div>

      {detail.requested ? (
        <Section title="What was requested">
          <dl>
            <Field label="capability">{detail.requested.capability}</Field>
            <Field label="purpose">{detail.requested.purpose}</Field>
            <Field label="principal">{detail.requested.principal}</Field>
            {detail.requested.predicate ? (
              <Field label="predicate">{detail.requested.predicate}</Field>
            ) : null}
            {detail.requested.as_of ? <Field label="as_of">{detail.requested.as_of}</Field> : null}
            {detail.requested.projection.length ? (
              <div className="grid grid-cols-[6.5rem_1fr] gap-2 py-1">
                <dt className="text-2xs uppercase tracking-wider text-subtle/70">projection</dt>
                <dd className="flex flex-wrap gap-1">
                  {detail.requested.projection.map((f) => (
                    <span
                      key={f}
                      className="rounded border border-border bg-muted px-1 py-px font-mono text-[10px] text-subtle"
                    >
                      {f}
                    </span>
                  ))}
                </dd>
              </div>
            ) : null}
          </dl>
        </Section>
      ) : null}

      {detail.decided ? (
        <Section title="What policy decided">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded border px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider ${
                DECISION_STYLE[detail.decided.decision] ?? "border-border text-subtle"
              }`}
            >
              {detail.decided.decision}
            </span>
            {detail.decided.reason_code ? (
              <span className="rounded bg-band-review/10 px-1.5 py-px font-mono text-[10px] text-band-review">
                {detail.decided.reason_code}
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-2xs leading-relaxed text-subtle">{detail.decided.note}</p>
        </Section>
      ) : null}

      {detail.pulled ? (
        <Section title="What the adapter pulled">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-2xs text-subtle">
            <span>
              rows <span className="tnum font-mono text-fg">{detail.pulled.rows}</span>
            </span>
            <span>
              cells <span className="tnum font-mono text-fg">{detail.pulled.cells}</span>
            </span>
            <span>
              tier <span className="font-mono text-fg">{detail.pulled.tier}</span>
            </span>
          </div>
          {detail.pulled.from_history + detail.pulled.from_source + detail.pulled.from_capture > 0 ? (
            <div className="mt-1 flex flex-wrap gap-x-6 gap-y-1 text-2xs text-subtle">
              <span>
                from history{" "}
                <span className="tnum font-mono text-band-auto">{detail.pulled.from_history}</span>
              </span>
              <span>
                from source{" "}
                <span className="tnum font-mono text-band-spot">{detail.pulled.from_source}</span>
              </span>
              <span>
                from capture{" "}
                <span className="tnum font-mono text-band-spot">{detail.pulled.from_capture}</span>
              </span>
            </div>
          ) : null}
          {detail.pulled.verdicts.length ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {detail.pulled.verdicts.map((v) => (
                <span
                  key={v.verdict}
                  className={`rounded border px-1.5 py-px font-mono text-[10px] ${
                    v.tone === "good"
                      ? "border-band-auto/40 text-band-auto"
                      : v.tone === "bad"
                        ? "border-band-review/40 text-band-review"
                        : v.tone === "warn"
                          ? "border-band-spot/40 text-band-spot"
                          : "border-border text-subtle"
                  }`}
                >
                  {v.verdict} <span className="tnum">{v.count}</span>
                </span>
              ))}
            </div>
          ) : null}
        </Section>
      ) : null}

      {detail.cost ? (
        <Section title="What it cost">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-2xs text-subtle">
            <span className="inline-flex items-center gap-1">
              <Coins className="h-3 w-3" />
              {detail.cost.tokens === null ? (
                <span className="italic text-subtle/80">tokens not recorded</span>
              ) : (
                <>
                  <span className="tnum font-mono text-fg">
                    {detail.cost.tokens.toLocaleString()}
                  </span>{" "}
                  tokens
                </>
              )}
            </span>
            <span className="inline-flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {detail.cost.ms === null ? (
                <span className="italic text-subtle/80">ms not recorded</span>
              ) : (
                <>
                  <span className="tnum font-mono text-fg">{detail.cost.ms.toLocaleString()}</span>{" "}
                  ms
                </>
              )}
            </span>
          </div>
        </Section>
      ) : null}

      {detail.notes.length ? (
        <Section title="Notes">
          <ul className="flex list-disc flex-col gap-1 pl-4 text-2xs leading-relaxed text-subtle">
            {detail.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------

/** Replay the sequence one message at a time. The play loop is opt-in and stops at the end. */
export function TraceTimeline({
  edges,
  step,
  setStep,
}: {
  edges: RoutedEdge[];
  step: number;
  setStep: (n: number) => void;
}) {
  const [playing, setPlaying] = React.useState(false);
  const last = edges.length ? edges[edges.length - 1].step : 0;

  React.useEffect(() => {
    if (!playing) return;
    if (step >= last) {
      setPlaying(false);
      return;
    }
    const t = window.setTimeout(() => setStep(step + 1), 1100);
    return () => window.clearTimeout(t);
  }, [playing, step, last, setStep]);

  const current = edges.find((e) => e.step === step);

  return (
    <div className="flex flex-col gap-2" data-testid="trace-timeline">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="Back to the first message"
            className="rounded border border-border p-1 text-subtle hover:text-fg"
            onClick={() => {
              setPlaying(false);
              setStep(1);
            }}
          >
            <SkipBack className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Previous message"
            className="rounded border border-border p-1 text-subtle hover:text-fg disabled:opacity-40"
            disabled={step <= 1}
            onClick={() => {
              setPlaying(false);
              setStep(Math.max(1, step - 1));
            }}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label={playing ? "Pause replay" : "Replay the sequence"}
            className="rounded border border-border p-1 text-subtle hover:text-fg"
            onClick={() => {
              if (!playing && step >= last) setStep(1);
              setPlaying(!playing);
            }}
          >
            {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            aria-label="Next message"
            className="rounded border border-border p-1 text-subtle hover:text-fg disabled:opacity-40"
            disabled={step >= last}
            onClick={() => {
              setPlaying(false);
              setStep(Math.min(last, step + 1));
            }}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>

        <span className="tnum font-mono text-2xs text-subtle">
          {step} / {last}
        </span>
        {current ? (
          <span className="truncate text-2xs text-fg">
            {current.from} → {current.to} · {current.label}
          </span>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-1">
        {edges.map((e) => (
          <button
            key={e.id}
            type="button"
            title={`${e.step}. ${e.label}`}
            aria-label={`Go to step ${e.step}: ${e.label}`}
            aria-current={e.step === step}
            onClick={() => {
              setPlaying(false);
              setStep(e.step);
            }}
            className={`tnum h-5 min-w-[1.25rem] rounded border px-1 text-[10px] ${
              e.step === step
                ? "border-accent bg-accent text-accent-fg"
                : e.step < step
                  ? "border-border bg-muted text-fg"
                  : "border-border text-subtle"
            }`}
          >
            {e.step}
          </button>
        ))}
      </div>
    </div>
  );
}
