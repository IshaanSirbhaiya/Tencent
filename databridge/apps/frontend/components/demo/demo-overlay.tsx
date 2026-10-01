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
import { usePack } from "@/lib/pack-context";

// The automated 90-second scripted demo. Mounted globally; when playing it navigates the real
// pages, narrates each beat, and emits page actions (load data, ask, slider, node, …). Just router
// pushes + a pausable timeline — no special framework. [Skip] and [Pause] for live demos.
//
// The script is NOT written here. It is `demo_steps` on the active pack (`config/packs.json`), for
// the same reason every other noun is: a tour that walks one vertical's surfaces, compiled into the
// platform shell, is a vertical compiled into the platform shell. A pack with no steps simply has
// no tour, and this component says so rather than playing someone else's.

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function DemoOverlay() {
  const demo = useDemoState();
  const router = useRouter();
  const pathname = usePathname();
  const pathRef = React.useRef(pathname);
  pathRef.current = pathname;
  const { pack } = usePack();
  const steps = pack.demo_steps;
  // Read through a ref inside the run loop: a pack switch mid-tour must not restart the effect and
  // replay the tour from step one.
  const stepsRef = React.useRef(steps);
  stepsRef.current = steps;

  React.useEffect(() => {
    if (!demo.playing) return;
    const STEPS = stepsRef.current;
    if (STEPS.length === 0) {
      // Nothing to play. Stop rather than render an empty progress bar forever.
      stopDemo();
      return;
    }
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
            {pack.label} tour · step {demo.step + 1}/{steps.length}
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
            style={{ width: `${((demo.step + 1) / Math.max(steps.length, 1)) * 100}%` }}
          />
        </div>
      </div>
    </div>
  );
}
