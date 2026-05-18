"""Compatibility patch for Polymarket deposit-wallet CLOB v2 trading."""

from __future__ import annotations

from functools import lru_cache


_PATCH_APPLIED = False


def apply_polymarket_v2_patch() -> bool:
    """Patch Nautilus' Polymarket factory to use py-clob-client-v2 for signature type 3."""
    global _PATCH_APPLIED

    if _PATCH_APPLIED:
        return True

    import nautilus_trader.adapters.polymarket.factories as factories

    if not hasattr(factories, "get_polymarket_http_client"):
        return False

    try:
        from py_clob_client_v2 import ApiCreds as V2ApiCreds
        from py_clob_client_v2 import ClobClient as V2ClobClient
        from py_clob_client_v2 import MarketOrderArgs as V2MarketOrderArgs
        from py_clob_client_v2 import OrderArgs as V2OrderArgs
        from py_clob_client_v2.clob_types import OrderPayload as V2OrderPayload
        from py_clob_client_v2.config import get_contract_config as get_v2_contract_config
        from py_clob_client_v2.constants import BYTES32_ZERO
    except ImportError:
        return False

    original_get_http_client = factories.get_polymarket_http_client

    class PolymarketV2CompatClient(V2ClobClient):
        """py-clob-client-v2 with method aliases expected by the Nautilus adapter."""

        def get_collateral_address(self):
            return get_v2_contract_config(self.chain_id).collateral

        def get_conditional_address(self):
            return get_v2_contract_config(self.chain_id).conditional_tokens

        def get_exchange_address(self, neg_risk=False):
            config = get_v2_contract_config(self.chain_id)
            return config.neg_risk_exchange_v2 if neg_risk else config.exchange_v2

        def get_orders(self, params=None):
            return self.get_open_orders(params=params)

        def cancel(self, order_id: str):
            return self.cancel_order(order_id=order_id)

        def cancel_order(self, order_id=None, payload=None):
            if isinstance(payload, V2OrderPayload):
                return super().cancel_order(payload)
            if isinstance(order_id, V2OrderPayload):
                return super().cancel_order(order_id)
            if not order_id:
                raise ValueError("cancel_order requires an order_id or OrderPayload")
            return super().cancel_order(V2OrderPayload(orderID=str(order_id)))

        def cancel_orders(self, order_ids=None, order_hashes=None):
            ids = order_hashes or order_ids or []
            if not ids:
                raise ValueError("cancel_orders requires order_ids or order_hashes")
            return super().cancel_orders([str(order_id) for order_id in ids])

        def create_order(self, order_args, options=None):
            if not isinstance(order_args, V2OrderArgs):
                order_args = V2OrderArgs(
                    token_id=order_args.token_id,
                    price=order_args.price,
                    size=order_args.size,
                    side=order_args.side,
                    expiration=getattr(order_args, "expiration", 0),
                    builder_code=getattr(order_args, "builder_code", None) or BYTES32_ZERO,
                    metadata=getattr(order_args, "metadata", None) or BYTES32_ZERO,
                )
            return super().create_order(order_args, options=options)

        def create_market_order(self, order_args, options=None):
            if not isinstance(order_args, V2MarketOrderArgs):
                order_args = V2MarketOrderArgs(
                    token_id=order_args.token_id,
                    amount=order_args.amount,
                    side=order_args.side,
                    price=getattr(order_args, "price", 0),
                    order_type=getattr(order_args, "order_type", "FOK"),
                    builder_code=getattr(order_args, "builder_code", None) or BYTES32_ZERO,
                    metadata=getattr(order_args, "metadata", None) or BYTES32_ZERO,
                )
            return super().create_market_order(order_args, options=options)

    @lru_cache(1)
    def get_polymarket_http_client(
        api_key: str | None = None,
        api_secret: str | None = None,
        passphrase: str | None = None,
        base_url: str | None = None,
        chain_id: int = factories.POLYGON,
        signature_type: int = 0,
        private_key: str | None = None,
        funder: str | None = None,
    ):
        if signature_type != 3:
            return original_get_http_client(
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                base_url=base_url,
                chain_id=chain_id,
                signature_type=signature_type,
                private_key=private_key,
                funder=funder,
            )

        creds = V2ApiCreds(
            api_key=api_key or factories.get_polymarket_api_key(),
            api_secret=api_secret or factories.get_polymarket_api_secret(),
            api_passphrase=passphrase or factories.get_polymarket_passphrase(),
        )
        key = private_key or factories.get_polymarket_private_key()
        funder = funder or factories.get_polymarket_funder()
        return PolymarketV2CompatClient(
            base_url or "https://clob.polymarket.com",
            chain_id=chain_id,
            signature_type=signature_type,
            creds=creds,
            key=key,
            funder=funder,
        )

    factories.get_polymarket_http_client = get_polymarket_http_client
    _PATCH_APPLIED = True
    return True
