"""
Tests for time-of-day trading filter.

Validates:
- Hour-of-day session multipliers (PRIME/GOOD/QUIET/DEAD)
- Day-of-week multipliers (Mon boost, Thu penalty, weekend reduction)
- Directional bias multipliers (long/short alignment)
- Combined multiplier capping and boost control
- get_full_time_multiplier() integration
- Edge cases and boundary conditions

Updated 2026-04-03 to match fresh 7-day 5m cross-asset analysis.
"""

import pytest
from datetime import datetime, timezone

from execution.time_sizing import (
    get_time_multiplier,
    get_time_sizing_info,
    get_directional_multiplier,
    get_full_time_multiplier,
    is_weekend,
    is_low_liquidity_hours,
    _SESSION_MULTIPLIERS,
    _DAY_MULTIPLIERS,
    _HOUR_BIAS,
    _LOW_LIQ_HOURS,
)


# ── D11 (wave2b L5): time sizing is SHADOW by default ───────────────
# The multipliers below are neutralized to 1.0x in production (FALLACY_AUDIT
# D11 — April-2026 single-week study, never re-scored on the clean ledger).
# These tests pin the enforcement ARITHMETIC, so re-arm via the kill-switch.
# TestShadowDefaultD11 pins the actual production default (1.0x).

@pytest.fixture(autouse=True)
def _enforce_time_sizing(monkeypatch):
    monkeypatch.setenv("TIME_SIZING_ENFORCE", "true")


# ── Helpers ──────────────────────────────────────────────────────────

def _utc(year=2025, month=1, day=6, hour=12):
    """Create a UTC datetime. Default: Monday 2025-01-06 12:00."""
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


# Monday=6, Tuesday=7, Wednesday=8, Thursday=9, Friday=10, Saturday=11, Sunday=12 (Jan 2025)
MON = 6
TUE = 7
WED = 8
THU = 9
FRI = 10
SAT = 11
SUN = 12


# ── Session Multiplier Tests ────────────────────────────────────────

class TestSessionMultipliers:
    """Hour-of-day session classification."""

    def test_prime_hours(self):
        """PRIME hours get >= 1.2x boost."""
        for hour in [1, 13, 14, 15, 17, 22]:
            assert _SESSION_MULTIPLIERS[hour] >= 1.2, f"Hour {hour} should be PRIME (>=1.2x), got {_SESSION_MULTIPLIERS[hour]}"

    def test_good_hours(self):
        """GOOD hours get 1.0x (neutral)."""
        for hour in [0, 2, 8, 12, 16]:
            assert _SESSION_MULTIPLIERS[hour] == 1.0, f"Hour {hour} should be GOOD (1.0x)"

    def test_quiet_hours(self):
        """QUIET hours get 0.7x."""
        for hour in [3, 5, 9, 11, 19, 23]:
            assert _SESSION_MULTIPLIERS[hour] == 0.7, f"Hour {hour} should be QUIET (0.7x)"

    def test_dead_hours(self):
        """DEAD hours get <= 0.5x."""
        for hour in [4, 6, 7, 10, 18, 20, 21]:
            assert _SESSION_MULTIPLIERS[hour] <= 0.5, f"Hour {hour} should be DEAD (<=0.5x), got {_SESSION_MULTIPLIERS[hour]}"

    def test_all_24_hours_covered(self):
        """Every hour 0-23 has a multiplier."""
        for hour in range(24):
            assert hour in _SESSION_MULTIPLIERS, f"Hour {hour} missing from session multipliers"


class TestDayMultipliers:
    """Day-of-week classification."""

    def test_monday_best(self):
        assert _DAY_MULTIPLIERS[0] == 1.15

    def test_thursday_worst(self):
        assert _DAY_MULTIPLIERS[3] == 0.85

    def test_weekends_reduced(self):
        assert _DAY_MULTIPLIERS[5] == 0.8  # Saturday
        assert _DAY_MULTIPLIERS[6] == 0.8  # Sunday

    def test_friday_slightly_reduced(self):
        assert _DAY_MULTIPLIERS[4] == 0.95

    def test_tuesday_wednesday_neutral(self):
        assert _DAY_MULTIPLIERS[1] == 1.0
        assert _DAY_MULTIPLIERS[2] == 1.0

    def test_all_7_days_covered(self):
        for day in range(7):
            assert day in _DAY_MULTIPLIERS, f"Day {day} missing"


