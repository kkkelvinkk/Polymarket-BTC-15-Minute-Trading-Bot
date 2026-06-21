"""
Learning Engine — read-only signal performance analysis.

Beta-5 changes:
  - ``optimize_weights`` deleted (the only ``set_weight`` callsite outside
    ``SignalFusionEngine.__init__``). Automated weight tuning is deferred
    to a follow-up plan per §12.
  - Module-level ``get_learning_engine`` singleton + ``_learning_engine_instance``
    deleted. Construction is via ``LearningEngine(fusion_engine=...)`` from
    the bot's startup site (single source of truth).
  - ``fusion_engine`` is a REQUIRED constructor kwarg (M11; no default,
    no sentinel-fallback).
"""
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
from dataclasses import dataclass
from loguru import logger

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from monitoring.performance_tracker import get_performance_tracker, Trade
from core.strategy_brain.fusion_engine.signal_fusion import SignalFusionEngine


@dataclass
class SignalPerformance:
    """Performance metrics for a signal source."""
    source_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_pnl: Decimal
    total_pnl: Decimal
    avg_confidence: float
    avg_score: float
    last_updated: datetime


class LearningEngine:
    """
    Learning engine that analyzes signal source performance.

    Beta-5 removed automated weight mutation; this surface is now
    read-only / analysis-only.
    """

    def __init__(
        self,
        *,
        fusion_engine: SignalFusionEngine,
        learning_rate: float = 0.1,
        min_trades_for_learning: int = 10,
    ):
        if not isinstance(fusion_engine, SignalFusionEngine):
            raise TypeError(
                "LearningEngine: fusion_engine must be a SignalFusionEngine instance"
            )
        self.learning_rate = learning_rate
        self.min_trades = min_trades_for_learning

        self.performance = get_performance_tracker()
        self.fusion = fusion_engine

        self._signal_performance: Dict[str, SignalPerformance] = {}

        logger.info(
            f"Initialized Learning Engine "
            f"(learning_rate={learning_rate}, min_trades={min_trades_for_learning})"
        )

    def analyze_signal_performance(
        self,
        lookback_days: int = 7,
    ) -> Dict[str, SignalPerformance]:
        """Analyze performance of each signal source."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        trades = self.performance.get_trade_history(
            limit=1000,
            start_date=cutoff,
        )

        source_trades: Dict[str, List[Trade]] = {}
        for trade in trades:
            sources = trade.metadata.get("signal_sources", [])
            for source in sources:
                if source not in source_trades:
                    source_trades[source] = []
                source_trades[source].append(trade)

        performances: Dict[str, SignalPerformance] = {}
        for source, source_trade_list in source_trades.items():
            wins = [t for t in source_trade_list if t.pnl > 0]
            losses = [t for t in source_trade_list if t.pnl < 0]

            total = len(source_trade_list)
            win_count = len(wins)
            loss_count = len(losses)

            win_rate = win_count / total if total > 0 else 0.0
            avg_pnl = (
                sum(t.pnl for t in source_trade_list) / total
                if total > 0 else Decimal("0")
            )
            total_pnl = sum(t.pnl for t in source_trade_list)
            avg_conf = (
                sum(t.signal_confidence for t in source_trade_list) / total
                if total > 0 else 0.0
            )
            avg_score = (
                sum(t.signal_score for t in source_trade_list) / total
                if total > 0 else 0.0
            )

            perf = SignalPerformance(
                source_name=source,
                total_trades=total,
                winning_trades=win_count,
                losing_trades=loss_count,
                win_rate=win_rate,
                avg_pnl=avg_pnl,
                total_pnl=total_pnl,
                avg_confidence=avg_conf,
                avg_score=avg_score,
                last_updated=datetime.now(timezone.utc),
            )
            performances[source] = perf
            self._signal_performance[source] = perf

        logger.info(
            f"Analyzed performance for {len(performances)} signal sources"
        )
        return performances

    def get_signal_rankings(self) -> List[Dict[str, Any]]:
        """Get signals ranked by performance."""
        rankings: List[Dict[str, Any]] = []
        for source, perf in self._signal_performance.items():
            rankings.append({
                "source": source,
                "win_rate": perf.win_rate,
                "total_pnl": float(perf.total_pnl),
                "avg_pnl": float(perf.avg_pnl),
                "total_trades": perf.total_trades,
                "current_weight": self.fusion.weights.get(source, 0.0),
            })
        rankings.sort(key=lambda x: x["total_pnl"], reverse=True)
        return rankings

    def export_insights(self) -> Dict[str, Any]:
        """Export learning insights."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_performance": {
                source: {
                    "win_rate": perf.win_rate,
                    "total_pnl": float(perf.total_pnl),
                    "total_trades": perf.total_trades,
                    "current_weight": self.fusion.weights.get(source, 0.0),
                }
                for source, perf in self._signal_performance.items()
            },
            "signal_rankings": self.get_signal_rankings(),
        }
