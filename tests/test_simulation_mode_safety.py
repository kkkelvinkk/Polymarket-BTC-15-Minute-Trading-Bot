import asyncio
import copy
import importlib
import json
import os
import subprocess
import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT_RESOLVED = REPO_ROOT.resolve()
_STUBBED_MODULE_NAMES = []
_DOTENV_CALLS = []


class _DummyLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _DummyConfig:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    @classmethod
    def from_str(cls, value):
        return cls(value)


class _DummyOrderSideValue:
    def __init__(self, name):
        self.name = name


class _DummyOrderSide:
    BUY = _DummyOrderSideValue("BUY")
    SELL = _DummyOrderSideValue("SELL")


class _DummyTimeInForce:
    IOC = "IOC"
    GTC = "GTC"


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
        self.limits = types.SimpleNamespace(
            max_position_size=Decimal(os.getenv("MAX_POSITION_SIZE", "5.51"))
        )

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
    if name in _STUBBED_MODULE_NAMES:
        return sys.modules[name]

    module = types.ModuleType(name)
    sys.modules[name] = module
    _STUBBED_MODULE_NAMES.append(name)

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


def _is_repo_module(module):
    module_file = getattr(module, "__file__", None)
    return (
        module_file is not None
        and Path(module_file).resolve().is_relative_to(REPO_ROOT_RESOLVED)
    )


def _install_bot_dependency_stubs():
    _install_module(
        "patch_gamma_markets",
        apply_gamma_markets_patch=lambda: True,
        verify_patch=lambda: None,
    )
    _install_module(
        "patch_market_orders",
        apply_market_order_patch=lambda: True,
        register_actual_fill_handler=lambda _handler: None,
        register_auto_redeem_handler=lambda _handler: None,
        unregister_actual_fill_handler=lambda _handler: None,
        unregister_auto_redeem_handler=lambda _handler: None,
    )
    _install_module(
        "patch_polymarket_quote_warnings",
        apply_polymarket_quote_warning_patch=lambda: True,
    )
    _install_module("polymarket_v2_compat", apply_polymarket_v2_patch=lambda: True)
    _install_module(
        "dotenv",
        load_dotenv=lambda *args, **kwargs: _DOTENV_CALLS.append((args, kwargs)),
    )
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
        OrderSide=_DummyOrderSide,
        OrderType=types.SimpleNamespace(MARKET="MARKET"),
        TimeInForce=_DummyTimeInForce,
    )
    _install_module("nautilus_trader.model.objects", Price=_DummyConfig, Quantity=_DummyConfig)
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
    _install_module(
        "execution.risk_engine",
        get_risk_engine=lambda: _DummyRiskEngine(),
        RiskEngine=_DummyRiskEngine,
    )
    _install_module("monitoring.performance_tracker", get_performance_tracker=lambda: _DummyPerformanceTracker())
    _install_module("monitoring.grafana_exporter", get_grafana_exporter=lambda: object())
    _install_module("feedback.learning_engine", get_learning_engine=lambda: object())
    _install_module(
        "core.strategy_brain.signal_processors.base_processor",
        SignalDirection=types.SimpleNamespace(BULLISH="BULLISH", BEARISH="BEARISH"),
    )


class SimulationModeSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._module_snapshot = dict(sys.modules)
        cls._path_snapshot = list(sys.path)
        _STUBBED_MODULE_NAMES.clear()
        _install_bot_dependency_stubs()
        cls._stubbed_module_names = tuple(_STUBBED_MODULE_NAMES)
        sys.path.insert(0, str(REPO_ROOT))
        sys.modules.pop("bot", None)
        cls.bot = importlib.import_module("bot")

    @classmethod
    def tearDownClass(cls):
        if "bot" in cls._module_snapshot:
            sys.modules["bot"] = cls._module_snapshot["bot"]
        else:
            sys.modules.pop("bot", None)
        for name, module in tuple(sys.modules.items()):
            if name not in cls._module_snapshot and _is_repo_module(module):
                sys.modules.pop(name, None)
        for name in reversed(cls._stubbed_module_names):
            if name in cls._module_snapshot:
                sys.modules[name] = cls._module_snapshot[name]
            else:
                sys.modules.pop(name, None)
        sys.path[:] = cls._path_snapshot

    def setUp(self):
        self._original_ledger_path = self.bot.LIVE_TRADE_LEDGER_PATH
        self._original_require_token_hint = os.environ.get("REQUIRE_AUTO_REDEEM_TOKEN_HINT")
        self._original_order_type = os.environ.get("ORDER_TYPE")
        self._original_sizing_mode = os.environ.get("SIZING_MODE")
        self._original_market_buy_usd = os.environ.get("MARKET_BUY_USD")
        self._original_max_position_size = os.environ.get("MAX_POSITION_SIZE")
        self._original_max_account_state_age_seconds = os.environ.get("MAX_ACCOUNT_STATE_AGE_SECONDS")
        self._original_balance_safety_buffer_usd = os.environ.get("BALANCE_SAFETY_BUFFER_USD")
        self._original_nautilus_log_dir = os.environ.get("NAUTILUS_LOG_DIR")
        self._original_quote_stability_required = os.environ.get("QUOTE_STABILITY_REQUIRED")
        self._original_ev_fee_buffer = os.environ.get("EV_FEE_BUFFER")
        self._original_ev_spread_buffer = os.environ.get("EV_SPREAD_BUFFER")
        self._strategies = []
        os.environ["REQUIRE_AUTO_REDEEM_TOKEN_HINT"] = "true"
        os.environ["ORDER_TYPE"] = "market_ioc"
        os.environ["SIZING_MODE"] = "fixed"
        os.environ["MARKET_BUY_USD"] = "5.51"
        os.environ["MAX_POSITION_SIZE"] = "5.51"
        os.environ["MAX_ACCOUNT_STATE_AGE_SECONDS"] = "30"
        os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.00"
        os.environ["NAUTILUS_LOG_DIR"] = "/tmp/nautilus-test-logs"
        os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
        os.environ["EV_FEE_BUFFER"] = "0.005"
        os.environ["EV_SPREAD_BUFFER"] = "0.01"
        self._test_ledger_path = Path(f"/tmp/codex_live_trades_test_{os.getpid()}_{id(self)}.json")
        for path in (
            self._test_ledger_path,
            Path(str(self._test_ledger_path) + ".tmp"),
            Path(str(self._test_ledger_path) + ".bak"),
            Path(str(self._test_ledger_path) + ".lock"),
        ):
            path.unlink(missing_ok=True)
        for path in self._test_ledger_path.parent.glob(f"{self._test_ledger_path.name}.schema-v*.bak"):
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
        if self._original_order_type is None:
            os.environ.pop("ORDER_TYPE", None)
        else:
            os.environ["ORDER_TYPE"] = self._original_order_type
        if self._original_sizing_mode is None:
            os.environ.pop("SIZING_MODE", None)
        else:
            os.environ["SIZING_MODE"] = self._original_sizing_mode
        if self._original_market_buy_usd is None:
            os.environ.pop("MARKET_BUY_USD", None)
        else:
            os.environ["MARKET_BUY_USD"] = self._original_market_buy_usd
        if self._original_max_position_size is None:
            os.environ.pop("MAX_POSITION_SIZE", None)
        else:
            os.environ["MAX_POSITION_SIZE"] = self._original_max_position_size
        if self._original_max_account_state_age_seconds is None:
            os.environ.pop("MAX_ACCOUNT_STATE_AGE_SECONDS", None)
        else:
            os.environ["MAX_ACCOUNT_STATE_AGE_SECONDS"] = self._original_max_account_state_age_seconds
        if self._original_balance_safety_buffer_usd is None:
            os.environ.pop("BALANCE_SAFETY_BUFFER_USD", None)
        else:
            os.environ["BALANCE_SAFETY_BUFFER_USD"] = self._original_balance_safety_buffer_usd
        if self._original_nautilus_log_dir is None:
            os.environ.pop("NAUTILUS_LOG_DIR", None)
        else:
            os.environ["NAUTILUS_LOG_DIR"] = self._original_nautilus_log_dir
        if self._original_quote_stability_required is None:
            os.environ.pop("QUOTE_STABILITY_REQUIRED", None)
        else:
            os.environ["QUOTE_STABILITY_REQUIRED"] = self._original_quote_stability_required
        if self._original_ev_fee_buffer is None:
            os.environ.pop("EV_FEE_BUFFER", None)
        else:
            os.environ["EV_FEE_BUFFER"] = self._original_ev_fee_buffer
        if self._original_ev_spread_buffer is None:
            os.environ.pop("EV_SPREAD_BUFFER", None)
        else:
            os.environ["EV_SPREAD_BUFFER"] = self._original_ev_spread_buffer
        for path in (
            self._test_ledger_path,
            Path(str(self._test_ledger_path) + ".tmp"),
            Path(str(self._test_ledger_path) + ".bak"),
            Path(str(self._test_ledger_path) + ".lock"),
        ):
            path.unlink(missing_ok=True)
        for path in self._test_ledger_path.parent.glob(f"{self._test_ledger_path.name}.schema-v*.bak"):
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
                self.kernel = types.SimpleNamespace(
                    executor=types.SimpleNamespace(
                        shutdown=lambda wait=True, cancel_futures=True: captured.setdefault(
                            "executor_shutdown",
                            {"wait": wait, "cancel_futures": cancel_futures},
                        )
                    )
                )

            def add_data_client_factory(self, *args, **kwargs):
                pass

            def add_exec_client_factory(self, *args, **kwargs):
                pass

            def build(self):
                pass

            def run(self):
                raise KeyboardInterrupt

            def dispose(self):
                self.kernel.executor.shutdown(wait=True, cancel_futures=True)
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

    def test_confirm_live_without_live_fails_argument_parsing(self):
        # Live startup gate: --confirm-live is only valid with --live.
        with self.assertRaises(SystemExit) as raised:
            self.bot.parse_runtime_args(["--confirm-live"])
        self.assertEqual(raised.exception.code, 2)

    def test_confirm_live_with_live_parses_successfully(self):
        args = self.bot.parse_runtime_args(["--live", "--confirm-live"])
        self.assertTrue(args.live)
        self.assertTrue(args.confirm_live)

    def test_main_forces_cli_process_exit_after_run_returns(self):
        class _ExitCalled(Exception):
            pass

        captured = {}
        original_argv = sys.argv
        original_run = self.bot.run_integrated_bot
        original_exit = self.bot.os._exit

        def _run_integrated_bot(**kwargs):
            captured["run_kwargs"] = kwargs

        def _exit(code):
            captured["exit_code"] = code
            raise _ExitCalled

        try:
            sys.argv = ["bot.py", "--test-mode", "--no-grafana"]
            self.bot.run_integrated_bot = _run_integrated_bot
            self.bot.os._exit = _exit
            with self.assertRaises(_ExitCalled):
                self.bot.main()
        finally:
            sys.argv = original_argv
            self.bot.run_integrated_bot = original_run
            self.bot.os._exit = original_exit

        self.assertEqual(
            captured["run_kwargs"],
            {"simulation": True, "enable_grafana": False, "test_mode": True},
        )
        self.assertEqual(captured["exit_code"], 0)

    def test_live_market_buy_usd_gate_blocks_5_50_exactly(self):
        # Strict comparison: 5.50 must be blocked, 5.51 allowed.
        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ["MARKET_BUY_USD"] = "5.50"
            with self.assertRaisesRegex(RuntimeError, "MARKET_BUY_USD must be greater than 5.50"):
                self.bot.enforce_live_market_buy_usd_gate()
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_live_market_buy_usd_gate_allows_5_51(self):
        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            self.assertEqual(
                self.bot.enforce_live_market_buy_usd_gate(),
                Decimal("5.51"),
            )
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_live_market_buy_usd_gate_blocks_missing_value(self):
        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ.pop("MARKET_BUY_USD", None)
            with self.assertRaisesRegex(RuntimeError, "MARKET_BUY_USD must be greater than 5.50"):
                self.bot.enforce_live_market_buy_usd_gate()
        finally:
            if original is not None:
                os.environ["MARKET_BUY_USD"] = original

    def test_live_market_buy_usd_gate_blocks_malformed_value(self):
        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ["MARKET_BUY_USD"] = "not-a-decimal"
            with self.assertRaisesRegex(RuntimeError, "MARKET_BUY_USD must be greater than 5.50"):
                self.bot.enforce_live_market_buy_usd_gate()
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_live_market_buy_usd_gate_blocks_zero_and_negative(self):
        original = os.environ.get("MARKET_BUY_USD")
        try:
            for bad_value in ("0", "0.00", "-1.00", "-5.51"):
                os.environ["MARKET_BUY_USD"] = bad_value
                with self.assertRaisesRegex(
                    RuntimeError,
                    "MARKET_BUY_USD must be greater than 5.50",
                ):
                    self.bot.enforce_live_market_buy_usd_gate()
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_live_market_buy_usd_gate_blocks_non_finite(self):
        original = os.environ.get("MARKET_BUY_USD")
        try:
            for bad_value in ("NaN", "Infinity", "-Infinity"):
                os.environ["MARKET_BUY_USD"] = bad_value
                with self.assertRaisesRegex(
                    RuntimeError,
                    "MARKET_BUY_USD must be greater than 5.50",
                ):
                    self.bot.enforce_live_market_buy_usd_gate()
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_live_market_buy_usd_gate_blocks_boundary_just_above_5_50(self):
        # Quantize-before-compare: 5.500001 must be quantized down to
        # 5.50 (ROUND_DOWN) BEFORE the comparison, so it remains blocked.
        # Reviewer #2 finding #8: previous draft compared raw Decimal then
        # quantized, which allowed 5.500001 through but recorded 5.50 as the
        # authoritative spend.
        original = os.environ.get("MARKET_BUY_USD")
        try:
            for boundary_value in ("5.5", "5.50", "5.500", "5.5000", "5.500001", "5.5099"):
                os.environ["MARKET_BUY_USD"] = boundary_value
                with self.assertRaisesRegex(
                    RuntimeError,
                    "MARKET_BUY_USD must be greater than 5.50",
                ):
                    self.bot.enforce_live_market_buy_usd_gate()
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_live_market_buy_usd_gate_allows_minimum_valid_values(self):
        original = os.environ.get("MARKET_BUY_USD")
        try:
            for good_value in ("5.51", "5.510", "5.5100", "5.99", "55.00", "100.00"):
                os.environ["MARKET_BUY_USD"] = good_value
                amount = self.bot.enforce_live_market_buy_usd_gate()
                self.assertGreater(amount, Decimal("5.50"))
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_validate_live_market_buy_usd_returns_tuple(self):
        # Non-raising validator that both startup and runtime call sites
        # can share. Avoids try/except in the order submission path (CLAUDE.md
        # rule #1 fallback compliance).
        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            ok, err, amount = self.bot.validate_live_market_buy_usd()
            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual(amount, Decimal("5.51"))

            os.environ["MARKET_BUY_USD"] = "5.50"
            ok, err, amount = self.bot.validate_live_market_buy_usd()
            self.assertFalse(ok)
            self.assertIn("MARKET_BUY_USD must be greater than 5.50", err)
            self.assertIsNone(amount)
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_prompt_for_live_confirmation_accepts_exact_LIVE(self):
        # Reviewer #3 finding #1: lock down literal-match contract.
        import builtins
        original_input = builtins.input
        try:
            builtins.input = lambda _prompt="": "LIVE"
            self.bot._prompt_for_live_confirmation()  # returns None on success
        finally:
            builtins.input = original_input

    def test_prompt_for_live_confirmation_rejects_lowercase(self):
        import builtins
        original_input = builtins.input
        try:
            builtins.input = lambda _prompt="": "live"
            with self.assertRaises(SystemExit) as raised:
                self.bot._prompt_for_live_confirmation()
            self.assertIn("did not type LIVE", str(raised.exception))
        finally:
            builtins.input = original_input

    def test_prompt_for_live_confirmation_rejects_trailing_whitespace(self):
        # The plan requires exact literal match; "LIVE " (trailing space)
        # must be rejected.
        import builtins
        original_input = builtins.input
        try:
            builtins.input = lambda _prompt="": "LIVE "
            with self.assertRaises(SystemExit) as raised:
                self.bot._prompt_for_live_confirmation()
            self.assertIn("did not type LIVE", str(raised.exception))
        finally:
            builtins.input = original_input

    def test_prompt_for_live_confirmation_handles_eof_on_non_tty(self):
        # Reviewer #2 finding #1: EOFError on piped/daemonized stdin must be
        # translated into a clean SystemExit, not a raw traceback.
        import builtins
        original_input = builtins.input

        def _raise_eof(_prompt=""):
            raise EOFError("stdin closed")

        try:
            builtins.input = _raise_eof
            with self.assertRaises(SystemExit) as raised:
                self.bot._prompt_for_live_confirmation()
            self.assertIn("stdin is not a TTY", str(raised.exception))
        finally:
            builtins.input = original_input

    def test_place_real_order_runtime_gate_rejects_invalid_market_buy_usd(self):
        # Runtime validation rejects invalid MARKET_BUY_USD before any order is
        # submitted, even if a live-enabled process reaches this call after
        # startup env drift or through a nonstandard submit path.
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        # Set instrument_id to a truthy value so the runtime gate is the
        # short-circuit, not the missing-instrument guard.
        strategy.instrument_id = "dummy-instrument-id"

        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ["MARKET_BUY_USD"] = "5.50"
            result = asyncio.run(
                strategy._place_real_order(
                    signal=None,
                    position_size=Decimal("5.50"),
                    current_price=0.5,
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                )
            )
            self.assertFalse(result)
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    # --- EV-gate VWAP wiring ------------------------------------------------

    def test_compute_depth_aware_entry_returns_vwap_when_book_is_healthy(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        order_book = {
            "bids": [],
            "asks": [
                {"price": "0.62", "size": "100"},
                {"price": "0.70", "size": "100"},
            ],
        }
        vwap = asyncio.run(
            strategy._compute_depth_aware_entry(
                side_token_id="TOKEN",
                entry_source="YES ask",
                position_size_usd=Decimal("10"),
                top_of_book_entry=Decimal("0.62"),
                rec=rec,
                order_book=order_book,
            )
        )
        # Budget $10 against 100 tokens at $0.62 ($62 capacity) → fully filled
        # at top level; VWAP ≈ $0.62 (Decimal division may carry trailing
        # digits — compare with a tight tolerance).
        self.assertIsNotNone(vwap)
        self.assertAlmostEqual(float(vwap), 0.62, places=8)
        self.assertIsNone(rec.fields["rejected_at_gate"])

    def test_compute_depth_aware_entry_market_ioc_records_explicit_cost(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        details = asyncio.run(
            strategy._compute_depth_aware_entry_details(
                side_token_id="TOKEN",
                entry_source="YES ask",
                position_size_usd=Decimal("10"),
                top_of_book_entry=Decimal("0.62"),
                rec=rec,
                order_book={
                    "bids": [],
                    "asks": [
                        {"price": "0.62", "size": "100"},
                        {"price": "0.70", "size": "100"},
                    ],
                },
            )
        )

        self.assertIsNotNone(details)
        self.assertEqual(details.actual_cost, Decimal("10"))
        self.assertIsNone(rec.fields["rejected_at_gate"])

    def test_compute_depth_aware_entry_fails_closed_on_missing_token_id(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        result = asyncio.run(
            strategy._compute_depth_aware_entry(
                side_token_id=None,
                entry_source="YES ask",
                position_size_usd=Decimal("10"),
                top_of_book_entry=Decimal("0.62"),
                rec=rec,
            )
        )
        self.assertIsNone(result)
        self.assertEqual(rec.fields["rejected_at_gate"], "depth_aware_missing_token_id")

    def test_compute_depth_aware_entry_requires_order_book_snapshot(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)

        with self.assertRaisesRegex(RuntimeError, "caller-provided order_book snapshot"):
            result = asyncio.run(
                strategy._compute_depth_aware_entry(
                    side_token_id="TOKEN",
                    entry_source="YES ask",
                    position_size_usd=Decimal("10"),
                    top_of_book_entry=Decimal("0.62"),
                    rec=rec,
                )
            )
        self.assertIsNone(rec.fields["rejected_at_gate"])

    def test_compute_depth_aware_entry_fails_closed_on_thin_book(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        order_book = {
            "bids": [],
            # Only 2 tokens at $0.50 = $1.00 of liquidity for a $10 budget
            "asks": [{"price": "0.50", "size": "2"}],
        }
        result = asyncio.run(
            strategy._compute_depth_aware_entry(
                side_token_id="TOKEN",
                entry_source="YES ask",
                position_size_usd=Decimal("10"),
                top_of_book_entry=Decimal("0.50"),
                rec=rec,
                order_book=order_book,
            )
        )
        self.assertIsNone(result)
        self.assertEqual(rec.fields["rejected_at_gate"], "depth_aware_book_too_thin")

    def test_compute_depth_aware_entry_fails_closed_on_invalid_book_level(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        order_book = {
            "bids": [],
            "asks": [{"price": "1.5", "size": "10"}],  # price > 1.0 — invalid
        }
        result = asyncio.run(
            strategy._compute_depth_aware_entry(
                side_token_id="TOKEN",
                entry_source="YES ask",
                position_size_usd=Decimal("10"),
                top_of_book_entry=Decimal("0.5"),
                rec=rec,
                order_book=order_book,
            )
        )
        self.assertIsNone(result)
        self.assertEqual(rec.fields["rejected_at_gate"], "depth_aware_invalid_book_level")

    def test_compute_depth_aware_entry_limit_ioc_partial_ok_proceeds_on_partial_depth(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        order_book = {
            "bids": [],
            "asks": [{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}],
        }
        details = asyncio.run(
            strategy._compute_depth_aware_entry_details(
                side_token_id="TOKEN",
                entry_source="YES ask",
                position_size_usd=Decimal("12.40"),
                top_of_book_entry=Decimal("0.62"),
                rec=rec,
                order_type=self.bot.ORDER_TYPE_LIMIT_IOC,
                submitted_limit_price=Decimal("0.62"),
                limit_order_token_qty=Decimal("20"),
                limit_ioc_fill_policy=self.bot.LIMIT_IOC_FILL_POLICY_PARTIAL_OK,
                order_book=order_book,
            )
        )
        self.assertIsNotNone(details)
        self.assertEqual(details.executable_entry, Decimal("0.62"))
        self.assertEqual(details.tokens_filled, Decimal("10"))
        self.assertEqual(details.actual_cost, Decimal("6.20"))
        self.assertFalse(details.fully_filled)
        self.assertIsNone(rec.fields["rejected_at_gate"])

    def test_compute_depth_aware_entry_uses_provided_order_book_snapshot(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)

        def _unexpected_fetch(_token_id):
            raise AssertionError("depth helper must use the provided order-book snapshot")

        strategy.orderbook_processor.fetch_order_book = _unexpected_fetch
        details = asyncio.run(
            strategy._compute_depth_aware_entry_details(
                side_token_id="TOKEN",
                entry_source="YES ask",
                position_size_usd=Decimal("12.40"),
                top_of_book_entry=Decimal("0.62"),
                rec=rec,
                order_type=self.bot.ORDER_TYPE_LIMIT_IOC,
                submitted_limit_price=Decimal("0.62"),
                limit_order_token_qty=Decimal("20"),
                limit_ioc_fill_policy=self.bot.LIMIT_IOC_FILL_POLICY_PARTIAL_OK,
                order_book={
                    "bids": [],
                    "asks": [{"price": "0.62", "size": "10"}, {"price": "0.70", "size": "15"}],
                },
            )
        )

        self.assertIsNotNone(details)
        self.assertEqual(details.tokens_filled, Decimal("10"))
        self.assertIsNone(rec.fields["rejected_at_gate"])

    def test_compute_depth_aware_entry_limit_ioc_rejects_no_liquidity_at_cap(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        order_book = {
            "bids": [],
            "asks": [{"price": "0.70", "size": "10"}],
        }
        details = asyncio.run(
            strategy._compute_depth_aware_entry_details(
                side_token_id="TOKEN",
                entry_source="YES ask",
                position_size_usd=Decimal("10"),
                top_of_book_entry=Decimal("0.70"),
                rec=rec,
                order_type=self.bot.ORDER_TYPE_LIMIT_IOC,
                submitted_limit_price=Decimal("0.62"),
                limit_order_token_qty=Decimal("10"),
                limit_ioc_fill_policy=self.bot.LIMIT_IOC_FILL_POLICY_PARTIAL_OK,
                order_book=order_book,
            )
        )
        self.assertIsNone(details)
        self.assertEqual(rec.fields["rejected_at_gate"], "depth_aware_limit_ioc_no_liquidity")

    def test_compute_depth_aware_entry_limit_ioc_all_or_nothing_blocks_fak_path(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=True,
            )
        )
        rec = self.bot.DecisionRecord(current_price=None)
        order_book = {
            "bids": [],
            "asks": [{"price": "0.62", "size": "10"}],
        }
        with self.assertRaisesRegex(RuntimeError, "requires verified FOK"):
            asyncio.run(
                strategy._compute_depth_aware_entry_details(
                    side_token_id="TOKEN",
                    entry_source="YES ask",
                    position_size_usd=Decimal("10"),
                    top_of_book_entry=Decimal("0.62"),
                    rec=rec,
                    order_type=self.bot.ORDER_TYPE_LIMIT_IOC,
                    submitted_limit_price=Decimal("0.62"),
                    limit_order_token_qty=Decimal("10"),
                    limit_ioc_fill_policy=self.bot.LIMIT_IOC_FILL_POLICY_ALL_OR_NOTHING,
                    order_book=order_book,
                )
            )

    # --- LIMIT_IOC helpers ---------------------------------------------------

    def test_compute_limit_price_returns_none_for_low_confidence(self):
        # fused 0.04 - edge 0.05 = -0.01 → out of (0, 1)
        result = self.bot.compute_limit_price(0.04, Decimal("0.05"))
        self.assertIsNone(result)

    def test_compute_limit_price_returns_none_when_cap_at_or_above_one(self):
        # fused 1.05 - edge 0.04 = 1.01 → >= 1
        result = self.bot.compute_limit_price(1.05, Decimal("0.04"))
        self.assertIsNone(result)

    def test_compute_limit_price_normal_case(self):
        # fused 0.78 - edge 0.05 = 0.73
        result = self.bot.compute_limit_price(0.78, Decimal("0.05"))
        self.assertEqual(result, Decimal("0.73"))

    def test_compute_limit_order_token_qty_rejects_below_five(self):
        # budget=$1, price=$0.50 → 2 tokens (below 5-token minimum)
        result = self.bot.compute_limit_order_token_qty(
            Decimal("1"), Decimal("0.50"), size_precision=6
        )
        self.assertIsNone(result)

    def test_compute_limit_order_token_qty_rounds_down(self):
        # budget=$5.51, price=$0.62, raw = 8.887096...
        # ROUND_DOWN to 6 decimal places → 8.887096
        result = self.bot.compute_limit_order_token_qty(
            Decimal("5.51"), Decimal("0.62"), size_precision=6
        )
        self.assertEqual(result, Decimal("8.887096"))
        # Worst-case spend at the cap: 8.887096 * 0.62 = 5.510999... <= 5.51 budget
        worst_case = result * Decimal("0.62")
        self.assertLessEqual(worst_case, Decimal("5.51"))

    def test_compute_limit_order_token_qty_at_minimum_passes(self):
        # budget=$1, price=$0.20 → exactly 5.0 tokens
        result = self.bot.compute_limit_order_token_qty(
            Decimal("1"), Decimal("0.20"), size_precision=6
        )
        self.assertEqual(result, Decimal("5.000000"))

    def test_compute_limit_order_token_qty_rejects_invalid_budget(self):
        with self.assertRaisesRegex(ValueError, "budget_usd must be positive"):
            self.bot.compute_limit_order_token_qty(
                Decimal("0"), Decimal("0.50"), size_precision=6
            )

    def test_compute_limit_order_token_qty_rejects_invalid_price(self):
        with self.assertRaisesRegex(ValueError, r"limit_price must be in"):
            self.bot.compute_limit_order_token_qty(
                Decimal("10"), Decimal("0"), size_precision=6
            )
        with self.assertRaisesRegex(ValueError, r"limit_price must be in"):
            self.bot.compute_limit_order_token_qty(
                Decimal("10"), Decimal("1"), size_precision=6
            )

    def test_derive_submitted_limit_price_rounds_buy_cap_down(self):
        self.assertEqual(
            self.bot.derive_submitted_limit_price(Decimal("0.626"), 2),
            Decimal("0.62"),
        )

    def test_derive_submitted_limit_price_rejects_unrepresentable_positive_cap(self):
        with self.assertRaisesRegex(RuntimeError, "safe submitted limit price"):
            self.bot.derive_submitted_limit_price(Decimal("0.0001"), 2)

    # --- Timing/price-band helpers ------------------------------------------

    def test_trade_window_label_buckets_seconds_correctly(self):
        cases = [
            (0, "before_06"),
            (359, "before_06"),
            (360, "06_09"),
            (539, "06_09"),
            (540, "09_11"),
            (659, "09_11"),
            (660, "11_13"),
            (779, "11_13"),
            (780, "13_14_current"),
            (812.3, "13_14_current"),
            (839, "13_14_current"),
            (840, "14_15_late"),
            (899, "14_15_late"),
            (900, "after_15"),
            (1200, "after_15"),
        ]
        for secs, expected in cases:
            self.assertEqual(
                self.bot.trade_window_label_for_seconds_into_sub_interval(secs),
                expected,
                msg=f"seconds={secs}",
            )

    def test_trend_price_band_classification(self):
        cases = [
            (0.10, "no_extreme_le_0.30"),
            (0.30, "no_extreme_le_0.30"),
            (0.31, "no_strong_0.30_0.40"),
            (0.40, "no_strong_0.30_0.40"),
            (0.41, "no_moderate_0.40_0.48"),
            (0.48, "no_moderate_0.40_0.48"),
            (0.49, "neutral_0.48_0.52"),
            (0.51, "neutral_0.48_0.52"),
            (0.52, "yes_moderate_0.52_0.60"),
            (0.55, "yes_moderate_0.52_0.60"),
            (0.59, "yes_moderate_0.52_0.60"),
            (0.60, "yes_strong_0.60_0.70"),
            (0.65, "yes_strong_0.60_0.70"),
            (0.69, "yes_strong_0.60_0.70"),
            (0.70, "yes_extreme_ge_0.70"),
            (0.95, "yes_extreme_ge_0.70"),
        ]
        for price, expected in cases:
            self.assertEqual(
                self.bot.trend_price_band_for(price),
                expected,
                msg=f"price={price}",
            )

    # --- SIZING_MODE validation ---------------------------------------------

    def test_sizing_mode_missing_raises(self):
        original = os.environ.get("SIZING_MODE")
        try:
            os.environ.pop("SIZING_MODE", None)
            with self.assertRaisesRegex(RuntimeError, "SIZING_MODE must be set"):
                self.bot.get_sizing_mode_for_live()
        finally:
            if original is not None:
                os.environ["SIZING_MODE"] = original

    def test_sizing_mode_invalid_value_raises(self):
        original = os.environ.get("SIZING_MODE")
        try:
            os.environ["SIZING_MODE"] = "dynamic"
            with self.assertRaisesRegex(RuntimeError, "must be 'fixed' or 'percent'"):
                self.bot.get_sizing_mode_for_live()
        finally:
            if original is None:
                os.environ.pop("SIZING_MODE", None)
            else:
                os.environ["SIZING_MODE"] = original

    def test_sizing_mode_fixed_and_percent_accepted(self):
        original = os.environ.get("SIZING_MODE")
        try:
            for mode in ("fixed", "percent"):
                os.environ["SIZING_MODE"] = mode
                self.assertEqual(self.bot.get_sizing_mode_for_live(), mode)
        finally:
            if original is None:
                os.environ.pop("SIZING_MODE", None)
            else:
                os.environ["SIZING_MODE"] = original

    def test_pct_of_free_collateral_missing_raises(self):
        original = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            with self.assertRaisesRegex(RuntimeError, "must be set when SIZING_MODE=percent"):
                self.bot.get_pct_of_free_collateral_per_trade()
        finally:
            if original is not None:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original

    def test_pct_of_free_collateral_out_of_range(self):
        original = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            for bad in ("0", "1", "-0.1", "1.5", "1.0"):
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = bad
                with self.assertRaisesRegex(RuntimeError, "must be in"):
                    self.bot.get_pct_of_free_collateral_per_trade()
        finally:
            if original is None:
                os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            else:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original

    def test_pct_of_free_collateral_valid(self):
        original = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.05"
            self.assertEqual(
                self.bot.get_pct_of_free_collateral_per_trade(),
                Decimal("0.05"),
            )
        finally:
            if original is None:
                os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            else:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original

    def test_pct_of_free_collateral_rejects_non_finite(self):
        original = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "NaN"
            with self.assertRaisesRegex(RuntimeError, "must be finite"):
                self.bot.get_pct_of_free_collateral_per_trade()
        finally:
            if original is None:
                os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            else:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original

    def test_live_sizing_config_fixed_requires_account_state_env(self):
        os.environ["SIZING_MODE"] = "fixed"
        os.environ["MARKET_BUY_USD"] = "5.51"
        os.environ["MAX_ACCOUNT_STATE_AGE_SECONDS"] = "30"
        os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.25"

        config = self.bot.validate_live_sizing_config()

        self.assertEqual(config["sizing_mode"], "fixed")
        self.assertEqual(config["fixed_market_buy_usd"], Decimal("5.51"))
        self.assertEqual(config["max_account_state_age_seconds"], Decimal("30"))
        self.assertEqual(config["balance_safety_buffer_usd"], Decimal("0.25"))

    def test_live_sizing_config_percent_requires_pct(self):
        original = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            os.environ["SIZING_MODE"] = "percent"
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.05"
            os.environ["MAX_ACCOUNT_STATE_AGE_SECONDS"] = "30"
            os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.00"
            config = self.bot.validate_live_sizing_config()
        finally:
            if original is None:
                os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            else:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original

        self.assertEqual(config["sizing_mode"], "percent")
        self.assertEqual(config["pct_of_free_collateral_per_trade"], Decimal("0.05"))

    def test_percent_strategy_startup_does_not_read_market_buy_usd(self):
        original_values = {
            key: os.environ.get(key)
            for key in (
                "SIZING_MODE",
                "MARKET_BUY_USD",
                "PCT_OF_FREE_COLLATERAL_PER_TRADE",
                "MAX_ACCOUNT_STATE_AGE_SECONDS",
                "BALANCE_SAFETY_BUFFER_USD",
            )
        }
        try:
            os.environ["SIZING_MODE"] = "percent"
            os.environ["MARKET_BUY_USD"] = "not-a-decimal"
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.05"
            os.environ["MAX_ACCOUNT_STATE_AGE_SECONDS"] = "30"
            os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.00"

            strategy = self._track_strategy(
                self.bot.IntegratedBTCStrategy(
                    redis_client=None,
                    enable_grafana=False,
                    simulation_mode=False,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(strategy.live_execution_enabled)

    def test_balance_safety_buffer_rejects_sub_cent_precision(self):
        original = os.environ.get("BALANCE_SAFETY_BUFFER_USD")
        try:
            os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.009"
            with self.assertRaisesRegex(RuntimeError, "whole cents"):
                self.bot.get_balance_safety_buffer_usd_for_live()
            os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.019"
            with self.assertRaisesRegex(RuntimeError, "whole cents"):
                self.bot.get_balance_safety_buffer_usd_for_live()
        finally:
            if original is None:
                os.environ.pop("BALANCE_SAFETY_BUFFER_USD", None)
            else:
                os.environ["BALANCE_SAFETY_BUFFER_USD"] = original

    def test_balance_safety_buffer_accepts_cent_precision(self):
        original = os.environ.get("BALANCE_SAFETY_BUFFER_USD")
        try:
            os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.010"
            self.assertEqual(
                self.bot.get_balance_safety_buffer_usd_for_live(),
                Decimal("0.01"),
            )
        finally:
            if original is None:
                os.environ.pop("BALANCE_SAFETY_BUFFER_USD", None)
            else:
                os.environ["BALANCE_SAFETY_BUFFER_USD"] = original

    def test_nautilus_log_dir_required(self):
        original = os.environ.get("NAUTILUS_LOG_DIR")
        try:
            os.environ.pop("NAUTILUS_LOG_DIR", None)
            with self.assertRaisesRegex(RuntimeError, "NAUTILUS_LOG_DIR"):
                self.bot.get_nautilus_log_dir()
            os.environ["NAUTILUS_LOG_DIR"] = "/tmp/nautilus-explicit"
            self.assertEqual(self.bot.get_nautilus_log_dir(), "/tmp/nautilus-explicit")
        finally:
            if original is None:
                os.environ.pop("NAUTILUS_LOG_DIR", None)
            else:
                os.environ["NAUTILUS_LOG_DIR"] = original

    def test_bot_import_loads_repo_dotenv_explicitly_in_simulation(self):
        self.assertTrue(
            any(
                kwargs.get("dotenv_path") == REPO_ROOT / ".env"
                for _args, kwargs in _DOTENV_CALLS
            )
        )

    def test_legacy_nautilus_integration_env_wiring(self):
        sys.modules["execution"].__path__ = [str(REPO_ROOT / "execution")]
        sys.modules.pop("execution.nautilus_polymarket_integration", None)
        module = importlib.import_module("execution.nautilus_polymarket_integration")
        dotenv_calls = []
        guard_calls = []
        original_load_dotenv = module.load_dotenv
        original_guard = module.refuse_plaintext_env_in_live_mode
        original_log_dir = os.environ.get("NAUTILUS_LOG_DIR")
        try:
            module.load_dotenv = lambda *args, **kwargs: dotenv_calls.append((args, kwargs))
            module.refuse_plaintext_env_in_live_mode = (
                lambda **kwargs: guard_calls.append(kwargs)
            )
            module.PolymarketBTCIntegration(simulation_mode=True)
            live_integration = module.PolymarketBTCIntegration(simulation_mode=False)
            os.environ["NAUTILUS_LOG_DIR"] = "/tmp/nautilus-legacy"
            config = live_integration._create_nautilus_config()
        finally:
            module.load_dotenv = original_load_dotenv
            module.refuse_plaintext_env_in_live_mode = original_guard
            if original_log_dir is None:
                os.environ.pop("NAUTILUS_LOG_DIR", None)
            else:
                os.environ["NAUTILUS_LOG_DIR"] = original_log_dir

        self.assertEqual(dotenv_calls[0][1]["dotenv_path"], REPO_ROOT / ".env")
        self.assertEqual(len(dotenv_calls), 1)
        self.assertEqual(guard_calls[0]["repo_root"], REPO_ROOT)
        self.assertEqual(tuple(guard_calls[0]["argv"]), ("--live",))
        self.assertEqual(
            config.kwargs["logging"].kwargs["log_directory"],
            "/tmp/nautilus-legacy",
        )
        data_config = config.kwargs["data_clients"][self.bot.POLYMARKET]
        exec_config = config.kwargs["exec_clients"][self.bot.POLYMARKET]
        self.assertIn("instrument_config", data_config.kwargs)
        self.assertIn("instrument_config", exec_config.kwargs)
        self.assertNotIn("instrument_provider", data_config.kwargs)
        self.assertNotIn("instrument_provider", exec_config.kwargs)

    def _legacy_live_integration(self):
        sys.modules["execution"].__path__ = [str(REPO_ROOT / "execution")]
        module = importlib.import_module("execution.nautilus_polymarket_integration")
        original_guard = module.refuse_plaintext_env_in_live_mode
        try:
            module.refuse_plaintext_env_in_live_mode = lambda **_kwargs: None
            integration = module.PolymarketBTCIntegration(simulation_mode=False)
        finally:
            module.refuse_plaintext_env_in_live_mode = original_guard
        return module, integration

    def test_legacy_nautilus_live_order_submission_disabled(self):
        _module, integration = self._legacy_live_integration()
        integration.btc_instrument_id = "instrument"

        with self.assertRaisesRegex(
            RuntimeError,
            "Legacy PolymarketBTCIntegration live order submission is disabled",
        ):
            asyncio.run(integration.place_market_order("buy", Decimal("5.51")))

        with self.assertRaisesRegex(
            RuntimeError,
            "Legacy PolymarketBTCIntegration live order submission is disabled",
        ):
            asyncio.run(
                integration.place_limit_order(
                    "buy",
                    Decimal("5.51"),
                    Decimal("0.50"),
                )
            )

    def test_execution_engine_live_mode_disabled(self):
        sys.modules["execution"].__path__ = [str(REPO_ROOT / "execution")]
        sys.modules.pop("execution.execution_engine", None)
        module = importlib.import_module("execution.execution_engine")

        with self.assertRaisesRegex(
            RuntimeError,
            "Legacy ExecutionEngine live mode is disabled",
        ):
            engine = module.ExecutionEngine(dry_run=False)

        engine = module.ExecutionEngine(dry_run=True)
        engine.dry_run = False
        with self.assertRaisesRegex(
            RuntimeError,
            "Legacy ExecutionEngine live mode is disabled",
        ):
            asyncio.run(
                engine.place_market_order(
                    module.OrderSide.BUY,
                    Decimal("5.51"),
                )
            )
        self.assertEqual(engine._orders, {})
        self.assertEqual(engine._order_counter, 0)
        self.assertEqual(engine._total_orders, 0)

    def test_legacy_polymarket_client_live_order_submission_disabled(self):
        sys.modules["execution"].__path__ = [str(REPO_ROOT / "execution")]
        sys.modules.pop("execution.polymarket_client", None)
        module = importlib.import_module("execution.polymarket_client")
        client = module.PolymarketClient(
            private_key="0x" + "1" * 64,
            api_key="api-key",
            api_secret="api-secret",
            api_passphrase="passphrase",
        )
        client.client = object()

        with self.assertRaisesRegex(
            RuntimeError,
            "Legacy PolymarketClient live order submission is disabled",
        ):
            asyncio.run(
                client.place_order(
                    token_id="token",
                    side="buy",
                    size=Decimal("1"),
                    price=Decimal("0.50"),
                )
            )

    def _account_state(
        self,
        free_collateral,
        seconds_old=0,
        *,
        is_reported=True,
        account_issuer="POLYMARKET",
        include_reported=True,
        currency="pUSD",
    ):
        class _Money:
            def as_decimal(self):
                return Decimal(str(free_collateral))

        class _AccountId:
            def get_issuer(self):
                return account_issuer

        class AccountState:
            pass

        event = AccountState()
        if include_reported:
            event.is_reported = is_reported
        event.account_id = _AccountId()
        event.balances = [
            types.SimpleNamespace(currency=currency, free=_Money()),
        ]
        event.ts_event = int(
            (datetime.now(timezone.utc) - timedelta(seconds=seconds_old)).timestamp()
            * 1_000_000_000
        )
        return event

    def test_account_state_updates_free_collateral_cache(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        strategy.on_event(self._account_state("100.25"))

        self.assertEqual(strategy._latest_free_collateral, Decimal("100.25"))
        self.assertEqual(strategy._account_state_sequence, 1)
        self.assertIsNone(strategy._balance_stale_reason)

    def test_account_state_rejects_usdc_currency(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "exactly one pUSD balance"):
            strategy.on_event(self._account_state("100.25", currency="USDC"))

    def test_account_state_rejects_system_calculated_state(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00"))
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            "order-account-state-reported",
        )

        with self.assertRaisesRegex(RuntimeError, "exchange-reported"):
            strategy.on_event(self._account_state("11.00", is_reported=False))

        self.assertEqual(strategy._latest_free_collateral, Decimal("10.00"))
        self.assertEqual(
            strategy._balance_stale_reason,
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
        )

    def test_account_state_rejects_missing_reported_flag(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "missing is_reported"):
            strategy.on_event(self._account_state("10.00", include_reported=False))

    def test_account_state_rejects_non_polymarket_issuer(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "issuer must be POLYMARKET"):
            strategy.on_event(self._account_state("10.00", account_issuer="BINANCE"))

    def test_fixed_sizing_uses_market_buy_usd_and_fresh_balance(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00"))
        rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))

        size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)

        self.assertEqual(size, Decimal("5.51"))
        self.assertEqual(rec.fields["sizing_mode"], "fixed")
        self.assertEqual(rec.fields["free_collateral_at_decision"], Decimal("10.00"))
        self.assertIsNone(rec.fields["rejected_at_gate"])

    def test_percent_sizing_uses_free_collateral(self):
        original_pct = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            os.environ["SIZING_MODE"] = "percent"
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.05"
            os.environ["MAX_POSITION_SIZE"] = "55.00"
            strategy = self._track_strategy(
                self.bot.IntegratedBTCStrategy(
                    redis_client=None,
                    enable_grafana=False,
                    simulation_mode=False,
                )
            )
            strategy.on_event(self._account_state("1000.00"))
            rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
            size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)
        finally:
            if original_pct is None:
                os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            else:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original_pct

        self.assertEqual(size, Decimal("50.00"))
        self.assertEqual(rec.fields["sizing_mode"], "percent")

    def test_percent_sizing_without_account_state_rejects_no_balance(self):
        original_pct = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            os.environ["SIZING_MODE"] = "percent"
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.05"
            strategy = self._track_strategy(
                self.bot.IntegratedBTCStrategy(
                    redis_client=None,
                    enable_grafana=False,
                    simulation_mode=False,
                )
            )
            rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
            size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)
        finally:
            if original_pct is None:
                os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            else:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original_pct

        self.assertIsNone(size)
        self.assertEqual(rec.fields["rejected_at_gate"], "no_balance")

    def test_stale_account_state_rejects(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00", seconds_old=31))
        rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))

        size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)

        self.assertIsNone(size)
        self.assertEqual(rec.fields["rejected_at_gate"], "stale_balance")
        self.assertEqual(rec.fields["balance_stale_reason"], "too_old")

    def test_future_account_state_rejects(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "must not be in the future"):
            strategy.on_event(self._account_state("10.00", seconds_old=-10))

        self.assertIsNone(strategy._latest_free_collateral)
        self.assertEqual(strategy._account_state_sequence, 0)

    def test_future_account_state_does_not_clear_after_order_staleness(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00", seconds_old=1))
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            "order-future-state",
        )

        with self.assertRaisesRegex(RuntimeError, "must not be in the future"):
            strategy.on_event(self._account_state("11.00", seconds_old=-10))

        self.assertEqual(strategy._latest_free_collateral, Decimal("10.00"))
        self.assertEqual(strategy._account_state_sequence, 1)
        self.assertEqual(
            strategy._balance_stale_reason,
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
        )

    def test_balance_marked_stale_after_order_rejects_until_account_state_refresh(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00"))
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            "order-refresh-required",
        )
        stale_rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))

        stale_size = strategy._resolve_position_size_usd(is_simulation=False, rec=stale_rec)

        self.assertIsNone(stale_size)
        self.assertEqual(stale_rec.fields["rejected_at_gate"], "stale_balance_after_order")
        strategy.on_event(self._account_state("10.00"))
        fresh_rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
        fresh_size = strategy._resolve_position_size_usd(is_simulation=False, rec=fresh_rec)
        self.assertEqual(fresh_size, Decimal("5.51"))

    def test_older_account_state_does_not_clear_after_order_staleness(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00", seconds_old=10))
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            "order-older-state",
        )

        with self.assertRaisesRegex(RuntimeError, "newer than the stale-balance"):
            strategy.on_event(self._account_state("11.00", seconds_old=5))

        rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
        size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)
        self.assertIsNone(size)
        self.assertEqual(rec.fields["rejected_at_gate"], "stale_balance_after_order")

    def test_older_account_state_does_not_clear_after_redeem_staleness(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00", seconds_old=10))
        strategy._mark_balance_stale(self.bot.ACCOUNT_BALANCE_STALE_AFTER_REDEEM)

        with self.assertRaisesRegex(RuntimeError, "newer than the stale-balance"):
            strategy.on_event(self._account_state("11.00", seconds_old=5))

        rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
        size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)
        self.assertIsNone(size)
        self.assertEqual(rec.fields["rejected_at_gate"], "stale_balance_after_redeem")

    def test_account_state_timestamp_must_increase(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.on_event(self._account_state("10.00", seconds_old=5))

        with self.assertRaisesRegex(RuntimeError, "increase monotonically"):
            strategy.on_event(self._account_state("11.00", seconds_old=10))

    def test_percent_sizing_above_max_position_rejects_not_clamps(self):
        original_pct = os.environ.get("PCT_OF_FREE_COLLATERAL_PER_TRADE")
        try:
            os.environ["SIZING_MODE"] = "percent"
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.10"
            os.environ["MAX_POSITION_SIZE"] = "55.00"
            strategy = self._track_strategy(
                self.bot.IntegratedBTCStrategy(
                    redis_client=None,
                    enable_grafana=False,
                    simulation_mode=False,
                )
            )
            strategy.on_event(self._account_state("1000.00"))
            rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
            size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)
        finally:
            if original_pct is None:
                os.environ.pop("PCT_OF_FREE_COLLATERAL_PER_TRADE", None)
            else:
                os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = original_pct

        self.assertIsNone(size)
        self.assertEqual(rec.fields["rejected_at_gate"], "size_exceeds_max_position_size")

    def test_place_real_order_percent_rejects_size_mismatch(self):
        original_values = {
            key: os.environ.get(key)
            for key in ("SIZING_MODE", "PCT_OF_FREE_COLLATERAL_PER_TRADE")
        }
        try:
            os.environ["SIZING_MODE"] = "percent"
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.05"
            strategy = self._track_strategy(
                self.bot.IntegratedBTCStrategy(
                    redis_client=None,
                    enable_grafana=False,
                    simulation_mode=False,
                )
            )
            strategy.instrument_id = "dummy-instrument-id"
            strategy.on_event(self._account_state("1000.00"))

            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("49.00"),
                    current_price=Decimal("0.62"),
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})

    # --- ORDER_TYPE validation ----------------------------------------------

    def test_order_type_missing_raises(self):
        original = os.environ.get("ORDER_TYPE")
        try:
            os.environ.pop("ORDER_TYPE", None)
            with self.assertRaisesRegex(RuntimeError, "ORDER_TYPE must be set"):
                self.bot.get_order_type_for_live()
        finally:
            if original is not None:
                os.environ["ORDER_TYPE"] = original

    def test_order_type_invalid_raises(self):
        original = os.environ.get("ORDER_TYPE")
        try:
            os.environ["ORDER_TYPE"] = "GTC"
            with self.assertRaisesRegex(RuntimeError, "must be 'market_ioc' or 'limit_ioc'"):
                self.bot.get_order_type_for_live()
        finally:
            if original is None:
                os.environ.pop("ORDER_TYPE", None)
            else:
                os.environ["ORDER_TYPE"] = original

    def test_order_type_market_ioc_and_limit_ioc_accepted(self):
        original = os.environ.get("ORDER_TYPE")
        try:
            for value in ("market_ioc", "limit_ioc"):
                os.environ["ORDER_TYPE"] = value
                self.assertEqual(self.bot.get_order_type_for_live(), value)
        finally:
            if original is None:
                os.environ.pop("ORDER_TYPE", None)
            else:
                os.environ["ORDER_TYPE"] = original

    def test_quote_stability_required_missing_raises(self):
        original = os.environ.get("QUOTE_STABILITY_REQUIRED")
        try:
            os.environ.pop("QUOTE_STABILITY_REQUIRED", None)
            with self.assertRaisesRegex(RuntimeError, "QUOTE_STABILITY_REQUIRED must be set"):
                self.bot.get_quote_stability_required_for_live()
        finally:
            if original is not None:
                os.environ["QUOTE_STABILITY_REQUIRED"] = original

    def test_quote_stability_required_invalid_values_raise(self):
        original = os.environ.get("QUOTE_STABILITY_REQUIRED")
        try:
            for bad in ("0", "-1", "three"):
                os.environ["QUOTE_STABILITY_REQUIRED"] = bad
                with self.assertRaisesRegex(RuntimeError, "QUOTE_STABILITY_REQUIRED"):
                    self.bot.get_quote_stability_required_for_live()
        finally:
            if original is None:
                os.environ.pop("QUOTE_STABILITY_REQUIRED", None)
            else:
                os.environ["QUOTE_STABILITY_REQUIRED"] = original

    def test_quote_stability_required_valid(self):
        original = os.environ.get("QUOTE_STABILITY_REQUIRED")
        try:
            os.environ["QUOTE_STABILITY_REQUIRED"] = "4"
            self.assertEqual(self.bot.get_quote_stability_required_for_live(), 4)
        finally:
            if original is None:
                os.environ.pop("QUOTE_STABILITY_REQUIRED", None)
            else:
                os.environ["QUOTE_STABILITY_REQUIRED"] = original

    def test_quote_stability_gate_supports_configured_thresholds_and_reset(self):
        class _Price:
            def __init__(self, value):
                self.value = Decimal(value)

            def as_decimal(self):
                return self.value

        class _Tick:
            instrument_id = "yes-instrument"
            bid_price = _Price("0.60")
            ask_price = _Price("0.62")

        original = os.environ.get("QUOTE_STABILITY_REQUIRED")
        try:
            for threshold in (1, 2, 3, 4):
                os.environ["QUOTE_STABILITY_REQUIRED"] = str(threshold)
                strategy = self._track_strategy(
                    self.bot.IntegratedBTCStrategy(
                        redis_client=None,
                        enable_grafana=False,
                        simulation_mode=True,
                    )
                )
                strategy.instrument_id = "yes-instrument"
                for _ in range(threshold - 1):
                    strategy.on_quote_tick(_Tick())
                    self.assertFalse(strategy._market_stable)
                strategy.on_quote_tick(_Tick())
                self.assertTrue(strategy._market_stable)
                self.assertEqual(strategy._stable_tick_count, threshold)

                strategy._reset_stability("unit test")
                self.assertFalse(strategy._market_stable)
                self.assertEqual(strategy._stable_tick_count, 0)
                for _ in range(threshold):
                    strategy.on_quote_tick(_Tick())
                self.assertTrue(strategy._market_stable)
                self.assertEqual(strategy._stable_tick_count, threshold)
                strategy._release_live_trade_ledger_lock()
                self._strategies.remove(strategy)
        finally:
            if original is None:
                os.environ.pop("QUOTE_STABILITY_REQUIRED", None)
            else:
                os.environ["QUOTE_STABILITY_REQUIRED"] = original

    def test_limit_ioc_fill_policy_required_for_limit_ioc(self):
        original = os.environ.get("LIMIT_IOC_FILL_POLICY")
        try:
            os.environ.pop("LIMIT_IOC_FILL_POLICY", None)
            with self.assertRaisesRegex(RuntimeError, "LIMIT_IOC_FILL_POLICY must be set"):
                self.bot.get_limit_ioc_fill_policy_for_live(self.bot.ORDER_TYPE_LIMIT_IOC)
        finally:
            if original is not None:
                os.environ["LIMIT_IOC_FILL_POLICY"] = original

    def test_limit_ioc_fill_policy_partial_ok_accepted(self):
        original = os.environ.get("LIMIT_IOC_FILL_POLICY")
        try:
            os.environ["LIMIT_IOC_FILL_POLICY"] = "partial_ok"
            self.assertEqual(
                self.bot.get_limit_ioc_fill_policy_for_live(self.bot.ORDER_TYPE_LIMIT_IOC),
                "partial_ok",
            )
        finally:
            if original is None:
                os.environ.pop("LIMIT_IOC_FILL_POLICY", None)
            else:
                os.environ["LIMIT_IOC_FILL_POLICY"] = original

    def test_limit_ioc_fill_policy_all_or_nothing_blocks_fak_path(self):
        original = os.environ.get("LIMIT_IOC_FILL_POLICY")
        try:
            os.environ["LIMIT_IOC_FILL_POLICY"] = "all_or_nothing"
            with self.assertRaisesRegex(RuntimeError, "requires verified FOK"):
                self.bot.get_limit_ioc_fill_policy_for_live(self.bot.ORDER_TYPE_LIMIT_IOC)
        finally:
            if original is None:
                os.environ.pop("LIMIT_IOC_FILL_POLICY", None)
            else:
                os.environ["LIMIT_IOC_FILL_POLICY"] = original

    def test_limit_required_edge_missing_raises(self):
        original = os.environ.get("LIMIT_REQUIRED_EDGE")
        try:
            os.environ.pop("LIMIT_REQUIRED_EDGE", None)
            with self.assertRaisesRegex(RuntimeError, "LIMIT_REQUIRED_EDGE must be set"):
                self.bot.get_validated_limit_required_edge()
        finally:
            if original is not None:
                os.environ["LIMIT_REQUIRED_EDGE"] = original

    def test_limit_required_edge_out_of_range(self):
        original = os.environ.get("LIMIT_REQUIRED_EDGE")
        try:
            for bad in ("0", "1", "-0.05", "1.5"):
                os.environ["LIMIT_REQUIRED_EDGE"] = bad
                with self.assertRaisesRegex(RuntimeError, "must be in"):
                    self.bot.get_validated_limit_required_edge()
        finally:
            if original is None:
                os.environ.pop("LIMIT_REQUIRED_EDGE", None)
            else:
                os.environ["LIMIT_REQUIRED_EDGE"] = original

    def test_limit_required_edge_valid(self):
        original = os.environ.get("LIMIT_REQUIRED_EDGE")
        try:
            os.environ["LIMIT_REQUIRED_EDGE"] = "0.05"
            self.assertEqual(
                self.bot.get_validated_limit_required_edge(),
                Decimal("0.05"),
            )
        finally:
            if original is None:
                os.environ.pop("LIMIT_REQUIRED_EDGE", None)
            else:
                os.environ["LIMIT_REQUIRED_EDGE"] = original

    def test_limit_required_edge_must_cover_ev_buffers(self):
        original_values = {
            key: os.environ.get(key)
            for key in (
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "LIMIT_IOC_FILL_POLICY",
                "LIMIT_REQUIRED_EDGE",
                "EV_FEE_BUFFER",
                "EV_SPREAD_BUFFER",
            )
        }
        try:
            os.environ["ORDER_TYPE"] = "limit_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["LIMIT_IOC_FILL_POLICY"] = "partial_ok"
            os.environ["LIMIT_REQUIRED_EDGE"] = "0.01"
            os.environ["EV_FEE_BUFFER"] = "0.005"
            os.environ["EV_SPREAD_BUFFER"] = "0.01"

            with self.assertRaisesRegex(
                RuntimeError,
                "LIMIT_REQUIRED_EDGE must be >= EV_FEE_BUFFER \\+ EV_SPREAD_BUFFER",
            ):
                self.bot.validate_live_order_config()

            os.environ["LIMIT_REQUIRED_EDGE"] = "0.015"
            config = self.bot.validate_live_order_config()
            self.assertEqual(config["limit_required_edge"], Decimal("0.015"))
            self.assertEqual(config["ev_fee_buffer"], Decimal("0.005"))
            self.assertEqual(config["ev_spread_buffer"], Decimal("0.01"))
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_place_real_order_rejects_position_size_mismatch_with_gate(self):
        # Reviewer #2 cycle-2 finding: caller-supplied position_size MUST match
        # the gate-validated amount to prevent rounding-mode divergence between
        # ROUND_HALF_EVEN (legacy sizing path) and ROUND_DOWN (gate quantize).
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.instrument_id = "dummy-instrument-id"

        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            # Caller passes a mismatched position_size (e.g. 5.52 from
            # ROUND_HALF_EVEN quantization while gate read 5.51).
            result = asyncio.run(
                strategy._place_real_order(
                    signal=None,
                    position_size=Decimal("5.52"),
                    current_price=0.5,
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                )
            )
            self.assertFalse(result)
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_place_real_order_runtime_gate_allows_valid_market_buy_usd_proceeds(self):
        # Companion to the rejection test: with a valid MARKET_BUY_USD the
        # runtime gate does NOT short-circuit; the order then fails at a later
        # check (no instrument cache, etc.) — what matters is the gate was
        # not the rejection cause.
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy.instrument_id = "dummy-instrument-id"
        strategy._stable_tick_count = 3

        original = os.environ.get("MARKET_BUY_USD")
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            strategy.on_event(self._account_state("10.00"))
            # Returns False because of later checks (NO token absent for short,
            # etc.), but if it raised an unexpected exception that would be a
            # signal the gate failed unexpectedly.
            result = asyncio.run(
                strategy._place_real_order(
                    signal=None,
                    position_size=Decimal("5.51"),
                    current_price=0.5,
                    direction="short",  # forces the no_id is None branch
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                )
            )
            self.assertFalse(result)
        finally:
            if original is None:
                os.environ.pop("MARKET_BUY_USD", None)
            else:
                os.environ["MARKET_BUY_USD"] = original

    def test_place_real_order_limit_ioc_uses_limit_order_factory(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        condition_id = "conditionlivelimit"
        yes_token_id = "yestoken"
        no_token_id = "notoken"
        yes_instrument_id = f"{condition_id}-{yes_token_id}.POLYMARKET"
        strategy.instrument_id = yes_instrument_id
        strategy._yes_instrument_id = yes_instrument_id
        strategy._stable_tick_count = 3
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))
        strategy.risk_engine.validate_new_position = lambda **_kwargs: (False, "risk blocked")
        submitted = {}

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, instrument_id):
                submitted["cache_instrument_id"] = instrument_id
                return _Instrument()

        class _OrderFactory:
            def limit(self, **kwargs):
                submitted["limit_kwargs"] = kwargs
                return {"kind": "limit", **kwargs}

        strategy.cache = _Cache()
        strategy.order_factory = _OrderFactory()
        strategy.submit_order = lambda order: submitted.setdefault("order", order)
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-live-limit",
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "LIMIT_REQUIRED_EDGE",
                "LIMIT_IOC_FILL_POLICY",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "limit_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["LIMIT_REQUIRED_EDGE"] = "0.05"
            os.environ["LIMIT_IOC_FILL_POLICY"] = "partial_ok"
            strategy.on_event(self._account_state("10.00"))
            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("5.51"),
                    current_price=Decimal("0.62"),
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_LIMIT_IOC,
                    accepted_limit_price=Decimal("0.62"),
                    submitted_limit_price=Decimal("0.62"),
                    limit_order_token_qty=Decimal("8.887096"),
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(result)
        kwargs = submitted["limit_kwargs"]
        self.assertEqual(kwargs["instrument_id"], yes_instrument_id)
        self.assertFalse(kwargs["quote_quantity"])
        self.assertEqual(kwargs["time_in_force"], self.bot.TimeInForce.IOC)
        intent = next(iter(strategy._submitted_order_intents.values()))
        self.assertEqual(intent["order_type"], "limit_ioc")
        self.assertFalse(intent["quote_quantity"])
        self.assertEqual(intent["quantity_mode"], "base_quantity")
        self.assertEqual(intent["submitted_limit_price"], Decimal("0.62"))

    def test_place_real_order_pre_intent_exception_does_not_block_live_settlement(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        condition_id = "conditionpreintent"
        yes_token_id = "yestoken"
        yes_instrument_id = f"{condition_id}-{yes_token_id}.POLYMARKET"
        strategy.instrument_id = yes_instrument_id
        strategy._yes_instrument_id = yes_instrument_id
        strategy._stable_tick_count = 3
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        class _OrderFactory:
            def market(self, **_kwargs):
                raise AssertionError("market order factory should not be called")

        def _raise_metadata_error():
            raise RuntimeError("metadata exploded")

        strategy.cache = _Cache()
        strategy.order_factory = _OrderFactory()
        strategy._current_market_metadata = _raise_metadata_error

        original_values = {
            key: os.environ.get(key)
            for key in ("MARKET_BUY_USD", "ORDER_TYPE", "QUOTE_STABILITY_REQUIRED")
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            strategy.on_event(self._account_state("10.00"))
            with self.assertRaisesRegex(RuntimeError, "metadata exploded"):
                asyncio.run(
                    strategy._place_real_order(
                        signal=types.SimpleNamespace(score=77, confidence=0.67),
                        position_size=Decimal("5.51"),
                        current_price=Decimal("0.62"),
                        direction="long",
                        order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                    )
                )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(strategy._submitted_order_intents, {})
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)
        self.assertIsNone(strategy._balance_stale_reason)

    def test_place_real_order_submit_exception_blocks_persisted_intent(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        condition_id = "conditionsubmitraises"
        yes_token_id = "yestoken"
        no_token_id = "notoken"
        yes_instrument_id = f"{condition_id}-{yes_token_id}.POLYMARKET"
        strategy.instrument_id = yes_instrument_id
        strategy._yes_instrument_id = yes_instrument_id
        strategy._stable_tick_count = 3
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        class _OrderFactory:
            def market(self, **kwargs):
                return {"kind": "market", **kwargs}

        def _raise_submit_error(_order):
            raise RuntimeError("submit exploded")

        strategy.cache = _Cache()
        strategy.order_factory = _OrderFactory()
        strategy.submit_order = _raise_submit_error
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-live-submit-raises",
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in ("MARKET_BUY_USD", "ORDER_TYPE", "QUOTE_STABILITY_REQUIRED")
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            strategy.on_event(self._account_state("10.00"))
            with self.assertRaisesRegex(RuntimeError, "submit exploded"):
                asyncio.run(
                    strategy._place_real_order(
                        signal=types.SimpleNamespace(score=77, confidence=0.67),
                        position_size=Decimal("5.51"),
                        current_price=Decimal("0.62"),
                        direction="long",
                        order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                    )
                )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(strategy._submitted_order_intents)
        self.assertEqual(
            strategy._balance_stale_reason,
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
        )
        self.assertIn("submit_order raised", strategy._settlement_ledger_blocked_reason)

    def test_place_real_order_rejects_yes_instrument_token_mismatch(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        condition_id = "conditionliveyesmismatch"
        strategy.instrument_id = f"{condition_id}-wrongyestoken.POLYMARKET"
        strategy._yes_instrument_id = strategy.instrument_id
        strategy._stable_tick_count = 3
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        class _OrderFactory:
            def market(self, **_kwargs):
                raise AssertionError("market order factory should not be called")

        strategy.cache = _Cache()
        strategy.order_factory = _OrderFactory()
        strategy.submit_order = lambda _order: None
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-live-yes-mismatch",
            "condition_id": condition_id,
            "yes_token_id": "yestoken",
            "no_token_id": "notoken",
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in ("MARKET_BUY_USD", "ORDER_TYPE", "QUOTE_STABILITY_REQUIRED")
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            strategy.on_event(self._account_state("10.00"))
            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("5.51"),
                    current_price=Decimal("0.62"),
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_place_real_order_rejects_no_instrument_token_mismatch(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        condition_id = "conditionlivenomismatch"
        strategy.instrument_id = f"{condition_id}-yestoken.POLYMARKET"
        strategy._yes_instrument_id = strategy.instrument_id
        strategy._no_instrument_id = f"{condition_id}-wrongnotoken.POLYMARKET"
        strategy._stable_tick_count = 3
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))
        strategy._last_no_bid_ask = (Decimal("0.38"), Decimal("0.40"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        class _OrderFactory:
            def market(self, **_kwargs):
                raise AssertionError("market order factory should not be called")

        strategy.cache = _Cache()
        strategy.order_factory = _OrderFactory()
        strategy.submit_order = lambda _order: None
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-live-no-mismatch",
            "condition_id": condition_id,
            "yes_token_id": "yestoken",
            "no_token_id": "notoken",
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in ("MARKET_BUY_USD", "ORDER_TYPE", "QUOTE_STABILITY_REQUIRED")
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            strategy.on_event(self._account_state("10.00"))
            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("5.51"),
                    current_price=Decimal("0.62"),
                    direction="short",
                    order_type=self.bot.ORDER_TYPE_MARKET_IOC,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_place_real_order_limit_ioc_rejects_price_above_accepted_cap(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        condition_id = "conditionlivelimitmismatch"
        yes_token_id = "yestoken"
        no_token_id = "notoken"
        yes_instrument_id = f"{condition_id}-{yes_token_id}.POLYMARKET"
        strategy.instrument_id = yes_instrument_id
        strategy._yes_instrument_id = yes_instrument_id
        strategy._stable_tick_count = 3
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        class _OrderFactory:
            def limit(self, **_kwargs):
                raise AssertionError("limit order factory should not be called")

        strategy.cache = _Cache()
        strategy.order_factory = _OrderFactory()
        strategy.submit_order = lambda _order: None
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-live-limit-mismatch",
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "LIMIT_REQUIRED_EDGE",
                "LIMIT_IOC_FILL_POLICY",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "limit_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["LIMIT_REQUIRED_EDGE"] = "0.05"
            os.environ["LIMIT_IOC_FILL_POLICY"] = "partial_ok"
            strategy.on_event(self._account_state("10.00"))
            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("5.51"),
                    current_price=Decimal("0.62"),
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_LIMIT_IOC,
                    accepted_limit_price=Decimal("0.62"),
                    submitted_limit_price=Decimal("0.63"),
                    limit_order_token_qty=Decimal("8.887096"),
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_place_real_order_limit_ioc_rejects_worst_case_notional_above_budget(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        condition_id = "conditionlivelimitbudget"
        yes_token_id = "yestoken"
        no_token_id = "notoken"
        yes_instrument_id = f"{condition_id}-{yes_token_id}.POLYMARKET"
        strategy.instrument_id = yes_instrument_id
        strategy._yes_instrument_id = yes_instrument_id
        strategy._stable_tick_count = 3
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        class _OrderFactory:
            def limit(self, **_kwargs):
                raise AssertionError("limit order factory should not be called")

        strategy.cache = _Cache()
        strategy.order_factory = _OrderFactory()
        strategy.submit_order = lambda _order: None
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-live-limit-budget",
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "LIMIT_REQUIRED_EDGE",
                "LIMIT_IOC_FILL_POLICY",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "limit_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["LIMIT_REQUIRED_EDGE"] = "0.05"
            os.environ["LIMIT_IOC_FILL_POLICY"] = "partial_ok"
            strategy.on_event(self._account_state("10.00"))
            result = asyncio.run(
                strategy._place_real_order(
                    signal=types.SimpleNamespace(score=77, confidence=0.67),
                    position_size=Decimal("5.51"),
                    current_price=Decimal("0.62"),
                    direction="long",
                    order_type=self.bot.ORDER_TYPE_LIMIT_IOC,
                    accepted_limit_price=Decimal("0.62"),
                    submitted_limit_price=Decimal("0.62"),
                    limit_order_token_qty=Decimal("8.887097"),
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertFalse(result)
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_decision_path_limit_ioc_derives_cap_and_quantity_from_config(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.70")] * 20
        strategy.instrument_id = "yes-instrument"
        strategy._yes_instrument_id = "yes-instrument"
        strategy._yes_token_id = "yes-token"
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        strategy.cache = _Cache()
        fused = types.SimpleNamespace(
            source="Fusion",
            direction=types.SimpleNamespace(value="bullish"),
            score=77,
            confidence=0.67,
        )
        strategy._process_signals = lambda _current_price, _metadata: [fused]
        strategy.fusion_engine = types.SimpleNamespace(
            fuse_signals=lambda _signals, min_signals, min_score: fused
        )

        async def _market_context(_current_price):
            return {
                "deviation": 0.0,
                "momentum": 0.0,
                "volatility": 0.0,
                "tick_buffer": [],
                "yes_token_id": "yes-token",
                "yes_order_book": {
                    "bids": [],
                    "asks": [{"price": "0.62", "size": "20"}],
                },
            }

        strategy._fetch_market_context = _market_context
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-decision-limit",
            "condition_id": "condition-decision-limit",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }
        captured = {}

        async def _capture_place(signal, position_size, current_price, direction, **kwargs):
            captured["signal"] = signal
            captured["position_size"] = position_size
            captured["current_price"] = current_price
            captured["direction"] = direction
            captured.update(kwargs)
            return True

        strategy._place_real_order = _capture_place

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "LIMIT_REQUIRED_EDGE",
                "LIMIT_IOC_FILL_POLICY",
                "MIN_SIGNAL_CONFIDENCE",
                "EV_FEE_BUFFER",
                "EV_SPREAD_BUFFER",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "limit_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["LIMIT_REQUIRED_EDGE"] = "0.05"
            os.environ["LIMIT_IOC_FILL_POLICY"] = "partial_ok"
            os.environ["MIN_SIGNAL_CONFIDENCE"] = "0.60"
            os.environ["EV_FEE_BUFFER"] = "0.005"
            os.environ["EV_SPREAD_BUFFER"] = "0.01"
            strategy.on_event(self._account_state("10.00"))
            rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
            result = asyncio.run(
                strategy._make_trading_decision_body(
                    Decimal("0.70"),
                    trade_key=("unit", 1),
                    is_simulation=False,
                    rec=rec,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(result)
        self.assertEqual(captured["order_type"], self.bot.ORDER_TYPE_LIMIT_IOC)
        self.assertEqual(captured["accepted_limit_price"], Decimal("0.62"))
        self.assertEqual(captured["submitted_limit_price"], Decimal("0.62"))
        self.assertEqual(captured["limit_order_token_qty"], Decimal("8.887096"))
        self.assertEqual(captured["direction"], "long")
        self.assertEqual(rec.fields["submitted_limit_price"], "0.62")
        self.assertEqual(rec.fields["limit_order_token_qty"], "8.887096")
        self.assertEqual(rec.fields["estimated_tokens_filled"], Decimal("8.887096"))
        self.assertEqual(rec.fields["estimated_actual_cost"], Decimal("5.50999952"))
        self.assertTrue(rec.fields["depth_fully_filled"])

    def test_decision_path_fails_closed_when_depth_actual_cost_is_missing(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.70")] * 20
        strategy.instrument_id = "yes-instrument"
        strategy._yes_instrument_id = "yes-instrument"
        strategy._yes_token_id = "yes-token"
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        class _Instrument:
            size_precision = 6
            price_precision = 2
            info = {}

        class _Cache:
            def instrument(self, _instrument_id):
                return _Instrument()

        strategy.cache = _Cache()
        fused = types.SimpleNamespace(
            source="Fusion",
            direction=types.SimpleNamespace(value="bullish"),
            score=77,
            confidence=0.67,
        )
        strategy._process_signals = lambda _current_price, _metadata: [fused]
        strategy.fusion_engine = types.SimpleNamespace(
            fuse_signals=lambda _signals, min_signals, min_score: fused
        )

        async def _market_context(_current_price):
            return {
                "deviation": 0.0,
                "momentum": 0.0,
                "volatility": 0.0,
                "tick_buffer": [],
                "yes_token_id": "yes-token",
                "yes_order_book": {
                    "bids": [],
                    "asks": [{"price": "0.62", "size": "20"}],
                },
            }

        async def _bad_depth_entry(**_kwargs):
            return self.bot.DepthAwareEntry(
                executable_entry=Decimal("0.62"),
                tokens_filled=Decimal("8.887096"),
                actual_cost=None,
                fully_filled=True,
            )

        async def _unexpected_place(*_args, **_kwargs):
            raise AssertionError("order placement must not run with missing actual_cost")

        strategy._fetch_market_context = _market_context
        strategy._compute_depth_aware_entry_details = _bad_depth_entry
        strategy._place_real_order = _unexpected_place
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-decision-missing-cost",
            "condition_id": "condition-decision-missing-cost",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "MIN_SIGNAL_CONFIDENCE",
                "EV_FEE_BUFFER",
                "EV_SPREAD_BUFFER",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["MIN_SIGNAL_CONFIDENCE"] = "0.60"
            os.environ["EV_FEE_BUFFER"] = "0.005"
            os.environ["EV_SPREAD_BUFFER"] = "0.01"
            strategy.on_event(self._account_state("10.00"))
            rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
            with self.assertRaisesRegex(RuntimeError, "actual_cost must be explicit"):
                asyncio.run(
                    strategy._make_trading_decision_body(
                        Decimal("0.70"),
                        trade_key=("unit", 1),
                        is_simulation=False,
                        rec=rec,
                    )
                )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertIsNone(rec.fields["estimated_actual_cost"])

    def test_shadow_policy_positive_decision_does_not_execute_or_write_paper_trade(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.70")] * 20
        strategy.instrument_id = "yes-instrument"
        strategy._yes_instrument_id = "yes-instrument"
        strategy._yes_token_id = "yes-token"
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        fused = types.SimpleNamespace(
            source="Fusion",
            direction=types.SimpleNamespace(value="bullish"),
            score=77,
            confidence=0.67,
        )
        strategy._process_signals = lambda _current_price, _metadata: [fused]
        strategy.fusion_engine = types.SimpleNamespace(
            fuse_signals=lambda _signals, min_signals, min_score: fused
        )

        async def _market_context(_current_price):
            return {
                "deviation": 0.0,
                "momentum": 0.0,
                "volatility": 0.0,
                "tick_buffer": [],
                "yes_token_id": "yes-token",
                "yes_order_book": {
                    "bids": [],
                    "asks": [{"price": "0.62", "size": "20"}],
                },
            }

        async def _unexpected_place(*_args, **_kwargs):
            raise AssertionError("shadow policy must not place live orders")

        async def _unexpected_paper(*_args, **_kwargs):
            raise AssertionError("shadow policy must not write paper trades")

        strategy._fetch_market_context = _market_context
        strategy._place_real_order = _unexpected_place
        strategy._record_paper_trade = _unexpected_paper
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-shadow-policy",
            "condition_id": "condition-shadow-policy",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "MIN_SIGNAL_CONFIDENCE",
                "EV_FEE_BUFFER",
                "EV_SPREAD_BUFFER",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["MIN_SIGNAL_CONFIDENCE"] = "0.60"
            os.environ["EV_FEE_BUFFER"] = "0.005"
            os.environ["EV_SPREAD_BUFFER"] = "0.01"
            strategy.on_event(self._account_state("10.00"))
            rec = self.bot.DecisionRecord(
                current_price=Decimal("0.70"),
                strategy_observation_mode="shadow_policy",
            )
            result = asyncio.run(
                strategy._make_trading_decision_body(
                    Decimal("0.70"),
                    trade_key=None,
                    is_simulation=False,
                    rec=rec,
                    observation_only=True,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(result)
        self.assertEqual(rec.fields["strategy_observation_mode"], "shadow_policy")
        self.assertEqual(rec.fields["decided_direction"], "long")
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_shadow_policy_rejects_below_quote_stability_threshold(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy._stable_tick_count = 2

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "MIN_SIGNAL_CONFIDENCE",
                "EV_FEE_BUFFER",
                "EV_SPREAD_BUFFER",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["MIN_SIGNAL_CONFIDENCE"] = "0.60"
            os.environ["EV_FEE_BUFFER"] = "0.005"
            os.environ["EV_SPREAD_BUFFER"] = "0.01"
            rec = self.bot.DecisionRecord(
                current_price=Decimal("0.70"),
                strategy_observation_mode="shadow_policy",
            )
            result = asyncio.run(
                strategy._make_trading_decision_body(
                    Decimal("0.70"),
                    trade_key=None,
                    is_simulation=False,
                    rec=rec,
                    observation_only=True,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertFalse(result)
        self.assertEqual(
            rec.fields["rejected_at_gate"],
            "quote_stability_below_configured_threshold",
        )
        self.assertEqual(strategy._submitted_order_intents, {})

    def test_shadow_policy_uses_live_percent_sizing(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        strategy._stable_tick_count = 3
        strategy.price_history = [Decimal("0.70")] * 20
        strategy.instrument_id = "yes-instrument"
        strategy._yes_instrument_id = "yes-instrument"
        strategy._yes_token_id = "yes-token"
        strategy._last_bid_ask = (Decimal("0.60"), Decimal("0.62"))

        fused = types.SimpleNamespace(
            source="Fusion",
            direction=types.SimpleNamespace(value="bullish"),
            score=77,
            confidence=0.67,
        )
        strategy._process_signals = lambda _current_price, _metadata: [fused]
        strategy.fusion_engine = types.SimpleNamespace(
            fuse_signals=lambda _signals, min_signals, min_score: fused
        )

        async def _market_context(_current_price):
            return {
                "deviation": 0.0,
                "momentum": 0.0,
                "volatility": 0.0,
                "tick_buffer": [],
                "yes_token_id": "yes-token",
                "yes_order_book": {
                    "bids": [],
                    "asks": [{"price": "0.62", "size": "20"}],
                },
            }

        async def _unexpected_place(*_args, **_kwargs):
            raise AssertionError("shadow policy must not place live orders")

        strategy._fetch_market_context = _market_context
        strategy._place_real_order = _unexpected_place
        now = datetime.now(timezone.utc)
        strategy._current_market_metadata = lambda: {
            "slug": "slug-shadow-policy-percent",
            "condition_id": "condition-shadow-policy-percent",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "start_time": (now - timedelta(minutes=15)).isoformat(),
            "end_time": (now + timedelta(minutes=15)).isoformat(),
        }

        original_values = {
            key: os.environ.get(key)
            for key in (
                "MARKET_BUY_USD",
                "ORDER_TYPE",
                "QUOTE_STABILITY_REQUIRED",
                "MIN_SIGNAL_CONFIDENCE",
                "EV_FEE_BUFFER",
                "EV_SPREAD_BUFFER",
                "SIZING_MODE",
                "PCT_OF_FREE_COLLATERAL_PER_TRADE",
                "MAX_POSITION_SIZE",
            )
        }
        try:
            os.environ["MARKET_BUY_USD"] = "5.51"
            os.environ["ORDER_TYPE"] = "market_ioc"
            os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
            os.environ["MIN_SIGNAL_CONFIDENCE"] = "0.60"
            os.environ["EV_FEE_BUFFER"] = "0.005"
            os.environ["EV_SPREAD_BUFFER"] = "0.01"
            os.environ["SIZING_MODE"] = "percent"
            os.environ["PCT_OF_FREE_COLLATERAL_PER_TRADE"] = "0.05"
            os.environ["MAX_POSITION_SIZE"] = "55.00"
            strategy.risk_engine.limits.max_position_size = Decimal("55.00")
            strategy.on_event(self._account_state("200.00"))
            rec = self.bot.DecisionRecord(
                current_price=Decimal("0.70"),
                strategy_observation_mode="shadow_policy",
            )
            result = asyncio.run(
                strategy._make_trading_decision_body(
                    Decimal("0.70"),
                    trade_key=None,
                    is_simulation=False,
                    rec=rec,
                    observation_only=True,
                )
            )
        finally:
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(result)
        self.assertEqual(rec.fields["sizing_mode"], "percent")
        self.assertEqual(rec.fields["resolved_trade_usd"], Decimal("10.00"))
        self.assertEqual(rec.fields["strategy_observation_mode"], "shadow_policy")
        self.assertEqual(strategy._submitted_order_intents, {})
        self.assertEqual(len(strategy.risk_engine._positions), 0)

    def test_shadow_policy_sync_does_not_clear_live_decision_flag(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        calls = []

        async def _record_call(*args, **kwargs):
            calls.append((args, kwargs))
            return False

        strategy._decision_in_progress = True
        strategy._make_trading_decision = _record_call

        strategy._make_trading_decision_sync(
            0.70,
            trade_key=None,
            strategy_observation_mode="shadow_policy",
        )

        self.assertTrue(strategy._decision_in_progress)
        self.assertEqual(calls[0][1]["strategy_observation_mode"], "shadow_policy")

    def test_shadow_policy_wrapper_bypasses_redis_mode_check(self):
        strategy = self._track_strategy(
            self.bot.IntegratedBTCStrategy(
                redis_client=None,
                enable_grafana=False,
                simulation_mode=False,
            )
        )
        calls = []

        async def _record_body(current_price, trade_key, is_simulation, rec, observation_only=False):
            calls.append((current_price, trade_key, is_simulation, rec, observation_only))
            return True

        strategy._make_trading_decision_body = _record_body

        result = asyncio.run(
            strategy._make_trading_decision(
                Decimal("0.70"),
                trade_key=None,
                strategy_observation_mode="shadow_policy",
            )
        )

        self.assertTrue(result)
        self.assertFalse(calls[0][2])
        self.assertTrue(calls[0][4])

    def test_fetch_market_context_uses_market_metadata_yes_token_for_order_book(self):
        strategy = self._new_strategy()
        strategy.price_history = [Decimal("0.60")] * 20
        strategy._yes_token_id = None
        now = datetime.now(timezone.utc)
        strategy.all_btc_instruments = [
            {
                "slug": "btc-updown-15m-context",
                "condition_id": "condition-context",
                "yes_token_id": "market-yes-token",
                "no_token_id": "market-no-token",
                "start_time": (now - timedelta(minutes=15)).isoformat(),
                "end_time": (now + timedelta(minutes=15)).isoformat(),
            }
        ]
        strategy.current_instrument_index = 0
        calls = []

        class _OrderBookProcessor:
            def fetch_order_book(self, token_id):
                calls.append(token_id)
                return {"token_id": token_id, "bids": [], "asks": []}

        class _NewsSocialDataSource:
            async def connect(self):
                return None

            async def get_fear_greed_index(self):
                return {"value": "42", "classification": "Neutral"}

            async def disconnect(self):
                return None

        class _CoinbaseDataSource:
            async def connect(self):
                return None

            async def get_current_price(self):
                return Decimal("100000")

            async def disconnect(self):
                return None

        news_module_name = "data_sources.news_social.adapter"
        coinbase_module_name = "data_sources.coinbase.adapter"
        original_news_module = sys.modules.get(news_module_name)
        original_coinbase_module = sys.modules.get(coinbase_module_name)
        news_module = types.ModuleType(news_module_name)
        news_module.NewsSocialDataSource = _NewsSocialDataSource
        coinbase_module = types.ModuleType(coinbase_module_name)
        coinbase_module.CoinbaseDataSource = _CoinbaseDataSource
        strategy.orderbook_processor = _OrderBookProcessor()

        try:
            sys.modules[news_module_name] = news_module
            sys.modules[coinbase_module_name] = coinbase_module
            metadata = asyncio.run(strategy._fetch_market_context(Decimal("0.60")))
        finally:
            if original_news_module is None:
                sys.modules.pop(news_module_name, None)
            else:
                sys.modules[news_module_name] = original_news_module
            if original_coinbase_module is None:
                sys.modules.pop(coinbase_module_name, None)
            else:
                sys.modules[coinbase_module_name] = original_coinbase_module

        self.assertEqual(metadata["yes_token_id"], "market-yes-token")
        self.assertEqual(metadata["yes_order_book"]["token_id"], "market-yes-token")
        self.assertEqual(metadata["no_order_book"]["token_id"], "market-no-token")
        self.assertEqual(calls, ["market-yes-token", "market-no-token"])

    def test_fetch_market_context_fails_closed_on_cached_yes_token_mismatch(self):
        strategy = self._new_strategy()
        strategy.price_history = [Decimal("0.60")] * 20
        strategy._yes_token_id = "cached-yes-token"
        now = datetime.now(timezone.utc)
        strategy.all_btc_instruments = [
            {
                "slug": "btc-updown-15m-context-mismatch",
                "condition_id": "condition-context-mismatch",
                "yes_token_id": "market-yes-token",
                "no_token_id": "market-no-token",
                "start_time": (now - timedelta(minutes=15)).isoformat(),
                "end_time": (now + timedelta(minutes=15)).isoformat(),
            }
        ]
        strategy.current_instrument_index = 0

        with self.assertRaisesRegex(RuntimeError, "cached YES token_id does not match"):
            asyncio.run(strategy._fetch_market_context(Decimal("0.60")))

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
        original_apply_market_order_patch = self.bot.apply_market_order_patch
        self.bot.patch_applied = False
        self.bot.apply_market_order_patch = lambda: False
        try:
            with self.assertRaises(RuntimeError):
                self._run_bot_with_fake_node(simulation=False, redis_client=_SeededRedis())
        finally:
            self.bot.patch_applied = original_patch_applied
            self.bot.apply_market_order_patch = original_apply_market_order_patch

    def test_simulation_mode_does_not_require_market_order_patch(self):
        original_patch_applied = self.bot.patch_applied
        original_apply_market_order_patch = self.bot.apply_market_order_patch
        self.bot.patch_applied = False
        self.bot.apply_market_order_patch = lambda: False
        try:
            captured = self._run_bot_with_fake_node(simulation=True, redis_client=None)
        finally:
            self.bot.patch_applied = original_patch_applied
            self.bot.apply_market_order_patch = original_apply_market_order_patch

        self.assertTrue(captured["strategy"].current_simulation_mode)

    def test_quote_warning_patch_failure_aborts_startup(self):
        original_quote_warning_patch_applied = self.bot.quote_warning_patch_applied
        self.bot.quote_warning_patch_applied = False
        try:
            with self.assertRaisesRegex(RuntimeError, "quote-warning filter patch is required"):
                self._run_bot_with_fake_node(simulation=True, redis_client=None)
        finally:
            self.bot.quote_warning_patch_applied = original_quote_warning_patch_applied

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

    def test_trading_node_uses_short_shutdown_timeouts(self):
        captured = self._run_bot_with_fake_node(simulation=True, redis_client=None)
        config_kwargs = captured["config"].kwargs

        self.assertEqual(
            config_kwargs["timeout_post_stop"],
            self.bot.NAUTILUS_SHUTDOWN_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            config_kwargs["timeout_disconnection"],
            self.bot.NAUTILUS_SHUTDOWN_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            config_kwargs["timeout_shutdown"],
            self.bot.NAUTILUS_SHUTDOWN_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            config_kwargs["logging"].kwargs["log_directory"],
            "/tmp/nautilus-test-logs",
        )

    def test_trading_node_dispose_does_not_wait_on_executor_shutdown(self):
        captured = self._run_bot_with_fake_node(simulation=True, redis_client=None)

        self.assertEqual(
            captured["executor_shutdown"],
            {"wait": False, "cancel_futures": True},
        )

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
            "order_type": "market_ioc",
            "quote_quantity": True,
            "quantity_mode": "quote_quantity",
            "filled_qty": Decimal("4"),
            "filled_notional": Decimal("2.00"),
            "instrument_id": f"{condition_id}-{token_id}.POLYMARKET",
            "token_id": token_id,
            "slug": slug,
            "condition_id": condition_id,
            "market_start_time": (now - timedelta(hours=2, minutes=15)).isoformat(),
            "market_end_time": (now - timedelta(hours=2)).isoformat(),
            "filled_at": (now - timedelta(hours=2)).isoformat(),
            "submitted_at": (now - timedelta(hours=2)).isoformat(),
            "signal_score": 75,
            "signal_confidence": 0.82,
        }

    def _fill_metadata_for_meta(self, meta):
        return {
            key: meta[key]
            for key in self.bot.FILL_METADATA_IDENTITY_KEYS
            if meta.get(key) not in (None, "")
        }

    def _limit_ioc_trade_meta(
        self,
        order_id="limit-order-1",
        token_id="token-yes",
        submitted_limit_price=Decimal("0.62"),
        estimated_tokens=Decimal("8"),
    ):
        meta = self._live_trade_meta(order_id=order_id, token_id=token_id)
        meta.update(
            {
                "entry_price": submitted_limit_price,
                "size": submitted_limit_price * Decimal("4"),
                "estimated_tokens": estimated_tokens,
                "order_type": "limit_ioc",
                "quote_quantity": False,
                "quantity_mode": "base_quantity",
                "submitted_limit_price": submitted_limit_price,
                "limit_ioc_fill_policy": "partial_ok",
                "filled_notional": submitted_limit_price * Decimal("4"),
            }
        )
        return meta

    def _pending_actual_fill(
        self,
        *,
        fill_key="trade:pending",
        filled_qty="4",
        price="0.50",
        venue_order_id="0xpending",
        submitted_size="2.00",
    ):
        notional = str(Decimal(str(filled_qty)) * Decimal(str(price)))
        pending = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "venue_order_id": venue_order_id,
            "condition_id": "cond-pending",
            "token_id": "token-pending",
            "fills": [
                {
                    "fill_key": fill_key,
                    "filled_qty": str(filled_qty),
                    "price": str(price),
                    "notional": notional,
                    "raw_callback_payload": {"status": "ok", "trade_id": fill_key},
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "total_filled_qty": str(filled_qty),
            "total_filled_notional": notional,
            "vwap": str(price),
        }
        if submitted_size is not None:
            pending["submitted_size"] = submitted_size
        return pending

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

        strategy._record_live_order_fill(
            order_id,
            Decimal("0.50"),
            Decimal("4"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )
        strategy._record_live_order_fill(
            order_id,
            Decimal("0.60"),
            Decimal("5"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        meta = strategy._open_live_trades[order_id]
        self.assertEqual(meta["filled_qty"], Decimal("9"))
        self.assertEqual(meta["filled_notional"], Decimal("5.00"))
        self.assertEqual(meta["size"], Decimal("5.00"))
        self.assertEqual(meta["entry_price"], Decimal("5.00") / Decimal("9"))
        self.assertEqual(strategy.risk_engine._positions[order_id]["size"], Decimal("5.00"))

    def test_actual_fill_ok_persists_pending_then_fill_consumes_actual_values(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-ok"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("5.50"), Decimal("0.55"), "buy_yes")

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-actual",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual",
                "condition_id": "cond-a",
                "token_id": "token-actual",
                "submitted_size": "2.21",
            },
        )

        self.assertIn(order_id, strategy._pending_actual_fills)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["pending_actual_fills"][order_id]["total_filled_qty"], "4.25")
        self.assertEqual(data["pending_actual_fills"][order_id]["total_filled_notional"], "2.2100")
        self.assertEqual(data["pending_actual_fills"][order_id]["vwap"], "0.52")

        self.assertTrue(
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.01"),
                Decimal("0.01"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        )
        recorded = strategy._open_live_trades[order_id]
        self.assertEqual(recorded["filled_qty"], Decimal("4.25"))
        self.assertEqual(recorded["entry_price"], Decimal("0.52"))
        self.assertEqual(recorded["venue_order_id"], "0xactual")
        self.assertNotIn(order_id, strategy._pending_actual_fills)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(order_id, data["pending_actual_fills"])
        self.assertEqual(data["open"][order_id]["venue_order_id"], "0xactual")
        self.assertNotIn("_actual_filled_qty", recorded)
        self.assertNotIn("_actual_fill_vwap", recorded)

    def test_actual_fill_ok_blocks_limit_ioc_vwap_above_submitted_limit(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-limit-price-violation"
        meta = self._limit_ioc_trade_meta(
            order_id=order_id,
            token_id="token-limit-price",
            submitted_limit_price=Decimal("0.62"),
            estimated_tokens=Decimal("8"),
        )
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-limit-price-violation",
                "filled_qty": Decimal("4"),
                "vwap": Decimal("0.63"),
                "venue_order_id": "0xlimitprice",
                "condition_id": "cond-a",
                "token_id": "token-limit-price",
            },
        )

        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "limit_ioc_fill_price_above_submitted_limit:vwap=0.63,submitted_limit_price=0.62",
        )
        self.assertEqual(strategy._settled_live_trades[-1]["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._pending_actual_fills)

    def test_direct_fill_blocks_missing_order_type_metadata(self):
        strategy = self._new_strategy()
        order_id = "direct-fill-missing-order-type"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-missing-order-type")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta.pop("order_type")
        strategy._submitted_positions[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.60"),
            Decimal("4"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "missing_order_type_for_fill_envelope",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._open_live_trades)

    def test_direct_fill_blocks_invalid_order_type_metadata(self):
        strategy = self._new_strategy()
        order_id = "direct-fill-invalid-order-type"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-invalid-order-type")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta["order_type"] = "limit"
        strategy._submitted_positions[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.60"),
            Decimal("4"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "invalid_order_type_for_fill_envelope:'limit'",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._open_live_trades)

    def test_direct_fill_blocks_limit_ioc_cumulative_quantity_above_submitted_quantity(self):
        strategy = self._new_strategy()
        order_id = "direct-fill-limit-qty-violation"
        meta = self._limit_ioc_trade_meta(
            order_id=order_id,
            token_id="token-limit-qty",
            submitted_limit_price=Decimal("0.62"),
            estimated_tokens=Decimal("8"),
        )
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.60"),
            Decimal("8.1"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "limit_ioc_fill_qty_above_submitted_quantity:filled_qty=8.1,submitted_qty=8",
        )
        self.assertEqual(strategy._settled_live_trades[-1]["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._open_live_trades)

    def test_direct_fill_blocks_later_limit_ioc_fill_above_cap_even_when_average_is_safe(self):
        strategy = self._new_strategy()
        order_id = "direct-fill-limit-later-price-violation"
        meta = self._limit_ioc_trade_meta(
            order_id=order_id,
            token_id="token-limit-later-price",
            submitted_limit_price=Decimal("0.62"),
            estimated_tokens=Decimal("8"),
        )
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        self.assertTrue(
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.60"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        )
        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.63"),
            Decimal("4"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "limit_ioc_fill_price_above_submitted_limit:vwap=0.63,submitted_limit_price=0.62",
        )
        self.assertEqual(strategy._settled_live_trades[-1]["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_pending_actual_fill_blocks_later_limit_ioc_fill_above_cap_even_when_average_is_safe(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-limit-later-price-violation"
        meta = self._limit_ioc_trade_meta(
            order_id=order_id,
            token_id="token-actual-limit-later",
            submitted_limit_price=Decimal("0.62"),
            estimated_tokens=Decimal("8"),
        )
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-limit-safe-first",
                "filled_qty": Decimal("4"),
                "vwap": Decimal("0.60"),
                "venue_order_id": "0xlimitlater",
                "condition_id": "cond-a",
                "token_id": "token-actual-limit-later",
            },
        )
        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "above_submitted_limit"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-limit-unsafe-second",
                    "filled_qty": Decimal("4"),
                    "vwap": Decimal("0.63"),
                    "venue_order_id": "0xlimitlater",
                    "condition_id": "cond-a",
                    "token_id": "token-actual-limit-later",
                },
            )

        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(
            strategy._pending_actual_fills[order_id]["external_fill_repair_reason"],
            "limit_ioc_fill_price_above_submitted_limit:vwap=0.63,submitted_limit_price=0.62",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_actual_fill_ok_rejects_payload_venue_matching_other_open_trade(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-venue-conflict"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        other_meta = self._live_trade_meta(order_id="other-open-venue-conflict", token_id="token-other")
        other_meta["venue_order_id"] = "0xvenue-conflict"
        strategy._open_live_trades["other-open-venue-conflict"] = other_meta

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already belongs to open trade"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-venue-conflict",
                    "filled_qty": Decimal("4.25"),
                    "vwap": Decimal("0.52"),
                    "venue_order_id": "0xVENUE-CONFLICT",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertNotIn(order_id, strategy._pending_actual_fills)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], order_id)
        self.assertEqual(strategy._settled_live_trades[-1]["unknown_reason"], "actual_fill_ok_venue_conflict")
        self.assertEqual(strategy._settled_live_trades[-1]["filled_qty"], "4.25")

    def test_actual_fill_ok_rejects_mismatched_tracked_venue_order_id(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-venue-mismatch"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta["venue_order_id"] = "0xtracked-venue"
        strategy._submitted_positions[order_id] = meta

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "venue_order_id mismatch"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-venue-mismatch",
                    "filled_qty": Decimal("4.25"),
                    "vwap": Decimal("0.52"),
                    "venue_order_id": "0xpayload-venue",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertNotIn(order_id, strategy._pending_actual_fills)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], order_id)
        self.assertEqual(strategy._settled_live_trades[-1]["unknown_reason"], "actual_fill_ok_venue_conflict")
        self.assertEqual(strategy._settled_live_trades[-1]["venue_conflict_payload_venue_order_id"], "0xpayload-venue")
        self.assertEqual(strategy._settled_live_trades[-1]["venue_conflict_tracked_venue_order_id"], "0xtracked-venue")

    def test_actual_fill_ok_does_not_synthesize_submitted_size_from_accounting_size(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-no-submitted-size"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-no-submitted-size",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual-no-submitted",
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )

        self.assertIn(order_id, strategy._pending_actual_fills)
        self.assertNotIn("submitted_size", strategy._pending_actual_fills[order_id])
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn("submitted_size", data["pending_actual_fills"][order_id])

    def test_actual_fill_ok_without_client_id_requires_external_repair(self):
        strategy = self._new_strategy()

        strategy._handle_actual_fill(
            None,
            {
                "status": "ok",
                "trade_id": "trade-no-client",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual-no-client",
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["order_id"], None)
        self.assertEqual(unknown["venue_order_id"], "0xactual-no-client")
        self.assertEqual(unknown["unknown_reason"], "actual_fill_ok_missing_client_order_id")
        self.assertTrue(unknown["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertNotIn("filled_qty", unknown)
        self.assertNotIn("entry_price", unknown)
        self.assertNotIn("filled_notional", unknown)
        self.assertNotIn("size", unknown)

    def test_actual_fill_ok_without_client_id_does_not_promote_matching_open_accounting(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-no-client-open"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta["venue_order_id"] = "0xactual-no-client-open"
        strategy._open_live_trades[order_id] = meta

        strategy._handle_actual_fill(
            None,
            {
                "status": "ok",
                "trade_id": "trade-no-client-open",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual-no-client-open",
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["order_id"], order_id)
        self.assertEqual(unknown["venue_order_id"], "0xactual-no-client-open")
        self.assertTrue(unknown["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertNotIn("filled_qty", unknown)
        self.assertNotIn("entry_price", unknown)
        self.assertNotIn("filled_notional", unknown)
        self.assertNotIn("size", unknown)

    def test_actual_fill_ok_without_client_id_marks_matching_pending_for_external_repair(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-no-client-pending"
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xactual-no-client-pending")
        pending_before = copy.deepcopy(strategy._pending_actual_fills[order_id]["fills"])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "requires external repair"):
            strategy._handle_actual_fill(
                None,
                {
                    "status": "ok",
                    "trade_id": "trade-no-client-pending",
                    "filled_qty": Decimal("4.25"),
                    "vwap": Decimal("0.52"),
                    "venue_order_id": "0xactual-no-client-pending",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertEqual(strategy._pending_actual_fills[order_id]["fills"], pending_before)
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(strategy._settled_live_trades, [])

    def test_malformed_actual_fill_ok_without_client_id_marks_matching_pending_for_external_repair(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-no-client-malformed-pending"
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(
            venue_order_id="0xactual-no-client-malformed-pending"
        )
        pending_before = copy.deepcopy(strategy._pending_actual_fills[order_id]["fills"])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "requires external repair"):
            strategy._handle_actual_fill(
                None,
                {
                    "status": "ok",
                    "trade_id": "trade-no-client-malformed-pending",
                    "venue_order_id": "0xactual-no-client-malformed-pending",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertEqual(strategy._pending_actual_fills[order_id]["fills"], pending_before)
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(
            strategy._pending_actual_fills[order_id]["external_fill_repair_reason"],
            "actual_fill_ok_missing_required_fields:filled_qty,vwap",
        )
        self.assertEqual(strategy._settled_live_trades, [])

    def test_actual_fill_ok_rejects_duplicate_pending_actual_fill_without_overwrite(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-duplicate-pending"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-duplicate",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual-first",
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )
        pending_before_duplicate = copy.deepcopy(strategy._pending_actual_fills[order_id])
        ledger_before_duplicate = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))[
            "pending_actual_fills"
        ][order_id]

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "duplicate actual-fill callback"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-duplicate",
                    "filled_qty": Decimal("5.25"),
                    "vwap": Decimal("0.62"),
                    "venue_order_id": "0xactual-second",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(strategy._pending_actual_fills[order_id]["fills"], pending_before_duplicate["fills"])
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(
            strategy._pending_actual_fills[order_id]["external_fill_repair_reason"],
            "duplicate_actual_fill_key",
        )
        self.assertEqual(strategy._settled_live_trades, [])
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["pending_actual_fills"][order_id]["fills"], ledger_before_duplicate["fills"])
        self.assertTrue(data["pending_actual_fills"][order_id]["requires_external_fill_repair"])
        self.assertEqual(data["settled"], [])
        self.assertEqual(strategy._submitted_positions[order_id]["_actual_filled_qty"], Decimal("4.25"))
        self.assertEqual(strategy._submitted_positions[order_id]["_actual_fill_vwap"], Decimal("0.52"))

    def test_blocked_direct_fill_preserves_external_repair_pending_actual_fill(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-repair-blocked-fill"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-repair",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual-repair",
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )
        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "duplicate actual-fill callback"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-repair",
                    "filled_qty": Decimal("5.25"),
                    "vwap": Decimal("0.62"),
                    "venue_order_id": "0xactual-repair",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )
        pending_before_direct_fill = copy.deepcopy(strategy._pending_actual_fills[order_id])
        ledger_before_direct_fill = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))[
            "pending_actual_fills"
        ][order_id]

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "pending actual fill requires external repair"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.62"),
                Decimal("5.25"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )

        self.assertEqual(strategy._pending_actual_fills[order_id], pending_before_direct_fill)
        self.assertEqual(strategy._settled_live_trades, [])
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["pending_actual_fills"][order_id], ledger_before_direct_fill)
        self.assertEqual(data["settled"], [])

    def test_malformed_actual_fill_preserves_external_repair_pending_actual_fill(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-repair-malformed"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-malformed-repair",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual-malformed-repair",
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )
        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "duplicate actual-fill callback"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-malformed-repair",
                    "filled_qty": Decimal("5.25"),
                    "vwap": Decimal("0.62"),
                    "venue_order_id": "0xactual-malformed-repair",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )
        existing_fills = copy.deepcopy(strategy._pending_actual_fills[order_id]["fills"])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "requires external repair"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-malformed-second",
                    "vwap": Decimal("0.62"),
                    "venue_order_id": "0xactual-malformed-repair",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertEqual(strategy._pending_actual_fills[order_id]["fills"], existing_fills)
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(strategy._settled_live_trades, [])
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertIn(order_id, data["pending_actual_fills"])
        self.assertEqual(data["settled"], [])

    def test_external_repair_pending_actual_fill_rejects_new_unique_fill_without_mutation(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-repair-freeze"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xactual-freeze")
        strategy._pending_actual_fills[order_id]["requires_external_fill_repair"] = True
        strategy._pending_actual_fills[order_id]["external_fill_repair_reason"] = "unit_test"
        pending_before = copy.deepcopy(strategy._pending_actual_fills[order_id])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already requires external repair"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-new-after-repair",
                    "filled_qty": Decimal("5.25"),
                    "vwap": Decimal("0.62"),
                    "venue_order_id": "0xactual-freeze",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertEqual(strategy._pending_actual_fills[order_id], pending_before)
        self.assertEqual(strategy._settled_live_trades, [])

    def test_external_repair_pending_actual_fill_rejects_keyless_fill_without_mutation(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-repair-keyless-freeze"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xactual-keyless-freeze")
        strategy._pending_actual_fills[order_id]["requires_external_fill_repair"] = True
        strategy._pending_actual_fills[order_id]["external_fill_repair_reason"] = "unit_test"
        pending_before = copy.deepcopy(strategy._pending_actual_fills[order_id])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already requires external repair"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "filled_qty": Decimal("5.25"),
                    "vwap": Decimal("0.62"),
                    "venue_order_id": "0xactual-keyless-freeze",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertEqual(strategy._pending_actual_fills[order_id], pending_before)
        self.assertEqual(strategy._settled_live_trades, [])

    def test_failed_actual_fill_preserves_external_repair_pending_actual_fill(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-repair-failed-callback"
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xactual-failed-repair")
        strategy._pending_actual_fills[order_id]["requires_external_fill_repair"] = True
        strategy._pending_actual_fills[order_id]["external_fill_repair_reason"] = "unit_test"
        pending_before = copy.deepcopy(strategy._pending_actual_fills[order_id])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already requires external repair"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "failed",
                    "reason": "adapter_failed_after_repair",
                },
            )

        self.assertEqual(strategy._pending_actual_fills[order_id], pending_before)
        self.assertEqual(strategy._settled_live_trades, [])

    def test_duplicate_actual_fill_with_new_conflicting_venue_still_rejects_without_overwrite(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-duplicate-new-venue"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        other_meta = self._live_trade_meta(order_id="other-open-duplicate-venue", token_id="token-other")
        other_meta["venue_order_id"] = "0xactual-second"
        strategy._open_live_trades["other-open-duplicate-venue"] = other_meta

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-first",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )
        pending_before_duplicate = copy.deepcopy(strategy._pending_actual_fills[order_id])
        ledger_before_duplicate = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))[
            "pending_actual_fills"
        ][order_id]

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already belongs to open trade"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "ok",
                    "trade_id": "trade-second",
                    "filled_qty": Decimal("5.25"),
                    "vwap": Decimal("0.62"),
                    "venue_order_id": "0xactual-second",
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(strategy._pending_actual_fills[order_id]["fills"], pending_before_duplicate["fills"])
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(
            strategy._pending_actual_fills[order_id]["external_fill_repair_reason"],
            "duplicate_actual_fill_venue_conflict",
        )
        self.assertEqual(strategy._settled_live_trades, [])
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["pending_actual_fills"][order_id]["fills"], ledger_before_duplicate["fills"])
        self.assertTrue(data["pending_actual_fills"][order_id]["requires_external_fill_repair"])
        self.assertEqual(data["settled"], [])

    def test_actual_fill_without_client_or_venue_id_is_rejected_without_unknown(self):
        strategy = self._new_strategy()

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "neither usable client_order_id nor venue_order_id"):
            strategy._handle_actual_fill(
                None,
                {
                    "status": "ok",
                    "filled_qty": Decimal("4.25"),
                    "vwap": Decimal("0.52"),
                    "condition_id": "cond-a",
                    "token_id": "token-actual",
                },
            )

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertFalse(self._test_ledger_path.exists())

    def test_actual_fill_with_malformed_submitted_intent_creates_unknown(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-malformed-intent"
        strategy._submitted_order_intents[order_id] = "not-an-object"

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-malformed-intent",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xmalformed-intent-fill",
                "condition_id": "cond-a",
                "token_id": "token-actual",
            },
        )

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._submitted_order_intents)
        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["order_id"], order_id)
        self.assertEqual(unknown["unknown_reason"], "actual_fill_ok_but_no_local_tracking")
        self.assertTrue(unknown["submitted_order_intent_malformed"])
        self.assertEqual(unknown["submitted_order_intent_raw"], "not-an-object")
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["settled"][-1]["order_id"], order_id)
        self.assertNotIn(order_id, data["submitted_order_intents"])

    def test_actual_fill_ok_save_failure_does_not_mutate_pending_or_override(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-save-fails"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-actual")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        original_write = strategy._write_live_trade_ledger_state

        def fail_write(_state):
            raise OSError("disk full")

        strategy._write_live_trade_ledger_state = fail_write
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to save live trade ledger"):
                strategy._handle_actual_fill(
                    order_id,
                    {
                        "status": "ok",
                        "trade_id": "trade-save-fails",
                        "filled_qty": Decimal("4.25"),
                        "vwap": Decimal("0.52"),
                        "venue_order_id": "0xactual-save-fails",
                        "condition_id": "cond-a",
                        "token_id": "token-actual",
                        "submitted_size": "2.21",
                    },
                )
        finally:
            strategy._write_live_trade_ledger_state = original_write

        self.assertNotIn(order_id, strategy._pending_actual_fills)
        self.assertNotIn("_actual_filled_qty", strategy._submitted_positions[order_id])
        self.assertNotIn("_actual_fill_vwap", strategy._submitted_positions[order_id])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_actual_fill_override_is_consumed_once(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-once"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-once")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("5.50"), Decimal("0.55"), "buy_yes")

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-once",
                "filled_qty": Decimal("4.25"),
                "vwap": Decimal("0.52"),
                "venue_order_id": "0xactual",
                "condition_id": "cond-a",
                "token_id": "token-once",
                "submitted_size": "2.21",
            },
        )

        self.assertTrue(
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.01"),
                Decimal("0.01"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        )
        self.assertTrue(
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.60"),
                Decimal("1.00"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        )

        recorded = strategy._open_live_trades[order_id]
        self.assertEqual(recorded["filled_qty"], Decimal("5.25"))
        self.assertEqual(recorded["filled_notional"], Decimal("2.21") + Decimal("0.60"))
        self.assertNotIn("_actual_filled_qty", recorded)
        self.assertNotIn("_actual_fill_vwap", recorded)

    def test_actual_fill_failed_with_venue_id_creates_reconcilable_unknown_without_synthetic_order_id(self):
        strategy = self._new_strategy()

        strategy._handle_actual_fill(
            None,
            {
                "status": "failed",
                "reason": "unmapped_venue_order_id",
                "venue_order_id": "0xvenue",
                "condition_id": "cond-venue",
                "token_id": "token-venue",
                "submitted_size": "5.00",
            },
        )

        self.assertIsNone(strategy._settled_live_trades[-1]["order_id"])
        self.assertEqual(strategy._settled_live_trades[-1]["venue_order_id"], "0xvenue")
        self.assertEqual(strategy._settled_live_trades[-1]["payout"], "UNKNOWN")
        self.assertNotIn("size", strategy._settled_live_trades[-1])
        self.assertEqual(strategy._settled_live_trades[-1]["submitted_size"], "5.00")
        self.assertTrue(strategy._settled_live_trades[-1]["needs_reconciliation"])
        self.assertTrue(strategy._unresolved_settlement_unknowns())

    def test_actual_fill_failed_with_venue_id_converts_matching_open_trade(self):
        strategy = self._new_strategy()
        order_id = "open-venue-match"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-open-venue")
        meta["venue_order_id"] = "0xvenue-open"
        strategy._open_live_trades[order_id] = meta

        strategy._handle_actual_fill(
            None,
            {
                "status": "failed",
                "reason": "unmapped_venue_order_id",
                "venue_order_id": "0xVENUE-OPEN",
                "condition_id": "cond-open-venue",
                "token_id": "token-open-venue",
            },
        )

        self.assertNotIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], order_id)
        self.assertEqual(strategy._settled_live_trades[-1]["venue_order_id"], "0xVENUE-OPEN")
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(order_id, data["open"])
        self.assertEqual(data["settled"][-1]["order_id"], order_id)

    def test_durable_unknown_preserves_open_trade_venue_order_id_when_payload_omits_it(self):
        strategy = self._new_strategy()
        order_id = "open-client-match"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-open-client")
        meta["venue_order_id"] = "0xvenue-from-open"
        strategy._open_live_trades[order_id] = meta

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "failed",
                "reason": "callback_failed_without_venue",
                "condition_id": "cond-a",
                "token_id": "token-open-client",
            },
        )

        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], order_id)
        self.assertEqual(strategy._settled_live_trades[-1]["venue_order_id"], "0xvenue-from-open")
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["settled"][-1]["venue_order_id"], "0xvenue-from-open")

    def test_durable_unknown_rejects_mismatched_payload_and_open_venue_order_id(self):
        strategy = self._new_strategy()
        order_id = "open-client-venue-mismatch"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-open-client-mismatch")
        meta["venue_order_id"] = "0xvenue-from-open"
        strategy._open_live_trades[order_id] = meta

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "venue_order_id mismatch"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "failed",
                    "reason": "callback_failed_with_wrong_venue",
                    "venue_order_id": "0xpayload-venue",
                    "condition_id": "cond-a",
                    "token_id": "token-open-client-mismatch",
                },
            )

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])

    def test_durable_unknown_rejects_duplicate_open_venue_when_payload_omits_it(self):
        strategy = self._new_strategy()
        order_id = "open-client-dup-venue"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-open-client-dup")
        meta["venue_order_id"] = "0xvenue-dup-from-open"
        strategy._open_live_trades[order_id] = meta
        strategy._settled_live_trades.append(
            {
                "order_id": "prior-dup",
                "venue_order_id": "0xVENUE-DUP-FROM-OPEN",
                "settlement_source": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": True,
                "payout": "UNKNOWN",
                "pnl": "UNKNOWN",
            }
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already exists"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "failed",
                    "reason": "callback_failed_without_venue",
                    "condition_id": "cond-a",
                    "token_id": "token-open-client-dup",
                },
            )

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(len(strategy._settled_live_trades), 1)

    def test_durable_unknown_rejects_effective_venue_matching_other_open_trade(self):
        strategy = self._new_strategy()
        order_id = "open-client-effective-venue"
        other_order_id = "other-open-effective-venue"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-open-effective")
        meta["venue_order_id"] = "0xvenue-shared-open"
        other_meta = self._live_trade_meta(order_id=other_order_id, token_id="token-other-effective")
        other_meta["venue_order_id"] = "0xVENUE-SHARED-OPEN"
        strategy._open_live_trades[order_id] = meta
        strategy._open_live_trades[other_order_id] = other_meta

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already belongs to open trade"):
            strategy._handle_actual_fill(
                order_id,
                {
                    "status": "failed",
                    "reason": "callback_failed_without_venue",
                    "condition_id": "cond-a",
                    "token_id": "token-open-effective",
                },
            )

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertIn(other_order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])

    def test_actual_fill_failed_rejects_venue_id_matching_different_open_trade(self):
        strategy = self._new_strategy()
        meta = self._live_trade_meta(order_id="open-venue-conflict", token_id="token-open-venue")
        meta["venue_order_id"] = "0xvenue-conflict"
        strategy._open_live_trades["open-venue-conflict"] = meta

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already belongs to open trade"):
            strategy._handle_actual_fill(
                "different-client-id",
                {
                    "status": "failed",
                    "reason": "unmapped_venue_order_id",
                    "venue_order_id": "0xVENUE-CONFLICT",
                },
            )

        self.assertFalse(strategy._settled_live_trades)
        self.assertIn("open-venue-conflict", strategy._open_live_trades)

    def test_actual_fill_venue_like_client_order_id_is_rejected(self):
        strategy = self._new_strategy()

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "venue-like client_order_id"):
            strategy._handle_actual_fill(
                "venue:0xvenue",
                {
                    "status": "failed",
                    "reason": "unmapped_venue_order_id",
                    "venue_order_id": "0xvenue",
                },
            )

        self.assertEqual(strategy._settled_live_trades, [])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_actual_fill_venue_like_client_order_id_without_payload_venue_is_rejected(self):
        strategy = self._new_strategy()

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "venue-like client_order_id"):
            strategy._handle_actual_fill(
                "venue:0xvenue-only-selector",
                {
                    "status": "failed",
                    "reason": "unmapped_venue_order_id",
                },
            )

        self.assertEqual(strategy._settled_live_trades, [])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_actual_fill_uppercase_venue_like_client_order_id_is_rejected(self):
        strategy = self._new_strategy()

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "venue-like client_order_id"):
            strategy._handle_actual_fill(
                "VENUE:0xvenue",
                {
                    "status": "failed",
                    "reason": "unmapped_venue_order_id",
                    "venue_order_id": "0xvenue",
                },
            )

        self.assertEqual(strategy._settled_live_trades, [])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_actual_fill_rejects_duplicate_venue_order_id_even_when_client_id_differs(self):
        strategy = self._new_strategy()
        strategy._settled_live_trades.append(
            {
                "order_id": None,
                "venue_order_id": "0xdup",
                "settlement_source": "manual_reconciliation",
                "needs_reconciliation": False,
                "payout": "1",
                "pnl": "0",
            }
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already exists"):
            strategy._handle_actual_fill(
                "client-dup",
                {
                    "status": "failed",
                    "reason": "unmapped_venue_order_id",
                    "venue_order_id": "0xdup",
                },
            )

        self.assertEqual(len(strategy._settled_live_trades), 1)

    def test_actual_fill_rejects_duplicate_venue_order_id_in_pending_actual_fills(self):
        strategy = self._new_strategy()
        strategy._pending_actual_fills["pending-dup"] = self._pending_actual_fill(venue_order_id="0xdup")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already exists"):
            strategy._handle_actual_fill(
                "client-dup",
                {
                    "status": "failed",
                    "reason": "unmapped_venue_order_id",
                    "venue_order_id": "0xdup",
                },
            )

        self.assertFalse(strategy._settled_live_trades)
        self.assertIn("pending-dup", strategy._pending_actual_fills)

    def test_actual_fill_failed_missing_reason_fails_closed(self):
        strategy = self._new_strategy()

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "missing reason"):
            strategy._handle_actual_fill(
                "missing-reason",
                {
                    "status": "failed",
                    "venue_order_id": "0xvenue",
                },
            )

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertFalse(strategy._settled_live_trades)

    def test_actual_fill_ok_rejects_non_positive_vwap(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-zero-vwap"
        strategy._submitted_positions[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-actual",
        )

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "filled_qty": "4.25",
                "vwap": "0",
                "venue_order_id": "0xactual",
                "condition_id": "cond-a",
                "token_id": "token-actual",
                "submitted_size": "2.21",
            },
        )

        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "actual_fill_ok_non_positive_qty_or_vwap",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_actual_fill_ok_rejects_non_finite_values(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-non-finite-values"
        strategy._submitted_positions[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-actual",
        )

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "filled_qty": "NaN",
                "vwap": "Infinity",
                "venue_order_id": "0xactual-infinite",
                "condition_id": "cond-a",
                "token_id": "token-actual",
                "submitted_size": "2.21",
            },
        )

        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "actual_fill_ok_non_finite_qty_or_vwap",
        )
        self.assertEqual(strategy._settled_live_trades[-1]["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertTrue(strategy._settled_live_trades[-1]["needs_reconciliation"])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._pending_actual_fills)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(order_id, data["pending_actual_fills"])

    def test_actual_fill_ok_rejects_vwap_above_one(self):
        strategy = self._new_strategy()
        order_id = "actual-fill-high-vwap"
        strategy._submitted_positions[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-actual",
        )

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "filled_qty": "4.25",
                "vwap": "2",
                "venue_order_id": "0xactual-high",
                "condition_id": "cond-a",
                "token_id": "token-actual",
                "submitted_size": "2.21",
            },
        )

        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "actual_fill_ok_vwap_above_one",
        )
        self.assertEqual(strategy._settled_live_trades[-1]["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertTrue(strategy._settled_live_trades[-1]["needs_reconciliation"])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._pending_actual_fills)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(order_id, data["pending_actual_fills"])

    def test_zero_price_fill_creates_durable_unknown_and_blocks(self):
        strategy = self._new_strategy()
        order_id = "zero-price-order"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-zero")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0"),
            Decimal("4"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], order_id)
        self.assertEqual(strategy._settled_live_trades[-1]["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertEqual(strategy._settled_live_trades[-1]["unknown_reason"], "non_positive_fill_price_from_nautilus")
        self.assertEqual(strategy._settled_live_trades[-1]["payout"], "UNKNOWN")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._open_live_trades)

    def test_missing_fill_metadata_object_blocks_before_recording_fill(self):
        strategy = self._new_strategy()
        order_id = "missing-fill-metadata-object"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-missing-fill-meta-object")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "fill metadata .* is missing"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=None,
            )

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertNotIn(order_id, strategy._open_live_trades)

    def test_empty_fill_metadata_creates_durable_unknown_and_blocks(self):
        strategy = self._new_strategy()
        order_id = "empty-fill-metadata"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-empty-fill-meta")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.50"),
            Decimal("4"),
            fill_metadata={},
        )

        self.assertFalse(result)
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["order_id"], order_id)
        self.assertEqual(unknown["unknown_reason"], "missing_fill_identity_metadata_from_nautilus")
        self.assertEqual(unknown["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertNotIn(order_id, strategy._open_live_trades)

    def test_invalid_direct_fill_preserves_pending_actual_fill(self):
        strategy = self._new_strategy()
        order_id = "invalid-direct-fill-pending"
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xinvalid-direct-pending")
        pending_before_fills = copy.deepcopy(strategy._pending_actual_fills[order_id]["fills"])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "requires external repair"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(strategy._pending_actual_fills[order_id]),
            )

        self.assertEqual(strategy._pending_actual_fills[order_id]["fills"], pending_before_fills)
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(strategy._settled_live_trades, [])

    def test_valid_direct_fill_rejects_external_repair_pending_actual_fill(self):
        strategy = self._new_strategy()
        order_id = "valid-direct-fill-repair-pending"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-direct-repair")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xvalid-direct-repair")
        strategy._pending_actual_fills[order_id]["requires_external_fill_repair"] = True
        strategy._pending_actual_fills[order_id]["external_fill_repair_reason"] = "unit_test"
        pending_before = copy.deepcopy(strategy._pending_actual_fills[order_id])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "already requires external repair"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )

        self.assertEqual(strategy._pending_actual_fills[order_id], pending_before)
        self.assertEqual(strategy._settled_live_trades, [])

    def test_invalid_direction_direct_fill_preserves_pending_actual_fill(self):
        strategy = self._new_strategy()
        order_id = "invalid-direction-direct-fill-pending"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-invalid-direction-pending")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta["direction"] = "sideways"
        strategy._submitted_positions[order_id] = meta
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xinvalid-direction-pending")
        pending_before_fills = copy.deepcopy(strategy._pending_actual_fills[order_id]["fills"])

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "requires external repair"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )

        self.assertEqual(strategy._pending_actual_fills[order_id]["fills"], pending_before_fills)
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(strategy._settled_live_trades, [])

    def test_direct_fill_rejects_non_finite_price(self):
        strategy = self._new_strategy()
        order_id = "nan-price-order"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-nan-price")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("NaN"),
            Decimal("4"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "non_finite_fill_price_or_qty_from_nautilus",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_direct_fill_rejects_non_positive_quantity(self):
        strategy = self._new_strategy()
        order_id = "zero-qty-order"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-zero-qty")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.50"),
            Decimal("0"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "non_positive_fill_qty_from_nautilus",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_direct_fill_rejects_price_above_one(self):
        strategy = self._new_strategy()
        order_id = "high-price-order"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-high-price")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("1.01"),
            Decimal("4"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertEqual(
            strategy._settled_live_trades[-1]["unknown_reason"],
            "fill_price_above_one_from_nautilus",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_untracked_direct_fill_creates_durable_unknown_and_blocks(self):
        strategy = self._new_strategy()
        order_id = "untracked-fill-order"

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.50"),
            Decimal("4"),
            fill_metadata={
                "instrument_id": "cond-untracked-token-untracked.POLYMARKET",
                "condition_id": "cond-untracked",
                "token_id": "token-untracked",
            },
        )

        self.assertFalse(result)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], order_id)
        self.assertEqual(strategy._settled_live_trades[-1]["unknown_reason"], "untracked_nautilus_fill")
        self.assertTrue(strategy._settled_live_trades[-1]["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertNotIn("filled_qty", strategy._settled_live_trades[-1])
        self.assertNotIn("entry_price", strategy._settled_live_trades[-1])
        self.assertNotIn("size", strategy._settled_live_trades[-1])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_open_trade_missing_previous_fill_accounting_creates_unknown_and_blocks(self):
        strategy = self._new_strategy()
        order_id = "open-missing-previous-fill"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-open-missing-prev")
        meta.pop("filled_qty")
        strategy._open_live_trades[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0.50"),
            Decimal("1"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertNotIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], order_id)
        self.assertEqual(strategy._settled_live_trades[-1]["unknown_reason"], "invalid_open_fill_accounting")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_zero_price_fill_converts_existing_open_trade_to_unknown(self):
        strategy = self._new_strategy()
        order_id = "zero-price-open-order"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-zero-open")
        strategy._open_live_trades[order_id] = meta

        result = strategy._record_live_order_fill(
            order_id,
            Decimal("0"),
            Decimal("1"),
            fill_metadata=self._fill_metadata_for_meta(meta),
        )

        self.assertFalse(result)
        self.assertNotIn(order_id, strategy._open_live_trades)
        matching_unknowns = [
            trade
            for trade in strategy._settled_live_trades
            if trade.get("order_id") == order_id
            and trade.get("settlement_source") == "SETTLEMENT_UNKNOWN"
        ]
        self.assertEqual(len(matching_unknowns), 1)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(order_id, data["open"])
        ledger_unknowns = [
            trade
            for trade in data["settled"]
            if trade.get("order_id") == order_id
            and trade.get("settlement_source") == "SETTLEMENT_UNKNOWN"
        ]
        self.assertEqual(len(ledger_unknowns), 1)
        unknown = ledger_unknowns[0]
        self.assertTrue(unknown["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertNotIn("size", unknown)
        self.assertNotIn("filled_qty", unknown)
        self.assertNotIn("entry_price", unknown)
        self.assertNotIn("filled_notional", unknown)
        self.assertNotIn("submitted_size", unknown)

        strategy._release_live_trade_ledger_lock()
        self._strategies.remove(strategy)
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "mark_settlement_resolved.py"),
                "--ledger",
                str(self._test_ledger_path),
                "--order-id",
                order_id,
                "--payout",
                "4",
                "--reason",
                "unit test open-to-unknown resolution",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("size is required", result.stderr + result.stdout)

    def test_durable_unknown_save_failure_preserves_in_memory_state(self):
        strategy = self._new_strategy()
        order_id = "save-fails"
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill(venue_order_id="0xsavefails")
        original_write = strategy._write_live_trade_ledger_state

        def fail_write(_state):
            raise OSError("disk full")

        strategy._write_live_trade_ledger_state = fail_write
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to save live trade ledger"):
                strategy._create_durable_settlement_unknown_from_actual_fill(
                    order_id,
                    {
                        "status": "failed",
                        "reason": "unit_test",
                        "venue_order_id": "0xsavefails",
                    },
                    "unit_test",
                    ignore_pending_order_id=order_id,
                )
        finally:
            strategy._write_live_trade_ledger_state = original_write

        self.assertFalse(strategy._settled_live_trades)
        self.assertIn(order_id, strategy._pending_actual_fills)
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_settled_live_trade_save_failure_raises_and_preserves_open_trade(self):
        strategy = self._new_strategy()
        order_id = "settled-save-fails"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-settled-save")
        strategy._open_live_trades[order_id] = meta
        original_write = strategy._write_live_trade_ledger_state

        def fail_write(_state):
            raise OSError("disk full")

        strategy._write_live_trade_ledger_state = fail_write
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to persist settled live trade"):
                strategy._record_settled_live_trade(
                    order_id,
                    meta,
                    Decimal("4"),
                    datetime.now(timezone.utc),
                    "unit_test",
                )
        finally:
            strategy._write_live_trade_ledger_state = original_write

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_pending_actual_fills_are_unresolved_until_consumed(self):
        strategy = self._new_strategy()
        order_id = "pending-actual"
        strategy._pending_actual_fills[order_id] = self._pending_actual_fill()
        strategy._pending_actual_fills[order_id]["_pending_reason"] = "unit test"

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == order_id for record in unresolved))

    def test_submitted_order_intent_is_unresolved_until_consumed(self):
        strategy = self._new_strategy()
        order_id = "intent-order"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")

        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        self.assertIn(order_id, strategy._submitted_order_intents)
        unresolved = strategy._unresolved_settlement_unknowns()
        self.assertTrue(
            any(
                record["order_id"] == order_id
                and record["settlement_source"] == "SUBMITTED_ORDER_INTENT"
                for record in unresolved
            )
        )
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["submitted_order_intents"][order_id]["trade_label"], "YES (UP)")

    def test_submitted_order_intent_no_order_status_is_not_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-no-order"] = {
            "status": "SUBMISSION_NOT_SEEN",
            "needs_reconciliation": False,
            "submission_not_seen_at": datetime.now(timezone.utc).isoformat(),
            "submission_not_seen_reason": "unit test no order",
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertFalse(any(record["order_id"] == "intent-no-order" for record in unresolved))

    def test_submitted_order_intent_no_order_without_audit_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-no-order-no-audit"] = {
            "status": "SUBMISSION_NOT_SEEN",
            "needs_reconciliation": False,
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-no-order-no-audit" for record in unresolved))

    def test_submitted_order_intent_no_order_with_naive_audit_time_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-no-order-naive-audit"] = {
            "status": "SUBMISSION_NOT_SEEN",
            "needs_reconciliation": False,
            "submission_not_seen_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "submission_not_seen_reason": "unit test no order",
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-no-order-naive-audit" for record in unresolved))

    def test_submitted_order_intent_non_object_entry_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-malformed"] = "not-an-object"

        unresolved = strategy._unresolved_settlement_unknowns()

        matching = [record for record in unresolved if record["order_id"] == "intent-malformed"]
        self.assertEqual(len(matching), 1)
        self.assertIn("not a JSON object", matching[0]["unknown_reason"])

    def test_submitted_order_intent_no_order_with_zero_fill_but_no_audit_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-no-order-zero-only"] = {
            "status": "SUBMISSION_NOT_SEEN",
            "needs_reconciliation": False,
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-no-order-zero-only" for record in unresolved))

    def test_submitted_order_intent_terminal_no_fill_statuses_are_not_unresolved(self):
        strategy = self._new_strategy()
        for status in (
            "ORDER_DENIED_NO_FILL",
            "ORDER_REJECTED_NO_FILL",
            "ORDER_CANCELED_NO_FILL",
            "ORDER_EXPIRED_NO_FILL",
        ):
            strategy._submitted_order_intents[f"intent-{status}"] = {
                "status": status,
                "needs_reconciliation": False,
                "terminal_no_fill_event": {
                    "event_type": "_TerminalNoFillEvent",
                    "raw_event": {
                        "event_type": "_TerminalNoFillEvent",
                        "repr": "<_TerminalNoFillEvent>",
                        "fields": {"filled_qty": "0"},
                    },
                },
                "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
            }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertFalse(
            any(record["order_id"].startswith("intent-ORDER_") for record in unresolved)
        )

    def test_terminal_no_fill_status_without_raw_event_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-no-raw-event"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-no-raw-event" for record in unresolved))

    def test_terminal_no_fill_status_without_raw_zero_field_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-empty-raw-fields"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
            "terminal_no_fill_event": {
                "event_type": "_TerminalNoFillEvent",
                "raw_event": {
                    "event_type": "_TerminalNoFillEvent",
                    "repr": "<_TerminalNoFillEvent>",
                    "fields": {},
                },
            },
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-empty-raw-fields" for record in unresolved))

    def test_terminal_no_fill_status_with_mismatched_raw_zero_field_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-mismatch-raw-field"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
            "terminal_no_fill_event": {
                "event_type": "_TerminalNoFillEvent",
                "raw_event": {
                    "event_type": "_TerminalNoFillEvent",
                    "repr": "<_TerminalNoFillEvent>",
                    "fields": {"filled_qty": "1"},
                },
            },
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-mismatch-raw-field" for record in unresolved))

    def test_terminal_no_fill_status_with_extra_raw_fill_evidence_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-extra-raw-fill"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
            "terminal_no_fill_event": {
                "event_type": "_TerminalNoFillEvent",
                "raw_event": {
                    "event_type": "_TerminalNoFillEvent",
                    "repr": "<_TerminalNoFillEvent>",
                    "fields": {
                        "filled_qty": "0",
                        "last_qty": "1",
                        "last_px": "0.60",
                    },
                },
            },
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-extra-raw-fill" for record in unresolved))

    def test_terminal_no_fill_status_with_zero_raw_price_fields_is_resolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-zero-raw-price"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
            "terminal_no_fill_event": {
                "event_type": "_TerminalNoFillEvent",
                "raw_event": {
                    "event_type": "_TerminalNoFillEvent",
                    "repr": "<_TerminalNoFillEvent>",
                    "fields": {
                        "filled_qty": "0",
                        "last_px": "0",
                        "avg_px": "0",
                    },
                },
            },
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertFalse(any(record["order_id"] == "intent-denied-zero-raw-price" for record in unresolved))

    def test_terminal_no_fill_status_with_top_level_fill_evidence_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-top-level-fill"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
            "terminal_no_fill_event": {
                "event_type": "_TerminalNoFillEvent",
                "last_qty": "1",
                "raw_event": {
                    "event_type": "_TerminalNoFillEvent",
                    "repr": "<_TerminalNoFillEvent>",
                    "fields": {"filled_qty": "0"},
                },
            },
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-top-level-fill" for record in unresolved))

    def test_terminal_no_fill_status_with_top_level_fill_identifier_is_unresolved(self):
        strategy = self._new_strategy()
        for key in self.bot.ACTUAL_FILL_UNIQUE_KEY_FIELDS:
            with self.subTest(key=key):
                order_id = f"intent-denied-top-level-{key}"
                strategy._submitted_order_intents[order_id] = {
                    "status": "ORDER_DENIED_NO_FILL",
                    "needs_reconciliation": False,
                    "terminal_no_fill_event": {
                        "event_type": "_TerminalNoFillEvent",
                        key: f"{key}-hidden",
                        "raw_event": {
                            "event_type": "_TerminalNoFillEvent",
                            "repr": "<_TerminalNoFillEvent>",
                            "fields": {"filled_qty": "0"},
                        },
                    },
                    "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
                }

                unresolved = strategy._unresolved_settlement_unknowns()

                self.assertTrue(any(record["order_id"] == order_id for record in unresolved))

    def test_terminal_no_fill_status_with_instance_attr_fill_evidence_is_unresolved(self):
        strategy = self._new_strategy()
        for key in self.bot.ACTUAL_FILL_UNIQUE_KEY_FIELDS:
            with self.subTest(key=key):
                order_id = f"intent-denied-instance-{key}"
                strategy._submitted_order_intents[order_id] = {
                    "status": "ORDER_DENIED_NO_FILL",
                    "needs_reconciliation": False,
                    "terminal_no_fill_event": {
                        "event_type": "_TerminalNoFillEvent",
                        "raw_event": {
                            "event_type": "_TerminalNoFillEvent",
                            "repr": "<_TerminalNoFillEvent>",
                            "fields": {"filled_qty": "0"},
                            "instance_attrs": {key: f"{key}-hidden"},
                        },
                    },
                    "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
                }

                unresolved = strategy._unresolved_settlement_unknowns()

                self.assertTrue(any(record["order_id"] == order_id for record in unresolved))

    def test_terminal_no_fill_status_without_zero_evidence_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-no-evidence"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-no-evidence" for record in unresolved))

    def test_terminal_no_fill_status_with_needs_reconciliation_true_is_unresolved(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-explicit-unresolved"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": True,
            "terminal_no_fill_zero_quantity_evidence": {"filled_qty": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-explicit-unresolved" for record in unresolved))

    def test_terminal_no_fill_status_rejects_unknown_zero_evidence_key(self):
        strategy = self._new_strategy()
        strategy._submitted_order_intents["intent-denied-bad-evidence"] = {
            "status": "ORDER_DENIED_NO_FILL",
            "needs_reconciliation": False,
            "terminal_no_fill_zero_quantity_evidence": {"not_a_fill_field": "0"},
        }

        unresolved = strategy._unresolved_settlement_unknowns()

        self.assertTrue(any(record["order_id"] == "intent-denied-bad-evidence" for record in unresolved))

    def test_terminal_no_fill_event_preserves_submitted_intent_audit(self):
        strategy = self._new_strategy()
        order_id = "intent-denied-audit"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _DeniedEvent:
            client_order_id = order_id
            venue_order_id = "0xdenied"
            reason = "no match"
            filled_qty = Decimal("0")

        strategy.on_order_denied(_DeniedEvent())

        self.assertNotIn(order_id, strategy._submitted_positions)
        self.assertIn(order_id, strategy._submitted_order_intents)
        intent = strategy._submitted_order_intents[order_id]
        self.assertEqual(intent["status"], "ORDER_DENIED_NO_FILL")
        self.assertFalse(intent["needs_reconciliation"])
        self.assertEqual(intent["terminal_no_fill_event"]["venue_order_id"], "0xdenied")
        raw_event = intent["terminal_no_fill_event"]["raw_event"]
        self.assertEqual(raw_event["event_type"], "_DeniedEvent")
        self.assertEqual(raw_event["fields"]["venue_order_id"], "0xdenied")
        self.assertEqual(raw_event["fields"]["filled_qty"], "0")
        unresolved = strategy._unresolved_settlement_unknowns()
        self.assertFalse(any(record["order_id"] == order_id for record in unresolved))
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["submitted_order_intents"][order_id]["status"], "ORDER_DENIED_NO_FILL")

    def test_terminal_no_fill_clears_after_order_balance_staleness(self):
        strategy = self._new_strategy()
        strategy.on_event(self._account_state("10.00"))
        order_id = "intent-denied-clears-balance"
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            order_id,
        )
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _DeniedEvent:
            client_order_id = order_id
            venue_order_id = "0xdenied-clear-stale"
            reason = "no match"
            filled_qty = Decimal("0")

        strategy.on_order_denied(_DeniedEvent())

        self.assertIsNone(strategy._balance_stale_reason)
        self.assertNotIn(order_id, strategy._submitted_positions)
        self.assertNotIn(order_id, strategy.risk_engine._positions)
        self.assertEqual(
            strategy._submitted_order_intents[order_id]["status"],
            "ORDER_DENIED_NO_FILL",
        )

    def test_untracked_terminal_no_fill_does_not_clear_after_order_balance_staleness(self):
        strategy = self._new_strategy()
        strategy.on_event(self._account_state("10.00"))
        stale_order_id = "submitted-order-awaiting-balance"
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            stale_order_id,
        )

        class _DeniedEvent:
            client_order_id = "unknown-denied-no-fill"
            venue_order_id = "0xunknown-denied-no-fill"
            reason = "no match"
            filled_qty = Decimal("0")

        strategy.on_order_denied(_DeniedEvent())

        self.assertEqual(
            strategy._balance_stale_reason,
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
        )
        self.assertEqual(
            strategy._account_balance_tracker.balance_stale_order_id,
            stale_order_id,
        )
        rec = self.bot.DecisionRecord(current_price=Decimal("0.70"))
        size = strategy._resolve_position_size_usd(is_simulation=False, rec=rec)
        self.assertIsNone(size)
        self.assertEqual(rec.fields["rejected_at_gate"], "stale_balance_after_order")

    def test_duplicate_no_fill_does_not_clear_newer_order_balance_staleness(self):
        strategy = self._new_strategy()
        strategy.on_event(self._account_state("10.00"))
        first_order_id = "older-denied-order"
        first_meta = self._live_trade_meta(order_id=first_order_id, token_id="token-first")
        first_meta.pop("filled_qty")
        first_meta.pop("filled_notional")
        strategy._submitted_positions[first_order_id] = dict(first_meta)
        strategy.risk_engine.add_position(first_order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(first_order_id, first_meta, "ask")
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            first_order_id,
        )

        class _FirstDeniedEvent:
            client_order_id = first_order_id
            venue_order_id = "0xolder-denied-order"
            reason = "no match"
            filled_qty = Decimal("0")

        strategy.on_order_denied(_FirstDeniedEvent())
        self.assertIsNone(strategy._balance_stale_reason)

        newer_order_id = "newer-filled-order"
        strategy._mark_balance_stale(
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
            newer_order_id,
        )
        strategy.on_order_denied(_FirstDeniedEvent())

        self.assertEqual(
            strategy._balance_stale_reason,
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_ORDER,
        )
        self.assertEqual(
            strategy._account_balance_tracker.balance_stale_order_id,
            newer_order_id,
        )

    def test_terminal_event_with_fill_evidence_blocks_instead_of_marking_no_fill(self):
        strategy = self._new_strategy()
        order_id = "intent-denied-with-fill"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _DeniedEvent:
            client_order_id = order_id
            venue_order_id = "0xdenied-fill"
            reason = "ambiguous"
            last_qty = Decimal("1")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "fill evidence"):
            strategy.on_order_denied(_DeniedEvent())

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._submitted_order_intents)
        self.assertIn(order_id, strategy._submitted_positions)
        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "terminal_positive_fill_evidence_without_price")
        self.assertEqual(unknown["venue_order_id"], "0xdenied-fill")

    def test_terminal_no_fill_with_fill_identifier_blocks(self):
        for key in self.bot.ACTUAL_FILL_UNIQUE_KEY_FIELDS:
            with self.subTest(key=key):
                strategy = self._new_strategy()
                try:
                    order_id = f"intent-denied-{key}"
                    meta = self._live_trade_meta(order_id=order_id, token_id=f"token-{key}")
                    strategy._submitted_positions[order_id] = dict(meta)
                    strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
                    strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

                    class _DeniedEvent:
                        client_order_id = order_id
                        venue_order_id = f"0xdenied{key}"
                        reason = "identifier evidence"
                        filled_qty = Decimal("0")

                    setattr(_DeniedEvent, key, f"{key}-value")

                    with self.assertRaisesRegex(self.bot.SettlementLedgerError, "fill evidence"):
                        strategy.on_order_denied(_DeniedEvent())

                    self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
                    self.assertEqual(strategy._submitted_order_intents[order_id]["status"], "INTENT_PERSISTED")
                finally:
                    strategy._release_live_trade_ledger_lock()

    def test_cancel_with_terminal_fill_details_records_fill_not_no_fill(self):
        strategy = self._new_strategy()
        order_id = "limit-cancel-terminal-fill"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-terminal-fill")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalfill"
            reason = "FAK partially filled before cancel"
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        strategy.on_order_canceled(_CanceledEvent())

        self.assertIn(order_id, strategy._open_live_trades)
        recorded = strategy._open_live_trades[order_id]
        self.assertEqual(recorded["filled_qty"], Decimal("4"))
        self.assertEqual(recorded["entry_price"], Decimal("0.60"))
        self.assertEqual(recorded["venue_order_id"], "0xterminalfill")
        self.assertNotIn(order_id, strategy._submitted_positions)
        self.assertNotIn(order_id, strategy._submitted_order_intents)
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["open"][order_id]["venue_order_id"], "0xterminalfill")

    def test_rejected_with_terminal_fill_details_does_not_reopen_trade_window(self):
        strategy = self._new_strategy()
        order_id = "limit-rejected-terminal-fill"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-rejected-fill")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")
        strategy.last_trade_time = ("market", 123)

        class _RejectedEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalrejectfill"
            reason = "FAK partially filled"
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        strategy.on_order_rejected(_RejectedEvent())

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(
            strategy._open_live_trades[order_id]["venue_order_id"],
            "0xterminalrejectfill",
        )
        self.assertEqual(strategy.last_trade_time, ("market", 123))
        self.assertNotIn(order_id, strategy._submitted_order_intents)
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["open"][order_id]["venue_order_id"], "0xterminalrejectfill")

    def test_terminal_fill_limit_violation_unknown_preserves_venue_order_id(self):
        strategy = self._new_strategy()
        order_id = "limit-terminal-fill-violation"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-terminal-violation")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalviolation"
            reason = "FAK fill above local cap"
            last_qty = Decimal("4")
            last_px = Decimal("0.63")

        strategy.on_order_canceled(_CanceledEvent())

        self.assertNotIn(order_id, strategy._open_live_trades)
        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["venue_order_id"], "0xterminalviolation")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xterminalviolation")
        self.assertEqual(
            unknown["unknown_reason"],
            "limit_ioc_fill_price_above_submitted_limit:vwap=0.63,submitted_limit_price=0.62",
        )
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["settled"][-1]["venue_order_id"], "0xterminalviolation")

    def test_terminal_fill_invalid_direction_unknown_preserves_venue_order_id(self):
        strategy = self._new_strategy()
        order_id = "terminal-fill-invalid-direction"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-invalid-direction")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta["direction"] = "sideways"
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalbaddir"
            reason = "terminal fill with malformed local direction"
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["venue_order_id"], "0xterminalbaddir")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xterminalbaddir")
        self.assertEqual(unknown["unknown_reason"], "invalid_fill_direction_metadata")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_fill_blocked_ledger_unknown_preserves_venue_order_id(self):
        strategy = self._new_strategy()
        order_id = "terminal-fill-blocked-ledger"
        strategy._settlement_ledger_blocked_reason = "unit test pre-existing block"

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalblocked"
            reason = "terminal fill while ledger blocked"
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "ledger is blocked"):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["venue_order_id"], "0xterminalblocked")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xterminalblocked")
        self.assertEqual(
            unknown["unknown_reason"],
            "live_fill_received_while_settlement_ledger_blocked",
        )

    def test_terminal_fill_impossible_price_unknown_preserves_venue_order_id(self):
        strategy = self._new_strategy()
        order_id = "terminal-fill-price-above-one"

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalpriceaboveone"
            reason = "terminal fill price above one"
            last_qty = Decimal("4")
            last_px = Decimal("1.01")

        strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["venue_order_id"], "0xterminalpriceaboveone")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xterminalpriceaboveone")
        self.assertEqual(unknown["unknown_reason"], "fill_price_above_one_from_nautilus")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_fill_submitted_venue_conflict_creates_durable_unknown(self):
        strategy = self._new_strategy()
        order_id = "terminal-fill-submitted-venue-conflict"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-submitted-conflict")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta["venue_order_id"] = "0xtrackedvenue"
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalvenue"
            reason = "terminal venue conflict"
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "conflicts on venue_order_id"):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "fill_metadata_conflict")
        self.assertEqual(unknown["venue_conflict_payload_venue_order_id"], "0xterminalvenue")
        self.assertEqual(unknown["venue_conflict_tracked_venue_order_id"], "0xtrackedvenue")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xterminalvenue")
        self.assertEqual(unknown["raw_callback_payload"]["tracked_venue_order_id"], "0xtrackedvenue")

    def test_terminal_fill_open_venue_conflict_creates_durable_unknown(self):
        strategy = self._new_strategy()
        order_id = "terminal-fill-open-venue-conflict"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-open-conflict")
        meta["venue_order_id"] = "0xopenvenue"
        strategy._open_live_trades[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.48"), Decimal("0.62"), "buy_yes")

        class _ExpiredEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalopenvenue"
            reason = "terminal open venue conflict"
            last_qty = Decimal("1")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "conflicts on venue_order_id"):
            strategy.on_order_expired(_ExpiredEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "fill_metadata_conflict")
        self.assertEqual(unknown["venue_conflict_payload_venue_order_id"], "0xterminalopenvenue")
        self.assertEqual(unknown["venue_conflict_tracked_venue_order_id"], "0xopenvenue")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xterminalopenvenue")
        self.assertEqual(unknown["raw_callback_payload"]["tracked_venue_order_id"], "0xopenvenue")

    def test_terminal_no_fill_open_venue_conflict_creates_durable_unknown(self):
        strategy = self._new_strategy()
        order_id = "terminal-no-fill-open-venue-conflict"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-no-fill-open-conflict")
        meta["venue_order_id"] = "0xopenvenue"
        strategy._open_live_trades[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.48"), Decimal("0.62"), "buy_yes")

        class _ExpiredEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalother"
            reason = "terminal zero-fill venue conflict"
            filled_qty = Decimal("0")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "conflicts on venue_order_id"):
            strategy.on_order_expired(_ExpiredEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "terminal_no_fill_metadata_conflict")
        self.assertEqual(unknown["venue_conflict_payload_venue_order_id"], "0xterminalother")
        self.assertEqual(unknown["venue_conflict_tracked_venue_order_id"], "0xopenvenue")
        self.assertEqual(unknown["raw_callback_payload"]["fill_metadata_conflict_key"], "venue_order_id")
        self.assertEqual(unknown["raw_callback_payload"]["terminal_venue_order_id"], "0xterminalother")
        self.assertEqual(unknown["raw_callback_payload"]["tracked_venue_order_id"], "0xopenvenue")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xterminalother")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_no_fill_pending_actual_venue_conflict_creates_durable_unknown(self):
        strategy = self._new_strategy()
        order_id = "terminal-no-fill-pending-venue-conflict"
        pending_fill = self._pending_actual_fill(
            venue_order_id="0xopenvenue",
        )
        pending_fill["slug"] = "slug-pending"
        pending_fill["instrument_id"] = "cond-pending-token-pending.POLYMARKET"
        expected_fill_entries = copy.deepcopy(pending_fill["fills"])
        strategy._pending_actual_fills[order_id] = pending_fill

        class _ExpiredEvent:
            client_order_id = order_id
            venue_order_id = "0xterminalother"
            reason = "terminal zero-fill venue conflict"
            filled_qty = Decimal("0")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "conflicts on venue_order_id"):
            strategy.on_order_expired(_ExpiredEvent())

        self.assertNotIn(order_id, strategy._pending_actual_fills)
        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "terminal_no_fill_metadata_conflict")
        self.assertEqual(unknown["venue_conflict_payload_venue_order_id"], "0xterminalother")
        self.assertEqual(unknown["venue_conflict_tracked_venue_order_id"], "0xopenvenue")
        self.assertEqual(unknown["condition_id"], "cond-pending")
        self.assertEqual(unknown["token_id"], "token-pending")
        self.assertEqual(unknown["slug"], "slug-pending")
        self.assertEqual(unknown["instrument_id"], "cond-pending-token-pending.POLYMARKET")
        self.assertEqual(unknown["raw_callback_payload"]["recorded_fill_source"], "pending_actual_fills")
        self.assertEqual(unknown["raw_callback_payload"]["recorded_filled_qty"], "4")
        self.assertEqual(unknown["raw_callback_payload"]["recorded_filled_notional"], "2.00")
        self.assertEqual(unknown["raw_callback_payload"]["recorded_vwap"], "0.50")
        self.assertEqual(
            unknown["raw_callback_payload"]["recorded_fill_identity"],
            {
                "venue_order_id": "0xopenvenue",
                "condition_id": "cond-pending",
                "token_id": "token-pending",
                "slug": "slug-pending",
                "instrument_id": "cond-pending-token-pending.POLYMARKET",
            },
        )
        self.assertEqual(unknown["raw_callback_payload"]["recorded_fill_entries"], expected_fill_entries)
        self.assertTrue(unknown["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_order_filled_submitted_venue_conflict_creates_durable_unknown(self):
        strategy = self._new_strategy()
        order_id = "normal-fill-submitted-venue-conflict"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-normal-conflict")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta["venue_order_id"] = "0xtrackednormalvenue"
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            venue_order_id = "0xnormalfillvenue"
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "conflicts on venue_order_id"):
            strategy.on_order_filled(_FillEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "fill_metadata_conflict")
        self.assertEqual(unknown["venue_conflict_payload_venue_order_id"], "0xnormalfillvenue")
        self.assertEqual(unknown["venue_conflict_tracked_venue_order_id"], "0xtrackednormalvenue")
        self.assertEqual(unknown["raw_callback_payload"]["venue_order_id"], "0xnormalfillvenue")
        self.assertEqual(unknown["raw_callback_payload"]["tracked_venue_order_id"], "0xtrackednormalvenue")

    def test_order_filled_instrument_token_conflict_creates_durable_unknown(self):
        strategy = self._new_strategy()
        order_id = "normal-fill-instrument-token-conflict"
        meta = self._limit_ioc_trade_meta(
            order_id=order_id,
            token_id="tokennormalinstrument",
        )
        meta["condition_id"] = "conda"
        meta["instrument_id"] = "conda-tokennormalinstrument.POLYMARKET"
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            instrument_id = "conda-tokenfromevent.POLYMARKET"
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "conflicts on token_id"):
            strategy.on_order_filled(_FillEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "fill_metadata_conflict")
        self.assertEqual(unknown["instrument_id"], "conda-tokenfromevent.POLYMARKET")
        self.assertEqual(unknown["raw_callback_payload"]["fill_metadata_conflict_key"], "token_id")
        self.assertEqual(unknown["raw_callback_payload"]["tracked_token_id"], "tokennormalinstrument")
        self.assertEqual(unknown["raw_callback_payload"]["terminal_token_id"], "tokenfromevent")
        self.assertEqual(unknown["raw_callback_payload"]["instrument_id"], "conda-tokenfromevent.POLYMARKET")

    def test_order_filled_event_identity_source_conflict_creates_durable_unknown(self):
        strategy = self._new_strategy()
        order_id = "normal-fill-event-identity-conflict"
        meta = self._limit_ioc_trade_meta(
            order_id=order_id,
            token_id="tokena",
        )
        meta["condition_id"] = "conda"
        meta["instrument_id"] = "conda-tokena.POLYMARKET"
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            instrument_id = "conda-tokena.POLYMARKET"
            info = {"asset_id": "tokenb", "market": "conda"}
            last_qty = Decimal("4")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "conflicts on token_id"):
            strategy.on_order_filled(_FillEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "fill_metadata_identity_conflict")
        self.assertEqual(unknown["order_id"], order_id)
        self.assertEqual(unknown["instrument_id"], "conda-tokena.POLYMARKET")
        self.assertIn("conflicts on token_id", unknown["raw_callback_payload"]["terminal_metadata_error"])
        terminal_fields = unknown["raw_callback_payload"]["terminal_event"]["raw_event"]["fields"]
        self.assertEqual(terminal_fields["info"]["asset_id"], "tokenb")
        self.assertEqual(terminal_fields["instrument_id"], "conda-tokena.POLYMARKET")

    def test_rejected_missing_reason_zero_fill_does_not_persist_no_fill_audit(self):
        strategy = self._new_strategy()
        order_id = "rejected-missing-reason-zero-fill"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-rejected-no-reason")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _RejectedEvent:
            client_order_id = order_id
            venue_order_id = "0xrejectednoreason"
            filled_qty = Decimal("0")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "missing reason"):
            strategy.on_order_rejected(_RejectedEvent())

        self.assertEqual(strategy._submitted_order_intents[order_id]["status"], "INTENT_PERSISTED")
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertIn(order_id, strategy.risk_engine._positions)
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_cancel_after_pending_actual_fill_does_not_mark_no_fill(self):
        strategy = self._new_strategy()
        order_id = "limit-cancel-after-pending-fill"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-partial-cancel")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-partial-before-cancel",
                "filled_qty": Decimal("4"),
                "vwap": Decimal("0.60"),
                "venue_order_id": "0xpartialcancel",
                "condition_id": "cond-a",
                "token_id": "token-partial-cancel",
            },
        )

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xpartialcancel"
            reason = "FAK unfilled remainder canceled"
            filled_qty = Decimal("0")

        strategy.on_order_canceled(_CanceledEvent())

        self.assertIn(order_id, strategy._submitted_positions)
        self.assertIn(order_id, strategy._pending_actual_fills)
        self.assertEqual(strategy._submitted_order_intents[order_id]["status"], "INTENT_PERSISTED")
        self.assertIn(order_id, strategy.risk_engine._positions)
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)

    def test_expire_after_recorded_fill_does_not_mark_no_fill(self):
        strategy = self._new_strategy()
        order_id = "limit-expire-after-recorded-fill"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-partial-expire")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertNotIn(order_id, strategy._submitted_order_intents)

        class _ExpiredEvent:
            client_order_id = order_id
            venue_order_id = "0xpartialexpire"
            reason = "FAK remainder expired"
            filled_qty = Decimal("0")

        strategy.on_order_expired(_ExpiredEvent())

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertNotIn(order_id, strategy._submitted_order_intents)
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)

    def test_cancel_cumulative_filled_qty_after_recorded_fill_does_not_double_count(self):
        strategy = self._new_strategy()
        order_id = "limit-cancel-cumulative-after-fill"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-cumulative-confirm")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xcumulativeconfirm"
            reason = "FAK terminal cumulative fill confirmation"
            filled_qty = Decimal("4")
            avg_px = Decimal("0.60")

        strategy.on_order_canceled(_CanceledEvent())

        recorded = strategy._open_live_trades[order_id]
        self.assertEqual(recorded["filled_qty"], Decimal("4"))
        self.assertEqual(recorded["filled_notional"], Decimal("2.40"))
        self.assertEqual(recorded["entry_price"], Decimal("0.60"))
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_mixed_incremental_exceeding_cumulative_delta_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-mixed-terminal-delta-conflict"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-mixed-delta-conflict")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xmixeddeltaconflict"
            reason = "terminal mixed fields exceed cumulative delta"
            filled_qty = Decimal("5")
            avg_px = Decimal("0.604")
            last_qty = Decimal("3")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_fill_conflicting_evidence_exceeds_cumulative_delta",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(
            unknown["unknown_reason"],
            "terminal_fill_conflicting_evidence_exceeds_cumulative_delta",
        )
        self.assertEqual(unknown["raw_callback_payload"]["selected_terminal_delta_qty"], "1")
        self.assertEqual(unknown["raw_callback_payload"]["terminal_fill_field_source"], "last_qty/last_px")

    def test_terminal_incremental_last_qty_with_avg_price_after_recorded_fill_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-last-qty-avg-after-fill"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-last-avg")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xlastavg"
            reason = "terminal last quantity with average price"
            last_qty = Decimal("1")
            avg_px = Decimal("0.604")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_incremental_fill_uses_average_price_after_recorded_fill",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(
            unknown["unknown_reason"],
            "terminal_incremental_fill_uses_average_price_after_recorded_fill",
        )
        self.assertEqual(unknown["raw_callback_payload"]["terminal_fill_field_source"], "last_qty/avg_px")

    def test_terminal_incremental_selection_with_cumulative_last_price_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-incremental-with-cumulative-last-price"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-cumulative-last-price")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xcumulativelastprice"
            reason = "terminal incremental plus cumulative last price"
            last_qty = Decimal("1")
            last_px = Decimal("0.60")
            filled_qty = Decimal("5")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_cumulative_fill_requires_average_price",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "terminal_cumulative_fill_requires_average_price")
        self.assertEqual(unknown["raw_callback_payload"]["selected_terminal_fill_field_source"], "last_qty/last_px")
        self.assertEqual(unknown["raw_callback_payload"]["terminal_fill_field_source"], "filled_qty/last_px")

    def test_terminal_positive_last_qty_with_zero_cumulative_qty_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-positive-last-zero-cumulative"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-zero-cumulative")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xzerocumulative"
            reason = "terminal positive last fill but zero cumulative"
            last_qty = Decimal("1")
            last_px = Decimal("0.60")
            filled_qty = Decimal("0")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_cumulative_quantity_below_selected_fill_evidence",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(
            unknown["unknown_reason"],
            "terminal_cumulative_quantity_below_selected_fill_evidence",
        )
        self.assertEqual(unknown["raw_callback_payload"]["terminal_quantity_field"], "filled_qty")
        self.assertEqual(unknown["raw_callback_payload"]["terminal_quantity_value"], "0")

    def test_terminal_malformed_fill_field_after_recorded_fill_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-terminal-malformed-fill-field"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-malformed-terminal")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xmalformedterminal"
            reason = "terminal malformed fill field"
            last_qty = "not-a-decimal"
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_fill_evidence_parse_error",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "terminal_fill_evidence_parse_error")
        self.assertIn("not-a-decimal", unknown["raw_callback_payload"]["terminal_parse_error"])
        self.assertEqual(
            unknown["raw_callback_payload"]["terminal_event"]["raw_event"]["fields"]["last_qty"],
            "not-a-decimal",
        )

    def test_cancel_cumulative_filled_qty_after_recorded_fill_records_only_delta(self):
        strategy = self._new_strategy()
        order_id = "limit-cancel-cumulative-delta"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-cumulative-delta")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xcumulativedelta"
            reason = "FAK terminal cumulative fill delta"
            last_qty = Decimal("1")
            filled_qty = Decimal("5")
            avg_px = Decimal("0.604")

        strategy.on_order_canceled(_CanceledEvent())

        recorded = strategy._open_live_trades[order_id]
        self.assertEqual(recorded["filled_qty"], Decimal("5"))
        self.assertEqual(recorded["filled_notional"], Decimal("3.020"))
        self.assertEqual(recorded["entry_price"], Decimal("0.604"))
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_cumulative_fill_without_avg_price_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-cumulative-without-avg"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-cumulative-no-avg")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xcumulativenoavg"
            reason = "terminal cumulative quantity without VWAP"
            filled_qty = Decimal("4")
            last_px = Decimal("0.60")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_cumulative_fill_requires_average_price",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "terminal_cumulative_fill_requires_average_price")
        self.assertEqual(unknown["venue_order_id"], "0xcumulativenoavg")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_mixed_cumulative_and_unsafe_last_price_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-mixed-terminal-unsafe-last"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-mixed-unsafe-last")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xmixedunsafe"
            reason = "terminal mixed fields with unsafe last price"
            filled_qty = Decimal("4")
            avg_px = Decimal("0.60")
            last_qty = Decimal("1")
            last_px = Decimal("0.63")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_fill_conflicting_evidence",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertTrue(unknown["unknown_reason"].startswith("terminal_fill_conflicting_evidence"))
        self.assertEqual(unknown["raw_callback_payload"]["terminal_fill_field_source"], "last_qty/last_px")
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_equal_cumulative_conflicting_vwap_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-equal-cumulative-vwap-conflict"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-vwap-conflict")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xvwapconflict"
            reason = "terminal cumulative VWAP conflicts with recorded fill"
            filled_qty = Decimal("4")
            avg_px = Decimal("0.61")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_cumulative_fill_conflicts_with_recorded_accounting",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(
            unknown["unknown_reason"],
            "terminal_cumulative_fill_conflicts_with_recorded_accounting",
        )

    def test_terminal_equal_cumulative_last_price_only_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-equal-cumulative-last-only"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-last-only")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xlastonly"
            reason = "terminal cumulative last price only"
            filled_qty = Decimal("4")
            last_px = Decimal("0.63")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_cumulative_fill_equal_requires_average_price",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(
            unknown["unknown_reason"],
            "terminal_cumulative_fill_equal_requires_average_price",
        )

    def test_terminal_positive_cumulative_quantity_without_price_blocks_unknown(self):
        strategy = self._new_strategy()
        order_id = "limit-positive-cumulative-no-price"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-no-price")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")

        class _FillEvent:
            client_order_id = order_id
            condition_id = meta["condition_id"]
            token_id = meta["token_id"]
            last_px = Decimal("0.60")
            last_qty = Decimal("4")

        strategy.on_order_filled(_FillEvent())

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xnoprice"
            reason = "terminal positive quantity without price"
            filled_qty = Decimal("5")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_positive_fill_evidence_without_price",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["unknown_reason"], "terminal_positive_fill_evidence_without_price")
        self.assertEqual(
            unknown["raw_callback_payload"]["terminal_positive_fill_quantities"]["filled_qty"],
            "5",
        )

    def test_cancel_cumulative_delta_after_pending_actual_fill_requires_repair(self):
        strategy = self._new_strategy()
        order_id = "limit-cumulative-delta-after-pending-actual"
        meta = self._limit_ioc_trade_meta(order_id=order_id, token_id="token-pending-cumulative")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("4.96"), Decimal("0.62"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        strategy._handle_actual_fill(
            order_id,
            {
                "status": "ok",
                "trade_id": "trade-pending-cumulative",
                "filled_qty": Decimal("4"),
                "vwap": Decimal("0.60"),
                "venue_order_id": "0xpendingcumulative",
                "condition_id": "cond-a",
                "token_id": "token-pending-cumulative",
            },
        )

        class _CanceledEvent:
            client_order_id = order_id
            venue_order_id = "0xpendingcumulative"
            reason = "FAK terminal cumulative fill delta after actual-fill callback"
            filled_qty = Decimal("5")
            avg_px = Decimal("0.604")

        with self.assertRaisesRegex(
            self.bot.SettlementLedgerError,
            "terminal_cumulative_fill_delta_requires_recorded_open_trade",
        ):
            strategy.on_order_canceled(_CanceledEvent())

        self.assertIn(order_id, strategy._pending_actual_fills)
        self.assertTrue(strategy._pending_actual_fills[order_id]["requires_external_fill_repair"])
        self.assertEqual(
            strategy._pending_actual_fills[order_id]["external_fill_repair_reason"],
            "terminal_cumulative_fill_delta_requires_recorded_open_trade",
        )
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_terminal_event_without_zero_fill_evidence_blocks(self):
        strategy = self._new_strategy()
        order_id = "intent-denied-no-fill-evidence"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        class _DeniedEvent:
            client_order_id = order_id
            venue_order_id = "0xdenied-no-zero"
            reason = "no match"

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "lacks verified zero-fill quantity"):
            strategy.on_order_denied(_DeniedEvent())

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(strategy._submitted_order_intents[order_id]["status"], "INTENT_PERSISTED")
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertIn(order_id, strategy.risk_engine._positions)

    def test_terminal_no_fill_malformed_submitted_intent_blocks_explicitly(self):
        strategy = self._new_strategy()
        order_id = "intent-denied-malformed"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._submitted_order_intents[order_id] = "not-an-object"

        class _DeniedEvent:
            client_order_id = order_id
            venue_order_id = "0xdenied-malformed"
            reason = "no match"
            filled_qty = Decimal("0")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "not a JSON object"):
            strategy.on_order_denied(_DeniedEvent())

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(strategy._submitted_order_intents[order_id], "not-an-object")
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertIn(order_id, strategy.risk_engine._positions)

    def test_terminal_no_fill_audit_save_failure_preserves_local_exposure(self):
        strategy = self._new_strategy()
        order_id = "intent-denied-save-fails"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")
        original_write = strategy._write_live_trade_ledger_state

        def fail_write(_state):
            raise OSError("disk full")

        class _DeniedEvent:
            client_order_id = order_id
            venue_order_id = "0xdenied-save-fails"
            reason = "no match"
            filled_qty = Decimal("0")

        strategy._write_live_trade_ledger_state = fail_write
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to persist terminal"):
                strategy.on_order_denied(_DeniedEvent())
        finally:
            strategy._write_live_trade_ledger_state = original_write

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(strategy._submitted_order_intents[order_id]["status"], "INTENT_PERSISTED")
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertIn(order_id, strategy.risk_engine._positions)

    def test_submitted_order_intent_save_failure_does_not_mutate_memory(self):
        strategy = self._new_strategy()
        order_id = "intent-save-fails"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        original_write = strategy._write_live_trade_ledger_state

        def fail_write(_state):
            raise OSError("disk full")

        strategy._write_live_trade_ledger_state = fail_write
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to save live trade ledger"):
                strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")
        finally:
            strategy._write_live_trade_ledger_state = original_write

        self.assertNotIn(order_id, strategy._submitted_order_intents)
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_fill_consumes_submitted_order_intent_atomically(self):
        strategy = self._new_strategy()
        order_id = "intent-fill"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._persist_submitted_order_intent_locked(order_id, meta, "ask")

        self.assertTrue(
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        )

        recorded = strategy._open_live_trades[order_id]
        self.assertNotIn(order_id, strategy._submitted_order_intents)
        self.assertEqual(recorded["submitted_order_intent"]["trade_label"], "YES (UP)")
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(order_id, data["submitted_order_intents"])
        self.assertEqual(data["open"][order_id]["submitted_order_intent"]["trade_label"], "YES (UP)")

    def test_fill_consumes_malformed_submitted_intent_with_explicit_evidence(self):
        strategy = self._new_strategy()
        order_id = "intent-fill-malformed"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-intent")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = dict(meta)
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._submitted_order_intents[order_id] = "not-an-object"

        self.assertTrue(
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        )

        recorded = strategy._open_live_trades[order_id]
        self.assertNotIn(order_id, strategy._submitted_order_intents)
        self.assertTrue(recorded["submitted_order_intent_malformed"])
        self.assertEqual(recorded["submitted_order_intent_raw"], "not-an-object")
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(order_id, data["submitted_order_intents"])
        self.assertTrue(data["open"][order_id]["submitted_order_intent_malformed"])
        self.assertEqual(data["open"][order_id]["submitted_order_intent_raw"], "not-an-object")

    def test_pending_actual_fill_is_preserved_on_startup_load(self):
        pending = self._pending_actual_fill(
            fill_key="trade:orphaned",
            venue_order_id="0xorphaned",
            submitted_size="5.00",
        )
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {"orphaned-actual": pending},
                    "submitted_order_intents": {},
                }
            ),
            encoding="utf-8",
        )

        strategy = self._new_strategy()

        self.assertEqual(strategy._pending_actual_fills["orphaned-actual"], pending)
        self.assertEqual(strategy._settled_live_trades, [])
        unresolved = strategy._unresolved_settlement_unknowns()
        self.assertEqual(unresolved[0]["order_id"], "orphaned-actual")
        self.assertEqual(unresolved[0]["settlement_source"], "PENDING_ACTUAL_FILL")
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["pending_actual_fills"]["orphaned-actual"], pending)
        self.assertEqual(data["settled"], [])

    def test_malformed_pending_actual_fill_aborts_startup_explicitly(self):
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {"malformed-pending-fill": "not-an-object"},
                    "submitted_order_intents": {},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "pending_actual_fills\\[malformed-pending-fill\\]"):
            self._new_strategy()

    def test_malformed_pending_auto_redeem_aborts_startup_explicitly(self):
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {"malformed-redeem": "not-an-object"},
                    "pending_actual_fills": {},
                    "submitted_order_intents": {},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "pending_auto_redeem_events\\[malformed-redeem\\]"):
            self._new_strategy()

    def test_scalar_pending_actual_fill_aborts_startup_explicitly(self):
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {
                        "scalar-pending": {
                            "filled_qty": "4",
                            "vwap": "0.50",
                        }
                    },
                    "submitted_order_intents": {},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "scalar filled_qty is not valid"):
            self._new_strategy()

    def test_legacy_live_trade_ledger_requires_current_schema(self):
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 2,
                    "open": {},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "schema_version must be 3"):
            self._new_strategy()
        backups = list(self._test_ledger_path.parent.glob(f"{self._test_ledger_path.name}.schema-v2.*.bak"))
        self.assertEqual(backups, [])

    def test_schema_v3_missing_required_sections_aborts_startup(self):
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "missing required section"):
            self._new_strategy()

    def test_schema_v3_missing_core_ledger_sections_aborts_startup(self):
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {},
                    "submitted_order_intents": {},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "missing required section: open"):
            self._new_strategy()
        backups = list(self._test_ledger_path.parent.glob(f"{self._test_ledger_path.name}.schema-v*.bak"))
        self.assertEqual(backups, [])

    def test_schema_v3_malformed_entry_values_abort_startup(self):
        cases = [
            ("open", lambda data: data["open"].update({"bad-open": "not-an-object"}), "open\\[bad-open\\]"),
            ("settled", lambda data: data["settled"].append("not-an-object"), "settled\\[0\\]"),
            (
                "pending_auto_redeem_events",
                lambda data: data["pending_auto_redeem_events"].update({"bad-redeem": "not-an-object"}),
                "pending_auto_redeem_events\\[bad-redeem\\]",
            ),
            (
                "pending_actual_fills",
                lambda data: data["pending_actual_fills"].update({"bad-fill": "not-an-object"}),
                "pending_actual_fills\\[bad-fill\\]",
            ),
            (
                "submitted_order_intents",
                lambda data: data["submitted_order_intents"].update({"bad-intent": "not-an-object"}),
                "submitted_order_intents\\[bad-intent\\]",
            ),
        ]
        for name, mutate, pattern in cases:
            with self.subTest(name=name):
                data = {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {},
                    "submitted_order_intents": {},
                }
                mutate(data)
                self._test_ledger_path.write_text(json.dumps(data), encoding="utf-8")

                with self.assertRaisesRegex(self.bot.SettlementLedgerError, pattern):
                    self._new_strategy()

    def test_same_day_settled_trade_missing_pnl_aborts_daily_risk_rehydrate(self):
        meta = self._live_trade_meta(order_id="settled-missing-pnl", token_id="token-settled-missing-pnl")
        meta.update(
            {
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "settlement_source": "manual_reconciliation",
                "needs_reconciliation": False,
                "payout": "4",
                "pnl": "UNKNOWN",
                "exit_price": "1",
            }
        )
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {},
                    "settled": [meta],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {},
                    "submitted_order_intents": {},
                },
                default=str,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "missing verified pnl"):
            self._new_strategy()

    def test_open_trade_missing_cost_basis_aborts_risk_rehydrate(self):
        meta = self._live_trade_meta(order_id="open-missing-size", token_id="token-open-missing")
        meta.pop("size")
        meta["entry_price"] = "0.50"
        meta["filled_qty"] = "4"
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {"open-missing-size": meta},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {},
                    "submitted_order_intents": {},
                },
                default=str,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "missing verified settlement size"):
            self._new_strategy()

    def test_open_trade_invalid_direction_aborts_risk_rehydrate(self):
        meta = self._live_trade_meta(order_id="open-bad-direction", token_id="token-open-bad-dir")
        meta["direction"] = "sideways"
        self._test_ledger_path.write_text(
            json.dumps(
                {
                    "ledger_schema_version": 3,
                    "open": {"open-bad-direction": meta},
                    "settled": [],
                    "seen_auto_redeem_events": [],
                    "pending_auto_redeem_events": {},
                    "pending_actual_fills": {},
                    "submitted_order_intents": {},
                },
                default=str,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "invalid direction"):
            self._new_strategy()

    def test_release_submitted_position_requires_release_position(self):
        strategy = self._new_strategy()
        order_id = "release-no-fallback"
        strategy._submitted_positions[order_id] = self._live_trade_meta(order_id=order_id)

        class _RiskWithoutRelease:
            def __init__(self):
                self.remove_called = False

            def remove_position(self, *_args, **_kwargs):
                self.remove_called = True

        risk = _RiskWithoutRelease()
        strategy.risk_engine = risk

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "release_position unavailable"):
            strategy._release_submitted_position(order_id)

        self.assertFalse(risk.remove_called)
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_mark_settlement_unknown_requires_release_position(self):
        strategy = self._new_strategy()
        order_id = "unknown-no-fallback"
        meta = self._live_trade_meta(order_id=order_id)

        class _RiskWithoutRelease:
            def __init__(self):
                self.remove_called = False

            def remove_position(self, *_args, **_kwargs):
                self.remove_called = True

        risk = _RiskWithoutRelease()
        strategy.risk_engine = risk

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "release_position unavailable"):
            strategy._mark_settlement_unknown(
                order_id,
                meta,
                "unit test missing release_position",
                datetime.now(timezone.utc),
            )

        self.assertFalse(risk.remove_called)
        self.assertFalse(strategy._settled_live_trades)
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_mark_settlement_unknown_save_failure_preserves_memory_and_risk(self):
        strategy = self._new_strategy()
        order_id = "unknown-save-fails"
        meta = self._live_trade_meta(order_id=order_id)
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        original_write = strategy._write_live_trade_ledger_state

        def fail_write(_state):
            raise OSError("disk full")

        strategy._write_live_trade_ledger_state = fail_write
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to persist unknown settlement"):
                strategy._mark_settlement_unknown(
                    order_id,
                    meta,
                    "unit test save failure",
                    datetime.now(timezone.utc),
                )
        finally:
            strategy._write_live_trade_ledger_state = original_write

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertFalse(strategy._settled_live_trades)
        self.assertIn(order_id, strategy.risk_engine._positions)
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_on_stop_actual_fill_unregister_failure_is_not_silent(self):
        strategy = self._new_strategy()
        strategy._actual_fill_registered = True
        original_unregister = self.bot.unregister_actual_fill_handler

        def fail_unregister(_handler):
            raise RuntimeError("unregister failed")

        self.bot.unregister_actual_fill_handler = fail_unregister
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to unregister Polymarket handler"):
                strategy.on_stop()
        finally:
            self.bot.unregister_actual_fill_handler = original_unregister

        self.assertFalse(strategy._actual_fill_registered)
        self.assertIsNone(strategy._ledger_lock_file)

    def test_on_stop_auto_redeem_unregister_failure_still_saves_and_releases_lock(self):
        strategy = self._new_strategy()
        strategy._auto_redeem_registered = True
        original_unregister = self.bot.unregister_auto_redeem_handler

        def fail_unregister(_handler):
            raise RuntimeError("auto unregister failed")

        self.bot.unregister_auto_redeem_handler = fail_unregister
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to unregister Polymarket handler"):
                strategy.on_stop()
        finally:
            self.bot.unregister_auto_redeem_handler = original_unregister

        self.assertFalse(strategy._auto_redeem_registered)
        self.assertIsNone(strategy._ledger_lock_file)
        self.assertTrue(self._test_ledger_path.exists())

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

    def test_auto_redeem_save_failure_rolls_back_without_ram_only_pending(self):
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
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "not durably recorded"):
                strategy._handle_auto_redeem_event(
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

        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)
        self.assertEqual(len(strategy._pending_auto_redeem_events), 0)
        self.assertIn(order_id, strategy.risk_engine._positions)
        self.assertEqual(strategy.performance_tracker.trades, [])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_unmatched_redeem_is_retried_after_fill(self):
        strategy = self._new_strategy()
        strategy.on_event(self._account_state("10.00"))
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
        self.assertEqual(
            strategy._balance_stale_reason,
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_REDEEM,
        )

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
        strategy.on_event(self._account_state("10.00"))
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
        self.assertEqual(
            strategy._balance_stale_reason,
            self.bot.ACCOUNT_BALANCE_STALE_AFTER_REDEEM,
        )

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

    def test_auto_redeem_negative_payout_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-negative-redeem"
        strategy._open_live_trades[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-negative-redeem",
            slug="slug-negative-redeem",
            condition_id="cond-negative-redeem",
        )
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xnegative-redeem",
                "amount": "-1",
                "slug": "slug-negative-redeem",
                "condition_id": "cond-negative-redeem",
                "asset_id": "token-negative-redeem",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_overpayout_stays_pending_without_capping(self):
        strategy = self._new_strategy()
        order_id = "order-overpay-redeem"
        strategy._open_live_trades[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-overpay-redeem",
            slug="slug-overpay-redeem",
            condition_id="cond-overpay-redeem",
        )
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xoverpay-redeem",
                "amount": "5",
                "slug": "slug-overpay-redeem",
                "condition_id": "cond-overpay-redeem",
                "asset_id": "token-overpay-redeem",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertIn("exceeds tracked", next(iter(strategy._pending_auto_redeem_events.values()))["_pending_reason"])

    def test_auto_redeem_non_finite_payout_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-non-finite-redeem"
        strategy._open_live_trades[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-non-finite-redeem",
            slug="slug-non-finite-redeem",
            condition_id="cond-non-finite-redeem",
        )
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xnon-finite-redeem",
                "amount": "Infinity",
                "slug": "slug-non-finite-redeem",
                "condition_id": "cond-non-finite-redeem",
                "asset_id": "token-non-finite-redeem",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_missing_payout_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-missing-redeem-amount"
        strategy._open_live_trades[order_id] = self._live_trade_meta(
            order_id=order_id,
            token_id="token-missing-redeem-amount",
            slug="slug-missing-redeem-amount",
            condition_id="cond-missing-redeem-amount",
        )
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xmissing-redeem-amount",
                "slug": "slug-missing-redeem-amount",
                "condition_id": "cond-missing-redeem-amount",
                "asset_id": "token-missing-redeem-amount",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_does_not_use_estimated_tokens_as_filled_units(self):
        strategy = self._new_strategy()
        meta = self._live_trade_meta(
            order_id="order-estimated-only",
            token_id="token-estimated-only",
            slug="slug-estimated-only",
            condition_id="cond-estimated-only",
        )
        meta.pop("filled_qty")
        meta["estimated_tokens"] = Decimal("4")
        strategy._open_live_trades["order-estimated-only"] = meta
        strategy.risk_engine.add_position("order-estimated-only", Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xestimated-only",
                "amount": "4",
                "slug": "slug-estimated-only",
                "condition_id": "cond-estimated-only",
                "asset_id": "token-estimated-only",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn("order-estimated-only", strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)

    def test_auto_redeem_missing_cost_basis_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-missing-size"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-missing-size",
            slug="slug-missing-size",
            condition_id="cond-missing-size",
        )
        meta.pop("size")
        meta.pop("filled_notional")
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xmissing-size",
                "amount": "4",
                "slug": "slug-missing-size",
                "condition_id": "cond-missing-size",
                "asset_id": "token-missing-size",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_missing_entry_price_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-missing-entry"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-missing-entry",
            slug="slug-missing-entry",
            condition_id="cond-missing-entry",
        )
        meta.pop("entry_price")
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xmissing-entry",
                "amount": "4",
                "slug": "slug-missing-entry",
                "condition_id": "cond-missing-entry",
                "asset_id": "token-missing-entry",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_missing_filled_notional_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-missing-filled-notional"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-missing-filled-notional",
            slug="slug-missing-filled-notional",
            condition_id="cond-missing-filled-notional",
        )
        meta.pop("filled_notional")
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xmissing-filled-notional",
                "amount": "4",
                "slug": "slug-missing-filled-notional",
                "condition_id": "cond-missing-filled-notional",
                "asset_id": "token-missing-filled-notional",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_auto_redeem_missing_entry_timestamp_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-missing-entry-time"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-missing-entry-time",
            slug="slug-missing-entry-time",
            condition_id="cond-missing-entry-time",
        )
        meta.pop("filled_at")
        meta.pop("submitted_at")
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xmissing-entry-time",
                "amount": "4",
                "slug": "slug-missing-entry-time",
                "condition_id": "cond-missing-entry-time",
                "asset_id": "token-missing-entry-time",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertIn(
            "missing verified filled_at/submitted_at",
            next(iter(strategy._pending_auto_redeem_events.values()))["_pending_reason"],
        )

    def test_auto_redeem_naive_entry_timestamp_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-naive-entry-time"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-naive-entry-time",
            slug="slug-naive-entry-time",
            condition_id="cond-naive-entry-time",
        )
        naive_timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        meta["filled_at"] = naive_timestamp
        meta["submitted_at"] = naive_timestamp
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xnaive-entry-time",
                "amount": "4",
                "slug": "slug-naive-entry-time",
                "condition_id": "cond-naive-entry-time",
                "asset_id": "token-naive-entry-time",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertIn(
            "missing verified filled_at/submitted_at",
            next(iter(strategy._pending_auto_redeem_events.values()))["_pending_reason"],
        )

    def test_auto_redeem_invalid_direction_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-invalid-direction-redeem"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-invalid-direction-redeem",
            slug="slug-invalid-direction-redeem",
            condition_id="cond-invalid-direction-redeem",
        )
        meta["direction"] = "sideways"
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xinvalid-direction-redeem",
                "amount": "4",
                "slug": "slug-invalid-direction-redeem",
                "condition_id": "cond-invalid-direction-redeem",
                "asset_id": "token-invalid-direction-redeem",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy._settled_live_trades, [])
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertIn(
            "invalid direction for settlement accounting",
            next(iter(strategy._pending_auto_redeem_events.values()))["_pending_reason"],
        )

    def test_auto_redeem_inconsistent_cost_basis_stays_pending(self):
        strategy = self._new_strategy()
        order_id = "order-inconsistent-accounting"
        meta = self._live_trade_meta(
            order_id=order_id,
            token_id="token-inconsistent-accounting",
            slug="slug-inconsistent-accounting",
            condition_id="cond-inconsistent-accounting",
        )
        meta["entry_price"] = Decimal("0.40")
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        settled = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xinconsistent-accounting",
                "amount": "4",
                "slug": "slug-inconsistent-accounting",
                "condition_id": "cond-inconsistent-accounting",
                "asset_id": "token-inconsistent-accounting",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(settled)
        self.assertIn(order_id, strategy._open_live_trades)
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

    def test_late_auto_redeem_missing_cost_basis_stays_unknown(self):
        strategy = self._new_strategy()
        trade = self._live_trade_meta(
            order_id="unknown-settled-missing-size",
            token_id="token-unknown-missing-size",
            slug="slug-unknown-missing-size",
            condition_id="cond-unknown-missing-size",
        )
        trade.pop("size")
        trade.pop("filled_notional")
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
                "txn_hash": "0xunknown-missing-size",
                "amount": "4",
                "slug": "slug-unknown-missing-size",
                "condition_id": "cond-unknown-missing-size",
                "asset_id": "token-unknown-missing-size",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(corrected)
        self.assertEqual(trade["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertEqual(trade["payout"], "UNKNOWN")
        self.assertEqual(len(strategy._pending_auto_redeem_events), 1)
        self.assertEqual(len(strategy._seen_auto_redeem_events), 0)

    def test_late_auto_redeem_venue_only_unknown_stays_pending(self):
        strategy = self._new_strategy()
        trade = self._live_trade_meta(
            order_id="venue-only-unknown",
            token_id="token-venue-only-unknown",
            slug="slug-venue-only-unknown",
            condition_id="cond-venue-only-unknown",
        )
        trade["order_id"] = None
        trade.update(
            {
                "settlement_source": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": True,
                "venue_order_id": "0xvenue-only-unknown",
                "payout": "UNKNOWN",
                "pnl": "UNKNOWN",
            }
        )
        strategy._settled_live_trades.append(trade)

        corrected = strategy._handle_auto_redeem_event(
            {
                "txn_hash": "0xvenue-only-late",
                "amount": "4",
                "slug": "slug-venue-only-unknown",
                "condition_id": "cond-venue-only-unknown",
                "asset_id": "token-venue-only-unknown",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
        )

        self.assertFalse(corrected)
        self.assertEqual(strategy._settled_live_trades[0]["settlement_source"], "SETTLEMENT_UNKNOWN")
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

    def test_fill_save_failure_blocks_and_raises(self):
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
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to persist live order fill"):
                strategy._record_live_order_fill(
                    order_id,
                    Decimal("0.50"),
                    Decimal("4"),
                    fill_metadata=self._fill_metadata_for_meta(meta),
                )
            unresolved = strategy._unresolved_settlement_unknowns()
        finally:
            self.bot.LIVE_TRADE_LEDGER_PATH = original_path
            bad_path.with_name(bad_path.name + ".tmp").unlink(missing_ok=True)
            bad_path.rmdir()

        self.assertEqual(unresolved[-1]["settlement_source"], "LEDGER_BLOCKED")
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertNotIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy.risk_engine._positions[order_id]["size"], Decimal("5.50"))
        self.assertEqual(strategy.risk_engine._positions[order_id]["entry_price"], Decimal("0.55"))

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "live fill received while settlement ledger is blocked"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        self.assertIn(order_id, strategy._submitted_positions)
        self.assertNotIn(order_id, strategy._open_live_trades)
        self.assertEqual(strategy.risk_engine._positions[order_id]["size"], Decimal("5.50"))
        self.assertEqual(strategy.risk_engine._positions[order_id]["entry_price"], Decimal("0.55"))
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["settled"][-1]["order_id"], order_id)
        self.assertEqual(data["settled"][-1]["settlement_source"], "SETTLEMENT_UNKNOWN")

    def test_fill_risk_adjust_failure_blocks_after_durable_fill(self):
        strategy = self._new_strategy()
        order_id = "fill-risk-adjust-fails"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-risk-adjust")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        strategy._submitted_positions[order_id] = meta

        class _RiskAdjustFails(_DummyRiskEngine):
            def adjust_position(self, *_args, **_kwargs):
                raise RuntimeError("risk adjust failed")

        strategy.risk_engine = _RiskAdjustFails()
        strategy.risk_engine.add_position(order_id, Decimal("5.50"), Decimal("0.55"), "buy_yes")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "failed to adjust risk position"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )

        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertIn(order_id, data["open"])
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_blocked_fill_does_not_increment_filled_metric(self):
        strategy = self._new_strategy()
        strategy._settlement_ledger_blocked_reason = "unit test ledger block"
        events = []
        strategy._track_order_event = lambda event_type: events.append(event_type)

        class _FillEvent:
            client_order_id = "blocked-fill"
            condition_id = "cond-blocked"
            token_id = "token-blocked"
            last_px = Decimal("0.50")
            last_qty = Decimal("4")

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "live fill received while settlement ledger is blocked"):
            strategy.on_order_filled(_FillEvent())

        self.assertEqual(events, [])
        self.assertEqual(strategy._settled_live_trades[-1]["order_id"], "blocked-fill")
        self.assertEqual(strategy._settled_live_trades[-1]["settlement_source"], "SETTLEMENT_UNKNOWN")
        self.assertTrue(strategy._settled_live_trades[-1]["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertNotIn("filled_qty", strategy._settled_live_trades[-1])
        self.assertNotIn("entry_price", strategy._settled_live_trades[-1])
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["settled"][-1]["order_id"], "blocked-fill")

    def test_blocked_fill_does_not_promote_existing_open_cumulative_accounting(self):
        strategy = self._new_strategy()
        order_id = "blocked-open-fill"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-blocked-open")
        strategy._open_live_trades[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._settlement_ledger_blocked_reason = "unit test ledger block"

        with self.assertRaisesRegex(self.bot.SettlementLedgerError, "live fill received while settlement ledger is blocked"):
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.60"),
                Decimal("1"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )

        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["order_id"], order_id)
        self.assertTrue(unknown["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertNotIn("filled_qty", unknown)
        self.assertNotIn("entry_price", unknown)
        self.assertNotIn("filled_notional", unknown)
        self.assertNotIn("size", unknown)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn("filled_qty", data["settled"][-1])
        self.assertNotIn("filled_notional", data["settled"][-1])

    def test_fill_with_invalid_direction_metadata_creates_unknown_without_risk_adjust(self):
        strategy = self._new_strategy()
        order_id = "fill-invalid-direction"
        meta = self._live_trade_meta(order_id=order_id, token_id="token-invalid-direction")
        meta.pop("filled_qty")
        meta.pop("filled_notional")
        meta["direction"] = "sideways"
        strategy._submitted_positions[order_id] = meta
        strategy.risk_engine.add_position(order_id, Decimal("2.00"), Decimal("0.50"), "buy_yes")

        self.assertFalse(
            strategy._record_live_order_fill(
                order_id,
                Decimal("0.50"),
                Decimal("4"),
                fill_metadata=self._fill_metadata_for_meta(meta),
            )
        )

        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)
        self.assertNotIn(order_id, strategy._open_live_trades)
        unknown = strategy._settled_live_trades[-1]
        self.assertEqual(unknown["order_id"], order_id)
        self.assertEqual(unknown["unknown_reason"], "invalid_fill_direction_metadata")
        self.assertTrue(unknown["raw_callback_payload"]["requires_external_fill_repair"])
        self.assertNotIn("filled_qty", unknown)
        self.assertNotIn("entry_price", unknown)

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

    def test_auto_redeem_event_key_separates_distinct_complete_payloads(self):
        strategy = self._new_strategy()
        base = {
            "txn_hash": "0xcomplete",
            "slug": "slug-complete",
            "condition_id": "cond-complete",
            "asset_id": "token-complete",
            "amount": "1",
        }

        self.assertNotEqual(
            strategy._auto_redeem_event_key({**base, "timestamp": "1000"}),
            strategy._auto_redeem_event_key({**base, "timestamp": "2000"}),
        )

    def test_pending_auto_redeem_events_are_preserved_without_prune_cap(self):
        strategy = self._new_strategy()
        now = datetime.now(timezone.utc)
        stale_key = "stale|slug|token|1"
        strategy._pending_auto_redeem_events[stale_key] = {
            "txn_hash": "stale",
            "amount": "1",
            "_pending_since": (now - timedelta(days=8)).isoformat(),
        }
        for idx in range(501):
            strategy._pending_auto_redeem_events[f"fresh-{idx}|slug|token|1"] = {
                "txn_hash": f"fresh-{idx}",
                "amount": "1",
                "_pending_since": (now - timedelta(seconds=idx)).isoformat(),
            }

        dropped = strategy._prune_pending_auto_redeem_events_locked(now)

        self.assertEqual(dropped, 0)
        self.assertIn(stale_key, strategy._pending_auto_redeem_events)
        self.assertEqual(len(strategy._pending_auto_redeem_events), 502)

    def test_seen_auto_redeem_events_are_preserved_without_cap(self):
        strategy = self._new_strategy()
        for idx in range(10005):
            event_key = f"seen-{idx}|slug|token|1"
            strategy._seen_auto_redeem_events.add(event_key)
            strategy._seen_auto_redeem_event_order.append(event_key)

        strategy._save_live_trade_ledger()

        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["seen_auto_redeem_events"]), 10005)
        self.assertEqual(data["seen_auto_redeem_events"][0], "seen-0|slug|token|1")
        self.assertEqual(data["seen_auto_redeem_events"][-1], "seen-10004|slug|token|1")

    def test_store_pending_auto_redeem_save_failure_preserves_memory(self):
        strategy = self._new_strategy()
        original_write = strategy._write_live_trade_ledger_state

        def fail_write(_state):
            raise OSError("disk full")

        strategy._write_live_trade_ledger_state = fail_write
        try:
            with self.assertRaisesRegex(self.bot.SettlementLedgerError, "not durably recorded"):
                strategy._store_pending_auto_redeem_event(
                    "pending-save-fails|slug|token|1",
                    {
                        "txn_hash": "pending-save-fails",
                        "amount": "1",
                        "slug": "slug",
                        "condition_id": "cond",
                        "asset_id": "token",
                    },
                    "unit test save failure",
                )
        finally:
            strategy._write_live_trade_ledger_state = original_write

        self.assertEqual(strategy._pending_auto_redeem_events, {})
        self.assertIsNotNone(strategy._settlement_ledger_blocked_reason)

    def test_pending_auto_redeem_duplicate_key_preserves_distinct_payloads(self):
        strategy = self._new_strategy()
        event_key = "same-key|slug||1"
        strategy._store_pending_auto_redeem_event(
            event_key,
            {
                "txn_hash": "same-key",
                "amount": "1",
                "slug": "slug",
                "condition_id": "cond-a",
            },
            "first reason",
        )
        strategy._store_pending_auto_redeem_event(
            event_key,
            {
                "txn_hash": "same-key",
                "amount": "1",
                "slug": "slug",
                "condition_id": "cond-b",
            },
            "second reason",
        )

        self.assertEqual(len(strategy._pending_auto_redeem_events), 2)
        self.assertIn(event_key, strategy._pending_auto_redeem_events)
        collision_keys = [
            key for key in strategy._pending_auto_redeem_events if key.startswith(f"{event_key}|collision:")
        ]
        self.assertEqual(len(collision_keys), 1)
        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["pending_auto_redeem_events"]), 2)

    def test_retry_pending_auto_redeem_processes_collision_storage_keys(self):
        strategy = self._new_strategy()
        event_key = "same-key|slug||1"
        strategy._open_live_trades["order-collision-a"] = self._live_trade_meta(
            order_id="order-collision-a",
            token_id="token-collision-a",
            slug="slug-collision-a",
            condition_id="cond-collision-a",
        )
        strategy._open_live_trades["order-collision-b"] = self._live_trade_meta(
            order_id="order-collision-b",
            token_id="token-collision-b",
            slug="slug-collision-b",
            condition_id="cond-collision-b",
        )
        strategy.risk_engine.add_position("order-collision-a", Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy.risk_engine.add_position("order-collision-b", Decimal("2.00"), Decimal("0.50"), "buy_yes")
        strategy._store_pending_auto_redeem_event(
            event_key,
            {
                "txn_hash": "same-key",
                "amount": "4",
                "slug": "slug-collision-a",
                "condition_id": "cond-collision-a",
                "asset_id": "token-collision-a",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            },
            "first reason",
        )
        strategy._store_pending_auto_redeem_event(
            event_key,
            {
                "txn_hash": "same-key",
                "amount": "4",
                "slug": "slug-collision-b",
                "condition_id": "cond-collision-b",
                "asset_id": "token-collision-b",
                "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            },
            "second reason",
        )

        strategy._retry_pending_auto_redeems("unit test collisions")

        self.assertEqual(strategy._pending_auto_redeem_events, {})
        self.assertEqual({trade["order_id"] for trade in strategy._settled_live_trades}, {"order-collision-a", "order-collision-b"})
        self.assertEqual(len(strategy._seen_auto_redeem_events), 2)

    def test_retry_pending_auto_redeem_does_not_prune_or_save_before_retry(self):
        strategy = self._new_strategy()
        stale_key = "stale-retry|slug|token|1"
        strategy._pending_auto_redeem_events[stale_key] = {
            "txn_hash": "stale-retry",
            "amount": "1",
            "_pending_since": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
        }
        calls = []
        original_handler = strategy._handle_auto_redeem_event

        def record_retry(payload, store_pending=True, event_key_override=None):
            calls.append((payload, store_pending, event_key_override))

        strategy._handle_auto_redeem_event = record_retry
        try:
            strategy._retry_pending_auto_redeems("unit test retry")
        finally:
            strategy._handle_auto_redeem_event = original_handler

        self.assertIn(stale_key, strategy._pending_auto_redeem_events)
        self.assertIsNone(strategy._settlement_ledger_blocked_reason)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1])
        self.assertEqual(calls[0][2], stale_key)

    def test_live_trade_ledger_write_preserves_all_settled_records(self):
        strategy = self._new_strategy()
        strategy._settled_live_trades = [
            {
                "order_id": f"settled-{idx}",
                "settlement_source": "manual_reconciliation",
                "needs_reconciliation": False,
                "size": "2.00",
                "filled_qty": "4",
                "entry_price": "0.50",
                "payout": "4",
                "pnl": "2.00",
            }
            for idx in range(510)
        ]
        strategy._settled_live_trades[0].update(
            {
                "settlement_source": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": True,
                "payout": "UNKNOWN",
                "pnl": "UNKNOWN",
            }
        )

        strategy._save_live_trade_ledger()

        data = json.loads(self._test_ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["settled"]), 510)
        self.assertEqual(data["settled"][0]["order_id"], "settled-0")
        self.assertEqual(data["settled"][-1]["order_id"], "settled-509")

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
