"""
End-to-end integration tests for PHASE A, B, and C.

Tests the full pipeline: data types -> validation -> gating -> memory -> triggers -> analytics.
"""

import os
import sys
import time
import json
import csv
import tempfile
from datetime import datetime, timezone

# Ensure bot/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── PHASE A: Safety Layer ──────────────────────────────────────────

def test_validator_schema():
    """Test strict schema validation."""
    from llm.decision_types import LLMDecision, StrategyWeights
    from llm.validator import validate_schema, validate_and_sanitize

    # Valid decision
    d = LLMDecision(
        action="proceed",
        confidence=0.75,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update="BTC breakout confirmed",
        notes="Strong momentum on all timeframes",
        size_multiplier=1.2,
        entry_adjustment="market now",
    )
    ok, err = validate_schema(d)
    assert ok, f"Valid decision failed schema: {err}"

    # Invalid action
    d2 = LLMDecision(
        action="buy",  # should be proceed/flat/flip
        confidence=0.75,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
    )
    ok, err = validate_schema(d2)
    assert not ok
    assert "action" in err.lower()

    # Out-of-range confidence
    d3 = LLMDecision(
        action="proceed",
        confidence=1.5,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
    )
    ok, err = validate_schema(d3)
    assert not ok
    assert "confidence" in err.lower()

    print("  [PASS] Schema validation")


def test_validator_semantics():
    """Test business logic validation."""
    from llm.decision_types import LLMDecision, StrategyWeights
    from llm.validator import validate_semantics

    # Flip with low confidence -> reject
    d = LLMDecision(
        action="flip",
        confidence=0.50,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
    )
    ok, err = validate_semantics(d)
    assert not ok
    assert "flip" in err.lower()

    # Panic regime requires high confidence
    d2 = LLMDecision(
        action="proceed",
        confidence=0.60,
        regime="panic",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
    )
    ok, err = validate_semantics(d2)
    assert not ok
    assert "panic" in err.lower()

    # Flat is always OK
    d3 = LLMDecision(
        action="flat",
        confidence=0.10,
        regime="unknown",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
    )
    ok, err = validate_semantics(d3)
    assert ok

    print("  [PASS] Semantic validation")


def test_recovery_pipeline():
    """Test error tracking and circuit breaker."""
    from llm.recovery import ErrorStats, handle_api_error, should_disable_llm_temporarily, reset_error_stats

    reset_error_stats()

    # Normal errors are recoverable
    ok, _, reason = handle_api_error("timeout", "timeout")
    assert ok

    ok, _, reason = handle_api_error("connection", "connection_error")
    assert ok

    # 3 consecutive -> circuit breaker
    ok, _, reason = handle_api_error("again", "timeout")
    assert not ok
    assert "circuit_breaker" in reason

    # Should disable LLM now
    assert should_disable_llm_temporarily()

    reset_error_stats()
    assert not should_disable_llm_temporarily()

    print("  [PASS] Recovery pipeline")


def test_normalizers():
    """Test input/output normalization."""
    from llm.normalizers import normalize_llm_output

    raw = {
        "action": "PROCEED",  # should normalize to lowercase
        "confidence": 1.5,    # should clamp to 1.0
        "regime": "TREND",
        "size_multiplier": 3.0,  # should clamp to 2.0
        "strategy_weights": {},
        "notes": "test",
        "memory_update": None,
    }
    normalized = normalize_llm_output(raw)
    assert normalized["action"] == "proceed"
    assert normalized["confidence"] == 1.0
    assert normalized["size_multiplier"] == 2.0
    assert normalized["regime"] == "trend"

    print("  [PASS] Normalizers")


def test_autonomy_router():
    """Test mode-specific routing."""
    from llm.autonomy_router import apply_autonomy_mode, can_llm_flip, can_llm_scale_size
    from llm.autonomy import LLMMode

    assert not can_llm_flip(LLMMode.OFF)
    assert not can_llm_flip(LLMMode.ADVISORY)
    assert not can_llm_flip(LLMMode.VETO_ONLY)
    assert not can_llm_flip(LLMMode.SIZING)
    assert can_llm_flip(LLMMode.DIRECTION)
    assert can_llm_flip(LLMMode.FULL)

    assert not can_llm_scale_size(LLMMode.OFF)
    assert not can_llm_scale_size(LLMMode.ADVISORY)
    assert not can_llm_scale_size(LLMMode.VETO_ONLY)
    assert can_llm_scale_size(LLMMode.SIZING)
    assert can_llm_scale_size(LLMMode.DIRECTION)
    assert can_llm_scale_size(LLMMode.FULL)

    print("  [PASS] Autonomy router")


