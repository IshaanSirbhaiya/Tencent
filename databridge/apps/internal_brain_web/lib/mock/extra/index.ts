import type { MockHandler } from './types';
import { handle as user } from './user';
import { handle as graph } from './graph';
import { handle as analytics } from './analytics';
import { handle as audit } from './audit';
import { handle as presenter } from './presenter';

/** Area handlers tried in order before the built-in mock routes. Each agent edits only its own file. */
export const EXTRA_HANDLERS: MockHandler[] = [user, graph, analytics, audit, presenter];