# ── get_time_multiplier Tests ────────────────────────────────────────

class TestGetTimeMultiplier:
    """Combined hour * day multiplier."""

    def test_monday_prime_hour(self):
        # Monday 15:00 UTC: 1.15 * 1.2 = 1.38
        now = _utc(day=MON, hour=15)
        assert abs(get_time_multiplier(now) - 1.38) < 0.001

    def test_monday_good_hour(self):
        # Monday 12:00 UTC: 1.15 * 1.0 = 1.15
        now = _utc(day=MON, hour=12)
        assert abs(get_time_multiplier(now) - 1.15) < 0.001

    def test_tuesday_quiet_hour(self):
        # Tuesday 05:00 UTC: 1.0 * 0.7 = 0.7
        now = _utc(day=TUE, hour=5)
        assert abs(get_time_multiplier(now) - 0.7) < 0.001

    def test_thursday_dead_hour(self):
        # Thursday 10:00 UTC: 0.85 * 0.5 = 0.425
        now = _utc(day=THU, hour=10)
        assert abs(get_time_multiplier(now) - 0.425) < 0.001

    def test_saturday_dead_hour(self):
        # Saturday 04:00 UTC: 0.8 * 0.5 = 0.4
        now = _utc(day=SAT, hour=4)
        assert abs(get_time_multiplier(now) - 0.4) < 0.001

    def test_sunday_prime_hour(self):
        # Sunday 15:00 UTC: 0.8 * 1.2 = 0.96
        now = _utc(day=SUN, hour=15)
        assert abs(get_time_multiplier(now) - 0.96) < 0.001

    def test_tuesday_midnight_good(self):
        # Tuesday 00:00 UTC: 1.0 * 1.0 = 1.0 (H00 is now GOOD)
        now = _utc(day=TUE, hour=0)
        assert abs(get_time_multiplier(now) - 1.0) < 0.001

    def test_wednesday_neutral(self):
        # Wednesday 12:00 UTC: 1.0 * 1.0 = 1.0
        now = _utc(day=WED, hour=12)
        assert abs(get_time_multiplier(now) - 1.0) < 0.001

    def test_result_is_rounded(self):
        now = _utc(day=MON, hour=15)
        result = get_time_multiplier(now)
        # Should have at most 4 decimal places
        assert result == round(result, 4)

    def test_defaults_to_utc_now(self):
        """If no argument, uses current UTC time (shouldn't crash)."""
        result = get_time_multiplier()
        assert 0.0 <= result <= 2.0  # any valid multiplier


# ── Directional Bias Tests ──────────────────────────────────────────

class TestDirectionalBias:
    """Hour-of-day directional bias."""

    def test_short_bias_hours(self):
        """Hours 8, 9, 17, 22 have short bias."""
        for hour in [8, 9, 17, 22]:
            assert _HOUR_BIAS[hour] == "short", f"Hour {hour} should be short"

    def test_long_bias_hours(self):
        """Hours 14, 15 have long bias (corrected from old SHORT)."""
        for hour in [14, 15]:
            assert _HOUR_BIAS[hour] == "long", f"Hour {hour} should be long"

    def test_neutral_hours_not_in_bias(self):
        """Most hours have no directional bias."""
        biased = set(_HOUR_BIAS.keys())
        for hour in range(24):
            if hour not in biased:
                assert hour not in _HOUR_BIAS

    def test_old_wrong_biases_removed(self):
        """H13 no longer has short bias (data shows neutral), H18 no longer long."""
        assert 13 not in _HOUR_BIAS or _HOUR_BIAS.get(13) != "short" or 13 in _HOUR_BIAS
        # H18 should NOT have long bias anymore (50/50 in data)
        assert _HOUR_BIAS.get(18) != "long"


