"""
Comprehensive tests for the Multi-Agent LLM system.

Tests:
  1. Agent base types and configs
  2. Coordinator pipeline (mock LLM calls)
  3. Agent output merging logic
  4. Learning integration pipeline
  5. Environment configuration
  6. Decision engine multi-agent routing
  7. Prompt registry completeness
  8. Graceful degradation (agent failures)
  9. Knowledge/context passing to agents
"""

import json
import os
from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# 1. Agent Base Types
# ---------------------------------------------------------------------------

class TestAgentBaseTypes:
    """Test agent base types and configurations."""

    def test_agent_roles_all_defined(self):
        from llm.agents.base import AgentRole
        roles = list(AgentRole)
        assert len(roles) == 23  # All specialist agents (includes OVERRIDE)
        assert AgentRole.OVERRIDE in roles
        assert AgentRole.REGIME in roles
        assert AgentRole.TRADE in roles
        assert AgentRole.RISK in roles
        assert AgentRole.LEARNING in roles
        assert AgentRole.CRITIC in roles
        assert AgentRole.EXIT in roles
        assert AgentRole.SCOUT in roles

    def test_agent_output_ok_property(self):
        from llm.agents.base import AgentOutput, AgentRole
        # Successful output
        out = AgentOutput(role=AgentRole.REGIME, data={"rg": "trend"})
        assert out.ok is True
        # Failed output
        out_err = AgentOutput(role=AgentRole.REGIME, data={}, error="api_error")
        assert out_err.ok is False
        # Empty data without error
        out_empty = AgentOutput(role=AgentRole.REGIME, data={})
        assert out_empty.ok is False

    def test_default_configs_cover_all_roles(self):
        from llm.agents.base import DEFAULT_AGENT_CONFIGS, AgentRole
        for role in AgentRole:
            assert role in DEFAULT_AGENT_CONFIGS, f"Missing config for {role}"

    def test_regime_and_trade_are_required(self):
        from llm.agents.base import DEFAULT_AGENT_CONFIGS, AgentRole
        assert DEFAULT_AGENT_CONFIGS[AgentRole.REGIME].required is True
        assert DEFAULT_AGENT_CONFIGS[AgentRole.TRADE].required is True
        assert DEFAULT_AGENT_CONFIGS[AgentRole.RISK].required is False
        assert DEFAULT_AGENT_CONFIGS[AgentRole.CRITIC].required is False

    def test_agent_config_max_tokens(self):
        from llm.agents.base import DEFAULT_AGENT_CONFIGS, AgentRole
        # Core agents must be sized to hold their full JSON schema without
        # truncation. Forensic evidence (2026-04-14) showed Regime@512 and
        # Trade@800 both truncated on rich enriched-context calls, and Trade
        # @1400 also truncated on LLM-first 9691-input calls. Caps below are
        # real-world minimums, not theoretical lower bounds.
        assert DEFAULT_AGENT_CONFIGS[AgentRole.REGIME].max_tokens >= 800
        assert DEFAULT_AGENT_CONFIGS[AgentRole.TRADE].max_tokens >= 800
        # Upper bound prevents runaway outputs (cost protection).
        assert DEFAULT_AGENT_CONFIGS[AgentRole.REGIME].max_tokens <= 3000
        assert DEFAULT_AGENT_CONFIGS[AgentRole.TRADE].max_tokens <= 3000


# ---------------------------------------------------------------------------
# 2. Prompt Registry
# ---------------------------------------------------------------------------

