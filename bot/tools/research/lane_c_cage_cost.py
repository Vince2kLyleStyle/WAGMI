"""LANE C — Cost of the Cage. THE_STANDARD v1.4 compliant.

Quantifies what pre-dechoke gates left on the table (or saved):
  A) counterfactual_resolved.jsonl -> per-gate-class hypothetical PnL (dedup'd, CI, era-split)
  B) signal_outcomes.jsonl volume_chop soft-rejects (broken 0.0 input) forward-scored vs 5m candles
  C) symmetric baseline: passed signals scored the same way

Read-only on bot code/data. Output: printed tables consumed by coordination/TABLE_C_CAGE_COST.md.
"""
import json, csv, math, random, statistics, datetime, re
from collections import defaultdict, Counter

random.seed(42)
ROOT = r"C:\Users\vince\WAGMI\bot\data"
FEE_RT = 0.10  # median round-trip fee % of notional, measured from trades.csv (n=102, median 0.098)
NOTIONAL = 687.0  # median implied notional June trades (n=86)

def boot_ci(vals, n=2000):
    if len(vals) < 3: return (float('nan'), float('nan'))
    means = []
    for _ in range(n):
        s = [random.choice(vals) for _ in vals]
        means.append(sum(s)/len(s))
    means.sort()
    return means[int(0.025*n)], means[int(0.975*n)]

# ---------- A) counterfactual_resolved by gate class ----------
def gate_class(sr):
    if sr.startswith('confidence_floor'): return 'confidence_floor (conf solo gate)'
    if sr.startswith('trend_adj_floor'): return 'trend_adj_floor'
    if sr == 'graduated_rule_veto': return 'graduated_rule_veto (enforced)'
    if sr == 'graduated_rule_veto_overridden': return 'graduated_rule_veto (overridden->shadow)'
    if sr.startswith('[MA]'): return 'LLM skip (not a mech gate)'
    return 'other'

recs = []
with open(ROOT + r"\llm\counterfactual_resolved.jsonl", encoding='utf-8', errors='replace') as f:
    for line in f:
        try: r = json.loads(line)
        except Exception: continue
        if not r.get('resolved'): continue
        p = r.get('hypothetical_pnl_pct')
        if p is None: continue
        recs.append(r)

# dedup: one opportunity per (symbol, side, 30-min bucket)
def dt(r):
    return datetime.datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))
