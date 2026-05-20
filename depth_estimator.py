"""
Depth-aware fill estimators for the EV gate.

The current EV gate uses top-of-book ask. Real market IOC fills sweep multiple
book levels; without depth-aware estimation, the gate filters on a price the
trade never actually pays. Two separate estimators are needed because they
have different inputs and different "fully filled" semantics:

- ``estimate_market_ioc_fill(levels, usd_to_spend)``: budget-driven. Spend up
  to a USD amount across asks. Used for ``ORDER_TYPE=market_ioc``.
- ``estimate_limit_ioc_fill(levels, target_token_qty, max_price)``:
  token-quantity-driven. Acquire up to N tokens at price <= cap. Used for
  ``ORDER_TYPE=limit_ioc``.

Book level units: each level's ``price`` is in (0, 1] (Polymarket binary token
price), ``size`` is in **tokens**. USD capacity at a level is
``price * size``.

Fail-closed on any impossible book data: ``InvalidBookLevelError`` is raised
on non-positive price/size, price > 1, or non-numeric values. The caller MUST
catch this, log the corrupt book, and refuse the trade. Per the
No-Fallback policy, this estimator does NOT silently skip bad levels.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional


class InvalidBookLevelError(ValueError):
    """Raised when a CLOB book level has impossible values."""


def _parse_book_level(level: dict, idx: int) -> tuple[Decimal, Decimal]:
    """Return ``(price, size_tokens)``, or raise on impossible book data."""
    try:
        price = Decimal(str(level["price"]))
        size_tokens = Decimal(str(level["size"]))
    except (KeyError, TypeError, ValueError, InvalidOperation) as e:
        raise InvalidBookLevelError(
            f"book level {idx} has non-numeric or missing price/size: {level!r}"
        ) from e
    if price <= 0 or price > 1:
        raise InvalidBookLevelError(
            f"book level {idx} price={price} is outside (0, 1]; refusing to compute"
        )
    if size_tokens <= 0:
        raise InvalidBookLevelError(
            f"book level {idx} size={size_tokens} is non-positive; refusing to compute"
        )
    return price, size_tokens


def estimate_market_ioc_fill(
    levels: list[dict],
    usd_to_spend: Decimal,
) -> tuple[Optional[Decimal], Decimal, bool]:
    """
    MARKET_IOC estimator: spend up to a USD budget across asks.

    Returns ``(vwap_or_none, total_tokens_filled, fully_filled)``.
    ``vwap_or_none`` is ``None`` when no tokens fill; never returns
    ``Decimal("0")`` as a no-fill sentinel.

    ``fully_filled`` is ``True`` when the budget was exhausted (either the
    last level only partially consumed remaining USD, or the full book was
    swept and there was zero remaining USD).

    Raises ``InvalidBookLevelError`` on any level with non-positive price or
    size, price > 1, or non-numeric values. Fail-closed; do NOT silently
    skip bad levels.
    """
    if usd_to_spend <= 0:
        raise ValueError(f"usd_to_spend must be positive, got {usd_to_spend}")
    remaining = usd_to_spend
    total_tokens = Decimal("0")
    total_cost = Decimal("0")
    for idx, level in enumerate(levels):
        price, size_tokens = _parse_book_level(level, idx)
        level_usd_capacity = price * size_tokens
        if remaining >= level_usd_capacity:
            total_tokens += size_tokens
            total_cost += level_usd_capacity
            remaining -= level_usd_capacity
        else:
            tokens_at_level = remaining / price
            total_tokens += tokens_at_level
            total_cost += remaining
            remaining = Decimal("0")
            break
    if total_tokens <= 0:
        return None, total_tokens, False
    vwap = total_cost / total_tokens
    return vwap, total_tokens, remaining <= 0


def estimate_fill_for_order_type(
    order_type: str,
    levels: list[dict],
    *,
    usd_to_spend: Optional[Decimal] = None,
    target_token_qty: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
) -> tuple[Optional[Decimal], Decimal, Optional[Decimal], bool]:
    """Unified entrypoint — dispatch to the correct estimator based
    on the validated ``ORDER_TYPE`` env value.

    Returns ``(vwap, tokens_filled, actual_cost_or_None, fully_filled)``.
    ``actual_cost_or_None`` is ``None`` for market orders (the budget IS the
    cost) and the realized USD spend for limit orders.

    Required arguments per order type:

    - ``market_ioc``: requires ``usd_to_spend``. ``target_token_qty`` and
      ``max_price`` must be omitted.
    - ``limit_ioc``: requires both ``target_token_qty`` and ``max_price``.
      ``usd_to_spend`` must be omitted.

    The strict argument-shape check is intentional: silently accepting the
    "wrong" arguments would let a caller pass a USD budget to a limit
    estimator and get a misleading partial-fill answer (see the reviewer-flagged
    P0 scenario in EXECUTION_PLAN.md).
    """
    if order_type == "market_ioc":
        if usd_to_spend is None:
            raise ValueError("market_ioc requires usd_to_spend")
        if target_token_qty is not None or max_price is not None:
            raise ValueError(
                "market_ioc does not accept target_token_qty or max_price"
            )
        vwap, tokens, full = estimate_market_ioc_fill(levels, usd_to_spend)
        return vwap, tokens, None, full

    if order_type == "limit_ioc":
        if target_token_qty is None or max_price is None:
            raise ValueError(
                "limit_ioc requires both target_token_qty and max_price"
            )
        if usd_to_spend is not None:
            raise ValueError("limit_ioc does not accept usd_to_spend")
        return estimate_limit_ioc_fill(levels, target_token_qty, max_price)

    raise ValueError(
        f"order_type must be 'market_ioc' or 'limit_ioc', got {order_type!r}"
    )


def estimate_limit_ioc_fill(
    levels: list[dict],
    target_token_qty: Decimal,
    max_price: Decimal,
) -> tuple[Optional[Decimal], Decimal, Decimal, bool]:
    """
    LIMIT_IOC estimator: acquire up to a token quantity at price <= max_price.

    Returns ``(vwap_or_none, total_tokens_filled, actual_cost, fully_filled)``.
    ``vwap_or_none`` is ``None`` when no tokens fill.

    Levels priced strictly above ``max_price`` are skipped (no fill at those
    levels); the walk stops at the first such level. ``actual_cost`` is the
    realized USD spend (always <= ``target_token_qty * max_price``).
    """
    if target_token_qty <= 0:
        raise ValueError(f"target_token_qty must be positive, got {target_token_qty}")
    if max_price <= 0 or max_price > 1:
        raise ValueError(f"max_price must be in (0, 1], got {max_price}")
    remaining_tokens = target_token_qty
    total_cost = Decimal("0")
    total_tokens = Decimal("0")
    for idx, level in enumerate(levels):
        price, size_tokens = _parse_book_level(level, idx)
        if price > max_price:
            break
        tokens_to_take = min(remaining_tokens, size_tokens)
        total_tokens += tokens_to_take
        total_cost += tokens_to_take * price
        remaining_tokens -= tokens_to_take
        if remaining_tokens <= 0:
            break
    if total_tokens <= 0:
        return None, total_tokens, total_cost, False
    vwap = total_cost / total_tokens
    return vwap, total_tokens, total_cost, remaining_tokens <= 0
