"""
Signal processing pipeline — extracted from multi_strategy_main._process_symbol().

Provides the RiskFilterChain that validates signals through a sequence of gates:
1. Signal validity (R:R, stop width, side correctness)
2. Circuit breaker check
3. Leverage decision
4. Liquidation safety check
5. Portfolio risk check

Usage:
    chain = RiskFilterChain(risk_mgr, leverage_mgr, config)
    result = chain.evaluate(signal, equity, current_positions)
    if result.approved:
        # proceed to trade
"""

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from strategies.base import Signal
from core.filter_annotations import FilterAnnotation, AnnotatedSignal
from execution.leverage import LeverageDecision

logger = logging.getLogger("bot.core.signal_pipeline")


def _get_tel():
    """Lazy import to avoid circular dependency."""
    try:
        from core.structured_logging import get_trade_event_logger
        return get_trade_event_logger()
    except Exception:
        return None

# Optional db logging — disabled in backtest to avoid writes
_rejection_log_enabled: bool = False


def enable_rejection_logging(enabled: bool = True):
    """Enable/disable signal rejection logging to SQLite (paper/live mode only)."""
    global _rejection_log_enabled
    _rejection_log_enabled = enabled


def _log_rejection(signal, gate: str, reason: str):
    """Best-effort rejection log — never raises."""
    if not _rejection_log_enabled:
        return
    try:
        from data import db
        db.log_signal_rejection(
            symbol=signal.symbol,
            strategy=signal.strategy,
            side=signal.side,
            confidence=signal.confidence,
            gate=gate,
            reason=reason,
            entry=getattr(signal, "entry", 0.0),
            sl=getattr(signal, "sl", 0.0),
        )
    except Exception:
        pass


@dataclass
class FilterResult:
    """Result of running a signal through the risk filter chain."""
    approved: bool
    signal: Signal
    leverage: float = 1.0
    risk_multiplier: float = 1.0
    position_qty: float = 0.0
    rejection_reason: str = ""
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def apply_quant_rules(
    signal: Signal,
    config,
    num_strategies_agree: int = 1,
    now: datetime = None,
) -> dict:
    """Apply proven quant rules to boost signal confidence and risk multiplier.

    These are statistically validated edges hardcoded into the pipeline.
    Applied BEFORE the risk filter chain so confidence boosts feed into
    all downstream sizing/leverage decisions.

    Returns a dict with:
      - "confidence_boost": float multiplier applied to signal.confidence
      - "risk_mult_boost": float multiplier applied to risk_multiplier
      - "rules_applied": list of rule names that fired
      - "meta": dict of metadata for logging
    """
    if now is None:
        now = datetime.now(timezone.utc)

    confidence_boost = 1.0
    risk_mult_boost = 1.0
    rules_applied = []
    meta = {}

    # Strip symbol suffix for matching
    base_symbol = signal.symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "").replace("/USD", "")

    # Get regime from signal metadata
    regime = (signal.metadata or {}).get("regime", "unknown")

    # ── Rule 1: Morning Edge (06-12 UTC = 75% WR) ──
    if getattr(config, "quant_morning_edge_enabled", True):
        if 6 <= now.hour < 12:
            boost = getattr(config, "quant_morning_edge_boost", 1.2)
            confidence_boost *= boost
            rules_applied.append("morning_edge")
            meta["morning_edge_hour"] = now.hour
            meta["morning_edge_boost"] = boost

    # SHIP-2026-04-19: hour_bucket_4 feature (IC=+0.178, p=0.035 per FEATURE_CANDIDATES).
    # 00-05 UTC: 18% WR, -$103 across 33 trades (asia deadzone)
    # 06-11 UTC: covered by morning edge above
    # 12-17 UTC: neutral baseline
    # 18-23 UTC: 39% WR, +$220 (US-session)
    # Log the bucket for all signals so downstream calibration + IC re-studies have it.
    meta["hour_bucket_4"] = now.hour // 6  # 0, 1, 2, 3

    # ── Rule 2: BTC SHORT Edge (67% WR, strongest setup) ──
    if getattr(config, "quant_btc_short_edge_enabled", True):
        if base_symbol == "BTC" and signal.side == "SELL":
            boost = getattr(config, "quant_btc_short_edge_boost", 1.15)
            confidence_boost *= boost
            rules_applied.append("btc_short_edge")
            meta["btc_short_boost"] = boost

    # ── Rule 3: HYPE BUY in High Vol (strongest edge at P50-P75 ATR) ──
    if getattr(config, "quant_hype_highvol_enabled", True):
        if base_symbol == "HYPE" and signal.side == "BUY" and regime == "high_volatility":
            boost = getattr(config, "quant_hype_highvol_boost", 1.2)
            confidence_boost *= boost
            rules_applied.append("hype_highvol_buy")
            meta["hype_highvol_boost"] = boost

    # ── Rule 4: Conviction Multiplier (size up on high-confidence multi-agree) ──
    if getattr(config, "quant_conviction_mult_enabled", True):
        min_conf = getattr(config, "quant_conviction_min_confidence", 80.0)
        min_agree = getattr(config, "quant_conviction_min_agree", 2)
        if signal.confidence >= min_conf and num_strategies_agree >= min_agree:
            rm_boost = getattr(config, "quant_conviction_risk_mult", 1.3)
            risk_mult_boost *= rm_boost
            rules_applied.append("conviction_mult")
            meta["conviction_risk_mult"] = rm_boost
            meta["conviction_confidence"] = signal.confidence
            meta["conviction_num_agree"] = num_strategies_agree

    if rules_applied:
        logger.info(
            f"[{signal.symbol}] QUANT RULES: {', '.join(rules_applied)} "
            f"conf_boost={confidence_boost:.2f}x risk_boost={risk_mult_boost:.2f}x"
        )

    return {
        "confidence_boost": confidence_boost,
        "risk_mult_boost": risk_mult_boost,
        "rules_applied": rules_applied,
        "meta": meta,
    }


