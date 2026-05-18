import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "patch_market_orders.py"


def load_patch_module():
    spec = importlib.util.spec_from_file_location("patch_market_orders_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PatchMarketOrdersTests(unittest.TestCase):
    def test_auto_redeem_handler_exception_is_not_swallowed(self):
        module = load_patch_module()

        def failing_handler(_payload):
            raise RuntimeError("handler failed")

        module.register_auto_redeem_handler(failing_handler)
        try:
            with self.assertRaisesRegex(RuntimeError, "handler failed"):
                module._dispatch_auto_redeem({"event_type": "auto_redeem", "amount": "1"})
        finally:
            module.unregister_auto_redeem_handler(failing_handler)


if __name__ == "__main__":
    unittest.main()
