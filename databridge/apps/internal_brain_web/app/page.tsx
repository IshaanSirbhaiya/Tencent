'use client';
import { ArrowUp, History } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnswerCard, AnswerError, AnswerLoading } from '@/components/AnswerPanel';
import { AppShell, useMe } from '@/components/AppShell';
import { SUBTITLE } from '@/components/Brand';
import { SourcesPanel } from '@/components/SourcesPanel';
import { Button } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { fmtDateTime } from '@/lib/format';
import { PERSONAS, SAMPLE_QUESTION, personaById } from '@/lib/personas';
import { useTourAction } from '@/lib/tour';
import type { AskResponse, HistoryItem } from '@/lib/types';

const MAX = 2000;

type Phase = { kind: 'idle' } | { kind: 'loading'; q: string } | { kind: 'done'; q: string; res: AskResponse } | { kind: 'error'; q: string; status: number };

function AskView() {
  const me = useMe();
  const router = useRouter();
  const persona = personaById(me?.sub) ?? PERSONAS[0];
  const [q, setQ] = useState('');
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [announce, setAnnounce] = useState('');
  const answerHeading = useRef<HTMLHeadingElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const loadHistory = useCallback(() => {
    api
      .history()
      .then((h) => setHistory(h.items ?? []))
      .catch(() => setHistory([]));
  }, []);
  useEffect(loadHistory, [loadHistory]);

  const ask = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || phase.kind === 'loading') return;
      setQ(text);
      setPhase({ kind: 'loading', q: text });
      setAnnounce('Checking access and searching the sources you can open…');
      try {
        const res = await api.ask(text);
        setPhase({ kind: 'done', q: text, res });
        setAnnounce(`Answer ready${res.citations.length ? ` with ${res.citations.length} cited source${res.citations.length > 1 ? 's' : ''}` : ''}.`);
        loadHistory();
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0;
        if (status === 401) {
          router.replace('/login');
          return;
        }
        setPhase({ kind: 'error', q: text, status });
        setAnnounce('');
      }
    },
    [phase.kind, loadHistory, router],
  );

  useEffect(() => {
    if (phase.kind === 'done') answerHeading.current?.focus();
  }, [phase]);

  useTourAction('ask-sample', () => void ask(SAMPLE_QUESTION));

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0 space-y-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Ask the Internal Brain</h1>
          <p className="mt-1 text-sm text-muted">{SUBTITLE}</p>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void ask(q);
          }}
          className="rounded-lg border border-line bg-surface p-3"
          aria-label="Ask a question"
          data-tour="ask-form"
        >
          <label htmlFor="question" className="mb-2 block text-sm font-medium">
            Your question
          </label>
          <textarea
            id="question"
            ref={box}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void ask(q);
              }
            }}
            rows={3}
            maxLength={MAX}
            aria-describedby="question-hint"
            placeholder="e.g. What's the status of the database migration project?"
            className="block w-full resize-y rounded-md border border-line-strong bg-bg px-3 py-2.5 text-[15px] leading-6 text-fg placeholder:text-muted"
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
            <p id="question-hint" className="text-2xs text-muted">
              Enter to ask · Shift+Enter for a new line · <span className="tabular">{q.length.toLocaleString()} / {MAX.toLocaleString()}</span>
            </p>
            <Button type="submit" variant="primary" icon={ArrowUp} busy={phase.kind === 'loading'} disabled={!q.trim()}>
              {phase.kind === 'loading' ? 'Asking…' : 'Ask'}
            </Button>
          </div>
        </form>

        <section aria-labelledby="suggested-heading">
          <h2 id="suggested-heading" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            Suggested for {persona.role.toLowerCase()}
          </h2>
          <ul className="flex flex-wrap gap-2">
            {persona.suggestions.map((s) => (
              <li key={s} className="min-w-0 max-w-full">
                <button
                  type="button"
                  onClick={() => void ask(s)}
                  disabled={phase.kind === 'loading'}
                  className="max-w-full rounded-md border border-line-strong bg-surface px-3 py-1.5 text-left text-xs text-fg hover:border-accent/60 hover:bg-raised disabled:opacity-60"
                >
                  {s}
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-2xs text-muted">Tip: open Compare to see the same question answered for Ken, an outside contractor.</p>
        </section>

        <div role="status" aria-live="polite" className="sr-only">
          {announce}
        </div>

        <div aria-busy={phase.kind === 'loading'}>
          {phase.kind === 'loading' ? (
            <div className="space-y-2">
              <p className="text-xs text-muted">Checking what you can open, then searching…</p>
              <AnswerLoading />
            </div>
          ) : phase.kind === 'done' ? (
            <AnswerCard ref={answerHeading} res={phase.res} question={phase.q} signedInAs={me?.sub} tour />
          ) : phase.kind === 'error' ? (
            <AnswerError status={phase.status} onRetry={() => void ask(phase.q)} />
          ) : null}
        </div>
      </div>

      <aside className="min-w-0 space-y-4" aria-label="Status and history">
        <SourcesPanel refreshKey={phase.kind === 'done' ? 1 : 0} />
        <section aria-labelledby="history-heading" className="rounded-lg border border-line bg-surface">
          <h2 id="history-heading" className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm font-semibold">
            <History aria-hidden className="h-4 w-4 text-muted" /> Your recent questions
          </h2>
          <div className="px-2 py-2">
            {!history ? (
              <p className="px-2 py-1 text-xs text-muted">Loading…</p>
            ) : history.length === 0 ? (
              <p className="px-2 py-1 text-xs text-muted">No questions yet. Ask one above. Only you can see this list.</p>
            ) : (
              <ul>
                {history.slice(0, 6).map((h) => (
                  <li key={h.req_id}>
                    <button
                      type="button"
                      onClick={() => {
                        setQ(h.question);
                        box.current?.focus();
                      }}
                      className="w-full rounded px-2 py-1.5 text-left hover:bg-raised"
                    >
                      <span className="line-clamp-2 block text-xs text-fg">{h.question}</span>
                      <span className="block text-2xs text-muted tabular">{fmtDateTime(h.event_time)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </aside>
    </div>
  );
}

export default function AskPage() {
  return (
    <AppShell>
      <AskView />
    </AppShell>
  );
}
