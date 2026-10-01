"use client";
/**
 * Step 3 — adapter-agent skills.
 *
 * `GOA Submission/11_SKILL_CATALOGUE.md` names 14 Skills (WS-01..WS-14). There is no Skill
 * runtime registry in this build, so a selection here is a DECLARED capability selection, not a
 * live grant — it lands on the generated declaration's `_connector.skills` block, which the
 * compiler strips before validating (same mechanism as its own review comments). The note below
 * is the gateway's own words (`GET /connectors/skills`), not paraphrased here.
 */
import * as React from "react";
import type { Skill } from "@/lib/connector-api";
import { cn } from "@/lib/utils";

export function SkillsPicker({
  skills,
  note,
  selected,
  onToggle,
}: {
  skills: Skill[];
  note: string | null;
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2" role="group" aria-label="Skills">
        {skills.map((s) => {
          const checked = selected.includes(s.id);
          return (
            <label
              key={s.id}
              data-testid={`skill-${s.id}`}
              className={cn(
                "flex cursor-pointer items-start gap-2 rounded border px-2.5 py-2 transition-colors",
                checked ? "border-accent bg-accent/5" : "border-border bg-bg hover:border-accent/40",
              )}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => onToggle(s.id)}
                className="mt-0.5 h-3.5 w-3.5 accent-accent"
              />
              <span className="min-w-0">
                <span className="flex items-baseline gap-1.5">
                  <span className="font-mono text-2xs text-fg">{s.id}</span>
                  <span className="font-mono text-2xs text-subtle">{s.skill}</span>
                </span>
                <span className="mt-0.5 block text-2xs leading-relaxed text-subtle">{s.purpose}</span>
              </span>
            </label>
          );
        })}
      </div>
      {note ? <p className="text-2xs leading-relaxed text-subtle">{note}</p> : null}
    </div>
  );
}
