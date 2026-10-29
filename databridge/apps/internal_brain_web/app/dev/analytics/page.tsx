'use client';
import { BarChart3 } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';
import { RequireRole } from '@/components/shell/RoleGate';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <RequireRole role="compliance_officer">
      <Placeholder icon={BarChart3} title="Analytics" description="Requests, the asked → answered funnel, Gate-2 verdicts, latency per stage, model mix and channels, from /audit/stats." />
    </RequireRole>
  );
}
