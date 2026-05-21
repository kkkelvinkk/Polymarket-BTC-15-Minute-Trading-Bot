from datetime import datetime, timezone
from decimal import Decimal

import pytest

from decision_snapshot import DecisionInputSnapshot, DecisionTickSnapshot


def _snapshot_kwargs():
    aware_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
    return {
        "decision_id": "decision-model-test",
        "captured_at": aware_time,
        "reference_time": aware_time,
        "current_price": Decimal("0.50"),
        "price_history": (Decimal("0.50"),),
        "tick_buffer": (DecisionTickSnapshot(ts=aware_time, price=Decimal("0.50")),),
        "yes_bid_ask": (Decimal("0.49"), Decimal("0.51")),
        "no_bid_ask": (Decimal("0.49"), Decimal("0.51")),
        "stable_tick_count": 3,
        "market_slug": "slug",
        "condition_id": "condition",
        "yes_token_id": "yes",
        "no_token_id": "no",
        "market_start_time": aware_time,
        "market_end_time": aware_time,
        "cached_yes_token_id": "yes",
        "instrument_id": "instrument",
        "yes_instrument_id": "yes-instrument",
        "no_instrument_id": "no-instrument",
        "market_timestamp": aware_time.timestamp(),
        "sub_interval": 0,
        "seconds_into_sub_interval": 780.0,
        "trade_window_label": "13_14_current",
    }


def test_decision_tick_snapshot_rejects_naive_timestamp():
    with pytest.raises(RuntimeError, match="DecisionTickSnapshot.ts"):
        DecisionTickSnapshot(ts=datetime(2030, 1, 1), price=Decimal("0.50"))


def test_decision_input_snapshot_rejects_naive_captured_at():
    kwargs = _snapshot_kwargs()
    kwargs["captured_at"] = datetime(2030, 1, 1)

    with pytest.raises(RuntimeError, match="captured_at"):
        DecisionInputSnapshot(**kwargs)


def test_decision_input_snapshot_rejects_naive_reference_time():
    kwargs = _snapshot_kwargs()
    kwargs["reference_time"] = datetime(2030, 1, 1)

    with pytest.raises(RuntimeError, match="reference_time"):
        DecisionInputSnapshot(**kwargs)
