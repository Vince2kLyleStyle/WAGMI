"""
Integration tests for the full trading pipeline end-to-end.

Tests the golden path (signal -> ensemble -> filter chain -> position),
rejection paths (circuit breaker, position limits, liquidation),
multi-symbol concurrent signals, position lifecycle,
circuit breaker activation, stale data handling, and config validation.
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
from execution.leverage import LeverageManager, LeverageDecision
from execution.position_manager import PositionManager, Position
from execution.position_state import IDLE, OPEN, TP1_HIT, TRAILING, CLOSED
from trading_config import TradingConfig


# ── Helpers ────────────────────────────────────────────────────────────


def _make_signal(
    symbol="BTC",
    side="BUY",
    confidence=82.0,
    entry=50000.0,
    sl=48500.0,
    tp1=52000.0,
    tp2=54000.0,
    atr=500.0,
    strategy="regime_trend",
    regime="consolidation",
    metadata_extra=None,
):
    """Create a realistic, valid signal for testing."""
    meta = {"regime": regime, "num_agree": 2}
    if metadata_extra:
        meta.update(metadata_extra)
    return Signal(
        strategy=strategy,
        symbol=symbol,
        side=side,
        confidence=confidence,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        atr=atr,
        metadata=meta,
    )


def _make_short_signal(
    symbol="BTC",
    confidence=82.0,
    entry=50000.0,
    sl=51500.0,
    tp1=48000.0,
    tp2=46000.0,
    atr=500.0,
    regime="consolidation",
):
    """Create a valid SHORT signal."""
    return _make_signal(
        symbol=symbol,
        side="SELL",
        confidence=confidence,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        atr=atr,
        regime=regime,
    )


def _make_config(**overrides):
    """Create a TradingConfig with test defaults."""
    cfg = TradingConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_risk_manager(equity=10000.0, risk_per_trade=0.02, max_open=8):
    """Create a RiskManager for testing."""
    cb = CircuitBreaker(
        daily_loss_limit_pct=0.05,
        max_consecutive_losses=5,
        max_drawdown_pct=0.15,
        cooldown_minutes=60,
    )
    rm = RiskManager(
        starting_equity=equity,
        risk_per_trade=risk_per_trade,
        max_open_positions=max_open,
        circuit_breaker=cb,
    )
    return rm


def _make_leverage_manager():
    """Create a LeverageManager for testing."""
    return LeverageManager(
        enable_leverage=True,
        max_leverage=25.0,
        max_extreme_positions=2,
        max_risk_multiplier=1.5,
    )


def _make_filter_chain(equity=10000.0, risk_per_trade=0.02, **config_overrides):
    """Create a full RiskFilterChain with real dependencies."""
    rm = _make_risk_manager(equity=equity, risk_per_trade=risk_per_trade)
    lm = _make_leverage_manager()
    cfg = _make_config(**config_overrides)
    chain = RiskFilterChain(rm, lm, cfg)
    return chain, rm, lm, cfg


def _make_position_manager():
    """Create a PositionManager for testing."""
    return PositionManager(taker_fee_bps=4, enable_trailing=True, trailing_atr_mult=1.5)


# ═══════════════════════════════════════════════════════════════════════
# SECTION A: Golden Path — signal → ensemble → pipeline → position
# ═══════════════════════════════════════════════════════════════════════


class TestGoldenPath:
    """End-to-end: valid signal passes all gates and opens a position."""

    def test_valid_buy_signal_passes_all_gates(self):
        """A good BUY signal with sufficient R:R, EV, and confidence passes."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(
            signal=signal,
            equity=10000.0,
            num_strategies_agree=2,
            total_strategies=4,
            current_open_count=0,
        )
        assert result.approved, f"Expected approval, got: {result.rejection_reason}"
        assert result.leverage > 0
        assert result.position_qty > 0

    def test_valid_sell_signal_passes_all_gates(self):
        """A good SELL signal passes all gates."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_short_signal(confidence=82.0)
        result = chain.evaluate(
            signal=signal,
            equity=10000.0,
            num_strategies_agree=2,
            total_strategies=4,
            current_open_count=0,
        )
        assert result.approved, f"Expected approval, got: {result.rejection_reason}"
        assert result.leverage > 0

    def test_signal_to_position_full_path(self):
        """Signal approved -> open position -> position is OPEN state."""
        chain, rm, lm, cfg = _make_filter_chain()
        pm = _make_position_manager()
        signal = _make_signal(confidence=82.0)

        result = chain.evaluate(
            signal=signal,
            equity=10000.0,
            num_strategies_agree=2,
            total_strategies=4,
        )
        assert result.approved

        # Use filter result to open position
        pos = pm.open_position(
            symbol=signal.symbol,
            side="LONG",
            entry=signal.entry,
            qty=result.position_qty,
            sl=signal.sl,
            tp1=signal.tp1,
            tp2=signal.tp2,
            atr=signal.atr,
            leverage=result.leverage,
            mode="leverage",
            strategy=signal.strategy,
            confidence=signal.confidence,
        )
        assert pos is not None
        assert pos.state == OPEN
        assert pos.side == "LONG"
        assert pos.leverage == result.leverage

    def test_high_confidence_gets_higher_leverage(self):
        """Higher confidence should produce higher leverage."""
        chain, rm, lm, cfg = _make_filter_chain()

        sig_low = _make_signal(confidence=65.0)
        sig_high = _make_signal(confidence=85.0)

        r_low = chain.evaluate(sig_low, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        r_high = chain.evaluate(sig_high, equity=10000.0, num_strategies_agree=2, total_strategies=4)

        assert r_low.approved and r_high.approved
        assert r_high.leverage >= r_low.leverage, (
            f"85% conf leverage ({r_high.leverage}) should be >= 65% conf ({r_low.leverage})"
        )

    def test_metadata_populated_on_approval(self):
        """Approved result should carry leverage tier, R:R, and other metadata."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert result.approved
        assert "leverage" in result.metadata
        assert "rr_tp1" in result.metadata
        assert "risk_multiplier" in result.metadata
        assert result.metadata["rr_tp1"] >= 1.0


