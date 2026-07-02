"""
Multi-strategy ensemble / voting system with quality gates.
Combines signals from all 4 strategies into consensus decisions.

Quality gates (applied to ALL modes):
1. Volume chop filter: skip if volume < 50% of 20-bar avg
2. Require 2+ strategies agreeing on same direction (weighted_veto)
3. Minimum 65% confidence after merge
4. Multi-TF trend consensus (5m+1h+6h+daily): aligned +3..+8, counter -8..-15

Modes:
- "voting": Require min_votes strategies to agree on side before trading.
  Confidence = average of agreeing strategies.
- "weighted_veto": Weight-aware voting with graduated veto.
  Chosen side must have veto_ratio × opposition strength.
- "weighted": Weight each strategy by historical performance.
  Combined confidence = weighted average.
- "best": Take the highest-confidence signal.
"""

import logging
from copy import deepcopy
from dataclasses import replace
from typing import Optional, Dict, Any, List

import pandas as pd

from .base import BaseStrategy, Signal
from core.filter_annotations import FilterAnnotation, AnnotatedSignal

logger = logging.getLogger("bot.strategy.ensemble")


def _get_dynamic_floor(regime: str, symbol: str, side: str, fallback: float) -> float:
    """Lazy import of DynamicThresholds to avoid circular imports."""
    try:
        from llm.dynamic_thresholds import get_dynamic_thresholds
        return get_dynamic_thresholds().get_confidence_floor(
            regime=regime, symbol=symbol, side=side, fallback=fallback
        )
    except Exception:
        return fallback


def _get_tel():
    """Lazy import to avoid circular dependency."""
    try:
        from core.structured_logging import get_trade_event_logger
        return get_trade_event_logger()
    except Exception:
        return None


