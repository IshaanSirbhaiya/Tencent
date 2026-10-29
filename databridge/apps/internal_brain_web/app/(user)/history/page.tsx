'use client';
import { History } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <Placeholder icon={History} title="History" description="Your questions, grouped by day. Open one to see its answer journey." />
  );
}
