"""End-to-end test of the LLM agent pipeline with mock API.

Tests the full coordinator pipeline (Regime -> Quant -> Trade -> Risk -> Critic)
using mocked LLM responses.  Verifies:
  - Each agent receives the enriched context it needs
  - The pipeline produces a valid LLMDecision
  - Context flows correctly between agents (upstream outputs appear downstream)
  - The pipeline survives individual agent failures gracefully
  - Critic veto/challenge adjusts or blocks trades
"""

import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

# Ensure bot/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.agents.base import AgentConfig, AgentOutput, AgentRole, DEFAULT_AGENT_CONFIGS
from llm.agents.coordinator import AgentCoordinator
from llm.decision_types import LLMDecision, StrategyWeights

# ---------------------------------------------------------------------------
# Mock realistic agent JSON responses
# ---------------------------------------------------------------------------

MOCK_REGIME_RESPONSE = json.dumps({
    "rg": "trend",
    "conf": 0.75,
    "factors": "ADX=45 strong directional, EMA9>EMA20, volume rising",
    "bias": "bearish",
    "transition": "stable",
    "regime_momentum": "strengthening",
    "expected_duration_h": [4, 12],
    "outlook": "Expect continuation of bearish trend",
})

MOCK_QUANT_RESPONSE = json.dumps({
    "ev": 0.35,
    "conditional_edge": {"wr": 0.62, "n": 15, "regime": "trend"},
    "probability": {"p_tp1": 0.65, "p_sl": 0.35},
    "kelly_fraction": 0.18,
    "signal_quality": "clean",
    "risk_profile": "moderate",
})

MOCK_TRADE_RESPONSE = json.dumps({
    "a": "go",
    "c": 0.72,
    "thesis": "SOL breaking below key support with volume confirmation",
    "ea": "market_now",
    "mu": "SOL bearish structure with declining volume on bounces",
    "n": "3-strategy consensus in trending regime with positive EV",
})

MOCK_RISK_RESPONSE = json.dumps({
    "sz": 1.2,
    "sw": {"rt": 0.8, "cs": 0.7, "be": 0.9},
    "risks": ["correlation with BTC position", "weekend liquidity"],
    "override": None,
})

MOCK_CRITIC_APPROVE_RESPONSE = json.dumps({
    "verdict": "approve",
    "counter_thesis": None,
    "objections": [],
    "adjusted_confidence": 0.70,
    "adjusted_action": "go",
    "reason": "Thesis is sound - trend alignment + volume + multiple strategy agreement",
})

MOCK_CRITIC_CHALLENGE_RESPONSE = json.dumps({
    "verdict": "challenge",
    "counter_thesis": "Bearish momentum fading, RSI divergence forming on 4H",
    "objections": ["RSI divergence", "declining volume on recent candles"],
    "adjusted_confidence": 0.35,
    "adjusted_action": "flat",
    "reason": "Momentum exhaustion risk outweighs continuation thesis",
})


# ---------------------------------------------------------------------------
# Helper: build a realistic snapshot_data
# ---------------------------------------------------------------------------

