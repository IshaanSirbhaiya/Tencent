"use client";
/**
 * The direct converter — your own data in, another format out. Deterministic: the router + rule
 * engine behind `POST /convert`, no model. Moved here from `/convert` (C5's page) unchanged in what
 * it sends; the preview now reads every output format rather than assuming CSV.
 *
 * The formats are the ones `POST /convert` routes today (structured → structured). The gateway has
 * no route that lists them, so they are named here; see the Data page's backend-gaps note.
 */
import * as React from "react";
import { Download, Play, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/input";
import { dataApi, saveBlob } from "@/lib/data-api";
import {
  convertFingerprint,
  decodeConvertResponse,
  latestOnly,
  payloadPreview,
  quarantineThresholdText,
  type QuarantineWithPolicy,
} from "./receipt-gate";

const SOURCE_FORMATS = ["json", "jsonl", "csv"] as const;
const TARGET_FORMATS = ["csv", "json", "jsonl"] as const;
const MIME: Record<string, string> = { csv: "text/csv", json: "application/json", jsonl: "application/x-ndjson" };

/** The identity mapping (`$` = every record as it is). A real JSONata spec, not a canned output. */
const IDENTITY_SPEC = JSON.stringify({ language: "jsonata", expression: "$", coercions: [], unmappable: [] }, null, 2);

function toB64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}

/** RFC-4180-ish: quoted fields, doubled quotes, commas and newlines inside quotes. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += c;
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/** Output → { header, rows } for the preview, or null when it is not a table (shown raw). */
function tableOf(text: string, format: string): { header: string[]; rows: string[][]; total: number } | null {
  try {
    if (format === "csv") {
      const all = parseCsv(text.replace(/\s+$/, ""));
      if (all.length === 0) return null;
      return { header: all[0], rows: all.slice(1), total: all.length - 1 };
    }
    const records: unknown[] =
      format === "jsonl"
        ? text.split(/\r?\n/).filter((l) => l.trim()).map((l) => JSON.parse(l))
        : (() => {
            const v = JSON.parse(text);
            return Array.isArray(v) ? v : [v];
          })();
    const objs = records.filter((r): r is Record<string, unknown> => !!r && typeof r === "object" && !Array.isArray(r));
    if (objs.length !== records.length) return null;
    const header = Array.from(new Set(objs.flatMap((o) => Object.keys(o))));
    return { header, rows: objs.map((o) => header.map((h) => cellText(o[h]))), total: objs.length };
  } catch {
    return null;
  }
}

