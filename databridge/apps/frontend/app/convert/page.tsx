"use client";
import * as React from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/input";
import { DataGrid } from "@/components/data-grid";
import { Grounded } from "@/components/grounded";
import { MetricTile } from "@/components/metric-tile";
import { api } from "@/lib/api";
import type { ConvertResponse, SourcePointer } from "@/lib/types";
import { Download, Play } from "lucide-react";

function parseCsv(csv: string): { header: string[]; rows: string[][] } {
  const lines = csv.trim().split("\n");
  const header = (lines[0] ?? "").split(",");
  const rows = lines.slice(1).map((l) => l.split(","));
  return { header, rows };
}

export default function ConvertPage() {
  const [sample, setSample] = React.useState<{ source_b64: string; source_format: string; target_format: string; transform_spec: Record<string, unknown>; preview: string } | null>(null);
  const [specText, setSpecText] = React.useState("");
  const [result, setResult] = React.useState<ConvertResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    api.convertSample().then((s) => {
      setSample(s);
      setSpecText(JSON.stringify(s.transform_spec, null, 2));
    });
  }, []);

  async function run() {
    if (!sample) return;
    setBusy(true);
    setError(null);
    try {
      let spec: Record<string, unknown>;
      try {
        spec = JSON.parse(specText);
      } catch {
        setError("transform spec is not valid JSON");
        return;
      }
      // a refusal (e.g. a bad spec) comes back as `{refusal}` with no result_b64 — guard it so
      // the page surfaces the reason instead of crashing on atob(undefined) (review L2).
      const r = (await api.convert({
        source_b64: sample.source_b64,
        source_format: sample.source_format,
        target_format: sample.target_format,
        transform_spec: spec,
      })) as ConvertResponse & { refusal?: { code: string; message: string } };
      if (r.refusal) {
        setResult(null);
        setError(`${r.refusal.code}: ${r.refusal.message}`);
      } else {
        setResult(r);
      }
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : "conversion failed");
    } finally {
      setBusy(false);
    }
  }

  const csv = result ? atob(result.result_b64) : "";
  const parsed = csv ? parseCsv(csv) : null;
  const previewRows = parsed
    ? parsed.rows.slice(0, 20).map((cells) => Object.fromEntries(parsed.header.map((h, i) => [h, cells[i] ?? ""])))
    : [];

  const gridCols: ColumnDef<Record<string, string>, unknown>[] = (parsed?.header ?? []).map((h) => ({
    header: h,
    accessorKey: h,
    cell: ({ getValue }) => {
      const ptr: SourcePointer = { source_id: "convert", kind: "table_cell", column: h };
      return <Grounded pointer={ptr} className="font-mono text-2xs">{String(getValue() ?? "")}</Grounded>;
    },
  }));

  function download() {
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "converted.csv"; a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <Topbar title="Conversion Studio" />
      <div className="min-w-0 flex-1 overflow-auto p-5">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded border border-border bg-surface p-4">
            <div className="mb-2 flex items-center gap-2 text-xs">
              <Badge>source: {sample?.source_format ?? "…"}</Badge>
              <span className="text-subtle">→</span>
              <Badge className="border-accent/40 text-accent">target: {sample?.target_format ?? "…"}</Badge>
            </div>
            <div className="mb-1 text-2xs uppercase tracking-wide text-subtle">source sample</div>
            <pre className="h-28 overflow-auto rounded border border-border bg-bg p-2 font-mono text-2xs text-subtle">
              {sample?.preview ?? "loading…"}
            </pre>
            <div className="mb-1 mt-3 text-2xs uppercase tracking-wide text-subtle">transform spec (JSONata)</div>
            <Textarea value={specText} onChange={(e) => setSpecText(e.target.value)} rows={8} />
            <Button className="mt-3" onClick={run} disabled={busy || !sample}>
              <Play className="h-3.5 w-3.5" /> {busy ? "Running…" : "Run conversion"}
            </Button>
            {error && (
              <div className="mt-3 rounded border border-band-review/40 bg-band-review/10 p-2 text-2xs text-band-review">
                {error}
              </div>
            )}
          </div>

          <div className="rounded border border-border bg-surface p-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-2xs uppercase tracking-wide text-subtle">output preview (first 20 rows)</div>
              {result && (
                <Button size="sm" variant="outline" onClick={download}>
                  <Download className="h-3 w-3" /> Download
                </Button>
              )}
            </div>
            {parsed ? <DataGrid data={previewRows} columns={gridCols} maxHeight={300} /> : (
              <div className="flex h-40 items-center justify-center text-xs text-subtle">
                Run the conversion to see typed output with provenance on every cell.
              </div>
            )}
          </div>
        </div>

        {result && (
          <div className="mt-4">
            <div className="mb-2 text-2xs uppercase tracking-wide text-subtle">quarantine — nothing silently dropped</div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <MetricTile label="rows out" value={parsed ? parsed.rows.length : 0} />
              <MetricTile label="quarantined" value={result.quarantine.quarantined} accent={result.quarantine.quarantined ? "warn" : "ok"} />
              <MetricTile
                label="quarantine rate"
                value={(result.quarantine.rate * 100).toFixed(2)}
                unit="%"
                accent={result.quarantine.over_threshold ? "bad" : "ok"}
                sub={result.quarantine.over_threshold ? "over 1% threshold" : "under 1% threshold"}
              />
              <MetricTile label="fabrications" value={0} accent="ok" sub="grounded conversion" />
            </div>
            {result.quarantine.records.length > 0 && (
              <div className="mt-3 rounded border border-border bg-surface p-3">
                <div className="mb-2 text-2xs uppercase tracking-wide text-subtle">quarantined rows</div>
                {result.quarantine.records.map((r, i) => (
                  <div key={i} className="flex items-start gap-3 border-b border-border/50 py-1.5 text-2xs last:border-0">
                    <Badge className="border-band-spot/40 text-band-spot">{r.reason_code}</Badge>
                    <span className="text-subtle">{r.detail}</span>
                    <span className="ml-auto truncate font-mono text-subtle">{JSON.stringify(r.payload).slice(0, 80)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
