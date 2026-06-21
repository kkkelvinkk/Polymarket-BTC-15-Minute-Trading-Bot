"""
Order Book Imbalance Signal Processor
Reads the Polymarket CLOB and detects buy/sell pressure skew.

Beta-2/3/4 + §3.D rows 5-8 contract:
  - ``name`` REQUIRED kwarg in __init__.
  - ``now``, ``decision_id`` REQUIRED kwargs on process().
  - Malformed levels → DROP with ``orderbook_level_malformed_dropped``.
  - process() top exception → DROP with
    ``orderbook_process_exception_dropped``.
  - fetch_order_book HTTP failure → DROP with ``orderbook_fetch_dropped``.
  - No silent substitution; no fallbacks.
"""
import httpx
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, Any, List
from loguru import logger

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from core.strategy_brain.signal_processors.base_processor import (
    BaseSignalProcessor,
    TradingSignal,
    SignalType,
    SignalDirection,
    SignalStrength,
)

CLOB_BASE = "https://clob.polymarket.com"


class OrderBookImbalanceProcessor(BaseSignalProcessor):
    def __init__(
        self,
        *,
        name: str,
        imbalance_threshold: float = 0.30,
        wall_threshold: float = 0.20,
        min_book_volume: float = 50.0,
        min_confidence: float = 0.55,
        top_levels: int = 10,
    ):
        super().__init__(name)

        self.imbalance_threshold = imbalance_threshold
        self.wall_threshold = wall_threshold
        self.min_book_volume = min_book_volume
        self.min_confidence = min_confidence
        self.top_levels = top_levels

        self._cache: Optional[Dict] = None

        logger.info(
            f"Initialized Order Book Imbalance Processor: "
            f"imbalance_threshold={imbalance_threshold:.0%}, "
            f"wall_threshold={wall_threshold:.0%}, "
            f"min_book_volume=${min_book_volume:.0f}"
        )

    def effective_params(self) -> Dict[str, Any]:
        return dict(sorted({
            "name": self.name,
            "imbalance_threshold": self.imbalance_threshold,
            "wall_threshold": self.wall_threshold,
            "min_book_volume": self.min_book_volume,
            "min_confidence": self.min_confidence,
            "top_levels": self.top_levels,
        }.items()))

    def _get_client(self) -> httpx.Client:
        return httpx.Client(timeout=5.0)

    def fetch_order_book(self, token_id: str) -> Optional[Dict]:
        """
        §3.D row-5 DROP on HTTP failure; caller increments
        ``orderbook_fetch_dropped`` and proceeds without this signal.

        Review-cycle fix: narrowed to ``httpx.HTTPError`` (covers network,
        timeout, HTTP-status, and decode failures from the httpx layer)
        plus ``ValueError`` (covers ``resp.json()`` failures on invalid
        JSON). Logic bugs (``NameError``, ``AttributeError``) propagate.
        """
        try:
            with self._get_client() as client:
                resp = client.get(
                    f"{CLOB_BASE}/book",
                    params={"token_id": token_id},
                )
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"OrderBook fetch failed for {token_id[:16]}…: {e}")
            self._increment_drop("orderbook_fetch_dropped")
            return None

    def _parse_levels(self, levels: List[Dict]) -> float:
        """Sum total USD volume; §3.D row-6 DROP malformed levels.

        Review-cycle fix: direct-index ``level["price"]`` / ``level["size"]``
        so a missing key raises ``KeyError`` and is caught by the malformed
        drop branch (the prior ``.get(..., 0)`` defaults silently produced
        zero-priced levels rather than dropping them, defeating §3.D).
        """
        total = 0.0
        for level in levels[:self.top_levels]:
            try:
                price = float(level["price"])
                size = float(level["size"])
                total += price * size
            except (ValueError, TypeError, KeyError):
                self._increment_drop("orderbook_level_malformed_dropped")
                continue
        return total

    def _detect_wall(self, levels: List[Dict], total_volume: float) -> Optional[float]:
        if total_volume <= 0:
            return None
        for level in levels[:self.top_levels]:
            try:
                price = float(level["price"])
                size = float(level["size"])
                order_usd = price * size
                if order_usd / total_volume >= self.wall_threshold:
                    return order_usd
            except (ValueError, TypeError, KeyError):
                self._increment_drop("orderbook_level_malformed_dropped")
                continue
        return None

    def process(
        self,
        current_price: Decimal,
        historical_prices: list,
        metadata: Dict[str, Any],
        *,
        now: datetime,
        decision_id: str,
    ) -> Optional[TradingSignal]:
        self._reset_signal_ordinal()

        if not self.is_enabled or not metadata:
            return None

        token_id = metadata.get("yes_token_id")
        if not token_id:
            return None

        if "yes_order_book" not in metadata:
            raise RuntimeError(
                "OrderBookImbalanceProcessor requires caller-provided yes_order_book snapshot"
            )
        book = metadata["yes_order_book"]

        try:
            if not book:
                return None

            bids = book.get("bids", [])
            asks = book.get("asks", [])

            bid_volume = self._parse_levels(bids)
            ask_volume = self._parse_levels(asks)
            total_volume = bid_volume + ask_volume

            if total_volume < self.min_book_volume:
                logger.debug(
                    f"OrderBook too thin: ${total_volume:.1f} < ${self.min_book_volume:.1f} — skipping"
                )
                return None

            imbalance = (bid_volume - ask_volume) / total_volume

            logger.info(
                f"OrderBook: bids=${bid_volume:.1f}, asks=${ask_volume:.1f}, "
                f"total=${total_volume:.1f}, imbalance={imbalance:+.3f}"
            )

            bid_wall = self._detect_wall(bids, total_volume)
            ask_wall = self._detect_wall(asks, total_volume)

            if abs(imbalance) < self.imbalance_threshold:
                logger.debug(f"OrderBook balanced (imbalance={imbalance:+.3f}) — no signal")
                return None

            direction = SignalDirection.BULLISH if imbalance > 0 else SignalDirection.BEARISH
            abs_imb = abs(imbalance)

            if abs_imb >= 0.70:
                strength = SignalStrength.VERY_STRONG
            elif abs_imb >= 0.50:
                strength = SignalStrength.STRONG
            elif abs_imb >= 0.35:
                strength = SignalStrength.MODERATE
            else:
                strength = SignalStrength.WEAK

            confidence = min(0.85, 0.55 + abs_imb * 0.40)
            wall_side = bid_wall if direction == SignalDirection.BULLISH else ask_wall
            if wall_side:
                confidence = min(0.90, confidence + 0.05)
                logger.info(
                    f"Wall detected on {'bid' if direction == SignalDirection.BULLISH else 'ask'} "
                    f"side: ${wall_side:.1f}"
                )

            if confidence < self.min_confidence:
                return None

            signal = TradingSignal(
                timestamp=now,
                source=self.name,
                signal_type=SignalType.VOLUME_SURGE,
                direction=direction,
                strength=strength,
                confidence=confidence,
                current_price=current_price,
                signal_id=self._next_signal_id(decision_id),
                metadata={
                    "bid_volume_usd": round(bid_volume, 2),
                    "ask_volume_usd": round(ask_volume, 2),
                    "total_volume_usd": round(total_volume, 2),
                    "imbalance": round(imbalance, 4),
                    "bid_wall_usd": round(bid_wall, 2) if bid_wall else None,
                    "ask_wall_usd": round(ask_wall, 2) if ask_wall else None,
                }
            )

            self._record_signal(signal)
            logger.info(
                f"Generated {direction.value.upper()} signal (OrderBook): "
                f"imbalance={imbalance:+.3f}, confidence={confidence:.2%}, "
                f"score={signal.score:.1f}"
            )
            return signal

        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as e:
            # §3.D row-8: narrow exception classes (review-cycle fix:
            # was bare `except Exception` which would swallow NameError /
            # logic bugs / etc.). DROP the whole orderbook signal for
            # this decision; increment the counter; never substitute.
            logger.warning(f"OrderBookImbalance process error: {e}")
            self._increment_drop("orderbook_process_exception_dropped")
            return None
