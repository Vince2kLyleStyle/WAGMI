"""
REPLAY CAMPAIGN driver — runs the 6-window regime campaign SEQUENTIALLY.

Plan: coordination/REPLAY_CAMPAIGN_PLAN.md (post-VAL1 entry-event triggering).
Each window is one tools/replay_harness.py run (its own sandbox under
bot/data/replay/C<n>/), cap 180 LLM calls, inter-pipeline sleep (quota
protection — the live bot's CLI calls have priority; never parallel bursts).

Features:
- Sequential windows C1..C6, each -> coordination/REPLAY_RUN_C<n>.md
  (written by the harness; includes closes, WR, PnL net fees, calls used,
  starved-signal count via the entry-event filter accounting).
- Campaign progress log: bot/data/replay/campaign.log — one line per LLM
  pipeline (window, symbol, decision) mirrored live from the sandbox journal,
  plus driver lifecycle events.
- RESUME: --resume skips windows already completed/skipped per
  bot/data/replay/campaign_state.json.
- Quota-limit resilience: on CLI-limit signatures (session/usage/rate limit,
  429, decision=none bursts) sleep 20 min and retry the window; after 3
  consecutive limit-pauses, checkpoint and exit cleanly with a RESUME note.
- Data resilience: a window whose candle fetch fails is skipped with a note.
- Final synthesis: coordination/REPLAY_CAMPAIGN_RESULTS.md aggregating all
  windows (closes by window/regime/side/source, WR, PnL net fees, live-era
  comparison, per-window fidelity caveats, verdict scaffold with numbers).

ISOLATION: writes only under bot/data/replay/ and coordination/*.md.
The production bot is never touched (each window's harness proves isolation
with a before/after production-data diff).

Usage (from bot/):
    python tools/replay_campaign.py                # full campaign
    python tools/replay_campaign.py --resume       # continue after a pause
    python tools/replay_campaign.py --synthesize-only
    # A/B arm (FULL_PIPE_BUILD_MAP §4): suffixed run dirs (C1p..), window
    # subsets for parallel instances, baseline candle-cache reuse + SHA stamp:
    python tools/replay_campaign.py --suffix p --windows C1,C2 --sleep 6
"""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent           # bot/
REPO_DIR = BOT_DIR.parent                                   # WAGMI/
REPLAY_ROOT = BOT_DIR / "data" / "replay"
CAMPAIGN_LOG = REPLAY_ROOT / "campaign.log"
STATE_PATH = REPLAY_ROOT / "campaign_state.json"
RESULTS_MD = REPO_DIR / "coordination" / "REPLAY_CAMPAIGN_RESULTS.md"

# Windows per REPLAY_CAMPAIGN_PLAN.md §2.3 (W1..W6 -> C1..C6)
WINDOWS = [
    ("C1", "2025-07-07", "2025-07-14", "trend-UP clean (+10.8%, ER 0.98)"),
    ("C2", "2026-04-04", "2026-04-11", "trend-UP current era (+8.5%, ER 0.73)"),
    ("C3", "2025-11-10", "2025-11-17", "trend-DOWN clean (-13.0%, ER 0.88)"),
    ("C4", "2025-06-07", "2025-06-14", "pure CHOP low vol (-0.1%, ER 0.01)"),
    ("C5", "2026-02-01", "2026-02-08", "HIGH-VOL panic (-8.6%, ER 0.24)"),
    ("C6", "2026-06-20", "2026-06-27", "bear drift, VAL1 A/B rerun (-5.4%, ER 0.49)"),
]

# CLI quota-limit signatures (careful: NOT bare "exhausted" — the runner
# prints "budget exhausted" for the USD budget, which is not a quota event).
LIMIT_SIGNATURES = (
    "session limit", "usage limit", "rate limit", "rate-limit", "429",
    "too many requests", "overloaded", "cli session limit hit",
    "limit reached", "hit the limit", "quota",
)
DATA_FAIL_SIGNATURES = (
    "no usable data", "preflight_failed",
)

