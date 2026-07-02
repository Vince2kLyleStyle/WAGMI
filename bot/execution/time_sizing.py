"""
Time-aware position sizing multipliers — NEUTRALIZED (shadow) by default.

D11 (FALLACY_AUDIT_2026-07-02, wave2b L5): every multiplier below derives from
ONE April-2026 week (5m composite study, N=336/hour) and was never re-scored
on the clean ledger. It also contradicted the Morning Edge boost (hours 6/7/10
simultaneously DEAD 0.5x and boosted 1.2x) — net size was an accident of
multiplication. Per THE_STANDARD §2b these hour/day/direction edges are now
SHADOW: the would-have multiplier is computed and logged ([SHADOW-TIME-SIZE]),
but 1.0x is applied. Kill-switch: TIME_SIZING_ENFORCE=true restores
enforcement (owner-gated; only after a clean-ledger hour study, one hour
policy).

The session/bias LABELS remain available as raw, honest context (get_*_info),
because describing the hour is data; sizing on a stale opinion of it is not.

Data-driven session sizing from 2000-candle / 7-day 5m analysis (updated 2026-04-03):
Cross-asset composite scoring (BTC/ETH/SOL/HYPE) using range * directional consistency.

- PRIME hours (UTC): 01, 13, 14, 15, 17, 22 — highest composite scores
- GOOD hours: 00, 02, 08, 12, 16
- QUIET hours: 03, 05, 09, 11, 19, 23
- DEAD hours: 04, 06, 07, 10, 18, 20, 21

Kill zones:
- PRIMARY: H14-H17 UTC (US session) — 4 of top 6 composite scores
- SECONDARY: H00-H02 UTC (Asia open) — elevated vol
- COUNTER-TREND: H08-H09 UTC (London open) — short-biased

Directional biases (from 336-sample cross-asset WR):
- 08-09 UTC: short bias (56-58% SHORT WR, London open selloff)
- 17 UTC: strong short bias (58-68% SHORT WR, US afternoon fade)
- 22 UTC: short bias (52-57% SHORT WR, confirmed by our winning trades)
- 14-15 UTC: long bias (54-60% LONG WR — was incorrectly marked SHORT)

Day-of-week:
- Monday best (1.15x), Thursday worst (0.85x), weekends reduced (0.8x)

These are multiplicative on top of normal sizing, NOT replacements.
All multipliers are env-configurable for tuning.

Configuration (in trading_config.py):
- ENABLE_TIME_SIZING: master switch (default: True)
- TIME_SIZING_ALLOW_BOOST: also boost sizing in PRIME hours (default: True)
- TIME_SIZING_MAX_BOOST: cap on combined boost multiplier (default: 1.4)
- TIME_SIZING_DIRECTIONAL_BOOST: extra boost when side matches hour bias (default: 1.15)
- TIME_SIZING_DIRECTIONAL_PENALTY: penalty when side opposes hour bias (default: 0.85)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Union

logger = logging.getLogger("bot.execution.time_sizing")

# Env-configurable multipliers
WEEKEND_SIZE_MULTIPLIER = float(os.getenv("WEEKEND_SIZE_MULTIPLIER", "0.8"))

# ── Hour-of-day session multipliers (UTC) ────────────────────────────
# From 500-candle / 20-day volatility & directional-move analysis.
# PRIME=1.2, GOOD=1.0, QUIET=0.7, DEAD=0.5

_SESSION_MULTIPLIERS = {
    # PRIME hours — top 6 composite scores (range * directional consistency)
    # Updated 2026-04-03 from 2000-candle 5m cross-asset analysis (BTC/ETH/SOL/HYPE)
    1:  1.2,   # Asia vol spike, highest range (0.31%), composite=0.164
    13: 1.2,   # US open anticipation, high volume, composite=0.147
    14: 1.2,   # US open, highest range (0.33%), LONG bias 54-60%, composite=0.191
    15: 1.2,   # US session, high range + 12% directional edge, composite=0.166
    17: 1.2,   # SHORT ONLY — highest composite (0.212), 61% bear cross-asset
    22: 1.2,   # Late session, confirmed by data + our winning trades, composite=0.145
    # GOOD hours — composite 0.12-0.14
    0:  1.0,   # Asia open, decent range, less directional than H01
    2:  1.0,   # Post-Asia vol, composite=0.133
    8:  1.0,   # London open, 12.5% SHORT edge, composite=0.123
    12: 1.0,   # Pre-US buildup, composite=0.134
    16: 1.0,   # US session continuation, composite=0.134
    # QUIET hours — composite 0.09-0.12
    3:  0.7,   # Asia mid-session, vol dropping
    5:  0.7,   # Pre-London, some vol
    9:  0.7,   # London morning, directional but low range
    11: 0.7,   # Downgraded from 1.3x — low range (0.19%), composite=0.107
    19: 0.7,   # US afternoon decline
    23: 0.7,   # Pre-midnight, modest pickup
    # DEAD hours — composite < 0.10
    4:  0.5,   # Lowest vol window globally
    6:  0.5,   # Pre-London dead zone
    7:  0.5,   # False London open, no real vol spike
    10: 0.5,   # London mid-morning, lowest range (0.17%)
    18: 0.5,   # US afternoon lull, zero directional edge
    20: 0.5,   # Downgraded from 1.3x — lowest composite (0.088), lost every trade
    21: 0.5,   # Pre-Asia quiet
}

# ── Day-of-week multipliers ──────────────────────────────────────────
# Monday=0, Tuesday=1, ... Sunday=6
_DAY_MULTIPLIERS = {
    0: 1.15,   # Monday — best day
    1: 1.0,    # Tuesday
    2: 1.0,    # Wednesday
    3: 0.85,   # Thursday — worst day
    4: 0.95,   # Friday
    5: 0.8,    # Saturday
    6: 0.8,    # Sunday
}

# ── Directional bias by hour ─────────────────────────────────────────
# Based on 7-day 5m cross-asset WR analysis (2026-04-03, N=336 per hour).
# Only hours with >= 55% directional WR across multiple assets.
_HOUR_BIAS = {
    8:  "short",   # 52-58% SHORT WR (BTC 58%, SOL 57%, HYPE 55%)
    9:  "short",   # 52-58% SHORT WR (BTC 56%, SOL 58%, HYPE 56%)
    14: "long",    # 53-57% LONG WR — was incorrectly SHORT! (ETH 54%, SOL 56%, BTC 57%)
    15: "long",    # 53-60% LONG WR (ETH 60%, SOL 54%, HYPE 57%)
    17: "short",   # 58-68% SHORT WR — strongest edge (HYPE 68%, SOL 61%, BTC/ETH 58%)
    22: "short",   # 52-57% SHORT WR (SOL 57%, ETH 56%, BTC 52%)
}

# Low-liquidity hours (QUIET + DEAD) for external checks
_LOW_LIQ_HOURS = {3, 4, 5, 6, 7, 9, 10, 11, 18, 19, 20, 21, 23}


def _enforce() -> bool:
    """D11 kill-switch: stale hour/day edges apply only when explicitly re-armed."""
    return os.getenv("TIME_SIZING_ENFORCE", "false").lower() in ("1", "true", "yes")


def _raw_time_multiplier(now: datetime = None):
    """Compute the WOULD-HAVE session*day multiplier (shadow arithmetic).

    Returns (multiplier, reasons). This is the pre-D11 math, kept so the
    shadow log and any future clean-ledger re-score have the exact number.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    reasons = []

    # Day-of-week multiplier
    day_mult = _DAY_MULTIPLIERS.get(now.weekday(), 1.0)
    if day_mult != 1.0:
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        reasons.append(f"{day_names[now.weekday()]}({day_mult:.2f}x)")

    # Session-aware hour multiplier
    session_mult = _SESSION_MULTIPLIERS.get(now.hour, 1.0)
    if session_mult != 1.0:
        reasons.append(f"session_h{now.hour}({session_mult:.2f}x)")

    return round(day_mult * session_mult, 4), reasons


