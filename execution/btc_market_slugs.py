"""BTC 15-minute Polymarket slug helpers."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from loguru import logger


def current_btc_15m_slug() -> str:
    """
    Get the current BTC 15-minute market slug.

    Polymarket BTC 15-min markets follow:
    btc-updown-15m-{unix_timestamp}
    """
    now = datetime.now(timezone.utc)
    unix_s = int(now.timestamp())
    interval_start = math.floor(unix_s / 900) * 900
    slug = f"btc-updown-15m-{interval_start}"

    logger.info(f"Current BTC 15-min market slug: {slug}")
    return slug


def get_next_btc_15m_markets(count: int = 3) -> list[str]:
    """Get current and future BTC 15-minute market slugs."""
    now = datetime.now(timezone.utc)
    unix_s = int(now.timestamp())
    interval_start = math.floor(unix_s / 900) * 900

    slugs = []
    for i in range(count):
        timestamp = interval_start + (i * 900)
        slugs.append(f"btc-updown-15m-{timestamp}")

    logger.info(f"BTC 15-min market slugs (next {count}): {slugs}")
    return slugs
