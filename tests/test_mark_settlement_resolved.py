import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "mark_settlement_resolved.py"


class MarkSettlementResolvedTests(unittest.TestCase):
    def _write_ledger(self, path: Path):
        path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [
                        {
                            "order_id": "order-1",
                            "settlement_source": "SETTLEMENT_UNKNOWN",
                            "needs_reconciliation": True,
                            "size": "2.00",
                            "filled_qty": "4",
                            "entry_price": "0.50",
                            "filled_notional": "2.00",
                            "payout": "UNKNOWN",
                            "pnl": "UNKNOWN",
                        }
                    ],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {},
                    "submitted_order_intents": {},
                }
            ),
            encoding="utf-8",
        )

    def _pending_actual_fill(
        self,
        *,
        fill_key: str = "trade:pending",
        filled_qty: str = "4",
        price: str = "0.50",
        venue_order_id: str | None = "0xpending",
        submitted_size: str | None = "5.00",
    ):
        notional = str(Decimal(filled_qty) * Decimal(price))
        pending = {
            "received_at": "2026-05-18T12:00:00+00:00",
            "condition_id": "cond-pending",
            "token_id": "token-pending",
            "fills": [
                {
                    "fill_key": fill_key,
                    "filled_qty": filled_qty,
                    "price": price,
                    "notional": notional,
                    "raw_callback_payload": {"status": "ok", "trade_id": fill_key},
                    "received_at": "2026-05-18T12:00:00+00:00",
                }
            ],
            "total_filled_qty": filled_qty,
            "total_filled_notional": notional,
            "vwap": price,
        }
        if venue_order_id is not None:
            pending["venue_order_id"] = venue_order_id
        if submitted_size is not None:
            pending["submitted_size"] = submitted_size
        return pending

    def test_requires_explicit_ledger_path(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--list-pending-actual-fills",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--ledger", result.stderr + result.stdout)

    def test_rejects_ledgers_missing_core_sections_before_rewrite(self):
        required_sections = [
            "open",
            "settled",
            "seen_auto_redeem_events",
            "pending_auto_redeem_events",
            "pending_actual_fills",
            "submitted_order_intents",
        ]
        for section in required_sections:
            with self.subTest(section=section):
                with tempfile.TemporaryDirectory() as tmpdir:
                    ledger = Path(tmpdir) / "live_trades.json"
                    self._write_ledger(ledger)
                    data = json.loads(ledger.read_text(encoding="utf-8"))
                    data.pop(section)
                    ledger.write_text(json.dumps(data), encoding="utf-8")
                    before = ledger.read_text(encoding="utf-8")

                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            "--ledger",
                            str(ledger),
                            "--list-pending-actual-fills",
                        ],
                        cwd=REPO_ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"ledger missing required section: {section}", result.stderr + result.stdout)
                    self.assertEqual(ledger.read_text(encoding="utf-8"), before)

    def test_rejects_malformed_ledger_entries_before_rewrite(self):
        cases = [
            ("open", lambda data: data["open"].update({"bad-open": "not-an-object"}), "open[bad-open]"),
            ("settled", lambda data: data["settled"].append("not-an-object"), "settled[1]"),
            (
                "pending_auto_redeem_events",
                lambda data: data["pending_auto_redeem_events"].update({"bad-redeem": "not-an-object"}),
                "pending_auto_redeem_events[bad-redeem]",
            ),
            (
                "pending_actual_fills",
                lambda data: data["pending_actual_fills"].update({"bad-fill": "not-an-object"}),
                "pending_actual_fills[bad-fill]",
            ),
            (
                "submitted_order_intents",
                lambda data: data["submitted_order_intents"].update({"bad-intent": "not-an-object"}),
                "submitted_order_intents[bad-intent]",
            ),
        ]
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmpdir:
                    ledger = Path(tmpdir) / "live_trades.json"
                    self._write_ledger(ledger)
                    data = json.loads(ledger.read_text(encoding="utf-8"))
                    mutate(data)
                    ledger.write_text(json.dumps(data), encoding="utf-8")
                    before = ledger.read_text(encoding="utf-8")

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
                            "unit test malformed entry",
                        ],
                        cwd=REPO_ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr + result.stdout)
                    self.assertEqual(ledger.read_text(encoding="utf-8"), before)

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
            self.assertTrue(data["settled"][0]["manual_reconciliation_allow_overpayout"])

    def test_rejects_repair_or_resolve_when_source_is_not_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-unknown-fill-accounting",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--payout",
                    "4",
                    "--reason",
                    "unit test not unknown",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not an unresolved SETTLEMENT_UNKNOWN record", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")
            self.assertNotIn("external_fill_repair_at", data["settled"][0])

    def test_rejects_repair_or_resolve_when_not_marked_needs_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "SETTLEMENT_UNKNOWN"
            data["settled"][0]["needs_reconciliation"] = False
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-unknown-fill-accounting",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--payout",
                    "4",
                    "--reason",
                    "unit test not unresolved",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not an unresolved SETTLEMENT_UNKNOWN record", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")
            self.assertNotIn("external_fill_repair_at", data["settled"][0])

    def test_repairs_inconsistent_resolved_record_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "4"
            data["settled"][0]["pnl"] = "2.00"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test stale flag repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "manual_reconciliation")
            self.assertFalse(repaired["needs_reconciliation"])
            self.assertEqual(
                repaired["settlement_flag_repair_previous_state"]["needs_reconciliation"],
                True,
            )
            self.assertEqual(repaired["settlement_flag_repair_reason"], "unit test stale flag repair")

    def test_repair_inconsistent_resolved_record_rejects_malformed_amounts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "not-a-decimal"
            data["settled"][0]["pnl"] = "also-bad"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test malformed resolved repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")
            self.assertEqual(
                repaired["settlement_flag_repair_previous_state"]["settlement_source"],
                "manual_reconciliation",
            )
            self.assertEqual(
                repaired["settlement_flag_repair_previous_state"]["payout"],
                "not-a-decimal",
            )
            self.assertEqual(
                repaired["settlement_flag_repair_previous_state"]["pnl"],
                "also-bad",
            )

    def test_repair_inconsistent_resolved_record_rejects_negative_payout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "-1"
            data["settled"][0]["pnl"] = "-3.00"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test negative payout repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")
            self.assertEqual(repaired["settlement_flag_repair_previous_state"]["payout"], "-1")

    def test_repair_inconsistent_resolved_record_rejects_pnl_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "4"
            data["settled"][0]["pnl"] = "999"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test pnl mismatch repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")
            self.assertEqual(repaired["settlement_flag_repair_previous_state"]["pnl"], "999")

    def test_repair_inconsistent_resolved_record_rejects_overpayout_without_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "40"
            data["settled"][0]["pnl"] = "38.00"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test overpayout stale repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")
            self.assertEqual(repaired["settlement_flag_repair_previous_state"]["payout"], "40")

    def test_repair_inconsistent_resolved_record_allows_prior_overpayout_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "40"
            data["settled"][0]["pnl"] = "38.00"
            data["settled"][0]["manual_reconciliation_allow_overpayout"] = True
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test prior overpayout marker",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "manual_reconciliation")
            self.assertFalse(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "40")

    def test_repair_inconsistent_resolved_record_rejects_missing_filled_qty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "4"
            data["settled"][0]["pnl"] = "2.00"
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
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test missing filled qty stale repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")

    def test_repair_inconsistent_resolved_record_rejects_missing_entry_price(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "4"
            data["settled"][0]["pnl"] = "2.00"
            data["settled"][0].pop("entry_price")
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test missing entry stale repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")

    def test_repair_inconsistent_resolved_record_rejects_zero_filled_qty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["filled_qty"] = "0"
            data["settled"][0]["payout"] = "0"
            data["settled"][0]["pnl"] = "-2.00"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test zero filled qty stale repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")

    def test_repairs_inconsistent_incomplete_record_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "manual_reconciliation"
            data["settled"][0]["needs_reconciliation"] = True
            data["settled"][0]["payout"] = "UNKNOWN"
            data["settled"][0]["pnl"] = "UNKNOWN"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test incomplete flag repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(
                repaired["settlement_flag_repair_previous_state"]["settlement_source"],
                "manual_reconciliation",
            )
            self.assertEqual(repaired["settlement_flag_repair_reason"], "unit test incomplete flag repair")

    def test_repairs_inconsistent_unknown_record_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["settlement_source"] = "SETTLEMENT_UNKNOWN"
            data["settled"][0]["needs_reconciliation"] = False
            data["settled"][0]["payout"] = "4"
            data["settled"][0]["pnl"] = "2.00"
            data["settled"][0]["exit_price"] = "1"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-inconsistent-settlement-flags",
                    "--confirm-inconsistent-settlement-flags",
                    "--reason",
                    "unit test unknown flag repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertTrue(repaired["needs_reconciliation"])
            self.assertEqual(repaired["payout"], "UNKNOWN")
            self.assertEqual(repaired["pnl"], "UNKNOWN")
            self.assertNotIn("exit_price", repaired)
            self.assertEqual(
                repaired["settlement_flag_repair_previous_state"]["needs_reconciliation"],
                False,
            )
            self.assertEqual(repaired["settlement_flag_repair_previous_state"]["payout"], "4")
            self.assertEqual(repaired["settlement_flag_repair_previous_state"]["pnl"], "2.00")
            self.assertEqual(repaired["settlement_flag_repair_previous_state"]["exit_price"], "1")
            self.assertEqual(repaired["settlement_flag_repair_reason"], "unit test unknown flag repair")

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
            self.assertIn("filled_qty is required for manual reconciliation", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_missing_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0].pop("size")
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
                    "unit test missing size",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("size is required for reconciliation", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_missing_entry_price(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0].pop("entry_price")
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
                    "unit test missing entry price",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("entry_price is required for reconciliation", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_missing_filled_notional(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0].pop("filled_notional")
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
                    "unit test missing filled notional",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("filled_notional is required for reconciliation", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_inconsistent_cost_basis(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["entry_price"] = "0.40"
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
                    "unit test inconsistent cost",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must match entry_price", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_zero_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["size"] = "0"
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
                    "0",
                    "--reason",
                    "unit test zero size",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("size must be greater than 0", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_negative_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["size"] = "-2.00"
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
                    "0",
                    "--reason",
                    "unit test negative size",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("size must be greater than 0", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_negative_filled_qty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["filled_qty"] = "-4"
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
                    "0",
                    "--reason",
                    "unit test negative filled qty",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("filled_qty must be non-negative", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_manual_resolution_with_zero_filled_qty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["filled_qty"] = "0"
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
                    "0",
                    "--reason",
                    "unit test zero filled qty",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("manual reconciliation requires known positive filled token units", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_missing_filled_units_even_with_override(self):
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

            self.assertNotEqual(result.returncode, 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_rejects_estimated_tokens_as_filled_units(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0].pop("filled_qty")
            data["settled"][0]["estimated_tokens"] = "4"
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
                    "unit test estimated tokens",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("estimated_tokens is not a verified fill unit count", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")

    def test_repairs_existing_unknown_fill_accounting_and_resolves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0].pop("size")
            data["settled"][0].pop("filled_qty")
            data["settled"][0]["submitted_size"] = "5.00"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--order-id",
                    "order-1",
                    "--repair-unknown-fill-accounting",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--payout",
                    "4",
                    "--reason",
                    "unit test verified external fill repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            repaired = data["settled"][0]
            self.assertEqual(repaired["settlement_source"], "manual_reconciliation")
            self.assertFalse(repaired["needs_reconciliation"])
            self.assertEqual(repaired["size"], "2.00")
            self.assertEqual(repaired["entry_price"], "0.50")
            self.assertEqual(repaired["filled_qty"], "4")
            self.assertEqual(repaired["filled_notional"], "2.00")
            self.assertEqual(repaired["payout"], "4")
            self.assertEqual(repaired["pnl"], "2.00")
            self.assertEqual(repaired["external_fill_repair_reason"], "unit test verified external fill repair")
            self.assertEqual(repaired["external_fill_repair_previous_state"]["size"], None)
            self.assertEqual(repaired["submitted_size"], "5.00")

    def test_repair_existing_unknown_requires_explicit_confirmation(self):
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
                    "--repair-unknown-fill-accounting",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--payout",
                    "4",
                    "--reason",
                    "unit test missing repair confirmation",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--repair-unknown-fill-accounting requires --confirm-external-order", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")
            self.assertNotIn("external_fill_repair_at", data["settled"][0])

    def test_repair_existing_unknown_rejects_inconsistent_fill_math(self):
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
                    "--repair-unknown-fill-accounting",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.90",
                    "--external-filled-qty",
                    "4",
                    "--payout",
                    "4",
                    "--reason",
                    "unit test bad repair math",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must match --external-entry-price", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["payout"], "UNKNOWN")
            self.assertNotIn("external_fill_repair_at", data["settled"][0])

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

    def test_external_order_creation_rejects_same_order_pending_actual_fill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["pending_actual_fills"] = {
                "external-order-1": self._pending_actual_fill(venue_order_id="0xpending")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

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
                    "unit test pending conflict",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exists in pending_actual_fills", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(any(trade.get("order_id") == "external-order-1" for trade in data["settled"]))
            self.assertIn("external-order-1", data["pending_actual_fills"])

    def test_external_order_creation_replaces_external_repair_pending_actual_fill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            pending = self._pending_actual_fill(venue_order_id="0xpending-repair")
            pending["requires_external_fill_repair"] = True
            pending["external_fill_repair_reason"] = "duplicate_actual_fill_key"
            data["pending_actual_fills"] = {"external-order-1": pending}
            ledger.write_text(json.dumps(data), encoding="utf-8")

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
                    "unit test pending external repair",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertNotIn("external-order-1", data["pending_actual_fills"])
            created = next(trade for trade in data["settled"] if trade["order_id"] == "external-order-1")
            self.assertEqual(created["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertEqual(created["venue_order_id"], "0xpending-repair")
            self.assertEqual(created["external_repair_reason"], "duplicate_actual_fill_key")
            self.assertEqual(
                created["external_repair_pending_actual_fill"]["external_fill_repair_reason"],
                "duplicate_actual_fill_key",
            )
            self.assertEqual(created["filled_qty"], "4")
            self.assertEqual(created["entry_price"], "0.50")
            self.assertEqual(created["size"], "2.00")

    def test_external_order_creation_rejects_external_repair_pending_duplicate_venue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"].append(
                {
                    "order_id": "settled-venue",
                    "venue_order_id": "0xpending-repair",
                    "settlement_source": "manual_reconciliation",
                    "needs_reconciliation": False,
                    "size": "2.00",
                    "filled_qty": "4",
                    "entry_price": "0.50",
                    "payout": "4",
                    "pnl": "2.00",
                }
            )
            pending = self._pending_actual_fill(venue_order_id="0xPENDING-REPAIR")
            pending["requires_external_fill_repair"] = True
            pending["external_fill_repair_reason"] = "duplicate_actual_fill_key"
            data["pending_actual_fills"] = {"external-order-1": pending}
            ledger.write_text(json.dumps(data), encoding="utf-8")

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
                    "unit test duplicate pending external repair venue",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("venue_order_id=0xPENDING-REPAIR already exists", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertIn("external-order-1", data["pending_actual_fills"])
            self.assertFalse(any(trade.get("order_id") == "external-order-1" for trade in data["settled"]))

    def test_external_order_creation_rejects_same_order_submitted_intent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["submitted_order_intents"] = {
                "external-order-1": {
                    "status": "INTENT_PERSISTED",
                    "submitted_at": "2026-05-18T12:00:00+00:00",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

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
                    "unit test submitted intent conflict",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("use --convert-submitted-intent", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(any(trade.get("order_id") == "external-order-1" for trade in data["settled"]))
            self.assertIn("external-order-1", data["submitted_order_intents"])

    def test_lists_pending_actual_fills_without_reason(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["pending_actual_fills"] = {
                "order-pending": self._pending_actual_fill(venue_order_id="0xpending")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--list-pending-actual-fills",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Pending actual fills: 1", result.stdout)
            self.assertIn("order-pending", result.stdout)
            self.assertIn("0xpending", result.stdout)

    def test_lists_submitted_order_intents_without_reason(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["submitted_order_intents"] = {
                "intent-order": {
                    "status": "INTENT_PERSISTED",
                    "submitted_at": "2026-05-18T12:00:00+00:00",
                    "trade_label": "NO (DOWN)",
                    "order_side": "BUY",
                    "quote_quantity": True,
                    "spend_amount": "55",
                    "estimated_tokens": "76.3",
                    "estimated_price": "0.72",
                    "slug": "slug-intent",
                    "condition_id": "cond-intent",
                    "token_id": "token-no",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--list-submitted-order-intents",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Submitted order intents: 1", result.stdout)
            self.assertIn("intent-order", result.stdout)
            self.assertIn("NO (DOWN)", result.stdout)

    def test_resolves_unknown_by_venue_order_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"][0]["order_id"] = None
            data["settled"][0]["client_order_id"] = None
            data["settled"][0]["venue_order_id"] = "0xvenue"
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--venue-order-id",
                    "0xvenue",
                    "--payout",
                    "4",
                    "--reason",
                    "unit test venue resolution",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["settled"][0]["settlement_source"], "manual_reconciliation")
            self.assertFalse(data["settled"][0]["needs_reconciliation"])
            self.assertEqual(data["settled"][0]["payout"], "4")

    def test_converts_pending_actual_fill_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["pending_actual_fills"] = {
                "pending-order": self._pending_actual_fill(venue_order_id="0xpending")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-order",
                    "--reason",
                    "unit test convert pending",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertNotIn("pending-order", data["pending_actual_fills"])
            converted = [
                trade for trade in data["settled"]
                if trade.get("order_id") == "pending-order"
            ][0]
            self.assertEqual(converted["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertEqual(converted["venue_order_id"], "0xpending")
            self.assertEqual(converted["size"], "2.00")
            self.assertEqual(converted["submitted_size"], "5.00")
            self.assertEqual(converted["filled_qty"], "4")
            self.assertEqual(converted["entry_price"], "0.50")
            self.assertEqual(converted["filled_notional"], "2.00")
            self.assertEqual(converted["unknown_reason"], "manual pending-actual-fill conversion: unit test convert pending")

    def test_convert_pending_actual_fill_rejects_missing_required_accounting_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["pending_actual_fills"] = {
                "pending-missing": self._pending_actual_fill(venue_order_id="0xmissing", submitted_size=None)
            }
            data["pending_actual_fills"]["pending-missing"].pop("total_filled_notional")
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-missing",
                    "--reason",
                    "unit test missing field",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "pending_actual_fills[pending-missing].total_filled_notional",
                result.stderr + result.stdout,
            )
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertIn("pending-missing", data["pending_actual_fills"])
            self.assertFalse(
                any(trade.get("order_id") == "pending-missing" for trade in data["settled"])
            )

    def test_convert_pending_actual_fill_rejects_duplicate_venue_order_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"].append(
                {
                    "order_id": "existing-order",
                    "venue_order_id": "0xdup",
                    "settlement_source": "manual_reconciliation",
                    "needs_reconciliation": False,
                    "size": "2.00",
                    "filled_qty": "4",
                    "payout": "4",
                    "pnl": "2",
                }
            )
            data["pending_actual_fills"] = {
                "pending-dup": self._pending_actual_fill(venue_order_id="0xDUP", submitted_size="2.00")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-dup",
                    "--reason",
                    "unit test duplicate venue",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("venue_order_id=0xDUP already exists", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            matches = [
                trade for trade in data["settled"]
                if str(trade.get("venue_order_id") or "").lower() == "0xdup"
            ]
            self.assertEqual(len(matches), 1)
            self.assertIn("pending-dup", data["pending_actual_fills"])

    def test_convert_pending_actual_fill_rejects_duplicate_open_venue_order_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["open"] = {
                "open-dup": {
                    "order_id": "open-dup",
                    "venue_order_id": "0xdup-open",
                    "size": "2.00",
                    "filled_qty": "4",
                    "entry_price": "0.50",
                }
            }
            data["pending_actual_fills"] = {
                "pending-dup-open": self._pending_actual_fill(venue_order_id="0xDUP-OPEN", submitted_size="2.00")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-dup-open",
                    "--reason",
                    "unit test duplicate open venue",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists in open trades", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertIn("open-dup", data["open"])
            self.assertIn("pending-dup-open", data["pending_actual_fills"])

    def test_convert_pending_actual_fill_removes_matching_open_trade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["open"] = {
                "pending-open": {
                    "order_id": "pending-open",
                    "venue_order_id": "0xpending-open",
                    "size": "2.00",
                    "filled_qty": "4",
                    "entry_price": "0.50",
                }
            }
            data["pending_actual_fills"] = {
                "pending-open": self._pending_actual_fill(venue_order_id="0xpending-open", submitted_size="2.00")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-open",
                    "--reason",
                    "unit test matching open conversion",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertNotIn("pending-open", data["open"])
            self.assertNotIn("pending-open", data["pending_actual_fills"])
            self.assertTrue(any(trade.get("order_id") == "pending-open" for trade in data["settled"]))

    def test_convert_pending_actual_fill_preserves_open_venue_when_payload_omits_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["open"] = {
                "pending-open": {
                    "order_id": "pending-open",
                    "venue_order_id": "0xopen-venue",
                    "size": "2.00",
                    "filled_qty": "4",
                    "entry_price": "0.50",
                }
            }
            data["pending_actual_fills"] = {
                "pending-open": self._pending_actual_fill(venue_order_id=None, submitted_size="2.00")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-open",
                    "--reason",
                    "unit test open venue preservation",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            converted = [trade for trade in data["settled"] if trade.get("order_id") == "pending-open"][0]
            self.assertEqual(converted["venue_order_id"], "0xopen-venue")

    def test_convert_pending_actual_fill_rejects_open_venue_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["open"] = {
                "pending-open": {
                    "order_id": "pending-open",
                    "venue_order_id": "0xopen-venue",
                }
            }
            data["pending_actual_fills"] = {
                "pending-open": self._pending_actual_fill(venue_order_id="0xpayload-venue", submitted_size="2.00")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-open",
                    "--reason",
                    "unit test open venue mismatch",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("venue_order_id mismatch", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertIn("pending-open", data["open"])
            self.assertIn("pending-open", data["pending_actual_fills"])

    def test_convert_pending_actual_fill_rejects_duplicate_pending_venue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["pending_actual_fills"] = {
                "pending-open": self._pending_actual_fill(venue_order_id="0xpending-venue", submitted_size="2.00"),
                "other-pending": self._pending_actual_fill(
                    fill_key="trade:other-pending",
                    filled_qty="1",
                    venue_order_id="0xPENDING-VENUE",
                    submitted_size="0.50",
                ),
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-open",
                    "--reason",
                    "unit test duplicate pending venue",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists in pending_actual_fills", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertIn("pending-open", data["pending_actual_fills"])

    def test_convert_pending_actual_fill_rejects_malformed_open_trade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["open"] = {"pending-open": "not-an-object"}
            data["pending_actual_fills"] = {
                "pending-open": self._pending_actual_fill(venue_order_id=None, submitted_size="2.00")
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-open",
                    "--reason",
                    "unit test malformed open",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be a JSON object", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["open"]["pending-open"], "not-an-object")
            self.assertIn("pending-open", data["pending_actual_fills"])

    def test_convert_pending_actual_fill_consumes_matching_submitted_intent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["pending_actual_fills"] = {
                "pending-intent": self._pending_actual_fill(venue_order_id=None, submitted_size="2.00")
            }
            data["submitted_order_intents"] = {
                "pending-intent": {
                    "status": "INTENT_PERSISTED",
                    "trade_label": "YES (UP)",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-pending-actual-fill",
                    "pending-intent",
                    "--reason",
                    "unit test intent consumption",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            converted = [trade for trade in data["settled"] if trade.get("order_id") == "pending-intent"][0]
            self.assertEqual(converted["submitted_order_intent"]["trade_label"], "YES (UP)")
            self.assertNotIn("pending-intent", data["submitted_order_intents"])

    def test_resolves_submitted_intent_as_no_exchange_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["submitted_order_intents"] = {
                "intent-no-order": {
                    "status": "INTENT_PERSISTED",
                    "submitted_at": "2026-05-18T12:00:00+00:00",
                    "trade_label": "YES (UP)",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--resolve-submitted-intent-no-order",
                    "intent-no-order",
                    "--confirm-no-exchange-order",
                    "--reason",
                    "operator verified no order on exchange",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(
                data["submitted_order_intents"]["intent-no-order"]["status"],
                "SUBMISSION_NOT_SEEN",
            )
            self.assertFalse(data["submitted_order_intents"]["intent-no-order"]["needs_reconciliation"])
            self.assertIn("no exchange order", result.stdout)

    def test_resolve_submitted_intent_no_order_rejects_existing_open_trade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["open"] = {"intent-no-order": {"order_id": "intent-no-order"}}
            data["submitted_order_intents"] = {
                "intent-no-order": {
                    "status": "INTENT_PERSISTED",
                    "submitted_at": "2026-05-18T12:00:00+00:00",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--resolve-submitted-intent-no-order",
                    "intent-no-order",
                    "--confirm-no-exchange-order",
                    "--reason",
                    "operator verified no order on exchange",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exists in open trades", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["submitted_order_intents"]["intent-no-order"]["status"], "INTENT_PERSISTED")

    def test_resolve_submitted_intent_no_order_rejects_pending_actual_fill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["pending_actual_fills"] = {
                "intent-no-order": self._pending_actual_fill(venue_order_id=None, submitted_size="2.00")
            }
            data["submitted_order_intents"] = {
                "intent-no-order": {
                    "status": "INTENT_PERSISTED",
                    "submitted_at": "2026-05-18T12:00:00+00:00",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--resolve-submitted-intent-no-order",
                    "intent-no-order",
                    "--confirm-no-exchange-order",
                    "--reason",
                    "operator verified no order on exchange",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exists in pending_actual_fills", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["submitted_order_intents"]["intent-no-order"]["status"], "INTENT_PERSISTED")
            self.assertIn("intent-no-order", data["pending_actual_fills"])

    def test_resolve_submitted_intent_no_order_rejects_existing_settled_trade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["settled"].append(
                {
                    "order_id": "intent-no-order",
                    "settlement_source": "SETTLEMENT_UNKNOWN",
                    "needs_reconciliation": True,
                }
            )
            data["submitted_order_intents"] = {
                "intent-no-order": {
                    "status": "INTENT_PERSISTED",
                    "submitted_at": "2026-05-18T12:00:00+00:00",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--resolve-submitted-intent-no-order",
                    "intent-no-order",
                    "--confirm-no-exchange-order",
                    "--reason",
                    "operator verified no order on exchange",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exists in settled trades", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["submitted_order_intents"]["intent-no-order"]["status"], "INTENT_PERSISTED")
            self.assertEqual(data["settled"][-1]["order_id"], "intent-no-order")

    def test_resolve_submitted_intent_no_order_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["submitted_order_intents"] = {"intent-no-order": {"status": "INTENT_PERSISTED"}}
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--resolve-submitted-intent-no-order",
                    "intent-no-order",
                    "--reason",
                    "missing confirm",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--resolve-submitted-intent-no-order requires --confirm-no-exchange-order", result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertIn("intent-no-order", data["submitted_order_intents"])

    def test_converts_submitted_intent_to_unknown_with_verified_fill_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Path(tmpdir) / "live_trades.json"
            self._write_ledger(ledger)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            data["submitted_order_intents"] = {
                "intent-filled": {
                    "status": "INTENT_PERSISTED",
                    "submitted_at": "2026-05-18T12:00:00+00:00",
                    "trade_label": "NO (DOWN)",
                }
            }
            ledger.write_text(json.dumps(data), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--ledger",
                    str(ledger),
                    "--convert-submitted-intent",
                    "intent-filled",
                    "--confirm-external-order",
                    "--external-size",
                    "2.00",
                    "--external-entry-price",
                    "0.50",
                    "--external-filled-qty",
                    "4",
                    "--external-direction",
                    "short",
                    "--external-trade-label",
                    "NO (DOWN)",
                    "--external-instrument-id",
                    "cond-token.POLYMARKET",
                    "--external-token-id",
                    "token-no",
                    "--external-slug",
                    "slug-intent",
                    "--external-condition-id",
                    "cond-intent",
                    "--external-submitted-at",
                    "2026-05-18T12:00:00Z",
                    "--external-filled-at",
                    "2026-05-18T12:00:02Z",
                    "--external-market-end-time",
                    "2026-05-18T12:15:00Z",
                    "--reason",
                    "operator verified filled exchange order",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertNotIn("intent-filled", data["submitted_order_intents"])
            converted = next(trade for trade in data["settled"] if trade.get("order_id") == "intent-filled")
            self.assertEqual(converted["settlement_source"], "SETTLEMENT_UNKNOWN")
            self.assertEqual(converted["filled_qty"], "4")
            self.assertEqual(converted["submitted_order_intent"]["trade_label"], "NO (DOWN)")
            self.assertIn("submitted-intent conversion", converted["unknown_reason"])

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
