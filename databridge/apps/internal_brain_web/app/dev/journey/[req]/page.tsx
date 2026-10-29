'use client';
import { Waypoints } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';
import { CodeHash } from '@/components/ui';

// Skeleton (Wave 0). Data: api.trace(req) for your own requests; api.journey(req) for compliance.
export default function JourneyPage({ params }: { params: { req: string } }) {
  const req = decodeURIComponent(params.req);
  return (
    <Placeholder icon={Waypoints} title="Request journey" description="Accept → Gate 1 → Gate 2 → seal → compose → release, with timings, the model, citations and the sealed ledger records.">
      <p className="mt-4 flex items-center gap-2 text-sm text-muted">
        Request <CodeHash value={req} label="request id" />
      </p>
    </Placeholder>
  );
}
