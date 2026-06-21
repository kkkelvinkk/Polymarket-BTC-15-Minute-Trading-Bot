"""
Beta-6 — Effective Decision Config

Builds a single dict capturing every config value the decision body
consumes (env reads, literal constants, per-processor params, fusion
weights, risk-engine limits). The dict is consumed by the raw decision
snapshot recorder for the §4.3 "effective_config" block AND read by
the production gates so they see one canonical source of truth.

Contract:
  - ``build_effective_decision_config`` is called EXACTLY ONCE per
    body invocation, AFTER ``observation_only`` is resolved, BEFORE
    any gate fires (RP9 / TC57 enforce).
  - The returned dict is JSON-serialisable, fully sorted (CSV-
    deterministic), and contains NO live process state (no positions,
    no current PnL — only configuration).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

# Avoid hard imports of production classes here — only structural
# accessors are required.


@dataclass(frozen=True)
class ProcessorRegistry:
    """Beta-6 frozen registry; one attribute per processor class."""

    spike: Any
    sentiment: Any
    divergence: Any
    orderbook: Any
    tick_velocity: Any
    deribit_pcr: Any


def build_effective_decision_config(
    env_reader: Callable[[str], str],
    processor_registry: ProcessorRegistry,
    fusion_engine: Any,
    risk_engine: Any,
) -> Dict[str, Any]:
    """Build the §4.3 effective_config dict.

    All values are deterministic functions of construction-time state.
    No wall-clock reads; no mutations.
    """

    # --- Per-processor effective_params() (Beta-4) ---
    processor_params: Dict[str, Any] = {
        "spike":         processor_registry.spike.effective_params(),
        "sentiment":     processor_registry.sentiment.effective_params(),
        "divergence":    processor_registry.divergence.effective_params(),
        "orderbook":     processor_registry.orderbook.effective_params(),
        "tick_velocity": processor_registry.tick_velocity.effective_params(),
        "deribit_pcr":   processor_registry.deribit_pcr.effective_params(),
    }

    # --- Fusion engine ---
    # Review-cycle fix: fusion_min_signals/fusion_min_score are §12 promoted
    # envs (FUSION_MIN_SIGNALS / FUSION_MIN_SCORE); reading hardcoded literals
    # would prevent the harness from sweeping them.
    fusion_block = {
        "fusion_weights_by_source": dict(fusion_engine.weights),
        "fusion_recency_window_seconds": int(fusion_engine.recency_window_seconds),
        "fusion_min_signals": int(env_reader("FUSION_MIN_SIGNALS")),
        "fusion_min_score": float(env_reader("FUSION_MIN_SCORE")),
    }

    # --- Risk engine ---
    risk_block = {
        "max_position_size": str(risk_engine.limits.max_position_size),
        "max_total_exposure": str(risk_engine.limits.max_total_exposure),
        "max_positions": int(risk_engine.limits.max_positions),
        "max_drawdown_pct": float(risk_engine.limits.max_drawdown_pct),
        "max_loss_per_day": str(risk_engine.limits.max_loss_per_day),
        "max_leverage": float(risk_engine.limits.max_leverage),
    }

    # --- Promoted-env constants consumed inside _make_trading_decision_body ---
    # Beta-10: liquidity_floor moves from a literal at bot.py to a
    # promoted env LIQUIDITY_FLOOR; the production gate reads from this
    # config rather than the inline literal.
    promoted_env = {
        "liquidity_floor": str(Decimal(env_reader("LIQUIDITY_FLOOR"))),
        # Replay-determinism windows (also surfaced by per-processor
        # effective_params, but mirrored here for the recorder's
        # §4.3 "promoted-env" sub-block).
        "fusion_recency_window_seconds": int(
            env_reader("FUSION_RECENCY_WINDOW_SECONDS")
        ),
        "divergence_spot_history_max_len": int(
            env_reader("DIVERGENCE_SPOT_HISTORY_MAX_LEN")
        ),
        "tick_velocity_tolerance_seconds": int(
            env_reader("TICK_VELOCITY_TOLERANCE_SECONDS")
        ),
    }

    # --- Trend thresholds: §12 promoted envs (TREND_UP_THRESHOLD /
    # TREND_DOWN_THRESHOLD). Review-cycle fix replaced the hardcoded
    # 0.60 / 0.40 literals with env reads so the harness can sweep them.
    trend_block = {
        "trend_up_threshold": float(env_reader("TREND_UP_THRESHOLD")),
        "trend_down_threshold": float(env_reader("TREND_DOWN_THRESHOLD")),
    }

    out: Dict[str, Any] = {
        "processors": processor_params,
        "fusion": fusion_block,
        "risk_engine": risk_block,
        "promoted_env": promoted_env,
        "trend": trend_block,
    }
    # Deterministic key ordering for downstream canonical hashing.
    return _sort_keys_deep(out)


def _sort_keys_deep(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sort_keys_deep(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_sort_keys_deep(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sort_keys_deep(v) for v in obj)
    return obj
