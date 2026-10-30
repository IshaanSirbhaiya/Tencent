// Mock GET /ib/galaxy (NEXT_PUBLIC_IB_MOCK=1): the graph page's overview and map from ONE live pass
// (databridge_ib/galaxy.py). The body is {overview: <GET /ib/graph?limit=N>, map: <GET /ib/map> +
// complete}; both halves come from the same mock builders as the two single views, for the signed-in
// person only (the compare slot's own cookie decides who that is). Synthetic data only.
import { NextResponse } from 'next/server';
import type { KnowledgeGalaxy } from '../../types';
import { SCOPE_NOTE } from '../fixtures';
import { buildMap } from '../map';
import { currentMockState, overviewFor } from './graph';
import type { MockHandler } from './types';

const DEFAULT_LIMIT = 120;
const MAX_LIMIT = 300;
const EMPLOYEE = ['employee', 'compliance_officer', 'admin_it'];
const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { 'cache-control': 'no-store' } });
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const handle: MockHandler = async ({ req, path, method, sub, roles }) => {
  if (path !== 'ib/galaxy' || method !== 'GET') return null;
  if (!roles.some((r) => EMPLOYEE.includes(r))) return json({ detail: 'forbidden' }, 403);
  const raw = req.nextUrl.searchParams.get('limit');
  const limit = raw === null ? DEFAULT_LIMIT : Number(raw);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) return json({ detail: 'invalid parameters' }, 422);
  // The live route checks every document once for both views: about as long as the map alone.
  await wait(1500);
  const overview = overviewFor(sub, limit);
  if (!overview) return json({ error: 'temporarily unavailable' }, 503);
  const body: KnowledgeGalaxy = { overview, map: { ...buildMap(sub, currentMockState(), SCOPE_NOTE), complete: true } };
  return json(body);
};