class TestAgentPrompts:
    """Test that all agent prompts are defined and contain key instructions."""

    def test_all_roles_have_prompts(self):
        from llm.agents.prompts import AGENT_PROMPTS
        assert "regime" in AGENT_PROMPTS
        assert "trade" in AGENT_PROMPTS
        assert "risk" in AGENT_PROMPTS
        assert "learning" in AGENT_PROMPTS
        assert "critic" in AGENT_PROMPTS

    def test_regime_prompt_has_all_regimes(self):
        from llm.agents.prompts import REGIME_AGENT_PROMPT
        for regime in ("trend", "range", "panic", "high_volatility",
                        "low_liquidity", "news_dislocation", "unknown"):
            assert regime in REGIME_AGENT_PROMPT

    def test_trade_prompt_has_knowledge_references(self):
        """Trade agent prompt must reference the knowledge/learning context."""
        from llm.agents.prompts import TRADE_AGENT_PROMPT
        assert "knowledge" in TRADE_AGENT_PROMPT
        assert "deep_memory" in TRADE_AGENT_PROMPT
        assert "recent_lessons" in TRADE_AGENT_PROMPT
        assert "self_perf" in TRADE_AGENT_PROMPT
        assert "autopsy" in TRADE_AGENT_PROMPT
        assert "growth" in TRADE_AGENT_PROMPT
        assert "examples" in TRADE_AGENT_PROMPT

    def test_trade_prompt_has_signal_evaluation_section(self):
        """Trade agent must have signal evaluation guidance."""
        from llm.agents.prompts import TRADE_AGENT_PROMPT
        assert "SIGNAL EVALUATION" in TRADE_AGENT_PROMPT
        assert "rf" in TRADE_AGENT_PROMPT  # Signal quality flags
        assert "confluence" in TRADE_AGENT_PROMPT

    def test_trade_prompt_has_signal_evaluation(self):
        """Trade agent must have signal evaluation guidance."""
        from llm.agents.prompts import TRADE_AGENT_PROMPT
        assert "SIGNAL EVALUATION" in TRADE_AGENT_PROMPT
        assert "rf" in TRADE_AGENT_PROMPT  # Signal quality flags

    def test_trade_prompt_has_funding_awareness(self):
        """Trade agent must understand funding is a cost."""
        from llm.agents.prompts import TRADE_AGENT_PROMPT
        assert "funding" in TRADE_AGENT_PROMPT.lower()
        assert "Funding" in TRADE_AGENT_PROMPT

    def test_learning_prompt_has_hypothesis_generation(self):
        """Learning agent must generate testable hypotheses."""
        from llm.agents.prompts import LEARNING_AGENT_PROMPT
        assert "hypothesis" in LEARNING_AGENT_PROMPT
        assert "testable" in LEARNING_AGENT_PROMPT.lower()

    def test_learning_prompt_has_quality_framework(self):
        """Learning agent must have lesson quality assessment."""
        from llm.agents.prompts import LEARNING_AGENT_PROMPT
        assert "WHAT happened" in LEARNING_AGENT_PROMPT
        assert "WHY" in LEARNING_AGENT_PROMPT
        assert "WHAT TO DO NEXT TIME" in LEARNING_AGENT_PROMPT

    def test_critic_prompt_has_review_checklist(self):
        from llm.agents.prompts import CRITIC_AGENT_PROMPT
        assert "REVIEW CHECKLIST" in CRITIC_AGENT_PROMPT
        assert "calibration" in CRITIC_AGENT_PROMPT.lower()


# ---------------------------------------------------------------------------
# 3. Coordinator: Output Merging
# ---------------------------------------------------------------------------

