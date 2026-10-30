'use client';
// B: the backup recording for the current step, full screen, muted, from public/clips/ (local, no
// network). Always labelled "Recording · not live" (BACKUP_PLAN.md: a recording is never presented
// as live). A missing file shows "Clip not recorded yet" with the expected path. B or Esc returns.
// A step with no recording (the slides) never opens it: B shows a short note and the slide stays
// (stage QA 30 Oct #4; controller.ts toggleClip).
import * as D from '@radix-ui/react-dialog';
import { Film, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useMessages } from '@/lib/i18n';
import { presenterMessages } from '@/lib/i18n/presenter';
import { STEPS } from './script';
import { setPresenter, usePresenter } from './store';

export function ClipOverlay() {
  const t = useMessages(presenterMessages);
  const s = usePresenter();
  const clip = STEPS[s.step]?.clip ?? null;
  const [missing, setMissing] = useState(false);
  useEffect(() => setMissing(false), [clip, s.clipOpen]);

  return (
    <>
    {/* A polite live region that is always mounted (round-5 review cross-check #2): a region that
        appears together with its text is often not announced; this one only changes its text. */}
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-testid="clip-note-region"
      className="pointer-events-none fixed inset-x-0 top-[max(0.75rem,2.5vh)] z-[60] flex justify-center px-3 print:hidden"
    >
      {s.clipNote ? (
        <p className="flex items-center gap-2 rounded-lg border border-line-strong bg-overlay px-3 py-2 text-body-sm text-fg shadow-pop" data-testid="clip-none-note">
          <Film aria-hidden className="h-4 w-4 shrink-0 text-muted" />
          {t('pr.clip.noneShort')}
        </p>
      ) : null}
    </div>
    <D.Root open={s.clipOpen} onOpenChange={(o) => setPresenter({ clipOpen: o })}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[60] bg-black" />
        <D.Content
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black text-white outline-none"
          aria-describedby={undefined}
          data-testid="clip-overlay"
        >
          <D.Title className="sr-only">{t('pr.clip.title')}</D.Title>
          {clip && !missing ? (
            // A muted screen recording with no speech: no captions track is needed.
            <video
              key={clip}
              src={`/clips/${clip}`}
              autoPlay
              muted
              playsInline
              onError={() => setMissing(true)}
              className="max-h-full max-w-full"
              data-testid="clip-video"
            />
          ) : (
            <div className="flex max-w-xl flex-col items-center px-6 text-center" data-testid="clip-missing">
              <Film aria-hidden className="mb-4 h-8 w-8 text-white/70" />
              <p className="text-heading text-white">{clip ? t('pr.clip.missing') : t('pr.clip.none')}</p>
              {clip ? <p className="mt-2 font-mono text-caption text-white/75">{t('pr.clip.expected', { file: clip })}</p> : null}
            </div>
          )}
          <p className="absolute left-[2.5vw] top-[2.5vh] rounded-md border border-white/40 bg-black/70 px-2.5 py-1 text-caption font-semibold uppercase tracking-wider text-white">
            {t('pr.clip.badge')}
          </p>
          <D.Close
            className="absolute right-[2.5vw] top-[2.5vh] inline-flex h-9 items-center gap-2 rounded-md border border-white/40 bg-black/70 px-3 text-caption font-medium text-white hover:bg-white/10 focus-visible:outline-white"
            data-testid="clip-close"
          >
            <X aria-hidden className="h-4 w-4" />
            {t('pr.clip.back')}
          </D.Close>
        </D.Content>
      </D.Portal>
    </D.Root>
    </>
  );
}
