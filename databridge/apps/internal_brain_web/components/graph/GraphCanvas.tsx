'use client';
// The WebGL canvas (sigma 3.0.3). One sigma instance per mount; all view state (selection, filters,
// working set, shared highlight, theme) is read by the reducers from refs, so changing it never
// rebuilds the renderer. Dark-theme label and hover renderers replace sigma's black-on-white
// defaults. Camera moves jump instead of animating under reduced motion.
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import type Sigma from 'sigma';
import type { Settings } from 'sigma/settings';
import type { NodeDisplayData, PartialButFor } from 'sigma/types';
import { displayLabel, kindLine } from './format';
import type { EdgeAttrs, KGraph, NodeAttrs } from './model';
import { type Palette, type Rgb, css, hex, mix, readPalette, rgba } from './palette';

export interface CanvasHandle {
  flyTo: (id: string) => void;
  fit: (ids?: Iterable<string>) => void;
  zoom: (dir: 1 | -1) => void;
  pan: (dx: number, dy: number) => void;
}

export interface CanvasView {
  selected: string | null;
  hiddenPlatforms: ReadonlySet<string>;
  hiddenKinds: ReadonlySet<string>;
  /** Working set: when set, only these nodes are drawn. */
  scope: ReadonlySet<string> | null;
  /** Compare: nodes the other person can ALSO open (labelled, never anything else). */
  shared: ReadonlySet<string> | null;
}

interface Props {
  graph: KGraph;
  view: CanvasView;
  reducedMotion: boolean;
  onSelect: (id: string | null) => void;
  onContextLost: () => void;
  onReady?: () => void;
  label: string;
  describedBy?: string;
  testId?: string;
}

const FADE_MS = 420;
const FONT = '"Inter Variable", Inter, ui-sans-serif, system-ui, sans-serif';
type Display = PartialButFor<NodeDisplayData, 'x' | 'y' | 'size' | 'label' | 'color'> & { fullLabel?: string; sub?: string; labelColor?: string };

function baseColor(a: NodeAttrs, p: Palette): Rgb {
  if (a.kind === 'person') return p.theme === 'dark' ? p.muted : p.subtle;
  const pf = a.platform ? p.platform[a.platform] : p.muted;
  if (a.kind === 'doc') return mix(pf, p.bg, p.theme === 'dark' ? 0.28 : 0.18);
  return pf;
}

