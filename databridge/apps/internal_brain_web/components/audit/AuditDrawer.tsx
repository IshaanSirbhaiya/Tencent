'use client';
import * as Dialog from '@radix-ui/react-dialog';
import { Eye, Lock, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { fmtDateTime, fmtTime, modelLabel } from '@/lib/format';
import type { AuditEvent } from '@/lib/types';
import { PlatformIcon } from '../PlatformIcon';
import { Button } from '../ui';
import { Decisions, titleFor } from './AuditTable';

const MIN_REASON = 10;

export function AuditDrawer({ event, onClose }: { event: AuditEvent | null; onClose: () => void }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<{ answer: string; at: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    setReason('');
    setRevealed(null);
    setError(null);
    setTouched(false);
  }, [event?.req_id]);

  const tooShort = reason.trim().length < MIN_REASON;

  async function reveal(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!event || tooShort) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.reveal(event.req_id, reason.trim());
      setRevealed({ answer: r.answer, at: new Date().toISOString() });
    } catch {
      setError('The answer could not be revealed. Nothing was disclosed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog.Root open={!!event} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l border-line bg-surface text-fg shadow-xl focus:outline-none"
          aria-describedby="drawer-desc"
          data-testid="audit-drawer"
        >
          {event ? (
            <>
              <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
                <div className="min-w-0">
                  <Dialog.Title className="text-base font-semibold">Request details</Dialog.Title>
                  <Dialog.Description id="drawer-desc" className="mt-0.5 truncate font-mono text-2xs text-muted">
                    {event.req_id}
                  </Dialog.Description>
                </div>
                <Dialog.Close className="inline-flex h-8 w-8 items-center justify-center rounded text-muted hover:bg-raised hover:text-fg" aria-label="Close details">
                  <X aria-hidden className="h-4 w-4" />
                </Dialog.Close>
              </div>
              <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
                <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
                  <dt className="text-muted">Time</dt>
                  <dd className="tabular">
                    {fmtDateTime(event.event_time)}
                    {event.synthetic_replay ? ' · synthetic replay' : ''}
                  </dd>
                  <dt className="text-muted">User</dt>
                  <dd className="font-mono">{event.sub}</dd>
                  <dt className="text-muted">Channel</dt>
                  <dd>{event.channel ?? '—'}</dd>
                  <dt className="text-muted">Model</dt>
                  <dd>{modelLabel(event.model)}</dd>
                  {typeof event.latency_ms === 'number' ? (
                    <>
                      <dt className="text-muted">Latency</dt>
                      <dd className="tabular">{event.latency_ms.toLocaleString()} ms</dd>
                    </>
                  ) : null}
                  <dt className="text-muted">Question</dt>
                  <dd>{event.question ?? '—'}</dd>
                </dl>

                <section aria-labelledby="considered-h">
                  <h3 id="considered-h" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                    Considered after the index filter
                  </h3>
                  {(event.considered_meta ?? []).length === 0 && (event.considered ?? []).length === 0 ? (
                    <p className="text-xs text-muted">None</p>
                  ) : (
                    <ul className="divide-y divide-line rounded-md border border-line text-xs">
                      {(event.considered ?? event.considered_meta?.map((m) => m.doc_id) ?? []).map((d) => {
                        const meta = event.considered_meta?.find((m) => m.doc_id === d);
                        return (
                          <li key={d} className="px-3 py-2">
                            <div className="flex flex-wrap items-center gap-2 text-muted">
                              {meta?.platform ? <PlatformIcon platform={meta.platform} /> : null}
                              {meta?.container ? <span>· {meta.container}</span> : null}
                            </div>
                            <div className="mt-0.5 font-medium">{titleFor(event, d)}</div>
                            <div className="font-mono text-2xs text-muted">{d}</div>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>

                <section aria-labelledby="decisions-h">
                  <h3 id="decisions-h" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                    Checked with each source just now
                  </h3>
                  <Decisions e={event} />
                </section>

                <section aria-labelledby="released-h">
                  <h3 id="released-h" className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                    Sources the answer used
                  </h3>
                  {event.refused ? (
                    <p className="text-xs">No answer was given.</p>
                  ) : (event.released ?? []).length === 0 ? (
                    <p className="text-xs text-muted">None</p>
                  ) : (
                    <ul className="space-y-0.5 text-xs">
                      {(event.released ?? []).map((d) => (
                        <li key={d}>
                          {titleFor(event, d)} <span className="font-mono text-2xs text-muted">{d}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section aria-labelledby="reveal-h" className="rounded-md border border-line bg-raised/50 p-3">
                  <h3 id="reveal-h" className="flex items-center gap-2 text-sm font-semibold">
                    <Lock aria-hidden className="h-4 w-4 text-muted" /> Answer text
                  </h3>
                  <p className="mt-1 text-xs text-muted">
                    The log keeps only a fingerprint of the answer. To read the text, give a reason. Your reveal is logged under your name.
                  </p>
                  {revealed ? (
                    <div className="mt-3 space-y-1" data-testid="revealed-answer">
                      <p className="whitespace-pre-wrap rounded border border-line bg-surface p-3 text-sm">{revealed.answer}</p>
                      <p className="text-2xs text-muted">Reveal recorded at {fmtTime(revealed.at)}.</p>
                    </div>
                  ) : (
                    <form onSubmit={reveal} className="mt-3 space-y-2" noValidate>
                      <label htmlFor="reveal-reason" className="block text-xs font-medium">
                        Reason for revealing (required)
                      </label>
                      <textarea
                        id="reveal-reason"
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        rows={2}
                        required
                        aria-invalid={touched && tooShort}
                        aria-describedby="reveal-hint"
                        placeholder="e.g. Investigating ticket GRC-14: confirm what was disclosed to jdoe"
                        className="block w-full rounded-md border border-line-strong bg-bg px-2.5 py-2 text-sm"
                      />
                      <p id="reveal-hint" className={touched && tooShort ? 'text-2xs text-bad' : 'text-2xs text-muted'}>
                        At least {MIN_REASON} characters. {touched && tooShort ? 'Please give a fuller reason.' : ''}
                      </p>
                      {error ? (
                        <p role="alert" className="text-xs">
                          {error}
                        </p>
                      ) : null}
                      <Button type="submit" icon={Eye} busy={busy}>
                        Reveal answer
                      </Button>
                    </form>
                  )}
                </section>
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