MAX_CONSECUTIVE_LIMIT_PAUSES = 3
LIMIT_PAUSE_S = 20 * 60
NONE_BURST_N = 5           # N consecutive decision=none entry pipelines = burst
POLL_INTERVAL_S = 20
INTER_WINDOW_SLEEP_S = 60  # let the live bot's quota breathe between windows


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clog(line: str):
    """Append one line to the campaign log (and echo to stdout)."""
    msg = f"{_utcnow()} {line}"
    print(msg, flush=True)
    try:
        REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
        with open(CAMPAIGN_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"windows": {}}


def save_state(state: dict):
    state["updated"] = _utcnow()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _read_jsonl(path: Path) -> list:
    rows = []
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
    return rows


def _tail_text(path: Path, max_bytes: int = 80_000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", errors="replace").lower()
    except OSError:
        return ""


def _has_limit_signature(text: str) -> bool:
    return any(sig in text for sig in LIMIT_SIGNATURES)


def _sha256_files(paths) -> str:
    """Order-stable SHA-256 over a set of files (name + bytes)."""
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.name):
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _resolve_ab_seed(base_id: str) -> Path | None:
    """For a suffixed (A/B arm) window, find the baseline's candle cache so
    both arms replay bit-identical price paths (FULL_PIPE_BUILD_MAP §4):
    prefer the original seed dir, else the baseline run's sandbox cache."""
    for cand in (REPLAY_ROOT / f"seed_{base_id}",
                 REPLAY_ROOT / base_id / "sandbox" / "data" / "cache"):
        if cand.is_dir() and any(cand.glob("*.csv")):
            return cand
    return None


# ── Window runner ────────────────────────────────────────────────────


class WindowResult:
    def __init__(self, status: str, note: str = ""):
        self.status = status  # done | limit | skipped_data | failed
        self.note = note


def try_seed(win_id: str, end: str, args) -> Path | None:
    """Pre-fetch the window's candles from Coinbase spot (free) into the
    fetcher disk-cache format. Rescues windows beyond exchange history depth
    (HL 1h depth ~208d; Bybit geo-blocked; Kraken last-720-only)."""
    seed_dir = REPLAY_ROOT / f"seed_{win_id}"
    cmd = [sys.executable, str(BOT_DIR / "tools" / "replay_seed_candles.py"),
           "--end", end, "--days", "11", "--symbols", args.symbols,
           "--out", str(seed_dir)]
    seed_log = REPLAY_ROOT / f"seed_{win_id}.log"
    with open(seed_log, "a", encoding="utf-8", errors="replace") as logf:
        rc = subprocess.call(cmd, cwd=str(BOT_DIR),
                             stdout=logf, stderr=subprocess.STDOUT)
    if rc == 0:
        return seed_dir
    clog(f"{win_id} SEED_FAILED rc={rc} (see {seed_log.name})")
    return None


