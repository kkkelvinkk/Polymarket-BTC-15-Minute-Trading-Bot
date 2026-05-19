#!/usr/bin/env python3
"""Explicit admin tool for resolving one SETTLEMENT_UNKNOWN ledger record."""

import argparse
import copy
import fcntl
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parent
LIVE_TRADE_LEDGER_SCHEMA_VERSION = 3
SETTLEMENT_ACCOUNTING_COST_TOLERANCE = Decimal("1E-18")
RESOLVED_SETTLEMENT_SOURCES = {"manual_reconciliation", "auto_redeem", "late_auto_redeem"}


def _fsync_parent_directory(path: Path) -> None:
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _resolve_ledger_path(raw_path: str | None) -> Path:
    if raw_path in (None, ""):
        raise SystemExit("--ledger is required; admin tooling has no default ledger path")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _decimal_arg(name: str, value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SystemExit(f"{name} must be a decimal value") from exc
    if not parsed.is_finite():
        raise SystemExit(f"{name} must be finite")
    return parsed


def _positive_decimal_arg(name: str, value: Any) -> Decimal:
    parsed = _decimal_arg(name, value)
    if parsed <= 0:
        raise SystemExit(f"{name} must be greater than 0")
    return parsed


def _non_negative_decimal_arg(name: str, value: Any) -> Decimal:
    parsed = _decimal_arg(name, value)
    if parsed < 0:
        raise SystemExit(f"{name} must be non-negative")
    return parsed


def _required_non_negative_decimal_arg(name: str, value: Any) -> Decimal:
    if value in (None, ""):
        raise SystemExit(
            f"{name} is required for reconciliation; repair verified fill accounting before resolving"
        )
    return _non_negative_decimal_arg(name, value)


def _required_positive_decimal_arg(name: str, value: Any) -> Decimal:
    if value in (None, ""):
        raise SystemExit(
            f"{name} is required for reconciliation; repair verified fill accounting before resolving"
        )
    return _positive_decimal_arg(name, value)


def _datetime_arg(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"{name} must be an ISO-8601 datetime with timezone") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_pending_actual_fill_aggregate(order_id: str, pending: Dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    if "filled_qty" in pending:
        raise SystemExit(
            f"pending_actual_fills[{order_id}] scalar filled_qty is not valid; "
            "current schema requires aggregate fills[]"
        )
    fills = pending.get("fills")
    if not isinstance(fills, list) or not fills:
        raise SystemExit(f"pending_actual_fills[{order_id}].fills must be a non-empty JSON list")
    seen_fill_keys = set()
    summed_qty = Decimal("0")
    summed_notional = Decimal("0")
    for index, fill in enumerate(fills):
        if not isinstance(fill, dict):
            raise SystemExit(f"pending_actual_fills[{order_id}].fills[{index}] must be a JSON object")
        fill_key = fill.get("fill_key")
        if fill_key in (None, ""):
            raise SystemExit(f"pending_actual_fills[{order_id}].fills[{index}].fill_key is required")
        fill_key = str(fill_key)
        if fill_key in seen_fill_keys:
            raise SystemExit(f"pending_actual_fills[{order_id}] duplicate fill_key={fill_key}")
        seen_fill_keys.add(fill_key)
        fill_qty = _positive_decimal_arg(
            f"pending_actual_fills[{order_id}].fills[{index}].filled_qty",
            fill.get("filled_qty"),
        )
        fill_price = _positive_decimal_arg(
            f"pending_actual_fills[{order_id}].fills[{index}].price",
            fill.get("price"),
        )
        if fill_price > 1:
            raise SystemExit(f"pending_actual_fills[{order_id}].fills[{index}].price must be <= 1")
        fill_notional = _positive_decimal_arg(
            f"pending_actual_fills[{order_id}].fills[{index}].notional",
            fill.get("notional"),
        )
        if abs((fill_qty * fill_price) - fill_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
            raise SystemExit(f"pending_actual_fills[{order_id}].fills[{index}].notional is inconsistent")
        summed_qty += fill_qty
        summed_notional += fill_notional
    total_qty = _positive_decimal_arg(
        f"pending_actual_fills[{order_id}].total_filled_qty",
        pending.get("total_filled_qty"),
    )
    total_notional = _positive_decimal_arg(
        f"pending_actual_fills[{order_id}].total_filled_notional",
        pending.get("total_filled_notional"),
    )
    vwap = _positive_decimal_arg(f"pending_actual_fills[{order_id}].vwap", pending.get("vwap"))
    if vwap > 1:
        raise SystemExit(f"pending_actual_fills[{order_id}].vwap must be <= 1")
    if abs(total_qty - summed_qty) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
        raise SystemExit(f"pending_actual_fills[{order_id}].total_filled_qty does not match fills[]")
    if abs(total_notional - summed_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
        raise SystemExit(f"pending_actual_fills[{order_id}].total_filled_notional does not match fills[]")
    if abs((total_qty * vwap) - total_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
        raise SystemExit(f"pending_actual_fills[{order_id}].vwap is inconsistent")
    submitted_size = pending.get("submitted_size")
    if submitted_size not in (None, ""):
        _positive_decimal_arg(f"pending_actual_fills[{order_id}].submitted_size", submitted_size)
    return total_qty, total_notional, vwap


def _validate_ledger_core_sections(data: Dict[str, Any]) -> None:
    schema_version = data.get("ledger_schema_version")
    if schema_version != LIVE_TRADE_LEDGER_SCHEMA_VERSION:
        raise SystemExit(
            f"ledger_schema_version must be {LIVE_TRADE_LEDGER_SCHEMA_VERSION}; found {schema_version!r}"
        )
    required_sections = {
        "open": dict,
        "settled": list,
        "seen_auto_redeem_events": list,
        "pending_auto_redeem_events": dict,
        "pending_actual_fills": dict,
        "submitted_order_intents": dict,
    }
    for section, expected_type in required_sections.items():
        if section not in data:
            raise SystemExit(f"ledger missing required section: {section}")
        if not isinstance(data[section], expected_type):
            expected_name = "JSON object" if expected_type is dict else "JSON list"
            raise SystemExit(f"ledger section {section} must be a {expected_name}")
    for order_id, meta in data["open"].items():
        if not isinstance(meta, dict):
            raise SystemExit(f"ledger open[{order_id}] must be a JSON object")
    for index, trade in enumerate(data["settled"]):
        if not isinstance(trade, dict):
            raise SystemExit(f"ledger settled[{index}] must be a JSON object")
    for event_key, payload in data["pending_auto_redeem_events"].items():
        if not isinstance(payload, dict):
            raise SystemExit(f"ledger pending_auto_redeem_events[{event_key}] must be a JSON object")
    for order_id, pending in data["pending_actual_fills"].items():
        if not isinstance(pending, dict):
            raise SystemExit(f"ledger pending_actual_fills[{order_id}] must be a JSON object")
        _validate_pending_actual_fill_aggregate(order_id, pending)
    for order_id, intent in data["submitted_order_intents"].items():
        if not isinstance(intent, dict):
            raise SystemExit(f"ledger submitted_order_intents[{order_id}] must be a JSON object")


def _load_ledger(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"ledger does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ledger is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise SystemExit("ledger root must be a JSON object")
    _validate_ledger_core_sections(data)
    return data


def _save_ledger(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    _fsync_parent_directory(path)


def _acquire_ledger_lock(path: Path):
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    lock_file = lock_path.open("r+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"manual-reconciliation:{os.getpid()}")
        lock_file.flush()
        os.fsync(lock_file.fileno())
    except BlockingIOError as exc:
        lock_file.close()
        raise SystemExit(
            f"ledger lock is held: {lock_path}. Stop the bot before manual reconciliation."
        ) from exc
    return lock_file


def _filled_qty(trade: Dict[str, Any]) -> Decimal:
    raw = trade.get("filled_qty")
    if raw in (None, ""):
        raise SystemExit(
            "filled_qty is required for manual reconciliation; "
            "estimated_tokens is not a verified fill unit count"
        )
    return _decimal_arg("filled_qty", raw)


def _validate_settlement_accounting(trade: Dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    size = _required_positive_decimal_arg("size", trade.get("size"))
    filled_qty = _filled_qty(trade)
    if filled_qty < 0:
        raise SystemExit("filled_qty must be non-negative")
    if filled_qty <= 0:
        raise SystemExit(
            "manual reconciliation requires known positive filled token units; "
            "repair filled_qty from verified exchange records before reconciliation"
        )
    raw_entry_price = trade.get("entry_price")
    if raw_entry_price in (None, ""):
        raise SystemExit(
            "entry_price is required for reconciliation; repair verified fill accounting before resolving"
        )
    entry_price = _positive_decimal_arg("entry_price", raw_entry_price)
    if entry_price > 1:
        raise SystemExit("entry_price must be less than or equal to 1 for binary outcome tokens")
    expected_size = filled_qty * entry_price
    if abs(size - expected_size) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
        raise SystemExit(
            f"size {size} must match entry_price {entry_price} * filled_qty {filled_qty} = "
            f"{expected_size} within {SETTLEMENT_ACCOUNTING_COST_TOLERANCE}"
        )
    raw_filled_notional = trade.get("filled_notional")
    if raw_filled_notional in (None, ""):
        raise SystemExit(
            "filled_notional is required for reconciliation; repair verified fill accounting before resolving"
        )
    filled_notional = _positive_decimal_arg("filled_notional", raw_filled_notional)
    if abs(filled_notional - size) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
        raise SystemExit(
            f"filled_notional {filled_notional} must match size {size} "
            f"within {SETTLEMENT_ACCOUNTING_COST_TOLERANCE}"
        )
    return size, filled_qty, entry_price


def _resolved_settlement_amounts_are_valid(trade: Dict[str, Any]) -> bool:
    try:
        payout = _non_negative_decimal_arg("payout", trade.get("payout"))
        pnl = _decimal_arg("pnl", trade.get("pnl"))
        size, filled_qty, _entry_price = _validate_settlement_accounting(trade)
    except SystemExit:
        return False
    overpayout_allowed = (
        trade.get("manual_reconciliation_allow_overpayout") is True
        or trade.get("allow_overpayout") is True
    )
    if filled_qty > 0 and payout > filled_qty and not overpayout_allowed:
        return False
    return pnl == payout - size


def _mark_repaired_trade_unknown(trade: Dict[str, Any]) -> None:
    trade["settlement_source"] = "SETTLEMENT_UNKNOWN"
    trade["needs_reconciliation"] = True
    trade["payout"] = "UNKNOWN"
    trade["pnl"] = "UNKNOWN"
    trade.pop("exit_price", None)


def _validate_external_fill_accounting(args: argparse.Namespace) -> tuple[Decimal, Decimal, Decimal]:
    size = _positive_decimal_arg("--external-size", args.external_size)
    entry_price = _positive_decimal_arg("--external-entry-price", args.external_entry_price)
    filled_qty = _positive_decimal_arg("--external-filled-qty", args.external_filled_qty)
    if entry_price > 1:
        raise SystemExit("--external-entry-price must be less than or equal to 1 for binary outcome tokens")
    expected_size = entry_price * filled_qty
    if abs(size - expected_size) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
        raise SystemExit(
            f"--external-size {size} must match --external-entry-price {entry_price} "
            f"* --external-filled-qty {filled_qty} = {expected_size} "
            f"within {SETTLEMENT_ACCOUNTING_COST_TOLERANCE}"
        )
    return size, entry_price, filled_qty


def _create_unknown_from_external_order(
    data: Dict[str, Any],
    args: argparse.Namespace,
    order_id: str | None = None,
    allow_existing_submitted_intent: bool = False,
) -> None:
    open_trades = data.get("open")
    if not isinstance(open_trades, dict):
        raise SystemExit("ledger must contain an open trade object")
    order_id = order_id or args.create_unknown_from_external_order
    if order_id in open_trades:
        raise SystemExit(f"order_id={order_id} already exists in open trades")
    existing = [
        trade
        for trade in data["settled"]
        if isinstance(trade, dict) and str(trade.get("order_id") or "") == order_id
    ]
    if existing:
        raise SystemExit(f"order_id={order_id} already exists in settled trades")
    pending_actual_fills = data.get("pending_actual_fills")
    if not isinstance(pending_actual_fills, dict):
        raise SystemExit("pending_actual_fills must be a JSON object")
    external_repair_pending_actual_fill = None
    if order_id in pending_actual_fills:
        pending_payload = pending_actual_fills[order_id]
        if not isinstance(pending_payload, dict):
            raise SystemExit(f"pending_actual_fills[{order_id}] must be a JSON object")
        if pending_payload.get("requires_external_fill_repair") is not True:
            raise SystemExit(f"order_id={order_id} exists in pending_actual_fills; convert that fill first")
        external_repair_pending_actual_fill = copy.deepcopy(pending_payload)
    submitted_intents = data.get("submitted_order_intents")
    if not isinstance(submitted_intents, dict):
        raise SystemExit("submitted_order_intents must be a JSON object")
    if order_id in submitted_intents and not allow_existing_submitted_intent:
        raise SystemExit(
            f"order_id={order_id} exists in submitted_order_intents; use --convert-submitted-intent"
        )

    size, entry_price, filled_qty = _validate_external_fill_accounting(args)

    submitted_at = _datetime_arg("--external-submitted-at", args.external_submitted_at)
    filled_at = _datetime_arg("--external-filled-at", args.external_filled_at)
    market_end_time = _datetime_arg("--external-market-end-time", args.external_market_end_time)
    if submitted_at > filled_at:
        raise SystemExit("--external-submitted-at must be before or equal to --external-filled-at")
    if filled_at > market_end_time:
        raise SystemExit("--external-filled-at must be before or equal to --external-market-end-time")
    reconstructed = {
        "order_id": order_id,
        "entry_price": str(entry_price),
        "size": str(size),
        "direction": args.external_direction,
        "trade_label": args.external_trade_label,
        "filled_qty": str(filled_qty),
        "filled_notional": str(size),
        "instrument_id": args.external_instrument_id,
        "token_id": args.external_token_id,
        "slug": args.external_slug,
        "condition_id": args.external_condition_id,
        "market_end_time": market_end_time.isoformat(),
        "submitted_at": submitted_at.isoformat(),
        "filled_at": filled_at.isoformat(),
        "settled_at": datetime.now(timezone.utc).isoformat(),
        "settlement_source": "SETTLEMENT_UNKNOWN",
        "needs_reconciliation": True,
        "unknown_reason": f"manual external-order reconstruction: {args.reason}",
        "payout": "UNKNOWN",
        "pnl": "UNKNOWN",
    }
    if external_repair_pending_actual_fill is not None:
        reconstructed["external_repair_pending_actual_fill"] = external_repair_pending_actual_fill
        reconstructed["external_repair_reason"] = external_repair_pending_actual_fill.get(
            "external_fill_repair_reason"
        )
        pending_venue_order_id = external_repair_pending_actual_fill.get("venue_order_id")
        if pending_venue_order_id not in (None, ""):
            normalized_pending_venue = str(pending_venue_order_id).lower()
            settled_venue_matches = [
                trade
                for trade in data["settled"]
                if isinstance(trade, dict)
                and str(trade.get("venue_order_id") or "").lower() == normalized_pending_venue
            ]
            if settled_venue_matches:
                raise SystemExit(f"venue_order_id={pending_venue_order_id} already exists in settled trades")
            open_venue_matches = [
                str(open_order_id)
                for open_order_id, open_trade in open_trades.items()
                if isinstance(open_trade, dict)
                and str(open_trade.get("venue_order_id") or "").lower() == normalized_pending_venue
            ]
            if open_venue_matches:
                raise SystemExit(
                    f"venue_order_id={pending_venue_order_id} already exists in open trades: "
                    + ", ".join(open_venue_matches)
                )
            pending_venue_matches = [
                str(pending_order_id)
                for pending_order_id, pending_payload in pending_actual_fills.items()
                if pending_order_id != order_id
                and isinstance(pending_payload, dict)
                and str(pending_payload.get("venue_order_id") or "").lower() == normalized_pending_venue
            ]
            if pending_venue_matches:
                raise SystemExit(
                    f"venue_order_id={pending_venue_order_id} already exists in pending_actual_fills: "
                    + ", ".join(pending_venue_matches)
                )
            reconstructed["venue_order_id"] = pending_venue_order_id
    print("External filled order selected for SETTLEMENT_UNKNOWN reconstruction:")
    print(f"  order_id: {order_id}")
    print(f"  slug: {args.external_slug}")
    print(f"  condition_id: {args.external_condition_id}")
    print(f"  token_id: {args.external_token_id}")
    print(f"  filled_qty: {filled_qty}")
    print(f"  size: {size}")
    if external_repair_pending_actual_fill is not None:
        pending_actual_fills.pop(order_id)
    data["settled"].append(reconstructed)


def _submitted_order_intents(data: Dict[str, Any]) -> Dict[str, Any]:
    if "submitted_order_intents" not in data:
        raise SystemExit("ledger missing submitted_order_intents; provide a current schema v3 ledger")
    intents = data["submitted_order_intents"]
    if not isinstance(intents, dict):
        raise SystemExit("submitted_order_intents must be a JSON object when present")
    return intents


def _list_submitted_order_intents(data: Dict[str, Any]) -> int:
    intents = _submitted_order_intents(data)
    if not intents:
        print("No submitted_order_intents entries.")
        return 0
    print(f"Submitted order intents: {len(intents)}")
    for order_id, payload in sorted(intents.items()):
        if not isinstance(payload, dict):
            print(f"- {order_id}: <invalid non-object payload>")
            continue
        print(f"- {order_id}")
        print(f"  status: {payload.get('status')}")
        print(f"  submitted_at: {payload.get('submitted_at')}")
        print(f"  trade_label: {payload.get('trade_label')}")
        print(f"  order_side: {payload.get('order_side')}")
        print(f"  quote_quantity: {payload.get('quote_quantity')}")
        print(f"  spend_amount: {payload.get('spend_amount')}")
        print(f"  estimated_tokens: {payload.get('estimated_tokens')}")
        print(f"  estimated_price: {payload.get('estimated_price')}")
        print(f"  slug: {payload.get('slug')}")
        print(f"  condition_id: {payload.get('condition_id')}")
        print(f"  token_id: {payload.get('token_id')}")
    return 0


def _resolve_submitted_intent_no_order(data: Dict[str, Any], order_id: str, reason: str) -> None:
    intents = _submitted_order_intents(data)
    if order_id not in intents:
        raise SystemExit(f"submitted order intent not found for order_id={order_id}")
    open_trades = data.get("open")
    if not isinstance(open_trades, dict):
        raise SystemExit("ledger must contain an open trade object")
    if order_id in open_trades:
        raise SystemExit(f"order_id={order_id} exists in open trades; cannot mark submission not seen")
    pending_actual_fills = data.get("pending_actual_fills")
    if not isinstance(pending_actual_fills, dict):
        raise SystemExit("pending_actual_fills must be a JSON object")
    if order_id in pending_actual_fills:
        raise SystemExit(f"order_id={order_id} exists in pending_actual_fills; cannot mark submission not seen")
    if any(isinstance(trade, dict) and str(trade.get("order_id") or "") == order_id for trade in data["settled"]):
        raise SystemExit(f"order_id={order_id} exists in settled trades; cannot mark submission not seen")
    payload = intents[order_id]
    if not isinstance(payload, dict):
        raise SystemExit(f"submitted order intent for order_id={order_id} must be a JSON object")
    resolved = dict(payload)
    resolved.update(
        {
            "status": "SUBMISSION_NOT_SEEN",
            "needs_reconciliation": False,
            "submission_not_seen_at": datetime.now(timezone.utc).isoformat(),
            "submission_not_seen_reason": reason,
        }
    )
    intents[order_id] = resolved
    print("Submitted order intent selected for no-order resolution:")
    print(f"  order_id: {order_id}")
    print(f"  submitted_at: {payload.get('submitted_at')}")
    print(f"  trade_label: {payload.get('trade_label')}")
    print(f"  reason: {reason}")


def _convert_submitted_intent_to_unknown(data: Dict[str, Any], args: argparse.Namespace) -> None:
    intents = _submitted_order_intents(data)
    order_id = args.convert_submitted_intent
    if order_id not in intents:
        raise SystemExit(f"submitted order intent not found for order_id={order_id}")
    intent = intents[order_id]
    if not isinstance(intent, dict):
        raise SystemExit(f"submitted order intent for order_id={order_id} must be a JSON object")
    _create_unknown_from_external_order(data, args, order_id=order_id, allow_existing_submitted_intent=True)
    created = data["settled"][-1]
    created["unknown_reason"] = f"manual submitted-intent conversion after external fill verification: {args.reason}"
    created["submitted_order_intent"] = intent
    intents.pop(order_id)


def _list_pending_actual_fills(data: Dict[str, Any]) -> int:
    if "pending_actual_fills" not in data:
        raise SystemExit("ledger missing pending_actual_fills; provide a current schema v3 ledger")
    pending = data["pending_actual_fills"]
    if not isinstance(pending, dict):
        raise SystemExit("pending_actual_fills must be a JSON object when present")
    if not pending:
        print("No pending_actual_fills entries.")
        return 0
    print(f"Pending actual fills: {len(pending)}")
    for order_id, payload in sorted(pending.items()):
        if not isinstance(payload, dict):
            print(f"- {order_id}: <invalid non-object payload>")
            continue
        print(f"- {order_id}")
        print(f"  received_at: {payload.get('received_at')}")
        print(f"  venue_order_id: {payload.get('venue_order_id')}")
        print(f"  condition_id: {payload.get('condition_id')}")
        print(f"  token_id: {payload.get('token_id')}")
        print(f"  total_filled_qty: {payload.get('total_filled_qty')}")
        print(f"  total_filled_notional: {payload.get('total_filled_notional')}")
        print(f"  vwap: {payload.get('vwap')}")
        print(f"  fills: {len(payload.get('fills') or [])}")
    return 0


def _convert_pending_actual_fill_to_unknown(data: Dict[str, Any], order_id: str, reason: str) -> None:
    if "pending_actual_fills" not in data:
        raise SystemExit("ledger missing pending_actual_fills; provide a current schema v3 ledger")
    open_trades = data.get("open")
    if not isinstance(open_trades, dict):
        raise SystemExit("ledger must contain an open trade object")
    pending = data["pending_actual_fills"]
    if not isinstance(pending, dict):
        raise SystemExit("pending_actual_fills must be a JSON object")
    if order_id not in pending:
        raise SystemExit(f"pending actual fill not found for order_id={order_id}")
    payload = pending[order_id]
    if not isinstance(payload, dict):
        raise SystemExit(f"pending actual fill for order_id={order_id} must be a JSON object")
    if payload.get("requires_external_fill_repair") is True:
        raise SystemExit(
            f"pending actual fill order_id={order_id} requires external fill repair; "
            "use --create-unknown-from-external-order or repair an existing SETTLEMENT_UNKNOWN with verified values"
        )
    if any(isinstance(trade, dict) and str(trade.get("order_id") or "") == order_id for trade in data["settled"]):
        raise SystemExit(f"order_id={order_id} already exists in settled trades")
    if order_id in open_trades and not isinstance(open_trades.get(order_id), dict):
        raise SystemExit(f"open trade for order_id={order_id} must be a JSON object")
    source_open_trade = open_trades.get(order_id) or {}
    payload_venue_order_id = payload.get("venue_order_id")
    source_venue_order_id = source_open_trade.get("venue_order_id")
    if (
        payload_venue_order_id not in (None, "")
        and source_venue_order_id not in (None, "")
        and str(payload_venue_order_id).lower() != str(source_venue_order_id).lower()
    ):
        raise SystemExit(
            f"venue_order_id mismatch for order_id={order_id}: "
            f"pending={payload_venue_order_id!r} open={source_venue_order_id!r}"
        )
    venue_order_id = payload_venue_order_id or source_venue_order_id
    if venue_order_id not in (None, ""):
        venue_matches = [
            trade
            for trade in data["settled"]
            if isinstance(trade, dict)
            and str(trade.get("venue_order_id") or "").lower() == str(venue_order_id).lower()
        ]
        if venue_matches:
            raise SystemExit(f"venue_order_id={venue_order_id} already exists in settled trades")
        open_venue_matches = [
            str(open_order_id)
            for open_order_id, open_trade in open_trades.items()
            if isinstance(open_trade, dict)
            and str(open_trade.get("venue_order_id") or "").lower() == str(venue_order_id).lower()
        ]
        conflicting_open = [open_order_id for open_order_id in open_venue_matches if open_order_id != order_id]
        if conflicting_open:
            raise SystemExit(
                f"venue_order_id={venue_order_id} already exists in open trades: "
                + ", ".join(conflicting_open)
            )
        pending_venue_matches = [
            str(pending_order_id)
            for pending_order_id, pending_payload in pending.items()
            if pending_order_id != order_id
            and isinstance(pending_payload, dict)
            and str(pending_payload.get("venue_order_id") or "").lower() == str(venue_order_id).lower()
        ]
        if pending_venue_matches:
            raise SystemExit(
                f"venue_order_id={venue_order_id} already exists in pending_actual_fills: "
                + ", ".join(pending_venue_matches)
            )

    filled_qty, filled_notional, vwap = _validate_pending_actual_fill_aggregate(order_id, payload)

    record = {
        "settlement_source": "SETTLEMENT_UNKNOWN",
        "needs_reconciliation": True,
        "payout": "UNKNOWN",
        "pnl": "UNKNOWN",
        "order_id": order_id,
        "client_order_id": order_id,
        "venue_order_id": venue_order_id,
        "condition_id": payload.get("condition_id"),
        "token_id": payload.get("token_id"),
        "slug": payload.get("slug"),
        "direction": payload.get("direction"),
        "trade_label": payload.get("trade_label"),
        "submitted_at": payload.get("submitted_at"),
        "size": str(filled_notional),
        "filled_qty": str(filled_qty),
        "entry_price": str(vwap),
        "filled_notional": str(filled_notional),
        "unknown_reason": f"manual pending-actual-fill conversion: {reason}",
        "raw_callback_payload": payload["raw_callback_payload"] if "raw_callback_payload" in payload else payload,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    submitted_size = payload.get("submitted_size")
    if submitted_size not in (None, ""):
        record["submitted_size"] = str(_positive_decimal_arg("pending_actual_fills.submitted_size", submitted_size))
    submitted_intents = data.get("submitted_order_intents")
    if not isinstance(submitted_intents, dict):
        raise SystemExit("submitted_order_intents must be a JSON object")
    if order_id in submitted_intents:
        intent = submitted_intents[order_id]
        if not isinstance(intent, dict):
            raise SystemExit(f"submitted_order_intents[{order_id}] must be a JSON object")
        record["submitted_order_intent"] = copy.deepcopy(intent)
    print("Pending actual fill selected for SETTLEMENT_UNKNOWN conversion:")
    print(f"  order_id: {order_id}")
    print(f"  venue_order_id: {record.get('venue_order_id')}")
    print(f"  condition_id: {record.get('condition_id')}")
    print(f"  token_id: {record.get('token_id')}")
    pending.pop(order_id)
    open_trades.pop(order_id, None)
    submitted_intents.pop(order_id, None)
    data["settled"].append(record)


def _select_unresolved_unknown(data: Dict[str, Any], args: argparse.Namespace) -> tuple[Dict[str, Any], str]:
    trade, selector_label = _select_single_settled_trade(data, args)
    if (
        trade.get("needs_reconciliation") is not True
        or trade.get("settlement_source") != "SETTLEMENT_UNKNOWN"
    ):
        raise SystemExit(f"{selector_label} is not an unresolved SETTLEMENT_UNKNOWN record")
    return trade, selector_label


def _select_single_settled_trade(data: Dict[str, Any], args: argparse.Namespace) -> tuple[Dict[str, Any], str]:
    if args.order_id:
        matches = [
            trade
            for trade in data["settled"]
            if isinstance(trade, dict) and str(trade.get("order_id") or "") == args.order_id
        ]
        selector_label = f"order_id={args.order_id}"
    else:
        matches = [
            trade
            for trade in data["settled"]
            if isinstance(trade, dict) and str(trade.get("venue_order_id") or "") == args.venue_order_id
        ]
        selector_label = f"venue_order_id={args.venue_order_id}"
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one settled trade for {selector_label}, found {len(matches)}")
    return matches[0], selector_label


def _repair_inconsistent_settlement_flags(data: Dict[str, Any], args: argparse.Namespace) -> tuple[str, str]:
    trade, selector_label = _select_single_settled_trade(data, args)
    needs_reconciliation = trade.get("needs_reconciliation") is True
    is_unknown = trade.get("settlement_source") == "SETTLEMENT_UNKNOWN"
    if needs_reconciliation == is_unknown:
        raise SystemExit(f"{selector_label} does not have inconsistent settlement flags")

    previous_state = {
        "settlement_source": trade.get("settlement_source"),
        "needs_reconciliation": trade.get("needs_reconciliation"),
        "payout": trade.get("payout"),
        "pnl": trade.get("pnl"),
        "exit_price": trade.get("exit_price"),
        "entry_price": trade.get("entry_price"),
        "size": trade.get("size"),
        "filled_qty": trade.get("filled_qty"),
    }
    if is_unknown and not needs_reconciliation:
        _mark_repaired_trade_unknown(trade)
        action = "marked SETTLEMENT_UNKNOWN as needing reconciliation"
    else:
        source = trade.get("settlement_source")
        if (
            source in RESOLVED_SETTLEMENT_SOURCES
            and _resolved_settlement_amounts_are_valid(trade)
        ):
            trade["needs_reconciliation"] = False
            action = "cleared stale needs_reconciliation on resolved settlement"
        else:
            _mark_repaired_trade_unknown(trade)
            action = "marked incomplete inconsistent settlement as SETTLEMENT_UNKNOWN"

    trade["settlement_flag_repair_at"] = datetime.now(timezone.utc).isoformat()
    trade["settlement_flag_repair_reason"] = args.reason
    trade["settlement_flag_repair_previous_state"] = previous_state
    return selector_label, action


def _repair_unknown_fill_accounting_from_external(trade: Dict[str, Any], args: argparse.Namespace) -> None:
    size, entry_price, filled_qty = _validate_external_fill_accounting(args)
    previous_state = {
        "size": trade.get("size"),
        "entry_price": trade.get("entry_price"),
        "filled_qty": trade.get("filled_qty"),
        "filled_notional": trade.get("filled_notional"),
    }
    trade.update(
        {
            "size": str(size),
            "entry_price": str(entry_price),
            "filled_qty": str(filled_qty),
            "filled_notional": str(size),
            "external_fill_repair_at": datetime.now(timezone.utc).isoformat(),
            "external_fill_repair_reason": args.reason,
            "external_fill_repair_previous_state": previous_state,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mark one unresolved SETTLEMENT_UNKNOWN live trade as manually "
            "reconciled after operator verification. The bot must be stopped."
        )
    )
    parser.add_argument(
        "--ledger",
        required=True,
        help="Explicit path to live_trades.json. Required; no default ledger path is used.",
    )
    parser.add_argument("--order-id", help="Exact settled order_id to resolve")
    parser.add_argument("--venue-order-id", help="Exact settled venue_order_id to resolve")
    parser.add_argument(
        "--convert-pending-actual-fill",
        metavar="ORDER_ID",
        help="Move one pending_actual_fills entry into settled SETTLEMENT_UNKNOWN for manual reconciliation",
    )
    parser.add_argument(
        "--create-unknown-from-external-order",
        metavar="ORDER_ID",
        help="Create one SETTLEMENT_UNKNOWN record from externally verified filled-order details",
    )
    parser.add_argument(
        "--list-pending-actual-fills",
        action="store_true",
        help="List pending actual-fill records that have not reached the live fill recorder yet",
    )
    parser.add_argument(
        "--list-submitted-order-intents",
        action="store_true",
        help="List submitted order intents that were persisted before exchange submission",
    )
    parser.add_argument(
        "--convert-submitted-intent",
        metavar="ORDER_ID",
        help="Convert one submitted_order_intents entry into SETTLEMENT_UNKNOWN using verified fill details",
    )
    parser.add_argument(
        "--resolve-submitted-intent-no-order",
        metavar="ORDER_ID",
        help="Mark one submitted_order_intents entry SUBMISSION_NOT_SEEN after verifying no exchange order exists",
    )
    parser.add_argument(
        "--confirm-external-order",
        action="store_true",
        help=(
            "Required with --create-unknown-from-external-order, --convert-submitted-intent, "
            "or --repair-unknown-fill-accounting after verifying exchange/order records"
        ),
    )
    parser.add_argument(
        "--confirm-no-exchange-order",
        action="store_true",
        help="Required with --resolve-submitted-intent-no-order after verifying no exchange order exists",
    )
    parser.add_argument("--external-size", help="Verified filled notional/cost for external-order reconstruction")
    parser.add_argument("--external-entry-price", help="Verified average fill price for external-order reconstruction")
    parser.add_argument("--external-filled-qty", help="Verified filled token units for external-order reconstruction")
    parser.add_argument("--external-direction", choices=("long", "short"), help="Bot direction for the filled order")
    parser.add_argument("--external-trade-label", help="Human-readable trade side label from the bot/order record")
    parser.add_argument("--external-instrument-id", help="Nautilus/Polymarket instrument id for the filled order")
    parser.add_argument("--external-token-id", help="CLOB token id for the filled order")
    parser.add_argument("--external-slug", help="Polymarket market slug for the filled order")
    parser.add_argument("--external-condition-id", help="Polymarket condition id for the filled order")
    parser.add_argument("--external-submitted-at", help="ISO-8601 submitted timestamp with timezone")
    parser.add_argument("--external-filled-at", help="ISO-8601 fill timestamp with timezone")
    parser.add_argument("--external-market-end-time", help="ISO-8601 market end timestamp with timezone")
    parser.add_argument(
        "--repair-unknown-fill-accounting",
        action="store_true",
        help=(
            "With --order-id or --venue-order-id, repair an existing SETTLEMENT_UNKNOWN "
            "using verified --external-size/--external-entry-price/--external-filled-qty before payout resolution"
        ),
    )
    parser.add_argument(
        "--repair-inconsistent-settlement-flags",
        action="store_true",
        help=(
            "With --order-id or --venue-order-id, repair one record where only one of "
            "needs_reconciliation or settlement_source=SETTLEMENT_UNKNOWN is set"
        ),
    )
    parser.add_argument(
        "--confirm-inconsistent-settlement-flags",
        action="store_true",
        help="Required with --repair-inconsistent-settlement-flags after verifying the ledger record state",
    )
    parser.add_argument("--payout", help="Verified payout in USD")
    parser.add_argument(
        "--allow-overpayout",
        action="store_true",
        help="Allow payout above filled token units after explicit operator verification",
    )
    parser.add_argument("--reason", help="Manual reconciliation note")
    args = parser.parse_args()
    selected_modes = [
        bool(args.order_id),
        bool(args.venue_order_id),
        bool(args.convert_pending_actual_fill),
        bool(args.create_unknown_from_external_order),
        bool(args.list_pending_actual_fills),
        bool(args.list_submitted_order_intents),
        bool(args.convert_submitted_intent),
        bool(args.resolve_submitted_intent_no_order),
    ]
    if sum(selected_modes) != 1:
        raise SystemExit(
            "provide exactly one of --order-id, --venue-order-id, "
            "--convert-pending-actual-fill, --create-unknown-from-external-order, "
            "--list-pending-actual-fills, --list-submitted-order-intents, "
            "--convert-submitted-intent, or --resolve-submitted-intent-no-order"
        )
    if not (args.list_pending_actual_fills or args.list_submitted_order_intents) and not args.reason:
        raise SystemExit("--reason is required for reconciliation mutations")
    if (
        (args.order_id or args.venue_order_id)
        and args.payout is None
        and not args.repair_inconsistent_settlement_flags
    ):
        raise SystemExit("--payout is required with --order-id or --venue-order-id")
    if args.repair_inconsistent_settlement_flags and args.payout is not None:
        raise SystemExit("--payout is not valid with --repair-inconsistent-settlement-flags")
    if (
        args.convert_pending_actual_fill
        or args.create_unknown_from_external_order
        or args.list_pending_actual_fills
        or args.list_submitted_order_intents
        or args.convert_submitted_intent
        or args.resolve_submitted_intent_no_order
    ) and args.payout is not None:
        raise SystemExit("--payout is only used with --order-id or --venue-order-id")
    if args.repair_unknown_fill_accounting and not (args.order_id or args.venue_order_id):
        raise SystemExit("--repair-unknown-fill-accounting requires --order-id or --venue-order-id")
    if args.repair_unknown_fill_accounting and not args.confirm_external_order:
        raise SystemExit("--repair-unknown-fill-accounting requires --confirm-external-order")
    if args.repair_inconsistent_settlement_flags and not (args.order_id or args.venue_order_id):
        raise SystemExit("--repair-inconsistent-settlement-flags requires --order-id or --venue-order-id")
    if args.repair_inconsistent_settlement_flags and not args.confirm_inconsistent_settlement_flags:
        raise SystemExit("--repair-inconsistent-settlement-flags requires --confirm-inconsistent-settlement-flags")
    if args.repair_inconsistent_settlement_flags and args.repair_unknown_fill_accounting:
        raise SystemExit("--repair-inconsistent-settlement-flags cannot be combined with --repair-unknown-fill-accounting")
    if args.confirm_inconsistent_settlement_flags and not args.repair_inconsistent_settlement_flags:
        raise SystemExit(
            "--confirm-inconsistent-settlement-flags is only valid with --repair-inconsistent-settlement-flags"
        )
    if args.confirm_external_order and not (
        args.create_unknown_from_external_order
        or args.convert_submitted_intent
        or args.repair_unknown_fill_accounting
    ):
        raise SystemExit(
            "--confirm-external-order is only valid with --create-unknown-from-external-order, "
            "--convert-submitted-intent, or --repair-unknown-fill-accounting"
        )
    if args.create_unknown_from_external_order and not args.confirm_external_order:
        raise SystemExit("--create-unknown-from-external-order requires --confirm-external-order")
    if args.convert_submitted_intent and not args.confirm_external_order:
        raise SystemExit("--convert-submitted-intent requires --confirm-external-order")
    if args.confirm_no_exchange_order and not args.resolve_submitted_intent_no_order:
        raise SystemExit("--confirm-no-exchange-order is only valid with --resolve-submitted-intent-no-order")
    if args.resolve_submitted_intent_no_order and not args.confirm_no_exchange_order:
        raise SystemExit("--resolve-submitted-intent-no-order requires --confirm-no-exchange-order")
    external_accounting_fields = [
        "external_size",
        "external_entry_price",
        "external_filled_qty",
    ]
    external_metadata_fields = [
        "external_direction",
        "external_trade_label",
        "external_instrument_id",
        "external_token_id",
        "external_slug",
        "external_condition_id",
        "external_submitted_at",
        "external_filled_at",
        "external_market_end_time",
    ]
    external_fields = external_accounting_fields + external_metadata_fields
    missing_external = [field for field in external_fields if getattr(args, field) in (None, "")]
    if (args.create_unknown_from_external_order or args.convert_submitted_intent) and missing_external:
        formatted = ", ".join("--" + field.replace("_", "-") for field in missing_external)
        if args.create_unknown_from_external_order:
            raise SystemExit(f"--create-unknown-from-external-order requires: {formatted}")
        raise SystemExit(f"--convert-submitted-intent requires: {formatted}")
    if args.repair_unknown_fill_accounting:
        missing_accounting = [
            field for field in external_accounting_fields
            if getattr(args, field) in (None, "")
        ]
        if missing_accounting:
            formatted = ", ".join("--" + field.replace("_", "-") for field in missing_accounting)
            raise SystemExit(f"--repair-unknown-fill-accounting requires: {formatted}")
        provided_metadata = [
            field for field in external_metadata_fields
            if getattr(args, field) not in (None, "")
        ]
        if provided_metadata:
            formatted = ", ".join("--" + field.replace("_", "-") for field in provided_metadata)
            raise SystemExit(
                "external order metadata fields are only valid with "
                f"--create-unknown-from-external-order or --convert-submitted-intent: {formatted}"
            )
    if not (
        args.create_unknown_from_external_order
        or args.convert_submitted_intent
        or args.repair_unknown_fill_accounting
    ):
        provided_external = [field for field in external_fields if getattr(args, field) not in (None, "")]
        if provided_external:
            formatted = ", ".join("--" + field.replace("_", "-") for field in provided_external)
            raise SystemExit(
                "external-order fields are only valid with --create-unknown-from-external-order, "
                f"--convert-submitted-intent, or --repair-unknown-fill-accounting: {formatted}"
            )

    ledger_path = _resolve_ledger_path(args.ledger)

    lock_file = _acquire_ledger_lock(ledger_path)
    try:
        print(f"Using ledger: {ledger_path}")
        print(f"Using lock: {ledger_path.with_name(ledger_path.name + '.lock')}")
        data = _load_ledger(ledger_path)
        if args.list_pending_actual_fills:
            return _list_pending_actual_fills(data)
        if args.list_submitted_order_intents:
            return _list_submitted_order_intents(data)
        if args.convert_pending_actual_fill:
            _convert_pending_actual_fill_to_unknown(data, args.convert_pending_actual_fill, args.reason)
            _save_ledger(ledger_path, data)
            print(
                f"Converted pending actual fill {args.convert_pending_actual_fill} "
                f"to SETTLEMENT_UNKNOWN ledger={ledger_path}"
            )
            return 0
        if args.create_unknown_from_external_order:
            _create_unknown_from_external_order(data, args)
            _save_ledger(ledger_path, data)
            print(
                f"Created SETTLEMENT_UNKNOWN record from external order "
                f"{args.create_unknown_from_external_order} ledger={ledger_path}"
            )
            return 0
        if args.convert_submitted_intent:
            _convert_submitted_intent_to_unknown(data, args)
            _save_ledger(ledger_path, data)
            print(
                f"Converted submitted order intent {args.convert_submitted_intent} "
                f"to SETTLEMENT_UNKNOWN ledger={ledger_path}"
            )
            return 0
        if args.resolve_submitted_intent_no_order:
            _resolve_submitted_intent_no_order(data, args.resolve_submitted_intent_no_order, args.reason)
            _save_ledger(ledger_path, data)
            print(
                f"Resolved submitted order intent {args.resolve_submitted_intent_no_order} "
                f"as no exchange order ledger={ledger_path}"
            )
            return 0
        if args.repair_inconsistent_settlement_flags:
            selector_label, action = _repair_inconsistent_settlement_flags(data, args)
            _save_ledger(ledger_path, data)
            print(
                f"Repaired inconsistent settlement flags for {selector_label}: "
                f"{action} ledger={ledger_path}"
            )
            return 0

        payout = _non_negative_decimal_arg("payout", args.payout)
        trade, selector_label = _select_unresolved_unknown(data, args)
        if args.repair_unknown_fill_accounting:
            _repair_unknown_fill_accounting_from_external(trade, args)

        size, filled_qty, _entry_price = _validate_settlement_accounting(trade)
        if filled_qty > 0 and payout > filled_qty and not args.allow_overpayout:
            raise SystemExit(
                f"payout {payout} exceeds filled token units {filled_qty}; "
                "check the value or rerun with --allow-overpayout after explicit verification"
            )
        pnl = payout - size
        exit_price = payout / filled_qty
        previous_state = {
            "settlement_source": trade.get("settlement_source"),
            "needs_reconciliation": trade.get("needs_reconciliation"),
            "payout": trade.get("payout"),
            "pnl": trade.get("pnl"),
            "exit_price": trade.get("exit_price"),
        }

        trade.update(
            {
                "settlement_source": "manual_reconciliation",
                "needs_reconciliation": False,
                "payout": str(payout),
                "pnl": str(pnl),
                "exit_price": str(exit_price),
                "manual_reconciled_at": datetime.now(timezone.utc).isoformat(),
                "manual_reconciliation_reason": args.reason,
                "manual_reconciliation_previous_state": previous_state,
            }
        )
        if args.allow_overpayout and filled_qty > 0 and payout > filled_qty:
            trade["manual_reconciliation_allow_overpayout"] = True

        _save_ledger(ledger_path, data)
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    print(
        f"Resolved {selector_label}: payout={payout} pnl={pnl} "
        f"ledger={ledger_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
