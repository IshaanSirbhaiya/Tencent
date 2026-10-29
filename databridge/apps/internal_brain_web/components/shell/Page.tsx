import { SectionHeader } from '@/components/ui';
import { cn } from '@/lib/cn';

const WIDTH = {
  narrow: 'max-w-3xl',
  default: 'max-w-6xl',
  wide: 'max-w-[90rem]',
  full: 'max-w-none',
} as const;

/**
 * The standard page frame inside the shell: a container and an optional h1 header row.
 * Pages under a route layout (app/dev/*, app/(user)/*) render <Page> as their root.
 * `width="full"` with `bleed` drops the padding too (canvases such as the graph).
 */
export function Page({
  title,
  description,
  eyebrow,
  actions,
  width = 'default',
  bleed,
  className,
  children,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  eyebrow?: React.ReactNode;
  actions?: React.ReactNode;
  width?: keyof typeof WIDTH;
  bleed?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn('mx-auto w-full', WIDTH[width], bleed ? '' : 'px-4 py-6 sm:px-6 lg:px-8', className)}>
      {title ? <SectionHeader as="h1" title={title} description={description} eyebrow={eyebrow} actions={actions} className="mb-6" /> : null}
      {children}
    </div>
  );
}
