'use client';
// Ask (User mode home): greeting, one composer, the scope pill, four starter cards; then a
// conversation thread of the last few questions on this page (local state only). In light mode the
// view uses the Xcode-like tokens (.ask-x in globals.css).
//
// The canvas: after each answer the Brain is asked whether it needs a page (POST /ib/views
// mode "decide"; the model decides when connected, else the rules). A simple answer stays in the
// chat. When a page is right ("everything about the project and its people"), the chat moves left,
// the sidebar shrinks to its icons and the page is built in the canvas on the right (ViewCanvas);
// below 1024 px it opens as a full-screen sheet. While a page is open, a short request about its
// look ("make the table black") changes that page instead of asking the Brain a new question.
import { Lock, SquarePen } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BrandMark } from '@/components/Brand';
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
  isAskCancelled,
  useAskProgress,
} from '@/components/user';
import { ViewCanvas, useCanvasIsInline, type CanvasState } from '@/components/user/ViewCanvas';
import { viewFailOf, viewsStatus } from '@/components/user/ViewRow';
import { ApiError, viewApi } from '@/lib/api';
import { useMessages } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import { viewMessages } from '@/lib/i18n/view';
import { autoNavRail } from '@/lib/navRail';
import { PERSONAS, SAMPLE_QUESTION, personaById } from '@/lib/personas';
import { useTourAction } from '@/lib/tour';
import type { AskResponse } from '@/lib/types';

/** How many questions the thread keeps on screen (older ones are in History). */
const MAX_TURNS = 8;

type Turn =
  | { id: number; question: string; state: 'pending'; asked?: string }
  | { id: number; question: string; state: 'done'; res: AskResponse; asked?: string }
  | { id: number; question: string; state: 'error'; status: number }
  /** a change to the open page, asked in the chat (no question to the Brain) */
  | { id: number; question: string; state: 'edit'; done: boolean };

function greetingKey(): 'ask.greeting.morning' | 'ask.greeting.afternoon' | 'ask.greeting.evening' {
  const h = new Date().getHours();
  return h < 12 ? 'ask.greeting.morning' : h < 18 ? 'ask.greeting.afternoon' : 'ask.greeting.evening';
}

/** A short request about how the open page looks ("make the table black", "show people as cards"),
 *  not a new question. English and 简体中文. */
const PAGE_EDIT =
  /^(?:(?:please\s+)?(?:make|turn|change|switch|use|show|put|render|display|sort|order|group|add|remove|hide|colou?r|reorder|move)\b.*\b(?:table|chart|cards?|list|timeline|black|dark|white|light|colou?r|green|blue|red|purple|orange|teal|pink|gray|grey|bigger|smaller|columns?|rows?|people|section|page|layout|order|top|bottom|first|last)\b|.*(?:改成|换成|改为|变成|黑色|白色|用表格|用卡片))/i;
function isPageEdit(text: string): boolean {
  const s = text.trim();
  return s.length > 0 && s.length <= 160 && PAGE_EDIT.test(s);
}