seen = set(); dedup = []
for r in sorted(recs, key=lambda x: x['created_at']):
    key = (r['symbol'], r['side'], int(dt(r).timestamp() // 1800))
    if key in seen: continue
    seen.add(key); dedup.append(r)

print(f"A) counterfactual_resolved: raw={len(recs)} dedup(sym,side,30min)={len(dedup)}")
print(f"{'gate class':45s} {'n_raw':>6} {'n_ded':>6} {'tp1%':>5} {'sl%':>5} {'mean%':>7} {'med%':>7} {'CI95':>18} {'net%':>7} {'frag':>7}")
by = defaultdict(list); byraw = Counter()
for r in recs: byraw[gate_class(r['skip_reason'])] += 1
for r in dedup: by[gate_class(r['skip_reason'])].append(r)
summary = {}
for g, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
    pn = [r['hypothetical_pnl_pct'] for r in rs]
    tp1 = sum(1 for r in rs if r.get('would_hit_tp1'))/len(rs)*100
    slr = sum(1 for r in rs if r.get('would_hit_sl'))/len(rs)*100
    lo, hi = boot_ci(pn)
    mean = statistics.mean(pn); med = statistics.median(pn)
    net = mean - FEE_RT
    # fragility: remove single best
    frag = statistics.mean(sorted(pn)[:-1]) - FEE_RT if len(pn) > 1 else float('nan')
    print(f"{g:45s} {byraw[g]:6d} {len(rs):6d} {tp1:5.1f} {slr:5.1f} {mean:+7.3f} {med:+7.3f} [{lo:+.3f},{hi:+.3f}] {net:+7.3f} {frag:+7.3f}")
    summary[g] = dict(n=len(rs), mean=mean, net=net, ci=(lo,hi))
    # era split by ISO week
    wk = defaultdict(list)
    for r in rs: wk[dt(r).strftime('%G-W%V')].append(r['hypothetical_pnl_pct'])
    parts = [f"{w}: n={len(v)} m={statistics.mean(v):+.2f}" for w, v in sorted(wk.items()) if len(v) >= 10]
    print("    era: " + " | ".join(parts))

# conf-floor by threshold, dedup'd (the 'conf>=60 solo gate class' detail)
print("\nA2) confidence_floor by threshold (dedup):")
byt = defaultdict(list)
for r in dedup:
    m = re.match(r'confidence_floor_(\d+)', r.get('skip_reason',''))
    if m: byt[int(m.group(1))].append(r['hypothetical_pnl_pct'])
for t, pn in sorted(byt.items()):
    if len(pn) < 15: continue
    lo, hi = boot_ci(pn)
    print(f"  floor={t}: n={len(pn)} mean={statistics.mean(pn):+.3f}% net={statistics.mean(pn)-FEE_RT:+.3f}% CI=[{lo:+.3f},{hi:+.3f}]")

# conf band of the blocked signal itself (was high-conf blocked?)
print("\nA3) blocked-signal conf band vs outcome (dedup, all conf/trend floors):")
bands = defaultdict(list)
for r in dedup:
    g = gate_class(r['skip_reason'])
    if 'floor' not in g: continue
    c = r.get('confidence') or 0
    band = f"{int(c//10)*10}-{int(c//10)*10+9}"
    bands[band].append(r['hypothetical_pnl_pct'])
for b, pn in sorted(bands.items()):
    if len(pn) < 30: continue
    print(f"  conf {b}: n={len(pn)} mean={statistics.mean(pn):+.3f}% net={statistics.mean(pn)-FEE_RT:+.3f}%")

# ---------- B) volume_chop forward-score ----------
def load_candles(sym):
    rows = []
    try:
        with open(ROOT + rf"\cache\{sym}_5m_merged.csv") as f:
            for r in csv.DictReader(f):
                ts = datetime.datetime.fromisoformat(r['time']).timestamp()
                rows.append((ts, float(r['open']), float(r['high']), float(r['low']), float(r['close'])))
    except FileNotFoundError:
        return None
    rows.sort()
    return rows

candles = {s: load_candles(s) for s in ('BTC','ETH','SOL','HYPE')}
candles = {k: v for k, v in candles.items() if v}

sigs = []
with open(ROOT + r"\logs\signal_outcomes.jsonl", encoding='utf-8', errors='replace') as f:
    for line in f:
        try: r = json.loads(line)
        except Exception: continue
        ann = {a['gate']: a['severity'] for a in r.get('annotations', [])}
        r['_vc_only_reject'] = (ann.get('volume_chop') == 'reject'
                                and all(v != 'reject' for g, v in ann.items() if g != 'volume_chop')
                                and not r.get('passed') and not r.get('hard_rej'))
        sigs.append(r)

vc_all = [s for s in sigs if s['_vc_only_reject']]
passed_all = [s for s in sigs if s.get('passed')]
# dedup 30-min buckets
def dsig(lst):
    seen = set(); out = []
    for s in sorted(lst, key=lambda x: x['ts']):
        k = (s['sym'], s['side'], int(s['ts']//1800))
        if k in seen: continue
        seen.add(k); out.append(s)
    return out
vc = dsig(vc_all); pas = dsig(passed_all)
print(f"\nB) volume_chop sole-rejector soft-rejects: raw={len(vc_all)} dedup={len(vc)}; passed raw={len(passed_all)} dedup={len(pas)}")

def idx_at(c, ts):
    # binary search first candle with ts >= signal ts
    lo, hi = 0, len(c)
    while lo < hi:
        mid = (lo+hi)//2
        if c[mid][0] < ts: lo = mid+1
        else: hi = mid
    return lo if lo < len(c) else None

def fwd_return(s, hours):
    c = candles.get(s['sym'])
    if not c: return None
    i = idx_at(c, s['ts'])
    if i is None or i == 0 or c[i][0] - s['ts'] > 900: return None  # need candle within 15min
    j = idx_at(c, s['ts'] + hours*3600)
    if j is None or c[j][0] - (s['ts']+hours*3600) > 1800: return None
    e, x = c[i][4], c[j][4]
    d = 1 if s['side'] in ('BUY','LONG') else -1
    return d * (x-e)/e * 100

def bracket(s, sl_pct=2.0, tp_pct=3.0, max_bars=576):  # RR1.5 (system rr_tp1), 48h cap
    c = candles.get(s['sym'])
    if not c: return None
    i = idx_at(c, s['ts'])
    if i is None or i == 0 or c[i][0] - s['ts'] > 900: return None
    e = c[i][4]
    d = 1 if s['side'] in ('BUY','LONG') else -1
    sl = e*(1 - d*sl_pct/100); tp = e*(1 + d*tp_pct/100)
    for k in range(i+1, min(i+1+max_bars, len(c))):
        _, o, h, l, cl = c[k]
        hit_sl = l <= sl if d == 1 else h >= sl
        hit_tp = h >= tp if d == 1 else l <= tp
        if hit_sl: return -sl_pct       # conservative: SL first when both
        if hit_tp: return +tp_pct
    if i+1 >= len(c): return None
    cl = c[min(i+max_bars, len(c)-1)][4]
    return d*(cl-e)/e*100

def score(lst, label):
    out = {}
    for h in (1, 4, 24):
        v = [x for x in (fwd_return(s, h) for s in lst) if x is not None]
        if len(v) >= 15:
            lo, hi = boot_ci(v)
            out[h] = (len(v), statistics.mean(v), lo, hi)
    b = [x for x in (bracket(s) for s in lst) if x is not None]
    print(f"  {label}:")
    for h, (n, m, lo, hi) in out.items():
        print(f"    fwd {h:>2}h: n={n} mean={m:+.3f}% net={m-FEE_RT:+.3f}% CI=[{lo:+.3f},{hi:+.3f}]")
    if len(b) >= 15:
        lo, hi = boot_ci(b)
        wr = sum(1 for x in b if x > 0)/len(b)*100
        print(f"    bracket SL2/TP3: n={len(b)} WR={wr:.1f}% mean={statistics.mean(b):+.3f}% net={statistics.mean(b)-FEE_RT:+.3f}% CI=[{lo:+.3f},{hi:+.3f}]")
    return out, b

vco, vcb = score(vc, "volume_chop-rejected (would have reached LLM w/ honest input)")
pao, pab = score(pas, "PASSED signals (symmetric baseline, same method)")

# era split for vc bracket (week-1 test)
wk = defaultdict(list)
for s in vc:
    r = bracket(s)
    if r is None: continue
    wk[datetime.datetime.fromtimestamp(s['ts'], datetime.timezone.utc).strftime('%G-W%V')].append(r)
print("  vc bracket by week: " + " | ".join(f"{w}: n={len(v)} m={statistics.mean(v):+.2f}%" for w, v in sorted(wk.items()) if len(v) >= 10))

# by symbol
bys = defaultdict(list)
for s in vc:
    r = bracket(s)
    if r is not None: bys[s['sym']].append(r)
print("  vc bracket by symbol: " + " | ".join(f"{k}: n={len(v)} m={statistics.mean(v):+.2f}%" for k, v in sorted(bys.items())))

# ---------- C) monthly dollar estimates ----------
print("\nC) Dollarization inputs:")
if vc:
    span_days = (vc[-1]['ts'] - vc[0]['ts'])/86400
    print(f"  vc unique opps: {len(vc)} over {span_days:.1f}d = {len(vc)/span_days:.1f}/day")
for g, s in summary.items():
    print(f"  {g}: n_dedup={s['n']} net_mean={s['net']:+.3f}%")
print(f"  fee_rt={FEE_RT}% notional=${NOTIONAL} (June median)")
