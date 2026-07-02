"""
Agent Performance Tracker — Measures whether each LLM agent adds alpha.

Records per-agent decisions on each pipeline run, then scores them against
actual trade outcomes when trades close. Provides aggregate reports to
determine if agents are worth their API cost.

Tracked agents:
  - Regime: Was the regime classification correct?
  - Trade: Was the directional thesis right?
  - Risk: Was sizing optimal (Kelly analysis)?
  - Critic: Did vetoes save money? Did approvals win?
  - Exit: Was exit timing good (money left on table vs saved)?

Storage: bot/data/llm/agent_performance.jsonl (append-only)
"""

import json
import logging
import math
import os
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from llm.agents.base import AgentOutput, AgentRole

logger = logging.getLogger("bot.llm.agents.performance_tracker")

# ── Data directory ──────────────────────────────────────────────────
_DEFAULT_DATA_DIR = os.path.join("data", "llm")
_PERF_FILE = "agent_performance.jsonl"
_DECISION_INDEX_FILE = "agent_decision_index.json"


# ── Data Classes ────────────────────────────────────────────────────

@dataclass
class AgentDecisionRecord:
    """Snapshot of one agent's decision at pipeline time."""
    record_id: str
    pipeline_id: str           # Groups all agents from same pipeline run
    timestamp: float
    agent_role: str            # AgentRole.value
    symbol: str
    side: str                  # BUY/SELL/FLAT from signal context
    decision: str              # The agent's output action/classification
    confidence: float          # Agent's confidence (0-1)
    reasoning_summary: str     # Brief summary of reasoning
    model_used: str
    latency_ms: int
    raw_data: Dict[str, Any]   # Full agent output dict

    # Outcome fields — filled when trade closes
    scored: bool = False
    outcome: Optional[Dict[str, Any]] = None
    # "agent" = confidence actually emitted by the model; "default" = 0.5
    # filler because the role's schema has no confidence field. Scorers must
    # exclude "default" rows (GM_AGENT_SKILL_24K: 7/9 roles were constant 0.5).
    confidence_source: str = "agent"


@dataclass
class RegimeScore:
    """Scoring for Regime Agent decisions."""
    predicted_regime: str
    actual_price_move_pct: float   # Actual % move over trade duration
    correct_regime: bool           # Was the classification reasonable?
    regime_confidence: float
    regime_bias: str               # bullish/bearish/neutral
    actual_direction: str          # up/down/flat

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TradeScore:
    """Scoring for Trade Agent decisions."""
    predicted_action: str          # proceed/flat/flip
    predicted_direction: str       # BUY/SELL
    actual_direction: str          # up/down
    correct_direction: bool
    thesis_accuracy: float         # 0-1 based on predicted vs actual move
    predicted_confidence: float
    actual_pnl_pct: float
    trade_won: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RiskScore:
    """Scoring for Risk Agent decisions."""
    recommended_size_mult: float
    actual_pnl_pct: float
    optimal_kelly_fraction: float  # Computed from actual outcome
    sizing_efficiency: float       # How close to optimal (0-1)
    max_drawdown_pct: float        # Actual max adverse excursion
    risk_flags_raised: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CriticScore:
    """Scoring for Critic Agent decisions."""
    verdict: str                   # approve/challenge/veto
    trade_proceeded: bool          # Did the trade actually execute?
    actual_pnl_pct: float          # What actually happened
    counterfactual_pnl_pct: float  # What would have happened without critic
    veto_saved_money: Optional[bool]  # For vetoes: did the vetoed trade lose?
    approval_made_money: Optional[bool]  # For approvals: did the trade win?
    veto_accuracy: float           # Running accuracy of veto decisions

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExitScore:
    """Scoring for Exit Agent decisions."""
    exit_recommendation: str       # hold/adjust/close
    actual_exit_type: str          # tp1/tp2/sl/trailing/manual
    pnl_at_recommendation: float   # PnL% when exit agent was consulted
    final_pnl: float               # Actual final PnL%
    money_left_on_table: float     # Positive = exited too early
    money_saved: float             # Positive = exited before worse drawdown
    timing_score: float            # -1 to 1: negative=too early, positive=too late

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Main Tracker Class ──────────────────────────────────────────────

