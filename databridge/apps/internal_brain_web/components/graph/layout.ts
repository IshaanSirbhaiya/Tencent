// Layout client: one Web Worker, results cached per dataset hash (memory + sessionStorage), so
// re-opening the same view is instant and always identical. Falls back to the main thread (after a
// frame, so the loading state paints) when Workers are unavailable.
import { LAYOUT_VERSION, type Positions } from './layout-core';
import { datasetHash, type KGraph, layoutInput } from './model';

const memory = new Map<string, Positions>();
let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, { resolve: (p: Positions) => void; reject: (e: Error) => void }>();

function getWorker(): Worker | null {
  if (worker) return worker;
  if (typeof Worker === 'undefined') return null;
  try {
    worker = new Worker(new URL('./layout.worker.ts', import.meta.url));
    worker.onmessage = (e: MessageEvent<{ id: number; positions?: Positions; error?: string }>) => {
      const p = pending.get(e.data.id);
      if (!p) return;
      pending.delete(e.data.id);
      if (e.data.positions) p.resolve(e.data.positions);
      else p.reject(new Error(e.data.error ?? 'layout failed'));
    };
    worker.onerror = () => {
      for (const p of pending.values()) p.reject(new Error('layout worker failed'));
      pending.clear();
      worker?.terminate();
      worker = null;
    };
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
  const cached = !!positions;
  if (!positions) {
    const w = getWorker();
    if (w) {
      const id = ++seq;
      positions = await new Promise<Positions>((resolve, reject) => {
        pending.set(id, { resolve, reject });
        w.postMessage({ id, ...input });
      }).catch(async () => {
        const { computeLayout } = await import('./layout-core');
        return computeLayout(input.nodes, input.edges);
      });
    } else {
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      const { computeLayout } = await import('./layout-core');
      positions = computeLayout(input.nodes, input.edges);
    }
    writeCache(key, positions);
  }
  g.forEachNode((id) => {
    const p = positions![id];
    if (p) g.mergeNodeAttributes(id, { x: p[0], y: p[1] });
  });
  return { cached, ms: Math.round(performance.now() - t0), hash: key };
}
