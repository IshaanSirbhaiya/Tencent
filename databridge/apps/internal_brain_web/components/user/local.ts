// Per-device User-mode state (never sent anywhere). Every access is inside try/catch: blocked
// storage simply means nothing is remembered.
export type FeedbackKind = 'helpful' | 'wrong' | 'outdated';

const FEEDBACK_KEY = 'ib-user-feedback';

/** Keys this device may hold for User mode; "Reset my local data" clears them. */
export const LOCAL_KEYS = [FEEDBACK_KEY, 'ib-tour-offer', 'ib-tour-done', 'ib-tour-checklist'] as const;
export const SESSION_KEYS = ['ib-tour', 'ib-tour-last-req'] as const;

function readAll(): Record<string, FeedbackKind> {
  try {
    const raw = window.localStorage.getItem(FEEDBACK_KEY);
    const v = raw ? (JSON.parse(raw) as unknown) : {};
    return v && typeof v === 'object' ? (v as Record<string, FeedbackKind>) : {};
  } catch {
    return {};
  }
}

export function getFeedback(requestId: string): FeedbackKind | null {
  return readAll()[requestId] ?? null;
}

export function setFeedback(requestId: string, kind: FeedbackKind | null) {
  try {
    const all = readAll();
    if (kind) all[requestId] = kind;
    else delete all[requestId];
    // keep it small: the newest 200 entries
    const entries = Object.entries(all).slice(-200);
    window.localStorage.setItem(FEEDBACK_KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch {
    /* storage unavailable */
  }
}

export function clearLocalData() {
  for (const k of LOCAL_KEYS) {
    try {
      window.localStorage.removeItem(k);
    } catch {
      /* storage unavailable */
    }
  }
  for (const k of SESSION_KEYS) {
    try {
      window.sessionStorage.removeItem(k);
    } catch {
      /* storage unavailable */
    }
  }
}
