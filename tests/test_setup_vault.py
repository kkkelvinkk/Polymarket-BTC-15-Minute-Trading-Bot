import contextlib
import io
import unittest
from unittest import mock

import setup_vault


class SetupVaultPromptTests(unittest.TestCase):
    def test_prompt_text_reprompts_after_empty_value(self):
        with mock.patch("builtins.input", side_effect=["", "value"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup_vault._prompt_text("LABEL"), "value")

        self.assertIn("LABEL cannot be empty", output.getvalue())

    def test_prompt_secret_reprompts_after_empty_value(self):
        with mock.patch.object(setup_vault, "getpass", side_effect=["", "secret"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup_vault._prompt_secret("SECRET"), "secret")

        self.assertIn("SECRET cannot be empty", output.getvalue())

    def test_prompt_private_key_reprompts_after_empty_and_invalid_values(self):
        valid_private_key = "1" * 64

        with mock.patch.object(
            setup_vault,
            "getpass",
            side_effect=["", "abc", valid_private_key],
        ):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup_vault._prompt_private_key(), "0x" + valid_private_key)

        prompt_output = output.getvalue()
        self.assertIn("POLYMARKET_PK cannot be empty", prompt_output)
        self.assertIn("private key must be 64 hex characters", prompt_output)

    def test_prompt_signature_type_reprompts_until_supported_integer(self):
        with mock.patch("builtins.input", side_effect=["", "abc", "4", "3"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup_vault._prompt_signature_type(), 3)

        prompt_output = output.getvalue()
        self.assertIn("POLYMARKET_SIGNATURE_TYPE (0, 1, 2, or 3) cannot be empty", prompt_output)
        self.assertIn("POLYMARKET_SIGNATURE_TYPE must be an integer", prompt_output)
        self.assertIn("POLYMARKET_SIGNATURE_TYPE must be 0, 1, 2, or 3", prompt_output)

    def test_prompt_api_credential_action_reprompts_until_supported_action(self):
        with mock.patch("builtins.input", side_effect=["", "bad", "derive"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup_vault._prompt_api_credential_action(), "derive")

        prompt_output = output.getvalue()
        self.assertIn("CLOB API credential action (create or derive) cannot be empty", prompt_output)
        self.assertIn("CLOB API credential action must be create or derive", prompt_output)

    def test_resolve_funder_reprompts_until_valid_address_for_signature_type_one(self):
        private_key = "0x" + "1" * 64
        valid_funder = "0x" + "2" * 40

        with mock.patch("builtins.input", side_effect=["", "abc", "0x" + "3" * 39, valid_funder]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup_vault._resolve_funder(private_key, 1), valid_funder)

        prompt_output = output.getvalue()
        self.assertIn("POLYMARKET_FUNDER cannot be empty", prompt_output)
        self.assertIn("POLYMARKET_FUNDER must start with 0x", prompt_output)
        self.assertIn("POLYMARKET_FUNDER must be 42 characters", prompt_output)

    def test_resolve_funder_rejects_invalid_proxy_wallet_for_signature_type_three(self):
        private_key = "0x" + "1" * 64

        with mock.patch.object(
            setup_vault,
            "_public_profile",
            return_value={"proxyWallet": "not-an-address"},
        ):
            with self.assertRaisesRegex(ValueError, "Polymarket proxyWallet must start with 0x"):
                setup_vault._resolve_funder(private_key, 3)

    def test_prompt_password_reprompts_after_invalid_or_mismatched_values(self):
        with mock.patch.object(
            setup_vault,
            "getpass",
            side_effect=[
                "",
                "short",
                "correcthorse",
                "differenthorse",
                "correcthorse",
                "correcthorse",
            ],
        ):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup_vault._prompt_password(), "correcthorse")

        prompt_output = output.getvalue()
        self.assertIn("credentials vault password cannot be empty", prompt_output)
        self.assertIn("credentials vault password must be at least 8 characters", prompt_output)
        self.assertIn("vault passwords do not match", prompt_output)
