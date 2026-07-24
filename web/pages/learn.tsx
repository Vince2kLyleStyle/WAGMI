'use client';

import React, { useState, useId } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { C, R, S, F, G, Glass, SP } from '../src/theme';
import { staggerContainer, fadeUp } from '../src/animations';
import { useApi } from '../hooks/useApi';

// ─── Living Case Studies — real lessons the bot has learned, from /v1/lessons ───

type LessonsData = {
  insights: { category?: string; insight?: string; confidence?: number; evidence?: string; source?: string; validated?: boolean }[];
  rules: { hypothesis?: string; conditions?: Record<string, unknown>; action?: string; confidence?: number; evidence_ratio?: number; total_evidence?: number; active?: boolean; retired_reason?: string | null }[];
  trades: { symbol?: string; side?: string; pnl?: number; outcome?: string; strategy?: string; regime?: string }[];
  stats?: { total_insights?: number; validated_insights?: number; by_category?: Record<string, number>; total_rules?: number; active_rules?: number; total_trades?: number };
};

// The bot's journal stores some mis-encoded unicode (â‰¤ etc.); tidy the worst offenders.
function tidy(s?: string): string {
  if (!s) return '';
  return s
    .replace(/â‰¤/g, '≤').replace(/â‰¥/g, '≥').replace(/â†'/g, '→').replace(/â†"/g, '↓')
    .replace(/âˆ'/g, '−').replace(/â€"/g, '—').replace(/â€™/g, '’').replace(/Ã—/g, '×')
    .replace(/[�]/g, '').trim();
}

const CAT_LABEL: Record<string, string> = {
  exit_pattern: 'Exit timing',
  trade_management: 'Trade management',
  meta: 'Meta',
  prediction: 'Prediction',
};

function LiveCaseStudies() {
  const { data } = useApi<LessonsData>('/v1/lessons?limit=24', { refreshInterval: 120_000 });
  const st = data?.stats;
  const insights = (data?.insights || []).filter((i) => tidy(i.insight)).slice(0, 6);
  const rules = (data?.rules || []).filter((r) => r.hypothesis).slice(0, 4);
  const trades = (data?.trades || []).filter((t) => t.symbol).slice(0, 4);
  const loading = !data;

  const stat = (n: number | undefined, label: string, color = C.text) => (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: F['2xl'], fontWeight: 800, color, fontFamily: 'JetBrains Mono, monospace', lineHeight: 1 }}>
        {n ?? '—'}
      </div>
      <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
    </div>
  );

  return (
    <section style={{ margin: '8px 0 40px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: C.brand, boxShadow: `0 0 0 4px ${C.brand}22` }} className="live-dot" />
        <span style={{ fontSize: F.xs, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', color: C.brand }}>
          Live · from real trading
        </span>
      </div>
      <h2 style={{ fontSize: F['3xl'], fontWeight: 800, color: C.text, margin: '0 0 6px', letterSpacing: -0.5 }}>
        What the bot has actually learned
      </h2>
      <p style={{ fontSize: F.md, color: C.textSub, margin: '0 0 20px', maxWidth: 640, lineHeight: 1.6 }}>
        Not textbook hypotheticals — these are the real lessons the engine extracted from its own trades,
        the hypotheses it has tested, and the trades that taught them. Updated live from the bot.
      </p>

      {/* Stat band */}
      <div
        style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 150px), 1fr))', gap: 12,
          padding: '18px 20px', marginBottom: 22, borderRadius: R.lg,
          border: `1px solid ${C.border}`, background: C.surface,
        }}
      >
        {stat(st?.total_trades, 'Real trades')}
        {stat(st?.total_insights, 'Lessons logged', C.brand)}
        {stat(st?.validated_insights, 'Validated', C.bull)}
        {stat(st?.active_rules, 'Active rules', C.info)}
      </div>

      {loading && <div style={{ color: C.muted, fontSize: F.sm, padding: '8px 0 24px' }}>Loading the bot's lessons…</div>}

      {/* Insights */}
      {insights.length > 0 && (
        <>
          <h3 style={{ fontSize: F.lg, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>Lessons from the journal</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12, marginBottom: 28 }}>
            {insights.map((i, idx) => (
              <div key={idx} style={{ ...Glass.crystal, border: `1px solid ${C.border}`, borderRadius: R.lg, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: F.xs, fontWeight: 700, padding: '2px 8px', borderRadius: R.pill, background: C.brand + '22', color: C.brand }}>
                    {CAT_LABEL[i.category || ''] || i.category || 'Lesson'}
                  </span>
                  {i.validated && (
                    <span style={{ fontSize: F.xs, fontWeight: 700, padding: '2px 8px', borderRadius: R.pill, background: C.bull + '22', color: C.bull }}>✓ validated</span>
                  )}
                  <span style={{ marginLeft: 'auto', fontSize: F.xs, color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>
                    {Math.round((i.confidence || 0) * 100)}%
                  </span>
                </div>
                <p style={{ margin: 0, fontSize: F.sm, color: C.textSub, lineHeight: 1.55 }}>{tidy(i.insight)}</p>
                {i.evidence && (
                  <p style={{ margin: 0, fontSize: F.xs, color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>{tidy(i.evidence)}</p>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Hypotheses */}
      {rules.length > 0 && (
        <>
          <h3 style={{ fontSize: F.lg, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>Hypotheses it has tested</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12, marginBottom: 28 }}>
            {rules.map((r, idx) => (
              <div key={idx} style={{ ...Glass.crystal, border: `1px solid ${C.border}`, borderRadius: R.lg, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <p style={{ margin: 0, fontSize: F.sm, color: C.text, fontWeight: 600, lineHeight: 1.5 }}>{tidy(r.hypothesis)}</p>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: F.xs }}>
                  {r.total_evidence != null && (
                    <span style={{ color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>n={r.total_evidence}</span>
                  )}
                  {r.evidence_ratio != null && (
                    <span style={{ color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>hit {Math.round((r.evidence_ratio || 0) * 100)}%</span>
                  )}
                  <span style={{ marginLeft: 'auto', fontWeight: 700, padding: '1px 7px', borderRadius: R.pill, background: (r.active ? C.bull : C.muted) + '22', color: r.active ? C.bull : C.muted }}>
                    {r.active ? 'active' : 'retired'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Trades */}
      {trades.length > 0 && (
        <>
          <h3 style={{ fontSize: F.lg, fontWeight: 700, color: C.text, margin: '0 0 12px' }}>Trades that taught them</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 150px), 1fr))', gap: 12 }}>
            {trades.map((t, idx) => {
              const win = (t.pnl || 0) >= 0;
              return (
                <div key={idx} style={{ ...Glass.crystal, border: `1px solid ${C.border}`, borderRadius: R.lg, padding: '14px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontSize: F.md, fontWeight: 800, color: C.text }}>{t.symbol}</span>
                    <span style={{ fontSize: F.xs, fontWeight: 700, color: t.side === 'SELL' ? C.bear : C.bull }}>{t.side}</span>
                  </div>
                  <div style={{ fontSize: F.lg, fontWeight: 800, color: win ? C.bull : C.bear, fontFamily: 'JetBrains Mono, monospace', margin: '6px 0 2px' }}>
                    {win ? '+' : ''}{(t.pnl || 0).toLocaleString(undefined, { style: 'currency', currency: 'USD' })}
                  </div>
                  <div style={{ fontSize: F.xs, color: C.muted }}>{t.strategy} · {t.regime}</div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

// ─── Accordion Card ───────────────────────────────────────────────────────────

function AccordionCard({
  title,
  badge,
  badgeColor,
  defaultOpen = false,
  children,
}: {
  title: string;
  badge?: string;
  badgeColor?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div
      className="card-hover glass-noise"
      style={{
        ...Glass.crystal,
        border: `1px solid ${open ? C.borderBright : C.border}`,
        borderRadius: R.lg,
        marginBottom: 12,
        overflow: 'hidden',
        transition: 'border-color 0.15s',
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 20px',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {badge && (
            <span
              style={{
                fontSize: F.xs,
                fontWeight: 700,
                padding: '2px 8px',
                borderRadius: R.pill,
                background: (badgeColor || C.brand) + '22',
                color: badgeColor || C.brand,
                flexShrink: 0,
              }}
            >
              {badge}
            </span>
          )}
          <span style={{ fontSize: F.md, fontWeight: 700, color: C.text }}>{title}</span>
        </div>
        <span style={{ color: C.muted, fontSize: 14, transition: 'transform 0.2s', transform: open ? 'rotate(180deg)' : 'rotate(0deg)', flexShrink: 0 }}>▼</span>
      </button>
      {open && (
        <div style={{ padding: '4px 20px 20px', fontSize: F.sm, color: C.textSub, lineHeight: 1.8 }}>
          {children}
        </div>
      )}
    </div>
  );
}

// ─── Info Box ─────────────────────────────────────────────────────────────────

function InfoBox({ children, color = C.info }: { children: React.ReactNode; color?: string }) {
  return (
    <div
      style={{
        padding: '12px 16px',
        background: color + '15',
        border: `1px solid ${color}33`,
        borderRadius: R.md,
        fontSize: F.sm,
        color: C.textSub,
        lineHeight: 1.7,
        marginBottom: 12,
      }}
    >
      {children}
    </div>
  );
}

// ─── Agent Pipeline SVG ───────────────────────────────────────────────────────

function AgentPipelineDiagram() {
  const agents = [
    { name: 'Regime', model: 'Haiku', color: C.info, desc: 'Market regime classification' },
    { name: 'Trade', model: 'Sonnet', color: C.brand, desc: 'Go / Skip / Flip decision' },
    { name: 'Risk', model: 'Haiku', color: C.warn, desc: 'Position sizing + risk flags' },
    { name: 'Critic', model: 'Sonnet', color: C.bear, desc: 'Stress-tests thesis, veto power' },
    { name: 'Learning', model: 'Haiku', color: C.bull, desc: 'Post-trade lessons (offline)' },
  ];

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, overflowX: 'auto', padding: '8px 0' }}>
      {agents.map((agent, i) => (
        <React.Fragment key={agent.name}>
          <div
            style={{
              background: agent.color + '18',
              border: `1px solid ${agent.color}55`,
              borderRadius: R.md,
              padding: '10px 14px',
              textAlign: 'center',
              minWidth: 100,
              flexShrink: 0,
            }}
          >
            <div style={{ fontSize: F.sm, fontWeight: 700, color: agent.color, marginBottom: 2 }}>{agent.name}</div>
            <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 4 }}>{agent.model}</div>
            <div style={{ fontSize: 10, color: C.textSub, lineHeight: 1.4 }}>{agent.desc}</div>
          </div>
          {i < agents.length - 1 && (
            <div style={{ flexShrink: 0, padding: '0 4px', color: C.muted, fontSize: 16 }}>→</div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

// ─── Gate Flow SVG ────────────────────────────────────────────────────────────

function GateFlowDiagram() {
  const gates = [
    { n: 1, label: 'Validity', desc: 'SL width ≥ 0.3%, proper direction', color: C.info },
    { n: 2, label: 'Circuit Breaker', desc: 'No daily loss limit breach', color: C.brand },
    { n: 3, label: 'Position Limits', desc: 'Max open positions not exceeded', color: C.warn },
    { n: 4, label: 'Leverage', desc: 'Calculated leverage within safe range', color: C.purple },
    { n: 5, label: 'Liquidation', desc: 'Liquidation price buffer adequate', color: C.bear },
    { n: 6, label: 'Sizing', desc: 'Position size within risk limits', color: C.bull },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {/* Signal in */}
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 4 }}>
        <div style={{ padding: '6px 20px', background: C.surfaceHover, borderRadius: R.pill, fontSize: F.sm, fontWeight: 700, color: C.textSub }}>
          📡 Signal Generated
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 4, color: C.muted }}>↓</div>

      {gates.map((gate, i) => (
        <React.Fragment key={gate.n}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 28, height: 28, borderRadius: '50%', background: gate.color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: F.xs, fontWeight: 800, color: '#fff', flexShrink: 0 }}>
              {gate.n}
            </div>
            <div style={{ flex: 1, padding: '8px 12px', background: gate.color + '12', border: `1px solid ${gate.color}33`, borderRadius: R.sm }}>
              <div style={{ fontSize: F.sm, fontWeight: 700, color: gate.color }}>{gate.label}</div>
              <div style={{ fontSize: F.xs, color: C.muted }}>{gate.desc}</div>
            </div>
            <div style={{ fontSize: F.xs, padding: '3px 8px', background: C.bull + '18', color: C.bull, borderRadius: R.pill, fontWeight: 700 }}>✓ PASS</div>
          </div>
          {i < gates.length - 1 && (
            <div style={{ display: 'flex', justifyContent: 'center', margin: '2px 0', color: C.muted }}>↓</div>
          )}
        </React.Fragment>
      ))}

      {/* Trade out */}
      <div style={{ display: 'flex', justifyContent: 'center', marginTop: 4, marginBottom: 4, color: C.muted }}>↓</div>
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <div style={{ padding: '8px 24px', background: C.bull + '22', border: `1px solid ${C.bull}44`, borderRadius: R.pill, fontSize: F.sm, fontWeight: 700, color: C.bull }}>
          ✅ Trade Executed
        </div>
      </div>
    </div>
  );
}

// ─── Regime Table ─────────────────────────────────────────────────────────────

function RegimeTable() {
  const regimes = [
    { name: 'trend', emoji: '📈', desc: 'Price moving strongly in one direction', behaviour: 'Bot favours momentum trades. Buy dips in uptrend, sell rallies in downtrend.', risk: 'Low-Med' },
    { name: 'range', emoji: '↔️', desc: 'Price bouncing between support and resistance', behaviour: 'Bot waits for extreme zones. Mean-reversion setups only.', risk: 'Low' },
    { name: 'panic', emoji: '🔴', desc: 'Rapid, disorderly sell-off or flash crash', behaviour: 'Bot pauses or uses very tight sizing. High risk of slippage.', risk: 'Very High' },
    { name: 'high_volatility', emoji: '⚡', desc: 'Expanded ranges, fast candles, unpredictable', behaviour: 'Wider stops required. Bot reduces confidence thresholds.', risk: 'High' },
    { name: 'low_liquidity', emoji: '💧', desc: 'Thin order book, large spread', behaviour: 'Bot reduces position size to avoid slippage impact.', risk: 'Med-High' },
    { name: 'news_dislocation', emoji: '📰', desc: 'Price moved by news event, not technicals', behaviour: 'Bot waits for dust to settle before re-entering.', risk: 'Unpredictable' },
  ];

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: F.sm }}>
        <thead>
          <tr style={{ background: C.surface }}>
            {['Regime', 'What it means', 'Bot behaviour', 'Risk'].map((h) => (
              <th key={h} style={{ padding: '10px 12px', textAlign: 'left', fontSize: F.xs, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, borderBottom: `1px solid ${C.border}` }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {regimes.map((r, i) => (
            <tr key={r.name} style={{ borderBottom: `1px solid ${C.border}`, background: i % 2 ? C.surfaceHover + '40' : 'transparent' }}>
              <td style={{ padding: '10px 12px', fontWeight: 700, color: C.text, whiteSpace: 'nowrap' }}>
                {r.emoji} {r.name}
              </td>
              <td style={{ padding: '10px 12px', color: C.textSub }}>{r.desc}</td>
              <td style={{ padding: '10px 12px', color: C.muted }}>{r.behaviour}</td>
              <td style={{ padding: '10px 12px', fontWeight: 700, color: r.risk === 'Very High' || r.risk === 'Unpredictable' ? C.bear : r.risk.includes('High') ? C.warn : C.bull }}>
                {r.risk}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Position Size Calculator ─────────────────────────────────────────────────

function PositionSizeCalc() {
  const [accountSize, setAccountSize] = useState(10000);
  const [riskPct, setRiskPct] = useState(1.5);
  const [entry, setEntry] = useState(95000);
  const [stopLoss, setStopLoss] = useState(94200);

  const safeAccountSize = isNaN(accountSize) || accountSize <= 0 ? 0 : accountSize;
  const safeEntry = isNaN(entry) || entry <= 0 ? 0 : entry;
  const safeStopLoss = isNaN(stopLoss) || stopLoss <= 0 ? 0 : stopLoss;
  const riskDollars = (safeAccountSize * riskPct) / 100;
  const stopDist = Math.abs(safeEntry - safeStopLoss);
  const stopDistPct = safeEntry > 0 ? (stopDist / safeEntry) * 100 : 0;
  const positionSize = stopDist > 0 ? riskDollars / stopDist : 0;
  const notionalValue = positionSize * safeEntry;
  const leverage = safeAccountSize > 0 ? notionalValue / safeAccountSize : 0;

  const inputStyle: React.CSSProperties = {
    padding: '8px 12px', background: C.surfaceHover, border: `1px solid ${C.border}`,
    borderRadius: R.sm, color: C.text, fontSize: F.sm, width: '100%', outline: 'none',
    fontVariantNumeric: 'tabular-nums',
  };
  const labelStyle: React.CSSProperties = { fontSize: F.xs, color: C.muted, fontWeight: 600, marginBottom: 4 };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      <div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div>
            <div style={labelStyle}>Account Size ($)</div>
            <input type="number" aria-label="Account size in dollars" style={inputStyle} value={accountSize} onChange={e => { const v = parseFloat(e.target.value); setAccountSize(isNaN(v) ? 0 : Math.max(0, v)); }} min={100} />
          </div>
          <div>
            <div style={labelStyle}>Risk Per Trade (%)</div>
            <input type="number" aria-label="Risk per trade percentage" style={inputStyle} value={riskPct} step={0.1} onChange={e => { const v = parseFloat(e.target.value); setRiskPct(isNaN(v) ? 0 : Math.max(0, Math.min(100, v))); }} min={0.1} max={10} />
          </div>
          <div>
            <div style={labelStyle}>Entry Price ($)</div>
            <input type="number" aria-label="Entry price in dollars" style={inputStyle} value={entry} onChange={e => { const v = parseFloat(e.target.value); setEntry(isNaN(v) ? 0 : Math.max(0, v)); }} min={0.01} />
          </div>
          <div>
            <div style={labelStyle}>Stop Loss Price ($)</div>
            <input type="number" aria-label="Stop loss price in dollars" style={inputStyle} value={stopLoss} onChange={e => { const v = parseFloat(e.target.value); setStopLoss(isNaN(v) ? 0 : Math.max(0, v)); }} min={0.01} />
          </div>
        </div>
        <div style={{ padding: '10px 14px', background: C.warn + '12', border: `1px solid ${C.warn}30`, borderRadius: R.sm, fontSize: F.xs, color: C.textSub }}>
          Stop distance: <strong style={{ color: C.warn }}>{stopDistPct.toFixed(2)}%</strong> = ${stopDist.toLocaleString(undefined, { maximumFractionDigits: 2 })} per unit
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {[
          { label: 'Max Dollar Risk', value: `$${riskDollars.toLocaleString(undefined, { maximumFractionDigits: 2 })}`, color: C.warn, desc: `${riskPct}% of account` },
          { label: 'Position Size', value: positionSize > 0 ? positionSize.toLocaleString(undefined, { maximumFractionDigits: 4 }) + ' units' : '—', color: C.text, desc: 'BTC / SOL / HYPE etc.' },
          { label: 'Notional Value', value: notionalValue > 0 ? `$${notionalValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—', color: C.info, desc: 'Total position exposure' },
          { label: 'Effective Leverage', value: leverage > 0 ? `${leverage.toFixed(1)}×` : '—', color: leverage > 10 ? C.bear : leverage > 5 ? C.warn : C.bull, desc: 'Notional ÷ account size' },
        ].map(({ label, value, color, desc }) => (
          <div key={label} style={{ padding: '12px 16px', background: C.surfaceHover, border: `1px solid ${C.border}`, borderRadius: R.md, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 2 }}>{label}</div>
              <div style={{ fontSize: F.xs, color: C.faint }}>{desc}</div>
            </div>
            <div style={{ fontSize: F.lg, fontWeight: 800, color }}>{value}</div>
          </div>
        ))}
        {leverage > 10 && (
          <div style={{ padding: '8px 12px', background: C.bear + '12', border: `1px solid ${C.bear}30`, borderRadius: R.sm, fontSize: F.xs, color: C.bearMid }}>
            ⚠ Leverage &gt;10× is high risk. Consider widening your stop or reducing position size.
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Risk/Reward Calculator ────────────────────────────────────────────────────

function RRCalc() {
  const [entry, setEntry] = useState(95000);
  const [sl, setSl] = useState(94200);
  const [tp1, setTp1] = useState(96400);
  const [tp2, setTp2] = useState(97800);

  const safeEntry = isNaN(entry) || entry <= 0 ? 0 : entry;
  const safeSl = isNaN(sl) || sl <= 0 ? 0 : sl;
  const safeTp1 = isNaN(tp1) || tp1 <= 0 ? 0 : tp1;
  const safeTp2 = isNaN(tp2) || tp2 <= 0 ? 0 : tp2;
  const risk = Math.abs(safeEntry - safeSl);
  const reward1 = Math.abs(safeTp1 - safeEntry);
  const reward2 = Math.abs(safeTp2 - safeEntry);
  const rr1 = risk > 0 ? reward1 / risk : 0;
  const rr2 = risk > 0 ? reward2 / risk : 0;
  const slPct = safeEntry > 0 ? (risk / safeEntry) * 100 : 0;
  const tp1Pct = safeEntry > 0 ? (reward1 / safeEntry) * 100 : 0;
  const tp2Pct = safeEntry > 0 ? (reward2 / safeEntry) * 100 : 0;

  const barMax = Math.max(reward2, risk) || 1;

  const inputStyle: React.CSSProperties = {
    padding: '8px 12px', background: C.surfaceHover, border: `1px solid ${C.border}`,
    borderRadius: R.sm, color: C.text, fontSize: F.sm, width: '100%', outline: 'none',
  };
  const labelStyle: React.CSSProperties = { fontSize: F.xs, color: C.muted, fontWeight: 600, marginBottom: 4 };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div style={{ gridColumn: '1/-1' }}>
          <div style={labelStyle}>Entry Price ($)</div>
          <input type="number" aria-label="Entry price" style={inputStyle} value={entry} onChange={e => { const v = parseFloat(e.target.value); setEntry(isNaN(v) ? 0 : Math.max(0, v)); }} />
        </div>
        <div>
          <div style={labelStyle}>Stop Loss ($)</div>
          <input type="number" aria-label="Stop loss price" style={inputStyle} value={sl} onChange={e => { const v = parseFloat(e.target.value); setSl(isNaN(v) ? 0 : Math.max(0, v)); }} />
        </div>
        <div>
          <div style={labelStyle}>TP1 ($)</div>
          <input type="number" aria-label="Take profit 1 price" style={inputStyle} value={tp1} onChange={e => { const v = parseFloat(e.target.value); setTp1(isNaN(v) ? 0 : Math.max(0, v)); }} />
        </div>
        <div style={{ gridColumn: '1/-1' }}>
          <div style={labelStyle}>TP2 (Final Target) ($)</div>
          <input type="number" aria-label="Take profit 2 final target price" style={inputStyle} value={tp2} onChange={e => { const v = parseFloat(e.target.value); setTp2(isNaN(v) ? 0 : Math.max(0, v)); }} />
        </div>
      </div>

      <div>
        {/* Visual bar */}
        <div style={{ marginBottom: 16 }}>
          {[
            { label: 'Risk (SL)', dist: risk, pct: slPct, color: C.bear, side: 'down' },
            { label: 'TP1 Reward', dist: reward1, pct: tp1Pct, color: C.bullMid, side: 'up' },
            { label: 'TP2 Reward', dist: reward2, pct: tp2Pct, color: C.bull, side: 'up' },
          ].map(({ label, dist, pct, color }) => (
            <div key={label} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span style={{ fontSize: F.xs, color: C.muted }}>{label}</span>
                <span style={{ fontSize: F.xs, fontWeight: 700, color }}>{pct.toFixed(2)}%</span>
              </div>
              <div style={{ height: 8, background: C.border, borderRadius: R.pill, overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(100, (dist / barMax) * 100)}%`, height: '100%', background: color, borderRadius: R.pill }} />
              </div>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          {[
            { label: 'R:R to TP1', value: `${rr1.toFixed(2)}:1`, ok: rr1 >= 1 },
            { label: 'R:R to TP2', value: `${rr2.toFixed(2)}:1`, ok: rr2 >= 2 },
          ].map(({ label, value, ok }) => (
            <div key={label} style={{ flex: 1, padding: '10px 14px', background: (ok ? C.bull : C.bear) + '12', border: `1px solid ${(ok ? C.bull : C.bear)}30`, borderRadius: R.md, textAlign: 'center' }}>
              <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 2 }}>{label}</div>
              <div style={{ fontSize: F.xl, fontWeight: 800, color: ok ? C.bull : C.bear }}>{value}</div>
              <div style={{ fontSize: F.xs, color: ok ? C.bullMid : C.bearMid }}>{ok ? '✓ Good' : '✗ Too tight'}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Compound Growth Calculator ────────────────────────────────────────────────

function CompoundCalc() {
  const uid = useId().replace(/:/g, '');
  const gradId = `compGrad-${uid}`;
  const [capital, setCapital] = useState(10000);
  const [monthlyPct, setMonthlyPct] = useState(5);
  const [months, setMonths] = useState(12);

  const rows: { month: number; equity: number; gain: number }[] = [];
  let equity = capital > 0 ? capital : 1;
  for (let m = 1; m <= Math.min(months, 60); m++) {
    const gain = equity * (monthlyPct / 100);
    equity = Math.max(0, equity + gain);
    rows.push({ month: m, equity, gain });
  }
  const finalEquity = rows[rows.length - 1]?.equity ?? capital;
  const safeCapital = capital > 0 ? capital : 1;
  const totalReturn = ((finalEquity - safeCapital) / safeCapital) * 100;
  const maxEquity = finalEquity;

  const inputStyle: React.CSSProperties = {
    padding: '8px 12px', background: C.surfaceHover, border: `1px solid ${C.border}`,
    borderRadius: R.sm, color: C.text, fontSize: F.sm, width: '100%', outline: 'none',
  };
  const labelStyle: React.CSSProperties = { fontSize: F.xs, color: C.muted, fontWeight: 600, marginBottom: 4 };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 24 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <div style={labelStyle}>Starting Capital ($)</div>
          <input type="number" aria-label="Starting capital in dollars" style={inputStyle} value={capital} onChange={e => { const v = parseFloat(e.target.value); setCapital(isNaN(v) ? 0 : Math.max(0, v)); }} min={100} />
        </div>
        <div>
          <div style={labelStyle}>Monthly Return (%)</div>
          <input type="number" aria-label="Monthly return percentage" style={inputStyle} value={monthlyPct} step={0.5} onChange={e => { const v = parseFloat(e.target.value); setMonthlyPct(isNaN(v) ? 0 : Math.max(0, Math.min(100, v))); }} min={0.1} max={100} />
        </div>
        <div>
          <div style={labelStyle}>Number of Months</div>
          <input type="number" aria-label="Number of months" style={inputStyle} value={months} onChange={e => setMonths(Math.min(60, Math.max(1, +e.target.value)))} min={1} max={60} />
        </div>
        <div style={{ padding: '14px 16px', background: C.bull + '12', border: `1px solid ${C.bull}30`, borderRadius: R.md }}>
          <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 4 }}>Final Equity</div>
          <div style={{ fontSize: F['2xl'], fontWeight: 800, color: C.bull }}>{isFinite(finalEquity) ? `$${finalEquity.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}</div>
          <div style={{ fontSize: F.xs, color: C.bullMid }}>{isFinite(totalReturn) ? `${totalReturn >= 0 ? '+' : ''}${totalReturn.toFixed(1)}%` : '—'} total</div>
        </div>
      </div>

      {/* Mini chart */}
      <div>
        <svg width="100%" viewBox={`0 0 400 160`} style={{ display: 'block', overflow: 'visible' }}>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={C.bull} stopOpacity={0.3} />
              <stop offset="100%" stopColor={C.bull} stopOpacity={0} />
            </linearGradient>
          </defs>
          {rows.length >= 2 && (() => {
            const minE = capital;
            const rangeE = maxEquity - minE || 1;
            const W = 380, H = 130, padL = 0, padT = 10;
            const px = (i: number) => padL + (i / (rows.length - 1)) * W;
            const py = (e: number) => padT + H - ((e - minE) / rangeE) * H;
            const pts = rows.map((r, i) => `${px(i)},${py(r.equity)}`);
            const area = `M ${pts[0]} L ${pts.slice(1).join(' L ')} L ${px(rows.length - 1)},${padT + H} L ${padL},${padT + H} Z`;
            const line = pts.join(' ');
            return (
              <>
                <path d={area} fill={`url(#${gradId})`} />
                <polyline fill="none" stroke={C.bull} strokeWidth={2} points={line} strokeLinejoin="round" />
                {/* Start + end labels */}
                <text x={padL} y={padT + H + 16} fontSize={10} fill={C.muted}>${capital.toLocaleString()}</text>
                <text x={W} y={padT + 8} fontSize={10} fill={C.bullMid} textAnchor="end">${finalEquity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</text>
              </>
            );
          })()}
        </svg>
        <div style={{ fontSize: F.xs, color: C.muted, textAlign: 'center', marginTop: 4 }}>
          Month-by-month equity growth over {months} months
        </div>
      </div>
    </div>
  );
}

// ─── RSI Scale ────────────────────────────────────────────────────────────────

function RSIScale() {
  const W = 500, H = 80;
  // Zone boundaries in RSI units → pixel x
  const toX = (rsi: number) => (rsi / 100) * W;

  const zones = [
    { x1: toX(0),  x2: toX(30), fill: '#16a34a', label: 'OVERSOLD',  subLabel: 'Potential Long', labelX: toX(15)  },
    { x1: toX(30), x2: toX(45), fill: '#4ade80', label: '',           subLabel: '',               labelX: toX(37)  },
    { x1: toX(45), x2: toX(55), fill: '#6b7280', label: 'NEUTRAL',   subLabel: '',               labelX: toX(50)  },
    { x1: toX(55), x2: toX(70), fill: '#f87171', label: '',           subLabel: '',               labelX: toX(62)  },
    { x1: toX(70), x2: toX(100),fill: '#dc2626', label: 'OVERBOUGHT',subLabel: 'Potential Short', labelX: toX(85)  },
  ];

  const markers = [
    { rsi: 30, label: '30' },
    { rsi: 50, label: '50' },
    { rsi: 70, label: '70' },
  ];

  // Example dots
  const dots = [
    { rsi: 62, color: '#f87171', note: '62' },
    { rsi: 48, color: '#9ca3af', note: '48' },
    { rsi: 71, color: '#dc2626', note: '71 ▲' },
  ];

  return (
    <div style={{ marginTop: 20, marginBottom: 8 }}>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 }}>
        RSI Zone Map
      </div>
      <div style={{ overflowX: 'auto' }}>
        <svg width={W} height={H} style={{ display: 'block', minWidth: W }}>
          <defs>
            <linearGradient id="rsiGrad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%"   stopColor="#16a34a" />
              <stop offset="30%"  stopColor="#4ade80" />
              <stop offset="50%"  stopColor="#6b7280" />
              <stop offset="70%"  stopColor="#f87171" />
              <stop offset="100%" stopColor="#dc2626" />
            </linearGradient>
          </defs>

          {/* Gradient band */}
          <rect x={0} y={12} width={W} height={18} rx={4} fill="url(#rsiGrad)" opacity={0.85} />

          {/* Zone labels above band */}
          {zones.filter(z => z.label).map(z => (
            <text key={z.label} x={z.labelX} y={9} textAnchor="middle" fontSize={9} fontWeight={700} fill={C.text as string} opacity={0.75}>
              {z.label}
            </text>
          ))}

          {/* Marker ticks + labels below band */}
          {markers.map(m => (
            <g key={m.rsi}>
              <line x1={toX(m.rsi)} y1={12} x2={toX(m.rsi)} y2={30} stroke={C.text as string} strokeWidth={1.5} opacity={0.6} />
              {/* Small downward arrow */}
              <text x={toX(m.rsi)} y={42} textAnchor="middle" fontSize={10} fontWeight={700} fill={C.text as string} opacity={0.8}>
                {m.label}
              </text>
            </g>
          ))}

          {/* Sub-labels */}
          <text x={toX(15)}  y={58} textAnchor="middle" fontSize={9} fill="#4ade80">Potential Long</text>
          <text x={toX(50)}  y={58} textAnchor="middle" fontSize={9} fill={C.muted as string}>Neutral</text>
          <text x={toX(85)}  y={58} textAnchor="middle" fontSize={9} fill="#f87171">Potential Short</text>

          {/* Example RSI dots */}
          {dots.map(d => (
            <g key={d.rsi}>
              <circle cx={toX(d.rsi)} cy={21} r={5} fill={d.color} stroke="#0f172a" strokeWidth={1.5} />
              <text x={toX(d.rsi)} y={76} textAnchor="middle" fontSize={9} fill={d.color} fontWeight={700}>
                {d.note}
              </text>
            </g>
          ))}

          {/* "Current" label */}
          <text x={toX(71) + 8} y={18} fontSize={8} fill="#dc2626" fontWeight={700}>Current</text>
        </svg>
      </div>
      <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>
        Dots show example RSI readings. Red dots are in overbought territory — the bot reduces long conviction here.
      </div>
    </div>
  );
}

// ─── Confidence Gauge ─────────────────────────────────────────────────────────

function ConfidenceGauge({ value = 78 }: { value?: number }) {
  const cx = 100, cy = 100, r = 72;
  // Half-circle gauge: arc from 180° (left = 0) to 0° (right = 100)
  // We'll do a 200° sweep centered at bottom for readability
  const startDeg = 200, endDeg = 340; // total 200° sweep mapped to 0-100
  const totalDeg = startDeg; // degrees of sweep = 200
  // Actually: left anchor 200° from positive-x axis = 10 o'clock, right anchor at -20° = 4 o'clock
  // Let's use standard approach: arc from 215° to -35° (going counter-clockwise is 250°)
  // Simpler: start=-215deg end=35deg, sweep=250deg
  const arcStart = 215; // degrees from positive x-axis, going clockwise
  const arcSweep = 250; // total degrees

  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const arcPt = (deg: number) => ({
    x: cx + r * Math.cos(toRad(deg)),
    y: cy + r * Math.sin(toRad(deg)),
  });

  const startAngle = arcStart;
  const endAngle = arcStart + arcSweep; // 465 = 105

  // Build colored arc segments
  const segments = [
    { from: 0,  to: 50,  color: '#dc2626' },
    { from: 50, to: 65,  color: '#f97316' },
    { from: 65, to: 75,  color: '#eab308' },
    { from: 75, to: 85,  color: '#22c55e' },
    { from: 85, to: 100, color: '#16a34a' },
  ];

  const valueAngle = startAngle + (value / 100) * arcSweep;

  const describeArc = (fromVal: number, toVal: number) => {
    const a1 = toRad(startAngle + (fromVal / 100) * arcSweep);
    const a2 = toRad(startAngle + (toVal / 100) * arcSweep);
    const p1 = { x: cx + r * Math.cos(a1), y: cy + r * Math.sin(a1) };
    const p2 = { x: cx + r * Math.cos(a2), y: cy + r * Math.sin(a2) };
    const large = (toVal - fromVal) / 100 * arcSweep > 180 ? 1 : 0;
    return `M ${p1.x} ${p1.y} A ${r} ${r} 0 ${large} 1 ${p2.x} ${p2.y}`;
  };

  // Needle
  const needleAngle = toRad(valueAngle);
  const needleTip = { x: cx + (r - 8) * Math.cos(needleAngle), y: cy + (r - 8) * Math.sin(needleAngle) };
  const needleBase1 = { x: cx + 8 * Math.cos(needleAngle + Math.PI / 2), y: cy + 8 * Math.sin(needleAngle + Math.PI / 2) };
  const needleBase2 = { x: cx + 8 * Math.cos(needleAngle - Math.PI / 2), y: cy + 8 * Math.sin(needleAngle - Math.PI / 2) };

  const thresholds = [
    { val: 0,   label: '0'  },
    { val: 50,  label: '50' },
    { val: 65,  label: '65' },
    { val: 75,  label: '75' },
    { val: 85,  label: '85' },
    { val: 100, label: '100'},
  ];

  const labelColor = value >= 85 ? '#16a34a' : value >= 75 ? '#22c55e' : value >= 65 ? '#eab308' : value >= 50 ? '#f97316' : '#dc2626';

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap', marginTop: 20 }}>
      <div>
        <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 }}>
          Confidence Gauge — Example: {value}
        </div>
        <svg width={200} height={130} style={{ display: 'block', overflow: 'visible' }}>
          {/* Background track */}
          <path d={describeArc(0, 100)} fill="none" stroke={C.border as string} strokeWidth={14} strokeLinecap="round" />

          {/* Colored segments */}
          {segments.map(seg => (
            <path key={seg.from} d={describeArc(seg.from, seg.to)} fill="none" stroke={seg.color} strokeWidth={14} strokeLinecap="butt" opacity={0.9} />
          ))}

          {/* Threshold tick marks */}
          {thresholds.map(t => {
            const angle = toRad(startAngle + (t.val / 100) * arcSweep);
            const inner = { x: cx + (r - 20) * Math.cos(angle), y: cy + (r - 20) * Math.sin(angle) };
            const outer = { x: cx + (r - 8)  * Math.cos(angle), y: cy + (r - 8)  * Math.sin(angle) };
            const lx    = cx + (r - 28) * Math.cos(angle);
            const ly    = cy + (r - 28) * Math.sin(angle);
            return (
              <g key={t.val}>
                <line x1={inner.x} y1={inner.y} x2={outer.x} y2={outer.y} stroke={C.text as string} strokeWidth={1.5} opacity={0.5} />
                <text x={lx} y={ly + 3} textAnchor="middle" fontSize={8} fill={C.muted as string}>{t.label}</text>
              </g>
            );
          })}

          {/* Needle */}
          <polygon
            points={`${needleTip.x},${needleTip.y} ${needleBase1.x},${needleBase1.y} ${needleBase2.x},${needleBase2.y}`}
            fill={labelColor}
            opacity={0.9}
          />
          <circle cx={cx} cy={cy} r={6} fill={C.card as string} stroke={labelColor} strokeWidth={2} />

          {/* Center value label */}
          <text x={cx} y={cy + 26} textAnchor="middle" fontSize={22} fontWeight={800} fill={labelColor}>{value}</text>
          <text x={cx} y={cy + 38} textAnchor="middle" fontSize={9} fill={C.muted as string}>Confidence</text>
        </svg>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {[
          { range: '85–100', label: 'Strong Setup',    color: '#16a34a' },
          { range: '75–84',  label: 'Good Setup',      color: '#22c55e' },
          { range: '65–74',  label: 'Moderate',        color: '#eab308' },
          { range: '50–64',  label: 'Below Threshold', color: '#f97316' },
          { range: '0–49',   label: 'Weak / No Trade', color: '#dc2626' },
        ].map(row => (
          <div key={row.range} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ width: 10, height: 10, borderRadius: 2, background: row.color, flexShrink: 0 }} />
            <span style={{ fontSize: 11, fontWeight: 700, color: row.color, minWidth: 48 }}>{row.range}</span>
            <span style={{ fontSize: 11, color: C.muted }}>{row.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Strategy When-It-Shines Grid ─────────────────────────────────────────────

function StrategyShinesGrid() {
  type Rating = 'Excellent' | 'Good' | 'Poor' | 'N/A';
  const ratingColor: Record<Rating, string> = {
    'Excellent': C.bull,
    'Good':      C.info,
    'Poor':      C.bear,
    'N/A':       C.muted,
  };
  const ratingBg: Record<Rating, string> = {
    'Excellent': C.bull + '20',
    'Good':      C.info + '20',
    'Poor':      C.bear + '20',
    'N/A':       C.surfaceHover,
  };

  const strategies: { name: string; ratings: Rating[] }[] = [
    { name: 'Regime Trend',      ratings: ['Excellent', 'Excellent', 'Poor',      'Poor', 'Good']      },
    { name: 'Monte Carlo',       ratings: ['Good',      'Good',      'Excellent', 'Poor', 'Excellent'] },
    { name: 'Conf. Scorer',      ratings: ['Good',      'Good',      'Good',      'Good', 'Good']      },
    { name: 'Multi-Tier Quality',ratings: ['Excellent', 'Excellent', 'Good',      'Poor', 'Good']      },
  ];

  const conditions = ['Trending ↑', 'Trending ↓', 'Ranging', 'High Vol', 'Low Vol'];

  return (
    <div style={{ marginTop: 24, overflowX: 'auto' }}>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 10 }}>
        Strategy Performance by Market Condition
      </div>
      <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: 440 }}>
        <thead>
          <tr>
            <th style={{ padding: '6px 12px', textAlign: 'left', fontSize: 11, color: C.muted, fontWeight: 600, borderBottom: `1px solid ${C.border}` }}>
              Strategy
            </th>
            {conditions.map(c => (
              <th key={c} style={{ padding: '6px 10px', textAlign: 'center', fontSize: 11, color: C.muted, fontWeight: 600, borderBottom: `1px solid ${C.border}`, whiteSpace: 'nowrap' }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {strategies.map((s, si) => (
            <tr key={s.name} style={{ background: si % 2 ? C.surfaceHover + '40' : 'transparent' }}>
              <td style={{ padding: '8px 12px', fontWeight: 700, color: C.text, fontSize: 12, whiteSpace: 'nowrap', borderBottom: `1px solid ${C.border}` }}>
                {s.name}
              </td>
              {s.ratings.map((rating, ri) => (
                <td key={ri} style={{ padding: '8px 10px', textAlign: 'center', borderBottom: `1px solid ${C.border}` }}>
                  <span style={{
                    display: 'inline-block',
                    padding: '2px 8px',
                    borderRadius: R.pill,
                    background: ratingBg[rating],
                    color: ratingColor[rating],
                    fontSize: 11,
                    fontWeight: 700,
                    whiteSpace: 'nowrap',
                  }}>
                    {rating}
                  </span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 10, color: C.muted, marginTop: 6 }}>
        "Excellent" = strategy fires reliably and accurately in this condition. "Poor" = avoid using this strategy here.
      </div>
    </div>
  );
}

// ─── Volatility Cycle Diagram ──────────────────────────────────────────────────

function VolatilityCycleDiagram() {
  const W = 480, H = 120;
  const padL = 20, padR = 20, padT = 16, padB = 28;
  const iW = W - padL - padR;
  const iH = H - padT - padB;

  // Wave: squeeze (narrow) → breakout (wide) → trend → re-squeeze
  // We model the "band width" as a wave
  const pts: { x: number; y: number }[] = [];
  const N = 120;
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    // Amplitude: starts low (squeeze), explodes at ~0.35, stays high, decays
    let amp: number;
    if (t < 0.3) {
      amp = 0.08 + t * 0.1; // narrow, slight drift down
    } else if (t < 0.45) {
      amp = 0.08 + 0.1 * 0.3 + ((t - 0.3) / 0.15) * 0.6; // explosion
    } else if (t < 0.75) {
      amp = 0.68 - ((t - 0.45) / 0.3) * 0.25; // elevated trend
    } else {
      amp = 0.43 - ((t - 0.75) / 0.25) * 0.3; // re-compression
    }
    // Upper band
    const mid = padT + iH * 0.5;
    pts.push({ x: padL + t * iW, y: mid - amp * iH * 0.5 });
  }

  // Lower band (mirror)
  const ptsLow = pts.map(p => ({
    x: p.x,
    y: H - padB - (H - padB - padT - (p.y - padT)),
  }));

  const midLine = pts.map((p, i) => ({
    x: p.x,
    y: (p.y + ptsLow[i].y) / 2,
  }));

  const polyUpper = pts.map(p => `${p.x},${p.y}`).join(' ');
  const polyLower = ptsLow.map(p => `${p.x},${p.y}`).join(' ');
  const polyMid   = midLine.map(p => `${p.x},${p.y}`).join(' ');

  // Area between bands
  const areaPath = `M ${pts[0].x},${pts[0].y} L ${pts.map(p => `${p.x},${p.y}`).join(' L ')} L ${ptsLow[ptsLow.length-1].x},${ptsLow[ptsLow.length-1].y} L ${ptsLow.slice().reverse().map(p => `${p.x},${p.y}`).join(' L ')} Z`;

  // Phase annotations
  const phases = [
    { xPct: 0.15,  label: 'SQUEEZE',   sub: 'Bands narrow\nBot waits', color: C.info  },
    { xPct: 0.37,  label: 'BREAKOUT',  sub: 'Volume spike\nBot enters', color: C.bull  },
    { xPct: 0.60,  label: 'TREND',     sub: 'Ride the move\nTrail stop', color: C.brand },
    { xPct: 0.875, label: 'RE-COIL',   sub: 'Volatility drops\nReduce size', color: C.warn  },
  ];

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 }}>
        Volatility Cycle (Bollinger Band Width)
      </div>
      <div style={{ overflowX: 'auto' }}>
        <svg width={W} height={H} style={{ display: 'block', minWidth: W }}>
          <defs>
            <linearGradient id="volGrad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%"   stopColor={C.info  as string} stopOpacity={0.15} />
              <stop offset="35%"  stopColor={C.bull  as string} stopOpacity={0.20} />
              <stop offset="70%"  stopColor={C.brand as string} stopOpacity={0.18} />
              <stop offset="100%" stopColor={C.warn  as string} stopOpacity={0.12} />
            </linearGradient>
          </defs>

          {/* Band fill */}
          <path d={areaPath} fill="url(#volGrad)" />

          {/* Upper + lower band */}
          <polyline points={polyUpper} fill="none" stroke={C.bull as string}  strokeWidth={1.5} strokeDasharray="4 2" opacity={0.7} />
          <polyline points={polyLower} fill="none" stroke={C.bear as string}  strokeWidth={1.5} strokeDasharray="4 2" opacity={0.7} />

          {/* Mid line (price) */}
          <polyline points={polyMid} fill="none" stroke={C.text as string} strokeWidth={1.8} opacity={0.5} />

          {/* Phase vertical dividers */}
          {[0.3, 0.45, 0.75].map(xPct => (
            <line
              key={xPct}
              x1={padL + xPct * iW} y1={padT}
              x2={padL + xPct * iW} y2={H - padB}
              stroke={C.border as string} strokeWidth={1} strokeDasharray="3 3"
            />
          ))}

          {/* Phase labels */}
          {phases.map(ph => (
            <g key={ph.label}>
              <text x={padL + ph.xPct * iW} y={H - padB + 10} textAnchor="middle" fontSize={8} fontWeight={700} fill={ph.color as string}>
                {ph.label}
              </text>
            </g>
          ))}
        </svg>
      </div>

      {/* Phase cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginTop: 10 }}>
        {phases.map(ph => (
          <div key={ph.label} style={{ padding: '8px 10px', background: (ph.color as string) + '12', border: `1px solid ${ph.color as string}30`, borderRadius: R.sm }}>
            <div style={{ fontSize: 10, fontWeight: 800, color: ph.color as string, marginBottom: 3 }}>{ph.label}</div>
            <div style={{ fontSize: 10, color: C.muted, lineHeight: 1.5, whiteSpace: 'pre-line' }}>{ph.sub}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Trading Flow Diagram ─────────────────────────────────────────────────────

function TradingFlowDiagram() {
  const W = 750, H = 180;
  const nodeW = 80, nodeH = 36;
  const arrowW = 24;
  const totalNodes = 8; // Signal + 6 gates + Execute
  // Horizontal spacing: total width split across nodes + arrows
  const nodeCount = 8;
  const arrowCount = 7;
  const totalUsed = nodeCount * nodeW + arrowCount * arrowW;
  const padH = (W - totalUsed) / 2;
  const nodeY = 32; // top of nodes

  const nodes: { label: string; sub?: string; color: string; bg: string }[] = [
    { label: 'Signal', sub: 'Detected', color: C.info, bg: C.info + '22' },
    { label: 'Gate 1', sub: 'Valid?', color: C.brand, bg: C.brand + '18' },
    { label: 'Gate 2', sub: 'Circuit?', color: C.brand, bg: C.brand + '18' },
    { label: 'Gate 3', sub: 'Pos. Limits?', color: C.brand, bg: C.brand + '18' },
    { label: 'Gate 4', sub: 'Leverage?', color: C.brand, bg: C.brand + '18' },
    { label: 'Gate 5', sub: 'Liq. Price?', color: C.brand, bg: C.brand + '18' },
    { label: 'Gate 6', sub: 'Sizing?', color: C.brand, bg: C.brand + '18' },
    { label: 'EXECUTE', sub: '✓', color: '#fff', bg: C.bull },
  ];

  const xs: number[] = [];
  let cx = padH;
  for (let i = 0; i < nodeCount; i++) {
    xs.push(cx);
    cx += nodeW + arrowW;
  }

  const midY = nodeY + nodeH / 2;
  // REJECT box: branches down from Gate 1 (index 1)
  const rejectX = xs[1];
  const rejectY = nodeY + nodeH + 44;
  const rejectW = 72, rejectH = 32;

  return (
    <div style={{ overflowX: 'auto', marginTop: 16, marginBottom: 8 }}>
      <svg width={W} height={H} style={{ display: 'block', minWidth: W }}>
        <defs>
          <marker id="arrowHead" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill={C.muted as string} />
          </marker>
          <marker id="arrowHeadRej" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill={C.bear as string} />
          </marker>
        </defs>

        {/* Horizontal arrows between nodes */}
        {xs.slice(0, -1).map((x, i) => (
          <line
            key={i}
            x1={x + nodeW}
            y1={midY}
            x2={x + nodeW + arrowW}
            y2={midY}
            stroke={C.muted as string}
            strokeWidth={1.5}
            markerEnd="url(#arrowHead)"
          />
        ))}

        {/* REJECT branch: diagonal down from Gate 1 bottom-center */}
        <line
          x1={xs[1] + nodeW / 2}
          y1={nodeY + nodeH}
          x2={rejectX + rejectW / 2}
          y2={rejectY}
          stroke={C.bear as string}
          strokeWidth={1.5}
          strokeDasharray="4 3"
          markerEnd="url(#arrowHeadRej)"
        />

        {/* Node boxes */}
        {nodes.map((node, i) => (
          <g key={i}>
            <rect
              x={xs[i]}
              y={nodeY}
              width={nodeW}
              height={nodeH}
              rx={6}
              fill={node.bg}
              stroke={i === nodes.length - 1 ? C.bull : C.brand}
              strokeWidth={i === nodes.length - 1 ? 2 : 1}
            />
            <text
              x={xs[i] + nodeW / 2}
              y={nodeY + 14}
              textAnchor="middle"
              fontSize={9}
              fontWeight={700}
              fill={node.color}
            >
              {node.label}
            </text>
            {node.sub && (
              <text
                x={xs[i] + nodeW / 2}
                y={nodeY + 26}
                textAnchor="middle"
                fontSize={8}
                fill={i === nodes.length - 1 ? 'rgba(255,255,255,0.85)' : (C.muted as string)}
              >
                {node.sub}
              </text>
            )}
          </g>
        ))}

        {/* REJECT box */}
        <rect
          x={rejectX + (nodeW - rejectW) / 2}
          y={rejectY}
          width={rejectW}
          height={rejectH}
          rx={6}
          fill={C.bear + '22'}
          stroke={C.bear}
          strokeWidth={1.5}
        />
        <text
          x={rejectX + nodeW / 2}
          y={rejectY + 13}
          textAnchor="middle"
          fontSize={9}
          fontWeight={700}
          fill={C.bear as string}
        >
          REJECT ✗
        </text>
        <text
          x={rejectX + nodeW / 2}
          y={rejectY + 24}
          textAnchor="middle"
          fontSize={8}
          fill={C.muted as string}
        >
          Logged + skipped
        </text>

        {/* "Any gate fails" label */}
        <text
          x={rejectX + nodeW / 2 + 44}
          y={nodeY + nodeH + 20}
          fontSize={8}
          fill={C.muted as string}
          fontStyle="italic"
        >
          any gate fails →
        </text>
      </svg>
      <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>
        A signal must pass all 6 gates sequentially. Failure at any gate triggers rejection — the trade is logged but never executed.
      </div>
    </div>
  );
}

// ─── Risk/Reward Visualizer ────────────────────────────────────────────────────

function RiskRewardVisualizer() {
  const W = 400, H = 220;
  const axisX = 300; // price axis x position
  const entryY = H / 2; // entry price at center

  // Price levels relative to entry ($63,700 example BTC)
  const entryPrice = 63700;
  const tp1Pct = 0.02;   // +2%
  const tp2Pct = 0.035;  // +3.5%
  const slPct  = 0.01;   // -1%

  const tp1Price = entryPrice * (1 + tp1Pct);
  const tp2Price = entryPrice * (1 + tp2Pct);
  const slPrice  = entryPrice * (1 - slPct);

  const tp1Dollar = entryPrice * tp1Pct;
  const tp2Dollar = entryPrice * tp2Pct;
  const slDollar  = entryPrice * slPct;

  // Pixel mapping: 3.5% range maps to half the svg height
  const maxPct = 0.04;
  const pctToY = (pct: number) => entryY - (pct / maxPct) * (H * 0.42);

  const tp2Y = pctToY(tp2Pct);
  const tp1Y = pctToY(tp1Pct);
  const slY  = pctToY(-slPct);

  const barX = axisX - 40;
  const barW = 22;

  return (
    <div style={{ marginTop: 20, marginBottom: 8 }}>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 8 }}>
        Risk / Reward Visualizer — BTC Example @ ${entryPrice.toLocaleString()}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <svg width={W} height={H} style={{ display: 'block', minWidth: 320 }}>
          {/* TP2 zone (dark green) */}
          <rect x={barX} y={tp2Y} width={barW} height={tp1Y - tp2Y} fill={C.bull + 'cc'} rx={2} />
          {/* TP1 zone (green) */}
          <rect x={barX} y={tp1Y} width={barW} height={entryY - tp1Y} fill={C.bull + '66'} rx={2} />
          {/* SL zone (red) */}
          <rect x={barX} y={entryY} width={barW} height={slY - entryY} fill={C.bear + '66'} rx={2} />

          {/* Price axis line */}
          <line x1={axisX} y1={tp2Y - 10} x2={axisX} y2={slY + 10} stroke={C.border as string} strokeWidth={1.5} />

          {/* Entry line */}
          <line x1={barX - 6} y1={entryY} x2={axisX + 6} y2={entryY} stroke={C.info as string} strokeWidth={2} strokeDasharray="4 2" />
          {/* Entry dot (current price) */}
          <circle cx={barX + barW / 2} cy={entryY} r={6} fill={C.info as string} stroke={C.card as string} strokeWidth={2} />

          {/* TP1 line */}
          <line x1={barX - 4} y1={tp1Y} x2={axisX + 4} y2={tp1Y} stroke={C.bullMid as string} strokeWidth={1.5} />
          {/* TP2 line */}
          <line x1={barX - 4} y1={tp2Y} x2={axisX + 4} y2={tp2Y} stroke={C.bull as string} strokeWidth={1.5} />
          {/* SL line */}
          <line x1={barX - 4} y1={slY} x2={axisX + 4} y2={slY} stroke={C.bearMid as string} strokeWidth={1.5} />

          {/* Labels — right of axis */}
          <text x={axisX + 10} y={tp2Y + 4} fontSize={9} fill={C.bull as string} fontWeight={700}>TP2 +{(tp2Pct * 100).toFixed(1)}%</text>
          <text x={axisX + 10} y={tp2Y + 14} fontSize={8} fill={C.muted as string}>+${tp2Dollar.toLocaleString(undefined, { maximumFractionDigits: 0 })}</text>

          <text x={axisX + 10} y={tp1Y + 4} fontSize={9} fill={C.bullMid as string} fontWeight={700}>TP1 +{(tp1Pct * 100).toFixed(1)}%</text>
          <text x={axisX + 10} y={tp1Y + 14} fontSize={8} fill={C.muted as string}>+${tp1Dollar.toLocaleString(undefined, { maximumFractionDigits: 0 })}</text>

          <text x={axisX + 10} y={entryY + 4} fontSize={9} fill={C.info as string} fontWeight={700}>Entry</text>
          <text x={axisX + 10} y={entryY + 14} fontSize={8} fill={C.muted as string}>${entryPrice.toLocaleString()}</text>

          <text x={axisX + 10} y={slY + 4} fontSize={9} fill={C.bearMid as string} fontWeight={700}>Stop -{(slPct * 100).toFixed(1)}%</text>
          <text x={axisX + 10} y={slY + 14} fontSize={8} fill={C.muted as string}>-${slDollar.toLocaleString(undefined, { maximumFractionDigits: 0 })}</text>

          {/* R:R label — left side */}
          <text x={barX - 14} y={entryY - 28} fontSize={10} fill={C.bull as string} fontWeight={800} textAnchor="middle">R:R</text>
          <text x={barX - 14} y={entryY - 16} fontSize={11} fill={C.bull as string} fontWeight={900} textAnchor="middle">2:1</text>

          {/* EV label */}
          <text x={barX - 14} y={entryY + 18} fontSize={8} fill={C.muted as string} textAnchor="middle">EV</text>
          <text x={barX - 14} y={entryY + 28} fontSize={9} fill={C.bullMid as string} fontWeight={700} textAnchor="middle">+${(tp1Dollar * 0.5).toLocaleString(undefined, { maximumFractionDigits: 0 })}</text>
        </svg>
      </div>
      <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>
        TP1 closes ~50% of position. Stop moves to breakeven after TP1. TP2 captures the remainder. Expected value assumes 60% win rate.
      </div>
    </div>
  );
}

// ─── Strategy Voting Visual ───────────────────────────────────────────────────

function StrategyVotingVisual() {
  const strategies = [
    { abbr: 'RGM', name: 'Regime Trend', vote: 'BUY' as const },
    { abbr: 'MCZ', name: 'Monte Carlo Zones', vote: 'BUY' as const },
    { abbr: 'CSC', name: 'Confidence Scorer', vote: 'BUY' as const },
    { abbr: 'MTF', name: 'Multi-TF Quality', vote: 'SKIP' as const },
  ];

  const voteColor: Record<string, string> = {
    BUY:  C.bull,
    SKIP: C.muted,
    SELL: C.bear,
  };
  const voteBg: Record<string, string> = {
    BUY:  C.bull + '22',
    SKIP: C.surfaceHover,
    SELL: C.bear + '22',
  };

  const cols = ['BUY', 'SKIP', 'SELL'];

  return (
    <div style={{ marginTop: 20, marginBottom: 8 }}>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 10 }}>
        Weighted-Veto Ensemble — Sample Vote
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: 360 }}>
          <thead>
            <tr>
              <th style={{ padding: '6px 12px', textAlign: 'left', fontSize: 11, color: C.muted, fontWeight: 600, borderBottom: `1px solid ${C.border}` }}>
                Strategy
              </th>
              {cols.map(col => (
                <th key={col} style={{ padding: '6px 16px', textAlign: 'center', fontSize: 11, color: col === 'BUY' ? C.bull : col === 'SELL' ? C.bear : C.muted, fontWeight: 700, borderBottom: `1px solid ${C.border}` }}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {strategies.map((s, si) => (
              <tr key={s.abbr} style={{ background: si % 2 ? C.surfaceHover + '40' : 'transparent' }}>
                <td style={{ padding: '8px 12px', borderBottom: `1px solid ${C.border}` }}>
                  <span style={{ fontSize: 11, fontWeight: 800, color: C.text }}>{s.abbr}</span>
                  <span style={{ fontSize: 10, color: C.muted, marginLeft: 6 }}>{s.name}</span>
                </td>
                {cols.map(col => (
                  <td key={col} style={{ padding: '8px 16px', textAlign: 'center', borderBottom: `1px solid ${C.border}` }}>
                    {(s.vote as string) === col ? (
                      <span style={{
                        display: 'inline-block',
                        padding: '2px 10px',
                        borderRadius: R.pill,
                        background: voteBg[col],
                        color: voteColor[col],
                        fontSize: 11,
                        fontWeight: 800,
                        border: `1px solid ${voteColor[col]}44`,
                      }}>
                        {col === 'BUY' ? '▲' : col === 'SELL' ? '▼' : '—'} {col}
                      </span>
                    ) : (
                      <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: C.faint }} />
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Tally + result */}
      <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          {[
            { label: '3 BUY', color: C.bull },
            { label: '0 SELL', color: C.bear },
            { label: '1 SKIP', color: C.muted },
          ].map(({ label, color }) => (
            <span key={label} style={{ fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: R.pill, background: color + '18', color, border: `1px solid ${color}33` }}>
              {label}
            </span>
          ))}
        </div>
        <span style={{ color: C.muted, fontSize: 12 }}>→</span>
        <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 12px', borderRadius: R.pill, background: C.brand + '18', color: C.brand, border: `1px solid ${C.brand}44` }}>
          Signal PASSES minimum votes
        </span>
        <span style={{ color: C.muted, fontSize: 12 }}>→</span>
        <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 12px', borderRadius: R.pill, background: C.info + '18', color: C.info, border: `1px solid ${C.info}44` }}>
          AI review
        </span>
        <span style={{ color: C.muted, fontSize: 12 }}>→</span>
        <span style={{ fontSize: 11, fontWeight: 800, padding: '3px 12px', borderRadius: R.pill, background: C.bull + '22', color: C.bull, border: `1px solid ${C.bull}55` }}>
          ✓ EXECUTE
        </span>
      </div>
      <div style={{ marginTop: 8, fontSize: 10, color: C.muted, lineHeight: 1.6 }}>
        Weighted-Veto mode: each strategy votes independently. Minimum 2 agreements required. No SELL votes = no veto triggered. Signal advances to the LLM pipeline for final review.
      </div>
    </div>
  );
}

// ─── Memory System Diagram ────────────────────────────────────────────────────

function MemorySystemDiagram() {
  const W = 600, H = 170;

  const boxW = 190, boxH = 130;
  const leftX = 10, rightX = W - boxW - 10;
  const boxY = 20;
  const brainX = W / 2, brainY = boxY + boxH / 2;

  // Arrow endpoints (from brain to boxes and back)
  const leftEdge  = leftX + boxW;
  const rightEdge = rightX;
  const arrowGap  = 18;

  const shortNotes = [
    '• RSI was 71 at 14:30',
    '• BTC rejected $65k twice',
    '• Regime shifted to range',
    '• Trade skipped: low ATR',
  ];
  const longPatterns = [
    '• BTC breakout → +4.2% avg',
    '• High-vol: skip → +EV',
    '• Range + RSI<35 = long edge',
    '• After panic: wait 2h',
  ];

  return (
    <div style={{ marginTop: 20, marginBottom: 8 }}>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 10 }}>
        Memory Architecture — How the Bot Remembers
      </div>
      <div style={{ overflowX: 'auto' }}>
        <svg width={W} height={H} style={{ display: 'block', minWidth: W }}>
          {/* Short-term box */}
          <rect x={leftX} y={boxY} width={boxW} height={boxH} rx={8} fill={C.info + '12'} stroke={C.info + '55'} strokeWidth={1.5} />
          <text x={leftX + boxW / 2} y={boxY + 18} textAnchor="middle" fontSize={11} fontWeight={800} fill={C.info as string}>
            Short-Term Memory
          </text>
          <text x={leftX + boxW / 2} y={boxY + 30} textAnchor="middle" fontSize={9} fill={C.infoMid as string}>
            7-day TTL · 100 notes max
          </text>
          {shortNotes.map((note, i) => (
            <text key={i} x={leftX + 10} y={boxY + 50 + i * 17} fontSize={9} fill={C.textSub as string}>
              {note}
            </text>
          ))}

          {/* Long-term box */}
          <rect x={rightX} y={boxY} width={boxW} height={boxH} rx={8} fill={C.purple + '12'} stroke={C.purple + '55'} strokeWidth={1.5} />
          <text x={rightX + boxW / 2} y={boxY + 18} textAnchor="middle" fontSize={11} fontWeight={800} fill={C.purple as string}>
            Long-Term Memory
          </text>
          <text x={rightX + boxW / 2} y={boxY + 30} textAnchor="middle" fontSize={9} fill={C.purpleLight as string + '99'}>
            Trade DNA · Patterns · Rules
          </text>
          {longPatterns.map((note, i) => (
            <text key={i} x={rightX + 10} y={boxY + 50 + i * 17} fontSize={9} fill={C.textSub as string}>
              {note}
            </text>
          ))}

          {/* Brain icon (center) */}
          <circle cx={brainX} cy={brainY} r={26} fill={C.brand + '22'} stroke={C.brand + '66'} strokeWidth={2} />
          <text x={brainX} y={brainY - 4} textAnchor="middle" fontSize={18} fill={C.brand as string}>🤖</text>
          <text x={brainX} y={brainY + 16} textAnchor="middle" fontSize={8} fontWeight={700} fill={C.brand as string}>AI Brain</text>

          {/* Arrows: left box → brain (top arrow going right) */}
          <line
            x1={leftEdge}
            y1={brainY - arrowGap / 2}
            x2={brainX - 28}
            y2={brainY - arrowGap / 2}
            stroke={C.info as string}
            strokeWidth={1.5}
            markerEnd="url(#arrowInfo)"
          />
          {/* Brain → left box (bottom arrow going left) */}
          <line
            x1={brainX - 28}
            y1={brainY + arrowGap / 2}
            x2={leftEdge}
            y2={brainY + arrowGap / 2}
            stroke={C.info as string}
            strokeWidth={1.5}
            markerEnd="url(#arrowInfoBack)"
          />

          {/* Arrows: brain → right box (top arrow going right) */}
          <line
            x1={brainX + 28}
            y1={brainY - arrowGap / 2}
            x2={rightEdge}
            y2={brainY - arrowGap / 2}
            stroke={C.purple as string}
            strokeWidth={1.5}
            markerEnd="url(#arrowPurple)"
          />
          {/* Right box → brain (bottom arrow going left) */}
          <line
            x1={rightEdge}
            y1={brainY + arrowGap / 2}
            x2={brainX + 28}
            y2={brainY + arrowGap / 2}
            stroke={C.purple as string}
            strokeWidth={1.5}
            markerEnd="url(#arrowPurpleBack)"
          />

          <defs>
            <marker id="arrowInfo" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 Z" fill={C.info as string} />
            </marker>
            <marker id="arrowInfoBack" markerWidth="6" markerHeight="6" refX="1" refY="3" orient="auto">
              <path d="M6,0 L0,3 L6,6 Z" fill={C.info as string} />
            </marker>
            <marker id="arrowPurple" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 Z" fill={C.purple as string} />
            </marker>
            <marker id="arrowPurpleBack" markerWidth="6" markerHeight="6" refX="1" refY="3" orient="auto">
              <path d="M6,0 L0,3 L6,6 Z" fill={C.purple as string} />
            </marker>
          </defs>
        </svg>
      </div>
      <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>
        The AI brain reads from both memory stores each cycle and writes new observations after each decision. Long-term patterns graduate from short-term notes via the Learning Agent.
      </div>
    </div>
  );
}

// ─── Glossary ─────────────────────────────────────────────────────────────────

const GLOSSARY = [
  { term: 'ATR (Average True Range)', def: 'Measures how much an asset typically moves in a single candle. High ATR = high volatility. The bot uses ATR to size stop losses so they are proportional to actual market movement.' },
  { term: 'SMA (Simple Moving Average)', def: 'Average of the last N closing prices. SMA20 (fast) crosses above SMA50 (slow) = uptrend signal. SMA20 crosses below SMA50 = downtrend signal.' },
  { term: 'RSI (Relative Strength Index)', def: 'Momentum oscillator on a 0-100 scale. Above 70 = potentially overbought (caution on longs). Below 30 = potentially oversold (caution on shorts). The bot uses RSI as one confluence factor among many.' },
  { term: 'Confidence Score', def: 'The bot\'s internal 0-100 rating for how strong a setup is. Based on how many strategies agree and how strongly. Score ≥ 70 is considered a strong signal. The LLM adds its own confidence layer on top.' },
  { term: 'Regime', def: 'Market state classification (trend/range/panic/high_volatility/low_liquidity). Different regimes call for different strategies. The Regime Agent classifies this each evaluation cycle.' },
  { term: 'Ensemble', def: 'The process of combining votes from multiple strategies. The bot uses "weighted_veto" mode: strategies vote, but any strategy can veto a trade if conditions are too risky.' },
  { term: 'Veto', def: 'When the Critic agent (or a strategy) overrides a trade signal. Usually triggered by conflicting signals, high risk conditions, or poor risk/reward ratio.' },
  { term: 'Circuit Breaker', def: 'An automatic stop-trading mechanism that activates when losses exceed a daily threshold or a certain number of consecutive losses occur. Prevents catastrophic drawdowns.' },
  { term: 'Drawdown', def: 'The peak-to-trough decline in account equity. Max drawdown is the worst historical decline. Lower is better. The bot aims to keep max drawdown below 15%.' },
  { term: 'Profit Factor', def: 'Gross profit divided by gross loss. A ratio above 1.0 means the strategy makes more than it loses. 1.5× is good; 2.0× is excellent.' },
  { term: 'R:R (Risk/Reward Ratio)', def: 'How much profit you aim for vs how much you risk. The bot requires R:R ≥ 1.0 before entering. A 2:1 setup means potential gain is twice the potential loss.' },
  { term: 'Accumulation Zone', def: 'Price level below current price where the bot considers the asset "cheap" relative to its volatility bands. Good levels to consider longs.' },
  { term: 'Distribution Zone', def: 'Price level above current price where the bot considers the asset "expensive". Good levels to consider profit-taking or shorts.' },
  { term: 'Funding Rate', def: 'The cost of holding a perpetual futures position. Positive funding = longs pay shorts. Extreme funding rates signal crowded trades the bot may fade.' },
  { term: 'Liquidation Price', def: 'The price at which your leveraged position gets automatically closed by the exchange. The bot always checks that the stop loss is hit long before the liquidation price.' },
  { term: 'Trailing Stop', def: 'A stop loss that moves up with a winning trade (for longs). Locks in profit as price rises, closes only if price falls back by a set amount. The bot uses progressive trailing after TP1 is hit.' },
  { term: 'Sharpe Ratio', def: 'Return per unit of risk taken. Higher = better. Adjusts for volatility so a steady 10% return is rated better than an erratic 15% return.' },
  { term: 'Open Interest (OI)', def: 'Total number of outstanding futures contracts. Rising OI + rising price = strong trend. Rising OI + falling price = distribution. The bot monitors OI divergence as a signal.' },
  { term: 'Advisory Mode', def: 'The LLM operates in advisory mode by default: it analyses and logs what it would trade, but does NOT execute trades. Useful for monitoring AI judgement before giving it execution power.' },
  { term: 'Slippage', def: 'The difference between the price you expected to trade at and the price you actually got. Higher volatility and lower liquidity = more slippage. The bot accounts for 0.05-0.1% slippage in its cost model.' },
  { term: 'VWAP (Volume-Weighted Average Price)', def: 'The average price weighted by volume, resetting each day. Institutional traders use VWAP as a benchmark. Price above VWAP = bullish bias; below = bearish. The Multi-Tier Quality strategy anchors its entries to VWAP.' },
  { term: 'MACD (Moving Average Convergence Divergence)', def: 'A momentum indicator that shows the relationship between two EMAs. When the MACD line crosses above the signal line = bullish momentum. Used by the bot\'s Regime Trend strategy for trend confirmation.' },
  { term: 'Bollinger Bands', def: 'Volatility bands placed 2 standard deviations above and below a moving average. Price near upper band = overbought. Near lower band = oversold. When bands squeeze (narrow), a big move is typically coming.' },
  { term: 'Keltner Channel', def: 'Volatility envelope based on ATR. When Bollinger Bands are inside Keltner Channels, the market is in a "squeeze" — coiling energy before a breakout. The Confidence Scorer uses squeeze detection.' },
  { term: 'EMA (Exponential Moving Average)', def: 'Like SMA but gives more weight to recent prices, making it more responsive to current conditions. The bot uses EMA crossovers (short EMA crossing long EMA) as trend signals.' },
  { term: 'ADX (Average Directional Index)', def: 'Measures the strength of a trend, regardless of direction. ADX above 25 = strong trend; below 20 = range/choppy market. The bot uses ADX to avoid trading range-bound markets with trend strategies.' },
  { term: 'WaveTrend', def: 'A custom momentum oscillator that combines channel-based smoothing with momentum. The Regime Trend strategy uses WaveTrend to detect oversold/overbought conditions within the context of the current regime.' },
  { term: 'Paper Trading', def: 'Simulated trading with real market data but no real money. The bot runs in paper mode by default. All signals, decisions, and "trades" are executed in a virtual account to test performance before going live.' },
  { term: 'Live Trading', def: 'Trading with real capital on the exchange. Requires the bot to be configured with exchange API keys and LLM_MODE ≥ 1. Only activate after extended paper trading validation.' },
  { term: 'Entry Price', def: 'The price at which a trade position is opened. The bot calculates entry based on current market price at the time the signal is confirmed. For limit orders, the entry may differ slightly from the trigger price.' },
  { term: 'Take Profit (TP)', def: 'Pre-set price target where a portion of a position is closed to realize profit. The bot uses two targets: TP1 closes ~50% of the position and moves the stop to breakeven; TP2 is the final target for the remainder.' },
  { term: 'Breakeven Stop', def: 'Moving the stop loss to the entry price after TP1 is hit. This guarantees the trade cannot lose money on the remaining position. The bot does this automatically after TP1.' },
  { term: 'Confluence', def: 'When multiple independent signals all point in the same direction. High confluence = higher confidence trade. The bot requires at least 2 strategies to agree, plus regime alignment, before considering a trade.' },
  { term: 'Perpetual Futures (Perps)', def: 'Futures contracts with no expiry date. Unlike regular futures, they track the spot price via a funding rate mechanism. Hyperliquid trades are all perpetual futures, allowing both long and short positions with leverage.' },
  { term: 'Long / Short', def: 'Long = betting price goes up. Short = betting price goes down. Perpetual futures allow you to profit in both directions. The bot can take either direction depending on signal and regime.' },
  { term: 'Mark Price', def: 'The "fair" price of a futures contract, calculated using a weighted average of spot prices across exchanges. Used by Hyperliquid to calculate unrealized PnL and trigger liquidations — not the last traded price.' },
  { term: 'Margin', def: 'The collateral deposited to open and maintain a leveraged position. Cross margin shares your full account balance across positions. Isolated margin limits risk to a fixed amount per position. The bot uses isolated margin.' },
  { term: 'Unrealized PnL', def: 'The profit or loss on an open position if it were closed at the current price. Shown in the dashboard as "open position PnL." Not banked until the position closes.' },
  { term: 'Realized PnL', def: 'Profit or loss that has been locked in by closing a position. This is what counts for your account equity.' },
  { term: 'Basis Points (bps)', def: '1 basis point = 0.01%. Used to express small percentage changes. A 50bps stop loss = 0.5% below entry. Fees are often quoted in basis points (e.g. 3.5bps maker fee = 0.035%).' },
  { term: 'Thesis', def: 'The reason for taking a trade. The Trade agent constructs a thesis: "BTC is in an accumulation zone, regime is trend, RSI is 58, 3/4 strategies agree. Thesis: continuation long." The Critic then stress-tests it.' },
  { term: 'Counter-Thesis', def: 'The opposite argument to the trade thesis. The Critic agent is required to provide a counter-thesis before it can veto a trade — it cannot just say "bad trade" without explaining why.' },
  { term: 'Backtest', def: 'Running the bot\'s strategies on historical price data to see how they would have performed. Results include total return, win rate, drawdown, and per-trade breakdown. Used to validate strategy changes before going live.' },
  { term: 'Walk-Forward Testing', def: 'A more rigorous form of backtesting where the strategy is optimized on one period and tested on the next, then the window moves forward. Helps detect overfitting — strategies that work on past data but fail on new data.' },
  { term: 'Overfitting', def: 'When a strategy is tuned so precisely to historical data that it fails on future data. The bot avoids overfitting by using robust, logic-based indicators rather than curve-fitted parameters.' },
  { term: 'Monte Carlo Simulation', def: 'Running thousands of random simulations of possible future price paths based on historical volatility. The bot uses this to find statistical support/resistance zones that hold across many possible scenarios.' },
  { term: 'Hyperliquid', def: 'The decentralized perpetuals exchange the bot trades on. Key advantages: fully onchain, sub-second execution, deep liquidity, low fees (0.035% maker / 0.05% taker), and a wide range of tradeable assets.' },
  { term: 'API Key', def: 'A credential pair (API key + secret) that allows software to interact with an exchange on your behalf. Required for live trading. Never share your API key — store it encrypted in your .env file only.' },
  { term: 'Hot Wallet', def: 'A wallet connected to the internet, used for exchange trading. Contrasted with cold storage (hardware wallets). Only fund your trading wallet with what you are willing to risk.' },
  { term: 'Equity Curve', def: 'A chart showing account equity over time. A smooth upward-sloping curve with small drawdowns is the goal. The bot\'s backtest results page shows the equity curve for all tested periods.' },
  { term: 'Max Drawdown', def: 'The largest peak-to-trough decline in equity during a trading period. A strategy with a 30% max drawdown would have caused an account to fall 30% from its peak before recovering. Lower is better.' },
  { term: 'Calmar Ratio', def: 'Annual return divided by maximum drawdown. A ratio above 1.0 means the strategy earns more annually than its worst drawdown. Higher is better.' },
  { term: 'Win Rate', def: 'The percentage of trades that are profitable. A strategy can be profitable with a win rate below 50% if the average win is much larger than the average loss. See the live track record at /forensics for the bot\'s actual win rate.' },
  { term: 'Expectancy', def: 'The average profit per trade, accounting for both win rate and average win/loss size. Formula: (WinRate × AvgWin) − (LossRate × AvgLoss). Positive expectancy means the system is profitable over many trades.' },
];

// ─── Scenario Simulator ───────────────────────────────────────────────────────

function ScenarioSimulator() {
  const [rsi, setRsi] = useState(62);
  const [atr, setAtr] = useState(1.8);
  const [score, setScore] = useState(72);
  const [regime, setRegime] = useState<'trend' | 'range' | 'panic' | 'high_volatility' | 'low_liquidity'>('trend');
  const [zone, setZone] = useState<'deep_accum' | 'accum' | 'neutral' | 'distrib' | 'safe_distrib'>('accum');
  const [sma, setSma] = useState<'bullish' | 'bearish' | 'mixed'>('bullish');

  // ── Regime Agent ──
  let regimeAction: string;
  let regimeConf: number;
  if (regime === 'trend' && score > 60) {
    regimeAction = 'CONFIRM TREND';
    regimeConf = Math.round(75 + score * 0.25);
  } else if (regime === 'panic') {
    regimeAction = 'CAUTION — PANIC MODE';
    regimeConf = 30;
  } else if (regime === 'range' && (rsi < 35 || rsi > 65)) {
    regimeAction = 'MEAN REVERSION SETUP';
    regimeConf = Math.round(55 + score * 0.2);
  } else {
    regimeAction = 'NEUTRAL — GATHER DATA';
    regimeConf = 40;
  }
  regimeConf = Math.min(regimeConf, 100);

  // ── Trade Agent ──
  let tradeAction: string;
  let tradeConf: number;
  const isAccumZone = zone === 'accum' || zone === 'deep_accum';
  const isDistribZone = zone === 'distrib' || zone === 'safe_distrib';
  if (score >= 75 && isAccumZone && sma === 'bullish' && regime === 'trend') {
    tradeAction = 'GO LONG';
    tradeConf = Math.round(score * 0.9);
  } else if (score >= 75 && isDistribZone && sma === 'bearish') {
    tradeAction = 'GO SHORT';
    tradeConf = Math.round(score * 0.85);
  } else if (score < 50) {
    tradeAction = 'SKIP — WEAK SETUP';
    tradeConf = Math.round(score * 0.6);
  } else if (regime === 'panic') {
    tradeAction = 'SKIP — PANIC REGIME';
    tradeConf = 25;
  } else {
    tradeAction = 'SKIP — INSUFFICIENT CONFLUENCE';
    tradeConf = Math.round(score * 0.65);
  }

  // ── Risk Agent ──
  let riskNote: string;
  let riskLeverage: number;
  if (atr > 3) {
    riskNote = 'High ATR — reduce position size 30%';
    riskLeverage = 2;
  } else if (atr > 1.5) {
    riskNote = 'Normal ATR — standard sizing';
    riskLeverage = 3;
  } else {
    riskNote = 'Low ATR — potential dead market, be cautious';
    riskLeverage = 2;
  }
  if (rsi > 70) riskNote += ' · RSI overbought caution on longs';
  if (rsi < 30) riskNote += ' · RSI oversold caution on shorts';

  // ── Critic Agent ──
  let criticAction: string;
  let criticReason: string;
  if (regime === 'panic') {
    criticAction = 'VETO';
    criticReason = 'Panic conditions — no clean entries';
  } else if (score >= 75 && regime === 'trend' && sma === 'bullish' && isAccumZone) {
    criticAction = 'APPROVE';
    criticReason = 'Strong confluence across all factors';
  } else if (score >= 65 && score < 75) {
    criticAction = 'SKEPTICAL — PROCEED WITH CAUTION';
    criticReason = 'Moderate confidence — requires tighter risk';
  } else {
    criticAction = 'VETO';
    criticReason = 'Sub-threshold confidence — setup not mature';
  }

  // ── Final decision ──
  const tradeIsGo = tradeAction === 'GO LONG' || tradeAction === 'GO SHORT';
  let finalVerdict: string;
  let finalColor: string;
  let finalBg: string;
  let finalGlow: string;
  if (tradeIsGo && criticAction === 'APPROVE') {
    finalVerdict = '✓ EXECUTE';
    finalColor = C.bull;
    finalBg = C.bull + '18';
    finalGlow = C.bull + '44';
  } else if (tradeIsGo && criticAction.startsWith('SKEPTICAL')) {
    finalVerdict = '⚠ PROCEED — REDUCED SIZING';
    finalColor = C.warn;
    finalBg = C.warn + '18';
    finalGlow = C.warn + '44';
  } else if (criticAction === 'VETO') {
    finalVerdict = '✗ BLOCKED — VETO';
    finalColor = C.bear;
    finalBg = C.bear + '18';
    finalGlow = C.bear + '44';
  } else {
    finalVerdict = '— SKIP';
    finalColor = C.muted;
    finalBg = C.surfaceHover;
    finalGlow = C.border;
  }

  // Setup quality score (0-100)
  const qualityScore = Math.round(
    (score * 0.4) +
    (regime === 'trend' ? 20 : regime === 'range' ? 10 : regime === 'panic' ? 0 : 8) +
    (isAccumZone && sma === 'bullish' ? 20 : isDistribZone && sma === 'bearish' ? 20 : 5) +
    (atr > 0.5 && atr < 3 ? 10 : 0) +
    (rsi >= 40 && rsi <= 65 ? 10 : 0)
  );
  const qualityPct = Math.min(qualityScore, 100);
  const qualityColor = qualityPct >= 75 ? C.bull : qualityPct >= 55 ? C.warn : C.bear;

  const labelStyle: React.CSSProperties = {
    fontSize: F.xs, color: C.muted, fontWeight: 600, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5,
  };
  const sliderStyle: React.CSSProperties = {
    width: '100%', accentColor: C.brand as string, cursor: 'pointer', height: 4,
  };
  const selectStyle: React.CSSProperties = {
    width: '100%', padding: '7px 10px', background: C.surfaceHover, border: `1px solid ${C.border}`,
    borderRadius: R.sm, color: C.text, fontSize: F.sm, outline: 'none', cursor: 'pointer',
  };

  const agents = [
    {
      name: 'Regime',
      model: 'Haiku',
      color: C.info,
      action: regimeAction,
      conf: regimeConf,
      note: `Regime: ${regime} · RSI: ${rsi}`,
    },
    {
      name: 'Trade',
      model: 'Sonnet',
      color: C.brand,
      action: tradeAction,
      conf: tradeConf,
      note: `Zone: ${zone} · SMA: ${sma} · Score: ${score}`,
    },
    {
      name: 'Risk',
      model: 'Haiku',
      color: C.warn,
      action: `Leverage ${riskLeverage}×`,
      conf: null,
      note: riskNote,
    },
    {
      name: 'Critic',
      model: 'Sonnet',
      color: criticAction === 'APPROVE' ? C.bull : criticAction.startsWith('SKEPTICAL') ? C.warn : C.bear,
      action: criticAction,
      conf: null,
      note: criticReason,
    },
  ];

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: F.md, fontWeight: 800, color: C.text, marginBottom: 4 }}>
          What Would the AI Do?
        </div>
        <div style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.6 }}>
          Adjust the market conditions to see how the 7 agents would respond. All logic is based on the bot&apos;s actual decision rules.
        </div>
      </div>

      <div id="ai-simulator" style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 320px) 1fr', gap: 24, alignItems: 'start' }}>
        {/* ── Left: Controls ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* RSI */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <div style={labelStyle}>RSI</div>
              <span style={{ fontSize: F.sm, fontWeight: 700, color: rsi > 70 ? C.bear : rsi < 30 ? C.bull : C.text }}>{rsi}</span>
            </div>
            <input type="range" min={0} max={100} step={1} value={rsi} onChange={e => setRsi(+e.target.value)} style={sliderStyle} aria-label="RSI value" />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.faint, marginTop: 2 }}>
              <span>0 Oversold</span><span>100 Overbought</span>
            </div>
          </div>

          {/* ATR% */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <div style={labelStyle}>ATR %</div>
              <span style={{ fontSize: F.sm, fontWeight: 700, color: atr > 3 ? C.bear : atr < 0.5 ? C.warn : C.text }}>{atr.toFixed(1)}%</span>
            </div>
            <input type="range" min={0} max={5} step={0.1} value={atr} onChange={e => setAtr(+e.target.value)} style={sliderStyle} aria-label="ATR percentage" />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.faint, marginTop: 2 }}>
              <span>0% Low</span><span>5% High</span>
            </div>
          </div>

          {/* Signal Score */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <div style={labelStyle}>Signal Score</div>
              <span style={{ fontSize: F.sm, fontWeight: 700, color: score >= 75 ? C.bull : score >= 50 ? C.warn : C.bear }}>{score}</span>
            </div>
            <input type="range" min={0} max={100} step={1} value={score} onChange={e => setScore(+e.target.value)} style={sliderStyle} aria-label="Signal score" />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.faint, marginTop: 2 }}>
              <span>0 Weak</span><span>100 Strong</span>
            </div>
          </div>

          {/* Regime */}
          <div>
            <div style={labelStyle}>Regime</div>
            <select value={regime} onChange={e => setRegime(e.target.value as typeof regime)} style={selectStyle}>
              <option value="trend">trend</option>
              <option value="range">range</option>
              <option value="panic">panic</option>
              <option value="high_volatility">high_volatility</option>
              <option value="low_liquidity">low_liquidity</option>
            </select>
          </div>

          {/* Price Zone */}
          <div>
            <div style={labelStyle}>Price Zone</div>
            <select value={zone} onChange={e => setZone(e.target.value as typeof zone)} style={selectStyle}>
              <option value="deep_accum">deep_accum</option>
              <option value="accum">accum</option>
              <option value="neutral">neutral</option>
              <option value="distrib">distrib</option>
              <option value="safe_distrib">safe_distrib</option>
            </select>
          </div>

          {/* SMA Alignment */}
          <div>
            <div style={labelStyle}>SMA Alignment</div>
            <select value={sma} onChange={e => setSma(e.target.value as typeof sma)} style={selectStyle}>
              <option value="bullish">bullish (SMA20 &gt; SMA50)</option>
              <option value="bearish">bearish (SMA20 &lt; SMA50)</option>
              <option value="mixed">mixed</option>
            </select>
          </div>

          {/* Setup Quality bar */}
          <div style={{ padding: '12px 14px', background: qualityColor + '12', border: `1px solid ${qualityColor}30`, borderRadius: R.md, marginTop: 4 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ fontSize: F.xs, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>Setup Quality</span>
              <span style={{ fontSize: F.sm, fontWeight: 800, color: qualityColor }}>{qualityPct}</span>
            </div>
            <div style={{ height: 6, background: C.border, borderRadius: R.pill, overflow: 'hidden' }}>
              <div style={{ width: `${qualityPct}%`, height: '100%', background: qualityColor, borderRadius: R.pill, transition: 'width 0.2s' }} />
            </div>
          </div>
        </div>

        {/* ── Right: Pipeline results ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {agents.map((agent, i) => (
            <React.Fragment key={agent.name}>
              <div style={{
                display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 14px',
                background: agent.color + '0d', border: `1px solid ${agent.color}2a`,
                borderRadius: R.md,
              }}>
                {/* Badge */}
                <div style={{
                  minWidth: 52, padding: '3px 0', textAlign: 'center',
                  background: agent.color + '22', border: `1px solid ${agent.color}44`,
                  borderRadius: R.sm, flexShrink: 0,
                }}>
                  <div style={{ fontSize: 10, fontWeight: 800, color: agent.color }}>{agent.name}</div>
                  <div style={{ fontSize: 9, color: C.muted }}>{agent.model}</div>
                </div>
                {/* Content */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 3 }}>
                    <span style={{ fontSize: F.sm, fontWeight: 800, color: agent.color }}>{agent.action}</span>
                    {agent.conf !== null && (
                      <span style={{
                        fontSize: F.xs, fontWeight: 700, padding: '1px 7px',
                        background: agent.color + '22', color: agent.color, borderRadius: R.pill,
                      }}>{agent.conf}%</span>
                    )}
                  </div>
                  <div style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.5 }}>{agent.note}</div>
                </div>
              </div>
              {i < agents.length - 1 && (
                <div style={{ display: 'flex', justifyContent: 'center', padding: '3px 0', color: C.muted, fontSize: 13 }}>↓</div>
              )}
            </React.Fragment>
          ))}

          {/* Final verdict */}
          <div style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'center', color: C.muted, fontSize: 13, marginBottom: 6 }}>↓</div>
            <div style={{
              padding: '16px 20px', borderRadius: R.md, textAlign: 'center',
              background: finalBg, border: `2px solid ${finalGlow}`,
              boxShadow: `0 0 16px ${finalGlow}`,
              transition: 'all 0.2s',
            }}>
              <div style={{ fontSize: F.xl, fontWeight: 900, color: finalColor, letterSpacing: 0.5 }}>{finalVerdict}</div>
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
                {finalVerdict.startsWith('✓') ? 'All agents aligned — trade meets execution criteria' :
                  finalVerdict.startsWith('⚠') ? 'Trade approved with reduced sizing — moderate confidence' :
                    finalVerdict.startsWith('✗') ? 'Trade blocked — Critic agent exercised veto power' :
                      'Insufficient confluence — agents recommend waiting'}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Exchange Comparison Chart ────────────────────────────────────────────────

function ExchangeComparisonChart() {
  const metrics = [
    { label: 'Taker Fee', unit: '%', HL: 0.05, Binance: 0.10, Bybit: 0.10, dYdX: 0.05, higherIsBetter: false, fmt: (v: number) => v.toFixed(3) },
    { label: 'Maker Fee', unit: '%', HL: 0.035, Binance: 0.02, Bybit: 0.01, dYdX: 0.02, higherIsBetter: false, fmt: (v: number) => v.toFixed(3) },
    { label: 'Exec Speed', unit: 'ms', HL: 50, Binance: 80, Bybit: 90, dYdX: 150, higherIsBetter: false, fmt: (v: number) => String(v) },
    { label: 'Max Leverage', unit: '×', HL: 50, Binance: 125, Bybit: 100, dYdX: 20, higherIsBetter: true, fmt: (v: number) => String(v) },
    { label: 'Onchain Settlement', unit: '', HL: 1, Binance: 0, Bybit: 0, dYdX: 0.5, higherIsBetter: true, fmt: (v: number) => v === 1 ? '✓' : v === 0.5 ? '~' : '✗' },
    { label: 'API Rate Limit', unit: '', HL: 5, Binance: 3, Bybit: 2, dYdX: 3, higherIsBetter: true, fmt: (v: number) => ['Low','Med','High','High+','Max'][v-1] || '?' },
  ];
  const exchanges = ['HL', 'Binance', 'Bybit', 'dYdX'] as const;
  const colors: Record<string, string> = { HL: C.brand, Binance: C.warn, Bybit: C.info, dYdX: C.bull };

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: F.xs, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
        Exchange Feature Comparison
      </div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 14, flexWrap: 'wrap' }}>
        {exchanges.map(ex => (
          <div key={ex} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: colors[ex] }} />
            <span style={{ fontSize: F.xs, color: ex === 'HL' ? C.brand : C.muted, fontWeight: ex === 'HL' ? 700 : 400 }}>
              {ex === 'HL' ? 'Hyperliquid ★' : ex}
            </span>
          </div>
        ))}
      </div>
      {/* Chart rows */}
      {metrics.map(m => {
        const vals: Record<string, number> = { HL: m.HL, Binance: m.Binance, Bybit: m.Bybit, dYdX: m.dYdX };
        const max = Math.max(...exchanges.map(ex => vals[ex]));
        return (
          <div key={m.label} style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: F.xs, color: C.textSub, fontWeight: 600 }}>{m.label}</span>
              {m.higherIsBetter
                ? <span style={{ fontSize: F.xs, color: C.bull }}>higher = better ↑</span>
                : <span style={{ fontSize: F.xs, color: C.warn }}>lower = better ↓</span>}
            </div>
            {exchanges.map(ex => {
              const v = vals[ex];
              const pct = max > 0 ? (v / max) * 100 : 100;
              const isBest = m.higherIsBetter
                ? v === Math.max(...exchanges.map(e => vals[e]))
                : v === Math.min(...exchanges.map(e => vals[e]));
              return (
                <div key={ex} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <div style={{ width: 60, fontSize: F.xs, color: ex === 'HL' ? C.brand : C.muted, fontWeight: ex === 'HL' ? 700 : 400, flexShrink: 0 }}>{ex}</div>
                  <div style={{ flex: 1, height: 18, background: C.surfaceHover, borderRadius: R.sm, overflow: 'hidden', position: 'relative' }}>
                    <div style={{
                      width: `${pct}%`, height: '100%',
                      background: isBest ? colors[ex] : colors[ex] + '55',
                      borderRadius: R.sm,
                      transition: 'width 0.3s',
                    }} />
                  </div>
                  <div style={{
                    width: 52, fontSize: F.xs, textAlign: 'right', flexShrink: 0,
                    color: isBest ? colors[ex] : C.muted,
                    fontWeight: isBest ? 700 : 400,
                  }}>
                    {m.fmt(v)}{m.unit && m.unit !== '' && m.label !== 'Exec Speed' ? m.unit : m.label === 'Exec Speed' ? 'ms' : ''}
                  </div>
                  {isBest && (
                    <div style={{ width: 14, fontSize: 10, color: isBest ? colors[ex] : 'transparent' }}>★</div>
                  )}
                </div>
              );
            })}
          </div>
        );
      })}
      <div style={{
        marginTop: 8, padding: '8px 12px', background: C.brand + '11',
        borderRadius: R.sm, border: `1px solid ${C.brand}33`,
        fontSize: F.xs, color: C.textSub, lineHeight: 1.6,
      }}>
        ★ Best-in-class for algo trading: Hyperliquid leads on execution speed, fees, and onchain transparency.
        The bot caps leverage at 10× regardless of what the exchange allows.
      </div>
    </div>
  );
}

// ─── Autonomy Level Meter ─────────────────────────────────────────────────────

function AutonomyLevelMeter() {
  const levels = [
    { n: 0, name: 'OFF', desc: 'Pure strategy', color: C.muted },
    { n: 1, name: 'ADVISORY', desc: 'Log only', color: C.info },
    { n: 2, name: 'VETO_ONLY', desc: 'Can block', color: C.warn },
    { n: 3, name: 'SIZING', desc: '+ size control', color: '#f97316' },
    { n: 4, name: 'DIRECTION', desc: '+ flip trades', color: C.bear + 'cc' },
    { n: 5, name: 'FULL', desc: 'Full control', color: C.bear },
  ];
  const currentMode = 1; // ADVISORY (default shown)
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: F.xs, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>
        Autonomy Scale (default: Level 1 — Advisory)
      </div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
        {levels.map(({ n, name, desc, color }) => (
          <div key={n} style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              padding: '10px 6px',
              borderRadius: R.sm,
              background: n === currentMode ? color + '22' : C.surfaceHover,
              border: `2px solid ${n === currentMode ? color : C.border}`,
              textAlign: 'center',
              transition: 'all 0.2s',
            }}>
              <div style={{ fontSize: 11, fontWeight: 900, color: n === currentMode ? color : C.muted }}>{n}</div>
              <div style={{ fontSize: 9, fontWeight: 700, color: n === currentMode ? color : C.muted, textTransform: 'uppercase', letterSpacing: 0.3, marginTop: 2 }}>{name}</div>
              <div style={{ fontSize: 9, color: C.muted, marginTop: 3, lineHeight: 1.3 }}>{desc}</div>
            </div>
          </div>
        ))}
      </div>
      {/* Gradient risk bar */}
      <div style={{ height: 6, borderRadius: R.pill, background: `linear-gradient(to right, ${C.muted}, ${C.info}, ${C.warn}, #f97316, ${C.bear + 'cc'}, ${C.bear})`, marginBottom: 4 }} />
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: C.muted }}>
        <span>Safe</span>
        <span>← Increasing LLM Autonomy →</span>
        <span>Full AI Control</span>
      </div>
    </div>
  );
}

// ─── Leverage Matrix ──────────────────────────────────────────────────────────

function LeverageMatrix() {
  const confLabels = ['60–69%', '70–79%', '80–89%', '≥90%'];
  const agrLabels = ['2/4', '3/4', '4/4'];
  // [conf][agreement] → max leverage
  const matrix = [
    [2, 3, 4],   // 60-69%
    [3, 5, 6],   // 70-79%
    [4, 6, 8],   // 80-89%
    [5, 7, 10],  // ≥90%
  ];
  const maxLev = 10;

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: F.xs, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
        Max Leverage by Confidence × Agreement
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 280 }}>
          <thead>
            <tr>
              <th style={{ padding: '6px 10px', fontSize: F.xs, color: C.muted, textAlign: 'left', fontWeight: 600, borderBottom: `1px solid ${C.border}` }}>Confidence</th>
              {agrLabels.map(a => (
                <th key={a} style={{ padding: '6px 10px', fontSize: F.xs, color: C.textSub, textAlign: 'center', fontWeight: 600, borderBottom: `1px solid ${C.border}` }}>
                  {a} agree
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, ri) => (
              <tr key={ri}>
                <td style={{ padding: '6px 10px', fontSize: F.xs, color: C.textSub, fontWeight: 600, borderBottom: `1px solid ${C.border}22` }}>{confLabels[ri]}</td>
                {row.map((lev, ci) => {
                  const intensity = lev / maxLev;
                  const bg = `rgba(99, 102, 241, ${intensity * 0.5})`;
                  return (
                    <td key={ci} style={{
                      padding: '8px 10px', textAlign: 'center',
                      background: bg, borderBottom: `1px solid ${C.border}22`,
                      fontSize: F.sm, fontWeight: 700,
                      color: intensity > 0.5 ? C.brand : C.textSub,
                    }}>
                      {lev}×
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 8, fontSize: F.xs, color: C.muted, lineHeight: 1.6 }}>
        Final leverage is also reduced in <span style={{ color: C.bear, fontWeight: 700 }}>PANIC</span> and <span style={{ color: C.warn, fontWeight: 700 }}>HIGH_VOLATILITY</span> regimes.
        Stop width overrides: wider SL = lower leverage to keep dollar risk constant at 1.5% equity.
      </div>
    </div>
  );
}

// ─── Stop Loss Visual ─────────────────────────────────────────────────────────

function StopLossVisual() {
  const W = 500, H = 220;
  const levels = [
    { label: 'Safe Distrib.', y: 30, price: 102400, color: '#7f1d1d', bg: '#7f1d1d22' },
    { label: 'Distribution', y: 65, price: 100800, color: C.bear, bg: C.bear + '22' },
    { label: 'ENTRY', y: 110, price: 99200, color: C.info, bg: C.info + '22', bold: true },
    { label: 'Accumulation', y: 150, price: 97800, color: C.bull, bg: C.bull + '22' },
    { label: 'Deep Accum. ← SL', y: 185, price: 96200, color: '#16a34a', bg: '#16a34a22', bold: true },
  ];
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: F.xs, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
        Where Stop Losses Are Placed (Long Trade)
      </div>
      <div style={{ overflowX: 'auto' }}>
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: W, height: 'auto', display: 'block' }}>
          <defs>
            <linearGradient id="slGreenGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={C.bull} stopOpacity="0.08" />
              <stop offset="100%" stopColor={C.bull} stopOpacity="0.22" />
            </linearGradient>
            <linearGradient id="slRedGrad" x1="0" y1="1" x2="0" y2="0">
              <stop offset="0%" stopColor={C.bear} stopOpacity="0.08" />
              <stop offset="100%" stopColor={C.bear} stopOpacity="0.22" />
            </linearGradient>
          </defs>
          {/* Green zone below entry */}
          <rect x="140" y="110" width="360" height="110" fill="url(#slGreenGrad)" />
          {/* Red zone above entry */}
          <rect x="140" y="0" width="360" height="110" fill="url(#slRedGrad)" />
          {/* Price levels */}
          {levels.map(({ label, y, price, color, bold }) => (
            <g key={label}>
              <line x1="140" y1={y} x2="500" y2={y} stroke={color} strokeWidth={bold ? 2 : 1} strokeDasharray={bold ? undefined : '4 4'} strokeOpacity="0.7" />
              <text x="135" y={y + 4} textAnchor="end" fontSize="11" fill={color} fontWeight={bold ? 700 : 400}>{label}</text>
              <text x="490" y={y + 4} textAnchor="end" fontSize="10" fill={color} opacity="0.7">${price.toLocaleString()}</text>
            </g>
          ))}
          {/* TP arrows */}
          {[{ y: 65, label: 'TP2', color: C.bull }, { y: 82, label: 'TP1', color: C.bull + 'bb' }].map(({ y, label, color }) => (
            <g key={label}>
              <text x="148" y={y + 4} fontSize="10" fill={color} fontWeight={700}>{label} ↑</text>
            </g>
          ))}
          {/* After TP1: stop to BE */}
          <text x="148" y="128" fontSize="10" fill={C.brand} fontWeight={700}>After TP1: SL → BE</text>
          {/* Price dot */}
          <circle cx="320" cy="110" r="6" fill={C.info} />
          <text x="328" y="106" fontSize="10" fill={C.info} fontWeight={700}>Current Price</text>
        </svg>
      </div>
      <div style={{ marginTop: 6, fontSize: F.xs, color: C.muted, lineHeight: 1.6 }}>
        The stop loss is placed at the <strong style={{ color: '#16a34a' }}>Deep Accumulation zone</strong> —
        a statistically significant support level computed from 1,000 Monte Carlo price paths.
        This is where the trade thesis is invalidated, not an arbitrary 2% below entry.
      </div>
    </div>
  );
}

