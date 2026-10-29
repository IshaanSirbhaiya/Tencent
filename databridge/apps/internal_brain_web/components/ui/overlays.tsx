'use client';
// Floating layers on Radix: Tooltip, Popover, DropdownMenu. All portal to <body>, close on Esc,
// return focus to the trigger, and animate with the .ds-pop class (stilled by reduced motion).
import * as DropdownPrimitive from '@radix-ui/react-dropdown-menu';
import * as PopoverPrimitive from '@radix-ui/react-popover';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { Check, ChevronRight } from 'lucide-react';
import { forwardRef } from 'react';
import { cn } from '@/lib/cn';

const panel = 'ds-pop z-50 rounded-lg border border-line bg-overlay text-fg shadow-pop outline-none';

/* ---------------------------------------------------------------- Tooltip */

/** Mounted once by the shell's Providers. */
export const TooltipProvider = TooltipPrimitive.Provider;

/**
 * Hover/focus hint for a control that ALREADY has an accessible name. Never put essential
 * information only in a tooltip (touch users cannot hover).
 */
export function Tooltip({
  content,
  children,
  side = 'top',
  align = 'center',
  delayDuration,
}: {
  content: React.ReactNode;
  children: React.ReactElement;
  side?: 'top' | 'right' | 'bottom' | 'left';
  align?: 'start' | 'center' | 'end';
  delayDuration?: number;
}) {
  return (
    <TooltipPrimitive.Root delayDuration={delayDuration}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          align={align}
          sideOffset={6}
          collisionPadding={8}
          className="ds-pop z-50 max-w-xs rounded-md border border-line-strong bg-overlay px-2 py-1 text-caption text-fg shadow-pop"
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

/* ---------------------------------------------------------------- Popover */

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;
export const PopoverClose = PopoverPrimitive.Close;

export const PopoverContent = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>>(function PopoverContent(
  { className, sideOffset = 6, collisionPadding = 8, align = 'start', ...rest },
  ref,
) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        align={align}
        className={cn(panel, 'w-72 max-w-[calc(100vw-16px)] p-3 text-sm', className)}
        {...rest}
      />
    </PopoverPrimitive.Portal>
  );
});

/* ---------------------------------------------------------------- DropdownMenu */

export const DropdownMenu = DropdownPrimitive.Root;
export const DropdownMenuTrigger = DropdownPrimitive.Trigger;
export const DropdownMenuGroup = DropdownPrimitive.Group;
export const DropdownMenuSub = DropdownPrimitive.Sub;
export const DropdownMenuRadioGroup = DropdownPrimitive.RadioGroup;

export const DropdownMenuContent = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof DropdownPrimitive.Content>>(
  function DropdownMenuContent({ className, sideOffset = 6, collisionPadding = 8, ...rest }, ref) {
    return (
      <DropdownPrimitive.Portal>
        <DropdownPrimitive.Content
          ref={ref}
          sideOffset={sideOffset}
          collisionPadding={collisionPadding}
          className={cn(panel, 'min-w-[12rem] max-w-[calc(100vw-16px)] p-1 text-sm', className)}
          {...rest}
        />
      </DropdownPrimitive.Portal>
    );
  },
);

const itemCls =
  'relative flex min-h-[2rem] cursor-default select-none items-center gap-2 rounded px-2 py-1.5 text-sm text-fg outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-raised data-[disabled]:opacity-50';

export const DropdownMenuItem = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof DropdownPrimitive.Item> & { inset?: boolean; destructive?: boolean }>(
  function DropdownMenuItem({ className, inset, destructive, ...rest }, ref) {
    return <DropdownPrimitive.Item ref={ref} className={cn(itemCls, inset && 'pl-8', destructive && 'text-bad', className)} {...rest} />;
  },
);

export const DropdownMenuCheckboxItem = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof DropdownPrimitive.CheckboxItem>>(
  function DropdownMenuCheckboxItem({ className, children, ...rest }, ref) {
    return (
      <DropdownPrimitive.CheckboxItem ref={ref} className={cn(itemCls, 'pl-8', className)} {...rest}>
        <span className="absolute left-2 inline-flex h-4 w-4 items-center justify-center">
          <DropdownPrimitive.ItemIndicator>
            <Check aria-hidden className="h-3.5 w-3.5" />
          </DropdownPrimitive.ItemIndicator>
        </span>
        {children}
      </DropdownPrimitive.CheckboxItem>
    );
  },
);

export const DropdownMenuRadioItem = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof DropdownPrimitive.RadioItem>>(
  function DropdownMenuRadioItem({ className, children, ...rest }, ref) {
    return (
      <DropdownPrimitive.RadioItem ref={ref} className={cn(itemCls, 'pl-8', className)} {...rest}>
        <span className="absolute left-2 inline-flex h-4 w-4 items-center justify-center">
          <DropdownPrimitive.ItemIndicator>
            <Check aria-hidden className="h-3.5 w-3.5" />
          </DropdownPrimitive.ItemIndicator>
        </span>
        {children}
      </DropdownPrimitive.RadioItem>
    );
  },
);

export function DropdownMenuLabel({ className, children }: { className?: string; children: React.ReactNode }) {
  return <DropdownPrimitive.Label className={cn('px-2 pb-1 pt-1.5 text-micro font-semibold uppercase tracking-wide text-subtle', className)}>{children}</DropdownPrimitive.Label>;
}

export function DropdownMenuSeparator({ className }: { className?: string }) {
  return <DropdownPrimitive.Separator className={cn('-mx-1 my-1 h-px bg-line', className)} />;
}

export const DropdownMenuSubTrigger = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof DropdownPrimitive.SubTrigger>>(
  function DropdownMenuSubTrigger({ className, children, ...rest }, ref) {
    return (
      <DropdownPrimitive.SubTrigger ref={ref} className={cn(itemCls, 'data-[state=open]:bg-raised', className)} {...rest}>
        {children}
        <ChevronRight aria-hidden className="ml-auto h-3.5 w-3.5 text-muted" />
      </DropdownPrimitive.SubTrigger>
    );
  },
);

export const DropdownMenuSubContent = forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof DropdownPrimitive.SubContent>>(
  function DropdownMenuSubContent({ className, ...rest }, ref) {
    return (
      <DropdownPrimitive.Portal>
        <DropdownPrimitive.SubContent ref={ref} collisionPadding={8} className={cn(panel, 'min-w-[10rem] p-1 text-sm', className)} {...rest} />
      </DropdownPrimitive.Portal>
    );
  },
);
