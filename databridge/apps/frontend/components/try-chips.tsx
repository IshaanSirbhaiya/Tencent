"use client";
/**
 * One-click trial chips — the affordance `/brain` already has, made shareable.
 *
 * A visitor who does not know what to type sees nothing on a page built around a text box. `/brain`
 * solved that with a row of pills that fill the box and submit it (`components/brain/ask-panel.tsx`,
 * the `SUGGESTED_FALLBACK` row). This is the same row, extracted so `/intelligence`, `/evidence`,
 * `/intake` and `/connectors` share one visual language rather than four near-copies of it.
 *
 * **`/brain` is deliberately NOT refactored onto this.** Its list is served by
 * `GET /brain/suggested` from `config/assistant.yaml`, which is the shape everything here wants to
 * grow into; rewriting the one surface that already reads its prompts from config, in order to
 * share a stylesheet, would be trading the better property for the smaller diff.
 *
 * **Groups exist because the tiers are the point.** On `/intelligence` a request is answered,
 * turned into a draft, or refused before a model is called — three different behaviours from one
 * box. A flat row of chips hides that; a labelled group makes a visitor see it before they click.
 * `note` is where a group says what it is, and it matters most for the group that refuses: that
 * chip is not a broken button, and the row has to say so in its own words.
 *
 * Nothing here fetches. A chip carries a payload and the surface decides what to do with it, so
 * this file has no opinion about gateways, and a surface whose input is a form rather than a
 * question box can still use it.
 */
import * as React from "react";
import { cn } from "@/lib/utils";

export type TryChip<P> = {
  /** Stable id — the `data-testid` suffix and the React key. */
  id: string;
  /** What the chip says. For a question box this IS the request, verbatim. */
  label: string;
  /** What the surface should do when it is clicked. */
  payload: P;
  /** Optional hover text — used where the label is shortened for width. */
  title?: string;
};

export type TryGroup<P> = {
  /** The tier: `Ask`, `Act`, `Refuses`, … Kept to one short word where possible. */
  label: string;
  /** One line under the chips saying what this tier does. Where a group refuses, this is the
   *  sentence that stops it reading as an error. */
  note?: string;
  chips: readonly TryChip<P>[];
};

export function TryChips<P>({
  groups,
  onPick,
  disabled,
  testId,
  className,
}: {
  groups: readonly TryGroup<P>[];
  onPick: (chip: TryChip<P>) => void;
  disabled?: boolean;
  testId?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)} data-testid={testId ?? "try-chips"}>
      {groups.map((group) => (
        <div key={group.label} className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="w-16 shrink-0 text-2xs uppercase tracking-wide text-subtle">
            {group.label}
          </span>
          {group.chips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              title={chip.title}
              disabled={disabled}
              onClick={() => onPick(chip)}
              data-testid={`try-chip-${chip.id}`}
              // Verbatim from `/brain`'s suggestion pills. One visual language, not two.
              className="rounded-full border border-border bg-surface px-2.5 py-1 text-2xs text-subtle transition-colors hover:border-accent/40 hover:text-fg disabled:opacity-50"
            >
              {chip.label}
            </button>
          ))}
          {group.note ? (
            <span className="w-full pl-16 text-2xs leading-relaxed text-subtle">{group.note}</span>
          ) : null}
        </div>
      ))}
    </div>
  );
}
