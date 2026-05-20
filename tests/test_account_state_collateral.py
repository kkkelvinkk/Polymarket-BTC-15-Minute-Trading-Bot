"""Tests for live AccountState collateral extraction."""

from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.model.currencies import pUSD
from nautilus_trader.model.objects import AccountBalance, Money

from account_state_collateral import AccountBalanceTracker


class _AccountId:
    def get_issuer(self):
        return "POLYMARKET"


class AccountState:
    pass


def test_tracker_accepts_real_nautilus_pusd_account_balance():
    total = Money(Decimal("100.25"), pUSD)
    event = AccountState()
    event.is_reported = True
    event.account_id = _AccountId()
    event.balances = [
        AccountBalance(
            total=total,
            locked=Money.from_raw(0, pUSD),
            free=total,
        )
    ]
    event.ts_event = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    tracker = AccountBalanceTracker()

    tracker.record(event)

    assert tracker.latest_free_collateral == Decimal("100.25")
    assert tracker.account_state_sequence == 1