export default function AskPage() {
  const t = useMessages(userMessages);
  const tv = useMessages(viewMessages);
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

  // The canvas: one page at a time; the last page of each answer can be shown again.
  const [canvas, setCanvas] = useState<CanvasState | null>(null);
  const canvasRef = useRef<CanvasState | null>(null);
  canvasRef.current = canvas;
  const pages = useRef(new Map<number, CanvasState>());
  const viewSeq = useRef(0);
  const inlineCanvas = useCanvasIsInline();

  /** Build (or rebuild) the page for one answer. `onDone` runs when it is shown or has failed. */
  const buildView = useCallback(
    (turnId: number, response: AskResponse, question: string, instruction: string, opts: { auto?: boolean; title?: string; onDone?: () => void } = {}) => {
      const my = ++viewSeq.current;
      const base = { turnId, response, question, instruction, title: opts.title, focus: !opts.auto };
      setCanvas({ ...base, load: { state: 'loading', since: Date.now() } });
      viewApi
        .create(response.request_id, instruction.trim().slice(0, 300))
        .then((v) => {
          if (my !== viewSeq.current) return;
          const c: CanvasState = { ...base, load: { state: 'ok', v } };
          pages.current.set(turnId, c);
          setCanvas(c);
          setAnnounce(tv('view.ready'));
        })
        .catch((e) => my === viewSeq.current && setCanvas({ ...base, load: { state: 'fail', kind: viewFailOf(e) } }))
        .finally(() => opts.onDone?.());
    },
    [tv],
  );
  const closeCanvas = () => {
    viewSeq.current++;
    setCanvas(null);
  };
  /** A change to the open page in the person's words: the page is rebuilt with it. */
  // The server knows the page's subject (the answer's own question), so only the change is sent.
  const changePage = useCallback(
    (change: string, opts: { fromChat?: boolean; onDone?: () => void } = {}) => {
      const c = canvasRef.current;
      if (!c) return;
      buildView(c.turnId, c.response, c.question, change || c.instruction, {
        auto: opts.fromChat, // a change asked in the chat keeps the keyboard in the chat
        title: c.load.state === 'ok' ? c.load.v.title : c.title,
        onDone: opts.onDone,
      });
    },
    [buildView],
  );

  // The sidebar shrinks to its icons while a page is open beside the chat.
  useEffect(() => {
    autoNavRail(canvas !== null && inlineCanvas);
  }, [canvas, inlineCanvas]);
  useEffect(() => () => autoNavRail(false), []);

  /** After a new answer: page or chat? Only the newest answer, once, never for a refusal. */
  const decide = useCallback(
    async (turnId: number, question: string, res: AskResponse) => {
      if (!res.citations.length) return;
      const st = await viewsStatus();
      if (!st) return;
      try {
        const d = await viewApi.decide(res.request_id, question);
        if (d.render !== 'page' || seq.current !== turnId) return;
        buildView(turnId, res, question, question, { auto: true, title: d.title });
      } catch {
        /* no page: the answer stays in the chat */
      }
    },
    [buildView],
  );

  const run = useCallback(
    async (question: string, replaceId?: number) => {
      const text = question.trim();
      if (!text || pending) return;
      // a request about the open page's look: change the page, do not ask the Brain
      if (replaceId === undefined && canvasRef.current && canvasRef.current.load.state !== 'loading' && isPageEdit(text)) {
        const id = ++seq.current;
        setTurns((prev) => [...prev, { id, question: text, state: 'edit' as const, done: false }].slice(-MAX_TURNS));
        setQ('');
        setAnnounce(tv('view.edited'));
        changePage(text, {
          fromChat: true,
          onDone: () => {
            setTurns((prev) => prev.map((x) => (x.id === id && x.state === 'edit' ? { ...x, done: true } : x)));
            setAnnounce(tv('view.editedDone'));
          },
        });
        return;
      }
      const id = replaceId ?? ++seq.current;
      // the question that was asked before this one (its words help a short follow-up)
      const previous = [...turns].reverse().find((x) => x.id !== id && (x.state === 'done' || x.state === 'error'));
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
        // A follow-up ("what was said in Slack?") or a long question becomes one standalone
        // search question first (the model, when connected); the chat shows what was searched.
        let asked = text;
        if (previous || text.split(/\s+/).length > 14) {
          const st = await viewsStatus();
          if (st?.composer?.available) {
            try {
              const r = await viewApi.rewrite(text, previous ? (previous.state === 'done' && previous.asked) || previous.question : '');
              if (r.rewritten && r.question.trim()) {
                asked = r.question.trim();
                setTurns((prev) => prev.map((x) => (x.id === id && x.state === 'pending' ? { ...x, asked } : x)));
              }
            } catch {
              /* ask as typed */
            }
          }
        }
        const res = await progress.run(asked);
        focusOnDone.current = id;
        setTurns((prev) => prev.map((x) => (x.id === id ? { id, question: text, state: 'done', res, asked: asked !== text ? asked : undefined } : x)));
        const n = res.citations.length;
        setAnnounce(n === 0 ? t('ask.announce.none') : n === 1 ? t('ask.announce.done1') : t('ask.announce.done', { n }));
        void decide(id, asked, res);
      } catch (e) {
        // Superseded (New conversation, or the page went away): that turn is no longer on screen.
        if (isAskCancelled(e)) return;
        const status = e instanceof ApiError ? e.status : 0;
        if (status === 401) {
          router.replace('/login');
          return;
        }
        setTurns((prev) => prev.map((x) => (x.id === id ? { id, question: text, state: 'error', status } : x)));
        setAnnounce(t('ask.announce.error'));
      }
    },
    [pending, progress, router, t, tv, changePage, decide, turns],
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
    closeCanvas();
    pages.current.clear();
    setTurns([]);
    setTrimmed(false);
    setSheet(null);
    progress.reset();
    setAnnounce('');
    setTimeout(() => composer.current?.focus(), 0);
  };

  const privacy = (
    <p className="mt-3 flex items-start justify-center gap-1.5 text-center text-caption text-subtle" data-testid="privacy-line">
      <Lock aria-hidden className="mt-[3px] h-3 w-3 shrink-0" />
      <span>{t('ask.privacy')}</span>
    </p>
  );

  const open = canvas !== null && inlineCanvas;
  return (
    <Page width="full" className="ask-x min-h-[calc(100dvh-var(--topbar-h))] bg-bg pb-0">
      <div role="status" aria-live="polite" className="sr-only" data-testid="ask-announce">
        {announce}
      </div>

      <div className="ask-split" data-canvas={open ? 'open' : 'closed'}>
        <div className="ask-chat">
          {empty ? (
            <div className="mx-auto flex min-h-[calc(100dvh-var(--topbar-h)-8rem)] max-w-[44rem] flex-col justify-center py-8 sm:py-12">
              <h1 className="text-[1.625rem] font-semibold leading-tight tracking-[-0.02em] text-fg sm:text-[1.875rem]" data-testid="greeting">
                {t(greetingKey(), { name: firstName })}
              </h1>
              <p className="mt-1.5 max-w-xl text-body-sm text-muted">{t('ask.subtitle')}</p>
              <div className="mt-7">
                <Composer ref={composer} value={q} onChange={setQ} onSubmit={() => void run(q)} busy={pending} placeholder={t('ask.placeholder')} size="lg" />
                <div className="mt-2.5 flex flex-wrap items-center justify-center gap-x-3 gap-y-1">
                  <ScopePill />
                </div>
                {privacy}
              </div>
              <div className="mt-10">
                <StarterCards questions={persona.suggestions} onPick={(s) => void run(s)} disabled={pending} />
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3 pb-2">
                <h1 className="min-w-0 truncate text-body-sm font-semibold text-fg" data-testid="greeting">
                  {t(greetingKey(), { name: firstName })}
                </h1>
                <Button variant="ghost" size="sm" icon={SquarePen} onClick={newConversation} disabled={pending}>
                  {t('ask.newChat')}
                </Button>
              </div>

              <section aria-label={t('ask.conversation')} className="pb-8 pt-4">
                {trimmed ? <p className="mb-6 text-center text-caption text-subtle">{t('ask.olderTrimmed')}</p> : null}
                <ol className="space-y-10">
                  {turns.map((turn, i) => {
                    const latest = i === turns.length - 1;
                    return (
                      <li key={turn.id} className="space-y-4" data-turn={turn.state}>
                        <div className="flex justify-end">
                          <p className="max-w-[85%] whitespace-pre-wrap rounded-[1.25rem] rounded-br-md bg-raised px-4 py-2.5 text-body-sm leading-6 text-fg" data-testid="question-bubble">
                            <span className="sr-only">{t('ask.you')}: </span>
                            {turn.question}
                          </p>
                        </div>
                        {(turn.state === 'pending' || turn.state === 'done') && turn.asked ? (
                          <p className="-mt-2 flex justify-end text-caption text-subtle" data-testid="searched-for">
                            <span className="max-w-[85%] truncate" title={turn.asked}>
                              {tv('view.searchedFor')} <span className="text-muted">{turn.asked}</span>
                            </span>
                          </p>
                        ) : null}
                        {turn.state === 'pending' ? (
                          <div aria-busy="true" className="pl-0.5">
                            <AskProgressLine step={progress.step} />
                          </div>
                        ) : turn.state === 'error' ? (
                          <AskErrorCard status={turn.status} onRetry={() => void run(turn.question, turn.id)} />
                        ) : turn.state === 'edit' ? (
                          <p className="flex items-center gap-2 text-body-sm text-muted" data-testid="page-edit">
                            <BrandMark className="!h-6 !w-6" />
                            {turn.done ? tv('view.editedDone') : tv('view.edited')}
                          </p>
                        ) : (
                          <AnswerCard
                            ref={latest ? heading : undefined}
                            response={turn.res}
                            question={turn.question}
                            latest={latest}
                            onCite={(index, opener) => setSheet({ turnId: turn.id, index, opener: opener ?? null })}
                            onFollowUp={(fq) => void run(fq)}
                            onBuildView={() => buildView(turn.id, turn.res, turn.question, turn.question)}
                            viewShown={canvas?.turnId === turn.id}
                            onShowView={pages.current.has(turn.id) ? () => setCanvas({ ...pages.current.get(turn.id)!, focus: true }) : undefined}
                          />
                        )}
                      </li>
                    );
                  })}
                </ol>
              </section>

              <div className="sticky bottom-0 z-10 bg-gradient-to-t from-bg from-70% to-transparent pb-3 pt-6">
                <Composer ref={composer} value={q} onChange={setQ} onSubmit={() => void run(q)} busy={pending} placeholder={t('ask.placeholderNext')} size="md" />
                <div className="mt-2 flex flex-wrap items-center justify-center gap-x-3 gap-y-1">
                  <ScopePill />
                  <p className="hidden min-w-0 items-center gap-1.5 text-caption text-subtle sm:flex" data-testid="privacy-line">
                    <Lock aria-hidden className="h-3 w-3 shrink-0" />
                    <span>{t('ask.privacy')}</span>
                  </p>
                </div>
              </div>
            </>
          )}
        </div>

        <ViewCanvas canvas={canvas} onClose={closeCanvas} onChange={(change) => changePage(change)} />
      </div>

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
