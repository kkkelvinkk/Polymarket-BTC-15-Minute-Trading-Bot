import types
import unittest
from decimal import Decimal

from monitoring import grafana_exporter


class _Gauge:
    def __init__(self):
        self.values = []

    def set(self, value):
        self.values.append(value)


class _Logger:
    def __init__(self):
        self.debug_messages = []
        self.error_messages = []

    def debug(self, message):
        self.debug_messages.append(message)

    def error(self, message):
        self.error_messages.append(message)


class _Performance:
    current_capital = Decimal("100")

    def calculate_metrics(self):
        return types.SimpleNamespace(
            total_pnl=Decimal("0"),
            roi=0,
            win_rate=0,
            sharpe_ratio=0,
            max_drawdown=0,
            open_positions=0,
            total_exposure=Decimal("0"),
            avg_signal_score=0,
            avg_signal_confidence=0,
        )


class _FailingPerformance:
    current_capital = Decimal("100")

    def calculate_metrics(self):
        raise RuntimeError("metric source unavailable")


class _Risk:
    def get_risk_summary(self):
        return {"exposure": {"utilization_pct": 0}}


class _Execution:
    def get_statistics(self):
        return {}


def _exporter(performance):
    exporter = object.__new__(grafana_exporter.GrafanaMetricsExporter)
    exporter.performance = performance
    exporter.risk = _Risk()
    exporter.execution = _Execution()
    for name in (
        "total_pnl",
        "roi",
        "win_rate",
        "sharpe_ratio",
        "max_drawdown",
        "open_positions",
        "total_exposure",
        "avg_signal_score",
        "avg_signal_confidence",
        "current_capital",
        "risk_utilization",
    ):
        setattr(exporter, name, _Gauge())
    return exporter


class GrafanaExporterLoggingTests(unittest.TestCase):
    def test_update_metrics_success_is_quiet(self):
        logger = _Logger()
        original_logger = grafana_exporter.logger
        grafana_exporter.logger = logger
        try:
            _exporter(_Performance()).update_metrics()
        finally:
            grafana_exporter.logger = original_logger

        self.assertEqual(logger.debug_messages, [])
        self.assertEqual(logger.error_messages, [])

    def test_update_metrics_failure_logs_error(self):
        logger = _Logger()
        original_logger = grafana_exporter.logger
        grafana_exporter.logger = logger
        try:
            _exporter(_FailingPerformance()).update_metrics()
        finally:
            grafana_exporter.logger = original_logger

        self.assertEqual(logger.debug_messages, [])
        self.assertEqual(
            logger.error_messages,
            ["Error updating metrics: metric source unavailable"],
        )


if __name__ == "__main__":
    unittest.main()
