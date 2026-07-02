# RESEARCH AGENDA — the infinite queue
Owner mandate (2026-07-02): "take the long aggressive move to understand the mass data... we can continue backtesting to no end... damn near infinite opportunity."
This is the standing research backlog. The learning engine pulls the top unanswered question each pass (after spine verification + HOLES burns). Every answer gets a coordination/ report + a one-line verdict added here. Questions are cheap — add freely; answers require evidence.

## Rules of the program
- **TOKEN TARGETING (owner, 2026-07-02): spend goes to TRADE and MARKET data first** — trades ledger, counterfactuals, theses, candles, funding/OI, exit decisions. Infrastructure/meta audits (memory stores, dashboards, non-trading data) run only when trade-data questions are exhausted for the pass or a P0 integrity issue demands it.
- Every claim gets a denominator, an era-split, and an adversarial check (week-1-artifact test is mandatory).
- Negative results are wins — a killed hypothesis is compounded knowledge. Log them.
- Nothing graduates to live behavior without: backtest + counterfactual + owner sign-off (or A/B rule with auto-retire).

## Fallacy definition (owner, 2026-07-02 — broadened)
A fallacy = ANY of: (a) a standard violation (v1.2/v1.3), (b) code that could be wired MORE EFFICIENTLY, (c) wiring that could produce BETTER EV UNDERSTANDING if arranged differently. Hunts under this definition treat 'suboptimal but working' as findings, not passes.

## OPEN QUESTIONS (ranked by expected value)
1. What does the honest post-fix data say after 15-20 clean closes? (The first-ever trustworthy sample. Re-run WR/conf/side/regime tables on it alone.)
3. Which entry source has real edge? (IN FLIGHT — signal-sources lane.)
4. Are any graduated rules dollar-negative under honest accounting? (IN FLIGHT — veto-rescore lane.)
5. High-ADX continuation: real edge or artifact? (IN FLIGHT — adx-survivor lane.)
6. Confidence-ladder sizing: which variant wins? (IN FLIGHT — sizing-ladder lane.)
7. RECALL layer design: which clean-ledger stats, injected into which agent prompts, improve thesis grade rates? (Blocked on clean closes; then A/B prompt-with vs prompt-without.)
8. Funding-crowding regime gate: does the fade edge hold when gated by trend-state? (Needs multi-regime funding span; collector must stay alive — H61.)
9. Exit-agent skill: are its hold/close/partial calls better than a mechanical baseline? (exit_decisions.jsonl vs price-after.)
10. Regime classification accuracy: does the Regime agent's label match realized vol/trend measures? (Its labels gate everything downstream.)
17. Fee drag map: realized fees as % of gross per setup type — where does fee drag eat the edge?
18. Old eras archaeology: the pre-May trades (rows 1-21, +$1,756) — what conditions produced them and are they detectable live?
19. Ensemble strategy attribution: which of the 4 strategies' signals actually correlate with wins? (Needs H22 strategy-capture fix live first.)
25. Scorecard validity: trade_scorecards.jsonl (2.5k records, nothing reads it) — do scorecard grades predict realized PnL, i.e. is the grader worth its tokens?
26. Proposal→adoption audit: growth/hypotheses+recommendations+self_improvement_proposals (1.5MB, ~write-only) — what fraction of machine proposals were ever enacted, and did enacted beat ignored?
27. P0 INTEGRITY — single source of truth: five trade stores disagree (trades.csv 91 vs trade_ledger.csv 157 vs ml_data/bot.db trades 330 vs ml_data/trade_outcomes.json vs analysis/trade_outcomes.csv 25). Reconcile once, designate canon, alias the rest — every WR/EV answer depends on this denominator. (Full flag list: DATA_CENSUS.md §4.)

28. DATA EXPANSION: what free trade/market streams do we NOT collect that would sharpen EV? (HL: L2 book depth/spread/imbalance snapshots, liquidation events, trade-tape aggregates; Binance: long/short account ratio, taker buy/sell flow, basis.) Time-series can't be backfilled — every uncollected day is lost forever. Collector must be ISOLATED (own daemon/task, new files, zero contact with the trade path during the open rewire).

29. SCENARIO LIBRARY (owner, 2026-07-02): mine the full corpus (56k signals, 79k rejections, graded theses, multi-year candles) to extract per-setup ARCHETYPES — the exact conditions where each setup historically peaked — then scenario-targeted emulation: replay each archetype through the (post-dechoke) brain for a per-archetype verdict: does it recognize + monetize its ideal scenario? Runs on the campaign infrastructure; fires after the C1'-C6' A/B lands.