// ─── Glossary Stats + Tag Cloud ───────────────────────────────────────────────

// Category color assignments for tag cloud pills
const GLOSSARY_TERM_CATEGORIES: Record<string, 'strategy' | 'risk' | 'ai' | 'technical'> = {
  'Regime':           'ai',
  'Ensemble':         'strategy',
  'Veto':             'ai',
  'Advisory Mode':    'ai',
  'Thesis':           'ai',
  'Counter-Thesis':   'ai',
  'ATR':              'technical',
  'RSI':              'technical',
  'SMA':              'technical',
  'EMA':              'technical',
  'MACD':             'technical',
  'Bollinger Bands':  'technical',
  'Keltner Channel':  'technical',
  'VWAP':             'technical',
  'WaveTrend':        'technical',
  'ADX':              'technical',
  'Open Interest':    'technical',
  'Confidence Score': 'strategy',
  'Confluence':       'strategy',
  'Backtest':         'strategy',
  'Walk-Forward Testing': 'strategy',
  'Monte Carlo Simulation': 'strategy',
  'Drawdown':         'risk',
  'Max Drawdown':     'risk',
  'Circuit Breaker':  'risk',
  'Liquidation Price':'risk',
  'R:R':              'risk',
  'Margin':           'risk',
  'Profit Factor':    'risk',
  'Trailing Stop':    'risk',
  'Sharpe Ratio':     'risk',
  'Calmar Ratio':     'risk',
  'Slippage':         'risk',
  'Funding Rate':     'risk',
};