# ── PHASE B: State Management ──────────────────────────────────────

def test_memory_store():
    """Test structured memory with pruning."""
    from llm.memory_store import (
        load_memory, apply_memory_update, get_memory_summary,
        get_symbol_patterns, get_memory_stats, clear_memory,
        _prune_stale, _dedup, _cap_size,
    )

    clear_memory()

    # Add notes (must pass quality gate: >=20 chars, mention symbol or have structure)
    apply_memory_update("BTC breakout at 65k, strong volume confirmation", symbol="BTC", regime="trending")
    apply_memory_update("SOL failing at resistance with declining volume", symbol="SOL", regime="ranging")
    apply_memory_update("ETH following BTC lead, correlated move", symbol="ETH")

    # Summary should include all 3
    summary = get_memory_summary()
    assert summary is not None
    assert "BTC" in summary
    assert "SOL" in summary

    # Symbol patterns
    btc_patterns = get_symbol_patterns("BTC")
    assert len(btc_patterns) >= 1
    assert any("breakout" in p.lower() for p in btc_patterns)

    # Stats
    stats = get_memory_stats()
    assert stats["total_notes"] == 3
    assert "BTC" in stats["symbol_counts"]

    # Dedup: same note should not be duplicated
    apply_memory_update("BTC breakout at 65k, strong volume confirmation", symbol="BTC")
    stats2 = get_memory_stats()
    assert stats2["total_notes"] == 3  # Should NOT increase

    # Staleness pruning (mock old notes — must be older than 7 days retention)
    old_notes = [{"text": "old note", "ts": time.time() - 700000, "symbol": "", "regime": ""}]
    pruned = _prune_stale(old_notes)
    assert len(pruned) == 0

    # Cap size (max notes is 100)
    many_notes = [{"text": f"note {i}", "ts": time.time(), "symbol": "", "regime": ""} for i in range(120)]
    capped = _cap_size(many_notes)
    assert len(capped) == 100

    clear_memory()
    print("  [PASS] Memory store")


def test_trigger_suppression():
    """Test trigger prioritization and suppression."""
    from llm.triggers import TriggerAccumulator, LLMTrigger

    acc = TriggerAccumulator()

    # Add a low-priority periodic
    acc.add(LLMTrigger.PERIODIC)
    assert acc.event_count == 1

    # Add a high-priority pre-trade
    acc.add(LLMTrigger.PRE_TRADE, symbol="BTC", context="test")
    assert acc.event_count == 2

    # Suppression should remove periodic when higher-priority exists
    acc.suppress_low_value()
    assert acc.event_count == 1

    # Best should be PRE_TRADE
    trigger, ctx, reasons = acc.get_best()
    assert trigger == LLMTrigger.PRE_TRADE

    acc.clear()
    print("  [PASS] Trigger suppression")


def test_reconciliation():
    """Test position reconciliation from exchange data."""
    from execution.reconciliation import reconcile_positions, _reconcile_one, _PAIR_TO_SYMBOL
    from execution.position_manager import PositionManager

    # Verify pair mapping covers all 18 symbols
    from trading_config import DEFAULT_SYMBOLS
    mapped_symbols = set(_PAIR_TO_SYMBOL.values())
    for sym in DEFAULT_SYMBOLS:
        assert sym in mapped_symbols, f"Symbol {sym} not in pair mapping"

    # Test reconcile with mock positions
    pm = PositionManager()
    mock_positions = [
        {
            "symbol": "SOL/USDC:USDC",
            "side": "long",
            "contracts": 10.0,
            "entryPrice": 150.0,
            "leverage": 5,
            "unrealizedPnl": 25.0,
        },
        {
            "symbol": "ETH/USDC:USDC",
            "side": "short",
            "contracts": 0.5,
            "entryPrice": 3000.0,
            "leverage": 3,
            "unrealizedPnl": -10.0,
        },
        {
            "symbol": "UNKNOWN/USDC:USDC",  # Should be skipped
            "side": "long",
            "contracts": 1.0,
            "entryPrice": 100.0,
            "leverage": 1,
            "unrealizedPnl": 0,
        },
    ]

    for raw in mock_positions:
        _reconcile_one(raw, pm, {"SOL": 152.5, "ETH": 3010.0})

    assert "SOL" in pm.positions
    assert "ETH" in pm.positions
    assert pm.positions["SOL"].side == "LONG"
    assert pm.positions["ETH"].side == "SHORT"
    assert pm.positions["SOL"].leverage == 5

    print("  [PASS] Position reconciliation")


