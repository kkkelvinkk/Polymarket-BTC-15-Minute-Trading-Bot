"""
Risk Engine
Manages position sizing, risk limits, and portfolio constraints.

Beta-8 contract:
  - Every public method that mutates time-keyed state takes a REQUIRED
    ``now: datetime`` kwarg (M11; no default; no ``datetime.now()``
    fallback at the helper layer).
  - ``_stats_date`` is UTC: ``now.date()`` where ``now`` is UTC-aware.
    Daily-reset boundary shifts from local-TZ to UTC; the bot's startup
    gates on ``POLYBOT_ACKNOWLEDGE_UTC_DAILY_RESET=1``.
  - ``update_position`` / ``_assess_risk_level`` / ``_check_stop_loss``
    / ``_check_take_profit`` DELETED (orphan helpers; no in-process
    callers; the `.get("entry_time", datetime.now())` double-fallback
    is removed by deletion).
  - ``state_snapshot(now)`` exposes READ-AFTER-IDEMPOTENT-RESET state
    for the raw decision recorder.
  - ``_alerts`` writes are UTC-aware (TC82).
  - Risk envs (MAX_POSITION_SIZE etc.) are required at construction
    via the §12 promoted-constant convention.
"""
import os
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger


class RiskLevel(Enum):
    """Risk level classification."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskLimits:
    """Risk management limits."""
    max_position_size: Decimal
    max_total_exposure: Decimal
    max_positions: int
    max_drawdown_pct: float
    max_loss_per_day: Decimal
    max_leverage: float = 1.0


@dataclass
class PositionRisk:
    """Risk assessment for a position."""
    position_id: str
    current_size: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    risk_level: RiskLevel
    stop_loss: Optional[Decimal]
    take_profit: Optional[Decimal]
    time_held: float
    metadata: Dict[str, Any]


def _require_utc_now(now: datetime, where: str) -> None:
    if not isinstance(now, datetime):
        raise TypeError(f"{where}: now must be a datetime instance")
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeError(
            f"{where}: now must be timezone-aware (UTC) — M9/M11 enforce"
        )


class RiskEngine:
    """Risk management engine."""

    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        *,
        now: datetime,
    ):
        """Initialize risk engine.

        Beta-8: ``now`` is a REQUIRED kwarg (M11). When ``limits`` is
        explicitly provided, it wins; otherwise risk limits are read
        from the §12 promoted-constant envs (missing env raises).
        Review-cycle fix: replaced the ``or`` truthiness fallback with
        an explicit ``is None`` branch (Rule 1).
        """
        _require_utc_now(now, "RiskEngine.__init__")

        if limits is None:
            limits = RiskLimits(
                max_position_size=Decimal(os.environ["MAX_POSITION_SIZE"]),
                max_total_exposure=Decimal(os.environ["MAX_TOTAL_EXPOSURE"]),
                max_positions=int(os.environ["MAX_POSITIONS"]),
                max_drawdown_pct=float(os.environ["MAX_DRAWDOWN_PCT"]),
                max_loss_per_day=Decimal(os.environ["MAX_LOSS_PER_DAY"]),
                max_leverage=1.0,
            )
        self.limits = limits

        self._positions: Dict[str, PositionRisk] = {}

        # Beta-8: UTC-aware daily stats date.
        self._daily_pnl = Decimal("0")
        self._daily_trades = 0
        self._stats_date = now.date()
        self._peak_balance = Decimal("1000.0")
        self._current_balance = Decimal("1000.0")

        self._alerts: List[Dict[str, Any]] = []

        logger.info(
            f"Initialized Risk Engine: "
            f"max_position=${self.limits.max_position_size}, "
            f"max_exposure=${self.limits.max_total_exposure}"
        )

    # ------------------------------------------------------------------
    # Read-after-idempotent-reset state snapshot
    # ------------------------------------------------------------------

    def state_snapshot(self, *, now: datetime) -> Dict[str, Any]:
        """
        Beta-8: READ-AFTER-IDEMPOTENT-RESET snapshot for the raw decision
        recorder. The reset is the same one ``validate_new_position`` would
        perform a moment later — idempotent when both share ``now``.

        The ONLY mutation surface is the reset trio
        (``_stats_date``, ``_daily_pnl``, ``_daily_trades``). No IO.
        ``stats_date_source`` is ``"captured_pre_reset"`` when no reset
        fired, ``"captured_post_reset"`` when one did. TC46 enforces.
        """
        _require_utc_now(now, "RiskEngine.state_snapshot")
        date_before = self._stats_date
        self._maybe_reset_daily_stats(now=now)
        stats_date_source = (
            "captured_pre_reset" if self._stats_date == date_before
            else "captured_post_reset"
        )
        return {
            "stats_date": self._stats_date,
            "stats_date_source": stats_date_source,
            "daily_pnl": self._daily_pnl,
            "daily_trades": self._daily_trades,
            "peak_balance": self._peak_balance,
            "current_balance": self._current_balance,
            "positions": {
                pid: PositionRisk(
                    position_id=p.position_id,
                    current_size=p.current_size,
                    entry_price=p.entry_price,
                    current_price=p.current_price,
                    unrealized_pnl=p.unrealized_pnl,
                    risk_level=p.risk_level,
                    stop_loss=p.stop_loss,
                    take_profit=p.take_profit,
                    time_held=p.time_held,
                    metadata=dict(p.metadata),
                )
                for pid, p in self._positions.items()
            },
            "limits": {
                "max_position_size": self.limits.max_position_size,
                "max_total_exposure": self.limits.max_total_exposure,
                "max_positions": self.limits.max_positions,
                "max_drawdown_pct": self.limits.max_drawdown_pct,
                "max_loss_per_day": self.limits.max_loss_per_day,
                "max_leverage": self.limits.max_leverage,
            },
        }

    # ------------------------------------------------------------------
    # Public mutation surface — all take REQUIRED now=
    # ------------------------------------------------------------------

    def validate_new_position(
        self,
        size: Decimal,
        direction: str,
        current_price: Decimal,
        *,
        now: datetime,
        state_override: Optional[Dict] = None,
    ) -> tuple[bool, Optional[str]]:
        _require_utc_now(now, "RiskEngine.validate_new_position")
        # Beta-8: state_override is the replayer's injected snapshot. When
        # provided, all checks read from the override instead of live state;
        # mutating live state from a replayer-injected snapshot would be a
        # G6 violation.
        if state_override is not None:
            # Review-cycle fix: direct-index ``positions`` so a missing
            # key on a malformed replay snapshot fail-stops with a clear
            # KeyError (was ``.get("positions", {})`` silent fallback).
            daily_pnl = state_override["daily_pnl"]
            positions = state_override["positions"]
            current_exposure_count = len(positions)
            current_total_exposure = sum(
                p.current_size for p in positions.values()
            )
        else:
            self._maybe_reset_daily_stats(now=now)
            daily_pnl = self._daily_pnl
            current_exposure_count = len(self._positions)
            current_total_exposure = self.get_total_exposure()

        if size > self.limits.max_position_size:
            return False, (
                f"Position size ${size} exceeds max ${self.limits.max_position_size}"
            )

        if current_exposure_count >= self.limits.max_positions:
            return False, f"Max positions reached ({self.limits.max_positions})"

        new_exposure = current_total_exposure + size
        if new_exposure > self.limits.max_total_exposure:
            return False, (
                f"Total exposure ${new_exposure} would exceed max "
                f"${self.limits.max_total_exposure}"
            )

        if daily_pnl < -self.limits.max_loss_per_day:
            return False, f"Daily loss limit reached (${abs(daily_pnl)})"

        drawdown = self.get_current_drawdown()
        if drawdown > self.limits.max_drawdown_pct:
            return False, (
                f"Drawdown {drawdown:.1%} exceeds max "
                f"{self.limits.max_drawdown_pct:.1%}"
            )

        return True, None

    def calculate_position_size(
        self,
        signal_confidence: float,
        signal_score: float,
        current_price: Decimal,
        risk_percent: float = 0.02,
    ) -> Decimal:
        risk_amount = self._current_balance * Decimal(str(risk_percent))
        strength_multiplier = (
            Decimal(str(signal_confidence)) * Decimal(str(signal_score / 100))
        )
        position_size = risk_amount * strength_multiplier

        if position_size > Decimal("1.0"):
            logger.info(f"Capping position size from ${float(position_size):.2f} to $1.00")
            position_size = Decimal("1.0")

        position_size = max(position_size, Decimal("1.0"))

        logger.info(
            f"Calculated position size: ${position_size:.2f} "
            f"(confidence={signal_confidence:.2%}, score={signal_score:.1f})"
        )
        return position_size

    def add_position(
        self,
        position_id: str,
        size: Decimal,
        entry_price: Decimal,
        direction: str,
        *,
        now: datetime,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        count_trade: bool = True,
    ) -> None:
        _require_utc_now(now, "RiskEngine.add_position")
        self._maybe_reset_daily_stats(now=now)

        position = PositionRisk(
            position_id=position_id,
            current_size=size,
            entry_price=entry_price,
            current_price=entry_price,
            unrealized_pnl=Decimal("0"),
            risk_level=RiskLevel.LOW,
            stop_loss=stop_loss,
            take_profit=take_profit,
            time_held=0.0,
            metadata={
                "direction": direction,
                # Beta-8: re-use the caller-supplied UTC ``now``; smuggling a
                # fresh ``datetime.now(timezone.utc)`` here would diverge from
                # the caller's ``now`` and break the recorder's
                # "captured state == validate state" invariant.
                "entry_time": now,
            }
        )

        self._positions[position_id] = position
        if count_trade:
            self._daily_trades += 1

        logger.info(f"Added position: {position_id} (${size:.2f} @ ${entry_price:.2f})")

    def adjust_position(
        self,
        position_id: str,
        size: Decimal,
        entry_price: Decimal,
        *,
        now: datetime,
        direction: Optional[str] = None,
    ) -> None:
        """Adjust an existing position after partial fills or final fill price updates."""
        _require_utc_now(now, "RiskEngine.adjust_position")
        if position_id not in self._positions:
            self.add_position(
                position_id=position_id,
                size=size,
                entry_price=entry_price,
                direction=direction or "buy",
                now=now,
                count_trade=False,
            )
            return

        position = self._positions[position_id]
        position.current_size = size
        position.entry_price = entry_price
        position.current_price = entry_price
        position.unrealized_pnl = Decimal("0")
        if direction:
            position.metadata["direction"] = direction

        logger.info(f"Adjusted position: {position_id} (${size:.2f} @ ${entry_price:.4f})")

    def release_position(self, position_id: str) -> bool:
        """Release a position without booking realized P&L."""
        if position_id not in self._positions:
            return False
        del self._positions[position_id]
        logger.info(f"Released position without P&L: {position_id}")
        return True

    def remove_position(
        self,
        position_id: str,
        exit_price: Decimal,
        *,
        now: datetime,
    ) -> Optional[Decimal]:
        _require_utc_now(now, "RiskEngine.remove_position")
        self._maybe_reset_daily_stats(now=now)

        if position_id not in self._positions:
            return None

        position = self._positions[position_id]

        # Review-cycle fix: direction is required-present in metadata;
        # silently substituting "long" would mis-price the exit on a short.
        if "direction" not in position.metadata:
            raise RuntimeError(
                f"remove_position({position_id!r}): metadata missing required "
                "'direction' key"
            )
        direction = position.metadata["direction"]
        direction_key = str(direction).lower()
        if direction_key in {"long", "buy", "buy_yes", "buy_no"}:
            pnl_pct = (exit_price - position.entry_price) / position.entry_price
        else:
            pnl_pct = (position.entry_price - exit_price) / position.entry_price

        realized_pnl = position.current_size * pnl_pct

        self._current_balance += realized_pnl
        self._daily_pnl += realized_pnl

        if self._current_balance > self._peak_balance:
            self._peak_balance = self._current_balance

        del self._positions[position_id]

        logger.info(
            f"Closed position: {position_id} "
            f"P&L: ${realized_pnl:+.2f} ({pnl_pct:+.2%})"
        )
        return realized_pnl

    def record_realized_pnl(
        self,
        pnl: Decimal,
        *,
        now: datetime,
        source: str = "settlement",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record realized P&L not tied to an open risk position."""
        _require_utc_now(now, "RiskEngine.record_realized_pnl")
        self._maybe_reset_daily_stats(now=now)

        self._current_balance += pnl
        self._daily_pnl += pnl

        if self._current_balance > self._peak_balance:
            self._peak_balance = self._current_balance

        logger.info(
            f"Recorded realized P&L from {source}: ${pnl:+.2f} "
            f"(daily=${self._daily_pnl:+.2f})"
        )

    def restore_daily_stats(
        self,
        daily_pnl: Decimal,
        daily_trades: int,
        *,
        now: datetime,
    ) -> None:
        """Restore same-day realized P&L after a process restart."""
        _require_utc_now(now, "RiskEngine.restore_daily_stats")
        self._maybe_reset_daily_stats(now=now)
        self._daily_pnl = daily_pnl
        self._daily_trades = daily_trades
        self._current_balance = Decimal("1000.0") + daily_pnl
        self._peak_balance = max(Decimal("1000.0"), self._current_balance)
        logger.info(
            f"Restored daily risk stats from ledger: trades={daily_trades}, "
            f"pnl=${daily_pnl:+.2f}"
        )

    # ------------------------------------------------------------------
    # Diagnostics / metrics surface — wall-clock-allowed at helper edge
    # (not on the verdict path; Beta-8 carve-out documented)
    # ------------------------------------------------------------------

    def _create_alert(
        self,
        alert_type: str,
        message: str,
        risk_level: RiskLevel,
    ) -> None:
        """Create a risk alert. ``_alerts`` writes are UTC-aware (TC82)."""
        alert = {
            "timestamp": datetime.now(timezone.utc),
            "type": alert_type,
            "message": message,
            "risk_level": risk_level.value,
        }
        self._alerts.append(alert)
        logger.warning(f"[{risk_level.value.upper()}] {alert_type}: {message}")

    def get_total_exposure(self) -> Decimal:
        return sum(pos.current_size for pos in self._positions.values())

    def get_total_unrealized_pnl(self) -> Decimal:
        return sum(pos.unrealized_pnl for pos in self._positions.values())

    def get_current_drawdown(self) -> float:
        if self._peak_balance == 0:
            return 0.0
        drawdown = (self._peak_balance - self._current_balance) / self._peak_balance
        return float(drawdown)

    def get_risk_summary(self) -> Dict[str, Any]:
        """
        Diagnostic surface — wall-clock UTC read at helper edge, not
        on the verdict path. Consumed by ``monitoring/grafana_exporter``.
        """
        now_utc = datetime.now(timezone.utc)
        return {
            "timestamp": now_utc,
            "positions": {
                "count": len(self._positions),
                "max_allowed": self.limits.max_positions,
            },
            "exposure": {
                "current": float(self.get_total_exposure()),
                "max_allowed": float(self.limits.max_total_exposure),
                "utilization_pct": (
                    float(
                        self.get_total_exposure()
                        / self.limits.max_total_exposure * 100
                    )
                    if self.limits.max_total_exposure > 0 else 0
                ),
            },
            "pnl": {
                "daily": float(self._daily_pnl),
                "unrealized": float(self.get_total_unrealized_pnl()),
                "daily_limit": float(self.limits.max_loss_per_day),
            },
            "balance": {
                "current": float(self._current_balance),
                "peak": float(self._peak_balance),
                "drawdown_pct": self.get_current_drawdown() * 100,
                "max_drawdown_pct": self.limits.max_drawdown_pct * 100,
            },
            "daily_stats": {
                "trades": self._daily_trades,
                "pnl": float(self._daily_pnl),
            },
            # TC82: aware-minus-aware; never raises TypeError.
            "alerts": len([
                a for a in self._alerts
                if (now_utc - a["timestamp"]).total_seconds() < 3600
            ]),
        }

    def reset_daily_stats(self, *, now: datetime) -> None:
        """Reset daily statistics."""
        _require_utc_now(now, "RiskEngine.reset_daily_stats")
        self._daily_pnl = Decimal("0")
        self._daily_trades = 0
        self._stats_date = now.date()
        logger.info("Reset daily statistics")

    def _maybe_reset_daily_stats(self, *, now: datetime) -> None:
        """Reset daily counters once when the UTC calendar day changes."""
        _require_utc_now(now, "RiskEngine._maybe_reset_daily_stats")
        if now.date() != self._stats_date:
            self.reset_daily_stats(now=now)


# Beta-8: get_risk_engine singleton retained for backwards compat at
# bot-startup-time only. Review-cycle fix: ``now=`` is now REQUIRED with
# no default (M11). The bot's startup site always passes
# ``datetime.now(timezone.utc)``; tests pass an explicit UTC instant.
_risk_engine_instance: Optional[RiskEngine] = None


def get_risk_engine(*, now: datetime) -> RiskEngine:
    """Get singleton risk engine. ``now`` is REQUIRED (M11)."""
    global _risk_engine_instance
    if _risk_engine_instance is None:
        _require_utc_now(now, "get_risk_engine")
        _risk_engine_instance = RiskEngine(now=now)
    return _risk_engine_instance