class EnsembleStrategy:
    """
    Combines multiple strategies into a consensus signal.
    Not a BaseStrategy itself - it wraps multiple strategies.
    """

    def __init__(
        self,
        strategies: List[BaseStrategy],
        mode: str = "voting",
        min_votes: int = 2,
        weights: Optional[Dict[str, float]] = None,
        weight_manager=None,
        veto_ratio: float = 1.2,  # Lowered from 1.5: fee-drag + EV gates handle quality
        chop_detector=None,
        # 2026-06-06: defaults lowered from hardcoded 69.0/68.0 to read from
        # configured ENSEMBLE_CONFIDENCE_FLOOR (20.0 per .env overdrive mode).
        # The previous magic numbers assumed May/June regime distribution and
        # over-filtered current live signals. Per Nunu directive — no fabricated
        # certainty in defaults; if dynamic floor engine fails, fall back to
        # configured value, not a hardcoded "data sweet spot."
        confidence_floor: float = 20.0,
        ranging_confidence_floor: float = 20.0,
        ic_tracker=None,
    ):
        self.strategies = strategies
        self.mode = mode
        self.min_votes = min_votes
        self.weights = weights or {s.name: 1.0 for s in strategies}
        self.weight_manager = weight_manager  # StrategyWeightManager instance
        self.ic_tracker = ic_tracker  # ICTracker: factor inversion protection
        self.veto_ratio = veto_ratio
        self.chop_detector = chop_detector  # ChopDetector instance (Wave 1)
        self.confidence_floor = confidence_floor
        self.ranging_confidence_floor = ranging_confidence_floor
        self._disabled_strategies: set = set()  # Strategy names to skip
        self._regime_profitability: Dict[str, Dict] = {}  # Push 3: regime WR data
        self._last_signals: Dict[str, Dict[str, Signal]] = {}  # symbol -> {strategy -> Signal}
        # Sniper hook: optional callback for single-strategy ensemble rejections.
        # Set by multi_strategy_main when LLM_SNIPER_ENABLED=true.
        # Signature: (signal: Signal, symbol: str) -> None (non-blocking)
        self._sniper_callback = None
        # Hysteresis: EMA-smoothed chop scores prevent floor oscillation on noise
        self._smoothed_chop: Dict[str, float] = {}  # symbol -> smoothed chop_score
        self._chop_ema_alpha: float = 0.3  # Smoothing factor (higher = more reactive)
        self._quality_scorer = None  # Optional: SignalQualityScorer for pre-floor adjustment
        self._shadow_ledger = None  # Optional: ShadowLedger for dormant strategy tracking
        # Rejection tracking: why signals were rejected (for LLM brain learning)
        self._last_rejections: Dict[str, Dict] = {}  # symbol -> {reason, confidence, side, ...}
        self._missed_trade_tracker = None  # Optional: MissedTradeTracker for feedback
        self._rejection_outcome_tracker = None  # Optional: RejectionOutcomeTracker for adaptive learning
        self._correlation_boost = None  # Optional: CrossAssetCorrelationBoost for market-wide confirmation
        self._lead_lag_engine = None  # Optional: LeadLagBoostEngine for BTC lead-lag confidence boost
        self._ev_calibrator = None  # Optional: EVCalibrator for adaptive EV threshold
        self._regime_strategy_weighter = None  # Optional: RegimeStrategyWeighter for regime-aware weight adjustments
        self._override_coordinator = None  # Optional: AgentCoordinator for LLM-reasoned overrides
        # Regime-aware min_votes: current regime per symbol (set externally by engine)
        self._current_regime: Dict[str, str] = {}  # symbol -> regime string (1h)
        self._current_regime_4h: Dict[str, str] = {}  # symbol -> regime string (4h)
        self._volatility_profiles: Dict[str, str] = {}  # symbol -> "low"/"medium"/"high"
        self._current_eval_symbol: Optional[str] = None  # Set during evaluate() for regime-aware weight lookup

    def set_quality_scorer(self, scorer):
        """Inject SignalQualityScorer so quality feedback affects ensemble confidence."""
        self._quality_scorer = scorer

    def set_shadow_ledger(self, ledger):
        """Inject ShadowLedger for tracking disabled strategy predictions."""
        self._shadow_ledger = ledger

    def set_missed_trade_tracker(self, tracker):
        """Inject MissedTradeTracker for comprehensive rejection feedback."""
        self._missed_trade_tracker = tracker

    def set_lead_lag_engine(self, engine):
        """Inject LeadLagBoostEngine for BTC lead-lag confidence boost.

        When BTC makes a decisive move (>0.3% in 15min), the engine creates
        time-delayed lead signals for follower assets (SOL, ETH). The boost
        is applied to aligned strategy signals during their expected lag window.
        This is a BOOST system only — it never generates standalone trades.
        """
        self._lead_lag_engine = engine

    def set_regime_strategy_weighter(self, weighter):
        """Inject RegimeStrategyWeighter for regime-aware weight adjustments.

        When set, strategy weights are multiplied by regime-specific factors
        (e.g., 1.3x for bollinger_squeeze in high_volatility, 0.7x for
        mean_reversion in trending). Auto-tunes from observed performance.
        """
        self._regime_strategy_weighter = weighter

    def set_regime(self, symbol: str, regime: str):
        """Set the current 1h market regime for a symbol.

        Used for regime-aware min_votes: trending markets allow min_votes-1
        since trend direction provides strong confirmation.
        Regime values: 'trend', 'range', 'high_volatility', 'consolidation', 'unknown'
        """
        self._current_regime[symbol] = regime

    def set_regime_4h(self, symbol: str, regime_4h: str):
        """Set the 4h regime for multi-timeframe confirmation.

        When 1h regime disagrees with 4h, 2-agree signals require both to align.
        This prevents counter-trend entries where 1h calls 'bull' but 4h is still 'bear'.
        """
        self._current_regime_4h[symbol] = regime_4h

    # Compatible 1h/4h regime pairs: these mismatches are acceptable.
    _COMPATIBLE_REGIME_PAIRS = {
        ('consolidation', 'trending_bull'),
        ('trending_bull', 'consolidation'),
        ('consolidation', 'trend'),
        ('trend', 'consolidation'),
    }

    def _check_timeframe_alignment(self, symbol: str, agreement_level: int) -> bool:
        """Check if 1h and 4h regimes are aligned for trade entry.

        For 2-agree signals: require 1h AND 4h regime agreement (or compatible pair).
        For 3+ agree signals: 1h alone suffices — high conviction overrides.
        """
        if agreement_level >= 3:
            return True  # 3-agree: 1h alone suffices

        regime_1h = self._current_regime.get(symbol, "unknown")
        regime_4h = self._current_regime_4h.get(symbol)

        if regime_4h is None:
            return True  # No 4h data available — don't block

        if regime_1h == regime_4h:
            return True  # Perfect alignment

        if (regime_1h, regime_4h) in self._COMPATIBLE_REGIME_PAIRS:
            return True  # Acceptable mismatch

        logger.info(
            f"[{symbol}] 4h regime filter: 1h={regime_1h} vs 4h={regime_4h} — "
            f"blocking 2-agree entry (timeframe conflict)"
        )
        return False

    # Regime-gated min_votes: data-driven from 75-day backtest results.
    # trending_bear at 2-agree = -$25/trade × 96 trades = largest performance drag.
    # consolidation 2-agree = 80-89% WR = best regime.
    # Quant philosophy: trade more often with smaller size. Single high-conviction
    # strategy trades allowed in trending regimes (risk_mult=0.5 for 1-agree).
    # In backtest, funding_rate/oi_delta/liquidation_cascade return None (need live data).
    # Effective pool per regime is 3-5 strategies, not 9. Requiring 3/5 = 60% agreement
    # kills almost all signals. Lowered to 2 for regimes with 4+ active strategies.
    # High-risk regimes (panic, low_liquidity, news_dislocation) stay at 3.
    REGIME_MIN_VOTES = {
        'trending_bear':   3,  # Worst regime (10-20% WR): require conviction
        'trending_bull':   2,
        'trend':           2,
        'consolidation':   2,
        'range':           2,
        'high_volatility': 2,
        'panic':           3,   # extreme regime: require conviction
        'low_liquidity':   3,   # thin book: require conviction
        'news_dislocation': 3,  # event-driven: require conviction
        'unknown':         2,
    }

    # Regime-specific strategy allowlist: only strategies with proven edge
    # in each regime are allowed to vote.
    # Regime-specific strategy allowlist: 9 active strategies, regime-gated.
    # New additions: liquidation_cascade, monte_carlo_zones, funding_rate, oi_delta
    STRATEGY_REGIME_ALLOWLIST = {
        'trending_bear':    {'confidence_scorer', 'regime_trend', 'bollinger_squeeze', 'vmc_cipher', 'probability_engine', 'oi_delta', 'liquidation_cascade'},
        'trending_bull':    {'confidence_scorer', 'regime_trend', 'bollinger_squeeze', 'vmc_cipher', 'probability_engine', 'oi_delta'},
        'trend':            {'confidence_scorer', 'regime_trend', 'bollinger_squeeze', 'vmc_cipher', 'probability_engine', 'oi_delta'},
        'consolidation':    {'confidence_scorer', 'multi_tier_quality', 'bollinger_squeeze', 'vmc_cipher', 'probability_engine', 'monte_carlo_zones', 'funding_rate', 'mean_reversion'},  # mean_reversion: designed for consolidation
        'range':            {'confidence_scorer', 'multi_tier_quality', 'bollinger_squeeze', 'vmc_cipher', 'probability_engine', 'monte_carlo_zones', 'funding_rate', 'mean_reversion'},  # mean_reversion: designed for range
        'high_volatility':  {'confidence_scorer', 'probability_engine', 'bollinger_squeeze', 'liquidation_cascade', 'oi_delta'},
        'panic':            {'confidence_scorer', 'liquidation_cascade'},
        'low_liquidity':    {'confidence_scorer'},
        'news_dislocation': {'confidence_scorer'},
        'unknown':          {'confidence_scorer', 'probability_engine', 'monte_carlo_zones', 'mean_reversion'},  # mean_reversion: has internal ADX gate
    }

    def _get_effective_min_votes(self, symbol: str) -> int:
        """Get min_votes for this symbol.

        Per-symbol overrides (data/symbol_strategy_profile.py) win over the
        global default. HYPE uses min_votes=1 because only confidence_scorer
        fires reliably on it (see forensic 2026-04-14).
        """
        try:
            from data.symbol_strategy_profile import get_min_votes_for_symbol
            return get_min_votes_for_symbol(symbol, default=self.min_votes)
        except Exception:
            return self.min_votes

    def set_symbol_volatility_profiles(self, profiles: Dict[str, str]):
        """Set volatility profiles for symbols (e.g., {"HYPE": "high", "BTC": "low"}).

        Used for per-symbol confidence floor capping: high-vol assets get lower
        max floors because their natural price action is inherently choppy.
        """
        self._volatility_profiles = profiles

    def set_disabled_strategies(self, names: set):
        """Temporarily disable specific strategies (e.g., for regime filtering)."""
        self._disabled_strategies = set(names)

    def set_fit_avoid_annotations(self, annotations: Dict[str, str]):
        """WAVE2A L3 (FULL_PIPE_BUILD_MAP R21c/R21d companion, 2026-07-02).

        STRATEGY_REGIME_FIT 'avoid' verdicts no longer disable strategies —
        they ride along as labeled metadata on the votes so the LLM sees the
        fitness table's opinion as context (THE_STANDARD §3b v1.3). Keyed by
        strategy name; value is a human-readable verdict label, e.g.
        "avoid (static theory, no n)". Called by the dispatch layer instead
        of set_disabled_strategies for FIT verdicts.

        Kill-switch: ENSEMBLE_SUPPRESS_FIT_AVOID=true makes evaluate_raw
        suppress these strategies again (legacy behavior, shadow-logged).
        """
        self._fit_avoid_annotations = dict(annotations or {})

    def get_last_signal(self, symbol: str, strategy_name: str) -> Optional[Signal]:
        """Get the last signal from a specific strategy for a symbol."""
        return self._last_signals.get(symbol, {}).get(strategy_name)

    # Map driving strategy → likely trade duration for TF weight selection.
    # Short-term strategies shouldn't get vetoed by daily bearish signals.
    STRATEGY_DURATION_MAP = {
        "multi_tier_quality": "MEDIUM",    # Uses 1h+6h → medium-term trades
        "confidence_scorer": "MEDIUM",     # ADX/MACD/squeeze momentum → medium-term
        "regime_trend": "TREND",           # Uses 1h+6h → trend following
        "monte_carlo_zones": "TREND",      # Uses daily → longer-term levels
        "bollinger_squeeze": "MEDIUM",     # BB squeeze/expansion → medium-term breakouts
        "funding_rate": "SCALP",           # Counter-trade extreme funding → short-term
        "lead_lag": "MEDIUM",              # BTC→alt catch-up → medium-term
        "liquidation_cascade": "SCALP",    # Post-cascade reversal → short-term
        "oi_delta": "MEDIUM",              # OI+price regime → medium-term
        "probability_engine": "TREND",     # Monte Carlo probability cones → trend
        "vmc_cipher": "MEDIUM",            # Multi-oscillator confluence → medium-term
    }

    # Strategy primary timeframe — used for duration-aware opposition penalty.
    # Daily-timeframe strategies penalize intraday signals less (and vice versa).
    STRATEGY_TIMEFRAME = {
        "multi_tier_quality": "intraday",   # 5m + 1h
        "confidence_scorer": "intraday",    # multi-factor, mostly 1h
        "regime_trend": "swing",            # 1h + 6h
        "monte_carlo_zones": "daily",       # daily zones
        "bollinger_squeeze": "intraday",    # BB on 1h candles
        "funding_rate": "intraday",         # Funding rate scalps
        "lead_lag": "intraday",             # BTC→alt lag on 1h
        "liquidation_cascade": "intraday",  # Cascade events on 1h
        "oi_delta": "intraday",             # OI changes on 1h
        "probability_engine": "swing",      # MC paths on 1h, forward-looking
        "vmc_cipher": "intraday",           # Multi-oscillator on 1h
    }

    # Max effective weight for any single strategy's opposition penalty.
    # Prevents a single bad strategy from swinging outcomes too much.
    MAX_OPPOSITION_WEIGHT = 0.8

    def _infer_duration(self, strategy_name: str) -> str:
        """Infer trade duration from the driving strategy."""
        return self.STRATEGY_DURATION_MAP.get(strategy_name, "")

    def _refresh_dynamic_weights(self):
        """Refresh ensemble weights using rolling strategy performance."""
        if self.weight_manager is not None:
            try:
                dynamic = self.weight_manager.get_rolling_weights()
                if dynamic:
                    self.weights = dynamic
                    # Log strategies that have been auto-muted
                    for name, w in dynamic.items():
                        if w <= 0.05:
                            logger.warning(
                                f"[ENSEMBLE] {name} effectively muted (weight={w}) "
                                f"-- sustained poor performance"
                            )
                else:
                    # Weights empty — likely no trade history yet.
                    # Try loading persisted weights from file as fallback.
                    # get_all_weights() reads from persisted file (smoothed, not rolling)
                    fallback = self.weight_manager.get_all_weights()
                    if fallback:
                        self.weights = fallback
                        logger.info(
                            "[ENSEMBLE] Loaded persisted strategy weights as fallback "
                            f"(no rolling data yet): {fallback}"
                        )
                        return
                    logger.warning(
                        "[ENSEMBLE] Dynamic strategy weights empty — using default equal weights. "
                        "Run a backtest with learning bridge to seed performance data."
                    )
            except Exception as e:
                logger.debug(f"Dynamic weight refresh failed: {e}")

    def get_all_required_timeframes(self) -> List[str]:
        """Get the union of all timeframes needed by all strategies."""
        tfs = set()
        for s in self.strategies:
            tfs.update(s.get_required_timeframes())
        return list(tfs)

    def apply_config_disables(self, config):
        """Apply strategy disable flags from TradingConfig.

        Strategies with proven negative edge are disabled via config flags
        but continue to generate shadow signals for IC tracking.
        """
        if hasattr(config, 'strategy_lead_lag_enabled') and not config.strategy_lead_lag_enabled:
            self._disabled_strategies.add('lead_lag')
        if hasattr(config, 'strategy_multi_tier_quality_enabled') and not config.strategy_multi_tier_quality_enabled:
            self._disabled_strategies.add('multi_tier_quality')

    def _get_regime_allowed_strategies(self, symbol: str) -> Optional[set]:
        """Get the set of strategies allowed in the current regime for this symbol.

        Returns None if no regime filter should be applied:
        - No regime has been set for this symbol
        - Regime is not in the allowlist lookup
        This ensures the filter only activates when we have explicit regime data.
        """
        if symbol not in self._current_regime:
            return None  # No regime set — don't filter
        regime = self._current_regime[symbol]
        allowed = self.STRATEGY_REGIME_ALLOWLIST.get(regime)
        # For 'unknown' regime, only filter if we actually have an empty set
        # (which means "block all"). If the regime isn't in the lookup, don't filter.
        if allowed is not None and len(allowed) == 0:
            # Only block all if regime was explicitly set (not just defaulting to unknown)
            return allowed
        return allowed

    def evaluate(
        self, symbol: str, data: Dict[str, pd.DataFrame]
    ) -> Optional[Signal]:
        """
        Run all strategies and combine their signals.
        Returns a single consensus Signal or None.
        """
        # Dynamic weight refresh: pull rolling weights before each evaluation
        self._refresh_dynamic_weights()

        # Set current eval symbol for regime-aware weight lookups in _get_strategy_weight
        self._current_eval_symbol = symbol

        # Get regime-allowed strategies for this symbol
        regime_allowed = self._get_regime_allowed_strategies(symbol)
        # Per-symbol strategy profile (drops dead-weight strategies per symbol)
        try:
            from data.symbol_strategy_profile import get_active_strategies_for_symbol
            symbol_active = get_active_strategies_for_symbol(symbol)
        except Exception:
            symbol_active = None

        signals: List[Signal] = []
        shadow_signals: List[Signal] = []  # Disabled strategy signals for IC tracking
        self._last_raw_signals: Dict[str, List[Signal]] = getattr(self, '_last_raw_signals', {})
        active_count = 0  # Strategies that ran (didn't error or get disabled)
        error_count = 0

        for strategy in self.strategies:
            # Config-disabled strategies: still generate shadow signals for IC tracking
            if strategy.name in self._disabled_strategies:
                try:
                    sig = strategy.evaluate(symbol, data)
                    if sig is not None:
                        shadow_signals.append(deepcopy(sig))
                        # Persist shadow signal for dormant strategy tracking
                        if self._shadow_ledger:
                            try:
                                self._shadow_ledger.record_shadow_signal(
                                    factor=sig.strategy,
                                    symbol=symbol,
                                    side=sig.side,
                                    confidence=sig.confidence,
                                    entry_price=sig.entry,
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                continue

            # Regime-based strategy filter
            if regime_allowed is not None and strategy.name not in regime_allowed:
                continue
            active_count += 1
            try:
                sig = strategy.evaluate(symbol, data)
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                error_count += 1
                logger.warning(f"[{symbol}] {strategy.name} error: {e}")

        # Telemetry: record which strategies fired/silent for this symbol
        try:
            from core.pipeline_telemetry import get_telemetry as _get_pt
            _pt = _get_pt()
            for _s in self.strategies:
                if _s.name in self._disabled_strategies:
                    continue
                _sig_match = next((x for x in signals if x.strategy == _s.name), None)
                _pt.record_strategy(symbol, _s.name, _sig_match is not None, _sig_match.confidence if _sig_match else 0, _sig_match.side if _sig_match else "")
        except Exception:
            pass

        # Per-strategy signal map for overwatch analysis
        _fired = [s.strategy for s in signals]
        _strat_names = [s.name for s in self.strategies if s.name not in self._disabled_strategies]
        _silent = [n for n in _strat_names if n not in _fired and (regime_allowed is None or n in regime_allowed)]
        if signals:
            logger.info(f"[{symbol}] Strategy map: fired={_fired} silent={_silent} ({len(signals)}/{active_count})")

        # Store raw signals for sniper access (before any consensus/EV filtering)
        self._last_raw_signals[symbol] = [deepcopy(s) for s in signals]

        # Deep copy signals FIRST, then cache copies — prevents mutation between
        # cache write and copy if any code path modifies signals in-place.
        signals = [deepcopy(s) for s in signals]

        # Cache copies for context extraction (these won't be mutated further)
        self._last_signals[symbol] = {s.strategy: deepcopy(s) for s in signals}

        if not signals:
            return None

        # ── Regime-aware min_votes + graceful degradation ──
        # In trending regimes, reduce min_votes by 1 since trend confirmation is strong.
        effective_min_votes = self._get_effective_min_votes(symbol)
        if effective_min_votes != self.min_votes:
            logger.info(
                f"[{symbol}] Regime-aware min_votes: {self.min_votes} → {effective_min_votes} "
                f"(regime={self._current_regime.get(symbol, 'unknown')})"
            )
        # If strategies errored, lower min_votes so the system doesn't deadlock.
        if error_count > 0 and active_count > 0:
            degraded = max(2, min(effective_min_votes, active_count - error_count))
            if degraded != effective_min_votes:
                logger.info(
                    f"[{symbol}] Strategy degradation: {error_count} errors, "
                    f"min_votes {effective_min_votes} → {degraded}"
                )
                effective_min_votes = degraded

        # Chop detector: graduated choppy market filter
        # Instead of binary kill, attach chop_score and let the confidence floor
        # handle rejection. This allows high-conviction setups through even in chop.
        if self.chop_detector:
            is_chop, chop_score, chop_detail = self.chop_detector.is_choppy(symbol, data)
            # Attach chop score to metadata — graduated floor below will handle filtering
            for sig in signals:
                sig.metadata["chop_score"] = round(chop_score, 3)
            if is_chop:
                logger.info(
                    f"[{symbol}] Chop detected (score={chop_score:.2f}), "
                    f"applying graduated confidence floor"
                )
        elif self._is_low_volume(symbol, data):
            # 2026-05-30: NEUTRALIZED — volume chop is now informational, not a gate.
            # Pure data flows to LLM; LLM decides whether low volume warrants skipping.
            logger.info(f"[{symbol}] Low volume detected (was chop-filtering; now informational only)")
            if self._missed_trade_tracker is not None:
                self._missed_trade_tracker.record_ensemble_rejection(
                    symbol=symbol, signals=signals, reason="low_volume_chop_observed_not_blocked"
                )
            # NO return — continue to signal construction

        # LLM-first mode: the EV gate inside _merge_signals becomes advisory.
        # Without this, consensus signals like BTC funding_rate BUY get
        # EV-blocked in evaluate() and never reach the LLM (the rescue path
        # only fires when evaluate returns None, which it does — but for a
        # DIFFERENT reason than solo min_votes). Setting llm_first_raw=True
        # here keeps consensus signals alive so they can reach LLM-first.
        _llm_first_active = False
        try:
            import os as _os
            _llm_first_active = _os.environ.get("LLM_FIRST_MODE", "false").lower() == "true"
        except Exception:
            pass

        if self.mode == "voting":
            result = self._voting(symbol, signals, effective_min_votes, llm_first_raw=_llm_first_active)
        elif self.mode == "weighted_veto":
            result = self._weighted_veto(symbol, signals, effective_min_votes, llm_first_raw=_llm_first_active)
        elif self.mode == "weighted":
            result = self._weighted(symbol, signals)
        elif self.mode == "best":
            result = self._best(symbol, signals)
        else:
            result = self._voting(symbol, signals, effective_min_votes, llm_first_raw=_llm_first_active)

        if result is None:
            return None

        # ── 4h regime confirmation filter (B2) ──
        # For 2-agree signals, check 1h and 4h regime alignment.
        # Instead of hard-blocking, apply a 0.7x sizing penalty for mismatches.
        agreement_level = result.metadata.get("num_agree", 1) if result.metadata else 1
        if not self._check_timeframe_alignment(symbol, agreement_level):
            result.metadata["risk_mult_override"] = result.metadata.get("risk_mult_override", 1.0) * 0.7
            result.metadata["4h_regime_penalty"] = True
            logger.info(
                f"[{symbol}] 4h regime conflict: 1h={self._current_regime.get(symbol, 'unknown')} "
                f"vs 4h={self._current_regime_4h.get(symbol, 'unknown')} — "
                f"applying 0.7x sizing penalty instead of blocking"
            )

        # ── Pre-floor quality adjustment ──
        # Apply signal quality feedback to ensemble confidence BEFORE the floor check.
        # This lets historically bad setups get rejected even with high raw confidence.
        if self._quality_scorer is not None:
            try:
                import datetime as _dt
                from feedback.signal_quality import QualityFeatures
                _utc_h = _dt.datetime.now(_dt.timezone.utc).hour
                features = QualityFeatures(
                    confidence=result.confidence,
                    num_strategies_agree=result.metadata.get("num_agree", 1),
                    total_strategies=len(self.strategies),
                    symbol=symbol,
                    side=result.side,
                    regime=result.metadata.get("regime", ""),
                    entry_type=result.metadata.get("entry_type", ""),
                    hour_of_day=_utc_h,
                )
                _adj, _mult, _breakdown = self._quality_scorer.adjust_confidence(
                    result.confidence, features
                )
                # Bound multiplier to 0.5-1.3 (same as SignalQualityScorer range)
                _mult = max(0.5, min(1.3, _mult))
                if abs(_mult - 1.0) > 0.01:
                    result.confidence = max(0, min(100, result.confidence * _mult))
                    result.metadata["quality_multiplier"] = round(_mult, 3)
                    logger.info(
                        f"[{symbol}] Quality adjustment: *{_mult:.2f} -> "
                        f"conf={result.confidence:.1f}%"
                    )
            except Exception as e:
                logger.debug(f"Quality scorer error: {e}")

        # ── Post-merge quality gates ──

        # 0. Graduated rules: apply learned rules from validated hypotheses
        try:
            from llm.graduated_rules import get_graduated_rules_engine
            _gre = get_graduated_rules_engine()
            _regime = result.metadata.get("regime", "")
            _setup = result.metadata.get("entry_type", "")
            _n_agree = result.metadata.get("num_agree", 1)
            _vetoed, _adj_conf, _rule_summary, _veto_rule_ids = _gre.evaluate_signal(
                symbol=symbol, regime=_regime, side=result.side,
                strategy=result.strategy or "", setup_type=_setup,
                num_agree=_n_agree, confidence=result.confidence,
                strategies_active=result.metadata.get("strategies_agree") or [],
            )
            if _vetoed:
                # 2026-05-30: under LLM_FIRST_MODE, graduated-rule vetoes become informational.
                # These rules carry stale verdicts (e.g. "HYPE BUY 23% WR" pre-rally).
                # The LLM gets the rule summary as context and decides itself.
                import os as _os
                if _os.environ.get("LLM_FIRST_MODE", "false").lower() == "true":
                    logger.info(f"[{symbol}] Graduated rule WOULD veto (LLM_FIRST_MODE override): {_rule_summary}")
                    result.metadata["graduated_rule_veto_overridden"] = _rule_summary
                    # Override = trade is NOT blocked → plain counterfactual WITHOUT
                    # veto_rule_ids. Must not enter the veto accuracy denominator.
                    self._record_counterfactual(result, "graduated_rule_veto_overridden")
                    # Continue — don't return None
                else:
                    logger.info(f"[{symbol}] Signal VETOED by graduated rule: {_rule_summary}")
                    # Real block → stamp veto_rule_ids so accuracy resolves by rule_id.
                    self._record_veto_counterfactual(result, _veto_rule_ids)
                    return None
            if _adj_conf != result.confidence:
                logger.info(f"[{symbol}] Graduated rules: {result.confidence:.0f}% → {_adj_conf:.0f}% ({_rule_summary})")
                result.confidence = _adj_conf
                result.metadata["graduated_rule_adj"] = _rule_summary
        except Exception:
            pass

        # 1. Minimum confidence floor — dynamic regime-aware
        # Base floor computed from live per-regime WR in trade_dna (updates every 30min).
        # Time-of-day adjustment applied on top, then chop score.
        _result_regime = result.metadata.get("regime", "")
        effective_floor = _get_dynamic_floor(
            regime=_result_regime,
            symbol=symbol,
            side=result.side,
            fallback=self.confidence_floor,
        )
        result.metadata["dynamic_floor"] = round(effective_floor, 1)
        # Time-of-day adjustment from live hourly WR data
        try:
            import datetime as _dt
            from llm.dynamic_thresholds import get_dynamic_thresholds as _get_dt
            _utc_hour = _dt.datetime.now(_dt.timezone.utc).hour
            _dt_engine = _get_dt()
            _tod_adj = _dt_engine.get_time_of_day_floor_adj(_utc_hour)
            if _tod_adj != 0.0:
                effective_floor = effective_floor + _tod_adj
                result.metadata["tod_floor_adj"] = round(_tod_adj, 1)
            # Entry type / trade profile floor adjustment (TREND=14% WR → +8 floor)
            _entry_type = result.metadata.get("entry_type", "")
            if _entry_type:
                _et_adj = _dt_engine.get_entry_type_floor_adj(_entry_type)
                if _et_adj != 0.0:
                    effective_floor = effective_floor + _et_adj
                    result.metadata["entry_type_floor_adj"] = round(_et_adj, 1)
        except Exception:
            pass

        raw_chop = result.metadata.get("chop_score", 0)
        # Apply EMA smoothing to prevent floor oscillation on noise
        prev = self._smoothed_chop.get(symbol, raw_chop)
        chop_score = self._chop_ema_alpha * raw_chop + (1 - self._chop_ema_alpha) * prev
        self._smoothed_chop[symbol] = chop_score
        result.metadata["chop_score_smoothed"] = round(chop_score, 3)
        if chop_score > 0.35:
            if chop_score >= 0.65:
                # Extreme chop: floor rises from regime-dynamic base toward max.
                _vol_profile = getattr(self, '_volatility_profiles', {}).get(symbol, "medium")
                _max_chop_floor = {"low": 77.0, "medium": 77.0, "high": 77.0}.get(_vol_profile, 77.0)
                chop_intensity = min(1.0, (chop_score - 0.65) / 0.20)  # 0→1 over 0.65→0.85
                effective_floor = effective_floor + chop_intensity * (
                    _max_chop_floor - effective_floor
                )
            else:
                # Moderate chop: push floor upward toward ranging_confidence_floor
                chop_intensity = (chop_score - 0.35) / 0.30  # 0→1 over 0.35→0.65
                effective_floor = effective_floor + chop_intensity * (
                    self.ranging_confidence_floor - self.confidence_floor
                )
            result.metadata["effective_confidence_floor"] = round(effective_floor, 1)

        if result.confidence < effective_floor:
            # Magnitude bypass: high-R:R signals on volatile assets get a second chance.
            # Data: HYPE BUY signals at 55-65% conf routinely produce 15-22% moves.
            # Allow if: (1) R:R > 2.5, (2) high-vol asset, (3) confidence within 10% of floor.
            try:
                _rr = float(result.risk_reward_tp1) if hasattr(result, 'risk_reward_tp1') else 0
            except (TypeError, ValueError):
                _rr = 0
            _vol_prof = getattr(self, '_volatility_profiles', {}).get(symbol, "medium")
            _gap = effective_floor - result.confidence
            _magnitude_bypass = (
                _rr >= 2.5
                and _vol_prof in ("high", "medium")
                and _gap <= 10.0  # within 10 points of floor
                and result.confidence >= 55.0  # absolute minimum
            )
            if _magnitude_bypass:
                # Let it through but mark for reduced sizing
                result.metadata["magnitude_bypass"] = True
                result.metadata["risk_mult_override"] = 0.65  # 65% size — reduced but meaningful
                logger.info(
                    f"[{symbol}] Magnitude bypass: conf {result.confidence:.0f}% < floor "
                    f"{effective_floor:.0f}% but R:R={_rr:.1f} on {_vol_prof}-vol asset — "
                    f"allowing at 65% size"
                )
            # WAVE2A L3 (FULL_PIPE_BUILD_MAP M5, 2026-07-02): HYPE BUY
            # floor-bypass demoted to SHADOW. The "88.6% WR / 40K
            # counterfactuals" claim was contradicted by 35 live trades
            # (23% WR, -$77.26 — F8 audit 2026-05-04). The bypass no longer
            # fires; the would-have-bypassed case is logged + counterfactual-
            # recorded so the opinion gets graded against price.
            # Kill-switch: HYPE_BUY_BYPASS_ENFORCE=true restores the bypass.
            elif (symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "") == "HYPE"
                  and result.side == "BUY"
                  and result.confidence >= 55.0):
                import os as _os
                if _os.environ.get("HYPE_BUY_BYPASS_ENFORCE", "false").lower() == "true":
                    result.metadata["hype_buy_bypass"] = True
                    result.metadata["risk_mult_override"] = 0.70
                    logger.info(
                        f"[{symbol}] HYPE BUY bypass ENFORCED (kill-switch): conf "
                        f"{result.confidence:.0f}% < floor {effective_floor:.0f}%"
                    )
                else:
                    logger.info(
                        f"[{symbol}] [SHADOW-GATE] hype_buy_bypass would_pass conf="
                        f"{result.confidence:.0f}% < floor {effective_floor:.0f}% — M5 "
                        f"shadow (88.6% WR claim refuted live: 23% WR n=35; "
                        f"HYPE_BUY_BYPASS_ENFORCE=true restores)"
                    )
                    self._record_counterfactual(
                        result, f"confidence_floor_{effective_floor:.0f}"
                    )
                    return None
            else:
                logger.info(
                    f"[{symbol}] Signal rejected: confidence {result.confidence:.0f}% "
                    f"< {effective_floor:.0f}% floor (chop={chop_score:.2f})"
                )
                self._record_counterfactual(result, f"confidence_floor_{effective_floor:.0f}")
                return None

        # 2. Trend alignment: FLIP counter-trend signals to ride the trend
        # Use duration-aware weights: short-term strategies don't get killed
        # by daily bearish signals, and long-term strategies don't flip on 5m noise.
        _driver = result.strategy or ""
        _duration_hint = self._infer_duration(_driver)
        result = self._trend_alignment_adjust(symbol, data, result, _duration_hint)

        if result is None:
            logger.info(f"[{symbol}] Signal rejected by trend alignment (counter-trend)")
            return None

        # Re-check floor after adjustment (should rarely fail now since we flip instead of crush)
        if result.confidence < effective_floor:
            logger.info(
                f"[{symbol}] Signal rejected: confidence {result.confidence:.0f}% "
                f"< {effective_floor:.0f}% after trend adjustment"
            )
            self._record_counterfactual(result, f"trend_adj_floor_{effective_floor:.0f}")
            return None

        # 3. BTC lead-lag boost: amplify confidence when BTC has made a decisive
        #    move and this asset is in the expected lag window. BOOST ONLY —
        #    never generates standalone trades.
        if self._lead_lag_engine is not None:
            try:
                _ll_boost = self._lead_lag_engine.get_boost(symbol, result.side)
                if _ll_boost > 0:
                    _pre_conf = result.confidence
                    result.confidence = min(100.0, result.confidence + _ll_boost)
                    result.metadata["lead_lag_boost"] = round(_ll_boost, 2)
                    result.metadata["lead_lag_pre_conf"] = round(_pre_conf, 1)
                    logger.info(
                        f"[{symbol}] Lead-lag boost: +{_ll_boost:.1f} confidence "
                        f"({_pre_conf:.0f}% -> {result.confidence:.0f}%) "
                        f"[BTC leading {symbol}]"
                    )
            except Exception as _ll_err:
                logger.debug(f"Lead-lag boost error: {_ll_err}")

        # Log SIGNAL_GENERATED for the final consensus signal
        try:
            tel = _get_tel()
            if tel is not None:
                tel.log(
                    "SIGNAL_GENERATED",
                    result.symbol,
                    side=result.side,
                    strategy=result.strategy or "",
                    confidence=result.confidence,
                    entry=result.entry,
                    sl=result.sl,
                    tp1=result.tp1,
                    tp2=getattr(result, "tp2", 0.0),
                    atr=getattr(result, "atr", 0.0),
                    regime=(result.metadata or {}).get("regime", ""),
                    num_agree=(result.metadata or {}).get("num_agree", 1),
                    strategies_agree=(result.metadata or {}).get("strategies_agree", []),
                )
        except Exception:
            pass

        # Apply signal quality context multipliers (learned meta-confidence)
        try:
            if hasattr(self, '_signal_quality_scorer') and self._signal_quality_scorer is not None:
                from feedback.signal_quality import QualityFeatures

                features = QualityFeatures(
                    confidence=result.confidence,
                    num_strategies_agree=(result.metadata or {}).get("num_agree", 1),
                    total_strategies=len(self.strategies),
                    symbol=symbol,
                    side=result.side,
                    regime=(result.metadata or {}).get("regime", "unknown"),
                    entry_type=(result.metadata or {}).get("entry_type", ""),
                )
                quality_mult, breakdown = self._signal_quality_scorer.score_signal(features)
                if quality_mult != 1.0:
                    old_conf = result.confidence
                    result.confidence = min(100.0, result.confidence * quality_mult)
                    logger.info(
                        f"[ENSEMBLE] {symbol} {result.side} Quality multiplier: {quality_mult:.3f} "
                        f"({old_conf:.0f}% → {result.confidence:.0f}%) | {breakdown}"
                    )
        except Exception as _sq_err:
            logger.debug(f"Signal quality scoring error: {_sq_err}")

        # Telemetry: record ensemble consensus result
        try:
            from core.pipeline_telemetry import get_telemetry as _get_pt
            _get_pt().record_ensemble(symbol, {"confidence": result.confidence, "side": result.side, "num_agree": (result.metadata or {}).get("num_agree", 1), "strategies": (result.metadata or {}).get("strategies_agree", [])})
        except Exception:
            pass

        return result

    def evaluate_raw(
        self, symbol: str, data: Dict[str, pd.DataFrame]
    ) -> Optional[Signal]:
        """Generate ensemble signal WITHOUT quality filters for LLM-first mode.

        Returns the raw consensus signal with metadata (chop_score, trend
        alignment, quality_score, win_prob, ev, etc.) attached as context
        for the LLM pipeline — but NOT used as hard gates.

        The only reason this returns None is if no strategies produce signals
        or if there's insufficient vote consensus. Quality filtering is
        delegated entirely to the LLM agents.
        """
        # Dynamic weight refresh
        self._refresh_dynamic_weights()
        self._current_eval_symbol = symbol

        # Get regime-allowed strategies for this symbol
        regime_allowed = self._get_regime_allowed_strategies(symbol)
        # Get per-symbol active strategy set (drops dead-weight strategies).
        # Returns None if no override for this symbol (legacy: all active).
        try:
            from data.symbol_strategy_profile import get_active_strategies_for_symbol
            symbol_active = get_active_strategies_for_symbol(symbol)
        except Exception:
            symbol_active = None

        # ── WAVE2A L3 VOTER EMANCIPATION (FULL_PIPE_BUILD_MAP R21a/R21b/R21c,
        # 2026-07-02) ──
        # The regime allowlist, per-symbol strategy profile, and FIT-avoid
        # table are OPINIONS (static tables, era-unstamped WRs, no per-cell n).
        # Per doctrine they no longer suppress votes in the LLM-first raw
        # path: every strategy votes, and each filter's verdict rides along
        # as LABELED METADATA so the LLM judges with full information
        # (THE_STANDARD §3b v1.3). Kill-switches restore legacy suppression
        # (suppressed votes are then shadow-logged, never silently dropped):
        #   ENSEMBLE_SUPPRESS_REGIME_ALLOWLIST=true
        #   ENSEMBLE_SUPPRESS_SYMBOL_PROFILE=true
        #   ENSEMBLE_SUPPRESS_FIT_AVOID=true
        import os as _os
        _suppress_regime = _os.environ.get(
            "ENSEMBLE_SUPPRESS_REGIME_ALLOWLIST", "false").lower() == "true"
        _suppress_profile = _os.environ.get(
            "ENSEMBLE_SUPPRESS_SYMBOL_PROFILE", "false").lower() == "true"
        _suppress_fit = _os.environ.get(
            "ENSEMBLE_SUPPRESS_FIT_AVOID", "false").lower() == "true"
        _regime_now = self._current_regime.get(symbol, "unknown")
        _fit_notes = getattr(self, "_fit_avoid_annotations", None) or {}
        _allowlist_flagged: List[str] = []  # voted; allowlist would have suppressed
        _profile_flagged: List[str] = []    # voted; symbol profile would have suppressed

        signals: List[Signal] = []
        shadow_signals: List[Signal] = []
        self._last_raw_signals: Dict[str, List[Signal]] = getattr(self, '_last_raw_signals', {})
        active_count = 0
        error_count = 0

        for strategy in self.strategies:
            # Config-disabled strategies (owner intent via apply_config_disables)
            # still shadow-record only. FIT-avoid no longer routes through
            # _disabled_strategies (see set_fit_avoid_annotations), but if the
            # dispatch layer still sends it here, this path preserves the
            # shadow record.
            _flag_regime = (regime_allowed is not None
                            and strategy.name not in regime_allowed)
            _flag_profile = (symbol_active is not None
                             and strategy.name not in symbol_active)
            _kill_switch_suppressed = (
                (_flag_regime and _suppress_regime)
                or (_flag_profile and _suppress_profile)
                or (_suppress_fit and strategy.name in _fit_notes)
            )
            if strategy.name in self._disabled_strategies or _kill_switch_suppressed:
                try:
                    sig = strategy.evaluate(symbol, data)
                    if sig is not None:
                        shadow_signals.append(deepcopy(sig))
                        if _kill_switch_suppressed:
                            _src = ("regime_allowlist" if (_flag_regime and _suppress_regime)
                                    else "symbol_profile" if (_flag_profile and _suppress_profile)
                                    else "fit_avoid")
                            logger.info(
                                f"[{symbol}] [SUPPRESS-SHADOW] {strategy.name} {sig.side} "
                                f"conf={sig.confidence:.0f}% vote suppressed by {_src} "
                                f"kill-switch (regime={_regime_now})"
                            )
                        if self._shadow_ledger:
                            try:
                                self._shadow_ledger.record_shadow_signal(
                                    factor=sig.strategy,
                                    symbol=symbol,
                                    side=sig.side,
                                    confidence=sig.confidence,
                                    entry_price=sig.entry,
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                continue

            if _flag_regime:
                _allowlist_flagged.append(strategy.name)
            if _flag_profile:
                _profile_flagged.append(strategy.name)
            active_count += 1
            try:
                sig = strategy.evaluate(symbol, data)
                if sig is not None:
                    # The vote flows; the filter's opinion rides along (v1.3).
                    if _flag_regime:
                        sig.metadata["regime_allowlist_would_suppress"] = (
                            f"mechanically disallowed in regime '{_regime_now}' "
                            "(static allowlist, no per-cell n) — vote flowed"
                        )
                    if _flag_profile:
                        sig.metadata["symbol_profile_would_suppress"] = (
                            "per-symbol profile claims no empirical edge "
                            "(era-unstamped WR) — vote flowed"
                        )
                    if strategy.name in _fit_notes:
                        sig.metadata["fit_avoid_would_suppress"] = (
                            f"fitness table: {_fit_notes[strategy.name]} "
                            "(static theory, no n) — vote flowed"
                        )
                    signals.append(sig)
            except Exception as e:
                error_count += 1
                logger.warning(f"[{symbol}] {strategy.name} error: {e}")

        # Telemetry
        try:
            from core.pipeline_telemetry import get_telemetry as _get_pt
            _pt = _get_pt()
            for _s in self.strategies:
                if _s.name in self._disabled_strategies:
                    continue
                _sig_match = next((x for x in signals if x.strategy == _s.name), None)
                _pt.record_strategy(symbol, _s.name, _sig_match is not None, _sig_match.confidence if _sig_match else 0, _sig_match.side if _sig_match else "")
        except Exception:
            pass

        # Per-strategy signal map
        _fired = [s.strategy for s in signals]
        _strat_names = [s.name for s in self.strategies if s.name not in self._disabled_strategies]
        # WAVE2A L3: allowlist no longer suppresses voters, so every active
        # strategy that didn't fire is genuinely silent.
        _silent = [n for n in _strat_names if n not in _fired]
        if signals:
            logger.info(f"[{symbol}] RAW strategy map: fired={_fired} silent={_silent} ({len(signals)}/{active_count})")

        self._last_raw_signals[symbol] = [deepcopy(s) for s in signals]
        signals = [deepcopy(s) for s in signals]
        self._last_signals[symbol] = {s.strategy: deepcopy(s) for s in signals}

        if not signals:
            return None

        # ── Voting / consensus ──
        # In LLM-first mode (evaluate_raw), we allow solo signals through
        # because the LLM will make the quality decision. The min_votes
        # requirement is a mechanical filter the LLM should override.
        effective_min_votes = self._get_effective_min_votes(symbol)
        if error_count > 0 and active_count > 0:
            degraded = max(2, min(effective_min_votes, active_count - error_count))
            if degraded != effective_min_votes:
                effective_min_votes = degraded

        # Chop detection: attach score as metadata (DON'T filter)
        if self.chop_detector:
            is_chop, chop_score, chop_detail = self.chop_detector.is_choppy(symbol, data)
            for sig in signals:
                sig.metadata["chop_score"] = round(chop_score, 3)

        # LLM-first bypass: allow min_votes=1 so solo signals reach the LLM.
        # The LLM decides if a solo signal is worth taking, not the ensemble.
        _llm_first_min = 1

        if self.mode == "voting":
            result = self._voting(symbol, signals, _llm_first_min, llm_first_raw=True)
        elif self.mode == "weighted_veto":
            result = self._weighted_veto(symbol, signals, _llm_first_min, llm_first_raw=True)
        elif self.mode == "weighted":
            result = self._weighted(symbol, signals)
        elif self.mode == "best":
            result = self._best(symbol, signals)
        else:
            result = self._voting(symbol, signals, _llm_first_min, llm_first_raw=True)

        if result is None:
            return None

        # ── Attach metadata WITHOUT filtering ──
        # These are context for the LLM, not gates.

        # WAVE2A L3 (R21a/R21b/R21c): labeled voter-suppression opinion —
        # which of the agreeing voters the mechanical filters would have
        # suppressed. Context for the LLM, never enforcement.
        _agreeing_now = (result.metadata or {}).get("strategies_agree") or [result.strategy]
        _fit_flagged_agreeing = {
            k: v for k, v in _fit_notes.items() if k in _agreeing_now
        }
        if _allowlist_flagged or _profile_flagged or _fit_flagged_agreeing:
            result.metadata["voter_suppression_opinion"] = {
                "regime": _regime_now,
                "regime_allowlist_would_suppress": list(_allowlist_flagged),
                "symbol_profile_would_suppress": list(_profile_flagged),
                "fit_avoid": _fit_flagged_agreeing,
                "note": ("mechanical filter opinions (static tables, no per-cell n) — "
                         "votes flowed per doctrine; informational only"),
            }
            logger.info(
                f"[{symbol}] [VOTER-EMANCIPATED] allowlist_flagged={_allowlist_flagged} "
                f"profile_flagged={_profile_flagged} fit_flagged={list(_fit_flagged_agreeing)} "
                f"regime={_regime_now} (votes flowed, opinion labeled)"
            )

        # Chop score (smoothed)
        raw_chop = result.metadata.get("chop_score", 0)
        prev = self._smoothed_chop.get(symbol, raw_chop)
        chop_score = self._chop_ema_alpha * raw_chop + (1 - self._chop_ema_alpha) * prev
        self._smoothed_chop[symbol] = chop_score
        result.metadata["chop_score_smoothed"] = round(chop_score, 3)

        # Effective confidence floor (what the mechanical system would use)
        _meta_regime = result.metadata.get("regime", "")
        effective_floor = _get_dynamic_floor(
            regime=_meta_regime, symbol=symbol, side=result.side, fallback=self.confidence_floor
        )
        if chop_score > 0.35:
            if chop_score >= 0.65:
                chop_intensity = min(1.0, (chop_score - 0.65) / 0.20)
                effective_floor = effective_floor + chop_intensity * (77.0 - effective_floor)
            else:
                chop_intensity = (chop_score - 0.35) / 0.30
                effective_floor = effective_floor + chop_intensity * (self.ranging_confidence_floor - self.confidence_floor)
        result.metadata["mechanical_confidence_floor"] = round(effective_floor, 1)
        result.metadata["would_pass_confidence_floor"] = result.confidence >= effective_floor

        # 4h regime alignment info
        agreement_level = result.metadata.get("num_agree", 1) if result.metadata else 1
        regime_aligned = self._check_timeframe_alignment(symbol, agreement_level)
        result.metadata["regime_4h_aligned"] = regime_aligned
        result.metadata["regime_1h"] = self._current_regime.get(symbol, "unknown")
        result.metadata["regime_4h"] = self._current_regime_4h.get(symbol, "unknown")

        # Quality score (attach but don't filter)
        if self._quality_scorer is not None:
            try:
                import datetime as _dt
                from feedback.signal_quality import QualityFeatures
                _utc_h = _dt.datetime.now(_dt.timezone.utc).hour
                features = QualityFeatures(
                    confidence=result.confidence,
                    num_strategies_agree=result.metadata.get("num_agree", 1),
                    total_strategies=len(self.strategies),
                    symbol=symbol,
                    side=result.side,
                    regime=result.metadata.get("regime", ""),
                    entry_type=result.metadata.get("entry_type", ""),
                    hour_of_day=_utc_h,
                )
                _adj, _mult, _breakdown = self._quality_scorer.adjust_confidence(
                    result.confidence, features
                )
                result.metadata["quality_multiplier"] = round(max(0.5, min(1.3, _mult)), 3)
                result.metadata["quality_breakdown"] = _breakdown
            except Exception:
                pass

        # Graduated rules check (attach as advisory, don't veto)
        try:
            from llm.graduated_rules import get_graduated_rules_engine
            _gre = get_graduated_rules_engine()
            _regime = result.metadata.get("regime", "")
            _setup = result.metadata.get("entry_type", "")
            _n_agree = result.metadata.get("num_agree", 1)
            _vetoed, _adj_conf, _rule_summary, _ = _gre.evaluate_signal(
                symbol=symbol, regime=_regime, side=result.side,
                strategy=result.strategy or "", setup_type=_setup,
                num_agree=_n_agree, confidence=result.confidence,
                strategies_active=result.metadata.get("strategies_agree") or [],
            )
            result.metadata["graduated_rules_advisory"] = {
                "would_veto": _vetoed,
                "adjusted_confidence": round(_adj_conf, 1),
                "summary": _rule_summary,
            }
        except Exception:
            pass

        # Trend alignment info (compute but don't filter)
        result.metadata["raw_confidence"] = result.confidence
        result.metadata["signal_source"] = "evaluate_raw"

        # Log SIGNAL_GENERATED
        try:
            tel = _get_tel()
            if tel is not None:
                tel.log(
                    "SIGNAL_GENERATED",
                    result.symbol,
                    side=result.side,
                    strategy=result.strategy or "",
                    confidence=result.confidence,
                    entry=result.entry,
                    sl=result.sl,
                    tp1=result.tp1,
                    tp2=getattr(result, "tp2", 0.0),
                    atr=getattr(result, "atr", 0.0),
                    regime=(result.metadata or {}).get("regime", ""),
                    num_agree=(result.metadata or {}).get("num_agree", 1),
                    strategies_agree=(result.metadata or {}).get("strategies_agree", []),
                )
        except Exception:
            pass

        logger.info(
            f"[{symbol}] RAW SIGNAL: {result.side} conf={result.confidence:.0f}% "
            f"rr={result.risk_reward_tp1:.2f} chop={chop_score:.2f} "
            f"floor_pass={result.metadata.get('would_pass_confidence_floor')} "
            f"→ forwarding to LLM"
        )

        return result

    def evaluate_with_annotations(
        self, symbol: str, data: Dict[str, pd.DataFrame]
    ) -> Optional[AnnotatedSignal]:
        """Run ensemble evaluation with soft-filter annotations instead of hard rejections.

        Returns an AnnotatedSignal with filter assessments attached, or None if
        no strategies produced any signal at all (nothing to annotate).

        Filters converted to annotations:
        - Confidence floor (normal, chop, ranging)
        - Trend alignment rejection
        - Volume/chop gating

        Hard rejects (min_votes not met) still return None since there's no
        meaningful signal to annotate.
        """
        # Dynamic weight refresh
        self._refresh_dynamic_weights()

        # Set current eval symbol for regime-aware weight lookups
        self._current_eval_symbol = symbol

        signals: List[Signal] = []
        active_count = 0
        error_count = 0

        for strategy in self.strategies:
            if strategy.name in self._disabled_strategies:
                continue
            active_count += 1
            try:
                sig = strategy.evaluate(symbol, data)
                if sig is not None:
                    signals.append(sig)
            except Exception as e:
                error_count += 1
                logger.warning(f"[{symbol}] {strategy.name} error: {e}")

        signals = [deepcopy(s) for s in signals]
        self._last_signals[symbol] = {s.strategy: deepcopy(s) for s in signals}

        if not signals:
            return None

        # Regime-aware min_votes + degradation
        effective_min_votes = self._get_effective_min_votes(symbol)
        if error_count > 0 and active_count > 0:
            effective_min_votes = max(2, min(effective_min_votes, active_count - error_count))

        # Chop detection — attach scores but don't reject
        annotations: List[FilterAnnotation] = []
        chop_score = 0.0

        if self.chop_detector:
            is_chop, chop_score, chop_detail = self.chop_detector.is_choppy(symbol, data)
            for sig in signals:
                sig.metadata["chop_score"] = round(chop_score, 3)
            if is_chop:
                annotations.append(FilterAnnotation(
                    gate="chop_floor",
                    passed=False,
                    severity="warning" if chop_score < 0.65 else "reject",
                    value=round(chop_score, 3),
                    threshold=0.65,
                    detail=f"chop={chop_score:.2f}",
                ))
        else:
            # DECHOKE 1 (2026-07-02, GM_GATE_ROC_56K §3 — owner-approved):
            # volume_chop was the biggest rejector in the stack (59% of all
            # raw rejections; re-verified 32,596/32,596 rejects in
            # signal_outcomes.jsonl) while logging a HARDCODED value=0.0
            # against threshold 0.5 — unauditable from its own log, below
            # base-rate precision, and its unique rejects would have MADE
            # +36.6 bps/24h (n=1,620). Two fixes:
            #   1) INPUT REPAIR: log the real measured volume ratio.
            #   2) DE-GATE: severity drops to "warning" (advisory context for
            #      the LLM; no longer marks the signal soft-rejected). A
            #      [SHADOW-GATE] line accumulates would-reject data for a
            #      fresh dollar re-score per THE_STANDARD §2b.
            # Kill-switch: VOLUME_CHOP_ENFORCE=true restores hard "reject".
            _vol_ratio = self._volume_ratio(symbol, data)
            if _vol_ratio is not None and _vol_ratio < 0.5:
                import os as _os
                _vc_enforce = _os.environ.get(
                    "VOLUME_CHOP_ENFORCE", "false").lower() == "true"
                if not _vc_enforce:
                    logger.info(
                        f"[SHADOW-GATE] volume_chop would_reject {symbol} "
                        f"vol_ratio={_vol_ratio:.3f} < 0.5 (advisory only; "
                        f"VOLUME_CHOP_ENFORCE=true re-enables)"
                    )
                annotations.append(FilterAnnotation(
                    gate="volume_chop",
                    passed=False,
                    severity="reject" if _vc_enforce else "warning",
                    value=round(_vol_ratio, 3),
                    threshold=0.5,
                    detail=f"low volume (ratio={_vol_ratio:.2f})",
                ))

        # Run voting/merge — if min_votes not met, no signal to annotate
        if self.mode == "voting":
            result = self._voting(symbol, signals, effective_min_votes)
        elif self.mode == "weighted_veto":
            result = self._weighted_veto(symbol, signals, effective_min_votes)
        elif self.mode == "weighted":
            result = self._weighted(symbol, signals)
        elif self.mode == "best":
            result = self._best(symbol, signals)
        else:
            result = self._voting(symbol, signals, effective_min_votes)

        if result is None:
            # Not enough votes — nothing meaningful to annotate
            return None

        # Quality adjustment (same as evaluate())
        if self._quality_scorer is not None:
            try:
                import datetime as _dt
                from feedback.signal_quality import QualityFeatures
                _utc_h = _dt.datetime.now(_dt.timezone.utc).hour
                features = QualityFeatures(
                    confidence=result.confidence,
                    num_strategies_agree=result.metadata.get("num_agree", 1),
                    total_strategies=len(self.strategies),
                    symbol=symbol,
                    side=result.side,
                    regime=result.metadata.get("regime", ""),
                    entry_type=result.metadata.get("entry_type", ""),
                    hour_of_day=_utc_h,
                )
                _adj, _mult, _breakdown = self._quality_scorer.adjust_confidence(
                    result.confidence, features
                )
                _mult = max(0.5, min(1.3, _mult))
                if abs(_mult - 1.0) > 0.01:
                    result.confidence = max(0, min(100, result.confidence * _mult))
                    result.metadata["quality_multiplier"] = round(_mult, 3)
            except Exception as e:
                logger.debug(f"Quality scorer error: {e}")

        # ── Soft-annotated confidence floor (dynamic) ──
        _ann_regime = result.metadata.get("regime", "")
        effective_floor = _get_dynamic_floor(
            regime=_ann_regime, symbol=symbol, side=result.side, fallback=self.confidence_floor
        )
        result.metadata["dynamic_floor"] = round(effective_floor, 1)
        raw_chop = result.metadata.get("chop_score", 0)
        prev = self._smoothed_chop.get(symbol, raw_chop)
        smoothed_chop = self._chop_ema_alpha * raw_chop + (1 - self._chop_ema_alpha) * prev
        self._smoothed_chop[symbol] = smoothed_chop
        result.metadata["chop_score_smoothed"] = round(smoothed_chop, 3)

        if smoothed_chop > 0.35:
            if smoothed_chop >= 0.65:
                _vol_profile = getattr(self, '_volatility_profiles', {}).get(symbol, "medium")
                _max_chop_floor = {"low": 77.0, "medium": 77.0, "high": 77.0}.get(_vol_profile, 77.0)
                chop_intensity = min(1.0, (smoothed_chop - 0.65) / 0.20)
                effective_floor = effective_floor + chop_intensity * (
                    _max_chop_floor - effective_floor
                )
            else:
                chop_intensity = (smoothed_chop - 0.35) / 0.30
                effective_floor = effective_floor + chop_intensity * (
                    self.ranging_confidence_floor - self.confidence_floor
                )
            result.metadata["effective_confidence_floor"] = round(effective_floor, 1)

        conf_passed = result.confidence >= effective_floor
        annotations.append(FilterAnnotation(
            gate="confidence_floor",
            passed=conf_passed,
            severity="reject" if not conf_passed else "ok",
            value=round(result.confidence, 1),
            threshold=round(effective_floor, 1),
            detail=f"conf={result.confidence:.0f} vs floor={effective_floor:.0f} (chop={smoothed_chop:.2f})",
        ))

        # DECHOKE 2 (2026-07-02, GM_GATE_ROC_56K §3 — owner-approved):
        # trend_alignment annotation gate DELETED. Re-verified: 51,257 evals,
        # ZERO rejects in signal_outcomes.jsonl; value and threshold logged
        # 0.0 always — decorative sediment. The live LLM-first path rebuilds
        # from evaluate_raw(), which never calls _trend_alignment_adjust, so
        # dropping it here also makes this telemetry copy consistent with
        # the signal the LLM actually receives. (_trend_alignment_adjust
        # itself remains in evaluate() for the mechanical-fallback path.)

        # Build filter metadata from result
        filter_meta = dict(result.metadata) if result.metadata else {}
        filter_meta["num_strategies_signaled"] = len(signals)
        filter_meta["num_strategies_active"] = active_count

        return AnnotatedSignal(
            signal=result,
            annotations=annotations,
            hard_rejected=False,
            filter_metadata=filter_meta,
        )

    def _record_counterfactual(self, signal, skip_reason: str):
        """Record a rejected signal for counterfactual analysis (missed opportunity tracking)."""
        # Store for LLM brain visibility via signal digest
        self._last_rejections[signal.symbol] = {
            "reason": skip_reason,
            "side": signal.side,
            "confidence": round(signal.confidence, 1),
            "strategy": signal.strategy or "",
            "regime": signal.metadata.get("regime", "") or self._current_regime.get(signal.symbol, ""),
        }
        # MissedTradeTracker: comprehensive rejection feedback (backtest + live)
        if self._missed_trade_tracker is not None:
            try:
                self._missed_trade_tracker.record_rejection(
                    signal=signal,
                    reason=skip_reason,
                    gate="ensemble",
                )
            except Exception:
                pass
        try:
            from llm.brain_wiring import record_skipped_trade
            record_skipped_trade(
                symbol=signal.symbol,
                side=signal.side,
                entry_price=signal.entry,
                sl=signal.sl,
                tp1=signal.tp1,
                tp2=signal.tp2,
                confidence=signal.confidence,
                skip_reason=skip_reason,
                strategy=signal.strategy or "",
                regime=signal.metadata.get("regime", "") or self._current_regime.get(signal.symbol, ""),
            )
        except Exception:
            pass  # Non-critical — don't let tracking break trading

    def _record_veto_counterfactual(self, signal, veto_rule_ids):
        """Record a graduated-rule VETO with rule_ids stamped for accuracy tracking.

        Same rejection bookkeeping as _record_counterfactual (last_rejections digest +
        missed-trade tracker) but routes the counterfactual through the veto ledger so
        the blocked trade's outcome resolves against the exact rules that fired.
        """
        _regime = signal.metadata.get("regime", "") or self._current_regime.get(signal.symbol, "")
        self._last_rejections[signal.symbol] = {
            "reason": "graduated_rule_veto",
            "side": signal.side,
            "confidence": round(signal.confidence, 1),
            "strategy": signal.strategy or "",
            "regime": _regime,
        }
        if self._missed_trade_tracker is not None:
            try:
                self._missed_trade_tracker.record_rejection(
                    signal=signal, reason="graduated_rule_veto", gate="ensemble",
                )
            except Exception:
                pass
        try:
            from llm.brain_wiring import record_veto_counterfactual
            record_veto_counterfactual(
                symbol=signal.symbol,
                side=signal.side,
                entry_price=signal.entry,
                sl=signal.sl,
                tp1=signal.tp1,
                tp2=getattr(signal, "tp2", 0.0),
                confidence=signal.confidence,
                veto_rule_ids=veto_rule_ids,
                strategy=signal.strategy or "",
                regime=_regime,
            )
        except Exception:
            pass  # Non-critical — don't let tracking break trading

    def _volume_ratio(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Optional[float]:
        """Current 1h volume as a fraction of the 20-bar average.

        Returns None when it cannot be measured (missing/short data, zero avg).
        DECHOKE 1 input repair: this real measurement is what the volume_chop
        assessment logs — previously a hardcoded 0.0 was written (GM_GATE_ROC).
        """
        df_1h = data.get("1h")
        if df_1h is None or df_1h.empty or len(df_1h) < 20:
            return None  # can't determine
        vol = df_1h["volume"]
        avg_vol = float(vol.tail(20).mean())
        if avg_vol <= 0:
            return None
        return float(vol.iloc[-1]) / avg_vol

    def _is_low_volume(self, symbol: str, data: Dict[str, pd.DataFrame]) -> bool:
        """Check if current volume is too low for reliable signals.
        Returns True if volume < 50% of 20-bar average (choppy market)."""
        ratio = self._volume_ratio(symbol, data)
        if ratio is None:
            return False  # can't determine, allow trading
        if ratio < 0.5:
            logger.info(f"[{symbol}] Volume ratio {ratio:.2f} < 0.5 (low volume)")
            return True
        return False

    # Default timeframe weights: higher TFs matter more for trend determination.
    # 5m noise should NOT cancel out a confirmed daily trend.
    TIMEFRAME_WEIGHTS = {"5m": 0.5, "1h": 1.0, "6h": 1.5, "daily": 2.0}

    # Trade-duration-aware weights: short trades care about short TFs,
    # long trades care about long TFs. A daily bearish signal shouldn't
    # kill a clean 5m scalp setup.
    DURATION_WEIGHTS = {
        "SCALP":  {"5m": 2.0, "1h": 1.0, "6h": 0.3, "daily": 0.1},
        "MEDIUM": {"5m": 0.8, "1h": 1.5, "6h": 1.0, "daily": 0.5},
        "TREND":  {"5m": 0.3, "1h": 0.8, "6h": 1.5, "daily": 2.0},
        "REGIME": {"5m": 0.2, "1h": 0.5, "6h": 1.5, "daily": 2.0},
    }

    def _compute_trend_scores(self, symbol: str, data: Dict[str, pd.DataFrame],
                              entry_type: str = ""):
        """Compute weighted multi-timeframe trend scores.
        Returns (total_score, num_timeframes, detail_string).
        Score range varies by weight set.

        Each timeframe's raw score (±1) is multiplied by its weight.
        If entry_type is provided, uses duration-aware weights so that
        short trades prioritize short TFs and long trades prioritize long TFs.
        """
        # Use duration-aware weights if entry_type matches, else default
        tf_weights = self.DURATION_WEIGHTS.get(entry_type, self.TIMEFRAME_WEIGHTS)
        scores = []
        weights = []
        details = []

        # ── 5m: fast momentum (weight: 0.5) ──
        df_5m = data.get("5m")
        if df_5m is not None and not df_5m.empty and len(df_5m) >= 50:
            c = df_5m["close"].astype(float)
            e20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
            e50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
            s = 1 if e20 > e50 else -1
            scores.append(s)
            weights.append(tf_weights["5m"])
            details.append(f"5m={'B' if s > 0 else 'S'}")

        # ── 1h: core trend + MACD momentum (weight: 1.0) ──
        df_1h = data.get("1h")
        if df_1h is not None and not df_1h.empty and len(df_1h) >= 50:
            c = df_1h["close"].astype(float)
            e20 = c.ewm(span=20, adjust=False).mean()
            e50 = c.ewm(span=50, adjust=False).mean()
            ema_bull = float(e20.iloc[-1]) > float(e50.iloc[-1])

            # MACD direction (12/26/9)
            e12 = c.ewm(span=12, adjust=False).mean()
            e26 = c.ewm(span=26, adjust=False).mean()
            macd_line = e12 - e26
            macd_signal = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist = float((macd_line - macd_signal).iloc[-1])
            macd_bull = macd_hist > 0

            if ema_bull and macd_bull:
                s = 1
            elif not ema_bull and not macd_bull:
                s = -1
            else:
                s = 0
            scores.append(s)
            weights.append(tf_weights["1h"])
            details.append(f"1h={'B' if s > 0 else 'S' if s < 0 else 'N'}")

        # ── 6h: higher timeframe structure (weight: 1.5) ──
        df_6h = data.get("6h")
        if df_6h is not None and not df_6h.empty and len(df_6h) >= 20:
            c = df_6h["close"].astype(float)
            e20 = c.ewm(span=20, adjust=False).mean()
            e50 = c.ewm(span=50, min_periods=10, adjust=False).mean()
            price = float(c.iloc[-1])
            ema50_val = float(e50.iloc[-1])
            ema_bull = float(e20.iloc[-1]) > ema50_val
            price_above = price > ema50_val
            s = 1 if (ema_bull and price_above) else (-1 if (not ema_bull and not price_above) else 0)
            scores.append(s)
            weights.append(tf_weights["6h"])
            details.append(f"6h={'B' if s > 0 else 'S' if s < 0 else 'N'}")

        # ── Daily: macro trend + RSI (weight: 2.0) ──
        df_d = data.get("daily")
        if df_d is not None and not df_d.empty and len(df_d) >= 50:
            c = df_d["close"].astype(float)
            sma50 = float(c.rolling(50).mean().iloc[-1])
            price = float(c.iloc[-1])

            delta = c.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi = float((100 - 100 / (1 + rs)).iloc[-1])

            price_bull = price > sma50
            rsi_bull = rsi > 50
            if price_bull and rsi_bull:
                s = 1
            elif not price_bull and not rsi_bull:
                s = -1
            else:
                s = 0
            scores.append(s)
            weights.append(tf_weights["daily"])
            details.append(f"D={'B' if s > 0 else 'S' if s < 0 else 'N'}")

        # Weighted total: weights vary by trade duration (entry_type)
        total = sum(s * w for s, w in zip(scores, weights)) if scores else 0
        n = len(scores)
        detail_str = " ".join(details)
        return total, n, detail_str

    def _trend_alignment_adjust(
        self, symbol: str, data: Dict[str, pd.DataFrame], result: "Signal",
        entry_type: str = ""
    ) -> "Signal":
        """Multi-timeframe trend alignment: flip or boost signals.

        Uses duration-aware WEIGHTED scores so trade-relevant timeframes dominate.
        For SCALP: 5m (2.0) + 1h (1.0) dominate, daily (0.1) barely matters.
        For TREND: daily (2.0) + 6h (1.5) dominate, 5m (0.3) barely matters.
        Default (no entry_type): daily (2.0) dominates per original behavior.

        Strong trend (score >= 2.5):
          - Counter-trend → FLIP side, recalculate levels, +5 bonus
          - Aligned → +8 bonus
        Moderate trend (score >= 1.0):
          - Counter-trend → FLIP side, recalculate levels, +0 (neutral)
          - Aligned → +3 bonus
        Neutral (< 1.0): no adjustment
        """
        total, n, detail_str = self._compute_trend_scores(symbol, data, entry_type)

        if n == 0:
            return result

        side = result.side
        is_buy = side == "BUY"

        # Thresholds adjusted for weighted scoring (max ±5.0 instead of ±4)
        # Trend bonuses are MULTIPLICATIVE to prevent confidence inflation.
        # Strong alignment: 1.06x (70→74.2)  Mild alignment: 1.03x (70→72.1)
        if abs(total) >= 2.5:
            trend_bullish = total > 0
            if is_buy == trend_bullish:
                # Aligned with strong trend — multiplicative bonus
                old_conf = result.confidence
                result.confidence = min(100, result.confidence * 1.06)
                adj = round(result.confidence - old_conf, 1)
                result.metadata["trend_adjustment"] = adj
                logger.info(
                    f"[{symbol}] Strong trend aligned {side}: "
                    f"score={total:.1f}/{n} [{detail_str}] *1.06 (+{adj:.1f})"
                )
            else:
                # Strong counter-trend — FLIP the signal (returns new object)
                # No confidence bonus: flipped signals have zero original strategy
                # conviction in the new direction. Let them prove themselves.
                result = self._flip_signal(symbol, result, data)
                result.metadata["trend_adjustment"] = 0
                result.metadata["trend_flipped"] = True
                logger.info(
                    f"[{symbol}] FLIP {side}->{result.side}: strong trend "
                    f"score={total:.1f}/{n} [{detail_str}] -- sniper mode"
                )
        elif abs(total) >= 1.5:
            # Raised threshold from 1.0 to 1.5 — moderate trend
            trend_bullish = total > 0
            if is_buy == trend_bullish:
                # Mild alignment — small multiplicative bonus
                old_conf = result.confidence
                result.confidence = min(100, result.confidence * 1.03)
                adj = round(result.confidence - old_conf, 1)
                result.metadata["trend_adjustment"] = adj
                logger.info(
                    f"[{symbol}] Trend aligned {side}: "
                    f"score={total:.1f}/{n} [{detail_str}] *1.03 (+{adj:.1f})"
                )
            else:
                # Moderate counter-trend — penalize but don't reject.
                # Rejection kills valid shorts during bear market bounces.
                old_conf = result.confidence
                result.confidence = max(0, result.confidence * 0.90)  # 10% penalty
                adj = round(result.confidence - old_conf, 1)
                result.metadata["trend_adjustment"] = adj
                result.metadata["trend_counter"] = True
                logger.info(
                    f"[{symbol}] Counter-trend {side} penalized: moderate trend "
                    f"score={total:.1f}/{n} [{detail_str}] *0.90 ({adj:.1f})"
                )
        else:
            result.metadata["trend_adjustment"] = 0
            logger.info(f"[{symbol}] Neutral trend: score={total:.1f}/{n} [{detail_str}]")

        return result

    def _flip_signal(
        self, symbol: str, signal: "Signal", data: Dict[str, pd.DataFrame]
    ) -> "Signal":
        """Flip a signal's direction: BUY→SELL or SELL→BUY.
        Returns a NEW Signal object — never mutates the original.
        Uses asymmetric ATR multiples for minimum 1.5:1 R:R on TP1."""
        entry = signal.entry
        atr = signal.atr

        if atr <= 0:
            # Estimate ATR from 1h data if not available
            df_1h = data.get("1h")
            if df_1h is not None and not df_1h.empty and len(df_1h) >= 14:
                prev = df_1h["close"].shift(1)
                tr = pd.concat([
                    df_1h["high"] - df_1h["low"],
                    (df_1h["high"] - prev).abs(),
                    (df_1h["low"] - prev).abs(),
                ], axis=1).max(axis=1)
                atr = float(tr.rolling(14, min_periods=1).mean().iloc[-1])
            else:
                atr = entry * 0.02  # fallback: 2% of price

        # Asymmetric levels: SL tight (1.2 ATR), TP1 wide (2.4 ATR) = 2:1 R:R
        # This ensures flipped signals are worth taking after fees.
        if signal.side == "BUY":
            new_side = "SELL"
            sl = entry + 1.2 * atr
            tp1 = entry - 2.4 * atr
            tp2 = entry - 4.8 * atr
        else:
            new_side = "BUY"
            sl = entry - 1.2 * atr
            tp1 = entry + 2.4 * atr
            tp2 = entry + 4.8 * atr

        # Return a NEW Signal — never mutate the original (downstream may reference it)
        return replace(
            signal,
            side=new_side,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            atr=atr,
            metadata={**signal.metadata, "flipped_from": signal.side},
        )

    def _voting(self, symbol: str, signals: List[Signal],
                effective_min_votes: int = 0,
                llm_first_raw: bool = False) -> Optional[Signal]:
        """Require min_votes strategies to agree on direction.
        Opposition veto: if any strategy actively votes the opposite side,
        require min_votes + len(opposition) to override.

        llm_first_raw: when True, the negative-EV hard block inside
        _merge_signals() becomes advisory. The flag is threaded through to
        _merge_signals.
        """
        min_v = effective_min_votes or self.min_votes
        buy_signals = [s for s in signals if s.side == "BUY"]
        sell_signals = [s for s in signals if s.side == "SELL"]

        # Determine which side has enough base votes
        buy_enough = len(buy_signals) >= min_v
        sell_enough = len(sell_signals) >= min_v

        if buy_enough and sell_enough:
            # Both sides have min_votes - break tie using weighted confidence
            buy_w = self._weighted_confidence_sum(buy_signals)
            sell_w = self._weighted_confidence_sum(sell_signals)
            if buy_w > sell_w:
                chosen, opposition = buy_signals, sell_signals
            elif sell_w > buy_w:
                chosen, opposition = sell_signals, buy_signals
            else:
                return None  # tied
        elif buy_enough:
            chosen, opposition = buy_signals, sell_signals
        elif sell_enough:
            chosen, opposition = sell_signals, buy_signals
        else:
            return None

        # Opposition veto: if strategies actively disagree, raise the bar
        if opposition:
            required = min_v + len(opposition)
            if len(chosen) < required:
                logger.info(
                    f"[{symbol}] Signal vetoed: {len(chosen)} {chosen[0].side} vs "
                    f"{len(opposition)} {opposition[0].side} (need {required} votes)"
                )
                return None

        merged = self._merge_signals(symbol, chosen, llm_first_raw=llm_first_raw)
        if merged is None:
            return None

        # WAVE2A L3 (R22): full vote map incl. opposing votes (metadata only).
        merged.metadata["vote_map"] = {
            "chosen_side_votes": [
                {"strategy": s.strategy, "side": s.side,
                 "confidence": round(s.confidence, 1)}
                for s in chosen
            ],
            "opposing_side_votes": [
                {"strategy": s.strategy, "side": s.side,
                 "confidence": round(s.confidence, 1)}
                for s in opposition
            ],
        }

        # Confidence penalty for opposition, weighted by opposer's confidence.
        # Previously flat 10pts per opposer regardless of their conviction.
        if opposition:
            penalty = sum(s.confidence / 100 * 8 for s in opposition)
            merged.confidence = max(0, merged.confidence - penalty)
            merged.metadata["opposition_penalty"] = round(penalty, 1)
            logger.info(
                f"[{symbol}] Opposition penalty: -{penalty} confidence "
                f"(opposed by {[s.strategy for s in opposition]})"
            )

        return merged

    def _weighted_veto(self, symbol: str, signals: List[Signal],
                       effective_min_votes: int = 0,
                       llm_first_raw: bool = False) -> Optional[Signal]:
        """Weight-aware voting with graduated veto.
        Uses strategy accuracy weights * confidence to determine direction.
        Requires chosen side to have veto_ratio times the opposition's strength.
        Minimum min_votes strategies must agree on the same side for a trade.

        llm_first_raw: threaded through to _merge_signals, where it turns the
        negative-EV hard block into an advisory attachment.
        """
        min_v = effective_min_votes or self.min_votes
        buy_signals = [s for s in signals if s.side == "BUY"]
        sell_signals = [s for s in signals if s.side == "SELL"]

        # Require at least min_votes strategies agreeing on the same direction.
        # Exceptions for proven edge cases (data-validated):
        #   1. Proven solo strategies at high confidence (any symbol)
        #   2. Symbol+regime combos with validated edge (e.g., BTC ranging = 77% WR)
        # Solo analysis (from per-symbol missed trade data):
        # vmc_cipher: 82% solo WR, bollinger_squeeze: 78% solo WR (paper trading validated)
        # confidence_scorer: solo ONLY on HYPE (PF=2.65). Bad on BTC (PF=0.0) and SOL (PF=0.23).
        # 1,410-signal analysis (raw strategy output with price outcome tracking):
        #   bollinger_squeeze: 57% WR, +0.15%/trade — ONLY PROFITABLE STRATEGY
        #   Solo BB: 62% WR, +0.28%/trade — BEST single pattern in entire system
        #   probability_engine: 53% WR — breakeven
        #   confidence_scorer: 47% WR — slight loser (60% of all signal volume)
        #   regime_trend: 43% WR — losing
        #   mean_reversion: 43% WR — losing
        # Solo BB outperforms 2-agree+BB (62% vs 52% WR). Consensus DILUTES BB edge.
        _PROVEN_SOLO_STRATEGIES = {"bollinger_squeeze"}  # ONLY BB: 57% live WR, 64% shadow. probability_engine REMOVED: 0% primary WR
        _HYPE_SOLO_STRATEGIES = set()  # Disabled: confidence_scorer solo is coinflip (49% WR)
        # 60-day backtest: solo signals peak at 57-67% confidence. 70% threshold
        # blocks nearly all solo signals. 60% captures the bulk of the edge.
        # mean_reversion at 60%+ = 77% WR, probability_engine at 60%+ = 57% WR.
        _SOLO_CONF_THRESHOLD = 60.0
        # Symbol+regime combos where solo signals have validated edge
        # Only allow solo trades in trending regimes with high confidence
        # Ranging regime solo trades have been consistent losers (-$7 net from trade data)
        # TIGHTENED from live data: solo trades net -$12.27 EXCEPT SOL SHORT (+$47).
        # Only allow solos with PROVEN profitable combos.
        _SYMBOL_REGIME_SOLO = {
            ("BTC", "trending_bear"):  {"min_conf": 75.0, "risk_mult": 0.5},  # +$55 live
            # REMOVED: BTC/trend (-$5), BTC/trending_bull (unproven), SOL/trend (unproven), HYPE/trend (-$9)
        }

        if len(buy_signals) < min_v and len(sell_signals) < min_v:
            lone_signals = buy_signals or sell_signals
            _allowed = False

            # Path 1: Proven strategy solo — BB solo is the BEST pattern (62% WR)
            if (lone_signals and len(lone_signals) == 1
                    and lone_signals[0].strategy in _PROVEN_SOLO_STRATEGIES
                    and lone_signals[0].confidence >= _SOLO_CONF_THRESHOLD):
                _sig = lone_signals[0]
                _base_sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
                _setup_key = f"{_base_sym}_{_sig.side}"

                # 2026-06-05: Hardcoded "GOLDEN setups" and "DEAD SETUP" rules stripped per Nunu directive.
                # The 0.8 ETH_SELL / 0.7 BTC_BUY etc multipliers and the HYPE_SELL_BB "35% WR" auto-skip
                # were from a "1,410-signal analysis" under the fee bug — fabricated certainty.
                # All solo setups now get a neutral 0.5 risk multiplier; let LLM agents reason about
                # quality from current ENRICHED CONTEXT, not hardcoded per-symbol-side bias.
                _GOLDEN = {}  # emptied
                if False:  # was: HYPE SELL BB auto-skip — DISABLED
                    logger.info(f"[{symbol}] (legacy HYPE_SELL_BB block removed)")
                else:
                    _rm = _GOLDEN.get(_setup_key, 0.5)
                    _sig.metadata["solo_proven"] = True
                    _sig.metadata["risk_mult_override"] = _rm
                    _is_golden = _setup_key in _GOLDEN
                    logger.info(
                        f"[{symbol}] {'GOLDEN' if _is_golden else 'Proven'} solo: "
                        f"{_sig.strategy} {_setup_key} conf={_sig.confidence:.0f}% "
                        f"({_rm}x size)"
                    )
                    _allowed = True

            # Path 1b: HYPE-specific solo strategies (confidence_scorer PF=2.65 on HYPE only)
            if (not _allowed and lone_signals and len(lone_signals) == 1
                    and lone_signals[0].strategy in _HYPE_SOLO_STRATEGIES
                    and symbol.startswith("HYPE")
                    and lone_signals[0].confidence >= _SOLO_CONF_THRESHOLD):
                lone_signals[0].metadata["solo_proven"] = True
                lone_signals[0].metadata["hype_solo"] = True
                lone_signals[0].metadata["risk_mult_override"] = 0.4  # Slightly more cautious than general solo
                logger.info(
                    f"[{symbol}] HYPE solo trade: {lone_signals[0].strategy} "
                    f"conf={lone_signals[0].confidence:.0f}% (0.4x size)"
                )
                _allowed = True

            # Path 1c: Regime momentum solo — when regime is strongly directional
            # and the solo signal aligns with regime direction, allow at half size.
            # Counterfactual: 100% of SELL signals in regime -2 were correct on 2026-04-01.
            if not _allowed and lone_signals and len(lone_signals) == 1:
                _regime = self._current_regime.get(symbol, "unknown")
                _regime_4h = self._current_regime_4h.get(symbol)
                _sig = lone_signals[0]
                # Directional regime + aligned signal → allow solo at half size
                # Live data Apr 6: regime classified as trending_bull let BUYs through
                # but SELL signals blocked because illiquid/ranging weren't in bear set.
                # Added illiquid (crypto illiquid = drift down) and ranging to bear set
                # so SELL signals get the same bypass opportunity as BUY signals.
                # ONLY trending_bear for SELL solos — live data: +$406, 75% WR
                # REMOVED: illiquid ($0 edge), ranging (-$35 edge) — both losers for solos
                _strong_regimes_bear = {"trending_bear"}
                _strong_regimes_bull = {"trending_bull"}
                _regime_aligned = (
                    (_sig.side == "SELL" and _regime in _strong_regimes_bear) or
                    (_sig.side == "BUY" and _regime in _strong_regimes_bull)
                )
                if _regime_aligned and _sig.confidence >= 65.0:
                    _sig.metadata["regime_momentum_solo"] = True
                    _sig.metadata["risk_mult_override"] = 0.5
                    logger.info(
                        f"[{symbol}] Regime momentum solo: {_sig.strategy} {_sig.side} "
                        f"regime={_regime} conf={_sig.confidence:.0f}% (0.5x size)"
                    )
                    _allowed = True

            # Path 2: Symbol+regime edge (solo signals in validated combos)
            if not _allowed and lone_signals:
                _regime = self._current_regime.get(symbol, "unknown")
                _base_sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
                _combo = (_base_sym, _regime)
                _edge = _SYMBOL_REGIME_SOLO.get(_combo)
                if _edge and lone_signals[0].confidence >= _edge["min_conf"]:
                    lone_signals[0].metadata["symbol_regime_solo"] = True
                    lone_signals[0].metadata["risk_mult_override"] = _edge["risk_mult"]
                    logger.info(
                        f"[{symbol}] Symbol+regime solo: {_base_sym}/{_regime} "
                        f"conf={lone_signals[0].confidence:.0f}% (risk_mult={_edge['risk_mult']})"
                    )
                    _allowed = True

            if not _allowed:
                if buy_signals:
                    logger.info(f"[{symbol}] Only {len(buy_signals)} BUY signal(s), need {min_v}+ same-side")
                elif sell_signals:
                    logger.info(f"[{symbol}] Only {len(sell_signals)} SELL signal(s), need {min_v}+ same-side")
                if self._missed_trade_tracker is not None:
                    self._missed_trade_tracker.record_ensemble_rejection(
                        symbol=symbol, signals=signals, reason="insufficient_votes"
                    )
                # Sniper hook: route single-strategy signals to LLM for evaluation.
                if self._sniper_callback is not None:
                    _lone = buy_signals or sell_signals
                    if _lone:
                        try:
                            self._sniper_callback(_lone[0], symbol)
                        except Exception as _se:
                            logger.debug(f"[{symbol}] Sniper callback error: {_se}")
                # Manual sniper hook: route solo signals for tracking/sim
                # The manual sniper has its own proven-setup gates (HYPE BUY, SOL SELL)
                # and can profitably trade signals the ensemble rejects for low consensus
                if hasattr(self, '_manual_sniper_callback') and self._manual_sniper_callback is not None:
                    _lone = buy_signals or sell_signals
                    if _lone:
                        try:
                            self._manual_sniper_callback(_lone[0])
                        except Exception:
                            pass
                return None

        # Redundant strategy clusters: strategies using the same core indicators
        # (MACD + BB + RSI) should not count as independent votes.
        # When ONLY a redundant cluster agrees, apply heavy penalty (effectively
        # requires 85%+ raw confidence to pass the 72% floor). This lets high-conviction
        # signals through while filtering the noise majority.
        # When a 3rd+ strategy also agrees, apply moderate penalty.
        _REDUNDANT_CLUSTERS = {
            frozenset({"bollinger_squeeze", "confidence_scorer"}): 0.95,  # Reduced from 0.85 (15%→5%). BB+CS 2-agree is proven profitable — 15% penalty was blocking valid signals below the floor.
            # confidence_scorer + vmc_cipher: REMOVED. vmc_cipher has independent oscillator logic (82% solo WR).
        }
        for side_signals in [buy_signals, sell_signals]:
            signal_names = frozenset(s.strategy for s in side_signals)
            for cluster, solo_penalty_mult in _REDUNDANT_CLUSTERS.items():
                if cluster.issubset(signal_names):
                    if signal_names == cluster:
                        # ONLY the redundant pair voted — per-cluster penalty
                        for s in side_signals:
                            s.confidence *= solo_penalty_mult
                        _pct = int((1 - solo_penalty_mult) * 100)
                        logger.info(
                            f"[{symbol}] Redundant-only cluster {sorted(cluster)}: "
                            f"confidence penalized {_pct}%"
                        )
                    else:
                        # 3rd+ strategy also agrees — light penalty
                        for s in side_signals:
                            if s.strategy in cluster:
                                s.confidence *= 0.93
                        logger.info(
                            f"[{symbol}] Redundant cluster {sorted(cluster)} + "
                            f"{sorted(signal_names - cluster)}: confidence penalized 7%"
                        )
                    break

        # Block known-losing combos (backtest-validated toxic combinations).
        # Only block when 3+ strategies agree and the toxic pair is a subset —
        # if the toxic pair are the ONLY voters, blocking guarantees zero trades.
        # NOTE: HYPE BUY exemption removed (F8, 2026-05-04): counterfactual 89% WR
        # was contradicted by 35 live trades showing 23% WR, -$77.26. HYPE BUY is
        # now vetoed at Gate 1g (graduated_rules.json: hype_long_veto_v1).
        _LOSING_COMBOS = {
            # CS+MTQ REMOVED from global blacklist: +$55 on BTC/ETH (60% WR, best combo).
            # Original PF 0.08 was from HYPE data only. LLM can judge per-trade.
            frozenset({"regime_trend", "vmc_cipher"}),                          # PF 0.39, 29% WR — consistently losing
            frozenset({"probability_engine", "regime_trend"}),                  # PF 0.0, 0% WR in multiple runs
        }
        _base_sym_lc = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
        for side_signals in [buy_signals, sell_signals]:
            if len(side_signals) >= 2:
                signal_names = frozenset(s.strategy for s in side_signals)
                for blocked in _LOSING_COMBOS:
                    # Block toxic combos only when they're a subset of a larger group.
                    # When the toxic pair are the ONLY voters, let them through to the
                    # EV gate which will properly evaluate. Blocking exact-match pairs
                    # guarantees zero trades in consolidation where only 2 strategies fire.
                    if blocked.issubset(signal_names) and signal_names != blocked:
                        logger.info(
                            f"[{symbol}] Blocked losing combo {sorted(blocked)} "
                            f"in {sorted(signal_names)}"
                        )
                        if self._missed_trade_tracker is not None:
                            self._missed_trade_tracker.record_ensemble_rejection(
                                symbol=symbol, signals=signals, reason="losing_combo"
                            )
                        return None

        buy_strength = self._weighted_confidence_sum(buy_signals) if buy_signals else 0
        sell_strength = self._weighted_confidence_sum(sell_signals) if sell_signals else 0

        if buy_strength > sell_strength and buy_signals:
            chosen, opposition = buy_signals, sell_signals
            chosen_strength, oppose_strength = buy_strength, sell_strength
        elif sell_strength > buy_strength and sell_signals:
            chosen, opposition = sell_signals, buy_signals
            chosen_strength, oppose_strength = sell_strength, buy_strength
        else:
            return None  # tied or empty

        # Soft veto: instead of hard-blocking when opposition is strong,
        # reduce position size. Data: hard veto was 29% accurate (vetoed 37 winners
        # vs 15 losers). Convert to size reduction instead of rejection.
        if opposition and chosen_strength < oppose_strength * self.veto_ratio:
            # Mark for size reduction instead of blocking
            _veto_severity = oppose_strength / max(chosen_strength, 0.01)
            _size_penalty = max(0.3, 1.0 - (_veto_severity - 1.0) * 0.5)  # 0.3-1.0x
            for s in chosen:
                s.metadata["opposition_size_reduction"] = round(_size_penalty, 2)
                s.metadata.setdefault("risk_mult_override", 1.0)
                s.metadata["risk_mult_override"] *= _size_penalty
            logger.info(
                f"[{symbol}] Soft veto: {chosen[0].side} strength={chosen_strength:.1f} "
                f"< {opposition[0].side} {oppose_strength:.1f} × {self.veto_ratio} "
                f"— size reduced to {_size_penalty:.0%} (not blocked)"
            )

        merged = self._merge_signals(symbol, chosen, llm_first_raw=llm_first_raw)
        if merged is None:
            return None

        # WAVE2A L3 (FULL_PIPE_BUILD_MAP R22, 2026-07-02): full-information
        # symmetry — expose the complete vote map INCLUDING the losing side so
        # the LLM sees genuine disagreement instead of a pre-resolved winner.
        # Consensus math unchanged; this is metadata only.
        merged.metadata["vote_map"] = {
            "chosen_side_votes": [
                {"strategy": s.strategy, "side": s.side,
                 "confidence": round(s.confidence, 1)}
                for s in chosen
            ],
            "opposing_side_votes": [
                {"strategy": s.strategy, "side": s.side,
                 "confidence": round(s.confidence, 1)}
                for s in opposition
            ],
            "chosen_strength_weighted": round(chosen_strength, 2),
            "opposing_strength_weighted": round(oppose_strength, 2),
        }

        # Symbol+side directional bias OBSERVATION (from counterfactual analysis, 20,664 records).
        # HYPE SELL: 2.3% WR — systemically unprofitable. BTC BUY: 15% WR.
        # TESTED as confidence penalty but hurt backtest PnL at 5pts and 10pts.
        # Kept as metadata logging only — the backtest data window may not match
        # the counterfactual data window. Monitor in paper trading.
        _base_sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
        _DIRECTIONAL_BIAS = {
            ("HYPE", "SELL"): "low_wr",   # 2.3% WR in counterfactuals
            ("BTC", "BUY"):   "low_wr",   # 15.0% WR in counterfactuals
        }
        _dir_bias = _DIRECTIONAL_BIAS.get((_base_sym, merged.side))
        if _dir_bias:
            merged.metadata["directional_bias_warning"] = _dir_bias
            logger.info(
                f"[{symbol}] Directional bias warning: {_base_sym} {merged.side} "
                f"has {_dir_bias} in counterfactual data (observation only, no penalty)"
            )

        # Duration-aware opposition penalty: daily strategies penalize
        # intraday signals less (different timeframe = weaker opposition).
        # Also cap per-strategy weight to prevent one bad strategy from
        # dominating the penalty.
        if opposition:
            # Infer the chosen side's dominant timeframe from the strongest signal
            chosen_tf = self.STRATEGY_TIMEFRAME.get(
                max(chosen, key=lambda s: s.confidence).strategy, "swing"
            )
            # Opposition penalty proportional to how close the veto was to firing.
            # A signal that barely passed the veto gets a bigger penalty than one
            # that dominated. The old 15x arbitrary multiplier was crushing
            # legitimate signals by 10-30 points regardless of veto margin.
            if oppose_strength > 0:
                safety_margin = chosen_strength / (oppose_strength * self.veto_ratio) - 1.0
                safety_margin = max(0.0, min(safety_margin, 1.0))  # Clamp 0-1
            else:
                safety_margin = 1.0  # No opposition strength = no penalty
            penalty_intensity = max(0.0, 1.0 - safety_margin)  # 0 = safe, 1 = barely passed

            penalty = 0.0
            for s in opposition:
                raw_weight = self._get_strategy_weight(s.strategy)
                capped_weight = min(raw_weight, self.MAX_OPPOSITION_WEIGHT)
                # Duration mismatch discount: daily opposing intraday = 40% penalty
                opp_tf = self.STRATEGY_TIMEFRAME.get(s.strategy, "swing")
                if chosen_tf != opp_tf:
                    tf_discount = 0.4  # Cross-timeframe = much weaker opposition
                else:
                    tf_discount = 1.0  # Same timeframe = full penalty
                # Scale by opposition strength: weak opposition (<55% confidence) = minimal penalty
                opp_strength_scale = s.confidence / 100.0
                if opp_strength_scale < 0.55:
                    opp_strength_scale *= 0.3  # Weak opposition: 70% penalty reduction
                penalty += capped_weight * 5 * (s.confidence / 100) * tf_discount * penalty_intensity * min(1.0, opp_strength_scale / 0.55)
            # Cap opposition penalty at 3 points. Data: 31% veto accuracy means
            # opposition is WRONG 69% of the time. Large penalties destroy good trades.
            penalty = min(penalty, 3.0)
            merged.confidence = max(0, merged.confidence - penalty)
            merged.metadata["opposition_penalty"] = round(penalty, 1)
            opp_names = [s.strategy for s in opposition]
            logger.info(
                f"[{symbol}] {chosen[0].side} passes weighted veto "
                f"({chosen_strength:.1f} vs {oppose_strength:.1f}), "
                f"penalty -{penalty:.1f} from {opp_names}"
            )

        # ── BB+MTQ Contra-Indicator (from 2,172-signal analysis) ──
        # When BB and MTQ agree: 35% WR (WORSE than either alone).
        # MTQ confirmation is noise that dilutes BB's edge.
        if merged and merged.metadata:
            _strats = set(merged.metadata.get("strategies_agree", []))
            if "bollinger_squeeze" in _strats and "multi_tier_quality" in _strats:
                merged.metadata["bb_mtq_contra"] = True
                merged.metadata["risk_mult_override"] = min(
                    merged.metadata.get("risk_mult_override", 1.0), 0.5
                )
                logger.info(
                    f"[{symbol}] BB+MTQ CONTRA: both agree = 35% WR. "
                    f"Reducing size to 0.5x"
                )

        return merged

    def _weighted(self, symbol: str, signals: List[Signal]) -> Optional[Signal]:
        """Weight strategies by performance and combine."""
        buy_signals = [s for s in signals if s.side == "BUY"]
        sell_signals = [s for s in signals if s.side == "SELL"]

        buy_weight = sum(self.weights.get(s.strategy, 1.0) * s.confidence for s in buy_signals)
        sell_weight = sum(self.weights.get(s.strategy, 1.0) * s.confidence for s in sell_signals)

        if buy_weight > sell_weight and len(buy_signals) >= 1:
            chosen = buy_signals
        elif sell_weight > buy_weight and len(sell_signals) >= 1:
            chosen = sell_signals
        else:
            return None

        return self._merge_signals(symbol, chosen)

    def _best(self, symbol: str, signals: List[Signal]) -> Optional[Signal]:
        """Take the single highest-confidence signal."""
        best = max(signals, key=lambda s: s.confidence)
        return best

    # Static base weight multipliers: demote strategies with poor backtest PF.
    # Applied BEFORE regime/IC adjustments. 1.0 = no change, 0.5 = half weight.
    STRATEGY_BASE_MULTIPLIER = {
        "regime_trend": 0.5,  # PF=0.95 in 30d backtest — marginally losing
    }

    def _get_strategy_weight(self, strategy_name: str) -> float:
        """Get weight for a strategy from weight manager, falling back to static weights.
        Applies static base multipliers, regime-aware multipliers, IC tracker penalties,
        and daily-TF caps. Caps daily-timeframe strategies (monte_carlo_zones) at
        MAX_OPPOSITION_WEIGHT to prevent a single high-timeframe strategy from dominating voting."""
        if self.weight_manager is not None:
            w = self.weight_manager.get_weight(strategy_name, symbol=self._current_eval_symbol or "")
        else:
            w = self.weights.get(strategy_name, 1.0)
        # Static base multiplier: demote strategies with poor backtest performance
        base_mult = self.STRATEGY_BASE_MULTIPLIER.get(strategy_name, 1.0)
        if base_mult != 1.0:
            w *= base_mult
        # Regime-aware weight adjustment: multiply by regime-specific factor
        # (e.g., 1.3x for bollinger_squeeze in high_volatility, 0.7x for mean_reversion in trend)
        if self._regime_strategy_weighter is not None and self._current_eval_symbol is not None:
            regime = self._current_regime.get(self._current_eval_symbol, "unknown")
            regime_mult = self._regime_strategy_weighter.get_regime_multiplier(regime, strategy_name)
            if regime_mult != 1.0:
                logger.debug(
                    f"[REGIME_WEIGHT] {strategy_name} weight {w:.3f} * {regime_mult:.2f}x "
                    f"(regime={regime}) = {w * regime_mult:.3f}"
                )
                w *= regime_mult
        # IC tracker: penalize inverted/decaying factors (0.0 = inverted, 0.5 = unknown, 1.0 = healthy)
        if self.ic_tracker is not None:
            try:
                ic_weight = self.ic_tracker.get_ic_weight(strategy_name)
                if ic_weight < 1.0:
                    logger.debug(
                        f"[IC] {strategy_name} weight adjusted by IC: {w:.3f} * {ic_weight:.2f} = {w * ic_weight:.3f}"
                    )
                w *= ic_weight
            except Exception:
                pass  # IC tracker error shouldn't break voting
        # Cap daily-TF strategies so they can't overpower intraday consensus
        if self.STRATEGY_TIMEFRAME.get(strategy_name) == "daily":
            w = min(w, self.MAX_OPPOSITION_WEIGHT)
        return w

    def _weighted_confidence_sum(self, signals: List[Signal]) -> float:
        """Compute sum of weight * confidence for a list of signals."""
        return sum(self._get_strategy_weight(s.strategy) * s.confidence for s in signals)

    def _merge_signals(self, symbol: str, signals: List[Signal],
                        llm_first_raw: bool = False) -> Signal:
        """Merge multiple agreeing signals into one consensus signal.
        Uses strategy accuracy weights for weighted-average confidence."""
        side = signals[0].side

        # Weighted average confidence using strategy accuracy weights
        total_weight = sum(self._get_strategy_weight(s.strategy) for s in signals)
        if total_weight > 0:
            weighted_conf = sum(
                self._get_strategy_weight(s.strategy) * s.confidence for s in signals
            ) / total_weight
        else:
            weighted_conf = sum(s.confidence for s in signals) / len(signals)

        # 2026-06-08: hardcoded _COMBO_EDGE dict removed. The 1.06x-1.12x boosts
        # were calibrated to 90d backtest PFs ("PF=4+ in 90d") that are stale.
        # The combination of strategies firing IS still useful information, but
        # the boost should come from CURRENT live edge data, not frozen multipliers.
        # We surface the combo as DATA so the LLM agents can weigh it themselves.
        signal_names = frozenset(s.strategy for s in signals)
        if hasattr(self, '_combo_log_metadata'):
            self._combo_log_metadata = {}
        # Note the combo composition for LLM context (no auto-multiplier applied).
        if len(signal_names) >= 2:
            logger.info(f"[{symbol}] Strategy combo firing: {sorted(signal_names)} — surfaced to LLM as data, no hardcoded boost")

        # Consensus bonus: reward genuine INDEPENDENT multi-strategy agreement.
        # 90d backtest: "strong_confluence" (4+ agree) = 0% WR because redundant
        # oscillator strategies all fire together at exhaustion points.
        # Solution: count INDEPENDENT votes, not total votes.
        n_agree = len(signals)
        _regime = self._current_regime.get(symbol, "unknown")

        # Count independent votes (strategies in different methodology groups)
        # Each strategy uses genuinely different methodology:
        #   confidence_scorer = multi-factor (ADX+MACD+squeeze+momentum)
        #   vmc_cipher = oscillator (wave trend + MFI)
        #   regime_trend = trend-following (multi-TF alignment)
        #   multi_tier_quality = multi-timeframe (5m+1h quality)
        #   bollinger_squeeze = volatility (BB/KC compression)
        #   probability_engine = statistical (Monte Carlo simulation)
        #   mean_reversion = mean-reversion (z-score deviation)
        # Previously confidence_scorer was grouped with vmc_cipher as "oscillator"
        # but confidence_scorer uses ADX+squeeze+momentum (multi-factor), not wave
        # trend oscillator. This grouping error caused n_independent=1 when both
        # fired, under-counting true independence.
        _METHODOLOGY_GROUPS = {
            "multi_factor": {"confidence_scorer"},  # ADX+MACD+squeeze+momentum
            "oscillator": {"vmc_cipher"},  # Wave trend + MFI oscillator
            "volatility": {"bollinger_squeeze"},  # BB/KC compression
            "probability": {"probability_engine"},  # Monte Carlo
            "trend_following": {"regime_trend"},  # Multi-timeframe trend alignment
            "zones": {"monte_carlo_zones"},  # Statistical zones
            "derivatives": {"funding_rate", "oi_delta", "liquidation_cascade"},
            "multi_tier": {"multi_tier_quality"},  # 5m+1h quality
            "lead_lag": {"lead_lag"},  # Cross-asset
            "mean_reversion": {"mean_reversion"},  # Z-score mean reversion
        }
        _groups_present = set()
        for s in signals:
            _found_group = False
            for group, members in _METHODOLOGY_GROUPS.items():
                if s.strategy in members:
                    _groups_present.add(group)
                    _found_group = True
                    break
            if not _found_group:
                _groups_present.add(f"_unknown_{s.strategy}")  # Unknown = independent
        n_independent = len(_groups_present)

        # 2026-06-08: hardcoded _CONSENSUS_MULT regime×N-agree dict removed.
        # The 1.20x trending_bull 4-agree boost, 0.92x redundant penalty etc.
        # were calibrated to historical data and applied automatically. Now
        # we surface consensus + independence as DATA. LLM weighs it with
        # current regime + alpha-ops context.
        consensus_mult = 1.0  # neutral — LLM decides if consensus deserves a boost
        if n_agree >= 4 and n_independent <= 2:
            logger.info(
                f"[{symbol}] Redundant 4+ agree: {n_agree} strategies, {n_independent} "
                f"independent groups — surfaced to LLM (no auto-penalty)"
            )
        # Cap ensemble confidence — raised to 92% so genuine unanimous signals pass
        try:
            from trading_config import TradingConfig
            max_conf = TradingConfig().max_ensemble_confidence
        except Exception:
            max_conf = 85.0
        if max_conf < 85.0:
            logger.warning(f"[ENSEMBLE] MAX_ENSEMBLE_CONFIDENCE={max_conf} is very low, may suppress valid signals")
        # Respect user's configured cap (don't silently override to 92)
        combined_conf = min(max_conf, weighted_conf * consensus_mult)

        # Weighted-average SL (preserves R:R), average TP1 (balanced), widest TP2 (aggressive).
        # Old policy: "widest SL" destroyed R:R when strategies disagreed on stops.
        # New: weight SL by strategy accuracy, so trusted strategies get more say.
        # Average TP1 prevents zone-based strategies from pulling targets too close.
        # Consistent accuracy-weighted averaging for SL, entry, TP1, ATR.
        # Using the same weighting for all levels preserves R:R geometry.
        # TP2 stays aggressive (widest) since it's the trailing target.
        if total_weight > 0:
            weighted_sl = sum(
                self._get_strategy_weight(s.strategy) * s.sl for s in signals
            ) / total_weight
            weighted_entry = sum(
                self._get_strategy_weight(s.strategy) * s.entry for s in signals
            ) / total_weight
            weighted_tp1 = sum(
                self._get_strategy_weight(s.strategy) * s.tp1 for s in signals
            ) / total_weight
            weighted_atr = sum(
                self._get_strategy_weight(s.strategy) * s.atr for s in signals
            ) / total_weight
        else:
            weighted_sl = sum(s.sl for s in signals) / len(signals)
            weighted_entry = sum(s.entry for s in signals) / len(signals)
            weighted_tp1 = sum(s.tp1 for s in signals) / len(signals)
            weighted_atr = sum(s.atr for s in signals) / len(signals)
        # Bear-market shorts: widen SL to survive bounce wicks.
        # 1.5x ATR is too tight for SELL in volatile bear markets — bounces
        # trigger stops before the move continues. Widen by 30% in trending regimes.
        regime = self._current_regime.get(symbol, "unknown")
        if side == "SELL" and regime == "trend":
            sl_widen = 1.3  # 30% wider stop for bear-market shorts
            old_sl = weighted_sl
            # For SELL, SL is above entry — widen means push it higher
            sl_dist = abs(weighted_sl - weighted_entry)
            weighted_sl = weighted_entry + sl_dist * sl_widen
            # Proportionally widen TP to maintain R:R geometry
            weighted_tp1 = weighted_entry - abs(weighted_entry - weighted_tp1) * sl_widen
            logger.info(
                f"[ENSEMBLE] {symbol} SELL SL widened {sl_widen}x for bear regime: "
                f"SL {old_sl:.2f} → {weighted_sl:.2f}"
            )

        if side == "BUY":
            best_sl = weighted_sl
            best_tp1 = weighted_tp1
            best_tp2 = max(s.tp2 for s in signals)
            entry = weighted_entry
        else:
            best_sl = weighted_sl
            best_tp1 = weighted_tp1
            best_tp2 = min(s.tp2 for s in signals)
            entry = weighted_entry

        atr = weighted_atr

        # Per-symbol SL/TP adjustment: high-volatility assets need wider stops.
        # HYPE has 2x BTC volatility and mean-reverts — wider SL lets trades survive initial vol.
        try:
            from trading_config import DEFAULT_SYMBOL_OVERRIDES
            _base = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
            _sym_ov = DEFAULT_SYMBOL_OVERRIDES.get(_base)
            if _sym_ov and _sym_ov.atr_mult_sl and atr > 0:
                # Widen SL to match per-symbol ATR mult (default strategies use ~1.5x)
                _default_mult = 1.5
                _target_mult = _sym_ov.atr_mult_sl
                if _target_mult > _default_mult:
                    _widen = _target_mult / _default_mult
                    if side == "BUY":
                        best_sl = entry - abs(entry - best_sl) * _widen
                    else:
                        best_sl = entry + abs(entry - best_sl) * _widen
                    logger.info(
                        f"[ENSEMBLE] {symbol} SL widened {_widen:.2f}x for {_base} "
                        f"(atr_mult={_target_mult})"
                    )
            if _sym_ov and _sym_ov.atr_mult_tp1 and atr > 0:
                _default_tp = 2.0
                _target_tp = _sym_ov.atr_mult_tp1
                if _target_tp < _default_tp:
                    _tighten = _target_tp / _default_tp
                    if side == "BUY":
                        best_tp1 = entry + abs(entry - best_tp1) * _tighten
                    else:
                        best_tp1 = entry - abs(entry - best_tp1) * _tighten
        except Exception:
            pass

        # ── R:R Floor Enforcement ──────────────────────────────────────
        # After all per-symbol SL/TP adjustments, enforce minimum R:R.
        # Prevents symbol overrides (wider SL + tighter TP) from creating
        # sub-1.0 R:R signals that always fail the EV check.
        try:
            from trading_config import TradingConfig as _TCfg
            _min_rr = _TCfg().min_rr_tp1
        except Exception:
            _min_rr = 1.5
        _current_stop = abs(entry - best_sl)
        if _current_stop > 0:
            _current_rr = abs(entry - best_tp1) / _current_stop
            if _current_rr < _min_rr:
                _required_tp_dist = _current_stop * _min_rr
                _old_tp1 = best_tp1
                if side == "BUY":
                    best_tp1 = entry + _required_tp_dist
                else:
                    best_tp1 = entry - _required_tp_dist
                logger.info(
                    f"[ENSEMBLE] {symbol} {side} TP1 widened for R:R floor: "
                    f"R:R {_current_rr:.2f} -> {_min_rr:.1f}, "
                    f"TP1 {_old_tp1:.2f} -> {best_tp1:.2f}"
                )

        # Preserve per-signal ATR and SL for profile classification
        per_signal_atr = {s.strategy: s.atr for s in signals}
        per_signal_sl = {s.strategy: s.sl for s in signals}
        per_signal_tp1 = {s.strategy: s.tp1 for s in signals}

        # Fee-aware Expected Value per $1 risked:
        #   EV = win_prob × (R:R - fee_drag) - loss_prob × (1.0 + fee_drag)
        # Fee drag = round-trip fees as fraction of stop width.
        # A 1.5 R:R trade with 10% fee drag: win nets 1.4R, loss costs 1.1R.
        #
        # CRITICAL: confidence ≠ win probability. 70% confidence historically
        # produces ~45% WR (overconfident). Apply conservative deflator to
        # prevent EV overestimation from uncalibrated confidence scores.
        # Deflator: assume confidence is ~1.4x actual win rate (empirical).
        # This makes EV a LOWER BOUND rather than an optimistic estimate.
        stop_width = abs(entry - best_sl)
        rr_tp1 = abs(entry - best_tp1) / stop_width if stop_width > 0 else 0
        rr_tp2 = abs(entry - best_tp2) / stop_width if stop_width > 0 else 0
        raw_win_prob = combined_conf / 100.0
        # 2026-06-08: hardcoded _WP_DEFLATION matrix removed (24 grid values).
        # The deflation values (e.g. 1-strategy / panic = 0.35x) were calibrated
        # to specific historical regimes. Applying them mechanically meant the
        # EV calculation downstream was systematically pessimistic for solo
        # signals in non-trending regimes — which silently rejected many trades
        # via the negative-EV gate.
        # Now: minimal deflation (0.90x) as a small caution-toward-realism
        # haircut. The honest WP from quant_brain (already live-calibrated)
        # is more accurate than these stale multipliers. LLM decides downstream.
        _regime_ev = self._current_regime.get(symbol, "unknown")
        _indep_key = min(n_independent, 4)
        # Tiny universal deflation acknowledging confidence > WR mismatch in noise.
        # Stronger consensus (more independent groups) deflates slightly less.
        _deflation = {4: 0.95, 3: 0.93, 2: 0.90, 1: 0.88}.get(_indep_key, 0.90)

        # Setup-specific edges from shadow ledger analysis (2026-04-15).
        # Finding 11 rewrite: the old `_PROVEN_SETUP_FLOOR` collapsed the
        # strategy dimension (symbol, side) → deflation. Reality: the same
        # (symbol, side) has opposite edges depending on which strategy
        # generated it. Example: SOL SELL via multi_tier_quality = 72% WR,
        # SOL SELL via regime_trend = 0% WR on 149 samples. Treating them
        # the same is a category error.
        #
        # Rebuilt from bot/data/shadow_ledger.csv (3,802 resolved entries) on
        # 2026-04-15. Cutoff: n >= 40 samples for KEEP/BLOCK, 20 for NEUTRAL.
        # Table format: (symbol, side, strategy) -> deflation floor.
        _base_sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
        # 2026-05-30 architectural decision:
        #   - BLOCKS (hard kill switches based on stale data) — REMOVED, they interfered with live regimes
        #   - EDGES (positive-WR setups from 3,802 resolved trades, 2026-04-15) — KEPT, this is real alpha
        # Phase 2 (later): convert EDGES from confidence-floor multiplier to metadata that flows to the LLM,
        #   so the LLM sees "this setup had 100% WR on 135 samples" as context and decides itself.
        # 2026-06-05: _SHADOW_EDGES emptied per Nunu directive (overdrive strip).
        # The 8 hardcoded (symbol,side,strategy) -> confidence-floor mappings were
        # directly modulating real trade sizing via confidence floors (0.72-0.90).
        # All claimed 60-100% WR from a pre-fee-fix April-window shadow audit. The
        # fabricated certainty was the SINGLE most impactful injection point — it
        # didn't just inform reasoning, it sized actual capital. Empty until live
        # rolling stats can be computed from corrected-fee trade_ledger.
        _SHADOW_EDGES: dict = {}
        _SHADOW_BLOCKS = set()  # Hardcoded blocks removed — see commit note above

        # Walk every agreeing strategy: find the best floor AND any block.
        _agreeing_strats = [s.strategy for s in signals]
        _best_floor = None
        _blocking_combos = []
        for _strat in _agreeing_strats:
            _key = (_base_sym, side, _strat)
            if _key in _SHADOW_BLOCKS:
                _blocking_combos.append(_strat)
                continue
            _edge = _SHADOW_EDGES.get(_key)
            if _edge is not None and (_best_floor is None or _edge > _best_floor):
                _best_floor = _edge

        if _blocking_combos and len(_agreeing_strats) <= 2:
            # Majority of agreeing strategies are known money losers.
            # Hard-deflate to 0.35 — signal must have overwhelming override to trade.
            _old_defl = _deflation
            _deflation = min(_deflation, 0.35)
            logger.info(
                f"[{symbol}] Shadow BLOCK: {_base_sym} {side} via "
                f"{_blocking_combos} — deflation {_old_defl:.2f} -> {_deflation:.2f} "
                f"(shadow-ledger-verified money loser)"
            )
        elif _best_floor is not None and _deflation < _best_floor:
            logger.info(
                f"[{symbol}] Shadow edge floor: {_base_sym} {side} via "
                f"{_agreeing_strats} — deflation {_deflation:.2f} -> {_best_floor:.2f}"
            )
            _deflation = _best_floor

        win_prob = raw_win_prob * _deflation
        # Cross-asset correlation boost: when multiple symbols move in signal direction
        if self._correlation_boost is not None:
            try:
                _corr_mult = self._correlation_boost.get_boost(symbol, side)
                if _corr_mult > 1.0:
                    win_prob *= _corr_mult
                    logger.debug(f"[{symbol}] Correlation boost: {_corr_mult:.2f}x -> win_prob={win_prob:.3f}")
            except Exception:
                pass

        # SHIP-2026-04-19: IC study proved win_prob_deflated has IC=-0.003 (p=0.976, pure noise)
        # and reliability diagram is inverted (Q5 predicts 22% WR, Q1 predicts 41%).
        # Regime-zoo found it works only in trending (AUC 0.60), broken in illiquid (AUC 0.388).
        # Clamp non-trending regimes to 0.50 neutral prior until win_prob_v2 ships.
        # Trending keeps original (has real signal per regime-conditional model zoo).
        _raw_win_prob_v1 = win_prob  # preserve original for shadow logging
        _wp_clamped = _regime_ev not in ("trending", "trend", "trending_bull", "trending_bear")
        if _wp_clamped:
            win_prob = 0.50
        try:
            from trading_config import TradingConfig as _TConf
            _fee_bps = _TConf().taker_fee_bps
        except Exception:
            _fee_bps = 4
        # Regime-specific slippage: high-vol/panic markets have wider spreads
        # and worse fills. Add slippage as additional cost beyond fees.
        _REGIME_SLIPPAGE_BPS = {
            "trending_bull": 1, "trending_bear": 2, "trend": 1,
            "consolidation": 1, "range": 1,
            "high_volatility": 4, "panic": 6,
            "low_liquidity": 5, "news_dislocation": 5,
        }
        _slippage_bps = _REGIME_SLIPPAGE_BPS.get(_regime_ev, 2)
        _total_cost_bps = _fee_bps * 2 + _slippage_bps  # round-trip fees + slippage
        fee_drag = (entry * _total_cost_bps / 10000.0) / stop_width if stop_width > 0 else 0
        # Partial-close-aware EV: model TP1 partial close + TP2 continuation
        # After TP1 hit, SL moves to breakeven → remaining position is risk-free
        # but only ~50% chance of reaching TP2 (conservative estimate)
        _tp1_close_pct = 0.60  # Default: MEDIUM profile closes 60% at TP1
        _p_tp2_given_tp1 = 0.45  # Conservative: once TP1 hit and SL at BE, 45% reach TP2
        # Blended win payoff: tp1_pct gets rr_tp1, remainder gets expected rr_tp2
        _win_payoff = (
            _tp1_close_pct * (rr_tp1 - fee_drag)
            + (1 - _tp1_close_pct) * _p_tp2_given_tp1 * (rr_tp2 - fee_drag)
            # remaining position that doesn't reach TP2: exits at ~breakeven (0 gain, pay exit fee)
            + (1 - _tp1_close_pct) * (1 - _p_tp2_given_tp1) * (-fee_drag * 0.5)
        )
        ev_per_dollar = round(win_prob * _win_payoff - (1.0 - win_prob) * (1.0 + fee_drag), 4)

        # Defense-in-depth: reject negative-EV signals at ensemble level.
        # The signal pipeline also checks EV, but this prevents wasted computation
        # on signals that are mathematically unprofitable.
        #
        # LLM-first raw path: EV is attached as metadata for the LLM to reason
        # about, but NOT used as a hard gate. The LLM is the final filter.
        if ev_per_dollar < 0 and not llm_first_raw:
            logger.info(
                f"[ENSEMBLE] {symbol} {side} rejected: negative EV ({ev_per_dollar:.4f}) "
                f"R:R={rr_tp1:.2f} fee_drag={fee_drag:.3f} win_prob={win_prob:.2f}"
            )
            # Check adaptive EV calibrator for override
            _ev_override = False
            _ev_override_source = ""

            # 2026-05-30 OVERDRIVE: when LLM_MODE >= 4, EV gate becomes informational only.
            # LLM is the trader; mechanical EV math is a data point, not a hard block.
            try:
                import os as _os
                if int(_os.getenv("LLM_MODE", "0")) >= 4:
                    _ev_override = True
                    _ev_override_source = "overdrive_llm_primary"
                    logger.info(
                        f"[ENSEMBLE] {symbol} {side} EV={ev_per_dollar:.4f} WP={win_prob:.2f} "
                        f"R:R={rr_tp1:.2f} — informational only (LLM_MODE>=4, LLM decides)"
                    )
            except Exception:
                pass
            if hasattr(self, '_ev_calibrator') and self._ev_calibrator is not None:
                try:
                    if self._ev_calibrator.should_override(ev_per_dollar, n_agree):
                        _ev_override = True
                        _ev_override_source = "ev_calibrator"
                        logger.info(
                            f"[ENSEMBLE] {symbol} {side} MARGINAL-EV OVERRIDE: "
                            f"EV={ev_per_dollar:.4f} n_agree={n_agree} "
                            f"size_mult={self._ev_calibrator.get_override_size_mult()}"
                        )
                except Exception:
                    pass

            # ── LLM-reasoned override: ask the OverrideAgent ──
            # Only if calibrator didn't already override, signal has real quality,
            # and this symbol+side has historical edge data.
            if (not _ev_override
                    and n_agree >= 2  # Require at least 2 strategies agreeing
                    and combined_conf >= 60  # Minimum quality threshold
                    and hasattr(self, '_override_coordinator')
                    and self._override_coordinator is not None):
                try:
                    from llm.override_context import build_override_context
                    from llm.override_ledger import get_override_ledger, OverrideRecord
                    import time as _time

                    # Build minimal signal-like object for context
                    class _SigLike:
                        def __init__(self):
                            self.entry = entry
                            self.sl = best_sl
                            self.tp1 = best_tp1
                            self.tp2 = best_tp2
                            self.confidence = combined_conf
                            self.metadata = {
                                "num_agree": n_agree,
                                "volume_ratio": _vol_ratio if '_vol_ratio' in dir() else 1.0,
                                "regime": self._current_regime.get(symbol, "unknown"),
                            }

                    ctx = build_override_context(
                        symbol=symbol,
                        side=side,
                        block_type="negative_ev",
                        block_reason=(
                            f"EV={ev_per_dollar:.4f} using WP={win_prob:.2f}, "
                            f"R:R={rr_tp1:.2f}, fee_drag={fee_drag:.3f}"
                        ),
                        block_details={
                            "ev": round(ev_per_dollar, 4),
                            "win_prob_used": round(win_prob, 2),
                            "rr_tp1": round(rr_tp1, 2),
                            "rr_tp2": round(rr_tp2, 2) if rr_tp2 else 0,
                            "fee_drag": round(fee_drag, 3),
                            "n_agree": n_agree,
                            "combined_conf": round(combined_conf, 1),
                        },
                        signal=_SigLike(),
                    )

                    # Only ask LLM if we have edge data — otherwise don't waste a call
                    if ctx.edge_n >= 20 and ctx.edge_wr >= 55:
                        decision = self._override_coordinator.evaluate_override(ctx)
                        if decision and decision.get("decision") == "override":
                            _agent_conf = float(decision.get("confidence", 0.0))
                            if _agent_conf >= 0.75:
                                _ev_override = True
                                _ev_override_source = "llm_override_agent"
                                # Log to ledger
                                try:
                                    ledger = get_override_ledger()
                                    rec = OverrideRecord(
                                        override_id="",
                                        timestamp=_time.time(),
                                        symbol=symbol,
                                        side=side,
                                        block_type="negative_ev",
                                        block_reason=ctx.block_reason,
                                        block_details=ctx.block_details,
                                        confidence=combined_conf,
                                        num_strategies_agree=n_agree,
                                        strategies_firing=[s.strategy for s in signals],
                                        regime_1h=ctx.regime_1h,
                                        volume_ratio=ctx.volume_ratio,
                                        edge_data={
                                            "setup_key": ctx.edge_setup_key,
                                            "wr": ctx.edge_wr,
                                            "pf": ctx.edge_pf,
                                            "n": ctx.edge_n,
                                            "verdict": ctx.edge_verdict,
                                        },
                                        override_decision="override",
                                        override_agent_confidence=_agent_conf,
                                        override_agent_reasoning=decision.get("reasoning", ""),
                                        evidence_cited=[decision.get("edge_citation", "")],
                                        override_approved=True,
                                        approval_reason=decision.get("summary", ""),
                                        outcome="pending",
                                    )
                                    ledger.record_override(rec)
                                except Exception as _le:
                                    logger.debug(f"[OVERRIDE] Ledger error: {_le}")
                                logger.info(
                                    f"[ENSEMBLE] {symbol} {side} LLM-OVERRIDE APPROVED: "
                                    f"conf={_agent_conf:.2f} edge={ctx.edge_setup_key} "
                                    f"WR={ctx.edge_wr:.0f}% ({decision.get('summary', '')[:80]})"
                                )
                except Exception as _oe:
                    logger.debug(f"[OVERRIDE] EV override eval error: {_oe}")

            # Record rejection for adaptive outcome tracking
            if self._rejection_outcome_tracker is not None:
                try:
                    self._rejection_outcome_tracker.record(
                        symbol=symbol, side=side, n_agree=n_agree,
                        ev=ev_per_dollar, win_prob=win_prob, price=entry,
                        regime=self._current_regime.get(symbol, "unknown"),
                    )
                except Exception:
                    pass

            if not _ev_override:
                logger.info(f"[ENSEMBLE] {symbol} {side} negative EV BLOCKED — no override")
                return None
            # Continue to signal construction (override active: source={_ev_override_source})

        # Propagate chop_score from input signals (attached by chop detector pre-merge)
        _chop_score = max(
            (s.metadata.get("chop_score", 0) for s in signals), default=0
        )

        # SHIP-2026-04-20: alpha gate shadow mode — log verdicts, do NOT enforce by default.
        # ALPHA_GATE_ENABLED=false → this block is a no-op.
        # ALPHA_GATE_ENABLED=true + ALPHA_GATE_SHADOW=true → logs verdicts, does NOT reject.
        # ALPHA_GATE_ENABLED=true + ALPHA_GATE_SHADOW=false → gate enforces live.
        try:
            from strategies.alpha_gate import (
                ALPHA_GATE_ENABLED,
                ALPHA_GATE_SHADOW,
                evaluate as _alpha_eval,
                log_shadow_verdict as _alpha_log,
            )
            if ALPHA_GATE_ENABLED:
                _alpha_ctx = {
                    "num_agree": len(signals),
                    "chop_score": _chop_score,
                    # TODO: wire btc_4h_return_signed + rsi_div_1h_6h_aligned from
                    # DataFetcher + feature cache. Until then, those two signals
                    # contribute None (neither + nor - to conviction count).
                }
                _alpha_verdict = _alpha_eval(None, _alpha_ctx)
                _alpha_shim = type("S", (), {
                    "symbol": symbol, "side": side, "strategy": "ensemble",
                })()
                _alpha_log(_alpha_shim, _alpha_verdict)
                if not ALPHA_GATE_SHADOW and not _alpha_verdict.passes:
                    logger.info(
                        f"[{symbol}] ALPHA-GATE reject: "
                        f"conviction={_alpha_verdict.conviction_count} "
                        f"reason={_alpha_verdict.reason}"
                    )
                    return None
        except Exception as _alpha_exc:
            logger.exception(f"[{symbol}] ALPHA-GATE hook failure (non-fatal): {_alpha_exc}")

        return Signal(
            strategy="ensemble",
            symbol=symbol,
            side=side,
            confidence=combined_conf,
            entry=entry,
            sl=best_sl,
            tp1=best_tp1,
            tp2=best_tp2,
            atr=atr,
            metadata={
                "strategies_agree": [s.strategy for s in signals],
                "num_agree": len(signals),
                "total_strategies": len(self.strategies),
                "individual_confidences": {s.strategy: s.confidence for s in signals},
                # SHIP-2026-04-19: propagate regime_score + align_long from input signals.
                # Previously dropped here, causing ml_conf, alerts, analytics to see always-0.
                # See REGIME_SCORE_BUG_2026_04_19.md. Max-by-abs for regime, max for align.
                "regime_score": max(
                    ((s.metadata or {}).get("regime_score", 0) for s in signals),
                    key=lambda v: abs(v or 0),
                    default=0,
                ),
                "align_long": max(
                    ((s.metadata or {}).get("align_long", 0) for s in signals),
                    default=0,
                ),
                "raw_weighted_conf": round(weighted_conf, 2),
                "consensus_mult": round(consensus_mult, 3),
                "combined_conf": round(combined_conf, 2),
                "strategy_weights": {s.strategy: round(self._get_strategy_weight(s.strategy), 3) for s in signals},
                "per_signal_atr": per_signal_atr,
                "per_signal_sl": per_signal_sl,
                "per_signal_tp1": per_signal_tp1,
                "mode": self.mode,
                "ev_per_dollar": ev_per_dollar,
                "win_prob": round(win_prob, 4),
                # WAVE2A L3 (FULL_PIPE_BUILD_MAP D4, 2026-07-02): provenance
                # labels at source. win_prob derives from ensemble confidence,
                # which the 2026-04-19 IC study measured at IC=-0.003
                # (p=0.976) vs outcomes — noise-grade. Downstream consumers
                # (prompts, EV math) must carry these labels, not certainty.
                "win_prob_v1_preclamp": round(_raw_win_prob_v1, 4),
                "win_prob_provenance": (
                    f"conf/100 x deflation {_deflation:.2f} (n_indep={_indep_key}); "
                    "IC=-0.003 p=0.976 vs outcomes (2026-04-19 study) — noise-grade; "
                    + (f"clamped to 0.50 neutral prior (regime={_regime_ev} "
                       "non-trending, AUC 0.388)" if _wp_clamped
                       else f"regime={_regime_ev} trending: v1 value kept (AUC 0.60)")
                ),
                "ev_provenance": (
                    "EV = win_prob x fee/slippage payoff model — inherits win_prob's "
                    "noise-grade caveat; treat as rough context, not a verdict"
                ),
                "rr_tp1": round(rr_tp1, 3),
                "rr_tp2": round(rr_tp2, 3),
                "fee_drag_pct": round(fee_drag * 100, 1) if stop_width > 0 else 0.0,
                "stop_width_pct": round(stop_width / entry * 100, 3) if entry > 0 else 0.0,
                "chop_score": _chop_score,
            },
        )

    def get_all_status(
        self, symbol: str, data: Dict[str, pd.DataFrame]
    ) -> List[Dict[str, Any]]:
        """Get status from all strategies for display."""
        statuses = []
        for strategy in self.strategies:
            try:
                status = strategy.get_status(symbol, data)
                statuses.append(status)
            except Exception as e:
                statuses.append({
                    "symbol": symbol,
                    "strategy": strategy.name,
                    "status": f"error: {e}",
                })
        return statuses

    def get_signal_digest(self, symbol: str) -> Dict[str, Any]:
        """Build comprehensive signal digest for LLM brain visibility.

        Returns ALL strategy readings for a symbol — not just passing ones.
        This gives the LLM full visibility into what every strategy is detecting,
        the ensemble decision, and why signals passed or were rejected.
        """
        cached = self._last_signals.get(symbol, {})
        if not cached:
            return {}

        readings = []
        sides = {"BUY": 0, "SELL": 0}
        total_conf = 0.0
        n_signals = 0

        for strat_name, sig in cached.items():
            reading = {
                "strategy": strat_name,
                "side": sig.side,
                "confidence": round(sig.confidence, 1),
                "entry": round(sig.entry, 2) if sig.entry else 0,
                "weight": round(self.weights.get(strat_name, 1.0), 2),
                "duration": self.STRATEGY_DURATION_MAP.get(strat_name, "unknown"),
                "timeframe": self.STRATEGY_TIMEFRAME.get(strat_name, "swing"),
            }
            # Include key metadata that strategies computed
            for key in ("regime_score", "chop_score", "quality_score", "signal_flags",
                        "entry_type", "setup_type", "atr_pct", "vol_ratio"):
                if key in sig.metadata:
                    reading[key] = sig.metadata[key]
            readings.append(reading)
            sides[sig.side] = sides.get(sig.side, 0) + 1
            total_conf += sig.confidence
            n_signals += 1

        # Compute agreement and consensus
        dominant_side = max(sides, key=sides.get) if sides else "NONE"
        agreement = sides.get(dominant_side, 0)
        dissent = n_signals - agreement

        digest = {
            "symbol": symbol,
            "n_strategies": n_signals,
            "readings": readings,
            "consensus": {
                "dominant_side": dominant_side,
                "agreement": agreement,
                "dissent": dissent,
                "avg_confidence": round(total_conf / n_signals, 1) if n_signals else 0,
                "min_votes_needed": self.min_votes,
                "would_pass_votes": agreement >= self.min_votes,
            },
        }

        # Add rejection history if available
        chop = self._smoothed_chop.get(symbol, 0)
        if chop > 0:
            digest["chop_score"] = round(chop, 3)

        # Include last rejection reason so LLM knows WHY signals were blocked
        rejection = self._last_rejections.get(symbol)
        if rejection:
            digest["last_rejection"] = rejection

        return digest

    def get_all_signal_digests(self) -> Dict[str, Dict]:
        """Get signal digests for ALL symbols with cached readings."""
        return {sym: self.get_signal_digest(sym) for sym in self._last_signals}

    def update_weights(self, performance: Dict[str, float]):
        """Update strategy weights based on observed performance."""
        for name, perf in performance.items():
            if name in self.weights:
                # Simple: weight = 0.5 + performance (bounded 0.1 to 2.0)
                self.weights[name] = max(0.1, min(2.0, 0.5 + perf))
        logger.info(f"Updated ensemble weights: {self.weights}")
