'use client';
// /dev/brain: the Walnut evidence engine underneath DataBridge, read-only, through /api/walnut.
// Four views over one snapshot: the knowledge graph, the company brain with its time slider, the
// provenance of a value down to its row, and the ontology.
import { Camera, Database, Eye, FlaskConical } from 'lucide-react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useMemo } from 'react';
import { Badge, SkeletonText, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui';
import { Page } from '@/components/shell/Page';
import { useSession } from '@/components/shell/Providers';
import { useMessages } from '@/lib/i18n';
import { engineMessages } from '@/lib/i18n/engine';
import {
  DEFAULT_PROVENANCE_ENTITY,
  ENGINE_PERSONA,
  ENGINE_PERSONA_NAME,
  engineApi,
  formatDate,
  isMockEngine,
  type GraphFacets,
  type GraphNeighborhood,
  type PackRegistry,
} from '@/lib/engine-api';
import { CompanyBrainTab } from './CompanyBrainTab';
import { KnowledgeGraphTab } from './KnowledgeGraphTab';
import { OntologyTab } from './OntologyTab';
import { buildPalette } from './palette';
import { EngineFailure, EngineLock } from './parts';
import { ProvenanceTab } from './ProvenanceTab';
import { useEngine } from './use-engine';

type Tab = 'graph' | 'brain' | 'provenance' | 'ontology';
const TABS: Tab[] = ['graph', 'brain', 'provenance', 'ontology'];
const ID_OK = /^[A-Za-z0-9_.:|-]{1,200}$/;

function SnapshotBanner({ hubs, facets }: { hubs: GraphNeighborhood | null; facets: GraphFacets | null }) {
  const t = useMessages(engineMessages);
  // The sync instant the build stamped on the graph (system_from: when the engine learned it).
  const built = useMemo(() => {
    const stamps = (hubs?.nodes ?? []).map((n) => n.system_from).filter((s): s is string => !!s);
    return stamps.length ? stamps.reduce((a, b) => (a > b ? a : b)) : null;
  }, [hubs]);
  return (
    <div
      role="note"
      aria-label="About this snapshot"
      className="mb-4 flex flex-col gap-2 rounded-lg border border-line bg-surface px-3 py-2.5 sm:flex-row sm:items-start sm:justify-between"
      data-testid="engine-snapshot-banner"
    >
      <div className="flex min-w-0 items-start gap-2.5">
        <Camera aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        <div className="min-w-0">
          <p className="text-body-sm font-medium text-fg">
            {t('engine.banner.title', { name: ENGINE_PERSONA_NAME, date: built ? formatDate(built) : t('engine.banner.dateUnknown') })}
          </p>
          <p className="text-caption text-muted">{t('engine.banner.body')}</p>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 sm:justify-end">
        {facets ? (
          <span className="mr-1 text-caption text-muted">
            <span className="tabular-nums text-fg">{facets.counts.nodes.toLocaleString('en-SG')}</span> entities ·{' '}
            <span className="tabular-nums text-fg">{facets.counts.edges.toLocaleString('en-SG')}</span> links ·{' '}
            <span className="tabular-nums text-fg">{facets.counts.sources.toLocaleString('en-SG')}</span> pointers
          </span>
        ) : null}
        <Badge icon={Eye}>{t('engine.banner.readOnly')}</Badge>
        {isMockEngine ? (
          <Badge tone="warn" icon={FlaskConical}>
            {t('engine.banner.mock')}
          </Badge>
        ) : null}
      </div>
    </div>
  );
}

function Engine() {
  const t = useMessages(engineMessages);
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const rawTab = params.get('tab') as Tab | null;
  const tab: Tab = rawTab && TABS.includes(rawTab) ? rawTab : 'graph';
  const rawEntity = params.get('entity');
  const entity = rawEntity && ID_OK.test(rawEntity) ? rawEntity : DEFAULT_PROVENANCE_ENTITY;
  const rawFocus = params.get('focus');
  const focus = rawFocus && ID_OK.test(rawFocus) ? rawFocus : null;

  const go = useCallback(
    (next: Partial<{ tab: Tab; entity: string; focus: string }>) => {
      const sp = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(next)) if (v) sp.set(k, v);
      router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
    },
    [params, pathname, router],
  );

  const facets = useEngine<GraphFacets>('facets', (s) => engineApi.facets(s));
  const packs = useEngine<PackRegistry>('packs', (s) => engineApi.packs(s));
  const hubs = useEngine<GraphNeighborhood>('hubs', (s) => engineApi.hubs(60, s));
  const palette = useMemo(
    () => buildPalette(packs.data, facets.data?.node_kinds.map((k) => k.kind) ?? []),
    [packs.data, facets.data],
  );

  const header = (
    <>
      <SnapshotBanner hubs={hubs.data} facets={facets.data} />
    </>
  );

  const first = facets.error ?? hubs.error;
  if (first?.failure === 'locked') {
    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
        <EngineLock />
      </div>
    );
  }

  return (
    <Page eyebrow={t('engine.eyebrow')} title={t('engine.title')} description={t('engine.description')} width="wide">
      {first ? (
        <EngineFailure
          error={first}
          onRetry={() => {
            facets.reload();
            hubs.reload();
            packs.reload();
          }}
        />
      ) : facets.loading || hubs.loading || !hubs.data ? (
        <SkeletonText lines={6} label={t('engine.loading')} />
      ) : (
        <>
          {header}
          <Tabs value={tab} onValueChange={(v) => go({ tab: v as Tab })}>
            <TabsList aria-label={t('engine.tabs.label')} className="mb-1">
              <TabsTrigger value="graph" data-testid="engine-tab-graph">{t('engine.tab.graph')}</TabsTrigger>
              <TabsTrigger value="brain" data-testid="engine-tab-brain">{t('engine.tab.brain')}</TabsTrigger>
              <TabsTrigger value="provenance" data-testid="engine-tab-provenance">{t('engine.tab.provenance')}</TabsTrigger>
              <TabsTrigger value="ontology" data-testid="engine-tab-ontology">{t('engine.tab.ontology')}</TabsTrigger>
            </TabsList>
            <TabsContent value="graph">
              <KnowledgeGraphTab
                hubs={hubs.data}
                facets={facets.data}
                palette={palette}
                onTrace={(id) => go({ tab: 'provenance', entity: id })}
                onOpenBrain={(id) => go({ tab: 'brain', focus: id })}
              />
            </TabsContent>
            <TabsContent value="brain">
              <CompanyBrainTab palette={palette} focusId={focus} />
            </TabsContent>
            <TabsContent value="provenance">
              <ProvenanceTab entity={entity} onEntity={(id) => go({ tab: 'provenance', entity: id })} palette={palette} />
            </TabsContent>
            <TabsContent value="ontology">
              <OntologyTab palette={palette} />
            </TabsContent>
          </Tabs>
          <p className="mt-6 flex items-center gap-1.5 text-caption text-subtle">
            <Database aria-hidden className="h-3.5 w-3.5" />
            Served by the Walnut gateway on this machine through a read-only, allowlisted proxy. No question is asked of the engine here: answers go through the Internal Brain.
          </p>
        </>
      )}
    </Page>
  );
}

/** Lock card first for personas the snapshot was not built for; the proxy enforces the same rule (403). */
export function EngineView() {
  const { me } = useSession();
  if (!me) return null;
  const allowed = me.sub === ENGINE_PERSONA || me.roles.includes('compliance_officer');
  if (!allowed) {
    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
        <EngineLock />
      </div>
    );
  }
  return <Engine />;
}
