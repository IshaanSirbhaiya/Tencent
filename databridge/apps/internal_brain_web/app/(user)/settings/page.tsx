'use client';
// Settings (PLAN §3.1). Wave 0 ships the working preference controls because they are shell state;
// the User-mode agent owns this file from here on (privacy notice, reset demo data, read-aloud…).
import { Compass } from 'lucide-react';
import { Page } from '@/components/shell/Page';
import { usePrefs } from '@/components/shell/Providers';
import { startTour } from '@/lib/tour';
import { Button, Card, CardBody, CardHeader, CardTitle, SegmentedControl, Separator, SwitchField } from '@/components/ui';
import { useI18n, type Lang } from '@/lib/i18n';
import type { TextSize, ThemePref } from '@/lib/prefs';

export default function SettingsPage() {
  const { t, lang, setLang } = useI18n();
  const { prefs, setPref, mode, switchMode } = usePrefs();
  return (
    <Page title={t('settings.title')} description={t('settings.subtitle')} width="narrow">
      <div className="space-y-4">
        <Card aria-labelledby="set-lang">
          <CardHeader>
            <CardTitle id="set-lang">{t('settings.language')}</CardTitle>
          </CardHeader>
          <CardBody>
            <SegmentedControl<Lang>
              label={t('settings.language')}
              value={lang}
              onChange={setLang}
              options={[
                { value: 'en', label: 'English' },
                { value: 'zh', label: '简体中文' },
              ]}
            />
          </CardBody>
        </Card>

        <Card aria-labelledby="set-look">
          <CardHeader>
            <CardTitle id="set-look">{t('settings.appearance')}</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm font-medium" id="lbl-theme">
                {t('theme.label')}
              </span>
              <SegmentedControl<ThemePref>
                label={t('theme.label')}
                value={prefs.theme}
                onChange={(v) => setPref('theme', v)}
                options={[
                  { value: 'dark', label: t('theme.dark') },
                  { value: 'light', label: t('theme.light') },
                  { value: 'system', label: t('theme.system') },
                ]}
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm font-medium">{t('settings.textSize')}</span>
              <SegmentedControl<TextSize>
                label={t('settings.textSize')}
                value={prefs.text}
                onChange={(v) => setPref('text', v)}
                options={[
                  { value: '100', label: '100%' },
                  { value: '115', label: '115%' },
                  { value: '130', label: '130%' },
                ]}
              />
            </div>
            <Separator />
            <SwitchField label={t('settings.reduceMotion')} hint={t('settings.reduceMotionHint')} checked={prefs.reduceMotion} onCheckedChange={(v) => setPref('reduceMotion', v)} />
            <SwitchField label={t('settings.highContrast')} hint={t('settings.highContrastHint')} checked={prefs.contrast} onCheckedChange={(v) => setPref('contrast', v)} />
            <Separator />
            <SwitchField
              label={t('settings.devMode')}
              hint={t('settings.devModeHint')}
              checked={mode === 'dev'}
              onCheckedChange={(v) => switchMode(v ? 'dev' : 'user')}
            />
          </CardBody>
        </Card>

        <Card aria-labelledby="set-tour">
          <CardHeader>
            <CardTitle id="set-tour">{t('settings.replayTour')}</CardTitle>
          </CardHeader>
          <CardBody>
            <Button icon={Compass} onClick={() => startTour(0)}>
              {t('settings.replayTour')}
            </Button>
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}
