'use client';
import { useI18n } from '@/lib/i18n';

/** The first Tab stop on every page. Translated (it used to be a hard-coded English string). */
export function SkipLink() {
  const { t } = useI18n();
  return (
    <a href="#main" className="skip-link">
      {t('shell.skip')}
    </a>
  );
}
