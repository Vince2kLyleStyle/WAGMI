import React, { useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';
import { C, fmtUsd, fmtPct } from '../src/theme';
import { useApi } from '../hooks/useApi';
import type { TradeRecord, LlmMarketView } from '../src/types';
import AnimatedNumber from '../components/AnimatedNumber';
import AgentBrainGraphic from '../components/AgentBrainGraphic';
import NeuralField from '../components/NeuralField';
import LiveActivityTape from '../components/LiveActivityTape';
import Shimmer from '../components/Shimmer';
import Icon from '../components/Icon';
import MarketPulse from '../components/MarketPulse';
import MetricSparkline from '../components/MetricSparkline';
import ReasoningTeaser from '../components/ReasoningTeaser';
import ProofStrip from '../components/ProofStrip';
import SystemStatus from '../components/SystemStatus';

// ─── Types ────────────────────────────────────────────────────────────────────

type SummaryApiResponse = {
  equity: number;
  peak_equity: number;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  open_positions: number;
  today_pnl: number;
  today_trades: number;
};

type RawTrade = {
  timestamp?: string;
  symbol: string;
  side: string;
  entry?: number;
  exit?: number;
  pnl: number;
  outcome: string;
  confidence?: number;
  leverage?: number;
  strategy?: string;
  regime?: string;
};

type TradeHistoryApiResponse = {
  trades: RawTrade[];
  total: number;
};

type EquityCurveApiResponse = {
  points: Array<{ ts: string; equity: number; pnl?: number }>;
};

type SummaryStats = {
  totalPnl: number | null;
  winRate: number | null;
  sharpe: number | null;
  maxDrawdown: number | null;
  totalTrades: number;
  equity: number | null;
  dailyPnl: number | null;
  openPositions: number;
};

// ─── Stat Card ────────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  numericValue,
  formatter,
  color,
  sub,
  loading,
  sparklineValues,
  sparklineColor,
}: {
  label: string;
  value?: string;
  numericValue?: number | null;
  formatter?: (n: number) => string;
  color?: string;
  sub?: string;
  loading?: boolean;
  sparklineValues?: number[];
  sparklineColor?: string;
}) {
  return (
    <div
      className="stat-card"
      style={{
        flex: '1 1 160px',
        padding: '20px 24px',
        background: 'rgba(13,13,20,0.8)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 12,
        minWidth: 140,
        transition: 'transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 600, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 26,
          fontWeight: 700,
          color: color || C.text,
          fontFamily: 'JetBrains Mono, monospace',
          letterSpacing: -0.5,
          lineHeight: 1.1,
          minHeight: 28,
        }}
      >
        {loading ? (
          <Shimmer width={96} height={24} radius={4} />
        ) : numericValue != null && formatter ? (
          <AnimatedNumber value={numericValue} format={formatter} duration={800} />
        ) : (
          value ?? '—'
        )}
      </div>
      {sub && !loading && (
        <div style={{ fontSize: 12, color: C.muted, marginTop: 4 }}>{sub}</div>
      )}
      {sparklineValues && sparklineValues.length > 1 && !loading && (
        <div style={{ marginTop: 10, opacity: 0.85 }}>
          <MetricSparkline
            values={sparklineValues}
            color={sparklineColor}
            height={22}
          />
        </div>
      )}
    </div>
  );
}

// ─── Mini Equity Curve SVG ────────────────────────────────────────────────────

