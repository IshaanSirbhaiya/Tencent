import { cva, type VariantProps } from 'class-variance-authority';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';

export type Tone = 'neutral' | 'ok' | 'warn' | 'bad' | 'accent' | 'locked';

const badgeVariants = cva('inline-flex max-w-full items-center gap-1 whitespace-nowrap border font-medium', {
  variants: {
    tone: {
      neutral: 'border-line-strong bg-raised text-muted',
      ok: 'border-ok/40 bg-ok/10 text-ok',
      warn: 'border-warn/40 bg-warn/10 text-warn',
      bad: 'border-bad/40 bg-bad/10 text-bad',
      accent: 'border-accent/40 bg-accent/10 text-accent',
      locked: 'border-line-strong bg-raised text-subtle',
    },
    shape: { badge: 'rounded', pill: 'rounded-full' },
    size: { sm: 'px-1.5 py-0.5 text-2xs', md: 'px-2 py-0.5 text-xs' },
  },
  defaultVariants: { tone: 'neutral', shape: 'badge', size: 'sm' },
});

export interface BadgeProps extends Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'>, VariantProps<typeof badgeVariants> {
  icon?: LucideIcon;
  children: React.ReactNode;
}

/** Status badge. Semantic tones ALWAYS come with text (and ideally an icon), never colour alone. */
export function Badge({ tone, shape, size, icon: Icon, className, children, ...rest }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ tone, shape, size }), className)} {...rest}>
      {Icon ? <Icon aria-hidden className="h-3 w-3 shrink-0" /> : null}
      <span className="truncate">{children}</span>
    </span>
  );
}

/** Rounded badge for scopes, filters and classifications ("Internal · Finance"). */
export function Pill(props: Omit<BadgeProps, 'shape'>) {
  return <Badge shape="pill" size="md" {...props} />;
}
