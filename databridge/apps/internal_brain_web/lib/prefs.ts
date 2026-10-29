// Viewer preferences: theme, language, text size, contrast, motion and the User/Developer mode.
// Stored in localStorage (always inside try/catch: private windows and blocked storage must still
// render with the defaults) and mirrored onto <html> as data attributes, which the CSS tokens read.

export type ThemePref = 'dark' | 'light' | 'system';
export type Lang = 'en' | 'zh';
export type TextSize = '100' | '115' | '130';
export type Mode = 'user' | 'dev';

export interface Prefs {
  theme: ThemePref;
  lang: Lang;
  text: TextSize;
  contrast: boolean;
  reduceMotion: boolean;
  mode: Mode;
}

export const DEFAULT_PREFS: Prefs = { theme: 'dark', lang: 'en', text: '100', contrast: false, reduceMotion: false, mode: 'user' };

/** Storage keys. `ib-theme` predates this file and is used by the e2e tests: keep it. */
export const PREF_KEYS = {
  theme: 'ib-theme',
  lang: 'ib-lang',
  text: 'ib-text',
  contrast: 'ib-contrast',
  reduceMotion: 'ib-motion',
  mode: 'ib-mode',
} as const;

export const HTML_LANG: Record<Lang, string> = { en: 'en', zh: 'zh-Hans' };

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function savePref<K extends keyof Prefs>(key: K, value: Prefs[K]) {
  try {
    window.localStorage.setItem(PREF_KEYS[key], String(value));
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}

export function loadPrefs(): Prefs {
  if (typeof window === 'undefined') return DEFAULT_PREFS;
  const t = read(PREF_KEYS.theme);
  const l = read(PREF_KEYS.lang);
  const x = read(PREF_KEYS.text);
  const m = read(PREF_KEYS.mode);
  return {
    theme: t === 'light' || t === 'dark' || t === 'system' ? t : DEFAULT_PREFS.theme,
    lang: l === 'zh' || l === 'en' ? l : DEFAULT_PREFS.lang,
    text: x === '115' || x === '130' ? x : '100',
    contrast: read(PREF_KEYS.contrast) === '1' || read(PREF_KEYS.contrast) === 'true',
    reduceMotion: read(PREF_KEYS.reduceMotion) === '1' || read(PREF_KEYS.reduceMotion) === 'true',
    mode: m === 'dev' ? 'dev' : 'user',
  };
}

export function resolveTheme(t: ThemePref): 'dark' | 'light' {
  if (t !== 'system') return t;
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

/** Mirror prefs onto <html>. `mode` is applied by the shell (it also follows the route). */
export function applyPrefs(p: Prefs) {
  const el = document.documentElement;
  el.setAttribute('data-theme', resolveTheme(p.theme));
  el.setAttribute('data-text', p.text);
  el.setAttribute('lang', HTML_LANG[p.lang]);
  if (p.contrast) el.setAttribute('data-contrast', 'more');
  else el.removeAttribute('data-contrast');
  if (p.reduceMotion) el.setAttribute('data-motion', 'reduce');
  else el.removeAttribute('data-motion');
}

/**
 * Runs inline in <head> before first paint (no theme flash, no size jump). Mirrors loadPrefs +
 * applyPrefs; it must stay tiny and must never throw. `?mode=dev` wins over the stored mode.
 */
export const PREFS_INIT_SCRIPT = `(function(){try{var d=document.documentElement,s=window.localStorage,g=function(k){try{return s.getItem(k)}catch(e){return null}};
var t=g('${PREF_KEYS.theme}');if(t==='system'){t=window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'}
d.setAttribute('data-theme',t==='light'?'light':'dark');
var x=g('${PREF_KEYS.text}');d.setAttribute('data-text',x==='115'||x==='130'?x:'100');
var l=g('${PREF_KEYS.lang}');d.setAttribute('lang',l==='zh'?'zh-Hans':'en');
var c=g('${PREF_KEYS.contrast}');if(c==='1'||c==='true')d.setAttribute('data-contrast','more');
var m=g('${PREF_KEYS.reduceMotion}');if(m==='1'||m==='true')d.setAttribute('data-motion','reduce');
var q=new URLSearchParams(location.search).get('mode');var md=location.pathname.indexOf('/dev')===0||q==='dev'?'dev':(q==='user'?'user':(g('${PREF_KEYS.mode}')==='dev'&&!/^\\/(compare|history|settings|login)?$/.test(location.pathname)?'dev':'user'));
d.setAttribute('data-mode',md);}catch(e){}})();`;

/** Routes that belong to User mode. Anything under /dev is Developer mode. The legacy pages
 *  (/map, /audit, /sim) keep whichever mode the viewer was in. */
const USER_ROUTE = /^\/(compare|history|settings|login)?(\/.*)?$/;

export function modeForPath(pathname: string, stored: Mode): Mode {
  if (pathname === '/dev' || pathname.startsWith('/dev/')) return 'dev';
  if (pathname === '/' || USER_ROUTE.test(pathname)) {
    // "/" alone matches the optional group; legacy pages do not match.
    return 'user';
  }
  return stored;
}

export const MODE_HOME: Record<Mode, string> = { user: '/', dev: '/dev' };
