export function BrandMark() {
  return (
    <span
      aria-hidden
      className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-line-strong bg-raised text-[11px] font-semibold tracking-tight text-fg"
    >
      IB
    </span>
  );
}

export const PRODUCT = 'Walnut Internal Brain';
export const SUBTITLE = "Answers only from what you're allowed to see — every access audited";

export function MockBanner() {
  if (process.env.NEXT_PUBLIC_IB_MOCK !== '1') return null;
  return (
    <div className="border-b border-line bg-raised px-4 py-1 text-center text-2xs text-muted">
      Mock mode: fixtures served by this web app; no backend connected.
    </div>
  );
}
