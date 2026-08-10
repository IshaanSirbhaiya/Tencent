"use client";
import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { Pause, Play, X } from "lucide-react";
import {
  emitDemoAction,
  getDemoState,
  setDemoState,
  stopDemo,
  togglePause,
  useDemoState,
} from "@/lib/demo";

// The automated 90-second investor demo (Part 4). Mounted globally; when playing it navigates the
// real pages, narrates each beat, and emits page actions (load data, ask, slider, node, …). Just
// router pushes + a pausable timeline — no special framework. [Skip] and [Pause] for live demos.

interface Step {
  route?: string;
  caption: string;
  action?: string;
  ms: number;
}

const STEPS: Step[] = [
  { route: "/brain", caption: "Meet the Company Brain — built automatically from your real data.", action: "load", ms: 9000 },
  { caption: "Ask anything. Every answer is cited — nothing is made up.", action: "ask", ms: 10000 },
  { caption: "Rewind time — see exactly what the company knew, and when.", action: "slider", ms: 9000 },
  { caption: "DataBridge found Sarah from an email. You can verify the source.", action: "node", ms: 8000 },
  { route: "/connectors", caption: "Connect Gmail, Slack, Drive — and this graph builds itself.", ms: 8000 },
  { route: "/brain", caption: "100% provenance. 0 fabrications. Enterprise-ready.", action: "finish", ms: 7000 },
];

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function DemoOverlay() {
  const demo = useDemoState();
  const router = useRouter();
  const pathname = usePathname();
  const pathRef = React.useRef(pathname);
  pathRef.current = pathname;

  React.useEffect(() => {
    if (!demo.playing) return;
    let alive = true;
    const timers = new Set<ReturnType<typeof setInterval>>();

    // resolves after `ms` of UNPAUSED time; bails immediately if the run is cancelled.
    const waitPausable = (ms: number) =>
      new Promise<void>((resolve) => {
        let left = ms;
        const id = setInterval(() => {
          if (!alive) {
            clearInterval(id);
            timers.delete(id);
            resolve();
            return;
          }
          if (getDemoState().paused) return;
          left -= 100;
          if (left <= 0) {
            clearInterval(id);
            timers.delete(id);
            resolve();
          }
        }, 100);
        timers.add(id);
      });

    (async () => {
      for (let i = 0; i < STEPS.length; i++) {
        if (!alive) return;
        const s = STEPS[i];
        if (s.route && pathRef.current !== s.route) {
          router.push(s.route);
          await sleep(900); // let the destination page mount + attach its action listener
        }
        if (!alive) return;
        setDemoState({ step: i, caption: s.caption, total: STEPS.length });
        if (s.action) {
          await sleep(500);
          if (!alive) return; // don't emit a stale action after Skip/unmount
          emitDemoAction(s.action);
        }
        await waitPausable(s.ms);
      }
      if (alive) stopDemo();
    })();

    return () => {
      alive = false;
      timers.forEach(clearInterval); // stop any in-flight pausable wait
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demo.playing]);

  if (!demo.playing) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex justify-center px-4 pb-6">
      <div className="pointer-events-auto flex w-full max-w-2xl flex-col gap-2 rounded-lg border border-border bg-elevated px-5 py-3 shadow-lg">
        <div className="flex items-center justify-between gap-3">
          <span className="text-2xs font-medium uppercase tracking-wider text-accent">
            Investor demo · step {demo.step + 1}/{STEPS.length}
          </span>
          <div className="flex items-center gap-1.5">
            <button
              onClick={togglePause}
              className="flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg"
            >
              {demo.paused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
              {demo.paused ? "Resume" : "Pause"}
            </button>
            <button
              onClick={stopDemo}
              className="flex items-center gap-1 rounded border border-border px-2 py-1 text-2xs text-subtle hover:text-fg"
            >
              <X className="h-3 w-3" /> Skip
            </button>
          </div>
        </div>
        <p className="text-sm font-medium leading-snug text-fg">{demo.caption}</p>
        <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-accent transition-all duration-500"
            style={{ width: `${((demo.step + 1) / STEPS.length) * 100}%` }}
          />
        </div>
      </div>
    </div>
  );
}
