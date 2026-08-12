"use client";
/**
 * `/impact` — where the minutes went.
 *
 * Every figure on this page is computed server-side from real intakes. There are no literals in
 * this JSX, which is the point: a dashboard whose headline number is hardcoded is worth nothing to
 * anyone assessing it, and the difference is invisible in a screenshot.
 *
 * The identity row is deliberately the most prominent thing in the table rather than a footnote.
 * A step we explicitly refuse to automate, shown as zero saved next to eight steps that do save,
 * is the clearest single statement of what this system is — and turning that constraint into a
 * visible feature is the whole argument.
 */
import { useQuery } from "@tanstack/react-query";
import { Lock, TrendingUp } from "lucide-react";
import { GATEWAY } from "@/lib/api";
import { cn } from "@/lib/utils";

interface BaselineStep {
  step: number;
  name: string;
  baseline_min: number;
  with_copilot_min: number;
  max_saving_min: number;
  automatable: boolean;
  never_automatable: boolean;
  note: string;
}

interface ImpactResponse {
  baseline: {
    total_min: number;
    patients_per_morning: number;
    band_min_total: number;
    band_max_total: number;
    steps: BaselineStep[];
  };
  measured: {
    intakes: number;
    saved_min: number;
    saved_pct: number;
    mean_saved_min: number;
    patients_per_morning: number;
    projected_morning_hours_saved: number;
    per_step: { step: number; saved_min: number; claimed_count: number }[];
  };
}

async function fetchImpact(): Promise<ImpactResponse> {
  const res = await fetch(`${GATEWAY}/impact`, { cache: "no-store" });
  if (!res.ok) throw new Error(`/impact → ${res.status}`);
  return res.json();
}

/** A proportional bar. Width is the fraction of the longest baseline step, so the eye compares
 *  steps to each other rather than to an arbitrary container. */
function Bar({ baseline, saved, max }: { baseline: number; saved: number; max: number }) {
  const total = (baseline / max) * 100;
  const savedPct = baseline > 0 ? (saved / baseline) * 100 : 0;
  return (
    <div className="h-3 w-full rounded-sm bg-muted/60" style={{ maxWidth: `${total}%` }}>
      <div
        className={cn("h-3 rounded-sm", saved > 0 ? "bg-band-auto" : "bg-band-review/50")}
        style={{ width: `${savedPct}%` }}
      />
    </div>
  );
}

