import asyncio
import importlib
import os
import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class _DummyLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _DummyConfig:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _DummyStrategy:
    def __init__(self, *args, **kwargs):
        pass


class _DummyProcessor:
    def __init__(self, *args, **kwargs):
        pass


class _DummyFusion:
    def __init__(self):
        self.weights = {}

    def set_weight(self, name, value):
        self.weights[name] = value


class _DummyRiskEngine:
    def __init__(self):
        self._positions = {}
        self.realized_pnl = []
        self.restored_daily_stats = None

    def add_position(self, position_id, size, entry_price, direction, **_kwargs):
        self._positions[str(position_id)] = {
            "size": Decimal(str(size)),
            "entry_price": Decimal(str(entry_price)),
            "direction": direction,
        }

    def adjust_position(self, position_id, size, entry_price, direction=None):
        position_id = str(position_id)
        if position_id not in self._positions:
            self.add_position(position_id, size, entry_price, direction or "buy")
            return
        self._positions[position_id]["size"] = Decimal(str(size))
        self._positions[position_id]["entry_price"] = Decimal(str(entry_price))
        if direction:
            self._positions[position_id]["direction"] = direction

    def release_position(self, position_id):
        return self._positions.pop(str(position_id), None) is not None

    def remove_position(self, position_id, exit_price):
        position = self._positions.pop(str(position_id), None)
        if not position:
            return None
        entry_price = position["entry_price"]
        pnl = position["size"] * ((Decimal(str(exit_price)) - entry_price) / entry_price)
        self.realized_pnl.append(pnl)
        return pnl

    def record_realized_pnl(self, pnl, **_kwargs):
        self.realized_pnl.append(Decimal(str(pnl)))

    def restore_daily_stats(self, daily_pnl, daily_trades):
        self.restored_daily_stats = (Decimal(str(daily_pnl)), daily_trades)

    def validate_new_position(self, **_kwargs):
        return True, None


class _DummyPerformanceTracker:
    def __init__(self):
        self.trades = []

    def record_trade(self, **kwargs):
        self.trades.append(kwargs)


class _DummyRedis:
    def __init__(self, *args, **kwargs):
        pass

    def ping(self):
        return True


def _ensure_module(name):
    if name in sys.modules:
        return sys.modules[name]

    module = types.ModuleType(name)
    sys.modules[name] = module

    if "." in name:
        parent_name, child_name = name.rsplit(".", 1)
        parent = _ensure_module(parent_name)
        setattr(parent, child_name, module)

    return module


