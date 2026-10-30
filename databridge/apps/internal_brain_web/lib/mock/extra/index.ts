import type { MockHandler } from './types';
import { handle as user } from './user';
import { handle as graph } from './graph';
import { handle as analytics } from './analytics';
import { handle as audit } from './audit';
import { handle as presenter } from './presenter';
import { handle as act } from './act';
import { handle as deliver } from './deliver';
import { handle as uploads } from './uploads';
import { handle as view } from './view';

/** Area handlers tried in order before the built-in mock routes. Each agent edits only its own file. */
// `act` comes before `analytics`: it extends the journey and receipt with action records.
// `deliver` comes before `graph`: it extends GET ib/status with the deliverables block.
// `view` comes before `deliver`: it extends GET ib/status with the views block.
export const EXTRA_HANDLERS: MockHandler[] = [user, act, view, deliver, uploads, graph, analytics, audit, presenter];
