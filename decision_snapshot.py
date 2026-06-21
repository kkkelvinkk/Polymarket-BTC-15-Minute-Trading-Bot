"""Immutable local inputs captured at the start of a trading decision.

Beta-1 contract:
  - ``PriceHistoryEntry`` (frozen dataclass) wraps each price tuple
    element with its source provenance and an optional UTC-aware
    timestamp.
  - ``DecisionInputSnapshot.price_history`` is
    ``tuple[PriceHistoryEntry, ...]`` (was ``tuple[Decimal, ...]``).
    Numeric-access readers must use ``.value``; the RP12-extended
    static check enforces.
  - New Optional UTC-aware fields ``yes_quote_timestamp`` and
    ``no_quote_timestamp`` carry per-side quote freshness for the
    raw snapshot's ``frozen_quotes`` block.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class DecisionTickSnapshot:
    """One immutable tick-buffer entry captured for a decision."""

    ts: datetime
    price: Decimal

    def __post_init__(self) -> None:
        _require_timezone_aware(self.ts, "DecisionTickSnapshot.ts")

    def as_processor_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "price": self.price}


@dataclass(frozen=True)
class PriceHistoryEntry:
    """
    Beta-1: One immutable price-history element with provenance.

    ``value`` is the numeric price; ``ts`` is the UTC-aware tick
    timestamp when the price came from a live quote tick, or None
    for synthetic-startup entries; ``source`` is a closed-set tag
    (``"live_quote_tick"`` | ``"synthetic_startup"``);
    ``synthetic`` is True for synthetic entries.
    """

    value: Decimal
    ts: Optional[datetime]
    source: str
    synthetic: bool

    def __post_init__(self) -> None:
        if self.ts is not None:
            _require_timezone_aware(self.ts, "PriceHistoryEntry.ts")
        if self.source not in ("live_quote_tick", "synthetic_startup"):
            raise RuntimeError(
                f"PriceHistoryEntry.source must be a known closed-enum "
                f"tag; got {self.source!r}"
            )


@dataclass(frozen=True)
class DecisionInputSnapshot:
    """Decision-local market state used by context fetches and processors."""

    decision_id: str
    captured_at: datetime
    reference_time: datetime
    current_price: Decimal
    price_history: tuple[PriceHistoryEntry, ...]
    tick_buffer: tuple[DecisionTickSnapshot, ...]
    yes_bid_ask: Optional[tuple[Decimal, Decimal]]
    no_bid_ask: Optional[tuple[Decimal, Decimal]]
    stable_tick_count: int
    market_slug: Optional[str]
    condition_id: Optional[str]
    yes_token_id: Optional[str]
    no_token_id: Optional[str]
    market_start_time: Any
    market_end_time: Any
    cached_yes_token_id: Optional[str]
    instrument_id: Any
    yes_instrument_id: Any
    no_instrument_id: Any
    market_timestamp: Optional[float]
    sub_interval: Optional[int]
    seconds_into_sub_interval: Optional[float]
    trade_window_label: Optional[str]
    # Beta-1: per-side quote freshness; Optional to permit early decisions
    # where one side has never received a tick. Recorder fills the §4.2
    # frozen_quotes block from these.
    yes_quote_timestamp: Optional[datetime] = None
    no_quote_timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_timezone_aware(self.captured_at, "DecisionInputSnapshot.captured_at")
        _require_timezone_aware(
            self.reference_time, "DecisionInputSnapshot.reference_time"
        )
        if self.yes_quote_timestamp is not None:
            _require_timezone_aware(
                self.yes_quote_timestamp,
                "DecisionInputSnapshot.yes_quote_timestamp",
            )
        if self.no_quote_timestamp is not None:
            _require_timezone_aware(
                self.no_quote_timestamp,
                "DecisionInputSnapshot.no_quote_timestamp",
            )

    def market_metadata(self) -> dict[str, Any]:
        return {
            "slug": self.market_slug,
            "condition_id": self.condition_id,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "start_time": self.market_start_time,
            "end_time": self.market_end_time,
            "yes_instrument_id": self.yes_instrument_id,
            "no_instrument_id": self.no_instrument_id,
        }
