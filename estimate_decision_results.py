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
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
WINNING_PRICE = Decimal("1")


def _decimal(value: Any, field_name: str) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return parsed


def _json_array(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON-encoded array string")
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return parsed


def _load_decisions(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stripped = line.strip()
            if stripped == "":
                raise ValueError(f"{path}:{line_number} is blank")
            record = json.loads(stripped)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            records.append(record)
    return records


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
    response = client.get(
        GAMMA_MARKETS_URL,
        params={"slug": slug, "limit": 2},
    )
    response.raise_for_status()
    markets = response.json()
    if not isinstance(markets, list):
        raise ValueError("Gamma markets response is not a JSON array")
    exact_matches = [market for market in markets if market["slug"] == slug]
    if len(exact_matches) != 1:
        raise ValueError(f"Gamma returned {len(exact_matches)} exact matches for slug {slug!r}")
    market = exact_matches[0]
    if not isinstance(market, dict):
        raise ValueError(f"Gamma market for slug {slug!r} is not a JSON object")
    return market


def _market_is_closed(market: dict[str, Any]) -> bool:
    closed = market["closed"]
    if isinstance(closed, bool):
        return closed
    raise ValueError(f"closed must be a boolean for {market['slug']}")


def _winning_side(market: dict[str, Any]) -> str | None:
    closed = _market_is_closed(market)
    if not closed:
        return None
    outcomes = _json_array(market["outcomes"], "outcomes")
    prices = _json_array(market["outcomePrices"], "outcomePrices")
    if len(outcomes) != len(prices):
        raise ValueError(f"outcomes/outcomePrices length mismatch for {market['slug']}")

    winners = []
    for outcome, price in zip(outcomes, prices):
        parsed_price = _decimal(price, "outcomePrices[]")
        if parsed_price == WINNING_PRICE:
            normalized = str(outcome).strip().lower()
            if normalized in ("yes", "up"):
                winners.append("long")
            elif normalized in ("no", "down"):
                winners.append("short")
            else:
                raise ValueError(f"unsupported winning outcome {outcome!r} for {market['slug']}")
    if len(winners) == 0:
        raise ValueError(f"closed market {market['slug']} has no winning outcome")
    if len(winners) != 1:
        raise ValueError(f"market {market['slug']} has {len(winners)} winners")
    return winners[0]


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