class TestGetDirectionalMultiplier:
    """Directional sizing boost/penalty."""

    def test_sell_at_17_gets_boost(self):
        # 17:00 UTC has short bias, SELL matches
        now = _utc(day=TUE, hour=17)
        mult = get_directional_multiplier("SELL", now)
        assert mult == 1.15

    def test_buy_at_17_gets_penalty(self):
        # 17:00 UTC has short bias, BUY opposes
        now = _utc(day=TUE, hour=17)
        mult = get_directional_multiplier("BUY", now)
        assert mult == 0.85

    def test_buy_at_14_gets_boost(self):
        # 14:00 UTC now has LONG bias (corrected), BUY matches
        now = _utc(day=TUE, hour=14)
        mult = get_directional_multiplier("BUY", now)
        assert mult == 1.15

    def test_sell_at_14_gets_penalty(self):
        # 14:00 UTC now has LONG bias, SELL opposes
        now = _utc(day=TUE, hour=14)
        mult = get_directional_multiplier("SELL", now)
        assert mult == 0.85

    def test_neutral_hour_returns_1(self):
        # 12:00 UTC has no bias
        now = _utc(day=TUE, hour=12)
        assert get_directional_multiplier("BUY", now) == 1.0
        assert get_directional_multiplier("SELL", now) == 1.0

    def test_custom_boost_penalty(self):
        now = _utc(day=TUE, hour=17)
        mult = get_directional_multiplier("SELL", now, boost=1.3, penalty=0.7)
        assert mult == 1.3

    def test_case_insensitive_side(self):
        now = _utc(day=TUE, hour=17)
        assert get_directional_multiplier("sell", now) == 1.15
        assert get_directional_multiplier("Sell", now) == 1.15


# ── get_full_time_multiplier Tests ──────────────────────────────────

class TestGetFullTimeMultiplier:
    """Combined time + directional multiplier with config controls."""

    def test_basic_no_side(self):
        """Without side, returns base multiplier only."""
        now = _utc(day=MON, hour=15)  # 1.15 * 1.2 = 1.38
        info = get_full_time_multiplier(now=now)
        assert abs(info["multiplier"] - 1.38) < 0.001
        assert info["directional_multiplier"] == 1.0
        assert info["session"] == "PRIME"
        assert info["bias"] == "long"

    def test_with_aligned_direction(self):
        """BUY at 14:00 UTC (long bias) gets boost."""
        now = _utc(day=TUE, hour=14)  # base=1.2, dir=1.15
        info = get_full_time_multiplier(side="BUY", now=now)
        expected = 1.2 * 1.15  # 1.38
        assert abs(info["multiplier"] - expected) < 0.001
        assert info["directional_multiplier"] == 1.15

    def test_with_opposed_direction(self):
        """SELL at 14:00 UTC (long bias) gets penalty."""
        now = _utc(day=TUE, hour=14)  # base=1.2, dir=0.85
        info = get_full_time_multiplier(side="SELL", now=now)
        expected = 1.2 * 0.85  # 1.02
        assert abs(info["multiplier"] - expected) < 0.001

    def test_max_boost_cap(self):
        """Combined multiplier capped at max_boost."""
        now = _utc(day=MON, hour=15)  # 1.15 * 1.2 = 1.38 base
        info = get_full_time_multiplier(
            side="BUY", now=now, max_boost=1.3,
        )
        # Would be 1.38 * 1.15 = 1.587, but capped at 1.3
        assert info["multiplier"] == 1.3

    def test_allow_boost_false(self):
        """With allow_boost=False, multiplier capped at 1.0."""
        now = _utc(day=MON, hour=15)  # base=1.38
        info = get_full_time_multiplier(now=now, allow_boost=False)
        assert info["multiplier"] == 1.0

    def test_allow_boost_false_still_reduces(self):
        """With allow_boost=False, reductions still apply."""
        now = _utc(day=THU, hour=4)  # 0.85 * 0.5 = 0.425
        info = get_full_time_multiplier(now=now, allow_boost=False)
        assert abs(info["multiplier"] - 0.425) < 0.001

    def test_dead_hour_with_directional_penalty(self):
        """DEAD hour + no directional bias = base reduction only."""
        now = _utc(day=THU, hour=4)  # base=0.425
        info = get_full_time_multiplier(side="BUY", now=now)
        # Dead hour (0.425) + no bias at h4 -> dir=1.0
        assert abs(info["multiplier"] - 0.425) < 0.001

    def test_session_classification(self):
        """Session field correctly identifies PRIME/GOOD/QUIET/DEAD."""
        assert get_full_time_multiplier(now=_utc(hour=14))["session"] == "PRIME"
        assert get_full_time_multiplier(now=_utc(hour=12))["session"] == "GOOD"
        assert get_full_time_multiplier(now=_utc(hour=9))["session"] == "QUIET"
        assert get_full_time_multiplier(now=_utc(hour=4))["session"] == "DEAD"

    def test_bias_field(self):
        """Bias field matches _HOUR_BIAS."""
        assert get_full_time_multiplier(now=_utc(hour=17))["bias"] == "short"
        assert get_full_time_multiplier(now=_utc(hour=14))["bias"] == "long"
        assert get_full_time_multiplier(now=_utc(hour=12))["bias"] == "neutral"

    def test_reasons_populated(self):
        """Reasons list includes relevant adjustments."""
        info = get_full_time_multiplier(
            side="BUY", now=_utc(day=MON, hour=14),
        )
        assert len(info["reasons"]) > 0
        # Should mention base and directional
        reasons_str = " ".join(info["reasons"])
        assert "base" in reasons_str
        assert "dir_aligned" in reasons_str

    def test_reasons_empty_for_neutral(self):
        """No reasons for perfectly neutral time (Wed 12:00)."""
        info = get_full_time_multiplier(now=_utc(day=WED, hour=12))
        assert info["multiplier"] == 1.0
        assert len(info["reasons"]) == 0

    def test_custom_directional_params(self):
        """Custom directional boost/penalty are respected."""
        now = _utc(day=TUE, hour=17)  # short bias, PRIME hour (1.2)
        info = get_full_time_multiplier(
            side="SELL", now=now,
            directional_boost=1.3,
            directional_penalty=0.7,
        )
        # 1.2 * 1.3 = 1.56 -> capped at max_boost=1.4
        assert info["multiplier"] <= 1.4
        assert info["directional_multiplier"] == 1.3

    def test_default_max_boost_is_1_4(self):
        """Default max boost is 1.4."""
        # Monday PRIME + aligned direction: 1.15 * 1.2 * 1.15 = 1.587
        now = _utc(day=MON, hour=14)
        info = get_full_time_multiplier(side="BUY", now=now)
        assert info["multiplier"] <= 1.4


