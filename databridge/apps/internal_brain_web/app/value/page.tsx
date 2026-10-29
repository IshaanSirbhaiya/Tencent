'use client';
import { Page } from '@/components/shell/Page';
import { ValueCalculator } from '@/components/value/ValueCalculator';
import { useMessages } from '@/lib/i18n';
import { analyticsMessages } from '@/lib/i18n/analytics';

export default function ValuePage() {
  const t = useMessages(analyticsMessages);
  return (
    <Page width="wide" eyebrow={t('value.eyebrow')} title={t('value.title')} description={t('value.description')}>
      <ValueCalculator />
    </Page>
  );
}
