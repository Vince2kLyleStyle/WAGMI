# BT_MAKER_EXITS — post-only limit exits vs actual taker fills (2026-07-02)

**Standard:** THE_STANDARD v1.4 — denominators, era-splits, explicit assumptions, fragility.
**Script:** `bot/tools/research/bt_maker_exits.py` · **Artifacts:** `bt_maker_exits_results.json`, `bt_candles_5m.json`
**Data:** `bot/data/logs/exit_closes.jsonl` (69 exits, exact ts/px/qty, Jun 24 – Jul 2) + `trade_ledger.csv` (99 qty-derivable exits inside candle coverage; ledger `timestamp` verified = EXIT time, matches exit_closes to <0.1s). Price truth: HL 5m candles — **HL only serves ~5,000 recent 5m candles, so coverage starts Jun 15; W1/early-MID unreplayable** (moot: W1 already filled maker per RQ17).

## VERDICT: SHIP — but ONLY for non-urgent exit classes (LLM_EXIT_AGENT closes + resting TP orders). NO-SHIP for SL and TRAILING_STOP.

Maker-for-everything is a wash-to-negative once missed fills are priced honestly. Maker for agent closes wins under **all three** fill assumptions.

## Fee + fill model (explicit assumptions)
- HL base tier: taker 4.5 bps/side, maker 1.5 bps/side → **3.0 bps of notional saved per filled exit**. Entry side untouched (taker).
- Post-only limit placed at the actual exit price P at the actual exit timestamp; unfilled → cancel-and-cross taker at the close of the timeout window (missed-exit slippage = adverse move while waiting; by construction misses are always adverse for the exit side).
- Three fill assumptions, 5m candles:
  - **OPTIMISTIC** — touch-fill (candle extreme reaches P, exit candle included, 30-min window). Ignores queue position; upper bound.
  - **MID** — trade-through by 2 bps, next candle onward, 30-min window.
  - **PESSIMISTIC** — trade-through by 5 bps, next candle onward, 15-min window.
- Not modeled: queue position at exact touch, intra-candle sequencing (mitigated by next-candle start in MID/PESS), maker-tier rebates (none assumed).

## Results — RECENT cohort (exit_closes, n=69, all LATE era = current bot)
| Class | n | scenario | fill rate | fee saved | miss slippage | **NET** | worst single miss |
|---|---|---|---|---|---|---|---|
| **ALL exits** | 69 | OPT / MID / PESS | 100 / 87 / 83% | +$8.27 / +$7.54 / +$7.47 | $0 / −$8.88 / −$7.47 | **+$8.27 / −$1.34 / −$0.01** | −$2.86 |
| **LLM_EXIT_AGENT** | 22 | OPT / MID / PESS | 100 / 95 / 91% | +$3.75 / +$3.70 / +$3.69 | $0 / −$0.18 / −$0.50 | **+$3.75 / +$3.52 / +$3.19** | −$0.48 |
| **SL** | 37 | OPT / MID / PESS | 100 / 86 / 81% | +$3.88 / +$3.29 / +$3.23 | $0 / −$7.79 / −$4.89 | **+$3.88 / −$4.50 / −$1.67** | −$2.86 |
| **TRAILING_STOP** | 10 | OPT / MID / PESS | 100 / 70 / 70% | +$0.64 / +$0.55 / +$0.55 | $0 / −$0.91 / −$2.09 | **+$0.64 / −$0.36 / −$1.53** | −$1.46 |

Ledger cohort (n=99, Jun 15+) corroborates: LATE-era ALL = +$7.59 / −$7.78 / −$6.96 (SL again the drag); MID-era (Jun 15–23) is **worse** — agent closes then were momentum-urgent and two single misses (BTC −$13.71 = 112 bps, SOL −$7.45) swamp all fee savings. Era lesson: maker only wins when the exit is genuinely non-urgent — E3 agent closes are dead-capital scratches near entry (fill 95%, worst miss −$0.48); June's panic-flavored closes were not.

**Agent partials** (applied, n=27, Jun 15+): fill 100/93/81%, but the misses are fat (−33 to −57 bps) — partials fire during moves. Borderline; only viable with a tight adverse-move bail. Not in the ship scope.

## The missed-exit cost, quantified (the real risk)
- SL class, MID scenario: 5/37 missed, mean −$1.56/miss, worst −$2.86 (52 bps) — one miss erases ~15 filled exits' savings. That ratio is the whole story: 3 bps saved vs 30–110 bps tail when price runs. **Urgent exits must stay taker.**
- Agent-close class (E3): 1–2 misses, all ≤ 12 bps — dead-capital closes have no runaway by nature (price is pinned near entry; that's why they're being closed).

## Fragility
- Agent-close win is NOT single-trade: positive in all 3 scenarios and remains positive removing the best fill (savings are 22 near-equal ~$0.17 slices). Worst-case tail observed for this class in the current era: −$0.48.
- Dollar magnitude is small: ~+$0.15/exit ≈ +$0.40/day at current cadence. The structural payoff is the fee floor: exit side 4.5→1.5 bps cuts the round trip ~10.4→~7.4 bps (−29%) exactly on the smallest-move class (agent closes median |move| 23.5 bps, RQ17).

## SHIP LINE
**SHIP: post-only maker exits for LLM_EXIT_AGENT full closes only; keep SL/TRAILING/partials taker.** Exact config:
- Order: ALO (post-only) limit at top-of-book on our side at decision time.
- Fallback: cancel-and-cross to taker at **10 min unfilled OR price ≥10 bps adverse from limit, whichever first** (backtest tested 15/30-min windows; the price bail caps the only tail that hurts).
- Scope guard: apply ONLY when exit reason class is non-urgent (agent full-close / dead-capital); never for SL, trailing, panic, or liquidation-adjacent exits.
- Additionally (structural, no backtest needed): rest TP1/TP2 as ALO limit orders — a resting TP is maker by definition on the same trigger; W1 data (RQ17: TP2 5.7 bps RT, trailing 1.4) proves HL fills these as maker.
- Watch: log maker_attempt/maker_filled/fallback_reason per exit; re-audit after 30 maker exits; kill if realized fallback slippage > fee saved over any 20-exit window.

Pre-approved per mandate — burn agent / engine implements.