def _build_snapshot():
    """Build a realistic snapshot dict with all fields the coordinator expects."""
    return {
        # Markets (compact format the coordinator uses)
        "m": [
            {
                "s": "SOL",
                "sym": "SOL",
                "price": 148.50,
                "p": 148.50,
                "price_change_1h_pct": -1.2,
                "price_change_24h_pct": -3.5,
                "volume_ratio": 1.8,
                "volatility": 0.045,
                "funding_rate": -0.0012,
                "oi_change_pct": 2.5,
                "signals": [
                    {"strategy": "regime_trend", "side": "short", "confidence": 0.78,
                     "sym": "SOL"},
                    {"strategy": "monte_carlo_zones", "side": "short", "confidence": 0.65,
                     "sym": "SOL"},
                    {"strategy": "confidence_scorer", "side": "short", "confidence": 0.71,
                     "sym": "SOL"},
                ],
            }
        ],
        # Global context
        "g": {
            "timestamp": 1711900000000,
            "btc_price": 69500.0,
            "btc_1h": -0.8,
            "btc_24h": -2.1,
            "eth_btc": 0.0525,
            "positions": 1,
            "daily_pnl": -12.50,
            "equity": 487.00,
            "cb_active": False,
        },
        # Signals (flat list for brain wiring)
        "signals": [
            {"sym": "SOL", "side": "SELL", "entry": 148.50, "sl": 151.0,
             "tp1": 145.0, "tp2": 142.0, "confidence": 75, "strategy": "regime_trend"},
        ],
        # Enriched context fields -----------------------------------------------
        # Knowledge base (self-teaching curriculum)
        "knowledge": "Axiom: In trending regimes favor continuation. Anti-pattern: chasing extended moves >3ATR.",
        # Deep memory
        "deep_memory": "REGIME: trend WR=68% avg_pnl=+1.2%. RISK: max leverage 8x. STRATEGY: regime_trend best in trend.",
        # Few-shot examples
        "examples": "SOL SHORT 2026-03-28 trend conf=78% pnl=+2.1% (continuation off breakdown).",
        # Growth intelligence
        "growth": "Hypothesis: SOL shorts in trend regime have 72% WR (n=18). Recommendation: increase sizing for aligned setups.",
        # Survival / accountability
        "survival": "Current drawdown: 2.5% of equity. Daily loss limit: 5%. 2 consecutive losses.",
        # Self performance
        "self_perf": {
            "accuracy": 0.63,
            "calibration": 0.58,
            "regime_wr": {"trend": 0.71, "range": 0.45},
            "vacc": 0.52,
        },
        # Recent decisions
        "recent_dec": [
            {"sym": "BTC", "action": "go", "conf": 0.68, "outcome": "win"},
            {"sym": "ETH", "action": "flat", "conf": 0.41, "outcome": None},
        ],
        # Recent lessons
        "recent_lessons": "Lesson: oversized positions in range regime cause large drawdowns. Reduce size.",
        # Trade autopsy
        "autopsy": "Last 5 trades: 3W 2L. Avg win +1.8%, avg loss -1.1%. Declining confidence calibration.",
        # Short-term memory
        "mem": "Note: SOL respecting 150 as resistance. BTC weak, dragging alts.",
        # Session performance
        "session_perf": {"US": {"wr": 0.65, "n": 20}, "ASIA": {"wr": 0.55, "n": 12}},
        # Regime shifts
        "regime_shifts": [{"sym": "ETH", "from": "range", "to": "trend", "ts": 1711899000}],
        # Cross-symbol signals
        "cross_sym": [{"leader": "BTC", "follower": "SOL", "lag_m": 15, "dir": "down"}],
        # Cross-symbol patterns
        "cross_pat": [{"pattern": "btc_leads_sol_short", "wr": 0.68, "n": 22}],
        # Portfolio risk
        "corr_risk": {"BTC_SOL": 0.85},
        "port_lev": 3.2,
        "funding_cost_pct": -0.001,
        "funding_alert": None,
        # External data
        "ext_funding": {"SOL": {"rate": -0.0012, "signal": "bearish"}},
        "ext_liq": {"SOL": {"liq_clusters": [145.0, 142.0], "leverage_ratio": 2.5}},
        "ext_mr": {"SOL": {"shadow_mr_signal": "bearish", "reversion_target": 146.0}},
        "ext_summary": "Funding bearish, liq clusters at 145/142, shadow MR bearish.",
        # Filter annotations
        "filt": {"chop": 0.25, "ev": 0.35, "cr": "pass", "fd": "bearish"},
        "near": [],
        # Positions
        "pos": [{"sym": "BTC", "side": "short", "pnl_pct": 0.8, "size_usd": 200}],
    }


# ---------------------------------------------------------------------------
# Mock the call_llm function so no real API calls are made
# ---------------------------------------------------------------------------

