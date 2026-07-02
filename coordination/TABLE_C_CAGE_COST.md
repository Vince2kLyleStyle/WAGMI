# TABLE C — THE COST OF THE CAGE (counterfactual of the old gates)
Lane C, 2026-07-02. THE_STANDARD v1.4: denominators, era-splits, adversarial pass, week-1-artifact test.
Script: `bot/tools/research/lane_c_cage_cost.py` (read-only on bot data; re-runnable).

## VERDICT (the number)
**Gate-cage cost ≈ $0/month. The hypothesis "the old gates left money on the table" is KILLED — logged as a win.**
Every gate class with n≥15 blocked a signal stream that was **net-NEGATIVE after fees**. The cage was net-protective:
at the bot's realistic capacity (~2 extra trades/day, $687 median June notional), the confidence-floor cage was
*saving* ≈ **$194/mo** (60 × -0.471% × $687), and the broken volume_chop gate was saving ≈ **$71/mo** (2/day × -0.172% × $687).
The dechoke's value is NOT recovered alpha — it is throughput + honest learning data for a selector that must beat its input stream.

## Method
- Sources: `data/llm/counterfactual_resolved.jsonl` (41,797 resolved), `data/logs/signal_outcomes.jsonl` (54,367 evals),
  `data/manual/sniper_rejections.jsonl` (81,364), 5m exchange candles Jun 5–24 (BTC/ETH/SOL/HYPE; XRP excluded, no candles).
- **Dedup is load-bearing**: signals re-fire every ~50s. One opportunity = (symbol, side, 30-min bucket). 41,797 raw → 3,978 unique; 32,645 vc rejects → 1,869 unique.
- Forward-scoring: bracket sim SL 2% / TP 3% (system rr_tp1=1.5), SL-first tiebreak (conservative), 48h cap; plus 1h/4h/24h fixed-horizon returns.
- Fees: 0.10% round-trip of notional (measured, trades.csv n=102 median 0.098%). All "net" = gross − 0.10%.
- CIs: 2,000-resample bootstrap on dedup'd means. Fragility: drop single best outcome.

## A) Counterfactual-resolved gates (bracket outcomes already resolved vs price)
| Gate class | n_raw | n_dedup | TP1% | SL% | mean gross% | net% | 95% CI (gross) | verdict |
|---|---|---|---|---|---|---|---|---|
| confidence_floor (conf solo gate) | 30,029 | 2,321 | 10.5 | 40.5 | **-0.371** | -0.471 | [-0.443, -0.305] | **PROTECTIVE** (CI fully negative) |
| LLM skip ([MA], not a mech gate) | 3,910 | 917 | 12.6 | 37.8 | -0.077 | -0.177 | [-0.252, +0.081] | neutral; LLM skips were right on avg |
| graduated_rule_veto (overridden) | 5,974 | 596 | 10.2 | 24.0 | +0.046 | -0.054 | [-0.123, +0.213] | ~zero; override cost nothing |
| trend_adj_floor | 1,534 | 121 | 7.4 | 33.9 | -0.797 | -0.897 | [-1.247, -0.357] | protective, but W27 flips +0.81 (n=27) — era-fragile |
| graduated_rule_veto (enforced) | 302 | **17** | 17.6 | 52.9 | +0.668 | +0.568 | [-2.583, +4.505] | **NO CLAIM** — n<15 effective, median -3.3, fragility flips to -0.39 |

Era-split, confidence_floor: W25 -0.28 (n=438) | W26 -0.41 (n=1,280) | W27 -0.35 (n=603). Survives every week and the week-1 test.
By threshold (dedup): floor 58: -0.19% (n=337) | 62: -0.20% (n=341) | 66: **-0.58%** (n=1,108, CI [-0.70,-0.47]) | 71: -0.16% (n=534).
By blocked-signal confidence: even the conf 60-69 band that got floored would have lost -0.29% gross (n=320). No high-conf gold was being caged.

## B) volume_chop — the broken gate (0.0 input, 32,645/32,645 soft-rejects)
Confirmed: every reject fired on value=0.0 vs threshold 0.5 — a dead input, i.e. an effectively **random** filter.
Forward-scored where volume_chop was the SOLE rejector (would have reached the LLM with honest input): 14,471 raw → 1,869 unique → 1,097 scored in candle window.

