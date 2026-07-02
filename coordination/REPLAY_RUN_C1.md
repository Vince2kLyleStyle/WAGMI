# REPLAY_RUN_C1 — full-system historical replay
Generated 2026-07-02 14:01 UTC by tools/replay_harness.py (THE_STANDARD v1.3 compliant reporting)

## What this is
Historical candles replayed through the REAL pipeline: ensemble -> RiskFilterChain (6 gates) -> 9-agent LLM coordinator (CLI-routed claude -p, default agent models) -> restored profit-lock exit engine (PositionManager: TP1 partial + BE stop, progressive trailing, 5m intra-bar fills). Manufactured clean-close sample for the live ledger.

## Window & config
- Window: 2025-07-07 -> 2025-07-14 (walk); fetch depth 11d (extra = indicator warmup)
- Symbols: BTC,ETH,SOL
- Starting equity: $500 (matches live account scale)
- Fee model: {'taker_fee_bps_per_side': 5, 'slippage_bps': 3, 'funding_rate_per_8h': 0.0001} (taker both sides; entry slippage rescales SL/TP proportionally; exit slippage on stop fills; conservative worst->best->close fill order inside each bar)
- LLM cap: 180 calls, sleep 15.0s/pipeline (live-bot quota protection)

## Results
- Closes generated: 3
- Win rate: 66.7% (2W/1L)
- Net PnL (after fees+funding): $+16.18 on $500 equity | fees paid $0.11
- Final equity: $516.22
- Per-side: {"LONG": {"n": 3, "wins": 2, "wr": 66.7, "pnl": 16.18}}
- Per-regime: {"trending_bull": {"n": 3, "wins": 2, "wr": 66.7, "pnl": 16.18}}

## LLM usage (honest accounting)
- Total LLM calls: 181 (cap 180; cap reached: True)
- Journal entries: 49 | failures: 0 | pre-filter skips: 327
- Entry-event filter: 87 qualifying events | starved by caps: 26 | cooldown-suppressed: 30 | per-symbol calls: {"BTC": 62, "ETH": 61, "SOL": 58}
- Wall time: 145 min
- SCALING MATH: ~1.0 closes per 60 LLM calls at this signal density (3 closes / 181 calls)

## Isolation proof
- Sandbox: bot/data/replay/C1/sandbox (code copy + empty data tree; runner refuses to start outside a marked sandbox)
- Production data diff (bot/data, bot/ml_data, bot/backtest_ml_data, bot/trades.csv; before vs after): 80 paths changed
- CHANGED PATHS (expected: live-bot churn only — the replay process has no handle to these by construction; verify none are backtest/replay artifacts):
    - data/analysis/performance.json
    - data/analysis/trade_outcomes.csv
    - data/bot_heartbeat.txt
    - data/circuit_breaker_state.json
    - data/counterfactuals/scenarios.json
    - data/execution_analytics.csv
    - data/feedback/adaptive_risk_state.json
    - data/feedback/adaptive_sizer_state.json
    - data/feedback/backtest_state.json
    - data/feedback/confidence_state.json
    - data/feedback/hold_time_rules_state.json
    - data/feedback/regime_feedback_state.json
    - data/feedback/signal_quality.json
    - data/feedback/tuner_state.json
    - data/funding_oi_history.jsonl
    - data/heartbeat.json
    - data/llm/active_learning.json
    - data/llm/agent_costs.json
    - data/llm/agent_performance.json
    - data/llm/agent_performance.jsonl
    - data/llm/bot_perception/percepts.jsonl
    - data/llm/counterfactual_pending.jsonl
    - data/llm/counterfactual_resolved.jsonl
    - data/llm/decisions.jsonl
    - data/llm/deep_memory/insight_journal.json
    - data/llm/deep_memory/strategy_fingerprints.json
    - data/llm/deep_memory/trade_dna.json
    - data/llm/graduated_rules.json
    - data/llm/growth/growth_reports.json
    - data/llm/growth/hypotheses.json
    - data/llm/growth/parameter_changes.json
    - data/llm/growth/recommendations.json
    - data/llm/growth/self_improvement_proposals.json
    - data/llm/growth/veto_tracker.json
    - data/llm/learning_state.json
    - data/llm/llm_memory.json
    - data/llm/network_learning.json
    - data/llm/neuroplasticity_state.json
    - data/llm/operator_messages.json
    - data/llm/overseer_memo.json

## Fidelity caveats (honest, per THE_STANDARD)
- DATA SOURCE: candles pre-seeded from Coinbase SPOT (exchange perp history does not reach this era) — spot prices proxy Hyperliquid perp prices; basis/funding divergence not modeled.
- EMPTY MEMORY: the replay brain starts with empty memory/rules/stats stores (prevents future-knowledge leaks, but the live bot carries accumulated memory the replay lacks).
- SNAPSHOT SCOPE: replay prompts contain candle-derived stats only (price changes, volume ratio, ATR); live prompts also carry funding/OI/intel feeds not reconstructed point-in-time here.
- FILL MODEL: 1h bars with 5m intra-bar sub-fills where 5m data exists; stop fills assume candle-low/high touch = fill (conservative); funding approximated at a flat rate per 8h.
- NON-DETERMINISM: LLM outputs vary run-to-run; a replay is one sample of the policy, not a deterministic backtest.
- REPLAY_MODE veto rule: entries with no LLM opinion (failure/cap/pre-filter) are skipped, not traded mechanically — the sample is 100% LLM-approved trades (live has a mechanical fallback path).

Artifacts: bot/data/replay/C1/replay_trades.csv, run.log, isolation_report.json, sandbox/replay_out/*