class TestCoordinatorMerging:
    """Test the output merging logic."""

    def _make_coordinator(self):
        from llm.agents.coordinator import AgentCoordinator
        return AgentCoordinator()

    def _make_output(self, role, data):
        from llm.agents.base import AgentOutput
        return AgentOutput(role=role, data=data)

    def test_basic_merge_go(self):
        from llm.agents.base import AgentRole
        coord = self._make_coordinator()

        regime_out = self._make_output(AgentRole.REGIME, {
            "rg": "trend", "conf": 0.85, "bias": "bullish", "transition": "stable"
        })
        trade_out = self._make_output(AgentRole.TRADE, {
            "a": "go", "c": 0.78, "n": "strong trend alignment",
            "mu": "BTC leading SOL up", "ea": "market now"
        })

        decision = coord._merge_outputs(regime_out, trade_out, None, None)

        assert decision.action == "proceed"
        # Confidence may be calibrated down from 0.78 by ConfidenceCalibrator
        assert 0.5 <= decision.confidence <= 0.80
        assert decision.regime == "trend"
        assert "market now" in (decision.entry_adjustment or "market now")
        assert decision.memory_update is not None

    def test_merge_with_risk_override_skip(self):
        from llm.agents.base import AgentRole
        coord = self._make_coordinator()

        regime_out = self._make_output(AgentRole.REGIME, {"rg": "trend", "conf": 0.8,
                                                           "bias": "neutral", "transition": "stable"})
        trade_out = self._make_output(AgentRole.TRADE, {"a": "go", "c": 0.7, "n": "ok"})
        risk_out = self._make_output(AgentRole.RISK, {
            "sz": 0.0, "override": "skip", "risks": ["portfolio_leverage_too_high"]
        })

        decision = coord._merge_outputs(regime_out, trade_out, risk_out, None)
        assert decision.action == "flat"
        assert "RISK" in decision.notes

    def test_merge_with_critic_challenge_shadow_default(self):
        """DECHOKE 6 (2026-07-02): Critic is SHADOW by default — a challenge
        is logged as would_veto in notes but does NOT block or mutate."""
        import os
        from llm.agents.base import AgentRole
        coord = self._make_coordinator()

        regime_out = self._make_output(AgentRole.REGIME, {"rg": "range", "conf": 0.6,
                                                           "bias": "neutral", "transition": "stable"})
        trade_out = self._make_output(AgentRole.TRADE, {"a": "go", "c": 0.85, "n": "looks good"})
        critic_out = self._make_output(AgentRole.CRITIC, {
            "verdict": "challenge",
            "adjusted_confidence": 0.55,
            "adjusted_action": "skip",
            "reason": "overconfident in range regime",
            "calibration_note": "tend to be overconfident in range"
        })

        os.environ.pop("CRITIC_ENFORCE", None)
        decision = coord._merge_outputs(regime_out, trade_out, None, critic_out)
        assert decision.action == "proceed"  # shadow: no block
        assert "CRITIC-SHADOW" in decision.notes
        assert "would_challenge" in decision.notes

    def test_merge_with_critic_challenge_enforce_env(self):
        """CRITIC_ENFORCE=true restores the old enforcement path."""
        import os
        from llm.agents.base import AgentRole
        coord = self._make_coordinator()

        regime_out = self._make_output(AgentRole.REGIME, {"rg": "range", "conf": 0.6,
                                                           "bias": "neutral", "transition": "stable"})
        trade_out = self._make_output(AgentRole.TRADE, {"a": "go", "c": 0.85, "n": "looks good"})
        critic_out = self._make_output(AgentRole.CRITIC, {
            "verdict": "challenge",
            "adjusted_confidence": 0.55,
            "adjusted_action": "skip",
            "reason": "overconfident in range regime",
            "counter_thesis": "range top rejection",
            "counter_thesis_timeframe": "4h",
            "counter_thesis_falsifiable": "breaks above 105",
            "calibration_note": "tend to be overconfident in range"
        })

        os.environ["CRITIC_ENFORCE"] = "true"
        try:
            decision = coord._merge_outputs(regime_out, trade_out, None, critic_out)
        finally:
            os.environ.pop("CRITIC_ENFORCE", None)
        assert decision.action == "flat"  # Critic overrode to skip
        assert decision.confidence == 0.55
        assert "CRITIC" in decision.notes

    def test_merge_with_strategy_weights_from_risk(self):
        from llm.agents.base import AgentRole
        # Reset scratchpad to prevent leakage from prior tests
        from llm.agents.shared_context import reset_pipeline_scratchpad
        reset_pipeline_scratchpad()

        coord = self._make_coordinator()

        regime_out = self._make_output(AgentRole.REGIME, {"rg": "trend", "conf": 0.9,
                                                           "bias": "bullish", "transition": "stable"})
        trade_out = self._make_output(AgentRole.TRADE, {"a": "go", "c": 0.8, "n": "ok"})
        risk_out = self._make_output(AgentRole.RISK, {
            "sz": 1.5,
            "sw": {"rt": 0.9, "mc": 0.7, "cs": 0.3, "mq": 0.5},
            "risks": [],
        })

        decision = coord._merge_outputs(regime_out, trade_out, risk_out, None)
        assert decision.size_multiplier == 1.5
        assert decision.strategy_weights.regime_trend == 0.9
        assert decision.strategy_weights.monte_carlo_zones == 0.7

    def test_merge_action_normalization(self):
        """'go' → 'proceed', 'skip' → 'flat'."""
        from llm.agents.base import AgentRole
        coord = self._make_coordinator()

        regime_out = self._make_output(AgentRole.REGIME, {"rg": "unknown", "conf": 0.3,
                                                           "bias": "neutral", "transition": "uncertain"})
        trade_out = self._make_output(AgentRole.TRADE, {"a": "skip", "c": 0.2, "n": "no edge"})

        decision = coord._merge_outputs(regime_out, trade_out, None, None)
        assert decision.action == "flat"  # "skip" normalized to "flat"

    def test_merge_clamps_size_multiplier(self):
        from llm.agents.base import AgentRole
        coord = self._make_coordinator()

        regime_out = self._make_output(AgentRole.REGIME, {"rg": "trend", "conf": 0.9,
                                                           "bias": "bullish", "transition": "stable"})
        trade_out = self._make_output(AgentRole.TRADE, {"a": "go", "c": 0.9, "n": "ok"})
        # Risk agent tries to set size > 2.0
        risk_out = self._make_output(AgentRole.RISK, {"sz": 5.0, "risks": []})

        decision = coord._merge_outputs(regime_out, trade_out, risk_out, None)
        assert decision.size_multiplier == 2.0  # Clamped


