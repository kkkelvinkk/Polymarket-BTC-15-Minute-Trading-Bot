"""Tests for SOPS plaintext-env guard."""

import os
import unittest
from pathlib import Path

from sops_plaintext_env_guard import (
    _is_live_invocation,
    refuse_plaintext_env_in_live_mode,
)


class IsLiveInvocationTests(unittest.TestCase):
    def test_live_flag_in_argv(self):
        self.assertTrue(_is_live_invocation(["bot.py", "--live"]))

    def test_live_with_confirm(self):
        self.assertTrue(_is_live_invocation(["bot.py", "--live", "--confirm-live"]))

    def test_no_live_flag(self):
        self.assertFalse(_is_live_invocation(["bot.py", "--test-mode"]))

    def test_env_var_truthy(self):
        original = os.environ.get("BOT_LIVE_MODE")
        try:
            for truthy in ("1", "true", "yes", "on", "TRUE", "YES"):
                os.environ["BOT_LIVE_MODE"] = truthy
                self.assertTrue(
                    _is_live_invocation(["bot.py"]),
                    msg=f"BOT_LIVE_MODE={truthy!r} should be truthy",
                )
        finally:
            if original is None:
                os.environ.pop("BOT_LIVE_MODE", None)
            else:
                os.environ["BOT_LIVE_MODE"] = original

    def test_env_var_falsy(self):
        original = os.environ.get("BOT_LIVE_MODE")
        try:
            for falsy in ("0", "false", "no", "off", "", "  "):
                os.environ["BOT_LIVE_MODE"] = falsy
                self.assertFalse(
                    _is_live_invocation(["bot.py"]),
                    msg=f"BOT_LIVE_MODE={falsy!r} should be falsy",
                )
        finally:
            if original is None:
                os.environ.pop("BOT_LIVE_MODE", None)
            else:
                os.environ["BOT_LIVE_MODE"] = original


class RefusePlaintextEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp_root = Path(f"/tmp/test_sops_guard_{os.getpid()}_{id(self)}")
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        # Best-effort cleanup
        env_path = self.tmp_root / ".env"
        env_path.unlink(missing_ok=True)
        try:
            self.tmp_root.rmdir()
        except OSError:
            pass

    def test_passes_in_simulation_with_plaintext_env(self):
        (self.tmp_root / ".env").write_text("FAKE=1\n", encoding="utf-8")
        # No --live flag → simulation → guard does NOT raise
        refuse_plaintext_env_in_live_mode(
            repo_root=self.tmp_root, argv=["bot.py", "--test-mode"]
        )

    def test_passes_in_live_without_plaintext_env(self):
        # No .env present → guard does NOT raise even in live mode
        refuse_plaintext_env_in_live_mode(
            repo_root=self.tmp_root, argv=["bot.py", "--live"]
        )

    def test_raises_in_live_with_plaintext_env(self):
        (self.tmp_root / ".env").write_text("POLYMARKET_PK=0xreal\n", encoding="utf-8")
        with self.assertRaisesRegex(
            RuntimeError, "Live mode refuses to start with plaintext .env"
        ):
            refuse_plaintext_env_in_live_mode(
                repo_root=self.tmp_root, argv=["bot.py", "--live"]
            )

    def test_raises_with_confirm_live_too(self):
        (self.tmp_root / ".env").write_text("POLYMARKET_PK=0xreal\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Live mode refuses"):
            refuse_plaintext_env_in_live_mode(
                repo_root=self.tmp_root,
                argv=["bot.py", "--live", "--confirm-live"],
            )

    def test_raises_via_env_var_only(self):
        (self.tmp_root / ".env").write_text("POLYMARKET_PK=0xreal\n", encoding="utf-8")
        original = os.environ.get("BOT_LIVE_MODE")
        try:
            os.environ["BOT_LIVE_MODE"] = "1"
            with self.assertRaisesRegex(RuntimeError, "Live mode refuses"):
                refuse_plaintext_env_in_live_mode(
                    repo_root=self.tmp_root, argv=["bot.py"]
                )
        finally:
            if original is None:
                os.environ.pop("BOT_LIVE_MODE", None)
            else:
                os.environ["BOT_LIVE_MODE"] = original


if __name__ == "__main__":
    unittest.main()
