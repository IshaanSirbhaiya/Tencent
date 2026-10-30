'use client';
// Compare two people (D-17): two independent graphs side by side. The left is this session; the
// right is a second person signed into compare slot b with their own signed cookie. Each side shows
// ONLY its own API results. There is no union, no diff and no placeholder for anything one side
// cannot open. The optional "also open" marking posts the LEFT side's own visible doc ids to
// POST /ib/graph/openable under the RIGHT person's session: it labels shared items on the left and
// can never reveal anything of the right person's that the left cannot already see.
import { Users } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Switch } from '@/components/ui';
import { useSession } from '@/components/shell';
import { ApiError, graphApi, withSlotLocks } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import { PERSONAS } from '@/lib/personas';
import type { KnowledgeGraph, KnowledgeMap } from '@/lib/types';
import { fmtCount } from './format';
import { GraphExplorer, type LoadedInfo } from './GraphExplorer';

const CHUNK = 300;

function firstName(n: string) {
  return n.split(' ')[0];
}

export function CompareView({ initialPersona }: { initialPersona?: string | null }) {
  const t = useMessages(graphMessages);
  const { me } = useSession();
  const choices = useMemo(() => PERSONAS.filter((p) => p.id !== 'admin.it' && p.id !== me?.sub), [me?.sub]);
  const fallback = choices.find((p) => p.id === 'ken.ext')?.id ?? choices[0]?.id ?? 'ken.ext';
  const [persona, setPersona] = useState(initialPersona && choices.some((c) => c.id === initialPersona) ? initialPersona : fallback);
  const [loadKey, setLoadKey] = useState(0);
  const [right, setRight] = useState<{ sub: string; name: string } | null>(null);
  const [leftInfo, setLeftInfo] = useState<LoadedInfo | null>(null);
  const [rightInfo, setRightInfo] = useState<LoadedInfo | null>(null);
  const [markShared, setMarkShared] = useState(true);
  const [shared, setShared] = useState<Set<string> | null>(null);
  const [sharedState, setSharedState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle');
  const rightSub = useRef<string | null>(null);

  // Sign slot b in as the chosen person and fetch THEIR graph + map (one GET /ib/galaxy), holding the slot lock so another
  // tab cannot switch the slot halfway. Every response is checked against the sub just signed in.
  const preload = useCallback(async (): Promise<{ overview: KnowledgeGraph; map: KnowledgeMap }> => {
    const api = graphApi('b');
    return withSlotLocks(['b'], async () => {
      const who = await api.login(persona);
      if (who.sub !== persona) throw new ApiError(0, 'identity');
      rightSub.current = who.sub;
      setRight({ sub: who.sub, name: who.name || persona });
      const { overview, map } = await api.galaxy({ limit: 300 });     // one live pass for this side
      if ((await api.whoami()).sub !== who.sub) throw new ApiError(0, 'identity');
      return { overview, map };
    });
    // loadKey: "Reload" re-runs the sign-in
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona, loadKey]);

  useEffect(() => {
    setRight(null);
    setRightInfo(null);
    setShared(null);
    setSharedState('idle');
  }, [persona, loadKey]);

  // "Also open" marking: the right person's session answers for the left side's own ids.
  useEffect(() => {
    if (!markShared || !leftInfo || !rightInfo || !rightSub.current) {
      setShared(null);
      setSharedState('idle');
      return;
    }
    let live = true;
    setSharedState('busy');
    const api = graphApi('b');
    const expect = rightSub.current;
    (async () => {
      const ids = leftInfo.docIds;
      const out = new Set<string>();
      await withSlotLocks(['b'], async () => {
        for (let i = 0; i < ids.length; i += CHUNK) {
          const r = await api.openable(ids.slice(i, i + CHUNK));
          if (r.sub !== expect) throw new ApiError(0, 'identity');
          r.openable.forEach((id) => out.add(id));
        }
      });
      if (live) {
        setShared(out);
        setSharedState('done');
      }
    })().catch(() => {
      if (live) {
        setShared(null);
        setSharedState('error');
      }
    });
    return () => {
      live = false;
    };
  }, [markShared, leftInfo, rightInfo]);

  const leftName = me?.name ?? me?.sub ?? '';
  const rightName = right?.name ?? '';
  const summary =
    leftInfo && rightInfo
      ? t('compare.summary', { a: firstName(leftName), an: fmtCount(leftInfo.totals.items), b: firstName(rightName), bn: fmtCount(rightInfo.totals.items) })
      : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface px-4 py-2">
        <p className="flex items-center gap-2 text-caption text-muted">
          <Users aria-hidden className="h-3.5 w-3.5" />
          {t('compare.note')}
        </p>
        {summary ? (
          // Count comparisons are for Developer mode: the Stage theme (projector) hides them (round-2 Fable §6).
          <p className="text-body-sm font-medium tabular-nums text-fg" data-testid="compare-summary" data-stage-hide>
            {summary}
          </p>
        ) : null}
        <div className="ml-auto flex items-center gap-2" data-stage-hide data-testid="compare-shared-group">
          <Switch
            id="graph-mark-shared"
            checked={markShared}
            onCheckedChange={setMarkShared}
            aria-describedby="graph-mark-shared-status"
          />
          <label htmlFor="graph-mark-shared" className="text-caption text-fg">
            {t('compare.shared', { name: firstName(rightName) || '…' })}
          </label>
          <span id="graph-mark-shared-status" className="text-caption tabular-nums text-muted" aria-live="polite" data-testid="compare-shared">
            {markShared && sharedState === 'done' && shared
              ? shared.size
                ? t('compare.sharedCount', { name: firstName(rightName), n: fmtCount(shared.size) })
                : t('compare.sharedNone', { name: firstName(rightName) })
              : markShared && sharedState === 'busy'
                ? '…'
                : ''}
          </span>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-rows-2 lg:grid-cols-2 lg:grid-rows-1">
        <section aria-label={`${t('compare.left')}: ${leftName}`} className="flex min-h-0 flex-col border-b border-line lg:border-b-0 lg:border-r">
          <SideHeader title={t('compare.left')} name={leftName} sub={me?.sub ?? ''} count={leftInfo?.totals.items ?? null} accent />
          <div className="min-h-0 flex-1">
            <GraphExplorer slot="a" compact shared={markShared ? shared : null} onLoaded={setLeftInfo} name={leftName} testId="graph-left" />
          </div>
        </section>
        <section aria-label={`${t('compare.right')}: ${rightName || persona}`} className="flex min-h-0 flex-col">
          <SideHeader
            title={t('compare.right')}
            name={rightName}
            sub={right?.sub ?? ''}
            count={rightInfo?.totals.items ?? null}
            control={
              <div className="flex items-center gap-1.5">
                <label htmlFor="graph-compare-persona" className="sr-only">
                  {t('compare.choose')}
                </label>
                <select
                  id="graph-compare-persona"
                  value={persona}
                  onChange={(e) => setPersona(e.target.value)}
                  className="h-7 rounded-md border border-line bg-surface px-2 text-caption text-fg hover:border-line-strong"
                  data-testid="compare-persona"
                >
                  {choices.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} · {p.role}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setLoadKey((k) => k + 1)}
                  className="h-7 rounded-md border border-line px-2 text-caption text-muted hover:border-line-strong hover:text-fg"
                >
                  {t('compare.reload')}
                </button>
              </div>
            }
          />
          <div className="min-h-0 flex-1">
            <GraphExplorer key={`${persona}:${loadKey}`} slot="b" expectSub={persona} preload={preload} compact onLoaded={setRightInfo} name={rightName} testId="graph-right" />
          </div>
        </section>
      </div>
    </div>
  );
}

function SideHeader({ title, name, sub, count, control, accent }: { title: string; name: string; sub: string; count: number | null; control?: React.ReactNode; accent?: boolean }) {
  return (
    <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-bg px-3 py-1.5">
      <div className="min-w-0">
        <p className="text-micro font-semibold uppercase tracking-wide text-subtle">{title}</p>
        <p className="truncate text-body-sm text-fg">
          <span className={cn('font-medium', accent && 'text-fg')}>{name || '…'}</span>
          {sub ? <span className="ml-1.5 font-mono text-caption text-muted">{sub}</span> : null}
        </p>
      </div>
      {count !== null ? (
        <p className="text-caption text-muted" data-stage-hide data-testid="compare-side-count">
          can open <span className="font-medium tabular-nums text-fg">{fmtCount(count)}</span> items
        </p>
      ) : null}
      <div className="ml-auto">{control}</div>
    </div>
  );
}
