'use client';
import { Lock } from 'lucide-react';
import Link from 'next/link';
import { Card } from '@/components/ui';
import { useI18n } from '@/lib/i18n';
import { MODE_HOME } from '@/lib/prefs';
import type { Role } from './nav';
import { useMode, useSession } from './Providers';

/**
 * The lock card (D-05): explains RBAC instead of a bare 403. It names only the role the VIEW needs;
 * it never says whether any data exists behind it.
 */
export function LockCard({ role, title, body }: { role: Role; title?: string; body?: React.ReactNode }) {
  const { t } = useI18n();
  const mode = useMode();
  const roleName = t(`role.${role}` as Parameters<typeof t>[0]);
  return (
    <Card role="alert" className="flex max-w-xl items-start gap-3 p-4" data-testid="role-lock">
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line bg-raised text-perm-locked">
        <Lock aria-hidden className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <h1 className="text-heading-sm text-fg">{title ?? t('role.lockedTitle')}</h1>
        <p className="mt-1 text-body-sm text-muted">{body ?? t('role.lockedBody', { role: roleName })}</p>
        <Link className="mt-2 inline-block text-sm text-accent underline underline-offset-2" href={MODE_HOME[mode]}>
          {t('role.goHome')}
        </Link>
      </div>
    </Card>
  );
}

/** Render children only for people with `role`; everyone else gets the lock card. */
export function RequireRole({ role, children, fallback }: { role: Role; children: React.ReactNode; fallback?: React.ReactNode }) {
  const { me } = useSession();
  if (!me) return null;
  if (!me.roles.includes(role))
    return (
      <>
        {fallback ?? (
          <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
            <LockCard role={role} />
          </div>
        )}
      </>
    );
  return <>{children}</>;
}
