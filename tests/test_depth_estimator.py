"""Phase 5A regression tests for depth_estimator helpers."""

import unittest
from decimal import Decimal

from depth_estimator import (
    InvalidBookLevelError,
    _parse_book_level,
    estimate_limit_ioc_fill,
    estimate_market_ioc_fill,
)


def book(*levels):
    return [{"price": str(p), "size": str(s)} for p, s in levels]


class ParseBookLevelTests(unittest.TestCase):
    def test_accepts_valid_level(self):
        price, size = _parse_book_level({"price": "0.62", "size": "10"}, 0)
        self.assertEqual(price, Decimal("0.62"))
        self.assertEqual(size, Decimal("10"))

    def test_rejects_zero_price(self):
        with self.assertRaisesRegex(InvalidBookLevelError, "price=0"):
            _parse_book_level({"price": "0", "size": "10"}, 1)

    def test_rejects_negative_price(self):
        with self.assertRaisesRegex(InvalidBookLevelError, r"price=-0\.1"):
            _parse_book_level({"price": "-0.1", "size": "10"}, 1)

    def test_rejects_price_above_one(self):
        with self.assertRaisesRegex(InvalidBookLevelError, r"price=1\.5"):
            _parse_book_level({"price": "1.5", "size": "10"}, 2)

    def test_rejects_zero_size(self):
        with self.assertRaisesRegex(InvalidBookLevelError, "size=0"):
            _parse_book_level({"price": "0.5", "size": "0"}, 3)

    def test_rejects_negative_size(self):
        with self.assertRaisesRegex(InvalidBookLevelError, "size=-5"):
            _parse_book_level({"price": "0.5", "size": "-5"}, 3)

    def test_rejects_missing_key(self):
        with self.assertRaisesRegex(InvalidBookLevelError, "non-numeric or missing"):
            _parse_book_level({"price": "0.5"}, 4)

    def test_rejects_non_numeric(self):
        with self.assertRaisesRegex(InvalidBookLevelError, "non-numeric or missing"):
            _parse_book_level({"price": "not-a-number", "size": "10"}, 5)


class EstimateMarketIocFillTests(unittest.TestCase):
    def test_per_plan_synthetic_example(self):
        """Per EXECUTION_PLAN.md Phase 5 acceptance test:
        book ``[$0.62 x 10, $0.70 x 15]``, budget $10.
        Level 1 USD capacity = 0.62 * 10 = $6.20. Remaining = $3.80.
        Tokens at level 2 = 3.80 / 0.70 = 5.428571...
        Total tokens = 15.428571..., VWAP = 10 / 15.428571 = 0.6481..."""
        vwap, tokens, full = estimate_market_ioc_fill(
            book((Decimal("0.62"), Decimal("10")), (Decimal("0.70"), Decimal("15"))),
            Decimal("10"),
        )
        self.assertTrue(full)
        self.assertAlmostEqual(float(vwap), 0.6481, places=3)
        self.assertAlmostEqual(float(tokens), 15.4286, places=3)

    def test_book_too_thin_returns_not_full(self):
        vwap, tokens, full = estimate_market_ioc_fill(
            book((Decimal("0.50"), Decimal("2"))),
            Decimal("10"),
        )
        self.assertFalse(full)
        self.assertEqual(vwap, Decimal("0.50"))
        self.assertEqual(tokens, Decimal("2"))

    def test_empty_book(self):
        vwap, tokens, full = estimate_market_ioc_fill([], Decimal("10"))
        self.assertIsNone(vwap)
        self.assertEqual(tokens, Decimal("0"))
        self.assertFalse(full)

    def test_rejects_non_positive_budget(self):
        with self.assertRaisesRegex(ValueError, "must be positive"):
            estimate_market_ioc_fill(book((Decimal("0.5"), Decimal("10"))), Decimal("0"))

    def test_propagates_invalid_book_level(self):
        with self.assertRaises(InvalidBookLevelError):
            estimate_market_ioc_fill(
                [{"price": "1.5", "size": "10"}],
                Decimal("5"),
            )

    def test_exact_budget_fits_full_level(self):
        vwap, tokens, full = estimate_market_ioc_fill(
            book((Decimal("0.50"), Decimal("10"))),
            Decimal("5"),
        )
        self.assertTrue(full)
        self.assertEqual(vwap, Decimal("0.50"))
        self.assertEqual(tokens, Decimal("10"))


