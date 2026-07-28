'use client';

import React, { useEffect, useRef } from 'react';

/**
 * NeuralField — the "living machine": the 9-agent brain rendered as a breathing neural
 * constellation. Agents are nodes, the decision pipeline is the wiring, and signals pulse
 * along the edges as the committee deliberates. Wired to real bot state so it reflects the
 * live regime / P&L direction / latest action.
 *
 * Pure Canvas2D (DPR-aware, 60fps-capped, pauses when offscreen, honors reduced-motion).
 */

type Props = {
  regime?: string | null;      // e.g. 'trend' | 'range' | 'consolidation'
  equityUp?: boolean | null;   // tints the field green/red
  lastAction?: string | null;  // 'BUY' | 'SELL' — colors the newest pulse
  height?: number;             // css px; canvas fills its container width
  className?: string;
  style?: React.CSSProperties;
};

type Tier = 'haiku' | 'sonnet' | 'opus';
type Node = { id: string; label: string; tier: Tier; x: number; y: number; phase: number };

// Normalised layout (0..1) — an organic constellation, core spine + orbiting specialists.
const NODES: Node[] = [
  { id: 'regime',   label: 'Regime',   tier: 'haiku',  x: 0.12, y: 0.50, phase: 0.0 },
  { id: 'trade',    label: 'Trade',    tier: 'sonnet', x: 0.34, y: 0.34, phase: 0.7 },
  { id: 'risk',     label: 'Risk',     tier: 'haiku',  x: 0.34, y: 0.70, phase: 1.4 },
  { id: 'critic',   label: 'Critic',   tier: 'sonnet', x: 0.56, y: 0.50, phase: 2.1 },
  { id: 'quant',    label: 'Quant',    tier: 'opus',   x: 0.56, y: 0.18, phase: 2.8 },
  { id: 'learning', label: 'Learning', tier: 'haiku',  x: 0.56, y: 0.82, phase: 3.5 },
  { id: 'exit',     label: 'Exit',     tier: 'haiku',  x: 0.78, y: 0.36, phase: 4.2 },
  { id: 'scout',    label: 'Scout',    tier: 'haiku',  x: 0.78, y: 0.66, phase: 4.9 },
  { id: 'overseer', label: 'Overseer', tier: 'opus',   x: 0.92, y: 0.50, phase: 5.6 },
];

const EDGES: [string, string][] = [
  ['regime', 'trade'], ['regime', 'risk'],
  ['trade', 'critic'], ['risk', 'critic'], ['quant', 'trade'], ['quant', 'critic'],
  ['critic', 'exit'], ['critic', 'scout'], ['critic', 'learning'],
  ['exit', 'overseer'], ['scout', 'overseer'], ['learning', 'overseer'],
];

const TIER_COLOR: Record<Tier, [number, number, number]> = {
  haiku: [68, 136, 255],   // info blue
  sonnet: [0, 204, 136],   // brand green
  opus: [170, 102, 255],   // purple
};

