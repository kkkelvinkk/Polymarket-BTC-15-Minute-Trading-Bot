"""Shared Gamma-resolution helpers extracted from calibration_decision_join
and estimate_decision_results (Alpha-4).

Per ``docs/RAW_DECISION_SNAPSHOT_PLAN.md`` §6.1 Alpha-4, this module exposes
ONLY the helpers both callers genuinely share. Per-caller policy stays in
the calling modules:

  * ``calibration_decision_join`` keeps its ``closed-only`` Gamma filter
    (``fetch_market_by_slug(client, slug, closed_only=True)``).
  * ``estimate_decision_results`` keeps its ``accept-unclosed-as-pending``
    behaviour (``fetch_market_by_slug(client, slug, closed_only=False)``
    and the caller treats ``winning_side()==None`` as pending).

The helpers are pure: they raise on malformed inputs and do not silently
substitute defaults (CLAUDE.md Rule 1; plan M4). The single network call
(``fetch_market_by_slug``) uses ``raise_for_status()`` so HTTP errors
propagate to the caller.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, overload

import httpx


GAMMA_MARKETS_URL: str = "https://gamma-api.polymarket.com/markets"
WINNING_PRICE: Decimal = Decimal("1")


def parse_finite_decimal(value: Any, field_name: str) -> Decimal:
    """Parse ``value`` into a finite ``Decimal`` or raise ``ValueError``.

    Replaces the per-file ``_decimal`` helpers in both source modules.
    """
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return parsed


def parse_json_array(value: Any, field_name: str) -> list[Any]:
    """Decode a JSON-encoded array string into a Python list.

    Gamma encodes ``outcomes`` and ``outcomePrices`` as JSON strings inside
    its market JSON object. The helper raises if the encoded payload is not
    a list.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON-encoded array string")
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return parsed


def load_decision_records(path: Path) -> list[dict[str, Any]]:
    """Load ``decisions.jsonl`` into an in-memory list.

    Blank lines and non-object lines raise ``ValueError`` with file:line
    location. Mirrors the prior ``_load_decisions`` /
    ``load_decision_records`` implementations bit-for-bit.
    """
    records: list[dict[str, Any]] = []
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


@overload
def fetch_market_by_slug(
    client: httpx.Client,
    slug: str,
    *,
    closed_only: Literal[True],
) -> dict[str, Any] | None: ...


@overload
def fetch_market_by_slug(
    client: httpx.Client,
    slug: str,
    *,
    closed_only: Literal[False],
) -> dict[str, Any]: ...


def fetch_market_by_slug(
    client: httpx.Client,
    slug: str,
    *,
    closed_only: bool,
) -> dict[str, Any] | None:
    """Fetch a single Gamma market by exact slug.

    ``closed_only=True`` mirrors the calibration caller's
    ``?closed=true&limit=2`` query — when no exact closed match exists,
    returns ``None`` IFF Gamma returned zero markets (interpreted as
    "market not yet present") and raises otherwise.

    ``closed_only=False`` mirrors the estimate caller's ``?limit=2`` query
    and requires exactly one exact match (zero matches raise). The caller
    then inspects ``winning_side(market)`` to distinguish closed-with-
    winner from still-open markets. The ``@overload`` signatures above
    narrow the return type to non-``None`` for this branch so callers
    do not need a defensive ``assert`` (which would be stripped under
    ``python -O``).
    """
    params: dict[str, Any] = {"slug": slug, "limit": 2}
    if closed_only:
        params["closed"] = "true"
    response = client.get(GAMMA_MARKETS_URL, params=params)
    response.raise_for_status()
    markets = response.json()
    if not isinstance(markets, list):
        raise ValueError("Gamma markets response is not a JSON array")
    exact_matches = [m for m in markets if m["slug"] == slug]
    if len(exact_matches) == 0:
        if closed_only:
            if markets:
                raise ValueError(
                    f"Gamma returned no exact closed match for slug {slug!r} "
                    f"among {len(markets)} candidate markets"
                )
            return None
        raise ValueError(
            f"Gamma returned 0 exact matches for slug {slug!r}"
        )
    if len(exact_matches) != 1:
        raise ValueError(
            f"Gamma returned {len(exact_matches)} exact matches for slug {slug!r}"
        )
    market = exact_matches[0]
    if not isinstance(market, dict):
        raise ValueError(f"Gamma market for slug {slug!r} is not a JSON object")
    return market


def market_is_closed(market: dict[str, Any]) -> bool:
    """Return ``market['closed']`` after asserting it is a Python ``bool``.

    Gamma returns ``closed`` as JSON ``true`` / ``false``; any other shape
    raises so callers don't silently coerce strings or ints.
    """
    closed = market["closed"]
    if isinstance(closed, bool):
        return closed
    raise ValueError(f"closed must be a boolean for {market['slug']}")


def winning_side(market: dict[str, Any]) -> str | None:
    """Return ``"long"`` or ``"short"`` for closed markets, ``None`` for open.

    Raises if the market is closed but has zero or multiple ``price==1``
    outcomes, or if an outcome label is not one of ``yes/up/no/down``.
    """
    if not market_is_closed(market):
        return None
    outcomes = parse_json_array(market["outcomes"], "outcomes")
    prices = parse_json_array(market["outcomePrices"], "outcomePrices")
    if len(outcomes) != len(prices):
        raise ValueError(
            f"outcomes/outcomePrices length mismatch for {market['slug']}"
        )

    winners: list[str] = []
    for outcome, price in zip(outcomes, prices):
        parsed_price = parse_finite_decimal(price, "outcomePrices[]")
        if parsed_price == WINNING_PRICE:
            normalized = str(outcome).strip().lower()
            if normalized in ("yes", "up"):
                winners.append("long")
            elif normalized in ("no", "down"):
                winners.append("short")
            else:
                raise ValueError(
                    f"unsupported winning outcome {outcome!r} for {market['slug']}"
                )
    if len(winners) == 0:
        raise ValueError(f"closed market {market['slug']} has no winning outcome")
    if len(winners) != 1:
        raise ValueError(f"market {market['slug']} has {len(winners)} winners")
    return winners[0]
