"use client";
/** Numbered step affordance shared by every step of the Adapter Builder wizard. */
import * as React from "react";

export function StepShell({
  n,
  title,
  hint,
  disabled,
  children,
}: {
  n: number;
  title: string;
  hint?: string;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className={disabled ? "pointer-events-none opacity-40" : undefined}
      aria-disabled={disabled}
    >
      <div className="mb-2 flex items-baseline gap-2">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-2xs font-semibold text-fg">
          {n}
        </span>
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        {hint ? <span className="text-2xs text-subtle">{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}
