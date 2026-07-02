"""
Agent Coordinator: orchestrates the multi-agent LLM decision pipeline.

Instead of one big LLM call that does everything (regime + decision + sizing
+ learning + self-critique), the coordinator runs focused specialist agents
in a logical chain:

  1. Regime Agent   → classify regime (Haiku — fast, cheap)
  2. Trade Agent    → decide action (Sonnet — main brain)
  3. Risk Agent     → size position + strategy weights (Haiku)
  4. Critic Agent   → review & adjust before execution (Sonnet)
  5. Learning Agent → extract lesson from closed trade (Haiku, async)

Benefits over monolithic:
  - Each agent gets a focused prompt → better at its domain
  - Smaller max_tokens per call → faster response, lower cost
  - Critic can veto overconfident decisions the trade agent wouldn't catch
  - Learning agent runs *after* trade closes, not mixed into pre-trade call
  - Easy to A/B test or swap individual agents

Enable: LLM_MULTI_AGENT=true
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from llm.agents.base import (
    AgentConfig,
    AgentOutput,
    AgentRole,
    DEFAULT_AGENT_CONFIGS,
)
from llm.agents.prompts import AGENT_PROMPTS
from llm.agents.prompt_enricher import enrich_prompt
from llm.agents.shared_context import (
    build_shared_context_block,
    get_pipeline_scratchpad,
    get_shared_lessons,
    reset_pipeline_scratchpad,
    score_confluence,
)
from llm.agents.thought_protocol import build_protocol_prefix
from llm.agents.consistency_checker import (
    check_pipeline_consistency,
    get_consistency_tracker,
)
from llm.client import call_llm
from llm.decision_types import LLMDecision, StrategyWeights, EntryDecision

# ── CLI LLM routing ──────────────────────────────────────────────────────────
# When USE_CLI_LLM=true (or no API key available), route all agent calls
# through the Claude Code CLI subprocess instead of the Anthropic API.
# This lets the full 9-agent system run on a Max subscription at $0/call.

def _should_use_cli() -> bool:
    import os
    # Never use CLI routing in test runs — mocks target call_llm, not the CLI path
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    if os.getenv("USE_CLI_LLM", "").lower() in ("1", "true", "yes", "on"):
        return True
    # Auto-detect: use CLI when no API key is set
    if not os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-"):
        try:
            from llm.claude_cli_client import available as _cli_avail
            return _cli_avail()
        except Exception:
            pass
    return False


_MODEL_ALIAS = {
    # Map Anthropic API model IDs to Claude CLI aliases
    "claude-haiku-4-5-20251001": "haiku",
    "claude-haiku-4-5": "haiku",
    "claude-haiku-3-5-20241022": "haiku",
    "claude-haiku": "haiku",
    "claude-sonnet-4-5-20250929": "sonnet",
    "claude-sonnet-4-6": "sonnet",
    "claude-sonnet-4-5": "sonnet",
    "claude-sonnet-3-7-20250219": "sonnet",
    "claude-sonnet": "sonnet",
    "claude-opus-4-7": "opus",
    "claude-opus-4-6": "opus",
    "claude-opus-4-5": "opus",
    "claude-opus-4-20250115": "opus",
    "claude-opus-3-5-20241022": "opus",
    "claude-opus": "opus",
}

_CLI_JSON_SUFFIX = (
    "\n\nCRITICAL: Your ENTIRE response must be a single JSON object. "
    "No markdown, no prose before or after. Start with { and end with }."
)


# ── Global Sonnet/Opus concurrency throttle ──────────────────────────────────
# When SCAN_PARALLEL_SYMBOLS is on, multiple symbols run their pipelines in
# parallel worker threads. The cheap Haiku stages (Regime/Risk/Quant) may fan
# out freely, but the expensive Sonnet/Opus calls (Trade/Critic) route through
# the Claude Code CLI subprocess, which is QUOTA/rate-limit sensitive (not
# pay-per-token). This BoundedSemaphore caps concurrent Sonnet/Opus `claude -p`
# sessions to LLM_SONNET_CONCURRENCY regardless of how many symbols are in
# flight, so adding symbols can never increase peak concurrent Sonnet sessions
# beyond the configured cap. In the serial path the semaphore is acquired and
# released by a single thread with no contention (no behavior change).
def _sonnet_concurrency_limit() -> int:
    try:
        n = int(os.getenv("LLM_SONNET_CONCURRENCY", "2"))
    except (TypeError, ValueError):
        n = 2
    return max(1, n)


_SONNET_SEMAPHORE = threading.BoundedSemaphore(_sonnet_concurrency_limit())


def _is_sonnet_or_opus(model: str) -> bool:
    """True if the resolved model is a Sonnet/Opus tier (throttled), else
    False for Haiku (free to fan out)."""
    if not model:
        return False
    m = model.lower()
    return ("sonnet" in m) or ("opus" in m)


def _call_llm_via_cli(
    system_prompt: str,
    snapshot_json: str,
    model: str = "sonnet",
    max_tokens: int = 1500,
    timeout: int = 90,
    cacheable_prefix: str = "",
) -> tuple:
    """Adapter: routes a coordinator agent call through Claude CLI.
    Returns (raw_text, usage_dict) — same interface as call_llm()."""
    from llm.claude_cli_client import call_agent as _cli_call
    # Translate API model name to CLI alias
    cli_model = _MODEL_ALIAS.get(model, "sonnet")
    # Combine stable agent prompt + dynamic system content
    # Add JSON enforcement suffix to both parts
    full_system = "\n\n".join(filter(None, [cacheable_prefix, system_prompt]))
    full_system = full_system + _CLI_JSON_SUFFIX
    # Prepend a hard JSON-only constraint to the user prompt.
    # This appears right before the model must respond — harder to ignore than system-prompt rules.
    json_guard = "OUTPUT RAW JSON ONLY — no prose, no markdown, no explanation. Start {, end }.\n\nDATA:\n"
    # Snapshot goes via stdin — no Windows cmd-line length limit. Pass full context.
    resp = _cli_call(
        user_prompt=json_guard + snapshot_json,
        system_prompt=full_system,
        model=cli_model,
        max_budget_usd=1.00,
        timeout=max(timeout, 300),
        allow_tools=False,
    )
    if not resp.ok:
        return None, {"error": resp.error, "latency_ms": int(resp.latency_s * 1000),
                      "input_tokens": 0, "output_tokens": 0}
    # Use the tolerant extractor from claude_cli_client as an additional fallback
    if resp.text and not resp.text.strip().startswith("{"):
        from llm.claude_cli_client import _extract_json as _cli_extract
        extracted = _cli_extract(resp.text)
        if extracted:
            import json as _json
            return _json.dumps(extracted), {
                "latency_ms": int(resp.latency_s * 1000), "input_tokens": 0,
                "output_tokens": 0, "cost_usd": resp.cost_usd,
            }
    return resp.text, {
        "latency_ms": int(resp.latency_s * 1000),
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": resp.cost_usd,
    }

# External data collectors (funding/OI, liquidation, shadow MR)
try:
    from llm.agents.external_data import (
        get_external_data_for_snapshot,
        format_for_agent as format_external_data,
    )
    _EXTERNAL_DATA_AVAILABLE = True
except ImportError:
    _EXTERNAL_DATA_AVAILABLE = False

# Market depth feed (F1, FULL_PIPE map §2b — L2 spread/depth/imbalance/tape).
# Separate guard from external_data so a depth import failure can never mute
# the funding/liquidation feeds. Flag: EXT_DEPTH_ENABLED (module-side, default true).
try:
    from llm.agents.market_depth import get_depth_for_snapshot
    _MARKET_DEPTH_AVAILABLE = True
except ImportError:
    _MARKET_DEPTH_AVAILABLE = False

# Strategic agents (Portfolio, Forecaster, Hypothesis, Correlator)
# These are optional Phase 3 agents
try:
    from llm.agents.strategic_agents import (
        build_portfolio_aggregator,
        build_regime_forecaster,
        build_hypothesis_generator,
        build_correlator,
    )
    _STRATEGIC_AGENTS_AVAILABLE = True
except ImportError:
    _STRATEGIC_AGENTS_AVAILABLE = False

# Phase 4 agents (Scalping + Conviction)
# These are optional Phase 4 agents
try:
    from llm.agents.phase_4_agents import (
        build_micro_trend_detector,
        build_scalper,
        build_conviction,
    )
    _PHASE_4_AGENTS_AVAILABLE = True
except ImportError:
    _PHASE_4_AGENTS_AVAILABLE = False

# Phase 4A agents (Core Trading System)
# These are optional Phase 4A agents
try:
    from llm.agents.phase_4a_trading_agents import (
        build_position_sizer,
        build_entry_optimizer,
        build_exit_advisor,
        build_risk_guard,
        build_agent_router,
        build_consensus_builder,
    )
    _PHASE_4A_AGENTS_AVAILABLE = True
except ImportError:
    _PHASE_4A_AGENTS_AVAILABLE = False

# Technical indicators for agent context
try:
    from llm.agents.technicals import compute_all_technicals, format_technicals_for_agent
    _TECHNICALS_AVAILABLE = True
except ImportError:
    _TECHNICALS_AVAILABLE = False

# Feedback loop states for agent awareness
try:
    from llm.agents.feedback_state import format_feedback_for_agent
    _FEEDBACK_STATE_AVAILABLE = True
except ImportError:
    _FEEDBACK_STATE_AVAILABLE = False

# Position enrichment for richer position context
try:
    from llm.agents.position_enrichment import format_positions_for_agent
    _POSITION_ENRICHMENT_AVAILABLE = True
except ImportError:
    _POSITION_ENRICHMENT_AVAILABLE = False

# Portfolio-level intelligence (exposure, correlation, risk budget)
try:
    from llm.agents.portfolio_intelligence import (
        compute_portfolio_state,
        format_portfolio_for_agent,
    )
    _PORTFOLIO_INTEL_AVAILABLE = True
except ImportError:
    _PORTFOLIO_INTEL_AVAILABLE = False

# Pipeline telemetry (recent gate decisions)
try:
    from core.pipeline_telemetry import get_telemetry
    _TELEMETRY_AVAILABLE = True
except ImportError:
    _TELEMETRY_AVAILABLE = False

# Agent self-performance tracker (per-agent accuracy, calibration, veto stats)
try:
    from llm.agents.agent_performance import get_tracker as get_perf_tracker
    _AGENT_PERF_AVAILABLE = True
except ImportError:
    _AGENT_PERF_AVAILABLE = False

# Background thinker journal (market observations, position reviews, patterns)
try:
    from llm.agents.background_thinker import BackgroundThinker
    _BACKGROUND_THINKER_AVAILABLE = True
except ImportError:
    _BACKGROUND_THINKER_AVAILABLE = False

# GAP 1: Execution quality / slippage tracking
try:
    from llm.execution_quality import get_execution_quality_summary
    _HAS_EXEC_QUALITY = True
except ImportError:
    _HAS_EXEC_QUALITY = False

# GAP 2: Reflection engine (move exhaustion, re-entry tracking, trade quality)
try:
    from llm.reflection_engine import ReflectionEngine
    _HAS_REFLECTION = True
except ImportError:
    _HAS_REFLECTION = False

# GAP 3: ML/RL predictions (direction model, win probability)
try:
    from ml.learner import SignalLearner
    _HAS_ML_LEARNER = True
except ImportError:
    _HAS_ML_LEARNER = False

# Pre-trade simulator (scenario analysis before Trade Agent)
try:
    from llm.agents.pre_trade_simulator import PreTradeSimulator
    _SIMULATOR_AVAILABLE = True
except ImportError:
    _SIMULATOR_AVAILABLE = False

# Pipeline extensions: quant engine, agent brains, debate, telemetry
# These are optional — gracefully degrade if modules not yet built
try:
    from llm.agents.pipeline_extensions import (
        get_brain_context_for_agent,
        record_agent_decision,
        run_debate_if_warranted,
        run_interactive_debate_if_enabled,
        apply_debate_to_confidence,
        log_pipeline_telemetry,
        format_quant_for_prompt,
        compute_quant_context,
    )
    _EXTENSIONS_AVAILABLE = True
except ImportError:
    _EXTENSIONS_AVAILABLE = False

logger = logging.getLogger("bot.llm.agents.coordinator")


def is_multi_agent_enabled() -> bool:
    """Check if multi-agent mode is enabled."""
    return os.getenv("LLM_MULTI_AGENT", "").lower() in ("1", "true", "yes")


class AgentCoordinator:
    """Orchestrates specialist agents for trading decisions.

    The coordinator manages:
      - Agent sequencing (regime → trade → risk → critic)
      - Context passing between agents (each sees prior agents' output)
      - Model routing per agent (uses tier system or per-agent override)
      - Failure handling (required agents abort pipeline, optional degrade)
      - Token budget tracking
    """

    def __init__(self, agent_configs: Optional[Dict[AgentRole, AgentConfig]] = None):
        self.configs = agent_configs or dict(DEFAULT_AGENT_CONFIGS)
        self._call_count = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_latency_ms = 0
        # Delta tracking: return per-pipeline usage, not cumulative
        self._last_reported_calls = 0
        self._last_reported_input = 0
        self._last_reported_output = 0
        self._last_reported_latency = 0
        # Preserve per-agent outputs from last pipeline run for external consumers
        self.last_pipeline_results: Dict[AgentRole, AgentOutput] = {}
        self.last_exit_output: Optional[AgentOutput] = None
        self.last_consistency_score: Optional[float] = None
        # Lazy-initialized enrichment modules
        self._background_thinker: Optional[Any] = None
        self._pre_trade_simulator: Optional[Any] = None
        # Cache lock: under SCAN_PARALLEL_SYMBOLS the regime/entry-decision
        # caches are read-modify-written from multiple worker threads. dict
        # get/set are individually GIL-atomic, but the TTL check-then-set
        # sequence is not — guard it so concurrent symbols can't corrupt
        # entries. Cheap and uncontended in the serial path.
        self._cache_lock = threading.RLock()
        # Regime cache: avoid re-calling Regime Agent when regime hasn't changed
        # Structure: {symbol: {"result": AgentOutput, "timestamp": time.time()}}
        self._regime_cache: Dict[str, Dict[str, Any]] = {}
        self._regime_cache_ttl: float = 30 * 60  # 30 minutes
        # Scout thesis cache: pre-formed theses from idle-time Scout runs
        # Structure: {SYMBOL: {"watchlist_item": {...}, "timestamp": time.time()}}
        # get_entry_decision() injects this into the snapshot so agents start
        # with Scout's pre-formed view instead of building from scratch.
        self._scout_thesis_cache: Dict[str, Dict[str, Any]] = {}
        self._scout_cache_ttl: float = 20 * 60  # 20 minutes
        # Decision cache: avoid re-running 5-agent pipeline for unchanged conditions.
        # Only SKIP decisions are cached — GO decisions are single-use by nature.
        # Structure: {cache_key: {"decision": EntryDecision, "ts": float, "entry_price": float}}
        # Quota impact: bot calls pipeline every 30s × 4 symbols. On stable markets this
        # burns all quota in ~30 min. Caching skips for 3 minutes reduces burn by ~6x.
        self._entry_decision_cache: Dict[str, Dict[str, Any]] = {}
        self._entry_cache_ttl: float = 3 * 60   # 3 minutes: fast enough to catch regime shifts
        self._entry_cache_price_tolerance: float = 0.003  # 0.3% price move busts the cache
        self._entry_cache_hits: int = 0
        self._entry_cache_misses: int = 0

    # ── Public API ──────────────────────────────────────────────

    def _entry_cache_key(self, signal_ctx: Dict[str, Any], market_ctx: Dict[str, Any]) -> str:
        """Build a stable cache key from the decision inputs.

        Buckets confidence to ±2.5pp and price to ±0.2% so near-identical
        repeated signals map to the same key without requiring exact equality.
        """
        symbol = signal_ctx.get("symbol", "")
        side = signal_ctx.get("side", "")
        conf_raw = float(signal_ctx.get("confidence", 0))
        # Bucket confidence in 5pp increments (65.2 → 65)
        conf_bucket = int(conf_raw // 5) * 5
        # Bucket price to nearest 0.2% (prevents micro-tick misses)
        entry = float(signal_ctx.get("entry", 1.0))
        price_bucket = round(entry / (entry * 0.002)) if entry > 0 else 0
        hour_utc = int(market_ctx.get("time_utc_hour", -1))
        # Include the strategy set so regime_trend vs bollinger_squeeze don't share a key
        strats = signal_ctx.get("strategies_agree", []) or []
        strat_key = ",".join(sorted(str(s) for s in strats)) if strats else signal_ctx.get("strategy", "")
        num_agree = int(signal_ctx.get("num_agree", signal_ctx.get("num_strategies_agree", 0)))
        return f"{symbol}|{side}|c{conf_bucket}|p{price_bucket}|h{hour_utc}|n{num_agree}|{strat_key}"

    def invalidate_regime_cache(self, symbol: Optional[str] = None) -> None:
        """Clear regime cache for a symbol or all symbols."""
        with self._cache_lock:
            if symbol:
                self._regime_cache.pop(symbol, None)
                logger.info(f"[MULTI-AGENT] Regime cache invalidated for {symbol}")
            else:
                self._regime_cache.clear()
                logger.info("[MULTI-AGENT] Regime cache fully cleared")

    def get_trading_decision(
        self,
        snapshot_data: dict,
        trigger_reason: str = "",
        model_for_trigger: Optional[str] = None,
    ) -> Optional[LLMDecision]:
        """Run the multi-agent pipeline and return a merged LLMDecision.

        Args:
            snapshot_data: The compact snapshot dict (same format as monolithic).
            trigger_reason: Why the LLM was called.
            model_for_trigger: Tier-routed model (fallback for agents without override).

        Returns:
            Merged LLMDecision or None on failure.
        """
        start = time.monotonic()
        pipeline_results: Dict[AgentRole, AgentOutput] = {}

        # Reset per-pipeline shared state
        scratchpad = reset_pipeline_scratchpad()
        shared_lessons = get_shared_lessons()

        # Flag backtest mode so inner builders skip live-performance penalties.
        # Live trading penalties (loss streaks, calibration drift) come from unfiltered
        # trades in the fallback era and should not colour data-collection backtests.
        _is_backtest = "backtest" in trigger_reason.lower()
        snapshot_data["_is_backtest"] = _is_backtest
        self._current_is_backtest = _is_backtest  # accessible to _call_agent system-prompt builders

        # In backtest mode strip all live-performance data from the snapshot so
        # it can't bleed into any agent's context.  These stats were accumulated
        # during the fallback-approve era (unfiltered trades) and carry poisoned
        # WR numbers (0-14%) that make Kelly universally negative → all skips.
        if _is_backtest:
            for _perf_key in ("self_perf", "network_calibration_adj",
                              "edge_decay_alerts", "_enr_dynamic_stats",
                              "_perf_tracker_summary"):
                snapshot_data.pop(_perf_key, None)

        # ── Inject external data (funding/OI, liq levels, shadow MR) ──
        # Skip in backtest: fetches live current data (funding/OI/liquidation),
        # not historical April values — look-ahead bias.
        if _EXTERNAL_DATA_AVAILABLE and not _is_backtest:
            try:
                ext = get_external_data_for_snapshot()
                if ext:
                    snapshot_data.update(ext)
                    logger.info("[MULTI-AGENT] External data injected: %s",
                                ", ".join(ext.keys()))
            except Exception as e:
                logger.warning("[MULTI-AGENT] External data injection failed: %s", e)

        # ── Build enriched context for all agents ──────────────
        enriched_parts = []

        # Extract symbol from snapshot for context
        _enrich_symbol = ""
        _markets = snapshot_data.get("m", [])
        if _markets and isinstance(_markets, list) and _markets:
            _enrich_symbol = _markets[0].get("s", _markets[0].get("sym", ""))

        # ── Inject market depth (F1: L2 spread/depth/imbalance/tape) ──
        # Scoped to the decision's symbol (~300 tokens/decision budget).
        # Skip in backtest: depth reads LIVE collector data (look-ahead); in
        # replay the file is absent for historical windows and the module
        # auto-mutes on staleness anyway.
        if _MARKET_DEPTH_AVAILABLE and not _is_backtest:
            try:
                _depth = get_depth_for_snapshot(
                    symbols=[_enrich_symbol] if _enrich_symbol else None
                )
                if _depth:
                    snapshot_data.update(_depth)
                    logger.info("[MULTI-AGENT] Market depth injected: %s",
                                ", ".join(_depth.keys()))
            except Exception as e:
                logger.warning("[MULTI-AGENT] Market depth injection failed: %s", e)

        # Technical indicators (needs ohlcv_1h in snapshot)
        if _TECHNICALS_AVAILABLE:
            try:
                # Prefer per-symbol OHLCV matching the enrichment symbol
                _ohlcv = None
                _ohlcv_all = snapshot_data.get("ohlcv_by_symbol_1h", {})
                if _enrich_symbol and _ohlcv_all.get(_enrich_symbol):
                    _ohlcv = _ohlcv_all[_enrich_symbol]
                if not _ohlcv:
                    _ohlcv = snapshot_data.get("ohlcv_1h")
                if _ohlcv:
                    techs = compute_all_technicals(_ohlcv)
                    if techs:
                        tech_text = format_technicals_for_agent(techs, _enrich_symbol)
                        if tech_text:
                            enriched_parts.append(tech_text)
            except Exception as e:
                logger.debug("[MULTI-AGENT] Technicals enrichment failed: %s", e)

            # 5m micro-structure technicals (shorter-term signals)
            try:
                _ohlcv_5m = None
                _ohlcv_5m_all = snapshot_data.get("ohlcv_by_symbol_5m", {})
                if _enrich_symbol and _ohlcv_5m_all.get(_enrich_symbol):
                    _ohlcv_5m = _ohlcv_5m_all[_enrich_symbol]
                if not _ohlcv_5m:
                    _ohlcv_5m = snapshot_data.get("ohlcv_5m")
                if _ohlcv_5m:
                    techs_5m = compute_all_technicals(_ohlcv_5m)
                    if techs_5m:
                        tech_text_5m = format_technicals_for_agent(
                            techs_5m, _enrich_symbol, timeframe="5m"
                        )
                        if tech_text_5m:
                            enriched_parts.append(tech_text_5m)
            except Exception as e:
                logger.debug("[MULTI-AGENT] 5m technicals enrichment failed: %s", e)

            # 4h intermediate-trend technicals (structural alignment context)
            try:
                _ohlcv_4h = snapshot_data.get("ohlcv_4h")
                if _ohlcv_4h is not None:
                    techs_4h = compute_all_technicals(_ohlcv_4h)
                    if techs_4h:
                        tech_text_4h = format_technicals_for_agent(
                            techs_4h, _enrich_symbol, timeframe="4h"
                        )
                        if tech_text_4h:
                            enriched_parts.append(tech_text_4h)
            except Exception as e:
                logger.debug("[MULTI-AGENT] 4h technicals enrichment failed: %s", e)

        # Mechanical regime overlay (RQ10, env-gated): compute the validated
        # ATR-ptile/ADX hybrid nowcast and stash it for the Regime agent's
        # INPUT (labeled context, additive — the agent still decides).
        if os.getenv("MECH_REGIME_OVERLAY", "").lower() in ("true", "1", "yes"):
            try:
                from llm.agents.mech_regime import compute_mech_regime
                _mech_ohlcv = None
                _mech_all = snapshot_data.get("ohlcv_by_symbol_1h", {})
                if _enrich_symbol and _mech_all.get(_enrich_symbol):
                    _mech_ohlcv = _mech_all[_enrich_symbol]
                if _mech_ohlcv is None:
                    _mech_ohlcv = snapshot_data.get("ohlcv_1h")
                _mech = compute_mech_regime(_mech_ohlcv)
                if _mech:
                    snapshot_data["mech_regime"] = _mech
                    logger.info(
                        "[MULTI-AGENT] Mech regime overlay: %s (ADX=%s, ATR ptile=%s)",
                        _mech.get("label"), _mech.get("adx"), _mech.get("atr_ptile"),
                    )
            except Exception as e:
                logger.debug("[MULTI-AGENT] Mech regime overlay failed: %s", e)

        # Strip raw OHLCV arrays after technicals computed — saves ~1800 tokens per call
        for _ohlcv_key in ["ohlcv_1h", "ohlcv_5m", "ohlcv_4h", "ohlcv_by_symbol_1h", "ohlcv_by_symbol_5m"]:
            snapshot_data.pop(_ohlcv_key, None)

        # Mark price / basis — inject when available (live mode only)
        # basis_pct = (oracle - mark) / mark: negative = longs overloaded (overheated),
        # positive = shorts overloaded (capitulation/oversold)
        if not _is_backtest:
            _mark = snapshot_data.get("mark_price")
            _basis = snapshot_data.get("basis_pct")
            if _mark is not None:
                _basis_str = ""
                if _basis is not None:
                    if _basis < -0.1:
                        _interp = f"mark {abs(_basis):.3f}% above oracle — longs overloaded"
                    elif _basis > 0.1:
                        _interp = f"mark {abs(_basis):.3f}% below oracle — shorts overloaded"
                    else:
                        _interp = "mark near oracle — neutral"
                    _basis_str = f" ({_interp})"
                enriched_parts.append(f"Mark price: ${_mark:,.2f}{_basis_str}")

        # OI history trend (live rolling window — skip in backtest)
        if not _is_backtest:
            _oi_hist = snapshot_data.get("oi_history")
            if _oi_hist:
                try:
                    _oi_vals = [e["oi"] for e in _oi_hist if isinstance(e, dict) and "oi" in e]
                    if len(_oi_vals) >= 2:
                        _oi_first, _oi_last = _oi_vals[0], _oi_vals[-1]
                        _oi_chg = (_oi_last - _oi_first) / _oi_first * 100 if _oi_first else 0
                        _oi_dir = "expanding" if _oi_chg > 2 else "contracting" if _oi_chg < -2 else "flat"
                        def _fmt_oi(v):
                            return f"${v/1e9:.2f}B" if v >= 1e8 else f"${v/1e6:.0f}M"
                        _mid = len(_oi_vals) // 2
                        _oi_str = " → ".join(_fmt_oi(v) for v in [_oi_vals[0], _oi_vals[_mid], _oi_vals[-1]])
                        _oi_note = ""
                        if abs(_oi_chg) > 10:
                            _oi_note = " — strong accumulation" if _oi_chg > 0 else " — strong distribution"
                        elif abs(_oi_chg) > 4:
                            _oi_note = " — accumulation" if _oi_chg > 0 else " — distribution"
                        enriched_parts.append(
                            f"OI trend: {_oi_dir} — {_oi_str} ({_oi_chg:+.1f}%{_oi_note})"
                        )
                except Exception as _e:
                    logger.debug("[MULTI-AGENT] OI history format failed: %s", _e)

        # Funding rate (valid in both live and backtest — reflects period's actual rate)
        _fr = snapshot_data.get("funding_rate")
        if _fr is not None:
            try:
                _fr_pct = float(_fr) * 100  # Stored as decimal e.g. 0.0005 → 0.05%
                if _fr_pct > 0.02:
                    _fr_interp = "longs pay — crowded long, mean-reversion risk"
                elif _fr_pct < -0.02:
                    _fr_interp = "shorts pay — crowded short, short squeeze risk"
                else:
                    _fr_interp = "near neutral"
                enriched_parts.append(f"Funding: {_fr_pct:+.4f}%/8h ({_fr_interp})")
            except Exception as _e:
                logger.debug("[MULTI-AGENT] Funding rate format failed: %s", _e)

        # Time-of-day session context (live + backtest if hour available)
        _hour = snapshot_data.get("time_utc_hour")
        if _hour is not None:
            try:
                _h = int(_hour)
                if 0 <= _h < 24:
                    if 8 <= _h < 12:
                        _sess = "London open"
                        _sess_note = "high directional momentum, trend initiation — prime setup window"
                    elif 12 <= _h < 17:
                        _sess = "NY session"
                        _sess_note = "peak liquidity, strong follow-through, best for momentum"
                    elif 17 <= _h < 21:
                        _sess = "NY afternoon"
                        _sess_note = "liquidity draining, choppy — reduce size, prefer mean-reversion"
                    elif 0 <= _h < 4:
                        _sess = "Asia early"
                        _sess_note = "low volume, range-bound — avoid breakout plays"
                    else:
                        _sess = "Asia/crossover"
                        _sess_note = "moderate activity"
                    _dow = snapshot_data.get("day_of_week")
                    _day_note = ""
                    if _dow is not None and int(_dow) >= 5:
                        _day_note = " | WEEKEND: reduced liquidity, fade extremes"
                    enriched_parts.append(f"Session: {_h:02d}:00 UTC — {_sess} ({_sess_note}){_day_note}")
            except Exception as _e:
                logger.debug("[MULTI-AGENT] Session context format failed: %s", _e)

        # External data (funding, OI, liquidation) — formatted text
        # Skip in backtest: fetches live current rates (May 2026), not historical
        # April data — injects present-day market state into past-window context.
        if _EXTERNAL_DATA_AVAILABLE and not _is_backtest:
            try:
                # Use the config-derived default (includes XRP and any future symbols) so
                # every tracked symbol's funding/OI/divergence reaches the agent prompt.
                ext_text = format_external_data()
                if ext_text:
                    enriched_parts.append(ext_text)
            except Exception as e:
                logger.debug("[MULTI-AGENT] External data text enrichment failed: %s", e)

        # Feedback loop states (strategy weights, Kelly, adaptive risk, tuner)
        # Skip in backtest: adaptive-risk multiplier is derived from unfiltered live
        # trades and would penalise fresh LLM evaluations with stale loss-streak data.
        if _FEEDBACK_STATE_AVAILABLE and not _is_backtest:
            try:
                fb_text = format_feedback_for_agent()
                if fb_text:
                    enriched_parts.append(fb_text)
            except Exception as e:
                logger.debug("[MULTI-AGENT] Feedback state enrichment failed: %s", e)

        # Pipeline telemetry (recent gate decisions)
        # Pipeline telemetry: recent live gate decisions — skip in backtest to avoid
        # injecting May 2026 signal rejections into the April 23-28 context window.
        if _TELEMETRY_AVAILABLE and not _is_backtest:
            try:
                tel_text = get_telemetry().format_for_llm(
                    symbol=_enrich_symbol, last_n=5
                )
                if tel_text:
                    enriched_parts.append(f"PIPELINE:\n{tel_text}")
            except Exception as e:
                logger.debug("[MULTI-AGENT] Telemetry enrichment failed: %s", e)

        # Position enrichment (rich position state for agents)
        if _POSITION_ENRICHMENT_AVAILABLE:
            try:
                _positions = snapshot_data.get("pos", {})
                _prices = {}
                for _mk in _markets:
                    _mk_sym = _mk.get("s", _mk.get("sym", ""))
                    _mk_price = _mk.get("p", _mk.get("price", 0))
                    if _mk_sym and _mk_price:
                        _prices[_mk_sym] = _mk_price
                if _positions:
                    pos_text = format_positions_for_agent(_positions, _prices)
                    if pos_text:
                        enriched_parts.append(pos_text)
            except Exception as e:
                logger.debug("[MULTI-AGENT] Position enrichment failed: %s", e)

        # Portfolio-level intelligence (exposure, correlation, risk budget)
        if _PORTFOLIO_INTEL_AVAILABLE:
            try:
                _positions = snapshot_data.get("pos", {})
                _prices = {}
                for _mk in _markets:
                    _mk_sym = _mk.get("s", _mk.get("sym", ""))
                    _mk_price = _mk.get("p", _mk.get("price", 0))
                    if _mk_sym and _mk_price:
                        _prices[_mk_sym] = _mk_price
                _equity = float(snapshot_data.get("g", {}).get("equity",
                                snapshot_data.get("g", {}).get("eq", 0)))
                if _equity > 0:
                    _port_state = compute_portfolio_state(_positions, _prices, _equity)
                    snapshot_data["_portfolio_state"] = _port_state
                    _port_text = format_portfolio_for_agent(_port_state)
                    if _port_text:
                        enriched_parts.append(_port_text)
            except Exception as e:
                logger.debug("[MULTI-AGENT] Portfolio intelligence failed: %s", e)

        # Agent self-performance: network-wide health summary for all agents
        if _AGENT_PERF_AVAILABLE:
            try:
                _perf_tracker = get_perf_tracker()
                _net_summary = _perf_tracker.format_network_summary()
                if _net_summary:
                    enriched_parts.append(_net_summary)
                # Store tracker reference on self (not in snapshot_data which gets JSON-serialized)
                self._perf_tracker_ref = _perf_tracker
                snapshot_data["_perf_tracker_summary"] = _net_summary or ""
            except Exception as e:
                logger.debug("[MULTI-AGENT] Agent performance enrichment failed: %s", e)

        # Background thinker journal (market observations, patterns, opportunities)
        # Skip in backtest: journal contains observations about the live current market
        # (May 2026), not the historical backtest window — look-ahead bias.
        if _BACKGROUND_THINKER_AVAILABLE and not _is_backtest:
            try:
                if self._background_thinker is None:
                    self._background_thinker = BackgroundThinker()
                journal_text = self._background_thinker.get_journal_for_agents(last_n=5)
                if journal_text:
                    enriched_parts.append(journal_text)
            except Exception as e:
                logger.debug("[MULTI-AGENT] Background thinker enrichment failed: %s", e)

        # GAP 1: Execution quality / slippage metrics
        # Skip in backtest: execution quality is computed from live trading fills that
        # post-date the backtest window — look-ahead bias.
        if _HAS_EXEC_QUALITY and not _is_backtest:
            try:
                eq_summary = get_execution_quality_summary()
                if eq_summary:
                    enriched_parts.append(eq_summary)
            except Exception as e:
                logger.debug("[MULTI-AGENT] Execution quality enrichment failed: %s", e)

        # GAP 2: Reflection engine (move exhaustion, re-entry patterns, trade quality)
        # Skip in backtest: reflection reads from closed live trades post-dating the window.
        if _HAS_REFLECTION and not _is_backtest:
            try:
                if not hasattr(self, '_reflection_engine') or self._reflection_engine is None:
                    self._reflection_engine = ReflectionEngine()
                refl_summary = self._reflection_engine.get_summary_for_agents()
                if refl_summary:
                    enriched_parts.append(refl_summary)
            except Exception as e:
                logger.debug("[MULTI-AGENT] Reflection engine enrichment failed: %s", e)

        # GAP 3: ML/RL predictions (direction probability, strategy win rates)
        try:
            _ml_data = snapshot_data.get("g", {}).get("ml", {})
            if _ml_data:
                ml_parts = []
                # Direction model prediction
                if "direction_prob" in _ml_data:
                    dp = _ml_data["direction_prob"]
                    direction = "LONG" if dp > 0.5 else "SHORT"
                    ml_parts.append(f"dir_prob={dp:.2f}({direction})")
                # Strategy win rates from ML
                if "strategy_win_rates" in _ml_data:
                    wr_str = " ".join(
                        f"{k}={v:.0%}" for k, v in _ml_data["strategy_win_rates"].items()
                    )
                    ml_parts.append(f"strat_WR=[{wr_str}]")
                # Strategy weight recommendations
                if "strategy_weights" in _ml_data:
                    sw_str = " ".join(
                        f"{k}={v:.2f}" for k, v in _ml_data["strategy_weights"].items()
                    )
                    ml_parts.append(f"ML_weights=[{sw_str}]")
                # Snapshot model sample count
                if "snapshot_model_samples" in _ml_data:
                    ml_parts.append(f"snap_samples={_ml_data['snapshot_model_samples']}")
                if ml_parts:
                    enriched_parts.append("ML: " + " | ".join(ml_parts))
        except Exception as e:
            logger.debug("[MULTI-AGENT] ML predictions enrichment failed: %s", e)

        # Network learning: inject accumulated lessons from past trades
        # Skip in backtest: lessons are derived from live trades that post-date the
        # backtest window — injects look-ahead bias even as qualitative heuristics.
        if not _is_backtest:
            try:
                from llm.agents.network_learning import get_network_learning
                _nl = get_network_learning()
                # Inject per-agent lessons into snapshot for downstream builders
                _nl_trade = _nl.get_prompt_injection("trade")
                _nl_risk = _nl.get_prompt_injection("risk")
                _nl_regime = _nl.get_regime_intelligence()
                _nl_critic = _nl.get_prompt_injection("critic")
                if _nl_trade:
                    snapshot_data["network_lessons_trade"] = _nl_trade
                if _nl_risk:
                    snapshot_data["network_lessons_risk"] = _nl_risk
                    snapshot_data["risk_constraints"] = _nl.get_risk_constraints()
                if _nl_regime:
                    enriched_parts.append(_nl_regime)
                if _nl_critic:
                    snapshot_data["network_lessons_critic"] = _nl_critic
                # Calibration adjustment for Quant Agent
                _cal_adj = _nl.get_calibration_adjustment()
                if _cal_adj != 0:
                    snapshot_data["network_calibration_adj"] = round(_cal_adj, 4)
                # Edge decay alerts for Overseer
                _decaying = _nl.get_decaying_edges()
                if _decaying:
                    snapshot_data["edge_decay_alerts"] = _decaying
            except Exception as e:
                logger.debug("[MULTI-AGENT] Network learning injection failed: %s", e)

        # Dynamic stats: live rolling WR, PF, regime performance, calibration
        # Replaces hardcoded historical stats in prompts with current data.
        # Skip in backtest: live WR stats are from the fallback-approve era (unfiltered
        # trades) and would poison the agent with regime/setup WRs near 0-14%.
        if not _is_backtest:
            try:
                from llm.agents.dynamic_stats import get_all_dynamic_stats
                _dyn_stats = get_all_dynamic_stats()
                if _dyn_stats:
                    enriched_parts.append(_dyn_stats)
                    snapshot_data["_enr_dynamic_stats"] = _dyn_stats
            except Exception as e:
                logger.debug("[MULTI-AGENT] Dynamic stats enrichment failed: %s", e)

        # Self-teaching knowledge base: axioms, principles, hypotheses, anti-patterns
        # Skip in backtest: knowledge base is built from live trading outcomes that
        # post-date the backtest window — look-ahead bias (Bug #16).
        if not _is_backtest:
            try:
                from llm.self_teaching import get_teaching_engine
                _teach = get_teaching_engine()
                _knowledge_text = _teach.get_knowledge_for_prompt(
                    symbol=_enrich_symbol, regime=""
                )
                if _knowledge_text:
                    enriched_parts.append(f"KNOWLEDGE BASE:\n{_knowledge_text}")
                    snapshot_data["_enr_knowledge"] = _knowledge_text

                # Curriculum level: what the LLM should be focused on learning
                _curriculum = _teach.get_curriculum_report()
                if _curriculum:
                    _level = _curriculum.get("current_level", 1)
                    _level_name = _curriculum.get("level_name", "PATTERN_RECOGNITION")
                    _focus = _curriculum.get("focus", "")
                    snapshot_data["curriculum_level"] = _level
                    snapshot_data["curriculum_focus"] = _focus
                    if _level >= 3:  # Level 3+ has predictive capability
                        enriched_parts.append(
                            f"CURRICULUM: Level {_level} ({_level_name}) — {_focus}"
                        )
            except Exception as e:
                logger.debug("[MULTI-AGENT] Self-teaching knowledge injection failed: %s", e)

        # Neuroplasticity: setup edge strengths, decay alerts, surprises
        # Skip in backtest: neuro weights are learned from live trades that post-date
        # the backtest window — look-ahead bias (Bug #16).
        if not _is_backtest:
            try:
                from llm.neuroplasticity import get_neuro_context_for_agents
                _neuro = get_neuro_context_for_agents(
                    symbol=_enrich_symbol,
                    side="",  # All sides
                )
                if _neuro:
                    enriched_parts.append(f"NEURO:\n{_neuro}")
                    snapshot_data["_enr_neuro"] = _neuro
            except Exception as e:
                logger.debug("[MULTI-AGENT] Neuroplasticity context failed: %s", e)

        enriched_context = "\n\n".join(enriched_parts) if enriched_parts else ""
        if enriched_context:
            snapshot_data["enriched_context"] = enriched_context
            logger.info("[MULTI-AGENT] Enriched context: %d chars from %d sources",
                        len(enriched_context), len(enriched_parts))

        # Also store each enrichment as a SEPARATE named key for structured agent access
        # (agents can read individual fields instead of parsing one blob)
        _loc = locals()
        for _var, _key in [
            ("tech_text", "_enr_tech"), ("tech_text_5m", "_enr_tech_5m"),
            ("fb_text", "_enr_feedback"), ("tel_text", "_enr_pipeline"),
            ("pos_text", "_enr_positions"), ("_port_text", "_enr_portfolio"),
            ("journal_text", "_enr_journal"), ("eq_summary", "_enr_exec"),
            ("refl_summary", "_enr_reflection"),
        ]:
            _val = _loc.get(_var)
            if _val:
                snapshot_data[_key] = _val

        # ── Step 1: Regime Agent (cached — 30 min TTL) ─────────
        _regime_symbol = ""
        _regime_markets = snapshot_data.get("m", [])
        if _regime_markets and isinstance(_regime_markets, list) and _regime_markets:
            _regime_symbol = _regime_markets[0].get("s", _regime_markets[0].get("sym", ""))

        with self._cache_lock:
            _cached = self._regime_cache.get(_regime_symbol)
            _cache_fresh = bool(
                _cached
                and _regime_symbol
                and (time.time() - _cached["timestamp"]) < self._regime_cache_ttl
            )
        if _cache_fresh:
            regime_out = _cached["result"]
            logger.info(
                f"[MULTI-AGENT] Regime cache HIT for {_regime_symbol} "
                f"(age={time.time() - _cached['timestamp']:.0f}s)"
            )
        else:
            regime_input = self._build_regime_input(snapshot_data)
            regime_out = self._call_agent(
                AgentRole.REGIME, regime_input, model_for_trigger
            )
            # Cache successful results
            if regime_out.ok and _regime_symbol:
                with self._cache_lock:
                    self._regime_cache[_regime_symbol] = {
                        "result": regime_out,
                        "timestamp": time.time(),
                    }
                logger.info(
                    f"[MULTI-AGENT] Regime cache MISS for {_regime_symbol} — cached new result"
                )

        pipeline_results[AgentRole.REGIME] = regime_out

        if not regime_out.ok:
            if self.configs[AgentRole.REGIME].required:
                logger.warning("[MULTI-AGENT] Regime agent failed — aborting pipeline")
                self.last_pipeline_results = pipeline_results
                return None
            # Fallback: unknown regime
            regime_out = AgentOutput(
                role=AgentRole.REGIME,
                data={"rg": "unknown", "conf": 0.3, "factors": "regime agent failed",
                      "bias": "neutral", "transition": "uncertain"},
            )

        # Technical regime fallback: if LLM returns "unknown", classify from market data
        if regime_out.data.get("rg", "unknown") == "unknown":
            try:
                _fb_regime = self._compute_regime_fallback(snapshot_data)
                if _fb_regime and _fb_regime != "unknown":
                    regime_out.data["rg"] = _fb_regime
                    regime_out.data["factors"] = f"technical_fallback: {_fb_regime}"
                    regime_out.data["conf"] = max(regime_out.data.get("conf", 0.3), 0.5)
                    logger.info(f"[MULTI-AGENT] Regime fallback: unknown → {_fb_regime}")
            except Exception as e:
                logger.debug(f"[MULTI-AGENT] Regime fallback error: {e}")

        # Write regime output to scratchpad for downstream agents
        scratchpad.write("regime", "regime", regime_out.data.get("rg", "unknown"))
        scratchpad.write("regime", "regime_conf", regime_out.data.get("conf", 0.5))
        scratchpad.write("regime", "bias", regime_out.data.get("bias", "neutral"))
        if regime_out.data.get("outlook"):
            scratchpad.write("regime", "outlook", regime_out.data["outlook"])
        # Gap 3: Regime transition prediction fields
        if regime_out.data.get("regime_momentum"):
            scratchpad.write("regime", "regime_momentum", regime_out.data["regime_momentum"])
        if regime_out.data.get("expected_duration_h"):
            scratchpad.write("regime", "expected_duration_h", regime_out.data["expected_duration_h"])

        # ── Step 1.25: Tiered Pipeline Router — DELETED (R12, FULL_PIPE map, 2026-07-02) ──
        # Tier-1 auto-flat decided "no LLM judgment needed" from regime+conf
        # heuristics — an OPINION router that returned a FLAT decision without
        # any agent seeing the signal (flag was default-off but armed). Per the
        # doctrine, call-tiering must be quota-count-based, never signal-quality
        # based, so the heuristic router was removed rather than left armed.

        # ── Step 1.5: Quant Agent ─────────────────────
        quant_out = None
        _quant_enabled = self.configs.get(AgentRole.QUANT, AgentConfig(role=AgentRole.QUANT)).enabled
        if _quant_enabled:
            quant_input = self._build_quant_input(snapshot_data, regime_out)
            quant_out = self._call_agent(
                AgentRole.QUANT, quant_input, model_for_trigger
            )
            pipeline_results[AgentRole.QUANT] = quant_out
            if quant_out and quant_out.ok:
                # Write quant analysis to scratchpad for Trade Agent
                scratchpad.write("quant", "ev", quant_out.data.get("ev", {}))
                if quant_out.data.get("conditional_edge"):
                    scratchpad.write("quant", "conditional_edge", quant_out.data["conditional_edge"])
                if quant_out.data.get("probability"):
                    scratchpad.write("quant", "probability", quant_out.data["probability"])
                if quant_out.data.get("kelly_fraction") is not None:
                    scratchpad.write("quant", "kelly_fraction", quant_out.data["kelly_fraction"])
                if quant_out.data.get("signal_quality"):
                    scratchpad.write("quant", "signal_quality", quant_out.data["signal_quality"])
                if quant_out.data.get("risk_profile"):
                    scratchpad.write("quant", "risk_profile", quant_out.data["risk_profile"])
            else:
                quant_out = None  # Degrade gracefully

        # ── Step 1.75: Pre-Trade Simulator (scenario analysis) ──
        if _SIMULATOR_AVAILABLE:
            try:
                if self._pre_trade_simulator is None:
                    self._pre_trade_simulator = PreTradeSimulator()
                # Extract signal data from snapshot to simulate
                _sim_signals = snapshot_data.get("signals", [])
                if not _sim_signals and _markets:
                    # Try extracting from market data
                    for _mk in (_markets if isinstance(_markets, list) else []):
                        _sigs = _mk.get("sg", _mk.get("sigs", []))
                        if _sigs:
                            _sim_signals = _sigs
                            break
                if _sim_signals and isinstance(_sim_signals, list) and _sim_signals:
                    _sig = _sim_signals[0] if isinstance(_sim_signals[0], dict) else {}
                    _sim_side = _sig.get("side", _sig.get("sd", ""))
                    _sim_entry = float(_sig.get("entry", _sig.get("e", 0)))
                    _sim_sl = float(_sig.get("sl", 0))
                    _sim_tp1 = float(_sig.get("tp1", 0))
                    _sim_lev = float(_sig.get("leverage", _sig.get("lev", 5)))
                    if _sim_side and _sim_entry > 0 and _sim_sl > 0 and _sim_tp1 > 0:
                        _sim_portfolio = snapshot_data.get("pos", {})
                        _sim_market = dict(snapshot_data.get("g", {}))
                        _sim_market["equity"] = float(
                            snapshot_data.get("g", {}).get("equity",
                            snapshot_data.get("g", {}).get("eq", 1000))
                        )
                        _sim_result = self._pre_trade_simulator.simulate(
                            symbol=_enrich_symbol or _sig.get("sym", _sig.get("s", "")),
                            side=_sim_side,
                            entry=_sim_entry,
                            sl=_sim_sl,
                            tp1=_sim_tp1,
                            leverage=_sim_lev,
                            current_portfolio=_sim_portfolio,
                            market_data=_sim_market,
                        )
                        if _sim_result:
                            _sim_text = self._pre_trade_simulator.format_for_agent(_sim_result)
                            if _sim_text:
                                snapshot_data["_simulation"] = _sim_text
                                logger.info("[MULTI-AGENT] Pre-trade simulation: EV=$%.2f rec=%s",
                                            _sim_result.get("expected_value", 0),
                                            _sim_result.get("recommendation", "?"))
            except Exception as e:
                logger.debug("[MULTI-AGENT] Pre-trade simulation failed: %s", e)

        # ── Step 2: Trade Agent ─────────────────────────────────
        # Cost optimization: default to Haiku for Trade Agent, promote to
        # Sonnet only when signal quality warrants it (saves ~4x per call).
        trade_model_for_trigger = model_for_trigger
        try:
            from llm.usage_tiers import MODEL_HAIKU, MODEL_SONNET
            _regime = regime_out.data.get("rg", "unknown") if regime_out and regime_out.ok else "unknown"
            _sig_conf = 0.0
            _sig_n_agree = 0
            for _mkt in (snapshot_data.get("m", []) or []):
                if not isinstance(_mkt, dict):
                    continue
                for _s in (_mkt.get("sg") or _mkt.get("sigs") or []):
                    if isinstance(_s, dict):
                        _sig_conf = max(_sig_conf, float(_s.get("confidence", _s.get("c", 0))))
                        _sig_n_agree = max(_sig_n_agree, int(_s.get("num_agree", _s.get("na", 1))))
            _promote_to_sonnet = (
                _sig_n_agree >= 2
                or _sig_conf >= 75
                or _regime in ("trending_bear", "trending_bull")
            )
            if _promote_to_sonnet:
                trade_model_for_trigger = MODEL_SONNET
                logger.info(
                    f"[COST] Trade Agent → Sonnet (n_agree={_sig_n_agree} "
                    f"conf={_sig_conf:.0f} regime={_regime})"
                )
            else:
                trade_model_for_trigger = MODEL_HAIKU
                logger.info(
                    f"[COST] Trade Agent → Haiku (n_agree={_sig_n_agree} "
                    f"conf={_sig_conf:.0f} regime={_regime})"
                )
        except Exception as e:
            logger.debug(f"[COST] Trade model routing failed: {e}")

        trade_input = self._build_trade_input(snapshot_data, regime_out, quant_out)

        # Trade Agent timeout + fallback: if Sonnet times out (>90s), retry immediately on Haiku
        trade_out = self._call_agent(
            AgentRole.TRADE, trade_input, trade_model_for_trigger
        )

        # If Trade Agent was trying Sonnet and timed out, retry on Haiku
        if (not trade_out.ok and
            trade_model_for_trigger == MODEL_SONNET and
            ("timeout" in str(trade_out.error).lower() or
             "session limit" in str(trade_out.error).lower())):
            logger.warning("[MULTI-AGENT] Trade Agent Sonnet timeout/session limit (>90s) — falling back to Haiku")
            from llm.usage_tiers import MODEL_HAIKU
            trade_out = self._call_agent(
                AgentRole.TRADE, trade_input, MODEL_HAIKU
            )

        pipeline_results[AgentRole.TRADE] = trade_out

        if not trade_out.ok:
            if self.configs[AgentRole.TRADE].required:
                logger.warning("[MULTI-AGENT] Trade agent failed — aborting pipeline")
                self.last_pipeline_results = pipeline_results
                return None
            # Fallback: skip
            trade_out = AgentOutput(
                role=AgentRole.TRADE,
                data={"a": "skip", "c": 0.0, "n": "trade agent failed"},
            )

        # Write trade output to scratchpad for downstream agents
        scratchpad.write("trade", "action", trade_out.data.get("a", "skip"))
        scratchpad.write("trade", "confidence", trade_out.data.get("c", 0.0))
        if trade_out.data.get("thesis"):
            scratchpad.write("trade", "thesis", trade_out.data["thesis"])

        # ── Step 3: Risk Agent (optional) ───────────────────────
        risk_out = None
        if self.configs.get(AgentRole.RISK, AgentConfig(role=AgentRole.RISK)).enabled:
            risk_input = self._build_risk_input(snapshot_data, regime_out, trade_out, quant_out)
            risk_out = self._call_agent(
                AgentRole.RISK, risk_input, model_for_trigger
            )
            pipeline_results[AgentRole.RISK] = risk_out
            if not risk_out.ok:
                risk_out = None  # Degrade gracefully

        # ── Step 4: Critic Agent (optional) ─────────────────────
        # High-stakes trades get structured debate (Critic R1 without
        # confidence to prevent anchoring + Trade rebuttal round).
        # Non-high-stakes trades use the cheaper simple Critic call.
        critic_out = None
        _structured_debate_result = None
        if self.configs.get(AgentRole.CRITIC, AgentConfig(role=AgentRole.CRITIC)).enabled:
            _high_stakes = self._is_high_stakes_trade(trade_out, risk_out, snapshot_data)

            if _high_stakes:
                # ── Structured Debate (2-round, de-anchored) ──────
                logger.info("[MULTI-AGENT] High-stakes trade detected — running structured debate")
                try:
                    critic_out, _structured_debate_result = self._run_structured_debate(
                        trade_out, regime_out, risk_out, snapshot_data, model_for_trigger
                    )
                except Exception as e:
                    logger.warning("[DEBATE] Structured debate failed: %s — falling back to simple critic", e)
                    critic_out = None
                    _structured_debate_result = None

            if critic_out is None:
                # ── Simple Critic (non-high-stakes or debate fallback) ──
                critic_input = self._build_critic_input(
                    snapshot_data, regime_out, trade_out, risk_out
                )
                critic_out = self._call_agent(
                    AgentRole.CRITIC, critic_input, model_for_trigger
                )

            if not critic_out.ok:
                # ── R13 (FULL_PIPE map, 2026-07-02): one Critic retry ──
                # The old "mechanical critic fallback" substituted a hardcoded
                # conf floor + counter-trend check for the missing agent — an
                # OPINION judging the signal on exactly the path where nobody
                # reviews it. Replaced: retry once; if still failing, mark the
                # decision degraded and let the agents that DID run stand.
                logger.warning("[MULTI-AGENT] Critic call failed — retrying once")
                critic_input = self._build_critic_input(
                    snapshot_data, regime_out, trade_out, risk_out
                )
                critic_out = self._call_agent(
                    AgentRole.CRITIC, critic_input, model_for_trigger
                )

            pipeline_results[AgentRole.CRITIC] = critic_out
            if not critic_out.ok:
                critic_out = None
                _structured_debate_result = None  # Debate result invalid without critic
                # Degraded pipeline: no mechanical judgment substitutes for the
                # missing agent. _merge_outputs applies a 0.5x size cap — a
                # physics-style degradation cap (reduced review capacity), not
                # an opinion about the signal. Stamp rides into notes →
                # decisions.jsonl + thesis record.
                logger.warning(
                    "[MULTI-AGENT] Critic failed after retry — degraded=true, "
                    "size capped 0.5x (degradation cap, not judgment)"
                )
                scratchpad.write("critic", "call_degraded", True)

        # ── Consistency Check ──────────────────────────────────────
        consistency_report = check_pipeline_consistency(
            regime_data=regime_out.data if regime_out.ok else {},
            trade_data=trade_out.data if trade_out.ok else {},
            risk_data=risk_out.data if risk_out and risk_out.ok else None,
            critic_data=critic_out.data if critic_out and critic_out.ok else None,
        )
        get_consistency_tracker().record(consistency_report)

        if not consistency_report.is_consistent:
            logger.warning(
                f"[MULTI-AGENT] Pipeline inconsistency detected: "
                f"{consistency_report.summary()}"
            )
            # On critical inconsistency: override action to skip but preserve
            # a fraction of the agent's confidence for downstream analysis.
            # Previously zeroed confidence, losing all signal information.
            critical_issues = [
                i for i in consistency_report.issues if i.severity == "critical"
            ]
            if critical_issues:
                original_conf = float(
                    trade_out.data.get("c", trade_out.data.get("confidence", 0.0))
                )
                logger.warning(
                    f"[MULTI-AGENT] Critical issues found — overriding to skip "
                    f"(original conf={original_conf:.2f}): "
                    f"{[i.description[:80] for i in critical_issues]}"
                )
                trade_out = AgentOutput(
                    role=AgentRole.TRADE,
                    data={
                        "a": "skip",
                        "c": original_conf * 0.5,  # halve instead of zero
                        "n": f"consistency_override: {critical_issues[0].description[:100]}",
                    },
                )

        # ── Quant Agent confidence adjustment — ADVISORY+SHADOW ─────────────
        # DECHOKE 5 (2026-07-02, FALLACY_AUDIT D8 + GM_AGENT_SKILL_24K — owner-
        # approved): the Quant Agent was the only enforcer with ZERO accuracy
        # gating (Risk and Critic both have one) while its measured signal is
        # a pure week-1 artifact (shorts wk1 +311bps → mid −22 → late −1) and
        # its noise-tag has zero separating power (|move| 106.5 vs 104.5bps).
        # Its ±0.15 confidence mutations, forced skips, and size reductions no
        # longer mutate the pipeline: they are shadow-logged ([SHADOW-QUANT])
        # for dollar scoring, and its assessment still reaches the Risk agent
        # as labeled advisory input via _build_risk_input (provenance intact).
        # Kill-switch: QUANT_AGENT_ENFORCE=true restores enforcement — only
        # after n>=13 dollar-positive shadow validation per THE_STANDARD §2b.
        _quant_enforce = os.getenv("QUANT_AGENT_ENFORCE", "false").lower() == "true"
        _consistency_overrode = "consistency_override" in trade_out.data.get("n", "")
        if quant_out and quant_out.ok and not _consistency_overrode:
            sq_raw = quant_out.data.get("signal_quality", {})
            sq = sq_raw if isinstance(sq_raw, dict) else {}
            quant_adj = sq.get("confidence_adjustment", 0)
            if quant_adj and isinstance(quant_adj, (int, float)):
                # Cap quant adjustment to prevent cascading reductions
                quant_adj = max(-0.15, min(0.15, quant_adj))
                td = trade_out.data
                old_c = float(td.get("c", td.get("confidence", 0.0)))
                new_c = max(0.0, min(1.0, old_c + quant_adj))
                if _quant_enforce:
                    # Update trade_out data in-place for downstream consensus
                    trade_out = AgentOutput(
                        role=AgentRole.TRADE,
                        data={**trade_out.data, "c": new_c,
                              "n": (td.get("n", "") + f" | QUANT_ADJ: {quant_adj:+.2f}")},
                        raw_text=trade_out.raw_text,
                        model_used=trade_out.model_used,
                        input_tokens=trade_out.input_tokens,
                        output_tokens=trade_out.output_tokens,
                        latency_ms=trade_out.latency_ms,
                    )
                else:
                    logger.info(
                        f"[SHADOW-QUANT] would_adjust_conf {old_c:.2f}->{new_c:.2f} "
                        f"(adj={quant_adj:+.2f}; advisory only, QUANT_AGENT_ENFORCE=true re-enables)"
                    )
            # Noise verdict: graduated response (hard skip / size reduce).
            # Support both old is_noise (bool) and new noise_probability (float)
            _noise_prob = sq.get("noise_probability", 1.0 if sq.get("is_noise") else 0.0)
            if _noise_prob > 0.6:
                trade_conf = float(trade_out.data.get("c", 0))
                noise_reason = sq.get("reason", "statistical noise")
                if trade_conf < 0.20:
                    if _quant_enforce:
                        trade_out = AgentOutput(
                            role=AgentRole.TRADE,
                            data={**trade_out.data, "a": "skip", "c": 0.0,
                                  "n": f"QUANT_NOISE: {noise_reason}"},
                            raw_text=trade_out.raw_text,
                            model_used=trade_out.model_used,
                        )
                    else:
                        logger.info(
                            f"[SHADOW-QUANT] would_skip (noise_prob={_noise_prob:.2f}, "
                            f"conf={trade_conf:.2f}, reason={str(noise_reason)[:80]})"
                        )
                elif trade_conf < 0.40:
                    td = trade_out.data
                    old_sm = float(td.get("sm", td.get("size_multiplier", 1.0)))
                    if _quant_enforce:
                        # Reduce size but let trade proceed for Critic review
                        trade_out = AgentOutput(
                            role=AgentRole.TRADE,
                            data={**td, "sm": round(old_sm * 0.5, 2),
                                  "n": (td.get("n", "") + f" | QUANT_NOISE_REDUCE: {noise_reason}")},
                            raw_text=trade_out.raw_text,
                            model_used=trade_out.model_used,
                            input_tokens=trade_out.input_tokens,
                            output_tokens=trade_out.output_tokens,
                            latency_ms=trade_out.latency_ms,
                        )
                    else:
                        logger.info(
                            f"[SHADOW-QUANT] would_reduce_size {old_sm:.2f}->"
                            f"{old_sm * 0.5:.2f} (noise_prob={_noise_prob:.2f}, "
                            f"reason={str(noise_reason)[:80]})"
                        )

        # ── Network Learning: apply calibration adjustment ────
        _net_cal = snapshot_data.get("network_calibration_adj", 0) if snapshot_data else 0
        if _net_cal and isinstance(_net_cal, (int, float)) and abs(_net_cal) >= 0.02:
            td = trade_out.data
            old_c = float(td.get("c", td.get("confidence", 0.0)))
            new_c = max(0.0, min(1.0, old_c + _net_cal))
            trade_out = AgentOutput(
                role=AgentRole.TRADE,
                data={**td, "c": new_c,
                      "n": (td.get("n", "") + f" | NET_CAL: {_net_cal:+.2f}")},
                raw_text=trade_out.raw_text,
                model_used=trade_out.model_used,
                input_tokens=trade_out.input_tokens,
                output_tokens=trade_out.output_tokens,
                latency_ms=trade_out.latency_ms,
            )

        # ── Agent Brain: Record decisions for learning ────────
        if _EXTENSIONS_AVAILABLE:
            try:
                _regime = regime_out.data.get("rg", "unknown") if regime_out.ok else "unknown"
                record_agent_decision("regime", regime_out.data, regime=_regime)
                record_agent_decision("trade", trade_out.data, regime=_regime)
                if risk_out and risk_out.ok:
                    record_agent_decision("risk", risk_out.data, regime=_regime)
                if critic_out and critic_out.ok:
                    record_agent_decision("critic", critic_out.data, regime=_regime)
                if quant_out and quant_out.ok:
                    record_agent_decision("quant", quant_out.data, regime=_regime)
            except Exception as e:
                logger.debug(f"[MULTI-AGENT] Brain recording error: {e}")

        # ── Debate: synthesize diverse agent viewpoints ───────
        # Structured debate (from Step 4) takes top priority, then
        # legacy interactive debate, then post-hoc debate scoring.
        debate_outcome = None
        interactive_debate_outcome = None

        # If structured debate already ran in Step 4, use its result
        if _structured_debate_result and _structured_debate_result.get("debate_occurred"):
            interactive_debate_outcome = _structured_debate_result
            scratchpad.write("debate", "structured_debate", _structured_debate_result)
            logger.info(
                "[DEBATE] Using structured debate result: winner=%s adj=%+.1f%%",
                _structured_debate_result.get("winner", "?"),
                _structured_debate_result.get("confidence_adjustment", 0) * 100,
            )

        # Otherwise fall back to legacy debate pipeline
        if not interactive_debate_outcome and _EXTENSIONS_AVAILABLE:
            try:
                _agent_data = {
                    "regime": regime_out.data if regime_out.ok else {},
                    "trade": trade_out.data if trade_out.ok else {},
                    "critic": critic_out.data if critic_out and critic_out.ok else {},
                }
                _sym = ""
                _markets = snapshot_data.get("m", []) if snapshot_data else []
                if _markets and isinstance(_markets[0], dict):
                    _sym = _markets[0].get("s", _markets[0].get("sym", ""))

                # Try interactive debate (env-gated, 2-round, real LLM calls)
                if trade_out.ok and critic_out and critic_out.ok:
                    _risk_data = risk_out.data if risk_out and risk_out.ok else {}
                    interactive_debate_outcome = run_interactive_debate_if_enabled(
                        trade_agent_output=trade_out.data,
                        critic_agent_output=critic_out.data,
                        market_context=snapshot_data or {},
                        risk_assessment=_risk_data,
                        position_size_pct=float(_risk_data.get(
                            "position_size_pct", _risk_data.get("sz_pct", 0)
                        )),
                    )
                    if interactive_debate_outcome:
                        scratchpad.write("debate", "interactive_outcome", interactive_debate_outcome)

                # Fall back to post-hoc debate if interactive not enabled
                if not interactive_debate_outcome:
                    debate_outcome = run_debate_if_warranted(
                        _agent_data,
                        regime=regime_out.data.get("rg", "unknown") if regime_out.ok else "unknown",
                        symbol=_sym,
                    )
                    if debate_outcome:
                        scratchpad.write("debate", "outcome", debate_outcome)
            except Exception as e:
                logger.debug(f"[MULTI-AGENT] Debate error: {e}")

        # ── Confidence Consensus & Consistency Scaling ─────────
        # Gap 1: Compound conviction across agents
        # Gap 7: Consistency score scales confidence
        consensus_conf = _compute_confidence_consensus(
            trade_out, regime_out, risk_out, critic_out, consistency_report.score,
        )
        if consensus_conf is not None:
            # Write to scratchpad for audit trail
            scratchpad.write("system", "consensus_confidence", round(consensus_conf, 3))

        # Apply debate adjustment to consensus confidence
        if _EXTENSIONS_AVAILABLE and consensus_conf is not None:
            # Interactive debate takes precedence if available
            if interactive_debate_outcome:
                # Use debate resolution's final confidence
                if "final_confidence" in interactive_debate_outcome:
                    consensus_conf = interactive_debate_outcome["final_confidence"]
            elif debate_outcome:
                consensus_conf = apply_debate_to_confidence(consensus_conf, debate_outcome)

        # ── Merge into LLMDecision ──────────────────────────────
        decision = self._merge_outputs(
            regime_out, trade_out, risk_out, critic_out, snapshot_data,
            consistency_score=consistency_report.score,
            consensus_confidence=consensus_conf,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._total_latency_ms += elapsed_ms

        agents_called = sum(1 for r in pipeline_results.values() if r.ok)
        consistency_score = consistency_report.score
        self.last_consistency_score = consistency_score
        logger.info(
            f"[MULTI-AGENT] Pipeline done: {agents_called} agents, "
            f"{elapsed_ms}ms total, action={decision.action} "
            f"conf={decision.confidence:.2f} regime={decision.regime} "
            f"consistency={consistency_score:.2f}"
        )

        # Append consistency score to decision notes for audit trail
        if decision.notes and consistency_score < 1.0:
            decision = LLMDecision(
                action=decision.action,
                confidence=decision.confidence,
                regime=decision.regime,
                strategy_weights=decision.strategy_weights,
                memory_update=decision.memory_update,
                notes=f"{decision.notes} | CONSISTENCY={consistency_score:.2f}",
                size_multiplier=decision.size_multiplier,
                entry_adjustment=decision.entry_adjustment,
            )

        # ── Feed decision pipeline into learning systems ────────
        try:
            from llm.agents.learning_integration import process_agent_decision_for_learning
            process_agent_decision_for_learning(
                decision_notes=decision.notes,
                regime_data=regime_out.data if regime_out.ok else {},
                critic_data=critic_out.data if critic_out and critic_out.ok else None,
                trade_context=trigger_reason,
            )
        except Exception as e:
            logger.debug(f"[MULTI-AGENT] Decision learning error: {e}")

        # ── Brain Wiring: thesis tracking + counterfactual + calibration ──
        try:
            from llm.brain_wiring import (
                record_thesis, record_skipped_trade, calibrate_confidence,
            )
            td = trade_out.data
            regime = regime_out.data.get("rg", "unknown") if regime_out.ok else "unknown"

            # Extract symbol: prefer trade agent's own symbol output, fall back to snapshot signal
            _sym = td.get("symbol", td.get("sym", ""))
            _markets = snapshot_data.get("m", []) if snapshot_data else []
            if not _sym:
                _signals = snapshot_data.get("signals", snapshot_data.get("sigs", [])) if snapshot_data else []
                if _signals and isinstance(_signals[0], dict):
                    _sym = _signals[0].get("sym", _signals[0].get("symbol", ""))
            if not _sym and _markets:
                _sym = _markets[0].get("s", _markets[0].get("sym", ""))

            if decision.action in ("go", "proceed"):
                # Record thesis for accuracy tracking
                _entry = 0.0
                _signals = snapshot_data.get("signals", snapshot_data.get("sigs", [])) if snapshot_data else []
                if _signals and isinstance(_signals[0], dict):
                    _entry = float(_signals[0].get("entry", _signals[0].get("e", 0.0)) or 0.0)
                if not _entry and _markets and isinstance(_markets[0], dict):
                    _entry = _markets[0].get("price", _markets[0].get("p", 0.0))
                thesis_text = td.get("thesis", td.get("n", ""))
                setup_type = td.get("setup_type", td.get("st", ""))
                # Audit #49: the Trade Agent schema emits no 'side', so the old
                # default recorded side="BUY" on every thesis (209/209). The
                # thesis direction is the signal side the agent judged — fall
                # back to it when the agent output has none.
                _thesis_side = str(td.get("side", td.get("s", "")) or "")
                if not _thesis_side and _signals and isinstance(_signals[0], dict):
                    _thesis_side = str(_signals[0].get("side", "") or "")
                thesis_id = record_thesis(
                    symbol=_sym,
                    side=_thesis_side or "BUY",
                    thesis=thesis_text[:200] if thesis_text else "",
                    confidence=decision.confidence * 100 if decision.confidence <= 1 else decision.confidence,
                    regime=regime,
                    entry_price=_entry,
                    setup_type=setup_type,
                )
                if thesis_id:
                    decision = LLMDecision(
                        action=decision.action,
                        confidence=decision.confidence,
                        regime=decision.regime,
                        strategy_weights=decision.strategy_weights,
                        memory_update=decision.memory_update,
                        notes=f"{decision.notes} | thesis_id={thesis_id}",
                        size_multiplier=decision.size_multiplier,
                        entry_adjustment=decision.entry_adjustment,
                    )

            elif decision.action in ("flat", "skip"):
                # Record skipped trade for counterfactual analysis
                _signals = snapshot_data.get("signals", []) if snapshot_data else []
                if _signals and isinstance(_signals[0], dict):
                    _sig = _signals[0]
                    record_skipped_trade(
                        symbol=_sym or _sig.get("sym", ""),
                        side=_sig.get("side", "BUY"),
                        entry_price=_sig.get("entry", _sig.get("e", 0.0)),
                        sl=_sig.get("sl", 0.0),
                        tp1=_sig.get("tp1", 0.0),
                        tp2=_sig.get("tp2", 0.0),
                        confidence=_sig.get("confidence", _sig.get("c", 0.0)),
                        skip_reason=decision.notes[:100] if decision.notes else "llm_skip",
                        strategy=_sig.get("strategy", ""),
                        regime=regime,
                    )
        except Exception as e:
            logger.info(f"[MULTI-AGENT] Brain wiring error: {e}")

        # ── Pipeline Telemetry ─────────────────────────────────
        if _EXTENSIONS_AVAILABLE:
            try:
                log_pipeline_telemetry(
                    pipeline_results, elapsed_ms,
                    decision.action, decision.confidence,
                )
            except Exception:
                pass

        # ── Performance Tracker: record pipeline run for agent scoring ──
        try:
            import uuid as _uuid
            from llm.agents.performance_tracker import get_performance_tracker
            _tracker = get_performance_tracker()
            _sym = ""
            _markets = snapshot_data.get("m", []) if snapshot_data else []
            if _markets and isinstance(_markets[0], dict):
                _sym = _markets[0].get("s", _markets[0].get("sym", ""))
            _side = ""
            if trade_out.ok:
                _side = trade_out.data.get("side", trade_out.data.get("s", ""))
            _pipeline_id = str(_uuid.uuid4())[:12]
            _tracker.record_pipeline_run(
                pipeline_id=_pipeline_id,
                symbol=_sym,
                side=_side,
                agent_outputs=pipeline_results,
            )
            # Record veto counterfactual if critic vetoed
            if decision.action in ("flat", "skip") and critic_out and critic_out.ok:
                _critic_verdict = critic_out.data.get("verdict", critic_out.data.get("v", ""))
                if _critic_verdict in ("veto", "reject"):
                    _entry = 0.0
                    if _markets and isinstance(_markets[0], dict):
                        _entry = _markets[0].get("price", _markets[0].get("p", 0.0))
                    _tracker.record_veto(
                        pipeline_id=_pipeline_id,
                        symbol=_sym,
                        side=_side,
                        entry_price=_entry,
                        critic_output=critic_out,
                    )
        except Exception as e:
            logger.debug(f"[MULTI-AGENT] Performance tracker record error: {e}")

        # Store pipeline results for external consumers (backtest logging, etc.)
        self.last_pipeline_results = pipeline_results

        return decision

    def get_entry_decision(
        self,
        signal_context: Dict[str, Any],
        market_context: Dict[str, Any],
        portfolio_context: Dict[str, Any],
        model_for_trigger: Optional[str] = None,
    ) -> EntryDecision:
        """LLM-first entry pipeline: full quality + sizing decisions.

        This replaces _llm_veto_check and 47 mechanical gates. The LLM
        receives the raw signal with all context and decides:
        1. Should we trade? (go/skip)
        2. How much? (leverage, risk_pct, qty)
        3. Why? (thesis, regime, debate summary)

        Args:
            signal_context: Raw signal data + metadata from evaluate_raw().
                Keys: symbol, side, confidence, entry, sl, tp1, tp2, atr,
                      strategy, num_agree, chop_score, win_prob, ev_per_dollar,
                      fee_drag_pct, rr_tp1, stop_width_pct, etc.
            market_context: Market data for LLM enrichment.
                Keys: funding_rate, volume_ratio, time_utc_hour, btc_trend,
                      signal_age, ohlcv_1h, ohlcv_5m, etc.
            portfolio_context: Current portfolio state.
                Keys: equity, open_positions, correlation_matrix,
                      daily_pnl, circuit_breaker_proximity, etc.
            model_for_trigger: Optional model override.

        Returns:
            EntryDecision with action, leverage, qty, regime, thesis.
            On pipeline failure, returns EntryDecision.skip().
        """
        start = time.monotonic()

        # ── Decision cache: return cached skip without running the pipeline ──
        # Quota rationale: bot scans every 30s × 4 symbols = potential 480 LLM
        # calls/hour. A cached skip for unchanged conditions saves ~5 CLI agent
        # calls per cache hit (~130-190s of quota per hit avoided).
        # Only skip decisions are cached (GOs are single-use — never repeat a trade).
        # Backtest mode bypasses the cache (each scenario must be independent).
        _is_backtest_mode = portfolio_context.get("_is_backtest", False)
        if not _is_backtest_mode:
            _cache_key = self._entry_cache_key(signal_context, market_context)
            _now = time.time()
            with self._cache_lock:
                _cached = self._entry_decision_cache.get(_cache_key)
            if _cached is not None:
                _age = _now - _cached["ts"]
                _entry_price = float(signal_context.get("entry", 0))
                _cached_price = _cached["entry_price"]
                _price_move = abs(_entry_price - _cached_price) / _cached_price if _cached_price > 0 else 1.0
                _ttl_ok = _age < self._entry_cache_ttl
                _price_ok = _price_move < self._entry_cache_price_tolerance
                if _ttl_ok and _price_ok:
                    with self._cache_lock:
                        self._entry_cache_hits += 1
                    _cached_dec = _cached["decision"]
                    logger.info(
                        f"[LLM-CACHE] HIT {signal_context.get('symbol','')} {signal_context.get('side','')} "
                        f"age={_age:.0f}s price_drift={_price_move*100:.2f}% "
                        f"(hits={self._entry_cache_hits} misses={self._entry_cache_misses})"
                    )
                    # Return a copy tagged as cached so callers can distinguish
                    from dataclasses import replace as _dc_replace
                    return _dc_replace(
                        _cached_dec,
                        notes=f"[CACHED {_age:.0f}s ago] {_cached_dec.notes}",
                    )
                else:
                    # Stale cache entry — remove it
                    with self._cache_lock:
                        self._entry_decision_cache.pop(_cache_key, None)
            with self._cache_lock:
                self._entry_cache_misses += 1
        else:
            _cache_key = None

        # ── Lever 2: Graduated-rules veto pre-filter ──
        # Run VETO-only graduated rules before the 5-agent pipeline.
        # Any hard-veto rule (e.g. hype_short_veto_v1, WR=2.3%) eliminates the
        # LLM call entirely — saves ~130-190s of subscription quota per hit.
        # BOOST/PENALIZE rules are NOT applied here — those need LLM context.
        # Backtest is excluded (snapshot cache key = None is backtest signal).
        if not _is_backtest_mode:
            try:
                from llm.graduated_rules import get_graduated_rules_engine
                _sym = signal_context.get("symbol", "")
                _side = signal_context.get("side", "")
                _conf = float(signal_context.get("confidence", 0))
                _strat = signal_context.get("strategy", "")
                _n_agree = int(signal_context.get("num_agree",
                               signal_context.get("num_strategies_agree", 0)))
                _hour = int(market_context.get("time_utc_hour", -1))
                _strats_active = signal_context.get("strategies_agree", [])
                _gre = get_graduated_rules_engine()
                _pre_vetoed, _, _pre_notes, _pre_veto_ids = _gre.evaluate_signal(
                    symbol=_sym, side=_side, confidence=_conf,
                    strategy=_strat, num_agree=_n_agree,
                    hour_utc=_hour, strategies_active=_strats_active,
                    veto_only=True,
                )
                if _pre_vetoed:
                    # ── R11 §2b provenance gate (FULL_PIPE map, 2026-07-02) ──
                    # A pre-LLM hard veto may ENFORCE only when at least one
                    # firing rule carries full §2b provenance: current-ledger
                    # version, era stamp, n>=13, and dollar-positive
                    # (net_pnl_saved > 0). Rules missing provenance are suspect
                    # by construction (learned on the condemned ledger) — they
                    # SHADOW instead: logged + counterfactual-recorded so they
                    # can dollar-score their way back, but never block.
                    _pf_enforce = False
                    _pf_prov_notes = []
                    try:
                        from llm.graduated_rules import LEDGER_VERSION as _CUR_LEDGER
                        _rules_by_id = {
                            getattr(r, "rule_id", ""): r
                            for r in getattr(_gre, "_rules", [])
                        }
                        for _rid in _pre_veto_ids:
                            _r = _rules_by_id.get(_rid)
                            if _r is None:
                                _pf_prov_notes.append(f"{_rid}:missing")
                                continue
                            _r_n = max(int(getattr(_r, "times_applied", 0) or 0),
                                       int(getattr(_r, "total_evidence", 0) or 0))
                            _r_net = (float(getattr(_r, "pnl_saved", 0.0) or 0.0)
                                      - float(getattr(_r, "pnl_missed", 0.0) or 0.0))
                            _r_ok = (
                                getattr(_r, "ledger_version", "") == _CUR_LEDGER
                                and bool(getattr(_r, "era", ""))
                                and _r_n >= 13
                                and _r_net > 0
                            )
                            if _r_ok:
                                _pf_enforce = True
                            _pf_prov_notes.append(
                                f"{_rid}:{'2b-OK' if _r_ok else 'shadow'}"
                                f"(n={_r_n},net={_r_net:+.1f},"
                                f"lv={getattr(_r, 'ledger_version', '') or 'none'})"
                            )
                    except Exception as _pve:
                        # Unverifiable provenance = suspect = shadow (§2b).
                        logger.debug(f"[PRE-FILTER] provenance check error: {_pve} — shadow")
                        _pf_enforce = False
                    _pf_prov_str = "; ".join(_pf_prov_notes)[:160]

                    # Both enforce and shadow paths record the counterfactual —
                    # it is the sole measurement channel for veto rule accuracy.
                    # Stamp veto_rule_ids so it becomes a measurable denominator.
                    try:
                        from llm.brain_wiring import record_veto_counterfactual
                        _pf_entry = float(signal_context.get("entry", 0) or 0)
                        _pf_sl = float(signal_context.get("sl", 0) or 0)
                        _pf_tp1 = float(signal_context.get("tp1", 0) or 0)
                        _pf_tp2 = float(signal_context.get("tp2", 0) or 0)
                        _pf_denom_only = not (_pf_entry > 0 and _pf_sl > 0 and _pf_tp1 > 0)
                        record_veto_counterfactual(
                            symbol=_sym, side=_side,
                            entry_price=_pf_entry, sl=_pf_sl,
                            tp1=_pf_tp1, tp2=_pf_tp2,
                            confidence=_conf,
                            veto_rule_ids=_pre_veto_ids,
                            strategy=_strat,
                            regime=str(market_context.get("regime", "")),
                            denominator_only=_pf_denom_only,
                            metadata=(None if _pf_enforce
                                      else {"shadow": True,
                                            "reason": "pre_filter_no_2b_provenance"}),
                        )
                    except Exception:
                        pass

                    if _pf_enforce:
                        logger.info(
                            f"[PRE-FILTER] VETO {_sym}/{_side} before LLM: {_pre_notes[:80]} "
                            f"| provenance: {_pf_prov_str}"
                        )
                        _veto_decision = EntryDecision(
                            action="skip",
                            leverage=1.0,
                            risk_pct=0.0,
                            position_qty=0.0,
                            regime="unknown",
                            thesis="",
                            confidence=_conf / 100.0 if _conf > 1.0 else _conf,
                            notes=f"[PRE-FILTER VETO] {_pre_notes[:200]}",
                        )
                        # Store in cache as skip (same TTL as LLM skips)
                        if _cache_key is not None:
                            with self._cache_lock:
                                self._entry_decision_cache[_cache_key] = {
                                    "decision": _veto_decision,
                                    "ts": time.time(),
                                    "entry_price": float(signal_context.get("entry", 0)),
                                }
                        return _veto_decision
                    else:
                        logger.info(
                            f"[PRE-FILTER SHADOW] would_veto {_sym}/{_side} — no §2b-compliant "
                            f"rule ({_pf_prov_str}) — signal flows to LLM"
                        )
            except Exception as _pfe:
                logger.debug(f"[PRE-FILTER] Error: {_pfe}")

        # ── Build a snapshot_data dict compatible with existing pipeline ──
        # The existing _build_*_input methods expect snapshot_data format.
        # We translate signal_context + market/portfolio into that format.
        snapshot_data = self._build_entry_snapshot(
            signal_context, market_context, portfolio_context
        )

        # ── Run the standard agent pipeline ──
        # Reuse get_trading_decision which handles all enrichment, agents,
        # debate, consistency, learning integration, etc.
        decision = self.get_trading_decision(
            snapshot_data=snapshot_data,
            trigger_reason="llm_first_entry",
            model_for_trigger=model_for_trigger,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if decision is None:
            # Detect budget exhaustion as a specific, expected case so the
            # log distinguishes it from real pipeline bugs. Budget exhaustion
            # is self-healing at 00:00 UTC when cost_tracker resets.
            try:
                from llm.cost_tracker import get_cost_tracker
                _budget_pct = get_cost_tracker().get_budget_used_pct()
                if _budget_pct >= 1.0:
                    logger.info(
                        f"[LLM-FIRST] Pipeline skipped — daily budget exhausted "
                        f"({_budget_pct:.0%}). Resumes at 00:00 UTC."
                    )
                    return EntryDecision.skip("budget exhausted (resumes 00:00 UTC)")
            except Exception:
                pass
            logger.warning("[LLM-FIRST] Pipeline returned None — skipping trade")
            return EntryDecision.skip("LLM pipeline failure")

        # ── Extract sizing from agent outputs ──
        # Risk Agent output contains leverage and sizing recommendations.
        risk_out = self.last_pipeline_results.get(AgentRole.RISK)
        trade_out = self.last_pipeline_results.get(AgentRole.TRADE)
        critic_out = self.last_pipeline_results.get(AgentRole.CRITIC)

        # Parse leverage + sizing from Risk Agent
        leverage = 1.0
        risk_pct = 0.0
        sz_mult = 1.0  # Risk Agent's primary sizing multiplier (0.0-2.0)
        sizing_rationale = ""
        risk_flags = []

        if risk_out and risk_out.ok:
            rd = risk_out.data
            try:
                leverage = float(rd.get("leverage", rd.get("lev", rd.get("l", 1.0))))
            except (TypeError, ValueError):
                leverage = 1.0
            try:
                risk_pct = float(rd.get("risk_pct", rd.get("rp", rd.get("position_size_pct",
                                 rd.get("sz_pct", 0.0)))))
            except (TypeError, ValueError):
                risk_pct = 0.0
            try:
                sz_mult = float(rd.get("sz", rd.get("size_mult", rd.get("sm", 1.0))))
            except (TypeError, ValueError):
                sz_mult = 1.0
            sizing_rationale = str(rd.get("sizing_rationale", rd.get("rationale",
                               rd.get("n", ""))) or "")
            risk_flags = rd.get("risk_flags", rd.get("flags", rd.get("risks", [])))
            if isinstance(risk_flags, str):
                risk_flags = [risk_flags]
            if not isinstance(risk_flags, list):
                risk_flags = []

        # Enforce leverage bounds (1.0 - 20.0)
        leverage = max(1.0, min(20.0, leverage))
        # Enforce sz_mult bounds (0.3 - 2.0)
        sz_mult = max(0.3, min(2.0, sz_mult))

        # Use size_multiplier from LLMDecision as fallback sizing signal
        size_mult = decision.size_multiplier if decision else 1.0

        # Compute position qty from risk_pct + equity + leverage
        equity = portfolio_context.get("equity", 0)
        entry_price = signal_context.get("entry", 0)
        sl_price = signal_context.get("sl", 0)

        position_qty = 0.0
        if equity > 0 and entry_price > 0 and sl_price > 0:
            stop_width = abs(entry_price - sl_price)
            if stop_width > 0:
                # risk_pct is fraction of equity to risk per trade
                # If Risk Agent didn't return risk_pct, derive from sz_mult
                if risk_pct <= 0:
                    risk_pct = 0.10 * sz_mult  # 10% base risk * sz multiplier
                risk_dollars = equity * risk_pct
                # qty = risk_$ / stop_width. Do NOT multiply by leverage.
                # Leverage affects margin required, not qty for a given risk budget.
                # Old bug: (risk_$ / stop_width) * leverage made actual dollar risk
                # = risk_pct * leverage (e.g., 2.5% @ 3x = 7.5% real risk → 32x equity).
                position_qty = risk_dollars / stop_width

        if position_qty <= 0:
            logger.warning(
                f"[LLM-FIRST] position_qty={position_qty:.6f} "
                f"(equity={equity}, risk_pct={risk_pct:.3f}, "
                f"entry={entry_price}, sl={sl_price}, lev={leverage})"
            )

        # ── Parse thesis and debate from agents ──
        thesis = ""
        debate_summary = ""

        if trade_out and trade_out.ok:
            td = trade_out.data
            thesis = str(td.get("thesis", td.get("n", "")) or "")

        if critic_out and critic_out.ok:
            cd = critic_out.data
            counter = str(cd.get("counter_thesis", cd.get("ct", "")) or "")
            verdict = str(cd.get("verdict", cd.get("v", "")) or "")
            if counter:
                debate_summary = f"Bull: {thesis[:100]}. Bear: {counter[:100]}. Verdict: {verdict}"

        # Map LLMDecision action → EntryDecision action
        action = "skip"
        if decision.action in ("proceed", "go"):
            action = "go"
        elif decision.action == "flip":
            action = "go"  # flip handled at caller level

        # ── DECHOKE 6: dollar-score the Critic's shadow would-vetoes ──
        # With the Critic in shadow mode, its would-vetoes no longer block.
        # Record each one as a counterfactual so the setup it wanted to veto
        # gets priced by the market: if these resolve net-negative (n>=13),
        # _maybe_recommend_critic_restore() journals a restore recommendation.
        try:
            if (critic_out and critic_out.ok
                    and os.getenv("CRITIC_ENFORCE", "false").lower() != "true"):
                _sv_verdict = str(critic_out.data.get(
                    "verdict", critic_out.data.get("v", "approve")) or "approve").lower().strip()
                if _sv_verdict not in ("approve", "ok", ""):
                    _sv_entry = float(signal_context.get("entry", 0) or 0)
                    _sv_sl = float(signal_context.get("sl", 0) or 0)
                    _sv_tp1 = float(signal_context.get("tp1", 0) or 0)
                    if _sv_entry > 0 and _sv_sl > 0 and _sv_tp1 > 0:
                        from llm.brain_wiring import record_skipped_trade
                        _sv_reason = str(critic_out.data.get("reason", ""))[:100]
                        record_skipped_trade(
                            symbol=str(signal_context.get("symbol", "")),
                            side=str(signal_context.get("side", "")),
                            entry_price=_sv_entry,
                            sl=_sv_sl,
                            tp1=_sv_tp1,
                            tp2=float(signal_context.get("tp2", 0) or 0),
                            confidence=float(signal_context.get("confidence", 0) or 0),
                            skip_reason=f"CRITIC_SHADOW_VETO: {_sv_verdict} {_sv_reason}",
                            strategy=str(signal_context.get("strategy", "") or ""),
                            regime=str(decision.regime or ""),
                            metadata={"final_action": action},
                        )
        except Exception as _sve:
            logger.debug(f"[SHADOW-CRITIC] counterfactual record error: {_sve}")

        # ── Capture per-agent STATED confidence for the calibration ledger ──
        # These are the numbers each agent actually emitted (NOT the blended
        # consensus). Without this they are discarded and the ledger records
        # confidence:0.0 for every trade/critic/risk outcome. Keys match the
        # agent names used in _record_agent_calibration: trade/regime/critic/risk.
        regime_out = self.last_pipeline_results.get(AgentRole.REGIME)
        _agent_confidences: Dict[str, float] = {}
        try:
            if trade_out and trade_out.ok:
                _tc = float(trade_out.data.get("c", trade_out.data.get("confidence", 0.0)) or 0.0)
                if _tc > 0.0:
                    _agent_confidences["trade"] = round(max(0.0, min(1.0, _tc)), 3)
            if regime_out and regime_out.ok:
                _rc = float(regime_out.data.get("conf", regime_out.data.get("confidence", 0.0)) or 0.0)
                if _rc > 0.0:
                    _agent_confidences["regime"] = round(max(0.0, min(1.0, _rc)), 3)
            if critic_out and critic_out.ok:
                # Critic's belief about the trade: adjusted_confidence if present,
                # else infer from verdict (approve high, challenge low).
                _cc = critic_out.data.get("adjusted_confidence", critic_out.data.get("adj_c"))
                if _cc is None:
                    _verdict = str(critic_out.data.get("verdict", critic_out.data.get("v", "approve"))).lower().strip()
                    _cc = 0.7 if _verdict in ("approve", "ok", "") else 0.3
                _cc = float(_cc or 0.0)
                if _cc > 0.0:
                    _agent_confidences["critic"] = round(max(0.0, min(1.0, _cc)), 3)
            if risk_out and risk_out.ok:
                # Risk Agent confidence proxy: override=skip is a strong (high-conf)
                # call to skip; normal sizing near 1.0x = moderate. Map to [0,1].
                _ovr = str(risk_out.data.get("override", "") or "").lower()
                if _ovr == "skip":
                    _rk = 0.8
                elif _ovr == "reduce":
                    _rk = 0.6
                else:
                    _rk = 0.5
                _agent_confidences["risk"] = round(_rk, 3)
        except Exception as _ace:
            logger.debug(f"[LLM-FIRST] agent_confidences capture error: {_ace}")

        entry_decision = EntryDecision(
            action=action,
            leverage=leverage,
            risk_pct=risk_pct,
            position_qty=position_qty,
            regime=decision.regime or "unknown",
            thesis=thesis[:300] if thesis else "",
            confidence=decision.confidence,
            sizing_rationale=sizing_rationale[:200] if sizing_rationale else "",
            risk_flags=risk_flags[:5] if risk_flags else [],
            debate_summary=debate_summary[:300] if debate_summary else "",
            size_multiplier=sz_mult,
            notes=decision.notes[:500] if decision.notes else "",
            memory_update=decision.memory_update,
            agent_confidences=_agent_confidences,
        )

        logger.info(
            f"[LLM-FIRST] Entry decision: {action} lev={leverage:.1f}x "
            f"risk={risk_pct:.1%} qty={position_qty:.4f} "
            f"regime={decision.regime} conf={decision.confidence:.2f} "
            f"({elapsed_ms}ms)"
        )

        # ── Store skip decisions in cache ──
        # GOs are single-use (never replay a trade). Skips in stable conditions are safe to cache.
        if entry_decision.action == "skip" and _cache_key is not None and not _is_backtest_mode:
            with self._cache_lock:
                self._entry_decision_cache[_cache_key] = {
                    "decision": entry_decision,
                    "ts": time.time(),
                    "entry_price": float(signal_context.get("entry", 0)),
                }
                if len(self._entry_decision_cache) > 50:
                    oldest = min(self._entry_decision_cache, key=lambda k: self._entry_decision_cache[k]["ts"])
                    del self._entry_decision_cache[oldest]

        return entry_decision

    def _build_entry_snapshot(
        self,
        signal_ctx: Dict[str, Any],
        market_ctx: Dict[str, Any],
        portfolio_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Translate LLM-first context into snapshot_data format.

        The existing agent pipeline expects data in a specific snapshot format
        with keys like 'm' (markets), 'g' (global), 'pos' (positions), etc.
        This bridges the raw signal context into that format.
        """
        symbol = signal_ctx.get("symbol", "")
        side = signal_ctx.get("side", "")
        entry = signal_ctx.get("entry", 0)
        sl = signal_ctx.get("sl", 0)
        tp1 = signal_ctx.get("tp1", 0)
        tp2 = signal_ctx.get("tp2", 0)
        confidence = signal_ctx.get("confidence", 0)
        atr = signal_ctx.get("atr", 0)

        # Build market data in snapshot format
        market_entry = {
            "s": symbol,
            "sym": symbol,
            "p": entry,
            "price": entry,
            "sg": [{
                "sym": symbol,
                "s": symbol,
                "side": side,
                "sd": side,
                "e": entry,
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "c": confidence / 100 if confidence > 1 else confidence,
                "confidence": confidence,
                "atr": atr,
                "strategy": signal_ctx.get("strategy", ""),
            }],
            "sigs": [{
                "sym": symbol,
                "side": side,
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "confidence": confidence,
                "strategy": signal_ctx.get("strategy", ""),
            }],
        }

        # Global context — surface edge_data in `setup_mfe` and `historical_edge`
        # so agent input builders can reach it via `_g.get("setup_mfe")`. Without
        # this the prompts say they check TOXIC setups but receive no data.
        equity = portfolio_ctx.get("equity", 0)
        _edge_data = signal_ctx.get("edge_data", {}) or {}
        global_ctx = {
            "equity": equity,
            "eq": equity,
            "daily_pnl": portfolio_ctx.get("daily_pnl", 0),
            "open_count": portfolio_ctx.get("open_positions_count", 0),
            "setup_mfe": _edge_data,
            "historical_edge": _edge_data,
            # Portfolio utilization so Risk Agent sees remaining OpsGuard capacity
            "notional_deployed_pct": portfolio_ctx.get("total_notional_pct", 0),
            "notional_remaining_pct": portfolio_ctx.get("remaining_notional_pct", 500),
        }

        # Positions
        positions = portfolio_ctx.get("open_positions", {})

        # Build snapshot
        snapshot = {
            "m": [market_entry],
            "g": global_ctx,
            "pos": positions,
            "signals": market_entry["sigs"],
            # Pass through raw context for enrichment modules
            "_llm_first_signal": signal_ctx,
            "_llm_first_market": market_ctx,
            "_llm_first_portfolio": portfolio_ctx,
        }

        # Inject pre-formed Scout thesis if fresh (< 20 min old)
        _sym_key = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "").upper()
        _scout_entry = self._scout_thesis_cache.get(_sym_key)
        if _scout_entry and (time.time() - _scout_entry.get("timestamp", 0)) < self._scout_cache_ttl:
            snapshot["_scout_pre_formed"] = _scout_entry
            # Surface the Scout watchlist item directly to the signal_metadata
            # so Trade/Risk agents see Scout's pre-formed view in their input JSON
            _wl_item = _scout_entry.get("watchlist_item", {})
            if _wl_item:
                snapshot.setdefault("signal_metadata", {}).update({
                    "scout_pre_bias": _wl_item.get("bias", ""),
                    "scout_setup_type": _wl_item.get("setup_type", ""),
                    "scout_conviction": _wl_item.get("conviction", 0),
                    "scout_thesis": str(_wl_item.get("thesis", ""))[:200],
                    "scout_key_levels": _wl_item.get("key_levels", []),
                    "scout_regime_forecast": _scout_entry.get("regime_forecast", {}),
                })
                logger.debug(f"[COORDINATOR] Injected Scout pre-formed thesis for {_sym_key}: "
                             f"bias={_wl_item.get('bias')} conviction={_wl_item.get('conviction')}")

        # Pass through OHLCV data for technicals enrichment
        ohlcv_1h = market_ctx.get("ohlcv_1h")
        ohlcv_5m = market_ctx.get("ohlcv_5m")
        if ohlcv_1h is not None:
            snapshot["ohlcv_1h"] = ohlcv_1h
            snapshot["ohlcv_by_symbol_1h"] = {symbol: ohlcv_1h}
        if ohlcv_5m is not None:
            snapshot["ohlcv_5m"] = ohlcv_5m
            snapshot["ohlcv_by_symbol_5m"] = {symbol: ohlcv_5m}

        # Signal metadata for agents (what mechanical gates used to check)
        signal_meta = {
            "chop_score": signal_ctx.get("chop_score", 0),
            "chop_score_smoothed": signal_ctx.get("chop_score_smoothed", 0),
            "win_prob": signal_ctx.get("win_prob"),
            "ev_per_dollar": signal_ctx.get("ev_per_dollar"),
            "fee_drag_pct": signal_ctx.get("fee_drag_pct"),
            "rr_tp1": signal_ctx.get("rr_tp1", 0),
            "rr_tp2": signal_ctx.get("rr_tp2", 0),
            "stop_width_pct": signal_ctx.get("stop_width_pct", 0),
            "num_agree": signal_ctx.get("num_agree", 1),
            "strategies_agree": signal_ctx.get("strategies_agree", []),
            "quality_multiplier": signal_ctx.get("quality_multiplier"),
            "regime_1h": signal_ctx.get("regime_1h", "unknown"),
            "regime_4h": signal_ctx.get("regime_4h", "unknown"),
            "regime_4h_aligned": signal_ctx.get("regime_4h_aligned", True),
            "mechanical_confidence_floor": signal_ctx.get("mechanical_confidence_floor"),
            "would_pass_confidence_floor": signal_ctx.get("would_pass_confidence_floor"),
            "graduated_rules_advisory": signal_ctx.get("graduated_rules_advisory"),
            # Market context
            "funding_rate": market_ctx.get("funding_rate"),
            "volume_ratio": market_ctx.get("volume_ratio", 1.0),
            "time_utc_hour": market_ctx.get("time_utc_hour"),
            "day_of_week": market_ctx.get("day_of_week"),
            "btc_price": market_ctx.get("btc_price", 0),
            "btc_trend": market_ctx.get("btc_trend", 0),
            "eth_price": market_ctx.get("eth_price", 0),
            "eth_trend": market_ctx.get("eth_trend", 0),
            "sol_price": market_ctx.get("sol_price", 0),
            "sol_trend": market_ctx.get("sol_trend", 0),
            "signal_age_s": market_ctx.get("signal_age", 0),
            # Portfolio context
            "portfolio_correlation": portfolio_ctx.get("correlation_matrix"),
            "circuit_breaker_proximity": portfolio_ctx.get("circuit_breaker_proximity"),
            # Historical edge — surface TOXIC/verdict/WR so agents can block bad setups
            "historical_edge": _edge_data,
            "is_toxic": bool(_edge_data.get("is_toxic", False)),
            "regime_wr": _edge_data.get("regime_wr"),
            "regime_n": _edge_data.get("regime_n"),
            "setup_verdict": _edge_data.get("verdict", ""),
        }
        snapshot["signal_metadata"] = signal_meta

        return snapshot

    def get_post_trade_lesson(
        self,
        trade_data: Dict[str, Any],
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run the Learning Agent on a closed trade.

        Returns parsed lesson dict or None.
        """
        if not self.configs.get(AgentRole.LEARNING, AgentConfig(role=AgentRole.LEARNING)).enabled:
            return None

        learning_input = self._build_learning_input(trade_data)
        out = self._call_agent(AgentRole.LEARNING, learning_input, model_for_trigger)

        if out.ok:
            logger.info(
                f"[MULTI-AGENT] Learning agent lesson: "
                f"{out.data.get('lesson', '')[:80]}"
            )

            # Persist lesson to short-term memory so future agents see it
            try:
                from llm.memory_store import apply_memory_update as _mem_upd
                _lesson_txt = (
                    out.data.get("lesson") or
                    out.data.get("insight") or
                    out.data.get("summary") or ""
                )
                if _lesson_txt:
                    _mem_upd(
                        _lesson_txt[:200],
                        symbol=trade_data.get("symbol", ""),
                        regime=trade_data.get("regime", ""),
                    )
            except Exception as _me:
                logger.debug(f"[LEARNING] Memory write error: {_me}")

            try:
                from llm.agents.performance_tracker import get_performance_tracker
                import uuid as _uuid
                get_performance_tracker().record_pipeline_run(
                    pipeline_id=f"learning_{str(_uuid.uuid4())[:8]}",
                    symbol=trade_data.get("symbol", ""),
                    side=trade_data.get("side", ""),
                    agent_outputs={AgentRole.LEARNING: out},
                )
            except Exception:
                pass

            # ── Brain Wiring: close thesis + record regime trade ──
            try:
                from llm.brain_wiring import close_thesis, record_regime_trade
                # Close thesis if thesis_id is in the trade notes
                notes = trade_data.get("notes", "")
                if "thesis_id=" in notes:
                    tid = notes.split("thesis_id=")[1].split(" ")[0].split("|")[0].strip()
                    if tid:
                        close_thesis(
                            thesis_id=tid,
                            exit_price=trade_data.get("exit_price", 0.0),
                            pnl_pct=trade_data.get("pnl_pct", 0.0),
                            max_favorable=trade_data.get("max_favorable"),
                            max_adverse=trade_data.get("max_adverse"),
                            actual_hold_h=trade_data.get("hold_hours"),
                        )
                # Record trade for regime feedback
                record_regime_trade(
                    regime=trade_data.get("regime", "unknown"),
                    pnl=trade_data.get("pnl_pct", 0.0),
                    confidence=trade_data.get("confidence", 0.0),
                    strategy=trade_data.get("strategy", ""),
                    hold_hours=trade_data.get("hold_hours", 0.0),
                )
            except Exception as e:
                logger.info(f"[MULTI-AGENT] Brain post-trade wiring error: {e}")

            return out.data
        return None

    # NOTE (R12, FULL_PIPE map, 2026-07-02): _decide_pipeline_tier was deleted.
    # It ranked signals by regime/conf/edge heuristics to decide whether the
    # LLM pipeline was "needed" — signal-quality routing is banned by doctrine.
    # If call-tiering ever returns it must count calls (quota), not judge signals.

    def evaluate_override(
        self,
        override_context,
    ) -> Optional[Dict[str, Any]]:
        """Ask the OverrideAgent whether a mechanical block should be bypassed.

        This is the "educated override" path: when a mechanical filter blocks a
        signal, we hand the full context to the OverrideAgent. It reasons about
        the block using regime-specific edge data and returns an override decision.

        Args:
            override_context: An OverrideContext with full block/signal/edge data

        Returns:
            {
                "decision": "override" | "confirm_block",
                "confidence": float,
                "summary": str,
                "reasoning": str,
                "edge_citation": str,
                "corrected_ev": float | None,
                "key_risks": list[str],
            }
            or None on failure (treat as confirm_block).
        """
        try:
            import json
            from llm.agents.base import AgentRole

            # Build compact input from context
            ctx_dict = override_context.to_prompt_dict()
            input_json = json.dumps(ctx_dict, separators=(",", ":"), default=str)

            # Call the OverrideAgent (Sonnet for reasoning depth)
            out = self._call_agent(
                AgentRole.OVERRIDE,
                input_json,
                fallback_model="claude-sonnet-4-6",
            )

            if not out or not out.ok:
                logger.warning(
                    f"[OVERRIDE] Agent call failed for {override_context.symbol} "
                    f"{override_context.side}: "
                    f"{out.error if out else 'no output'} — confirming block"
                )
                return None

            result = out.data
            decision = result.get("decision", "confirm_block")
            confidence = float(result.get("confidence", 0.0))
            summary = result.get("summary", "")

            logger.info(
                f"[OVERRIDE] {override_context.symbol} {override_context.side} "
                f"block={override_context.block_type} -> "
                f"{decision} conf={confidence:.2f}: {summary[:120]}"
            )
            return result

        except Exception as e:
            logger.warning(f"[OVERRIDE] Evaluation error: {e}")
            return None

    def get_exit_intelligence(
        self,
        position_data: Dict[str, Any],
        market_data: Optional[dict] = None,
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run the Exit Intelligence Agent on an open position.

        Args:
            position_data: Open position state (symbol, side, entry, sl, tp1, tp2,
                           unrealized_pnl, hold_time_s, state, thesis, setup_type)
            market_data: Current market snapshot (regime, BTC direction, funding, signals)
            model_for_trigger: Model override (defaults to Haiku for cost efficiency)

        Returns:
            Parsed exit recommendation dict or None.
        """
        if not self.configs.get(AgentRole.EXIT, AgentConfig(role=AgentRole.EXIT)).enabled:
            return None

        exit_input = self._build_exit_input(position_data, market_data)
        out = self._call_agent(AgentRole.EXIT, exit_input, model_for_trigger)
        self.last_exit_output = out

        try:
            from llm.agents.performance_tracker import get_performance_tracker
            import uuid as _uuid
            get_performance_tracker().record_pipeline_run(
                pipeline_id=f"exit_{str(_uuid.uuid4())[:8]}",
                symbol=position_data.get("symbol", ""),
                side=position_data.get("side", ""),
                agent_outputs={AgentRole.EXIT: out},
            )
        except Exception:
            pass

        if out.ok:
            action = out.data.get("action", "hold")
            urgency = out.data.get("urgency", "low")
            logger.info(
                f"[MULTI-AGENT] Exit agent: {position_data.get('symbol', '?')} "
                f"action={action} urgency={urgency} "
                f"thesis_valid={out.data.get('thesis_still_valid', '?')} "
                f"reason={out.data.get('reason', '')[:60]}"
            )

            # Feed exit reasoning to learning systems when closing
            if action in ("full_close", "partial_close", "close"):
                try:
                    from llm.agents.learning_integration import process_exit_feedback
                    process_exit_feedback(out.data, position_data)
                except Exception as ef:
                    logger.debug(f"[MULTI-AGENT] Exit feedback error: {ef}")

            return out.data
        return None

    def _build_exit_input(
        self, position_data: Dict[str, Any], market_data: Optional[dict] = None
    ) -> str:
        """Build exit agent input: position state + current market + thesis context."""
        exit_data = dict(position_data)

        # Inject current market context if available
        if market_data:
            if "m" in market_data:
                # Extract just the relevant market for this symbol
                symbol = position_data.get("symbol", "")
                for m in market_data.get("m", []):
                    if m.get("s") == symbol or m.get("sym") == symbol:
                        exit_data["current_market"] = m
                        break
            if "g" in market_data:
                exit_data["global"] = market_data["g"]

            # Run a quick regime classification from scratchpad if available
            scratchpad = get_pipeline_scratchpad()
            regime = scratchpad.read_by_key("regime")
            if regime:
                exit_data["current_regime"] = regime
                exit_data["regime_bias"] = scratchpad.read_by_key("bias") or "neutral"
                exit_data["regime_outlook"] = scratchpad.read_by_key("outlook") or ""

        # Add deep memory context for this symbol's exit patterns
        try:
            from llm.deep_memory import get_deep_memory
            dm = get_deep_memory()
            symbol = position_data.get("symbol", "")
            regime = position_data.get("regime", "")
            summary = dm.build_llm_knowledge_summary(
                symbol=symbol, regime=regime, max_tokens=300
            )
            if summary:
                exit_data["exit_history"] = summary[:300]
        except Exception:
            pass

        # Enriched context from market_data if available
        if market_data and market_data.get("enriched_context"):
            exit_data["enriched"] = market_data["enriched_context"]

        return json.dumps(exit_data, separators=(",", ":"), default=str)

    def run_scout(
        self,
        scout_data: Dict[str, Any],
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run the Scout/Preparation Agent during idle time.

        The Scout Agent runs between signal evaluations to:
        - Identify setups forming (approaching key levels)
        - Pre-form directional theses for likely trades
        - Forecast regime transitions
        - Surface lead-lag opportunities
        - Calculate risk budget and correlation warnings

        Results are written to the pipeline scratchpad so downstream
        agents (Trade, Risk) can read preparation data.

        Args:
            scout_data: Market overview (all symbols, prices, levels, regime, positions)
            model_for_trigger: Model override (defaults to Haiku)

        Returns:
            Parsed scout output dict or None.
        """
        if not self.configs.get(AgentRole.SCOUT, AgentConfig(role=AgentRole.SCOUT)).enabled:
            return None

        scout_input = json.dumps(scout_data, separators=(",", ":"), default=str)
        out = self._call_agent(AgentRole.SCOUT, scout_input, model_for_trigger)

        if out.ok:
            # Write scout findings to scratchpad for Trade Agent to consume
            scratchpad = get_pipeline_scratchpad()

            watchlist = out.data.get("watchlist", [])
            if watchlist:
                scratchpad.write("scout", "watchlist", watchlist)
                high_priority = [w for w in watchlist if w.get("priority") == "high"]
                if high_priority:
                    scratchpad.write("scout", "high_priority_setups", high_priority)
                # Cache pre-formed theses per symbol so get_entry_decision()
                # can inject Scout's view without re-running the full pipeline
                _now = time.time()
                for _item in watchlist:
                    _sym = str(_item.get("symbol", "")).upper()
                    if _sym:
                        self._scout_thesis_cache[_sym] = {
                            "watchlist_item": _item,
                            "timestamp": _now,
                            "regime_forecast": out.data.get("regime_forecast"),
                            "risk_budget": out.data.get("risk_budget"),
                        }

            regime_forecast = out.data.get("regime_forecast")
            if regime_forecast:
                scratchpad.write("scout", "regime_forecast", regime_forecast)

            lead_lag = out.data.get("lead_lag_alerts", [])
            if lead_lag:
                scratchpad.write("scout", "lead_lag_alerts", lead_lag)

            corr_warning = out.data.get("correlation_warning")
            if corr_warning:
                scratchpad.write("scout", "correlation_warning", corr_warning)

            risk_budget = out.data.get("risk_budget")
            if risk_budget:
                scratchpad.write("scout", "risk_budget", risk_budget)

            logger.info(
                f"[MULTI-AGENT] Scout: {len(watchlist)} watchlist items, "
                f"regime_forecast={regime_forecast.get('direction', '?') if regime_forecast else 'none'}, "
                f"lead_lag={len(lead_lag)} alerts"
            )

            try:
                from llm.agents.performance_tracker import get_performance_tracker
                import uuid as _uuid
                get_performance_tracker().record_pipeline_run(
                    pipeline_id=f"scout_{str(_uuid.uuid4())[:8]}",
                    symbol="GLOBAL",
                    side="",
                    agent_outputs={AgentRole.SCOUT: out},
                )
            except Exception:
                pass

            return out.data
        return None

    def run_overseer(
        self,
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run the Overseer meta-optimizer agent.

        The Overseer runs periodically (every 30-60 min) to:
        - Analyze cross-trade patterns invisible to individual agents
        - Identify systematic drift and strategy degradation
        - Generate long-term theses for the system to learn from
        - Recommend strategy adjustments, model routing, parameter changes
        - Assess agent quality and calibration

        Results feed into the growth orchestrator as recommendations and
        are written to scratchpad for downstream agents to read.

        Returns:
            Parsed overseer output dict or None.
        """
        cfg = self.configs.get(
            AgentRole.OVERSEER,
            AgentConfig(role=AgentRole.OVERSEER),
        )
        if not cfg.enabled:
            return None

        # Build comprehensive system state for the Overseer
        overseer_input = self._build_overseer_input()

        # Cold-start guard: if the state dict is near-empty (fresh bot, no
        # accumulated self-performance / survival / deep memory data), the
        # Overseer will hallucinate filler to fill its rich JSON schema and
        # truncate. Forensic 2026-04-14 showed a 599-input / 2500-output
        # truncated garbage call. Skip instead of burning budget on nothing.
        MIN_OVERSEER_INPUT_CHARS = 1500
        if len(overseer_input) < MIN_OVERSEER_INPUT_CHARS:
            logger.info(
                f"[MULTI-AGENT] Overseer skipped: input too thin "
                f"({len(overseer_input)} chars < {MIN_OVERSEER_INPUT_CHARS} min). "
                f"Warm up with trade history before running."
            )
            return None

        out = self._call_agent(AgentRole.OVERSEER, overseer_input, model_for_trigger)

        # SHIP-2026-04-19: log Overseer calls so agent_performance.jsonl is no longer silent.
        # Completes the 4-dead-agent revival started 2026-04-17 (Learning/Exit/Scout already done).
        try:
            from llm.agents.performance_tracker import get_performance_tracker
            import uuid as _uuid
            get_performance_tracker().record_pipeline_run(
                pipeline_id=f"overseer_{str(_uuid.uuid4())[:8]}",
                symbol="GLOBAL",
                side="",
                agent_outputs={AgentRole.OVERSEER: out},
            )
        except Exception:
            pass

        if not out.ok:
            logger.warning("[MULTI-AGENT] Overseer call failed")
            return None

        data = out.data

        # Write Overseer findings to scratchpad for Trade/Risk/Critic to read
        scratchpad = get_pipeline_scratchpad()

        # Strategy adjustments become context for Trade Agent
        strat_adj = data.get("strategy_adjustments")
        if strat_adj:
            scratchpad.write("overseer", "strategy_adjustments", strat_adj)

        # Symbol focus becomes context for Trade Agent
        sym_focus = data.get("symbol_focus")
        if sym_focus:
            scratchpad.write("overseer", "symbol_focus", sym_focus)

        # Agent feedback becomes context for respective agents
        agent_fb = data.get("agent_feedback")
        if agent_fb:
            scratchpad.write("overseer", "agent_feedback", agent_fb)

        # System health for all agents to see
        health = data.get("system_health", "stable")
        diagnosis = data.get("diagnosis", "")
        scratchpad.write("overseer", "health", health)
        if diagnosis:
            scratchpad.write("overseer", "diagnosis", diagnosis[:200])

        # Feed recommendations into the growth orchestrator
        recs = data.get("recommendations", [])
        if recs:
            try:
                from llm.growth.orchestrator import get_growth_orchestrator
                growth = get_growth_orchestrator()
                for rec in recs[:5]:
                    growth.on_recommendation_from_llm(
                        rec_type=rec.get("type", "parameter"),
                        title=rec.get("title", "Overseer recommendation"),
                        description=rec.get("rationale", ""),
                        suggested_action=rec.get("action", ""),
                        confidence=0.7 if rec.get("priority") in ("critical", "high") else 0.5,
                    )
            except Exception as e:
                logger.debug(f"[OVERSEER] Failed to feed recommendations: {e}")

        # Feed theses into hypothesis tracker
        theses = data.get("theses", [])
        if theses:
            try:
                from llm.growth.hypothesis_tracker import get_hypothesis_tracker
                tracker = get_hypothesis_tracker()
                for th in theses[:3]:
                    tracker.propose(
                        hypothesis=th.get("thesis", ""),
                        source="overseer",
                        confidence=th.get("confidence", 0.5),
                    )
            except Exception as e:
                logger.debug(f"[OVERSEER] Failed to feed theses: {e}")

        # Trigger self-analyst: let the bot analyze its own trades and write new KB rules.
        # Rate-limited internally (max 3/day, min 8h between runs) so this is safe to call
        # on every overseer cycle.
        try:
            from llm.self_analyst import run_analysis as _self_analyse
            import threading as _threading
            _t = _threading.Thread(target=_self_analyse, daemon=True, name="self_analyst")
            _t.start()
        except Exception as _se:
            logger.debug(f"[OVERSEER] Self-analyst launch error: {_se}")

        # Trigger deep trade analyst: milestone-gated full historical analysis
        # (runs at 50/100/150/200/300 trade milestones, writes to insight_journal)
        try:
            from llm.deep_trade_analyst import run_milestone_check as _deep_check
            import threading as _threading2
            _t2 = _threading2.Thread(target=_deep_check, daemon=True, name="deep_analyst")
            _t2.start()
        except Exception as _de:
            logger.debug(f"[OVERSEER] Deep analyst launch error: {_de}")

        logger.info(
            f"[MULTI-AGENT] Overseer: health={health}, "
            f"{len(recs)} recommendations, {len(theses)} theses, "
            f"diagnosis={diagnosis[:80]}"
        )
        return data

    # ── Phase 3 Strategic Agents ────────────────────────────────

    def get_portfolio_intelligence(
        self, model_for_trigger: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Run Portfolio Aggregator agent for holistic portfolio health analysis.
        Runs DAILY (not per-trade).

        Returns:
            Portfolio analysis dict or None on failure.
        """
        if not _STRATEGIC_AGENTS_AVAILABLE:
            return None
        return build_portfolio_aggregator(self, model_for_trigger)

    def get_regime_forecast(
        self, model_for_trigger: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Run Regime Forecaster agent to predict regime transitions.
        Runs DAILY.

        Returns:
            Regime forecast dict or None on failure.
        """
        if not _STRATEGIC_AGENTS_AVAILABLE:
            return None
        return build_regime_forecaster(self, model_for_trigger)

    def get_novel_hypotheses(
        self, model_for_trigger: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Run Hypothesis Generator agent to discover novel trading patterns.
        Runs WEEKLY.

        Returns:
            Novel hypotheses dict or None on failure.
        """
        if not _STRATEGIC_AGENTS_AVAILABLE:
            return None
        return build_hypothesis_generator(self, model_for_trigger)

    def get_correlator_analysis(
        self, model_for_trigger: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Run Correlator agent to analyze cross-asset relationships.
        Runs DAILY.

        Returns:
            Correlation analysis dict or None on failure.
        """
        if not _STRATEGIC_AGENTS_AVAILABLE:
            return None
        return build_correlator(self, model_for_trigger)

    # ── Phase 4 Scalping + Conviction Agents ────────────────────

    def get_micro_trend(
        self, model_for_trigger: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Run Micro-Trend Detector to classify 5m micro-trends.
        Feeds context into Scalper Agent.

        Returns:
            Micro-trend classification dict or None on failure.
        """
        if not _PHASE_4_AGENTS_AVAILABLE:
            return None
        return build_micro_trend_detector(self, model_for_trigger)

    def get_scalp_signal(
        self, model_for_trigger: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Run Scalper Agent to find 1m-5m micro-trading opportunities.
        Runs very frequently (every 1m when enabled).

        Returns:
            Scalp signal dict or None on failure.
        """
        if not _PHASE_4_AGENTS_AVAILABLE:
            return None
        return build_scalper(self, model_for_trigger)

    def get_conviction_analysis(
        self,
        regime_out: AgentOutput,
        trade_out: AgentOutput,
        quant_out: Optional[AgentOutput] = None,
        critic_out: Optional[AgentOutput] = None,
        forecaster_out: Optional[AgentOutput] = None,
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Conviction Agent to authorize high-leverage trades when all agents align.
        Runs per signal (rare, ~5-10/month).

        Returns:
            Conviction analysis dict or None on failure.
        """
        if not _PHASE_4_AGENTS_AVAILABLE:
            return None
        return build_conviction(
            self, regime_out, trade_out, quant_out, critic_out, forecaster_out, model_for_trigger
        )

    # ── Phase 4A Core Trading Agents ─────────────────────────────

    def get_position_size(
        self,
        capital: float,
        edge_confidence: float,
        kelly_fraction: Optional[float] = None,
        regime: str = "unknown",
        risk_per_trade: float = 1.0,
        leverage: float = 1.5,
        atr: float = 0.0,
        stop_distance: float = 0.0,
        consecutive_losses: int = 0,
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Position Sizer Agent to calculate exact position size in USD.

        Returns:
            Position sizing dict with position_size_usd, leverage_applied, etc or None on failure.
        """
        if not _PHASE_4A_AGENTS_AVAILABLE:
            return None
        return build_position_sizer(
            self,
            capital=capital,
            edge_confidence=edge_confidence,
            kelly_fraction=kelly_fraction,
            regime=regime,
            risk_per_trade=risk_per_trade,
            leverage=leverage,
            atr=atr,
            stop_distance=stop_distance,
            consecutive_losses=consecutive_losses,
            model_for_trigger=model_for_trigger,
        )

    def get_entry_method(
        self,
        signal_confidence: float,
        current_price: float,
        entry_price_from_signal: float,
        regime: str = "unknown",
        recent_momentum: str = "flat",
        order_book: Optional[Dict] = None,
        position_size_usd: float = 0.0,
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Entry Optimizer Agent to determine entry method and timing.

        Returns:
            Entry optimization dict with entry_method, entry_price, urgency, etc or None on failure.
        """
        if not _PHASE_4A_AGENTS_AVAILABLE:
            return None
        return build_entry_optimizer(
            self,
            signal_confidence=signal_confidence,
            current_price=current_price,
            entry_price_from_signal=entry_price_from_signal,
            regime=regime,
            recent_momentum=recent_momentum,
            order_book=order_book,
            position_size_usd=position_size_usd,
            model_for_trigger=model_for_trigger,
        )

    def get_exit_recommendation(
        self,
        position_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        current_price: float,
        pnl_usd: float,
        thesis: str = "",
        regime: str = "unknown",
        original_regime: str = "unknown",
        time_held_seconds: int = 0,
        funding_paid: float = 0.0,
        volume_trend: str = "stable",
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Exit Advisor Agent to recommend exit actions for open positions.

        Returns:
            Exit recommendation dict with action, thesis_still_valid, etc or None on failure.
        """
        if not _PHASE_4A_AGENTS_AVAILABLE:
            return None
        return build_exit_advisor(
            self,
            position_id=position_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            current_price=current_price,
            pnl_usd=pnl_usd,
            thesis=thesis,
            regime=regime,
            original_regime=original_regime,
            time_held_seconds=time_held_seconds,
            funding_paid=funding_paid,
            volume_trend=volume_trend,
            model_for_trigger=model_for_trigger,
        )

    def get_risk_check(
        self,
        proposed_trade: Dict[str, Any],
        portfolio_leverage: float,
        circuit_breaker_active: bool = False,
        daily_loss_pct: float = 0.0,
        consecutive_losses: int = 0,
        open_positions: Optional[list] = None,
        max_single_position_pct: float = 3.0,
        max_portfolio_leverage: float = 8.0,
        correlation_to_open: float = 0.0,
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Risk Guard Agent to check safety gates and prevent catastrophic losses.

        Returns:
            Risk check dict with approved, risk_flags, max_size_allowed, etc or None on failure.
        """
        if not _PHASE_4A_AGENTS_AVAILABLE:
            return None
        return build_risk_guard(
            self,
            proposed_trade=proposed_trade,
            portfolio_leverage=portfolio_leverage,
            circuit_breaker_active=circuit_breaker_active,
            daily_loss_pct=daily_loss_pct,
            consecutive_losses=consecutive_losses,
            open_positions=open_positions,
            max_single_position_pct=max_single_position_pct,
            max_portfolio_leverage=max_portfolio_leverage,
            correlation_to_open=correlation_to_open,
            model_for_trigger=model_for_trigger,
        )

    def get_routing_decision(
        self,
        signal: Dict[str, Any],
        market_state: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        system_state: Optional[Dict[str, Any]] = None,
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Agent Router to determine which agents to call and how.

        Returns:
            Routing dict with route, agents_to_call, agent_configs, etc or None on failure.
        """
        if not _PHASE_4A_AGENTS_AVAILABLE:
            return None
        return build_agent_router(
            self,
            signal=signal,
            market_state=market_state,
            portfolio_state=portfolio_state,
            system_state=system_state,
            model_for_trigger=model_for_trigger,
        )

    def get_final_decision(
        self,
        position_sizer_output: Dict[str, Any],
        entry_optimizer_output: Dict[str, Any],
        risk_guard_output: Dict[str, Any],
        exit_advisor_output: Dict[str, Any],
        original_signal: Dict[str, Any],
        route: str = "normal_pipeline",
        model_for_trigger: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run Consensus Builder Agent to merge all specialist outputs into final trade decision.

        Returns:
            Final decision dict with final_decision (execute|skip), trade parameters, etc or None on failure.
        """
        if not _PHASE_4A_AGENTS_AVAILABLE:
            return None
        return build_consensus_builder(
            self,
            position_sizer_output=position_sizer_output,
            entry_optimizer_output=entry_optimizer_output,
            risk_guard_output=risk_guard_output,
            exit_advisor_output=exit_advisor_output,
            original_signal=original_signal,
            route=route,
            model_for_trigger=model_for_trigger,
        )

    def _build_overseer_input(self) -> str:
        """Build comprehensive system state for the Overseer agent."""
        state: Dict[str, Any] = {}

        # 1. Self-performance stats
        try:
            from llm.self_performance import get_performance_stats
            perf = get_performance_stats()
            if perf:
                state["self_perf"] = {
                    "accuracy": round(perf.get("accuracy", 0.5), 2),
                    "veto_accuracy": round(perf.get("veto_accuracy", 0.5), 2),
                    "calibration": round(perf.get("calibration", 0), 2),
                    "streak": perf.get("streak", ""),
                    "total_decisions": perf.get("total_decisions", 0),
                    "regime_accuracy": perf.get("regime_accuracy", {}),
                    "symbol_accuracy": perf.get("symbol_accuracy", {}),
                    "go_count": perf.get("go_count", 0),
                    "skip_count": perf.get("skip_count", 0),
                    "flip_count": perf.get("flip_count", 0),
                }
        except Exception:
            pass

        # 2. Survival metrics
        try:
            from llm.survival_pressure import get_survival_state
            surv = get_survival_state()
            state["survival"] = {
                "score": round(surv.survival_score, 0),
                "trajectory": surv.trajectory,
                "total_trades": surv.total_trades,
                "total_wins": surv.total_wins,
                "total_pnl": round(surv.total_pnl, 1),
                "drawdown_pct": round(surv.current_drawdown_pct, 1),
                "funding_paid": round(surv.total_funding_paid, 1),
                "streak": surv.current_streak,
            }
        except Exception:
            pass

        # 3. Strategy performance (from deep memory)
        try:
            from llm.deep_memory import get_deep_memory
            dm = get_deep_memory()
            strat_wr = dm.trade_dna.get_win_rate_by("strategy")
            if strat_wr:
                state["strategy_perf"] = {
                    k: {"wr": round(v.get("wins", 0) / max(v.get("total", 1), 1) * 100),
                        "n": v.get("total", 0),
                        "pnl": round(v.get("pnl", 0), 1)}
                    for k, v in strat_wr.items() if v.get("total", 0) >= 3
                }
            # Setup edge map
            setup_wr = dm.trade_dna.get_win_rate_by("setup_type")
            if setup_wr:
                state["setup_edge"] = {
                    k: {"wr": round(v.get("wins", 0) / max(v.get("total", 1), 1) * 100),
                        "n": v.get("total", 0)}
                    for k, v in setup_wr.items() if v.get("total", 0) >= 5
                }
        except Exception:
            pass

        # 4. Growth state (enriched with integrator context)
        try:
            from llm.learning_integrator import get_learning_integrator
            integrator = get_learning_integrator()
            # Get enriched context (includes growth + veto accuracy + session perf +
            # symbol patterns + feedback status + self-improvement)
            enriched = integrator.get_enriched_llm_context()
            if enriched:
                state["growth"] = enriched[:600]
        except Exception:
            # Fallback to basic growth context
            try:
                from llm.growth.orchestrator import get_growth_orchestrator
                growth = get_growth_orchestrator()
                ctx = growth.get_llm_context()
                if ctx:
                    state["growth"] = ctx[:400]
            except Exception:
                pass

        # 5. Cost tracking
        try:
            from llm.cost_tracker import get_daily_summary
            cost = get_daily_summary()
            if cost:
                state["cost"] = cost
        except Exception:
            pass

        # 6. Recent trade outcomes
        try:
            from llm.survival_pressure import get_survival_state
            surv = get_survival_state()
            recent = surv.recent_outcomes[-20:]
            recent_pnl = surv.recent_pnls[-20:]
            if recent:
                state["recent_trades"] = {
                    "outcomes": "".join("W" if o == "WIN" else "L" for o in recent),
                    "pnls": [round(p, 1) for p in recent_pnl[-10:]],
                }
        except Exception:
            pass

        # 7. Agent pipeline scratchpad (what other agents have written)
        try:
            scratchpad = get_pipeline_scratchpad()
            sp_data = scratchpad.read_all()
            if sp_data:
                state["agent_outputs"] = {
                    k: v for k, v in sp_data.items()
                    if k in ("regime", "trade", "risk", "critic", "scout")
                }
        except Exception:
            pass

        # 8. Historical patterns from replay engine (free — no API calls)
        try:
            from llm.replay_engine import get_historical_patterns
            patterns = get_historical_patterns(max_decisions=200)
            if patterns and "error" not in patterns:
                state["historical_patterns"] = patterns
        except Exception:
            pass

        # 9. Per-agent calibration ledger summaries
        try:
            from llm.agents.calibration_ledger import get_calibration_ledger
            ledger = get_calibration_ledger()
            agent_cals = {}
            for agent_name in ("trade", "critic", "regime"):
                summary = ledger.get_agent_summary(agent_name)
                if summary.get("total_decisions", 0) >= 5:
                    agent_cals[agent_name] = summary
            if agent_cals:
                state["agent_calibrations"] = agent_cals
        except Exception:
            pass

        # 10. Brain upgrades: thesis accuracy, calibration, counterfactual, regime feedback, drawdown
        try:
            from llm.brain_wiring import (
                get_thesis_tracker, get_confidence_calibrator,
                get_counterfactual_learner, get_regime_feedback, get_graduated_risk,
            )
            # Thesis accuracy
            tt = get_thesis_tracker()
            if tt:
                stats = tt.get_accuracy_stats(lookback_days=14, min_samples=3)
                if stats.get("sufficient_data"):
                    state["thesis_accuracy"] = {
                        "overall": round(stats["overall_accuracy"] * 100),
                        "total": stats["total_theses"],
                        "by_regime": {k: round(v["accuracy"] * 100) for k, v in
                                      stats.get("by_regime", {}).items() if v["total"] >= 3},
                    }
            # Confidence calibration
            cc = get_confidence_calibrator()
            if cc:
                cal_summary = cc.get_calibration_summary()
                if cal_summary.get("overall_bias"):
                    state["calibration"] = cal_summary["overall_bias"]
            # Counterfactual (missed opportunities)
            cf = get_counterfactual_learner()
            if cf:
                missed = cf.get_missed_opportunity_stats(lookback_days=7)
                if missed.get("sufficient_data"):
                    state["missed_opportunities"] = {
                        "skips": missed["total_skips"],
                        "would_win": missed["would_win"],
                        "skip_accuracy": round((1 - missed["win_rate_of_skips"]) * 100),
                        "problem_filters": list(missed.get("problem_filters", {}).keys()),
                    }
            # Regime feedback
            rf = get_regime_feedback()
            if rf:
                state["regime_feedback"] = {
                    name: {"wr": round(s.win_rate * 100), "n": s.total_trades,
                           "floor": s.confidence_floor, "risk_mult": s.risk_multiplier}
                    for name, s in rf.regimes.items() if s.total_trades >= 3
                }
            # Graduated risk
            grm = get_graduated_risk()
            if grm and grm.peak_equity > 0:
                state["drawdown_status"] = grm.get_status()
        except Exception as e:
            logger.info(f"[OVERSEER] Brain upgrade injection error: {e}")

        # 11. Network learning summary (edge decay, calibration drift, constraints)
        try:
            from llm.agents.network_learning import get_network_learning
            nl_summary = get_network_learning().format_for_overseer()
            if nl_summary:
                state["network_learning"] = nl_summary[:400]
        except Exception:
            pass

        # 12. Agent self-performance network summary (accuracy, calibration per agent)
        if _AGENT_PERF_AVAILABLE:
            try:
                _net_health = get_perf_tracker().format_network_summary()
                if _net_health:
                    state["agent_network_health"] = _net_health[:500]
            except Exception:
                pass

        return json.dumps(state, separators=(",", ":"), default=str)

    def get_stats(self) -> Dict[str, Any]:
        """Return coordinator statistics SINCE LAST CALL to get_stats().

        Returns delta (per-pipeline) usage, not cumulative. This prevents
        cost_tracker from double-counting tokens on each successive call.
        """
        delta_calls = self._call_count - self._last_reported_calls
        delta_input = self._total_input_tokens - self._last_reported_input
        delta_output = self._total_output_tokens - self._last_reported_output
        delta_latency = self._total_latency_ms - self._last_reported_latency

        # Update watermarks
        self._last_reported_calls = self._call_count
        self._last_reported_input = self._total_input_tokens
        self._last_reported_output = self._total_output_tokens
        self._last_reported_latency = self._total_latency_ms

        return {
            "total_calls": delta_calls,
            "total_input_tokens": delta_input,
            "total_output_tokens": delta_output,
            "total_latency_ms": delta_latency,
            "avg_latency_ms": (
                delta_latency // max(delta_calls, 1)
            ),
        }

    def get_last_pipeline_detail(self) -> Optional[Dict[str, Any]]:
        """Return serialized per-agent outputs from the last pipeline run.

        Returns None if no pipeline has run yet.
        """
        if not self.last_pipeline_results:
            return None

        detail = {}
        for role, output in self.last_pipeline_results.items():
            detail[role.value] = {
                "data": output.data,
                "model": output.model_used,
                "input_tokens": output.input_tokens,
                "output_tokens": output.output_tokens,
                "latency_ms": output.latency_ms,
                "ok": output.ok,
                "error": output.error,
            }
        if self.last_consistency_score is not None:
            detail["_meta"] = {"consistency_score": self.last_consistency_score}
        return detail

    # ── Agent calling ───────────────────────────────────────────

    def _call_agent(
        self,
        role: AgentRole,
        input_json: str,
        fallback_model: Optional[str] = None,
    ) -> AgentOutput:
        """Call a single specialist agent."""
        # Budget check: stop all calls if daily budget exceeded
        try:
            from llm.cost_tracker import get_cost_tracker
            if get_cost_tracker().get_budget_used_pct() >= 1.0:
                logger.warning(f"[COORD] BUDGET EXCEEDED — skipping {role.value} agent call")
                return AgentOutput(role=role, data={}, error="budget_exceeded")
        except Exception:
            pass

        config = self.configs.get(role, DEFAULT_AGENT_CONFIGS.get(role))
        if config is None or not config.enabled:
            return AgentOutput(role=role, data={}, error="disabled")

        prompt = AGENT_PROMPTS.get(role.value)
        if not prompt:
            return AgentOutput(role=role, data={}, error="no_prompt")

        # Enrich prompt with latest quant intelligence from deep memory
        # Skip in backtest mode — insight_journal/network_learning contain pre-overhaul data
        _bt = getattr(self, '_current_is_backtest', False)
        _enrich_on = os.getenv("AGENT_ENRICH_ENABLED", "true").lower() in ("1", "true", "yes", "on")
        if not _bt and _enrich_on:
            try:
                prompt = enrich_prompt(role.value, prompt)
            except Exception as e:
                logger.debug(f"[COORD] Prompt enrichment failed for {role.value}: {e}")

        # Inject thought protocol and shared context into the prompt
        protocol_prefix = build_protocol_prefix(role.value)
        scratchpad = get_pipeline_scratchpad()
        # Extract symbol from input JSON for asset DNA injection
        _agent_sym = ""
        try:
            _inp = json.loads(input_json) if isinstance(input_json, str) else input_json
            _agent_sym = _inp.get("symbol", _inp.get("sym", ""))
        except Exception:
            pass
        shared_context = build_shared_context_block(
            agent_role=role.value,
            scratchpad=scratchpad,
            shared_lessons=get_shared_lessons(),
            include_axioms=(role in (AgentRole.TRADE, AgentRole.CRITIC, AgentRole.OVERSEER, AgentRole.QUANT)),
            include_regime_map=(role in (AgentRole.TRADE, AgentRole.CRITIC, AgentRole.OVERSEER)),
            include_strategy_theory=(role in (AgentRole.TRADE, AgentRole.CRITIC, AgentRole.OVERSEER, AgentRole.QUANT)),
            current_regime=scratchpad.read_by_key("regime") or "",
            symbol=_agent_sym,
        )

        # Dynamic calibration injection for Trade, Critic, Regime, and Risk agents
        # (RISK added 2026-07-02, R17: its calibration now reaches it as prompt
        # context instead of mechanically downgrading its skip verdicts.)
        # Skip in backtest: calibration ledger tracks live-trade agent accuracy and
        # would penalise confidence based on post-backtest-window performance data.
        calibration_prefix = ""
        _bt = getattr(self, '_current_is_backtest', False)
        if not _bt and role in (AgentRole.TRADE, AgentRole.CRITIC, AgentRole.REGIME, AgentRole.RISK):
            try:
                from llm.agents.calibration_ledger import get_calibration_ledger
                ledger = get_calibration_ledger()
                current_regime = scratchpad.read_by_key("regime") or ""
                calibration_prefix = ledger.get_prompt_calibration(role.value, current_regime)
            except Exception:
                pass

        # Agent brain context injection (beliefs, performance, calibration)
        # Skip in backtest: brain context includes graduated rules and quant priors
        # derived from live trading AFTER the backtest window — look-ahead bias.
        brain_prefix = ""
        if _EXTENSIONS_AVAILABLE and not _bt:
            try:
                current_regime = scratchpad.read_by_key("regime") or ""
                brain_prefix = get_brain_context_for_agent(role.value, regime=current_regime)
            except Exception:
                pass

        # Build the dynamic prefix separately from the stable agent prompt so
        # Anthropic prompt caching can hit on repeated calls. The stable
        # agent prompt (`prompt`) is passed as `cacheable_prefix` — it's
        # bytewise identical across calls to the same agent. The dynamic
        # content (calibration, brain, protocol, shared_context) is in the
        # second non-cached block via `system_prompt`.
        dynamic_parts = []
        if calibration_prefix:
            dynamic_parts.append(f"CALIBRATION: {calibration_prefix}")
        if brain_prefix:
            dynamic_parts.append(f"BRAIN: {brain_prefix}")
        if protocol_prefix:
            dynamic_parts.append(protocol_prefix)
        if shared_context:
            dynamic_parts.append(f"SHARED CONTEXT: {shared_context}")
        dynamic_prefix = "\n\n".join(dynamic_parts) if dynamic_parts else ""

        model = config.model_override or fallback_model or _get_default_model(role)

        # Global Sonnet/Opus throttle: only the expensive tier is gated, so
        # under parallel symbol scan we never exceed LLM_SONNET_CONCURRENCY
        # concurrent Sonnet/Opus CLI sessions. Haiku stages skip the semaphore
        # entirely. Acquire is a context manager so the permit is always
        # released even if the call raises/times out.
        _throttle = _is_sonnet_or_opus(model)
        _sem_acquired = False
        try:
            if _throttle:
                _SONNET_SEMAPHORE.acquire()
                _sem_acquired = True

            # Route through CLI (subscription) or Anthropic API depending on config.
            if _should_use_cli():
                raw_text, usage = _call_llm_via_cli(
                    system_prompt=dynamic_prefix or "",
                    snapshot_json=input_json,
                    model=model,
                    max_tokens=config.max_tokens,
                    timeout=config.timeout_s,
                    cacheable_prefix=prompt,
                )
                logger.debug(f"[COORD-CLI] {role.value} latency={usage.get('latency_ms',0)}ms")
            elif dynamic_prefix:
                raw_text, usage = call_llm(
                    system_prompt=dynamic_prefix,
                    snapshot_json=input_json,
                    model=model,
                    max_tokens=config.max_tokens,
                    max_retries=1,
                    timeout=config.timeout_s,
                    cacheable_prefix=prompt,
                )
            else:
                raw_text, usage = call_llm(
                    system_prompt=prompt,
                    snapshot_json=input_json,
                    model=model,
                    max_tokens=config.max_tokens,
                    max_retries=1,
                    timeout=config.timeout_s,
                )
        finally:
            if _sem_acquired:
                _SONNET_SEMAPHORE.release()

        self._call_count += 1
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        self._total_input_tokens += in_tok
        self._total_output_tokens += out_tok

        # Cost tracking handled by client.py — do NOT double-count here

        if raw_text is None:
            api_error = usage.get("error", "unknown")
            logger.warning(
                f"[MULTI-AGENT] {role.value} agent API call FAILED: {api_error} "
                f"(model={model}, latency={usage.get('latency_ms', 0)}ms)"
            )
            return AgentOutput(
                role=role,
                data={},
                model_used=model,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=usage.get("latency_ms", 0),
                error=f"api_error: {api_error}",
            )

        # Parse JSON response
        parsed = _parse_agent_json(raw_text)
        if parsed is None:
            logger.warning(
                f"[MULTI-AGENT] {role.value} agent returned unparseable response "
                f"(model={model}, {len(raw_text)} chars). "
                f"First 200 chars: {raw_text[:200]}"
            )
            return AgentOutput(
                role=role,
                data={},
                raw_text=raw_text[:500],
                model_used=model,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=usage.get("latency_ms", 0),
                error=f"json_parse_failed",
            )

        return AgentOutput(
            role=role,
            data=parsed,
            raw_text=raw_text[:500],
            model_used=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=usage.get("latency_ms", 0),
        )

    # ── Input builders ──────────────────────────────────────────
    # Each agent gets the FULL context relevant to its domain.
    # The snapshot dict contains these rich knowledge fields:
    #   knowledge      — axioms, principles, anti-patterns from self-teaching curriculum
    #   deep_memory    — trade DNA, strategy fingerprints, pattern library, regime history
    #   examples       — few-shot examples of similar past trades
    #   growth         — growth intelligence (hypotheses, recommendations, recent outcomes)
    #   survival       — accountability/survival pressure context
    #   self_perf      — LLM's own track record (accuracy, calibration, regime WR)
    #   recent_dec     — last 3 decisions for consistency
    #   recent_lessons — immediate feedback from closed trades
    #   autopsy        — structured analysis of last 5 trades
    #   mem            — short-term memory notes
    #   session_perf   — win rates by trading session
    #   regime_shifts  — symbols with regime transitions
    #   cross_sym      — cross-symbol lead-lag signals
    #   cross_pat      — validated cross-symbol patterns

    def _build_quant_input(self, snapshot: dict, regime_out: AgentOutput) -> str:
        """Build quant agent input: market data + regime + historical stats.

        The Quant Agent needs:
        - Raw market data (prices, volume, funding, signals) for statistical analysis
        - Regime classification for conditional probability computation
        - Historical win rates per setup type, strategy, regime, symbol
        - Recent trade outcomes for variance/drawdown analysis
        - Confluence quality for signal vs noise assessment
        """
        quant_data = {}

        # Market data
        if "m" in snapshot:
            quant_data["markets"] = snapshot["m"]
        if "g" in snapshot:
            quant_data["global"] = snapshot["g"]

        # Regime from Regime Agent
        quant_data["regime"] = regime_out.data

        # Compute confluence quality
        confluence = _compute_confluence_from_snapshot(
            snapshot, regime_out.data.get("rg", "unknown")
        )
        if confluence:
            quant_data["confluence"] = confluence

        # Historical stats for conditional probability computation
        # Skip in backtest: setup_edge and strategy_perf are computed from live
        # trading history that post-dates the backtest window — look-ahead bias.
        if not snapshot.get("_is_backtest") and "g" in snapshot:
            g = snapshot["g"]
            if isinstance(g, dict):
                if "edge" in g:
                    quant_data["setup_edge"] = g["edge"]
                if "stperf" in g:
                    quant_data["strategy_perf"] = g["stperf"]

        # Self-performance for calibration/drawdown context
        if "self_perf" in snapshot:
            quant_data["self_perf"] = snapshot["self_perf"]

        # Recent outcomes for variance analysis
        if "autopsy" in snapshot:
            quant_data["autopsy"] = snapshot["autopsy"]

        # Funding data for cost computation
        for key in ("funding_cost_pct", "funding_alert", "port_lev"):
            if key in snapshot:
                quant_data[key] = snapshot[key]

        # Real quant data backbone: Kelly, conditional edge, fat-tail, priors
        # Skip in backtest: quant provider computes priors from live-trading history
        # accumulated AFTER the backtest window — look-ahead bias.
        if not snapshot.get("_is_backtest"):
            try:
                from llm.quant_data import get_quant_provider
                qp = get_quant_provider()
                regime = regime_out.data.get("rg", "unknown") if regime_out.ok else "unknown"
                n_agree = 0
                setup_type = ""
                if "m" in snapshot:
                    for mkt in (snapshot["m"] if isinstance(snapshot["m"], list) else []):
                        sigs = mkt.get("sg", mkt.get("sigs", []))
                        if sigs:
                            n_agree = max(n_agree, len(sigs))
                            if isinstance(sigs[0], dict):
                                setup_type = sigs[0].get("st", "")
                quant_package = qp.build_quant_package(regime=regime, num_agree=n_agree, setup_type=setup_type)
                if quant_package:
                    quant_data["quant"] = quant_package
            except Exception as e:
                logger.debug(f"Quant data injection error: {e}")

        # Per-agent calibration for Bayesian updating
        # Skip in backtest: same reasoning as agent_cal — stale live-trade accuracy
        # data would make Quant deflate confidence before Trade agent even decides.
        if not snapshot.get("_is_backtest"):
            try:
                from llm.agents.calibration_ledger import get_calibration_ledger
                ledger = get_calibration_ledger()
                regime = regime_out.data.get("rg", "unknown")
                cal = ledger.get_calibration("trade", regime)
                if cal.get("reliable"):
                    quant_data["trade_calibration"] = cal
            except Exception:
                pass

        # Historical patterns from replay engine
        # Skip in backtest: replay engine reads from live decisions.jsonl which
        # post-dates the backtest window — look-ahead bias.
        if not snapshot.get("_is_backtest"):
            try:
                from llm.replay_engine import get_historical_patterns
                patterns = get_historical_patterns(max_decisions=100)
                if patterns and "error" not in patterns:
                    compact = {}
                    for k in ("regime_wr", "conf_low_wr", "conf_mid_wr", "conf_high_wr",
                               "conf_low_n", "conf_mid_n", "conf_high_n",
                               "max_win_streak", "max_loss_streak", "recent_outcomes"):
                        if k in patterns:
                            compact[k] = patterns[k]
                    if compact:
                        quant_data["historical"] = compact
            except Exception:
                pass

        # Enriched context from technicals, feedback, telemetry, positions
        if snapshot.get("enriched_context"):
            quant_data["enriched"] = snapshot["enriched_context"]

        # Per-agent self-performance stats for the Quant Agent (skip in backtest)
        _pt = getattr(self, '_perf_tracker_ref', None)
        if _pt and not snapshot.get("_is_backtest"):
            try:
                _self_perf_text = _pt.format_for_agent("quant")
                if _self_perf_text:
                    quant_data["self_performance"] = _self_perf_text
            except Exception:
                pass

        return json.dumps(quant_data, separators=(",", ":"), default=str)

    def _compute_regime_fallback(self, snapshot: dict) -> str:
        """Technical regime classification when LLM returns 'unknown'.

        Uses price changes, volatility, and volume from snapshot to classify:
        - Large moves (>3% 24h) with volume → 'trend'
        - Large moves with extreme volatility → 'high_volatility'
        - Tight ranges (<1% 24h) → 'range'
        - Multiple symbols dropping hard → 'panic'
        - Default → 'consolidation' (safe neutral)
        """
        markets = snapshot.get("m", [])
        if not markets:
            return "consolidation"

        pct_changes = []
        vol_signals = []
        for mkt in (markets if isinstance(markets, list) else []):
            # Try to extract price change from various snapshot formats
            pct_24h = mkt.get("pct_24h", mkt.get("chg24h", 0))
            if isinstance(pct_24h, str):
                try:
                    pct_24h = float(pct_24h.replace("%", ""))
                except (ValueError, TypeError):
                    pct_24h = 0
            pct_changes.append(abs(float(pct_24h or 0)))

            # Volume clues
            vol_ratio = mkt.get("vol_ratio", mkt.get("vr", 1.0))
            if isinstance(vol_ratio, (int, float)) and vol_ratio > 2.0:
                vol_signals.append(True)

        if not pct_changes:
            return "consolidation"

        avg_move = sum(pct_changes) / len(pct_changes)
        max_move = max(pct_changes)
        drops = sum(1 for m in (markets if isinstance(markets, list) else [])
                     if float(m.get("pct_24h", m.get("chg24h", 0)) or 0) < -5)

        # Panic: multiple symbols dropping >5%
        if drops >= 2:
            return "panic"
        # High volatility: extreme moves
        if max_move > 8 or avg_move > 5:
            return "high_volatility"
        # Trend: clear directional move with decent volume
        if avg_move > 2.5 or (avg_move > 1.5 and len(vol_signals) >= 1):
            return "trend"
        # Range: very tight moves
        if avg_move < 1.0:
            return "range"
        # Default: consolidation
        return "consolidation"

    def _build_regime_input(self, snapshot: dict) -> str:
        """Build regime agent input: markets + global + regime history.

        The Regime Agent needs:
        - Raw market data (prices, volume, funding, OI) to classify regime
        - Regime transition history to detect shifts
        - Cross-symbol data for correlation analysis
        - Deep memory regime history for pattern matching
        """
        regime_data = {}
        if "m" in snapshot:
            regime_data["markets"] = snapshot["m"]
        if "g" in snapshot:
            regime_data["global"] = snapshot["g"]
        # Regime-specific knowledge
        if "regime_shifts" in snapshot:
            regime_data["regime_shifts"] = snapshot["regime_shifts"]
        if "cross_sym" in snapshot:
            regime_data["cross_symbol_signals"] = snapshot["cross_sym"]
        if "cross_pat" in snapshot:
            regime_data["cross_patterns"] = snapshot["cross_pat"]
        # Deep memory regime section (extract just regime history if available)
        if "deep_memory" in snapshot:
            dm = str(snapshot["deep_memory"])
            # Find regime-relevant sections
            regime_section = _extract_section(dm, "REGIME", 300)
            if regime_section:
                regime_data["regime_history"] = regime_section
        # External data: funding/OI + liquidation (critical for regime classification)
        if "ext_funding" in snapshot:
            regime_data["ext_funding"] = snapshot["ext_funding"]
        if "ext_liq" in snapshot:
            regime_data["ext_liq"] = snapshot["ext_liq"]
        if "ext_summary" in snapshot:
            regime_data["ext_data"] = snapshot["ext_summary"]
        if "ext_depth_summary" in snapshot:
            regime_data["ext_depth"] = snapshot["ext_depth_summary"]
        # Enriched context from technicals, feedback, telemetry, positions
        if snapshot.get("enriched_context"):
            regime_data["enriched"] = snapshot["enriched_context"]

        # Mechanical regime overlay (RQ10, env-gated upstream): labeled
        # additive context — NOT an override; the agent still decides.
        if snapshot.get("mech_regime"):
            try:
                from llm.agents.mech_regime import format_mech_regime
                regime_data["mechanical_classifier"] = format_mech_regime(
                    snapshot["mech_regime"]
                )
            except Exception:
                pass

        # Per-agent self-performance stats for the Regime Agent (skip in backtest)
        _pt = getattr(self, '_perf_tracker_ref', None)
        if _pt and not snapshot.get("_is_backtest"):
            try:
                _self_perf_text = _pt.format_for_agent("regime")
                if _self_perf_text:
                    regime_data["self_performance"] = _self_perf_text
            except Exception:
                pass

        # Surface historical edge data (setup_mfe) for regime context
        try:
            _g = snapshot.get("g", {}) or {}
            _setup_mfe = _g.get("setup_mfe", {}) if isinstance(_g, dict) else {}
            if _setup_mfe:
                regime_data["edge_data"] = _setup_mfe
        except Exception:
            pass

        return json.dumps(regime_data, separators=(",", ":"), default=str)

    def _build_trade_input(self, snapshot: dict, regime_out: AgentOutput, quant_out: Optional[AgentOutput] = None) -> str:
        """Build trade agent input: FULL context for the main decision-maker.

        The Trade Agent is the primary brain — it gets EVERYTHING:
        - Full market snapshot (prices, signals, volume, funding, OI)
        - Regime classification from the Regime Agent
        - Knowledge base (axioms, principles from self-teaching curriculum)
        - Deep memory (trade DNA, strategy fingerprints, pattern library)
        - Few-shot examples of similar past trades
        - Growth intelligence (hypotheses, recommendations, learning progress)
        - Recent decisions (for consistency)
        - Recent lessons (immediate feedback from closed trades)
        - Trade autopsy (structured analysis of last batch)
        - Memory notes (short-term observations)
        - Session performance (time-of-day edge)
        - Survival pressure (accountability context)
        """
        trade_data = dict(snapshot)
        # Inject regime agent's output so trade agent knows the classified regime
        trade_data["regime_analysis"] = regime_out.data

        # Surface historical edge data prominently (was buried in g.setup_mfe)
        # This is CRITICAL: the Trade Agent must see WR/PF/n for the exact
        # symbol+side it's evaluating to reason about whether the setup has proven edge.
        try:
            _g = snapshot.get("g", {}) or {}
            _setup_mfe = _g.get("setup_mfe", {}) if isinstance(_g, dict) else {}
            if _setup_mfe:
                trade_data["edge_data"] = _setup_mfe
        except Exception:
            pass

        # Compute and inject confluence quality scoring
        confluence = _compute_confluence_from_snapshot(
            snapshot, regime_out.data.get("rg", "unknown")
        )
        if confluence:
            trade_data["confluence"] = confluence

        # Ensure all knowledge/learning fields are present with generous limits
        # (the Trade Agent is the decision-maker — don't starve it of context)
        # Knowledge from self-teaching system (axioms, principles, hypotheses)
        if "_enr_knowledge" in snapshot and snapshot["_enr_knowledge"]:
            trade_data["knowledge"] = snapshot["_enr_knowledge"][:1000]
        else:
            _ensure_field(trade_data, "knowledge", snapshot, max_len=1000)
        _ensure_field(trade_data, "deep_memory", snapshot, max_len=1000)
        _ensure_field(trade_data, "examples", snapshot, max_len=600)
        _ensure_field(trade_data, "growth", snapshot, max_len=500)
        _ensure_field(trade_data, "survival", snapshot, max_len=300)
        _ensure_field(trade_data, "self_perf", snapshot)
        _ensure_field(trade_data, "recent_dec", snapshot)
        _ensure_field(trade_data, "recent_lessons", snapshot)
        _ensure_field(trade_data, "autopsy", snapshot)
        _ensure_field(trade_data, "mem", snapshot, max_len=800)
        _ensure_field(trade_data, "session_perf", snapshot)
        _ensure_field(trade_data, "cross_sym", snapshot)
        _ensure_field(trade_data, "cross_pat", snapshot)
        _ensure_field(trade_data, "corr_risk", snapshot)
        _ensure_field(trade_data, "port_lev", snapshot)
        _ensure_field(trade_data, "funding_cost_pct", snapshot)
        _ensure_field(trade_data, "funding_alert", snapshot)
        # External collector data: funding/OI, liq levels, shadow MR
        _ensure_field(trade_data, "ext_funding", snapshot)
        _ensure_field(trade_data, "ext_liq", snapshot)
        _ensure_field(trade_data, "ext_mr", snapshot)
        _ensure_field(trade_data, "ext_summary", snapshot)
        _ensure_field(trade_data, "ext_depth_summary", snapshot)

        # Network learning: inject accumulated lessons for Trade Agent
        if "network_lessons_trade" in snapshot:
            trade_data["network_lessons"] = snapshot["network_lessons_trade"]
        if "network_calibration_adj" in snapshot:
            trade_data["calibration_adj"] = snapshot["network_calibration_adj"]

        # Inject filter annotations so Trade Agent sees what filters measured
        if "filt" in snapshot:
            trade_data["filter_assessment"] = snapshot["filt"]
        if "near" in snapshot:
            trade_data["near_miss_signals"] = snapshot["near"]

        # Inject Quant Agent's statistical analysis if available
        if quant_out and quant_out.ok:
            trade_data["quant_analysis"] = quant_out.data

        # Gap 4+5: Inject per-agent calibration data into trade input
        # Skip in backtest: agent-cal reflects live-trading accuracy which has been
        # contaminated by the fallback-approve era and would distort fresh evaluations.
        if not snapshot.get("_is_backtest"):
            try:
                from llm.agents.calibration_ledger import get_calibration_ledger
                ledger = get_calibration_ledger()
                cal_data = ledger.get_compact_for_snapshot("trade")
                if cal_data:
                    trade_data["agent_cal"] = cal_data
            except Exception:
                pass

        # Inject similar patterns + known failure modes from deep memory
        # Skip in backtest: deep memory is populated from live trades that post-date
        # the backtest window — injecting it causes look-ahead bias (Bug #16).
        if not snapshot.get("_is_backtest"):
            try:
                from llm.deep_memory import get_deep_memory
                dm = get_deep_memory()
                regime = regime_out.data.get("rg", "unknown") if regime_out else "unknown"
                # Find signals that have patterns from this market context
                signals = snapshot.get("signals", [])
                symbol = ""
                if signals:
                    symbol = signals[0].get("sym", "") if isinstance(signals[0], dict) else ""

                # PatternLibrary: find similar historical patterns for context
                similar = dm.patterns.find_similar(
                    pattern_type="trade_setup",
                    symbol=symbol,
                    regime=regime,
                    limit=3,
                )
                if similar:
                    trade_data["similar_patterns"] = [
                        {k: v for k, v in p.items() if k in ("type", "symbol", "regime", "outcome", "lesson")}
                        for p in similar[:3]
                    ]

                # TradeDNAStore: get recent failure patterns to avoid
                failures = dm.trade_dna.get_failures(limit=5)
                if failures:
                    trade_data["recent_failures"] = [
                        {k: v for k, v in f.items() if k in ("symbol", "side", "regime", "strategy", "pnl", "lesson")}
                        for f in failures[:3]
                    ]
            except Exception:
                pass

        # ── Brain Intelligence Injection ──
        # Skip in backtest: brain context carries graduated rules and quant priors
        # from live trading AFTER the backtest window — look-ahead bias (Bug #16).
        if not snapshot.get("_is_backtest"):
            try:
                from llm.brain_wiring import get_brain_context_for_trade
                regime = regime_out.data.get("rg", "unknown") if regime_out else "unknown"
                symbol = ""
                signals = snapshot.get("signals", [])
                if signals and isinstance(signals[0], dict):
                    symbol = signals[0].get("sym", "")
                elif "m" in snapshot and snapshot["m"]:
                    symbol = snapshot["m"][0].get("s", snapshot["m"][0].get("sym", ""))
                brain_ctx = get_brain_context_for_trade(symbol, regime)
                if brain_ctx:
                    trade_data["brain"] = brain_ctx
            except Exception as e:
                logger.info(f"[MULTI-AGENT] Brain context injection error: {e}")

        # Inject Scout Agent findings (rename from agent_outputs.scout to scout_preparation
        # so Trade Agent prompt can find it by the documented key name)
        try:
            scratchpad = get_pipeline_scratchpad()
            scout_data = scratchpad.read("scout")
            if scout_data:
                trade_data["scout_preparation"] = scout_data
        except Exception:
            pass

        # Enriched context (already in trade_data via dict(snapshot), rename key)
        if "enriched_context" in trade_data:
            trade_data["enriched"] = trade_data.pop("enriched_context")

        # Inject structured enrichment fields (named keys instead of one blob)
        _enr_map = {
            "_enr_tech": "tech", "_enr_tech_5m": "tech_5m",
            "_enr_feedback": "feedback", "_enr_pipeline": "pipeline",
            "_enr_positions": "positions", "_enr_portfolio": "portfolio",
            "_enr_journal": "journal", "_enr_exec": "exec_quality",
            "_enr_reflection": "reflection",
        }
        for _snap_key, _agent_key in _enr_map.items():
            if _snap_key in snapshot:
                trade_data[_agent_key] = snapshot[_snap_key]

        # Per-agent self-performance stats for the Trade Agent
        # Skip in backtest: perf tracker has contaminated live-trade accuracy data.
        _pt = getattr(self, '_perf_tracker_ref', None)
        if _pt and not snapshot.get("_is_backtest"):
            try:
                _self_perf_text = _pt.format_for_agent("trade")
                if _self_perf_text:
                    trade_data["self_performance"] = _self_perf_text
            except Exception:
                pass

        # Pre-trade simulation results (scenario analysis)
        if "_simulation" in snapshot:
            trade_data["simulation"] = snapshot["_simulation"]

        # LLM-first signal metadata: in LLM_FIRST_MODE, Trade Agent
        # evaluates quality that mechanical gates used to handle.
        if "signal_metadata" in snapshot:
            sm = snapshot["signal_metadata"]
            trade_data["signal_quality_data"] = {
                "chop_score": sm.get("chop_score"),
                "chop_score_smoothed": sm.get("chop_score_smoothed"),
                "win_prob": sm.get("win_prob"),
                "ev_per_dollar": sm.get("ev_per_dollar"),
                "fee_drag_pct": sm.get("fee_drag_pct"),
                "rr_tp1": sm.get("rr_tp1"),
                "rr_tp2": sm.get("rr_tp2"),
                "stop_width_pct": sm.get("stop_width_pct"),
                "num_agree": sm.get("num_agree"),
                "strategies_agree": sm.get("strategies_agree"),
                "regime_4h_aligned": sm.get("regime_4h_aligned"),
                "regime_1h": sm.get("regime_1h"),
                "regime_4h": sm.get("regime_4h"),
                "mechanical_floor": sm.get("mechanical_confidence_floor"),
                "would_pass_floor": sm.get("would_pass_confidence_floor"),
                "funding_rate": sm.get("funding_rate"),
                "volume_ratio": sm.get("volume_ratio"),
                "time_utc_hour": sm.get("time_utc_hour"),
                "btc_trend": sm.get("btc_trend"),
            }
            if sm.get("graduated_rules_advisory") and not snapshot.get("_is_backtest"):
                trade_data["graduated_rules_advisory"] = sm["graduated_rules_advisory"]

        return json.dumps(trade_data, separators=(",", ":"), default=str)

    def _build_risk_input(
        self, snapshot: dict, regime_out: AgentOutput, trade_out: AgentOutput,
        quant_out: Optional[AgentOutput] = None,
    ) -> str:
        """Build risk agent input: portfolio state + trade decision + regime + risk knowledge.

        The Risk Agent needs:
        - The trade decision (to size it)
        - Regime classification (sizing depends on regime)
        - Full portfolio state (positions, leverage, correlation, funding)
        - Session performance (time-of-day risk)
        - Self-performance (are we on a losing streak?)
        - Recent autopsy (are we declining?)
        - Deep memory risk insights
        """
        risk_data = {
            "regime": regime_out.data,
            "trade_decision": trade_out.data,
        }
        # Portfolio data
        for key in ("g", "pos", "corr_risk", "port_lev", "funding_cost_pct",
                     "funding_alert", "session_perf"):
            if key in snapshot:
                risk_data[key] = snapshot[key]
        # Portfolio-level intelligence (computed earlier in pipeline)
        if "_portfolio_state" in snapshot:
            risk_data["portfolio"] = snapshot["_portfolio_state"]
        # Filter annotations for risk-aware sizing (fd, ev, cr matter for sizing)
        if "filt" in snapshot:
            risk_data["filter_assessment"] = snapshot["filt"]
        # Self-awareness for risk decisions (losing streak → reduce size)
        if "self_perf" in snapshot:
            risk_data["self_perf"] = snapshot["self_perf"]
        if "autopsy" in snapshot:
            risk_data["autopsy"] = snapshot["autopsy"]
        # Deep memory risk-relevant sections
        if "deep_memory" in snapshot:
            dm = str(snapshot["deep_memory"])
            risk_section = _extract_section(dm, "RISK", 200)
            strat_section = _extract_section(dm, "STRATEGY", 200)
            if risk_section:
                risk_data["risk_history"] = risk_section
            if strat_section:
                risk_data["strategy_history"] = strat_section
        # Recent lessons about sizing/risk
        if "recent_lessons" in snapshot:
            risk_data["recent_lessons"] = snapshot["recent_lessons"]
        # Inject Quant Agent analysis for Kelly-informed sizing
        if quant_out and quant_out.ok:
            q = quant_out.data
            quant_compact = {}
            if "kelly_fraction" in q:
                quant_compact["kelly"] = q["kelly_fraction"]
            if "ev" in q:
                quant_compact["ev"] = q["ev"]
            if "risk_profile" in q:
                quant_compact["risk_profile"] = q["risk_profile"]
            if "signal_quality" in q:
                quant_compact["signal_quality"] = q["signal_quality"]
            if quant_compact:
                risk_data["quant"] = quant_compact

        # Confluence quality for informed sizing (convergent setups → size up)
        if "confluence" in snapshot:
            risk_data["confluence"] = snapshot["confluence"]
        else:
            # Compute confluence from market signals if available
            markets = snapshot.get("m", [])
            if markets:
                try:
                    confluence = score_confluence(markets[0].get("signals", []))
                    if confluence.get("count", 0) > 0:
                        risk_data["confluence"] = confluence
                except Exception:
                    pass

        # ── Brain Intelligence: regime feedback + graduated risk ──
        # Skip in backtest: same look-ahead bias concern as trade brain injection.
        if not snapshot.get("_is_backtest"):
            try:
                from llm.brain_wiring import get_brain_context_for_risk
                regime = regime_out.data.get("rg", "unknown") if regime_out else "unknown"
                brain_risk = get_brain_context_for_risk(regime)
                if brain_risk:
                    risk_data["brain"] = brain_risk
            except Exception as e:
                logger.info(f"[MULTI-AGENT] Brain risk context error: {e}")

        # External data: liq levels critical for risk sizing
        _ensure_field(risk_data, "ext_liq", snapshot)
        _ensure_field(risk_data, "ext_funding", snapshot)
        _ensure_field(risk_data, "ext_depth_summary", snapshot)

        # Network learning: inject risk constraints and lessons
        if "network_lessons_risk" in snapshot:
            risk_data["network_lessons"] = snapshot["network_lessons_risk"]
        if "risk_constraints" in snapshot:
            risk_data["hard_constraints"] = snapshot["risk_constraints"]

        # Enriched context from technicals, feedback, telemetry, positions
        if snapshot.get("enriched_context"):
            risk_data["enriched"] = snapshot["enriched_context"]

        # Inject structured enrichment fields (named keys instead of one blob)
        _enr_map = {
            "_enr_tech": "tech", "_enr_feedback": "feedback",
            "_enr_pipeline": "pipeline", "_enr_portfolio": "portfolio",
            "_enr_exec": "exec_quality", "_enr_reflection": "reflection",
        }
        for _snap_key, _agent_key in _enr_map.items():
            if _snap_key in snapshot:
                risk_data[_agent_key] = snapshot[_snap_key]

        # Per-agent self-performance stats for the Risk Agent (skip in backtest)
        _pt = getattr(self, '_perf_tracker_ref', None)
        if _pt and not snapshot.get("_is_backtest"):
            try:
                _self_perf_text = _pt.format_for_agent("risk")
                if _self_perf_text:
                    risk_data["self_performance"] = _self_perf_text
            except Exception:
                pass

        # Pre-trade simulation for risk-aware sizing
        if "_simulation" in snapshot:
            risk_data["simulation"] = snapshot["_simulation"]

        # LLM-first signal metadata: data that mechanical gates used to check.
        # In LLM_FIRST_MODE, Risk Agent is responsible for evaluating these.
        if "signal_metadata" in snapshot:
            sm = snapshot["signal_metadata"]
            risk_data["signal_quality"] = {
                "chop_score": sm.get("chop_score"),
                "win_prob": sm.get("win_prob"),
                "ev_per_dollar": sm.get("ev_per_dollar"),
                "fee_drag_pct": sm.get("fee_drag_pct"),
                "rr_tp1": sm.get("rr_tp1"),
                "rr_tp2": sm.get("rr_tp2"),
                "stop_width_pct": sm.get("stop_width_pct"),
                "num_agree": sm.get("num_agree"),
                "regime_4h_aligned": sm.get("regime_4h_aligned"),
                "mechanical_floor": sm.get("mechanical_confidence_floor"),
                "would_pass_floor": sm.get("would_pass_confidence_floor"),
                "funding_rate": sm.get("funding_rate"),
                "volume_ratio": sm.get("volume_ratio"),
                "time_utc_hour": sm.get("time_utc_hour"),
                "btc_trend": sm.get("btc_trend"),
            }
            # Graduated rules advisory (what mechanical system would do)
            if sm.get("graduated_rules_advisory") and not snapshot.get("_is_backtest"):
                risk_data["graduated_rules_advisory"] = sm["graduated_rules_advisory"]

        # ── Sizing constraint: pre-compute max_risk_pct so Risk Agent stays under OpsGuard cap ──
        # risk_pct / stop_width_pct = position notional / equity. OpsGuard cap = 500% notional.
        # Without this, Risk Agent sizes blind to remaining capacity and OpsGuard rejects the trade.
        try:
            _port_state = snapshot.get("_portfolio_state", {}) or {}
            _exposure_pct = float(_port_state.get("total_exposure_pct", 0) or 0)
            _remaining_pct = max(0.0, 500.0 - _exposure_pct)  # OpsGuard MAX_SINGLE_POSITION_PCT
            _sm = snapshot.get("signal_metadata", {}) or {}
            _sw_pct = float(_sm.get("stop_width_pct", 0) or 0)
            if _sw_pct > 0:
                # max_risk_pct such that resulting notional <= remaining capacity
                _max_risk_pct = (_remaining_pct / 100.0) * (_sw_pct / 100.0)
                risk_data["sizing_constraint"] = {
                    "current_notional_pct": round(_exposure_pct, 1),
                    "remaining_capacity_pct": round(_remaining_pct, 1),
                    "stop_width_pct": round(_sw_pct, 3),
                    "max_risk_pct": round(_max_risk_pct, 4),
                    "note": (
                        f"risk_pct ceiling={_max_risk_pct:.3f} "
                        f"(stop={_sw_pct:.2f}%, {_exposure_pct:.0f}% notional already deployed, "
                        f"{_remaining_pct:.0f}% cap remaining)"
                    ),
                }
        except Exception:
            pass

        return json.dumps(risk_data, separators=(",", ":"), default=str)

    def _build_critic_input(
        self,
        snapshot: dict,
        regime_out: AgentOutput,
        trade_out: AgentOutput,
        risk_out: Optional[AgentOutput],
    ) -> str:
        """Build critic input: all prior agent outputs + full self-awareness context.

        The Critic Agent is the quality gate — it needs:
        - All prior agent outputs (to check consistency)
        - Self-performance stats (to detect overconfidence/underconfidence)
        - Recent decisions (to check for flip-flopping)
        - Recent lessons (to check if similar setups failed)
        - Trade autopsy (to check for declining performance)
        - Knowledge base (axioms to verify against)
        - Growth intelligence (hypotheses to validate)
        """
        critic_data = {
            "regime_analysis": regime_out.data,
            "trade_decision": trade_out.data,
        }
        if risk_out and risk_out.ok:
            critic_data["risk_assessment"] = risk_out.data

        # Surface historical edge data for the Critic (same as Trade Agent gets)
        # Critic needs this to check: "does this setup have proven edge in this regime?"
        try:
            _g = snapshot.get("g", {}) or {}
            _setup_mfe = _g.get("setup_mfe", {}) if isinstance(_g, dict) else {}
            if _setup_mfe:
                critic_data["edge_data"] = _setup_mfe
        except Exception:
            pass

        # Inject confluence quality so Critic can assess agreement type
        confluence = _compute_confluence_from_snapshot(
            snapshot, regime_out.data.get("rg", "unknown")
        )
        if confluence:
            critic_data["confluence"] = confluence
        # Filter annotations for veto reasoning (weight warnings in decisions)
        if "filt" in snapshot:
            critic_data["filter_assessment"] = snapshot["filt"]
        if "near" in snapshot:
            critic_data["near_miss_signals"] = snapshot["near"]
        # Full self-awareness context — the critic's primary tool
        for key in ("self_perf", "recent_dec", "recent_lessons", "autopsy"):
            if key in snapshot:
                critic_data[key] = snapshot[key]
        # Knowledge for verifying the decision against learned principles
        _ensure_field(critic_data, "knowledge", snapshot, max_len=500)
        # Growth context (hypotheses, recommendations)
        _ensure_field(critic_data, "growth", snapshot, max_len=300)
        # Portfolio state for risk validation
        for key in ("corr_risk", "port_lev", "funding_cost_pct"):
            if key in snapshot:
                critic_data[key] = snapshot[key]
        # Memory notes for pattern checking
        _ensure_field(critic_data, "mem", snapshot, max_len=400)

        # External data: compact summary for critic cross-check
        _ensure_field(critic_data, "ext_summary", snapshot)
        _ensure_field(critic_data, "ext_liq", snapshot)
        _ensure_field(critic_data, "ext_depth_summary", snapshot)

        # Gap 4+5: Inject calibration data for the critic
        # Skip in backtest: calibration ledger is populated from live trade outcomes
        # that post-date the backtest window — look-ahead bias (Bug #16).
        if not snapshot.get("_is_backtest"):
            try:
                from llm.agents.calibration_ledger import get_calibration_ledger
                ledger = get_calibration_ledger()
                cal_data = ledger.get_compact_for_snapshot("critic")
                if cal_data:
                    critic_data["agent_cal"] = cal_data
            except Exception:
                pass

        # Veto counterfactual feedback: show Critic its recent veto outcomes
        # Skip in backtest: veto stats are from live session, not the backtest window.
        if not snapshot.get("_is_backtest"):
            try:
                _pt = getattr(self, '_perf_tracker_ref', None)
                if _pt:
                    veto_stats = _pt.get_veto_stats() if hasattr(_pt, 'get_veto_stats') else None
                    if veto_stats:
                        critic_data["veto_feedback"] = veto_stats
            except Exception:
                pass

        # Network learning: inject past lessons for better veto decisions
        if "network_lessons_critic" in snapshot:
            critic_data["network_lessons"] = snapshot["network_lessons_critic"]

        # Enriched context from technicals, feedback, telemetry, positions
        if snapshot.get("enriched_context"):
            critic_data["enriched"] = snapshot["enriched_context"]

        # Inject structured enrichment fields (named keys instead of one blob)
        _enr_map = {
            "_enr_tech": "tech", "_enr_feedback": "feedback",
            "_enr_pipeline": "pipeline", "_enr_portfolio": "portfolio",
            "_enr_exec": "exec_quality",
        }
        for _snap_key, _agent_key in _enr_map.items():
            if _snap_key in snapshot:
                critic_data[_agent_key] = snapshot[_snap_key]

        # Per-agent self-performance stats for the Critic Agent
        _pt = getattr(self, '_perf_tracker_ref', None)
        if _pt:
            try:
                _self_perf_text = _pt.format_for_agent("critic")
                if _self_perf_text:
                    critic_data["self_performance"] = _self_perf_text
            except Exception:
                pass

        # Pre-trade simulation for stress-testing the thesis
        if "_simulation" in snapshot:
            critic_data["simulation"] = snapshot["_simulation"]

        # Global context for Critic (fixes prompt-data mismatch where prompt
        # references g.cf, g.edge, g.ml but they weren't in the input)
        if "g" in snapshot:
            critic_data["g"] = snapshot["g"]
        # Add edge/WR data from dynamic stats if available
        if "_enr_dynamic_stats" in snapshot:
            critic_data.setdefault("g", {})["edge_stats"] = snapshot["_enr_dynamic_stats"]

        # LLM-first signal quality data for comprehensive review
        if "signal_metadata" in snapshot:
            sm = snapshot["signal_metadata"]
            strategies_agree = sm.get("strategies_agree", [])
            critic_data["signal_quality"] = {
                "chop_score": sm.get("chop_score"),
                "win_prob": sm.get("win_prob"),
                "ev_per_dollar": sm.get("ev_per_dollar"),
                "fee_drag_pct": sm.get("fee_drag_pct"),
                "rr_tp1": sm.get("rr_tp1"),
                "rr_tp2": sm.get("rr_tp2"),
                "stop_width_pct": sm.get("stop_width_pct"),
                "num_agree": sm.get("num_agree"),
                "regime_4h_aligned": sm.get("regime_4h_aligned"),
                "would_pass_floor": sm.get("would_pass_confidence_floor"),
                "strategies_agree": strategies_agree,
                "bb_involved": "bollinger_squeeze" in (strategies_agree or []),
                "regime_1h": sm.get("regime_1h"),
                "regime_4h": sm.get("regime_4h"),
                "funding_rate": sm.get("funding_rate"),
                "volume_ratio": sm.get("volume_ratio"),
                "time_utc_hour": sm.get("time_utc_hour"),
                "btc_trend": sm.get("btc_trend"),
                "mechanical_floor": sm.get("mechanical_floor"),
            }

        return json.dumps(critic_data, separators=(",", ":"), default=str)

    def _is_high_stakes_trade(
        self,
        trade_out: AgentOutput,
        risk_out: Optional[AgentOutput],
        snapshot_data: dict,
    ) -> bool:
        """Determine if a trade warrants the structured debate protocol.

        High-stakes when ANY of:
          1. Trade Agent confidence > 0.70
          2. Position would use > 10% of equity in risk
          3. First trade after 3+ consecutive losses (extra scrutiny)
        """
        # Must be an active trade proposal
        action = trade_out.data.get("a", trade_out.data.get("action", "skip"))
        if action in ("skip", "flat"):
            return False

        # 1. High conviction
        conf = float(trade_out.data.get("c", trade_out.data.get("confidence", 0)))
        if conf > 0.70:
            logger.info("[HIGH-STAKES] Triggered: confidence=%.0f%% > 70%%", conf * 100)
            return True

        # 2. Large position size (>10% equity at risk)
        if risk_out and risk_out.ok:
            sz_pct = float(risk_out.data.get(
                "position_size_pct", risk_out.data.get("sz_pct", 0)
            ))
            if sz_pct > 10.0:
                logger.info("[HIGH-STAKES] Triggered: position_size=%.1f%% > 10%%", sz_pct)
                return True

        # 3. First trade after 3+ consecutive losses
        try:
            survival = snapshot_data.get("survival", {})
            streak = survival.get("streak", 0)
            if isinstance(streak, (int, float)) and streak <= -3:
                logger.info("[HIGH-STAKES] Triggered: loss_streak=%d (>=3 losses)", abs(int(streak)))
                return True
        except Exception:
            pass
        # Also check self_perf streak field (e.g. "L3" = 3 losses)
        try:
            sp_streak = snapshot_data.get("self_perf", {}).get("streak", "")
            if isinstance(sp_streak, str) and sp_streak.startswith("L"):
                loss_count = int(sp_streak[1:])
                if loss_count >= 3:
                    logger.info("[HIGH-STAKES] Triggered: self_perf streak=%s", sp_streak)
                    return True
        except (ValueError, TypeError):
            pass

        return False

    def _build_critic_round1_input(
        self,
        snapshot: dict,
        regime_out: AgentOutput,
        trade_out: AgentOutput,
        risk_out: Optional[AgentOutput],
    ) -> dict:
        """Build Round 1 Critic input WITHOUT confidence (prevents anchoring).

        Returns a dict with thesis and evidence but no confidence score.
        The Critic must evaluate the thesis on its merits alone.
        """
        td = trade_out.data
        thesis = td.get("thesis", td.get("n", td.get("reasoning", "No thesis provided")))
        side = td.get("side", td.get("s", ""))
        action = td.get("a", td.get("action", ""))

        evidence_parts = []
        if td.get("evidence"):
            evidence_parts.append(str(td["evidence"]))
        if td.get("n"):
            evidence_parts.append(td["n"])
        regime = regime_out.data.get("rg", regime_out.data.get("regime", "unknown")) if regime_out.ok else "unknown"
        regime_bias = regime_out.data.get("bias", "neutral") if regime_out.ok else "neutral"
        evidence_parts.append(f"Regime: {regime}, Bias: {regime_bias}")

        if risk_out and risk_out.ok:
            risk_flags = risk_out.data.get("red_flags", risk_out.data.get("risks", []))
            if risk_flags:
                evidence_parts.append(f"Risk flags: {risk_flags}")

        return {
            "proposal_thesis": f"{action.upper()} {side} — {thesis}",
            "proposal_evidence": "\n".join(evidence_parts) if evidence_parts else "No specific evidence cited",
            "regime": regime,
        }

    def _run_structured_debate(
        self,
        trade_out: AgentOutput,
        regime_out: AgentOutput,
        risk_out: Optional[AgentOutput],
        snapshot_data: dict,
        model_for_trigger: Optional[str],
    ) -> tuple:
        """Run 2-round structured debate: Critic R1 (no confidence) + Trade rebuttal.

        Returns:
            (critic_out, debate_result) where debate_result is a dict or None on failure.
        """
        from llm.agents.prompts import CRITIC_ROUND1_PROMPT, TRADE_REBUTTAL_PROMPT

        # ── Round 1: Critic evaluates thesis WITHOUT confidence ──
        r1_input = self._build_critic_round1_input(
            snapshot_data, regime_out, trade_out, risk_out
        )

        r1_prompt = CRITIC_ROUND1_PROMPT.format(**r1_input)

        # Build a full critic context (for downstream compatibility) but
        # strip confidence fields so the Critic evaluates on merit alone.
        _critic_context = self._build_critic_input(
            snapshot_data, regime_out, trade_out, risk_out
        )
        # Remove confidence from the context JSON to prevent anchoring
        try:
            _ctx = json.loads(_critic_context)
            td_in_ctx = _ctx.get("trade_decision", {})
            td_in_ctx.pop("c", None)
            td_in_ctx.pop("confidence", None)
            _critic_context = json.dumps(_ctx, separators=(",", ":"), default=str)
        except Exception:
            pass

        config = self.configs.get(AgentRole.CRITIC, AgentConfig(role=AgentRole.CRITIC))
        model = config.model_override or model_for_trigger or _get_default_model(AgentRole.CRITIC)

        raw_r1, usage_r1 = call_llm(
            system_prompt=r1_prompt,
            snapshot_json=_critic_context,
            model=model,
            max_tokens=config.max_tokens or 512,
            max_retries=1,
            timeout=20,
        )

        # Cost tracking handled by client.py — do NOT double-count here

        critic_r1_data = None
        if raw_r1:
            critic_r1_data = _parse_agent_json(raw_r1)

        if not critic_r1_data:
            logger.warning("[DEBATE] Critic Round 1 failed — falling back to simple critic")
            return None, None

        critic_out = AgentOutput(
            role=AgentRole.CRITIC,
            data=critic_r1_data,
            input_tokens=usage_r1.get("input_tokens", 0),
            output_tokens=usage_r1.get("output_tokens", 0),
            model_used=model,
        )

        verdict = critic_r1_data.get("verdict", "approve").lower().strip()

        # If Critic approves, no need for Round 2
        if verdict == "approve":
            logger.info("[DEBATE] Critic approved thesis on merit (no anchoring) — skip rebuttal")
            return critic_out, {
                "debate_occurred": True,
                "debate_type": "structured_r1_only",
                "verdict": "approve",
                "winner": "trade",
                "confidence_adjustment": 0.0,
                "final_confidence": float(trade_out.data.get("c", trade_out.data.get("confidence", 0.5))),
            }

        # ── Round 2: Trade Agent rebuts Critic's challenge ──────
        td = trade_out.data
        thesis = td.get("thesis", td.get("n", td.get("reasoning", "")))
        action = td.get("a", td.get("action", ""))

        objections = critic_r1_data.get("objections", [])
        objections_fmt = "\n".join(
            f"  {i}. {obj.get('reason', '?')} (likelihood={obj.get('likelihood', '?')}, impact={obj.get('impact', '?')})"
            for i, obj in enumerate(objections, 1)
        ) if objections else "  (no specific objections)"

        red_flags = critic_r1_data.get("red_flags", [])
        red_flags_fmt = "\n".join(
            f"  - {f}" for f in red_flags[:5]
        ) if red_flags else "  (none)"

        r2_prompt = TRADE_REBUTTAL_PROMPT.format(
            original_thesis=thesis,
            original_action=action.upper(),
            critic_counter_thesis=critic_r1_data.get("counter_thesis") or "No explicit counter-thesis",
            critic_objections_formatted=objections_fmt,
            critic_red_flags=red_flags_fmt,
        )

        trade_config = self.configs.get(AgentRole.TRADE, AgentConfig(role=AgentRole.TRADE))
        trade_model = trade_config.model_override or model_for_trigger or _get_default_model(AgentRole.TRADE)

        raw_r2, usage_r2 = call_llm(
            system_prompt=r2_prompt,
            snapshot_json="{}",
            model=trade_model,
            max_tokens=trade_config.max_tokens or 512,
            max_retries=1,
            timeout=20,
        )

        # Cost tracking handled by client.py — do NOT double-count here

        rebuttal_data = None
        if raw_r2:
            rebuttal_data = _parse_agent_json(raw_r2)

        if not rebuttal_data:
            logger.warning("[DEBATE] Trade rebuttal failed — using Critic R1 verdict only")
            return critic_out, {
                "debate_occurred": True,
                "debate_type": "structured_r1_only",
                "verdict": verdict,
                "winner": "critic",
                "confidence_adjustment": -0.05,
                "final_confidence": max(0.05, float(td.get("c", td.get("confidence", 0.5))) - 0.05),
            }

        # ── Merge debate results ──────────────────────────────
        original_conf = float(td.get("c", td.get("confidence", 0.5)))
        maintains_thesis = rebuttal_data.get("maintains_thesis", True)
        rebuttal_conf = float(rebuttal_data.get("c", rebuttal_data.get("confidence", original_conf)))

        if maintains_thesis:
            # Trade Agent defended successfully -> confidence boost +5%
            final_conf = min(1.0, original_conf + 0.05)
            winner = "trade"
            conf_adj = 0.05
            logger.info(
                "[DEBATE] Trade maintained thesis after challenge: "
                "conf %.0f%% -> %.0f%% (+5%%)",
                original_conf * 100, final_conf * 100,
            )
        else:
            # Trade Agent revised thesis -> use revised confidence
            final_conf = rebuttal_conf
            winner = "critic"
            conf_adj = final_conf - original_conf
            logger.info(
                "[DEBATE] Trade revised thesis after challenge: "
                "conf %.0f%% -> %.0f%% (revised)",
                original_conf * 100, final_conf * 100,
            )

        # Update trade_out with rebuttal results for downstream
        trade_out.data["debate_rebuttal"] = {
            "maintains_thesis": maintains_thesis,
            "concessions": rebuttal_data.get("concessions", []),
            "rebuttal_points": rebuttal_data.get("rebuttal_points", []),
        }
        critic_out.data["debate_round"] = 1
        critic_out.data["debate_type"] = "structured"

        # If rebuttal changed action, propagate
        rebuttal_action = rebuttal_data.get("a", rebuttal_data.get("action"))
        if rebuttal_action and rebuttal_action != action:
            trade_out.data["a"] = rebuttal_action
            trade_out.data["n"] = (
                trade_out.data.get("n", "") +
                f" | debate: {action}->{rebuttal_action}"
            )

        debate_result = {
            "debate_occurred": True,
            "debate_type": "structured_2round",
            "verdict": verdict,
            "winner": winner,
            "confidence_adjustment": round(conf_adj, 3),
            "final_confidence": round(final_conf, 3),
            "original_confidence": round(original_conf, 3),
            "maintains_thesis": maintains_thesis,
            "objections_count": len(objections),
            "concessions": rebuttal_data.get("concessions", []),
            "cost_tokens": (
                usage_r1.get("input_tokens", 0) + usage_r1.get("output_tokens", 0) +
                usage_r2.get("input_tokens", 0) + usage_r2.get("output_tokens", 0)
            ),
        }

        return critic_out, debate_result

    def _build_learning_input(self, trade_data: Dict[str, Any]) -> str:
        """Build learning agent input from closed trade data + context.

        The Learning Agent needs:
        - Full trade outcome data (symbol, side, pnl, regime, timing, exit)
        - Prior knowledge context (what did we know going in?)
        - Self-performance (what's our track record for this type of trade?)
        """
        relevant = {}
        for key in (
            "symbol", "side", "outcome", "pnl", "regime", "strategy",
            "confidence", "hold_time_s", "exit_action", "exit_reason",
            "funding_rate", "entry_price", "exit_price",
            "llm_action", "llm_confidence", "num_strategies_agreed",
            "ensemble_confidence", "chop_score", "entry_type",
            "funding_paid", "leverage", "size_multiplier",
            "thesis", "counter_thesis", "setup_type", "confluence_quality",
        ):
            if key in trade_data:
                relevant[key] = trade_data[key]
        # Add any recent lessons for context (what have we learned before?)
        try:
            from llm.post_trade_learner import get_recent_lessons
            lessons = get_recent_lessons(3)
            if lessons:
                relevant["prior_lessons"] = " | ".join(lessons)
        except Exception:
            pass
        # Inject exit agent reasoning if it was involved in closing this trade
        if self.last_exit_output and self.last_exit_output.ok:
            exit_data = self.last_exit_output.data
            exit_reason = exit_data.get("reason", "")
            exit_action = exit_data.get("action", "")
            if exit_reason and exit_action in ("full_close", "partial_close", "close"):
                relevant["exit_agent_reasoning"] = exit_reason[:200]
                relevant["exit_thesis_valid"] = exit_data.get("thesis_still_valid", True)

        # Add deep memory context for this symbol/regime
        try:
            from llm.deep_memory import get_deep_memory
            dm = get_deep_memory()
            sym = trade_data.get("symbol", "")
            regime = trade_data.get("regime", "")
            summary = dm.build_llm_knowledge_summary(
                symbol=sym, regime=regime, max_tokens=400
            )
            if summary:
                relevant["prior_knowledge"] = summary[:400]
        except Exception:
            pass
        return json.dumps(relevant, separators=(",", ":"), default=str)

    # ── Critic shadow auto-restore hook (DECHOKE 6) ─────────────

    def _maybe_recommend_critic_restore(self) -> None:
        """Score the Critic's shadow would-vetoes in dollars; journal a
        restore recommendation once they prove out.

        THE_STANDARD §2b restore criterion: n>=13 resolved shadow vetoes AND
        net dollar-positive (the setups it would have vetoed lost money, i.e.
        sum(hypothetical_pnl_pct) < 0 → the vetoes would have saved money).
        Recommendation only — actual re-enforcement (CRITIC_ENFORCE=true)
        stays an owner/meta-audit decision.
        OWNER NOTE (2026-07-02): if shadow shows nothing by the next weekly
        meta-audit, the standing proposal is to DELETE the Critic call
        entirely to save Sonnet tokens.
        Throttled to one scan per 6h (file scan of resolved counterfactuals).
        """
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from pathlib import Path as _Path
        now = time.time()
        if now - getattr(self, "_critic_restore_last_check", 0.0) < 6 * 3600:
            return
        self._critic_restore_last_check = now
        resolved_path = _Path("data/llm/counterfactual_resolved.jsonl")
        if not resolved_path.exists():
            return
        n = 0
        net_pnl_pct = 0.0
        try:
            with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "CRITIC_SHADOW_VETO" not in line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except Exception:
                        continue
                    if not str(rec.get("skip_reason", "")).startswith("CRITIC_SHADOW_VETO"):
                        continue
                    pnl = rec.get("hypothetical_pnl_pct")
                    if pnl is None:
                        continue
                    n += 1
                    net_pnl_pct += float(pnl)
        except Exception as e:
            logger.debug(f"[SHADOW-CRITIC-RESTORE] scan error: {e}")
            return
        state = {
            "checked_at": _dt.now(_tz.utc).isoformat(),
            "n_resolved": n,
            "net_hypothetical_pnl_pct": round(net_pnl_pct, 3),
            "dollar_positive": bool(n >= 13 and net_pnl_pct < 0),
        }
        if n >= 13 and net_pnl_pct < 0:
            state["recommendation"] = (
                "RESTORE CANDIDATE: Critic shadow would-vetoes are net "
                "dollar-positive at n>=13 — journal for owner/meta-audit; "
                "set CRITIC_ENFORCE=true only after adversarial re-check."
            )
            logger.warning(
                f"[SHADOW-CRITIC-RESTORE] restore recommendation: n={n} "
                f"net_would_veto_pnl={net_pnl_pct:+.2f}% (vetoed setups lost "
                f"→ vetoes would have saved). Journaled to "
                f"data/llm/critic_shadow_state.json for the weekly meta-audit."
            )
        else:
            logger.info(
                f"[SHADOW-CRITIC-RESTORE] no restore case yet: n={n} "
                f"net_would_veto_pnl={net_pnl_pct:+.2f}% (need n>=13 and net<0)"
            )
        try:
            with open("data/llm/critic_shadow_state.json", "w", encoding="utf-8") as f:
                _json.dump(state, f, indent=2)
        except Exception:
            pass

    # ── Output merger ───────────────────────────────────────────

    def _merge_outputs(
        self,
        regime_out: AgentOutput,
        trade_out: AgentOutput,
        risk_out: Optional[AgentOutput],
        critic_out: Optional[AgentOutput],
        snapshot_data: Optional[dict] = None,
        consistency_score: float = 1.0,
        consensus_confidence: Optional[float] = None,
    ) -> LLMDecision:
        """Merge all agent outputs into a single LLMDecision.

        Priority chain:
          1. Critic can override action + adjust confidence
          2. Risk agent provides sizing + strategy weights
          3. Trade agent provides base action + confidence + reasoning
          4. Regime agent provides regime classification
        """
        # Start with Trade Agent's core decision
        td = trade_out.data
        action = _normalize_action(td.get("a", td.get("action", "flat")))
        confidence = float(td.get("c", td.get("confidence", 0.0)))
        notes = td.get("n", td.get("notes", ""))
        memory_update = td.get("mu", td.get("memory_update"))
        entry_adj = td.get("ea", td.get("entry_adjustment"))

        # ── Confidence Calibration ──
        # Apply calibration curve to deflate/inflate confidence based on historical accuracy.
        try:
            from llm.brain_wiring import calibrate_confidence
            raw_conf = confidence
            # Calibrator works on 0-100 scale
            conf_pct = confidence * 100 if confidence <= 1.0 else confidence
            calibrated_pct = calibrate_confidence(conf_pct, agent="trade_agent")
            calibrated = calibrated_pct / 100.0 if confidence <= 1.0 else calibrated_pct
            if abs(calibrated - confidence) > 0.01:
                notes += f" | CAL: {raw_conf:.2f}→{calibrated:.2f}"
                confidence = calibrated
        except Exception:
            pass

        # Regime from Regime Agent
        rd = regime_out.data
        regime = rd.get("rg", rd.get("regime", "unknown"))
        regime_conf = float(rd.get("conf", rd.get("confidence", 0.5)))
        regime_bias = rd.get("bias", "neutral")
        regime_transition = rd.get("transition", "stable")

        # Enrich notes with regime info
        regime_note = f"regime={regime}({regime_conf:.0%})"
        if regime_bias != "neutral":
            regime_note += f" bias={regime_bias}"
        if regime_transition != "stable":
            regime_note += f" {regime_transition}"
        regime_outlook = rd.get("outlook", "")

        # Trade thesis
        trade_thesis = td.get("thesis", "")

        # Risk Agent: sizing + strategy weights
        size_mult = 1.0
        strategy_weights = StrategyWeights()
        risk_flags = []

        if risk_out and risk_out.ok:
            rk = risk_out.data
            size_mult = float(rk.get("sz", rk.get("size_multiplier", 1.0)))
            size_mult = max(0.0, min(2.0, size_mult))

            # Parse strategy weights
            sw_raw = rk.get("sw", rk.get("strategy_weights", {}))
            if isinstance(sw_raw, dict):
                strategy_weights = _parse_strategy_weights(sw_raw)

            risk_flags = rk.get("risks", [])
            override = rk.get("override")

            # R17 (FULL_PIPE map, 2026-07-02): the risk_vacc<0.45 skip→reduce
            # auto-downgrade was DELETED — a mechanical override of an LLM
            # verdict using contaminated calibration stats (D3/M19 class). The
            # Risk Agent now sees its own calibration in its prompt (calibration
            # prefix in _call_agent, RISK added to the injected roles) and
            # self-adjusts; Overseer/consistency can flag. Its verdict stands.

            if override == "skip":
                action = "flat"
                risk_reason = rk.get("reason", rk.get("n", "unspecified"))
                notes += f" | RISK: override to skip ({risk_reason})"
            elif override == "reduce":
                old_mult = size_mult
                size_mult = min(size_mult, 0.7)
                risk_reason = rk.get("reason", rk.get("n", ""))
                notes += f" | RISK: sizing {old_mult:.1f}x→{size_mult:.1f}x ({risk_reason})"

        # Kelly Criterion modulation from Quant Agent — ADVISORY+SHADOW
        # DECHOKE 5 (2026-07-02, FALLACY_AUDIT D8 — owner-approved): the Quant
        # Agent's ungated Kelly path scaled real position size 0.5-1.5x with
        # zero accuracy gating, from an agent with no surviving measured
        # signal (GM_AGENT_SKILL_24K). Shadow-logged, not applied.
        # QUANT_AGENT_ENFORCE=true restores (requires n>=13 dollar-positive).
        scratchpad = get_pipeline_scratchpad()
        kelly_frac = scratchpad.read_by_key("kelly_fraction")
        if kelly_frac is not None and isinstance(kelly_frac, (int, float)) and kelly_frac > 0:
            kelly_baseline = 0.15  # normalized baseline
            kelly_mult = max(0.5, min(1.5, kelly_frac / kelly_baseline))
            _quant_enforce_kelly = os.getenv(
                "QUANT_AGENT_ENFORCE", "false").lower() == "true"
            if _quant_enforce_kelly:
                old_size = size_mult
                size_mult = max(0.0, min(2.0, size_mult * kelly_mult))
                if abs(kelly_mult - 1.0) > 0.05:
                    notes += f" | KELLY: f={kelly_frac:.3f} mult={kelly_mult:.2f} sz={old_size:.2f}→{size_mult:.2f}"
            elif abs(kelly_mult - 1.0) > 0.05:
                logger.info(
                    f"[SHADOW-QUANT] kelly would_resize sz {size_mult:.2f}->"
                    f"{max(0.0, min(2.0, size_mult * kelly_mult)):.2f} "
                    f"(f={kelly_frac:.3f} mult={kelly_mult:.2f}; advisory only)"
                )

        # ── Graduated Risk: apply drawdown-proportional size reduction ──
        try:
            from llm.brain_wiring import get_graduated_risk
            grm = get_graduated_risk()
            if grm is not None:
                old_size = size_mult
                size_mult = grm.apply_to_risk_multiplier(size_mult)
                if abs(size_mult - old_size) > 0.01:
                    status = grm.get_status()
                    notes += (f" | DD: {status['drawdown_pct']:.1f}% "
                              f"band={status['band']} sz={old_size:.2f}→{size_mult:.2f}")
        except Exception:
            pass

        # ── R13 degradation cap (FULL_PIPE map, 2026-07-02) ──
        # Critic call failed after retry: the pipeline ran with reduced review
        # capacity. Cap size at 0.5x as a PHYSICS-style degradation cap (labeled
        # as such, not as judgment) and stamp degraded=true into notes so it
        # rides into decisions.jsonl + the thesis record.
        try:
            if scratchpad.read_by_key("call_degraded"):
                if size_mult > 0.5:
                    notes += (f" | DEGRADED: critic unavailable after retry — "
                              f"sz {size_mult:.2f}→0.50 (degradation cap, not judgment)")
                    size_mult = 0.5
                else:
                    notes += " | DEGRADED: critic unavailable after retry (degraded=true)"
        except Exception:
            pass

        # Critic Agent — SHADOW MODE (DECHOKE 6, 2026-07-02, owner-approved).
        # GM_AGENT_SKILL_24K: the Critic's apparent veto value is a week-1
        # crash artifact — in the mid era its ordering was INVERTED (vetoed
        # −79bps vs approved −196bps @24h, n=63/41): it vetoed the less-bad
        # and approved the worse. It keeps evaluating and its would-vetoes
        # are logged ([SHADOW-CRITIC]) + counterfactual-recorded for dollar
        # scoring, but it no longer blocks or mutates the decision.
        # AUTO-RESTORE HOOK: _maybe_recommend_critic_restore() journals a
        # restore recommendation once shadow scoring reaches n>=13 resolved
        # would-vetoes that are net dollar-positive (the vetoed setups lost).
        # OWNER NOTE (2026-07-02): owner considers the Critic near-worthless
        # ("so misguided and strained") — if shadow shows nothing by the next
        # weekly meta-audit, propose DELETING the Critic call entirely to
        # save Sonnet tokens. Kill-switch: CRITIC_ENFORCE=true restores the
        # old enforcement path below.
        counter_thesis = ""
        _critic_enforce = os.getenv("CRITIC_ENFORCE", "false").lower() == "true"

        if critic_out and critic_out.ok:
            cd = critic_out.data
            verdict = cd.get("verdict", "approve").lower().strip()
            counter_thesis = cd.get("counter_thesis", "")

            if verdict != "approve" and not _critic_enforce:
                _sc_reason = str(cd.get("reason", ""))[:120]
                logger.info(
                    f"[SHADOW-CRITIC] would_veto verdict={verdict} "
                    f"reason={_sc_reason} counter={str(counter_thesis)[:80]} "
                    f"(shadow only; CRITIC_ENFORCE=true re-enables)"
                )
                notes += f" | CRITIC-SHADOW: would_{verdict} ({_sc_reason[:60]})"
                if counter_thesis:
                    notes += f" | COUNTER(shadow): {str(counter_thesis)[:80]}"
                try:
                    self._maybe_recommend_critic_restore()
                except Exception:
                    pass
            # R18 (FULL_PIPE map, 2026-07-02): the vacc<0.45 "challenge blocked"
            # auto-approve channel was DELETED. It mechanically suppressed the
            # Critic's veto using a calibration stat computed from poisoned data
            # (M19) — silently turning the adversarial agent into a rubber stamp.
            # The Critic's accuracy now reaches it as prompt context (calibration
            # prefix in _call_agent) and is graded against price; its verdict
            # stands within the pipeline. veto_is_structured below is kept as a
            # schema check (a veto must carry a counter-thesis) — protocol, not
            # opinion.
            elif verdict != "approve":
                # NEW: Check for structured counter-thesis before allowing veto as action block
                # Vetoes without structure are downgraded to confidence reduction only
                has_counter_price = counter_thesis and len(str(counter_thesis).strip()) > 0
                has_counter_timeframe = cd.get("counter_thesis_timeframe") and len(str(cd.get("counter_thesis_timeframe")).strip()) > 0
                has_counter_falsifiable = cd.get("counter_thesis_falsifiable") and len(str(cd.get("counter_thesis_falsifiable")).strip()) > 0
                veto_is_structured = has_counter_price and has_counter_timeframe and has_counter_falsifiable

                adj_action = cd.get("adjusted_action") if veto_is_structured else None
                adj_conf = cd.get("adjusted_confidence")
                reason = cd.get("reason", "")

                if adj_action:
                    old_action = action
                    action = _normalize_action(adj_action)
                    notes += f" | CRITIC: {old_action}→{action} ({reason[:60]})"
                elif verdict != "approve" and not veto_is_structured:
                    # Veto lacked structure — downgrade to confidence reduction only
                    notes += f" | CRITIC: veto lacked structure (need price/timeframe/falsifiable) — confidence reduction only"
                    logger.info(
                        f"[COORDINATOR] Critic veto downgraded to confidence-only: "
                        f"has_price={has_counter_price}, has_timeframe={has_counter_timeframe}, has_falsifiable={has_counter_falsifiable}"
                    )

                if adj_conf is not None:
                    old_conf = confidence
                    confidence = float(adj_conf)
                    confidence = max(0.0, min(1.0, confidence))
                    notes += f" | CRITIC: conf {old_conf:.2f}→{confidence:.2f}"

                if counter_thesis:
                    notes += f" | COUNTER: {counter_thesis[:80]}"
                if has_counter_timeframe:
                    notes += f" | TF: {cd.get('counter_thesis_timeframe', '')[:30]}"
                if has_counter_falsifiable:
                    notes += f" | FALSIF: {cd.get('counter_thesis_falsifiable', '')[:50]}"

            cal_note = cd.get("calibration_note")
            if cal_note:
                # Store as memory update if we don't already have one
                if not memory_update:
                    memory_update = cal_note[:100]

        # Gap 7: Scale confidence by consistency score (soft circuit breaker)
        if consistency_score < 0.7 and action != "flat":
            old_conf = confidence
            # Scale: consistency 0.5 → reduce 15%, consistency 0.3 → reduce 35%
            scale = 0.5 + consistency_score * 0.5  # maps [0, 1] → [0.5, 1.0]
            confidence = round(confidence * scale, 3)
            confidence = max(0.0, min(1.0, confidence))
            notes += (f" | CONSISTENCY_ADJ: {old_conf:.2f}→{confidence:.2f} "
                      f"(agents disagreeing, score={consistency_score:.2f})")

        # Gap 1: Apply consensus confidence if it significantly differs
        if consensus_confidence is not None and action != "flat":
            # If consensus is much lower than trade agent's confidence, reduce
            if consensus_confidence < confidence - 0.1:
                old_conf = confidence
                # Blend: 60% trade agent, 40% consensus
                confidence = round(confidence * 0.6 + consensus_confidence * 0.4, 3)
                confidence = max(0.0, min(1.0, confidence))
                notes += (f" | CONSENSUS_ADJ: {old_conf:.2f}→{confidence:.2f} "
                          f"(agents not fully aligned)")

        # Add risk flags to notes
        if risk_flags:
            notes += f" | RISKS: {', '.join(str(f) for f in risk_flags[:3])}"

        # Confluence quality for notes
        confluence_info = _compute_confluence_from_snapshot(
            snapshot_data or {}, regime,
        )
        confl_note = ""
        setup_type = ""
        if confluence_info:
            setup_type = confluence_info.get("setup_type", "")
            confl_note = (
                f"CONFLUENCE: {confluence_info['count']}strat "
                f"q={confluence_info['quality']:.0%} "
                f"type={confluence_info['best_pair']} "
                f"setup={setup_type}"
            )

        # Build the final notes: regime + thesis + confluence + trade + risk + critic
        thesis_parts = []
        if regime_outlook:
            thesis_parts.append(f"OUTLOOK: {regime_outlook[:80]}")
        if trade_thesis:
            thesis_parts.append(f"THESIS: {trade_thesis[:80]}")
        thesis_block = " | ".join(thesis_parts)
        combined_notes = f"[MA] {regime_note}"
        if thesis_block:
            combined_notes += f" | {thesis_block}"
        if confl_note:
            combined_notes += f" | {confl_note}"
        combined_notes = f"{combined_notes} | {notes}"[:1500]

        # Default memory note from thesis if Trade Agent didn't provide mu
        if not memory_update and trade_thesis and action in ("go", "proceed"):
            memory_update = trade_thesis[:100]

        # ── Graduated Rules: apply empirically-validated executable rules ──
        # These rules graduated from hypothesis_tracker via 10+ evidence events.
        # They can VETO, BOOST, or PENALIZE confidence based on regime/symbol/side.
        if action not in ("flat", "skip") and snapshot_data:
            try:
                from llm.graduated_rules import get_graduated_rules_engine
                _sym = ""
                _side = ""
                _strat = ""
                _n_agree = 0
                _m_entry = 0.0
                _m_sl = 0.0
                _m_tp1 = 0.0
                _m_tp2 = 0.0
                # Extract from snapshot_data (abbreviated keys; mirrors sim block ~990-994)
                for _mk in snapshot_data.get("m", []):
                    for _sg in _mk.get("sg", _mk.get("sigs", [])):
                        if not _sym:
                            _sym = str(_sg.get("sym", _sg.get("s", "")))
                        if not _side:
                            _side = str(_sg.get("side", _sg.get("sd", "")))
                        if not _strat:
                            _strat = str(_sg.get("strategy", ""))
                        if not _n_agree:
                            _n_agree = int(snapshot_data.get("g", {}).get("n_agree", 0))
                        if _m_entry <= 0:
                            _m_entry = float(_sg.get("entry", _sg.get("e", 0)) or 0)
                        if _m_sl <= 0:
                            _m_sl = float(_sg.get("sl", 0) or 0)
                        if _m_tp1 <= 0:
                            _m_tp1 = float(_sg.get("tp1", 0) or 0)
                        if _m_tp2 <= 0:
                            _m_tp2 = float(_sg.get("tp2", 0) or 0)
                _vetoed, _adj_conf, _grad_notes, _m_veto_ids = get_graduated_rules_engine().evaluate_signal(
                    symbol=_sym, regime=regime, side=_side,
                    strategy=_strat, num_agree=_n_agree,
                    confidence=confidence * 100 if confidence <= 1.0 else confidence,
                )
                if _vetoed:
                    action = "flat"
                    notes += f" | GRAD_VETO: {_grad_notes[:80]}"
                    logger.info(f"[GRAD-RULES] Signal vetoed for {_sym}/{_side}: {_grad_notes[:60]}")
                    # Self-measuring veto denominator for the merge-veto path.
                    try:
                        from llm.brain_wiring import record_veto_counterfactual
                        # Synthesize tp2 if compacted snapshot omitted it.
                        if _m_tp2 <= 0 and _m_entry > 0 and _m_tp1 > 0:
                            _m_tp2 = _m_entry + 2.0 * (_m_tp1 - _m_entry)
                        _m_denom_only = not (_m_entry > 0 and _m_sl > 0 and _m_tp1 > 0)
                        record_veto_counterfactual(
                            symbol=_sym, side=_side,
                            entry_price=_m_entry, sl=_m_sl,
                            tp1=_m_tp1, tp2=_m_tp2,
                            confidence=(confidence * 100 if confidence <= 1.0 else confidence),
                            veto_rule_ids=_m_veto_ids,
                            strategy=_strat, regime=str(regime or ""),
                            denominator_only=_m_denom_only,
                        )
                    except Exception:
                        pass
                elif _grad_notes:
                    # Scale adjusted confidence back to [0,1] if needed
                    _new_conf = _adj_conf / 100.0 if _adj_conf > 1.0 else _adj_conf
                    if abs(_new_conf - confidence) > 0.01:
                        notes += f" | GRAD: {_grad_notes[:60]} conf {confidence:.2f}→{_new_conf:.2f}"
                        confidence = _new_conf
            except Exception as _ge:
                logger.debug(f"[GRAD-RULES] Eval error: {_ge}")

        return LLMDecision(
            action=action,
            confidence=confidence,
            regime=regime,
            strategy_weights=strategy_weights,
            memory_update=memory_update[:200] if memory_update else None,
            notes=combined_notes,
            size_multiplier=size_mult,
            entry_adjustment=entry_adj,
        )


# ── Module-level helpers ────────────────────────────────────────

def _compute_confidence_consensus(
    trade_out: AgentOutput,
    regime_out: AgentOutput,
    risk_out: Optional[AgentOutput],
    critic_out: Optional[AgentOutput],
    consistency_score: float,
) -> Optional[float]:
    """Bayesian confidence consensus: sequential probability updating.

    Instead of heuristic multiplicative factors, this computes a posterior
    probability using Bayes' rule. Each agent contributes a likelihood ratio
    (how much their analysis shifts the probability), and the prior is
    updated sequentially:

        posterior = prior × LR_regime × LR_risk × LR_critic × LR_consistency

    Likelihood ratios > 1.0 increase confidence, < 1.0 decrease it.
    The posterior is then converted back to a probability via normalization.

    Returns consensus confidence [0, 1] or None for skip decisions.
    """
    trade_conf = float(trade_out.data.get("c", trade_out.data.get("confidence", 0.0)))
    if trade_conf == 0.0:
        return None  # Skip decisions don't need consensus

    # Prior: Trade Agent's confidence as the starting probability
    prior_odds = trade_conf / max(1.0 - trade_conf, 0.01)  # convert to odds

    # Regime Agent likelihood ratio:
    # High regime confidence in a clear regime → supports the trade
    # Low regime confidence or "unknown" → uncertainty weakens it
    regime_conf = float(regime_out.data.get("conf", regime_out.data.get("confidence", 0.5)))
    regime = regime_out.data.get("rg", regime_out.data.get("regime", "unknown"))
    regime_bias = regime_out.data.get("bias", "neutral")

    # Regime clarity: clear regimes are informative, unknown is not
    if regime == "unknown":
        lr_regime = 0.85  # slight negative: uncertainty
    elif regime_conf >= 0.7:
        # High-confidence regime: does the trade agree with regime bias?
        # If bias aligns with trade direction, boost; if not, reduce
        trade_action = trade_out.data.get("a", trade_out.data.get("action", ""))
        if regime_bias in ("bullish", "risk_on") and trade_action in ("go", "proceed"):
            lr_regime = 1.0 + (regime_conf - 0.5) * 0.4  # 0.7→1.08, 0.9→1.16
        elif regime_bias in ("bearish", "risk_off") and trade_action in ("go", "proceed"):
            lr_regime = 1.0 - (regime_conf - 0.5) * 0.3  # mild headwind
        else:
            lr_regime = 0.9 + regime_conf * 0.1
    else:
        lr_regime = 0.9 + regime_conf * 0.1  # low conf → ~neutral

    # Risk Agent likelihood ratio:
    # Override=skip is strong negative evidence, reduce is moderate, normal sizing is neutral
    lr_risk = 1.0
    if risk_out and risk_out.ok:
        override = risk_out.data.get("override")
        if override == "skip":
            lr_risk = 0.2  # Risk Agent sees major problems
        elif override == "reduce":
            lr_risk = 0.6  # Moderate concern
        else:
            size_mult = float(risk_out.data.get("sz", risk_out.data.get("size_multiplier", 1.0)))
            # Large size = bullish, small = bearish on this trade
            lr_risk = 0.7 + min(size_mult, 2.0) * 0.3  # 0.5→0.85, 1.0→1.0, 1.5→1.15

    # Critic Agent likelihood ratio:
    # Approval is confirmatory evidence, veto is disconfirming
    lr_critic = 1.0
    if critic_out and critic_out.ok:
        verdict = critic_out.data.get("verdict", "approve").lower().strip()
        if verdict == "approve":
            lr_critic = 1.1  # Confirmed by independent review
        else:
            adj_conf = critic_out.data.get("adjusted_confidence")
            if adj_conf is not None:
                # Critic's adjusted confidence implies their belief about the true probability
                # LR = P(critic_says_challenge | trade_is_good) / P(critic_says_challenge | trade_is_bad)
                # Approximate: if critic says conf=0.3, it's strong disconfirmation
                lr_critic = max(0.3, 0.5 + float(adj_conf))
            else:
                lr_critic = 0.5  # Challenge without specifics = moderate disconfirmation

    # Consistency score as likelihood ratio:
    # Agents agreeing is evidence the analysis is sound
    lr_consistency = 0.6 + consistency_score * 0.5  # [0,1] → [0.6, 1.1]

    # Bayesian update: multiply all likelihood ratios with prior odds
    posterior_odds = prior_odds * lr_regime * lr_risk * lr_critic * lr_consistency

    # Convert back to probability
    posterior = posterior_odds / (1.0 + posterior_odds)

    # Clamp to [0.05, 0.95] — never be absolutely certain
    posterior = max(0.05, min(0.95, posterior))

    return round(posterior, 3)


_ACTION_MAP = {
    "go": "proceed", "proceed": "proceed", "long": "proceed", "short": "proceed",
    "buy": "proceed", "sell": "proceed", "enter": "proceed", "trade": "proceed",
    "skip": "flat", "flat": "flat", "hold": "flat", "pass": "flat",
    "wait": "flat", "no": "flat", "none": "flat",
    "flip": "flip", "reverse": "flip",
}


def _normalize_action(raw: str) -> str:
    """Normalize agent action output to canonical form."""
    return _ACTION_MAP.get(str(raw).lower().strip(), "flat")


def _parse_agent_json(raw_text: str) -> Optional[dict]:
    """Parse JSON from agent response, handling markdown fences and truncation."""
    import re
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        # Try extracting JSON from mixed text
        match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        # Try repairing truncated JSON by closing open braces/brackets
        repaired = _try_repair_truncated_json(text)
        if repaired:
            return repaired
    return None


def _try_repair_truncated_json(text: str) -> Optional[dict]:
    """Attempt to repair truncated JSON by closing open braces/brackets."""
    # Find the start of JSON
    start = text.find("{")
    if start < 0:
        return None
    fragment = text[start:]
    # Remove any trailing partial string (e.g., `"key": "some text` without closing quote)
    # by finding the last complete key-value pair
    # Strategy: progressively close open brackets/braces
    open_braces = fragment.count("{") - fragment.count("}")
    open_brackets = fragment.count("[") - fragment.count("]")
    if open_braces <= 0 and open_brackets <= 0:
        return None  # Not a truncation issue
    # Strip trailing partial values (e.g., incomplete strings)
    # Find last comma or colon, trim after that, then close
    last_complete = max(fragment.rfind(","), fragment.rfind(":"), fragment.rfind("}"), fragment.rfind("]"))
    if last_complete > 0:
        # If last char before our closing is a colon, drop the incomplete key-value pair
        trimmed = fragment[:last_complete]
        if trimmed.rstrip().endswith(":") or trimmed.rstrip().endswith(","):
            trimmed = trimmed.rstrip().rstrip(":,")
        suffix = "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        try:
            parsed = json.loads(trimmed + suffix)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


_WEIGHT_KEY_MAP = {
    "rt": "regime_trend", "mc": "monte_carlo_zones",
    "cs": "confidence_scorer", "mq": "multi_tier_quality",
    "fr": "funding_rate", "oi": "open_interest",
    "vm": "volume_momentum", "ca": "cross_asset",
}


def _parse_strategy_weights(raw: dict) -> StrategyWeights:
    """Parse compact or full strategy weight keys."""
    expanded = {}
    for k, v in raw.items():
        full_key = _WEIGHT_KEY_MAP.get(k, k)
        try:
            expanded[full_key] = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            pass
    return StrategyWeights.from_dict(expanded)


def _ensure_field(
    target: dict, key: str, source: dict, max_len: Optional[int] = None
):
    """Copy a field from source to target if present, with optional truncation."""
    if key in source:
        val = source[key]
        if max_len and isinstance(val, str) and len(val) > max_len:
            val = val[:max_len]
        target[key] = val


def _extract_section(text: str, keyword: str, max_len: int = 300) -> Optional[str]:
    """Extract a section from deep memory text by keyword.

    Looks for lines containing the keyword and returns surrounding context.
    Stops at the next blank line after the keyword section to prevent bleeding.
    """
    lines = text.split("\n")
    result_lines = []
    capturing = False
    chars = 0
    for line in lines:
        if not capturing and keyword.upper() in line.upper():
            capturing = True
            result_lines.append(line)
            chars += len(line)
        elif capturing:
            if not line.strip():
                break  # End of section (blank line)
            result_lines.append(line)
            chars += len(line)
            if chars >= max_len:
                break
    return "\n".join(result_lines) if result_lines else None


def _compute_confluence_from_snapshot(
    snapshot: dict, regime: str
) -> Optional[Dict[str, Any]]:
    """Extract agreeing strategies from the snapshot and compute confluence quality."""
    try:
        markets = snapshot.get("m", [])
        if not markets:
            return None

        # Find the primary market (first one with signals)
        for market in markets:
            sigs = market.get("sg", [])
            if not sigs:
                continue

            # Find the consensus side
            side_counts: Dict[str, list] = {}
            for sig in sigs:
                sd = sig.get("sd", "neutral")
                if sd in ("neutral",):
                    continue
                side_counts.setdefault(sd, []).append(sig.get("st", ""))

            if not side_counts:
                continue

            # Get the majority side
            majority_side = max(side_counts, key=lambda s: len(side_counts[s]))
            agreeing = side_counts[majority_side]

            if agreeing:
                return score_confluence(agreeing, regime)

    except Exception:
        pass
    return None


def _get_default_model(role: AgentRole) -> str:
    """Default model per agent role when no tier routing is available."""
    # Regime + Risk + Learning use cheaper Haiku; Trade + Critic use Sonnet
    try:
        from llm.usage_tiers import MODEL_HAIKU, MODEL_SONNET
    except ImportError:
        return "claude-sonnet-4-6"

    if role in (AgentRole.REGIME, AgentRole.RISK, AgentRole.LEARNING, AgentRole.EXIT, AgentRole.SCOUT, AgentRole.QUANT):
        return MODEL_HAIKU
    return MODEL_SONNET


# ── Singleton ───────────────────────────────────────────────────

_coordinator: Optional[AgentCoordinator] = None


def _build_configs_from_env() -> Dict[AgentRole, AgentConfig]:
    """Build agent configs from environment variables."""
    configs = dict(DEFAULT_AGENT_CONFIGS)

    # Per-agent model overrides
    env_model_map = {
        AgentRole.REGIME: "AGENT_REGIME_MODEL",
        AgentRole.TRADE: "AGENT_TRADE_MODEL",
        AgentRole.RISK: "AGENT_RISK_MODEL",
        AgentRole.LEARNING: "AGENT_LEARNING_MODEL",
        AgentRole.CRITIC: "AGENT_CRITIC_MODEL",
        AgentRole.EXIT: "AGENT_EXIT_MODEL",
        AgentRole.SCOUT: "AGENT_SCOUT_MODEL",
        AgentRole.OVERSEER: "AGENT_OVERSEER_MODEL",
        AgentRole.QUANT: "AGENT_QUANT_MODEL",
    }
    for role, env_key in env_model_map.items():
        model = os.getenv(env_key, "").strip()
        if model and role in configs:
            configs[role].model_override = model
            logger.info(f"[MULTI-AGENT] {role.value} model override: {model}")

    # Per-agent enable/disable
    env_enabled_map = {
        AgentRole.RISK: "AGENT_RISK_ENABLED",
        AgentRole.LEARNING: "AGENT_LEARNING_ENABLED",
        AgentRole.CRITIC: "AGENT_CRITIC_ENABLED",
        AgentRole.EXIT: "AGENT_EXIT_ENABLED",
        AgentRole.SCOUT: "AGENT_SCOUT_ENABLED",
        AgentRole.OVERSEER: "AGENT_OVERSEER_ENABLED",
        AgentRole.QUANT: "AGENT_QUANT_ENABLED",
    }
    for role, env_key in env_enabled_map.items():
        val = os.getenv(env_key, "true").lower()
        if val in ("0", "false", "no") and role in configs:
            configs[role].enabled = False
            logger.info(f"[MULTI-AGENT] {role.value} agent disabled via {env_key}")

    return configs


def get_coordinator() -> AgentCoordinator:
    """Get or create the singleton AgentCoordinator."""
    global _coordinator
    if _coordinator is None:
        configs = _build_configs_from_env()
        _coordinator = AgentCoordinator(agent_configs=configs)
        logger.info("[MULTI-AGENT] Coordinator initialized")
    return _coordinator
