import asyncio
import types
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from decision_snapshot_test_helpers import DecisionSnapshotTestCase


class _Record:
    def __init__(self):
        self.fields = {
            "rejected_at_gate": None,
            "rejection_reason": None,
            "decided_direction": None,
        }

    def update(self, **kwargs):
        self.fields.update(kwargs)

    def reject(self, gate, reason):
        self.fields["rejected_at_gate"] = gate
        self.fields["rejection_reason"] = reason
        self.fields["decided_direction"] = None

    def decided(self, direction, **extra):
        self.fields["decided_direction"] = direction
        self.fields["rejected_at_gate"] = None
        self.fields["rejection_reason"] = None
        self.fields.update(extra)


class TestDecisionSnapshotLiveOrder(DecisionSnapshotTestCase):
    def test_live_decision_rejects_stale_snapshot_before_context_fetch(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(strategy, condition_id="condition-stale-snapshot")
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.62")] * 20
        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.62"),
            "decision-stale-before-context",
        )
        stale_snapshot = replace(
            snapshot,
            captured_at=datetime.now(timezone.utc) - timedelta(seconds=31),
            reference_time=datetime.now(timezone.utc) - timedelta(seconds=31),
        )

        async def _fetch_market_context(_snapshot, *, observation_only=False):
            raise AssertionError("stale snapshot should reject before context fetch")

        rec = _Record()
        strategy._fetch_market_context = _fetch_market_context

        result = asyncio.run(
            strategy._make_trading_decision_body(
                stale_snapshot,
                trade_key=None,
                is_simulation=False,
                rec=rec,
            )
        )

        self.assertFalse(result)
        self.assertEqual(
            rec.fields["rejected_at_gate"],
            "decision_snapshot_stale_before_context",
        )
        self.assertIn(
            "MAX_DECISION_SNAPSHOT_AGE_SECONDS=10",
            rec.fields["rejection_reason"],
        )

    def test_live_decision_rejects_stale_snapshot_after_context_fetch(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(strategy, condition_id="condition-stale-after-context")
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.62")] * 20
        base = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        snapshot = replace(
            strategy._capture_decision_input_snapshot(
                Decimal("0.62"),
                "decision-stale-after-context",
            ),
            captured_at=base,
            reference_time=base,
        )
        rec = _Record()
        now_values = [
            base + timedelta(seconds=1),
            base + timedelta(seconds=11),
        ]
        original_datetime = self.bot.datetime

        class _FakeDateTime:
            @classmethod
            def now(cls, tz=None):
                return now_values.pop(0)

        async def _fetch_market_context(_snapshot, *, observation_only=False):
            return {
                "context_sma20_deviation": 0.0,
                "momentum": 0.0,
                "volatility": 0.0,
            }

        def _unexpected_process(*_args, **_kwargs):
            raise AssertionError("signals should not run after stale context")

        strategy._fetch_market_context = _fetch_market_context
        strategy._process_signals = _unexpected_process
        self.bot.datetime = _FakeDateTime
        try:
            result = asyncio.run(
                strategy._make_trading_decision_body(
                    snapshot,
                    trade_key=None,
                    is_simulation=False,
                    rec=rec,
                )
            )
        finally:
            self.bot.datetime = original_datetime

        self.assertFalse(result)
        self.assertEqual(
            rec.fields["rejected_at_gate"],
            "decision_snapshot_stale_before_signals",
        )

    def test_live_decision_rejects_stale_snapshot_before_execution_side_effects(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(strategy, condition_id="condition-stale-before-execution")
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.70")] * 20
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))
        base = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        snapshot = replace(
            strategy._capture_decision_input_snapshot(
                Decimal("0.70"),
                "decision-stale-before-execution",
            ),
            captured_at=base,
            reference_time=base,
        )
        rec = _Record()
        now_values = [
            base + timedelta(seconds=1),
            base + timedelta(seconds=2),
            base + timedelta(seconds=11),
        ]
        original_datetime = self.bot.datetime
        fused = types.SimpleNamespace(
            source="Fusion",
            direction=types.SimpleNamespace(value="bullish"),
            score=80,
            confidence=0.80,
            metadata={},
        )

        class _FakeDateTime:
            @classmethod
            def now(cls, tz=None):
                return now_values.pop(0)

        async def _fetch_market_context(_snapshot, *, observation_only=False):
            return {
                "context_sma20_deviation": 0.0,
                "momentum": 0.0,
                "volatility": 0.0,
                "tick_buffer": [],
                "yes_token_id": "yes-token",
                "yes_order_book": {"bids": [], "asks": [{"price": "0.62", "size": "20"}]},
            }

        async def _depth_entry(**_kwargs):
            return self.bot.DepthAwareEntry(
                executable_entry=Decimal("0.62"),
                tokens_filled=Decimal("8.887096"),
                actual_cost=Decimal("5.51"),
                fully_filled=True,
            )

        def _unexpected_risk_validation(**_kwargs):
            raise AssertionError("risk validation should not run after stale execution gate")

        async def _unexpected_place(*_args, **_kwargs):
            raise AssertionError("live order should not run after stale execution gate")

        strategy._fetch_market_context = _fetch_market_context
        strategy._process_signals = (
            lambda _snapshot, _metadata, *, observation_only=False: [fused]
        )
        strategy.fusion_engine = types.SimpleNamespace(
            fuse_signals=lambda _signals, min_signals, min_score: fused
        )
        strategy._resolve_position_size_usd = lambda is_simulation, rec: Decimal("5.51")
        strategy._compute_depth_aware_entry_details = _depth_entry
        strategy.risk_engine.validate_new_position = _unexpected_risk_validation
        strategy._place_real_order = _unexpected_place
        self.bot.datetime = _FakeDateTime
        try:
            result = asyncio.run(
                strategy._make_trading_decision_body(
                    snapshot,
                    trade_key=None,
                    is_simulation=False,
                    rec=rec,
                )
            )
        finally:
            self.bot.datetime = original_datetime

        self.assertFalse(result)
        self.assertEqual(
            rec.fields["rejected_at_gate"],
            "decision_snapshot_stale_before_execution",
        )

    def test_live_order_rechecks_snapshot_age_before_intent_persistence(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(
            strategy,
            condition_id="conditionboundary",
            yes_token_id="yestoken",
            no_token_id="notoken",
        )
        market_meta = strategy._capture_decision_input_snapshot(
            Decimal("0.62"),
            "decision-stale-order-boundary",
        ).market_metadata()
        base = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        now_values = [base, base + timedelta(seconds=11)]
        original_datetime = self.bot.datetime
        original_parse = strategy._parse_utc_datetime

        class _FakeDateTime:
            @classmethod
            def now(cls, tz=None):
                return now_values.pop(0)

        class _OrderFactory:
            def market(self, **kwargs):
                return types.SimpleNamespace(order_kwargs=kwargs)

        strategy.cache = types.SimpleNamespace(
            instrument=lambda _instrument_id: types.SimpleNamespace(
                size_precision=6,
                price_precision=2,
                info={},
            )
        )
        strategy.order_factory = _OrderFactory()
        strategy.submit_order = (
            lambda _order: (_ for _ in ()).throw(
                AssertionError("submit_order should not run for stale snapshot")
            )
        )
        strategy._persist_submitted_order_intent_locked = (
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("intent should not persist for stale snapshot")
            )
        )
        strategy.risk_engine.add_position = (
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("risk position should not be added for stale snapshot")
            )
        )
        strategy._live_balance_block_reason_for_order = (
            lambda _config, _position_size: None
        )
        rec = _Record()
        strategy._parse_utc_datetime = lambda _value: base + timedelta(minutes=1)
        self.bot.datetime = _FakeDateTime
        try:
            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("5.51"),
                    current_price=Decimal("0.62"),
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                    market_meta=market_meta,
                    quoted_price=Decimal("0.62"),
                    price_source="YES ask",
                    decision_stable_tick_count=3,
                    decision_reference_time=base,
                    decision_record=rec,
                )
            )
        finally:
            strategy._parse_utc_datetime = original_parse
            self.bot.datetime = original_datetime

        self.assertFalse(result)
        self.assertEqual(
            rec.fields["rejected_at_gate"],
            "decision_snapshot_stale_before_intent_persistence",
        )
        self.assertEqual(rec.fields["decision_snapshot_age_seconds"], Decimal("11"))
        self.assertEqual(rec.fields["max_decision_snapshot_age_seconds"], Decimal("10"))
        self.assertEqual(strategy._submitted_order_intents, {})
        self.assertEqual(strategy._submitted_positions, {})

    def test_live_order_rejects_when_active_market_no_longer_matches_snapshot(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(strategy, condition_id="condition-active")
        market_meta = strategy._capture_decision_input_snapshot(
            Decimal("0.62"),
            "decision-active-market",
        ).market_metadata()
        self._set_market(strategy, condition_id="condition-mutated")
        strategy.cache = types.SimpleNamespace(
            instrument=lambda _instrument_id: types.SimpleNamespace(
                size_precision=6,
                price_precision=2,
                info={},
            )
        )

        result = asyncio.run(
            strategy._place_real_order(
                signal=types.SimpleNamespace(score=77, confidence=0.67),
                position_size=Decimal("5.51"),
                current_price=Decimal("0.62"),
                direction="long",
                order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                market_meta=market_meta,
                quoted_price=Decimal("0.62"),
                price_source="YES ask",
                decision_stable_tick_count=3,
                decision_reference_time=datetime.now(timezone.utc),
                decision_record=_Record(),
            )
        )

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_live_order_rechecks_snapshot_expiry_before_intent_persistence(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(strategy, condition_id="condition-final-expiry")
        market_meta = strategy._capture_decision_input_snapshot(
            Decimal("0.62"),
            "decision-final-expiry",
        ).market_metadata()
        strategy.cache = types.SimpleNamespace(
            instrument=lambda _instrument_id: types.SimpleNamespace(
                size_precision=6,
                price_precision=2,
                info={},
            )
        )
        market_end_time = datetime(2030, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        now_values = [
            datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2030, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        ]
        original_parse = strategy._parse_utc_datetime
        original_datetime = self.bot.datetime

        class _FakeDateTime:
            @classmethod
            def now(cls, tz=None):
                return now_values.pop(0)

        strategy._parse_utc_datetime = lambda _value: market_end_time
        self.bot.datetime = _FakeDateTime
        try:
            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("5.51"),
                    current_price=Decimal("0.62"),
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                    market_meta=market_meta,
                    quoted_price=Decimal("0.62"),
                    price_source="YES ask",
                    decision_stable_tick_count=3,
                    decision_reference_time=now_values[0],
                    decision_record=_Record(),
                )
            )
        finally:
            strategy._parse_utc_datetime = original_parse
            self.bot.datetime = original_datetime

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_live_order_rejects_when_snapshot_market_expired_before_submit(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(strategy, condition_id="condition-expired")
        market_meta = strategy._capture_decision_input_snapshot(
            Decimal("0.62"),
            "decision-expired-market",
        ).market_metadata()
        market_meta["end_time"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        strategy.cache = types.SimpleNamespace(
            instrument=lambda _instrument_id: types.SimpleNamespace(
                size_precision=6,
                price_precision=2,
                info={},
            )
        )

        result = asyncio.run(
            strategy._place_real_order(
                signal=types.SimpleNamespace(score=77, confidence=0.67),
                position_size=Decimal("5.51"),
                current_price=Decimal("0.62"),
                direction="long",
                order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                market_meta=market_meta,
                quoted_price=Decimal("0.62"),
                price_source="YES ask",
                decision_stable_tick_count=3,
                decision_reference_time=datetime.now(timezone.utc),
                decision_record=_Record(),
            )
        )

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})
