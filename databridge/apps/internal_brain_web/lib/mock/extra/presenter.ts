import type { MockHandler } from './types';

// Mock handlers owned by the "presenter" page agent. Return null for paths you do not handle.
//
// Nothing is needed here today: the Demo Day story is already served by the other areas' mocks,
// which run before this one (lib/mock/extra/index.ts):
//   - lib/mock/extra/user.ts      POST /ib/ask: Amy (approved customer script, cited), Rachel (the
//                                 Velquarry case, cited [1] and [2]), Ken on Velquarry and on the
//                                 made-up Bayfront Spice Traders (the same constant refusal), and
//                                 GET /ib/doc for those cited documents;
//   - lib/mock/extra/analytics.ts GET /ib/status (presenter status dots), /audit/journey (step 4,
//                                 reading the shared ledger the asks append to);
//   - lib/mock/extra/audit.ts     POST /audit/tamper-demo (step 6).
// tests/e2e/presenter.spec.ts checks the story end to end, so a change there shows up as a failure.
export const handle: MockHandler = async () => null;