// Importance weights for pill sizing (px font-size base)
const GLOSSARY_IMPORTANCE: Record<string, number> = {
  ATR: 16, RSI: 16, Regime: 16, Drawdown: 14, Confluence: 14, Veto: 14,
  'Confidence Score': 14, Ensemble: 13, 'Circuit Breaker': 13, 'R:R': 13,
  Backtest: 13, 'Trailing Stop': 12, Thesis: 12, MACD: 12, 'Bollinger Bands': 12,
  SMA: 11, EMA: 11, VWAP: 11, 'Profit Factor': 11, 'Sharpe Ratio': 11,
  'Liquidation Price': 11, Funding: 11, 'Monte Carlo Simulation': 11,
};

const GLOSSARY_CAT_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  strategy: { bg: `${C.brand}18`, border: `${C.brand}40`, text: C.brand },
  risk:      { bg: `${C.bear}14`,  border: `${C.bear}35`,  text: C.bearMid },
  ai:        { bg: 'rgba(124,58,237,0.12)', border: 'rgba(124,58,237,0.32)', text: '#c084fc' },
  technical: { bg: `${C.info}14`,  border: `${C.info}30`,  text: C.infoMid },
};

// Derive a short display label from a full term string
function shortTermLabel(term: string): string {
  // Use first parenthetical abbreviation if present, else first word(s)
  const abbr = term.match(/\(([^)]+)\)/);
  if (abbr) return abbr[1];
  if (term.length <= 14) return term;
  const words = term.split(' ');
  return words.length >= 2 ? words.slice(0, 2).join(' ') : term.slice(0, 12);
}

