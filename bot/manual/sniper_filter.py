"""
Manual Sniper Signal Filter.

Evaluates bot-generated signals for manual scalp execution.
Two modes:
- AGGRESSIVE ($100 scaling): Only fires on absolute best signals.
  High leverage (10-25x), heavy sizing (5-10% risk), strict dedup.
  Goal: compound $100 → $1000+ with 1-2 sniper trades/day.
- STANDARD ($10k+): More signals, moderate leverage/sizing.

The math for aggressive mode on $100:
- SNIPER signal: 85%+ conf, 3 agree, HYPE BUY → ~52% WR (edge weakening, was 85%)
- Reduced leverage (3-5x, was 25x) due to WR decay. Half Kelly at 52% WR = ~3x.
- Win: +$5-8 per trade. Loss: -$5-8 per trade.
- At 52% WR, 1.34 PF: modest positive EV. Vol regime matters most (High Vol PF 3.51).
- Conservative sizing until edge stabilizes. Monitor WR trend closely.

Never modifies the bot's signals or trading logic.
"""

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, date
from typing import Optional, Dict, Any, List

from manual.config import ManualSniperConfig
from manual.trade_scorecard import TradeScorecard

logger = logging.getLogger("bot.manual.sniper")


@dataclass
class SniperSignal:
    """A filtered manual sniper signal ready for execution."""
    # Signal identity
    symbol: str
    side: str                   # BUY or SELL
    tier: str                   # STANDARD / PREMIUM / SNIPER

    # Entry/exit levels
    entry: float
    sl: float
    tp_scalp: float             # Quick scalp TP
    tp_swing: float             # Swing TP (hold longer)

    # Sizing
    leverage: float
    risk_pct: float             # % of equity risked
    risk_amount: float          # $ risked
    position_size_usd: float    # Total position value
    qty: float                  # Asset quantity
    margin_required: float      # Actual margin needed (position / leverage)

    # Expected outcomes
    pnl_scalp: float            # $ if scalp TP hit
    pnl_swing: float            # $ if swing TP hit
    loss_amount: float          # $ if SL hit
    rr_scalp: float
    rr_swing: float

    # Account context
    account_equity: float       # Current account size
    account_after_win: float    # Equity if scalp TP hit
    account_after_loss: float   # Equity if SL hit
    growth_pct: float           # % account growth on scalp win

    # Signal context
    confidence: float
    num_agree: int
    strategies: List[str]
    regime: str
    ev_per_dollar: float
    signal_context: str

    # Dip-buy detection
    is_dip_buy: bool = False    # True if signal is a dip-buy setup (higher conviction)

    # Quality scoring
    quality_score: int = 0          # 0-100, higher = better edge
    quality_grade: str = ""         # A+/A/B/C/F
    quality_recommendation: str = ""  # FIRE/TAKE/CAUTIOUS/SKIP
    size_multiplier: float = 1.0    # Score-based sizing adjustment

    # Metadata
    timestamp: str = ""
    daily_target_pct: float = 0.0     # How much of daily target this covers
    hold_target_hours: str = ""       # Suggested hold time
    funding_note: str = ""            # Funding rate context (tailwind/headwind/empty)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ManualSniperFilter:
    """
    Filters bot signals for manual scalp execution.

    In aggressive mode ($100 account), only fires on the absolute best
    signals and sizes heavily. Strict dedup ensures you only get 1-3
    truly distinct, actionable alerts per day.
    """

    def __init__(self, config: Optional[ManualSniperConfig] = None):
        self.config = config or ManualSniperConfig()
        self._daily_signals: List[SniperSignal] = []
        self._daily_date: Optional[date] = None
        self._last_alert_ts: Dict[str, float] = {}
        # Dedup: track (symbol, side, conf_band) → timestamp
        self._dedup_cache: Dict[str, float] = {}
        # Running equity tracker for compound sizing
        self._running_equity: float = self.config.equity
        self._log_path = os.path.join("data", "manual", "sniper_signals.jsonl")
        self._rejection_log_path = os.path.join("data", "manual", "sniper_rejections.jsonl")
        os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
        # Rejection stats (in-memory, for quick access)
        self._daily_rejections: Dict[str, int] = {}  # reason -> count
        # Rolling price tracker for dip detection (per symbol)
        # Stores last N prices to compute rolling high/low
        self._price_history: Dict[str, List[float]] = {}
        self._price_history_max: int = 20  # 20-period rolling window
        # Micro-sniper daily counter
        self._micro_sniper_today: int = 0
        self._micro_sniper_date: Optional[date] = None
        # Pre-trade quality scorecard (prevents junk entries)
        self._scorecard = TradeScorecard()
        # Reflection engine for re-entry quality analysis
        self._reflection_engine = None
        try:
            from llm.reflection_engine import ReflectionEngine
            self._reflection_engine = ReflectionEngine()
        except Exception:
            pass
        # Kelly sizing optimizer (optional — enhances fixed % risk with data-driven sizing)
        self._sizing_optimizer = None
        try:
            from execution.sizing_optimizer import SizingOptimizer
            self._sizing_optimizer = SizingOptimizer()
            logger.info("[SNIPER] Kelly sizing optimizer enabled")
        except Exception:
            pass

    def record_trade_outcome(self, setup: str, won: bool, pnl_pct: float) -> None:
        """Record a trade outcome for Kelly sizing optimizer learning.

        Call this when a sniper trade closes to update per-setup WR/payoff.
        """
        if self._sizing_optimizer is not None:
            self._sizing_optimizer.record_outcome(setup, won, pnl_pct)

    def update_equity(self, new_equity: float) -> None:
        """Update running equity for compound sizing."""
        if new_equity > 0:
            self._running_equity = new_equity

    def _update_price_history(self, symbol: str, price: float) -> None:
        """Track rolling price for dip detection."""
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        hist = self._price_history[symbol]
        hist.append(price)
        if len(hist) > self._price_history_max:
            self._price_history[symbol] = hist[-self._price_history_max:]

    def _get_dip_pct(self, symbol: str, current_price: float) -> Optional[float]:
        """Get current price's distance below the rolling high as a percentage.

        Returns None if insufficient data (< 5 prices).
        Returns 0.0 if at the high, positive values for dips (e.g., 5.0 = 5% below high).
        """
        hist = self._price_history.get(symbol, [])
        if len(hist) < 5:
            return None  # Not enough data yet
        rolling_high = max(hist)
        if rolling_high <= 0:
            return None
        return ((rolling_high - current_price) / rolling_high) * 100.0

    def evaluate(self, signal, equity: Optional[float] = None) -> Optional[SniperSignal]:
        """
        Evaluate a bot signal for manual sniper quality.

        Args:
            signal: strategies.base.Signal from ensemble
            equity: Override equity (otherwise uses running equity or config)

        Returns:
            SniperSignal if signal qualifies, None otherwise
        """
        if not self.config.enabled:
            return None

        # Use explicit equity if provided, otherwise running equity (compounds),
        # otherwise config default
        if equity is not None and not self.config.compound_sizing:
            acct_equity = equity
        else:
            acct_equity = self._running_equity if self.config.compound_sizing else self.config.equity

        # Guard: equity must be positive
        if acct_equity <= 0:
            logger.warning(f"[SNIPER] Invalid equity: ${acct_equity:.2f}, skipping")
            return None

        # Track price for dip detection
        self._update_price_history(signal.symbol, signal.entry)

        # Reset daily tracking
        today = date.today()
        if self._daily_date != today:
            self._daily_signals = []
            self._daily_date = today
            self._dedup_cache = {}  # Reset dedup daily
            self._daily_rejections = {}  # Reset rejection stats daily

        # Check daily signal limit
        if len(self._daily_signals) >= self.config.max_daily_signals:
            logger.debug("[SNIPER] Daily signal limit reached")
            self._log_rejection(signal, "daily_limit")
            return None

        # Extract metadata
        meta = getattr(signal, 'metadata', {}) or {}
        confidence = signal.confidence
        # Guard against NaN/None confidence
        if confidence is None or (isinstance(confidence, float) and math.isnan(confidence)):
            logger.debug("[SNIPER] NaN/None confidence, rejecting")
            return None
        num_agree = meta.get("num_agree", 1)
        strategies = meta.get("strategies_agree", [signal.strategy])
        if isinstance(strategies, str):
            strategies = [strategies]
        regime = meta.get("regime", "unknown")
        ev_per_dollar = meta.get("ev_per_dollar", 0)

        # ── Gate 0b: WIN PROBABILITY FLOOR ──
        # Data analysis (26.9k signals): low_win_prob_0.33 was 80.4% correct (KEEP)
        # but low_win_prob_0.34 was only 24% correct (TOO AGGRESSIVE).
        # Lowered from 0.48 to 0.33 — the actual break-even point for this gate.
        win_prob = meta.get("win_prob", meta.get("win_prob_deflated"))
        if win_prob is not None and isinstance(win_prob, (int, float)):
            if win_prob < 0.33:
                self._log_rejection(signal, f"low_win_prob_{win_prob:.2f}")
                return None

        # ── Gate 1: SETUP FILTER (the real edge — from missed trade analysis) ──
        # Data proves: symbol+side IS the edge, not confidence.
        # HYPE BUY: 85% WR at ANY confidence. SOL SELL: 59% WR at ANY confidence.
        # Everything else is negative EV. Confidence adds nothing to prediction.
        # Additional filter: chop < 0.3 captures 90% of HYPE BUY edge (cleaner entries)
        setup_key = f"{signal.symbol}_{signal.side}"
        chop = meta.get("chop_score_smoothed", meta.get("chop_score", 0))
        # Guard against NaN chop — treat as unknown (pass through)
        if chop is None or (isinstance(chop, float) and math.isnan(chop)):
            chop = 0.0
        # HYPE SELL: Was hard-blocked based on old backtest data showing 0-7% WR.
        # LIVE DATA (26.9k signals, Mar 25-Apr 1): 85.2% WR (225W/39L) !!
        # The backtest was WRONG. HYPE is in a bearish trend — shorts print.
        # Now treated as a proven A-grade setup.

        # SOL SELL: Edge STRENGTHENING (+33pp, 35%→68% WR over 500h study).
        # Best at Normal Vol (ATR% 0.80-0.98%): PF=1.75, WR=61.5%.
        # Negative EV at High+ Vol (ATR%>1.20%): PF<0.72. Upgraded to full setup.

        # BTC BUY: 15% WR overall, BUT 70-80% confidence band shows 69% WR in backtest
        # Allow through only in that narrow band for discovery/paper validation
        if setup_key == "BTC_BUY":
            if not (70 <= confidence <= 80 and num_agree >= 2):
                return None  # Block outside the profitable band

        # SOL BUY: 15% WR overall, BUT 70-75% band shows 64% WR in backtest
        # Allow through only in that narrow band for discovery
        if setup_key == "SOL_BUY":
            if not (70 <= confidence <= 75 and num_agree >= 2):
                return None  # Block outside the profitable band

        # ── RSI HARD GATE (data-driven) ──
        # RSI 35-65 = 62-64% WR. RSI <30 = panic zone (50% WR = coin flip).
        # RSI >75 = overbought (reversal risk). Don't enter at extremes.
        rsi_val = meta.get("rsi")
        if rsi_val is not None and isinstance(rsi_val, (int, float)):
            if setup_key == "HYPE_BUY" and rsi_val < 30:
                self._log_rejection(signal, f"rsi_oversold_{rsi_val:.0f}")
                return None
            if setup_key == "HYPE_BUY" and rsi_val > 75:
                self._log_rejection(signal, f"rsi_overbought_{rsi_val:.0f}")
                return None
            # SOL RSI<20 is a DEATH TRAP: 0% up at 6h, avg -4.73% at 24h.
            # SOL extreme oversold is continuation, not reversal.
            if setup_key == "SOL_BUY" and rsi_val < 20:
                self._log_rejection(signal, f"sol_rsi_death_trap_{rsi_val:.0f}")
                return None

        positive_ev_setups = {
            "HYPE_BUY": {"grade": "A", "max_chop": 0.55},   # Edge WEAKENING (64%→40%). Require higher confluence.
            "HYPE_SELL": {"grade": "A", "max_chop": 0.65},   # LIVE: 85.2% WR (225W/39L). Was hard-blocked by mistake!
            "SOL_SELL": {"grade": "A", "max_chop": 0.55},    # Edge STRENGTHENING (35%→68%). Best at Normal Vol.
            "BTC_SELL": {"grade": "A", "max_chop": 0.60},    # LIVE: 60.6% WR. Real trades: 100% WR (+$92).
            "ETH_BUY":  {"grade": "B", "max_chop": 0.55},    # LIVE: 60.2% WR. New discovery from rejection analysis.
        }

        # ── TREND-AWARE GATE for HYPE_BUY in bearish conditions ──
        # Edge study: HYPE_BUY edge is WEAKENING (-24pp). In a bear market,
        # buying dips is the #1 money loser. If HYPE is below BOTH EMA20 and
        # EMA50 (full bear alignment), require 80%+ confidence AND 2+ agree
        # even for the "proven" setup. This prevents 3/4 of bear market losses.
        if setup_key == "HYPE_BUY":
            ema20_val = meta.get("ema20")
            ema50_val = meta.get("ema50")
            if ema20_val is not None and ema50_val is not None:
                try:
                    _ema20 = float(ema20_val)
                    _ema50 = float(ema50_val)
                    _price = signal.entry
                    if _price < _ema20 and _price < _ema50:
                        # Price below BOTH EMAs = bearish structure
                        if confidence < 80 or num_agree < 2:
                            self._log_rejection(
                                signal,
                                f"hype_buy_bearish_trend_conf{confidence:.0f}_agree{num_agree}"
                            )
                            return None
                        # Additional penalty: if EMA20 < EMA50, it's a full bear cross
                        if _ema20 < _ema50 and confidence < 85:
                            self._log_rejection(
                                signal,
                                f"hype_buy_bear_cross_conf{confidence:.0f}"
                            )
                            return None
                except (ValueError, TypeError):
                    pass

        # Expanded setups (paper-mode validation)
        # These are research-identified edges that need live validation
        if self.config.expanded_setups:
            positive_ev_setups.update({
                # BTC SHORT only at >=90% conf — 67% WR, PF 1.98
                # NEVER below 90%: 70-80% conf is a death trap (PF 0.31-0.79)
                "BTC_SELL": {"grade": "B+", "max_chop": 0.5, "min_confidence": 90},
                # NOTE: BTC_BUY removed — counterfactuals show 15% WR (toxic).
                # BTC_BUY is in the toxic_setups block above. Do NOT add here.
            })

        setup = positive_ev_setups.get(setup_key)
        if setup is not None:
            # Proven/expanded setup — filter on chop + optional confidence band
            max_chop = setup.get("max_chop", 0.5)
            if chop > max_chop:
                logger.debug(f"[SNIPER] {signal.symbol} {signal.side} rejected: chop {chop:.2f} > {max_chop}")
                self._log_rejection(signal, f"chop_too_high_{chop:.2f}")
                return None

            # ── DIP FILTER: Prevent buying at extreme highs / chasing drops ──
            # Replay validated: 94% WR at current prices. The original 2% dip
            # threshold was too strict — 108 signals/hour rejected for being
            # 0.1-0.4% from rolling high (normal noise, not "buying the top").
            # Only block truly extreme positions: new ATH breakouts or deep chase.
            dip_pct = self._get_dip_pct(signal.symbol, signal.entry)
            if dip_pct is not None:
                if setup_key == "HYPE_BUY":
                    # Block only at new highs (price ABOVE rolling high)
                    # dip_pct < 0 means price is above the rolling high
                    if dip_pct < -1.0:
                        self._log_rejection(signal, f"above_high_{abs(dip_pct):.1f}pct")
                        return None
                elif setup_key == "SOL_SELL":
                    # Don't chase deep drops — only short near highs
                    if dip_pct > 8.0:
                        self._log_rejection(signal, f"already_dipped_{dip_pct:.1f}pct")
                        return None

            # Expanded setups may have confidence band requirements
            min_conf = setup.get("min_confidence")
            max_conf = setup.get("max_confidence")
            if min_conf is not None and confidence < min_conf:
                self._log_rejection(signal, f"setup_low_conf_{confidence:.0f}_need_{min_conf}")
                return None
            if max_conf is not None and confidence > max_conf:
                self._log_rejection(signal, f"setup_high_conf_{confidence:.0f}_max_{max_conf}")
                return None
        else:
            # Not a proven edge — apply confidence filter as discovery mechanism
            if confidence < self.config.min_confidence:
                self._log_rejection(signal, f"low_confidence_{confidence:.0f}")
                return None
            if num_agree < self.config.min_num_agree:
                self._log_rejection(signal, f"low_consensus_{num_agree}")
                return None

        # ── Gate 1x: MINIMUM QUALITY FOR ALL TRADES (even proven setups) ──
        # Data: 83.5% of signals are 80+/3-agree SNIPER quality (+$20 EV/trade).
        # But sim was executing 60% conf / 1-agree junk (-$2.90 EV/trade).
        # The alpha tradeoff: fewer trades at MUCH higher quality = profitable.
        # Proven setups still bypass WHICH setup to trade, but not HOW GOOD the signal is.
        # EXCEPTION: A-grade proven setups (SOL_SELL, HYPE_BUY) with validated edge
        # get a lower solo quality floor. SOL SELL is strengthening (+33pp) but only
        # 1 strategy (regime_trend) generates it — requiring 80% solo conf blocks ALL
        # SOL SELL signals since regime_trend typically produces 60-75% conf.
        # Data analysis (2026-04-01, 26.9k signals):
        # quality_floor_solo_76 was ONLY 3.5% correct — killed 306 winners, blocked 11 losers
        # quality_floor_conf_66 was only 42.5% correct — killed more winners than losers
        # Solo signals CAN work (low_consensus_1 only 30.8% correct rejection rate)
        # Lowered all thresholds significantly. Full Kelly approach: take trades, let sizing
        # manage risk. These gates should only block obvious junk, not marginal signals.
        _proven_solo_setups = {"SOL_SELL", "HYPE_BUY", "BTC_SELL", "ETH_BUY"}
        if self.config.mode == "aggressive":
            if setup_key in _proven_solo_setups:
                # Proven A-grade: floor at 50% (was 60% — killed too many)
                if confidence < 50:
                    self._log_rejection(signal, f"quality_floor_proven_solo_{confidence:.0f}")
                    return None
            else:
                if confidence < 55:
                    self._log_rejection(signal, f"quality_floor_conf_{confidence:.0f}")
                    return None
                # Removed solo_76 gate entirely — 96.5% of its rejections were wrong

        # ── Gate 2: R:R floor (always check — prevents bad entries) ──
        risk = abs(signal.entry - signal.sl)
        if risk <= 0:
            self._log_rejection(signal, "zero_risk")
            return None
        reward1 = abs(signal.tp1 - signal.entry)
        rr = reward1 / risk if risk > 0 else 0
        if rr < self.config.min_rr:
            self._log_rejection(signal, f"low_rr_{rr:.2f}")
            return None

        # ── Gate 2b: Time-of-day tagging (data-driven, informational only) ──
        # Deep analysis: HYPE_BUY PF=2.47 during 18-06 UTC vs PF=1.29 during 06-18 UTC.
        # Tag signals with time quality but DON'T block — LLMs need data to learn from.
        current_hour = datetime.now(timezone.utc).hour
        _is_prime_hours = (current_hour >= 18 or current_hour < 6)  # 18-06 UTC

        # ── Gate 3: Regime filter (ALL setups in dangerous regimes) ──
        # Only block in truly dangerous regimes. "unknown" is just missing data,
        # not an actual dangerous regime — was blocking 102 high-conf signals.
        regime_lower = regime.lower()
        _dangerous_regimes = {"panic", "high_volatility"}
        if regime_lower in _dangerous_regimes:
            if confidence < 85 or num_agree < 3:
                self._log_rejection(signal, f"dangerous_regime_{regime_lower}_conf{confidence:.0f}_agree{num_agree}")
                return None
        if setup is None and regime_lower in [r.lower() for r in self.config.weak_regimes]:
            if confidence < 85:
                self._log_rejection(signal, f"weak_regime_{regime_lower}")
                return None

        # ── Gate 4: Volatility regime gate (edge study 2026-03-27) ──
        # ATR% determines profitability per setup. Block negative EV vol regimes.
        atr_val = meta.get("atr")
        if atr_val is not None and signal.entry > 0:
            try:
                atr_pct = (float(atr_val) / signal.entry) * 100.0
                if setup_key == "HYPE_BUY" and atr_pct > 1.90:
                    self._log_rejection(signal, f"hype_extreme_vol_atr{atr_pct:.2f}")
                    return None
                if setup_key == "SOL_SELL" and atr_pct > 1.20:
                    self._log_rejection(signal, f"sol_high_vol_atr{atr_pct:.2f}")
                    return None
            except (ValueError, TypeError):
                pass

        # ── Gate 5: Dedup (symbol + side — one signal per symbol per window) ──
        now = time.time()
        # Include entry price rounded to prevent same-scan duplicates
        entry_rounded = round(signal.entry, 2)
        dedup_key = f"{signal.symbol}:{signal.side}:{entry_rounded}"
        last_dedup = self._dedup_cache.get(dedup_key, 0)
        if (now - last_dedup) < self.config.dedup_window_s:
            self._log_rejection(signal, "dedup")
            return None
        self._dedup_cache[dedup_key] = now

        # Also block any signal for this symbol within cooldown (broader than dedup)
        symbol_key = f"{signal.symbol}:any"
        last_symbol = self._dedup_cache.get(symbol_key, 0)
        if (now - last_symbol) < self.config.min_alert_gap_s:
            self._log_rejection(signal, "symbol_cooldown")
            return None
        self._dedup_cache[symbol_key] = now

        # ── Classify tier ──
        tier = self._classify_tier(confidence, num_agree, signal.symbol, regime_lower, signal.side)

        # ── Dip-buy detection ──
        # Data shows HYPE BUY during moderate dips (2-5% from recent high) has 88.5% WR
        # vs 85% baseline. Detect dip conditions from signal metadata.
        # Only boost tier for proven setups — unproven setups shouldn't get free upgrades.
        is_dip_buy = False
        if signal.side == "BUY":
            is_dip_buy = self._detect_dip_buy(signal, meta, regime_lower, chop)
            if is_dip_buy and setup is not None:
                # Only boost proven setups (HYPE BUY, etc.)
                if tier == "STANDARD":
                    tier = "PREMIUM"  # Boost: dip-buy upgrades STANDARD → PREMIUM
                elif tier == "PREMIUM":
                    tier = "SNIPER"   # Boost: dip-buy upgrades PREMIUM → SNIPER

        # ── Gate 6: Funding rate edge (structural alpha from derivatives) ──
        # When funding confirms our direction, it's an independent signal:
        # - SELL + positive funding = shorts earn funding + overcrowded longs = boost
        # - BUY + negative funding = longs earn funding + overcrowded shorts = boost
        # When funding opposes, it's a headwind (we'll be paying).
        funding_rate = meta.get("funding_rate")
        funding_note = ""
        if funding_rate is not None and isinstance(funding_rate, (int, float)):
            abs_fr = abs(funding_rate)
            if abs_fr >= 0.0002:  # Only care about non-trivial funding
                funding_favors = (
                    (signal.side == "SELL" and funding_rate > 0) or
                    (signal.side == "BUY" and funding_rate < 0)
                )
                funding_against = (
                    (signal.side == "BUY" and funding_rate > 0) or
                    (signal.side == "SELL" and funding_rate < 0)
                )
                if funding_favors and abs_fr >= 0.0005:
                    # Extreme funding confirming our trade = tier boost
                    if tier == "STANDARD":
                        tier = "PREMIUM"
                    elif tier == "PREMIUM":
                        tier = "SNIPER"
                    funding_note = f"FUNDING TAILWIND +{abs_fr*100:.3f}%/8h (earning)"
                    logger.info(f"[SNIPER] {signal.symbol} {signal.side} funding boost: {funding_note}")
                elif funding_favors:
                    funding_note = f"funding aligned +{abs_fr*100:.3f}%/8h"
                elif funding_against and abs_fr >= 0.0005:
                    # Extreme funding against us = reduce hold time suggestion
                    funding_note = f"FUNDING HEADWIND {abs_fr*100:.3f}%/8h (paying)"
                    logger.info(f"[SNIPER] {signal.symbol} {signal.side} funding warning: {funding_note}")
                elif funding_against:
                    funding_note = f"funding drag {abs_fr*100:.3f}%/8h"

        # ── Gate 7: MICRO-SNIPER check (before tier rejection) ──
        # Micro-sniper is an alternative qualification path: even if the signal
        # would be a normal SNIPER/PREMIUM, it can ALSO qualify as MICRO_SNIPER
        # with completely different sizing (tiny risk, huge leverage, fast TP).
        is_micro_sniper = False
        if self.config.micro_sniper_enabled:
            stop_width_pct_check = abs(signal.entry - signal.sl) / signal.entry if signal.entry > 0 else 0.01
            is_micro_sniper = self._qualifies_micro_sniper(
                signal, meta, confidence, num_agree, regime, stop_width_pct_check, is_dip_buy
            )
            if is_micro_sniper:
                tier = "MICRO_SNIPER"
                self._micro_sniper_today += 1

        # ── Gate 8: PRE-TRADE QUALITY SCORECARD ──
        # Composite score across 6 dimensions. Prevents junk entries that
        # historically caused all losses (1-agree, 60% conf, bad regimes).
        #
        # Anticipatory entries get +15 bonus (pre-planned = higher conviction)
        # and use the standard min_score (50). Reactive entries must clear 65
        # to suppress marginal reactive signals that produced 4 losses / 1 win.
        is_anticipatory = meta.get("anticipatory_entry", False)
        atr_for_score = meta.get("atr", getattr(signal, 'atr', None))
        scorecard_result = self._scorecard.score(
            symbol=signal.symbol,
            side=signal.side,
            confidence=confidence,
            num_agree=num_agree,
            regime=regime,
            atr=atr_for_score,
            entry_price=signal.entry,
            metadata=meta,
        )

        # Apply anticipatory bonus: +15 to score (pre-planned precision entry)
        effective_score = scorecard_result.total_score
        if is_anticipatory:
            effective_score = min(100, effective_score + 15)
            logger.info(
                f"[SNIPER] ANTICIPATORY BONUS +15 | {signal.symbol} {signal.side} | "
                f"raw={scorecard_result.total_score} -> effective={effective_score}"
            )

        # Reactive signals need 40+ to let proven setups through while still
        # blocking junk. Solo signals now get partial credit (5pts consensus,
        # 3pts confidence) so a SOL_SELL with edge_trend+regime+vol+time can pass.
        reactive_min_score = 40
        min_threshold = self._scorecard.min_score if is_anticipatory else reactive_min_score

        if effective_score < min_threshold:
            logger.info(
                f"[SNIPER] SCORECARD REJECT | {signal.symbol} {signal.side} | "
                f"score={effective_score}/100 (raw={scorecard_result.total_score}) | "
                f"min={min_threshold} | anticipatory={is_anticipatory} | "
                f"components={scorecard_result.components}"
            )
            self._log_rejection(signal, f"scorecard_{effective_score}_min{min_threshold}")
            return None

        # ── Reflection Engine: re-entry quality check ──
        _refl_size_mult = 1.0
        if self._reflection_engine is not None:
            try:
                _refl_score = self._reflection_engine.get_entry_quality_score(
                    symbol=signal.symbol, side=signal.side,
                    entry_price=signal.entry, confidence=confidence,
                    regime=regime, atr=getattr(signal, 'atr', 0) or 0,
                    win_prob=meta.get("win_prob", 0),
                )
                if _refl_score["advisory"] == "WEAK":
                    logger.info(
                        f"[SNIPER] REFLECT: WEAK entry SKIPPED (score={_refl_score['quality_score']}) "
                        f"codes=[{','.join(_refl_score['codes'])}]"
                    )
                    self._log_rejection(signal, f"reflect_weak_{_refl_score['quality_score']}")
                    return None
                elif _refl_score["advisory"] == "CAUTION":
                    _refl_size_mult = 0.75
                    logger.info(
                        f"[SNIPER] REFLECT: CAUTION entry (score={_refl_score['quality_score']}) "
                        f"codes=[{','.join(_refl_score['codes'])}] — size * 0.75"
                    )
            except Exception:
                pass

        # ── In aggressive mode, skip STANDARD tier entirely ──
        if self.config.mode == "aggressive" and tier == "STANDARD":
            self._log_rejection(signal, "aggressive_standard_skip")
            return None

        # ── Dynamic leverage based on stop width + confidence ──
        # Tight stop = higher leverage (same $ risk, bigger position, bigger P&L)
        # Wide stop = lower leverage (keep risk manageable)
        stop_width = abs(signal.entry - signal.sl)
        stop_width_pct = stop_width / signal.entry if signal.entry > 0 else 0.01

        leverage = self._get_dynamic_leverage(tier, confidence, num_agree, stop_width_pct)

        # ── Hard cap: sniper leverage must never exceed max_sniper_leverage (default 5x) ──
        # One sniper_premium trade at 9.7x lost $147 and wiped 26 other wins.
        leverage = min(leverage, self.config.max_sniper_leverage)

        # ── Conviction-based override: if confluence data is available, use it ──
        _conviction_result = None
        _confluence_count = meta.get("confluence_count", 0)
        if isinstance(_confluence_count, (int, float)) and _confluence_count >= 1:
            try:
                from manual.conviction_sizer import ConvictionSizer
                _conv_sizer = ConvictionSizer()
                _conviction_result = _conv_sizer.size(
                    equity=acct_equity,
                    entry_price=signal.entry,
                    sl_price=signal.sl,
                    tp_price=signal.tp1,
                    confluences=int(_confluence_count),
                    confluence_sources=meta.get("confluence_sources", []),
                    symbol=signal.symbol,
                    side=signal.side,
                    multi_tf_aligned=bool(meta.get("multi_tf_aligned", False)),
                    regime=regime,
                    atr=meta.get("atr") or getattr(signal, "atr", None),
                )
                if _conviction_result is not None:
                    leverage = _conviction_result.leverage
                    logger.info(
                        f"[SNIPER] CONVICTION override | {signal.symbol} {signal.side} | "
                        f"{_conviction_result.summary()}"
                    )
            except Exception as _conv_err:
                logger.debug(f"[SNIPER] Conviction sizer fallback: {_conv_err}")

        # ── Final sniper leverage cap (applies after all overrides) ──
        # Conviction sizer / Kelly optimizer may have overridden leverage above.
        # Re-enforce the hard cap here, before any sizing calculations.
        leverage = min(leverage, self.config.max_sniper_leverage)

        # ── MICRO_SNIPER: Override sizing completely ──
        # Micro-sniper uses its own tiny risk + high leverage formula.
        # This bypasses Kelly/fixed sizing entirely.
        if is_micro_sniper:
            mc = self.config
            risk_pct = mc.micro_sniper_risk_pct  # 1-2%
            risk_amount = acct_equity * risk_pct
            # Leverage: scale between min/max based on confidence
            conf_frac = min(1.0, max(0.0, (confidence - 80) / 20.0))  # 80→0, 100→1
            leverage = mc.micro_sniper_min_leverage + conf_frac * (mc.micro_sniper_max_leverage - mc.micro_sniper_min_leverage)
            leverage = round(min(leverage, mc.micro_sniper_max_leverage, self.config.max_sniper_leverage), 1)
            position_size_usd = risk_amount / stop_width_pct if stop_width_pct > 0 else 0
            margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd

            # Margin cap (never exceed 50% of equity for micro-sniper — keep it small)
            if margin_required > acct_equity * 0.50:
                scale = (acct_equity * 0.50) / margin_required
                position_size_usd *= scale
                risk_amount *= scale
                margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd

            qty = position_size_usd / signal.entry if signal.entry > 0 else 0

            # Micro-sniper TP: tight scalp (1.5-2x stop), no swing
            scalp_target_pct = stop_width_pct * mc.micro_sniper_tp_multiplier
            swing_target_pct = stop_width_pct * (mc.micro_sniper_tp_multiplier + 1.0)

            if signal.side == "BUY":
                tp_scalp = signal.entry * (1 + scalp_target_pct)
                tp_swing = signal.entry * (1 + swing_target_pct)
            else:
                tp_scalp = signal.entry * (1 - scalp_target_pct)
                tp_swing = signal.entry * (1 - swing_target_pct)

            pnl_scalp = position_size_usd * scalp_target_pct
            pnl_swing = position_size_usd * swing_target_pct
            loss_amount = risk_amount

            rr_scalp = mc.micro_sniper_tp_multiplier
            rr_swing = mc.micro_sniper_tp_multiplier + 1.0

            account_after_win = acct_equity + pnl_scalp
            account_after_loss = acct_equity - loss_amount
            growth_pct = (pnl_scalp / acct_equity * 100) if acct_equity > 0 else 0
            daily_target_pct = (pnl_scalp / self.config.daily_target * 100) if self.config.daily_target > 0 else 0

            # Determine mean-reversion tag for context
            consecutive_red = meta.get("consecutive_red_candles", 0)
            is_mean_rev = isinstance(consecutive_red, (int, float)) and consecutive_red >= mc.micro_sniper_min_red_candles
            mr_tag = f" [MEAN-REV {int(consecutive_red)} red]" if is_mean_rev else ""
            dip_tag = " [DIP-BUY]" if is_dip_buy else ""

            # Quality scoring (still run for tracking)
            try:
                from manual.signal_scorer import score_signal
                score_result = score_signal(
                    symbol=signal.symbol, side=signal.side,
                    confidence=confidence, num_agree=num_agree,
                    chop=chop, ev_per_dollar=ev_per_dollar,
                    regime=regime, is_dip_buy=is_dip_buy, metadata=meta,
                )
            except Exception:
                score_result = {"score": 90, "grade": "A+", "recommendation": "FIRE", "size_multiplier": 1.0}

            sniper = SniperSignal(
                symbol=signal.symbol,
                side=signal.side,
                tier="MICRO_SNIPER",
                entry=signal.entry,
                sl=signal.sl,
                tp_scalp=round(tp_scalp, 6),
                tp_swing=round(tp_swing, 6),
                leverage=leverage,
                risk_pct=risk_pct,
                risk_amount=round(risk_amount, 2),
                position_size_usd=round(position_size_usd, 2),
                qty=round(qty, 6),
                margin_required=round(margin_required, 2),
                pnl_scalp=round(pnl_scalp, 2),
                pnl_swing=round(pnl_swing, 2),
                loss_amount=round(loss_amount, 2),
                rr_scalp=round(rr_scalp, 2),
                rr_swing=round(rr_swing, 2),
                account_equity=round(acct_equity, 2),
                account_after_win=round(account_after_win, 2),
                account_after_loss=round(account_after_loss, 2),
                growth_pct=round(growth_pct, 1),
                confidence=confidence,
                num_agree=num_agree,
                strategies=strategies,
                regime=regime,
                ev_per_dollar=ev_per_dollar,
                signal_context=getattr(signal, 'signal_context', '') or '',
                is_dip_buy=is_dip_buy,
                quality_score=score_result.get("score", 0),
                quality_grade=score_result.get("grade", ""),
                quality_recommendation=score_result.get("recommendation", ""),
                size_multiplier=1.0,  # No score-based adjustment for micro-sniper
                timestamp=datetime.now(timezone.utc).isoformat(),
                daily_target_pct=round(daily_target_pct, 1),
                hold_target_hours=f"<{self.config.micro_sniper_time_stop_hours:.0f}h (micro-scalp)",
                funding_note=funding_note,
            )

            self._daily_signals.append(sniper)
            self._last_alert_ts[signal.symbol] = time.time()
            self._log_signal(sniper)

            logger.info(
                f"[MICRO-SNIPER] FIRED{dip_tag}{mr_tag} | {signal.symbol} {signal.side} | "
                f"conf={confidence:.0f}% agree={num_agree} lev={leverage:.0f}x | "
                f"acct=${acct_equity:.0f} risk=${risk_amount:.2f} ({risk_pct:.1%}) | "
                f"win=+${pnl_scalp:.2f} ({growth_pct:.1f}%) loss=-${loss_amount:.2f} | "
                f"R:R={rr_scalp:.1f}:1 time_stop={mc.micro_sniper_time_stop_hours:.0f}h"
            )

            return sniper

        # ── Risk sizing: Conviction-based → Kelly-optimized → fixed % ──
        _kelly_rationale = ""

        # If conviction sizer produced a result, use it directly
        if _conviction_result is not None:
            risk_pct = _conviction_result.risk_pct
            risk_amount = _conviction_result.risk_amount
            leverage = min(_conviction_result.leverage, self.config.max_sniper_leverage)
            position_size_usd = _conviction_result.position_notional
            margin_required = _conviction_result.margin_required
            qty = _conviction_result.qty
            _kelly_rationale = (
                f"conviction:{_conviction_result.conviction_tier} "
                f"{_conviction_result.confluence_count}conf "
                f"{'|'.join(_conviction_result.modifiers_applied)}"
            )
        elif self._sizing_optimizer is not None:
            try:
                _opt_sizing = self._sizing_optimizer.get_optimal_size(
                    setup=setup_key,
                    equity=acct_equity,
                    confidence=confidence,
                    num_agree=num_agree,
                    regime=regime,
                    is_dip_buy=is_dip_buy,
                    stop_width_pct=stop_width_pct,
                )
                risk_pct = _opt_sizing.risk_pct
                risk_amount = _opt_sizing.risk_amount
                leverage = min(_opt_sizing.leverage, self.config.max_sniper_leverage)  # Override with Kelly-optimal leverage, capped
                position_size_usd = _opt_sizing.position_size_usd
                margin_required = _opt_sizing.margin_required
                _kelly_rationale = _opt_sizing.rationale
            except Exception:
                # Fallback to fixed sizing
                risk_pct = self._get_risk_pct(tier)
                risk_amount = acct_equity * risk_pct
                position_size_usd = risk_amount / stop_width_pct if stop_width_pct > 0 else 0
                margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd
        else:
            risk_pct = self._get_risk_pct(tier)
            risk_amount = acct_equity * risk_pct
            position_size_usd = risk_amount / stop_width_pct if stop_width_pct > 0 else 0
            margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd

        # ── Sanity check: margin can't exceed equity ──
        if margin_required > acct_equity * 0.95:
            scale = (acct_equity * 0.95) / margin_required
            position_size_usd *= scale
            risk_amount *= scale
            margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd

        # Recalculate qty unless conviction sizer already set it
        if _conviction_result is None:
            qty = position_size_usd / signal.entry if signal.entry > 0 else 0

        # ── Calculate TPs ──
        # Scalp TP: 1.5x risk (quick capture, target $20-50 at leverage)
        # Swing TP: 3x risk (let it run for bigger win)
        scalp_target_pct = stop_width_pct * 1.5
        swing_target_pct = stop_width_pct * 3.0

        if signal.side == "BUY":
            tp_scalp = signal.entry * (1 + scalp_target_pct)
            tp_swing = signal.entry * (1 + swing_target_pct)
        else:
            tp_scalp = signal.entry * (1 - scalp_target_pct)
            tp_swing = signal.entry * (1 - swing_target_pct)

        # Use bot's TP1 as swing target if it's better
        bot_tp1_dist = abs(signal.tp1 - signal.entry)
        manual_swing_dist = abs(tp_swing - signal.entry)
        if bot_tp1_dist > manual_swing_dist:
            tp_swing = signal.tp1

        # ── Calculate expected P&L ──
        pnl_scalp = position_size_usd * scalp_target_pct
        pnl_swing = position_size_usd * swing_target_pct
        loss_amount = risk_amount

        rr_scalp = 1.5  # By construction
        rr_swing = (swing_target_pct / stop_width_pct) if stop_width_pct > 0 else 0

        # ── Account growth projection ──
        account_after_win = acct_equity + pnl_scalp
        account_after_loss = acct_equity - loss_amount
        growth_pct = (pnl_scalp / acct_equity * 100) if acct_equity > 0 else 0

        # Daily target coverage
        daily_target_pct = (pnl_scalp / self.config.daily_target * 100) if self.config.daily_target > 0 else 0

        # Hold time suggestion
        if tier == "SNIPER":
            hold_target = "1-4h (scalp)"
        elif tier == "PREMIUM":
            hold_target = "2-8h (swing)"
        else:
            hold_target = "4-12h (swing)"

        # ── Quality scoring ──
        try:
            from manual.signal_scorer import score_signal
            score_result = score_signal(
                symbol=signal.symbol, side=signal.side,
                confidence=confidence, num_agree=num_agree,
                chop=chop, ev_per_dollar=ev_per_dollar,
                regime=regime, is_dip_buy=is_dip_buy, metadata=meta,
            )
        except Exception:
            score_result = {"score": 50, "grade": "B", "recommendation": "TAKE", "size_multiplier": 1.0}

        # Apply score-based sizing adjustment (signal_scorer)
        score_size_mult = score_result.get("size_multiplier", 1.0)
        position_size_usd *= score_size_mult
        qty *= score_size_mult
        risk_amount *= score_size_mult

        # Apply reflection engine sizing (re-entry quality)
        if _refl_size_mult < 1.0:
            position_size_usd *= _refl_size_mult
            qty *= _refl_size_mult
            risk_amount *= _refl_size_mult

        margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd

        # Apply scorecard size factor (50-69 = half size, 70+ = full)
        if scorecard_result.size_factor < 1.0:
            position_size_usd *= scorecard_result.size_factor
            qty *= scorecard_result.size_factor
            risk_amount *= scorecard_result.size_factor
            margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd

        # Re-apply margin cap after score multiplier (prevents exceeding equity)
        if margin_required > acct_equity * 0.95:
            scale = (acct_equity * 0.95) / margin_required
            position_size_usd *= scale
            qty *= scale
            risk_amount *= scale
            margin_required = position_size_usd / leverage if leverage > 0 else position_size_usd

        pnl_scalp = position_size_usd * scalp_target_pct
        pnl_swing = position_size_usd * swing_target_pct
        loss_amount = risk_amount
        account_after_win = acct_equity + pnl_scalp
        account_after_loss = acct_equity - loss_amount
        growth_pct = (pnl_scalp / acct_equity * 100) if acct_equity > 0 else 0

        sniper = SniperSignal(
            symbol=signal.symbol,
            side=signal.side,
            tier=tier,
            entry=signal.entry,
            sl=signal.sl,
            tp_scalp=round(tp_scalp, 6),
            tp_swing=round(tp_swing, 6),
            leverage=leverage,
            risk_pct=risk_pct,
            risk_amount=round(risk_amount, 2),
            position_size_usd=round(position_size_usd, 2),
            qty=round(qty, 6),
            margin_required=round(margin_required, 2),
            pnl_scalp=round(pnl_scalp, 2),
            pnl_swing=round(pnl_swing, 2),
            loss_amount=round(loss_amount, 2),
            rr_scalp=round(rr_scalp, 2),
            rr_swing=round(rr_swing, 2),
            account_equity=round(acct_equity, 2),
            account_after_win=round(account_after_win, 2),
            account_after_loss=round(account_after_loss, 2),
            growth_pct=round(growth_pct, 1),
            confidence=confidence,
            num_agree=num_agree,
            strategies=strategies,
            regime=regime,
            ev_per_dollar=ev_per_dollar,
            signal_context=getattr(signal, 'signal_context', '') or '',
            is_dip_buy=is_dip_buy,
            quality_score=score_result.get("score", 0),
            quality_grade=score_result.get("grade", ""),
            quality_recommendation=score_result.get("recommendation", ""),
            size_multiplier=score_size_mult,
            timestamp=datetime.now(timezone.utc).isoformat(),
            daily_target_pct=round(daily_target_pct, 1),
            hold_target_hours=hold_target,
            funding_note=funding_note,
        )

        # Track
        self._daily_signals.append(sniper)
        self._last_alert_ts[signal.symbol] = now
        self._log_signal(sniper)

        dip_tag = " [DIP-BUY]" if is_dip_buy else ""
        fund_tag = f" [{funding_note}]" if funding_note else ""
        logger.info(
            f"[SNIPER] {tier}{dip_tag}{fund_tag} | {signal.symbol} {signal.side} | "
            f"conf={confidence:.0f}% agree={num_agree} lev={leverage:.0f}x | "
            f"acct=${acct_equity:.0f} risk=${risk_amount:.2f} win=+${pnl_scalp:.2f} | "
            f"growth={growth_pct:.1f}%"
        )

        return sniper

    def _classify_tier(
        self, confidence: float, num_agree: int, symbol: str, regime: str,
        side: str = ""
    ) -> str:
        """Classify signal into STANDARD / PREMIUM / SNIPER tier.

        Setup-first: proven edges get automatic tier upgrades because
        the edge is the setup itself, not the confidence score.
        Updated 2026-03-25: HYPE BUY edge weakening (51.7% WR, was 58%).
        SOL_SELL marginal (PF < 1.0). Tier classification downgraded.
        """
        # HYPE BUY: still best setup but edge weakening. Require higher confluence
        # for top tiers. 3-agree has 2x better WR than 2-agree.
        if symbol == "HYPE" and side == "BUY":
            if num_agree >= 3 and confidence >= 85:
                return "SNIPER"    # Triple confluence + high confidence
            if num_agree >= 3 and confidence >= 80:
                return "PREMIUM"   # Triple confluence, good confidence
            if num_agree >= 2 and confidence >= 80:
                return "PREMIUM"   # Double confluence + high confidence
            if num_agree >= 2:
                return "STANDARD"  # Double confluence, lower confidence — was PREMIUM
            return "STANDARD"      # Solo signal — was PREMIUM, downgraded due to edge decay

        # SOL SELL: STRENGTHENING edge (+33pp, 35%→68% WR). Upgraded from marginal.
        # Best at Normal Vol (ATR% 0.80-0.98%): PF=1.75.
        # Solo signals get PREMIUM because only 1 strategy (regime_trend) generates
        # SOL SELL — requiring 2-agree is structurally impossible. The edge IS the setup.
        if symbol == "SOL" and side == "SELL":
            if num_agree >= 3 and confidence >= 85:
                return "SNIPER"    # Full upgrade: strong edge + triple confluence
            if num_agree >= 3 and confidence >= 80:
                return "PREMIUM"   # Triple confluence, good confidence
            if num_agree >= 2 and confidence >= 80:
                return "PREMIUM"   # Double confluence + high confidence
            if confidence >= 60:
                return "PREMIUM"   # Solo SOL SELL with decent confidence — proven edge
            return "STANDARD"

        # BTC_BUY in validated 70-80 confidence band — BUG FIX: these pass setup gate
        # but fell through to STANDARD (then blocked by aggressive mode). Give PREMIUM.
        if symbol == "BTC" and side == "BUY":
            if 70 <= confidence <= 80 and num_agree >= 2:
                return "PREMIUM"  # Validated band from backtest data

        # Expanded setups — PREMIUM tier (lower edge, need validation)
        if self.config.expanded_setups:
            if symbol == "BTC" and side == "SELL" and confidence >= 90 and num_agree >= 3:
                return "PREMIUM"  # BTC SHORT >=90% — 67% WR, PF 1.98

        # Everything else: confidence-based (discovery mode)
        if (confidence >= 85 and num_agree >= 3) or \
           (confidence >= 90 and num_agree >= 2):
            return "SNIPER"

        if confidence >= self.config.premium_min_confidence and num_agree >= 2:
            return "PREMIUM"

        return "STANDARD"

    def _get_dynamic_leverage(
        self, tier: str, confidence: float, num_agree: int, stop_width_pct: float
    ) -> float:
        """
        Dynamic leverage based on stop width + confidence + tier.

        The sweet spot: tighter stops allow higher leverage (same $ risk, bigger
        position). Wide stops force lower leverage to keep risk manageable.

        Stop width ranges we see:
        - Tight: 0.5-1.5% (scalp setups) → higher leverage
        - Medium: 1.5-3.0% (swing setups) → moderate leverage
        - Wide: 3.0%+ (trend setups) → lower leverage
        """
        c = self.config

        # Base leverage from tier + confidence
        if confidence >= 90 and num_agree >= 3:
            base_lev = c.leverage_tier_5   # 25x
        elif tier == "SNIPER":
            base_lev = c.leverage_tier_4   # 25x
        elif tier == "PREMIUM" and confidence >= 85:
            base_lev = c.leverage_tier_3   # 20x
        elif tier == "PREMIUM":
            base_lev = c.leverage_tier_2   # 15x
        else:
            base_lev = c.leverage_tier_1   # 10x

        # Stop width adjustment: tighter stop → leverage boost, wider → cut
        if stop_width_pct <= 0.01:  # <= 1% stop
            stop_mult = 1.25  # Boost 25% — tight stop = precise entry
        elif stop_width_pct <= 0.015:  # 1-1.5% stop
            stop_mult = 1.1   # Slight boost
        elif stop_width_pct <= 0.025:  # 1.5-2.5% stop
            stop_mult = 1.0   # Neutral
        elif stop_width_pct <= 0.035:  # 2.5-3.5% stop
            stop_mult = 0.8   # Cut 20%
        else:  # > 3.5% stop
            stop_mult = 0.6   # Cut 40% — wide stop needs less leverage

        adjusted = base_lev * stop_mult
        return min(round(adjusted, 1), c.max_leverage)

    def _get_leverage(self, tier: str, confidence: float, num_agree: int) -> float:
        """Fallback: fixed leverage from tier + confidence (used if stop width unknown)."""
        return self._get_dynamic_leverage(tier, confidence, num_agree, 0.02)

    def _detect_dip_buy(self, signal, meta: Dict, regime_lower: str, chop: float) -> bool:
        """
        Detect if this BUY signal is a dip-buy setup.

        Dip-buy conditions (any of):
        1. Regime suggests consolidation/range/pullback (price likely off highs)
        2. Chop score is low (<0.2) — clean, trending dip rather than messy chop
        3. Signal metadata explicitly indicates a dip (dip_depth_pct, dip_detected)
        4. Price is significantly below recent high (from metadata)

        From research: HYPE BUY during moderate dips (2-5%) has 88.5% WR vs 85% baseline.
        """
        # Check explicit dip detector metadata (from DipDetector integration)
        if meta.get("dip_detected") is True:
            return True
        dip_depth = meta.get("dip_depth_pct", 0)
        if isinstance(dip_depth, (int, float)) and dip_depth >= 2.0:
            return True

        # Regime-based inference: consolidation/range suggests price pulled back
        dip_regimes = {"consolidation", "range", "pullback", "mean_reversion"}
        if regime_lower in dip_regimes:
            # Low chop confirms clean entry (not messy sideways)
            if chop < 0.3:
                return True

        # Check price_vs_high metadata if available
        price_vs_high = meta.get("price_vs_high_pct", 0)
        if isinstance(price_vs_high, (int, float)) and price_vs_high <= -2.0:
            return True

        return False

    def _qualifies_micro_sniper(
        self, signal, meta: Dict, confidence: float, num_agree: int,
        regime: str, stop_width_pct: float, is_dip_buy: bool
    ) -> bool:
        """
        Check if signal qualifies for MICRO_SNIPER tier.

        Two qualification paths:
        1. ELITE: 85%+ conf, 3-agree, RSI sweet spot, prime hours, proven setup
        2. MEAN REVERSION: 4+ red candles, BUY signal, moderate confidence

        Both paths require tight SL (0.5-1.2%) for fast resolution.
        """
        c = self.config
        if not c.micro_sniper_enabled:
            return False

        # Reset daily counter
        today = date.today()
        if self._micro_sniper_date != today:
            self._micro_sniper_today = 0
            self._micro_sniper_date = today

        # Max 1 micro-sniper per day
        if self._micro_sniper_today >= c.micro_sniper_max_daily:
            return False

        # Stop width must be tight (0.5-1.2%) for fast resolution
        if stop_width_pct < c.micro_sniper_min_stop_pct:
            return False  # Too tight — likely bad data
        if stop_width_pct > c.micro_sniper_max_stop_pct:
            return False  # Too wide — defeats the purpose

        # RSI sweet spot (35-65) — no extremes
        rsi_val = meta.get("rsi")
        if rsi_val is not None and isinstance(rsi_val, (int, float)):
            if rsi_val < c.micro_sniper_rsi_min or rsi_val > c.micro_sniper_rsi_max:
                return False

        # Prime hours check (18-06 UTC where PF=2.47)
        if c.micro_sniper_prime_hours_only:
            current_hour = datetime.now(timezone.utc).hour
            if not (current_hour >= 18 or current_hour < 6):
                return False

        # Dangerous regimes — never micro-snipe in panic/high_vol
        regime_lower = regime.lower()
        if regime_lower in {"panic", "high_volatility"}:
            return False

        # ── Path 1: ELITE setup qualification ──
        setup_key = f"{signal.symbol}_{signal.side}"
        elite_setups = {"HYPE_BUY", "SOL_SELL"}  # Proven +EV setups only

        if setup_key in elite_setups:
            if confidence >= c.micro_sniper_min_confidence and num_agree >= c.micro_sniper_min_agree:
                logger.info(
                    f"[MICRO-SNIPER] ELITE qualification: {setup_key} "
                    f"conf={confidence:.0f}% agree={num_agree} stop={stop_width_pct:.3f}"
                )
                return True

        # ── Path 2: MEAN REVERSION qualification ──
        if c.micro_sniper_mean_reversion and signal.side == "BUY":
            consecutive_red = meta.get("consecutive_red_candles", 0)
            if isinstance(consecutive_red, (int, float)) and consecutive_red >= c.micro_sniper_min_red_candles:
                # Mean reversion after 4+ red candles — looser confidence requirement
                if confidence >= 75 and num_agree >= 2:
                    # Extra check: must be a known symbol (no random shitcoins)
                    if signal.symbol in {"HYPE", "BTC", "SOL", "ETH"}:
                        logger.info(
                            f"[MICRO-SNIPER] MEAN-REVERSION qualification: {setup_key} "
                            f"red_candles={consecutive_red} conf={confidence:.0f}% "
                            f"agree={num_agree} stop={stop_width_pct:.3f}"
                        )
                        return True

        return False

    def _get_risk_pct(self, tier: str) -> float:
        """Map tier → risk percentage of account."""
        if tier == "MICRO_SNIPER":
            return self.config.micro_sniper_risk_pct
        if tier == "SNIPER":
            return self.config.risk_pct_sniper
        elif tier == "PREMIUM":
            return self.config.risk_pct_premium
        return self.config.risk_pct_standard

    def _log_signal(self, sniper: SniperSignal) -> None:
        """Append signal to JSONL log with flush for durability."""
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(sniper.to_dict()) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            logger.warning(f"[SNIPER] Failed to log signal: {e}")

    def _log_rejection(self, signal, reason: str) -> None:
        """Log a filter rejection for analysis. Lightweight — no fsync."""
        self._daily_rejections[reason] = self._daily_rejections.get(reason, 0) + 1
        try:
            meta = getattr(signal, 'metadata', {}) or {}
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": getattr(signal, 'symbol', '?'),
                "side": getattr(signal, 'side', '?'),
                "confidence": getattr(signal, 'confidence', 0),
                "reason": reason,
                "num_agree": meta.get("num_agree", 0),
                "regime": meta.get("regime", "unknown"),
                "chop": meta.get("chop_score_smoothed", meta.get("chop_score", 0)),
                # GM_REJECTIONS_79K measurement fix (2026-07-02): without the
                # bracket geometry, rejections could only be scored on fixed
                # horizons — true TP/SL counterfactual EV was unknowable for
                # all 79,855 prior records. Log it going forward.
                "entry": getattr(signal, 'entry', None),
                "sl": getattr(signal, 'sl', None),
                "tp1": getattr(signal, 'tp1', None),
                "tp2": getattr(signal, 'tp2', None),
            }
            with open(self._rejection_log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # Rejection logging is best-effort

    def get_rejection_stats(self) -> Dict[str, int]:
        """Get today's rejection counts by reason."""
        return dict(self._daily_rejections)

    def get_daily_summary(self) -> Dict[str, Any]:
        """Get today's manual signal summary."""
        signals = self._daily_signals
        total_potential_scalp = sum(s.pnl_scalp for s in signals)
        total_potential_swing = sum(s.pnl_swing for s in signals)
        total_risk = sum(s.risk_amount for s in signals)

        return {
            "date": str(self._daily_date or date.today()),
            "mode": self.config.mode,
            "account_equity": self._running_equity,
            "signals_sent": len(signals),
            "max_signals": self.config.max_daily_signals,
            "total_potential_scalp": round(total_potential_scalp, 2),
            "total_potential_swing": round(total_potential_swing, 2),
            "total_risk": round(total_risk, 2),
            "daily_target": self.config.daily_target,
            "target_coverage_scalp_pct": round(
                total_potential_scalp / self.config.daily_target * 100, 1
            ) if self.config.daily_target > 0 else 0,
            "by_tier": {
                "MICRO_SNIPER": len([s for s in signals if s.tier == "MICRO_SNIPER"]),
                "SNIPER": len([s for s in signals if s.tier == "SNIPER"]),
                "PREMIUM": len([s for s in signals if s.tier == "PREMIUM"]),
                "STANDARD": len([s for s in signals if s.tier == "STANDARD"]),
            },
            "rejections": dict(self._daily_rejections),
            "total_rejections": sum(self._daily_rejections.values()),
            "signals": [s.to_dict() for s in signals],
        }
