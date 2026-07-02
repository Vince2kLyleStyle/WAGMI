# LEFT ON THE TABLE — Merged Lane Table (2026-07-02)

Merge of TABLE_A_UNSHIPPED.md (lane A, commit 7964ec0), TABLE_B_NEWSTREAMS.md (lane B, 4743d29), TABLE_C_CAGE_COST.md (lane C, aee1de5). One ranked table of everything on the table. Disposition per v1.4: **SHIP-NOW** = evidence-backed + reversible, the engine burns these without asking. **PILOT** = needs accrual, auto-test scheduled. **OWNER** = spend / irreversible / fork decision.

## SHIP-NOW (engine burns these first)

| # | Opportunity | Evidence + n | Expected value | Ship-spec / next test | Lane |
|---|-------------|--------------|----------------|----------------------|------|
| 1 | After-loss de-sizing | Post-loss WR 20% (n=65) vs post-win 46%, p=0.012; ship-eligible since morning, never built | Cuts the single worst-conditioned trade class; largest verified WR gap in the book | Size multiplier <1 after a realized loss (config-flagged, reversible); forward-track post-loss WR | A |
| 2 | RQ15 thesis checklist in agent prompts | 74% vs 37% graded outcome split, n=155, holds in both eras; verified ABSENT from bot/llm/agents/prompts.py | Doubles thesis-quality hit rate at zero infra cost | Inject checklist into prompts.py; A/B stamp checklist-on vs off in decision log | A |
| 3 | RQ9 full-close gate is INVERTED | Hold logging 0 records ever; exit_engine.py:130-148 allows loser-closes (−$1,306-class) and blocks winner-closes (+$951-class) | Stops a sign-flipped gate that costs four figures per class | Fix gate direction to match RQ9; turn on hold logging same commit | A |
| 4 | RQ10 validated post-label overlay | Validated .600→.652 uplift; only the UNVALIDATED input-injection variant shipped (c88b0a5) | +.05 calibration on labels the bot already produces | Ship the validated overlay path; retire or flag the unvalidated injection | A |
| 5 | Measurement pack | strategies_agree stamp, 7/9 agent conf logging, sniper rej entry/SL/TP, 35 blank-fee rows — none committed | Unblocks every downstream study; self-knowledge instruments first (mandate) | Commit the pack; it is pure logging, fully reversible | A |
| 6 | Collector fixes (lane B flagged) | buy_ratio_10t samples last 10 trades = dust; OKX ratio timestamp unlogged so effective-n dishonest | Makes the lane-B pilot signals validatable at all | Taker volume over full 15min interval; log OKX ratio timestamp | B |

## PILOT (accrual running, auto-test scheduled)

| # | Opportunity | Evidence + n | Expected value | Ship-spec / next test | Lane |
|---|-------------|--------------|----------------|----------------------|------|
| 7 | spread_bps 4h feature | Pooled IC +0.36, 5/5 symbols, survives top-10%-move trim (+0.39), noise-like (lag1-AC~0) — but 480 rows, 23h only | Best new-stream candidate; IC of this size clears fee bar if it holds | Pre-registered gate: n>=780 non-overlapping (~26d for 4h), |IC|>=0.05 block-bootstrap p<0.01, 4/5 sign + half-split, exclude pilot day; weekly auto-rerun of lane_b_newstream_ic.py | B |
| 8 | d_ls (long/short ratio delta) 4h | Delta IC +0.19, 5/5; level IC +0.33 but AC 0.98 = effective n≈3-5/sym, too stale; direction is MOMENTUM not contrarian | Secondary confirm feature once accrued | Same pre-registered gate; use delta only, never level | B |
| 9 | basis_bps 4h contrarian | IC −0.16 (fade premium), 4/5 symbols, survives trimming (−0.18) | Third feature in the same pilot batch | Same gate; ~26d accrual | B |
| 10 | Maker exits backtest | BT_MAKER_EXITS never ran (no file/script/commit); runnable today from exit_decisions.jsonl + 15m candles | Halves the 10.4bps cost floor if fills confirm | Run the backtest first (free, data on disk); ship maker exits only if fill-rate-adjusted cost beats taker | A |
| 11 | EMA20 shadow forward test | +0.127R, n=993 historical | Cheap regime filter if forward-confirmed | Shadow-log EMA20 signal alongside live decisions; no capital until forward n accrues | A |
| 12 | RQ21 live revivals | BB-squeeze BTC re-resolution; sniper PF recheck now that S1 shipped (2 live, 3 dead) | Option value on already-collected data | Re-run both studies against post-S1 data | A |
| 13 | Depth reader wiring | market_depth.py built, ZERO importers; no ext_depth in snapshot_builder | New orthogonal stream at zero collection cost | Wire into snapshot_builder as logged-only field; graduates via lane-B-style IC gate | A |
| 14 | taker_bs_ratio | Weak +0.09 both horizons, 4/5, weakens under trimming | Watch only | Stays in weekly auto-rerun; no build | B |
| 15 | LLM selection-edge KPI | Taken-vs-offered edge ≈ +0.03% now, below 0.10% fee bar | The dechoke buys learning data, not money, until this clears fees | Track weekly; unchoke throughput decisions key off this number | C |

## OWNER (spend / irreversible / fork)

| # | Opportunity | Evidence + n | Expected value | Ship-spec / next test | Lane |
|---|-------------|--------------|----------------|----------------------|------|
| 16 | volume_chop throughput dechoke | 32,645/32,645 rejects on broken 0.0 input; blocked RANDOMLY (rejected −0.072% vs passed −0.043%, identical, n=1,097 vs 768) | Zero alpha cost from removal, but restores 63 candidate-opps/day of LLM throughput + learning data — quota spend is the owner call | Fix the broken 0.0 input or remove the gate; pairs with #15 KPI | C |
| 17 | RECALL build | Built, flag-OFF | Unknown; flipping a system-wide flag on live capital | Owner flips flag or approves a bounded pilot window | A |
| 18 | graduated_rule_veto class | Only positive-mean gate class: n_dedup=17, median −3.3%, CI [−2.6,+4.5] — no claim, small-n humility | Possibly the one gate worth loosening; n too small to act | Accrue to n>=30 before any change; owner-visible because it touches the only maybe-positive block | C |

## Closed / not on the table (wins by killing)

- Cage cost ≈ $0/mo (lane C): every gate class n>=15 blocked net-NEGATIVE flow after 0.10% fees; conf_floor n=2,321 dedup −0.471%/opp, survives all 3 weeks — the cage SAVED ~$194/mo. Era caveat: priced in June, a bad stream month.
- Book imbalance ALL bands dead 1-4h (IC 0.00-0.05, p 0.29-0.97); funding level dead (BTC +0.64 vs SOL −0.50 = day noise); hype_long_veto honestly re-retired (8b20614); RQ21 #1-3 killed by later lanes.

## The 3 most valuable things we were leaving on the table

1. **After-loss de-sizing** — the strongest verified conditional edge in the book (20% vs 46% WR, p=0.012) sat ship-eligible all day with nothing built.
2. **The inverted RQ9 full-close gate** — live code actively selecting loser-closes and blocking winner-closes, a sign error worth four figures per class.
3. **The RQ15 thesis checklist** — a 2x outcome-quality prompt change (74% vs 37%, n=155) that was never pasted into prompts.py.
