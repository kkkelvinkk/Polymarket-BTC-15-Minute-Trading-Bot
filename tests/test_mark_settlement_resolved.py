import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "mark_settlement_resolved.py"


class MarkSettlementResolvedTests(unittest.TestCase):
    def _write_ledger(self, path: Path):
        path.write_text(
            json.dumps(
                {
                    "open": {},
                    "settled": [
                        {
                            "order_id": "order-1",
                            "settlement_source": "SETTLEMENT_UNKNOWN",
                            "needs_reconciliation": True,
                            "size": "2.00",
                            "filled_qty": "4",
                            "payout": "UNKNOWN",
                            "pnl": "UNKNOWN",
                        }
                    ],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                }
            ),
            encoding="utf-8",
        )

    def test_rejects_manual_overpayout_without_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--payout",
                    "40",
                    "--reason",
                    "unit test",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exceeds filled token units", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_allows_manual_overpayout_with_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--payout",
                    "40",
                    "--allow-overpayout",
                    "--reason",
                    "unit test override",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "40")

    def test_rejects_positive_payout_when_filled_units_are_missing_without_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0].pop("filled_qty")
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--payout",
                    "4",
                    "--reason",
                    "unit test missing units",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("positive payout requires known positive filled token units", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_allows_positive_payout_with_missing_filled_units_only_with_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0].pop("filled_qty")
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--payout",
                    "4",
                    "--allow-overpayout",
                    "--reason",
                    "unit test missing units override",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "4")

    def test_creates_unknown_from_external_order_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--create-unknown-from-external-order",
                    "external-order-1",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--external-direction",
                    "long",
                    "--external-trade-label",
                    "YES (UP)",
                    "--external-instrument-id",
                    "cond-token.POLYMARKET",
                    "--external-token-id",
                    "token-yes",
                    "--external-slug",
                    "slug-external",
                    "--external-condition-id",
                    "cond-external",
                    "--external-submitted-at",
                    "2026-05-18T12:00:00Z",
                    "--external-filled-at",
                    "2026-05-18T12:00:02Z",
                    "--external-market-end-time",
                    "2026-05-18T12:15:00Z",
                    "--reason",
                    "unit test external reconstruction",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            created = next(trade for trade in data["settled"] if trade["order_id"] == "external-order-1")
            self.assertEqual(created["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(created["needs_reconciliation"])
            self.assertEqual(created["filled_qty"], "4")
            self.assertEqual(created["size"], "2.00")
            self.assertEqual(created["slug"], "slug-external")

    def test_external_order_creation_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--create-unknown-from-external-order",
                    "external-order-1",
                    "--reason",
                    "unit test missing confirmation",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--create-unknown-from-external-order requires --confirm-external-order", result.stderr + result.stdout)

    def test_external_order_creation_rejects_inconsistent_fill_math(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--create-unknown-from-external-order",
                    "external-order-bad-math",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.90",
                    "--external-filled-qty",
                    "4",
                    "--external-direction",
                    "long",
                    "--external-trade-label",
                    "YES (UP)",
                    "--external-instrument-id",
                    "cond-token.POLYMARKET",
                    "--external-token-id",
                    "token-yes",
                    "--external-slug",
                    "slug-external",
                    "--external-condition-id",
                    "cond-external",
                    "--external-submitted-at",
                    "2026-05-18T12:00:00Z",
                    "--external-filled-at",
                    "2026-05-18T12:00:02Z",
                    "--external-market-end-time",
                    "2026-05-18T12:15:00Z",
                    "--reason",
                    "unit test bad math",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must match --external-entry-price", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(any(trade.get("order_id") == "external-order-bad-math" for trade in data["settled"]))

    def test_external_order_creation_rejects_entry_price_above_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--create-unknown-from-external-order",
                    "external-order-bad-price",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "2.00",
                    "--external-filled-qty",
                    "1",
                    "--external-direction",
                    "long",
                    "--external-trade-label",
                    "YES (UP)",
                    "--external-instrument-id",
                    "cond-token.POLYMARKET",
                    "--external-token-id",
                    "token-yes",
                    "--external-slug",
                    "slug-external",
                    "--external-condition-id",
                    "cond-external",
                    "--external-submitted-at",
                    "2026-05-18T12:00:00Z",
                    "--external-filled-at",
                    "2026-05-18T12:00:02Z",
                    "--external-market-end-time",
                    "2026-05-18T12:15:00Z",
                    "--reason",
                    "unit test bad price",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--external-entry-price must be less than or equal to 1", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(any(trade.get("order_id") == "external-order-bad-price" for trade in data["settled"]))

    def test_external_order_creation_rejects_tiny_relative_notional_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--create-unknown-from-external-order",
                    "external-order-relative-mismatch",
                    "--confirm-external-order",
                    "--external-size",
                    "0.01",
                    "--external-entry-price",
                    "0.019",
                    "--external-filled-qty",
                    "1",
                    "--external-direction",
                    "long",
                    "--external-trade-label",
                    "YES (UP)",
                    "--external-instrument-id",
                    "cond-token.POLYMARKET",
                    "--external-token-id",
                    "token-yes",
                    "--external-slug",
                    "slug-external",
                    "--external-condition-id",
                    "cond-external",
                    "--external-submitted-at",
                    "2026-05-18T12:00:00Z",
                    "--external-filled-at",
                    "2026-05-18T12:00:02Z",
                    "--external-market-end-time",
                    "2026-05-18T12:15:00Z",
                    "--reason",
                    "unit test tiny mismatch",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must match --external-entry-price", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(any(trade.get("order_id") == "external-order-relative-mismatch" for trade in data["settled"]))

    def test_external_order_creation_rejects_impossible_chronology(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--create-unknown-from-external-order",
                    "external-order-bad-time",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--external-direction",
                    "long",
                    "--external-trade-label",
                    "YES (UP)",
                    "--external-instrument-id",
                    "cond-token.POLYMARKET",
                    "--external-token-id",
                    "token-yes",
                    "--external-slug",
                    "slug-external",
                    "--external-condition-id",
                    "cond-external",
                    "--external-submitted-at",
                    "2026-05-18T12:00:03Z",
                    "--external-filled-at",
                    "2026-05-18T12:00:02Z",
                    "--external-market-end-time",
                    "2026-05-18T12:15:00Z",
                    "--reason",
                    "unit test bad chronology",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--external-submitted-at must be before or equal to --external-filled-at", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(any(trade.get("order_id") == "external-order-bad-time" for trade in data["settled"]))

    def test_external_order_creation_rejects_market_end_before_fill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--create-unknown-from-external-order",
                    "external-order-market-ended",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--external-direction",
                    "long",
                    "--external-trade-label",
                    "YES (UP)",
                    "--external-instrument-id",
                    "cond-token.POLYMARKET",
                    "--external-token-id",
                    "token-yes",
                    "--external-slug",
                    "slug-external",
                    "--external-condition-id",
                    "cond-external",
                    "--external-submitted-at",
                    "2026-05-18T12:00:00Z",
                    "--external-filled-at",
                    "2026-05-18T12:00:02Z",
                    "--external-market-end-time",
                    "2026-05-18T12:00:01Z",
                    "--reason",
                    "unit test market ended before fill",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--external-filled-at must be before or equal to --external-market-end-time", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(any(trade.get("order_id") == "external-order-market-ended" for trade in data["settled"]))


if __name__ == "__main__":
    unittest.main()
