"use client";
import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ColumnDef } from "@tanstack/react-table";
import { Topbar } from "@/components/topbar";
import { MetricTile } from "@/components/metric-tile";
import { DataGrid } from "@/components/data-grid";
import { GATEWAY, authHeaders } from "@/lib/api";
import type { E1PlumbingProof } from "@/lib/api";

const BUCKET_LABEL: Record<string, string> = {
  "lt_0.80": "<0.80 (review)",
  "0.80_0.95": "0.80–0.95 (spot)",
  "gte_0.95": "≥0.95 (auto)",
};

// Keyed by bucket, not position: with empty buckets omitted, index colouring would shift.
const BUCKET_FILL: Record<string, string> = {
  "lt_0.80": "hsl(var(--band-review))",
  "0.80_0.95": "hsl(var(--band-spot))",
  "gte_0.95": "hsl(var(--band-auto))",
};

/** What `/evals/e1` answered. A 404 / 405 means the route is not mounted on this gateway (the
 *  evaluation routes are unwired in this build) — that is a fact to state, not an error to dress up
 *  and not a reason to draw placeholder numbers. */
type E1Load =
  | { state: "loading" }
  | { state: "unwired"; status: number }
  | { state: "error"; message: string }
  | { state: "ok"; e1: E1PlumbingProof };

async function loadE1(): Promise<E1Load> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}/evals/e1`, { headers: { ...authHeaders() }, cache: "no-store" });
  } catch (e) {
    return { state: "error", message: `the gateway could not be reached: ${(e as Error).message}` };
  }
  if (res.status === 404 || res.status === 405) return { state: "unwired", status: res.status };
  if (!res.ok) return { state: "error", message: `/evals/e1 answered ${res.status}` };
  return { state: "ok", e1: (await res.json()) as E1PlumbingProof };
}

export default function EvalsPage() {
  const [load, setLoad] = React.useState<E1Load>({ state: "loading" });
  React.useEffect(() => {
    let live = true;
    loadE1().then((r) => live && setLoad(r));
    return () => {
      live = false;
    };
  }, []);

  if (load.state === "unwired")
    return (
      <>
        <Topbar title="Eval Dashboard" />
        <div className="p-6" data-testid="evals-unwired">
          <div className="max-w-2xl rounded border border-border bg-surface p-4 text-sm text-fg">
            <p className="font-medium">Evaluation routes are not wired in this build.</p>
            <p className="mt-1 text-xs text-subtle">
              The gateway answered {load.status} for <code className="font-mono">/evals/e1</code> (and{" "}
              <code className="font-mono">/evals/brain</code> is unwired too), so there are no eval results to show. No
              figures are drawn in their place.
            </p>
          </div>
        </div>
      </>
    );
  if (load.state === "error")
    return (<><Topbar title="Eval Dashboard" /><div className="p-6 text-sm text-band-review" data-testid="evals-error">{load.message}</div></>);
  if (load.state === "loading")
    return (<><Topbar title="Eval Dashboard" /><div className="p-6 text-sm text-subtle">Loading E1…</div></>);
  const e1 = load.e1;

  // A bucket with no measured accuracy is OMITTED, not drawn as a 0% bar: "no columns landed in
  // this band" and "every column in this band was wrong" are different facts.
  const reliability = Object.entries(e1.reliability)
    .filter(([, v]) => v.accuracy != null)
    .map(([k, v]) => ({
      bucket: BUCKET_LABEL[k] ?? k,
      band: k,
      accuracy: Math.round((v.accuracy as number) * 100),
      n: v.n,
    }));

  const perTable = Object.entries(e1.per_table).map(([table, v]) => ({
    table, accuracy: v.accuracy, n: v.n,
  }));
  const tableCols: ColumnDef<{ table: string; accuracy: number | null; n: number }, unknown>[] = [
    { header: "Table", accessorKey: "table", cell: ({ getValue }) => <span className="font-mono text-2xs">{getValue() as string}</span> },
    { header: "Accuracy", accessorKey: "accuracy", cell: ({ getValue }) => <span className="tnum">{getValue() == null ? "—" : ((getValue() as number) * 100).toFixed(1) + "%"}</span> },
    { header: "Columns", accessorKey: "n", cell: ({ getValue }) => <span className="tnum text-subtle">{getValue() as number}</span> },
  ];

  return (
    <>
      <Topbar title="Eval Dashboard" />
      <div className="min-w-0 flex-1 overflow-auto p-5">
        {/* The label is not decoration. These numbers score the annotator's PLUMBING against a
            deterministic gold backend over a fixed synthetic schema; they say nothing about how
            well a model annotates your data. */}
        <div className="mb-4 rounded border border-band-spot/40 bg-band-spot/10 p-3 text-2xs text-fg" data-testid="evals-plumbing-label">
          <span className="font-semibold">Plumbing proof, not model accuracy.</span>{" "}
          {e1.note ?? "Scored against a deterministic gold backend over a fixed synthetic schema."}
          {e1.backend && <span className="ml-1 font-mono text-subtle">backend: {e1.backend}</span>}
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricTile label="E1 accuracy" value={(e1.value * 100).toFixed(1)} unit="%" accent="ok" sub={`${e1.correct}/${e1.total} columns`} />
          <MetricTile label="role accuracy" value={(e1.role_accuracy * 100).toFixed(1)} unit="%" accent="ok" />
          <MetricTile label="mistakes" value={e1.mistakes.length} accent={e1.mistakes.length ? "warn" : "ok"} />
          <MetricTile label="suite" value={e1.suite} sub={e1.metric.replace(/_/g, " ")} />
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded border border-border bg-surface p-4">
            <div className="mb-3 text-2xs uppercase tracking-wide text-subtle">
              reliability diagram — accuracy by confidence bucket (calibration)
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={reliability} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: "hsl(var(--subtle))" }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "hsl(var(--subtle))" }} />
                <Tooltip
                  contentStyle={{
                    background: "hsl(var(--elevated))",
                    border: "1px solid hsl(var(--border))",
                    color: "hsl(var(--fg))",
                    fontSize: 11,
                  }}
                  formatter={(v: number, _n, p) => [`${v}% (n=${(p.payload as { n: number }).n})`, "accuracy"]}
                />
                <Bar dataKey="accuracy" radius={[3, 3, 0, 0]}>
                  {reliability.map((r, i) => (
                    <Cell key={i} fill={BUCKET_FILL[r.band] ?? "hsl(var(--accent))"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="rounded border border-border bg-surface p-4">
            <div className="mb-3 text-2xs uppercase tracking-wide text-subtle">per-provenance accuracy</div>
            <div className="space-y-3">
              {Object.entries(e1.per_provenance).map(([prov, v]) => (
                <div key={prov}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="font-mono text-fg">{prov}</span>
                    <span className="tnum text-subtle">
                      {v.accuracy == null ? "—" : (v.accuracy * 100).toFixed(1) + "%"} · n={v.n}
                    </span>
                  </div>
                  {v.accuracy != null && (
                    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${v.accuracy * 100}%` }} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-4">
          <div className="mb-2 text-2xs uppercase tracking-wide text-subtle">per-table breakdown</div>
          <DataGrid data={perTable} columns={tableCols} maxHeight={280} />
        </div>
      </div>
    </>
  );
}