def _identify_agent_role(system_prompt):
    """Identify which agent is being called based on the full system prompt.

    We search for distinctive phrases unique to each agent's raw prompt
    (from prompts.py).  These are specific enough to not appear in the
    shared context blocks that get appended to every agent.
    """
    prompt_lower = system_prompt.lower()

    # Phrases taken directly from each agent's prompt in prompts.py.
    # These are unique to each agent and won't appear in shared context.
    if "you are the risk manager" in prompt_lower:
        return "risk"
    if "you are the critic" in prompt_lower or "you review the trade agent" in prompt_lower:
        return "critic"
    if "market regime classifier" in prompt_lower:
        return "regime"
    if "you are the trade agent" in prompt_lower or "you are the trade evaluator" in prompt_lower:
        return "trade"
    if "you are the quant" in prompt_lower or "statistical analysis" in prompt_lower:
        return "quant"
    return "unknown"


# Ordered pipeline response map
ROLE_RESPONSE_MAP = {
    "regime": MOCK_REGIME_RESPONSE,
    "quant": MOCK_QUANT_RESPONSE,
    "trade": MOCK_TRADE_RESPONSE,
    "risk": MOCK_RISK_RESPONSE,
    "critic": MOCK_CRITIC_APPROVE_RESPONSE,
}


def _make_mock_call_llm(capture_inputs=None, critic_response=None):
    """Return a mock call_llm that dispatches based on the system_prompt content.

    If capture_inputs is a list, appends (role_hint, input_json) tuples for
    downstream assertions about what each agent received.
    If critic_response is provided, it overrides the default critic response.
    """
    def mock_call_llm(system_prompt, snapshot_json, model="", max_tokens=4096,
                      max_retries=2, timeout=30.0, cacheable_prefix=None):
        # When coordinator passes cacheable_prefix (stable agent prompt), use
        # THAT for role identification. The `system_prompt` in that call is
        # the dynamic prefix (calibration/brain/protocol).
        role_hint = _identify_agent_role(cacheable_prefix or system_prompt)

        if role_hint == "critic" and critic_response is not None:
            response = critic_response
        else:
            response = ROLE_RESPONSE_MAP.get(role_hint, "{}")

        if capture_inputs is not None:
            capture_inputs.append((role_hint, snapshot_json))

        usage = {"input_tokens": 500, "output_tokens": 200, "latency_ms": 150}
        return response, usage

    return mock_call_llm


# ---------------------------------------------------------------------------
# Patches applied to every test so the coordinator never touches real services
# ---------------------------------------------------------------------------

def _standard_patches():
    """Return a list of patch context-managers that isolate the coordinator."""
    return [
        # The main LLM call — replaced by our dispatcher
        patch("llm.agents.coordinator.call_llm"),
        # External data collector — not always importable
        patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False),
        # Extensions (brain, debate, telemetry) — not always importable
        patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False),
        # Strategic agents — not needed for core pipeline
        patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False),
        # Phase 4 agents
        patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False),
        # Phase 4A agents
        patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False),
    ]


def _build_coordinator(all_enabled=True):
    """Build an AgentCoordinator with Regime/Quant/Trade/Risk/Critic enabled."""
    configs = dict(DEFAULT_AGENT_CONFIGS)
    # Ensure core pipeline agents are enabled
    for role in (AgentRole.REGIME, AgentRole.QUANT, AgentRole.TRADE,
                 AgentRole.RISK, AgentRole.CRITIC):
        if role in configs:
            configs[role] = AgentConfig(
                role=role,
                enabled=all_enabled,
                max_tokens=configs[role].max_tokens,
                timeout_s=configs[role].timeout_s,
                required=configs[role].required,
            )
    # Disable non-core agents so they don't interfere
    for role in configs:
        if role not in (AgentRole.REGIME, AgentRole.QUANT, AgentRole.TRADE,
                        AgentRole.RISK, AgentRole.CRITIC):
            configs[role] = AgentConfig(role=role, enabled=False)
    return AgentCoordinator(agent_configs=configs)


# ===========================================================================
# Tests
# ===========================================================================

