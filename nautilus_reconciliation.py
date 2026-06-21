from __future__ import annotations

from datetime import datetime, timezone


def loaded_window_reconciliation_lookback_mins(
    *,
    now: datetime,
    first_loaded_market_start: int,
    startup_buffer_seconds: int,
) -> int:
    """Return the Nautilus lookback covering only loaded past markets."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError("now must be timezone-aware")
    if startup_buffer_seconds < 0:
        raise RuntimeError("startup_buffer_seconds must not be negative")

    now_ts = int(now.astimezone(timezone.utc).timestamp())
    if first_loaded_market_start > now_ts:
        raise RuntimeError(
            "first_loaded_market_start must not be later than now"
        )

    elapsed_seconds = now_ts - first_loaded_market_start + startup_buffer_seconds
    return (elapsed_seconds + 59) // 60


def assert_reconciliation_window_covers_a_market(
    *,
    now: datetime,
    lookback_mins: int,
    market_start_timestamps: list[int],
    market_interval_seconds: int,
) -> int:
    """Fail-stop guard: the Nautilus reconciliation window must overlap at least
    one candidate market window.

    If it overlaps none, the patched aggregate order-status generation raises,
    ``generate_mass_status`` returns ``None``, and the kernel returns from
    ``start_async`` BEFORE ``trader.start()`` — leaving the strategy unstarted
    while the node still logs ``RUNNING``. This guard surfaces that geometry
    mismatch loudly at startup instead. Returns the overlap count.

    The window is ``[now - lookback_mins, now]``; each market window is
    ``[start, start + market_interval_seconds]``. The overlap test mirrors the
    patch verbatim: a market is excluded iff ``market_end < start_ts`` or
    ``market_start > end_ts``.

    Scope: this is a STARTUP pre-flight over the candidate slugs the bot is
    about to request (``market_start_timestamps``), validating that the
    configured lookback geometry can overlap a market at all. It is not a
    guarantee about the instruments Gamma actually returns — the patched
    runtime reconciliation re-checks the same overlap against the truly-loaded
    ``instrument_provider.list_all()`` set. The current active market is always
    in both sets, so a passing pre-flight means a coherent lookback config; an
    empty/partial Gamma load is caught (and raised) by the runtime patch.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError("now must be timezone-aware")
    if lookback_mins <= 0:
        raise RuntimeError("lookback_mins must be positive")
    if market_interval_seconds <= 0:
        raise RuntimeError("market_interval_seconds must be positive")
    if not market_start_timestamps:
        raise RuntimeError("market_start_timestamps must not be empty")

    now_ts = int(now.astimezone(timezone.utc).timestamp())
    start_ts = now_ts - lookback_mins * 60
    end_ts = now_ts

    overlapping = 0
    for market_start in market_start_timestamps:
        market_end = market_start + market_interval_seconds
        if market_end < start_ts or market_start > end_ts:
            continue
        overlapping += 1

    if overlapping == 0:
        raise RuntimeError(
            f"Reconciliation window [{start_ts}, {end_ts}] overlaps none of the "
            f"{len(market_start_timestamps)} candidate market windows; startup "
            "reconciliation would abort the trader before it starts. Check the "
            "reconciliation lookback against market slug generation."
        )
    return overlapping
