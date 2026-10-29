'use client';
// Ask (User mode home): greeting, one composer, the scope pill, four starter cards; then a
// conversation thread of the last few questions on this page (local state only).
import { Lock, SquarePen } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Page } from '@/components/shell/Page';
import { useMe } from '@/components/shell/Providers';
import { Button } from '@/components/ui';
import {
  AnswerCard,
  AskErrorCard,
  AskProgressLine,
  Composer,
  ScopePill,
  SourcesSheet,
  StarterCards,
  Toaster,
  type ComposerHandle,
  useAskProgress,
} from '@/components/user';
import { ApiError } from '@/lib/api';
import { useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import { PERSONAS, SAMPLE_QUESTION, personaById } from '@/lib/personas';
import { useTourAction } from '@/lib/tour';
import type { AskResponse } from '@/lib/types';

/** How many questions the thread keeps on screen (older ones are in History). */
const MAX_TURNS = 8;

type Turn =
  | { id: number; question: string; state: 'pending' }
  | { id: number; question: string; state: 'done'; res: AskResponse }
  | { id: number; question: string; state: 'error'; status: number };

function greetingKey(): 'ask.greeting.morning' | 'ask.greeting.afternoon' | 'ask.greeting.evening' {
  const h = new Date().getHours();
  return h < 12 ? 'ask.greeting.morning' : h < 18 ? 'ask.greeting.afternoon' : 'ask.greeting.evening';
}

export default function AskPage() {
  const t = useMessages(userMessages);
  const me = useMe();
  const router = useRouter();
  const persona = personaById(me?.sub) ?? PERSONAS[0];
  const firstName = (me?.name ?? persona.name).split(/\s+/)[0];
  const progress = useAskProgress();

  const [q, setQ] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [trimmed, setTrimmed] = useState(false);
  const [announce, setAnnounce] = useState('');
  const [sheet, setSheet] = useState<{ turnId: number; index: number; opener: HTMLElement | null } | null>(null);
  const seq = useRef(0);
  const composer = useRef<ComposerHandle>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const focusOnDone = useRef<number | null>(null);
  const pending = turns.some((x) => x.state === 'pending');

  const run = useCallback(
    async (question: string, replaceId?: number) => {
      const text = question.trim();
      if (!text || pending) return;
      const id = replaceId ?? ++seq.current;
      setTurns((prev) => {
        const turn: Turn = { id, question: text, state: 'pending' };
        if (replaceId !== undefined) return prev.map((x) => (x.id === replaceId ? turn : x));
        const next = [...prev, turn];
        if (next.length > MAX_TURNS) setTrimmed(true);
        return next.slice(-MAX_TURNS);
      });
      if (replaceId === undefined) setQ('');
      setAnnounce(t('ask.announce.start'));
      try {
        const res = await progress.run(text);
        focusOnDone.current = id;
        setTurns((prev) => prev.map((x) => (x.id === id ? { id, question: text, state: 'done', res } : x)));
        const n = res.citations.length;
        setAnnounce(n === 0 ? t('ask.announce.none') : n === 1 ? t('ask.announce.done1') : t('ask.announce.done', { n }));
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0;
        if (status === 401) {
          router.replace('/login');
          return;
        }
        setTurns((prev) => prev.map((x) => (x.id === id ? { id, question: text, state: 'error', status } : x)));
        setAnnounce(t('ask.announce.error'));
      }
    },
    [pending, progress, router, t],
  );

  // When an answer arrives, move focus to it (keyboard and screen-reader users land on it).
  useEffect(() => {
    if (focusOnDone.current === null) return;
    const last = turns[turns.length - 1];
    if (last && last.id === focusOnDone.current && last.state === 'done') {
      focusOnDone.current = null;
      heading.current?.focus();
    }
  }, [turns]);

  useTourAction('ask-sample', () => void run(SAMPLE_QUESTION));

  const sheetTurn = sheet ? turns.find((x) => x.id === sheet.turnId) : undefined;
  const sheetRes = sheetTurn && sheetTurn.state === 'done' ? sheetTurn.res : null;
  const empty = turns.length === 0;

  const newConversation = () => {
    setTurns([]);
    setTrimmed(false);
    setSheet(null);
    progress.reset();
    setAnnounce('');
    setTimeout(() => composer.current?.focus(), 0);
  };

  const privacy = (
    <p className="mt-2.5 flex items-start justify-center gap-1.5 text-center text-caption text-subtle" data-testid="privacy-line">
      <Lock aria-hidden className="mt-[3px] h-3 w-3 shrink-0" />
      <span>{t('ask.privacy')}</span>
    </p>
  );

  return (
    <Page width="narrow" className="pb-0">
      <div role="status" aria-live="polite" className="sr-only" data-testid="ask-announce">
        {announce}
      </div>

      {empty ? (
        <div className="flex min-h-[calc(100dvh-var(--topbar-h)-12rem)] flex-col justify-center py-6 sm:py-10">
          <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-fg sm:text-[2rem]" data-testid="greeting">
            {t(greetingKey(), { name: firstName })}
          </h1>
          <p className="mt-2 max-w-xl text-body text-muted">{t('ask.subtitle')}</p>
          <div className="mt-8">
            <div className="mb-2.5 flex items-center">
              <ScopePill />
            </div>
            <Composer ref={composer} value={q} onChange={setQ} onSubmit={() => void run(q)} busy={pending} placeholder={t('ask.placeholder')} size="lg" />
            {privacy}
          </div>
          <div className="mt-10">
            <StarterCards questions={persona.suggestions} onPick={(s) => void run(s)} disabled={pending} />
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between gap-3 border-b border-line pb-4">
            <h1 className="min-w-0 truncate text-heading text-fg" data-testid="greeting">
              {t(greetingKey(), { name: firstName })}
            </h1>
            <Button variant="ghost" size="sm" icon={SquarePen} onClick={newConversation} disabled={pending}>
              {t('ask.newChat')}
            </Button>
          </div>

          <section aria-label={t('ask.conversation')} className="pb-6 pt-6">
            {trimmed ? <p className="mb-6 text-center text-caption text-subtle">{t('ask.olderTrimmed')}</p> : null}
            <ol className="space-y-10">
              {turns.map((turn, i) => {
                const latest = i === turns.length - 1;
                return (
                  <li key={turn.id} className="space-y-5" data-turn={turn.state}>
                    <div className="flex justify-end">
                      <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-raised px-4 py-2.5 text-body text-fg" data-testid="question-bubble">
                        <span className="sr-only">{t('ask.you')}: </span>
                        {turn.question}
                      </p>
                    </div>
                    {turn.state === 'pending' ? (
                      <div aria-busy="true" className="pl-0.5">
                        <AskProgressLine step={progress.step} />
                      </div>
                    ) : turn.state === 'error' ? (
                      <AskErrorCard status={turn.status} onRetry={() => void run(turn.question, turn.id)} />
                    ) : (
                      <AnswerCard
                        ref={latest ? heading : undefined}
                        response={turn.res}
                        question={turn.question}
                        latest={latest}
                        onCite={(index, opener) => setSheet({ turnId: turn.id, index, opener: opener ?? null })}
                        onFollowUp={(fq) => void run(fq)}
                      />
                    )}
                  </li>
                );
              })}
            </ol>
          </section>

          <div className="sticky bottom-0 z-10 -mx-4 border-t border-line bg-bg px-4 pb-3 pt-3 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
            <Composer ref={composer} value={q} onChange={setQ} onSubmit={() => void run(q)} busy={pending} placeholder={t('ask.placeholderNext')} size="md" />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <ScopePill />
              <p className="hidden min-w-0 items-center gap-1.5 text-caption text-subtle sm:flex" data-testid="privacy-line">
                <Lock aria-hidden className="h-3 w-3 shrink-0" />
                <span>{t('ask.privacy')}</span>
              </p>
            </div>
          </div>
        </>
      )}

      <SourcesSheet
        response={sheetRes}
        index={sheetRes ? sheet!.index : null}
        onIndexChange={(i) => setSheet((s) => (s ? { ...s, index: i } : s))}
        onClose={() => {
          const el = sheet?.opener;
          setSheet(null);
          // Esc returns focus to the chip or card that opened the sheet.
          setTimeout(() => {
            const a = document.activeElement;
            if (el?.isConnected && (!a || a === document.body)) el.focus();
          }, 0);
        }}
        returnFocusTo={sheet?.opener ?? null}
      />
      <Toaster />
    </Page>
  );
}