# ── PHASE C: Final Integration ──────────────────────────────────────

def test_time_sizing():
    """Time-of-day/day-of-week multipliers — NEUTRALIZED by default (D11).

    wave2b L5 / FALLACY_AUDIT D11: the hour/day edges came from one April-2026
    week and were never re-scored, so the default is 1.0x (shadow). The raw
    would-have arithmetic is preserved behind TIME_SIZING_ENFORCE=true.
    """
    import os
    from execution.time_sizing import get_time_multiplier, get_time_sizing_info, is_weekend

    # ── Default (shadow): every hour/day returns 1.0 ──
    mon = datetime(2025, 1, 6, 12, 0, tzinfo=timezone.utc)
    mon_3pm = datetime(2025, 1, 6, 15, 0, tzinfo=timezone.utc)
    thu_dead = datetime(2025, 1, 9, 10, 0, tzinfo=timezone.utc)
    assert get_time_multiplier(mon) == 1.0
    assert get_time_multiplier(mon_3pm) == 1.0
    assert get_time_multiplier(thu_dead) == 1.0

    # ── Enforced (kill-switch): the pre-D11 arithmetic is intact ──
    _old = os.environ.get("TIME_SIZING_ENFORCE")
    os.environ["TIME_SIZING_ENFORCE"] = "true"
    try:
        # Monday 12pm UTC -> 1.15 (Mon) * 1.0 (GOOD hour) = 1.15
        assert abs(get_time_multiplier(mon) - 1.15) < 0.001

        # Monday 15pm UTC -> 1.15 (Mon) * 1.2 (PRIME hour) = 1.38
        assert abs(get_time_multiplier(mon_3pm) - 1.38) < 0.001

        # Saturday 3am UTC -> 0.8 (Sat) * 0.7 (QUIET hour) = 0.56
        sat = datetime(2025, 1, 4, 3, 0, tzinfo=timezone.utc)
        assert abs(get_time_multiplier(sat) - 0.56) < 0.001

        # Sunday 15pm UTC -> 0.8 (Sun) * 1.2 (PRIME hour) = 0.96
        sun = datetime(2025, 1, 5, 15, 0, tzinfo=timezone.utc)
        assert abs(get_time_multiplier(sun) - 0.96) < 0.001

        # Tuesday 0am UTC -> 1.0 (Tue) * 1.0 (GOOD hour) = 1.0
        tue_midnight = datetime(2025, 1, 7, 0, 0, tzinfo=timezone.utc)
        assert abs(get_time_multiplier(tue_midnight) - 1.0) < 0.001

        # Tuesday 5am UTC -> 1.0 (Tue) * 0.7 (QUIET hour) = 0.7
        tue_dead = datetime(2025, 1, 7, 5, 0, tzinfo=timezone.utc)
        assert abs(get_time_multiplier(tue_dead) - 0.7) < 0.001

        # Thursday 10am UTC -> 0.85 (Thu) * 0.5 (DEAD hour) = 0.425
        assert abs(get_time_multiplier(thu_dead) - 0.425) < 0.001
    finally:
        if _old is None:
            os.environ.pop("TIME_SIZING_ENFORCE", None)
        else:
            os.environ["TIME_SIZING_ENFORCE"] = _old

    # ── Bias labels remain raw context regardless of enforcement ──
    # Directional bias: 18:00 UTC = neutral (no longer in _HOUR_BIAS)
    info_18 = get_time_sizing_info(datetime(2025, 1, 6, 18, 0, tzinfo=timezone.utc))
    assert info_18["bias"] == "neutral"

    # Directional bias: 14:00 UTC = long (data-driven update)
    info_14 = get_time_sizing_info(datetime(2025, 1, 6, 14, 0, tzinfo=timezone.utc))
    assert info_14["bias"] == "long"

    # Directional bias: 12:00 UTC = neutral
    info_12 = get_time_sizing_info(datetime(2025, 1, 6, 12, 0, tzinfo=timezone.utc))
    assert info_12["bias"] == "neutral"

    print("  [PASS] Time sizing (neutralized default + enforced arithmetic)")


