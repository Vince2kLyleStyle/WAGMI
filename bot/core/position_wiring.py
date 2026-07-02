"""
Position lifecycle management mixin for MultiStrategyBot.

Extracted from multi_strategy_main.py — contains exit intelligence,
position aging, pending order fills, trade rotation, sniper signal
execution, and solo signal handling.

All methods are designed to be mixed into MultiStrategyBot via inheritance.
They access bot state through `self.*` attributes.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any

from trading_config import DEFAULT_SYMBOLS
from data.db import log_trade
from data.fetchers.telemetry import Telemetry
from execution.precision import get_min_qty, round_qty

# Optional imports
try:
    from llm.exit_engine import ExitEngine
    from llm.exit_types import ExitDecision
    _EXIT_ENGINE_AVAILABLE = True
except ImportError:
    _EXIT_ENGINE_AVAILABLE = False

try:
    from llm.deep_memory import get_deep_memory
    _DEEP_MEMORY_AVAILABLE = True
except ImportError:
    _DEEP_MEMORY_AVAILABLE = False

logger = logging.getLogger("bot.main")


class PositionWiringMixin:
    """Mixin providing position lifecycle management methods."""

    def _execute_pending_fill(self, order, trace_id: str = ""):
        """Execute a filled pending order by opening a position."""
        symbol = order.symbol
        side = order.side

        # Check we can still open (circuit breaker, max positions, etc.)
        if not self.risk_mgr.can_open_position(
            symbol, side, self.pos_mgr.get_open_count(),
            self.pos_mgr.get_open_positions(),
        ):
            logger.info(f"[{trace_id}][{symbol}] Pending fill blocked by risk manager")
            return

        # Already have a position in this symbol?
        if symbol in self.pos_mgr.get_open_positions():
            logger.info(f"[{trace_id}][{symbol}] Pending fill skipped — already in position")
            return

        qty = order.qty
        min_q = get_min_qty(symbol)
        if qty < min_q:
            logger.info(f"[{trace_id}][{symbol}] Pending fill qty {qty} < min {min_q}")
            return

        # Determine TP1 close percentage from trade profile
        tp1_pct = 0.7
        if order.trade_profile:
            exit_params = getattr(order.trade_profile, 'exit_params', None)
            if exit_params:
                tp1_pct = getattr(exit_params, 'tp1_close_pct', 0.7)

        self.pos_mgr.open_position(
            symbol=symbol,
            side=side,
            entry=order.entry_price,
            qty=qty,
            sl=order.sl,
            tp1=order.tp1,
            tp2=order.tp2,
            atr=order.atr,
            leverage=order.leverage,
            mode="leverage" if order.leverage > 1 else "spot",
            strategy=order.strategy,
            confidence=order.confidence,
            tp1_close_pct=tp1_pct,
            entry_reasons=order.entry_reasons,
            trade_profile=order.trade_profile,
            notes=getattr(order, 'notes', ''),
            setup_type=getattr(order, 'setup_type', ''),
        )

        log_trade(
            symbol=symbol,
            action="OPEN",
            side=side,
            price=order.entry_price,
            qty=qty,
            leverage=order.leverage,
            strategy=order.strategy,
            metadata={
                "confidence": order.confidence,
                "order_type": "limit",
                "pending_wait_s": round(order.age_s, 1),
                "order_id": order.order_id,
            }
        )

        Telemetry.inc("total_trades")
        Telemetry.inc("limit_order_fills")
        self.ops_guard.record_trade()

        # Clear the slippage cooldown since we successfully filled
        self._slippage_reject_cooldown.pop(symbol, None)

        logger.info(
            f"[{trace_id}][{symbol}] LIMIT FILL: {side} @ {order.entry_price:.6f} "
            f"qty={qty:.4f} lev={order.leverage:.1f}x conf={order.confidence:.0f}% "
            f"(waited {order.age_s:.0f}s)"
        )

        self.alerts.send_market_update(
            f"[LIMIT FILL] {side} {symbol} @ {order.entry_price:.6f}\n"
            f"Qty: {qty:.4f} | Lev: {order.leverage:.1f}x | Conf: {order.confidence:.0f}%\n"
            f"Waited {order.age_s:.0f}s for entry level"
        )

    def _evaluate_rotations(self, trace_id: str = ""):
        """
        Evaluate whether any open position should be rotated into a better signal.

        Called once per tick after all symbols have been processed. Uses the
        candidate signals collected during symbol processing.
        """
        open_pos = self.pos_mgr.get_open_positions()
        if not open_pos:
            return

        # Build position dicts compatible with rotation manager
        positions_dict = {}
        for sym, pos in open_pos.items():
            positions_dict[sym] = {
                "symbol": sym,
                "side": pos.side,
                "entry": pos.entry,
                "sl": pos.sl,
                "tp1": pos.tp1,
                "tp2": pos.tp2,
                "qty": pos.qty,
                "status": "open",
                "open_time": pos.open_time.isoformat() if pos.open_time else None,
            }

        # Filter candidates: exclude symbols that already have open positions
        # (can't rotate INTO a symbol we already hold)
        candidates = [
            c for c in self._tick_candidates
            if c["symbol"] not in open_pos
        ]

        if not candidates:
            return

        # Get current prices
        current_prices = {sym: self._last_prices[sym]
                          for sym in positions_dict if sym in self._last_prices}

        actions = self.rotation_mgr.evaluate_rotations(
            positions_dict, candidates, current_prices
        )

        for action in actions:
            self._execute_rotation(action, trace_id)

    def _execute_rotation(self, action, trace_id: str = ""):
        """Execute a rotation: close the old position, open the new one."""
        close_symbol = action.close_symbol
        new_signal = action.open_signal
        new_symbol = new_signal["symbol"]

        logger.info(
            f"[{trace_id}] ROTATION: {close_symbol} -> {new_symbol} | "
            f"reason={action.close_reason} | "
            f"current_pnl={action.current_unrealized_pct:+.2f}% | "
            f"old_rr={action.old_rr_ratio:.2f} -> new_rr={action.new_rr_ratio:.2f} "
            f"({action.rr_improvement:.2f}x improvement) | "
            f"new_conf={action.confidence_new:.0f}%"
        )

        # 1. Close the old position (exchange order + internal state)
        close_price = self._last_prices.get(close_symbol)
        if close_price is None:
            logger.warning(f"[{trace_id}] Rotation aborted: no price for {close_symbol}")
            return

        # Submit exchange close order BEFORE updating internal state
        _rot_pos = self.pos_mgr.positions.get(close_symbol)
        if _rot_pos and _rot_pos.qty > 0:
            _rot_close_side = "SELL" if _rot_pos.side == "LONG" else "BUY"
            _rot_close_result = self.order_executor.close_position(
                close_symbol, _rot_close_side, _rot_pos.qty, close_price,
                reason=action.close_reason
            )
            if not (_rot_close_result and getattr(_rot_close_result, "filled", False)):
                logger.critical(
                    f"[{trace_id}] Rotation close FAILED for {close_symbol} — "
                    f"position still open. Aborting rotation. "
                    f"Reconciliation will handle. Result: {_rot_close_result}"
                )
                return
        close_event = self.pos_mgr.force_close(
            close_symbol, close_price, action.close_reason
        )
        if close_event is None:
            logger.warning(f"[{trace_id}] Rotation aborted: could not close {close_symbol}")
            return

        # Process the close event (equity, logging, ML, etc.)
        self.risk_mgr.update_equity(close_event.pnl - close_event.fee)
        log_trade(
            symbol=close_event.symbol,
            action=close_event.action,
            side=close_event.side,
            price=close_event.price,
            qty=close_event.qty,
            pnl=close_event.pnl,
            fee=close_event.fee,
            leverage=close_event.leverage,
            strategy=close_event.strategy,
            metadata={
                **close_event.metadata,
                "rotation_to": new_symbol,
                "rotation_reason": action.close_reason,
                "rotation_rr_improvement": action.rr_improvement,
            }
        )

        # Record cooldown for the closed symbol
        self._symbol_cooldown[close_symbol] = time.time()
        pos = self.pos_mgr.positions.get(close_symbol)
        if pos:
            self._last_close_win[close_symbol] = pos.realized_pnl > 0
            self._last_close_side[close_symbol] = pos.side

        # Send alert
        self.alerts.send_trade_event(
            action.close_reason, close_symbol,
            f"ROTATION: {close_symbol} -> {new_symbol}\n"
            f"PnL: ${close_event.pnl:+.2f} | R/R improvement: {action.rr_improvement:.1f}x\n"
            f"New signal confidence: {action.confidence_new:.0f}%"
        )

        # 2. Record the rotation
        self.rotation_mgr.record_rotation(action)
        Telemetry.inc("rotations")

        # 3. The new position will be opened on the next tick when
        # the signal is re-evaluated (don't double-open here since
        # the signal may have already been opened in _process_symbol)
        logger.info(
            f"[{trace_id}] Rotation complete: closed {close_symbol}, "
            f"{new_symbol} signal available for entry next tick"
        )

    def _prioritize_symbols(self, symbols_dict):
        """Order symbols by evaluation priority for the current tick.

        Priority scoring (higher = evaluated first):
        - +10: Has open position (need to monitor)
        - +8:  Is a lead-lag follower with active signal (about to move)
        - +5:  High recent price change (>2% in 1h, volatile = actionable)
        - +3:  In favorable regime (trend or high_volatility)
        - +0:  Default (evaluated last)

        Returns list of (symbol, config) tuples sorted by priority (descending).
        """
        scored = []
        open_positions = self.pos_mgr.get_open_positions()

        # Get lead-lag follower symbols
        lead_lag_followers = set()
        if self.cross_symbol_tracker:
            try:
                for sig in self.cross_symbol_tracker.get_active_signals():
                    if sig.get("confidence", 0) >= 0.3:
                        lead_lag_followers.add(sig["follower"])
            except Exception:
                pass

        for symbol, cfg in symbols_dict.items():
            priority = 0

            # Open position -> high priority (monitoring)
            if symbol in open_positions:
                priority += 10

            # Lead-lag follower -> expected to move soon
            if symbol in lead_lag_followers:
                priority += 8

            # High volatility -> more likely to produce signals
            change_1h = abs(self._price_changes_1h.get(symbol, 0.0))
            if change_1h >= 2.0:
                priority += 5
            elif change_1h >= 1.0:
                priority += 2

            # Favorable regime (use tick cache to avoid redundant API calls)
            regime = self._tick_regime_cache.get(symbol, "unknown")
            if regime in ("trend", "high_volatility"):
                priority += 3

            scored.append((priority, symbol, cfg))

        # Sort descending by priority, stable sort preserves original order for ties
        scored.sort(key=lambda x: -x[0])
        return [(sym, cfg) for _, sym, cfg in scored]

    def _on_solo_signal_for_sniper(self, signal) -> None:
        """Callback from ensemble: receives 1-agree signals rejected for low consensus.

        The sniper filter has its own gates (proven setup, chop, dip) and can
        profitably trade signals the ensemble sits out on.
        """
        if self._manual_sniper is None:
            return
        try:
            # Quant Brain pre-filter for solo sniper signals
            if self._quant_brain is not None:
                try:
                    _qb_solo = self._quant_brain.evaluate_signal(signal)
                    if _qb_solo.action in ("veto", "skip"):
                        logger.debug(
                            f"[SNIPER-SOLO-QB] {signal.symbol} {signal.side} "
                            f"blocked by QuantBrain: {_qb_solo.action}"
                        )
                        return
                except Exception:
                    pass  # Fail-open

            symbol = signal.symbol
            current_price = self._last_prices.get(symbol, signal.entry)
            _sniper_sig = self._manual_sniper.evaluate(
                signal, equity=self.risk_mgr.equity
            )
            if _sniper_sig is not None:
                logger.info(
                    f"[SNIPER-SOLO] {symbol} {_sniper_sig.side} tier={_sniper_sig.tier} "
                    f"conf={_sniper_sig.confidence:.0f}% lev={_sniper_sig.leverage:.1f}x "
                    f"sim={'YES' if self._sniper_simulator else 'NO'}"
                )
                if self._manual_alerter is not None:
                    self._manual_alerter.send_sniper_alert(
                        _sniper_sig, equity=self.risk_mgr.equity
                    )
                if self._sniper_simulator is not None:
                    try:
                        _sim_pos = self._sniper_simulator.on_signal(_sniper_sig)
                        if _sim_pos:
                            logger.info(
                                f"[SIM] OPENED {_sim_pos.trade_id} {_sim_pos.symbol} "
                                f"{_sim_pos.side} @ ${_sim_pos.entry:.2f} "
                                f"size=${_sim_pos.position_size_usd:.2f} "
                                f"(from solo signal)"
                            )
                        else:
                            logger.debug(
                                f"[SIM] Rejected {symbol} {_sniper_sig.side} "
                                f"(dedup or circuit breaker)"
                            )
                    except Exception as _sim_err:
                        logger.warning(f"[SIM] Error on solo signal: {_sim_err}")
                if hasattr(self, '_signal_tracker') and self._signal_tracker is not None:
                    try:
                        self._signal_tracker.record_signal(_sniper_sig)
                    except Exception:
                        pass
                if self._sniper_auto_execute and _sniper_sig.tier in ("SNIPER", "PREMIUM"):
                    try:
                        self._execute_sniper_signal(_sniper_sig, symbol, current_price)
                    except Exception as _sae_err:
                        logger.error(f"[SNIPER-EXEC] Solo signal error: {_sae_err}")
        except Exception as e:
            logger.debug(f"[SNIPER-SOLO] Error: {e}")

    def _execute_sniper_signal(self, sniper_sig, symbol: str, current_price: float):
        """Execute a qualifying sniper signal through the order executor.

        Safety gates (checked in order):
        1. Circuit breaker must not be tripped
        2. No existing position on this symbol
        3. Max open positions not exceeded
        4. Ops guard rate limits respected
        """
        # Gate 1: Circuit breaker
        if not self.risk_mgr.is_trading_allowed():
            logger.info(f"[SNIPER-EXEC] {symbol} blocked: circuit breaker tripped")
            return

        # Gate 2: No existing position
        if symbol in self.pos_mgr.positions:
            pos = self.pos_mgr.positions[symbol]
            if pos.state not in ("CLOSED",):
                logger.debug(f"[SNIPER-EXEC] {symbol} blocked: position already open")
                return

        # Gate 3: Max positions
        open_count = sum(
            1 for p in self.pos_mgr.positions.values()
            if p.state not in ("CLOSED",)
        )
        if open_count >= self.config.max_open_positions:
            logger.info(f"[SNIPER-EXEC] {symbol} blocked: max positions ({open_count})")
            return

        # Extract trade params early (needed for ops guard check)
        side = sniper_sig.side  # "BUY" or "SELL"

        # Gate 4: Ops guard
        if hasattr(self, 'ops_guard') and self.ops_guard is not None:
            guard_result = self.ops_guard.can_execute()
            if not guard_result.get("allowed", True):
                logger.info(f"[SNIPER-EXEC] {symbol} blocked: ops guard - {guard_result.get('reason', '')}")
                return

        # Execute the trade — use Kelly leverage from main leverage manager,
        # not the sniper's conservative 3-4x sizing
        _kelly_decision = self.leverage_mgr.decide(
            confidence=getattr(sniper_sig, 'confidence', 75),
            num_strategies_agree=getattr(sniper_sig, 'num_agree', 1),
            total_strategies=10,
            risk_tier="medium",
        )
        leverage = _kelly_decision.leverage
        # Auto-exec safety cap (2026-04-16): the sniper emits up to 20x now
        # for manual exploration, but auto-executed trades get clamped to
        # the tighter auto-exec cap (default 5x). This is the "can never
        # unsupervised-blow-up like the -$147 SOL SHORT at 9.7x" safety.
        try:
            from manual.config import ManualSniperConfig as _MSC
            _auto_exec_cap = getattr(
                _MSC(), "max_auto_exec_leverage", 5.0
            )
            if leverage > _auto_exec_cap:
                logger.info(
                    f"[SNIPER-EXEC] {symbol} leverage clamped "
                    f"{leverage:.1f}x -> {_auto_exec_cap:.1f}x (auto-exec cap)"
                )
                leverage = _auto_exec_cap
        except Exception as _cap_err:
            # On any error, fall back to hard 5x cap (defense in depth)
            logger.warning(f"[SNIPER-EXEC] auto-exec cap load failed: {_cap_err}; using 5x fallback")
            leverage = min(leverage, 5.0)
        # Recalculate qty with Kelly leverage and Full Kelly risk
        _stop_dist = abs(current_price - sniper_sig.sl)
        if _stop_dist > 0:
            _risk_usd = self.risk_mgr.equity * self.config.risk_per_trade
            qty = _risk_usd / (_stop_dist * max(leverage, 1.0))
        else:
            qty = sniper_sig.qty

        logger.info(
            f"[SNIPER-EXEC] Executing {symbol} {side} | "
            f"tier={sniper_sig.tier} conf={sniper_sig.confidence:.0f}% "
            f"lev={leverage:.0f}x qty={qty:.6f} "
            f"entry=${current_price:.2f} sl=${sniper_sig.sl:.2f} "
            f"tp_scalp=${sniper_sig.tp_scalp:.2f}"
        )

        order_result = self.order_executor.open_position(
            symbol=symbol,
            side=side,
            qty=qty,
            price=current_price,
            leverage=leverage,
        )

        if order_result and getattr(order_result, "filled", False):
            # Register position with position manager
            fill_price = getattr(order_result, "fill_price", current_price)
            fill_qty = getattr(order_result, "fill_qty", qty)

            # MFE-based TP for sniper path
            _MFE_TP1 = {"BTC": 0.0038, "SOL": 0.0051, "ETH": 0.0044, "HYPE": 0.0078}
            _MFE_TP2 = {"BTC": 0.0099, "SOL": 0.0134, "ETH": 0.0132, "HYPE": 0.0189}
            _sl_dist = abs(fill_price - sniper_sig.sl)
            if leverage > 5.0 and symbol in _MFE_TP1:
                _mfe_tp1 = fill_price * _MFE_TP1[symbol]
                _mfe_tp2 = fill_price * _MFE_TP2[symbol]
                _orig_tp1 = abs(fill_price - sniper_sig.tp_scalp)
                _new_tp1 = min(_mfe_tp1, _orig_tp1)
                _new_tp2 = min(_mfe_tp2, abs(fill_price - sniper_sig.tp_swing))
                if _sl_dist > 0:
                    _new_tp1 = max(_new_tp1, _sl_dist * 1.0)
                    _new_tp2 = max(_new_tp2, _sl_dist * 2.0)
                if side == "BUY":
                    _tp1 = fill_price + _new_tp1
                    _tp2 = fill_price + _new_tp2
                else:
                    _tp1 = fill_price - _new_tp1
                    _tp2 = fill_price - _new_tp2
                _rr = _new_tp1 / _sl_dist if _sl_dist > 0 else 0
                logger.info(
                    f"[SNIPER-EXEC] MFE TP for {leverage:.0f}x: "
                    f"TP1 ${sniper_sig.tp_scalp:.2f}->${_tp1:.2f} "
                    f"TP2 ${sniper_sig.tp_swing:.2f}->${_tp2:.2f} R:R={_rr:.1f}:1"
                )
            else:
                _tp1 = sniper_sig.tp_scalp
                _tp2 = sniper_sig.tp_swing

            self.pos_mgr.open_position(
                symbol=symbol,
                side="LONG" if side == "BUY" else "SHORT",
                entry=fill_price,
                qty=fill_qty,
                sl=sniper_sig.sl,
                tp1=_tp1,
                tp2=_tp2,
                leverage=leverage,
                strategy=f"sniper_{sniper_sig.tier.lower()}",
                confidence=getattr(sniper_sig, 'confidence', 0),
                entry_reasons={
                    "sniper_tier": sniper_sig.tier,
                    "auto_executed": True,
                },
            )

            # Place exchange-side SL/TP for crash protection
            close_side = "SELL" if side == "BUY" else "BUY"
            try:
                sl_result = self.order_executor.place_stop_loss(
                    symbol=symbol, side=close_side,
                    qty=fill_qty, trigger_price=sniper_sig.sl,
                )
                if sl_result.success:
                    logger.info(f"[SNIPER-EXEC] SL order placed @ ${sniper_sig.sl:.2f}")
                else:
                    logger.warning(f"[SNIPER-EXEC] SL order FAILED: {sl_result.error}")
            except Exception as _sl_err:
                logger.warning(f"[SNIPER-EXEC] SL order error: {_sl_err}")

            try:
                tp_result = self.order_executor.place_take_profit(
                    symbol=symbol, side=close_side,
                    qty=fill_qty, trigger_price=sniper_sig.tp_scalp,
                )
                if tp_result.success:
                    logger.info(f"[SNIPER-EXEC] TP order placed @ ${sniper_sig.tp_scalp:.2f}")
                else:
                    logger.warning(f"[SNIPER-EXEC] TP order FAILED: {tp_result.error}")
            except Exception as _tp_err:
                logger.warning(f"[SNIPER-EXEC] TP order error: {_tp_err}")

            # Track in ops guard
            if hasattr(self, 'ops_guard') and self.ops_guard is not None:
                self.ops_guard.record_trade()

            logger.info(
                f"[SNIPER-EXEC] FILLED {symbol} {side} @ ${fill_price:.2f} "
                f"qty={fill_qty:.6f} lev={leverage:.0f}x | "
                f"SL=${sniper_sig.sl:.2f} TP1=${sniper_sig.tp_scalp:.2f} "
                f"TP2=${sniper_sig.tp_swing:.2f}"
            )

            if self.alerts:
                self.alerts.send_trade_alert(
                    f"SNIPER {sniper_sig.tier} EXECUTED: {symbol} {side} "
                    f"@ ${fill_price:.2f} | {leverage:.0f}x | "
                    f"conf={sniper_sig.confidence:.0f}% "
                    f"grade={sniper_sig.quality_grade}"
                )
        else:
            logger.error(
                f"[SNIPER-EXEC] FAILED to fill {symbol} {side}: "
                f"{getattr(order_result, 'error', 'unknown')}"
            )

    def _check_llm_exit_suggestions(self):
        """Evaluate open positions for dynamic SL/TP adjustments using the exit engine.

        Uses heuristic rules informed by deep memory data (strategy win rates,
        regime-specific patterns) to decide when to tighten stops on losing patterns
        and widen TPs on confirmed winning patterns.

        Stores TradeEvents from full closes (LLM_EXIT_AGENT force_close calls) in
        self._pending_exit_events so they can be injected into the main event loop
        for post-trade callbacks (graduated_rules.record_outcome, learning, etc).

        Called every 5th tick from _tick_once() to avoid excessive computation.
        """
        # Initialize pending events list if needed
        if not hasattr(self, '_pending_exit_events'):
            self._pending_exit_events = []

        if not self.exit_engine:
            return

        open_positions = self.pos_mgr.get_open_positions()
        if not open_positions:
            return

        now = datetime.now(timezone.utc)

        # Deep memory: fetch strategy effectiveness for pattern-aware decisions
        deep_mem = None
        strategy_effectiveness = {}
        if _DEEP_MEMORY_AVAILABLE:
            try:
                deep_mem = get_deep_memory()
                strategy_effectiveness = deep_mem.trade_dna.get_strategy_effectiveness()
            except Exception:
                pass  # Non-critical — fall back to heuristics without memory

        for symbol, pos in open_positions.items():
            try:
                # Respect per-symbol cooldown built into the exit engine
                if not self.exit_engine.should_evaluate(symbol):
                    continue

                current_price = self._last_prices.get(symbol)
                if current_price is None or current_price <= 0:
                    continue

                # ── Build exit context ──
                is_long = pos.side == "LONG"
                if is_long:
                    unrealized_pnl = (current_price - pos.entry) * pos.qty * pos.leverage
                    unrealized_pct = (current_price - pos.entry) / pos.entry
                else:
                    unrealized_pnl = (pos.entry - current_price) * pos.qty * pos.leverage
                    unrealized_pct = (pos.entry - current_price) / pos.entry

                # Time in position
                hold_seconds = (now - pos.open_time).total_seconds()
                hold_minutes = hold_seconds / 60.0

                # Current regime (use tick cache)
                regime = self._tick_regime_cache.get(symbol, "unknown")

                # Funding rate
                funding_rate = self._last_funding_rates.get(symbol, 0.0)

                # ── 2026-06-19 GUILLOTINE GUARD ──────────────────────────────────
                # The discretionary exit layer (LLM Exit Agent + heuristics) was
                # force-closing nearly every position in <2h under "critical health"
                # panic — 15/15 recent closes by LLM_EXIT_AGENT, 0/10 WR, a 24-loss
                # streak — overriding the Exit Agent's own "2h = noise phase, default
                # HOLD" rule. That manufactured the losing streak (trades never lived
                # long enough to work). Fix: let young positions BREATHE — skip the
                # discretionary exit review for positions younger than MIN_EXIT_HOLD_HOURS
                # UNLESS a HARD invalidation is present (adverse regime, or stop already
                # breached). Mechanical SL/TP/trailing in position_manager still protect
                # downside the whole time. Fully reversible via env (set 0 to disable).
                _min_exit_hold_h = float(os.getenv("MIN_EXIT_HOLD_HOURS", "2.0"))
                _sl_breached = (is_long and current_price <= pos.sl) or \
                               ((not is_long) and current_price >= pos.sl)
                _hard_invalidation = regime in ("panic", "crash", "extreme_fear") or _sl_breached
                if _min_exit_hold_h > 0 and hold_minutes < _min_exit_hold_h * 60 and not _hard_invalidation:
                    logger.info(
                        f"[EXIT-GUILLOTINE-GUARD] {symbol} discretionary exit skipped: "
                        f"hold {hold_minutes:.0f}min < {_min_exit_hold_h}h, no hard invalidation "
                        f"(regime={regime}, sl_breached={_sl_breached}) — letting it breathe."
                    )
                    self.exit_engine.mark_evaluated(symbol)
                    continue

                # ── Qualification gate: skip positions that don't warrant review ──
                # Position must meet at least one criterion:
                should_review = False

                # 1. Position open > 30 minutes
                if hold_minutes > 30:
                    should_review = True

                # 2. Losing position open > 15 minutes
                if unrealized_pnl < 0 and hold_minutes > 15:
                    should_review = True

                # 3. Regime has shifted to an adverse state
                if regime in ("panic", "crash", "extreme_fear"):
                    should_review = True

                if not should_review:
                    continue

                # ── LLM Exit Intelligence Agent (when multi-agent enabled) ──
                # Replaces heuristic rules with thesis-aware LLM reasoning.
                #
                # Finding 5 (2026-04-15): Exit agent was calling the API even
                # when LLM_MODE=0 (user's "LLM off" state). That created a
                # silent ~$2-8/month cost leak. Now we respect the master
                # LLM_MODE gate before calling, so LLM_MODE=0 genuinely stops
                # all agent API calls. Preserves the agent for manual use
                # (coordinator can still be invoked directly from scripts/REPL).
                if os.getenv("LLM_MULTI_AGENT", "").lower() in ("1", "true", "yes"):
                    try:
                        from llm.agents.coordinator import get_coordinator, is_multi_agent_enabled
                        from llm.autonomy import get_llm_mode, should_call_llm
                        if not should_call_llm(get_llm_mode()):
                            # LLM_MODE=0 (OFF): skip background Exit agent entirely.
                            # Mechanical exit rules below still apply.
                            pass
                        elif is_multi_agent_enabled():
                            coordinator = get_coordinator()
                            pos_data = {
                                "symbol": symbol,
                                "side": pos.side,
                                "entry": pos.entry,
                                "current_price": current_price,
                                "sl": pos.sl,
                                "original_sl": getattr(pos, 'original_sl', pos.sl),
                                "tp1": pos.tp1,
                                "tp2": pos.tp2,
                                "state": pos.state,
                                "unrealized_pnl": round(unrealized_pnl, 2),
                                "unrealized_pct": round(unrealized_pct * 100, 2),
                                "hold_minutes": round(hold_minutes, 1),
                                "leverage": getattr(pos, 'leverage', 1),
                                "regime": regime,
                                "funding_rate": funding_rate,
                                "strategy": getattr(pos, 'strategy', ''),
                                "entry_type": getattr(pos, 'entry_type', ''),
                                # MFE telemetry (computed in position_manager profit-lock
                                # block). Let the LLM decide what to do with this.
                                "mfe_pct_current": getattr(pos, 'mfe_pct_current', None),
                                "mfe_pct_peak": getattr(pos, 'mfe_pct_peak', None),
                                "mfe_retrace_pct": getattr(pos, 'mfe_retrace_pct', None),
                                "atr_pct": getattr(pos, 'atr_pct', None),
                                "fee_pct": getattr(pos, 'fee_pct', None),
                                "suggested_be_sl": getattr(pos, 'suggested_be_sl', None),
                                # Mechanical review flags — set by position_manager when
                                # legacy auto-close conditions trigger. LLM sees them as
                                # signals (not commands) and reasons whether to act.
                                "time_stop_review_requested": getattr(pos, '_time_stop_review_requested', False),
                                "time_stop_age_h": getattr(pos, '_time_stop_age_h', None),
                                "early_exit_review_requested": getattr(pos, '_early_exit_review_requested', False),
                            }
                            # Pull thesis from position notes if available
                            notes = getattr(pos, 'notes', '') or ''
                            if 'THESIS:' in notes:
                                thesis_start = notes.index('THESIS:') + 7
                                thesis_end = notes.index('|', thesis_start) if '|' in notes[thesis_start:] else len(notes)
                                pos_data["original_thesis"] = notes[thesis_start:thesis_start + thesis_end].strip()[:100]
                            if 'setup=' in notes:
                                setup_start = notes.index('setup=') + 6
                                setup_end = notes.index(' ', setup_start) if ' ' in notes[setup_start:] else len(notes)
                                pos_data["setup_type"] = notes[setup_start:setup_start + setup_end].strip()

                            snapshot = getattr(self, '_last_snapshot_data', None)
                            exit_rec = coordinator.get_exit_intelligence(
                                pos_data, market_data=snapshot
                            )

                            # RQ9 measurement fix (2026-07-02): HOLD decisions were
                            # NEVER logged (0 records ever in exit_decisions.jsonl),
                            # so the agent's most frequent output was unscoreable.
                            # Log them (sampled 1-in-N via EXIT_HOLD_LOG_SAMPLE_N,
                            # default 1 = every hold). Advisory-only: nothing applied.
                            if exit_rec and exit_rec.get("action", "hold") == "hold":
                                try:
                                    _hold_n = max(1, int(os.getenv("EXIT_HOLD_LOG_SAMPLE_N", "1") or "1"))
                                    self._hold_log_counter = getattr(self, "_hold_log_counter", 0) + 1
                                    if self._hold_log_counter % _hold_n == 0:
                                        _hold_decision = ExitDecision(
                                            symbol=symbol,
                                            exit_action="hold",
                                            exit_confidence=0.5,
                                            reason=f"[LLM-EXIT] {exit_rec.get('reason', '')[:120]}",
                                        )
                                        self.exit_engine._log_decision(
                                            _hold_decision, pos, True,
                                            f"hold (advisory; urgency={exit_rec.get('urgency', 'low')}, "
                                            f"thesis_valid={exit_rec.get('thesis_still_valid')})",
                                        )
                                except Exception as _hle:
                                    logger.debug(f"[EXIT-INTEL-LLM] hold-log error for {symbol}: {_hle}")

                            if exit_rec and exit_rec.get("action", "hold") != "hold":
                                exit_action = exit_rec["action"]
                                urgency = exit_rec.get("urgency", "medium")

                                decision = None
                                # Convert LLM exit recommendation to ExitDecision
                                if exit_action == "tighten_sl" and exit_rec.get("new_sl"):
                                    decision = ExitDecision(
                                        symbol=symbol,
                                        exit_action="tighten_sl",
                                        exit_confidence=0.80 if urgency in ("high", "critical") else 0.65,
                                        new_sl=exit_rec["new_sl"],
                                        reason=f"[LLM-EXIT] {exit_rec.get('reason', '')[:120]}",
                                    )
                                elif exit_action == "widen_tp" and exit_rec.get("new_tp"):
                                    decision = ExitDecision(
                                        symbol=symbol,
                                        exit_action="widen_tp",
                                        exit_confidence=0.70,
                                        new_tp=exit_rec["new_tp"],
                                        reason=f"[LLM-EXIT] {exit_rec.get('reason', '')[:120]}",
                                    )
                                elif exit_action == "partial_close":
                                    decision = ExitDecision(
                                        symbol=symbol,
                                        exit_action="partial",
                                        exit_confidence=0.75 if urgency in ("high", "critical") else 0.60,
                                        partial_pct=exit_rec.get("partial_pct", 0.5),
                                        reason=f"[LLM-EXIT] {exit_rec.get('reason', '')[:120]}",
                                    )
                                elif exit_action == "full_close":
                                    decision = ExitDecision(
                                        symbol=symbol,
                                        exit_action="close",
                                        exit_confidence=0.85 if urgency == "critical" else 0.75,
                                        reason=f"[LLM-EXIT] {exit_rec.get('reason', '')[:120]}",
                                    )

                                if decision is not None:
                                    # Skip heuristic rules — LLM made the call
                                    result = self.exit_engine.apply_exit_decision(
                                        decision=decision,
                                        position=pos,
                                        current_price=current_price,
                                    )
                                    if result["applied"]:
                                        action_name = result["action"]
                                        logger.info(
                                            f"[EXIT-INTEL-LLM] {symbol} {action_name}: "
                                            f"{result['details']} (urgency={urgency})"
                                        )
                                        if action_name == "close":
                                            _exit_pos = self.pos_mgr.positions.get(symbol)
                                            if _exit_pos and _exit_pos.qty > 0:
                                                _ex_side = "SELL" if _exit_pos.side == "LONG" else "BUY"
                                                _llm_close = self.order_executor.close_position(
                                                    symbol, _ex_side, _exit_pos.qty, current_price,
                                                    reason="LLM_EXIT_AGENT"
                                                )
                                                if _llm_close and getattr(_llm_close, "filled", False):
                                                    _fc = self.pos_mgr.force_close(symbol, current_price, "LLM_EXIT_AGENT")
                                                    # Store force_close event for post-trade callbacks
                                                    # (record_outcome, learning, ledger, etc).
                                                    # Event will be injected into main event loop by
                                                    # next symbol processing cycle.
                                                    if _fc:
                                                        _fc.metadata["_exchange_submitted"] = True
                                                        self._pending_exit_events.append(_fc)
                                                else:
                                                    logger.critical(
                                                        f"[{symbol}] LLM EXIT CLOSE FAILED — position still open. "
                                                        f"Reconciliation will handle."
                                                    )
                                        elif action_name == "partial":
                                            partial_pct = result.get("partial_pct", 0.5)
                                            close_qty = round_qty(symbol, pos.qty * partial_pct)
                                            if close_qty > 0:
                                                _part_side = "SELL" if pos.side == "LONG" else "BUY"
                                                _part_close = self.order_executor.close_position(
                                                    symbol, _part_side, close_qty, current_price,
                                                    reason="LLM_EXIT_PARTIAL"
                                                )
                                                if _part_close and getattr(_part_close, "filled", False):
                                                    # Wiring audit #2 (2026-07-01): book the leg via
                                                    # partial_close() (fee/PnL/funding/qty) and inject
                                                    # the event so equity + log_trade flow through the
                                                    # main event loop. Previously only `pos.qty -=`.
                                                    _pev = self.pos_mgr.partial_close(
                                                        symbol, partial_pct, current_price,
                                                        action="LLM_EXIT_PARTIAL", qty=close_qty,
                                                    )
                                                    if _pev:
                                                        _pev.metadata["_exchange_submitted"] = True
                                                        self._pending_exit_events.append(_pev)
                                                    logger.info(
                                                        f"[EXIT-INTEL-LLM] {symbol} partial: "
                                                        f"closed {close_qty:.6f}, remaining {pos.qty:.6f}"
                                                    )
                                                else:
                                                    logger.critical(
                                                        f"[{symbol}] LLM PARTIAL CLOSE FAILED — "
                                                        f"keeping full position. Reconciliation will handle."
                                                    )
                                    self.exit_engine.mark_evaluated(symbol)
                                    continue  # Skip heuristic rules below
                    except Exception as e:
                        logger.debug(f"[EXIT-INTEL-LLM] Error for {symbol}: {e}")

                # ── Deep memory pattern lookup ──
                # Check if the strategy combo that opened this trade is historically
                # profitable or a known loser, so we can be more/less aggressive
                strategy_key = pos.strategy  # e.g. "RegimeTrend,MonteCarlo"
                strategy_wr = 0.5  # default neutral
                strategy_sample_size = 0
                if strategy_effectiveness and strategy_key in strategy_effectiveness:
                    se = strategy_effectiveness[strategy_key]
                    strategy_wr = se.get("win_rate", 0.5)
                    strategy_sample_size = se.get("total", 0)

                # Also check symbol-specific history from deep memory
                symbol_wr = 0.5
                if deep_mem:
                    try:
                        symbol_trades = deep_mem.trade_dna.get_by_symbol(symbol, limit=20)
                        if len(symbol_trades) >= 5:
                            wins = sum(1 for t in symbol_trades if t.get("outcome") == "WIN")
                            symbol_wr = wins / len(symbol_trades)
                    except Exception:
                        pass

                # ── Heuristic exit decision rules ──
                # These rules use regime, PnL, funding, and deep memory patterns
                # to make fast, cost-free decisions (no LLM API call per position)
                decision = None
                risk_distance = abs(pos.entry - pos.original_sl) if pos.original_sl else 0
                equity = self.risk_mgr.equity if self.risk_mgr.equity > 0 else 1.0
                loss_pct_of_equity = abs(unrealized_pnl) / equity if unrealized_pnl < 0 else 0

                # Rule 1: Panic/crash regime + LONG position -> tighten SL aggressively
                if regime in ("panic", "crash", "extreme_fear") and is_long:
                    # Move SL to halfway between current SL and current price
                    new_sl = (pos.sl + current_price) / 2.0
                    if new_sl > pos.sl:  # Only tighten (move up for longs)
                        confidence = 0.75 if regime == "panic" else 0.85
                        decision = ExitDecision(
                            symbol=symbol,
                            exit_action="tighten_sl",
                            exit_confidence=confidence,
                            new_sl=new_sl,
                            reason=f"Regime={regime}, protecting capital on LONG "
                                   f"(strategy WR={strategy_wr:.0%} in this combo)",
                        )

                # Rule 2: Unrealized loss > 2% of equity -> tighten SL
                elif loss_pct_of_equity > 0.02:
                    # Tighten to 60% of remaining distance
                    if is_long:
                        new_sl = pos.sl + (current_price - pos.sl) * 0.4
                    else:
                        new_sl = pos.sl - (pos.sl - current_price) * 0.4
                    # Only if it actually tightens
                    tightens = (is_long and new_sl > pos.sl) or (not is_long and new_sl < pos.sl)
                    if tightens:
                        # Lower confidence if strategy has a good track record (give it room)
                        conf = 0.65 if strategy_wr < 0.5 or strategy_sample_size < 5 else 0.55
                        decision = ExitDecision(
                            symbol=symbol,
                            exit_action="tighten_sl",
                            exit_confidence=conf,
                            new_sl=new_sl,
                            reason=f"Unrealized loss {loss_pct_of_equity:.1%} of equity "
                                   f"(strat WR={strategy_wr:.0%}, n={strategy_sample_size})",
                        )

                # Rule 3: Big winner -> partial close or widen TP based on pattern strength
                elif risk_distance > 0 and unrealized_pnl > 0:
                    gain_vs_risk = abs(unrealized_pct * pos.entry) / risk_distance
                    if gain_vs_risk >= 3.0 and pos.state not in ("TP1_HIT", "TRAILING"):
                        # On confirmed winning patterns, widen TP instead of partial close
                        if strategy_wr >= 0.6 and strategy_sample_size >= 10:
                            # Strong pattern -> let it ride, widen TP2
                            if is_long:
                                new_tp = current_price + risk_distance * 2.0
                                if new_tp > pos.tp2:
                                    decision = ExitDecision(
                                        symbol=symbol,
                                        exit_action="widen_tp",
                                        exit_confidence=0.70,
                                        new_tp=new_tp,
                                        reason=f"Confirmed winning pattern "
                                               f"(strat WR={strategy_wr:.0%}, n={strategy_sample_size}), "
                                               f"gain={gain_vs_risk:.1f}x risk — letting winner run",
                                    )
                            else:
                                new_tp = current_price - risk_distance * 2.0
                                if new_tp < pos.tp2:
                                    decision = ExitDecision(
                                        symbol=symbol,
                                        exit_action="widen_tp",
                                        exit_confidence=0.70,
                                        new_tp=new_tp,
                                        reason=f"Confirmed winning pattern "
                                               f"(strat WR={strategy_wr:.0%}, n={strategy_sample_size}), "
                                               f"gain={gain_vs_risk:.1f}x risk — letting winner run",
                                    )
                        else:
                            # Unproven or weak pattern -> lock in partial profit
                            decision = ExitDecision(
                                symbol=symbol,
                                exit_action="partial",
                                exit_confidence=0.65,
                                partial_pct=0.5,
                                reason=f"Gain={gain_vs_risk:.1f}x risk, "
                                       f"pattern unproven (strat WR={strategy_wr:.0%}, "
                                       f"n={strategy_sample_size}) — locking 50%",
                            )

                # Rule 4: Adverse funding rate > 0.05% -> tighten SL
                elif abs(funding_rate) > 0.0005:
                    funding_is_adverse = (is_long and funding_rate > 0) or \
                                         (not is_long and funding_rate < 0)
                    if funding_is_adverse and hold_minutes > 30:
                        # Tighten SL modestly (20% closer to price)
                        if is_long:
                            new_sl = pos.sl + (current_price - pos.sl) * 0.2
                        else:
                            new_sl = pos.sl - (pos.sl - current_price) * 0.2
                        tightens = (is_long and new_sl > pos.sl) or (not is_long and new_sl < pos.sl)
                        if tightens:
                            decision = ExitDecision(
                                symbol=symbol,
                                exit_action="tighten_sl",
                                exit_confidence=0.55,
                                new_sl=new_sl,
                                reason=f"Adverse funding rate {funding_rate:.5f} "
                                       f"(hold={hold_minutes:.0f}min, "
                                       f"symbol WR={symbol_wr:.0%})",
                            )

                # ── Apply decision via exit engine ──
                if decision is not None:
                    result = self.exit_engine.apply_exit_decision(
                        decision=decision,
                        position=pos,
                        current_price=current_price,
                    )

                    if result["applied"]:
                        action = result["action"]
                        logger.info(
                            f"[EXIT-INTEL] {symbol} {action}: {result['details']} "
                            f"(regime={regime}, hold={hold_minutes:.0f}min)"
                        )

                        # Handle close/partial actions that need exchange execution
                        if action == "close":
                            _exit_pos2 = self.pos_mgr.positions.get(symbol)
                            if _exit_pos2 and _exit_pos2.qty > 0:
                                _ex_side2 = "SELL" if _exit_pos2.side == "LONG" else "BUY"
                                _eng_close = self.order_executor.close_position(
                                    symbol, _ex_side2, _exit_pos2.qty, current_price,
                                    reason="LLM_EXIT_ENGINE"
                                )
                                if _eng_close and getattr(_eng_close, "filled", False):
                                    # Wiring audit #51 (2026-07-01): mirror the patched
                                    # LLM_EXIT_AGENT pattern — inject the close event so
                                    # equity/log_trade/learning book it (was discarded).
                                    _eng_fc = self.pos_mgr.force_close(symbol, current_price, "LLM_EXIT_ENGINE")
                                    if _eng_fc:
                                        _eng_fc.metadata["_exchange_submitted"] = True
                                        self._pending_exit_events.append(_eng_fc)
                                else:
                                    logger.critical(
                                        f"[{symbol}] EXIT ENGINE CLOSE FAILED — position still open. "
                                        f"Reconciliation will handle."
                                    )
                        elif action == "partial":
                            # Partial close: submit exchange order, then update internal state
                            partial_pct = result.get("partial_pct", 0.5)
                            close_qty = round_qty(symbol, pos.qty * partial_pct)
                            if close_qty > 0:
                                _part_side2 = "SELL" if pos.side == "LONG" else "BUY"
                                _part_close2 = self.order_executor.close_position(
                                    symbol, _part_side2, close_qty, current_price,
                                    reason="EXIT_ENGINE_PARTIAL"
                                )
                                if _part_close2 and getattr(_part_close2, "filled", False):
                                    # Wiring audit #2 (2026-07-01): book the leg via
                                    # partial_close() (fee/PnL/funding/qty) and inject the
                                    # event so equity + log_trade flow through the main
                                    # event loop. Previously only `pos.qty -= close_qty`.
                                    _pev2 = self.pos_mgr.partial_close(
                                        symbol, partial_pct, current_price,
                                        action="EXIT_ENGINE_PARTIAL", qty=close_qty,
                                    )
                                    if _pev2:
                                        _pev2.metadata["_exchange_submitted"] = True
                                        self._pending_exit_events.append(_pev2)
                                    logger.info(
                                        f"[EXIT-INTEL] {symbol} partial close: "
                                        f"closed {close_qty:.6f}, remaining {pos.qty:.6f}"
                                    )
                                else:
                                    logger.critical(
                                        f"[{symbol}] EXIT ENGINE PARTIAL CLOSE FAILED — "
                                        f"keeping full position. Reconciliation will handle."
                                    )
                    else:
                        logger.debug(
                            f"[EXIT-INTEL] {symbol} decision not applied: "
                            f"{result.get('details', 'unknown')}"
                        )
                else:
                    # No action needed — mark as evaluated to respect cooldown
                    self.exit_engine.mark_evaluated(symbol)

            except Exception as e:
                logger.warning(f"[EXIT-INTEL] Error evaluating {symbol}: {e}")

    def _check_position_aging(self):
        """Alert on positions held too long — funding costs eat profits.
        Also enforces max hold time limits (tighten SL or force close)."""
        open_pos = self.pos_mgr.get_open_positions()
        now = time.time()
        max_hold = self.config.max_hold_hours
        hold_action = self.config.hold_limit_action

        for sym, pos in open_pos.items():
            if not hasattr(pos, 'open_time') or pos.open_time is None:
                continue
            # Calculate age
            if isinstance(pos.open_time, datetime):
                age_hours = (now - pos.open_time.timestamp()) / 3600
            else:
                age_hours = (now - pos.open_time) / 3600

            # Hold limit enforcement
            price = self._last_prices.get(sym, pos.entry)
            event = self.pos_mgr.check_hold_limits(sym, price, max_hold, hold_action)
            if event:
                logger.warning(f"[HOLD_LIMIT] {sym} force-closed after {age_hours:.0f}h")
                # Wiring audit #15 (accounting half, 2026-07-01): this TradeEvent
                # was only logged/alerted — the close never reached equity,
                # log_trade, trades.csv, or learning. Inject via
                # _pending_exit_events so the main events loop books it.
                # (Exchange submission for HOLD_LIMIT remains owner-gated.)
                if not hasattr(self, '_pending_exit_events'):
                    self._pending_exit_events = []
                self._pending_exit_events.append(event)
                if self.alerts:
                    try:
                        self.alerts.send_trade_event(
                            "HOLD_LIMIT", sym,
                            f"Force closed after {age_hours:.0f}h (max {max_hold}h)\nPnL: ${event.pnl:.2f}"
                        )
                    except Exception:
                        pass
                continue  # Position is now closed

            # Alert thresholds
            funding_rate = self._last_funding_rates.get(sym, 0.0)
            is_paying = (pos.side == "LONG" and funding_rate > 0) or \
                        (pos.side == "SHORT" and funding_rate < 0)

            # Calculate estimated funding cost since entry
            notional = pos.qty * price * pos.leverage
            periods_since_entry = age_hours / 8  # 8h funding periods
            est_funding_paid = abs(funding_rate) * periods_since_entry * notional if is_paying else 0
            est_funding_pct = est_funding_paid / self.risk_mgr.equity * 100 if self.risk_mgr.equity > 0 else 0

            # Alert conditions
            if age_hours > 24 and is_paying and est_funding_pct > 0.1:
                logger.warning(
                    f"[AGING] {sym} {pos.side} open {age_hours:.0f}h, "
                    f"estimated funding paid: {est_funding_pct:.2f}% of equity"
                )
                if self.alerts and age_hours > 48:
                    try:
                        self.alerts.send_market_update(
                            f"[POSITION AGING] {sym} {pos.side}\n"
                            f"Open: {age_hours:.0f}h\n"
                            f"Funding paid: ~{est_funding_pct:.2f}% of equity\n"
                            f"Current PnL: ${pos.realized_pnl:.2f}"
                        )
                    except Exception:
                        pass
