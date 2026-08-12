"use client";
import * as React from "react";
import { GripHorizontal } from "lucide-react";

// T-32 — the bottom ask/insights/stats row used to be a fixed flex ratio (flex-[2] against the
// canvas's flex-[3]). On a real screen the answer, its citations and the graph compete for room and
// nobody could rebalance them. This is the drag handle plus the height it drags, split into a hook
// (`useBottomPanelHeight`) so `app/brain/page.tsx` can own the layout while this file owns the one
// interaction: drag, or ArrowUp/ArrowDown with focus on the handle.

const STORAGE_KEY = "brain-bottom-panel-height";
// Tall enough for the ask panel's input row + suggested chips + one line of answer without its own
// scrollbar fighting the drag. Below this the row is present but useless, which is the "dragged to
// nothing" failure the spec calls out by name.
const MIN_HEIGHT = 180;
const MAX_HEIGHT = 640;
// 300 left ~54px for the answer once the header, the input and the suggestion chips
// had taken their share — enough to hold a grounded answer and not enough to show one.
const DEFAULT_HEIGHT = 380;
const ARROW_STEP = 24;

function clamp(px: number): number {
  return Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, px));
}

/**
 * Persisted per-browser (localStorage), never per-session. This is how someone likes their own
 * monitor divided, not a fact about who they are — it must not go anywhere near `lib/staff-session`,
 * which exists specifically to attribute ledger writes to a named person (CLAUDE.md hard constraint
 * 1). Mixing a UI preference into that store would make a future "what does staff-session hold"
 * audit have to rule this out by hand.
 */
export function useBottomPanelHeight(): [number, (next: number) => void] {
  // Start at the default on the server AND on first client render, then adjust once mounted — the
  // stored value is only knowable in the browser, and reading it during render would desync from
  // what Next.js sent for SSR and log a hydration-mismatch warning.
  const [height, setHeightState] = React.useState(DEFAULT_HEIGHT);

  React.useEffect(() => {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? Number(raw) : NaN;
    if (Number.isFinite(parsed)) setHeightState(clamp(parsed));
  }, []);

  const setHeight = React.useCallback((next: number) => {
    const c = clamp(next);
    setHeightState(c);
    window.localStorage.setItem(STORAGE_KEY, String(c));
  }, []);

  return [height, setHeight];
}

export function ResizeHandle({
  height,
  onResize,
}: {
  height: number;
  onResize: (next: number) => void;
}) {
  // A ref, not state: the drag origin only needs to survive between mousedown and mouseup, and
  // putting it in state would re-run the window listener effect on every pixel of drag.
  const dragRef = React.useRef<{ startY: number; startHeight: number } | null>(null);

  React.useEffect(() => {
    function onMove(e: MouseEvent) {
      const d = dragRef.current;
      if (!d) return;
      // The handle sits above the panel it resizes, so dragging UP (negative clientY delta) must
      // GROW the panel — the sign is inverted relative to raw screen Y.
      onResize(d.startHeight + (d.startY - e.clientY));
    }
    function onUp() {
      dragRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onResize]);

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize the ask panel"
      aria-valuemin={MIN_HEIGHT}
      aria-valuemax={MAX_HEIGHT}
      aria-valuenow={Math.round(height)}
      tabIndex={0}
      onMouseDown={(e) => {
        e.preventDefault(); // don't let the browser start a text selection while dragging
        dragRef.current = { startY: e.clientY, startHeight: height };
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowUp") {
          e.preventDefault();
          onResize(height + ARROW_STEP);
        } else if (e.key === "ArrowDown") {
          e.preventDefault();
          onResize(height - ARROW_STEP);
        }
      }}
      className="group flex h-2.5 shrink-0 cursor-row-resize items-center justify-center border-y border-border bg-surface outline-none transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:ring-1 focus-visible:ring-accent"
    >
      <GripHorizontal className="h-3 w-6 text-subtle transition-colors group-hover:text-fg group-focus-visible:text-fg" />
    </div>
  );
}