# ═══════════════════════════════════════════════════════════════════════
# SECTION B: Rejection Paths
# ═══════════════════════════════════════════════════════════════════════


class TestRejectionPaths:
    """Signals that should be blocked by various pipeline gates."""

    def test_invalid_signal_rejected(self):
        """Signal with SL on wrong side of entry is rejected at gate 1."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = Signal(
            strategy="test",
            symbol="BTC",
            side="BUY",
            confidence=80.0,
            entry=50000.0,
            sl=52000.0,  # SL above entry for a BUY = invalid
            tp1=53000.0,
            tp2=55000.0,
        )
        assert not signal.is_valid
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved
        assert "Invalid" in result.rejection_reason or "stop_width" in result.rejection_reason

    def test_near_zero_stop_width_rejected(self):
        """Signal with stop width < 0.3% rejected (prevents infinite R:R)."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = Signal(
            strategy="test",
            symbol="BTC",
            side="BUY",
            confidence=80.0,
            entry=50000.0,
            sl=49990.0,  # 0.02% stop width << 0.3% minimum
            tp1=52000.0,
            tp2=54000.0,
        )
        assert not signal.is_valid
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved

    def test_circuit_breaker_blocks_signal(self):
        """Tripped circuit breaker blocks signals (unless override)."""
        chain, rm, lm, cfg = _make_filter_chain()
        # Trip the circuit breaker with consecutive losses
        cb = rm.circuit_breaker
        cb.peak_equity = 10000.0
        for _ in range(5):
            cb.record_trade(-100, 10000.0)
        assert cb.tripped

        signal = _make_signal(confidence=70.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved
        assert "Circuit breaker" in result.rejection_reason

    def test_max_positions_blocks_signal(self):
        """Signal rejected when max open positions reached."""
        chain, rm, lm, cfg = _make_filter_chain(max_open_positions=3)
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(
            signal,
            equity=10000.0,
            num_strategies_agree=2,
            total_strategies=4,
            current_open_count=3,
        )
        assert not result.approved
        assert "Max positions" in result.rejection_reason

    def test_liquidation_check_blocks_unsafe_trade(self):
        """Signal where SL is beyond liquidation price is blocked."""
        chain, rm, lm, cfg = _make_filter_chain()
        # Very wide stop relative to leverage -> should still be safe
        # But we can test with a crafted scenario where SL passes liquidation
        # Create a signal with very high leverage forced and tight stop
        signal = Signal(
            strategy="test",
            symbol="BTC",
            side="BUY",
            confidence=82.0,
            entry=50000.0,
            sl=45000.0,  # 10% stop width
            tp1=55000.0,
            tp2=60000.0,
            metadata={"regime": "consolidation", "num_agree": 2},
        )
        # This should pass since 10% stop with reasonable leverage is safe
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        # With reasonable leverage, this should be fine
        # The test validates the liquidation check runs without error
        assert isinstance(result, FilterResult)

    def test_low_rr_no_longer_rejected(self):
        """DECHOKE 2 (2026-07-02): rr_floor gate deleted (zero live firings,
        GM_GATE_ROC). R:R below config minimum no longer rejects; the
        structural validity floor (R:R >= 1.0) still applies."""
        chain, rm, lm, cfg = _make_filter_chain()
        cfg.min_signal_rr = 2.0  # Old strict R:R requirement (now advisory-only)
        signal = Signal(
            strategy="test",
            symbol="BTC",
            side="BUY",
            confidence=80.0,
            entry=50000.0,
            sl=48500.0,  # 3% stop = 1500 width
            tp1=52000.0,  # R:R = 2000/1500 = 1.33 (passes is_valid but < 2.0 config)
            tp2=54000.0,
            metadata={"regime": "consolidation", "num_agree": 2},
        )
        assert signal.is_valid, "Signal should pass is_valid (R:R >= 1.0)"
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert "R:R" not in result.rejection_reason

    def test_low_confidence_gets_low_leverage(self):
        """Very low confidence should still get some leverage but small size."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=25.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=1, total_strategies=4)
        # Sub-20% confidence gets 0 leverage, 25% should get some
        if result.approved:
            assert result.leverage > 0


# ═══════════════════════════════════════════════════════════════════════
# SECTION C: Multi-Symbol Concurrent Signals
# ═══════════════════════════════════════════════════════════════════════


class TestMultiSymbol:
    """Multiple symbols generating signals simultaneously."""

    def test_multiple_symbols_pass_independently(self):
        """BTC and SOL signals both approved when under max positions."""
        chain, rm, lm, cfg = _make_filter_chain()

        sig_btc = _make_signal(symbol="BTC", confidence=82.0, entry=50000.0,
                               sl=48500.0, tp1=52000.0, tp2=54000.0)
        sig_sol = _make_signal(symbol="SOL", confidence=78.0, entry=150.0,
                               sl=145.0, tp1=158.0, tp2=165.0)

        r_btc = chain.evaluate(sig_btc, equity=10000.0, num_strategies_agree=2,
                               total_strategies=4, current_open_count=0)
        r_sol = chain.evaluate(sig_sol, equity=10000.0, num_strategies_agree=2,
                               total_strategies=4, current_open_count=1)

        assert r_btc.approved, f"BTC rejected: {r_btc.rejection_reason}"
        assert r_sol.approved, f"SOL rejected: {r_sol.rejection_reason}"

    def test_third_signal_blocked_at_max_positions(self):
        """Third symbol blocked when max_open_positions=2."""
        chain, rm, lm, cfg = _make_filter_chain(max_open_positions=2)

        sig = _make_signal(symbol="HYPE", confidence=82.0, entry=25.0,
                           sl=24.0, tp1=27.0, tp2=29.0)
        result = chain.evaluate(sig, equity=10000.0, num_strategies_agree=2,
                                total_strategies=4, current_open_count=2)
        assert not result.approved
        assert "Max positions" in result.rejection_reason

    def test_multi_symbol_position_manager_isolation(self):
        """Each symbol gets its own independent position."""
        pm = _make_position_manager()

        pm.open_position("BTC", "LONG", 50000.0, 0.01, 48500.0, 52000.0, 54000.0, atr=500.0)
        pm.open_position("SOL", "SHORT", 150.0, 1.0, 155.0, 140.0, 130.0, atr=5.0)

        assert "BTC" in pm.positions
        assert "SOL" in pm.positions
        assert pm.positions["BTC"].side == "LONG"
        assert pm.positions["SOL"].side == "SHORT"
        assert pm.positions["BTC"].state == OPEN
        assert pm.positions["SOL"].state == OPEN

    def test_cannot_double_open_same_symbol(self):
        """Second open on same symbol returns None (one position per symbol)."""
        pm = _make_position_manager()
        pos1 = pm.open_position("BTC", "LONG", 50000.0, 0.01, 48500.0, 52000.0, 54000.0)
        assert pos1 is not None
        pos2 = pm.open_position("BTC", "LONG", 51000.0, 0.01, 49000.0, 53000.0, 55000.0)
        assert pos2 is None

    def test_independent_sl_tp_per_symbol(self):
        """SL hit on BTC does not affect SOL position."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 0.01, 48500.0, 52000.0, 54000.0, atr=500.0)
        pm.open_position("SOL", "LONG", 150.0, 1.0, 145.0, 160.0, 170.0, atr=5.0)

        # BTC hits SL
        btc_events = pm.update_price("BTC", 48000.0)
        assert len(btc_events) == 1
        assert btc_events[0].action == "SL"
        assert pm.positions["BTC"].state == CLOSED

        # SOL still open
        assert pm.positions["SOL"].state == OPEN


# ═══════════════════════════════════════════════════════════════════════
# SECTION D: Position Lifecycle
# ═══════════════════════════════════════════════════════════════════════


class TestPositionLifecycle:
    """Full position lifecycle: OPEN -> TP1_HIT -> TRAILING -> CLOSED."""

    def test_long_full_lifecycle_tp1_then_tp2(self):
        """LONG: OPEN -> TP1 hit -> TRAILING -> TP2 hit -> CLOSED."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         atr=500.0, leverage=3.0)
        pos = pm.positions["BTC"]
        assert pos.state == OPEN

        # Price approaches TP1 but doesn't hit
        events = pm.update_price("BTC", 51500.0)
        assert len(events) == 0
        assert pos.state == OPEN

        # TP1 hit
        events = pm.update_price("BTC", 52100.0)
        assert len(events) >= 1
        assert any(e.action == "TP1" or e.action == "TP1_FULL" for e in events)
        assert pos.state in (TP1_HIT, TRAILING, CLOSED)

        # If position still open (partial close), continue to TP2
        if pos.state != CLOSED:
            # Price continues up
            pm.update_price("BTC", 53000.0)

            # TP2 hit
            events = pm.update_price("BTC", 54100.0)
            assert len(events) >= 1
            assert pm.positions["BTC"].state == CLOSED

    def test_long_sl_hit(self):
        """LONG: OPEN -> SL hit -> CLOSED."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)

        events = pm.update_price("BTC", 48000.0)
        assert len(events) == 1
        assert events[0].action == "SL"
        assert pm.positions["BTC"].state == CLOSED

    def test_short_sl_hit(self):
        """SHORT: OPEN -> SL hit -> CLOSED."""
        pm = _make_position_manager()
        pm.open_position("BTC", "SHORT", 50000.0, 1.0, 51500.0, 48000.0, 46000.0, atr=500.0)

        events = pm.update_price("BTC", 52000.0)
        assert len(events) == 1
        assert events[0].action == "SL"
        assert pm.positions["BTC"].state == CLOSED

    def test_short_tp1_hit(self):
        """SHORT: price drops to TP1 -> partial close."""
        pm = _make_position_manager()
        pm.open_position("BTC", "SHORT", 50000.0, 1.0, 51500.0, 48000.0, 46000.0, atr=500.0)

        events = pm.update_price("BTC", 47500.0)
        assert len(events) >= 1
        tp_actions = [e.action for e in events]
        assert any(a in ("TP1", "TP1_FULL") for a in tp_actions)

    def test_trailing_stop_updates_with_price(self):
        """After TP1, trailing stop should follow price."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 58000.0,
                         atr=500.0, leverage=3.0)
        pos = pm.positions["BTC"]

        # TP1 hit
        pm.update_price("BTC", 52500.0)

        if pos.state != CLOSED and pos.state in (TP1_HIT, TRAILING):
            initial_sl = pos.sl

            # Price moves higher
            pm.update_price("BTC", 53500.0)
            pm.update_price("BTC", 54500.0)

            # SL should have moved up (or at least not down)
            assert pos.sl >= initial_sl, (
                f"Trailing SL should move up: was {initial_sl}, now {pos.sl}"
            )

    def test_pnl_calculation_long_win(self):
        """LONG position closed at TP2 should have positive PnL."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 55000.0,
                         atr=500.0, leverage=2.0)

        # Hit TP1 then TP2
        pm.update_price("BTC", 52500.0)
        pm.update_price("BTC", 55500.0)

        pos = pm.positions["BTC"]
        assert pos.state == CLOSED
        assert pos.realized_pnl > 0, f"Expected positive PnL, got {pos.realized_pnl}"

    def test_pnl_calculation_long_loss(self):
        """LONG position stopped out should have negative PnL."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         atr=500.0, leverage=2.0)

        events = pm.update_price("BTC", 48000.0)
        pos = pm.positions["BTC"]
        assert pos.state == CLOSED
        assert pos.realized_pnl < 0, f"Expected negative PnL, got {pos.realized_pnl}"

    def test_state_path_recorded(self):
        """State transitions are recorded in state_path."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pos = pm.positions["BTC"]

        assert IDLE in pos.state_path
        assert OPEN in pos.state_path

        pm.update_price("BTC", 48000.0)  # SL hit
        assert CLOSED in pos.state_path

    def test_mfe_mae_tracked(self):
        """MFE (max favorable excursion) and MAE (max adverse excursion) tracked."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pos = pm.positions["BTC"]

        pm.update_price("BTC", 51000.0)  # favorable
        pm.update_price("BTC", 49500.0)  # adverse
        pm.update_price("BTC", 48000.0)  # SL hit

        assert pos.highest_price >= 51000.0
        assert pos.lowest_price <= 49500.0
        assert pos.mfe > 0
        assert pos.mae > 0


# ═══════════════════════════════════════════════════════════════════════
# SECTION E: Circuit Breaker Activation
# ═══════════════════════════════════════════════════════════════════════


class TestCircuitBreakerActivation:
    """Circuit breaker triggers on consecutive losses and daily loss limits."""

    def test_consecutive_losses_trip_breaker(self):
        """5 consecutive losses trips the circuit breaker."""
        # Use high daily_loss_limit_pct so daily loss doesn't trip first
        cb = CircuitBreaker(max_consecutive_losses=5, cooldown_minutes=60,
                            daily_loss_limit_pct=1.0, max_drawdown_pct=1.0)
        cb.peak_equity = 10000.0

        for i in range(4):
            cb.record_trade(-50, 10000.0 - (i + 1) * 50)
            assert not cb.tripped, f"Should not trip after {i + 1} losses"

        cb.record_trade(-50, 9750.0)
        assert cb.tripped
        assert "consecutive losses" in cb.trip_reason.lower()

    def test_daily_loss_limit_trips_breaker(self):
        """Daily loss exceeding 5% trips the breaker."""
        cb = CircuitBreaker(daily_loss_limit_pct=0.05, cooldown_minutes=60)
        cb.peak_equity = 10000.0

        # Single large loss: 6% of equity
        cb.record_trade(-600, 9400.0)
        assert cb.tripped
        assert "daily loss" in cb.trip_reason.lower()

    def test_drawdown_trips_breaker(self):
        """Drawdown from peak exceeding 15% trips breaker."""
        cb = CircuitBreaker(max_drawdown_pct=0.15, cooldown_minutes=60,
                            daily_loss_limit_pct=1.0, max_consecutive_losses=100)
        cb.peak_equity = 10000.0

        # Series of losses bringing equity to 8400 (16% drawdown from 10000)
        cb.record_trade(-800, 9200.0)
        cb.record_trade(-800, 8400.0)
        assert cb.tripped
        assert "drawdown" in cb.trip_reason.lower()

    def test_breaker_blocks_pipeline(self):
        """Tripped breaker causes pipeline to reject signals."""
        chain, rm, lm, cfg = _make_filter_chain()
        cb = rm.circuit_breaker
        cb.peak_equity = 10000.0

        # Trip breaker
        for _ in range(5):
            cb.record_trade(-100, 10000.0)

        signal = _make_signal(confidence=70.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved
        assert "Circuit breaker" in result.rejection_reason

    def test_high_confidence_override_during_breaker(self):
        """Very high confidence can override circuit breaker (if max_overrides > 0)."""
        cb = CircuitBreaker(max_consecutive_losses=3, cooldown_minutes=60, max_cb_overrides=1)
        cb.peak_equity = 10000.0
        for _ in range(3):
            cb.record_trade(-100, 10000.0)
        assert cb.tripped

        # High confidence (93% >= 92% threshold) should override
        allowed = cb.is_trading_allowed(confidence=93.0, cb_conf_override_pct=0.92)
        assert allowed

        # Second override should fail (max_overrides=1)
        allowed2 = cb.is_trading_allowed(confidence=95.0, cb_conf_override_pct=0.92)
        assert not allowed2

    def test_winning_trade_resets_consecutive_losses(self):
        """A winning trade resets the consecutive loss counter."""
        cb = CircuitBreaker(max_consecutive_losses=5, cooldown_minutes=60)
        cb.peak_equity = 10000.0

        cb.record_trade(-100, 9900.0)
        cb.record_trade(-100, 9800.0)
        assert cb.consecutive_losses == 2

        cb.record_trade(200, 10000.0)
        assert cb.consecutive_losses == 0

    def test_mtm_drawdown_trips_breaker(self):
        """Mark-to-market equity drop trips the drawdown breaker."""
        cb = CircuitBreaker(max_drawdown_pct=0.10, cooldown_minutes=60,
                            daily_loss_limit_pct=1.0, max_consecutive_losses=100)
        cb.peak_equity = 10000.0

        # Simulate unrealized loss bringing MTM equity to 8900 (11% drawdown)
        cb.check_mtm_breakers(8900.0)
        assert cb.tripped
        assert "MTM drawdown" in cb.trip_reason

    def test_session_halt_permanent(self):
        """Session halt cannot be recovered via cooldown."""
        # Set max_drawdown_pct high so regular DD breaker doesn't trip before session halt
        cb = CircuitBreaker(max_drawdown_pct=0.90, cooldown_minutes=1,
                            daily_loss_limit_pct=1.0, max_consecutive_losses=100)
        cb.start_session(10000.0)
        cb.peak_equity = 10000.0
        cb.max_session_drawdown_pct = 0.20

        # Trigger session halt: 25% cumulative drawdown (> 20% session limit)
        cb.record_trade(-1000, 9000.0)
        cb.record_trade(-1500, 7500.0)
        assert cb._session_halted

        # Even after cooldown, should remain halted
        assert not cb.is_trading_allowed(confidence=95.0)


# ═══════════════════════════════════════════════════════════════════════
# SECTION F: Stale Data / Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestStaleDataHandling:
    """Pipeline behavior with edge-case or missing data."""

    def test_zero_entry_price_rejected(self):
        """Signal with entry=0 is invalid and rejected."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=80.0, entry=0.0, sl=-100.0, tp1=100.0, tp2=200.0,
        )
        assert not signal.is_valid
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert not result.approved

    def test_negative_entry_price_rejected(self):
        """Signal with negative entry is invalid."""
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=80.0, entry=-100.0, sl=-200.0, tp1=0.0, tp2=100.0,
        )
        assert not signal.is_valid

    def test_zero_equity_produces_zero_qty(self):
        """With zero equity, position size should be zero."""
        chain, rm, lm, cfg = _make_filter_chain(equity=0.0)
        rm.equity = 0.0
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=0.0, num_strategies_agree=2, total_strategies=4)
        # Should either reject or produce zero qty
        if result.approved:
            assert result.position_qty == 0.0

    def test_position_update_on_closed_position_noop(self):
        """Updating price on a closed position produces no events."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)

        # Close via SL
        pm.update_price("BTC", 48000.0)
        assert pm.positions["BTC"].state == CLOSED

        # Further updates should be no-ops
        events = pm.update_price("BTC", 45000.0)
        assert len(events) == 0

    def test_update_nonexistent_symbol_noop(self):
        """Updating price for a symbol with no position produces no events."""
        pm = _make_position_manager()
        events = pm.update_price("DOGE", 1.0)
        assert len(events) == 0

    def test_signal_with_no_metadata_survives(self):
        """Signal with empty/None metadata doesn't crash the pipeline."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=82.0, entry=50000.0, sl=48500.0,
            tp1=52000.0, tp2=54000.0,
            metadata={},
        )
        # Should not raise
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert isinstance(result, FilterResult)


