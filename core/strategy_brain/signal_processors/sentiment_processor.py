"""
Sentiment Signal Processor
Generates signals based on market sentiment (Fear & Greed Index).

Beta-2/3/4 contract: see base_processor docstring.
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


class SentimentProcessor(BaseSignalProcessor):
    def __init__(
        self,
        *,
        name: str,
        extreme_fear_threshold: float = 25,
        extreme_greed_threshold: float = 75,
        min_confidence: float = 0.50,
    ):
        super().__init__(name)
        self.extreme_fear = extreme_fear_threshold
        self.extreme_greed = extreme_greed_threshold
        self.min_confidence = min_confidence

        logger.info(
            f"Initialized Sentiment Processor: "
            f"fear<{extreme_fear_threshold}, greed>{extreme_greed_threshold}"
        )

    def effective_params(self) -> Dict[str, Any]:
        return dict(sorted({
            "name": self.name,
            "extreme_fear_threshold": self.extreme_fear,
            "extreme_greed_threshold": self.extreme_greed,
            "min_confidence": self.min_confidence,
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
        if not metadata or 'sentiment_score' not in metadata:
            return None

        sentiment_score = float(metadata['sentiment_score'])

        if sentiment_score <= self.extreme_fear:
            direction = SignalDirection.BULLISH
            signal_type = SignalType.SENTIMENT_SHIFT
            extremeness = (self.extreme_fear - sentiment_score) / self.extreme_fear
            if extremeness >= 0.8:
                strength = SignalStrength.VERY_STRONG
                confidence = 0.85
            elif extremeness >= 0.5:
                strength = SignalStrength.STRONG
                confidence = 0.75
            else:
                strength = SignalStrength.MODERATE
                confidence = 0.65
            logger.info(
                f"Extreme fear detected: score={sentiment_score:.1f} → Contrarian BULLISH signal"
            )

        elif sentiment_score >= self.extreme_greed:
            direction = SignalDirection.BEARISH
            signal_type = SignalType.SENTIMENT_SHIFT
            extremeness = (sentiment_score - self.extreme_greed) / (100 - self.extreme_greed)
            if extremeness >= 0.8:
                strength = SignalStrength.VERY_STRONG
                confidence = 0.85
            elif extremeness >= 0.5:
                strength = SignalStrength.STRONG
                confidence = 0.75
            else:
                strength = SignalStrength.MODERATE
                confidence = 0.65
            logger.info(
                f"Extreme greed detected: score={sentiment_score:.1f} → Contrarian BEARISH signal"
            )

        elif sentiment_score < 45:
            direction = SignalDirection.BULLISH
            signal_type = SignalType.SENTIMENT_SHIFT
            strength = SignalStrength.WEAK
            confidence = 0.55

        elif sentiment_score > 55:
            direction = SignalDirection.BEARISH
            signal_type = SignalType.SENTIMENT_SHIFT
            strength = SignalStrength.WEAK
            confidence = 0.55

        else:
            return None

        if confidence < self.min_confidence:
            return None

        signal = TradingSignal(
            timestamp=now,
            source=self.name,
            signal_type=signal_type,
            direction=direction,
            strength=strength,
            confidence=confidence,
            current_price=current_price,
            signal_id=self._next_signal_id(decision_id),
            metadata={
                "sentiment_score": sentiment_score,
                "sentiment_classification": metadata.get('sentiment_classification', 'unknown'),
            }
        )
        self._record_signal(signal)
        logger.debug(
            f"Generated sentiment signal: {direction.value}, score={signal.score:.1f}"
        )
        return signal
