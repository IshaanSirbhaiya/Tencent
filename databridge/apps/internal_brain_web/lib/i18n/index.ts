'use client';
// Tiny typed i18n (EN + 简体中文). No library: two dictionaries and a context.
//
// Core strings (shell, nav, settings):      const { t } = useI18n();  t('nav.ask')
// Feature strings, in the feature's OWN files, so agents never edit the same file:
//
//   // lib/i18n/user.en.ts
//   export const userEn = { 'ask.greeting': 'Good afternoon, {name}' } as const;
//   // lib/i18n/user.zh.ts
//   import type { userEn } from './user.en';
//   export const userZh: Record<keyof typeof userEn, string> = { 'ask.greeting': '下午好，{name}' };
//   // lib/i18n/user.ts
//   export const userMessages = defineMessages({ en: userEn, zh: userZh });
//   // in a component
//   const t = useMessages(userMessages);   t('ask.greeting', { name })
//
// Developer mode stays English by decision (PLAN §4): dev pages may skip i18n.
import { createContext, useCallback, useContext, useMemo } from 'react';
import type { Lang } from '../prefs';
import { en, type CoreKey } from './en';
import { zh } from './zh';

export type { Lang };
export type Vars = Record<string, string | number>;
export type Messages<K extends string> = { en: Record<K, string>; zh: Record<K, string> };

function fill(s: string, vars?: Vars): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m));
}

/** Declare a feature dictionary. The zh object must carry every en key (TypeScript checks it). */
export function defineMessages<K extends string>(m: { en: Record<K, string>; zh: Record<K, string> }): Messages<K> {
  return m;
}

export function translate<K extends string>(m: Messages<K>, lang: Lang, key: K, vars?: Vars): string {
  return fill(m[lang]?.[key] ?? m.en[key] ?? key, vars);
}

const CORE: Messages<CoreKey> = { en, zh };

interface LangState {
  lang: Lang;
  setLang: (l: Lang) => void;
}
export const LangContext = createContext<LangState>({ lang: 'en', setLang: () => undefined });

/** The current language and a setter (persists and updates <html lang>). */
export function useLang(): LangState {
  return useContext(LangContext);
}

/** Core dictionary: `t('nav.ask')`. */
export function useI18n() {
  const { lang, setLang } = useLang();
  const t = useCallback((key: CoreKey, vars?: Vars) => translate(CORE, lang, key, vars), [lang]);
  return { lang, setLang, t };
}

/** A feature dictionary from defineMessages(): `const t = useMessages(userMessages)`. */
export function useMessages<K extends string>(m: Messages<K>) {
  const { lang } = useLang();
  return useMemo(() => (key: K, vars?: Vars) => translate(m, lang, key, vars), [m, lang]);
}

export { en, zh, type CoreKey };
