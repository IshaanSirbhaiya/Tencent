'use client';
// /dev/trust: the Responsible-AI evidence pack (docs/finals/rubric/responsible_ai) as an in-app
// overview. Static, self-assessed content; every claim names the test or eval that proves it.
import {
  ArrowRight,
  BadgeCheck,
  ExternalLink,
  EyeOff,
  FileText,
  Filter,
  Fingerprint,
  KeyRound,
  Link2,
  ScanSearch,
  ShieldQuestion,
  Shapes,
  type LucideIcon,
} from 'lucide-react';
import Link from 'next/link';
import { Page } from '@/components/shell/Page';
import { Badge, Pill } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { trustMessages } from '@/lib/i18n/audit';
import { CONTROLS, DOCS, FRAMEWORKS, NEVER, OWASP, RAI_DIR, type ControlIcon, type Status } from './data';

const ICONS: Record<ControlIcon, LucideIcon> = {
  filter: Filter,
  key: KeyRound,
  ledger: Link2,
  shape: Shapes,
  ground: ScanSearch,
  reveal: Fingerprint,
  mask: EyeOff,
};

const STATUS_TONE: Record<Status, 'ok' | 'warn' | 'neutral' | 'locked'> = { met: 'ok', partial: 'warn', gap: 'neutral', 'n/a': 'locked' };
const STATUS_LABEL: Record<Status, string> = { met: 'Met', partial: 'Partial', gap: 'Gap', 'n/a': 'n/a' };

function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="text-micro font-semibold uppercase tracking-wide text-subtle">{children}</p>;
}

function SectionTitle({ id, title, sub }: { id: string; title: string; sub?: string }) {
  return (
    <div className="mb-4 max-w-3xl">
      <h2 id={id} className="text-heading text-fg">
        {title}
      </h2>
      {sub ? <p className="mt-1 text-body-sm text-muted">{sub}</p> : null}
    </div>
  );
}

/** Proportional bar of the hand-copied counts (decorative; the numbers sit next to it). */
function StatusBar({ met, partial, gap, na = 0 }: { met: number; partial: number; gap: number; na?: number }) {
  const total = met + partial + gap + na;
  const seg = (n: number, cls: string) => (n ? <span className={cn('h-full', cls)} style={{ width: `${(n / total) * 100}%` }} /> : null);
  return (
    <span aria-hidden className="flex h-2 w-full min-w-[6rem] overflow-hidden rounded-full bg-line">
      {seg(met, 'bg-ok')}
      {seg(partial, 'bg-warn')}
      {seg(gap, 'bg-line-strong')}
      {seg(na, 'bg-raised')}
    </span>
  );
}

const docHref = (file: string) => `${RAI_DIR}/${file}`;

