"use client";
/**
 * The auto-approve toggle, and the limit on it stated in the same box — so nobody reads the switch
 * without reading what it cannot do. `neverCovers` comes straight from the gateway's own payload
 * (`GET /ontology`'s `auto_approve_never_covers`), so the sentence on screen cannot drift from what
 * `guardrail()` actually enforces.
 */
import * as React from "react";
import { ShieldAlert } from "lucide-react";

export function GrowthPolicyPanel({
  enabled,
  setBy,
  neverCovers,
  actingAs,
  onToggle,
  refusal,
}: {
  enabled: boolean;
  setBy: string | null;
  neverCovers: string[];
  actingAs: string;
  onToggle: () => void;
  refusal: string | null;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <label className="flex cursor-pointer items-center gap-3">
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label="Auto-approve in-policy proposals"
            data-testid="auto-approve-toggle"
            onClick={onToggle}
            className={`relative h-5 w-9 shrink-0 rounded-full border transition-colors ${
              enabled ? "border-accent bg-accent" : "border-border bg-muted"
            }`}
          >
            <span
              className={`absolute top-0.5 h-3.5 w-3.5 rounded-full bg-surface transition-transform ${
                enabled ? "translate-x-[1.15rem]" : "translate-x-0.5"
              }`}
            />
          </button>
          <span>
            <span className="block text-xs font-medium text-fg">Auto-approve in-policy proposals</span>
            <span className="block text-2xs text-subtle">
              {enabled
                ? `On${setBy ? ` — set by ${setBy}` : ""}. A proposal that touches nothing structural lands without review.`
                : "Off. Every proposal waits for a named human, including ones inside policy."}
            </span>
          </span>
        </label>
      </div>

      <div
        className="flex items-start gap-2 rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2"
        data-testid="escalation-note"
      >
        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
        <p className="text-2xs leading-relaxed text-subtle">
          <span className="font-medium text-fg">This switch cannot approve an identity decision.</span>{" "}
          Never waived, whatever it is set to:
          <ul className="mt-1 list-inside list-disc">
            {neverCovers.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </p>
      </div>

      {refusal ? (
        <p className="text-2xs text-band-review" data-testid="auto-approve-refusal">
          Refused: {refusal}
        </p>
      ) : null}
      {!actingAs ? (
        <p className="text-2xs text-subtle">Sign in for this shift to change this setting.</p>
      ) : null}
    </div>
  );
}