def test_uplift_analytics():
    """Test uplift computation with synthetic data."""
    from llm.uplift_analytics import (
        _compute_stats, _compute_veto_accuracy, _compute_uplift_delta,
        format_uplift_report,
    )

    # Baseline: some wins, some losses
    baseline_candidates = [
        {"realized_pnl": 100.0, "realized_r": 2.0, "llm_action": "proceed"},
        {"realized_pnl": -50.0, "realized_r": -1.0, "llm_action": "proceed"},
        {"realized_pnl": 30.0, "realized_r": 0.5, "llm_action": "proceed"},
        {"realized_pnl": -40.0, "realized_r": -0.8, "llm_action": "flat"},  # vetoed
        {"realized_pnl": -25.0, "realized_r": -0.5, "llm_action": "flat"},  # vetoed
    ]

    baseline = _compute_stats(baseline_candidates, "baseline")
    assert baseline["count"] == 5
    assert baseline["win_rate"] == 2 / 5

    # LLM-filtered (only proceeded)
    proceeded = [c for c in baseline_candidates if c["llm_action"] == "proceed"]
    llm_filtered = _compute_stats(proceeded, "llm_filtered")
    assert llm_filtered["count"] == 3
    assert llm_filtered["win_rate"] == 2 / 3  # Better win rate!

    # Veto accuracy
    vetoed = [c for c in baseline_candidates if c["llm_action"] == "flat"]
    va = _compute_veto_accuracy(vetoed)
    assert va["accuracy"] == 1.0  # Both vetoed trades were losses
    assert va["saved_pnl"] == 65.0  # 40 + 25

    # Uplift delta
    uplift = _compute_uplift_delta(baseline, llm_filtered)
    assert uplift["has_data"]
    assert uplift["is_positive"]
    assert uplift["win_rate_delta"] > 0

    # Format report
    report = format_uplift_report({
        "total_candidates": 5,
        "with_outcome": 5,
        "baseline": baseline,
        "llm_filtered": llm_filtered,
        "veto_accuracy": va,
        "uplift": uplift,
    })
    assert "UPLIFT" in report
    assert "POSITIVE" in report

    print("  [PASS] Uplift analytics")


def test_mode_constraints():
    """Test LLM mode constraint enforcement in decision engine."""
    from llm.decision_types import LLMDecision, StrategyWeights
    from llm.decision_engine import _apply_mode_constraints
    from llm.autonomy import LLMMode

    # VETO_ONLY: flip -> flat, size_mult -> 1.0, entry_adj -> None
    d = LLMDecision(
        action="flip",
        confidence=0.85,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
        size_multiplier=1.5,
        entry_adjustment="wait for pullback",
    )
    d2, overrides = _apply_mode_constraints(d, LLMMode.VETO_ONLY)
    assert d2.action == "flat"
    assert d2.size_multiplier == 1.0
    assert d2.entry_adjustment is None
    assert "flip_to_flat" in overrides

    # SIZING: flip -> flat, but keep size_multiplier
    d3 = LLMDecision(
        action="flip",
        confidence=0.85,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
        size_multiplier=1.5,
        entry_adjustment="scale in",
    )
    d4, overrides2 = _apply_mode_constraints(d3, LLMMode.SIZING)
    assert d4.action == "flat"
    assert d4.size_multiplier == 1.5  # Kept!
    assert d4.entry_adjustment is None

    # DIRECTION: no constraints on action
    d5 = LLMDecision(
        action="flip",
        confidence=0.85,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
        size_multiplier=1.5,
        entry_adjustment="wait for pullback",
    )
    d6, overrides3 = _apply_mode_constraints(d5, LLMMode.DIRECTION)
    assert d6.action == "flip"  # Allowed!
    assert len(overrides3) == 0

    print("  [PASS] Mode constraints")