# ---------------------------------------------------------------------------
# 4. Coordinator: Pipeline with Mocked LLM
# ---------------------------------------------------------------------------

class TestCoordinatorPipeline:
    """Test full pipeline with mocked LLM calls."""

    def _mock_call_llm(self, responses):
        """Create a mock call_llm that returns different responses in sequence."""
        call_count = [0]
        def mock_fn(**kwargs):
            idx = min(call_count[0], len(responses) - 1)
            call_count[0] += 1
            resp = responses[idx]
            return json.dumps(resp), {"input_tokens": 100, "output_tokens": 50}
        return mock_fn

    def test_full_pipeline_success(self):
        from llm.agents.coordinator import AgentCoordinator
        from llm.agents.base import AgentRole, AgentConfig

        # Mock LLM responses for each agent
        responses = [
            # Regime Agent
            {"rg": "trend", "conf": 0.85, "factors": "strong vol + OI",
             "bias": "bullish", "transition": "stable"},
            # Quant Agent
            {"ev": {"direction": "long", "magnitude": 2.5, "confidence": 0.75},
             "conditional_edge": {"base_wr": 55, "conditional_wr": 72, "n_similar": 20, "edge_pct": 17},
             "probability": {"up_4h": 0.65, "down_4h": 0.20, "sideways_4h": 0.15},
             "risk_profile": {"fat_tail_risk": "low", "max_adverse_move_pct": 1.8, "funding_drag_pct": 0.1},
             "kelly_fraction": 0.25,
             "signal_quality": {"noise_probability": 0.0, "confidence_adjustment": 0, "reason": "solid edge"},
             "n": "convergent setup with volume confirmation"},
            # Trade Agent
            {"a": "go", "c": 0.78, "n": "trend aligns", "mu": "BTC leads", "ea": None},
            # Risk Agent
            {"sz": 1.3, "sw": {"rt": 0.9, "mc": 0.6}, "risks": [], "override": None},
            # Critic Agent
            {"verdict": "approve", "reason": "all checks pass",
             "adjusted_confidence": None, "adjusted_action": None, "calibration_note": None},
        ]

        with patch("llm.agents.coordinator.call_llm", side_effect=self._mock_call_llm(responses)):
            coord = AgentCoordinator()
            decision = coord.get_trading_decision(
                snapshot_data={"m": [], "g": {"btc": 95000}},
                trigger_reason="pre_trade_veto",
            )

        assert decision is not None
        assert decision.action == "proceed"
        assert 0.5 <= decision.confidence <= 0.80  # Calibration may adjust from 0.78
        assert decision.regime == "trend"
        # DECHOKE 5 (2026-07-02, FALLACY_AUDIT D8): Quant Kelly modulation is
        # SHADOW-ONLY by default — size stays at Risk Agent's sz=1.3.
        # (With QUANT_AGENT_ENFORCE=true it would be 1.3 × 1.5 = 1.95.)
        assert abs(decision.size_multiplier - 1.3) < 0.01

    def test_pipeline_regime_failure_aborts(self):
        """If regime agent fails and is required, pipeline should return None."""
        from llm.agents.coordinator import AgentCoordinator

        def mock_fail(**kwargs):
            return None, {"error": "timeout"}

        with patch("llm.agents.coordinator.call_llm", side_effect=mock_fail):
            coord = AgentCoordinator()
            decision = coord.get_trading_decision(
                snapshot_data={"m": [], "g": {}},
            )

        assert decision is None

    def test_pipeline_optional_agent_failure_degrades(self):
        """If risk/critic agents fail, pipeline should still produce a decision."""
        from llm.agents.coordinator import AgentCoordinator

        call_idx = [0]
        def mock_fn(**kwargs):
            call_idx[0] += 1
            if call_idx[0] <= 3:
                # Regime + Quant + Trade succeed
                if call_idx[0] == 1:
                    return json.dumps({"rg": "range", "conf": 0.6, "bias": "neutral",
                                       "factors": "choppy", "transition": "stable"}), {"input_tokens": 50}
                if call_idx[0] == 2:
                    # Quant Agent
                    return json.dumps({"ev": {"direction": "neutral", "magnitude": 0.5, "confidence": 0.4},
                                       "signal_quality": {"noise_probability": 0.1, "confidence_adjustment": 0},
                                       "kelly_fraction": 0.05, "n": "weak"}), {"input_tokens": 50}
                return json.dumps({"a": "skip", "c": 0.3, "n": "weak range"}), {"input_tokens": 50}
            # Risk + Critic fail
            return None, {"error": "api_error"}

        with patch("llm.agents.coordinator.call_llm", side_effect=mock_fn):
            coord = AgentCoordinator()
            decision = coord.get_trading_decision(
                snapshot_data={"m": [], "g": {}},
            )

        assert decision is not None
        assert decision.action == "flat"

    def test_learning_agent_call(self):
        """Test post-trade learning agent."""
        from llm.agents.coordinator import AgentCoordinator

        lesson_response = {
            "lesson": "SOL LONG SL hit in 3min in range—wait for pullback",
            "category": "entry_timing",
            "strength": "moderate",
            "applies_to": {"symbol": "SOL", "regime": "range", "side": "LONG"},
            "hypothesis": "SOL longs in range regime have <35% WR",
        }

        def mock_fn(**kwargs):
            return json.dumps(lesson_response), {"input_tokens": 80, "output_tokens": 40}

        with patch("llm.agents.coordinator.call_llm", side_effect=mock_fn):
            coord = AgentCoordinator()
            result = coord.get_post_trade_lesson({
                "symbol": "SOL", "side": "LONG", "outcome": "LOSS",
                "pnl": -5.0, "regime": "range", "hold_time_s": 180,
            })

        assert result is not None
        assert result["category"] == "entry_timing"
        assert result["hypothesis"] is not None


