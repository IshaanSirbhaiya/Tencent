'use client';
import { RequireRole } from '@/components/shell/RoleGate';
import { SimConsole } from '@/components/sim/SimConsole';

// Platform simulator (admin_it).
export default function Page() {
  return (
    <RequireRole role="admin_it">
      <SimConsole />
    </RequireRole>
  );
}