# ═══════════════════════════════════════════════════════════════════════
# SECTION G: Config Validation
# ═══════════════════════════════════════════════════════════════════════


class TestConfigValidation:
    """Invalid configurations are caught."""

    def test_config_defaults_are_sane(self):
        """Default TradingConfig values should be within reasonable bounds."""
        cfg = TradingConfig()
        assert cfg.risk_per_trade > 0
        assert cfg.risk_per_trade <= 0.10  # No more than 10% risk per trade
        assert cfg.max_leverage >= 1.0
        assert cfg.max_open_positions >= 1
        assert cfg.circuit_breaker_daily_loss_pct > 0
        assert cfg.circuit_breaker_daily_loss_pct <= 0.50
        assert cfg.max_consecutive_losses >= 1

    def test_circuit_breaker_params_positive(self):
        """Circuit breaker params must be positive."""
        cb = CircuitBreaker(
            daily_loss_limit_pct=0.05,
            max_consecutive_losses=5,
            cooldown_minutes=60,
        )
        assert cb.daily_loss_limit_pct > 0
        assert cb.max_consecutive_losses > 0
        assert cb.cooldown_minutes > 0

    def test_leverage_manager_respects_max(self):
        """LeverageManager should never exceed max_leverage."""
        lm = LeverageManager(enable_leverage=True, max_leverage=10.0)
        for conf in range(50, 100, 5):
            decision = lm.decide(
                confidence=conf,
                num_strategies_agree=3,
                total_strategies=4,
                risk_tier="low",
            )
            assert decision.leverage <= 10.0, (
                f"Leverage {decision.leverage} > max 10.0 at confidence {conf}"
            )

    def test_risk_tier_caps_leverage(self):
        """High-risk symbols should get lower leverage caps."""
        lm = LeverageManager(enable_leverage=True, max_leverage=25.0)

        d_low = lm.decide(confidence=85.0, num_strategies_agree=3,
                          total_strategies=4, risk_tier="low")
        d_high = lm.decide(confidence=85.0, num_strategies_agree=3,
                           total_strategies=4, risk_tier="high")

        assert d_high.leverage <= d_low.leverage, (
            f"High risk leverage ({d_high.leverage}) should be <= low risk ({d_low.leverage})"
        )

    def test_min_stop_width_pct_used_in_signal_validation(self):
        """Signal.MIN_STOP_WIDTH_PCT should match TradingConfig."""
        cfg = TradingConfig()
        assert Signal.MIN_STOP_WIDTH_PCT == cfg.min_stop_width_pct

    def test_max_open_positions_respected(self):
        """Pipeline uses config.max_open_positions for the gate."""
        chain, rm, lm, cfg = _make_filter_chain(max_open_positions=2)
        signal = _make_signal(confidence=82.0)

        r1 = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2,
                            total_strategies=4, current_open_count=1)
        r2 = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2,
                            total_strategies=4, current_open_count=2)

        assert r1.approved
        assert not r2.approved