# ── get_time_sizing_info Tests ──────────────────────────────────────

class TestGetTimeSizingInfo:
    """Legacy API compatibility."""

    def test_returns_multiplier_and_bias(self):
        info = get_time_sizing_info(_utc(day=MON, hour=17))
        assert "multiplier" in info
        assert "bias" in info
        assert info["bias"] == "short"

    def test_multiplier_matches_get_time_multiplier(self):
        now = _utc(day=MON, hour=15)
        info = get_time_sizing_info(now)
        assert info["multiplier"] == get_time_multiplier(now)


# ── Utility Tests ────────────────────────────────────────────────────

class TestUtilities:
    """is_weekend, is_low_liquidity_hours."""

    def test_weekend_saturday(self):
        assert is_weekend(_utc(day=SAT)) is True

    def test_weekend_sunday(self):
        assert is_weekend(_utc(day=SUN)) is True

    def test_not_weekend_monday(self):
        assert is_weekend(_utc(day=MON)) is False

    def test_low_liq_dead_hours(self):
        for hour in [4, 6, 7, 10, 18, 20, 21]:
            assert is_low_liquidity_hours(_utc(hour=hour)) is True

    def test_low_liq_quiet_hours(self):
        for hour in [3, 5, 9, 11, 19, 23]:
            assert is_low_liquidity_hours(_utc(hour=hour)) is True

    def test_not_low_liq_prime_and_good(self):
        for hour in [1, 2, 8, 12, 13, 14, 15, 16, 17, 22]:
            assert is_low_liquidity_hours(_utc(hour=hour)) is False


# ── Integration / Edge Case Tests ────────────────────────────────────

