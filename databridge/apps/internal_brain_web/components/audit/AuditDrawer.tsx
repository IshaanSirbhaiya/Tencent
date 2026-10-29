'use client';
// One request, end to end, for a compliance officer: facts, the sealed records, what Gate 2 decided
// for each considered source, what the answer used, and the answer text behind a required reason.
import { Eye, Lock, Route } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Button, CodeHash, PlatformIcon, Sheet, SheetContent } from '@/components/ui';
import { api } from '@/lib/api';
import { fmtDateTime, fmtTime, modelLabel } from '@/lib/format';
import { useMessages } from '@/lib/i18n';
import { auditMessages } from '@/lib/i18n/audit';
import type { AuditRow } from '@/lib/types';
import { CHANNEL, DecisionBadge, OutcomeBadge, titleFor } from './shared';

const MIN_REASON = 10;

function H({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h3 id={id} className="mb-2 text-micro font-semibold uppercase tracking-wide text-subtle">
      {children}
    </h3>
  );
}

export function AuditDrawer({ row, onClose, returnFocus }: { row: AuditRow | null; onClose: () => void; returnFocus?: () => HTMLElement | null }) {
  const t = useMessages(auditMessages);
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
  }, [row?.req_id]);

  const tooShort = reason.trim().length < MIN_REASON;

  async function reveal(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!row || tooShort) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.reveal(row.req_id, reason.trim());
      setRevealed({ answer: r.answer, at: new Date().toISOString() });
    } catch {
      setError('The answer could not be revealed. Nothing was disclosed.');
    } finally {
      setBusy(false);
    }
  }

  const considered = row ? row.considered ?? row.considered_meta?.map((m) => m.doc_id) ?? [] : [];
  const gate2 = new Map((row?.gate2 ?? []).map((g) => [g.doc_id, g]));

  return (
    <Sheet open={!!row} onOpenChange={(o) => !o && onClose()}>
      {row ? (
        <SheetContent
          width="lg"
          title={t('audit.drawer.title')}
          description={<span className="font-mono">{row.req_id}</span>}
          closeLabel="Close details"
          data-testid="audit-drawer"
          onCloseAutoFocus={(e) => {
            const el = returnFocus?.();
            if (el && el.isConnected) {
              e.preventDefault();
              el.focus();
            }
          }}
          footer={
            <Button asChild variant="secondary" size="sm">
              <Link href={`/dev/journey/${encodeURIComponent(row.req_id)}`}>
                <Route aria-hidden className="h-3.5 w-3.5" />
                {t('audit.drawer.journey')}
              </Link>
            </Button>
          }
        >
          <div className="space-y-6 text-body-sm">
            <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-2">
              <dt className="text-muted">Time</dt>
              <dd className="tabular-nums">
                {fmtDateTime(row.event_time)}
                {row.synthetic_replay ? <span className="text-muted"> · synthetic replay</span> : null}
              </dd>
              <dt className="text-muted">Person</dt>
              <dd className="font-mono text-xs">{row.sub}</dd>
              <dt className="text-muted">Channel</dt>
              <dd>{CHANNEL[row.channel ?? ''] ?? row.channel ?? '—'}</dd>
              <dt className="text-muted">Outcome</dt>
              <dd>
                <OutcomeBadge e={row} />
              </dd>
              <dt className="text-muted">Model</dt>
              <dd>{modelLabel(row.model)}</dd>
              {typeof row.latency_ms === 'number' ? (
                <>
                  <dt className="text-muted">Latency</dt>
                  <dd className="tabular-nums">{row.latency_ms.toLocaleString('en-SG')} ms</dd>
                </>
              ) : null}
              <dt className="text-muted">Question</dt>
              <dd>{row.question ?? '—'}</dd>
            </dl>

            {row.records?.length ? (
              <section aria-labelledby="records-h">
                <H id="records-h">{t('audit.drawer.records')}</H>
                <ol className="divide-y divide-line rounded-md border border-line">
                  {row.records.map((r) => (
                    <li key={r.seq} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                      <span className="flex items-center gap-2">
                        <span className="font-mono text-2xs text-subtle tabular-nums">#{r.seq}</span>
                        <span className="font-mono text-xs">{r.kind}</span>
                      </span>
                      <CodeHash value={r.hash} label={`record ${r.seq} hash`} />
                    </li>
                  ))}
                </ol>
              </section>
            ) : null}

            <section aria-labelledby="decisions-h">
              <H id="decisions-h">Considered after the index filter, checked live with each source</H>
              {considered.length === 0 ? (
                <p className="text-muted">None</p>
              ) : (
                <ul className="divide-y divide-line rounded-md border border-line">
                  {considered.map((d) => {
                    const meta = row.considered_meta?.find((m) => m.doc_id === d);
                    const g = gate2.get(d);
                    return (
                      <li key={d} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2 text-2xs text-muted">
                            {meta?.platform ? <PlatformIcon platform={meta.platform} showLabel /> : null}
                            {meta?.container ? <span>· {meta.container}</span> : null}
                          </div>
                          <div className="mt-0.5 font-medium">{titleFor(row, d)}</div>
                          <div className="break-all font-mono text-2xs text-subtle">{d}</div>
                        </div>
                        {g ? <DecisionBadge d={g} /> : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section aria-labelledby="released-h">
              <H id="released-h">Sources the answer used</H>
              {row.refused ? (
                <p>No answer was given.</p>
              ) : (row.released ?? []).length === 0 ? (
                <p className="text-muted">None</p>
              ) : (
                <ul className="space-y-1">
                  {(row.released ?? []).map((d) => (
                    <li key={d}>
                      {titleFor(row, d)} <span className="font-mono text-2xs text-subtle">{d}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-labelledby="reveal-h" className="rounded-lg border border-line bg-raised/50 p-3">
              <h3 id="reveal-h" className="flex items-center gap-2 text-heading-sm">
                <Lock aria-hidden className="h-4 w-4 text-muted" /> Answer text
              </h3>
              <p className="mt-1 text-muted">
                The log keeps only a fingerprint of the answer. To read the text, give a reason. Your reveal is logged under your name.
              </p>
              {revealed ? (
                <div className="mt-3 space-y-1" data-testid="revealed-answer">
                  <p className="whitespace-pre-wrap rounded-md border border-line bg-surface p-3">{revealed.answer}</p>
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
                    className="block w-full rounded-md border border-line-strong bg-bg px-2.5 py-2 text-sm placeholder:text-subtle"
                  />
                  <p id="reveal-hint" className={touched && tooShort ? 'text-2xs font-medium text-warn' : 'text-2xs text-muted'}>
                    At least {MIN_REASON} characters. {touched && tooShort ? 'Please give a fuller reason.' : ''}
                  </p>
                  {error ? (
                    <p role="alert" className="text-xs">
                      {error}
                    </p>
                  ) : null}
                  <Button type="submit" size="sm" icon={Eye} busy={busy}>
                    Reveal answer
                  </Button>
                </form>
              )}
            </section>
          </div>
        </SheetContent>
      ) : null}
    </Sheet>
  );
}
