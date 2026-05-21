#!/usr/bin/env python3
"""Discover the Polymarket deposit wallet and update the encrypted vault."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

from eth_account import Account

from derive_polymarket_api_creds import normalize_private_key
from vault_store import (
    DEFAULT_VAULT_FILE,
    PolymarketVault,
    load_vault,
    prompt_vault_password,
    save_vault,
)


PUBLIC_PROFILE_URL = "https://gamma-api.polymarket.com/public-profile"


def public_profile(owner_address: str) -> dict[str, object]:
    query = urllib.parse.urlencode({"address": owner_address})
    request = urllib.request.Request(
        f"{PUBLIC_PROFILE_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "polymarket-deposit-wallet-config",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise RuntimeError("Polymarket profile response must be a JSON object")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover the Polymarket deposit wallet and update the encrypted vault for signature type 3.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT_FILE,
        help="Encrypted credentials vault path.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the discovered wallet without updating the vault.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    password = prompt_vault_password()
    vault = load_vault(password, args.vault)

    signer_address = Account.from_key(normalize_private_key(vault.private_key)).address
    payload = public_profile(signer_address)
    deposit_wallet = payload["proxyWallet"]
    if not isinstance(deposit_wallet, str) or deposit_wallet.strip() == "":
        raise RuntimeError(f"Polymarket profile returned invalid proxyWallet: {payload}")

    print(f"Signer address: {signer_address}")
    print(f"Deposit wallet: {deposit_wallet}")
    print("Required mode: POLYMARKET_SIGNATURE_TYPE=3")

    if args.dry_run:
        return 0

    updated_vault = PolymarketVault(
        private_key=vault.private_key,
        funder=deposit_wallet,
        signature_type=3,
        api_key=vault.api_key,
        api_secret=vault.api_secret,
        passphrase=vault.passphrase,
        polygon_rpc_url=vault.polygon_rpc_url,
    )
    save_vault(updated_vault, password, args.vault)
    print(f"Updated encrypted vault: {args.vault}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
