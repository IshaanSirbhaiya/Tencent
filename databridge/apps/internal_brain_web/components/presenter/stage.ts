'use client';
// The Stage theme: `data-stage` on <html>. Light theme, root font 150 % (body 24 px), larger
// citation chips, secondary chrome hidden. Every rule below is scoped to :root[data-stage], and the
// style element is injected by this module (not globals.css), so nothing changes when it is off.
//
// It is module-level on purpose: <html> outlives every page, so Stage stays on while the Presenter
// drives pages it does not render (/dev/*), and turns off cleanly from anywhere.
import { loadPrefs, resolveTheme } from '@/lib/prefs';

const STYLE_ID = 'ib-stage-style';

// The doubled attribute selector out-ranks :root[data-text=…] (Settings text size).
const CSS = `
:root[data-stage][data-stage] { font-size: 150%; --fs-body: 1rem; --fs-body-sm: 0.875rem; --lh-body: 1.5; }
:root[data-stage] [data-shell] > div > aside,
:root[data-stage] [data-testid="tour-offer"],
:root[data-stage] [data-tour-launch],
:root[data-stage] button[aria-keyshortcuts="Control+K Meta+K"],
:root[data-stage] [data-testid="theme-switch"],
:root[data-stage] [data-stage-hide] { display: none !important; }
:root[data-stage] [data-stage-only] { display: block !important; }
:root[data-stage] #main { padding-inline: 2.5vw; padding-bottom: 7rem; }
:root[data-stage] .skip-link:not(:focus) { top: -6rem; }
:root[data-stage] [data-testid="citation-chip"] { height: 1.45em; min-width: 1.6em; padding: 0 0.3em; font-size: 0.72em; vertical-align: 0.12em; }
:root[data-stage] [data-compare-col] [data-testid="answer-text"],
:root[data-stage] [data-compare-col] [data-testid="refusal-text"] { font-size: 1.0625rem; line-height: 1.55; }
`;

let observer: MutationObserver | null = null;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.textContent = CSS;
  document.head.appendChild(el);
}

/** Turn the Stage theme on or off. Off restores the visitor's own theme preference. */
export function applyStage(on: boolean) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (on) {
    ensureStyle();
    root.setAttribute('data-stage', '1');
    root.setAttribute('data-theme', 'light');
    // The prefs provider re-applies the stored theme when any preference changes (e.g. L flips
    // the language): keep Stage light without touching the stored preference.
    if (!observer) {
      observer = new MutationObserver(() => {
        if (root.hasAttribute('data-stage') && root.getAttribute('data-theme') !== 'light') root.setAttribute('data-theme', 'light');
      });
      observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    }
  } else {
    observer?.disconnect();
    observer = null;
    if (!root.hasAttribute('data-stage')) return;
    root.removeAttribute('data-stage');
    root.setAttribute('data-theme', resolveTheme(loadPrefs().theme));
  }
}
