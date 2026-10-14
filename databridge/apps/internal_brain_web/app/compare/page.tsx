'use client';
import { AlertCircle, ArrowLeftRight, Users } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useState } from 'react';
import { AnswerCard, AnswerLoading } from '@/components/AnswerPanel';
import { AppShell, DEMO_COMPARE, useMe } from '@/components/AppShell';
import { InfoTip } from '@/components/InfoTip';
import { MapLoading } from '@/components/map/MapLoading';
import { MapSummary } from '@/components/map/MapSummary';
import { Button, Field, inputClass } from '@/components/ui';
import { ApiError, slotApi } from '@/lib/api';
import { PERSONAS, SAMPLE_QUESTION, personaById } from '@/lib/personas';
import { useTourAction } from '@/lib/tour';
import type { AskResponse, KnowledgeMap } from '@/lib/types';

type SlotId = 'b' | 'c';
type Side =
  | { kind: 'idle' }
  | { kind: 'loading'; persona: string }
  | { kind: 'done'; persona: string; sub: string; name: string; question: string; res: AskResponse; map: KnowledgeMap | null | 'loading' }
  | { kind: 'error'; persona: string; status: number };

const CHOICES = PERSONAS.filter((p) => p.id !== 'admin.it');

function label(id: string) {
  const p = personaById(id);
  return p ? `${p.name} — ${p.role}` : id;
}

function errorText(status: number) {
  if (status === 404) return { what: 'Demo sign-in is switched off on this server.', todo: 'Ask whoever runs the demo to turn it on.' };
  if (status === 401) return { what: "This person couldn't be signed in.", todo: 'Sign in from the sign-in page first, then come back.' };
  return { what: 'This answer did not come through.', todo: 'The Internal Brain may be busy. Try again in a moment.' };
}

