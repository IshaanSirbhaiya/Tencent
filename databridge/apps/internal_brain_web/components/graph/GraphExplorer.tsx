'use client';
// One explorer = one signed-in session's graph: load (overview + map), deterministic layout, canvas,
// search-to-focus, one-hop growth, working set, legend filters, node panel and table view.
// Used once for Explore and twice (main session + compare slot b) for Compare. Every response is
// checked against the session it is meant to belong to; a mismatch shows the error state instead.
import { Check, Circle, Loader2, Network, RefreshCw, Table2, Undo2, Workflow } from 'lucide-react';
import Link from 'next/link';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { Button, EmptyState, SegmentedControl } from '@/components/ui';
import { ApiError, graphApi } from '@/lib/api';
import { cn } from '@/lib/cn';
import { fmtTime } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import type { KnowledgeGraph, KnowledgeMap, Platform } from '@/lib/types';
import { Legend, MapControls, useReducedMotion } from './bits';
import { fmtCount } from './format';
import { type CanvasHandle, type CanvasView, GraphCanvas } from './GraphCanvas';
import { GraphTable } from './GraphTable';
import { applyLayout } from './layout';
import { buildGalaxy, type KGraph, mergeResponse } from './model';
import { NodePanel } from './NodePanel';
import { type SearchHandle, type SearchHit, SearchBox } from './SearchBox';

export const WORKING_SET_CAP = 400;
const OVERVIEW_LIMIT = 300;
const FOCUS_LIMIT = 120;

export interface LoadedInfo {
  sub: string;
  totals: KnowledgeMap['totals'];
  docIds: string[];
  asOf: string;
}

export interface ExplorerHandle {
  docIds: () => string[];
}

type Phase =
  | { kind: 'loading'; places: boolean; docs: boolean }
  | { kind: 'ready' }
  | { kind: 'empty' }
  | { kind: 'error'; status: number; mismatch?: boolean };

interface Props {
  slot: 'a' | 'b';
  /** When set, every response must be for this sub (the compare slot's freshly signed-in person). */
  expectSub?: string;
  /** Replaces the default load (the compare view signs the slot in under its lock first). */
  preload?: () => Promise<{ overview: KnowledgeGraph; map: KnowledgeMap }>;
  compact?: boolean;
  shared?: ReadonlySet<string> | null;
  onLoaded?: (info: LoadedInfo) => void;
  /** Sync ?node= with the URL (the main Explore view only). */
  urlState?: boolean;
  name?: string;
  testId: string;
}

class Mismatch extends Error {}

