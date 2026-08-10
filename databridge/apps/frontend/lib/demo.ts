"use client";
import * as React from "react";

// Tiny demo "bus" for the automated /demo autoplay (Part 4). A module-level store (subscribed via
// useSyncExternalStore) holds play/pause/caption; page-level actions are dispatched as window
// CustomEvents so they survive route changes (the listener re-attaches when a page mounts).

export interface DemoState {
  playing: boolean;
  paused: boolean;
  step: number;
  total: number;
  caption: string;
}

let state: DemoState = { playing: false, paused: false, step: 0, total: 0, caption: "" };
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function getDemoState(): DemoState {
  return state;
}
export function setDemoState(patch: Partial<DemoState>): void {
  state = { ...state, ...patch };
  emit();
}
export function subscribeDemo(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useDemoState(): DemoState {
  return React.useSyncExternalStore(subscribeDemo, getDemoState, getDemoState);
}

// page-action channel — pages add a `demo-action` listener and perform the step.
export function emitDemoAction(action: string): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("demo-action", { detail: action }));
}
export function onDemoAction(cb: (action: string) => void): () => void {
  const h = (e: Event) => cb((e as CustomEvent).detail as string);
  window.addEventListener("demo-action", h);
  return () => window.removeEventListener("demo-action", h);
}

export function startDemo(total: number): void {
  setDemoState({ playing: true, paused: false, step: 0, total, caption: "" });
}
export function stopDemo(): void {
  setDemoState({ playing: false, paused: false, caption: "" });
}
export function togglePause(): void {
  setDemoState({ paused: !state.paused });
}
