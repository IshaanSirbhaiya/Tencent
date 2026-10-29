'use client';
import { CheckCircle2, X } from 'lucide-react';
import { useEffect, useSyncExternalStore } from 'react';
import { useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';

// A tiny toast: one message at a time, announced politely, gone after a few seconds.
let current: { id: number; text: string } | null = null;
let seq = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function toast(text: string) {
  current = { id: ++seq, text };
  emit();
}
function dismiss(id?: number) {
  if (!current || (id !== undefined && current.id !== id)) return;
  current = null;
  emit();
}

/** Mount once per page that calls toast(). */
export function Toaster() {
  const t = useMessages(userMessages);
  const msg = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
    () => null,
  );
  useEffect(() => {
    if (!msg) return;
    const h = setTimeout(() => dismiss(msg.id), 3200);
    return () => clearTimeout(h);
  }, [msg]);
  return (
    <div className="pointer-events-none fixed inset-x-0 top-[calc(var(--topbar-h)+var(--banner-h,0px)+12px)] z-50 flex justify-center px-4">
      <div role="status" aria-live="polite" className="contents">
        {msg ? (
          <div
            key={msg.id}
            className="pointer-events-auto flex max-w-md items-center gap-2.5 rounded-lg border border-line-strong bg-overlay py-2 pl-3 pr-1.5 text-sm text-fg shadow-pop motion-safe:animate-[ds-in_160ms_cubic-bezier(0.2,0,0,1)]"
            data-testid="toast"
          >
            <CheckCircle2 aria-hidden className="h-4 w-4 shrink-0 text-ok" />
            <span className="min-w-0">{msg.text}</span>
            <button
              type="button"
              onClick={() => dismiss()}
              aria-label={t('toast.dismiss')}
              className="ml-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted hover:bg-raised hover:text-fg"
            >
              <X aria-hidden className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