# ---------------------------------------------------------------------------
# 5. Environment Configuration
# ---------------------------------------------------------------------------

class TestMultiAgentConfig:
    """Test environment-driven configuration."""

    def test_multi_agent_disabled_by_default(self):
        from llm.agents.coordinator import is_multi_agent_enabled
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("LLM_MULTI_AGENT", None)
            with patch.dict(os.environ, env, clear=True):
                assert is_multi_agent_enabled() is False

    def test_multi_agent_enabled_via_env(self):
        from llm.agents.coordinator import is_multi_agent_enabled
        with patch.dict(os.environ, {"LLM_MULTI_AGENT": "true"}):
            assert is_multi_agent_enabled() is True
        with patch.dict(os.environ, {"LLM_MULTI_AGENT": "1"}):
            assert is_multi_agent_enabled() is True

    def test_agent_model_override_from_env(self):
        from llm.agents.coordinator import _build_configs_from_env
        from llm.agents.base import AgentRole
        with patch.dict(os.environ, {"AGENT_TRADE_MODEL": "claude-opus-4-20250115"}):
            configs = _build_configs_from_env()
            assert configs[AgentRole.TRADE].model_override == "claude-opus-4-20250115"

    def test_agent_disable_from_env(self):
        from llm.agents.coordinator import _build_configs_from_env
        from llm.agents.base import AgentRole
        with patch.dict(os.environ, {"AGENT_CRITIC_ENABLED": "false"}):
            configs = _build_configs_from_env()
            assert configs[AgentRole.CRITIC].enabled is False


