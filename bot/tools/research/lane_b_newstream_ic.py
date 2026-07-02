"""LANE B pilot: IC scan of market_depth_history.jsonl fields vs forward mid returns.

PILOT ONLY — n is tiny (~1 day). Naive p-values are inflated by overlapping
forward windows (15min cadence, 1h/4h horizons). This script is the exact
test to re-run as data accrues (see TABLE_B_NEWSTREAMS.md protocol).

Usage: python bot/tools/research/lane_b_newstream_ic.py [path_to_jsonl]
"""
import json, sys, math
from datetime import datetime, timezone
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "bot/data/market_depth_history.jsonl"

def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

def spearman(x, y):
    n = len(x)
    if n < 5:
        return None, None, n
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None, None, n
    rho = num / (dx * dy)
    # naive t-approx p (two-sided) — INFLATED under overlapping windows
    if abs(rho) >= 1:
        return rho, 0.0, n
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    # normal approx of t for simplicity
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return rho, p, n

rows = [json.loads(l) for l in open(PATH)]
by_sym = defaultdict(list)
for r in rows:
    by_sym[r["symbol"]].append(r)
for s in by_sym:
    by_sym[s].sort(key=lambda r: r["ts"])

# feature extractors: name -> fn(row) -> float|None
def g(r, *ks):
    d = r
    for k in ks:
        d = d.get(k) if isinstance(d, dict) else None
        if d is None:
            return None
    return d

FEATURES = {
    "imbalance_0_1pct": lambda r: g(r, "l2", "imbalance_0_1pct"),
    "imbalance_0_5pct": lambda r: g(r, "l2", "imbalance_0_5pct"),
    "imbalance_1pct":   lambda r: g(r, "l2", "imbalance_1pct"),
    "spread_bps":       lambda r: g(r, "l2", "spread_bps"),
    "depth_total_1pct": lambda r: (g(r, "l2", "bid_depth_1pct") or 0) + (g(r, "l2", "ask_depth_1pct") or 0),
    "buy_ratio_10t":    lambda r: g(r, "trades", "buy_ratio"),
    "funding_rate":     lambda r: g(r, "futures_ctx", "funding_rate"),
    "basis_bps":        lambda r: g(r, "futures_ctx", "basis_bps"),
    "ls_account_ratio": lambda r: g(r, "futures_ctx", "long_short_account_ratio"),
    "taker_bs_ratio":   lambda r: g(r, "futures_ctx", "taker_buy_sell_ratio"),
}
DELTA_FEATURES = ["imbalance_0_5pct", "basis_bps", "ls_account_ratio", "taker_bs_ratio"]

HORIZONS = {"1h": 3600, "4h": 4 * 3600}

def build(sym_rows, horizon_s):
    """Return dict feat -> (xs, ys) using mid-price forward return."""
    ts = [parse_ts(r["ts"]) for r in sym_rows]
    mids = [g(r, "l2", "mid") for r in sym_rows]
    out = defaultdict(lambda: ([], []))
    prev_vals = {}
    for i, r in enumerate(sym_rows):
        # forward index: first sample >= t + horizon
        j = None
        for k in range(i + 1, len(sym_rows)):
            if (ts[k] - ts[i]).total_seconds() >= horizon_s:
                j = k
                break
        if j is None or mids[i] is None or mids[j] is None:
            # still update prev for deltas
            for f in DELTA_FEATURES:
                v = FEATURES[f](r)
                if v is not None:
                    prev_vals[f] = v
            continue
        fwd = math.log(mids[j] / mids[i])
        for name, fn in FEATURES.items():
            v = fn(r)
            if v is not None:
                out[name][0].append(v)
                out[name][1].append(fwd)
        for f in DELTA_FEATURES:
            v = FEATURES[f](r)
            if v is not None:
                if f in prev_vals:
                    out["d_" + f][0].append(v - prev_vals[f])
                    out["d_" + f][1].append(fwd)
                prev_vals[f] = v
    return out

results = []  # (feature, horizon, per-sym ICs, pooled IC)
all_feats = list(FEATURES) + ["d_" + f for f in DELTA_FEATURES]
for hname, hsec in HORIZONS.items():
    per_sym_data = {s: build(by_sym[s], hsec) for s in by_sym}
    for feat in all_feats:
        sym_ics = {}
        pooled_x, pooled_y = [], []
        for s, data in per_sym_data.items():
            xs, ys = data.get(feat, ([], []))
            rho, p, n = spearman(xs, ys)
            if rho is not None:
                sym_ics[s] = (rho, n)
                # pool via within-symbol ranks (z-score of ranks) to avoid scale mixing
                nn = len(xs)
                def zr(v):
                    order = sorted(range(nn), key=lambda i: v[i])
                    rk = [0] * nn
                    for pos, idx in enumerate(order):
                        rk[idx] = pos
                    return [(k - (nn - 1) / 2) / max(1, nn) for k in rk]
                pooled_x += zr(xs)
                pooled_y += zr(ys)
        prho, pp, pn = spearman(pooled_x, pooled_y)
        results.append((feat, hname, sym_ics, prho, pp, pn))

print(f"{'feature':<20}{'hz':<4}{'pooledIC':>9}{'naive_p':>9}{'n':>5}  sign-agree  per-symbol IC")
for feat, hname, sym_ics, prho, pp, pn in sorted(results, key=lambda r: -abs(r[3] or 0)):
    if prho is None:
        continue
    signs = [1 if v[0] > 0 else -1 for v in sym_ics.values()]
    agree = max(signs.count(1), signs.count(-1))
    persym = " ".join(f"{s}:{v[0]:+.2f}" for s, v in sorted(sym_ics.items()))
    print(f"{feat:<20}{hname:<4}{prho:>+9.3f}{pp:>9.3f}{pn:>5}  {agree}/5        {persym}")
