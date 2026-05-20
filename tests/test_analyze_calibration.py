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
import calibration_decision_join as cdj


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


def _decision(decision_id, slug, confidence, direction, entry="0.55", cost="5.50"):
    return {
        "decision_id": decision_id,
        "slug": slug,
        "fused_confidence": confidence,
        "fused_direction": direction,
        "executable_entry": entry,
        "estimated_actual_cost": cost,
    }


def _gamma_market(slug, winner, closed=True):
    outcomes = ["Yes", "No"]
    if winner == "long":
        prices = ["1", "0"]
    elif winner == "short":
        prices = ["0", "1"]
    else:
        prices = ["0.5", "0.5"]
    return {
        "slug": slug,
        "closed": closed,
        "outcomes": json.dumps(outcomes),
        "outcomePrices": json.dumps(prices),
    }


class _Resolver:
    def __init__(self, markets):
        self.markets = markets

    def market_for_slug(self, slug):
        return self.markets[slug]


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


class AnalyzePathBTests(unittest.TestCase):
    def test_gamma_fetch_requests_closed_markets(self):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [_gamma_market("slug-closed", "long")]

        class _Client:
            def __init__(self):
                self.calls = []

            def get(self, url, params):
                self.calls.append((url, params))
                return _Response()

        client = _Client()

        market = cdj.fetch_gamma_market_by_slug(client, "slug-closed")

        self.assertEqual(market["slug"], "slug-closed")
        self.assertEqual(
            client.calls,
            [
                (
                    cdj.GAMMA_MARKETS_URL,
                    {"slug": "slug-closed", "closed": "true", "limit": 2},
                )
            ],
        )

    def test_gamma_fetch_returns_none_when_slug_has_no_closed_market(self):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return []

        class _Client:
            def get(self, url, params):
                self.url = url
                self.params = params
                return _Response()

        client = _Client()

        market = cdj.fetch_gamma_market_by_slug(client, "slug-open")

        self.assertIsNone(market)
        self.assertEqual(client.url, cdj.GAMMA_MARKETS_URL)
        self.assertEqual(client.params, {"slug": "slug-open", "closed": "true", "limit": 2})

    def test_gamma_fetch_requires_exact_closed_slug_when_candidates_exist(self):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [_gamma_market("different-closed-slug", "long")]

        class _Client:
            def get(self, url, params):
                return _Response()

        with self.assertRaisesRegex(ValueError, "no exact closed match"):
            cdj.fetch_gamma_market_by_slug(_Client(), "requested-slug")

    def test_joins_decisions_to_gamma_resolution_by_slug(self):
        records = [
            _decision("D1", "slug-a", 0.72, "bullish", entry="0.50", cost="5.00"),
            _decision("D2", "slug-b", 0.68, "bearish", entry="0.55", cost="5.00"),
        ]
        resolver = _Resolver(
            {
                "slug-a": _gamma_market("slug-a", "long"),
                "slug-b": _gamma_market("slug-b", "long"),
            }
        )
        report = ac.analyze_path_b(records, resolver, Decimal("0.005"), Decimal("0.01"))

        self.assertEqual(report["total_decision_records"], 2)
        self.assertEqual(report["resolved_calibration_records"], 2)
        self.assertIn(0.7, report["buckets"])
        bucket = report["buckets"][0.7]
        self.assertEqual(bucket["n"], 2)
        self.assertAlmostEqual(bucket["win_rate"], 0.5)
        self.assertEqual(bucket["entry_samples"], 2)
        self.assertAlmostEqual(bucket["entry_win_rate"], 0.5)
        self.assertAlmostEqual(bucket["weighted_avg_entry_price"], 0.525)
        self.assertAlmostEqual(bucket["probability_edge"], -0.025)
        self.assertAlmostEqual(
            bucket["wilson_edge_after_buffers"],
            bucket["entry_wilson_lower_bound_95"] - 0.525 - 0.005 - 0.01,
        )

    def test_includes_rejected_decisions_with_fused_signal(self):
        record = _decision("D1", "slug-a", 0.71, "bullish")
        record["rejected_at_gate"] = "trend_filter_neutral"
        resolver = _Resolver({"slug-a": _gamma_market("slug-a", "long")})

        report = ac.analyze_path_b([record], resolver, Decimal("0.005"), Decimal("0.01"))

        self.assertEqual(report["resolved_calibration_records"], 1)
        self.assertEqual(report["buckets"][0.7]["win_rate"], 1.0)

    def test_excludes_pending_and_pre_fusion_records(self):
        records = [
            _decision("D1", "slug-open", 0.72, "bullish"),
            _decision("D2", "slug-missing-fusion", None, None),
            _decision("D3", None, 0.80, "bearish"),
        ]
        resolver = _Resolver({"slug-open": _gamma_market("slug-open", "long", closed=False)})

        report = ac.analyze_path_b(records, resolver, Decimal("0.005"), Decimal("0.01"))

        self.assertEqual(report["buckets"], {})
        self.assertEqual(report["excluded_records"]["pending_market"], 1)
        self.assertEqual(report["excluded_records"]["missing_fused_signal"], 1)
        self.assertEqual(report["excluded_records"]["missing_market_slug"], 1)

    def test_excludes_no_closed_gamma_match_as_pending_market(self):
        records = [_decision("D1", "slug-open", 0.72, "bullish")]
        resolver = _Resolver({"slug-open": None})

        report = ac.analyze_path_b(records, resolver, Decimal("0.005"), Decimal("0.01"))

        self.assertEqual(report["buckets"], {})
        self.assertEqual(report["excluded_records"]["pending_market"], 1)
        self.assertEqual(report["resolved_calibration_records"], 0)

    def test_excludes_records_with_omitted_decision_fields(self):
        records = [
            {"decision_id": "D1", "fused_confidence": 0.72, "fused_direction": "bullish"},
            {"decision_id": "D2", "slug": "slug-missing-fused"},
            {
                "slug": "slug-missing-entry",
                "fused_confidence": 0.74,
                "fused_direction": "bullish",
            },
        ]
        resolver = _Resolver(
            {"slug-missing-entry": _gamma_market("slug-missing-entry", "long")}
        )

        report = ac.analyze_path_b(records, resolver, Decimal("0.005"), Decimal("0.01"))

        self.assertEqual(report["total_decision_records"], 3)
        self.assertEqual(report["resolved_calibration_records"], 1)
        self.assertEqual(report["excluded_records"]["missing_market_slug"], 1)
        self.assertEqual(report["excluded_records"]["missing_fused_signal"], 1)
        self.assertEqual(report["excluded_records"]["missing_entry_metrics"], 1)
        self.assertEqual(report["buckets"][0.7]["entry_samples"], 0)
        self.assertIsNone(report["buckets"][0.7]["entry_win_rate"])
        self.assertIsNone(report["buckets"][0.7]["probability_edge"])
        self.assertIsNone(report["buckets"][0.7]["wilson_edge_after_buffers"])

    def test_path_b_edge_metrics_use_entry_complete_cohort(self):
        entry_missing_winner = _decision(
            "D1", "slug-missing-entry-winner", 0.72, "bullish"
        )
        entry_missing_winner.pop("executable_entry")
        entry_missing_winner.pop("estimated_actual_cost")
        records = [
            entry_missing_winner,
            _decision("D2", "slug-entry-loser", 0.73, "bearish", entry="0.55", cost="5.00"),
        ]
        resolver = _Resolver(
            {
                "slug-missing-entry-winner": _gamma_market(
                    "slug-missing-entry-winner", "long"
                ),
                "slug-entry-loser": _gamma_market("slug-entry-loser", "long"),
            }
        )

        report = ac.analyze_path_b(records, resolver, Decimal("0.005"), Decimal("0.01"))

        bucket = report["buckets"][0.7]
        self.assertEqual(bucket["n"], 2)
        self.assertAlmostEqual(bucket["win_rate"], 0.5)
        self.assertEqual(bucket["entry_samples"], 1)
        self.assertAlmostEqual(bucket["entry_win_rate"], 0.0)
        self.assertAlmostEqual(bucket["weighted_avg_entry_price"], 0.55)
        self.assertAlmostEqual(bucket["probability_edge"], -0.55)

    def test_closed_market_without_one_winner_raises(self):
        record = _decision("D1", "slug-bad", 0.72, "bullish")
        resolver = _Resolver({"slug-bad": _gamma_market("slug-bad", "none")})

        with self.assertRaisesRegex(ValueError, "no winning outcome"):
            ac.analyze_path_b([record], resolver, Decimal("0.005"), Decimal("0.01"))


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
