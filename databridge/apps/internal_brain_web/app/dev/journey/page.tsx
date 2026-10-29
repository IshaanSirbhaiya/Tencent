'use client';
import { ArrowRight, MessageSquare } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { SourceChip } from '@/components/analytics/ChartCard';
import { toLoadError, Unavailable, type LoadError } from '@/components/analytics/States';
import { RecentRequests, type RecentRow } from '@/components/journey/RecentRequests';
import { Page } from '@/components/shell/Page';
import { useSession } from '@/components/shell/Providers';
import { Button, Card, EmptyState, Input, SkeletonText } from '@/components/ui';
import { analyticsApi } from '@/lib/api';

/** /dev/journey: recent requests (everyone's for compliance, your own otherwise) and open-by-id. */
export default function JourneyIndex() {
  const { me, hasRole } = useSession();
  const compliance = hasRole('compliance_officer');
  const router = useRouter();
  const [rows, setRows] = useState<RecentRow[] | null>(null);
  const [error, setError] = useState<LoadError | null>(null);
  const [id, setId] = useState('');

  const load = useCallback(() => {
    setError(null);
    setRows(null);
    const p: Promise<RecentRow[]> = compliance
      ? analyticsApi.recentRequests(100)
      : analyticsApi.history().then((h) => (h.items ?? []).map((x) => ({ ...x })));
    p.then(setRows).catch((e) => setError(toLoadError(e)));
  }, [compliance]);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  const open = (e: React.FormEvent) => {
    e.preventDefault();
    const v = id.trim();
    if (v) router.push(`/dev/journey/${encodeURIComponent(v)}`);
  };

  return (
    <Page
      width="wide"
      eyebrow="Observe"
      title="Request journey"
      description={
        compliance
          ? 'Any request in the ledger, end to end: both gates with reason codes, the model, grounding and the sealed records.'
          : 'Your own questions, end to end: how each was answered, its sources and the sealed records.'
      }
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <section aria-labelledby="recent-h" className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 id="recent-h" className="text-heading-sm text-fg">
              {compliance ? 'Recent requests' : 'Your recent requests'}
            </h2>
            <SourceChip>{compliance ? 'ledger · request.accepted' : '/ib/history'}</SourceChip>
          </div>
          {error ? (
            <Unavailable what="The request list" onRetry={load} />
          ) : !rows ? (
            <Card className="p-4">
              <SkeletonText lines={8} label="Loading recent requests" />
            </Card>
          ) : rows.length === 0 && !compliance ? (
            <EmptyState
              icon={MessageSquare}
              title="No questions yet"
              description="Ask something in User mode; every answer gets a receipt you can open here."
              action={
                <Button asChild size="sm">
                  <Link href="/?mode=user">Ask a question</Link>
                </Button>
              }
              className="bg-surface"
            />
          ) : (
            <RecentRequests rows={rows} showAsker={compliance} caption={compliance ? 'Recent requests in the ledger' : 'Your recent requests'} maxHeightClass="max-h-[70vh]" />
          )}
        </section>
        <aside className="space-y-4">
          <Card className="p-4">
            <form onSubmit={open} className="space-y-2" aria-labelledby="open-h">
              <h2 id="open-h" className="text-heading-sm text-fg">
                Open by request id
              </h2>
              <label htmlFor="req-id" className="sr-only">
                Request id
              </label>
              <Input id="req-id" value={id} onChange={(e) => setId(e.target.value)} placeholder="req_…" className="font-mono text-caption" autoComplete="off" spellCheck={false} />
              <Button type="submit" size="sm" iconRight={ArrowRight} disabled={!id.trim()} className="w-full">
                Open journey
              </Button>
            </form>
          </Card>
          <Card className="p-4 text-caption text-muted">
            <h2 className="mb-1 text-heading-sm text-fg">What each role sees</h2>
            <p>
              <span className="font-medium text-fg">Compliance</span> sees any request: the timed waterfall, every considered document with its Gate-2 verdict and reason code, and global ledger
              sequence numbers.
            </p>
            <p className="mt-2">
              <span className="font-medium text-fg">Everyone else</span> sees a receipt of their own requests only: the steps, total time, the model, the sources they can still open and the
              record hashes.
            </p>
          </Card>
        </aside>
      </div>
    </Page>
  );
}
