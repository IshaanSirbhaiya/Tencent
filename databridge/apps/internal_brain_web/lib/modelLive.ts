import type { IbStatus } from './types';

/** Hunyuan/Hy3 may be called LIVE only when the ACTIVE chain is TokenHub AND the launcher found it
 *  live (IB_TOKENHUB_LIVE=1). Anything else is neutral: "AI model active" or quoted. */
export function hunyuanLive(m?: Partial<IbStatus['models']> | null): boolean {
  return m?.active === true && m.active_provider === 'tencent-tokenhub' && m.primary_live === true;
}
