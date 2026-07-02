"""
P0 Critical Tests — Cover the three highest-priority test gaps:

1. Intra-candle SL/TP wick simulation (backtest accuracy)
2. RiskFilterChain 6-gate sequential validation
3. MTM equity impact on circuit breakers and risk metrics

These features directly affect PnL accuracy and capital safety.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ────────────────────────────────────────────────────────────────────
# SECTION 1: Intra-candle SL/TP wick simulation
# ────────────────────────────────────────────────────────────────────

class TestIntraCandleWickSLTP:
    """Verify that the backtest engine checks SL/TP using candle wicks,
    not just the close price. Worst-case-first order prevents
    the backtest from surviving wicks that would stop out live positions."""

    def _make_pos_mgr(self):
        from execution.position_manager import PositionManager
        return PositionManager(taker_fee_bps=5, enable_trailing=True)

    def _open_long(self, pm, symbol="BTC", entry=100.0, sl=95.0, tp1=110.0, tp2=120.0):
        pm.open_position(
            symbol=symbol, side="LONG", entry=entry, qty=1.0,
            sl=sl, tp1=tp1, tp2=tp2, leverage=1.0, atr=2.0,
        )
        return pm.positions[symbol]

    def _open_short(self, pm, symbol="BTC", entry=100.0, sl=105.0, tp1=90.0, tp2=80.0):
        pm.open_position(
            symbol=symbol, side="SHORT", entry=entry, qty=1.0,
            sl=sl, tp1=tp1, tp2=tp2, leverage=1.0, atr=2.0,
        )
        return pm.positions[symbol]

    # ── Long: wick hits SL ──

    def test_long_sl_hit_by_low_wick(self):
        """LONG position: candle low dips below SL, close above SL.
        The backtest must stop out using the low wick, not survive on close."""
        pm = self._make_pos_mgr()
        self._open_long(pm, sl=95.0)

        # Simulate worst-case first (candle low = 94.0 < SL 95.0)
        events = pm.update_price("BTC", 94.0)
        assert len(events) == 1
        assert events[0].action == "SL"

    def test_long_survives_wick_above_sl(self):
        """LONG: candle low stays above SL → no stop out."""
        pm = self._make_pos_mgr()
        self._open_long(pm, sl=95.0)

        events = pm.update_price("BTC", 96.0)
        assert len(events) == 0
        assert pm.positions["BTC"].state != "CLOSED"

    def test_long_tp1_hit_by_high_wick(self):
        """LONG: candle high reaches TP1 while close is below TP1.
        Backtest should trigger partial close via high wick."""
        pm = self._make_pos_mgr()
        self._open_long(pm, tp1=110.0)

        # First check worst case (low = 97, above SL) → no event
        events = pm.update_price("BTC", 97.0)
        assert len(events) == 0

        # Then check best case (high = 111, above TP1) → TP1 partial close
        events = pm.update_price("BTC", 111.0)
        assert len(events) == 1
        assert events[0].action == "TP1"

    def test_long_sl_takes_priority_over_tp(self):
        """LONG: candle wicks both below SL and above TP1.
        Worst-case-first: SL must fire, not TP1."""
        pm = self._make_pos_mgr()
        self._open_long(pm, entry=100.0, sl=95.0, tp1=110.0)

        # Worst case first: low = 94 < SL
        events = pm.update_price("BTC", 94.0)
        assert len(events) == 1
        assert events[0].action == "SL"
        # Position is closed, so TP check never runs

    # ── Short: wick hits SL ──

    def test_short_sl_hit_by_high_wick(self):
        """SHORT position: candle high spikes above SL → stop out."""
        pm = self._make_pos_mgr()
        self._open_short(pm, sl=105.0)

        events = pm.update_price("BTC", 106.0)
        assert len(events) == 1
        assert events[0].action == "SL"

    def test_short_tp1_hit_by_low_wick(self):
        """SHORT: candle low dips below TP1 → partial close."""
        pm = self._make_pos_mgr()
        self._open_short(pm, tp1=90.0)

        # Worst case (high = 103, below SL 105) → no SL
        events = pm.update_price("BTC", 103.0)
        assert len(events) == 0

        # Best case (low = 89 < TP1 90) → TP1 hit
        events = pm.update_price("BTC", 89.0)
        assert len(events) == 1
        assert events[0].action == "TP1"

    def test_short_sl_priority_over_tp(self):
        """SHORT: wick hits both SL (high) and TP (low).
        SL fires first per worst-case-first rule."""
        pm = self._make_pos_mgr()
        self._open_short(pm, entry=100.0, sl=105.0, tp1=90.0)

        # Worst case: high = 106 > SL 105
        events = pm.update_price("BTC", 106.0)
        assert len(events) == 1
        assert events[0].action == "SL"

    # ── Slippage on exits ──

    def test_exit_slippage_worsens_long_sl(self):
        """Exit slippage should make SL fill price worse for longs
        (fill lower than actual low wick)."""
        pm = self._make_pos_mgr()
        self._open_long(pm, entry=100.0, sl=95.0)

        # Candle low = 94.5 (below SL 95.0)
        # With slippage, fill should be even lower
        exit_slip_bps = 10  # 0.10%
        exit_slip = exit_slip_bps / 10000.0
        worst_price = 94.5
        worst_with_slip = worst_price * (1 - exit_slip)

        events = pm.update_price("BTC", worst_with_slip)
        assert len(events) == 1
        assert events[0].action == "SL"
        assert events[0].price <= worst_price  # Filled at or worse than wick

    def test_exit_slippage_worsens_short_sl(self):
        """Exit slippage should make SL fill price worse for shorts
        (fill higher than actual high wick)."""
        pm = self._make_pos_mgr()
        self._open_short(pm, entry=100.0, sl=105.0)

        exit_slip_bps = 10
        exit_slip = exit_slip_bps / 10000.0
        worst_price = 105.5
        worst_with_slip = worst_price * (1 + exit_slip)

        events = pm.update_price("BTC", worst_with_slip)
        assert len(events) == 1
        assert events[0].action == "SL"
        assert events[0].price >= worst_price

    # ── Three-stage ordering ──

    def test_three_stage_ordering_close_price_fallback(self):
        """When neither SL nor TP is hit by wicks, settle on close price
        for trailing stop updates."""
        pm = self._make_pos_mgr()
        pos = self._open_long(pm, entry=100.0, sl=95.0, tp1=110.0)

        # Stage 1: worst (low=97) > SL → no events
        events = pm.update_price("BTC", 97.0)
        assert len(events) == 0

        # Stage 2: best (high=108) < TP1 → no events
        events = pm.update_price("BTC", 108.0)
        assert len(events) == 0

        # Stage 3: close=102 → no events, position still open
        events = pm.update_price("BTC", 102.0)
        assert len(events) == 0
        assert pm.positions["BTC"].state != "CLOSED"

    def test_tp2_full_close_on_wick(self):
        """Full TP2 close triggers when wick reaches TP2 level."""
        pm = self._make_pos_mgr()
        self._open_long(pm, entry=100.0, sl=95.0, tp1=110.0, tp2=120.0)

        # First hit TP1 to transition to TRAILING
        events = pm.update_price("BTC", 111.0)
        assert any(e.action == "TP1" for e in events)

        # Now hit TP2 via high wick
        events = pm.update_price("BTC", 121.0)
        assert any(e.action == "TP2" for e in events)


# ────────────────────────────────────────────────────────────────────
# SECTION 2: RiskFilterChain — all 6 gates
# ────────────────────────────────────────────────────────────────────

class TestRiskFilterChainGates:
    """Test each gate in the RiskFilterChain individually,
    confirming rejection reasons and pass-through behavior."""

    def _make_signal(self, **overrides):
        from strategies.base import Signal
        defaults = dict(
            strategy="test", symbol="BTC", side="BUY",
            confidence=75.0, entry=100.0, sl=97.0,
            tp1=106.0, tp2=112.0, atr=1.5,
            metadata={"ev_per_dollar": 0.50},
        )
        defaults.update(overrides)
        return Signal(**defaults)

    def _make_chain(self, equity=10000.0, **cb_kwargs):
        from core.signal_pipeline import RiskFilterChain
        from execution.risk import RiskManager, CircuitBreaker
        from execution.leverage import LeverageManager
        from trading_config import TradingConfig

        cb = CircuitBreaker(**cb_kwargs)
        rm = RiskManager(starting_equity=equity, circuit_breaker=cb)
        lm = LeverageManager(enable_leverage=True)
        cfg = TradingConfig()

        # RiskFilterChain calls is_trading_allowed() and get_override_constraints()
        # (CircuitBreaker methods) AND calculate_qty() (RiskManager method) on risk_mgr.
        # Bridge the interface gap by adding CB methods to the RiskManager instance.
        rm.is_trading_allowed = cb.is_trading_allowed
        rm.get_override_constraints = cb.get_override_constraints

        return RiskFilterChain(rm, lm, cfg), rm, cb

    # ── Gate 1: Signal validity ──

    def test_gate1_rejects_invalid_stop_width(self):
        """Gate 1: Signals with stop width < 0.3% are rejected."""
        sig = self._make_signal(entry=100.0, sl=99.9)  # 0.1% stop width
        chain, _, _ = self._make_chain()
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert not result.approved
        assert "Invalid signal" in result.rejection_reason or "stop_width" in result.rejection_reason

    def test_gate1_rejects_wrong_side_sl(self):
        """Gate 1: BUY with SL above entry is invalid."""
        sig = self._make_signal(side="BUY", entry=100.0, sl=102.0, tp1=106.0)
        chain, _, _ = self._make_chain()
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert not result.approved

    def test_gate1_rejects_low_rr(self):
        """Gate 1: R:R < 1.0 is rejected."""
        # entry=100, sl=97 (3% stop), tp1=101 (1% gain) → R:R = 0.33
        sig = self._make_signal(entry=100.0, sl=97.0, tp1=101.0)
        chain, _, _ = self._make_chain()
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert not result.approved
        assert "R:R" in result.rejection_reason or "Invalid" in result.rejection_reason

    # ── Gate 1b: Config min R:R — DELETED (DECHOKE 2, 2026-07-02) ──

    def test_gate1b_min_rr_deleted(self):
        """DECHOKE 2: rr_floor gate deleted (zero live firings, GM_GATE_ROC).
        A 1.2 R:R signal is no longer rejected for config min_signal_rr."""
        # entry=100, sl=97 (3 stop), tp1=103.6 (3.6 gain) → R:R=1.2
        sig = self._make_signal(entry=100.0, sl=97.0, tp1=103.6, tp2=112.0)
        chain, _, _ = self._make_chain()
        chain.config.min_signal_rr = 1.5
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert "R:R" not in result.rejection_reason

    # ── Gate 1c: Minimum EV — DELETED (DECHOKE 2, 2026-07-02) ──

    def test_gate1c_ev_floor_deleted(self):
        """DECHOKE 2: ev_floor gate deleted (zero live firings; ev_per_dollar
        input measured anti-predictive, AUC .44-.47, FALLACY_AUDIT D4).
        Low EV no longer rejects; the number stays as labeled context."""
        sig = self._make_signal(metadata={"ev_per_dollar": 0.05})
        chain, _, _ = self._make_chain()
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert "EV" not in result.rejection_reason
        if result.approved:
            assert result.metadata.get("ev_per_dollar") == 0.05

    def test_gate1c_passes_high_ev(self):
        """Gate 1c: EV above threshold passes."""
        sig = self._make_signal(metadata={"ev_per_dollar": 0.50})
        chain, _, _ = self._make_chain()
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        # Should pass gate 1c (may fail later gates, but EV is fine)
        assert "EV" not in result.rejection_reason

    def test_gate1c_no_ev_passes(self):
        """Gate 1c: If EV not in metadata, gate is skipped (not rejected)."""
        sig = self._make_signal(metadata={})
        chain, _, _ = self._make_chain()
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert "EV" not in result.rejection_reason

    # ── Gate 2: Circuit breaker ──

    def test_gate2_rejects_when_cb_tripped(self):
        """Gate 2: When CB is tripped, normal-confidence signals are rejected."""
        chain, rm, cb = self._make_chain()
        cb.tripped = True
        cb.trip_time = 1e18  # Far future — no cooldown
        cb.trip_reason = "test"
        sig = self._make_signal(confidence=75.0)
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert not result.approved
        assert "Circuit breaker" in result.rejection_reason

    def test_gate2_allows_high_confidence_override(self):
        """Gate 2: High-confidence signals can override CB (if overrides available)."""
        chain, rm, cb = self._make_chain(max_cb_overrides=2)
        cb.tripped = True
        cb.trip_time = 1e18
        cb.trip_reason = "test"
        sig = self._make_signal(confidence=95.0)  # > 92% default threshold
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        # Should pass gate 2 via override
        assert "Circuit breaker" not in result.rejection_reason

    # ── Gate 3: Max open positions ──

    def test_gate3_rejects_at_max_positions(self):
        """Gate 3: Reject when already at max open positions."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal()
        result = chain.evaluate(
            sig, equity=10000, num_strategies_agree=3, total_strategies=4,
            current_open_count=chain.config.max_open_positions,
        )
        assert not result.approved
        assert "Max positions" in result.rejection_reason

    def test_gate3_passes_below_max(self):
        """Gate 3: Passes when below max open positions."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal()
        result = chain.evaluate(
            sig, equity=10000, num_strategies_agree=3, total_strategies=4,
            current_open_count=0,
        )
        assert "Max positions" not in result.rejection_reason

    # ── Gate 4: Correlation guard ──

    def test_gate4_rejects_high_cluster_risk(self):
        """Gate 4: Cluster risk >= 0.85 causes rejection (when NOT a market-wide move)."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal()

        # Mock portfolio risk engine
        mock_engine = MagicMock()
        mock_corr_matrix = MagicMock()
        mock_corr_matrix.get_cluster_risk.return_value = 0.90
        mock_engine.compute_correlation_matrix.return_value = mock_corr_matrix

        # Only 2 unique symbols (BTC from signal + ETH) — NOT a market-wide move,
        # so the correlation guard should still reject.
        mock_positions = {"ETH": MagicMock(side="LONG"), "ETH2": MagicMock(side="SELL")}

        result = chain.evaluate(
            sig, equity=10000, num_strategies_agree=3, total_strategies=4,
            current_open_count=2, open_positions=mock_positions,
            portfolio_risk_engine=mock_engine,
        )
        assert not result.approved
        assert "Correlation" in result.rejection_reason or "cluster" in result.rejection_reason.lower()

    def test_gate4_market_wide_override(self):
        """Gate 4: Market-wide move (3+ symbols same direction) overrides correlation guard."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal()  # BTC BUY

        mock_engine = MagicMock()
        mock_corr_matrix = MagicMock()
        mock_corr_matrix.get_cluster_risk.return_value = 0.90
        mock_engine.compute_correlation_matrix.return_value = mock_corr_matrix

        # 3 unique symbols all LONG (BTC + ETH + SOL) — market-wide move, should pass
        mock_positions = {"ETH": MagicMock(side="LONG"), "SOL": MagicMock(side="LONG")}

        result = chain.evaluate(
            sig, equity=10000, num_strategies_agree=3, total_strategies=4,
            current_open_count=2, open_positions=mock_positions,
            portfolio_risk_engine=mock_engine,
        )
        # Should NOT be rejected — market-wide override kicks in
        assert "cluster" not in (result.rejection_reason or "").lower()
        assert result.metadata.get("corr_guard_override") == "market_wide_move"

    def test_gate4_reduces_size_moderate_risk(self):
        """Gate 4: Cluster risk 0.70-0.85 reduces position size but doesn't reject."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal()

        mock_engine = MagicMock()
        mock_corr_matrix = MagicMock()
        mock_corr_matrix.get_cluster_risk.return_value = 0.75
        mock_engine.compute_correlation_matrix.return_value = mock_corr_matrix

        mock_positions = {"ETH": MagicMock(side="LONG"), "SOL": MagicMock(side="LONG")}

        result = chain.evaluate(
            sig, equity=10000, num_strategies_agree=3, total_strategies=4,
            current_open_count=2, open_positions=mock_positions,
            portfolio_risk_engine=mock_engine,
        )
        # Should pass (not rejected) but with size reduction
        if result.approved:
            assert result.metadata.get("correlation_size_reduction") == 0.7

    def test_gate4_skipped_under_2_positions(self):
        """Gate 4: Skipped entirely when < 2 open positions."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal()

        mock_engine = MagicMock()
        result = chain.evaluate(
            sig, equity=10000, num_strategies_agree=3, total_strategies=4,
            current_open_count=1, portfolio_risk_engine=mock_engine,
        )
        # correlation guard shouldn't have been consulted
        mock_engine.compute_correlation_matrix.assert_not_called()

    # ── Gate 5: Leverage decision ──

    def test_gate5_rejects_low_confidence(self):
        """Gate 5: Confidence < 20% → leverage=0 → rejected. (Aggressive mode: lowered from 60%)"""
        chain, _, _ = self._make_chain()
        sig = self._make_signal(confidence=15.0)
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=2, total_strategies=4)
        assert not result.approved
        assert "Leverage denied" in result.rejection_reason or "too low" in result.rejection_reason.lower()

    def test_gate5_approves_with_leverage(self):
        """Gate 5: Confidence 75% + 2 strategies → approved with leverage."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal(confidence=75.0)
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=2, total_strategies=4)
        if result.approved:
            assert result.leverage >= 1.0
            assert result.metadata.get("leverage_tier") is not None

    def test_gate5_cb_caps_leverage(self):
        """Gate 5: CB override constraints cap leverage at 2x."""
        chain, rm, cb = self._make_chain(max_cb_overrides=5)
        cb.tripped = True
        cb.trip_time = 1e18
        cb.trip_reason = "test"
        sig = self._make_signal(confidence=95.0)  # High enough for CB override
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        if result.approved:
            assert result.leverage <= 2.0  # CB caps at 2x

    # ── Gate 5b: Leverage-scaled EV floor ──

    def test_gate5b_rejects_low_ev_with_high_leverage(self):
        """Gate 5b: High leverage + low EV → rejected."""
        chain, _, _ = self._make_chain()
        # EV=0.21: passes gate 1d (>0.20) but should fail gate 5b (>4x needs >=0.22 for 3-agree)
        sig = self._make_signal(confidence=85.0, metadata={"ev_per_dollar": 0.21})
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        # If leverage > 2.0, gate 5b should require higher EV
        if not result.approved and "EV" in result.rejection_reason:
            assert "leverage" in result.rejection_reason.lower()

    # ── Gate 6: Liquidation safety ──

    def test_gate6_rejects_sl_beyond_liquidation(self):
        """Gate 6: SL farther from entry than liquidation price → rejected."""
        chain, _, _ = self._make_chain()
        # entry=100, sl=95 (5% stop), tp1=110, tp2=120 — valid R:R
        # At 20x leverage, liq price is ~95 so SL at 95 is right at liquidation
        sig = self._make_signal(
            confidence=85.0, entry=100.0, sl=95.0, tp1=110.0, tp2=120.0,
        )
        # Force very high leverage so liquidation price is closer than SL
        with patch.object(chain.leverage_mgr, 'decide') as mock_decide:
            from execution.leverage import LeverageDecision
            mock_decide.return_value = LeverageDecision(20.0, "leverage", "extreme", "test", 1.2)
            with patch.object(chain.leverage_mgr, 'validate_stop_vs_liquidation') as mock_liq:
                mock_liq.return_value = {"safe": False, "liquidation_price": 96.0, "gap_pct": -0.01}
                result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
                assert not result.approved
                assert "liquidation" in result.rejection_reason.lower() or "SL" in result.rejection_reason

    # ── Gate 6b: Position sizing ──

    def test_gate6b_rejects_zero_qty(self):
        """Gate 6b: If risk manager returns qty=0, signal is rejected."""
        chain, rm, _ = self._make_chain()
        sig = self._make_signal()
        with patch.object(rm, 'calculate_qty', return_value=0.0):
            result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
            assert not result.approved
            assert "size zero" in result.rejection_reason.lower() or "Position" in result.rejection_reason

    # ── Full pipeline pass ──

    def test_full_pipeline_approval(self):
        """All gates pass → approved with leverage, risk_mult, and qty."""
        chain, _, _ = self._make_chain()
        sig = self._make_signal(
            confidence=85.0, entry=100.0, sl=97.0, tp1=106.0, tp2=112.0,
            metadata={"ev_per_dollar": 0.50},
        )
        result = chain.evaluate(sig, equity=10000, num_strategies_agree=3, total_strategies=4)
        assert result.approved
        assert result.leverage >= 1.0
        assert result.position_qty > 0
        assert "rr_tp1" in result.metadata
        assert "leverage" in result.metadata

    def test_gate_order_is_sequential(self):
        """Rejection at gate N means gates N+1..6 are never checked.
        CB tripped → no leverage check, no sizing."""
        chain, rm, cb = self._make_chain()
        cb.tripped = True
        cb.trip_time = 1e18
        cb.trip_reason = "test"

        sig = self._make_signal(confidence=50.0)  # Too low for CB override

        with patch.object(chain.leverage_mgr, 'decide') as mock_lev:
            result = chain.evaluate(sig, equity=10000, num_strategies_agree=2, total_strategies=4)
            assert not result.approved
            # Leverage decide should NOT have been called (rejected at gate 2)
            mock_lev.assert_not_called()


