'use client';
// The canvas on the Ask page: when an answer needs a page (decided per answer, like a chat
// assistant that sometimes opens a canvas), the chat moves left and the page opens here, beside it;
// below 1024 px it opens as a full-screen sheet instead. An Xcode-like toolbar (the page's title,
// Copy, Full screen, Close) and, under the page, "Change this page": a request in the person's own
// words ("make the table black") that rebuilds the page. The page itself is ViewResult.
import { Check, Copy, Expand, FileText, Sparkles, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Sheet, SheetContent } from '@/components/ui';
import { useMessages } from '@/lib/i18n';
import { viewMessages } from '@/lib/i18n/view';
import type { AskResponse } from '@/lib/types';
import { toast } from './Toast';
import { ViewResult, viewPlainText, type ViewLoad } from './ViewRow';

export interface CanvasState {
  turnId: number;
  response: AskResponse;
  /** the question as asked (the page's subject) */
  question: string;
  /** what the page was built from: the question, or a change asked for since */
  instruction: string;
  /** the title while the page is being built (from the decision) */
  title?: string;
  load: ViewLoad;
  /** the person asked for this page: focus moves to its title when it is ready */
  focus: boolean;
}

function useWide(): boolean {
  const [wide, setWide] = useState(true);
  useEffect(() => {
    const m = window.matchMedia('(min-width: 1024px)');
    const on = () => setWide(m.matches);
    on();
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return wide;
}

export function useCanvasIsInline(): boolean {
  return useWide();
}

export function ViewCanvas({
  canvas,
  onClose,
  onChange,
}: {
  canvas: CanvasState | null;
  onClose: () => void;
  /** a change to the page in the person's own words ("" rebuilds it as it was asked) */
  onChange: (change: string) => void;
}) {
  const t = useMessages(viewMessages);
  const wide = useWide();
  const [full, setFull] = useState(false);
  const open = canvas !== null;
  // keep the last page on screen while the panel animates closed
  const [shown, setShown] = useState<CanvasState | null>(canvas);
  useEffect(() => {
    if (canvas) setShown(canvas);
  }, [canvas]);

  const body = shown ? <CanvasBody c={shown} onChange={onChange} onClose={onClose} onFull={() => setFull(true)} /> : null;

  return (
    <>
      {wide ? (
        <aside
          className="ask-canvas"
          aria-label={t('view.canvas')}
          aria-hidden={!open || undefined}
          data-testid="view-canvas"
          data-open={open ? 'true' : 'false'}
          {...(!open ? { inert: '' as unknown as boolean } : {})}
        >
          <div className="sticky top-[calc(var(--topbar-h)+1rem)] flex h-[calc(100dvh-var(--topbar-h)-2rem)] min-w-[28rem] flex-col overflow-hidden rounded-[14px] border border-line bg-surface shadow-[var(--ask-shadow)]">
            {body}
          </div>
        </aside>
      ) : (
        <Sheet open={open} onOpenChange={(o) => (o ? undefined : onClose())}>
          {open && body ? (
            <SheetContent width="xl" title={t('view.fullTitle')} description={t('view.fullDesc')} closeLabel={t('view.close')} data-testid="view-canvas-sheet" bodyClassName="p-0">
              <div className="flex h-full flex-col">{body}</div>
            </SheetContent>
          ) : null}
        </Sheet>
      )}
      <Sheet open={full && shown?.load.state === 'ok'} onOpenChange={(o) => setFull(o)}>
        {full && shown && shown.load.state === 'ok' ? (
          <SheetContent width="xl" title={t('view.fullTitle')} description={t('view.fullDesc')} closeLabel={t('view.close')} data-testid="view-sheet">
            <ViewResult response={shown.response} load={shown.load} t={t} onRetry={() => onChange('')} actions={false} />
          </SheetContent>
        ) : null}
      </Sheet>
    </>
  );
}

function CanvasBody({ c, onChange, onClose, onFull }: { c: CanvasState; onChange: (change: string) => void; onClose: () => void; onFull: () => void }) {
  const t = useMessages(viewMessages);
  const titleId = useId();
  const inputId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const [copied, setCopied] = useState(false);
  const [change, setChange] = useState('');
  const ready = c.load.state === 'ok' && c.load.v.status === 'ready' ? c.load.v : null;
  const busy = c.load.state === 'loading';
  const title = ready?.title ?? c.title ?? c.question;

  useEffect(() => {
    if (ready && c.focus) titleRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready?.view_id]);

  const copy = async () => {
    if (!ready) return;
    try {
      await navigator.clipboard.writeText(viewPlainText(c.response, ready));
      setCopied(true);
      toast(t('view.copied'));
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast(t('view.copyFailed'));
    }
  };
  const icon =
    'inline-flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors duration-fast hover:bg-raised hover:text-fg disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus';

  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !e.defaultPrevented) {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      {/* the toolbar */}
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface/90 px-3 backdrop-blur" data-testid="view-canvas-toolbar">
        <span aria-hidden className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-raised text-accent">
          <FileText className="h-4 w-4" />
        </span>
        <h2 id={titleId} ref={titleRef} tabIndex={-1} lang="en" className="min-w-0 flex-1 truncate text-body-sm font-semibold text-fg outline-none" title={title}>
          {title}
        </h2>
        <div className="flex shrink-0 items-center gap-0.5">
          <button type="button" className={icon} onClick={() => void copy()} disabled={!ready} aria-label={t('view.copy')} title={t('view.copy')} data-testid="view-copy">
            {copied ? <Check aria-hidden className="h-4 w-4 text-ok" /> : <Copy aria-hidden className="h-4 w-4" />}
          </button>
          <button type="button" className={icon} onClick={onFull} disabled={!ready} aria-label={t('view.open')} title={t('view.open')} data-testid="view-open">
            <Expand aria-hidden className="h-4 w-4" />
          </button>
          <button type="button" className={icon} onClick={onClose} aria-label={t('view.closeCanvas')} title={t('view.closeCanvas')} data-testid="view-canvas-close">
            <X aria-hidden className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* the page */}
      <div className="min-h-0 flex-1 overflow-y-auto" data-testid="view-canvas-body">
        <div className="mx-auto w-full max-w-[54rem] px-6 py-7 lg:px-10 lg:py-9" aria-labelledby={titleId}>
          <ViewResult response={c.response} load={c.load} t={t} onRetry={() => onChange('')} actions={false} />
        </div>
      </div>

      {/* change this page, in the person's own words */}
      <form
        className="shrink-0 border-t border-line bg-surface px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          const v = change.trim();
          if (!v || busy) return;
          onChange(v);
          setChange('');
        }}
        data-testid="view-change"
      >
        <label htmlFor={inputId} className="sr-only">
          {t('view.changeLabel')}
        </label>
        <div className="flex items-center gap-2 rounded-full border border-line bg-bg px-3 py-1.5 focus-within:border-focus/60 focus-within:ring-4 focus-within:ring-focus/10">
          <Sparkles aria-hidden className="h-4 w-4 shrink-0 text-accent" />
          <input
            id={inputId}
            value={change}
            maxLength={200}
            onChange={(e) => setChange(e.target.value)}
            placeholder={t('view.changePlaceholder')}
            disabled={busy}
            data-testid="view-change-input"
            className="h-7 min-w-0 flex-1 bg-transparent text-body-sm text-fg placeholder:text-subtle focus:outline-none disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={busy || !change.trim()}
            className="h-7 shrink-0 rounded-full bg-accent-solid px-3 text-caption font-semibold text-accent-fg transition-colors duration-fast hover:bg-accent-solid/90 disabled:bg-raised disabled:text-subtle"
            data-testid="view-change-send"
          >
            {t('view.changeSend')}
          </button>
        </div>
      </form>
    </div>
  );
}
