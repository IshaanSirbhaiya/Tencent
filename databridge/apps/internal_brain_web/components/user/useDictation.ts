'use client';
// Browser voice dictation (Web Speech API). Nothing is stored here and no audio touches our servers:
// the browser's own speech service does the recognising. Never auto-sends; the caller only gets text.
import { useCallback, useEffect, useRef, useState } from 'react';

interface Alt {
  transcript: string;
}
interface Res {
  readonly length: number;
  [i: number]: Alt;
}
interface ResEvent {
  resultIndex: number;
  results: { readonly length: number; [i: number]: Res };
}
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: ResEvent) => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}
type Ctor = new () => Recognition;

function getCtor(): Ctor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { SpeechRecognition?: Ctor; webkitSpeechRecognition?: Ctor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export type DictationState = 'idle' | 'listening' | 'stopped' | 'denied' | 'error';

/**
 * `getValue` returns the current text; dictated text is appended after what was there when dictation
 * began. `onText` receives the full new value (interim results included).
 */
export function useDictation({ lang, getValue, onText }: { lang: string; getValue: () => string; onText: (v: string) => void }) {
  const [supported, setSupported] = useState(false);
  const [state, setState] = useState<DictationState>('idle');
  const rec = useRef<Recognition | null>(null);
  const getRef = useRef(getValue);
  const textRef = useRef(onText);
  getRef.current = getValue;
  textRef.current = onText;

  useEffect(() => {
    setSupported(!!getCtor());
  }, []);

  const stop = useCallback(() => {
    const r = rec.current;
    rec.current = null;
    if (r) {
      r.onresult = r.onerror = r.onend = null;
      try {
        r.stop();
      } catch {
        /* already stopped */
      }
      setState((s) => (s === 'listening' ? 'stopped' : s));
    }
  }, []);

  const start = useCallback(() => {
    const C = getCtor();
    if (!C || rec.current) return;
    const base = getRef.current();
    const r = new C();
    r.lang = lang;
    r.continuous = true;
    r.interimResults = true;
    r.onresult = (e) => {
      // A result that arrives after this session was stopped (Send, typing, Escape) is dropped, so it can
      // never write an old question back into a box that was cleared or edited.
      if (rec.current !== r) return;
      let text = '';
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0]?.transcript ?? '';
      const sep = base && !/\s$/.test(base) && text && !/^[\s，。,.]/.test(text) ? ' ' : '';
      textRef.current(base + sep + text);
    };
    r.onerror = (e) => {
      rec.current = null;
      setState(e.error === 'not-allowed' || e.error === 'service-not-allowed' ? 'denied' : e.error === 'aborted' || e.error === 'no-speech' ? 'stopped' : 'error');
    };
    r.onend = () => {
      rec.current = null;
      setState((s) => (s === 'listening' ? 'stopped' : s));
    };
    rec.current = r;
    try {
      r.start();
      setState('listening');
    } catch {
      rec.current = null;
      setState('error');
    }
  }, [lang]);

  const toggle = useCallback(() => (rec.current ? stop() : start()), [start, stop]);

  // Stop if the language changes mid-dictation or the component goes away.
  useEffect(() => stop, [lang, stop]);

  return { supported, state, listening: state === 'listening', start, stop, toggle };
}
