'use client';
import { ChevronRight, Database, Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { BrandMark, MockBanner, PRODUCT, SUBTITLE } from '@/components/Brand';
import { ThemeToggle } from '@/components/ThemeToggle';
import { ApiError, api } from '@/lib/api';
import { PERSONAS, type Persona } from '@/lib/personas';

export default function LoginPage() {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [passcode, setPasscode] = useState('');

  async function signIn(p: Persona) {
    setBusy(p.id);
    setError(null);
    try {
      const r = await api.login(p.id, passcode.trim() || undefined);
      router.replace(r.roles.includes('admin_it') && !r.roles.includes('compliance_officer') ? '/sim' : '/');
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(
        status === 401
          ? 'This demo needs the passcode you were given. Check it and try again.'
          : status === 404
          ? 'Demo sign-in is not enabled on this server.'
          : status === 400
            ? 'That persona is not in the directory.'
            : 'The Internal Brain is temporarily unavailable. Try again in a moment.',
      );
      setBusy(null);
    }
  }

  const people = PERSONAS.filter((p) => p.id !== 'admin.it');
  const admin = PERSONAS.find((p) => p.id === 'admin.it')!;

  const PersonaButton = ({ p }: { p: Persona }) => (
    <button
      type="button"
      onClick={() => void signIn(p)}
      disabled={busy !== null}
      data-persona={p.id}
      className="group flex w-full items-center gap-3 rounded-md border border-line-strong bg-surface px-3 py-2.5 text-left hover:border-accent/60 hover:bg-raised disabled:opacity-60"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-fg">
          {p.id === 'admin.it' ? 'IT admin (simulator)' : `${p.name} — ${p.role}`}
        </span>
        <span className="block truncate text-2xs text-muted">{p.note}</span>
        <span className="block font-mono text-2xs text-muted">{p.id}</span>
      </span>
      {busy === p.id ? (
        <Loader2 aria-hidden className="h-4 w-4 shrink-0 text-muted motion-safe:animate-spin" />
      ) : (
        <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-muted group-hover:text-fg" />
      )}
    </button>
  );

  return (
    <>
      <MockBanner />
      <main id="main" tabIndex={-1} className="mx-auto flex min-h-[calc(100vh-24px)] max-w-3xl flex-col justify-center px-4 py-10 focus:outline-none sm:px-6">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <BrandMark />
            <div>
              <p className="text-base font-semibold tracking-tight">{PRODUCT}</p>
              <p className="text-sm text-muted">{SUBTITLE}</p>
            </div>
          </div>
          <ThemeToggle />
        </div>

        <section aria-labelledby="signin-heading" className="rounded-lg border border-line bg-surface">
          <div className="border-b border-line px-5 py-4">
            <div className="mb-2 inline-flex items-center gap-1.5 rounded border border-line-strong bg-raised px-2 py-0.5 text-2xs font-medium text-muted" data-testid="synthetic-label">
              <Database aria-hidden className="h-3 w-3" /> Synthetic demo data — Company A
            </div>
            <h1 id="signin-heading" className="text-lg font-semibold">
              Demo sign-in
            </h1>
            <p className="mt-1 text-sm text-muted">
              Choose a persona. Each one sees only what they can open in Confluence, Jira, Slack and Google Drive, and every question is recorded in the audit ledger.
            </p>
          </div>
          <div className="space-y-5 px-5 py-4">
            <div>
              <label htmlFor="demo-passcode" className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted">
                Demo passcode <span className="font-normal normal-case">(only if you were given one)</span>
              </label>
              <input
                id="demo-passcode"
                type="password"
                autoComplete="off"
                value={passcode}
                onChange={(e) => setPasscode(e.target.value)}
                className="w-full max-w-xs rounded-md border border-line-strong bg-raised px-3 py-2 text-sm text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              />
            </div>
            <div>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">People at Company A</h2>
              <ul className="grid gap-2 sm:grid-cols-2">
                {people.map((p) => (
                  <li key={p.id}>
                    <PersonaButton p={p} />
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Platform operator</h2>
              <ul className="grid gap-2 sm:grid-cols-2">
                <li>
                  <PersonaButton p={admin} />
                </li>
              </ul>
            </div>
            {error ? (
              <p role="alert" className="rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-fg">
                {error}
              </p>
            ) : null}
          </div>
          <p className="border-t border-line px-5 py-3 text-2xs text-muted">
            Demo sign-in stands in for single sign-on. Your session is an httpOnly cookie; the browser never holds a token.
          </p>
        </section>
      </main>
    </>
  );
}
