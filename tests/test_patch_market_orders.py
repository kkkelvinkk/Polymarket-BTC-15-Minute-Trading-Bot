import importlib.util
import asyncio
from datetime import datetime, timezone
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "patch_market_orders.py"
TEST_MARKET_INTERVAL_SECONDS = 900


def load_patch_module():
    spec = importlib.util.spec_from_file_location("patch_market_orders_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PatchMarketOrdersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Save unpatched Nautilus methods so individual tests can patch
        # globally without leaking the patch into sibling tests.
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
        except ImportError:
            cls._original_methods = None
            return
        cls._original_methods = {
            "generate_order_status_reports":
                PolymarketExecutionClient.generate_order_status_reports,
            "generate_fill_reports":
                PolymarketExecutionClient.generate_fill_reports,
            "_parse_trades_response_object":
                PolymarketExecutionClient._parse_trades_response_object,
            "_submit_market_order":
                PolymarketExecutionClient._submit_market_order,
            "_handle_ws_message":
                PolymarketExecutionClient._handle_ws_message,
        }

    def tearDown(self):
        # Restore originals (no-op if class wasn't patched by this test).
        if not self._original_methods:
            return
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
        except ImportError:
            return
        for name, original in self._original_methods.items():
            setattr(PolymarketExecutionClient, name, original)
        canonical = sys.modules.get("patch_market_orders")
        if canonical is not None:
            canonical._uuid_guard_applied = False
            canonical._uuid_guard_market_interval_seconds = None
            canonical._patch_applied = False
            canonical._patch_market_interval_seconds = None

    def test_auto_redeem_handler_exception_is_not_swallowed(self):
        module = load_patch_module()

        def failing_handler(_payload):
            raise RuntimeError("handler failed")

        module.register_auto_redeem_handler(failing_handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "handler failed"):
                module._dispatch_auto_redeem({"event_type": "auto_redeem", "amount": "1"})
        finally:
            module.unregister_auto_redeem_handler(failing_handler)

    def test_apply_patch_preserves_native_market_order_submit(self):
        module = load_patch_module()
        from nautilus_trader.adapters.polymarket.execution import (
            PolymarketExecutionClient,
        )

        original_submit = PolymarketExecutionClient._submit_market_order
        self.assertTrue(
            module.apply_market_order_patch(
                market_interval_seconds=TEST_MARKET_INTERVAL_SECONDS,
            )
        )
        self.assertIs(PolymarketExecutionClient._submit_market_order, original_submit)

    def test_native_limit_ioc_maps_to_clob_limit_order_and_fak_post(self):
        try:
            import nautilus_trader.adapters.polymarket.execution as execution
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
            from nautilus_trader.model.enums import OrderSide, TimeInForce
        except ImportError:
            self.skipTest("nautilus_trader is not installed in this environment")

        captured = {"retry_calls": []}

        class _RetryManager:
            last_exception = None
            message = "ok"

            async def run(self, name, keys, runner, func, *args, **kwargs):
                captured["retry_calls"].append(
                    {
                        "name": name,
                        "keys": keys,
                        "func": getattr(func, "__name__", repr(func)),
                        "args": args,
                        "kwargs": kwargs,
                    }
                )
                return func(*args, **kwargs)

        class _RetryPool:
            async def acquire(self):
                return _RetryManager()

            async def release(self, _retry_manager):
                return None

        class _HttpClient:
            def create_order(self, order_args, options=None):
                captured["order_args"] = order_args
                captured["create_options"] = options
                return {"signed": "limit-order"}

            def post_order(self, signed_order, order_type, post_only):
                captured["post_order"] = {
                    "signed_order": signed_order,
                    "order_type": order_type,
                    "post_only": post_only,
                }
                return {"success": True, "orderID": "0xLIMITIOC"}

        class _Clock:
            def timestamp(self):
                return 1.0

            def timestamp_ns(self):
                return 1

        class _Log:
            def debug(self, *_args, **_kwargs):
                return None

            def info(self, *_args, **_kwargs):
                return None

            def error(self, *_args, **_kwargs):
                return None

        class _Cache:
            def add_venue_order_id(self, client_order_id, venue_order_id):
                captured["venue_order_id"] = (client_order_id, str(venue_order_id))

        fake_client = types.SimpleNamespace(
            _log=_Log(),
            _clock=_Clock(),
            _http_client=_HttpClient(),
            _retry_manager_pool=_RetryPool(),
            _cache=_Cache(),
            _ack_events_order={},
            _ack_events_trade={},
            _get_neg_risk_for_instrument=lambda _instrument: False,
            _expected_venue_order_id=lambda _signed_order, neg_risk: None,
            generate_order_submitted=lambda **kwargs: captured.setdefault("submitted", kwargs),
            generate_order_rejected=lambda **kwargs: captured.setdefault("rejected", kwargs),
            _send_quote_to_base_update=lambda *_args, **_kwargs: None,
            _register_fill_tracker=lambda *_args, **_kwargs: None,
            _execute_deferred_cancel_if_pending=lambda *_args, **_kwargs: None,
        )
        fake_client._post_signed_order = types.MethodType(
            PolymarketExecutionClient._post_signed_order,
            fake_client,
        )
        fake_order = types.SimpleNamespace(
            is_quote_quantity=False,
            price="0.62",
            instrument_id="condition-token.POLYMARKET",
            quantity="8.887096",
            side=OrderSide.BUY,
            expire_time_ns=0,
            strategy_id="strategy",
            client_order_id="client-limit-ioc",
            is_post_only=False,
            time_in_force=TimeInForce.IOC,
        )
        command = types.SimpleNamespace(order=fake_order)

        original_get_token = execution.get_polymarket_token_id
        try:
            execution.get_polymarket_token_id = lambda _instrument_id: "TOKEN-LIMIT-IOC"
            asyncio.run(
                PolymarketExecutionClient._submit_limit_order(
                    fake_client,
                    command,
                    instrument=types.SimpleNamespace(),
                )
            )
        finally:
            execution.get_polymarket_token_id = original_get_token

        self.assertEqual(captured["order_args"].token_id, "TOKEN-LIMIT-IOC")
        self.assertEqual(captured["order_args"].price, 0.62)
        self.assertEqual(captured["order_args"].size, 8.887096)
        self.assertEqual(captured["post_order"]["signed_order"], {"signed": "limit-order"})
        self.assertEqual(captured["post_order"]["order_type"], "FAK")
        self.assertFalse(captured["post_order"]["post_only"])

    def test_actual_fill_handler_exception_is_not_swallowed(self):
        module = load_patch_module()

        def failing_handler(_client_order_id, _payload):
            raise RuntimeError("actual fill handler failed")

        module.register_actual_fill_handler(failing_handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "actual fill handler failed"):
                module._dispatch_actual_fill("order-1", {"status": "ok"})
        finally:
            module.unregister_actual_fill_handler(failing_handler)

    def test_actual_fill_dispatch_without_handler_fails_closed(self):
        module = load_patch_module()

        with self.assertRaisesRegex(RuntimeError, "no registered handler"):
            module._dispatch_actual_fill("order-1", {"status": "ok"})

    def test_auto_redeem_dispatch_without_handler_fails_closed(self):
        module = load_patch_module()

        with self.assertRaisesRegex(RuntimeError, "no registered handler"):
            module._dispatch_auto_redeem({"event_type": "auto_redeem", "amount": "1"})

    def test_unregister_auto_redeem_missing_handler_fails_closed(self):
        module = load_patch_module()

        def handler(_payload):
            return None

        with self.assertRaises(ValueError):
            module.unregister_auto_redeem_handler(handler)

    def test_actual_fill_dispatch_copies_payload_and_stringifies_order_id(self):
        module = load_patch_module()
        calls = []

        def handler(client_order_id, payload):
            payload["mutated"] = True
            calls.append((client_order_id, payload))

        source_payload = {"status": "ok"}
        module.register_actual_fill_handler(handler)
        try:
            module._dispatch_actual_fill(123, source_payload)
        finally:
            module.unregister_actual_fill_handler(handler)

        self.assertEqual(calls, [("123", {"status": "ok", "mutated": True})])
        self.assertEqual(source_payload, {"status": "ok"})

    def test_actual_fill_dispatch_preserves_missing_client_order_id(self):
        module = load_patch_module()
        calls = []

        def handler(client_order_id, payload):
            calls.append((client_order_id, payload))

        module.register_actual_fill_handler(handler)
        try:
            module._dispatch_actual_fill(None, {"status": "failed", "venue_order_id": "0xabc"})
        finally:
            module.unregister_actual_fill_handler(handler)

        self.assertEqual(calls, [(None, {"status": "failed", "venue_order_id": "0xabc"})])

    def test_uuid_client_order_id_fallback_detector_blocks_source(self):
        module = load_patch_module()

        self.assertTrue(
            module._source_contains_client_order_id_uuid_fallback(
                "client_order_id = ClientOrderId( str( UUID4( ) ) )"
            )
        )
        self.assertFalse(
            module._source_contains_client_order_id_uuid_fallback(
                "client_order_id = self._cache.client_order_id(venue_order_id)"
            )
        )

    def test_uuid_guard_reports_method_specific_sites(self):
        module = load_patch_module()

        class DummyClient:
            def generate_order_status_reports(self):
                client_order_id = ClientOrderId(str(UUID4()))
                return client_order_id

            def _parse_trades_response_object(self):
                return None

        with self.assertRaisesRegex(RuntimeError, "generate_order_status_reports"):
            module.verify_no_nautilus_client_order_id_uuid_fallback(DummyClient)

    def test_uuid_guard_blocks_unpatched_installed_nautilus(self):
        """Regression: installed nautilus_trader (1.222.0 or 1.228.0) still
        contains the UUID4 client-id fallback at 3 sites until our
        guard patch is applied. The verify MUST block live startup on the
        unpatched class.

        If this fails, either (a) installed Nautilus was upgraded past the
        fallback (good — verify the clean-env Nautilus audit before unblocking live), or
        (b) the verification was weakened (bad).
        """
        module = load_patch_module()
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
        except ImportError:
            self.skipTest("nautilus_trader is not installed in this environment")

        if module._uuid_guard_applied:
            self.skipTest(
                "UUID-fallback guard already applied in this Python process; "
                "test depends on the unpatched installed Nautilus class"
            )

        with self.assertRaisesRegex(RuntimeError, "ClientOrderId.*UUID4"):
            module.verify_no_nautilus_client_order_id_uuid_fallback(
                PolymarketExecutionClient
            )

    def test_zz_patched_parse_trades_dispatches_failure_on_unmapped_venue(self):
        """When the patched _parse_trades_response_object sees a venue_order_id
        the cache cannot resolve, it MUST dispatch _dispatch_actual_fill with
        status=failed, reason=unmapped_venue_order_id, the real venue_order_id,
        the report source, and the filled_user_order_id — and skip the report.
        """
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
        except ImportError:
            self.skipTest("nautilus_trader is not installed in this environment")

        # Use the canonical module so the handler registry is shared with the
        # patched method's _dispatch_actual_fill closure.
        import patch_market_orders as canonical
        canonical.apply_uuid_fallback_guard_patch(
            market_interval_seconds=TEST_MARKET_INTERVAL_SECONDS,
        )

        captured = []

        def handler(client_order_id, payload):
            captured.append((client_order_id, payload))

        canonical.register_actual_fill_handler(handler)
        try:
            # Minimal mock self with the exact attributes the patched body
            # touches in the unmapped-venue path: _decoder_trade_report,
            # _wallet_address, _api_key, _cache, account_id, _clock,
            # _fill_tracker, _log.
            class _MockTrade:
                market = "0xCONDITION"

                def get_filled_user_order_ids(self, _wallet, _api):
                    return ["0xUSERORDERID"]

                def get_asset_id(self, _order_id):
                    return "TOKEN"

                def venue_order_id(self, _order_id):
                    from nautilus_trader.model.identifiers import VenueOrderId
                    return VenueOrderId("0xVENUEORDERID")

            class _MockDecoder:
                def decode(self, _raw):
                    return _MockTrade()

            class _MockCache:
                def client_order_id(self, _venue_order_id):
                    return None  # FORCE the unmapped-venue branch

            class _MockInstrumentProvider:
                async def initialize(self):
                    return None

                def find(self, _instrument_id):
                    return object()  # truthy

            class _MockClock:
                def timestamp_ns(self):
                    return 0

            class _MockLog:
                def warning(self, *_args, **_kwargs):
                    pass

            class _MockSelf:
                _decoder_trade_report = _MockDecoder()
                _wallet_address = "0xWALLET"
                _api_key = "API"
                _cache = _MockCache()
                _instrument_provider = _MockInstrumentProvider()
                account_id = object()
                _clock = _MockClock()
                _log = _MockLog()

            class _MockCommand:
                instrument_id = None
                venue_order_id = None

            mock_self = _MockSelf()
            reports = []
            parsed_fill_keys = set()

            PolymarketExecutionClient._parse_trades_response_object(
                mock_self, _MockCommand(), {"any": "json"}, parsed_fill_keys, reports
            )

            self.assertEqual(reports, [], "no report should be appended on unmapped venue")
            self.assertEqual(len(captured), 1, "exactly one actual-fill dispatch expected")
            client_oid, payload = captured[0]
            self.assertIsNone(client_oid)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "unmapped_venue_order_id")
            self.assertEqual(payload["venue_order_id"], "0xVENUEORDERID")
            self.assertEqual(payload["report_source"], "_parse_trades_response_object")
            self.assertEqual(payload["filled_user_order_id"], "0xUSERORDERID")
            self.assertIn("report_received_at", payload)
        finally:
            canonical.unregister_actual_fill_handler(handler)

    def test_zz_patched_generate_fill_reports_scopes_aggregate_to_provider_markets(self):
        """Startup mass-status fill reconciliation must query loaded markets.

        Nautilus calls GenerateFillReports with instrument_id=None at startup.
        The patched method must not issue the upstream account-wide
        TradeParams() request; it must enumerate provider-loaded Polymarket condition
        IDs and set params.market before any trade rows are decoded.
        """
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
        except ImportError:
            self.skipTest("nautilus_trader is not installed in this environment")

        import patch_market_orders as canonical
        canonical.apply_uuid_fallback_guard_patch(
            market_interval_seconds=TEST_MARKET_INTERVAL_SECONDS,
        )

        captured_requests = []
        releases = []

        class _Symbol:
            def __init__(self, value):
                self.value = value

        class _InstrumentId:
            def __init__(self, value):
                self.symbol = _Symbol(value)

        class _Instrument:
            def __init__(self, value, expiration_s):
                self.id = _InstrumentId(value)
                self.expiration_ns = expiration_s * 1_000_000_000
                # Reconciliation derives the market window from the slug START;
                # expiration_ns is Gamma's date-only midnight and is unused. The
                # slug start = expiration - interval keeps the window identical.
                self.info = {
                    "market_slug": (
                        f"btc-updown-15m-{expiration_s - TEST_MARKET_INTERVAL_SECONDS}"
                    )
                }

        class _Cache:
            pass

        class _InstrumentProvider:
            def __init__(self):
                self.initialized = 0

            async def initialize(self):
                self.initialized += 1

            def list_all(self):
                return [
                    _Instrument("condOld-tokenYES", 1781982899),
                    _Instrument("condA-tokenYES", 1781982900),
                    _Instrument("condA-tokenNO", 1781982900),
                    _Instrument("condB-tokenYES", 1781983800),
                    _Instrument("condFuture-tokenYES", 1781984700),
                ]

        class _RetryManager:
            async def run(self, name, details, runner, func, *, params):
                captured_requests.append((name, details, params))
                return []

        class _RetryPool:
            async def acquire(self):
                return _RetryManager()

            async def release(self, retry_manager):
                releases.append(retry_manager)

        class _HttpClient:
            def get_trades(self, *, params):
                raise AssertionError("retry manager should own get_trades execution")

        class _Log:
            def debug(self, *_args, **_kwargs):
                return None

        class _MockSelf:
            _log = _Log()
            _cache = _Cache()
            _retry_manager_pool = _RetryPool()
            _http_client = _HttpClient()

            def __init__(self):
                self._instrument_provider = _InstrumentProvider()

            def _parse_trades_response_object(self, *_args, **_kwargs):
                raise AssertionError("empty trade responses should not be decoded")

            def _log_report_receipt(self, count, report_type, level):
                self.receipt = (count, report_type, level)

        command = types.SimpleNamespace(
            instrument_id=None,
            venue_order_id=None,
            start=datetime.fromtimestamp(1781982900, tz=timezone.utc),
            end=datetime.fromtimestamp(1781983200, tz=timezone.utc),
        )

        mock_self = _MockSelf()
        reports = asyncio.run(
            PolymarketExecutionClient.generate_fill_reports(mock_self, command)
        )

        self.assertEqual(reports, [])
        self.assertEqual([params.market for _, _, params in captured_requests], ["condA", "condB"])
        self.assertEqual(
            [details for _, details, _ in captured_requests],
            [["condA"], ["condB"]],
        )
        self.assertEqual(
            [params.after for _, _, params in captured_requests],
            [1781982900, 1781982900],
        )
        self.assertEqual(
            [params.before for _, _, params in captured_requests],
            [1781983200, 1781983200],
        )
        self.assertEqual(len(releases), 2)
        self.assertEqual(mock_self.receipt[0:2], (0, "FillReport"))
        self.assertEqual(mock_self._instrument_provider.initialized, 1)

    def test_zz_patched_order_status_reports_scopes_aggregate_open_orders(self):
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
        except ImportError:
            self.skipTest("nautilus_trader is not installed in this environment")

        import patch_market_orders as canonical
        canonical.apply_uuid_fallback_guard_patch(
            market_interval_seconds=TEST_MARKET_INTERVAL_SECONDS,
        )

        captured_requests = []

        class _Symbol:
            def __init__(self, value):
                self.value = value

        class _InstrumentId:
            def __init__(self, value):
                self.symbol = _Symbol(value)

        class _Instrument:
            def __init__(self, value, expiration_s):
                self.id = _InstrumentId(value)
                self.expiration_ns = expiration_s * 1_000_000_000
                # Reconciliation derives the market window from the slug START;
                # expiration_ns is Gamma's date-only midnight and is unused. The
                # slug start = expiration - interval keeps the window identical.
                self.info = {
                    "market_slug": (
                        f"btc-updown-15m-{expiration_s - TEST_MARKET_INTERVAL_SECONDS}"
                    )
                }

        class _Cache:
            pass

        class _InstrumentProvider:
            def __init__(self):
                self.initialized = 0

            async def initialize(self):
                self.initialized += 1

            def list_all(self):
                return [
                    _Instrument("condA-tokenYES", 1781982900),
                    _Instrument("condA-tokenNO", 1781982900),
                    _Instrument("condB-tokenYES", 1781983800),
                    _Instrument("condFuture-tokenYES", 1781984700),
                ]

        class _RetryManager:
            async def run(self, name, details, runner, func, *, params):
                captured_requests.append((name, details, params))
                return []

        class _RetryPool:
            async def acquire(self):
                return _RetryManager()

            async def release(self, _retry_manager):
                return None

        class _Log:
            def debug(self, *_args, **_kwargs):
                return None

        class _MockSelf:
            _log = _Log()
            _cache = _Cache()
            _retry_manager_pool = _RetryPool()
            _http_client = types.SimpleNamespace(get_open_orders=object())
            _config = types.SimpleNamespace(generate_order_history_from_trades=False)

            def __init__(self):
                self._instrument_provider = _InstrumentProvider()

            def _log_report_receipt(self, count, report_type, level):
                self.receipt = (count, report_type, level)

        command = types.SimpleNamespace(
            instrument_id=None,
            start=datetime.fromtimestamp(1781982900, tz=timezone.utc),
            end=datetime.fromtimestamp(1781983200, tz=timezone.utc),
            log_receipt_level="INFO",
        )

        mock_self = _MockSelf()
        reports = asyncio.run(
            PolymarketExecutionClient.generate_order_status_reports(mock_self, command)
        )

        self.assertEqual(reports, [])
        self.assertEqual(
            [(params.market, params.asset_id) for _, _, params in captured_requests],
            [
                ("condA", "tokenYES"),
                ("condA", "tokenNO"),
                ("condB", "tokenYES"),
            ],
        )
        self.assertEqual(mock_self.receipt, (0, "OrderStatusReport", "INFO"))
        self.assertEqual(mock_self._instrument_provider.initialized, 1)

    def test_zz_order_history_from_trades_preserves_reconciliation_window(self):
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
            from nautilus_trader.model.identifiers import (
                ClientOrderId,
                InstrumentId,
                VenueOrderId,
            )
        except ImportError:
            self.skipTest("nautilus_trader is not installed in this environment")

        import patch_market_orders as canonical
        canonical.apply_uuid_fallback_guard_patch(
            market_interval_seconds=TEST_MARKET_INTERVAL_SECONDS,
        )

        captured_fill_command = {}

        class _Symbol:
            def __init__(self, value):
                self.value = value

        class _InstrumentId:
            def __init__(self, value):
                self.symbol = _Symbol(value)

        class _Instrument:
            id = _InstrumentId("condA-tokenYES")
            expiration_ns = 1781982900 * 1_000_000_000
            # Slug START = expiration - interval (900); window stays [..2000, ..2900].
            info = {"market_slug": "btc-updown-15m-1781982000"}

        class _Cache:
            def orders_open(self, venue):
                return [
                    types.SimpleNamespace(
                        instrument_id=InstrumentId.from_str("condA-tokenYES.POLYMARKET"),
                        client_order_id=ClientOrderId("client-open"),
                        venue_order_id=VenueOrderId("venue-open"),
                    )
                ]

            def orders(self):
                return []

        class _InstrumentProvider:
            def __init__(self):
                self.initialized = 0

            async def initialize(self):
                self.initialized += 1

            def list_all(self):
                return [_Instrument()]

        class _RetryManager:
            async def run(self, name, details, runner, func, *, params):
                return []

        class _RetryPool:
            async def acquire(self):
                return _RetryManager()

            async def release(self, _retry_manager):
                return None

        class _Log:
            def debug(self, *_args, **_kwargs):
                return None

            def warning(self, *_args, **_kwargs):
                return None

        class _MockSelf:
            _log = _Log()
            _cache = _Cache()
            _retry_manager_pool = _RetryPool()
            _http_client = types.SimpleNamespace(get_open_orders=object())
            _config = types.SimpleNamespace(generate_order_history_from_trades=True)
            _clock = types.SimpleNamespace(timestamp_ns=lambda: 0)

            def __init__(self):
                self._instrument_provider = _InstrumentProvider()

            async def generate_order_status_report(self, command):
                self.order_status_command = command
                return None

            async def generate_fill_reports(self, command):
                captured_fill_command["command"] = command
                return []

            def _log_report_receipt(self, count, report_type, level):
                self.receipt = (count, report_type, level)

        start = datetime.fromtimestamp(1781982900, tz=timezone.utc)
        end = datetime.fromtimestamp(1781983200, tz=timezone.utc)
        command = types.SimpleNamespace(
            instrument_id=None,
            start=start,
            end=end,
            log_receipt_level="INFO",
        )

        mock_self = _MockSelf()
        reports = asyncio.run(
            PolymarketExecutionClient.generate_order_status_reports(mock_self, command)
        )

        self.assertEqual(reports, [])
        self.assertIs(captured_fill_command["command"].start, start)
        self.assertIs(captured_fill_command["command"].end, end)
        self.assertEqual(mock_self.receipt, (0, "OrderStatusReport", "INFO"))
        self.assertEqual(mock_self._instrument_provider.initialized, 1)

    def test_zz_uuid_fallback_guard_patch_lets_verify_pass(self):
        """Applying the UUID-fallback guard patch replaces the 3
        ClientOrderId-via-UUID4 fallback sites with _dispatch_actual_fill +
        skip. After patching, verify_no_nautilus_client_order_id_uuid_fallback
        MUST stop tripping on the now-patched method source.

        This test mutates global state (PolymarketExecutionClient class
        attributes). The `zz_` prefix orders this test LAST within the file
        under unittest's default alphabetical ordering, narrowing the window
        during which a tearDown failure could affect sibling tests. The
        existing tearDown also restores originals as belt-and-suspenders.
        """
        module = load_patch_module()
        try:
            from nautilus_trader.adapters.polymarket.execution import (
                PolymarketExecutionClient,
            )
        except ImportError:
            self.skipTest("nautilus_trader is not installed in this environment")

        # Apply the patch
        result = module.apply_uuid_fallback_guard_patch(
            market_interval_seconds=TEST_MARKET_INTERVAL_SECONDS,
        )
        self.assertTrue(result)

        # Verify now passes on the patched method source
        self.assertTrue(
            module.verify_no_nautilus_client_order_id_uuid_fallback(
                PolymarketExecutionClient
            )
        )

        # Confirm the patched methods are callables (not torn down by reload)
        self.assertTrue(callable(PolymarketExecutionClient.generate_order_status_reports))
        self.assertTrue(callable(PolymarketExecutionClient._parse_trades_response_object))


if __name__ == "__main__":
    unittest.main()
