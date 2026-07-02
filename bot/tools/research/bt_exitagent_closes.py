# BT_EXITAGENT_CLOSES: replay LLM exit-agent FULL-CLOSE decisions under candidate policy bars.
# Read-only on bot code/data; writes only bt_* artifacts here.
#
# Policies (predicate evaluated per call; agent full-close applies at FIRST call passing the bar):
#   ACTUAL        : what really happened (baseline, delta $0)
#   CURRENT_RULE  : dead-capital/thesis-invalid keyword AND position not in profit AND conf>=0.60
#                   (= exit_engine.py post-2026-06-30 gate; conf floor never binds: all confs are 0.75/0.85)
#   DEAD_CONF80   : same but conf>=0.80 (== the 0.85-conf subset)
#   DEAD_ANY_SIDE : dead-capital keyword, winners allowed, conf>=0.60
#   ALL_CONF60    : any close call with conf>=0.60 (pre-block June behavior)
#   DISABLED      : full-close never applies (tightens/partials untouched — they are separate actions)
#
# Counterfactual accounting per position:
#   policy closes EARLIER than actual exit -> exact realized delta = qty*d*(px_policy_close - px_actual_exit)
#     using the position's real final exit from trade_ledger.csv (no horizon truncation).
#   policy REMOVES an applied close -> RQ9 convention: simulate recorded SL/TP2 forward on 15m candles,
#     24h horizon; delta = -(close_value$) of that call.
#   Fees ~neutral (one taker exit either way); partials/tightens on blocked positions mean qty is
#   approximate for early-close deltas (noted, not modeled).
import json, os, re, csv, time, urllib.request
from datetime import datetime, timezone
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEC = os.path.join(ROOT, "data", "logs", "exit_decisions.jsonl")
LEDGER = os.path.join(ROOT, "data", "trade_ledger.csv")
CACHE = os.path.join(HERE, "bt_candles_15m.json")
OUT = os.path.join(HERE, "bt_exitagent_closes_results.json")

SYMS = ["BTC", "ETH", "SOL", "XRP", "HYPE"]
START_MS = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)

def fetch_candles():
    if os.path.exists(CACHE):
        d = json.load(open(CACHE))
        if time.time() * 1000 - max(c[0] for c in d["BTC"]) < 3 * 3600 * 1000:
            return d
    out = {}
    end = int(time.time() * 1000)
    for s in SYMS:
        req = json.dumps({"type": "candleSnapshot", "req": {"coin": s, "interval": "15m",
                          "startTime": START_MS, "endTime": end}}).encode()
        r = urllib.request.Request("https://api.hyperliquid.xyz/info", data=req,
                                   headers={"Content-Type": "application/json"})
        rows = json.loads(urllib.request.urlopen(r, timeout=30).read())
        out[s] = [[int(c["t"]), float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])] for c in rows]
        print(s, len(out[s]), "15m candles ->", datetime.fromtimestamp(out[s][-1][0]/1000, tz=timezone.utc))
        time.sleep(0.3)
    json.dump(out, open(CACHE, "w"))
    return out

CANDLES = fetch_candles()
IDX = {s: {c[0]: i for i, c in enumerate(CANDLES[s])} for s in SYMS}
C15 = 15 * 60 * 1000

def px_at(sym, ts_ms):
    i = IDX[sym].get(ts_ms - ts_ms % C15)
    return CANDLES[sym][i][4] if i is not None else None

def sim_mech(sym, ts_ms, d, sl, tp2, horizon_h=24):
    i0 = IDX[sym].get(ts_ms - ts_ms % C15)
    if i0 is None:
        return None, False
    arr = CANDLES[sym]
    n = int(horizon_h * 4)
    for i in range(i0 + 1, min(i0 + 1 + n, len(arr))):
        t, o, h, l, c = arr[i]
        if d > 0:
            if sl and l <= sl: return sl, True
            if tp2 and h >= tp2: return tp2, True
        else:
            if sl and h >= sl: return sl, True
            if tp2 and l <= tp2: return tp2, True
    last = min(i0 + n, len(arr) - 1)
    return arr[last][4], (i0 + n) <= len(arr) - 1

DEAD_KW = ("dead capital", "no-progress", "no progress", "no progres",
           "thesis invalidated", "thesis invalid", "invalidated", "toxic")

def era(ts):
    d = ts[:10]
    if d <= "2026-06-10": return "E1_jun01-10"
    if d <= "2026-06-22": return "E2_jun16-22"
    return "E3_jun23-jul02"

# ---- load close calls, group into position episodes ----
recs = [json.loads(l) for l in open(DEC, encoding="utf-8")]
calls = []
for r in recs:
    if not (r.get("reason") or "").startswith("[LLM-EXIT]") or r["exit_action"] != "close":
        continue
    ts_ms = int(datetime.fromisoformat(r["ts"]).timestamp() * 1000)
    sym, side = r["symbol"], r["position_side"]
    p0 = px_at(sym, ts_ms)
    if p0 is None:
        continue
    d = 1 if side == "LONG" else -1
    entry = r.get("position_entry")
    calls.append({"ts": r["ts"], "ts_ms": ts_ms, "sym": sym, "side": side, "d": d,
                  "entry": entry, "p0": p0, "sl": r.get("position_sl"), "tp2": r.get("position_tp2"),
                  "conf": r.get("exit_confidence") or 0, "applied": r["applied"],
                  "reason_l": (r.get("reason") or "").lower(),
                  "profitable": entry is not None and d * (p0 - entry) > 0,
                  "pos_key": f"{sym}|{side}|{entry}"})

episodes = defaultdict(list)
for c in sorted(calls, key=lambda x: x["ts"]):
    episodes[c["pos_key"]].append(c)