function GlossaryStats() {
  const totalTerms = GLOSSARY.length;

  // Unique topic labels used in a quick descriptive way
  const topics = ['RSI', 'ATR', 'Regime', 'Risk', 'AI Agents', 'Backtesting', 'Leverage'];

  // Build tag cloud entries from GLOSSARY_TERM_CATEGORIES
  const tagEntries = Object.entries(GLOSSARY_TERM_CATEGORIES).map(([term, cat]) => ({
    term,
    cat,
    size: GLOSSARY_IMPORTANCE[term] ?? 11,
  }));
  // Sort by size desc so prominent terms appear first
  tagEntries.sort((a, b) => b.size - a.size);

  return (
    <div style={{ marginBottom: 20 }}>
      {/* ── 3 mini stat cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 16 }}>
        {[
          {
            label: 'Total Terms',
            value: String(totalTerms),
            sub: 'definitions',
            color: C.brand,
          },
          {
            label: 'Topics Covered',
            value: String(topics.length) + '+',
            sub: topics.slice(0, 3).join(', ') + '…',
            color: C.info,
          },
          {
            label: 'Last Updated',
            value: 'Mar 2026',
            sub: 'kept current',
            color: C.bull,
          },
        ].map(stat => (
          <div key={stat.label} style={{
            ...Glass.card,
            border: `1px solid ${C.border}`,
            borderRadius: R.md,
            padding: '12px 16px',
          }}>
            <div style={{ fontSize: F.xs, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{stat.label}</div>
            <div style={{ fontSize: F.xl, fontWeight: 800, color: stat.color, marginBottom: 2 }}>{stat.value}</div>
            <div style={{ fontSize: F.xs, color: C.muted }}>{stat.sub}</div>
          </div>
        ))}
      </div>

      {/* ── Category legend ── */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        {(Object.entries(GLOSSARY_CAT_COLORS) as [string, { bg: string; border: string; text: string }][]).map(([cat, cols]) => (
          <span key={cat} style={{
            fontSize: F.xs,
            padding: '2px 8px',
            borderRadius: R.pill,
            background: cols.bg,
            border: `1px solid ${cols.border}`,
            color: cols.text,
            fontWeight: 600,
            textTransform: 'capitalize',
          }}>
            {cat === 'ai' ? 'AI / Agents' : cat}
          </span>
        ))}
      </div>

      {/* ── Tag cloud ── */}
      <div className="card-hover glass-noise" style={{
        ...Glass.crystal,
        border: `1px solid ${C.border}`,
        borderRadius: R.lg,
        padding: '16px 18px',
        marginBottom: 16,
      }}>
        <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text, marginBottom: 12 }}>Key Concepts</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {tagEntries.map(({ term, cat, size }) => {
            const cols = GLOSSARY_CAT_COLORS[cat];
            return (
              <span key={term} style={{
                fontSize: size,
                fontWeight: size >= 14 ? 700 : 600,
                padding: '3px 10px',
                borderRadius: R.pill,
                background: cols.bg,
                border: `1px solid ${cols.border}`,
                color: cols.text,
                cursor: 'default',
                lineHeight: 1.5,
              }} title={term}>
                {shortTermLabel(term)}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── Risk of Ruin Chart ───────────────────────────────────────────────────────

function RiskOfRuinChart() {
  const W = 480, H = 260;
  const padL = 48, padR = 24, padT = 20, padB = 44;
  const iW = W - padL - padR;
  const iH = H - padT - padB;

  // Win rates from 40% to 90% (10 points)
  const winRates = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90];

  // Risk of ruin formula: ((1-WR)/WR)^(1/riskFrac)
  // where riskFrac = risk per trade as fraction of bankroll
  // Simplified: RoR = min(1, ((1-WR)/WR)^(100/riskPct))
  function ror(wr: number, riskPct: number): number {
    if (wr >= 1) return 0;
    const base = (1 - wr) / wr;
    if (base >= 1) return 1;
    const exp = 100 / riskPct;
    return Math.min(1, Math.pow(base, exp));
  }

  const curves: { riskPct: number; color: string; label: string }[] = [
    { riskPct: 5,   color: '#dc2626', label: '5% risk/trade' },
    { riskPct: 2,   color: '#d97706', label: '2% risk/trade' },
    { riskPct: 1,   color: '#16a34a', label: '1% risk/trade' },
  ];

  const toX = (wr: number) => padL + ((wr - 0.40) / 0.50) * iW;
  const toY = (prob: number) => padT + iH - prob * iH;

  // Safe zone threshold (ruin < 5%)
  const safeThresholdY = toY(0.05);

  // Example reference line at 60% win rate (illustrative, not a claimed bot stat)
  const botWR = 0.60;
  const botX = toX(botWR);

  // Ruin at 60% WR, 1.5% risk
  const botRoR = ror(botWR, 1.5);

  // Build polyline points for each curve
  const curvePoints = curves.map(({ riskPct }) =>
    winRates.map(wr => `${toX(wr).toFixed(1)},${toY(ror(wr, riskPct)).toFixed(1)}`).join(' ')
  );

  // X-axis tick labels
  const xTicks = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90];
  const yTicks = [0, 0.25, 0.50, 0.75, 1.00];

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text, marginBottom: 4 }}>
        Risk of Ruin vs Win Rate
      </div>
      <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 12, lineHeight: 1.6 }}>
        Why the bot uses 1.5% risk per trade — keeps ruin probability near zero
      </div>

      <div style={{ overflowX: 'auto' }}>
        <svg width={W} height={H} style={{ display: 'block', minWidth: W }}>
          <defs>
            <clipPath id="rorClip">
              <rect x={padL} y={padT} width={iW} height={iH} />
            </clipPath>
          </defs>

          {/* Safe zone shading (ruin < 5%) */}
          <rect
            x={padL}
            y={safeThresholdY}
            width={iW}
            height={padT + iH - safeThresholdY}
            fill={C.bull + '12'}
            clipPath="url(#rorClip)"
          />
          <text x={padL + iW - 4} y={safeThresholdY - 4} textAnchor="end" fontSize={9} fill={C.bull as string} fontWeight={700}>
            Safe zone (&lt;5% ruin)
          </text>

          {/* 5% ruin threshold line */}
          <line
            x1={padL} y1={safeThresholdY}
            x2={padL + iW} y2={safeThresholdY}
            stroke={C.bull as string} strokeWidth={1} strokeDasharray="4 3" opacity={0.5}
          />

          {/* Y grid lines */}
          {yTicks.map(p => (
            <g key={p}>
              <line
                x1={padL} y1={toY(p)}
                x2={padL + iW} y2={toY(p)}
                stroke={C.border as string} strokeWidth={1} opacity={0.5}
              />
              <text x={padL - 6} y={toY(p) + 4} textAnchor="end" fontSize={9} fill={C.muted as string}>
                {Math.round(p * 100)}%
              </text>
            </g>
          ))}

          {/* X axis */}
          <line x1={padL} y1={padT + iH} x2={padL + iW} y2={padT + iH} stroke={C.border as string} strokeWidth={1} />

          {/* X tick labels */}
          {xTicks.map(wr => (
            <g key={wr}>
              <line x1={toX(wr)} y1={padT + iH} x2={toX(wr)} y2={padT + iH + 4} stroke={C.border as string} strokeWidth={1} />
              <text x={toX(wr)} y={padT + iH + 14} textAnchor="middle" fontSize={9} fill={C.muted as string}>
                {Math.round(wr * 100)}%
              </text>
            </g>
          ))}

          {/* Axis labels */}
          <text x={padL + iW / 2} y={H - 4} textAnchor="middle" fontSize={10} fill={C.muted as string}>
            Win Rate
          </text>
          <text
            x={12} y={padT + iH / 2}
            textAnchor="middle" fontSize={10} fill={C.muted as string}
            transform={`rotate(-90, 12, ${padT + iH / 2})`}
          >
            Ruin Prob
          </text>

          {/* Curve polylines */}
          {curves.map(({ color }, i) => (
            <polyline
              key={i}
              points={curvePoints[i]}
              fill="none"
              stroke={color}
              strokeWidth={2}
              strokeLinejoin="round"
              clipPath="url(#rorClip)"
            />
          ))}

          {/* Reference line at 60% win rate (illustrative example) */}
          <line
            x1={botX} y1={padT}
            x2={botX} y2={padT + iH}
            stroke={C.brand as string} strokeWidth={1.5} strokeDasharray="5 3"
          />
          <text x={botX + 4} y={padT + 12} fontSize={9} fontWeight={700} fill={C.brand as string}>
            Example: 60% WR
          </text>

          {/* Dot at 60% WR, 1.5% risk */}
          {(() => {
            const dotRoR = ror(botWR, 1.5);
            const dotX = botX;
            const dotY = toY(dotRoR);
            const clampedDotY = Math.max(padT, Math.min(padT + iH, dotY));
            return (
              <g>
                <circle cx={dotX} cy={clampedDotY} r={6} fill={C.bull as string} stroke={C.card as string} strokeWidth={2} />
                <text x={dotX + 10} y={clampedDotY + 4} fontSize={9} fontWeight={700} fill={C.bull as string}>
                  Low ruin at 1.5% risk
                </text>
              </g>
            );
          })()}
        </svg>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        {curves.map(({ color, label }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ width: 20, height: 3, background: color, borderRadius: 2 }} />
            <span style={{ fontSize: F.xs, color: C.muted }}>{label}</span>
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 20, height: 2, background: C.brand as string, borderRadius: 2, opacity: 0.8 }} />
          <span style={{ fontSize: F.xs, color: C.brand }}>Example (60% WR, 1.5% risk)</span>
        </div>
      </div>

      {/* Stats callout */}
      <div style={{
        marginTop: 12,
        padding: '10px 14px',
        background: C.bull + '10',
        border: `1px solid ${C.bull}25`,
        borderRadius: R.md,
        fontSize: F.xs,
        color: C.textSub,
        lineHeight: 1.7,
      }}>
        At <strong style={{ color: C.text }}>1.5% risk/trade</strong>, even a modest win rate keeps ruin probability extremely low. Doubling risk to 5%/trade dramatically increases your ruin probability — this is why position sizing discipline matters.
      </div>
    </div>
  );
}

