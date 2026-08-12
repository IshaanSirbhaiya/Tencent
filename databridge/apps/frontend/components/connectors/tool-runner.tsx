"use client";
/**
 * "Try it" — one READ tool, its published schema, and the raw result.
 *
 * This is the control that makes the page obviously real. A list of registered servers is a claim;
 * a tool that runs against the live aggregator and prints what came back is a demonstration, and
 * the difference costs one button.
 *
 * Two things it deliberately does NOT do:
 *
 * 1. **It never renders for an ACT tool.** The parent hides the button (`invocable`) and the
 *    gateway refuses the call regardless (`ACT_TOOL_NOT_INVOCABLE`), because an act happens after
 *    a named human approves a draft — never because somebody pressed a button on a settings page.
 * 2. **It does not prettify the result.** The aggregator's payload is shown as it arrived,
 *    including `isError`. A tool that rejected its arguments answers HTTP 200 with an error inside;
 *    a page that flattened that into a green tick would be lying about the call it just made.
 *
 * The input fields come from the tool's own `input_schema` as published by the aggregator — never
 * from a hardcoded list keyed on the tool name.
 */
import * as React from "react";
import { AlertTriangle, Loader2, Play } from "lucide-react";
import { mcpApi, schemaFields, type InvokeResponse, type McpTool } from "@/lib/mcp-api";
import type { Refusal } from "@/lib/types";
import { RefusalCard } from "@/components/refusal-card";
import { cn } from "@/lib/utils";

/** Text in, JSON-ish out: a numeric field types as a number, everything else stays a string.
 *  Guided by the schema, so nothing is coerced on a guess. */
function coerce(raw: string, type: string): unknown {
  if (raw === "") return undefined;
  if (type === "integer" || type === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (type === "boolean") return raw === "true";
  return raw;
}

export function ToolRunner({ tool }: { tool: McpTool }) {
  const fields = React.useMemo(() => schemaFields(tool.input_schema), [tool.input_schema]);
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<InvokeResponse | null>(null);
  const [refusal, setRefusal] = React.useState<Refusal | null>(null);

  const missing = fields.some((f) => f.required && !(values[f.name] ?? "").trim());

  async function run() {
    setBusy(true);
    setRefusal(null);
    setResult(null);
    const args: Record<string, unknown> = {};
    for (const f of fields) {
      const v = coerce(values[f.name] ?? "", f.type);
      if (v !== undefined) args[f.name] = v;
    }
    const res = await mcpApi.invoke(tool.name, args);
    if (res.ok) setResult(res.data);
    else setRefusal(res.refusal);
    setBusy(false);
  }

  return (
    <div className="mt-2 rounded border border-border bg-bg p-3">
      {fields.length > 0 ? (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {fields.map((f) => (
            <label key={f.name} className="block">
              <span className="font-mono text-2xs text-subtle">
                {f.name}
                <span className="ml-1 text-subtle/70">
                  {f.type}
                  {f.required ? " · required" : ""}
                </span>
              </span>
              <input
                value={values[f.name] ?? ""}
                onChange={(e) => setValues((s) => ({ ...s, [f.name]: e.target.value }))}
                className="mt-1 w-full rounded border border-border bg-surface px-2 py-1 text-xs text-fg focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </label>
          ))}
        </div>
      ) : (
        <p className="text-2xs text-subtle">This tool takes no arguments.</p>
      )}

      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={run}
          disabled={busy || missing}
          className="flex items-center gap-1.5 rounded border border-border bg-surface px-2.5 py-1 text-2xs font-medium text-fg transition-colors hover:border-accent hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-accent"
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <Play className="h-3 w-3" aria-hidden />
          )}
          Run
        </button>
        {missing ? (
          <span className="text-2xs text-subtle">Fill the required arguments.</span>
        ) : null}
        {result ? (
          <span className="tnum text-2xs text-subtle">{result.duration_ms} ms</span>
        ) : null}
      </div>

      {refusal ? (
        <div className="mt-2">
          <RefusalCard refusal={refusal} />
        </div>
      ) : null}

      {result ? (
        <div className="mt-2">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-2xs font-medium",
                result.is_error
                  ? "border-band-review/40 bg-band-review/10 text-band-review"
                  : "border-band-auto/40 bg-band-auto/10 text-band-auto",
              )}
            >
              {result.is_error ? "isError: true" : "isError: false"}
            </span>
            <span className="font-mono text-2xs text-subtle">{result.tool}</span>
            {result.is_error ? (
              <AlertTriangle className="h-3 w-3 text-band-review" aria-hidden />
            ) : null}
          </div>
          <pre className="mt-1.5 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-surface p-2 font-mono text-2xs text-fg">
            {result.text || JSON.stringify(result.raw, null, 2)}
          </pre>
          <p className="mt-1 text-2xs text-subtle">
            Raw payload from the aggregator, relayed unmodified.
          </p>
        </div>
      ) : null}
    </div>
  );
}
