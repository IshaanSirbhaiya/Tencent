import { clsx } from 'clsx';
import type { LucideIcon } from 'lucide-react';
import { forwardRef } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost';

export const Button = forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' | 'md'; icon?: LucideIcon; busy?: boolean }
>(function Button({ variant = 'secondary', size = 'md', icon: Icon, busy, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={clsx(
        'inline-flex shrink-0 items-center justify-center gap-2 rounded-md font-medium transition-colors duration-150',
        'disabled:cursor-not-allowed disabled:opacity-60',
        size === 'sm' ? 'h-8 px-2.5 text-xs' : 'h-10 px-4 text-sm',
        variant === 'primary' && 'bg-accent-solid text-accent-fg hover:bg-accent-solid/90',
        variant === 'secondary' && 'border border-line-strong bg-surface text-fg hover:bg-raised',
        variant === 'ghost' && 'text-muted hover:bg-raised hover:text-fg',
        className,
      )}
      {...rest}
    >
      {Icon ? <Icon aria-hidden className={size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4'} /> : null}
      {children}
    </button>
  );
});

export type Tone = 'neutral' | 'ok' | 'warn' | 'bad' | 'accent';

/** Status badge: ALWAYS icon + text, never colour alone. */
export function Badge({ tone = 'neutral', icon: Icon, children, title, className }: { tone?: Tone; icon?: LucideIcon; children: React.ReactNode; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={clsx(
        'inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-2xs font-medium',
        tone === 'neutral' && 'border-line-strong bg-raised text-muted',
        tone === 'ok' && 'border-ok/40 bg-ok/10 text-ok',
        tone === 'warn' && 'border-warn/40 bg-warn/10 text-warn',
        tone === 'bad' && 'border-bad/40 bg-bad/10 text-bad',
        tone === 'accent' && 'border-accent/40 bg-accent/10 text-accent',
        className,
      )}
    >
      {Icon ? <Icon aria-hidden className="h-3 w-3 shrink-0" /> : null}
      {children}
    </span>
  );
}

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLElement> & { as?: 'section' | 'div' | 'aside' }) {
  return (
    <section className={clsx('rounded-lg border border-line bg-surface', className)} {...rest}>
      {children}
    </section>
  );
}

export function SrOnly({ children }: { children: React.ReactNode }) {
  return <span className="sr-only">{children}</span>;
}

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
  'h-9 w-full min-w-0 rounded-md border border-line-strong bg-bg px-2.5 text-sm text-fg placeholder:text-muted/80 focus-visible:outline-2';
