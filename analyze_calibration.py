#!/usr/bin/env python3
"""
Calibration validation script.

Reads ``live_trades.json`` (Path A: settled live trades) and reports per-
confidence-bucket statistics needed for the three-gate decision rule:

1. **Dollar-weighted realized return** (``sum_pnl_usd / sum_size_usd``) positive
   in at least one bucket with ``n >= 100``.
2. **Probability-edge Wilson 95% CI lower bound** minus weighted-avg-entry
   minus ``EV_FEE_BUFFER + EV_SPREAD_BUFFER`` strictly positive.
3. **Out-of-sample persistence**: realized return positive in BOTH halves
   (chronological split).

Exclusion rules (per EXECUTION_PLAN.md):

- Skip records where ``settlement_source`` is not one of
  ``auto_redeem``, ``late_auto_redeem``, ``manual_reconciliation``.
- Skip records where ``payout`` or ``pnl`` is ``"UNKNOWN"`` or missing.
- Raise on non-positive ``size`` (corrupt accounting; fail closed).

Usage::

    venv/bin/python analyze_calibration.py --ledger /path/to/live_trades.json

Outputs a per-bucket table plus a final pass/fail summary against the
three gates. **Decision-only**: the script never modifies the ledger.

When ``--decisions`` is supplied, the script also performs Path B: it joins
``decisions.jsonl`` records to Polymarket Gamma market resolutions by slug and
reports confidence-bucket calibration across accepted and rejected decisions.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from calibration_decision_join import (
    GammaResolutionResolver,
    analyze_path_b,
    load_decision_records,
)


RESOLVED_SOURCES = ("auto_redeem", "late_auto_redeem", "manual_reconciliation")
UNKNOWN_SENTINELS = ("UNKNOWN", None)


def _is_resolved(trade: dict) -> bool:
    source = trade.get("settlement_source")
    if source not in RESOLVED_SOURCES:
        return False
    payout = trade.get("payout")
    pnl = trade.get("pnl")
    if payout in UNKNOWN_SENTINELS or pnl in UNKNOWN_SENTINELS:
        return False
    return True


def _wilson_lower_bound_95(wins: int, n: int) -> float:
    """Wilson 95% confidence interval lower bound for a binomial proportion."""
    if n <= 0:
        return 0.0
    z = 1.959963984540054  # 97.5% quantile of standard normal
    p_hat = wins / n
    denom = 1 + z * z / n
    centre = p_hat + z * z / (2 * n)
    radius = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    return (centre - radius) / denom


def _brier_score(samples: list[tuple[float, int]]) -> float:
    if not samples:
        return float("nan")
    return sum((conf - outcome) ** 2 for conf, outcome in samples) / len(samples)


def _log_loss(samples: list[tuple[float, int]]) -> float:
    if not samples:
        return float("nan")
    eps = 1e-12
    total = 0.0
    for conf, outcome in samples:
        c = min(max(conf, eps), 1 - eps)
        total += -(outcome * math.log(c) + (1 - outcome) * math.log(1 - c))
    return total / len(samples)


def _bucket_key(confidence: float) -> float:
    """0.50, 0.60, 0.70, ..."""
    return round(confidence * 10) / 10


def analyze_path_a(ledger: dict, fee_buffer: Decimal, spread_buffer: Decimal) -> dict:
    """Path A analysis from settled live trades."""
    buckets: dict[float, dict[str, Any]] = defaultdict(
        lambda: {
            "outcome_wins": 0,
            "outcome_losses": 0,
            "sum_entry_price_weighted": Decimal("0"),
            "sum_pnl_usd": Decimal("0"),
            "sum_size_usd": Decimal("0"),
            "trades": 0,
            # raw (conf, outcome) pairs for Brier / log-loss
            "samples": [],
            # (timestamp, trade_dict) pairs for out-of-sample halves
            "chrono": [],
        }
    )

    settled = ledger.get("settled", {})
    if isinstance(settled, dict):
        records = list(settled.values())
    elif isinstance(settled, list):
        records = list(settled)
    else:
        records = []

    excluded = 0
    for trade in records:
        if not isinstance(trade, dict):
            continue
        if not _is_resolved(trade):
            excluded += 1
            continue

        confidence_raw = trade.get("signal_confidence")
        size_raw = trade.get("size")
        entry_raw = trade.get("entry_price")
        payout_raw = trade.get("payout")
        pnl_raw = trade.get("pnl")
        if any(v in UNKNOWN_SENTINELS for v in (confidence_raw, size_raw, entry_raw)):
            excluded += 1
            continue

        try:
            conf = float(confidence_raw)
            size = Decimal(str(size_raw))
            entry = Decimal(str(entry_raw))
            payout = Decimal(str(payout_raw))
            pnl = Decimal(str(pnl_raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"settled trade has unparseable accounting fields: {trade!r}"
            ) from exc

        if size <= 0:
            raise ValueError(
                f"settled trade has non-positive size — refusing to analyze: {trade!r}"
            )

        outcome = 1 if payout > 0 else 0
        bucket = _bucket_key(conf)
        b = buckets[bucket]
        b["outcome_wins" if outcome else "outcome_losses"] += 1
        b["sum_entry_price_weighted"] += entry * size
        b["sum_pnl_usd"] += pnl
        b["sum_size_usd"] += size
        b["trades"] += 1
        b["samples"].append((conf, outcome))
        b["chrono"].append(
            (trade.get("settled_at") or trade.get("submitted_at") or "", outcome, pnl, size)
        )

    # Build per-bucket summary
    summary = {}
    for bucket, b in buckets.items():
        n = b["trades"]
        if n == 0:
            continue
        size_total = b["sum_size_usd"]
        if size_total <= 0:
            raise ValueError(f"bucket {bucket} has no positive traded size")
        win_rate = b["outcome_wins"] / n
        wilson_lo = _wilson_lower_bound_95(b["outcome_wins"], n)
        weighted_entry = b["sum_entry_price_weighted"] / size_total
        realized_return = b["sum_pnl_usd"] / size_total
        probability_edge = Decimal(str(win_rate)) - weighted_entry
        wilson_edge = Decimal(str(wilson_lo)) - weighted_entry - fee_buffer - spread_buffer

        chrono_sorted = sorted(b["chrono"], key=lambda t: t[0])
        half = len(chrono_sorted) // 2
        h1 = chrono_sorted[:half]
        h2 = chrono_sorted[half:]
        h1_return = (
            sum(p for _, _, p, _ in h1) / sum(s for _, _, _, s in h1)
            if h1 and sum(s for _, _, _, s in h1) > 0
            else Decimal("0")
        )
        h2_return = (
            sum(p for _, _, p, _ in h2) / sum(s for _, _, _, s in h2)
            if h2 and sum(s for _, _, _, s in h2) > 0
            else Decimal("0")
        )

        summary[bucket] = {
            "n": n,
            "win_rate": win_rate,
            "wilson_lower_bound_95": wilson_lo,
            "weighted_avg_entry_price": float(weighted_entry),
            "realized_return": float(realized_return),
            "probability_edge": float(probability_edge),
            "wilson_edge_after_buffers": float(wilson_edge),
            "first_half_return": float(h1_return),
            "second_half_return": float(h2_return),
            "brier": _brier_score(b["samples"]),
            "log_loss": _log_loss(b["samples"]),
        }

    return {
        "buckets": summary,
        "excluded_records": excluded,
        "total_settled_records": len(records),
    }


def gate_three_check(buckets: dict) -> tuple[bool, list[str]]:
    """Return (passed, reasons). All three gates must pass in at least one
    bucket with n >= 100."""
    passing_buckets = []
    reasons = []
    for bucket, stats in buckets.items():
        if stats["n"] < 100:
            continue
        gate1 = stats["realized_return"] > 0
        gate2 = stats["wilson_edge_after_buffers"] > 0
        gate3 = stats["first_half_return"] > 0 and stats["second_half_return"] > 0
        if gate1 and gate2 and gate3:
            passing_buckets.append(bucket)
        else:
            reasons.append(
                f"bucket {bucket:.1f} (n={stats['n']}): "
                f"realized_return={'PASS' if gate1 else 'FAIL'} "
                f"wilson_edge={'PASS' if gate2 else 'FAIL'} "
                f"out_of_sample={'PASS' if gate3 else 'FAIL'}"
            )
    if passing_buckets:
        return True, [f"passing buckets: {sorted(passing_buckets)}"]
    if not reasons:
        reasons.append(
            "no bucket has n>=100 yet; keep collecting settled live trades"
        )
    return False, reasons


def path_b_sample_check(buckets: dict) -> tuple[bool, list[str]]:
    passing_buckets = [
        bucket for bucket, stats in buckets.items() if stats["n"] >= 200
    ]
    if passing_buckets:
        return True, [f"Path B buckets with n>=200: {sorted(passing_buckets)}"]
    return False, ["no Path B confidence bucket has n>=200 resolved decisions yet"]


def _format_optional_float(value: Any, width: int, precision: int = 4, signed: bool = False) -> str:
    if value is None:
        return "NA".rjust(width)
    sign = "+" if signed else ""
    return f"{value:{sign}{width}.{precision}f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Calibration analysis")
    parser.add_argument("--ledger", required=True, help="Path to live_trades.json")
    parser.add_argument("--decisions", help="Optional path to decisions.jsonl (Path B)")
    parser.add_argument(
        "--fee-buffer",
        default="0.005",
        help="EV_FEE_BUFFER value (default 0.005)",
    )
    parser.add_argument(
        "--spread-buffer",
        default="0.01",
        help="EV_SPREAD_BUFFER value (default 0.01)",
    )
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"ERROR: ledger not found: {ledger_path}", file=sys.stderr)
        return 2
    with open(ledger_path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)

    fee_buffer = Decimal(args.fee_buffer)
    spread_buffer = Decimal(args.spread_buffer)
    report = analyze_path_a(ledger, fee_buffer, spread_buffer)

    print(f"Path A — settled live trades from {ledger_path}")
    print(f"  total settled records: {report['total_settled_records']}")
    print(f"  excluded (unresolved or unsupported source): {report['excluded_records']}")
    print(f"  fee_buffer={fee_buffer}, spread_buffer={spread_buffer}")
    print()
    if not report["buckets"]:
        print("  No resolved settled records yet; nothing to bucket.")
    else:
        cols = (
            ("conf", 6), ("n", 5), ("win_rate", 9), ("wilson_lo", 10),
            ("w_avg_entry", 12), ("realized_ret", 13), ("wilson_edge", 12),
            ("h1_ret", 10), ("h2_ret", 10), ("brier", 8), ("log_loss", 9),
        )
        header = " ".join(name.rjust(width) for name, width in cols)
        print("  " + header)
        for bucket in sorted(report["buckets"]):
            s = report["buckets"][bucket]
            row = (
                f"{bucket:6.1f}",
                f"{s['n']:5d}",
                f"{s['win_rate']:9.1%}",
                f"{s['wilson_lower_bound_95']:10.4f}",
                f"{s['weighted_avg_entry_price']:12.4f}",
                f"{s['realized_return']:+13.4f}",
                f"{s['wilson_edge_after_buffers']:+12.4f}",
                f"{s['first_half_return']:+10.4f}",
                f"{s['second_half_return']:+10.4f}",
                f"{s['brier']:8.4f}",
                f"{s['log_loss']:9.4f}",
            )
            print("  " + " ".join(row))

    print()
    passed, reasons = gate_three_check(report["buckets"])
    print(f"Three-gate decision: {'PASS' if passed else 'FAIL / INSUFFICIENT DATA'}")
    for r in reasons:
        print(f"  - {r}")

    if args.decisions:
        decisions_path = Path(args.decisions)
        if not decisions_path.exists():
            print(f"\nERROR: decisions.jsonl not found at {decisions_path}", file=sys.stderr)
            return 2
        else:
            decision_records = load_decision_records(decisions_path)
            with httpx.Client(timeout=20.0) as client:
                resolver = GammaResolutionResolver(client)
                path_b_report = analyze_path_b(
                    decision_records,
                    resolver,
                    fee_buffer,
                    spread_buffer,
                )
            print(f"\nPath B — decisions.jsonl joined to Polymarket Gamma resolutions from {decisions_path}")
            print(f"  total decision records: {path_b_report['total_decision_records']}")
            print(f"  resolved calibration records: {path_b_report['resolved_calibration_records']}")
            print("  excluded records:")
            for reason, count in path_b_report["excluded_records"].items():
                print(f"    {reason}: {count}")
            if not path_b_report["buckets"]:
                print("  No resolved decision observations yet; nothing to bucket.")
            else:
                cols = (
                    ("conf", 6), ("n", 5), ("win_rate", 9), ("wilson_lo", 10),
                    ("entry_n", 8), ("entry_win", 9), ("entry_wil", 9),
                    ("w_avg_entry", 12), ("prob_edge", 11), ("wilson_edge", 12),
                    ("brier", 8), ("log_loss", 9),
                )
                header = " ".join(name.rjust(width) for name, width in cols)
                print("  " + header)
                for bucket in sorted(path_b_report["buckets"]):
                    s = path_b_report["buckets"][bucket]
                    row = (
                        f"{bucket:6.1f}",
                        f"{s['n']:5d}",
                        f"{s['win_rate']:9.1%}",
                        f"{s['wilson_lower_bound_95']:10.4f}",
                        f"{s['entry_samples']:8d}",
                        _format_optional_float(s["entry_win_rate"], 9),
                        _format_optional_float(s["entry_wilson_lower_bound_95"], 9),
                        _format_optional_float(s["weighted_avg_entry_price"], 12),
                        _format_optional_float(s["probability_edge"], 11, signed=True),
                        _format_optional_float(s["wilson_edge_after_buffers"], 12, signed=True),
                        f"{s['brier']:8.4f}",
                        f"{s['log_loss']:9.4f}",
                    )
                    print("  " + " ".join(row))
            passed_b, reasons_b = path_b_sample_check(path_b_report["buckets"])
            print(
                "Path B sample gate: "
                f"{'PASS' if passed_b else 'FAIL / INSUFFICIENT DATA'}"
            )
            for reason in reasons_b:
                print(f"  - {reason}")
    else:
        print(
            "\nPath B not provided. Pass --decisions /path/to/decisions.jsonl "
            "to join the unbiased decision-observation set against Polymarket "
            "historical resolutions."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
