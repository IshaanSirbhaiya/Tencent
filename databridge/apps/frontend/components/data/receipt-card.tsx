"use client";
import * as React from "react";
import { CheckCircle2, CircleDashed, Download, Lock, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fetchRunFile, saveBlob, type RunSummary } from "@/lib/data-api";
import {
  datasetDownloadGate,
  fileDecision,
  leakVerdict,
  pointerDisplay,
  receiptNotes,
  reproducibility,
  validatedReceipt,
  type ReceiptState,
} from "./receipt-gate";

function Hash({ label, value, testid }: { label: string; value: string | null | undefined; testid?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-wide text-subtle">{label}</div>
      <div className="break-all font-mono text-2xs text-fg" data-testid={testid}>
        {value || "—"}
      </div>
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: number | string | undefined | null; tone?: "ok" | "warn" | "bad" }) {
  const color = tone === "ok" ? "text-band-auto" : tone === "warn" ? "text-band-spot" : tone === "bad" ? "text-band-review" : "text-fg";
  return (
    <div className="rounded border border-border bg-surface px-2.5 py-1.5">
      <div className="text-2xs text-subtle">{label}</div>
      <div className={`tnum text-sm font-semibold ${color}`}>{value ?? "—"}</div>
    </div>
  );
}

function DownloadButton({ planHash, name, blocked, role, declared }: { planHash: string; name: string; blocked: string | null; role: string; declared?: string }) {
  const [state, setState] = React.useState<"idle" | "busy" | string>("idle");
  return (
    <span className="inline-flex flex-col">
      <Button
        size="sm"
        variant="outline"
        data-testid={`download-${name}`}
        data-role={role}
        data-blocked={blocked ? "true" : "false"}
        title={blocked ?? undefined}
        disabled={state === "busy" || !!blocked}
        onClick={async () => {
          setState("busy");
          try {
            saveBlob(await fetchRunFile(planHash, name), name);
            setState("idle");
          } catch (e) {
            setState(e instanceof Error ? e.message : "download failed");
          }
        }}
      >
        {blocked ? <Lock className="h-3 w-3" /> : <Download className="h-3 w-3" />} {name.length > 36 ? `${name.slice(0, 16)}…${name.slice(-14)}` : name}
      </Button>
      {declared === "pointers" ? (
        <span className="text-2xs text-subtle" data-testid={`file-caption-${name}`}>
          per-cell pointers: one line per row and column, each grounded cell&apos;s source
        </span>
      ) : null}
      {state !== "idle" && state !== "busy" ? <span className="text-2xs text-band-review">{state}</span> : null}
    </span>
  );
}

