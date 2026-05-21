import asyncio
import contextlib
import io
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from vault_store import DEFAULT_VAULT_FILE, PolymarketVault


def make_test_vault(
    *,
    private_key_digit: str = "1",
    funder_digit: str = "2",
    signature_type: int = 3,
    api_key: str = "api-key",
    api_secret: str = "api-secret",
    passphrase: str = "passphrase",
) -> PolymarketVault:
    return PolymarketVault(
        private_key="0x" + private_key_digit * 64,
        funder="0x" + funder_digit * 40,
        signature_type=signature_type,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        polygon_rpc_url="https://rpc.example/key",
    )


def clear_polymarket_client_cache(client_module) -> None:
    client_module._polymarket_client_instance = None
    client_module._polymarket_client_cache_key = None
    client_module._polymarket_client_source_path = None


def make_polymarket_client(client_module, *, signature_type: int = 1):
    return client_module.PolymarketClient(
        private_key="0x" + "1" * 64,
        funder="0x" + "2" * 40,
        signature_type=signature_type,
        api_key="api-key",
        api_secret="api-secret",
        api_passphrase="passphrase",
    )


class PolymarketClientVaultTests(unittest.TestCase):
    def test_get_polymarket_client_loads_vault_credentials(self):
        import execution.polymarket_client as client_module

        vault = make_test_vault(signature_type=1)

        clear_polymarket_client_cache(client_module)
        try:
            with mock.patch.object(
                client_module,
                "load_vault_from_prompt",
                return_value=vault,
            ) as load_vault_mock:
                client = client_module.get_polymarket_client(force_new=True)
        finally:
            clear_polymarket_client_cache(client_module)

        load_vault_mock.assert_called_once_with(client_module.DEFAULT_VAULT_FILE)
        self.assertEqual(client.private_key, vault.private_key)
        self.assertEqual(client.api_key, vault.api_key)
        self.assertEqual(client.api_secret, vault.api_secret)
        self.assertEqual(client.api_passphrase, vault.passphrase)
        self.assertEqual(client.funder, vault.funder)
        self.assertEqual(client.signature_type, vault.signature_type)

    def test_get_polymarket_client_rebuilds_for_different_vault(self):
        import execution.polymarket_client as client_module

        first_vault = make_test_vault(
            signature_type=1,
            api_key="api-key-one",
            api_secret="api-secret-one",
            passphrase="passphrase-one",
        )
        second_vault = make_test_vault(
            private_key_digit="3",
            funder_digit="4",
            signature_type=1,
            api_key="api-key-two",
            api_secret="api-secret-two",
            passphrase="passphrase-two",
        )

        clear_polymarket_client_cache(client_module)
        try:
            first_client = client_module.get_polymarket_client(vault=first_vault)
            second_client = client_module.get_polymarket_client(vault=second_vault)
            testnet_client = client_module.get_polymarket_client(
                vault=second_vault,
                testnet=True,
            )
        finally:
            clear_polymarket_client_cache(client_module)

        self.assertIsNot(first_client, second_client)
        self.assertIsNot(second_client, testnet_client)
        self.assertEqual(second_client.api_key, "api-key-two")
        self.assertTrue(testnet_client.testnet)

    def test_get_polymarket_client_reuses_cached_default_without_prompt(self):
        import execution.polymarket_client as client_module

        vault = make_test_vault(signature_type=1)

        clear_polymarket_client_cache(client_module)
        try:
            with mock.patch.object(
                client_module,
                "load_vault_from_prompt",
                return_value=vault,
            ) as load_vault_mock:
                first_client = client_module.get_polymarket_client()
                second_client = client_module.get_polymarket_client()
        finally:
            clear_polymarket_client_cache(client_module)

        self.assertIs(first_client, second_client)
        load_vault_mock.assert_called_once_with(client_module.DEFAULT_VAULT_FILE)

    def test_get_polymarket_client_accepts_string_vault_path(self):
        import execution.polymarket_client as client_module

        vault = make_test_vault(signature_type=1)
        path_text = str(client_module.DEFAULT_VAULT_FILE)

        clear_polymarket_client_cache(client_module)
        try:
            with mock.patch.object(
                client_module,
                "load_vault_from_prompt",
                return_value=vault,
            ) as load_vault_mock:
                client_module.get_polymarket_client(
                    force_new=True,
                    vault_path=path_text,
                )
        finally:
            clear_polymarket_client_cache(client_module)

        load_vault_mock.assert_called_once_with(client_module.DEFAULT_VAULT_FILE)

    def test_polymarket_client_connect_uses_balance_allowance_v1(self):
        import execution.polymarket_client as client_module

        captured = {}

        class _FakeClobClient:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

            def get_balance_allowance(self, params):
                captured["params"] = params
                return {"balance": "1234567"}

        client = make_polymarket_client(client_module, signature_type=1)

        with mock.patch.object(client_module, "ClobClient", _FakeClobClient):
            self.assertTrue(asyncio.run(client.connect()))

        self.assertEqual(captured["kwargs"]["signature_type"], 1)
        self.assertEqual(captured["kwargs"]["funder"], "0x" + "2" * 40)
        self.assertIn("creds", captured["kwargs"])
        self.assertEqual(client.is_connected, True)
        self.assertEqual(asyncio.run(client.get_balance())["USDC"], Decimal("1.234567"))

    def test_polymarket_client_connect_uses_v2_for_signature_type_three(self):
        import execution.polymarket_client as client_module

        captured = {}

        class _FakeV2ClobClient:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

            def get_balance_allowance(self, params):
                captured["params"] = params
                return {"balance": "1000000"}

        client = make_polymarket_client(client_module, signature_type=3)

        with mock.patch.object(client_module, "V2ClobClient", _FakeV2ClobClient):
            self.assertTrue(asyncio.run(client.connect()))

        self.assertEqual(captured["kwargs"]["signature_type"], 3)
        self.assertEqual(captured["kwargs"]["funder"], "0x" + "2" * 40)
        self.assertIn("creds", captured["kwargs"])
        self.assertEqual(asyncio.run(client.get_balance())["USDC"], Decimal("1"))

    def test_polymarket_client_connect_rejects_invalid_balance_units(self):
        import execution.polymarket_client as client_module

        invalid_values = ["-1", "1.5", True, 1.5]
        for raw_balance in invalid_values:
            with self.subTest(raw_balance=raw_balance):

                class _FakeClobClient:
                    def __init__(self, *args, **kwargs):
                        pass

                    def get_balance_allowance(self, params):
                        return {"balance": raw_balance}

                client = make_polymarket_client(client_module, signature_type=1)
                with mock.patch.object(client_module, "ClobClient", _FakeClobClient):
                    self.assertFalse(asyncio.run(client.connect()))

                self.assertIsNone(client.client)
                self.assertFalse(client.is_connected)

    def test_polymarket_client_open_orders_are_normalized_for_v2(self):
        import execution.polymarket_client as client_module

        raw_orders = [
            {
                "id": "order-one",
                "asset_id": "token-one",
                "side": "BUY",
                "price": "0.50",
                "original_size": "1",
                "size_matched": "0.25",
                "created_at": "1710000000",
            }
        ]

        class _FakeV2Client:
            def get_open_orders(self):
                return raw_orders

        client = make_polymarket_client(client_module, signature_type=3)
        client.client = _FakeV2Client()
        client._connected = True

        self.assertEqual(
            asyncio.run(client.get_open_orders()),
            [
                {
                    "order_id": "order-one",
                    "token_id": "token-one",
                    "side": "BUY",
                    "price": Decimal("0.50"),
                    "size": Decimal("1"),
                    "filled": Decimal("0.25"),
                    "timestamp": datetime.fromtimestamp(1710000000),
                }
            ],
        )

    def test_polymarket_client_open_orders_are_normalized_for_v1(self):
        import execution.polymarket_client as client_module

        raw_orders = [
            {
                "id": "order-one",
                "asset_id": "token-one",
                "side": "BUY",
                "price": "0.50",
                "original_size": "1",
                "size_matched": "0.25",
                "created_at": "1710000000",
            }
        ]

        class _FakeV1Client:
            def get_orders(self):
                return raw_orders

        client = make_polymarket_client(client_module, signature_type=1)
        client.client = _FakeV1Client()
        client._connected = True

        self.assertEqual(
            asyncio.run(client.get_open_orders())[0]["token_id"],
            "token-one",
        )

    def test_polymarket_client_open_orders_require_verified_connection(self):
        import execution.polymarket_client as client_module

        client = make_polymarket_client(client_module, signature_type=3)
        client.client = object()

        with self.assertRaisesRegex(RuntimeError, "connected"):
            asyncio.run(client.get_open_orders())

    def test_polymarket_client_cancel_order_uses_sdk_specific_api(self):
        import execution.polymarket_client as client_module

        calls = []

        class _FakeV1Client:
            def cancel(self, order_id):
                calls.append(("v1", order_id))
                return {"canceled": [order_id], "not_canceled": {}}

        class _FakeV2Client:
            def cancel_order(self, payload):
                calls.append(("v2", payload.orderID))
                return {"canceled": [payload.orderID], "not_canceled": {}}

        v1_client = make_polymarket_client(client_module, signature_type=1)
        v1_client.client = _FakeV1Client()
        v1_client._connected = True
        v2_client = make_polymarket_client(client_module, signature_type=3)
        v2_client.client = _FakeV2Client()
        v2_client._connected = True

        self.assertTrue(asyncio.run(v1_client.cancel_order("order-one")))
        self.assertTrue(asyncio.run(v2_client.cancel_order("order-two")))
        self.assertEqual(calls, [("v1", "order-one"), ("v2", "order-two")])

    def test_polymarket_client_cancel_order_returns_false_when_not_canceled(self):
        import execution.polymarket_client as client_module

        class _FakeV1Client:
            def cancel(self, order_id):
                return {"canceled": [], "not_canceled": {order_id: "reason"}}

        client = make_polymarket_client(client_module, signature_type=1)
        client.client = _FakeV1Client()
        client._connected = True

        self.assertFalse(asyncio.run(client.cancel_order("order-one")))

    def test_polymarket_client_orderbook_supports_v1_objects_and_v2_dicts(self):
        import execution.polymarket_client as client_module

        class _FakeV1Client:
            def get_order_book(self, _token_id):
                return SimpleNamespace(
                    bids=[SimpleNamespace(price="0.55", size="2")],
                    asks=[SimpleNamespace(price="0.57", size="3")],
                )

        class _FakeV2Client:
            def get_order_book(self, _token_id):
                return {
                    "bids": [{"price": "0.45", "size": "4"}],
                    "asks": [{"price": "0.47", "size": "5"}],
                }

        v1_client = make_polymarket_client(client_module, signature_type=1)
        v1_client.client = _FakeV1Client()
        v1_client._connected = True
        v2_client = make_polymarket_client(client_module, signature_type=3)
        v2_client.client = _FakeV2Client()
        v2_client._connected = True

        self.assertEqual(
            asyncio.run(v1_client.get_market_price("token-one")),
            Decimal("0.55"),
        )
        self.assertEqual(
            asyncio.run(v2_client.get_orderbook("token-two"))["asks"][0]["size"],
            Decimal("5"),
        )


