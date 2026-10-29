import type { NextRequest, NextResponse } from 'next/server';

/** One area's mock handlers. Return null when the path is not yours, so the next area can try. */
export interface MockCtx {
  req: NextRequest;
  path: string;
  method: string;
  sub: string;
  roles: string[];
}
export type MockHandler = (ctx: MockCtx) => Promise<NextResponse | null>;