class RiskFilterChain:
    """Sequential risk filter chain for signal validation.

    Extracts the 200+ lines of risk checks from _process_symbol() into
    a clean, testable pipeline.
    """

    def __init__(self, risk_mgr, leverage_mgr, config):
        self.risk_mgr = risk_mgr
        self.leverage_mgr = leverage_mgr
        self.config = config
        self._missed_trade_tracker = None  # Optional: set via set_missed_trade_tracker()

    def set_missed_trade_tracker(self, tracker):
        """Inject MissedTradeTracker for rejection tracking."""
        self._missed_trade_tracker = tracker

    def _track_pipeline_rejection(self, signal: Signal, reason: str):
        """Record a pipeline rejection in the missed trade tracker."""
        if self._missed_trade_tracker is None:
            return
        try:
            self._missed_trade_tracker.record_rejection(
                signal=signal, reason=reason, gate="pipeline",
            )
        except Exception:
            pass

    @staticmethod
    def _log_signal_filtered(signal: Signal, gate: str, reason: str):
        """Log a SIGNAL_FILTERED event to the TradeEventLogger."""
        try:
            tel = _get_tel()
            if tel is None:
                return
            tel.log(
                "SIGNAL_FILTERED",
                signal.symbol,
                side=signal.side,
                strategy=getattr(signal, "strategy", ""),
                confidence=signal.confidence,
                entry=signal.entry,
                sl=getattr(signal, "sl", 0.0),
                reason=f"[{gate}] {reason}",
                regime=(signal.metadata or {}).get("regime", ""),
            )
        except Exception:
            pass

    def evaluate(
        self,
        signal: Signal,
        equity: float,
        num_strategies_agree: int,
        total_strategies: int,
        current_open_count: int = 0,
        current_extreme_count: int = 0,
        risk_tier: str = "medium",
        cb_conf_override_pct: float = 0.92,
        open_positions: Optional[Dict[str, Any]] = None,
        portfolio_risk_engine=None,
    ) -> FilterResult:
        """Run a signal through all risk gates.

        Returns FilterResult with approved=True if signal passes all checks.
        """
        meta = {}
        # Pipeline telemetry: capture every gate decision and multiplier
        try:
            from core.pipeline_telemetry import get_telemetry as _get_pt
            _pt = _get_pt()
        except Exception:
            _pt = None

        # ── Quant Rules: apply proven statistical edges ──
        # Boost confidence and risk_mult BEFORE the filter chain so all
        # downstream gates (leverage, sizing, EV) benefit from the edge.
        quant = apply_quant_rules(
            signal=signal,
            config=self.config,
            num_strategies_agree=num_strategies_agree,
            now=None,  # use current time
        )
        if quant["rules_applied"]:
            # Apply confidence boost (deep copy to avoid mutating original)
            signal = copy.copy(signal)
            boosted_conf = signal.confidence * quant["confidence_boost"]
            max_conf = getattr(self.config, "max_ensemble_confidence", 95.0)
            signal.confidence = min(boosted_conf, max_conf)
            meta["quant_rules"] = quant["rules_applied"]
            meta["quant_confidence_boost"] = quant["confidence_boost"]
            meta.update(quant["meta"])

        # Gate 1: Signal validity (R:R, stop width, side)
        if not signal.is_valid:
            _reason = (f"Invalid signal: stop_width_pct={signal.stop_width_pct:.4f}, "
                       f"rr={signal.risk_reward_tp1:.2f}")
            if _pt: _pt.record_gate(signal.symbol, "validity", False, signal.stop_width_pct, 0.003, _reason)
            _log_rejection(signal, "validity", _reason)
            self._log_signal_filtered(signal, "validity", _reason)
            return FilterResult(approved=False, signal=signal, rejection_reason=_reason)
        # Gate 1a: BTC stop width advisory — 47/47 historical BTC losses had stops <0.8%.
        # 2026-06-08: changed from hard reject to advisory. A tight-stop BTC scalp
        # might be valid; let the LLM see the warning + the setup and decide.
        # Only HARD-REJECT for sub-noise stops (<0.3% — below BTC's 1min wick noise).
        if signal.symbol == "BTC" and signal.stop_width_pct < 0.003:
            _reason = f"BTC stop below noise floor: stop_width={signal.stop_width_pct:.4f} <0.3%"
            if _pt: _pt.record_gate(signal.symbol, "btc_stop_width_noise", False, signal.stop_width_pct, 0.003, _reason)
            _log_rejection(signal, "btc_stop_width_noise", _reason)
            self._log_signal_filtered(signal, "btc_stop_width_noise", _reason)
            return FilterResult(approved=False, signal=signal, rejection_reason=_reason)
        elif signal.symbol == "BTC" and signal.stop_width_pct < 0.008:
            # Advisory: historically poor performance, but LLM can override.
            if hasattr(signal, 'metadata') and isinstance(signal.metadata, dict):
                signal.metadata.setdefault("advisory_warnings", []).append(
                    f"BTC tight stop {signal.stop_width_pct:.4f} — historical 47/47 SL hits"
                )
        # DECHOKE 2 (2026-07-02, GM_GATE_ROC_56K §3 — owner-approved):
        # Gate 1b (rr_floor) DELETED. Re-verified in the live rejection DB
        # (ml_data/bot.db signal_rejections): ZERO firings ever — Signal.is_valid
        # already enforces R:R >= 1.0 (validity gate, untouched), and config
        # min_signal_rr never exceeded that, so this gate was unwired sediment.
        # The R:R numbers stay in meta as labeled LLM context.
        min_rr = getattr(self.config, "min_signal_rr", 1.0)
        if _pt: _pt.record_gate(signal.symbol, "validity", True, signal.risk_reward_tp1, min_rr)
        meta["rr_tp1"] = round(signal.risk_reward_tp1, 2)
        meta["rr_tp2"] = round(signal.risk_reward_tp2, 2)

        # Gate 1c: Fee-drag filter
        # Reject trades where round-trip fees + slippage consume too much of stop width.
        # A stop width of 0.3% with 0.10% round-trip fees = 33% fee drag — barely viable.
        fee_bps = getattr(self.config, "taker_fee_bps", 4)
        slippage_bps = getattr(self.config, "slippage_bps", 3)
        # Regime-specific slippage: high-vol/panic have wider spreads
        _regime_slippage = {
            "trending_bull": 1, "trending_bear": 2, "trend": 1,
            "consolidation": 1, "range": 1,
            "high_volatility": 4, "panic": 6,
            "low_liquidity": 5, "news_dislocation": 5,
        }
        _sig_regime = (signal.metadata or {}).get("regime", "unknown")
        _extra_slip = _regime_slippage.get(_sig_regime, 2)
        round_trip_fee_pct = (fee_bps * 2 + _extra_slip) / 10000.0
        stop_pct = signal.stop_width_pct
        if stop_pct > 0:
            fee_drag_pct = round_trip_fee_pct / stop_pct
            meta["fee_drag_pct"] = round(fee_drag_pct * 100, 1)
            # DECHOKE 2 (2026-07-02, GM_GATE_ROC_56K §3 — owner-approved):
            # Gate 1c (fee_drag hard reject) DELETED. Re-verified in the live
            # rejection DB: 2 firings ever, both 2026-06-02, none since —
            # effectively unwired. The fee-drag ARITHMETIC stays (meta above)
            # as labeled LLM context per the FALLACY_AUDIT gate verdict table.

        # DECHOKE 2 (2026-07-02, GM_GATE_ROC_56K §3 — owner-approved):
        # Gate 1d (ev_floor hard reject) DELETED. Re-verified in the live
        # rejection DB: ZERO firings ever (config MIN_SIGNAL_EV kept it off),
        # and its input ev_per_dollar is measured ANTI-predictive
        # (AUC 0.444-0.470, FALLACY_AUDIT D4) — never re-enable a gate on
        # this input. The EV number stays in meta as labeled LLM context.
        ev = signal.metadata.get("ev_per_dollar") if signal.metadata else None
        if ev is not None:
            meta["ev_per_dollar"] = ev

        # Gate 1e: Slippage rejection (hard reject on high slippage, not just warning)
        # High slippage can turn winners into losers and should be avoided preemptively.
        # Calculate expected slippage impact as % of stop width.
        if stop_pct > 0:
            # Base slippage + regime-specific adjustment
            _slippage_impact_pct = (slippage_bps + _extra_slip) / 10000.0
            _slippage_pct_of_stop = _slippage_impact_pct / stop_pct if stop_pct > 0 else 0
            meta["slippage_pct_of_stop"] = round(_slippage_pct_of_stop * 100, 1)

            # Reject if slippage + fees consume >50% of stop width (leaves 50% for actual risk)
            # Loosened from 40%: backtest shows 44.6% gate accuracy — blocking too many winners
            max_slippage_pct_of_stop = 0.50
            if _slippage_pct_of_stop > max_slippage_pct_of_stop:
                _reason = (f"Slippage impact {_slippage_pct_of_stop:.0%} of stop width > "
                          f"{max_slippage_pct_of_stop:.0%} (regime={_sig_regime}, stop={stop_pct:.4f})")
                if _pt: _pt.record_gate(signal.symbol, "slippage", False, _slippage_pct_of_stop, max_slippage_pct_of_stop, _reason)
                _log_rejection(signal, "slippage", _reason)
                self._log_signal_filtered(signal, "slippage", _reason)
                return FilterResult(
                    approved=False, signal=signal,
                    rejection_reason=_reason,
                    metadata=meta,
                )

        # Gate 1f: Minimum win probability (post-deflation from ensemble)
        # Trades 2&3 on 2026-03-25 had 42%/40% win_prob — below coin flip.
        # Sub-48% WP = negative EV after fees. Block these regardless of setup.
        # Floor is regime-adaptive: bad regimes (illiquid=28% WR) require higher
        # predicted win_prob since SL hits are noisy by default.
        _win_prob = meta.get("win_prob") if meta else None
        if _win_prob is not None and isinstance(_win_prob, (int, float)):
            _min_wp = getattr(self.config, "min_signal_win_prob", 0.43)
            # Regime-adaptive adjustment: raise floor for losing regimes
            try:
                from llm.dynamic_thresholds import get_dynamic_thresholds
                _regime_key = (meta.get("regime") or "") if meta else ""
                if _regime_key:
                    _rstats = get_dynamic_thresholds().get_regime_stats(_regime_key)
                    if _rstats and _rstats["n"] >= 10:
                        _rwr = _rstats["wr"]
                        _blend = min(1.0, (_rstats["n"] - 10) / 30)
                        # Poor regime → raise floor; strong regime → slight reduction
                        if _rwr < 0.30:
                            _dyn_wp = _min_wp + 0.08 * _blend   # up to +8%
                        elif _rwr < 0.38:
                            _dyn_wp = _min_wp + 0.04 * _blend   # up to +4%
                        elif _rwr >= 0.48:
                            _dyn_wp = _min_wp - 0.03 * _blend   # slight relaxation
                        else:
                            _dyn_wp = _min_wp
                        _min_wp = max(0.35, _dyn_wp)  # absolute floor
            except Exception:
                pass
            # 2026-06-08: win_prob floor → ADVISORY in LLM_FIRST mode. The
            # old code hard-rejected; now we tag the signal so the LLM sees
            # "this is below historical edge floor — investigate before
            # approving." Hard reject only if win_prob is critically low
            # (<0.30, the structural noise floor).
            try:
                from trading_config import TradingConfig as _TC2
                _llm_mode = getattr(_TC2(), "llm_mode", 5)
            except Exception:
                _llm_mode = 5  # default to LLM_FIRST
            _critical_wp_floor = 0.30
            if _win_prob < _critical_wp_floor:
                _reason = f"Win probability {_win_prob:.1%} < critical {_critical_wp_floor:.1%} (true noise floor)"
                if _pt: _pt.record_gate(signal.symbol, "win_prob_critical", False, _win_prob, _critical_wp_floor, _reason)
                _log_rejection(signal, "win_prob_critical", _reason)
                self._log_signal_filtered(signal, "win_prob_critical", _reason)
                return FilterResult(
                    approved=False, signal=signal,
                    rejection_reason=_reason,
                    metadata=meta,
                )
            elif _win_prob < _min_wp:
                # Below edge floor but above noise floor — advisory to LLM.
                if hasattr(signal, 'metadata') and isinstance(signal.metadata, dict):
                    signal.metadata.setdefault("advisory_warnings", []).append(
                        f"win_prob {_win_prob:.1%} below edge floor {_min_wp:.1%} (regime-adjusted)"
                    )
                if _pt: _pt.record_gate(signal.symbol, "win_prob_advisory", True, _win_prob, _min_wp, "advisory_not_rejected")

        # Gate 1g: Graduated rules — validated hypotheses become executable rules.
        # Rules can VETO (block), BOOST (add confidence), or PENALIZE (subtract).
        # Only vetoes hard-block; confidence adjustments are advisory and logged.
        try:
            from llm.graduated_rules import get_graduated_rules_engine
            _gr_engine = get_graduated_rules_engine()
            _gr_regime = (meta.get("regime") or "") if meta else ""
            _gr_side = signal.side if isinstance(signal.side, str) else signal.side.value
            _gr_n = (meta.get("num_agree") or 1) if meta else 1
            _gr_veto, _gr_adj_conf, _gr_applied, _gr_veto_ids = _gr_engine.evaluate_signal(
                symbol=signal.symbol, regime=_gr_regime, side=_gr_side,
                num_agree=_gr_n, confidence=signal.confidence,
                hour_utc=datetime.now(timezone.utc).hour,
                strategies_active=(meta.get("strategies_agree") or []) if meta else [],
            )
            if _gr_veto:
                _reason = f"Graduated rule veto: {_gr_applied[:100]}"
                if _pt: _pt.record_gate(signal.symbol, "graduated_rule_veto", False, 0, 0, _reason)
                _log_rejection(signal, "graduated_rule_veto", _reason)
                self._log_signal_filtered(signal, "graduated_rule_veto", _reason)
                # Stamp veto_rule_ids so this gate's vetoes become self-measuring
                # (new denominator path — previously recorded nothing).
                try:
                    from llm.brain_wiring import record_veto_counterfactual
                    record_veto_counterfactual(
                        symbol=signal.symbol, side=_gr_side,
                        entry_price=getattr(signal, "entry", 0.0),
                        sl=getattr(signal, "sl", 0.0),
                        tp1=getattr(signal, "tp1", 0.0),
                        tp2=getattr(signal, "tp2", 0.0),
                        confidence=signal.confidence,
                        veto_rule_ids=_gr_veto_ids,
                        strategy=getattr(signal, "strategy", "") or "",
                        regime=_gr_regime,
                    )
                except Exception:
                    pass
                return FilterResult(approved=False, signal=signal, rejection_reason=_reason, metadata=meta)
            if _gr_applied:
                _adj_delta = _gr_adj_conf - signal.confidence
                if abs(_adj_delta) > 0.5:
                    signal = copy.copy(signal)
                    signal.confidence = max(0.0, min(100.0, _gr_adj_conf))
                    meta["graduated_rules_adj"] = round(_adj_delta, 1)
                    meta["graduated_rules_applied"] = _gr_applied[:120]
        except Exception:
            pass

        # Gate 1h: Consecutive-loss streak penalty — penalize -15 conf after 2+ losses.
        # Prevents amplifying bad signals during losing streaks (4 consecutive losses at shutdown).
        try:
            _consec_losses = getattr(self.risk_mgr, "consecutive_losses", 0)
            if _consec_losses >= 2:
                signal = copy.copy(signal)
                signal.confidence = max(0.0, min(100.0, signal.confidence - 15.0))
                meta["streak_penalty"] = -15.0
                meta["consecutive_losses_at_eval"] = _consec_losses
                logger.info(
                    f"[{signal.symbol}] STREAK GATE: consecutive_losses={_consec_losses} "
                    f"→ conf -15pts to {signal.confidence:.1f}"
                )
        except Exception:
            pass

        # Gate 1.5: Committee thesis alignment (flag-gated via COMMITTEE_GATE_ENABLED)
        # Blocks signals that conflict with the live quant analyst committee verdict.
        # No-op if thesis is missing/stale (fail-open to preserve trade throughput).
        try:
            from llm.committee_reader import is_enabled as _comm_enabled, committee_veto_reason as _comm_veto
            if _comm_enabled():
                _side_str = signal.side if isinstance(signal.side, str) else signal.side.value
                _veto = _comm_veto(signal.symbol, _side_str, max_age_s=900)
                if _veto:
                    if _pt: _pt.record_gate(signal.symbol, "committee_thesis", False, 0, 0, _veto[:120])
                    _log_rejection(signal, "committee_thesis", _veto[:120])
                    self._log_signal_filtered(signal, "committee_thesis", _veto[:120])
                    return FilterResult(approved=False, signal=signal, rejection_reason=_veto[:200])
                else:
                    # Apply size multiplier from committee
                    from llm.committee_reader import committee_size_multiplier as _comm_size
                    _mult = _comm_size(signal.symbol, max_age_s=900)
                    if _mult != 1.0:
                        meta["committee_size_mult"] = _mult
        except Exception:
            pass  # committee reader failure must never block trading

        # Gate 2: Circuit breaker
        if not self.risk_mgr.is_trading_allowed(
            confidence=signal.confidence,
            cb_conf_override_pct=cb_conf_override_pct,
        ):
            if _pt: _pt.record_gate(signal.symbol, "circuit_breaker", False, signal.confidence, cb_conf_override_pct * 100, "Circuit breaker active")
            _log_rejection(signal, "circuit_breaker", "Circuit breaker active")
            self._log_signal_filtered(signal, "circuit_breaker", "Circuit breaker active")
            return FilterResult(
                approved=False, signal=signal,
                rejection_reason="Circuit breaker active"
            )

        # Gate 3: Max open positions
        if current_open_count >= self.config.max_open_positions:
            _reason = f"Max positions ({self.config.max_open_positions}) reached"
            if _pt: _pt.record_gate(signal.symbol, "max_positions", False, current_open_count, self.config.max_open_positions, _reason)
            _log_rejection(signal, "max_positions", _reason)
            self._log_signal_filtered(signal, "max_positions", _reason)
            return FilterResult(
                approved=False, signal=signal,
                rejection_reason=_reason,
            )

        # Gate 3b: Duplicate position guard - block signals for symbols with open positions
        # This prevents the 9-BTC-SHORT-in-one-day bug where re-entry bypassed cooldowns.
        if open_positions and signal.symbol in open_positions:
            _existing = open_positions[signal.symbol]
            _ex_side = getattr(_existing, 'side', 'unknown')
            _ex_entry = getattr(_existing, 'entry', 0.0)
            _ex_leverage = getattr(_existing, 'leverage', 1.0)
            _reason = (
                f"Duplicate position: {signal.symbol} already has open {_ex_side} position "
                f"(entry={_ex_entry}, leverage={_ex_leverage}x)"
            )
            if _pt: _pt.record_gate(signal.symbol, "duplicate_position", False, 1, 0, _reason)
            logger.warning(
                f"[{signal.symbol}] DUPLICATE BLOCKED in pipeline: {_reason}"
            )
            _log_rejection(signal, 'duplicate_position', _reason)
            return FilterResult(
                approved=False, signal=signal,
                rejection_reason=_reason,
            )

        # Gate 4: Correlation guard — prevent clustered directional risk
        # If we already hold positions, check that adding this trade doesn't
        # create excessive correlated exposure (e.g., 3 long alts all 0.8+ corr)
        if portfolio_risk_engine and open_positions and current_open_count >= 2:
            try:
                corr_matrix = portfolio_risk_engine.compute_correlation_matrix()
                if corr_matrix:
                    # Build positions map including the proposed new trade
                    positions_map = {}
                    for sym, pos in open_positions.items():
                        side = getattr(pos, 'side', 'LONG').lower()
                        positions_map[sym] = side
                    proposed_side = "long" if signal.side == "BUY" else "short"
                    positions_map[signal.symbol] = proposed_side

                    cluster_risk = corr_matrix.get_cluster_risk(positions_map)
                    meta["cluster_risk"] = round(cluster_risk, 3)

                    # High correlation cluster → reduce size or reject
                    # Lowered from 0.90 to 0.85 — 3 correlated longs at 0.85+ all
                    # stop out in a dump, creating compounded drawdown.
                    if cluster_risk >= 0.85:
                        # Market-wide move override: if 3+ DIFFERENT symbols all
                        # signal the same direction, this is a regime event (e.g.,
                        # broad selloff), not a diversification problem. Allow it.
                        unique_symbols = set(positions_map.keys())
                        directions = set(positions_map.values())
                        is_market_wide = (
                            len(unique_symbols) >= 3
                            and len(directions) == 1  # all same direction
                        )
                        if is_market_wide:
                            _dir = list(directions)[0]
                            logger.info(
                                f"[CORR-GUARD] OVERRIDE: market-wide {_dir.upper()} "
                                f"({len(unique_symbols)} symbols agree: "
                                f"{', '.join(sorted(unique_symbols))}) "
                                f"— allowing {signal.symbol} through"
                            )
                            meta["corr_guard_override"] = "market_wide_move"
                            meta["market_wide_direction"] = _dir
                            meta["market_wide_symbols"] = len(unique_symbols)
                        else:
                            _reason = (f"Correlation cluster risk {cluster_risk:.2f} >= 0.85 "
                                       f"(too many correlated positions in same direction)")
                            if _pt: _pt.record_gate(signal.symbol, "correlation", False, cluster_risk, 0.85, _reason)
                            _log_rejection(signal, "correlation", _reason)
                            self._log_signal_filtered(signal, "correlation", _reason)
                            return FilterResult(
                                approved=False, signal=signal,
                                rejection_reason=_reason,
                                metadata=meta,
                            )
                    elif cluster_risk >= 0.70:
                        # Don't reject, but reduce risk multiplier by 30%
                        meta["correlation_size_reduction"] = 0.7
                        logger.info(
                            f"[CORR-GUARD] {signal.symbol} cluster_risk={cluster_risk:.2f} >= 0.70 "
                            f"— reducing position size by 30%"
                        )
            except Exception as e:
                logger.debug(f"[CORR-GUARD] Error: {e}")

        # Gate 5: Leverage decision
        lev_decision = self.leverage_mgr.decide(
            confidence=signal.confidence,
            num_strategies_agree=num_strategies_agree,
            total_strategies=total_strategies,
            risk_tier=risk_tier,
            current_extreme_count=current_extreme_count,
            symbol=signal.symbol,
        )

        if lev_decision.leverage <= 0:
            _reason = f"Leverage denied: {lev_decision.reason}"
            if _pt: _pt.record_gate(signal.symbol, "leverage", False, 0, 1, _reason)
            _log_rejection(signal, "leverage", _reason)
            self._log_signal_filtered(signal, "leverage", _reason)
            return FilterResult(
                approved=False, signal=signal,
                rejection_reason=_reason,
            )

        # Gate 5a: Graduated leverage eligibility gate (D2)
        # Floor at 1.0x — allows 2-agree signals (which return 1.0x leverage) to pass
        # with reduced sizing via the graduated scalar below. Graduated sizing 1.0x–1.8x
        # (scalar 0.6→1.0), full size above 1.8x. Replaces binary 2.0x gate that blocked
        # all signals in bearish/wide-stop conditions.
        min_leverage_gate = getattr(self.config, "min_leverage_entry_gate", 1.0)
        LEVERAGE_FULL_SIZE = 2.5  # full position size at 2.5x+; graduated 1.0x→2.5x
        if lev_decision.leverage < min_leverage_gate:
            _reason = f"Below leverage gate: {lev_decision.leverage:.1f}x < {min_leverage_gate:.1f}x minimum"
            if _pt: _pt.record_gate(signal.symbol, "leverage_gate", False, lev_decision.leverage, min_leverage_gate, _reason)
            _log_rejection(signal, "leverage_gate", _reason)
            self._log_signal_filtered(signal, "leverage_gate", _reason)
            return FilterResult(
                approved=False, signal=signal,
                rejection_reason=_reason,
                metadata=meta,
            )

        # Graduated size reduction for sub-optimal leverage (1.0x→0.80 rm, 2.5x→1.0 rm)
        # Was 0.6 floor — too aggressive, compounded with other multipliers to near-zero.
        if lev_decision.leverage < LEVERAGE_FULL_SIZE:
            lev_scalar = 0.80 + (lev_decision.leverage - min_leverage_gate) / (LEVERAGE_FULL_SIZE - min_leverage_gate) * 0.20
            lev_decision = LeverageDecision(
                leverage=lev_decision.leverage,
                mode=lev_decision.mode,
                tier=lev_decision.tier,
                reason=lev_decision.reason,
                risk_multiplier=lev_decision.risk_multiplier * lev_scalar,
            )
            logger.info(f"[{signal.symbol}] Leverage gate graduated: {lev_decision.leverage:.1f}x → "
                        f"size scalar {lev_scalar:.2f} (rm={lev_decision.risk_multiplier:.2f})")

        if _pt: _pt.record_gate(signal.symbol, "leverage", True, lev_decision.leverage, min_leverage_gate)

        # CB override constraints: if CB tripped, cap leverage
        override_constraints = self.risk_mgr.get_override_constraints(signal.confidence)
        leverage = min(lev_decision.leverage, override_constraints["max_leverage"])
        risk_mult = lev_decision.risk_multiplier * override_constraints["size_multiplier"]
        if _pt: _pt.record_multiplier(signal.symbol, "base_risk_mult", risk_mult, "leverage_mgr+cb_override")

        # Stop-width-dependent leverage cap: prevent liquidation on tight stops.
        # Kelly sizing already controls risk-per-trade — this is a liquidation
        # safety net, not a risk control. Caps raised to allow Kelly-optimal leverage.
        stop_width_pct = abs(signal.entry - signal.sl) / signal.entry if signal.entry > 0 else 1.0
        is_short = signal.side == "SELL"
        if stop_width_pct < 0.005:  # < 0.5% stop
            stop_lev_cap = 8.0 if is_short else 10.0
        elif stop_width_pct < 0.010:  # < 1.0% stop
            stop_lev_cap = 12.0 if is_short else 15.0
        else:
            stop_lev_cap = 15.0 if is_short else 20.0
        if leverage > stop_lev_cap:
            logger.info(f"[{signal.symbol}] Leverage capped {leverage:.1f}x → {stop_lev_cap:.1f}x "
                        f"(stop width {stop_width_pct:.2%} too tight for {leverage:.1f}x, side={signal.side})")
            leverage = stop_lev_cap

        # Apply correlation-based size reduction if flagged
        corr_reduction = meta.get("correlation_size_reduction", 1.0)
        if corr_reduction < 1.0:
            risk_mult *= corr_reduction
            if _pt: _pt.record_multiplier(signal.symbol, "correlation_reduction", corr_reduction, "corr_guard")

        # Apply regime-based risk sizing: bet bigger where edge is proven
        try:
            from trading_config import get_regime_risk_mult
            _regime = signal.metadata.get("regime", "unknown")
            _regime_rm = get_regime_risk_mult(_regime)
            if _regime_rm <= 0:
                _reason = f"Regime '{_regime}' has 0 risk multiplier — blocked by data"
                _log_rejection(signal, "regime_block", _reason)
                self._log_signal_filtered(signal, "regime_block", _reason)
                return FilterResult(
                    approved=False, signal=signal,
                    rejection_reason=_reason,
                    metadata=meta,
                )
            if _regime_rm != 1.0:
                risk_mult *= _regime_rm
                meta["regime_risk_mult"] = _regime_rm
                if _pt: _pt.record_multiplier(signal.symbol, "regime_risk", _regime_rm, f"regime={_regime}")
            # Warn when a trade passes in the worst regime
            if _regime == "trending_bear":
                logger.warning(
                    f"[REGIME_WARN] {signal.symbol} {signal.side} passing in trending_bear "
                    f"regime (0% historical WR) — risk mult={_regime_rm:.2f}, "
                    f"conf={signal.confidence:.0f}%"
                )
        except ImportError:
            pass

        # Apply symbol-specific risk scaling (data-driven per-symbol edge)
        try:
            from trading_config import get_symbol_risk_mult
            _sym_rm = get_symbol_risk_mult(signal.symbol)
            if _sym_rm != 1.0:
                risk_mult *= _sym_rm
                meta["symbol_risk_mult"] = _sym_rm
                if _pt: _pt.record_multiplier(signal.symbol, "symbol_risk", _sym_rm, "symbol_edge")
        except ImportError:
            pass

        # Apply symbol+side risk scaling (penalize weak directional edge)
        # e.g., SOL LONG has 46% WR / -$93 avg vs SOL SHORT 62% WR / +$181 avg
        try:
            from trading_config import get_symbol_side_risk_mult
            _side_rm = get_symbol_side_risk_mult(signal.symbol, signal.side)
            if _side_rm != 1.0:
                risk_mult *= _side_rm
                meta["symbol_side_risk_mult"] = _side_rm
                if _pt: _pt.record_multiplier(signal.symbol, "symbol_side_risk", _side_rm, f"{signal.symbol}_{signal.side}")
                logger.info(
                    f"[{signal.symbol}] Symbol+side risk mult: {signal.side} → {_side_rm:.2f}x"
                )
        except ImportError:
            pass

        # Apply adaptive sizing (anti-martingale): size up when hot, down when cold.
        # Applied after quant rules and regime/symbol mults, before confidence sizing.
        if getattr(self.config, "adaptive_sizing_enabled", True):
            try:
                from execution.adaptive_risk import get_adaptive_sizer
                _sizer = get_adaptive_sizer(self.config)
                _adaptive_mult = _sizer.get_sizing_multiplier(signal.symbol)
                if _adaptive_mult != 1.0:
                    risk_mult *= _adaptive_mult
                    meta["adaptive_sizing_mult"] = _adaptive_mult
                    if _pt: _pt.record_multiplier(signal.symbol, "adaptive_sizing", _adaptive_mult, "anti_martingale")
                    meta["adaptive_sizing_heat"] = round(
                        _sizer.get_heat(signal.symbol), 3
                    )
                    logger.info(
                        f"[{signal.symbol}] ADAPTIVE SIZING: heat={meta['adaptive_sizing_heat']:.2f} "
                        f"→ {_adaptive_mult:.3f}x"
                    )
            except Exception as e:
                logger.debug(f"[ADAPTIVE_SIZER] Error: {e}")

        # Apply momentum sizing (win/loss streak from 2,172-signal analysis)
        # After 2 wins: 1.3x. After 2 losses: 0.35x. 75% vs 29% WR spread.
        try:
            from execution.momentum_tracker import get_momentum_tracker
            _mt = get_momentum_tracker()
            _mom_mult = _mt.get_multiplier(signal.symbol)
            if _mom_mult != 1.0:
                risk_mult *= _mom_mult
                meta["momentum_mult"] = _mom_mult
                meta["momentum_streak"] = _mt.get_streak(signal.symbol)
                if _pt: _pt.record_multiplier(signal.symbol, "momentum", _mom_mult, f"streak={meta['momentum_streak']}")
                logger.info(
                    f"[{signal.symbol}] MOMENTUM: streak={meta['momentum_streak']:+d} "
                    f"-> {_mom_mult:.2f}x"
                )
            # On extreme losing streak (3+), reduce size further instead of hard skip
            # Hard skip was blocking test signals. Soft reduction is safer.
            if _mt.get_streak(signal.symbol) <= -3:
                risk_mult *= 0.3  # Additional 70% reduction on top of momentum_mult
                meta["momentum_extreme_reduction"] = True
        except Exception as e:
            logger.debug(f"[MOMENTUM] Error: {e}")

        # Apply solo proven strategy size override (half size for safety)
        _solo_rm = signal.metadata.get("risk_mult_override")
        if _solo_rm is not None:
            risk_mult *= _solo_rm
            meta["solo_risk_mult"] = _solo_rm

        # Apply committee size multiplier (from live_analyst thesis, flag-gated)
        _comm_mult = meta.get("committee_size_mult")
        if _comm_mult is not None and _comm_mult != 1.0:
            risk_mult *= _comm_mult
            if _pt: _pt.record_multiplier(signal.symbol, "committee_size_mult", _comm_mult, "committee_reader")

        # ── Confidence Calibration ──
        # Apply calibration BEFORE confidence-based sizing so sizing uses
        # calibrated (realistic) confidence, not raw (overconfident) values.
        # Fixes: 90-100% raw confidence signals losing, 70-79% winning.
        if getattr(self.config, "confidence_calibration_enabled", True):
            try:
                from llm.confidence_calibrator import ConfidenceCalibrator
                _cal_window = getattr(self.config, "calibration_window", 50)
                _calibrator = ConfidenceCalibrator(calibration_window=_cal_window)
                _raw_conf = signal.confidence
                _calibrated = _calibrator.calibrate(
                    _raw_conf, symbol=signal.symbol
                )
                if abs(_calibrated - _raw_conf) > 1.0:
                    signal = copy.copy(signal)
                    signal.confidence = _calibrated
                    meta["raw_confidence"] = round(_raw_conf, 1)
                    meta["calibrated_confidence"] = round(_calibrated, 1)
                    meta["calibration_adjustment"] = round(
                        _calibrated - _raw_conf, 1
                    )
                    logger.info(
                        f"[{signal.symbol}] CALIBRATION: "
                        f"{_raw_conf:.0f}% -> {_calibrated:.0f}%"
                    )
            except Exception as e:
                logger.debug(f"[CALIBRATION] Error: {e}")

        # ── Time-of-day sizing ──
        # Apply session/day-of-week multiplier from time_sizing.py.
        # DEAD hours (03-06, 09-10, 17) get 0.5x, QUIET get 0.7x, etc.
        # This runs in both live and backtest paths via RiskFilterChain.
        try:
            from execution.time_sizing import get_full_time_multiplier
            _time_info = get_full_time_multiplier(side=signal.side)
            _time_mult = _time_info["multiplier"]
            if _time_mult != 1.0:
                risk_mult *= _time_mult
                meta["time_sizing_mult"] = round(_time_mult, 3)
                meta["time_sizing_session"] = _time_info["session"]
                meta["time_sizing_reasons"] = _time_info["reasons"]
                if _pt: _pt.record_multiplier(signal.symbol, "time_sizing", _time_mult, _time_info.get("session", ""))
        except Exception as e:
            logger.debug(f"[TIME_SIZING] Error: {e}")

        # Confidence-based sizing: bet bigger on high-conviction signals.
        # Data: 80-89% confidence = PF 7.89 (60% WR, +$2,202).
        # 70-79% = PF 0.70 (losing). Below 70% = PF 0.0.
        _conf = signal.confidence
        _regime = signal.metadata.get("regime", "unknown")
        # Confidence-based sizing with regime awareness.
        # Data: 80-89% = PF 9.77. 90%+ = 0% WR across all regimes except consolidation.
        # Extended exhaustion protection to ALL non-consolidation regimes (was trending-only).
        # Lowered from 0.5x to 0.4x: 90-100% confidence has -$1,792 total PnL.
        # Confidence sizing: leverage tiers already scale by confidence, so this
        # layer only applies conviction BOOSTS, not penalties. Previous version
        # double-penalized low confidence (leverage rm=0.3 × conf sizing 0.5 = 0.15).
        # 2026-06-08: 90% exhaustion penalty REMOVED at base. Risk Agent (LLM)
        # decides if exhaustion penalty applies based on regime + momentum +
        # alpha-ops context. Old code hardcoded 0.7x for ALL 90%+ signals in
        # non-consolidation regimes — that penalized genuine high-conviction
        # bull-run signals. Now surfaced as advisory only.
        if _conf >= 90 and _regime not in ("consolidation",):
            # Advisory only — no mechanical penalty. LLM sees this in metadata.
            meta["exhaustion_signal_advisory"] = "conf>=90 in non-consolidation — historically 22% WR. LLM should weigh."
            meta["confidence_sizing"] = "exhaustion_advisory_no_penalty"
        elif _conf >= 85:
            risk_mult *= 1.5  # High conviction — 85%+ is PF=17-22 sweet spot
            meta["confidence_sizing"] = "high_conviction_1.5x"
        elif _conf >= 80:
            risk_mult *= 1.2  # Strong conviction boost
            meta["confidence_sizing"] = "strong_1.2x"
        elif _conf >= 75:
            risk_mult *= 1.1  # Slight boost
            meta["confidence_sizing"] = "good_1.1x"
        else:
            pass  # Below 75%: no penalty. Leverage tier already handles this.
            meta["confidence_sizing"] = "neutral_1.0x"

        if _pt and meta.get("confidence_sizing"): _pt.record_multiplier(signal.symbol, "confidence_sizing", {"exhaustion_0.7x": 0.7, "high_conviction_1.5x": 1.5, "strong_1.2x": 1.2, "good_1.1x": 1.1, "neutral_1.0x": 1.0}.get(meta["confidence_sizing"], 1.0), meta["confidence_sizing"])

        # Apply quant conviction risk multiplier (Rule 4: size up on proven setups)
        if quant["risk_mult_boost"] != 1.0:
            risk_mult *= quant["risk_mult_boost"]
            meta["quant_risk_mult_boost"] = quant["risk_mult_boost"]
            if _pt: _pt.record_multiplier(signal.symbol, "quant_conviction", quant["risk_mult_boost"], "proven_setup")

        # Floor: prevent multiplicative chain from crushing risk_mult to near-zero.
        # Full Kelly approach: floor at 0.50 so every trade risks at least half
        # of the intended amount. On $1k at 2.5% risk = $12.50 minimum risk.
        risk_mult = max(risk_mult, 0.50)

        meta["leverage"] = leverage
        meta["leverage_tier"] = lev_decision.tier
        meta["risk_multiplier"] = round(risk_mult, 2)
        meta["num_agree"] = num_strategies_agree

        # Gate 5b: REMOVED — leverage-scaled EV floor was double-filtering.
        # Gate 1d already checks EV. This gate rejected 80% of winners.
        # Data: only 20% of risk_filter_chain rejections were correct.
        # Kept as comment for audit trail. Removed 2026-03-24.

        # Gate 6: Liquidation safety
        side_str = "BUY" if signal.side == "BUY" else "SELL"
        notional_est = equity * leverage * 0.5  # rough estimate
        liq_check = self.leverage_mgr.validate_stop_vs_liquidation(
            entry=signal.entry,
            stop_loss=signal.sl,
            side=side_str,
            leverage=leverage,
            notional_usd=notional_est,
        )
        if not liq_check["safe"]:
            _reason = f"SL ({signal.sl}) beyond liquidation ({liq_check['liquidation_price']:.2f})"
            if _pt: _pt.record_gate(signal.symbol, "liquidation", False, signal.sl, liq_check.get("liquidation_price", 0), _reason)
            _log_rejection(signal, "liquidation", _reason)
            self._log_signal_filtered(signal, "liquidation", _reason)
            return FilterResult(
                approved=False, signal=signal,
                rejection_reason=_reason,
            )
        meta["liq_gap_pct"] = round(liq_check.get("gap_pct", 0), 4)

        # Gate 6: Position sizing
        qty = self.risk_mgr.calculate_qty(
            entry=signal.entry,
            stop_loss=signal.sl,
            leverage=leverage,
            risk_multiplier=risk_mult,
            symbol=signal.symbol,
        )
        if qty <= 0:
            _reason = "Position size zero (stop width too narrow or equity too low)"
            if _pt: _pt.record_gate(signal.symbol, "sizing", False, qty, 0, _reason)
            _log_rejection(signal, "sizing", _reason)
            self._log_signal_filtered(signal, "sizing", _reason)
            return FilterResult(
                approved=False, signal=signal,
                rejection_reason=_reason,
            )

        if _pt: _pt.record_gate(signal.symbol, "all_gates", True, qty, 0, f"lev={leverage:.1f}x rm={risk_mult:.2f}")

        return FilterResult(
            approved=True,
            signal=signal,
            leverage=leverage,
            risk_multiplier=risk_mult,
            position_qty=qty,
            metadata=meta,
        )

    def evaluate_annotated(
        self,
        signal: Signal,
        equity: float,
        num_strategies_agree: int,
        total_strategies: int,
        current_open_count: int = 0,
        current_extreme_count: int = 0,
        risk_tier: str = "medium",
        cb_conf_override_pct: float = 0.92,
        open_positions: Optional[Dict[str, Any]] = None,
        portfolio_risk_engine=None,
    ) -> AnnotatedSignal:
        """Run a signal through all gates, converting soft gates to annotations.

        Hard gates (safety-critical) still reject immediately:
          - Signal.is_valid (structurally broken)
          - Circuit breaker
          - Max open positions
          - Liquidation safety

        Soft gates produce annotations instead of rejecting:
          - R:R floor, fee-drag, EV floor, correlation, leverage-scaled EV
        """
        annotations: List[FilterAnnotation] = []
        meta: Dict[str, Any] = {}

        # ── Quant Rules: apply proven statistical edges (annotated path) ──
        quant = apply_quant_rules(
            signal=signal,
            config=self.config,
            num_strategies_agree=num_strategies_agree,
            now=None,
        )
        if quant["rules_applied"]:
            signal = copy.copy(signal)
            boosted_conf = signal.confidence * quant["confidence_boost"]
            max_conf = getattr(self.config, "max_ensemble_confidence", 95.0)
            signal.confidence = min(boosted_conf, max_conf)
            meta["quant_rules"] = quant["rules_applied"]
            meta["quant_confidence_boost"] = quant["confidence_boost"]
            meta.update(quant["meta"])

        # ── Setup exit metadata (for position manager trailing logic) ──
        # Fixed % TPs don't work — BTC moves 0.3%/h, a 1.5% TP is a coinflip.
        # Instead, tag the signal with exit STRATEGY (trail ATR, TP1 at 1R, etc.)
        # and let the position manager handle it. Don't override strategy TPs here.
        try:
            from trading_config import get_setup_exit
            _exit_profile = get_setup_exit(signal.symbol, signal.side)
            if _exit_profile:
                meta["setup_exit_strategy"] = _exit_profile
                meta["setup_edge_tier"] = _exit_profile.get("edge", "unknown")
                meta["setup_tp1_close_pct"] = _exit_profile.get("tp1_close_pct", 0.5)
                meta["setup_trail_atr"] = _exit_profile.get("trail_atr", 1.0)
                meta["setup_time_stop_h"] = _exit_profile.get("time_stop_h", 12)
        except Exception as e:
            logger.debug(f"[SETUP-EXIT] Error: {e}")

        # ── Hard Gate: Signal validity ──
        if not signal.is_valid:
            return AnnotatedSignal(
                signal=signal,
                hard_rejected=True,
                hard_rejection_reason=f"Invalid: sw={signal.stop_width_pct:.4f} rr={signal.risk_reward_tp1:.2f}",
            )

        # ── Soft Gate: R:R floor ──
        min_rr = getattr(self.config, "min_signal_rr", 1.0)
        rr = signal.risk_reward_tp1
        meta["rr_tp1"] = round(rr, 2)
        meta["rr_tp2"] = round(signal.risk_reward_tp2, 2)
        # DECHOKE 2: rr_floor enforcement deleted (zero live firings); the
        # annotation stays informational — max severity "warning" so it can
        # never mark a signal soft-rejected for a gate that no longer exists.
        annotations.append(FilterAnnotation(
            gate="rr_floor",
            passed=rr >= min_rr,
            severity="warning" if rr < min_rr * 1.15 else "ok",
            value=round(rr, 2),
            threshold=min_rr,
            detail=f"rr={rr:.2f} vs {min_rr:.1f}",
        ))

        # ── Soft Gate: Fee-drag (regime-aware slippage) ──
        fee_bps = getattr(self.config, "taker_fee_bps", 4)
        _regime_slip_ann = {
            "trending_bull": 1, "trending_bear": 2, "trend": 1,
            "consolidation": 1, "range": 1,
            "high_volatility": 4, "panic": 6,
            "low_liquidity": 5, "news_dislocation": 5,
        }
        _sig_regime_ann = (signal.metadata or {}).get("regime", "unknown")
        _extra_slip_ann = _regime_slip_ann.get(_sig_regime_ann, 2)
        round_trip_fee_pct = (fee_bps * 2 + _extra_slip_ann) / 10000.0
        stop_pct = signal.stop_width_pct
        if stop_pct > 0:
            fee_drag_pct = round_trip_fee_pct / stop_pct
            meta["fee_drag_pct"] = round(fee_drag_pct * 100, 1)
            _n_agree_ann = signal.metadata.get("num_agree", 1) if signal.metadata else 1
            max_fee_drag = 0.35 if _n_agree_ann >= 3 else 0.30
            # DECHOKE 2: fee_drag enforcement deleted (2 firings ever, both
            # 2026-06-02); annotation stays informational — max "warning".
            annotations.append(FilterAnnotation(
                gate="fee_drag",
                passed=fee_drag_pct <= max_fee_drag,
                severity="warning" if fee_drag_pct > max_fee_drag * 0.8 else "ok",
                value=round(fee_drag_pct * 100, 1),
                threshold=round(max_fee_drag * 100, 0),
                detail=f"fd={fee_drag_pct:.0%} vs {max_fee_drag:.0%}",
            ))

        # ── Soft Gate: EV floor ──
        ev = signal.metadata.get("ev_per_dollar") if signal.metadata else None
        min_ev = getattr(self.config, "min_signal_ev", 0.10)
        if stop_pct > 0 and stop_pct < 0.004:
            min_ev = max(min_ev, 0.25)
        elif stop_pct > 0 and stop_pct < 0.006:
            min_ev = max(min_ev, 0.22)
        if ev is not None:
            meta["ev_per_dollar"] = ev
            # DECHOKE 2: ev_floor enforcement deleted (zero live firings;
            # ev_per_dollar input is anti-predictive per FALLACY_AUDIT D4);
            # annotation stays informational — max "warning".
            annotations.append(FilterAnnotation(
                gate="ev_floor",
                passed=ev >= min_ev,
                severity="warning" if ev < min_ev * 1.2 else "ok",
                value=round(ev, 3),
                threshold=min_ev,
                detail=f"ev={ev:.3f} vs {min_ev:.2f}",
            ))

        # ── Hard Gate: Circuit breaker ──
        if not self.risk_mgr.is_trading_allowed(
            confidence=signal.confidence,
            cb_conf_override_pct=cb_conf_override_pct,
        ):
            return AnnotatedSignal(
                signal=signal,
                annotations=annotations,
                hard_rejected=True,
                hard_rejection_reason="Circuit breaker active",
                filter_metadata=meta,
            )

        # ── Hard Gate: Max open positions ──
        if current_open_count >= self.config.max_open_positions:
            return AnnotatedSignal(
                signal=signal,
                annotations=annotations,
                hard_rejected=True,
                hard_rejection_reason=f"Max positions ({self.config.max_open_positions}) reached",
                filter_metadata=meta,
            )

        # ── Soft Gate: Correlation guard ──
        if portfolio_risk_engine and open_positions and current_open_count >= 2:
            try:
                corr_matrix = portfolio_risk_engine.compute_correlation_matrix()
                if corr_matrix:
                    positions_map = {}
                    for sym, pos in open_positions.items():
                        side = getattr(pos, 'side', 'LONG').lower()
                        positions_map[sym] = side
                    proposed_side = "long" if signal.side == "BUY" else "short"
                    positions_map[signal.symbol] = proposed_side

                    cluster_risk = corr_matrix.get_cluster_risk(positions_map)
                    meta["cluster_risk"] = round(cluster_risk, 3)

                    # Market-wide move override (same logic as hard gate)
                    unique_symbols = set(positions_map.keys())
                    directions = set(positions_map.values())
                    is_market_wide = (
                        len(unique_symbols) >= 3
                        and len(directions) == 1
                    )

                    corr_passed = cluster_risk < 0.85 or is_market_wide
                    if is_market_wide and cluster_risk >= 0.85:
                        meta["corr_guard_override"] = "market_wide_move"
                        meta["market_wide_direction"] = list(directions)[0]
                        meta["market_wide_symbols"] = len(unique_symbols)

                    annotations.append(FilterAnnotation(
                        gate="correlation",
                        passed=corr_passed,
                        severity="ok" if corr_passed else (
                            "reject" if cluster_risk >= 0.85 else (
                                "warning" if cluster_risk >= 0.70 else "ok")),
                        value=round(cluster_risk, 3),
                        threshold=0.85,
                        detail=(f"corr={cluster_risk:.2f} (market-wide override)"
                                if is_market_wide and cluster_risk >= 0.85
                                else f"corr={cluster_risk:.2f}"),
                    ))

                    if cluster_risk >= 0.70 and not is_market_wide:
                        meta["correlation_size_reduction"] = 0.7
            except Exception as e:
                logger.debug(f"[CORR-GUARD] Error: {e}")

        # ── Leverage decision (needed for sizing + lev-EV gate) ──
        lev_decision = self.leverage_mgr.decide(
            confidence=signal.confidence,
            num_strategies_agree=num_strategies_agree,
            total_strategies=total_strategies,
            risk_tier=risk_tier,
            current_extreme_count=current_extreme_count,
            symbol=signal.symbol,
        )

        if lev_decision.leverage <= 0:
            return AnnotatedSignal(
                signal=signal,
                annotations=annotations,
                hard_rejected=True,
                hard_rejection_reason=f"Leverage denied: {lev_decision.reason}",
                filter_metadata=meta,
            )

        # Graduated leverage eligibility gate (annotated path)
        # Floor at 1.0x — allows 2-agree signals (which return 1.0x leverage) to pass
        # with reduced sizing. Graduated sizing 1.0x–2.5x (scalar 0.80→1.0).
        min_leverage_gate = getattr(self.config, "min_leverage_entry_gate", 1.0)
        LEVERAGE_FULL_SIZE = 2.5
        if lev_decision.leverage < min_leverage_gate:
            return AnnotatedSignal(
                signal=signal,
                annotations=annotations,
                hard_rejected=True,
                hard_rejection_reason=f"Below leverage gate: {lev_decision.leverage:.1f}x < {min_leverage_gate:.1f}x",
                filter_metadata=meta,
            )

        if lev_decision.leverage < LEVERAGE_FULL_SIZE:
            lev_scalar = 0.80 + (lev_decision.leverage - min_leverage_gate) / (LEVERAGE_FULL_SIZE - min_leverage_gate) * 0.20
            lev_decision = LeverageDecision(
                leverage=lev_decision.leverage,
                mode=lev_decision.mode,
                tier=lev_decision.tier,
                reason=lev_decision.reason,
                risk_multiplier=lev_decision.risk_multiplier * lev_scalar,
            )
            logger.info(f"[{signal.symbol}] Leverage gate graduated (annotated): {lev_decision.leverage:.1f}x → "
                        f"size scalar {lev_scalar:.2f} (rm={lev_decision.risk_multiplier:.2f})")

        override_constraints = self.risk_mgr.get_override_constraints(signal.confidence)
        leverage = min(lev_decision.leverage, override_constraints["max_leverage"])
        risk_mult = lev_decision.risk_multiplier * override_constraints["size_multiplier"]

        # Stop-width-dependent leverage cap (annotated path)
        # Liquidation safety net — Kelly sizing controls risk, this prevents liq.
        stop_width_pct = abs(signal.entry - signal.sl) / signal.entry if signal.entry > 0 else 1.0
        is_short = signal.side == "SELL"
        if stop_width_pct < 0.005:  # < 0.5% stop
            stop_lev_cap = 8.0 if is_short else 10.0
        elif stop_width_pct < 0.010:  # < 1.0% stop
            stop_lev_cap = 12.0 if is_short else 15.0
        else:
            stop_lev_cap = 15.0 if is_short else 20.0
        if leverage > stop_lev_cap:
            logger.info(f"[{signal.symbol}] Leverage capped {leverage:.1f}x → {stop_lev_cap:.1f}x "
                        f"(stop width {stop_width_pct:.2%} too tight for {leverage:.1f}x, side={signal.side})")
            leverage = stop_lev_cap

        corr_reduction = meta.get("correlation_size_reduction", 1.0)
        if corr_reduction < 1.0:
            risk_mult *= corr_reduction

        # Apply regime-based risk sizing (annotated path)
        try:
            from trading_config import get_regime_risk_mult
            _regime = signal.metadata.get("regime", "unknown")
            _regime_rm = get_regime_risk_mult(_regime)
            if _regime_rm <= 0:
                return AnnotatedSignal(
                    signal=signal,
                    hard_rejected=True,
                    hard_rejection_reason=f"Regime '{_regime}' blocked (0 risk mult)",
                )
            if _regime_rm != 1.0:
                risk_mult *= _regime_rm
                meta["regime_risk_mult"] = _regime_rm
        except ImportError:
            pass

        # Apply symbol-specific risk scaling (annotated path)
        try:
            from trading_config import get_symbol_risk_mult
            _sym_rm = get_symbol_risk_mult(signal.symbol)
            if _sym_rm != 1.0:
                risk_mult *= _sym_rm
                meta["symbol_risk_mult"] = _sym_rm
        except ImportError:
            pass

        # Apply symbol+side risk scaling (annotated path)
        try:
            from trading_config import get_symbol_side_risk_mult
            _side_rm = get_symbol_side_risk_mult(signal.symbol, signal.side)
            if _side_rm != 1.0:
                risk_mult *= _side_rm
                meta["symbol_side_risk_mult"] = _side_rm
                logger.info(
                    f"[{signal.symbol}] Symbol+side risk mult: {signal.side} → {_side_rm:.2f}x"
                )
        except ImportError:
            pass

        # Apply adaptive sizing (anti-martingale, annotated path)
        if getattr(self.config, "adaptive_sizing_enabled", True):
            try:
                from execution.adaptive_risk import get_adaptive_sizer
                _sizer = get_adaptive_sizer(self.config)
                _adaptive_mult = _sizer.get_sizing_multiplier(signal.symbol)
                if _adaptive_mult != 1.0:
                    risk_mult *= _adaptive_mult
                    meta["adaptive_sizing_mult"] = _adaptive_mult
                    meta["adaptive_sizing_heat"] = round(
                        _sizer.get_heat(signal.symbol), 3
                    )
            except Exception as e:
                logger.debug(f"[ADAPTIVE_SIZER] Error: {e}")

        # Apply solo proven strategy size override (half size for safety)
        _solo_rm = signal.metadata.get("risk_mult_override")
        if _solo_rm is not None:
            risk_mult *= _solo_rm
            meta["solo_risk_mult"] = _solo_rm

        # ── Confidence Calibration (annotated path) ──
        if getattr(self.config, "confidence_calibration_enabled", True):
            try:
                from llm.confidence_calibrator import ConfidenceCalibrator
                _cal_window = getattr(self.config, "calibration_window", 50)
                _calibrator = ConfidenceCalibrator(calibration_window=_cal_window)
                _raw_conf = signal.confidence
                _calibrated = _calibrator.calibrate(
                    _raw_conf, symbol=signal.symbol
                )
                if abs(_calibrated - _raw_conf) > 1.0:
                    signal = copy.copy(signal)
                    signal.confidence = _calibrated
                    meta["raw_confidence"] = round(_raw_conf, 1)
                    meta["calibrated_confidence"] = round(_calibrated, 1)
                    meta["calibration_adjustment"] = round(
                        _calibrated - _raw_conf, 1
                    )
            except Exception as e:
                logger.debug(f"[CALIBRATION] Error: {e}")

        # ── Time-of-day sizing (annotated path) ──
        try:
            from execution.time_sizing import get_full_time_multiplier
            _time_info = get_full_time_multiplier(side=signal.side)
            _time_mult = _time_info["multiplier"]
            if _time_mult != 1.0:
                risk_mult *= _time_mult
                meta["time_sizing_mult"] = round(_time_mult, 3)
                meta["time_sizing_session"] = _time_info["session"]
                meta["time_sizing_reasons"] = _time_info["reasons"]
        except Exception as e:
            logger.debug(f"[TIME_SIZING] Error: {e}")

        # Confidence-based sizing: bet bigger on high-conviction signals (annotated path).
        # Data: 80-89% confidence = PF 7.89 (60% WR, +$2,202).
        # 70-79% = PF 0.70 (losing). Below 70% = PF 0.0.
        _conf = signal.confidence
        _regime = signal.metadata.get("regime", "unknown")
        if _conf >= 90 and _regime not in ("consolidation",):
            risk_mult *= 0.4  # Exhaustion protection: 90%+ confidence = overconfident signal
            meta["confidence_sizing"] = "exhaustion_reduced_0.4x"
        elif _conf >= 85:
            risk_mult *= 1.5  # High conviction — 85%+ is PF=17-22 sweet spot.
            meta["confidence_sizing"] = "high_conviction_1.5x"
        elif _conf >= 80:
            risk_mult *= 1.15  # Strong conviction.
            meta["confidence_sizing"] = "strong_1.15x"
        elif _conf >= 75:
            pass  # 75-79% stays at 1.0x (neutral zone)
        elif _conf >= 70:
            risk_mult *= 0.7  # 70-74%: marginal — reduce size 30%
            meta["confidence_sizing"] = "marginal_0.7x"
        else:
            risk_mult *= 0.5  # Below 70% — reduce size 50%
            meta["confidence_sizing"] = "low_conviction_0.5x"

        # Apply quant conviction risk multiplier (annotated path)
        if quant["risk_mult_boost"] != 1.0:
            risk_mult *= quant["risk_mult_boost"]
            meta["quant_risk_mult_boost"] = quant["risk_mult_boost"]

        # Floor: prevent multiplicative chain from crushing risk_mult to near-zero (annotated path).
        # Raised from 0.12 to 0.25 for executability on small accounts.
        risk_mult = max(risk_mult, 0.25)

        meta["leverage"] = leverage
        meta["leverage_tier"] = lev_decision.tier
        meta["risk_multiplier"] = round(risk_mult, 2)
        meta["num_agree"] = num_strategies_agree

        # ── Soft Gate: Leverage-scaled EV floor ──
        # Was 0.22/0.28 — blocked 69% of leveraged signals including
        # proven setups (BTC SHORT trending_bear with +$55 live edge).
        # System runs at 35% WR: win_prob=0.35 at R:R=2.0 gives EV=0.05.
        # Lowered to 0.08 (matches MIN_SIGNAL_EV) to stop double-blocking.
        ev = meta.get("ev_per_dollar")
        if ev is not None and leverage > 2.0:
            n_agree = meta.get("num_agree", 0)
            if leverage > 4.0:
                lev_ev_floor = 0.06 if n_agree >= 3 else 0.10
            else:
                lev_ev_floor = 0.08
            annotations.append(FilterAnnotation(
                gate="lev_ev_floor",
                passed=ev >= lev_ev_floor,
                severity="reject" if ev < lev_ev_floor else "ok",
                value=round(ev, 3),
                threshold=lev_ev_floor,
                detail=f"ev={ev:.3f} vs {lev_ev_floor:.2f} (at {leverage:.1f}x)",
            ))

        # ── Hard Gate: Liquidation safety ──
        side_str = "BUY" if signal.side == "BUY" else "SELL"
        notional_est = equity * leverage * 0.5
        liq_check = self.leverage_mgr.validate_stop_vs_liquidation(
            entry=signal.entry,
            stop_loss=signal.sl,
            side=side_str,
            leverage=leverage,
            notional_usd=notional_est,
        )
        if not liq_check["safe"]:
            return AnnotatedSignal(
                signal=signal,
                annotations=annotations,
                hard_rejected=True,
                hard_rejection_reason=f"SL beyond liquidation ({liq_check['liquidation_price']:.2f})",
                filter_metadata=meta,
            )
        meta["liq_gap_pct"] = round(liq_check.get("gap_pct", 0), 4)

        # ── Position sizing ──
        qty = self.risk_mgr.calculate_qty(
            entry=signal.entry,
            stop_loss=signal.sl,
            leverage=leverage,
            risk_multiplier=risk_mult,
            symbol=signal.symbol,
        )
        if qty <= 0:
            return AnnotatedSignal(
                signal=signal,
                annotations=annotations,
                hard_rejected=True,
                hard_rejection_reason="Position size zero",
                filter_metadata=meta,
            )

        return AnnotatedSignal(
            signal=signal,
            annotations=annotations,
            hard_rejected=False,
            filter_metadata=meta,
            leverage=leverage,
            risk_multiplier=risk_mult,
            position_qty=qty,
        )