class TestLLMPipelineE2E:
    """Test the full agent pipeline with mock API responses."""

    # ------------------------------------------------------------------
    # 1. Full pipeline produces a valid LLMDecision
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_full_pipeline_produces_decision(self, mock_llm):
        """The coordinator should produce a valid LLMDecision from mock agents."""
        mock_llm.side_effect = _make_mock_call_llm()
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
            model_for_trigger="claude-sonnet-4-5-20250929",
        )

        # Must produce a decision
        assert decision is not None, "Pipeline returned None — expected LLMDecision"
        assert isinstance(decision, LLMDecision)

        # Core fields populated
        assert decision.action in ("proceed", "go", "flat", "flip"), (
            f"Unexpected action: {decision.action}"
        )
        assert 0.0 <= decision.confidence <= 1.0, (
            f"Confidence out of range: {decision.confidence}"
        )
        assert decision.regime == "trend", (
            f"Expected regime 'trend' from mock, got '{decision.regime}'"
        )
        assert isinstance(decision.strategy_weights, StrategyWeights)
        assert isinstance(decision.notes, str)
        assert decision.size_multiplier > 0, "Size multiplier should be positive"

    # ------------------------------------------------------------------
    # 2. Enriched context reaches each agent
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_enriched_context_reaches_agents(self, mock_llm):
        """Verify technicals, external data, feedback state reach each agent."""
        captured = []
        mock_llm.side_effect = _make_mock_call_llm(capture_inputs=captured)
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None

        # We expect 5 agent calls: regime, quant, trade, risk, critic
        assert len(captured) >= 5, (
            f"Expected >= 5 agent calls, got {len(captured)}: "
            f"{[c[0] for c in captured]}"
        )

        # Build a dict of role -> parsed input for easy assertion
        agent_inputs = {}
        for role_hint, input_json in captured:
            try:
                agent_inputs[role_hint] = json.loads(input_json)
            except (json.JSONDecodeError, TypeError):
                agent_inputs[role_hint] = input_json

        # --- Regime Agent gets market data + external data ---
        regime_in = agent_inputs.get("regime", {})
        assert "markets" in regime_in or "global" in regime_in, (
            "Regime agent should receive market data"
        )

        # --- Quant Agent gets market context + regime output ---
        quant_in = agent_inputs.get("quant", {})
        assert "regime" in quant_in or "regime_classification" in quant_in, (
            "Quant agent should receive regime classification"
        )

        # --- Trade Agent gets full snapshot + regime + knowledge ---
        trade_in = agent_inputs.get("trade", {})
        assert "regime_analysis" in trade_in, (
            "Trade agent must receive regime_analysis from upstream Regime Agent"
        )
        # Enriched context fields that should flow through
        for field in ("knowledge", "deep_memory", "self_perf", "recent_dec",
                       "recent_lessons", "autopsy", "mem", "session_perf"):
            assert field in trade_in, (
                f"Trade agent missing enriched field '{field}'"
            )
        # External data should appear
        for field in ("ext_funding", "ext_liq", "ext_mr", "ext_summary"):
            assert field in trade_in, (
                f"Trade agent missing external data field '{field}'"
            )

        # --- Risk Agent gets regime + trade decision + portfolio state ---
        risk_in = agent_inputs.get("risk", {})
        assert "regime" in risk_in, "Risk agent should receive regime data"
        assert "trade_decision" in risk_in, (
            "Risk agent should receive trade decision from upstream Trade Agent"
        )
        # Portfolio fields
        for field in ("g", "self_perf"):
            assert field in risk_in, (
                f"Risk agent missing portfolio/performance field '{field}'"
            )

        # --- Critic Agent gets all prior agent outputs ---
        critic_in = agent_inputs.get("critic", {})
        assert "regime_analysis" in critic_in, (
            "Critic agent should receive regime_analysis"
        )
        assert "trade_decision" in critic_in, (
            "Critic agent should receive trade_decision"
        )
        # Self-awareness fields for the quality gate
        for field in ("self_perf", "recent_dec", "recent_lessons"):
            assert field in critic_in, (
                f"Critic agent missing self-awareness field '{field}'"
            )

    # ------------------------------------------------------------------
    # 3. Pipeline survives agent failure
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_pipeline_survives_agent_failure(self, mock_llm):
        """If an optional agent fails (Risk, Critic), pipeline should degrade gracefully."""

        call_count = [0]

        def flaky_llm(system_prompt, snapshot_json, model="", max_tokens=4096,
                      max_retries=2, timeout=30.0, cacheable_prefix=None):
            call_count[0] += 1
            # Use cacheable_prefix for role identification when present
            # (coordinator's new two-block caching structure)
            role = _identify_agent_role(cacheable_prefix or system_prompt)

            # Risk and Critic agents return garbage (simulating failure)
            if role == "risk":
                return "NOT VALID JSON AT ALL {{{{", {"input_tokens": 100, "output_tokens": 50, "latency_ms": 100}
            if role == "critic":
                return None, {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "error": "timeout"}

            # Regime, Quant, Trade work normally
            return _make_mock_call_llm()(system_prompt, snapshot_json, model,
                                        max_tokens, max_retries, timeout,
                                        cacheable_prefix=cacheable_prefix)

        mock_llm.side_effect = flaky_llm
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )

        # Should still produce a decision (Risk and Critic are optional)
        assert decision is not None, (
            "Pipeline should produce a decision even when optional agents fail"
        )
        assert isinstance(decision, LLMDecision)
        # Action should come from the Trade Agent
        assert decision.action in ("proceed", "go", "flat", "flip")
        # Default size_multiplier when Risk agent fails
        assert decision.size_multiplier == 1.0 or decision.size_multiplier > 0

    # ------------------------------------------------------------------
    # 4. Required agent failure aborts pipeline
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_required_agent_failure_aborts(self, mock_llm):
        """If Regime Agent (required) fails, pipeline should return None."""

        def always_fail(system_prompt, snapshot_json, model="", max_tokens=4096,
                        max_retries=2, timeout=30.0, cacheable_prefix=None):
            return None, {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0,
                          "error": "api_error"}

        mock_llm.side_effect = always_fail
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )

        assert decision is None, (
            "Pipeline should return None when required Regime Agent fails"
        )

    # ------------------------------------------------------------------
    # 5. Critic veto (challenge) reduces confidence or blocks trade
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_critic_veto_shadow_logged_not_enforced(self, mock_llm):
        """DECHOKE 6 (2026-07-02): Critic is SHADOW by default — its challenge
        is recorded in notes as a would-veto but does NOT block the trade or
        mutate confidence. (CRITIC_ENFORCE=true restores enforcement.)"""
        import os
        mock_llm.side_effect = _make_mock_call_llm(
            critic_response=MOCK_CRITIC_CHALLENGE_RESPONSE
        )
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        os.environ.pop("CRITIC_ENFORCE", None)
        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )

        assert decision is not None
        # Shadow: the critic's adjusted_action=flat must NOT be applied
        assert decision.action != "flat" or "consistency_override" in decision.notes, (
            f"Critic shadow must not block the trade. "
            f"Got action={decision.action}, notes={decision.notes}"
        )
        # The would-veto must be visible in notes for downstream scoring
        assert "CRITIC-SHADOW" in decision.notes, (
            f"Decision notes should carry the shadow would-veto: {decision.notes}"
        )

    # ------------------------------------------------------------------
    # 6. Context flows between agents (regime output in trade input)
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_context_flows_between_agents(self, mock_llm):
        """Regime output should appear in Trade Agent's input, trade in Risk, etc."""
        captured = []
        mock_llm.side_effect = _make_mock_call_llm(capture_inputs=captured)
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None

        agent_inputs = {}
        for role_hint, input_json in captured:
            try:
                agent_inputs[role_hint] = json.loads(input_json)
            except (json.JSONDecodeError, TypeError):
                agent_inputs[role_hint] = input_json

        # Regime -> Trade: regime_analysis should contain the mock regime output
        trade_in = agent_inputs.get("trade", {})
        ra = trade_in.get("regime_analysis", {})
        assert ra.get("rg") == "trend", (
            f"Trade agent should see regime='trend' from Regime Agent, got: {ra}"
        )
        assert ra.get("bias") == "bearish", (
            f"Trade agent should see bias='bearish' from Regime Agent, got: {ra}"
        )

        # Regime + Trade -> Risk: risk input should have both
        risk_in = agent_inputs.get("risk", {})
        assert risk_in.get("regime", {}).get("rg") == "trend", (
            "Risk agent should see regime classification"
        )
        assert risk_in.get("trade_decision", {}).get("a") == "go", (
            "Risk agent should see trade action='go'"
        )

        # Regime + Trade + Risk -> Critic: critic input should have all upstream
        critic_in = agent_inputs.get("critic", {})
        assert critic_in.get("regime_analysis", {}).get("rg") == "trend", (
            "Critic agent should see regime classification"
        )
        assert critic_in.get("trade_decision", {}).get("a") == "go", (
            "Critic agent should see trade action"
        )
        # Risk assessment injected if Risk Agent succeeded
        assert "risk_assessment" in critic_in, (
            "Critic agent should see risk_assessment from Risk Agent"
        )

    # ------------------------------------------------------------------
    # 7. Quant output flows to Trade and Risk
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_quant_output_flows_downstream(self, mock_llm):
        """Quant Agent output should be available to Trade and Risk agents."""
        captured = []
        mock_llm.side_effect = _make_mock_call_llm(capture_inputs=captured)
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None

        agent_inputs = {}
        for role_hint, input_json in captured:
            try:
                agent_inputs[role_hint] = json.loads(input_json)
            except (json.JSONDecodeError, TypeError):
                agent_inputs[role_hint] = input_json

        # Quant -> Trade: quant_analysis should be present
        trade_in = agent_inputs.get("trade", {})
        assert "quant_analysis" in trade_in, (
            "Trade agent should receive quant_analysis from Quant Agent"
        )
        qa = trade_in["quant_analysis"]
        assert qa.get("ev") == 0.35, "Quant EV should flow to trade agent"
        assert qa.get("kelly_fraction") == 0.18, "Kelly fraction should flow"

        # Quant -> Risk: quant compact should be present
        risk_in = agent_inputs.get("risk", {})
        assert "quant" in risk_in, (
            "Risk agent should receive quant data (kelly, ev, risk_profile)"
        )
        qr = risk_in["quant"]
        assert "kelly" in qr or "ev" in qr, (
            f"Risk agent quant data should have kelly or ev, got: {qr}"
        )

    # ------------------------------------------------------------------
    # 8. Pipeline call count is correct
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_pipeline_call_count(self, mock_llm):
        """Pipeline should call exactly 5 agents: regime, quant, trade, risk, critic."""
        mock_llm.side_effect = _make_mock_call_llm()
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None
        assert mock_llm.call_count == 5, (
            f"Expected 5 LLM calls (regime, quant, trade, risk, critic), "
            f"got {mock_llm.call_count}"
        )

    # ------------------------------------------------------------------
    # 9. Pipeline results stored for external consumers
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_pipeline_results_stored(self, mock_llm):
        """Coordinator should store per-agent outputs in last_pipeline_results."""
        mock_llm.side_effect = _make_mock_call_llm()
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None

        results = coord.last_pipeline_results
        assert AgentRole.REGIME in results, "Regime output should be stored"
        assert AgentRole.QUANT in results, "Quant output should be stored"
        assert AgentRole.TRADE in results, "Trade output should be stored"
        assert AgentRole.RISK in results, "Risk output should be stored"
        assert AgentRole.CRITIC in results, "Critic output should be stored"

        # Each stored result should be an AgentOutput with data
        for role, out in results.items():
            assert isinstance(out, AgentOutput), f"{role} result is not AgentOutput"
            assert out.ok, f"{role} output should be ok (no error)"
            assert out.data, f"{role} output should have parsed data"

    # ------------------------------------------------------------------
    # 10. Disabled agents are skipped
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_disabled_agents_skipped(self, mock_llm):
        """Disabled optional agents should not be called."""
        mock_llm.side_effect = _make_mock_call_llm()

        # Build coordinator with Risk and Critic disabled
        configs = dict(DEFAULT_AGENT_CONFIGS)
        for role in (AgentRole.REGIME, AgentRole.QUANT, AgentRole.TRADE):
            configs[role] = AgentConfig(
                role=role, enabled=True,
                max_tokens=configs[role].max_tokens,
                timeout_s=configs[role].timeout_s,
                required=configs[role].required,
            )
        for role in configs:
            if role not in (AgentRole.REGIME, AgentRole.QUANT, AgentRole.TRADE):
                configs[role] = AgentConfig(role=role, enabled=False)

        coord = AgentCoordinator(agent_configs=configs)
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None
        # Only 3 agents should have been called: regime, quant, trade
        assert mock_llm.call_count == 3, (
            f"Expected 3 LLM calls (regime, quant, trade), got {mock_llm.call_count}"
        )

    # ------------------------------------------------------------------
    # 11. Regime fallback on 'unknown' classification
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_regime_unknown_fallback(self, mock_llm):
        """If Regime Agent returns 'unknown', technical fallback should try to classify."""
        unknown_regime = json.dumps({
            "rg": "unknown",
            "conf": 0.3,
            "factors": "conflicting signals",
            "bias": "neutral",
            "transition": "uncertain",
        })

        def unknown_regime_llm(system_prompt, snapshot_json, model="", max_tokens=4096,
                               max_retries=2, timeout=30.0, cacheable_prefix=None):
            role = _identify_agent_role(cacheable_prefix or system_prompt)
            if role == "regime":
                return unknown_regime, {"input_tokens": 300, "output_tokens": 100, "latency_ms": 100}
            return _make_mock_call_llm()(system_prompt, snapshot_json, model,
                                        max_tokens, max_retries, timeout,
                                        cacheable_prefix=cacheable_prefix)

        mock_llm.side_effect = unknown_regime_llm
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        # Should still produce a decision — the pipeline doesn't abort on unknown regime
        assert decision is not None
        assert isinstance(decision, LLMDecision)

    # ------------------------------------------------------------------
    # 12. Decision notes aggregate info from all agents
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_decision_notes_aggregate(self, mock_llm):
        """Decision notes should contain info from Trade Agent and pipeline annotations."""
        mock_llm.side_effect = _make_mock_call_llm()
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None
        # Trade Agent note should be present
        assert "3-strategy consensus" in decision.notes or "regime" in decision.notes.lower(), (
            f"Decision notes should include trade agent's reasoning: {decision.notes}"
        )

    # ------------------------------------------------------------------
    # 13. Filter annotations reach Trade and Critic
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_filter_annotations_flow(self, mock_llm):
        """Filter annotations (filt) should reach Trade and Critic agents."""
        captured = []
        mock_llm.side_effect = _make_mock_call_llm(capture_inputs=captured)
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None

        agent_inputs = {}
        for role_hint, input_json in captured:
            try:
                agent_inputs[role_hint] = json.loads(input_json)
            except (json.JSONDecodeError, TypeError):
                agent_inputs[role_hint] = input_json

        # Trade Agent gets filter_assessment
        trade_in = agent_inputs.get("trade", {})
        assert "filter_assessment" in trade_in or "filt" in trade_in, (
            "Trade agent should receive filter annotations"
        )

        # Critic Agent gets filter_assessment
        critic_in = agent_inputs.get("critic", {})
        assert "filter_assessment" in critic_in, (
            "Critic agent should receive filter annotations"
        )

    # ------------------------------------------------------------------
    # 14. Consistency check runs and score is stored
    # ------------------------------------------------------------------
    @patch("llm.agents.coordinator._EXTENSIONS_AVAILABLE", False)
    @patch("llm.agents.coordinator._EXTERNAL_DATA_AVAILABLE", False)
    @patch("llm.agents.coordinator._STRATEGIC_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator._PHASE_4A_AGENTS_AVAILABLE", False)
    @patch("llm.agents.coordinator.call_llm")
    def test_consistency_check_runs(self, mock_llm):
        """Pipeline should run consistency check and store the score."""
        mock_llm.side_effect = _make_mock_call_llm()
        coord = _build_coordinator()
        snapshot = _build_snapshot()

        decision = coord.get_trading_decision(
            snapshot_data=snapshot,
            trigger_reason="PRE_TRADE",
        )
        assert decision is not None
        # Consistency score should be computed
        assert coord.last_consistency_score is not None, (
            "Consistency score should be computed and stored"
        )
        assert 0.0 <= coord.last_consistency_score <= 1.0, (
            f"Consistency score out of range: {coord.last_consistency_score}"
        )
