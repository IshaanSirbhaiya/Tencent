"use client";
import * as React from "react";
import { ArrowRight, Search, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { GATEWAY, authHeaders } from "@/lib/api";
import {
  orchestratorApi,
  isRefusal,
  type OrchestratorAskResult,
  type OrchestratorCell,
  type OrchestratorFleetStatus,
  type OrchestratorSourceOutcome,
  type Refusal,
} from "@/lib/orchestrator-api";
import { Grounded } from "@/components/grounded";
import type { SourcePointer } from "@/lib/types";
import { cn } from "@/lib/utils";

// The ask-the-fleet panel (the main-agent loop, `packages/orchestrator`). Modelled on
// `components/brain/ask-panel.tsx`'s interaction shape — search box → suggestions → staged result
// — because that is the shape the founder already likes; the difference is what stage 2 renders.
// `/brain/query` answers with a model-written sentence plus citations bolted on; `/orchestrate/ask`
// has no sentence to show at all. What crosses that boundary is CELLS AND POINTERS — a pointer with
// a trust label, never prose a model wrote about a row — so this panel renders exactly that: one
// card per source the orchestrator asked, each cell as a labelled value with its receipt behind a
// click (`Grounded`, reused as-is), and a refused source rendered as a refusal with its reason code,
// never as an absent row.
//
// Self-contained: no required props. `initialQuestion` and `className` are optional, for a host
// page that wants to seed the box or control layout; there is no dependency on a selected graph
// node or an active filter because this loop queries the adapter fleet directly, not the graph
// store `app/graph` renders.

const TONE = {
  good: "border-band-auto/40 text-band-auto",
  bad: "border-band-review/40 text-band-review",
  warn: "border-band-spot/40 text-band-spot",
  neutral: "border-border text-subtle",
} as const;

function toneFor(status: OrchestratorSourceOutcome["status"]): keyof typeof TONE {
  if (status === "answered") return "good";
  if (status === "refused") return "bad";
  return "warn";
}

function StatusBadge({ status }: { status: OrchestratorSourceOutcome["status"] }) {
  const tone = toneFor(status);
  const Icon = status === "answered" ? ShieldCheck : status === "refused" ? ShieldAlert : ShieldQuestion;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wide",
        TONE[tone],
      )}
    >
      <Icon className="h-3 w-3" />
      {status}
    </span>
  );
}

function TrustBadge({ trust }: { trust: string | null }) {
  if (!trust) return null;
  const untrusted = trust === "untrusted";
  return (
    <span
      className={cn(
        "rounded border px-1 py-px font-mono text-[10px] uppercase",
        untrusted ? TONE.warn : "border-border text-subtle",
      )}
      title={
        untrusted
          ? "attacker-writable content — quotable, never actionable"
          : "source-contract-trusted field"
      }
    >
      {trust}
    </span>
  );
}

function pointerFor(cell: OrchestratorCell): SourcePointer | null {
  if (!cell.pointer) return null;
  return cell.pointer as unknown as SourcePointer;
}

