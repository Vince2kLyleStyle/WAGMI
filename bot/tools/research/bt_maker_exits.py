# BT_MAKER_EXITS: replay historical exits as post-only limit fills vs actual taker fills.
# Read-only on bot code/data; writes only bt_* artifacts in this directory.
#
# Fee model (Hyperliquid base tier): taker 4.5 bps/side, maker 1.5 bps/side -> 3.0 bps saved per filled exit.
# Fill model (5m candles, three explicit assumptions):
#   OPTIMISTIC : touch-fill. Sell limit at P fills if any candle high >= P (incl. the exit candle itself),
#                window 30 min. Generous: same-candle extremes may predate order placement.
#   MID        : trade-through. Fill requires high >= P*(1+2bps) (sell) / low <= P*(1-2bps) (buy),
#                starting from the NEXT candle, window 30 min.
#   PESSIMISTIC: trade-through 5 bps, next candle onward, window 15 min.
# Unfilled -> taker fallback at the close of the last window candle: pay taker fee AND the adverse move
# (missed-exit slippage). By construction unfilled means price moved away from the limit, so slippage <= 0.
import json, os, csv, time, urllib.request
from datetime import datetime, timezone
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))  # bot/
CLOSES = os.path.join(ROOT, "data", "logs", "exit_closes.jsonl")
LEDGER = os.path.join(ROOT, "data", "trade_ledger.csv")
CACHE = os.path.join(HERE, "bt_candles_5m.json")
OUT = os.path.join(HERE, "bt_maker_exits_results.json")

SYMS = ["BTC", "ETH", "SOL", "XRP", "HYPE"]
START_MS = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)
C5 = 5 * 60 * 1000
TAKER, MAKER = 4.5e-4, 1.5e-4  # HL base tier per side

def fetch_candles():
    if os.path.exists(CACHE):
        d = json.load(open(CACHE))
        if time.time() * 1000 - max(c[0] for c in d["BTC"]) < 3 * 3600 * 1000:
            return d
    out = {}
    end = int(time.time() * 1000)
    for s in SYMS:
        rows = []
        t0 = START_MS
        while t0 < end:
            t1 = min(t0 + 14 * 24 * 3600 * 1000, end)  # 14d chunks (~4032 candles < 5000 cap)
            req = json.dumps({"type": "candleSnapshot", "req": {"coin": s, "interval": "5m",
                              "startTime": t0, "endTime": t1}}).encode()
            r = urllib.request.Request("https://api.hyperliquid.xyz/info", data=req,
                                       headers={"Content-Type": "application/json"})
            chunk = json.loads(urllib.request.urlopen(r, timeout=30).read())
            rows += [[int(c["t"]), float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])] for c in chunk]
            t0 = t1
            time.sleep(0.3)
        dedup = {c[0]: c for c in rows}
        out[s] = [dedup[k] for k in sorted(dedup)]
        print(s, len(out[s]), "5m candles",
              datetime.fromtimestamp(out[s][0][0] / 1000, tz=timezone.utc), "->",
              datetime.fromtimestamp(out[s][-1][0] / 1000, tz=timezone.utc))
    json.dump(out, open(CACHE, "w"))
    return out

CANDLES = fetch_candles()
IDX = {s: {c[0]: i for i, c in enumerate(CANDLES[s])} for s in SYMS}

def candle_idx(sym, ts_ms):
    return IDX[sym].get(ts_ms - ts_ms % C5)

def sim_limit_exit(sym, ts_ms, order_side, px, buf_bps, incl_same, timeout_min):
    """order_side: 'SELL' (long exit) or 'BUY' (short exit). Limit at px.
    Returns (filled, fill_px, fallback_px, waited_min). fallback_px = close of last window candle."""
    i0 = candle_idx(sym, ts_ms)
    if i0 is None:
        return None
    arr = CANDLES[sym]
    n = timeout_min // 5
    start = i0 if incl_same else i0 + 1
    lastw = min(i0 + n, len(arr) - 1)
    trig = px * (1 + buf_bps / 1e4) if order_side == "SELL" else px * (1 - buf_bps / 1e4)
    for i in range(start, lastw + 1):
        t, o, h, l, c = arr[i]
        if (order_side == "SELL" and h >= trig) or (order_side == "BUY" and l <= trig):
            return (True, px, None, (i - i0) * 5)
    return (False, None, arr[lastw][4], (lastw - i0) * 5)

SCEN = [("OPTIMISTIC", 0.0, True, 30), ("MID", 2.0, False, 30), ("PESSIMISTIC", 5.0, False, 15)]

