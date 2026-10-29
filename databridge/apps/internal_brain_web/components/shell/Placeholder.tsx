'use client';
import { Hammer, type LucideIcon } from 'lucide-react';
import { EmptyState } from '@/components/ui';
import { useI18n } from '@/lib/i18n';
import { Page } from './Page';

/** Route skeleton used until a page agent fills the page in. Delete it from the page when you do. */
export function Placeholder({ title, description, icon = Hammer, children }: { title: string; description: string; icon?: LucideIcon; children?: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <Page title={title} description={description}>
      <EmptyState icon={icon} title={t('common.comingTogether')} description={description} />
      {children}
    </Page>
  );
}
