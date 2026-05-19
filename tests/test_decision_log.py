"""Tests for Phase 2.4 decision_log.DecisionRecord."""

import json
import os
import unittest
from decimal import Decimal
from pathlib import Path

from decision_log import DecisionRecord, _default_decision_log_path


class DecisionRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(f"/tmp/test_decisions_{os.getpid()}_{id(self)}.jsonl")
        self.path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.path.unlink(missing_ok=True)

    def _read_lines(self) -> list[dict]:
        with open(self.path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_records_decided_direction_on_clean_exit(self):
        with DecisionRecord(current_price=Decimal("50000.00"), path=self.path) as rec:
            rec.update(slug="btc-updown-15m-1779", fused_confidence=0.78)
            rec.decided(direction="long", executable_entry="0.62")
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["decided_direction"], "long")
        self.assertIsNone(lines[0]["rejected_at_gate"])
        self.assertEqual(lines[0]["slug"], "btc-updown-15m-1779")
        self.assertEqual(lines[0]["fused_confidence"], 0.78)
        self.assertEqual(lines[0]["executable_entry"], "0.62")
        self.assertEqual(lines[0]["current_price"], "50000.00")

    def test_records_rejection_on_early_return(self):
        with DecisionRecord(current_price=Decimal("50000.00"), path=self.path) as rec:
            rec.update(fused_confidence=0.55)
            rec.reject("min_confidence", "0.55 < MIN_SIGNAL_CONFIDENCE 0.70")
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["rejected_at_gate"], "min_confidence")
        self.assertEqual(
            lines[0]["rejection_reason"],
            "0.55 < MIN_SIGNAL_CONFIDENCE 0.70",
        )
        self.assertIsNone(lines[0]["decided_direction"])

    def test_records_exception_branch(self):
        with self.assertRaises(ValueError):
            with DecisionRecord(current_price=None, path=self.path) as rec:
                rec.update(fused_confidence=0.99)
                raise ValueError("processor blew up")
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["rejected_at_gate"], "exception")
        self.assertIn("ValueError", lines[0]["rejection_reason"])
        self.assertIn("processor blew up", lines[0]["rejection_reason"])
        self.assertIsNone(lines[0]["decided_direction"])

    def test_emits_exactly_one_record_per_invocation(self):
        # Even if the caller calls reject() multiple times (which would be a
        # bug), still only one record is written (the last reject wins).
        with DecisionRecord(current_price=Decimal("50000"), path=self.path) as rec:
            rec.reject("first_gate", "first reason")
            rec.reject("second_gate", "second reason")
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["rejected_at_gate"], "second_gate")

    def test_decided_clears_rejection_fields(self):
        # If a caller first sets reject() then changes its mind and calls
        # decided(), the record reflects the decision (not the rejection).
        with DecisionRecord(current_price=Decimal("50000"), path=self.path) as rec:
            rec.reject("liquidity", "thin book")
            rec.decided(direction="short")
        lines = self._read_lines()
        self.assertEqual(lines[0]["decided_direction"], "short")
        self.assertIsNone(lines[0]["rejected_at_gate"])
        self.assertIsNone(lines[0]["rejection_reason"])

    def test_decimal_and_datetime_serialization(self):
        from datetime import datetime, timezone
        end_time = datetime(2026, 5, 19, 15, 0, tzinfo=timezone.utc)
        with DecisionRecord(current_price=Decimal("12345.678"), path=self.path) as rec:
            rec.update(
                market_end_time=end_time,
                yes_ask=Decimal("0.6253"),
                no_ask=Decimal("0.3747"),
            )
            rec.decided(direction="long")
        lines = self._read_lines()
        self.assertEqual(lines[0]["market_end_time"], "2026-05-19T15:00:00+00:00")
        self.assertEqual(lines[0]["yes_ask"], "0.6253")
        self.assertEqual(lines[0]["no_ask"], "0.3747")

    def test_default_path_uses_decision_log_path_env(self):
        original = os.environ.get("DECISION_LOG_PATH")
        try:
            os.environ["DECISION_LOG_PATH"] = "/tmp/explicit-decisions.jsonl"
            self.assertEqual(
                _default_decision_log_path(),
                Path("/tmp/explicit-decisions.jsonl"),
            )
        finally:
            if original is None:
                os.environ.pop("DECISION_LOG_PATH", None)
            else:
                os.environ["DECISION_LOG_PATH"] = original

    def test_default_path_falls_back_to_ledger_sibling(self):
        original_decision = os.environ.get("DECISION_LOG_PATH")
        original_ledger = os.environ.get("LIVE_TRADE_LEDGER_PATH")
        try:
            os.environ.pop("DECISION_LOG_PATH", None)
            ledger_path = Path("/tmp/ledger-dir/live_trades.json").resolve()
            os.environ["LIVE_TRADE_LEDGER_PATH"] = str(ledger_path)
            self.assertEqual(
                _default_decision_log_path(),
                ledger_path.parent / "decisions.jsonl",
            )
        finally:
            if original_decision is None:
                os.environ.pop("DECISION_LOG_PATH", None)
            else:
                os.environ["DECISION_LOG_PATH"] = original_decision
            if original_ledger is None:
                os.environ.pop("LIVE_TRADE_LEDGER_PATH", None)
            else:
                os.environ["LIVE_TRADE_LEDGER_PATH"] = original_ledger

    def test_decision_id_is_unique_per_record(self):
        ids = set()
        for _ in range(10):
            with DecisionRecord(current_price=None, path=self.path) as rec:
                rec.reject("noop", "test")
                ids.add(rec.fields["decision_id"])
        self.assertEqual(len(ids), 10)
        lines = self._read_lines()
        self.assertEqual(len(lines), 10)

    def test_write_failure_propagates(self):
        # Pointing path at a non-writable location should propagate the OSError
        # rather than silently dropping the record.
        bad_path = Path("/proc/cannot-write/decisions.jsonl")
        with self.assertRaises(OSError):
            with DecisionRecord(current_price=None, path=bad_path) as rec:
                rec.reject("noop", "test")


if __name__ == "__main__":
    unittest.main()
