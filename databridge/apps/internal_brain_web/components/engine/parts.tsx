'use client';
// Small shared pieces of the engine view: pointer lines, the failure states, the kind legend, the
// entity search box and the evidence-sheet renderer. Formatting only; nothing here computes a value.
import { CircleAlert, FileText, Lock, PlugZap, Power, RotateCw, Search, X } from 'lucide-react';
import { Fragment, useEffect, useId, useRef, useState } from 'react';
import { Badge, Button, Card, EmptyState, IconButton, inputClass } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { engineMessages } from '@/lib/i18n/engine';
import {
  ENGINE_PERSONA_NAME,
  ENGINE_START_COMMAND,
  EngineError,
  engineApi,
  fixText,
  labelText,
  pointerPath,
  type GraphSearch,
  type KindFacet,
  type SourcePointer,
} from '@/lib/engine-api';
import type { Palette } from './palette';

/* ---------------------------------------------------------------- pointers */

/** One SourcePointer on one line: file → table[row].column, its kind and trust. */
export function PointerLine({ pointer, className }: { pointer: SourcePointer; className?: string }) {
  return (
    <span className={cn('inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-caption', className)}>
      <FileText aria-hidden className="h-3 w-3 shrink-0 text-subtle" />
      <span className="font-mono text-fg">{pointer.source_id}</span>
      <span aria-hidden className="text-subtle">→</span>
      <span className="min-w-0 break-all font-mono text-muted">{pointerPath(pointer)}</span>
      <span className="text-subtle">
        {pointer.kind}
        {pointer.trust ? ` · ${pointer.trust}` : ''}
      </span>
    </span>
  );
}

/* ---------------------------------------------------------------- failure states */

export function EngineFailure({ error, onRetry, compact }: { error: EngineError; onRetry?: () => void; compact?: boolean }) {
  const t = useMessages(engineMessages);
  if (error.failure === 'refused') {
    return (
      <EmptyState
        compact
        icon={CircleAlert}
        title={error.refusal?.code ?? 'Refused'}
        description={fixText(error.refusal?.message ?? error.message)}
      />
    );
  }
  if (error.failure === 'locked') return <EngineLock />;
  if (error.failure === 'off') {
    return <EmptyState compact={compact} icon={Power} title={t('engine.off.title')} description={t('engine.off.body')} />;
  }
  if (error.failure === 'signed-out') return <EmptyState compact={compact} icon={Lock} title={t('engine.signedOut.title')} />;
  return (
    <EmptyState
      compact={compact}
      icon={PlugZap}
      title={t('engine.down.title')}
      description={
        <>
          {t('engine.down.body')}
          <code className="mt-2 block select-all rounded border border-line bg-raised px-2 py-1 text-left font-mono text-caption text-fg">{ENGINE_START_COMMAND}</code>
        </>
      }
      action={
        onRetry ? (
          <Button size="sm" icon={RotateCw} onClick={onRetry}>
            {t('engine.retry')}
          </Button>
        ) : undefined
      }
    />
  );
}

/** Least privilege made visible: who the snapshot was built for, and who may open it. */
export function EngineLock() {
  const t = useMessages(engineMessages);
  return (
    <Card role="alert" className="flex max-w-xl items-start gap-3 p-4" data-testid="engine-lock">
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-perm-locked">
        <Lock aria-hidden className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <h1 className="text-heading-sm text-fg">{t('engine.lock.title')}</h1>
        <p className="mt-1 text-body-sm text-muted">{t('engine.lock.body', { name: ENGINE_PERSONA_NAME })}</p>
      </div>
    </Card>
  );
}

/* ---------------------------------------------------------------- legend */

/** Kind swatches with the store's own counts (GET /graph/facets). */
export function KindLegend({ kinds, palette, className }: { kinds: KindFacet[]; palette: Palette; className?: string }) {
  return (
    <ul className={cn('flex flex-wrap gap-x-3 gap-y-1', className)} aria-label="Node kinds, with the number in the store">
      {kinds.map((k) => {
        const s = palette.node(k.kind);
        return (
          <li key={k.kind} className="inline-flex items-center gap-1.5 text-caption text-muted">
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full border border-fg/25" style={{ background: s.color }} />
            <span className="text-fg">{s.label}</span>
            <span className="tabular-nums text-subtle">{k.count.toLocaleString('en-SG')}</span>
          </li>
        );
      })}
    </ul>
  );
}

/* ---------------------------------------------------------------- search */

