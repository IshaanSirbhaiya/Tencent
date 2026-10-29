'use client';
// The engine's graph canvas: React Flow 11 (as in the original Walnut screens) with a frozen,
// deterministic d3-force layout. Small subgraphs only (≤ ~300 nodes); the whole store is never drawn.
//
// Accessibility: every node and edge is focusable with an accessible name; Enter or Space selects;
// the zoom and pan buttons work from the keyboard; the same data is available as tables next to the
// canvas (GraphTables). Motion: viewport moves are instant when reduced motion is requested.
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { memo, useCallback, useEffect, useMemo, useRef } from 'react';
import ReactFlow, {
  Background,
  BackgroundVariant,
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeChange,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
} from 'reactflow';
import 'reactflow/dist/base.css';
import { IconButton } from '@/components/ui';
import { cn } from '@/lib/cn';
import { computeLayout, degreeMap, nodeSize, type Pos } from './layout';
import type { Palette } from './palette';
import { useReducedMotion } from './use-engine';

export interface CanvasNode {
  id: string;
  kind: string;
  label: string;
  /** Drawn faded (e.g. created after the time slider's date). */
  dim?: boolean;
}

export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  dim?: boolean;
}

export type Selection = { type: 'node' | 'edge'; id: string } | null;

interface NodeData {
  label: string;
  kindLabel: string;
  color: string;
  size: number;
  dim: boolean;
  showLabel: boolean;
}

interface EdgeData {
  targetRadius: number;
  label: string;
  dim: boolean;
}

const HANDLE_STYLE: React.CSSProperties = {
  top: '50%',
  left: '50%',
  transform: 'translate(-50%, -50%)',
  width: 1,
  height: 1,
  minWidth: 0,
  minHeight: 0,
  opacity: 0,
  border: 0,
  pointerEvents: 'none',
};

const EntityNode = memo(function EntityNode({ data, selected }: NodeProps<NodeData>) {
  const short = data.label.length > 30 ? `${data.label.slice(0, 29)}…` : data.label;
  return (
    <div className="group relative" style={{ width: data.size, height: data.size }}>
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} isConnectable={false} />
      <span
        aria-hidden
        className={cn(
          'block rounded-full border transition-opacity duration-base',
          selected ? 'border-fg ring-2 ring-accent ring-offset-2 ring-offset-bg' : 'border-fg/25',
        )}
        style={{ width: data.size, height: data.size, background: data.color, opacity: data.dim ? 0.16 : 1 }}
      />
      <span
        aria-hidden
        className={cn(
          'pointer-events-none absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-bg/85 px-1 text-micro leading-4',
          data.showLabel || selected ? 'block' : 'hidden group-hover:block [.react-flow__node:focus_&]:block',
          selected ? 'font-semibold text-fg' : 'text-muted',
          data.dim && !selected ? 'bg-transparent opacity-40' : '',
        )}
      >
        {short}
      </span>
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} isConnectable={false} />
    </div>
  );
});

