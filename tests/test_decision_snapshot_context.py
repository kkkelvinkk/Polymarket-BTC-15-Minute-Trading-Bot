import asyncio
import sys
import types
from datetime import datetime, timezone
from decimal import Decimal

from decision_snapshot_test_helpers import DecisionSnapshotTestCase


class TestDecisionSnapshotContext(DecisionSnapshotTestCase):
    def _fetch_with_sources(
        self,
        strategy,
        snapshot,
        *,
        orderbook_processor,
        news_source,
        coinbase_source,
        observation_only=False,
    ):
        news_module_name = "data_sources.news_social.adapter"
        coinbase_module_name = "data_sources.coinbase.adapter"
        original_news_module = sys.modules.get(news_module_name)
        original_coinbase_module = sys.modules.get(coinbase_module_name)
        news_module = types.ModuleType(news_module_name)
        news_module.NewsSocialDataSource = news_source
        coinbase_module = types.ModuleType(coinbase_module_name)
        coinbase_module.CoinbaseDataSource = coinbase_source
        if observation_only:
            strategy._shadow_orderbook_processor = orderbook_processor
        else:
            strategy.orderbook_processor = orderbook_processor

        try:
            sys.modules[news_module_name] = news_module
            sys.modules[coinbase_module_name] = coinbase_module
            return asyncio.run(
                strategy._fetch_market_context(
                    snapshot,
                    observation_only=observation_only,
                )
            )
        finally:
            if original_news_module is None:
                sys.modules.pop(news_module_name, None)
            else:
                sys.modules[news_module_name] = original_news_module
            if original_coinbase_module is None:
                sys.modules.pop(coinbase_module_name, None)
            else:
                sys.modules[coinbase_module_name] = original_coinbase_module

    def test_context_uses_frozen_snapshot_after_history_and_market_mutate(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._tick_buffer.append({"ts": datetime.now(timezone.utc), "price": Decimal("0.50")})

        class _OrderBookProcessor:
            def fetch_order_book(self, token_id):
                return {"token_id": token_id, "bids": [], "asks": []}

        class _NewsSocialDataSource:
            async def connect(self):
                return None

            async def get_fear_greed_index(self):
                return {"value": "50", "classification": "Neutral"}

            async def disconnect(self):
                return None

        class _CoinbaseDataSource:
            async def connect(self):
                return None

            async def get_current_price(self):
                return Decimal("100000")

            async def disconnect(self):
                return None

        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.40"),
            "decision-frozen-context",
        )
        strategy.price_history = [Decimal("0.90")] * 20
        strategy._tick_buffer.append({"ts": datetime.now(timezone.utc), "price": Decimal("0.90")})
        self._set_market(
            strategy,
            condition_id="condition-mutated",
            yes_token_id="mutated-yes",
            no_token_id="mutated-no",
        )

        metadata = self._fetch_with_sources(
            strategy,
            snapshot,
            orderbook_processor=_OrderBookProcessor(),
            news_source=_NewsSocialDataSource,
            coinbase_source=_CoinbaseDataSource,
        )

        self.assertAlmostEqual(metadata["context_sma20_deviation"], -0.2)
        self.assertEqual(metadata["yes_token_id"], "yes-token")
        self.assertEqual(metadata["yes_order_book"]["token_id"], "yes-token")
        self.assertEqual(metadata["no_order_book"]["token_id"], "no-token")
        self.assertEqual(metadata["decision_tick_buffer_len"], 1)
        self.assertEqual(metadata["decision_reference_time"], snapshot.reference_time)
        self.assertEqual(len(metadata["tick_buffer"]), 1)

    def test_context_fails_closed_when_order_book_missing(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._tick_buffer.append({"ts": datetime.now(timezone.utc), "price": Decimal("0.50")})

        class _OrderBookProcessor:
            def fetch_order_book(self, _token_id):
                return None

        class _NewsSocialDataSource:
            async def connect(self):
                return None

            async def get_fear_greed_index(self):
                return {"value": "50", "classification": "Neutral"}

            async def disconnect(self):
                return None

        class _CoinbaseDataSource:
            async def connect(self):
                return None

            async def get_current_price(self):
                return Decimal("100000")

            async def disconnect(self):
                return None

        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.40"),
            "decision-missing-book",
        )
        with self.assertRaisesRegex(RuntimeError, "YES order book"):
            self._fetch_with_sources(
                strategy,
                snapshot,
                orderbook_processor=_OrderBookProcessor(),
                news_source=_NewsSocialDataSource,
                coinbase_source=_CoinbaseDataSource,
            )

    def test_context_fails_closed_when_fear_greed_classification_missing(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._tick_buffer.append({"ts": datetime.now(timezone.utc), "price": Decimal("0.50")})

        class _OrderBookProcessor:
            def fetch_order_book(self, token_id):
                return {"token_id": token_id, "bids": [], "asks": []}

        class _NewsSocialDataSource:
            async def connect(self):
                return None

            async def get_fear_greed_index(self):
                return {"value": "50"}

            async def disconnect(self):
                return None

        class _CoinbaseDataSource:
            async def connect(self):
                return None

            async def get_current_price(self):
                return Decimal("100000")

            async def disconnect(self):
                return None

        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.40"),
            "decision-missing-classification",
        )
        with self.assertRaisesRegex(RuntimeError, "classification"):
            self._fetch_with_sources(
                strategy,
                snapshot,
                orderbook_processor=_OrderBookProcessor(),
                news_source=_NewsSocialDataSource,
                coinbase_source=_CoinbaseDataSource,
            )

    def test_shadow_context_uses_shadow_orderbook_processor(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._tick_buffer.append({"ts": datetime.now(timezone.utc), "price": Decimal("0.50")})
        fetched = []

        class _LiveOrderBookProcessor:
            def fetch_order_book(self, _token_id):
                raise AssertionError("live orderbook processor should not run for shadow context")

        class _ShadowOrderBookProcessor:
            def fetch_order_book(self, token_id):
                fetched.append(token_id)
                return {"token_id": token_id, "bids": [], "asks": []}

        class _NewsSocialDataSource:
            async def connect(self):
                return None

            async def get_fear_greed_index(self):
                return {"value": "50", "classification": "Neutral"}

            async def disconnect(self):
                return None

        class _CoinbaseDataSource:
            async def connect(self):
                return None

            async def get_current_price(self):
                return Decimal("100000")

            async def disconnect(self):
                return None

        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.40"),
            "decision-shadow-context",
        )
        strategy.orderbook_processor = _LiveOrderBookProcessor()

        metadata = self._fetch_with_sources(
            strategy,
            snapshot,
            orderbook_processor=_ShadowOrderBookProcessor(),
            news_source=_NewsSocialDataSource,
            coinbase_source=_CoinbaseDataSource,
            observation_only=True,
        )

        self.assertEqual(fetched, ["yes-token", "no-token"])
        self.assertEqual(metadata["yes_order_book"]["token_id"], "yes-token")
        self.assertEqual(metadata["no_order_book"]["token_id"], "no-token")

    def test_process_signals_uses_frozen_history_and_tick_buffer_after_mutation(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        first_tick_ts = datetime.now(timezone.utc)
        strategy.price_history = [Decimal("0.50")] * 20
        strategy._tick_buffer.append({"ts": first_tick_ts, "price": Decimal("0.50")})
        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.40"),
            "decision-frozen-signals",
        )
        strategy.price_history = [Decimal("0.90")] * 20
        strategy._tick_buffer.append({"ts": datetime.now(timezone.utc), "price": Decimal("0.90")})
        captured = {}

        class _CaptureHistoryProcessor:
            def process(self, current_price, historical_prices, metadata):
                captured["history"] = (current_price, tuple(historical_prices))
                return None

        class _CaptureTickProcessor:
            def process(self, current_price, historical_prices, metadata):
                captured["tick_buffer"] = tuple(metadata["tick_buffer"])
                return None

        class _NullProcessor:
            def process(self, current_price, historical_prices, metadata):
                return None

        strategy.spike_detector = _CaptureHistoryProcessor()
        strategy.tick_velocity_processor = _CaptureTickProcessor()
        strategy.deribit_pcr_processor = _NullProcessor()
        metadata = {"tick_buffer": [tick.as_processor_dict() for tick in snapshot.tick_buffer]}

        signals = strategy._process_signals(snapshot, metadata)

        self.assertEqual(signals, [])
        self.assertEqual(captured["history"], (Decimal("0.40"), tuple([Decimal("0.50")] * 20)))
        self.assertEqual(
            captured["tick_buffer"],
            ({"ts": first_tick_ts, "price": Decimal("0.50")},),
        )

    def test_shadow_observation_uses_isolated_processors_and_fusion(self):
        strategy = self._new_strategy()
        self._set_market(strategy)
        strategy.price_history = [Decimal("0.50")] * 20
        snapshot = strategy._capture_decision_input_snapshot(
            Decimal("0.40"),
            "decision-shadow-isolated",
        )
        calls = []

        class _RaisingProcessor:
            def process(self, current_price, historical_prices, metadata):
                raise AssertionError("live processor should not run for shadow observation")

        class _RecordingProcessor:
            def __init__(self, name):
                self.name = name

            def process(self, current_price, historical_prices, metadata):
                calls.append(self.name)
                return None

        strategy.spike_detector = _RaisingProcessor()
        strategy.divergence_processor = _RaisingProcessor()
        strategy.deribit_pcr_processor = _RaisingProcessor()
        strategy._shadow_spike_detector = _RecordingProcessor("shadow_spike")
        strategy._shadow_divergence_processor = _RecordingProcessor("shadow_divergence")
        strategy._shadow_deribit_pcr_processor = _RecordingProcessor("shadow_pcr")

        signals = strategy._process_signals(
            snapshot,
            {"spot_price": Decimal("100000")},
            observation_only=True,
        )

        self.assertEqual(signals, [])
        self.assertEqual(calls, ["shadow_spike", "shadow_divergence", "shadow_pcr"])
        self.assertIs(strategy._fusion_engine_for_decision(True), strategy._shadow_fusion_engine)
        self.assertIs(strategy._fusion_engine_for_decision(False), strategy.fusion_engine)
