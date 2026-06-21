import importlib
import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_HELPER_MODULE_PATH = Path(__file__).with_name("test_simulation_mode_safety.py")
_HELPER_SPEC = importlib.util.spec_from_file_location(
    "_decision_snapshot_safety_helpers",
    _HELPER_MODULE_PATH,
)
_safety_helpers = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_safety_helpers)

REPO_ROOT = _safety_helpers.REPO_ROOT
_install_bot_dependency_stubs = _safety_helpers._install_bot_dependency_stubs
_is_repo_module = _safety_helpers._is_repo_module
_STUBBED_MODULE_NAMES = _safety_helpers._STUBBED_MODULE_NAMES


class DecisionSnapshotTestCase(unittest.TestCase):
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
        self._strategies = []
        self._original_order_type = os.environ.get("ORDER_TYPE")
        self._original_sizing_mode = os.environ.get("SIZING_MODE")
        self._original_market_buy_usd = os.environ.get("MARKET_BUY_USD")
        self._original_max_position_size = os.environ.get("MAX_POSITION_SIZE")
        self._original_quote_stability_required = os.environ.get("QUOTE_STABILITY_REQUIRED")
        self._original_ev_fee_buffer = os.environ.get("EV_FEE_BUFFER")
        self._original_ev_spread_buffer = os.environ.get("EV_SPREAD_BUFFER")
        self._original_max_account_state_age_seconds = os.environ.get("MAX_ACCOUNT_STATE_AGE_SECONDS")
        self._original_max_decision_snapshot_age_seconds = os.environ.get(
            "MAX_DECISION_SNAPSHOT_AGE_SECONDS"
        )
        self._original_balance_safety_buffer_usd = os.environ.get("BALANCE_SAFETY_BUFFER_USD")
        os.environ["ORDER_TYPE"] = "market_ioc"
        os.environ["SIZING_MODE"] = "fixed"
        os.environ["MARKET_BUY_USD"] = "5.51"
        os.environ["MAX_POSITION_SIZE"] = "5.51"
        os.environ["QUOTE_STABILITY_REQUIRED"] = "3"
        os.environ["EV_FEE_BUFFER"] = "0.005"
        os.environ["EV_SPREAD_BUFFER"] = "0.01"
        os.environ["MAX_ACCOUNT_STATE_AGE_SECONDS"] = "30"
        os.environ["MAX_DECISION_SNAPSHOT_AGE_SECONDS"] = "10"
        os.environ["BALANCE_SAFETY_BUFFER_USD"] = "0.00"

    def tearDown(self):
        for strategy in self._strategies:
            strategy._release_live_trade_ledger_lock()
        self._restore_env("ORDER_TYPE", self._original_order_type)
        self._restore_env("SIZING_MODE", self._original_sizing_mode)
        self._restore_env("MARKET_BUY_USD", self._original_market_buy_usd)
        self._restore_env("MAX_POSITION_SIZE", self._original_max_position_size)
        self._restore_env("QUOTE_STABILITY_REQUIRED", self._original_quote_stability_required)
        self._restore_env("EV_FEE_BUFFER", self._original_ev_fee_buffer)
        self._restore_env("EV_SPREAD_BUFFER", self._original_ev_spread_buffer)
        self._restore_env(
            "MAX_ACCOUNT_STATE_AGE_SECONDS",
            self._original_max_account_state_age_seconds,
        )
        self._restore_env(
            "MAX_DECISION_SNAPSHOT_AGE_SECONDS",
            self._original_max_decision_snapshot_age_seconds,
        )
        self._restore_env("BALANCE_SAFETY_BUFFER_USD", self._original_balance_safety_buffer_usd)

    def _restore_env(self, key, value):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def _new_strategy(self, *, simulation_mode=True):
        strategy = self.bot.IntegratedBTCStrategy(
            redis_client=None,
            enable_grafana=False,
            simulation_mode=simulation_mode,
        )
        self._strategies.append(strategy)
        return strategy

    def _set_market(
        self,
        strategy,
        *,
        condition_id="condition-snapshot",
        yes_token_id="yes-token",
        no_token_id="no-token",
    ):
        now = datetime.now(timezone.utc)
        yes_instrument_id = f"{condition_id}-{yes_token_id}.POLYMARKET"
        no_instrument_id = f"{condition_id}-{no_token_id}.POLYMARKET"
        strategy.instrument_id = yes_instrument_id
        strategy._yes_instrument_id = yes_instrument_id
        strategy._no_instrument_id = no_instrument_id
        strategy._yes_token_id = yes_token_id
        strategy.all_btc_instruments = [
            {
                "slug": f"slug-{condition_id}",
                "condition_id": condition_id,
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "yes_instrument_id": yes_instrument_id,
                "no_instrument_id": no_instrument_id,
                "start_time": (now - timedelta(minutes=15)).isoformat(),
                "end_time": (now + timedelta(minutes=15)).isoformat(),
            }
        ]
        strategy.current_instrument_index = 0

    def _quote_tick(self, strategy, *, bid="0.39", ask="0.41"):
        class _Price:
            def __init__(self, value):
                self.value = Decimal(value)

            def as_decimal(self):
                return self.value

        # Review-cycle fix: ts_event is required on every tick per Beta-1
        # (review-cycle removed the wall-clock fallback at on_quote_tick).
        # Use a fixed test instant in nanoseconds-since-epoch.
        from datetime import datetime, timezone as _tz
        _epoch_ns = int(datetime.now(_tz.utc).timestamp() * 1_000_000_000)

        class _Tick:
            instrument_id = strategy.instrument_id
            bid_price = _Price(bid)
            ask_price = _Price(ask)
            ts_event = _epoch_ns

        return _Tick()
