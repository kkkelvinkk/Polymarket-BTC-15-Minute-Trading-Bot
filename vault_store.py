"""Load and save the encrypted Polymarket runtime vault."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from vault_crypto import load_encrypted_json, save_encrypted_json, verify_private_key


DEFAULT_VAULT_PATH = Path("credentials/encrypted_credentials.json")
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_VAULT_FILE = REPO_ROOT / DEFAULT_VAULT_PATH
SIGNATURE_TYPES = frozenset({0, 1, 2, 3})
SECRET_DOTENV_EXACT_KEYS = frozenset({"POLYGON_RPC_URL"})
SECRET_DOTENV_PREFIXES = ("POLYMARKET_",)


class VaultFileSecurityError(RuntimeError):
    """Raised when the encrypted vault file is readable beyond the owner."""


def refuse_secret_dotenv_keys(env_path: Path) -> None:
    """Refuse live startup if .env contains values that belong in the vault."""
    if not env_path.exists():
        return
    for key in dotenv_values(env_path):
        if _is_vault_secret_key(key):
            raise RuntimeError(
                f"Live mode refuses secret key {key} in {env_path}. "
                "Move Polymarket credentials and Polygon RPC URL into "
                "credentials/encrypted_credentials.json."
            )


def refuse_secret_environment_keys() -> None:
    """Refuse live startup if inherited environment exposes vault secrets."""
    for key in sorted(os.environ):
        if _is_vault_secret_key(key):
            raise RuntimeError(
                f"Live mode refuses secret key {key} in process environment. "
                "Move Polymarket credentials and Polygon RPC URL into "
                "credentials/encrypted_credentials.json."
            )


def _is_vault_secret_key(key: str) -> bool:
    return key in SECRET_DOTENV_EXACT_KEYS or key.startswith(SECRET_DOTENV_PREFIXES)


def validate_vault_password(password: str) -> str:
    if password == "":
        raise ValueError("credentials vault password cannot be empty")
    if password != password.strip():
        raise ValueError("credentials vault password cannot start or end with whitespace")
    if len(password) < 8:
        raise ValueError("credentials vault password must be at least 8 characters")
    return password


def assert_vault_file_security(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise VaultFileSecurityError(f"vault path must be a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise VaultFileSecurityError(f"vault file owner must be the current user: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077:
        raise VaultFileSecurityError(
            f"vault file permissions must be owner-only: {path} has mode {mode:o}"
        )


@dataclass(frozen=True)
class PolymarketVault:
    """Runtime credentials decrypted from the local vault."""

    private_key: str
    funder: str
    signature_type: int
    api_key: str
    api_secret: str
    passphrase: str
    polygon_rpc_url: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PolymarketVault":
        polymarket = payload["polymarket"]
        if not isinstance(polymarket, dict):
            raise ValueError("vault polymarket field must be an object")

        private_key = verify_private_key(_required_text(polymarket, "private_key"))
        funder = _normalize_address(_required_text(polymarket, "funder"))
        signature_type = _required_signature_type(polymarket["signature_type"])
        api_key = _required_text(polymarket, "api_key")
        api_secret = _required_text(polymarket, "api_secret")
        passphrase = _required_text(polymarket, "passphrase")
        polygon_rpc_url = _required_text(payload, "polygon_rpc_url")

        return cls(
            private_key=private_key,
            funder=funder,
            signature_type=signature_type,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            polygon_rpc_url=polygon_rpc_url,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "polymarket": {
                "private_key": verify_private_key(self.private_key),
                "funder": _normalize_address(self.funder),
                "signature_type": _required_signature_type(self.signature_type),
                "api_key": _non_empty_text(self.api_key, "api_key"),
                "api_secret": _non_empty_text(self.api_secret, "api_secret"),
                "passphrase": _non_empty_text(self.passphrase, "passphrase"),
            },
            "polygon_rpc_url": _non_empty_text(
                self.polygon_rpc_url,
                "polygon_rpc_url",
            ),
        }

    def to_runtime_credentials(self) -> dict[str, str | int]:
        return {
            "private_key": self.private_key,
            "funder": self.funder,
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "passphrase": self.passphrase,
            "signature_type": self.signature_type,
        }


def _required_text(mapping: dict[str, Any], key: str) -> str:
    return _non_empty_text(mapping[key], key)


def _non_empty_text(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"vault {key} must be a string")
    text = value.strip()
    if text == "":
        raise ValueError(f"vault {key} cannot be empty")
    return text


def _normalize_address(value: str) -> str:
    address = value.strip()
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError("funder must be a 0x-prefixed Ethereum address")
    int(address[2:], 16)
    return address


def _required_signature_type(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("signature_type must be an integer")
    if value not in SIGNATURE_TYPES:
        raise ValueError("signature_type must be 0, 1, 2, or 3")
    return value


def load_vault(password: str, path: Path = DEFAULT_VAULT_FILE) -> PolymarketVault:
    validate_vault_password(password)
    assert_vault_file_security(path)
    payload = load_encrypted_json(password, path)
    return PolymarketVault.from_payload(payload)


def save_vault(
    vault: PolymarketVault,
    password: str,
    path: Path = DEFAULT_VAULT_FILE,
) -> Path:
    validate_vault_password(password)
    return save_encrypted_json(vault.to_payload(), password, path)


def prompt_vault_password() -> str:
    return validate_vault_password(getpass("Credentials vault password: "))


def load_vault_from_prompt(path: Path = DEFAULT_VAULT_FILE) -> PolymarketVault:
    return load_vault(prompt_vault_password(), path)
