// Brand: "Walnut DataBridge" (D-02). "Internal Brain" is the ask product inside it.

/** The mark: two offset strokes bridging two blocks. Neutral, single colour, no gradient. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span aria-hidden className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line-strong bg-raised text-fg ${className ?? ''}`}>
      <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2.5" y="4" width="4.5" height="12" rx="1.2" />
        <rect x="13" y="4" width="4.5" height="12" rx="1.2" />
        <path d="M7 8h6" />
        <path d="M7 12h6" className="opacity-60" />
      </svg>
    </span>
  );
}

/** Mark + "Walnut DataBridge" wordmark. `compact` hides the second word on narrow screens. */
export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <BrandMark />
      <span className="whitespace-nowrap text-sm font-semibold tracking-tight text-fg">
        Walnut<span className={compact ? 'hidden font-medium text-muted sm:inline' : 'font-medium text-muted'}> DataBridge</span>
      </span>
    </span>
  );
}

export const PRODUCT = 'Walnut DataBridge';
export const SUBTITLE = 'Ask a work question. Get an answer with its sources, using only what you can open.';

export function MockBanner() {
  if (process.env.NEXT_PUBLIC_IB_MOCK !== '1') return null;
  return (
    <div className="border-b border-line bg-raised px-4 py-1 text-center text-2xs text-muted" data-testid="mock-banner">
      Mock mode: fixtures served by this web app; no backend connected.
    </div>
  );
}
