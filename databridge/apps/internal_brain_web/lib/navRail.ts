'use client';
// The desktop sidebar can shrink to an icon rail (like ChatGPT's or Xcode's). The state lives on
// <html data-nav="rail"> so CSS does the work, and in localStorage when the person chose it. The Ask
// page asks for the rail while its page canvas is open (`auto`) and gives it back when it closes,
// unless the person chose the rail themselves.
import { useEffect, useState } from 'react';

const KEY = 'ib-nav';
const EVENT = 'ib-nav-change';

function read(): boolean {
  return typeof document !== 'undefined' && document.documentElement.dataset.nav === 'rail';
}

function apply(rail: boolean) {
  if (typeof document === 'undefined') return;
  if (rail) document.documentElement.dataset.nav = 'rail';
  else delete document.documentElement.dataset.nav;
  window.dispatchEvent(new Event(EVENT));
}

/** The person's own choice (remembered). */
export function setNavRail(rail: boolean) {
  try {
    localStorage.setItem(KEY, rail ? 'rail' : 'full');
  } catch {
    /* storage blocked: this page only */
  }
  apply(rail);
}

/** The Ask canvas: rail while it is open, the person's own choice when it closes. */
export function autoNavRail(on: boolean) {
  let chosen = false;
  try {
    chosen = localStorage.getItem(KEY) === 'rail';
  } catch {
    /* storage blocked */
  }
  apply(on || chosen);
}

/** Restores the remembered choice on first paint of the shell. */
export function restoreNavRail() {
  try {
    if (localStorage.getItem(KEY) === 'rail') apply(true);
  } catch {
    /* storage blocked */
  }
}

export function useNavRail(): boolean {
  const [rail, setRail] = useState(false);
  useEffect(() => {
    const on = () => setRail(read());
    on();
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return rail;
}