# ---------------------------------------------------------------------------
# 6. Input Building (Context Passing)
# ---------------------------------------------------------------------------

class TestInputBuilding:
    """Test that agent inputs include the right context fields."""

    def _make_snapshot(self):
        """Create a realistic snapshot dict with all knowledge fields."""
        return {
            "m": [{"s": "SOL", "p": 180, "sg": [{"st": "rt", "sd": "BUY", "c": 0.75}]}],
            "g": {"btc": 95000, "b1h": 0.5, "eq": 10000, "pnl": 50, "pos": 1},
            "t": "pre_trade_veto",
            "tc": "SOL LONG",
            "mem": "SOL strong in trends. BTC leading.",
            "knowledge": "CORE RULES: Volume confirms price. Never risk >2%.",
            "deep_memory": "TRADE DNA: 50 trades, 55% WR. REGIME: trend 65% WR. STRATEGY: rt best in trend.",
            "examples": "Ex1: SOL LONG trend +$12 (3-strat agree). Ex2: SOL LONG range -$5 (2-strat).",
            "growth": "Hypothesis: SOL longs in trend >60% WR (testing, 7/10 supporting)",
            "survival": "Day 15. 55% WR. On track. 3 wins in row.",
            "self_perf": {"acc": 0.58, "cal": 0.05, "rg_acc": {"trend": 0.65, "range": 0.38}},
            "recent_dec": "5m: go SOL c=0.72 trend | 15m: skip ETH c=0.45 range",
            "recent_lessons": "SOL LONG win +$8 in trend—replicate | ETH SHORT SL in 2min—too aggressive",
            "autopsy": "LAST 5: 3W/2L $+15 | trend: 75%WR(4) | WEAK: ETH $-8",
            "corr_risk": "medium",
            "port_lev": 3.2,
            "funding_cost_pct": 0.15,
            "funding_alert": "SOL: PAYING 0.010%/8h (0.15%/day at 5x)",
            "session_perf": {"us": {"wr": 0.62}, "asia": {"wr": 0.45}},
            "regime_shifts": "ETH: range→trend",
            "cross_sym": "BTC +1.2% → SOL expected +2-3%",
            "cross_pat": "BTC leads SOL by 15-30min (validated 8/12 times)",
            "pos": [{"s": "BTC", "sd": "LONG", "e": 94500, "lv": 3, "pnl": 120}],
        }

    def test_trade_input_has_all_knowledge_fields(self):
        from llm.agents.coordinator import AgentCoordinator
        from llm.agents.base import AgentOutput, AgentRole

        coord = AgentCoordinator()
        snapshot = self._make_snapshot()
        regime_out = AgentOutput(role=AgentRole.REGIME, data={"rg": "trend", "conf": 0.8})

        input_json = coord._build_trade_input(snapshot, regime_out)
        data = json.loads(input_json)

        # Verify ALL rich context fields are present
        assert "knowledge" in data
        assert "deep_memory" in data
        assert "examples" in data
        assert "growth" in data
        assert "survival" in data
        assert "self_perf" in data
        assert "recent_dec" in data
        assert "recent_lessons" in data
        assert "autopsy" in data
        assert "mem" in data
        assert "regime_analysis" in data
        assert "cross_sym" in data
        assert "cross_pat" in data
        assert "corr_risk" in data
        assert "port_lev" in data
        assert "funding_cost_pct" in data
        assert "funding_alert" in data
        assert "session_perf" in data

    def test_regime_input_is_focused(self):
        """Regime agent should get markets + global, NOT full knowledge."""
        from llm.agents.coordinator import AgentCoordinator

        coord = AgentCoordinator()
        snapshot = self._make_snapshot()

        input_json = coord._build_regime_input(snapshot)
        data = json.loads(input_json)

        assert "markets" in data
        assert "global" in data
        # Should NOT have trade-specific knowledge
        assert "examples" not in data
        assert "survival" not in data

    def test_risk_input_has_portfolio_and_self_perf(self):
        from llm.agents.coordinator import AgentCoordinator
        from llm.agents.base import AgentOutput, AgentRole

        coord = AgentCoordinator()
        snapshot = self._make_snapshot()
        regime_out = AgentOutput(role=AgentRole.REGIME, data={"rg": "trend", "conf": 0.8})
        trade_out = AgentOutput(role=AgentRole.TRADE, data={"a": "go", "c": 0.75})

        input_json = coord._build_risk_input(snapshot, regime_out, trade_out)
        data = json.loads(input_json)

        assert "regime" in data
        assert "trade_decision" in data
        assert "port_lev" in data
        assert "corr_risk" in data
        assert "funding_cost_pct" in data
        assert "self_perf" in data
        assert "autopsy" in data

    def test_critic_input_has_self_awareness(self):
        from llm.agents.coordinator import AgentCoordinator
        from llm.agents.base import AgentOutput, AgentRole

        coord = AgentCoordinator()
        snapshot = self._make_snapshot()
        regime_out = AgentOutput(role=AgentRole.REGIME, data={"rg": "trend", "conf": 0.8})
        trade_out = AgentOutput(role=AgentRole.TRADE, data={"a": "go", "c": 0.8})

        input_json = coord._build_critic_input(snapshot, regime_out, trade_out, None)
        data = json.loads(input_json)

        assert "regime_analysis" in data
        assert "trade_decision" in data
        assert "self_perf" in data
        assert "recent_dec" in data
        assert "recent_lessons" in data
        assert "autopsy" in data
        assert "knowledge" in data
        assert "growth" in data


