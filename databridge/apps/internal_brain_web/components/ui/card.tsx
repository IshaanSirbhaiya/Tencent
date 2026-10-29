import { cn } from '@/lib/cn';

type CardProps = React.HTMLAttributes<HTMLElement> & { as?: 'section' | 'div' | 'aside' | 'article' | 'li'; interactive?: boolean };

/** Bordered surface. Borders + spacing structure the page; no drop shadows. Default element: <section>. */
export function Card({ as: Tag = 'section', interactive, className, children, ...rest }: CardProps) {
  return (
    <Tag
      className={cn(
        'rounded-lg border border-line bg-surface',
        interactive && 'transition-colors duration-fast hover:border-line-strong hover:bg-raised/40',
        className,
      )}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function CardHeader({ className, children, actions }: { className?: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className={cn('flex items-start justify-between gap-3 border-b border-line px-4 py-3', className)}>
      <div className="min-w-0 flex-1">{children}</div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function CardTitle({ as: Tag = 'h2', className, children, id }: { as?: 'h2' | 'h3' | 'h4' | 'p'; className?: string; children: React.ReactNode; id?: string }) {
  return (
    <Tag id={id} className={cn('text-heading-sm text-fg', className)}>
      {children}
    </Tag>
  );
}

export function CardDescription({ className, children }: { className?: string; children: React.ReactNode }) {
  return <p className={cn('mt-0.5 text-caption text-muted', className)}>{children}</p>;
}

export function CardBody({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn('px-4 py-3', className)}>{children}</div>;
}

export function CardFooter({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn('flex items-center gap-2 border-t border-line px-4 py-2.5 text-caption text-muted', className)}>{children}</div>;
}
