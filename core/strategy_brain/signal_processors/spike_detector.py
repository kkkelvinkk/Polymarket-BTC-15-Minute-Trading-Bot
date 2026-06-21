"""
Spike Detection Signal Processor
Detects sudden price movements and generates signals.

Beta-2/3/4 contract:
  - ``name`` REQUIRED kwarg in __init__.
  - ``now``, ``decision_id`` REQUIRED kwargs on process().
  - All internal ``datetime.now()`` reads replaced by injected ``now``.
  - ``signal_id`` populated on every TradingSignal.
  - ``effective_params()`` exposes parameters.
"""
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, Any
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


class SpikeDetectionProcessor(BaseSignalProcessor):
    def __init__(
        self,
        *,
        name: str,
        spike_threshold: float = 0.05,
        lookback_periods: int = 20,
        min_confidence: float = 0.55,
        velocity_threshold: float = 0.03,
    ):
        super().__init__(name)

        self.spike_threshold = spike_threshold
        self.lookback_periods = lookback_periods
        self.min_confidence = min_confidence
        self.velocity_threshold = velocity_threshold

        logger.info(
            f"Initialized Spike Detector (FIXED): "
            f"deviation_threshold={spike_threshold:.1%}, "
            f"velocity_threshold={velocity_threshold:.1%}, "
            f"lookback={lookback_periods}"
        )

    def effective_params(self) -> Dict[str, Any]:
        return dict(sorted({
            "name": self.name,
            "spike_threshold": self.spike_threshold,
            "lookback_periods": self.lookback_periods,
            "min_confidence": self.min_confidence,
            "velocity_threshold": self.velocity_threshold,
        }.items()))

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

        if not self.is_enabled:
            return None
        if len(historical_prices) < self.lookback_periods:
            return None

        # Beta-1: historical_prices is tuple[PriceHistoryEntry, ...]; ``.value``
        # is the numeric component. RP12-extended static check enforces.
        # Beta-1: historical_prices is tuple[PriceHistoryEntry, ...]; ``.value``
        # is the numeric component. RP12-extended static check enforces.
        recent = historical_prices[-self.lookback_periods:]
        ma = sum(float(p.value) for p in recent) / len(recent)
        curr = float(current_price)

        # Review-cycle fix: a non-positive moving average is corruption
        # (Polymarket probabilities are > 0); fail-stop rather than
        # silently substituting 0.0 deviation.
        if ma <= 0:
            raise RuntimeError(
                f"SpikeDetection: moving average {ma!r} is non-positive — corruption"
            )
        deviation = (curr - ma) / ma
        deviation_abs = abs(deviation)

        velocity = 0.0
        if len(historical_prices) >= 3:
            prev3 = float(historical_prices[-3].value)
            if prev3 <= 0:
                raise RuntimeError(
                    f"SpikeDetection: prev3 {prev3!r} is non-positive — corruption"
                )
            velocity = (curr - prev3) / prev3

        logger.debug(
            f"SpikeDetector: price={curr:.4f}, MA={ma:.4f}, "
            f"deviation={deviation:+.3%}, velocity={velocity:+.3%}"
        )

        # SIGNAL 1: MA DEVIATION SPIKE → mean reversion
        if deviation_abs >= self.spike_threshold:
            logger.info(
                f"MA deviation spike: {deviation:+.3%} from MA "
                f"(${curr:.4f} vs MA={ma:.4f})"
            )

            direction = SignalDirection.BEARISH if deviation > 0 else SignalDirection.BULLISH
            target = Decimal(str(ma))

            if deviation_abs >= 0.12:
                strength = SignalStrength.VERY_STRONG
            elif deviation_abs >= 0.08:
                strength = SignalStrength.STRONG
            elif deviation_abs >= 0.05:
                strength = SignalStrength.MODERATE
            else:
                strength = SignalStrength.WEAK

            confidence = min(0.90, 0.50 + (deviation_abs - self.spike_threshold) * 3.0)
            if confidence < self.min_confidence:
                return None

            stop_distance = abs(Decimal(str(curr)) - Decimal(str(ma))) * Decimal("1.5")
            stop_loss = (
                Decimal(str(curr)) + stop_distance if direction == SignalDirection.BEARISH
                else Decimal(str(curr)) - stop_distance
            )

            signal = TradingSignal(
                timestamp=now,
                source=self.name,
                signal_type=SignalType.SPIKE_DETECTED,
                direction=direction,
                strength=strength,
                confidence=confidence,
                current_price=current_price,
                signal_id=self._next_signal_id(decision_id),
                target_price=target,
                stop_loss=stop_loss,
                metadata={
                    "detection_mode": "ma_deviation",
                    "deviation_pct": deviation,
                    "moving_average": ma,
                    "velocity": velocity,
                    "spike_direction": "up" if deviation > 0 else "down",
                }
            )
            self._record_signal(signal)
            logger.info(
                f"Generated {direction.value.upper()} signal (MA deviation): "
                f"deviation={deviation:+.3%}, confidence={confidence:.2%}, "
                f"score={signal.score:.1f}"
            )
            return signal

        # SIGNAL 2: VELOCITY SPIKE → short-term momentum continuation
        if abs(velocity) >= self.velocity_threshold and deviation_abs < self.spike_threshold * 0.6:
            logger.info(f"Velocity spike: {velocity:+.3%} in last 3 ticks")

            direction = SignalDirection.BULLISH if velocity > 0 else SignalDirection.BEARISH

            vel_strength = abs(velocity) / self.velocity_threshold
            if vel_strength >= 3:
                strength = SignalStrength.MODERATE
                confidence = 0.65
            elif vel_strength >= 2:
                strength = SignalStrength.WEAK
                confidence = 0.60
            else:
                strength = SignalStrength.WEAK
                confidence = 0.57

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
                    "detection_mode": "velocity",
                    "velocity_pct": velocity,
                    "moving_average": ma,
                    "deviation_pct": deviation,
                }
            )
            self._record_signal(signal)
            logger.info(
                f"Generated {direction.value.upper()} signal (velocity): "
                f"velocity={velocity:+.3%}, confidence={confidence:.2%}"
            )
            return signal

        return None