export function Converter() {
  const [sourceText, setSourceText] = React.useState("");
  const [fileName, setFileName] = React.useState<string | null>(null);
  const [sourceFormat, setSourceFormat] = React.useState<string>("json");
  const [targetFormat, setTargetFormat] = React.useState<string>("csv");
  const [specText, setSpecText] = React.useState("");
  // The result carries the fingerprint of the inputs that produced it; when the controls no longer
  // match, the result is stale and is neither previewed nor downloadable (R-021 finding 7).
  // The result is decoded and validated in run() before it is committed — render never decodes
  // (R-021 round 2, finding 6).
  const [result, setResult] = React.useState<{
    text: string;
    quarantine: QuarantineWithPolicy;
    artifact_id: string;
    target: string;
    fingerprint: string;
  } | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  // Every file selection, and every edit of the source, starts a new generation; only the latest
  // read may write the source, its name and its format (R-021 round 2, finding 5).
  const reads = React.useRef(latestOnly());

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = ""; // choosing the same file again is a new selection
    if (!f) return;
    const token = reads.current.begin();
    const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
    try {
      const text = await f.text();
      if (!reads.current.current(token)) return; // a newer selection or edit superseded this read
      if ((SOURCE_FORMATS as readonly string[]).includes(ext)) setSourceFormat(ext);
      setSourceText(text);
      setFileName(f.name);
      setError(null);
    } catch {
      if (reads.current.current(token)) setError(`could not read ${f.name}`);
    }
  }

  const fingerprint = convertFingerprint({ source: sourceText, sourceFormat, targetFormat, spec: specText });
  const stale = !!result && result.fingerprint !== fingerprint;

  async function run() {
    if (!sourceText.trim() || !specText.trim()) return;
    const runFingerprint = fingerprint;
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(specText);
    } catch {
      setError("the transform spec is not valid JSON");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r: unknown = await dataApi.convert({
        source_b64: toB64(sourceText),
        source_format: sourceFormat,
        target_format: targetFormat,
        transform_spec: spec,
      });
      const d = decodeConvertResponse(r);
      if (!d.ok) {
        setResult(null);
        setError(d.error);
      } else {
        setResult({ text: d.text, quarantine: d.quarantine, artifact_id: d.artifactId, target: targetFormat, fingerprint: runFingerprint });
      }
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : "conversion failed");
    } finally {
      setBusy(false);
    }
  }

  const outText = result ? result.text : "";
  const table = result ? tableOf(outText, result.target) : null;
  const byReason = result ? Object.entries(result.quarantine.by_reason ?? {}) : [];
  const RAW_LIMIT = 20000;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label className="flex items-center gap-1">
            <span className="text-subtle">from</span>
            <select
              data-testid="convert-source-format"
              value={sourceFormat}
              onChange={(e) => setSourceFormat(e.target.value)}
              className="rounded border border-border bg-bg px-1 py-0.5 text-2xs"
            >
              {SOURCE_FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <span className="text-subtle">→</span>
          <label className="flex items-center gap-1">
            <span className="text-subtle">to</span>
            <select
              data-testid="convert-target-format"
              value={targetFormat}
              onChange={(e) => setTargetFormat(e.target.value)}
              className="rounded border border-border bg-bg px-1 py-0.5 text-2xs"
            >
              {TARGET_FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label className="ml-auto flex cursor-pointer items-center gap-1 text-2xs text-subtle hover:text-fg">
            <Upload className="h-3 w-3" /> {fileName ?? "upload a file"}
            <input type="file" accept=".json,.jsonl,.csv" onChange={onFile} className="hidden" data-testid="convert-file" />
          </label>
        </div>
        <div className="text-2xs uppercase tracking-wide text-subtle">Source — paste, or upload</div>
        <Textarea
          data-testid="convert-source"
          value={sourceText}
          onChange={(e) => {
            reads.current.begin(); // an edit supersedes any file read still in flight
            setSourceText(e.target.value);
            setFileName(null);
          }}
          rows={7}
          placeholder="Paste your own records here (JSON array, JSON lines, or CSV with a header row)."
        />
        <div className="flex items-baseline justify-between">
          <div className="text-2xs uppercase tracking-wide text-subtle">Rules — transform spec (JSONata)</div>
          <button type="button" data-testid="convert-identity" className="text-2xs text-accent hover:underline" onClick={() => setSpecText(IDENTITY_SPEC)}>
            use the identity mapping ($)
          </button>
        </div>
        <Textarea
          data-testid="convert-spec"
          value={specText}
          onChange={(e) => setSpecText(e.target.value)}
          rows={6}
          placeholder='{"language": "jsonata", "expression": "...", "coercions": [], "unmappable": []}'
        />
        <div className="flex items-center gap-2">
          <Button data-testid="convert-run" onClick={run} disabled={busy || !sourceText.trim() || !specText.trim()}>
            <Play className="h-3.5 w-3.5" /> {busy ? "Converting…" : "Convert"}
          </Button>
          <span className="text-2xs text-subtle">Deterministic — the rule engine, no model.</span>
        </div>
        {error && (
          <div data-testid="convert-error" className="rounded border border-band-review/40 bg-band-review/10 p-2 text-2xs text-band-review">
            {error}
          </div>
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="text-2xs uppercase tracking-wide text-subtle">
            Output{table ? ` — first ${Math.min(20, table.rows.length)} of ${table.total} rows` : ""}
          </div>
          {result && !stale && (
            <Button
              size="sm"
              variant="outline"
              data-testid="convert-download"
              onClick={() => saveBlob(new Blob([outText], { type: MIME[result.target] ?? "text/plain" }), `converted.${result.target}`)}
            >
              <Download className="h-3 w-3" /> converted.{result.target}
            </Button>
          )}
        </div>
        {stale && (
          <div
            role="status"
            data-testid="convert-stale"
            className="rounded border border-band-spot/40 bg-band-spot/10 p-2 text-2xs text-band-spot"
          >
            The inputs changed since this conversion ran — the output below is for the previous inputs and cannot be downloaded. Convert
            again to see the current result.
          </div>
        )}
        {!result && (
          <div className="flex h-40 items-center justify-center rounded border border-dashed border-border text-xs text-subtle" data-testid="convert-empty">
            Nothing converted yet.
          </div>
        )}
        {result && table && (
          <div className={`max-h-80 overflow-auto rounded border border-border ${stale ? "opacity-50" : ""}`} data-testid="convert-preview" data-stale={String(stale)}>
            <table className="w-full text-2xs">
              <thead className="sticky top-0 bg-muted">
                <tr>
                  {table.header.map((h, i) => (
                    <th key={i} className="whitespace-nowrap px-2 py-1 text-left font-medium text-fg">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.slice(0, 20).map((r, i) => (
                  <tr key={i} className="border-t border-border/50">
                    {table.header.map((_, j) => (
                      <td key={j} className="whitespace-nowrap px-2 py-0.5 font-mono text-fg">
                        {r[j] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {result && !table && (
          <div className="flex flex-col gap-1">
            <pre
              className={`max-h-80 overflow-auto rounded border border-border bg-bg p-2 font-mono text-2xs ${stale ? "opacity-50" : ""}`}
              data-testid="convert-preview-raw"
              data-stale={String(stale)}
            >
              {outText.slice(0, RAW_LIMIT)}
            </pre>
            {outText.length > RAW_LIMIT && (
              <div className="text-2xs text-band-spot" data-testid="convert-preview-truncated">
                Preview truncated: showing the first {RAW_LIMIT.toLocaleString()} of {outText.length.toLocaleString()} characters. The download
                has all of it.
              </div>
            )}
          </div>
        )}
        {result && (
          <div className="flex flex-col gap-2 text-xs" data-testid="convert-quarantine" data-quarantined={result.quarantine.quarantined}>
            <div className="flex flex-wrap items-center gap-3">
              <span>
                <span className="text-subtle">quarantined: </span>
                <span className={result.quarantine.quarantined ? "text-band-spot" : "text-band-auto"}>{result.quarantine.quarantined}</span>
              </span>
              <span>
                <span className="text-subtle">rate: </span>
                <span className={result.quarantine.over_threshold ? "text-band-review" : "text-fg"}>{(result.quarantine.rate * 100).toFixed(2)}%</span>
                <span className="text-2xs text-subtle" data-testid="convert-threshold">
                  {" "}
                  ({quarantineThresholdText(result.quarantine)})
                </span>
              </span>
              <span className="text-2xs text-subtle">
                replayable artifact <span className="font-mono text-fg">{result.artifact_id}</span>
              </span>
            </div>
            {byReason.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {byReason.map(([k, n]) => (
                  <Badge key={k}>
                    {k}: {n}
                  </Badge>
                ))}
              </div>
            )}
            {result.quarantine.records.length > 0 && (
              <div className="rounded border border-border p-2">
                <div className="mb-1 text-2xs uppercase tracking-wide text-subtle">Quarantined rows — nothing silently dropped</div>
                {result.quarantine.records.length < result.quarantine.quarantined && (
                  <div className="mb-1 text-2xs text-band-spot" data-testid="convert-quarantine-partial">
                    The gateway returned {result.quarantine.records.length} of {result.quarantine.quarantined} quarantined rows; the rest are in
                    the replayable artifact.
                  </div>
                )}
                {result.quarantine.records.map((r, i) => {
                  const p = payloadPreview(r.payload, 80);
                  return (
                    <div key={i} className="flex flex-col gap-0.5 border-b border-border/50 py-1 text-2xs last:border-0">
                      <div className="flex items-start gap-2">
                        <Badge className="border-band-spot/40 text-band-spot">{r.reason_code}</Badge>
                        <span className="text-subtle">{r.detail}</span>
                        {!p.truncated && <span className="ml-auto break-all font-mono text-subtle">{p.full}</span>}
                      </div>
                      {p.truncated && (
                        <details data-testid="convert-quarantine-payload" data-truncated="true">
                          <summary className="cursor-pointer break-all font-mono text-subtle">{p.text}</summary>
                          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-bg p-1 font-mono text-subtle">{p.full}</pre>
                        </details>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
