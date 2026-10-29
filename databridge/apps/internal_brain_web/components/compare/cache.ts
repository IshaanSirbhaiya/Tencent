'use client';
// Finished Compare results, kept in memory for this tab (never stored), keyed by the exact
// comparison. Coming back to the same comparison (the browser Back button, or the Presenter's
// step 3) shows the earlier answers instead of asking again. A page reload starts clean.
import type { AskResponse } from '@/lib/types';
import { specKey, type CompareSpec } from './params';

export interface DoneSide {
  kind: 'done';
  persona: string;
  sub: string;
  name: string;
  question: string;
  res: AskResponse;
}

const cache = new Map<string, { left: DoneSide; right: DoneSide }>();

export function cachedResult(spec: CompareSpec) {
  return cache.get(specKey(spec)) ?? null;
}

export function cacheResult(spec: CompareSpec, left: DoneSide, right: DoneSide) {
  cache.set(specKey(spec), { left, right });
}

export function clearCompareCache() {
  cache.clear();
}

/** Drop one comparison (a new run of it starts: an older result must not be restored instead). */
export function forgetResult(spec: CompareSpec) {
  cache.delete(specKey(spec));
}
