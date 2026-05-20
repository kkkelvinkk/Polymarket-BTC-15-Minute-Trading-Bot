import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import estimate_decision_results as estimator


class EstimateDecisionResultsTests(unittest.TestCase):
    def test_winning_side_reads_binary_gamma_outcome(self):
        market = {
            "slug": "btc-updown-15m-test",
            "closed": True,
            "outcomes": json.dumps(["Up", "Down"]),
            "outcomePrices": json.dumps(["1", "0"]),
        }

        self.assertEqual(estimator._winning_side(market), "long")

    def test_active_market_with_one_dollar_price_is_pending(self):
        market = {
            "slug": "btc-updown-15m-test",
            "closed": False,
            "outcomes": json.dumps(["Up", "Down"]),
            "outcomePrices": json.dumps(["1", "0"]),
        }

        self.assertIsNone(estimator._winning_side(market))

    def test_active_market_without_resolution_fields_is_pending(self):
        market = {
            "slug": "btc-updown-15m-test",
            "closed": False,
        }

        self.assertIsNone(estimator._winning_side(market))

    def test_closed_market_without_winner_fails_closed(self):
        market = {
            "slug": "btc-updown-15m-test",
            "closed": True,
            "outcomes": json.dumps(["Up", "Down"]),
            "outcomePrices": json.dumps(["0.50", "0.50"]),
        }

        with self.assertRaisesRegex(ValueError, "has no winning outcome"):
            estimator._winning_side(market)

    def test_estimate_record_calculates_binary_share_pnl(self):
        record = {
            "slug": "btc-updown-15m-test",
            "ts": "2026-05-20T00:00:00+00:00",
            "decided_direction": "long",
            "executable_entry": "0.50",
            "estimated_tokens_filled": "10.0",
            "estimated_actual_cost": "5.00",
        }

        result = estimator._estimate_record(record, "long", Decimal("5.00"))

        self.assertTrue(result["won"])
        self.assertEqual(result["tokens"], Decimal("10.0"))
        self.assertEqual(result["pnl"], Decimal("5.00"))

    def test_run_resolves_decision_file_with_fake_gamma_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            decisions_path = Path(tmp) / "decisions.jsonl"
            decisions_path.write_text(
                json.dumps(
                    {
                        "ts": "2026-05-20T00:00:00+00:00",
                        "slug": "btc-updown-15m-test",
                        "rejected_at_gate": None,
                        "decided_direction": "short",
                        "executable_entry": "0.25",
                        "estimated_tokens_filled": "20.0",
                        "estimated_actual_cost": "5.00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            class _Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return [
                        {
                            "slug": "btc-updown-15m-test",
                            "closed": True,
                            "outcomes": json.dumps(["Up", "Down"]),
                            "outcomePrices": json.dumps(["0", "1"]),
                        }
                    ]

            class _Client:
                def __init__(self, timeout):
                    self.timeout = timeout

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def get(self, url, params):
                    self.url = url
                    self.params = params
                    return _Response()

            original_client = estimator.httpx.Client
            estimator.httpx.Client = _Client
            try:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = estimator.run(decisions_path, Decimal("5.00"))
            finally:
                estimator.httpx.Client = original_client

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("ESTIMATE ONLY", output)
        self.assertIn("wins: 1", output)
        self.assertIn("estimated_pnl: $15.00", output)

    def test_decided_record_requires_estimated_execution_size(self):
        with self.assertRaisesRegex(ValueError, "missing estimated_tokens_filled"):
            estimator._decided_records(
                [
                    {
                        "ts": "2026-05-20T00:00:00+00:00",
                        "slug": "btc-updown-15m-test",
                        "rejected_at_gate": None,
                        "decided_direction": "long",
                        "executable_entry": "0.50",
                        "estimated_tokens_filled": None,
                        "estimated_actual_cost": "5.00",
                    }
                ]
            )

    def test_run_rejects_non_finite_stake(self):
        with tempfile.TemporaryDirectory() as tmp:
            decisions_path = Path(tmp) / "decisions.jsonl"
            decisions_path.write_text(
                json.dumps(
                    {
                        "ts": "2026-05-20T00:00:00+00:00",
                        "slug": "btc-updown-15m-test",
                        "rejected_at_gate": None,
                        "decided_direction": "long",
                        "executable_entry": "0.50",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "stake_usd must be positive"):
                estimator.run(decisions_path, Decimal("Infinity"))

            with self.assertRaisesRegex(ValueError, "stake_usd must be positive"):
                estimator.run(decisions_path, Decimal("NaN"))


if __name__ == "__main__":
    unittest.main()
