import inspect
import unittest

import patch_polymarket_quote_warnings


class PolymarketQuoteWarningPatchTests(unittest.TestCase):
    def test_book_snapshot_handler_patch_matches_sync_nautilus_call_site(self):
        patch_polymarket_quote_warnings._PATCH_APPLIED = False

        self.assertTrue(patch_polymarket_quote_warnings.apply_polymarket_quote_warning_patch())

        from nautilus_trader.adapters.polymarket.data import PolymarketDataClient

        self.assertFalse(
            inspect.iscoroutinefunction(PolymarketDataClient._handle_book_snapshot)
        )
        self.assertFalse(inspect.iscoroutinefunction(PolymarketDataClient._handle_quote))


if __name__ == "__main__":
    unittest.main()