function CellRow({ cell }: { cell: OrchestratorCell }) {
  if (cell.state === "grounded") {
    return (
      <div className="flex items-baseline justify-between gap-3 py-0.5 text-2xs">
        <span className="shrink-0 text-subtle">{cell.column}</span>
        <span className="flex min-w-0 items-center gap-1.5">
          <Grounded pointer={pointerFor(cell)}>
            <span className="truncate font-mono text-fg">{String(cell.value ?? "—")}</span>
          </Grounded>
          <TrustBadge trust={cell.trust} />
        </span>
      </div>
    );
  }
  if (cell.state === "withheld") {
    return (
      <div className="flex items-baseline justify-between gap-3 py-0.5 text-2xs">
        <span className="shrink-0 text-subtle">{cell.column}</span>
        <span className={cn("rounded border px-1.5 py-px font-mono text-[10px]", TONE.bad)}>
          withheld · {cell.reason_code ?? "no reason recorded"}
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5 text-2xs">
      <span className="shrink-0 text-subtle">{cell.column}</span>
      <span className="text-subtle">absent — not in the source</span>
    </div>
  );
}

function SourceCard({ outcome }: { outcome: OrchestratorSourceOutcome }) {
  const grounded = outcome.cells.filter((c) => c.state === "grounded").length;
  const withheld = outcome.cells.filter((c) => c.state === "withheld").length;
  const absent = outcome.cells.filter((c) => c.state === "absent").length;

  return (
    <div className="rounded border border-border bg-surface p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs font-medium text-fg">{outcome.source_id}</span>
        <StatusBadge status={outcome.status} />
      </div>

      {outcome.entity && (
        <div className="mt-0.5 text-2xs text-subtle">
          {outcome.capability} · {outcome.entity}
          {outcome.cells.length > 0 &&
            ` · ${grounded} grounded${withheld ? ` · ${withheld} withheld` : ""}${absent ? ` · ${absent} absent` : ""}`}
        </div>
      )}

      {outcome.status === "answered" && outcome.cells.length > 0 && (
        <div className="mt-2 divide-y divide-border/60 border-t border-border/60 pt-1">
          {outcome.cells.map((c, i) => (
            <CellRow key={`${c.column}-${c.row_id ?? i}`} cell={c} />
          ))}
        </div>
      )}
      {outcome.status === "answered" && outcome.cells.length === 0 && (
        <div className="mt-1.5 text-2xs text-subtle">
          {outcome.message || "no rows in scope for this projection"}
        </div>
      )}

      {outcome.status === "refused" && outcome.denial && (
        <div className="mt-1.5 flex items-start gap-1.5 rounded border border-band-review/30 bg-band-review/[0.06] px-2 py-1.5">
          <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0 text-band-review" />
          <div className="text-2xs text-fg">
            <div className="font-mono">{outcome.denial.reason_code}</div>
            <div className="text-subtle">policy {outcome.denial.policy_id}</div>
          </div>
        </div>
      )}

      {outcome.status === "escalated" && (
        <div className="mt-1.5 flex items-start gap-1.5 rounded border border-band-spot/30 bg-band-spot/[0.06] px-2 py-1.5">
          <ShieldQuestion className="mt-0.5 h-3 w-3 shrink-0 text-band-spot" />
          <span className="text-2xs text-fg">{outcome.message || "could not plan a read inside this source's advertised capabilities"}</span>
        </div>
      )}
    </div>
  );
}

// No identities and no questions are written in this file. WHO asks is the signed-in identity
// the gateway authenticated (the bearer token) — the fleet payload names it, and there is nothing
// to pick: a request cannot choose who it is (R-015). The suggested questions come from the same
// payload (the bound packs' `trials.orchestrate`, or questions derived from what the fixture
// adapters describe); when there are none, none are shown.
type FleetPayload = OrchestratorFleetStatus & {
  mode?: string;
  principal?: { id: string; label: string; purpose: string | null } | null;
  can_ask?: boolean;
  ask_disabled_reason?: string | null;
  suggested_questions?: string[];
};

// The body carries the QUESTION only. The fleet derives the asker from the credential; a body
// that named one would be refused (the gateway forbids extra fields).
async function askFleet(question: string): Promise<OrchestratorAskResult | { refusal: Refusal }> {
  const res = await fetch(`${GATEWAY}/orchestrate/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ question }),
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok && !("refusal" in body)) throw new Error(`/orchestrate/ask → ${res.status}`);
  return body as OrchestratorAskResult | { refusal: Refusal };
}

export function AskPanel({
  initialQuestion,
  className,
}: {
  /** Pre-fills the ask box; the panel still waits for a submit. Optional. */
  initialQuestion?: string;
  className?: string;
}) {
  const [q, setQ] = React.useState(initialQuestion ?? "");
  const [fleet, setFleet] = React.useState<FleetPayload | null>(null);
  const [fleetError, setFleetError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [result, setResult] = React.useState<OrchestratorAskResult | null>(null);
  const [refusal, setRefusal] = React.useState<Refusal | null>(null);
  const [fetchError, setFetchError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    orchestratorApi
      .fleet()
      .then((status) => {
        if (cancelled) return;
        const payload = status as FleetPayload & { refusal?: Refusal };
        if (payload.refusal) {
          setFleetError(payload.refusal.message);
          return;
        }
        setFleet(payload);
      })
      .catch(() => {
        if (!cancelled) setFleetError("Could not reach the gateway — asking is disabled.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const principal = fleet?.principal ?? null;
  const canAsk = Boolean(fleet && fleet.can_ask !== false && principal);
  const disabledReason =
    fleetError ??
    (fleet
      ? fleet.can_ask === false
        ? fleet.ask_disabled_reason ?? "Asking is not available for this identity."
        : !principal
          ? "Sign in first — a question is always asked as someone."
          : null
      : null);
  const suggested = fleet?.suggested_questions ?? [];

  async function ask(question: string) {
    if (!question.trim() || loading || !canAsk) return;
    setQ(question);
    setLoading(true);
    setFetchError(null);
    setRefusal(null);
    try {
      const res = await askFleet(question);
      if (isRefusal(res)) {
        setResult(null);
        setRefusal(res.refusal);
      } else {
        setResult(res);
      }
    } catch {
      setResult(null);
      setFetchError("Ask failed — is the gateway running?");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={cn("flex h-full flex-col gap-3", className)}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-2xs font-semibold uppercase tracking-wider text-subtle">Ask the fleet</h3>
        <span
          aria-label="Asking as"
          title="The signed-in identity. It comes from your credential and cannot be changed here."
          className="rounded border border-border bg-bg px-1.5 py-0.5 text-2xs text-subtle"
        >
          {principal ? `asking as ${principal.label}` : "not signed in"}
        </span>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(q);
        }}
        className="relative"
      >
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          disabled={!canAsk}
          placeholder={canAsk ? "Ask a question the fleet can ground…" : "Asking is unavailable"}
          aria-label="Ask the fleet"
          className="h-10 w-full rounded border border-border bg-bg pl-9 pr-10 text-sm text-fg outline-none transition-colors placeholder:text-subtle focus:border-accent"
        />
        <button
          type="submit"
          disabled={loading || !canAsk}
          aria-label="Ask"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded bg-accent p-1.5 text-accent-fg transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <ArrowRight className="h-4 w-4" />
        </button>
      </form>

      {disabledReason && (
        <div className="flex items-start gap-2 rounded border border-band-spot/30 bg-band-spot/[0.06] px-3 py-2">
          <ShieldQuestion className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
          <span className="text-xs text-fg">{disabledReason}</span>
        </div>
      )}

      <div className={cn("flex flex-wrap gap-1.5", (result !== null || !canAsk) && "hidden")}>
        {suggested.map((s) => (
          <button
            key={s}
            onClick={() => ask(s)}
            disabled={loading || !canAsk}
            className="rounded-full border border-border bg-surface px-2.5 py-1 text-2xs text-subtle transition-colors hover:border-accent/40 hover:text-fg disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
        {loading ? (
          <div className="text-xs text-subtle">Asking the fleet…</div>
        ) : fetchError !== null ? (
          <div className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
            <span className="text-xs text-fg">{fetchError}</span>
          </div>
        ) : refusal !== null ? (
          <div className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
            <div className="text-xs text-fg">
              <div className="font-mono">{refusal.code}</div>
              <div className="mt-0.5 text-subtle">{refusal.message}</div>
            </div>
          </div>
        ) : result ? (
          <div className="space-y-2.5">
            <div className="space-y-1 text-2xs text-subtle">
              <span className="uppercase tracking-wide">Asked</span>
              <p>{result.selection_note}</p>
              {result.trace_id && (
                <a
                  href={`${GATEWAY}/trace/${result.trace_id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block text-accent hover:underline"
                >
                  trace {result.trace_id}
                </a>
              )}
            </div>

            <div className="space-y-2">
              {result.sources.map((o) => (
                <SourceCard key={o.source_id} outcome={o} />
              ))}
            </div>
          </div>
        ) : (
          <div className="text-xs text-subtle">
            Ask a question — every source the fleet consults is shown, answered or refused, with
            pointers you can click through to the source.
          </div>
        )}
      </div>
    </div>
  );
}

export default AskPanel;