/** Find an entity by label, id or canonical id (GET /graph/search), as an ARIA combobox. */
export function EntitySearch({
  onPick,
  palette,
  placeholder = 'Find an entity, e.g. migration',
  label = 'Find an entity',
  className,
}: {
  onPick: (id: string) => void;
  palette: Palette;
  placeholder?: string;
  label?: string;
  className?: string;
}) {
  const [q, setQ] = useState('');
  const [res, setRes] = useState<GraphSearch | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const text = q.trim();
    if (text.length < 2) {
      setRes(null);
      setErr(null);
      return;
    }
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      engineApi
        .search(text, 8, ctl.signal)
        .then((r) => {
          setRes(r);
          setErr(null);
          setActive(0);
          setOpen(true);
        })
        .catch((e: unknown) => {
          if (ctl.signal.aborted) return;
          setRes(null);
          setErr(e instanceof EngineError ? e.message : 'Search failed.');
          setOpen(true);
        });
    }, 220);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [q]);

  const hits = res?.nodes ?? [];
  const pick = (id: string) => {
    onPick(id);
    setOpen(false);
    setQ('');
  };

  return (
    <div className={cn('relative w-full sm:w-80', className)}>
      <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
      <input
        ref={inputRef}
        role="combobox"
        aria-label={label}
        aria-expanded={open && (hits.length > 0 || !!err)}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && hits[active] ? `${listId}-${active}` : undefined}
        className={cn(inputClass, 'h-8 pl-8 pr-8 text-body-sm')}
        placeholder={placeholder}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => res && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, Math.max(hits.length - 1, 0)));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === 'Enter' && hits[active]) {
            e.preventDefault();
            pick(hits[active].id);
          } else if (e.key === 'Escape') setOpen(false);
        }}
      />
      {q ? (
        <IconButton size="sm" icon={X} label="Clear search" className="absolute right-0.5 top-1/2 -translate-y-1/2" onClick={() => setQ('')} />
      ) : null}
      {open && (hits.length > 0 || err || res) ? (
        <ul
          id={listId}
          role="listbox"
          aria-label="Matching entities"
          className="absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-auto rounded-md border border-line bg-overlay p-1 shadow-pop"
        >
          {err ? <li className="px-2 py-1.5 text-caption text-bad">{err}</li> : null}
          {res && hits.length === 0 ? <li className="px-2 py-1.5 text-caption text-muted">No entity matches “{res.q}”.</li> : null}
          {hits.map((h, i) => {
            const s = palette.node(h.kind);
            return (
              <li
                key={h.id}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                className={cn('flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-body-sm', i === active ? 'bg-raised text-fg' : 'text-muted')}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(h.id);
                }}
                onMouseEnter={() => setActive(i)}
              >
                <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
                <span className="min-w-0 flex-1 truncate text-fg">{labelText(h.label)}</span>
                <span className="shrink-0 text-caption text-subtle">{s.label}</span>
              </li>
            );
          })}
          {res && res.truncated ? (
            <li className="px-2 pt-1 text-micro text-subtle">
              Showing {res.returned} of {res.matched_total.toLocaleString('en-SG')} matches
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- evidence sheet */

/** Inline markdown of the engine's sheets: `code`, **bold**, _italic_, [label](#wiki:…), \escapes. */
function inline(text: string, onEntity?: (id: string) => void): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))|(_[^_]+_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  const plain = (s: string) => s.replace(/\\([\\`*_{}[\]()#+\-.!|>])/g, '$1');
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(plain(text.slice(last, m.index)));
    const tok = m[0];
    if (m[1]) out.push(<code key={k++} className="rounded bg-raised px-1 font-mono text-[0.92em] text-fg">{tok.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={k++} className="font-semibold text-fg">{plain(tok.slice(2, -2))}</strong>);
    else if (m[3]) {
      const lm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      const text2 = plain(lm?.[1] ?? tok);
      const href = lm?.[2] ?? '';
      const wiki = href.startsWith('#wiki:') ? decodeURIComponent(href.slice(6)) : null;
      out.push(
        wiki && onEntity ? (
          <button key={k++} type="button" className="text-accent underline decoration-dotted underline-offset-2 hover:decoration-solid" onClick={() => onEntity(wiki)}>
            {text2}
          </button>
        ) : (
          <span key={k++} className="text-fg">{text2}</span>
        ),
      );
    } else if (m[4]) out.push(<em key={k++} className="text-muted">{plain(tok.slice(1, -1))}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(plain(text.slice(last)));
  return out;
}

/**
 * Renders the server-side evidence sheet (GET /brain/node/{id}/sheet). The sheet is the same text
 * the engine's assistant reads, so it is shown as sent: headings, lists and inline marks become
 * elements; nothing is injected as HTML.
 */
export function SheetMarkdown({ markdown, onEntity }: { markdown: string; onEntity?: (id: string) => void }) {
  const lines = fixText(markdown).split('\n');
  const blocks: React.ReactNode[] = [];
  let list: { depth: number; text: string }[] = [];
  const flush = (key: number) => {
    if (!list.length) return;
    const items = list;
    list = [];
    blocks.push(
      <ul key={`l${key}`} className="my-1.5 space-y-1">
        {items.map((it, i) => (
          <li key={i} className={cn('relative text-body-sm', it.depth > 0 ? 'ml-4 text-caption text-muted' : 'pl-3 text-fg')}>
            {it.depth === 0 ? <span aria-hidden className="absolute left-0 top-[0.6em] h-1 w-1 rounded-full bg-subtle" /> : null}
            {inline(it.text, onEntity)}
          </li>
        ))}
      </ul>,
    );
  };
  lines.forEach((raw, i) => {
    const li = /^(\s*)- (.*)$/.exec(raw);
    if (li) {
      list.push({ depth: li[1].length >= 2 ? 1 : 0, text: li[2] });
      return;
    }
    flush(i);
    const line = raw.trim();
    if (!line) return;
    if (line.startsWith('# ')) blocks.push(<h3 key={i} className="text-heading-sm text-fg">{inline(line.slice(2), onEntity)}</h3>);
    else if (line.startsWith('## ')) blocks.push(<h4 key={i} className="mt-3 text-micro font-semibold uppercase tracking-wider text-subtle">{inline(line.slice(3), onEntity)}</h4>);
    else blocks.push(<p key={i} className="my-1 text-body-sm text-muted">{inline(line, onEntity)}</p>);
  });
  flush(lines.length);
  return <div className="min-w-0 break-words">{blocks.map((b, i) => <Fragment key={i}>{b}</Fragment>)}</div>;
}

export function KindBadge({ kind, palette }: { kind: string; palette: Palette }) {
  const s = palette.node(kind);
  return (
    <Badge>
      <span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full align-middle" style={{ background: s.color }} />
      {s.label}
    </Badge>
  );
}
