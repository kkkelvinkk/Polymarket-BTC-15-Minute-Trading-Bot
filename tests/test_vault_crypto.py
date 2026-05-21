import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vault_crypto import (
    InvalidVaultPasswordError,
    VaultCryptoError,
    decrypt_json_payload,
    encrypt_json_payload,
    save_encrypted_json,
    verify_private_key,
)
from vault_store import (
    DEFAULT_VAULT_FILE,
    DEFAULT_VAULT_PATH,
    PolymarketVault,
    VaultFileSecurityError,
    load_vault,
    refuse_secret_dotenv_keys,
    refuse_secret_environment_keys,
    save_vault,
    validate_vault_password,
)


class VaultCryptoTests(unittest.TestCase):
    def test_json_vault_round_trip(self):
        payload = {
            "polymarket": {
                "private_key": "0x" + "1" * 64,
                "funder": "0x" + "2" * 40,
                "signature_type": 3,
                "api_key": "api-key",
                "api_secret": "api-secret",
                "passphrase": "passphrase",
            },
            "polygon_rpc_url": "https://rpc.example/key",
        }

        encrypted = encrypt_json_payload(payload, "correct horse")

        self.assertEqual(encrypted["version"], 2)
        self.assertEqual(encrypted["kdf"]["name"], "argon2id")
        self.assertEqual(encrypted["kdf"]["iterations"], 3)
        self.assertEqual(decrypt_json_payload(encrypted, "correct horse"), payload)

    def test_wrong_password_raises(self):
        encrypted = encrypt_json_payload({"value": "secret"}, "correct horse")

        with self.assertRaises(InvalidVaultPasswordError):
            decrypt_json_payload(encrypted, "wrong horse")

    def test_unsupported_kdf_parameter_raises(self):
        encrypted = encrypt_json_payload({"value": "secret"}, "correct horse")
        encrypted["kdf"]["lanes"] = 1

        with self.assertRaisesRegex(VaultCryptoError, "lanes"):
            decrypt_json_payload(encrypted, "correct horse")

    def test_saved_vault_mode_is_0600(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "encrypted_credentials.json"

            save_encrypted_json({"value": "secret"}, "correct horse", path)

            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_private_key_validation(self):
        self.assertEqual(
            verify_private_key("1" * 64),
            "0x" + "1" * 64,
        )
        with self.assertRaisesRegex(ValueError, "64 hex"):
            verify_private_key("1" * 63)


class VaultStoreTests(unittest.TestCase):
    def test_polymarket_vault_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "encrypted_credentials.json"
            vault = PolymarketVault(
                private_key="0x" + "1" * 64,
                funder="0x" + "2" * 40,
                signature_type=3,
                api_key="api-key",
                api_secret="api-secret",
                passphrase="passphrase",
                polygon_rpc_url="https://rpc.example/key",
            )

            save_vault(vault, "correct horse", path)
            loaded = load_vault("correct horse", path)

            self.assertEqual(loaded, vault)
            self.assertEqual(
                loaded.to_runtime_credentials(),
                {
                    "private_key": vault.private_key,
                    "funder": vault.funder,
                    "api_key": vault.api_key,
                    "api_secret": vault.api_secret,
                    "passphrase": vault.passphrase,
                    "signature_type": vault.signature_type,
                },
            )

    def test_missing_required_payload_field_raises(self):
        payload = {
            "polymarket": {
                "private_key": "0x" + "1" * 64,
                "funder": "0x" + "2" * 40,
                "signature_type": 3,
                "api_key": "api-key",
                "api_secret": "api-secret",
            },
            "polygon_rpc_url": "https://rpc.example/key",
        }

        with self.assertRaises(KeyError):
            PolymarketVault.from_payload(payload)

    def test_vault_password_rejects_boundary_whitespace(self):
        with self.assertRaisesRegex(ValueError, "whitespace"):
            validate_vault_password(" correct horse")
        with self.assertRaisesRegex(ValueError, "whitespace"):
            validate_vault_password("correct horse ")

    def test_load_rejects_group_or_world_readable_vault(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "encrypted_credentials.json"
            vault = PolymarketVault(
                private_key="0x" + "1" * 64,
                funder="0x" + "2" * 40,
                signature_type=3,
                api_key="api-key",
                api_secret="api-secret",
                passphrase="passphrase",
                polygon_rpc_url="https://rpc.example/key",
            )

            save_vault(vault, "correct horse", path)
            os.chmod(path, 0o644)

            with self.assertRaisesRegex(VaultFileSecurityError, "owner-only"):
                load_vault("correct horse", path)

    def test_dotenv_guard_parses_export_whitespace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            path.write_text(
                "export   POLYMARKET_PROXY_WALLET=0xreal\n"
                "export\tPOLYGON_RPC_URL=https://rpc.example/key\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "POLYMARKET_PROXY_WALLET"):
                refuse_secret_dotenv_keys(path)

    def test_environment_guard_rejects_inherited_secret_keys(self):
        with mock.patch.dict(os.environ, {"POLYMARKET_BUILDER_KEY": "secret"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "POLYMARKET_BUILDER_KEY"):
                refuse_secret_environment_keys()


class VaultDocsTests(unittest.TestCase):
    def test_deploy_service_does_not_use_sops(self):
        service = Path("deploy/polybot.service").read_text(encoding="utf-8")

        self.assertIn("systemd-ask-password", service)
        self.assertIn("/usr/sbin/runuser", service)
        self.assertIn("/run/systemd/ask-password", service)
        self.assertNotIn("User=polybot", service)
        self.assertNotIn("EnvironmentFile=", service)
        self.assertNotIn("sops exec-env", service)

    def test_execution_plan_systemd_snippet_matches_root_launcher(self):
        plan = Path("EXECUTION_PLAN.md").read_text(encoding="utf-8")

        self.assertIn("/usr/sbin/runuser", plan)
        self.assertIn("/run/systemd/ask-password", plan)
        self.assertNotIn("User=polybot\nGroup=polybot", plan)
        self.assertNotIn("| /opt/polybot/venv/bin/python bot.py", plan)

    def test_env_example_excludes_live_credentials(self):
        env_example = Path(".env.example").read_text(encoding="utf-8")

        self.assertNotIn("POLYMARKET_PK=", env_example)
        self.assertNotIn("POLYMARKET_API_SECRET=", env_example)
        self.assertIn("setup_vault.py", env_example)

    def test_vault_file_is_gitignored(self):
        result = os.popen(
            "git check-ignore credentials/encrypted_credentials.json"
        ).read()

        self.assertEqual(result.strip(), "credentials/encrypted_credentials.json")

    def test_default_vault_file_is_repo_anchored(self):
        self.assertFalse(DEFAULT_VAULT_PATH.is_absolute())
        self.assertTrue(DEFAULT_VAULT_FILE.is_absolute())
        self.assertEqual(DEFAULT_VAULT_FILE.name, "encrypted_credentials.json")
