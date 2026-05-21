from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.strategy_brain.signal_processors.tick_velocity_processor import (
    TickVelocityProcessor,
)


def test_tick_velocity_uses_decision_reference_time():
    processor = TickVelocityProcessor(
        velocity_threshold_60s=0.001,
        velocity_threshold_30s=0.001,
        min_ticks=2,
    )
    reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "decision_reference_time": reference_time,
        "tick_buffer": [
            {"ts": reference_time - timedelta(seconds=30), "price": Decimal("0.50")},
            {"ts": reference_time, "price": Decimal("0.55")},
        ],
    }

    signal = processor.process(Decimal("0.55"), [], metadata)

    assert signal is not None
    assert signal.direction.value == "bullish"


def test_tick_velocity_requires_decision_reference_time():
    processor = TickVelocityProcessor(min_ticks=2)
    reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "tick_buffer": [
            {"ts": reference_time - timedelta(seconds=30), "price": Decimal("0.50")},
            {"ts": reference_time, "price": Decimal("0.55")},
        ],
    }

    with pytest.raises(RuntimeError, match="decision_reference_time"):
        processor.process(Decimal("0.55"), [], metadata)


def test_tick_velocity_rejects_naive_decision_reference_time():
    processor = TickVelocityProcessor(min_ticks=2)
    reference_time = datetime(2000, 1, 1)
    aware_reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "decision_reference_time": reference_time,
        "tick_buffer": [
            {"ts": aware_reference_time, "price": Decimal("0.50")},
            {"ts": aware_reference_time, "price": Decimal("0.55")},
        ],
    }

    with pytest.raises(RuntimeError, match="timezone-aware decision_reference_time"):
        processor.process(Decimal("0.55"), [], metadata)


def test_tick_velocity_rejects_naive_tick_timestamp():
    processor = TickVelocityProcessor(min_ticks=2)
    reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "decision_reference_time": reference_time,
        "tick_buffer": [
            {"ts": datetime(2000, 1, 1), "price": Decimal("0.50")},
            {"ts": reference_time, "price": Decimal("0.55")},
        ],
    }

    with pytest.raises(RuntimeError, match="timezone-aware tick timestamps"):
        processor.process(Decimal("0.55"), [], metadata)
