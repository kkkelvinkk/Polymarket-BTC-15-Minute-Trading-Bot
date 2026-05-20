"""Tests for SOPS plaintext-env guard."""

import os
import unittest
from pathlib import Path

from sops_plaintext_env_guard import (
    _is_live_invocation,
    refuse_plaintext_env_in_live_mode,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


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


class DeployServiceTests(unittest.TestCase):
    def test_service_template_uses_sops_exec_env_by_default(self):
        service = (REPO_ROOT / "deploy" / "polybot.service").read_text(encoding="utf-8")

        self.assertIn("sops exec-env", service)
        self.assertIn("ExecStart=/usr/local/bin/sops exec-env", service)
        self.assertNotIn("\nEnvironmentFile=", service)

    def test_deploy_readme_first_run_uses_sops_secret_file(self):
        readme = (REPO_ROOT / "deploy" / "README.md").read_text(encoding="utf-8")

        self.assertIn("/opt/polybot/secrets/.env.sops.yaml", readme)
        self.assertNotIn(
            "/opt/polybot/Polymarket-BTC-15-Minute-Trading-Bot/.env",
            readme,
        )
        self.assertIn("Repo-root plaintext `.env` files are simulation-only", readme)

    def test_top_level_docs_scope_plaintext_env_to_local_simulation(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("Repo-root plaintext `.env` is for local simulation/test-mode", readme)
        self.assertIn("/opt/polybot/secrets/.env.sops.yaml", readme)
        self.assertIn("Live deployments must use SOPS", env_example)
        self.assertIn("Live mode refuses repo-root plaintext `.env`", env_example)

    def test_execution_plan_security_checklist_uses_sops_secret_file(self):
        plan = (REPO_ROOT / "EXECUTION_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("/opt/polybot/secrets/.env.sops.yaml", plan)
        self.assertNotIn("Polymarket private key in `.env`", plan)
        self.assertNotIn("`.env` file mode `0600", plan)


if __name__ == "__main__":
    unittest.main()
