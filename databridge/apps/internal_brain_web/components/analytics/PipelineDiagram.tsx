'use client';
// The pipeline diagram on Mission control: React Flow 11, static, hand-set positions (no layout
// engine, no physics, nothing draggable). Every node is a real <button>, in pipeline order, so the
// diagram is keyboard- and screen-reader-operable; React Flow only draws the canvas and the edges.
import 'reactflow/dist/style.css';
import { Database, Fingerprint, Filter, MessageSquareQuote, ScanSearch, ShieldCheck, Sparkles, Waypoints, type LucideIcon } from 'lucide-react';
import { memo, useEffect, useMemo, useState } from 'react';
import ReactFlow, { Handle, MarkerType, Position, type Edge, type Node, type NodeProps } from 'reactflow';
import { cn } from '@/lib/cn';
import { PIPELINE, type PipelineKind, type PipelineStep, type Timing } from './pipeline';

const ICON: Record<string, LucideIcon> = {
  identity: Fingerprint,
  'ledger-accepted': Database,
  gate1: Filter,
  relevance: ScanSearch,
  gate2: ShieldCheck,
  'ledger-decided': Database,
  model: Sparkles,
  grounding: Waypoints,
  'ledger-released': Database,
  answer: MessageSquareQuote,
};

const KIND_CLS: Record<PipelineKind, string> = {
  identity: 'border-line-strong',
  ledger: 'border-line-strong border-dashed',
  gate: 'border-line-strong',
  model: 'border-line-strong',
  check: 'border-line-strong',
  answer: 'border-accent/60',
};


interface Data {
  step: PipelineStep;
  index: number;
  selected: boolean;
  timing: Timing;
  onSelect: (id: string) => void;
}

const SIDES = [Position.Left, Position.Right, Position.Top, Position.Bottom];

const StepNode = memo(function StepNode({ data }: NodeProps<Data>) {
  const { step, selected, timing, index } = data;
  const Icon = ICON[step.id] ?? Database;
  return (
    <>
      {SIDES.map((p) => (
        <Handle key={`t${p}`} id={`t-${p}`} type="target" position={p} isConnectable={false} className="!h-1 !w-1 !min-w-0 !border-0 !bg-transparent" />
      ))}
      <button
        type="button"
        onClick={() => data.onSelect(step.id)}
        aria-pressed={selected}
        aria-label={`Step ${index + 1} of ${PIPELINE.length}: ${step.title}. ${timing.value ? `p50 ${timing.value}${timing.note ? `, ${timing.note}` : ''}` : step.short}`}
        data-testid={`pipeline-node-${step.id}`}
        className={cn(
          'nodrag nopan flex h-[64px] w-[192px] flex-col justify-center rounded-lg border bg-surface px-3 text-left transition-colors duration-fast',
          'hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus',
          KIND_CLS[step.kind],
          selected && 'border-accent bg-accent/10 shadow-[0_0_0_1px_rgb(var(--accent))]',
        )}
      >
        <span className="flex w-full min-w-0 items-center gap-1.5">
          <Icon aria-hidden className={cn('h-3.5 w-3.5 shrink-0', step.kind === 'ledger' ? 'text-muted' : 'text-accent')} />
          <span className="truncate text-[12px] font-semibold leading-4 text-fg">{step.title}</span>
        </span>
        <span className="mt-1 block w-full truncate text-[11px] leading-4 text-muted">
          {timing.value ? (
            <>
              <span className="font-medium tabular-nums text-fg">p50 {timing.value}</span>
              {timing.note ? <span className="text-subtle"> · {timing.note}</span> : null}
            </>
          ) : (
            step.short
          )}
        </span>
      </button>
      {SIDES.map((p) => (
        <Handle key={`s${p}`} id={`s-${p}`} type="source" position={p} isConnectable={false} className="!h-1 !w-1 !min-w-0 !border-0 !bg-transparent" />
      ))}
    </>
  );
});

const NODE_TYPES = { step: StepNode };
const COL = 228;
const ROW = 118;

function useNarrow(query = '(max-width: 767px)') {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    on();
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [query]);
  return narrow;
}

export default function PipelineDiagram({
  selected,
  onSelect,
  timings,
}: {
  selected: string;
  onSelect: (id: string) => void;
  timings: Record<string, Timing>;
}) {
  const narrow = useNarrow();
  const { nodes, edges } = useMemo(() => {
    const pos = (i: number) => {
      if (narrow) return { x: (i % 2) * 150, y: i * 74 };
      // a snake: five across, then five back
      return i < 5 ? { x: i * COL, y: 0 } : { x: (9 - i) * COL, y: ROW };
    };
    const nodes: Node<Data>[] = PIPELINE.map((step, index) => ({
      id: step.id,
      type: 'step',
      position: pos(index),
      data: { step, index, selected: selected === step.id, timing: timings[step.id] ?? { value: null }, onSelect },
      draggable: false,
      selectable: false,
      focusable: false,
    }));
    const edges: Edge[] = PIPELINE.slice(1).map((step, k) => {
      const i = k + 1;
      let sh = Position.Right;
      let th = Position.Left;
      if (narrow) {
        sh = Position.Bottom;
        th = Position.Top;
      } else if (i === 5) {
        sh = Position.Bottom;
        th = Position.Top;
      } else if (i > 5) {
        sh = Position.Left;
        th = Position.Right;
      }
      return {
        id: `e${i}`,
        source: PIPELINE[i - 1].id,
        target: step.id,
        sourceHandle: `s-${sh}`,
        targetHandle: `t-${th}`,
        type: 'smoothstep',
        focusable: false,
        style: { stroke: 'rgb(var(--line-strong))', strokeWidth: 1.25 },
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: 'rgb(var(--line-strong))' },
      };
    });
    return { nodes, edges };
  }, [narrow, selected, timings, onSelect]);

  return (
    <div
      role="group"
      aria-label="The request pipeline, ten steps in order. Select a step to read what it guarantees."
      className={cn('w-full', narrow ? 'h-[780px]' : 'h-[230px]')}
      data-testid="pipeline-diagram"
    >
      <ReactFlow
        key={narrow ? 'narrow' : 'wide'}
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        // a click handler gives nodes pointer events (React Flow turns them off for static nodes)
        onNodeClick={(_, n) => onSelect(n.id)}
        fitView
        fitViewOptions={{ padding: narrow ? 0.04 : 0.06 }}
        minZoom={0.3}
        maxZoom={1.25}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        panOnDrag={false}
        panOnScroll={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
        className="!bg-transparent"
      />
    </div>
  );
}
