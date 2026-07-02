"""
Tests for quant rules in the signal pipeline — SHADOW since wave2b L5 (M1).

apply_quant_rules() computes WOULD-HAVE boosts only; the RiskFilterChain
applies 1.0x unless QUANT_RULES_ENFORCE=true (FALLACY_AUDIT_2026-07-02 M1).

Rules under test (would-have arithmetic):
1. Morning Edge (06-12 UTC) — 1.2x confidence (shadow)
2. BTC SHORT Edge — DELETED outright (pure directional opinion, M1)
3. 12h Time Stop — optimal hold duration
4. HYPE BUY in High Vol — 1.2x confidence (shadow)
5. Conviction Multiplier — 1.3x risk mult (shadow)
"""

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import pytest

from core.signal_pipeline import apply_quant_rules


# ── Minimal stubs for testing ──────────────────────────────────────────

@dataclass
class MockSignal:
    strategy: str = "test"
    symbol: str = "BTC"
    side: str = "BUY"
    confidence: float = 75.0
    entry: float = 100.0
    sl: float = 98.0
    tp1: float = 103.0
    tp2: float = 106.0
    atr: float = 2.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MockConfig:
    """Minimal config with quant rule flags."""
    quant_morning_edge_enabled: bool = True
    quant_morning_edge_boost: float = 1.2
    quant_btc_short_edge_enabled: bool = True
    quant_btc_short_edge_boost: float = 1.15
    quant_hype_highvol_enabled: bool = True
    quant_hype_highvol_boost: float = 1.2
    quant_conviction_mult_enabled: bool = True
    quant_conviction_risk_mult: float = 1.3
    quant_conviction_min_confidence: float = 80.0
    quant_conviction_min_agree: int = 2
    max_ensemble_confidence: float = 95.0


# ── Rule 1: Morning Edge Tests ────────────────────────────────────────

class TestMorningEdge:
    """06-12 UTC should apply 1.2x confidence boost."""

    def test_morning_edge_applies_in_window(self):
        """Signals in 06-12 UTC get boosted."""
        signal = MockSignal()
        config = MockConfig()
        for hour in [6, 7, 8, 9, 10, 11]:
            now = datetime(2026, 3, 30, hour, 30, tzinfo=timezone.utc)
            result = apply_quant_rules(signal, config, now=now)
            assert "morning_edge" in result["rules_applied"], f"hour={hour}"
            assert result["confidence_boost"] == pytest.approx(1.2), f"hour={hour}"

    def test_morning_edge_not_applied_outside_window(self):
        """Signals outside 06-12 UTC should NOT get boosted."""
        signal = MockSignal()
        config = MockConfig()
        for hour in [0, 1, 5, 12, 13, 18, 23]:
            now = datetime(2026, 3, 30, hour, 30, tzinfo=timezone.utc)
            result = apply_quant_rules(signal, config, now=now)
            assert "morning_edge" not in result["rules_applied"], f"hour={hour}"

    def test_morning_edge_disabled(self):
        """When disabled, no morning boost."""
        signal = MockSignal()
        config = MockConfig(quant_morning_edge_enabled=False)
        now = datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "morning_edge" not in result["rules_applied"]
        assert result["confidence_boost"] == 1.0

    def test_morning_edge_custom_boost(self):
        """Custom boost value works."""
        signal = MockSignal()
        config = MockConfig(quant_morning_edge_boost=1.5)
        now = datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert result["confidence_boost"] == pytest.approx(1.5)


# ── Rule 2: BTC SHORT Edge — DELETED (wave2b L5, FALLACY_AUDIT M1) ───

class TestBtcShortEdgeDeleted:
    """Rule 2 was a pure directional opinion (×1.15 on EVERY BTC SELL from an
    unversioned dirty-era '67% WR'). It must never fire — even when the
    config flag says enabled."""

    def test_btc_short_never_fires(self):
        """BTC SELL gets NO btc_short_edge boost (rule deleted)."""
        signal = MockSignal(symbol="BTC", side="SELL")
        config = MockConfig()  # quant_btc_short_edge_enabled=True — irrelevant
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)  # outside morning window
        result = apply_quant_rules(signal, config, now=now)
        assert "btc_short_edge" not in result["rules_applied"]
        assert result["confidence_boost"] == pytest.approx(1.0)
        assert "btc_short_boost" not in result["meta"]

    def test_btc_short_with_suffix_never_fires(self):
        """Suffixed BTC SELL also gets nothing."""
        signal = MockSignal(symbol="BTC/USDC:USDC", side="SELL")
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "btc_short_edge" not in result["rules_applied"]