// ─── Expected Value Calculator ────────────────────────────────────────────────

function ExpectedValueCalc() {
  const [winRate, setWinRate] = useState(0.60);
  const [avgWin, setAvgWin] = useState(300);
  const [avgLoss, setAvgLoss] = useState(150);

  const ev = winRate * avgWin - (1 - winRate) * avgLoss;
  const isPositive = ev >= 0;
  const tradesPerMonth = 20;
  const monthlyEV = ev * tradesPerMonth;

  // Breakeven win rate: WR * avgWin = (1-WR) * avgLoss → WR = avgLoss / (avgWin + avgLoss)
  const breakevenWR = avgLoss > 0 && avgWin > 0 ? avgLoss / (avgWin + avgLoss) : 0;

  // Visual breakdown bar: width proportional to magnitude
  const totalBar = winRate * avgWin + (1 - winRate) * avgLoss;
  const gainPct = totalBar > 0 ? (winRate * avgWin / totalBar) * 100 : 50;
  const lossPct = 100 - gainPct;

  const inputStyle: React.CSSProperties = {
    padding: '7px 10px',
    background: C.surfaceHover,
    border: `1px solid ${C.border}`,
    borderRadius: R.sm,
    color: C.text,
    fontSize: F.sm,
    width: '100%',
    outline: 'none',
    fontVariantNumeric: 'tabular-nums',
  };
  const labelStyle: React.CSSProperties = { fontSize: F.xs, color: C.muted, fontWeight: 600, marginBottom: 4 };

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: R.lg, padding: '20px 22px' }}>
      <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text, marginBottom: 16 }}>
        Expected Value Calculator
      </div>

      {/* EV Result */}
      <div style={{ marginBottom: 20, padding: '16px 20px', background: (isPositive ? C.bull : C.bear) + '14', border: `1px solid ${(isPositive ? C.bull : C.bear)}33`, borderRadius: R.md, textAlign: 'center' }}>
        <div style={{ fontSize: 28, fontWeight: 900, color: isPositive ? C.bull : C.bear, letterSpacing: -0.5 }}>
          {isPositive ? '+' : ''}{ev >= 0 ? '$' : '-$'}{Math.abs(ev).toFixed(2)} per trade
        </div>
        <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
          Win Rate: {(winRate * 100).toFixed(0)}% | Avg Win: ${avgWin} | Avg Loss: ${avgLoss}
        </div>
      </div>

      {/* Inputs */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
        <div>
          <div style={labelStyle}>Win Rate (%)</div>
          <input
            type="range"
            min={0} max={100} step={1}
            value={Math.round(winRate * 100)}
            onChange={e => setWinRate(+e.target.value / 100)}
            style={{ width: '100%', accentColor: C.brand as string, cursor: 'pointer' }}
            aria-label="Win rate percentage slider"
          />
          <div style={{ ...inputStyle, textAlign: 'center', marginTop: 4 }}>
            <input
              type="number" min={0} max={100} step={1}
              value={Math.round(winRate * 100)}
              onChange={e => setWinRate(Math.min(100, Math.max(0, +e.target.value)) / 100)}
              style={{ background: 'none', border: 'none', color: C.text, fontSize: F.sm, width: '100%', textAlign: 'center', outline: 'none' }}
              aria-label="Win rate percentage"
            />
          </div>
        </div>
        <div>
          <div style={labelStyle}>Avg Win ($)</div>
          <input
            type="range"
            min={10} max={2000} step={10}
            value={avgWin}
            onChange={e => setAvgWin(+e.target.value)}
            style={{ width: '100%', accentColor: C.bull as string, cursor: 'pointer' }}
            aria-label="Average win amount slider"
          />
          <div style={{ ...inputStyle, textAlign: 'center', marginTop: 4 }}>
            <input
              type="number" min={1} max={10000} step={10}
              value={avgWin}
              onChange={e => setAvgWin(Math.max(1, +e.target.value))}
              style={{ background: 'none', border: 'none', color: C.text, fontSize: F.sm, width: '100%', textAlign: 'center', outline: 'none' }}
              aria-label="Average win amount"
            />
          </div>
        </div>
        <div>
          <div style={labelStyle}>Avg Loss ($)</div>
          <input
            type="range"
            min={10} max={2000} step={10}
            value={avgLoss}
            onChange={e => setAvgLoss(+e.target.value)}
            style={{ width: '100%', accentColor: C.bear as string, cursor: 'pointer' }}
            aria-label="Average loss amount slider"
          />
          <div style={{ ...inputStyle, textAlign: 'center', marginTop: 4 }}>
            <input
              type="number" min={1} max={10000} step={10}
              value={avgLoss}
              onChange={e => setAvgLoss(Math.max(1, +e.target.value))}
              style={{ background: 'none', border: 'none', color: C.text, fontSize: F.sm, width: '100%', textAlign: 'center', outline: 'none' }}
              aria-label="Average loss amount"
            />
          </div>
        </div>
      </div>

      {/* Visual breakdown bar */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.xs, color: C.muted, marginBottom: 4 }}>
          <span style={{ color: C.bull }}>Expected Gain ({(winRate * 100).toFixed(0)}% × ${avgWin})</span>
          <span style={{ color: C.bear }}>Expected Loss ({((1 - winRate) * 100).toFixed(0)}% × ${avgLoss})</span>
        </div>
        <div style={{ height: 14, borderRadius: R.pill, overflow: 'hidden', display: 'flex' }}>
          <div style={{ width: `${gainPct}%`, background: C.bull, transition: 'width 0.2s' }} />
          <div style={{ width: `${lossPct}%`, background: C.bear, transition: 'width 0.2s' }} />
        </div>
      </div>

      {/* Monthly projection + breakeven */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div style={{ padding: '10px 14px', background: C.surfaceHover, border: `1px solid ${C.border}`, borderRadius: R.md }}>
          <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 2 }}>Monthly Projection</div>
          <div style={{ fontSize: F.md, fontWeight: 800, color: isPositive ? C.bull : C.bear }}>
            {isPositive ? '+' : ''}${monthlyEV.toFixed(0)}
          </div>
          <div style={{ fontSize: F.xs, color: C.muted }}>If {tradesPerMonth} trades/month</div>
        </div>
        <div style={{ padding: '10px 14px', background: C.surfaceHover, border: `1px solid ${C.border}`, borderRadius: R.md }}>
          <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 2 }}>Breakeven Win Rate</div>
          <div style={{ fontSize: F.md, fontWeight: 800, color: winRate >= breakevenWR ? C.bull : C.bear }}>
            {(breakevenWR * 100).toFixed(1)}%
          </div>
          <div style={{ fontSize: F.xs, color: C.muted }}>
            Need ≥{(breakevenWR * 100).toFixed(1)}% to be profitable
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Kelly Formula Diagram ─────────────────────────────────────────────────────