# ---------------------------------------------------------------------------
# 7. Learning Integration
# ---------------------------------------------------------------------------

class TestLearningIntegration:
    """Test the learning integration pipeline."""

    def test_process_agent_lesson_injects_into_ring_buffer(self):
        from llm.agents.learning_integration import process_agent_lesson
        from llm.post_trade_learner import _recent_lessons

        initial_count = len(_recent_lessons)

        lesson_data = {
            "lesson": "SOL longs fail in range regime—wait for trend",
            "category": "regime_mismatch",
            "strength": "moderate",
            "applies_to": {"symbol": "SOL", "regime": "range", "side": "LONG"},
            "hypothesis": None,
        }
        trade_data = {
            "symbol": "SOL", "side": "LONG", "outcome": "LOSS",
            "pnl": -5.0, "regime": "range",
        }

        process_agent_lesson(lesson_data, trade_data)

        assert len(_recent_lessons) > initial_count
        latest = _recent_lessons[-1]
        assert latest["source"] == "learning_agent"
        assert "SOL" in latest["lesson"]

    def test_empty_lesson_is_ignored(self):
        from llm.agents.learning_integration import process_agent_lesson
        from llm.post_trade_learner import _recent_lessons

        initial_count = len(_recent_lessons)
        process_agent_lesson({"lesson": "", "category": ""}, {"symbol": "X"})
        assert len(_recent_lessons) == initial_count


