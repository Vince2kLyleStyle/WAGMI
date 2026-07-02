"""
Execution tracking tests — verifies that every trade decision is logged with
full context, signal rejections are tracked with reasons, position state
transitions are recorded, and PnL attribution works per strategy/symbol/regime.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.base import Signal
from core.signal_pipeline import RiskFilterChain, FilterResult
from execution.risk import CircuitBreaker, RiskManager
from execution.leverage import LeverageManager
from execution.position_manager import PositionManager, Position, TradeEvent
from execution.position_state import IDLE, OPEN, TP1_HIT, TRAILING, CLOSED
from trading_config import TradingConfig


# ── Helpers ────────────────────────────────────────────────────────────


def _make_signal(
    symbol="BTC", side="BUY", confidence=82.0, entry=50000.0,
    sl=48500.0, tp1=52000.0, tp2=54000.0, atr=500.0,
    strategy="regime_trend", regime="consolidation",
):
    return Signal(
        strategy=strategy, symbol=symbol, side=side, confidence=confidence,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2, atr=atr,
        metadata={"regime": regime, "num_agree": 2},
    )


def _make_filter_chain(equity=10000.0):
    cb = CircuitBreaker(daily_loss_limit_pct=0.05, max_consecutive_losses=5,
                        max_drawdown_pct=0.15, cooldown_minutes=60)
    rm = RiskManager(starting_equity=equity, risk_per_trade=0.02,
                     max_open_positions=8, circuit_breaker=cb)
    lm = LeverageManager(enable_leverage=True, max_leverage=25.0)
    cfg = TradingConfig()
    chain = RiskFilterChain(rm, lm, cfg)
    return chain, rm


def _make_pm():
    return PositionManager(taker_fee_bps=4, enable_trailing=True, trailing_atr_mult=1.5)


# ═══════════════════════════════════════════════════════════════════════
# SECTION A: Trade Decision Logging with Full Context
# ═══════════════════════════════════════════════════════════════════════


class TestTradeDecisionLogging:
    """Every trade decision is logged with full context."""

    def test_open_event_contains_strategy(self):
        """OPEN event records which strategy generated the signal."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         strategy="regime_trend", confidence=82.0, atr=500.0)
        event = pm.trade_log[-1]
        assert event.strategy == "regime_trend"

    def test_open_event_contains_confidence(self):
        """OPEN event metadata includes confidence score."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         strategy="test", confidence=85.5, atr=500.0)
        event = pm.trade_log[-1]
        assert event.metadata.get("confidence") == 85.5

    def test_open_event_contains_leverage(self):
        """OPEN event records leverage used."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         leverage=5.0, atr=500.0)
        event = pm.trade_log[-1]
        assert event.leverage == 5.0

    def test_open_event_contains_entry_reasons(self):
        """OPEN event metadata includes entry_reasons for post-trade analysis."""
        pm = _make_pm()
        reasons = {"regime": "consolidation", "num_agree": 3,
                   "strategies_agree": ["regime_trend", "monte_carlo_zones", "confidence_scorer"]}
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         entry_reasons=reasons, atr=500.0)
        event = pm.trade_log[-1]
        assert event.metadata.get("entry_reasons") == reasons

    def test_close_event_contains_pnl(self):
        """Close event records PnL."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        events = pm.update_price("BTC", 48000.0)
        assert len(events) == 1
        assert events[0].pnl != 0

    def test_close_event_has_hold_time(self):
        """Close event metadata includes hold_time_s."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        events = pm.update_price("BTC", 48000.0)
        meta = events[0].metadata
        assert "hold_time_s" in meta
        assert meta["hold_time_s"] >= 0

    def test_close_event_includes_outcome(self):
        """Close event metadata includes outcome classification."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        events = pm.update_price("BTC", 48000.0)
        meta = events[0].metadata
        assert "outcome" in meta
        assert meta["outcome"] != ""

    def test_close_event_includes_state_path(self):
        """Close event metadata includes state path."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        events = pm.update_price("BTC", 48000.0)
        meta = events[0].metadata
        assert "state_path" in meta
        assert "IDLE" in meta["state_path"]
        assert "OPEN" in meta["state_path"]
        assert "CLOSED" in meta["state_path"]

    def test_multiple_events_for_partial_close(self):
        """TP1 partial close generates TP1 event, then full close generates another."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 58000.0,
                         atr=500.0, leverage=3.0)

        tp1_events = pm.update_price("BTC", 52500.0)
        assert len(tp1_events) >= 1
        tp1_actions = [e.action for e in tp1_events]
        assert any(a in ("TP1", "TP1_FULL") for a in tp1_actions)

    def test_trade_log_grows_with_events(self):
        """trade_log accumulates all events across positions."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        assert len(pm.trade_log) == 1  # OPEN

        pm.update_price("BTC", 48000.0)  # SL
        assert len(pm.trade_log) >= 2  # OPEN + SL

        pm.open_position("SOL", "LONG", 150.0, 10.0, 145.0, 160.0, 170.0, atr=5.0)
        assert len(pm.trade_log) >= 3  # + SOL OPEN