export const GraphCanvas = forwardRef<CanvasHandle, Props>(function GraphCanvas({ graph, view, reducedMotion, onSelect, onContextLost, onReady, label, describedBy, testId }, ref) {
  const box = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma<NodeAttrs, EdgeAttrs> | null>(null);
  const viewRef = useRef(view);
  const motionRef = useRef(reducedMotion);
  const palRef = useRef<Palette | null>(null);
  const egoRef = useRef<Set<string>>(new Set());
  const cbRef = useRef({ onSelect, onContextLost, onReady });
  cbRef.current = { onSelect, onContextLost, onReady };
  motionRef.current = reducedMotion;

  // keep the ego set in step with the selection
  useEffect(() => {
    viewRef.current = view;
    const ego = new Set<string>();
    if (view.selected && graph.hasNode(view.selected)) {
      ego.add(view.selected);
      graph.forEachNeighbor(view.selected, (n) => ego.add(n));
    }
    egoRef.current = ego;
    sigmaRef.current?.refresh();
  }, [view, graph]);

  useEffect(() => {
    let killed = false;
    let raf = 0;
    let fadeUntil = 0;
    const cleanups: (() => void)[] = [];
    (async () => {
      const [{ default: SigmaCtor }, rendering] = await Promise.all([import('sigma'), import('sigma/rendering')]);
      if (killed || !box.current) return;
      const el = box.current;
      palRef.current = readPalette(el);
      const pal = () => palRef.current!;

      const drawLabel = (ctx: CanvasRenderingContext2D, data: Display, settings: Settings) => {
        if (!data.label) return;
        const size = settings.labelSize;
        ctx.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
        const x = data.x + data.size + 4;
        const y = data.y + size / 3;
        ctx.lineJoin = 'round';
        ctx.lineWidth = 3;
        ctx.strokeStyle = rgba(pal().bg, 0.92);
        ctx.strokeText(data.label, x, y);
        ctx.fillStyle = data.labelColor ?? css(pal().fg);
        ctx.fillText(data.label, x, y);
      };

      const drawHover = (ctx: CanvasRenderingContext2D, data: Display, settings: Settings) => {
        const p = pal();
        const title = data.fullLabel ?? data.label ?? '';
        const sub = data.sub ?? '';
        const size = settings.labelSize;
        ctx.font = `600 ${size}px ${settings.labelFont}`;
        const w1 = ctx.measureText(title).width;
        ctx.font = `400 ${size - 1}px ${settings.labelFont}`;
        const w2 = sub ? ctx.measureText(sub).width : 0;
        const padX = 8;
        const h = sub ? size * 2 + 12 : size + 10;
        const x = data.x + data.size + 6;
        const y = data.y - h / 2;
        const w = Math.max(w1, w2) + padX * 2;
        // node ring
        ctx.beginPath();
        ctx.arc(data.x, data.y, data.size + 2.5, 0, Math.PI * 2);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = css(p.accent);
        ctx.stroke();
        // label card: surface + 1px line, no shadow, no glow
        ctx.beginPath();
        const r = 5;
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + w, y, x + w, y + h, r);
        ctx.arcTo(x + w, y + h, x, y + h, r);
        ctx.arcTo(x, y + h, x, y, r);
        ctx.arcTo(x, y, x + w, y, r);
        ctx.closePath();
        ctx.fillStyle = css(p.surface);
        ctx.fill();
        ctx.lineWidth = 1;
        ctx.strokeStyle = css(p.lineStrong);
        ctx.stroke();
        ctx.fillStyle = css(p.fg);
        ctx.font = `600 ${size}px ${settings.labelFont}`;
        ctx.fillText(title, x + padX, y + size + 3);
        if (sub) {
          ctx.fillStyle = css(p.muted);
          ctx.font = `400 ${size - 1}px ${settings.labelFont}`;
          ctx.fillText(sub, x + padX, y + size * 2 + 6);
        }
      };

      const nodeReducer = (node: string, a: NodeAttrs): Partial<NodeDisplayData> => {
        const p = pal();
        const v = viewRef.current;
        const full = displayLabel(a);
        const short = full.length > 44 ? `${full.slice(0, 42)}…` : full;
        const out: Display = { x: a.x, y: a.y, size: a.size, label: null, color: '', zIndex: 1, fullLabel: full, sub: kindLine(a) };
        if (v.scope && !v.scope.has(node)) return { ...out, hidden: true };
        if ((a.platform && v.hiddenPlatforms.has(a.platform)) || v.hiddenKinds.has(a.kind)) return { ...out, hidden: true };
        let c = baseColor(a, p);
        const hub = a.kind === 'platform' || a.kind === 'container';
        out.label = short;
        out.forceLabel = hub;
        out.labelColor = hub ? css(p.fg) : css(p.muted);
        if (a.kind === 'platform') out.zIndex = 3;
        else if (a.kind === 'container') out.zIndex = 2;
        if (v.shared) {
          if (v.shared.has(node)) {
            c = p.accent;
            out.zIndex = 4;
            out.size = a.size * 1.35;
          } else if (a.kind === 'doc') c = mix(c, p.bg, 0.45);
        }
        if (v.selected) {
          const ego = egoRef.current;
          if (node === v.selected) {
            out.zIndex = 6;
            out.forceLabel = true;
            out.size = a.size * 1.3 + 1.5;
            out.labelColor = css(p.fg);
          } else if (ego.has(node)) {
            out.zIndex = 5;
            out.forceLabel = hub || ego.size <= 36;
            out.labelColor = css(p.fg);
          } else {
            c = mix(c, p.bg, 0.84);
            out.label = null;
            out.forceLabel = false;
            out.zIndex = 0;
          }
        }
        if (a.born && !motionRef.current) {
          const t = (performance.now() - a.born) / FADE_MS;
          if (t < 1) c = mix(p.bg, c, Math.max(0, t));
        }
        out.color = hex(c);
        return out;
      };

      const edgeReducer = (edge: string, a: EdgeAttrs): Partial<{ color: string; size: number; hidden: boolean; zIndex: number; type: string }> => {
        const p = pal();
        const v = viewRef.current;
        const g = graph;
        const s = g.source(edge);
        const t = g.target(edge);
        const dark = p.theme === 'dark';
        let color: string;
        let alpha: number;
        if (a.kind === 'contains') {
          const top = g.getNodeAttribute(s, 'kind') === 'platform';
          color = top ? hex(p.lineStrong) : hex(p.fg);
          alpha = top ? (dark ? 0.55 : 0.7) : dark ? 0.075 : 0.13;
        } else if (a.kind === 'authored') {
          color = hex(p.muted);
          alpha = dark ? 0.09 : 0.14;
        } else {
          color = hex(p.fg);
          alpha = dark ? 0.32 : 0.38;
        }
        let rgb = { r: parseInt(color.slice(1, 3), 16), g: parseInt(color.slice(3, 5), 16), b: parseInt(color.slice(5, 7), 16) };
        if (v.selected) {
          if (s === v.selected || t === v.selected) {
            // a big neighbourhood (a place with 100 documents) stays hairline; a small one is drawn bolder
            const big = egoRef.current.size > 30;
            return big
              ? { color: hex(mix(p.bg, p.accent, dark ? 0.5 : 0.6)), size: 1, zIndex: 2, type: 'line' }
              : { color: hex(mix(p.bg, p.accent, 0.8)), size: 1.4, zIndex: 2, type: 'rect' };
          }
          return { hidden: true };
        }
        if (v.shared) alpha *= 0.6;
        if (a.born && !motionRef.current) {
          const k = (performance.now() - a.born) / FADE_MS;
          if (k < 1) alpha *= Math.max(0, k);
        }
        // Blend into the canvas colour here: sigma's line program does not premultiply alpha, so an rgba
        // edge would render far brighter than asked. An opaque pre-blended colour is exact and crisp.
        return { color: hex(mix(p.bg, rgb, alpha)), size: 1, zIndex: a.kind === 'links' || a.kind === 'mentions' ? 1 : 0, type: 'line' };
      };

      let s: Sigma<NodeAttrs, EdgeAttrs>;
      try {
        s = new SigmaCtor<NodeAttrs, EdgeAttrs>(graph, el, {
          allowInvalidContainer: true,
          renderEdgeLabels: false,
          enableEdgeEvents: false,
          zIndex: true,
          hideEdgesOnMove: false,
          // 2D label drawing is the per-frame cost while the camera moves: above ~1,000 nodes labels
          // pause during a move (measured: 52 -> 60 fps at 1,300 nodes on an integrated GPU).
          hideLabelsOnMove: graph.order > 1000,
          labelFont: FONT,
          labelSize: 11,
          labelWeight: '500',
          labelColor: { color: css(pal().fg) },
          labelDensity: 0.55,
          labelGridCellSize: 110,
          labelRenderedSizeThreshold: 6.5,
          minEdgeThickness: 1,
          stagePadding: 36,
          minCameraRatio: 0.025,
          maxCameraRatio: 2.5,
          zoomDuration: 220,
          defaultEdgeType: 'line',
          edgeProgramClasses: { line: rendering.EdgeLineProgram, rect: rendering.EdgeRectangleProgram },
          defaultDrawNodeLabel: drawLabel as never,
          defaultDrawNodeHover: drawHover as never,
          nodeReducer: nodeReducer as never,
          edgeReducer: edgeReducer as never,
        });
      } catch {
        cbRef.current.onContextLost();
        return;
      }
      sigmaRef.current = s;

      s.on('clickNode', ({ node }) => cbRef.current.onSelect(node));
      s.on('clickStage', () => cbRef.current.onSelect(null));
      s.on('enterNode', () => (el.style.cursor = 'pointer'));
      s.on('leaveNode', () => (el.style.cursor = ''));

      for (const c of Object.values(s.getCanvases())) {
        const lost = (e: Event) => {
          e.preventDefault();
          cbRef.current.onContextLost();
        };
        c.addEventListener('webglcontextlost', lost);
        cleanups.push(() => c.removeEventListener('webglcontextlost', lost));
      }

      // fade newly merged nodes and edges in (skipped under reduced motion: they simply appear)
      const tick = () => {
        s.refresh({ skipIndexation: true });
        if (performance.now() < fadeUntil) raf = requestAnimationFrame(tick);
        else raf = 0;
      };
      const onAdded = (e: { attributes: { born?: number } }) => {
        if (!e.attributes.born || motionRef.current) return;
        fadeUntil = performance.now() + FADE_MS + 40;
        if (!raf) raf = requestAnimationFrame(tick);
      };
      graph.on('nodeAdded', onAdded);
      cleanups.push(() => graph.off('nodeAdded', onAdded));

      // theme / contrast changes repaint with the new tokens
      const mo = new MutationObserver(() => {
        palRef.current = readPalette(el);
        s.setSetting('labelColor', { color: css(pal().fg) });
        s.refresh();
      });
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-contrast'] });
      cleanups.push(() => mo.disconnect());

      const ro = new ResizeObserver(() => {
        s.resize();
        s.refresh({ skipIndexation: true });
      });
      ro.observe(el);
      cleanups.push(() => ro.disconnect());

      cbRef.current.onReady?.();
    })();
    return () => {
      killed = true;
      if (raf) cancelAnimationFrame(raf);
      cleanups.forEach((f) => f());
      sigmaRef.current?.kill();
      sigmaRef.current = null;
    };
  }, [graph]);

  useImperativeHandle(
    ref,
    () => ({
      flyTo(id) {
        const s = sigmaRef.current;
        if (!s) return;
        const d = s.getNodeDisplayData(id);
        if (!d) return;
        const cam = s.getCamera();
        const target = { x: d.x, y: d.y, ratio: Math.min(cam.ratio, 0.2) };
        if (motionRef.current) cam.setState(target);
        else void cam.animate(target, { duration: 520, easing: 'cubicInOut' });
      },
      fit(ids) {
        const s = sigmaRef.current;
        if (!s) return;
        const cam = s.getCamera();
        const list = ids ? [...ids] : null;
        let target = { x: 0.5, y: 0.5, ratio: 1, angle: 0 };
        if (list && list.length) {
          let x1 = Infinity;
          let x2 = -Infinity;
          let y1 = Infinity;
          let y2 = -Infinity;
          for (const id of list) {
            const d = s.getNodeDisplayData(id);
            if (!d || d.hidden) continue;
            x1 = Math.min(x1, d.x);
            x2 = Math.max(x2, d.x);
            y1 = Math.min(y1, d.y);
            y2 = Math.max(y2, d.y);
          }
          if (Number.isFinite(x1)) target = { x: (x1 + x2) / 2, y: (y1 + y2) / 2, ratio: Math.min(1.1, Math.max(0.06, Math.max(x2 - x1, y2 - y1) * 1.35)), angle: 0 };
        }
        if (motionRef.current) cam.setState(target);
        else void cam.animate(target, { duration: 420, easing: 'cubicInOut' });
      },
      zoom(dir) {
        const cam = sigmaRef.current?.getCamera();
        if (!cam) return;
        const duration = motionRef.current ? 0 : 200;
        if (dir > 0) void cam.animatedZoom({ duration, factor: 1.6 });
        else void cam.animatedUnzoom({ duration, factor: 1.6 });
      },
      pan(dx, dy) {
        const cam = sigmaRef.current?.getCamera();
        if (!cam) return;
        const st = cam.getState();
        const target = { x: st.x + dx * 0.18 * st.ratio, y: st.y + dy * 0.18 * st.ratio };
        if (motionRef.current) cam.setState(target);
        else void cam.animate(target, { duration: 160 });
      },
    }),
    [],
  );

  return (
    <div
      ref={box}
      role="img"
      aria-label={label}
      aria-describedby={describedBy}
      data-testid={testId}
      className="absolute inset-0 [&_canvas]:outline-none"
    />
  );
});