/** A straight link that stops at the target's rim, so its arrowhead stays visible. */
function EntityEdge({ id, sourceX, sourceY, targetX, targetY, data, selected, markerEnd }: EdgeProps<EdgeData>) {
  const dx = targetX - sourceX;
  const dy = targetY - sourceY;
  const len = Math.hypot(dx, dy) || 1;
  const cut = Math.min((data?.targetRadius ?? 6) + 3, len / 2);
  const ex = targetX - (dx / len) * cut;
  const ey = targetY - (dy / len) * cut;
  const dim = data?.dim ?? false;
  return (
    <>
      <BaseEdge
        id={id}
        path={`M ${sourceX},${sourceY} L ${ex},${ey}`}
        markerEnd={markerEnd}
        interactionWidth={14}
        style={{
          stroke: selected ? 'rgb(var(--accent))' : 'rgb(var(--subtle))',
          strokeWidth: selected ? 2 : 1,
          opacity: dim ? 0.12 : selected ? 1 : 0.55,
        }}
      />
      {selected && data ? (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded border border-line bg-overlay px-1.5 py-0.5 text-micro text-fg shadow-pop"
            style={{ transform: `translate(-50%, -50%) translate(${(sourceX + targetX) / 2}px, ${(sourceY + targetY) / 2}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const nodeTypes = { entity: EntityNode };
const edgeTypes = { entity: EntityEdge };

export interface EngineCanvasProps {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  palette: Palette;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onExpand?: (nodeId: string) => void;
  /** Keep positions of nodes already drawn when the node set grows (expand). */
  keepPositions?: boolean;
  /** Show every label (small diagrams such as the ontology). */
  allLabels?: boolean;
  layout?: { charge?: number; distance?: number };
  label: string;
  className?: string;
  testId?: string;
}

function CanvasInner({ nodes, edges, palette, selection, onSelect, onExpand, keepPositions, allLabels, layout, label, className, testId }: EngineCanvasProps) {
  const rf = useReactFlow();
  const reduce = useReducedMotion();
  const duration = reduce ? 0 : 200;
  const prior = useRef<Map<string, Pos>>(new Map());

  const nodeKey = useMemo(() => nodes.map((n) => n.id).sort().join('\n'), [nodes]);
  const edgeKey = useMemo(() => edges.map((e) => e.id).sort().join('\n'), [edges]);

  const positions = useMemo(() => {
    const pinned = keepPositions ? new Map([...prior.current].filter(([id]) => nodes.some((n) => n.id === id))) : undefined;
    const pos = computeLayout(
      nodes.map((n) => n.id),
      edges.map((e) => ({ source: e.source, target: e.target })),
      pinned,
      layout,
    );
    prior.current = pos;
    return pos;
    // The layout depends only on the id sets (a frozen picture per node/edge set).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeKey, edgeKey, keepPositions]);

  const degree = useMemo(() => degreeMap(edges), [edges]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  const rfNodes: Node<NodeData>[] = useMemo(
    () =>
      nodes.map((n) => {
        const size = nodeSize(degree.get(n.id) ?? 0);
        const p = positions.get(n.id) ?? { x: 0, y: 0 };
        const style = palette.node(n.kind);
        return {
          id: n.id,
          type: 'entity',
          position: { x: p.x - size / 2, y: p.y - size / 2 },
          data: {
            label: n.label,
            kindLabel: style.label,
            color: style.color,
            size,
            dim: !!n.dim,
            showLabel: allLabels || nodes.length <= 12 || size >= (nodes.length > 60 ? 28 : 22),
          },
          selected: selection?.type === 'node' && selection.id === n.id,
          ariaLabel: `${style.label}: ${n.label}${n.dim ? ' (not yet created at this date)' : ''}`,
          draggable: false,
          connectable: false,
          zIndex: selection?.id === n.id ? 10 : 1,
        };
      }),
    [nodes, positions, degree, palette, selection, allLabels],
  );

  const rfEdges: Edge<EdgeData>[] = useMemo(
    () =>
      edges
        .filter((e) => byId.has(e.source) && byId.has(e.target))
        .map((e) => {
          const sel = selection?.type === 'edge' && selection.id === e.id;
          const lbl = palette.edgeLabel(e.kind);
          return {
            id: e.id,
            source: e.source,
            target: e.target,
            type: 'entity',
            selected: sel,
            data: { targetRadius: nodeSize(degree.get(e.target) ?? 0) / 2, label: lbl, dim: !!e.dim },
            markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: sel ? 'rgb(var(--accent))' : 'rgb(var(--subtle))' },
            ariaLabel: `${byId.get(e.source)?.label ?? e.source} ${lbl} ${byId.get(e.target)?.label ?? e.target}`,
            zIndex: sel ? 5 : 0,
          };
        }),
    [edges, byId, selection, palette, degree],
  );

  // Fit when the drawn set changes.
  useEffect(() => {
    const raf = requestAnimationFrame(() => rf.fitView({ padding: 0.15, duration }));
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeKey]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const sel = changes.find((c) => c.type === 'select' && c.selected);
      if (sel && sel.type === 'select') onSelect({ type: 'node', id: sel.id });
    },
    [onSelect],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const sel = changes.find((c) => c.type === 'select' && c.selected);
      if (sel && sel.type === 'select') onSelect({ type: 'edge', id: sel.id });
    },
    [onSelect],
  );

  const pan = (dx: number, dy: number) => {
    const v = rf.getViewport();
    rf.setViewport({ x: v.x + dx, y: v.y + dy, zoom: v.zoom }, { duration });
  };

  return (
    <div
      className={cn('engine-canvas relative h-[460px] w-full overflow-hidden rounded-lg border border-line bg-bg sm:h-[540px]', className)}
      role="group"
      aria-label={label}
      data-testid={testId}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDoubleClick={onExpand ? (_, n) => onExpand(n.id) : undefined}
        onPaneClick={() => onSelect(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        nodesFocusable
        edgesFocusable
        selectNodesOnDrag={false}
        zoomOnDoubleClick={false}
        minZoom={0.15}
        maxZoom={2.5}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        proOptions={{ hideAttribution: true }}
        deleteKeyCode={null}
        selectionKeyCode={null}
        multiSelectionKeyCode={null}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="rgb(var(--line-strong))" />
        <Panel position="bottom-left" className="!m-2">
          <div className="flex items-center gap-0.5 rounded-md border border-line bg-surface/95 p-0.5 shadow-pop">
            <IconButton size="sm" icon={ZoomIn} label="Zoom in" onClick={() => rf.zoomIn({ duration })} />
            <IconButton size="sm" icon={ZoomOut} label="Zoom out" onClick={() => rf.zoomOut({ duration })} />
            <IconButton size="sm" icon={Maximize2} label="Fit the graph to the view" onClick={() => rf.fitView({ padding: 0.15, duration })} />
            <span aria-hidden className="mx-0.5 h-4 w-px bg-line" />
            <IconButton size="sm" icon={ArrowLeft} label="Pan left" onClick={() => pan(80, 0)} />
            <IconButton size="sm" icon={ArrowUp} label="Pan up" onClick={() => pan(0, 80)} />
            <IconButton size="sm" icon={ArrowDown} label="Pan down" onClick={() => pan(0, -80)} />
            <IconButton size="sm" icon={ArrowRight} label="Pan right" onClick={() => pan(-80, 0)} />
          </div>
        </Panel>
      </ReactFlow>
    </div>
  );
}

export function EngineCanvas(props: EngineCanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
