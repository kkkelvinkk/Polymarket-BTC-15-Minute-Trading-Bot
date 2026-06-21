"""Path B decision-log calibration join helpers."""

from __future__ import annotations

import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from analysis.gamma_resolution import (
    GAMMA_MARKETS_URL,
    WINNING_PRICE,
    fetch_market_by_slug,
    load_decision_records,
    parse_finite_decimal as _decimal,
    winning_side as _winning_side,
)

__all__ = [
    "GAMMA_MARKETS_URL",
    "WINNING_PRICE",
    "GammaResolutionResolver",
    "analyze_path_b",
    "fetch_gamma_market_by_slug",
    "load_decision_records",
]


def fetch_gamma_market_by_slug(client: httpx.Client, slug: str) -> dict[str, Any] | None:
    """Calibration caller uses the ``closed-only`` Gamma filter."""
    return fetch_market_by_slug(client, slug, closed_only=True)


class GammaResolutionResolver:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._markets_by_slug: dict[str, dict[str, Any] | None] = {}

    def market_for_slug(self, slug: str) -> dict[str, Any] | None:
        if slug not in self._markets_by_slug:
            self._markets_by_slug[slug] = fetch_gamma_market_by_slug(self._client, slug)
        return self._markets_by_slug[slug]


def _fused_direction_won(record: dict[str, Any], winner: str, decision_id: str) -> int:
    fused_direction_raw = record.get("fused_direction")
    fused_direction = str(fused_direction_raw).strip().lower()
    if fused_direction == "bullish":
        predicted_side = "long"
    elif fused_direction == "bearish":
        predicted_side = "short"
    else:
        raise ValueError(
            f"decision {decision_id} has unsupported fused_direction={fused_direction_raw!r}"
        )
    return 1 if predicted_side == winner else 0


def _wilson_lower_bound_95(wins: int, n: int) -> float:
    if n <= 0:
        raise ValueError("Wilson lower bound requires at least one sample")
    z = 1.959963984540054
    p_hat = wins / n
    denom = 1 + z * z / n
    centre = p_hat + z * z / (2 * n)
    radius = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    return (centre - radius) / denom


def _brier_score(samples: list[tuple[float, int]]) -> float:
    if not samples:
        raise ValueError("Brier score requires at least one sample")
    return sum((conf - outcome) ** 2 for conf, outcome in samples) / len(samples)


def _log_loss(samples: list[tuple[float, int]]) -> float:
    if not samples:
        raise ValueError("log loss requires at least one sample")
    eps = 1e-12
    total = 0.0
    for conf, outcome in samples:
        c = min(max(conf, eps), 1 - eps)
        total += -(outcome * math.log(c) + (1 - outcome) * math.log(1 - c))
    return total / len(samples)


def _bucket_key(confidence: float) -> float:
    return round(confidence * 10) / 10


