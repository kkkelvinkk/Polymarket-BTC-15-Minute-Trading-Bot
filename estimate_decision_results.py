"""Estimate win/loss from decision-observation records.

This reads `decisions.jsonl`, joins decided trades to Polymarket Gamma markets
by slug, and estimates binary-share P&L from the logged decision-side fill
size/cost estimate.

This is not live-equivalent P&L. It ignores order submission failures, partial
fill drift after the decision snapshot, fees, settlement timing, and ledger
repair paths.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from analysis.gamma_resolution import (
    GAMMA_MARKETS_URL,
    WINNING_PRICE,
    fetch_market_by_slug,
    load_decision_records as _load_decisions,
    parse_finite_decimal as _decimal,
    winning_side as _winning_side,
)

__all__ = [
    "GAMMA_MARKETS_URL",
    "WINNING_PRICE",
    "main",
    "run",
]


def _decided_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decided = []
    for record in records:
        if record["rejected_at_gate"] is not None:
            continue
        direction = record["decided_direction"]
        if direction not in ("long", "short"):
            raise ValueError(f"unexpected decided_direction={direction!r}")
        if record["slug"] in (None, ""):
            raise ValueError("decided record is missing slug")
        if record["executable_entry"] in (None, ""):
            raise ValueError(f"decided record for {record['slug']} is missing executable_entry")
        if record["estimated_tokens_filled"] in (None, ""):
            raise ValueError(
                f"decided record for {record['slug']} is missing estimated_tokens_filled"
            )
        if record["estimated_actual_cost"] in (None, ""):
            raise ValueError(
                f"decided record for {record['slug']} is missing estimated_actual_cost"
            )
        decided.append(record)
    return decided


def _fetch_market_by_slug(client: httpx.Client, slug: str) -> dict[str, Any]:
    """Estimate caller uses the ``accept-unclosed-as-pending`` policy.

    ``closed_only=False`` requires an exact slug match and the caller treats
    ``winning_side()==None`` (i.e., the market is still open) as a pending
    record, not a missing one. The shared helper's ``@overload``-narrowed
    return type guarantees a non-``None`` dict on this branch.
    """
    return fetch_market_by_slug(client, slug, closed_only=False)


def _estimate_record(record: dict[str, Any], winner: str, stake_usd: Decimal) -> dict[str, Any]:
    entry = _decimal(record["executable_entry"], "executable_entry")
    if entry <= 0 or entry > 1:
        raise ValueError(f"executable_entry must be in (0, 1], got {entry}")
    tokens = _decimal(record["estimated_tokens_filled"], "estimated_tokens_filled")
    actual_cost = _decimal(record["estimated_actual_cost"], "estimated_actual_cost")
    if tokens <= 0:
        raise ValueError(f"estimated_tokens_filled must be positive, got {tokens}")
    if actual_cost <= 0:
        raise ValueError(f"estimated_actual_cost must be positive, got {actual_cost}")
    if actual_cost > stake_usd:
        raise ValueError(
            f"estimated_actual_cost={actual_cost} exceeds supplied stake_usd={stake_usd}"
        )
    won = record["decided_direction"] == winner
    payout = tokens if won else Decimal("0")
    pnl = payout - actual_cost
    return {
        "slug": record["slug"],
        "ts": record["ts"],
        "direction": record["decided_direction"],
        "winner": winner,
        "entry": entry,
        "stake_usd": stake_usd,
        "tokens": tokens,
        "actual_cost": actual_cost,
        "payout": payout,
        "pnl": pnl,
        "won": won,
    }


def _money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'))}"


def run(decisions_path: Path, stake_usd: Decimal) -> int:
    if not stake_usd.is_finite() or stake_usd <= 0:
        raise ValueError(f"stake_usd must be positive, got {stake_usd}")
    records = _load_decisions(decisions_path)
    decided = _decided_records(records)
    markets_by_slug = {}
    estimates = []
    pending = []

    with httpx.Client(timeout=20.0) as client:
        for record in decided:
            slug = record["slug"]
            if slug not in markets_by_slug:
                markets_by_slug[slug] = _fetch_market_by_slug(client, slug)
            winner = _winning_side(markets_by_slug[slug])
            if winner is None:
                pending.append(record)
                continue
            estimates.append(_estimate_record(record, winner, stake_usd))

    wins = sum(1 for item in estimates if item["won"])
    losses = len(estimates) - wins
    total_pnl = sum((item["pnl"] for item in estimates), Decimal("0"))
    total_staked = sum((item["actual_cost"] for item in estimates), Decimal("0"))

    print("Decision Observation Result Estimate")
    print("=" * 42)
    print("ESTIMATE ONLY: not live-equivalent execution P&L.")
    print("Uses logged decision-side estimated fill size/cost; excludes fees and live failures.")
    print(f"records_read: {len(records)}")
    print(f"decided_records: {len(decided)}")
    print(f"resolved_decisions: {len(estimates)}")
    print(f"pending_decisions: {len(pending)}")
    print(f"wins: {wins}")
    print(f"losses: {losses}")
    if estimates:
        print(f"win_rate: {wins / len(estimates):.2%}")
    print(f"estimated_staked: {_money(total_staked)}")
    print(f"estimated_pnl: {_money(total_pnl)}")
    print()

    for item in estimates:
        result = "WIN" if item["won"] else "LOSS"
        print(
            f"{result:4} {item['ts']} {item['slug']} "
            f"dir={item['direction']} winner={item['winner']} "
            f"entry={item['entry']} pnl={_money(item['pnl'])}"
        )
    for record in pending:
        print(f"PENDING {record['ts']} {record['slug']} dir={record['decided_direction']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate win/loss for decision-observation records."
    )
    parser.add_argument("decisions_path", type=Path)
    parser.add_argument("--stake-usd", required=True, type=Decimal)
    args = parser.parse_args()
    raise SystemExit(run(args.decisions_path, args.stake_usd))


if __name__ == "__main__":
    main()