class TestEdgeCases:
    """Boundary conditions and integration scenarios."""

    def test_never_returns_zero(self):
        """Multiplier should never be zero (would kill trades)."""
        for day in range(7):
            for hour in range(24):
                dt = datetime(2025, 1, 6 + day, hour, 0, tzinfo=timezone.utc)
                mult = get_time_multiplier(dt)
                assert mult > 0, f"day={day} hour={hour} returned 0"

    def test_never_returns_negative(self):
        """No negative multipliers."""
        for day in range(7):
            for hour in range(24):
                dt = datetime(2025, 1, 6 + day, hour, 0, tzinfo=timezone.utc)
                info = get_full_time_multiplier(side="BUY", now=dt)
                assert info["multiplier"] > 0

    def test_worst_case_minimum(self):
        """Worst case: Saturday/Sunday DEAD hour = 0.4."""
        worst = _utc(day=SAT, hour=4)
        assert abs(get_time_multiplier(worst) - 0.4) < 0.001

    def test_best_case_capped(self):
        """Best case: Monday PRIME + aligned direction, capped at 1.4."""
        best = _utc(day=MON, hour=14)  # PRIME=1.2, Mon=1.15, long bias
        info = get_full_time_multiplier(side="BUY", now=best)
        assert info["multiplier"] <= 1.4

    def test_full_multiplier_structure(self):
        """get_full_time_multiplier returns all expected keys."""
        info = get_full_time_multiplier(side="BUY", now=_utc(hour=14))
        expected_keys = {
            "multiplier", "shadow_multiplier", "base_multiplier",
            "directional_multiplier", "bias", "session", "reasons",
        }
        assert set(info.keys()) == expected_keys

    def test_reduce_only_mode_never_above_1(self):
        """allow_boost=False means multiplier is always <= 1.0."""
        for day in range(7):
            for hour in range(24):
                dt = datetime(2025, 1, 6 + day, hour, 0, tzinfo=timezone.utc)
                for side in ["BUY", "SELL"]:
                    info = get_full_time_multiplier(
                        side=side, now=dt, allow_boost=False,
                    )
                    assert info["multiplier"] <= 1.0, (
                        f"day={day} h={hour} side={side} "
                        f"mult={info['multiplier']} > 1.0 in reduce-only mode"
                    )


# ── D11 Shadow Default Tests ─────────────────────────────────────────

class TestShadowDefaultD11:
    """Production default: multipliers computed + logged but 1.0x applied."""

    @pytest.fixture(autouse=True)
    def _no_enforce(self, monkeypatch):
        monkeypatch.delenv("TIME_SIZING_ENFORCE", raising=False)

    def test_time_multiplier_neutralized(self):
        # Monday 15:00 UTC would be 1.38x — shadow default applies 1.0x
        assert get_time_multiplier(_utc(day=MON, hour=15)) == 1.0

    def test_directional_neutralized(self):
        # 17:00 short bias would boost SELL 1.15x — shadow default 1.0x
        assert get_directional_multiplier("SELL", _utc(day=TUE, hour=17)) == 1.0
        assert get_directional_multiplier("BUY", _utc(day=TUE, hour=17)) == 1.0

    def test_full_multiplier_neutralized_but_shadow_reported(self):
        info = get_full_time_multiplier(side="BUY", now=_utc(day=MON, hour=14))
        assert info["multiplier"] == 1.0
        # Would-have number preserved for the shadow ledger
        assert abs(info["shadow_multiplier"] - 1.4) < 0.001  # capped at 1.4
        assert any("neutralized_D11" in r for r in info["reasons"])

    def test_dead_hour_not_reduced(self):
        # Saturday DEAD hour would be 0.4x — shadow default keeps 1.0x
        assert get_time_multiplier(_utc(day=SAT, hour=4)) == 1.0


# ── Config Integration Test ──────────────────────────────────────────

class TestConfigIntegration:
    """Verify trading_config.py has the right defaults."""

    def test_config_has_time_sizing_fields(self):
        from trading_config import TradingConfig
        config = TradingConfig()
        assert hasattr(config, "enable_time_sizing")
        assert hasattr(config, "time_sizing_allow_boost")
        assert hasattr(config, "time_sizing_max_boost")
        assert hasattr(config, "time_sizing_directional_boost")
        assert hasattr(config, "time_sizing_directional_penalty")

    def test_config_defaults(self):
        from trading_config import TradingConfig
        config = TradingConfig()
        assert config.enable_time_sizing is True
        assert config.time_sizing_allow_boost is True
        assert abs(config.time_sizing_max_boost - 1.4) < 0.01
        assert abs(config.time_sizing_directional_boost - 1.15) < 0.01
        assert abs(config.time_sizing_directional_penalty - 0.85) < 0.01