def analyze_path_b(
    decision_records: list[dict[str, Any]],
    resolver: GammaResolutionResolver,
    fee_buffer: Decimal,
    spread_buffer: Decimal,
) -> dict[str, Any]:
    """Analyze decisions.jsonl joined to Gamma resolutions."""
    buckets: dict[float, dict[str, Any]] = defaultdict(
        lambda: {
            "outcome_wins": 0,
            "outcome_losses": 0,
            "decisions": 0,
            "samples": [],
            "entry_samples": 0,
            "entry_outcome_wins": 0,
            "entry_outcome_losses": 0,
            "sum_entry_price_weighted": Decimal("0"),
            "sum_entry_weight": Decimal("0"),
        }
    )
    excluded = {
        "missing_market_slug": 0,
        "missing_fused_signal": 0,
        "pending_market": 0,
        "missing_entry_metrics": 0,
    }

    for index, record in enumerate(decision_records, 1):
        # §3.A row 16 / §3.D guard-rail #2: decision_id is foundational
        # state; missing → fail-stop, not substitute a synthetic id.
        if "decision_id" not in record:
            raise ValueError(
                f"decision record #{index} is missing required field "
                "'decision_id'"
            )
        decision_id = str(record["decision_id"])
        slug = record.get("slug")
        if slug in (None, ""):
            excluded["missing_market_slug"] += 1
            continue
        fused_confidence_raw = record.get("fused_confidence")
        fused_direction_raw = record.get("fused_direction")
        if fused_confidence_raw in (None, "") or fused_direction_raw in (None, ""):
            excluded["missing_fused_signal"] += 1
            continue

        market = resolver.market_for_slug(str(slug))
        if market is None:
            excluded["pending_market"] += 1
            continue
        winner = _winning_side(market)
        if winner is None:
            excluded["pending_market"] += 1
            continue

        confidence = float(fused_confidence_raw)
        if not math.isfinite(confidence) or confidence <= 0 or confidence >= 1:
            raise ValueError(
                f"decision {decision_id} fused_confidence must be in (0, 1), got {confidence!r}"
            )
        outcome = _fused_direction_won(record, winner, decision_id)
        bucket = _bucket_key(confidence)
        b = buckets[bucket]
        b["outcome_wins" if outcome else "outcome_losses"] += 1
        b["decisions"] += 1
        b["samples"].append((confidence, outcome))

        executable_entry = record.get("executable_entry")
        estimated_actual_cost = record.get("estimated_actual_cost")
        if executable_entry in (None, "") or estimated_actual_cost in (None, ""):
            excluded["missing_entry_metrics"] += 1
            continue
        entry = _decimal(executable_entry, "executable_entry")
        entry_weight = _decimal(estimated_actual_cost, "estimated_actual_cost")
        if entry <= 0 or entry > 1:
            raise ValueError(
                f"decision {decision_id} executable_entry must be in (0, 1], got {entry}"
            )
        if entry_weight <= 0:
            raise ValueError(
                f"decision {decision_id} estimated_actual_cost must be positive, got {entry_weight}"
            )
        b["entry_samples"] += 1
        b["entry_outcome_wins" if outcome else "entry_outcome_losses"] += 1
        b["sum_entry_price_weighted"] += entry * entry_weight
        b["sum_entry_weight"] += entry_weight

    summary = {}
    for bucket, b in buckets.items():
        n = b["decisions"]
        if n == 0:
            continue
        win_rate = b["outcome_wins"] / n
        wilson_lo = _wilson_lower_bound_95(b["outcome_wins"], n)
        entry_win_rate = None
        entry_wilson_lo = None
        weighted_entry = None
        probability_edge = None
        wilson_edge_after_buffers = None
        if b["sum_entry_weight"] > 0:
            entry_n = b["entry_samples"]
            entry_win_rate = b["entry_outcome_wins"] / entry_n
            entry_wilson_lo = _wilson_lower_bound_95(b["entry_outcome_wins"], entry_n)
            weighted_entry = b["sum_entry_price_weighted"] / b["sum_entry_weight"]
            probability_edge = Decimal(str(entry_win_rate)) - weighted_entry
            wilson_edge_after_buffers = (
                Decimal(str(entry_wilson_lo)) - weighted_entry - fee_buffer - spread_buffer
            )
        summary[bucket] = {
            "n": n,
            "win_rate": win_rate,
            "wilson_lower_bound_95": wilson_lo,
            "entry_samples": b["entry_samples"],
            "entry_win_rate": entry_win_rate,
            "entry_wilson_lower_bound_95": entry_wilson_lo,
            "weighted_avg_entry_price": None if weighted_entry is None else float(weighted_entry),
            "probability_edge": None if probability_edge is None else float(probability_edge),
            "wilson_edge_after_buffers": (
                None if wilson_edge_after_buffers is None else float(wilson_edge_after_buffers)
            ),
            "brier": _brier_score(b["samples"]),
            "log_loss": _log_loss(b["samples"]),
        }

    return {
        "buckets": summary,
        "excluded_records": excluded,
        "total_decision_records": len(decision_records),
        "resolved_calibration_records": sum(stats["n"] for stats in summary.values()),
    }