def test_snapshot_compaction():
    """Test token-efficient snapshot serialization."""
    from llm.snapshot_builder import snapshot_to_json, build_snapshot
    from llm.decision_types import (
        StrategySignal, MarketSnapshot, GlobalContext, LLMInputSnapshot,
    )

    markets = [
        MarketSnapshot(
            symbol="BTC",
            price=65000.0,
            price_change_1h_pct=1.5,
            price_change_24h_pct=5.2,
            volume_ratio=1.8,
            volatility=2.1,
            signals=[
                StrategySignal(
                    symbol="BTC",
                    strategy="regime_trend",
                    side="long",
                    confidence=0.85,
                    regime_score=0.7,
                ),
            ],
        ),
    ]
    global_ctx = GlobalContext(
        timestamp=int(time.time() * 1000),
        btc_price=65000.0,
        btc_change_1h_pct=1.5,
        btc_change_24h_pct=5.2,
        eth_btc_ratio=0.045,
        total_open_positions=2,
        daily_pnl=-50.0,
        equity=10000.0,
    )

    snapshot = build_snapshot(markets, global_ctx, memory_summary="BTC trending up")
    compact_json = snapshot_to_json(snapshot, compact=True)

    # Should use short keys
    parsed = json.loads(compact_json)
    assert "m" in parsed  # markets
    assert "g" in parsed  # global
    assert "mem" in parsed  # memory
    assert parsed["m"][0]["s"] == "BTC"
    assert "d1h" in parsed["m"][0]

    # Compact core data (excluding enrichment context) should be shorter than verbose
    parsed_compact = json.loads(compact_json)
    # Remove enrichment fields only present in compact mode
    for key in ("growth", "survival", "knowledge", "trade_dna", "deep_memory", "rules"):
        parsed_compact.pop(key, None)
    compact_core = json.dumps(parsed_compact, separators=(",", ":"))
    verbose_json = snapshot_to_json(snapshot, compact=False)
    assert len(compact_core) < len(verbose_json)

    print("  [PASS] Snapshot compaction")


def test_full_import_chain():
    """Test that all modules can be imported without circular dependencies."""
    # Core types
    from llm.decision_types import LLMDecision, MarketSnapshot, GlobalContext, Regime
    from llm.decision_types import StrategySignal, StrategyWeights, LLMInputSnapshot

    # Safety layer
    from llm.validator import validate_schema, validate_semantics, validate_and_sanitize
    from llm.recovery import ErrorStats, handle_api_error, should_disable_llm_temporarily
    from llm.normalizers import normalize_llm_output, decision_from_normalized_dict
    from llm.autonomy_router import apply_autonomy_mode, can_llm_flip

    # State management
    from llm.memory_store import load_memory, apply_memory_update, get_memory_summary
    from llm.triggers import TriggerAccumulator, LLMTrigger
    from execution.reconciliation import reconcile_positions

    # Integration
    from execution.time_sizing import get_time_multiplier
    from llm.uplift_analytics import compute_uplift, format_uplift_report
    from execution.candidate import TradeCandidate, CandidateLogger

    # Engine (depends on everything above)
    from llm.decision_engine import DecisionResult, get_trading_decision
    from llm.snapshot_builder import build_snapshot, snapshot_to_json
    from llm.autonomy import LLMMode, get_llm_mode, should_call_llm, llm_has_veto

    print("  [PASS] Full import chain (no circular deps)")


def test_risk_gating_new_actions():
    """Test risk gating with proceed/flat/flip actions."""
    from llm.decision_types import LLMDecision, StrategyWeights
    from llm.risk_gating import gate_decision, RiskContext

    ctx = RiskContext(
        daily_pnl=-100.0,
        max_daily_loss=500.0,
        equity=10000.0,
        max_leverage=25.0,
        current_leverage=5.0,
        volatility=3.0,
        max_volatility=15.0,
        open_positions=2,
        max_positions=6,
        circuit_breaker_active=False,
        consecutive_losses=1,
    )

    # proceed should pass
    d = LLMDecision(
        action="proceed",
        confidence=0.80,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
    )
    result = gate_decision(d, ctx)
    assert result.allowed

    # flip with low confidence should fail
    d2 = LLMDecision(
        action="flip",
        confidence=0.50,
        regime="trend",
        strategy_weights=StrategyWeights(),
        memory_update=None,
        notes="test",
    )
    result2 = gate_decision(d2, ctx)
    assert not result2.allowed

    print("  [PASS] Risk gating with new actions")


# ── Run all tests ──────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("END-TO-END INTEGRATION TESTS")
    print("=" * 60)

    print("\n--- PHASE A: Safety Layer ---")
    test_validator_schema()
    test_validator_semantics()
    test_recovery_pipeline()
    test_normalizers()
    test_autonomy_router()

    print("\n--- PHASE B: State Management ---")
    test_memory_store()
    test_trigger_suppression()
    test_reconciliation()

    print("\n--- PHASE C: Final Integration ---")
    test_time_sizing()
    test_uplift_analytics()
    test_mode_constraints()
    test_snapshot_compaction()
    test_risk_gating_new_actions()

    print("\n--- Cross-Module ---")
    test_full_import_chain()

    print("\n" + "=" * 60)
    print("ALL 14 TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