export function TrustCentre() {
  const t = useMessages(trustMessages);
  return (
    <Page
      eyebrow={t('trust.eyebrow')}
      title={t('trust.title')}
      description={t('trust.description')}
      width="wide"
      actions={
        <div className="flex flex-wrap gap-2">
          <Pill icon={ShieldQuestion} tone="neutral">
            {t('trust.selfAssessed')}
          </Pill>
          <Pill tone="neutral">Synthetic data only</Pill>
        </div>
      }
    >
      <div className="space-y-12">
        {/* 1. Controls */}
        <section aria-labelledby="controls-h">
          <SectionTitle id="controls-h" title={t('trust.controls')} sub={t('trust.controlsSub')} />
          <ol className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="trust-controls" data-tour="trust-controls">
            {CONTROLS.map((c, i) => {
              const Icon = ICONS[c.icon];
              return (
                <li key={c.id} className="flex flex-col rounded-xl border border-line bg-surface" data-testid={`control-${c.id}`}>
                  <div className="flex items-start gap-3 px-4 pt-4">
                    <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
                      <Icon aria-hidden className="h-[18px] w-[18px]" />
                    </span>
                    <div className="min-w-0">
                      <p className="font-mono text-2xs text-subtle tabular-nums">C{String(i + 1).padStart(2, '0')}</p>
                      <h3 className="text-heading-sm text-fg">{c.title}</h3>
                    </div>
                  </div>
                  <div className="flex flex-1 flex-col gap-3 px-4 pb-4 pt-3">
                    <div>
                      <Eyebrow>{t('trust.what')}</Eyebrow>
                      <p className="mt-1 text-body-sm text-fg">{c.what}</p>
                    </div>
                    <div>
                      <Eyebrow>{t('trust.why')}</Eyebrow>
                      <p className="mt-1 text-body-sm text-muted">{c.why}</p>
                      <ul className="mt-2 flex flex-wrap gap-1" aria-label="Mapped to">
                        {c.maps.map((m) => (
                          <li key={m}>
                            <Badge tone="neutral">{m}</Badge>
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <Eyebrow>{t('trust.proof')}</Eyebrow>
                      <ul className="mt-1 space-y-1">
                        {c.proof.map((p) => (
                          <li key={p} className="flex gap-1.5 text-2xs">
                            <BadgeCheck aria-hidden className="mt-px h-3.5 w-3.5 shrink-0 text-ok" />
                            <code className="break-all font-mono text-muted">{p}</code>
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div className="mt-auto border-t border-line pt-3">
                      <Link href={c.live.href} className="inline-flex min-h-[1.5rem] items-center gap-1 text-body-sm font-medium text-accent underline-offset-2 hover:underline">
                        {t('trust.live')}: {c.live.label}
                        <ArrowRight aria-hidden className="h-3.5 w-3.5" />
                      </Link>
                      {c.live.note ? <p className="mt-0.5 text-2xs text-subtle">{c.live.note}</p> : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        </section>

        {/* 2. Crosswalk */}
        <section aria-labelledby="crosswalk-h">
          <SectionTitle id="crosswalk-h" title={t('trust.crosswalk')} sub={t('trust.crosswalkSub')} />
          <div className="relative overflow-x-auto rounded-xl border border-line bg-surface" role="region" aria-labelledby="crosswalk-cap" tabIndex={0}>
            <table className="w-full min-w-[40rem] border-collapse text-left text-body-sm" data-testid="crosswalk">
              <caption id="crosswalk-cap" className="sr-only">Framework crosswalk: how many items of each framework are met, partial or a gap (self-assessed, 30 Oct 2026).</caption>
              <thead>
                <tr className="border-b border-line text-micro uppercase tracking-wide text-subtle">
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Framework
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Met
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Partial
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">
                    Gap
                  </th>
                  <th scope="col" className="w-[30%] px-4 py-2 font-semibold">
                    <span className="sr-only">Proportion</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {FRAMEWORKS.map((f) => (
                  <tr key={f.id} className="border-b border-line align-top last:border-b-0" data-testid={`fw-${f.id}`}>
                    <th scope="row" className="px-4 py-3 font-normal">
                      <div className="font-medium text-fg">{f.name}</div>
                      <div className="text-caption text-muted">
                        {f.scope}
                        {f.na ? ` · ${f.na} not applicable` : ''} · FRAMEWORK_CROSSWALK.md {f.section}
                      </div>
                      <details className="group mt-1.5">
                        <summary className="inline-flex min-h-[1.5rem] cursor-pointer items-center text-caption text-accent">Show each item</summary>
                        <ul className="mt-2 grid gap-1 sm:grid-cols-2">
                          {f.items.map((it) => (
                            <li key={it.name} className="flex items-start justify-between gap-2 rounded-md border border-line px-2 py-1 text-caption">
                              <span className="text-fg">{it.name}</span>
                              <Badge tone={STATUS_TONE[it.status]}>{STATUS_LABEL[it.status]}</Badge>
                            </li>
                          ))}
                        </ul>
                      </details>
                    </th>
                    <td className="px-3 py-3 text-right text-heading-sm tabular-nums text-ok" data-col="met">
                      {f.met}
                    </td>
                    <td className="px-3 py-3 text-right text-heading-sm tabular-nums text-warn" data-col="partial">
                      {f.partial}
                    </td>
                    <td className="px-3 py-3 text-right text-heading-sm tabular-nums text-muted" data-col="gap">
                      {f.gap}
                    </td>
                    <td className="px-4 py-4">
                      <StatusBar met={f.met} partial={f.partial} gap={f.gap} na={f.na} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-caption text-subtle">
            ISO/IEC 42001, NIST AI RMF and the EU AI Act are in the full crosswalk. We hold no AI management system and claim nothing against ISO/IEC 42001 as a whole.
          </p>

          <div className="mt-8">
            <h3 className="text-heading-sm text-fg">{t('trust.owasp')}</h3>
            <p className="mt-1 text-body-sm text-muted">{t('trust.owaspSub')}</p>
            <div className="relative mt-3 overflow-x-auto rounded-xl border border-line bg-surface" role="region" aria-labelledby="owasp-cap" tabIndex={0}>
              <table className="w-full min-w-[36rem] border-collapse text-left text-body-sm" data-testid="owasp">
                <caption id="owasp-cap" className="sr-only">OWASP Top 10 for LLM applications: our main control and the residual risk, from OWASP_LLM_REDTEAM.md.</caption>
                <thead>
                  <tr className="border-b border-line text-micro uppercase tracking-wide text-subtle">
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Risk
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      Main control
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Residual
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {OWASP.map((o) => (
                    <tr key={o.id} className="border-b border-line last:border-b-0">
                      <th scope="row" className="px-4 py-2 font-normal">
                        <span className="mr-2 font-mono text-2xs text-subtle">{o.id}</span>
                        <span className="text-fg">{o.risk}</span>
                      </th>
                      <td className="px-3 py-2 text-muted">{o.control}</td>
                      <td className="px-4 py-2">
                        <Badge tone={o.residual === 'Medium' ? 'warn' : 'neutral'}>{o.residual}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* 3. Claims we never make */}
        <section aria-labelledby="never-h">
          <SectionTitle id="never-h" title={t('trust.never')} sub={t('trust.neverSub')} />
          <ul className="divide-y divide-line rounded-xl border border-line bg-surface" data-testid="never-claims">
            {NEVER.map((n) => (
              <li key={n.never} className="grid gap-x-6 gap-y-1 px-4 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1fr)]">
                <p className="text-body-sm text-muted">
                  <span className="sr-only">Never say: </span>
                  <s className="decoration-line-strong">{n.never}</s>
                </p>
                <p className="text-body-sm font-medium text-fg">
                  <span className="sr-only">Say instead: </span>
                  {n.instead}
                </p>
                <p className="text-caption text-subtle">
                  <span className="sr-only">Why: </span>
                  {n.why}
                </p>
              </li>
            ))}
          </ul>
        </section>

        {/* 4. Docs */}
        <section aria-labelledby="docs-h">
          <SectionTitle id="docs-h" title={t('trust.docs')} sub={t('trust.docsSub')} />
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {DOCS.map((d) => (
              <li key={d.file}>
                <a
                  href={docHref(d.file)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group flex h-full items-start gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 hover:border-accent/50 hover:bg-raised/50"
                >
                  <FileText aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1 font-mono text-xs text-fg">
                      {d.file}
                      <ExternalLink aria-hidden className="h-3 w-3 text-subtle" />
                      <span className="sr-only">(opens GitHub in a new tab)</span>
                    </span>
                    <span className="mt-0.5 block text-caption text-muted">{d.what}</span>
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </Page>
  );
}
