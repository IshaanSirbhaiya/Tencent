"use client";
/**
 * What the four steps compiled to — or the refusal, which is the moment this surface exists to
 * produce. Deliberately close in shape to `/builder`'s `CompiledView` / `RefusalView`: an adapter
 * declaration is an adapter declaration whichever surface authored it, and two renderings of one
 * compiled object drifting apart is worse than the duplication of keeping them visually aligned.
 */
import * as React from "react";
import { CheckCircle2, CircleSlash } from "lucide-react";
import type { CompiledConnector, CompileRefusal } from "@/lib/connector-api";
import { cn } from "@/lib/utils";

function KeyValue({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
      <dt className="shrink-0 text-2xs text-subtle">{label}</dt>
      <dd className="min-w-0 truncate text-right font-mono text-2xs text-fg">{value}</dd>
    </div>
  );
}

const STAGE_LABEL: Record<string, string> = {
  catalogue: "refused before a declaration was even generated — the server has no reviewed artefact",
  request: "refused before compiling — the wizard's own input did not validate",
  compile: "the compiler refused it",
};

export function CompiledConnectorView({ out }: { out: CompiledConnector }) {
  return (
    <div data-testid="connector-compiled" className="flex flex-col gap-3">
      <div className="rounded border border-band-auto/40 bg-band-auto/[0.06] p-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-band-auto">
          <CheckCircle2 className="h-3.5 w-3.5" />
          COMPILED — {out.source_id}
        </p>
        <p className="mt-1 text-2xs text-subtle">
          {out.title}
          {out.estate ? <> · estate <code className="text-fg">{out.estate}</code></> : null}
        </p>
      </div>

      <div>
        <p className="text-2xs uppercase tracking-wider text-subtle/70">Capability grants</p>
        <dl className="mt-1 divide-y divide-border rounded border border-border">
          <KeyValue label="read" value={out.grants.read.join(", ") || "—"} />
          <KeyValue
            label="write"
            value={out.grants.write.length ? out.grants.write.join(", ") : <span className="text-band-auto">empty</span>}
          />
        </dl>
        <p className="mt-1 text-2xs text-subtle">{out.grants.note}</p>
      </div>

      <div>
        <p className="text-2xs uppercase tracking-wider text-subtle/70">Pinning &amp; capture</p>
        <dl className="mt-1 divide-y divide-border rounded border border-border">
          <KeyValue label="tier" value={out.pinning.tier} />
          <KeyValue label="deletes detectable" value={out.pinning.deletes_detectable ?? "—"} />
          <KeyValue label="artefact digest" value={out.server.digest ?? "—"} />
          <KeyValue label="declaration digest" value={out.server.declaration_digest ?? "—"} />
          <KeyValue label="sandbox" value={out.server.sandbox ?? "—"} />
        </dl>
      </div>

      <div>
        <p className="text-2xs uppercase tracking-wider text-subtle/70">
          Trust, per field — every <span className="text-band-auto">trusted</span> carries a written reason
        </p>
        <ul className="mt-1 divide-y divide-border rounded border border-border">
          {out.trust.map((f) => (
            <li key={f.field} className="px-3 py-1.5">
              <p className="flex items-baseline gap-2">
                <code className="text-2xs text-fg">{f.field}</code>
                <span
                  className={cn(
                    "rounded border px-1 text-2xs",
                    f.trust === "trusted"
                      ? "border-band-auto/40 text-band-auto"
                      : "border-band-spot/40 text-band-spot",
                  )}
                >
                  {f.trust}
                </span>
                {f.redaction.length ? (
                  <span className="text-2xs text-subtle">redacts {f.redaction.join(", ")}</span>
                ) : null}
              </p>
              {f.justification || f.note ? (
                <p className="mt-0.5 text-2xs text-subtle">{f.justification ?? f.note}</p>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function ConnectorRefusalView({ refusal }: { refusal: CompileRefusal }) {
  return (
    <div
      data-testid="connector-refusal"
      data-refusal-code={refusal.code}
      className="rounded border-l-4 border border-band-review/50 bg-band-review/[0.09] p-3"
    >
      <p className="flex items-center gap-1.5 text-xs font-bold text-band-review">
        <CircleSlash className="h-3.5 w-3.5" />
        REFUSED — this connector cannot deploy
      </p>
      <p className="mt-1.5 text-2xs">
        <span className="font-mono text-fg">{refusal.kind}</span>
        <span className="text-subtle"> · {STAGE_LABEL[refusal.stage] ?? refusal.stage}</span>
      </p>
      <p className="mt-1.5 whitespace-pre-wrap rounded border border-border bg-surface px-2.5 py-2 text-2xs text-fg">
        {refusal.message}
      </p>
      <p className="mt-2 text-2xs text-subtle">
        The reason above is the compiler&apos;s own — this screen does not write refusals, it only
        asks the loader the question honestly enough that its real answer is the one shown.
      </p>
    </div>
  );
}

export function GeneratedDeclaration({ text }: { text: string }) {
  return (
    <details className="rounded border border-border bg-surface" data-testid="generated-declaration">
      <summary className="cursor-pointer select-none px-3 py-2 text-2xs font-medium uppercase tracking-wide text-subtle hover:text-fg">
        Generated declaration (what step 1–4 produced, before Compile ran it through the loader)
      </summary>
      <pre className="max-h-96 overflow-auto border-t border-border bg-bg p-3 font-mono text-2xs leading-relaxed text-fg">
        {text}
      </pre>
    </details>
  );
}
