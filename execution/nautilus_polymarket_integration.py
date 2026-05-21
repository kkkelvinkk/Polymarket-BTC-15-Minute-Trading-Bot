import os
import asyncio
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger
from dotenv import load_dotenv

from execution.btc_market_slugs import current_btc_15m_slug, get_next_btc_15m_markets
from vault_store import (
    DEFAULT_VAULT_PATH,
    PolymarketVault,
    load_vault_from_prompt,
    refuse_secret_dotenv_keys,
    refuse_secret_environment_keys,
)

from nautilus_trader.adapters.polymarket import POLYMARKET
from nautilus_trader.adapters.polymarket import (
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
)
from nautilus_trader.adapters.polymarket.factories import (
    PolymarketLiveDataClientFactory,
    PolymarketLiveExecClientFactory,
)
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_nautilus_log_dir() -> str:
    raw = os.getenv("NAUTILUS_LOG_DIR")
    if raw is None or raw == "":
        raise RuntimeError("NAUTILUS_LOG_DIR must be set")
    return raw


class PolymarketBTCIntegration:
    """
    Integration layer between BTC strategy and Polymarket via Nautilus.
    
    This handles:
    - Nautilus node setup
    - Polymarket client configuration
    - Instrument loading
    - Order routing
    - Position tracking
    """
    
    def __init__(
        self,
        simulation_mode: bool = True,
        btc_market_condition_id: Optional[str] = None,
    ):
        """
        Initialize Polymarket integration.
        
        Args:
            simulation_mode: If True, don't place real orders
            btc_market_condition_id: Polymarket condition ID for BTC market
        """
        self.simulation_mode = simulation_mode
        self.btc_market_condition_id = btc_market_condition_id
        self._live_vault: Optional[PolymarketVault] = None
        if self.simulation_mode:
            load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
        else:
            refuse_secret_dotenv_keys(PROJECT_ROOT / ".env")
            load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
            refuse_secret_environment_keys()
            self._live_vault = load_vault_from_prompt(PROJECT_ROOT / DEFAULT_VAULT_PATH)
        
        # Nautilus components
        self.node: Optional[TradingNode] = None
        self.strategy: Optional[Strategy] = None
        
        # Track Polymarket instruments
        self.btc_instrument_id: Optional[InstrumentId] = None
        
        # Statistics
        self.orders_submitted = 0
        self.orders_filled = 0
        self.orders_rejected = 0
        
        mode = "SIMULATION" if simulation_mode else "LIVE TRADING"
        logger.info(f"Initialized Polymarket BTC Integration [{mode}]")
    
    async def start(self) -> bool:
        """
        Start the Nautilus trading node with Polymarket.
        
        Returns:
            True if started successfully
        """
        try:
            logger.info("="*80)
            logger.info("STARTING NAUTILUS-POLYMARKET INTEGRATION")
            logger.info("="*80)
            
            # Create Nautilus config
            config = self._create_nautilus_config()
            
            # Create trading node
            self.node = TradingNode(config=config)
            
            # Add Polymarket factories
            self.node.add_data_client_factory(POLYMARKET, PolymarketLiveDataClientFactory)
            self.node.add_exec_client_factory(POLYMARKET, PolymarketLiveExecClientFactory)
            
            # Build node
            logger.info("Building Nautilus node...")
            self.node.build()
            
            logger.info("✓ Nautilus node built successfully")
            
            # Start node asynchronously
            logger.info("Starting node (instruments loading)...")
            self.node.start()
            
            # Wait for instruments to load
            await asyncio.sleep(5)
            
            # Find BTC instrument
            found_instrument = await self._find_btc_instrument()
            if not found_instrument:
                raise RuntimeError("No BTC 15-min instrument loaded")
            
            logger.info("✓ Nautilus-Polymarket integration started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start integration: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _create_nautilus_config(self) -> TradingNodeConfig:
        """Create Nautilus trading node configuration."""
        
        # Get current and next BTC 15-min market slugs
        btc_markets = get_next_btc_15m_markets(count=2)  # Current + next market
        
        # Instrument provider config - use Gamma Markets API for faster filtering
        instrument_cfg = InstrumentProviderConfig(
            load_all=False,  # Only load specific markets
            use_gamma_markets=True,  # CRITICAL: Use Gamma API for slug filtering
            filters={
                "active": True,
                "closed": False,
                "archived": False,
                "slug": btc_markets,  # Load current 15-min BTC market(s)
            }
        )
        
        logger.info(f"Loading BTC 15-min markets: {btc_markets}")
        
        if self.simulation_mode:
            private_key = os.getenv("POLYMARKET_PK")
            api_key = os.getenv("POLYMARKET_API_KEY")
            api_secret = os.getenv("POLYMARKET_API_SECRET")
            passphrase = os.getenv("POLYMARKET_PASSPHRASE")
            extra_auth_config: dict[str, object] = {}
        else:
            if self._live_vault is None:
                raise RuntimeError("live vault was not loaded")
            private_key = self._live_vault.private_key
            api_key = self._live_vault.api_key
            api_secret = self._live_vault.api_secret
            passphrase = self._live_vault.passphrase
            extra_auth_config = {
                "signature_type": self._live_vault.signature_type,
                "funder": self._live_vault.funder,
            }

        # Polymarket data client config
        poly_data_cfg = PolymarketDataClientConfig(
            private_key=private_key,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            **extra_auth_config,
            instrument_config=instrument_cfg,
        )
        
        # Polymarket execution client config
        poly_exec_cfg = PolymarketExecClientConfig(
            private_key=private_key,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            **extra_auth_config,
            instrument_config=instrument_cfg,
        )
        
        # Trading node config
        node_config = TradingNodeConfig(
            environment="live",
            trader_id="BTC-15MIN-BOT-001",
            logging=LoggingConfig(
                log_level="INFO",
                log_directory=get_nautilus_log_dir(),
            ),
            data_engine=LiveDataEngineConfig(qsize=6000),
            exec_engine=LiveExecEngineConfig(qsize=6000),
            risk_engine=LiveRiskEngineConfig(
                bypass=self.simulation_mode,  # Bypass in simulation
            ),
            data_clients={POLYMARKET: poly_data_cfg},
            exec_clients={POLYMARKET: poly_exec_cfg},
        )
        
        return node_config
    
    async def _find_btc_instrument(self) -> bool:
        """
        Find the BTC 15-minute prediction market instrument.
        
        Returns:
            True if found
        """
        if not self.node:
            return False
        
        logger.info("Searching for BTC 15-min prediction market instruments...")
        
        # Get all instruments from cache
        instruments = self.node.cache.instruments()
        
        logger.info(f"Found {len(instruments)} total instruments")
        
        # Search for BTC 15-min instruments
        btc_instruments = []
        for instrument in instruments:
            instrument_str = str(instrument.id)
            if '.POLYMARKET' in instrument_str:
                # Log all Polymarket instruments for debugging
                logger.debug(f"  Polymarket instrument: {instrument.id}")
                
                # Check if it's a BTC market
                # Instruments follow pattern: {condition_id}-{token_id}.POLYMARKET
                # We loaded by slug, so any instrument here should be our BTC 15-min market
                btc_instruments.append(instrument)
                logger.info(f"  Found BTC 15-min instrument: {instrument.id}")
        
        if not btc_instruments:
            logger.error("No BTC 15-min instruments found!")
            logger.error("This usually means:")
            logger.error("  1. The current 15-min market hasn't been created yet")
            logger.error("  2. Credentials are incorrect")
            logger.error("  3. Gamma Markets API is not enabled")
            return False
        
        # Use the first BTC instrument (should be the current 15-min market)
        self.btc_instrument_id = btc_instruments[0].id
        logger.info(f"✓ Using BTC 15-min instrument: {self.btc_instrument_id}")
        
        # Log market details
        instrument = btc_instruments[0]
        logger.info(f"  Market details:")
        logger.info(f"    Price precision: {instrument.price_precision}")
        logger.info(f"    Size precision: {instrument.size_precision}")
        logger.info(f"    Min quantity: {instrument.min_quantity}")
        
        return True
    
    async def place_market_order(
        self,
        side: str,  # "buy" or "sell"
        size_usd: Decimal,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Place market order on Polymarket.
        
        Args:
            side: "buy" or "sell"
            size_usd: Size in USD
            metadata: Order metadata (signal info, etc.)
            
        Returns:
            Order ID if successful
        """
        if self.simulation_mode:
            logger.info(f"[SIMULATION] Would place {side.upper()} order for ${size_usd}")
            return f"sim_order_{datetime.now().timestamp()}"
        raise RuntimeError(
            "Legacy PolymarketBTCIntegration live order submission is disabled; "
            "use bot.py live order pipeline"
        )
    
    async def place_limit_order(
        self,
        side: str,
        size_usd: Decimal,
        limit_price: Decimal,  # 0-1 range
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Place limit order on Polymarket.
        
        Args:
            side: "buy" or "sell"
            size_usd: Size in USD
            limit_price: Limit price (0-1 range)
            metadata: Order metadata
            
        Returns:
            Order ID if successful
        """
        if self.simulation_mode:
            logger.info(f"[SIMULATION] Would place {side.upper()} limit @ ${limit_price:.4f}")
            return f"sim_order_{datetime.now().timestamp()}"
        raise RuntimeError(
            "Legacy PolymarketBTCIntegration live order submission is disabled; "
            "use bot.py live order pipeline"
        )
    
    def get_open_positions(self) -> list:
        """Get open positions from Nautilus."""
        if not self.node:
            return []
        
        return list(self.node.cache.positions_open())
    
    def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        if not self.node:
            return {"USDC": 0.0}
        
        # Get account state from Nautilus cache
        account = self.node.cache.account(self.node.trader.id.get_tag())
        
        if not account:
            return {"USDC": 0.0}
        
        return {
            "USDC": float(account.balance_total().as_decimal()),
            "free": float(account.balance_free().as_decimal()),
            "locked": float(account.balance_locked().as_decimal()),
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get trading statistics."""
        return {
            "simulation_mode": self.simulation_mode,
            "orders_submitted": self.orders_submitted,
            "orders_filled": self.orders_filled,
            "orders_rejected": self.orders_rejected,
            "instrument_id": str(self.btc_instrument_id) if self.btc_instrument_id else None,
            "node_running": self.node is not None,
        }
    
    async def stop(self) -> None:
        """Stop the integration."""
        if self.node:
            logger.info("Stopping Nautilus node...")
            await self.node.stop_async()
            await self.node.dispose_async()
            self.node = None
        
        logger.info("Polymarket integration stopped")


# Singleton instance
_integration_instance: Optional[PolymarketBTCIntegration] = None

def get_polymarket_integration(
    simulation_mode: bool = True,
    btc_market_condition_id: Optional[str] = None,
) -> PolymarketBTCIntegration:
    """Get singleton Polymarket integration."""
    global _integration_instance
    
    if _integration_instance is None:
        _integration_instance = PolymarketBTCIntegration(
            simulation_mode=simulation_mode,
            btc_market_condition_id=btc_market_condition_id,
        )
    
    return _integration_instance