const rgba = (c: [number, number, number], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

export default function NeuralField({ regime, equityUp, lastAction, height = 520, className, style }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef({ regime, equityUp, lastAction });
  stateRef.current = { regime, equityUp, lastAction };

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const reduce = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

    let W = 0, H = 0, dpr = 1;
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = wrap.clientWidth;
      H = height;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    // Pixel position for a node given current size + gentle breathing drift.
    const pos = (n: Node, t: number) => {
      const driftX = Math.sin(t * 0.4 + n.phase) * (W * 0.008);
      const driftY = Math.cos(t * 0.33 + n.phase * 1.3) * (H * 0.012);
      // inset so labels/glow don't clip
      const px = W * (0.06 + n.x * 0.88) + driftX;
      const py = H * (0.10 + n.y * 0.80) + driftY;
      return [px, py] as const;
    };

    // Pulses travel along edges; a new one is emitted as the deliberation sweep advances.
    type Pulse = { edge: number; t: number; speed: number; color: [number, number, number] };
    let pulses: Pulse[] = [];
    let sweep = 0;         // which node is "active" (deliberating)
    let sweepClock = 0;

    let paused = false;
    const io = new IntersectionObserver(([e]) => { paused = !e.isIntersecting; }, { threshold: 0.01 });
    io.observe(wrap);

    let raf = 0;
    let last = 0;
    const FRAME = 1000 / 60;

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      if (paused) { last = now; return; }
      if (now - last < FRAME) return;
      last = now;
      const t = now / 1000;
      const { regime: rg, equityUp: up, lastAction: act } = stateRef.current;

      // Field tint: green when up / red when down, neutral otherwise.
      const tint: [number, number, number] =
        up === true ? [0, 204, 136] : up === false ? [255, 68, 102] : [0, 204, 136];

      ctx.clearRect(0, 0, W, H);

      // Ambient vignette glow at the core.
      const [cx, cy] = pos(NODES[3], t);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(W, H) * 0.6);
      g.addColorStop(0, rgba(tint, 0.05));
      g.addColorStop(1, 'rgba(5,5,8,0)');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);

      // Edges — faint living wires.
      ctx.lineWidth = 1;
      for (const [a, b] of EDGES) {
        const na = NODES.find((n) => n.id === a)!;
        const nb = NODES.find((n) => n.id === b)!;
        const [ax, ay] = pos(na, t);
        const [bx, by] = pos(nb, t);
        const breathe = 0.06 + 0.05 * (0.5 + 0.5 * Math.sin(t * 0.8 + na.phase));
        ctx.strokeStyle = rgba(tint, breathe);
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.stroke();
      }

      // Advance deliberation sweep + emit a pulse from the active node's outgoing edges.
      if (!reduce) {
        sweepClock += 1;
        if (sweepClock > 42) {
          sweepClock = 0;
          sweep = (sweep + 1) % NODES.length;
          const activeId = NODES[sweep].id;
          const actColor: [number, number, number] =
            act === 'SELL' ? [255, 68, 102] : act === 'BUY' ? [0, 204, 136] : tint;
          EDGES.forEach((e, i) => {
            if (e[0] === activeId) pulses.push({ edge: i, t: 0, speed: 0.02 + Math.random() * 0.015, color: actColor });
          });
        }
      }

      // Pulses.
      pulses = pulses.filter((p) => p.t <= 1);
      for (const p of pulses) {
        p.t += p.speed;
        const [a, b] = EDGES[p.edge];
        const na = NODES.find((n) => n.id === a)!;
        const nb = NODES.find((n) => n.id === b)!;
        const [ax, ay] = pos(na, t);
        const [bx, by] = pos(nb, t);
        const x = ax + (bx - ax) * p.t;
        const y = ay + (by - ay) * p.t;
        const fade = Math.sin(p.t * Math.PI); // in-out
        ctx.beginPath();
        ctx.arc(x, y, 2.4, 0, Math.PI * 2);
        ctx.fillStyle = rgba(p.color, 0.9 * fade);
        ctx.shadowColor = rgba(p.color, 0.8);
        ctx.shadowBlur = 10;
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      // Nodes — breathing glow, the active one flares.
      NODES.forEach((n, i) => {
        const [x, y] = pos(n, t);
        const c = TIER_COLOR[n.tier];
        const active = i === sweep;
        const pulseR = 0.5 + 0.5 * Math.sin(t * 1.6 + n.phase);
        const baseR = 4.2;
        const glowR = (active ? 26 : 14) + pulseR * 6;

        const rg2 = ctx.createRadialGradient(x, y, 0, x, y, glowR);
        rg2.addColorStop(0, rgba(c, active ? 0.55 : 0.32));
        rg2.addColorStop(1, rgba(c, 0));
        ctx.fillStyle = rg2;
        ctx.beginPath();
        ctx.arc(x, y, glowR, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.arc(x, y, baseR + (active ? 1.6 : 0), 0, Math.PI * 2);
        ctx.fillStyle = rgba(c, active ? 1 : 0.85);
        ctx.fill();
        ctx.strokeStyle = rgba([255, 255, 255], active ? 0.5 : 0.18);
        ctx.lineWidth = 1;
        ctx.stroke();

        // Labels
        ctx.font = `600 ${active ? 12 : 11}px "JetBrains Mono", ui-monospace, monospace`;
        ctx.fillStyle = rgba([240, 240, 245], active ? 0.95 : 0.5);
        ctx.textAlign = 'center';
        ctx.fillText(n.label, x, y - glowR - 4);
      });

      // Regime caption, bottom-left, if provided.
      if (rg) {
        ctx.font = '600 11px "JetBrains Mono", ui-monospace, monospace';
        ctx.textAlign = 'left';
        ctx.fillStyle = rgba([107, 107, 123], 0.9);
        ctx.fillText(`REGIME · ${String(rg).toUpperCase()}`, 14, H - 14);
      }
    };

    if (reduce) {
      // Single elegant static frame.
      draw(0);
    } else {
      raf = requestAnimationFrame(draw);
    }

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
    };
  }, [height]);

  return (
    <div ref={wrapRef} className={className} style={{ position: 'relative', width: '100%', height, ...style }}>
      <canvas ref={canvasRef} style={{ display: 'block' }} aria-hidden="true" />
    </div>
  );
}
