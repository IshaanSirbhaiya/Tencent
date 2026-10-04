import { test, expect } from "@playwright/test";
import {
  MOVE_MS,
  cameraMove,
  isSettled,
  joinKey,
  type CameraInputs,
} from "../components/graph/camera";
import {
  LAYOUT_MAX_TICKS,
  computeGraphLayout,
  layoutTicksUsed,
} from "../components/graph/layout";

/**
 * E2E run 5 W-12 — `/graph` canvas nodes never settled, so the path and trail specs timed out.
 * Pure tests (no server): the layout is bounded and converged and never moves a drawn node again;
 * the camera asks for at most ONE move per change of what it frames, and none on a re-render;
 * "settled" is a deterministic predicate the specs wait on.
 */

function ring(n: number): { ids: string[]; edges: { source: string; target: string }[] } {
  const ids = Array.from({ length: n }, (_, i) => `n${i}`);
  const edges = ids.map((id, i) => ({ source: id, target: ids[(i + 1) % n] }));
  return { ids, edges };
}

test.describe("@unit W-12 graph layout and camera", () => {
  test("the layout is bounded, converged and deterministic", () => {
    for (const n of [1, 2, 12, 80]) {
      const { ids, edges } = ring(n);
      const a = computeGraphLayout(ids, edges);
      const ticks = layoutTicksUsed();
      expect(ticks).toBeGreaterThan(0);
      expect(ticks).toBeLessThanOrEqual(LAYOUT_MAX_TICKS);
      const b = computeGraphLayout([...ids].reverse(), edges);
      expect(a.size).toBe(n);
      for (const id of ids) expect(b.get(id)).toEqual(a.get(id)); // same input -> same picture
      for (const p of a.values()) {
        expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
      }
    }
  });

  test("re-laying out a drawn canvas moves no node (pins hold), and a merge moves none drawn", () => {
    const { ids, edges } = ring(30);
    const first = computeGraphLayout(ids, edges);
    const again = computeGraphLayout(ids, edges, first);
    for (const id of ids) expect(again.get(id)).toEqual(first.get(id));
    // an expand merges newcomers: every node already drawn stays exactly where it was
    const more = [...ids, "new-a", "new-b"];
    const merged = computeGraphLayout(
      more,
      [...edges, { source: ids[0], target: "new-a" }, { source: "new-a", target: "new-b" }],
      first,
    );
    for (const id of ids) expect(merged.get(id)).toEqual(first.get(id));
    expect(merged.has("new-a") && merged.has("new-b")).toBe(true);
  });

  test("one camera move per change of what is framed; none when nothing changed", () => {
    const base: CameraInputs = { nodeSetKey: joinKey(["a", "b"]), pathKey: "", matchKey: "", selectedId: null };
    expect(cameraMove(null, base)).toEqual({ type: "fit-all" });
    // a re-render with the same keys (a new layout object, a trail update) asks for nothing
    expect(cameraMove(base, { ...base })).toBeNull();
    // selecting a node centres it once; re-rendering with it still selected does not
    const sel = { ...base, selectedId: "a" };
    expect(cameraMove(base, sel)).toEqual({ type: "center", id: "a" });
    expect(cameraMove(sel, { ...sel })).toBeNull();
    // an expand (selection already set, node set grows) is ONE move, the fit — not two racing
    const grown = { ...sel, nodeSetKey: joinKey(["a", "b", "c"]) };
    expect(cameraMove(sel, grown)).toEqual({ type: "fit-all" });
    // a path frames the path, and a later merge keeps framing the path
    const withPath = { ...grown, pathKey: joinKey(["a", "c"]) };
    expect(cameraMove(grown, withPath)).toEqual({ type: "fit-path", ids: ["a", "c"] });
    const pathMerged = { ...withPath, nodeSetKey: joinKey(["a", "b", "c", "d"]) };
    expect(cameraMove(withPath, pathMerged)).toEqual({ type: "fit-path", ids: ["a", "c"] });
    // a search with one match centres it; several are framed together
    expect(cameraMove(base, { ...base, matchKey: joinKey(["b"]) })).toEqual({ type: "center", id: "b" });
    expect(cameraMove(base, { ...base, matchKey: joinKey(["a", "b"]) })).toEqual({
      type: "fit-matches",
      ids: ["a", "b"],
    });
    // every move has a finite, fixed duration
    for (const ms of Object.values(MOVE_MS)) expect(ms).toBeGreaterThan(0);
  });

  test("settled means measured and no move in flight", () => {
    expect(isSettled(true, 0)).toBe(true);
    expect(isSettled(false, 0)).toBe(false);
    expect(isSettled(true, 1)).toBe(false);
  });
});
