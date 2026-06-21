"""
Signal Fusion Engine
Combines multiple signals with weighted voting.

Beta-5 contract:
  - ``weights`` and ``recency_window_seconds`` are REQUIRED constructor
    keyword arguments (no defaults; M11).
  - ``fuse_signals(now=...)`` is REQUIRED keyword (no default; M11).
  - ``set_weight()`` is callable ONLY from ``SignalFusionEngine.__init__``
    (TC61b static check); module ``_check_setweight_caller`` enforces
    at runtime.
  - Unknown ``signal.source`` lookups are DROPPED per §3.D
    (``unknown_signal_source_dropped`` counter exposed via
    ``last_fusion_diagnostics()``); no ``"default"`` weight is read.
  - ``last_fusion_diagnostics()`` returns the §4.2 fusion block fields
    (call_inputs filled by the recorder from effective_config).
"""
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from loguru import logger

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from core.strategy_brain.signal_processors.base_processor import (
    TradingSignal,
    SignalDirection,
    SignalStrength,
)


@dataclass
class FusedSignal:
    timestamp: datetime
    direction: SignalDirection
    confidence: float
    score: float

    signals: List[TradingSignal]
    weights: Dict[str, float]
    metadata: Dict[str, Any]

    @property
    def num_signals(self) -> int:
        return len(self.signals)

    @property
    def is_strong(self) -> bool:
        return self.score >= 70.0

    @property
    def is_actionable(self) -> bool:
        return self.score >= 60.0 and self.confidence >= 0.6


