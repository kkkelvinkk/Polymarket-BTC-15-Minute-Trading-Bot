"""Tests for analyze_calibration.py."""

import json
import os
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import analyze_calibration as ac


def _resolved_trade(order_id, conf, payout, pnl, size, entry, settled_at):
    return {
        "order_id": order_id,
        "settlement_source": "manual_reconciliation",
        "signal_confidence": conf,
        "payout": str(payout),
        "pnl": str(pnl),
        "size": str(size),
        "entry_price": str(entry),
        "settled_at": settled_at,
    }


def _unresolved_trade():
    return {
        "order_id": "X",
        "settlement_source": "SETTLEMENT_UNKNOWN",
        "needs_reconciliation": True,
        "signal_confidence": 0.75,
        "payout": "UNKNOWN",
        "pnl": "UNKNOWN",
        "size": "5",
        "entry_price": "0.5",
    }


class IsResolvedTests(unittest.TestCase):
    def test_resolved_known(self):
        t = _resolved_trade("A", 0.75, 10, 5, 5, 0.5, "2026-05-19T00:00:00+00:00")
        self.assertTrue(ac._is_resolved(t))

    def test_unresolved_source(self):
        t = _resolved_trade("A", 0.75, 10, 5, 5, 0.5, "2026-05-19T00:00:00+00:00")
        t["settlement_source"] = "SETTLEMENT_UNKNOWN"
        self.assertFalse(ac._is_resolved(t))

    def test_payout_unknown_excluded(self):
        t = _resolved_trade("A", 0.75, 10, 5, 5, 0.5, "2026-05-19T00:00:00+00:00")
        t["payout"] = "UNKNOWN"
        self.assertFalse(ac._is_resolved(t))


class WilsonLowerBoundTests(unittest.TestCase):
    def test_zero_trades(self):
        self.assertEqual(ac._wilson_lower_bound_95(0, 0), 0.0)

    def test_perfect_record_small_n(self):
        # 10 wins in 10 trades → CI lower bound is significantly below 1.0
        lb = ac._wilson_lower_bound_95(10, 10)
        self.assertGreater(lb, 0.5)
        self.assertLess(lb, 1.0)

    def test_zero_wins(self):
        # Wilson lower bound for 0/100 is mathematically near zero but not
        # exactly zero due to the continuity correction (centre / denom with a
        # tiny positive numerator). Assert it's negligibly small.
        lb = ac._wilson_lower_bound_95(0, 100)
        self.assertLess(lb, 1e-10)
        self.assertGreaterEqual(lb, 0.0)


class AnalyzePathATests(unittest.TestCase):
    def setUp(self):
        self.fee = Decimal("0.005")
        self.spread = Decimal("0.01")

    def test_empty_ledger(self):
        report = ac.analyze_path_a({}, self.fee, self.spread)
        self.assertEqual(report["buckets"], {})
        self.assertEqual(report["total_settled_records"], 0)

    def test_excludes_unresolved(self):
        ledger = {"settled": {"A": _unresolved_trade()}}
        report = ac.analyze_path_a(ledger, self.fee, self.spread)
        self.assertEqual(report["buckets"], {})
        self.assertEqual(report["excluded_records"], 1)

    def test_two_resolved_trades_in_same_bucket(self):
        ledger = {
            "settled": {
                "A": _resolved_trade(
                    "A", 0.72, payout=10, pnl=5, size=5, entry=0.50,
                    settled_at="2026-05-19T00:00:00+00:00"
                ),
                "B": _resolved_trade(
                    "B", 0.68, payout=0, pnl=-5, size=5, entry=0.55,
                    settled_at="2026-05-19T01:00:00+00:00"
                ),
            }
        }
        report = ac.analyze_path_a(ledger, self.fee, self.spread)
        # bucket_key(0.72) = 0.7, bucket_key(0.68) = 0.7
        self.assertIn(0.7, report["buckets"])
        b = report["buckets"][0.7]
        self.assertEqual(b["n"], 2)
        # One win (payout 10>0), one loss (payout 0)
        self.assertAlmostEqual(b["win_rate"], 0.5)
        # weighted avg entry = (5*0.50 + 5*0.55) / 10 = 0.525
        self.assertAlmostEqual(b["weighted_avg_entry_price"], 0.525)
        # realized return = (5 + -5) / 10 = 0
        self.assertAlmostEqual(b["realized_return"], 0.0)
        # probability_edge = 0.5 - 0.525 = -0.025
        self.assertAlmostEqual(b["probability_edge"], -0.025)

    def test_negative_size_raises(self):
        ledger = {
            "settled": {
                "A": _resolved_trade(
                    "A", 0.75, payout=10, pnl=5, size=-5, entry=0.50,
                    settled_at="2026-05-19T00:00:00+00:00"
                )
            }
        }
        with self.assertRaisesRegex(ValueError, "non-positive size"):
            ac.analyze_path_a(ledger, self.fee, self.spread)


class GateThreeCheckTests(unittest.TestCase):
    def _bucket(self, n, realized, wilson_edge, h1, h2):
        return {
            "n": n,
            "win_rate": 0.5,
            "wilson_lower_bound_95": 0.5,
            "weighted_avg_entry_price": 0.5,
            "realized_return": realized,
            "probability_edge": 0.0,
            "wilson_edge_after_buffers": wilson_edge,
            "first_half_return": h1,
            "second_half_return": h2,
            "brier": 0.25,
            "log_loss": 0.69,
        }

    def test_no_buckets_with_enough_n_fails(self):
        buckets = {0.7: self._bucket(50, 0.05, 0.05, 0.05, 0.05)}
        passed, reasons = ac.gate_three_check(buckets)
        self.assertFalse(passed)
        self.assertTrue(any("n>=100" in r for r in reasons))

    def test_all_three_gates_pass(self):
        buckets = {0.7: self._bucket(150, 0.05, 0.05, 0.05, 0.05)}
        passed, reasons = ac.gate_three_check(buckets)
        self.assertTrue(passed)

    def test_realized_return_negative_fails(self):
        buckets = {0.7: self._bucket(150, -0.01, 0.05, 0.05, 0.05)}
        passed, reasons = ac.gate_three_check(buckets)
        self.assertFalse(passed)

    def test_wilson_edge_negative_fails(self):
        buckets = {0.7: self._bucket(150, 0.05, -0.01, 0.05, 0.05)}
        passed, _ = ac.gate_three_check(buckets)
        self.assertFalse(passed)

    def test_one_half_negative_fails_out_of_sample(self):
        buckets = {0.7: self._bucket(150, 0.05, 0.05, 0.10, -0.02)}
        passed, _ = ac.gate_three_check(buckets)
        self.assertFalse(passed)


class CliSmokeTest(unittest.TestCase):
    def test_runs_against_synthetic_ledger(self):
        ledger_path = Path(f"/tmp/test_calibration_{os.getpid()}.json")
        ledger = {
            "settled": [
                _resolved_trade("A", 0.75, 10, 5, 5, 0.50, "2026-05-19T00:00:00+00:00"),
                _resolved_trade("B", 0.75, 0, -5, 5, 0.55, "2026-05-19T01:00:00+00:00"),
            ]
        }
        try:
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "analyze_calibration.py"),
                 "--ledger", str(ledger_path)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Path A", result.stdout)
            self.assertIn("Three-gate decision", result.stdout)
        finally:
            ledger_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