class AgentPerformanceTracker:
    """Tracks and scores individual agent contributions to trading alpha.

    Usage:
        tracker = AgentPerformanceTracker()

        # On each pipeline run, record agent decisions
        tracker.record_pipeline_run(pipeline_id, symbol, side, agent_outputs)

        # When a trade closes, score the agents
        tracker.score_trade(pipeline_id, trade_outcome)

        # Get performance report
        report = tracker.get_agent_report()
    """

    def __init__(self, data_dir: str = _DEFAULT_DATA_DIR):
        self._data_dir = data_dir
        self._perf_path = os.path.join(data_dir, _PERF_FILE)
        self._index_path = os.path.join(data_dir, _DECISION_INDEX_FILE)
        self._lock = threading.Lock()

        # In-memory index: pipeline_id -> list of AgentDecisionRecord
        self._pipeline_index: Dict[str, List[AgentDecisionRecord]] = {}
        # All scored outcomes for reporting
        self._scored_records: List[Dict[str, Any]] = []

        os.makedirs(data_dir, exist_ok=True)
        self._load_existing()

    # ── Recording ───────────────────────────────────────────────────

    def record_pipeline_run(
        self,
        pipeline_id: str,
        symbol: str,
        side: str,
        agent_outputs: Dict[AgentRole, AgentOutput],
        signal_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record all agent decisions from a single pipeline run.

        Args:
            pipeline_id: Unique ID for this pipeline execution
            symbol: Trading symbol (e.g., "HYPE", "SOL")
            side: Signal direction ("BUY", "SELL", "FLAT")
            agent_outputs: Dict of role -> AgentOutput from coordinator
            signal_context: Optional extra context (entry price, etc.)
        """
        now = time.time()
        records = []

        for role, output in agent_outputs.items():
            if not output or not output.ok:
                continue

            decision = self._extract_decision(role, output)
            confidence, conf_source = self._extract_confidence(role, output)
            reasoning = self._extract_reasoning(role, output)

            record = AgentDecisionRecord(
                record_id=str(uuid.uuid4())[:12],
                pipeline_id=pipeline_id,
                timestamp=now,
                agent_role=role.value if isinstance(role, AgentRole) else str(role),
                symbol=symbol,
                side=side,
                decision=decision,
                confidence=confidence,
                # 200 -> 400: side words were truncated out of trade/critic
                # records, leaving 66% of "go" calls unscoreable for direction
                # (GM_AGENT_SKILL_24K side-coverage 33.7%, era-biased).
                reasoning_summary=reasoning[:400],
                model_used=output.model_used,
                latency_ms=output.latency_ms,
                raw_data=output.data,
                confidence_source=conf_source,
            )
            records.append(record)

        if not records:
            return

        with self._lock:
            self._pipeline_index[pipeline_id] = records
            self._append_records(records)

        logger.info(
            f"[PERF] Recorded {len(records)} agent decisions for "
            f"pipeline={pipeline_id} {symbol} {side}"
        )

    def record_veto(
        self,
        pipeline_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        critic_output: AgentOutput,
    ) -> None:
        """Record a Critic veto for counterfactual tracking.

        When the Critic vetoes a trade, we need to track what would have
        happened to measure veto accuracy.
        """
        now = time.time()
        veto_record = {
            "type": "veto",
            "pipeline_id": pipeline_id,
            "timestamp": now,
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "critic_verdict": critic_output.data.get("verdict", "unknown"),
            "counter_thesis": critic_output.data.get("counter_thesis", ""),
            "adjusted_confidence": critic_output.data.get("adjusted_confidence"),
        }

        with self._lock:
            self._append_raw(veto_record)

    # ── Scoring ─────────────────────────────────────────────────────

    def score_trade(
        self,
        pipeline_id: str,
        trade_outcome: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Score all agents involved in a completed trade.

        Args:
            pipeline_id: The pipeline run that produced this trade
            trade_outcome: Trade result dict with keys:
                - symbol: str
                - side: str (BUY/SELL)
                - entry_price: float
                - exit_price: float
                - pnl_pct: float (net PnL as percentage)
                - hold_hours: float
                - exit_type: str (tp1/tp2/sl/trailing/manual)
                - max_favorable_pct: float (best unrealized PnL)
                - max_adverse_pct: float (worst unrealized PnL)
                - price_at_exit_signal: float (optional, for exit agent)

        Returns:
            Dict with per-agent scores
        """
        with self._lock:
            records = self._pipeline_index.get(pipeline_id, [])

        if not records:
            logger.debug(f"[PERF] No agent records found for pipeline={pipeline_id}")
            return {}

        scores = {}
        pnl_pct = trade_outcome.get("pnl_pct", 0.0)
        side = trade_outcome.get("side", "BUY")
        entry = trade_outcome.get("entry_price", 0.0)
        exit_price = trade_outcome.get("exit_price", 0.0)
        won = pnl_pct > 0

        # Price movement
        if entry > 0:
            price_move_pct = ((exit_price - entry) / entry) * 100
        else:
            price_move_pct = 0.0

        actual_direction = "up" if price_move_pct > 0.1 else ("down" if price_move_pct < -0.1 else "flat")

        for record in records:
            role = record.agent_role

            if role == AgentRole.REGIME.value:
                scores["regime"] = self._score_regime(record, trade_outcome, actual_direction, price_move_pct)
            elif role == AgentRole.TRADE.value:
                scores["trade"] = self._score_trade_agent(record, trade_outcome, actual_direction, won, pnl_pct)
            elif role == AgentRole.RISK.value:
                scores["risk"] = self._score_risk_agent(record, trade_outcome, pnl_pct)
            elif role == AgentRole.CRITIC.value:
                scores["critic"] = self._score_critic(record, trade_outcome, pnl_pct, won)
            elif role == AgentRole.EXIT.value:
                scores["exit"] = self._score_exit_agent(record, trade_outcome, pnl_pct)

        # Persist scored outcome
        scored_entry = {
            "type": "scored_trade",
            "pipeline_id": pipeline_id,
            "timestamp": time.time(),
            "symbol": trade_outcome.get("symbol", ""),
            "side": side,
            "pnl_pct": pnl_pct,
            "won": won,
            "scores": {k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in scores.items()},
        }

        with self._lock:
            self._scored_records.append(scored_entry)
            self._append_raw(scored_entry)

            # Mark records as scored
            for record in records:
                record.scored = True
                record.outcome = scored_entry

        logger.info(
            f"[PERF] Scored pipeline={pipeline_id}: "
            f"pnl={pnl_pct:+.2f}% won={won} agents_scored={list(scores.keys())}"
        )

        return scores

    def score_veto_counterfactual(
        self,
        pipeline_id: str,
        counterfactual_outcome: Dict[str, Any],
    ) -> Optional[CriticScore]:
        """Score a Critic veto using counterfactual price data.

        Args:
            pipeline_id: The pipeline where the veto happened
            counterfactual_outcome: Dict with:
                - would_have_won: bool
                - counterfactual_pnl_pct: float
                - price_move_pct: float (actual price movement)
        """
        with self._lock:
            records = self._pipeline_index.get(pipeline_id, [])

        critic_records = [r for r in records if r.agent_role == AgentRole.CRITIC.value]
        if not critic_records:
            return None

        cf_pnl = counterfactual_outcome.get("counterfactual_pnl_pct", 0.0)
        would_have_won = counterfactual_outcome.get("would_have_won", cf_pnl > 0)

        # Veto saved money if the counterfactual trade would have lost
        veto_saved = not would_have_won

        score = CriticScore(
            verdict="veto",
            trade_proceeded=False,
            actual_pnl_pct=0.0,  # No trade happened
            counterfactual_pnl_pct=cf_pnl,
            veto_saved_money=veto_saved,
            approval_made_money=None,
            veto_accuracy=1.0 if veto_saved else 0.0,
        )

        scored_entry = {
            "type": "veto_counterfactual",
            "pipeline_id": pipeline_id,
            "timestamp": time.time(),
            "scores": {"critic": score.to_dict()},
        }

        with self._lock:
            self._scored_records.append(scored_entry)
            self._append_raw(scored_entry)

        return score

    # ── Reporting ───────────────────────────────────────────────────

    def get_agent_report(
        self,
        lookback_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate a comprehensive per-agent performance report.

        Args:
            lookback_days: Only include records from last N days (None = all)

        Returns:
            Dict with per-agent stats, alpha attribution, recommendations
        """
        with self._lock:
            records = list(self._scored_records)

        if lookback_days is not None:
            cutoff = time.time() - lookback_days * 86400
            records = [r for r in records if r.get("timestamp", 0) >= cutoff]

        if not records:
            return {
                "total_scored_trades": 0,
                "message": "No scored trades yet. Run the bot with LLM_MULTI_AGENT=true and wait for trades to close.",
                "agents": {},
            }

        # Separate trade scores from veto counterfactuals
        trade_scores = [r for r in records if r.get("type") == "scored_trade"]
        veto_scores = [r for r in records if r.get("type") == "veto_counterfactual"]

        report = {
            "total_scored_trades": len(trade_scores),
            "total_veto_counterfactuals": len(veto_scores),
            "period_days": lookback_days or "all",
            "agents": {},
        }

        # Per-agent aggregation
        report["agents"]["regime"] = self._aggregate_regime(trade_scores)
        report["agents"]["trade"] = self._aggregate_trade(trade_scores)
        report["agents"]["risk"] = self._aggregate_risk(trade_scores)
        report["agents"]["critic"] = self._aggregate_critic(trade_scores, veto_scores)
        report["agents"]["exit"] = self._aggregate_exit(trade_scores)

        # Alpha attribution
        report["alpha_attribution"] = self._compute_alpha_attribution(trade_scores, veto_scores)

        # Recommendations
        report["recommendations"] = self._generate_recommendations(report["agents"])

        return report

    def get_agent_summary_line(self) -> str:
        """One-line summary for embedding in dashboards."""
        report = self.get_agent_report()
        n = report["total_scored_trades"]
        if n == 0:
            return "Agent perf: no data yet"

        parts = []
        for agent_name, stats in report.get("agents", {}).items():
            if not stats or stats.get("count", 0) == 0:
                continue
            acc = stats.get("accuracy", stats.get("correct_pct", 0))
            parts.append(f"{agent_name}={acc:.0%}")

        return f"Agent perf ({n} trades): {', '.join(parts)}"

    # ── Scoring Helpers ─────────────────────────────────────────────

    def _score_regime(
        self,
        record: AgentDecisionRecord,
        outcome: Dict[str, Any],
        actual_direction: str,
        price_move_pct: float,
    ) -> RegimeScore:
        """Score the Regime Agent's classification."""
        data = record.raw_data
        predicted_regime = data.get("rg", data.get("regime", "unknown"))
        regime_conf = record.confidence
        regime_bias = data.get("bias", "neutral")

        # Check if regime classification was reasonable
        correct = self._regime_matches_outcome(predicted_regime, regime_bias, actual_direction, price_move_pct)

        return RegimeScore(
            predicted_regime=predicted_regime,
            actual_price_move_pct=price_move_pct,
            correct_regime=correct,
            regime_confidence=regime_conf,
            regime_bias=regime_bias,
            actual_direction=actual_direction,
        )

    def _score_trade_agent(
        self,
        record: AgentDecisionRecord,
        outcome: Dict[str, Any],
        actual_direction: str,
        won: bool,
        pnl_pct: float,
    ) -> TradeScore:
        """Score the Trade Agent's directional thesis."""
        data = record.raw_data
        action = data.get("a", data.get("action", "flat"))
        side = record.side

        # Determine if direction was correct
        predicted_dir = "up" if side == "BUY" else "down"
        correct_dir = predicted_dir == actual_direction

        # Thesis accuracy: scale 0-1 based on PnL magnitude
        # A correct direction with large PnL = high accuracy
        # A correct direction with small PnL = moderate accuracy
        if correct_dir and pnl_pct > 0:
            thesis_accuracy = min(1.0, 0.5 + abs(pnl_pct) / 5.0)
        elif correct_dir:
            thesis_accuracy = 0.4  # Right direction but lost (execution issue)
        else:
            thesis_accuracy = max(0.0, 0.3 - abs(pnl_pct) / 10.0)

        return TradeScore(
            predicted_action=action,
            predicted_direction=side,
            actual_direction=actual_direction,
            correct_direction=correct_dir,
            thesis_accuracy=thesis_accuracy,
            predicted_confidence=record.confidence,
            actual_pnl_pct=pnl_pct,
            trade_won=won,
        )

    def _score_risk_agent(
        self,
        record: AgentDecisionRecord,
        outcome: Dict[str, Any],
        pnl_pct: float,
    ) -> RiskScore:
        """Score the Risk Agent's sizing recommendation."""
        data = record.raw_data
        rec_size = float(data.get("sz", data.get("size_multiplier", 1.0)))
        risk_flags = data.get("risks", [])

        max_adverse = abs(outcome.get("max_adverse_pct", 0.0))

        # Compute optimal Kelly fraction from actual outcome
        # f* = WR - (1-WR)/payoff_ratio
        # For a single trade: if won, optimal is max size; if lost, 0
        # We use a smoothed version based on risk-reward
        if pnl_pct > 0:
            optimal_kelly = min(1.5, 0.5 + pnl_pct / 5.0)
        else:
            optimal_kelly = max(0.0, 0.5 + pnl_pct / 5.0)

        # Sizing efficiency: how close was recommended to optimal
        if optimal_kelly > 0:
            efficiency = 1.0 - min(1.0, abs(rec_size - optimal_kelly) / optimal_kelly)
        else:
            # Optimal was 0 (losing trade) — lower size = better
            efficiency = 1.0 - min(1.0, rec_size / 1.5)

        efficiency = max(0.0, min(1.0, efficiency))

        return RiskScore(
            recommended_size_mult=rec_size,
            actual_pnl_pct=pnl_pct,
            optimal_kelly_fraction=optimal_kelly,
            sizing_efficiency=efficiency,
            max_drawdown_pct=max_adverse,
            risk_flags_raised=risk_flags if isinstance(risk_flags, list) else [],
        )

    def _score_critic(
        self,
        record: AgentDecisionRecord,
        outcome: Dict[str, Any],
        pnl_pct: float,
        won: bool,
    ) -> CriticScore:
        """Score the Critic Agent's challenge/approve decision."""
        data = record.raw_data
        verdict = data.get("verdict", "approve").lower()

        # For approved trades that executed
        if verdict == "approve":
            return CriticScore(
                verdict="approve",
                trade_proceeded=True,
                actual_pnl_pct=pnl_pct,
                counterfactual_pnl_pct=0.0,  # Alternative was skip
                veto_saved_money=None,
                approval_made_money=won,
                veto_accuracy=1.0 if won else 0.0,  # Good approval = trade won
            )
        else:
            # Challenged but trade still proceeded (veto was overridden or downgraded)
            return CriticScore(
                verdict=verdict,
                trade_proceeded=True,
                actual_pnl_pct=pnl_pct,
                counterfactual_pnl_pct=0.0,
                veto_saved_money=None,
                approval_made_money=None,
                veto_accuracy=0.0 if won else 1.0,  # Bad challenge if trade won
            )

    def _score_exit_agent(
        self,
        record: AgentDecisionRecord,
        outcome: Dict[str, Any],
        pnl_pct: float,
    ) -> ExitScore:
        """Score the Exit Agent's timing recommendation."""
        data = record.raw_data
        recommendation = data.get("recommendation", data.get("action", "hold"))

        max_favorable = outcome.get("max_favorable_pct", pnl_pct)
        pnl_at_rec = outcome.get("pnl_at_exit_signal", pnl_pct)

        # Money left on table: if max favorable > final PnL, we exited too early
        money_left = max(0.0, max_favorable - pnl_pct)

        # Money saved: if we would have lost more without the exit signal
        money_saved = max(0.0, pnl_at_rec - pnl_pct) if pnl_pct < pnl_at_rec else 0.0

        # Timing score: -1 (exited way too early) to +1 (exited way too late)
        if max_favorable > 0:
            # How much of the max favorable move did we capture?
            capture_ratio = pnl_pct / max_favorable if max_favorable != 0 else 0
            timing = (capture_ratio - 0.5) * 2  # Center around 50% capture
            timing = max(-1.0, min(1.0, timing))
        else:
            timing = 0.0

        return ExitScore(
            exit_recommendation=recommendation,
            actual_exit_type=outcome.get("exit_type", "unknown"),
            pnl_at_recommendation=pnl_at_rec,
            final_pnl=pnl_pct,
            money_left_on_table=money_left,
            money_saved=money_saved,
            timing_score=timing,
        )

    # ── Regime Matching Logic ───────────────────────────────────────

    @staticmethod
    def _regime_matches_outcome(
        regime: str,
        bias: str,
        actual_direction: str,
        move_pct: float,
    ) -> bool:
        """Check if the regime classification was reasonable given the outcome."""
        abs_move = abs(move_pct)

        # Trend regime: should have big directional move
        if regime == "trend":
            if abs_move > 0.5:
                # If bias was given, check direction alignment
                if bias == "bullish" and actual_direction == "up":
                    return True
                if bias == "bearish" and actual_direction == "down":
                    return True
                if bias == "neutral":
                    return abs_move > 1.0  # Trend with no bias needs strong move
                return False
            return False  # Trend regime but small move = wrong

        # Range: should be small move
        if regime == "range":
            return abs_move < 2.0  # Range is correct if move stays contained

        # Panic: big move down
        if regime == "panic":
            return actual_direction == "down" and abs_move > 2.0

        # High volatility: big move either way
        if regime in ("high_volatility", "high_vol"):
            return abs_move > 1.5

        # Low liquidity: small moves
        if regime in ("low_liquidity", "low_liq"):
            return abs_move < 1.0

        # Unknown: always "wrong" — agent should classify
        if regime == "unknown":
            return False

        return True  # Default: give benefit of doubt

    # ── Aggregation Helpers ─────────────────────────────────────────

    def _aggregate_regime(self, trade_scores: List[Dict]) -> Dict[str, Any]:
        """Aggregate regime agent performance."""
        entries = []
        for ts in trade_scores:
            s = ts.get("scores", {}).get("regime")
            if s:
                entries.append(s)
        if not entries:
            return {"count": 0}

        correct = sum(1 for e in entries if e.get("correct_regime", False))
        avg_conf = _mean([e.get("regime_confidence", 0.5) for e in entries])

        # Accuracy by regime type
        by_regime = defaultdict(lambda: {"total": 0, "correct": 0})
        for e in entries:
            rg = e.get("predicted_regime", "unknown")
            by_regime[rg]["total"] += 1
            if e.get("correct_regime"):
                by_regime[rg]["correct"] += 1

        regime_breakdown = {
            rg: {"accuracy": v["correct"] / v["total"] if v["total"] else 0, "count": v["total"]}
            for rg, v in by_regime.items()
        }

        return {
            "count": len(entries),
            "accuracy": correct / len(entries),
            "correct": correct,
            "incorrect": len(entries) - correct,
            "avg_confidence": round(avg_conf, 3),
            "by_regime": regime_breakdown,
            "adding_alpha": correct / len(entries) > 0.55,  # >55% = adding value
        }

    def _aggregate_trade(self, trade_scores: List[Dict]) -> Dict[str, Any]:
        """Aggregate trade agent performance."""
        entries = []
        for ts in trade_scores:
            s = ts.get("scores", {}).get("trade")
            if s:
                entries.append(s)
        if not entries:
            return {"count": 0}

        correct_dir = sum(1 for e in entries if e.get("correct_direction", False))
        wins = sum(1 for e in entries if e.get("trade_won", False))
        avg_thesis = _mean([e.get("thesis_accuracy", 0.5) for e in entries])
        avg_conf = _mean([e.get("predicted_confidence", 0.5) for e in entries])

        # Calibration: does high confidence correlate with wins?
        high_conf = [e for e in entries if e.get("predicted_confidence", 0) > 0.7]
        low_conf = [e for e in entries if e.get("predicted_confidence", 0) <= 0.7]
        high_conf_wr = (
            sum(1 for e in high_conf if e.get("trade_won")) / len(high_conf)
            if high_conf else 0
        )
        low_conf_wr = (
            sum(1 for e in low_conf if e.get("trade_won")) / len(low_conf)
            if low_conf else 0
        )

        return {
            "count": len(entries),
            "direction_accuracy": correct_dir / len(entries),
            "win_rate": wins / len(entries),
            "avg_thesis_accuracy": round(avg_thesis, 3),
            "avg_confidence": round(avg_conf, 3),
            "calibration": {
                "high_conf_wr": round(high_conf_wr, 3),
                "low_conf_wr": round(low_conf_wr, 3),
                "well_calibrated": high_conf_wr > low_conf_wr,
            },
            "adding_alpha": correct_dir / len(entries) > 0.52,
        }

    def _aggregate_risk(self, trade_scores: List[Dict]) -> Dict[str, Any]:
        """Aggregate risk agent performance."""
        entries = []
        for ts in trade_scores:
            s = ts.get("scores", {}).get("risk")
            if s:
                entries.append(s)
        if not entries:
            return {"count": 0}

        avg_efficiency = _mean([e.get("sizing_efficiency", 0.5) for e in entries])
        avg_size = _mean([e.get("recommended_size_mult", 1.0) for e in entries])
        avg_kelly = _mean([e.get("optimal_kelly_fraction", 0.5) for e in entries])

        # Did size recommendations correlate with outcomes?
        # (larger size on winners, smaller on losers = good)
        winners = [e for e in entries if e.get("actual_pnl_pct", 0) > 0]
        losers = [e for e in entries if e.get("actual_pnl_pct", 0) <= 0]
        winner_avg_size = _mean([e.get("recommended_size_mult", 1) for e in winners]) if winners else 0
        loser_avg_size = _mean([e.get("recommended_size_mult", 1) for e in losers]) if losers else 0
        size_discriminates = winner_avg_size > loser_avg_size

        return {
            "count": len(entries),
            "avg_sizing_efficiency": round(avg_efficiency, 3),
            "avg_recommended_size": round(avg_size, 3),
            "avg_optimal_kelly": round(avg_kelly, 3),
            "size_discriminates_winners": size_discriminates,
            "winner_avg_size": round(winner_avg_size, 3),
            "loser_avg_size": round(loser_avg_size, 3),
            "adding_alpha": avg_efficiency > 0.45 and size_discriminates,
        }

    def _aggregate_critic(
        self,
        trade_scores: List[Dict],
        veto_scores: List[Dict],
    ) -> Dict[str, Any]:
        """Aggregate critic agent performance."""
        # Executed trade verdicts
        entries = []
        for ts in trade_scores:
            s = ts.get("scores", {}).get("critic")
            if s:
                entries.append(s)

        # Veto counterfactuals
        veto_entries = []
        for vs in veto_scores:
            s = vs.get("scores", {}).get("critic")
            if s:
                veto_entries.append(s)

        total_count = len(entries) + len(veto_entries)
        if total_count == 0:
            return {"count": 0}

        # Approval accuracy
        approvals = [e for e in entries if e.get("verdict") == "approve"]
        approval_wins = sum(1 for a in approvals if a.get("approval_made_money", False))
        approval_accuracy = approval_wins / len(approvals) if approvals else 0

        # Veto accuracy (from counterfactuals)
        vetoes = veto_entries
        veto_saves = sum(1 for v in vetoes if v.get("veto_saved_money", False))
        veto_accuracy = veto_saves / len(vetoes) if vetoes else 0

        # Challenged-but-proceeded accuracy
        challenges = [e for e in entries if e.get("verdict") != "approve"]
        challenge_right = sum(1 for c in challenges if not c.get("approval_made_money", True))
        challenge_accuracy = challenge_right / len(challenges) if challenges else 0

        # Money saved by vetoes
        money_saved_by_vetoes = sum(
            abs(v.get("counterfactual_pnl_pct", 0))
            for v in vetoes
            if v.get("veto_saved_money", False)
        )

        # Money missed by bad vetoes
        money_missed = sum(
            v.get("counterfactual_pnl_pct", 0)
            for v in vetoes
            if not v.get("veto_saved_money", False)
        )

        return {
            "count": total_count,
            "approval_count": len(approvals),
            "approval_accuracy": round(approval_accuracy, 3),
            "veto_count": len(vetoes),
            "veto_accuracy": round(veto_accuracy, 3),
            "challenge_count": len(challenges),
            "challenge_accuracy": round(challenge_accuracy, 3),
            "money_saved_by_vetoes_pct": round(money_saved_by_vetoes, 3),
            "money_missed_by_bad_vetoes_pct": round(money_missed, 3),
            "net_veto_value_pct": round(money_saved_by_vetoes - money_missed, 3),
            "adding_alpha": (
                (approval_accuracy > 0.5 if approvals else True)
                and (veto_accuracy > 0.5 if vetoes else True)
            ),
        }

    def _aggregate_exit(self, trade_scores: List[Dict]) -> Dict[str, Any]:
        """Aggregate exit agent performance."""
        entries = []
        for ts in trade_scores:
            s = ts.get("scores", {}).get("exit")
            if s:
                entries.append(s)
        if not entries:
            return {"count": 0}

        avg_timing = _mean([e.get("timing_score", 0) for e in entries])
        avg_left = _mean([e.get("money_left_on_table", 0) for e in entries])
        avg_saved = _mean([e.get("money_saved", 0) for e in entries])

        return {
            "count": len(entries),
            "avg_timing_score": round(avg_timing, 3),
            "avg_money_left_on_table_pct": round(avg_left, 3),
            "avg_money_saved_pct": round(avg_saved, 3),
            "net_exit_value_pct": round(avg_saved - avg_left, 3),
            "adding_alpha": avg_saved > avg_left,
        }

    # ── Alpha Attribution ───────────────────────────────────────────

    def _compute_alpha_attribution(
        self,
        trade_scores: List[Dict],
        veto_scores: List[Dict],
    ) -> Dict[str, Any]:
        """Compute how much alpha each agent contributes."""
        if not trade_scores:
            return {"message": "No data"}

        total_pnl = sum(t.get("pnl_pct", 0) for t in trade_scores)
        n = len(trade_scores)

        # Regime: trades where regime was correct vs incorrect
        regime_correct_pnl = []
        regime_wrong_pnl = []
        for t in trade_scores:
            rs = t.get("scores", {}).get("regime")
            if rs:
                if rs.get("correct_regime"):
                    regime_correct_pnl.append(t.get("pnl_pct", 0))
                else:
                    regime_wrong_pnl.append(t.get("pnl_pct", 0))

        # Trade: correct direction vs wrong
        trade_correct_pnl = []
        trade_wrong_pnl = []
        for t in trade_scores:
            ts_data = t.get("scores", {}).get("trade")
            if ts_data:
                if ts_data.get("correct_direction"):
                    trade_correct_pnl.append(t.get("pnl_pct", 0))
                else:
                    trade_wrong_pnl.append(t.get("pnl_pct", 0))

        # Critic: money saved by vetoes
        veto_value = sum(
            abs(v.get("scores", {}).get("critic", {}).get("counterfactual_pnl_pct", 0))
            for v in veto_scores
            if v.get("scores", {}).get("critic", {}).get("veto_saved_money")
        )

        return {
            "total_pnl_pct": round(total_pnl, 3),
            "avg_pnl_per_trade": round(total_pnl / n, 3) if n else 0,
            "regime_alpha": {
                "correct_avg_pnl": round(_mean(regime_correct_pnl), 3) if regime_correct_pnl else 0,
                "wrong_avg_pnl": round(_mean(regime_wrong_pnl), 3) if regime_wrong_pnl else 0,
                "regime_edge": round(
                    (_mean(regime_correct_pnl) if regime_correct_pnl else 0)
                    - (_mean(regime_wrong_pnl) if regime_wrong_pnl else 0),
                    3,
                ),
            },
            "trade_alpha": {
                "correct_dir_avg_pnl": round(_mean(trade_correct_pnl), 3) if trade_correct_pnl else 0,
                "wrong_dir_avg_pnl": round(_mean(trade_wrong_pnl), 3) if trade_wrong_pnl else 0,
                "direction_edge": round(
                    (_mean(trade_correct_pnl) if trade_correct_pnl else 0)
                    - (_mean(trade_wrong_pnl) if trade_wrong_pnl else 0),
                    3,
                ),
            },
            "critic_alpha": {
                "total_money_saved_by_vetoes_pct": round(veto_value, 3),
            },
        }

    # ── Recommendations ─────────────────────────────────────────────

    @staticmethod
    def _generate_recommendations(agents: Dict[str, Any]) -> List[str]:
        """Generate actionable recommendations based on agent performance."""
        recs = []

        # Regime
        regime = agents.get("regime", {})
        if regime.get("count", 0) >= 5:
            acc = regime.get("accuracy", 0)
            if acc < 0.5:
                recs.append(
                    f"REGIME AGENT: Accuracy {acc:.0%} is below random. "
                    f"Consider rewriting the regime prompt or switching to technical fallback."
                )
            elif acc > 0.75:
                recs.append(
                    f"REGIME AGENT: Strong {acc:.0%} accuracy. Consider increasing regime confidence weight."
                )

        # Trade
        trade = agents.get("trade", {})
        if trade.get("count", 0) >= 5:
            dir_acc = trade.get("direction_accuracy", 0)
            cal = trade.get("calibration", {})
            if dir_acc < 0.5:
                recs.append(
                    f"TRADE AGENT: Direction accuracy {dir_acc:.0%} is coin-flip level. "
                    f"Thesis generation is not adding value — review prompt or reduce confidence."
                )
            if cal and not cal.get("well_calibrated"):
                recs.append(
                    "TRADE AGENT: Poorly calibrated — high-confidence trades don't win "
                    "more than low-confidence ones. Needs confidence recalibration."
                )

        # Risk
        risk = agents.get("risk", {})
        if risk.get("count", 0) >= 5:
            if not risk.get("size_discriminates_winners"):
                recs.append(
                    "RISK AGENT: Sizing doesn't discriminate winners from losers. "
                    "Agent is not adding alpha via position sizing."
                )

        # Critic
        critic = agents.get("critic", {})
        if critic.get("count", 0) >= 5:
            net_veto = critic.get("net_veto_value_pct", 0)
            if net_veto < 0:
                recs.append(
                    f"CRITIC AGENT: Net veto value is NEGATIVE ({net_veto:+.2f}%). "
                    f"Vetoes are COSTING money. Consider lowering critic's veto power."
                )
            vacc = critic.get("veto_accuracy", 0)
            if vacc < 0.5 and critic.get("veto_count", 0) >= 3:
                recs.append(
                    f"CRITIC AGENT: Veto accuracy {vacc:.0%} — more wrong than right. "
                    f"Vetoed trades would have been profitable. Disable veto or retune."
                )

        # Exit
        exit_stats = agents.get("exit", {})
        if exit_stats.get("count", 0) >= 5:
            net_exit = exit_stats.get("net_exit_value_pct", 0)
            if net_exit < 0:
                recs.append(
                    f"EXIT AGENT: Net exit value is negative ({net_exit:+.2f}%). "
                    f"Leaving more money on the table than saving. Review exit logic."
                )

        if not recs:
            recs.append("Insufficient data for recommendations. Need 5+ scored trades per agent.")

        return recs

    # ── Extraction Helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_decision(role: AgentRole, output: AgentOutput) -> str:
        """Extract the key decision from an agent output."""
        d = output.data
        role_val = role.value if isinstance(role, AgentRole) else str(role)

        if role_val == "regime":
            return d.get("rg", d.get("regime", "unknown"))
        elif role_val == "trade":
            return d.get("a", d.get("action", "flat"))
        elif role_val == "risk":
            override = d.get("override", "none")
            sz = d.get("sz", d.get("size_multiplier", 1.0))
            return f"size={sz},override={override}"
        elif role_val == "critic":
            return d.get("verdict", "approve")
        elif role_val == "exit":
            return d.get("recommendation", d.get("action", "hold"))
        elif role_val == "scout":
            return d.get("action", "monitor")
        elif role_val == "quant":
            # Quant has no "action" — summarize EV direction + signal quality
            ev_dir = d.get("ev", {}).get("direction", "neutral") if isinstance(d.get("ev"), dict) else "neutral"
            sq = d.get("signal_quality", {})
            noise = sq.get("noise_probability", 0.5) if isinstance(sq, dict) else 0.5
            quality = "noise" if noise > 0.6 else ("clean" if noise < 0.3 else "marginal")
            return f"ev={ev_dir},quality={quality}"
        return str(d.get("action", d.get("a", "unknown")))

    @staticmethod
    def _extract_confidence(role: AgentRole, output: AgentOutput) -> tuple:
        """Extract confidence from agent output.

        GM_AGENT_SKILL_24K measurement fix (2026-07-02): 7/9 roles logged a
        constant 0.5 because only the generic c/conf/confidence keys were
        checked — the role-specific schemas keep confidence elsewhere.
        Returns (confidence, source) where source is "agent" (real emitted
        number) or "default" (0.5 filler — scorers must exclude these).
        """
        d = output.data
        role_val = role.value if isinstance(role, AgentRole) else str(role)

        candidates = [d.get("c"), d.get("conf"), d.get("confidence")]
        if role_val == "critic":
            # adjusted_confidence (nullable) or confidence_in_assessment (round1)
            candidates.extend([d.get("adjusted_confidence"), d.get("adj_c"),
                               d.get("confidence_in_assessment")])
        elif role_val == "quant":
            ev = d.get("ev")
            if isinstance(ev, dict):
                candidates.append(ev.get("confidence"))
        elif role_val == "scout":
            rf = d.get("regime_forecast")
            if isinstance(rf, dict):
                candidates.append(rf.get("confidence"))
        elif role_val == "exit":
            candidates.append(d.get("exit_confidence"))

        for conf in candidates:
            if conf is None:
                continue
            try:
                return float(conf), "agent"
            except (TypeError, ValueError):
                continue
        # No real confidence in the schema (e.g. risk/overseer/learning):
        # keep the 0.5 filler for shape-compat but MARK it so it can never
        # again masquerade as a calibration instrument.
        return 0.5, "default"

    @staticmethod
    def _extract_reasoning(role: AgentRole, output: AgentOutput) -> str:
        """Extract reasoning summary from agent output."""
        d = output.data
        # Try various common keys
        for key in ("n", "notes", "reason", "reasoning", "thesis", "factors", "rationale"):
            val = d.get(key)
            if val and isinstance(val, str):
                return val
        return str(d)[:200]

    # ── Persistence ─────────────────────────────────────────────────

    def _append_records(self, records: List[AgentDecisionRecord]) -> None:
        """Append decision records to the JSONL file."""
        try:
            with open(self._perf_path, "a") as f:
                for rec in records:
                    entry = {
                        "type": "decision",
                        "record_id": rec.record_id,
                        "pipeline_id": rec.pipeline_id,
                        "timestamp": rec.timestamp,
                        "agent_role": rec.agent_role,
                        "symbol": rec.symbol,
                        "side": rec.side,
                        "decision": rec.decision,
                        "confidence": rec.confidence,
                        "confidence_source": rec.confidence_source,
                        "reasoning_summary": rec.reasoning_summary,
                        "model_used": rec.model_used,
                        "latency_ms": rec.latency_ms,
                    }
                    f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"[PERF] Failed to write records: {e}")

    def _append_raw(self, entry: Dict[str, Any]) -> None:
        """Append a raw dict entry to the JSONL file."""
        try:
            with open(self._perf_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error(f"[PERF] Failed to write entry: {e}")

    def _load_existing(self) -> None:
        """Load existing performance data on startup."""
        if not os.path.exists(self._perf_path):
            return

        try:
            with open(self._perf_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entry_type = entry.get("type", "")

                        if entry_type == "decision":
                            pid = entry.get("pipeline_id", "")
                            if pid not in self._pipeline_index:
                                self._pipeline_index[pid] = []
                            self._pipeline_index[pid].append(
                                AgentDecisionRecord(
                                    record_id=entry.get("record_id", ""),
                                    pipeline_id=pid,
                                    timestamp=entry.get("timestamp", 0),
                                    agent_role=entry.get("agent_role", ""),
                                    symbol=entry.get("symbol", ""),
                                    side=entry.get("side", ""),
                                    decision=entry.get("decision", ""),
                                    confidence=entry.get("confidence", 0.5),
                                    reasoning_summary=entry.get("reasoning_summary", ""),
                                    model_used=entry.get("model_used", ""),
                                    latency_ms=entry.get("latency_ms", 0),
                                    raw_data=entry.get("raw_data", {}),
                                    confidence_source=entry.get("confidence_source", "agent"),
                                )
                            )
                        elif entry_type in ("scored_trade", "veto_counterfactual"):
                            self._scored_records.append(entry)

                    except json.JSONDecodeError:
                        continue

            n_pipelines = len(self._pipeline_index)
            n_scored = len(self._scored_records)
            if n_pipelines > 0 or n_scored > 0:
                logger.info(
                    f"[PERF] Loaded {n_pipelines} pipeline records, "
                    f"{n_scored} scored outcomes"
                )
        except Exception as e:
            logger.error(f"[PERF] Failed to load existing data: {e}")


# ── Module-level singleton ──────────────────────────────────────────

_tracker_instance: Optional[AgentPerformanceTracker] = None
_tracker_lock = threading.Lock()


def get_performance_tracker(data_dir: str = _DEFAULT_DATA_DIR) -> AgentPerformanceTracker:
    """Get or create the singleton performance tracker."""
    global _tracker_instance
    if _tracker_instance is None:
        with _tracker_lock:
            if _tracker_instance is None:
                _tracker_instance = AgentPerformanceTracker(data_dir)
    return _tracker_instance


# ── Utility ─────────────────────────────────────────────────────────

def _mean(values: List[float]) -> float:
    """Safe mean that returns 0 for empty list."""
    if not values:
        return 0.0
    return sum(values) / len(values)
