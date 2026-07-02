#!/usr/bin/env python3
"""V-TRUE A/B verdict — brain-vs-cage discrimination scoring.

Computes the pre-registered A/B verdict (THOUGHT_JOURNAL 2026-07-02 ~23:30Z
directive + FULL_PIPE_BUILD_MAP §4) between the baseline campaign runs
(C1..C6, old pipe) and the V-TRUE re-campaign runs (C1t..C6t, post-dechoke
full pipe). Runs on whatever subset of run dirs exists and lists what is
missing — safe to run mid-campaign.

Metrics (per the 23:30 owner epistemology — "selection != blanket refusal"):
  1. DISCRIMINATION SPREAD: forward return of brain-APPROVED (go/proceed)
     signals minus brain-REJECTED (flat/skip) signals at +6h/+12h/+24h from
     decision sim-time, per window + pooled. The cage scores 0 by
     construction (a wall doesn't choose).
  2. POSITIVE-SUBSET TEST: does the approved subset clear fees
     (net-positive expectancy after the round-trip taker cost)? Any positive
     subset strictly dominates the cage (which forfeits everything).
  3. Aggregates: closes / WR / net PnL per window per architecture
     (replay_trades.csv is ground truth; REPLAY_RUN_C*.md cross-checked),
     plus the C3-shorts three-way verdict (filter-starved vs brain-declined
     vs shorted).
  4. VERDICT: brain beats cage iff pooled spread > 0 (honest n stated) AND
     the approved subset is net-positive in >= 2 regimes (windows).

Data fidelity:
  - V-TRUE journals (post-ddd2fdf) carry signal_side / signal_entry /
    signal_confidence / sim_ts per entry-decision — exact scoring.
  - BASELINE journals LACK those fields. The rejected side is RECONSTRUCTED:
    each journaled decision is joined to the nearest-preceding
    SIGNAL_GENERATED event (trade_events.jsonl) for the same symbol
    (side / entry price), and the decision's sim-time is recovered by
    matching the signal entry price against the run's own 1h candle-cache
    closes, constrained to the replay window and per-symbol monotonic sim
    time. This is LOWER FIDELITY (burst signals within one candle can alias;
    price-match can be ambiguous) — unresolved decisions are counted and
    excluded, and every baseline number is flagged "reconstructed".
  - Forward returns use the run's own sandbox candle cache first; where the
    cache ends before sim_ts + horizon, post-window candles are fetched from
    the Hyperliquid candleSnapshot API and cached under
    bot/tools/research/candle_cache/. (Note: 2025 baseline windows were
    Coinbase-seeded; HL post-window continuation is a second, smaller
    fidelity caveat and is source-labeled.)

Usage:
  python bot/tools/research/ab_verdict.py                 # markdown verdict
  python bot/tools/research/ab_verdict.py --json          # machine output
  python bot/tools/research/ab_verdict.py --windows C1,C3 --treat-suffix t
  python bot/tools/research/ab_verdict.py --no-fetch      # cache-only

Fail-soft everywhere: a broken/missing window degrades to a warning line,
never a crash.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT_ROOT = HERE.parent.parent                      # bot/
REPLAY_ROOT = BOT_ROOT / "data" / "replay"
COORD_ROOT = BOT_ROOT.parent / "coordination"
FETCH_CACHE_DIR = HERE / "candle_cache"

# Mirrors bot/tools/replay_campaign.py WINDOWS (held constant across arms).
WINDOWS = [
    ("C1", "2025-07-07", "2025-07-14", "trend-UP clean (+10.8%, ER 0.98)"),
    ("C2", "2026-04-04", "2026-04-11", "trend-UP current era (+8.5%, ER 0.73)"),
    ("C3", "2025-11-10", "2025-11-17", "trend-DOWN clean (-13.0%, ER 0.88)"),
    ("C4", "2025-06-07", "2025-06-14", "pure CHOP low vol (-0.1%, ER 0.01)"),
    ("C5", "2026-02-01", "2026-02-08", "HIGH-VOL panic (-8.6%, ER 0.24)"),
    ("C6", "2026-06-20", "2026-06-27", "bear drift (-5.4%, ER 0.49)"),
]

HORIZONS_H = (6, 12, 24)
APPROVED = {"go", "proceed"}
REJECTED = {"flat", "skip"}
DEFAULT_FEE_RT_BPS = 10.4   # 5bps taker/side x2 + funding drift; see fee_model
HL_API = "https://api.hyperliquid.xyz/info"

# Entry-event filter constants (mirrors llm_integration._replay_is_entry_event)
SOLO_WHITELIST = {"regime_trend", "bollinger_squeeze", "vmc_cipher",
                  "mean_reversion"}
SOLO_CONF_MIN = 75.0

WARNINGS: list[str] = []


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"[warn] {msg}", file=sys.stderr)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M")


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        warn(f"cannot read {path.name}: {e}")
    return rows


# ── Candle store ──────────────────────────────────────────────────────


class CandleStore:
    """1h closes per symbol: run-local sandbox cache first, HL API fallback
    (fetched candles cached under bot/tools/research/candle_cache/)."""

    def __init__(self, allow_fetch: bool = True):
        self.allow_fetch = allow_fetch
        self.local: dict[str, dict[float, float]] = {}   # sym -> epoch->close
        self.fetched: dict[str, dict[float, float]] = {}
        self.fetch_count = 0
        self.fetch_fail: set[str] = set()

    def load_run_cache(self, run_dir: Path) -> None:
        cache_dir = run_dir / "sandbox" / "data" / "cache"
        if not cache_dir.is_dir():
            warn(f"{run_dir.name}: no sandbox candle cache at {cache_dir}")
            return
        for p in sorted(cache_dir.glob("*_1h_*.csv")):
            sym = p.name.split("_")[0]
            store = self.local.setdefault(sym, {})
            try:
                with open(p, encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        try:
                            ts = _epoch(_parse_iso(row["time"]))
                            store[ts] = float(row["close"])
                        except (KeyError, ValueError):
                            continue
            except OSError as e:
                warn(f"cannot read candle cache {p.name}: {e}")

    def _disk_cache_path(self, sym: str) -> Path:
        return FETCH_CACHE_DIR / f"HL_{sym}_1h.json"

    def _load_disk_cache(self, sym: str) -> dict[float, float]:
        if sym in self.fetched:
            return self.fetched[sym]
        d: dict[float, float] = {}
        p = self._disk_cache_path(sym)
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                d = {float(k): float(v) for k, v in raw.items()}
            except (OSError, json.JSONDecodeError, ValueError) as e:
                warn(f"bad fetch-cache {p.name}: {e}")
        self.fetched[sym] = d
        return d

    def _save_disk_cache(self, sym: str) -> None:
        try:
            FETCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p = self._disk_cache_path(sym)
            p.write_text(json.dumps(
                {str(int(k)): v for k, v in self.fetched[sym].items()}),
                encoding="utf-8")
        except OSError as e:
            warn(f"cannot write fetch-cache for {sym}: {e}")

    def _hl_fetch(self, sym: str, start_epoch: float, end_epoch: float) -> None:
        """Fetch 1h candles [start, end] from HL candleSnapshot; merge+cache."""
        if not self.allow_fetch or sym in self.fetch_fail:
            return
        body = json.dumps({
            "type": "candleSnapshot",
            "req": {"coin": sym, "interval": "1h",
                    "startTime": int(start_epoch * 1000),
                    "endTime": int(end_epoch * 1000)},
        }).encode()
        req = urllib.request.Request(
            HL_API, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001 — fail-soft by design
            warn(f"HL fetch failed for {sym}: {e}")
            self.fetch_fail.add(sym)
            return
        store = self._load_disk_cache(sym)
        n = 0
        for c in data or []:
            try:
                store[float(c["t"]) / 1000.0] = float(c["c"])
                n += 1
            except (KeyError, TypeError, ValueError):
                continue
        self.fetch_count += 1
        if n:
            self._save_disk_cache(sym)

    def close_at(self, sym: str, epoch: float) -> tuple[float | None, str]:
        """Close of the 1h candle stamped `epoch` (exact hourly grid).
        Returns (close, source) with source in {cache, hl_fetch, missing}."""
        epoch = math.floor(epoch / 3600.0) * 3600.0
        c = self.local.get(sym, {}).get(epoch)
        if c is not None:
            return c, "cache"
        disk = self._load_disk_cache(sym)
        c = disk.get(epoch)
        if c is not None:
            return c, "hl_fetch"
        # fetch a 3-day pad around the gap once, then retry
        self._hl_fetch(sym, epoch - 3600, epoch + 72 * 3600)
        c = self.fetched.get(sym, {}).get(epoch)
        if c is not None:
            return c, "hl_fetch"
        return None, "missing"


# ── Decision extraction ───────────────────────────────────────────────


def load_journal_decisions(run_dir: Path) -> list[dict]:
    """kind=entry journal rows with decision in APPROVED|REJECTED."""
    jp = run_dir / "sandbox" / "data" / "replay_llm_journal.jsonl"
    if not jp.exists():
        warn(f"{run_dir.name}: journal missing ({jp})")
        return []
    out = []
    for r in _read_jsonl(jp):
        if r.get("kind") != "entry":
            continue
        dec = str(r.get("decision") or "").lower()
        if dec in APPROVED:
            r["_verdict"] = "approved"
        elif dec in REJECTED:
            r["_verdict"] = "rejected"
        else:
            continue  # 'none' = pipeline error, not a choice
        out.append(r)
    return out


def load_signal_events(run_dir: Path) -> list[dict]:
    ep = run_dir / "sandbox" / "data" / "trade_events.jsonl"
    if not ep.exists():
        return []
    evs = [e for e in _read_jsonl(ep) if e.get("event") == "SIGNAL_GENERATED"]
    for e in evs:
        try:
            e["_ts"] = _epoch(_parse_iso(e["timestamp"]))
        except (KeyError, ValueError):
            e["_ts"] = None
    return [e for e in evs if e["_ts"] is not None]


def _side_sign(side: str) -> int | None:
    s = (side or "").upper()
    if s in ("BUY", "LONG"):
        return 1
    if s in ("SELL", "SHORT"):
        return -1
    return None


def resolve_decisions(win_id: str, run_dir: Path, start: str, end: str,
                      store: CandleStore) -> tuple[list[dict], dict]:
    """Return per-decision records with side/entry/sim_ts, plus fidelity meta.

    V-TRUE journals: native fields (exact). Baseline journals: reconstructed
    (event join + candle price match) — flagged in meta."""
    decisions = load_journal_decisions(run_dir)
    meta = {"journal_decisions": len(decisions), "native": 0,
            "reconstructed": 0, "unresolved": 0, "mode": "none"}
    if not decisions:
        return [], meta

    native = [d for d in decisions if d.get("sim_ts") is not None
              and d.get("signal_side")]
    out = []
    if len(native) >= max(1, len(decisions) // 2):
        meta["mode"] = "native"
        for d in decisions:
            sign = _side_sign(str(d.get("signal_side") or ""))
            try:
                sim_ts = float(d.get("sim_ts"))
                entry = float(d.get("signal_entry"))
            except (TypeError, ValueError):
                meta["unresolved"] += 1
                continue
            if sign is None:
                meta["unresolved"] += 1
                continue
            meta["native"] += 1
            out.append({"window": win_id, "verdict": d["_verdict"],
                        "symbol": d.get("symbol"), "side_sign": sign,
                        "side": str(d.get("signal_side")).upper(),
                        "entry": entry, "sim_ts": sim_ts,
                        "confidence": d.get("signal_confidence"),
                        "fidelity": "native"})
        return out, meta

    # ── Baseline reconstruction path ─────────────────────────────────
    meta["mode"] = "reconstructed"
    events = load_signal_events(run_dir)
    if not events:
        warn(f"{win_id}: no trade_events.jsonl — cannot reconstruct "
             f"rejected side")
        meta["unresolved"] = len(decisions)
        return [], meta
    win_start = _epoch(_parse_iso(start + "T00:00:00+00:00"))
    win_end = _epoch(_parse_iso(end + "T00:00:00+00:00")) + 86400
    last_sim: dict[str, float] = {}

    for d in decisions:
        sym = d.get("symbol") or ""
        try:
            jt = _epoch(_parse_iso(d["ts"]))
        except (KeyError, ValueError):
            meta["unresolved"] += 1
            continue
        cands = [e for e in events
                 if e.get("symbol") == sym and e["_ts"] <= jt + 2]
        if not cands:
            meta["unresolved"] += 1
            continue
        ev = max(cands, key=lambda e: e["_ts"])
        sign = _side_sign(str(ev.get("side") or ""))
        try:
            entry = float(ev.get("entry"))
        except (TypeError, ValueError):
            entry = None
        if sign is None or not entry:
            meta["unresolved"] += 1
            continue
        # sim-time recovery: match entry price to a 1h close, in-window,
        # per-symbol monotonic.
        closes = store.local.get(sym, {})
        lo = last_sim.get(sym, win_start)
        sim_ts = None
        for tol in (1e-6, 2e-4, 1e-3):
            hits = [(abs(c - entry) / entry, t) for t, c in closes.items()
                    if win_start <= t < win_end and t >= lo
                    and abs(c - entry) / entry <= tol]
            if hits:
                hits.sort()
                best_err = hits[0][0]
                # among equal-error hits take the earliest (first bar of
                # a cluster is the one that fires the pipeline)
                sim_ts = min(t for err, t in hits if err <= best_err + 1e-12)
                break
        if sim_ts is None:
            meta["unresolved"] += 1
            continue
        last_sim[sym] = sim_ts
        meta["reconstructed"] += 1
        out.append({"window": win_id, "verdict": d["_verdict"],
                    "symbol": sym, "side_sign": sign,
                    "side": str(ev.get("side")).upper(), "entry": entry,
                    "sim_ts": sim_ts, "confidence": ev.get("confidence"),
                    "fidelity": "reconstructed"})
    return out, meta


def score_forward(decisions: list[dict], store: CandleStore) -> None:
    """Attach signed forward returns (fraction) at each horizon in-place."""
    for d in decisions:
        d["fwd"] = {}
        d["fwd_src"] = {}
        for h in HORIZONS_H:
            c, src = store.close_at(d["symbol"], d["sim_ts"] + h * 3600)
            if c is None or not d["entry"]:
                d["fwd"][h] = None
            else:
                d["fwd"][h] = d["side_sign"] * (c / d["entry"] - 1.0)
            d["fwd_src"][h] = src


# ── Aggregation ───────────────────────────────────────────────────────


def _mean(xs: list[float]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


def spread_stats(decisions: list[dict]) -> dict:
    """Discrimination spread per horizon: mean(approved) - mean(rejected)."""
    out = {}
    for h in HORIZONS_H:
        app = [d["fwd"][h] for d in decisions
               if d["verdict"] == "approved" and d["fwd"].get(h) is not None]
        rej = [d["fwd"][h] for d in decisions
               if d["verdict"] == "rejected" and d["fwd"].get(h) is not None]
        ma, mr = _mean(app), _mean(rej)
        out[h] = {
            "n_approved": len(app), "n_rejected": len(rej),
            "mean_approved": ma, "mean_rejected": mr,
            "spread": (ma - mr) if (ma is not None and mr is not None)
                      else None,
        }
    return out


def positive_subset(decisions: list[dict], fee_rt_bps: float) -> dict:
    """Approved-subset net expectancy after RT fees, per horizon."""
    fee = fee_rt_bps / 10000.0
    out = {}
    for h in HORIZONS_H:
        app = [d["fwd"][h] for d in decisions
               if d["verdict"] == "approved" and d["fwd"].get(h) is not None]
        m = _mean(app)
        out[h] = {"n": len(app), "gross_mean": m,
                  "net_mean": (m - fee) if m is not None else None}
    return out


def load_trades(run_dir: Path) -> list[dict]:
    p = run_dir / "replay_trades.csv"
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError as e:
        warn(f"cannot read {p}: {e}")
        return []


def trade_aggregates(trades: list[dict]) -> dict:
    n = len(trades)
    pnl = wins = fees = 0.0
    for t in trades:
        try:
            v = float(t.get("pnl") or 0)
        except ValueError:
            v = 0.0
        pnl += v
        wins += 1 if v > 0 else 0
        try:
            fees += float(t.get("fees") or 0)
        except ValueError:
            pass
    return {"closes": n, "wins": int(wins),
            "wr": (100.0 * wins / n) if n else None,
            "net_pnl": pnl, "fees": fees,
            "pnl_per_close": (pnl / n) if n else None}


def report_crosscheck(win_id: str) -> dict:
    """Light parse of coordination/REPLAY_RUN_<id>.md for closes / net PnL."""
    p = COORD_ROOT / f"REPLAY_RUN_{win_id}.md"
    out = {"report": p.name, "exists": p.exists()}
    if not p.exists():
        return out
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    m = re.search(r"Closes generated:\s*(\d+)", txt)
    if m:
        out["closes"] = int(m.group(1))
    m = re.search(r"Net PnL[^:]*:\s*\$([+\-]?[\d.]+)", txt)
    if m:
        out["net_pnl"] = float(m.group(1))
    return out


def fee_model_of(run_dir: Path) -> dict:
    p = run_dir / "sandbox" / "replay_out" / "llm_summary.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("fee_model", {})
    except (OSError, json.JSONDecodeError):
        return {}


def c3_shorts_verdict(win_id: str, run_dir: Path,
                      decisions: list[dict], trades: list[dict]) -> dict:
    """Three-way C3 accounting: (a) short entry events, (b) shorts reaching
    the coordinator, (c) shorts approved/entered + PnL."""
    events = load_signal_events(run_dir)
    raw_shorts = [e for e in events if _side_sign(str(e.get("side"))) == -1]

    def _qualifies(e: dict) -> bool:
        try:
            if int(e.get("num_agree") or 1) >= 2:
                return True
        except (TypeError, ValueError):
            pass
        try:
            conf = float(e.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        strats = set(e.get("strategies_agree") or [])
        strats.add(str(e.get("strategy") or ""))
        return conf >= SOLO_CONF_MIN and bool(strats & SOLO_WHITELIST)

    a = sum(1 for e in raw_shorts if _qualifies(e))
    coord_shorts = [d for d in decisions if d["side_sign"] == -1]
    b = len(coord_shorts)
    approved_shorts = [d for d in coord_shorts if d["verdict"] == "approved"]
    short_trades = [t for t in trades
                    if str(t.get("side", "")).upper() in ("SHORT", "SELL")]
    short_pnl = 0.0
    for t in short_trades:
        try:
            short_pnl += float(t.get("pnl") or 0)
        except ValueError:
            pass
    c = len(short_trades)
    if a == 0 and raw_shorts:
        verdict = "FILTER-STARVED (raw shorts existed; none qualified as entry events)"
    elif b == 0 and a > 0:
        verdict = "FILTER/CAP-STARVED (qualified shorts never reached the coordinator)"
    elif c > 0 or approved_shorts:
        verdict = "BRAIN SHORTED"
    elif b > 0:
        verdict = "BRAIN DECLINED (shorts reached the coordinator; all skipped)"
    else:
        verdict = "NO SHORT SIGNALS GENERATED"
    return {"window": win_id, "raw_short_signals": len(raw_shorts),
            "a_qualified_entry_events": a, "b_reached_coordinator": b,
            "b_approved": len(approved_shorts), "c_short_closes": c,
            "c_short_pnl": short_pnl, "verdict": verdict}


# ── Arm processing ────────────────────────────────────────────────────


def process_arm(label: str, suffix: str, windows: list, store: CandleStore,
                fee_rt_bps: float) -> dict:
    arm = {"label": label, "suffix": suffix, "windows": {}, "missing": [],
           "decisions": []}
    for base_id, start, end, regime in windows:
        win_id = base_id + suffix
        run_dir = REPLAY_ROOT / win_id
        if not run_dir.is_dir():
            arm["missing"].append(win_id)
            continue
        try:
            store.load_run_cache(run_dir)
            decisions, fmeta = resolve_decisions(
                base_id, run_dir, start, end, store)
            score_forward(decisions, store)
            trades = load_trades(run_dir)
            w = {
                "id": win_id, "regime": regime, "window": f"{start}->{end}",
                "fidelity": fmeta,
                "in_progress": not (run_dir / "replay_trades.csv").exists(),
                "spread": spread_stats(decisions),
                "positive_subset": positive_subset(decisions, fee_rt_bps),
                "aggregates": trade_aggregates(trades),
                "report_crosscheck": report_crosscheck(win_id),
                "fee_model": fee_model_of(run_dir),
            }
            if base_id == "C3":
                w["c3_shorts"] = c3_shorts_verdict(
                    win_id, run_dir, decisions, trades)
            arm["windows"][base_id] = w
            arm["decisions"].extend(decisions)
        except Exception as e:  # noqa: BLE001 — fail-soft per window
            warn(f"{win_id}: window processing failed: {e!r}")
            arm["missing"].append(win_id + " (error)")
    arm["pooled_spread"] = spread_stats(arm["decisions"])
    arm["pooled_positive_subset"] = positive_subset(
        arm["decisions"], fee_rt_bps)
    return arm


def compute_verdict(arm: dict, fee_rt_bps: float, primary_h: int = 24) -> dict:
    """Brain-beats-cage iff pooled spread > 0 AND approved subset
    net-positive in >= 2 regimes (windows)."""
    ps = arm["pooled_spread"].get(primary_h, {})
    spread = ps.get("spread")
    n_app, n_rej = ps.get("n_approved", 0), ps.get("n_rejected", 0)
    cond1 = spread is not None and spread > 0

    fee = fee_rt_bps / 10000.0
    pos_windows_realized, pos_windows_journal = [], []
    for wid, w in arm["windows"].items():
        ag = w["aggregates"]
        if ag["closes"] > 0 and ag["net_pnl"] > 0:
            pos_windows_realized.append(wid)
        nm = w["positive_subset"].get(primary_h, {})
        if (nm.get("n") or 0) > 0 and nm.get("net_mean") is not None \
                and nm["net_mean"] > 0:
            pos_windows_journal.append(wid)
    pos_windows = sorted(set(pos_windows_realized) | set(pos_windows_journal))
    cond2 = len(pos_windows) >= 2

    return {
        "primary_horizon_h": primary_h,
        "cond1_spread_positive": cond1,
        "pooled_spread": spread,
        "n_approved": n_app, "n_rejected": n_rej,
        "cond2_net_positive_regimes": cond2,
        "positive_windows_realized_pnl": pos_windows_realized,
        "positive_windows_journal_netfwd": pos_windows_journal,
        "positive_windows_union": pos_windows,
        "verdict": ("BRAIN BEATS CAGE" if (cond1 and cond2)
                    else "NOT PROVEN (criteria not met)"),
    }


# ── Rendering ─────────────────────────────────────────────────────────


def _pct(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None else f"{100.0 * x:+.{digits}f}%"


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:+.2f}"


def render_markdown(base: dict, treat: dict | None, fee_rt_bps: float,
                    verdict: dict | None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = []
    L.append("## REPLAY A/B VERDICT — V-TRUE (brain) vs baseline (old pipe)")
    L.append(f"Generated {now} by bot/tools/research/ab_verdict.py "
             f"(pre-registered metrics: THOUGHT_JOURNAL 2026-07-02 23:30Z "
             f"+ FULL_PIPE_BUILD_MAP §4)")
    L.append("")
    arms = [base] + ([treat] if treat else [])

    # missing data
    miss = []
    for a in arms:
        if a["missing"]:
            miss.append(f"{a['label']}: missing {', '.join(a['missing'])}")
    if treat is None:
        miss.append("V-TRUE arm: NO run dirs found yet")
    if miss:
        L.append("**Missing data:** " + " | ".join(miss))
        L.append("")

    for a in arms:
        recon = any(w["fidelity"]["mode"] == "reconstructed"
                    for w in a["windows"].values())
        L.append(f"### Arm: {a['label']}"
                 + (" — REJECTED-SIDE RECONSTRUCTED (lower fidelity: "
                    "journal lacks signal fields pre-ddd2fdf; side/price "
                    "joined from trade_events, sim-time recovered by "
                    "candle price-match)" if recon else ""))
        L.append("")
        L.append("| window | regime | closes | WR | net PnL | spread 6h | "
                 "spread 12h | spread 24h | approved net fwd 24h (post-fee) "
                 "| n app/rej (24h) |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for base_id, *_ in WINDOWS:
            w = a["windows"].get(base_id)
            if not w:
                continue
            ag, sp = w["aggregates"], w["spread"]
            ns = w["positive_subset"][24]
            s24 = sp[24]
            flag = " (RUN IN PROGRESS)" if w.get("in_progress") else ""
            L.append(
                f"| {w['id']}{flag} | {w['regime'].split('(')[0].strip()} | "
                f"{ag['closes']} | "
                f"{'n/a' if ag['wr'] is None else f'{ag['wr']:.0f}%'} | "
                f"{_usd(ag['net_pnl'])} | {_pct(sp[6]['spread'])} | "
                f"{_pct(sp[12]['spread'])} | {_pct(s24['spread'])} | "
                f"{_pct(ns['net_mean'])} (n={ns['n']}) | "
                f"{s24['n_approved']}/{s24['n_rejected']} |")
        L.append("")
        pooled = a["pooled_spread"]
        pps = a["pooled_positive_subset"]
        L.append(f"**Pooled {a['label']}** — discrimination spread: "
                 + " | ".join(
                     f"{h}h {_pct(pooled[h]['spread'])} "
                     f"(n={pooled[h]['n_approved']}/{pooled[h]['n_rejected']})"
                     for h in HORIZONS_H))
        L.append(f"**Pooled approved-subset net fwd (after {fee_rt_bps}bps "
                 f"RT):** " + " | ".join(
                     f"{h}h {_pct(pps[h]['net_mean'])} (n={pps[h]['n']})"
                     for h in HORIZONS_H))
        # fidelity accounting
        tot_unres = sum(w["fidelity"]["unresolved"]
                        for w in a["windows"].values())
        if tot_unres:
            L.append(f"Fidelity: {tot_unres} journaled decisions unresolved "
                     f"(excluded from spread).")
        # fee model note
        fm = next((w["fee_model"] for w in a["windows"].values()
                   if w["fee_model"]), {})
        if fm:
            maker = {k: v for k, v in fm.items() if "maker" in k.lower()}
            L.append(f"Fee model in run: {fm}"
                     + (f" — MAKER config shipped: {maker}" if maker else
                        " (taker both sides; verdict fee assumption "
                        f"{fee_rt_bps}bps RT is consistent)"))
        L.append("")
        c3 = next((w.get("c3_shorts") for w in a["windows"].values()
                   if w.get("c3_shorts")), None)
        if c3:
            L.append(f"**C3 shorts three-way ({a['label']}):** "
                     f"raw shorts {c3['raw_short_signals']}, "
                     f"(a) qualified entry events {c3['a_qualified_entry_events']}, "
                     f"(b) reached coordinator {c3['b_reached_coordinator']} "
                     f"(approved {c3['b_approved']}), "
                     f"(c) short closes {c3['c_short_closes']} "
                     f"(PnL {_usd(c3['c_short_pnl'])}) -> "
                     f"**{c3['verdict']}**")
            L.append("")

    L.append("### VERDICT (pre-registered criteria)")
    L.append("Brain beats cage iff (1) pooled discrimination spread > 0 "
             "with honest n AND (2) approved subset net-positive in >= 2 "
             "regimes. Blanket-refusal profit is not choosing; the cage "
             "scores spread = 0 by construction.")
    if verdict is None:
        L.append("")
        L.append("**VERDICT: PENDING — V-TRUE run dirs not present yet.** "
                 "Re-run this script when C1t..C6t land; the baseline side "
                 "above is final.")
    else:
        v = verdict
        L.append("")
        L.append(f"- (1) V-TRUE pooled spread @{v['primary_horizon_h']}h: "
                 f"{_pct(v['pooled_spread'])} "
                 f"(n={v['n_approved']} approved / {v['n_rejected']} "
                 f"rejected) -> {'PASS' if v['cond1_spread_positive'] else 'FAIL'}")
        L.append(f"- (2) approved-subset net-positive regimes: "
                 f"{v['positive_windows_union']} "
                 f"(realized-PnL basis: {v['positive_windows_realized_pnl']}; "
                 f"journal fwd-return basis: "
                 f"{v['positive_windows_journal_netfwd']}) -> "
                 f"{'PASS' if v['cond2_net_positive_regimes'] else 'FAIL'}")
        L.append("")
        L.append(f"**VERDICT: {v['verdict']}**")
    if WARNINGS:
        L.append("")
        L.append("<details><summary>Warnings "
                 f"({len(WARNINGS)})</summary>")
        L.extend(f"- {w}" for w in WARNINGS)
        L.append("</details>")
    L.append("")
    return "\n".join(L)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--windows", default="",
                    help="comma list of base window ids (default: all)")
    ap.add_argument("--treat-suffix", default="t",
                    help="V-TRUE run-dir suffix (default 't' -> C1t..)")
    ap.add_argument("--fee-bps", type=float, default=DEFAULT_FEE_RT_BPS,
                    help=f"round-trip taker cost in bps "
                         f"(default {DEFAULT_FEE_RT_BPS})")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable JSON to stdout")
    ap.add_argument("--no-fetch", action="store_true",
                    help="cache-only; never hit the HL candle API")
    args = ap.parse_args()

    sel = [w.strip().upper() for w in args.windows.split(",") if w.strip()]
    windows = [w for w in WINDOWS if not sel or w[0] in sel]
    if not windows:
        print(f"unknown --windows (known: {[w[0] for w in WINDOWS]})",
              file=sys.stderr)
        return 2

    base_store = CandleStore(allow_fetch=not args.no_fetch)
    base = process_arm("BASELINE (old pipe, C1-C6)", "", windows,
                       base_store, args.fee_bps)

    treat = None
    treat_store = CandleStore(allow_fetch=not args.no_fetch)
    if any((REPLAY_ROOT / (w[0] + args.treat_suffix)).is_dir()
           for w in windows):
        treat = process_arm(
            f"V-TRUE (full pipe, C1{args.treat_suffix}-C6"
            f"{args.treat_suffix})", args.treat_suffix, windows,
            treat_store, args.fee_bps)

    verdict = compute_verdict(treat, args.fee_bps) if treat else None
    md = render_markdown(base, treat, args.fee_bps, verdict)

    if args.json:
        def _strip(a):
            if a is None:
                return None
            return {k: v for k, v in a.items() if k != "decisions"}
        print(json.dumps({
            "generated": datetime.now(timezone.utc).isoformat(),
            "fee_rt_bps": args.fee_bps,
            "baseline": _strip(base),
            "vtrue": _strip(treat),
            "verdict": verdict,
            "warnings": WARNINGS,
            "markdown": md,
        }, indent=1, default=str))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
