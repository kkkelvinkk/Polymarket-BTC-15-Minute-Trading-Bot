"""Patch Polymarket reconciliation hooks without overriding market submit.

Nautilus 1.228.0 has the required market-order implementation for quote
quantity buys: it builds MarketOrderArgsV2, passes the USDC balance, computes
the base quantity from the signed order, and calls _post_signed_order with the
expected venue order id. This module must not replace that submit path.
"""

import inspect
import json
import logging
import re

logger = logging.getLogger(__name__)

_patch_applied = False
_patch_market_interval_seconds = None
_auto_redeem_handlers = []
_actual_fill_handlers = []


def _source_contains_client_order_id_uuid_fallback(source: str) -> bool:
    """Detect unreconcilable synthetic client-order-id generation."""
    uuid_fallback_pattern = re.compile(
        r"ClientOrderId\s*\(\s*str\s*\(\s*UUID4\s*\(\s*\)\s*\)\s*\)"
    )
    return bool(uuid_fallback_pattern.search(source))


def verify_no_nautilus_client_order_id_uuid_fallback(client_cls=None) -> bool:
    """
    Fail closed if installed Nautilus can synthesize a ClientOrderId from UUID4.

    The bot's reconciliation path must use a real client_order_id or first-class
    venue_order_id metadata. A UUID client id is externally unreconcilable.
    """
    if client_cls is None:
        from nautilus_trader.adapters.polymarket.execution import PolymarketExecutionClient

        client_cls = PolymarketExecutionClient
    methods_to_check = (
        "generate_order_status_reports",
        "_parse_trades_response_object",
    )
    fallback_sites = []
    for method_name in methods_to_check:
        method = getattr(client_cls, method_name, None)
        if method is None:
            fallback_sites.append(f"{method_name}:missing_method")
            continue
        try:
            source = inspect.getsource(method)
        except (OSError, TypeError) as exc:
            fallback_sites.append(f"{method_name}:source_unavailable:{type(exc).__name__}")
            continue
        if _source_contains_client_order_id_uuid_fallback(source):
            fallback_sites.append(method_name)
    if fallback_sites:
        raise RuntimeError(
            "Installed Nautilus Polymarket adapter still contains "
            "ClientOrderId-via-UUID4 fallback in "
            f"{', '.join(fallback_sites)}. Live startup is blocked until the "
            "adapter paths are patched in tracked code or the installed adapter "
            "is upgraded to fail closed without synthetic client ids."
        )
    return True


def register_auto_redeem_handler(handler):
    """Register a synchronous callback for Polymarket auto_redeem events."""
    if handler not in _auto_redeem_handlers:
        _auto_redeem_handlers.append(handler)


def unregister_auto_redeem_handler(handler):
    """Remove a previously registered auto_redeem callback."""
    _auto_redeem_handlers.remove(handler)


def _dispatch_auto_redeem(payload):
    """Forward auto_redeem payloads to registered bot handlers."""
    handlers = list(_auto_redeem_handlers)
    if not handlers:
        raise RuntimeError("auto_redeem dispatch has no registered handler; refusing to drop redeem data")
    for handler in handlers:
        try:
            handler(dict(payload))
        except Exception as exc:
            # Handler order is fail-closed: a failing handler aborts later handlers
            # and lets the websocket layer surface the settlement-path exception.
            logger.exception("auto_redeem handler failed; stopping websocket event consumption: %s", exc)
            raise


def register_actual_fill_handler(handler):
    """Register a synchronous callback for adapter-observed actual fills."""
    if handler not in _actual_fill_handlers:
        _actual_fill_handlers.append(handler)


def unregister_actual_fill_handler(handler):
    """Remove a previously registered actual-fill callback."""
    _actual_fill_handlers.remove(handler)


