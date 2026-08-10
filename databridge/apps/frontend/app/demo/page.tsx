"use client";
import * as React from "react";
import { startDemo, useDemoState } from "@/lib/demo";

// The /demo route — kicks off the automated investor demo, then the global DemoOverlay drives the
// real pages (navigation + narration + page actions). Renders a brief splash; the overlay takes over.
export default function DemoPage() {
  const demo = useDemoState();
  React.useEffect(() => {
    startDemo(6);
  }, []);

  return (
    <div className="flex h-full items-center justify-center bg-bg">
      <div className="text-center">
        <div className="text-sm font-medium text-fg">
          {demo.playing ? "Playing the investor demo…" : "Demo finished."}
        </div>
        <p className="mt-1 text-2xs text-subtle">Use Pause / Skip in the bar at the bottom.</p>
      </div>
    </div>
  );
}