def get_time_multiplier(now: datetime = None) -> float:
    """Return the combined time-based sizing multiplier.

    D11 SHADOW (default): returns 1.0 and logs the would-have multiplier.
    TIME_SIZING_ENFORCE=true restores the session*day enforcement.

    Args:
        now: Override for testing. Defaults to current UTC time.

    Returns:
        Combined multiplier (1.0 unless enforced).
    """
    multiplier, reasons = _raw_time_multiplier(now)

    if _enforce():
        if reasons:
            logger.info(
                f"[TIME-SIZE] Applying time multiplier: {multiplier:.3f}x "
                f"({', '.join(reasons)})"
            )
        return multiplier

    if multiplier != 1.0:
        logger.info(
            f"[SHADOW-TIME-SIZE] would apply {multiplier:.3f}x "
            f"({', '.join(reasons)}) — neutralized to 1.0x "
            f"(FALLACY_AUDIT D11: one-April-week era, never re-scored; "
            f"TIME_SIZING_ENFORCE=true restores)"
        )
    return 1.0


def _raw_directional_multiplier(side: str, now: datetime = None,
                                boost: float = 1.15, penalty: float = 0.85):
    """Compute the WOULD-HAVE directional multiplier (shadow arithmetic)."""
    if now is None:
        now = datetime.now(timezone.utc)

    bias = _HOUR_BIAS.get(now.hour, "neutral")
    if bias == "neutral":
        return 1.0, bias, "neutral"

    trade_dir = "long" if side.upper() == "BUY" else "short"
    if trade_dir == bias:
        return boost, bias, "aligned"
    return penalty, bias, "opposed"


