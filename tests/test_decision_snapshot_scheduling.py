import asyncio
import json
import os
import types
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from decision_snapshot_test_helpers import DecisionSnapshotTestCase


class TestDecisionSnapshotScheduling(DecisionSnapshotTestCase):
    def test_snapshot_is_captured_before_mode_check_can_mutate_live_state(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(strategy)
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._last_bid_ask = (Decimal("0.39"), Decimal("0.40"))
        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.40"),
            "decision-before-mode-check",
        )
        captured = {}

        async def _mode_check():
            strategy.price_history = [Decimal("0.90")] * 20
            strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
            strategy._price_history_ts = [None] * len(strategy.price_history)
            strategy._last_bid_ask = (Decimal("0.80"), Decimal("0.82"))
            return True

        async def _body(decision_snapshot, trade_key, is_simulation, rec, observation_only=False):
            captured["snapshot"] = decision_snapshot
            captured["is_simulation"] = is_simulation
            return False

        strategy.check_simulation_mode = _mode_check
        strategy._make_trading_decision_body = _body

        result = asyncio.run(strategy._make_trading_decision(snapshot))

        self.assertFalse(result)
        self.assertTrue(captured["is_simulation"])
        self.assertEqual(
            tuple(p.value for p in captured["snapshot"].price_history),
            tuple([Decimal("0.50")] * 20),
        )
        self.assertEqual(captured["snapshot"].yes_bid_ask, (Decimal("0.39"), Decimal("0.40")))

    def test_on_quote_tick_schedules_trigger_tick_snapshot(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.all_btc_instruments[0]["market_timestamp"] = (
            datetime.now(timezone.utc).timestamp() - 800
        )
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._market_stable = True
        strategy._stable_tick_count = 3
        strategy.last_trade_time = -1
        scheduled = []
        captured = {}
        original_snapshot_from_state = strategy._decision_snapshot_from_state

        def _capture_sync(snapshot, trade_key=None, strategy_observation_mode=None):
            captured["snapshot"] = snapshot
            captured["trade_key"] = trade_key
            captured["strategy_observation_mode"] = strategy_observation_mode

        strategy._make_trading_decision_sync = _capture_sync
        strategy.run_in_executor = lambda fn: scheduled.append(fn)

        def _mutating_snapshot_from_state(**kwargs):
            strategy.price_history = [Decimal("0.90")] * 20
            strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
            strategy._price_history_ts = [None] * len(strategy.price_history)
            strategy._last_bid_ask = (Decimal("0.80"), Decimal("0.82"))
            return original_snapshot_from_state(**kwargs)

        strategy._decision_snapshot_from_state = _mutating_snapshot_from_state

        strategy.on_quote_tick(self._quote_tick(strategy))
        strategy.price_history = [Decimal("0.90")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._last_bid_ask = (Decimal("0.80"), Decimal("0.82"))
        scheduled[0]()

        snapshot = captured["snapshot"]
        self.assertEqual(snapshot.current_price, Decimal("0.40"))
        self.assertEqual(snapshot.price_history[-1].value, Decimal("0.40"))
        self.assertNotIn(Decimal("0.90"), [p.value for p in snapshot.price_history])
        self.assertEqual(snapshot.yes_bid_ask, (Decimal("0.39"), Decimal("0.41")))
        self.assertEqual(snapshot.reference_time, snapshot.tick_buffer[-1].ts)
        self.assertEqual(snapshot.sub_interval, 0)
        self.assertGreaterEqual(snapshot.seconds_into_sub_interval, 780)
        self.assertLess(snapshot.seconds_into_sub_interval, 840)
        self.assertEqual(snapshot.trade_window_label, "13_14_current")
        self.assertIsNone(captured["strategy_observation_mode"])

    def test_on_quote_tick_skips_snapshot_copy_outside_candidate_windows(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.all_btc_instruments[0]["market_timestamp"] = (
            datetime.now(timezone.utc).timestamp() - 100
        )
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._market_stable = True
        strategy._stable_tick_count = 3
        strategy._capture_locked_decision_state = (
            lambda _reference_time: (_ for _ in ()).throw(
                AssertionError("snapshot copy should not run outside candidate windows")
            )
        )

        strategy.on_quote_tick(self._quote_tick(strategy))

    def test_on_quote_tick_skips_decision_scheduling_during_restart(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.all_btc_instruments[0]["market_timestamp"] = (
            datetime.now(timezone.utc).timestamp() - 800
        )
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._market_stable = True
        strategy._stable_tick_count = 3
        strategy._restart_in_progress = True
        strategy._capture_locked_decision_state = (
            lambda _reference_time: (_ for _ in ()).throw(
                AssertionError("snapshot copy should not run during restart")
            )
        )
        strategy.run_in_executor = (
            lambda _fn: (_ for _ in ()).throw(
                AssertionError("decision should not enqueue during restart")
            )
        )

        strategy.on_quote_tick(self._quote_tick(strategy))

        self.assertFalse(strategy._decision_in_progress)

    def test_live_decision_scheduling_waits_for_shadow_observation(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.all_btc_instruments[0]["market_timestamp"] = (
            datetime.now(timezone.utc).timestamp() - 800
        )
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._market_stable = True
        strategy._stable_tick_count = 3
        strategy._shadow_decision_in_progress = True
        strategy._capture_locked_decision_state = (
            lambda _reference_time: (_ for _ in ()).throw(
                AssertionError("live snapshot copy should not run during shadow observation")
            )
        )
        strategy.run_in_executor = (
            lambda _fn: (_ for _ in ()).throw(
                AssertionError("live decision should not enqueue during shadow observation")
            )
        )

        strategy.on_quote_tick(self._quote_tick(strategy))

        self.assertFalse(strategy._decision_in_progress)
        self.assertTrue(strategy._shadow_decision_in_progress)

    def test_live_executor_enqueue_failure_clears_decision_flag(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.all_btc_instruments[0]["market_timestamp"] = (
            datetime.now(timezone.utc).timestamp() - 800
        )
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._market_stable = True
        strategy._stable_tick_count = 3

        def _raise_executor(_fn):
            raise RuntimeError("executor unavailable")

        strategy.run_in_executor = _raise_executor
        decision_log_path = Path(
            f"/tmp/codex_executor_enqueue_{os.getpid()}_{id(self)}.jsonl"
        )
        decision_log_path.unlink(missing_ok=True)
        original_decision_log_path = os.environ.get("DECISION_LOG_PATH")
        os.environ["DECISION_LOG_PATH"] = str(decision_log_path)

        try:
            with self.assertRaises(self.bot.DecisionExecutorEnqueueError):
                strategy.on_quote_tick(self._quote_tick(strategy))
            record = json.loads(decision_log_path.read_text(encoding="utf-8").strip())
        finally:
            if original_decision_log_path is None:
                os.environ.pop("DECISION_LOG_PATH", None)
            else:
                os.environ["DECISION_LOG_PATH"] = original_decision_log_path
            decision_log_path.unlink(missing_ok=True)

        self.assertFalse(strategy._decision_in_progress)
        self.assertEqual(record["strategy_observation_mode"], "mode_check_pending")
        self.assertEqual(record["rejected_at_gate"], "executor_enqueue_exception")
        self.assertIn("executor unavailable", record["rejection_reason"])

    def test_shadow_executor_enqueue_failure_rolls_back_observed_key(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        market_timestamp = datetime.now(timezone.utc).timestamp() - 400
        strategy.all_btc_instruments[0]["market_timestamp"] = market_timestamp
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._market_stable = True
        strategy._stable_tick_count = 3

        def _raise_executor(_fn):
            raise RuntimeError("shadow executor unavailable")

        decision_log_path = Path(
            f"/tmp/codex_shadow_enqueue_{os.getpid()}_{id(self)}.jsonl"
        )
        decision_log_path.unlink(missing_ok=True)
        original_decision_log_path = os.environ.get("DECISION_LOG_PATH")
        strategy.run_in_executor = _raise_executor
        os.environ["DECISION_LOG_PATH"] = str(decision_log_path)

        try:
            with self.assertRaises(self.bot.DecisionExecutorEnqueueError):
                strategy.on_quote_tick(self._quote_tick(strategy))
            record = json.loads(decision_log_path.read_text(encoding="utf-8").strip())
        finally:
            if original_decision_log_path is None:
                os.environ.pop("DECISION_LOG_PATH", None)
            else:
                os.environ["DECISION_LOG_PATH"] = original_decision_log_path
            decision_log_path.unlink(missing_ok=True)

        self.assertFalse(strategy._shadow_decision_in_progress)
        self.assertNotIn((market_timestamp, 0, "06_09"), strategy._shadow_policy_observed_keys)
        self.assertEqual(record["strategy_observation_mode"], "shadow_policy")
        self.assertEqual(record["rejected_at_gate"], "executor_enqueue_exception")
        self.assertIn("shadow executor unavailable", record["rejection_reason"])

    def test_snapshot_capture_exception_records_and_propagates(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.all_btc_instruments[0]["market_timestamp"] = (
            datetime.now(timezone.utc).timestamp() - 800
        )
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._price_history_sources = ["synthetic_startup"] * len(strategy.price_history)
        strategy._price_history_ts = [None] * len(strategy.price_history)
        strategy._market_stable = True
        strategy._stable_tick_count = 3
        decision_log_path = Path(
            f"/tmp/codex_snapshot_capture_{os.getpid()}_{id(self)}.jsonl"
        )
        decision_log_path.unlink(missing_ok=True)
        original_decision_log_path = os.environ.get("DECISION_LOG_PATH")

        def _raise_capture(_reference_time):
            raise RuntimeError("capture unavailable")

        strategy._capture_locked_decision_state = _raise_capture
        os.environ["DECISION_LOG_PATH"] = str(decision_log_path)
        try:
            with self.assertRaises(self.bot.DecisionSnapshotCaptureError):
                strategy.on_quote_tick(self._quote_tick(strategy))
            record = json.loads(decision_log_path.read_text(encoding="utf-8").strip())
        finally:
            if original_decision_log_path is None:
                os.environ.pop("DECISION_LOG_PATH", None)
            else:
                os.environ["DECISION_LOG_PATH"] = original_decision_log_path
            decision_log_path.unlink(missing_ok=True)

        self.assertEqual(record["rejected_at_gate"], "snapshot_capture_exception")
        self.assertIn("capture unavailable", record["rejection_reason"])

    def test_timer_loop_postpones_market_switch_while_decision_in_progress(self):
        strategy = self._new_strategy()
        strategy.next_switch_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        strategy._decision_in_progress = True
        switch_calls = []
        sleep_calls = []
        original_sleep = self.bot.asyncio.sleep
        strategy._switch_to_next_market = lambda: switch_calls.append("switch")

        async def _stop_after_postpone(seconds):
            sleep_calls.append(seconds)
            raise RuntimeError("stop timer loop")

        self.bot.asyncio.sleep = _stop_after_postpone
        try:
            with self.assertRaisesRegex(RuntimeError, "stop timer loop"):
                asyncio.run(strategy._timer_loop())
        finally:
            self.bot.asyncio.sleep = original_sleep

        self.assertEqual(switch_calls, [])
        self.assertEqual(sleep_calls, [10])

    def test_switch_to_next_market_rechecks_decision_lock_before_mutating(self):
        strategy = self._new_strategy()
        now = datetime.now(timezone.utc)
        strategy.current_instrument_index = 0
        strategy.instrument_id = "yes-current"
        strategy.all_btc_instruments = [
            {
                "slug": "current",
                "instrument": types.SimpleNamespace(id="yes-current"),
                "start_time": now - timedelta(minutes=15),
                "end_time": now - timedelta(seconds=1),
            },
            {
                "slug": "next",
                "instrument": types.SimpleNamespace(id="yes-next"),
                "start_time": now - timedelta(seconds=1),
                "end_time": now + timedelta(minutes=15),
                "yes_token_id": "yes-next-token",
                "yes_instrument_id": "yes-next",
                "no_instrument_id": "no-next",
            },
        ]
        strategy._decision_in_progress = True
        subscriptions = []
        strategy.subscribe_quote_ticks = lambda instrument_id: subscriptions.append(instrument_id)

        switched = strategy._switch_to_next_market()

        self.assertFalse(switched)
        self.assertEqual(strategy.current_instrument_index, 0)
        self.assertEqual(strategy.instrument_id, "yes-current")
        self.assertEqual(subscriptions, [])
