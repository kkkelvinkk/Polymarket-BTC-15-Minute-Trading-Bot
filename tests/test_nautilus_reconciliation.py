from datetime import datetime, timezone

import pytest

from nautilus_reconciliation import (
    assert_reconciliation_window_covers_a_market,
    loaded_window_reconciliation_lookback_mins,
)


def test_loaded_window_reconciliation_lookback_ceil_minutes():
    now = datetime.fromtimestamp(1781984095, tz=timezone.utc)
    first_loaded_market_start = 1781982900

    assert (
        loaded_window_reconciliation_lookback_mins(
            now=now,
            first_loaded_market_start=first_loaded_market_start,
            startup_buffer_seconds=900,
        )
        == 35
    )


def test_loaded_window_reconciliation_lookback_rejects_naive_now():
    with pytest.raises(RuntimeError, match="timezone-aware"):
        loaded_window_reconciliation_lookback_mins(
            now=datetime(2026, 6, 20, 19, 30, 0),
            first_loaded_market_start=1781982900,
            startup_buffer_seconds=900,
        )


def test_loaded_window_reconciliation_lookback_rejects_future_start():
    now = datetime.fromtimestamp(1781984095, tz=timezone.utc)

    with pytest.raises(RuntimeError, match="must not be later"):
        loaded_window_reconciliation_lookback_mins(
            now=now,
            first_loaded_market_start=1781984096,
            startup_buffer_seconds=900,
        )


def test_loaded_window_reconciliation_lookback_rejects_negative_buffer():
    now = datetime.fromtimestamp(1781984095, tz=timezone.utc)

    with pytest.raises(RuntimeError, match="must not be negative"):
        loaded_window_reconciliation_lookback_mins(
            now=now,
            first_loaded_market_start=1781982900,
            startup_buffer_seconds=-1,
        )


def test_assert_window_covers_counts_prior_and_current_markets():
    # now=1000000, lookback=10min -> window [999400, 1000000]; interval=900.
    now = datetime.fromtimestamp(1000000, tz=timezone.utc)
    # 999000 -> [999000, 999900] overlaps; 1000000 -> [1000000, 1000900]
    # overlaps at the right boundary; 1001000 -> starts after now, excluded.
    overlapping = assert_reconciliation_window_covers_a_market(
        now=now,
        lookback_mins=10,
        market_start_timestamps=[999000, 1000000, 1001000],
        market_interval_seconds=900,
    )
    assert overlapping == 2


def test_assert_window_covers_boundary_inclusive():
    # market_end == start_ts must count as overlapping (exclusion is strict `<`).
    now = datetime.fromtimestamp(1000000, tz=timezone.utc)
    # start_ts = 999400; choose start so end == 999400 -> start = 998500.
    overlapping = assert_reconciliation_window_covers_a_market(
        now=now,
        lookback_mins=10,
        market_start_timestamps=[998500],
        market_interval_seconds=900,
    )
    assert overlapping == 1


def test_assert_window_covers_raises_when_all_future():
    now = datetime.fromtimestamp(1000000, tz=timezone.utc)
    with pytest.raises(RuntimeError, match="overlaps none"):
        assert_reconciliation_window_covers_a_market(
            now=now,
            lookback_mins=10,
            market_start_timestamps=[2000000, 3000000],
            market_interval_seconds=900,
        )


def test_assert_window_covers_raises_when_all_past():
    now = datetime.fromtimestamp(1000000, tz=timezone.utc)
    with pytest.raises(RuntimeError, match="overlaps none"):
        assert_reconciliation_window_covers_a_market(
            now=now,
            lookback_mins=10,
            market_start_timestamps=[100000, 200000],
            market_interval_seconds=900,
        )


def test_assert_window_covers_rejects_naive_now():
    with pytest.raises(RuntimeError, match="timezone-aware"):
        assert_reconciliation_window_covers_a_market(
            now=datetime(2026, 6, 20, 19, 30, 0),
            lookback_mins=10,
            market_start_timestamps=[1000000],
            market_interval_seconds=900,
        )


def test_assert_window_covers_rejects_non_positive_lookback():
    now = datetime.fromtimestamp(1000000, tz=timezone.utc)
    with pytest.raises(RuntimeError, match="lookback_mins must be positive"):
        assert_reconciliation_window_covers_a_market(
            now=now,
            lookback_mins=0,
            market_start_timestamps=[1000000],
            market_interval_seconds=900,
        )


def test_assert_window_covers_rejects_non_positive_interval():
    now = datetime.fromtimestamp(1000000, tz=timezone.utc)
    with pytest.raises(RuntimeError, match="market_interval_seconds must be positive"):
        assert_reconciliation_window_covers_a_market(
            now=now,
            lookback_mins=10,
            market_start_timestamps=[1000000],
            market_interval_seconds=0,
        )


def test_assert_window_covers_rejects_empty_market_list():
    now = datetime.fromtimestamp(1000000, tz=timezone.utc)
    with pytest.raises(RuntimeError, match="must not be empty"):
        assert_reconciliation_window_covers_a_market(
            now=now,
            lookback_mins=10,
            market_start_timestamps=[],
            market_interval_seconds=900,
        )
