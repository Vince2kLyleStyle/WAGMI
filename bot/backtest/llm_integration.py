"""
LLM Multi-Agent Integration for Backtesting.

Wraps the AgentCoordinator for use in the backtest engine with:
  - Preflight validation (zero-waste: validates everything before any real API call)
  - Budget enforcement (stops LLM calls when budget exceeded, falls back to strategy-only)
  - Checkpoint/resume (atomic saves every N candles, resume from last checkpoint)
  - Per-candle error handling (never crashes the backtest, always falls back gracefully)
  - Cost tracking and progress reporting

Usage:
    llm = BacktestLLMIntegration(budget_usd=5.0)
    preflight = llm.run_preflight(symbols, all_data, ensemble, config)
    if not preflight.passed:
        print(preflight.errors)
        return

    # In walk loop:
    decision = llm.evaluate_entry(snapshot_data, signal, "pre_trade_backtest")
    exit_rec = llm.evaluate_exit(position_data, market_data)
    lesson = llm.run_learning(trade_data)
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("bot.backtest.llm")

# Model pricing per 1M tokens (input, output)
_MODEL_PRICING = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-5": (15.0, 75.0),
}

# Default pricing for unknown models (use Sonnet as conservative estimate)
_DEFAULT_PRICING = (3.0, 15.0)

# Exit agent throttle: evaluate every N candles per position
_EXIT_EVAL_INTERVAL = 6


@dataclass
class PreflightResult:
    """Result of preflight validation."""
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    estimated_cost: float = 0.0
    estimated_llm_calls: int = 0
    candle_count: int = 0


@dataclass
class CheckpointState:
    """Serializable backtest state for resume."""
    candle_index: int
    symbol: str
    symbols_completed: List[str]
    equity: float
    llm_stats: Dict[str, Any]
    timestamp: str


class BacktestLLMIntegration:
    """Wraps AgentCoordinator for backtest with reliability safeguards."""

    def __init__(
        self,
        budget_usd: float = 5.0,
        checkpoint_dir: str = "data/backtest_checkpoints",
        resume: bool = False,
    ):
        self.budget_usd = budget_usd
        self.checkpoint_dir = checkpoint_dir
        self.resume = resume

        # Coordinator (created lazily after preflight)
        self._coordinator = None

        # Cost tracking
        self.total_cost_usd: float = 0.0
        self.budget_exhausted: bool = False
        self._symbol_cost_usd: float = 0.0
        self._budget_per_symbol: float = budget_usd  # updated when symbols known
        self._num_symbols: int = 1

        # Call tracking
        self.llm_calls: int = 0
        self.llm_failures: int = 0
        self.candles_with_llm: int = 0
        self.candles_fallback: int = 0
        self.pre_filter_skips: int = 0  # Signals skipped before LLM call

        # Decision log
        self.decisions: List[Dict[str, Any]] = []
        self.decisions_log_path = os.path.join("data", "llm", "backtest_decisions.jsonl")

        # Exit decisions log (captures ALL exit agent responses)
        self.exit_decisions: List[Dict[str, Any]] = []

        # Learning lessons buffer
        self.learning_lessons: List[Dict[str, Any]] = []

        # Regime timeline (tracks transitions)
        self.regime_timeline: List[Dict[str, Any]] = []

        # Per-agent cost tracking
        self.agent_costs: Dict[str, float] = {}

        # Exit agent throttle
        self._exit_eval_counters: Dict[str, int] = {}

        # Resume state
        self.resume_state: Optional[CheckpointState] = None
        if resume:
            self.resume_state = self._load_checkpoint()

        # ── Replay-harness discipline (tools/replay_harness.py) ──────
        # All default OFF; a normal backtest is unaffected.
        # REPLAY_MAX_LLM_CALLS: hard cap on total LLM calls per run (0 = off).
        # REPLAY_LLM_SLEEP_S: sleep after each coordinator invocation so the
        #   live bot's CLI quota isn't starved by the replay burst.
        # REPLAY_MODE: journal every LLM invocation to data/replay_llm_journal.jsonl.
        try:
            self.max_llm_calls = int(os.getenv("REPLAY_MAX_LLM_CALLS", "0") or 0)
        except (TypeError, ValueError):
            self.max_llm_calls = 0
        try:
            self.llm_sleep_s = float(os.getenv("REPLAY_LLM_SLEEP_S", "0") or 0)
        except (TypeError, ValueError):
            self.llm_sleep_s = 0.0
        self.call_cap_reached = False
        self._journal_path = (
            os.path.join("data", "replay_llm_journal.jsonl")
            if os.getenv("REPLAY_MODE") else None
        )

        # ── Entry-event trigger filter (REPLAY_CAMPAIGN_PLAN §2.1) ────
        # REPLAY-gated: normal backtests keep the legacy confidence
        # pre-filter below. Fixes VAL1's first-come-first-served starvation
        # (cap burned on the weakest solo signals; multi-agree never seen).
        # LLM pipeline fires ONLY on entry events:
        #   num_agree >= 2, OR solo conf >= 75 from the whitelist strategies;
        # plus a per-symbol 4h same-direction cooldown (only the first bar
        # of a signal cluster spends calls) and a per-symbol call budget of
        # cap/num_symbols (BTC cannot starve ETH/SOL).
        self._replay_filter_on = bool(os.getenv("REPLAY_MODE"))
        try:
            self._replay_solo_conf = float(
                os.getenv("REPLAY_SOLO_CONF_MIN", "75"))
        except (TypeError, ValueError):
            self._replay_solo_conf = 75.0
        self._replay_solo_whitelist = {
            s.strip() for s in os.getenv(
                "REPLAY_SOLO_WHITELIST",
                "regime_trend,bollinger_squeeze,vmc_cipher,mean_reversion",
            ).split(",") if s.strip()
        }
        try:
            self._replay_cooldown_s = float(
                os.getenv("REPLAY_COOLDOWN_H", "4")) * 3600.0
        except (TypeError, ValueError):
            self._replay_cooldown_s = 4 * 3600.0
        self._replay_last_fire: Dict[str, float] = {}   # "SYM:SIDE" -> sim ts
        self._replay_symbol_calls: Dict[str, int] = {}  # symbol -> calls spent
        self.replay_entry_events = 0    # signals qualifying as entry events
        self.replay_starved_events = 0  # entry events lost to call caps
        self.replay_cooldown_skips = 0  # entry events inside a cluster cooldown

    # ── Preflight ─────────────────────────────────────────────────

    def run_preflight(
        self,
        symbols: List[str],
        all_data: Dict[str, Any],
        ensemble,
        config,
    ) -> PreflightResult:
        """Validate everything before making any real API calls.

        Checks (ordered cheapest to most expensive):
        1. API key present
        2. API ping (one Haiku call, ~$0.0001)
        3. Data quality (non-empty DataFrames, >= 50 candles)
        4. Strategy dry-run (ensemble.evaluate on sample candles)
        5. Snapshot builder works
        6. Coordinator instantiates
        7. Cost estimation

        Returns PreflightResult with passed=False on any fatal error.
        """
        result = PreflightResult(passed=True)

        # 1. API key check — allow CLI path (USE_CLI_LLM=true) to bypass API key requirement
        use_cli_llm = os.getenv("USE_CLI_LLM", "").lower() in ("true", "1", "yes")
        if use_cli_llm:
            logger.info("[PREFLIGHT] USE_CLI_LLM=true detected — skipping API key check, using claude CLI")
        else:
            try:
                from llm.client import get_client
                client = get_client()
                if client is None:
                    result.passed = False
                    result.errors.append(
                        "ANTHROPIC_API_KEY not set or anthropic package not installed. "
                        "No API calls possible."
                    )
                    return result
            except Exception as e:
                result.passed = False
                result.errors.append(f"Failed to initialize API client: {e}")
                return result

        # 2. API ping (one cheap Haiku call) — for CLI path, do a session-limit check
        if use_cli_llm:
            # Test the CLI session with a minimal call to detect session-limit 429 early.
            # Without this, the backtest runs all candles with every LLM call failing silently.
            try:
                from llm.claude_cli_client import call_agent as _cli_ping
                ping = _cli_ping(
                    user_prompt='{"ping":1}',
                    system_prompt="Reply with exactly: {\"ok\":true}",
                    model="haiku",
                    max_budget_usd=0.01,
                    timeout=30,
                )
                if not ping.ok and ("session limit" in (ping.error or "").lower() or "429" in (ping.error or "")):
                    result.passed = False
                    result.errors.append(
                        f"CLI SESSION LIMIT HIT — all LLM calls will fail. "
                        f"Retry after session resets (error: {ping.error[:120]})"
                    )
                    return result
                logger.info(f"[PREFLIGHT] CLI session OK (latency {ping.latency_s:.1f}s)")
            except Exception as e:
                result.warnings.append(f"CLI session ping failed (non-fatal): {e}")

        else:
            try:
                from llm.client import call_llm
                text, usage = call_llm(
                    system_prompt="Reply with exactly: OK",
                    snapshot_json="{}",
                    model="claude-haiku-4-5",
                    max_tokens=8,
                    max_retries=1,
                    timeout=15.0,
                )
                if text is None:
                    result.passed = False
                    error_msg = usage.get("error", "unknown error")
                    result.errors.append(
                        f"API ping failed: {error_msg}. "
                        "Check your API key and network connection."
                    )
                    return result
                ping_cost = self._compute_cost_from_usage(usage)
                self.total_cost_usd += ping_cost
                logger.info(f"[PREFLIGHT] API ping OK (cost: ${ping_cost:.6f})")
            except Exception as e:
                result.passed = False
                result.errors.append(f"API ping failed with exception: {e}")
            return result

        # 3. Data validation
        total_candles = 0
        for symbol in symbols:
            data = all_data.get(symbol, {})
            df_1h = data.get("1h")
            df_daily = data.get("daily")

            if df_1h is not None and not df_1h.empty:
                n = len(df_1h)
                if n < 50:
                    result.warnings.append(
                        f"{symbol}: only {n} 1h candles (need 50 for warmup)"
                    )
                else:
                    total_candles += n - 50  # Subtract warmup
            elif df_daily is not None and not df_daily.empty:
                n = len(df_daily)
                if n < 50:
                    result.warnings.append(
                        f"{symbol}: only {n} daily candles (need 50 for warmup)"
                    )
                else:
                    total_candles += n - 50
            else:
                result.warnings.append(f"{symbol}: no 1h or daily data available")

        if total_candles == 0:
            result.passed = False
            result.errors.append("No usable data for any symbol after warmup.")
            return result

        result.candle_count = total_candles

        # 4. Strategy dry-run (verify ensemble doesn't crash)
        try:
            import pandas as pd
            test_count = 0
            for symbol in symbols:
                data = all_data.get(symbol, {})
                df_1h = data.get("1h")
                if df_1h is None or df_1h.empty or len(df_1h) < 55:
                    continue
                # Test 3 candles after warmup
                for i in range(50, min(53, len(df_1h))):
                    windowed = {}
                    for tf, df in data.items():
                        if df is not None and not df.empty:
                            current_time = df_1h["time"].iloc[i]
                            mask = df["time"] <= current_time
                            windowed[tf] = df[mask].copy()
                    ensemble.evaluate(symbol, windowed)
                    test_count += 1
            if test_count == 0:
                result.warnings.append("Could not dry-run strategies (no suitable data)")
            else:
                logger.info(f"[PREFLIGHT] Strategy dry-run OK ({test_count} candles tested)")
        except Exception as e:
            result.passed = False
            result.errors.append(
                f"Strategy dry-run crashed: {e}. "
                "Fix strategy errors before spending API credits."
            )
            return result

        # 5. Snapshot builder validation
        try:
            snapshot = self._build_test_snapshot(symbols[0], 50000.0)
            snapshot_json = json.dumps(snapshot, separators=(",", ":"))
            parsed = json.loads(snapshot_json)
            if "m" not in parsed:
                result.warnings.append("Test snapshot missing 'm' key (markets)")
            logger.info(f"[PREFLIGHT] Snapshot builder OK ({len(snapshot_json)} bytes)")
        except Exception as e:
            result.passed = False
            result.errors.append(f"Snapshot builder failed: {e}")
            return result

        # 6. Coordinator instantiation — use env-var-aware factory so that
        # AGENT_*_ENABLED flags (e.g. AGENT_QUANT_ENABLED=false) are honoured.
        try:
            from llm.agents.coordinator import get_coordinator
            self._coordinator = get_coordinator()
            logger.info("[PREFLIGHT] AgentCoordinator instantiated OK")
        except Exception as e:
            result.passed = False
            result.errors.append(f"AgentCoordinator failed to instantiate: {e}")
            return result

        # 7. Cost estimation
        # Conservative estimate: 10% of candles produce signals
        estimated_signal_rate = 0.10
        estimated_signals = int(total_candles * estimated_signal_rate)
        # ~30% of signals pass risk gates -> become trades
        estimated_trades = int(estimated_signals * 0.30)
        # Exit agent: runs every EXIT_EVAL_INTERVAL candles per open position
        # Assume avg 1 position open for 20% of candles
        estimated_exit_calls = int(total_candles * 0.20 / _EXIT_EVAL_INTERVAL)
        # Learning agent: once per closed trade
        estimated_learning_calls = estimated_trades

        # Cost per entry pipeline: ~$0.007 (documented average)
        # Cost per exit call: ~$0.0002 (Haiku)
        # Cost per learning call: ~$0.0004 (Haiku)
        cost_entry = estimated_signals * 0.007
        cost_exit = estimated_exit_calls * 0.0002
        cost_learning = estimated_learning_calls * 0.0004
        estimated_total = cost_entry + cost_exit + cost_learning

        result.estimated_cost = round(estimated_total, 2)
        result.estimated_llm_calls = (
            estimated_signals * 4  # 4 agents per entry pipeline
            + estimated_exit_calls
            + estimated_learning_calls
        )

        if estimated_total > self.budget_usd:
            result.warnings.append(
                f"Estimated cost ${estimated_total:.2f} exceeds budget ${self.budget_usd:.2f}. "
                f"LLM will be disabled after budget is exhausted; remaining candles use strategy-only."
            )

        logger.info(
            f"[PREFLIGHT] Cost estimate: ${estimated_total:.2f} "
            f"({result.estimated_llm_calls} API calls, {total_candles} candles)"
        )

        # Set per-symbol budget so each symbol gets a fair share
        self._num_symbols = max(len(symbols), 1)
        self._budget_per_symbol = self.budget_usd / self._num_symbols
        logger.info(
            f"[PREFLIGHT] Per-symbol budget: ${self._budget_per_symbol:.2f} "
            f"({self._num_symbols} symbols)"
        )

        # 8. Learning systems validation — prevent spend-then-crash
        try:
            from llm.agents.learning_integration import process_agent_lesson  # noqa: F401
            from llm.deep_memory import get_deep_memory
            get_deep_memory()
        except Exception as e:
            result.warnings.append(
                f"Learning systems init warning: {e}. "
                f"Lessons may not persist to deep memory."
            )

        return result

    def reset_for_symbol(self, symbol: str):
        """Reset per-symbol budget tracking at the start of each symbol walk.

        This ensures each symbol gets a fair share of the total LLM budget
        instead of the first symbol consuming everything.
        """
        self._symbol_cost_usd = 0.0
        # Re-enable LLM if global budget not yet exhausted
        if self.total_cost_usd < self.budget_usd:
            self.budget_exhausted = False
        logger.info(
            f"[BACKTEST-LLM] Starting {symbol}: "
            f"symbol budget ${self._budget_per_symbol:.2f}, "
            f"global spent ${self.total_cost_usd:.2f}/${self.budget_usd:.2f}"
        )

    # ── Replay discipline ─────────────────────────────────────────

    def _replay_discipline(self, kind: str, calls_delta: int, cost: float,
                           symbol: str = "", extra: Optional[Dict[str, Any]] = None):
        """Replay-harness bookkeeping after a coordinator invocation.

        Journals the call, enforces the hard call cap, and rate-limits so
        the live bot's CLI quota isn't starved. No-op unless the REPLAY_*
        env vars are set (normal backtests are unaffected).
        """
        if symbol and calls_delta:
            # Per-symbol call accounting for the entry-event filter budget
            self._replay_symbol_calls[symbol] = (
                self._replay_symbol_calls.get(symbol, 0) + calls_delta)
        if self._journal_path:
            try:
                os.makedirs(os.path.dirname(self._journal_path), exist_ok=True)
                record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "kind": kind,
                    "symbol": symbol,
                    "calls_delta": calls_delta,
                    "total_calls": self.llm_calls,
                    "cost_delta_usd": round(cost, 6),
                    "total_cost_usd": round(self.total_cost_usd, 6),
                    "cap": self.max_llm_calls,
                }
                if extra:
                    record.update(extra)
                with open(self._journal_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
            except Exception:
                pass  # Journaling must never break the run
        if (self.max_llm_calls > 0 and self.llm_calls >= self.max_llm_calls
                and not self.call_cap_reached):
            self.call_cap_reached = True
            logger.warning(
                f"[REPLAY] LLM call cap reached ({self.llm_calls}/"
                f"{self.max_llm_calls}) — no further LLM calls this run."
            )
        if self.llm_sleep_s > 0 and calls_delta > 0:
            time.sleep(self.llm_sleep_s)

    # ── Pre-LLM Signal Filter ─────────────────────────────────────

    def _replay_is_entry_event(self, signal) -> bool:
        """True if the signal qualifies as an entry event (plan §2.1):
        multi-strategy confluence (num_agree >= 2) OR a whitelisted solo
        strategy at conf >= REPLAY_SOLO_CONF_MIN (0-100 scale)."""
        meta = getattr(signal, "metadata", None) or {}
        try:
            if int(meta.get("num_agree", 1) or 1) >= 2:
                return True
        except (TypeError, ValueError):
            pass
        try:
            conf = float(getattr(signal, "confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        strat = str(getattr(signal, "strategy", "") or "")
        return strat in self._replay_solo_whitelist and conf >= self._replay_solo_conf

    def _replay_symbol_cap(self) -> int:
        """Per-symbol LLM call budget = global cap / num_symbols (0 = off)."""
        if self.max_llm_calls <= 0:
            return 0
        return max(1, self.max_llm_calls // max(self._num_symbols, 1))

    def _replay_should_skip(self, signal, symbol: str) -> bool:
        """REPLAY-only entry-event trigger filter (REPLAY_CAMPAIGN_PLAN §2.1).

        Returns True (skip the LLM pipeline) unless:
        - the signal is an entry event (multi-agree OR whitelisted solo >= 75), AND
        - the symbol still has per-symbol call budget (cap/num_symbols), AND
        - the symbol+direction is outside its 4h cooldown (only the first bar
          of a persistent signal cluster spends calls).
        """
        if not self._replay_is_entry_event(signal):
            return True
        self.replay_entry_events += 1

        cap = self._replay_symbol_cap()
        if cap and self._replay_symbol_calls.get(symbol, 0) >= cap:
            self.replay_starved_events += 1
            return True

        meta = getattr(signal, "metadata", None) or {}
        side = str(getattr(signal, "side", "") or "").upper()
        key = f"{symbol}:{side}"
        sim_ts = meta.get("replay_sim_ts")  # stamped by engine._apply_llm_entry
        try:
            sim_ts = float(sim_ts) if sim_ts is not None else None
        except (TypeError, ValueError):
            sim_ts = None
        last = self._replay_last_fire.get(key)
        if (sim_ts is not None and last is not None
                and 0 <= (sim_ts - last) < self._replay_cooldown_s):
            self.replay_cooldown_skips += 1
            return True
        if sim_ts is not None:
            self._replay_last_fire[key] = sim_ts
        return False

    def _should_skip_llm(self, snapshot_data: Optional[dict], signal) -> bool:
        """Pre-filter signals BEFORE calling the LLM API to save budget.

        Skips signals that are almost certainly going to be vetoed anyway:
        - No signal at all
        - Solo strategy signal with low confidence (< 55%)
        - Signal in low_liquidity regime (hard limit in Trade Agent prompt)

        In REPLAY_MODE the legacy check is replaced by the entry-event
        trigger filter (see _replay_should_skip).

        Returns True if we should skip the LLM call entirely.
        """
        if not snapshot_data or not signal:
            return True

        if self._replay_filter_on:
            symbol = ""
            markets = snapshot_data.get("m", [])
            if markets:
                symbol = markets[0].get("s", "")
            return self._replay_should_skip(signal, symbol)

        # Check signal confidence — solo signals below floor almost always get vetoed.
        # Threshold reads from ENSEMBLE_CONFIDENCE_FLOOR (default 55% if not set).
        # In OVERDRIVE/paper-trading mode with floor=20, solo signals down to 20% pass through.
        _solo_floor = float(os.getenv("ENSEMBLE_CONFIDENCE_FLOOR", "55")) / 100.0
        markets = snapshot_data.get("m", [])
        if markets:
            sigs = markets[0].get("sg", [])
            if sigs and len(sigs) == 1:
                # Solo strategy signal — check confidence
                sig_conf = sigs[0].get("c", 0)
                if sig_conf < _solo_floor:
                    return True

        return False

    # ── Entry Evaluation ──────────────────────────────────────────

    def evaluate_entry(
        self,
        snapshot_data: Optional[dict],
        signal,
        trigger_reason: str = "pre_trade_backtest",
    ):
        """Run multi-agent pipeline on a signal. Returns LLMDecision or None.

        On ANY failure: returns None (strategy-only fallback), never crashes.
        """
        if self.budget_exhausted or self.call_cap_reached:
            # Replay sample purity: post-cap signals stay forced-skip, but
            # count the entry events the cap starved (report honesty).
            if (self._replay_filter_on and signal is not None
                    and self._replay_is_entry_event(signal)):
                self.replay_starved_events += 1
            self.candles_fallback += 1
            return None

        if not snapshot_data or not isinstance(snapshot_data, dict):
            self.candles_fallback += 1
            return None

        if self._coordinator is None:
            self.candles_fallback += 1
            return None

        # Pre-filter: skip obviously bad signals before spending API credits
        if self._should_skip_llm(snapshot_data, signal):
            self.pre_filter_skips += 1
            self.candles_fallback += 1
            return None

        try:
            decision = self._coordinator.get_trading_decision(
                snapshot_data, trigger_reason=trigger_reason
            )

            # Track cost (global + per-symbol)
            stats = self._coordinator.get_stats()
            call_cost = self._compute_cost_from_stats(stats)
            self.total_cost_usd += call_cost
            self._symbol_cost_usd += call_cost
            _calls_delta = stats.get("total_calls", 0)
            self.llm_calls += _calls_delta
            self._replay_discipline(
                "entry", _calls_delta, call_cost,
                symbol=(snapshot_data.get("m", [{}])[0].get("s", "")
                        if snapshot_data else ""),
                extra={
                    "decision": (getattr(decision, "action", None) or "none")
                                if decision else "none",
                    "decision_confidence": getattr(decision, "confidence", None)
                                           if decision else None,
                    "regime": getattr(decision, "regime", None)
                              if decision else None,
                    # A/B verdict metrics (THOUGHT_JOURNAL 2026-07-02 23:30Z):
                    # discrimination spread + positive-subset test need the
                    # forward return of REJECTED signals too — journal the
                    # signal's side/price/sim-time so synthesis can score
                    # approved vs rejected against the candle cache.
                    "signal_side": str(getattr(signal, "side", "") or ""),
                    "signal_entry": getattr(signal, "entry", None),
                    "signal_strategy": str(getattr(signal, "strategy", "")
                                           or ""),
                    "signal_confidence": getattr(signal, "confidence", None),
                    "sim_ts": (getattr(signal, "metadata", None)
                               or {}).get("replay_sim_ts"),
                },
            )

            if decision:
                self.candles_with_llm += 1
                self._log_decision(decision, snapshot_data, call_cost, trigger_reason)

                # Persist memory update from Trade Agent's 'mu' field
                # (mirrors decision_engine.py:726-730 behavior)
                if decision.memory_update:
                    try:
                        from llm.memory_store import apply_memory_update
                        symbol = ""
                        markets = snapshot_data.get("m", []) if snapshot_data else []
                        if markets:
                            symbol = markets[0].get("s", "")
                        apply_memory_update(
                            decision.memory_update,
                            symbol=symbol,
                            regime=decision.regime or "",
                        )
                    except Exception as e:
                        logger.debug(f"[BACKTEST-LLM] Memory update failed: {e}")
            else:
                self.candles_fallback += 1
                self._log_skipped_decision(
                    snapshot_data, call_cost, trigger_reason,
                    reason="coordinator_returned_none",
                )

            # Check budget (per-symbol first, then global)
            if self._symbol_cost_usd >= self._budget_per_symbol:
                self.budget_exhausted = True
                logger.warning(
                    f"[BACKTEST-LLM] Symbol budget exhausted: "
                    f"${self._symbol_cost_usd:.2f} >= ${self._budget_per_symbol:.2f}. "
                    f"Remaining candles for this symbol use strategy-only."
                )
            elif self.total_cost_usd >= self.budget_usd:
                self.budget_exhausted = True
                logger.warning(
                    f"[BACKTEST-LLM] Budget exhausted: "
                    f"${self.total_cost_usd:.2f} >= ${self.budget_usd:.2f}. "
                    f"Remaining candles will use strategy-only."
                )

            return decision

        except Exception as e:
            import traceback
            logger.warning(
                f"[BACKTEST-LLM] Entry evaluation failed: {e}\n"
                f"{traceback.format_exc()}"
            )
            self.llm_failures += 1
            self.candles_fallback += 1
            return None

    # ── Exit Evaluation ───────────────────────────────────────────

    def evaluate_exit(
        self,
        position_data: Dict[str, Any],
        market_data: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run Exit Agent on an open position. Returns recommendation or None.

        Throttled: only evaluates every EXIT_EVAL_INTERVAL candles per position.
        """
        if self.budget_exhausted or self.call_cap_reached or self._coordinator is None:
            return None

        symbol = position_data.get("symbol", "")

        # Throttle: evaluate on first candle (counter==1) and every N candles after
        counter = self._exit_eval_counters.get(symbol, 0) + 1
        self._exit_eval_counters[symbol] = counter
        if counter != 1 and counter % _EXIT_EVAL_INTERVAL != 0:
            return None

        try:
            result = self._coordinator.get_exit_intelligence(
                position_data, market_data
            )

            stats = self._coordinator.get_stats()
            call_cost = self._compute_cost_from_stats(stats)
            self.total_cost_usd += call_cost
            _calls_delta = stats.get("total_calls", 0)
            self.llm_calls += _calls_delta
            self._replay_discipline("exit", _calls_delta, call_cost, symbol=symbol)

            if self.total_cost_usd >= self.budget_usd:
                self.budget_exhausted = True

            # Log ALL exit decisions for audit trail and learning
            if result:
                self._log_exit_decision(result, position_data, call_cost)

            return result

        except Exception as e:
            logger.warning(f"[BACKTEST-LLM] Exit evaluation failed for {symbol}: {e}")
            self.llm_failures += 1
            return None

    def clear_exit_counter(self, symbol: str):
        """Reset exit eval counter when a position closes."""
        self._exit_eval_counters.pop(symbol, None)

    # ── Learning ──────────────────────────────────────────────────

    def run_learning(self, trade_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run Learning Agent after a trade closes. Returns lesson or None.

        Feeds lessons into ALL 6 growth systems via process_agent_lesson():
        deep memory, post-trade learner, hypothesis tracker, self-teaching,
        improvement proposals, and calibration ledger.
        """
        if self.budget_exhausted or self.call_cap_reached or self._coordinator is None:
            return None

        try:
            result = self._coordinator.get_post_trade_lesson(trade_data)

            stats = self._coordinator.get_stats()
            call_cost = self._compute_cost_from_stats(stats)
            self.total_cost_usd += call_cost
            _calls_delta = stats.get("total_calls", 0)
            self.llm_calls += _calls_delta
            self._replay_discipline(
                "learning", _calls_delta, call_cost,
                symbol=str(trade_data.get("symbol", "")),
            )

            if self.total_cost_usd >= self.budget_usd:
                self.budget_exhausted = True

            # CRITICAL: Feed lesson into ALL growth systems
            if result:
                self.learning_lessons.append(result)
                try:
                    from llm.agents.learning_integration import process_agent_lesson
                    process_agent_lesson(result, trade_data)
                except Exception as e:
                    logger.debug(f"[BACKTEST-LLM] Learning integration feed error: {e}")

            return result

        except Exception as e:
            logger.warning(f"[BACKTEST-LLM] Learning agent failed: {e}")
            self.llm_failures += 1
            return None

    # ── Snapshot Building ─────────────────────────────────────────

    def build_backtest_snapshot(
        self,
        symbol: str,
        windowed_data: Dict[str, Any],
        signal,
        current_price: float,
        open_positions: Dict[str, Any],
        equity: float,
        daily_pnl: float = 0.0,
        circuit_breaker_active: bool = False,
    ) -> Optional[dict]:
        """Build a snapshot dict compatible with coordinator.get_trading_decision().

        Constructs the compact format that agents expect from the data available
        in the backtest walk loop.
        """
        try:
            import pandas as pd

            # Build market entry for this symbol
            market = {"s": symbol, "p": _round_price(current_price)}

            # Compute price changes from 1h data
            df_1h = windowed_data.get("1h")
            if df_1h is not None and not df_1h.empty and len(df_1h) >= 2:
                prev_close = float(df_1h["close"].iloc[-2])
                if prev_close > 0:
                    chg_1h = (current_price - prev_close) / prev_close * 100
                    market["d1h"] = round(chg_1h, 1)

                if len(df_1h) >= 25:
                    close_24h_ago = float(df_1h["close"].iloc[-25])
                    if close_24h_ago > 0:
                        chg_24h = (current_price - close_24h_ago) / close_24h_ago * 100
                        market["d24h"] = round(chg_24h, 1)

                # Volume ratio
                if "volume" in df_1h.columns:
                    recent_vol = float(df_1h["volume"].iloc[-1])
                    avg_vol = float(df_1h["volume"].iloc[-20:].mean())
                    if avg_vol > 0:
                        market["vr"] = round(recent_vol / avg_vol, 1)

                # Volatility (ATR-based)
                if len(df_1h) >= 14:
                    highs = df_1h["high"].iloc[-14:].values
                    lows = df_1h["low"].iloc[-14:].values
                    closes = df_1h["close"].iloc[-15:-1].values
                    if len(closes) == 14:
                        tr = []
                        for j in range(14):
                            tr.append(max(
                                float(highs[j]) - float(lows[j]),
                                abs(float(highs[j]) - float(closes[j])),
                                abs(float(lows[j]) - float(closes[j])),
                            ))
                        atr = sum(tr) / 14
                        if current_price > 0:
                            market["vol"] = round(atr / current_price, 4)

            # Add signal data
            if signal:
                sig = {
                    "st": signal.strategy,
                    "sd": signal.side.lower(),
                    "c": round(signal.confidence / 100.0, 2),  # Normalize to 0-1
                }
                market["sg"] = [sig]

            # Build global context
            global_ctx = {
                "eq": round(equity, 0),
                "pos": len(open_positions),
                "pnl": round(daily_pnl, 1),
                "btc": _round_price(current_price) if symbol == "BTC" else 0,
                "b1h": 0.0,
                "b24h": 0.0,
                "eb": 0.0,
            }
            if circuit_breaker_active:
                global_ctx["cb"] = True

            # Build position context
            positions = []
            for sym, pos in open_positions.items():
                positions.append({
                    "s": sym,
                    "sd": pos.side.lower() if hasattr(pos, "side") else "long",
                    "e": _round_price(pos.entry) if hasattr(pos, "entry") else 0,
                    "lev": pos.leverage if hasattr(pos, "leverage") else 1.0,
                    "st": pos.state if hasattr(pos, "state") else "OPEN",
                })

            snapshot = {
                "m": [market],
                "g": global_ctx,
            }
            if positions:
                snapshot["pos"] = positions

            return snapshot

        except Exception as e:
            logger.warning(f"[BACKTEST-LLM] Snapshot build failed for {symbol}: {e}")
            return None

    # ── Checkpoint / Resume ───────────────────────────────────────

    def save_checkpoint(
        self,
        candle_index: int,
        symbol: str,
        symbols_completed: List[str],
        equity: float,
    ):
        """Save checkpoint state atomically.

        Flushes accumulated decisions to JSONL first so they survive crashes.
        """
        # Flush accumulated data to disk before checkpointing
        self.flush_decisions()

        try:
            os.makedirs(self.checkpoint_dir, exist_ok=True)
            state = {
                "candle_index": candle_index,
                "symbol": symbol,
                "symbols_completed": symbols_completed,
                "equity": equity,
                "llm_stats": {
                    "total_cost_usd": self.total_cost_usd,
                    "llm_calls": self.llm_calls,
                    "llm_failures": self.llm_failures,
                    "candles_with_llm": self.candles_with_llm,
                    "candles_fallback": self.candles_fallback,
                    "budget_exhausted": self.budget_exhausted,
                    "agent_costs": dict(self.agent_costs),
                    "regime_timeline": self.regime_timeline,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            checkpoint_path = os.path.join(self.checkpoint_dir, "checkpoint.json")
            tmp_path = checkpoint_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, checkpoint_path)

        except Exception as e:
            logger.warning(f"[BACKTEST-LLM] Checkpoint save failed: {e}")

    def _load_checkpoint(self) -> Optional[CheckpointState]:
        """Load most recent checkpoint."""
        checkpoint_path = os.path.join(self.checkpoint_dir, "checkpoint.json")
        if not os.path.exists(checkpoint_path):
            logger.info("[BACKTEST-LLM] No checkpoint found, starting fresh")
            return None

        try:
            with open(checkpoint_path) as f:
                data = json.load(f)

            # Restore LLM stats
            stats = data.get("llm_stats", {})
            self.total_cost_usd = stats.get("total_cost_usd", 0.0)
            self.llm_calls = stats.get("llm_calls", 0)
            self.llm_failures = stats.get("llm_failures", 0)
            self.candles_with_llm = stats.get("candles_with_llm", 0)
            self.candles_fallback = stats.get("candles_fallback", 0)
            self.budget_exhausted = stats.get("budget_exhausted", False)
            self.agent_costs = stats.get("agent_costs", {})
            self.regime_timeline = stats.get("regime_timeline", [])

            state = CheckpointState(
                candle_index=data["candle_index"],
                symbol=data["symbol"],
                symbols_completed=data.get("symbols_completed", []),
                equity=data["equity"],
                llm_stats=stats,
                timestamp=data.get("timestamp", ""),
            )

            logger.info(
                f"[BACKTEST-LLM] Resumed from checkpoint: "
                f"symbol={state.symbol}, candle={state.candle_index}, "
                f"equity=${state.equity:.2f}, cost=${self.total_cost_usd:.2f}"
            )
            return state

        except Exception as e:
            logger.warning(f"[BACKTEST-LLM] Checkpoint load failed: {e}")
            return None

    # ── Progress / Reporting ──────────────────────────────────────

    def get_progress_line(self, candle_idx: int, total_candles: int) -> str:
        """Format a progress line for console output."""
        budget_pct = (
            self.total_cost_usd / self.budget_usd * 100
            if self.budget_usd > 0
            else 0
        )
        return (
            f"[BACKTEST-LLM] [{candle_idx}/{total_candles}] "
            f"LLM: {self.candles_with_llm} calls (${self.total_cost_usd:.2f}) | "
            f"Pre-filtered: {self.pre_filter_skips} | "
            f"Fallback: {self.candles_fallback} | "
            f"Budget: ${self.total_cost_usd:.2f}/${self.budget_usd:.2f} ({budget_pct:.1f}%)"
        )

    def get_summary(self) -> Dict[str, Any]:
        """Return summary dict for the backtest report."""
        return {
            "total_cost_usd": round(self.total_cost_usd, 4),
            "budget_usd": self.budget_usd,
            "budget_used_pct": round(
                self.total_cost_usd / self.budget_usd * 100
                if self.budget_usd > 0
                else 0,
                1,
            ),
            "llm_calls": self.llm_calls,
            "llm_failures": self.llm_failures,
            "candles_with_llm": self.candles_with_llm,
            "candles_fallback": self.candles_fallback,
            "pre_filter_skips": self.pre_filter_skips,
            "budget_exhausted": self.budget_exhausted,
            "decisions_logged": len(self.decisions),
            "agent_costs": dict(self.agent_costs),
            "exit_decisions_logged": len(self.exit_decisions),
            "learning_lessons_processed": len(self.learning_lessons),
            "regime_transitions": len(self.regime_timeline),
            "regime_timeline": self.regime_timeline,
            "veto_stats": self._compute_veto_stats(),
            "replay_filter": {
                "enabled": self._replay_filter_on,
                "entry_events": self.replay_entry_events,
                "starved_events": self.replay_starved_events,
                "cooldown_skips": self.replay_cooldown_skips,
                "per_symbol_calls": dict(self._replay_symbol_calls),
                "per_symbol_cap": self._replay_symbol_cap(),
                "solo_conf_min": self._replay_solo_conf,
                "solo_whitelist": sorted(self._replay_solo_whitelist),
                "cooldown_h": round(self._replay_cooldown_s / 3600, 1),
            },
        }

    def _compute_veto_stats(self) -> Dict[str, Any]:
        """Compute veto/approval stats from logged decisions."""
        total = len(self.decisions)
        if total == 0:
            return {"total_decisions": 0, "approved": 0, "vetoed": 0,
                    "veto_rate": 0.0}
        vetoed = sum(1 for d in self.decisions if d["action"] == "flat")
        approved = total - vetoed

        # Identify critic-driven vetoes vs other
        critic_vetoes = 0
        for d in self.decisions:
            if d["action"] != "flat":
                continue
            agents = d.get("agents", {})
            critic = agents.get("critic", {})
            if critic.get("ok") and critic.get("data", {}).get("verdict") == "challenge":
                critic_vetoes += 1

        return {
            "total_decisions": total,
            "approved": approved,
            "vetoed": vetoed,
            "critic_vetoes": critic_vetoes,
            "veto_rate": round(vetoed / max(total, 1), 3),
        }

    def flush_decisions(self):
        """Write all buffered decisions and exit decisions to JSONL log files."""
        if self.decisions:
            try:
                os.makedirs(os.path.dirname(self.decisions_log_path), exist_ok=True)
                with open(self.decisions_log_path, "a") as f:
                    for dec in self.decisions:
                        f.write(json.dumps(dec, default=str) + "\n")
                logger.info(
                    f"[BACKTEST-LLM] Flushed {len(self.decisions)} decisions to "
                    f"{self.decisions_log_path}"
                )
            except Exception as e:
                logger.warning(f"[BACKTEST-LLM] Failed to flush decisions: {e}")

        if self.exit_decisions:
            exit_log_path = os.path.join("data", "llm", "backtest_exits.jsonl")
            try:
                os.makedirs(os.path.dirname(exit_log_path), exist_ok=True)
                with open(exit_log_path, "a") as f:
                    for dec in self.exit_decisions:
                        f.write(json.dumps(dec, default=str) + "\n")
                logger.info(
                    f"[BACKTEST-LLM] Flushed {len(self.exit_decisions)} exit decisions"
                )
            except Exception as e:
                logger.warning(f"[BACKTEST-LLM] Failed to flush exit decisions: {e}")

    # ── Private Helpers ───────────────────────────────────────────

    def _log_decision(
        self,
        decision,
        snapshot_data: dict,
        cost: float,
        trigger: str,
    ):
        """Buffer a decision with full per-agent breakdown for learning."""
        # Extract symbol from snapshot data
        symbol = ""
        try:
            markets = snapshot_data.get("m", []) if snapshot_data else []
            if markets:
                symbol = markets[0].get("s", "")
        except (AttributeError, IndexError):
            pass

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "action": decision.action,
            "confidence": decision.confidence,
            "regime": decision.regime,
            "size_multiplier": decision.size_multiplier,
            "notes": decision.notes[:500] if decision.notes else "",
            "cost_usd": round(cost, 6),
            "trigger": trigger,
            "source": "backtest",
        }

        # Capture per-agent breakdown (regime, trade thesis, risk, critic)
        if self._coordinator:
            agent_detail = self._coordinator.get_last_pipeline_detail()
            if agent_detail:
                entry["agents"] = agent_detail
                # Track per-agent costs with actual model pricing
                for agent_name, detail in agent_detail.items():
                    if not isinstance(detail, dict):
                        continue
                    if detail.get("ok"):
                        model = detail.get("model", "")
                        pricing = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
                        agent_cost = (
                            detail.get("input_tokens", 0) * pricing[0] / 1_000_000
                            + detail.get("output_tokens", 0) * pricing[1] / 1_000_000
                        )
                        self.agent_costs[agent_name] = (
                            self.agent_costs.get(agent_name, 0) + agent_cost
                        )

        # Track regime timeline (only on transitions)
        regime = decision.regime
        if regime and (
            not self.regime_timeline
            or self.regime_timeline[-1]["regime"] != regime
        ):
            self.regime_timeline.append({
                "timestamp": entry["timestamp"],
                "regime": regime,
                "confidence": decision.confidence,
            })

        self.decisions.append(entry)

    def _log_exit_decision(
        self,
        result: Dict[str, Any],
        position_data: Dict[str, Any],
        cost: float,
    ):
        """Buffer an exit agent decision for the audit trail."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "exit",
            "symbol": position_data.get("symbol", ""),
            "action": result.get("action", "hold"),
            "urgency": result.get("urgency", "low"),
            "thesis_still_valid": result.get("thesis_still_valid"),
            "reason": result.get("reason", "")[:300],
            "cost_usd": round(cost, 6),
            "source": "backtest",
        }
        # Store exit agent detail
        if self._coordinator and self._coordinator.last_exit_output:
            out = self._coordinator.last_exit_output
            entry["agent_detail"] = {
                "data": out.data,
                "model": out.model_used,
                "input_tokens": out.input_tokens,
                "output_tokens": out.output_tokens,
            }
        self.exit_decisions.append(entry)

    def _log_skipped_decision(
        self,
        snapshot_data: Optional[dict],
        cost: float,
        trigger: str,
        reason: str,
    ):
        """Log a decision that was skipped (coordinator returned None)."""
        symbol = ""
        try:
            markets = snapshot_data.get("m", []) if snapshot_data else []
            if markets:
                symbol = markets[0].get("s", "")
        except (AttributeError, IndexError):
            pass

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "action": "skipped",
            "confidence": 0,
            "regime": "",
            "cost_usd": round(cost, 6),
            "trigger": trigger,
            "source": "backtest",
            "skip_reason": reason,
        }
        # Capture partial pipeline results even on failure
        if self._coordinator:
            agent_detail = self._coordinator.get_last_pipeline_detail()
            if agent_detail:
                entry["agents"] = agent_detail
        self.decisions.append(entry)

    def _compute_cost_from_stats(self, stats: Dict[str, Any]) -> float:
        """Compute cost from coordinator stats using actual per-agent model pricing."""
        # Use per-agent costs from pipeline results when available
        if self._coordinator and self._coordinator.last_pipeline_results:
            total = 0.0
            for role, output in self._coordinator.last_pipeline_results.items():
                if output.ok:
                    pricing = _MODEL_PRICING.get(output.model_used, _DEFAULT_PRICING)
                    total += output.input_tokens * pricing[0] / 1_000_000
                    total += output.output_tokens * pricing[1] / 1_000_000
            if total > 0:
                return total
        # Fallback to stats-based estimate
        in_tokens = stats.get("total_input_tokens", 0)
        out_tokens = stats.get("total_output_tokens", 0)
        in_cost = in_tokens * _DEFAULT_PRICING[0] / 1_000_000
        out_cost = out_tokens * _DEFAULT_PRICING[1] / 1_000_000
        return in_cost + out_cost

    def _compute_cost_from_usage(self, usage: Dict[str, Any]) -> float:
        """Compute cost from a single call_llm usage dict."""
        in_tokens = usage.get("input_tokens", 0)
        out_tokens = usage.get("output_tokens", 0)
        in_cost = in_tokens * _MODEL_PRICING["claude-haiku-4-5"][0] / 1_000_000
        out_cost = out_tokens * _MODEL_PRICING["claude-haiku-4-5"][1] / 1_000_000
        return in_cost + out_cost

    def _build_test_snapshot(self, symbol: str, price: float) -> dict:
        """Build a minimal test snapshot for preflight validation."""
        return {
            "m": [{
                "s": symbol,
                "p": _round_price(price),
                "d1h": 0.0,
                "d24h": 0.0,
            }],
            "g": {
                "btc": _round_price(price) if symbol == "BTC" else 0,
                "b1h": 0.0,
                "b24h": 0.0,
                "eb": 0.0,
                "pos": 0,
                "pnl": 0.0,
                "eq": 10000.0,
            },
        }


def _round_price(price: float) -> float:
    """Round price to appropriate precision."""
    if price >= 1000:
        return round(price, 1)
    elif price >= 1:
        return round(price, 2)
    elif price >= 0.01:
        return round(price, 4)
    else:
        return round(price, 6)
