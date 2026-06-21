"""Record dataclass + canonical JSON serializer for the raw recorder.

Split from ``raw_decision_snapshot.py`` per CLAUDE.md Rule 7 / plan §6.1
Alpha-1 "budget escape". The recorder module crossed the 500-line cap
once the §3.A/§3.D/§6.1 v22 dispositions landed (gate_scope discipline,
non-contiguous re-entry, scoped-gate-on-exception clearing, last-failed-
gate selection, idempotency guard, process-run-id reinitializer).

Splitting along ownership boundaries:

  * :mod:`raw_decision_snapshot_enums` — closed enumerations (data-only).
  * :mod:`raw_decision_snapshot_record` — record dataclass + serializer.
  * :mod:`raw_decision_snapshot`       — recorder context manager and
    public API (re-exports everything else).

This keeps the runtime path compact and lets the validator/loader in
Phase Delta import the record + serializer without pulling in the
recorder machinery.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from raw_decision_snapshot_enums import (
    GATE_NAME_VALUES,
    empty_drop_counters,
    empty_policy_filter_counters,
)


SCHEMA_VERSION: int = 1
SOURCE_REPO: str = "Polymarket-BTC-15-Minute-Trading-Bot"


# --------------------------------------------------------------------------- #
# Canonical JSON serializer                                                    #
# --------------------------------------------------------------------------- #


def json_default(obj: Any) -> Any:
    """JSON default hook for recorder writes.

    Decimal → string. UTC-aware datetime → ISO 8601 string. Naïve datetime
    raises ``TypeError`` (M9). Any callable / partial / lambda also raises
    (M11 default-value-expression guard).
    """
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        if obj.tzinfo is None or obj.utcoffset() is None:
            raise TypeError(
                "M9 violation: naïve datetime is not JSON serializable "
                f"({obj!r}); use timezone.utc"
            )
        return obj.isoformat()
    if callable(obj):
        raise TypeError(
            "M11 violation: callable (likely a default-value expression) "
            f"is not JSON serializable: {obj!r}"
        )
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, frozenset):
        return sorted(obj)
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")


def canonical_bytes(payload: Any) -> bytes:
    """Return the canonical sha256-input bytes for ``payload``.

    §5.5: ``sort_keys=True``, ``ensure_ascii=False``,
    ``separators=(",", ":")``. Used for sidecar hashes and the
    ``deribit_pcr.raw_option_summaries_hash`` field.
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# Dataclasses                                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class GateEntry:
    """One row in ``record.gates[]``. See §4.4."""

    name: str
    passed: bool
    reason: str
    inputs: Any = None
    output: Any = None

    def __post_init__(self) -> None:
        if self.name not in GATE_NAME_VALUES:
            raise ValueError(f"unknown gate name {self.name!r}")


@dataclass
class RawDecisionSnapshotRecord:
    """In-memory representation of one raw corpus line.

    Field set is intentionally compact at Phase Alpha — the full §4.2
    schema is filled in by Phase Gamma wiring. The fields here cover the
    "always-present" top-level invariants tested by TC04a / TC08 / TC09
    plus the universal-trailing ``final_decision`` row tested by TC83.

    The instance OWNS its mutable fields (``drop_counters``,
    ``policy_filter_counters``, ``gates``, ``signals``). Callers must NOT
    mutate the dicts/lists returned by :meth:`to_dict` — they alias
    internal state for performance. Deep-copy semantics for selected
    blocks (``risk_engine_state.positions``) are introduced in Phase
    Beta-9 per plan §4.2.
    """

    decision_id: str
    run_id: str
    bot_mode: str
    schema_version: int = SCHEMA_VERSION
    source_repo: str = SOURCE_REPO
    strategy_version: str = ""
    git_sha: str = ""
    captured_at: Optional[datetime] = None
    decision_reference_time: Optional[datetime] = None
    drop_counters: dict[str, int] = field(default_factory=empty_drop_counters)
    policy_filter_counters: dict[str, int] = field(
        default_factory=empty_policy_filter_counters
    )
    recorder_internal_failure: Optional[dict[str, Any]] = None
    gates: list[GateEntry] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Flatten the record to a JSON-ready dict.

        WARNING: every mutable container reference in the returned dict
        aliases internal state. Specifically:

        * ``drop_counters``, ``policy_filter_counters``,
          ``recorder_internal_failure`` are aliased OUTRIGHT;
        * each gate's ``inputs`` / ``output`` value (if a dict) is
          aliased — the outer ``gates`` list is freshly built but the
          inner ``inputs`` / ``output`` references point at the recorder's
          storage;
        * ``signals`` is shallow-copied: the outer list is fresh, but
          each ``signals[*]`` dict is aliased.

        Callers MUST NOT mutate the result. Deep-copy semantics for
        specific blocks (``risk_engine_state.positions``) land in Phase
        Beta-9 per plan §4.2.
        """
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "decision_id": self.decision_id,
            "source_repo": self.source_repo,
            "strategy_version": self.strategy_version,
            "git_sha": self.git_sha,
            "bot_mode": self.bot_mode,
            "captured_at": self.captured_at,
            "decision_reference_time": self.decision_reference_time,
            "drop_counters": self.drop_counters,
            "policy_filter_counters": self.policy_filter_counters,
            "recorder_internal_failure": self.recorder_internal_failure,
            "gates": [
                {
                    "name": g.name,
                    "passed": g.passed,
                    "reason": g.reason,
                    "inputs": g.inputs,
                    "output": g.output,
                }
                for g in self.gates
            ],
            "signals": list(self.signals),
        }