class SafetyFilterChain:
    """Safety-only gates that run BEFORE the LLM pipeline.

    Used in LLM_FIRST_MODE: after these pass, the signal goes directly
    to the multi-agent LLM pipeline which handles ALL quality/sizing
    decisions. This replaces the 47 mechanical gates in RiskFilterChain.

    Safety gates (never bypassed):
      1. Signal structural validity (is_valid)
      2. Circuit breaker (daily loss, consecutive losses)
      3. Max open positions
      4. Duplicate position guard
      5. Liquidation safety (worst-case leverage check)
    """

    def __init__(self, risk_mgr, leverage_mgr, config):
        self.risk_mgr = risk_mgr
        self.leverage_mgr = leverage_mgr
        self.config = config

    @staticmethod
    def _log_signal_filtered(signal: Signal, gate: str, reason: str):
        """Log a SIGNAL_FILTERED event to the TradeEventLogger."""
        try:
            tel = _get_tel()
            if tel is None:
                return
            tel.log(
                "SIGNAL_FILTERED",
                signal.symbol,
                side=signal.side,
                strategy=getattr(signal, "strategy", ""),
                confidence=signal.confidence,
                entry=signal.entry,
                sl=getattr(signal, "sl", 0.0),
                reason=f"[safety:{gate}] {reason}",
                regime=(signal.metadata or {}).get("regime", ""),
            )
        except Exception:
            pass

    def evaluate(
        self,
        signal: Signal,
        equity: float,
        current_open_count: int = 0,
        open_positions: Optional[Dict[str, Any]] = None,
        cb_conf_override_pct: float = 0.92,
    ) -> FilterResult:
        """Run a signal through safety-only gates.

        Returns FilterResult with approved=True if the signal is structurally
        safe for the LLM to evaluate. No quality or sizing decisions are made.
        The LLM pipeline handles confidence, R:R, EV, fees, sizing, leverage.
        """
        meta = {}

        # ── Gate 1: Signal structural validity ──
        if not signal.is_valid:
            _reason = (
                f"Invalid signal: stop_width_pct={signal.stop_width_pct:.4f}, "
                f"rr={signal.risk_reward_tp1:.2f}"
            )
            _log_rejection(signal, "safety_validity", _reason)
            self._log_signal_filtered(signal, "validity", _reason)
            return FilterResult(
                approved=False, signal=signal, rejection_reason=_reason,
                metadata=meta,
            )

        # ── Gate 2: Circuit breaker ──
        if not self.risk_mgr.is_trading_allowed(
            confidence=signal.confidence,
            cb_conf_override_pct=cb_conf_override_pct,
        ):
            _reason = "Circuit breaker active"
            _log_rejection(signal, "safety_circuit_breaker", _reason)
            self._log_signal_filtered(signal, "circuit_breaker", _reason)
            return FilterResult(
                approved=False, signal=signal, rejection_reason=_reason,
                metadata=meta,
            )

        # ── Gate 3: Max open positions ──
        max_pos = self.config.max_open_positions
        if current_open_count >= max_pos:
            _reason = f"Max positions ({max_pos}) reached"
            _log_rejection(signal, "safety_max_positions", _reason)
            self._log_signal_filtered(signal, "max_positions", _reason)
            return FilterResult(
                approved=False, signal=signal, rejection_reason=_reason,
                metadata=meta,
            )

        # ── Gate 4: Duplicate position guard ──
        if open_positions and signal.symbol in open_positions:
            _existing = open_positions[signal.symbol]
            _ex_side = getattr(_existing, 'side', 'unknown')
            _ex_entry = getattr(_existing, 'entry', 0.0)
            _reason = (
                f"Duplicate position: {signal.symbol} already has open "
                f"{_ex_side} (entry={_ex_entry})"
            )
            _log_rejection(signal, "safety_duplicate", _reason)
            self._log_signal_filtered(signal, "duplicate_position", _reason)
            return FilterResult(
                approved=False, signal=signal, rejection_reason=_reason,
                metadata=meta,
            )

        # ── Gate 5: Liquidation safety (practical-max check) ──
        # Check at the practical leverage ceiling (10x), not the theoretical max.
        # The Risk Agent uses 3-5x standard / 6-10x high conviction in overdrive mode.
        # Checking at config.max_leverage (15x) was blocking wide-ATR-stop signals for
        # volatile assets (HYPE, SOL) that the LLM would correctly size at 3-8x.
        # At 10x, a 7% stop is still safely above liquidation; at 15x it is not.
        max_lev = getattr(self.config, "max_leverage", 25.0)
        liq_check_lev = min(max_lev, 10.0)  # practical ceiling: LLM max is 6-10x in overdrive
        side_str = "BUY" if signal.side == "BUY" else "SELL"
        notional_est = equity * liq_check_lev * 0.5
        liq_check = self.leverage_mgr.validate_stop_vs_liquidation(
            entry=signal.entry,
            stop_loss=signal.sl,
            side=side_str,
            leverage=liq_check_lev,
            notional_usd=notional_est,
        )
        if not liq_check["safe"]:
            _reason = (
                f"SL ({signal.sl}) beyond liquidation "
                f"({liq_check['liquidation_price']:.2f}) at practical max leverage {liq_check_lev}x"
            )
            _log_rejection(signal, "safety_liquidation", _reason)
            self._log_signal_filtered(signal, "liquidation", _reason)
            return FilterResult(
                approved=False, signal=signal, rejection_reason=_reason,
                metadata=meta,
            )
        meta["liq_gap_pct"] = round(liq_check.get("gap_pct", 0), 4)
        meta["liq_check_leverage"] = liq_check_lev  # inform LLM of the leverage ceiling used

        # ── PASS: Signal is structurally safe ──
        # Attach useful metadata for the LLM pipeline to consume.
        meta["stop_width_pct"] = round(signal.stop_width_pct, 6)
        meta["rr_tp1"] = round(signal.risk_reward_tp1, 2)
        meta["rr_tp2"] = round(signal.risk_reward_tp2, 2)
        meta["equity"] = equity
        meta["open_positions_count"] = current_open_count

        logger.info(
            f"[{signal.symbol}] SAFETY PASS: {signal.side} "
            f"conf={signal.confidence:.0f}% rr={signal.risk_reward_tp1:.2f} "
            f"→ forwarding to LLM pipeline"
        )

        return FilterResult(
            approved=True,
            signal=signal,
            metadata=meta,
        )
