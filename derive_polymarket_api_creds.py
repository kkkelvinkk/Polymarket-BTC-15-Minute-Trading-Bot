#!/usr/bin/env python3
"""Derive wallet-backed CLOB API credentials and update the encrypted vault."""

from __future__ import annotations

import argparse
from pathlib import Path

from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client_v2 import ClobClient as V2ClobClient

from vault_store import (
    DEFAULT_VAULT_FILE,
    PolymarketVault,
    load_vault,
    prompt_vault_password,
    save_vault,
)


CLOB_HOST = "https://clob.polymarket.com"


def mask_secret(value: str) -> str:
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def normalize_private_key(private_key: str) -> str:
    private_key = private_key.strip()
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    if len(private_key) != 66:
        raise RuntimeError("private key should be a 32-byte hex value")
    return private_key


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Derive Polymarket CLOB API credentials and save them to the encrypted vault.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT_FILE,
        help="Encrypted credentials vault path.",
    )
    parser.add_argument(
        "--print-secrets",
        action="store_true",
        help="Print the derived API credentials. Avoid this on shared screens.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    password = prompt_vault_password()
    vault = load_vault(password, args.vault)

    private_key = normalize_private_key(vault.private_key)
    signer_address = Account.from_key(private_key).address
    signature_type = vault.signature_type
    funder = vault.funder

    if signature_type == 0 and funder.lower() != signer_address.lower():
        raise RuntimeError(
            "For a normal MetaMask wallet, vault funder must match the signer address.",
        )

    print(f"Signer address: {signer_address}")
    print(f"Funder address: {funder}")
    print(f"Signature type: {signature_type}")
    print("Deriving CLOB API credentials...")

    if signature_type == 3:
        client = V2ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=POLYGON,
            signature_type=signature_type,
            funder=funder,
        )
        creds = client.derive_api_key()
    else:
        client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=POLYGON,
            signature_type=signature_type,
            funder=funder,
        )
        creds = client.derive_api_key()

    updated_vault = PolymarketVault(
        private_key=vault.private_key,
        funder=vault.funder,
        signature_type=vault.signature_type,
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        passphrase=creds.api_passphrase,
        polygon_rpc_url=vault.polygon_rpc_url,
    )
    save_vault(updated_vault, password, args.vault)

    print(f"Updated encrypted vault: {args.vault}")
    if args.print_secrets:
        print(f"POLYMARKET_API_KEY={creds.api_key}")
        print(f"POLYMARKET_API_SECRET={creds.api_secret}")
        print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
    else:
        print(f"POLYMARKET_API_KEY={mask_secret(creds.api_key)}")
        print("POLYMARKET_API_SECRET=***")
        print("POLYMARKET_PASSPHRASE=***")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