def get_directional_multiplier(
    side: str,
    now: datetime = None,
    boost: float = 1.15,
    penalty: float = 0.85,
) -> float:
    """Return a directional bias multiplier based on hour-of-day edge.

    D11 SHADOW (default): returns 1.0 and logs the would-have multiplier.
    TIME_SIZING_ENFORCE=true restores enforcement.

    Args:
        side: "BUY" or "SELL" — the trade direction.
        now: Override for testing. Defaults to current UTC time.
        boost: Multiplier when direction matches bias (default 1.15).
        penalty: Multiplier when direction opposes bias (default 0.85).

    Returns:
        Directional multiplier (1.0 unless enforced).
    """
    mult, bias, alignment = _raw_directional_multiplier(
        side, now=now, boost=boost, penalty=penalty)

    if _enforce():
        if mult != 1.0:
            _now = now or datetime.now(timezone.utc)
            logger.info(
                f"[TIME-SIZE] Directional {alignment}: {side} vs h{_now.hour} "
                f"{bias} bias → {mult:.2f}x"
            )
        return mult

    if mult != 1.0:
        _now = now or datetime.now(timezone.utc)
        logger.info(
            f"[SHADOW-TIME-SIZE] directional would apply {mult:.2f}x "
            f"({side} {alignment} h{_now.hour} {bias} bias) — neutralized to "
            f"1.0x (FALLACY_AUDIT D11)"
        )
    return 1.0


