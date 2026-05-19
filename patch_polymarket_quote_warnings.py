"""Suppress noisy 'Dropping QuoteTick' WARN logs from the Polymarket data client.

These warnings fire whenever a Polymarket binary-option book is one-sided (which
is the normal state for resolved or far-future BTC-15min markets). The bot
subscribes to ~98 markets in the rolling 24-hour interval, so the noise
dominates the log even though the active market is healthy.

We replace the two PolymarketDataClient methods that emit the noisy warning with
copies that omit just those log calls. Everything else (other WARN/INFO from the
data client, and the quote-drop behaviour itself) is unchanged.

NOTE: pinned to nautilus_trader's Polymarket adapter as of 1.227.0. If that
adapter is updated, re-verify the two methods here still match upstream.
"""

from __future__ import annotations


_PATCH_APPLIED = False


def apply_polymarket_quote_warning_patch() -> bool:
    """Replace PolymarketDataClient._handle_book_snapshot/_handle_quote with no-warn variants."""
    global _PATCH_APPLIED

    if _PATCH_APPLIED:
        return True

    try:
        from nautilus_trader.adapters.polymarket.common.constants import (
            POLYMARKET_MAX_PRICE,
            POLYMARKET_MIN_PRICE,
        )
        from nautilus_trader.adapters.polymarket.common.enums import PolymarketOrderSide
        from nautilus_trader.adapters.polymarket.data import PolymarketDataClient
        from nautilus_trader.adapters.polymarket.schemas.book import (
            PolymarketQuote,
            PolymarketQuotes,
        )
        from nautilus_trader.adapters.polymarket.schemas.book import PolymarketBookSnapshot
        from nautilus_trader.common.enums import LogColor
        from nautilus_trader.core.datetime import millis_to_nanos
        from nautilus_trader.model.data import (
            BookOrder,
            OrderBookDelta,
            OrderBookDeltas,
            QuoteTick,
        )
        from nautilus_trader.model.enums import BookAction, OrderSide, RecordFlag
        from nautilus_trader.model.instruments import BinaryOption
    except ImportError:
        return False

    async def _handle_book_snapshot(
        self,
        instrument: BinaryOption,
        ws_message: PolymarketBookSnapshot,
    ) -> None:
        now_ns = self._clock.timestamp_ns()
        deltas = ws_message.parse_to_snapshot(instrument=instrument, ts_init=now_ns)

        if deltas is None:
            return

        if instrument.id in self._pending_snapshot_after_tick_change:
            self._pending_snapshot_after_tick_change.discard(instrument.id)
            self._log.info(
                f"Resumed book for {instrument.id} after tick size change",
                LogColor.BLUE,
            )

        self._handle_deltas(instrument, deltas)

        if instrument.id in self.subscribed_quote_ticks():
            quote = ws_message.parse_to_quote(
                instrument=instrument,
                ts_init=now_ns,
                drop_quotes_missing_side=self._config.drop_quotes_missing_side,
            )

            if quote is None:
                # Original emits a WARN here; suppressed to keep the log readable
                # when subscribed to many markets that are resolved/one-sided.
                return
            self._last_quotes[instrument.id] = quote
            self._handle_data(quote)

    def _handle_quote(
        self,
        instrument: BinaryOption,
        ws_message: PolymarketQuotes,
        price_change: PolymarketQuote,
    ) -> None:
        if instrument.id in self._pending_snapshot_after_tick_change:
            self._log.debug(
                f"Dropping price_change for {instrument.id}: awaiting snapshot after tick size change",
            )
            return

        now_ns = self._clock.timestamp_ns()

        order = BookOrder(
            side=OrderSide.BUY if price_change.side == PolymarketOrderSide.BUY else OrderSide.SELL,
            price=instrument.make_price(float(price_change.price)),
            size=instrument.make_qty(float(price_change.size)),
            order_id=0,
        )
        delta = OrderBookDelta(
            instrument_id=instrument.id,
            action=BookAction.UPDATE if order.size > 0 else BookAction.DELETE,
            order=order,
            flags=RecordFlag.F_LAST,
            sequence=0,
            ts_event=millis_to_nanos(float(ws_message.timestamp)),
            ts_init=now_ns,
        )
        deltas = OrderBookDeltas(instrument.id, [delta])

        if instrument.id not in self._local_books:
            if (
                instrument.id not in self.subscribed_quote_ticks()
                and instrument.id not in self.subscribed_order_book_deltas()
            ):
                return
            self._create_local_book(instrument.id)

        local_book = self._local_books[instrument.id]
        local_book.apply(deltas)

        self._handle_data(deltas)

        if instrument.id in self.subscribed_quote_ticks():
            bid_price = local_book.best_bid_price()
            ask_price = local_book.best_ask_price()
            bid_size = local_book.best_bid_size()
            ask_size = local_book.best_ask_size()

            if bid_price is None or ask_price is None:
                if self._config.drop_quotes_missing_side:
                    # Original emits a WARN here; suppressed (see module docstring).
                    return
                else:
                    if bid_price is None:
                        bid_price = instrument.make_price(POLYMARKET_MIN_PRICE)
                        bid_size = instrument.make_qty(0.0)
                    if ask_price is None:
                        ask_price = instrument.make_price(POLYMARKET_MAX_PRICE)
                        ask_size = instrument.make_qty(0.0)

            quote = QuoteTick(
                instrument_id=instrument.id,
                bid_price=bid_price,
                ask_price=ask_price,
                bid_size=bid_size,
                ask_size=ask_size,
                ts_event=millis_to_nanos(float(ws_message.timestamp)),
                ts_init=self._clock.timestamp_ns(),
            )

            last_quote = self._last_quotes.get(instrument.id)

            if last_quote is not None and (
                quote.bid_price == last_quote.bid_price
                and quote.ask_price == last_quote.ask_price
                and quote.bid_size == last_quote.bid_size
                and quote.ask_size == last_quote.ask_size
            ):
                return

            self._last_quotes[instrument.id] = quote
            self._handle_data(quote)

    PolymarketDataClient._handle_book_snapshot = _handle_book_snapshot
    PolymarketDataClient._handle_quote = _handle_quote

    _PATCH_APPLIED = True
    return True
