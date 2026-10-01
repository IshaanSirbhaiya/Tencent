"use client";
/**
 * `Verify` — the live half of `/evidence` (`DEMO_AND_FRONTEND.md` §2.3).
 *
 * The four steps, in order, each answered by something that can be wrong:
 *
 * 1. pick a citation a real read emitted → **Verify** → the verdict the adapter returned;
 * 2. change the underlying row;
 * 3. Verify again → `DRIFT_MATERIAL`, old beside new;
 * 4. the reverse index names what is affected — **or says it cannot**.
 *
 * Two rendering rules this component exists to hold:
 *
 * **The eight verdicts stay distinguishable.** `DRIFT_MATERIAL` is alarming, `DRIFT_BENIGN` is
 * not, and `UNVERIFIABLE` reads as an honest limit rather than a failure (§1a). Colour comes from
 * the tone the GATEWAY computed off the enum's own `must_escalate` / `is_positive`; this file adds
 * an icon and a weight so the states are separable without relying on colour alone.
 *
 * **"Unknown" is never drawn as "nothing".** The impact panel has three states and they look
 * different: an index that cannot be reached, an index that admits it has gaps, and an index that
 * is complete over this store and really is telling you nothing cites the value. Collapsing the
 * first two into the third is how a drift alarm becomes falsely reassuring.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertOctagon,
  Archive,
  Ghost,
  HelpCircle,
  Lock,
  ShieldCheck,
  TimerOff,
  Waves,
} from "lucide-react";
import { Panel } from "@/components/platform/preview-notice";
import {
  fetchCitations,
  fetchLattice,
  mutateCell,
  verifyPointer,
  type Citation,
  type ImpactAnswer,
  type VerdictSpec,
  type VerifyResult,
} from "@/lib/verify-api";
import { TONE_TEXT, type Tone } from "@/lib/platform-preview";
import { cn } from "@/lib/utils";

/** One icon per verdict, so the eight are separable without relying on colour alone. */
const VERDICT_ICON: Record<string, typeof ShieldCheck> = {
  verified: ShieldCheck,
  verified_as_of_capture: Archive,
  drift_benign: Waves,
  drift_material: AlertOctagon,
  expired: TimerOff,
  vanished: Ghost,
  forbidden: Lock,
  unverifiable: HelpCircle,
};

/** The one-word consequence, beside the verdict. What a reader has to DO, not how bad it sounds. */
function consequenceOf(v: { verdict: string; must_escalate: boolean; is_positive: boolean }): string {
  if (v.must_escalate) return "escalate — a human must be told";
  if (v.is_positive) return "the citation holds";
  if (v.verdict === "unverifiable") return "an honest limit — nobody is paged";
  if (v.verdict === "drift_benign") return "nothing we cited moved — nobody is paged";
  return "schedules work — resync or mark unverifiable";
}

const TONE_RING: Record<Tone, string> = {
  good: "border-band-auto/50",
  warn: "border-band-spot/50",
  bad: "border-band-review",
  neutral: "border-border",
};

const TONE_WASH: Record<Tone, string> = {
  good: "bg-band-auto/[0.06]",
  warn: "bg-band-spot/[0.06]",
  bad: "bg-band-review/[0.10]",
  neutral: "bg-muted",
};

