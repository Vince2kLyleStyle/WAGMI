"""Backfill estimated fees for the 35 blank-fee trade_ledger.csv rows (Jun 2-10).

RQ17_FEE_DRAG hole: 35 rows have fees=='' and net_pnl==gross_pnl, so early-era
net PnL is overstated ~$90-360 and every fee analysis inherits the hole.

We do NOT rewrite the ledger (net_pnl history + the gross-fees+funding==net
identity on the other rows must stay intact). Instead this writes a SIDECAR:
    bot/data/ledger_fee_backfill.json
mapping trade_id -> estimated round-trip fee, so fee analyses can join it.
Reversible: delete the sidecar.

Method (mirrors RQ17's capped estimate):
  qty      = gross_pnl / (exit_price - entry_price)   [signed; LONG/SHORT safe via abs]
  notional = |qty| * entry_price, capped at p90 of notionals derived from
             fee-present rows (per RQ17: uncapped notionals are inflated by
             tiny-move derivation noise)
  fee_est  = 2 sides x HL taker 4.5 bps x capped notional

Usage: cd bot && python tools/backfill_ledger_fees.py
"""
import csv
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "..", "data", "trade_ledger.csv")
SIDECAR = os.path.join(HERE, "..", "data", "ledger_fee_backfill.json")
TAKER_BPS_PER_SIDE = 4.5  # HL taker


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def derive_notional(row):
    entry = _f(row.get("entry_price"))
    exit_p = _f(row.get("exit_price"))
    gross = _f(row.get("gross_pnl"))
    if entry is None or exit_p is None or gross is None:
        return None
    move = exit_p - entry
    if abs(move) < 1e-12:
        return None
    qty = abs(gross / move)
    return qty * entry


def main():
    with open(LEDGER, newline="") as f:
        rows = list(csv.DictReader(f))

    # p90 notional from fee-present rows (the trustworthy population)
    notionals = sorted(
        n for r in rows if (r.get("fees") or "").strip() != ""
        for n in [derive_notional(r)] if n is not None
    )
    if not notionals:
        raise SystemExit("no fee-present rows to derive p90 notional cap")
    p90 = notionals[int(0.9 * (len(notionals) - 1))]

    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "tools/backfill_ledger_fees.py",
            "evidence": "RQ17_FEE_DRAG.md §1: 35 rows Jun 2-10 fees=='' , net==gross",
            "method": (
                f"notional=|gross/(exit-entry)|*entry capped at p90=${p90:,.0f} "
                f"of fee-present rows; fee=2x{TAKER_BPS_PER_SIDE}bps taker"
            ),
            "note": (
                "SIDECAR ONLY — trade_ledger.csv untouched so net_pnl history "
                "and the gross-fees+funding==net identity stay intact. Fee "
                "analyses should treat net_pnl-fee_est as the honest net for "
                "these trade_ids."
            ),
        },
        "fees": {},
    }

    total = 0.0
    n_est = n_unable = 0
    for r in rows:
        if (r.get("fees") or "").strip() != "":
            continue
        tid = r.get("trade_id", "")
        notional = derive_notional(r)
        if notional is None:
            out["fees"][tid] = {"fee_est": None, "reason": "zero-move row, notional underivable"}
            n_unable += 1
            continue
        capped = min(notional, p90)
        fee = 2 * (TAKER_BPS_PER_SIDE / 1e4) * capped
        out["fees"][tid] = {
            "fee_est": round(fee, 2),
            "notional_derived": round(notional, 2),
            "notional_capped": round(capped, 2),
            "symbol": r.get("symbol", ""),
            "timestamp": r.get("timestamp", ""),
        }
        total += fee
        n_est += 1

    out["_meta"]["n_estimated"] = n_est
    out["_meta"]["n_underivable"] = n_unable
    out["_meta"]["total_fee_est"] = round(total, 2)

    with open(SIDECAR, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {SIDECAR}: {n_est} estimated (${total:,.2f} total), {n_unable} underivable, p90 cap ${p90:,.0f}")


if __name__ == "__main__":
    main()
