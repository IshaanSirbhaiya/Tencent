// Layout client: one Web Worker, results cached per dataset hash (memory + sessionStorage), so
// re-opening the same view is instant and always identical. Falls back to the main thread (after a
// frame, so the loading state paints) when Workers are unavailable, fail, or take longer than 3 s.
import { LAYOUT_VERSION, type Positions, computeLayout } from './layout-core';
import { datasetHash, type KGraph, layoutInput } from './model';

const memory = new Map<string, Positions>();
let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, { resolve: (p: Positions) => void; reject: (e: Error) => void }>();
const WORKER_DEADLINE_MS = 3000;
/** Set once the worker has failed or timed out: later layouts go straight to the main thread. */
let workerBroken = false;

function dropWorker() {
  workerBroken = true;
  for (const p of pending.values()) p.reject(new Error('layout worker unavailable'));
  pending.clear();
  try {
    worker?.terminate();
  } catch {
    /* already gone */
  }
  worker = null;
}

/** Every node has a finite position (a cached or worker result is never trusted blindly). */
function complete(p: Positions, nodes: { id: string }[]): boolean {
  return nodes.every((n) => {
    const v = p[n.id];
    return !!v && Number.isFinite(v[0]) && Number.isFinite(v[1]);
  });
}

function getWorker(): Worker | null {
  if (worker) return worker;
  if (workerBroken || typeof Worker === 'undefined') return null;
  try {
    worker = new Worker(new URL('./layout.worker.ts', import.meta.url));
    worker.onmessage = (e: MessageEvent<{ id: number; positions?: Positions; error?: string }>) => {
      const p = pending.get(e.data.id);
      if (!p) return;
      pending.delete(e.data.id);
      if (e.data.positions) p.resolve(e.data.positions);
      else p.reject(new Error(e.data.error ?? 'layout failed'));
    };
    worker.onerror = () => dropWorker();
    worker.onmessageerror = () => dropWorker();
    return worker;
  } catch {
    return null;
  }
}

function readCache(key: string): Positions | null {
  const m = memory.get(key);
  if (m) return m;
  try {
    const s = window.sessionStorage.getItem(`ib-graph-layout:${key}`);
    if (s) {
      const p = JSON.parse(s) as Positions;
      memory.set(key, p);
      return p;
    }
  } catch {
    /* storage unavailable */
  }
  return null;
}

function writeCache(key: string, p: Positions) {
  memory.set(key, p);
  try {
    window.sessionStorage.setItem(`ib-graph-layout:${key}`, JSON.stringify(p));
  } catch {
    /* storage full or unavailable: memory cache still works */
  }
}

/** Compute (or reuse) positions for the graph and write them onto its nodes. */
export async function applyLayout(g: KGraph): Promise<{ cached: boolean; ms: number; hash: string }> {
  const t0 = performance.now();
  const input = layoutInput(g);
  const key = `${LAYOUT_VERSION}:${datasetHash(input)}`;
  let positions = readCache(key);
  if (positions && !complete(positions, input.nodes)) positions = null;
  const cached = !!positions;
  if (!positions) {
    const w = getWorker();
    let fromWorker: Positions | null = null;
    if (w) {
      const id = ++seq;
      // The worker is an optimisation, never a dependency: if its chunk fails or hangs, or it takes
      // longer than WORKER_DEADLINE_MS, drop it and compute here (about 10 ms for ~900 nodes).
      fromWorker = await new Promise<Positions | null>((resolve) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          dropWorker();
          resolve(null);
        }, WORKER_DEADLINE_MS);
        pending.set(id, {
          resolve: (p) => {
            clearTimeout(timer);
            resolve(p);
          },
          reject: () => {
            clearTimeout(timer);
            resolve(null);
          },
        });
        try {
          w.postMessage({ id, ...input });
        } catch {
          clearTimeout(timer);
          pending.delete(id);
          dropWorker();
          resolve(null);
        }
      });
    } else {
      await new Promise((r) => requestAnimationFrame(() => r(null)));
    }
    // computeLayout is statically imported (no second lazy chunk that could also hang)
    positions = fromWorker && complete(fromWorker, input.nodes) ? fromWorker : computeLayout(input.nodes, input.edges);
    writeCache(key, positions);
  }
  g.forEachNode((id) => {
    const p = positions![id];
    if (p) g.mergeNodeAttributes(id, { x: p[0], y: p[1] });
  });
  return { cached, ms: Math.round(performance.now() - t0), hash: key };
}