function KellyFormulaDiagram() {
  // Kelly: f* = (bp - q) / b
  // b = odds (avg win / avg loss = 300/150 = 2.0) — illustrative example
  // p = win rate = 0.60, q = 0.40 — illustrative example
  const b = 2.0;
  const p = 0.60;
  const q = 1 - p;
  const fullKelly = (b * p - q) / b; // ≈ 0.67
  const halfKelly = fullKelly / 2;
  const quarterKelly = fullKelly / 4;
  // Bot caps at 1.5% risk
  const botRisk = 0.015;

  const kellyPct = (fullKelly * 100).toFixed(1);
  const halfKellyPct = (halfKelly * 100).toFixed(1);
  const quarterKellyPct = (quarterKelly * 100).toFixed(1);

  // Bar zones
  const zones = [
    { from: 0,   to: 15,  label: 'Safe', color: C.bull },
    { from: 15,  to: 35,  label: 'Moderate', color: C.warn },
    { from: 35,  to: 67,  label: 'Aggressive', color: '#f97316' },
    { from: 67,  to: 100, label: 'Full Kelly', color: C.bear },
  ];

  const fractions = [
    { label: 'Full Kelly', pct: fullKelly * 100, color: C.bear, note: '~50%+ drawdowns possible' },
    { label: 'Half Kelly', pct: halfKelly * 100, color: '#f97316', note: 'Still aggressive' },
    { label: 'Quarter Kelly', pct: quarterKelly * 100, color: C.warn, note: `${quarterKellyPct}% → capped at 1.5%` },
  ];

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text, marginBottom: 4 }}>
        Kelly Criterion — Why 1.5% Risk?
      </div>
      <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 14, lineHeight: 1.6 }}>
        Full Kelly maximizes long-run growth but causes 50%+ drawdowns. Quarter-Kelly is the standard conservative approach.
      </div>

      {/* Formula display */}
      <div style={{ padding: '14px 18px', background: C.surfaceHover, border: `1px solid ${C.border}`, borderRadius: R.md, marginBottom: 16, fontFamily: 'monospace' }}>
        <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 8 }}>Kelly Formula:</div>
        <div style={{ fontSize: F.md, color: C.text, fontWeight: 700, marginBottom: 6 }}>
          f* = (b × p − q) / b
        </div>
        <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 8 }}>
          where: b = odds (avg win / avg loss), p = win rate, q = loss rate
        </div>
        <div style={{ fontSize: F.sm, color: C.brand, fontWeight: 700 }}>
          f* = ({b} × {p} − {q.toFixed(2)}) / {b} = <span style={{ color: C.bull }}>{(fullKelly * 100).toFixed(0)}%</span>
        </div>
      </div>

      {/* Horizontal bar with zones */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ display: 'flex', height: 28, borderRadius: R.sm, overflow: 'hidden', marginBottom: 6 }}>
          {zones.map(z => (
            <div
              key={z.label}
              style={{
                width: `${z.to - z.from}%`,
                background: z.color + '50',
                borderRight: `1px solid ${z.color}`,
                position: 'relative',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <span style={{ fontSize: 9, fontWeight: 700, color: z.color }}>{z.label}</span>
            </div>
          ))}
        </div>
        {/* Scale */}
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: C.muted, marginBottom: 10 }}>
          {[0, 15, 35, 67, 100].map(v => (
            <span key={v}>{v}%</span>
          ))}
        </div>
      </div>

      {/* Kelly fraction comparison */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
        {fractions.map(f => {
          const isBotRange = f.label === 'Quarter Kelly';
          return (
            <div
              key={f.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '10px 14px',
                background: (isBotRange ? C.bull : f.color) + '12',
                border: `1px solid ${(isBotRange ? C.bull : f.color)}33`,
                borderRadius: R.md,
              }}
            >
              <div style={{ minWidth: 110, fontSize: F.xs, fontWeight: 700, color: isBotRange ? C.bull : f.color }}>{f.label}</div>
              <div style={{ flex: 1, height: 8, background: C.border, borderRadius: R.pill, overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(100, f.pct)}%`, height: '100%', background: isBotRange ? C.bull : f.color, borderRadius: R.pill }} />
              </div>
              <div style={{ minWidth: 44, fontSize: F.sm, fontWeight: 800, color: isBotRange ? C.bull : f.color, textAlign: 'right' }}>{f.pct.toFixed(1)}%</div>
              <div style={{ fontSize: F.xs, color: C.muted, minWidth: 160 }}>{f.note}</div>
            </div>
          );
        })}
      </div>

      {/* Bot marker */}
      <div style={{
        padding: '12px 16px',
        background: C.brand + '14',
        border: `1px solid ${C.brand}40`,
        borderRadius: R.md,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}>
        <div style={{ fontSize: 20 }}>←</div>
        <div>
          <div style={{ fontSize: F.sm, fontWeight: 800, color: C.brand }}>Bot uses: 1.5% per trade</div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 2, lineHeight: 1.6 }}>
            Quarter-Kelly ({quarterKellyPct}%) would suggest {quarterKellyPct}% per trade.
            The bot caps this at <strong style={{ color: C.brand }}>1.5%</strong> for additional safety — well inside the green zone.
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Scenario Stress Test ────────────────────────────────────────────────────

function ScenarioStressTest() {
  const [scenario, setScenario] = useState(0);

  type ScenarioData = {
    label: string;
    emoji: string;
    color: string;
    return30d: string;
    trades: number;
    maxDD: string;
    description: string;
    // SVG path points as [x, y] fractions of 200×60 viewBox
    curvePoints: string;
    fillPath: string;
  };

  const scenarios: ScenarioData[] = [
    {
      label: 'Normal',
      emoji: '📈',
      color: C.bull,
      return30d: '+11%',
      trades: 22,
      maxDD: '4%',
      description: 'Bot runs normally. Steady trend-following with standard sizing. Most signals pass all 6 gates.',
      curvePoints: '0,54 20,50 40,46 60,42 80,38 100,34 120,30 140,26 160,20 180,14 200,6',
      fillPath: 'M0,54 L20,50 L40,46 L60,42 L80,38 L100,34 L120,30 L140,26 L160,20 L180,14 L200,6 L200,60 L0,60 Z',
    },
    {
      label: 'Flash Crash',
      emoji: '🔴',
      color: C.bear,
      return30d: '+3%',
      trades: 14,
      maxDD: '15%',
      description: 'Circuit breaker triggers on sharp drop. Bot pauses, then re-enters cautiously as price recovers above key zones.',
      curvePoints: '0,10 30,8 55,40 75,52 90,48 110,38 135,26 160,16 180,10 200,6',
      fillPath: 'M0,10 L30,8 L55,40 L75,52 L90,48 L110,38 L135,26 L160,16 L180,10 L200,6 L200,60 L0,60 Z',
    },
    {
      label: 'Bull Run',
      emoji: '🚀',
      color: '#22c55e',
      return30d: '+25%',
      trades: 30,
      maxDD: '5%',
      description: 'Regime locked in TREND. Most signals qualify for higher leverage. Trailing stops keep winning trades open longer.',
      curvePoints: '0,56 20,50 40,44 60,36 80,26 100,18 120,12 140,8 160,4 180,2 200,1',
      fillPath: 'M0,56 L20,50 L40,44 L60,36 L80,26 L100,18 L120,12 L140,8 L160,4 L180,2 L200,1 L200,60 L0,60 Z',
    },
    {
      label: 'Sideways Chop',
      emoji: '↔️',
      color: C.muted,
      return30d: '+2%',
      trades: 8,
      maxDD: '3%',
      description: 'Low ADX and failed squeeze setups. Most signals rejected at ensemble vote. Bot mostly sits on the sidelines.',
      curvePoints: '0,30 20,28 40,33 60,29 80,34 100,30 120,27 140,32 160,28 180,26 200,24',
      fillPath: 'M0,30 L20,28 L40,33 L60,29 L80,34 L100,30 L120,27 L140,32 L160,28 L180,26 L200,24 L200,60 L0,60 Z',
    },
    {
      label: 'High Vol',
      emoji: '⚡',
      color: C.warn,
      return30d: '+7%',
      trades: 18,
      maxDD: '9%',
      description: 'Wider stops required. Leverage capped lower. Bot reduces size in HIGH_VOLATILITY regime but still finds valid setups.',
      curvePoints: '0,30 15,20 30,36 50,22 70,40 90,18 110,34 130,16 155,28 175,12 200,20',
      fillPath: 'M0,30 L15,20 L30,36 L50,22 L70,40 L90,18 L110,34 L130,16 L155,28 L175,12 L200,20 L200,60 L0,60 Z',
    },
  ];

  const s = scenarios[scenario];

  const btnStyle = (active: boolean, color: string): React.CSSProperties => ({
    flex: 1,
    padding: '7px 4px',
    borderRadius: R.sm,
    border: `1px solid ${active ? color : C.border}`,
    background: active ? color + '22' : C.surfaceHover,
    color: active ? color : C.muted,
    fontSize: 11,
    fontWeight: active ? 800 : 500,
    cursor: 'pointer',
    transition: 'all 0.15s',
    whiteSpace: 'nowrap' as const,
    textAlign: 'center' as const,
  });

  return (
    <div>
      <div style={{ fontSize: F.sm, fontWeight: 800, color: C.text, marginBottom: 14 }}>
        Market Scenario Simulator
      </div>

      {/* Scenario selector buttons */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
        {scenarios.map((sc, i) => (
          <button key={sc.label} onClick={() => setScenario(i)} style={btnStyle(scenario === i, sc.color)}>
            {sc.emoji} {sc.label}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 20, alignItems: 'start' }}>
        {/* Left: SVG equity curve + KPI pills */}
        <div>
          <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 6, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>
            30-Day Equity Curve
          </div>
          <div style={{ background: C.surfaceHover, border: `1px solid ${C.border}`, borderRadius: R.md, padding: '10px 12px', marginBottom: 12 }}>
            <svg viewBox="0 0 200 60" style={{ width: '100%', height: 60, display: 'block', overflow: 'visible' }}>
              <defs>
                <linearGradient id={`ssGrad${scenario}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={s.color} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={s.color} stopOpacity={0.03} />
                </linearGradient>
              </defs>
              <path d={s.fillPath} fill={`url(#ssGrad${scenario})`} />
              <polyline
                points={s.curvePoints}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            </svg>
          </div>

          {/* KPI pills */}
          <div style={{ display: 'flex', gap: 6 }}>
            {[
              { label: 'Return', value: s.return30d, color: s.return30d.startsWith('+') ? C.bull : C.bear },
              { label: 'Max DD', value: `-${s.maxDD}`, color: C.warn },
              { label: 'Trades', value: String(s.trades), color: C.info },
            ].map(({ label, value, color }) => (
              <div
                key={label}
                style={{
                  flex: 1,
                  textAlign: 'center',
                  padding: '8px 6px',
                  background: color + '12',
                  border: `1px solid ${color}30`,
                  borderRadius: R.md,
                }}
              >
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: F.md, fontWeight: 800, color }}>{value}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Right: description + bot behavior */}
        <div>
          <div
            style={{
              padding: '14px 16px',
              background: s.color + '0e',
              border: `1px solid ${s.color}33`,
              borderRadius: R.md,
              marginBottom: 12,
            }}
          >
            <div style={{ fontSize: F.sm, fontWeight: 700, color: s.color, marginBottom: 6 }}>
              {s.emoji} {s.label} Scenario
            </div>
            <div style={{ fontSize: F.xs, color: C.textSub, lineHeight: 1.7 }}>{s.description}</div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {[
              { label: '30-day projected return', value: s.return30d, positive: s.return30d.startsWith('+') },
              { label: 'Expected trade count', value: `${s.trades} trades` },
              { label: 'Expected max drawdown', value: s.maxDD, warn: true },
            ].map(({ label, value, positive, warn }) => (
              <div
                key={label}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '8px 12px',
                  background: C.surfaceHover,
                  borderRadius: R.sm,
                  border: `1px solid ${C.border}`,
                }}
              >
                <span style={{ fontSize: F.xs, color: C.muted }}>{label}</span>
                <span style={{
                  fontSize: F.sm,
                  fontWeight: 700,
                  color: warn ? C.warn : positive === true ? C.bull : positive === false ? C.bear : C.text,
                }}>
                  {value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Position Size Demo ───────────────────────────────────────────────────────

function PositionSizeDemo() {
  const examples = [
    {
      confidence: 65,
      label: 'Low Confidence',
      notional: 500,
      leverage: '2×',
      barColor: C.warn,
      riskDollars: 150,
    },
    {
      confidence: 75,
      label: 'Medium Confidence',
      notional: 1500,
      leverage: '5×',
      barColor: C.brand,
      riskDollars: 150,
    },
    {
      confidence: 90,
      label: 'High Confidence',
      notional: 3000,
      leverage: '10×',
      barColor: C.bull,
      riskDollars: 150,
    },
  ];

  const maxNotional = 3000;

  return (
    <div>
      <div style={{ fontSize: F.sm, fontWeight: 800, color: C.text, marginBottom: 6 }}>
        How Confidence Affects Position Size
      </div>
      <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 20, lineHeight: 1.6 }}>
        Higher confidence = bot sizes up, but max risk per trade stays fixed at 1.5%
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        {examples.map((ex) => {
          const barPct = (ex.notional / maxNotional) * 100;
          return (
            <div
              key={ex.label}
              style={{
                background: ex.barColor + '0e',
                border: `1px solid ${ex.barColor}30`,
                borderRadius: R.md,
                padding: '16px 14px',
              }}
            >
              {/* Confidence badge */}
              <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: F.xs, color: C.muted, fontWeight: 600 }}>{ex.label}</span>
                <span style={{
                  fontSize: F.xs,
                  fontWeight: 800,
                  padding: '2px 8px',
                  borderRadius: R.pill,
                  background: ex.barColor + '22',
                  color: ex.barColor,
                  border: `1px solid ${ex.barColor}44`,
                }}>
                  {ex.confidence}%
                </span>
              </div>

              {/* Horizontal position bar */}
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>Notional Size</div>
                <div style={{ height: 18, background: C.border + '60', borderRadius: R.sm, overflow: 'hidden', position: 'relative' }}>
                  <div style={{
                    width: `${barPct}%`,
                    height: '100%',
                    background: ex.barColor,
                    borderRadius: R.sm,
                    transition: 'width 0.3s',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'flex-end',
                    paddingRight: 4,
                  }}>
                    {barPct > 20 && (
                      <span style={{ fontSize: 9, fontWeight: 700, color: '#fff', whiteSpace: 'nowrap' }}>
                        ${ex.notional.toLocaleString()}
                      </span>
                    )}
                  </div>
                  {barPct <= 20 && (
                    <span style={{ position: 'absolute', left: `${barPct + 2}%`, top: 2, fontSize: 9, fontWeight: 700, color: ex.barColor, whiteSpace: 'nowrap' }}>
                      ${ex.notional.toLocaleString()}
                    </span>
                  )}
                </div>
              </div>

              {/* Leverage pill */}
              <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 10, color: C.muted }}>Leverage:</span>
                <span style={{
                  fontSize: F.xs,
                  fontWeight: 700,
                  padding: '1px 7px',
                  borderRadius: R.pill,
                  background: C.surfaceHover,
                  color: ex.barColor,
                  border: `1px solid ${ex.barColor}33`,
                }}>
                  {ex.leverage}
                </span>
              </div>

              {/* Risk line */}
              <div style={{
                fontSize: F.xs,
                color: C.muted,
                padding: '6px 8px',
                background: C.warn + '0e',
                border: `1px solid ${C.warn}22`,
                borderRadius: R.sm,
                lineHeight: 1.5,
              }}>
                Risk: <strong style={{ color: C.warn }}>${ex.riskDollars}</strong>{' '}
                <span style={{ color: C.faint }}>(1.5% of $10K)</span>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{
        marginTop: 14,
        padding: '10px 14px',
        background: C.brand + '10',
        border: `1px solid ${C.brand}30`,
        borderRadius: R.md,
        fontSize: F.xs,
        color: C.textSub,
        lineHeight: 1.7,
      }}>
        The dollar risk ($150) stays constant across all confidence levels — only the position notional and leverage change. This is achieved by adjusting quantity, not the stop width.
      </div>
    </div>
  );
}

// ─── RegimeWheel ─────────────────────────────────────────────────────────────

function RegimeWheel({ currentRegime = 'trend' }: { currentRegime?: string }) {
  const cx = 150, cy = 150, r = 110, innerR = 42;
  const regimes = [
    { name: 'trend',             color: '#16a34a', brightColor: '#22c55e', label: 'trend' },
    { name: 'range',             color: '#4b5563', brightColor: '#9ca3af', label: 'range' },
    { name: 'panic',             color: '#dc2626', brightColor: '#f87171', label: 'panic' },
    { name: 'high_volatility',   color: '#d97706', brightColor: '#fbbf24', label: 'hi-vol' },
    { name: 'low_liquidity',     color: '#7c3aed', brightColor: '#a78bfa', label: 'lo-liq' },
    { name: 'news_dislocation',  color: '#2563eb', brightColor: '#60a5fa', label: 'news' },
  ];
  const n = regimes.length;
  const sliceDeg = 360 / n;
  const gapDeg = 3;

  // Transition probabilities shown on arcs between adjacent segments
  const transitions = [
    { from: 0, to: 1, label: '40%' },
    { from: 1, to: 2, label: '15%' },
    { from: 2, to: 0, label: '30%' },
    { from: 3, to: 0, label: '55%' },
    { from: 4, to: 1, label: '45%' },
    { from: 5, to: 0, label: '35%' },
  ];

  const toRad = (deg: number) => (deg * Math.PI) / 180;

  function slicePath(index: number): string {
    const startDeg = index * sliceDeg - 90 + gapDeg / 2;
    const endDeg   = startDeg + sliceDeg - gapDeg;
    const a1 = toRad(startDeg);
    const a2 = toRad(endDeg);
    const ox1 = cx + r * Math.cos(a1), oy1 = cy + r * Math.sin(a1);
    const ox2 = cx + r * Math.cos(a2), oy2 = cy + r * Math.sin(a2);
    const ix1 = cx + innerR * Math.cos(a1), iy1 = cy + innerR * Math.sin(a1);
    const ix2 = cx + innerR * Math.cos(a2), iy2 = cy + innerR * Math.sin(a2);
    const large = sliceDeg - gapDeg > 180 ? 1 : 0;
    return `M ${ix1} ${iy1} L ${ox1} ${oy1} A ${r} ${r} 0 ${large} 1 ${ox2} ${oy2} L ${ix2} ${iy2} A ${innerR} ${innerR} 0 ${large} 0 ${ix1} ${iy1} Z`;
  }

  function labelPos(index: number): { x: number; y: number } {
    const midDeg = index * sliceDeg - 90 + sliceDeg / 2;
    const midR = (r + innerR) / 2;
    return { x: cx + midR * Math.cos(toRad(midDeg)), y: cy + midR * Math.sin(toRad(midDeg)) };
  }

  function arrowPos(fromIdx: number, toIdx: number): { x: number; y: number; angle: number } {
    const midFromDeg = fromIdx * sliceDeg - 90 + sliceDeg / 2;
    const midToDeg   = toIdx   * sliceDeg - 90 + sliceDeg / 2;
    const edgeR = r + 14;
    const mx = cx + edgeR * Math.cos(toRad((midFromDeg + midToDeg) / 2));
    const my = cy + edgeR * Math.sin(toRad((midFromDeg + midToDeg) / 2));
    const angle = (midFromDeg + midToDeg) / 2;
    return { x: mx, y: my, angle };
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: 12, marginBottom: 8 }}>
      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 10 }}>
        Market Regime Wheel
      </div>
      <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
        <svg width={300} height={300} style={{ display: 'block' }}>
          <defs>
            <filter id="regimePulse">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          {/* Segment arcs */}
          {regimes.map((regime, i) => {
            const isActive = regime.name === currentRegime;
            return (
              <path
                key={regime.name}
                d={slicePath(i)}
                fill={isActive ? regime.brightColor : regime.color}
                opacity={isActive ? 1 : 0.65}
                style={{ transition: 'fill 0.3s, opacity 0.3s' }}
              />
            );
          })}

          {/* Pulsing ring for active regime */}
          {(() => {
            const activeIdx = regimes.findIndex(r => r.name === currentRegime);
            if (activeIdx < 0) return null;
            const midDeg = activeIdx * sliceDeg - 90 + sliceDeg / 2;
            const pr = (r + innerR) / 2;
            const px = cx + pr * Math.cos(toRad(midDeg));
            const py = cy + pr * Math.sin(toRad(midDeg));
            return (
              <circle
                cx={px} cy={py} r={18}
                fill="none"
                stroke={regimes[activeIdx].brightColor}
                strokeWidth={2}
                opacity={0.6}
                filter="url(#regimePulse)"
              />
            );
          })()}

          {/* Labels */}
          {regimes.map((regime, i) => {
            const pos = labelPos(i);
            const isActive = regime.name === currentRegime;
            return (
              <text
                key={regime.name}
                x={pos.x} y={pos.y + 4}
                textAnchor="middle"
                fontSize={isActive ? 9 : 8}
                fontWeight={isActive ? 800 : 600}
                fill={isActive ? '#ffffff' : '#e2e8f0'}
                opacity={isActive ? 1 : 0.85}
              >
                {regime.label}
              </text>
            );
          })}

          {/* Transition arrows */}
          {transitions.map(({ from, to, label }, i) => {
            const pos = arrowPos(from, to);
            const fromColor = regimes[from].color;
            return (
              <g key={i}>
                <text
                  x={pos.x} y={pos.y}
                  textAnchor="middle"
                  fontSize={7}
                  fontWeight={700}
                  fill={fromColor}
                  opacity={0.9}
                >
                  {label}
                </text>
              </g>
            );
          })}

          {/* Center */}
          <circle cx={cx} cy={cy} r={innerR - 2} fill="#0f172a" stroke="#334155" strokeWidth={1.5} />
          <text x={cx} y={cy - 5} textAnchor="middle" fontSize={9} fontWeight={700} fill="#64748b">MARKET</text>
          <text x={cx} y={cy + 7} textAnchor="middle" fontSize={9} fontWeight={700} fill="#64748b">REGIME</text>
          <text x={cx} y={cy + 19} textAnchor="middle" fontSize={8} fill="#6366f1" fontWeight={800}>
            {currentRegime}
          </text>
        </svg>
      </div>
      {/* Legend */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center', marginTop: 8 }}>
        {regimes.map(regime => (
          <div key={regime.name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <div style={{ width: 8, height: 8, borderRadius: 2, background: regime.brightColor }} />
            <span style={{ fontSize: 10, color: regime.name === currentRegime ? regime.brightColor : '#64748b', fontWeight: regime.name === currentRegime ? 700 : 400 }}>
              {regime.name}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── StrategyContributionSankey ───────────────────────────────────────────────

function StrategyContributionSankey() {
  const W = 500, H = 200;
  const leftX = 10, rightX = 390;
  const nodeW = 110;
  const strategies = [
    { name: 'regime_trend',      weight: 40, color: '#6366f1' },
    { name: 'monte_carlo',       weight: 25, color: '#2563eb' },
    { name: 'confidence_scorer', weight: 20, color: '#d97706' },
    { name: 'multi_tier_quality',weight: 15, color: '#7c3aed' },
  ];
  const outcomes = [
    { name: 'WIN',  pct: 77, color: '#16a34a' },
    { name: 'LOSS', pct: 23, color: '#dc2626' },
  ];

  const padV = 10;
  const totalH = H - padV * 2;

  // Strategy box heights proportional to weight (sum=100)
  const totalWeight = strategies.reduce((s, x) => s + x.weight, 0);
  const stratGap = 6;
  const stratBoxes: { y: number; h: number; strategy: typeof strategies[0] }[] = [];
  let sy = padV;
  strategies.forEach(st => {
    const h = (st.weight / totalWeight) * totalH - stratGap;
    stratBoxes.push({ y: sy, h: Math.max(h, 12), strategy: st });
    sy += h + stratGap;
  });

  // Outcome box heights proportional to pct
  const totalPct = outcomes.reduce((s, x) => s + x.pct, 0);
  const outcomeGap = 10;
  const outcomeBoxes: { y: number; h: number; outcome: typeof outcomes[0] }[] = [];
  let oy = padV;
  outcomes.forEach(oc => {
    const h = (oc.pct / totalPct) * totalH - outcomeGap;
    outcomeBoxes.push({ y: oy, h: Math.max(h, 14), outcome: oc });
    oy += h + outcomeGap;
  });

  // Generate Sankey paths: each strategy connects to both outcomes
  // Flow width ∝ strategy weight × outcome pct
  const paths: { d: string; color: string; opacity: number }[] = [];
  stratBoxes.forEach(sb => {
    const sCenter = sb.y + sb.h / 2;
    outcomeBoxes.forEach(ob => {
      // Width of path at source proportional to (strategy weight) * (outcome %) / 100
      const flowH = sb.h * (ob.outcome.pct / 100);
      const halfFH = flowH / 2;
      const oCenter = ob.y + ob.h * (ob.outcome.pct / 100) / 2 + (ob.outcome.name === 'WIN' ? 0 : ob.h * 0.77 / 2);

      const x1 = leftX + nodeW;
      const x2 = rightX;
      const mx = (x1 + x2) / 2;

      const ys = sCenter - halfFH;
      const ye = ob.y + ob.h * (ob.outcome.name === 'WIN' ? 0 : 0.77);

      const d = `M ${x1} ${ys} C ${mx} ${ys}, ${mx} ${ye}, ${x2} ${ye} L ${x2} ${ye + flowH} C ${mx} ${ye + flowH}, ${mx} ${ys + flowH}, ${x1} ${ys + flowH} Z`;
      paths.push({ d, color: ob.outcome.color, opacity: 0.18 + sb.strategy.weight / 200 });
    });
  });

  return (
    <div style={{ marginTop: 12, marginBottom: 8 }}>
      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 10 }}>
        Strategy → Outcome Flow
      </div>
      <div style={{ overflowX: 'auto' }}>
        <svg width={W} height={H} style={{ display: 'block', minWidth: W }}>
          {/* Sankey flow paths */}
          {paths.map((p, i) => (
            <path key={i} d={p.d} fill={p.color} opacity={p.opacity} />
          ))}

          {/* Strategy boxes (left) */}
          {stratBoxes.map(({ y, h, strategy }) => (
            <g key={strategy.name}>
              <rect x={leftX} y={y} width={nodeW} height={h} rx={4} fill={strategy.color} opacity={0.85} />
              <text x={leftX + 6} y={y + h / 2 - 4} fontSize={8} fontWeight={700} fill="#fff">{strategy.name}</text>
              <text x={leftX + 6} y={y + h / 2 + 6} fontSize={8} fill="rgba(255,255,255,0.75)">{strategy.weight}% weight</text>
            </g>
          ))}

          {/* Outcome boxes (right) */}
          {outcomeBoxes.map(({ y, h, outcome }) => (
            <g key={outcome.name}>
              <rect x={rightX} y={y} width={nodeW} height={h} rx={4} fill={outcome.color} opacity={0.85} />
              <text x={rightX + nodeW / 2} y={y + h / 2 - 4} textAnchor="middle" fontSize={11} fontWeight={800} fill="#fff">{outcome.name}</text>
              <text x={rightX + nodeW / 2} y={y + h / 2 + 8} textAnchor="middle" fontSize={9} fill="rgba(255,255,255,0.8)">{outcome.pct}%</text>
            </g>
          ))}

          {/* Center label */}
          <text x={W / 2} y={H / 2 + 4} textAnchor="middle" fontSize={9} fill="#334155" fontWeight={700}>contribution</text>
        </svg>
      </div>
      <div style={{ fontSize: 10, color: '#64748b', marginTop: 6 }}>
        Flow width is proportional to each strategy&apos;s contribution to wins and losses. Wider green paths = stronger edge.
      </div>
    </div>
  );
}

// ─── RiskParameterSlider ──────────────────────────────────────────────────────

function RiskParameterSlider() {
  const [riskPct, setRiskPct]   = useState(15);  // 0–100 mapped to 0.1%–5%
  const [confThresh, setConfThresh] = useState(75); // 0–100 raw
  const [maxLevRaw, setMaxLevRaw]   = useState(50); // 0–100 mapped to 1×–20×

  const actualRisk     = 0.1 + (riskPct / 100) * 4.9;          // 0.1%–5%
  const actualConf     = confThresh;                             // 0–100
  const actualMaxLev   = 1 + (maxLevRaw / 100) * 19;           // 1×–20×

  // Derived stats — educational approximations
  const winRate = 0.5 + (actualConf / 100) * 0.35;             // 50%–85% as conf rises
  const avgWin  = 2.5 * actualRisk;                             // avg win ≈ 2.5× risk
  const avgLoss = actualRisk;
  const ev      = winRate * avgWin - (1 - winRate) * avgLoss;   // expected value %
  const evPerTrade = ev;

  // Risk of ruin (simplified Kelly formula)
  const base    = (1 - winRate) / winRate;
  const rawRoR  = base < 1 ? Math.pow(base, 100 / actualRisk) * 100 : 100;
  const rorPct  = Math.min(99, Math.max(0, rawRoR));

  // Projected monthly return (20 trades/month)
  const monthlyReturn = ev * 20;

  const evColor   = evPerTrade >= 0 ? '#16a34a' : '#dc2626';
  const rorColor  = rorPct < 5 ? '#16a34a' : rorPct < 20 ? '#d97706' : '#dc2626';
  const mthColor  = monthlyReturn >= 0 ? '#16a34a' : '#dc2626';

  const sliders = [
    {
      label: 'Risk per Trade',
      unit: '%',
      value: riskPct,
      display: actualRisk.toFixed(1) + '%',
      setter: setRiskPct,
      color: '#d97706',
      min: 0, max: 100,
      hint: 'Low → 0.1% | High → 5%',
    },
    {
      label: 'Confidence Threshold',
      unit: '',
      value: confThresh,
      display: actualConf + ' / 100',
      setter: setConfThresh,
      color: '#6366f1',
      min: 0, max: 100,
      hint: 'Low → more trades, less selective',
    },
    {
      label: 'Max Leverage',
      unit: '×',
      value: maxLevRaw,
      display: actualMaxLev.toFixed(0) + '×',
      setter: setMaxLevRaw,
      color: '#dc2626',
      min: 0, max: 100,
      hint: 'Low → 1× | High → 20×',
    },
  ];

  return (
    <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 12, padding: '20px 22px', marginTop: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 800, color: '#e2e8f0', marginBottom: 4 }}>Risk Parameter Explorer</div>
      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 20, lineHeight: 1.6 }}>
        Drag the sliders to see how risk parameters affect expected value, risk of ruin, and monthly return.
      </div>

      {/* Sliders */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginBottom: 22 }}>
        {sliders.map(sl => (
          <div key={sl.label}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>{sl.label}</span>
              <span style={{ fontSize: 14, fontWeight: 800, color: sl.color, fontVariantNumeric: 'tabular-nums' }}>{sl.display}</span>
            </div>
            {/* Visual bar track */}
            <div style={{ position: 'relative', height: 20 }}>
              <div style={{ position: 'absolute', top: 8, left: 0, right: 0, height: 4, background: '#334155', borderRadius: 4 }} />
              <div style={{ position: 'absolute', top: 8, left: 0, height: 4, width: `${sl.value}%`, background: sl.color, borderRadius: 4 }} />
              <input
                type="range"
                min={sl.min}
                max={sl.max}
                value={sl.value}
                onChange={e => sl.setter(+e.target.value)}
                aria-label={sl.label}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: 20,
                  opacity: 0,
                  cursor: 'pointer',
                  margin: 0,
                  padding: 0,
                  zIndex: 2,
                }}
              />
              {/* Thumb indicator */}
              <div style={{
                position: 'absolute',
                top: 4,
                left: `calc(${sl.value}% - 6px)`,
                width: 12,
                height: 12,
                borderRadius: '50%',
                background: sl.color,
                border: '2px solid #0f172a',
                pointerEvents: 'none',
              }} />
            </div>
            <div style={{ fontSize: 9, color: '#475569', marginTop: 2 }}>{sl.hint}</div>
          </div>
        ))}
      </div>

      {/* Live output cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 16 }}>
        {[
          { label: 'Expected Value', value: (evPerTrade >= 0 ? '+' : '') + evPerTrade.toFixed(2) + '%', sub: 'per trade', color: evColor },
          { label: 'Risk of Ruin', value: rorPct.toFixed(1) + '%', sub: 'probability', color: rorColor },
          { label: 'Proj. Monthly', value: (monthlyReturn >= 0 ? '+' : '') + monthlyReturn.toFixed(1) + '%', sub: '20 trades', color: mthColor },
        ].map(card => (
          <div key={card.label} style={{
            padding: '12px 10px',
            background: card.color + '12',
            border: `1px solid ${card.color}30`,
            borderRadius: 8,
            textAlign: 'center',
          }}>
            <div style={{ fontSize: 9, color: '#64748b', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.4 }}>{card.label}</div>
            <div style={{ fontSize: 18, fontWeight: 900, color: card.color, fontVariantNumeric: 'tabular-nums' }}>{card.value}</div>
            <div style={{ fontSize: 9, color: '#475569', marginTop: 2 }}>{card.sub}</div>
          </div>
        ))}
      </div>

      {/* Formula */}
      <div style={{ padding: '10px 14px', background: '#111827', border: '1px solid #1e293b', borderRadius: 8, fontFamily: 'monospace' }}>
        <div style={{ fontSize: 10, color: '#475569', marginBottom: 4 }}>Formula</div>
        <div style={{ fontSize: 12, color: '#e2e8f0', fontWeight: 700, marginBottom: 2 }}>
          EV = WR &times; AvgWin &minus; (1&minus;WR) &times; AvgLoss
        </div>
        <div style={{ fontSize: 10, color: '#64748b', marginTop: 4 }}>
          WR ≈ {(winRate * 100).toFixed(0)}% &nbsp;|&nbsp;
          AvgWin ≈ {avgWin.toFixed(2)}% &nbsp;|&nbsp;
          AvgLoss ≈ {avgLoss.toFixed(2)}% &nbsp;→&nbsp;
          <span style={{ color: evColor, fontWeight: 700 }}>EV = {evPerTrade >= 0 ? '+' : ''}{evPerTrade.toFixed(2)}%</span>
        </div>
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function Learn() {
  const [glossarySearch, setGlossarySearch] = useState('');
  const filteredGlossary = GLOSSARY.filter(
    (g) =>
      g.term.toLowerCase().includes(glossarySearch.toLowerCase()) ||
      g.def.toLowerCase().includes(glossarySearch.toLowerCase())
  );

  const LEARN_SECTIONS = [
    { href: '#what-is-this-bot',    num: '01', label: 'Foundation',        sub: 'What is this bot?' },
    { href: '#signals',             num: '02', label: 'Signals',           sub: 'Score & indicators' },
    { href: '#ai-brain',            num: '03', label: 'AI Brain',          sub: 'Agents & decisions' },
    { href: '#risk-management',     num: '04', label: 'Risk Management',   sub: 'Safety & sizing' },
    { href: '#trade-flow',          num: '05', label: 'Trade Flow',        sub: 'Signal → execution' },
    { href: '#calculators',         num: '06', label: 'Calculators',       sub: 'Interactive tools' },
    { href: '#copy-trade-guide',    num: '07', label: 'Copy Trade Guide',  sub: 'Step-by-step' },
    { href: '#glossary',            num: '08', label: 'Glossary',          sub: `${GLOSSARY.length} terms` },
  ];

  return (
    <div className="bg-aurora" style={{ position: 'relative' }}>
      <div className="floating-orb orb-brand" style={{ position: 'fixed', top: '10%', left: '5%' }} />
      <div className="floating-orb orb-cyan" style={{ position: 'fixed', bottom: '15%', right: '10%' }} />

      {/* ── Header ───────────────────────────────────── */}
      <motion.div variants={fadeUp} initial="hidden" animate="show" style={{ marginBottom: 28 }}>
        <div style={{ fontSize: F.xs, color: C.brand, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
          Knowledge Base
        </div>
        <h1 style={{ margin: 0, fontSize: F['3xl'], fontWeight: 900, color: C.text, letterSpacing: -0.5 }}>
          Understand the <span className="gradient-text">Edge</span>
        </h1>
        <p style={{ margin: '6px 0 0', fontSize: F.sm, color: C.muted, maxWidth: 600 }}>
          Every signal explained. Every decision unpacked. The more you know, the better you&apos;ll trade.
        </p>
      </motion.div>

      {/* ── Two-column layout: sidebar + content ─────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 40, alignItems: 'start' }} className="learn-layout">

        {/* ── Sticky sidebar ───────────────────────────── */}
        <aside
          className="learn-sidebar glass-noise"
          style={{
            ...Glass.crystal,
            position: 'sticky',
            top: 68,
            maxHeight: 'calc(100vh - 90px)',
            overflowY: 'auto',
            paddingBottom: 20,
            borderRadius: R.lg,
            padding: SP[4],
          }}
        >
          <div style={{ fontSize: 10, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: 1.2, marginBottom: 14, paddingLeft: 10 }}>
            Contents
          </div>
          {LEARN_SECTIONS.map(({ href, num, label, sub }) => (
            <a
              key={href}
              href={href}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 10,
                padding: '8px 10px',
                borderRadius: R.md,
                textDecoration: 'none',
                marginBottom: 2,
                transition: 'background 0.12s',
              }}
              className="sidebar-link"
            >
              <span style={{ fontSize: 10, fontWeight: 700, color: C.brand, minWidth: 18, paddingTop: 1 }}>{num}</span>
              <div>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: C.textSub, lineHeight: 1.2, marginBottom: 1 }}>{label}</div>
                <div style={{ fontSize: 10.5, color: C.muted }}>{sub}</div>
              </div>
            </a>
          ))}
        </aside>

        {/* ── Main content ─────────────────────────────── */}
        <div>

      {/* Living case studies — real lessons from the bot, ahead of the static curriculum */}
      <LiveCaseStudies />

      {/* ─────────────────────────────────── */}
      <div id="what-is-this-bot" />
      <div style={{ marginBottom: 8, paddingTop: 8 }}>
        <div style={{ fontSize: F.xs, color: C.brand, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>01 — Foundation</div>
        <h2 style={{ margin: '0 0 16px', fontSize: F.xl, fontWeight: 700, color: C.text }}>What Is This Bot?</h2>
      </div>

      <AccordionCard title="The 4 Core Strategies" badge="How it works" defaultOpen>
        <p>The bot runs 4 independent strategies simultaneously. Each one looks at different aspects of the market:</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12, marginTop: 12 }}>
          {[
            { name: 'Regime Trend', tf: '1h + 6h', desc: 'Identifies the direction of the trend using WaveTrend and MACD. Only trades in the direction of the dominant regime.' },
            { name: 'Monte Carlo Zones', tf: 'Daily', desc: 'Simulates 1,000 possible future price paths to find statistical support/resistance levels. Trades near high-probability reversal zones.' },
            { name: 'Confidence Scorer', tf: '1h', desc: 'Scores each setup based on 5+ confluence factors: ADX trend strength, MACD, Bollinger/Keltner squeeze, RSI divergence. Higher score = stronger setup.' },
            { name: 'Multi-Tier Quality', tf: '1h + 6h', desc: 'Uses EMA crossovers, VWAP anchoring, and multi-timeframe regime alignment. Only trades when the 1h signal and 6h regime agree.' },
          ].map((s) => (
            <div key={s.name} style={{ padding: '12px 14px', background: C.surfaceHover, borderRadius: R.md }}>
              <div style={{ fontSize: F.sm, fontWeight: 700, color: C.brand, marginBottom: 2 }}>{s.name}</div>
              <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 6 }}>Timeframe: {s.tf}</div>
              <div style={{ fontSize: F.xs, color: C.textSub, lineHeight: 1.6 }}>{s.desc}</div>
            </div>
          ))}
        </div>
        <StrategyShinesGrid />
        <StrategyContributionSankey />
      </AccordionCard>

      <AccordionCard title="How Ensemble Voting Works" badge="Weighted-Veto">
        <p>The 4 strategies each cast a vote (BUY, SELL, or abstain). The ensemble uses "weighted-veto" mode:</p>
        <ol style={{ paddingLeft: 20, marginTop: 8 }}>
          <li style={{ marginBottom: 8 }}><strong>Weighted votes:</strong> Each strategy has a performance-based weight. A strategy that has been winning recently gets more influence.</li>
          <li style={{ marginBottom: 8 }}><strong>Minimum votes:</strong> At least 2 strategies must agree on direction before any trade is considered.</li>
          <li style={{ marginBottom: 8 }}><strong>Veto rule:</strong> If any strategy sees a strong counter-signal (e.g., the trend strategy says SHORT while others say LONG), it can veto the trade entirely.</li>
          <li><strong>Confidence floor:</strong> The combined signal must score ≥ 75 confidence before advancing to LLM review.</li>
        </ol>
        <StrategyVotingVisual />
        <InfoBox color={C.bull}>
          This system is designed to only trade when multiple independent analyses agree. A single strategy firing alone is not enough.
        </InfoBox>
      </AccordionCard>

      <AccordionCard title="What Would the AI Do? — Scenario Simulator" badge="Interactive" badgeColor={C.brand} defaultOpen>
        <ScenarioSimulator />
      </AccordionCard>

      <AccordionCard title="Why Hyperliquid?">
        <p>Hyperliquid is a high-performance perpetuals exchange with several features that make it ideal for algo trading:</p>
        <ul style={{ paddingLeft: 20 }}>
          <li style={{ marginBottom: 6 }}>Sub-second order execution — critical for tight stop losses</li>
          <li style={{ marginBottom: 6 }}>Low fees (≈0.035% maker, 0.05% taker) — important for frequent trading</li>
          <li style={{ marginBottom: 6 }}>Onchain settlement — transparent, auditable</li>
          <li style={{ marginBottom: 6 }}>Deep liquidity on BTC, ETH, SOL, HYPE and 50+ other perps</li>
          <li>Up to 50× leverage available (bot uses 2-10× depending on confidence)</li>
        </ul>
        <ExchangeComparisonChart />
      </AccordionCard>

      {/* ─────────────────────────────────── */}
      <div style={{ height: 24 }} />
      <div id="signals" />
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: F.xs, color: C.brand, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>02 — Signals</div>
        <h2 style={{ margin: '0 0 16px', fontSize: F.xl, fontWeight: 700, color: C.text }}>Understanding Signals</h2>
      </div>

      <AccordionCard title="Signal Score: What 0-100 Means" badge="Confidence">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
          {[
            { range: '0–49', label: 'Weak / No Trade', color: C.bear, desc: 'Bot ignores. Not enough agreement between strategies.' },
            { range: '50–64', label: 'Below Threshold', color: C.warn, desc: 'Some agreement but not enough for the confidence floor. Bot waits.' },
            { range: '65–74', label: 'Moderate', color: C.warnMid, desc: 'Enters consideration queue. LLM reviews and often skips.' },
            { range: '75–84', label: 'Good Setup', color: C.bull, desc: 'Typically trades with standard sizing.' },
            { range: '85–100', label: 'Strong Setup', color: '#22c55e', desc: 'High confidence. Bot may use larger position size.' },
          ].map(({ range, label, color, desc }) => (
            <div key={range} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ width: 48, flexShrink: 0, fontWeight: 700, fontSize: F.sm, color }}>{range}</div>
              <div style={{ flex: 1, height: 6, background: C.surfaceHover, borderRadius: R.pill, overflow: 'hidden' }}>
                <div style={{ width: `${parseInt(range.split('–')[1])}%`, height: '100%', background: color, borderRadius: R.pill }} />
              </div>
              <div style={{ minWidth: 120, fontSize: F.xs, fontWeight: 700, color }}>{label}</div>
              <div style={{ flex: 2, fontSize: F.xs, color: C.muted }}>{desc}</div>
            </div>
          ))}
        </div>
        <RSIScale />
        <ConfidenceGauge value={78} />
      </AccordionCard>

      <AccordionCard title="How Confidence Affects Position Size" badge="Sizing" badgeColor={C.brand}>
        <p style={{ marginBottom: 16 }}>
          Confidence score directly controls how large the bot sizes a trade. Risk per trade stays capped at 1.5% of account equity — only the notional exposure changes.
        </p>
        <PositionSizeDemo />
      </AccordionCard>

      <AccordionCard title="Accumulation & Distribution Zones" badge="Price Zones">
        <p>The Monte Carlo strategy generates four key price levels relative to current price:</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, margin: '12px 0' }}>
          {[
            { label: 'Safe Distribution', color: '#7f1d1d', desc: 'Very expensive. Strong sell zone. Price rarely sustains here.' },
            { label: 'Distribution', color: C.bear, desc: 'Above fair value. Consider taking profit on longs.' },
            { label: '── Current Price ──', color: C.info, desc: '' },
            { label: 'Accumulation', color: C.bull, desc: 'Below fair value. Consider building long positions.' },
            { label: 'Deep Accumulation', color: '#166534', desc: 'Very cheap. High-probability long entry. Strong support.' },
          ].map(({ label, color, desc }) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 12, height: 12, borderRadius: 3, background: color, flexShrink: 0 }} />
              <div style={{ fontSize: F.sm, fontWeight: 600, color, minWidth: 160 }}>{label}</div>
              {desc && <div style={{ fontSize: F.xs, color: C.muted }}>{desc}</div>}
            </div>
          ))}
        </div>
        <InfoBox color={C.info}>
          These zones shift every day as the bot recalculates based on recent volatility. A zone that was "Deep Accumulation" yesterday may be "Accumulation" today if price has moved.
        </InfoBox>
      </AccordionCard>

      <AccordionCard title="Volatility Cycle: Squeeze → Breakout → Trend" badge="Vol Cycle" badgeColor={C.bull}>
        <p>Markets move in cycles of <strong>compression</strong> and <strong>expansion</strong>. When Bollinger Bands squeeze inside Keltner Channels, energy is coiling. The bot watches for this pattern to anticipate the next big move:</p>
        <VolatilityCycleDiagram />
        <InfoBox color={C.info}>
          The bot uses the squeeze detector in the Confidence Scorer strategy. A confirmed squeeze breakout raises the signal confidence score and can unlock higher leverage tiers.
        </InfoBox>
      </AccordionCard>

      <AccordionCard title="How Does a Trade Actually Flow?" badge="Signal → Execution" badgeColor={C.bull}>
        <p>Every signal travels through a deterministic pipeline before a single dollar is put at risk. Here&apos;s the full path from detection to execution — or rejection:</p>
        <TradingFlowDiagram />
        <InfoBox color={C.info}>
          Gates run sequentially — a signal rejected at Gate 2 never reaches Gate 3. Every rejection is logged with the reason so you can audit exactly why a potential trade was blocked.
        </InfoBox>
      </AccordionCard>

      <AccordionCard title="Regime Types Explained" badge="Market States" defaultOpen={false}>
        <p>The Regime Agent classifies the market every evaluation cycle. This classification affects which strategies run and how aggressively the bot sizes up:</p>
        <RegimeWheel currentRegime="trend" />
        <RegimeTable />
      </AccordionCard>

      {/* ─────────────────────────────────── */}
      <div style={{ height: 24 }} />
      <div id="ai-brain" />
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: F.xs, color: C.brand, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>03 — AI Brain</div>
        <h2 style={{ margin: '0 0 16px', fontSize: F.xl, fontWeight: 700, color: C.text }}>The LLM Multi-Agent System</h2>
      </div>

      <AccordionCard title="Agent Pipeline Overview" badge="5 Agents" badgeColor={C.brand} defaultOpen>
        <p>When multi-agent mode is enabled, every trade goes through 5 specialist Claude AI agents in sequence:</p>
        <div style={{ overflowX: 'auto', marginBottom: 16 }}>
          <AgentPipelineDiagram />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10, marginTop: 12 }}>
          {[
            { agent: 'Regime', model: 'Claude Haiku', role: 'Classifies market regime + directional outlook. Sets the context for all downstream agents.' },
            { agent: 'Trade', model: 'Claude Sonnet', role: 'Forms a directional thesis. Decides Go / Skip / Flip based on all available signals.' },
            { agent: 'Risk', model: 'Claude Haiku', role: 'Sizes the position. Flags portfolio risk overlaps. Sets leverage tier.' },
            { agent: 'Critic', model: 'Claude Sonnet', role: 'Stress-tests the Trade agent\'s thesis. Must provide a counter-thesis if it wants to veto.' },
            { agent: 'Learning', model: 'Claude Haiku', role: 'Post-close only. Extracts lessons and tracks thesis accuracy per setup type.' },
          ].map(({ agent, model, role }) => (
            <div key={agent} style={{ padding: '10px 12px', background: C.surfaceHover, borderRadius: R.md }}>
              <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text, marginBottom: 2 }}>{agent}</div>
              <div style={{ fontSize: F.xs, color: C.brand, marginBottom: 6 }}>{model}</div>
              <div style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.5 }}>{role}</div>
            </div>
          ))}
        </div>
        <MemorySystemDiagram />
      </AccordionCard>

      <AccordionCard title="Advisory Mode vs Full Autonomy" badge="Mode Levels">
        <p>The bot supports 6 levels of LLM autonomy (<code>LLM_MODE</code> 0-5):</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
          {[
            { level: '0 — OFF', desc: 'LLM disabled. Pure strategy signals only.', color: C.muted },
            { level: '1 — ADVISORY', desc: 'LLM analyses every setup and logs what it would do. Does NOT execute. This is the default safe mode shown on the dashboard.', color: C.info },
            { level: '2 — VETO_ONLY', desc: 'LLM can veto (block) bad trades, but cannot initiate trades on its own.', color: C.warn },
            { level: '3 — SIZING', desc: 'LLM controls position sizing in addition to veto power.', color: C.warn },
            { level: '4 — DIRECTION', desc: 'LLM can change trade direction (long → short flip).', color: C.bear + 'cc' },
            { level: '5 — FULL', desc: 'LLM has full control: can initiate, veto, resize, and flip trades autonomously.', color: C.bear },
          ].map(({ level, desc, color }) => (
            <div key={level} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <div style={{ fontSize: F.xs, fontWeight: 800, color, minWidth: 90, paddingTop: 2 }}>{level}</div>
              <div style={{ fontSize: F.xs, color: C.textSub, lineHeight: 1.6 }}>{desc}</div>
            </div>
          ))}
        </div>
        <AutonomyLevelMeter />
      </AccordionCard>

      <AccordionCard title="What 'AI VETO' Means" badge="VETO" badgeColor={C.bear}>
        <p>A veto happens when the Critic agent (or the LLM in VETO_ONLY+ mode) decides the proposed trade should not be taken.</p>
        <p>The Critic agent is designed to:</p>
        <ul style={{ paddingLeft: 20 }}>
          <li style={{ marginBottom: 6 }}>Challenge the Trade agent's thesis by finding counter-arguments</li>
          <li style={{ marginBottom: 6 }}>Detect conflicting signals the strategies may have missed</li>
          <li style={{ marginBottom: 6 }}>Flag when confidence is high but risk is disproportionate</li>
        </ul>
        <InfoBox color={C.warn}>
          A veto is NOT a guarantee the trade would have lost — it means the AI decided the risk wasn't worth taking given current conditions. In the activity feed, you can see every veto with the reasoning.
        </InfoBox>
      </AccordionCard>

      {/* ─────────────────────────────────── */}
      <div style={{ height: 24 }} />
      <div id="risk-management" />
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: F.xs, color: C.warn, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>04 — Safety</div>
        <h2 style={{ margin: '0 0 16px', fontSize: F.xl, fontWeight: 700, color: C.text }}>Risk Management</h2>
      </div>

      <AccordionCard title="Circuit Breakers" badge="Auto-Stop" badgeColor={C.bear} defaultOpen>
        <p>Circuit breakers are automatic kill switches that prevent catastrophic losses:</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10, marginTop: 8 }}>
          {[
            { name: 'Daily Loss Limit', desc: 'If the bot loses more than X% of current equity in a single day, it stops trading until the next day.' },
            { name: 'Consecutive Losses', desc: 'After N losses in a row, the bot pauses and waits for conditions to improve before re-entering.' },
            { name: 'Position Limits', desc: 'Maximum number of open positions at once. Prevents over-exposure to correlated assets.' },
            { name: 'Drawdown Guard', desc: 'If equity drops below a rolling 30-day peak by more than X%, the bot enters a cautious mode with reduced sizing.' },
          ].map(({ name, desc }) => (
            <div key={name} style={{ padding: '12px 14px', background: C.bear + '10', border: `1px solid ${C.bear}22`, borderRadius: R.md }}>
              <div style={{ fontSize: F.sm, fontWeight: 700, color: C.bear, marginBottom: 4 }}>{name}</div>
              <div style={{ fontSize: F.xs, color: C.textSub, lineHeight: 1.5 }}>{desc}</div>
            </div>
          ))}
        </div>
      </AccordionCard>

      <AccordionCard title="Position Sizing & Leverage">
        <p>The bot uses a fixed fractional position sizing model:</p>
        <InfoBox color={C.brand}>
          <strong>Risk per trade = 1.5% of current equity.</strong> If your account is $10,000, no single trade risks more than $150.
        </InfoBox>
        <p>Leverage is calculated dynamically based on:</p>
        <ul style={{ paddingLeft: 20 }}>
          <li style={{ marginBottom: 6 }}>Signal confidence (higher confidence = higher allowed leverage)</li>
          <li style={{ marginBottom: 6 }}>Number of strategies agreeing (3/4 agreement allows more than 2/4)</li>
          <li style={{ marginBottom: 6 }}>Distance to stop loss (wider stop = lower leverage to keep $ risk constant)</li>
          <li>Market regime (panic or high_volatility = leverage cap reduced)</li>
        </ul>
        <p>The result: leverage typically ranges 2-7× for normal setups and rarely exceeds 10×.</p>
        <LeverageMatrix />
        <RiskRewardVisualizer />
      </AccordionCard>

      <AccordionCard title="Stop Loss Philosophy">
        <p>Stop losses are placed at the <strong>Deep Accumulation zone</strong> for longs (or Safe Distribution zone for shorts) — not at arbitrary percentage levels.</p>
        <p>Why this works better than percentage-based stops:</p>
        <ul style={{ paddingLeft: 20 }}>
          <li style={{ marginBottom: 6 }}>Zones are based on actual volatility (ATR), not arbitrary numbers</li>
          <li style={{ marginBottom: 6 }}>They represent price levels where the trade thesis is genuinely invalid</li>
          <li>They adapt to each asset's current volatility regime</li>
        </ul>
        <p>After TP1 is hit, the stop loss moves to breakeven and a trailing stop activates — locking in profit progressively.</p>
        <StopLossVisual />
      </AccordionCard>

      <AccordionCard title="Why 1.5% Risk Per Trade?" badge="Risk Science" badgeColor={C.bull}>
        <p style={{ marginBottom: 12 }}>
          The 1.5% risk rule is not arbitrary — it is the result of ruin-probability mathematics. The chart below shows how ruin probability collapses as risk per trade decreases.
        </p>
        <RiskOfRuinChart />
        <RiskParameterSlider />
      </AccordionCard>

      <AccordionCard title="Expected Value Calculator" badge="EV" badgeColor={C.brand}>
        <p style={{ marginBottom: 16 }}>
          A strategy is profitable only when its expected value (EV) is positive. EV = (win rate × avg win) − (loss rate × avg loss). Adjust the inputs to see the effect on long-run profitability.
        </p>
        <ExpectedValueCalc />
        <div style={{ marginTop: 16, padding: '10px 14px', background: C.info + '10', border: `1px solid ${C.info}25`, borderRadius: R.sm, fontSize: F.xs, color: C.textSub, lineHeight: 1.7 }}>
          Adjust the inputs to match your own trading results. Positive EV means you make money over enough trades — the exact numbers depend on your actual outcomes.
        </div>
      </AccordionCard>

      <AccordionCard title="Kelly Criterion — Why 1.5% Risk?" badge="Position Sizing" badgeColor={C.warn}>
        <p style={{ marginBottom: 16 }}>
          The Kelly Criterion is a mathematical formula for optimal position sizing. Understanding why the bot uses Quarter-Kelly shows the trade-off between growth and drawdown.
        </p>
        <KellyFormulaDiagram />
      </AccordionCard>

      <AccordionCard title="Market Scenario Simulator" badge="Interactive" badgeColor={C.info}>
        <p style={{ marginBottom: 16 }}>
          See how the bot behaves across 5 different market environments — from a calm uptrend to a flash crash. Projected figures are based on 30-day backtest analogues.
        </p>
        <ScenarioStressTest />
      </AccordionCard>

      <AccordionCard title="Compounding With Consistent Returns" badge="Growth" badgeColor={C.brand}>
        <p style={{ marginBottom: 12 }}>
          Consistent risk management enables compounding. Adjust the inputs below to see how the bot&apos;s target monthly return compounds over time.
        </p>
        <CompoundCalc />
      </AccordionCard>

      {/* ─────────────────────────────────── */}
      <div style={{ height: 24 }} />
      <div id="trade-flow" />
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: F.xs, color: C.bull, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>05 — Process</div>
        <h2 style={{ margin: '0 0 16px', fontSize: F.xl, fontWeight: 700, color: C.text }}>How a Trade Flows</h2>
      </div>

      <div className="card-hover glass-noise" style={{ ...Glass.crystal, borderRadius: R.lg, padding: '20px 24px', marginBottom: 12 }}>
        <p style={{ margin: '0 0 20px', fontSize: F.sm, color: C.textSub }}>
          A signal must pass through 6 sequential gates before becoming a trade. If it fails any gate, it&apos;s rejected and logged.
        </p>
        <GateFlowDiagram />
      </div>

      <div className="card-hover glass-noise" style={{ ...Glass.crystal, borderRadius: R.lg, padding: '20px 24px', marginBottom: 24 }}>
        <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text, marginBottom: 12 }}>After the Trade Opens</div>
        <div style={{ display: 'flex', gap: 0, overflowX: 'auto' }}>
          {[
            { state: 'IDLE', desc: 'No position', color: C.muted },
            { state: 'OPEN', desc: 'Position active, stop loss set', color: C.info },
            { state: 'TP1_HIT', desc: 'First profit target reached, size reduced', color: C.bull },
            { state: 'TRAILING', desc: 'Stop moved to BE+, trailing up', color: C.brand },
            { state: 'CLOSED', desc: 'Trade complete, results logged', color: C.bull },
          ].map((s, i) => (
            <React.Fragment key={s.state}>
              <div style={{ textAlign: 'center', flexShrink: 0, minWidth: 90 }}>
                <div style={{ padding: '6px 10px', background: s.color + '20', border: `1px solid ${s.color}44`, borderRadius: R.sm, marginBottom: 6 }}>
                  <div style={{ fontSize: F.xs, fontWeight: 700, color: s.color }}>{s.state}</div>
                </div>
                <div style={{ fontSize: 10, color: C.muted, lineHeight: 1.4 }}>{s.desc}</div>
              </div>
              {i < 4 && <div style={{ color: C.muted, fontSize: 12, padding: '8px 2px', flexShrink: 0, alignSelf: 'flex-start' }}>→</div>}
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* ─────────────────────────────────── */}
      <div style={{ height: 24 }} />
      <div id="calculators" />
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: F.xs, color: C.brand, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>06 — Tools</div>
        <h2 style={{ margin: '0 0 16px', fontSize: F.xl, fontWeight: 700, color: C.text }}>Trading Calculators</h2>
      </div>

      <AccordionCard title="Position Size Calculator" badge="Risk Per Trade" badgeColor={C.warn} defaultOpen>
        <p style={{ marginBottom: 16 }}>Enter your account size, risk tolerance, and stop distance. The calculator shows exactly how many units to buy and what leverage that implies.</p>
        <PositionSizeCalc />
        <div style={{ marginTop: 16, padding: '10px 14px', background: C.info + '10', border: `1px solid ${C.info}25`, borderRadius: R.sm, fontSize: F.xs, color: C.textSub, lineHeight: 1.7 }}>
          <strong>Pro tip:</strong> The bot uses 1.5% risk per trade by default. At $10K account, that&apos;s $150 max risk per trade. Effective leverage of 3-7× is healthy for most setups.
        </div>
      </AccordionCard>

      <AccordionCard title="Risk/Reward Calculator" badge="R:R Ratio">
        <p style={{ marginBottom: 16 }}>Visualize your setup&apos;s risk-to-reward ratio before taking a trade. The bot requires R:R ≥ 1.0 to pass Gate 1.</p>
        <RRCalc />
        <div style={{ marginTop: 16, padding: '10px 14px', background: C.bull + '10', border: `1px solid ${C.bull}25`, borderRadius: R.sm, fontSize: F.xs, color: C.textSub, lineHeight: 1.7 }}>
          <strong>Target:</strong> R:R ≥ 1.5:1 to TP1 and ≥ 2.5:1 to TP2. Higher R:R means you can be wrong more often and still be profitable overall.
        </div>
      </AccordionCard>

      <AccordionCard title="Compound Growth Calculator" badge="Monthly Returns">
        <p style={{ marginBottom: 16 }}>See how consistent monthly returns compound over time — run the bot to see your actual returns projected forward.</p>
        <CompoundCalc />
        <div style={{ marginTop: 16, padding: '10px 14px', background: C.brand + '10', border: `1px solid ${C.brand}25`, borderRadius: R.sm, fontSize: F.xs, color: C.textSub, lineHeight: 1.7 }}>
          <strong>Reality check:</strong> Past backtest results don&apos;t guarantee future performance. Use conservative estimates (3-5% monthly) for realistic projections.
        </div>
      </AccordionCard>

      {/* ─────────────────────────────────── */}
      <div style={{ height: 24 }} />
      <div id="copy-trade-guide" />
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: F.xs, color: C.brand, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>07 — Copy Trading</div>
        <h2 style={{ margin: '0 0 16px', fontSize: F.xl, fontWeight: 700, color: C.text }}>How to Copy-Trade This Bot</h2>
      </div>

      <AccordionCard title="Step-by-Step: Copy a Signal" badge="Beginner" badgeColor={C.bull} defaultOpen>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {[
            { n: 1, title: 'Watch the Signals page', desc: 'Go to Signals → watch for a signal card to appear with a score ≥ 75. The higher the score, the stronger the bot\'s conviction.' },
            { n: 2, title: 'Check the regime', desc: 'Look at the current regime badge in the top nav. "TREND" or "RANGE" regimes are typically safer. Avoid copying trades in "PANIC" or "HIGH_VOLATILITY" regimes unless you understand the risk.' },
            { n: 3, title: 'Calculate your position size', desc: 'Use the Position Size Calculator above. Risk 1-2% of your capital max. Enter the signal\'s entry, SL, and TP levels into the calculator.' },
            { n: 4, title: 'Set your order on Hyperliquid', desc: 'Open Hyperliquid → select the symbol → set a limit order at the entry price, or market order if price is currently at the entry zone. Set your stop loss immediately.' },
            { n: 5, title: 'Set your take profits', desc: 'Place a TP1 order at the first target (typically 50% of your position), and TP2 for the remainder. Consider setting a trailing stop after TP1 is hit.' },
            { n: 6, title: 'Follow the bot\'s exit signal', desc: 'Check the Activity Feed for exit signals. If the bot shows a regime change or veto on the same symbol, consider closing early.' },
          ].map(({ n, title, desc }) => (
            <div key={n} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
              <div style={{ width: 28, height: 28, borderRadius: '50%', background: C.brand, color: '#fff', fontSize: F.sm, fontWeight: 800, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{n}</div>
              <div>
                <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text, marginBottom: 3 }}>{title}</div>
                <div style={{ fontSize: F.xs, color: C.textSub, lineHeight: 1.6 }}>{desc}</div>
              </div>
            </div>
          ))}
        </div>
      </AccordionCard>

      <AccordionCard title="Common Copy-Trading Mistakes" badge="Avoid These" badgeColor={C.bear}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[
            { mistake: 'Chasing signals after they\'re old', fix: 'Only enter a signal if price is still within 0.5% of the stated entry. If price has moved significantly, the setup has changed.' },
            { mistake: 'Skipping the stop loss', fix: 'ALWAYS set your stop loss before entering. The stop is the only thing protecting you from a large loss. Never trade without one.' },
            { mistake: 'Oversizing positions', fix: 'The signal score says nothing about position size. Use the calculator. 1-2% risk per trade. 10× leverage on a 1% stop = 10% account at risk per trade.' },
            { mistake: 'Copying in PANIC regime', fix: 'During panic regimes, signals have much lower reliability. The bot reduces its own sizing in this mode. You should too — or sit on the sidelines.' },
            { mistake: 'Ignoring the LLM veto', fix: 'If the Activity Feed shows "AI VETO" on the same symbol you\'re about to copy, that\'s a warning. The Critic agent found a reason not to trade this setup.' },
            { mistake: 'Holding through TP2 hoping for more', fix: 'Respect the take-profit levels. They\'re calculated zones where the statistical edge weakens. Greed is how winning trades become losers.' },
          ].map(({ mistake, fix }) => (
            <div key={mistake} style={{ padding: '10px 14px', background: C.bear + '08', border: `1px solid ${C.bear}20`, borderRadius: R.md }}>
              <div style={{ fontSize: F.xs, fontWeight: 700, color: C.bear, marginBottom: 4 }}>✗ {mistake}</div>
              <div style={{ fontSize: F.xs, color: C.textSub, lineHeight: 1.5 }}>→ {fix}</div>
            </div>
          ))}
        </div>
      </AccordionCard>

      <AccordionCard title="Market Psychology: Why Most Traders Lose" badge="Mindset">
        <p>Even with a profitable system, traders lose because of psychology. Here&apos;s what to watch for:</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10, marginTop: 12 }}>
          {[
            { bias: 'FOMO', desc: 'Fear of Missing Out. Causes traders to enter too late, past the entry zone. Solution: set alerts, not market orders. If you missed it, the next signal is coming.' },
            { bias: 'Loss Aversion', desc: 'Holding losers too long hoping they\'ll recover, while cutting winners short. Solution: set TP and SL levels before entry and don\'t move them based on emotion.' },
            { bias: 'Overconfidence', desc: 'After 3 winning trades, traders increase size dramatically. The 4th trade then blows up the gains. Solution: fixed risk % per trade, always.' },
            { bias: 'Revenge Trading', desc: 'After a loss, jumping into the next trade immediately to "get it back." Usually results in a second, bigger loss. Solution: if you\'ve had 2 consecutive losses, take a break.' },
            { bias: 'Recency Bias', desc: 'Assuming recent market conditions will continue forever. "BTC has been going up all month" leads to oversized longs at tops. Solution: trust the regime classification, not your gut.' },
            { bias: 'Analysis Paralysis', desc: 'Waiting for the "perfect" signal while good setups pass. Solution: if a signal meets all criteria (score ≥ 75, regime confirmed, R:R ≥ 1.5), that\'s enough. Act.' },
          ].map(({ bias, desc }) => (
            <div key={bias} style={{ padding: '12px 14px', background: C.purple + '10', border: `1px solid ${C.purple}25`, borderRadius: R.md }}>
              <div style={{ fontSize: F.sm, fontWeight: 700, color: C.purple, marginBottom: 4 }}>{bias}</div>
              <div style={{ fontSize: F.xs, color: C.textSub, lineHeight: 1.5 }}>{desc}</div>
            </div>
          ))}
        </div>
      </AccordionCard>

      {/* ─────────────────────────────────── */}
      <div style={{ height: 24 }} />
      <div id="glossary" />
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: F.xs, color: C.muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>08 — Reference</div>
        <h2 style={{ margin: '0 0 8px', fontSize: F.xl, fontWeight: 700, color: C.text }}>Glossary</h2>
        <input
          type="text"
          placeholder="Search terms…"
          aria-label="Search glossary terms"
          value={glossarySearch}
          onChange={(e) => setGlossarySearch(e.target.value)}
          style={{
            width: '100%',
            maxWidth: 400,
            padding: '8px 14px',
            ...Glass.card,
            border: `1px solid ${C.border}`,
            borderRadius: R.md,
            color: C.text,
            fontSize: F.sm,
            outline: 'none',
          }}
        />
      </div>

      {/* ── Glossary Stats + Tag Cloud ── */}
      <GlossaryStats />

      <div className="card-hover glass-noise" style={{ ...Glass.crystal, borderRadius: R.lg, overflow: 'hidden' }}>
        {filteredGlossary.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: C.muted, fontSize: F.sm }}>No terms matching "{glossarySearch}"</div>
        ) : (
          filteredGlossary.map((item, i) => (
            <div
              key={item.term}
              style={{
                padding: '14px 20px',
                borderBottom: i < filteredGlossary.length - 1 ? `1px solid ${C.border}` : 'none',
                display: 'flex',
                gap: 20,
                alignItems: 'flex-start',
              }}
            >
              <div style={{ fontSize: F.sm, fontWeight: 700, color: C.brand, minWidth: 200, flexShrink: 0 }}>{item.term}</div>
              <div style={{ fontSize: F.sm, color: C.textSub, lineHeight: 1.7 }}>{item.def}</div>
            </div>
          ))
        )}
      </div>

      {/* ── CTA ──────────────────────────────────────── */}
      <div
        style={{
          marginTop: 40,
          padding: '28px 32px',
          background: `linear-gradient(135deg, ${C.brand}18, ${G.card})`,
          border: `1px solid ${C.brand}40`,
          borderRadius: R.xl,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 20,
        }}
      >
        <div>
          <div style={{ fontSize: F.lg, fontWeight: 700, color: C.text, marginBottom: 4 }}>Ready to see it in action?</div>
          <div style={{ fontSize: F.sm, color: C.muted }}>View live signals, copy trades, or explore backtest results.</div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <Link href="/copy-trade" style={{ padding: '10px 20px', background: C.brand, color: '#fff', borderRadius: R.md, fontSize: F.sm, fontWeight: 700, textDecoration: 'none' }}>
            Copy Trade
          </Link>
          <Link href="/results" style={{ padding: '10px 20px', border: `1px solid ${C.brand}`, color: C.brand, borderRadius: R.md, fontSize: F.sm, fontWeight: 700, textDecoration: 'none' }}>
            See Results
          </Link>
        </div>
      </div>

        </div> {/* end content column */}
      </div> {/* end two-column grid */}

      <style>{`
        @media (max-width: 900px) {
          /* minmax(0,1fr) — NOT 1fr — so the single column can't expand past the
             viewport to fit a wide child (tables/diagrams), which was bleeding the
             whole page's inner grids off-screen on phones. */
          .learn-layout { grid-template-columns: minmax(0, 1fr) !important; }
          .learn-sidebar { display: none !important; }
          /* Wide data tables / diagrams scroll inside their own box instead of
             widening the column. */
          .learn-layout table, .learn-layout pre { display: block; overflow-x: auto; }
        }
        .sidebar-link:hover {
          background: ${C.surfaceHover} !important;
        }
        .sidebar-link:hover div:first-child {
          color: ${C.text} !important;
        }
      `}</style>
    </div>
  );
}
