# LIVE READINESS — the gates between paper and real money
Created 2026-07-02 (owner: "get locked in... so I can actually use it to trade"). Every gate is measurable. Live goes on when ALL gates are green — not before, not on a feeling. THE_STANDARD v1.3 governs; flipping live is owner-only, always.

## GATE 1 — Correctness (zero known live-lethal bugs) — 🟡 ONE OPEN
- [x] LLM-first entries submit real exchange orders (fixed c9f9eed)
- [x] All closes/partials produce honest accounting (spine fixes, live-verified)
- [x] State survives restarts; one equity truth; pytest can't clobber prod
- [ ] **ORDER-SIDE INVERSION (H-crit): mechanical path passes LONG/SHORT to an executor that maps anything ≠"BUY" to SELL.** Paper masks it; live it could invert entries. MUST die before any live flip. (Autonomous fix — queued next.)
- [x] CLI-launcher churn resilience (auto-update killed calls mid-run — fixed 25ce6f3)

## GATE 2 — Demonstrated edge (the campaign + live sample) — 🟡 IN PROGRESS
- [ ] Replay campaign verdict: positive expectancy net of fees in ≥2 distinct regimes, zero catastrophic windows (4/6 windows done: +$16.67, 5W1L, no losing window yet)
- [ ] The C3 question answered: why no shorts in the trend-down window (long-only edge = rally-dependent; not crownable)
- [ ] Live clean sample ≥15-20 closes with new-era WR/expectancy ≥ replay's ballpark (7/15 so far)
- [ ] Thesis grader observed working on ≥5 LLM-approved closes (grades match reality)

## GATE 3 — Risk machinery proven under fire — 🟡 MOSTLY GREEN
- [x] Profit-lock ratchet: locked + harvested winners live (SOL/XRP, 2026-07-02)
- [x] Capped losses: every new-era loss ≤ $1.60
- [x] Circuit breaker: intact, never weakened (verified through every audit)
- [x] Exploration: 0.1x sized, through guards, cannot convert pipeline failures
- [ ] One full week of invariants ALL-CLEAR with zero manual interventions

## GATE 4 — Sizing discipline (the lesson of 2026-07-02) — 🔴 NOT STARTED (by design)
- [ ] Leverage stays 1.0x until: 15-20 clean closes confirm the 80+ conf band live, THEN the RQ16/20 Monte-Carlo ramp table governs each step (P(50% DD) < 5%)
- [ ] Per-trade notional cap so no single idea can be an "all-in" (the exact failure that cost $450 manually today — the bot must be structurally incapable of it)
- [x] Live capital PRE-COMMITTED (2026-07-02): first live deployment = $100-250, scaling only per the ramp table

## GATE 5 — Owner decisions cleared — 🟡 9 PENDING
- [ ] The 9 gate-stack decisions in RETURN_PACKAGE §3 answered (several directly affect entry quality: volume_chop input fix, dead-gate cleanup, quant-agent demotion)

## PRE-AUTHORIZATIONS (owner, 2026-07-02 — no round-trips needed)
- A/B validates -> PAPER sizing ramp per the Monte-Carlo table: STANDING AUTHORITY, ship immediately.
- First live capital: $100-250 (pre-committed; live flip itself remains owner-only).
- Notifications: EVERY MILESTONE proactively to phone (window completions, ships, verdicts) — not just emergencies.

## THE SEQUENCE FROM HERE
1. Fix the order-side inversion (next autonomous ship)
2. Campaign finishes (C5/C6 + synthesis) → answer C3 → edge verdict
3. Live sample accrues to 15-20 → calibration read
4. Owner answers the 9 → gate-stack cleaned
5. One quiet week, all green → **owner flips live with small capital, ramp table governs from there**

## The promise this document makes
The bot that goes live is one that CANNOT do what happened manually today: it cannot all-in, cannot revenge-rotate, cannot trade through a breaker, cannot lie to itself about a single close. That is the entire point.
