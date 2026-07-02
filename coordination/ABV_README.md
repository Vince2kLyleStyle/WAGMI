# ABV — V-TRUE A/B verdict tooling (prestaged 2026-07-02)

**Tool:** `bot/tools/research/ab_verdict.py` — computes the final
brain-vs-cage verdict in minutes once the V-TRUE windows (C1t..C6t) land.
Pre-registered metrics: THOUGHT_JOURNAL 2026-07-02 ~23:30Z directive +
FULL_PIPE_BUILD_MAP §4. Runs on whatever subset exists; missing windows are
listed, never fatal.

## Run it

```bash
python bot/tools/research/ab_verdict.py              # markdown verdict (paste into REPLAY_AB_VERDICT.md)
python bot/tools/research/ab_verdict.py --json       # machine output (verdict block + all per-window stats)
python bot/tools/research/ab_verdict.py --windows C1,C3 --treat-suffix t
python bot/tools/research/ab_verdict.py --no-fetch   # cache-only, no HL API calls
python bot/tools/research/ab_verdict.py --fee-bps 10.4
```

When the last V-TRUE window finishes: `python bot/tools/research/ab_verdict.py`
and paste the output into `coordination/REPLAY_AB_VERDICT.md`. Done.

## What it computes

1. **Discrimination spread** (per window + pooled, per architecture):
   forward return of brain-APPROVED (go/proceed) minus brain-REJECTED
   (flat/skip) entry-decisions at +6h/+12h/+24h from decision sim-time.
   The cage scores 0 by construction — a wall doesn't choose.
2. **Positive-subset test:** approved-subset net expectancy after fees
   (default 10.4 bps RT taker; each run's `fee_model` from
   `llm_summary.json` is surfaced, and any maker fields are called out).
3. **Aggregates:** closes / WR / net PnL per window per arm from
   `replay_trades.csv` (cross-checked against `REPLAY_RUN_C*.md`), plus the
   **C3-shorts three-way verdict**: (a) short signals qualifying as entry
   events, (b) shorts reaching the coordinator, (c) shorts approved/entered
   + PnL → FILTER-STARVED vs BRAIN-DECLINED vs BRAIN SHORTED.
4. **Verdict block:** BRAIN BEATS CAGE iff pooled spread > 0 (honest n
   stated) AND approved subset net-positive in ≥ 2 regimes (union of
   realized-PnL basis and journal forward-return basis, both shown).

## Data sources & fidelity

- **V-TRUE arm (C1t..C6t):** journals are exact — post-ddd2fdf
  `replay_llm_journal.jsonl` entries carry `signal_side` / `signal_entry` /
  `signal_confidence` / `sim_ts`. Scoring is native.
- **Baseline arm (C1..C6):** journals pre-date the patch and LACK signal
  fields. The rejected side is **reconstructed at lower fidelity**: each
  journaled decision is joined to the nearest-preceding `SIGNAL_GENERATED`
  event in the run's `trade_events.jsonl` (side/price), and sim-time is
  recovered by matching the signal entry price against the run's own 1h
  candle-cache closes (in-window, per-symbol monotonic). Ambiguous/unmatched
  decisions are counted as "unresolved" and excluded — the count is printed.
  Burst signals inside one candle can alias; treat baseline spread as
  approximate. (Smoke run 2026-07-02: 21/229 unresolved.)
- **Forward candles:** run's sandbox cache first; where the cache ends
  before sim_ts+24h, 1h candles are fetched from Hyperliquid
  `candleSnapshot` and cached under `bot/tools/research/candle_cache/`
  (runtime artifact, not committed). 2025 baseline windows were
  Coinbase-seeded, so HL continuation is a second, smaller caveat —
  sources are labeled per lookup.

## Baseline-side smoke numbers (old pipe, run 2026-07-02, C6 in progress)

Pooled discrimination spread: **6h +0.195% | 12h +0.126% | 24h +0.548%**
(n = 13 approved / ~197 rejected — reconstructed fidelity).
Pooled approved-subset net fwd after 10.4 bps RT: **6h +0.28% | 12h +0.49% |
24h +1.43%** (n=13). Per window @24h: C1 spread +2.33% (net +3.09%, n=8) |
C2 −0.75% (net −1.11%, n=2) | C4 −1.02% (net −0.83%, n=2) | C3/C5 no
approvals (0 closes). Realized net PnL: C1 +$16.18, C2 +$0.05, C4 +$0.44.
C3 shorts three-way: raw 317 → (a) 73 qualified → (b) 35 reached
coordinator, 0 approved → **BRAIN DECLINED** (not filter-starved).

Read: the old pipe's positive pooled spread is carried entirely by C1
(trend-up); approved subsets were net-negative in C2/C4. Only C1 qualifies
as a net-positive regime → the baseline itself would FAIL the ≥2-regime
criterion on current data. That is the bar V-TRUE has to clear.
