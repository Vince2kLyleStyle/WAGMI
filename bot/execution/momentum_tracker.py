"""
Momentum Tracker: tracks win/loss streaks per symbol for sizing adjustments.

From 2,172-signal analysis:
- After 1 win: next signal has 67% WR (vs 50% baseline)
- After 2 wins: 75% WR
- After 1 loss: 34% WR
- After 2 losses: 29% WR

The spread (75% vs 29%) is the strongest single predictor in the system.
This module tracks streaks and provides sizing multipliers.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger("bot.execution.momentum_tracker")

# Sizing multipliers derived from 2,172-signal analysis
MOMENTUM_MULTIPLIERS = {
    2: 1.3,    # After 2+ consecutive wins: 75% WR -> size up 30%
    1: 1.15,   # After 1 win: 67% WR -> size up 15%
    0: 1.0,    # No streak: baseline
    -1: 0.6,   # After 1 loss: 34% WR -> reduce 40%
    -2: 0.35,  # After 2+ losses: 29% WR -> reduce 65%
}


class MomentumTracker:
    """Tracks win/loss momentum per symbol for data-driven sizing."""

    def __init__(self, state_path: str = "data/momentum_state.json"):
        self._state_path = state_path
        # streak > 0 = consecutive wins, < 0 = consecutive losses
        self._streaks: Dict[str, int] = {}
        self._last_outcome: Dict[str, bool] = {}
        # GLOBAL (book-level) outcome of the most recent close across ALL
        # symbols. RQ16_20_RISK_MATH Part A: WR after a loss = 20.0% (n=65)
        # vs after a win = 45.8% (n=24), runs-test clustering p=0.012
        # (survives era-split p=0.024). None = no close recorded yet.
        self._global_last_win: Optional[bool] = None
        self._load_state()

    def _load_state(self):
        try:
            # Don't load state if file doesn't exist or in test environments
            import sys
            if "pytest" in sys.modules:
                return
            if os.path.exists(self._state_path):
                with open(self._state_path) as f:
                    state = json.load(f)
                self._streaks = state.get("streaks", {})
                self._last_outcome = {k: v for k, v in state.get("last_outcome", {}).items()}
                self._global_last_win = state.get("global_last_win")
        except Exception as e:
            logger.debug(f"Momentum state load error: {e}")

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            with open(self._state_path, "w") as f:
                json.dump({
                    "streaks": self._streaks,
                    "last_outcome": self._last_outcome,
                    "global_last_win": self._global_last_win,
                    "updated": datetime.now(timezone.utc).isoformat(),
                }, f)
        except Exception as e:
            logger.debug(f"Momentum state save error: {e}")

    def record_outcome(self, symbol: str, won: bool):
        """Record a trade outcome for streak tracking."""
        sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
        current = self._streaks.get(sym, 0)

        if won:
            self._streaks[sym] = max(1, current + 1) if current >= 0 else 1
        else:
            self._streaks[sym] = min(-1, current - 1) if current <= 0 else -1

        self._last_outcome[sym] = won
        self._global_last_win = won  # book-level: last close across ALL symbols
        self._save_state()

        logger.info(
            f"[MOMENTUM] {sym}: {'WIN' if won else 'LOSS'} -> "
            f"streak={self._streaks[sym]:+d} "
            f"-> size_mult={self.get_multiplier(symbol):.2f}x"
        )

    def get_multiplier(self, symbol: str) -> float:
        """Get sizing multiplier based on current streak.

        Returns 0.35x to 1.3x based on momentum state.
        """
        sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
        streak = self._streaks.get(sym, 0)

        # Clamp to lookup range
        if streak >= 2:
            return MOMENTUM_MULTIPLIERS[2]
        elif streak == 1:
            return MOMENTUM_MULTIPLIERS[1]
        elif streak == 0:
            return MOMENTUM_MULTIPLIERS[0]
        elif streak == -1:
            return MOMENTUM_MULTIPLIERS[-1]
        else:  # -2 or worse
            return MOMENTUM_MULTIPLIERS[-2]

    def get_streak(self, symbol: str) -> int:
        """Get current streak for a symbol. Positive = wins, negative = losses."""
        sym = symbol.replace("/USDC:USDC", "").replace("/USDT:USDT", "")
        return self._streaks.get(sym, 0)

    def get_after_loss_multiplier(self) -> float:
        """Book-level after-loss de-sizing multiplier (RQ16_20 Part A).

        Evidence: next-trade WR after a realized LOSS is 20.0% (n=65) vs
        45.8% after a win (n=24); loss clustering runs-test p=0.012
        (p=0.024 inside Jun7+ alone). Window = 1 trade: the multiplier
        applies until the NEXT close updates the global last-outcome.
        Stacks multiplicatively with the per-symbol momentum ladder.

        Env: AFTER_LOSS_RISK_MULT (default 0.5). Set to 1.0 to disable.
        """
        if self._global_last_win is not False:  # None (no data) or True (won)
            return 1.0
        try:
            mult = float(os.getenv("AFTER_LOSS_RISK_MULT", "0.5"))
        except (TypeError, ValueError):
            mult = 0.5
        # De-sizing only: never allow this knob to size UP after a loss.
        return max(0.1, min(1.0, mult))

    def should_skip(self, symbol: str) -> bool:
        """Should we skip this symbol due to extreme losing streak?

        After 3+ consecutive losses: 29% WR is below breakeven for any R:R.
        Disabled by MOMENTUM_SKIP_ENABLED=false env var for testing.
        """
        if os.getenv("MOMENTUM_SKIP_ENABLED", "true").lower() not in ("1", "true", "yes"):
            return False
        return self.get_streak(symbol) <= -3

    def get_all_streaks(self) -> Dict[str, int]:
        """Get all symbol streaks for monitoring."""
        return dict(self._streaks)


# Module-level singleton
_tracker: Optional[MomentumTracker] = None


def get_momentum_tracker() -> MomentumTracker:
    global _tracker
    if _tracker is None:
        _tracker = MomentumTracker()
    return _tracker


def reset_momentum_tracker():
    """Reset singleton (for testing)."""
    global _tracker
    _tracker = None