# ── Rule 3: 12h Time Stop Tests ──────────────────────────────────────

class TestTimeStop:
    """Time stop should default to 12h (was 8h)."""

    def test_default_time_stop_is_2h(self):
        """Default time_stop_hours in config should be 2 (scalp approach)."""
        from trading_config import TradingConfig
        config = TradingConfig()
        assert config.time_stop_hours == 2

    def test_time_stop_env_override(self):
        """TIME_STOP_HOURS env var should still work."""
        import os
        old = os.environ.get("TIME_STOP_HOURS")
        try:
            os.environ["TIME_STOP_HOURS"] = "16"
            from trading_config import TradingConfig
            config = TradingConfig()
            assert config.time_stop_hours == 16
        finally:
            if old is None:
                os.environ.pop("TIME_STOP_HOURS", None)
            else:
                os.environ["TIME_STOP_HOURS"] = old


# ── Rule 4: HYPE BUY in High Vol Tests ──────────────────────────────

class TestHypeHighVolBuy:
    """HYPE BUY in high_volatility regime should get 1.2x boost."""

    def test_hype_buy_high_vol_boosted(self):
        """HYPE BUY in high_volatility gets 1.2x boost."""
        signal = MockSignal(symbol="HYPE", side="BUY", metadata={"regime": "high_volatility"})
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "hype_highvol_buy" in result["rules_applied"]
        assert result["confidence_boost"] == pytest.approx(1.2)

    def test_hype_sell_high_vol_not_boosted(self):
        """HYPE SELL in high vol should NOT get boost (only BUY)."""
        signal = MockSignal(symbol="HYPE", side="SELL", metadata={"regime": "high_volatility"})
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "hype_highvol_buy" not in result["rules_applied"]

    def test_hype_buy_trending_not_boosted(self):
        """HYPE BUY in non-high-vol regime should NOT get boost."""
        signal = MockSignal(symbol="HYPE", side="BUY", metadata={"regime": "trending_bull"})
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "hype_highvol_buy" not in result["rules_applied"]

    def test_btc_buy_high_vol_not_boosted(self):
        """BTC BUY in high vol should NOT get HYPE-specific boost."""
        signal = MockSignal(symbol="BTC", side="BUY", metadata={"regime": "high_volatility"})
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "hype_highvol_buy" not in result["rules_applied"]

    def test_hype_highvol_disabled(self):
        """When disabled, no HYPE high vol boost."""
        signal = MockSignal(symbol="HYPE", side="BUY", metadata={"regime": "high_volatility"})
        config = MockConfig(quant_hype_highvol_enabled=False)
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "hype_highvol_buy" not in result["rules_applied"]


# ── Rule 5: Conviction Multiplier Tests ──────────────────────────────

class TestConvictionMultiplier:
    """High confidence + multi-agree should get 1.3x risk multiplier."""

    def test_conviction_fires_on_high_conf_multi_agree(self):
        """Confidence >= 80 + 2+ agree = 1.3x risk_mult_boost."""
        signal = MockSignal(confidence=85.0)
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=3, now=now)
        assert "conviction_mult" in result["rules_applied"]
        assert result["risk_mult_boost"] == pytest.approx(1.3)

    def test_conviction_at_exact_threshold(self):
        """Confidence exactly 80 + exactly 2 agree = fires."""
        signal = MockSignal(confidence=80.0)
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=2, now=now)
        assert "conviction_mult" in result["rules_applied"]

    def test_conviction_below_confidence_threshold(self):
        """Confidence < 80 should NOT trigger conviction multiplier."""
        signal = MockSignal(confidence=79.0)
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=3, now=now)
        assert "conviction_mult" not in result["rules_applied"]
        assert result["risk_mult_boost"] == 1.0

    def test_conviction_single_strategy(self):
        """Only 1 strategy agreeing should NOT trigger conviction."""
        signal = MockSignal(confidence=90.0)
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=1, now=now)
        assert "conviction_mult" not in result["rules_applied"]

    def test_conviction_disabled(self):
        """When disabled, no conviction boost."""
        signal = MockSignal(confidence=90.0)
        config = MockConfig(quant_conviction_mult_enabled=False)
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=3, now=now)
        assert "conviction_mult" not in result["rules_applied"]

    def test_conviction_custom_thresholds(self):
        """Custom min_confidence and min_agree thresholds work."""
        signal = MockSignal(confidence=70.0)
        config = MockConfig(
            quant_conviction_min_confidence=65.0,
            quant_conviction_min_agree=3,
            quant_conviction_risk_mult=1.5,
        )
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=3, now=now)
        assert "conviction_mult" in result["rules_applied"]
        assert result["risk_mult_boost"] == pytest.approx(1.5)


