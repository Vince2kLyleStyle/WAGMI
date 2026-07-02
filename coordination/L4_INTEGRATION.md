# L4 INTEGRATION — ext_depth stitch (apply POST-DECHOKE; target file is dechoke/L2-owned)

Date: 2026-07-02 | Lane: L4 FEEDS (F1 market-depth) | Status: module SHIPPED + tested, coordinator stitch PENDING

## What exists now
- `bot/llm/agents/market_depth.py` — first reader of `bot/data/market_depth_history.jsonl` (L2 spread/depth/imbalance/tape/OKX futures ctx, 15min collector, 5 symbols since 2026-07-01).
- Contract: `get_depth_for_snapshot(symbols=None) -> {"ext_depth": {sym: raw+deltas+provenance}, "ext_depth_summary": "<one v1.3 line per symbol + one NOTE line>"}`. Returns `{}` when flag off / file missing / all symbols >45min stale (auto-mute, one WARN per staleness episode). ASCII-only output (no `\uXXXX` token waste in json.dumps'd agent inputs).
- Flag: `EXT_DEPTH_ENABLED` (default **true**; set `false` to mute, no code change).
- Tests: `bot/tests/test_market_depth.py` (24 tests: fixtures, staleness episodes, deltas, formatting, mute contract).
- Token cost measured live: ~116 tokens for 1 symbol incl. 1h/4h deltas + NOTE; ~466 for all 5. Pass the decision's symbol to stay in the ~300/decision budget.

## The 5-line stitch into bot/llm/agents/coordinator.py
Line numbers vs f0549a3; grep anchors given because dechoke is editing this file.

1. **Import** (anchor `get_external_data_for_snapshot,` ~:183, inside the same try):
   `from llm.agents.market_depth import get_depth_for_snapshot`
2. **Snapshot injection** (anchor `_enrich_symbol = _markets[0].get(` ~:484 — inject AFTER `_enrich_symbol` is extracted so we can scope to the decision's symbol, guarded by the same look-ahead rule as external data: depth reads LIVE collector data, so skip when `_is_backtest`; in replay the file is absent for 2025-26 windows and the block self-mutes anyway, as Section 4 of the build map requires):
   `if not _is_backtest: snapshot_data.update(get_depth_for_snapshot(symbols=[_enrich_symbol] if _enrich_symbol else None))`
3. **Regime input** (anchor `regime_data["ext_data"] = snapshot["ext_summary"]` ~:3793, add sibling):
   `if "ext_depth_summary" in snapshot: regime_data["ext_depth"] = snapshot["ext_depth_summary"]`
4. **Trade input** (anchor `_ensure_field(trade_data, "ext_summary", snapshot)` ~:3896, add sibling):
   `_ensure_field(trade_data, "ext_depth_summary", snapshot)`
5. **Risk input** (anchor `_ensure_field(risk_data, "ext_funding", snapshot)` ~:4144, add sibling):
   `_ensure_field(risk_data, "ext_depth_summary", snapshot)`

Optional 6th (map §2b lists Critic for symmetry check; skip if token-tight): anchor `_ensure_field(critic_data, "ext_liq", snapshot)` ~:4296 → `_ensure_field(critic_data, "ext_depth_summary", snapshot)`.

Notes for the stitcher:
- Inject the TEXT summary (`ext_depth_summary`), not the structured `ext_depth` dict — same information, ~40% fewer tokens; the structured dict stays in the snapshot for decisions.jsonl provenance.
- No prompt edits required: the block is self-describing (source labels + "raw observation not signal" note). If a prompts pass later wants a one-line key description next to `ext_funding_momentum` (prompts.py ~:97), that belongs to the prompts owner, not L4.
- Rollback: `EXT_DEPTH_ENABLED=false` or revert the 5 lines; module has no other callers.