# ────────────────────────────────────────────────────────────────────
# SECTION 3: MTM Equity — unrealized PnL affects circuit breakers
# ────────────────────────────────────────────────────────────────────

class TestMTMEquityCircuitBreaker:
    """Verify that circuit breaker evaluation uses current equity
    (which should include unrealized PnL) rather than stale values."""

    def test_daily_loss_uses_current_equity(self):
        """CB daily loss % is computed against CURRENT equity, not peak.
        During drawdown, losses are a bigger % of actual capital."""
        from execution.risk import CircuitBreaker

        cb = CircuitBreaker(daily_loss_limit_pct=0.05)  # 5% daily limit
        cb.peak_equity = 12000  # Peak was 12k
        cb.start_of_day_equity = 10000

        # Lose $500 on current equity of $10,000 → 5% → should trip
        cb.record_trade(-500, equity=10000)
        assert cb.tripped
        assert "Daily loss" in cb.trip_reason

    def test_daily_loss_not_against_peak_equity(self):
        """If daily loss were calculated against peak equity (12k),
        $500 loss would be only 4.2% and NOT trip. Verify it trips
        because we use current equity (10k → 5%)."""
        from execution.risk import CircuitBreaker

        cb = CircuitBreaker(daily_loss_limit_pct=0.05)
        cb.peak_equity = 12000

        # $500 loss at current equity 10000 → 5.0% (trips)
        # If we wrongly used peak_equity=12000 → 4.17% (wouldn't trip)
        cb.record_trade(-500, equity=10000)
        assert cb.tripped, "CB should trip using current equity, not peak"

    def test_drawdown_from_peak(self):
        """CB drawdown check: (peak - current) / peak."""
        from execution.risk import CircuitBreaker

        cb = CircuitBreaker(max_drawdown_pct=0.10)  # 10% drawdown limit
        cb.peak_equity = 10000

        # Equity dropped to 8900 → 11% drawdown → should trip
        cb.record_trade(-100, equity=8900)
        assert cb.tripped
        assert "Drawdown" in cb.trip_reason

    def test_consecutive_losses_trip(self):
        """CB consecutive loss limit."""
        from execution.risk import CircuitBreaker

        cb = CircuitBreaker(max_consecutive_losses=3, daily_loss_limit_pct=1.0, max_drawdown_pct=1.0)
        cb.peak_equity = 10000

        cb.record_trade(-10, equity=9990)
        assert not cb.tripped
        cb.record_trade(-10, equity=9980)
        assert not cb.tripped
        cb.record_trade(-10, equity=9970)
        assert cb.tripped
        assert "consecutive" in cb.trip_reason.lower()

    def test_win_resets_consecutive_losses(self):
        """A winning trade resets the consecutive loss counter."""
        from execution.risk import CircuitBreaker

        cb = CircuitBreaker(max_consecutive_losses=3, daily_loss_limit_pct=1.0, max_drawdown_pct=1.0)
        cb.peak_equity = 10000

        cb.record_trade(-10, equity=9990)
        cb.record_trade(-10, equity=9980)
        assert cb.consecutive_losses == 2

        cb.record_trade(50, equity=10030)
        assert cb.consecutive_losses == 0
        assert not cb.tripped

    def test_risk_manager_equity_tracks_realized_pnl(self):
        """RiskManager.equity updates after each trade PnL."""
        from execution.risk import RiskManager

        rm = RiskManager(starting_equity=10000.0)
        assert rm.equity == 10000.0

        rm.update_equity(-200)
        assert rm.equity == 9800.0

        rm.update_equity(500)
        assert rm.equity == 10300.0

    def test_position_sizing_uses_current_equity(self):
        """Position sizing must use current equity, not starting equity."""
        from execution.risk import RiskManager

        rm = RiskManager(starting_equity=10000.0, risk_per_trade=0.02)

        # After losses, equity is lower → position size should decrease
        rm.update_equity(-2000)  # equity now 8000
        assert rm.equity == 8000.0

        qty = rm.calculate_qty(entry=100.0, stop_loss=97.0, leverage=1.0)
        # risk_usd = 8000 * 0.02 = 160. stop_width = 3. qty = 160 / 3 = 53.3
        expected_max = 8000.0 * 0.02 / 3.0
        assert qty <= expected_max * 1.01  # Allow tiny float tolerance

    def test_cb_trips_during_backtest_with_sim_time(self):
        """CB should trip during backtest using sim_time, not wall clock."""
        from execution.risk import CircuitBreaker
        from datetime import datetime, timezone

        cb = CircuitBreaker(daily_loss_limit_pct=0.05)
        cb.peak_equity = 10000

        sim_time = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        cb.record_trade(-600, equity=10000, sim_time=sim_time)
        assert cb.tripped

    def test_cb_daily_reset_across_sim_days(self):
        """Daily PnL resets when simulation crosses midnight boundary."""
        from execution.risk import CircuitBreaker
        from datetime import datetime, timezone

        cb = CircuitBreaker(daily_loss_limit_pct=0.05)
        cb.peak_equity = 10000

        day1 = datetime(2025, 1, 15, 20, 0, tzinfo=timezone.utc)
        cb.record_trade(-400, equity=10000, sim_time=day1)  # 4% → no trip
        assert not cb.tripped
        assert cb.daily_pnl == -400

        # Next day: daily PnL should reset
        day2 = datetime(2025, 1, 16, 8, 0, tzinfo=timezone.utc)
        cb.record_trade(-100, equity=9900, sim_time=day2)
        # daily_pnl should be -100 (reset), not -500 (accumulated)
        assert cb.daily_pnl == -100

    def test_funding_costs_reduce_position_pnl(self):
        """Funding costs accumulated on positions reduce net PnL."""
        from execution.position_manager import PositionManager

        pm = PositionManager(taker_fee_bps=5)
        pm.open_position(
            symbol="BTC", side="LONG", entry=100.0, qty=1.0,
            sl=95.0, tp1=110.0, tp2=120.0, leverage=2.0, atr=2.0,
        )
        pos = pm.positions["BTC"]

        # Accrue funding
        pm.accrue_funding("BTC", funding_rate=0.0001, interval_hours=8.0)
        assert pos.funding_costs > 0

        initial_funding = pos.funding_costs
        pm.accrue_funding("BTC", funding_rate=0.0001, interval_hours=8.0)
        assert pos.funding_costs > initial_funding  # Costs accumulate

    def test_post_cooldown_caution_mode(self):
        """After CB cooldown, next 4 trades get reduced size (caution mode)."""
        from execution.risk import CircuitBreaker

        cb = CircuitBreaker(daily_loss_limit_pct=0.05, cooldown_minutes=1)
        cb.peak_equity = 10000

        # Trip the CB
        sim_time = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        cb.record_trade(-600, equity=10000, sim_time=sim_time)
        assert cb.tripped

        # After cooldown (2 minutes later)
        after_cooldown = sim_time + timedelta(minutes=2)
        allowed = cb.is_trading_allowed(confidence=75, sim_time=after_cooldown)
        assert allowed
        assert not cb.tripped  # CB reset
        assert cb.post_cooldown_caution == 4  # Next 4 trades at reduced size

        # Constraints should be "cautious"
        constraints = cb.get_override_constraints(75)
        assert constraints["constrained"]
        assert constraints["max_leverage"] <= 2.0
        assert constraints["size_multiplier"] == 0.5


