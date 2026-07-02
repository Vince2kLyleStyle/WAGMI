# BT_EXITAGENT_CLOSES — full-close policy-bar replay (2026-07-02)

**Standard:** THE_STANDARD v1.4 — denominators, era-splits, fragility, honest assumptions.
**Script:** `bot/tools/research/bt_exitagent_closes.py` · **Artifacts:** `bt_exitagent_closes_results.json`, `bt_candles_15m.json`
**Data:** `exit_decisions.jsonl` (526 LLM close calls → **128 position episodes**, 81 with an applied close; 116 ledger-matched, 108 qty-matched, 8–17 unscored per policy, disclosed) + `trade_ledger.csv` + HL 15m candles Jun 1 – Jul 2.

## VERDICT: raise the dead-capital full-close confidence floor 0.60 → **0.80**. That bar (DEAD_CONF80) wins the current era; the incumbent 0.75-conf dead-capital closes are the value-negative slice.

## Method (what "delta vs actual" means)
- Baseline = what actually happened (applied/blocked history as logged). Per position, each policy picks the **first call passing its bar**.
- Policy closes earlier than the real exit → **exact realized delta** = qty·d·(px@policy-close − actual ledger exit px). No horizon truncation.
- Policy removes an applied close → RQ9 convention: simulate recorded SL/TP2 forward on 15m candles, 24h horizon; delta = −(close value).
- Fees ≈ neutral (one taker exit either way). Qty is approximate where partials ran mid-episode (noted, not modeled).
- Confidence is bimodal in the data: every close call is **0.75 (463) or 0.85 (63)** — so conf≥0.80 ≡ the 0.85 subset, and the current 0.60 floor never binds (CURRENT_RULE ≡ "dead-capital-only" in this data).

## Results — net PnL delta vs actual (positive = policy better), by era
| Policy | ALL (n_chg) | E1 Jun1–10 | E2 Jun16–22 | **E3 Jun23–Jul2** | ALL w/o best | **E3 w/o best** |
|---|---|---|---|---|---|---|
| (a) CURRENT_RULE (dead-cap loser, conf≥0.60) | +$1,397 (62) | +$1,464 | −$103 | +$36 (22) | +$233 | +$11 |
| (b) = (a) — conf floor never binds | — | — | — | — | — | — |
| (c) **DEAD_CONF80** (dead-cap loser, conf≥0.80) | +$1,122 (74) | +$1,103 | −$91 | **+$111 (25)** | −$42 | **+$40** |
| (d) DISABLED (tightens/partials keep working) | +$983 (73) | +$1,104 | −$220 | +$98 (22) | −$181 | +$28 |
| (e) DEAD_ANY_SIDE (winners allowed, exploratory) | +$2,650 (71) | +$2,689 | −$87 | +$48 (28) | +$1,486 | +$23 |
| (f) ALL_CONF60 (pre-block June behavior) | +$574 (60) | +$297 | +$222 | +$55 (44) | +$295 | +$30 |

Denominators: 128 episodes total; E2 = 30, E3 = 66. n_chg = positions where the policy diverges from actual.

## Reading it honestly
- **E3 (current bot) is the decision cell.** DEAD_CONF80 wins it: **+$110.57 over actual, n=25 changed, survives remove-best (+$40.21)**. It beats the incumbent rule in E3 by **+$75** and also edges it in E2 (+$11); only E1 prefers the incumbent (−$361 for the raise) — and E1 is the pre-rebuild bot whose fat-tail single trades RQ9 already disqualified as evidence.
- **DISABLED is a close second in E3 (+$98)** — i.e., the dead-capital gate enabled 2026-06-30 has been *mildly value-negative* so far: its 0.75-conf closes gave back more than they saved (biggest E3 givebacks: XRP LONG +$70, ETH SHORT +$28, ETH LONG +$16 recovered within 24h of being closed). Keeping full-close alive only at 0.85 conf preserves the true dead-capital escape hatch (the reason the blanket block was lifted: positions stuck ~11h) while cutting the losing slice.
- ALL-era headline numbers (+$1.0–2.6k) are **E1 artifacts** — the same 06-02 ETH (+$1,164) / 06-03–04 HYPE/ETH fat tails from RQ9; DEAD_CONF80 fails remove-best across ALL eras (−$42). Do not quote the ALL column as the win.
- DEAD_ANY_SIDE (+$2,650 ALL) is entirely E1 winner-closes; in E3 it adds nothing (+$48 < DEAD_CONF80). Does not justify opening winner-closes now.
- Caveats: 20/25 of DEAD_CONF80's E3 changed cells are 24h-truncated sims; E3 dollar magnitudes are $1.5–4.5/position — a small-dollar knob either way; every cell is n<30.

## RECOMMENDED CONFIG (evidence-backed → ships per v1.4; queue for burn agent)
In `bot/llm/exit_engine.py` Rule 3, loser branch: raise the close confidence floor for the dead-capital/thesis-invalid exception from **0.60 to 0.80** (one-line change; winners branch already at 0.90 + env gate — unchanged; tightens/partials untouched — RQ9 E3 tightens 18/21 protective stay as-is).
- Evidence line: E3 +$110.57 vs actual (66 episodes, 25 changed, +$40.21 without best); beats current rule in both post-rebuild eras; DISABLED-equivalent downside is capped because 0.85-conf closes remain available for genuinely stuck capital.
- Watch: re-run this scorer after ~25 new full-close episodes; if the 0.85-conf closes grade negative too, fall back to DISABLED (it was within $12 of the winner in E3).
- Measurement debt (unchanged from RQ9): `hold` decisions still unlogged — the agent's most frequent output remains unscoreable.
