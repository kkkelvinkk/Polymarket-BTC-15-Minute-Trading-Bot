#!/usr/bin/env python3
"""Create credentials/encrypted_credentials.json for live trading."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from getpass import getpass
from pathlib import Path

from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client_v2 import ClobClient as V2ClobClient

from vault_crypto import verify_private_key
from vault_store import DEFAULT_VAULT_FILE, PolymarketVault, save_vault, validate_vault_password


CLOB_HOST = "https://clob.polymarket.com"
PUBLIC_PROFILE_URL = "https://gamma-api.polymarket.com/public-profile"
API_CREDENTIAL_ACTIONS = frozenset({"create", "derive"})


def _print_input_error(message: str) -> None:
    print(f"Error: {message}")


def _prompt_text(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value != "":
            return value
        _print_input_error(f"{label} cannot be empty")


def _prompt_secret(label: str) -> str:
    while True:
        value = getpass(f"{label}: ").strip()
        if value != "":
            return value
        _print_input_error(f"{label} cannot be empty")


def _prompt_private_key() -> str:
    while True:
        try:
            return verify_private_key(_prompt_secret("POLYMARKET_PK"))
        except ValueError as exc:
            _print_input_error(str(exc))


def _prompt_signature_type() -> int:
    while True:
        raw = _prompt_text("POLYMARKET_SIGNATURE_TYPE (0, 1, 2, or 3)")
        try:
            signature_type = int(raw)
        except ValueError:
            _print_input_error("POLYMARKET_SIGNATURE_TYPE must be an integer")
            continue
        if signature_type not in {0, 1, 2, 3}:
            _print_input_error("POLYMARKET_SIGNATURE_TYPE must be 0, 1, 2, or 3")
            continue
        return signature_type


def _prompt_api_credential_action() -> str:
    while True:
        action = _prompt_text("CLOB API credential action (create or derive)").lower()
        if action in API_CREDENTIAL_ACTIONS:
            return action
        _print_input_error("CLOB API credential action must be create or derive")


def _validate_address(address: str, label: str) -> str:
    if not address.startswith("0x"):
        raise ValueError(f"{label} must start with 0x")
    if len(address) != 42:
        raise ValueError(f"{label} must be 42 characters")
    try:
        int(address[2:], 16)
    except ValueError as exc:
        raise ValueError(f"{label} must contain only hex characters") from exc
    return address


def _prompt_funder() -> str:
    while True:
        funder = _prompt_text("POLYMARKET_FUNDER")
        try:
            return _validate_address(funder, "POLYMARKET_FUNDER")
        except ValueError as exc:
            _print_input_error(str(exc))


def _public_profile(owner_address: str) -> dict[str, object]:
    query = urllib.parse.urlencode({"address": owner_address})
    request = urllib.request.Request(
        f"{PUBLIC_PROFILE_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "polymarket-vault-setup",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise RuntimeError("Polymarket profile response must be a JSON object")
    return payload


def _resolve_funder(private_key: str, signature_type: int) -> str:
    signer_address = Account.from_key(private_key).address
    if signature_type == 0:
        print(f"Signer/funder address: {signer_address}")
        return signer_address
    if signature_type == 3:
        payload = _public_profile(signer_address)
        deposit_wallet = payload["proxyWallet"]
        if not isinstance(deposit_wallet, str) or deposit_wallet.strip() == "":
            raise RuntimeError(f"Polymarket profile returned invalid proxyWallet: {payload}")
        deposit_wallet = _validate_address(deposit_wallet.strip(), "Polymarket proxyWallet")
        print(f"Signer address: {signer_address}")
        print(f"Deposit wallet: {deposit_wallet}")
        return deposit_wallet
    print(f"Signer address: {signer_address}")
    return _prompt_funder()


def _request_clob_credentials(
    private_key: str,
    funder: str,
    signature_type: int,
    action: str,
):
    if action not in API_CREDENTIAL_ACTIONS:
        raise ValueError("CLOB API credential action must be create or derive")
    if signature_type == 3:
        client = V2ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=POLYGON,
            signature_type=signature_type,
            funder=funder,
        )
        if action == "create":
            return client.create_api_key()
        return client.derive_api_key()
    client = ClobClient(
        CLOB_HOST,
        key=private_key,
        chain_id=POLYGON,
        signature_type=signature_type,
        funder=funder,
    )
    if action == "create":
        return client.create_api_key()
    return client.derive_api_key()


def _prompt_password() -> str:
    while True:
        try:
            password = validate_vault_password(getpass("New vault password: "))
            confirm = validate_vault_password(getpass("Confirm vault password: "))
        except ValueError as exc:
            _print_input_error(str(exc))
            continue
        if password == confirm:
            return password
        _print_input_error("vault passwords do not match")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the encrypted Polymarket credentials vault.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT_FILE,
        help="Vault path to create.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.vault.exists():
        raise RuntimeError(f"vault already exists: {args.vault}")

    print("Create encrypted Polymarket runtime vault")
    print(f"Vault path: {args.vault}")
    print()

    private_key = _prompt_private_key()
    signature_type = _prompt_signature_type()
    polygon_rpc_url = _prompt_secret("POLYGON_RPC_URL")
    funder = _resolve_funder(private_key, signature_type)
    api_credential_action = _prompt_api_credential_action()
    password = _prompt_password()
    print(f"Requesting CLOB API credentials with action: {api_credential_action}")
    creds = _request_clob_credentials(
        private_key,
        funder,
        signature_type,
        api_credential_action,
    )

    vault = PolymarketVault(
        private_key=private_key,
        funder=funder,
        signature_type=signature_type,
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        passphrase=creds.api_passphrase,
        polygon_rpc_url=polygon_rpc_url,
    )
    saved_path = save_vault(vault, password, args.vault)
    print(f"Encrypted credentials vault written: {saved_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
