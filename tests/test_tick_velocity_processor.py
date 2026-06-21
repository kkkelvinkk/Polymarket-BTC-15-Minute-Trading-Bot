from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.strategy_brain.signal_processors.tick_velocity_processor import (
    TickVelocityProcessor,
)


def _build_processor(**overrides):
    """Build a processor with the Beta-3/4 REQUIRED kwargs satisfied."""
    defaults = dict(
        name="TickVelocity",
        tolerance_seconds=15,
        velocity_threshold_60s=0.001,
        velocity_threshold_30s=0.001,
        min_ticks=2,
    )
    defaults.update(overrides)
    return TickVelocityProcessor(**defaults)


def test_tick_velocity_uses_injected_now():
    """Beta-2: now= is REQUIRED and feeds the tick-window math."""
    processor = _build_processor()
    reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "tick_buffer": [
            {"ts": reference_time - timedelta(seconds=30), "price": Decimal("0.50")},
            {"ts": reference_time, "price": Decimal("0.55")},
        ],
    }

    signal = processor.process(
        Decimal("0.55"), [], metadata,
        now=reference_time,
        decision_id="dec-1",
    )

    assert signal is not None
    assert signal.direction.value == "bullish"
    assert signal.signal_id == "dec-1:TickVelocity:0"


def test_tick_velocity_requires_now_kwarg():
    """Beta-2 M11: missing now= raises TypeError."""
    processor = _build_processor()
    reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "tick_buffer": [
            {"ts": reference_time - timedelta(seconds=30), "price": Decimal("0.50")},
            {"ts": reference_time, "price": Decimal("0.55")},
        ],
    }

    with pytest.raises(TypeError):
        processor.process(Decimal("0.55"), [], metadata, decision_id="dec-1")


def test_tick_velocity_rejects_naive_now():
    """Beta-2 M9: naïve now= raises RuntimeError."""
    processor = _build_processor()
    aware_ts = datetime(2000, 1, 1, tzinfo=timezone.utc)
    naive_now = datetime(2000, 1, 1)
    metadata = {
        "tick_buffer": [
            {"ts": aware_ts, "price": Decimal("0.50")},
            {"ts": aware_ts, "price": Decimal("0.55")},
        ],
    }

    with pytest.raises(RuntimeError, match="timezone-aware now"):
        processor.process(
            Decimal("0.55"), [], metadata,
            now=naive_now,
            decision_id="dec-1",
        )


def test_tick_velocity_rejects_naive_tick_timestamp():
    """Tick timestamps must be timezone-aware."""
    processor = _build_processor()
    reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "tick_buffer": [
            {"ts": datetime(2000, 1, 1), "price": Decimal("0.50")},
            {"ts": reference_time, "price": Decimal("0.55")},
        ],
    }

    with pytest.raises(RuntimeError, match="timezone-aware tick timestamps"):
        processor.process(
            Decimal("0.55"), [], metadata,
            now=reference_time,
            decision_id="dec-1",
        )
