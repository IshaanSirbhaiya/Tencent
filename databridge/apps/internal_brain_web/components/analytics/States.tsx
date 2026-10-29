'use client';
import { CloudOff, Lock, RotateCw, SearchX } from 'lucide-react';
import { Button, Card, EmptyState } from '@/components/ui';
import { ApiError } from '@/lib/api';

export type LoadError = 'unavailable' | 'not_found' | 'forbidden';

export function toLoadError(e: unknown): LoadError {
  if (e instanceof ApiError && e.status === 404) return 'not_found';
  if (e instanceof ApiError && e.status === 403) return 'forbidden';
  return 'unavailable';
}

/** The one generic "try again" state for 503 / network / unexpected failures. */
export function Unavailable({ onRetry, what = 'This view' }: { onRetry?: () => void; what?: string }) {
  return (
    <EmptyState
      icon={CloudOff}
      title={`${what} is temporarily unavailable`}
      description="Nothing was shown that was not recorded first. Try again in a moment."
      action={
        onRetry ? (
          <Button size="sm" icon={RotateCw} onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
      className="bg-surface"
    />
  );
}

export function NotFound({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return <EmptyState icon={SearchX} title={title} description={description} action={action} className="bg-surface" />;
}

/** Org-wide numbers are compliance-only (D-05): the lock explains RBAC instead of a bare 403. */
export function OrgNumbersLock({ className }: { className?: string }) {
  return (
    <Card className={className} data-testid="org-numbers-lock">
      <div className="flex items-start gap-3 p-4">
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-perm-locked">
          <Lock aria-hidden className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h2 className="text-heading-sm text-fg">Organisation numbers</h2>
          <p className="mt-1 text-body-sm text-muted">Visible to the compliance role only: each person sees what their role needs.</p>
          <p className="mt-1 text-caption text-subtle">You still see the pipeline, your own requests and their receipts.</p>
        </div>
      </div>
    </Card>
  );
}
