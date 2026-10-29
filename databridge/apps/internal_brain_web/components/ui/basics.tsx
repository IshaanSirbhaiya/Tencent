// Small, stateless primitives: Separator, Kbd, Skeleton, Avatar, SrOnly, Field, Input.
import { forwardRef } from 'react';
import { cn } from '@/lib/cn';

/** A 1px rule. Decorative by default; pass decorative={false} for a semantic separator. */
export function Separator({ orientation = 'horizontal', decorative = true, className }: { orientation?: 'horizontal' | 'vertical'; decorative?: boolean; className?: string }) {
  return (
    <div
      role={decorative ? 'none' : 'separator'}
      aria-orientation={decorative ? undefined : orientation}
      className={cn('shrink-0 bg-line', orientation === 'horizontal' ? 'h-px w-full' : 'w-px self-stretch', className)}
    />
  );
}

/** Keyboard key hint. */
export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border border-line-strong bg-raised px-1 font-sans text-micro font-medium text-muted',
        className,
      )}
    >
      {children}
    </kbd>
  );
}

/** Placeholder block while data loads. Decorative: pair it with a role="status" text (see SkeletonText). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn('ds-skeleton rounded-md bg-raised', className)} />;
}

/** Skeleton text lines plus an sr-only status message. */
export function SkeletonText({ lines = 3, label = 'Loading…', className }: { lines?: number; label?: string; className?: string }) {
  return (
    <div className={cn('space-y-2', className)} role="status">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('h-3.5', i === lines - 1 ? 'w-2/3' : 'w-full')} />
      ))}
    </div>
  );
}

export function initials(name: string): string {
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return '?';
  const first = parts[0].charAt(0);
  const last = parts.length > 1 ? parts[parts.length - 1].charAt(0) : '';
  return (first + last).toUpperCase();
}

/** Initials avatar, neutral (identity is never colour-coded). Decorative unless `label` is given. */
export function Avatar({ name, size = 'md', label, className }: { name: string; size?: 'sm' | 'md' | 'lg'; label?: string; className?: string }) {
  return (
    <span
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center rounded-full border border-line-strong bg-raised font-semibold text-fg',
        size === 'sm' && 'h-6 w-6 text-micro',
        size === 'md' && 'h-7 w-7 text-micro',
        size === 'lg' && 'h-10 w-10 text-sm',
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}

export function SrOnly({ children }: { children: React.ReactNode }) {
  return <span className="sr-only">{children}</span>;
}

/** Label + control + optional hint. The control must carry id={id} (and aria-describedby={`${id}-hint`} if hinted). */
export function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-fg">
        {label}
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-2xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export const inputClass =
  'h-9 w-full min-w-0 rounded-md border border-line-strong bg-bg px-2.5 text-sm text-fg placeholder:text-subtle focus-visible:outline-2';

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cn(inputClass, className)} {...rest} />;
});
