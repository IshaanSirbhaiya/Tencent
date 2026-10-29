// Canvas colours for the WebGL graph, read from the design tokens (app/globals.css) at runtime so the
// graph follows the theme, high-contrast mode and any token change. WebGL needs concrete colours, so
// the CSS variables ("214 138 184") are resolved here; nothing in this folder hard-codes a hue.
import type { Platform } from '@/lib/types';

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

export interface Palette {
  theme: 'dark' | 'light';
  bg: Rgb;
  surface: Rgb;
  raised: Rgb;
  fg: Rgb;
  muted: Rgb;
  subtle: Rgb;
  line: Rgb;
  lineStrong: Rgb;
  accent: Rgb;
  platform: Record<Platform, Rgb>;
}

const FALLBACK: Palette = {
  theme: 'dark',
  bg: { r: 10, g: 11, b: 13 },
  surface: { r: 17, g: 18, b: 21 },
  raised: { r: 26, g: 28, b: 32 },
  fg: { r: 236, g: 237, b: 239 },
  muted: { r: 156, g: 162, b: 172 },
  subtle: { r: 128, g: 134, b: 145 },
  line: { r: 36, g: 39, b: 45 },
  lineStrong: { r: 70, g: 75, b: 85 },
  accent: { r: 146, g: 155, b: 255 },
  platform: {
    confluence: { r: 110, g: 172, b: 232 },
    jira: { r: 72, g: 186, b: 160 },
    slack: { r: 214, g: 138, b: 184 },
    drive: { r: 208, g: 186, b: 96 },
  },
};

function parse(v: string, fb: Rgb): Rgb {
  const m = v.trim().split(/[\s,]+/).map(Number);
  return m.length >= 3 && m.every((n) => Number.isFinite(n)) ? { r: m[0], g: m[1], b: m[2] } : fb;
}

/** Read the tokens from an element (the graph root, so a `data-theme-scope` subtree also works). */
export function readPalette(el?: Element | null): Palette {
  if (typeof window === 'undefined') return FALLBACK;
  const cs = getComputedStyle(el ?? document.documentElement);
  const v = (name: string, fb: Rgb) => parse(cs.getPropertyValue(name), fb);
  const theme = (el?.closest('[data-theme-scope]')?.getAttribute('data-theme-scope') ?? document.documentElement.getAttribute('data-theme')) === 'light' ? 'light' : 'dark';
  return {
    theme,
    bg: v('--bg', FALLBACK.bg),
    surface: v('--surface', FALLBACK.surface),
    raised: v('--raised', FALLBACK.raised),
    fg: v('--fg', FALLBACK.fg),
    muted: v('--muted', FALLBACK.muted),
    subtle: v('--subtle', FALLBACK.subtle),
    line: v('--line', FALLBACK.line),
    lineStrong: v('--line-strong', FALLBACK.lineStrong),
    accent: v('--accent', FALLBACK.accent),
    platform: {
      confluence: v('--pf-confluence', FALLBACK.platform.confluence),
      jira: v('--pf-jira', FALLBACK.platform.jira),
      slack: v('--pf-slack', FALLBACK.platform.slack),
      drive: v('--pf-drive', FALLBACK.platform.drive),
    },
  };
}

const hex2 = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
export const hex = (c: Rgb) => `#${hex2(c.r)}${hex2(c.g)}${hex2(c.b)}`;
export const rgba = (c: Rgb, a: number) => `rgba(${c.r},${c.g},${c.b},${a})`;
export const css = (c: Rgb) => `rgb(${c.r},${c.g},${c.b})`;

/** Mix `c` toward `to` by t (0 = c, 1 = to). Used for dimming: blend into the canvas, never alpha soup. */
export function mix(c: Rgb, to: Rgb, t: number): Rgb {
  return { r: c.r + (to.r - c.r) * t, g: c.g + (to.g - c.g) * t, b: c.b + (to.b - c.b) * t };
}

export function parseCss(color: string): Rgb | null {
  const m = color.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (m) return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) };
  const r = color.match(/rgba?\(([^)]+)\)/);
  if (r) {
    const [a, b, c] = r[1].split(',').map((x) => Number(x.trim()));
    return { r: a, g: b, b: c };
  }
  return null;
}
