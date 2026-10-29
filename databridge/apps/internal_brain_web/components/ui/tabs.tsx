'use client';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { forwardRef } from 'react';
import { cn } from '@/lib/cn';

/** Radix Tabs: arrow keys move between tabs, Home/End jump, the panel is labelled by its tab. */
export const Tabs = TabsPrimitive.Root;

export const TabsList = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof TabsPrimitive.List> & { variant?: 'underline' | 'segmented' }>(
  function TabsList({ className, variant = 'underline', ...rest }, ref) {
    return (
      <TabsPrimitive.List
        ref={ref}
        className={cn(
          'flex max-w-full items-center overflow-x-auto overflow-y-hidden',
          variant === 'underline' ? 'gap-1 shadow-[inset_0_-1px_0_rgb(var(--line))]' : 'inline-flex gap-0.5 rounded-md border border-line bg-raised p-0.5',
          className,
        )}
        data-variant={variant}
        {...rest}
      />
    );
  },
);

export const TabsTrigger = forwardRef<HTMLButtonElement, React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>>(function TabsTrigger(
  { className, ...rest },
  ref,
) {
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        'inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap px-2.5 text-sm font-medium text-muted transition-colors duration-fast hover:text-fg focus-visible:outline-offset-[-2px] disabled:opacity-50',
        // underline variant (default)
        '[[data-variant=underline]_&]:border-b-2 [[data-variant=underline]_&]:border-transparent [[data-variant=underline]_&]:data-[state=active]:border-accent [[data-variant=underline]_&]:data-[state=active]:text-fg',
        // segmented variant
        '[[data-variant=segmented]_&]:h-7 [[data-variant=segmented]_&]:rounded [[data-variant=segmented]_&]:data-[state=active]:bg-surface [[data-variant=segmented]_&]:data-[state=active]:text-fg [[data-variant=segmented]_&]:data-[state=active]:shadow-[0_0_0_1px_rgb(var(--line-strong))]',
        className,
      )}
      {...rest}
    />
  );
});

export const TabsContent = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>>(function TabsContent(
  { className, ...rest },
  ref,
) {
  return <TabsPrimitive.Content ref={ref} className={cn('pt-3 focus-visible:outline-2', className)} {...rest} />;
});