## ANSWERED (verdict + report)
- Q2 Exit intent restoration: RESTORED S3 geometry beats current + naive control in EVERY year/symbol/side over 2.5y (+91.7R vs −65.0R, t=3.80, n=1,131) — structural, not era-luck; standalone-alpha claim does NOT survive worst-case ordering — RQ_MULTIYEAR_SIM.md
- Q11 Sessions: NO session is a real dollar edge; night-block "savings" = generic selectivity, not the clock; "night is dead" premise factually wrong (EU 06-12 quietest) — RQ11_SESSIONS.md
- Q12 Symbol personality: winners don't breathe (26/36 zero adverse excursion); no exit-geometry grid cell creates alpha; HYPE doesn't pay (17% WR, −$914, both eras); HYPE-LONG 1/13 qualifies for n≥13 veto — RQ12_SYMBOL_PERSONALITY.md
- Q13 Lead-lag: KILL — BTC→alt lead at 1-6h does not exist (|r|≤0.057); alts move inside the same bar; tradeable residue after fees: zero — RQ13_LEAD_LAG.md
- Q14 CF model: confidence floors have ~zero rank signal (AUC 0.516); logistic beats base (p=2e-5) but not confidence head-to-head (p=0.13–0.27) — no graduation, NO deployment — RQ14_CF_MODEL.md
- Q15 Thesis forensics: quality IS legible — checklist score≥2 → 74% vs ≤0 → 37%, monotonic, both eras; fresh numeric target + ≤25 words in; numeric QB-stat citations + session-edge language out — RQ15_THESIS_FORENSICS.md
- Q16 Drawdown structure: streaks are REAL temporal clustering (runs-test p=0.012; post-loss WR 20% vs post-win 46%), no session/symbol pocket; fix = after-loss de-sizing — RQ16_20_RISK_MATH.md
- Q20 Equity-curve MC: above ~2x leverage buys ruin with zero median growth (3x = 20.5% ruin under forward dist); 1x now, 2x gated on n≥30 live mean R ≥ +0.10 — leverage-ramp table in RQ16_20_RISK_MATH.md
- Q21 Archive mining: estate mined — trend-shorts edge, HYPE LONG toxic all eras, conf 60–79 anti-signal; 82 A/B rules' primary data PURGED; sniper edge real but PF 1.02 (exit geometry eats it); top-5 revivals specced — RQ21_ARCHIVE_MINING.md
- Missed-EV regime-skips: ARTIFACT (week-1 crash shorts) — MISSED_EV_LOCKDOWN_2026-07-01.md
- Funding-crowding as mean-revert fade: weak fade, regime-dominated; don't flip sign, gate by regime — CROWDING_STUDY.md
- Quant Brain stats value: anti-signal (17% WR when cited); muted — THESIS_AUDIT_2026-07-01.md
- Thesis confidence calibration: INVERTED (30-44 band 67% right vs 60-74 43%) — THESIS_GRADES_2026-07-01.md
- Q22 Gate ROC: "56k" = 6,967 episodes (8x re-emission); confidence doesn't rank (era AUC 0.43–0.51); 60–79 anti-signal confirmed n=926; live floors 66/71 keep the WORST slice; no threshold makes stream positive (best −9bps gross vs fees) — problem is upstream signal gen; volume_chop broken (fires on 0.0) + most expensive gate; trend_alignment/rr/fee/ev floors dead-wired — GM_GATE_ROC_56K.md
- Q23 Sniper rejections: "79k" = 5,528 episodes (41% dupes); NO gate provably denies era-stable EV (low_consensus/low_confidence/daily_limit "costs" = W1 crash beta); two gates save money (quality_floor_proven_solo −70bps, aggressive_standard_skip LATE −179); keep all; low_rr watch at n≥50; rejections never logged entry/SL/TP (fix) — GM_REJECTIONS_79K.md
- Q24 Agent skill: REGIME only keeper (58.5% @4h vs mech 47.5%, all eras); QUANT = wk1 artifact, de-Opus/mute (ev_per_dollar AUC 0.444–0.470 anti-predictive — Quant-Brain-suspect CONFIRMED); TRADE skip filter real post-wk1, go-confidence inverted (−0.17); CRITIC veto wk1 artifact (mid inverted) → shadow-mode proposal owner-gated; EXIT holds −25bps → fix hold prompt; 7/9 roles have broken confidence logging — measurement first — GM_AGENT_SKILL_24K.md
- Synthesis of Q22–24 + disposition under THE_STANDARD v1.1 — GOLDMINE_2026-07-02.md
