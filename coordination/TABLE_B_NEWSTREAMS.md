# TABLE B — NEW-STREAM SIGNAL SCAN (market_depth_history.jsonl)

Lane B, 2026-07-02. Standard: THE_STANDARD.md v1.4.
Script (re-runnable): `bot/tools/research/lane_b_newstream_ic.py`

## HONESTY BLOCK — read before believing anything below
- **n is tiny.** 480 rows = 5 symbols x 96 samples over **23 hours** (2026-07-01T23:06Z → 2026-07-02T22:22Z, ~15min cadence). ONE market day. Zero era-split possible.
- **Overlapping windows.** Forward 1h returns at 15min sampling overlap 4x; 4h overlap 16x. Naive p-values below are **inflated roughly 2-4x in effective-n terms**; treat every p as decorative.
- **ls_account_ratio is near-constant intraday** (lag-1 autocorrelation 0.89-0.98; only 20-47 distinct values per symbol in 96 samples). Its effective independent n is **~3-5 per symbol**. Its big IC is one day's macro coincidence until proven otherwise.
- Therefore: **this is a PILOT PROTOCOL, not a verdict.** Nothing here graduates (Standard §1: nothing on n<15 independent obs). The deliverable is the shortlist + the exact validation test per candidate.

## Method
Per symbol: feature at t (Spearman-ranked within symbol) vs forward log mid-return over 1h/4h (mid from the same L2 snapshot stream — self-consistent, no candle-join error). Pooled IC = Spearman on within-symbol rank z-scores. Adversarial passes run: (a) time-index-vs-return trend-artifact check (4h day-drift exists: XRP time-IC -0.44, BTC -0.28 — day trended), (b) lag-1 autocorrelation / effective-n, (c) fragility: recompute after trimming top-10% |moves|.

## RANKED SHORTLIST (pilot IC, 23h, overlapping — suspicion mandatory)

| rank | candidate | horizon | pooled IC | trimmed IC | sign-agree | naive p | verdict |
|---|---|---|---|---|---|---|---|
| 1 | **spread_bps** (wider spread → positive fwd ret) | 4h | **+0.361** | **+0.392** | 5/5 | <0.001 | Best pilot signal. Noise-like feature (lag1-AC ~0.0) so NOT a trend artifact; survives trimming. Story: spread widens into stress → mean-revert bounce. |
| 2 | spread_bps | 1h | +0.140 | +0.144 | 5/5 | 0.002 | Same signal, weaker at 1h. |
| 3 | **ls_account_ratio** (crowded-long → UP, i.e. momentum not contrarian) | 1h | +0.328 | +0.271 | 5/5 | <0.001 | HUGE caveat: AC 0.98, effective n≈3-5/sym. Direction is the OPPOSITE of the contrarian folk-prior — that alone makes it worth tracking. 4h: +0.280 but HYPE flips (-0.27). |
| 4 | **basis_bps** (premium → fade; discount → long) | 4h | -0.157 | -0.175 | 4/5 (BTC flips) | 0.002 | Classic carry/contrarian shape; moderate persistence (AC 0.3-0.5). |
| 5 | d_ls_account_ratio (rising long crowd → UP) | 4h | +0.185 | — | 5/5 | <0.001 | Delta version fixes the staleness objection; 1h +0.100 also 4/5. |
| 6 | taker_bs_ratio (taker buy flow → momentum) | 1h+4h | +0.090/+0.096 | +0.063/+0.084 | 4/5 | ~0.05 | Weak, consistent sign, weakens under trimming. Keep on watch, low priority. |
| 7 | depth_total_1pct (thick book → up) | 4h | +0.196 | — | 5/5 | <0.001 | Likely confounded with the day's calm-drift; re-test only. |

## KILLED (pilot) — logged as wins per Standard §1
- **Book imbalance (all bands 0.1/0.5/1pct, levels AND deltas): DEAD.** Pooled IC +0.00 to +0.05, 3/5 sign agreement, p 0.29-0.97 at both horizons. Imbalance predicts seconds-to-minutes, not 1-4h; at 15min cadence it's noise. Do not build on it at this cadence.
- **buy_ratio_10t: DEAD + measurement broken.** IC -0.06/+0.02. The collector samples only the LAST 10 TRADES (BTC row: buy_vol 0.00171 — dust). This field cannot work as collected. → collector fix below.
- **funding_rate (level): DEAD at these horizons.** IC -0.03/+0.01, per-symbol signs wild (BTC +0.64, SOL -0.50) = pure day-noise.
- **d_basis, d_taker, d_imbalance:** all |IC|<0.04 except d_ls (above).

## VALIDATION PROTOCOL — the exact test each candidate must pass
**Auto-run:** `python bot/tools/research/lane_b_newstream_ic.py` weekly (attach to the 3h learning loop's weekly slot or the meta-audit cadence); it re-prints the full table from the growing jsonl. Collector must stay watchdogged (H61) — a gap week resets nothing but delays graduation.

**Sample-size math (the honest denominator):** to detect IC=0.10 at two-sided alpha=0.05 with 80% power needs **n≈780 independent obs**. Non-overlapping 1h windows = 24/day/symbol ×5 = 120/day → **~7 days**. Non-overlapping 4h = 30/day pooled → **~26 days**. So: 1h candidates get a real verdict in ~1 week; 4h candidates need ~4 weeks.

**Graduation gate (per candidate, per horizon) — ALL required:**
1. n ≥ 780 NON-OVERLAPPING samples (subsample to stride=horizon before computing IC; the pilot script's overlap shortcut is banned at verdict time).
2. |pooled IC| ≥ 0.05 with block-bootstrap (1-day blocks) p < 0.01.
3. Sign agreement ≥ 4/5 symbols AND survives era-split (first half vs second half of the accrual window, same sign both halves).
4. Fragility: survives trimming top-10% |moves| with same sign and ≥60% of magnitude.
5. Week-1-artifact test: exclude the pilot day (Jul 1-2) entirely from the verdict sample — if the effect only lives in the pilot day, it dies.

**Candidate-specific thresholds to pre-register NOW (no post-hoc fitting):**
- spread_bps: signal = spread_bps > rolling 7-day 80th pct per symbol → expect positive 4h fwd ret vs base rate. Also test as EXIT-TIMING filter (don't market-exit into a blown spread).
- ls_account_ratio: use the DELTA (d_ls over 4h) not the level; signal = d_ls in top/bottom quintile. Level is too stale to be tradeable.
- basis_bps: signal = basis_bps < -5 (discount) long-tilt / > +5 fade-tilt, 4h horizon.
- taker_bs_ratio: quintile spread test only; kill if trimmed IC < 0.05 at n≥780.

**If graduated:** enters the LLM prompt as RAW data with n/era/provenance per §3b (no naked "book says long"), and any veto/tilt built on it follows §2b shadow-mode-first.

## Collector fixes recommended (measurement first, per overdrive mandate)
1. **trades window:** 10 trades is unusable. Collect taker buy/sell VOLUME over the full 15min interval (HL API `userFills`-style or candle taker fields), not last-10-trades. Until fixed, `trades.*` is muted (Invariant 7).
2. **ls_account_ratio staleness:** log the OKX timestamp of the ratio, not just poll time — dedupe repeated values so effective-n is honest.
3. One row (of 480) missing `trades` — harmless, but nulls should be explicit.
