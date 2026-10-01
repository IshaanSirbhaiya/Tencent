"use client";
import * as React from "react";
import { startDemo, useDemoState } from "@/lib/demo";
import { usePack } from "@/lib/pack-context";

// The /demo route — kicks off the active pack's guided tour, then the global DemoOverlay drives the
// real pages (navigation + narration + page actions). The step count is the pack's own
// `demo_steps` length, never a number written here; a pack with no steps has no tour.
export default function DemoPage() {
  const demo = useDemoState();
  const { pack } = usePack();
  const total = pack.demo_steps.length;
  React.useEffect(() => {
    if (total > 0) startDemo(total);
  }, [total]);

  return (
    <div className="flex h-full items-center justify-center bg-bg">
      <div className="text-center">
        <div className="text-sm font-medium text-fg">
          {total === 0
            ? `The ${pack.label} pack defines no guided tour.`
            : demo.playing
              ? `Playing the ${pack.label} tour…`
              : "Tour finished."}
        </div>
        {total > 0 && <p className="mt-1 text-2xs text-subtle">Use Pause / Skip in the bar at the bottom.</p>}
      </div>
    </div>
  );
}