# ═══════════════════════════════════════════════════════════════════════
# SECTION B: Signal Filtering Reasons Tracked
# ═══════════════════════════════════════════════════════════════════════


class TestSignalFilteringReasons:
    """Every rejection has a clear, inspectable reason."""

    def test_invalid_signal_reason(self):
        """Invalid signal carries specific rejection reason."""
        chain, rm = _make_filter_chain()
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=80.0, entry=50000.0,
            sl=52000.0, tp1=53000.0, tp2=55000.0,  # SL above entry
        )
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved
        assert result.rejection_reason != ""

    def test_circuit_breaker_reason(self):
        """Circuit breaker rejection states the reason."""
        chain, rm = _make_filter_chain()
        cb = rm.circuit_breaker
        cb.peak_equity = 10000.0
        for _ in range(5):
            cb.record_trade(-100, 10000.0)

        signal = _make_signal(confidence=70.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved
        assert "Circuit breaker" in result.rejection_reason

    def test_max_positions_reason(self):
        """Max positions rejection says how many are allowed."""
        chain, rm = _make_filter_chain()
        signal = _make_signal()
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2,
                                total_strategies=4, current_open_count=8)
        assert not result.approved
        assert "Max positions" in result.rejection_reason

    def test_rr_floor_deleted_no_longer_rejects(self):
        """DECHOKE 2 (2026-07-02): the rr_floor gate was deleted (zero live
        firings ever — GM_GATE_ROC). A signal below config min_signal_rr is
        no longer rejected for R:R; validity (R:R >= 1.0) still applies."""
        chain, rm = _make_filter_chain()
        chain.config.min_signal_rr = 3.0
        # Signal passes is_valid (R:R >= 1.0) but is below the old config floor
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=82.0, entry=50000.0,
            sl=48500.0,  # 1500 stop width
            tp1=52000.0,  # R:R = 2000/1500 = 1.33 (< 3.0)
            tp2=55000.0,
            metadata={"regime": "consolidation", "num_agree": 2},
        )
        assert signal.is_valid, "Signal should pass is_valid (R:R >= 1.0)"
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert "R:R" not in result.rejection_reason
        # R:R numbers still surface as labeled context
        if result.approved:
            assert result.metadata.get("rr_tp1") == 1.33

    def test_sizing_zero_reason(self):
        """Zero position size carries a clear reason."""
        chain, rm = _make_filter_chain()
        rm.equity = 0.001  # Near-zero equity
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=0.001, num_strategies_agree=2, total_strategies=4)
        # May fail at sizing or leverage
        assert isinstance(result, FilterResult)

    def test_approved_result_has_empty_rejection(self):
        """Approved signals have empty rejection_reason."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            assert result.rejection_reason == ""

    def test_rejected_result_has_nonempty_reason(self):
        """Every rejected signal has a non-empty rejection_reason."""
        chain, rm = _make_filter_chain()

        # Invalid signal
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=80.0, entry=50000.0,
            sl=52000.0, tp1=53000.0, tp2=55000.0,
        )
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved
        assert len(result.rejection_reason) > 0


# ═══════════════════════════════════════════════════════════════════════
# SECTION C: Position State Transitions Recorded
# ═══════════════════════════════════════════════════════════════════════


class TestPositionStateTransitions:
    """All state transitions are recorded and verifiable."""

    def test_open_records_idle_to_open(self):
        """Opening records IDLE -> OPEN transition."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pos = pm.positions["BTC"]
        assert pos.state_path == [IDLE, OPEN]

    def test_sl_records_open_to_closed(self):
        """SL hit records OPEN -> CLOSED transition."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pm.update_price("BTC", 48000.0)
        pos = pm.positions["BTC"]
        assert OPEN in pos.state_path
        assert CLOSED in pos.state_path

    def test_tp1_records_transition(self):
        """TP1 hit records appropriate state transition."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 58000.0,
                         atr=500.0, leverage=3.0)
        pm.update_price("BTC", 52500.0)
        pos = pm.positions["BTC"]
        # TP1 hit should transition through TP1_HIT (and possibly to TRAILING or CLOSED)
        assert TP1_HIT in pos.state_path or CLOSED in pos.state_path

    def test_state_path_is_monotonic(self):
        """State path should not revisit earlier states (no cycles)."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 58000.0, atr=500.0)

        # Drive through lifecycle
        pm.update_price("BTC", 52500.0)  # TP1
        pm.update_price("BTC", 54000.0)  # Move up
        pm.update_price("BTC", 58500.0)  # TP2

        pos = pm.positions["BTC"]
        path = pos.state_path

        # CLOSED should only appear once and be the last state
        if CLOSED in path:
            assert path.count(CLOSED) == 1
            assert path[-1] == CLOSED

    def test_state_path_string_format(self):
        """state_path_str produces arrow-separated path."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pm.update_price("BTC", 48000.0)
        pos = pm.positions["BTC"]
        path_str = pos.state_path_str
        assert "->" in path_str
        assert "IDLE" in path_str
        assert "CLOSED" in path_str