def run_window(win_id: str, start: str, end: str, regime: str,
               args, seed_dir: Path = None) -> WindowResult:
    """Run one window via the harness; mirror pipelines to campaign.log."""
    run_dir = REPLAY_ROOT / win_id
    journal = run_dir / "sandbox" / "data" / "replay_llm_journal.jsonl"
    harness_log = REPLAY_ROOT / f"harness_{win_id}.log"

    cmd = [
        sys.executable, str(BOT_DIR / "tools" / "replay_harness.py"),
        "--start", start, "--end", end,
        "--symbols", args.symbols,
        "--equity", str(args.equity),
        "--budget", str(args.budget),
        "--max-llm-calls", str(args.cap),
        "--sleep", str(args.sleep),
        "--run-id", win_id,
        "--timeout-min", str(args.timeout_min),
    ]
    if seed_dir is not None:
        cmd += ["--seed-cache-dir", str(seed_dir)]
    clog(f"{win_id} WINDOW_START {start}->{end} regime=[{regime}] "
         f"cap={args.cap} sleep={args.sleep}s"
         f"{' seeded=coinbase_spot' if seed_dir else ''}")

    seen = 0                 # journal entries already mirrored
    recent_entry_decisions = []
    killed_for_burst = False

    with open(harness_log, "a", encoding="utf-8", errors="replace") as logf:
        proc = subprocess.Popen(
            cmd, cwd=str(BOT_DIR), stdout=logf, stderr=subprocess.STDOUT)
        while proc.poll() is None:
            time.sleep(POLL_INTERVAL_S)
            rows = _read_jsonl(journal)
            for rec in rows[seen:]:
                kind = rec.get("kind", "?")
                if kind == "entry":
                    decision = rec.get("decision", "?")
                    clog(f"{win_id} {rec.get('symbol', '?')} entry "
                         f"decision={decision} "
                         f"conf={rec.get('decision_confidence')} "
                         f"regime={rec.get('regime')} "
                         f"calls={rec.get('total_calls')}/{rec.get('cap')}")
                    recent_entry_decisions.append(decision)
                else:
                    clog(f"{win_id} {rec.get('symbol', '?')} {kind} "
                         f"calls={rec.get('total_calls')}/{rec.get('cap')}")
            seen = len(rows)
            # decision=none burst = CLI failing under us (limit / exit-1 burst)
            tail = recent_entry_decisions[-NONE_BURST_N:]
            if len(tail) == NONE_BURST_N and all(d == "none" for d in tail):
                clog(f"{win_id} NONE_BURST {NONE_BURST_N} consecutive "
                     f"pipelines returned no decision — killing window "
                     f"(suspected CLI limit)")
                proc.kill()
                killed_for_burst = True
                break
        rc = proc.wait()

    # Mirror any journal entries written after the last poll
    for rec in _read_jsonl(journal)[seen:]:
        if rec.get("kind") == "entry":
            clog(f"{win_id} {rec.get('symbol', '?')} entry "
                 f"decision={rec.get('decision', '?')} "
                 f"calls={rec.get('total_calls')}/{rec.get('cap')}")

    if killed_for_burst:
        return WindowResult("limit", "decision=none burst (suspected CLI limit)")

    # Classify the outcome
    log_text = _tail_text(run_dir / "run.log") + _tail_text(harness_log)
    results_path = run_dir / "sandbox" / "replay_out" / "results.json"
    results = {}
    if results_path.exists():
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    if isinstance(results, dict) and results.get("error"):
        err_blob = (json.dumps(results) + log_text).lower()
        if _has_limit_signature(err_blob):
            return WindowResult("limit", f"preflight/limit: {results.get('errors')}")
        if any(sig in err_blob for sig in DATA_FAIL_SIGNATURES):
            return WindowResult("skipped_data",
                                f"candle fetch/preflight failed: {results.get('errors')}")
        return WindowResult("failed", f"engine error: {results.get('errors')}")

    if rc != 0 or not results:
        if _has_limit_signature(log_text):
            return WindowResult("limit", f"rc={rc}, limit signature in logs")
        if any(sig in log_text for sig in DATA_FAIL_SIGNATURES):
            return WindowResult("skipped_data", f"rc={rc}, no usable candle data")
        return WindowResult("failed", f"rc={rc}, no results.json")

    # Completed run — sanity-check for a mid-run limit that silently zeroed it
    summary = {}
    summary_path = run_dir / "sandbox" / "replay_out" / "llm_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    failures = summary.get("llm_failures", 0)
    calls = summary.get("llm_calls", 0)
    if failures >= 5 and failures > 0.4 * max(calls + failures, 1) \
            and _has_limit_signature(log_text):
        return WindowResult("limit",
                            f"{failures} LLM failures / {calls} calls + limit signature")

    n_closes = 0
    trades_csv = run_dir / "replay_trades.csv"
    if trades_csv.exists():
        with open(trades_csv, encoding="utf-8") as f:
            n_closes = max(0, sum(1 for _ in f) - 1)
    clog(f"{win_id} WINDOW_DONE closes={n_closes} llm_calls={calls} "
         f"failures={failures} "
         f"entry_events={summary.get('replay_entry_events')} "
         f"starved={summary.get('replay_starved_events')}")
    return WindowResult("done", f"closes={n_closes} calls={calls}")


