"""
ISOLATED market-data expansion collector (RQ28, coordination/RESEARCH_AGENDA.md).

Collects per symbol (BTC, ETH, SOL, HYPE, XRP), one run per invocation:
  1. Hyperliquid L2 book snapshot -> derived microstructure metrics only
     (spread, mid, depth within 0.1%/0.5%/1% of mid per side, imbalance).
  2. Hyperliquid recent-trades aggregate (buy/sell volume, largest trade, count).
  3. Futures context (free, no key): mark/index/funding + basis, long/short
     account ratio, taker buy/sell ratio. Primary source Binance fapi
     (HYPE not listed there); fallback OKX public/rubik endpoints when
     Binance is unreachable or geo-blocked (HTTP 451 — the case on this
     host). OKX also covers HYPE funding/basis. Record carries "source".

Appends one JSON line per symbol per run to bot/data/market_depth_history.jsonl.
Fail-soft per source: a failed source yields null fields, never kills the run.

HARD CONSTRAINT: zero contact with bot runtime code. Stdlib + requests only.
Driven by its own Windows scheduled task ("WAGMI-MarketCollector", every 15 min):
  python market_collector.py --once
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

SYMBOLS = ["BTC", "ETH", "SOL", "HYPE", "XRP"]
BINANCE_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}  # HYPE not on Binance

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
BINANCE_FAPI = "https://fapi.binance.com"
OKX_BASE = "https://www.okx.com"

# Set to True after the first HTTP 451 so we stop hammering a geo-blocked API.
_BINANCE_GEO_BLOCKED = False

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "market_depth_history.jsonl")
TIMEOUT = 10  # seconds per HTTP call
DEPTH_BANDS = [0.001, 0.005, 0.01]  # 0.1%, 0.5%, 1% of mid


def _post_hl(payload):
    r = requests.post(HL_INFO_URL, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _get_binance(path, params=None):
    r = requests.get(BINANCE_FAPI + path, params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _fetch_book(coin, n_sig_figs=None):
    """Fetch HL L2 book; returns (bids, asks) level lists."""
    payload = {"type": "l2Book", "coin": coin}
    if n_sig_figs is not None:
        payload["nSigFigs"] = n_sig_figs
    book = _post_hl(payload)
    levels = book.get("levels") or []
    if len(levels) < 2 or not levels[0] or not levels[1]:
        raise ValueError("empty book")
    return levels[0], levels[1]  # HL: levels[0]=bids, levels[1]=asks


def _band_depth(side_levels, mid, band):
    lo, hi = mid * (1 - band), mid * (1 + band)
    return sum(float(l["sz"]) for l in side_levels if lo <= float(l["px"]) <= hi)


def collect_l2_metrics(coin):
    """HL L2 book -> derived metrics only (never store the raw book).

    The book returns max 20 levels per side, and its price span depends on
    nSigFigs AND where the price sits in its sig-fig decade (e.g. nSigFigs=4
    spans ~0.33% on BTC@60k but ~1.9% on XRP@1.05). So we take three snapshots
    (full precision, nSigFigs=4, nSigFigs=3) and, per band, use the FINEST
    snapshot whose span actually covers the band. Spread/mid always come from
    the full-precision book.
    """
    books = [("fine", _fetch_book(coin))]  # fetched eagerly; nsf books lazily
    bids, asks = books[0][1]
    best_bid, best_ask = float(bids[0]["px"]), float(asks[0]["px"])
    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid
    if mid <= 0 or spread < 0:
        raise ValueError(f"insane book: bid={best_bid} ask={best_ask}")

    m = {
        "mid": round(mid, 8),
        "spread": round(spread, 8),
        "spread_bps": round(spread / mid * 1e4, 4),
    }

    def covers(book_levels, band):
        b_lv, a_lv = book_levels
        return float(b_lv[-1]["px"]) <= mid * (1 - band) and float(a_lv[-1]["px"]) >= mid * (1 + band)

    for band in DEPTH_BANDS:
        tag = f"{band * 100:g}pct".replace(".", "_")  # 0_1pct, 0_5pct, 1pct
        try:
            chosen = None
            for name, nsf in [("fine", None), ("nsf4", 4), ("nsf3", 3)]:
                if name not in [n for n, _ in books]:
                    books.append((name, _fetch_book(coin, n_sig_figs=nsf)))
                lv = dict(books)[name]
                if covers(lv, band):
                    chosen = lv
                    break
            if chosen is None:  # nothing spans the full band — widest is a lower bound
                chosen = dict(books)["nsf3"]
            bid_d = _band_depth(chosen[0], mid, band)
            ask_d = _band_depth(chosen[1], mid, band)
            m[f"bid_depth_{tag}"] = round(bid_d, 6)
            m[f"ask_depth_{tag}"] = round(ask_d, 6)
            m[f"imbalance_{tag}"] = round((bid_d - ask_d) / (bid_d + ask_d), 4) if (bid_d + ask_d) > 0 else None
        except Exception as e:
            print(f"[WARN] {coin} l2 band {tag}: {e}")
            m[f"bid_depth_{tag}"] = m[f"ask_depth_{tag}"] = m[f"imbalance_{tag}"] = None
    return m


def collect_trades_agg(coin):
    """HL recent trades -> aggregate. Endpoint may not exist; caller handles failure.

    MEASUREMENT CAVEAT (TABLE_B_NEWSTREAMS, 2026-07-02): HL recentTrades
    returns only the LAST ~10 TRADES (verified live: len=10, span ~5s), so
    buy_ratio here is dust and CANNOT work as a flow signal. Kept for
    largest_trade/trade_count continuity only; the honest full-interval taker
    flow lives in collect_taker_volume_15m() -> record key "taker_15m".
    """
    trades = _post_hl({"type": "recentTrades", "coin": coin})
    if not isinstance(trades, list) or not trades:
        raise ValueError("no trades returned")
    buy_vol = sell_vol = largest = 0.0
    for t in trades:
        sz = float(t.get("sz", 0))
        largest = max(largest, sz)
        if t.get("side") == "B":
            buy_vol += sz
        else:
            sell_vol += sz
    return {
        "trade_count": len(trades),
        "buy_vol": round(buy_vol, 6),
        "sell_vol": round(sell_vol, 6),
        "largest_trade": round(largest, 6),
        "buy_ratio": round(buy_vol / (buy_vol + sell_vol), 4) if (buy_vol + sell_vol) > 0 else None,
        "window": "last_10_trades_only",  # muted per TABLE_B Invariant 7 — use taker_15m
    }


def collect_taker_volume_15m(coin):
    """Full 15-min taker buy/sell VOLUME (TABLE_B collector fix #1, 2026-07-02).

    OKX rubik taker-volume, period=5m, rows newest-first as
    [ts, sellVol, buyVol] (verified live for all 5 symbols incl HYPE).
    Sums the 3 most recent rows => a true 15-minute interval, replacing the
    unusable last-10-trades sample. Window timestamps logged so effective-n
    stays honest.
    """
    rows = _get_okx("/api/v5/rubik/stat/taker-volume",
                    {"ccy": coin, "instType": "CONTRACTS", "period": "5m"})
    if not rows or len(rows) < 3:
        raise ValueError(f"okx taker-volume: {len(rows or [])} rows < 3")
    window = rows[:3]  # newest-first; newest row may be an in-progress bucket
    sell_vol = sum(float(r[1]) for r in window)
    buy_vol = sum(float(r[2]) for r in window)
    total = buy_vol + sell_vol
    return {
        "buy_vol_usd": round(buy_vol, 2),
        "sell_vol_usd": round(sell_vol, 2),
        "buy_ratio": round(buy_vol / total, 4) if total > 0 else None,
        "window_min": 15,
        "window_ts_start": int(window[-1][0]),
        "window_ts_end": int(window[0][0]),
        "src": "okx_rubik_taker_volume_5m_x3",
    }


def _get_okx(path, params=None):
    r = requests.get(OKX_BASE + path, params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if str(j.get("code")) != "0":
        raise ValueError(f"okx code={j.get('code')} msg={j.get('msg')}")
    return j.get("data") or []


def collect_binance_context(coin):
    """Binance futures free context (spec-primary source). Raises on failure."""
    global _BINANCE_GEO_BLOCKED
    if _BINANCE_GEO_BLOCKED:
        raise ValueError("binance geo-blocked (451) earlier this run")
    bsym = BINANCE_MAP.get(coin)
    if not bsym:
        raise ValueError("not listed on Binance futures")
    try:
        p = _get_binance("/fapi/v1/premiumIndex", {"symbol": bsym})
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 451:
            _BINANCE_GEO_BLOCKED = True
        raise
    out = {
        "source": "binance",
        "mark_price": float(p["markPrice"]),
        "index_price": float(p["indexPrice"]),
        "funding_rate": float(p["lastFundingRate"]),
    }
    out["basis_bps"] = round((out["mark_price"] - out["index_price"]) / out["index_price"] * 1e4, 4)
    try:
        ls = _get_binance("/futures/data/globalLongShortAccountRatio", {"symbol": bsym, "period": "15m", "limit": 1})
        out["long_short_account_ratio"] = float(ls[0]["longShortRatio"]) if ls else None
        # TABLE_B collector fix #2: log the SOURCE timestamp of the ratio (not
        # just poll time) so repeated stale values can be deduped — the level's
        # lag-1 AC is 0.89-0.98 and effective-n was dishonest without this.
        out["long_short_account_ratio_ts"] = int(ls[0]["timestamp"]) if ls else None
    except Exception as e:
        print(f"[WARN] {coin} binance longShortRatio: {e}")
        out["long_short_account_ratio"] = None
        out["long_short_account_ratio_ts"] = None
    try:
        tk = _get_binance("/futures/data/takerlongshortRatio", {"symbol": bsym, "period": "15m", "limit": 1})
        out["taker_buy_sell_ratio"] = float(tk[0]["buySellRatio"]) if tk else None
    except Exception as e:
        print(f"[WARN] {coin} binance takerRatio: {e}")
        out["taker_buy_sell_ratio"] = None
    return out


def collect_okx_context(coin):
    """OKX public fallback — same three metrics, free, no key. Covers HYPE too."""
    swap = f"{coin}-USDT-SWAP"
    out = {"source": "okx", "mark_price": None, "index_price": None,
           "funding_rate": None, "basis_bps": None,
           "long_short_account_ratio": None, "long_short_account_ratio_ts": None,
           "taker_buy_sell_ratio": None}
    try:
        fr = _get_okx("/api/v5/public/funding-rate", {"instId": swap})
        out["funding_rate"] = float(fr[0]["fundingRate"]) if fr else None
    except Exception as e:
        print(f"[WARN] {coin} okx funding: {e}")
    try:
        mk = _get_okx("/api/v5/public/mark-price", {"instId": swap})
        ix = _get_okx("/api/v5/market/index-tickers", {"instId": f"{coin}-USDT"})
        if mk and ix:
            out["mark_price"] = float(mk[0]["markPx"])
            out["index_price"] = float(ix[0]["idxPx"])
            out["basis_bps"] = round((out["mark_price"] - out["index_price"]) / out["index_price"] * 1e4, 4)
    except Exception as e:
        print(f"[WARN] {coin} okx mark/index: {e}")
    try:
        ls = _get_okx("/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": coin, "period": "5m"})
        out["long_short_account_ratio"] = float(ls[0][1]) if ls else None
        # TABLE_B collector fix #2: OKX row ts (source time, not poll time)
        out["long_short_account_ratio_ts"] = int(ls[0][0]) if ls else None
    except Exception as e:
        print(f"[WARN] {coin} okx longShortRatio: {e}")
    try:
        # rubik taker-volume rows: [ts, sellVol, buyVol]
        tk = _get_okx("/api/v5/rubik/stat/taker-volume", {"ccy": coin, "instType": "CONTRACTS", "period": "5m"})
        if tk and float(tk[0][1]) > 0:
            out["taker_buy_sell_ratio"] = round(float(tk[0][2]) / float(tk[0][1]), 4)
    except Exception as e:
        print(f"[WARN] {coin} okx takerRatio: {e}")
    if all(v is None for k, v in out.items() if k != "source"):
        raise ValueError("okx: all metrics failed")
    return out


def collect_futures_context(coin):
    """Binance primary; OKX fallback (geo-block, outage, or unlisted symbol)."""
    try:
        return collect_binance_context(coin)
    except Exception as e:
        print(f"[INFO] {coin} binance unavailable ({e}); trying okx")
        return collect_okx_context(coin)


def collect_once():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = []
    for coin in SYMBOLS:
        rec = {"ts": ts, "symbol": coin, "l2": None, "trades": None,
               "taker_15m": None, "futures_ctx": None}
        try:
            rec["l2"] = collect_l2_metrics(coin)
        except Exception as e:
            print(f"[WARN] {coin} l2Book: {e}")
        try:
            rec["trades"] = collect_trades_agg(coin)
        except Exception as e:
            print(f"[WARN] {coin} recentTrades: {e}")
        try:
            # TABLE_B fix #1: full 15-min taker buy/sell volume (the last-10-
            # trades sample above is dust and stays muted).
            rec["taker_15m"] = collect_taker_volume_15m(coin)
        except Exception as e:
            print(f"[WARN] {coin} taker_15m: {e}")
        try:
            rec["futures_ctx"] = collect_futures_context(coin)
        except Exception as e:
            print(f"[WARN] {coin} futures context: {e}")
        records.append(rec)
        time.sleep(0.25)  # be polite to free APIs
    return records


def save_records(records):
    path = os.path.abspath(DATA_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="Isolated market-data expansion collector (RQ28)")
    ap.add_argument("--once", action="store_true", default=True,
                    help="run one collection cycle and exit (default; scheduler drives cadence)")
    ap.parse_args()

    records = collect_once()
    path = save_records(records)
    ok = sum(1 for r in records if r["l2"] is not None)
    print(f"[{records[0]['ts']}] wrote {len(records)} rows ({ok} with L2 data) -> {path}")
    # Non-zero exit only if EVERY source failed for EVERY symbol (total outage)
    total_dead = all(r["l2"] is None and r["trades"] is None and r["futures_ctx"] is None for r in records)
    sys.exit(1 if total_dead else 0)


if __name__ == "__main__":
    main()
