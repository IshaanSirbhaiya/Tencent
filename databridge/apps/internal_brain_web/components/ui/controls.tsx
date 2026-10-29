'use client';
// Switch, SegmentedControl, ScrollArea.
import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area';
import * as SwitchPrimitive from '@radix-ui/react-switch';
import type { LucideIcon } from 'lucide-react';
import { forwardRef, useId, useRef } from 'react';
import { cn } from '@/lib/cn';

/* ---------------------------------------------------------------- Switch */

export const Switch = forwardRef<HTMLButtonElement, React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>>(function Switch(
  { className, ...rest },
  ref,
) {
  return (
    <SwitchPrimitive.Root
      ref={ref}
      className={cn(
        'peer inline-flex h-6 w-10 shrink-0 cursor-pointer items-center rounded-full border border-line-strong bg-raised p-0.5 transition-colors duration-fast',
        'data-[state=checked]:border-accent-solid data-[state=checked]:bg-accent-solid disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...rest}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block h-[1.125rem] w-[1.125rem] rounded-full bg-fg transition-transform duration-fast ease-out data-[state=checked]:translate-x-4 data-[state=checked]:bg-accent-fg" />
    </SwitchPrimitive.Root>
  );
});

/** A labelled switch row (label + optional hint on the left, switch on the right). */
export function SwitchField({
  label,
  hint,
  checked,
  onCheckedChange,
  disabled,
  id,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  disabled?: boolean;
  id?: string;
}) {
  const auto = useId();
  const sid = id ?? `sw-${auto}`;
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0">
        <label htmlFor={sid} className="text-sm font-medium text-fg">
          {label}
        </label>
        {hint ? (
          <p id={`${sid}-hint`} className="text-caption text-muted">
            {hint}
          </p>
        ) : null}
      </div>
      <Switch id={sid} checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} aria-describedby={hint ? `${sid}-hint` : undefined} />
    </div>
  );
}

/* ---------------------------------------------------------------- SegmentedControl */

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: LucideIcon;
  /** Hide the text visually (icon-only segment); it stays the accessible name. */
  iconOnly?: boolean;
  title?: string;
}

/**
 * Single-choice segmented control with radio-group semantics: one Tab stop, arrow keys move and
 * select (WAI-ARIA radio group pattern). Used by the mode switch, theme and text size.
 */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  label,
  size = 'md',
  className,
  testId,
}: {
  value: T;
  onChange: (v: T) => void;
  options: SegmentOption<T>[];
  /** Accessible name of the group. */
  label: string;
  size?: 'sm' | 'md';
  className?: string;
  testId?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const idx = Math.max(0, options.findIndex((o) => o.value === value));
  const move = (to: number) => {
    const n = (to + options.length) % options.length;
    onChange(options[n].value);
    refs.current[n]?.focus();
  };
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className={cn('inline-flex shrink-0 items-center gap-0.5 rounded-md border border-line bg-raised p-0.5', className)}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
          e.preventDefault();
          move(idx + 1);
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
          e.preventDefault();
          move(idx - 1);
        } else if (e.key === 'Home') {
          e.preventDefault();
          move(0);
        } else if (e.key === 'End') {
          e.preventDefault();
          move(options.length - 1);
        }
      }}
    >
      {options.map((o, i) => {
        const on = o.value === value;
        const Icon = o.icon;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={o.iconOnly ? o.label : undefined}
            title={o.title ?? (o.iconOnly ? o.label : undefined)}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(o.value)}
            data-value={o.value}
            className={cn(
              'inline-flex items-center justify-center gap-1.5 rounded font-medium transition-colors duration-fast',
              size === 'sm' ? 'h-6 min-w-[1.5rem] px-2 text-caption' : 'h-7 min-w-[1.75rem] px-2.5 text-xs',
              on ? 'bg-surface text-fg shadow-[0_0_0_1px_rgb(var(--line-strong))]' : 'text-muted hover:text-fg',
            )}
          >
            {Icon ? <Icon aria-hidden className="h-3.5 w-3.5" /> : null}
            {o.iconOnly ? null : <span>{o.label}</span>}
          </button>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- ScrollArea */

/** Scroll container with a thin custom scrollbar. Give it a height (className="h-64"). */
export const ScrollArea = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> & { viewportLabel?: string }>(
  function ScrollArea({ className, children, viewportLabel, ...rest }, ref) {
    return (
      <ScrollAreaPrimitive.Root ref={ref} className={cn('relative overflow-hidden', className)} {...rest}>
        <ScrollAreaPrimitive.Viewport
          className="h-full w-full rounded-[inherit]"
          // a scrollable region must be reachable by keyboard (axe: scrollable-region-focusable)
          tabIndex={0}
          aria-label={viewportLabel}
          role={viewportLabel ? 'region' : undefined}
        >
          {children}
        </ScrollAreaPrimitive.Viewport>
        <ScrollAreaPrimitive.Scrollbar orientation="vertical" className="flex w-2.5 touch-none select-none p-0.5">
          <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-line-strong" />
        </ScrollAreaPrimitive.Scrollbar>
        <ScrollAreaPrimitive.Scrollbar orientation="horizontal" className="flex h-2.5 touch-none select-none flex-col p-0.5">
          <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-line-strong" />
        </ScrollAreaPrimitive.Scrollbar>
        <ScrollAreaPrimitive.Corner />
      </ScrollAreaPrimitive.Root>
    );
  },
);
