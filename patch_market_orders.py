"""
Patch for PolymarketExecutionClient market buys.

BUY market orders must be quote-denominated, meaning order.quantity is the
USDC.e amount to spend. This patch keeps that behavior explicit and prevents
token-denominated BUY market orders from being submitted accidentally.

How Polymarket market orders work:
  - BUY:  amount = USDC.e to spend (e.g. 1.0 = spend $1)
  - SELL: amount = tokens to sell (e.g. 5.0 = sell 5 tokens)

Minimum order: $1 USDC.e for market BUY orders (NOT 5 tokens).
The 5-token minimum only applies to LIMIT orders.
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_patch_applied = False
_auto_redeem_handlers = []


def register_auto_redeem_handler(handler):
    """Register a synchronous callback for Polymarket auto_redeem events."""
    if handler not in _auto_redeem_handlers:
        _auto_redeem_handlers.append(handler)


def unregister_auto_redeem_handler(handler):
    """Remove a previously registered auto_redeem callback."""
    try:
        _auto_redeem_handlers.remove(handler)
    except ValueError:
        pass


def _dispatch_auto_redeem(payload):
    """Forward auto_redeem payloads to registered bot handlers."""
    for handler in list(_auto_redeem_handlers):
        try:
            handler(dict(payload))
        except Exception as exc:
            # Handler order is fail-closed: a failing handler aborts later handlers
            # and lets the websocket layer surface the settlement-path exception.
            logger.exception("auto_redeem handler failed; stopping websocket event consumption: %s", exc)
            raise


def apply_market_order_patch():
    """Apply monkey patch to PolymarketExecutionClient."""
    global _patch_applied

    if _patch_applied:
        logger.info("Market order patch already applied")
        return True

    try:
        from nautilus_trader.adapters.polymarket.execution import PolymarketExecutionClient
        from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_token_id
        from nautilus_trader.adapters.polymarket.http.conversion import convert_tif_to_polymarket_order_type
        from nautilus_trader.model.enums import OrderSide, order_side_to_str
        from nautilus_trader.common.enums import LogColor
        from py_clob_client.client import MarketOrderArgs, PartialCreateOrderOptions

        async def _patched_submit_market_order(self, command, instrument):
            """
            Patched market order handler.

            For BUY orders:  use quote quantity as the USDC.e amount via create_market_order.
            For SELL orders: use token quantity as normal (base-denominated).
            """
            order = command.order

            if order.side == OrderSide.BUY:
                if not order.is_quote_quantity:
                    self._deny_market_order_quantity(
                        order,
                        "Polymarket market BUY orders require quote-denominated quantities; "
                        "resubmit with `quote_quantity=True`",
                    )
                    return

                usd_amount = float(order.quantity)

                self._log.info(
                    f"[PATCH] BUY market order → spending ${usd_amount:.2f} USDC.e",
                    LogColor.MAGENTA,
                )

                order_type = convert_tif_to_polymarket_order_type(order.time_in_force)

                market_order_args = MarketOrderArgs(
                    token_id=get_polymarket_token_id(order.instrument_id),
                    amount=usd_amount,          # ← USD, not tokens
                    side=order_side_to_str(order.side),
                    order_type=order_type,
                )

                neg_risk = self._get_neg_risk_for_instrument(instrument)
                options = PartialCreateOrderOptions(neg_risk=neg_risk)

                signing_start = self._clock.timestamp()
                signed_order = await asyncio.to_thread(
                    self._http_client.create_market_order,
                    market_order_args,
                    options=options,
                )
                interval = self._clock.timestamp() - signing_start
                self._log.info(
                    f"[PATCH] Signed market BUY in {interval:.3f}s (${usd_amount:.2f})",
                    LogColor.BLUE,
                )

                self.generate_order_submitted(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    ts_event=self._clock.timestamp_ns(),
                )

                await self._post_signed_order(order, signed_order)

            else:
                # SELL: use token quantity (base-denominated), standard behavior
                if order.is_quote_quantity:
                    self._deny_market_order_quantity(
                        order,
                        "Polymarket market SELL orders require base-denominated quantities; "
                        "resubmit with `quote_quantity=False`",
                    )
                    return

                amount = float(order.quantity)
                order_type = convert_tif_to_polymarket_order_type(order.time_in_force)

                market_order_args = MarketOrderArgs(
                    token_id=get_polymarket_token_id(order.instrument_id),
                    amount=amount,
                    side=order_side_to_str(order.side),
                    order_type=order_type,
                )

                neg_risk = self._get_neg_risk_for_instrument(instrument)
                options = PartialCreateOrderOptions(neg_risk=neg_risk)

                signing_start = self._clock.timestamp()
                signed_order = await asyncio.to_thread(
                    self._http_client.create_market_order,
                    market_order_args,
                    options=options,
                )
                interval = self._clock.timestamp() - signing_start
                self._log.info(f"Signed Polymarket market SELL in {interval:.3f}s", LogColor.BLUE)

                self.generate_order_submitted(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    ts_event=self._clock.timestamp_ns(),
                )

                await self._post_signed_order(order, signed_order)

        original_handle_ws_message = PolymarketExecutionClient._handle_ws_message

        def _patched_handle_ws_message(self, raw: bytes) -> None:
            try:
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            except Exception:
                return original_handle_ws_message(self, raw)

            if payload.get("event_type") == "auto_redeem":
                payload_keys = ",".join(sorted(str(key) for key in payload.keys()))
                self._log.info(
                    "[PATCH] Handling Polymarket auto_redeem websocket event "
                    f"slug={payload.get('slug')} amount={payload.get('amount')} "
                    f"txn={payload.get('txn_hash')} keys={payload_keys} "
                    f"asset_id={payload.get('asset_id')} assetId={payload.get('assetId')} "
                    f"token_id={payload.get('token_id')} tokenId={payload.get('tokenId')} "
                    f"clobTokenId={payload.get('clobTokenId')} outcome={payload.get('outcome')} "
                    f"side={payload.get('side')}",
                    LogColor.BLUE,
                )
                _dispatch_auto_redeem(payload)
                return

            return original_handle_ws_message(self, raw)

        # Apply the patch
        PolymarketExecutionClient._submit_market_order = _patched_submit_market_order
        PolymarketExecutionClient._handle_ws_message = _patched_handle_ws_message
        _patch_applied = True
        logger.info(
            "Market order patch applied — BUY orders use quote_quantity as USDC.e spend; "
            "auto_redeem websocket events are handled locally"
        )
        return True

    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to apply market order patch: {e}")
        import traceback
        traceback.print_exc()
        return False