class SignalFusionEngine:
    def __init__(
        self,
        *,
        weights: Dict[str, float],
        recency_window_seconds: int,
    ):
        if not isinstance(weights, dict) or not weights:
            raise ValueError("SignalFusionEngine: weights must be a non-empty dict")
        if not isinstance(recency_window_seconds, int) or recency_window_seconds <= 0:
            raise ValueError(
                "SignalFusionEngine: recency_window_seconds must be a positive int"
            )

        self.weights: Dict[str, float] = {}
        for source, weight in weights.items():
            self._set_weight_internal(source, float(weight))
        self.recency_window_seconds = recency_window_seconds

        self._signal_history: List[FusedSignal] = []
        self._max_history = 100
        self._fusions_performed = 0

        self._last_diagnostics: Dict[str, Any] = {}
        self._reset_diagnostics()

        logger.info("Initialized Signal Fusion Engine")
        logger.info(
            f"Weights: {self.weights}; recency_window_seconds={recency_window_seconds}"
        )

    def _set_weight_internal(self, processor_name: str, weight: float) -> None:
        if not 0.0 <= weight <= 1.0:
            raise ValueError("Weight must be between 0.0 and 1.0")
        self.weights[processor_name] = weight

    def set_weight(self, processor_name: str, weight: float) -> None:
        """
        Beta-5 invariant: NOT callable outside ``__init__``. TC61b is the
        static check; this runtime guard ensures even a missed grep
        cannot mutate weights mid-decision.
        """
        raise RuntimeError(
            "SignalFusionEngine.set_weight() is not callable post-construction; "
            "weights are frozen after __init__ per Beta-5"
        )

    def _reset_diagnostics(self) -> None:
        self._last_diagnostics = {
            "outcome": None,
            "early_return_reason": None,
            "signals_in": 0,
            "signals_recent": 0,
            "unknown_signal_source_dropped": 0,
            "unknown_signal_source_names": [],
            "bullish_contrib": 0.0,
            "bearish_contrib": 0.0,
            "total_contrib": 0.0,
            "num_bullish": 0,
            "num_bearish": 0,
            "consensus_score": 0.0,
            "avg_confidence": 0.0,
            "direction": None,
            "min_signals": None,
            "min_score": None,
            "now": None,
            "recency_window_seconds": self.recency_window_seconds,
        }

    def last_fusion_diagnostics(self) -> Dict[str, Any]:
        return dict(self._last_diagnostics)

    def fuse_signals(
        self,
        signals: List[TradingSignal],
        *,
        now: datetime,
        min_signals: int = 1,
        min_score: float = 50.0,
    ) -> Optional[FusedSignal]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("fuse_signals: now must be timezone-aware (UTC)")

        self._reset_diagnostics()
        self._last_diagnostics["min_signals"] = min_signals
        self._last_diagnostics["min_score"] = min_score
        self._last_diagnostics["now"] = now
        self._last_diagnostics["signals_in"] = len(signals)

        if not signals:
            logger.debug("No signals to fuse")
            self._last_diagnostics["outcome"] = "early_return_none"
            self._last_diagnostics["early_return_reason"] = "no_signals"
            return None

        if len(signals) < min_signals:
            logger.debug(f"Not enough signals: {len(signals)} < {min_signals}")
            self._last_diagnostics["outcome"] = "early_return_none"
            self._last_diagnostics["early_return_reason"] = "below_min_signals_total"
            return None

        recent_signals = [
            s for s in signals
            if (now - s.timestamp) < timedelta(seconds=self.recency_window_seconds)
        ]
        self._last_diagnostics["signals_recent"] = len(recent_signals)

        if len(recent_signals) < min_signals:
            logger.debug(f"Not enough recent signals: {len(recent_signals)}")
            self._last_diagnostics["outcome"] = "early_return_none"
            self._last_diagnostics["early_return_reason"] = "below_min_signals_recent"
            return None

        bullish_contrib = 0.0
        bearish_contrib = 0.0
        num_bullish = 0
        num_bearish = 0
        kept_signals: List[TradingSignal] = []

        for signal in recent_signals:
            weight = self.weights.get(signal.source)
            if weight is None:
                # §3.D DROP — unknown source; counted, not substituted.
                self._last_diagnostics["unknown_signal_source_dropped"] += 1
                self._last_diagnostics["unknown_signal_source_names"].append(
                    signal.source
                )
                logger.warning(
                    f"DROP unknown signal source: {signal.source!r} "
                    "(no weight in PRODUCTION_DEFAULT_WEIGHTS)"
                )
                continue

            strength_val = signal.strength.value if signal.strength else 2
            strength_factor = strength_val / 4.0
            conf = min(1.0, max(0.0, signal.confidence))
            contribution = weight * conf * strength_factor

            logger.debug(
                f"Signal {signal.source}: dir={signal.direction}, "
                f"strength={signal.strength.name if signal.strength else 'MISSING'}, "
                f"conf={conf:.3f}, weight={weight:.2f}, "
                f"str_factor={strength_factor:.3f}, contrib={contribution:.6f}"
            )

            direction_str = str(signal.direction).upper()
            if "BULLISH" in direction_str:
                bullish_contrib += contribution
                num_bullish += 1
                kept_signals.append(signal)
            elif "BEARISH" in direction_str:
                bearish_contrib += contribution
                num_bearish += 1
                kept_signals.append(signal)
            else:
                logger.warning(f"Ignored unknown direction: {signal.direction!r}")

        total_contrib = bullish_contrib + bearish_contrib
        self._last_diagnostics["bullish_contrib"] = bullish_contrib
        self._last_diagnostics["bearish_contrib"] = bearish_contrib
        self._last_diagnostics["total_contrib"] = total_contrib
        self._last_diagnostics["num_bullish"] = num_bullish
        self._last_diagnostics["num_bearish"] = num_bearish

        logger.debug(
            f"Final: bullish={bullish_contrib:.6f} | "
            f"bearish={bearish_contrib:.6f} | total={total_contrib:.6f}"
        )

        if total_contrib < 0.0001:
            logger.warning(
                f"Extremely weak total contribution: {total_contrib:.8f} → fusion skipped"
            )
            self._last_diagnostics["outcome"] = "early_return_none"
            self._last_diagnostics["early_return_reason"] = (
                "below_min_contrib_policy_filter"
            )
            return None

        if bullish_contrib >= bearish_contrib:
            direction = SignalDirection.BULLISH
            dominant = bullish_contrib
        else:
            direction = SignalDirection.BEARISH
            dominant = bearish_contrib

        consensus_score = (dominant / total_contrib) * 100 if total_contrib > 0 else 0.0
        avg_conf = (
            sum(s.confidence for s in kept_signals) / len(kept_signals)
            if kept_signals else 0.0
        )

        self._last_diagnostics["consensus_score"] = consensus_score
        self._last_diagnostics["avg_confidence"] = avg_conf
        self._last_diagnostics["direction"] = direction.value

        if consensus_score < min_score:
            logger.debug(
                f"Consensus score too low: {consensus_score:.1f} < {min_score}"
            )
            self._last_diagnostics["outcome"] = "early_return_none"
            self._last_diagnostics["early_return_reason"] = "below_min_score"
            return None

        fused = FusedSignal(
            timestamp=now,
            direction=direction,
            confidence=avg_conf,
            score=consensus_score,
            signals=kept_signals,
            weights=self.weights.copy(),
            metadata={
                "bullish_contrib": round(bullish_contrib, 4),
                "bearish_contrib": round(bearish_contrib, 4),
                "total_contrib": round(total_contrib, 4),
                "num_bullish": num_bullish,
                "num_bearish": num_bearish,
            }
        )

        self._fusions_performed += 1
        self._signal_history.append(fused)
        if len(self._signal_history) > self._max_history:
            self._signal_history.pop(0)

        self._last_diagnostics["outcome"] = "fused"
        logger.info(
            f"Fused {len(kept_signals)} signals → {direction} "
            f"(score={consensus_score:.1f}, conf={avg_conf:.1%})"
        )

        return fused

    def get_recent_fusions(self, limit: int = 10) -> List[FusedSignal]:
        return self._signal_history[-limit:]

    def get_statistics(self) -> Dict[str, Any]:
        if not self._signal_history:
            return {
                "total_fusions": self._fusions_performed,
                "recent_fusions": 0,
                "avg_score": 0.0,
                "avg_confidence": 0.0,
            }

        recent = self._signal_history[-20:]
        return {
            "total_fusions": self._fusions_performed,
            "recent_fusions": len(recent),
            "avg_score": sum(f.score for f in recent) / len(recent) if recent else 0.0,
            "avg_confidence": (
                sum(f.confidence for f in recent) / len(recent) if recent else 0.0
            ),
            "weights": self.weights.copy(),
        }
