'use client';
import { ScrollText } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';
import { RequireRole } from '@/components/shell/RoleGate';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <RequireRole role="compliance_officer">
      <Placeholder icon={ScrollText} title="Audit console" description="Plain-English audit search, reveal with a reason, and Verify chain." />
    </RequireRole>
  );
}
