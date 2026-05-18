#!/usr/bin/env python3
"""Configure .env for Polymarket deposit-wallet CLOB v2 trading."""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account

from derive_polymarket_api_creds import normalize_private_key


ENV_PATH = Path(".env")
PUBLIC_PROFILE_URL = "https://gamma-api.polymarket.com/public-profile"


def required_env_text(path: Path) -> str:
    if not path.exists():
        raise RuntimeError(".env file not found")
    return path.read_text(encoding="utf-8")


def update_env(path: Path, updates: dict[str, str]) -> Path:
    lines = required_env_text(path).splitlines()
    remaining = dict(updates)
    output: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)

    if remaining:
        if output and output[-1].strip():
            output.append("")
        for key, value in remaining.items():
            output.append(f"{key}={value}")

    backup_path = path.with_name(f"{path.name}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup_path)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return backup_path


def public_profile(owner_address: str) -> dict[str, object]:
    query = urllib.parse.urlencode({"address": owner_address})
    request = urllib.request.Request(
        f"{PUBLIC_PROFILE_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": "polymarket-deposit-wallet-config"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover the Polymarket deposit wallet and update .env for signature type 3.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the discovered wallet without updating .env.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(dotenv_path=ENV_PATH, override=True)

    import os

    private_key = os.getenv("POLYMARKET_PK")
    if not private_key:
        raise RuntimeError("POLYMARKET_PK is missing in .env")

    signer_address = Account.from_key(normalize_private_key(private_key)).address
    payload = public_profile(signer_address)
    deposit_wallet = payload.get("proxyWallet")
    if not deposit_wallet:
        raise RuntimeError(f"Polymarket profile did not return a wallet address: {payload}")

    print(f"Signer address: {signer_address}")
    print(f"Deposit wallet: {deposit_wallet}")
    print("Required mode: POLYMARKET_SIGNATURE_TYPE=3")

    if args.dry_run:
        return 0

    backup_path = update_env(
        ENV_PATH,
        {
            "POLYMARKET_FUNDER": deposit_wallet,
            "POLYMARKET_SIGNATURE_TYPE": "3",
        },
    )
    print(f"Updated {ENV_PATH}")
    print(f"Backup written to {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
