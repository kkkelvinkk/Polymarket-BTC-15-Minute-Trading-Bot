#!/usr/bin/env python3
"""Decrypt the local Polymarket vault and display its stored credentials.

Read-only recovery helper: it never writes the vault, re-derives credentials,
or contacts the network. It decrypts credentials/encrypted_credentials.json
with your vault password and prints the values so you can recover them.

Usage:
    python view_vault.py                 # masked previews
    python view_vault.py --show-secrets  # full secret values
    python view_vault.py --json          # full decrypted payload as JSON
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eth_account import Account

from vault_crypto import load_encrypted_json
from vault_store import (
    DEFAULT_VAULT_FILE,
    PolymarketVault,
    assert_vault_file_security,
    prompt_vault_password,
)


SECRET_PREVIEW_HEAD = 6
SECRET_PREVIEW_TAIL = 4
LABEL_WIDTH = 26


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decrypt and display the encrypted Polymarket credentials vault.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT_FILE,
        help="Encrypted credentials vault path.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--show-secrets",
        action="store_true",
        help="Print full secret values instead of masked previews.",
    )
    output.add_argument(
        "--json",
        action="store_true",
        help="Print the full decrypted payload as JSON (always includes secrets).",
    )
    return parser.parse_args()


def mask_secret(value: str) -> str:
    if len(value) <= SECRET_PREVIEW_HEAD + SECRET_PREVIEW_TAIL:
        return "***"
    return f"{value[:SECRET_PREVIEW_HEAD]}...{value[-SECRET_PREVIEW_TAIL:]}"


def render_secret(value: str, show_secrets: bool) -> str:
    if show_secrets:
        return value
    return mask_secret(value)


def print_line(label: str, value: object) -> None:
    print(f"{label:<{LABEL_WIDTH}}{value}")


def print_vault(vault: PolymarketVault, show_secrets: bool) -> None:
    signer_address = Account.from_key(vault.private_key).address
    print_line("Signer address (derived)", signer_address)
    print_line("POLYMARKET_FUNDER", vault.funder)
    print_line("POLYMARKET_SIGNATURE_TYPE", vault.signature_type)
    print_line("POLYMARKET_PK", render_secret(vault.private_key, show_secrets))
    print_line("POLYMARKET_API_KEY", render_secret(vault.api_key, show_secrets))
    print_line("POLYMARKET_API_SECRET", render_secret(vault.api_secret, show_secrets))
    print_line("POLYMARKET_PASSPHRASE", render_secret(vault.passphrase, show_secrets))
    print_line("POLYGON_RPC_URL", render_secret(vault.polygon_rpc_url, show_secrets))


def main() -> int:
    args = parse_args()
    assert_vault_file_security(args.vault)
    password = prompt_vault_password()
    payload = load_encrypted_json(password, args.vault)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    vault = PolymarketVault.from_payload(payload)
    print(f"Vault decrypted: {args.vault}")
    print_vault(vault, args.show_secrets)
    if not args.show_secrets:
        print()
        print("Values are masked. Re-run with --show-secrets or --json to reveal them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
