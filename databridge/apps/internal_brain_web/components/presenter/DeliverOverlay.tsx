'use client';
// The D key's screen: Leo's cached answer (behind) and the deliverables panel (front), opened from
// the warm-up cache with no request (components/presenter/deliver.ts). Esc closes it.
import { AnswerCard } from '@/components/user/AnswerCard';
import { DeliverSheet } from '@/components/user/DeliverSheet';
import { closeDeliverOverlay, setDeliverKind, useDeliverOverlay } from './deliver';

export function DeliverOverlay() {
  const s = useDeliverOverlay();
  if (!s.open || !s.cache) return null;
  const { response, deliverables, kinds } = s.cache;
  return (
    <>
      <div role="region" aria-label="Answer" className="fixed inset-0 z-40 overflow-y-auto bg-bg px-6 py-8" data-testid="presenter-deliver-answer">
        <div className="mx-auto max-w-3xl">
          <AnswerCard response={response} onCite={() => undefined} latest={false} idPrefix="pd-" showJourney={false} compact />
        </div>
      </div>
      <DeliverSheet
        response={response}
        kinds={kinds}
        kind={s.kind}
        onKind={setDeliverKind}
        onClose={closeDeliverOverlay}
        preloaded={deliverables}
      />
    </>
  );
}
