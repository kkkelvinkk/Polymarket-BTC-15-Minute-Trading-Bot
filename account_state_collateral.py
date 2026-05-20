"""AccountState free-collateral tracking for live trade sizing."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from loguru import logger


ACCOUNT_BALANCE_STALE_STARTUP = "startup"
ACCOUNT_BALANCE_STALE_AFTER_ORDER = "after_order"
ACCOUNT_BALANCE_STALE_AFTER_REDEEM = "after_redeem"
ACCOUNT_BALANCE_STALE_TOO_OLD = "too_old"
ACCOUNT_BALANCE_STALE_FUTURE = "future"
ACCOUNT_STATE_COLLATERAL_CURRENCY = "pUSD"
POLYMARKET_ACCOUNT_ISSUER = "POLYMARKET"

_ALLOWED_ACCOUNT_BALANCE_STALE_REASONS = frozenset(
    {
        ACCOUNT_BALANCE_STALE_STARTUP,
        ACCOUNT_BALANCE_STALE_AFTER_ORDER,
        ACCOUNT_BALANCE_STALE_AFTER_REDEEM,
        ACCOUNT_BALANCE_STALE_TOO_OLD,
        ACCOUNT_BALANCE_STALE_FUTURE,
    }
)


class AccountBalanceTracker:
    """Cache the latest AccountState free collateral and enforce freshness."""

    def __init__(self) -> None:
        self.latest_free_collateral: Optional[Decimal] = None
        self.latest_account_state_ts: Optional[datetime] = None
        self.account_state_sequence = 0
        self.balance_stale_reason: Optional[str] = ACCOUNT_BALANCE_STALE_STARTUP
        self.balance_stale_order_id: Optional[str] = None
        self.balance_stale_since_ts: Optional[datetime] = datetime.now(timezone.utc)

    def record(self, account_state: Any) -> None:
        self._validate_exchange_reported_state(account_state)
        free_collateral = self._extract_free_collateral(account_state)
        event_time = self._event_time(account_state)
        now = datetime.now(timezone.utc)
        if event_time > now:
            raise RuntimeError(
                f"AccountState ts_event must not be in the future "
                f"({event_time.isoformat()} > {now.isoformat()})"
            )
        if (
            self.latest_account_state_ts is not None
            and event_time <= self.latest_account_state_ts
        ):
            raise RuntimeError(
                "AccountState ts_event must increase monotonically "
                f"({event_time.isoformat()} <= {self.latest_account_state_ts.isoformat()})"
            )
        if (
            self.balance_stale_reason
            in (ACCOUNT_BALANCE_STALE_AFTER_ORDER, ACCOUNT_BALANCE_STALE_AFTER_REDEEM)
            and self.balance_stale_since_ts is not None
            and event_time <= self.balance_stale_since_ts
        ):
            raise RuntimeError(
                "AccountState ts_event must be newer than the stale-balance "
                f"invalidation ({event_time.isoformat()} <= "
                f"{self.balance_stale_since_ts.isoformat()})"
            )
        self.latest_free_collateral = free_collateral
        self.latest_account_state_ts = event_time
        self.account_state_sequence += 1
        self.balance_stale_reason = None
        self.balance_stale_order_id = None
        self.balance_stale_since_ts = None
        logger.info(
            "AccountState free collateral updated: "
            f"{ACCOUNT_STATE_COLLATERAL_CURRENCY} {free_collateral} "
            f"(seq={self.account_state_sequence}, ts={event_time.isoformat()})"
        )

    def mark_stale(self, reason: str, *, order_id: Optional[str] = None) -> None:
        if reason not in _ALLOWED_ACCOUNT_BALANCE_STALE_REASONS:
            raise RuntimeError(f"unknown balance stale reason: {reason!r}")
        if reason == ACCOUNT_BALANCE_STALE_AFTER_ORDER:
            if order_id in (None, ""):
                raise RuntimeError("after_order balance stale markers require order_id")
            stale_order_id = str(order_id)
        elif order_id not in (None, ""):
            raise RuntimeError(f"{reason} balance stale markers must not include order_id")
        else:
            stale_order_id = None
        self.balance_stale_reason = reason
        self.balance_stale_order_id = stale_order_id
        self.balance_stale_since_ts = datetime.now(timezone.utc)
        order_detail = "" if stale_order_id is None else f", order_id={stale_order_id}"
        logger.warning(
            f"AccountState free-collateral cache marked stale: {reason} "
            f"(seq={self.account_state_sequence}{order_detail})"
        )

    def clear_after_verified_no_fill(self, order_id: str) -> None:
        if order_id in (None, ""):
            raise RuntimeError("verified no-fill balance restore requires order_id")
        if (
            self.balance_stale_reason == ACCOUNT_BALANCE_STALE_AFTER_ORDER
            and self.balance_stale_order_id == str(order_id)
            and self.latest_account_state_ts is not None
            and self.latest_free_collateral is not None
        ):
            self.balance_stale_reason = None
            self.balance_stale_order_id = None
            self.balance_stale_since_ts = None
            logger.info(
                "AccountState free-collateral cache restored after verified no-fill "
                f"terminal order event for {order_id} (seq={self.account_state_sequence})"
            )

    def snapshot_for_decision(
        self,
        sizing_config: dict[str, Any],
        rec: Any,
    ) -> Optional[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        stale_reason = self.balance_stale_reason
        if self.latest_account_state_ts is None or self.latest_free_collateral is None:
            rec.update(
                free_collateral_at_decision=None,
                account_state_age_seconds=None,
                account_state_sequence=self.account_state_sequence,
                balance_stale_reason=ACCOUNT_BALANCE_STALE_STARTUP,
            )
            rec.reject("no_balance", "no AccountState free-collateral snapshot received")
            return None

        if self.latest_account_state_ts > now:
            rec.update(
                account_state_age_seconds=None,
                balance_stale_reason=ACCOUNT_BALANCE_STALE_FUTURE,
            )
            rec.reject(
                "future_account_state",
                f"AccountState ts={self.latest_account_state_ts.isoformat()} is after now={now.isoformat()}",
            )
            return None

        age_seconds = Decimal(str((now - self.latest_account_state_ts).total_seconds()))
        rec.update(
            free_collateral_at_decision=self.latest_free_collateral,
            account_state_age_seconds=float(age_seconds),
            account_state_sequence=self.account_state_sequence,
            balance_stale_reason=stale_reason,
        )
        if stale_reason == ACCOUNT_BALANCE_STALE_AFTER_ORDER:
            rec.reject(
                "stale_balance_after_order",
                f"AccountState seq={self.account_state_sequence} has not refreshed after order",
            )
            return None
        if stale_reason == ACCOUNT_BALANCE_STALE_AFTER_REDEEM:
            rec.reject(
                "stale_balance_after_redeem",
                f"AccountState seq={self.account_state_sequence} has not refreshed after redeem",
            )
            return None
        max_age = sizing_config["max_account_state_age_seconds"]
        if age_seconds > max_age:
            rec.update(balance_stale_reason=ACCOUNT_BALANCE_STALE_TOO_OLD)
            rec.reject(
                "stale_balance",
                f"AccountState age {age_seconds}s exceeds {max_age}s",
            )
            return None
        return {
            "free_collateral": self.latest_free_collateral,
            "account_state_age_seconds": age_seconds,
            "account_state_sequence": self.account_state_sequence,
        }

    def block_reason_for_order(
        self,
        sizing_config: dict[str, Any],
        position_size: Decimal,
    ) -> Optional[str]:
        if self.latest_account_state_ts is None or self.latest_free_collateral is None:
            return "no AccountState free-collateral snapshot received"
        if self.balance_stale_reason == ACCOUNT_BALANCE_STALE_AFTER_ORDER:
            return "AccountState has not refreshed after order"
        if self.balance_stale_reason == ACCOUNT_BALANCE_STALE_AFTER_REDEEM:
            return "AccountState has not refreshed after redeem"
        now = datetime.now(timezone.utc)
        if self.latest_account_state_ts > now:
            return (
                f"AccountState ts={self.latest_account_state_ts.isoformat()} "
                f"is after now={now.isoformat()}"
            )
        age_seconds = Decimal(str((now - self.latest_account_state_ts).total_seconds()))
        max_age = sizing_config["max_account_state_age_seconds"]
        if age_seconds > max_age:
            return f"AccountState age {age_seconds}s exceeds {max_age}s"
        required_free_collateral = position_size + sizing_config["balance_safety_buffer_usd"]
        if self.latest_free_collateral < required_free_collateral:
            return (
                f"free_collateral={self.latest_free_collateral} "
                f"< required={required_free_collateral}"
            )
        return None

    def _extract_free_collateral(self, account_state: Any) -> Decimal:
        if not hasattr(account_state, "balances"):
            raise RuntimeError("AccountState event missing balances")
        balances = account_state.balances
        if not isinstance(balances, list):
            raise RuntimeError("AccountState balances must be a list")
        matching_balances = []
        for balance in balances:
            if not hasattr(balance, "currency"):
                raise RuntimeError("AccountState balance missing currency")
            if str(balance.currency) == ACCOUNT_STATE_COLLATERAL_CURRENCY:
                matching_balances.append(balance)
        if len(matching_balances) != 1:
            raise RuntimeError(
                f"AccountState must contain exactly one {ACCOUNT_STATE_COLLATERAL_CURRENCY} "
                f"balance, found {len(matching_balances)}"
            )
        collateral_balance = matching_balances[0]
        if not hasattr(collateral_balance, "free"):
            raise RuntimeError(
                f"{ACCOUNT_STATE_COLLATERAL_CURRENCY} AccountState balance missing free amount"
            )
        free_money = collateral_balance.free
        if not hasattr(free_money, "as_decimal"):
            raise RuntimeError(
                f"{ACCOUNT_STATE_COLLATERAL_CURRENCY} AccountState free amount missing as_decimal()"
            )
        free_collateral = Decimal(str(free_money.as_decimal()))
        if not free_collateral.is_finite() or free_collateral < Decimal("0"):
            raise RuntimeError(
                f"{ACCOUNT_STATE_COLLATERAL_CURRENCY} free collateral must be finite "
                f"and >= 0, got {free_collateral}"
            )
        return free_collateral

    def _validate_exchange_reported_state(self, account_state: Any) -> None:
        if not hasattr(account_state, "is_reported"):
            raise RuntimeError("AccountState event missing is_reported flag")
        if account_state.is_reported is not True:
            raise RuntimeError("AccountState must be exchange-reported")
        if not hasattr(account_state, "account_id"):
            raise RuntimeError("AccountState event missing account_id")
        account_id = account_state.account_id
        if not hasattr(account_id, "get_issuer"):
            raise RuntimeError("AccountState account_id missing get_issuer()")
        account_issuer = str(account_id.get_issuer())
        if account_issuer != POLYMARKET_ACCOUNT_ISSUER:
            raise RuntimeError(
                f"AccountState issuer must be {POLYMARKET_ACCOUNT_ISSUER}, got {account_issuer!r}"
            )

    def _event_time(self, account_state: Any) -> datetime:
        if not hasattr(account_state, "ts_event"):
            raise RuntimeError("AccountState event missing ts_event")
        ts_event = int(account_state.ts_event)
        if ts_event <= 0:
            raise RuntimeError(f"AccountState ts_event must be positive, got {ts_event}")
        return datetime.fromtimestamp(ts_event / 1_000_000_000, timezone.utc)
