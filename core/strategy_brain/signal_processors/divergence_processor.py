"""
Price Divergence Signal Processor
Detects mispricings between Polymarket UP probability and BTC spot momentum.

Beta-2/3/4 + §3.D row-10 contract:
  - ``name`` REQUIRED kwarg in __init__.
  - ``max_spot_history`` REQUIRED kwarg in __init__ (no default — M11 +
    §12 promoted env DIVERGENCE_SPOT_HISTORY_MAX_LEN).
  - ``now``, ``decision_id`` REQUIRED kwargs on process().
  - EARLY-RETURN when ``spot_price`` is None: drop the divergence signal
    entirely AND increment ``divergence_coinbase_missing_dropped``. NO
    polymarket-momentum substitution (the substitution was a silent
    semantic corruption per §3.D guard-rail 1; row-10 disposition).
  - Atomic helper ``_append_spot(value, ts)`` is the SOLE writer to
    ``_spot_history`` / ``_spot_history_ts``; static check (RP12)
    forbids any other write.
  - ``spot_history_pre_state_snapshot()`` returns BEFORE-mutation state
    for the recorder.
"""
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
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


class PriceDivergenceProcessor(BaseSignalProcessor):
    def __init__(
        self,
        *,
        name: str,
        max_spot_history: int,
        divergence_threshold: float = 0.05,
        min_confidence: float = 0.55,
        momentum_threshold: float = 0.003,
        extreme_prob_threshold: float = 0.68,
        low_prob_threshold: float = 0.32,
    ):
        super().__init__(name)

        if not isinstance(max_spot_history, int) or max_spot_history <= 0:
            raise ValueError(
                "PriceDivergenceProcessor: max_spot_history must be a positive int"
            )

        self.divergence_threshold = divergence_threshold
        self.min_confidence = min_confidence
        self.momentum_threshold = momentum_threshold
        self.extreme_prob_threshold = extreme_prob_threshold
        self.low_prob_threshold = low_prob_threshold

        # Production state — written ONLY by _append_spot (RP12 static check).
        self._spot_history: List[float] = []
        self._spot_history_ts: List[Optional[datetime]] = []
        self._max_spot_history = max_spot_history

        # Pre-state snapshot captured at top of process() so the recorder
        # can observe state BEFORE process() mutated it.
        self._pre_state: Tuple[Tuple[Optional[datetime], float], ...] = tuple()

        logger.info(
            f"Initialized Price Divergence Processor (FIXED): "
            f"momentum_thresh={momentum_threshold:.1%}, "
            f"extreme_fade={extreme_prob_threshold:.0%}/{low_prob_threshold:.0%}, "
            f"max_spot_history={max_spot_history}"
        )

    def effective_params(self) -> Dict[str, Any]:
        return dict(sorted({
            "name": self.name,
            "max_spot_history": self._max_spot_history,
            "divergence_threshold": self.divergence_threshold,
            "min_confidence": self.min_confidence,
            "momentum_threshold": self.momentum_threshold,
            "extreme_prob_threshold": self.extreme_prob_threshold,
            "low_prob_threshold": self.low_prob_threshold,
        }.items()))

    def _append_spot(self, value: float, ts: Optional[datetime]) -> None:
        """SOLE allowed writer to _spot_history / _spot_history_ts."""
        self._spot_history.append(value)
        self._spot_history_ts.append(ts)
        while len(self._spot_history) > self._max_spot_history:
            self._spot_history.pop(0)
            self._spot_history_ts.pop(0)

    def spot_history_pre_state_snapshot(self) -> Tuple[Tuple[Optional[datetime], float], ...]:
        """Recorder reads pre-mutation state captured at process() entry."""
        return self._pre_state

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
        # Capture pre-state for the recorder BEFORE any mutation.
        self._pre_state = tuple(
            (ts, val) for ts, val in zip(self._spot_history_ts, self._spot_history)
        )

        if not self.is_enabled:
            return None

        if not metadata:
            self._increment_drop("divergence_metadata_missing_dropped")
            return None

        spot_price = metadata.get('spot_price')

        # §3.D row-10: spot_price missing → DROP the divergence signal entirely.
        # NO polymarket-momentum substitution. Early return BEFORE any
        # spot_momentum branch evaluates.
        if spot_price is None:
            self._increment_drop("divergence_coinbase_missing_dropped")
            logger.warning(
                "PriceDivergence: spot_price missing → divergence signal DROPPED "
                "(no polymarket-momentum substitution per §3.D)"
            )
            return None

        poly_prob = float(current_price)
        spot_price_f = float(spot_price)

        # Update spot history via the sole allowed helper.
        self._append_spot(spot_price_f, now)

        # Compute spot momentum from the (now-updated) history.
        # Review-cycle fix: a non-positive `oldest` is data corruption
        # (spot prices are always > 0), so raise rather than silently
        # substituting 0.0 momentum (Rule 1).
        spot_momentum = 0.0
        if len(self._spot_history) >= 3:
            oldest = self._spot_history[-min(3, len(self._spot_history))]
            if oldest <= 0:
                raise RuntimeError(
                    f"PriceDivergence: spot history contains non-positive "
                    f"value {oldest!r}; corruption — fail-stop"
                )
            spot_momentum = (spot_price_f - oldest) / oldest

        logger.info(
            f"PriceDivergence: poly_prob={poly_prob:.3f}, "
            f"spot_momentum={spot_momentum:+.4f} ({spot_momentum*100:+.2f}%), "
            f"spot_price=${spot_price_f:,.2f}"
        )

        # SIGNAL 1: EXTREME PROBABILITY FADE
        if poly_prob >= self.extreme_prob_threshold:
            if spot_momentum <= 0.001:
                extremeness = (poly_prob - self.extreme_prob_threshold) / (
                    1.0 - self.extreme_prob_threshold
                )
                confidence = min(0.80, self.min_confidence + extremeness * 0.25)
                strength = (
                    SignalStrength.STRONG if extremeness > 0.5
                    else SignalStrength.MODERATE
                )

                signal = TradingSignal(
                    timestamp=now,
                    source=self.name,
                    signal_type=SignalType.PRICE_DIVERGENCE,
                    direction=SignalDirection.BEARISH,
                    strength=strength,
                    confidence=confidence,
                    current_price=current_price,
                    signal_id=self._next_signal_id(decision_id),
                    metadata={
                        "signal_type": "extreme_prob_fade_down",
                        "poly_prob": poly_prob,
                        "spot_momentum": spot_momentum,
                        "extremeness": extremeness,
                    }
                )
                self._record_signal(signal)
                logger.info(
                    f"Generated BEARISH fade signal: poly Up prob too high "
                    f"({poly_prob:.0%}) with weak momentum → fade DOWN, "
                    f"confidence={confidence:.2%}"
                )
                return signal

        elif poly_prob <= self.low_prob_threshold:
            if spot_momentum >= -0.001:
                extremeness = (self.low_prob_threshold - poly_prob) / self.low_prob_threshold
                confidence = min(0.80, self.min_confidence + extremeness * 0.25)
                strength = (
                    SignalStrength.STRONG if extremeness > 0.5
                    else SignalStrength.MODERATE
                )

                signal = TradingSignal(
                    timestamp=now,
                    source=self.name,
                    signal_type=SignalType.PRICE_DIVERGENCE,
                    direction=SignalDirection.BULLISH,
                    strength=strength,
                    confidence=confidence,
                    current_price=current_price,
                    signal_id=self._next_signal_id(decision_id),
                    metadata={
                        "signal_type": "extreme_prob_fade_up",
                        "poly_prob": poly_prob,
                        "spot_momentum": spot_momentum,
                        "extremeness": extremeness,
                    }
                )
                self._record_signal(signal)
                logger.info(
                    f"Generated BULLISH fade signal: poly Down prob too high "
                    f"({1-poly_prob:.0%}) with weak negative momentum → fade UP, "
                    f"confidence={confidence:.2%}"
                )
                return signal

        # SIGNAL 2: MOMENTUM MISPRICING
        if 0.35 <= poly_prob <= 0.65 and abs(spot_momentum) >= self.momentum_threshold:
            momentum_strength = abs(spot_momentum) / self.momentum_threshold
            confidence = min(0.78, 0.55 + min(momentum_strength - 1, 2) * 0.08)

            if momentum_strength >= 3:
                strength = SignalStrength.STRONG
            elif momentum_strength >= 2:
                strength = SignalStrength.MODERATE
            else:
                strength = SignalStrength.WEAK

            if confidence < self.min_confidence:
                return None

            direction = SignalDirection.BULLISH if spot_momentum > 0 else SignalDirection.BEARISH

            signal = TradingSignal(
                timestamp=now,
                source=self.name,
                signal_type=SignalType.PRICE_DIVERGENCE,
                direction=direction,
                strength=strength,
                confidence=confidence,
                current_price=current_price,
                signal_id=self._next_signal_id(decision_id),
                metadata={
                    "signal_type": "momentum_mispricing",
                    "poly_prob": poly_prob,
                    "spot_momentum": spot_momentum,
                    "momentum_strength": momentum_strength,
                }
            )
            self._record_signal(signal)
            logger.info(
                f"Generated {direction.value.upper()} momentum signal: "
                f"spot moved {spot_momentum:+.3%} but poly still at {poly_prob:.0%}, "
                f"confidence={confidence:.2%}"
            )
            return signal

        logger.debug(
            f"PriceDivergence: no signal — prob={poly_prob:.2f}, "
            f"momentum={spot_momentum:+.4f}"
        )
        return None
