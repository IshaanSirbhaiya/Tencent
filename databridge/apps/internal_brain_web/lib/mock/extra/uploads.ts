import { NextResponse } from 'next/server';
import type { UploadFolder } from '../../types';
import type { MockHandler } from './types';

// GET /sim/upload-folders (D-34) in mock mode: ONLY the folders the signed-in persona may upload
// into, by name, sorted by path. Mirrors the synthetic world's Drive grants (fixtures/enterprise/
// world.json: a folder's WRITE grants, the shared drives' members are readers); admin_it may upload
// into any folder. A persona with no WRITE anywhere gets [] (the same bytes for everyone).
const FOLDERS: { id: string; path: string; writers: string[] }[] = [
  { id: 'fd-1', path: 'Postmortems', writers: [] },
  { id: 'fd-2', path: 'Architecture', writers: [] },
  { id: 'fd-3', path: 'DB Migration', writers: [] },
  { id: 'fd-4', path: 'SRE Ops', writers: [] },
  { id: 'fd-5', path: 'Security', writers: ['grp-security'] },
  { id: 'fd-6', path: 'Pricing & Offers', writers: ['grp-product'] },
  { id: 'fd-7', path: 'Vendor share', writers: ['grp-product'] },
  { id: 'fd-8', path: 'Company policies', writers: [] },
  { id: 'fd-9', path: 'Finance', writers: ['grp-finance'] },
  { id: 'fd-10', path: 'Marketing', writers: [] },
  { id: 'fd-11', path: 'Compliance evidence', writers: ['grp-compliance'] },
  { id: 'fd-12', path: 'FinCrime case files', writers: ['grp-fincrime'] },
  { id: 'fd-13', path: 'People', writers: ['grp-people'] },
  { id: 'fd-14', path: 'People / Compensation (People leads only)', writers: ['grp-people'] },
];

/** The mock personas' Drive groups (world.json directory). */
const GROUPS: Record<string, string[]> = {
  jdoe: ['grp-eng', 'grp-payments-eng', 'grp-staff'],
  'priya.sre': ['grp-eng', 'grp-sre', 'grp-staff'],
  'ken.ext': [],
  'sara.sec': ['grp-eng', 'grp-security', 'grp-staff'],
  'maya.compliance': ['grp-compliance', 'grp-staff'],
  'leo.ops': ['grp-product', 'grp-staff'],
  'amy.am': ['grp-accounts', 'grp-staff'],
  'mlro.tan': ['grp-fincrime', 'grp-staff'],
};

const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });

export function mockUploadFolders(sub: string, roles: string[]): UploadFolder[] {
  const admin = roles.includes('admin_it');
  const groups = new Set(GROUPS[sub] ?? []);
  return FOLDERS.filter((f) => admin || f.writers.some((g) => groups.has(g)))
    .map(({ id, path }) => ({ id, path }))
    .sort((a, b) => a.path.localeCompare(b.path, 'en') || a.id.localeCompare(b.id));
}

export const handle: MockHandler = async ({ path, method, sub, roles }) => {
  if (path !== 'sim/upload-folders' || method !== 'GET') return null;
  return json(mockUploadFolders(sub, roles));
};
