'use client';
import { Suspense } from 'react';
import { SkeletonText } from '@/components/ui';
import { EngineView } from '@/components/engine/EngineView';

// The Walnut engine (D-09, D-21): a read-only view of the evidence engine's snapshot of Company A.
export default function Page() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-6xl px-4 py-6"><SkeletonText lines={4} label="Loading" /></div>}>
      <EngineView />
    </Suspense>
  );
}
