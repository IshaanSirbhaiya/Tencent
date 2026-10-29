'use client';
import { Suspense } from 'react';
import { AuditConsole } from '@/components/audit/AuditConsole';
import { RequireRole } from '@/components/shell/RoleGate';

// Audit console (compliance_officer). ?verify=1 runs Verify; ?tamper=1 runs the sandbox tamper demo.
export default function Page() {
  return (
    <RequireRole role="compliance_officer">
      <Suspense fallback={null}>
        <AuditConsole />
      </Suspense>
    </RequireRole>
  );
}
