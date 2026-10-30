'use client';
// Sign-in as a persona picker (PLAN §3.1). The cards are one radio group: Tab reaches the chosen
// person, the arrow keys move the choice, Enter (or a click) signs in. Demo sign-in stands in for
// the company's single sign-on; identity still comes only from the signed session cookie.
import { ArrowRight, Compass, Database, KeyRound, Loader2, ShieldCheck } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { AppShell } from '@/components/shell/AppShell';
import { safeNextPath } from '@/components/shell/nextPath';
import { useSession } from '@/components/shell/Providers';
import { Avatar, Button, Input, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useI18n, useMessages } from '@/lib/i18n';
import { PERSONA_ZH, userMessages } from '@/lib/i18n/user';
import { PERSONAS, type Persona } from '@/lib/personas';
import { startTour } from '@/lib/tour';

const ORDER = PERSONAS; // jdoe first ("Start here"); the IT admin last

export default function LoginPage() {
  const router = useRouter();
  const t = useMessages(userMessages);
  const { lang, t: core } = useI18n();
  const session = useSession();
  const [selected, setSelected] = useState(ORDER[0].id);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showPass, setShowPass] = useState(false);
  const [passcode, setPasscode] = useState('');
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const passRef = useRef<HTMLInputElement>(null);

  // A fresh sign-in: forget whoever was cached before.
  const { clear } = session;
  useEffect(() => clear(), [clear]);

  const role = (p: Persona) => (lang === 'zh' ? PERSONA_ZH[p.id]?.role ?? p.role : p.role);
  const note = (p: Persona) => (lang === 'zh' ? PERSONA_ZH[p.id]?.note ?? p.note : p.note);

  async function signIn(p: Persona) {
    if (busy) return;
    setSelected(p.id);
    setBusy(p.id);
    setError(null);
    try {
      const r = await api.login(p.id, passcode.trim() || undefined);
      // force: a whoami that started before this sign-in must not answer for it.
      await session.refresh({ force: true });
      // Back to the page that sent us here (?next=, a validated same-site path only), else home.
      const next = safeNextPath(new URLSearchParams(window.location.search).get('next'));
      router.replace(next ?? (r.roles.includes('admin_it') && !r.roles.includes('compliance_officer') ? '/sim' : '/'));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      if (status === 401) {
        setShowPass(true);
        setTimeout(() => passRef.current?.focus(), 0);
      }
      setError(
        status === 401 ? t('login.err.passcode') : status === 404 ? t('login.err.disabled') : status === 400 ? t('login.err.unknown') : t('login.err.unavailable'),
      );
      setBusy(null);
    }
  }

  const idx = Math.max(0, ORDER.findIndex((p) => p.id === selected));
  const move = (to: number) => {
    const n = (to + ORDER.length) % ORDER.length;
    setSelected(ORDER[n].id);
    refs.current[n]?.focus();
  };
  const current = ORDER[idx];
  const firstName = (p: Persona) => p.name.split(/\s+/)[0];

  return (
    <AppShell variant="public">
      <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 sm:py-12 lg:py-16">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0 max-w-2xl">
            <p className="mb-2 text-micro font-semibold uppercase tracking-wider text-subtle">{t('login.eyebrow')}</p>
            <h1 id="signin-heading" className="text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-fg sm:text-[2rem]">
              {t('login.title')}
            </h1>
            <p className="mt-2 text-body text-muted">{t('login.subtitle')}</p>
          </div>
          <span
            className="inline-flex items-center gap-1.5 rounded-full border border-line-strong bg-raised px-2.5 py-1 text-caption font-medium text-muted"
            data-testid="synthetic-label"
          >
            <Database aria-hidden className="h-3 w-3" /> {core('app.syntheticData')}
          </span>
        </div>

        <section aria-labelledby="signin-heading" data-tour="personas">
          <p id="persona-keys" className="sr-only">
            {t('login.keys')}
          </p>
          <div
            role="radiogroup"
            aria-label={t('login.people')}
            aria-describedby="persona-keys"
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
            onKeyDown={(e) => {
              if (busy) return;
              if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                e.preventDefault();
                move(idx + 1);
              } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                e.preventDefault();
                move(idx - 1);
              } else if (e.key === 'Home') {
                e.preventDefault();
                move(0);
              } else if (e.key === 'End') {
                e.preventDefault();
                move(ORDER.length - 1);
              }
            }}
          >
            {ORDER.map((p, i) => {
              const on = p.id === selected;
              const loading = busy === p.id;
              return (
                <button
                  key={p.id}
                  ref={(el) => {
                    refs.current[i] = el;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  tabIndex={on ? 0 : -1}
                  disabled={busy !== null && !loading}
                  aria-busy={loading || undefined}
                  data-persona={p.id}
                  onClick={() => void signIn(p)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      void signIn(p);
                    } else if (e.key === ' ') {
                      e.preventDefault();
                      setSelected(p.id);
                    }
                  }}
                  className={cn(
                    'group relative flex min-w-0 items-start gap-3 rounded-xl border bg-surface p-4 text-left transition-colors duration-fast disabled:opacity-60',
                    on ? 'border-accent/70 bg-accent/[0.06]' : 'border-line hover:border-line-strong hover:bg-raised/50',
                  )}
                >
                  <Avatar name={p.name} size="lg" />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-heading-sm text-fg">{p.name}</span>
                      {p.id === 'jdoe' ? (
                        <span className="rounded-full border border-accent/50 bg-accent/10 px-1.5 py-px text-micro font-semibold text-accent">{t('login.startHere')}</span>
                      ) : null}
                    </span>
                    <span className="mt-0.5 block text-body-sm text-muted">{role(p)}</span>
                    <span className="mt-2 block text-caption text-muted">
                      <span className="font-medium text-fg">{t('login.roleLabel')}</span> <span className="line-clamp-2 inline">{note(p)}</span>
                    </span>
                  </span>
                  <span aria-hidden className="absolute right-3 top-3">
                    {loading ? (
                      <Loader2 className="h-4 w-4 text-muted motion-safe:animate-spin" />
                    ) : (
                      <span className={cn('flex h-4 w-4 items-center justify-center rounded-full border', on ? 'border-accent' : 'border-line-strong')}>
                        {on ? <span className="h-2 w-2 rounded-full bg-accent" /> : null}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="mt-6 flex flex-col gap-4 rounded-xl border border-line bg-surface p-4 sm:flex-row sm:items-end sm:justify-between sm:p-5">
            <div className="min-w-0 flex-1">
              {showPass ? (
                <div className="max-w-xs">
                  <label htmlFor="demo-passcode" className="mb-1 block text-caption font-medium text-fg">
                    {t('login.passcode')}
                  </label>
                  <Input
                    ref={passRef}
                    id="demo-passcode"
                    type="password"
                    autoComplete="off"
                    value={passcode}
                    onChange={(e) => setPasscode(e.target.value)}
                    aria-describedby="passcode-hint"
                  />
                  <p id="passcode-hint" className="mt-1 text-caption text-muted">
                    {t('login.passcodeHint')}
                  </p>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setShowPass(true);
                    setTimeout(() => passRef.current?.focus(), 0);
                  }}
                  className="inline-flex h-8 items-center gap-1.5 rounded-md px-1 text-caption font-medium text-muted hover:text-fg"
                >
                  <KeyRound aria-hidden className="h-3.5 w-3.5" />
                  {t('login.passcodeToggle')}
                </button>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2 sm:justify-end">
              <Button variant="ghost" icon={Compass} onClick={() => startTour(0)} data-testid="login-tour">
                {t('login.tour')}
              </Button>
              <Button variant="primary" size="lg" iconRight={busy ? undefined : ArrowRight} busy={busy !== null} onClick={() => void signIn(current)} data-testid="continue">
                {busy ? t('login.signingIn', { name: firstName(current) }) : t('login.continueAs', { name: firstName(current) })}
              </Button>
            </div>
          </div>

          {error ? (
            <p role="alert" className="mt-4 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-fg">
              {error}
            </p>
          ) : null}
        </section>

        <footer className="mt-8 flex flex-col gap-2 border-t border-line pt-5 text-caption text-muted sm:flex-row sm:items-center sm:justify-between">
          <p className="flex items-start gap-1.5" data-testid="login-notice">
            <ShieldCheck aria-hidden className="mt-[2px] h-3.5 w-3.5 shrink-0 text-subtle" />
            <span>
              {t('login.notice')}{' '}
              <Popover>
                <PopoverTrigger asChild>
                  <button type="button" className="font-medium text-fg underline decoration-line-strong underline-offset-4 hover:decoration-fg">
                    {t('login.noticeMore')}
                  </button>
                </PopoverTrigger>
                <PopoverContent className="w-96" aria-label={t('login.noticeTitle')}>
                  <p className="text-heading-sm text-fg">{t('login.noticeTitle')}</p>
                  <p className="mt-2 text-body-sm text-muted">{t('login.noticeBody')}</p>
                </PopoverContent>
              </Popover>
            </span>
          </p>
          <p className="text-subtle">{t('login.sso')}</p>
        </footer>
      </div>
    </AppShell>
  );
}
