"""Immutable local inputs captured at the start of a trading decision."""

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
class DecisionInputSnapshot:
    """Decision-local market state used by context fetches and processors."""

    decision_id: str
    captured_at: datetime
    reference_time: datetime
    current_price: Decimal
    price_history: tuple[Decimal, ...]
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

    def __post_init__(self) -> None:
        _require_timezone_aware(self.captured_at, "DecisionInputSnapshot.captured_at")
        _require_timezone_aware(self.reference_time, "DecisionInputSnapshot.reference_time")

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
