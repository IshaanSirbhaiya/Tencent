'use client';
// Settings (PLAN §3.1): language, appearance and accessibility, Developer mode, the tour, the
// privacy notice and "Reset my local data". Everything here is stored in this browser only.
import { Compass, Languages, Lock, MonitorCog, Palette, RotateCcw, Accessibility } from 'lucide-react';
import { useState } from 'react';
import { Page } from '@/components/shell/Page';
import { usePrefs } from '@/components/shell/Providers';
import { Button, Card, CardBody, CardDescription, CardHeader, CardTitle, Dialog, DialogClose, DialogContent, SegmentedControl, Separator, SwitchField } from '@/components/ui';
import { DemoFeaturesSetting } from '@/components/sim/DemoFeatures';
import { Toaster, toast } from '@/components/user/Toast';
import { clearLocalData } from '@/components/user/local';
import { translate, useI18n, useMessages, type Lang } from '@/lib/i18n';
import { userMessages } from '@/lib/i18n/user';
import { DEFAULT_PREFS, type TextSize, type ThemePref } from '@/lib/prefs';
import { startTour } from '@/lib/tour';

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 py-2">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{label}</p>
        {hint ? <p className="text-caption text-muted">{hint}</p> : null}
      </div>
      {children}
    </div>
  );
}

function SectionTitle({ id, icon: Icon, children }: { id: string; icon: typeof Lock; children: React.ReactNode }) {
  return (
    <CardTitle id={id} className="flex items-center gap-2">
      <Icon aria-hidden className="h-4 w-4 text-subtle" />
      {children}
    </CardTitle>
  );
}