function Column({ slot, side }: { slot: SlotId; side: Side }) {
  const headingId = `compare-${slot}-heading`;
  const persona = side.kind === 'idle' ? null : side.persona;
  const p = persona ? personaById(persona) : null;
  return (
    <section aria-labelledby={headingId} className="min-w-0 space-y-3" data-testid={`compare-${slot}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id={headingId} className="text-base font-semibold">
          {side.kind === 'done' ? side.name : p?.name ?? persona}
          {p ? <span className="font-normal text-muted"> · {p.role}</span> : null}
        </h2>
        {side.kind === 'done' ? (
          <p className="text-2xs text-muted">
            Signed in separately as <span className="font-mono text-fg">{side.sub}</span>
          </p>
        ) : null}
      </div>
      {side.kind === 'loading' ? (
        <div className="space-y-2" aria-hidden>
          <p className="text-xs text-muted">Signing in and asking as this person…</p>
          <AnswerLoading />
        </div>
      ) : side.kind === 'error' ? (
        <div role="alert" className="flex items-start gap-3 rounded-lg border border-line bg-surface p-4">
          <AlertCircle aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-bad" />
          <div>
            <p className="text-sm font-medium">{errorText(side.status).what}</p>
            <p className="mt-1 text-xs text-muted">{errorText(side.status).todo}</p>
          </div>
        </div>
      ) : side.kind === 'done' ? (
        <>
          <AnswerCard res={side.res} question={side.question} idPrefix={`${slot}-`} signedInAs={side.sub} headingLevel="h3" />
          <div className="rounded-lg border border-line bg-surface px-4 py-3">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">What {side.name.split(' ')[0]} can open</h3>
            {side.map === 'loading' ? (
              <MapLoading compact className="border-0 !px-0 !py-0" />
            ) : side.map ? (
              <MapSummary map={side.map} who={side.name.split(' ')[0]} />
            ) : (
              <p className="text-xs text-muted">Their map didn&apos;t load. Ask again to retry.</p>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}

function CompareView() {
  const me = useMe();
  const [a, setA] = useState('jdoe');
  const [b, setB] = useState('ken.ext');
  const [q, setQ] = useState(SAMPLE_QUESTION);
  const [left, setLeft] = useState<Side>({ kind: 'idle' });
  const [right, setRight] = useState<Side>({ kind: 'idle' });
  const [announce, setAnnounce] = useState('');

  // Per side: sign in to that side's slot and ask. The maps are fetched only after BOTH answers are
  // in (the two maps in parallel): the IB checks every map item live with its source, and that work
  // should not compete with the live checks behind the answers. A summary appears only when whole.
  const askSide = useCallback(async (slot: SlotId, persona: string, question: string, set: React.Dispatch<React.SetStateAction<Side>>) => {
    set({ kind: 'loading', persona });
    const s = slotApi(slot);
    try {
      const who = await s.login(persona);
      const res = await s.ask(question);
      set({ kind: 'done', persona, sub: who.sub, name: who.name || label(persona), question, res, map: 'loading' });
      return { s, id: res.request_id };
    } catch (e) {
      set({ kind: 'error', persona, status: e instanceof ApiError ? e.status : 0 });
      return null;
    }
  }, []);
  const mapSide = useCallback(async (r: { s: ReturnType<typeof slotApi>; id: string } | null, set: React.Dispatch<React.SetStateAction<Side>>) => {
    if (!r) return;
    const map = await r.s.map().catch(() => null);
    set((cur) => (cur.kind === 'done' && cur.res.request_id === r.id ? { ...cur, map } : cur));
  }, []);

  const busy = left.kind === 'loading' || right.kind === 'loading' || (left.kind === 'done' && left.map === 'loading') || (right.kind === 'done' && right.map === 'loading');
  const run = useCallback(async () => {
    const question = q.trim();
    if (!question || busy) return;
    setAnnounce('Asking for both people…');
    const [ra, rb] = await Promise.all([askSide('b', a, question, setLeft), askSide('c', b, question, setRight)]);
    await Promise.all([mapSide(ra, setLeft), mapSide(rb, setRight)]);
    setAnnounce('Both answers are ready.');
  }, [q, a, b, busy, askSide, mapSide]);

  useTourAction('compare-run', () => void run());

  const settled = !busy && (left.kind === 'done' || left.kind === 'error') && (right.kind === 'done' || right.kind === 'error');
  const started = left.kind !== 'idle' || right.kind !== 'idle';

  if (!DEMO_COMPARE) {
    return (
      <div className="rounded-lg border border-line bg-surface p-6">
        <p className="text-sm font-medium">Comparing two people needs demo sign-in.</p>
        <p className="mt-1 text-xs text-muted">It is switched off on this server.</p>
        <Link href="/" className="mt-3 inline-flex h-8 items-center rounded-md border border-line-strong px-3 text-xs font-medium hover:bg-raised">
          Go to Ask
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-1">
          <h1 className="text-xl font-semibold tracking-tight">Same question, two people</h1>
          <InfoTip title="Same question, two people" testId="infotip-compare">
            Each person is signed in separately on this page, with their own signed session, so each answer is exactly what that person would get. Your own sign-in{me ? ` (${me.sub})` : ''} is not changed.
          </InfoTip>
        </div>
        <p className="mt-1 max-w-3xl text-sm text-muted" data-testid="compare-why">
          Each answer uses only what that person can open. The contractor&apos;s answer doesn&apos;t hint that more exists.
        </p>
      </div>

      <form
        className="space-y-3 rounded-lg border border-line bg-surface p-4"
        aria-label="Compare two people"
        data-tour="compare-form"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-end">
          <Field id="cmp-a" label="First person">
            <select id="cmp-a" value={a} onChange={(e) => setA(e.target.value)} className={inputClass} disabled={busy}>
              {CHOICES.map((p) => (
                <option key={p.id} value={p.id}>
                  {label(p.id)} ({p.id})
                </option>
              ))}
            </select>
          </Field>
          <ArrowLeftRight aria-hidden className="mx-auto hidden h-4 w-4 text-muted sm:mb-2.5 sm:block" />
          <Field id="cmp-b" label="Second person">
            <select id="cmp-b" value={b} onChange={(e) => setB(e.target.value)} className={inputClass} disabled={busy}>
              {CHOICES.map((p) => (
                <option key={p.id} value={p.id}>
                  {label(p.id)} ({p.id})
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field id="cmp-q" label="Question for both">
          <textarea
            id="cmp-q"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            rows={2}
            maxLength={2000}
            className="block w-full resize-y rounded-md border border-line-strong bg-bg px-3 py-2 text-sm leading-6 text-fg"
          />
        </Field>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-2xs text-muted">Both questions are recorded in the audit log under each person&apos;s own name.</p>
          <Button type="submit" variant="primary" icon={Users} busy={busy} disabled={!q.trim()}>
            {busy ? 'Asking…' : 'Ask for both'}
          </Button>
        </div>
      </form>

      <div role="status" aria-live="polite" className="sr-only">
        {announce}
      </div>

      {started ? (
        <div className="grid gap-6 lg:grid-cols-2" data-tour={settled ? 'compare-results' : undefined} data-testid="compare-results" aria-busy={busy}>
          <Column slot="b" side={left} />
          <Column slot="c" side={right} />
        </div>
      ) : null}
    </div>
  );
}

export default function ComparePage() {
  return (
    <AppShell>
      <CompareView />
    </AppShell>
  );
}