function plain(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// ============================================================================================
// the verdict
// ============================================================================================
function VerdictCard({ result }: { result: VerifyResult }) {
  const Icon = VERDICT_ICON[result.verdict] ?? HelpCircle;
  const escalates = result.must_escalate;
  return (
    <div
      data-testid="verify-verdict"
      data-verdict={result.verdict}
      data-tone={result.tone}
      className={cn(
        "rounded-lg border p-4",
        TONE_RING[result.tone],
        TONE_WASH[result.tone],
        escalates && "border-l-4",
        result.verdict === "unverifiable" && "border-dashed",
      )}
    >
      <div className="flex items-start gap-2.5">
        <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", TONE_TEXT[result.tone])} />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span
              className={cn(
                "font-mono text-sm tracking-tight",
                TONE_TEXT[result.tone],
                escalates ? "font-bold" : "font-medium",
              )}
            >
              {result.name}
            </span>
            <span className="text-2xs text-subtle">{consequenceOf(result)}</span>
          </p>
          <p className="mt-1 text-2xs text-subtle">{result.meaning}</p>
          {result.note ? (
            <p className="mt-1.5 rounded border border-border bg-surface px-2 py-1 text-2xs text-fg">
              {result.note}
            </p>
          ) : null}
          <p className="mt-1.5 text-2xs text-subtle/80">
            Re-executed{result.verified_by ? <> by <code className="text-fg">{result.verified_by}</code></> : null}{" "}
            at <span className="tabular-nums">{result.verified_at.slice(0, 19).replace("T", " ")}</span>Z
          </p>
        </div>
      </div>

      {/* Old beside new. Only a material drift gets a second read, and the "old" half is the value
          the gateway itself emitted when it served the citation — not one the browser sent back. */}
      {escalates && result.cited_value_known ? (
        <dl
          data-testid="verify-diff"
          className="mt-3 grid grid-cols-1 gap-2 border-t border-band-review/30 pt-3 sm:grid-cols-2"
        >
          <div className="rounded border border-border bg-surface px-2.5 py-1.5">
            <dt className="text-2xs uppercase tracking-wider text-subtle">as cited</dt>
            <dd className="font-mono text-xs tabular-nums text-fg">{plain(result.cited_value)}</dd>
          </div>
          <div className="rounded border border-band-review/40 bg-surface px-2.5 py-1.5">
            <dt className="text-2xs uppercase tracking-wider text-band-review">source says now</dt>
            <dd className="font-mono text-xs tabular-nums text-band-review">
              {result.current_value_known ? plain(result.current_value) : "not re-read"}
            </dd>
            {!result.current_value_known ? (
              <p className="mt-0.5 text-2xs text-subtle">
                the re-read did not cover that row — that is &ldquo;we did not look&rdquo;, not
                &ldquo;it is empty&rdquo;
              </p>
            ) : null}
          </div>
        </dl>
      ) : null}
    </div>
  );
}

// ============================================================================================
// step 4 — what relies on this
// ============================================================================================
function ImpactPanel({ impact }: { impact: ImpactAnswer }) {
  const unknown = impact.answer !== "known";
  const count = impact.records.length + impact.emissions.length;

  return (
    <div
      data-testid="verify-impact"
      data-impact-answer={impact.answer}
      className={cn(
        "rounded-lg border p-3",
        unknown ? "border-band-spot/50 bg-band-spot/[0.06]" : "border-border bg-surface",
      )}
    >
      <p className="flex items-center gap-2 text-xs font-medium">
        {unknown ? (
          <>
            <HelpCircle className="h-3.5 w-3.5 shrink-0 text-band-spot" />
            <span className="text-band-spot">
              {impact.answer === "unavailable"
                ? "impact unknown — index unavailable"
                : "impact unknown — index incomplete"}
            </span>
          </>
        ) : count > 0 ? (
          <>
            <AlertOctagon className="h-3.5 w-3.5 shrink-0 text-band-review" />
            <span className="text-band-review">
              {count} record{count === 1 ? "" : "s"} cite a value that has since changed
            </span>
          </>
        ) : (
          <>
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-band-auto" />
            <span className="text-band-auto">nothing in this store cites this pointer</span>
          </>
        )}
      </p>

      {/* The reason is always rendered. An empty list without one reads as reassurance, and the
          two sentences "I looked and found nothing" and "I cannot look" are different answers. */}
      <p className="mt-1 text-2xs text-subtle">{impact.reason}</p>

      {impact.records.length ? (
        <ul className="mt-2 divide-y divide-border rounded border border-border" data-testid="impact-records">
          {impact.records.slice(0, 12).map((r, i) => (
            <li key={`${r.entity_id}-${r.field ?? "entity"}-${i}`} className="px-2.5 py-1.5">
              <p className="flex flex-wrap items-baseline gap-x-2">
                <span className="rounded border border-border px-1 font-mono text-2xs text-subtle">
                  {r.entity_kind}
                </span>
                <span className="font-mono text-2xs text-fg">{r.kind ?? "—"}</span>
                <span className="truncate text-2xs text-subtle">{r.label ?? r.entity_id}</span>
                {!r.is_open ? (
                  <span className="text-2xs text-subtle/70">(closed version — no action)</span>
                ) : null}
              </p>
              {r.field ? (
                <p className="mt-0.5 pl-1 text-2xs text-subtle">
                  <code className="text-fg">{r.field}</code> = {plain(r.value)}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {impact.records.length > 12 ? (
        <p className="mt-1 text-2xs text-subtle">
          …and {impact.records.length - 12} more.
        </p>
      ) : null}

      {impact.emissions.length ? (
        <ul className="mt-2 flex flex-col gap-1" data-testid="impact-emissions">
          {impact.emissions.map((e) => (
            <li key={e.ref} className="text-2xs text-subtle">
              <span className="font-mono text-fg">{e.kind}</span> {e.ref}
              {e.ledger_bound ? <span className="text-band-review"> · ledger-bound</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {impact.coverage ? (
        <p className="mt-2 text-2xs text-subtle/80 tabular-nums">
          Index: {impact.coverage.pointers ?? 0} pointer(s), {impact.coverage.bindings ?? 0}{" "}
          binding(s), {impact.coverage.emissions ?? 0} emission(s)
          {impact.coverage.unindexed_pointers
            ? `, ${impact.coverage.unindexed_pointers} never indexed`
            : ""}
          .
        </p>
      ) : null}
    </div>
  );
}

// ============================================================================================
// the lattice
// ============================================================================================
function Lattice({ verdicts, live, reason }: { verdicts: VerdictSpec[]; live: boolean; reason: string | null }) {
  return (
    <div>
      <p className="text-2xs text-subtle">
        {live ? (
          <>
            Served by <code className="text-fg">GET /evidence/verify/lattice</code> — the frontend
            keeps no copy of a closed vocabulary.
          </>
        ) : (
          <>
            Gateway unreachable ({reason}); showing the bundled copy of the lattice. These are the
            state names, not a measurement of anything.
          </>
        )}
      </p>
      <dl className="mt-2 divide-y divide-border rounded border border-border" data-testid="verdict-lattice">
        {verdicts.map((v) => {
          const Icon = VERDICT_ICON[v.verdict] ?? HelpCircle;
          return (
            <div key={v.verdict} className="flex flex-wrap items-baseline gap-x-3 px-3 py-1.5">
              <dt
                data-verdict={v.verdict}
                className={cn(
                  "flex w-52 shrink-0 items-center gap-1.5 font-mono text-2xs",
                  TONE_TEXT[v.tone],
                  v.must_escalate && "font-bold",
                )}
              >
                <Icon className="h-3 w-3 shrink-0" />
                {v.name}
              </dt>
              <dd className="min-w-0 flex-1 text-2xs text-subtle">
                {v.meaning}
                {v.requires_advertised_capability ? (
                  <span className="text-subtle/70">
                    {" "}
                    Only emitted by a source that advertises the change signal behind it.
                  </span>
                ) : null}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

// ============================================================================================
// the surface
// ============================================================================================
export function VerifyPanel() {
  const qc = useQueryClient();
  const [selected, setSelected] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<VerifyResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [newValue, setNewValue] = React.useState("");

  const lattice = useQuery({ queryKey: ["verify-lattice"], queryFn: () => fetchLattice() });
  const citations = useQuery({
    queryKey: ["verify-citations"],
    queryFn: () => fetchCitations(false),
    retry: false,
  });

  const rows = citations.data?.citations ?? [];
  const fleet = citations.data?.fleet;
  const active: Citation | undefined =
    rows.find((c) => c.citation_id === selected) ?? rows[0];

  const verify = useMutation({
    mutationFn: (c: Citation) => verifyPointer(c.pointer),
    onSuccess: (r) => {
      setResult(r);
      setError(null);
    },
    onError: (e: Error) => {
      // A failed request is NOT a verdict. The previous one is cleared rather than left on
      // screen beside an error, which would read as "still verified".
      setResult(null);
      setError(e.message);
    },
  });

  const mutate = useMutation({
    mutationFn: ({ c, v }: { c: Citation; v: string }) => mutateCell(c.pointer, v),
    onSuccess: () => {
      setError(null);
      // Refetch so the change log below updates. The citation list deliberately keeps showing
      // `value_at_read` — that is what the pointer vouches for, and swapping it for the new value
      // would quietly resolve the very disagreement the next Verify is about to report.
      qc.invalidateQueries({ queryKey: ["verify-citations"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <section className="flex flex-col gap-3" data-testid="verify-panel">
      <header>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-subtle">
          Verify — re-execute one pointer
        </h2>
        <p className="mt-1 max-w-3xl text-xs text-subtle">
          Every citation below was minted by a real read through the five-verb adapter port, under
          the estate&apos;s own policy. <span className="text-fg">Verify</span> re-executes the
          pointer against the source and answers from the verdict lattice — never a boolean.
        </p>
      </header>

      {/* What is behind the button. A verdict whose provenance is invisible is a label. */}
      {fleet ? (
        <div
          data-testid="verify-fleet"
          className={cn(
            "rounded border px-3 py-2 text-2xs",
            fleet.available
              ? "border-band-auto/40 bg-band-auto/[0.06]"
              : "border-band-review/40 bg-band-review/[0.06]",
          )}
        >
          {fleet.available ? (
            <>
              <span className="font-medium text-fg">Live.</span> {fleet.estate}. Reads run under{" "}
              <code className="text-fg">{fleet.read_purpose}</code>; verification under{" "}
              <code className="text-fg">{fleet.verify_purpose}</code>.
              <ul className="mt-1 flex flex-col gap-0.5">
                {fleet.sources.map((s) => (
                  <li key={s.source_id} className="text-subtle">
                    <code className="text-fg">{s.source_id}</code> · tier {s.pinning_tier} ·{" "}
                    egress {s.egress_class} · can return{" "}
                    <span className="font-mono">{s.reachable_verdicts.join(", ")}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-subtle/80">
                The estate is a private working copy; the repository fixture is never written.
              </p>
            </>
          ) : (
            <>
              <span className="font-medium text-band-review">
                No adapter is available to verify against.
              </span>{" "}
              {fleet.reason}
            </>
          )}
        </div>
      ) : null}

      {citations.isError ? (
        <p className="text-xs text-band-review" role="alert">
          {(citations.error as Error).message}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* ---- the citations a real read produced ---- */}
        <Panel title="Citations" hint={`${rows.length} cell${rows.length === 1 ? "" : "s"}`}>
          {citations.isLoading ? (
            <p className="text-2xs text-subtle">Reading the estate…</p>
          ) : rows.length === 0 ? (
            <p className="text-2xs text-subtle">
              No citation has been served. With no read there is nothing to verify — which is the
              true answer, not an empty state.
            </p>
          ) : (
            <ul className="flex flex-col gap-1" data-testid="citation-list">
              {rows.map((c) => {
                const isActive = c.citation_id === active?.citation_id;
                return (
                  <li key={c.citation_id}>
                    <button
                      onClick={() => {
                        setSelected(c.citation_id);
                        setResult(null);
                        setError(null);
                      }}
                      data-testid="citation-row"
                      className={cn(
                        "w-full rounded border px-2.5 py-1.5 text-left transition-colors",
                        isActive
                          ? "border-accent/60 bg-muted"
                          : "border-border hover:border-accent/40",
                      )}
                    >
                      <span className="flex flex-wrap items-baseline gap-x-2">
                        <code className="text-2xs text-fg">
                          {c.entity}.{c.column}
                        </code>
                        <span className="text-2xs text-subtle">{c.row_id}</span>
                        <span
                          className={cn(
                            "ml-auto rounded border px-1 text-2xs",
                            c.trust === "trusted"
                              ? "border-band-auto/40 text-band-auto"
                              : "border-band-spot/40 text-band-spot",
                          )}
                        >
                          {c.trust ?? "unlabelled"}
                        </span>
                      </span>
                      <span className="mt-0.5 block font-mono text-xs tabular-nums text-fg">
                        {plain(c.value_at_read)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {active ? (
            <>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  onClick={() => verify.mutate(active)}
                  disabled={verify.isPending}
                  data-testid="verify-button"
                  className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50"
                >
                  {verify.isPending ? "Verifying…" : "Verify"}
                </button>
                <input
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  placeholder="new value"
                  data-testid="mutate-input"
                  className="w-32 rounded border border-border bg-surface px-2 py-1 text-2xs outline-none focus:border-accent"
                />
                <button
                  onClick={() => mutate.mutate({ c: active, v: newValue })}
                  disabled={!newValue || mutate.isPending}
                  data-testid="mutate-button"
                  className="rounded border border-band-spot/50 px-2.5 py-1.5 text-2xs text-band-spot hover:bg-band-spot/[0.08] disabled:opacity-40"
                >
                  {mutate.isPending ? "Changing…" : "Change the row"}
                </button>
              </div>
              <p className="mt-1.5 text-2xs text-subtle">
                &ldquo;Change the row&rdquo; writes to this deployment&apos;s private copy of the
                synthetic estate and to nothing else. It appends no ledger entry and asserts nothing
                about anybody&apos;s identity — it exists so the next verdict is a{" "}
                <span className="text-fg">detection</span> rather than a slide.
              </p>
              <details className="mt-2">
                <summary className="cursor-pointer text-2xs text-subtle hover:text-fg">
                  The SourcePointer, as stored
                </summary>
                <pre className="mt-1 overflow-x-auto rounded bg-bg p-2 font-mono text-2xs text-fg">
                  {JSON.stringify(active.pointer, null, 2)}
                </pre>
              </details>
            </>
          ) : null}
        </Panel>

        {/* ---- the verdict, and what relies on it ---- */}
        <div className="flex flex-col gap-3">
          {error ? (
            <p
              className="rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-2xs text-band-review"
              role="alert"
            >
              {error}
              <span className="mt-0.5 block text-subtle">
                An error is not a state of the lattice, so no verdict is shown.
              </span>
            </p>
          ) : null}

          {result ? (
            <>
              <VerdictCard result={result} />
              <ImpactPanel impact={result.impact} />
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-border p-4 text-2xs text-subtle">
              Nothing has been verified yet. Pick a citation and press{" "}
              <span className="text-fg">Verify</span>; then change the row and press it again.
            </div>
          )}

          {fleet?.mutations?.length ? (
            <div className="rounded border border-border px-3 py-2 text-2xs" data-testid="mutation-log">
              <p className="font-medium text-fg">Changes made from this screen</p>
              <ul className="mt-1 flex flex-col gap-0.5">
                {fleet.mutations.map((m, i) => (
                  <li key={`${m.at}-${i}`} className="text-subtle tabular-nums">
                    <code className="text-fg">
                      {m.entity}.{m.column}
                    </code>{" "}
                    {m.row_id}: {plain(m.from)} → {plain(m.to)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </div>

      <div className="border-t border-border pt-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-subtle">
          The verdict lattice
        </h3>
        {lattice.data ? (
          <Lattice
            verdicts={lattice.data.verdicts}
            live={lattice.data.live}
            reason={lattice.data.reason}
          />
        ) : (
          <p className="mt-1 text-2xs text-subtle">Loading the lattice…</p>
        )}
      </div>
    </section>
  );
}
