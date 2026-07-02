# FULL_PIPE_BUILD_MAP — "ALL mechanical signal and analysis goes straight through Claude's brains"

Date: 2026-07-02 | Author: read-only mapping agent | Anchor commit: f0549a3 (line numbers cited against this tree — a dechoke agent is editing gate files concurrently, so every edit below carries a **grep anchor** to re-locate after the dechoke lands).
Doctrine (owner, 2026-07-02 evening, THOUGHT_JOURNAL): every ensemble SIGNAL reaches the LLM coordinator with raw data + honest stats (THE_STANDARD §3b v1.3); the mechanical layer vetoes ONLY physics (validity / circuit breaker / position limits / liquidation / quota); ALL opinion lives inside Claude's pipeline and is graded against price. Quota boundary: no-signal scans don't call.
Companion doc: FALLACY_AUDIT_2026-07-02.md (D*/M* refs below) — the dechoke's spec. This map is the **build** spec for what comes after.

**Definitions used throughout**
- PHYSICS (keep): structural validity, circuit breaker, max positions, duplicate guard, liquidation safety, notional caps, execution staleness, and quota-routing (cooldowns/caches/budgets that defer or drop *calls*, never based on an opinion about signal quality).
- OPINION (must go / demote to context): any condition that predicts whether a signal is *good* (confidence thresholds, WR/EV stats, regime fitness, noise scores, learned rules failing §2b provenance) and uses that prediction to route a signal away from the coordinator or to mutate the coordinator's output.
- Sanctioned exception: graduated-rule hard vetoes MAY enforce pre-LLM **only** when §2b-compliant (n>=13, dollar-positive, current-ledger provenance) — owner-standing rule ("trust LLM + data-learned vetoes n>=13").

---

## 1. RESIDUAL LLM-ROUTING THRESHOLDS (the router census)

Signal flow today: `handle_symbol` → `ensemble.evaluate()` [R1] → solo-recovery via `evaluate_raw()` [R2-R4] → sniper/QB side-channels [R20] → regime-floor annotation [R6] → LLM-first branch [R7-R9] → `_process_symbol_llm_first` → SafetyFilterChain [R25] → `coordinator.get_entry_decision` [R10-R19] → post-LLM caps → execute. Mechanical fallback path (signal_pipeline RiskFilterChain) engages only when LLM-first is off/diverted — its gates are the dechoke's job (Section 3).

### 1a. Routers in `bot/multi_strategy_main.py`

