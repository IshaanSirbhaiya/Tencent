'use client';
// The one seam between Compare and the User-mode answer components. Compare imports answers only
// from here, so a change in components/user is absorbed in one place.
export { AnswerCard, AskProgressLine, RefusalCard, CitationChip, SourcesSheet, isRefusal } from '@/components/user';
import type { AskResponse } from '@/lib/types';

/**
 * Two responses are identical when what each person SAW is the same: the answer text, the
 * citations and the scope note. Request ids and times are per-request metadata, not the response.
 * A display comparison of two visible responses; nothing about hidden documents is computed.
 */
export function sameResponse(a: AskResponse, b: AskResponse): boolean {
  return a.answer === b.answer && a.scope_note === b.scope_note && JSON.stringify(a.citations) === JSON.stringify(b.citations);
}