def get_full_time_multiplier(
    side: str = None,
    now: datetime = None,
    allow_boost: bool = True,
    max_boost: float = 1.4,
    directional_boost: float = 1.15,
    directional_penalty: float = 0.85,
) -> Dict[str, Union[float, str]]:
    """Return the complete time-based sizing adjustment.

    Combines:
    1. Hour-of-day session multiplier (PRIME/GOOD/QUIET/DEAD)
    2. Day-of-week multiplier (Mon boost, Thu penalty, weekend reduction)
    3. Directional bias multiplier (if side is provided)

    The combined multiplier is capped at max_boost to prevent runaway sizing.
    When allow_boost=False, the multiplier is capped at 1.0 (reductions only).

    Args:
        side: "BUY" or "SELL" for directional bias. None to skip.
        now: Override for testing. Defaults to current UTC time.
        allow_boost: If False, cap multiplier at 1.0 (reduce-only mode).
        max_boost: Maximum combined multiplier (default 1.4).
        directional_boost: Boost when side matches hour bias.
        directional_penalty: Penalty when side opposes hour bias.

    Returns:
        {
            "multiplier": float,  # Combined multiplier (1.0 unless TIME_SIZING_ENFORCE)
            "shadow_multiplier": float,  # Would-have combined multiplier (D11 shadow)
            "base_multiplier": float,  # Would-have hour * day multiplier (no directional)
            "directional_multiplier": float,  # Would-have directional bias component
            "bias": "long"|"short"|"neutral",
            "session": "PRIME"|"GOOD"|"QUIET"|"DEAD",
            "reasons": [str],  # Human-readable reasons
        }
    """
    if now is None:
        now = datetime.now(timezone.utc)

    reasons = []

    # Base time multiplier (hour * day) — raw would-have value (D11 shadow)
    base_mult, _base_reasons = _raw_time_multiplier(now)
    reasons.extend(_base_reasons)

    # Classify session for metadata
    session_mult = _SESSION_MULTIPLIERS.get(now.hour, 1.0)
    if session_mult >= 1.2:
        session = "PRIME"
    elif session_mult >= 1.0:
        session = "GOOD"
    elif session_mult >= 0.7:
        session = "QUIET"
    else:
        session = "DEAD"

    bias = _HOUR_BIAS.get(now.hour, "neutral")

    # Directional bias — raw would-have value (D11 shadow)
    dir_mult = 1.0
    if side is not None:
        dir_mult, _bias, _alignment = _raw_directional_multiplier(
            side=side,
            now=now,
            boost=directional_boost,
            penalty=directional_penalty,
        )
        if dir_mult != 1.0:
            reasons.append(f"dir_{_alignment}({dir_mult:.2f}x)")

    combined = base_mult * dir_mult

    # Cap boost if not allowed
    if not allow_boost and combined > 1.0:
        combined = 1.0
        reasons.append("boost_capped_at_1.0")

    # Cap at max boost
    if combined > max_boost:
        reasons.append(f"capped_{combined:.2f}→{max_boost:.2f}")
        combined = max_boost

    combined = round(combined, 4)

    if base_mult != 1.0:
        reasons.insert(0, f"base({base_mult:.3f}x)")

    # D11 SHADOW (default): report the would-have number, apply 1.0x.
    if _enforce():
        applied = combined
    else:
        applied = 1.0
        if combined != 1.0:
            reasons.append(f"neutralized_D11(would={combined:.3f}x)")
            logger.info(
                f"[SHADOW-TIME-SIZE] full time mult would apply {combined:.3f}x "
                f"(session={session}, bias={bias}) — neutralized to 1.0x "
                f"(FALLACY_AUDIT D11; TIME_SIZING_ENFORCE=true restores)"
            )

    return {
        "multiplier": applied,
        "shadow_multiplier": combined,
        "base_multiplier": base_mult,
        "directional_multiplier": dir_mult,
        "bias": bias,
        "session": session,
        "reasons": reasons,
    }


def get_time_sizing_info(now: datetime = None) -> Dict[str, Union[float, str]]:
    """Return multiplier AND directional bias for the current time.

    This is the rich version of get_time_multiplier() that also
    provides a directional bias hint for strategies that can use it.

    Args:
        now: Override for testing. Defaults to current UTC time.

    Returns:
        {"multiplier": float, "bias": "long"|"short"|"neutral"}
    """
    if now is None:
        now = datetime.now(timezone.utc)

    multiplier = get_time_multiplier(now)
    bias = _HOUR_BIAS.get(now.hour, "neutral")

    return {"multiplier": multiplier, "bias": bias}


def is_weekend(now: datetime = None) -> bool:
    """Check if current time is weekend (Saturday or Sunday UTC)."""
    if now is None:
        now = datetime.now(timezone.utc)
    return now.weekday() in (5, 6)


def is_low_liquidity_hours(now: datetime = None) -> bool:
    """Check if current time is in low-liquidity window (QUIET + DEAD hours)."""
    if now is None:
        now = datetime.now(timezone.utc)
    return now.hour in _LOW_LIQ_HOURS
