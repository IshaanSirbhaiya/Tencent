'use client';
import { Waypoints } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <Placeholder icon={Waypoints} title="Request journey" description="Pick one of your recent questions to see how it was answered: both gates, the model, grounding and the sealed ledger records." />
  );
}
