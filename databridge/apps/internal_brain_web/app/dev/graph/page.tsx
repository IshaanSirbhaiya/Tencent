'use client';
// /dev/graph: the knowledge graph (Developer mode). Explore = the signed-in person's permitted world
// as one WebGL graph; Compare = two separately signed-in people side by side (D-17). Sigma touches
// WebGL and window at import time, so the explorer is loaded client-only.
import { Network, Users } from 'lucide-react';
import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';
import { SegmentedControl } from '@/components/ui';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';

const GraphExplorer = dynamic(() => import('@/components/graph/GraphExplorer').then((m) => m.GraphExplorer), { ssr: false, loading: () => <Boot /> });
const CompareView = dynamic(() => import('@/components/graph/CompareView').then((m) => m.CompareView), { ssr: false, loading: () => <Boot /> });

function Boot() {
  return <div className="h-full bg-bg" aria-busy="true" />;
}

type Mode = 'explore' | 'compare';

export default function GraphPage() {
  const t = useMessages(graphMessages);
  const [mode, setMode] = useState<Mode | null>(null);
  const [comparePersona, setComparePersona] = useState<string | null>(null);

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const c = p.get('compare');
    setComparePersona(c);
    setMode(c !== null ? 'compare' : 'explore');
  }, []);

  const change = (m: Mode) => {
    setMode(m);
    const u = new URL(window.location.href);
    u.searchParams.delete('node');
    if (m === 'compare') u.searchParams.set('compare', comparePersona ?? 'ken.ext');
    else u.searchParams.delete('compare');
    window.history.replaceState(window.history.state, '', u.toString());
  };

  return (
    <div className="flex h-[calc(100dvh-var(--topbar-h)-var(--banner-h,0px))] min-h-[32rem] flex-col">
      <header className="flex flex-wrap items-end gap-x-6 gap-y-2 border-b border-line px-4 pb-3 pt-4 sm:px-6">
        <div className="min-w-0 flex-1">
          <p className="text-micro font-semibold uppercase tracking-wide text-subtle">{t('page.eyebrow')}</p>
          <h1 className="text-title text-fg">{t('page.title')}</h1>
          <p className="mt-0.5 hidden max-w-3xl text-body-sm text-muted sm:block">{t('page.desc')}</p>
        </div>
        {mode ? (
          <SegmentedControl
            label={t('mode.label')}
            value={mode}
            onChange={change}
            options={[
              { value: 'explore', label: t('mode.explore'), icon: Network },
              { value: 'compare', label: t('mode.compare'), icon: Users },
            ]}
            testId="graph-mode"
          />
        ) : null}
      </header>
      <div className="min-h-0 flex-1">
        {mode === 'explore' ? <GraphExplorer slot="a" urlState testId="graph-main" /> : null}
        {mode === 'compare' ? <CompareView initialPersona={comparePersona} /> : null}
      </div>
    </div>
  );
}