/** The receipt, exactly as the run measured it. */
export function ReceiptCard({ run, receipt }: { run: RunSummary; receipt: ReceiptState }) {
  // The full receipt is used only when it loaded AND is for this run; otherwise the card is the
  // job's summary, labelled as such.
  const doc = validatedReceipt(run, receipt);
  const gate = datasetDownloadGate(run, receipt);
  const repro = reproducibility(run.reproducible ?? doc?.platform.reproducible, run.shaped_by ?? doc?.receipt.shaped_by);
  const notes = receiptNotes(run, doc);
  const refused = doc?.receipt.columns_refused ?? [];
  const directives = doc?.receipt.shaping_directives ?? [];
  // file roles, the pointer verdict and the leak verdict come only from a VALID receipt (R-021 round 2)
  const files = run.files.map((f) => ({ name: f, ...fileDecision(f, run, receipt) }));
  // The server's declared role (from a VALID receipt only) — used for a caption, never for gating:
  // `fileDecision` gates every non-evidence role, the pointer file included (E2E run 2, N-08).
  const declaredRoles = (doc as unknown as { file_roles?: Record<string, string> } | null)?.file_roles ?? {};
  const pointer = pointerDisplay(run, receipt);
  const pc = pointer.pc;
  const pcFailures = Array.isArray(pc?.failures) ? pc.failures : [];
  const pb = run.produced_by ?? doc?.platform.produced_by ?? null;
  const unresolved = run.unresolved ?? doc?.platform.unresolved ?? null;
  const leak = doc?.receipt.missingness_leak_check ?? null;
  const leakV = leakVerdict(doc);
  // files withheld for a reason other than the dataset gate (e.g. not in the receipt's file list)
  const otherWithheld = files.filter((f) => f.blocked && f.blocked !== `Withheld: ${gate.reason}`);

  return (
    <div data-testid="receipt-card" data-plan-hash={run.plan_hash} className="flex flex-col gap-3 rounded border border-border bg-surface p-3">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-2xs font-semibold uppercase tracking-wide text-subtle" data-testid="receipt-label" data-full={String(!!doc)}>
          {doc ? "Receipt" : "Job summary — the full receipt is not loaded"}
        </div>
        <div className="text-2xs text-subtle">
          {run.executed_at} · run by <span className="font-mono">{run.run_by ?? "—"}</span>
        </div>
      </div>

      {run.honest_summary ? <p className="text-xs text-fg" data-testid="receipt-summary">{run.honest_summary}</p> : null}

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Hash label="plan_hash — the dataset's identity" value={run.plan_hash} testid="receipt-plan-hash" />
        <Hash label="content_hash — the bytes" value={run.content_hash} testid="receipt-content-hash" />
      </div>
      <p className="text-2xs text-subtle" data-testid="receipt-reproducible" data-reproducible={repro.kind}>
        {repro.text}
      </p>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Count label="rows emitted" value={run.rows?.emitted} />
        <Count label="held out: maturity" value={run.rows?.rejected_by_maturity} tone={run.rows?.rejected_by_maturity ? "warn" : undefined} />
        <Count label="held out: policy" value={run.rows?.rejected_by_policy} />
        <Count label="held out: incomplete" value={run.rows?.rejected_incomplete} />
        <Count label="grounded cells" value={run.cells?.grounded} />
        <Count label="unresolved cells" value={run.cells?.unresolved} tone={run.cells?.unresolved ? "warn" : undefined} />
        <Count label="withheld by policy" value={run.cells?.policy_suppressed} />
        <Count label="grounding dropped" value={run.cells?.grounding_dropped} tone={run.cells?.grounding_dropped ? "bad" : undefined} />
      </div>

      {!pc && (
        <div className="flex items-start gap-2 text-xs text-band-review" data-testid="receipt-pointer-check" data-ok="absent" data-state={pointer.state}>
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />{" "}
          {pointer.state === "disagree"
            ? `Pointer re-check inconsistent: ${pointer.reason}.`
            : "No pointer re-check was reported for this run — nothing says its values trace to a source."}
        </div>
      )}
      {pc && (
        <div className="flex items-start gap-2 text-xs" data-testid="receipt-pointer-check" data-ok={String(pointer.state === "pass")} data-state={pointer.state}>
          {pointer.state === "pass" ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-band-auto" />
          ) : pointer.state === "unverified" ? (
            <CircleDashed className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
          ) : (
            <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
          )}
          <div>
            <div className="text-fg">
              Pointer re-check{pointer.state === "unverified" ? " (job summary)" : ""}: {pc.pointers_resolved} of {pc.grounded_cells} pointers
              resolve, {pc.values_match} of {pc.grounded_cells} say the value in the cell.
            </div>
            {pointer.state === "pass" ? (
              <div className="text-2xs text-subtle">Every grounded cell was re-read from its source after the build — not a sample.</div>
            ) : (
              <div className={`text-2xs ${pointer.state === "unverified" ? "text-subtle" : "text-band-review"}`} data-testid="receipt-pointer-reason">
                {pointer.state === "unverified" ? "Not confirmed: " : pointer.state === "disagree" ? "Inconsistent: " : "Failed: "}
                {pointer.reason}.
              </div>
            )}
            {pcFailures.length > 0 && pcFailures.length < pc.grounded_cells - Math.min(pc.pointers_resolved, pc.values_match) && (
              <div className="text-2xs text-band-review">
                Showing the {pcFailures.length} failures the gateway listed — it caps the list; the counts above are complete.
              </div>
            )}
            {pcFailures.length > 0 && (
              <ul className="mt-1 text-2xs text-band-review">
                {pcFailures.map((f, i) => (
                  <li key={i} className="font-mono">
                    row {f.row} · {f.column} · {f.pointer_kind} · resolves {String(f.resolves)} · matches {String(f.matches)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {unresolved && (
        <div className="text-xs" data-testid="receipt-unresolved">
          <span className="text-subtle">Unresolved: </span>
          {unresolved.cells} cell{unresolved.cells === 1 ? "" : "s"} with no value in any source (left empty, with a reason — never filled in).
          {(unresolved.columns_fully_absent ?? []).length > 0 && (
            <div className="text-2xs text-band-spot">
              Columns with no value in any row: <span className="font-mono">{unresolved.columns_fully_absent.join(", ")}</span>
            </div>
          )}
        </div>
      )}

      {doc && (
        <div className="flex flex-col gap-1 text-xs" data-testid="receipt-policy">
          <div>
            <span className="text-subtle">Policy applied: </span>
            <span className="font-mono">{doc.receipt.policy_applied || "not provided"}</span>
            <span className="text-subtle"> · purpose </span>
            <span className="font-mono">{doc.receipt.purpose || "not provided"}</span>
          </div>
          <div data-testid="receipt-columns-refused" data-count={refused.length}>
            <span className="text-subtle">Columns refused: </span>
            {refused.length === 0 ? (
              <span className="text-subtle">none</span>
            ) : (
              <span className="font-mono text-band-review">{refused.join(", ")}</span>
            )}
            {refused.length > 0 && (
              <span className="text-2xs text-subtle"> — not in the dataset, whatever the plan lists (governing policy above)</span>
            )}
          </div>
          {(doc.receipt.columns_fully_absent ?? []).length > 0 && (
            <div className="text-2xs text-band-spot">
              Columns with no value in any row (receipt): <span className="font-mono">{doc.receipt.columns_fully_absent.join(", ")}</span>
            </div>
          )}
          <div data-testid="receipt-shaping-directives" data-count={directives.length}>
            <span className="text-subtle">Shaping directives: </span>
            {directives.length === 0 ? (
              <span className="text-subtle">none</span>
            ) : (
              <ul className="list-disc pl-4 text-2xs">
                {directives.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {doc && (
        <div className="text-xs" data-testid="receipt-leak-check" data-state={leakV.state}>
          <span className="text-subtle">Missingness leak check: </span>
          <span className={leakV.ok ? "text-band-auto" : "text-band-review"}>
            {leakV.state === "pass" ? "passed" : leakV.state === "fail" ? "failed" : leakV.state === "absent" ? "not recorded" : "inconsistent"}
          </span>
          <span className="text-2xs text-subtle">
            {leak && typeof leak === "object" && leakV.state !== "absent"
              ? ` (worst class delta ${String(leak.max_class_delta_observed)} vs threshold ${String(leak.threshold)})`
              : ""}
            {leakV.ok ? "" : ` — ${leakV.reason}`}
          </span>
        </div>
      )}

      {pb && (
        <div className="text-xs" data-testid="receipt-produced-by">
          <span className="text-subtle">Produced by: </span>
          <span className="font-mono">{pb.agent}</span> <span className="text-subtle">{pb.agent_version}</span>
          {" · model "}
          <span className="font-mono">{pb.model ?? "none"}</span>
          {pb.task_class ? <span className="text-subtle"> · task class {pb.task_class}</span> : null}
          <span className="text-subtle"> · run {pb.run_id}</span>
        </div>
      )}

      {doc && Array.isArray(doc.platform.sources) && doc.platform.sources.length > 0 && (
        <div data-testid="receipt-sources">
          <div className="mb-1 text-2xs uppercase tracking-wide text-subtle">
            Pinned sources — read as of <span className="font-mono normal-case">{doc.platform.system_as_of}</span>
          </div>
          <table className="w-full text-2xs">
            <thead>
              <tr className="text-left text-subtle">
                <th className="py-0.5 pr-2 font-normal">source</th>
                <th className="py-0.5 pr-2 font-normal">tier</th>
                <th className="py-0.5 pr-2 font-normal">as of</th>
                <th className="py-0.5 font-normal">content hash</th>
              </tr>
            </thead>
            <tbody>
              {doc.platform.sources.map((s) => (
                <tr key={s.source_id} className="border-t border-border/50">
                  <td className="py-0.5 pr-2 font-mono text-fg">{s.source_id}</td>
                  <td className="py-0.5 pr-2">{s.tier}</td>
                  <td className="py-0.5 pr-2 font-mono">{s.as_of}</td>
                  <td className="py-0.5 font-mono" title={s.content_hash}>{s.content_hash.slice(0, 16)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {notes.length > 0 && (
        <details className="text-2xs text-subtle" data-testid="receipt-notes" data-count={notes.length}>
          <summary className="cursor-pointer">
            Notes ({notes.length}){doc ? " — from the job summary and the receipt" : " — from the job summary only"}
          </summary>
          <ul className="mt-1 list-disc pl-4">
            {notes.map((n, i) => (
              <li key={i}>
                {n.text}{" "}
                <span className="text-subtle">[{n.origin === "both" ? "summary + receipt" : n.origin === "run" ? "summary" : "receipt"}]</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap gap-2" data-testid="receipt-files" data-dataset-allowed={String(gate.allowed)}>
          {files.map((f) => (
            <DownloadButton key={f.name} planHash={run.plan_hash} name={f.name} role={f.role} blocked={f.blocked} declared={declaredRoles[f.name]} />
          ))}
          {run.files.length === 0 && <span className="text-2xs text-subtle">The run lists no output files.</span>}
        </div>
        {!gate.allowed && files.some((f) => f.role === "dataset") && (
          <div className="text-2xs text-band-review" data-testid="receipt-download-withheld">
            Dataset downloads withheld: {gate.reason}. The run&apos;s evidence files (receipt.json, plan.yaml) stay downloadable.
          </div>
        )}
        {otherWithheld.length > 0 && (
          <ul className="text-2xs text-band-review" data-testid="receipt-file-withheld">
            {otherWithheld.map((f) => (
              <li key={f.name}>
                <span className="font-mono">{f.name}</span>: {f.blocked}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