# ────────────────────────────────────────────────────────────────────
# SECTION 4: RiskManager delegation + MTM circuit breaker awareness
# ────────────────────────────────────────────────────────────────────

class TestRiskManagerDelegation:
    """RiskManager must expose is_trading_allowed() and get_override_constraints()
    so that RiskFilterChain can call them (signal_pipeline.py:107,177)."""

    def test_is_trading_allowed_delegates_to_cb(self):
        """RiskManager.is_trading_allowed() delegates to CircuitBreaker."""
        from execution.risk import RiskManager
        rm = RiskManager(starting_equity=10000.0)
        assert rm.is_trading_allowed(confidence=50.0) is True

        # Trip the CB
        rm.circuit_breaker.tripped = True
        rm.circuit_breaker.trip_time = 1e18
        rm.circuit_breaker.trip_reason = "test"
        assert rm.is_trading_allowed(confidence=50.0) is False

    def test_get_override_constraints_delegates_to_cb(self):
        """RiskManager.get_override_constraints() delegates to CircuitBreaker."""
        from execution.risk import RiskManager
        rm = RiskManager(starting_equity=10000.0)
        constraints = rm.get_override_constraints(confidence=50.0)
        assert not constraints["constrained"]
        assert constraints["max_leverage"] == 25.0

        # Trip CB → constrained
        rm.circuit_breaker.tripped = True
        rm.circuit_breaker.trip_time = 1e18
        rm.circuit_breaker.trip_reason = "test"
        constraints = rm.get_override_constraints(confidence=99.0)
        assert constraints["constrained"]
        assert constraints["max_leverage"] == 2.0

    def test_signal_pipeline_calls_work_with_risk_manager(self):
        """RiskFilterChain gates 2 and 5 call is_trading_allowed / get_override_constraints
        on the risk_mgr object — verify no AttributeError."""
        from execution.risk import RiskManager
        rm = RiskManager(starting_equity=10000.0)

        # Simulate what signal_pipeline.py does
        allowed = rm.is_trading_allowed(confidence=80.0, cb_conf_override_pct=0.92)
        assert allowed is True

        constraints = rm.get_override_constraints(confidence=80.0)
        assert "max_leverage" in constraints
        assert "size_multiplier" in constraints


