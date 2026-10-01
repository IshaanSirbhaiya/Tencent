"use client";
/**
 * The pack switcher — the control that makes the platform claim checkable in one click.
 *
 * The list is NOT written here. It comes from `lib/packs.ts`, which reads `GET /packs` and falls
 * back to `config/packs.json`; a hardcoded array of verticals inside a component is precisely the
 * per-domain branching this repository forbids.
 *
 * A pack whose adapters are unbuilt is shown and labelled unavailable rather than hidden. Hiding
 * it would make the switcher look like it has one option; showing it dead would be a lie. Labelled
 * is the honest third thing.
 */
import * as React from "react";
import { Check, ChevronsUpDown, Layers } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { usePack } from "@/lib/pack-context";
import { cn } from "@/lib/utils";

export function PackSwitcher() {
  const { registry, pack, setPack } = usePack();
  const [open, setOpen] = React.useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="pack-switcher"
          data-active-pack={pack.id}
          className="flex w-full items-center gap-2 rounded border border-border bg-bg px-2.5 py-1.5 text-left text-2xs transition-colors hover:bg-muted"
        >
          <Layers className="h-3.5 w-3.5 shrink-0 text-accent" />
          <span className="min-w-0 flex-1 truncate font-medium text-fg">{pack.label}</span>
          <ChevronsUpDown className="h-3 w-3 shrink-0 text-subtle" />
        </button>
      </PopoverTrigger>

      <PopoverContent align="start" className="w-72 p-2">
        <p className="px-1.5 pb-1.5 text-2xs text-subtle">
          The engine is domain-free. A pack supplies the vocabulary, the operator surfaces and the
          rules — switching one changes no engine code.
        </p>
        <div className="flex flex-col gap-0.5">
          {registry.packs.map((p) => {
            const active = p.id === pack.id;
            return (
              <button
                key={p.id}
                type="button"
                data-testid={`pack-option-${p.id}`}
                onClick={() => {
                  setPack(p.id);
                  setOpen(false);
                }}
                className={cn(
                  "flex items-start gap-2 rounded px-1.5 py-1.5 text-left transition-colors",
                  active ? "bg-muted" : "hover:bg-muted",
                )}
              >
                <Check
                  className={cn("mt-0.5 h-3 w-3 shrink-0", active ? "text-accent" : "opacity-0")}
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5">
                    <span className="text-xs font-medium text-fg">{p.label}</span>
                    {!p.available && (
                      <span className="rounded border border-border px-1 py-px text-[10px] text-subtle">
                        surfaces unbuilt
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-2xs leading-relaxed text-subtle">
                    {p.tagline}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