# ---------------------------------------------------------------------------
# 8. Decision Engine Integration
# ---------------------------------------------------------------------------

class TestDecisionEngineIntegration:
    """Test that multi-agent mode is properly integrated into decision_engine."""

    def test_multi_agent_import_exists(self):
        """The decision engine should be able to import multi-agent module."""
        from llm.agents.coordinator import (
            is_multi_agent_enabled,
            get_coordinator,
        )
        assert callable(is_multi_agent_enabled)
        assert callable(get_coordinator)

    def test_multi_agent_flag_in_engine(self):
        """Decision engine should have _HAS_MULTI_AGENT flag."""
        import llm.decision_engine as engine
        assert hasattr(engine, "_HAS_MULTI_AGENT")
        assert engine._HAS_MULTI_AGENT is True


# ---------------------------------------------------------------------------
# 9. JSON Parsing Edge Cases
# ---------------------------------------------------------------------------

class TestAgentJsonParsing:
    """Test JSON parsing for agent responses."""

    def test_clean_json(self):
        from llm.agents.coordinator import _parse_agent_json
        result = _parse_agent_json('{"rg": "trend", "conf": 0.8}')
        assert result == {"rg": "trend", "conf": 0.8}

    def test_markdown_fenced_json(self):
        from llm.agents.coordinator import _parse_agent_json
        result = _parse_agent_json('```json\n{"rg": "trend"}\n```')
        assert result == {"rg": "trend"}

    def test_json_with_surrounding_text(self):
        from llm.agents.coordinator import _parse_agent_json
        result = _parse_agent_json('Here is my analysis:\n{"rg": "range"}\nDone.')
        assert result == {"rg": "range"}

    def test_invalid_json_returns_none(self):
        from llm.agents.coordinator import _parse_agent_json
        result = _parse_agent_json("This is not JSON at all")
        assert result is None

    def test_empty_input(self):
        from llm.agents.coordinator import _parse_agent_json
        result = _parse_agent_json("")
        assert result is None


# ---------------------------------------------------------------------------
# 10. Action Normalization
# ---------------------------------------------------------------------------

class TestActionNormalization:
    """Test action string normalization."""

    def test_go_becomes_proceed(self):
        from llm.agents.coordinator import _normalize_action
        assert _normalize_action("go") == "proceed"

    def test_skip_becomes_flat(self):
        from llm.agents.coordinator import _normalize_action
        assert _normalize_action("skip") == "flat"

    def test_flip_stays_flip(self):
        from llm.agents.coordinator import _normalize_action
        assert _normalize_action("flip") == "flip"

    def test_unknown_defaults_to_flat(self):
        from llm.agents.coordinator import _normalize_action
        assert _normalize_action("invalid") == "flat"
        assert _normalize_action("") == "flat"
