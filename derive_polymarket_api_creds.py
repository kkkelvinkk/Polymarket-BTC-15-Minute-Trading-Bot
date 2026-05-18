#!/usr/bin/env python3
"""
Derive Polymarket CLOB API credentials from POLYMARKET_PK and record them in .env.

This creates or re-derives the L2 CLOB credentials expected by this bot:

    POLYMARKET_API_KEY
    POLYMARKET_API_SECRET
    POLYMARKET_PASSPHRASE

It does not print secret values by default.
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client_v2 import ClobClient as V2ClobClient


ENV_PATH = Path(".env")
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
        raise RuntimeError("POLYMARKET_PK should be a 32-byte hex private key")
    return private_key


def read_env_file(path: Path) -> list[str]:
    if not path.exists():
        raise RuntimeError(".env file not found. Create it first and set POLYMARKET_PK.")
    return path.read_text(encoding="utf-8").splitlines()


def write_env_values(path: Path, updates: dict[str, str]) -> Path:
    lines = read_env_file(path)
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


def get_signature_type() -> int:
    raw_value = os.getenv("POLYMARKET_SIGNATURE_TYPE", "0").strip()
    try:
        signature_type = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("POLYMARKET_SIGNATURE_TYPE must be 0, 1, 2, or 3") from exc
    if signature_type not in {0, 1, 2, 3}:
        raise RuntimeError("POLYMARKET_SIGNATURE_TYPE must be 0, 1, 2, or 3 for this bot")
    return signature_type


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or derive Polymarket CLOB API credentials and save them to .env.",
    )
    parser.add_argument(
        "--print-secrets",
        action="store_true",
        help="Print the derived API credentials. Avoid this on shared screens.",
    )
    args = parser.parse_args()

    load_dotenv(dotenv_path=ENV_PATH, override=True)

    private_key = os.getenv("POLYMARKET_PK")
    if not private_key:
        raise RuntimeError("POLYMARKET_PK is missing in .env")

    private_key = normalize_private_key(private_key)
    signer_address = Account.from_key(private_key).address
    signature_type = get_signature_type()
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()

    updates: dict[str, str] = {
        "POLYMARKET_SIGNATURE_TYPE": str(signature_type),
    }

    if signature_type == 0:
        if funder and funder.lower() != signer_address.lower():
            raise RuntimeError(
                "POLYMARKET_FUNDER does not match the address derived from POLYMARKET_PK. "
                "For a normal MetaMask wallet, POLYMARKET_FUNDER must be the same public address.",
            )
        if not funder:
            funder = signer_address
            updates["POLYMARKET_FUNDER"] = signer_address
    elif not funder:
        raise RuntimeError("POLYMARKET_FUNDER is required for proxy, safe, or deposit wallet signature types")

    print(f"Signer address: {signer_address}")
    print(f"Funder address: {funder}")
    print(f"Signature type: {signature_type}")
    print("Creating or deriving CLOB API credentials...")

    if signature_type == 3:
        client = V2ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=POLYGON,
            signature_type=signature_type,
            funder=funder,
        )
        creds = client.create_or_derive_api_key()
    else:
        client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=POLYGON,
        )
        creds = client.create_or_derive_api_creds()

    updates.update(
        {
            "POLYMARKET_API_KEY": creds.api_key,
            "POLYMARKET_API_SECRET": creds.api_secret,
            "POLYMARKET_PASSPHRASE": creds.api_passphrase,
        },
    )
    backup_path = write_env_values(ENV_PATH, updates)

    print(f"Updated {ENV_PATH}")
    print(f"Backup written to {backup_path}")
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