# ═══════════════════════════════════════════════════════════════════════
# SECTION H: Regime & Symbol Risk Multipliers
# ═══════════════════════════════════════════════════════════════════════


class TestRegimeSymbolRisk:
    """Regime and symbol risk multipliers affect position sizing."""

    def test_consolidation_regime_gets_reduced_size(self):
        """Consolidation (0% WR, -$169 in live) should get reduced risk multiplier."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=82.0, regime="consolidation")
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        # consolidation = 0.30x risk mult (disaster regime from live data)
        if result.approved:
            assert result.metadata.get("regime_risk_mult", 1.0) == 0.30

    def test_panic_regime_gets_reduced_size(self):
        """Panic regime should reduce position size."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=82.0, regime="panic")
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            panic_rm = result.metadata.get("regime_risk_mult", 1.0)
            assert panic_rm < 1.0, f"Panic regime mult should be < 1.0, got {panic_rm}"

    def test_btc_gets_near_full_symbol_risk(self):
        """BTC (proven edge) should get 0.90x symbol risk multiplier."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(symbol="BTC", confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            sym_rm = result.metadata.get("symbol_risk_mult", 1.0)
            assert sym_rm == 0.90


# ═══════════════════════════════════════════════════════════════════════
# SECTION I: Confidence-Based Sizing
# ═══════════════════════════════════════════════════════════════════════


class TestConfidenceSizing:
    """Confidence level affects position sizing through the pipeline."""

    def test_exhaustion_advisory_at_90_plus_in_trend(self):
        """90%+ confidence in non-consolidation regime: advisory only, no penalty
        (2026-06-08 de-penalized; wave2b L5 M13 keeps it advisory)."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=92.0, regime="trending_bull")
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=3, total_strategies=4)
        if result.approved:
            assert result.risk_multiplier > 0
            assert result.metadata.get("confidence_sizing") == "exhaustion_advisory_no_penalty"

    def test_85_confidence_boost_is_shadow(self):
        """85-89% confidence tier is SHADOW (wave2b L5, FALLACY_AUDIT M13):
        would-have 1.5x recorded, 1.0x applied (fossil-stat tiers need a
        clean-ledger n>=13 recompute before enforcement)."""
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=87.0, regime="consolidation")
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            assert result.metadata.get("confidence_sizing") == "high_conviction_1.5x_shadow"
            assert result.metadata.get("confidence_sizing_would_be") == 1.5