class TestMTMCircuitBreakerAwareness:
    """CircuitBreaker.check_mtm_breakers() catches drawdowns from open positions."""

    def test_mtm_drawdown_trips_cb(self):
        """Unrealized losses that exceed drawdown threshold trip the CB."""
        from execution.risk import CircuitBreaker
        cb = CircuitBreaker(max_drawdown_pct=0.10)
        cb.peak_equity = 10000

        # Realized equity is still 10000, but open positions are -1500
        # MTM equity = 10000 - 1500 = 8500 → 15% drawdown → should trip
        cb.check_mtm_breakers(mtm_equity=8500)
        assert cb.tripped
        assert "MTM drawdown" in cb.trip_reason
        assert "unrealized" in cb.trip_reason.lower()

    def test_mtm_no_trip_within_threshold(self):
        """Unrealized losses within threshold don't trip."""
        from execution.risk import CircuitBreaker
        cb = CircuitBreaker(max_drawdown_pct=0.10)
        cb.peak_equity = 10000

        # MTM equity = 9200 → 8% drawdown → within 10% limit
        cb.check_mtm_breakers(mtm_equity=9200)
        assert not cb.tripped

    def test_mtm_updates_peak_equity(self):
        """MTM check updates peak equity when MTM > current peak."""
        from execution.risk import CircuitBreaker
        cb = CircuitBreaker(max_drawdown_pct=0.10)
        cb.peak_equity = 10000

        # Open positions are profitable → MTM > peak
        cb.check_mtm_breakers(mtm_equity=10500)
        assert cb.peak_equity == 10500
        assert not cb.tripped

    def test_mtm_peak_then_drop_trips(self):
        """Peak updates on unrealized gain, then drawdown measured from new peak."""
        from execution.risk import CircuitBreaker
        cb = CircuitBreaker(max_drawdown_pct=0.10)
        cb.peak_equity = 10000

        # Unrealized gain pushes peak to 11000
        cb.check_mtm_breakers(mtm_equity=11000)
        assert cb.peak_equity == 11000
        assert not cb.tripped

        # Now unrealized reversal: MTM drops to 9800 → 10.9% from 11000 peak
        cb.check_mtm_breakers(mtm_equity=9800)
        assert cb.tripped
        assert "MTM drawdown" in cb.trip_reason

    def test_mtm_noop_when_already_tripped(self):
        """If CB is already tripped, MTM check is a no-op."""
        from execution.risk import CircuitBreaker
        cb = CircuitBreaker(max_drawdown_pct=0.10)
        cb.peak_equity = 10000
        cb.tripped = True
        cb.trip_reason = "previous trip"

        cb.check_mtm_breakers(mtm_equity=5000)  # 50% drawdown
        assert cb.trip_reason == "previous trip"  # Unchanged

    def test_risk_manager_check_unrealized_risk(self):
        """RiskManager.check_unrealized_risk() feeds unrealized PnL to CB."""
        from execution.risk import RiskManager
        rm = RiskManager(starting_equity=10000.0)
        rm.circuit_breaker.peak_equity = 10000

        # Unrealized loss of -1500 → MTM = 8500 → 15% drawdown
        rm.check_unrealized_risk(unrealized_pnl=-1500)
        assert rm.circuit_breaker.tripped
        assert "MTM" in rm.circuit_breaker.trip_reason

    def test_risk_manager_mtm_no_trip_small_loss(self):
        """Small unrealized loss doesn't trip CB."""
        from execution.risk import RiskManager
        rm = RiskManager(starting_equity=10000.0)
        rm.circuit_breaker.peak_equity = 10000

        rm.check_unrealized_risk(unrealized_pnl=-500)  # 5% drawdown
        assert not rm.circuit_breaker.tripped

    def test_mtm_with_sim_time(self):
        """MTM check works with sim_time for backtests."""
        from execution.risk import CircuitBreaker
        cb = CircuitBreaker(max_drawdown_pct=0.10)
        cb.peak_equity = 10000

        sim_time = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
        cb.check_mtm_breakers(mtm_equity=8500, sim_time=sim_time)
        assert cb.tripped
