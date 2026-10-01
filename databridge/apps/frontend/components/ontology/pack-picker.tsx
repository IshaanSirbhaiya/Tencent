"use client";
/**
 * Which pack's vocabulary this screen shows. Deliberately separate from `usePack()`
 * (`lib/pack-context.tsx`) — that switcher chooses the app's active VERTICAL for every domain noun
 * on screen; this one chooses which `ontology.yaml` to look at, and the two are not always the same
 * pack. `common` in particular is never a vertical (`packs/common/ui.yaml` declares no operator
 * surfaces) but is exactly the pack worth viewing here — it is the general vocabulary other packs
 * build on. Backed by `GET /ontology/packs`, a directory scan, so a pack shows up here the moment
 * its `ontology.yaml` exists, whether or not this deployment has bound it.
 */
import * as React from "react";
import { Library } from "lucide-react";

export function PackPicker({
  packs,
  enabled,
  active,
  onChange,
}: {
  packs: string[];
  enabled: string[];
  active: string;
  onChange: (pack: string) => void;
}) {
  if (packs.length <= 1) return null;
  return (
    <div className="flex items-center gap-2">
      <Library className="h-3.5 w-3.5 shrink-0 text-subtle" />
      <div className="inline-flex flex-wrap gap-1 rounded border border-border bg-muted p-0.5">
        {packs.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onChange(p)}
            title={enabled.includes(p) ? "Bound by this deployment" : "On disk, not bound by this deployment"}
            data-testid={`ontology-pack-${p}`}
            className={`rounded px-2 py-1 text-2xs font-medium transition-colors ${
              active === p ? "bg-surface text-fg shadow-sm" : "text-subtle hover:text-fg"
            }`}
          >
            {p}
            {!enabled.includes(p) ? <span className="ml-1 text-subtle/60">·unbound</span> : null}
          </button>
        ))}
      </div>
    </div>
  );
}
