#!/usr/bin/env python3
"""Explicit admin tool for resolving one SETTLEMENT_UNKNOWN ledger record."""

import argparse
import fcntl
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parent
EXTERNAL_NOTIONAL_TOLERANCE = Decimal("0.01")
EXTERNAL_NOTIONAL_RELATIVE_TOLERANCE = Decimal("0.005")


def _resolve_ledger_path(raw_path: str | None) -> Path:
    configured = raw_path or os.getenv("LIVE_TRADE_LEDGER_PATH") or "live_trades.json"
    path = Path(configured).expanduser()
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


def _datetime_arg(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"{name} must be an ISO-8601 datetime with timezone") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _load_ledger(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"ledger does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ledger is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise SystemExit("ledger root must be a JSON object")
    if not isinstance(data.get("settled"), list):
        raise SystemExit("ledger must contain a settled trade list")
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
    raw = trade.get("filled_qty") or trade.get("estimated_tokens") or "0"
    return _decimal_arg("filled_qty/estimated_tokens", raw)


def _migrate_open_to_unknown(data: Dict[str, Any], order_id: str, reason: str) -> None:
    open_trades = data.get("open")
    if not isinstance(open_trades, dict):
        raise SystemExit("ledger must contain an open trade object")
    if order_id not in open_trades:
        raise SystemExit(f"open trade not found for order_id={order_id}")
    meta = open_trades.pop(order_id)
    if not isinstance(meta, dict):
        raise SystemExit(f"open trade for order_id={order_id} must be a JSON object")
    print("Open trade selected for SETTLEMENT_UNKNOWN migration:")
    print(f"  order_id: {order_id}")
    print(f"  filled_at: {meta.get('filled_at')}")
    print(f"  submitted_at: {meta.get('submitted_at')}")
    print(f"  market_end_time: {meta.get('market_end_time')}")
    print(f"  slug: {meta.get('slug')}")
    settled = dict(meta)
    settled.update(
        {
            "order_id": order_id,
            "settled_at": datetime.now(timezone.utc).isoformat(),
            "settlement_source": "SETTLEMENT_UNKNOWN",
            "needs_reconciliation": True,
            "unknown_reason": f"manual open-trade migration: {reason}",
            "payout": "UNKNOWN",
            "pnl": "UNKNOWN",
        }
    )
    data["settled"].append(settled)


def _create_unknown_from_external_order(
    data: Dict[str, Any],
    args: argparse.Namespace,
) -> None:
    open_trades = data.get("open")
    if not isinstance(open_trades, dict):
        raise SystemExit("ledger must contain an open trade object")
    order_id = args.create_unknown_from_external_order
    if order_id in open_trades:
        raise SystemExit(f"order_id={order_id} already exists in open trades; use --migrate-open-to-unknown")
    existing = [
        trade
        for trade in data["settled"]
        if isinstance(trade, dict) and str(trade.get("order_id") or "") == order_id
    ]
    if existing:
        raise SystemExit(f"order_id={order_id} already exists in settled trades")

    size = _decimal_arg("--external-size", args.external_size)
    entry_price = _decimal_arg("--external-entry-price", args.external_entry_price)
    filled_qty = _decimal_arg("--external-filled-qty", args.external_filled_qty)
    if size <= 0:
        raise SystemExit("--external-size must be greater than 0")
    if entry_price <= 0:
        raise SystemExit("--external-entry-price must be greater than 0")
    if entry_price > 1:
        raise SystemExit("--external-entry-price must be less than or equal to 1 for binary outcome tokens")
    if filled_qty <= 0:
        raise SystemExit("--external-filled-qty must be greater than 0")
    expected_size = entry_price * filled_qty
    notional_tolerance = min(
        EXTERNAL_NOTIONAL_TOLERANCE,
        expected_size * EXTERNAL_NOTIONAL_RELATIVE_TOLERANCE,
    )
    if abs(size - expected_size) > notional_tolerance:
        raise SystemExit(
            f"--external-size {size} must match --external-entry-price {entry_price} "
            f"* --external-filled-qty {filled_qty} = {expected_size} "
            f"within {notional_tolerance}"
        )

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
    print("External filled order selected for SETTLEMENT_UNKNOWN reconstruction:")
    print(f"  order_id: {order_id}")
    print(f"  slug: {args.external_slug}")
    print(f"  condition_id: {args.external_condition_id}")
    print(f"  token_id: {args.external_token_id}")
    print(f"  filled_qty: {filled_qty}")
    print(f"  size: {size}")
    data["settled"].append(reconstructed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mark one unresolved SETTLEMENT_UNKNOWN live trade as manually "
            "reconciled after operator verification. The bot must be stopped."
        )
    )
    parser.add_argument(
        "--ledger",
        help="Path to live_trades.json. Defaults to LIVE_TRADE_LEDGER_PATH, then ./live_trades.json.",
    )
    parser.add_argument("--order-id", help="Exact settled order_id to resolve")
    parser.add_argument(
        "--migrate-open-to-unknown",
        metavar="ORDER_ID",
        help="Move one stuck open trade into settled SETTLEMENT_UNKNOWN for manual reconciliation",
    )
    parser.add_argument(
        "--create-unknown-from-external-order",
        metavar="ORDER_ID",
        help="Create one SETTLEMENT_UNKNOWN record from externally verified filled-order details",
    )
    parser.add_argument(
        "--confirm-open-migration",
        action="store_true",
        help="Required with --migrate-open-to-unknown after operator verifies the open trade is stuck",
    )
    parser.add_argument(
        "--confirm-external-order",
        action="store_true",
        help="Required with --create-unknown-from-external-order after verifying exchange/order records",
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
    parser.add_argument("--payout", help="Verified payout in USD")
    parser.add_argument(
        "--allow-overpayout",
        action="store_true",
        help="Allow payout above filled token units after explicit operator verification",
    )
    parser.add_argument("--reason", required=True, help="Manual reconciliation note")
    args = parser.parse_args()
    selected_modes = [
        bool(args.order_id),
        bool(args.migrate_open_to_unknown),
        bool(args.create_unknown_from_external_order),
    ]
    if sum(selected_modes) != 1:
        raise SystemExit("provide exactly one of --order-id, --migrate-open-to-unknown, or --create-unknown-from-external-order")
    if args.order_id and args.payout is None:
        raise SystemExit("--payout is required with --order-id")
    if (args.migrate_open_to_unknown or args.create_unknown_from_external_order) and args.payout is not None:
        raise SystemExit("--payout is only used with --order-id")
    if args.confirm_open_migration and not args.migrate_open_to_unknown:
        raise SystemExit("--confirm-open-migration is only valid with --migrate-open-to-unknown")
    if args.migrate_open_to_unknown and not args.confirm_open_migration:
        raise SystemExit("--migrate-open-to-unknown requires --confirm-open-migration")
    if args.confirm_external_order and not args.create_unknown_from_external_order:
        raise SystemExit("--confirm-external-order is only valid with --create-unknown-from-external-order")
    if args.create_unknown_from_external_order and not args.confirm_external_order:
        raise SystemExit("--create-unknown-from-external-order requires --confirm-external-order")
    external_fields = [
        "external_size",
        "external_entry_price",
        "external_filled_qty",
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
    missing_external = [field for field in external_fields if getattr(args, field) in (None, "")]
    if args.create_unknown_from_external_order and missing_external:
        formatted = ", ".join("--" + field.replace("_", "-") for field in missing_external)
        raise SystemExit(f"--create-unknown-from-external-order requires: {formatted}")
    if not args.create_unknown_from_external_order:
        provided_external = [field for field in external_fields if getattr(args, field) not in (None, "")]
        if provided_external:
            formatted = ", ".join("--" + field.replace("_", "-") for field in provided_external)
            raise SystemExit(f"external-order fields are only valid with --create-unknown-from-external-order: {formatted}")

    ledger_path = _resolve_ledger_path(args.ledger)

    lock_file = _acquire_ledger_lock(ledger_path)
    try:
        print(f"Using ledger: {ledger_path}")
        print(f"Using lock: {ledger_path.with_name(ledger_path.name + '.lock')}")
        data = _load_ledger(ledger_path)
        if args.migrate_open_to_unknown:
            _migrate_open_to_unknown(data, args.migrate_open_to_unknown, args.reason)
            _save_ledger(ledger_path, data)
            print(
                f"Migrated open trade {args.migrate_open_to_unknown} to SETTLEMENT_UNKNOWN "
                f"ledger={ledger_path}"
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

        payout = _decimal_arg("payout", args.payout)
        if payout < 0:
            raise SystemExit("payout must be non-negative")
        matches = [
            trade
            for trade in data["settled"]
            if isinstance(trade, dict) and str(trade.get("order_id") or "") == args.order_id
        ]
        if len(matches) != 1:
            raise SystemExit(f"expected exactly one settled trade for order_id={args.order_id}, found {len(matches)}")

        trade = matches[0]
        if (
            trade.get("needs_reconciliation") is not True
            and trade.get("settlement_source") != "SETTLEMENT_UNKNOWN"
        ):
            raise SystemExit(f"order_id={args.order_id} is not an unresolved SETTLEMENT_UNKNOWN record")

        size = _decimal_arg("size", trade.get("size", "0"))
        filled_qty = _filled_qty(trade)
        if payout > 0 and filled_qty <= 0 and not args.allow_overpayout:
            raise SystemExit(
                "positive payout requires known positive filled token units; "
                "repair filled_qty/estimated_tokens or rerun with --allow-overpayout after explicit verification"
            )
        if filled_qty > 0 and payout > filled_qty and not args.allow_overpayout:
            raise SystemExit(
                f"payout {payout} exceeds filled token units {filled_qty}; "
                "check the value or rerun with --allow-overpayout after explicit verification"
            )
        pnl = payout - size
        exit_price = payout / filled_qty if filled_qty > 0 else Decimal("0")
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

        _save_ledger(ledger_path, data)
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    print(
        f"Resolved {args.order_id}: payout={payout} pnl={pnl} "
        f"ledger={ledger_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