def _dispatch_actual_fill(client_order_id, payload):
    """Forward actual-fill payloads to registered bot handlers."""
    normalized_client_order_id = None if client_order_id in (None, "") else str(client_order_id)
    handlers = list(_actual_fill_handlers)
    if not handlers:
        raise RuntimeError("actual_fill dispatch has no registered handler; refusing to drop fill data")
    for handler in handlers:
        try:
            handler(normalized_client_order_id, dict(payload))
        except Exception as exc:
            logger.exception("actual_fill handler failed; stopping fill processing: %s", exc)
            raise


_uuid_guard_applied = False
_uuid_guard_market_interval_seconds = None


def _datetime_now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


_UUID_GUARD_PINNED_NAUTILUS_VERSION = "1.228.0"


def apply_uuid_fallback_guard_patch(*, market_interval_seconds: int):
    """Replace the 3 ClientOrderId-via-UUID4 fallback sites
    in nautilus_trader 1.228.0 ``PolymarketExecutionClient`` with structured
    fail-closed dispatches.

    Affected sites (installed 1.228.0 source):
      - ``generate_order_status_reports`` active-order loop (line ~431).
      - ``generate_order_status_reports`` ``generate_order_history_from_trades``
        branch (line ~534).
      - ``_parse_trades_response_object`` (line ~879).

    The patch also scopes aggregate order-status and fill-report generation
    to cached Polymarket market IDs before decoding rows. Nautilus asks for
    startup mass status with ``instrument_id=None``; the upstream adapter
    turns that into account-wide ``get_open_orders`` / ``get_trades``
    requests, so unrelated account history can fail schema decode before
    loaded-instrument filters run.

    Each replacement: when ``self._cache.client_order_id(venue_order_id)``
    returns ``None``, dispatch ``_dispatch_actual_fill(None, payload)`` with
    ``reason="unmapped_venue_order_id"`` and skip the report build. This
    preserves the No-Fallback policy: UUID synthesis is removed; the durable
    failure-callback path records the unreconcilable venue order id so the
    operator can resolve via ``--venue-order-id``.

    This patch MUST be applied before
    ``verify_no_nautilus_client_order_id_uuid_fallback`` so the verify reads
    the patched source (no UUID4 marker) and lets live startup proceed.

    Two intentional deviations from upstream 1.228.0 source:

    1. **Site 2 cache lookup (history-from-trades branch).** Upstream
       unconditionally synthesizes a UUID at this site; the patch consults the
       cache first and reuses the real client_order_id when one exists. When
       the cache is empty, the patch fails closed via ``_dispatch_actual_fill``
       rather than synthesizing. Strictly safer than upstream and consistent
       with the No-Fallback policy.

    2. **Omitted debug `self._log.warning` lines.** The upstream
       history-from-trades branch emits four `self._log.warning(f"...")` debug
       breadcrumbs (`venue_order_id=`, `avg_px=`, `filled_qty=`,
       `Generated from fill report: {report}`). The patch restores these to
       keep operator-facing diagnostics identical to upstream.

    Installed-version guard: this patch is tied to nautilus_trader
    ``{_UUID_GUARD_PINNED_NAUTILUS_VERSION}``. If the installed version
    differs, the patch refuses to apply so a routine version bump cannot
    silently install a stale verbatim copy of method bodies that no longer
    match the upstream API.
    """
    global _uuid_guard_applied
    global _uuid_guard_market_interval_seconds

    if _uuid_guard_applied:
        if _uuid_guard_market_interval_seconds != market_interval_seconds:
            raise RuntimeError(
                "UUID-fallback guard patch already applied with "
                f"market_interval_seconds={_uuid_guard_market_interval_seconds}, "
                f"refusing reapply with {market_interval_seconds}"
            )
        return True

    if not isinstance(market_interval_seconds, int) or market_interval_seconds <= 0:
        raise RuntimeError(
            "apply_uuid_fallback_guard_patch requires a positive integer "
            "market_interval_seconds"
        )

    try:
        import nautilus_trader
    except ImportError as exc:
        raise RuntimeError(
            "apply_uuid_fallback_guard_patch requires nautilus_trader to be installed"
        ) from exc
    installed_version = getattr(nautilus_trader, "__version__", "<unknown>")
    if installed_version != _UUID_GUARD_PINNED_NAUTILUS_VERSION:
        raise RuntimeError(
            "UUID-fallback guard patch is pinned to nautilus_trader "
            f"{_UUID_GUARD_PINNED_NAUTILUS_VERSION}, but installed version is "
            f"{installed_version}. Re-run the clean-env Nautilus audit "
            "before bumping the pin: the patched method bodies are verbatim "
            "copies of the 1.228.0 source and may have drifted upstream."
        )

    import asyncio  # noqa: F401  -- re-imported for clarity inside the patch
    from collections import defaultdict

    import msgspec

    from nautilus_trader.adapters.polymarket.common.constants import POLYMARKET_VENUE
    from nautilus_trader.adapters.polymarket.common.symbol import (
        get_polymarket_condition_id,
        get_polymarket_instrument_id,
        get_polymarket_token_id,
    )
    from nautilus_trader.adapters.polymarket.execution import PolymarketExecutionClient
    from nautilus_trader.common.enums import LogLevel
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.execution.messages import (
        GenerateFillReports,
        GenerateOrderStatusReport,
        GenerateOrderStatusReports,
    )
    from nautilus_trader.execution.reports import FillReport, OrderStatusReport
    from nautilus_trader.model.enums import (
        ContingencyType,
        LiquiditySide,
        OrderStatus,
        OrderType,
        TimeInForce,
    )
    from nautilus_trader.model.identifiers import ClientOrderId, TradeId, VenueOrderId
    # Mirror upstream nautilus_trader 1.228.0 import path verbatim (re-export
    # via py_clob_client_v2.client; canonical class lives in clob_types).
    from py_clob_client_v2.client import OpenOrderParams
    from py_clob_client_v2.clob_types import TradeParams

    def _timestamp_to_ns(ts):
        return int(ts.timestamp() * 1_000_000_000)

    def _reconciliation_window_ns(self, command):
        if command.start is None:
            raise RuntimeError(
                "Polymarket aggregate reconciliation requires command.start "
                "to scope loaded-market queries"
            )
        start_ns = _timestamp_to_ns(command.start)
        if command.end is None:
            end_ns = self._clock.timestamp_ns()
        else:
            end_ns = _timestamp_to_ns(command.end)
        if end_ns < start_ns:
            raise RuntimeError(
                "Polymarket aggregate reconciliation command.end is earlier "
                "than command.start"
            )
        return start_ns, end_ns

    def _market_window_ns_from_slug(instrument):
        # The Polymarket BTC 15-min slug encodes the market START as a trailing
        # Unix-seconds timestamp (e.g. "btc-updown-15m-1718900000"); the market
        # closes market_interval_seconds later. instrument.expiration_ns is
        # parsed from Gamma's DATE-only endDateIso, which collapses to midnight
        # UTC and is wrong for intraday markets — using it here drops every
        # market from the reconciliation window. Derive the window from the slug
        # exactly as bot.py _load_all_btc_instruments does.
        info = instrument.info
        if not info:
            raise RuntimeError(
                "Polymarket aggregate reconciliation: instrument "
                f"{instrument.id} carries no info to derive its market slug"
            )
        slug = info.get("market_slug")
        if not slug:
            raise RuntimeError(
                "Polymarket aggregate reconciliation: instrument "
                f"{instrument.id} carries no market_slug to derive its window"
            )
        slug_tail = str(slug).rsplit("-", 1)[-1]
        try:
            market_start_seconds = int(slug_tail)
        except ValueError as exc:
            raise RuntimeError(
                "Polymarket aggregate reconciliation: instrument "
                f"{instrument.id} slug {slug!r} has no trailing Unix-seconds "
                "timestamp to derive its market window"
            ) from exc
        market_start_ns = market_start_seconds * 1_000_000_000
        market_end_ns = market_start_ns + market_interval_seconds * 1_000_000_000
        return market_start_ns, market_end_ns

    def _loaded_polymarket_reconciliation_instruments(self, command):
        start_ns, end_ns = _reconciliation_window_ns(self, command)
        scoped = []
        for instrument in self._instrument_provider.list_all():
            market_start_ns, market_end_ns = _market_window_ns_from_slug(instrument)
            if market_end_ns < start_ns or market_start_ns > end_ns:
                continue
            scoped.append(
                (
                    get_polymarket_condition_id(instrument.id),
                    get_polymarket_token_id(instrument.id),
                    instrument.id,
                )
            )
        if not scoped:
            raise RuntimeError(
                "Polymarket aggregate reconciliation found no provider-loaded "
                "instruments overlapping command.start/command.end"
            )
        return scoped

    def _loaded_polymarket_condition_ids(self, command):
        condition_ids = []
        seen = set()
        for condition_id, _token_id, _instrument_id in (
            _loaded_polymarket_reconciliation_instruments(self, command)
        ):
            if condition_id in seen:
                continue
            seen.add(condition_id)
            condition_ids.append(condition_id)
        return condition_ids

    async def _ensure_polymarket_provider_initialized(self):
        await self._instrument_provider.initialize()

    async def _patched_generate_order_status_reports(self, command):
        # Verbatim copy of nautilus_trader 1.228.0
        # PolymarketExecutionClient.generate_order_status_reports EXCEPT the
        # two ClientOrderId-via-UUID4 fallbacks are replaced with
        # _dispatch_actual_fill failure callbacks.
        self._log.debug("Requesting OrderStatusReports...")
        await _ensure_polymarket_provider_initialized(self)
        reports = []

        if command.instrument_id is not None:
            condition_id = get_polymarket_condition_id(command.instrument_id)
            asset_id = get_polymarket_token_id(command.instrument_id)
            scoped_open_order_requests = [
                (
                    OpenOrderParams(market=condition_id, asset_id=asset_id),
                    [command.instrument_id],
                )
            ]
        else:
            scoped_open_order_requests = [
                (
                    OpenOrderParams(market=condition_id, asset_id=token_id),
                    [instrument_id],
                )
                for condition_id, token_id, instrument_id in (
                    _loaded_polymarket_reconciliation_instruments(self, command)
                )
            ]

        seen_open_venue_order_ids = set()
        for params, details in scoped_open_order_requests:
            retry_manager = await self._retry_manager_pool.acquire()
            try:
                response = await retry_manager.run(
                    "generate_order_status_reports",
                    details,
                    asyncio.to_thread,
                    self._http_client.get_open_orders,
                    params=params,
                )

                if response:
                    for json_obj in response:
                        raw = msgspec.json.encode(json_obj)
                        polymarket_order = self._decoder_order_report.decode(raw)

                        instrument_id = get_polymarket_instrument_id(
                            polymarket_order.market,
                            polymarket_order.asset_id,
                        )
                        instrument = self._instrument_provider.find(instrument_id)
                        if instrument is None:
                            self._log.warning(
                                f"Cannot handle order report: instrument {instrument_id} not found "
                                f"(market={polymarket_order.market}, asset_id={polymarket_order.asset_id})",
                            )
                            continue

                        venue_order_id = polymarket_order.get_venue_order_id()
                        if venue_order_id in seen_open_venue_order_ids:
                            continue
                        seen_open_venue_order_ids.add(venue_order_id)
                        client_order_id = self._cache.client_order_id(venue_order_id)
                        if client_order_id is None:
                            # UUID-fallback guard (site 1 of 3): no
                            # synthetic client_order_id. Dispatch failure callback
                            # and skip this report.
                            _dispatch_actual_fill(
                                None,
                                {
                                    "status": "failed",
                                    "reason": "unmapped_venue_order_id",
                                    "venue_order_id": str(venue_order_id),
                                    "raw_status_report": json_obj,
                                    "report_source": "generate_order_status_reports.active_order_loop",
                                    "report_received_at": _datetime_now_iso(),
                                },
                            )
                            continue

                        report = polymarket_order.parse_to_order_status_report(
                            account_id=self.account_id,
                            instrument=instrument,
                            client_order_id=client_order_id,
                            ts_init=self._clock.timestamp_ns(),
                        )
                        reports.append(report)
            finally:
                await self._retry_manager_pool.release(retry_manager)

        if self._config.generate_order_history_from_trades:
            original_command_start = command.start
            original_command_end = command.end
            original_log_receipt_level = command.log_receipt_level
            self._log.warning(
                "Experimental feature not currently recommended: generating order history from trades",
            )
            reported_client_order_ids = {r.client_order_id for r in reports}
            for order in self._cache.orders_open(venue=POLYMARKET_VENUE):
                if order.client_order_id in reported_client_order_ids:
                    continue

                # Mirror upstream Nautilus 1.228.0's variable shadowing exactly:
                # the `command` parameter is reassigned inside this loop, so the
                # downstream `fill_command = GenerateFillReports(instrument_id=
                # command.instrument_id, ...)` uses the LAST sub-command's
                # instrument_id (an individual open order). Reviewer #2 cycle-3
                # flagged this scope difference as a P1 semantic deviation — we
                # preserve upstream behavior verbatim instead of "fixing" it.
                command = GenerateOrderStatusReport(
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    venue_order_id=order.venue_order_id,
                    command_id=UUID4(),
                    ts_init=self._clock.timestamp_ns(),
                )
                maybe_report = await self.generate_order_status_report(command)
                if maybe_report:
                    reports.append(maybe_report)

            known_venue_order_ids = {o.venue_order_id for o in self._cache.orders()}
            known_venue_order_ids.update({r.venue_order_id for r in reports})

            # Operational caveat (verbatim upstream behavior, preserved):
            # `command` here is the LAST loop-shadowed GenerateOrderStatusReport
            # built from `order.instrument_id`, NOT the original aggregate command
            # passed into generate_order_status_reports. Under multi-market queries
            # (orders spanning more than one instrument), this fill request will
            # filter on the instrument of whichever open order was processed last.
            # Single-instrument deployments (current 15-min BTC bot) are unaffected.
            fill_command = GenerateFillReports(
                instrument_id=command.instrument_id,
                venue_order_id=None,
                start=original_command_start,
                end=original_command_end,
                command_id=UUID4(),
                ts_init=self._clock.timestamp_ns(),
            )
            fill_reports = await self.generate_fill_reports(fill_command)
            if fill_reports and not known_venue_order_ids:
                self._log.warning(
                    "No previously known venue order IDs found in cache or from active orders",
                )

            venue_order_id_fill_reports = defaultdict(list)
            for fill in fill_reports:
                if fill.venue_order_id in known_venue_order_ids:
                    continue
                venue_order_id_fill_reports[fill.venue_order_id].append(fill)

            for venue_order_id, fr_list in venue_order_id_fill_reports.items():
                # UUID-fallback guard (site 2 of 3): cache lookup is
                # not in installed Nautilus's 1.228.0 source here — Nautilus
                # bypasses the cache entirely and synthesizes a UUID for every
                # venue_order_id reconstructed from fills. Our guard refuses
                # to fabricate a client id from venue data: dispatch the
                # failure and skip the synthetic report.
                client_order_id = self._cache.client_order_id(venue_order_id)
                if client_order_id is None:
                    _dispatch_actual_fill(
                        None,
                        {
                            "status": "failed",
                            "reason": "unmapped_venue_order_id",
                            "venue_order_id": str(venue_order_id),
                            "raw_status_report": None,
                            "report_source": "generate_order_status_reports.history_from_trades",
                            "report_received_at": _datetime_now_iso(),
                            "fill_count": len(fr_list),
                        },
                    )
                    continue

                first_fill = fr_list[0]
                instrument = self._instrument_provider.find(first_fill.instrument_id)
                if instrument is None:
                    self._log.warning(
                        f"Cannot handle order report: instrument {first_fill.instrument_id} not found "
                        f"(venue_order_id={venue_order_id})",
                    )
                    continue

                order_type = (
                    OrderType.MARKET
                    if first_fill.liquidity_side == LiquiditySide.TAKER
                    else OrderType.LIMIT
                )
                price = first_fill.last_px if order_type == OrderType.LIMIT else None
                order_side = first_fill.order_side

                avg_px_float = 0.0
                filled_qty_float = 0.0
                ts_last = first_fill.ts_event
                for fr in fr_list:
                    avg_px_float += float(fr.last_px) * float(fr.last_qty)
                    filled_qty_float += float(fr.last_qty)
                    ts_last = fr.ts_event
                if filled_qty_float > 0:
                    avg_px_float /= filled_qty_float
                else:
                    avg_px_float = 0.0

                # Restored from upstream 1.228.0 source (reviewer #1 finding
                # #8): preserve the operator-facing debug breadcrumbs so log
                # output of the patched method is identical to upstream.
                self._log.warning(f"{venue_order_id=}")
                self._log.warning(f"{avg_px_float=}")
                self._log.warning(f"{filled_qty_float=}")

                report = OrderStatusReport(
                    account_id=first_fill.account_id,
                    instrument_id=first_fill.instrument_id,
                    client_order_id=client_order_id,
                    order_list_id=None,
                    venue_order_id=venue_order_id,
                    order_side=order_side,
                    order_type=order_type,
                    contingency_type=ContingencyType.NO_CONTINGENCY,
                    time_in_force=TimeInForce.GTC,
                    order_status=OrderStatus.FILLED,
                    price=price,
                    avg_px=instrument.make_price(avg_px_float),
                    quantity=instrument.make_qty(filled_qty_float),
                    filled_qty=instrument.make_qty(filled_qty_float),
                    ts_accepted=ts_last,
                    ts_last=ts_last,
                    report_id=UUID4(),
                    ts_init=self._clock.timestamp_ns(),
                )
                self._log.warning(f"Generated from fill report: {report}")
                reports.append(report)

        self._log_report_receipt(
            len(reports),
            "OrderStatusReport",
            original_log_receipt_level
            if self._config.generate_order_history_from_trades
            else command.log_receipt_level,
        )
        return reports

    async def _patched_generate_fill_reports(self, command):
        # Upstream-compatible PolymarketExecutionClient.generate_fill_reports,
        # except aggregate commands are market-scoped before trade decode.
        self._log.debug("Requesting FillReports...")
        await _ensure_polymarket_provider_initialized(self)
        reports = []

        if command.instrument_id is not None:
            condition_ids = [get_polymarket_condition_id(command.instrument_id)]
        else:
            condition_ids = _loaded_polymarket_condition_ids(self, command)

        parsed_fill_keys = set()

        for condition_id in condition_ids:
            params = TradeParams()
            params.market = condition_id

            if command.start is not None:
                params.after = int(command.start.timestamp())
            if command.end is not None:
                params.before = int(command.end.timestamp())

            retry_manager = await self._retry_manager_pool.acquire()
            try:
                response = await retry_manager.run(
                    "generate_fill_reports",
                    [condition_id],
                    asyncio.to_thread,
                    self._http_client.get_trades,
                    params=params,
                )

                if response:
                    for json_obj in response:
                        self._parse_trades_response_object(
                            command=command,
                            json_obj=json_obj,
                            parsed_fill_keys=parsed_fill_keys,
                            reports=reports,
                        )
            finally:
                await self._retry_manager_pool.release(retry_manager)

        self._log_report_receipt(len(reports), "FillReport", LogLevel.INFO)

        return reports

    def _patched_parse_trades_response_object(
        self, command, json_obj, parsed_fill_keys, reports
    ):
        # Verbatim copy of nautilus_trader 1.228.0
        # PolymarketExecutionClient._parse_trades_response_object EXCEPT the
        # ClientOrderId-via-UUID4 fallback is replaced with
        # _dispatch_actual_fill and a continue.
        raw = msgspec.json.encode(json_obj)
        polymarket_trade = self._decoder_trade_report.decode(raw)

        filled_user_order_ids = polymarket_trade.get_filled_user_order_ids(
            self._wallet_address,
            self._api_key,
        )

        for order_id in filled_user_order_ids:
            asset_id = polymarket_trade.get_asset_id(order_id)
            instrument_id = get_polymarket_instrument_id(polymarket_trade.market, asset_id)

            if command.instrument_id is not None and instrument_id != command.instrument_id:
                continue

            instrument = self._instrument_provider.find(instrument_id)
            if instrument is None:
                self._log.warning(
                    f"Cannot handle trade report: instrument {instrument_id} not found "
                    f"(market={polymarket_trade.market}, asset_id={asset_id})",
                )
                continue

            venue_order_id = polymarket_trade.venue_order_id(order_id)

            if command.venue_order_id is not None and venue_order_id != command.venue_order_id:
                continue

            client_order_id = self._cache.client_order_id(venue_order_id)
            if client_order_id is None:
                # UUID-fallback guard (site 3 of 3): no synthetic
                # client_order_id; dispatch failure callback and skip.
                _dispatch_actual_fill(
                    None,
                    {
                        "status": "failed",
                        "reason": "unmapped_venue_order_id",
                        "venue_order_id": str(venue_order_id),
                        "raw_status_report": json_obj,
                        "report_source": "_parse_trades_response_object",
                        "report_received_at": _datetime_now_iso(),
                        "filled_user_order_id": order_id,
                    },
                )
                continue

            report = polymarket_trade.parse_to_fill_report(
                account_id=self.account_id,
                instrument=instrument,
                client_order_id=client_order_id,
                ts_init=self._clock.timestamp_ns(),
                filled_user_order_id=order_id,
            )

            report.last_qty = self._fill_tracker.snap_fill_qty(venue_order_id, report.last_qty)

            fill_key = (report.trade_id, report.venue_order_id)
            if fill_key in parsed_fill_keys:
                self._log.warning(f"Duplicate fill key {fill_key}, skipping")
                continue

            parsed_fill_keys.add(fill_key)
            reports.append(report)

    PolymarketExecutionClient.generate_order_status_reports = (
        _patched_generate_order_status_reports
    )
    PolymarketExecutionClient.generate_fill_reports = _patched_generate_fill_reports
    PolymarketExecutionClient._parse_trades_response_object = (
        _patched_parse_trades_response_object
    )
    _uuid_guard_applied = True
    _uuid_guard_market_interval_seconds = market_interval_seconds
    logger.info(
        "UUID-fallback guard patch applied (3 sites) with scoped startup reports: "
        "generate_order_status_reports active-order loop, "
        "generate_order_status_reports history-from-trades, "
        "_parse_trades_response_object"
    )
    return True


