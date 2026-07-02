# TABLE A — VALIDATED-BUT-UNSHIPPED INVENTORY (2026-07-02)

**Lane:** A (research, read-only on bot code). **Standard:** THE_STANDARD.md v1.4.
**Method:** swept all coordination/*.md for positive findings; verified ship-status against the actual tree + git log (not against what reports *said* would ship). Every "UNSHIPPED" below was code-verified absent today.

**Ship-status verification receipts (how each was checked):**
- Thesis checklist: `grep` of `bot/llm/agents/prompts.py` — no fresh-numeric-target requirement, no ≤25-word cap, no TP1/TP2 ban. Absent.
- Hold logging: parsed `bot/data/logs/exit_decisions.jsonl` — **0** records with `exit_action=="hold"` (holds return early at `exit_engine.py:94,182` before `_log_decision`).
- Maker exits: no `BT_MAKER_EXITS.md`, no script in `bot/tools/{research,backtests}/`, no commits matching "maker".
- Depth reader: `bot/llm/agents/market_depth.py` EXISTS but repo-wide grep shows **zero importers**; `snapshot_builder.py` has no `ext_depth` key.
- RECALL: `bot/llm/recall.py` does not exist.
- After-loss de-sizing: no flag/commit; `bot/execution/adaptive_risk.py` has old streak logic but nothing keyed to the RQ16_20 finding shipped.
- Regime nowcast: c88b0a5 shipped the INPUT-injection variant only; the validated post-label OVERLAY (.600→.652) is not enforced anywhere.
- Full-close gate: `exit_engine.py:130-148` allows closes only for dead-capital/thesis-invalid **losers** — the inverse of RQ9's evidence.

---

## RANKED BY EV IF SHIPPED

### 1. After-loss de-sizing (streak-aware cut-only sizing)
- **What:** halve (or 0.5x-ladder) risk/trade after a loss, restore after a win. Risk-reducing-only.
- **Evidence:** post-loss WR **20.0% (n=65)** vs post-win **45.8% (n=24)**; runs-test clustering **p=0.012** (survives era-split: p=0.024 inside Jun7+ alone); 16-loss streak lived here. [RQ16_20_RISK_MATH.md Part A; MASS_RESEARCH §3 ship-eligible #2]
- **EV:** the V2 cut-only analogue turned −$1,008 → −$67 and cut max DD $1,008→$127; this is the same mechanism keyed to time instead of confidence. Also a hard prerequisite for the 3x rung of the leverage-ramp table. Order $100s of avoided DD per streak episode.
- **Ship-spec:** multiplier in `risk_mgr.calculate_qty` path (or `bot/execution/adaptive_risk.py`), env flag `AFTER_LOSS_DESIZE=true`, quick replay backtest on trade_ledger.csv first (cut-only ⇒ ships on backtest+flag per §2).
- **BLOCKED-BY:** nothing. Explicitly ship-eligible since 07-02 morning; never built.

### 2. Thesis-quality checklist → Trade/Critic prompt (A/B, auto-retire) — RQ15
- **What:** inject: REQUIRE fresh numeric market target, ≤25 words, prefer cross-asset confirmation. Ban: numeric QB-stat citations, session/UTC-hour edge language, "holds/continues" theses, self-referential TP1/TP2 targets.
- **Evidence:** composite score≥2 → **74% right (48/65)** vs score≤0 → **37% (15/41)**, monotonic, holds BOTH eras (E1 74/17, E2 75/45); top feature fresh-numeric-target 64% vs 37% (n=155 deduped, per-symbol consistent, fragility-safe). [RQ15_THESIS_FORENSICS.md; MASS_RESEARCH ship-eligible #5]
- **EV:** direct accuracy lever on every entry decision; even half the in-sample gap (~+10-15pts thesis accuracy) moves the 26.7%-WR book more than any gate change studied.
- **Ship-spec:** prompt block in `bot/llm/agents/prompts.py` (Trade) + Critic reject-rule; flag `THESIS_CHECKLIST=true`; A/B scored against graded-thesis accuracy (grading loop is live since 07-01) with auto-retire. In-sample feature selection ⇒ must ship as A/B, which §2 allows.
- **BLOCKED-BY:** nothing. Verified absent from prompts.py.

### 3. Maker/limit exits — backtest never ran (pre-approved as backtest-first)
- **What:** rest non-urgent exits (TP2/trailing/partials, never SL) as post-only limits instead of taker markets.
- **Evidence:** W1 maker fills 1.4-5.7bps vs the 10.4bps taker round-trip floor — "limit exits roughly halve the round trip when they fill" (n=20 W1 fee rows; floor itself era-stable MID 10.2/LATE 10.4). [RQ17_FEE_DRAG.md; MORNING_BRIEF decision #9 leaned yes-backtest-first]
- **EV:** ~4-5bps/side saved on every non-urgent exit ≈ 40-50% of the fee floor; ex-W1 fees were $85 vs −$712 gross so today's dollar EV is small — but it scales linearly with any volume/size ramp and is pure cost-side (no directional risk).
- **Ship-spec (the missing backtest IS runnable from data):** `bot/tools/research/bt_maker_exits.py` — replay `exit_decisions.jsonl` + trade_ledger exits against 15m candles: rest a limit at decision price, measure fill-rate within k bars + adverse-selection cost of non-fills vs immediate taker fill; pass bar = net bps saved > 0 era-split. Then flag `MAKER_EXITS=true` on non-urgent exit types only.
- **BLOCKED-BY:** the backtest itself (verified: no BT_MAKER_EXITS artifact, no script, no commit). This item has been "pre-approved pending backtest" since the morning brief and nobody ran it.

### 4. RQ9 exit-agent pair: (a) hold logging, (b) full-close gate is INVERTED vs evidence
- **What (a):** log `hold` decisions — the agent's most frequent output is unscoreable (verified 0 records ever).
- **What (b):** current gate (`exit_engine.py:130-148`) permits full closes only for dead-capital/thesis-invalid **losers** and blocks winner-closes. RQ9's counterfactual says the opposite: the agent is good at closing **winners** (in-profit + `thesis_invalidated`/`regime_mismatch`: 61-72% correct, +$951 after removing the one HYPE outlier) and catastrophic cutting **losers** (right often, **−$1,306** when wrong).
- **Evidence:** RQ9_EXIT_AGENT_SKILL.md (n=74 applied / 72 blocked episodes; E3 blocked-advice 81% correct @24h; "0/71 disaster" was a denominator error). Fragility-fragile ⇒ flag-gated watch window, not a leap.
- **EV:** (a) unblocks all future exit-agent scoring (measurement, autonomous per §2); (b) recovers the +$860-class blocked-winner-exit value while keeping the loser-cut block that saves −$1,306-class errors.
- **Ship-spec:** (a) call `_log_decision` for holds (sampled 1-in-N if spam is a concern) — week-1 artifact: hold-accuracy table. (b) invert the carve-out: allow full-close when `unrealized_pnl > 0 AND reason ∈ {thesis_invalidated, regime_mismatch}`; keep loser block; keep tightens untouched (18/21 protective, 0 premature in E3 — already live and correctly permitted, no further action needed there). Flag `EXIT_AGENT_CLOSE_WINNERS=true`, watch 15-20 closes.
- **BLOCKED-BY:** nothing.

### 5. Regime-oracle follow-ons — the VALIDATED overlay never shipped (only the input-injection did)
- **What:** c88b0a5 shipped "mechanical nowcast injected into Regime agent INPUT (env-gated)". The thing RQ10 actually validated (.600→**.652**, era-stable .568→.607 / .630→.694) was the **post-label overlay**: hard-override to high_vol when ATR%-ptile ≥0.90 + demote agent "trending" to ranging when ADX(14)<20. Also unshipped: suppress regime-agent conf≥0.8 downstream (it is 27.3% accurate — inverted), and "gate nothing on the class label alone."
- **Evidence:** RQ10_REGIME_ACCURACY.md Findings 3/5/7 (n=3,414 label-hours; agent trending precision .127 vs .160 base rate; misses 90% of high-vol).
- **EV:** +5.2pts regime accuracy feeding every downstream agent; high_vol recall .10→.67 is the difference between knowing and not knowing a crash regime is on. Trades entered under "ranging" labels lost −$1,001 (n=69, both eras, fragility-safe) — better labels attack the one robust trade-level loss pocket found.
- **Ship-spec:** ~20 lines in `bot/llm/agents/mech_regime.py` post-label path, flag `REGIME_OVERLAY_ENFORCE=true`; A/B vs input-injection-only variant; strip conf≥0.8 celebration from downstream context.
- **BLOCKED-BY:** nothing; the prompt-input variant shipped is the UNVALIDATED one — this is a correctness gap, not just an enhancement.

### 6. Depth feed F1 — reader built, wired to NOTHING (data accruing unread since 07-01)
- **What:** `bot/llm/agents/market_depth.py` exists; zero importers; `snapshot_builder.py` has no `ext_depth`. The collector (isolated task, 15min, 5 symbols) writes L2 spread/depth-bands/imbalance/tape/futures-ctx that no agent sees.
- **Evidence:** FULL_PIPE_BUILD_MAP F1 ("HIGHEST VALUE, ZERO CONSUMERS", build order says "enable now"); DATA_CENSUS flags it can't be backfilled.
- **EV:** unquantified (n<1wk of data — honest), but it is the only slippage-physics input Risk could have, and every unwired day is lost calibration for the depth-vs-EV studies already queued.
- **Ship-spec:** wire `ext_depth` into `snapshot_builder.py` + coordinator `_ensure_field` lines for Regime/Trade/Risk/Critic per the F1 spec (format string already written, staleness auto-mute >30min). Prompt-input-only change, reversible.
- **BLOCKED-BY:** nothing.

### 7. Measurement pack (5 small autonomous fixes, all still absent, all block future re-scores)
| Fix | Evidence | Why it matters |
|---|---|---|
| `strategies_agree` stamping in counterfactual records | BT_VETO_RESCORE / synthesis DO-NOW #5 | 17/59 rules unmeasurable until stamped |
| Agent confidence logging (7/9 roles log constant 0.5) + go→ledger matching (15.7%) | GM_AGENT_SKILL_24K | SCOUT/OVERSEER/LEARNING can never be scored |
| sniper_rejections entry/SL/TP logging | GM_REJECTIONS_79K | rejections can't be outcome-scored without them |
| 35 ledger rows Jun 2-10 blank fees ($90-360 understated) | RQ17_FEE_DRAG | fee analyses inherit the hole |
| Re-point stragglers at trade_ledger.csv (trades.csv missing 54 trades, +$695 optimistic bias) + audit `signal_quality.by_session` tracker | RQ11_SESSIONS | any tool still reading trades.csv is lying |
- **EV:** no direct dollars; unlocks the fortnightly veto re-score, agent scoring, and rejection ROC v2 — all currently capped by these holes. All are §2 measurement fixes = autonomous, no owner gate. No commits found for any of them.

### 8. EMA20-pullback residue — pre-registered forward test (shadow only)
- **What:** the only weakly-positive entry family from all four backtest lanes: unconditional EMA20-pullback continuation.
- **Evidence:** **+0.127R net, n=993**, week-cluster CI [+0.018, +0.245], 17/27 weeks positive; short-biased (+0.147R short vs +0.015R long) over a bear span ⇒ likely regime beta. ADX-conditioning makes it WORSE (killed). [BT_ADX_SURVIVOR.md; BACKTEST_EVERYTHING §3]
- **EV:** if real, first-ever demonstrated entry edge (+0.13R/trade); if beta, a cheap kill. Either outcome is worth more than the shadow emitter costs.
- **Ship-spec:** pre-registration doc (entry rule frozen verbatim from `adx_continuation_bt.py`, fills at EMA-touch, 1.5×ATR stop) + shadow signal emitter logging would-be trades to a new jsonl — ZERO capital; graduate only on regime-neutral span per the report's own bar.
- **BLOCKED-BY:** needs a non-bear stretch to mean anything (data-time, not work-time); that is exactly why the shadow clock should start now.

### 9. RQ21 revival candidates — status-checked, two are live, three are dead/superseded
| Candidate | Status today | Next required evidence |
|---|---|---|
| #5 BTC-only BB-squeeze short (74%, n=68 shadow) | **LIVE, unstarted** | re-resolve the 68 shadow signals against exchange candles to kill the expiry bias (7,245/8,714 expired = survivorship); bar ≥60% WR + positive avg on unbiased set |
| #4 Sniper auto-execute (April's only live-profitable path, +$328/34; sim WR 62.7% n=59, PF 1.02) | **LIVE — precondition newly met**: S1 exit-geometry restore shipped 07-02 | re-run sniper sim on restored geometry; require PF≥1.3 over 20 more sim trades; verify 5x cap binds (old −$147 ran 9.7x) |
| #1 High-ADX trend-short boost | superseded by BT_ADX_SURVIVOR: ADX conditioning FAILED forward validation (ex-W23 −0.229R, n=59) | dead unless re-specced without ADX |
| #2 conf≥80 upweight | KILLED by BT_SIZING_LADDER (n=8, all shorts, top-trade-dependent); the cut-half shipped as S3 | none — closed |
| #3 Night-block re-test | KILLED by RQ11 (night skips were the BEST cell in the derivation window) | none — closed |
- **EV:** #4 is the highest-optionality item on this whole page if PF clears — it is the only entry path with a live-profitable era on record; #5 is a half-day script.

### 10. RECALL block (F2) — build now flag-OFF, arm later
- **What:** graded-thesis track-record injection into Trade/Critic (`bot/llm/recall.py` — does not exist).
- **Evidence/EV:** design-validated only (spec §3b-compliant, FULL_PIPE_BUILD_MAP F2); the mechanism it feeds (thesis accuracy) is the #2 item's lever, so EV is real but unproven — mandatory A/B before permanence.
- **Ship-spec:** build module flag-OFF (`RECALL_BLOCK=false`) per build map so arming is a 1-line flip; arms after 15-20 clean $5k-era closes + A/B vs graded accuracy.
- **BLOCKED-BY:** clean-close counter (2/15 as of 07-02 04:07 UTC) for ARMING — but not for building.

### 11. Owner-gated residue (proposals written, never sent as one-liners)
- **CB re-key onto loss-streak/rolling-R** (MULTIYEAR failure mode: −1R clustering, 10-15R rolling DD) — correct per evidence; CB changes are genuinely owner-first. Needs the one-paragraph proposal actually put in front of Nunu.
- **65-confidence CEILING** (beats every floor in GM_GATE_ROC; but no threshold makes the stream positive) — low EV; park unless entry quality improves.
- **2x leverage step** — gate table exists (RQ16_20); blocked by data (n≥30 live, mean R≥+0.10, WR≥55%), not by work.

### 12. Depth-collector expansions (small, clock-driven)
- **What:** collector runs 15-min × 5 symbols. Q28's own list includes **liquidation events** — still uncollected; finer cadence during 13-18 UTC (hour-13 is the #1 vol hour for all 5 symbols, RQ12 #5) is a config tweak.
- **EV:** modest; but non-backfillable — every uncollected liquidation day is gone. New symbols: gated by the one-at-a-time expansion mandate, park.
- **Ship-spec:** add HL liquidation-feed capture to `tools/market_collector.py` (isolated task, new file); optional second intraday cadence window.
- **BLOCKED-BY:** nothing for liquidations; symbol adds owner-mandate-paced.

---

## Adversarial self-check (per §1)
- Verified ship-status in CODE, not reports — 3 items reports imply are done are not (RQ10 overlay vs input-injection; F1 reader unwired; hold logging).
- Checked each candidate against later kills: RQ21 #1/#2/#3 are listed as CLOSED here precisely so they don't get "revived" again — negative knowledge preserved.
- hype_long_veto deliberately EXCLUDED from this table: restored then honestly re-retired by dollar criterion (8b20614) — contested, not unshipped.
- Fragility disclosed where it exists (#4b winner-close value is single-trade-sensitive; #8 is probably beta).
- Week-1-artifact test: items 1-7 all produce a concrete artifact inside a week (DD delta, A/B thesis table, fill-rate table, hold-accuracy table, regime-accuracy A/B, depth-block-in-prompt, re-score unblocked). Items 8-10 are clock-starters, labeled as such.
