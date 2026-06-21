"""
Base Signal Processor
Abstract interface for all signal processors.

Beta-2 / Beta-3 contract:
  - ``process(..., *, now: datetime, decision_id: str)`` REQUIRED kwargs
    on every subclass (M11; no defaults).
  - ``__init__(*, name: str, ...)`` REQUIRED kwarg on every subclass.
  - ``TradingSignal`` gains ``signal_id: str`` (required) per Beta-3
    canonical scheme ``f"{decision_id}:{processor_name}:{ordinal}"``.
  - Each processor maintains ``self._signal_ordinal`` reset to 0 at
    the first line of every ``process()`` call and post-incremented
    after each emitted ``TradingSignal``.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, List
from enum import Enum


class SignalType(Enum):
    """Types of trading signals."""
    SPIKE_DETECTED = "spike_detected"
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM = "momentum"
    SENTIMENT_SHIFT = "sentiment_shift"
    VOLUME_SURGE = "volume_surge"
    PRICE_DIVERGENCE = "price_divergence"
    ANOMALY = "anomaly"


class SignalStrength(Enum):
    """Signal strength levels."""
    WEAK = 1
    MODERATE = 2
    STRONG = 3
    VERY_STRONG = 4


class SignalDirection(Enum):
    """Signal direction."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class TradingSignal:
    """
    Trading signal from a processor.
    """
    timestamp: datetime
    source: str
    signal_type: SignalType
    direction: SignalDirection
    strength: SignalStrength
    confidence: float
    current_price: Decimal
    signal_id: str

    target_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    @property
    def score(self) -> float:
        """Calculate signal score (0-100). Combines strength and confidence."""
        strength_weight = self.strength.value / 4.0
        return (strength_weight * 0.5 + self.confidence * 0.5) * 100


class BaseSignalProcessor(ABC):
    """
    Base class for all signal processors.

    Subclasses MUST accept ``name`` as a REQUIRED kwarg in their own
    ``__init__`` and forward it to ``super().__init__(name)``.
    """

    def __init__(self, name: str):
        self.name = name
        self._enabled = True
        self._signals_generated = 0
        self._last_signal: Optional[TradingSignal] = None
        # Beta-3: per-instance, per-call ordinal for signal_id construction.
        self._signal_ordinal: int = 0
        # §3.D drop counters local to this processor (recorder queries
        # via ``last_drop_counters()`` and merges into the snapshot's
        # top-level ``drop_counters`` block at __exit__).
        self._drop_counters: Dict[str, int] = {}

    @abstractmethod
    def process(
        self,
        current_price: Decimal,
        historical_prices: list,
        metadata: Dict[str, Any],
        *,
        now: datetime,
        decision_id: str,
    ) -> Optional[TradingSignal]:
        """
        Process market data and generate signal if conditions met.

        Beta-2 / Beta-3: ``now`` and ``decision_id`` are REQUIRED kwargs
        (M11; no defaults).
        """
        raise NotImplementedError

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def signals_generated(self) -> int:
        return self._signals_generated

    def _reset_signal_ordinal(self) -> None:
        """Beta-3: call at first line of every ``process()``."""
        self._signal_ordinal = 0

    def _next_signal_id(self, decision_id: str) -> str:
        """
        Beta-3: build the canonical signal_id and post-increment the
        ordinal. Call BEFORE constructing each TradingSignal.
        """
        sid = f"{decision_id}:{self.name}:{self._signal_ordinal}"
        self._signal_ordinal += 1
        return sid

    def _record_signal(self, signal: TradingSignal) -> None:
        self._signals_generated += 1
        self._last_signal = signal

    def _increment_drop(self, drop_class: str) -> None:
        """§3.D drop counter increment; closed-enum class names."""
        self._drop_counters[drop_class] = self._drop_counters.get(drop_class, 0) + 1

    def last_drop_counters(self) -> Dict[str, int]:
        """Recorder reads this at end-of-body to merge into snapshot."""
        return dict(self._drop_counters)

    def reset_drop_counters(self) -> None:
        """Recorder calls this at start-of-body so counters are per-decision."""
        self._drop_counters = {}

    def effective_params(self) -> Dict[str, Any]:
        """
        Beta-4: return every adjustable parameter for this processor as
        a sorted dict (CSV-deterministic). Subclasses override.
        """
        return {"name": self.name}

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self._enabled,
            "signals_generated": self._signals_generated,
            "last_signal": (
                {
                    "timestamp": self._last_signal.timestamp.isoformat(),
                    "type": self._last_signal.signal_type.value,
                    "direction": self._last_signal.direction.value,
                    "score": self._last_signal.score,
                }
                if self._last_signal else None
            ),
        }