# ── Synthesis ────────────────────────────────────────────────────────


def _load_trades(win_id: str) -> list:
    path = REPLAY_ROOT / win_id / "replay_trades.csv"
    trades = []
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                trades = list(csv.DictReader(f))
        except OSError:
            pass
    return trades


def _stats(trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "wr": 0.0, "pnl": 0.0, "fees": 0.0}
    wins = sum(1 for t in trades if float(t.get("pnl", 0) or 0) > 0)
    return {
        "n": n, "wins": wins, "wr": round(wins / n * 100, 1),
        "pnl": round(sum(float(t.get("pnl", 0) or 0) for t in trades), 2),
        "fees": round(sum(float(t.get("fees", 0) or 0) for t in trades), 2),
    }


def _group(trades: list, key: str) -> dict:
    groups = {}
    for t in trades:
        groups.setdefault(t.get(key) or "unknown", []).append(t)
    return {k: _stats(v) for k, v in sorted(groups.items())}


def _live_era_stats() -> dict:
    """Read-only look at the production ledger for comparison."""
    path = BOT_DIR / "data" / "trades.csv"
    try:
        with open(path, encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        return _stats(trades)
    except OSError:
        return {}


def _coverage_note(win_id: str) -> str:
    """Extract the 5m data-coverage facts from the window's run.log."""
    text = ""
    try:
        text = (REPLAY_ROOT / win_id / "run.log").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return "run.log missing"
    lines = [ln.strip() for ln in text.splitlines() if " 5m:" in ln]
    if not lines:
        return "no 5m coverage info — assume 1h-touch fills only"
    return "; ".join(lines[:3])


def synthesize(state: dict, args, windows=None):
    """Write the campaign results MD aggregating all selected windows."""
    windows = windows if windows is not None else WINDOWS
    suffix = getattr(args, "suffix", "") or ""
    all_trades = []
    win_rows = []
    for base_id, start, end, regime in windows:
        win_id = base_id + suffix
        wstate = state.get("windows", {}).get(win_id, {})
        status = wstate.get("status", "not_run")
        trades = _load_trades(win_id) if status == "done" else []
        for t in trades:
            t["_window"] = win_id
        all_trades.extend(trades)

        summary = {}
        spath = REPLAY_ROOT / win_id / "sandbox" / "replay_out" / "llm_summary.json"
        if spath.exists():
            try:
                summary = json.loads(spath.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                summary = {}
        st = _stats(trades)
        win_rows.append({
            "id": win_id, "base": base_id,
            "window": f"{start} -> {end}", "regime": regime,
            "status": status, "note": wstate.get("note", ""),
            "seeded": wstate.get("seeded", False),
            "stats": st,
            "calls": summary.get("llm_calls", 0),
            "entry_events": summary.get("replay_entry_events", "n/a"),
            "starved": summary.get("replay_starved_events", "n/a"),
            "cooldown": summary.get("replay_cooldown_skips", "n/a"),
            "final_equity": summary.get("final_equity", None),
            "coverage": _coverage_note(win_id) if status == "done" else "",
        })

    tot = _stats(all_trades)
    live = _live_era_stats()

    lines = [
        f"# REPLAY_CAMPAIGN_RESULTS{('_' + suffix) if suffix else ''} — "
        f"{len(windows)}-window regime campaign",
        f"Generated {_utcnow()} by tools/replay_campaign.py "
        "(THE_STANDARD v1.3 compliant reporting)",
        *([f"Arm: suffix='{suffix}' windows={[r['id'] for r in win_rows]} "
           "(architecture A/B per FULL_PIPE_BUILD_MAP §4 — identical "
           "windows/seeds/fees/caps; only the pipeline architecture varies; "
           "seed/cache SHA-256 stamps in campaign log + state)"]
          if (suffix or len(windows) != len(WINDOWS)) else []),
        "",
        "## Question under test",
        "Does the repaired brain show ENTRY edge in ANY regime? "
        "(numbers below; small-n humility mandatory — no vibes)",
        "",
        "## Per-window results",
        "| Win | Window | Regime | Status | Closes | WR | PnL net fees | "
        "Calls | Entry events | Starved | Cooldown-suppressed |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in win_rows:
        s = r["stats"]
        lines.append(
            f"| {r['id']} | {r['window']} | {r['regime']} | {r['status']} | "
            f"{s['n']} | {s['wr']}% ({s['wins']}W/{s['n'] - s['wins']}L) | "
            f"${s['pnl']:+.2f} (fees ${s['fees']:.2f}) | {r['calls']} | "
            f"{r['entry_events']} | {r['starved']} | {r['cooldown']} |")
    for r in win_rows:
        if r["status"] not in ("done",) and r["note"]:
            lines.append(f"- {r['id']} note: {r['note']}")

    lines += [
        "",
        "## Aggregate (all completed windows)",
        f"- Closes: {tot['n']} | WR {tot['wr']}% "
        f"({tot['wins']}W/{tot['n'] - tot['wins']}L) | "
        f"PnL net fees ${tot['pnl']:+.2f} | fees ${tot['fees']:.2f} "
        f"(equity ${args.equity:.0f}/window)",
        f"- By regime: {json.dumps(_group(all_trades, 'regime'))}",
        f"- By side: {json.dumps(_group(all_trades, 'side'))}",
        f"- By source (strategy): {json.dumps(_group(all_trades, 'strategy'))}",
        f"- By window: {json.dumps(_group(all_trades, '_window'))}",
        "",
        "## Comparison vs live-era ledger (bot/data/trades.csv, read-only)",
        (f"- Live era: n={live.get('n')} WR {live.get('wr')}% "
         f"PnL ${live.get('pnl', 0):+.2f} (fees ${live.get('fees', 0):.2f}) — "
         "NOTE: different eras, different call caps, live carries accumulated "
         "memory the replay brain lacks; directional comparison only."
         if live else "- Live ledger unavailable at synthesis time."),
        "",
        "## Fidelity caveats (per window, honest)",
    ]
    for r in win_rows:
        if r["status"] == "done":
            seeded_note = (" | CANDLES: Coinbase SPOT proxy (perp history "
                           "unreachable for this era)" if r["seeded"] else "")
            lines.append(f"- {r['id']}: 5m depth: {r['coverage']}{seeded_note}")
    lines += [
        "- Empty-memory brain; candle-only prompts (no funding/OI "
        "reconstruction); LLM non-determinism — one policy sample per window.",
        "- No exploration path in replay: this measures the SELECTIVE policy "
        "only, not the live epsilon mix.",
        "- REPLAY_MODE forced-skip: post-cap/pre-filter signals never trade — "
        "the sample is 100% LLM-approved entries.",
        "",
        "## Verdict scaffold (fill = numbers above)",
    ]
    grouped = _group(all_trades, "_window")
    positive = [w for w, s in grouped.items() if s["pnl"] > 0 and s["n"] >= 3]
    negative = [w for w, s in grouped.items() if s["pnl"] < 0 and s["n"] >= 3]
    chop_row = next((r for r in win_rows if r["base"] == "C4"), None)
    lines += [
        f"- Windows with positive PnL at n>=3: {positive or 'NONE'}",
        f"- Windows with negative PnL at n>=3: {negative or 'NONE'}",
        f"- Chop test (C4 should go quiet): "
        f"{chop_row['stats']['n'] if chop_row else '?'} closes, "
        f"{chop_row['entry_events'] if chop_row else '?'} entry events",
        f"- Success criteria (plan §2.4): (i) >0 closes in C1-C3/C5: "
        f"{[r['id'] for r in win_rows if r['base'] in ('C1', 'C2', 'C3', 'C5') and r['stats']['n'] > 0] or 'NOT MET'}; "
        f"(ii) C4 quiet vs trend windows: see table; "
        f"(iii) per-regime WR/PF vs live ranging-entry pathology: see by-regime; "
        f"(iv) isolation: see per-window REPLAY_RUN_C*.md isolation sections.",
        "- HONEST BOTTOM LINE: with ~5-10 closes/window, no single window is "
        "statistically decisive; treat direction + regime contrast as the "
        "signal, and require live confirmation before any policy change.",
        "",
        f"Artifacts: bot/data/replay/{{{','.join(r['id'] for r in win_rows)}}}/, "
        f"{CAMPAIGN_LOG.name}, coordination/REPLAY_RUN_<id>.md",
    ]
    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")
    clog(f"SYNTHESIS written -> {RESULTS_MD}")


# ── Main ─────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="6-window replay campaign driver")
    ap.add_argument("--symbols", default="BTC,ETH,SOL")
    ap.add_argument("--equity", type=float, default=500.0)
    ap.add_argument("--budget", type=float, default=15.0,
                    help="USD budget per window (cap is the real limiter)")
    ap.add_argument("--cap", type=int, default=180,
                    help="LLM call cap per window (plan §2.2)")
    ap.add_argument("--sleep", type=float, default=15.0,
                    help="inter-pipeline sleep seconds (quota protection)")
    ap.add_argument("--timeout-min", type=int, default=300)
    ap.add_argument("--resume", action="store_true",
                    help="skip windows already completed/skipped")
    ap.add_argument("--synthesize-only", action="store_true")
    ap.add_argument("--suffix", default="",
                    help="A/B arm suffix for run dirs + reports (e.g. 'p' -> "
                         "run dir C1p, coordination/REPLAY_RUN_C1p.md); also "
                         "suffixes campaign log/state/results so instances "
                         "never collide with the baseline campaign")
    ap.add_argument("--windows", default="",
                    help="comma-separated subset of window ids to run "
                         "(e.g. C1,C2); default = all windows")
    args = ap.parse_args()

    sel = [w.strip().upper() for w in args.windows.split(",") if w.strip()]
    known = {w[0] for w in WINDOWS}
    unknown = [w for w in sel if w not in known]
    if unknown:
        print(f"unknown --windows ids: {unknown} (known: {sorted(known)})",
              file=sys.stderr)
        return 2
    windows = [w for w in WINDOWS if not sel or w[0] in sel]

    # Per-instance log/state/results so parallel A/B instances and the
    # baseline campaign never write over each other. Defaults unchanged.
    global CAMPAIGN_LOG, STATE_PATH, RESULTS_MD
    if args.suffix or sel:
        tag = f"{args.suffix}{('_' + '-'.join(sel)) if sel else ''}"
        CAMPAIGN_LOG = REPLAY_ROOT / f"campaign_{tag}.log"
        STATE_PATH = REPLAY_ROOT / f"campaign_state_{tag}.json"
        RESULTS_MD = (REPO_DIR / "coordination"
                      / f"REPLAY_CAMPAIGN_RESULTS_{tag}.md")

    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    state = load_state()

    if args.synthesize_only:
        # Aggregate synthesis across parallel instances of the same arm:
        # merge every campaign_state_<suffix>*.json window record.
        if args.suffix:
            for p in sorted(REPLAY_ROOT.glob(
                    f"campaign_state_{args.suffix}*.json")):
                try:
                    other = json.loads(p.read_text(encoding="utf-8"))
                    for k, v in other.get("windows", {}).items():
                        state.setdefault("windows", {}).setdefault(k, v)
                except (OSError, json.JSONDecodeError):
                    pass
        synthesize(state, args, windows)
        return 0

    clog(f"CAMPAIGN_START windows={[w[0] + args.suffix for w in windows]} "
         f"cap={args.cap} sleep={args.sleep}s resume={args.resume} "
         f"suffix={args.suffix!r} pid={os.getpid()}")

    consecutive_limit_pauses = 0
    for base_id, start, end, regime in windows:
        win_id = base_id + args.suffix
        wstate = state.setdefault("windows", {}).setdefault(win_id, {})
        # Resume skips only DONE windows: skipped_data windows get another
        # chance via the Coinbase seed path.
        if args.resume and wstate.get("status") == "done":
            clog(f"{win_id} RESUME_SKIP already done")
            continue

        seed_dir = None
        attempted_seed = False
        # A/B arm: reuse the BASELINE window's candles (seed dir or its
        # sandbox cache) so both arms replay bit-identical price paths;
        # stamp the cache SHA-256 (launch gate: no stamp, no A/B).
        if args.suffix:
            ab_seed = _resolve_ab_seed(base_id)
            if ab_seed is not None:
                seed_dir = ab_seed
                attempted_seed = True
                sha = _sha256_files(ab_seed.glob("*.csv"))
                clog(f"{win_id} SEED_REUSE {ab_seed} sha256={sha} "
                     f"(bit-identical price path vs {base_id} baseline)")
                wstate["seed_source"] = str(ab_seed)
                wstate["seed_sha256"] = sha
            else:
                clog(f"{win_id} SEED_MISSING no baseline cache for {base_id} "
                     f"— will fetch (deterministic historical candles; "
                     f"cache SHA stamped post-run)")
        while True:
            result = run_window(win_id, start, end, regime, args,
                                seed_dir=seed_dir)

            if result.status == "skipped_data" and not attempted_seed:
                attempted_seed = True
                clog(f"{win_id} DATA_FALLBACK exchange history insufficient — "
                     f"seeding Coinbase spot candles")
                seed_dir = try_seed(win_id, end, args)
                if seed_dir is not None:
                    continue  # retry the window with seeded cache

            if result.status == "limit":
                consecutive_limit_pauses += 1
                clog(f"{win_id} LIMIT_PAUSE #{consecutive_limit_pauses} "
                     f"({result.note}) — sleeping {LIMIT_PAUSE_S // 60} min")
                if consecutive_limit_pauses >= MAX_CONSECUTIVE_LIMIT_PAUSES:
                    wstate.update(status="limit_checkpoint", note=result.note)
                    save_state(state)
                    clog(f"CAMPAIGN_PAUSED {consecutive_limit_pauses} "
                         f"consecutive limit-pauses at {win_id}. RESUME with: "
                         f"python tools/replay_campaign.py --resume")
                    return 0
                time.sleep(LIMIT_PAUSE_S)
                continue  # retry the same window

            consecutive_limit_pauses = 0
            wstate.update(status=result.status, note=result.note,
                          window=f"{start}->{end}", regime=regime,
                          seeded=seed_dir is not None)
            if result.status == "done":
                cache = (REPLAY_ROOT / win_id / "sandbox" / "data" / "cache")
                cache_files = list(cache.glob("*.csv")) if cache.is_dir() else []
                if cache_files:
                    sha = _sha256_files(cache_files)
                    wstate["cache_sha256"] = sha
                    clog(f"{win_id} CACHE_SHA256 {sha} "
                         f"({len(cache_files)} files)")
            save_state(state)
            if result.status == "skipped_data":
                clog(f"{win_id} WINDOW_SKIPPED_DATA {result.note}")
            elif result.status == "failed":
                clog(f"{win_id} WINDOW_FAILED {result.note} — continuing")
            break

        time.sleep(INTER_WINDOW_SLEEP_S)

    synthesize(state, args, windows)
    clog("CAMPAIGN_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
