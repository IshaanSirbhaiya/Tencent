'use client';
// Dialog (centred) and Sheet (right or left side panel) on Radix Dialog: focus is trapped inside,
// Esc closes, focus returns to the trigger, and the page behind is inert. Title is required.
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { forwardRef } from 'react';
import { cn } from '@/lib/cn';

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const Sheet = DialogPrimitive.Root;
export const SheetTrigger = DialogPrimitive.Trigger;
export const SheetClose = DialogPrimitive.Close;

function Scrim() {
  return <DialogPrimitive.Overlay className="ds-scrim fixed inset-0 z-40 bg-black/50" />;
}

function CloseX({ label }: { label: string }) {
  return (
    <DialogPrimitive.Close
      aria-label={label}
      title={label}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted hover:bg-raised hover:text-fg"
    >
      <X aria-hidden className="h-4 w-4" />
    </DialogPrimitive.Close>
  );
}

interface PanelProps extends Omit<React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>, 'title'> {
  /** Visible heading; also the accessible name. */
  title: React.ReactNode;
  /** Optional one-line description under the title (aria-describedby). */
  description?: React.ReactNode;
  /** Actions shown in the header, left of the close button. */
  headerActions?: React.ReactNode;
  /** Footer row (e.g. Cancel / Confirm). */
  footer?: React.ReactNode;
  closeLabel?: string;
  /** Classes for the scrolling body (default padding). */
  bodyClassName?: string;
}

export const DialogContent = forwardRef<HTMLDivElement, PanelProps & { size?: 'sm' | 'md' | 'lg' }>(function DialogContent(
  { title, description, headerActions, footer, closeLabel = 'Close', size = 'md', className, bodyClassName, children, ...rest },
  ref,
) {
  return (
    <DialogPrimitive.Portal>
      <Scrim />
      <DialogPrimitive.Content
        ref={ref}
        className={cn(
          'ds-pop fixed left-1/2 top-1/2 z-50 flex max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-line bg-overlay text-fg shadow-pop outline-none',
          size === 'sm' && 'max-w-sm',
          size === 'md' && 'max-w-lg',
          size === 'lg' && 'max-w-3xl',
          className,
        )}
        {...(description ? {} : { 'aria-describedby': undefined })}
        {...rest}
      >
        <div className="flex items-start gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1">
            <DialogPrimitive.Title className="text-heading text-fg">{title}</DialogPrimitive.Title>
            {description ? <DialogPrimitive.Description className="mt-1 text-sm text-muted">{description}</DialogPrimitive.Description> : null}
          </div>
          {headerActions}
          <CloseX label={closeLabel} />
        </div>
        <div className={cn('ds-scroll min-h-0 flex-1 overflow-y-auto px-5 py-4', bodyClassName)}>{children}</div>
        {footer ? <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div> : null}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
});

/** Side panel. Right by default (details, sources, node panels); full width on phones. */
export const SheetContent = forwardRef<HTMLDivElement, PanelProps & { side?: 'right' | 'left'; width?: 'sm' | 'md' | 'lg' }>(function SheetContent(
  { title, description, headerActions, footer, closeLabel = 'Close', side = 'right', width = 'md', className, bodyClassName, children, ...rest },
  ref,
) {
  return (
    <DialogPrimitive.Portal>
      <Scrim />
      <DialogPrimitive.Content
        ref={ref}
        className={cn(
          'fixed inset-y-0 z-50 flex w-full flex-col border-line bg-overlay text-fg shadow-pop outline-none',
          side === 'right' ? 'ds-sheet right-0 border-l' : 'ds-sheet-left left-0 border-r',
          width === 'sm' && 'sm:max-w-sm',
          width === 'md' && 'sm:max-w-md',
          width === 'lg' && 'sm:max-w-2xl',
          className,
        )}
        {...(description ? {} : { 'aria-describedby': undefined })}
        {...rest}
      >
        <div className="flex items-start gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <DialogPrimitive.Title className="text-heading-sm text-fg">{title}</DialogPrimitive.Title>
            {description ? <DialogPrimitive.Description className="mt-0.5 text-caption text-muted">{description}</DialogPrimitive.Description> : null}
          </div>
          {headerActions}
          <CloseX label={closeLabel} />
        </div>
        <div className={cn('ds-scroll min-h-0 flex-1 overflow-y-auto px-4 py-4', bodyClassName)}>{children}</div>
        {footer ? <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">{footer}</div> : null}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
});