function EquityCurveMini({ points }: { points: Array<{ equity: number }> }) {
  if (!points || points.length < 2) return null;
  const values = points.map((p) => p.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const w = 800;
  const h = 120;
  const pad = 4;
  const pts = values.map((v, i) => {
    const x = pad + (i / (values.length - 1)) * (w - pad * 2);
    const y = pad + ((max - v) / range) * (h - pad * 2);
    return `${x},${y}`;
  });
  const pathD = 'M' + pts.join(' L');
  const fillD = pathD + ` L${w - pad},${h - pad} L${pad},${h - pad} Z`;
  const isPositive = values[values.length - 1] >= values[0];
  const lineColor = isPositive ? C.bull : C.bear;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%' }}>
      <defs>
        <linearGradient id="ecGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={lineColor} stopOpacity="0.2" />
          <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={fillD} fill="url(#ecGrad)" />
      <path d={pathD} fill="none" stroke={lineColor} strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
}

// ─── Recent Trade Row ─────────────────────────────────────────────────────────

function TradeRow({ trade, index }: { trade: TradeRecord; index: number }) {
  const isWin = trade.outcome === 'WIN' || (trade.pnl ?? 0) > 0;
  const pnl = trade.pnl ?? 0;

  return (
    <div
      className={`fade-in-${Math.min(index + 1, 4)}`}
      style={{
        display: 'grid',
        gridTemplateColumns: '80px 56px 1fr 80px 70px',
        gap: 8,
        padding: '10px 16px',
        borderBottom: '1px solid rgba(255,255,255,0.04)',
        alignItems: 'center',
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text, fontFamily: 'JetBrains Mono, monospace' }}>
        {trade.symbol}
      </div>
      <div>
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            padding: '2px 7px',
            borderRadius: 999,
            background: trade.side === 'BUY' ? C.bullLight : C.bearLight,
            color: trade.side === 'BUY' ? C.bull : C.bear,
          }}
        >
          {trade.side}
        </span>
      </div>
      <div style={{ fontSize: 12, color: C.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {trade.strategy}
      </div>
      <div
        style={{
          fontSize: 13,
          fontWeight: 700,
          color: isWin ? C.bull : C.bear,
          textAlign: 'right',
          fontFamily: 'JetBrains Mono, monospace',
        }}
      >
        {fmtUsd(pnl)}
      </div>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          padding: '2px 7px',
          borderRadius: 999,
          background: isWin ? C.bullLight : C.bearLight,
          color: isWin ? C.bull : C.bear,
          textAlign: 'center',
        }}
      >
        {trade.outcome}
      </div>
    </div>
  );
}

// ─── Feature Card ─────────────────────────────────────────────────────────────

function FeatureCard({
  iconName,
  iconColor,
  title,
  desc,
}: {
  iconName: 'brain' | 'chart' | 'shield' | 'telegram';
  iconColor: string;
  title: string;
  desc: string;
}) {
  return (
    <div
      className="card-hover feature-card"
      style={{
        padding: '24px',
        background: '#0d0d14',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 12,
        flex: '1 1 220px',
        minWidth: 200,
        transition: 'transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease',
      }}
    >
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: 10,
          background: `${iconColor}15`,
          border: `1px solid ${iconColor}30`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: iconColor,
          marginBottom: 14,
        }}
      >
        <Icon name={iconName} size={20} strokeWidth={1.8} />
      </div>
      <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 8 }}>{title}</div>
      <div style={{ fontSize: 13, color: C.muted, lineHeight: 1.7 }}>{desc}</div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

function normalizeTrade(t: RawTrade): TradeRecord {
  const pnl = t.pnl ?? 0;
  return {
    symbol: t.symbol ?? '',
    side: t.side ?? '',
    strategy: t.strategy ?? 'ensemble',
    close_reason: '',
    entry: t.entry ?? null,
    exit: t.exit ?? null,
    sl: null,
    tp1: null,
    tp2: null,
    pnl,
    fee: null,
    leverage: t.leverage ?? null,
    confidence: t.confidence ?? null,
    rr_achieved: null,
    duration_h: null,
    outcome: t.outcome || (pnl > 0 ? 'WIN' : 'LOSS'),
    llm_action: null,
    llm_regime: t.regime ?? null,
    llm_confidence: null,
  };
}