export default function SettingsPage() {
  const { t: core, lang, setLang } = useI18n();
  const t = useMessages(userMessages);
  const { prefs, setPref, mode, switchMode } = usePrefs();
  const [confirm, setConfirm] = useState(false);

  const reset = () => {
    clearLocalData();
    setPref('theme', DEFAULT_PREFS.theme);
    setPref('text', DEFAULT_PREFS.text);
    setPref('contrast', DEFAULT_PREFS.contrast);
    setPref('reduceMotion', DEFAULT_PREFS.reduceMotion);
    setLang(DEFAULT_PREFS.lang);
    setConfirm(false);
    // the toast is in the language just restored
    toast(translate(userMessages, DEFAULT_PREFS.lang, 'settings.resetDone'));
  };

  return (
    <Page title={core('settings.title')} description={core('settings.subtitle')} width="narrow">
      <div className="space-y-4">
        <Card aria-labelledby="set-lang">
          <CardHeader>
            <SectionTitle id="set-lang" icon={Languages}>
              {core('settings.language')}
            </SectionTitle>
            <CardDescription>{t('settings.languageHint')}</CardDescription>
          </CardHeader>
          <CardBody>
            <SegmentedControl<Lang>
              label={core('settings.language')}
              value={lang}
              onChange={setLang}
              testId="settings-language"
              options={[
                { value: 'en', label: 'English' },
                { value: 'zh', label: '简体中文' },
              ]}
            />
          </CardBody>
        </Card>

        <Card aria-labelledby="set-look">
          <CardHeader>
            <SectionTitle id="set-look" icon={Palette}>
              {core('settings.appearance')}
            </SectionTitle>
          </CardHeader>
          <CardBody className="divide-y divide-line py-1">
            <Row label={t('settings.theme')}>
              <SegmentedControl<ThemePref>
                label={core('theme.label')}
                value={prefs.theme}
                onChange={(v) => setPref('theme', v)}
                options={[
                  { value: 'dark', label: core('theme.dark') },
                  { value: 'light', label: core('theme.light') },
                  { value: 'system', label: core('theme.system') },
                ]}
              />
            </Row>
            <Row label={core('settings.textSize')}>
              <SegmentedControl<TextSize>
                label={core('settings.textSize')}
                value={prefs.text}
                onChange={(v) => setPref('text', v)}
                options={[
                  { value: '100', label: '100%' },
                  { value: '115', label: '115%' },
                  { value: '130', label: '130%' },
                ]}
              />
            </Row>
          </CardBody>
        </Card>

        <Card aria-labelledby="set-a11y">
          <CardHeader>
            <SectionTitle id="set-a11y" icon={Accessibility}>
              {t('settings.accessibility')}
            </SectionTitle>
          </CardHeader>
          <CardBody className="py-1">
            <SwitchField label={core('settings.reduceMotion')} hint={core('settings.reduceMotionHint')} checked={prefs.reduceMotion} onCheckedChange={(v) => setPref('reduceMotion', v)} />
            <Separator />
            <SwitchField label={core('settings.highContrast')} hint={core('settings.highContrastHint')} checked={prefs.contrast} onCheckedChange={(v) => setPref('contrast', v)} />
          </CardBody>
        </Card>

        <Card aria-labelledby="set-dev">
          <CardHeader>
            <SectionTitle id="set-dev" icon={MonitorCog}>
              {t('settings.devTitle')}
            </SectionTitle>
          </CardHeader>
          <CardBody className="py-1">
            <SwitchField label={core('settings.devMode')} hint={core('settings.devModeHint')} checked={mode === 'dev'} onCheckedChange={(v) => switchMode(v ? 'dev' : 'user')} />
          </CardBody>
        </Card>

        {/* demo-proof features (D-24): renders nothing unless the server has them on */}
        <DemoFeaturesSetting />

        <Card aria-labelledby="set-tour">
          <CardHeader>
            <SectionTitle id="set-tour" icon={Compass}>
              {core('settings.replayTour')}
            </SectionTitle>
            <CardDescription>{t('settings.tourHint')}</CardDescription>
          </CardHeader>
          <CardBody>
            <Button icon={Compass} onClick={() => startTour(0)}>
              {core('settings.replayTour')}
            </Button>
          </CardBody>
        </Card>

        <Card aria-labelledby="set-privacy">
          <CardHeader>
            <SectionTitle id="set-privacy" icon={Lock}>
              {t('settings.privacyTitle')}
            </SectionTitle>
          </CardHeader>
          <CardBody>
            <ul className="space-y-2.5 text-body-sm text-fg" data-testid="privacy-notice">
              {(['settings.p1', 'settings.p2', 'settings.p3', 'settings.p4', 'settings.p5'] as const).map((k) => (
                <li key={k} className="flex items-start gap-2.5">
                  <span aria-hidden className="mt-[0.6em] h-1 w-1 shrink-0 rounded-full bg-subtle" />
                  <span>{t(k)}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>

        <Card aria-labelledby="set-reset">
          <CardHeader>
            <SectionTitle id="set-reset" icon={RotateCcw}>
              {t('settings.resetTitle')}
            </SectionTitle>
            <CardDescription>{t('settings.resetHint')}</CardDescription>
          </CardHeader>
          <CardBody>
            <Dialog open={confirm} onOpenChange={setConfirm}>
              <Button variant="danger" icon={RotateCcw} onClick={() => setConfirm(true)} data-testid="reset-local">
                {t('settings.resetButton')}
              </Button>
              <DialogContent
                size="sm"
                title={t('settings.resetConfirmTitle')}
                description={t('settings.resetConfirmBody')}
                closeLabel={core('common.close')}
                footer={
                  <>
                    <DialogClose asChild>
                      <Button>{t('settings.resetCancel')}</Button>
                    </DialogClose>
                    <Button variant="danger" onClick={reset} data-testid="reset-confirm">
                      {t('settings.resetConfirm')}
                    </Button>
                  </>
                }
              >
                <p className="text-body-sm text-muted">{t('settings.resetHint')}</p>
              </DialogContent>
            </Dialog>
          </CardBody>
        </Card>
      </div>
      <Toaster />
    </Page>
  );
}
