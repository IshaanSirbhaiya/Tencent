'use client';
import { Activity } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';
import { RequireRole } from '@/components/shell/RoleGate';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <RequireRole role="admin_it">
      <Placeholder icon={Activity} title="Platform simulator" description="Change access the way an administrator would and watch the next answer follow." />
    </RequireRole>
  );
}