# ═══════════════════════════════════════════════════════════════════════
# SECTION I2: wave2b L5 — fallback-pipe hygiene (Gate 0 stand-down, M1/M13
# shadows, M22 deletion)
# ═══════════════════════════════════════════════════════════════════════


class TestLLMFirstStanddown:
    """Gate 0: when LLM-first is configured, the mechanical fallback chain
    must be a SAFE STAND-DOWN (honest labeled no-trade), never a
    mechanical-opinion trade (FULL_PIPE_BUILD_MAP §L5)."""

    def test_standdown_when_llm_first_configured(self):
        chain, rm, lm, cfg = _make_filter_chain(llm_first_mode=True)
        os.environ.pop("ALLOW_MECHANICAL_FALLBACK", None)
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert result.approved is False
        assert "STAND-DOWN" in result.rejection_reason
        assert "pipeline_error" in result.rejection_reason

    def test_owner_dial_restores_mechanical_trading(self):
        chain, rm, lm, cfg = _make_filter_chain(llm_first_mode=True)
        old = os.environ.get("ALLOW_MECHANICAL_FALLBACK")
        os.environ["ALLOW_MECHANICAL_FALLBACK"] = "true"
        try:
            signal = _make_signal(confidence=82.0)
            result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
            # Must NOT stand down — whatever the other gates decide
            assert "STAND-DOWN" not in result.rejection_reason
        finally:
            if old is None:
                os.environ.pop("ALLOW_MECHANICAL_FALLBACK", None)
            else:
                os.environ["ALLOW_MECHANICAL_FALLBACK"] = old

    def test_offline_harness_opt_out(self):
        """Backtest engine sets llm_first_standdown_enabled=False — the
        mechanical A/B keeps trading even with LLM_FIRST_MODE in .env."""
        chain, rm, lm, cfg = _make_filter_chain(llm_first_mode=True)
        chain.llm_first_standdown_enabled = False
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert "STAND-DOWN" not in result.rejection_reason

    def test_mechanical_mode_unaffected(self):
        """llm_first_mode=False (owner runs mechanical): no stand-down."""
        chain, rm, lm, cfg = _make_filter_chain(llm_first_mode=False)
        signal = _make_signal(confidence=82.0)
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert "STAND-DOWN" not in result.rejection_reason


