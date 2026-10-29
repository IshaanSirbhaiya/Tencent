// Web Worker: runs the deterministic layout off the main thread, so opening the graph never janks.
import { computeLayout } from './layout-core';
import type { LayoutInputEdge, LayoutInputNode } from './model';

interface Req {
  id: number;
  nodes: LayoutInputNode[];
  edges: LayoutInputEdge[];
}

const ctx = self as unknown as { onmessage: ((e: MessageEvent<Req>) => void) | null; postMessage: (m: unknown) => void };

ctx.onmessage = (e) => {
  const { id, nodes, edges } = e.data;
  try {
    ctx.postMessage({ id, positions: computeLayout(nodes, edges) });
  } catch (err) {
    ctx.postMessage({ id, error: String(err) });
  }
};
