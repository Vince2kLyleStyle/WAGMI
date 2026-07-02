"""
Thesis Tracking with Outcome Attribution

Tracks every directional thesis the Trade Agent forms and measures:
1. Was the thesis correct? (Did price reach the predicted target?)
2. Was it correct within the predicted timeframe?
3. Which setup types / regimes / symbols produce the most accurate theses?
4. What's the agent's calibration curve? (claimed confidence vs actual accuracy)

This data feeds back into the Trade Agent's prompt as performance context,
making the LLM squad genuinely learn from its own predictions.

Storage: bot/data/llm/thesis_history.jsonl (append-only)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("bot.llm.thesis_tracker")


class ThesisRecord:
    """A single thesis prediction with outcome tracking."""

    def __init__(self, thesis_id: str, symbol: str, side: str,
                 thesis: str, confidence: float, regime: str,
                 entry_price: float, target_price: Optional[float] = None,
                 expected_hold_h: Optional[float] = None,
                 setup_type: Optional[str] = None,
                 agent_name: str = "trade_agent"):
        self.thesis_id = thesis_id
        self.symbol = symbol
        self.side = side
        self.thesis = thesis
        self.confidence = confidence
        self.regime = regime
        self.entry_price = entry_price
        self.target_price = target_price
        self.expected_hold_h = expected_hold_h
        self.setup_type = setup_type or "unknown"
        self.agent_name = agent_name
        self.created_at = datetime.now(timezone.utc).isoformat()
        # A/B stamp (RQ15): was the thesis-quality checklist active in the
        # prompt when this thesis was formed? Enables checklist-on vs -off
        # graded-accuracy scoring with auto-retire.
        self.checklist_active: Optional[bool] = None
        try:
            from llm.agents.prompts import THESIS_CHECKLIST_ENABLED
            self.checklist_active = bool(THESIS_CHECKLIST_ENABLED)
        except Exception:
            pass

        # Outcome fields (filled later)
        self.outcome: Optional[str] = None  # "correct", "incorrect", "partial", "pending"
        self.exit_price: Optional[float] = None
        self.actual_hold_h: Optional[float] = None
        self.max_favorable: Optional[float] = None  # Best price in direction of thesis
        self.max_adverse: Optional[float] = None     # Worst price against thesis
        self.pnl_pct: Optional[float] = None
        self.closed_at: Optional[str] = None
        self.outcome_notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thesis_id": self.thesis_id,
            "symbol": self.symbol,
            "side": self.side,
            "thesis": self.thesis,
            "confidence": self.confidence,
            "regime": self.regime,
            "entry_price": self.entry_price,
            "target_price": self.target_price,
            "expected_hold_h": self.expected_hold_h,
            "setup_type": self.setup_type,
            "agent_name": self.agent_name,
            "created_at": self.created_at,
            "checklist_active": self.checklist_active,
            "outcome": self.outcome,
            "exit_price": self.exit_price,
            "actual_hold_h": self.actual_hold_h,
            "max_favorable": self.max_favorable,
            "max_adverse": self.max_adverse,
            "pnl_pct": self.pnl_pct,
            "closed_at": self.closed_at,
            "outcome_notes": self.outcome_notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ThesisRecord":
        rec = cls(
            thesis_id=d["thesis_id"],
            symbol=d["symbol"],
            side=d["side"],
            thesis=d["thesis"],
            confidence=d["confidence"],
            regime=d.get("regime", "unknown"),
            entry_price=d["entry_price"],
            target_price=d.get("target_price"),
            expected_hold_h=d.get("expected_hold_h"),
            setup_type=d.get("setup_type", "unknown"),
            agent_name=d.get("agent_name", "trade_agent"),
        )
        rec.created_at = d.get("created_at", rec.created_at)
        rec.checklist_active = d.get("checklist_active")
        rec.outcome = d.get("outcome")
        rec.exit_price = d.get("exit_price")
        rec.actual_hold_h = d.get("actual_hold_h")
        rec.max_favorable = d.get("max_favorable")
        rec.max_adverse = d.get("max_adverse")
        rec.pnl_pct = d.get("pnl_pct")
        rec.closed_at = d.get("closed_at")
        rec.outcome_notes = d.get("outcome_notes")
        return rec


class ThesisTracker:
    """
    Tracks Trade Agent theses and measures prediction accuracy.

    Provides:
    - Per-regime accuracy stats
    - Per-symbol accuracy stats
    - Per-setup-type accuracy stats
    - Calibration data (confidence → actual win rate)
    - Summary statistics for injection into agent prompts
    """

    def __init__(self, data_dir: str = "data/llm"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.data_dir / "thesis_history.jsonl"
        self._pending: Dict[str, ThesisRecord] = {}  # thesis_id → record
        self._history: List[ThesisRecord] = []
        self._load()
        self._counter = 0

    def _load(self):
        """Load thesis history from JSONL file."""
        if not self.history_file.exists():
            return
        try:
            with open(self.history_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        rec = ThesisRecord.from_dict(d)
                        self._history.append(rec)
                        if rec.outcome is None or rec.outcome == "pending":
                            self._pending[rec.thesis_id] = rec
                    except (json.JSONDecodeError, KeyError):
                        continue
            logger.info(f"Loaded {len(self._history)} thesis records "
                        f"({len(self._pending)} pending)")
        except Exception as e:
            logger.warning(f"Failed to load thesis history: {e}")

    def _save_record(self, record: ThesisRecord):
        """Append a single record to the JSONL file."""
        try:
            with open(self.history_file, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
        except Exception as e:
            logger.error(f"Failed to save thesis record: {e}")

    def _generate_id(self) -> str:
        self._counter += 1
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"thesis_{ts}_{self._counter}"

    def record_thesis(self, symbol: str, side: str, thesis: str,
                       confidence: float, regime: str, entry_price: float,
                       target_price: Optional[float] = None,
                       expected_hold_h: Optional[float] = None,
                       setup_type: Optional[str] = None,
                       agent_name: str = "trade_agent") -> str:
        """Record a new thesis prediction. Returns thesis_id."""
        thesis_id = self._generate_id()
        record = ThesisRecord(
            thesis_id=thesis_id,
            symbol=symbol,
            side=side,
            thesis=thesis,
            confidence=confidence,
            regime=regime,
            entry_price=entry_price,
            target_price=target_price,
            expected_hold_h=expected_hold_h,
            setup_type=setup_type,
            agent_name=agent_name,
        )
        record.outcome = "pending"
        self._pending[thesis_id] = record
        self._history.append(record)
        self._save_record(record)
        logger.debug(f"Recorded thesis {thesis_id}: {side} {symbol} conf={confidence:.0f}%")
        return thesis_id

    def close_thesis(self, thesis_id: str, exit_price: float,
                      pnl_pct: float, max_favorable: Optional[float] = None,
                      max_adverse: Optional[float] = None,
                      actual_hold_h: Optional[float] = None,
                      notes: Optional[str] = None):
        """Close a thesis with its outcome."""
        record = self._pending.pop(thesis_id, None)
        if record is None:
            logger.warning(f"Thesis {thesis_id} not found in pending")
            return

        record.exit_price = exit_price
        record.pnl_pct = pnl_pct
        record.max_favorable = max_favorable
        record.max_adverse = max_adverse
        record.actual_hold_h = actual_hold_h
        record.closed_at = datetime.now(timezone.utc).isoformat()
        record.outcome_notes = notes

        # Determine outcome
        if pnl_pct > 0:
            if record.target_price is not None and max_favorable is not None:
                target_reached = (
                    (record.side == "BUY" and max_favorable >= record.target_price) or
                    (record.side == "SELL" and max_favorable <= record.target_price)
                )
                record.outcome = "correct" if target_reached else "partial"
            else:
                record.outcome = "correct"
        else:
            record.outcome = "incorrect"

        # Re-save (append updated version)
        self._save_record(record)
        logger.info(f"Closed thesis {thesis_id}: {record.outcome} pnl={pnl_pct:+.2f}%")

    def get_accuracy_stats(self, lookback_days: int = 30,
                            min_samples: int = 5) -> Dict[str, Any]:
        """Compute accuracy statistics across multiple dimensions."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        closed = [r for r in self._history
                   if r.outcome and r.outcome != "pending"
                   and r.closed_at and r.closed_at >= cutoff.isoformat()]

        if len(closed) < min_samples:
            return {"total_theses": len(closed), "sufficient_data": False}

        correct = [r for r in closed if r.outcome in ("correct", "partial")]
        overall_accuracy = len(correct) / len(closed) if closed else 0

        # By regime
        by_regime: Dict[str, Dict] = {}
        for r in closed:
            regime = r.regime or "unknown"
            if regime not in by_regime:
                by_regime[regime] = {"correct": 0, "total": 0, "avg_pnl": 0}
            by_regime[regime]["total"] += 1
            if r.outcome in ("correct", "partial"):
                by_regime[regime]["correct"] += 1
            by_regime[regime]["avg_pnl"] += (r.pnl_pct or 0)

        for k, v in by_regime.items():
            v["accuracy"] = v["correct"] / v["total"] if v["total"] > 0 else 0
            v["avg_pnl"] = v["avg_pnl"] / v["total"] if v["total"] > 0 else 0

        # By symbol
        by_symbol: Dict[str, Dict] = {}
        for r in closed:
            sym = r.symbol
            if sym not in by_symbol:
                by_symbol[sym] = {"correct": 0, "total": 0, "avg_pnl": 0}
            by_symbol[sym]["total"] += 1
            if r.outcome in ("correct", "partial"):
                by_symbol[sym]["correct"] += 1
            by_symbol[sym]["avg_pnl"] += (r.pnl_pct or 0)

        for k, v in by_symbol.items():
            v["accuracy"] = v["correct"] / v["total"] if v["total"] > 0 else 0
            v["avg_pnl"] = v["avg_pnl"] / v["total"] if v["total"] > 0 else 0

        # By setup type
        by_setup: Dict[str, Dict] = {}
        for r in closed:
            st = r.setup_type or "unknown"
            if st not in by_setup:
                by_setup[st] = {"correct": 0, "total": 0, "avg_pnl": 0}
            by_setup[st]["total"] += 1
            if r.outcome in ("correct", "partial"):
                by_setup[st]["correct"] += 1
            by_setup[st]["avg_pnl"] += (r.pnl_pct or 0)

        for k, v in by_setup.items():
            v["accuracy"] = v["correct"] / v["total"] if v["total"] > 0 else 0
            v["avg_pnl"] = v["avg_pnl"] / v["total"] if v["total"] > 0 else 0

        # Calibration curve: confidence buckets → actual accuracy
        calibration = self._compute_calibration(closed)

        return {
            "total_theses": len(closed),
            "sufficient_data": True,
            "overall_accuracy": overall_accuracy,
            "correct_count": len(correct),
            "by_regime": by_regime,
            "by_symbol": by_symbol,
            "by_setup_type": by_setup,
            "calibration": calibration,
        }

    def _compute_calibration(self, records: List[ThesisRecord]) -> Dict[str, Dict]:
        """Compute calibration: binned confidence → actual accuracy."""
        bins = {
            "50-60": {"correct": 0, "total": 0},
            "60-70": {"correct": 0, "total": 0},
            "70-80": {"correct": 0, "total": 0},
            "80-90": {"correct": 0, "total": 0},
            "90-100": {"correct": 0, "total": 0},
        }

        for r in records:
            conf = r.confidence
            if conf < 60:
                bucket = "50-60"
            elif conf < 70:
                bucket = "60-70"
            elif conf < 80:
                bucket = "70-80"
            elif conf < 90:
                bucket = "80-90"
            else:
                bucket = "90-100"

            bins[bucket]["total"] += 1
            if r.outcome in ("correct", "partial"):
                bins[bucket]["correct"] += 1

        for k, v in bins.items():
            v["accuracy"] = v["correct"] / v["total"] if v["total"] > 0 else None

        return bins

    def get_prompt_context(self, symbol: Optional[str] = None,
                            regime: Optional[str] = None,
                            lookback_days: int = 14) -> str:
        """Generate a context string for injection into Trade Agent prompts."""
        stats = self.get_accuracy_stats(lookback_days=lookback_days)
        if not stats.get("sufficient_data"):
            return ""

        lines = [f"THESIS ACCURACY ({stats['total_theses']} predictions, {lookback_days}d):"]
        lines.append(f"  Overall: {stats['overall_accuracy']*100:.0f}% correct")

        # Symbol-specific if available
        if symbol and symbol in stats.get("by_symbol", {}):
            s = stats["by_symbol"][symbol]
            lines.append(f"  {symbol}: {s['accuracy']*100:.0f}% correct ({s['total']} trades, avg PnL {s['avg_pnl']:+.2f}%)")

        # Regime-specific if available
        if regime and regime in stats.get("by_regime", {}):
            r = stats["by_regime"][regime]
            lines.append(f"  In {regime} regime: {r['accuracy']*100:.0f}% correct ({r['total']} trades)")

        # Calibration warning
        cal = stats.get("calibration", {})
        for bucket, data in cal.items():
            if data["total"] >= 5 and data["accuracy"] is not None:
                expected_mid = (int(bucket.split("-")[0]) + int(bucket.split("-")[1])) / 200
                if abs(data["accuracy"] - expected_mid) > 0.15:
                    if data["accuracy"] < expected_mid:
                        lines.append(f"  WARNING: {bucket}% confidence band → only {data['accuracy']*100:.0f}% actual (overconfident)")
                    else:
                        lines.append(f"  NOTE: {bucket}% confidence band → {data['accuracy']*100:.0f}% actual (underconfident)")

        return "\n".join(lines)

    def get_pending_theses(self) -> List[Dict[str, Any]]:
        """Get all open/pending theses for monitoring."""
        return [r.to_dict() for r in self._pending.values()]
