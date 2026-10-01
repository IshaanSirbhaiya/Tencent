"use client";
/**
 * A multi-select of the pack's ENTITY kinds — what a relation's source and target may be.
 *
 * Offered from the kinds the gateway served, never typed free-hand: a relation that names a kind
 * the pack does not declare is refused by the loader (and by `POST /ontology/kinds`), so the form
 * should make that mistake impossible rather than report it. Deprecated kinds are not offered
 * (the gateway refuses them as endpoints). Subject kinds are marked — choosing one makes the
 * relation identity-protected, and the save will ask for an explicit confirmation.
 */
import * as React from "react";
import { Check, ShieldAlert } from "lucide-react";
import type { NodeKindView } from "@/lib/ontology-api";

export const ANY_KIND = "*";

export function KindPicker({
  kinds,
  value,
  onChange,
  testId,
  allowAny = true,
}: {
  kinds: NodeKindView[];
  value: string[];
  onChange: (next: string[]) => void;
  testId?: string;
  allowAny?: boolean;
}) {
  const offered = kinds.filter((k) => !k.deprecated || value.includes(k.kind));
  const toggle = (name: string) =>
    onChange(value.includes(name) ? value.filter((v) => v !== name) : [...value, name]);

  if (!offered.length) {
    return (
      <p className="text-2xs italic text-subtle">
        No entity kinds yet — define an entity kind first, then relate it.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap gap-1" data-testid={testId} role="group">
      {offered.map((k) => {
        const on = value.includes(k.kind);
        return (
          <button
            key={k.kind}
            type="button"
            aria-pressed={on}
            onClick={() => toggle(k.kind)}
            data-testid={testId ? `${testId}-${k.kind}` : undefined}
            title={k.subject ? "Identity-protected subject — saving will ask for confirmation" : k.description}
            className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-2xs transition-colors ${
              on
                ? k.subject
                  ? "border-band-review bg-band-review/[0.12] text-band-review"
                  : "border-accent bg-accent/[0.12] text-fg"
                : "border-border text-subtle hover:text-fg"
            }`}
          >
            {on ? <Check className="h-3 w-3" /> : null}
            {k.subject ? <ShieldAlert className="h-3 w-3" /> : null}
            {k.kind}
          </button>
        );
      })}
      {allowAny ? (
        <button
          type="button"
          aria-pressed={value.includes(ANY_KIND)}
          onClick={() => toggle(ANY_KIND)}
          title="Any entity kind — use deliberately; it also reaches every subject kind"
          className={`rounded border border-dashed px-1.5 py-0.5 text-2xs ${
            value.includes(ANY_KIND) ? "border-accent text-fg" : "border-border text-subtle hover:text-fg"
          }`}
        >
          any kind (*)
        </button>
      ) : null}
    </div>
  );
}
