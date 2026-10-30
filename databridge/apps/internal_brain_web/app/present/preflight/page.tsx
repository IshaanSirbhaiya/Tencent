import { readFile } from 'node:fs/promises';
import path from 'node:path';
import type { Metadata } from 'next';
import { cookies, headers } from 'next/headers';
import { notFound } from 'next/navigation';
import { NextRequest } from 'next/server';
import { PreflightBoard, type ServerCheck, type ScriptReport } from '@/components/preflight/PreflightBoard';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Walnut DataBridge · Preflight' };

// Demo Day preflight (docs/finals/demo_day/DEMO_DAY_RUNBOOK.md). Read-only: the server side probes
// the local services' /health and reads the last report of scripts/demo_preflight.py (which the
// one-click Start runs, with the pre-warm asks); the board adds the browser-side checks (screen,
// zoom, clock, clips, live model status). Nothing here writes anywhere. Not served when deployed;
// locally only on the loopback address to a signed-in person (see allowed() below).

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

/** The Host the browser used is this machine's loopback name (the stage laptop opening its own board). */
function loopbackHost(): boolean {
  const host = (headers().get('host') ?? '').trim().toLowerCase();
  const name = host.startsWith('[') ? host.slice(0, host.indexOf(']') + 1) : host.split(':')[0];
  return name === 'localhost' || name === '127.0.0.1' || name === '[::1]';
}

/**
 * A valid Internal Brain session, per the IB itself (GET /auth/whoami with the ib_session cookie, as
 * /api/walnut does). A cookie that is merely present is not enough. Any error or timeout means no.
 */
async function validSession(): Promise<boolean> {
  const session = cookies().get('ib_session')?.value;
  if (!session) return false;
  try {
    let status: number;
    let body: unknown;
    if (MOCK) {
      const { handleMock } = await import('@/lib/mock/server');
      const r = await handleMock(new NextRequest('http://127.0.0.1/api/ib/auth/whoami', { method: 'GET' }), 'auth/whoami', session);
      status = r.status;
      body = await r.json().catch(() => null);
    } else {
      const base = (process.env.IB_API_URL || 'http://127.0.0.1:8100').replace(/\/+$/, '');
      const r = await fetch(`${base}/auth/whoami`, {
        method: 'GET',
        headers: { accept: 'application/json', cookie: `ib_session=${session}` },
        cache: 'no-store',
        redirect: 'manual',
        signal: AbortSignal.timeout(5000),
      });
      status = r.status;
      body = await r.json().catch(() => null);
    }
    return status === 200 && !!body && typeof body === 'object' && typeof (body as { sub?: unknown }).sub === 'string';
  } catch {
    return false;
  }
}

/**
 * Who may see the board (it shows service health, ledger counts and per-persona citation counts):
 * never on a deployment (IB_DEPLOYED=1, the same switch as /api/walnut; deploy/lighthouse sets it on
 * the web service). Otherwise only the stage laptop on its own loopback address (DNS rebinding) AND
 * with a session the IB confirms. Everything else gets the one 404.
 */
async function allowed(): Promise<boolean> {
  if (process.env.IB_DEPLOYED === '1') return false;
  if (!loopbackHost()) return false;
  return validSession();
}

export default async function PreflightPage() {
  if (!(await allowed())) notFound();
  const services: ServerCheck[] = MOCK
    ? [{ key: 'mode', label: 'Offline mock build: no services needed', status: 'info', detail: 'answers are recorded fixtures (say so on stage)' }]
    : await Promise.all([
        probe('ib', 'Internal Brain :8100', 'http://127.0.0.1:8100/health', true),
        probe('mock', 'Mock platforms :8200 (Confluence, Jira, Slack, Drive)', 'http://127.0.0.1:8200/health', true),
        probe('engine', 'Walnut engine gateway :8300 (Developer > Brain only)', 'http://127.0.0.1:8300/health', false),
      ]);
  return <PreflightBoard services={services} report={await readReport()} serverNow={Date.now()} mock={MOCK} />;
}
