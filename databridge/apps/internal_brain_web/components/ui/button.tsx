'use client';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { Loader2, type LucideIcon } from 'lucide-react';
import { forwardRef } from 'react';
import { cn } from '@/lib/cn';

export const buttonVariants = cva(
  'inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-colors duration-fast ease-out disabled:pointer-events-none disabled:opacity-55 aria-disabled:opacity-55',
  {
    variants: {
      variant: {
        primary: 'bg-accent-solid text-accent-fg hover:bg-accent-solid/90 active:bg-accent-solid/80',
        secondary: 'border border-line-strong bg-surface text-fg hover:bg-raised active:bg-raised/70',
        ghost: 'text-muted hover:bg-raised hover:text-fg',
        outline: 'border border-line bg-transparent text-fg hover:border-line-strong hover:bg-raised',
        danger: 'border border-bad/50 bg-bad/10 text-bad hover:bg-bad/15',
        link: 'h-auto px-0 text-accent underline-offset-4 hover:underline',
      },
      size: {
        xs: 'h-7 px-2 text-caption',
        sm: 'h-8 px-2.5 text-xs',
        md: 'h-9 px-3.5 text-sm',
        lg: 'h-10 px-4 text-sm',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Leading icon (decorative). */
  icon?: LucideIcon;
  /** Trailing icon (decorative). */
  iconRight?: LucideIcon;
  /** Shows a spinner, disables the button and sets aria-busy. */
  busy?: boolean;
  /** Render the child element (e.g. a next/link) with button styles. Icons are not added. */
  asChild?: boolean;
}

/** The one button. Variants: primary (one per area), secondary (default), ghost, outline, danger, link. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, icon: Icon, iconRight: IconRight, busy, asChild, className, children, disabled, type, ...rest },
  ref,
) {
  const iconCls = size === 'xs' || size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4';
  const classes = cn(buttonVariants({ variant, size }), className);
  if (asChild) {
    return (
      <Slot ref={ref} className={classes} {...rest}>
        {children}
      </Slot>
    );
  }
  return (
    <button ref={ref} type={type ?? 'button'} disabled={disabled || busy} aria-busy={busy || undefined} className={classes} {...rest}>
      {busy ? <Loader2 aria-hidden className={cn(iconCls, 'motion-safe:animate-spin')} /> : Icon ? <Icon aria-hidden className={iconCls} /> : null}
      {children}
      {IconRight ? <IconRight aria-hidden className={iconCls} /> : null}
    </button>
  );
});

const iconButtonVariants = cva(
  'inline-flex shrink-0 items-center justify-center rounded-md transition-colors duration-fast ease-out disabled:pointer-events-none disabled:opacity-55',
  {
    variants: {
      variant: {
        ghost: 'text-muted hover:bg-raised hover:text-fg data-[state=open]:bg-raised data-[state=open]:text-fg',
        secondary: 'border border-line-strong bg-surface text-fg hover:bg-raised',
        primary: 'bg-accent-solid text-accent-fg hover:bg-accent-solid/90',
      },
      size: { sm: 'h-7 w-7', md: 'h-8 w-8', lg: 'h-9 w-9' },
    },
    defaultVariants: { variant: 'ghost', size: 'md' },
  },
);

export interface IconButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'>, VariantProps<typeof iconButtonVariants> {
  icon: LucideIcon;
  /** Required accessible name (also the native tooltip unless `noTitle`). */
  label: string;
  /** Skip the native title tooltip (e.g. when wrapped in <Tooltip>). */
  noTitle?: boolean;
}

/** Square icon-only button. Always named; minimum 28px target (WCAG 2.5.8 asks for 24px). */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon: Icon, label, noTitle, variant, size, className, type, title, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      aria-label={label}
      title={noTitle ? undefined : title ?? label}
      className={cn(iconButtonVariants({ variant, size }), className)}
      {...rest}
    >
      <Icon aria-hidden className={size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4'} />
    </button>
  );
});