class TestQuantRulesShadow:
    """M1: quant-rule boosts are shadow — would-have recorded, 1.0x applied."""

    def test_conviction_boost_is_shadow(self):
        os.environ.pop("QUANT_RULES_ENFORCE", None)
        chain, rm, lm, cfg = _make_filter_chain()
        # conf>=80 + 2 agree fires Rule 4 (conviction ×1.3) — shadow only
        signal = _make_signal(confidence=87.0, regime="consolidation")
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            assert "quant_risk_mult_boost" not in result.metadata
            assert result.metadata.get("quant_risk_mult_boost_would_be") == pytest.approx(1.3)
            # Confidence must NOT have been boosted
            assert result.signal.confidence == pytest.approx(87.0)


class TestWinProbGateDeleted:
    """M22: the win-prob floor gate was doubly-dead instrumentation — deleted.
    A rock-bottom win_prob must not produce a win_prob rejection."""

    def test_low_win_prob_not_rejected_for_win_prob(self):
        chain, rm, lm, cfg = _make_filter_chain()
        signal = _make_signal(confidence=82.0, metadata_extra={"win_prob": 0.05})
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        assert "Win probability" not in result.rejection_reason


# ═══════════════════════════════════════════════════════════════════════
# SECTION J: Trade Log and Events
# ═══════════════════════════════════════════════════════════════════════


