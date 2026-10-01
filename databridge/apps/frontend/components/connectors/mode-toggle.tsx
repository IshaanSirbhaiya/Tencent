"use client";
/**
 * Step 2 — read or write.
 *
 * "Then say it's read or write, so data sources are or aren't written over." Adapters cannot
 * write today — `test_no_adapter_verb_can_write_anything` enforces it at the port — so this toggle
 * is not a preference that changes what gets compiled quietly; it is the question whose honest
 * answer, on "write", is a refusal computed by the real loader. Nothing here decides that in
 * advance: the copy below states the CONSEQUENCE, not the verdict, and the verdict only exists
 * after Compile runs.
 */
import { Eye, PenLine } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Mode } from "@/lib/connector-api";

const META: Record<Mode, { title: string; icon: typeof Eye; tone: string; blurb: string }> = {
  read: {
    title: "Read",
    icon: Eye,
    tone: "band-auto",
    blurb: "The adapter may only read the reviewed projection below. Nothing at this source is ever written over.",
  },
  write: {
    title: "Write",
    icon: PenLine,
    tone: "band-spot",
    blurb: "Ask for write access to this source. Writes are a separate capability class the engine does not implement on an adapter — Compile will show the real reason.",
  },
};

export function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div>
      <div role="radiogroup" aria-label="Read or write" className="flex flex-col gap-2 sm:flex-row">
        {(["read", "write"] as const).map((m) => {
          const meta = META[m];
          const Icon = meta.icon;
          const selected = mode === m;
          return (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={selected}
              data-testid={`mode-${m}`}
              onClick={() => onChange(m)}
              className={cn(
                "flex flex-1 flex-col rounded border p-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-accent",
                selected ? "border-accent bg-accent/5" : "border-border bg-bg hover:border-accent/40",
              )}
            >
              <span className="flex items-center gap-2">
                <Icon
                  className={cn("h-4 w-4", m === "read" ? "text-band-auto" : "text-band-spot")}
                  aria-hidden
                />
                <span className="text-sm font-semibold text-fg">{meta.title}</span>
              </span>
              <span className="mt-2 text-2xs leading-relaxed text-subtle">{meta.blurb}</span>
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-2xs text-subtle">
        Default is Read. The choice is what gets compiled — not restated in the UI's own words —
        so the switch cannot say one thing and the compiled adapter do another.
      </p>
    </div>
  );
}