# ── Stacking / Compound Tests ────────────────────────────────────────

class TestQuantRulesStacking:
    """Multiple rules can fire simultaneously and compound."""

    def test_morning_edge_only_for_btc_short(self):
        """BTC SHORT at 08:00 UTC gets ONLY morning edge (Rule 2 deleted)."""
        signal = MockSignal(symbol="BTC", side="SELL")
        config = MockConfig()
        now = datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "morning_edge" in result["rules_applied"]
        assert "btc_short_edge" not in result["rules_applied"]
        assert result["confidence_boost"] == pytest.approx(1.2)

    def test_morning_edge_plus_hype_highvol(self):
        """HYPE BUY in high vol at 10:00 UTC should get both boosts."""
        signal = MockSignal(symbol="HYPE", side="BUY", metadata={"regime": "high_volatility"})
        config = MockConfig()
        now = datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, now=now)
        assert "morning_edge" in result["rules_applied"]
        assert "hype_highvol_buy" in result["rules_applied"]
        assert result["confidence_boost"] == pytest.approx(1.2 * 1.2)

    def test_all_rules_disabled(self):
        """All rules disabled = no boosts."""
        signal = MockSignal(symbol="BTC", side="SELL", confidence=90.0,
                           metadata={"regime": "high_volatility"})
        config = MockConfig(
            quant_morning_edge_enabled=False,
            quant_btc_short_edge_enabled=False,
            quant_hype_highvol_enabled=False,
            quant_conviction_mult_enabled=False,
        )
        now = datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=3, now=now)
        assert result["rules_applied"] == []
        assert result["confidence_boost"] == 1.0
        assert result["risk_mult_boost"] == 1.0

    def test_no_rules_for_vanilla_signal(self):
        """A vanilla SOL BUY at 15:00 UTC with 60% confidence gets nothing."""
        signal = MockSignal(symbol="SOL", side="BUY", confidence=60.0,
                           metadata={"regime": "range"})
        config = MockConfig()
        now = datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=1, now=now)
        assert result["rules_applied"] == []
        assert result["confidence_boost"] == 1.0
        assert result["risk_mult_boost"] == 1.0

    def test_confidence_boost_plus_conviction_independent(self):
        """Confidence boost and conviction risk_mult boost are independent."""
        signal = MockSignal(symbol="BTC", side="SELL", confidence=85.0)
        config = MockConfig()
        now = datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc)  # morning
        result = apply_quant_rules(signal, config, num_strategies_agree=2, now=now)
        # Morning edge (1.2x conf) + conviction (1.3x risk); Rule 2 deleted
        assert result["confidence_boost"] == pytest.approx(1.2)
        assert result["risk_mult_boost"] == pytest.approx(1.3)
        assert len(result["rules_applied"]) == 2

    def test_metadata_returned_for_all_rules(self):
        """All fired rules should have metadata entries (Rule 2 deleted)."""
        signal = MockSignal(symbol="BTC", side="SELL", confidence=85.0)
        config = MockConfig()
        now = datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc)
        result = apply_quant_rules(signal, config, num_strategies_agree=2, now=now)
        meta = result["meta"]
        assert "morning_edge_hour" in meta
        assert "btc_short_boost" not in meta
        assert "conviction_risk_mult" in meta