| Stream | n | WR | bracket gross% | net% | 95% CI (gross) |
|---|---|---|---|---|---|
| vc-rejected | 1,097 | 39.7% | -0.072 | -0.172 | [-0.211, +0.066] |
| **PASSED signals (baseline, same method)** | 768 | 40.2% | -0.043 | -0.143 | [-0.215, +0.119] |

**Killer comparison: rejected ≈ passed.** The broken gate blocked at random from a stream statistically identical to what passed —
zero adverse selection, therefore ~zero alpha cost. 24h fixed-horizon: rejected -0.458% vs passed -0.414% — the whole June stream was net-negative.
Week-split (rejected, bracket): W23 +0.39 (n=246) | W24 -0.10 (n=297) | W25 -0.19 (n=495) | W26 -0.92 (n=59). Week-1-artifact test: the only
positive week is week 1; removing it makes the stream MORE negative. Symbols: BTC -0.13 | ETH -0.48 | HYPE +0.13 | SOL -0.01 — no hidden winner.

## C) Router gates (sniper_rejections, conf≥60 classes, forward-scored)
| Reason class | raw | dedup | scored | WR | net% | 95% CI (gross) |
|---|---|---|---|---|---|---|
| low_confidence floor (conf≥60) | 13,275 | 792 | 176 | 37.5% | -0.165 | [-0.412, +0.300] |
| low_consensus_1 (solo, conf≥60, mean conf 87.8) | 2,172 | 237 | 76 | 34.2% | -0.355 | [-0.750, +0.279] |
| quality_floor_proven_solo | 5,614 | 776 | 375 | 34.9% | -0.362 | [-0.467, -0.059] |
| daily_limit (conf≥60) | 5,230 | 274 | 61 | 36.1% | -0.525 | [-0.929, +0.118] |

The dramatic-looking "solo signals at conf 88 blocked for consensus" class: would have lost -0.36% net per trade. Not a cage — a fence around a hole.

## Dollar table (per month, $687 June median notional, capacity-honest at 2 extra trades/day)
| Gate | net EV/blocked opp | cage cost/mo @2/day | CI on cost |
|---|---|---|---|
| confidence_floor | -$3.24 | **-$194 (SAVED)** | [-$224, -$168] |
| volume_chop (broken) | -$1.18 | **-$71 (SAVED)** | [-$128, +$14] |
| trend_adj_floor | -$6.16 | -$370 (saved; era-fragile) | [-$555, -$188] |
| graduated_rule_veto (enforced) | +$3.90 | +$234?? — **REJECTED, n=17**, CI [-$1,106, +$1,895] | fails small-n humility |
Unconstrained ("take all 63 vc opps/day") is a fantasy number and still NEGATIVE: -$2,230/mo avoided losses.

## Adversarial self-check (what could make this wrong)
1. **"The LLM would have cherry-picked winners from the blocked stream."** Bounded: LLM-passed baseline scored -0.043% vs blocked -0.072% —
   measured selection edge ≈ +0.03-0.3%/trade, not enough to turn a -0.17 to -0.47% stream positive. Actual June trades ≈ breakeven (median -$0.99, n=104) — consistent.
2. **Bracket geometry choice.** SL2/TP3 is arbitrary; checked 1h/4h/24h fixed horizons — all negative for blocked streams. Direction-robust.
3. **Counterfactual resolver bias.** Its own geometry (median SL 2.17%, TP1 4.09%) differs from mine, yet both methods agree in sign everywhere they overlap.
4. **Survivorship in "resolved."** Unresolved records excluded; no reason resolution correlates with outcome sign (48-bar cap resolves ~all).
5. **June was a bad month (era risk).** True and acknowledged: this prices the cage IN JUNE. In a month where the raw stream is positive, gates would cost money.
   That is exactly why the dechoke is still right — but its value must be measured as *selector edge × throughput*, not as "freed alpha."

## What this means for the unchoke (the actionable line)
The unchoke is worth **$0/mo in recovered June alpha** and that is the honest number. Its real value: (1) honest volume_chop input restores
~63 candidate-opportunities/day to the LLM selector; (2) every dollar the unchoke earns must come from the LLM beating its input stream —
so the KPI to watch is **selection edge (taken vs offered)**, currently ≈ +0.03% (noise). If post-dechoke selection edge doesn't clear fees (0.10%),
the unchoke buys learning data, not money. Killed-hypothesis win: stop looking for caged alpha in June-era gates; it isn't there.
