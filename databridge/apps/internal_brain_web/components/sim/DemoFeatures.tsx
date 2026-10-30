'use client';
// The Settings card for the demo-proof features (D-24). It renders NOTHING unless the server says
// demo features are on (IB_DEMO_FEATURES=1 and not a deployment), so normal users never see it.
import { FlaskConical } from 'lucide-react';
import { Badge, Card, CardBody, CardDescription, CardHeader, CardTitle, SwitchField } from '@/components/ui';
import { useMessages } from '@/lib/i18n';
import { graphMessages } from '@/lib/i18n/graph';
import { setDemoLocal, useDemoFeatures } from './demo';

export function DemoFeaturesSetting() {
  const t = useMessages(graphMessages);
  const demo = useDemoFeatures();
  if (demo.server !== true) return null;
  return (
    <Card aria-labelledby="set-demo" data-testid="settings-demo">
      <CardHeader>
        <CardTitle id="set-demo" className="flex items-center gap-2">
          <FlaskConical aria-hidden className="h-4 w-4 text-subtle" />
          {t('demo.settingTitle')}
          <Badge tone="warn">{t('demo.badge')}</Badge>
        </CardTitle>
        <CardDescription>{t('demo.settingHint')}</CardDescription>
      </CardHeader>
      <CardBody className="py-1">
        <SwitchField label={t('demo.settingLabel')} checked={demo.local} onCheckedChange={(v) => setDemoLocal(v)} id="settings-demo-switch" />
      </CardBody>
    </Card>
  );
}