class AdminVaultToolTests(unittest.TestCase):
    def test_derive_passes_signature_type_and_funder_to_v1_client(self):
        import derive_polymarket_api_creds as derive

        captured = {}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

            def derive_api_key(self):
                return type(
                    "Creds",
                    (),
                    {
                        "api_key": "derived-key",
                        "api_secret": "derived-secret",
                        "api_passphrase": "derived-passphrase",
                    },
                )()

        vault = make_test_vault(
            signature_type=1,
            api_key="old-key",
            api_secret="old-secret",
            passphrase="old-passphrase",
        )

        with mock.patch.object(derive, "prompt_vault_password", return_value="password"):
            with mock.patch.object(derive, "load_vault", return_value=vault):
                with mock.patch.object(derive, "save_vault") as save_vault_mock:
                    with mock.patch.object(derive, "ClobClient", _FakeClient):
                        with mock.patch.object(derive, "parse_args") as parse_args_mock:
                            parse_args_mock.return_value = type(
                                "Args",
                                (),
                                {
                                    "vault": DEFAULT_VAULT_FILE,
                                    "print_secrets": False,
                                },
                            )()
                            self.assertEqual(derive.main(), 0)

        self.assertEqual(captured["kwargs"]["signature_type"], 1)
        self.assertEqual(captured["kwargs"]["funder"], "0x" + "2" * 40)
        updated_vault = save_vault_mock.call_args.args[0]
        self.assertEqual(updated_vault.api_key, "derived-key")

    def test_check_balance_rejects_missing_balance_field(self):
        import check_polymarket_balance as balance

        with self.assertRaisesRegex(RuntimeError, "missing balance"):
            balance.print_balance_response({"allowance": "1"}, "USDC.e")

    def test_check_balance_rejects_missing_allowance_fields(self):
        import check_polymarket_balance as balance

        with self.assertRaisesRegex(RuntimeError, "missing allowance"):
            with contextlib.redirect_stdout(io.StringIO()):
                balance.print_balance_response({"balance": "1"}, "USDC.e")

    def test_check_balance_rejects_empty_allowances_object(self):
        import check_polymarket_balance as balance

        with self.assertRaisesRegex(RuntimeError, "missing allowance"):
            with contextlib.redirect_stdout(io.StringIO()):
                balance.print_balance_response({"balance": "1", "allowances": {}}, "USDC.e")

    def test_check_balance_rejects_negative_units(self):
        import check_polymarket_balance as balance

        responses = [
            {"balance": -1, "allowance": "0"},
            {"balance": "1", "allowance": -1},
            {"balance": "1", "allowances": {"spender": -1}},
        ]
        for response in responses:
            with self.subTest(response=response):
                with self.assertRaisesRegex(RuntimeError, "negative"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        balance.print_balance_response(response, "USDC.e")

    def test_check_balance_rejects_malformed_unit_values(self):
        import check_polymarket_balance as balance

        responses = [
            {"balance": True, "allowance": "0"},
            {"balance": False, "allowance": "0"},
            {"balance": 1.5, "allowance": "0"},
            {"balance": "1.5", "allowance": "0"},
            {"balance": "-1", "allowance": "0"},
            {"balance": "1", "allowance": True},
            {"balance": "1", "allowances": {"spender": 1.5}},
        ]
        for response in responses:
            with self.subTest(response=response):
                with self.assertRaisesRegex(RuntimeError, "invalid"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        balance.print_balance_response(response, "USDC.e")