export default function ImpactPage() {
  const q = useQuery({ queryKey: ["impact"], queryFn: fetchImpact, refetchInterval: 10_000 });

  if (q.isError) {
    return (
      <p className="p-6 text-xs text-band-review" role="alert">
        Could not reach the gateway: {(q.error as Error).message}
      </p>
    );
  }
  // Guard on the DATA, not on `isLoading`. A query paused by react-query's `networkMode: "online"`
  // (browser offline, gateway unreachable at mount) reports `isPending` without `isFetching` and
  // without `isError`, so an `isLoading`-only guard falls through and destructuring undefined
  // white-screens the page instead of showing the message it already has.
  if (!q.data) return <p className="p-6 text-xs text-subtle">Measuring…</p>;

  const { baseline, measured } = q.data;
  const baselineBand = `${baseline.band_min_total}–${baseline.band_max_total}`;
  const savedByStep = new Map(measured.per_step.map((s) => [s.step, s]));
  // `Math.max(...[])` is -Infinity, which would make every bar width NaN%.
  const longest = Math.max(1, ...baseline.steps.map((s) => s.baseline_min));

  return (
    <div className="flex flex-col gap-5 p-6">
      <header>
        <h1 className="text-lg font-semibold">Impact</h1>
        <p className="text-xs text-subtle">
          Measured over the {measured.intakes} intake{measured.intakes === 1 ? "" : "s"} currently
          read by this system. Savings are claimed per stage, and only for stages that actually
          completed — an intake whose coverage could not be determined claims nothing for
          eligibility, billing or the claim draft.
        </p>
      </header>

      {measured.intakes === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-xs text-subtle">
          No intakes measured yet. Read a document from <code>/intake</code> and the numbers here
          compute from it — there is nothing hardcoded on this page to show in the meantime.
        </p>
      ) : (
        <div className="flex flex-wrap gap-3">
          <div className="rounded-lg border border-border bg-surface px-4 py-3">
            <div className="tnum text-2xl font-semibold">{measured.mean_saved_min}</div>
            <div className="text-2xs text-subtle">minutes saved per patient (mean)</div>
          </div>
          <div className="rounded-lg border border-border bg-surface px-4 py-3">
            <div className="tnum text-2xl font-semibold">{measured.saved_pct}%</div>
            <div className="text-2xs text-subtle">of the {baseline.total_min}-minute baseline</div>
          </div>
          <div className="rounded-lg border border-border bg-surface px-4 py-3">
            <div className="tnum flex items-center gap-1.5 text-2xl font-semibold text-band-auto">
              <TrendingUp className="h-5 w-5" />
              {measured.projected_morning_hours_saved}
            </div>
            <div className="text-2xs text-subtle">
              hours per morning, projected at {measured.patients_per_morning} patients
            </div>
          </div>
        </div>
      )}

      <section>
        <h2 className="mb-2 text-sm font-semibold">Where the minutes go</h2>
        <table className="w-full text-left text-xs">
          <thead className="text-2xs uppercase tracking-wide text-subtle">
            <tr className="border-b border-border">
              <th className="py-1.5 pr-2 font-medium">Step</th>
              <th className="px-2 py-1.5 font-medium">Baseline</th>
              <th className="px-2 py-1.5 font-medium">Saved (measured)</th>
              <th className="px-2 py-1.5 font-medium">Claimed on</th>
              <th className="w-1/3 px-2 py-1.5 font-medium">Proportion</th>
            </tr>
          </thead>
          <tbody data-testid="impact-steps">
            {baseline.steps.map((s) => {
              const row = savedByStep.get(s.step);
              const saved = row?.saved_min ?? 0;
              const perIntake = measured.intakes > 0 ? saved / measured.intakes : 0;
              return (
                <tr
                  key={s.step}
                  data-testid={`impact-step-${s.step}`}
                  className={cn(
                    "border-b border-border/60",
                    s.never_automatable && "bg-band-review/[0.06]",
                  )}
                >
                  <td className="py-2 pr-2">
                    <span className="flex items-start gap-1.5">
                      {s.never_automatable && (
                        <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
                      )}
                      <span>
                        <span className="block">{s.name}</span>
                        {s.never_automatable && (
                          <span className="block text-2xs font-medium text-band-review">
                            never automated — verified in person by staff, by design
                          </span>
                        )}
                      </span>
                    </span>
                  </td>
                  <td className="tnum px-2 py-2">{s.baseline_min} min</td>
                  <td className="tnum px-2 py-2">
                    {/* Rendered from the measurement even for the identity row. Writing "0.0 min"
                        as a literal here made the e2e test that guards hard constraint 1 vacuous:
                        it would have passed even if the backend started crediting the step. */}
                    <span
                      className={cn(
                        s.never_automatable
                          ? "font-medium text-band-review"
                          : perIntake > 0
                            ? "text-band-auto"
                            : "text-subtle",
                      )}
                    >
                      {perIntake.toFixed(1)} min
                    </span>
                  </td>
                  <td className="tnum px-2 py-2 text-subtle">
                    {s.never_automatable
                      ? "—"
                      : `${row?.claimed_count ?? 0} / ${measured.intakes}`}
                  </td>
                  <td className="px-2 py-2">
                    <Bar baseline={s.baseline_min} saved={perIntake} max={longest} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <p className="max-w-3xl text-2xs text-subtle">
        The baseline is the midpoint of the {baselineBand} minute band in the Technical Problem Statement;
        the &ldquo;with copilot&rdquo; figures are our estimates. Both live in{" "}
        <code>config/time_baseline.yaml</code> so they can be argued with. The identity row cannot
        be made non-zero by editing that file — the saving is refused in code before any arithmetic
        runs.
      </p>
    </div>
  );
}