export const GraphExplorer = forwardRef<ExplorerHandle, Props>(function GraphExplorer(
  { slot, expectSub, preload, compact, shared = null, onLoaded, urlState, name, testId },
  ref,
) {
  const t = useMessages(graphMessages);
  const src = useMemo(() => graphApi(slot), [slot]);
  const reducedMotion = useReducedMotion();
  const [phase, setPhase] = useState<Phase>({ kind: 'loading', places: false, docs: false });
  const [graph, setGraph] = useState<KGraph | null>(null);
  const [map, setMap] = useState<KnowledgeMap | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [display, setDisplay] = useState<'graph' | 'table'>('graph');
  const [webglLost, setWebglLost] = useState(false);
  const [hiddenPlatforms, setHiddenPlatforms] = useState<Set<string>>(new Set());
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());
  const [scope, setScope] = useState<Set<string> | null>(null);
  const [capped, setCapped] = useState(false);
  const [expanding, setExpanding] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState<string | null>(null);
  const [announce, setAnnounce] = useState('');
  const [attempt, setAttempt] = useState(0);
  const canvas = useRef<CanvasHandle>(null);
  const search = useRef<SearchHandle>(null);
  const appRef = useRef<HTMLDivElement>(null);
  const panelHeading = useRef<HTMLHeadingElement>(null);
  const graphRef = useRef<KGraph | null>(null);
  const pendingFly = useRef<string | null>(null);
  const subRef = useRef<string | null>(null);
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  useImperativeHandle(ref, () => ({ docIds: () => (graphRef.current ? graphRef.current.filterNodes((_id, a) => a.kind === 'doc') : []) }), []);

  // ---------------------------------------------------------------- load
  useEffect(() => {
    let live = true;
    setPhase({ kind: 'loading', places: false, docs: false });
    setGraph(null);
    setSelected(null);
    setScope(null);
    (async () => {
      try {
        let overview: KnowledgeGraph;
        let m: KnowledgeMap;
        if (preload) {
          ({ overview, map: m } = await preload());
          if (live) setPhase({ kind: 'loading', places: true, docs: true });
        } else {
          [overview, m] = await Promise.all([
            src.graph({ limit: OVERVIEW_LIMIT }).then((r) => {
              if (live) setPhase((p) => (p.kind === 'loading' ? { ...p, places: true } : p));
              return r;
            }),
            src.map().then((r) => {
              if (live) setPhase((p) => (p.kind === 'loading' ? { ...p, docs: true } : p));
              return r;
            }),
          ]);
        }
        if (overview.sub !== m.sub || (expectSub && overview.sub !== expectSub)) throw new Mismatch();
        const g = buildGalaxy(overview, m);
        if (g.order === 0) {
          if (live) setPhase({ kind: 'empty' });
          return;
        }
        await applyLayout(g);
        if (!live) return;
        subRef.current = overview.sub;
        graphRef.current = g;
        setGraph(g);
        setMap(m);
        setAsOf(overview.as_of);
        setPhase({ kind: 'ready' });
        onLoadedRef.current?.({ sub: overview.sub, totals: m.totals, docIds: g.filterNodes((_id, a) => a.kind === 'doc'), asOf: overview.as_of });
        if (urlState) {
          const node = new URLSearchParams(window.location.search).get('node');
          if (node && g.hasNode(node)) {
            setSelected(node);
            pendingFly.current = node;
          }
        }
      } catch (e) {
        if (!live) return;
        setPhase({ kind: 'error', status: e instanceof ApiError ? e.status : 0, mismatch: e instanceof Mismatch });
      }
    })();
    return () => {
      live = false;
    };
  }, [src, preload, expectSub, attempt, urlState]);

  // ---------------------------------------------------------------- url state
  useEffect(() => {
    if (!urlState || phase.kind !== 'ready') return;
    const u = new URL(window.location.href);
    if (selected) u.searchParams.set('node', selected);
    else u.searchParams.delete('node');
    window.history.replaceState(window.history.state, '', u.toString());
  }, [selected, urlState, phase.kind]);

  const view: CanvasView = useMemo(() => ({ selected, hiddenPlatforms, hiddenKinds, scope, shared }), [selected, hiddenPlatforms, hiddenKinds, scope, shared]);

  // ---------------------------------------------------------------- actions
  const flyLater = useCallback((id: string) => {
    // after a merge sigma indexes the new nodes on its next frame; fly once their display data exists
    requestAnimationFrame(() => requestAnimationFrame(() => canvas.current?.flyTo(id)));
  }, []);

  const select = useCallback(
    (id: string | null, opts?: { fly?: boolean; focusPanel?: boolean }) => {
      setSelected(id);
      setNotice(null);
      if (id && opts?.fly) flyLater(id);
      if (id && opts?.focusPanel) requestAnimationFrame(() => panelHeading.current?.focus());
      if (id && graphRef.current?.hasNode(id)) {
        const a = graphRef.current.getNodeAttributes(id);
        setAnnounce(`${a.label} selected. ${graphRef.current.degree(id)} linked nodes highlighted.`);
      }
    },
    [flyLater],
  );

  const checkSub = (r: { sub: string }) => {
    if (subRef.current && r.sub !== subRef.current) throw new Mismatch();
  };

  const addToScope = useCallback((ids: string[], base?: Set<string> | null) => {
    setScope((cur) => {
      const s = new Set(base ?? cur ?? []);
      let hitCap = false;
      for (const id of ids) {
        if (s.has(id)) continue;
        if (s.size >= WORKING_SET_CAP) {
          hitCap = true;
          break;
        }
        s.add(id);
      }
      setCapped(hitCap);
      return s;
    });
  }, []);

  const expand = useCallback(
    async (id: string, limit = FOCUS_LIMIT) => {
      const g = graphRef.current;
      if (!g) return;
      setExpanding(id);
      setNotice(null);
      try {
        const r = await src.graph({ focus: id, limit });
        checkSub(r);
        const { ids, added } = mergeResponse(g, r, id, performance.now());
        if (scope) addToScope(ids);
        setVersion((v) => v + 1);
        // bring the grown neighbourhood into view
        const ego = [id, ...g.neighbors(id)];
        requestAnimationFrame(() => requestAnimationFrame(() => canvas.current?.fit(ego)));
        setAnnounce(added.length ? `${added.length} nodes added around ${g.getNodeAttribute(id, 'label')}.` : `Expanded ${g.getNodeAttribute(id, 'label')}; everything linked was already on the canvas.`);
      } catch (e) {
        if (e instanceof Mismatch) setPhase({ kind: 'error', status: 0, mismatch: true });
        else if (e instanceof ApiError && e.status === 404) setNotice(t('panel.unavailable'));
        else setNotice(t('panel.failed'));
      } finally {
        setExpanding(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [src, scope, addToScope, t],
  );

  const startWorkingSet = useCallback(
    (id: string) => {
      const g = graphRef.current;
      if (!g) return;
      const base = new Set<string>([id]);
      g.forEachNeighbor(id, (n) => base.add(n));
      setScope(base);
      setCapped(false);
      setSelected(id);
      void (async () => {
        await expand(id);
        requestAnimationFrame(() => requestAnimationFrame(() => canvas.current?.fit([...(base ?? [])])));
      })();
    },
    [expand],
  );

  const collapseOthers = () => {
    const g = graphRef.current;
    if (!g || !selected) return;
    const base = new Set<string>([selected]);
    g.forEachNeighbor(selected, (n) => base.add(n));
    setScope(base);
    setCapped(false);
    requestAnimationFrame(() => canvas.current?.fit([...base]));
  };

  const serverSearch = useCallback(
    async (q: string): Promise<SearchHit[]> => {
      const g = graphRef.current;
      if (!g) return [];
      const r = await src.graph({ q, limit: 80 });
      checkSub(r);
      mergeResponse(g, r, null, performance.now());
      if (scope) addToScope(r.nodes.map((n) => n.id));
      setVersion((v) => v + 1);
      return r.nodes
        .filter((n) => typeof n.rank === 'number')
        .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
        .map((n) => ({ id: n.id, rank: n.rank ?? null }));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [src, scope, addToScope],
  );

  const pick = useCallback(
    (id: string, q: string) => {
      setQuery(q || null);
      if (scope && !scope.has(id)) addToScope([id]);
      const a = graphRef.current?.getNodeAttributes(id);
      if (a && ((a.platform && hiddenPlatforms.has(a.platform)) || hiddenKinds.has(a.kind))) {
        setHiddenPlatforms((s) => {
          const n = new Set(s);
          if (a.platform) n.delete(a.platform);
          return n;
        });
        setHiddenKinds((s) => {
          const n = new Set(s);
          n.delete(a.kind);
          return n;
        });
      }
      select(id, { fly: display === 'graph' });
    },
    [scope, addToScope, select, display, hiddenPlatforms, hiddenKinds],
  );

  // fly to a node chosen from the URL once the canvas is up
  const onCanvasReady = useCallback(() => {
    const id = pendingFly.current;
    pendingFly.current = null;
    if (id) flyLater(id);
  }, [flyLater]);

  const toggle = (set: Set<string>, v: string) => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v);
    else n.add(v);
    return n;
  };

  // ---------------------------------------------------------------- keyboard on the canvas
  const onKey = (e: React.KeyboardEvent) => {
    const c = canvas.current;
    if (!c) return;
    const k = e.key;
    if (k === 'ArrowUp' || k === 'ArrowDown' || k === 'ArrowLeft' || k === 'ArrowRight') {
      e.preventDefault();
      c.pan(k === 'ArrowLeft' ? -1 : k === 'ArrowRight' ? 1 : 0, k === 'ArrowUp' ? 1 : k === 'ArrowDown' ? -1 : 0);
    } else if (k === '+' || k === '=') {
      e.preventDefault();
      c.zoom(1);
    } else if (k === '-' || k === '_') {
      e.preventDefault();
      c.zoom(-1);
    } else if (k === '0') {
      e.preventDefault();
      c.fit(scope ?? undefined);
    } else if (k === 'Escape' && selected) {
      e.preventDefault();
      select(null);
    } else if (k === 'Enter' && selected) {
      e.preventDefault();
      panelHeading.current?.focus();
    }
  };

  const onRootKey = (e: React.KeyboardEvent) => {
    if (e.key === '/' && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) {
      e.preventDefault();
      search.current?.focus();
    }
  };

  // ---------------------------------------------------------------- render
  const platforms = useMemo(() => (map ? map.platforms.map((p) => ({ platform: p.platform as Platform, count: p.count })) : []), [map]);
  const summary = map ? t('stats.items', { items: fmtCount(map.totals.items), places: fmtCount(map.totals.containers), platforms: fmtCount(map.totals.platforms) }) : '';
  const ariaLabel = `${t('page.title')}${name ? `: ${name}` : ''}. ${summary}. ${t('ctl.keys')}`;
  const showTable = display === 'table' || webglLost;

  if (phase.kind === 'loading') return <LoadingState compact={compact} phase={phase} testId={testId} />;
  if (phase.kind === 'error') {
    if (phase.status === 401)
      return (
        <StateFrame testId={testId}>
          <EmptyState icon={Network} title={t('error.session')} action={<Button asChild variant="primary" size="sm"><Link href="/login">{t('error.signin')}</Link></Button>} />
        </StateFrame>
      );
    return (
      <StateFrame testId={testId}>
        <EmptyState
          icon={Network}
          title={phase.mismatch ? t('compare.mismatch') : t('error.title')}
          description={phase.mismatch ? undefined : t('error.desc')}
          action={
            <Button variant="secondary" size="sm" icon={RefreshCw} onClick={() => setAttempt((n) => n + 1)} data-testid="graph-retry">
              {t('error.retry')}
            </Button>
          }
        />
      </StateFrame>
    );
  }
  if (phase.kind === 'empty' || !graph)
    return (
      <StateFrame testId={testId}>
        <EmptyState icon={Network} title={t('empty.title')} description={t('empty.desc')} />
      </StateFrame>
    );

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid={testId} data-sub={subRef.current ?? ''} onKeyDown={onRootKey}>
      {/* toolbar */}
      <div className={cn('flex flex-wrap items-center gap-2 border-b border-line bg-bg px-3 py-2', compact ? '' : 'sm:px-4')}>
        <SearchBox ref={search} graph={graph} version={version} onPick={pick} onServerSearch={serverSearch} compact={compact} testId={`${testId}-search`} />
        {scope ? (
          <div className="flex items-center gap-1.5" data-testid="graph-scope">
            <span className="inline-flex h-7 items-center gap-1.5 rounded-md border border-accent/40 bg-accent/10 px-2 text-caption font-medium text-fg">
              <Workflow aria-hidden className="h-3.5 w-3.5 text-accent" />
              <span className="tabular-nums">{t('scope.working', { n: fmtCount(scope.size) })}</span>
            </span>
            {selected ? (
              <Button size="xs" variant="ghost" onClick={collapseOthers}>
                {t('scope.collapse')}
              </Button>
            ) : null}
            <Button
              size="xs"
              variant="ghost"
              icon={Undo2}
              onClick={() => {
                setScope(null);
                setCapped(false);
                requestAnimationFrame(() => canvas.current?.fit());
              }}
            >
              {t('scope.exit')}
            </Button>
          </div>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          {compact ? null : (
            <p className="hidden text-caption text-muted xl:block" title={t('stats.source')}>
              <span className="tabular-nums">{summary}</span>
            </p>
          )}
          <SegmentedControl
            label={t('view.label')}
            size="sm"
            value={showTable ? 'table' : 'graph'}
            onChange={(v) => setDisplay(v as 'graph' | 'table')}
            options={[
              { value: 'graph', label: t('view.graph'), icon: Network },
              { value: 'table', label: t('view.table'), icon: Table2 },
            ]}
            testId={`${testId}-display`}
          />
        </div>
      </div>

      {capped ? (
        <p role="status" className="border-b border-line bg-warn/10 px-4 py-1.5 text-caption text-fg">
          {t('scope.cap', { n: WORKING_SET_CAP })}
        </p>
      ) : null}
      {webglLost ? (
        <p role="status" className="border-b border-line bg-raised px-4 py-1.5 text-caption text-fg">
          {t('error.webgl')}
        </p>
      ) : null}

      <div className="relative flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1 bg-bg">
          {/* canvas stays mounted under the table so the camera is kept */}
          <div
            ref={appRef}
            role="application"
            aria-roledescription="interactive graph"
            aria-label={ariaLabel}
            tabIndex={0}
            onKeyDown={onKey}
            className={cn('absolute inset-0 focus-visible:outline-offset-[-2px]', showTable && 'invisible')}
            data-testid={`${testId}-canvas`}
          >
            {!webglLost ? (
              <GraphCanvas
                ref={canvas}
                graph={graph}
                view={view}
                reducedMotion={reducedMotion}
                onSelect={(id) => select(id)}
                onContextLost={() => setWebglLost(true)}
                onReady={onCanvasReady}
                label={ariaLabel}
              />
            ) : null}
          </div>

          {showTable ? (
            <div className="absolute inset-0 overflow-hidden p-2 sm:p-3">
              <GraphTable graph={graph} version={version} view={view} selected={selected} onOpen={(id) => select(id, { focusPanel: true })} testId={`${testId}-table`} />
            </div>
          ) : (
            <>
              <div className="pointer-events-none absolute right-3 top-3 z-10 flex flex-col items-end gap-2">
                <Legend
                  platforms={platforms}
                  hiddenPlatforms={hiddenPlatforms}
                  hiddenKinds={hiddenKinds}
                  onTogglePlatform={(p) => setHiddenPlatforms((s) => toggle(s, p))}
                  onToggleKind={(k) => setHiddenKinds((s) => toggle(s, k))}
                  totals={{ containers: map?.totals.containers ?? null, items: map?.totals.items ?? null }}
                  compact={compact}
                />
              </div>
              <div className="pointer-events-none absolute bottom-3 right-3 z-10">
                <MapControls compact={compact} onZoom={(d) => canvas.current?.zoom(d)} onFit={() => canvas.current?.fit(scope ?? undefined)} onPan={(dx, dy) => canvas.current?.pan(dx, dy)} />
              </div>
              <div className="pointer-events-none absolute bottom-3 left-3 z-10 max-w-[70%] space-y-0.5 rounded bg-bg/80 px-1.5 py-1 text-caption">
                {compact ? <p className="tabular-nums text-fg">{summary}</p> : null}
                <p className="text-subtle">
                  {asOf ? <span className="tabular-nums">As of {fmtTime(asOf)}</span> : null}
                  {compact ? null : <span className="hidden sm:inline"> · {t('stats.source')}</span>}
                </p>
                {compact ? null : <p className="hidden text-subtle md:block">{t('ctl.keys')}</p>}
              </div>
            </>
          )}
        </div>

        {selected && graph.hasNode(selected) ? (
          <NodePanel
            ref={panelHeading}
            id={selected}
            graph={graph}
            version={version}
            docEnabled={slot === 'a'}
            query={query}
            expanding={expanding === selected}
            onExpand={(id, limit) => void expand(id, limit)}
            onWorkingSet={startWorkingSet}
            onSelect={(id) => select(id, { fly: !showTable })}
            onClose={() => {
              select(null);
              appRef.current?.focus();
            }}
            notice={notice}
            className={cn('absolute inset-0 z-20 md:static md:z-auto', compact ? 'md:w-[19rem]' : 'md:w-[23rem] xl:w-[25rem]')}
            testId={`${testId}-panel`}
          />
        ) : null}
      </div>
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>
    </div>
  );
});

function StateFrame({ children, testId }: { children: React.ReactNode; testId: string }) {
  return (
    <div className="flex h-full min-h-[18rem] items-center justify-center bg-bg p-6" data-testid={testId}>
      <div className="w-full max-w-lg">{children}</div>
    </div>
  );
}

function LoadingState({ phase, compact, testId }: { phase: { places: boolean; docs: boolean }; compact?: boolean; testId: string }) {
  const t = useMessages(graphMessages);
  const steps = [
    { label: t('loading.places'), done: phase.places },
    { label: t('loading.docs'), done: phase.docs },
    { label: t('loading.layout'), done: false },
  ];
  const current = steps.findIndex((s) => !s.done);
  return (
    <div className="relative flex h-full min-h-[18rem] items-center justify-center overflow-hidden bg-bg p-6" data-testid={`${testId}-loading`}>
      {/* a quiet static ring diagram: decorative, not data */}
      <svg aria-hidden className="pointer-events-none absolute inset-0 h-full w-full text-line" preserveAspectRatio="xMidYMid slice" viewBox="0 0 800 500">
        {[70, 130, 200, 280].map((r) => (
          <circle key={r} cx="400" cy="250" r={r} fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="2 6" />
        ))}
      </svg>
      <div role="status" aria-live="polite" className={cn('relative w-full rounded-lg border border-line bg-surface p-4', compact ? 'max-w-xs' : 'max-w-sm')}>
        <p className="flex items-center gap-2 text-body-sm font-medium text-fg">
          <Loader2 aria-hidden className="h-4 w-4 animate-spin text-accent motion-reduce:animate-none" />
          {t('loading.title')}
        </p>
        <ol className="mt-3 space-y-1.5">
          {steps.map((s, i) => (
            <li key={s.label} className={cn('flex items-center gap-2 text-caption', s.done ? 'text-muted' : i === current ? 'text-fg' : 'text-subtle')}>
              {s.done ? <Check aria-hidden className="h-3.5 w-3.5 text-ok" /> : <Circle aria-hidden className={cn('h-3 w-3', i === current ? 'text-accent' : '')} />}
              {s.label}
              <span className="sr-only">{s.done ? '(done)' : i === current ? '(in progress)' : '(waiting)'}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
