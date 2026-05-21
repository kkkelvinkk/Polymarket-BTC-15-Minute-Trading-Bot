"""Legacy Polymarket CLOB client wrapper."""

import hashlib
import json
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from loguru import logger

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams
from py_clob_client_v2 import ApiCreds as V2ApiCreds
from py_clob_client_v2 import AssetType as V2AssetType
from py_clob_client_v2 import BalanceAllowanceParams as V2BalanceAllowanceParams
from py_clob_client_v2 import ClobClient as V2ClobClient
from py_clob_client_v2.clob_types import OrderPayload as V2OrderPayload
from clob_units import parse_clob_units
from vault_store import DEFAULT_VAULT_FILE, PolymarketVault, load_vault_from_prompt
POLYMARKET_AVAILABLE = True


class PolymarketClient:
    """
    Production Polymarket API client.
    
    Features:
    - Real order placement
    - Live market data
    - Position tracking
    - Balance management
    """
    
    def __init__(
        self,
        private_key: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
        funder: Optional[str] = None,
        signature_type: int = 1,
        chain_id: int = 137,  # Polygon mainnet
        testnet: bool = False,
    ):
        """
        Initialize Polymarket client.
        
        Args:
            private_key: Ethereum private key (without 0x prefix)
            api_key: Polymarket API key
            api_secret: Polymarket API secret
            api_passphrase: Polymarket API passphrase
            chain_id: 137 for Polygon mainnet, 80002 for Amoy testnet
            testnet: Use testnet mode
        """
        self.private_key = private_key
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.funder = funder
        self.signature_type = signature_type
        
        self.chain_id = chain_id
        self.testnet = testnet
        
        # Client instance
        self.client: Optional[object] = None
        self._connected = False
        
        # Market cache
        self._markets_cache: Dict[str, Any] = {}
        
        # Check if SDK available
        if not POLYMARKET_AVAILABLE:
            logger.error("Polymarket SDK not available. Install: pip install py-clob-client")
            return
        
        # Validate credentials
        if not self.private_key:
            logger.error("Polymarket private key was not provided")
        if not self.api_key:
            logger.error("Polymarket API key was not provided")
        
        mode = "TESTNET" if testnet else "MAINNET"
        logger.info(f"Initialized Polymarket Client [{mode}] Chain ID: {chain_id}")
    
    async def connect(self) -> bool:
        """
        Connect to Polymarket API.
        
        Returns:
            True if connected successfully
        """
        if not POLYMARKET_AVAILABLE:
            logger.error("Cannot connect: SDK not installed")
            return False
        
        if (
            not self.private_key
            or not self.api_key
            or not self.api_secret
            or not self.api_passphrase
        ):
            logger.error("Cannot connect: Missing credentials")
            return False
        
        self._connected = False
        self.client = None
        try:
            self.client = self._build_clob_client()
            balance = await self._get_balance_internal()
            
            if balance is not None:
                self._connected = True
                logger.info(f"✓ Connected to Polymarket CLOB")
                logger.info(f"  Balance: ${balance.get('USDC', 0):.2f} USDC")
                return True
            else:
                logger.error("Failed to verify connection")
                self.client = None
                return False
                
        except Exception as e:
            self.client = None
            logger.error(f"Failed to connect to Polymarket: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _host(self) -> str:
        if self.testnet:
            return "https://clob-testnet.polymarket.com"
        return "https://clob.polymarket.com"

    def _build_clob_client(self) -> object:
        if self.signature_type == 3:
            return V2ClobClient(
                self._host(),
                key=self.private_key,
                chain_id=self.chain_id,
                signature_type=self.signature_type,
                funder=self.funder,
                creds=V2ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.api_passphrase,
                ),
            )
        return ClobClient(
            self._host(),
            key=self.private_key,
            chain_id=self.chain_id,
            signature_type=self.signature_type,
            funder=self.funder,
            creds=ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase,
            ),
        )

    def _collateral_balance_params(self) -> object:
        if self.signature_type == 3:
            return V2BalanceAllowanceParams(asset_type=V2AssetType.COLLATERAL)
        return BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self.signature_type,
        )
    
    async def disconnect(self) -> None:
        """Disconnect from API."""
        self._connected = False
        self.client = None
        logger.info("Disconnected from Polymarket")
    
    async def get_btc_market(self) -> Optional[Dict[str, Any]]:
        """
        Get BTC prediction market details.
        
        Returns:
            Market information dict
        """
        if not self.client:
            logger.error("Client not connected")
            return None
        
        try:
            logger.warning("BTC market lookup not fully implemented")
            return {
                "condition_id": "BTC_PRICE_PREDICTION",
                "market_id": "btc_market",
                "question": "Will BTC be above $65000?",
                "end_date": "2026-03-01",
            }
            
        except Exception as e:
            logger.error(f"Error fetching BTC market: {e}")
            return None
    
    async def get_market_price(self, token_id: str) -> Optional[Decimal]:
        """
        Get current market price for a token.
        
        Args:
            token_id: Token ID (outcome token)
            
        Returns:
            Current price (0-1 for binary markets)
        """
        if not self.is_connected:
            raise RuntimeError("Polymarket client must be connected before reading prices")

        book = self.client.get_order_book(token_id)
        bids, _asks = self._orderbook_entries(book)
        if len(bids) == 0:
            return None
        return Decimal(str(self._order_summary_value(bids[0], "price")))
    
    async def get_orderbook(self, token_id: str) -> Optional[Dict[str, Any]]:
        """
        Get order book for token.
        
        Args:
            token_id: Token ID
            
        Returns:
            Order book with bids and asks
        """
        if not self.is_connected:
            raise RuntimeError("Polymarket client must be connected before reading order books")

        book = self.client.get_order_book(token_id)
        bids, asks = self._orderbook_entries(book)
        return {
            "timestamp": datetime.now(),
            "token_id": token_id,
            "bids": [self._normalize_order_summary(bid) for bid in bids],
            "asks": [self._normalize_order_summary(ask) for ask in asks],
        }

    def _orderbook_entries(self, book: object) -> tuple[list, list]:
        if self.signature_type == 3:
            return book["bids"], book["asks"]
        return book.bids, book.asks

    def _normalize_order_summary(self, summary: object) -> Dict[str, Decimal]:
        return {
            "price": Decimal(str(self._order_summary_value(summary, "price"))),
            "size": Decimal(str(self._order_summary_value(summary, "size"))),
        }

    def _order_summary_value(self, summary: object, key: str) -> str:
        if self.signature_type == 3:
            return summary[key]
        return getattr(summary, key)
    
    async def place_order(
        self,
        token_id: str,
        side: str,  # "buy" or "sell"
        size: Decimal,
        price: Optional[Decimal] = None,
        order_type: str = "GTC",  # GTC, FOK, GTD
    ) -> Optional[str]:
        """
        Place order on market.
        
        Args:
            token_id: Token ID to trade
            side: "buy" or "sell"
            size: Order size (number of outcome tokens)
            price: Limit price (0-1 range), None for market order
            order_type: Order type (GTC, FOK, GTD)
            
        Returns:
            Order ID if successful
        """
        raise RuntimeError(
            "Legacy PolymarketClient live order submission is disabled; "
            "use bot.py live order pipeline"
        )
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancelled successfully
        """
        if not self.is_connected:
            raise RuntimeError("Polymarket client must be connected before cancelling orders")
        if self.signature_type == 3:
            response = self.client.cancel_order(V2OrderPayload(orderID=str(order_id)))
        else:
            response = self.client.cancel(order_id)
        if self._cancel_response_succeeded(response, str(order_id)):
            logger.info(f"Order cancelled: {order_id}")
            return True
        return False

    def _cancel_response_succeeded(self, response: object, order_id: str) -> bool:
        if not isinstance(response, dict):
            raise RuntimeError(f"unexpected cancel response type: {type(response).__name__}")
        canceled = response["canceled"]
        not_canceled = response["not_canceled"]
        if not isinstance(canceled, list) or not isinstance(not_canceled, dict):
            raise RuntimeError(f"unexpected cancel response shape: {response!r}")
        return order_id in canceled and order_id not in not_canceled
    
    async def get_open_orders(self) -> List[Dict[str, Any]]:
        """
        Get all open orders.
        
        Returns:
            List of open orders
        """
        if not self.is_connected:
            raise RuntimeError("Polymarket client must be connected before listing open orders")
        if self.signature_type == 3:
            orders = self.client.get_open_orders()
        else:
            orders = self.client.get_orders()
        return [self._normalize_open_order(order) for order in orders]

    def _normalize_open_order(self, order: dict[str, Any]) -> Dict[str, Any]:
        return {
            "order_id": order["id"],
            "token_id": order["asset_id"],
            "side": order["side"],
            "price": Decimal(str(order["price"])),
            "size": Decimal(str(order["original_size"])),
            "filled": Decimal(str(order["size_matched"])),
            "timestamp": datetime.fromtimestamp(int(order["created_at"])),
        }
    
    async def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get current positions.
        
        Returns:
            List of positions
        """
        raise RuntimeError(
            "Legacy PolymarketClient position lookup is disabled; "
            "use bot.py live position accounting"
        )
    
    async def _get_balance_internal(self) -> Optional[Dict[str, Decimal]]:
        """Internal method to get balance."""
        if not self.client:
            return None
        
        try:
            response = self.client.get_balance_allowance(
                self._collateral_balance_params(),
            )
            balance_units = parse_clob_units(response["balance"], "balance", response)
            return {"USDC": Decimal(balance_units) / Decimal("1000000")}
            
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return None
    
    async def get_balance(self) -> Dict[str, Decimal]:
        """
        Get account balance.
        
        Returns:
            Balance dict with USDC and token balances
        """
        return await self._get_balance_internal() or {}
    
    async def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent trades.
        
        Args:
            limit: Maximum trades to return
            
        Returns:
            List of recent trades
        """
        if not self.client:
            return []
        
        try:
            trades = self.client.get_trades()
            
            recent_trades = []
            for trade in trades[:limit]:
                recent_trades.append({
                    "trade_id": trade["id"],
                    "order_id": trade["order_id"],
                    "token_id": trade["asset_id"],
                    "side": trade["side"],
                    "price": Decimal(str(trade["price"])),
                    "size": Decimal(str(trade["size"])),
                    "timestamp": datetime.fromisoformat(trade["timestamp"]),
                })
            
            return recent_trades
            
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            return []
    
    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected and self.client is not None


# Singleton instance
_polymarket_client_instance = None
_polymarket_client_cache_key = None
_polymarket_client_source_path = None


def _client_cache_key(vault: PolymarketVault, testnet: bool) -> tuple[bool, str]:
    credential_shape = json.dumps(
        {
            "private_key": vault.private_key,
            "api_key": vault.api_key,
            "api_secret": vault.api_secret,
            "passphrase": vault.passphrase,
            "funder": vault.funder,
            "signature_type": vault.signature_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        testnet,
        hashlib.sha256(credential_shape.encode("utf-8")).hexdigest(),
    )


def _client_from_vault(vault: PolymarketVault, testnet: bool) -> PolymarketClient:
    return PolymarketClient(
        private_key=vault.private_key,
        api_key=vault.api_key,
        api_secret=vault.api_secret,
        api_passphrase=vault.passphrase,
        funder=vault.funder,
        signature_type=vault.signature_type,
        testnet=testnet,
    )

def get_polymarket_client(
    testnet: bool = False,
    force_new: bool = False,
    vault: Optional[PolymarketVault] = None,
    vault_path=DEFAULT_VAULT_FILE,
) -> PolymarketClient:
    """
    Get singleton Polymarket client.
    
    Args:
        testnet: Use testnet mode
        force_new: Force creation of new instance
        vault: Preloaded encrypted-vault credentials
        vault_path: Vault path to load when vault is not provided
    """
    global _polymarket_client_instance, _polymarket_client_cache_key, _polymarket_client_source_path

    source_path = Path(vault_path).expanduser().resolve(strict=False)
    if (
        vault is None
        and not force_new
        and _polymarket_client_instance is not None
        and _polymarket_client_source_path == source_path
        and _polymarket_client_instance.testnet == testnet
    ):
        return _polymarket_client_instance

    selected_vault = vault
    if selected_vault is None:
        selected_vault = load_vault_from_prompt(source_path)
    else:
        source_path = None

    cache_key = _client_cache_key(selected_vault, testnet)
    if (
        _polymarket_client_instance is None
        or force_new
        or cache_key != _polymarket_client_cache_key
        or source_path != _polymarket_client_source_path
    ):
        _polymarket_client_instance = _client_from_vault(selected_vault, testnet)
        _polymarket_client_cache_key = cache_key
        _polymarket_client_source_path = source_path
    
    return _polymarket_client_instance
