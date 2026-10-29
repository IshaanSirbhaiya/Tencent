'use client';
import { LayoutDashboard } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <Placeholder icon={LayoutDashboard} title="Mission control" description="KPI tiles, the pipeline with live per-stage p50, source freshness, the ledger head and model status." />
  );
}
