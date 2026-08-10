"use client";
import * as React from "react";

// Animate a counter 0 → target with requestAnimationFrame (no animation lib). Re-runs when target
// changes. Purely presentational — the FINAL value is always the exact backend figure (D-FE-2).
export function useCountUp(target: number, durationMs = 900): number {
  const [value, setValue] = React.useState(0);
  const prefersReduced =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  React.useEffect(() => {
    if (prefersReduced || target <= 0) {
      setValue(target);
      return;
    }
    let raf = 0;
    let start = 0;
    const tick = (t: number) => {
      if (!start) start = t;
      const p = Math.min((t - start) / durationMs, 1);
      const eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
      setValue(Math.round(target * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
      else setValue(target);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs, prefersReduced]);

  return value;
}
