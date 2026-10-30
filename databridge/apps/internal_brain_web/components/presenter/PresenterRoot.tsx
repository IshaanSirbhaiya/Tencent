'use client';
// Mount point for Presenter mode. Renders nothing unless presenter mode is armed.
//
// Designed to be mounted ONCE in app/layout.tsx (inside <Providers>) so the bar shows on every page.
// Until then, /compare and /present/* mount it themselves; when several are mounted, only the
// first one renders (the others stay silent), and the keys live on `window` (controller.ts), so they
// keep working on pages that do not render the bar.
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useSession } from '@/components/shell';
import { useLang } from '@/lib/i18n';
import { ClipOverlay } from './ClipOverlay';
import { DeliverOverlay } from './DeliverOverlay';
import { installPresenterKeys, setArmed, setHost, setStage } from './controller';
import { PresenterBar, PresenterShowHandle } from './PresenterBar';
import { ResetConfirm } from './ResetConfirm';
import { applyStage } from './stage';
import { getPresenter, setPresenter, usePresenter } from './store';
import { WorkBuddyCard } from './WorkBuddyCard';
import { growOptIn, setGrowSteps, STEPS } from './script';
import { useDemoFeatures } from '@/components/sim/demo';

/** Pages the script itself drives; the bar stays there after the last step. */
const PRESENTER_PAGE = /^\/(present(\/|$)|compare$|dev\/journey(\/|$)|dev\/audit$)/;

/* ---- one renderer at a time ---- */
let seq = 0;
const mounted: number[] = [];
const ownerListeners = new Set<() => void>();
const notifyOwners = () => ownerListeners.forEach((l) => l());

function useIsOwner(): boolean {
  const [id] = useState(() => ++seq);
  useEffect(() => {
    mounted.push(id);
    notifyOwners();
    return () => {
      const i = mounted.indexOf(id);
      if (i >= 0) mounted.splice(i, 1);
      notifyOwners();
    };
  }, [id]);
  return useSyncExternalStore(
    (l) => {
      ownerListeners.add(l);
      return () => ownerListeners.delete(l);
    },
    () => mounted[0] === id,
    () => false,
  );
}

export function PresenterRoot() {
  const router = useRouter();
  const pathname = usePathname();
  const { refresh } = useSession();
  const { lang, setLang } = useLang();
  const s = usePresenter();
  const owner = useIsOwner();
  // The live-growth steps exist only while demo features are on (D-24) AND this tab opted in with
  // ?grow=1 (round-2 review #1): never on the default Space path.
  const demo = useDemoFeatures();
  useEffect(() => {
    // read ?grow=1 on every page change, even before demo features are known (a signed-out tab):
    // the opt-in is stored for this tab, so the steps appear once the sign-in makes demo.on true
    const opted = growOptIn();
    if (setGrowSteps(demo.on && opted)) setPresenter((p) => ({ step: Math.min(p.step, STEPS.length - 1) }));
  }, [demo.on, pathname]);
  const langRef = useRef(lang);
  langRef.current = lang;

  // Hand the router, session and language to the module-level controller, and install the keys.
  useEffect(() => {
    setHost({
      push: (url) => router.push(url),
      refreshSession: refresh,
      toggleLang: () => setLang(langRef.current === 'en' ? 'zh' : 'en'),
    });
    installPresenterKeys();
  }, [router, refresh, setLang]);

  // ?present=1 arms presenter mode; ?stage=1 turns the Stage theme on. Before P, ?present=1 lands
  // on the title slide in Stage (what the room sees first), not on whatever page was opened.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get('present') === '1') {
      if (!s.armed) setArmed(true);
      if (!getPresenter().started && !pathname?.startsWith('/present')) {
        setStage(true);
        router.replace('/present');
        return;
      }
    }
    if (q.get('stage') === '1' && !s.stage) setStage(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // After a full reload the stored Stage choice is re-applied to <html>.
  useEffect(() => {
    if (s.stage) applyStage(true);
  }, [s.stage]);

  // After the script, off its pages, the bar is out of the way; the handle brings it back on this
  // page (so the mouse can still reach Back, Reset or Exit), until the next page change.
  const [peek, setPeek] = useState(false);
  useEffect(() => setPeek(false), [pathname]);

  if (!owner || !s.armed) return null;
  const step = s.started ? STEPS[s.step] : null;
  // The script is over: off the presenter's own pages (login, graph, Q&A) the bar gets out of the way.
  const finished = s.started && s.step === STEPS.length - 1 && !s.busy;
  if (finished && !PRESENTER_PAGE.test(pathname ?? '') && !peek)
    return (
      <>
        <PresenterShowHandle
          onShow={() => {
            setPresenter({ hidden: false });
            setPeek(true);
          }}
        />
        <ClipOverlay />
        <ResetConfirm />
        <DeliverOverlay />
      </>
    );
  return (
    <>
      {step?.offApp && !s.hidden ? <WorkBuddyCard /> : null}
      {!s.hidden ? <PresenterBar /> : <PresenterShowHandle />}
      <ClipOverlay />
      <ResetConfirm />
      <DeliverOverlay />
    </>
  );
}
