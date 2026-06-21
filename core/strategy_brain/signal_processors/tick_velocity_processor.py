"""
Tick Velocity Signal Processor
Measures Polymarket UP probability velocity over the last 60 seconds.

Beta-2/3/4 contract:
  - ``name`` REQUIRED kwarg in __init__.
  - ``tolerance_seconds`` REQUIRED kwarg in __init__ (no default — M11
    + §12 promoted env TICK_VELOCITY_TOLERANCE_SECONDS).
  - ``now``, ``decision_id`` REQUIRED kwargs on process().
  - Uses injected ``now`` for tick-window math instead of any
    ``datetime.now()`` read.
"""
from decimal import Decimal
from datetime import datetime, timedelta
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


class TickVelocityProcessor(BaseSignalProcessor):
    def __init__(
        self,
        *,
        name: str,
        tolerance_seconds: int,
        velocity_threshold_60s: float = 0.015,
        velocity_threshold_30s: float = 0.010,
        min_ticks: int = 5,
        min_confidence: float = 0.55,
    ):
        super().__init__(name)

        if not isinstance(tolerance_seconds, int) or tolerance_seconds <= 0:
            raise ValueError(
                "TickVelocityProcessor: tolerance_seconds must be a positive int"
            )

        self.velocity_threshold_60s = velocity_threshold_60s
        self.velocity_threshold_30s = velocity_threshold_30s
        self.min_ticks = min_ticks
        self.min_confidence = min_confidence
        self.tolerance_seconds = tolerance_seconds

        logger.info(
            f"Initialized Tick Velocity Processor: "
            f"60s_threshold={velocity_threshold_60s:.1%}, "
            f"30s_threshold={velocity_threshold_30s:.1%}, "
            f"tolerance_seconds={tolerance_seconds}"
        )

    def effective_params(self) -> Dict[str, Any]:
        return dict(sorted({
            "name": self.name,
            "velocity_threshold_60s": self.velocity_threshold_60s,
            "velocity_threshold_30s": self.velocity_threshold_30s,
            "min_ticks": self.min_ticks,
            "min_confidence": self.min_confidence,
            "tolerance_seconds": self.tolerance_seconds,
        }.items()))

    def _get_price_at(
        self,
        tick_buffer: List[Dict],
        seconds_ago: float,
        now: datetime,
    ) -> Optional[float]:
        target = now - timedelta(seconds=seconds_ago)
        best = None
        best_diff = float('inf')

        for tick in tick_buffer:
            ts = tick['ts']
            if ts.tzinfo is None or ts.utcoffset() is None:
                raise RuntimeError("TickVelocity requires timezone-aware tick timestamps")
            diff = abs((ts - target).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best = float(tick['price'])

        if best_diff <= self.tolerance_seconds:
            return best
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

        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("TickVelocity requires timezone-aware now=")

        tick_buffer = metadata.get("tick_buffer")
        if not tick_buffer or len(tick_buffer) < self.min_ticks:
            logger.debug(
                f"TickVelocity: insufficient ticks "
                f"({len(tick_buffer) if tick_buffer else 0} < {self.min_ticks})"
            )
            return None

        curr = float(current_price)
        price_60s = self._get_price_at(tick_buffer, 60, now)
        price_30s = self._get_price_at(tick_buffer, 30, now)

        if price_60s is None and price_30s is None:
            logger.debug("TickVelocity: no historical ticks in 60s window")
            return None

        # Review-cycle fix: explicit `is not None` (was truthy check that
        # also substituted None on price == 0.0; a zero Polymarket
        # probability is data corruption and should fail-stop).
        if price_60s is not None:
            if price_60s == 0:
                raise RuntimeError("TickVelocity: price_60s == 0 (corruption)")
            vel_60s = (curr - price_60s) / price_60s
        else:
            vel_60s = None
        if price_30s is not None:
            if price_30s == 0:
                raise RuntimeError("TickVelocity: price_30s == 0 (corruption)")
            vel_30s = (curr - price_30s) / price_30s
        else:
            vel_30s = None

        acceleration = 0.0
        if vel_60s is not None and vel_30s is not None:
            vel_first_30s = vel_60s - vel_30s
            acceleration = vel_30s - vel_first_30s

        vel_60s_text = f"{vel_60s * 100:+.3f}%" if vel_60s is not None else "N/A"
        vel_30s_text = f"{vel_30s * 100:+.3f}%" if vel_30s is not None else "N/A"
        logger.info(
            f"TickVelocity: curr={curr:.4f}, "
            f"vel_60s={vel_60s_text}, vel_30s={vel_30s_text}, "
            f"accel={acceleration * 100:+.4f}%"
        )

        primary_vel = vel_30s if vel_30s is not None else vel_60s
        threshold = (
            self.velocity_threshold_30s if vel_30s is not None
            else self.velocity_threshold_60s
        )

        if abs(primary_vel) < threshold:
            logger.debug(
                f"TickVelocity: {primary_vel*100:+.3f}% below threshold "
                f"{threshold*100:.1f}% — no signal"
            )
            return None

        direction = SignalDirection.BULLISH if primary_vel > 0 else SignalDirection.BEARISH
        abs_vel = abs(primary_vel)

        if abs_vel >= 0.04:
            strength = SignalStrength.VERY_STRONG
        elif abs_vel >= 0.025:
            strength = SignalStrength.STRONG
        elif abs_vel >= 0.015:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.WEAK

        confidence = min(0.82, 0.55 + (abs_vel / threshold - 1) * 0.12)

        accel_same_direction = (
            (acceleration > 0 and primary_vel > 0) or
            (acceleration < 0 and primary_vel < 0)
        )
        if accel_same_direction and abs(acceleration) > 0.005:
            confidence = min(0.88, confidence + 0.06)
            logger.info(
                f"TickVelocity: acceleration bonus applied ({acceleration*100:+.4f}%)"
            )

        if vel_60s is not None and vel_30s is not None:
            if (vel_60s > 0) != (vel_30s > 0):
                confidence *= 0.80
                logger.info("TickVelocity: velocity reversal — confidence reduced")

        if confidence < self.min_confidence:
            return None

        signal = TradingSignal(
            timestamp=now,
            source=self.name,
            signal_type=SignalType.MOMENTUM,
            direction=direction,
            strength=strength,
            confidence=confidence,
            current_price=current_price,
            signal_id=self._next_signal_id(decision_id),
            metadata={
                "velocity_60s": round(vel_60s, 6) if vel_60s else None,
                "velocity_30s": round(vel_30s, 6) if vel_30s else None,
                "acceleration": round(acceleration, 6),
                "price_60s_ago": round(price_60s, 6) if price_60s else None,
                "price_30s_ago": round(price_30s, 6) if price_30s else None,
                "ticks_in_buffer": len(tick_buffer),
            }
        )

        self._record_signal(signal)

        logger.info(
            f"Generated {direction.value.upper()} signal (TickVelocity): "
            f"vel={primary_vel*100:+.3f}%, accel={acceleration*100:+.4f}%, "
            f"confidence={confidence:.2%}, score={signal.score:.1f}"
        )

        return signal
