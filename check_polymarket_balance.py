#!/usr/bin/env python3
"""Print the Polymarket CLOB collateral balance for the configured .env account."""

from __future__ import annotations

import argparse
import os
from decimal import Decimal

from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ApiCreds
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType
from py_clob_client.clob_types import BalanceAllowanceParams
from py_clob_client.constants import POLYGON
from py_clob_client_v2 import ApiCreds as V2ApiCreds
from py_clob_client_v2 import AssetType as V2AssetType
from py_clob_client_v2 import BalanceAllowanceParams as V2BalanceAllowanceParams
from py_clob_client_v2 import ClobClient as V2ClobClient
from py_clob_client_v2.config import get_contract_config as get_v2_contract_config


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing from .env")
    return value.strip()


def normalize_private_key(private_key: str) -> str:
    return private_key if private_key.startswith("0x") else "0x" + private_key


def units_to_usdc(raw_units: int) -> Decimal:
    return Decimal(raw_units) / Decimal("1000000")


def print_balance_response(response: dict[str, object], unit_label: str) -> None:
    balance = units_to_usdc(int(response.get("balance", 0)))
    print(f"CLOB collateral balance: {balance:.6f} {unit_label}")

    if "allowance" in response:
        allowance = units_to_usdc(int(response.get("allowance", 0)))
        print(f"CLOB collateral allowance: {allowance:.6f} {unit_label}")
        return

    allowances = response.get("allowances")
    if isinstance(allowances, dict):
        for spender, raw_allowance in allowances.items():
            allowance = units_to_usdc(int(raw_allowance))
            print(f"CLOB allowance for {spender}: {allowance:.6f} {unit_label}")
        return

    print("CLOB collateral allowance: unavailable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the Polymarket CLOB collateral balance for the configured .env account.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Ask Polymarket CLOB to refresh cached collateral balance and allowance before printing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(dotenv_path=".env", override=True)

    private_key = normalize_private_key(required_env("POLYMARKET_PK"))
    signer_address = Account.from_key(private_key).address
    funder = required_env("POLYMARKET_FUNDER")
    signature_type = int(required_env("POLYMARKET_SIGNATURE_TYPE"))

    if signature_type == 3:
        creds = V2ApiCreds(
            api_key=required_env("POLYMARKET_API_KEY"),
            api_secret=required_env("POLYMARKET_API_SECRET"),
            api_passphrase=required_env("POLYMARKET_PASSPHRASE"),
        )
        client = V2ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=POLYGON,
            signature_type=signature_type,
            funder=funder,
            creds=creds,
        )
        params = V2BalanceAllowanceParams(asset_type=V2AssetType.COLLATERAL)
        collateral_address = get_v2_contract_config(POLYGON).collateral
        exchange_address = get_v2_contract_config(POLYGON).exchange_v2
        unit_label = "USDC-equivalent"
    else:
        creds = ApiCreds(
            api_key=required_env("POLYMARKET_API_KEY"),
            api_secret=required_env("POLYMARKET_API_SECRET"),
            api_passphrase=required_env("POLYMARKET_PASSPHRASE"),
        )
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=POLYGON,
            signature_type=signature_type,
            funder=funder,
            creds=creds,
        )
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=signature_type,
        )
        collateral_address = client.get_collateral_address()
        exchange_address = client.get_exchange_address()
        unit_label = "USDC.e"

    if args.sync:
        client.update_balance_allowance(params)

    response = client.get_balance_allowance(
        params,
    )

    print(f"Signer address: {signer_address}")
    print(f"Funder address: {funder}")
    print(f"Signature type: {signature_type}")
    print(f"CLOB collateral token: {collateral_address}")
    print(f"CLOB exchange address: {exchange_address}")
    print_balance_response(response, unit_label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
