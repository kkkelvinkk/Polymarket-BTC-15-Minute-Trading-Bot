#!/usr/bin/env python3
"""Print the Polymarket CLOB collateral balance for the configured vault account."""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

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

from clob_units import parse_clob_units
from vault_store import DEFAULT_VAULT_FILE, load_vault_from_prompt


def normalize_private_key(private_key: str) -> str:
    return private_key if private_key.startswith("0x") else "0x" + private_key


def units_to_usdc(raw_units: int) -> Decimal:
    return Decimal(raw_units) / Decimal("1000000")


def required_units(response: dict[str, object], key: str) -> int:
    try:
        raw_value = response[key]
    except KeyError as exc:
        raise RuntimeError(f"CLOB balance response missing {key}: {response!r}") from exc
    return parse_clob_units(raw_value, key, response)


def print_balance_response(response: dict[str, object], unit_label: str) -> None:
    balance = units_to_usdc(required_units(response, "balance"))
    print(f"CLOB collateral balance: {balance:.6f} {unit_label}")

    if "allowance" in response:
        allowance = units_to_usdc(required_units(response, "allowance"))
        print(f"CLOB collateral allowance: {allowance:.6f} {unit_label}")
        return

    if "allowances" not in response:
        raise RuntimeError(f"CLOB balance response missing allowance data: {response!r}")

    allowances = response["allowances"]
    if not isinstance(allowances, dict) or len(allowances) == 0:
        raise RuntimeError(f"CLOB balance response missing allowance data: {response!r}")

    for spender, raw_allowance in allowances.items():
        allowance_units = parse_clob_units(raw_allowance, f"allowance for {spender}", response)
        allowance = units_to_usdc(allowance_units)
        print(f"CLOB allowance for {spender}: {allowance:.6f} {unit_label}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the Polymarket CLOB collateral balance for the configured vault account.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Ask Polymarket CLOB to refresh cached collateral balance and allowance before printing.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT_FILE,
        help="Encrypted credentials vault path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    vault = load_vault_from_prompt(args.vault)

    private_key = normalize_private_key(vault.private_key)
    signer_address = Account.from_key(private_key).address
    funder = vault.funder
    signature_type = vault.signature_type

    if signature_type == 3:
        creds = V2ApiCreds(
            api_key=vault.api_key,
            api_secret=vault.api_secret,
            api_passphrase=vault.passphrase,
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
            api_key=vault.api_key,
            api_secret=vault.api_secret,
            api_passphrase=vault.passphrase,
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