| # | Line | Condition | Class | Verdict / removal edit |
|---|---|---|---|---|
| R1 | 4817 | `signal_result = self.ensemble.evaluate(symbol, data)` runs first, with all internal quality gates | MIXED | Keep the call (its telemetry feeds trackers) but the LLM route must not depend on its verdict — see R7. End-state: LLM route keys off `evaluate_raw` only; `evaluate()` verdict becomes labeled context ("mechanical would have: pass/reject b/c X"). Anchor: `Always run mechanical first`. |
| R2 | 4828 | `if signal_result is None and self.llm_mode >= LLMMode.SIZING` | PHYSICS | Keep — LLM_MODE is the owner's autonomy dial (owner-gated to flip). |
| R3 | **4838** | `if _raw.confidence >= 60` (solo signals only reach LLM at conf>=60) | **OPINION — the May-31 classic's surviving twin.** Live floor for consensus path is 20 (ENSEMBLE_CONFIDENCE_FLOOR=20.0) but solo signals still need a hardcoded 60. | DELETE the threshold: every non-None `evaluate_raw` result dispatches. Confidence goes into the prompt as raw context (it already does). Quota protection = R9 cooldown + R10 cache, not conf. Anchor: `LLM-FIRST: all ≥60% solo signals`. |
| R4 | 4858-4863 | `_PROVEN_SOLOS` whitelist (BTC SELL / ETH BUY / SOL SELL, conf>=65) — legacy branch | OPINION (M20 — "100% WR on 135 shadow signals" from the condemned ledger) | DELETE the whole `else:` legacy branch — LLM-first supersedes it; it only executes when llm_first silently flips off (R5), i.e. exactly when nobody is watching. Anchor: `_PROVEN_SOLOS`. |
| R5 | 1584 | `self.config.llm_first_mode = False` when startup prereqs lapse → silent full mechanical fallback | RISK CHANNEL | Keep the check, make it LOUD: alert (Discord/Telegram + heartbeat flag `llm_first_degraded=true`) and require `ALLOW_MECHANICAL_FALLBACK=true` to trade mechanically at all; default = signals annotate/track but do not execute without the brain. Anchor: `prerequisites not met`. |
| R6 | 5090-5108 | Regime-floor gating (`llm.signal_gating`) — currently logs "would reject (bypassed)" and lets everything through | OPINION, already defanged | VERIFY no enforcing branch exists (grep `gating_result.approved` — only the bypass log should consume it); convert its output into a labeled prompt line ("regime floor gater would have rejected: conf X < floor Y in regime Z") instead of a dead log. Anchor: `Regime floor would reject (bypassed)`. |
| R7 | **5166-5174** | `_llm_first_min = min(60.0, ensemble_confidence_floor)`; `if _sig_conf < _llm_first_min: _llm_first = False` → mechanical path | **OPINION — the direct descendant of the May-31 `conf<60 → _llm_first=False`.** Live value 20 (floor=20), so it bites signals conf<20 today and re-arms to 60 the day anyone raises the floor. | DELETE the divert. If any floor survives it must be 0/absent; `confidence` is already in `signal_ctx`. The comment block ("Cost gate: skip LLM entirely for low-confidence signals") is the exact opinion-as-quota confusion the doctrine bans — quota gates count calls, they don't judge signals. Anchor: `LLM SKIP: confidence`. |
| R8 | 5176 | `if _llm_first and self.llm_mode >= LLMMode.SIZING` | PHYSICS | Keep (owner dial). |
| R9 | 5179-5187 | 10-min cooldown per `{base}_{side}` → **silent `return`** — signal reaches neither LLM nor mechanical nor any log | PHYSICS (quota) with two defects | Keep the cooldown; fix: (a) log + `signal_tracker.record_signal(passed=False, stage="llm_cooldown")` so the drop is measurable; (b) add a price-move escape hatch mirroring the coordinator cache (`_entry_cache_price_tolerance`) so a genuinely new setup on the same side within 10min isn't dropped. Anchor: `_llm_eval_cooldowns`. |
| R20a | 5217-5259 | Quant Brain pre-filter veto/skip nulls `signal_result` on the mechanical path | OPINION (owner-ruled anti-signal, 17% WR) — inert today (`QUANT_BRAIN_ENABLED=false` → `_quant_brain is None`) | DELETE both blocks (here and R20b) rather than leave armed-when-flag-flips; QB stats can re-enter later as labeled context per §2b. Anchor: `QuantBrain VETO`. |
| R20b | 4891-4900, 4937-4947 | QB veto `continue`s raw signals out of the sniper/simulator channel (D16) — no counterfactual logged | OPINION, inert today | DELETE (same rationale). Anchor: `SNIPER-QB`. |
| R21d | 4747-4758 | STRATEGY_REGIME_FIT static `'avoid'` disables strategies before evaluate/evaluate_raw (D10 sibling; has shadow ledger) | OPINION | Demote to annotation: strategies always vote; FIT verdict rides in metadata ("mechanical fitness table says avoid (static theory, no n)"). Anchor: `set_disabled_strategies`. |
| R23 | 7766-7919 | Exploration epsilon converts LLM skips → entries (conviction-gated, eps=0.12) | SANCTIONED (owner-approved epsilon-greedy; reversible; conviction gate D-series-fixed) | Keep. Out of dechoke scope. Note for the swarm: this is the one place mechanical opinion overrides Claude — by design, for edge-data purchase. |
| R24 | 8031-8038 | `MAX_ENTRY_SLIPPAGE_PCT=1.5%` live-price staleness reject on the LLM path | PHYSICS (execution staleness — the price the LLM approved no longer exists) | Keep. Distinct from the D9 slippage-vs-stop OPINION gate in signal_pipeline (dechoke item). |
| R25 | 7521-7537 | SafetyFilterChain (signal_pipeline.py:1452-1465): validity / CB / max-positions / duplicate / liquidation | PHYSICS | Keep — this IS the doctrine's mechanical layer. One leak to note: `cb_conf_override_pct=0.92` lets conf>=92 override a tripped CB, and M1's boost chain can manufacture conf>=92 — after dechoke shadows M1, verify nothing inflates confidence upstream of this override, or drop the override (CB changes are OWNER-gated). |

### 1b. Routers inside `bot/llm/agents/coordinator.py` (post-dispatch, pre/post-agent)

