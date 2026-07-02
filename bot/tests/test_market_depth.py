"""Tests for the L4 market-depth feed (llm/agents/market_depth.py).

The feed is raw-truth INPUT (FULL_PIPE_BUILD_MAP F1): it must serve the latest
snapshot per symbol with provenance, compute 1h/4h deltas deterministically,
auto-mute on staleness (>45min) with ONE warning per episode, and degrade
gracefully on missing/empty/garbage files.
"""
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm.agents.market_depth as md  # noqa: E402
from llm.agents.market_depth import (  # noqa: E402
    format_depth_for_agent,
    format_depth_line,
    get_depth_for_snapshot,
    get_latest_depth,
)

NOW = datetime(2026, 7, 2, 22, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rec(sym="BTC", ts=None, mid=60000.0, spread_bps=0.20,
         bid05=1000.0, ask05=900.0, imb05=0.05,
         buy_vol=0.5, sell_vol=0.3, taker_bs=1.10, ls_acct=1.5):
    return {
        "ts": _iso(ts or NOW),
        "symbol": sym,
        "l2": {
            "mid": mid, "spread": 1.0, "spread_bps": spread_bps,
            "bid_depth_0_1pct": bid05 / 3, "ask_depth_0_1pct": ask05 / 3,
            "imbalance_0_1pct": imb05,
            "bid_depth_0_5pct": bid05, "ask_depth_0_5pct": ask05,
            "imbalance_0_5pct": imb05,
            "bid_depth_1pct": bid05 * 2, "ask_depth_1pct": ask05 * 2,
            "imbalance_1pct": imb05,
        },
        "trades": {"trade_count": 10, "buy_vol": buy_vol, "sell_vol": sell_vol,
                   "largest_trade": 0.1, "buy_ratio": 0.6},
        "futures_ctx": {"source": "okx", "mark_price": mid, "index_price": mid,
                        "funding_rate": 1e-5, "basis_bps": -3.0,
                        "long_short_account_ratio": ls_acct,
                        "taker_buy_sell_ratio": taker_bs},
    }


def _write(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(path)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Module-level one-WARN bookkeeping must not leak between tests."""
    md._stale_warned.clear()
    md._missing_file_warned = False
    monkeypatch.setenv("EXT_DEPTH_ENABLED", "true")
    yield
    md._stale_warned.clear()


# ── Reader ───────────────────────────────────────────────────────────

class TestReader:
    def test_latest_record_per_symbol_wins(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [
            _rec(ts=NOW - timedelta(minutes=30), spread_bps=9.9),
            _rec(ts=NOW - timedelta(minutes=5), spread_bps=0.2),
        ])
        out = get_latest_depth(["BTC"], filepath=f, now=NOW)
        assert "BTC" in out
        assert out["BTC"]["spread_bps"] == 0.2
        assert out["BTC"]["age_min"] == pytest.approx(5.0, abs=0.1)

    def test_missing_file_returns_empty(self, tmp_path):
        out = get_latest_depth(["BTC"], filepath=str(tmp_path / "nope.jsonl"), now=NOW)
        assert out == {}

    def test_empty_file_returns_empty(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [])
        assert get_latest_depth(["BTC"], filepath=f, now=NOW) == {}

    def test_malformed_lines_skipped(self, tmp_path):
        f = tmp_path / "d.jsonl"
        f.write_text('not json\n' + json.dumps(_rec(ts=NOW)) + '\n{"broken": \n')
        out = get_latest_depth(["BTC"], filepath=str(f), now=NOW)
        assert "BTC" in out

    def test_symbol_filter(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [_rec("BTC", ts=NOW), _rec("ETH", ts=NOW)])
        out = get_latest_depth(["ETH"], filepath=f, now=NOW)
        assert list(out.keys()) == ["ETH"]

    def test_usd_conversion_and_provenance(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW, mid=60000.0, bid05=1000.0)])
        e = get_latest_depth(["BTC"], filepath=f, now=NOW)["BTC"]
        assert e["bid_usd_0_5pct"] == pytest.approx(60_000_000.0)
        assert e["src"] == md.SOURCE_LABEL
        assert e["ts"] == _iso(NOW)
        assert e["fut"]["src"] == "okx"

    def test_young_stream_note_present(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW - timedelta(hours=6)),
                                          _rec(ts=NOW)])
        e = get_latest_depth(["BTC"], filepath=f, now=NOW)["BTC"]
        assert "no historical norms yet" in e["note"]


# ── Staleness auto-mute ──────────────────────────────────────────────

class TestStaleness:
    def test_stale_record_omitted(self, tmp_path, caplog):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW - timedelta(minutes=46))])
        with caplog.at_level(logging.WARNING):
            out = get_latest_depth(["BTC"], filepath=f, now=NOW)
        assert out == {}
        assert sum("muted" in r.message for r in caplog.records) == 1

    def test_fresh_record_kept_at_44min(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW - timedelta(minutes=44))])
        assert "BTC" in get_latest_depth(["BTC"], filepath=f, now=NOW)

    def test_one_warn_per_episode_not_per_scan(self, tmp_path, caplog):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW - timedelta(hours=2))])
        with caplog.at_level(logging.WARNING):
            get_latest_depth(["BTC"], filepath=f, now=NOW)
            get_latest_depth(["BTC"], filepath=f, now=NOW)
            get_latest_depth(["BTC"], filepath=f, now=NOW)
        assert sum("muted" in r.message for r in caplog.records) == 1

    def test_new_episode_warns_again_after_fresh(self, tmp_path, caplog):
        stale1 = _rec(ts=NOW - timedelta(hours=2))
        with caplog.at_level(logging.WARNING):
            f = _write(tmp_path / "d.jsonl", [stale1])
            get_latest_depth(["BTC"], filepath=f, now=NOW)              # warn 1
            f = _write(tmp_path / "d.jsonl", [stale1, _rec(ts=NOW)])
            get_latest_depth(["BTC"], filepath=f, now=NOW)              # fresh, resets
            f2 = _write(tmp_path / "d2.jsonl", [_rec(ts=NOW - timedelta(minutes=50))])
            get_latest_depth(["BTC"], filepath=f2, now=NOW)             # warn 2
        assert sum("muted" in r.message for r in caplog.records) == 2

    def test_bad_timestamp_treated_as_stale(self, tmp_path):
        r = _rec(ts=NOW)
        r["ts"] = "garbage"
        f = _write(tmp_path / "d.jsonl", [r])
        assert get_latest_depth(["BTC"], filepath=f, now=NOW) == {}


# ── Deltas ───────────────────────────────────────────────────────────

class TestDeltas:
    def test_1h_and_4h_deltas_computed(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [
            _rec(ts=NOW - timedelta(hours=4), spread_bps=0.10, imb05=0.20, taker_bs=1.00),
            _rec(ts=NOW - timedelta(hours=1), spread_bps=0.15, imb05=0.10, taker_bs=1.05),
            _rec(ts=NOW, spread_bps=0.20, imb05=0.05, taker_bs=1.10),
        ])
        e = get_latest_depth(["BTC"], filepath=f, now=NOW)["BTC"]
        assert e["d1h"]["spread_bps"] == pytest.approx(0.05)
        assert e["d1h"]["imb_0_5pct"] == pytest.approx(-0.05)
        assert e["d1h"]["taker_bs"] == pytest.approx(0.05)
        assert e["d4h"]["spread_bps"] == pytest.approx(0.10)
        assert e["d4h"]["imb_0_5pct"] == pytest.approx(-0.15)

    def test_no_history_yields_none_deltas(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW)])
        e = get_latest_depth(["BTC"], filepath=f, now=NOW)["BTC"]
        assert e["d1h"] is None and e["d4h"] is None

    def test_record_outside_tolerance_not_used(self, tmp_path):
        # 2.5h-old record: >30min from the 1h target, >60min from the 4h target
        f = _write(tmp_path / "d.jsonl", [
            _rec(ts=NOW - timedelta(hours=2, minutes=30), spread_bps=0.10),
            _rec(ts=NOW, spread_bps=0.20),
        ])
        e = get_latest_depth(["BTC"], filepath=f, now=NOW)["BTC"]
        assert e["d1h"] is None and e["d4h"] is None

    def test_depth_pct_change(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [
            _rec(ts=NOW - timedelta(hours=1), mid=60000.0, bid05=1000.0, ask05=1000.0),
            _rec(ts=NOW, mid=60000.0, bid05=1100.0, ask05=1100.0),
        ])
        e = get_latest_depth(["BTC"], filepath=f, now=NOW)["BTC"]
        assert e["d1h"]["depth_usd_0_5pct_pct"] == pytest.approx(10.0)


# ── Formatting (v1.3: raw + timestamp + source, no opinions) ─────────

class TestFormatting:
    def _entry(self, tmp_path, **kw):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW, **kw)])
        return get_latest_depth(["BTC"], filepath=f, now=NOW)["BTC"]

    def test_line_has_provenance_and_raw_values(self, tmp_path):
        line = format_depth_line("BTC", self._entry(tmp_path))
        assert line.startswith("DEPTH BTC @2026-07-02T22:00:00Z")
        assert md.SOURCE_LABEL in line
        assert "spread 0.20bps" in line
        assert "bid $60.0M/ask $54.0M" in line
        assert "taker B/S 1.10" in line

    def test_line_is_ascii_and_within_token_budget(self, tmp_path):
        line = format_depth_line("BTC", self._entry(tmp_path))
        assert line.isascii()
        assert len(line) < 700  # ~<175 tokens per symbol line

    def test_no_opinion_words(self, tmp_path):
        line = format_depth_line("BTC", self._entry(tmp_path)).lower()
        for word in ("bullish", "bearish", "buy signal", "should", "recommend"):
            assert word not in line

    def test_block_emits_note_once(self, tmp_path):
        f = _write(tmp_path / "d.jsonl",
                   [_rec("BTC", ts=NOW), _rec("ETH", ts=NOW)])
        depth = get_latest_depth(["BTC", "ETH"], filepath=f, now=NOW)
        block = format_depth_for_agent(depth, ["BTC", "ETH"])
        assert block.count("no historical norms") == 1
        assert block.count("DEPTH ") == 2


# ── Snapshot injection contract ──────────────────────────────────────

class TestSnapshotBlock:
    def test_keys_present_when_fresh(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW)])
        out = get_depth_for_snapshot(["BTC"], filepath=f, now=NOW)
        assert set(out.keys()) == {"ext_depth", "ext_depth_summary"}
        assert "BTC" in out["ext_depth"]

    def test_all_stale_auto_mutes_whole_block(self, tmp_path):
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW - timedelta(hours=3))])
        assert get_depth_for_snapshot(["BTC"], filepath=f, now=NOW) == {}

    def test_missing_file_mutes(self, tmp_path):
        assert get_depth_for_snapshot(
            ["BTC"], filepath=str(tmp_path / "nope.jsonl"), now=NOW) == {}

    def test_flag_off_mutes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXT_DEPTH_ENABLED", "false")
        f = _write(tmp_path / "d.jsonl", [_rec(ts=NOW)])
        assert get_depth_for_snapshot(["BTC"], filepath=f, now=NOW) == {}