# ---- ledger: actual final exit per position ----
ledger = []
for row in csv.DictReader(open(LEDGER, encoding="utf-8")):
    try:
        ledger.append({"ts": float(row["timestamp"]), "sym": row["symbol"], "side": row["side"],
                       "entry": float(row["entry_price"]), "exit": float(row["exit_price"]),
                       "gross": float(row["gross_pnl"]), "exit_type": row["exit_type"]})
    except (ValueError, KeyError):
        continue

def match_ledger(sym, side, entry, first_call_ms):
    """ledger row = the real final exit of this position (exit ts at/after first close call)."""
    cand = [L for L in ledger if L["sym"] == sym and L["side"] == side and entry
            and abs(L["entry"] - entry) / entry < 1e-4
            and L["ts"] * 1000 >= first_call_ms - 60_000]
    if not cand:
        return None
    L = min(cand, key=lambda x: x["ts"])
    d = 1 if side == "LONG" else -1
    qty = L["gross"] / ((L["exit"] - L["entry"]) * d) if L["exit"] != L["entry"] else None
    return {**L, "qty": qty if qty and qty > 0 else None}

def dead(c): return any(k in c["reason_l"] for k in DEAD_KW)

POLICIES = {
    "CURRENT_RULE": lambda c: dead(c) and not c["profitable"] and c["conf"] >= 0.60,
    "DEAD_CONF80": lambda c: dead(c) and not c["profitable"] and c["conf"] >= 0.80,
    "DEAD_ANY_SIDE": lambda c: dead(c) and c["conf"] >= 0.60,
    "ALL_CONF60": lambda c: c["conf"] >= 0.60,
    "DISABLED": lambda c: False,
}

rows = []
for pk, cs in episodes.items():
    first = cs[0]
    applied = next((c for c in cs if c["applied"]), None)
    L = match_ledger(first["sym"], first["side"], first["entry"], first["ts_ms"])
    qty = L["qty"] if L else None
    row = {"pos_key": pk, "era": era(first["ts"]), "first_ts": first["ts"], "n_calls": len(cs),
           "applied": applied is not None, "qty": qty,
           "actual_exit_px": L["exit"] if L else None, "actual_exit_type": L["exit_type"] if L else None,
           "ledger_matched": L is not None}
    for name, pred in POLICIES.items():
        pol = next((c for c in cs if pred(c)), None)
        delta, method = 0.0, "same"
        if pol and applied and pol["ts_ms"] == applied["ts_ms"]:
            delta, method = 0.0, "same"
        elif pol is not None:
            # policy closes at pol; actual exit = applied close (== ledger row) or later mech exit (ledger)
            ax = applied["p0"] if (applied and not L) else (L["exit"] if L else None)
            if L and applied:
                ax = L["exit"]  # applied close is the ledger exit itself
            if ax is not None and qty:
                delta = qty * pol["d"] * (pol["p0"] - ax)
                method = "ledger_realized"
            else:
                ep, ok = sim_mech(pol["sym"], pol["ts_ms"], pol["d"], pol["sl"], pol["tp2"])
                if ep is not None and qty:
                    delta = qty * pol["d"] * (pol["p0"] - ep)  # close now vs hold-24h-mech
                    method = "sim24h"
                else:
                    method = "unscored"
        elif applied is not None:
            # policy removes the applied close -> hold under mech 24h (RQ9 convention)
            ep, ok = sim_mech(applied["sym"], applied["ts_ms"], applied["d"], applied["sl"], applied["tp2"])
            if ep is not None and qty:
                delta = -(qty * applied["d"] * (applied["p0"] - ep))
                method = "sim24h_removed"
            else:
                method = "unscored"
        row[name] = {"delta": round(delta, 2), "method": method,
                     "pol_ts": pol["ts"] if pol else None}
    rows.append(row)

json.dump(rows, open(OUT, "w"), indent=1)

ERAS = ("E1_jun01-10", "E2_jun16-22", "E3_jun23-jul02")
print(f"\npositions (close episodes): {len(rows)}  | applied closes: {sum(1 for r in rows if r['applied'])} "
      f"| ledger-matched: {sum(1 for r in rows if r['ledger_matched'])} "
      f"| qty-matched: {sum(1 for r in rows if r['qty'])}")

print(f"\n{'policy':14s} {'era':16s} {'n_chg':>5s} {'delta$':>9s}  (delta vs ACTUAL; + = policy better)")
for name in POLICIES:
    tot = [r[name]["delta"] for r in rows if r[name]["method"] not in ("same", "unscored")]
    uns = sum(1 for r in rows if r[name]["method"] == "unscored")
    line = sum(r[name]["delta"] for r in rows)
    print(f"{name:14s} {'ALL':16s} {len(tot):5d} {line:+9.2f}   unscored={uns}")
    for e in ERAS:
        er = [r for r in rows if r["era"] == e]
        chg = [r[name]["delta"] for r in er if r[name]["method"] not in ("same", "unscored")]
        print(f"{'':14s} {e:16s} {len(chg):5d} {sum(r[name]['delta'] for r in er):+9.2f}")
    # fragility: without single best / worst changed position
    if tot:
        print(f"{'':14s} fragility: w/o best {line - max(tot):+9.2f} | w/o worst {line - min(tot):+9.2f}")

print("\n== biggest per-position deltas (any policy) ==")
seen = []
for r in rows:
    for name in POLICIES:
        if abs(r[name]["delta"]) > 20:
            seen.append((r[name]["delta"], name, r))
for dl, name, r in sorted(seen)[:8] + sorted(seen)[-8:]:
    print(f"  {dl:+8.2f} {name:14s} {r['pos_key'][:34]:34s} {r['first_ts'][:16]} "
          f"applied={r['applied']} actual={r['actual_exit_type']} m={r[name]['method']}")