| # | Line | Condition | Class | Verdict / removal edit |
|---|---|---|---|---|
| R10 | 1654-1689 | Entry skip-cache (TTL + price tolerance, skips only) | PHYSICS (quota) | Keep. |
| R11 | 1697-1759 | Graduated-rules veto pre-filter (veto_only) before the 5-agent pipeline | SANCTIONED **iff §2b-compliant** | Keep the mechanism; gate enforcement on rule provenance fields `{era, n>=13, dollar-positive, ledger_version=current}` (D1 adds the fields); rules missing them → shadow (log + counterfactual, no block). It already records counterfactuals — keep that. Anchor: `PRE-FILTER] VETO`. |
| R12 | 989-1017, 2212-2313 | Tiered router: Tier-1 auto-flat ("no LLM judgment needed") on regime+conf heuristics | OPINION (flag OFF: `AGENT_TIERED_ROUTING` default false) | DELETE Tier-1 auto-flat and the `_decide_pipeline_tier` conf/edge heuristics; if call-tiering is ever needed it must be quota-count-based, not signal-quality-based. Tier-2 "skip Quant agent" is fine to keep (fewer calls, no verdict). Anchor: `Tier 1 skip: low-quality regime`. |
| R13 | 1218-1235 | Critic-call-failed fallback: mechanical conf floor (`ENSEMBLE_CONFIDENCE_FLOOR/100`) + counter-trend check force skip; else -10% conf | OPINION substituting for an agent on the failure path | Replace with: one Critic retry; if still failing, mark decision `degraded=true` (rides into decisions.jsonl + thesis record), let the agents that DID run stand, and cap size_mult at 0.5x as a **physics-style degradation cap** (labeled as such, not as judgment). Anchor: `critic_fallback`. |
| R14 | 1246-1273 | Consistency-checker critical issues → override to skip (conf halved) | PHYSICS-adjacent (pipeline malfunction guard: agents contradicting on side/regime is a broken decision, not a judged one) | Keep; ensure overrides are stamped (`consistency_override`) and counted in the grading denominator so the guard itself gets graded. |
| R15 | 1275-1332 | Quant Agent mutations: conf ±0.15; noise_prob>0.6 & conf<0.20 → forced skip; conf<0.40 → size×0.5 | **OPINION (D8) — dechoke target "quant mutations"** | Post-dechoke verify gone (Section 3). If dechoke only demoted: end-state = Quant output enters the *Critic/Trade prompts* as labeled stats, mutates nothing. Anchor: `QUANT_ADJ` / `QUANT_NOISE`. |
| R16 | 4764-4777 | Kelly fraction from scratchpad scales size 0.5-1.5x, zero accuracy gating | OPINION (D8) | Same treatment: advisory line in Risk prompt ("half_kelly=X from n=Y era=Z"), no mechanical multiply, until dollar-scored n>=13. Anchor: `KELLY: f=`. |
| R17 | 4740-4762 | Risk Agent skip → downgraded to reduce when `risk_vacc<0.45` (calibration ledger) | OPINION-on-opinion (mechanical override of an LLM verdict using calibration stats the audit shows are contaminated — D3/M19 class) | Remove the auto-downgrade; instead inject the calibration line INTO the Risk prompt ("your recent skip accuracy: X% (n=N, era)") and let Risk self-adjust; Overseer/consistency can flag. Anchor: `risk_vacc`. |
| R18 | 4803-4840+ | Critic non-approve verdict **blocked** when `_critic_vacc<0.45` ("challenge blocked") + `veto_is_structured` requirement | **OPINION — dechoke target "critic block"** (mechanically suppressing one agent's veto with a contaminated stat = auto-approve channel; M19 shows vacc computed from poisoned data) | Post-dechoke verify gone. End-state: Critic verdict always stands within the pipeline; its accuracy is fed BACK to it as prompt context and graded against price. Keep `veto_is_structured` as a schema check (a veto must carry a counter-thesis) — that's protocol, not opinion. Anchor: `challenge blocked (vacc=`. |
| R19 | 1786-1793 | Daily budget exhausted → `EntryDecision.skip("budget exhausted")` | PHYSICS (quota) | Keep; ensure these are stage-tagged (they are: distinguishable from llm_skip) and excluded from veto/skip grading denominators. |

### 1c. Signal-existence filters inside `bot/strategies/ensemble.py::evaluate_raw` (a signal that can't be born can't be routed)

| # | Line | Condition | Class | Verdict / removal edit |
|---|---|---|---|---|
| R21a | 872-873 | `_get_regime_allowed_strategies` — STRATEGY_REGIME_ALLOWLIST suppresses voters even in the raw path (D10: low_liquidity → zero possible voters) | OPINION | Strategies always vote in `evaluate_raw`; allowlist verdict → metadata annotation ("mechanically disallowed in {regime}, no per-cell n"). Shadow-log suppressed votes (reuse ShadowLedger). Anchor: `regime_allowed is not None`. |
| R21b | 877-878 | `symbol_active` per-symbol strategy profile drops strategies ("no empirical edge on this symbol") | OPINION (era-unstamped WRs) | Same: vote + annotate; suppression only via §2b-compliant rules. Anchor: `symbol_active is not None`. |
| R21c | 852-870 | `self._disabled_strategies` (fed by R21d) shadow-records but excludes from voting | OPINION | Falls out of R21d fix. |
| R22 | 936-937 | `_weighted_veto(..., min_votes=1, llm_first_raw=True)` picks ONE winning side; a genuinely split book can resolve to None or hide the losing side | SIGNAL-FORMING (acceptable) with an information gap | Don't change the consensus math; ADD to metadata the full vote map incl. opposing votes (`opposing_side_votes: [{strategy, side, conf}]`) so Claude sees disagreement — v1.3 full-information symmetry. Anchor: `llm_first_raw=True`. |

**Worst live residual:** R3+R7 as a pair — solo signals below conf 60 and all signals below the ensemble floor never reach the coordinator, on hardcoded confidence (the audit's D4 shows ensemble confidence has IC ≈ 0 vs outcomes — the router keys on noise). Second worst: R18 (critic block) because it silently converts the one adversarial agent into a rubber stamp using a poisoned stat.

---

## 2. PROMPT-INPUT COMPLETENESS vs THE_STANDARD v1.3

### 2a. What ACTUALLY enters agent context today (post-fallacy-fix tree)

Entry path context assembly: `multi_strategy_main.py:7547-7603` (signal_ctx + market_ctx) → `coordinator._build_entry_snapshot` (:1971-2110) → per-agent builders (`_build_regime_input` :3716+, trade :3780+, risk :4071+, `_build_critic_input` :4162+) + shared enrichment (:536-620).

| Feed | In prompts today? | Where | v1.3 status |
|---|---|---|---|
| Raw signal geometry (entry/SL/TP/ATR/conf/strategies/num_agree) | YES | signal_ctx :7547-7573 | OK (raw) |
| OHLCV 1h/5m/4h → technicals (raw arrays stripped after compute, :560-562) | YES | market_ctx + technicals enrichment | OK |
| Funding rate (instantaneous, w/ crowding interpretation) | YES | :607-618 | OK |
| Funding trend/momentum (8h) + OI per symbol | YES (regime/trade/risk via `ext_funding`, `ext_funding_momentum`; `ext_summary` regime/trade/critic) | external_data.py:418-586 ← funding_oi_history.jsonl | OK, but source stream is fragile (22-day hole precedent; watchdogged since) |
| OI trend (rolling window w/ accumulation/distribution label) | YES | coordinator :582-605 ← `_meta.oi_history` | OK |
| Mark/oracle basis (longs/shorts overloaded) | YES | :564-580 | OK |
| Liquidation levels | YES (`ext_liq` — regime, risk, critic) | external_data ← liquidation_levels.jsonl | OK |
| **Mech regime nowcast (RQ10)** | **YES — SHIPPED**, env-gated `MECH_REGIME_OVERLAY=true` (live) | coordinator :541-558 compute → :3729-3733 into **Regime agent input only**, `format_mech_regime` labeled | OK. Gap: Trade/Critic don't see it (Regime's output summarizes — acceptable; no action needed unless RQ10 A/B says otherwise) |
| edge_data (setup WR/PF/n + regime cell + is_toxic) | YES | :7648-7719 (TOXIC now shadow) | Post-dechoke: needs n/era labels per D14 |
| BTC/ETH/SOL price + 1h trend, hour-of-day, day-of-week | YES | market_ctx :7587-7594 | OK |
| Portfolio (equity, positions, notional budget, CB proximity, consecutive losses) | YES | portfolio_ctx :7606-7637 | OK |
| Memory/knowledge/insights/self-perf blocks | YES (many) | snapshot_builder + prompt_enricher + deep_memory | Dechoke/learning-lane scope (D3, D5-D7, D12-D15, M11-M19) |
| Scout pre-formed thesis (<20min fresh) | YES | :2057-2075 | M18 cleanup applies |

### 2b. Raw truth that exists but is fed to NOTHING (the missing feeds, ranked by value)

**F1. `bot/data/market_depth_history.jsonl` — L2 depth/spread/imbalance/taker flow. HIGHEST VALUE, ZERO CONSUMERS.**
- Confirmed: sole writer `bot/tools/market_collector.py` (isolated task, 15min cadence, 5 symbols since 2026-07-01 ~23:10Z); repo-wide grep shows **no reader**. Contents per record: spread_bps, mid, bid/ask depth within 0.1/0.5/1% bands, imbalance ratio, trade-tape buy/sell vol + largest print + count, futures ctx (funding/basis/long-short-account-ratio/taker buy-sell ratio via OKX).
- Inject where: new reader module `bot/llm/agents/market_depth.py` (mirror external_data.py's tail-read + staleness pattern: skip if record >30min old). Wire into `llm/snapshot_builder.py` as `ext_depth` (same `_ensure_field` plumbing as `ext_funding`), consumed by: **Regime** (spread/vol regime), **Trade** (imbalance + taker flow at entry), **Risk** (depth-within-0.5% vs intended notional = slippage physics), **Critic** (symmetry check).
- v1.3 format (raw + timestamp + provenance), one line per symbol:
  `DEPTH BTC @2026-07-02T21:15Z (HL L2 + OKX, collector 15m): spread 0.8bps | depth±0.5%: bid $2.1M/ask $1.7M (imb +0.11 bid-heavy) | tape 15m: buy $4.2M/sell $3.8M, largest $310K | taker B/S 1.06, L/S accts 1.8 | NOTE: collecting since 2026-07-01 — no historical norms yet (n<1 week), treat levels as raw observation not signal.`
- Est. token cost: ~60-80 tokens/symbol/agent-call; 1 symbol × 4 agents ≈ **~300 tokens/decision** (~2-3% of a typical entry pipeline). Trend deltas (vs 6h ago) add ~20 tokens once ≥6h of data exists.
- Build note: also fixes WIRING invariant 7 (dataset consumed or muted) for the newest un-backfillable stream.

**F2. RECALL block — recent graded-thesis track record. Plumbing spec only; flag-gated OFF until 15-20 clean closes (HOLES H65, RESEARCH_AGENDA Q7).**
- Truth that exists: `bot/data/llm/thesis_history.jsonl` (279 theses + grades), `bot/data/thesis_grades.jsonl` (graded vs price — the §2b external anchor), decisions.jsonl outcomes. Nothing serves "here is how your last N graded theses on this symbol/side/regime scored" back to the agents.
- Spec (do NOT enable until 15-20 clean $5k-era closes, then **A/B on/off vs graded thesis accuracy** per §3b): flag `RECALL_BLOCK=true`; module `bot/llm/recall.py`; injected into **Trade + Critic** inputs via snapshot_builder key `recall`.
- v1.3 format: `RECALL (clean ledger v2026-07-02, era 07-02→): your last graded theses this symbol/side: 3 — [07-02 SOL LONG "reclaim" → CORRECT +$0.98 | 07-02 HYPE SHORT "exhaust" → WRONG -$1.48 | ...]. All-symbol graded: 7/12 directionally correct (n=12 — small-n, do not overweight). Base rate this regime: LONG 4/7, SHORT 3/5.`
- Est. token cost: ~120-200 tokens/decision (2 agents).
- Hard rules: clean-ledger only, denominators always, symmetric (show the opposite side's rate), provenance stamped, auto-mute if grader falls behind (>20% ungraded closes).

**F3. Funding/OI *history windows* beyond 8h.** Today: 8h momentum only (external_data :196+). funding_oi_history.jsonl holds 1,569+ records since 06-06. Add 24h/72h funding percentile + OI 24h change to `ext_funding_momentum` (same module, ~15 lines) — raw numbers with window labels, no interpretation beyond units. ~30 tokens. Low effort, do with F1.

**F4. Mechanical-gate would-have verdicts as context (unification of R1/R6/R21).** Once routers are demoted (Section 1), their outputs become one labeled block: `MECHANICAL OPINION (not enforced, for your information): conf floor 20 → pass; regime gater → would reject (range); FIT table → avoid(confidence_scorer/low_liq, static, no n); QB → muted (anti-signal 17% WR era).` ~50-70 tokens. This is what "all mechanical analysis goes through Claude" means for the analyses themselves.

Not missing (verified): funding rate, OI trend, basis, liquidation levels, regime nowcast, counterfactual/veto feedback (critic `veto_feedback` :4240-4248), calibration (`agent_cal` :4227-4236).

---

## 3. POST-DECHOKE VERIFICATION CHECKLIST (run BEFORE building Sections 1-2 on top)

For each item: run the grep; expected state; if not, it's a dechoke follow-up — fix before your lane proceeds.

| # | Dechoke item | Verify command / expectation |
|---|---|---|
| V1 | volume_chop gate | `grep -n "low_volume_chop" bot/strategies/ensemble.py` → only `low_volume_chop_observed_not_blocked` (informational, :504-509). No `return None` on volume. Confirm the annotated path (`evaluate_with_annotations` :1128) marks it severity=info not reject. |
| V2 | Dead gates deleted | Win-prob floor Gate 1f (signal_pipeline.py:383-439, M22 — doubly-dead read): `grep -n "win_prob" bot/core/signal_pipeline.py` → gate gone or advisory-only. `_quant_backtest_2026_03_26` dead key (refuted-claim cleanup): no live reader assumes it exists. |
| V3 | Slippage enforce (D9) | signal_pipeline.py:359-381: `grep -n "44.6" bot/core/signal_pipeline.py` — the >50%-of-stop hard reject must be shadow/advisory (counterfactual logged); threshold re-enable is OWNER-gated. |
| V4 | TOXIC enforce (D17) | multi_strategy_main.py:7686-7717: ALREADY SHADOW in f0549a3 (`TOXIC SHADOW (would have blocked, not enforcing)` + counterfactual + no return). Verify still true post-dechoke AND the deep_memory writer/reader key fix didn't silently re-arm any enforcement elsewhere: `grep -rn "_is_toxic" bot/ --include=*.py` → no `return`/block conditioned on it. |
| V5 | Quant mutations (D8) | coordinator.py:1275-1332 + :4764-4777: `grep -n "QUANT_ADJ\|QUANT_NOISE\|kelly_mult" bot/llm/agents/coordinator.py` → no mutation of trade_out/size_mult; values appear only in prompt-context builders or shadow logs. |
| V6 | Critic block (D8-adjacent / M19) | coordinator.py:4808-4819: `grep -n "challenge blocked" bot/llm/agents/coordinator.py` → gone (Critic verdict stands; vacc becomes Critic prompt context). Also :4740-4762 `risk_vacc` skip→reduce downgrade → gone or shadow. |
| V7 | Graduated-rules quarantine (D1) | `python -c "import json;rs=json.load(open('bot/data/llm/graduated_rules.json'));print(sum(1 for r in rs.get('rules',rs if isinstance(rs,list) else []) if isinstance(r,dict) and not r.get('ledger_version')))"` → 0 enforcing rules without provenance; keyword-parsed ~40 rules quarantined/shadow; hypothesis_tracker fast-track (n>=7) and INVALIDATED-graduation paths dead. |
| V8 | Quant boost rules M1 | signal_pipeline.py:117-160: Morning Edge ×1.2 / BTC-SHORT ×1.15 / HYPE-vol ×1.2 / conviction ×1.3 → shadow (1.0x applied, would-have logged); confirms R25's CB conf-override can no longer be reached by manufactured confidence. |
| V9 | Prompt threshold deletions (D4) | `grep -n "0.43\|ev_per_dollar<\|Hard to beat" bot/llm/agents/prompts.py` → skip-threshold prose gone; win_prob/ev lines labeled with provenance ("conf/100 deflated, IC≈0"). |
| V10 | Fossil-stat prompt purge (D5, M11) | `grep -n "101 LIVE TRADES\|3,802\|NEVER veto" bot/llm/agents/prompts.py` → gone; pinned tests updated. |
| V11 | No new unlabeled close / invariants | Run learning-engine spine verification (7/7) after dechoke restart; bot pid healthy; one full scan with `LLM-FIRST: ACTIVE` in the log. |

---

## 4. RE-BACKTEST SPEC — architecture A/B: C1'-C6' (new pipe) vs C1-C6 (old pipe, tonight)

**Baselines (old pipe, from REPLAY_RUN_C*.md / campaign state):** C1 trend-up +$16.18 (3 closes, 66.7% WR) | C2 dead market +$0.05 (1 close) | C3 trend-DOWN $0.00 (0 closes — SAT OUT, did not short) | C4 chop +$0.44 (2W0L) | C5 panic, C6 bear-drift = whatever REPLAY_CAMPAIGN_RESULTS.md records (C5 was running at map time — freeze the numbers there before C' launches).

**Held constant (the ONLY variable is pipeline architecture):**
- Windows: identical to `bot/tools/replay_campaign.py` WINDOWS table — C1' 2025-07-07→14, C2' 2026-04-04→11, C3' 2025-11-10→17, C4' 2025-06-07→14, C5' 2026-02-01→08, C6' 2026-06-20→27. Same symbols (BTC,ETH,SOL), equity $500, fee model `{taker 5bps/side, slip 3bps, funding 1bp/8h}`, cap 180 calls/window, sleep 15s, sequential driver, same isolation/sandbox protocol.
- Candle data: REUSE the C-run seeds — copy `bot/data/replay/seed_C1`, `seed_C3`, `seed_C4` (Coinbase-seeded) and each C<n>/sandbox candle cache into C<n>' so both arms replay bit-identical price paths. Record cache SHA-256 per window in the run doc.
- "Seeds": LLM outputs are non-deterministic (CLI, no temperature control) — the A/B is policy-vs-policy on identical data, not run-vs-run identity. Therefore: success margins must exceed single-run noise (see criteria), and the per-decision journal (replay_llm_journal.jsonl) is the tiebreaker evidence, not just PnL.
- Trigger filter: **keep the entry-event filter IDENTICAL for the primary A/B** (num_agree>=2 OR solo conf>=75 from the whitelist + 4h side-cooldown + per-symbol cap/3). Yes, the filter is old-pipe opinion — but changing it and the pipe simultaneously destroys the A/B. The doctrine-trigger question gets its own diagnostic run (below).

**What changes (the treatment):** the post-dechoke + Sections 1-2 pipe — routers removed (R3/R7/R12/R15-R18 etc.), voter suppression demoted (R21), depth feed F1 live (NOTE: depth history doesn't exist for 2025-2026 windows — the depth block auto-mutes on stale/absent data, so F1 is inert in replay; state this in every C' report), prompts de-steered.

**Success metric (pre-registered, per §1 evidence standard):**
1. Primary: **LLM-approved closes** (action=go from the coordinator, excluding exploration conversions and pipeline errors) — C' total > C total, at **equal-or-better expectancy**: (net PnL / closes)_C' >= (net PnL / closes)_C − $0.50 tolerance, and campaign net PnL_C' >= PnL_C.
2. Zero losing windows preserved (no window < −$2 net; C set the bar at zero losing windows).
3. **The C3 question (flagged, must be answered explicitly):** does C3' SHORT the −13% trend-down window? Report three numbers from the C3' journal + entry-event accounting: (a) short signals that qualified as entry events, (b) shorts that reached the coordinator, (c) shorts approved/entered and their PnL. Verdict table: C3 sat out because *filter starved shorts* (a≈0 while raw short signals existed) vs *brain declined shorts* (b>0, all skipped — read the theses) vs *brain shorted* (c>0). If (c)>0 with non-negative PnL → the new pipe restored the historical short skill; if the brain still declines with reasoned theses, that's a legitimate selective verdict — log it as such, not as failure.
4. Guardrail: skip-rate ordering preserved (C4' chop skip-rate > trend windows) — selectivity must survive the dechoke; if C4' starts churning entries, the removed gates were load-bearing and the finding goes to the owner before any live restart.
- Grading of every C' close feeds thesis_grades (sandbox copy) so RECALL's 15-20-clean-close counter does NOT count replay closes — live closes only.

**Diagnostic addendum (after the primary A/B, quota permitting):** C3'' — same window, doctrine trigger (ANY raw ensemble signal, first-bar-of-cluster, per-symbol cap/3, cap 240) — isolates the trigger-filter variable that the primary A/B holds constant. Compare C3' vs C3'' entry-event counts by side.

**Cost:** ~6 × 180 = ~1,080 calls (+240 diagnostic), ~12-14h wall in limit-window gaps, never parallel with live-bot quota — same discipline as C1-C6.

---

## 5. LANES — parallel-safe implementation split (no file owned by two lanes)

Precedence: L0 → L1 (unblocks everything) → L2+L3+L4 in parallel → L5 after L1-L3 land → L6 last.

| Lane | Mission | Owns (exclusive) | Key items |
|---|---|---|---|
| **L0 VERIFY** (first, read-only + micro-fixes) | Run Section 3 checklist; file dechoke follow-ups | no code ownership; may patch only files other lanes don't own yet (coordinate via this doc) | V1-V11 |
| **L1 ROUTERS** | Kill the divert conditions in the dispatch layer | `bot/multi_strategy_main.py` | R3 (solo conf 60), R7 (min(60,floor) divert), R4 (_PROVEN_SOLOS delete), R5 (loud fallback), R9 (cooldown logging + price escape), R20a/R20b (QB blocks delete), R21d (FIT annotate), R6 (gater → context), F4 market_ctx passthrough hook (single dict key `mech_opinion` — content built by L4's module) |
| **L2 COORDINATOR INTERIOR** | Opinion-free pipeline core | `bot/llm/agents/coordinator.py` | R12 (tier-1 delete), R13 (critic-fallback replacement), R15/R16 (quant mutations → context, if dechoke left residue), R17 (risk_vacc downgrade → prompt context), R18 (critic block removal residue), R11 (§2b provenance gate on pre-filter vetoes), wire `ext_depth`+`recall` keys into agent input builders (`_ensure_field` lines only) |
| **L3 ENSEMBLE EMANCIPATION** | Every strategy votes; suppression → annotation | `bot/strategies/ensemble.py`, `bot/data/symbol_strategy_profile.py` | R21a/R21b (allowlist + symbol profile → vote+annotate+shadow-log), R22 (opposing-vote metadata), M5 HYPE-bypass shadow (if not dechoked), D4 win_prob provenance labels at source (:2301-2421) |
| **L4 FEEDS** | New raw-truth inputs, v1.3-formatted | NEW `bot/llm/agents/market_depth.py`, NEW `bot/llm/recall.py`, `bot/llm/agents/external_data.py`, `bot/llm/snapshot_builder.py` | F1 depth reader + `ext_depth` (enable now), F3 funding windows, F2 RECALL module (built flag-OFF, `RECALL_BLOCK=false`, arms after 15-20 clean closes + A/B), F4 formatter (`format_mech_opinion()` consumed via L1's hook), staleness auto-mute on all |
| **L5 FALLBACK-PIPE HYGIENE** | The mechanical path that remains when LLM is truly unavailable | `bot/core/signal_pipeline.py`, `bot/execution/time_sizing.py` | Whatever V2/V3/V8 found un-dechoked: M1 shadow, D9 advisory, M13 sizing-tier recompute-or-1.0x, M22 delete, D11 neutralize, D19 keep-off. This path should shrink to: SafetyFilterChain + §2b-compliant rules + sizing physics |
| **L6 REPLAY A/B** | Section 4 execution | `bot/tools/replay_campaign.py`, `bot/tools/replay_harness.py`, `bot/backtest/llm_integration.py`, `coordination/REPLAY_RUN_C*_PRIME.md` | C'-window configs, seed reuse + SHA stamps, C3 side-accounting instrumentation, C3'' doctrine trigger (REPLAY-gated flag), REPLAY_AB_RESULTS.md |
| Shared/none | prompts | `bot/llm/agents/prompts.py`, `shared_context.py` are DECHOKE territory (D4/D5/M4/M6/M15/M18) — no lane above may edit them; if V9/V10 fail, the fix goes back to the dechoke agent or a dedicated prompts pass after all lanes land | — |

**Build order (ranked):** 1) L0 verify → 2) L1 R7+R3 (the doctrine's core sentence becomes true the moment these two die) → 3) L2 R18+R13 (un-gag the Critic honestly) → 4) L4 F1 depth (clock already running on live data value) + L3 in parallel → 5) L2/L1 remainder + L5 → 6) full-suite tests + live watch window (15-20 closes per §2) → 7) L6 re-backtest A/B → 8) verdict vs Section 4 criteria → owner report (results, not requests).

**Test gates per lane:** L1/L5 → `pytest tests/ -k "safety or pipeline or execution"` full-suite after; L2 → `pytest tests/ -k "agent or multi_agent"`; L3 → `-k "ensemble or strategy"`; L4 → new unit tests for staleness-mute + formatting (mirror test_mech_regime.py pattern); every lane: one live paper scan with invariants 7/7 before handoff.
