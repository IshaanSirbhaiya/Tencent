// The answer's own numbers for a view's tags (the same numbers as the answer's chips). Kept apart
// from ViewSurface so the row can use it without loading A2UI (which needs a browser).
import type { Numbering } from '@/components/user/DeliverSheet';
import type { AskResponse, Citation, DeliverCitation, JitView } from '@/lib/types';

export function viewNumbering(response: AskResponse, v: JitView): Numbering {
  const byTag = new Map<string, { n: number; c?: Citation; i: number; dc?: DeliverCitation }>();
  response.citations.forEach((c, i) => byTag.set(c.tag, { n: i + 1, c, i }));
  for (const dc of v.citations) {
    const cur = byTag.get(dc.tag);
    byTag.set(dc.tag, { n: dc.n || cur?.n || 0, c: cur?.c, i: cur?.i ?? -1, dc });
  }
  return byTag;
}
