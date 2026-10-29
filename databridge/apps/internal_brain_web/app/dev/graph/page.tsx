'use client';
import { Network } from 'lucide-react';
import { Placeholder } from '@/components/shell/Placeholder';

// Skeleton (Wave 0). The owning page agent replaces this file's body; keep the route.
export default function Page() {
  return (
    <Placeholder icon={Network} title="Knowledge graph" description="What you can open, grown one hop at a time from a search or an answer’s citations, with View as for a second person." />
  );
}
