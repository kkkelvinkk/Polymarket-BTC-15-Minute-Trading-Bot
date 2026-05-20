"""Path B decision-log calibration join helpers."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
WINNING_PRICE = Decimal("1")


def _decimal(value: Any, field_name: str) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return parsed


def _json_array(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON-encoded array string")
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return parsed


def load_decision_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stripped = line.strip()
            if stripped == "":
                raise ValueError(f"{path}:{line_number} is blank")
            record = json.loads(stripped)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            records.append(record)
    return records


def fetch_gamma_market_by_slug(client: httpx.Client, slug: str) -> dict[str, Any] | None:
    response = client.get(
        GAMMA_MARKETS_URL,
        params={"slug": slug, "closed": "true", "limit": 2},
    )
    response.raise_for_status()
    markets = response.json()
    if not isinstance(markets, list):
        raise ValueError("Gamma markets response is not a JSON array")
    exact_matches = [market for market in markets if market["slug"] == slug]
    if len(exact_matches) == 0:
        if markets:
            raise ValueError(
                f"Gamma returned no exact closed match for slug {slug!r} "
                f"among {len(markets)} candidate markets"
            )
        return None
    if len(exact_matches) != 1:
        raise ValueError(f"Gamma returned {len(exact_matches)} exact matches for slug {slug!r}")
    market = exact_matches[0]
    if not isinstance(market, dict):
        raise ValueError(f"Gamma market for slug {slug!r} is not a JSON object")
    return market


class GammaResolutionResolver:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._markets_by_slug: dict[str, dict[str, Any] | None] = {}

    def market_for_slug(self, slug: str) -> dict[str, Any] | None:
        if slug not in self._markets_by_slug:
            self._markets_by_slug[slug] = fetch_gamma_market_by_slug(self._client, slug)
        return self._markets_by_slug[slug]


def _market_is_closed(market: dict[str, Any]) -> bool:
    closed = market["closed"]
    if isinstance(closed, bool):
        return closed
    raise ValueError(f"closed must be a boolean for {market['slug']}")


def _winning_side(market: dict[str, Any]) -> str | None:
    if not _market_is_closed(market):
        return None
    outcomes = _json_array(market["outcomes"], "outcomes")
    prices = _json_array(market["outcomePrices"], "outcomePrices")
    if len(outcomes) != len(prices):
        raise ValueError(f"outcomes/outcomePrices length mismatch for {market['slug']}")

    winners = []
    for outcome, price in zip(outcomes, prices):
        parsed_price = _decimal(price, "outcomePrices[]")
        if parsed_price == WINNING_PRICE:
            normalized = str(outcome).strip().lower()
            if normalized in ("yes", "up"):
                winners.append("long")
            elif normalized in ("no", "down"):
                winners.append("short")
            else:
                raise ValueError(f"unsupported winning outcome {outcome!r} for {market['slug']}")
    if len(winners) == 0:
        raise ValueError(f"closed market {market['slug']} has no winning outcome")
    if len(winners) != 1:
        raise ValueError(f"market {market['slug']} has {len(winners)} winners")
    return winners[0]


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
        decision_id = str(record.get("decision_id", f"record#{index}"))
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
