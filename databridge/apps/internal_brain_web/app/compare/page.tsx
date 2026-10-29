'use client';
import { Suspense } from 'react';
import { CompareView } from '@/components/compare/CompareView';
import { PresenterRoot } from '@/components/presenter/PresenterRoot';
import { AppShell } from '@/components/shell';

// /compare: same question for two people, or the same person with two questions.
// Deep links: ?a=amy.am&b=mlro.tan&q=…  or  ?mode=person&p=ken.ext&q1=…&q2=…
export default function ComparePage() {
  return (
    <>
      <AppShell container={false}>
        <Suspense fallback={null}>
          <CompareView />
        </Suspense>
      </AppShell>
      <PresenterRoot />
    </>
  );
}