# ---------- cohort A: exit_closes.jsonl (exact ts/price/qty, Jun 24+) ----------
exits = []
for l in open(CLOSES, encoding="utf-8"):
    r = json.loads(l)
    ts = int(datetime.fromisoformat(r["ts"]).timestamp() * 1000)
    exits.append({"cohort": "RECENT", "ts": r["ts"], "ts_ms": ts, "symbol": r["symbol"],
                  "side": r["side"], "exit_type": r["exit_type"], "px": r["exit_price"],
                  "qty": r["qty"], "notional": r["qty"] * r["exit_price"]})

# ---------- cohort B: trade_ledger.csv (ledger `timestamp` IS the exit time; verified vs exit_closes) ----------
for row in csv.DictReader(open(LEDGER, encoding="utf-8")):
    try:
        ets = float(row["timestamp"])
        px = float(row["exit_price"]); ep = float(row["entry_price"]); g = float(row["gross_pnl"])
    except (ValueError, KeyError):
        continue
    if px <= 0 or ep <= 0 or px == ep:
        continue
    d = 1 if row["side"] == "LONG" else -1
    qty = g / ((px - ep) * d)
    if qty <= 0 or row["symbol"] not in SYMS:
        continue
    iso = datetime.fromtimestamp(ets, tz=timezone.utc).isoformat()
    exits.append({"cohort": "LEDGER", "ts": iso, "ts_ms": int(ets * 1000), "symbol": row["symbol"],
                  "side": row["side"], "exit_type": row["exit_type"] or "?", "px": px,
                  "qty": qty, "notional": qty * px})

def era(iso):
    d = iso[:10]
    if d <= "2026-06-07": return "W1"
    if d <= "2026-06-23": return "MID"
    return "LATE"

results = []
for e in exits:
    oside = "SELL" if e["side"] == "LONG" else "BUY"
    row = dict(e)
    row["era"] = era(e["ts"])
    ok = True
    for name, buf, incl, tmo in SCEN:
        sim = sim_limit_exit(e["symbol"], e["ts_ms"], oside, e["px"], buf, incl, tmo)
        if sim is None:
            ok = False
            break
        filled, fpx, fb, waited = sim
        if filled:
            fee_delta = (TAKER - MAKER) * e["notional"]       # saved
            slip = 0.0
        else:
            d = 1 if e["side"] == "LONG" else -1
            slip = d * (fb - e["px"]) * e["qty"]              # <=0: missed-exit cost
            fee_delta = 0.0                                    # taker either way
        row[name] = {"filled": filled, "fee_saved": round(fee_delta, 4),
                     "slip": round(slip, 4), "net": round(fee_delta + slip, 4),
                     "waited_min": waited}
    if ok:
        results.append(row)

json.dump(results, open(OUT, "w"), indent=1)

def fmt(rows, name):
    out = {}
    for sc, _, _, _ in SCEN:
        n = len(rows)
        if not n: continue
        fills = [r for r in rows if r[sc]["filled"]]
        miss = [r for r in rows if not r[sc]["filled"]]
        net = sum(r[sc]["net"] for r in rows)
        fee = sum(r[sc]["fee_saved"] for r in rows)
        sl = sum(r[sc]["slip"] for r in rows)
        worst = min((r[sc]["slip"] for r in rows), default=0)
        out[sc] = (f"n={n} fill {len(fills)}/{n} ({len(fills)/n*100:.0f}%) | fee_saved ${fee:+.2f} "
                   f"slip ${sl:+.2f} NET ${net:+.2f} (${net/n:+.3f}/exit) | worst slip ${worst:+.2f}")
    print(f"\n== {name} ==")
    for k, v in out.items():
        print(f"  {k:11s} {v}")

for cohort in ("RECENT", "LEDGER"):
    sub = [r for r in results if r["cohort"] == cohort]
    fmt(sub, f"{cohort} ALL (n={len(sub)})")
    for et in sorted(set(r["exit_type"] for r in sub)):
        fmt([r for r in sub if r["exit_type"] == et], f"{cohort} exit_type={et}")
    for er in ("W1", "MID", "LATE"):
        fmt([r for r in sub if r["era"] == er], f"{cohort} era={er}")

# tail risk of misses (MID scenario)
print("\n== MISSED-FILL TAIL (MID scenario, both cohorts) ==")
miss = sorted([r for r in results if not r["MID"]["filled"]], key=lambda r: r["MID"]["slip"])
for r in miss[:12]:
    print(f"  {r['ts'][:16]} {r['symbol']:4s} {r['side']:5s} {r['exit_type']:15s} "
          f"slip ${r['MID']['slip']:+.2f} notional ${r['notional']:.0f} ({r['cohort']})")
