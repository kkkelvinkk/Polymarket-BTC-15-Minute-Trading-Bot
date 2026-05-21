"""Password-based encryption for the local runtime vault."""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


class VaultCryptoError(RuntimeError):
    """Raised when encrypted vault data is malformed or unsupported."""


class InvalidVaultPasswordError(VaultCryptoError):
    """Raised when a vault password cannot authenticate the payload."""


VAULT_VERSION = 2
KDF_NAME = "argon2id"
SALT_SIZE = 16
ARGON2ID_ITERATIONS = 3
ARGON2ID_LANES = 4
ARGON2ID_MEMORY_COST = 65536
ARGON2ID_LENGTH = 32


def verify_private_key(private_key: str) -> str:
    """Return a normalized 0x-prefixed 32-byte hex private key."""
    key = private_key.strip().lower()
    if key.startswith("0x"):
        key = key[2:]
    if len(key) != 64:
        raise ValueError("private key must be 64 hex characters")
    try:
        int(key, 16)
    except ValueError as exc:
        raise ValueError("private key contains non-hex characters") from exc
    return f"0x{key}"


def _derive_key(
    password: str,
    *,
    salt: bytes,
    iterations: int,
    lanes: int,
    memory_cost: int,
    length: int,
) -> bytes:
    kdf = Argon2id(
        salt=salt,
        length=length,
        iterations=iterations,
        lanes=lanes,
        memory_cost=memory_cost,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _new_kdf_metadata(salt: bytes) -> dict[str, object]:
    return {
        "name": KDF_NAME,
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "iterations": ARGON2ID_ITERATIONS,
        "lanes": ARGON2ID_LANES,
        "memory_cost": ARGON2ID_MEMORY_COST,
        "length": ARGON2ID_LENGTH,
    }


def _read_kdf_metadata(encrypted_data: dict[str, Any]) -> dict[str, object]:
    kdf = encrypted_data["kdf"]
    if not isinstance(kdf, dict):
        raise VaultCryptoError("vault kdf must be an object")

    name = kdf["name"]
    if not isinstance(name, str) or name.lower() != KDF_NAME:
        raise VaultCryptoError("vault kdf name is unsupported")

    salt_text = kdf["salt"]
    if not isinstance(salt_text, str):
        raise VaultCryptoError("vault salt must be a string")
    salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
    if len(salt) != SALT_SIZE:
        raise VaultCryptoError("vault salt length is unsupported")

    params = {
        "iterations": kdf["iterations"],
        "lanes": kdf["lanes"],
        "memory_cost": kdf["memory_cost"],
        "length": kdf["length"],
    }
    for key, value in params.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise VaultCryptoError(f"vault Argon2id {key} must be an integer")

    expected = {
        "iterations": ARGON2ID_ITERATIONS,
        "lanes": ARGON2ID_LANES,
        "memory_cost": ARGON2ID_MEMORY_COST,
        "length": ARGON2ID_LENGTH,
    }
    for key, expected_value in expected.items():
        if params[key] != expected_value:
            raise VaultCryptoError(f"vault Argon2id {key} is unsupported")

    return {"salt": salt, **params}


def encrypt_json_payload(payload: dict[str, Any], password: str) -> dict[str, object]:
    """Encrypt a JSON object into the vault file shape."""
    if not isinstance(payload, dict):
        raise ValueError("vault payload must be a JSON object")
    if len(password) < 8:
        raise ValueError("vault password must be at least 8 characters")

    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    salt = secrets.token_bytes(SALT_SIZE)
    cipher = Fernet(
        _derive_key(
            password,
            salt=salt,
            iterations=ARGON2ID_ITERATIONS,
            lanes=ARGON2ID_LANES,
            memory_cost=ARGON2ID_MEMORY_COST,
            length=ARGON2ID_LENGTH,
        )
    )
    encrypted = cipher.encrypt(plaintext.encode("utf-8"))
    return {
        "version": VAULT_VERSION,
        "kdf": _new_kdf_metadata(salt),
        "encrypted": base64.urlsafe_b64encode(encrypted).decode("ascii"),
    }


def decrypt_json_payload(
    encrypted_data: dict[str, Any],
    password: str,
) -> dict[str, Any]:
    """Decrypt a vault file object into its JSON payload."""
    try:
        version = encrypted_data["version"]
        if version != VAULT_VERSION:
            raise VaultCryptoError("vault version is unsupported")

        params = _read_kdf_metadata(encrypted_data)
        encrypted_text = encrypted_data["encrypted"]
        if not isinstance(encrypted_text, str):
            raise VaultCryptoError("vault encrypted payload must be a string")

        cipher = Fernet(
            _derive_key(
                password,
                salt=params["salt"],
                iterations=params["iterations"],
                lanes=params["lanes"],
                memory_cost=params["memory_cost"],
                length=params["length"],
            )
        )
        plaintext = cipher.decrypt(base64.urlsafe_b64decode(encrypted_text))
        payload = json.loads(plaintext.decode("utf-8"))
    except InvalidToken as exc:
        raise InvalidVaultPasswordError("invalid vault password or payload") from exc
    except VaultCryptoError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VaultCryptoError(f"invalid vault data: {exc}") from exc

    if not isinstance(payload, dict):
        raise VaultCryptoError("vault payload must be a JSON object")
    return payload


def save_encrypted_json(
    payload: dict[str, Any],
    password: str,
    path: Path,
) -> Path:
    """Encrypt and save a JSON object with owner-only permissions."""
    encrypted_data = encrypt_json_payload(payload, password)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(encrypted_data, indent=2) + "\n"
    temp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as vault_file:
        vault_file.write(serialized)
        vault_file.flush()
        os.fsync(vault_file.fileno())
    os.replace(temp_path, path)
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    os.chmod(path, 0o600)
    return path


def load_encrypted_json(password: str, path: Path) -> dict[str, Any]:
    """Load and decrypt a JSON vault file."""
    encrypted_data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(encrypted_data, dict):
        raise VaultCryptoError("vault file must contain a JSON object")
    return decrypt_json_payload(encrypted_data, password)
