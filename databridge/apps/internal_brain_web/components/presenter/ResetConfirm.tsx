'use client';
import { RotateCcw } from 'lucide-react';
import { useRef } from 'react';
import { Button, Dialog, DialogClose, DialogContent } from '@/components/ui';
import { useMessages } from '@/lib/i18n';
import { presenterMessages } from '@/lib/i18n/presenter';
import { resetDemo } from './controller';
import { setPresenter, usePresenter } from './store';

/** R: confirm, then sign every demo session out, clear presenter state, back to step 0. */
export function ResetConfirm() {
  const t = useMessages(presenterMessages);
  const s = usePresenter();
  const confirmRef = useRef<HTMLButtonElement>(null);
  return (
    <Dialog open={s.resetOpen} onOpenChange={(o) => setPresenter({ resetOpen: o })}>
      <DialogContent
        size="sm"
        title={t('pr.reset.title')}
        description={t('pr.reset.body')}
        closeLabel={t('pr.reset.cancel')}
        data-testid="presenter-reset"
        bodyClassName="hidden"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          confirmRef.current?.focus();
        }}
        footer={
          <>
            <DialogClose asChild>
              <Button variant="secondary">{t('pr.reset.cancel')}</Button>
            </DialogClose>
            <Button ref={confirmRef} variant="primary" icon={RotateCcw} onClick={() => void resetDemo()} data-testid="presenter-reset-confirm">
              {t('pr.reset.confirm')}
            </Button>
          </>
        }
      >
        {null}
      </DialogContent>
    </Dialog>
  );
}