class EstimateLimitIocFillTests(unittest.TestCase):
    def test_reviewer_correctness_case(self):
        """Per EXECUTION_PLAN.md Phase 5 reviewer-flagged correctness case:
        operator wants up to 10 tokens at price <= $0.50 with budget $5.
        Book has 10 tokens @ $0.40. Token-driven walk accumulates 10 tokens,
        spends $4 (less than budget), reports fully_filled=True."""
        vwap, tokens, cost, full = estimate_limit_ioc_fill(
            book((Decimal("0.40"), Decimal("10"))),
            target_token_qty=Decimal("10"),
            max_price=Decimal("0.50"),
        )
        self.assertTrue(full)
        self.assertEqual(vwap, Decimal("0.40"))
        self.assertEqual(tokens, Decimal("10"))
        self.assertEqual(cost, Decimal("4.00"))

    def test_price_cap_stops_walk(self):
        """target_token_qty=20, max_price=$0.62. Book has 10 @ $0.62 + 15 @ $0.70.
        Walk stops at the $0.70 level (price > cap). 10 tokens filled,
        fully_filled=False."""
        vwap, tokens, cost, full = estimate_limit_ioc_fill(
            book((Decimal("0.62"), Decimal("10")), (Decimal("0.70"), Decimal("15"))),
            target_token_qty=Decimal("20"),
            max_price=Decimal("0.62"),
        )
        self.assertFalse(full)
        self.assertEqual(vwap, Decimal("0.62"))
        self.assertEqual(tokens, Decimal("10"))
        self.assertEqual(cost, Decimal("6.20"))

    def test_first_level_above_cap_returns_no_fill(self):
        vwap, tokens, cost, full = estimate_limit_ioc_fill(
            book((Decimal("0.80"), Decimal("10"))),
            target_token_qty=Decimal("5"),
            max_price=Decimal("0.50"),
        )
        self.assertFalse(full)
        self.assertIsNone(vwap)
        self.assertEqual(tokens, Decimal("0"))
        self.assertEqual(cost, Decimal("0"))

    def test_rejects_non_positive_target(self):
        with self.assertRaisesRegex(ValueError, "target_token_qty must be positive"):
            estimate_limit_ioc_fill(
                book((Decimal("0.50"), Decimal("10"))),
                target_token_qty=Decimal("0"),
                max_price=Decimal("0.5"),
            )

    def test_rejects_max_price_outside_range(self):
        with self.assertRaisesRegex(ValueError, r"max_price must be in"):
            estimate_limit_ioc_fill([], Decimal("10"), Decimal("1.5"))
        with self.assertRaisesRegex(ValueError, r"max_price must be in"):
            estimate_limit_ioc_fill([], Decimal("10"), Decimal("0"))

    def test_propagates_invalid_book_level(self):
        with self.assertRaises(InvalidBookLevelError):
            estimate_limit_ioc_fill(
                [{"price": "0.5", "size": "-1"}],
                Decimal("5"),
                Decimal("0.6"),
            )

    def test_sweeps_multiple_levels_below_cap(self):
        vwap, tokens, cost, full = estimate_limit_ioc_fill(
            book(
                (Decimal("0.40"), Decimal("5")),
                (Decimal("0.45"), Decimal("5")),
                (Decimal("0.49"), Decimal("5")),
            ),
            target_token_qty=Decimal("12"),
            max_price=Decimal("0.50"),
        )
        self.assertTrue(full)
        self.assertEqual(tokens, Decimal("12"))
        # 5 * 0.40 + 5 * 0.45 + 2 * 0.49 = 2.00 + 2.25 + 0.98 = 5.23
        self.assertEqual(cost, Decimal("5.23"))
        self.assertAlmostEqual(float(vwap), 5.23 / 12, places=4)


