"""Market-depth feed for the LLM agent network (FULL_PIPE_BUILD_MAP F1, lane L4).

Reads bot/data/market_depth_history.jsonl — L2 spread/depth/imbalance + trade
tape + OKX futures context, written every 15min by tools/market_collector.py
(5 symbols since 2026-07-01). This module is the stream's FIRST reader.

Output: an ``ext_depth`` context block — latest snapshot per symbol plus 1h/4h
deltas. THE_STANDARD v1.3: raw values with timestamps + source labels, no
opinions, no interpretation beyond units. Deterministic unit conversion
(coin size × mid → USD) is the only transform applied.

Staleness auto-mute: records older than STALE_MINUTES (45min = 3 missed
collector cycles) are omitted with ONE warning per staleness episode, not one
per scan. Missing/empty file → empty dict (silent skip after one warning).

Flag: EXT_DEPTH_ENABLED (default true). Reversible: set false to mute the
whole block without code changes.

Token budget: ~60-90 tokens per symbol line. Callers should pass the
decision's symbol(s) to ``get_depth_for_snapshot`` to stay inside the
~300 tokens/decision budget from the build map.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# Reuse the battle-tested helpers from the sibling feed module (same package).
from llm.agents.external_data import (  # noqa: F401
    DEFAULT_SYMBOLS,
    _parse_ts,
    _read_jsonl_tail,
)

logger = logging.getLogger("bot.llm.agents.market_depth")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DEPTH_FILE = os.path.join(DATA_DIR, "market_depth_history.jsonl")

STALE_MINUTES = 45.0          # 3 missed 15-min collector cycles → mute
DELTA_WINDOWS_H = (1.0, 4.0)  # trend deltas vs ~1h and ~4h ago
DELTA_TOLERANCE_H = {1.0: 0.5, 4.0: 1.0}  # how far a record may sit from target
NORMS_NOTE_DAYS = 7.0         # while stream is younger than this, flag "no norms"
SOURCE_LABEL = "HL L2+OKX, 15m collector"
# 15-min cadence × 5 symbols ≈ 480 lines/day; tail window covers > NORMS_NOTE_DAYS
_TAIL_LINES = 4000

# One-WARN bookkeeping: symbol → stale ts we already warned about.
# Cleared when the symbol goes fresh again (new staleness episode re-warns once).
_stale_warned: Dict[str, str] = {}
_missing_file_warned = False


def _enabled() -> bool:
    return os.getenv("EXT_DEPTH_ENABLED", "true").strip().lower() in ("1", "true", "yes")


def _now(now: Optional[datetime] = None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _age_minutes(ts_str: str, now: Optional[datetime] = None) -> float:
    dt = _parse_ts(ts_str)
    if not dt:
        return float("inf")
    return (_now(now) - dt).total_seconds() / 60.0


def _usd(val: float) -> str:
    """Compact USD: $72.9M / $310K / $87."""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "$?"
    if val >= 1e9:
        return f"${val / 1e9:.1f}B"
    if val >= 1e6:
        return f"${val / 1e6:.1f}M"
    if val >= 1e3:
        return f"${val / 1e3:.0f}K"
    return f"${val:.0f}"


def _read_records(filepath: Optional[str] = None) -> List[dict]:
    """Read the depth history tail. Empty list (one WARN) when file missing."""
    global _missing_file_warned
    path = filepath or DEPTH_FILE
    if not os.path.exists(path):
        if not _missing_file_warned:
            logger.warning("[EXT-DEPTH] history file missing: %s — depth block muted", path)
            _missing_file_warned = True
        return []
    _missing_file_warned = False
    return _read_jsonl_tail(path, max_lines=_TAIL_LINES)


def _records_by_symbol(records: List[dict], symbols: List[str]) -> Dict[str, List[dict]]:
    by_sym: Dict[str, List[dict]] = {}
    for r in records:
        sym = r.get("symbol", "")
        if sym in symbols:
            by_sym.setdefault(sym, []).append(r)
    return by_sym


def _record_near(records: List[dict], target: datetime, tolerance_h: float) -> Optional[dict]:
    """The record whose ts is closest to `target`, within tolerance. None if none."""
    best, best_gap = None, tolerance_h * 3600.0
    for r in records:
        dt = _parse_ts(r.get("ts", ""))
        if not dt:
            continue
        gap = abs((dt - target).total_seconds())
        if gap <= best_gap:
            best, best_gap = r, gap
    return best


def _total_depth_usd(l2: dict, mid: float) -> float:
    return (float(l2.get("bid_depth_0_5pct", 0) or 0)
            + float(l2.get("ask_depth_0_5pct", 0) or 0)) * mid


def _compute_delta(latest: dict, past: Optional[dict]) -> Optional[dict]:
    """Raw diffs latest-minus-past for the fields worth trending. None if no past."""
    if not past:
        return None
    l_l2, p_l2 = latest.get("l2", {}) or {}, past.get("l2", {}) or {}
    l_fut, p_fut = latest.get("futures_ctx", {}) or {}, past.get("futures_ctx", {}) or {}
    out = {}
    try:
        out["spread_bps"] = round(
            float(l_l2.get("spread_bps", 0)) - float(p_l2.get("spread_bps", 0)), 3)
        out["imb_0_5pct"] = round(
            float(l_l2.get("imbalance_0_5pct", 0)) - float(p_l2.get("imbalance_0_5pct", 0)), 4)
        l_mid = float(l_l2.get("mid", 0) or 0)
        p_mid = float(p_l2.get("mid", 0) or 0)
        p_depth = _total_depth_usd(p_l2, p_mid)
        if p_depth > 0:
            out["depth_usd_0_5pct_pct"] = round(
                (_total_depth_usd(l_l2, l_mid) - p_depth) / p_depth * 100.0, 1)
        if l_fut.get("taker_buy_sell_ratio") is not None and \
                p_fut.get("taker_buy_sell_ratio") is not None:
            out["taker_bs"] = round(float(l_fut["taker_buy_sell_ratio"])
                                    - float(p_fut["taker_buy_sell_ratio"]), 3)
        if l_fut.get("long_short_account_ratio") is not None and \
                p_fut.get("long_short_account_ratio") is not None:
            out["ls_acct"] = round(float(l_fut["long_short_account_ratio"])
                                   - float(p_fut["long_short_account_ratio"]), 2)
    except (TypeError, ValueError):
        return None
    return out or None


def get_latest_depth(symbols: Optional[List[str]] = None,
                     filepath: Optional[str] = None,
                     now: Optional[datetime] = None) -> Dict[str, dict]:
    """Latest NON-STALE depth record per symbol, plus 1h/4h deltas.

    Returns {symbol: structured dict} — see keys built below. Symbols whose
    latest record is older than STALE_MINUTES are omitted (auto-mute) with one
    warning per staleness episode.
    """
    symbols = symbols or DEFAULT_SYMBOLS
    records = _read_records(filepath)
    if not records:
        return {}
    by_sym = _records_by_symbol(records, symbols)
    now_dt = _now(now)

    # "no historical norms yet" note while stream is young (v1.3 honesty about n)
    earliest = None
    for r in records:
        dt = _parse_ts(r.get("ts", ""))
        if dt and (earliest is None or dt < earliest):
            earliest = dt
    young = earliest is not None and (now_dt - earliest) < timedelta(days=NORMS_NOTE_DAYS)

    result: Dict[str, dict] = {}
    for sym, recs in by_sym.items():
        latest = recs[-1]  # file is chronological
        ts = latest.get("ts", "")
        age_min = _age_minutes(ts, now_dt)
        if age_min > STALE_MINUTES:
            if _stale_warned.get(sym) != ts:
                logger.warning(
                    "[EXT-DEPTH] %s latest record %.0fmin old (> %.0fmin) — muted (ts=%s)",
                    sym, age_min, STALE_MINUTES, ts)
                _stale_warned[sym] = ts
            continue
        _stale_warned.pop(sym, None)  # fresh again → next episode warns anew

        l2 = latest.get("l2", {}) or {}
        trades = latest.get("trades", {}) or {}
        fut = latest.get("futures_ctx", {}) or {}
        mid = float(l2.get("mid", 0) or 0)

        entry = {
            "ts": ts,
            "age_min": round(age_min, 1),
            "src": SOURCE_LABEL,
            "mid": mid,
            "spread_bps": l2.get("spread_bps"),
            "bid_usd_0_5pct": round(float(l2.get("bid_depth_0_5pct", 0) or 0) * mid, 0),
            "ask_usd_0_5pct": round(float(l2.get("ask_depth_0_5pct", 0) or 0) * mid, 0),
            "imb_0_5pct": l2.get("imbalance_0_5pct"),
            "imb_1pct": l2.get("imbalance_1pct"),
            "tape": {
                "n": trades.get("trade_count", 0),
                "buy_usd": round(float(trades.get("buy_vol", 0) or 0) * mid, 0),
                "sell_usd": round(float(trades.get("sell_vol", 0) or 0) * mid, 0),
                "buy_ratio": trades.get("buy_ratio"),
                "largest_usd": round(float(trades.get("largest_trade", 0) or 0) * mid, 0),
            },
            "fut": {
                "src": fut.get("source", "?"),
                "funding": fut.get("funding_rate"),
                "basis_bps": fut.get("basis_bps"),
                "ls_acct": fut.get("long_short_account_ratio"),
                "taker_bs": fut.get("taker_buy_sell_ratio"),
            },
        }
        for win_h in DELTA_WINDOWS_H:
            past = _record_near(recs, now_dt - timedelta(hours=win_h),
                                DELTA_TOLERANCE_H[win_h])
            # A "past" record too close to the latest one is not a trend sample
            if past is latest:
                past = None
            entry[f"d{int(win_h)}h"] = _compute_delta(latest, past)
        if young:
            entry["note"] = (
                f"collecting since {earliest.date().isoformat()} - no historical norms yet "
                f"(n<{int(NORMS_NOTE_DAYS)}d), treat levels as raw observation not signal")
        result[sym] = entry
    return result


def _fmt_delta(tag: str, d: Optional[dict]) -> str:
    if not d:
        return ""
    bits = []
    if "spread_bps" in d:
        bits.append(f"spread{d['spread_bps']:+.2f}bps")
    if "imb_0_5pct" in d:
        bits.append(f"imb{d['imb_0_5pct']:+.3f}")
    if "depth_usd_0_5pct_pct" in d:
        bits.append(f"depth{d['depth_usd_0_5pct_pct']:+.1f}%")
    if "taker_bs" in d:
        bits.append(f"takerBS{d['taker_bs']:+.2f}")
    if not bits:
        return ""
    return f"{tag} chg: " + " ".join(bits)


def format_depth_line(sym: str, e: dict, include_note: bool = True) -> str:
    """One v1.3 line per symbol: raw values + timestamp + source, no opinions."""
    header = f"DEPTH {sym} @{e.get('ts', '?')} ({e.get('src', '?')}, age {e.get('age_min', '?')}m): "
    parts = []
    if e.get("spread_bps") is not None:
        parts.append(f"spread {e['spread_bps']:.2f}bps")
    parts.append(
        f"depth0.5%: bid {_usd(e.get('bid_usd_0_5pct', 0))}/ask {_usd(e.get('ask_usd_0_5pct', 0))}"
        + (f" imb {e['imb_0_5pct']:+.3f}" if e.get("imb_0_5pct") is not None else ""))
    tape = e.get("tape", {}) or {}
    if tape.get("n"):
        t = f"tape(last {tape['n']}): buy {_usd(tape.get('buy_usd', 0))}/sell {_usd(tape.get('sell_usd', 0))}"
        if tape.get("largest_usd"):
            t += f", largest {_usd(tape['largest_usd'])}"
        parts.append(t)
    fut = e.get("fut", {}) or {}
    fut_bits = []
    if fut.get("taker_bs") is not None:
        fut_bits.append(f"taker B/S {fut['taker_bs']:.2f}")
    if fut.get("ls_acct") is not None:
        fut_bits.append(f"L/S accts {fut['ls_acct']:.2f}")
    if fut.get("basis_bps") is not None:
        fut_bits.append(f"basis {fut['basis_bps']:+.1f}bps")
    if fut_bits:
        parts.append(f"{', '.join(fut_bits)} ({fut.get('src', '?')})")
    for tag in ("1h", "4h"):
        s = _fmt_delta(tag, e.get(f"d{tag[0]}h"))
        if s:
            parts.append(s)
    line = header + " | ".join(parts)
    if include_note and e.get("note"):
        line += f" | NOTE: {e['note']}"
    return line


def format_depth_for_agent(depth: Dict[str, dict],
                           symbols: Optional[List[str]] = None) -> str:
    """Multi-symbol text block, one line per symbol, ordered by `symbols`.

    The young-stream NOTE (identical across symbols) is emitted ONCE at the
    end instead of per line — token budget.
    """
    order = [s for s in (symbols or DEFAULT_SYMBOLS) if s in depth]
    order += [s for s in depth if s not in order]
    lines = [format_depth_line(s, depth[s], include_note=False) for s in order]
    notes = {depth[s].get("note") for s in order if depth[s].get("note")}
    if notes:
        lines.append(f"NOTE: {sorted(notes)[0]}")
    return "\n".join(lines)


def get_depth_for_snapshot(symbols: Optional[List[str]] = None,
                           filepath: Optional[str] = None,
                           now: Optional[datetime] = None) -> Dict:
    """Structured + text depth block for snapshot injection.

    Returns (possibly empty) dict:
      ext_depth:         {sym: structured raw values + deltas + provenance}
      ext_depth_summary: compact one-line-per-symbol text (v1.3 format)

    Empty dict when flag off, file missing, or ALL symbols stale — the block
    auto-mutes rather than serving dead data (WIRING invariant 7).
    """
    if not _enabled():
        return {}
    depth = get_latest_depth(symbols=symbols, filepath=filepath, now=now)
    if not depth:
        return {}
    return {
        "ext_depth": depth,
        "ext_depth_summary": format_depth_for_agent(depth, symbols),
    }