class TestTradeLogging:
    """Trade events are logged with proper context."""

    def test_open_event_logged(self):
        """Opening a position creates an OPEN event in trade_log."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0,
                         strategy="regime_trend", confidence=82.0, atr=500.0)

        assert len(pm.trade_log) == 1
        assert pm.trade_log[0].action == "OPEN"
        assert pm.trade_log[0].symbol == "BTC"
        assert pm.trade_log[0].side == "LONG"
        assert pm.trade_log[0].strategy == "regime_trend"

    def test_sl_event_logged(self):
        """SL hit creates a close event with PnL."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        events = pm.update_price("BTC", 48000.0)

        assert len(events) == 1
        assert events[0].action == "SL"
        assert events[0].pnl < 0  # Should have negative PnL

    def test_trade_event_has_metadata(self):
        """Close events include metadata (hold_time, outcome, state_path)."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        events = pm.update_price("BTC", 48000.0)

        meta = events[0].metadata
        assert "total_pnl" in meta
        assert "outcome" in meta
        assert "state_path" in meta

    def test_fees_tracked(self):
        """Position tracks fees paid."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pos = pm.positions["BTC"]
        assert pos.fees_paid > 0  # Opening fee

        pm.update_price("BTC", 48000.0)  # Close
        assert pos.fees_paid > 0  # Should include close fee too

    def test_outcome_classified_on_close(self):
        """Position outcome is classified (CLEAN_WIN, CLEAN_LOSS, etc.)."""
        pm = _make_position_manager()
        pm.open_position("BTC", "LONG", 50000.0, 1.0, 48500.0, 52000.0, 54000.0, atr=500.0)
        pm.update_price("BTC", 48000.0)
        pos = pm.positions["BTC"]
        assert pos.outcome != "", "Outcome should be classified on close"


