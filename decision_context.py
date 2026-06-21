"""Market context construction for a frozen decision snapshot."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from decision_snapshot import DecisionInputSnapshot


async def fetch_market_context_for_snapshot(
    snapshot: DecisionInputSnapshot,
    orderbook_processor: Any,
    logger: Any,
) -> dict[str, Any]:
    """
    Fetch external data and compute local stats for processor metadata.

    Local price/tick inputs always come from ``snapshot`` so processor logs and
    decision inputs refer to one coherent decision point.
    """
    if len(snapshot.price_history) < 20:
        raise RuntimeError("market context snapshot requires at least 20 price points")

    current_price_float = float(snapshot.current_price)

    # Beta-1: price_history elements are PriceHistoryEntry; ``.value`` is the
    # numeric component. RP12-extended static check enforces this access path.
    recent_prices = [float(p.value) for p in snapshot.price_history[-20:]]
    sma_20 = sum(recent_prices) / len(recent_prices)
    context_sma20_deviation = (current_price_float - sma_20) / sma_20
    momentum = (
        (current_price_float - float(snapshot.price_history[-5].value))
        / float(snapshot.price_history[-5].value)
    )
    variance = sum((p - sma_20) ** 2 for p in recent_prices) / len(recent_prices)
    volatility = math.sqrt(variance)

    yes_token_id = snapshot.yes_token_id
    if yes_token_id in (None, ""):
        raise RuntimeError("market context fetch: decision snapshot missing yes_token_id")
    if snapshot.cached_yes_token_id not in (None, "") and snapshot.cached_yes_token_id != yes_token_id:
        raise RuntimeError(
            "market context fetch: cached YES token_id does not match decision snapshot metadata "
            f"({snapshot.cached_yes_token_id!r} != {yes_token_id!r})"
        )
    no_token_id = snapshot.no_token_id
    metadata = {
        "decision_id": snapshot.decision_id,
        "decision_snapshot_at": snapshot.captured_at.isoformat(),
        "decision_reference_time": snapshot.reference_time,
        "decision_price_history_len": len(snapshot.price_history),
        "decision_tick_buffer_len": len(snapshot.tick_buffer),
        "context_sma20_deviation": context_sma20_deviation,
        "momentum": momentum,
        "volatility": volatility,
        "tick_buffer": [tick.as_processor_dict() for tick in snapshot.tick_buffer],
        "yes_token_id": yes_token_id,
    }
    if no_token_id not in (None, ""):
        metadata["no_token_id"] = no_token_id
    yes_order_book = await asyncio.to_thread(
        orderbook_processor.fetch_order_book,
        yes_token_id,
    )
    if not yes_order_book:
        raise RuntimeError("YES order book fetch returned no data")
    metadata["yes_order_book"] = yes_order_book
    if no_token_id not in (None, ""):
        no_order_book = await asyncio.to_thread(
            orderbook_processor.fetch_order_book,
            no_token_id,
        )
        if not no_order_book:
            raise RuntimeError("NO order book fetch returned no data")
        metadata["no_order_book"] = no_order_book

    from data_sources.news_social.adapter import NewsSocialDataSource
    news_source = NewsSocialDataSource()
    await news_source.connect()
    try:
        fg = await news_source.get_fear_greed_index()
    finally:
        await news_source.disconnect()
    if not fg or "value" not in fg:
        raise RuntimeError("Fear & Greed fetch returned no value")
    if "classification" not in fg or fg["classification"] in (None, ""):
        raise RuntimeError("Fear & Greed fetch returned no classification")
    metadata["sentiment_score"] = float(fg["value"])
    metadata["sentiment_classification"] = str(fg["classification"])
    logger.info(
        f"Fear & Greed: {metadata['sentiment_score']:.0f} "
        f"({metadata['sentiment_classification']})"
    )

    from data_sources.coinbase.adapter import CoinbaseDataSource
    coinbase = CoinbaseDataSource()
    await coinbase.connect()
    try:
        spot = await coinbase.get_current_price()
    finally:
        await coinbase.disconnect()
    if not spot:
        raise RuntimeError("Coinbase price fetch returned no value")
    metadata["spot_price"] = float(spot)
    logger.info(f"Coinbase spot price: ${float(spot):,.2f}")

    logger.info(
        f"Market context [{snapshot.decision_id}] — "
        f"context_sma20_deviation={context_sma20_deviation:.2%}, "
        f"momentum={momentum:.2%}, volatility={volatility:.4f}, "
        f"sentiment={metadata['sentiment_score']:.0f}, "
        f"spot=${metadata['spot_price']:.2f}"
    )
    return metadata
