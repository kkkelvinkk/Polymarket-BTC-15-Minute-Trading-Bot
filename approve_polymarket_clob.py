#!/usr/bin/env python3
"""Approve Polymarket CLOB exchange spending for the configured MetaMask EOA."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from decimal import Decimal

from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ApiCreds
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import AssetType
from py_clob_client.clob_types import BalanceAllowanceParams
from py_clob_client.constants import POLYGON


USDC_DECIMALS = Decimal("1000000")
MAX_UINT256 = 2**256 - 1


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing from .env")
    return value.strip()


def normalize_private_key(private_key: str) -> str:
    return private_key if private_key.startswith("0x") else "0x" + private_key


def normalize_address(address: str) -> str:
    address = address.strip()
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"Invalid address: {address}")
    return address


def pad_address(address: str) -> str:
    return normalize_address(address).lower().replace("0x", "").rjust(64, "0")


def pad_uint(value: int) -> str:
    return hex(value).replace("0x", "").rjust(64, "0")


def ensure_0x(value: str) -> str:
    return value if value.startswith("0x") else "0x" + value


def usdc_to_units(value: Decimal) -> int:
    return int((value * USDC_DECIMALS).to_integral_value())


def units_to_usdc(raw_units: int) -> Decimal:
    return Decimal(raw_units) / USDC_DECIMALS


def rpc_call(rpc_url: str, method: str, params: list[object]) -> object:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    ).encode()
    request = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "polymarket-clob-approval-helper",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read())
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def erc20_call(rpc_url: str, token: str, selector: str, *args: str) -> int:
    data = selector + "".join(args)
    result = rpc_call(
        rpc_url,
        "eth_call",
        [{"to": token, "data": data}, "latest"],
    )
    return int(str(result), 16)


def token_balance(rpc_url: str, token: str, owner: str) -> int:
    return erc20_call(rpc_url, token, "0x70a08231", pad_address(owner))


def token_allowance(rpc_url: str, token: str, owner: str, spender: str) -> int:
    return erc20_call(rpc_url, token, "0xdd62ed3e", pad_address(owner), pad_address(spender))


def native_balance(rpc_url: str, owner: str) -> int:
    result = rpc_call(rpc_url, "eth_getBalance", [owner, "latest"])
    return int(str(result), 16)


def build_approve_data(spender: str, amount_units: int) -> str:
    return "0x095ea7b3" + pad_address(spender) + pad_uint(amount_units)


def wait_for_receipt(rpc_url: str, tx_hash: str, timeout_seconds: int = 120) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = rpc_call(rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if result:
            return dict(result)
        time.sleep(3)
    raise TimeoutError(f"Timed out waiting for transaction receipt: {tx_hash}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Approve USDC.e collateral spending for the Polymarket CLOB exchange. "
            "Dry-run by default; pass --execute to broadcast."
        ),
    )
    parser.add_argument(
        "--amount",
        type=Decimal,
        help="USDC.e amount to approve. Defaults to the current wallet USDC.e balance.",
    )
    parser.add_argument(
        "--unlimited",
        action="store_true",
        help="Approve max uint256 instead of an exact amount. More convenient, less restrictive.",
    )
    parser.add_argument(
        "--spender",
        choices=("clob-api", "standard", "neg-risk", "both"),
        default="clob-api",
        help=(
            "Which Polymarket spender address to approve. "
            "'clob-api' uses the spender addresses reported by the CLOB balance endpoint."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Broadcast the approval transaction. Without this flag the script only prints a dry run.",
    )
    parser.add_argument(
        "--rpc-url",
        help="Polygon RPC URL. Required unless POLYGON_RPC_URL is set.",
    )
    return parser.parse_args()


def resolve_rpc_url(args: argparse.Namespace) -> str:
    rpc_url = args.rpc_url or os.getenv("POLYGON_RPC_URL")
    if not rpc_url:
        raise RuntimeError("Set --rpc-url or POLYGON_RPC_URL; no public RPC fallback is used")
    return rpc_url.strip()


def choose_spenders(client: ClobClient, spender_arg: str) -> list[tuple[str, str]]:
    standard = ("standard CLOB exchange", client.get_exchange_address(False))
    neg_risk = ("neg-risk CLOB exchange", client.get_exchange_address(True))
    if spender_arg == "standard":
        return [standard]
    if spender_arg == "neg-risk":
        return [neg_risk]
    return [standard, neg_risk]


def choose_clob_api_spenders(client: ClobClient, signature_type: int) -> list[tuple[str, str]]:
    response = client.get_balance_allowance(
        BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=signature_type,
        ),
    )
    allowances = response.get("allowances")
    if not isinstance(allowances, dict) or not allowances:
        raise RuntimeError(f"CLOB balance endpoint did not report spender allowances: {response}")
    return [(f"CLOB API spender #{index}", address) for index, address in enumerate(allowances, start=1)]


def main() -> int:
    args = parse_args()
    load_dotenv(dotenv_path=".env", override=True)

    private_key = normalize_private_key(required_env("POLYMARKET_PK"))
    signer_address = Account.from_key(private_key).address
    funder = normalize_address(required_env("POLYMARKET_FUNDER"))
    signature_type = int(required_env("POLYMARKET_SIGNATURE_TYPE"))

    if signature_type != 0:
        raise RuntimeError("This approval helper is only for MetaMask EOA mode: POLYMARKET_SIGNATURE_TYPE=0")
    if signer_address.lower() != funder.lower():
        raise RuntimeError("For EOA mode, POLYMARKET_FUNDER must match the POLYMARKET_PK address")

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

    rpc_url = resolve_rpc_url(args)
    chain_id_result = rpc_call(rpc_url, "eth_chainId", [])
    chain_id = int(str(chain_id_result), 16)
    if chain_id != POLYGON:
        raise RuntimeError(f"RPC is not Polygon mainnet: chain_id={chain_id}")

    collateral = client.get_collateral_address()
    owner_balance = token_balance(rpc_url, collateral, signer_address)
    gas_balance = native_balance(rpc_url, signer_address)
    if args.spender == "clob-api":
        spenders = choose_clob_api_spenders(client, signature_type)
    else:
        spenders = choose_spenders(client, args.spender)

    if args.amount is not None and args.unlimited:
        raise RuntimeError("Use either --amount or --unlimited, not both")
    if args.unlimited:
        approval_units = MAX_UINT256
        approval_label = "unlimited"
    elif args.amount is not None:
        if args.amount <= 0:
            raise RuntimeError("--amount must be greater than 0")
        approval_units = usdc_to_units(args.amount)
        approval_label = f"{units_to_usdc(approval_units):.6f} USDC.e"
    else:
        approval_units = owner_balance
        approval_label = f"{units_to_usdc(approval_units):.6f} USDC.e"

    print(f"Wallet: {signer_address}")
    print(f"Polygon RPC: {rpc_url}")
    print(f"Collateral token: {collateral}")
    print(f"Wallet collateral balance: {units_to_usdc(owner_balance):.6f} USDC.e")
    print(f"Wallet POL gas balance: {Decimal(gas_balance) / Decimal(10**18):.8f} POL")
    print(f"Approval amount: {approval_label}")

    if owner_balance == 0:
        raise RuntimeError("Wallet has 0 USDC.e collateral; nothing to approve")
    if not args.unlimited and approval_units > owner_balance:
        raise RuntimeError("Approval amount is greater than the wallet USDC.e balance")
    if gas_balance == 0:
        raise RuntimeError("Wallet has 0 POL. Add a small amount of Polygon POL for gas before approving.")

    for label, spender in spenders:
        current_allowance = token_allowance(rpc_url, collateral, signer_address, spender)
        print(f"\nSpender: {label}")
        print(f"Address: {spender}")
        print(f"Current allowance: {units_to_usdc(current_allowance):.6f} USDC.e")

        if current_allowance >= approval_units:
            print("No transaction needed: allowance is already high enough.")
            continue

        approve_data = build_approve_data(spender, approval_units)
        nonce = int(str(rpc_call(rpc_url, "eth_getTransactionCount", [signer_address, "pending"])), 16)
        gas_price = int(str(rpc_call(rpc_url, "eth_gasPrice", [])), 16)
        estimate_tx = {
            "from": signer_address,
            "to": collateral,
            "value": "0x0",
            "data": approve_data,
        }
        gas_estimate = int(str(rpc_call(rpc_url, "eth_estimateGas", [estimate_tx])), 16)
        gas_limit = int(gas_estimate * 1.25)
        gas_cost_pol = Decimal(gas_limit * gas_price) / Decimal(10**18)

        print(f"Estimated gas limit: {gas_limit}")
        print(f"Estimated gas cost: {gas_cost_pol:.8f} POL")

        if not args.execute:
            print("Dry run only. Re-run with --execute to broadcast this approval.")
            continue

        tx = {
            "chainId": POLYGON,
            "nonce": nonce,
            "to": collateral,
            "value": 0,
            "data": approve_data,
            "gas": gas_limit,
            "gasPrice": gas_price,
        }
        signed = Account.sign_transaction(tx, private_key)
        raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
        tx_hash = str(rpc_call(rpc_url, "eth_sendRawTransaction", [ensure_0x(raw_tx.hex())]))
        print(f"Broadcasted: {tx_hash}")
        print(f"Polygonscan: https://polygonscan.com/tx/{tx_hash}")

        receipt = wait_for_receipt(rpc_url, tx_hash)
        status = int(str(receipt.get("status", "0x0")), 16)
        if status != 1:
            raise RuntimeError(f"Approval transaction failed: {tx_hash}")
        updated_allowance = token_allowance(rpc_url, collateral, signer_address, spender)
        print(f"Confirmed. Updated allowance: {units_to_usdc(updated_allowance):.6f} USDC.e")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
