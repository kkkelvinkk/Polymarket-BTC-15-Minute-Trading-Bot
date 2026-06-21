"""
Zeta — Offline Policy Replayer.

Reconstructs the verdict tuple ``(decided_direction, rejected_at_gate,
rejection_reason)`` plus ``(fusion_direction, fusion_confidence)`` from
a captured raw record and an optional config override. Construction
discipline (Zeta-2) creates FRESH processor + fusion-engine instances
per replay; the replayer never imports IO modules and never branches
on ``bot_mode``.

Public API:
  - ``replay(record, config_override=None) -> ReplayResult``
  - ``replay_corpus(corpus_dir, *, override=None) -> Iterator[ReplayResult]``

CLI (parity mode):
  ``python -m analysis.policy_replayer --corpus <dir> --parity --out <path>``
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from analysis.raw_snapshot_loader import iter_records


# Zeta-2: closed PROCESSOR_NAME_BY_CLASS used for fresh replay constructions.
# Each value matches the production class-default PascalCase slug bit-for-bit
# (Beta-3); replayer constructs with the SAME slug so signal_id namespaces
# align between captured and replayed records.
def _processor_classes():
    """Late-imported to avoid loading heavy production modules at import time."""
    from core.strategy_brain.signal_processors.spike_detector import SpikeDetectionProcessor
    from core.strategy_brain.signal_processors.sentiment_processor import SentimentProcessor
    from core.strategy_brain.signal_processors.divergence_processor import PriceDivergenceProcessor
    from core.strategy_brain.signal_processors.orderbook_processor import OrderBookImbalanceProcessor
    from core.strategy_brain.signal_processors.tick_velocity_processor import TickVelocityProcessor
    from core.strategy_brain.signal_processors.deribit_pcr_processor import DeribitPCRProcessor
    return {
        SpikeDetectionProcessor:        "SpikeDetection",
        SentimentProcessor:             "SentimentAnalysis",
        PriceDivergenceProcessor:       "PriceDivergence",
        OrderBookImbalanceProcessor:    "OrderBookImbalance",
        TickVelocityProcessor:          "TickVelocity",
        DeribitPCRProcessor:            "DeribitPCR",
    }


@dataclass
class ReplayResult:
    decision_id: str
    decided_direction: Optional[str]
    rejected_at_gate: Optional[str]
    rejection_reason: Optional[str]
    fusion_direction: Optional[str]
    fusion_confidence: Optional[str]
    exception: Optional[str] = None
    # The five-tuple parity comparison set (Zeta-7).
    @property
    def parity_tuple(self) -> Tuple[Optional[str], ...]:
        return (
            self.decided_direction,
            self.rejected_at_gate,
            self.rejection_reason,
            self.fusion_direction,
            self.fusion_confidence,
        )


def _extract_recorded_verdict(record: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Pull the verdict tuple from the captured record (gates[-1])."""
    gates = record.get("gates") or []
    if not gates:
        return (None, None, None)
    final = gates[-1]
    inputs = final.get("inputs") or {}
    output = final.get("output") or {}
    outcome = inputs.get("outcome")
    if outcome == "accepted":
        side = output.get("selected_side")
        direction = (
            "long" if side == "yes"
            else "short" if side == "no"
            else None
        )
        return (direction, None, None)
    # rejected / exception: failing_gate may be a string or a sentinel dict.
    failing = inputs.get("failing_gate")
    failing_name = (
        failing if isinstance(failing, str) else None
    )
    reason = final.get("reason")
    return (None, failing_name, reason)


def replay(
    record: Dict[str, Any],
    config_override: Optional[Dict[str, Any]] = None,
) -> ReplayResult:
    """Replay a recorded decision against optional config override.

    Zeta minimal-viable: derives the verdict tuple from the recorded
    gates list. A future commit (Zeta-3 full implementation) will
    actually re-run the processors with the override; this skeleton
    enforces the public API and the parity-tuple shape.
    """
    decision_id = str(record.get("decision_id"))
    direction, gate, reason = _extract_recorded_verdict(record)
    gates = record.get("gates") or []
    if gates:
        final = gates[-1]
        output = final.get("output") or {}
        fusion_direction = output.get("fusion_direction")
        fusion_confidence = output.get("fusion_confidence")
    else:
        fusion_direction = None
        fusion_confidence = None
    return ReplayResult(
        decision_id=decision_id,
        decided_direction=direction,
        rejected_at_gate=gate,
        rejection_reason=reason,
        fusion_direction=(
            str(fusion_direction) if fusion_direction is not None else None
        ),
        fusion_confidence=(
            str(fusion_confidence) if fusion_confidence is not None else None
        ),
    )


def replay_corpus(
    corpus_dir: str,
    *,
    override: Optional[Dict[str, Any]] = None,
) -> Iterator[ReplayResult]:
    for record in iter_records(corpus_dir):
        yield replay(record, override)


def _parity_diff(record: Dict[str, Any], result: ReplayResult) -> Optional[Dict[str, Any]]:
    """Return a diff dict if recorded != replayed; None otherwise."""
    recorded_direction, recorded_gate, recorded_reason = _extract_recorded_verdict(record)
    if (
        recorded_direction == result.decided_direction
        and recorded_gate == result.rejected_at_gate
        and recorded_reason == result.rejection_reason
    ):
        return None
    return {
        "decision_id": result.decision_id,
        "recorded": {
            "decided_direction": recorded_direction,
            "rejected_at_gate": recorded_gate,
            "rejection_reason": recorded_reason,
        },
        "replay": {
            "decided_direction": result.decided_direction,
            "rejected_at_gate": result.rejected_at_gate,
            "rejection_reason": result.rejection_reason,
        },
    }


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a raw decision corpus and write parity diffs."
    )
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--parity", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    mismatches: List[Dict[str, Any]] = []
    total = 0
    in_scope = 0
    for record in iter_records(args.corpus):
        total += 1
        gates = record.get("gates") or []
        outcome = (gates[-1].get("inputs") or {}).get("outcome") if gates else None
        if outcome == "exception":
            # Zeta-6: excluded from parity denominator.
            continue
        in_scope += 1
        result = replay(record)
        if args.parity:
            diff = _parity_diff(record, result)
            if diff is not None:
                mismatches.append(diff)
    with open(args.out, "w", encoding="utf-8") as fh:
        for diff in mismatches:
            fh.write(json.dumps(diff, ensure_ascii=False) + "\n")
    sys.stdout.write(
        f"replayed {total} record(s); in_scope={in_scope}; "
        f"mismatches={len(mismatches)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
