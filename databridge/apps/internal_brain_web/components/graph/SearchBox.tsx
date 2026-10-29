'use client';
// Search-to-focus: one combobox. Type-ahead over the labels already on the canvas, then (Enter on
// the last option, or no local match) GET /ib/graph?q= for the server's own relevance search.
// Picking an option flies the camera to that node and highlights its one-hop neighbourhood.
import { Loader2, Search, Server } from 'lucide-react';
import { forwardRef, useEffect, useId, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { PlatformIcon } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import { displayLabel, kindLine } from './format';
import type { KGraph } from './model';

export interface SearchHit {
  id: string;
  rank: number | null;
}

interface Option {
  key: string;
  kind: 'local' | 'remote' | 'server';
  id?: string;
}

export interface SearchHandle {
  focus: () => void;
}

export const SearchBox = forwardRef<SearchHandle, {
  graph: KGraph | null;
  version: number;
  onPick: (id: string, query: string) => void;
  onServerSearch: (q: string) => Promise<SearchHit[]>;
  compact?: boolean;
  testId?: string;
}>(function SearchBox({ graph, version, onPick, onServerSearch, compact, testId }, ref) {
  const t = useMessages(graphMessages);
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [remote, setRemote] = useState<{ q: string; hits: SearchHit[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  useImperativeHandle(ref, () => ({ focus: () => input.current?.focus() }), []);

  const local = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!graph || s.length < 2) return [] as string[];
    const starts: string[] = [];
    const contains: string[] = [];
    graph.forEachNode((id, a) => {
      const l = displayLabel(a).toLowerCase();
      if (l.startsWith(s) || l.replace(/^#/, '').startsWith(s)) starts.push(id);
      else if (l.includes(s)) contains.push(id);
    });
    const rank = (id: string) => ({ platform: 0, container: 1, person: 2, doc: 3 })[graph.getNodeAttribute(id, 'kind')];
    return [...starts.sort((a, b) => rank(a) - rank(b)), ...contains.sort((a, b) => rank(a) - rank(b))].slice(0, 7);
    // version: the graph mutates in place when responses are merged
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, graph, version]);

  const remoteHits = remote && remote.q === q.trim() ? remote.hits.filter((h) => graph?.hasNode(h.id) && !local.includes(h.id)) : [];
  const options: Option[] = [
    ...local.map((id) => ({ key: `l:${id}`, kind: 'local' as const, id })),
    ...remoteHits.map((h) => ({ key: `r:${h.id}`, kind: 'remote' as const, id: h.id })),
    ...(q.trim().length >= 2 && !(remote && remote.q === q.trim()) ? [{ key: 'server', kind: 'server' as const }] : []),
  ];
  useEffect(() => setActive(0), [q]);

  const runServer = async () => {
    const s = q.trim();
    if (s.length < 2) return;
    setBusy(true);
    setFailed(false);
    try {
      const hits = await onServerSearch(s);
      setRemote({ q: s, hits });
      setActive(local.length);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const choose = (o: Option | undefined) => {
    if (!o) return;
    if (o.kind === 'server') {
      void runServer();
      return;
    }
    if (o.id) {
      onPick(o.id, q.trim());
      setOpen(false);
    }
  };

  const activeOpt = options[active];
  const showList = open && q.trim().length >= 2;

  return (
    <div className={cn('relative min-w-0 flex-1', compact ? 'max-w-xs' : 'max-w-md')}>
      <label htmlFor={`${listId}-input`} className="sr-only">
        {t('search.label')}
      </label>
      <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
      <input
        ref={input}
        id={`${listId}-input`}
        data-testid={testId}
        role="combobox"
        aria-expanded={showList}
        aria-controls={`${listId}-list`}
        aria-autocomplete="list"
        aria-activedescendant={showList && activeOpt ? `${listId}-${activeOpt.key}` : undefined}
        autoComplete="off"
        spellCheck={false}
        value={q}
        placeholder={t('search.placeholder')}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setFailed(false);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
            setActive((i) => Math.min(options.length - 1, i + 1));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActive((i) => Math.max(0, i - 1));
          } else if (e.key === 'Enter') {
            e.preventDefault();
            choose(activeOpt ?? options[options.length - 1]);
          } else if (e.key === 'Escape') {
            if (open) {
              e.stopPropagation();
              setOpen(false);
            }
          }
        }}
        className="h-8 w-full rounded-md border border-line bg-surface pl-8 pr-8 text-body-sm text-fg placeholder:text-subtle hover:border-line-strong focus:border-accent"
      />
      {busy ? <Loader2 aria-hidden className="absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-subtle motion-reduce:animate-none" /> : null}
      {showList ? (
        <div className="ds-pop absolute left-0 right-0 top-9 z-30 overflow-hidden rounded-lg border border-line bg-overlay shadow-pop">
          <ul id={`${listId}-list`} role="listbox" aria-label={t('search.label')} className="max-h-80 overflow-y-auto py-1">
            {local.length ? <GroupLabel>{t('search.local')}</GroupLabel> : null}
            {options.map((o, i) => {
              if (o.kind === 'server') {
                return (
                  <li
                    key={o.key}
                    id={`${listId}-${o.key}`}
                    role="option"
                    aria-selected={i === active}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => choose(o)}
                    className={cn('mx-1 flex min-h-8 cursor-pointer items-center gap-2 rounded px-2 text-body-sm', i === active ? 'bg-raised text-fg' : 'text-muted')}
                    data-testid="graph-search-server"
                  >
                    <Server aria-hidden className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">{busy ? t('search.busy') : t('search.server', { q: q.trim() })}</span>
                  </li>
                );
              }
              const a = graph!.getNodeAttributes(o.id!);
              const first = o.kind === 'remote' && (i === 0 || options[i - 1].kind !== 'remote');
              return (
                <Row key={o.key} first={first} label={t('search.remote')}>
                  <li
                    id={`${listId}-${o.key}`}
                    role="option"
                    aria-selected={i === active}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => choose(o)}
                    className={cn('mx-1 flex min-h-8 cursor-pointer items-center gap-2 rounded px-2 py-1', i === active ? 'bg-raised' : '')}
                    data-testid="graph-search-option"
                  >
                    {a.platform ? <PlatformIcon platform={a.platform} tone="platform" size="sm" showLabel={false} /> : <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-muted" />}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body-sm text-fg">{displayLabel(a)}</span>
                      <span className="block truncate text-caption text-subtle">{kindLine(a)}</span>
                    </span>
                    {o.kind === 'remote' && a.rank ? <span className="text-caption tabular-nums text-subtle">#{a.rank}</span> : null}
                  </li>
                </Row>
              );
            })}
            {remote && remote.q === q.trim() && !remoteHits.length && !local.length ? <li className="px-3 py-2 text-body-sm text-muted">{t('search.none')}</li> : null}
            {failed ? (
              <li className="px-3 py-2 text-body-sm text-bad" role="alert">
                {t('search.failed')}
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
});

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <li role="presentation" className="px-3 pb-0.5 pt-1.5 text-micro font-semibold uppercase tracking-wide text-subtle">
      {children}
    </li>
  );
}

function Row({ first, label, children }: { first: boolean; label: string; children: React.ReactNode }) {
  return (
    <>
      {first ? <GroupLabel>{label}</GroupLabel> : null}
      {children}
    </>
  );
}
