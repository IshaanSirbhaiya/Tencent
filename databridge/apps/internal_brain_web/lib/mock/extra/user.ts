import type { MockHandler } from './types';

// Mock handlers owned by the "user" page agent. Return null for paths you do not handle.
export const handle: MockHandler = async () => null;
