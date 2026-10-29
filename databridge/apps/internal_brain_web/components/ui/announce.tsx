'use client';
// One shared polite live region for short status messages ("Copied"), instead of one live region
// per row. Mounted once by <Providers>; call announce('…') from anywhere.
//
// Screen readers only speak a live region when its text CHANGES, so the region is cleared first
// and the message is written on the next frame (saying "Copied" twice in a row is still heard).

const ID = 'ib-live-announcer';
let clearTimer: ReturnType<typeof setTimeout> | undefined;

/** Speak `message` politely through the shared live region. Safe to call during SSR (no-op). */
export function announce(message: string) {
  if (typeof document === 'undefined') return;
  const el = document.getElementById(ID);
  if (!el) return;
  el.textContent = '';
  const write = () => {
    el.textContent = message;
    clearTimeout(clearTimer);
    // Empty it again so stale text is not read when someone browses into it later.
    clearTimer = setTimeout(() => {
      if (el.textContent === message) el.textContent = '';
    }, 4000);
  };
  if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(() => setTimeout(write, 30));
  else setTimeout(write, 50);
}

/** The single region. Render once, near the root (Providers does). */
export function LiveAnnouncer() {
  return <div id={ID} role="status" aria-live="polite" aria-atomic="true" className="sr-only" data-testid="live-announcer" />;
}
