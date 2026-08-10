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
import { api } from "@/lib/api";
import type { E1Result } from "@/lib/types";

const BUCKET_LABEL: Record<string, string> = {
  "lt_0.80": "<0.80 (review)",
  "0.80_0.95": "0.80–0.95 (spot)",
  "gte_0.95": "≥0.95 (auto)",
};

export default function EvalsPage() {
  const [e1, setE1] = React.useState<E1Result | null>(null);
  React.useEffect(() => {
    api.evalsE1().then(setE1);
  }, []);

  if (!e1) return (<><Topbar title="Eval Dashboard" /><div className="p-6 text-sm text-subtle">Loading E1…</div></>);

  const reliability = Object.entries(e1.reliability).map(([k, v]) => ({
    bucket: BUCKET_LABEL[k] ?? k,
    accuracy: v.accuracy == null ? 0 : Math.round(v.accuracy * 100),
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
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3a" />
                <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: "#9ca3af" }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "#9ca3af" }} />
                <Tooltip
                  contentStyle={{ background: "#16191f", border: "1px solid #2a2f3a", fontSize: 11 }}
                  formatter={(v: number, _n, p) => [`${v}% (n=${(p.payload as { n: number }).n})`, "accuracy"]}
                />
                <Bar dataKey="accuracy" radius={[3, 3, 0, 0]}>
                  {reliability.map((_, i) => (
                    <Cell key={i} fill={["#dc2626", "#d97706", "#16a34a"][i] ?? "#2563EB"} />
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
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${(v.accuracy ?? 0) * 100}%` }} />
                  </div>
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
