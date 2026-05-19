import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "patch_market_orders.py"


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
            "_parse_trades_response_object":
                PolymarketExecutionClient._parse_trades_response_object,
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
        """Regression: installed nautilus_trader (1.222.0 or 1.227.0) still
        contains the UUID4 client-id fallback at 3 sites until our Phase 0.4
        guard patch is applied. The verify MUST block live startup on the
        unpatched class.

        If this fails, either (a) installed Nautilus was upgraded past the
        fallback (good — verify Phase 0.5a audit before unblocking live), or
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
        canonical.apply_uuid_fallback_guard_patch()

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
                def instrument(self, _instrument_id):
                    return object()  # truthy

                def client_order_id(self, _venue_order_id):
                    return None  # FORCE the unmapped-venue branch

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

    def test_zz_uuid_fallback_guard_patch_lets_verify_pass(self):
        """Phase 0.4 — applying the UUID-fallback guard patch replaces the 3
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
        result = module.apply_uuid_fallback_guard_patch()
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
