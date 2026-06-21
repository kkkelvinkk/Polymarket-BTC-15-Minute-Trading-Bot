"""
Test environment bootstrap.

The §12 "promoted-constant" envs are REQUIRED at startup in production.
For pytest runs, this conftest pre-populates them via
``os.environ.setdefault`` ONLY when they are not already set in the
test invoker's environment, so test sessions do not have to re-export
every env per test module. The production code path
(``int(os.environ["X"])`` etc.) still raises ``KeyError`` when an
operator forgets to set the env in a real deploy; this conftest is
loaded ONLY when pytest collects tests, never by production code.

Review-cycle disposition: this is a test-fixture bootstrap (the
``setdefault`` only fires when the operator has not already exported
the env), not a Rule-1 fallback — there is no production code path
through this module.
"""
from __future__ import annotations

import os

_TEST_ENV_DEFAULTS = {
    # Beta-5 — fusion engine recency window
    "FUSION_RECENCY_WINDOW_SECONDS": "300",
    # Beta-4 — divergence processor spot-history max length
    "DIVERGENCE_SPOT_HISTORY_MAX_LEN": "100",
    # Beta-4 — tick-velocity processor tolerance window (seconds)
    "TICK_VELOCITY_TOLERANCE_SECONDS": "5",
    # Beta-8 — operator acknowledgement gate for the UTC daily-reset boundary
    "POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET": "1",
    # Alpha-2 / §4.2 — git SHA required for the raw snapshot provenance block
    "POLYBOT_GIT_SHA": "test-sha-conftest",
    # Beta-10 / §12 — promoted liquidity floor
    "LIQUIDITY_FLOOR": "0.02",
    # Beta-8 / §12 — risk-engine promoted-constant envs.
    "MAX_POSITION_SIZE": "5.51",
    "MAX_TOTAL_EXPOSURE": "10.0",
    "MAX_POSITIONS": "5",
    "MAX_DRAWDOWN_PCT": "0.15",
    "MAX_LOSS_PER_DAY": "5.0",
    # §12 — fusion / trend promoted envs (review-cycle fix: were inline
    # literals in bot.py, now driven by env so harness sweeps work).
    "FUSION_MIN_SIGNALS": "2",
    "FUSION_MIN_SCORE": "55.0",
    "TREND_UP_THRESHOLD": "0.60",
    "TREND_DOWN_THRESHOLD": "0.40",
}

for _k, _v in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)
