"""
Structured decision-observation writer.

Every _make_trading_decision invocation MUST emit exactly one JSON line to
decisions.jsonl, including early-return rejected branches. A finalizer-style
context manager guarantees the "exactly one" semantics so naive log-at-each-
return refactors can't drop a branch.

This is decision-observation data, NOT trade simulation. The records describe
what the strategy decided (or why it skipped) at a moment in time; they do not
model fills, settlement, or P&L. Per AGENTS.md item 2, a decision/paper-
observation log MUST NOT be used to claim live-equivalent profitability.

The writer is intentionally minimal:
  - Appends one JSON line per record.
  - Performs atomic append (single open-write-close per record).
  - No silent drops: a write failure raises and propagates out of the
    `with` block. The caller's exception handler decides whether to fail-stop
    the trading loop or surface the error to the operator.

Schema fields documented at EXECUTION_PLAN.md. New fields can be
added without coordination; older lines simply lack them.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional


def _default_decision_log_path() -> Path:
    """Resolve decisions.jsonl relative to the live ledger path."""
    raw = os.getenv("DECISION_LOG_PATH")
    if raw:
        return Path(raw)
    ledger_raw = os.getenv("LIVE_TRADE_LEDGER_PATH")
    if ledger_raw:
        return Path(ledger_raw).resolve().parent / "decisions.jsonl"
    return Path("decisions.jsonl").resolve()


_write_lock = threading.Lock()


def new_decision_id() -> str:
    return str(uuid.uuid4())


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")


def _atomic_append_jsonl(path: Path, record: dict) -> None:
    """Append one JSON line. Single open() under a process lock so concurrent
    callers in the same process never interleave bytes. Cross-process
    interleaving is impossible because the bot holds an exclusive ledger
    lock for the whole runtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=_json_default, ensure_ascii=False)
    with _write_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


class DecisionRecord:
    """Finalizer-style decision-observation record.

    Usage:

        with DecisionRecord(current_price) as rec:
            rec.update(slug=..., condition_id=..., fused_confidence=...)
            if rejected:
                rec.reject("trend_filter", "yes_price=0.45 not extreme")
                return False
            rec.decided(direction="long", executable_entry=...)
            return True

    Exactly one record is appended to decisions.jsonl when the context
    exits — including when an exception escapes the `with` block. The
    record then carries `rejected_at_gate="exception"` and the exception
    summary in `rejection_reason`.

    The caller never needs to remember to "log on this branch": the
    `__exit__` does it for every branch.
    """

    def __init__(
        self,
        current_price: Any,
        path: Optional[Path] = None,
        strategy_observation_mode: str = "live_gate",
        decision_id: Optional[str] = None,
    ) -> None:
        self._path = path or _default_decision_log_path()
        self.fields: dict = {
            "decision_id": decision_id if decision_id is not None else new_decision_id(),
            "ts": datetime.now(timezone.utc).isoformat(),
            "current_price": (
                str(current_price) if current_price is not None else None
            ),
            "slug": None,
            "condition_id": None,
            "yes_token_id": None,
            "no_token_id": None,
            "market_end_time": None,
            "decision_snapshot_at": None,
            "decision_reference_time": None,
            "decision_price_history_len": None,
            "decision_tick_buffer_len": None,
            "decision_market_timestamp": None,
            "decision_sub_interval": None,
            "context_sma20_deviation": None,
            "context_momentum": None,
            "context_volatility": None,
            "seconds_into_sub_interval": None,
            "trade_window_label": None,
            "trend_price_band": None,
            "strategy_observation_mode": strategy_observation_mode,
            "fused_confidence": None,
            "fused_direction": None,
            "decided_direction": None,
            "rejected_at_gate": None,
            "rejection_reason": None,
            "executable_entry": None,
            "estimated_tokens_filled": None,
            "estimated_actual_cost": None,
            "depth_fully_filled": None,
            "yes_ask": None,
            "no_ask": None,
            "model_signals": None,
            "sizing_mode": None,
            "resolved_trade_usd": None,
            "free_collateral_at_decision": None,
            "account_state_age_seconds": None,
            "account_state_sequence": None,
            "balance_stale_reason": None,
        }

    def update(self, **kwargs: Any) -> None:
        """Merge join-key fields into the record."""
        self.fields.update(kwargs)

    def reject(self, gate: str, reason: str) -> None:
        """Record an early-return rejection with the gate name + reason."""
        self.fields["rejected_at_gate"] = gate
        self.fields["rejection_reason"] = reason
        # An explicitly-rejected decision must not also carry a decided
        # direction. Clear it so post-hoc analysis can rely on the invariant
        # `decided_direction is None iff rejected_at_gate is not None`.
        self.fields["decided_direction"] = None

    def decided(self, direction: str, **extra: Any) -> None:
        """Record a positive trade decision."""
        self.fields["decided_direction"] = direction
        self.fields["rejected_at_gate"] = None
        self.fields["rejection_reason"] = None
        if extra:
            self.fields.update(extra)

    def __enter__(self) -> "DecisionRecord":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            # Always write before propagating. The record carries the exception
            # type and value as the rejection reason so the operator can join
            # the log against any later traceback. Do not swallow the
            # exception — return False (or omit explicit return) so it
            # propagates out of the with block.
            self.fields["rejected_at_gate"] = "exception"
            self.fields["rejection_reason"] = f"{exc_type.__name__}: {exc_val}"
            self.fields["decided_direction"] = None
        try:
            _atomic_append_jsonl(self._path, self.fields)
        except Exception:
            # The write itself failed. We must NOT silently swallow the write
            # error — it gets re-raised so the caller can decide whether to
            # fail-stop or surface to the operator. If an earlier exception
            # was already in flight, Python chains the two automatically.
            raise
        return False
