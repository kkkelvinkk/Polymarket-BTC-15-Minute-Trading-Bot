import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from execution.risk_engine import RiskEngine


class RiskEngineEnvTests(unittest.TestCase):
    def test_env_controls_position_limits(self):
        original = {
            key: os.environ.get(key)
            for key in (
                "MAX_POSITION_SIZE",
                "MAX_TOTAL_EXPOSURE",
                "MAX_POSITIONS",
                "MAX_DRAWDOWN_PCT",
                "MAX_LOSS_PER_DAY",
            )
        }
        try:
            os.environ["MAX_POSITION_SIZE"] = "5.50"
            os.environ["MAX_TOTAL_EXPOSURE"] = "20.00"
            os.environ["MAX_POSITIONS"] = "3"
            os.environ["MAX_DRAWDOWN_PCT"] = "0.20"
            os.environ["MAX_LOSS_PER_DAY"] = "8.00"

            # Beta-8: now= is a REQUIRED constructor kwarg (M11).
            now = datetime(2026, 5, 24, tzinfo=timezone.utc)
            risk = RiskEngine(now=now)

            self.assertEqual(risk.limits.max_position_size, Decimal("5.50"))
            self.assertEqual(risk.limits.max_total_exposure, Decimal("20.00"))
            self.assertEqual(risk.limits.max_positions, 3)
            self.assertEqual(risk.limits.max_drawdown_pct, 0.20)
            self.assertEqual(risk.limits.max_loss_per_day, Decimal("8.00"))

            is_valid, error = risk.validate_new_position(
                size=Decimal("5.50"),
                direction="long",
                current_price=Decimal("0.8550"),
                now=now,
            )

            self.assertTrue(is_valid, error)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