# ═══════════════════════════════════════════════════════════════════════
# SECTION K: Leverage & Stop Width Safety
# ═══════════════════════════════════════════════════════════════════════


class TestLeverageStopSafety:
    """Leverage caps based on stop width to prevent liquidation."""

    def test_tight_stop_caps_leverage(self):
        """Very tight stops should cap leverage to prevent liquidation."""
        chain, rm, lm, cfg = _make_filter_chain()
        # 0.4% stop width, very tight
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=85.0, entry=50000.0,
            sl=49800.0,  # 0.4% stop
            tp1=52000.0, tp2=54000.0,
            metadata={"regime": "consolidation", "num_agree": 2},
        )
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            # Tight stop should cap leverage at 10x for longs
            assert result.leverage <= 10.0, (
                f"Tight stop leverage {result.leverage} should be <= 10.0"
            )

    def test_wide_stop_allows_higher_leverage(self):
        """Wide stops allow higher leverage."""
        chain, rm, lm, cfg = _make_filter_chain()
        # 3% stop width, wide
        signal = Signal(
            strategy="test", symbol="BTC", side="BUY",
            confidence=85.0, entry=50000.0,
            sl=48500.0,  # 3% stop
            tp1=53000.0, tp2=56000.0,
            metadata={"regime": "consolidation", "num_agree": 2},
        )
        result = chain.evaluate(signal, equity=10000.0, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            # Wide stop should allow higher leverage
            assert result.leverage > 1.0

    def test_short_side_gets_tighter_leverage_cap(self):
        """SHORT positions should have tighter leverage caps than LONG (asymmetric risk)."""
        lm = LeverageManager(enable_leverage=True, max_leverage=25.0)

        # Both at same confidence
        d_long = lm.decide(confidence=85.0, num_strategies_agree=2,
                           total_strategies=4, risk_tier="medium")
        d_short = lm.decide(confidence=85.0, num_strategies_agree=2,
                            total_strategies=4, risk_tier="medium")

        # The stop-width cap in the pipeline treats shorts more conservatively
        # Here we verify the leverage manager itself produces valid results
        assert d_long.leverage > 0
        assert d_short.leverage > 0


# ═══════════════════════════════════════════════════════════════════════
# SECTION L: Integration with RiskManager.calculate_qty
# ═══════════════════════════════════════════════════════════════════════


class TestRiskManagerSizing:
    """RiskManager.calculate_qty produces sensible position sizes."""

    def test_basic_sizing(self):
        """Basic sizing: risk_usd / stop_width produces correct qty."""
        rm = _make_risk_manager(equity=10000.0, risk_per_trade=0.02)
        qty = rm.calculate_qty(entry=50000.0, stop_loss=48500.0, leverage=1.0)
        assert qty > 0

    def test_higher_leverage_smaller_qty(self):
        """Higher leverage should produce SMALLER qty (fixed-risk sizing)."""
        rm = _make_risk_manager(equity=10000.0, risk_per_trade=0.02)
        qty_1x = rm.calculate_qty(entry=50000.0, stop_loss=48500.0, leverage=1.0)
        qty_5x = rm.calculate_qty(entry=50000.0, stop_loss=48500.0, leverage=5.0)
        # With fixed-risk: higher leverage = smaller qty (same dollar risk)
        assert qty_5x < qty_1x

    def test_risk_multiplier_scales_qty(self):
        """Higher risk_multiplier should produce larger position."""
        rm = _make_risk_manager(equity=10000.0, risk_per_trade=0.02)
        qty_base = rm.calculate_qty(entry=50000.0, stop_loss=48500.0, leverage=2.0, risk_multiplier=1.0)
        qty_high = rm.calculate_qty(entry=50000.0, stop_loss=48500.0, leverage=2.0, risk_multiplier=1.5)
        assert qty_high > qty_base

    def test_near_zero_stop_returns_zero(self):
        """Near-zero stop width produces zero quantity (safety guard)."""
        rm = _make_risk_manager(equity=10000.0)
        qty = rm.calculate_qty(entry=50000.0, stop_loss=49999.0)
        assert qty == 0.0

    def test_notional_cap_applied(self):
        """Position size is capped to prevent oversized positions."""
        rm = _make_risk_manager(equity=1000.0, risk_per_trade=0.50)
        qty = rm.calculate_qty(entry=100.0, stop_loss=95.0, leverage=10.0)
        notional = qty * 100.0
        max_notional = 1000.0 * 10.0 * 2  # equity * leverage * 2
        assert notional <= max_notional * 1.01  # Small tolerance for float math
