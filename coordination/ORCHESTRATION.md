# ORCHESTRATION RUNBOOK — how swarms run without junk
Owner mandate (2026-07-02): "have everything run seamlessly without junk." Every rule below was paid for tonight. Every future agent prompt inherits these.

## The junk classes and their kill-rules
1. **Watcher-stall / echo storms** (agent ends "standing by", re-fires empty notifications forever): agents NEVER launch-and-wait. Work only on what exists on disk; detached processes are monitored by the ENGINE CRON, not by the spawning agent. One bounded attempt max on anything external.
2. **Session-limit guillotine**: workflows must be resume-safe (cache-friendly structure); heavy work paced smoothly under the ceiling, never burst; when a limit hits: checkpoint, journal the resume command, exit clean.
3. **Parallel-edit collisions**: every swarm runs on LANES with exclusive file ownership declared up front (FULL_PIPE_BUILD_MAP pattern). Scoped `git add <files>` ALWAYS — `git add -A` is banned (it swept another agent's WIP once).
4. **Silent kill failures**: process kills via PowerShell CommandLine match + VERIFY-dead, never wmic loops (failed silently, caused a fought-over equity reset).
5. **Stale-read edit failures**: Read-tool the region before every Edit; shell peeks don't count.
6. **State surgery races**: bot STOPPED for any state-file edit; archive before modify; era markers on discontinuities.
7. **Self-damage**: pytest guards on prod writers stay sacred; replay/sandbox isolation proven by before/after fingerprints.
8. **Zero-token first**: deterministic scripts (check_invariants, gen_state) find problems for free; agents only investigate confirmed signal. Token spend targets trade/market data.
9. **Receipts or it didn't happen**: every agent ends with scoped commits; the coordinator pushes; unpushed work is invisible work.
10. **Milestones ping the owner** (SendUserFile proactive / push); junk never does.

## The standing loop (who watches what)
- ENGINE CRON (3h): health, spine verify, campaign/build monitoring, queue burn, STATE refresh, push.
- DETACHED PROCESSES (campaign/collector): watched by cron via their logs, not by agents.
- WORKFLOWS: fire-verify-land; resume via cached runId on any interruption.