export default function LandingPage() {
  const { data: summary } =
    useApi<SummaryApiResponse>('/v1/summary', { refreshInterval: 15_000 });
  const { data: tradesData, isLoading: tradesLoading } =
    useApi<TradeHistoryApiResponse>('/v1/trades/history?limit=50', { refreshInterval: 30_000 });
  const { data: equityData } =
    useApi<EquityCurveApiResponse>('/v1/trades/equity-curve', { refreshInterval: 30_000 });
  const { data: marketView } =
    useApi<LlmMarketView>('/v1/llm/market-view', { refreshInterval: 60_000 });

  const loading = tradesLoading && !tradesData;

  const trades: TradeRecord[] = ((tradesData?.trades ?? []).map(normalizeTrade)).slice(-6).reverse();
  const equityPoints = equityData?.points ?? [];

  // Compute Sharpe + MaxDD from equity curve
  let sharpe: number | null = null;
  let maxDrawdown: number | null = null;
  if (equityPoints.length > 5) {
    const returns = equityPoints.slice(1).map((p, i) => (p.equity - equityPoints[i].equity) / (equityPoints[i].equity || 1));
    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / returns.length;
    const std = Math.sqrt(variance);
    if (std > 0) sharpe = (mean / std) * Math.sqrt(365);
  }
  if (equityPoints.length > 1) {
    let peak = equityPoints[0].equity;
    let maxDD = 0;
    for (const p of equityPoints) {
      if (p.equity > peak) peak = p.equity;
      const dd = peak > 0 ? ((peak - p.equity) / peak) * 100 : 0;
      if (dd > maxDD) maxDD = dd;
    }
    maxDrawdown = maxDD;
  }

  const stats: SummaryStats = {
    totalPnl: summary?.total_pnl ?? null,
    winRate: summary?.win_rate ?? null,
    sharpe,
    maxDrawdown,
    totalTrades: summary?.total_trades ?? tradesData?.total ?? 0,
    equity: summary?.equity ?? null,
    dailyPnl: summary?.today_pnl ?? null,
    openPositions: summary?.open_positions ?? 0,
  };

  // Sparkline source data — derived from equity points (equity) + cumulative PnL (totalPnl) + rolling WR
  const equitySpark = equityPoints.slice(-30).map((p) => p.equity);
  const pnlSpark = (() => {
    if (equityPoints.length < 2) return [];
    const first = equityPoints[0].equity;
    return equityPoints.slice(-30).map((p) => p.equity - first);
  })();
  const rollingWR = (() => {
    if (!tradesData?.trades) return [];
    const tr = tradesData.trades.slice(-50);
    const out: number[] = [];
    const window = 10;
    for (let i = window; i <= tr.length; i++) {
      const chunk = tr.slice(i - window, i);
      const w = chunk.filter((t) => (t.pnl ?? 0) > 0).length;
      out.push((w / chunk.length) * 100);
    }
    return out;
  })();

  const biasColor = marketView?.overall_bias === 'bullish' ? C.bull
    : marketView?.overall_bias === 'bearish' ? C.bear
    : C.warn;

  return (
    <>
      <Head>
        <title>WAGMI — AI-Powered Perpetual Trading</title>
        <meta name="description" content="A quant engine that trades BTC, ETH, SOL, and HYPE on Hyperliquid perpetual futures." />
      </Head>

      <div style={{
        background: `
          radial-gradient(ellipse 800px 500px at 15% 8%, rgba(0,204,136,0.05) 0%, transparent 60%),
          radial-gradient(ellipse 700px 400px at 85% 22%, rgba(68,136,255,0.03) 0%, transparent 60%),
          radial-gradient(ellipse 600px 400px at 50% 85%, rgba(170,102,255,0.03) 0%, transparent 60%),
          #050508
        `,
        minHeight: '100vh',
        color: C.text,
        fontFamily: "'Inter', system-ui, sans-serif",
      }}>

        {/* ── Sticky Nav ─────────────────────────────────────────────────── */}
        <nav
          style={{
            position: 'sticky',
            top: 0,
            zIndex: 100,
            background: 'rgba(5,5,8,0.92)',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            backdropFilter: 'blur(12px)',
            WebkitBackdropFilter: 'blur(12px)',
          }}
        >
          <div
            style={{
              maxWidth: 1200,
              margin: '0 auto',
              padding: '0 24px',
              height: 60,
              display: 'flex',
              alignItems: 'center',
              gap: 32,
            }}
          >
            {/* Logo */}
            <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none' }}>
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 6,
                  border: `1.5px solid ${C.brand}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 11,
                  fontWeight: 800,
                  color: C.brand,
                  fontFamily: 'JetBrains Mono, monospace',
                  background: C.brandMuted,
                  flexShrink: 0,
                }}
              >
                W
              </div>
              <span style={{ fontSize: 15, fontWeight: 700, color: C.text, letterSpacing: -0.3 }}>
                WAGMI
              </span>
            </Link>

            {/* Desktop nav links */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 2, marginLeft: 8 }} className="desktop-nav">
              {['Performance', 'Brain', 'Learn'].map((label) => {
                const href = label === 'Performance' ? '/performance' : label === 'Brain' ? '/agent-intelligence' : '/learn';
                return (
                  <Link
                    key={label}
                    href={href}
                    className="nav-link"
                    style={{
                      padding: '6px 4px',
                      margin: '0 8px',
                      fontSize: 13,
                      fontWeight: 500,
                      color: C.muted,
                      textDecoration: 'none',
                      position: 'relative',
                      transition: 'color 0.15s ease',
                    }}
                  >
                    {label}
                  </Link>
                );
              })}
            </div>

            <div style={{ flex: 1 }} />

            {/* Live dot */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div
                className="live-dot"
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  background: C.bull,
                }}
              />
              <span style={{ fontSize: 11, fontWeight: 600, color: C.bull }}>LIVE</span>
            </div>

            {/* CTA */}
            <Link
              href="/dashboard"
              style={{
                padding: '8px 18px',
                background: C.brand,
                color: '#050508',
                borderRadius: 8,
                fontSize: 13,
                fontWeight: 700,
                textDecoration: 'none',
                transition: 'opacity 0.15s ease',
                flexShrink: 0,
              }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLAnchorElement).style.opacity = '0.85')}
              onMouseLeave={(e) => ((e.currentTarget as HTMLAnchorElement).style.opacity = '1')}
            >
              Dashboard →
            </Link>
          </div>
        </nav>

        {/* ── Live Activity Tape ─────────────────────────────────────────── */}
        <LiveActivityTape />

        {/* ── Hero ───────────────────────────────────────────────────────── */}
        <section style={{ position: 'relative', overflow: 'hidden', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
          {/* ── Living neural field: the 9-agent brain, breathing on live bot state ── */}
          <div style={{ position: 'absolute', inset: 0, zIndex: 0 }} aria-hidden="true">
            <NeuralField
              height={640}
              regime={(marketView as any)?.regime}
              equityUp={(summary?.total_pnl ?? 0) >= 0}
              lastAction={trades[0]?.side}
            />
            <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(90deg, rgba(5,5,8,0.95) 0%, rgba(5,5,8,0.72) 42%, rgba(5,5,8,0.18) 74%, rgba(5,5,8,0.5) 100%)' }} />
            <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(180deg, rgba(5,5,8,0.55) 0%, rgba(5,5,8,0) 24%, rgba(5,5,8,0) 66%, rgba(5,5,8,0.92) 100%)' }} />
          </div>
          {/* ── Hero content ── */}
          <div style={{ position: 'relative', zIndex: 1, maxWidth: 1200, margin: '0 auto', padding: '104px 24px 96px', minHeight: 640, display: 'flex', flexDirection: 'column', justifyContent: 'center' }} className="hero-content">
            <div style={{ maxWidth: 620 }}>
            {/* Live badge */}
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                padding: '5px 12px',
                background: 'rgba(0,204,136,0.08)',
                border: '1px solid rgba(0,204,136,0.2)',
                borderRadius: 999,
                marginBottom: 28,
              }}
            >
              <div
                className="live-dot"
                style={{ width: 6, height: 6, borderRadius: '50%', background: C.bull }}
              />
              <span style={{ fontSize: 12, fontWeight: 600, color: C.bull }}>
                Trading live on Hyperliquid
              </span>
            </div>

            <h1
              style={{
                fontSize: 'clamp(36px, 6vw, 62px)',
                fontWeight: 800,
                lineHeight: 1.08,
                letterSpacing: -1.5,
                color: C.text,
                margin: '0 0 20px',
              }}
            >
              AI-powered perpetual
              <br />
              <span style={{ color: C.brand }}>trading.</span>
            </h1>

            <p
              style={{
                fontSize: 17,
                lineHeight: 1.7,
                color: C.textSub,
                margin: '0 0 36px',
                maxWidth: 560,
              }}
            >
              A quant engine that trades BTC, ETH, SOL, and HYPE on Hyperliquid
              perpetual futures. Multi-strategy signals filtered through a{' '}
              <strong style={{ color: C.text, fontWeight: 600 }}>9-agent AI brain.</strong>
            </p>

            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <Link
                href="/dashboard"
                style={{
                  padding: '12px 28px',
                  background: C.brand,
                  color: '#050508',
                  borderRadius: 8,
                  fontSize: 14,
                  fontWeight: 700,
                  textDecoration: 'none',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLAnchorElement).style.opacity = '0.85')}
                onMouseLeave={(e) => ((e.currentTarget as HTMLAnchorElement).style.opacity = '1')}
              >
                View Dashboard
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M5 12h14M12 5l7 7-7 7" /></svg>
              </Link>
              <Link
                href="/learn"
                style={{
                  padding: '12px 28px',
                  background: 'transparent',
                  color: C.textSub,
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 8,
                  fontSize: 14,
                  fontWeight: 600,
                  textDecoration: 'none',
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.borderColor = 'rgba(255,255,255,0.2)'; (e.currentTarget as HTMLAnchorElement).style.color = C.text; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.borderColor = 'rgba(255,255,255,0.1)'; (e.currentTarget as HTMLAnchorElement).style.color = C.textSub; }}
              >
                Learn More
              </Link>
            </div>
          </div>

          </div>
        </section>

        {/* ── Stats Row ──────────────────────────────────────────────────── */}
        <section style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px 48px' }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <StatCard
              label="Equity"
              numericValue={stats.equity}
              formatter={(n) => fmtUsd(n)}
              color={C.text}
              sub="Live portfolio"
              loading={loading || stats.equity == null}
              sparklineValues={equitySpark}
            />
            <StatCard
              label="Today's P&L"
              numericValue={stats.dailyPnl}
              formatter={(n) => fmtUsd(n)}
              color={stats.dailyPnl == null ? C.text : stats.dailyPnl >= 0 ? C.bull : C.bear}
              sub="Since UTC midnight"
              loading={loading || stats.dailyPnl == null}
            />
            <StatCard
              label="Total P&L"
              numericValue={stats.totalPnl}
              formatter={(n) => fmtUsd(n)}
              color={(stats.totalPnl ?? 0) >= 0 ? C.bull : C.bear}
              sub={`${stats.totalTrades} trades`}
              loading={loading}
              sparklineValues={pnlSpark}
            />
            <StatCard
              label="Win Rate"
              numericValue={stats.winRate}
              formatter={(n) => fmtPct(n)}
              color={(stats.winRate ?? 0) >= 50 ? C.bull : C.bear}
              loading={loading}
              sparklineValues={rollingWR}
              sparklineColor={C.info}
            />
            <StatCard
              label="Open Positions"
              value={loading ? '—' : String(stats.openPositions)}
              color={stats.openPositions > 0 ? C.info : C.muted}
              sub="On exchange"
              loading={loading}
            />
          </div>
        </section>

        {/* ── Market Pulse ───────────────────────────────────────────────── */}
        <section style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px 32px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1.4, fontFamily: 'JetBrains Mono, monospace' }}>
              Market Pulse
            </div>
            <div style={{ fontSize: 11, color: C.muted }}>
              Updates every 20s
            </div>
          </div>
          <MarketPulse />
        </section>

        {/* ── Equity Curve ───────────────────────────────────────────────── */}
        <section style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px 48px' }}>
          <div
            style={{
              background: '#0d0d14',
              border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: 12,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                padding: '16px 20px',
                borderBottom: '1px solid rgba(255,255,255,0.04)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Equity Curve</div>
                <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>Portfolio value over time</div>
              </div>
              {equityPoints.length > 0 && (
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 700,
                    color: (equityPoints[equityPoints.length - 1]?.equity ?? 0) >= (equityPoints[0]?.equity ?? 0) ? C.bull : C.bear,
                    fontFamily: 'JetBrains Mono, monospace',
                  }}
                >
                  {fmtUsd(equityPoints[equityPoints.length - 1]?.equity)}
                </div>
              )}
            </div>
            <div style={{ height: 200, padding: '12px 0' }}>
              {equityPoints.length > 1 ? (
                <EquityCurveMini points={equityPoints} />
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: C.muted, fontSize: 13 }}>
                  {loading ? 'Loading...' : 'No equity data yet'}
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ── Recent Trades + AI Brain ────────────────────────────────────── */}
        <section style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px 56px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.5fr) minmax(0,1fr)', gap: 16 }}>

            {/* Recent Trades */}
            <div
              style={{
                background: '#0d0d14',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: 12,
                overflow: 'hidden',
              }}
            >
              <div style={{ padding: '16px 20px', borderBottom: '1px solid rgba(255,255,255,0.04)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Recent Trades</div>
                <Link href="/results" style={{ fontSize: 12, color: C.brand, textDecoration: 'none', fontWeight: 600 }}>
                  View all →
                </Link>
              </div>
              {/* Table header */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '80px 56px 1fr 80px 70px',
                  gap: 8,
                  padding: '8px 16px',
                  borderBottom: '1px solid rgba(255,255,255,0.04)',
                }}
              >
                {['Symbol', 'Side', 'Strategy', 'P&L', 'Result'].map((h) => (
                  <div key={h} style={{ fontSize: 10, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 0.8 }}>{h}</div>
                ))}
              </div>
              {loading ? (
                <div style={{ padding: '20px 16px', color: C.muted, fontSize: 13 }}>Loading...</div>
              ) : trades.length === 0 ? (
                <div style={{ padding: '24px 16px', color: C.muted, fontSize: 13, textAlign: 'center' }}>No trade history yet</div>
              ) : (
                trades.map((t, i) => <TradeRow key={i} trade={t} index={i} />)
              )}
            </div>

            {/* AI Brain Status */}
            <div
              style={{
                background: '#0d0d14',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: 12,
                overflow: 'hidden',
              }}
            >
              <div style={{ padding: '16px 20px', borderBottom: '1px solid rgba(255,255,255,0.04)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>AI Brain</div>
                <Link href="/agent-intelligence" style={{ fontSize: 12, color: C.brand, textDecoration: 'none', fontWeight: 600 }}>
                  Details →
                </Link>
              </div>

              <div style={{ padding: '20px' }}>
                {loading || !marketView ? (
                  <div style={{ color: C.muted, fontSize: 13 }}>Loading brain status...</div>
                ) : !marketView.has_data ? (
                  <div style={{ color: C.muted, fontSize: 13 }}>Brain data will appear once the bot runs.</div>
                ) : (
                  <>
                    {/* Regime */}
                    <div style={{ marginBottom: 20 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>
                        Market Regime
                      </div>
                      <div
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '6px 12px',
                          borderRadius: 8,
                          background: 'rgba(0,204,136,0.08)',
                          border: '1px solid rgba(0,204,136,0.15)',
                        }}
                      >
                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: C.bull }} />
                        <span style={{ fontSize: 13, fontWeight: 700, color: C.bull, textTransform: 'capitalize' }}>
                          {marketView.regime?.replace(/_/g, ' ') || 'Unknown'}
                        </span>
                      </div>
                    </div>

                    {/* Bias */}
                    <div style={{ marginBottom: 20 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>
                        Market Bias
                      </div>
                      <div
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                          padding: '4px 10px',
                          borderRadius: 999,
                          background: `${biasColor}15`,
                          border: `1px solid ${biasColor}30`,
                        }}
                      >
                        <span style={{ fontSize: 12, fontWeight: 700, color: biasColor, textTransform: 'capitalize' }}>
                          {marketView.overall_bias || 'Neutral'}
                        </span>
                      </div>
                    </div>

                    {/* Decision counts */}
                    <div style={{ marginBottom: 20 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>
                        Recent Decisions
                      </div>
                      <div style={{ display: 'flex', gap: 8 }}>
                        {[
                          { label: 'Proceed', value: marketView.decision_counts?.proceed ?? 0, color: C.bull },
                          { label: 'Skip', value: marketView.decision_counts?.flat ?? 0, color: C.muted },
                          { label: 'Flip', value: marketView.decision_counts?.flip ?? 0, color: C.warn },
                        ].map((d) => (
                          <div key={d.label} style={{ flex: 1, textAlign: 'center', padding: '8px 4px', background: 'rgba(255,255,255,0.03)', borderRadius: 8 }}>
                            <div style={{ fontSize: 18, fontWeight: 800, color: d.color, fontFamily: 'JetBrains Mono, monospace' }}>
                              {d.value}
                            </div>
                            <div style={{ fontSize: 10, color: C.muted, marginTop: 2, fontWeight: 600 }}>{d.label}</div>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Confidence */}
                    {marketView.avg_confidence != null && (
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>
                          Avg Confidence
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <div
                            style={{
                              flex: 1,
                              height: 4,
                              background: 'rgba(255,255,255,0.06)',
                              borderRadius: 999,
                              overflow: 'hidden',
                            }}
                          >
                            <div
                              style={{
                                height: '100%',
                                width: `${(marketView.avg_confidence * 100).toFixed(0)}%`,
                                background: C.brand,
                                borderRadius: 999,
                                transition: 'width 0.5s ease',
                              }}
                            />
                          </div>
                          <span style={{ fontSize: 13, fontWeight: 700, color: C.text, fontFamily: 'JetBrains Mono, monospace', flexShrink: 0 }}>
                            {(marketView.avg_confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* ── Latest Reasoning ───────────────────────────────────────────── */}
        <section style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px 56px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 16 }}>
            <div>
              <h2 style={{ fontSize: 22, fontWeight: 800, color: C.text, margin: '0 0 4px', letterSpacing: -0.5 }}>
                Latest Reasoning
              </h2>
              <p style={{ fontSize: 13, color: C.muted, margin: 0 }}>
                What the 9-agent brain is actually thinking, right now.
              </p>
            </div>
            <Link href="/reasoning" style={{ fontSize: 13, color: C.brand, textDecoration: 'none', fontWeight: 600, flexShrink: 0 }}>
              Full feed →
            </Link>
          </div>
          <ReasoningTeaser />
        </section>

        {/* ── Proof Strip — live lifetime metrics ───────────────────────── */}
        <section style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px 48px' }}>
          <div style={{ marginBottom: 14 }}>
            <div style={{
              fontSize: 11,
              fontWeight: 700,
              color: C.muted,
              textTransform: 'uppercase',
              letterSpacing: 1.4,
              fontFamily: 'JetBrains Mono, monospace',
              marginBottom: 6,
            }}>
              By the numbers
            </div>
            <div style={{ fontSize: 13, color: C.textSub, margin: 0 }}>
              Not a mockup. Every metric streams from the live bot.
            </div>
          </div>
          <ProofStrip />
        </section>

        {/* ── How It Works ───────────────────────────────────────────────── */}
        <section
          style={{
            maxWidth: 1200,
            margin: '0 auto',
            padding: '0 24px 64px',
          }}
        >
          <div style={{ marginBottom: 32 }}>
            <h2 style={{ fontSize: 28, fontWeight: 800, color: C.text, margin: '0 0 8px', letterSpacing: -0.5 }}>
              How It Works
            </h2>
            <p style={{ fontSize: 14, color: C.muted, margin: 0 }}>
              Three stages from raw market data to executed trade.
            </p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
            {[
              {
                step: '01',
                title: 'Signal Generation',
                desc: 'Four independent strategies scan BTC, ETH, SOL, and HYPE simultaneously using regime analysis, Monte Carlo zones, multi-timeframe quality scoring, and confidence models.',
                color: C.info,
              },
              {
                step: '02',
                title: 'AI Brain Analysis',
                desc: 'A 9-agent specialist pipeline — Regime, Trade, Risk, Critic, Learning, Exit, Scout, Overseer, and Quant — deliberates on every signal before any position opens.',
                color: C.brand,
              },
              {
                step: '03',
                title: 'Execution',
                desc: 'Positions are sized using Kelly Criterion, protected by circuit breakers, and managed through a state machine with progressive trailing stops and dynamic TP targets.',
                color: C.purple,
              },
            ].map((item) => (
              <div
                key={item.step}
                className="card-hover"
                style={{
                  padding: '24px',
                  background: '#0d0d14',
                  border: '1px solid rgba(255,255,255,0.06)',
                  borderRadius: 12,
                  position: 'relative',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    fontSize: 48,
                    fontWeight: 900,
                    color: `${item.color}10`,
                    position: 'absolute',
                    top: 12,
                    right: 16,
                    fontFamily: 'JetBrains Mono, monospace',
                    lineHeight: 1,
                    letterSpacing: -2,
                    userSelect: 'none',
                  }}
                >
                  {item.step}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: item.color,
                    textTransform: 'uppercase',
                    letterSpacing: 1.2,
                    marginBottom: 10,
                  }}
                >
                  Step {item.step}
                </div>
                <div style={{ fontSize: 16, fontWeight: 700, color: C.text, marginBottom: 10 }}>
                  {item.title}
                </div>
                <div style={{ fontSize: 13, color: C.muted, lineHeight: 1.7 }}>
                  {item.desc}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── Feature Cards ──────────────────────────────────────────────── */}
        <section
          style={{
            maxWidth: 1200,
            margin: '0 auto',
            padding: '0 24px 72px',
          }}
        >
          <div style={{ marginBottom: 32 }}>
            <h2 style={{ fontSize: 28, fontWeight: 800, color: C.text, margin: '0 0 8px', letterSpacing: -0.5 }}>
              Built for edge, not noise.
            </h2>
          </div>

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <FeatureCard
              iconName="brain"
              iconColor={C.brand}
              title="9-Agent AI Brain"
              desc="Every trade decision passes through a deliberation pipeline of specialist AI agents — regime analysis, thesis formation, risk assessment, critic review, and learning from outcomes."
            />
            <FeatureCard
              iconName="chart"
              iconColor={C.info}
              title="Multi-Strategy Ensemble"
              desc="Four independent strategies vote on every signal. Weighted-veto mode requires consensus with minimum agreement thresholds to block low-conviction trades."
            />
            <FeatureCard
              iconName="shield"
              iconColor={C.warn}
              title="Risk-First Design"
              desc="Six-stage signal gate, circuit breakers, Kelly-based position sizing, progressive trailing stops, and daily drawdown caps. Risk management is non-negotiable."
            />
            <FeatureCard
              iconName="telegram"
              iconColor={C.purple}
              title="Telegram Copilot"
              desc="Real-time trade alerts, AI brain summaries, and position updates delivered to Telegram. Full transparency on every decision the bot makes."
            />
          </div>
        </section>

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        <footer
          style={{
            borderTop: '1px solid rgba(255,255,255,0.06)',
            padding: '32px 24px',
          }}
        >
          <div
            style={{
              maxWidth: 1200,
              margin: '0 auto',
              display: 'flex',
              alignItems: 'flex-start',
              justifyContent: 'space-between',
              gap: 32,
              flexWrap: 'wrap',
            }}
          >
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <div style={{ width: 22, height: 22, borderRadius: 4, border: `1px solid ${C.brand}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 800, color: C.brand, fontFamily: 'JetBrains Mono, monospace', background: C.brandMuted }}>W</div>
                <span style={{ fontSize: 13, fontWeight: 700, color: C.text }}>WAGMI</span>
              </div>
              <p style={{ fontSize: 12, color: C.muted, lineHeight: 1.7, maxWidth: 380, margin: 0 }}>
                AI-driven market analysis for informational purposes only. Not financial advice.
                Crypto trading carries significant risk. Historical results do not predict future performance.
              </p>
            </div>

            <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>System</div>
                {[
                  { href: '/dashboard', label: 'Dashboard' },
                  { href: '/performance', label: 'Performance' },
                  { href: '/agent-intelligence', label: 'AI Brain' },
                  { href: '/backtest', label: 'Backtest' },
                ].map((link) => (
                  <Link key={link.href} href={link.href} style={{ display: 'block', fontSize: 13, color: C.muted, marginBottom: 6, textDecoration: 'none' }}
                    onMouseEnter={(e) => ((e.target as HTMLAnchorElement).style.color = C.text)}
                    onMouseLeave={(e) => ((e.target as HTMLAnchorElement).style.color = C.muted)}
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>Learn</div>
                {[
                  { href: '/learn', label: 'Course' },
                  { href: '/masterclass', label: 'Masterclass' },
                  { href: '/forensics', label: 'Forensics' },
                ].map((link) => (
                  <Link key={link.href} href={link.href} style={{ display: 'block', fontSize: 13, color: C.muted, marginBottom: 6, textDecoration: 'none' }}
                    onMouseEnter={(e) => ((e.target as HTMLAnchorElement).style.color = C.text)}
                    onMouseLeave={(e) => ((e.target as HTMLAnchorElement).style.color = C.muted)}
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
            </div>
          </div>

          <div
            style={{
              maxWidth: 1200,
              margin: '20px auto 0',
              paddingTop: 20,
              borderTop: '1px solid rgba(255,255,255,0.04)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 16,
              flexWrap: 'wrap',
            }}
          >
            <SystemStatus />
            <div style={{ fontSize: 11, color: C.faint }}>
              &copy; 2026 WAGMI · wagmi.trading
            </div>
          </div>
        </footer>

        <style>{`
          @media (max-width: 960px) {
            .hero-grid { grid-template-columns: 1fr !important; gap: 28px !important; }
            .hero-graphic { order: 2; }
          }
          @media (max-width: 768px) {
            .desktop-nav { display: none !important; }
          }
          @media (max-width: 640px) {
            section { padding-left: 16px !important; padding-right: 16px !important; }
          }
          @media (max-width: 380px) {
            section { padding-left: 14px !important; padding-right: 14px !important; }
            h1 { font-size: 34px !important; }
          }
          .nav-link {
            position: relative;
          }
          .nav-link::after {
            content: '';
            position: absolute;
            left: 0;
            right: 0;
            bottom: -4px;
            height: 1.5px;
            background: ${C.brand};
            transform: scaleX(0);
            transform-origin: left center;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          }
          .nav-link:hover {
            color: ${C.text} !important;
          }
          .nav-link:hover::after {
            transform: scaleX(1);
          }
          .stat-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255,255,255,0.12) !important;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4), 0 0 0 1px rgba(0,204,136,0.08);
          }
          .feature-card:hover {
            transform: translateY(-3px);
            border-color: rgba(255,255,255,0.14) !important;
            box-shadow: 0 10px 28px rgba(0,0,0,0.5);
          }
          .live-dot {
            box-shadow: 0 0 12px rgba(0,204,136,0.6);
            animation: live-pulse 2s ease-in-out infinite;
          }
          @keyframes live-pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(0.9); }
          }
          @keyframes hero-float {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-4px); }
          }
          .hero-graphic > div {
            animation: hero-float 6s ease-in-out infinite;
          }
        `}</style>
      </div>
    </>
  );
}

// No sidebar layout for landing page
LandingPage.noLayout = true;
