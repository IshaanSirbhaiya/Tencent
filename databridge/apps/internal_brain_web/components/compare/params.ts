// Compare deep links. Two shapes (the Presenter builds them; anyone can share them):
//   /compare?a=amy.am&b=mlro.tan&q=…                     same question, two people
//   /compare?mode=person&p=ken.ext&q1=…&q2=…             same person, two questions
// The spec's alternative names are accepted too (?mode=people&left=&right=, ?person=).
// Persona ids are checked against the fixed list; anything else falls back to the defaults.
import { PERSONAS, SAMPLE_QUESTION } from '@/lib/personas';

export type CompareMode = 'people' | 'person';

export interface CompareSpec {
  mode: CompareMode;
  /** Persona per column (both the same in person mode). */
  left: string;
  right: string;
  /** Question per column (both the same in people mode). */
  q1: string;
  q2: string;
}

/** People who can be compared (the platform simulator operator is not a knowledge worker). */
export const CHOICES = PERSONAS.filter((p) => p.id !== 'admin.it');
const IDS = new Set(CHOICES.map((p) => p.id));
const MAX_Q = 2000;

export const DEFAULT_SPEC: CompareSpec = { mode: 'people', left: 'jdoe', right: 'ken.ext', q1: SAMPLE_QUESTION, q2: SAMPLE_QUESTION };

const persona = (v: string | null, fallback: string) => (v && IDS.has(v) ? v : fallback);
const question = (v: string | null, fallback: string) => {
  const s = (v ?? '').trim();
  return s && s.length <= MAX_Q ? s : fallback;
};

export function parseSpec(p: URLSearchParams | null): CompareSpec {
  if (!p) return DEFAULT_SPEC;
  const mode: CompareMode = p.get('mode') === 'person' ? 'person' : 'people';
  if (mode === 'person') {
    const who = persona(p.get('p') ?? p.get('person'), 'ken.ext');
    const q1 = question(p.get('q1') ?? p.get('q'), SAMPLE_QUESTION);
    return { mode, left: who, right: who, q1, q2: question(p.get('q2'), q1) };
  }
  const q = question(p.get('q'), SAMPLE_QUESTION);
  return {
    mode,
    left: persona(p.get('a') ?? p.get('left'), DEFAULT_SPEC.left),
    right: persona(p.get('b') ?? p.get('right'), DEFAULT_SPEC.right),
    q1: q,
    q2: q,
  };
}

export function specUrl(s: CompareSpec): string {
  const u = new URLSearchParams();
  if (s.mode === 'person') {
    u.set('mode', 'person');
    u.set('p', s.left);
    u.set('q1', s.q1);
    u.set('q2', s.q2);
  } else {
    u.set('a', s.left);
    u.set('b', s.right);
    u.set('q', s.q1);
  }
  return `/compare?${u.toString()}`;
}

export const peopleUrl = (a: string, b: string, q: string) => specUrl({ mode: 'people', left: a, right: b, q1: q, q2: q });
export const personUrl = (p: string, q1: string, q2: string) => specUrl({ mode: 'person', left: p, right: p, q1, q2 });

export function specKey(s: CompareSpec): string {
  return JSON.stringify([s.mode, s.left, s.right, s.q1.trim(), s.q2.trim()]);
}

export function sameSpec(a: CompareSpec, b: CompareSpec): boolean {
  return specKey(a) === specKey(b);
}
