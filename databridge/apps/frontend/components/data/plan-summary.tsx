"use client";
import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { flattenPlanYaml, humanDuration, understand } from "./plan-yaml";

function Line({ k, children, testid }: { k: string; children: React.ReactNode; testid?: string }) {
  return (
    <div className="flex gap-3 py-0.5 text-xs" data-testid={testid}>
      <span className="w-36 shrink-0 text-subtle">{k}</span>
      <span className="min-w-0 break-words text-fg">{children}</span>
    </div>
  );
}

const RELATION_WORDS: Record<string, string> = {
  cohort_key: "row key",
  decision_time: "decision time",
  as_of_prior: "value as known before the decision",
  aggregate_count: "count of prior rows",
};

/**
 * "Here is what I understood" — read from the `plan.yaml` the run actually executed, never from a
 * restatement of the request. The raw file is one click away.
 */
export function PlanSummary({
  yaml,
  verified,
  unverifiedNote,
}: {
  yaml: string;
  /** True only when the plan was checked against a VALID receipt for this run (R-021 round 2). */
  verified: boolean;
  unverifiedNote?: string | null;
}) {
  const [raw, setRaw] = React.useState(false);
  const u = React.useMemo(() => understand(flattenPlanYaml(yaml)), [yaml]);

  return (
    <div data-testid="plan-summary" data-verified={String(verified)} className="rounded border border-border bg-bg/50 p-3">
      <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-subtle">
        Here is what I understood{" "}
        <span className="font-normal normal-case">{verified ? "(from the plan that ran)" : "(from plan.yaml — not yet verified as the plan that ran)"}</span>
      </div>
      {!verified && (
        <div className="mb-1.5 text-2xs text-band-spot" data-testid="plan-unverified">
          {unverifiedNote ?? "Unverified — no valid receipt confirms this plan.yaml as the plan that ran."}
        </div>
      )}
      <Line k="rows" testid="plan-entity">
        {u.entity ? (
          <>
            one row per <span className="font-mono">{u.entity}</span>
            {u.decisionTimeField ? (
              <>
                {" "}
                at its decision time <span className="font-mono text-subtle">({u.decisionTimeField})</span>
              </>
            ) : null}
          </>
        ) : (
          <span className="text-subtle">not stated in the plan</span>
        )}
      </Line>
      <Line k="output shape" testid="plan-shape">
        <span className="font-mono">{u.shape ?? "—"}</span>
        {u.consumer ? <span className="text-subtle"> · for {u.consumer}</span> : null}
        {u.outputFormat ? <span className="text-subtle"> · {u.outputFormat}</span> : null}
      </Line>
      {u.features.length > 0 && (
        <Line k={`columns (${u.features.length})`}>
          <ul className="flex flex-col gap-0.5">
            {u.features.map((f) => (
              <li key={f.path}>
                <span className="font-mono">{f.path}</span>
                {f.relation ? <span className="text-subtle"> — {RELATION_WORDS[f.relation] ?? f.relation}</span> : null}
              </li>
            ))}
          </ul>
        </Line>
      )}
      {u.chunking && (
        <Line k="chunking">
          text from <span className="font-mono">{u.chunking.textField ?? "—"}</span>, up to {u.chunking.maxChars} characters per chunk
        </Line>
      )}
      <Line k="label" testid="plan-label">
        {u.label ? (
          <>
            <span className="font-mono">{u.label.path}</span>
            {u.label.within ? <> · outcome within {humanDuration(u.label.within)}</> : null}
            {u.label.maturityLag ? (
              <> · maturity lag {humanDuration(u.label.maturityLag)} (rows younger than this are held out, not labelled negative)</>
            ) : (
              <> · <span className="text-band-review">no maturity lag</span></>
            )}
          </>
        ) : (
          <span className="text-subtle">none — this dataset has no label</span>
        )}
      </Line>
      <Line k="time axes">
        features on <span className="font-mono">{u.featureAxis ?? "—"}</span>
        {u.labelAxis && u.labelAxis !== "none" ? (
          <>
            , label on <span className="font-mono">{u.labelAxis}</span>
          </>
        ) : null}
      </Line>
      <Line k="as of">
        {u.asOf ? (
          <>
            <span className="font-mono">{u.asOf}</span>
            {u.asOfKind ? <span className="text-subtle"> ({u.asOfKind})</span> : null}
          </>
        ) : (
          <span className="text-subtle">no instant pinned in the plan</span>
        )}
      </Line>
      <Line k="policy" testid="plan-policy">
        subject identifiers <span className="font-mono">{u.identifiers ?? "—"}</span>
        {u.identifiers === "suppress" ? <span className="text-subtle"> (withheld — the default)</span> : null}
        {u.purpose ? <> · purpose <span className="font-mono">{u.purpose}</span></> : null}
        {u.lawfulBasis ? <> · basis <span className="font-mono">{u.lawfulBasis}</span></> : null}
        {u.egress ? <> · egress <span className="font-mono">{u.egress}</span></> : null}
      </Line>
      <button
        type="button"
        onClick={() => setRaw((r) => !r)}
        className="mt-2 flex items-center gap-1 text-2xs text-subtle hover:text-fg"
      >
        {raw ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} plan.yaml as written
      </button>
      {raw && (
        <pre className="mt-1 max-h-72 overflow-auto rounded bg-bg p-2 font-mono text-2xs text-fg">{yaml}</pre>
      )}
    </div>
  );
}
