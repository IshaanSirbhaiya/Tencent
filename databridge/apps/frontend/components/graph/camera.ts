// The `/graph` camera: WHICH single move a render asks for, and WHEN the canvas has settled.
//
// E2E run 5 W-12: canvas nodes never settled, so the path and trail specs timed out. Two effects
// drove one camera (a re-centre on the selection, re-run whenever the layout object changed, and
// a re-fit whenever the node set changed), so one expand started two competing transitions, and
// any re-layout re-centred again even when the selected node had not moved. This module makes the
// decision pure and single: compare what the camera last framed with what it should frame now,
// and return at most ONE move — or none, when nothing the camera depends on has changed. The
// canvas then exposes a deterministic "settled" signal (`isSettled`) for readers and specs.

/** What the camera frames depends on exactly these keys — never on object identity. */
export interface CameraInputs {
  /** Every node id on the canvas, joined. */
  nodeSetKey: string;
  /** The found path's node ids, joined ("" when no path). */
  pathKey: string;
  /** The canvas-search matches, joined ("" when no search). */
  matchKey: string;
  /** The selected node id, or null. */
  selectedId: string | null;
}

export type CameraMove =
  | { type: "fit-path"; ids: string[] }
  | { type: "fit-matches"; ids: string[] }
  | { type: "center"; id: string }
  | { type: "fit-all" }
  | null;

const SEP = "\u0000";

export function joinKey(ids: Iterable<string>): string {
  return Array.from(ids).join(SEP);
}

function split(key: string): string[] {
  return key ? key.split(SEP) : [];
}

/**
 * The ONE move this render asks for, given what was framed before (`prev`, null on first render).
 * Priority: a new path frames the path; a new search frames its matches (one match is centred);
 * a changed node set frames the path if one is shown, else everything; a newly selected node is
 * centred. Nothing changed: no move — so a re-render can never restart the camera.
 */
export function cameraMove(prev: CameraInputs | null, next: CameraInputs): CameraMove {
  const changed = (k: keyof CameraInputs) => !prev || prev[k] !== next[k];
  if (next.pathKey && changed("pathKey")) return { type: "fit-path", ids: split(next.pathKey) };
  if (next.matchKey && changed("matchKey")) {
    const ids = split(next.matchKey);
    return ids.length === 1 ? { type: "center", id: ids[0] } : { type: "fit-matches", ids };
  }
  if (next.nodeSetKey && changed("nodeSetKey")) {
    return next.pathKey ? { type: "fit-path", ids: split(next.pathKey) } : { type: "fit-all" };
  }
  if (next.selectedId && changed("selectedId")) return { type: "center", id: next.selectedId };
  return null;
}

/** How long each move animates (ms). Fixed, so "settled" is deterministic. */
export const MOVE_MS: Record<Exclude<CameraMove, null>["type"], number> = {
  "fit-path": 500,
  "fit-matches": 600,
  center: 600,
  "fit-all": 300,
};
/** Slack after a move's duration before the camera counts as still (one frame or two). */
export const SETTLE_SLACK_MS = 80;

/** Settled: the nodes are measured and no camera move is in flight. */
export function isSettled(nodesMeasured: boolean, movesInFlight: number): boolean {
  return nodesMeasured && movesInFlight === 0;
}