class EstimateFillForOrderTypeTests(unittest.TestCase):
    """Phase 5B unified entrypoint dispatching to the right estimator."""

    def test_market_ioc_dispatches_correctly(self):
        from depth_estimator import estimate_fill_for_order_type
        vwap, tokens, cost, full = estimate_fill_for_order_type(
            "market_ioc",
            book((Decimal("0.62"), Decimal("10")), (Decimal("0.70"), Decimal("15"))),
            usd_to_spend=Decimal("10"),
        )
        self.assertTrue(full)
        self.assertIsNone(cost)  # market: budget IS the cost
        self.assertAlmostEqual(float(vwap), 0.6481, places=3)

    def test_limit_ioc_dispatches_correctly(self):
        from depth_estimator import estimate_fill_for_order_type
        vwap, tokens, cost, full = estimate_fill_for_order_type(
            "limit_ioc",
            book((Decimal("0.40"), Decimal("10"))),
            target_token_qty=Decimal("10"),
            max_price=Decimal("0.50"),
        )
        self.assertTrue(full)
        self.assertEqual(cost, Decimal("4.00"))
        self.assertEqual(vwap, Decimal("0.40"))

    def test_market_ioc_rejects_missing_budget(self):
        from depth_estimator import estimate_fill_for_order_type
        with self.assertRaisesRegex(ValueError, "market_ioc requires usd_to_spend"):
            estimate_fill_for_order_type("market_ioc", [])

    def test_market_ioc_rejects_limit_args(self):
        from depth_estimator import estimate_fill_for_order_type
        with self.assertRaisesRegex(ValueError, "does not accept target_token_qty"):
            estimate_fill_for_order_type(
                "market_ioc", [],
                usd_to_spend=Decimal("5"),
                target_token_qty=Decimal("10"),
            )

    def test_limit_ioc_rejects_missing_qty_or_price(self):
        from depth_estimator import estimate_fill_for_order_type
        with self.assertRaisesRegex(ValueError, "limit_ioc requires both"):
            estimate_fill_for_order_type(
                "limit_ioc", [], target_token_qty=Decimal("10")
            )

    def test_limit_ioc_rejects_usd_to_spend(self):
        from depth_estimator import estimate_fill_for_order_type
        with self.assertRaisesRegex(ValueError, "does not accept usd_to_spend"):
            estimate_fill_for_order_type(
                "limit_ioc", [],
                target_token_qty=Decimal("10"),
                max_price=Decimal("0.5"),
                usd_to_spend=Decimal("5"),
            )

    def test_invalid_order_type_raises(self):
        from depth_estimator import estimate_fill_for_order_type
        with self.assertRaisesRegex(ValueError, "must be 'market_ioc' or 'limit_ioc'"):
            estimate_fill_for_order_type(
                "gtc", [], usd_to_spend=Decimal("10")
            )


class SelectedTokenSideTests(unittest.TestCase):
    """Verifies the EV-gate-side semantics: the estimator must walk the
    SELECTED token's asks (YES for long, NO for short), not the other side.
    Per Phase 5.2 critical note."""

    def test_no_book_empty_yes_only_returns_not_full_for_no_trade(self):
        yes_book = book((Decimal("0.62"), Decimal("100")))
        no_book = []
        # Simulating a "short" trade (buy NO): use no_book
        vwap, tokens, full = estimate_market_ioc_fill(no_book, Decimal("10"))
        self.assertFalse(full)
        self.assertIsNone(vwap)
        # The opposite-side book is still healthy, but the estimator MUST
        # walk the correct side.
        vwap2, tokens2, full2 = estimate_market_ioc_fill(yes_book, Decimal("10"))
        self.assertTrue(full2)


if __name__ == "__main__":
    unittest.main()