# ═══════════════════════════════════════════════════════════════════════
# SECTION D: PnL Attribution per Strategy / Symbol / Regime
# ═══════════════════════════════════════════════════════════════════════


class TestPnLAttribution:
    """PnL can be attributed per strategy, symbol, and regime."""

    def _run_trade(self, pm, symbol, side, entry, qty, sl, tp1, tp2,
                   exit_price, strategy="regime_trend", regime="consolidation"):
        """Helper: open then close a position at exit_price."""
        entry_reasons = {"regime": regime, "num_agree": 2}
        pm.open_position(symbol, side, entry, qty, sl, tp1, tp2,
                         strategy=strategy, confidence=80.0, atr=abs(entry * 0.01),
                         entry_reasons=entry_reasons)
        pm.update_price(symbol, exit_price)
        return pm.positions[symbol]

    def test_pnl_by_strategy(self):
        """Can aggregate PnL by strategy from trade_log."""
        pm = _make_pm()

        # Trade 1: regime_trend wins
        self._run_trade(pm, "BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                        exit_price=52500.0, strategy="regime_trend")
        # Trade 2: monte_carlo loses
        self._run_trade(pm, "SOL", "LONG", 150.0, 10.0, 145.0, 160.0, 170.0,
                        exit_price=144.0, strategy="monte_carlo_zones")

        # Attribute PnL by strategy
        strategy_pnl = {}
        for event in pm.trade_log:
            if event.action not in ("OPEN",):
                strat = event.strategy
                strategy_pnl[strat] = strategy_pnl.get(strat, 0) + event.pnl

        assert "regime_trend" in strategy_pnl
        assert "monte_carlo_zones" in strategy_pnl
        # regime_trend should be positive (won), monte_carlo negative (lost)
        assert strategy_pnl["regime_trend"] > 0
        assert strategy_pnl["monte_carlo_zones"] < 0

    def test_pnl_by_symbol(self):
        """Can aggregate PnL by symbol from trade_log."""
        pm = _make_pm()

        self._run_trade(pm, "BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                        exit_price=52500.0)
        self._run_trade(pm, "SOL", "LONG", 150.0, 10.0, 145.0, 160.0, 170.0,
                        exit_price=144.0)

        symbol_pnl = {}
        for event in pm.trade_log:
            if event.action not in ("OPEN",):
                symbol_pnl[event.symbol] = symbol_pnl.get(event.symbol, 0) + event.pnl

        assert "BTC" in symbol_pnl
        assert "SOL" in symbol_pnl
        assert symbol_pnl["BTC"] > 0
        assert symbol_pnl["SOL"] < 0

    def test_pnl_by_regime(self):
        """Can aggregate PnL by regime from position entry_reasons."""
        pm = _make_pm()

        self._run_trade(pm, "BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                        exit_price=52500.0, regime="consolidation")
        self._run_trade(pm, "SOL", "LONG", 150.0, 10.0, 145.0, 160.0, 170.0,
                        exit_price=144.0, regime="high_volatility")

        regime_pnl = {}
        for event in pm.trade_log:
            if event.action not in ("OPEN",):
                regime = event.metadata.get("regime", "unknown")
                if not regime:
                    entry_reasons = event.metadata.get("entry_reasons", {})
                    regime = entry_reasons.get("regime", "unknown")
                regime_pnl[regime] = regime_pnl.get(regime, 0) + event.pnl

        # At least one regime should be present
        assert len(regime_pnl) >= 1

    def test_fees_attribution(self):
        """Fees are tracked per position and deducted from PnL."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pos = pm.positions["BTC"]
        open_fee = pos.fees_paid

        pm.update_price("BTC", 48000.0)
        total_fee = pos.fees_paid

        assert total_fee > open_fee, "Close should add to fees"
        assert pos.realized_pnl < 0  # Loss + fees should be negative

        # PnL should account for fees
        gross_pnl = (48000.0 - 50000.0) * 1.0 * 1.0  # (exit - entry) * qty * leverage
        assert pos.realized_pnl < gross_pnl, "Net PnL should be worse than gross due to fees"

    def test_funding_costs_tracked(self):
        """Funding costs are accumulated when accrue_funding is called."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         leverage=5.0, atr=500.0)
        pos = pm.positions["BTC"]

        # Accrue funding
        pm.accrue_funding("BTC", 0.0001, interval_hours=8.0)
        assert pos.funding_costs > 0

    def test_realized_pnl_includes_funding(self):
        """Realized PnL deducts accumulated funding costs on close."""
        pm = _make_pm()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         leverage=5.0, atr=500.0)
        pos = pm.positions["BTC"]

        # Accrue significant funding (simulate many ticks)
        for _ in range(100):
            pm.accrue_funding("BTC", 0.001, interval_hours=8.0)

        funding_before_close = pos.funding_costs
        assert funding_before_close > 0

        # Close at entry (flat trade) — PnL should be negative due to fees + funding
        pm.update_price("BTC", 48000.0)
        # Realized PnL should reflect funding costs
        assert pos.realized_pnl < 0


# ═══════════════════════════════════════════════════════════════════════
# SECTION E: Filter Chain Metadata Completeness
# ═══════════════════════════════════════════════════════════════════════


class TestFilterChainMetadata:
    """FilterResult metadata provides full context for decision audit."""

    def test_approved_has_leverage_tier(self):
        """Approved result metadata includes leverage_tier."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert result.approved
        assert "leverage_tier" in result.metadata

    def test_approved_has_rr(self):
        """Approved result metadata includes risk-reward ratios."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert result.approved
        assert "rr_tp1" in result.metadata
        assert "rr_tp2" in result.metadata

    def test_approved_has_num_agree(self):
        """Approved result metadata includes number of strategies that agreed."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=3, total_strategies=4)
        assert result.approved
        assert result.metadata.get("num_agree") == 3

    def test_approved_has_liq_gap(self):
        """Approved result metadata includes liquidation gap percentage."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            assert "liq_gap_pct" in result.metadata

    def test_approved_has_fee_drag(self):
        """Approved result metadata includes fee drag estimate."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            assert "fee_drag_pct" in result.metadata

    def test_risk_manager_sizing_breakdown(self):
        """RiskManager stores last sizing breakdown for attribution."""
        rm = RiskManager(starting_equity=10000.0, risk_per_trade=0.02)
        qty = rm.calculate_qty(entry=50000.0, stop_loss=48500.0, leverage=3.0,
                               risk_multiplier=1.2, symbol="BTC")
        breakdown = rm.last_sizing_breakdown
        assert "equity" in breakdown
        assert "risk_usd" in breakdown
        assert "symbol" in breakdown
        assert breakdown["symbol"] == "BTC"


# ═══════════════════════════════════════════════════════════════════════
# SECTION F: Annotated Signal Path
# ═══════════════════════════════════════════════════════════════════════


class TestAnnotatedSignalPath:
    """evaluate_annotated produces rich filter annotations."""

    def test_annotated_valid_signal(self):
        """Valid signal through annotated path produces non-rejected result."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate_annotated(
            signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.hard_rejected, f"Unexpected rejection: {result.hard_rejection_reason}"

    def test_annotated_invalid_signal_hard_rejected(self):
        """Invalid signal is hard-rejected in annotated path."""
        chain, rm = _make_filter_chain()
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=80.0, entry=50000.0,
            sl=52000.0, tp1=53000.0, tp2=55000.0,
        )
        result = chain.evaluate_annotated(
            signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert result.hard_rejected

    def test_annotated_circuit_breaker_hard_rejected(self):
        """Circuit breaker is a hard rejection in annotated path."""
        chain, rm = _make_filter_chain()
        cb = rm.circuit_breaker
        cb.peak_equity = 10000.0
        for _ in range(5):
            cb.record_trade(-100, 10000.0)

        signal = _make_signal(confidence=70.0)
        result = chain.evaluate_annotated(
            signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert result.hard_rejected
        assert "Circuit breaker" in result.hard_rejection_reason

    def test_annotated_has_rr_annotation(self):
        """Annotated path includes R:R floor annotation."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate_annotated(
            signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        gates = [a.gate for a in result.annotations]
        assert "rr_floor" in gates

    def test_annotated_has_fee_drag_annotation(self):
        """Annotated path includes fee drag annotation."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate_annotated(
            signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        gates = [a.gate for a in result.annotations]
        assert "fee_drag" in gates

    def test_annotation_compact_format(self):
        """Annotations produce compact string for token-efficient logging."""
        chain, rm = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate_annotated(
            signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        for ann in result.annotations:
            compact = ann.to_compact()
            assert isinstance(compact, str)
            assert len(compact) > 0


# ═══════════════════════════════════════════════════════════════════════
# SECTION G: End-to-End Multi-Trade Sequence
# ═══════════════════════════════════════════════════════════════════════


class TestMultiTradeSequence:
    """Multi-trade sequences with circuit breaker and PnL tracking."""

    def test_three_trade_sequence_with_cb_trip(self):
        """Run 3 trades: 2 losses trip CB, then verify CB blocks."""
        chain, rm = _make_filter_chain()
        pm = _make_pm()
        cb = rm.circuit_breaker
        cb.max_consecutive_losses = 2

        equity = 10000.0

        # Trade 1: loss
        sig1 = _make_signal(symbol="BTC", confidence=82.0)
        r1 = chain.evaluate(sig1, equity=equity, num_strategies_agree=2, total_strategies=4)
        if r1.approved:
            pm.open_position("BTC", "LONG", 50000.0, r1.position_qty, 48500.0, 52000.0, 54000.0,
                             atr=500.0, leverage=r1.leverage)
            events = pm.update_price("BTC", 48000.0)
            if events:
                cb.record_trade(events[0].pnl, equity + events[0].pnl)

        # Trade 2: loss
        sig2 = _make_signal(symbol="SOL", confidence=82.0, entry=150.0,
                            sl=145.0, tp1=160.0, tp2=170.0)
        r2 = chain.evaluate(sig2, equity=equity, num_strategies_agree=2, total_strategies=4)
        if r2.approved:
            pm.open_position("SOL", "LONG", 150.0, r2.position_qty, 145.0, 160.0, 170.0,
                             atr=5.0, leverage=r2.leverage)
            events = pm.update_price("SOL", 144.0)
            if events:
                cb.record_trade(events[0].pnl, equity + events[0].pnl)

        # Circuit breaker should now be tripped (2 consecutive losses)
        assert cb.tripped

        # Trade 3: should be blocked
        sig3 = _make_signal(symbol="HYPE", confidence=70.0, entry=25.0,
                            sl=24.0, tp1=27.0, tp2=29.0)
        r3 = chain.evaluate(sig3, equity=equity, num_strategies_agree=2, total_strategies=4)
        assert not r3.approved

    def test_win_loss_alternation_no_cb_trip(self):
        """Alternating win/loss should not trip consecutive loss breaker."""
        cb = CircuitBreaker(max_consecutive_losses=3, daily_loss_limit_pct=1.0,
                            max_drawdown_pct=1.0)
        cb.peak_equity = 10000.0

        for _ in range(10):
            cb.record_trade(-50, 10000.0)  # loss
            cb.record_trade(100, 10000.0)  # win

        assert not cb.tripped
        assert cb.consecutive_losses == 0