def apply_market_order_patch(*, market_interval_seconds: int):
    """Apply monkey patch to PolymarketExecutionClient."""
    global _patch_applied
    global _patch_market_interval_seconds

    if _patch_applied:
        if _patch_market_interval_seconds != market_interval_seconds:
            raise RuntimeError(
                "Market order patch already applied with "
                f"market_interval_seconds={_patch_market_interval_seconds}, "
                f"refusing reapply with {market_interval_seconds}"
            )
        logger.info("Market order patch already applied")
        return True

    try:
        from nautilus_trader.adapters.polymarket.execution import PolymarketExecutionClient
        from nautilus_trader.common.enums import LogColor

        # Apply UUID-fallback guard FIRST so the verify reads the
        # patched method source.
        apply_uuid_fallback_guard_patch(
            market_interval_seconds=market_interval_seconds,
        )

        verify_no_nautilus_client_order_id_uuid_fallback(PolymarketExecutionClient)

        original_handle_ws_message = PolymarketExecutionClient._handle_ws_message

        def _patched_handle_ws_message(self, raw: bytes) -> None:
            try:
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError("failed to decode Polymarket websocket JSON message") from exc

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
        PolymarketExecutionClient._handle_ws_message = _patched_handle_ws_message
        _patch_applied = True
        _patch_market_interval_seconds = market_interval_seconds
        logger.info(
            "Market order patch applied — Nautilus native market submit is preserved; "
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
