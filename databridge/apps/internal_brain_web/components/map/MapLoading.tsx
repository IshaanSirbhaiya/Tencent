import { clsx } from 'clsx';
import { ShieldCheck } from 'lucide-react';
import { InfoTip } from '../InfoTip';

/**
 * Shown while GET /ib/map runs. The IB confirms every item live with its source before listing
 * it, which takes a few seconds for a large map; nothing partial is ever drawn meanwhile.
 */
export function MapLoading({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <div className={clsx('rounded-lg border border-line bg-surface', compact ? 'px-4 py-3' : 'px-6 py-8', className)} data-testid="map-loading">
      <div role="status" className="flex items-start gap-3">
        <ShieldCheck aria-hidden className={clsx('shrink-0 text-muted', compact ? 'mt-0.5 h-4 w-4' : 'mt-0.5 h-5 w-5')} />
        <div className="min-w-0 flex-1">
          <p className={clsx('flex items-center gap-1 font-medium text-fg', compact ? 'text-xs' : 'text-sm')}>
            Checking each item with its source…
            <InfoTip title="Why this takes a moment">
              We ask Slack, Jira, Confluence and Drive again, live, so nothing you&apos;ve lost access to appears.
            </InfoTip>
          </p>
          <p className="mt-0.5 text-2xs text-muted">A large map can take around ten seconds.</p>
          <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-raised" aria-hidden>
            <div className="ib-indeterminate h-1 w-1/3 rounded-full bg-accent/70" />
          </div>
        </div>
      </div>
    </div>
  );
}
