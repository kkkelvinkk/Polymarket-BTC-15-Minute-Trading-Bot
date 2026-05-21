import asyncio
import types
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from decision_snapshot_test_helpers import DecisionSnapshotTestCase


class TestDecisionSnapshotIntentFreshness(DecisionSnapshotTestCase):
    def test_live_decision_preserves_final_stale_rejection_gate(self):
        strategy = self._new_strategy(simulation_mode=False)
        self._set_market(
            strategy,
            condition_id="conditionbodyboundary",
            yes_token_id="yestoken",
            no_token_id="notoken",
        )
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.70")] * 20
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))
        base = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        snapshot = replace(
            strategy._capture_decision_input_snapshot(
                Decimal("0.70"),
                "decision-final-stale-structured",
            ),
            captured_at=base,
            reference_time=base,
        )
        rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
        now_values = [
            base + timedelta(seconds=1),
            base + timedelta(seconds=2),
            base + timedelta(seconds=3),
            base + timedelta(seconds=4),
            base + timedelta(seconds=11),
        ]
        original_datetime = self.bot.datetime
        original_parse = strategy._parse_utc_datetime
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

        class _OrderFactory:
            def market(self, **kwargs):
                return types.SimpleNamespace(order_kwargs=kwargs)

        async def _fetch_market_context(_snapshot, *, observation_only=False):
            return {
                "context_sma20_deviation": 0.0,
                "momentum": 0.0,
                "volatility": 0.0,
                "tick_buffer": [],
                "yes_token_id": "yestoken",
                "yes_order_book": {"bids": [], "asks": [{"price": "0.62", "size": "20"}]},
            }

        async def _depth_entry(**_kwargs):
            return self.bot.DepthAwareEntry(
                executable_entry=Decimal("0.62"),
                tokens_filled=Decimal("8.887096"),
                actual_cost=Decimal("5.51"),
                fully_filled=True,
            )

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
        strategy._fetch_market_context = _fetch_market_context
        strategy._process_signals = (
            lambda _snapshot, _metadata, *, observation_only=False: [fused]
        )
        strategy.fusion_engine = types.SimpleNamespace(
            fuse_signals=lambda _signals, min_signals, min_score: fused
        )
        strategy._resolve_position_size_usd = lambda is_simulation, rec: Decimal("5.51")
        strategy._compute_depth_aware_entry_details = _depth_entry
        strategy.risk_engine.validate_new_position = lambda **_kwargs: (True, None)
        strategy.risk_engine.add_position = (
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("risk position should not be added for stale snapshot")
            )
        )
        strategy._live_balance_block_reason_for_order = (
            lambda _config, _position_size: None
        )
        strategy._parse_utc_datetime = lambda _value: base + timedelta(minutes=1)
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
            strategy._parse_utc_datetime = original_parse
            self.bot.datetime = original_datetime

        self.assertFalse(result)
        self.assertEqual(
            rec.fields["rejected_at_gate"],
            "decision_snapshot_stale_before_intent_persistence",
        )
        self.assertIn("MAX_DECISION_SNAPSHOT_AGE_SECONDS=10", rec.fields["rejection_reason"])
        self.assertIsNone(rec.fields["decided_direction"])
        self.assertEqual(rec.fields["decision_snapshot_age_seconds"], Decimal("11"))