def _install_module(name, **attrs):
    module = _ensure_module(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _install_bot_dependency_stubs():
    _install_module(
        "patch_gamma_markets",
        apply_gamma_markets_patch=lambda: True,
        verify_patch=lambda: None,
    )
    _install_module(
        "patch_market_orders",
        apply_market_order_patch=lambda: True,
        register_auto_redeem_handler=lambda _handler: None,
        unregister_auto_redeem_handler=lambda _handler: None,
    )
    _install_module("polymarket_v2_compat", apply_polymarket_v2_patch=lambda: True)
    _install_module("dotenv", load_dotenv=lambda: None)
    _install_module("loguru", logger=_DummyLogger())
    _install_module("redis", Redis=_DummyRedis)

    _install_module(
        "nautilus_trader.config",
        InstrumentProviderConfig=_DummyConfig,
        LiveDataEngineConfig=_DummyConfig,
        LiveExecEngineConfig=_DummyConfig,
        LiveRiskEngineConfig=_DummyConfig,
        LoggingConfig=_DummyConfig,
        TradingNodeConfig=_DummyConfig,
    )
    _install_module("nautilus_trader.live.node", TradingNode=_DummyConfig)
    _install_module(
        "nautilus_trader.adapters.polymarket",
        POLYMARKET="POLYMARKET",
        PolymarketDataClientConfig=_DummyConfig,
        PolymarketExecClientConfig=_DummyConfig,
    )
    _install_module(
        "nautilus_trader.adapters.polymarket.factories",
        PolymarketLiveDataClientFactory=_DummyConfig,
        PolymarketLiveExecClientFactory=_DummyConfig,
    )
    _install_module("nautilus_trader.trading.strategy", Strategy=_DummyStrategy)
    _install_module(
        "nautilus_trader.model.identifiers",
        InstrumentId=_DummyConfig,
        ClientOrderId=_DummyConfig,
    )
    _install_module(
        "nautilus_trader.model.enums",
        OrderSide=_DummyConfig,
        TimeInForce=_DummyConfig,
    )
    _install_module("nautilus_trader.model.objects", Quantity=_DummyConfig)
    _install_module("nautilus_trader.model.data", QuoteTick=_DummyConfig)

    _install_module(
        "core.strategy_brain.signal_processors.spike_detector",
        SpikeDetectionProcessor=_DummyProcessor,
    )
    _install_module(
        "core.strategy_brain.signal_processors.sentiment_processor",
        SentimentProcessor=_DummyProcessor,
    )
    _install_module(
        "core.strategy_brain.signal_processors.divergence_processor",
        PriceDivergenceProcessor=_DummyProcessor,
    )
    _install_module(
        "core.strategy_brain.signal_processors.orderbook_processor",
        OrderBookImbalanceProcessor=_DummyProcessor,
    )
    _install_module(
        "core.strategy_brain.signal_processors.tick_velocity_processor",
        TickVelocityProcessor=_DummyProcessor,
    )
    _install_module(
        "core.strategy_brain.signal_processors.deribit_pcr_processor",
        DeribitPCRProcessor=_DummyProcessor,
    )
    _install_module(
        "core.strategy_brain.fusion_engine.signal_fusion",
        get_fusion_engine=_DummyFusion,
    )
    _install_module("execution.risk_engine", get_risk_engine=lambda: _DummyRiskEngine())
    _install_module("monitoring.performance_tracker", get_performance_tracker=lambda: _DummyPerformanceTracker())
    _install_module("monitoring.grafana_exporter", get_grafana_exporter=lambda: object())
    _install_module("feedback.learning_engine", get_learning_engine=lambda: object())


class SimulationModeSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_bot_dependency_stubs()
        sys.path.insert(0, str(REPO_ROOT))
        sys.modules.pop("bot", None)
        cls.bot = importlib.import_module("bot")

    def setUp(self):
        self._original_ledger_path = self.bot.LIVE_TRADE_LEDGER_PATH
        self._original_require_token_hint = os.environ.get("REQUIRE_AUTO_REDEEM_TOKEN_HINT")
        self._strategies = []
        os.environ["REQUIRE_AUTO_REDEEM_TOKEN_HINT"] = "true"
        self._test_ledger_path = Path(f"/tmp/codex_live_trades_test_{os.getpid()}_{id(self)}.json")
        for path in (
            self._test_ledger_path,
            Path(str(self._test_ledger_path) + ".tmp"),
            Path(str(self._test_ledger_path) + ".bak"),
            Path(str(self._test_ledger_path) + ".lock"),
        ):
            path.unlink(missing_ok=True)
        self.bot.LIVE_TRADE_LEDGER_PATH = self._test_ledger_path

    def tearDown(self):
        for strategy in self._strategies:
            strategy._release_live_trade_ledger_lock()
        self._strategies = []
        self.bot.LIVE_TRADE_LEDGER_PATH = self._original_ledger_path
        if self._original_require_token_hint is None:
            os.environ.pop("REQUIRE_AUTO_REDEEM_TOKEN_HINT", None)
        else:
            os.environ["REQUIRE_AUTO_REDEEM_TOKEN_HINT"] = self._original_require_token_hint
        for path in (
            self._test_ledger_path,
            Path(str(self._test_ledger_path) + ".tmp"),
            Path(str(self._test_ledger_path) + ".bak"),
            Path(str(self._test_ledger_path) + ".lock"),
        ):
            path.unlink(missing_ok=True)

    def _run_bot_with_fake_node(self, simulation, redis_client):
        captured = {}
        original_init_redis = self.bot.init_redis
        original_trading_node = self.bot.TradingNode
        required_env = {
            "POLYMARKET_PK": "0x" + "1" * 64,
            "POLYMARKET_FUNDER": "0x" + "2" * 40,
            "POLYMARKET_SIGNATURE_TYPE": "0",
            "POLYMARKET_API_KEY": "test-api-key",
            "POLYMARKET_API_SECRET": "test-api-secret",
            "POLYMARKET_PASSPHRASE": "test-passphrase",
        }
        original_env = {key: os.environ.get(key) for key in required_env}

        class _RecordingTrader:
            def add_strategy(self, strategy):
                captured["strategy"] = strategy

        class _RecordingNode:
            def __init__(self, config):
                captured["config"] = config
                self.trader = _RecordingTrader()

            def add_data_client_factory(self, *args, **kwargs):
                pass

            def add_exec_client_factory(self, *args, **kwargs):
                pass

            def build(self):
                pass

            def run(self):
                raise KeyboardInterrupt

            def dispose(self):
                captured["disposed"] = True

        try:
            os.environ.update(required_env)
            self.bot.init_redis = lambda: redis_client
            self.bot.TradingNode = _RecordingNode
            self.bot.run_integrated_bot(
                simulation=simulation,
                enable_grafana=False,
                test_mode=False,
            )
        finally:
            self.bot.init_redis = original_init_redis
            self.bot.TradingNode = original_trading_node
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        if "strategy" in captured:
            self._track_strategy(captured["strategy"])
        return captured

    def _track_strategy(self, strategy):
        self._strategies.append(strategy)
        return strategy

    def test_strategy_defaults_to_simulation_without_redis(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
            )
        )

        self.assertTrue(strategy.current_simulation_mode)
        self.assertTrue(asyncio.run(strategy.check_simulation_mode()))
        self.assertIsNone(strategy._last_trade_wait_log_key)

    def test_boolean_env_rejects_invalid_values(self):
        os.environ["REQUIRE_AUTO_REDEEM_TOKEN_HINT"] = "treu"

        with self.assertRaisesRegex(RuntimeError, "REQUIRE_AUTO_REDEEM_TOKEN_HINT"):
            self.bot.get_env_bool("REQUIRE_AUTO_REDEEM_TOKEN_HINT", True)

    def test_boolean_env_accepts_explicit_false_values(self):
        os.environ["REQUIRE_AUTO_REDEEM_TOKEN_HINT"] = "off"

        self.assertFalse(self.bot.get_env_bool("REQUIRE_AUTO_REDEEM_TOKEN_HINT", True))

    def test_live_and_test_mode_flags_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as raised:
            self.bot.parse_runtime_args(["--live", "--test-mode"])

        self.assertEqual(raised.exception.code, 2)

    def test_live_redis_read_error_aborts_mode_check(self):
        class _BrokenRedis:
            def get(self, key):
                raise RuntimeError("redis unavailable")

        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=_BrokenRedis(),
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(strategy.check_simulation_mode())

    def test_invalid_redis_mode_value_aborts_mode_check(self):
        class _MalformedRedis:
            def get(self, key):
                return "maybe-live"

        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=_MalformedRedis(),
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(strategy.check_simulation_mode())

    def test_missing_redis_mode_value_aborts_mode_check(self):
        class _MissingModeRedis:
            def get(self, key):
                return None

        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=_MissingModeRedis(),
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(strategy.check_simulation_mode())

    def test_live_mode_requires_available_seeded_redis_control(self):
        with self.assertRaises(RuntimeError):
            self._run_bot_with_fake_node(simulation=False, redis_client=None)

    def test_live_mode_requires_market_order_patch(self):
        class _SeededRedis:
            def set(self, _key, _value):
                pass

            def get(self, _key):
                return "0"

        original_patch_applied = self.bot.patch_applied
        self.bot.patch_applied = False
        try:
            with self.assertRaises(RuntimeError):
                self._run_bot_with_fake_node(simulation=False, redis_client=_SeededRedis())
        finally:
            self.bot.patch_applied = original_patch_applied

    def test_live_mode_requires_v2_patch(self):
        class _SeededRedis:
            def set(self, _key, _value):
                pass

            def get(self, _key):
                return "0"

        original_v2_patch_applied = self.bot.v2_patch_applied
        self.bot.v2_patch_applied = False
        try:
            with self.assertRaises(RuntimeError):
                self._run_bot_with_fake_node(simulation=False, redis_client=_SeededRedis())
        finally:
            self.bot.v2_patch_applied = original_v2_patch_applied

    def test_explicit_live_mode_still_works_when_redis_is_seeded(self):
        class _SeededRedis:
            def __init__(self):
                self.values = {}

            def set(self, key, value):
                self.values[key] = value

            def get(self, key):
                return self.values.get(key)

        redis_client = _SeededRedis()
        captured = self._run_bot_with_fake_node(
            simulation=False,
            redis_client=redis_client,
        )

        self.assertEqual(redis_client.values["btc_trading:simulation_mode"], "0")
        self.assertFalse(captured["strategy"].current_simulation_mode)

    def test_failed_live_redis_seed_aborts_startup(self):
        class _FailingRedis:
            def set(self, key, value):
                raise RuntimeError("write failed")

            def get(self, key):
                return "0"

        with self.assertRaises(RuntimeError):
            self._run_bot_with_fake_node(
                simulation=False,
                redis_client=_FailingRedis(),
            )

    def _new_strategy(self):
        return self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )

    def _live_trade_meta(self, order_id="order-1", token_id="token-yes", slug="slug-a", condition_id="cond-a"):
        now = datetime.now(timezone.utc)
        return {
            "order_id": order_id,
            "entry_price": Decimal("0.50"),
            "size": Decimal("2.00"),
            "direction": "long",
            "trade_label": "YES (UP)",
            "estimated_tokens": Decimal("4"),
            "filled_qty": Decimal("4"),
            "filled_notional": Decimal("2.00"),
            "instrument_id": f"{condition_id}-{token_id}.POLYMARKET",
            "token_id": token_id,
            "slug": slug,
            "condition_id": condition_id,
            "market_end_time": (now - timedelta(hours=2)).isoformat(),
            "filled_at": (now - timedelta(hours=2)).isoformat(),
            "submitted_at": (now - timedelta(hours=2)).isoformat(),
            "signal_score": 75,
            "signal_confidence": 0.82,
        }

    def test_partial_fill_accumulates_actual_notional(self):
        strategy = self._new_strategy()
        order_id = "partial-order"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-partial",
        )
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("5.50"), Decimal("0.55"), "buy_yes")

        strategy._record_live_order_fill(order_id, Decimal("0.50"), Decimal("4"))
        strategy._record_live_order_fill(order_id, Decimal("0.60"), Decimal("5"))

        meta = strategy._open_live_trades[order_id]
        self.assertEqual(meta["filled_qty"], Decimal("9"))
        self.assertEqual(meta["filled_notional"], Decimal("5.00"))
        self.assertEqual(meta["size"], Decimal("5.00"))
        self.assertEqual(meta["entry_price"], Decimal("5.00") / Decimal("9"))
        self.assertEqual(strategy.risk_engine._positions[order_id]["size"], Decimal("5.00"))

    def test_unknown_settlement_late_redeem_correction(self):
        strategy = self._new_strategy()
        order_id = "late-order"
        strategy._open_live_trades[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-late",
            slug="slug-late",
            condition_id="cond-late",
        )
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        original_grace = os.environ.get("LIVE_SETTLEMENT_GRACE_SECONDS")
        try:
            os.environ["LIVE_SETTLEMENT_GRACE_SECONDS"] = "0"
            strategy._settle_expired_live_trades()
        finally:
            if original_grace is None:
                os.environ.pop("LIVE_SETTLEMENT_GRACE_SECONDS", None)
            else:
                os.environ["LIVE_SETTLEMENT_GRACE_SECONDS"] = original_grace

        unknown = next(trade for trade in strategy._settled_live_trades if trade["order_id"] == order_id)
        self.assertEqual(unknown["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertEqual(unknown["pnl"], "UNKNOWN")
        self.assertNotIn(order_id, strategy.risk_engine._positions)

        strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xlate",
                "amount": "4",
                "slug": "slug-late",
                "condition_id": "cond-late",
                "asset_id": "token-late",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        corrected = next(trade for trade in strategy._settled_live_trades if trade["order_id"] == order_id)
        self.assertEqual(corrected["settlement_source"], "late_auto_redeem")
        self.assertEqual(corrected["corrected_from"], "SETTLEMENT_UNKNOWN")
        self.assertEqual(Decimal(corrected["payout"]), Decimal("4"))
        self.assertEqual(Decimal(corrected["pnl"]), Decimal("2.00"))
        self.assertFalse(corrected["needs_reconciliation"])

    def test_same_transaction_multiple_redeems_are_not_deduped_together(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-a"] = self._live_trade_meta(
            order_id="order-a",
            token_id="token-a",
            slug="slug-a",
            condition_id="cond-a",
        )
        strategy._open_live_trades["order-b"] = self._live_trade_meta(
            order_id="order-b",
            token_id="token-b",
            slug="slug-b",
            condition_id="cond-b",
        )
        strategy.risk_engine.add_position("order-a", Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy.risk_engine.add_position("order-b", Decimal("2.00"), Decimal("0.50"), "buy_yes")

        base = {
            "txn_hash": "0xbatched",
            "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        }
        self.assertTrue(strategy._handle_auto_redeem_event({**base, "amount": "4", "slug": "slug-a", "condition_id": "cond-a", "asset_id": "token-a"}))
        self.assertTrue(strategy._handle_auto_redeem_event({**base, "amount": "3", "slug": "slug-b", "condition_id": "cond-b", "asset_id": "token-b"}))

        settled_ids = {trade["order_id"] for trade in strategy._settled_live_trades}
        self.assertEqual(settled_ids, {"order-a", "order-b"})
        self.assertEqual(len(strategy._seen_auto_redeem_events), 2)
        for trade in strategy._settled_live_trades:
            self.assertEqual(trade["auto_redeem"]["txn_hash"], "0xbatched")
            self.assertTrue(trade["auto_redeem_event_key"].startswith("0xbatched|"))

    def test_auto_redeem_save_failure_rolls_back_and_keeps_event_retryable(self):
        strategy = self._new_strategy()
        order_id = "order-save-failure"
        strategy._open_live_trades[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-save-failure",
            slug="slug-save-failure",
            condition_id="cond-save-failure",
        )
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        bad_path = Path(f"/tmp/codex_live_trades_redeem_bad_{os.getpid()}_{id(self)}")
        bad_path.mkdir(exist_ok=True)
        original_path = self.bot.LIVE_TRADE_LEDGER_PATH
        self.bot.LIVE_TRADE_LEDGER_PATH = bad_path
        try:
            result = strategy._handle_auto_redeem_event(
                {
                    "txn_hash": "0xsave-failure",
                    "amount": "4",
                    "slug": "slug-save-failure",
                    "condition_id": "cond-save-failure",
                    "asset_id": "token-save-failure",
                    "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                }
            )
        finally:
            self.bot.LIVE_TRADE_LEDGER_PATH = original_path
            bad_path.with_name(bad_path.name + ".tmp").unlink(missing_ok=True)
            bad_path.rmdir()

        self.assertFalse(result)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertIn(order_id, strategy.risk_engine._positions)
        self.assertEqual(strategy.performance_tracker.trades, [])

    def test_unmatched_redeem_is_retried_after_fill(self):
        strategy = self._new_strategy()
        payload = {
            "txn_hash": "0xearly",
            "amount": "4",
            "slug": "slug-early",
            "condition_id": "cond-early",
            "asset_id": "token-early",
            "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        }

        self.assertFalse(strategy._handle_auto_redeem_event(payload))
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

        strategy._open_live_trades["order-early"] = self._live_trade_meta(
            order_id="order-early",
            token_id="token-early",
            slug="slug-early",
            condition_id="cond-early",
        )
        strategy.risk_engine.add_position("order-early", Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._retry_pending_auto_redeems("unit test")

        self.assertEqual(len(strategy._pending_auto_redeem_events), 0)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 1)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], "order-early")

    def test_redeem_without_token_hint_is_not_auto_allocated(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-manual-risk"] = self._live_trade_meta(
            order_id="order-manual-risk",
            token_id="token-manual-risk",
            slug="slug-manual-risk",
            condition_id="cond-manual-risk",
        )

        self.assertFalse(
            strategy._handle_auto_redeem_event(
                {
                    "txn_hash": "0xnohint",
                    "amount": "4",
                    "slug": "slug-manual-risk",
                    "condition_id": "cond-manual-risk",
                    "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                }
            )
        )
        self.assertIn("order-manual-risk", strategy._open_live_trades)
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_buy_side_is_not_an_outcome_hint(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-buy-side"] = self._live_trade_meta(
            order_id="order-buy-side",
            token_id="token-buy-side",
            slug="slug-buy-side",
            condition_id="cond-buy-side",
        )

        self.assertFalse(
            strategy._handle_auto_redeem_event(
                {
                    "txn_hash": "0xbuy-side",
                    "amount": "4",
                    "slug": "slug-buy-side",
                    "condition_id": "cond-buy-side",
                    "side": "BUY",
                    "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                }
            )
        )

        self.assertIn("order-buy-side", strategy._open_live_trades)
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_outcome_side_can_match_structured_direction(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-side-outcome"] = self._live_trade_meta(
            order_id="order-side-outcome",
            token_id="token-side-outcome",
            slug="slug-side-outcome",
            condition_id="cond-side-outcome",
        )
        strategy.risk_engine.add_position("order-side-outcome", Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xside-outcome",
                "amount": "4",
                "slug": "slug-side-outcome",
                "condition_id": "cond-side-outcome",
                "side": "YES",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertTrue(settled)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], "order-side-outcome")
        self.assertEqual(len(strategy._pending_auto_redeem_events), 0)

    def test_auto_redeem_positive_payout_with_unknown_units_stays_pending(self):
        strategy = self._new_strategy()
        meta = self._live_trade_meta(
            order_id="order-unknown-units",
            token_id="token-unknown-units",
            slug="slug-unknown-units",
            condition_id="cond-unknown-units",
        )
        meta.pop("filled_qty")
        meta.pop("estimated_tokens")
        strategy._open_live_trades["order-unknown-units"] = meta
        strategy.risk_engine.add_position("order-unknown-units", Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xunknown-units",
                "amount": "4",
                "slug": "slug-unknown-units",
                "condition_id": "cond-unknown-units",
                "asset_id": "token-unknown-units",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn("order-unknown-units", strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_late_auto_redeem_positive_payout_with_unknown_units_stays_unknown(self):
        strategy = self._new_strategy()
        trade = self._live_trade_meta(
            order_id="unknown-settled-units",
            token_id="token-unknown-settled",
            slug="slug-unknown-settled",
            condition_id="cond-unknown-settled",
        )
        trade.pop("filled_qty")
        trade.pop("estimated_tokens")
        trade.update(
            {
                "settlement_source": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": True,
                "payout": "UNKNOWN",
                "pnl": "UNKNOWN",
            }
        )
        strategy._settled_live_trades.append(trade)

        corrected = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xunknown-settled",
                "amount": "4",
                "slug": "slug-unknown-settled",
                "condition_id": "cond-unknown-settled",
                "asset_id": "token-unknown-settled",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(corrected)
        self.assertEqual(trade["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertEqual(trade["payout"], "UNKNOWN")
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_transactional_path_logs_settlement_banner(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-banner"] = self._live_trade_meta(
            order_id="order-banner",
            token_id="token-banner",
            slug="slug-banner",
            condition_id="cond-banner",
        )
        strategy.risk_engine.add_position("order-banner", Decimal("2.00"), Decimal("0.50"), "buy_yes")
        info_messages = []
        original_info = self.bot.logger.info
        self.bot.logger.info = lambda message, *args, **kwargs: info_messages.append(str(message))

        try:
            settled = strategy._handle_auto_redeem_event(
                {
                    "txn_hash": "0xbanner",
                    "amount": "4",
                    "slug": "slug-banner",
                    "condition_id": "cond-banner",
                    "asset_id": "token-banner",
                    "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                }
            )
        finally:
            self.bot.logger.info = original_info

        self.assertTrue(settled)
        self.assertIn("LIVE TRADE SETTLED", info_messages)

    def test_late_auto_redeem_transactional_path_logs_correction_banner(self):
        strategy = self._new_strategy()
        trade = self._live_trade_meta(
            order_id="order-late-banner",
            token_id="token-late-banner",
            slug="slug-late-banner",
            condition_id="cond-late-banner",
        )
        trade.update(
            {
                "settlement_source": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": True,
                "payout": "UNKNOWN",
                "pnl": "UNKNOWN",
            }
        )
        strategy._settled_live_trades.append(trade)
        info_messages = []
        original_info = self.bot.logger.info
        self.bot.logger.info = lambda message, *args, **kwargs: info_messages.append(str(message))

        try:
            corrected = strategy._handle_auto_redeem_event(
                {
                    "txn_hash": "0xlate-banner",
                    "amount": "4",
                    "slug": "slug-late-banner",
                    "condition_id": "cond-late-banner",
                    "asset_id": "token-late-banner",
                    "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                }
            )
        finally:
            self.bot.logger.info = original_info

        self.assertTrue(corrected)
        self.assertIn("LIVE TRADE SETTLEMENT CORRECTED", info_messages)

    def test_auto_redeem_uses_first_normalizable_outcome_hint(self):
        strategy = self._new_strategy()
        self.assertEqual(
            strategy._payload_outcome_hint({"outcome": "resolved", "side": "YES"}),
            "up",
        )

    def test_auto_redeem_token_alias_matches_trade(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-alias"] = self._live_trade_meta(
            order_id="order-alias",
            token_id="token-alias",
            slug="slug-alias",
            condition_id="cond-alias",
        )
        strategy.risk_engine.add_position("order-alias", Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xalias",
                "amount": "4.000000",
                "slug": "slug-alias",
                "condition_id": "cond-alias",
                "tokenId": "token-alias",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertTrue(settled)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], "order-alias")
        self.assertEqual(len(strategy._pending_auto_redeem_events), 0)

    def test_auto_redeem_without_valid_timestamp_is_pending(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-no-timestamp"] = self._live_trade_meta(
            order_id="order-no-timestamp",
            token_id="token-no-timestamp",
            slug="slug-no-timestamp",
            condition_id="cond-no-timestamp",
        )

        self.assertFalse(
            strategy._handle_auto_redeem_event(
                {
                    "txn_hash": "0xno-timestamp",
                    "amount": "4",
                    "slug": "slug-no-timestamp",
                    "condition_id": "cond-no-timestamp",
                    "asset_id": "token-no-timestamp",
                }
            )
        )

        self.assertIn("order-no-timestamp", strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)

    def test_auto_redeem_with_invalid_timestamp_is_pending(self):
        strategy = self._new_strategy()
        strategy._open_live_trades["order-bad-timestamp"] = self._live_trade_meta(
            order_id="order-bad-timestamp",
            token_id="token-bad-timestamp",
            slug="slug-bad-timestamp",
            condition_id="cond-bad-timestamp",
        )

        self.assertFalse(
            strategy._handle_auto_redeem_event(
                {
                    "txn_hash": "0xbad-timestamp",
                    "amount": "4",
                    "slug": "slug-bad-timestamp",
                    "condition_id": "cond-bad-timestamp",
                    "asset_id": "token-bad-timestamp",
                    "timestamp": "not-a-timestamp",
                }
            )
        )

        self.assertIn("order-bad-timestamp", strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)

    def test_unreadable_existing_ledger_aborts_startup(self):
        self.assertEqual(self.bot.LIVE_TRADE_LEDGER_PATH, self._test_ledger_path)
        self._test_ledger_path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(self.bot.SettlementLedgerError):
            self._new_strategy()

    def test_ledger_save_failure_blocks_live_trading(self):
        strategy = self._new_strategy()
        bad_path = Path(f"/tmp/codex_live_trades_bad_{os.getpid()}_{id(self)}")
        bad_path.mkdir(exist_ok=True)
        original_path = self.bot.LIVE_TRADE_LEDGER_PATH
        self.bot.LIVE_TRADE_LEDGER_PATH = bad_path
        try:
            with self.assertRaises(self.bot.SettlementLedgerError):
                strategy._save_live_trade_ledger()
            unresolved = strategy._unresolved_settlement_unknowns()
        finally:
            self.bot.LIVE_TRADE_LEDGER_PATH = original_path
            bad_path.with_name(bad_path.name + ".tmp").unlink(missing_ok=True)
            bad_path.rmdir()

        self.assertEqual(unresolved[-1]["settlement_source"], "LEDGER_BLOCKED")

    def test_fill_save_failure_blocks_without_callback_exception(self):
        strategy = self._new_strategy()
        order_id = "fill-save-failure"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-save-failure")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("5.50"), Decimal("0.55"), "buy_yes")
        bad_path = Path(f"/tmp/codex_live_trades_fill_bad_{os.getpid()}_{id(self)}")
        bad_path.mkdir(exist_ok=True)
        original_path = self.bot.LIVE_TRADE_LEDGER_PATH
        self.bot.LIVE_TRADE_LEDGER_PATH = bad_path
        try:
            try:
                result = strategy._record_live_order_fill(order_id, Decimal("0.50"), Decimal("4"))
            except Exception as exc:
                self.fail(f"_record_live_order_fill propagated exception: {exc}")
            unresolved = strategy._unresolved_settlement_unknowns()
        finally:
            self.bot.LIVE_TRADE_LEDGER_PATH = original_path
            bad_path.with_name(bad_path.name + ".tmp").unlink(missing_ok=True)
            bad_path.rmdir()

        self.assertFalse(result)
        self.assertEqual(unresolved[-1]["settlement_source"], "LEDGER_BLOCKED")
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertNotIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy.risk_engine._positions[order_id]["size"], Decimal("5.50"))
        self.assertEqual(strategy.risk_engine._positions[order_id]["entry_price"], Decimal("0.55"))

        self.assertFalse(strategy._record_live_order_fill(order_id, Decimal("0.50"), Decimal("4")))
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertNotIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy.risk_engine._positions[order_id]["size"], Decimal("5.50"))
        self.assertEqual(strategy.risk_engine._positions[order_id]["entry_price"], Decimal("0.55"))

    def test_blocked_fill_does_not_increment_filled_metric(self):
        strategy = self._new_strategy()
        strategy._settlement_ledger_blocked_reason = "unit test ledger block"
        events = []
        strategy._track_order_event = lambda event_type: events.append(event_type)

        class _FillEvent:
            client_order_id = "blocked-fill"
            last_px = Decimal("0.50")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        self.assertEqual(events, [])

    def test_make_trading_decision_sync_logs_mode_control_errors(self):
        strategy = self._new_strategy()
        strategy.live_execution_enabled = True
        strategy.current_simulation_mode = False
        errors = []
        original_error = self.bot.logger.error
        self.bot.logger.error = lambda message, *args, **kwargs: errors.append(str(message))

        try:
            strategy._make_trading_decision_sync(0.70)
        finally:
            self.bot.logger.error = original_error

        self.assertFalse(strategy._decision_in_progress)
        self.assertTrue(any("Trading decision aborted" in message for message in errors))
        self.assertTrue(any("Traceback" in message for message in errors))


    def test_missing_market_end_time_blocks_settlement_accounting(self):
        strategy = self._new_strategy()
        meta = self._live_trade_meta(order_id="missing-end-time")
        meta.pop("market_end_time")
        strategy._open_live_trades["missing-end-time"] = meta

        strategy._settle_expired_live_trades()

        unresolved = strategy._unresolved_settlement_unknowns()
        self.assertEqual(unresolved[-1]["settlement_source"], "LEDGER_BLOCKED")

    def test_missing_market_end_time_block_logging_is_deduped(self):
        strategy = self._new_strategy()
        for order_id in ("missing-end-time-a", "missing-end-time-b"):
            meta = self._live_trade_meta(order_id=order_id)
            meta.pop("market_end_time")
            strategy._open_live_trades[order_id] = meta
        errors = []
        original_error = self.bot.logger.error
        self.bot.logger.error = lambda message, *args, **kwargs: errors.append(str(message))

        try:
            strategy._settle_expired_live_trades()
            strategy._settle_expired_live_trades()
        finally:
            self.bot.logger.error = original_error

        self.assertEqual(len(errors), 1)
        self.assertIn("2 open live trade(s) missing market_end_time", errors[0])

    def test_invalid_settlement_grace_blocks_settlement_accounting(self):
        strategy = self._new_strategy()
        original_grace = os.environ.get("LIVE_SETTLEMENT_GRACE_SECONDS")
        try:
            os.environ["LIVE_SETTLEMENT_GRACE_SECONDS"] = "not-an-int"
            strategy._settle_expired_live_trades()
        finally:
            if original_grace is None:
                os.environ.pop("LIVE_SETTLEMENT_GRACE_SECONDS", None)
            else:
                os.environ["LIVE_SETTLEMENT_GRACE_SECONDS"] = original_grace

        unresolved = strategy._unresolved_settlement_unknowns()
        self.assertEqual(unresolved[-1]["settlement_source"], "LEDGER_BLOCKED")

    def test_auto_redeem_event_key_normalizes_amount_format(self):
        strategy = self._new_strategy()
        base = {
            "txn_hash": "0xamount",
            "slug": "slug-amount",
            "condition_id": "cond-amount",
            "asset_id": "token-amount",
        }

        self.assertEqual(
            strategy._auto_redeem_event_key({**base, "amount": "1.000000"}),
            strategy._auto_redeem_event_key({**base, "amount": "1.0"}),
        )

    def test_pending_auto_redeem_events_are_pruned_and_capped(self):
        strategy = self._new_strategy()
        now = datetime.now(timezone.utc)
        stale_key = "stale|slug|token|1"
        strategy._pending_auto_redeem_events[stale_key] = {
            "txn_hash": "stale",
            "amount": "1",
            "_pending_since": (now - timedelta(days=8)).isoformat(),
        }
        for idx in range(self.bot.MAX_PENDING_AUTO_REDEEM_EVENTS + 1):
            strategy._pending_auto_redeem_events[f"fresh-{idx}|slug|token|1"] = {
                "txn_hash": f"fresh-{idx}",
                "amount": "1",
                "_pending_since": (now - timedelta(seconds=idx)).isoformat(),
            }

        dropped = strategy._prune_pending_auto_redeem_events_locked(now)

        self.assertEqual(dropped, 2)
        self.assertNotIn(stale_key, strategy._pending_auto_redeem_events)
        self.assertEqual(len(strategy._pending_auto_redeem_events), self.bot.MAX_PENDING_AUTO_REDEEM_EVENTS)

    def test_live_trading_pauses_with_unresolved_unknown_settlement(self):
        class _LiveRedis:
            def get(self, _key):
                return "0"

        strategy = self._new_strategy()
        strategy.redis_client = _LiveRedis()
        strategy.live_execution_enabled = True
        strategy.current_simulation_mode = False
        strategy._settled_live_trades.append(
            {
                "order_id": "unknown-order",
                "settlement_source": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": True,
            }
        )
        errors = []
        original_error = self.bot.logger.error
        self.bot.logger.error = lambda message, *args, **kwargs: errors.append(str(message))

        try:
            placed = asyncio.run(strategy._make_trading_decision(Decimal("0.70")))
        finally:
            self.bot.logger.error = original_error

        self.assertFalse(placed)
        self.assertTrue(any("LIVE TRADING PAUSED" in message for message in errors))

    def test_startup_retries_pending_auto_redeems(self):
        strategy = self._new_strategy()
        calls = []
        strategy._auto_redeem_registered = True
        strategy._retry_pending_auto_redeems = lambda reason: calls.append(reason)
        strategy._load_all_btc_instruments = lambda: None
        strategy._generate_synthetic_history = lambda *args, **kwargs: None
        strategy.run_in_executor = lambda fn: None
        strategy.instrument_id = None

        strategy.on_start()

        self.assertEqual(calls, ["startup ledger replay"])

    def test_token_pairing_uses_outcome_not_load_order(self):
        class _Instrument:
            def __init__(self, instrument_id, slug, condition_id, tokens):
                self.id = instrument_id
                self.info = {
                    "question": "Bitcoin Up or Down",
                    "market_slug": slug,
                    "condition_id": condition_id,
                    "tokens": tokens,
                }

        class _Cache:
            def __init__(self, instruments):
                self._instruments = instruments

            def instruments(self):
                return self._instruments

        future_ts = int(datetime.now(timezone.utc).timestamp()) + 60
        slug = f"btc-updown-15m-{future_ts}"
        condition_id = "0xcondition"
        tokens = [
            {"token_id": "tokendown", "outcome": "Down"},
            {"token_id": "tokenup", "outcome": "Up"},
        ]
        # Down appears first on purpose. YES/UP must still be selected from outcome.
        strategy = self._new_strategy()
        strategy.cache = _Cache(
            [
                _Instrument(f"{condition_id}-tokendown.POLYMARKET", slug, condition_id, tokens),
                _Instrument(f"{condition_id}-tokenup.POLYMARKET", slug, condition_id, tokens),
            ]
        )
        subscribed = []
        strategy.subscribe_quote_ticks = lambda instrument_id: subscribed.append(str(instrument_id))

        strategy._load_all_btc_instruments()

        self.assertEqual(strategy.all_btc_instruments[0]["yes_token_id"], "tokenup")
        self.assertEqual(strategy.all_btc_instruments[0]["no_token_id"], "tokendown")
        self.assertEqual(str(strategy._yes_instrument_id), f"{condition_id}-tokenup.POLYMARKET")
        self.assertEqual(str(strategy._no_instrument_id), f"{condition_id}-tokendown.POLYMARKET")

    def test_token_pairing_refuses_market_without_outcome_metadata(self):
        class _Instrument:
            def __init__(self, instrument_id, slug, condition_id, tokens):
                self.id = instrument_id
                self.info = {
                    "question": "Bitcoin Up or Down",
                    "market_slug": slug,
                    "condition_id": condition_id,
                    "tokens": tokens,
                }

        class _Cache:
            def __init__(self, instruments):
                self._instruments = instruments

            def instruments(self):
                return self._instruments

        future_ts = int(datetime.now(timezone.utc).timestamp()) + 60
        slug = f"btc-updown-15m-{future_ts}"
        condition_id = "0xconditionmissing"
        tokens = [
            {"token_id": "token-a"},
            {"token_id": "token-b"},
        ]
        strategy = self._new_strategy()
        strategy.cache = _Cache(
            [
                _Instrument(f"{condition_id}-token-a.POLYMARKET", slug, condition_id, tokens),
                _Instrument(f"{condition_id}-token-b.POLYMARKET", slug, condition_id, tokens),
            ]
        )

        strategy._load_all_btc_instruments()

        self.assertEqual(strategy.all_btc_instruments, [])


if __name__ == "__main__":
    unittest.main()
