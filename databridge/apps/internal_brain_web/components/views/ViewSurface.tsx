'use client';
// Renders one just-in-time view: its A2UI v0.9 messages through @a2ui/react with the Walnut catalog
// only. A message that names another catalog or an unknown component is not rendered (the
// processor refuses it); a render error shows one sentence, never a stack. Browser only: A2UI's
// core defines custom elements when it loads, so import this file through next/dynamic with
// ssr:false (ViewRow does).
import { A2uiSurface } from '@a2ui/react/v0_9';
import { MessageProcessor } from '@a2ui/web_core/v0_9';
import { Component, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import { useLang, useMessages } from '@/lib/i18n';
import { deliverMessages } from '@/lib/i18n/deliver';
import { viewMessages } from '@/lib/i18n/view';
import type { AskResponse, JitView } from '@/lib/types';
import { CATALOG_ID, ViewEnvContext, walnutCatalog, type ViewEnv } from './catalog';
import { viewNumbering } from './numbering';

class Boundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

type Surface = Parameters<typeof A2uiSurface>[0]['surface'];

export function ViewSurface({ response, view }: { response: AskResponse; view: JitView }) {
  const t = useMessages(viewMessages);
  const td = useMessages(deliverMessages);
  const { lang } = useLang();
  const listId = useId();
  const num = useMemo(() => viewNumbering(response, view), [response, view]);
  const [state, setState] = useState<{ surface: Surface | null; error: boolean }>({ surface: null, error: false });

  useEffect(() => {
    let processor: MessageProcessor | null = null;
    try {
      const create = view.a2ui.find((m) => 'createSurface' in m) as { createSurface?: { catalogId?: string } } | undefined;
      if (create?.createSurface?.catalogId !== CATALOG_ID) throw new Error('catalog');
      processor = new MessageProcessor([walnutCatalog()]);
      processor.processMessages(view.a2ui as never);
      const surface = processor.model.surfacesMap.get(view.view_id) ?? null;
      setState({ surface: surface as Surface | null, error: !surface });
    } catch {
      setState({ surface: null, error: true });
    }
    return () => processor?.dispose();
  }, [view]);

  const env: ViewEnv = {
    num,
    lang,
    t,
    td,
    listId,
    onJump: (n) => {
      const el = document.getElementById(listId)?.querySelector<HTMLElement>(`[data-ns~="${n}"]`);
      if (!el) return;
      el.scrollIntoView({ block: 'nearest' });
      el.focus();
    },
  };
  const failed = (
    <p role="alert" className="py-6 text-center text-body-sm text-muted" data-testid="view-render-error">
      {t('view.render')}
    </p>
  );
  if (state.error) return failed;
  if (!state.surface) return null;
  return (
    <ViewEnvContext.Provider value={env}>
      <Boundary fallback={failed}>
        <div data-testid="view-surface" data-view={view.view_id} data-layout={view.layout.join(' ')}>
          <A2uiSurface surface={state.surface} />
        </div>
      </Boundary>
    </ViewEnvContext.Provider>
  );
}
