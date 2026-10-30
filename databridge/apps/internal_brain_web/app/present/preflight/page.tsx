import { readFile } from 'node:fs/promises';
import path from 'node:path';
import type { Metadata } from 'next';
import { PreflightBoard, type ServerCheck, type ScriptReport } from '@/components/preflight/PreflightBoard';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Walnut DataBridge · Preflight' };

// Demo Day preflight (docs/finals/demo_day/DEMO_DAY_RUNBOOK.md). Read-only: the server side probes
// the local services' /health and reads the last report of scripts/demo_preflight.py (which the
// one-click Start runs, with the pre-warm asks); the board adds the browser-side checks (screen,
// zoom, clock, clips, live model status). Nothing here writes anywhere.

const MOCK = process.env.NEXT_PUBLIC_IB_MOCK === '1' || process.env.IB_MOCK === '1';

/** databridge/var/demo-day/preflight.json (the web app runs from databridge/apps/internal_brain_web). */
function reportPath(): string {
  return process.env.DEMO_PREFLIGHT_JSON || path.resolve(process.cwd(), '..', '..', 'var', 'demo-day', 'preflight.json');
}

async function readReport(): Promise<ScriptReport | null> {
  try {
    const raw = await readFile(reportPath(), 'utf8');
    const r = JSON.parse(raw) as ScriptReport;
    return r && Array.isArray(r.items) ? r : null;
  } catch {
    return null;
  }
}

async function probe(key: string, label: string, url: string, required: boolean): Promise<ServerCheck> {
  const t0 = Date.now();
  try {
    const r = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(2500) });
    const ms = Date.now() - t0;
    return { key, label, status: r.ok ? 'pass' : required ? 'fail' : 'warn', detail: `HTTP ${r.status}, ${ms} ms` };
  } catch (e) {
    return { key, label, status: required ? 'fail' : 'warn', detail: e instanceof Error && e.name === 'TimeoutError' ? 'no answer in 2.5 s' : 'not running' };
  }
}

export default async function PreflightPage() {
  const services: ServerCheck[] = MOCK
    ? [{ key: 'mode', label: 'Offline mock build: no services needed', status: 'info', detail: 'answers are recorded fixtures (say so on stage)' }]
    : await Promise.all([
        probe('ib', 'Internal Brain :8100', 'http://127.0.0.1:8100/health', true),
        probe('mock', 'Mock platforms :8200 (Confluence, Jira, Slack, Drive)', 'http://127.0.0.1:8200/health', true),
        probe('engine', 'Walnut engine gateway :8300 (Developer > Brain only)', 'http://127.0.0.1:8300/health', false),
      ]);
  return <PreflightBoard services={services} report={await readReport()} serverNow={Date.now()} mock={MOCK} />;
}
