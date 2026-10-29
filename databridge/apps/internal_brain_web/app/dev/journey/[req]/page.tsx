'use client';
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { NotFound, toLoadError, Unavailable, type LoadError } from '@/components/analytics/States';
import { ComplianceJourney } from '@/components/journey/ComplianceJourney';
import { Receipt } from '@/components/journey/Receipt';
import { Page } from '@/components/shell/Page';
import { useSession } from '@/components/shell/Providers';
import { Button, Card, Skeleton } from '@/components/ui';
import { analyticsApi } from '@/lib/api';
import type { JourneyV1, TraceReceipt } from '@/lib/types';

type Loaded = { lens: 'compliance'; j: JourneyV1 } | { lens: 'receipt'; r: TraceReceipt };

function JourneySkeleton() {
  return (
    <div role="status" className="space-y-4" data-testid="journey-skeleton">
      <span className="sr-only">Loading the request journey</span>
      <Card className="space-y-3 p-4">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-6 w-3/4" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-9" />
          ))}
        </div>
      </Card>
      <Card className="space-y-2 p-4">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-6" />
        ))}
      </Card>
      <div className="grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-36" />
        <Skeleton className="h-36" />
        <Skeleton className="h-36" />
      </div>
    </div>
  );
}

/**
 * One URL, two lenses picked by role: compliance gets the full journey (/audit/journey); everyone
 * else gets the receipt of their OWN request (/ib/trace). Someone else's id and an unknown id give
 * the same "not found".
 */
export default function JourneyPage({ params }: { params: { req: string } }) {
  const reqId = decodeURIComponent(params.req);
  const { me, hasRole } = useSession();
  const compliance = hasRole('compliance_officer');
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<LoadError | null>(null);

  const load = useCallback(() => {
    setError(null);
    setData(null);
    const p: Promise<Loaded> = compliance
      ? analyticsApi.journey(reqId).then((j) => ({ lens: 'compliance' as const, j }))
      : analyticsApi.receipt(reqId).then((r) => ({ lens: 'receipt' as const, r }));
    p.then(setData).catch((e) => setError(toLoadError(e)));
  }, [compliance, reqId]);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  return (
    <Page
      width="wide"
      eyebrow="Observe · Request journey"
      title="Request journey"
      description={
        compliance
          ? 'Every stage of this request as the ledger sealed it: timings, the Gate-2 verdicts with reason codes, the model and the hash-chained records.'
          : 'The receipt of your own question: how it was answered, the sources it cited and the sealed records that prove it.'
      }
      actions={
        <Button asChild size="sm" variant="ghost">
          <Link href="/dev/journey">
            <ArrowLeft aria-hidden className="h-3.5 w-3.5" /> All requests
          </Link>
        </Button>
      }
    >
      {error === 'not_found' ? (
        <NotFound
          title="Request not found"
          description={compliance ? 'No request with this id is in the ledger.' : 'There is no request with this id in your history.'}
          action={
            <Button asChild size="sm">
              <Link href="/dev/journey">See recent requests</Link>
            </Button>
          }
        />
      ) : error ? (
        <Unavailable what="The journey" onRetry={load} />
      ) : !data ? (
        <JourneySkeleton />
      ) : data.lens === 'compliance' ? (
        <ComplianceJourney j={data.j} />
      ) : (
        <Receipt r={data.r} />
      )}
    </Page>
  );
}
