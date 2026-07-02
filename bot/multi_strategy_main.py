"""
Main entry point for the multi-strategy auto-trading bot.
Wires together all components: data fetcher, strategies, ensemble,
position management, leverage, risk, ML, and alerts.

Usage:
    python multi_strategy_main.py           # Paper trading (default)
    ENVIRONMENT=production python multi_strategy_main.py  # Live trading
"""

import asyncio
import collections
import logging
import os
import signal
import sys
import time
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from data.fetcher import DataFetcher
from data.db import (
    init_db, log_signal, log_trade, log_equity, get_daily_summary,
    update_signal_traded, log_signal_outcome, log_health_event,
    update_daily_performance, get_signal_performance, get_recent_trades,
)
from data.strategy_weights import StrategyWeightManager
from feedback.regime_feedback import RegimeFeedbackManager
from feedback.adaptive_confidence import AdaptiveConfidenceFloor
from feedback.hold_time_rules import HoldTimeRuleManager
from data.risk_log import log_rejection, get_rejection_counts
from data.ml_log import log_ml_stats, log_ml_confidence
from data.trade_log import log_closed_trade
from data.learning import record_trade_outcome, get_performance
from trading_config import TradingConfig, DEFAULT_SYMBOLS, apply_profile, get_symbol_param
from strategies.regime_trend import RegimeTrendStrategy
from strategies.monte_carlo_zones import MonteCarloZonesStrategy
from strategies.confidence_scorer import ConfidenceScorerStrategy
from strategies.multi_tier_quality import MultiTierQualityStrategy
from strategies.funding_rate import FundingRateStrategy
from strategies.oi_delta import OIDeltaStrategy
from strategies.bollinger_squeeze import BollingerSqueezeStrategy
from strategies.vmc_cipher import VMCCipherStrategy
from strategies.lead_lag import LeadLagStrategy
from strategies.liquidation_cascade import LiquidationCascadeStrategy
from strategies.probability_engine import ProbabilityEngineStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.ensemble import EnsembleStrategy
from execution.position_manager import PositionManager
from execution.leverage import LeverageManager
from execution.risk import RiskManager, CircuitBreaker
from execution.trade_logger import TradeLogger
from ml.learner import SignalLearner, TradeOutcome, MarketSnapshot
from alerts.router import AlertRouter
from alerts.telegram_bot import TelegramCommandBot
from execution.trade_profile import classify_trade, apply_profile_to_signal
from execution.dynamic_tp import optimize_tp_sl as dynamic_tp_optimize
from execution.precision import validate_fill_price, get_min_qty, get_max_leverage, get_all_symbol_specs, round_qty

# LLM meta-brain
from llm.autonomy import LLMMode, get_llm_mode, should_call_llm, llm_has_veto, describe_mode
from llm.decision_engine import get_trading_decision, DecisionResult
from llm.decision_types import (
    StrategySignal as LLMStrategySignal,
    MarketSnapshot as LLMMarketSnapshot,
    GlobalContext as LLMGlobalContext,
)
from llm.risk_gating import RiskContext as LLMRiskContext
from llm.triggers import LLMTrigger, TriggerAccumulator, TRIGGER_LABELS
from execution.candidate import TradeCandidate, CandidateLogger
from execution.reconciliation import (
    reconcile_positions,
    save_circuit_breaker_state,
    restore_circuit_breaker_state,
    periodic_reconciliation_check,
)
from execution.auto_recovery import (
    startup_recovery,
    save_position_state,
    save_heartbeat,
    should_skip_stale_signals,
)
from execution.time_sizing import get_time_multiplier, get_full_time_multiplier
from execution.ops_guard import OpsGuard
from execution.rotation_manager import RotationManager, RotationConfig
from data.fetchers.telemetry import Telemetry

# Feedback loop system
from feedback.loop import FeedbackLoop
from feedback.signal_quality import QualityFeatures, SignalQualityScorer
from feedback.parameter_tuner import ParameterTuner
from feedback.continuous_backtest import ContinuousBacktester

# Perpetual learning systems (Master Engine + 5 subsystems)
from learning import get_master_engine

# Signal ingestion pipeline
from signals.telegram_ingest import TelegramSignalMonitor, IngestedSignal
from signals.llm_analyzer import analyze_signal, format_analysis_for_telegram

# Growth intelligence — self-evolving meta-brain
from llm.growth.orchestrator import get_growth_orchestrator

# Mechanical bot instrumentation (TIER 4: observation-only hooks)
try:
    from llm.mechanical_bot_instrumentation import get_mechanical_bot_instrumentation
    from llm.mechanical_bot_memory import get_mechanical_bot_memory
    _MECHANICAL_BOT_INSTRUMENTATION_AVAILABLE = True
except ImportError:
    _MECHANICAL_BOT_INSTRUMENTATION_AVAILABLE = False

# Bot perception system (TIER 5: unified API + instrumentation aggregation)
try:
    from llm.bot_perception_api import get_bot_perception_api_client
    from llm.bot_perception_aggregator import get_bot_perception_aggregator
    _BOT_PERCEPTION_SYSTEM_AVAILABLE = True
except ImportError:
    _BOT_PERCEPTION_SYSTEM_AVAILABLE = False

# LLM exit engine — dynamic SL/TP management for open positions
try:
    from llm.exit_engine import ExitEngine
    from llm.exit_types import ExitDecision
    _EXIT_ENGINE_AVAILABLE = True
except ImportError:
    _EXIT_ENGINE_AVAILABLE = False

# Feedback loop closers — self-performance, cost, operator channel
from llm.cost_tracker import get_cost_tracker
from llm.operator_channel import get_operator_channel
from llm.self_performance import get_performance_stats

# Wave 1: Signal Flagger — cheap heuristic flags for LLM attention routing
try:
    from llm.signal_flagger import get_signal_flagger, FlagType, FlaggedSignal
    _SIGNAL_FLAGGER_AVAILABLE = True
except ImportError:
    _SIGNAL_FLAGGER_AVAILABLE = False

# Wave 1: Signal Override — bypass soft blockers for powerful signals
try:
    from llm.signal_override import (
        should_override_blocker, BlockerType, get_override_engine,
    )
    _SIGNAL_OVERRIDE_AVAILABLE = True
except ImportError:
    _SIGNAL_OVERRIDE_AVAILABLE = False

# Wave 1: Self-Teaching — periodic learning cycles + knowledge injection
try:
    from llm.self_teaching import get_teaching_engine
    _SELF_TEACHING_AVAILABLE = True
except ImportError:
    _SELF_TEACHING_AVAILABLE = False

# Wave 2: Liquidity Guard — pre-trade market health validation
try:
    from execution.liquidity_guard import validate_liquidity
    _LIQUIDITY_GUARD_AVAILABLE = True
except ImportError:
    _LIQUIDITY_GUARD_AVAILABLE = False

# Phase D+E+F: new modules
from execution.funding_timer import should_close_before_funding, minutes_until_next_funding
from strategies.regime_detector import RegimeTransitionDetector
from monitoring.health import HealthMonitor
from execution.graceful_degradation import DegradationManager
from execution.pending_orders import PendingOrderManager

# Watchdog: background health monitoring with stall detection and auto-alerts
from monitoring.watchdog import get_watchdog

# Enhanced Telegram alerts: actionable signal formatting
from alerts.enhanced_telegram import (
    format_signal_telegram, format_trade_event_telegram,
    format_heartbeat_telegram, format_daily_report_telegram,
)

# Telegram alert bridge: critical event notifications via TradeEventLogger callbacks
from alerts.telegram_alert_bridge import TelegramAlertBridge

# Global Brain + Portfolio Brain: cross-market reasoning for LLM
from llm.global_brain import build_global_context, apply_global_bias
from llm.portfolio_brain import build_portfolio_snapshot

# Self-Tuning Risk Engine: adaptive risk profiles
from risk.self_tuning import (
    get_telemetry as get_risk_telemetry,
    evaluate_and_adjust as risk_evaluate_and_adjust,
    get_dynamic_leverage_cap,
    get_profile_params as get_risk_profile_params,
)

# RL system: transition logging + policy application
from rl.buffer import append_transition as rl_append_transition
from rl.apply_policy import get_combined_rl_multiplier, is_rl_enabled

# Deep memory + cross-symbol pattern tracking
try:
    from llm.deep_memory import get_deep_memory, TradeDNA
    _DEEP_MEMORY_AVAILABLE = True
except ImportError:
    _DEEP_MEMORY_AVAILABLE = False

try:
    from strategies.cross_symbol_patterns import CrossSymbolTracker
    _CROSS_SYMBOL_AVAILABLE = True
except ImportError:
    _CROSS_SYMBOL_AVAILABLE = False

# Survival Pressure: performance accountability injected into LLM context
try:
    from llm.survival_pressure import (
        record_trade_outcome as survival_record_outcome,
        get_survival_context_for_llm,
        get_survival_report,
    )
    _SURVIVAL_PRESSURE_AVAILABLE = True
except ImportError:
    _SURVIVAL_PRESSURE_AVAILABLE = False

# Learning Mode: progressive LLM autonomy (ABSORB -> APPRENTICE -> ACTIVE)
try:
    from llm.learning_mode import (
        is_learning_mode_active,
        get_current_phase,
        record_signal_observed as learning_record_signal,
        record_trade_observed as learning_record_trade,
        record_counterfactual,
        apply_learning_constraints,
        get_learning_report,
        LearningPhase,
    )
    _LEARNING_MODE_AVAILABLE = True
except ImportError:
    _LEARNING_MODE_AVAILABLE = False

# Autonomy Progression: gate-based mode advancement (VETO_ONLY -> SIZING -> DIRECTION -> FULL)
try:
    from llm.progression import evaluate_progression, format_progression_status
    _PROGRESSION_AVAILABLE = True
except ImportError:
    _PROGRESSION_AVAILABLE = False

# Uplift Analytics: baseline vs LLM-filtered performance comparison
try:
    from llm.uplift_analytics import compute_uplift, format_uplift_report
    _UPLIFT_AVAILABLE = True
except ImportError:
    _UPLIFT_AVAILABLE = False

# Adaptive Risk: dynamic risk-per-trade based on streak and regime
try:
    from execution.adaptive_risk import get_adaptive_risk
    _ADAPTIVE_RISK_AVAILABLE = True
except ImportError:
    _ADAPTIVE_RISK_AVAILABLE = False

# Strategy Pruning: auto-reduce weights for underperforming strategies
try:
    from execution.strategy_pruning import evaluate_and_adjust as pruning_evaluate, get_strategy_weight
    _STRATEGY_PRUNING_AVAILABLE = True
except ImportError:
    _STRATEGY_PRUNING_AVAILABLE = False

# Human Copy-Trade Classifier: gate for copy-tradable signal publication
try:
    from classification.human_copy_classifier import classify_human_copy_tradable, CopyTradeResult
    _COPY_CLASSIFIER_AVAILABLE = True
except ImportError:
    _COPY_CLASSIFIER_AVAILABLE = False

# LLM Self-Performance: rolling accuracy stats for self-calibration
try:
    from llm.self_performance import get_compact_stats as get_llm_self_stats
    _SELF_PERF_AVAILABLE = True
except ImportError:
    _SELF_PERF_AVAILABLE = False

# Wave 3: Portfolio Risk Engine — correlation matrix, vol forecasting, risk budgeting
try:
    from analytics.portfolio_risk import get_portfolio_risk_engine
    _PORTFOLIO_RISK_AVAILABLE = True
except ImportError:
    _PORTFOLIO_RISK_AVAILABLE = False

# Wave 4: Performance Attribution — which decisions actually made money
try:
    from analytics.attribution import compute_attribution, format_attribution_report
    _ATTRIBUTION_AVAILABLE = True
except ImportError:
    _ATTRIBUTION_AVAILABLE = False

# Wave 4: A/B Testing — live strategy variant testing
try:
    from analytics.ab_testing import get_ab_manager
    _AB_TESTING_AVAILABLE = True
except ImportError:
    _AB_TESTING_AVAILABLE = False

# Wave 4: Counterfactual Learning — what-if analysis for vetoes and sizing
try:
    from analytics.counterfactual import get_counterfactual_engine
    _COUNTERFACTUAL_AVAILABLE = True
except ImportError:
    _COUNTERFACTUAL_AVAILABLE = False

# Wave 4: Meta-Learning — pattern analysis and strategy idea generation
try:
    from analytics.meta_learning import get_meta_engine
    _META_LEARNING_AVAILABLE = True
except ImportError:
    _META_LEARNING_AVAILABLE = False

# Web Dashboard: visual monitoring
try:
    from dashboard.server import get_dashboard_server
    _DASHBOARD_AVAILABLE = True
except ImportError:
    _DASHBOARD_AVAILABLE = False


def get_tp1_close_pct(confidence: float) -> float:
    """Legacy confidence-based TP1 close percentage.
    Now superseded by TradeProfile for live trading, but kept for
    backward compat (backtest engine, tests).
    Lower confidence = lock in more profit. Higher = let more ride."""
    if confidence < 70:
        return 1.00
    elif confidence < 85:
        return 0.70
    elif confidence < 92:
        return 0.50
    else:
        return 0.30


def exploration_conviction_ok(
    *,
    skip_conf: float,
    win_prob,
    is_toxic: bool,
    reg_wr,
    reg_n: int,
    respect: bool = None,
    conv_thresh: float = None,
    wp_floor: float = None,
    # ── Unified-toxic / EV params (2026-06-25 swarm #4) ──────────────────
    rr_tp1=None,
    fee_drag=None,
    setup_verdict=None,
    setup_pf=None,
    setup_wr=None,
    setup_n=None,
    unified: bool = None,
    min_ev: float = None,
    toxic_min_n: int = None,
):
    """Conviction-aware exploration gate (2026-06-25).

    Decides whether a forced skip->go exploration entry is ALLOWED, using only
    STRUCTURED signals (never text-parsing). Exploration adds edge-data value
    ONLY on genuinely-uncertain (coin-flip) skips; it must NOT override a
    clearly -EV / toxic skip (that just bleeds money — overnight BTC/ETH/HYPE
    LONG were forced and 3/3 stopped out -$64; a toxic BTC_SHORT at 8% WR / n=13
    / PF 0.28 was force-admitted because the regime cell only gated at n>=20).

    Design (swarm #4 GATED FIX):
      * PRIMARY admit signal is the regime-keyed quant win_prob / EV arm, NOT
        the inverted skip_conf "conviction" (entry_decision.confidence is the
        GO-thesis confidence, which LLMs emit LOW on skips, so the old
        `uncertain = skip_conf < conv_thresh` arm almost always passed and was
        effectively inert). skip_conf is DEMOTED to a non-binding secondary
        signal when EXPLORATION_UNIFIED_TOXIC is on.
      * TOXIC is UNIFIED with the LLM/counterfactual veto: it reads the SAME
        {sym}_{side} symbol-side verdict (NEGATIVE_EV/TOXIC, or PF<1.0 at
        n>=EXPLORATION_TOXIC_MIN_N=13) the edge/veto path consults — closing the
        force-admit hole where the regime cell only gated at n>=20.
      * REGIME-AWARENESS is preserved: the admit path is the regime-keyed
        win_prob/EV (so BTC_SHORT in trending_bear, +EV, still explores while
        BTC_SHORT in range/consolidation is declined). No symbol-blanket block.

    Args:
        skip_conf: entry_decision.confidence — the GO-thesis 0-1 confidence
            (NOT a skip-conviction; demoted to non-binding under unified mode).
        win_prob: quant final_wp on 0-1 scale (regime-keyed when
            USE_REGIME_PRIORS on), or None (degrades safe).
        is_toxic: regime-cell toxic flag (belt-and-suspenders arm).
        reg_wr: regime-cell win-rate % (0-100) or None.
        reg_n: regime-cell sample count.
        rr_tp1: reward:risk to TP1 for the EV arm (None -> EV arm skipped).
        fee_drag: round-trip cost in R units for the EV arm (default 0.0).
        setup_verdict: {sym}_{side} backtest verdict string (e.g.
            NEGATIVE_EV_BLOCKED). Toxic when it contains NEGATIVE_EV or TOXIC.
        setup_pf: {sym}_{side} profit factor. Toxic when <1.0 at n>=toxic_min_n.
        setup_wr: {sym}_{side} win-rate % (informational).
        setup_n: {sym}_{side} sample count.
        respect/conv_thresh/wp_floor: overrides; default to env vars
            EXPLORATION_RESPECT_CONVICTION / EXPLORATION_CONVICTION_MAX /
            EXPLORATION_MIN_WINPROB.
        unified/min_ev/toxic_min_n: overrides; default to env vars
            EXPLORATION_UNIFIED_TOXIC / EXPLORATION_MIN_EV /
            EXPLORATION_TOXIC_MIN_N.

    Returns:
        (conviction_ok, uncertain, neg_ev) — conviction_ok is the gate result.
        When respect is False, behaves exactly like prior behavior (always ok).
    """
    if respect is None:
        respect = os.getenv("EXPLORATION_RESPECT_CONVICTION", "true").lower() in ("1", "true", "yes")
    if conv_thresh is None:
        conv_thresh = float(os.getenv("EXPLORATION_CONVICTION_MAX", "0.65"))
    if wp_floor is None:
        wp_floor = float(os.getenv("EXPLORATION_MIN_WINPROB", "0.40"))
    if unified is None:
        unified = os.getenv("EXPLORATION_UNIFIED_TOXIC", "true").lower() in ("1", "true", "yes")
    if min_ev is None:
        min_ev = float(os.getenv("EXPLORATION_MIN_EV", "0.0"))
    if toxic_min_n is None:
        toxic_min_n = int(os.getenv("EXPLORATION_TOXIC_MIN_N", "13"))
    try:
        skip_conf = float(skip_conf or 0.0)
    except (TypeError, ValueError):
        skip_conf = 0.0
    try:
        wp = float(win_prob) if win_prob is not None else None
    except (TypeError, ValueError):
        wp = None

    # ── Symbol-side toxic verdict (UNIFIED with the LLM/counterfactual veto) ──
    # Same {sym}_{side} verdict the edge/veto path consults at n>=13. Degrades
    # safe: missing verdict/PF adds no block.
    setup_toxic = False
    if unified and respect:
        try:
            _n = int(setup_n) if setup_n is not None else 0
        except (TypeError, ValueError):
            _n = 0
        _verdict_str = str(setup_verdict or "").upper()
        if "NEGATIVE_EV" in _verdict_str or "TOXIC" in _verdict_str:
            setup_toxic = True
        try:
            _pf = float(setup_pf) if setup_pf is not None else None
        except (TypeError, ValueError):
            _pf = None
        if _pf is not None and _pf < 1.0 and _n >= toxic_min_n:
            setup_toxic = True

    # ── EV arm (regime-keyed win_prob, same shape ensemble.py:2421 uses) ──────
    # EV = wp*RR_tp1 - (1-wp)*1 - fee_drag. Degrades safe: only fires when both
    # wp and rr_tp1 are present. Blocks when EV<=min_ev (default 0.0).
    ev = None
    ev_neg = False
    if unified and respect and wp is not None and rr_tp1 is not None:
        try:
            _rr = float(rr_tp1)
            _fd = float(fee_drag) if fee_drag is not None else 0.0
            ev = wp * _rr - (1.0 - wp) * 1.0 - _fd
            ev_neg = ev <= min_ev
        except (TypeError, ValueError):
            ev = None
            ev_neg = False

    # -EV guard (independent of conviction): never explore a clearly negative-
    # edge / toxic setup. Degrades safe — a missing/None win_prob adds no block.
    neg_ev = respect and (
        (wp is not None and wp < wp_floor)
        or bool(is_toxic)
        or (reg_wr == 0.0 and reg_n >= 5)
        or setup_toxic
        or ev_neg
    )

    if unified:
        # PRIMARY admit = regime win_prob/EV / non-toxic cell. skip_conf is
        # DEMOTED to a non-binding signal: it no longer GATES admission (it was
        # inverted/inert), so `uncertain` is reported for visibility but the
        # admit decision rests on the -EV/toxic guard only.
        uncertain = (not respect) or (skip_conf < conv_thresh)
        conviction_ok = not neg_ev
    else:
        # Legacy split-source behavior (flag OFF): inverted skip_conf primary +
        # regime-cell-only toxic. Reproduces today's exact behavior.
        uncertain = (not respect) or (skip_conf < conv_thresh)
        conviction_ok = uncertain and not neg_ev
    return conviction_ok, uncertain, neg_ev


def _fmt_price(price: float) -> str:
    """Format price with appropriate precision (handles micro-prices like PEPE)."""
    if price == 0:
        return "0"
    abs_p = abs(price)
    if abs_p >= 1.0:
        return f"{price:,.2f}"
    elif abs_p >= 0.001:
        return f"{price:.4f}"
    elif abs_p >= 0.000001:
        return f"{price:.8f}"
    else:
        return f"{price:.12f}"


# Setup structured logging with rotating file handler
from core.structured_logging import setup_logging, log_trade_event, log_metric

# Extracted mixin modules (refactored from this file)
from core.analytics import AnalyticsMixin
from core.llm_integration import LLMIntegrationMixin
from core.position_wiring import PositionWiringMixin

_is_production = os.getenv("ENVIRONMENT", "paper").lower() == "production"
setup_logging(
    json_mode=_is_production,
    level=os.getenv("LOG_LEVEL", "INFO"),
    log_dir="logs",
)
logger = logging.getLogger("bot.main")


class MultiStrategyBot(AnalyticsMixin, LLMIntegrationMixin, PositionWiringMixin):
    """
    The main bot that orchestrates everything.

    Loop:
    1. Fetch data for all symbols
    2. Run ensemble evaluation
    3. ML-adjust confidence
    4. Determine leverage
    5. Open positions if signal passes filters
    6. Update existing positions (TP/SL/trailing)
    7. Record outcomes for ML learning
    8. Send alerts
    9. Sleep and repeat
    """

    def __init__(self, config: TradingConfig):
        self.config = config
        self.stop_event = threading.Event()

        # Lock guarding self._tick_candidates — under SCAN_PARALLEL_SYMBOLS
        # multiple worker threads (one per symbol) append concurrently.
        self._tick_candidates_lock = threading.Lock()

        # Cheap liveness snapshot for the heartbeat daemon. The main loop
        # refreshes this every tick (and the daemon thread reads it every
        # HEARTBEAT_DAEMON_INTERVAL_S) so data/heartbeat.json stays fresh even
        # during a multi-minute scan, killing false-positive watchdog stalls.
        self._hb_snapshot: dict = {"positions": 0, "equity": 0.0}
        self._hb_snapshot_lock = threading.Lock()
        self._heartbeat_daemon: Optional[threading.Thread] = None

        # Apply paper/live profile overrides (caps leverage, risk, etc.)
        apply_profile(config)

        # Data
        self.fetcher = DataFetcher(
            max_retries=config.fetcher_max_retries,
            retry_delay=5.0,
            cache_ttl=max(90, config.scan_interval_s * 3),  # survive full tick + LLM pipeline
            cb_threshold=config.fetcher_circuit_breaker_threshold,
            cb_reset_s=config.fetcher_circuit_breaker_reset_s,
        )

        # Strategy accuracy weights
        self.weight_mgr = StrategyWeightManager(
            path="ml_data/strategy_weights.json",
            decay_alpha=0.9,
        )

        # Regime-specific feedback (tracks per-regime performance)
        self.regime_feedback = RegimeFeedbackManager(data_dir="data/feedback")

        # Adaptive confidence floor (dynamic thresholds from realized performance)
        self.confidence_floor = AdaptiveConfidenceFloor(data_dir="data/feedback")

        # Hold-time rules (minimum hold times per regime based on live performance)
        self.hold_time_rules = HoldTimeRuleManager(data_dir="data/feedback")

        # Signal quality scorer (meta-confidence based on signal context)
        self.signal_quality = SignalQualityScorer(data_dir="data/feedback")

        # Parameter tuner (autonomous parameter adaptation based on performance)
        self.parameter_tuner = ParameterTuner(data_dir="data/feedback")

        # Continuous backtest (real-time validation of signal quality against historical baselines)
        self.continuous_backtest = ContinuousBacktester(data_dir="data/feedback")

        # Strategies — each toggleable via STRATEGY_*_ENABLED env var
        sym_configs = DEFAULT_SYMBOLS
        self.strategies = []

        if os.getenv("STRATEGY_REGIME_TREND_ENABLED", "true").lower() == "true":
            self.strategies.append(RegimeTrendStrategy(sym_configs, config.htf_hours))
        if os.getenv("STRATEGY_CONFIDENCE_SCORER_ENABLED", "true").lower() == "true":
            self.strategies.append(ConfidenceScorerStrategy(sym_configs, data_dir="ml_data", backtest_mode=True))
        if os.getenv("STRATEGY_MULTI_TIER_QUALITY_ENABLED", "true").lower() == "true":
            self.strategies.append(MultiTierQualityStrategy(sym_configs))
        if os.getenv("STRATEGY_MONTE_CARLO_ENABLED", "false").lower() == "true":
            self.strategies.append(MonteCarloZonesStrategy(
                sym_configs,
                mc_sims=config.mc_num_sims,
                mc_hours=config.mc_forward_hours,
            ))

        # New quant strategies (Phase 6 alpha generation)
        if os.getenv("STRATEGY_FUNDING_RATE_ENABLED", "true").lower() == "true":
            self.strategies.append(FundingRateStrategy(sym_configs))
        if os.getenv("STRATEGY_OI_DELTA_ENABLED", "true").lower() == "true":
            self.strategies.append(OIDeltaStrategy(sym_configs))
        if os.getenv("STRATEGY_BOLLINGER_SQUEEZE_ENABLED", "true").lower() == "true":
            self.strategies.append(BollingerSqueezeStrategy(sym_configs))
        if os.getenv("STRATEGY_VMC_CIPHER_ENABLED", "false").lower() == "true":
            self.strategies.append(VMCCipherStrategy(sym_configs))
        if os.getenv("STRATEGY_LEAD_LAG_ENABLED", "false").lower() == "true":
            self.strategies.append(LeadLagStrategy(sym_configs))
        if os.getenv("STRATEGY_LIQUIDATION_CASCADE_ENABLED", "true").lower() == "true":
            self.strategies.append(LiquidationCascadeStrategy(sym_configs))
        if os.getenv("STRATEGY_PROBABILITY_ENGINE_ENABLED", "true").lower() == "true":
            self.strategies.append(ProbabilityEngineStrategy(
                sym_configs,
                num_sims=config.mc_num_sims,
                forward_bars=config.mc_forward_hours,
            ))
        if os.getenv("STRATEGY_CVD_SIGNAL_ENABLED", "false").lower() == "true":
            try:
                from strategies.cvd_signal import CVDSignalStrategy
                self.strategies.append(CVDSignalStrategy(sym_configs))
            except Exception as e:
                logger.warning(f"[INIT] CVD signal strategy unavailable: {e}")
        if os.getenv("STRATEGY_MEAN_REVERSION_ENABLED", "true").lower() == "true":
            self.strategies.append(MeanReversionStrategy(sym_configs))

        enabled_names = [s.name for s in self.strategies]
        logger.info(f"[INIT] Active strategies: {enabled_names}")
        # Chop detector: multi-factor choppy market filter
        chop = None
        if config.enable_chop_detector:
            try:
                from strategies.chop_detector import ChopDetector
                from trading_config import DEFAULT_SYMBOL_OVERRIDES
                chop = ChopDetector(threshold=config.chop_threshold)
                # Set per-symbol volatility profiles for adaptive thresholds
                for sym, overrides in DEFAULT_SYMBOL_OVERRIDES.items():
                    if hasattr(overrides, "volatility_profile"):
                        chop.set_symbol_profile(sym, overrides.volatility_profile)
                logger.info(f"[INIT] Chop detector enabled (threshold={config.chop_threshold})")
            except Exception as e:
                logger.warning(f"[INIT] Chop detector init failed: {e}")

        self.ensemble = EnsembleStrategy(
            strategies=self.strategies,
            mode=config.ensemble_mode,
            min_votes=config.min_votes_required,
            weight_manager=self.weight_mgr,
            veto_ratio=config.veto_ratio,
            chop_detector=chop,
            confidence_floor=config.ensemble_confidence_floor,
            ranging_confidence_floor=config.ranging_confidence_floor,
        )
        # Wire signal quality scoring to ensemble (applies learned context confidence multipliers)
        self.ensemble._signal_quality_scorer = self.signal_quality

        # Wire manual sniper callback: receives solo signals that the ensemble
        # rejects for insufficient consensus. The sniper has its own proven-setup
        # gates and can profitably trade signals the bot sits out on.
        self.ensemble._manual_sniper_callback = self._on_solo_signal_for_sniper

        # Apply quant system config disables (kill toxic strategies)
        self.ensemble.apply_config_disables(config)

        # Wire volatility profiles for per-symbol confidence floor capping
        from trading_config import DEFAULT_SYMBOL_OVERRIDES
        vol_profiles = {
            sym: ov.volatility_profile
            for sym, ov in DEFAULT_SYMBOL_OVERRIDES.items()
            if hasattr(ov, 'volatility_profile') and ov.volatility_profile
        }
        self.ensemble.set_symbol_volatility_profiles(vol_profiles)

        # ── LLM Sniper Engine (optional, additive — never touches existing trades) ──
        # Intercepts single-strategy ensemble rejections and queues LLM proposals.
        # Activated only when LLM_SNIPER_ENABLED=true. Off by default.
        try:
            import os as _os
            if _os.getenv("LLM_SNIPER_ENABLED", "").lower() in ("1", "true", "yes"):
                from llm.sniper import LLMSniperEngine
                _sniper = LLMSniperEngine(max_leverage=config.max_leverage)
                self.ensemble._sniper_callback = _sniper.evaluate_candidate
                logger.info("[INIT] LLM Sniper Engine enabled — queuing rejected 1-vote signals for LLM review")
            else:
                logger.debug("[INIT] LLM Sniper disabled (LLM_SNIPER_ENABLED not set)")
        except Exception as _se:
            logger.warning(f"[INIT] LLM Sniper Engine init failed (non-fatal): {_se}")

        # ── Manual Sniper Signal System (reads signals only, never touches trading) ──
        # Also runs standalone via: python -m manual.runner
        self._manual_sniper = None
        self._manual_alerter = None
        self._sniper_simulator = None
        try:
            from manual.sniper_filter import ManualSniperFilter
            from manual.alerts import ManualSniperAlerter
            from manual.config import ManualSniperConfig
            _ms_config = ManualSniperConfig()
            if _ms_config.enabled:
                self._manual_sniper = ManualSniperFilter(_ms_config)
                self._manual_alerter = ManualSniperAlerter(_ms_config)
                try:
                    from manual.simulator import SniperSimulator
                    self._sniper_simulator = SniperSimulator(
                        starting_equity=_ms_config.equity
                    )
                except Exception:
                    pass
                # Signal value tracker: quantifies every signal's real-world outcome
                self._signal_tracker = None
                try:
                    from manual.signal_tracker import SignalValueTracker
                    self._signal_tracker = SignalValueTracker()
                    logger.info("[INIT] Signal Value Tracker enabled")
                except Exception as _svt_err:
                    logger.debug(f"[INIT] Signal tracker not available: {_svt_err}")
                # Auto-execute: optionally route sniper signals to the order executor
                self._sniper_auto_execute = os.getenv(
                    "SNIPER_AUTO_EXECUTE", "false"
                ).lower() == "true"
                logger.info(
                    f"[INIT] Manual Sniper System enabled — "
                    f"target=${_ms_config.daily_target}/day "
                    f"max_lev={_ms_config.max_leverage}x "
                    f"auto_execute={self._sniper_auto_execute}"
                )
        except Exception as _ms_err:
            logger.debug(f"[INIT] Manual Sniper System not available: {_ms_err}")
        if not hasattr(self, "_sniper_auto_execute"):
            self._sniper_auto_execute = False
        if not hasattr(self, "_signal_tracker"):
            self._signal_tracker = None

        # ── Anticipatory Entry Engine (precision limit-order style entries) ──
        self._anticipation_engine = None
        _anticipatory_enabled = os.getenv("ANTICIPATORY_ENTRIES_ENABLED", "true").lower() == "true"
        if _anticipatory_enabled and self._manual_sniper is not None:
            try:
                from manual.anticipatory_entries import get_anticipation_engine
                self._anticipation_engine = get_anticipation_engine()
                logger.info("[INIT] Anticipatory Entry Engine enabled (precision entries)")
            except Exception as _ae_err:
                logger.debug(f"[INIT] Anticipatory Engine not available: {_ae_err}")

        # ── Quant Brain Pre-Filter (zero-cost, rule-based signal gating) ──
        self._quant_brain = None
        self._quant_brain_enabled = os.getenv("QUANT_BRAIN_ENABLED", "true").lower() == "true"
        if self._quant_brain_enabled:
            try:
                from llm.quant_brain import get_quant_brain
                self._quant_brain = get_quant_brain()
                logger.info("[INIT] Quant Brain pre-filter enabled (zero API cost)")
            except Exception as _qb_err:
                logger.warning(f"[INIT] Quant Brain init failed (non-fatal): {_qb_err}")
                self._quant_brain = None

        # Reflection Engine: post-trade analysis with coded observations
        self._reflection_engine = None
        try:
            from llm.reflection_engine import ReflectionEngine
            self._reflection_engine = ReflectionEngine()
            logger.info("[INIT] Reflection Engine enabled (post-trade analysis)")
        except Exception as _re_err:
            logger.debug(f"[INIT] Reflection engine not available: {_re_err}")

        # Background Thinker: periodic rule-based analysis between signals (no LLM calls)
        self._background_thinker = None
        try:
            from llm.agents.background_thinker import BackgroundThinker
            self._background_thinker = BackgroundThinker(interval_seconds=300)
            logger.info("[INIT] Background thinker enabled (5min cycles)")
        except ImportError:
            logger.debug("[INIT] Background thinker not available")

        # Pre-Trade Simulator: scenario-based imagination before each entry (no LLM calls)
        self._pre_trade_sim = None
        self._last_simulation: Dict[str, Any] = {}
        try:
            from llm.agents.pre_trade_simulator import PreTradeSimulator
            self._pre_trade_sim = PreTradeSimulator()
            logger.info("[INIT] Pre-trade simulator enabled")
        except ImportError:
            logger.debug("[INIT] Pre-trade simulator not available")

        # Agent Performance Tracker: per-agent decision quality measurement
        self._agent_perf = None
        try:
            from llm.agents.agent_performance import get_tracker
            self._agent_perf = get_tracker()
            logger.info("[INIT] Agent performance tracker enabled")
        except ImportError:
            logger.debug("[INIT] Agent performance tracker not available")

        # Agent Cost Optimizer: gates LLM calls based on budget, tracks ROI per pipeline
        self._cost_optimizer = None
        try:
            from llm.agents.cost_optimizer import get_cost_optimizer
            _daily_budget = float(os.getenv("LLM_DAILY_BUDGET_USD", "0.50"))
            self._cost_optimizer = get_cost_optimizer(daily_budget=_daily_budget)
            logger.info(f"[INIT] Cost optimizer enabled (budget=${_daily_budget:.2f}/day)")
        except Exception as _co_err:
            logger.debug(f"[INIT] Cost optimizer not available: {_co_err}")

        # Active Learning Engine: meta-learning that improves the brain over time
        self._active_learning = None
        try:
            from llm.agents.active_learning import ActiveLearningEngine
            self._active_learning = ActiveLearningEngine()
            logger.info("[INIT] Active learning engine enabled (30min cycles)")
        except Exception as _al_err:
            logger.debug(f"[INIT] Active learning engine not available: {_al_err}")

        # Execution
        self.risk_mgr = RiskManager(
            starting_equity=config.starting_equity,
            risk_per_trade=config.risk_per_trade,
            max_open_positions=config.max_open_positions,
            circuit_breaker=CircuitBreaker(
                daily_loss_limit_pct=config.circuit_breaker_daily_loss_pct,
                max_consecutive_losses=config.max_consecutive_losses,
                cooldown_minutes=config.circuit_breaker_cooldown_min,
            ),
        )

        self.pos_mgr = PositionManager(
            taker_fee_bps=config.taker_fee_bps,
            enable_trailing=config.enable_trailing_stop,
            trailing_atr_mult=config.trailing_stop_atr_mult,
            time_stop_hours=config.time_stop_hours,
            hold_time_rules=self.hold_time_rules,
        )
        # Per-symbol execution lock: prevents duplicate entries when two signals
        # for the same symbol race through the pipeline simultaneously.
        self._executing_symbols: set = set()
        self._executing_lock = threading.Lock()
        self.leverage_mgr = LeverageManager(
            enable_leverage=config.enable_leverage,
            max_leverage=config.max_leverage,
        )

        # Order executor: bridges PositionManager with exchange
        from execution.order_executor import create_executor
        _exec_mode = "live" if not config.is_paper else "paper"
        _max_slip = float(os.getenv("MAX_ENTRY_SLIPPAGE_PCT", "1.5"))
        try:
            self.order_executor = create_executor(
                fetcher=self.fetcher,
                mode=_exec_mode,
                max_slippage_pct=_max_slip,
            )
        except ValueError:
            # Live mode without exchange credentials — fall back to paper
            logger.warning("[INIT] No exchange credentials for live mode, falling back to paper executor")
            self.order_executor = create_executor(fetcher=self.fetcher, mode="paper")

        # Entry optimizer: limit-order-first for better fills (saves ~3 bps per entry)
        try:
            from execution.entry_optimizer import EntryOptimizer
            _limit_timeout = float(os.getenv("ENTRY_LIMIT_TIMEOUT_S", "10"))
            self.entry_optimizer = EntryOptimizer(
                use_limit_orders=os.getenv("ENTRY_USE_LIMIT_ORDERS", "true").lower() in ("1", "true", "yes"),
                use_burst_detection=False,  # Burst detection adds latency, disable for now
                limit_timeout_s=_limit_timeout,
            )
            logger.info(f"[INIT] Entry optimizer enabled (limit timeout={_limit_timeout}s)")
        except Exception as _eo_err:
            logger.debug(f"[INIT] Entry optimizer not available: {_eo_err}")
            self.entry_optimizer = None

        # ML
        self.ml = SignalLearner(
            data_dir="ml_data",
            min_samples=config.ml_min_samples,
            retrain_interval=config.ml_retrain_interval,
            adjustment_weight=config.ml_adjustment_weight,
        ) if config.enable_ml else None

        # Alerts
        self.alerts = AlertRouter(
            discord_webhook=config.discord_webhook,
            telegram_token=config.telegram_token,
            telegram_chat_id=config.telegram_chat_id,
        )

        # Telegram alert bridge: hooks into TradeEventLogger for critical alerts
        self.telegram_bridge = TelegramAlertBridge(
            telegram_token=config.telegram_token,
            telegram_chat_id=config.telegram_chat_id,
        )
        try:
            from core.structured_logging import get_trade_event_logger
            tel = get_trade_event_logger()
            tel.add_callback(self.telegram_bridge.on_trade_event)
        except Exception as e:
            logger.debug(f"Telegram alert bridge callback registration skipped: {e}")

        # Trade logging (paper trading validation)
        self.trade_logger = TradeLogger(log_dir="paper_trades") if not config.auto_trade else None

        self._tick = 0
        self._needed_tfs = self.ensemble.get_all_required_timeframes()
        # Always fetch 4h for intermediate-trend context injected into LLM agent snapshots
        if "4h" not in self._needed_tfs:
            self._needed_tfs.append("4h")

        # Per-symbol cooldown: prevent rapid re-entry after a position closes
        self._symbol_cooldown: Dict[str, float] = {}  # symbol -> timestamp of last close
        self._cooldown_seconds = config.loss_cooldown_s
        self._win_cooldown_seconds = config.win_cooldown_s

        # Correlation guard: prevent correlated blowups
        self._max_same_direction = int(os.getenv("MAX_SAME_DIRECTION", "3"))
        self._max_same_tier = int(os.getenv("MAX_SAME_TIER", "2"))

        # Track last close result per symbol for anti-round-tripping
        self._last_close_win: Dict[str, bool] = {}  # symbol -> was_win
        self._last_close_side: Dict[str, str] = {}  # symbol -> "LONG"/"SHORT"

        # Per-symbol daily loss tracking: stop trading a symbol after -$30/day
        self._symbol_daily_pnl: Dict[str, float] = {}  # symbol -> cumulative PnL today
        self._symbol_daily_pnl_date: str = ""  # YYYY-MM-DD of last reset
        self._symbol_daily_loss_limit = float(os.getenv("SYMBOL_DAILY_LOSS_LIMIT", "-30"))

        # Signal dedup: prevent spam from repeated same-side evaluations
        self._last_signal: Dict[str, tuple] = {}  # symbol -> (side, timestamp)
        self._signal_dedup_seconds = config.signal_dedup_window_s

        # Last known prices for fill-price validation
        self._last_prices: Dict[str, float] = {}  # symbol -> price
        # Last known funding rates per symbol (updated from fetcher)
        self._last_funding_rates: Dict[str, float] = {}  # symbol -> funding rate
        self._last_open_interest: Dict[str, float] = {}  # symbol -> OI (for oi_delta strategy)
        self._oi_history: Dict[str, collections.deque] = {}  # symbol -> deque of {ts, oi} (12-entry rolling)

        # LLM meta-brain
        self.llm_mode = get_llm_mode()
        self._llm_triggers = TriggerAccumulator()
        self._slippage_reject_cooldown: Dict[str, float] = {}  # symbol -> timestamp
        self.pending_orders = PendingOrderManager(max_pending=5)

        # Dual-world candidate logging (baseline vs LLM)
        self._candidate_logger = CandidateLogger()
        self._active_candidates: Dict[str, TradeCandidate] = {}  # symbol -> last candidate that opened a trade

        # Operations guard: kill switch, rate limiting, exposure limits
        self.ops_guard = OpsGuard()

        # Trade rotation manager: rotate stale/losing positions into better signals
        if config.enable_rotation:
            self.rotation_mgr = RotationManager(RotationConfig(
                min_hold_before_rotation_s=config.rotation_min_hold_s,
                global_rotation_cooldown_s=config.rotation_global_cooldown_s,
                max_rotations_per_hour=config.rotation_max_per_hour,
                max_rotations_per_day=config.rotation_max_per_day,
                estimated_round_trip_fee_pct=config.taker_fee_bps / 100.0,  # one-way fee in % (rotation mgr doubles for close+open)
            ))
        else:
            self.rotation_mgr = None

        # Feedback loop: self-improving confidence, backtesting, quality scoring
        self.feedback = FeedbackLoop(data_dir="data/feedback")
        # Wire SignalQualityScorer into ensemble so session/hour/entry_type WR
        # adjustments (US=57% WR, Asia=14% WR) actually affect confidence scoring.
        self.ensemble.set_quality_scorer(self.feedback.quality)
        logger.info("[INIT] SignalQualityScorer wired into ensemble — session/hour WR now adjusts confidence")

        # Quant system: IC tracker, Kelly engine, trade ledger, shadow ledger, daily report
        try:
            from feedback.ic_tracker import ICTracker
            from feedback.kelly_engine import KellyEngine
            from feedback.trade_ledger import TradeLedger
            from feedback.shadow_ledger import ShadowLedger
            from feedback.daily_report import DailyReporter
            from execution.correlation_gate import CorrelationGate
            self.ic_tracker = ICTracker(data_dir="data")
            self.kelly_engine = KellyEngine(data_path="data/kelly_weights.json")
            self.trade_ledger = TradeLedger(data_dir="data")
            self.shadow_ledger = ShadowLedger(data_dir="data")
            self.correlation_gate = CorrelationGate()
            from execution.sector_exposure import SectorExposure
            self._sector_exposure_cls = SectorExposure
            from execution.execution_analytics import ExecutionAnalytics
            self.execution_analytics = ExecutionAnalytics(data_dir="data")
            self.daily_reporter = DailyReporter(
                trade_ledger=self.trade_ledger,
                ic_tracker=self.ic_tracker,
                kelly_engine=self.kelly_engine,
            )
            self.ensemble.set_shadow_ledger(self.shadow_ledger)
            # Wire missed trade tracker into ensemble + main loop
            try:
                from feedback.missed_trade_tracker import MissedTradeTracker
                self._missed_trade_tracker = MissedTradeTracker(data_dir="data")
                self.ensemble.set_missed_trade_tracker(self._missed_trade_tracker)
                logger.info("[INIT] MissedTradeTracker wired into ensemble + pipeline")
            except Exception as mt_e:
                logger.debug(f"[INIT] MissedTradeTracker unavailable: {mt_e}")
                self._missed_trade_tracker = None
            # Wire rejection outcome tracker for adaptive EV calibration
            try:
                from feedback.rejection_tracker import RejectionOutcomeTracker
                self._rejection_tracker = RejectionOutcomeTracker(data_dir="data")
                self.ensemble._rejection_outcome_tracker = self._rejection_tracker
                logger.info("[INIT] RejectionOutcomeTracker wired into ensemble EV gate")
            except Exception as rt_e:
                logger.debug(f"[INIT] RejectionOutcomeTracker unavailable: {rt_e}")
                self._rejection_tracker = None
            # Wire EV calibrator for adaptive threshold adjustment.
            # FALLACY_AUDIT M7 (2026-07-02): the tracker was never passed (so
            # update() could not read outcome stats) and ingest_outcome did not
            # exist (AttributeError nulled this handle while the ensemble kept
            # the override). Tracker now passed; callback exists.
            try:
                from feedback.ev_calibrator import EVCalibrator
                self._ev_calibrator = EVCalibrator(
                    rejection_tracker=self._rejection_tracker,
                    data_dir="data",
                )
                self.ensemble._ev_calibrator = self._ev_calibrator
                # Connect rejection tracker -> EV calibrator feedback loop
                if self._rejection_tracker is not None:
                    self._rejection_tracker._outcome_callback = self._ev_calibrator.ingest_outcome
                logger.info("[INIT] EVCalibrator wired into ensemble EV gate (tracker connected)")
            except Exception as ev_e:
                logger.debug(f"[INIT] EVCalibrator unavailable: {ev_e}")
                self._ev_calibrator = None
            # Wire LLM-reasoned override coordinator into ensemble
            # Allows OverrideAgent to bypass EV blocks when regime-specific edge is proven
            try:
                from llm.agents.coordinator import get_coordinator, is_multi_agent_enabled
                if is_multi_agent_enabled():
                    self.ensemble._override_coordinator = get_coordinator()
                    logger.info("[INIT] LLM Override Coordinator wired into ensemble EV gate")
            except Exception as oc_e:
                logger.debug(f"[INIT] Override coordinator unavailable: {oc_e}")
            # Wire cross-asset correlation boost into ensemble
            try:
                from feedback.correlation_boost import CrossAssetCorrelationBoost
                self._correlation_boost = CrossAssetCorrelationBoost(symbols=list(DEFAULT_SYMBOLS.keys()))
                self.ensemble._correlation_boost = self._correlation_boost
                logger.info("[INIT] CrossAssetCorrelationBoost wired into ensemble win_prob")
            except Exception as cb_e:
                logger.debug(f"[INIT] CrossAssetCorrelationBoost unavailable: {cb_e}")
                self._correlation_boost = None
            # Wire IC tracker into ensemble so inverted/decaying factors get downweighted in voting
            if self.ic_tracker:
                self.ensemble.ic_tracker = self.ic_tracker
                logger.info("[INIT] IC tracker wired into ensemble voting — inverted factors will be auto-downweighted")
            # Wire regime-aware strategy weighting into ensemble
            try:
                if config.regime_strategy_weighting_enabled:
                    from data.regime_strategy_weighter import RegimeStrategyWeighter
                    self._regime_strategy_weighter = RegimeStrategyWeighter(data_dir="data/regime_strategy_weights")
                    self.ensemble.set_regime_strategy_weighter(self._regime_strategy_weighter)
                    logger.info("[INIT] RegimeStrategyWeighter wired — strategy weights adjust per regime")
                else:
                    self._regime_strategy_weighter = None
            except Exception as rsw_e:
                logger.debug(f"[INIT] RegimeStrategyWeighter unavailable: {rsw_e}")
                self._regime_strategy_weighter = None
            logger.info("[INIT] Quant system loaded: IC tracker, Kelly engine, trade ledger, shadow ledger, correlation gate, daily report")
        except Exception as e:
            logger.warning(f"[INIT] Quant system partially unavailable: {e}")
            self.ic_tracker = None
            self.kelly_engine = None
            self.trade_ledger = None
            self.shadow_ledger = None
            self.correlation_gate = None
            self._sector_exposure_cls = None
            self.execution_analytics = None
            self._missed_trade_tracker = None

        # AutoOptimizer: autonomous review + parameter tuning
        # Lazy-initialized on first tick when EvolutionTracker is ready
        self._evolution_tracker = None
        self._auto_optimizer_initialized = False
        logger.info("[INIT] AutoOptimizer will initialize on first tick with EvolutionTracker")

        # Growth intelligence: self-evolving meta-brain
        self.growth = get_growth_orchestrator()

        # Operator channel: LLM → operator communication via Telegram
        self.operator_channel = get_operator_channel(alert_router=self.alerts)

        # Wave 1: Signal Flagger — cheap heuristic flags for every signal
        self.signal_flagger = get_signal_flagger() if _SIGNAL_FLAGGER_AVAILABLE else None

        # Wave 1: Signal Override — bypass soft blockers for powerful signals
        self.signal_override = get_override_engine() if _SIGNAL_OVERRIDE_AVAILABLE else None

        # Wave 1: Self-Teaching — periodic learning cycles
        self.teaching_engine = get_teaching_engine() if _SELF_TEACHING_AVAILABLE else None

        # Seed knowledge base with foundational axioms (idempotent — skips if already seeded)
        if self.teaching_engine:
            try:
                from llm.knowledge_seed import seed_knowledge_base
                seed_knowledge_base()
            except Exception as e:
                logger.debug(f"Knowledge seed error (non-fatal): {e}")

        # Veto tracking is handled by growth orchestrator (growth/veto_feedback.py)

        # Confidence calibration: bootstrap from backtest data if no curve exists yet
        try:
            from llm.confidence_calibrator import ConfidenceCalibrator
            self._confidence_calibrator = ConfidenceCalibrator(data_dir="data/llm")
            if not self._confidence_calibrator._curve:
                self._confidence_calibrator.bootstrap_from_backtest("data/backtest_trades_30d.csv")
                logger.info("[INIT] Confidence calibrator bootstrapped from backtest data")
            else:
                logger.info("[INIT] Confidence calibrator loaded existing calibration curve")
        except Exception as cc_err:
            logger.debug(f"[INIT] Confidence calibrator unavailable: {cc_err}")
            self._confidence_calibrator = None

        # Phase D+E+F: new subsystems
        self.regime_detector = RegimeTransitionDetector()
        self.health_monitor = HealthMonitor()
        self.degradation = DegradationManager()

        # LLM exit engine: dynamic SL/TP management for open positions
        if _EXIT_ENGINE_AVAILABLE:
            self.exit_engine = ExitEngine()
            logger.info("[INIT] LLM exit engine loaded")
        else:
            self.exit_engine = None
            logger.warning("[INIT] LLM exit engine unavailable — running without dynamic exits")
        self._exit_check_counter = 0

        # Cross-symbol pattern tracker: detects lead-lag relationships
        self.cross_symbol_tracker = CrossSymbolTracker() if _CROSS_SYMBOL_AVAILABLE else None

        # Cross-asset lead-lag monitor: BTC leads SOL/ETH
        self._cross_asset_monitor = None
        try:
            from execution.cross_asset_alert import CrossAssetLeadLagMonitor, LeadLagBoostEngine
            self._cross_asset_monitor = CrossAssetLeadLagMonitor()
            logger.info("[INIT] Cross-asset lead-lag monitor enabled (BTC→SOL/ETH)")
            # LeadLagBoostEngine: real-time confidence boost for follower signals
            if getattr(self.config, 'enable_lead_lag_boost', False):
                self._lead_lag_engine = LeadLagBoostEngine(
                    btc_move_threshold=getattr(self.config, 'lead_lag_btc_move_threshold', 0.3),
                    max_boost=getattr(self.config, 'lead_lag_max_boost', 12.0),
                    min_correlation=getattr(self.config, 'lead_lag_min_correlation', 0.60),
                    correlation_decay=getattr(self.config, 'lead_lag_correlation_decay', 0.98),
                )
                self.ensemble.set_lead_lag_engine(self._lead_lag_engine)
                logger.info("[INIT] Lead-lag boost engine enabled (BTC→SOL/ETH confidence boost)")
            else:
                self._lead_lag_engine = None
        except Exception as _ca_err:
            logger.debug(f"[INIT] Cross-asset monitor not available: {_ca_err}")
            self._lead_lag_engine = None

        # Track 1h price changes for cross-market divergence detection
        self._price_changes_1h: Dict[str, float] = {}
        self._price_highs_1h: Dict[str, float] = {}  # Rolling 1h high per symbol (for veto resolution)
        self._price_lows_1h: Dict[str, float] = {}   # Rolling 1h low per symbol (for veto resolution)

        # Self-tuning risk engine: adaptive profiles based on equity curve
        self.risk_telemetry = get_risk_telemetry()

        # Adaptive risk: dynamic risk-per-trade based on streak and regime
        self.adaptive_risk = get_adaptive_risk() if _ADAPTIVE_RISK_AVAILABLE else None

        # Cache global bias from Global Brain (updated each LLM context build)
        self._global_bias: str = "neutral"
        self._global_bias_adjustment: Dict[str, Any] = {}

        # Telegram command bot
        tg_user_id = int(os.getenv("TELEGRAM_ALLOWED_USER_ID") or "0")
        self.telegram_bot = TelegramCommandBot(
            token=config.telegram_token,
            allowed_user_id=tg_user_id,
            bot_instance=self,
        )

        # Telegram signal ingestion pipeline
        self.signal_monitor = TelegramSignalMonitor(
            on_signal=self._handle_ingested_signal,
        )

        # Watchdog: background health monitoring with stall detection
        self.watchdog = get_watchdog(
            alert_fn=self.alerts.send_market_update if self.alerts else None,
        )

        # Wave 3: Portfolio Risk Engine — correlation, vol forecasting, risk budgeting
        self.portfolio_risk = get_portfolio_risk_engine() if (
            _PORTFOLIO_RISK_AVAILABLE and config.enable_portfolio_risk
        ) else None

        # Wave 4: A/B Testing — live strategy variant testing
        self.ab_manager = get_ab_manager() if (
            _AB_TESTING_AVAILABLE and config.enable_ab_testing
        ) else None

        # Wave 4: Counterfactual Learning — what-if veto and sizing analysis
        self.counterfactual = get_counterfactual_engine() if (
            _COUNTERFACTUAL_AVAILABLE and config.enable_counterfactual
        ) else None

        # Wave 4: Meta-Learning — pattern analysis and strategy idea generation
        self.meta_engine = get_meta_engine() if (
            _META_LEARNING_AVAILABLE and config.enable_meta_learning
        ) else None

        # Web Dashboard
        self.dashboard = get_dashboard_server() if (
            _DASHBOARD_AVAILABLE and config.enable_dashboard
        ) else None

        # Paper trading hourly checkpoint (paper mode only)
        self.paper_validator = None
        self._paper_checkpoint_last = time.time()
        if config.is_paper:
            try:
                from monitoring.paper_validator import PaperValidator
                from core.signal_pipeline import enable_rejection_logging
                enable_rejection_logging(True)
                self.paper_validator = PaperValidator(
                    risk_mgr=self.risk_mgr,
                    pos_mgr=self.pos_mgr,
                    alert_router=self.alerts,
                    start_equity=getattr(config, "starting_equity", 0.0),
                )
                logger.info("[PAPER-VALIDATOR] Hourly checkpoint monitoring enabled")
            except Exception as e:
                logger.debug(f"[PAPER-VALIDATOR] Not available: {e}")

        # ── Dual Wallet System ──
        self._dual_wallet_enabled = config.dual_wallet_enabled
        self._wallet_a = None
        self._wallet_b = None
        self._wallet_dispatcher = None
        self._account_guardian = None

        if self._dual_wallet_enabled:
            try:
                from wallet.profile import wallet_a_default, wallet_b_default
                from wallet.context import WalletContext
                from wallet.dispatcher import WalletDispatcher
                from wallet.guardian import AccountGuardian
                from wallet.pnl_tracker import WalletPnLTracker

                profile_a = wallet_a_default()
                profile_b = wallet_b_default()

                # Each wallet gets its own execution components
                wallet_equity_a = config.starting_equity * config.wallet_a_equity_pct
                wallet_equity_b = config.starting_equity * config.wallet_b_equity_pct

                self._wallet_a = WalletContext(profile=profile_a)
                self._wallet_a.pos_mgr = PositionManager(
                    taker_fee_bps=config.taker_fee_bps,
                    enable_trailing=config.enable_trailing_stop,
                    trailing_atr_mult=config.trailing_stop_atr_mult,
                    time_stop_hours=config.time_stop_hours,
                    hold_time_rules=self.hold_time_rules,
                )
                self._wallet_a.risk_mgr = RiskManager(
                    starting_equity=wallet_equity_a,
                    risk_per_trade=profile_a.risk_per_trade,
                    max_open_positions=profile_a.max_open_positions,
                    circuit_breaker=CircuitBreaker(
                        daily_loss_limit_pct=profile_a.cb_daily_loss_pct,
                        max_consecutive_losses=profile_a.cb_max_consecutive_losses,
                        cooldown_minutes=config.circuit_breaker_cooldown_min,
                    ),
                )
                self._wallet_a.circuit_breaker = self._wallet_a.risk_mgr.circuit_breaker
                self._wallet_a.leverage_mgr = LeverageManager(
                    enable_leverage=config.enable_leverage,
                    max_leverage=profile_a.max_leverage,
                )
                self._wallet_a.pnl_tracker = WalletPnLTracker(
                    "A", wallet_equity_a, data_dir="data",
                )
                self._wallet_a.initialize()

                self._wallet_b = WalletContext(profile=profile_b)
                self._wallet_b.pos_mgr = PositionManager(
                    taker_fee_bps=config.taker_fee_bps,
                    enable_trailing=config.enable_trailing_stop,
                    trailing_atr_mult=config.trailing_stop_atr_mult,
                    time_stop_hours=config.time_stop_hours,
                    hold_time_rules=self.hold_time_rules,
                )
                self._wallet_b.risk_mgr = RiskManager(
                    starting_equity=wallet_equity_b,
                    risk_per_trade=profile_b.risk_per_trade,
                    max_open_positions=profile_b.max_open_positions,
                    circuit_breaker=CircuitBreaker(
                        daily_loss_limit_pct=profile_b.cb_daily_loss_pct,
                        max_consecutive_losses=profile_b.cb_max_consecutive_losses,
                        cooldown_minutes=config.circuit_breaker_cooldown_min,
                    ),
                )
                self._wallet_b.circuit_breaker = self._wallet_b.risk_mgr.circuit_breaker
                self._wallet_b.leverage_mgr = LeverageManager(
                    enable_leverage=config.enable_leverage,
                    max_leverage=profile_b.max_leverage,
                )
                self._wallet_b.pnl_tracker = WalletPnLTracker(
                    "B", wallet_equity_b, data_dir="data",
                )
                self._wallet_b.initialize()

                self._account_guardian = AccountGuardian()
                self._wallet_dispatcher = WalletDispatcher(
                    self._wallet_a, self._wallet_b, self._account_guardian,
                )

                logger.info(
                    f"[INIT] Dual Wallet System enabled: "
                    f"A ({profile_a.name}: {profile_a.max_leverage}x, "
                    f"${wallet_equity_a:.2f}) + "
                    f"B ({profile_b.name}: {profile_b.max_leverage}x, "
                    f"${wallet_equity_b:.2f})"
                )
            except Exception as dw_err:
                logger.warning(f"[INIT] Dual wallet init failed, falling back to single: {dw_err}")
                self._dual_wallet_enabled = False
                self._wallet_a = None
                self._wallet_b = None
                self._wallet_dispatcher = None
                self._account_guardian = None

    def _start_perception_capture(self):
        """Start async perception capture task (TIER 5) in background thread."""
        if not _BOT_PERCEPTION_SYSTEM_AVAILABLE:
            logger.debug("[INIT] Bot perception system not available, skipping")
            return

        def run_perception_loop():
            """Run async perception capture in a dedicated thread."""
            try:
                async def capture_loop():
                    """Async loop that continuously captures perception data."""
                    api = get_bot_perception_api_client()
                    agg = get_bot_perception_aggregator()

                    while not self.stop_event.is_set():
                        try:
                            # Fetch complete perception snapshot from API
                            perception_data = await api.fetch_complete_perception()

                            if perception_data:
                                # Capture unified perception combining API + mechanical bot data
                                agg.capture_unified_perception(
                                    system_summary=perception_data.get('summary', {}),
                                    strategy_summaries=perception_data.get('strategies', {}),
                                    llm_decision=perception_data.get('llm', {}).get('latest_decision'),
                                    agent_brains=perception_data.get('agents', {}),
                                    agent_debate=perception_data.get('debate'),
                                    pipeline_health=perception_data.get('pipeline', {}),
                                )

                        except Exception as e:
                            logger.debug(f"[PERCEPTION] Capture error: {e}")

                        # Sleep before next capture (5-second interval)
                        await asyncio.sleep(5)

                # Create and run event loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(capture_loop())

            except Exception as e:
                logger.warning(f"[PERCEPTION] Thread error: {e}")
            finally:
                logger.debug("[PERCEPTION] Capture thread stopped")

        # Start perception capture in background thread
        perception_thread = threading.Thread(target=run_perception_loop, daemon=True, name="PerceptionCapture")
        perception_thread.start()
        logger.info("[INIT] Bot perception capture started (TIER 5)")

    def _start_exit_regret_scorer(self):
        """Background: score matured exit-closes against forward price (exit-regret) on a throttled
        cadence. MEASUREMENT-ONLY — never touches any trading/exit decision. Runs every
        EXIT_REGRET_SCAN_S (default 300s) in a daemon thread so the price fetches never block the tick.
        Produces data/logs/exit_regret_scores.jsonl: per (symbol,side,regime,exit_type) regret —
        the foundation for ever letting the LLM exit agent earn back close authority on measured edge."""
        interval = int(os.getenv("EXIT_REGRET_SCAN_S", "300"))

        def _scorer_loop():
            try:
                from analytics.exit_regret import ExitRegretEngine
                eng = ExitRegretEngine()
            except Exception as e:
                logger.warning(f"[EXIT-REGRET] init failed, scorer disabled: {e}")
                return
            time.sleep(90)  # let startup settle before competing for the price fetcher
            while True:
                try:
                    n = eng.resolve_pending()
                    if n:
                        logger.info(f"[EXIT-REGRET] scored {n} matured exits")
                except Exception as e:
                    logger.debug(f"[EXIT-REGRET] scan error: {e}")
                time.sleep(interval)

        t = threading.Thread(target=_scorer_loop, daemon=True, name="ExitRegretScorer")
        t.start()
        logger.info(f"[INIT] Exit-regret scorer started (every {interval}s)")

    def _start_funding_oi_collector(self):
        """Background: collect funding-rate + open-interest time-series for all tracked symbols
        into data/funding_oi_history.jsonl on a throttled cadence. MEASUREMENT-ONLY — never touches a
        trade. Feeds the OI-divergence + funding-trend perception in llm/agents/external_data.py
        (get_oi_divergence_insight / get_funding_trend). The standalone tools/funding_oi_collector.py
        died in the ~Jun-7 blackout (22d stale); this revives it INSIDE the bot so it lives and restarts
        with the process — no separate task, no admin. Free Hyperliquid public data, no creds."""
        if os.getenv("FUNDING_OI_COLLECTOR_ENABLED", "true").lower() not in ("1", "true", "yes"):
            logger.info("[FUNDING-OI] collector disabled via env")
            return
        interval = int(os.getenv("FUNDING_OI_INTERVAL_S", "900"))  # 15 min default

        def _collector_loop():
            try:
                import sys as _sys
                _tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
                if _tools not in _sys.path:
                    _sys.path.insert(0, _tools)
                import funding_oi_collector as foc
                ex = foc.init_exchange()
            except Exception as e:
                logger.warning(f"[FUNDING-OI] init failed, collector disabled: {e}")
                return
            time.sleep(120)  # let startup settle before competing for network
            while True:
                try:
                    recs = foc.collect_tick(ex)
                    if recs:
                        foc.save_records(recs)
                        logger.info(f"[FUNDING-OI] collected {len(recs)} funding/OI records")
                except Exception as e:
                    logger.debug(f"[FUNDING-OI] collect error: {e}")
                time.sleep(interval)

        t = threading.Thread(target=_collector_loop, daemon=True, name="FundingOICollector")
        t.start()
        logger.info(f"[INIT] Funding/OI collector started (every {interval}s)")

    def _run_health_check(self):
        """Startup symbol health check: validate precision, connectivity, leverage caps."""
        logger.info("=" * 60)
        logger.info("SYMBOL HEALTH CHECK")
        logger.info("=" * 60)
        specs = get_all_symbol_specs()
        healthy = 0
        total = len(DEFAULT_SYMBOLS)

        for symbol, sym_cfg in DEFAULT_SYMBOLS.items():
            spec = specs.get(symbol, {})
            price_dp = spec.get("price", 2)
            qty_dp = spec.get("qty", 4)
            min_q = spec.get("min_qty", 0.01)
            tick = spec.get("tick_size", 0.01)
            max_lev = spec.get("max_leverage", 25)

            # Try to fetch a ticker price
            price = self.fetcher.latest_price(symbol, sym_cfg.coingecko_id)
            if price and price > 0:
                status = "OK"
                healthy += 1
            else:
                status = "NO DATA"

            logger.info(
                f"  {symbol:10s} | {status:7s} | "
                f"price={f'${price:,.{price_dp}f}' if price else 'N/A':>16s} | "
                f"tick={tick} | qty_dp={qty_dp} min_qty={min_q} | "
                f"max_lev={max_lev}x | tier={sym_cfg.risk_tier}"
            )

            # Cache the price for fill validation
            if price and price > 0:
                self._last_prices[symbol] = price

        logger.info(f"Health: {healthy}/{total} symbols reachable")
        logger.info("=" * 60)

    def _reconcile_exchange_positions(self):
        """Full auto-recovery: load persisted state, reconcile with exchange."""
        try:
            recovery_result = startup_recovery(
                pos_mgr=self.pos_mgr,
                exchanges=self.fetcher._exchanges,
                last_prices=self._last_prices,
                risk_mgr=self.risk_mgr,
            )
            self._skip_stale_signals = recovery_result.get("skip_stale_signals", False)
            total = (
                recovery_result.get("positions_loaded_from_disk", 0)
                + recovery_result.get("positions_reconciled_from_exchange", 0)
            )
            if total > 0:
                downtime_s = recovery_result.get("downtime_seconds", 0)
                phantoms = recovery_result.get("phantoms_closed", [])
                self.alerts.send_market_update(
                    f"[STARTUP] Auto-recovery: {total} position(s) restored "
                    f"(downtime: {downtime_s:.0f}s, "
                    f"phantoms: {len(phantoms)})"
                )
                # Enhanced Telegram restart alert
                try:
                    self.telegram_bridge.send_bot_restart(
                        downtime_seconds=downtime_s,
                        positions_reconciled=total,
                        phantoms_closed=len(phantoms),
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Auto-recovery failed, falling back to basic reconciliation: {e}")
            # Fallback to original reconciliation
            try:
                count = reconcile_positions(
                    pos_mgr=self.pos_mgr,
                    exchanges=self.fetcher._exchanges,
                    last_prices=self._last_prices,
                    risk_mgr=self.risk_mgr,
                )
                if count > 0:
                    self.alerts.send_market_update(
                        f"[STARTUP] Reconciled {count} open position(s) from Hyperliquid"
                    )
            except Exception as e2:
                logger.warning(f"Position reconciliation also failed: {e2}")

    def run(self):
        """Main run loop."""
        logger.info("=" * 60)
        logger.info(f"Multi-Strategy Bot Starting")
        logger.info(f"  Environment: {self.config.environment}")
        logger.info(f"  Symbols: {len(DEFAULT_SYMBOLS)} ({', '.join(DEFAULT_SYMBOLS.keys())})")
        logger.info(f"  Strategies: {[s.name for s in self.strategies]}")
        logger.info(f"  Ensemble mode: {self.config.ensemble_mode} (min_votes={self.config.min_votes_required})")
        logger.info(f"  Leverage: {'enabled' if self.config.enable_leverage else 'disabled'} (max={self.config.max_leverage}x)")
        logger.info(f"  ML: {'enabled' if self.config.enable_ml else 'disabled'}")
        logger.info(f"  Trailing stop: {'enabled' if self.config.enable_trailing_stop else 'disabled'}")
        logger.info(f"  Scan interval: {self.config.scan_interval_s}s")
        logger.info(f"  Max positions: {self.config.max_open_positions}")
        logger.info(f"  Risk per trade: {self.config.risk_per_trade:.1%}")
        logger.info(f"  LLM meta-brain: {self.llm_mode.name} ({describe_mode(self.llm_mode)})")
        _llm_first = getattr(self.config, 'llm_first_mode', False)
        _llm_dual = getattr(self.config, 'llm_first_dual_track', False)
        if _llm_first:
            _multi = os.getenv("LLM_MULTI_AGENT", "false").lower() == "true"
            # Accept CLI routing (USE_CLI_LLM=true) as equivalent to having an API key
            _has_llm = (
                bool(os.getenv("ANTHROPIC_API_KEY", ""))
                or os.getenv("USE_CLI_LLM", "").lower() in ("1", "true", "yes")
            )
            _mode_ok = self.llm_mode >= LLMMode.SIZING
            if _multi and _mode_ok and _has_llm:
                _llm_src = "CLI" if os.getenv("USE_CLI_LLM", "").lower() in ("1", "true", "yes") else "API"
                logger.info(f"  LLM-FIRST: ACTIVE — brain before gates (9 agents, {_llm_src})")
            else:
                _reasons = []
                if not _multi: _reasons.append("LLM_MULTI_AGENT=false")
                if not _mode_ok: _reasons.append(f"LLM_MODE={self.llm_mode.value} < SIZING(3)")
                if not _has_llm: _reasons.append("no ANTHROPIC_API_KEY or USE_CLI_LLM")
                logger.warning(
                    f"  LLM-FIRST: DISABLED — prerequisites not met: "
                    f"{', '.join(_reasons)}. Falling back to mechanical path."
                )
                self.config.llm_first_mode = False
                # WAVE2B L1 (R5, FULL_PIPE_BUILD_MAP): the silent flip to full
                # mechanical is gone. Degradation is LOUD (alert + heartbeat
                # flag llm_first_degraded=true) and mechanical EXECUTION now
                # requires ALLOW_MECHANICAL_FALLBACK=true — the exact moment
                # llm_first lapses is the moment the legacy opinion gates used
                # to take over unwatched (see R4 deletion in handle_symbol).
                self.llm_first_degraded = True
                _allow_mech = os.getenv("ALLOW_MECHANICAL_FALLBACK", "false").lower() == "true"
                if _allow_mech:
                    logger.warning(
                        "  LLM-FIRST DEGRADED → mechanical execution ENABLED "
                        "(ALLOW_MECHANICAL_FALLBACK=true)"
                    )
                else:
                    logger.warning(
                        "  LLM-FIRST DEGRADED → mechanical path will annotate/track "
                        "signals but NOT execute. Set ALLOW_MECHANICAL_FALLBACK=true "
                        "to trade without the brain."
                    )
                try:
                    self.alerts.send_market_update(
                        f"⚠️ LLM-FIRST DEGRADED at startup: {', '.join(_reasons)} — "
                        f"mechanical execution "
                        f"{'ENABLED (ALLOW_MECHANICAL_FALLBACK=true)' if _allow_mech else 'BLOCKED (annotate-only)'}"
                    )
                except Exception:
                    pass
        elif _llm_dual:
            logger.info(f"  LLM-FIRST DUAL-TRACK: logging LLM vs mechanical divergence")
        logger.info(f"  Signal monitor: {len(self.signal_monitor.channel_ids)} channels configured")
        logger.info("=" * 60)

        # Startup health check
        self._run_health_check()

        # Position reconciliation: restore open positions from exchange
        self._reconcile_exchange_positions()

        # Restore circuit breaker state from disk (survives restarts during drawdowns)
        try:
            restore_circuit_breaker_state(self.risk_mgr.circuit_breaker)
        except Exception as e:
            logger.debug(f"CB state restore skipped: {e}")

        # Auto-seed LLM memory if empty (first run or fresh install)
        try:
            import json as _json
            _mem_path = os.path.join("data", "llm", "llm_memory.json")
            _needs_seed = True
            if os.path.exists(_mem_path):
                with open(_mem_path) as _f:
                    _mem = _json.load(_f)
                _needs_seed = len(_mem.get("notes", [])) < 5
            if _needs_seed:
                from llm.memory_seeder import seed_memory
                seed_memory()
                logger.info("[INIT] LLM memory auto-seeded with 18 findings")
        except Exception as e:
            logger.debug(f"[INIT] Memory seed skipped: {e}")

        if self.config.auto_trade:
            logger.warning("AUTO-TRADING ENABLED - REAL MONEY MODE")
            logger.warning("Starting in 5 seconds... Press CTRL+C to abort")
            time.sleep(5)

        # Start Telegram command bot
        self.telegram_bot.start()

        # Start signal ingestion pipeline
        self.signal_monitor.start()

        # Start watchdog (background health monitoring)
        self.watchdog.start()

        # Start heartbeat daemon: decouples liveness reporting from the scan
        # loop so data/heartbeat.json never goes stale during a multi-minute
        # scan. ON by default — it only improves liveness reporting and cannot
        # affect trading. Seed the snapshot so the first daemon write is valid.
        self._update_hb_snapshot()
        if self._heartbeat_daemon is None or not self._heartbeat_daemon.is_alive():
            self._heartbeat_daemon = threading.Thread(
                target=self._run_heartbeat_daemon,
                daemon=True,
                name="heartbeat-daemon",
            )
            self._heartbeat_daemon.start()

        log_health_event("BOT_START", "INFO", f"Bot started: {len(DEFAULT_SYMBOLS)} symbols, LLM={self.llm_mode.name}")

        # Auto-start live_analyst as background subprocess when committee gate is enabled
        # Writes thesis files to web/public/thesis/{symbol}/ that committee_reader.py reads
        if os.getenv("COMMITTEE_GATE_ENABLED", "").lower() in ("1", "true", "yes"):
            try:
                import subprocess, sys
                _analyst_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "tools", "live_analyst.py"
                )
                if os.path.exists(_analyst_script):
                    _pid_file = os.path.join("data", "live_analyst.pid")
                    _already_running = False
                    if os.path.exists(_pid_file):
                        try:
                            _pid = int(open(_pid_file).read().strip())
                            os.kill(_pid, 0)  # raises if dead
                            _already_running = True
                        except Exception:
                            pass
                    if not _already_running:
                        _proc = subprocess.Popen(
                            [sys.executable, _analyst_script, "--loop", "--interval", "600"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                        )
                        with open(_pid_file, 'w') as _pf:
                            _pf.write(str(_proc.pid))
                        logger.info(f"[INIT] live_analyst started (pid={_proc.pid}, committee gate active)")
                    else:
                        logger.info(f"[INIT] live_analyst already running (committee gate active)")
            except Exception as _ae:
                logger.debug(f"[INIT] live_analyst auto-start: {_ae}")

        # Start web dashboard (background HTTP server)
        if self.dashboard:
            try:
                self.dashboard.start(bot_instance=self)
                logger.info(f"[INIT] Web dashboard started on port {self.config.dashboard_port}")
            except Exception as e:
                logger.warning(f"[INIT] Dashboard start failed: {e}")

        # Start Bot Perception System (TIER 5: async perception capture)
        self._start_perception_capture()

        # Start exit-regret scorer (measurement-only background loop)
        self._start_exit_regret_scorer()

        # Start funding/OI collector (measurement-only; revives the dead perception time-series)
        self._start_funding_oi_collector()

        # Start HTTP health endpoint (container/orchestrator readiness probes)
        try:
            from monitoring.health_server import start_health_server
            start_health_server(
                health_monitor=self.health_monitor,
                port=self.config.health_port,
                extra_status_fn=lambda: {
                    "tick": self._tick,
                    "open_positions": self.pos_mgr.get_open_count(),
                    "equity": self.risk_mgr.equity,
                    "circuit_breaker": "tripped" if self.risk_mgr.circuit_breaker.tripped else "ok",
                },
            )
            logger.info(f"[INIT] Health server started on port {self.config.health_port}")
        except Exception as e:
            logger.warning(f"[INIT] Health server start failed: {e}")

        # Go-live gate: evaluate 5 deployment criteria
        try:
            from validation.go_live_gate import GoLiveGate
            self._go_live_gate = GoLiveGate(
                trade_ledger=self.trade_ledger,
                ic_tracker=self.ic_tracker,
                circuit_breaker=self.risk_mgr.circuit_breaker if hasattr(self.risk_mgr, 'circuit_breaker') else None,
            )
            gate_result = self._go_live_gate.evaluate()
            gate_text = self._go_live_gate.format_report(gate_result)
            logger.info(f"\n{gate_text}")
            if not gate_result["passed"] and os.environ.get("ENVIRONMENT") == "production":
                logger.critical("GO-LIVE GATE FAILED — aborting live session")
                raise SystemExit("Go-live gate not passed. Run in paper mode first.")
            elif not gate_result["passed"]:
                logger.warning("Go-live gate not passed — running in paper/backtest mode despite failures")
        except SystemExit:
            raise
        except Exception as e:
            logger.warning(f"[INIT] Go-live gate evaluation error: {e}")
            self._go_live_gate = None

        # Signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        _consecutive_failures = 0
        _MAX_CONSECUTIVE_FAILURES = 3

        while not self.stop_event.is_set():
            try:
                self._tick_once()
                _consecutive_failures = 0  # Reset on success
            except Exception as e:
                _consecutive_failures += 1
                logger.error(
                    f"Error in main loop (failure {_consecutive_failures}/{_MAX_CONSECUTIVE_FAILURES}): {e}",
                    exc_info=True,
                )
                self.watchdog.record_error()

                # Save heartbeat with error status so external watchdog sees it.
                # Route through the shared atomic writer so this error-path write
                # shares the same lock + atomic replace as the daemon/main-loop and
                # can never tear the JSON.
                try:
                    from monitoring.health import write_heartbeat_atomic
                    write_heartbeat_atomic({
                        "last_alive": datetime.now(timezone.utc).isoformat(),
                        "pid": os.getpid(),
                        "status": "error",
                        "error": str(e)[:200],
                        "consecutive_failures": _consecutive_failures,
                    })
                except Exception:
                    pass

                # After N consecutive tick failures, trigger graceful shutdown
                if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    logger.critical(
                        f"FATAL: {_MAX_CONSECUTIVE_FAILURES} consecutive tick failures. "
                        f"Initiating graceful shutdown to prevent crash loop."
                    )
                    log_health_event(
                        "FATAL_SHUTDOWN", "CRITICAL",
                        f"{_MAX_CONSECUTIVE_FAILURES} consecutive tick failures: {e}"
                    )
                    # Save position state before shutdown
                    try:
                        save_position_state(self.pos_mgr)
                        logger.info("[SHUTDOWN] Position state saved")
                    except Exception as _pe:
                        logger.error(f"[SHUTDOWN] Failed to save position state: {_pe}")
                    # Alert via Telegram
                    if self.alerts:
                        try:
                            self.alerts.send_trade_event(
                                "EMERGENCY", "ALL",
                                f"Bot shutting down: {_MAX_CONSECUTIVE_FAILURES} consecutive failures.\n"
                                f"Last error: {str(e)[:200]}\n"
                                f"Open positions: {self.pos_mgr.get_open_count()}"
                            )
                        except Exception:
                            pass
                    self.stop_event.set()
                    break

            self._tick += 1

            # Check for graceful restart request (written by other terminals/tools)
            if self._tick % 5 == 0:  # Check every 5th tick (~2-3 min)
                _restart_file = os.path.join("data", ".restart_requested")
                if os.path.exists(_restart_file):
                    try:
                        with open(_restart_file, "r") as _rf:
                            _reason = _rf.read().strip()[:200]
                        os.remove(_restart_file)
                        logger.info(f"[RESTART] Graceful restart requested: {_reason}")
                        if self.alerts:
                            self.alerts.send_trade_alert(
                                f"BOT RESTARTING: {_reason}"
                            )
                        self.stop_event.set()
                    except Exception as _re:
                        logger.error(f"[RESTART] Error processing restart file: {_re}")

            # Periodic paper trading summary (every 60 ticks ≈ 6 hours)
            if self._tick % 60 == 0:
                try:
                    self._log_periodic_summary()
                except Exception as e:
                    logger.debug(f"Periodic summary error: {e}")

            # Paper trading hourly checkpoint
            if self.paper_validator is not None:
                _now = time.time()
                if _now - self._paper_checkpoint_last >= 3600:
                    try:
                        self.paper_validator.run_checkpoint()
                        self._paper_checkpoint_last = _now
                    except Exception as e:
                        logger.debug(f"Paper validator checkpoint error: {e}")

            self._sleep_interruptible(self._adaptive_scan_interval())

        # Final session summary on shutdown
        try:
            self._log_periodic_summary(final=True)
        except Exception:
            pass

        self.watchdog.stop()
        log_health_event("BOT_STOP", "INFO", f"Bot stopped gracefully after {self._tick} ticks")
        logger.info("Bot stopped gracefully")

    def _handle_signal(self, signum, frame):
        logger.info(f"Received signal {signum}, stopping...")
        # Cancel all pending orders before shutdown to prevent unmanaged fills
        try:
            pending = self.pending_orders.get_pending()
            if pending:
                logger.info(f"[SHUTDOWN] Cancelling {len(pending)} pending orders...")
                self.pending_orders.cancel_all(reason="shutdown")
                # In live mode, also cancel on exchange
                if self.order_executor.mode == "live" and self.order_executor.exchange:
                    try:
                        self.order_executor.exchange.cancel_all_orders()
                        logger.info("[SHUTDOWN] Exchange orders cancelled")
                    except Exception as e:
                        logger.error(f"[SHUTDOWN] Failed to cancel exchange orders: {e}")
        except Exception as e:
            logger.error(f"[SHUTDOWN] Error cancelling pending orders: {e}")
        self.stop_event.set()

    def _adaptive_scan_interval(self) -> float:
        """Compute scan interval based on market conditions.

        Volatile/active markets → scan faster (15s) to catch moves.
        Calm/range markets → scan slower (45s) to save compute.
        Open positions → scan faster for exit monitoring.
        """
        base = self.config.scan_interval_s  # default 30s

        # Check dominant regime across tracked symbols
        regimes = []
        for symbol in DEFAULT_SYMBOLS:
            try:
                r = self._tick_regime_cache.get(symbol) or self.regime_detector.get_regime(symbol)
                regimes.append(r)
            except Exception:
                pass

        has_open = self.pos_mgr.get_open_count() > 0
        cb_active = self.risk_mgr.circuit_breaker.tripped

        # Fast scan: volatile, panic, or active positions
        if any(r in ("panic", "news_dislocation") for r in regimes):
            return max(10, base * 0.5)  # 15s — urgent
        if any(r in ("high_volatility",) for r in regimes):
            return max(15, base * 0.65)  # 20s — volatile
        if has_open:
            return max(20, base * 0.75)  # 22s — monitoring positions
        if cb_active:
            return max(15, base * 0.5)  # 15s — recovery mode

        # Slow scan: calm markets, no positions
        if all(r in ("range", "low_liquidity", "unknown") for r in regimes) and not has_open:
            return min(60, base * 1.5)  # 45s — nothing happening

        return base  # 30s default

    def _sleep_interruptible(self, seconds: float):
        step = 0.5
        waited = 0.0
        while waited < seconds and not self.stop_event.is_set():
            time.sleep(min(step, seconds - waited))
            waited += step

    def _update_hb_snapshot(self):
        """Cheaply refresh the liveness snapshot read by the heartbeat daemon.

        Reads positions/equity (in-memory, no I/O) and stores them under a lock
        so the daemon thread can write an accurate data/heartbeat.json without
        racing the main loop or doing expensive work itself.
        """
        try:
            positions = self.pos_mgr.get_open_count()
        except Exception:
            positions = 0
        try:
            equity = self.risk_mgr.equity
        except Exception:
            equity = 0.0
        try:
            exchange_healthy = not self.degradation.should_halt_entries()
        except Exception:
            exchange_healthy = True
        with self._hb_snapshot_lock:
            self._hb_snapshot = {
                "positions": positions,
                "equity": equity,
                "exchange_healthy": exchange_healthy,
            }

    def _run_heartbeat_daemon(self):
        """Background daemon that keeps liveness reporting decoupled from the
        scan loop.

        The scan loop only writes data/heartbeat.json at cycle boundaries, so
        during a multi-minute scan the file goes stale and the external watchdog
        false-positives a 'stall' -> exit-code-1 restart even though the bot is
        fine. This daemon writes the heartbeat every HEARTBEAT_DAEMON_INTERVAL_S
        from the cheap snapshot the main loop refreshes each tick, so last_alive
        never goes stale mid-scan. If the process genuinely wedges, this thread
        stops too and the file legitimately goes stale (no masking of real hangs).
        """
        try:
            interval = max(5, int(os.getenv("HEARTBEAT_DAEMON_INTERVAL_S", "30")))
        except (TypeError, ValueError):
            interval = 30
        logger.info(f"[HEARTBEAT-DAEMON] Started (interval={interval}s)")
        while not self.stop_event.is_set():
            try:
                with self._hb_snapshot_lock:
                    snap = dict(self._hb_snapshot)
                # Write data/heartbeat.json (read by external watchdog.py).
                # extra marks this as a daemon write so loop_duration_s/scan_count
                # stats remain owned by the end-of-cycle record at the loop.
                self.health_monitor.record_heartbeat(
                    loop_duration_s=0.0,
                    positions=int(snap.get("positions", 0)),
                    equity=float(snap.get("equity", 0.0)),
                    extra={
                        "source": "heartbeat_daemon",
                        # WAVE2B L1 (R5): loud degradation — watchdog/owner can
                        # see when llm_first lapsed to mechanical at startup.
                        "llm_first_degraded": bool(getattr(self, "llm_first_degraded", False)),
                    },
                )
                # Also poke the in-process watchdog so its stall timer resets.
                self.watchdog.heartbeat(
                    equity=float(snap.get("equity", 0.0)),
                    scan_count=self._tick,
                    exchange_healthy=bool(snap.get("exchange_healthy", True)),
                )
                # ── D6b (2026-07-02): one-equity-truth convergence sentinel ──
                # CB, sizing, heartbeat and persistence all read risk_mgr.equity;
                # the persisted file can only diverge if an external writer
                # clobbers it (2026-07-01: pytest wrote $2318.91/peak=10000 over
                # the real $1951.01 and the next restart trusted it). Detect >1%
                # drift, WARN (throttled 10min), and re-persist the in-memory
                # truth so a poisoned file can never silently feed a restart.
                try:
                    _eq_mem = float(snap.get("equity", 0.0))
                    if _eq_mem > 0:
                        with open(os.path.join("data", "risk_equity_state.json"),
                                  encoding="utf-8") as _ef:
                            _eq_disk = float((json.load(_ef) or {}).get("equity", 0.0))
                        if _eq_disk > 0 and abs(_eq_disk - _eq_mem) / _eq_mem > 0.01:
                            _now_ts = time.time()
                            if _now_ts - getattr(self, "_equity_drift_last_warn", 0.0) > 600:
                                self._equity_drift_last_warn = _now_ts
                                logger.warning(
                                    f"[EQUITY-DRIFT] persisted ${_eq_disk:.2f} vs in-memory "
                                    f"${_eq_mem:.2f} ({(_eq_disk - _eq_mem) / _eq_mem:+.1%}) — "
                                    f"external writer clobbered risk_equity_state.json? "
                                    f"Re-persisting in-memory truth."
                                )
                                self.risk_mgr.save_equity_state()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"[HEARTBEAT-DAEMON] write error: {e}")
            # Interruptible wait so shutdown is prompt.
            self.stop_event.wait(interval)
        logger.info("[HEARTBEAT-DAEMON] Stopped")

    def _tick_once(self):
        """One iteration of the main loop."""
        trace_id = uuid.uuid4().hex[:8]
        _loop_start = time.time()

        # Refresh the cheap liveness snapshot for the heartbeat daemon at the
        # START of the tick (before the multi-minute scan) so the daemon can
        # report fresh positions/equity even while this scan is in progress.
        self._update_hb_snapshot()

        # Per-trade compound sizing cache: stored at entry, read at close for attribution
        self._compound_mult_cache: Dict[str, float] = {}

        # Walk-forward degradation: auto-reduce sizing when OOS performance degrades
        self._wf_ratio: float = 1.0  # Default: no degradation
        self._wf_last_computed: float = 0.0  # Epoch of last computation

        # Per-tick regime cache: computed once, reused by all subsystems
        # (avoids 5x redundant get_regime() calls per symbol per tick)
        self._tick_regime_cache = {}
        for sym in DEFAULT_SYMBOLS:
            try:
                self._tick_regime_cache[sym] = self.regime_detector.get_regime(sym)
            except Exception:
                self._tick_regime_cache[sym] = "unknown"

        # ── BACKGROUND THINKER: periodic rule-based analysis (no LLM calls) ──
        if self._background_thinker and self._background_thinker.should_think():
            try:
                _bt_market_data = {}
                for _bt_sym in DEFAULT_SYMBOLS:
                    _bt_price = self._last_prices.get(_bt_sym)
                    if _bt_price:
                        _bt_market_data[_bt_sym] = {
                            "price": _bt_price,
                            "regime": self._tick_regime_cache.get(_bt_sym, "unknown"),
                            "funding_rate": self._last_funding_rates.get(_bt_sym),
                            "oi": self._last_open_interest.get(_bt_sym),
                        }
                _bt_observations = self._background_thinker.think(
                    market_data=_bt_market_data,
                    positions=self.pos_mgr.get_open_positions(),
                    recent_trades=get_recent_trades(10),
                    feedback_state=None,
                )
                if _bt_observations:
                    _bt_total = sum(
                        len(_bt_observations.get(k, []))
                        for k in ("market_changes", "position_reviews", "opportunities", "patterns")
                    )
                    if _bt_total > 0:
                        logger.info(f"[{trace_id}][BACKGROUND] Thought cycle #{_bt_observations.get('cycle', 0)}: {_bt_total} observations")
            except Exception as _bt_err:
                logger.debug(f"[{trace_id}] Background thinker error: {_bt_err}")

        # ── PARALLEL PREFETCH: fetch all symbols' data concurrently ──
        # This front-loads all exchange I/O so _process_symbol hits cache.
        _prefetch_start = time.time()
        prefetch_results = self.fetcher.prefetch_all_symbols(DEFAULT_SYMBOLS, self._needed_tfs)
        _prefetch_ms = (time.time() - _prefetch_start) * 1000
        # Track prefetch failures for degradation awareness
        prefetch_failures = sum(1 for v in prefetch_results.values() if not v)
        prefetch_total = len(prefetch_results)
        if prefetch_failures > 0:
            logger.warning(
                f"[{trace_id}] Prefetch: {prefetch_failures}/{prefetch_total} symbols failed"
            )
            if prefetch_failures == prefetch_total:
                logger.error(f"[{trace_id}] ALL prefetches failed — exchange may be down")
                self.degradation.record_exchange_error()
        else:
            logger.info(f"[{trace_id}] Prefetch done: {prefetch_total} symbols in {_prefetch_ms:.0f}ms")

        # Collect candidate signals for rotation evaluation
        self._tick_candidates: list = []

        # Smart symbol evaluation priority: evaluate symbols most likely
        # to produce actionable signals first (lead-lag targets, volatile, open positions)
        eval_order = self._prioritize_symbols(DEFAULT_SYMBOLS)

        # Update ensemble confidence floor from adaptive floor (dynamic gating).
        # OVERDRIVE/LLM_FIRST_MODE: cap adaptive floor at the configured value -- the
        # adaptive module hardcodes ABSOLUTE_MIN_FLOOR=50 in feedback/adaptive_confidence.py,
        # which silently overrode ENSEMBLE_CONFIDENCE_FLOOR=20 every scan, gating off
        # signals like ETH BUY conf=52% that LLM said GO on. Adaptive can lower the floor
        # in LLM-first mode but cannot raise it above the user's configured baseline.
        try:
            if self.confidence_floor and hasattr(self.ensemble, 'confidence_floor'):
                new_floor = self.confidence_floor.current_floor
                llm_first = os.getenv("LLM_FIRST_MODE", "false").lower() == "true"
                if llm_first:
                    new_floor = min(new_floor, self.config.ensemble_confidence_floor)
                self.ensemble.confidence_floor = new_floor
                # Log if floor changed significantly
                if abs(new_floor - self.config.ensemble_confidence_floor) > 2.0:
                    logger.info(
                        f"[ADAPTIVE-FLOOR] Updated ensemble confidence floor from "
                        f"{self.config.ensemble_confidence_floor:.1f} to {new_floor:.1f}"
                    )
        except Exception as e:
            logger.debug(f"Failed to update adaptive confidence floor: {e}")

        # ── Symbol evaluation: serial (default) or bounded-parallel ──
        # SCAN_PARALLEL_SYMBOLS runs each symbol's full Regime->Quant->Trade->
        # Risk->Critic chain in its own worker thread so wall-clock collapses
        # from sum(per-symbol) to ceil(N/K) of per-symbol time. Each symbol keeps
        # its OWN serial intra-symbol pipeline and its own thread-local
        # scratchpad, so directional decisions are byte-for-byte identical to
        # the serial path; only cross-symbol ordering/wall-clock changes. The
        # global Sonnet semaphore in the coordinator caps concurrent Sonnet/Opus
        # CLI sessions independent of K, so quota cannot be exceeded.
        _parallel = os.getenv("SCAN_PARALLEL_SYMBOLS", "false").lower() in ("1", "true", "yes", "on")
        if _parallel and len(eval_order) > 1:
            try:
                _k = int(os.getenv("SCAN_MAX_CONCURRENCY", "2"))
            except (TypeError, ValueError):
                _k = 2
            # Hard cap at 3 — never let a config typo open the floodgates.
            _k = max(1, min(_k, 3, len(eval_order)))
            logger.info(
                f"[{trace_id}] Parallel symbol scan: {len(eval_order)} symbols, "
                f"concurrency={_k}"
            )
            with ThreadPoolExecutor(max_workers=_k, thread_name_prefix="symscan") as _pool:
                _futs = {
                    _pool.submit(self._process_symbol, _sym, _cfg, trace_id): _sym
                    for _sym, _cfg in eval_order
                }
                for _fut in as_completed(_futs):
                    _sym = _futs[_fut]
                    try:
                        _fut.result()
                    except Exception as e:
                        logger.error(f"[{trace_id}][{_sym}] Error: {e}", exc_info=True)
                        self.health_monitor.record_error()
        else:
            for symbol, sym_cfg in eval_order:
                try:
                    self._process_symbol(symbol, sym_cfg, trace_id)
                except Exception as e:
                    logger.error(f"[{trace_id}][{symbol}] Error: {e}", exc_info=True)
                    self.health_monitor.record_error()

        # ── Pending limit order fills ──
        # Check if any pending orders should fill at current prices
        if self.pending_orders.get_pending():
            try:
                _current_prices = {}
                for sym in DEFAULT_SYMBOLS:
                    _p = self.fetcher.fetch_live_price(sym)
                    if _p:
                        _current_prices[sym] = _p
                filled_orders = self.pending_orders.check_fills(_current_prices)
                for filled in filled_orders:
                    self._execute_pending_fill(filled, trace_id)
            except Exception as e:
                logger.warning(f"[{trace_id}] Pending order check error: {e}")

        # Signal Value Tracker: update all tracked signals with current prices
        if hasattr(self, '_signal_tracker') and self._signal_tracker is not None:
            if hasattr(self, '_last_prices') and self._last_prices:
                try:
                    self._signal_tracker.update_prices(self._last_prices)
                except Exception:
                    pass

        # Sniper Simulator: check sim positions against live prices
        if self._sniper_simulator is not None and hasattr(self, '_last_prices') and self._last_prices:
            try:
                _sim_closed = self._sniper_simulator.check_positions(self._last_prices)
                for _st in (_sim_closed or []):
                    logger.info(
                        f"[SIM] CLOSED {_st.trade_id} {_st.symbol} {_st.side} "
                        f"{_st.exit_reason} @ ${_st.exit_price:.2f} "
                        f"PnL=${_st.pnl_usd:+.2f} ({_st.pnl_pct:+.1f}%) "
                        f"hold={_st.hold_time_hours:.1f}h | "
                        f"Sim equity=${self._sniper_simulator._equity:.2f}"
                    )
            except Exception as _sim_err:
                logger.warning(f"[SIM] Error checking positions: {_sim_err}")

        # ── Anticipatory Entry Engine: scan for setups + check pending triggers ──
        if self._anticipation_engine is not None and hasattr(self, '_last_prices') and self._last_prices:
            try:
                # 1. Scan all symbols for new anticipatory setups
                _default_syms = getattr(self, '_default_symbols', None)
                if _default_syms is None:
                    from trading_config import DEFAULT_SYMBOLS as _default_syms
                for _ant_sym in self.config.symbols:
                    _ant_cfg = _default_syms.get(_ant_sym) if _default_syms else None
                    if _ant_cfg:
                        _ant_1h = None
                        _ant_5m = None
                        try:
                            _ant_1h = self.fetcher.fetch_ohlcv(_ant_sym, _ant_cfg.coingecko_id, "1h")
                        except Exception:
                            pass
                        try:
                            _ant_5m = self.fetcher.fetch_ohlcv(_ant_sym, _ant_cfg.coingecko_id, "5m")
                        except Exception:
                            pass
                        if _ant_1h is not None and not _ant_1h.empty:
                            _new_entries = self._anticipation_engine.scan_for_setups(_ant_sym, _ant_1h, _ant_5m)
                            # Fire WATCH alerts for newly-staged anticipatory entries (2026-04-16)
                            # These are setups the engine expects to trigger soon. User
                            # gets advance notice so they're ready when EXECUTE fires.
                            if (_new_entries
                                    and os.environ.get("PREMIUM_ALERTS_ENABLED", "true").lower() in ("1", "true", "yes")
                                    and self.alerts
                                    and self.alerts.telegram_token
                                    and self.alerts.telegram_chat_id):
                                try:
                                    from alerts.premium_filter import (
                                        evaluate_for_alert, AlertTier,
                                        is_watch_deduped, mark_watch_sent,
                                    )
                                    from alerts.premium_telegram import format_premium_watch_alert
                                    for _ne in _new_entries:
                                        _ne_side = "BUY" if _ne.side == "BUY" else "SELL"
                                        _ne_strategy = _ne.setup_type or "anticipatory"
                                        # Dedup: skip if same (symbol, side, strategy) WATCH
                                        # was sent in the last 30 min.
                                        if is_watch_deduped(_ne.symbol, _ne_side, _ne_strategy):
                                            continue
                                        _ne_dec = evaluate_for_alert(
                                            symbol=_ne.symbol, side=_ne_side,
                                            strategy=_ne_strategy,
                                            confidence=70.0,  # anticipatory defaults
                                            num_agree=1,
                                            regime="",
                                            entry=_ne.target_price,
                                            sl=_ne.sl, tp1=_ne.tp, tp2=_ne.tp,
                                            leverage=_ne.leverage,
                                            equity=self.risk_mgr.equity,
                                            anticipatory_prestage=True,
                                        )
                                        if _ne_dec.tier == AlertTier.WATCH:
                                            _watch_msg = format_premium_watch_alert(
                                                symbol=_ne.symbol, side=_ne_side,
                                                entry=_ne.target_price,
                                                sl=_ne.sl, tp1=_ne.tp, tp2=_ne.tp,
                                                leverage=_ne.leverage,
                                                confidence=70.0,
                                                decision=_ne_dec,
                                                strategy=_ne_strategy,
                                                regime="", num_agree=1, total_strategies=0,
                                            )
                                            self.alerts._send_telegram(_watch_msg)
                                            mark_watch_sent(_ne.symbol, _ne_side, _ne_strategy)
                                            logger.info(
                                                f"[WATCH-ALERT] Sent for {_ne.symbol} {_ne_side} "
                                                f"@ ${_ne.target_price:.2f} ({_ne_strategy})"
                                            )
                                except Exception as _we:
                                    logger.debug(f"Watch alert dispatch failed: {_we}")

                # 2. Build indicators dict for trigger checking
                _ant_indicators = {}
                for _ant_sym in self.config.symbols:
                    _ant_cfg = _default_syms.get(_ant_sym) if _default_syms else None
                    if _ant_cfg:
                        try:
                            _ant_df = self.fetcher.fetch_ohlcv(_ant_sym, _ant_cfg.coingecko_id, "1h")
                            if _ant_df is not None and not _ant_df.empty:
                                from manual.anticipatory_entries import _compute_indicators
                                _ant_indicators[_ant_sym] = _compute_indicators(_ant_df)
                        except Exception:
                            pass

                # 3. Check pending entries for triggers
                _ant_triggered = self._anticipation_engine.check_pending_entries(
                    self._last_prices, _ant_indicators
                )
                for _ant_entry in _ant_triggered:
                    try:
                        _ant_price = self._last_prices.get(_ant_entry.symbol, _ant_entry.target_price)
                        _ant_vol_ratio = _ant_indicators.get(_ant_entry.symbol, {}).get("vol_ratio", 0.0)
                        _ant_signal = self._anticipation_engine.pending_to_signal(_ant_entry, _ant_price, vol_ratio=_ant_vol_ratio)
                        if _ant_signal and _ant_signal.is_valid:
                            # Route through sniper filter
                            _ant_sniper = self._manual_sniper.evaluate(_ant_signal)
                            if _ant_sniper is not None:
                                logger.info(
                                    f"[ANTICIPATE-EXEC] {_ant_entry.symbol} {_ant_entry.side} "
                                    f"{_ant_entry.setup_type} @ ${_ant_price:.2f} "
                                    f"R:R={_ant_entry.rr_ratio:.1f} lev={_ant_entry.leverage:.0f}x"
                                )
                                if self._sniper_simulator is not None:
                                    _sim_pos = self._sniper_simulator.on_signal(_ant_sniper)
                                    if _sim_pos:
                                        logger.info(
                                            f"[SIM] Anticipatory opened {_sim_pos.trade_id} "
                                            f"{_sim_pos.symbol} {_sim_pos.side} "
                                            f"R:R={_ant_entry.rr_ratio:.1f}"
                                        )
                                # Auto-execute if enabled
                                if self._sniper_auto_execute and _ant_sniper.tier in ("SNIPER", "PREMIUM"):
                                    try:
                                        self._execute_sniper_signal(_ant_sniper, _ant_entry.symbol, _ant_price)
                                    except Exception as _sae_err:
                                        logger.error(f"[ANTICIPATE-EXEC] Error executing: {_sae_err}")
                            else:
                                logger.debug(
                                    f"[ANTICIPATE] {_ant_entry.symbol} {_ant_entry.side} "
                                    f"triggered but rejected by sniper filter"
                                )
                    except Exception as _ant_inner:
                        logger.warning(f"[ANTICIPATE] Error processing triggered entry: {_ant_inner}")

                # Log status periodically (every 10 ticks)
                if self._tick % 10 == 0:
                    _ant_status = self._anticipation_engine.get_status()
                    if _ant_status["active_pending"] > 0:
                        logger.info(
                            f"[ANTICIPATE] Status: {_ant_status['active_pending']} pending, "
                            f"{_ant_status['total_triggered']} triggered, "
                            f"{_ant_status['total_expired']} expired, "
                            f"trigger_rate={_ant_status['trigger_rate']:.0f}%"
                        )
            except Exception as _ant_err:
                logger.debug(f"[ANTICIPATE] Engine error (non-fatal): {_ant_err}")

        # ── Trade rotation evaluation ──
        # Check if any open position should be rotated into a better signal
        if self.rotation_mgr and self._tick_candidates:
            try:
                self._evaluate_rotations(trace_id)
            except Exception as e:
                logger.warning(f"[{trace_id}] Rotation evaluation error: {e}")

        # LLM meta-brain: hybrid trigger system
        # Triggers accumulated during symbol processing; also check periodic + cross-market
        if should_call_llm(self.llm_mode):
            # Check cross-market divergence (BTC vs alts)
            divergence = self._llm_triggers.check_cross_market_divergence(
                self._price_changes_1h
            )
            if divergence:
                self._llm_triggers.add(
                    LLMTrigger.CROSS_MARKET_DIVERGENCE,
                    context=divergence,
                )

            # Check lead-lag signals: a leader moved, follower hasn't responded yet
            # This proactively triggers LLM evaluation BEFORE the follower moves
            if self.cross_symbol_tracker:
                try:
                    lead_lag_signals = self.cross_symbol_tracker.get_active_signals()
                    for ll_sig in lead_lag_signals:
                        if ll_sig.get("confidence", 0) >= 0.45:
                            self._llm_triggers.add(
                                LLMTrigger.LEAD_LAG_SIGNAL,
                                context={
                                    "leader": ll_sig["leader"],
                                    "follower": ll_sig["follower"],
                                    "leader_move": ll_sig["leader_move"],
                                    "expected_follower_move": ll_sig["expected_follower_move"],
                                    "avg_lag_min": ll_sig["avg_lag_min"],
                                },
                            )
                            break  # One lead-lag trigger per tick is enough
                except Exception:
                    pass

            # Check memory-worthy events (performance shifts, streaks)
            mem_events = self._llm_triggers.check_memory_events()
            for mem_ctx in mem_events:
                self._llm_triggers.add(
                    LLMTrigger.MEMORY_EVENT,
                    context=mem_ctx,
                )

            # Periodic heartbeat DISABLED to conserve LLM credits.
            # The LLM only fires on actual signals now (PRE_TRADE triggers).
            # Re-enable when credits are not a concern.
            # if self._llm_triggers.event_count == 0:
            #     if self._llm_triggers.check_periodic():
            #         self._llm_triggers.add(LLMTrigger.PERIODIC)

            # Fire if any trigger should run
            if self._llm_triggers.should_fire():
                try:
                    self._run_llm_metabrain(trace_id)
                except Exception as e:
                    logger.warning(f"[{trace_id}] LLM meta-brain error: {e}")
                self._llm_triggers.clear()
            else:
                self._llm_triggers.clear()

        # Feedback loop: run periodic backtests and apply parameter adjustments
        try:
            self.feedback.tick()
        except Exception as e:
            logger.warning(f"[{trace_id}] Feedback loop tick error: {e}")

        # Perpetual learning engine: auto-fix, execution forensics, live injection, etc.
        try:
            master_engine = get_master_engine()
            master_engine.tick(trade_count=len(self.trade_ledger.all_trades()) if hasattr(self, 'trade_ledger') else 0)
        except Exception as e:
            logger.debug(f"[{trace_id}] Master learning engine tick error: {e}")

        # Growth intelligence: periodic learning cycles, hypothesis graduation,
        # veto resolution, auto-safe proposal application, report generation
        try:
            # Build current prices for veto resolution using actual candle high/low
            # Previously used spot price for high=low=close, breaking veto resolution
            _growth_prices = {}
            for _sym, _cfg in DEFAULT_SYMBOLS.items():
                p = self._last_prices.get(_sym)
                if p:
                    # Use 1h price range tracking if available for accurate veto resolution
                    _high = getattr(self, '_price_highs_1h', {}).get(_sym, p)
                    _low = getattr(self, '_price_lows_1h', {}).get(_sym, p)
                    _growth_prices[_sym] = {"high": _high, "low": _low, "close": p}
            self.growth.tick(
                current_prices=_growth_prices,
                market_state={"price_changes_1h": self._price_changes_1h},
            )
        except Exception as e:
            logger.debug(f"[{trace_id}] Growth tick error: {e}")

        # Learning integrator: evolution→growth bridge, weight sync, curriculum advance
        try:
            from llm.learning_integrator import get_learning_integrator
            get_learning_integrator().tick()
        except Exception as e:
            logger.debug(f"[{trace_id}] Learning integrator tick error: {e}")

        # LLM exit intelligence: evaluate open positions for dynamic SL/TP adjustments
        # Runs every 5th tick (~5 min at 60s intervals) to balance responsiveness vs cost
        self._exit_check_counter += 1
        if self._exit_check_counter >= 5:
            self._exit_check_counter = 0
            try:
                self._check_llm_exit_suggestions()
            except Exception as e:
                logger.warning(f"[{trace_id}] Exit intelligence error: {e}")

        # Scout Agent: idle-time preparation and watchlist formation
        # CLI = $0/call: drop from 120-tick (~2h) to 15-tick (~15 min) cadence
        if self._tick % 15 == 0 and os.getenv("LLM_MULTI_AGENT", "").lower() in ("1", "true", "yes"):
            try:
                self._run_scout_preparation(trace_id)
            except Exception as e:
                logger.debug(f"[{trace_id}] Scout preparation error: {e}")

        # Exit Agent: thesis-validity check on ALL open positions
        # CLI = $0/call: run every 2 ticks (~2 min) and only if positions are open
        _has_open_positions = bool(getattr(self, 'pos_mgr', None) and
                                   hasattr(self.pos_mgr, 'positions') and
                                   any(p.state not in ("CLOSED",) for p in self.pos_mgr.positions.values()))
        if (_has_open_positions
                and self._tick % 2 == 0
                and os.getenv("LLM_MULTI_AGENT", "").lower() in ("1", "true", "yes")
                and os.getenv("AGENT_EXIT_ENABLED", "true").lower() in ("1", "true", "yes")):
            try:
                self._run_exit_agent_checks(trace_id)
            except Exception as e:
                logger.debug(f"[{trace_id}] Exit agent periodic check error: {e}")

        # Overseer Agent: system-level portfolio review every 60 ticks (~1 hour)
        if (self._tick % 60 == 0
                and os.getenv("LLM_MULTI_AGENT", "").lower() in ("1", "true", "yes")
                and os.getenv("AGENT_OVERSEER_ENABLED", "true").lower() in ("1", "true", "yes")):
            try:
                self._run_overseer_review(trace_id)
            except Exception as e:
                logger.debug(f"[{trace_id}] Overseer review error: {e}")

        # Daily summary: send once per day at ~8:00 UTC (every 1440 ticks = 24h at 60s)
        # Also triggers if the bot just started and hasn't sent one today
        if self._tick % 1440 == 720:  # ~12 hours into the day
            try:
                self._send_daily_summary()
            except Exception as e:
                logger.debug(f"[{trace_id}] Daily summary error: {e}")

        # Morning briefing (2026-04-16): wall-clock-based, sends the /briefing
        # multi-window snapshot to Telegram at 08:00 UTC daily regardless of
        # tick count drift. User asked: "I want the briefing on my phone first
        # thing in the morning without typing anything."
        try:
            from datetime import datetime as _dt, timezone as _tz
            _now_utc = _dt.now(_tz.utc)
            _last_morning = getattr(self, "_last_morning_briefing_date", None)
            _today_str = _now_utc.strftime("%Y-%m-%d")
            # Fire once between 08:00 and 08:59 UTC, at most one per day
            if (_now_utc.hour == 8
                    and _last_morning != _today_str
                    and hasattr(self, "alerts") and self.alerts
                    and getattr(self.alerts, "telegram_token", None)
                    and getattr(self.alerts, "telegram_chat_id", None)):
                try:
                    # Build the briefing inline (use the same logic as /briefing
                    # command but without requiring a telegram_bot instance).
                    _briefing_msg = self._build_morning_briefing_message()
                    self.alerts._send_telegram(_briefing_msg)
                    self._last_morning_briefing_date = _today_str
                    logger.info(f"[MORNING-BRIEFING] Sent for {_today_str}")
                except Exception as _mb_err:
                    logger.debug(f"[MORNING-BRIEFING] Send error: {_mb_err}")
        except Exception:
            pass

        # Position aging alerts: flag positions held too long with adverse funding
        # Runs every 10th tick (~10 min at 60s intervals)
        if self._tick % 10 == 0:
            try:
                self._check_position_aging()
            except Exception as e:
                logger.debug(f"[{trace_id}] Position aging check error: {e}")

        # Record health monitor heartbeat every tick
        _loop_elapsed = time.time() - _loop_start
        self.health_monitor.record_heartbeat(
            loop_duration_s=_loop_elapsed,
            positions=self.pos_mgr.get_open_count(),
            equity=self.risk_mgr.equity,
        )

        # Watchdog heartbeat: report we're alive
        self.watchdog.heartbeat(
            equity=self.risk_mgr.equity,
            scan_count=self._tick,
            exchange_healthy=not self.degradation.should_halt_entries(),
        )

        # Equity snapshot to DB every 15 ticks (~15 min at 60s intervals)
        if self._tick % 15 == 0:
            try:
                log_equity(
                    equity=self.risk_mgr.equity,
                    open_positions=self.pos_mgr.get_open_count(),
                    daily_pnl=self.risk_mgr.circuit_breaker.daily_pnl,
                )
            except Exception:
                pass

        # Strategy research cycle: every 240 ticks (~4 hours at 60s intervals)
        if self._tick % 240 == 0 and self._tick > 0:
            try:
                from llm.strategy_discovery.research_agent import run_research_cycle
                notify_fn = None
                if self.alerts:
                    notify_fn = self.alerts.send_market_update
                proposals = run_research_cycle(
                    max_proposals=3,
                    notify_fn=notify_fn,
                )
                if proposals:
                    logger.info(
                        f"[{trace_id}] Research cycle: {len(proposals)} new proposals"
                    )
            except Exception as e:
                logger.debug(f"[{trace_id}] Research cycle error: {e}")

        # Self-Teaching: run learning cycle when enough trades accumulated
        # Checks every 60 ticks (~1 hour), but only runs if should_run_cycle() says yes
        if self._tick % 60 == 30 and self.teaching_engine and self.config.enable_self_teaching:
            try:
                if self.teaching_engine.should_run_cycle():
                    # Gather recent trades from deep memory for analysis
                    _teach_trades = []
                    try:
                        from llm.deep_memory import get_deep_memory
                        _dm = get_deep_memory()
                        _teach_trades = _dm.trade_dna._trades[-50:] if hasattr(_dm.trade_dna, '_trades') and _dm.trade_dna._trades else []
                    except Exception:
                        pass

                    _teach_report = self.teaching_engine.run_learning_cycle(
                        recent_trades=_teach_trades,
                        market_state={
                            "price_changes_1h": self._price_changes_1h,
                            "global_bias": self._global_bias,
                            "equity": self.risk_mgr.equity,
                        },
                    )
                    if _teach_report.get("knowledge_added"):
                        logger.info(
                            f"[TEACH] Learning cycle #{_teach_report['cycle_number']}: "
                            f"level={_teach_report['curriculum_level']}, "
                            f"+{len(_teach_report['knowledge_added'])} knowledge items"
                        )
            except Exception as e:
                logger.debug(f"[{trace_id}] Self-teaching cycle error: {e}")

        # Self-Tuning Risk: evaluate and auto-adjust risk profile every 30 ticks (~30 min)
        if self._tick % 30 == 0 and self._tick > 0:
            try:
                new_profile = risk_evaluate_and_adjust()
                if new_profile:
                    logger.info(f"[RISK-TUNE] Auto-adjusted to {new_profile} profile")
                    if self.alerts:
                        self.alerts.send_market_update(
                            f"Risk profile auto-adjusted to *{new_profile}*"
                        )
            except Exception as e:
                logger.debug(f"Risk tuning evaluation error: {e}")

        # Heartbeat every 60 ticks (~1 hour at 60s intervals)
        if self._tick % 60 == 0:
            self._send_heartbeat()
            Telemetry.save_snapshot()  # Persist telemetry for dashboards/LLM

        # Market update every 15 ticks (~15 min) - sends even without signals
        if self._tick % 15 == 0 and self._tick % 60 != 0:
            self._send_market_update(trace_id)

        # Quant brain market intel to Telegram every 30 ticks (~30 min)
        if self._tick % 30 == 0 and self._tick > 0:
            self._send_quant_intel()

        # Evolution tracker: daily strategy evolution report (~1440 ticks at 60s = 24h)
        if self._tick % 1440 == 0 and self._tick > 0:
            try:
                from feedback.evolution_tracker import EvolutionTracker
                tracker = EvolutionTracker("data")

                # Lazy-initialize AutoOptimizer with EvolutionTracker on first run
                if not self._auto_optimizer_initialized:
                    self._evolution_tracker = tracker
                    try:
                        self.feedback.setup_auto_optimizer(
                            evolution_tracker=tracker,
                            llm_client=self.llm_client if hasattr(self, 'llm_client') else None
                        )
                        self._auto_optimizer_initialized = True
                        logger.info("[INIT] AutoOptimizer initialized with EvolutionTracker")
                    except Exception as aoi_e:
                        logger.warning(f"[INIT] AutoOptimizer setup failed: {aoi_e}")

                report = tracker.generate_report()
                if self.alerts and report:
                    # Enhanced daily report with full breakdown
                    _dr_ds = get_daily_summary()
                    _dr_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    try:
                        _dr_msg = format_daily_report_telegram(
                            date=_dr_date,
                            total_trades=report.total_trades,
                            wins=_dr_ds.get("wins", 0),
                            losses=_dr_ds.get("losses", 0),
                            net_pnl=report.net_pnl,
                            equity=self.risk_mgr.equity,
                            by_strategy=_dr_ds.get("by_strategy"),
                        )
                        self.alerts.send_market_update(_dr_msg)
                        self.alerts.send_telegram_important(_dr_msg)
                    except Exception:
                        # Fallback to simple
                        summary = (
                            f"*Daily Evolution Report*\n"
                            f"Trades: {report.total_trades}\n"
                            f"Win rate: {report.win_rate:.1%}\n"
                            f"Net PnL: ${report.net_pnl:+.2f}"
                        )
                        self.alerts.send_market_update(summary)
                        self.alerts.send_telegram_important(summary)

                    # Enhanced bridge daily summary with best/worst trade
                    try:
                        _wins = _dr_ds.get("wins", 0) if isinstance(_dr_ds, dict) else 0
                        _best = None
                        _worst = None
                        _by_sym = _dr_ds.get("by_symbol", {}) if isinstance(_dr_ds, dict) else {}
                        if _by_sym:
                            _sorted_syms = sorted(_by_sym.items(), key=lambda x: x[1].get("pnl", 0), reverse=True)
                            if _sorted_syms:
                                _best = {"symbol": _sorted_syms[0][0], "pnl": _sorted_syms[0][1].get("pnl", 0)}
                            if len(_sorted_syms) > 1:
                                _worst = {"symbol": _sorted_syms[-1][0], "pnl": _sorted_syms[-1][1].get("pnl", 0)}
                        self.telegram_bridge.send_daily_summary(
                            total_trades=report.total_trades,
                            wins=_wins,
                            net_pnl=report.net_pnl,
                            best_trade=_best,
                            worst_trade=_worst,
                            active_positions=len(self.pos_mgr.get_open_positions()),
                        )
                    except Exception:
                        pass

                    # Aggregate daily performance into SQLite
                    update_daily_performance(_dr_date)
                # ── Feed lessons into LLM memory for future decisions ──
                if hasattr(report, "lessons") and report.lessons:
                    try:
                        from llm.memory_store import apply_memory_update
                        _fed = 0
                        for lesson in report.lessons[:5]:  # Top 5 highest-confidence lessons
                            if lesson.confidence >= 0.5 and lesson.action:
                                # Format with structured markers so quality gate passes
                                _note = f"{lesson.category}: {lesson.message} — {lesson.action}"
                                apply_memory_update(update=_note)
                                _fed += 1
                        if _fed:
                            logger.info(f"[EVOLUTION] Fed {_fed} lessons into LLM memory")
                    except Exception as e:
                        logger.debug(f"Evolution→LLM memory feed error: {e}")

                # ── Feed evolution lessons into Parameter Tuner ──
                # Closes the evolution→tuner feedback loop: lessons about what's
                # working/failing flow into dynamic parameter adjustments.
                try:
                    if hasattr(self, 'feedback') and hasattr(self.feedback, 'tuner'):
                        _tuner_result = tracker.apply_lessons_to_tuner(report, self.feedback.tuner)
                        if _tuner_result:
                            logger.info(f"[EVOLUTION→TUNER] Applied lessons: {_tuner_result}")
                except Exception as e:
                    logger.debug(f"Evolution→Tuner feed error: {e}")

                logger.info(f"[EVOLUTION] Daily report generated: {report.total_trades} trades")
            except Exception as e:
                logger.debug(f"Evolution tracker error: {e}")

            # Auto-decay Kelly and strategy weights daily (prevents stale data dominating)
            try:
                if self.kelly_engine:
                    # Kelly engine doesn't have apply_decay — but strategy weights do
                    pass
                if hasattr(self, 'ensemble') and self.ensemble.weight_manager:
                    self.ensemble.weight_manager.apply_decay()
                    logger.info("[QUANT] Applied daily decay to strategy weights (alpha=0.9)")
            except Exception as e:
                logger.debug(f"Weight decay error: {e}")

            # Walk-forward ratio: auto-reduce sizing when OOS performance degrades
            try:
                if self.trade_ledger:
                    _wf_trades = self.trade_ledger.get_trades(lookback_days=60)
                    if len(_wf_trades) >= 10:
                        from validation.walk_forward import run_rolling_walk_forward, avg_wf_ratio
                        _wf_input = [
                            {"pnl": float(t.get("net_pnl", "0")),
                             "timestamp": float(t.get("timestamp", "0"))}
                            for t in _wf_trades
                        ]
                        _wf_results = run_rolling_walk_forward(_wf_input)
                        self._wf_ratio = avg_wf_ratio(_wf_results) if _wf_results else 1.0
                        _wf_mult = self._get_wf_multiplier()
                        logger.info(
                            f"[QUANT] Walk-forward ratio: {self._wf_ratio:.3f} "
                            f"→ sizing mult={_wf_mult:.2f}"
                        )
                        if self._wf_ratio < 0.4:
                            logger.warning(
                                f"[QUANT] WALK-FORWARD CRITICAL: ratio={self._wf_ratio:.3f} "
                                f"— sizing reduced to {_wf_mult:.0%}"
                            )
            except Exception as e:
                logger.debug(f"Walk-forward computation error: {e}")

            # Rolling Sharpe: early edge degradation detection
            try:
                if self.trade_ledger:
                    import math
                    _sharpe_trades = self.trade_ledger.get_trades(lookback_days=30)
                    _pnls = [float(t.get("net_pnl", "0")) for t in _sharpe_trades if t.get("net_pnl")]
                    if len(_pnls) >= 10:
                        _mean = sum(_pnls) / len(_pnls)
                        _var = sum((p - _mean) ** 2 for p in _pnls) / len(_pnls)
                        _std = math.sqrt(_var) if _var > 0 else 0.001
                        _sharpe_30d = round(_mean / _std, 3)
                        logger.info(f"[QUANT] 30-day rolling Sharpe: {_sharpe_30d:.3f} ({len(_pnls)} trades)")
                        if _sharpe_30d < 0:
                            logger.warning(f"[QUANT] NEGATIVE SHARPE ({_sharpe_30d:.3f}) — edge may be degraded")
                        elif _sharpe_30d < 0.3:
                            logger.warning(f"[QUANT] LOW SHARPE ({_sharpe_30d:.3f}) — monitor for further degradation")
            except Exception as e:
                logger.debug(f"Rolling Sharpe error: {e}")

            # Quant daily report: 6 key metrics with alerting
            if self.daily_reporter:
                try:
                    _qr = self.daily_reporter.generate_report()
                    _qr_text = self.daily_reporter.format_report(_qr)
                    logger.info(f"\n{_qr_text}")
                    if self.alerts and _qr.get("alerts"):
                        self.alerts.send_market_update(
                            f"📊 Quant Daily Report — {len(_qr['alerts'])} alerts\n"
                            + "\n".join(_qr["alerts"][:5])
                        )
                except Exception as e:
                    logger.debug(f"Quant daily report error: {e}")

            # Execution analytics summary
            if hasattr(self, 'execution_analytics') and self.execution_analytics:
                try:
                    _ea_summary = self.execution_analytics.get_slippage_summary(lookback_days=7)
                    if _ea_summary.get("total_fills", 0) > 0:
                        logger.info(
                            f"[EXEC-ANALYTICS] 7d Summary: "
                            f"mean_slippage={_ea_summary['overall_mean_bps']:.2f}bps "
                            f"fills={_ea_summary['total_fills']} "
                            f"maker_rate={_ea_summary['maker_rate']:.1%}"
                        )
                        if self.alerts and _ea_summary["overall_mean_bps"] > 3.0:
                            self.alerts.send_market_update(
                                f"Execution Alert: avg slippage {_ea_summary['overall_mean_bps']:.1f}bps > 3bps threshold"
                            )
                except Exception as e:
                    logger.debug(f"Execution analytics summary error: {e}")

            # Go-live gate status check (runs with daily report)
            if hasattr(self, '_go_live_gate') and self._go_live_gate:
                try:
                    _gate_result = self._go_live_gate.evaluate()
                    _gate_text = self._go_live_gate.format_report(_gate_result)
                    logger.info(f"\n{_gate_text}")
                    if self.alerts and not _gate_result["passed"]:
                        _n_fail = len([g for g in _gate_result["gates"].values() if not g.get("passed")])
                        self.alerts.send_market_update(
                            f"Go-Live Gate: {_n_fail} gate(s) failed — {_gate_result['recommendation']}"
                        )
                except Exception as e:
                    logger.debug(f"Go-live gate daily check error: {e}")

        # Uplift Analytics + Progression: evaluate LLM value and autonomy readiness
        # Runs every 720 ticks (~12 hours) — gives enough time for data to accumulate
        if self._tick % 720 == 360 and self._tick > 360:
            # Uplift Analytics: compute baseline vs LLM-filtered performance delta
            if _UPLIFT_AVAILABLE:
                try:
                    uplift_data = compute_uplift()
                    if uplift_data.get("with_outcome", 0) >= 10:
                        uplift_report = format_uplift_report(uplift_data)
                        logger.info(f"[UPLIFT]\n{uplift_report}")
                        _uplift_delta = uplift_data.get("uplift", {})
                        if _uplift_delta.get("has_data"):
                            _is_positive = _uplift_delta.get("is_positive", False)
                            if self.alerts:
                                self.alerts.send_market_update(
                                    f"*LLM Uplift Report*\n"
                                    f"Verdict: {'POSITIVE' if _is_positive else 'NEGATIVE'}\n"
                                    f"WR delta: {_uplift_delta.get('win_rate_delta', 0):+.1%}\n"
                                    f"Avg PnL delta: ${_uplift_delta.get('avg_pnl_delta', 0):+.2f}"
                                )
                except Exception as e:
                    logger.debug(f"Uplift analytics error: {e}")

            # Progression Controller: evaluate readiness for next autonomy level
            if _PROGRESSION_AVAILABLE:
                try:
                    prog_report = evaluate_progression(self.llm_mode)
                    if prog_report and prog_report.all_passed:
                        logger.info(
                            f"[PROGRESSION] READY to advance: "
                            f"{prog_report.current_mode.name} -> {prog_report.target_mode.name} "
                            f"({prog_report.passed_count}/{prog_report.total_count} gates passed)"
                        )
                        if self.alerts:
                            self.alerts.send_market_update(
                                f"*Autonomy Progression Ready*\n"
                                f"{prog_report.current_mode.name} -> {prog_report.target_mode.name}\n"
                                f"All {prog_report.total_count} gates passed.\n"
                                f"Use /mode to advance."
                            )
                    elif prog_report:
                        logger.info(
                            f"[PROGRESSION] Not ready: "
                            f"{prog_report.passed_count}/{prog_report.total_count} gates"
                        )
                except Exception as e:
                    logger.debug(f"Progression evaluation error: {e}")

        # Strategy Pruning: evaluate and adjust weights for underperforming strategies
        # Runs every 1440 ticks (~24h) offset by 480 to avoid overlap with evolution report
        if self._tick % 1440 == 480 and self._tick > 480:
            if _STRATEGY_PRUNING_AVAILABLE:
                try:
                    _perf_path = os.path.join("data", "analysis", "performance.json")
                    if os.path.exists(_perf_path):
                        import json as _json
                        with open(_perf_path) as _f:
                            _perf_data = _json.load(_f)
                        adjustments = pruning_evaluate(_perf_data)
                        if adjustments:
                            logger.info(
                                f"[PRUNING] {len(adjustments)} strategy weight adjustments made"
                            )
                            if self.alerts:
                                _adj_lines = [
                                    f"  {a['name']}: {a['old_weight']:.2f} -> {a['new_weight']:.2f} ({a['action']})"
                                    for a in adjustments
                                ]
                                self.alerts.send_market_update(
                                    f"*Strategy Pruning*\n"
                                    + "\n".join(_adj_lines)
                                )
                except Exception as e:
                    logger.debug(f"Strategy pruning error: {e}")

        # RL auto-training: retrain policy daily if buffer has enough data
        if self._tick % 1440 == 720 and self._tick > 720:
            try:
                from rl.buffer import get_buffer_stats
                from rl.train_offline import train
                stats = get_buffer_stats()
                if stats.get("total", 0) >= 50:
                    policy = train()
                    if policy:
                        logger.info(
                            f"[RL-AUTO] Daily retrain: {stats['total']} transitions, "
                            f"policy updated"
                        )
            except Exception as e:
                logger.debug(f"RL auto-training error: {e}")

        # ── Wave 3: Portfolio Risk Engine — periodic correlation/vol updates ──
        # Every 30 ticks (~30 min): update forecasts and rebalance suggestions
        if self._tick % 30 == 15 and self.portfolio_risk:
            try:
                self.portfolio_risk.tick(
                    prices=self._last_prices,
                    open_positions={s: {"side": p.side, "entry": p.entry,
                                       "qty": p.qty, "leverage": p.leverage}
                                   for s, p in self.pos_mgr.get_open_positions().items()},
                    equity=self.risk_mgr.equity,
                )
                # Check for cascade signals
                if self.config.enable_cascade_signals:
                    _cascades = self.portfolio_risk.detect_cascade_signals(
                        self._price_changes_1h, threshold_pct=2.0
                    )
                    for _cs in _cascades[:2]:  # Cap at 2 alerts per tick
                        logger.info(
                            f"[CASCADE] {_cs.get('leader')} -> {_cs.get('follower')}: "
                            f"expected {_cs.get('expected_direction')}"
                        )
                        self._llm_triggers.add(
                            LLMTrigger.CROSS_MARKET_DIVERGENCE,
                            context=f"Cascade: {_cs.get('leader')} moved {_cs.get('leader_change', 0):.1f}%, "
                                    f"{_cs.get('follower')} may follow {_cs.get('expected_direction')}",
                        )
            except Exception as e:
                logger.debug(f"[{trace_id}] Portfolio risk tick error: {e}")

        # ── Wave 4: Counterfactual Resolution — resolve pending veto scenarios ──
        # Every 60 ticks (~1 hour): check if vetoed trades would have hit TP/SL
        if self._tick % 60 == 45 and self.counterfactual:
            try:
                self.counterfactual.resolve_pending(
                    current_prices=self._last_prices,
                    lookback_hours=24,
                )
            except Exception as e:
                logger.debug(f"[{trace_id}] Counterfactual resolution error: {e}")

        # ── Wave 4: Meta-Learning — periodic pattern analysis ──
        # Every 360 ticks (~6 hours): analyze decision patterns and generate ideas
        if self._tick % 360 == 180 and self._tick > 180 and self.meta_engine:
            try:
                # Gather recent trades from DB
                _ml_trades = get_recent_trades(50)
                self.meta_engine.tick(
                    recent_trades=_ml_trades,
                    market_state={
                        "price_changes_1h": self._price_changes_1h,
                        "global_bias": self._global_bias,
                        "equity": self.risk_mgr.equity,
                    },
                )
                _meta_ideas = self.meta_engine.get_active_ideas()
                if _meta_ideas:
                    logger.info(
                        f"[META-LEARN] {len(_meta_ideas)} active strategy ideas"
                    )
            except Exception as e:
                logger.debug(f"[{trace_id}] Meta-learning tick error: {e}")

        # ── Wave 4: Performance Attribution — daily attribution report ──
        # Every 1440 ticks (~24h), offset by 960 to avoid overlap
        if self._tick % 1440 == 960 and self._tick > 960 and _ATTRIBUTION_AVAILABLE:
            try:
                _attr_report = compute_attribution(days=7)
                if _attr_report and _attr_report.total_trades >= 5:
                    _attr_text = format_attribution_report(_attr_report)
                    logger.info(f"[ATTRIBUTION]\n{_attr_text}")
                    if self.alerts:
                        # Send summary to Telegram
                        _top = ", ".join(_attr_report.top_contributors[:3]) if _attr_report.top_contributors else "none"
                        _worst = ", ".join(_attr_report.worst_detractors[:3]) if _attr_report.worst_detractors else "none"
                        self.alerts.send_market_update(
                            f"*Performance Attribution (7d)*\n"
                            f"PnL: ${_attr_report.total_pnl:+.2f} | "
                            f"Trades: {_attr_report.total_trades}\n"
                            f"Top contributors: {_top}\n"
                            f"Worst detractors: {_worst}\n"
                            f"LLM value-add: ${_attr_report.llm_value_add:+.2f}\n"
                            f"Sizing alpha: ${_attr_report.sizing_alpha:+.2f}"
                        )
            except Exception as e:
                logger.debug(f"[{trace_id}] Attribution report error: {e}")

        # ── Wave 4: A/B Testing — evaluate experiments periodically ──
        # Every 720 ticks (~12h): check experiment results
        if self._tick % 720 == 420 and self._tick > 420 and self.ab_manager:
            try:
                for _exp in self.ab_manager.get_active_experiments():
                    _result = self.ab_manager.evaluate_experiment(_exp.id)
                    if _result and _result.is_significant:
                        _action_msg = _result.recommended_action.replace("_", " ").title()
                        logger.info(
                            f"[A/B TEST] {_exp.name}: SIGNIFICANT result! "
                            f"p={_result.p_value:.4f}, recommendation={_action_msg}"
                        )
                        if self.alerts:
                            self.alerts.send_market_update(
                                f"*A/B Test Result*\n"
                                f"Experiment: {_exp.name}\n"
                                f"Control WR: {_result.control_win_rate:.1%} | "
                                f"Variant WR: {_result.variant_win_rate:.1%}\n"
                                f"Control PnL: ${_result.control_avg_pnl:+.2f} | "
                                f"Variant PnL: ${_result.variant_avg_pnl:+.2f}\n"
                                f"p-value: {_result.p_value:.4f} | "
                                f"Action: {_action_msg}"
                            )
                        if _result.recommended_action == "graduate_variant":
                            self.ab_manager.graduate_experiment(_exp.id)
            except Exception as e:
                logger.debug(f"[{trace_id}] A/B test evaluation error: {e}")

        # ── Heartbeat + position state persistence ──
        # Save heartbeat every tick for downtime detection on restart
        try:
            save_heartbeat()
        except Exception:
            pass
        # Save position state every 5 ticks for crash recovery
        if self._tick % 5 == 0:
            try:
                save_position_state(self.pos_mgr)
            except Exception:
                pass

        # ── Circuit breaker state persistence ──
        # Save CB state every 10 ticks so it survives restarts during drawdowns
        if self._tick % 10 == 0:
            try:
                save_circuit_breaker_state(self.risk_mgr.circuit_breaker)
            except Exception:
                pass

        # ── Periodic position reconciliation ──
        # Every 60 ticks (~1h): detect and auto-correct position mismatches
        if self._tick % 60 == 45 and self._tick > 0:
            try:
                result = periodic_reconciliation_check(
                    pos_mgr=self.pos_mgr,
                    exchanges=self.fetcher._exchanges,
                    last_prices=self._last_prices,
                )
                phantoms = result.get("phantom", [])
                orphans = result.get("orphan", [])
                if phantoms or orphans:
                    logger.warning(
                        f"[RECONCILE] Drift detected: "
                        f"{len(phantoms)} phantom, {len(orphans)} orphan"
                    )
                    # Auto-correct phantom positions (bot tracking, exchange closed)
                    for sym in phantoms:
                        pos = self.pos_mgr.positions.get(sym)
                        if pos and pos.state != "CLOSED":
                            logger.warning(
                                f"[RECONCILE] Closing phantom position {sym} "
                                f"(exchange says closed)"
                            )
                            pos.state = "CLOSED"
                            pos.close_time = datetime.now(timezone.utc)
                            pos.close_reason = "reconciliation_phantom"
                    # Alert for orphans (need manual review)
                    if self.alerts and orphans:
                        self.alerts.send_market_update(
                            f"⚠️ {len(orphans)} orphan positions on exchange "
                            f"not tracked by bot: {orphans}"
                        )
            except Exception as e:
                logger.debug(f"[{trace_id}] Periodic reconciliation error: {e}")

    def _get_wf_multiplier(self) -> float:
        """Walk-forward degradation multiplier for compound sizing.
        WF ratio >= 0.7: 1.0× (no reduction)
        WF ratio 0.0-0.7: linear scale (0.0× to 1.0×)
        WF ratio < 0.0: 0.0× (halt new entries — overfitting detected)
        """
        if self._wf_ratio >= 0.7:
            return 1.0
        if self._wf_ratio < 0.0:
            return 0.0
        return max(0.0, self._wf_ratio / 0.7)

    def _process_symbol(self, symbol: str, sym_cfg, trace_id: str = ""):
        """Process one symbol: fetch data, check positions, generate signals."""
        # F3: Graceful degradation — halt new entries if exchange is down
        if self.degradation.should_halt_entries():
            # Still process existing positions for SL/TP, but skip new entries
            open_pos = self.pos_mgr.get_open_positions()
            if symbol not in open_pos:
                return  # Skip signal evaluation for symbols without positions

        # Fetch data for all needed timeframes
        try:
            data = self.fetcher.fetch_multi_timeframe(symbol, sym_cfg.coingecko_id, self._needed_tfs)
            self.degradation.record_exchange_success()
        except Exception as e:
            self.degradation.record_exchange_error()
            logger.warning(f"[{symbol}] Exchange fetch failed: {e}")
            return

        # Inject metadata for metadata-dependent strategies (funding_rate, oi_delta)
        _meta = data.setdefault("_meta", {})
        _fr = self._last_funding_rates.get(symbol)
        if _fr is not None:
            data["_funding_rate"] = _fr
            _meta["funding_rate"] = _fr
        # OI: fetch and inject current + previous for delta calculation
        if self._tick % 60 == 0 or symbol not in self._last_open_interest:
            try:
                _oi = self.fetcher.fetch_open_interest(symbol)
                if _oi is not None:
                    _oi_prev = self._last_open_interest.get(symbol)
                    self._last_open_interest[symbol] = _oi
                    _meta["open_interest"] = _oi
                    if _oi_prev is not None:
                        _meta["open_interest_prev"] = _oi_prev
                    # Append to rolling OI history (12 entries ≈ 12h at 60-tick sampling)
                    if symbol not in self._oi_history:
                        self._oi_history[symbol] = collections.deque(maxlen=12)
                    self._oi_history[symbol].append({"ts": int(time.time()), "oi": _oi})
            except Exception:
                pass
        else:
            _oi = self._last_open_interest.get(symbol)
            if _oi is not None:
                _meta["open_interest"] = _oi
        # Always inject OI history when available
        if symbol in self._oi_history and len(self._oi_history[symbol]) >= 2:
            _meta["oi_history"] = list(self._oi_history[symbol])
        # Mark price + basis: fetch every 60 ticks (same cadence as OI)
        if self._tick % 60 == 0 or symbol not in getattr(self, "_last_mark_price", {}):
            if not hasattr(self, "_last_mark_price"):
                self._last_mark_price: Dict[str, tuple] = {}
            try:
                _mark, _basis = self.fetcher.fetch_mark_price(symbol)
                if _mark is not None:
                    self._last_mark_price[symbol] = (_mark, _basis)
                    _meta["mark_price"] = _mark
                    if _basis is not None:
                        _meta["basis_pct"] = round(_basis * 100, 4)  # as % e.g. 0.15
            except Exception:
                pass
        else:
            _cached = getattr(self, "_last_mark_price", {}).get(symbol)
            if _cached:
                _meta["mark_price"] = _cached[0]
                if _cached[1] is not None:
                    _meta["basis_pct"] = round(_cached[1] * 100, 4)

        # Inject BTC 1h data for lead_lag strategy on non-BTC symbols
        if symbol != "BTC":
            try:
                _btc_sym = DEFAULT_SYMBOLS.get("BTC")
                if _btc_sym:
                    _btc_data = self.fetcher.fetch_multi_timeframe("BTC", _btc_sym.coingecko_id, ["1h"])
                    if "1h" in _btc_data and not _btc_data["1h"].empty:
                        data["_btc_1h"] = _btc_data["1h"]
            except Exception:
                pass  # lead_lag will gracefully return None

        # Stale data guard: reject signals if candle data is too old.
        # After restarts or API hiccups, strategies may fire on stale candles.
        # Check the most granular timeframe (5m or 1h) — if its last candle
        # is older than 5 minutes past its expected close, skip signal generation.
        _stale_max_s = 300  # 5 minutes tolerance
        _stale_check_tf = "5m" if "5m" in data else ("1h" if "1h" in data else None)
        if _stale_check_tf and data.get(_stale_check_tf) is not None:
            _stale_df = data[_stale_check_tf]
            if not _stale_df.empty:
                # DataFrame has numeric index with 'time' column (from fetcher)
                # Fall back to index if 'time' column is missing
                if "time" in _stale_df.columns:
                    _last_candle_time = pd.Timestamp(_stale_df["time"].iloc[-1], unit="ms" if isinstance(_stale_df["time"].iloc[-1], (int, float)) and _stale_df["time"].iloc[-1] > 1e12 else None)
                elif _stale_df.index.dtype.kind == 'M':
                    _last_candle_time = _stale_df.index[-1]
                else:
                    _last_candle_time = None
                if _last_candle_time is not None:
                    if _last_candle_time.tzinfo is None:
                        _last_candle_time = _last_candle_time.tz_localize("UTC")
                    _candle_age_s = (pd.Timestamp.now(tz="UTC") - _last_candle_time).total_seconds()
                    _tf_period_s = {"5m": 300, "1h": 3600}.get(_stale_check_tf, 3600)
                    # Data is stale if the last candle closed more than (period + tolerance) ago
                    if _candle_age_s > _tf_period_s + _stale_max_s:
                        # Still process existing positions (SL/TP), but skip new signal generation
                        if symbol not in self.pos_mgr.get_open_positions():
                            logger.warning(
                                f"[{trace_id}][{symbol}] STALE DATA: {_stale_check_tf} candle "
                                f"is {_candle_age_s:.0f}s old (max {_tf_period_s + _stale_max_s}s), "
                                f"skipping signal generation"
                            )
                            Telemetry.inc("stale_data_skips")
                            return

        # Get current price
        current_price = self.fetcher.latest_price(symbol, sym_cfg.coingecko_id)
        if current_price is None:
            return

        # Fill-price guardrail: validate against last known price
        last_known = self._last_prices.get(symbol)
        if last_known is not None:
            err = validate_fill_price(symbol, current_price, last_known)
            if err:
                log_rejection(symbol, "FILL_PRICE_OFFSCALE", confidence=0)
                logger.warning(f"[{symbol}] PRICE REJECTED: {err}")
                return
        self._last_prices[symbol] = current_price

        # Update reflection engine price tracking (move exhaustion)
        if self._reflection_engine is not None:
            try:
                self._reflection_engine.on_price_update(symbol, current_price)
            except Exception:
                pass

        # Feed cross-asset lead-lag monitor with BTC prices
        if self._cross_asset_monitor is not None and symbol == "BTC":
            try:
                sol_price = self._last_prices.get("SOL", 0)
                eth_price = self._last_prices.get("ETH", 0)
                _ca_result = self._cross_asset_monitor.check_btc_lead(
                    [current_price], sol_price, eth_price
                )
                if _ca_result.get("alert"):
                    _ca_msg = (
                        f"BTC LEAD-LAG ALERT: BTC moved {_ca_result['btc_move_pct']:+.2f}% | "
                        f"Expected SOL {_ca_result['expected_sol_move']:+.2f}%, "
                        f"ETH {_ca_result['expected_eth_move']:+.2f}% | "
                        f"Bias: {_ca_result['recommended_side']}"
                    )
                    logger.info(f"[CROSS-ASSET] {_ca_msg}")
                    if self.alerts:
                        self.alerts.send_market_intel(f"🔗 {_ca_msg}")
            except Exception as _ca_err:
                logger.debug(f"Cross-asset check error: {_ca_err}")

        # Feed lead-lag boost engine with price data (BTC + followers)
        if hasattr(self, '_lead_lag_engine') and self._lead_lag_engine is not None:
            try:
                base = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "").replace("/USD", "")
                if base == "BTC":
                    self._lead_lag_engine.update_btc_price(current_price)
                else:
                    self._lead_lag_engine.update_follower_price(base, current_price)
            except Exception:
                pass

        # Feed correlation boost with price data
        if hasattr(self, '_correlation_boost') and self._correlation_boost is not None:
            try:
                self._correlation_boost.update_price(symbol, current_price)
            except Exception:
                pass
        # Feed correlation gate with price data
        if self.correlation_gate:
            try:
                self.correlation_gate.update_prices(symbol, current_price, time.time())
            except Exception:
                pass

        # Update counterfactual learner with current price for resolution
        try:
            from llm.brain_wiring import update_counterfactuals_with_price
            # Use 1h candle OHLC if available, otherwise approximate from latest price
            _1h = data.get("1h") if data else None
            if _1h is not None and not _1h.empty:
                _last = _1h.iloc[-1]
                update_counterfactuals_with_price(
                    symbol, float(_last.get("high", current_price)),
                    float(_last.get("low", current_price)), current_price
                )
            else:
                update_counterfactuals_with_price(symbol, current_price, current_price, current_price)
        except Exception:
            pass  # Non-critical: counterfactual learning is best-effort

        # Cross-symbol pattern tracking: record price for lead-lag detection
        if self.cross_symbol_tracker:
            try:
                self.cross_symbol_tracker.record_price(symbol, current_price)
            except Exception:
                pass  # Non-critical

        # Wave 3: Portfolio risk engine — record price for correlation/vol tracking
        if self.portfolio_risk:
            try:
                self.portfolio_risk.record_price(symbol, current_price)
            except Exception:
                pass  # Non-critical

        # Fetch and cache funding rate (throttled: once per 60 ticks per symbol)
        if self._tick % 60 == 0 or symbol not in self._last_funding_rates:
            try:
                fr = self.fetcher.fetch_funding_rate(symbol)
                if fr is not None:
                    self._last_funding_rates[symbol] = fr
            except Exception:
                pass

        # Track 1h price changes and high/low for cross-market divergence + veto resolution
        try:
            df_1h_div = data.get("1h")
            if df_1h_div is not None and not df_1h_div.empty and len(df_1h_div) > 2:
                pch = (current_price - float(df_1h_div["close"].iloc[-2])) / float(df_1h_div["close"].iloc[-2]) * 100
                self._price_changes_1h[symbol] = pch
                # Track 1h high/low from the latest candle for accurate veto resolution
                self._price_highs_1h[symbol] = float(df_1h_div["high"].iloc[-1])
                self._price_lows_1h[symbol] = float(df_1h_div["low"].iloc[-1])
        except Exception:
            pass

        # Record market snapshot for ML passive learning
        if self.ml:
            snapshot = MarketSnapshot(symbol=symbol, price=current_price)
            # Compute market context from available data
            try:
                df_1h = data.get("1h")
                if df_1h is not None and not df_1h.empty and len(df_1h) > 2:
                    snapshot.price_change_1h_pct = (current_price - float(df_1h["close"].iloc[-2])) / float(df_1h["close"].iloc[-2]) * 100
                    if len(df_1h) > 24:
                        snapshot.price_change_24h_pct = (current_price - float(df_1h["close"].iloc[-24])) / float(df_1h["close"].iloc[-24]) * 100
                    avg_vol = float(df_1h["volume"].tail(20).mean())
                    if avg_vol > 0:
                        snapshot.volume_ratio = float(df_1h["volume"].iloc[-1]) / avg_vol
                    # ATR-based volatility (ATR14 / price as percentage)
                    if len(df_1h) > 14:
                        prev_c = df_1h["close"].shift(1)
                        tr = pd.concat([
                            df_1h["high"] - df_1h["low"],
                            (df_1h["high"] - prev_c).abs(),
                            (df_1h["low"] - prev_c).abs(),
                        ], axis=1).max(axis=1)
                        atr14 = float(tr.rolling(14, min_periods=1).mean().iloc[-1])
                        snapshot.volatility = atr14 / current_price * 100
            except Exception:
                pass
            self.ml.record_snapshot(snapshot)

        # Pre-close trigger: predict if any open position is about to close
        open_pos = self.pos_mgr.get_open_positions()
        if symbol in open_pos:
            pos = open_pos[symbol]
            pre_close = self._llm_triggers.check_pre_close(
                symbol=symbol,
                side=pos.side,
                entry=pos.entry,
                current_price=current_price,
                sl=pos.sl,
                tp1=pos.tp1,
                tp2=pos.tp2,
                state=pos.state,
                atr=pos.atr,
            )
            if pre_close:
                self._llm_triggers.add(
                    LLMTrigger.PRE_CLOSE,
                    symbol=symbol,
                    context=pre_close,
                )

        # force_close() calls below return TradeEvents that bypass the normal events loop.
        # Collect them here and inject after update_price() so equity/ledger/kelly stay in sync.
        _force_close_events: list = []

        # Liquidation distance monitoring: check every tick for leveraged positions
        if symbol in open_pos and open_pos[symbol].leverage > 1.0:
            _liq_pos = open_pos[symbol]
            _liq_check = self.leverage_mgr.check_liquidation_risk(
                entry=_liq_pos.entry,
                current_price=current_price,
                side=_liq_pos.side,
                leverage=_liq_pos.leverage,
                safety_buffer=0.03,  # 3% distance triggers alert
            )
            _liq_dist = _liq_check.get("distance_pct", 1.0)
            if _liq_dist < 0.015:
                # < 1.5% from liquidation — force close
                logger.warning(
                    f"[{trace_id}][{symbol}] LIQUIDATION PROXIMITY: "
                    f"{_liq_dist:.1%} from liquidation at {_liq_check.get('liquidation_price', 0):.4f}. "
                    f"Force closing."
                )
                _fc_pos = self.pos_mgr.positions.get(symbol)
                if _fc_pos:
                    _close_side = "SELL" if _fc_pos.side == "LONG" else "BUY"
                    _liq_close = self.order_executor.close_position(
                        symbol, _close_side, _fc_pos.qty, current_price, "LIQUIDATION_PROXIMITY"
                    )
                    if _liq_close and getattr(_liq_close, "filled", False):
                        _fc = self.pos_mgr.force_close(symbol, current_price, "LIQUIDATION_PROXIMITY")
                        if _fc:
                            _fc.metadata["_exchange_submitted"] = True
                            _force_close_events.append(_fc)
                    else:
                        logger.critical(
                            f"[{trace_id}][{symbol}] LIQUIDATION CLOSE FAILED — position still open on exchange. "
                            f"Reconciliation will handle. Result: {_liq_close}"
                        )
            elif _liq_dist < 0.03:
                # < 3% from liquidation — tighten SL
                _new_sl = current_price * (0.995 if _liq_pos.side == "LONG" else 1.005)
                if (_liq_pos.side == "LONG" and _new_sl > _liq_pos.sl) or \
                   (_liq_pos.side == "SHORT" and _new_sl < _liq_pos.sl):
                    _liq_pos.sl = _new_sl
                    logger.warning(
                        f"[{trace_id}][{symbol}] Liquidation warning: "
                        f"{_liq_dist:.1%} distance — tightened SL to {_new_sl:.4f}"
                    )

        # D2: Funding-aware hold time optimization
        # Close marginal positions before 8-hour funding payments
        if symbol in open_pos and open_pos[symbol].leverage > 1.0:
            _fund_pos = open_pos[symbol]
            _fr = self._last_funding_rates.get(symbol, 0.0)
            if _fr != 0:
                _p = self._last_prices.get(symbol, _fund_pos.entry)
                if _fund_pos.side == "LONG":
                    _unrealized_pct = (_p - _fund_pos.entry) / _fund_pos.entry * 100
                else:
                    _unrealized_pct = (_fund_pos.entry - _p) / _fund_pos.entry * 100
                if should_close_before_funding(
                    pnl_pct=_unrealized_pct,
                    funding_rate=_fr,
                    leverage=_fund_pos.leverage,
                    side=_fund_pos.side,
                ):
                    logger.info(
                        f"[{trace_id}][{symbol}] Closing before funding: "
                        f"PnL={_unrealized_pct:.2f}%, rate={_fr:.5f}, "
                        f"lev={_fund_pos.leverage:.0f}x"
                    )
                    _fc_pos = self.pos_mgr.positions.get(symbol)
                    if _fc_pos:
                        _close_side = "SELL" if _fc_pos.side == "LONG" else "BUY"
                        _fund_close = self.order_executor.close_position(
                            symbol, _close_side, _fc_pos.qty, current_price, "FUNDING_AVOIDANCE"
                        )
                        if _fund_close and getattr(_fund_close, "filled", False):
                            _fc = self.pos_mgr.force_close(symbol, current_price, "FUNDING_AVOIDANCE")
                            if _fc:
                                _fc.metadata["_exchange_submitted"] = True
                                _force_close_events.append(_fc)
                        else:
                            logger.critical(
                                f"[{trace_id}][{symbol}] FUNDING CLOSE FAILED — position still open. "
                                f"Reconciliation will handle. Result: {_fund_close}"
                            )

        # Accrue funding costs on open positions (paper trading doesn't auto-deduct)
        _fr = self._last_funding_rates.get(symbol, 0.0)
        if _fr:
            self.pos_mgr.accrue_funding(symbol, _fr)

        # MFE-aware exit intelligence: check if we should take profit or cut early
        if symbol in open_pos:
            _mfe_pos = open_pos[symbol]
            try:
                from execution.mfe_exit import get_exit_recommendation
                _hold_h = (time.time() - _mfe_pos.open_time.timestamp()) / 3600 if _mfe_pos.open_time else 0
                _vol_ratio = data.get("1h", pd.DataFrame()).get("volume", pd.Series()).iloc[-1] / data.get("1h", pd.DataFrame()).get("volume", pd.Series()).rolling(20).mean().iloc[-1] if "1h" in data and len(data.get("1h", pd.DataFrame())) > 20 else 1.0
                _mfe_rec = get_exit_recommendation(
                    symbol=symbol,
                    entry=_mfe_pos.entry,
                    current_price=current_price,
                    side=_mfe_pos.side,
                    leverage=_mfe_pos.leverage,
                    hold_hours=_hold_h,
                    volume_ratio=_vol_ratio,
                )
                if _mfe_rec.action == "TAKE_PROFIT":
                    logger.info(f"[{symbol}] MFE EXIT: TAKE_PROFIT — {_mfe_rec.reason}")
                    _fc = self.pos_mgr.force_close(symbol, current_price, "MFE_TAKE_PROFIT")
                    close_side = "SELL" if _mfe_pos.side == "LONG" else "BUY"
                    self.order_executor.close_position(symbol, close_side, _mfe_pos.qty, current_price, "MFE_TAKE_PROFIT")
                    if _fc:
                        _fc.metadata["_exchange_submitted"] = True
                        _force_close_events.append(_fc)
                elif _mfe_rec.action == "EXIT_NOW":
                    logger.info(f"[{symbol}] MFE EXIT: EXIT_NOW — {_mfe_rec.reason}")
                    _fc = self.pos_mgr.force_close(symbol, current_price, "MFE_EXIT_NOW")
                    close_side = "SELL" if _mfe_pos.side == "LONG" else "BUY"
                    self.order_executor.close_position(symbol, close_side, _mfe_pos.qty, current_price, "MFE_EXIT_NOW")
                    if _fc:
                        _fc.metadata["_exchange_submitted"] = True
                        _force_close_events.append(_fc)
                elif _mfe_rec.action == "TIGHTEN_STOP":
                    # Move SL closer to lock in gains
                    if _mfe_pos.side == "LONG":
                        _new_sl = current_price * 0.997  # 0.3% trailing
                        if _new_sl > _mfe_pos.sl:
                            _mfe_pos.sl = _new_sl
                            logger.info(f"[{symbol}] MFE: Tightened SL to ${_new_sl:.2f} — {_mfe_rec.reason}")
                    else:
                        _new_sl = current_price * 1.003
                        if _new_sl < _mfe_pos.sl:
                            _mfe_pos.sl = _new_sl
                            logger.info(f"[{symbol}] MFE: Tightened SL to ${_new_sl:.2f} — {_mfe_rec.reason}")
            except Exception as _mfe_err:
                logger.debug(f"[{symbol}] MFE exit check error: {_mfe_err}")

        # Update existing positions (pass 5m data for early exit momentum detection)
        df_5m = data.get("5m")
        events = list(self.pos_mgr.update_price(symbol, current_price, df_5m=df_5m))
        if _force_close_events:
            events.extend(_force_close_events)
        # Inject LLM_EXIT_AGENT close events from _check_llm_exit_suggestions()
        # These were collected in self._pending_exit_events during background exit checks.
        # Must be injected per symbol to ensure they go through post-trade callbacks.
        if hasattr(self, '_pending_exit_events') and self._pending_exit_events:
            # Filter to events for this symbol
            symbol_exit_events = [e for e in self._pending_exit_events if e.symbol == symbol]
            if symbol_exit_events:
                events.extend(symbol_exit_events)
                # Remove from pending (already injected)
                self._pending_exit_events = [e for e in self._pending_exit_events if e.symbol != symbol]
        for event in events:
            # 2026-06-05: capture position object BEFORE close processing removes
            # it from pos_mgr.positions. Without this snapshot, every downstream
            # lookup (line ~3282 strategy weights, ~3457 deep_memory trade_dna,
            # learning_integration, etc) gets None and silently skips writes.
            # This was the root cause of ALL memory/learning writes being dead
            # since 2026-05-30 restart — every closed trade was information loss.
            _captured_pos = self.pos_mgr.positions.get(symbol)

            # Submit close order to exchange for full/partial closes
            _close_actions = ("SL", "TP1", "TP2", "TRAILING_STOP", "EARLY_EXIT",
                              "EMERGENCY", "LIQUIDATION_AVOID", "LIQUIDATION_PROXIMITY",
                              "FUNDING_AVOIDANCE", "ROTATE_PROFIT", "ROTATE_LOSS_AVOIDANCE",
                              "TIME_STOP", "TP1_FULL")
            if event.action in _close_actions and event.qty > 0 and not event.metadata.get("_exchange_submitted"):
                # Determine close side (opposite of position side)
                close_side = "SELL" if event.side == "LONG" else "BUY"
                close_result = self.order_executor.close_position(
                    symbol=event.symbol,
                    side=close_side,
                    qty=event.qty,
                    price=event.price,
                    reason=event.action,
                )
                if not close_result.filled:
                    logger.critical(
                        f"[{trace_id}][{symbol}] CLOSE ORDER FAILED for {event.action} — "
                        f"position still open on exchange. Skipping state update. "
                        f"Reconciliation will handle. Error: {close_result.error}"
                    )
                    continue  # Skip P&L update — position is still open on exchange

            self.risk_mgr.update_equity(event.pnl - event.fee)

            # Log trade event to database
            log_trade(
                symbol=event.symbol,
                action=event.action,
                side=event.side,
                price=event.price,
                qty=event.qty,
                pnl=event.pnl,
                fee=event.fee,
                leverage=event.leverage,
                strategy=event.strategy,
                metadata=event.metadata
            )

            # Full close actions (for ML, weight tracking, cooldown)
            # TIME_STOP/TP1_FULL/HOLD_LIMIT previously missing → trades silently dropped from trade_ledger
            # MFE_TAKE_PROFIT/MFE_EXIT_NOW: force-close path, injected via _force_close_events
            _FULL_CLOSE = ("SL", "TP2", "TRAILING_STOP", "EARLY_EXIT",
                           "EMERGENCY", "LIQUIDATION_AVOID", "LIQUIDATION_PROXIMITY",
                           "FUNDING_AVOIDANCE", "ROTATE_PROFIT", "ROTATE_LOSS_AVOIDANCE",
                           "TIME_STOP", "TP1_FULL", "HOLD_LIMIT",
                           "MFE_TAKE_PROFIT", "MFE_EXIT_NOW",
                           # 2026-06-06: LLM_EXIT_AGENT was missing — Exit Agent closes
                           # silently bypassed trade_ledger writes + strategy weight
                           # outcome + deep memory + ALL post-trade learning. HYPE SHORT
                           # close at 07:40:37 (-$1.49) was lost. Adding here so future
                           # Exit Agent closes properly persist.
                           "LLM_EXIT_AGENT",
                           # 2026-07-01 (audit #51): heuristic exit-engine full closes
                           # now inject their event — persist them like the agent's.
                           "LLM_EXIT_ENGINE")

            # Record outcome for strategy weight tracking (only on full close, use total PnL)
            # 2026-06-05: removed `and event.strategy` guard — empty strategy was silently
            # bypassing record_outcome for ALL ensemble trades since 2026-05-30 restart,
            # causing strategy weights frozen at 0.30 across all 6 strategies. Fallback
            # to "ensemble" when event.strategy is empty so the outcome still records.
            if event.action in _FULL_CLOSE:
                # 2026-06-06: use _captured_pos from line 3149 instead of re-fetching.
                # For LLM_EXIT_AGENT closes, pos_mgr.positions.get(symbol) was returning
                # None mid-cycle, causing silent skip of trade_ledger.record_trade
                # (P1 bug: 4 LLM_EXIT closes missing from CSV) AND counterfactual being
                # called with entry_price=0 → safety floor 0.01 → -35,868% amplification
                # (P3 bug). Same root cause, same fix pattern as 5e1489d.
                pos = _captured_pos
                total_pnl = pos.realized_pnl if pos else event.pnl
                _strategy_key = event.strategy if event.strategy else "ensemble"
                self.weight_mgr.record_outcome(_strategy_key, total_pnl > 0, symbol=symbol)

                # Record regime-specific feedback and confidence floor
                try:
                    regime = "unknown"
                    confidence = 50.0
                    hold_hours = 0.0
                    if pos:
                        regime = pos.entry_reasons.get("regime", "unknown") if pos.entry_reasons else "unknown"
                        confidence = pos.confidence if pos.confidence else (pos.entry_reasons.get("llm_confidence") or pos.entry_reasons.get("win_prob_deflated") or 50.0) if pos.entry_reasons else 50.0
                        if pos.opened_at and pos.close_time:
                            hold_hours = (pos.close_time - pos.opened_at).total_seconds() / 3600.0
                    self.regime_feedback.record_trade(
                        regime=regime,
                        pnl=total_pnl,
                        confidence=confidence,
                        strategy=event.strategy,
                        hold_hours=hold_hours,
                        metadata={"symbol": symbol, "action": event.action}
                    )
                    # Record for adaptive confidence floor (binned by confidence level)
                    self.confidence_floor.record_outcome(
                        confidence=confidence,
                        win=total_pnl > 0,
                        pnl=total_pnl,
                        strategy=event.strategy,
                        symbol=symbol,
                        regime=regime,
                    )
                    # Record hold-time performance (learn min hold times per regime)
                    self.hold_time_rules.record_trade(
                        regime=regime,
                        hold_hours=hold_hours,
                        win=total_pnl > 0,
                        pnl=total_pnl
                    )
                    # Record signal quality outcome (learns meta-confidence)
                    if self.signal_quality:
                        self.signal_quality.record_outcome(
                            features_key=(event.strategy, symbol, regime),
                            win=total_pnl > 0,
                            pnl=total_pnl
                        )
                    # Record parameter tuning outcome (learns parameter adjustments)
                    if self.parameter_tuner:
                        self.parameter_tuner.record_outcome(
                            win=total_pnl > 0,
                            pnl=total_pnl,
                            pnl_pct=total_pnl / (self.risk_mgr.equity or 1.0),
                            regime=regime,
                            symbol=symbol,
                            num_agree=len(pos.entry_reasons.get("strategies_agree", [])) if pos and pos.entry_reasons else 1
                        )
                    # Record continuous backtest outcome (real-time validation)
                    if self.continuous_backtest:
                        self.continuous_backtest.record_outcome(
                            symbol=symbol,
                            side=pos.side if pos else ("BUY" if "BUY" in event.side else "SELL"),
                            entry_price=pos.entry if pos else 0,
                            exit_price=event.price if hasattr(event, 'price') else 0,
                            entry_confidence=confidence,
                            predicted_direction=1 if (pos and pos.side == "LONG") else -1 if pos else 0,
                            actual_return=total_pnl,
                            holding_time_hours=hold_hours
                        )
                except Exception as e:
                    logger.debug(f"Feedback recording error: {e}")

                # Record for LLM memory-worthy event detection
                _et = ""
                if pos and pos.trade_profile:
                    _et = pos.trade_profile.entry_type
                self._llm_triggers.record_trade_outcome(
                    strategy=event.strategy,
                    entry_type=_et,
                    win=total_pnl > 0,
                )

                # Record outcome for Quant Brain chase prevention
                if self._quant_brain is not None:
                    try:
                        self._quant_brain.record_outcome(symbol, total_pnl > 0)
                    except Exception:
                        pass

            # Log trade event (paper trading compatibility)
            if self.trade_logger:
                hold_time = event.metadata.get("hold_time_s", 0)
                self.trade_logger.log_trade_event(event, hold_time_s=hold_time)

            # Record outcome for feedback loop (TOTAL trade PnL)
            if event.action in _FULL_CLOSE:
                # 2026-06-05: use _captured_pos (snapshot taken before close removed
                # position from pos_mgr) so trade DNA + memory writes actually fire
                pos = _captured_pos if _captured_pos else self.pos_mgr.positions.get(symbol)
                total_pnl = pos.realized_pnl if pos else event.pnl
                _et_fb = ""
                _pd_fb = ""
                _rg_fb = ""
                if pos and pos.trade_profile:
                    _et_fb = pos.trade_profile.entry_type
                    _pd_fb = pos.trade_profile.primary_driver
                    _rg_fb = pos.trade_profile.regime
                # Record outcome for regime-aware strategy weighting auto-tuning
                if getattr(self, '_regime_strategy_weighter', None) is not None and _rg_fb and event.strategy:
                    try:
                        self._regime_strategy_weighter.record_outcome(_rg_fb, event.strategy, total_pnl > 0)
                    except Exception:
                        pass
                # Extract LLM decision data from position's entry_reasons
                _llm_action = pos.entry_reasons.get("llm_action", "") if pos and pos.entry_reasons else ""
                _llm_conf = pos.entry_reasons.get("llm_confidence", 0.0) if pos and pos.entry_reasons else 0.0
                _llm_agreed = pos.entry_reasons.get("llm_agreed", True) if pos and pos.entry_reasons else True
                try:
                    self.feedback.record_outcome(
                        confidence=pos.confidence if pos else 0,
                        win=total_pnl > 0,
                        pnl=total_pnl,
                        strategy=event.strategy,
                        symbol=symbol,
                        regime=_rg_fb,
                        side=event.side,
                        entry_type=_et_fb,
                        num_agree=pos.entry_reasons.get("num_agree", 1) if pos and pos.entry_reasons else 1,
                        hold_time_s=event.metadata.get("hold_time_s", 0),
                        exit_action=event.action,
                        leverage=event.leverage,
                        llm_action=_llm_action,
                        llm_confidence=_llm_conf,
                        llm_agreed=_llm_agreed,
                    )
                except Exception as e:
                    logger.warning(f"Feedback outcome error: {e}")

                # Graduated rules: record outcome so rules track accuracy + auto-retire poor rules
                try:
                    from llm.graduated_rules import get_graduated_rules_engine
                    _gr_hr = pos.open_time.hour if pos and getattr(pos, "open_time", None) else -1
                    # 2026-06-16: entry_reasons can be a dict OR a list (trade_profile path
                    # sets it to strategies_agree). The old `.get()` threw on lists, so the
                    # bare except silently skipped record_outcome for those trades — a hidden
                    # cause of times_correct=0. Handle both shapes.
                    _gr_er_raw = getattr(pos, "entry_reasons", None) if pos else None
                    _gr_er = _gr_er_raw if isinstance(_gr_er_raw, dict) else {}
                    _gr_strats = _gr_er.get("strategies_agree") or (
                        _gr_er_raw if isinstance(_gr_er_raw, list) else [])
                    _gr_num = (len(_gr_strats) if _gr_strats else 0) or _gr_er.get("num_agree", 0)
                    # evaluate_signal matches confidence on a 0-100 scale; the old code passed
                    # llm_confidence (0-1, e.g. 0.52) so every confidence-banded rule failed to
                    # match at close. Prefer the entry ensemble confidence; normalize 0-1 -> 0-100.
                    _gr_conf = (_gr_er.get("confidence") or _gr_er.get("llm_confidence")
                                or _gr_er.get("win_prob_deflated") or 0.0)
                    _gr_conf = float(_gr_conf) if _gr_conf else 0.0
                    if 0.0 < _gr_conf <= 1.0:
                        _gr_conf *= 100.0
                    # Use the ENTRY regime (what evaluate_signal saw), not the close-time regime.
                    _gr_regime = _gr_er.get("regime") or _rg_fb
                    get_graduated_rules_engine().record_outcome(
                        symbol=symbol, regime=_gr_regime, side=event.side, won=total_pnl > 0,
                        hour_utc=_gr_hr, strategies_active=_gr_strats,
                        num_agree=_gr_num, confidence=_gr_conf,
                    )
                except Exception as _gr_e:
                    # Was a bare `except: pass` — silently swallowing this was a hidden cause
                    # of times_correct=0 (the numerator never got written). Log it instead.
                    logger.warning(f"[GRAD-RULES] record_outcome failed at close for {symbol}: {_gr_e}")

                # Quant system: record to IC tracker, Kelly engine, trade ledger, resolve shadows
                if pos:
                    _factors = pos.entry_reasons.get("strategies", []) if pos.entry_reasons else []
                    if not _factors and event.strategy:
                        _factors = [event.strategy]
                    _direction = 1 if (pos.side if hasattr(pos, 'side') else event.side) == "LONG" else -1
                    _actual_return = total_pnl / (self.risk_mgr.equity or 1.0)
                    _pnl_pct = _actual_return * 100

                    if self.ic_tracker:
                        try:
                            for _factor in _factors:
                                self.ic_tracker.record(_factor, _direction, _actual_return)
                        except Exception as e:
                            logger.debug(f"IC tracker record error: {e}")

                    if self.kelly_engine:
                        try:
                            for _factor in _factors:
                                self.kelly_engine.record_trade(_factor, total_pnl > 0, _pnl_pct)
                        except Exception as e:
                            logger.debug(f"Kelly engine record error: {e}")

                    if self.trade_ledger:
                        try:
                            _hold_hours = event.metadata.get("hold_time_s", 0) / 3600
                            _regime = _rg_fb or self._tick_regime_cache.get(symbol, "unknown")
                            _factors_str = ",".join(_factors)
                            _num_agree = pos.entry_reasons.get("num_agree", 1) if pos.entry_reasons else 1
                            _session_dd = 0.0
                            cb = self.risk_mgr.circuit_breaker
                            if hasattr(cb, 'session_peak_equity') and cb.session_peak_equity > 0:
                                _session_dd = round(
                                    (cb.session_peak_equity - self.risk_mgr.equity) / cb.session_peak_equity * 100, 2
                                )
                            # Compute realized R:R for EV calibration
                            _stop_width = abs(pos.entry - pos.sl) if pos.sl else 0
                            _realized_rr = round(total_pnl / (_stop_width * (pos.qty or 1)), 3) if _stop_width > 0 and pos.qty else 0
                            _predicted_ev = pos.entry_reasons.get("ev_per_dollar", "") if pos.entry_reasons else ""
                            self.trade_ledger.record_trade({
                                "symbol": symbol,
                                "side": event.side,
                                "regime_1h": _regime,
                                "regime_4h": self._tick_regime_cache.get(f"{symbol}_4h", ""),
                                "agreement_level": str(_num_agree),
                                "contributing_factors": _factors_str,
                                "confidence_score": str(pos.confidence),
                                "kelly_weight_applied": str(
                                    self.kelly_engine.compute_kelly_weight(event.strategy)
                                    if self.kelly_engine and event.strategy else ""
                                ),
                                "compound_size_multiplier": str(self._compound_mult_cache.pop(symbol, "")),
                                "leverage": str(pos.leverage),
                                "hold_hours": f"{_hold_hours:.2f}",
                                "exit_type": event.action,
                                "entry_price": str(pos.entry),
                                "snapshot_entry": str(pos.entry_reasons.get("snapshot_entry", "")) if pos.entry_reasons else "",
                                "exit_price": str(event.price),
                                "gross_pnl": str(round(total_pnl + (pos.fees_paid or 0), 2)),
                                "fees": str(round(pos.fees_paid or 0, 2)),
                                "funding": "0",
                                "net_pnl": str(round(total_pnl, 2)),
                                "running_equity": str(round(self.risk_mgr.equity, 2)),
                                "session_dd_pct": str(_session_dd),
                                "predicted_ev": str(_predicted_ev),
                                "realized_rr": str(_realized_rr),
                                "win": "1" if total_pnl > 0 else "0",
                            })
                        except Exception as e:
                            logger.warning(f"Trade ledger record error: {e}")

                    if self.shadow_ledger:
                        try:
                            self.shadow_ledger.resolve_shadows(symbol, event.price)
                        except Exception as e:
                            logger.debug(f"Shadow ledger resolve error: {e}")

                # Growth intelligence: feed trade data to self-evolving systems
                try:
                    now_utc = datetime.now(timezone.utc)
                    self.growth.on_trade_closed({
                        "symbol": symbol,
                        "side": event.side,
                        "outcome": "WIN" if total_pnl > 0 else "LOSS",
                        "pnl": total_pnl,
                        "pnl_pct": (total_pnl / self.risk_mgr.equity * 100) if self.risk_mgr.equity > 0 else 0,
                        "confidence": pos.confidence if pos else 0,
                        "regime": _rg_fb,
                        "strategy": event.strategy,
                        "num_agree": pos.entry_reasons.get("num_agree", 1) if pos and pos.entry_reasons else 1,
                        "hold_time_s": event.metadata.get("hold_time_s", 0),
                        "leverage": event.leverage,
                        "hour": now_utc.hour,
                        "entry_type": _et_fb,
                    })
                except Exception as e:
                    logger.debug(f"Growth trade record error: {e}")

                # E1: Feed strategy discovery corpus with trade observations
                try:
                    from llm.strategy_discovery.corpus import add_observation
                    _outcome_str = "WIN" if total_pnl > 0 else "LOSS"
                    add_observation(
                        category="trade_outcome",
                        symbol=symbol,
                        regime=_rg_fb or "unknown",
                        observation=(
                            f"{event.strategy} {event.side} {_outcome_str}: "
                            f"pnl=${total_pnl:.2f}, regime={_rg_fb}, "
                            f"exit={event.action}, entry_type={_et_fb}, "
                            f"lev={event.leverage:.0f}x, "
                            f"hold={event.metadata.get('hold_time_s', 0):.0f}s"
                        ),
                    )
                except Exception:
                    pass  # Corpus not critical

                # Deep memory: record full trade DNA for LLM knowledge base
                # 2026-06-05: use _captured_pos snapshot — re-fetching from
                # pos_mgr.positions returned None because close already removed it.
                try:
                    _dm_pos = _captured_pos if _captured_pos else self.pos_mgr.positions.get(symbol)
                    if _dm_pos:
                        self._record_trade_dna(symbol, _dm_pos, event)
                        # Invalidate dynamic threshold cache so next decision sees fresh data
                        try:
                            from llm.dynamic_thresholds import get_dynamic_thresholds
                            get_dynamic_thresholds().invalidate()
                        except Exception:
                            pass
                        # Invalidate prompt enricher cache so agents see fresh regime floors
                        try:
                            from llm.agents.prompt_enricher import invalidate_cache as _inv_enricher
                            _inv_enricher()
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"Deep memory trade DNA error: {e}")

                # Post-Trade Learner: generate immediate lesson and inject into memory
                try:
                    from llm.post_trade_learner import generate_immediate_lesson
                    from llm.memory_store import apply_memory_update as _ptl_mem_update
                    _ptl_lesson = generate_immediate_lesson({
                        "symbol": symbol,
                        "side": event.side,
                        "outcome": "WIN" if total_pnl > 0 else "LOSS",
                        "pnl": total_pnl,
                        "confidence": pos.confidence if pos else 0,
                        "regime": _rg_fb,
                        "strategy": event.strategy,
                        "hold_time_s": event.metadata.get("hold_time_s", 0),
                        "exit_action": event.action,
                        "llm_action": _llm_action,
                        "llm_confidence": _llm_conf,
                        "funding_rate": self._last_funding_rates.get(symbol, 0)
                            if hasattr(self, '_last_funding_rates') else 0,
                    })
                    if _ptl_lesson:
                        _ptl_mem_update(_ptl_lesson, symbol=symbol, regime=_rg_fb)
                except Exception as e:
                    logger.debug(f"Post-trade learner error: {e}")

                # Reflection Engine: post-trade analysis with coded observations
                try:
                    if hasattr(self, '_reflection_engine') and self._reflection_engine is not None:
                        _refl = self._reflection_engine.on_close(
                            symbol=symbol,
                            side=event.side,
                            entry_price=pos.entry if pos else 0,
                            exit_price=event.metadata.get("exit_price", 0),
                            pnl=total_pnl,
                            hold_time_s=event.metadata.get("hold_time_s", 0),
                            leverage=pos.leverage if pos else 1,
                            confidence=pos.confidence if pos else 0,
                            regime=_rg_fb,
                            exit_action=event.action,
                            sl_price=pos.original_sl if pos else 0,
                            tp1_price=pos.tp1 if pos else 0,
                            peak_price=pos.highest_price if pos else 0,
                            lowest_price=pos.lowest_price if pos else 0,
                            win_prob=pos.entry_reasons.get("win_prob", 0) if pos and hasattr(pos, 'entry_reasons') else 0,
                            ev=pos.entry_reasons.get("ev_per_dollar", 0) if pos and hasattr(pos, 'entry_reasons') else 0,
                            rr=pos.entry_reasons.get("rr_tp1", 0) if pos and hasattr(pos, 'entry_reasons') else 0,
                            entry_reasons=pos.entry_reasons if pos and hasattr(pos, 'entry_reasons') else {},
                            atr=getattr(pos, 'atr', 0) or 0,
                        )
                except Exception as e:
                    logger.debug(f"Reflection engine error: {e}")

                # Trade Autopsy: generate periodic structured analysis every 5 trades
                try:
                    from llm.trade_autopsy import should_run_autopsy, generate_autopsy
                    # Count closed trades (approximate via feedback loop or counter)
                    _trade_count = getattr(self, '_closed_trade_count', 0) + 1
                    self._closed_trade_count = _trade_count
                    if should_run_autopsy(_trade_count):
                        generate_autopsy()  # Pulls from deep memory, caches result
                except Exception as e:
                    logger.debug(f"Trade autopsy error: {e}")

                # Self-Teaching: removed direct feed — now handled exclusively via
                # growth.on_trade_closed() → orchestrator._teaching_engine.record_trade_for_learning()
                # This fixes the duplicate feed that was counting every trade twice.

                # Learning Integrator: validate insights, close broken feedback loops
                try:
                    from llm.learning_integrator import get_learning_integrator
                    get_learning_integrator().on_trade_closed({
                        "symbol": symbol,
                        "side": event.side,
                        "outcome": "WIN" if total_pnl > 0 else "LOSS",
                        "pnl": total_pnl,
                        "confidence": pos.confidence if pos else 0,
                        "regime": _rg_fb,
                        "strategy": event.strategy,
                    })
                except Exception as e:
                    logger.debug(f"Learning integrator trade close error: {e}")

                # Thesis grading (2026-07-01): close the thesis directly on trade
                # close. Previously grading only happened inside the coordinator's
                # get_post_trade_lesson (an LLM call) — when that call failed or
                # the notes never carried thesis_id, theses stayed "pending"
                # forever (209/209 ungraded). This direct call grades on price/PnL
                # facts; the coordinator's own close_thesis becomes a no-op after.
                try:
                    _th_er = {}
                    if pos is not None and getattr(pos, "entry_reasons", None):
                        try:
                            import json as _json_th
                            _th_er = (_json_th.loads(pos.entry_reasons)
                                      if isinstance(pos.entry_reasons, str)
                                      else (pos.entry_reasons or {}))
                        except Exception:
                            _th_er = {}
                    _th_notes = str(_th_er.get("llm_notes", "") or "")
                    if "thesis_id=" in _th_notes:
                        _th_id = _th_notes.split("thesis_id=")[1].split(" ")[0].split("|")[0].strip()
                        if _th_id:
                            from llm.brain_wiring import close_thesis as _bw_close_thesis
                            _th_pnl_pct = (total_pnl / self.risk_mgr.equity * 100) if self.risk_mgr.equity > 0 else 0.0
                            _th_mf = None
                            _th_ma = None
                            if pos is not None:
                                if event.side == "LONG":
                                    _th_mf = getattr(pos, "highest_price", None)
                                    _th_ma = getattr(pos, "lowest_price", None)
                                else:
                                    _th_mf = getattr(pos, "lowest_price", None)
                                    _th_ma = getattr(pos, "highest_price", None)
                            _bw_close_thesis(
                                thesis_id=_th_id,
                                exit_price=event.price,
                                pnl_pct=_th_pnl_pct,
                                max_favorable=_th_mf,
                                max_adverse=_th_ma,
                                actual_hold_h=event.metadata.get("hold_time_s", 0) / 3600.0,
                            )
                            logger.info(f"[THESIS] Graded {_th_id} on close: pnl_pct={_th_pnl_pct:+.2f}%")
                except Exception as e:
                    logger.debug(f"Thesis close-grading error: {e}")

                # Multi-Agent Learning: run LLM Learning Agent on each closed trade
                # Previously only ran in backtest — now wired to live loop
                if os.getenv("LLM_MULTI_AGENT", "").lower() in ("1", "true", "yes"):
                    try:
                        from llm.agents.coordinator import get_coordinator
                        _llm_notes_close = (pos.entry_reasons or {}).get("llm_notes", "") if pos else ""
                        _exit_price_close = event.metadata.get("exit_price", 0.0)
                        _hold_h = event.metadata.get("hold_time_s", 0) / 3600.0
                        _ma_lesson = get_coordinator().get_post_trade_lesson({
                            "symbol": symbol,
                            "side": event.side,
                            "outcome": "WIN" if total_pnl > 0 else "LOSS",
                            "pnl": total_pnl,
                            "pnl_pct": (total_pnl / self.risk_mgr.equity * 100) if self.risk_mgr.equity > 0 else 0,
                            "pnl_pct_signed": (total_pnl / self.risk_mgr.equity * 100) if self.risk_mgr.equity > 0 else 0,
                            "confidence": pos.confidence if pos else 0,
                            "regime": _rg_fb,
                            "strategy": event.strategy,
                            "hold_time_s": event.metadata.get("hold_time_s", 0),
                            "hold_hours": _hold_h,
                            "exit_action": event.action,
                            "exit_price": _exit_price_close,
                            "leverage": event.leverage,
                            "entry_type": _et_fb,
                            "notes": _llm_notes_close,  # carries thesis_id= for close_thesis()
                        })
                        if _ma_lesson and isinstance(_ma_lesson, dict):
                            _lesson_txt = _ma_lesson.get("lesson", "") or _ma_lesson.get("insight", "")
                            if _lesson_txt:
                                logger.info(f"[LEARNING-AGENT] {symbol}: {str(_lesson_txt)[:100]}")

                            # Wire lesson into all learning systems (deep_memory, knowledge_base, calibration, etc.)
                            try:
                                from llm.agents.learning_integration import process_agent_lesson
                                # 2026-06-19: add RAW price move % (entry->exit) so regime
                                # calibration can score predicted-vs-actual instead of mirroring
                                # the trade win/loss. Signed: +up / -down.
                                _entry_px = pos.entry if (pos and getattr(pos, "entry", 0)) else 0.0
                                try:
                                    _price_move_pct = ((_exit_price_close - _entry_px) / _entry_px * 100.0) if _entry_px else 0.0
                                except Exception:
                                    _price_move_pct = 0.0
                                # Recover per-agent stated confidences captured at
                                # decision time (entry_reasons may be a JSON string).
                                _er_close = {}
                                if pos is not None and getattr(pos, "entry_reasons", None):
                                    try:
                                        import json as _json_er
                                        _er_close = (_json_er.loads(pos.entry_reasons)
                                                     if isinstance(pos.entry_reasons, str)
                                                     else (pos.entry_reasons or {}))
                                    except Exception:
                                        _er_close = {}
                                _agent_confs_close = _er_close.get("agent_confidences", {}) or {}
                                _trade_data_for_learning = {
                                    "symbol": symbol,
                                    "side": event.side,
                                    "outcome": "WIN" if total_pnl > 0 else "LOSS",
                                    "pnl": total_pnl,
                                    "pnl_pct": (total_pnl / self.risk_mgr.equity * 100) if self.risk_mgr.equity > 0 else 0,
                                    "entry_price": _entry_px,
                                    "exit_price": _exit_price_close,
                                    "price_move_pct": _price_move_pct,
                                    "confidence": pos.confidence if pos else 0,
                                    "agent_confidences": _agent_confs_close,
                                    "regime": _rg_fb,
                                    "strategy": event.strategy,
                                    "notes": _llm_notes_close,
                                }
                                process_agent_lesson(_ma_lesson, _trade_data_for_learning)
                                logger.debug(f"[LEARNING-AGENT] Lesson wired to deep_memory/knowledge/calibration")
                            except Exception as e:
                                logger.debug(f"[LEARNING-AGENT] Integration error: {e}")
                    except Exception as e:
                        logger.debug(f"Multi-agent learning error: {e}")

                # RL buffer: record transition for offline learning
                try:
                    _rl_pos = self.pos_mgr.positions.get(symbol) or _captured_pos
                    _risk_amt = self.risk_mgr.equity * 0.01  # 1% risk base
                    _rl_reward = total_pnl / _risk_amt if _risk_amt > 0 else 0
                    rl_append_transition(
                        state={
                            "symbol": symbol,
                            "regime": _rg_fb,
                            "confidence": _rl_pos.confidence if _rl_pos else 0,
                            "side": event.side,
                            "entry": _rl_pos.entry if _rl_pos else 0,
                            "volatility": _close_vol if '_close_vol' in dir() else 0,
                        },
                        action={
                            "llm_mode": self.llm_mode.name if hasattr(self.llm_mode, 'name') else str(self.llm_mode),
                            "llm_action": _llm_action,
                            "size_multiplier": _rl_pos.entry_reasons.get("llm_size_mult", 1.0) if _rl_pos and _rl_pos.entry_reasons else 1.0,
                            "leverage": event.leverage,
                            "entry_type": _et_fb,
                        },
                        reward=round(_rl_reward, 4),
                        metadata={
                            "trigger": _rl_pos.entry_reasons.get("trigger", "") if _rl_pos and _rl_pos.entry_reasons else "",
                            "hold_time_s": event.metadata.get("hold_time_s", 0),
                            "outcome": "WIN" if total_pnl > 0 else "LOSS",
                            "pnl": round(total_pnl, 2),
                            "strategy": event.strategy,
                        },
                    )
                except Exception as e:
                    logger.debug(f"RL buffer append error: {e}")

                # Self-Tuning Risk: update telemetry on trade close
                try:
                    self.risk_telemetry.update(
                        equity=self.risk_mgr.equity,
                        daily_pnl=self.risk_mgr.circuit_breaker.daily_pnl,
                    )
                except Exception as e:
                    logger.debug(f"Risk telemetry update error: {e}")

                # Survival Pressure: track outcome for LLM accountability
                if _SURVIVAL_PRESSURE_AVAILABLE:
                    try:
                        _funding_cost = 0.0
                        _fr = self._last_funding_rates.get(symbol, 0)
                        if _fr and pos:
                            _hold_h = event.metadata.get("hold_time_s", 0) / 3600
                            _funding_cost = abs(_fr) * pos.leverage * abs(pos.qty) * pos.entry * (_hold_h / 8)
                        survival_record_outcome(
                            outcome="WIN" if total_pnl > 0 else "LOSS",
                            pnl=total_pnl,
                            funding_cost=_funding_cost,
                            equity=self.risk_mgr.equity,
                        )
                    except Exception as e:
                        logger.debug(f"Survival pressure record error: {e}")

                # Adaptive Risk: record outcome for dynamic risk sizing
                if self.adaptive_risk:
                    try:
                        self.adaptive_risk.record_outcome(
                            win=total_pnl > 0,
                            regime=_rg_fb,
                        )
                    except Exception as e:
                        logger.debug(f"Adaptive risk record error: {e}")

                # Adaptive Sizer: record per-symbol outcome for anti-martingale heat tracking
                try:
                    from execution.adaptive_risk import get_adaptive_sizer
                    get_adaptive_sizer(self.config).record_outcome(symbol, won=total_pnl > 0)
                except Exception as e:
                    logger.debug(f"Adaptive sizer record error: {e}")

                # Wave 4: Counterfactual — record exit alternative (TP1 vs TP2)
                if self.counterfactual:
                    try:
                        self.counterfactual.record_exit_alternative(
                            symbol=symbol,
                            actual_exit_action=event.action,
                            actual_exit_price=event.price,
                            tp1_price=pos.tp1 if pos else 0,
                            tp2_price=pos.tp2 if pos else 0,
                            entry_price=pos.entry if pos else 0,
                            actual_pnl=total_pnl,
                        )
                    except Exception as e:
                        logger.debug(f"Counterfactual exit record error: {e}")

                # Wave 4: A/B Testing — record outcome for active experiments
                if self.ab_manager:
                    try:
                        for exp in self.ab_manager.get_active_experiments():
                            _ab_group = self.ab_manager.get_assignment(
                                exp.id, symbol, event.strategy or ""
                            )
                            self.ab_manager.record_outcome(
                                experiment_id=exp.id,
                                group=_ab_group,
                                symbol=symbol,
                                pnl=total_pnl,
                                win=total_pnl > 0,
                                metadata={
                                    "strategy": event.strategy,
                                    "regime": _rg_fb,
                                    "leverage": event.leverage,
                                },
                            )
                    except Exception as e:
                        logger.debug(f"A/B testing outcome record error: {e}")

                # Learning Mode: record trade observation for phase progression
                if _LEARNING_MODE_AVAILABLE and is_learning_mode_active():
                    try:
                        learning_record_trade(
                            symbol=symbol,
                            side=event.side,
                            outcome="WIN" if total_pnl > 0 else "LOSS",
                            pnl=total_pnl,
                            confidence=pos.confidence if pos else 0,
                        )
                    except Exception as e:
                        logger.debug(f"Learning mode trade record error: {e}")

                # Signal Outcome Tracking: log to SQLite for performance scoring
                try:
                    _so_entry = pos.entry if pos else 0
                    _so_conf = pos.confidence if pos else 0
                    _so_pnl_pct = (total_pnl / (self.risk_mgr.equity * self.config.risk_per_trade) * 100) if self.risk_mgr.equity > 0 else 0
                    log_signal_outcome(
                        symbol=symbol,
                        strategy=event.strategy or "",
                        side=event.side,
                        confidence=_so_conf,
                        entry_price=_so_entry,
                        exit_price=event.price,
                        exit_action=event.action,
                        pnl=total_pnl,
                        pnl_pct=_so_pnl_pct,
                        hold_time_s=event.metadata.get("hold_time_s", 0),
                        regime=_rg_fb,
                        leverage=event.leverage,
                        win=total_pnl > 0,
                    )
                except Exception as e:
                    logger.debug(f"Signal outcome tracking error: {e}")

            # Record outcome for ML (use TOTAL trade PnL, not just final leg)
            if self.ml and event.action in _FULL_CLOSE:
                # 2026-07-01: fall back to _captured_pos — the dict entry can be
                # deleted by a parallel symbol's stale cleanup mid-loop (race),
                # which zeroed conf/realized_pnl here ('ML recorded: conf=0%').
                pos = self.pos_mgr.positions.get(symbol) or _captured_pos
                total_pnl = pos.realized_pnl if pos else event.pnl

                # Gather close-time market context for richer ML learning
                _close_vol = 0.0
                _close_pchange_1h = 0.0
                _close_regime = ""
                try:
                    sym_cfg = DEFAULT_SYMBOLS.get(symbol)
                    if sym_cfg:
                        df_1h = self.fetcher.fetch_ohlcv(symbol, sym_cfg.coingecko_id, "1h")
                        if df_1h is not None and not df_1h.empty and len(df_1h) > 14:
                            atr_series = df_1h["close"].rolling(14, min_periods=1).std()
                            _close_vol = float(atr_series.iloc[-1] / df_1h["close"].iloc[-1] * 100) if df_1h["close"].iloc[-1] > 0 else 0.0
                            _close_pchange_1h = float((df_1h["close"].iloc[-1] - df_1h["close"].iloc[-2]) / df_1h["close"].iloc[-2] * 100) if len(df_1h) > 1 else 0.0
                        _close_regime = _rg_fb  # reuse regime from feedback block above
                except Exception as e:
                    logger.debug(f"ML close-context fetch error: {e}")

                self.ml.record_outcome(TradeOutcome(
                    symbol=symbol,
                    strategy=event.strategy,
                    side=event.side,
                    confidence=pos.confidence if pos else 0,
                    leverage=event.leverage,
                    win=total_pnl > 0,
                    pnl=total_pnl,
                    exit_action=event.action,
                    hold_time_s=event.metadata.get("hold_time_s", 0),
                    hour_of_day=datetime.now(timezone.utc).hour,
                    day_of_week=datetime.now(timezone.utc).weekday(),
                    close_volatility=_close_vol,
                    close_price_change_1h_pct=_close_pchange_1h,
                    close_regime=_close_regime,
                ))

            # Learning hooks + enhanced trade log on full closes
            if event.action in _FULL_CLOSE:
                self._symbol_cooldown[symbol] = time.time()
                # 2026-07-01: fall back to _captured_pos (parallel-scan stale-
                # cleanup race) — a None here silently skipped log_closed_trade,
                # i.e. the trades.csv row for the whole close.
                pos = self.pos_mgr.positions.get(symbol) or _captured_pos
                # Per-symbol daily PnL tracking
                if pos:
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if self._symbol_daily_pnl_date != today:
                        self._symbol_daily_pnl = {}
                        self._symbol_daily_pnl_date = today
                    self._symbol_daily_pnl[symbol] = self._symbol_daily_pnl.get(symbol, 0) + pos.realized_pnl
                    if self._symbol_daily_pnl[symbol] <= self._symbol_daily_loss_limit:
                        logger.warning(f"[{symbol}] DAILY LOSS LIMIT HIT: ${self._symbol_daily_pnl[symbol]:.2f} — pausing {symbol} for today")
                # Anti-round-trip: track win/loss and side for cooldown logic
                if pos:
                    self._last_close_win[symbol] = pos.realized_pnl > 0
                    self._last_close_side[symbol] = pos.side
                    # Telemetry: track win/loss and PnL
                    if pos.realized_pnl > 0:
                        Telemetry.inc("trades_won")
                    else:
                        Telemetry.inc("trades_lost")
                    Telemetry.record("pnls", pos.realized_pnl)

                # LLM trigger: position closed -> learn from it
                _close_pnl = pos.realized_pnl if pos else event.pnl
                _close_side = pos.side if pos else event.side
                self._llm_triggers.add(
                    LLMTrigger.POSITION_CLOSED,
                    symbol=symbol,
                    context=f"Closed {_close_side} {symbol} via {event.action} "
                            f"PnL=${_close_pnl:+.2f}",
                )
                if pos:
                    # Extract profile data for logging
                    _et = pos.trade_profile.entry_type if pos.trade_profile else ""
                    _pd = pos.trade_profile.primary_driver if pos.trade_profile else ""
                    _rg = pos.trade_profile.regime if pos.trade_profile else ""
                    _vb = pos.trade_profile.volatility_band if pos.trade_profile else ""

                    # Record to data/analysis/trade_outcomes.csv
                    record_trade_outcome(
                        symbol=symbol,
                        side=event.side,
                        outcome=pos.outcome,
                        pnl=pos.realized_pnl,
                        entry=pos.entry,
                        sl=pos.original_sl,
                        tp1=pos.tp1,
                        tp2=pos.tp2,
                        tp1_hit=pos.filled_tp1,
                        sl_after_tp1=event.action == "SL" and pos.filled_tp1,
                        state_path=pos.state_path_str,
                        leverage=pos.leverage,
                        confidence=pos.confidence,
                        strategy=pos.strategy,
                        entry_reasons=pos.entry_reasons,
                        entry_type=_et,
                        primary_driver=_pd,
                        regime=_rg,
                        volatility_band=_vb,
                    )
                    # Record to data/trades.csv (enhanced)
                    log_closed_trade(
                        symbol=symbol,
                        side=event.side,
                        entry=pos.entry,
                        exit_price=event.price,
                        action=event.action,
                        pnl=pos.realized_pnl,
                        fees=pos.fees_paid,
                        state_path=pos.state_path_str,
                        outcome=pos.outcome,
                        leverage=pos.leverage,
                        confidence=pos.confidence,
                        strategy=pos.strategy,
                        ml_samples_at_entry=0,
                        ml_samples_at_exit=len(self.ml.outcomes) if self.ml else 0,
                        entry_reasons=pos.entry_reasons,
                        entry_type=_et,
                        primary_driver=_pd,
                        regime=_rg,
                        volatility_band=_vb,
                    )

                    # Backfill candidate with realized outcome for dual-world analysis
                    _candidate = self._active_candidates.pop(symbol, None)
                    if _candidate:
                        _candidate.realized_pnl = pos.realized_pnl
                        _hold = event.metadata.get("hold_time_s", 0)
                        _risk = abs(pos.entry - pos.original_sl) if pos.original_sl else 1e-9
                        _candidate.realized_r = pos.realized_pnl / _risk if _risk > 0 else 0
                        _candidate.hold_time_s = _hold
                        _candidate.close_reason = event.action
                        _candidate.outcome = pos.outcome
                        self._candidate_logger.log_candidate(_candidate)

                    # Agent Performance Tracker: record trade outcome for per-agent evaluation
                    if self._agent_perf:
                        try:
                            self._agent_perf.record_outcome(
                                symbol=symbol,
                                pnl=pos.realized_pnl,
                                entry_time=pos.open_time.isoformat() if hasattr(pos, 'open_time') and pos.open_time else "",
                                exit_time=datetime.now(timezone.utc).isoformat(),
                                mfe_pct=pos.max_favorable_pct if hasattr(pos, 'max_favorable_pct') else 0,
                                mae_pct=pos.max_adverse_pct if hasattr(pos, 'max_adverse_pct') else 0,
                                side=pos.side,
                            )
                        except Exception as _ap_err:
                            logger.debug(f"Agent perf outcome error: {_ap_err}")

                    # Cost Optimizer: record PnL outcome for ROI tracking per pipeline type
                    if self._cost_optimizer:
                        try:
                            _co_pipeline = pos.entry_reasons.get("cost_pipeline", "standard") if pos.entry_reasons else "standard"
                            self._cost_optimizer.record_outcome(
                                pipeline_type=_co_pipeline,
                                pnl=pos.realized_pnl,
                            )
                        except Exception as _co_err:
                            logger.debug(f"Cost optimizer outcome error: {_co_err}")

                    # Auto-demotion check: monitor brain health after each close
                    try:
                        from llm.auto_demotion import get_auto_demotion
                        _ad = get_auto_demotion()
                        # Gather recent trades for WR calculation
                        _ad_trades = []
                        try:
                            import csv as _ad_csv
                            _tc_path = os.path.join("data", "trades.csv")
                            if os.path.exists(_tc_path):
                                with open(_tc_path) as _tf:
                                    _reader = _ad_csv.DictReader(_tf)
                                    for _row in _reader:
                                        try:
                                            _ad_trades.append({"pnl": float(_row.get("pnl", 0))})
                                        except (ValueError, TypeError):
                                            pass
                                _ad_trades = _ad_trades[-30:]  # Last 30 trades
                        except Exception:
                            pass
                        # Get cost info
                        _ad_cost = 0.0
                        try:
                            from llm.cost_tracker import get_cost_tracker
                            _ct = get_cost_tracker()
                            _ad_cost = _ct.get_budget_used_pct() * float(os.environ.get("LLM_DAILY_BUDGET_USD", "5"))
                        except Exception:
                            pass
                        # Get drawdown
                        _ad_dd = 0.0
                        try:
                            _peak = max(self.risk_mgr.equity, self.config.starting_equity)
                            _ad_dd = 1.0 - (self.risk_mgr.equity / _peak) if _peak > 0 else 0
                        except Exception:
                            pass
                        _new_mode = _ad.check_after_trade(
                            recent_trades=_ad_trades,
                            daily_cost_usd=_ad_cost,
                            drawdown_pct=_ad_dd,
                        )
                        if _new_mode is not None:
                            self.llm_mode = LLMMode(_new_mode)
                            logger.warning(f"[AUTO-DEMOTION] LLM mode changed to {self.llm_mode.name}")
                    except Exception as _ad_err:
                        logger.debug(f"Auto-demotion check error: {_ad_err}")

                    # Active Learning: run a learning cycle after trade close if due
                    if self._active_learning and self._active_learning.should_run():
                        try:
                            _al_recent = []
                            try:
                                _al_recent = self._active_learning._load_trades(20)
                            except Exception:
                                pass
                            _al_agent_stats = {}
                            if self._agent_perf:
                                try:
                                    _al_agent_stats = self._agent_perf.get_all_stats()
                                except Exception:
                                    pass
                            _al_result = self._active_learning.run_cycle(
                                recent_trades=_al_recent,
                                agent_stats=_al_agent_stats,
                                feedback_states={},
                                rejection_stats={},
                            )
                            logger.info(
                                f"[ACTIVE-LEARN] Cycle: health={_al_result.get('diagnosis', {}).get('overall_health', '?')} "
                                f"hypotheses={_al_result.get('new_hypotheses', 0)} "
                                f"applied={_al_result.get('applications', 0)}"
                            )
                        except Exception as _al_err:
                            logger.debug(f"Active learning cycle error: {_al_err}")

            # Send enhanced trade event alert
            pos = self.pos_mgr.positions.get(symbol) or _captured_pos
            _total_pnl_alert = pos.realized_pnl if pos else event.pnl
            _hold_time_alert = event.metadata.get("hold_time_s", 0)
            try:
                _ds = self.risk_mgr.daily_summary() if hasattr(self.risk_mgr, 'daily_summary') else {}
                # Extract trade quality data for diagnosis
                _entry_reasons = {}
                if pos and hasattr(pos, 'entry_reasons'):
                    try:
                        _entry_reasons = json.loads(pos.entry_reasons) if isinstance(pos.entry_reasons, str) else (pos.entry_reasons or {})
                    except (json.JSONDecodeError, TypeError):
                        _entry_reasons = {}
                _enhanced_msg = format_trade_event_telegram(
                    action=event.action,
                    symbol=symbol,
                    side=event.side,
                    price=event.price,
                    pnl=event.pnl,
                    leverage=event.leverage,
                    total_pnl=_total_pnl_alert if event.action in _FULL_CLOSE else 0,
                    hold_time_s=_hold_time_alert,
                    strategy=event.strategy or "",
                    equity=self.risk_mgr.equity,
                    daily_pnl=self.risk_mgr.circuit_breaker.daily_pnl if hasattr(self.risk_mgr, 'circuit_breaker') else 0,
                    daily_trades=_ds.get("total_trades", 0) if isinstance(_ds, dict) else 0,
                    daily_wins=_ds.get("wins", 0) if isinstance(_ds, dict) else 0,
                    entry_price=pos.entry if pos else 0,
                    original_sl=pos.original_sl if pos and hasattr(pos, 'original_sl') else 0,
                    confidence=pos.confidence if pos else 0,
                    num_agree=_entry_reasons.get("num_agree", 0),
                    ev_per_dollar=_entry_reasons.get("ev_per_dollar", 0),
                    regime=_entry_reasons.get("regime", ""),
                    tp1_hit="TP1" in (pos.state_path_str if pos and hasattr(pos, 'state_path_str') else ""),
                    tp1_price=pos.tp1 if pos and hasattr(pos, 'tp1') else 0,
                    max_favorable_pct=pos.max_favorable_pct if pos and hasattr(pos, 'max_favorable_pct') else 0,
                )
                self.alerts.send_trade_event(event.action, symbol, _enhanced_msg)
            except Exception:
                # Fallback to simple format
                details = (
                    f"{event.action} {event.side} @ {_fmt_price(event.price)}\n"
                    f"PnL: ${event.pnl:+.2f} | Leverage: {event.leverage:.1f}x"
                )
                if event.action in _FULL_CLOSE and pos:
                    details += f"\nTotal PnL: ${pos.realized_pnl:+.2f}"
                self.alerts.send_trade_event(event.action, symbol, details)

            # Check circuit breaker
            if not self.risk_mgr.circuit_breaker.is_trading_allowed():
                reason = self.risk_mgr.circuit_breaker.trip_reason
                self.alerts.send_circuit_breaker(reason)
                # Enhanced Telegram alert with full CB details
                try:
                    cb = self.risk_mgr.circuit_breaker
                    self.telegram_bridge.send_circuit_breaker(
                        reason=reason,
                        daily_pnl=cb.daily_pnl,
                        consecutive_losses=cb.consecutive_losses,
                        cooldown_minutes=cb.cooldown_minutes,
                    )
                except Exception:
                    pass

        # Check leverage liquidation risk on open positions
        open_pos = self.pos_mgr.get_open_positions()
        if symbol in open_pos:
            pos = open_pos[symbol]
            if pos.leverage > 1.0:
                liq_check = self.leverage_mgr.check_liquidation_risk(
                    pos.entry, current_price, pos.side, pos.leverage,
                    safety_buffer=0.03,  # 3% — match the other liq check, not 15% default
                )
                if liq_check["at_risk"]:
                    logger.warning(f"[{symbol}] LIQUIDATION RISK: {liq_check}")
                    _liq_pos2 = self.pos_mgr.positions.get(symbol)
                    if _liq_pos2 and _liq_pos2.qty > 0:
                        close_side = "SELL" if _liq_pos2.side == "LONG" else "BUY"
                        _liq_close2 = self.order_executor.close_position(
                            symbol, close_side, _liq_pos2.qty, current_price,
                            reason="LIQUIDATION_AVOID"
                        )
                        if _liq_close2 and getattr(_liq_close2, "filled", False):
                            event = self.pos_mgr.force_close(symbol, current_price, "LIQUIDATION_AVOID")
                            if event:
                                # Wiring audit #3 (2026-07-01): this force_close runs AFTER the
                                # events loop, so its TradeEvent was used only for the alert and
                                # then dropped — the close never reached equity, trades.csv, or
                                # learning (the largest leveraged losses silently erased).
                                # Inject via _pending_exit_events (same pattern as LLM_EXIT_AGENT)
                                # so the next events-loop pass books it; the stale-cleanup guard
                                # keeps the CLOSED position alive until the event is processed.
                                event.metadata["_exchange_submitted"] = True
                                if not hasattr(self, '_pending_exit_events'):
                                    self._pending_exit_events = []
                                self._pending_exit_events.append(event)
                            if self.alerts and event:
                                self.alerts.send_trade_alert(
                                    f"LIQUIDATION AVOID: {symbol} {event.side} "
                                    f"force-closed @ {current_price}"
                                )
                        else:
                            logger.critical(
                                f"[{symbol}] LIQUIDATION AVOID CLOSE FAILED — position still open. "
                                f"Reconciliation will handle. Result: {_liq_close2}"
                            )

        # Clean up stale closed positions (prevent memory growth overnight)
        # 2026-06-06: skip cleanup if there's a pending Exit Agent close event
        # for that symbol — otherwise the main loop's _captured_pos lookup at
        # line 3149 returns None and the trade_ledger.record_trade silently
        # fails, losing the trade row. (P1 bug: 6+ LLM_EXIT_AGENT closes missing
        # from ledger across the day.) Keep the closed position in dict until
        # its pending event has been processed.
        from execution.position_state import CLOSED as _CLOSED
        _pending_syms = {e.symbol for e in getattr(self, '_pending_exit_events', [])}
        # 2026-07-01 (ledger completeness): only clean up THIS symbol's closed
        # position. With SCAN_PARALLEL_SYMBOLS, this cleanup previously deleted
        # OTHER symbols' CLOSED positions while their (slow, LLM-call-laden)
        # close pipelines were still mid-events-loop — the re-fetched
        # pos_mgr.positions.get(symbol) went None and log_closed_trade/
        # record_trade_outcome silently skipped (9 of 12 closes on 07-01 have
        # no trades.csv row; DB shows 'ML recorded: BTC conf=0%' from the
        # same race). Each symbol's own _process_symbol pass cleans its own
        # position AFTER its events have been processed.
        stale = [s for s, p in self.pos_mgr.positions.items()
                 if s == symbol and p.state == _CLOSED
                 and s not in open_pos and s not in _pending_syms]
        for s in stale:
            del self.pos_mgr.positions[s]

        # Try to generate new signal
        if self.telegram_bot.is_paused:
            return  # Paused via Telegram /pause command
        if self.ops_guard.is_killed:
            return  # Kill switch active

        # If we already have a position in this symbol, still evaluate signals
        # for rotation candidates (other symbols may want to rotate into this one)
        has_position = symbol in open_pos
        at_max_positions = self.pos_mgr.get_open_count() >= self.risk_mgr.max_open_positions

        # If we have a position and rotation is enabled, still evaluate the
        # ensemble so the signal can serve as a rotation candidate for OTHER
        # open positions that might want to rotate into this symbol.
        if has_position and not self.rotation_mgr:
            return  # No rotation, nothing to do

        # Per-symbol daily loss limit: stop trading a symbol that's bleeding
        if not has_position:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._symbol_daily_pnl_date == today:
                sym_pnl = self._symbol_daily_pnl.get(symbol, 0)
                if sym_pnl <= self._symbol_daily_loss_limit:
                    return  # Symbol hit daily loss limit

        # Per-symbol re-entry gating: minimal safety floor + LLM structure check
        # (skip for rotation candidate collection — cooldown is for fresh entries)
        if not has_position:
            last_close = self._symbol_cooldown.get(symbol, 0)
            time_since_close = time.time() - last_close

            # Hard floor: never re-enter within 60s (prevents same-candle re-entry)
            if last_close > 0 and time_since_close < 60:
                return

            # LLM re-entry gate: if LLM is enabled and we're in the post-close
            # window (< 10 min), check Scout data for structure-based clearance.
            # After 10 min, fall back to normal signal flow.
            if (last_close > 0 and time_since_close < 600
                    and should_call_llm(self.llm_mode)):
                if not self._check_llm_reentry_clearance(symbol):
                    return

            # Fallback: if LLM is OFF, use short configurable cooldown
            if not should_call_llm(self.llm_mode):
                was_win = self._last_close_win.get(symbol, False)
                cd = self._win_cooldown_seconds if was_win else self._cooldown_seconds
                if last_close > 0 and time_since_close < cd:
                    return

        # Correlation guard: check before evaluating (saves compute)
        # We need to know the signal direction first, so we do a quick check
        # on existing positions to reject early if same-tier is maxed out
        if not has_position:
            sym_tier = sym_cfg.risk_tier
            tier_count = sum(
                1 for s, p in open_pos.items()
                if DEFAULT_SYMBOLS.get(s) and DEFAULT_SYMBOLS[s].risk_tier == sym_tier
            )
            if tier_count >= self._max_same_tier:
                return  # Too many positions in same risk tier

        # ── Standalone regime classification (runs every tick, before filter) ──
        # Uses regime_trend strategy's proper Wilder ADX + alignment scores —
        # identical logic to backtest/engine.py (lines ~688-716). Replaces the
        # previous volatility-proxy approach (std dev of returns < 1%) which
        # triggered "range" far more aggressively than ADX < 20.
        try:
            _live_regime = None
            # Primary: use quant_regime detector with full 1h candle data
            try:
                from core.quant_regime import detect_regime as _qr_detect
                df_1h = data.get("1h")
                if df_1h is not None and not df_1h.empty and len(df_1h) >= 20:
                    _candles = [
                        {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]}
                        for _, r in df_1h.iterrows()
                    ]
                    _live_regime = _qr_detect(_candles, symbol=symbol)
            except Exception:
                pass

            # Fallback: simple ADX-based from regime_trend strategy
            if not _live_regime or _live_regime == "unknown":
                _rt_strategy = next(
                    (s for s in self.ensemble.strategies if s.name == "regime_trend"), None
                )
                if _rt_strategy is not None:
                    _rt_status = _rt_strategy.get_status(symbol, data)
                    _adx_live = _rt_status.get("adx", 25.0)
                    _al_live = _rt_status.get("align_long", 0)
                    _ash_live = _rt_status.get("align_short", 0)
                    if _al_live >= 3 or _ash_live >= 3:
                        _live_regime = "trend"
                    elif _adx_live < 20:
                        _live_regime = "range"
                    elif _adx_live > 40:
                        _live_regime = "high_volatility"
                    else:
                        _live_regime = "consolidation"

            if _live_regime:
                self.regime_detector.update(symbol, _live_regime)
                self._tick_regime_cache[symbol] = _live_regime
        except Exception:
            pass

        # WAVE2B L1 (F4, FULL_PIPE_BUILD_MAP 2026-07-02): per-symbol collector
        # for mechanical would-have verdicts. Routers demoted below record
        # their opinions here; the dict rides into market_ctx["mech_opinion"]
        # (formatted for prompts by L4's format_mech_opinion) so Claude sees
        # the mechanical layer's analysis as labeled context, not enforcement.
        if not hasattr(self, '_mech_opinion'):
            self._mech_opinion = {}
        self._mech_opinion[symbol] = {}

        # ── Wave 2B L1 (R21d): FIT verdicts annotate, never disable ──
        # STRATEGY_REGIME_FIT "avoid" used to disable strategies before they
        # could vote (static theory, no n — OPINION per FULL_PIPE_BUILD_MAP).
        # Doctrine: strategies always vote; the fitness table's verdict rides
        # along as labeled metadata (set_fit_avoid_annotations, WAVE2A L3).
        # Kill-switch: ROUTER_FIT_DISABLE_ENFORCE=true restores legacy disable.
        if self.config.enable_regime_strategy_filter:
            _fit_enforce = os.getenv("ROUTER_FIT_DISABLE_ENFORCE", "false").lower() == "true"
            try:
                _cur_regime = self._tick_regime_cache.get(symbol) or self.regime_detector.get_regime(symbol)
                if _cur_regime:
                    from llm.agents.shared_context import STRATEGY_REGIME_FIT
                    _fit = STRATEGY_REGIME_FIT.get(_cur_regime, {})
                    _avoid = {_sname for _sname, _sfit in _fit.items() if _sfit == "avoid"}

                    # Dynamic WR-based disabling skipped to match backtest behavior.
                    # Will re-enable once sufficient paper trading data is collected.
                    # Original: disable strategies with <35% WR over 10+ trades via deep_memory.

                    if _avoid and _fit_enforce:
                        # Legacy enforcement path (kill-switch only)
                        self.ensemble.set_disabled_strategies(_avoid)
                        logger.info(
                            f"[{trace_id}][{symbol}] Regime filter (ENFORCED by "
                            f"ROUTER_FIT_DISABLE_ENFORCE): disabled {_avoid} "
                            f"in {_cur_regime} regime"
                        )
                    else:
                        self.ensemble.set_disabled_strategies(set())
                        try:
                            self.ensemble.set_fit_avoid_annotations({
                                _sname: f"avoid in {_cur_regime} (static theory, no n)"
                                for _sname in _avoid
                            })
                        except Exception:
                            pass
                        if _avoid:
                            logger.info(
                                f"[{trace_id}][{symbol}] [SHADOW-ROUTER] R21d FIT table "
                                f"would disable {sorted(_avoid)} in {_cur_regime} — "
                                f"annotating votes instead (not enforced)"
                            )
                            self._mech_opinion[symbol]["fit_avoid"] = {
                                "strategies": sorted(_avoid),
                                "regime": _cur_regime,
                                "label": "static theory, no n",
                            }
                    # Set regime for regime-aware min_votes
                    self.ensemble.set_regime(symbol, _cur_regime)
                else:
                    self.ensemble.set_disabled_strategies(set())
                    self.ensemble.set_regime(symbol, "unknown")
            except Exception:
                self.ensemble.set_disabled_strategies(set())
                self.ensemble.set_regime(symbol, "unknown")

        # ── 4h regime confirmation (B2) ──
        # Uses proper Wilder ADX on 4h data (same threshold as 1h: < 20 = range).
        try:
            from strategies.regime_trend import _adx as _adx_fn
            df_4h = data.get("4h")
            if df_4h is not None and not df_4h.empty and len(df_4h) > 20:
                _adx_4h = _adx_fn(df_4h, 14)
                _returns_4h = df_4h["close"].pct_change().tail(10)
                _dir_4h = abs(float(_returns_4h.mean())) / max(float(_returns_4h.std()), 1e-9)

                if _adx_4h > 40:
                    _regime_4h = "high_volatility"
                elif _adx_4h > 25 and _dir_4h > 0.5:
                    _regime_4h = "trend"
                elif _adx_4h < 20:
                    _regime_4h = "range"
                else:
                    _regime_4h = "consolidation"

                self.ensemble.set_regime_4h(symbol, _regime_4h)
        except Exception:
            pass

        # Pipeline telemetry: start tracking this signal's journey
        try:
            from core.pipeline_telemetry import get_telemetry as _get_pt
            _get_pt().start_journey(symbol, "pending")
        except Exception:
            pass

        # HYBRID MODE: Mechanical consensus (min_votes=2) is the primary path.
        # But when LLM is active, ALSO check for proven solo setups that the
        # LLM can evaluate. This lets the LLM take trades mechanical blocks.
        signal_result = self.ensemble.evaluate(symbol, data)  # Always run mechanical first

        # If mechanical returned None (solo signal or sub-consensus), route to
        # the LLM-first pathway when LLM_FIRST_MODE is active.
        #
        # In legacy mode (LLM_FIRST_MODE=false), only a hardcoded whitelist of
        # proven-solo setups bypass the consensus gate. In LLM-first mode, we
        # let the LLM see every ≥60% solo signal — the cost gate at line 4147
        # and the 10-min cooldown at line 4164 still protect the API budget.
        # Hard safety (circuit breakers, position limits, liquidation, notional
        # cap) still applies downstream via SafetyFilterChain + post-LLM caps.
        if signal_result is None and self.llm_mode >= LLMMode.SIZING:
            _raw = self.ensemble.evaluate_raw(symbol, data)
            if _raw is not None:
                _base = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
                _llm_first_active = getattr(self.config, 'llm_first_mode', False)
                if _llm_first_active:
                    # WAVE2B L1 (R3, FULL_PIPE_BUILD_MAP): the solo conf>=60
                    # gate is DEAD by default — ensemble confidence has IC≈0
                    # vs outcomes (FALLACY_AUDIT D4), so routing on it is
                    # opinion, not physics. EVERY non-None evaluate_raw result
                    # dispatches; confidence rides in the prompt as raw
                    # context. Quota physics = 10-min cooldown + entry cache.
                    # Kill-switch: ROUTER_SOLO_CONF_ENFORCE=true restores the
                    # 60% floor (the May-31 classic's surviving twin).
                    _solo_enforce = os.getenv("ROUTER_SOLO_CONF_ENFORCE", "false").lower() == "true"
                    _spam_key = f"{_base}_{_raw.side}"
                    if not hasattr(self, '_llm_first_solo_log_ts'):
                        self._llm_first_solo_log_ts = {}
                    _last_log = self._llm_first_solo_log_ts.get(_spam_key, 0)
                    _log_novel = time.time() - _last_log >= 600  # match cooldown
                    if _raw.confidence < 60:
                        self._mech_opinion[symbol]["solo_conf_gate"] = {
                            "conf": round(float(_raw.confidence), 1),
                            "threshold": 60,
                            "would_divert": True,
                            "label": "IC≈0 stat — not enforced",
                        }
                        if _log_novel:
                            logger.info(
                                f"[{symbol}] [SHADOW-ROUTER] R3 would-divert solo: "
                                f"{_base} {_raw.side} conf={_raw.confidence:.0f}% < 60% "
                                f"({'ENFORCED' if _solo_enforce else 'not enforced — dispatching to LLM'})"
                            )
                    if _raw.confidence >= 60 or not _solo_enforce:
                        signal_result = _raw
                        if signal_result.metadata is None:
                            signal_result.metadata = {}
                        signal_result.metadata["llm_solo_evaluation"] = True
                        signal_result.metadata["bypassed_consensus"] = True
                        # Only log the first occurrence per cooldown window.
                        # Matches the LLM-first cooldown key at line ~4164.
                        if _log_novel:
                            logger.info(
                                f"[{symbol}] LLM-FIRST solo → LLM: {_base} {_raw.side} "
                                f"conf={_raw.confidence:.0f}% num_agree=1 (LLM decides)"
                            )
                            self._llm_first_solo_log_ts[_spam_key] = time.time()
                # WAVE2B L1 (R4): the legacy _PROVEN_SOLOS whitelist branch
                # (BTC SELL / ETH BUY / SOL SELL, conf>=65) is DELETED — its
                # stats came from the condemned ledger (M20: "100% WR on 135
                # shadow signals") and it only executed when llm_first_mode
                # silently flipped off, i.e. exactly when nobody was watching.
                # LLM-first supersedes it; mechanical fallback execution is
                # now gated by ALLOW_MECHANICAL_FALLBACK (R5).

        # ── EARLY: Sniper Signal Evaluation (before regime gating can null the signal) ──
        # Route ALL raw strategy signals to sniper, even if ensemble rejected them.
        # The sniper has its own proven-setup gates and can trade what the bot sits out on.
        if self._manual_sniper is not None:
            _raw_sigs = getattr(self.ensemble, '_last_raw_signals', {}).get(symbol, [])
            # Inject funding rate into raw signal metadata for quant brain + sniper.
            # Funding is a structural edge signal that most retail ignores.
            _fr_for_sniper = self._last_funding_rates.get(symbol)
            for _raw_sig in _raw_sigs:
                if _fr_for_sniper is not None:
                    _sig_meta = getattr(_raw_sig, 'metadata', None)
                    if _sig_meta is None:
                        _raw_sig.metadata = {}
                        _sig_meta = _raw_sig.metadata
                    if isinstance(_sig_meta, dict):
                        _sig_meta["funding_rate"] = _fr_for_sniper
                try:
                    # WAVE2B L1 (R20b): QuantBrain pre-filter for sniper raw
                    # signals DELETED — QB is an owner-ruled anti-signal
                    # (17% WR era) that `continue`d signals out of the channel
                    # with no counterfactual logged (FALLACY_AUDIT D16). Inert
                    # while QUANT_BRAIN_ENABLED=false but armed-when-flag-flips;
                    # removed rather than left loaded. QB stats may re-enter
                    # later as labeled context per THE_STANDARD §2b.
                    _sniper_sig = self._manual_sniper.evaluate(_raw_sig, equity=self.risk_mgr.equity)
                    if _sniper_sig is not None:
                        if self._sniper_simulator is not None:
                            try:
                                _sim_pos = self._sniper_simulator.on_signal(_sniper_sig)
                                if _sim_pos:
                                    logger.info(
                                        f"[SIM] OPENED {_sim_pos.trade_id} {_sim_pos.symbol} "
                                        f"{_sim_pos.side} @ ${_sim_pos.entry:.2f} "
                                        f"size=${_sim_pos.position_size_usd:.2f} "
                                        f"lev={_sim_pos.leverage:.0f}x"
                                    )
                            except Exception as _sim_err:
                                logger.warning(f"[SIM] Error: {_sim_err}")
                        if self._manual_alerter is not None:
                            self._manual_alerter.send_sniper_alert(_sniper_sig, equity=self.risk_mgr.equity)
                        if hasattr(self, '_signal_tracker') and self._signal_tracker is not None:
                            try:
                                self._signal_tracker.record_signal(_sniper_sig)
                            except Exception:
                                pass
                        if self._sniper_auto_execute and _sniper_sig.tier in ("SNIPER", "PREMIUM"):
                            try:
                                self._execute_sniper_signal(_sniper_sig, symbol, current_price)
                            except Exception as _sae_err:
                                logger.error(f"[SNIPER-EXEC] Error: {_sae_err}")
                except Exception:
                    pass

        # Also evaluate the consensus signal if it exists
        if signal_result is not None and self._manual_sniper is not None:
            # WAVE2B L1 (R20b): QuantBrain consensus pre-filter DELETED (same
            # rationale as the raw-signal block above — anti-signal stat,
            # no counterfactual, armed-when-flag-flips).
            if True:
                try:
                    _sniper_sig = self._manual_sniper.evaluate(
                        signal_result, equity=self.risk_mgr.equity
                    )
                    if _sniper_sig is not None:
                        if self._manual_alerter is not None:
                            self._manual_alerter.send_sniper_alert(
                                _sniper_sig, equity=self.risk_mgr.equity
                            )
                        if self._sniper_simulator is not None:
                            try:
                                _sim_pos = self._sniper_simulator.on_signal(_sniper_sig)
                                if _sim_pos:
                                    logger.info(
                                        f"[SIM] Opened {_sim_pos.trade_id} {_sim_pos.symbol} "
                                        f"{_sim_pos.side} @ ${_sim_pos.entry:.2f} "
                                        f"size=${_sim_pos.position_size_usd:.2f}"
                                    )
                            except Exception as _sim_err:
                                logger.warning(f"[SIM] Error on signal: {_sim_err}")
                        # Signal Value Tracker
                        if hasattr(self, '_signal_tracker') and self._signal_tracker is not None:
                            try:
                                self._signal_tracker.record_signal(_sniper_sig)
                            except Exception:
                                pass
                        # Sniper Auto-Execute
                        if self._sniper_auto_execute and _sniper_sig.tier in ("SNIPER", "PREMIUM"):
                            try:
                                self._execute_sniper_signal(_sniper_sig, symbol, current_price)
                            except Exception as _sae_err:
                                logger.error(f"[SNIPER-EXEC] Error executing {symbol}: {_sae_err}")
                except Exception as _sniper_err:
                    logger.warning(f"[SNIPER-EARLY] Error evaluating {symbol}: {_sniper_err}")

        # ── TIER 3b: Quant Brain Signal Generation ──
        # The quant brain doesn't just filter — it FINDS opportunities using
        # research-validated setups (mean reversion, divergence, squeeze breakout)
        # that individual strategies may miss. Feeds directly to sniper/sim.
        if self._quant_brain is not None and self._manual_sniper is not None:
            try:
                _default_syms = getattr(self, '_default_symbols', None)
                if _default_syms is None:
                    from config import DEFAULT_SYMBOLS as _default_syms
                for _qb_sym in self.config.symbols:
                    _qb_data = {}
                    _qb_cfg = _default_syms.get(_qb_sym) if _default_syms else None
                    if _qb_cfg:
                        for _qb_tf in ["1h", "6h"]:
                            try:
                                _qb_df = self.fetcher.fetch_ohlcv(_qb_sym, _qb_cfg.coingecko_id, _qb_tf)
                                if _qb_df is not None and not _qb_df.empty:
                                    _qb_data[_qb_tf] = _qb_df
                            except Exception:
                                pass
                    if not _qb_data:
                        continue
                    _qb_signals = self._quant_brain.generate_signals(_qb_sym, _qb_data)
                    for _qb_sig_dict in _qb_signals:
                        try:
                            from strategies.base import Signal
                            _qb_signal = Signal(
                                strategy=f"quant_brain_{_qb_sig_dict['type']}",
                                symbol=_qb_sig_dict["symbol"],
                                side=_qb_sig_dict["side"],
                                confidence=_qb_sig_dict["confidence"],
                                entry=_qb_sig_dict["entry"],
                                sl=_qb_sig_dict["sl"],
                                tp1=_qb_sig_dict["tp1"],
                                tp2=_qb_sig_dict.get("tp2", _qb_sig_dict["tp1"]),
                                atr=_qb_sig_dict.get("atr", 0),
                                metadata={
                                    "num_agree": 1,
                                    "strategies_agree": [f"quant_brain_{_qb_sig_dict['type']}"],
                                    "regime": "quant_brain",
                                    "quant_brain_generated": True,
                                    "reasoning": _qb_sig_dict.get("reasoning", ""),
                                },
                            )
                            _qb_sniper = self._manual_sniper.evaluate(_qb_signal)
                            if _qb_sniper is not None:
                                logger.info(
                                    f"[QUANT-BRAIN-GEN] {_qb_sig_dict['symbol']} {_qb_sig_dict['side']} "
                                    f"{_qb_sig_dict['type']} conf={_qb_sig_dict['confidence']}% "
                                    f"lev={_qb_sig_dict.get('leverage_suggestion', '?')}x"
                                )
                                if self._sniper_simulator is not None:
                                    _sim_pos = self._sniper_simulator.on_signal(_qb_sniper)
                                    if _sim_pos:
                                        logger.info(
                                            f"[SIM] Quant brain opened {_sim_pos.trade_id} "
                                            f"{_sim_pos.symbol} {_sim_pos.side}"
                                        )
                        except Exception as _qb_inner:
                            logger.debug(f"[QUANT-BRAIN-GEN] Error: {_qb_inner}")
            except Exception as _qb_err:
                logger.debug(f"[QUANT-BRAIN-GEN] Scan error: {_qb_err}")

        # ── TIER 4: Mechanical Bot Instrumentation (Signal Generation Hook) ──
        # Record signal generation with full market context for perception system
        if _MECHANICAL_BOT_INSTRUMENTATION_AVAILABLE and signal_result is not None:
            try:
                instr = get_mechanical_bot_instrumentation()

                # Capture the signal with all market context
                market_context = {
                    'regime': signal_result.metadata.get("regime", "unknown"),
                    'volatility': signal_result.metadata.get("volatility", 0.0),
                    'alignment': signal_result.metadata.get("alignment", 0.0),
                    'btc_correlation': signal_result.metadata.get("btc_correlation", 0.0),
                    'num_strategies_agree': signal_result.metadata.get("num_agree", 1),
                    'timestamp': time.time(),
                }

                # Record signal with all context
                signal_id = instr.on_signal_generated(
                    symbol=symbol,
                    side=signal_result.side,
                    confidence=signal_result.confidence,
                    entry=signal_result.entry,
                    sl=signal_result.sl,
                    tp1=signal_result.tp1,
                    tp2=signal_result.tp2,
                    strategy_names=signal_result.metadata.get("strategy_names", []),
                    num_strategies=signal_result.metadata.get("num_agree", 1),
                    market_context=market_context
                )

                # Store signal_id in metadata for later reference (when position opens/closes)
                if not hasattr(signal_result, 'metadata'):
                    signal_result.metadata = {}
                signal_result.metadata['mechanical_signal_id'] = signal_id

            except Exception as e:
                logger.debug(f"[{symbol}] Mechanical bot instrumentation error (signal generation): {e}")

        # ── TIER 1.2: Regime-Specific Confidence Floor (LLM-side improvement) ──
        # Apply regime-specific confidence floors to filter noisy signals in poor regimes.
        # This is a pure LLM improvement that doesn't modify the mechanical system.
        # Can be disabled via env var REGIME_FLOOR_GATING=false
        if signal_result is not None:
            try:
                _regime_gating_enabled = os.getenv("REGIME_FLOOR_GATING", "true").lower() == "true"
                if _regime_gating_enabled:
                    from llm.signal_gating import get_signal_gater
                    regime = signal_result.metadata.get("regime", "unknown")
                    gater = get_signal_gater()
                    gating_result = gater.gate_signal(signal_result, regime)
                    # WAVE2B L1 (R6): the gater has no enforcing branch (verified
                    # — only this block consumes gating_result). Its verdict is
                    # no longer a dead log: it rides into mech_opinion + signal
                    # metadata as labeled context for the LLM.
                    _rf_verdict = {
                        "would_reject": not gating_result.approved,
                        "conf": round(float(gating_result.signal_confidence), 1),
                        "floor": round(float(gating_result.floor_applied), 1),
                        "regime": regime,
                    }
                    self._mech_opinion.setdefault(symbol, {})["regime_floor"] = _rf_verdict
                    if signal_result.metadata is not None:
                        signal_result.metadata["mech_opinion_regime_floor"] = _rf_verdict
                    if not gating_result.approved:
                        logger.info(
                            f"[{symbol}] Regime floor would reject (bypassed): "
                            f"confidence={gating_result.signal_confidence:.0f}% < "
                            f"floor={gating_result.floor_applied:.0f}% "
                            f"(regime={regime})"
                        )
                        # Not enforced: verdict passed to LLM as context above
            except Exception as e:
                logger.debug(f"[{symbol}] Regime floor gating error: {e}")
                # If gating fails, allow signal to proceed (fail-safe)

        # ── Soft-filter annotation: run annotated ensemble in parallel ──
        # When enabled, this captures filter assessments for LLM visibility
        # even when the signal passes hard filters normally.
        if getattr(self.config, 'enable_soft_filters', False) or getattr(self.config, 'soft_filter_log_only', False):
            try:
                annotated_ensemble = self.ensemble.evaluate_with_annotations(symbol, data)
                if annotated_ensemble is not None:
                    # Store for LLM snapshot injection and signal tracking
                    if not hasattr(self, '_pending_annotations'):
                        self._pending_annotations = {}
                    self._pending_annotations[symbol] = annotated_ensemble

                    # Track signal in signal tracker (all signals, not just approved)
                    from core.signal_tracker import get_signal_tracker
                    tracker = get_signal_tracker()
                    tracker.record_signal(
                        symbol=symbol,
                        side=annotated_ensemble.signal.side if hasattr(annotated_ensemble.signal, 'side') else "",
                        confidence=annotated_ensemble.signal.confidence if hasattr(annotated_ensemble.signal, 'confidence') else 0,
                        strategy=annotated_ensemble.signal.strategy if hasattr(annotated_ensemble.signal, 'strategy') else "",
                        passed=annotated_ensemble.passed_all,
                        hard_rejected=annotated_ensemble.hard_rejected,
                        hard_rejection_reason=annotated_ensemble.hard_rejection_reason,
                        annotations=[
                            {"gate": a.gate, "severity": a.severity, "value": a.value, "threshold": a.threshold}
                            for a in annotated_ensemble.annotations
                        ],
                        filter_metadata=annotated_ensemble.filter_metadata,
                        num_strategies_agree=annotated_ensemble.filter_metadata.get("num_strategies_signaled", 0),
                        regime=annotated_ensemble.filter_metadata.get("regime", ""),
                    )
            except Exception as e:
                logger.debug(f"[{symbol}] Soft-filter annotation error: {e}")

        # Update last snapshot with ensemble context for ML learning
        if self.ml and self.ml.snapshots:
            last_snap = self.ml.snapshots[-1]
            if last_snap.symbol == symbol:
                if signal_result:
                    last_snap.ensemble_direction = signal_result.side
                    last_snap.ensemble_confidence = signal_result.confidence

        if signal_result is None:
            return

        # ══════════════════════════════════════════════════════════════════
        # ── LLM-FIRST MODE: bypass 47 mechanical gates, let LLM decide ──
        # ══════════════════════════════════════════════════════════════════
        _llm_first = getattr(self.config, 'llm_first_mode', False)
        _llm_dual_track = getattr(self.config, 'llm_first_dual_track', False)

        # WAVE2B L1 (R7, FULL_PIPE_BUILD_MAP): the min(60, floor) LLM-divert is
        # DEAD by default. It was the direct descendant of the May-31
        # `conf<60 → _llm_first=False` classic — an OPINION router keyed on a
        # confidence stat with IC≈0 vs outcomes (D4), dressed up as a "cost
        # gate". Quota gates count calls; they don't judge signals (the 10-min
        # cooldown + entry cache below are the real quota physics). Confidence
        # is already in signal_ctx for the LLM to weigh.
        # Kill-switch: ROUTER_CONF_DIVERT_ENFORCE=true restores the divert.
        _sig_conf = signal_result.confidence if hasattr(signal_result, 'confidence') else 0
        _llm_first_min = min(60.0, float(self.config.ensemble_confidence_floor))
        if _sig_conf < _llm_first_min:
            if os.getenv("ROUTER_CONF_DIVERT_ENFORCE", "false").lower() == "true":
                logger.info(
                    f"[{trace_id}][{symbol}] LLM SKIP (ENFORCED by ROUTER_CONF_DIVERT_ENFORCE): "
                    f"confidence {_sig_conf:.0f}% < {_llm_first_min:.0f}% threshold"
                )
                # Legacy: fall through to mechanical path (no LLM cost incurred)
                _llm_first = False
                _llm_dual_track = False
            else:
                logger.info(
                    f"[{trace_id}][{symbol}] [SHADOW-ROUTER] R7 would-divert: "
                    f"confidence {_sig_conf:.0f}% < {_llm_first_min:.0f}% — "
                    f"dispatching to LLM anyway (not enforced)"
                )
                if hasattr(self, '_mech_opinion'):
                    self._mech_opinion.setdefault(symbol, {})["conf_divert_gate"] = {
                        "conf": round(float(_sig_conf), 1),
                        "threshold": round(float(_llm_first_min), 1),
                        "would_divert": True,
                        "label": "IC≈0 stat — not enforced",
                    }

        if _llm_first and self.llm_mode >= LLMMode.SIZING:
            _base_sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")

            # Cooldown: don't re-evaluate same symbol+side within 5 minutes
            # Prevents burning credits on the same signal firing every 60s
            _llm_eval_key = f"{_base_sym}_{signal_result.side}"
            if not hasattr(self, '_llm_eval_cooldowns'):
                self._llm_eval_cooldowns = {}
            _cd_entry = self._llm_eval_cooldowns.get(_llm_eval_key, (0.0, None))
            if isinstance(_cd_entry, (int, float)):  # legacy float format
                _cd_entry = (float(_cd_entry), None)
            _last_eval, _last_eval_price = _cd_entry
            _cd_elapsed = time.time() - _last_eval
            if _cd_elapsed < 600:  # 10 minute cooldown to conserve credits (quota physics)
                # WAVE2B L1 (R9 defect b): price-move escape hatch mirroring
                # coordinator._entry_cache_price_tolerance (0.3%) — a genuinely
                # new setup on the same side within 10min (price moved) is NOT
                # the same signal and must not be dropped.
                _price_moved_pct = None
                if _last_eval_price and current_price and _last_eval_price > 0:
                    _price_moved_pct = abs(float(current_price) - _last_eval_price) / _last_eval_price
                if _price_moved_pct is not None and _price_moved_pct >= 0.003:
                    logger.info(
                        f"[{trace_id}][{symbol}] [COOLDOWN-ESCAPE] {_llm_eval_key} "
                        f"price moved {_price_moved_pct * 100:.2f}% >= 0.30% since last "
                        f"eval — re-evaluating within cooldown (new setup)"
                    )
                else:
                    # WAVE2A L3 (R9 defect a): the cooldown stays (quota
                    # physics) but the drop must never be silent.
                    logger.info(
                        f"[{trace_id}][{symbol}] [COOLDOWN-DROP] {_llm_eval_key} "
                        f"conf={_sig_conf:.0f}% dropped by 10-min LLM eval cooldown "
                        f"({_cd_elapsed:.0f}s since last eval, "
                        f"{600 - _cd_elapsed:.0f}s remaining)"
                    )
                    # WAVE2B L1 (R9 defect a remainder): stage-tag the drop in
                    # the signal tracker so it's measurable, not just grep-able.
                    try:
                        from core.signal_tracker import get_signal_tracker
                        get_signal_tracker().record_signal(
                            symbol=symbol,
                            side=signal_result.side,
                            confidence=float(_sig_conf),
                            strategy=getattr(signal_result, 'strategy', '') or '',
                            passed=False,
                            hard_rejected=False,
                            hard_rejection_reason="llm_cooldown",
                            filter_metadata={
                                "stage": "llm_cooldown",
                                "cooldown_remaining_s": round(600 - _cd_elapsed),
                            },
                            regime=(signal_result.metadata or {}).get("regime", "") if getattr(signal_result, 'metadata', None) else "",
                        )
                    except Exception:
                        pass
                    return  # Already evaluated this setup recently (drop logged above)
            self._llm_eval_cooldowns[_llm_eval_key] = (
                time.time(),
                float(current_price) if current_price else None,
            )

            try:
                self._process_symbol_llm_first(
                    symbol, sym_cfg, signal_result, data, open_pos,
                    current_price, trace_id,
                )
            except Exception as e:
                logger.error(
                    f"[{trace_id}][{symbol}] LLM-FIRST pipeline error: {e} "
                    f"— falling back to mechanical path"
                )
                # Fall through to mechanical path on failure
            else:
                return  # LLM-first handled it (or skipped it)

        # WAVE2B L1 (R5): degraded-brain guard. If llm_first was CONFIGURED but
        # its prerequisites lapsed at startup (llm_first_degraded=true), the
        # mechanical path may annotate/track but must NOT execute without
        # explicit owner consent (ALLOW_MECHANICAL_FALLBACK=true). Bots that
        # were never llm_first-configured are unaffected.
        if getattr(self, 'llm_first_degraded', False) and \
                os.getenv("ALLOW_MECHANICAL_FALLBACK", "false").lower() != "true":
            _now_dg = time.time()
            if _now_dg - getattr(self, '_degraded_drop_last_log', 0.0) > 600:
                self._degraded_drop_last_log = _now_dg
                logger.warning(
                    f"[{trace_id}][{symbol}] [SHADOW-ROUTER] R5 degraded-brain: "
                    f"mechanical execution blocked (llm_first degraded, "
                    f"ALLOW_MECHANICAL_FALLBACK=false) — signal tracked, not traded"
                )
            try:
                from core.signal_tracker import get_signal_tracker
                get_signal_tracker().record_signal(
                    symbol=symbol,
                    side=signal_result.side,
                    confidence=float(_sig_conf),
                    strategy=getattr(signal_result, 'strategy', '') or '',
                    passed=False,
                    hard_rejected=False,
                    hard_rejection_reason="llm_first_degraded",
                    filter_metadata={"stage": "llm_first_degraded"},
                )
            except Exception:
                pass
            return

        # Dual-track: run LLM-first path in background for comparison logging
        # but still execute through the mechanical path below.
        if _llm_dual_track and self.llm_mode >= LLMMode.SIZING:
            try:
                # Get raw signal for LLM pipeline
                _raw_sig = self.ensemble.evaluate_raw(symbol, data)
                if _raw_sig is not None:
                    self._log_dual_track_llm_decision(
                        symbol, _raw_sig, data, open_pos, current_price, trace_id,
                    )
            except Exception as e:
                logger.debug(f"[{trace_id}][{symbol}] Dual-track LLM error: {e}")

        # ── WAVE2B L1 (R20a): QUANT BRAIN PRE-FILTER DELETED (2026-07-02) ──
        # QB veto/skip nulled signal_result and its "go" branch mutated
        # confidence on the mechanical path — an owner-ruled anti-signal
        # (17% WR era) judging signal quality (FULL_PIPE_BUILD_MAP R20a).
        # Inert while QUANT_BRAIN_ENABLED=false (_quant_brain is None), but
        # armed-when-flag-flips — deleted rather than left loaded. QB stats
        # may re-enter later as labeled prompt context per THE_STANDARD §2b.

        if signal_result is None:
            return

        side = "LONG" if signal_result.side == "BUY" else "SHORT"

        Telemetry.inc("total_signals")

        # Signal dedup: skip if we just saw the same side signal for this symbol
        now = time.time()
        last_sig = self._last_signal.get(symbol)
        if last_sig and last_sig[0] == signal_result.side:
            elapsed = now - last_sig[1]
            if elapsed < self._signal_dedup_seconds:
                return  # same signal, skip silently
        self._last_signal[symbol] = (signal_result.side, now)

        # (Sniper evaluation moved earlier — before regime gating — to see all signals)

        # LLM triggers: detect meaningful decision boundaries
        num_agree = signal_result.metadata.get("num_agree", 1)

        # High-confidence signal trigger (>=75%)
        # Skip if symbol was recently slippage-rejected (stale signal, don't waste LLM)
        _slip_cd = self._slippage_reject_cooldown.get(symbol, 0)
        _in_slip_cooldown = (time.time() - _slip_cd) < 300
        if signal_result.confidence >= 75 and not _in_slip_cooldown:
            self._llm_triggers.add(
                LLMTrigger.HIGH_CONFIDENCE,
                symbol=symbol,
                context=f"{symbol} {signal_result.side} signal "
                        f"conf={signal_result.confidence:.0f}%",
            )

        # Strategy consensus trigger (3+ agree)
        if num_agree >= 3:
            strategies = signal_result.metadata.get("strategies_agree", [])
            self._llm_triggers.add(
                LLMTrigger.STRATEGY_CONSENSUS,
                symbol=symbol,
                context=f"{num_agree} strategies agree on {symbol} "
                        f"{signal_result.side}: {strategies}",
            )

        # Regime shift detection (from strategy metadata) + transition tracking
        current_regime = signal_result.metadata.get("regime", "")
        if current_regime:
            # E2: Feed regime detector for transition detection
            regime_result = self.regime_detector.update(symbol, current_regime)
            if regime_result["transitioning"]:
                signal_result.metadata["regime_transition"] = {
                    "from": regime_result["from_regime"],
                    "to": regime_result["to_regime"],
                    "confidence": regime_result["confidence"],
                }
                # Fire explicit LLM trigger on regime transition
                self._llm_triggers.add(
                    LLMTrigger.REGIME_SHIFT,
                    symbol=symbol,
                    context=f"Regime transition: {regime_result.get('from_regime', '?')} -> "
                            f"{regime_result.get('to_regime', '?')} for {symbol}",
                )
            if self._llm_triggers.check_regime_shift(symbol, current_regime):
                _transition_ctx = ""
                if regime_result.get("transitioning"):
                    _transition_ctx = (
                        f" (transition: {regime_result['from_regime']}->"
                        f"{regime_result['to_regime']} "
                        f"conf={regime_result['confidence']:.0%})"
                    )
                self._llm_triggers.add(
                    LLMTrigger.REGIME_SHIFT,
                    symbol=symbol,
                    context=f"{symbol} regime shifted to '{current_regime}'{_transition_ctx}",
                )

        # Strategy disagreement detection (2 long vs 2 short, or high-conf outlier)
        try:
            statuses = self.ensemble.get_all_status(symbol, data)
            strat_signals = {}
            strat_confs = {}
            for s in statuses:
                strat_name = s.get("strategy", "unknown")
                action = s.get("action", s.get("side", "neutral"))
                if action in ("BUY", "buy"):
                    strat_signals[strat_name] = "long"
                elif action in ("SELL", "sell"):
                    strat_signals[strat_name] = "short"
                else:
                    strat_signals[strat_name] = "neutral"
                conf = s.get("confidence", 0)
                if conf == 0:
                    align_l = s.get("align_long", 0)
                    align_s = s.get("align_short", 0)
                    conf = max(align_l, align_s) / 100.0 if max(align_l, align_s) > 1 else max(align_l, align_s)
                strat_confs[strat_name] = min(conf, 1.0)
            disagreement = self._llm_triggers.check_strategy_disagreement(
                strat_signals, strat_confs
            )
            if disagreement:
                self._llm_triggers.add(
                    LLMTrigger.STRATEGY_DISAGREEMENT,
                    symbol=symbol,
                    context=f"{symbol}: {disagreement}",
                )
        except Exception:
            pass

        # Anti-round-trip: same-direction re-entry after a win needs more confidence.
        # Threshold: 75% for unknown setups, 70% for CONFIRMED_EDGE setups
        # (symbol+side with WR >= 55% and n >= 20 in backtest data).
        # Rationale: confirmed edges are the exact patterns we want to repeat;
        # requiring 75% blocks legitimate re-entries on our best setups.
        last_side = self._last_close_side.get(symbol)
        was_win = self._last_close_win.get(symbol, False)
        if was_win and last_side == side:
            # Check for confirmed edge on this symbol+side
            _roundtrip_threshold = 75.0
            try:
                from llm.deep_memory import get_deep_memory
                _dm = get_deep_memory()
                _bt = _dm.strategy_fps.get_all().get("_quant_backtest_2026_03_26", {})
                _setup_key = f"{symbol}_{'BUY' if side == 'LONG' else 'SELL'}"
                _setup = _bt.get(_setup_key, {})
                # WR is stored as 0-100 (percentage), not 0-1 decimal
                _wr = _setup.get("wr", 0)
                _n = _setup.get("total", 0)
                if _wr >= 55 and _n >= 20:
                    _roundtrip_threshold = 70.0
                    logger.info(
                        f"[{trace_id}][{symbol}] Confirmed edge detected "
                        f"({_setup_key}: {_wr:.0f}% WR, n={_n}) — "
                        f"roundtrip threshold lowered to 70%"
                    )
            except Exception:
                pass

            if signal_result.confidence < _roundtrip_threshold:
                log_rejection(symbol, "ANTI_ROUNDTRIP",
                              confidence=signal_result.confidence)
                logger.info(
                    f"[{trace_id}][{symbol}] Anti-round-trip: same-dir re-entry "
                    f"after win needs >={_roundtrip_threshold:.0f}% conf "
                    f"(got {signal_result.confidence:.0f}%)"
                )
                return

        # ── Signal Flagger: cheap heuristic flag evaluation ──
        # Runs on every signal (no LLM call). Flags interesting characteristics
        # (SNIPER, ANOMALY, BREAKOUT, etc.) and fires LLM triggers for high-priority flags.
        _flagged_signal = None
        if self.signal_flagger and self.config.enable_signal_flagger:
            try:
                _sf_regime = signal_result.metadata.get("regime", "")
                _sf_vol_ratio = signal_result.metadata.get("volume_ratio", 1.0)
                _sf_funding = self._last_funding_rates.get(symbol, 0.0)
                _sf_pch_1h = self._price_changes_1h.get(symbol, 0.0)

                # BTC trend context for the flagger
                _sf_btc_1h = self._price_changes_1h.get("BTC", 0.0)
                _sf_btc_trend = "bullish" if _sf_btc_1h > 0.5 else ("bearish" if _sf_btc_1h < -0.5 else "neutral")

                _flagged_signal = self.signal_flagger.evaluate_signal(
                    symbol=symbol,
                    side=signal_result.side,
                    confidence=signal_result.confidence,
                    regime=_sf_regime,
                    num_agree=num_agree,
                    total_strategies=len(self.strategies),
                    volume_ratio=_sf_vol_ratio,
                    funding_rate=_sf_funding,
                    btc_trend=_sf_btc_trend,
                    price_change_1h=_sf_pch_1h,
                )

                # High-priority flags fire an LLM trigger for richer meta-brain context
                if _flagged_signal and _flagged_signal.should_trigger_llm:
                    self._llm_triggers.add(
                        LLMTrigger.HIGH_CONFIDENCE,
                        symbol=symbol,
                        context=f"FLAGGED: {_flagged_signal.flag_summary} "
                                f"conf={signal_result.confidence:.0f}%",
                    )
                    logger.info(
                        f"[{trace_id}][{symbol}] Signal flagged: {_flagged_signal.flag_summary}"
                    )

                # Attach flags to metadata for downstream logging
                if _flagged_signal and _flagged_signal.flags:
                    signal_result.metadata["signal_flags"] = _flagged_signal.flag_summary
                    signal_result.metadata["flag_max_priority"] = _flagged_signal.max_priority
            except Exception as e:
                logger.debug(f"[{trace_id}][{symbol}] Signal flagger error: {e}")

        # Log every signal generated to database (even if not traded)
        _signal_id = log_signal(
            symbol=symbol,
            strategy=signal_result.strategy,
            side=signal_result.side,
            confidence=signal_result.confidence,
            entry=signal_result.entry,
            sl=signal_result.sl,
            tp1=signal_result.tp1,
            tp2=signal_result.tp2,
            atr=signal_result.atr,
            leverage=1.0,
            traded=False,
            metadata=signal_result.metadata
        )

        # Log signal (paper trading compatibility)
        if self.trade_logger:
            regime_score = signal_result.metadata.get("align_long", 0) or signal_result.metadata.get("regime_score", 0)
            num_agree = signal_result.metadata.get("num_agree", 1)
            total_strategies = signal_result.metadata.get("total_strategies", len(self.strategies))
            self.trade_logger.log_signal(
                symbol=symbol,
                signal_obj=signal_result,
                trace_id=trace_id,
                regime_score=regime_score,
                num_agree=num_agree,
                total_strategies=total_strategies,
            )

        # ML confidence adjustment (pass full market context for both models)
        original_conf = signal_result.confidence
        ml_volatility = 0.0  # Used by feedback loop below even if ML is disabled
        if self.ml:
            # Compute market context for ML
            ml_pchange_1h = 0.0
            ml_pchange_24h = 0.0
            ml_vol_ratio = 1.0
            ml_volatility = 0.0
            try:
                df_1h = data.get("1h")
                if df_1h is not None and not df_1h.empty and len(df_1h) > 2:
                    ml_pchange_1h = (current_price - float(df_1h["close"].iloc[-2])) / float(df_1h["close"].iloc[-2]) * 100
                    if len(df_1h) > 24:
                        ml_pchange_24h = (current_price - float(df_1h["close"].iloc[-24])) / float(df_1h["close"].iloc[-24]) * 100
                    avg_vol = float(df_1h["volume"].tail(20).mean())
                    if avg_vol > 0:
                        ml_vol_ratio = float(df_1h["volume"].iloc[-1]) / avg_vol
                    if len(df_1h) > 14:
                        prev_c = df_1h["close"].shift(1)
                        tr = pd.concat([
                            df_1h["high"] - df_1h["low"],
                            (df_1h["high"] - prev_c).abs(),
                            (df_1h["low"] - prev_c).abs(),
                        ], axis=1).max(axis=1)
                        atr14 = float(tr.rolling(14, min_periods=1).mean().iloc[-1])
                        ml_volatility = atr14 / current_price * 100
            except Exception:
                pass

            adjusted_conf = self.ml.adjust_confidence(
                original_conf,
                regime_score=signal_result.metadata.get("align_long", 0) or signal_result.metadata.get("regime_score", 0),
                vwap_aligned=signal_result.metadata.get("vwap_align", False),
                ema_aligned=signal_result.metadata.get("ema_1h_align", False),
                stop_width_ratio=signal_result.stop_width / max(signal_result.atr, 1e-9) if signal_result.atr else 1.5,
                leverage=1.0,
                side=signal_result.side,
                price_change_1h_pct=ml_pchange_1h,
                price_change_24h_pct=ml_pchange_24h,
                volume_ratio=ml_vol_ratio,
                volatility=ml_volatility,
                num_strategies_agree=signal_result.metadata.get("num_agree", 1),
            )
            signal_result.confidence = adjusted_conf

        # ── Feedback loop: adaptive confidence floor + quality scoring ──
        # Replaces static 65% floor with dynamic, learned thresholds
        try:
            _regime = signal_result.metadata.get("regime", "")
            _entry_type_fb = ""
            _vol_ratio_fb = signal_result.metadata.get("volume_ratio", 1.0)

            # quality_pre_applied: ensemble already called quality.adjust_confidence()
            # when quality_scorer is wired in. Avoid double-applying.
            _quality_already_done = "quality_multiplier" in signal_result.metadata
            should_trade, fb_adjusted_conf, fb_floor, fb_reason = self.feedback.evaluate_signal(
                confidence=signal_result.confidence,
                strategy=signal_result.strategy,
                symbol=symbol,
                regime=_regime,
                side=signal_result.side,
                entry_type=_entry_type_fb,
                num_agree=signal_result.metadata.get("num_agree", 1),
                total_strategies=signal_result.metadata.get("total_strategies", len(self.strategies)),
                volume_ratio=_vol_ratio_fb,
                volatility=ml_volatility if self.ml else 0.0,
                rr1=signal_result.risk_reward_tp1,
                trend_alignment=signal_result.metadata.get("trend_adjustment", 0.0),
                quality_pre_applied=_quality_already_done,
            )

            # Apply quality-adjusted confidence
            signal_result.confidence = fb_adjusted_conf

            # Record signal for backtest tracking
            self.feedback.record_signal(
                symbol=symbol,
                side=signal_result.side,
                confidence=signal_result.confidence,
                strategy=signal_result.strategy,
                entry=signal_result.entry,
                sl=signal_result.sl,
                tp1=signal_result.tp1,
                regime=_regime,
                num_agree=signal_result.metadata.get("num_agree", 1),
            )

            if not should_trade:
                # ── Signal Override: can powerful signal bypass feedback floor? ──
                _override_ok = False
                if (self.signal_override and self.config.enable_signal_override
                        and _SIGNAL_OVERRIDE_AVAILABLE):
                    try:
                        _ov_result = should_override_blocker(
                            confidence=signal_result.confidence,
                            num_agree=signal_result.metadata.get("num_agree", 1),
                            total_strategies=len(self.strategies),
                            blocker=BlockerType.CONFIDENCE_FLOOR,
                            blocker_detail=fb_reason,
                            volume_confirms=signal_result.metadata.get("volume_ratio", 1.0) >= 1.5,
                            regime_aligned=_regime in ("trend", "trending"),
                            trend_aligned=signal_result.metadata.get("trend_adjustment", 0) > 0,
                            current_equity=self.risk_mgr.equity,
                            daily_pnl=self.risk_mgr.circuit_breaker.daily_pnl,
                        )
                        if _ov_result.should_override:
                            _override_ok = True
                            logger.info(
                                f"[{trace_id}][{symbol}] OVERRIDE: feedback floor bypassed "
                                f"(power={_ov_result.power_score:.0f}, {_ov_result.reason})"
                            )
                    except Exception as e:
                        logger.debug(f"Signal override feedback error: {e}")

                if not _override_ok:
                    logger.info(f"[{trace_id}][{symbol}] Feedback floor BLOCKED: {fb_reason}")
                    # Data collection phase complete (100+ trades). Enforce the floor.
                    return
        except Exception as e:
            logger.warning(f"[{trace_id}][{symbol}] Feedback loop error (proceeding): {e}")

        # ── Circuit breaker check (with high-confidence override) ──
        if not self.risk_mgr.can_open_position(
            self.pos_mgr.get_open_count(),
            confidence=signal_result.confidence,
            cb_conf_override_pct=self.config.cb_conf_override_pct,
        ):
            # ── Signal Override: can powerful signal bypass circuit breaker? ──
            _cb_override_ok = False
            if (self.signal_override and self.config.enable_signal_override
                    and _SIGNAL_OVERRIDE_AVAILABLE):
                try:
                    _cb_blocker = BlockerType.CIRCUIT_BREAKER
                    if self.risk_mgr.circuit_breaker.consecutive_losses >= self.config.max_consecutive_losses:
                        _cb_blocker = BlockerType.CONSECUTIVE_LOSSES

                    _cb_ov_result = should_override_blocker(
                        confidence=signal_result.confidence,
                        num_agree=signal_result.metadata.get("num_agree", 1),
                        total_strategies=len(self.strategies),
                        blocker=_cb_blocker,
                        volume_confirms=signal_result.metadata.get("volume_ratio", 1.0) >= 1.5,
                        regime_aligned=signal_result.metadata.get("regime", "") in ("trend", "trending"),
                        trend_aligned=signal_result.metadata.get("trend_adjustment", 0) > 0,
                        current_equity=self.risk_mgr.equity,
                        daily_pnl=self.risk_mgr.circuit_breaker.daily_pnl,
                    )
                    if _cb_ov_result.should_override:
                        _cb_override_ok = True
                        logger.warning(
                            f"[{trace_id}][{symbol}] OVERRIDE: circuit breaker bypassed "
                            f"(power={_cb_ov_result.power_score:.0f}, {_cb_ov_result.reason})"
                        )
                except Exception as e:
                    logger.debug(f"Signal override CB error: {e}")

            if not _cb_override_ok:
                return

        # Portfolio leverage guard: block new entries when total leverage too high
        _portfolio_lev = self._compute_portfolio_leverage()
        _max_portfolio_lev = max(self.config.max_portfolio_leverage * 2.5, 8.0)  # Notional-based cap is primary
        if _portfolio_lev >= _max_portfolio_lev:
            log_rejection(symbol, "PORTFOLIO_LEVERAGE",
                          confidence=signal_result.confidence,
                          portfolio_leverage=_portfolio_lev)
            logger.info(
                f"[{trace_id}][{symbol}] Portfolio leverage guard: "
                f"{_portfolio_lev:.1f}x >= {_max_portfolio_lev:.1f}x max"
            )
            return

        # Get CB override constraints (graduated risk during CB override)
        cb_constraints = self.risk_mgr.circuit_breaker.get_override_constraints(
            confidence=signal_result.confidence
        )

        # ── Risk filters (all rejections logged to data/logs/risk_rejections.csv) ──
        rr1 = signal_result.risk_reward_tp1
        sl_distance = signal_result.stop_width

        # R:R sanity filter
        if rr1 < 0.5:
            log_rejection(symbol, "rr1_too_low", rr1=rr1, sl_distance=sl_distance,
                          confidence=signal_result.confidence)
            logger.info(f"[{trace_id}][{symbol}] Rejected: R:R1={rr1:.2f} < 0.5")
            return

        # SL too tight relative to ATR (noise will stop us out)
        if signal_result.atr > 0 and sl_distance < signal_result.atr * 0.5:
            log_rejection(symbol, "sl_too_tight", rr1=rr1, sl_distance=sl_distance,
                          confidence=signal_result.confidence)
            logger.info(f"[{trace_id}][{symbol}] Rejected: SL distance {sl_distance:.4f} < 0.5*ATR")
            return

        # Higher-timeframe trend gate (alpha research 2026-03-24):
        # LONGs against 6h bearish trend have 34% WR. SHORTs against 6h bullish = same.
        # Penalize counter-HTF signals with reduced position size.
        try:
            _6h_df = data.get("6h")
            if _6h_df is not None and len(_6h_df) >= 20:
                _6h_c = float(_6h_df["close"].iloc[-1])
                _6h_ema = float(_6h_df["close"].ewm(span=20).mean().iloc[-1])
                _htf_bear = _6h_c < _6h_ema
                _htf_bull = _6h_c > _6h_ema
                signal_result.metadata["htf_6h_trend"] = "bear" if _htf_bear else "bull"
                if signal_result.side == "BUY" and _htf_bear:
                    _rm = signal_result.metadata.get("risk_mult_override", 1.0)
                    signal_result.metadata["risk_mult_override"] = min(_rm, 0.4)
                    signal_result.metadata["htf_penalty"] = "long_vs_6h_bear"
                    logger.info(f"[{trace_id}][{symbol}] HTF gate: LONG against 6h bear → 0.4x size")
                elif signal_result.side == "SELL" and _htf_bull:
                    _rm = signal_result.metadata.get("risk_mult_override", 1.0)
                    signal_result.metadata["risk_mult_override"] = min(_rm, 0.4)
                    signal_result.metadata["htf_penalty"] = "short_vs_6h_bull"
                    logger.info(f"[{trace_id}][{symbol}] HTF gate: SHORT against 6h bull → 0.4x size")
        except Exception:
            pass  # HTF gate is best-effort, don't break signal processing

        # Run signal through RiskFilterChain for EV filter, liquidation safety,
        # and correlation guard. These gates don't exist in the inline checks above.
        num_agree = signal_result.metadata.get("num_agree", 1)
        total = signal_result.metadata.get("total_strategies", len(self.strategies))
        extreme_count = sum(1 for p in open_pos.values() if p.leverage > 5.0)

        try:
            from core.signal_pipeline import RiskFilterChain
            _chain = RiskFilterChain(self.risk_mgr, self.leverage_mgr, self.config)
            _chain_result = _chain.evaluate(
                signal=signal_result,
                equity=self.risk_mgr.equity,
                num_strategies_agree=num_agree,
                total_strategies=total,
                current_open_count=len(open_pos),
                current_extreme_count=extreme_count,
                risk_tier=sym_cfg.risk_tier,
                open_positions=open_pos,
                portfolio_risk_engine=self.portfolio_risk if hasattr(self, 'portfolio_risk') else None,
            )
            if not _chain_result.approved:
                log_rejection(symbol, f"risk_filter_chain: {_chain_result.rejection_reason}",
                              confidence=signal_result.confidence)
                logger.info(
                    f"[{trace_id}][{symbol}] RiskFilterChain rejected: "
                    f"{_chain_result.rejection_reason}"
                )
                # Track pipeline rejection in missed trade tracker
                if self._missed_trade_tracker is not None:
                    try:
                        self._missed_trade_tracker.record_rejection(
                            signal=signal_result,
                            reason=_chain_result.rejection_reason,
                            gate="pipeline",
                        )
                    except Exception:
                        pass

                # ── Annotated chain: record what filters measured even on rejection ──
                if getattr(self.config, 'enable_soft_filters', False) or getattr(self.config, 'soft_filter_log_only', False):
                    try:
                        _annotated = _chain.evaluate_annotated(
                            signal=signal_result,
                            equity=self.risk_mgr.equity,
                            num_strategies_agree=num_agree,
                            total_strategies=total,
                            current_open_count=len(open_pos),
                            current_extreme_count=extreme_count,
                            risk_tier=sym_cfg.risk_tier,
                            open_positions=open_pos,
                            portfolio_risk_engine=self.portfolio_risk if hasattr(self, 'portfolio_risk') else None,
                        )
                        # Merge pipeline annotations with ensemble annotations
                        if hasattr(self, '_pending_annotations') and symbol in self._pending_annotations:
                            existing = self._pending_annotations[symbol]
                            existing.annotations.extend(_annotated.annotations)
                            existing.filter_metadata.update(_annotated.filter_metadata)
                            if _annotated.hard_rejected:
                                existing.hard_rejected = True
                                existing.hard_rejection_reason = _annotated.hard_rejection_reason

                            # Track the chain-rejected signal
                            from core.signal_tracker import get_signal_tracker
                            tracker = get_signal_tracker()
                            tracker.record_signal(
                                symbol=symbol,
                                side=signal_result.side,
                                confidence=signal_result.confidence,
                                strategy=signal_result.strategy or "ensemble",
                                passed=False,
                                hard_rejected=_annotated.hard_rejected,
                                hard_rejection_reason=_annotated.hard_rejection_reason,
                                annotations=[
                                    {"gate": a.gate, "severity": a.severity, "value": a.value, "threshold": a.threshold}
                                    for a in _annotated.annotations
                                ],
                                filter_metadata=_annotated.filter_metadata,
                                num_strategies_agree=num_agree,
                            )
                    except Exception as ann_e:
                        logger.debug(f"[{symbol}] Annotated chain error: {ann_e}")

                try: _get_pt().finish_journey(symbol, traded=False)
                except Exception: pass
                return
        except Exception as e:
            logger.warning(f"[{trace_id}][{symbol}] RiskFilterChain error: {e} — rejecting signal for safety")
            return  # Do NOT fall through to weaker inline checks

        # Use leverage from RiskFilterChain (already computed by leverage_mgr.decide()
        # inside the chain). Avoids double-computation which could diverge if state
        # changes between calls. Live-specific caps are applied below.
        if '_chain_result' in dir() and _chain_result.approved:
            lev_decision = self.leverage_mgr.decide(
                signal_result.confidence, num_agree, total,
                sym_cfg.risk_tier, extreme_count,
            )
            # Override with chain result's leverage to stay consistent
            lev_decision.leverage = _chain_result.leverage
            lev_decision.risk_multiplier = _chain_result.risk_multiplier
        else:
            lev_decision = self.leverage_mgr.decide(
                signal_result.confidence, num_agree, total,
                sym_cfg.risk_tier, extreme_count,
            )

        if lev_decision.leverage <= 0:
            return  # Confidence too low

        # Feedback-driven leverage cap (learned from backtest performance)
        try:
            _fb_regime = signal_result.metadata.get("regime", "")
            fb_lev_cap = self.feedback.get_leverage_cap(symbol, _fb_regime)
            if lev_decision.leverage > fb_lev_cap:
                lev_decision.leverage = fb_lev_cap
                lev_decision.reason = f"feedback-capped to {fb_lev_cap:.0f}x"
        except Exception:
            pass  # Feedback failure shouldn't block trading

        # Circuit breaker override constraints: cap leverage during CB
        if cb_constraints.get("constrained"):
            cb_max_lev = cb_constraints["max_leverage"]
            if lev_decision.leverage > cb_max_lev:
                logger.info(
                    f"[{trace_id}][{symbol}] CB override: leverage {lev_decision.leverage:.1f}x → {cb_max_lev:.1f}x"
                )
                lev_decision.leverage = cb_max_lev
                lev_decision.reason = f"CB override capped to {cb_max_lev:.0f}x"

        # Self-Tuning Risk: dynamic leverage cap based on drawdown
        try:
            dyn_lev_cap = get_dynamic_leverage_cap(self.config.max_leverage)
            if lev_decision.leverage > dyn_lev_cap:
                logger.info(
                    f"[{trace_id}][{symbol}] Risk-tune: leverage "
                    f"{lev_decision.leverage:.1f}x → {dyn_lev_cap:.1f}x (drawdown cap)"
                )
                lev_decision.leverage = dyn_lev_cap
        except Exception:
            pass

        # Per-symbol leverage cap from precision config
        sym_max_lev = get_max_leverage(symbol)
        if lev_decision.leverage > sym_max_lev:
            lev_decision.leverage = sym_max_lev
            lev_decision.reason = f"capped to {sym_max_lev}x ({symbol} limit)"

        # Extra R:R gate for high leverage: need R:R1 >= 1.0 above 8x
        if lev_decision.leverage > 8.0 and rr1 < 1.0:
            log_rejection(symbol, "rr1_too_low_high_lev", rr1=rr1,
                          leverage=lev_decision.leverage,
                          confidence=signal_result.confidence)
            logger.info(
                f"[{trace_id}][{symbol}] Rejected: R:R1={rr1:.2f} < 1.0 at {lev_decision.leverage:.1f}x"
            )
            return

        # ── Rotation candidate collection ──
        # If we already have a position in this symbol or are at max positions,
        # don't open a new position — but save the signal as a potential rotation
        # target for other open positions that may want to rotate into this one.
        if has_position or at_max_positions:
            if self.rotation_mgr and not has_position:
                with self._tick_candidates_lock:
                    self._tick_candidates.append({
                        "symbol": symbol,
                        "side": signal_result.side,
                        "entry": signal_result.entry,
                        "sl": signal_result.sl,
                        "tp1": signal_result.tp1,
                        "tp2": signal_result.tp2,
                        "atr": signal_result.atr,
                        "confidence": signal_result.confidence,
                        "align_score": signal_result.metadata.get("num_agree", 1),
                        "strategy": signal_result.strategy,
                        "rr1": rr1,
                    })
            return

        # ── Wave 2: Signal Decay — reduce confidence for stale signals ──
        _signal_gen_time = signal_result.metadata.get("generated_at", 0)
        if _signal_gen_time and self.config.signal_decay_seconds > 0:
            _signal_age = time.time() - _signal_gen_time
            if _signal_age > self.config.signal_decay_seconds:
                _decay = max(0.8, 1.0 - (_signal_age - 60) / 600)
                signal_result.confidence *= _decay
                logger.info(
                    f"[{trace_id}][{symbol}] Signal decay: {_signal_age:.0f}s old, "
                    f"conf * {_decay:.2f} = {signal_result.confidence:.0f}%"
                )

        # ── Wave 2: Liquidity Guard — reject dead markets, reduce size in thin liquidity ──
        _liq_size_mult = 1.0
        if self.config.enable_liquidity_guard and _LIQUIDITY_GUARD_AVAILABLE:
            try:
                _liq_result = validate_liquidity(
                    symbol=symbol,
                    volume_ratio=signal_result.metadata.get("volume_ratio", 1.0),
                    funding_rate=self._last_funding_rates.get(symbol, 0.0),
                )
                if not _liq_result.can_trade:
                    log_rejection(symbol, "LIQUIDITY_GUARD",
                                  confidence=signal_result.confidence)
                    logger.info(
                        f"[{trace_id}][{symbol}] Liquidity guard: {_liq_result.reason}"
                    )
                    return
                _liq_size_mult = _liq_result.size_multiplier
                if _liq_size_mult < 1.0:
                    logger.info(
                        f"[{trace_id}][{symbol}] Liquidity guard: "
                        f"size * {_liq_size_mult:.2f} ({_liq_result.reason})"
                    )
            except Exception as e:
                logger.debug(f"Liquidity guard error: {e}")

        # ── Push 1: Funding rate entry filter ──
        # Reject entries near negative funding (avoid paying funding right after entry)
        if self.config.enable_funding_check:
            _fr_entry = self._last_funding_rates.get(symbol, 0.0)
            if _fr_entry != 0 and lev_decision.leverage > 2.0:
                _side_lower = side.lower()
                _is_paying = (
                    (_side_lower == "long" and _fr_entry > 0) or
                    (_side_lower == "short" and _fr_entry < 0)
                )
                if _is_paying and abs(_fr_entry) > 0.0005:
                    from execution.funding_timer import minutes_until_next_funding
                    _mins = minutes_until_next_funding()
                    if _mins < 60:
                        log_rejection(symbol, "FUNDING_ENTRY_FILTER",
                                      confidence=signal_result.confidence,
                                      funding_rate=_fr_entry)
                        logger.info(
                            f"[{trace_id}][{symbol}] Funding entry filter: "
                            f"{side} at {lev_decision.leverage:.0f}x, rate={_fr_entry:.5f}, "
                            f"{_mins}min to payment — skipping"
                        )
                        return

        # ── Push 1: Min profit threshold gate ──
        # Reject trades where TP1 profit < min_mult * total expected costs
        _tp1_distance_pct = abs(signal_result.tp1 - signal_result.entry) / signal_result.entry if signal_result.entry > 0 else 0
        _total_cost_pct = (self.config.taker_fee_bps + self.config.slippage_bps) * 2 / 10000.0
        if _tp1_distance_pct < self.config.min_profit_threshold_mult * _total_cost_pct:
            log_rejection(symbol, "MIN_PROFIT_THRESHOLD",
                          confidence=signal_result.confidence,
                          tp1_pct=_tp1_distance_pct * 100,
                          cost_pct=_total_cost_pct * 100)
            logger.info(
                f"[{trace_id}][{symbol}] Min profit gate: "
                f"TP1 distance={_tp1_distance_pct*100:.3f}% < "
                f"{self.config.min_profit_threshold_mult}x costs={_total_cost_pct*100:.3f}%"
            )
            return

        # ── Push 1: Per-symbol parameter overrides ──
        _sym_max_lev = get_symbol_param(symbol, "max_leverage", self.config)
        if lev_decision.leverage > _sym_max_lev:
            logger.info(
                f"[{trace_id}][{symbol}] Per-symbol override: "
                f"leverage {lev_decision.leverage:.1f}x → {_sym_max_lev:.1f}x"
            )
            lev_decision.leverage = _sym_max_lev
        _sym_risk = get_symbol_param(symbol, "risk_per_trade", self.config)

        # Vol-targeting: single-parameter sizing (replaces 11-multiplier compound system).
        # Scales risk inversely with current ATR vs 1.5% baseline ATR (crypto long-run avg).
        # High vol (3% ATR) → 0.5× risk. Low vol (0.75% ATR) → 2× risk, capped at 2× base.
        # Circuit breaker still halts trading on drawdowns — safety preserved.
        # Kelly/IC weights can be re-enabled after 200+ trades of live calibration data.
        _compound_mult = 1.0
        _atr_pct = 0.0
        try:
            if hasattr(signal_result, 'atr') and signal_result.atr > 0 and signal_result.entry > 0:
                _atr_pct = signal_result.atr / signal_result.entry
                _BASELINE_ATR = 0.015  # 1.5% daily ATR = neutral
                _compound_mult = _BASELINE_ATR / max(_atr_pct, 0.001)
                _compound_mult = max(0.3, min(2.0, _compound_mult))  # bound 0.3×–2.0×
            # Vol-targeting: scale risk inversely with volatility.
            # Base is risk_per_trade (user-set), NOT vol_target_pct (which was
            # 0.5% and neutered the 20% risk_per_trade to 0.15-1.0%).
            # The compound_mult adjusts risk: high vol → smaller, low vol → larger.
            _sym_risk = self.config.risk_per_trade * _compound_mult
            # Safety cap: never exceed 2× base risk_per_trade
            _sym_risk = min(_sym_risk, self.config.risk_per_trade * 2)
            # Cache for trade ledger attribution at close
            self._compound_mult_cache[symbol] = round(_compound_mult, 4)
            logger.debug(
                f"[{symbol}] Vol-target sizing: atr_pct={_atr_pct:.3%}, "
                f"mult={_compound_mult:.2f}, risk={_sym_risk:.4f}"
            )
        except Exception as e:
            logger.debug(f"Vol-target sizing error (using base): {e}")
            _sym_risk = _sym_risk or self.config.risk_per_trade

        # Calculate position size (risk-based: qty = risk$ / (stop_dist * leverage))
        qty = self.risk_mgr.calculate_qty(
            signal_result.entry, signal_result.sl,
            leverage=lev_decision.leverage,
            risk_multiplier=lev_decision.risk_multiplier,
            symbol=symbol,
            slippage_bps=self.config.slippage_bps,
            risk_per_trade_override=_sym_risk,
        )
        if qty <= 0:
            return

        # ── LLM AUTHORITATIVE SIZING: skip mechanical multiplier chain ──
        # When LLM is in SIZING+ mode, the Risk Agent's sz output (0.3-2.0)
        # replaces the 19 mechanical multipliers. Only safety caps remain.
        _llm_authority_active = False
        if self.llm_mode >= LLMMode.SIZING:
            _candidate = self._active_candidates.get(symbol)
            _llm_sz_early = getattr(_candidate, 'llm_size_mult', None) if _candidate else None
            if _llm_sz_early is not None and _llm_sz_early > 0:
                _llm_sz_early = max(0.3, min(2.0, _llm_sz_early))
                _original_qty = qty  # Save for MIN_NOTIONAL floor check
                qty = qty * _llm_sz_early
                # Apply ONLY circuit breaker cap (hard safety)
                if cb_constraints.get("constrained"):
                    qty = qty * cb_constraints["size_multiplier"]
                logger.info(
                    f"[{trace_id}][{symbol}] LLM AUTHORITATIVE SIZING: "
                    f"base_qty * {_llm_sz_early:.2f} = {qty:.6f} "
                    f"(skipping {19} mechanical multipliers)"
                )
                _llm_authority_active = True

        # Circuit breaker override: reduce size during CB
        if cb_constraints.get("constrained"):
            cb_size_mult = cb_constraints["size_multiplier"]
            qty = qty * cb_size_mult
            logger.info(
                f"[{trace_id}][{symbol}] CB override: qty * {cb_size_mult:.2f} = {qty:.6f}"
            )

        # Portfolio correlation guard: reduce size for correlated same-direction trades
        try:
            corr_info = self._compute_portfolio_correlation()
            if corr_info["risk_level"] == "high":
                qty = qty * 0.70  # 30% reduction
                logger.info(f"[{trace_id}][{symbol}] Correlation guard (high): qty * 0.70")
            elif corr_info["risk_level"] == "medium":
                qty = qty * 0.85  # 15% reduction
                logger.info(f"[{trace_id}][{symbol}] Correlation guard (medium): qty * 0.85")
        except Exception:
            pass

        # Dedicated CorrelationGate: per-symbol cluster logic
        if self.correlation_gate:
            try:
                _open_for_corr = [
                    {"symbol": s, "side": p.side}
                    for s, p in open_pos.items()
                ]
                _corr_mult = self.correlation_gate.check_correlation_budget(
                    new_symbol=symbol,
                    new_side=side,
                    open_positions=_open_for_corr,
                )
                if _corr_mult == 0.0:
                    logger.info(f"[{trace_id}][{symbol}] CorrelationGate: cluster at capacity — SKIP")
                    return
                elif _corr_mult < 1.0:
                    qty = qty * _corr_mult
                    logger.info(f"[{trace_id}][{symbol}] CorrelationGate: size reduced to {_corr_mult:.0%}")
            except Exception as e:
                logger.debug(f"CorrelationGate check error: {e}")

        # Sector exposure gate: prevent thematic concentration
        if hasattr(self, '_sector_exposure_cls') and self._sector_exposure_cls:
            try:
                _se = self._sector_exposure_cls(total_equity=self.risk_mgr.equity)
                _open_notionals = [
                    (s, p.qty * p.entry) for s, p in open_pos.items()
                ]
                _se_result = _se.check_new_position(
                    symbol=symbol,
                    new_notional=qty * actual_entry if 'actual_entry' in dir() else qty * signal_result.entry,
                    open_positions=_open_notionals,
                )
                if not _se_result.allowed:
                    logger.info(
                        f"[{trace_id}][{symbol}] SectorExposure: blocked by "
                        f"{_se_result.limiting_sector} cap — SKIP"
                    )
                    return
                elif _se_result.size_multiplier < 1.0:
                    qty = qty * _se_result.size_multiplier
                    logger.info(
                        f"[{trace_id}][{symbol}] SectorExposure: size reduced to "
                        f"{_se_result.size_multiplier:.0%} ({_se_result.limiting_sector})"
                    )
            except Exception as e:
                logger.debug(f"SectorExposure check error: {e}")

        # Global Brain bias: adjust sizing based on macro regime
        try:
            _gb_size_mult = self._global_bias_adjustment.get("size_multiplier", 1.0)
            if _gb_size_mult != 1.0:
                qty = qty * _gb_size_mult
                logger.info(
                    f"[{trace_id}][{symbol}] Global bias ({self._global_bias}): "
                    f"qty * {_gb_size_mult:.2f} = {qty:.6f}"
                )
        except Exception:
            pass

        # Wave 3: Portfolio Risk Engine — dynamic position limits based on correlation/vol
        if self.portfolio_risk:
            try:
                _pr_limit = self.portfolio_risk.get_position_limit(
                    symbol=symbol,
                    side=side,
                    open_positions={s: {"side": p.side, "entry": p.entry, "qty": p.qty, "leverage": p.leverage}
                                   for s, p in open_pos.items()},
                    equity=self.risk_mgr.equity,
                )
                _pr_max_pct = _pr_limit.get("max_qty_pct", 1.0)
                if _pr_max_pct < 1.0:
                    qty = qty * _pr_max_pct
                    logger.info(
                        f"[{trace_id}][{symbol}] Portfolio risk limit: "
                        f"qty * {_pr_max_pct:.2f} ({_pr_limit.get('reason', '')})"
                    )
            except Exception as e:
                logger.debug(f"Portfolio risk limit error: {e}")

        # Time-aware sizing: adjust based on hour-of-day + day-of-week + directional bias
        if getattr(self.config, "enable_time_sizing", True):
            _time_info = get_full_time_multiplier(
                side=side,
                allow_boost=getattr(self.config, "time_sizing_allow_boost", True),
                max_boost=getattr(self.config, "time_sizing_max_boost", 1.4),
                directional_boost=getattr(self.config, "time_sizing_directional_boost", 1.15),
                directional_penalty=getattr(self.config, "time_sizing_directional_penalty", 0.85),
            )
            time_mult = _time_info["multiplier"]
            if time_mult != 1.0:
                qty = qty * time_mult
                _reasons = ", ".join(_time_info["reasons"]) if _time_info["reasons"] else ""
                logger.info(
                    f"[{trace_id}][{symbol}] Time sizing: qty * {time_mult:.3f} "
                    f"(session={_time_info['session']}, bias={_time_info['bias']}, "
                    f"{_reasons}) = {qty:.6f}"
                )

        # Liquidity guard sizing (applied after all other multipliers)
        if _liq_size_mult < 1.0:
            qty = qty * _liq_size_mult
            logger.info(
                f"[{trace_id}][{symbol}] Liquidity guard sizing: "
                f"qty * {_liq_size_mult:.2f} = {qty:.6f}"
            )

        # ── Reflection Engine: entry quality sizing adjustment ──
        # WEAK entries (re-entry chasing, exhausted moves) get reduced size.
        # Doesn't block — just sizes down proportionally to quality.
        if self._reflection_engine is not None:
            try:
                _refl_score = self._reflection_engine.get_entry_quality_score(
                    symbol=symbol, side=side,
                    entry_price=signal_result.entry,
                    confidence=signal_result.confidence,
                    regime=self._tick_regime_cache.get(symbol, "unknown"),
                    atr=signal_result.atr,
                    win_prob=entry_reasons.get("win_prob", 0),
                )
                _quality = _refl_score["quality_score"]
                _advisory = _refl_score["advisory"]
                if _quality < 50:
                    logger.info(
                        f"[{trace_id}][{symbol}] REFLECT: WEAK entry SKIPPED (score={_quality}) "
                        f"codes=[{','.join(_refl_score['codes'])}]"
                    )
                    return
                elif _quality < 80:
                    qty = qty * 0.75
                    logger.info(
                        f"[{trace_id}][{symbol}] REFLECT: CAUTION entry (score={_quality}) "
                        f"codes=[{','.join(_refl_score['codes'])}] — qty * 0.75"
                    )
            except Exception as e:
                logger.debug(f"Reflection sizing error: {e}")

        # ── QTY FLOOR: prevent multiplicative chain from crushing to dust ──
        # The chain above (correlation, sector, portfolio, time, liquidity) can
        # multiply qty down to near-zero. Floor at 50% of original calculated qty
        # so we always take a meaningful position. The risk gates already approved
        # this trade — the multipliers are just sizing adjustments, not vetoes.
        _original_qty = self.risk_mgr.calculate_qty(
            signal_result.entry, signal_result.sl,
            leverage=lev_decision.leverage,
            risk_multiplier=lev_decision.risk_multiplier,
            symbol=symbol,
            slippage_bps=self.config.slippage_bps,
            risk_per_trade_override=_sym_risk,
        )
        if _original_qty > 0 and qty < _original_qty * 0.50:
            qty = _original_qty * 0.50
            logger.info(
                f"[{trace_id}][{symbol}] QTY FLOOR: multiplier chain crushed qty. "
                f"Restored to 50% of base ({qty:.6f})"
            )

        # Enforce minimum order size
        min_q = get_min_qty(symbol)
        if qty < min_q:
            logger.info(f"[{trace_id}][{symbol}] Qty {qty:.6f} < min {min_q} — bumping to min")
            qty = min_q

        # ── Trade Classification Layer ──
        # Classify trade -> TradeProfile (drives exits, TP1%, trailing)
        # Add volume_ratio to metadata for regime detection
        try:
            df_1h_vol = data.get("1h")
            if df_1h_vol is not None and not df_1h_vol.empty and len(df_1h_vol) >= 20:
                avg_v = float(df_1h_vol["volume"].tail(20).mean())
                cur_v = float(df_1h_vol["volume"].iloc[-1])
                if avg_v > 0:
                    signal_result.metadata["volume_ratio"] = cur_v / avg_v
        except Exception:
            pass

        trade_prof = classify_trade(
            signal_metadata=signal_result.metadata,
            confidence=signal_result.confidence,
            atr=signal_result.atr,
            entry=signal_result.entry,
            side=signal_result.side,
        )

        # ── CB entry_type filter: when CB is active, only allow TREND/REGIME ──
        if self.risk_mgr.circuit_breaker.tripped:
            allowed_types = ("TREND", "REGIME")
            if trade_prof.entry_type not in allowed_types:
                log_rejection(
                    symbol, "CB_HIGH_CONF_ONLY",
                    confidence=signal_result.confidence,
                    rr1=signal_result.risk_reward_tp1,
                )
                logger.info(
                    f"[SAFETY] CB active: rejecting {trade_prof.entry_type} trade "
                    f"(only {allowed_types} allowed during CB)"
                )
                return

        # Apply profile-recommended TP1/SL/TP2 (overrides strategy levels)
        adjusted = apply_profile_to_signal(
            trade_prof,
            entry=signal_result.entry,
            sl=signal_result.sl,
            tp1=signal_result.tp1,
            tp2=signal_result.tp2,
            atr=signal_result.atr,
            side=signal_result.side,
        )
        adj_sl = adjusted["sl"]
        adj_tp1 = adjusted["tp1"]
        adj_tp2 = adjusted["tp2"]
        tp1_pct = adjusted["tp1_close_pct"]

        # ── Dynamic TP/SL optimization (MFE-based) ──
        # Adjusts TP1/SL using per-symbol MFE data + regime/volume/time/ATR
        if getattr(self.config, 'dynamic_tp_enabled', True):
            try:
                _vol_ratio = signal_result.metadata.get("volume_ratio", 1.0)
                _regime = trade_prof.regime if trade_prof else "unknown"
                # Compute ATR 75th percentile from 1h data if available
                _atr_p75 = 0.0
                _df_atr = data.get("1h") if data else None
                if _df_atr is not None and hasattr(_df_atr, 'empty') and not _df_atr.empty and len(_df_atr) >= 20:
                    try:
                        import numpy as np
                        _atr_col = _df_atr.get("atr")
                        if _atr_col is not None:
                            _atr_p75 = float(np.percentile(_atr_col.dropna().tail(100), 75))
                    except Exception:
                        pass

                dtp_result = dynamic_tp_optimize(
                    symbol=symbol,
                    side=signal_result.side,
                    entry=signal_result.entry,
                    current_tp1=adj_tp1,
                    current_sl=adj_sl,
                    regime=_regime,
                    volume_ratio=_vol_ratio,
                    atr=signal_result.atr,
                    atr_p75=_atr_p75,
                )
                if dtp_result.enabled:
                    adj_tp1 = dtp_result.tp1
                    adj_sl = dtp_result.sl
                    logger.info(
                        f"[{trace_id}][{symbol}] DynamicTP applied: "
                        f"TP1={_fmt_price(adj_tp1)} SL={_fmt_price(adj_sl)} "
                        f"({', '.join(dtp_result.adjustments[-1:])})"
                    )
            except Exception as e:
                logger.warning(f"[{trace_id}][{symbol}] DynamicTP error (using profile levels): {e}")

        # Build entry reasons: WHY this trade was entered (for EV analysis)
        # LLM decision info will be populated later when candidate is built
        _cand_llm_action = ""
        _cand_llm_conf = 0.0

        entry_reasons = {
            "strategies_agree": signal_result.metadata.get("strategies_agree", []),
            "num_agree": num_agree,
            "trend_adjustment": signal_result.metadata.get("trend_adjustment", 0),
            "regime_score": signal_result.metadata.get("align_long", 0) or signal_result.metadata.get("regime_score", 0),
            "individual_confidences": signal_result.metadata.get("individual_confidences", {}),
            "mode": signal_result.metadata.get("mode", ""),
            "rr1": round(rr1, 2),
            "ml_adjusted": original_conf != signal_result.confidence,
            "entry_type": trade_prof.entry_type,
            "primary_driver": trade_prof.primary_driver,
            "regime": trade_prof.regime,
            "volatility_band": trade_prof.volatility_band,
            # LLM decision data for feedback loop
            "llm_action": _cand_llm_action,
            "llm_confidence": _cand_llm_conf,
            "llm_agreed": _cand_llm_action in ("proceed", "go", "", None),
            # Signal flagger data for post-trade analysis
            "signal_flags": signal_result.metadata.get("signal_flags", ""),
            "flag_max_priority": signal_result.metadata.get("flag_max_priority", 0),
            # EV tracking for calibration
            "ev_per_dollar": signal_result.metadata.get("ev_per_dollar", ""),
            "win_prob_deflated": signal_result.metadata.get("win_prob", ""),
            "fee_drag_pct": signal_result.metadata.get("fee_drag_pct", ""),
            # Setup key for neuroplasticity + setup-specific exits
            "setup_key": self._compute_setup_key(signal_result, trade_prof),
        }

        # Track portfolio correlation risk for LLM learning feedback
        if self.portfolio_risk and len(open_pos) >= 2:
            try:
                _pos_map = {s: p.side.lower() for s, p in open_pos.items()}
                _pos_map[symbol] = side.lower()
                _corr_m = self.portfolio_risk.compute_correlation_matrix()
                if _corr_m:
                    _cluster = _corr_m.get_cluster_risk(_pos_map)
                    entry_reasons["cluster_risk"] = round(_cluster, 3)
            except Exception:
                pass

        # Correlation guard: max same-direction positions
        same_dir_count = sum(1 for p in open_pos.values() if p.side == side)
        if same_dir_count >= self._max_same_direction:
            log_rejection(symbol, "CORRELATION_GUARD",
                          confidence=signal_result.confidence)
            logger.info(
                f"[{trace_id}][{symbol}] Correlation guard: "
                f"{same_dir_count} {side} positions open (max {self._max_same_direction})"
            )
            return

        # Skip if this symbol was recently slippage-rejected (5-min cooldown)
        _slip_cooldown = self._slippage_reject_cooldown.get(symbol, 0)
        if time.time() - _slip_cooldown < 300:
            logger.info(
                f"[{trace_id}][{symbol}] Skipping: slippage-rejected "
                f"{int(time.time() - _slip_cooldown)}s ago (cooldown 300s)"
            )
            return

        # LLM trigger: about to open a position (highest priority)
        self._llm_triggers.add(
            LLMTrigger.PRE_TRADE,
            symbol=symbol,
            context=f"Opening {side} {symbol} @ {_fmt_price(signal_result.entry)} "
                    f"lev={lev_decision.leverage:.1f}x conf={signal_result.confidence:.0f}% "
                    f"type={trade_prof.entry_type}",
        )

        # ── PRE-LLM PRICE REFRESH: use live price so everything is consistent ──
        # Strategies set entry from candle closes, which can be minutes old.
        # Refresh BEFORE the LLM pipeline so (a) we don't waste LLM calls on
        # stale signals, and (b) the LLM sees current market prices.
        pre_llm_live = self.fetcher.fetch_live_price(symbol)
        if pre_llm_live is None:
            pre_llm_live = current_price  # fall back to tick-start price

        pre_llm_slippage = abs(pre_llm_live - signal_result.entry) / signal_result.entry * 100 if signal_result.entry > 0 else 0
        max_slippage = float(os.getenv("MAX_ENTRY_SLIPPAGE_PCT", "1.5"))

        # Extract LLM notes/setup_type early — needed for both pending and immediate fills
        _llm_notes_early = entry_reasons.get("llm_notes", "")
        _setup_type_early = ""
        if "setup=" in _llm_notes_early:
            try:
                _st = _llm_notes_early.split("setup=")[1].split(" ")[0].split("|")[0].strip()
                _setup_type_early = _st[:30]
            except Exception:
                pass

        if pre_llm_slippage > max_slippage:
            # Price moved too far for a market order. Instead of discarding,
            # place a pending limit order at the strategy-computed entry price.
            # This lets the exchange fill us if price retraces to our level.
            if not self.pending_orders.get_pending_for_symbol(symbol):
                order_id = self.pending_orders.place(
                    symbol=symbol,
                    side=side,
                    entry_price=signal_result.entry,
                    qty=qty,
                    sl=adj_sl,
                    tp1=adj_tp1,
                    tp2=adj_tp2,
                    atr=signal_result.atr,
                    leverage=lev_decision.leverage,
                    strategy=signal_result.strategy,
                    confidence=signal_result.confidence,
                    trade_profile=trade_prof,
                    entry_reasons=entry_reasons,
                    regime=signal_result.metadata.get("regime", ""),
                    notes=_llm_notes_early[:200],
                    setup_type=_setup_type_early,
                )
                if order_id:
                    logger.info(
                        f"[{trace_id}][{symbol}] PENDING LIMIT: {side} @ "
                        f"{_fmt_price(signal_result.entry)} (live={_fmt_price(pre_llm_live)}, "
                        f"slip={pre_llm_slippage:.1f}%) — waiting for fill"
                    )
            else:
                logger.info(
                    f"[{trace_id}][{symbol}] Already has pending order, skipping"
                )
            # Remove accumulated LLM triggers for this symbol to avoid
            # wasting LLM calls on signals we already know are stale
            self._llm_triggers.remove_symbol_events(symbol)
            # Track slippage-rejected symbols with cooldown to prevent
            # the same stale signal from re-triggering LLM every tick
            self._slippage_reject_cooldown[symbol] = time.time()
            return

        # Shift entry + SL/TP to live price so entire pipeline uses fresh numbers
        _price_shift = pre_llm_live - signal_result.entry
        signal_result.entry = pre_llm_live
        adj_sl = adj_sl + _price_shift
        adj_tp1 = adj_tp1 + _price_shift
        adj_tp2 = adj_tp2 + _price_shift

        # Safety: ensure SL/TP still on correct side after shift
        if side == "LONG":
            if adj_sl >= signal_result.entry:
                adj_sl = signal_result.entry - signal_result.atr * 1.5 if signal_result.atr > 0 else signal_result.entry * 0.98
        else:
            if adj_sl <= signal_result.entry:
                adj_sl = signal_result.entry + signal_result.atr * 1.5 if signal_result.atr > 0 else signal_result.entry * 1.02

        logger.info(
            f"[{trace_id}][{symbol}] Price refreshed: {_fmt_price(pre_llm_live)} "
            f"(shift={_price_shift:+.6f}, slip={pre_llm_slippage:.2f}%)"
        )

        # ── VETO_ONLY+ mode: synchronous LLM check before trade entry ──
        # Build a TradeCandidate for dual-world logging regardless of mode
        candidate = TradeCandidate(
            symbol=symbol,
            side=side,
            entry=signal_result.entry,
            sl=adj_sl,
            tp1=adj_tp1,
            tp2=adj_tp2,
            atr=signal_result.atr,
            ensemble_confidence=signal_result.confidence,
            ensemble_strategy=signal_result.strategy,
            entry_type=trade_prof.entry_type,
            primary_driver=trade_prof.primary_driver,
            regime=trade_prof.regime,
            timestamp=time.time(),
            trace_id=trace_id,
            num_agree=num_agree,
            strategies_agree=signal_result.metadata.get("strategies_agree", []),
            risk_reward_tp1=rr1,
        )

        # WAVE2B L1 (R3 sibling): the conf<60 "cost gate" that skipped the LLM
        # veto consult on the mechanical path is DEAD by default — same
        # opinion-as-quota confusion as R3/R7 (IC≈0 confidence deciding who
        # gets the brain). Kill-switch: ROUTER_VETO_CONF_ENFORCE=true restores.
        _veto_conf = signal_result.confidence if hasattr(signal_result, 'confidence') else 0
        _veto_conf_enforce = os.getenv("ROUTER_VETO_CONF_ENFORCE", "false").lower() == "true"
        if llm_has_veto(self.llm_mode) and _veto_conf < 60 and _veto_conf_enforce:
            logger.info(
                f"[{trace_id}][{symbol}] LLM SKIP (ENFORCED by ROUTER_VETO_CONF_ENFORCE): "
                f"confidence {_veto_conf:.0f}% < 60% threshold"
            )
        elif llm_has_veto(self.llm_mode):
            if _veto_conf < 60:
                logger.info(
                    f"[{trace_id}][{symbol}] [SHADOW-ROUTER] veto-conf gate would skip "
                    f"LLM veto (conf {_veto_conf:.0f}% < 60%) — consulting anyway (not enforced)"
                )
            veto_result = self._llm_veto_check(candidate, trace_id)
            if veto_result is not None:
                # LLM vetoed this trade
                candidate.llm_action = "flat"
                candidate.llm_confidence = veto_result.decision.confidence if veto_result.decision else None
                candidate.llm_regime = veto_result.decision.regime if veto_result.decision else None
                candidate.llm_notes = veto_result.decision.notes if veto_result.decision else veto_result.reason
                candidate.leverage_used = lev_decision.leverage
                self._candidate_logger.log_candidate(candidate)

                # Agent Performance: record critic veto decision
                if self._agent_perf:
                    try:
                        self._agent_perf.record_decision(
                            agent_name="critic",
                            symbol=symbol,
                            action="veto",
                            confidence=veto_result.decision.confidence if veto_result.decision else 0.5,
                            context={
                                "regime": veto_result.decision.regime if veto_result.decision else "",
                                "side": side,
                                "veto_reason": (candidate.llm_notes or "")[:100],
                            },
                        )
                    except Exception:
                        pass

                log_rejection(symbol, "LLM_VETO",
                              confidence=signal_result.confidence)
                logger.info(
                    f"[{trace_id}][{symbol}] LLM VETO: {side} trade rejected | "
                    f"reason: {veto_result.reason}"
                )
                self.alerts.send_market_update(
                    f"[LLM VETO] {symbol} {side} {trade_prof.entry_type} "
                    f"conf={signal_result.confidence:.0f}% "
                    f"lev={lev_decision.leverage:.1f}x\n"
                    f"Reason: {candidate.llm_notes or 'no reason given'}"
                )

                # Growth intelligence: track veto for outcome analysis
                try:
                    self.growth.on_veto(
                        symbol=symbol,
                        side=side,
                        confidence=signal_result.confidence,
                        entry_price=signal_result.entry,
                        sl_price=signal_result.sl,
                        tp1_price=signal_result.tp1,
                        tp2_price=signal_result.tp2,
                        llm_reason=candidate.llm_notes or "",
                        regime=signal_result.metadata.get("regime", ""),
                        trigger="pre_trade_veto",
                        strategies_agreed=num_agree,
                    )
                except Exception as e:
                    logger.debug(f"Growth veto record error: {e}")

                # Learning Mode: record counterfactual for veto accuracy tracking
                if _LEARNING_MODE_AVAILABLE and is_learning_mode_active():
                    try:
                        record_counterfactual(
                            llm_would_have_vetoed=True,
                            actual_outcome="PENDING",  # Will be resolved later
                            pnl=0.0,
                            symbol=symbol,
                            reasoning=candidate.llm_notes or "",
                        )
                    except Exception:
                        pass

                # Wave 4: Counterfactual — record vetoed trade for later resolution
                if self.counterfactual:
                    try:
                        self.counterfactual.record_veto(
                            symbol=symbol,
                            side=side,
                            entry_price=signal_result.entry,
                            sl_price=signal_result.sl,
                            tp1_price=signal_result.tp1,
                            tp2_price=signal_result.tp2,
                            confidence=signal_result.confidence,
                            reason=candidate.llm_notes or "LLM veto",
                        )
                    except Exception as e:
                        logger.debug(f"Counterfactual veto record error: {e}")

                # Learning Mode: in ABSORB phase, override veto to proceed
                if _LEARNING_MODE_AVAILABLE and is_learning_mode_active():
                    try:
                        _lm_action, _lm_size, _lm_reason = apply_learning_constraints(
                            llm_action="flat",
                            llm_confidence=candidate.llm_confidence or 0.5,
                            llm_size_multiplier=1.0,
                            signal_confidence=signal_result.confidence,
                        )
                        if _lm_action != "flat":
                            logger.info(
                                f"[{trace_id}][{symbol}] Learning mode OVERRIDE: "
                                f"veto -> {_lm_action} ({_lm_reason})"
                            )
                            candidate.llm_action = _lm_action
                            # Fall through to proceed instead of returning
                        else:
                            return  # Veto still stands
                    except Exception:
                        return  # Default: respect veto if learning mode fails
                else:
                    return
            else:
                # Finding 18 (2026-04-15): distinguish real LLM approval from
                # fail-open fallbacks. Previously this unconditionally stamped
                # `llm_action="proceed"` on every non-veto path including LLM
                # failures, hiding true LLM state in the metadata. Now we only
                # mark "proceed" when the LLM actually returned an approval;
                # anything else is tagged "no_llm" so analytics can distinguish
                # "LLM said go" from "LLM was skipped/failed".
                if veto_result is None:
                    # _llm_veto_check returned None for two reasons:
                    #   1. LLM wasn't consulted (throttled, disabled, below cost gate)
                    #   2. LLM was called and approved (candidate.llm_* already set)
                    # Preserve any value _llm_veto_check wrote; otherwise tag no_llm.
                    if not candidate.llm_action:
                        candidate.llm_action = "no_llm"
                else:
                    # veto_result is a DecisionResult object (LLM was called)
                    _dec = getattr(veto_result, 'decision', None)
                    if _dec is not None and getattr(_dec, 'action', None) == "proceed":
                        candidate.llm_action = "proceed"
                        candidate.llm_confidence = _dec.confidence
                        candidate.llm_notes = _dec.notes
                    else:
                        candidate.llm_action = "no_llm"

        # Learning Mode: record signal observation
        if _LEARNING_MODE_AVAILABLE and is_learning_mode_active():
            try:
                learning_record_signal(
                    symbol=symbol,
                    side=side,
                    confidence=signal_result.confidence,
                    regime=signal_result.metadata.get("regime", ""),
                    strategies=signal_result.metadata.get("strategies_agree", [signal_result.strategy]),
                    num_agree=num_agree,
                )
            except Exception:
                pass

        # Agent Performance: record trade approval decision
        if self._agent_perf:
            try:
                self._agent_perf.record_decision(
                    agent_name="trade",
                    symbol=symbol,
                    action="proceed",
                    confidence=signal_result.confidence / 100.0,
                    context={
                        "regime": self._tick_regime_cache.get(symbol, "unknown"),
                        "side": side,
                        "sizing_pct": lev_decision.leverage,
                    },
                )
            except Exception:
                pass

        # Log candidate as proceeding (will be updated with outcome on close)
        candidate.llm_action = candidate.llm_action or "no_llm"
        candidate.leverage_used = lev_decision.leverage
        self._candidate_logger.log_candidate(candidate)
        # Track for outcome backfill when trade closes
        self._active_candidates[symbol] = candidate

        # Update entry_reasons with LLM decision info now that candidate is populated
        entry_reasons["llm_action"] = candidate.llm_action or ""
        entry_reasons["llm_confidence"] = getattr(candidate, 'llm_confidence', 0.0) or 0.0
        # Audit #40: only an explicit LLM proceed/go counts as agreement —
        # ""/"no_llm"/None mean the LLM never judged the trade, not that it agreed.
        entry_reasons["llm_agreed"] = candidate.llm_action in ("proceed", "go")
        entry_reasons["llm_notes"] = getattr(candidate, 'llm_notes', '') or ""

        # ── LLM size multiplier: apply the meta-brain's sizing adjustment ──
        # In SIZING+ modes, the LLM can scale position size 0.5x-2.0x
        #
        # TODO(architecture): AUTHORITATIVE LLM SIZING REDESIGN
        # Current problem: Risk Agent outputs sz=1.0 but 19 downstream multipliers
        # compound to ~0.027x, crushing positions to dust. The LLM sz is applied
        # AFTER the mechanical chain as just another multiplier on top.
        #
        # Target design (when LLM_MODE >= SIZING):
        #   1. Calculate base_qty = (equity * risk_per_trade * llm_sz) / (stop_width * leverage)
        #      where llm_sz is the Risk Agent's authoritative sizing (0.3-2.0)
        #   2. SKIP the mechanical multiplier chain (lines 4794-4918):
        #      - Skip: correlation guard, CorrelationGate, sector exposure, global bias,
        #        portfolio risk engine, time-aware sizing, liquidity guard, reflection engine
        #      (Risk Agent prompt now incorporates all these factors in its sz decision)
        #   3. KEEP only safety caps after LLM sz:
        #      - Circuit breaker (CB) override (hard safety, not sizing opinion)
        #      - Notional cap (15x equity hard limit)
        #      - Exchange minimum ($10 Hyperliquid floor)
        #      - QTY floor (exchange round_qty ROUND_DOWN margin)
        #   4. Formula: qty = risk_mgr.calculate_qty(entry, sl, leverage, risk_per_trade) * llm_sz
        #      Then apply ONLY: CB cap, notional cap, exchange min floor.
        #
        # Implementation approach:
        #   - Add an early branch after line 4788 (base qty calculation):
        #     if self.llm_mode >= LLMMode.SIZING and llm_sz is not None:
        #         qty = base_qty * llm_sz  # Risk Agent is the authority
        #         # jump past mechanical chain to safety caps
        #   - The vol-targeting (line 4754) that adjusts _sym_risk should also be
        #     skipped when LLM is authoritative — Risk Agent accounts for vol regime.
        #   - Keep adaptive_risk and RL policy as optional post-LLM adjustments
        #     ONLY if they are safety-critical (e.g., streak circuit breaker).
        #
        # Depends on: moving llm_size_mult extraction earlier in the pipeline,
        # before the mechanical sizing chain runs.
        # ── LLM AUTHORITATIVE SIZING RESTORE ──
        # If LLM authority bypass was active, the mechanical chain ran but
        # we now RESTORE qty to the LLM-decided value (base_qty * llm_sz).
        # The mechanical chain's modifications are discarded.
        if _llm_authority_active:
            _pre_restore = qty
            qty = _original_qty * _llm_sz_early  # Restore to LLM-decided size
            # Re-apply ONLY circuit breaker (hard safety)
            if cb_constraints.get("constrained"):
                qty = qty * cb_constraints["size_multiplier"]
            logger.info(
                f"[{trace_id}][{symbol}] LLM SIZING RESTORED: "
                f"mechanical={_pre_restore:.6f} → LLM={qty:.6f} "
                f"(base * {_llm_sz_early:.2f})"
            )
        else:
            # Non-LLM path: apply the old LLM-sz-as-multiplier logic
            llm_sz = getattr(candidate, 'llm_size_mult', None)
            if llm_sz is not None and llm_sz != 1.0 and self.llm_mode >= LLMMode.SIZING:
                # Learning Mode: constrain size adjustment during learning phases
                if _LEARNING_MODE_AVAILABLE and is_learning_mode_active():
                    try:
                        _, llm_sz, _lm_reason = apply_learning_constraints(
                            llm_action="proceed",
                            llm_confidence=candidate.llm_confidence or 0.5,
                            llm_size_multiplier=llm_sz,
                            signal_confidence=signal_result.confidence,
                        )
                    except Exception:
                        pass
                llm_sz = max(0.5, min(2.0, llm_sz))  # Safety clamp
            qty = qty * llm_sz
            logger.info(
                f"[{trace_id}][{symbol}] LLM size mult: qty * {llm_sz:.2f} = {qty:.6f}"
            )

        # ── Adaptive Risk: dynamic risk-per-trade multiplier based on streak/regime ──
        if self.adaptive_risk:
            try:
                _regime_label = signal_result.metadata.get("regime", "")
                _sym_wr = self.feedback.quality.get_symbol_win_rate(symbol) if hasattr(self.feedback.quality, 'get_symbol_win_rate') else 0.0
                _ar_mult = self.adaptive_risk.get_risk_multiplier(
                    regime=_regime_label,
                    symbol_wr=_sym_wr,
                )
                if _ar_mult != 1.0:
                    qty = qty * _ar_mult
                    logger.info(
                        f"[{trace_id}][{symbol}] Adaptive risk: qty * {_ar_mult:.3f} = {qty:.6f}"
                    )
            except Exception as e:
                logger.debug(f"Adaptive risk application error: {e}")

        # ── RL policy multiplier: apply learned regime/symbol adjustments ──
        if is_rl_enabled():
            try:
                _rl_regime = signal_result.metadata.get("regime", "unknown")
                _rl_mult = get_combined_rl_multiplier(symbol, _rl_regime)
                if _rl_mult != 1.0:
                    qty = qty * _rl_mult
                    logger.info(
                        f"[{trace_id}][{symbol}] RL policy: qty * {_rl_mult:.2f} = {qty:.6f}"
                    )
            except Exception as e:
                logger.debug(f"RL policy application error: {e}")

        # ── Profitable pattern boost: confirmed winners get larger size ──
        # Uses deep memory: if this strategy+regime+symbol combo has >60% WR
        # over 10+ trades, allow up to 1.3x size boost
        try:
            sym_data = self.feedback.quality.by_symbol.get(symbol)
            strat_data = self.feedback.quality.by_strategy.get(signal_result.strategy)
            regime_data = self.feedback.quality.by_regime.get(
                signal_result.metadata.get("regime", "")
            )
            # Need at least one dimension with enough data
            pattern_boost = 1.0
            if sym_data and sym_data["total"] >= 10:
                sym_wr = sym_data["wins"] / sym_data["total"]
                if sym_wr >= 0.60:
                    pattern_boost = max(pattern_boost, 1.0 + (sym_wr - 0.50) * 0.6)
            if strat_data and strat_data["total"] >= 10:
                strat_wr = strat_data["wins"] / strat_data["total"]
                if strat_wr >= 0.60:
                    pattern_boost = max(pattern_boost, 1.0 + (strat_wr - 0.50) * 0.6)
            # Regime alignment: if this regime has good WR, boost
            if regime_data and regime_data["total"] >= 8:
                reg_wr = regime_data["wins"] / regime_data["total"]
                if reg_wr >= 0.55:
                    pattern_boost = max(pattern_boost, 1.0 + (reg_wr - 0.50) * 0.4)
            # Cap at 1.3x (confirmed profitable = up to 30% more aggressive)
            pattern_boost = min(1.3, pattern_boost)
            if pattern_boost > 1.0:
                qty = qty * pattern_boost
                logger.info(
                    f"[{trace_id}][{symbol}] Profitable pattern boost: "
                    f"qty * {pattern_boost:.2f} = {qty:.6f}"
                )
        except Exception:
            pass

        # ── MIN NOTIONAL FLOOR: ensure position meets exchange minimum ──
        # After ALL multipliers have been applied, check if the final qty * price
        # still meets the $10 Hyperliquid minimum. If not, bump qty UP.
        # This prevents the multiplier chain (risk_mult * HTF * time * adaptive * LLM * RL)
        # from crushing positions below exchange minimums on small accounts.
        _MIN_NOTIONAL = 10.0  # Hyperliquid minimum
        _NOTIONAL_FLOOR_MARGIN = 1.15  # 15% margin to survive round_qty ROUND_DOWN
        _pre_floor_qty = qty
        _pre_floor_notional = qty * signal_result.entry
        if _pre_floor_notional < _MIN_NOTIONAL * _NOTIONAL_FLOOR_MARGIN:
            _floor_qty = (_MIN_NOTIONAL * _NOTIONAL_FLOOR_MARGIN) / signal_result.entry
            # Sanity: don't let the floor exceed 2x the original base qty
            # (prevents unbounded risk on very small signals)
            if _original_qty > 0 and _floor_qty > _original_qty * 2.0:
                logger.warning(
                    f"[{trace_id}][{symbol}] MIN_NOTIONAL floor would need {_floor_qty:.6f} "
                    f"(>{_original_qty * 2.0:.6f} = 2x base) — rejecting as too risky"
                )
                log_rejection(symbol, "MIN_NOTIONAL_FLOOR_TOO_RISKY",
                              confidence=signal_result.confidence)
                return
            qty = max(qty, _floor_qty)
            # Also bump to exchange min_qty if needed
            _floor_min_q = get_min_qty(symbol)
            if qty < _floor_min_q:
                qty = _floor_min_q
            logger.info(
                f"[{trace_id}][{symbol}] MIN_NOTIONAL floor applied: qty {_pre_floor_qty:.6f} "
                f"-> {qty:.6f} (notional ${_pre_floor_notional:.2f} -> ${qty * signal_result.entry:.2f})"
            )

        # ── Hard cap: no single position > 15x equity in notional ──
        _MAX_SINGLE_POSITION_LEVERAGE = 15.0
        _single_notional = qty * signal_result.entry * lev_decision.leverage
        _equity = self.risk_mgr.equity or 1.0
        if _single_notional > _MAX_SINGLE_POSITION_LEVERAGE * _equity:
            _capped_notional = _MAX_SINGLE_POSITION_LEVERAGE * _equity
            qty = _capped_notional / (signal_result.entry * lev_decision.leverage)
            logger.warning(
                f"[{trace_id}][{symbol}] HARD NOTIONAL CAP: "
                f"${_single_notional:.0f} > 15x equity (${_equity:.0f}), "
                f"capped to ${_capped_notional:.0f}, qty={qty:.6f}"
            )

        # ── Push 1: Portfolio notional cap ──
        # Prevent aggregate over-leverage across all positions
        _new_notional = qty * signal_result.entry * lev_decision.leverage
        if not self.pos_mgr.check_portfolio_notional_cap(
            new_notional=_new_notional,
            equity=self.risk_mgr.equity,
            max_portfolio_leverage=self.config.max_portfolio_leverage,
        ):
            log_rejection(symbol, "PORTFOLIO_NOTIONAL_CAP",
                          confidence=signal_result.confidence,
                          new_notional=_new_notional)
            return

        # ── OpsGuard: rate limiting, exposure limits ──
        position_size_usd = qty * signal_result.entry * lev_decision.leverage
        total_exposure = sum(
            p.qty * p.entry * p.leverage
            for p in self.pos_mgr.get_open_positions().values()
        )
        ops_check = self.ops_guard.can_execute(
            position_size_usd=position_size_usd,
            equity=self.risk_mgr.equity,
            total_exposure_usd=total_exposure,
        )
        if not ops_check["allowed"]:
            Telemetry.inc("throttle_blocks")
            log_rejection(symbol, "OPS_GUARD", confidence=signal_result.confidence)
            logger.warning(f"[{trace_id}][{symbol}] OPS GUARD: {ops_check['reason']}")
            return

        # ── LIVE PRICE ENTRY: fetch fresh price right before opening ──
        # signal_result.entry is the stale candle-close price from strategy eval.
        # We need the actual live market price for accurate entry/TP/SL/PnL.
        snapshot_entry = signal_result.entry
        live_entry = self.fetcher.fetch_live_price(symbol)
        if live_entry is None:
            live_entry = current_price  # fall back to tick-start price

        # Compute slippage between signal snapshot and live execution price
        slippage_pct = abs(live_entry - snapshot_entry) / snapshot_entry * 100 if snapshot_entry > 0 else 0
        max_slippage = float(os.getenv("MAX_ENTRY_SLIPPAGE_PCT", "1.5"))

        if slippage_pct > max_slippage:
            log_rejection(symbol, "ENTRY_SLIPPAGE",
                          confidence=signal_result.confidence)
            logger.warning(
                f"[{trace_id}][{symbol}] ENTRY REJECTED: slippage {slippage_pct:.2f}% "
                f"(snapshot={_fmt_price(snapshot_entry)} live={_fmt_price(live_entry)} "
                f"max={max_slippage}%)"
            )
            return

        # Use live price as actual entry
        actual_entry = live_entry

        # Shift TP/SL proportionally to match the live entry price
        entry_shift = actual_entry - snapshot_entry
        adj_sl = adj_sl + entry_shift
        adj_tp1 = adj_tp1 + entry_shift
        adj_tp2 = adj_tp2 + entry_shift

        # Safety: ensure SL/TP are still on the correct side of entry
        if side == "LONG":
            if adj_sl >= actual_entry:
                adj_sl = actual_entry - signal_result.atr * 1.5 if signal_result.atr > 0 else actual_entry * 0.98
            if adj_tp1 <= actual_entry:
                adj_tp1 = actual_entry + signal_result.atr * 1.0 if signal_result.atr > 0 else actual_entry * 1.01
            if adj_tp2 <= actual_entry:
                adj_tp2 = actual_entry + signal_result.atr * 2.0 if signal_result.atr > 0 else actual_entry * 1.02
        else:  # SHORT
            if adj_sl <= actual_entry:
                adj_sl = actual_entry + signal_result.atr * 1.5 if signal_result.atr > 0 else actual_entry * 1.02
            if adj_tp1 >= actual_entry:
                adj_tp1 = actual_entry - signal_result.atr * 1.0 if signal_result.atr > 0 else actual_entry * 0.99
            if adj_tp2 >= actual_entry:
                adj_tp2 = actual_entry - signal_result.atr * 2.0 if signal_result.atr > 0 else actual_entry * 0.98

        # MFE-based TP scaling — data-driven from 500+ candle analysis.
        # Median 2h MFE by asset (how far price typically moves in our favor):
        #   BTC: 0.38%, SOL: 0.51%, ETH: 0.44%, HYPE: 0.78%
        # TP1 = median 2h MFE (reachable 50% of time)
        # TP2 = p75 4h MFE (reachable 25% of time, bigger win)
        # SL uses p75 2h MAE (only hit 25% of time)
        _MFE_TP1 = {"BTC": 0.0038, "SOL": 0.0051, "ETH": 0.0044, "HYPE": 0.0078}
        _MFE_TP2 = {"BTC": 0.0099, "SOL": 0.0134, "ETH": 0.0132, "HYPE": 0.0189}
        _eff_lev = lev_decision.leverage if lev_decision.leverage > 0 else 1.0
        if _eff_lev > 5.0 and symbol in _MFE_TP1:
            _sl_dist = abs(actual_entry - adj_sl)
            _mfe_tp1_dist = actual_entry * _MFE_TP1[symbol]
            _mfe_tp2_dist = actual_entry * _MFE_TP2[symbol]
            # Use the tighter of: MFE-based or leverage-compressed ATR-based
            _atr_tp1_dist = abs(actual_entry - adj_tp1)
            _atr_tp2_dist = abs(actual_entry - adj_tp2)
            _new_tp1_dist = min(_mfe_tp1_dist, _atr_tp1_dist)
            _new_tp2_dist = min(_mfe_tp2_dist, _atr_tp2_dist)
            # Enforce minimum R:R of 1.0 vs SL
            if _sl_dist > 0:
                _new_tp1_dist = max(_new_tp1_dist, _sl_dist * 1.0)
                _new_tp2_dist = max(_new_tp2_dist, _sl_dist * 2.0)
            if side == "LONG":
                adj_tp1 = actual_entry + _new_tp1_dist
                adj_tp2 = actual_entry + _new_tp2_dist
            else:
                adj_tp1 = actual_entry - _new_tp1_dist
                adj_tp2 = actual_entry - _new_tp2_dist
            _rr = _new_tp1_dist / _sl_dist if _sl_dist > 0 else 0
            logger.info(
                f"[{symbol}] MFE-based TP for {_eff_lev:.0f}x: "
                f"TP1 {_atr_tp1_dist:.2f}->{_new_tp1_dist:.2f} ({_new_tp1_dist/actual_entry*100:.2f}%) "
                f"TP2 {_atr_tp2_dist:.2f}->{_new_tp2_dist:.2f} R:R={_rr:.1f}:1"
            )

        # Recalculate qty with live entry price (stop distance may have changed)
        live_sl_dist = abs(actual_entry - adj_sl)
        snapshot_sl_dist = abs(snapshot_entry - (adj_sl - entry_shift))
        if snapshot_sl_dist > 0 and live_sl_dist > 0:
            sl_ratio = snapshot_sl_dist / live_sl_dist
            # Only adjust if the change is significant (>5% wider)
            if sl_ratio < 0.95:
                qty = qty * sl_ratio
            min_q = get_min_qty(symbol)
            if qty < min_q:
                # Don't reject — bump to min qty (aggressive mode)
                qty = min_q
                logger.info(f"[{trace_id}][{symbol}] Live SL wider: qty bumped to min {min_q}")

        if slippage_pct > 0.1:
            logger.info(
                f"[{trace_id}][{symbol}] Entry slippage: {slippage_pct:.2f}% "
                f"(snapshot={_fmt_price(snapshot_entry)} -> live={_fmt_price(actual_entry)})"
            )

        # Track snapshot vs live for dual-entry analysis
        entry_reasons["snapshot_entry"] = snapshot_entry
        entry_reasons["live_entry"] = actual_entry
        entry_reasons["entry_slippage_pct"] = round(slippage_pct, 4)

        # Collect as rotation candidate for other open positions
        if self.rotation_mgr:
            with self._tick_candidates_lock:
                self._tick_candidates.append({
                    "symbol": symbol,
                    "side": signal_result.side,
                    "entry": actual_entry,
                    "sl": adj_sl,
                    "tp1": adj_tp1,
                    "tp2": adj_tp2,
                    "atr": signal_result.atr,
                    "confidence": signal_result.confidence,
                    "align_score": signal_result.metadata.get("num_agree", 1),
                    "strategy": signal_result.strategy,
                    "rr1": rr1,
                })

        # Telemetry: record slippage (trade count deferred until order confirms)
        Telemetry.record("slippages", slippage_pct)

        # Open position with LIVE price as entry
        # Extract LLM thesis and setup type for Exit Agent thesis continuity
        _llm_notes = entry_reasons.get("llm_notes", "")
        _setup_type = ""
        if "setup=" in _llm_notes:
            try:
                _st = _llm_notes.split("setup=")[1].split(" ")[0].split("|")[0].strip()
                _setup_type = _st[:30]
            except Exception:
                pass

        # ── PRE-TRADE SIMULATOR: scenario-based imagination before entry ──
        if self._pre_trade_sim:
            try:
                _sim_result = self._pre_trade_sim.simulate(
                    symbol=symbol,
                    side=signal_result.side,
                    entry=actual_entry,
                    sl=adj_sl,
                    tp1=adj_tp1,
                    leverage=lev_decision.leverage,
                    current_portfolio=self.pos_mgr.get_open_positions(),
                    market_data={
                        "prices": self._last_prices,
                        "atr": signal_result.atr,
                        "equity": self.risk_mgr.equity,
                        "is_weekend": datetime.now(timezone.utc).weekday() >= 5,
                    },
                )
                logger.info(
                    f"[{trace_id}][{symbol}] PRE-TRADE SIM: "
                    f"EV=${_sim_result.get('expected_value', 0):.2f} "
                    f"max_loss=${_sim_result.get('max_loss', 0):.2f} "
                    f"rec={_sim_result.get('recommendation', '?')}"
                )
                # Store for LLM context enrichment (agents can read this)
                self._last_simulation[symbol] = _sim_result
                # Add sim summary to entry reasons for post-trade analysis
                entry_reasons["pre_trade_sim"] = {
                    "ev": _sim_result.get("expected_value", 0),
                    "max_loss": _sim_result.get("max_loss", 0),
                    "recommendation": _sim_result.get("recommendation", ""),
                    "correlation_risk": _sim_result.get("portfolio_impact", {}).get("correlation_risk", ""),
                }
            except Exception as _sim_err:
                logger.debug(f"[{trace_id}][{symbol}] Pre-trade sim error: {_sim_err}")

        # Per-symbol execution lock: atomic check-and-acquire prevents two signals
        # for the same symbol from racing through the pipeline simultaneously.
        with self._executing_lock:
            if symbol in self._executing_symbols:
                logger.warning(
                    f"[{trace_id}][{symbol}] DUPLICATE BLOCKED by execution lock: "
                    f"another signal is already being executed. Aborting."
                )
                return
            self._executing_symbols.add(symbol)

        # Final duplicate position guard — last line of defense before order submission.
        # Checks both position manager state AND ops guard to prevent the
        # 9-BTC-SHORT-in-one-day bug where multiple code paths could open duplicates.
        if self.pos_mgr.has_open_position(symbol):
            with self._executing_lock:
                self._executing_symbols.discard(symbol)
            logger.warning(
                f"[{trace_id}][{symbol}] DUPLICATE BLOCKED at execution gate: "
                f"position already exists. Aborting order."
            )
            return
        _dup_check = self.ops_guard.check_duplicate_position(
            symbol=symbol,
            side=side,
            open_positions=self.pos_mgr.get_open_positions(),
        )
        if not _dup_check["allowed"]:
            with self._executing_lock:
                self._executing_symbols.discard(symbol)
            logger.warning(
                f"[{trace_id}][{symbol}] DUPLICATE BLOCKED by OpsGuard: "
                f"{_dup_check['reason']}"
            )
            return

        # Submit order to exchange (paper=simulated, live=real)
        # Try limit order first for better fills (saves ~3 bps: 0.045% taker → 0.015% maker)
        _order_type = "market"
        _limit_price = actual_entry
        if self.entry_optimizer is not None:
            try:
                _num_agree = len(entry_reasons.get("strategies_agree", []))
                _entry_decision = self.entry_optimizer.evaluate_entry(
                    symbol=symbol, side=side, entry_price=actual_entry,
                    confidence=confidence, num_agree=_num_agree,
                )
                if _entry_decision.action == "LIMIT" and _entry_decision.limit_price:
                    _order_type = "limit"
                    _limit_price = _entry_decision.limit_price
                    logger.info(
                        f"[{trace_id}][{symbol}] Entry optimizer: LIMIT @ ${_limit_price:.4f} "
                        f"({_entry_decision.improvement_pct:.2f}% improvement) — {_entry_decision.rationale}"
                    )
            except Exception as _eo_err:
                logger.debug(f"[{trace_id}][{symbol}] Entry optimizer error: {_eo_err}")

        order_result = self.order_executor.open_position(
            symbol=symbol,
            side=side,
            qty=qty,
            price=_limit_price,
            leverage=int(lev_decision.leverage),
            order_type=_order_type,
        )

        # If limit order didn't fill, fall back to market
        if _order_type == "limit" and not order_result.filled:
            logger.info(f"[{trace_id}][{symbol}] Limit order did not fill, falling back to market")
            order_result = self.order_executor.open_position(
                symbol=symbol,
                side=side,
                qty=qty,
                price=actual_entry,
                leverage=int(lev_decision.leverage),
                order_type="market",
            )

        if not order_result.filled:
            logger.warning(
                f"[{trace_id}][{symbol}] Order FAILED: {order_result.error}"
            )
            return

        # Record trade for rate limiting AFTER order confirms (not before)
        Telemetry.inc("total_trades")
        self.ops_guard.record_trade()

        # Use actual fill price and qty from exchange
        actual_entry = order_result.fill_price if order_result.fill_price > 0 else actual_entry
        qty = order_result.fill_qty if order_result.fill_qty > 0 else qty

        # Record fill quality for execution analytics
        if hasattr(self, 'execution_analytics') and self.execution_analytics:
            try:
                self.execution_analytics.record_fill(
                    trade_id=f"{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=side,
                    expected_price=snapshot_entry,
                    actual_fill=order_result.fill_price if order_result.fill_price > 0 else actual_entry,
                    notional=qty * actual_entry,
                    regime=self._tick_regime_cache.get(symbol, "unknown"),
                    signal_time=entry_reasons.get("signal_time", time.time()),
                )
            except Exception as e:
                logger.debug(f"Execution analytics record error: {e}")

        self.pos_mgr.open_position(
            symbol=symbol,
            side=side,
            entry=actual_entry,
            qty=qty,
            sl=adj_sl,
            tp1=adj_tp1,
            tp2=adj_tp2,
            atr=signal_result.atr,
            leverage=lev_decision.leverage,
            mode=lev_decision.mode,
            strategy=signal_result.strategy,
            confidence=signal_result.confidence,
            tp1_close_pct=tp1_pct,
            entry_reasons=entry_reasons,
            trade_profile=trade_prof,
            notes=_llm_notes[:200],
            setup_type=_setup_type,
        )

        # Release execution lock — position is now registered, duplicate checks will see it
        with self._executing_lock:
            self._executing_symbols.discard(symbol)

        # Pipeline telemetry: record successful trade
        try: _get_pt().finish_journey(symbol, traded=True, qty=qty, notional=qty * actual_entry, leverage=lev_decision.leverage)
        except Exception: pass

        # Reflection Engine: analyze entry quality
        try:
            if self._reflection_engine is not None:
                _entry_analysis = self._reflection_engine.on_entry(
                    symbol=symbol,
                    side=side,
                    entry_price=actual_entry,
                    confidence=signal_result.confidence,
                    regime=self._tick_regime_cache.get(symbol, "unknown"),
                    atr=signal_result.atr,
                    win_prob=entry_reasons.get("win_prob", 0),
                    ev=entry_reasons.get("ev_per_dollar", 0),
                )
        except Exception as e:
            logger.debug(f"Reflection entry analysis error: {e}")

        # Log trade open to database (with live price)
        log_trade(
            symbol=symbol,
            action="OPEN",
            side=side,
            price=actual_entry,
            qty=qty,
            leverage=lev_decision.leverage,
            strategy=signal_result.strategy,
            metadata={
                "confidence": signal_result.confidence,
                "strategies": signal_result.metadata.get("strategies_agree", []),
                "snapshot_entry": snapshot_entry,
                "live_entry": actual_entry,
                "entry_slippage_pct": round(slippage_pct, 4),
            }
        )

        # Mark the original signal as traded (update, don't duplicate)
        if _signal_id:
            update_signal_traded(_signal_id, traded=True)

        # Send signal alert (enhanced format with actionable data)
        tier = signal_result.metadata.get("tier", "")
        try:
            # Fetch historical win rates for context (used by Discord/dashboard)
            _sp = get_signal_performance(7, symbol=symbol)
            _sym_wr = _sp.get("by_symbol", {}).get(symbol, {}).get("win_rate", 0)
            _sym_trades = _sp.get("by_symbol", {}).get(symbol, {}).get("trades", 0)
            _strat_wr = _sp.get("by_strategy", {}).get(signal_result.strategy, {}).get("win_rate", 0)

            # ── PREMIUM ALERT FILTER (2026-04-16) ──
            # Only send Telegram alerts for shadow-ledger-verified setups.
            # The old behavior sent every ensemble signal to Telegram (~170/day),
            # most of them low quality. Now we route through premium_filter
            # which enforces a shadow-verified quality bar. Disable by setting
            # PREMIUM_ALERTS_ENABLED=false in .env if you want the old firehose.
            _premium_enabled = os.environ.get(
                "PREMIUM_ALERTS_ENABLED", "true"
            ).lower() in ("1", "true", "yes")

            _telegram_msg = None
            _alert_decision = None

            if _premium_enabled:
                try:
                    from alerts.premium_filter import evaluate_for_alert, AlertTier
                    from alerts.premium_telegram import (
                        format_premium_execute_alert,
                        format_premium_watch_alert,
                        format_signal_skipped_debug,
                    )

                    _alert_decision = evaluate_for_alert(
                        symbol=symbol,
                        side=side,
                        strategy=signal_result.strategy,
                        confidence=signal_result.confidence,
                        num_agree=signal_result.metadata.get("num_agree", 1),
                        regime=trade_prof.regime or signal_result.metadata.get("regime", ""),
                        entry=actual_entry,
                        sl=signal_result.sl,
                        tp1=signal_result.tp1,
                        tp2=signal_result.tp2,
                        leverage=lev_decision.leverage,
                        strategies_agree=signal_result.metadata.get("strategies_agree"),
                        equity=self.risk_mgr.equity,
                        ev_per_dollar=entry_reasons.get("ev_per_dollar", 0),
                    )

                    if _alert_decision.tier == AlertTier.EXECUTE:
                        _telegram_msg = format_premium_execute_alert(
                            symbol=symbol, side=side,
                            entry=actual_entry, sl=signal_result.sl,
                            tp1=signal_result.tp1, tp2=signal_result.tp2,
                            leverage=lev_decision.leverage,
                            confidence=signal_result.confidence,
                            decision=_alert_decision,
                            strategy=signal_result.strategy,
                            regime=trade_prof.regime or "",
                            num_agree=signal_result.metadata.get("num_agree", 1),
                            total_strategies=len(self.strategies),
                        )
                    elif _alert_decision.tier == AlertTier.WATCH:
                        _telegram_msg = format_premium_watch_alert(
                            symbol=symbol, side=side,
                            entry=actual_entry, sl=signal_result.sl,
                            tp1=signal_result.tp1, tp2=signal_result.tp2,
                            leverage=lev_decision.leverage,
                            confidence=signal_result.confidence,
                            decision=_alert_decision,
                            strategy=signal_result.strategy,
                            regime=trade_prof.regime or "",
                            num_agree=signal_result.metadata.get("num_agree", 1),
                            total_strategies=len(self.strategies),
                        )
                    else:
                        # Alert filtered. Log the skip reason so we can audit filter quality.
                        logger.info(
                            format_signal_skipped_debug(
                                symbol=symbol, side=side,
                                decision=_alert_decision,
                                confidence=signal_result.confidence,
                            )
                        )
                except Exception as _pe:
                    logger.warning(
                        f"[{trace_id}][{symbol}] Premium filter error: {_pe}. "
                        f"Falling back to no alert."
                    )

            # Fallback path: if premium filter disabled, use old raw-signal alert
            if not _premium_enabled:
                _telegram_msg = format_signal_telegram(
                    symbol=symbol,
                    side=side,
                    confidence=signal_result.confidence,
                    entry=actual_entry,
                    sl=signal_result.sl,
                    tp1=signal_result.tp1,
                    tp2=signal_result.tp2,
                    leverage=lev_decision.leverage,
                    strategies_agree=signal_result.metadata.get("strategies_agree"),
                    num_agree=signal_result.metadata.get("num_agree", 1),
                    total_strategies=len(self.strategies),
                    regime=trade_prof.regime or "",
                    equity=self.risk_mgr.equity,
                    risk_per_trade=self.config.risk_per_trade,
                    win_rate_symbol=_sym_wr,
                    win_rate_strategy=_strat_wr,
                    total_trades_symbol=_sym_trades,
                    ev_per_dollar=entry_reasons.get("ev_per_dollar", 0),
                    fee_drag_pct=entry_reasons.get("fee_drag_pct", 0),
                    setup_type=entry_reasons.get("primary_driver", ""),
                    solo_trade=signal_result.metadata.get("solo_trade", False),
                )

            # Send only if we have a message (premium may filter to None)
            if _telegram_msg and self.alerts.telegram_token and self.alerts.telegram_chat_id:
                self.alerts._send_telegram(_telegram_msg)

            # Discord still always gets the raw signal (that channel is bot-operator-focused)
            self.alerts.send_signal(signal_result, lev_decision.leverage, tier)
        except Exception:
            self.alerts.send_signal(signal_result, lev_decision.leverage, tier)

        logger.info(
            f"[{trace_id}][{symbol}] OPENED {side} | "
            f"Type: {trade_prof.entry_type} | "
            f"Entry: {_fmt_price(actual_entry)} (snap={_fmt_price(snapshot_entry)} slip={slippage_pct:.2f}%) | "
            f"Conf: {original_conf:.0f}%->{signal_result.confidence:.0f}% | "
            f"Lev: {lev_decision.leverage:.1f}x ({lev_decision.reason}) | "
            f"TP1close: {tp1_pct:.0%} | Trail: {trade_prof.exit_params.trailing_style} | "
            f"Regime: {trade_prof.regime} | "
            f"Driver: {trade_prof.primary_driver} | "
            f"Strategies: {signal_result.metadata.get('strategies_agree', [signal_result.strategy])}"
        )

        # Human Copy-Trade Classifier: check if signal qualifies for copy-trading
        if _COPY_CLASSIFIER_AVAILABLE:
            try:
                _copy_result = classify_human_copy_tradable(
                    confidence=signal_result.confidence,
                    regime=trade_prof.regime or "",
                    volatility_band=trade_prof.volatility_band or "",
                    entry_type=trade_prof.entry_type or "",
                    primary_driver=trade_prof.primary_driver or "",
                    leverage=lev_decision.leverage,
                    rr=rr1,
                    snapshot_age_s=slippage_pct,
                    slippage_pct=slippage_pct,
                    circuit_breaker_active=self.risk_mgr.circuit_breaker.tripped,
                )
                if _copy_result.eligible:
                    logger.info(
                        f"[{trace_id}][{symbol}] COPY-TRADABLE: score={_copy_result.score:.0f}"
                    )
                    self.alerts.send_market_update(
                        f"*COPY-TRADE SIGNAL*\n"
                        f"{symbol} {side} @ {_fmt_price(actual_entry)}\n"
                        f"Confidence: {signal_result.confidence:.0f}%\n"
                        f"R:R: {rr1:.2f}\n"
                        f"Leverage: {lev_decision.leverage:.1f}x\n"
                        f"Regime: {trade_prof.regime}\n"
                        f"Type: {trade_prof.entry_type}\n"
                        f"Score: {_copy_result.score:.0f}/100"
                    )
                    # Tag the candidate for copy-trade tracking
                    entry_reasons["human_copy_tradable"] = True
                    entry_reasons["copy_score"] = _copy_result.score
            except Exception as e:
                logger.debug(f"Copy classifier error: {e}")

    def _compute_setup_key(self, signal_result, trade_prof) -> str:
        """Compute setup key for neuroplasticity tracking and setup-specific exits."""
        meta = signal_result.metadata or {}
        strats = meta.get("strategies_agree", [signal_result.strategy])
        sym = signal_result.symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "").split("/")[0]
        side = signal_result.side
        rg = (getattr(trade_prof, "regime", "") or meta.get("regime", "")).split("_")[0][:8]
        has_bb = "bollinger_squeeze" in strats
        has_mtq = "multi_tier_quality" in strats
        if has_bb and has_mtq:
            strat_tag = "BB+MTQ"
        elif has_bb:
            strat_tag = "BB"
        elif len(strats) >= 2:
            strat_tag = f"{len(strats)}agree"
        elif strats:
            strat_tag = strats[0][:12]
        else:
            strat_tag = "unknown"
        regime_tag = f"_{rg}" if rg else ""
        return f"{sym}_{side}_{strat_tag}{regime_tag}"

    # ══════════════════════════════════════════════════════════════════
    # ── LLM-FIRST ARCHITECTURE: Signal → Safety → LLM → Execute ──
    # ══════════════════════════════════════════════════════════════════

    def _track_llm_first_outcome(
        self,
        raw_signal,
        symbol: str,
        passed: bool,
        hard_rejected: bool,
        reason: str,
        stage: str,
        metadata: dict = None,
    ):
        """Record an LLM-first path outcome to signal_outcomes.jsonl.

        The LLM-first path doesn't flow through the mechanical annotation
        pipeline that populates signal_tracker, so without this call every
        LLM-first decision is invisible to downstream analytics.
        """
        try:
            from core.signal_tracker import get_signal_tracker
            tracker = get_signal_tracker()
            meta = metadata or {}
            meta["pipeline"] = "llm_first"
            meta["stage"] = stage
            tracker.record_signal(
                symbol=symbol,
                side=raw_signal.side or "",
                confidence=raw_signal.confidence or 0,
                strategy=raw_signal.strategy or "",
                passed=passed,
                hard_rejected=hard_rejected,
                hard_rejection_reason=reason,
                annotations=[{"gate": stage, "severity": "ok" if passed else "rej", "value": 0, "threshold": 0}],
                filter_metadata=meta,
                num_strategies_agree=(raw_signal.metadata or {}).get("num_agree", 1),
                regime=(raw_signal.metadata or {}).get("regime", "") or "",
            )
        except Exception:
            pass  # Never crash the bot on tracking errors

    def _process_symbol_llm_first(
        self,
        symbol: str,
        sym_cfg,
        signal_result,  # From ensemble.evaluate() (already passed quality gates)
        data: dict,
        open_pos: dict,
        current_price: float,
        trace_id: str = "",
    ):
        """LLM-first entry path: bypasses 47 mechanical gates.

        Flow:
          1. Get raw signal (with metadata, no quality filtering)
          2. SafetyFilterChain (5 hard gates only)
          3. LLM agent pipeline (quality + sizing + thesis)
          4. Post-LLM safety caps (notional, portfolio, OpsGuard)
          5. Live price + execution

        Falls through (returns) on any rejection. Raises on unexpected errors
        so the caller can fall back to the mechanical path.
        """
        # ── Step 1: Get raw signal for LLM ──
        # The signal_result from evaluate() already passed quality gates.
        # We want the RAW signal with metadata but no quality filtering.
        raw_signal = self.ensemble.evaluate_raw(symbol, data)
        if raw_signal is None:
            logger.info(
                f"[{trace_id}][{symbol}] LLM-FIRST: no raw signal "
                f"(no strategy consensus)"
            )
            return

        # ── Step 2: Safety gates only ──
        from core.signal_pipeline import SafetyFilterChain
        safety = SafetyFilterChain(
            self.risk_mgr, self.leverage_mgr, self.config
        )
        safety_result = safety.evaluate(
            signal=raw_signal,
            equity=self.risk_mgr.equity,
            current_open_count=len(open_pos),
            open_positions=open_pos,
        )
        if not safety_result.approved:
            logger.info(
                f"[{trace_id}][{symbol}] LLM-FIRST safety reject: "
                f"{safety_result.rejection_reason}"
            )
            return

        # ── Step 3: Build context and run LLM agent pipeline ──
        from llm.agents.coordinator import get_coordinator, is_multi_agent_enabled
        if not is_multi_agent_enabled():
            raise RuntimeError("LLM_FIRST_MODE requires LLM_MULTI_AGENT=true")

        coordinator = get_coordinator()

        # Build signal context (everything the LLM needs to decide)
        signal_ctx = {
            "symbol": raw_signal.symbol,
            "side": raw_signal.side,
            "confidence": raw_signal.confidence,
            "entry": raw_signal.entry,
            "sl": raw_signal.sl,
            "tp1": raw_signal.tp1,
            "tp2": raw_signal.tp2,
            "atr": raw_signal.atr,
            "strategy": raw_signal.strategy or "",
            "num_agree": (raw_signal.metadata or {}).get("num_agree", 1),
            "strategies_agree": (raw_signal.metadata or {}).get("strategies_agree", []),
            "chop_score": (raw_signal.metadata or {}).get("chop_score", 0),
            "chop_score_smoothed": (raw_signal.metadata or {}).get("chop_score_smoothed", 0),
            "win_prob": (raw_signal.metadata or {}).get("win_prob"),
            "ev_per_dollar": (raw_signal.metadata or {}).get("ev_per_dollar"),
            "rr_tp1": round(raw_signal.risk_reward_tp1, 2),
            "rr_tp2": round(raw_signal.risk_reward_tp2, 2),
            "stop_width_pct": round(raw_signal.stop_width_pct, 6),
            "quality_multiplier": (raw_signal.metadata or {}).get("quality_multiplier"),
            "regime_1h": (raw_signal.metadata or {}).get("regime_1h", "unknown"),
            "regime_4h": (raw_signal.metadata or {}).get("regime_4h", "unknown"),
            "regime_4h_aligned": (raw_signal.metadata or {}).get("regime_4h_aligned", True),
            "mechanical_confidence_floor": (raw_signal.metadata or {}).get("mechanical_confidence_floor"),
            "would_pass_confidence_floor": (raw_signal.metadata or {}).get("would_pass_confidence_floor"),
            "graduated_rules_advisory": (raw_signal.metadata or {}).get("graduated_rules_advisory"),
        }

        # Market context
        _now_utc = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        _lp = self._last_prices if hasattr(self, '_last_prices') else {}
        _pc1h = self._price_changes_1h if hasattr(self, '_price_changes_1h') else {}
        # 2026-06-06 CRITICAL FIX: _meta was being referenced at lines 7072-7075 (mark_price,
        # basis_pct, oi_history, open_interest) without being defined → every LLM-FIRST call
        # failed with NameError → bot was running on mechanical fallback for hours. Pull
        # _meta from data["_meta"] which is populated by handle_symbol's metadata injection.
        _meta = data.get("_meta", {}) if isinstance(data, dict) else {}
        market_ctx = {
            "funding_rate": self._last_funding_rates.get(symbol),
            "volume_ratio": (raw_signal.metadata or {}).get("volume_ratio", 1.0),
            "time_utc_hour": _now_utc.hour,
            "day_of_week": _now_utc.weekday(),  # 0=Mon … 6=Sun; weekends = low liquidity
            "btc_price": _lp.get("BTC", _lp.get("BTC/USDC:USDC", 0.0)),
            "btc_trend": _pc1h.get("BTC", 0.0),
            "eth_price": _lp.get("ETH", _lp.get("ETH/USDC:USDC", 0.0)),
            "eth_trend": _pc1h.get("ETH", 0.0),
            "sol_price": _lp.get("SOL", _lp.get("SOL/USDC:USDC", 0.0)),
            "sol_trend": _pc1h.get("SOL", 0.0),
            "signal_age": time.time() - (raw_signal.metadata or {}).get("generated_at", time.time()),
            "ohlcv_1h": data.get("1h"),
            "ohlcv_5m": data.get("5m"),
            "ohlcv_4h": data.get("4h"),
            "mark_price": _meta.get("mark_price"),
            "basis_pct": _meta.get("basis_pct"),
            "oi_history": _meta.get("oi_history"),
            "open_interest": _meta.get("open_interest"),
            # WAVE2B L1 (F4, FULL_PIPE_BUILD_MAP): mechanical would-have
            # verdicts collected by the demoted routers in handle_symbol
            # (R3 solo gate, R6 regime floor, R7 conf divert, R21d FIT).
            # Formatted for prompts by L4's format_mech_opinion — "all
            # mechanical analysis goes through Claude" as labeled context.
            "mech_opinion": (getattr(self, '_mech_opinion', {}) or {}).get(symbol),
        }

        # Portfolio context
        _equity = self.risk_mgr.equity
        # Include "symbol" key in each position dict so _parse() in
        # portfolio_intelligence.py can identify the symbol when iterating values.
        # Without it, _parse silently drops all positions → Risk Agent sees "0 positions"
        # → sizes freely → OpsGuard rejects at 500% notional cap.
        _open_positions_ctx = {
            s: {"symbol": s, "side": p.side, "entry": float(p.entry),
                "qty": float(p.qty), "leverage": getattr(p, "leverage", 1.0)}
            for s, p in open_pos.items()
        }
        # Pre-compute total notional deployed so Risk Agent has explicit budget info
        _total_notional = sum(
            v["entry"] * v["qty"] for v in _open_positions_ctx.values()
        )
        _notional_cap = _equity * 5.0  # OpsGuard MAX_SINGLE_POSITION_PCT = 5.0
        _remaining_notional = max(0.0, _notional_cap - _total_notional)
        portfolio_ctx = {
            "equity": _equity,
            "open_positions": _open_positions_ctx,
            "open_positions_count": len(open_pos),
            "daily_pnl": getattr(self.risk_mgr, 'daily_pnl', 0.0),
            "circuit_breaker_proximity": getattr(
                self.risk_mgr, 'circuit_breaker_proximity', 1.0
            ),
            "consecutive_losses": getattr(
                self.risk_mgr.circuit_breaker, 'consecutive_losses', 0
            ) if hasattr(self.risk_mgr, 'circuit_breaker') else 0,
            "total_notional": round(_total_notional, 2),
            "total_notional_pct": round(_total_notional / _equity * 100, 1) if _equity > 0 else 0.0,
            "notional_cap_pct": 500.0,
            "remaining_notional_pct": round(_remaining_notional / _equity * 100, 1) if _equity > 0 else 500.0,
        }

        # Enrich signal context with edge data and behavioral patterns
        # so the LLM can make truly informed decisions.
        #
        # Loads TWO kinds of edge data:
        #   1. Global backtest verdict (CONFIRMED_EDGE / MARGINAL / NEGATIVE_EV_BLOCKED)
        #   2. Regime-specific live performance (HYPE_BUY_illiquid = 9% WR TOXIC etc.)
        #
        # is_toxic=True when regime-specific WR < 10% with n >= 10. This flag is
        # read downstream by SafetyFilterChain and the agent snapshot.
        try:
            from llm.deep_memory import get_deep_memory
            _dm = get_deep_memory()
            _bt = _dm.strategy_fps.get_all().get("_quant_backtest_2026_03_26", {})
            _base_sym = symbol.replace('/USDC:USDC','').replace('/USDT:USDT','')
            _side = 'BUY' if raw_signal.side == 'BUY' else 'SELL'
            _setup_key = f"{_base_sym}_{_side}"
            _setup = _bt.get(_setup_key, {})

            # Regime-specific live performance (from ensemble.by_symbol_regime bucket)
            _regime = (raw_signal.metadata or {}).get("regime_1h") or \
                      (raw_signal.metadata or {}).get("regime", "unknown")
            _regime_bucket = _dm.strategy_fps.get_all().get("ensemble", {}).get(
                "by_symbol_regime", {}
            )
            _regime_key = f"{_base_sym}_{_side}_{_regime}"
            _regime_setup = _regime_bucket.get(_regime_key, {}) if isinstance(_regime_bucket, dict) else {}
            _reg_n = int(_regime_setup.get("total", 0) or 0)
            _reg_wins = int(_regime_setup.get("wins", 0) or 0)
            _reg_wr = (_reg_wins / _reg_n * 100.0) if _reg_n > 0 else None
            # TOXIC threshold: WR < 10% AND n >= 20 (n=10 was too small; 20 gives ~2.5 SE).
            # n=10 caused SOL SHORT to be hard-blocked with insufficient data.
            _is_toxic = bool(_reg_wr is not None and _reg_wr < 10.0 and _reg_n >= 20)

            if _setup and _setup.get("total", 0) > 0:
                signal_ctx["edge_data"] = {
                    "setup_key": _setup_key,
                    "wr": _setup.get("wr", 0),
                    "pf": _setup.get("pf", 0),
                    "n": _setup.get("total", 0),
                    "verdict": _setup.get("verdict", ""),
                    "best_hours": _setup.get("best_hours_utc", ""),
                    # Regime-specific live verdict
                    "regime": _regime,
                    "regime_wr": _reg_wr,
                    "regime_n": _reg_n,
                    "is_toxic": _is_toxic,
                }
            # TOXIC gate — SHADOW MODE (FALLACY_AUDIT D17, 2026-07-02).
            # History: this hard-block was DEAD since birth — the writer keyed
            # by_symbol_regime as "{symbol}_{regime}" while this reader looked
            # up "{base}_{side}_{regime}", so _is_toxic could never be True.
            # The key mismatch is now fixed in deep_memory (side-canonical keys
            # with provenance), which would have silently ARMED a learned rule
            # that never passed THE_STANDARD 2b dollar re-validation. Per the
            # audit verdict, enforcement stays OFF (owner-gated): we log the
            # would-have-blocked counterfactual and let the signal flow to the
            # LLM, which still sees is_toxic in edge_data.
            if _is_toxic:
                _toxic_reason = f"TOXIC_SETUP_SHADOW: {_regime_key} WR={_reg_wr:.1f}% n={_reg_n}"
                logger.warning(
                    f"[{trace_id}][{symbol}] TOXIC SHADOW (would have blocked, not enforcing): "
                    f"{_regime_key} WR={_reg_wr:.1f}% n={_reg_n}"
                )
                # Record counterfactual so the shadow verdict is dollar-scored
                if self.counterfactual:
                    try:
                        self.counterfactual.record_veto(
                            symbol=symbol,
                            side=raw_signal.side,
                            entry_price=raw_signal.entry,
                            sl_price=raw_signal.sl,
                            tp1_price=raw_signal.tp1,
                            tp2_price=raw_signal.tp2,
                            confidence=raw_signal.confidence,
                            reason=_toxic_reason,
                        )
                    except Exception:
                        pass
                # NO return — signal continues to the LLM (shadow, not enforcement)
        except Exception as e:
            logger.debug(f"[{trace_id}][{symbol}] edge_data load failed: {e}")

        # Add reflection/exhaustion context if available
        try:
            if hasattr(self, '_reflection_engine') and self._reflection_engine:
                _refl = self._reflection_engine.get_entry_context(
                    symbol, raw_signal.side, current_price
                ) if hasattr(self._reflection_engine, 'get_entry_context') else None
                if _refl:
                    signal_ctx["reflection"] = _refl
        except Exception:
            pass

        # Get model routing from trigger system
        model_for_trigger = None
        try:
            from llm.usage_tiers import get_model_for_trigger
            model_for_trigger = get_model_for_trigger("PRE_TRADE")
        except Exception:
            pass

        from llm.decision_types import EntryDecision
        try:
            entry_decision = coordinator.get_entry_decision(
                signal_context=signal_ctx,
                market_context=market_ctx,
                portfolio_context=portfolio_ctx,
                model_for_trigger=model_for_trigger,
            )
        except Exception as e:
            logger.error(
                f"[{trace_id}][{symbol}] LLM-FIRST coordinator error: {e}"
            )
            raise  # Let caller fall back to mechanical path

        _thesis = (entry_decision.thesis or "")[:100]
        # Truthful labeling (audit #40/#7): distinguish pipeline failures and
        # exploration overrides from genuine LLM judgments in all records.
        _pipeline_failed = "pipeline failure" in (entry_decision.thesis or "").lower()
        _exploration_entry = False
        if entry_decision.action == "skip" and _pipeline_failed:
            # Owner call 2026-07-02 (live HYPE-SHORT example; audit #7 conversion half):
            # a pipeline FAILURE is not a considered skip — the brain rendered no
            # opinion, so there is nothing to explore against. Converting malfunctions
            # into coin-flip entries is noise, not edge data. Stand down; the signal
            # still reaches the pipeline_error logging path below.
            logger.info(f"[{trace_id}][{symbol}] exploration BLOCKED: pipeline failure is not an explorable skip")
        elif entry_decision.action == "skip":
            # ── EXPLORATION MODE (Nunu-authorized 2026-06-20) ──────────────────
            # Break the entry paralysis to GATHER EDGE DATA: convert a throttled
            # fraction of LLM skips into REDUCED-SIZE exploratory entries. SAFE by
            # construction: poison patterns (hype_long/sol_long graduated vetoes) are
            # hard-blocked UPSTREAM in the signal pipeline so they cannot reach here;
            # the circuit breaker is checked explicitly below; duplicate-block,
            # 15x notional cap, portfolio cap, OpsGuard & slippage all still apply on
            # the open path. Naturally throttled by MAX_OPEN_POSITIONS + the 2h min
            # hold. Fully reversible: EXPLORATION_MODE=false. Tunables:
            # EXPLORATION_EPSILON, EXPLORATION_RISK_PCT, EXPLORATION_MAX_LEV.
            _explored = False
            try:
                import random as _rnd
                _cb = getattr(self.risk_mgr, "circuit_breaker", None)
                # Belt-and-suspenders (2026-06-20): NEVER explore known-poison combos even
                # if their graduated veto is CONDITIONAL and didn't fire for this signal.
                # (Caught a SOL_LONG exploration entry — sol_long_veto is regime/strategy
                # conditional, not a blanket symbol+side block, so some slip through to the LLM
                # path.) This guarantees exploration can't open HYPE_LONG/SOL_LONG regardless.
                _ex_side = "LONG" if raw_signal.side in ("BUY", "LONG") else "SHORT"
                _ex_combo = f"{symbol}_{_ex_side}"
                _ex_blocked = os.getenv("EXPLORATION_BLOCK_COMBOS", "HYPE_LONG,SOL_LONG").replace(" ", "").split(",")
                # ── CONVICTION-AWARE EXPLORATION GATE (2026-06-25) ─────────────
                # Exploration must add EDGE-DATA value, not bleed money. It only
                # adds value on GENUINELY-UNCERTAIN skips (coin-flips where we
                # truly don't know the edge). On HIGH-CONVICTION -EV skips ("0% WR",
                # "lacks credible edge", "likely chops") the LLM is right and
                # forcing a trade just loses money (overnight BTC/ETH/HYPE LONG:
                # 3/3 stopped out -$64). We gate on STRUCTURED signals only — the
                # SKIP's consensus conviction (entry_decision.confidence) and the
                # quant win_prob / regime-cell guards — NEVER text-parsing.
                # Behind EXPLORATION_RESPECT_CONVICTION (default true); flag OFF
                # reproduces prior behavior exactly. This makes the name-based
                # EXPLORATION_BLOCK_COMBOS list unnecessary (the -EV guard catches
                # every symbol/side universally), but it is left in place as
                # belt-and-suspenders.
                _skip_conf = float(getattr(entry_decision, "confidence", 0.0) or 0.0)
                _conv_thresh = float(os.getenv("EXPLORATION_CONVICTION_MAX", "0.65"))
                _wp = (raw_signal.metadata or {}).get("win_prob")
                # UNIFIED TOXIC (2026-06-25 swarm #4): pass the SAME {sym}_{side}
                # verdict the LLM/counterfactual veto consults (n>=13-capable),
                # not only the regime cell (n>=20). `_setup` is the symbol-side
                # backtest verdict already pulled at :7413. Also pass an RR/EV
                # arm so the regime-keyed win_prob is the PRIMARY admit signal.
                _setup_verdict = (_setup or {}).get("verdict")
                _setup_pf = (_setup or {}).get("pf")
                _setup_wr = (_setup or {}).get("wr")
                _setup_n = (_setup or {}).get("total")
                _rr_tp1 = None
                try:
                    _sw = abs(raw_signal.entry - raw_signal.sl)
                    if _sw > 0:
                        _rr_tp1 = abs(raw_signal.tp1 - raw_signal.entry) / _sw
                except (TypeError, ValueError, AttributeError):
                    _rr_tp1 = None
                _conviction_ok, _uncertain, _neg_ev = exploration_conviction_ok(
                    skip_conf=_skip_conf,
                    win_prob=_wp,
                    is_toxic=_is_toxic,
                    reg_wr=_reg_wr,
                    reg_n=_reg_n,
                    conv_thresh=_conv_thresh,
                    rr_tp1=_rr_tp1,
                    setup_verdict=_setup_verdict,
                    setup_pf=_setup_pf,
                    setup_wr=_setup_wr,
                    setup_n=_setup_n,
                )
                try:
                    _wp = float(_wp) if _wp is not None else None
                except (TypeError, ValueError):
                    _wp = None
                if (os.getenv("EXPLORATION_MODE", "false").lower() in ("1", "true", "yes")
                        and _ex_combo not in _ex_blocked
                        and not getattr(_cb, "tripped", False)
                        and _conviction_ok
                        and _rnd.random() < float(os.getenv("EXPLORATION_EPSILON", "0.40"))):
                    _stop_w = abs(raw_signal.entry - raw_signal.sl)
                    if _stop_w > 0 and raw_signal.entry > 0:
                        _ex_lev = max(1.0, min(getattr(entry_decision, "leverage", 1.0) or 1.0,
                                               float(os.getenv("EXPLORATION_MAX_LEV", "2.0"))))
                        # D4 (owner-approved 2026-07-02, audit #6): route exploration
                        # sizing through risk_mgr.calculate_qty so EVERY sizing guard
                        # applies — fee-padded effective stop, min-stop-width reject,
                        # notional cap, per-symbol risk multiplier. The old raw
                        # `risk/(stop*lev)` formula sized inverse-to-stop-width with
                        # no floor (the $628-notional tight-stop XRP LONG class).
                        # D1 (owner-approved 2026-07-02): EXPLORATION_RISK_MULT
                        # (default 0.1) scales exploration risk to ~0.1x on top of
                        # EXPLORATION_RISK_PCT — exploration keeps buying edge data
                        # but at a tenth of the noise cost.
                        _ex_risk_mult = float(os.getenv("EXPLORATION_RISK_MULT", "0.1"))
                        _ex_risk_pct = (float(os.getenv("EXPLORATION_RISK_PCT", "0.004"))
                                        * _ex_risk_mult)
                        _risk_usd = (self.risk_mgr.equity or 0.0) * _ex_risk_pct
                        from trading_config import get_symbol_risk_mult as _gsrm
                        _ex_qty = self.risk_mgr.calculate_qty(
                            entry=raw_signal.entry,
                            stop_loss=raw_signal.sl,
                            leverage=_ex_lev,
                            risk_multiplier=_gsrm(symbol),
                            symbol=symbol,
                            risk_per_trade_override=_ex_risk_pct,
                        )
                        if _ex_qty > 0:
                            entry_decision.action = "go"
                            entry_decision.position_qty = _ex_qty
                            entry_decision.leverage = _ex_lev
                            if hasattr(entry_decision, "size_multiplier"):
                                entry_decision.size_multiplier = 0.25
                            _rf = list(getattr(entry_decision, "risk_flags", None) or [])
                            _rf.append("EXPLORATION")
                            entry_decision.risk_flags = _rf
                            _explored = True
                            _exploration_entry = True
                            logger.info(
                                f"[{trace_id}][{symbol}] EXPLORATION ENTRY: skip→go "
                                f"qty={_ex_qty:.6f} lev={_ex_lev:.1f}x risk=${_risk_usd:.2f} "
                                f"(gathering edge data; LLM had skipped: {_thesis})"
                            )
                # Visibility: log when the conviction gate DECLINED to convert a
                # skip that exploration would otherwise have sampled. Only logs
                # when exploration is enabled and the combo/circuit-breaker checks
                # passed, so this directly surfaces the gate stopping the -EV bleed.
                elif (os.getenv("EXPLORATION_MODE", "false").lower() in ("1", "true", "yes")
                        and _ex_combo not in _ex_blocked
                        and not getattr(_cb, "tripped", False)
                        and not _conviction_ok):
                    # Under unified mode the admit decision rests on the -EV/
                    # toxic guard (skip_conf is non-binding), so a decline here
                    # means the cell is -EV or toxic (the bleed we are stopping).
                    _unified_on = os.getenv("EXPLORATION_UNIFIED_TOXIC", "true").lower() in ("1", "true", "yes")
                    if _unified_on:
                        _decline_reason = "clearly -EV/toxic setup"
                    else:
                        _decline_reason = (
                            "high-conviction skip" if not _uncertain
                            else "clearly -EV setup"
                        )
                    logger.info(
                        f"[{trace_id}][{symbol}] EXPLORATION DECLINED: respecting "
                        f"{_decline_reason} (skip_conf={_skip_conf:.2f} "
                        f"thresh={_conv_thresh:.2f} win_prob="
                        f"{('%.2f' % _wp) if _wp is not None else 'NA'} "
                        f"toxic={_is_toxic} reg_wr={_reg_wr} reg_n={_reg_n} "
                        f"setup_verdict={_setup_verdict} setup_pf={_setup_pf} "
                        f"setup_n={_setup_n} rr_tp1="
                        f"{('%.2f' % _rr_tp1) if _rr_tp1 is not None else 'NA'}); "
                        f"keeping LLM skip: {_thesis}"
                    )
            except Exception as _ex_e:
                logger.debug(f"[{trace_id}][{symbol}] exploration convert error: {_ex_e}")
                _explored = False

            if not _explored:
                logger.info(
                    f"[{trace_id}][{symbol}] LLM-FIRST SKIP: {_thesis}"
                )
                # Record for counterfactual tracking.
                # Audit #7 (labeling half): pipeline failures are NOT LLM vetoes —
                # recording them as vetoes polluted veto-accuracy learning
                # (167 'LLM pipeline failure' records in scenarios.json).
                if self.counterfactual and not _pipeline_failed:
                    try:
                        self.counterfactual.record_veto(
                            symbol=symbol,
                            side=raw_signal.side,
                            entry_price=raw_signal.entry,
                            sl_price=raw_signal.sl,
                            tp1_price=raw_signal.tp1,
                            tp2_price=raw_signal.tp2,
                            confidence=raw_signal.confidence,
                            reason=f"LLM_FIRST: {_thesis}",
                        )
                    except Exception:
                        pass
                # Track rejection in signal_outcomes.jsonl
                # (pipeline errors get their own stage so llm_skip stats stay clean)
                self._track_llm_first_outcome(
                    raw_signal, symbol,
                    passed=False, hard_rejected=False,
                    reason=(f"LLM pipeline error: {_thesis}" if _pipeline_failed
                            else f"LLM veto: {_thesis}"),
                    stage="pipeline_error" if _pipeline_failed else "llm_skip",
                    metadata={
                        "llm_confidence": entry_decision.confidence,
                        "llm_regime": entry_decision.regime,
                        "thesis": _thesis,
                    },
                )
                return
            # else: fall through to the open path (all downstream safety gates apply)

        # ── Step 4: Post-LLM safety caps ──
        leverage = max(1.0, min(
            entry_decision.leverage,
            getattr(self.config, 'max_leverage', 25.0),
        ))
        qty = entry_decision.position_qty
        side = raw_signal.side

        # Validate qty before proceeding
        if qty <= 0 or raw_signal.entry <= 0:
            logger.warning(
                f"[{trace_id}][{symbol}] LLM-FIRST bad qty/entry: "
                f"qty={qty}, entry={raw_signal.entry}"
            )
            return

        # Hard notional cap (15x equity)
        _MAX_NOTIONAL_MULT = 15.0
        _equity = self.risk_mgr.equity or 1.0
        _notional = qty * raw_signal.entry * leverage
        if _notional > _MAX_NOTIONAL_MULT * _equity:
            _capped = _MAX_NOTIONAL_MULT * _equity
            qty = _capped / (raw_signal.entry * leverage)
            logger.warning(
                f"[{trace_id}][{symbol}] LLM-FIRST notional cap: "
                f"${_notional:.0f} → ${_capped:.0f}"
            )

        # Portfolio notional cap
        _new_notional = qty * raw_signal.entry * leverage
        if not self.pos_mgr.check_portfolio_notional_cap(
            new_notional=_new_notional,
            equity=_equity,
            max_portfolio_leverage=self.config.max_portfolio_leverage,
        ):
            logger.info(
                f"[{trace_id}][{symbol}] LLM-FIRST portfolio cap reject"
            )
            return

        # OpsGuard
        total_exposure = sum(
            p.qty * p.entry * p.leverage
            for p in self.pos_mgr.get_open_positions().values()
        )
        ops_check = self.ops_guard.can_execute(
            position_size_usd=_new_notional,
            equity=_equity,
            total_exposure_usd=total_exposure,
        )
        if not ops_check["allowed"]:
            logger.warning(
                f"[{trace_id}][{symbol}] LLM-FIRST OpsGuard: "
                f"{ops_check['reason']}"
            )
            return

        # Min qty / min notional floor
        _MIN_NOTIONAL = 10.0
        if qty * raw_signal.entry < _MIN_NOTIONAL * 1.15:
            qty = max(qty, (_MIN_NOTIONAL * 1.15) / raw_signal.entry)
        _min_q = get_min_qty(symbol)
        if qty < _min_q:
            qty = _min_q

        # ── Step 5: Live price refresh + execution ──
        snapshot_entry = raw_signal.entry
        live_entry = self.fetcher.fetch_live_price(symbol)
        if live_entry is None:
            live_entry = current_price

        slippage_pct = abs(live_entry - snapshot_entry) / snapshot_entry * 100 if snapshot_entry > 0 else 0
        max_slippage = float(os.getenv("MAX_ENTRY_SLIPPAGE_PCT", "1.5"))
        if slippage_pct > max_slippage:
            logger.warning(
                f"[{trace_id}][{symbol}] LLM-FIRST slippage reject: "
                f"{slippage_pct:.2f}% > {max_slippage}%"
            )
            return

        actual_entry = live_entry
        entry_shift = actual_entry - snapshot_entry
        adj_sl = raw_signal.sl + entry_shift
        adj_tp1 = raw_signal.tp1 + entry_shift
        adj_tp2 = raw_signal.tp2 + entry_shift

        # Safety: ensure SL/TP are on the correct side
        if side == "BUY" or side == "LONG":
            side = "LONG"
            if adj_sl >= actual_entry:
                adj_sl = actual_entry - raw_signal.atr * 1.5 if raw_signal.atr > 0 else actual_entry * 0.98
            if adj_tp1 <= actual_entry:
                adj_tp1 = actual_entry + raw_signal.atr * 1.0 if raw_signal.atr > 0 else actual_entry * 1.01
        else:
            side = "SHORT"
            if adj_sl <= actual_entry:
                adj_sl = actual_entry + raw_signal.atr * 1.5 if raw_signal.atr > 0 else actual_entry * 1.02
            if adj_tp1 >= actual_entry:
                adj_tp1 = actual_entry - raw_signal.atr * 1.0 if raw_signal.atr > 0 else actual_entry * 0.99

        # Round qty for exchange
        qty = round_qty(symbol, qty)

        # Final qty check
        if qty <= 0:
            logger.warning(
                f"[{trace_id}][{symbol}] LLM-FIRST qty=0 after rounding"
            )
            return

        _thesis = (entry_decision.thesis or "")
        _sizing = (entry_decision.sizing_rationale or "")
        _debate = (entry_decision.debate_summary or "")
        _regime = (entry_decision.regime or "unknown")

        logger.info(
            f"[{trace_id}][{symbol}] LLM-FIRST TRADE: {side} "
            f"qty={qty:.6f} @ ${actual_entry:.4f} "
            f"lev={leverage:.1f}x SL=${adj_sl:.4f} TP1=${adj_tp1:.4f} "
            f"regime={_regime} thesis={_thesis[:60]}"
        )

        # Track successful LLM-first execution.
        # Audit #40: exploration overrides were logged as "LLM approved" — the
        # LLM actually said SKIP (conf may legitimately be 0.0 on pipeline
        # failure; record it as returned, never fabricate).
        _entry_label = "EXPLORATION" if _exploration_entry else "LLM_APPROVED"
        self._track_llm_first_outcome(
            raw_signal, symbol,
            passed=True, hard_rejected=False,
            reason=("exploration override (LLM skipped)" if _exploration_entry
                    else "LLM approved"),
            stage="llm_execute",
            metadata={
                "llm_confidence": entry_decision.confidence,
                "llm_regime": _regime,
                "leverage": leverage,
                "qty": qty,
                "entry": actual_entry,
                "thesis": _thesis[:100],
                "entry_type": _entry_label,
                "llm_action": "exploration_override" if _exploration_entry else "go",
                "pipeline_failed": _pipeline_failed,
            },
        )

        # Build entry reasons for position manager
        entry_reasons = {
            "llm_first": True,
            "confidence": raw_signal.confidence,
            "strategies_agree": signal_ctx.get("strategies_agree", []),
            "num_agree": signal_ctx.get("num_agree", 1),
            "regime": _regime,
            "thesis": _thesis[:200],
            "sizing_rationale": _sizing[:200],
            "risk_flags": entry_decision.risk_flags or [],
            "debate_summary": _debate[:200],
            "llm_confidence": entry_decision.confidence,
            "llm_action": "exploration_override" if _exploration_entry else "go",
            "llm_agreed": not _exploration_entry,
            "entry_type": _entry_label,
            "pipeline_failed": _pipeline_failed,
            "agent_confidences": getattr(entry_decision, "agent_confidences", {}) or {},
            # Carries "thesis_id=..." so the close path can grade the thesis
            # (2026-07-01: LLM-first entries never wrote llm_notes — theses
            # were structurally ungradeable on the live path).
            "llm_notes": (entry_decision.notes or "")[:500],
        }

        # ── Execute trade ──
        # 2026-06-01 fix: TradeProfile requires entry_reasons/confidence/volatility_band/
        # timeframe_bias (all positional, no defaults). Previous code only passed 3 of 7
        # required args, crashing every LLM-first GO at trade entry and falling back to
        # mechanical path. The first ETH GO at 14:48 UTC hit this. Now populated from
        # raw_signal + entry_decision context.
        from execution.position_manager import TradeProfile
        _vol_band = "high" if _regime == "high_volatility" else ("low" if _regime == "consolidation" else "medium")
        _tf_bias = "short" if _regime in ("range", "consolidation") else "medium"
        _strategies = (raw_signal.metadata or {}).get("strategies_agree", [raw_signal.strategy or "ensemble"])
        if isinstance(_strategies, str):
            _strategies = [_strategies]
        trade_prof = TradeProfile(
            # EXPLORATION label is truthful metadata only — neither value matches
            # the SCALP/TREND exit-behavior branches, so exit geometry unchanged.
            entry_type=_entry_label if _exploration_entry else "LLM_FIRST",
            entry_reasons=list(_strategies),
            primary_driver=raw_signal.strategy or "ensemble",
            confidence=float(entry_decision.confidence * 100.0),  # scale 0-1 -> 0-100
            regime=_regime,
            volatility_band=_vol_band,
            timeframe_bias=_tf_bias,
        )

        # Build fake LeverageDecision for compatibility
        from execution.leverage import LeverageDecision
        lev_decision = LeverageDecision(
            leverage=leverage,
            mode="llm_first",
            tier="llm",
            reason=f"LLM Risk Agent: {_sizing[:80]}",
            risk_multiplier=entry_decision.size_multiplier or 1.0,
        )

        # OpsGuard duplicate check
        _dup_check = self.ops_guard.check_duplicate_position(
            symbol=symbol,
            side=side,
            open_positions=self.pos_mgr.get_open_positions(),
        )
        if not _dup_check["allowed"]:
            logger.warning(
                f"[{trace_id}][{symbol}] LLM-FIRST duplicate blocked"
            )
            return

        # ── Submit order to exchange (D5, owner-approved 2026-07-02, audit #1) ──
        # The LLM-first path previously registered the position ONLY in the
        # PositionManager — no exchange order was ever submitted (paper mode
        # masked it; one env flip from live and every entry would be a phantom
        # whose reduce-only close is rejected forever). Mirror the mechanical
        # path: submit first, bail cleanly if not filled, reconcile the
        # position to the actual fill price/qty.
        # NOTE: order_executor expects BUY/SELL (side.upper()=="BUY" else sell);
        # `side` here is already normalized to LONG/SHORT, so map it back.
        _order_side = "BUY" if side == "LONG" else "SELL"
        order_result = self.order_executor.open_position(
            symbol=symbol,
            side=_order_side,
            qty=qty,
            price=actual_entry,
            leverage=int(leverage),
            order_type="market",
        )
        if not order_result.filled:
            logger.warning(
                f"[{trace_id}][{symbol}] LLM-FIRST order FAILED: {order_result.error}"
            )
            return

        # Record trade for rate limiting AFTER order confirms (mirrors mechanical)
        Telemetry.inc("total_trades")
        self.ops_guard.record_trade()

        # Use actual fill price and qty from exchange
        actual_entry = order_result.fill_price if order_result.fill_price > 0 else actual_entry
        qty = order_result.fill_qty if order_result.fill_qty > 0 else qty

        # Record fill quality for execution analytics (mirrors mechanical path)
        if hasattr(self, 'execution_analytics') and self.execution_analytics:
            try:
                self.execution_analytics.record_fill(
                    trade_id=f"{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side=side,
                    expected_price=snapshot_entry,
                    actual_fill=order_result.fill_price if order_result.fill_price > 0 else actual_entry,
                    notional=qty * actual_entry,
                    regime=self._tick_regime_cache.get(symbol, "unknown"),
                    signal_time=entry_reasons.get("signal_time", time.time()),
                )
            except Exception as e:
                logger.debug(f"Execution analytics record error: {e}")

        self.pos_mgr.open_position(
            symbol=symbol,
            side=side,
            entry=actual_entry,
            sl=adj_sl,
            tp1=adj_tp1,
            tp2=adj_tp2,
            qty=qty,
            leverage=leverage,
            atr=raw_signal.atr,
            # ROOT FIX (rank-2): the LLM-FIRST path never passed confidence=, so pos.confidence
            # defaulted to 0.0 for every LLM-first trade (81/85 zeroed rows in trades.csv). The
            # value lived only in entry_reasons['confidence']. Setting it here at entry makes the
            # ~10 downstream pos.confidence readers (close-log, feedback, analytics) all correct.
            confidence=raw_signal.confidence,
            entry_reasons=entry_reasons,
            trade_profile=trade_prof,
            notes=(f"EXPLORATION (LLM skipped): {_thesis[:200]}" if _exploration_entry
                   else f"LLM-FIRST: {_thesis[:200]}"),
        )

        # Log trade
        log_trade(
            symbol=symbol,
            action="OPEN",
            side=side,
            price=actual_entry,
            qty=qty,
            leverage=leverage,
            strategy=raw_signal.strategy,
            metadata={
                "confidence": raw_signal.confidence,
                "llm_first": True,
                "llm_regime": _regime,
                "llm_thesis": _thesis[:100],
                "llm_leverage": leverage,
                "llm_risk_pct": entry_decision.risk_pct,
                "entry_type": _entry_label,
                "llm_action": "exploration_override" if _exploration_entry else "go",
            }
        )

        # Send alert
        self.alerts.send_market_update(
            f"*LLM-FIRST TRADE*\n"
            f"{symbol} {side} @ ${actual_entry:.4f}\n"
            f"Confidence: {raw_signal.confidence:.0f}%\n"
            f"Leverage: {leverage:.1f}x | Qty: {qty:.6f}\n"
            f"R:R: {signal_ctx.get('rr_tp1', 0):.2f}\n"
            f"Regime: {_regime}\n"
            f"Thesis: {_thesis[:100]}"
        )

    def _log_dual_track_llm_decision(
        self,
        symbol: str,
        raw_signal,
        data: dict,
        open_pos: dict,
        current_price: float,
        trace_id: str = "",
    ):
        """Dual-track: log what LLM-first would have decided (no execution).

        Used for validation before switching to LLM-first mode.
        Runs the full LLM pipeline and logs divergence from mechanical path.
        """
        try:
            from core.signal_pipeline import SafetyFilterChain
            safety = SafetyFilterChain(
                self.risk_mgr, self.leverage_mgr, self.config
            )
            safety_result = safety.evaluate(
                signal=raw_signal,
                equity=self.risk_mgr.equity,
                current_open_count=len(open_pos),
                open_positions=open_pos,
            )
            if not safety_result.approved:
                logger.info(
                    f"[DUAL-TRACK][{symbol}] LLM-first would reject: "
                    f"safety={safety_result.rejection_reason}"
                )
                return

            from llm.agents.coordinator import get_coordinator, is_multi_agent_enabled
            if not is_multi_agent_enabled():
                return

            coordinator = get_coordinator()

            signal_ctx = {
                "symbol": raw_signal.symbol,
                "side": raw_signal.side,
                "confidence": raw_signal.confidence,
                "entry": raw_signal.entry,
                "sl": raw_signal.sl,
                "tp1": raw_signal.tp1,
                "tp2": raw_signal.tp2,
                "atr": raw_signal.atr,
                "strategy": raw_signal.strategy or "",
            }
            market_ctx = {
                "ohlcv_1h": data.get("1h"),
                "ohlcv_5m": data.get("5m"),
                "ohlcv_4h": data.get("4h"),
            }
            portfolio_ctx = {
                "equity": self.risk_mgr.equity,
                "open_positions": {
                    s: {"symbol": s, "side": p.side, "entry": float(p.entry),
                        "qty": float(getattr(p, "qty", 0)),
                        "leverage": getattr(p, "leverage", 1.0)}
                    for s, p in open_pos.items()
                },
                "open_positions_count": len(open_pos),
            }

            entry_decision = coordinator.get_entry_decision(
                signal_context=signal_ctx,
                market_context=market_ctx,
                portfolio_context=portfolio_ctx,
            )

            logger.info(
                f"[DUAL-TRACK][{symbol}] LLM-first would: "
                f"{entry_decision.action} lev={entry_decision.leverage:.1f}x "
                f"conf={entry_decision.confidence:.2f} "
                f"thesis={entry_decision.thesis[:60]}"
            )
        except Exception as e:
            logger.debug(f"[DUAL-TRACK][{symbol}] Error: {e}")


















    def _build_morning_briefing_message(self) -> str:
        """Build the morning briefing message for 08:00 UTC auto-send.

        Phone-first, scannable, multi-window PnL so the user sees the
        true state without needing to type /briefing. 2026-04-16 addition.
        """
        from datetime import datetime, timezone, timedelta
        import csv

        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_7d = now - timedelta(days=7)
        today_str = now.strftime("%Y-%m-%d")

        trades_today = []
        trades_24h = []
        trades_7d = []

        try:
            with open("data/trades.csv", "r", encoding="utf-8") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:
                    try:
                        ts = row[0]
                        pnl = float(row[10])
                        tdt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if tdt.tzinfo is None:
                            tdt = tdt.replace(tzinfo=timezone.utc)
                        if ts.startswith(today_str):
                            trades_today.append(pnl)
                        if tdt >= cutoff_24h:
                            trades_24h.append(pnl)
                        if tdt >= cutoff_7d:
                            trades_7d.append(pnl)
                    except Exception:
                        continue
        except Exception:
            pass

        def _winline(label, trades):
            if not trades:
                return f"  *{label}*: no trades"
            n = len(trades)
            wins = sum(1 for p in trades if p > 0)
            net = sum(trades)
            emoji = "📈" if net > 0 else ("📉" if net < 0 else "➡️")
            return f"  {emoji} *{label}*: {n} trades, {wins}W/{n-wins}L, net ${net:+.2f}"

        lines = [
            "☀️ *Good morning — WAGMI Briefing*",
            f"_{now.strftime('%Y-%m-%d %H:%M UTC')}_",
            "",
            f"💰 *Equity*: ${self.risk_mgr.equity:,.2f}",
            _winline("Today (since UTC 00:00)", trades_today),
            _winline("Last 24h rolling", trades_24h),
            _winline("Last 7d rolling", trades_7d),
            "",
        ]

        # Open positions
        try:
            open_pos = [p for p in self.pos_mgr.positions.values() if p.qty > 0]
            lines.append(f"💼 *Open*: {len(open_pos)} position{'s' if len(open_pos) != 1 else ''}")
            for p in open_pos[:5]:
                side_emoji = "🟢" if p.side == "LONG" else "🔴"
                lines.append(f"  {side_emoji} {p.symbol} {p.side} @ ${p.entry:.4g} ({p.state})")
            if not open_pos:
                lines.append("  (flat — scanning)")
        except Exception:
            pass

        # Recent WATCH alerts
        try:
            from alerts.premium_filter import _last_watch_alert, _WATCH_ALERT_COOLDOWN_S
            import time as _t
            now_ts = _t.time()
            recent = [k for k, t in _last_watch_alert.items() if (now_ts - t) < _WATCH_ALERT_COOLDOWN_S]
            if recent:
                lines.append("")
                lines.append(f"🔔 *Watching* ({len(recent)} setups forming)")
        except Exception:
            pass

        lines.append("")
        lines.append("*Quick actions:*")
        lines.append("/briefing • /positions • /watch • /edges • /ask <q>")
        return "\n".join(lines)

    def _send_daily_summary(self):
        """Send daily summary via Telegram alert bridge."""
        try:
            from alerts.telegram_alert_bridge import get_telegram_alert_bridge
            bridge = get_telegram_alert_bridge()
            if not bridge.enabled:
                return

            # Gather today's stats
            total_trades = 0
            wins = 0
            net_pnl = 0.0
            best_trade = None
            worst_trade = None

            for event in self.pos_mgr.trade_log:
                if hasattr(event, 'pnl'):
                    total_trades += 1
                    net_pnl += event.pnl
                    if event.pnl > 0:
                        wins += 1
                    if best_trade is None or event.pnl > best_trade.get("pnl", 0):
                        best_trade = {"symbol": event.symbol, "pnl": event.pnl}
                    if worst_trade is None or event.pnl < worst_trade.get("pnl", 0):
                        worst_trade = {"symbol": event.symbol, "pnl": event.pnl}

            # LLM cost info
            llm_cost = 0.0
            llm_budget = float(os.environ.get("LLM_DAILY_BUDGET_USD", "5"))
            try:
                from llm.cost_tracker import get_cost_tracker
                ct = get_cost_tracker()
                llm_cost = ct.get_budget_used_pct() * llm_budget
            except Exception:
                pass

            bridge.send_daily_summary(
                total_trades=total_trades,
                wins=wins,
                net_pnl=net_pnl,
                best_trade=best_trade,
                worst_trade=worst_trade,
                active_positions=self.pos_mgr.get_open_count(),
                equity=self.risk_mgr.equity,
                llm_cost=llm_cost,
                llm_budget=llm_budget,
            )
            logger.info(f"[DAILY-SUMMARY] Sent: {total_trades} trades, ${net_pnl:+.2f} PnL")
        except Exception as e:
            logger.debug(f"Daily summary send error: {e}")


def main():
    # Load .env: bot/.env first (specific config), then root .env (fallback)
    # load_dotenv does NOT override existing vars, so first-loaded wins
    try:
        from pathlib import Path
        from dotenv import load_dotenv
        local_env = Path(__file__).parent / ".env"
        root_env = Path(__file__).parent.parent / ".env"
        if local_env.exists():
            load_dotenv(local_env)
        if root_env.exists():
            load_dotenv(root_env)
        if not local_env.exists() and not root_env.exists():
            load_dotenv()
    except ImportError:
        pass

    os.makedirs("ml_data", exist_ok=True)
    init_db()  # Initialize SQLite database for trade journal

    config = TradingConfig()
    bot = MultiStrategyBot(config)
    bot.run()


if __name__ == "__main__":
    main()
