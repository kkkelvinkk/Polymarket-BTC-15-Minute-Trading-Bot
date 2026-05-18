import asyncio
import copy
import json
import os
import fcntl
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import math
from decimal import Decimal
import time
import threading
import traceback
from dataclasses import dataclass
from typing import Any, List, Optional, Dict
import random

# Add project to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


try:
    from patch_gamma_markets import apply_gamma_markets_patch, verify_patch
    patch_applied = apply_gamma_markets_patch()
    if patch_applied:
        verify_patch()
    else:
        print("ERROR: Failed to apply gamma_market patch")
        sys.exit(1)
except ImportError as e:
    print(f"ERROR: Could not import patch module: {e}")
    print("Make sure patch_gamma_markets.py is in the same directory")
    sys.exit(1)

# Now import Nautilus
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.adapters.polymarket import POLYMARKET
from nautilus_trader.adapters.polymarket import (
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
)
from nautilus_trader.adapters.polymarket.factories import (
    PolymarketLiveDataClientFactory,
    PolymarketLiveExecClientFactory,
)
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.identifiers import InstrumentId, ClientOrderId
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.data import QuoteTick

from dotenv import load_dotenv
from loguru import logger
import redis

logger.remove()
logger.add(
    sys.stderr,
    format="{time:YYYY-MM-DDTHH:mm:ss.SSSSSS!UTC}Z | {level:<8} | {name}:{function}:{line} - {message}",
)

# Import our phases
from core.strategy_brain.signal_processors.spike_detector import SpikeDetectionProcessor
from core.strategy_brain.signal_processors.sentiment_processor import SentimentProcessor
from core.strategy_brain.signal_processors.divergence_processor import PriceDivergenceProcessor
from core.strategy_brain.signal_processors.orderbook_processor import OrderBookImbalanceProcessor
from core.strategy_brain.signal_processors.tick_velocity_processor import TickVelocityProcessor
from core.strategy_brain.signal_processors.deribit_pcr_processor import DeribitPCRProcessor
from core.strategy_brain.fusion_engine.signal_fusion import get_fusion_engine
from execution.risk_engine import get_risk_engine
from monitoring.performance_tracker import get_performance_tracker
from monitoring.grafana_exporter import get_grafana_exporter
from feedback.learning_engine import get_learning_engine
load_dotenv()
from patch_market_orders import (
    apply_market_order_patch,
    register_auto_redeem_handler,
    unregister_auto_redeem_handler,
)
patch_applied = apply_market_order_patch()
if patch_applied:
    logger.info("Market order patch applied successfully")
else:
    logger.error("Market order patch failed")

from polymarket_v2_compat import apply_polymarket_v2_patch
v2_patch_applied = apply_polymarket_v2_patch()
if v2_patch_applied:
    logger.info("Polymarket CLOB v2 compatibility patch applied successfully")
else:
    logger.error("Polymarket CLOB v2 compatibility patch failed")


# =============================================================================
# CONSTANTS
# =============================================================================
QUOTE_STABILITY_REQUIRED = 3      # Need only 3 valid ticks to be stable (faster startup)
QUOTE_MIN_SPREAD = 0.001          # Both bid AND ask must be at least this
MARKET_INTERVAL_SECONDS = 900     # 15-minute markets
MAX_SEEN_AUTO_REDEEM_EVENTS = 10_000
MAX_PENDING_AUTO_REDEEM_EVENTS = 500
PENDING_AUTO_REDEEM_RETENTION = timedelta(days=7)
DEFAULT_LIVE_SETTLEMENT_GRACE_SECONDS = 3600
_ledger_path = Path(os.getenv("LIVE_TRADE_LEDGER_PATH", "live_trades.json"))
LIVE_TRADE_LEDGER_PATH = _ledger_path if _ledger_path.is_absolute() else project_root / _ledger_path
AUTO_REDEEM_TOKEN_HINT_KEYS = (
    "asset_id",
    "assetId",
    "token_id",
    "tokenId",
    "clobTokenId",
    "clob_token_id",
)
AUTO_REDEEM_OUTCOME_HINT_KEYS = (
    "outcome",
    "redeemed_outcome",
    "redeemedOutcome",
    "winning_outcome",
    "winningOutcome",
)
AUTO_REDEEM_SIDE_HINT_KEYS = ("side",)
AUTO_REDEEM_OUTCOME_VALUES = {
    "up": "up",
    "yes": "up",
    "down": "down",
    "no": "down",
}


class SettlementLedgerError(RuntimeError):
    """Raised when live settlement ledger state cannot be trusted."""


@dataclass
class PaperTrade:
    """Track decision-only simulation observations."""
    timestamp: datetime
    direction: str
    size_usd: float
    price: float
    signal_score: float
    signal_confidence: float
    outcome: str = "PENDING"

    def to_dict(self):
        return {
            'timestamp': self.timestamp.isoformat(),
            'direction': self.direction,
            'size_usd': self.size_usd,
            'price': self.price,
            'signal_score': self.signal_score,
            'signal_confidence': self.signal_confidence,
            'outcome': self.outcome,
        }


_last_redis_init_error: Optional[Exception] = None


def init_redis():
    """Initialize Redis connection for simulation mode control."""
    global _last_redis_init_error
    _last_redis_init_error = None
    try:
        redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 2)),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
        redis_client.ping()
        logger.info("Redis connection established")
        return redis_client
    except Exception as e:
        _last_redis_init_error = e
        logger.warning(f"Redis connection failed: {e}")
        logger.warning("Redis control disabled; startup mode will be used")
        return None


def get_required_env(name: str, description: str) -> str:
    """Read a required environment variable without logging secret values."""
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(f"Environment variable '{name}' not set. {description}")


def get_optional_env(name: str, default: str) -> str:
    """Read an optional environment variable, treating empty strings as unset."""
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def get_polymarket_runtime_credentials(simulation: bool) -> dict[str, str | int]:
    """Return Polymarket credentials, using dummy values only for simulation mode."""
    if simulation:
        dummy_private_key = "0x" + "1" * 64
        dummy_funder = "0x" + "2" * 40
        signature_raw = get_optional_env("POLYMARKET_SIGNATURE_TYPE", "0")
        try:
            signature_type = int(signature_raw)
        except ValueError:
            signature_type = 0
        if signature_type not in {0, 1, 2, 3}:
            signature_type = 0
        return {
            "private_key": get_optional_env("POLYMARKET_PK", dummy_private_key),
            "funder": get_optional_env("POLYMARKET_FUNDER", dummy_funder),
            "api_key": get_optional_env("POLYMARKET_API_KEY", "simulation-api-key"),
            "api_secret": get_optional_env("POLYMARKET_API_SECRET", "simulation-api-secret"),
            "passphrase": get_optional_env("POLYMARKET_PASSPHRASE", "simulation-passphrase"),
            "signature_type": signature_type,
        }

    return {
        "private_key": get_required_env(
            "POLYMARKET_PK",
            "Set this to the private key for the wallet that signs Polymarket orders.",
        ),
        "funder": get_required_env(
            "POLYMARKET_FUNDER",
            "Set this to the public wallet/deposit address that holds funds on Polymarket.",
        ),
        "api_key": get_required_env(
            "POLYMARKET_API_KEY",
            "Set this to your Polymarket CLOB API key.",
        ),
        "api_secret": get_required_env(
            "POLYMARKET_API_SECRET",
            "Set this to your Polymarket CLOB API secret.",
        ),
        "passphrase": get_required_env(
            "POLYMARKET_PASSPHRASE",
            "Set this to your Polymarket CLOB API passphrase.",
        ),
        "signature_type": get_polymarket_signature_type(),
    }


def get_polymarket_signature_type() -> int:
    """Read the Polymarket signature type from the environment."""
    raw_value = get_required_env(
        "POLYMARKET_SIGNATURE_TYPE",
        "Use 0 for a normal MetaMask/EOA wallet, 1 for an existing Polymarket proxy wallet, "
        "2 for a Gnosis Safe wallet, or 3 for a deposit wallet.",
    )
    try:
        signature_type = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("POLYMARKET_SIGNATURE_TYPE must be an integer: 0, 1, 2, or 3") from exc
    if signature_type not in {0, 1, 2, 3}:
        raise RuntimeError("POLYMARKET_SIGNATURE_TYPE must be 0, 1, 2, or 3")
    return signature_type


def get_market_buy_usd() -> Decimal:
    """Read the configured USD spend per market buy."""
    raw_value = os.getenv("MARKET_BUY_USD", "1.00")
    try:
        amount = Decimal(raw_value)
    except Exception as exc:
        raise RuntimeError("MARKET_BUY_USD must be a positive decimal amount") from exc
    if amount <= Decimal("0"):
        raise RuntimeError("MARKET_BUY_USD must be greater than 0")
    return amount.quantize(Decimal("0.01"))


def get_env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment flag without silently accepting typos."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean: true/false, 1/0, yes/no, or on/off")


def get_min_signal_confidence() -> float:
    """Minimum fused signal confidence required before following market price."""
    raw_value = os.getenv("MIN_SIGNAL_CONFIDENCE", "0.70")
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError("MIN_SIGNAL_CONFIDENCE must be a decimal between 0 and 1") from exc
    if not 0 <= value <= 1:
        raise RuntimeError("MIN_SIGNAL_CONFIDENCE must be between 0 and 1")
    return value


class IntegratedBTCStrategy(Strategy):
    """
    Integrated BTC Strategy - FIXED VERSION
    - Subscribes immediately at startup
    - Forces stability for first trade
    - Correct timing for market switching
    """

    def __init__(
        self,
        redis_client=None,
        enable_grafana=True,
        test_mode=False,
        simulation_mode: bool = True,
    ):
        super().__init__()

        self.bot_start_time = datetime.now(timezone.utc)
        self.restart_after_minutes = 90

        # Nautilus
        self.instrument_id = None
        self.redis_client = redis_client
        self.current_simulation_mode = simulation_mode
        self.live_execution_enabled = not simulation_mode

        # Store ALL BTC instruments
        self.all_btc_instruments: List[Dict] = []
        self.current_instrument_index: int = -1
        self.next_switch_time: Optional[datetime] = None

        # Quote-stability tracking
        self._stable_tick_count = 0
        self._market_stable = False
        self._last_instrument_switch = None
        
        # =========================================================================
        # FIX 1: Force first trade by setting last_trade_time to -1
        # =========================================================================
        self.last_trade_time = -1  # Force first trade immediately!
        self._waiting_for_market_open = False  # True when waiting for a future market to open
        self._last_bid_ask = None  # (bid_decimal, ask_decimal) from last tick, for liquidity checks
        self._last_no_bid_ask = None  # (bid_decimal, ask_decimal) for NO token when subscribed
        self._last_trade_wait_log_key = None
        self._decision_in_progress = False
        self._submitted_positions = {}
        self._open_live_trades: Dict[str, Dict[str, Any]] = {}
        self._settled_live_trades: List[Dict[str, Any]] = []
        self._seen_auto_redeem_events = set()
        self._seen_auto_redeem_event_order: List[str] = []
        self._pending_auto_redeem_events: Dict[str, Dict[str, Any]] = {}
        self._settlement_lock = threading.RLock()
        self._settlement_ledger_blocked_reason: Optional[str] = None
        self._ledger_lock_file = None
        self._auto_redeem_registered = False
        self._auto_redeem_handler = self._handle_auto_redeem_event
        self._acquire_live_trade_ledger_lock()
        try:
            self._load_live_trade_ledger()
        except Exception:
            self._release_live_trade_ledger_lock()
            raise

        # Tick buffer: rolling 90s of ticks for TickVelocityProcessor
        from collections import deque
        self._tick_buffer: deque = deque(maxlen=2000)

        # YES token id for the current market (set in _load_all_btc_instruments)
        self._yes_token_id: Optional[str] = None

        # Phase 4: Signal Processors
        self.spike_detector = SpikeDetectionProcessor(
            spike_threshold=0.05,       # FIXED: was 0.15 (too high for probabilities)
            lookback_periods=20,
        )
        self.sentiment_processor = SentimentProcessor(
            extreme_fear_threshold=25,
            extreme_greed_threshold=75,
        )
        self.divergence_processor = PriceDivergenceProcessor(
            divergence_threshold=0.05,
        )
        self.orderbook_processor = OrderBookImbalanceProcessor(
            imbalance_threshold=0.30,   # 30% skew to signal
            min_book_volume=50.0,       # ignore illiquid books
        )
        self.tick_velocity_processor = TickVelocityProcessor(
            velocity_threshold_60s=0.015,  # 1.5% move in 60s
            velocity_threshold_30s=0.010,  # 1.0% move in 30s
        )
        self.deribit_pcr_processor = DeribitPCRProcessor(
            bullish_pcr_threshold=1.20,
            bearish_pcr_threshold=0.70,
            max_days_to_expiry=2,
            cache_seconds=300,          # refresh every 5 min
        )

        # Phase 4: Signal Fusion — update weights for 6 processors
        self.fusion_engine = get_fusion_engine()
        # Rebalanced weights (must sum ≤ 1.0; higher = more influence)
        self.fusion_engine.set_weight("OrderBookImbalance", 0.30)  # best real-time signal
        self.fusion_engine.set_weight("TickVelocity",       0.25)  # fast poly momentum
        self.fusion_engine.set_weight("PriceDivergence",    0.18)  # spot momentum
        self.fusion_engine.set_weight("SpikeDetection",     0.12)  # mean reversion
        self.fusion_engine.set_weight("DeribitPCR",         0.10)  # institutional sentiment
        self.fusion_engine.set_weight("SentimentAnalysis",  0.05)  # daily F&G (weak)

        # Phase 5: Risk Management
        self.risk_engine = get_risk_engine()
        self._rehydrate_settled_daily_risk()
        self._rehydrate_open_settlement_risk()

        # Phase 6: Performance Tracking
        self.performance_tracker = get_performance_tracker()

        # Phase 7: Learning Engine
        self.learning_engine = get_learning_engine()

        # Phase 6: Grafana (optional)
        if enable_grafana:
            self.grafana_exporter = get_grafana_exporter()
        else:
            self.grafana_exporter = None

        # Price history
        self.price_history = []
        self.max_history = 100

        # Decision-only simulation observation tracker
        self.paper_trades: List[PaperTrade] = []

        self.test_mode = test_mode

        if test_mode:
            logger.info("=" * 80)
            logger.info("  TEST MODE ACTIVE - Trading every minute!")
            logger.info("=" * 80)

        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY INITIALIZED - FIXED VERSION")
        logger.info("  Phase 4: Signal processors ready")
        logger.info("  Phase 5: Risk engine ready")
        logger.info("  Phase 6: Performance tracking ready")
        logger.info("  Phase 7: Learning engine ready")
        logger.info(f"  ${float(get_market_buy_usd()):.2f} per trade maximum")
        logger.info(
            "  Signal confirmation: "
            f"{'enabled' if get_env_bool('REQUIRE_SIGNAL_CONFIRMATION', True) else 'disabled'} "
            f"(min confidence {get_min_signal_confidence():.0%})"
        )
        logger.info("=" * 80)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seconds_to_next_15min_boundary(self) -> float:
        """Return seconds until the next 15-minute UTC boundary."""
        now_ts = datetime.now(timezone.utc).timestamp()
        next_boundary = (math.floor(now_ts / MARKET_INTERVAL_SECONDS) + 1) * MARKET_INTERVAL_SECONDS
        return next_boundary - now_ts

    def _is_quote_valid(self, bid, ask) -> bool:
        """Return True only when BOTH bid and ask are present and make sense."""
        if bid is None or ask is None:
            return False
        try:
            b = float(bid)
            a = float(ask)
        except (TypeError, ValueError):
            return False
        if b < QUOTE_MIN_SPREAD or a < QUOTE_MIN_SPREAD:
            return False
        if b > 0.999 or a > 0.999:
            return False
        return True

    def _reset_stability(self, reason: str = ""):
        """Mark the market as unstable and reset the counter."""
        if self._market_stable:
            logger.warning(f"Market stability RESET{' – ' + reason if reason else ''}")
        self._market_stable = False
        self._stable_tick_count = 0

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    async def check_simulation_mode(self) -> bool:
        """Check Redis for current simulation mode."""
        if not self.live_execution_enabled:
            if not self.current_simulation_mode:
                logger.warning("Live mode requested in a simulation-only process; forcing SIMULATION")
            self.current_simulation_mode = True
            return True

        if not self.redis_client:
            raise RuntimeError("Live-enabled process requires Redis mode control")
        try:
            sim_mode = self.redis_client.get('btc_trading:simulation_mode')
            if sim_mode is None:
                raise RuntimeError("Redis simulation mode is missing")

            if sim_mode == '1':
                redis_simulation = True
            elif sim_mode == '0':
                redis_simulation = False
            else:
                raise RuntimeError(f"Invalid Redis simulation mode value: {sim_mode!r}")

            if redis_simulation != self.current_simulation_mode:
                self.current_simulation_mode = redis_simulation
                mode_text = "SIMULATION" if redis_simulation else "LIVE TRADING"
                logger.warning(f"Trading mode changed to: {mode_text}")
                if not redis_simulation:
                    logger.warning("LIVE TRADING ACTIVE - Real money at risk!")
            return redis_simulation
        except Exception as e:
            raise RuntimeError("Failed to check Redis simulation mode") from e

    # ------------------------------------------------------------------
    # Live settlement ledger
    # ------------------------------------------------------------------

    def _block_live_settlement_ledger(self, reason: str) -> None:
        """Mark live settlement accounting as unsafe until the process is restarted."""
        if self._settlement_ledger_blocked_reason == reason:
            return
        self._settlement_ledger_blocked_reason = reason
        logger.error(f"LIVE SETTLEMENT LEDGER BLOCKED: {reason}")

    def _acquire_live_trade_ledger_lock(self) -> None:
        """Hold an exclusive process lock while this bot owns the live ledger."""
        lock_path = LIVE_TRADE_LEDGER_PATH.with_name(LIVE_TRADE_LEDGER_PATH.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=True)
        lock_file = lock_path.open("r+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(str(os.getpid()))
            lock_file.flush()
            os.fsync(lock_file.fileno())
        except BlockingIOError as exc:
            lock_file.close()
            reason = f"live settlement ledger lock is already held: {lock_path}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason) from exc
        except Exception as exc:
            lock_file.close()
            reason = f"could not acquire live settlement ledger lock {lock_path}: {exc}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason) from exc
        self._ledger_lock_file = lock_file
        logger.info(f"Live settlement ledger: {LIVE_TRADE_LEDGER_PATH} (lock: {lock_path})")

    def _release_live_trade_ledger_lock(self) -> None:
        """Release the live ledger process lock."""
        lock_file = self._ledger_lock_file
        self._ledger_lock_file = None
        if not lock_file:
            return
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()

    def _jsonable(self, value):
        """Convert internal ledger values to JSON-safe values."""
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): self._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._jsonable(v) for v in value]
        return value

    def _normalize_seen_auto_redeem_state(self, seen_events, seen_order):
        """Return a bounded, internally consistent auto_redeem dedupe index."""
        seen_events = set(seen_events)
        seen_order = list(seen_order)
        if len(seen_events) > MAX_SEEN_AUTO_REDEEM_EVENTS:
            seen_order = [
                event_key
                for event_key in seen_order
                if event_key in seen_events
            ][-MAX_SEEN_AUTO_REDEEM_EVENTS:]
            seen_events = set(seen_order)
        if set(seen_order) != seen_events or len(seen_order) != len(seen_events):
            raise SettlementLedgerError("seen auto_redeem event index is inconsistent")
        return seen_events, seen_order

    def _prepare_live_trade_ledger_state(
        self,
        open_trades,
        settled_trades,
        seen_events,
        seen_order,
        pending_events,
    ) -> Dict[str, Any]:
        """Prepare a ledger state for writing without mutating current bot state."""
        normalized_seen, normalized_seen_order = self._normalize_seen_auto_redeem_state(
            seen_events,
            seen_order,
        )
        normalized_pending = copy.deepcopy(pending_events)
        self._prune_pending_auto_redeem_events(
            normalized_pending,
            datetime.now(timezone.utc),
        )
        return {
            "open": dict(open_trades),
            "settled": list(settled_trades),
            "seen": normalized_seen,
            "seen_order": normalized_seen_order,
            "pending": normalized_pending,
        }

    def _write_live_trade_ledger_state(self, state: Dict[str, Any]) -> None:
        """Write a prepared live-trade ledger state to disk."""
        data = {
            "open": self._jsonable(dict(state["open"])),
            "settled": self._jsonable(list(state["settled"][-500:])),
            "seen_auto_redeem_events": list(state["seen_order"]),
            "pending_auto_redeem_events": self._jsonable(dict(state["pending"])),
        }
        payload = json.dumps(data, indent=2, sort_keys=True)

        LIVE_TRADE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = LIVE_TRADE_LEDGER_PATH.with_name(LIVE_TRADE_LEDGER_PATH.name + ".tmp")

        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, LIVE_TRADE_LEDGER_PATH)

    def _save_live_trade_ledger_state(
        self,
        open_trades,
        settled_trades,
        seen_events,
        seen_order,
        pending_events,
    ) -> Dict[str, Any]:
        """Persist the supplied ledger state and return the normalized state that was written."""
        with self._settlement_lock:
            try:
                state = self._prepare_live_trade_ledger_state(
                    open_trades=open_trades,
                    settled_trades=settled_trades,
                    seen_events=seen_events,
                    seen_order=seen_order,
                    pending_events=pending_events,
                )
                self._write_live_trade_ledger_state(state)
                return state
            except Exception as e:
                reason = f"failed to save live trade ledger {LIVE_TRADE_LEDGER_PATH}: {e}"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason) from e

    def _save_live_trade_ledger(self) -> None:
        """Persist open/settled live trades so restarts do not lose settlement mapping."""
        with self._settlement_lock:
            state = self._save_live_trade_ledger_state(
                open_trades=self._open_live_trades,
                settled_trades=self._settled_live_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
            )
            self._seen_auto_redeem_events = set(state["seen"])
            self._seen_auto_redeem_event_order = list(state["seen_order"])
            self._pending_auto_redeem_events = dict(state["pending"])

    def _try_save_live_trade_ledger(self, context: str) -> bool:
        """Persist the live ledger without propagating framework-callback exceptions."""
        try:
            self._save_live_trade_ledger()
            return True
        except SettlementLedgerError as exc:
            logger.error(f"{context}; live trading blocked until ledger is repaired: {exc}")
            return False

    def _try_save_live_trade_ledger_state(
        self,
        context: str,
        open_trades,
        settled_trades,
        seen_events,
        seen_order,
        pending_events,
    ) -> Optional[Dict[str, Any]]:
        """Persist a candidate ledger state without propagating framework-callback exceptions."""
        try:
            return self._save_live_trade_ledger_state(
                open_trades=open_trades,
                settled_trades=settled_trades,
                seen_events=seen_events,
                seen_order=seen_order,
                pending_events=pending_events,
            )
        except SettlementLedgerError as exc:
            logger.error(f"{context}; live trading blocked until ledger is repaired: {exc}")
            return None

    def _load_live_trade_ledger(self) -> None:
        """Load pending live trades from the previous bot process, if any."""
        with self._settlement_lock:
            if not LIVE_TRADE_LEDGER_PATH.exists():
                return
            try:
                data = json.loads(LIVE_TRADE_LEDGER_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise SettlementLedgerError("live trade ledger root is not a JSON object")
                self._open_live_trades = dict(data.get("open", {}))
                self._settled_live_trades = list(data.get("settled", []))
                self._seen_auto_redeem_event_order = list(data.get("seen_auto_redeem_events", []))
                self._seen_auto_redeem_events = set(self._seen_auto_redeem_event_order)
                if (
                    set(self._seen_auto_redeem_event_order) != self._seen_auto_redeem_events
                    or len(self._seen_auto_redeem_event_order) != len(self._seen_auto_redeem_events)
                ):
                    raise SettlementLedgerError("seen auto_redeem event index is inconsistent")
                self._pending_auto_redeem_events = dict(data.get("pending_auto_redeem_events", {}))
                self._prune_pending_auto_redeem_events_locked(datetime.now(timezone.utc))
                if self._open_live_trades:
                    logger.info(
                        f"Loaded live settlement ledger from {LIVE_TRADE_LEDGER_PATH.name}: "
                        f"{len(self._open_live_trades)} open trade(s) pending settlement"
                    )
            except Exception as e:
                reason = f"failed to load live trade ledger {LIVE_TRADE_LEDGER_PATH}: {e}"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason) from e

    def _rehydrate_open_settlement_risk(self) -> None:
        """Restore unresolved filled trades into the risk engine after a restart."""
        with self._settlement_lock:
            open_items = list(self._open_live_trades.items())
        for order_id, meta in open_items:
            try:
                self.risk_engine.add_position(
                    position_id=order_id,
                    size=Decimal(str(meta.get("size", "0"))),
                    entry_price=Decimal(str(meta.get("entry_price", "1"))),
                    direction="buy_yes" if meta.get("direction") == "long" else "buy_no",
                    count_trade=False,
                )
            except Exception as e:
                logger.warning(f"Failed to restore open settlement risk for {order_id}: {e}")

    def _rehydrate_settled_daily_risk(self) -> None:
        """Restore same-day realized settlement P&L after a process restart."""
        today = datetime.now().astimezone().date()
        daily_pnl = Decimal("0")
        daily_trades = 0

        with self._settlement_lock:
            settled_items = list(self._settled_live_trades)

        for trade in settled_items:
            settled_at = self._parse_utc_datetime(trade.get("settled_at"))
            if not settled_at or settled_at.astimezone().date() != today:
                continue
            pnl_value = trade.get("pnl")
            if pnl_value in (None, "", "UNKNOWN"):
                continue
            try:
                daily_pnl += Decimal(str(pnl_value))
                daily_trades += 1
            except Exception:
                continue

        if daily_trades and hasattr(self.risk_engine, "restore_daily_stats"):
            self.risk_engine.restore_daily_stats(daily_pnl, daily_trades)

    def _unresolved_settlement_unknowns(self) -> List[Dict[str, Any]]:
        """Return settled records that still need manual or REST reconciliation."""
        with self._settlement_lock:
            unresolved = [
                trade
                for trade in self._settled_live_trades
                if trade.get("needs_reconciliation") is True
                or trade.get("settlement_source") == "SETTLEMENT_UNKNOWN"
            ]
            if self._settlement_ledger_blocked_reason:
                unresolved.append(
                    {
                        "order_id": "LIVE_SETTLEMENT_LEDGER_BLOCKED",
                        "settlement_source": "LEDGER_BLOCKED",
                        "needs_reconciliation": True,
                        "unknown_reason": self._settlement_ledger_blocked_reason,
                    }
                )
            return unresolved

    def _current_market_metadata(self) -> Dict[str, Any]:
        """Return the current market metadata, if loaded."""
        if 0 <= self.current_instrument_index < len(self.all_btc_instruments):
            return self.all_btc_instruments[self.current_instrument_index]
        return {}

    def _parse_event_time(self, payload: Dict[str, Any]) -> Optional[datetime]:
        """Parse a Polymarket event timestamp without fabricating a substitute."""
        timestamp = payload.get("timestamp")
        try:
            if timestamp is not None:
                raw = int(str(timestamp))
                if raw > 10_000_000_000:
                    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
                return datetime.fromtimestamp(raw, tz=timezone.utc)
        except Exception:
            return None
        return None

    def _parse_utc_datetime(self, value) -> Optional[datetime]:
        """Parse a stored ISO datetime into timezone-aware UTC."""
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _as_decimal(self, value) -> Decimal:
        """Convert Nautilus numeric wrappers or plain values to Decimal."""
        if hasattr(value, "as_decimal"):
            return value.as_decimal()
        return Decimal(str(value))

    def _payload_trade_token_matches(self, payload: Dict[str, Any], meta: Dict[str, Any]) -> bool:
        """Match token/outcome details when Polymarket includes them in the payload."""
        payload_token = str(self._first_payload_value(payload, AUTO_REDEEM_TOKEN_HINT_KEYS) or "")
        if payload_token:
            return payload_token == str(meta.get("token_id") or "")

        payload_outcome = self._payload_outcome_hint(payload)
        if payload_outcome:
            direction = str(meta.get("direction") or "").lower()
            if direction == "long":
                return payload_outcome == "up"
            if direction == "short":
                return payload_outcome == "down"
            return False

        return True

    def _require_auto_redeem_token_hint(self) -> bool:
        """Whether to require token/outcome on auto_redeem before auto-settling."""
        return get_env_bool("REQUIRE_AUTO_REDEEM_TOKEN_HINT", True)

    def _payload_has_token_hint(self, payload: Dict[str, Any]) -> bool:
        """Return True if a settlement payload identifies the winning token/side."""
        return (
            self._first_payload_value(payload, AUTO_REDEEM_TOKEN_HINT_KEYS) is not None
            or self._payload_outcome_hint(payload) is not None
        )

    def _payload_outcome_hint(self, payload: Dict[str, Any]) -> Optional[str]:
        """Return a normalized winning outcome hint, or None for non-outcome side values."""
        for key in AUTO_REDEEM_OUTCOME_HINT_KEYS + AUTO_REDEEM_SIDE_HINT_KEYS:
            raw_value = payload.get(key)
            if raw_value in (None, ""):
                continue
            normalized = AUTO_REDEEM_OUTCOME_VALUES.get(str(raw_value).strip().lower())
            if normalized:
                return normalized
        return None

    def _first_payload_value(self, payload: Dict[str, Any], keys: tuple[str, ...]):
        """Return the first non-empty payload value for an explicit field list."""
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        return None

    def _trade_match_key(self, meta: Dict[str, Any]) -> str:
        """Return the most specific token/side key available for ambiguity checks."""
        return str(meta.get("token_id") or meta.get("trade_label") or meta.get("direction") or "")

    def _trade_payout_units(self, meta: Dict[str, Any]) -> Decimal:
        """Maximum possible payout units for this bot trade."""
        try:
            units = Decimal(str(meta.get("filled_qty") or meta.get("estimated_tokens") or "0"))
        except Exception:
            units = Decimal("0")
        return max(units, Decimal("0"))

    def _extract_token_id_from_instrument_id(self, instrument_id) -> str:
        """Extract the CLOB token id from a Nautilus Polymarket instrument id."""
        raw_id = str(instrument_id)
        without_suffix = raw_id.split('.')[0] if '.' in raw_id else raw_id
        return without_suffix.split('-')[-1] if '-' in without_suffix else without_suffix

    def _infer_polymarket_token_outcome(self, instrument, token_id: str) -> str:
        """Infer whether a Polymarket instrument is the Up/Yes or Down/No token."""
        info = getattr(instrument, "info", {}) or {}
        for key in ("outcome", "token_outcome", "side"):
            value = info.get(key)
            if value:
                return str(value).strip().lower()

        for token in info.get("tokens", []) or []:
            token_value = (
                token.get("token_id")
                or token.get("asset_id")
                or token.get("id")
                or token.get("clobTokenId")
            )
            if str(token_value) == str(token_id):
                return str(token.get("outcome") or token.get("name") or "").strip().lower()

        return ""

    def _auto_redeem_event_key(self, payload: Dict[str, Any]) -> str:
        """Build a dedupe key granular enough for batched redeem transactions."""
        tx_key = str(payload.get("txn_hash") or f"no-tx:{payload.get('timestamp') or ''}")
        market_key = str(payload.get("condition_id") or payload.get("slug") or "")
        token_key = str(
            self._first_payload_value(payload, AUTO_REDEEM_TOKEN_HINT_KEYS)
            or self._payload_outcome_hint(payload)
            or ""
        )
        amount_key = self._normalized_auto_redeem_amount_key(payload.get("amount"))
        return "|".join((tx_key, market_key, token_key, amount_key))

    def _normalized_auto_redeem_amount_key(self, amount) -> str:
        """Normalize decimal-equivalent amount strings for auto_redeem dedupe keys."""
        if amount in (None, ""):
            return ""
        try:
            return format(Decimal(str(amount)).normalize(), "f")
        except Exception:
            return str(amount)

    def _mark_auto_redeem_seen(self, event_key: str) -> None:
        """Record a redeem event as processed only after successful settlement."""
        if event_key not in self._seen_auto_redeem_events:
            self._seen_auto_redeem_events.add(event_key)
            self._seen_auto_redeem_event_order.append(event_key)
        self._pending_auto_redeem_events.pop(event_key, None)

    def _snapshot_settlement_state(self) -> Dict[str, Any]:
        """Snapshot mutable settlement state for rollback before durable save."""
        return {
            "open": copy.deepcopy(self._open_live_trades),
            "settled": copy.deepcopy(self._settled_live_trades),
            "seen": set(self._seen_auto_redeem_events),
            "seen_order": list(self._seen_auto_redeem_event_order),
            "pending": copy.deepcopy(self._pending_auto_redeem_events),
        }

    def _restore_settlement_state(self, snapshot: Dict[str, Any]) -> None:
        """Restore settlement state after a failed transactional ledger save."""
        self._open_live_trades = copy.deepcopy(snapshot["open"])
        self._settled_live_trades = copy.deepcopy(snapshot["settled"])
        self._seen_auto_redeem_events = set(snapshot["seen"])
        self._seen_auto_redeem_event_order = list(snapshot["seen_order"])
        self._pending_auto_redeem_events = copy.deepcopy(snapshot["pending"])

    def _keep_auto_redeem_pending_after_failed_save(
        self,
        event_key: str,
        payload: Dict[str, Any],
        reason: str,
    ) -> None:
        """Keep a redeem retryable in memory when the durable ledger write failed."""
        pending_payload = dict(payload)
        pending_payload["_pending_since"] = (
            pending_payload.get("_pending_since")
            or datetime.now(timezone.utc).isoformat()
        )
        pending_payload["_pending_reason"] = reason
        self._pending_auto_redeem_events[event_key] = pending_payload

    def _prune_pending_auto_redeem_events(
        self,
        pending_events: Dict[str, Dict[str, Any]],
        now: datetime,
    ) -> int:
        """Drop stale pending settlement events and enforce the pending-event cap."""
        dropped = 0
        normalized: List[tuple[str, datetime]] = []

        for event_key, payload in list(pending_events.items()):
            pending_since = self._parse_utc_datetime(payload.get("_pending_since"))
            if pending_since is None:
                logger.warning(f"Pending auto_redeem event {event_key} has no valid _pending_since; keeping it")
                continue

            if now - pending_since > PENDING_AUTO_REDEEM_RETENTION:
                pending_events.pop(event_key, None)
                dropped += 1
                continue

            normalized.append((event_key, pending_since))

        if len(normalized) > MAX_PENDING_AUTO_REDEEM_EVENTS:
            keep = {
                event_key
                for event_key, _pending_since in sorted(
                    normalized,
                    key=lambda item: item[1],
                    reverse=True,
                )[:MAX_PENDING_AUTO_REDEEM_EVENTS]
            }
            for event_key in list(pending_events):
                if event_key not in keep:
                    pending_events.pop(event_key, None)
                    dropped += 1

        if dropped:
            logger.warning(
                f"Dropped {dropped} stale/excess pending auto_redeem event(s); "
                f"remaining={len(pending_events)}"
            )

        return dropped

    def _prune_pending_auto_redeem_events_locked(self, now: datetime) -> int:
        """Drop stale pending settlement events from current bot state."""
        return self._prune_pending_auto_redeem_events(self._pending_auto_redeem_events, now)

    def _store_pending_auto_redeem_event(
        self,
        event_key: str,
        payload: Dict[str, Any],
        reason: str,
    ) -> None:
        """Persist an auto_redeem event that could not be safely matched yet."""
        pending_payload = dict(payload)
        pending_payload["_pending_since"] = pending_payload.get("_pending_since") or datetime.now(timezone.utc).isoformat()
        pending_payload["_pending_reason"] = reason
        if event_key in self._pending_auto_redeem_events:
            logger.debug(f"Overwriting existing pending auto_redeem event {event_key}")
        self._pending_auto_redeem_events[event_key] = pending_payload
        logger.warning(
            "Stored auto_redeem for retry/reconciliation: "
            f"{reason} (slug={payload.get('slug')}, condition_id={payload.get('condition_id')}, "
            f"amount={payload.get('amount')})"
        )
        self._try_save_live_trade_ledger("Failed to persist pending auto_redeem event")

    def _retry_pending_auto_redeems(self, reason: str) -> None:
        """Retry pending redeem events after fills or settlement-state changes."""
        with self._settlement_lock:
            dropped = self._prune_pending_auto_redeem_events_locked(datetime.now(timezone.utc))
            pending_payloads = list(self._pending_auto_redeem_events.values())
        if dropped:
            if not self._try_save_live_trade_ledger("Failed to persist pending auto_redeem pruning"):
                return
        if pending_payloads:
            logger.info(f"Retrying {len(pending_payloads)} pending auto_redeem event(s): {reason}")
        for payload in pending_payloads:
            self._handle_auto_redeem_event(payload, store_pending=False)

    def _matching_open_live_trades(self, payload: Dict[str, Any]) -> List[str]:
        """Find pending live trades that correspond to an auto_redeem payload."""
        slug = str(payload.get("slug") or "")
        condition_id = str(payload.get("condition_id") or "").lower()
        matches = []
        for order_id, meta in self._open_live_trades.items():
            meta_slug = str(meta.get("slug") or "")
            meta_condition = str(meta.get("condition_id") or "").lower()
            market_matches = (slug and meta_slug == slug) or (condition_id and meta_condition == condition_id)
            if market_matches and self._payload_trade_token_matches(payload, meta):
                matches.append(order_id)
        return matches

    def _matching_unknown_settled_trades(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find prior timeout-unknown settlements that can be corrected by a late redeem."""
        slug = str(payload.get("slug") or "")
        condition_id = str(payload.get("condition_id") or "").lower()
        matches = []
        for trade in self._settled_live_trades:
            if trade.get("settlement_source") != "SETTLEMENT_UNKNOWN":
                continue
            meta_slug = str(trade.get("slug") or "")
            meta_condition = str(trade.get("condition_id") or "").lower()
            market_matches = (slug and meta_slug == slug) or (condition_id and meta_condition == condition_id)
            if market_matches and self._payload_trade_token_matches(payload, trade):
                matches.append(trade)
        return matches

    def _matches_are_ambiguous(self, payload: Dict[str, Any], metas: List[Dict[str, Any]]) -> bool:
        """Avoid assigning wallet-level payouts when both sides could match."""
        if self._payload_has_token_hint(payload):
            return False
        match_keys = {self._trade_match_key(meta) for meta in metas if self._trade_match_key(meta)}
        return len(match_keys) > 1

    def _allocated_auto_redeem_payouts(
        self,
        payout: Decimal,
        matches: List[tuple[str, Dict[str, Any]]],
    ) -> Dict[str, Decimal]:
        """Allocate wallet-level auto_redeem payout across bot trades, capped by bot tokens."""
        if not matches:
            return {}

        expected_by_order = {
            order_id: self._trade_payout_units(meta)
            for order_id, meta in matches
        }
        total_expected = sum(expected_by_order.values(), Decimal("0"))
        if total_expected <= 0:
            if payout > 0:
                raise SettlementLedgerError("cannot allocate positive auto_redeem payout without known token units")
            return {order_id: Decimal("0") for order_id, _ in matches}

        capped_payout = min(payout, total_expected)
        if payout > total_expected:
            logger.warning(
                "auto_redeem payout exceeds tracked bot tokens; capping allocation "
                f"from ${float(payout):.6f} to ${float(capped_payout):.6f}"
            )

        return {
            order_id: capped_payout * expected_by_order[order_id] / total_expected
            for order_id, _ in matches
        }

    def _unknown_positive_auto_redeem_units_reason(
        self,
        payout: Decimal,
        matches: List[tuple[str, Dict[str, Any]]],
    ) -> Optional[str]:
        """Return a manual-review reason when positive payout units are unknown."""
        if payout <= 0:
            return None
        unknown_order_ids = [
            order_id
            for order_id, meta in matches
            if self._trade_payout_units(meta) <= 0
        ]
        if not unknown_order_ids:
            return None
        return (
            "positive auto_redeem payout matched trade(s) with unknown filled token units: "
            + ", ".join(unknown_order_ids)
        )

    def _record_settled_live_trade(
        self,
        order_id: str,
        meta: Dict[str, Any],
        payout: Decimal,
        exit_time: datetime,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        event_key: Optional[str] = None,
        save: bool = True,
        record_accounting: bool = True,
    ) -> None:
        """Record final P&L for a filled live trade."""
        with self._settlement_lock:
            size = Decimal(str(meta.get("size", "0")))
            entry_price = Decimal(str(meta.get("entry_price", "0")))
            filled_qty = Decimal(str(meta.get("filled_qty") or meta.get("estimated_tokens") or "0"))
            if entry_price <= 0 and filled_qty > 0 and size > 0:
                entry_price = size / filled_qty
            if entry_price <= 0:
                entry_price = Decimal("1")
            if filled_qty > 0:
                exit_price = payout / filled_qty
            else:
                exit_price = Decimal("1") if payout > 0 else Decimal("0")

            pnl = payout - size
            entry_time = (
                self._parse_utc_datetime(meta.get("filled_at"))
                or self._parse_utc_datetime(meta.get("submitted_at"))
                or exit_time
            )

            settled = dict(meta)
            settled.update(
                {
                    "order_id": order_id,
                    "settled_at": exit_time.isoformat(),
                    "settlement_source": source,
                    "payout": str(payout),
                    "pnl": str(pnl),
                    "exit_price": str(exit_price),
                    "needs_reconciliation": False,
                    "auto_redeem": payload or {},
                    "auto_redeem_event_key": event_key,
                }
            )
            self._settled_live_trades.append(settled)
            self._open_live_trades.pop(order_id, None)
            if save:
                if not self._try_save_live_trade_ledger("Failed to persist settled live trade"):
                    return
            if record_accounting:
                self._record_settlement_accounting(
                    order_id=order_id,
                    meta=meta,
                    payout=payout,
                    exit_time=exit_time,
                    source=source,
                    payload=payload or {},
                    entry_price=entry_price,
                    exit_price=exit_price,
                    size=size,
                    pnl=pnl,
                    entry_time=entry_time,
                )

            if record_accounting:
                logger.info("=" * 80)
                logger.info("LIVE TRADE SETTLED")
                logger.info(f"  Order: {order_id}")
                logger.info(f"  Market: {meta.get('slug')}")
                logger.info(f"  Side: {meta.get('trade_label')}")
                logger.info(f"  Cost: ${float(size):.2f}")
                logger.info(f"  Payout: ${float(payout):.6f}")
                logger.info(f"  Realized P&L: ${float(pnl):+.6f}")
                logger.info(f"  Source: {source}")
                logger.info("=" * 80)

    def _record_settlement_accounting(
        self,
        order_id: str,
        meta: Dict[str, Any],
        payout: Decimal,
        exit_time: datetime,
        source: str,
        payload: Dict[str, Any],
        entry_price: Decimal,
        exit_price: Decimal,
        size: Decimal,
        pnl: Decimal,
        entry_time: datetime,
    ) -> None:
        """Book risk/performance accounting after the settlement ledger is durable."""
        try:
            token_buy_direction = "buy_yes" if meta.get("direction") == "long" else "buy_no"
            self.performance_tracker.record_trade(
                trade_id=order_id,
                direction=token_buy_direction,
                entry_price=entry_price,
                exit_price=exit_price,
                size=size,
                entry_time=entry_time,
                exit_time=exit_time,
                signal_score=float(meta.get("signal_score", 0.0)),
                signal_confidence=float(meta.get("signal_confidence", 0.0)),
                metadata={
                    "market_direction": meta.get("direction"),
                    "trade_label": meta.get("trade_label"),
                    "slug": meta.get("slug"),
                    "condition_id": meta.get("condition_id"),
                    "instrument_id": meta.get("instrument_id"),
                    "payout": str(payout),
                    "source": source,
                    "auto_redeem": payload or {},
                },
            )
        except Exception as e:
            logger.warning(f"Failed to record performance trade for {order_id}: {e}")

        try:
            risk_pnl = self.risk_engine.remove_position(order_id, exit_price)
            if risk_pnl is None and hasattr(self.risk_engine, "record_realized_pnl"):
                self.risk_engine.record_realized_pnl(
                    pnl,
                    source=f"polymarket_{source}",
                    metadata={"order_id": order_id, "slug": meta.get("slug")},
                )
        except Exception as e:
            logger.warning(f"Failed to record risk P&L for {order_id}: {e}")

    def _book_settlement_accounting_from_record(
        self,
        order_id: str,
        meta: Dict[str, Any],
        payout: Decimal,
        exit_time: datetime,
        source: str,
        payload: Dict[str, Any],
    ) -> None:
        """Compute and book settlement accounting after the ledger save succeeds."""
        size = Decimal(str(meta.get("size", "0")))
        entry_price = Decimal(str(meta.get("entry_price", "0")))
        filled_qty = Decimal(str(meta.get("filled_qty") or meta.get("estimated_tokens") or "0"))
        if entry_price <= 0 and filled_qty > 0 and size > 0:
            entry_price = size / filled_qty
        if entry_price <= 0:
            entry_price = Decimal("1")
        exit_price = payout / filled_qty if filled_qty > 0 else (Decimal("1") if payout > 0 else Decimal("0"))
        pnl = payout - size
        entry_time = (
            self._parse_utc_datetime(meta.get("filled_at"))
            or self._parse_utc_datetime(meta.get("submitted_at"))
            or exit_time
        )
        self._record_settlement_accounting(
            order_id=order_id,
            meta=meta,
            payout=payout,
            exit_time=exit_time,
            source=source,
            payload=payload,
            entry_price=entry_price,
            exit_price=exit_price,
            size=size,
            pnl=pnl,
            entry_time=entry_time,
        )
        banner = "LIVE TRADE SETTLEMENT CORRECTED" if source == "late_auto_redeem" else "LIVE TRADE SETTLED"
        logger.info("=" * 80)
        logger.info(banner)
        logger.info(f"  Order: {order_id}")
        logger.info(f"  Market: {meta.get('slug')}")
        logger.info(f"  Side: {meta.get('trade_label')}")
        logger.info(f"  Cost: ${float(size):.2f}")
        logger.info(f"  Payout: ${float(payout):.6f}")
        logger.info(f"  Realized P&L: ${float(pnl):+.6f}")
        logger.info(f"  Source: {source}")
        logger.info("=" * 80)

    def _record_late_auto_redeem_correction(
        self,
        trade: Dict[str, Any],
        payout: Decimal,
        exit_time: datetime,
        payload: Dict[str, Any],
        event_key: Optional[str] = None,
        save: bool = True,
        record_accounting: bool = True,
    ) -> None:
        """Correct a previous SETTLEMENT_UNKNOWN record when a delayed redeem arrives."""
        order_id = str(trade.get("order_id") or "")
        if not order_id:
            logger.warning("Late auto_redeem matched a settled trade without order_id; ignoring")
            return

        size = Decimal(str(trade.get("size", "0")))
        filled_qty = self._trade_payout_units(trade)
        entry_price = Decimal(str(trade.get("entry_price", "0") or "0"))
        if entry_price <= 0 and filled_qty > 0 and size > 0:
            entry_price = size / filled_qty
        if entry_price <= 0:
            entry_price = Decimal("1")
        exit_price = payout / filled_qty if filled_qty > 0 else (Decimal("1") if payout > 0 else Decimal("0"))
        pnl = payout - size
        entry_time = (
            self._parse_utc_datetime(trade.get("filled_at"))
            or self._parse_utc_datetime(trade.get("submitted_at"))
            or exit_time
        )

        trade.update(
            {
                "settled_at": exit_time.isoformat(),
                "settlement_source": "late_auto_redeem",
                "corrected_from": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": False,
                "payout": str(payout),
                "pnl": str(pnl),
                "exit_price": str(exit_price),
                "auto_redeem": payload,
                "auto_redeem_event_key": event_key,
            }
        )
        if save:
            if not self._try_save_live_trade_ledger("Failed to persist late settlement correction"):
                return
        if record_accounting:
            self._record_settlement_accounting(
                order_id=order_id,
                meta=trade,
                payout=payout,
                exit_time=exit_time,
                source="late_auto_redeem",
                payload=payload,
                entry_price=entry_price,
                exit_price=exit_price,
                size=size,
                pnl=pnl,
                entry_time=entry_time,
            )

        if record_accounting:
            logger.info("=" * 80)
            logger.info("LIVE TRADE SETTLEMENT CORRECTED")
            logger.info(f"  Order: {order_id}")
            logger.info(f"  Market: {trade.get('slug')}")
            logger.info(f"  Payout: ${float(payout):.6f}")
            logger.info(f"  Realized P&L: ${float(pnl):+.6f}")
            logger.info("  Source: late_auto_redeem")
            logger.info("=" * 80)

    def _mark_settlement_unknown(
        self,
        order_id: str,
        meta: Dict[str, Any],
        reason: str,
        exit_time: datetime,
    ) -> None:
        """Move an unresolved trade out of active exposure without fabricating P&L."""
        with self._settlement_lock:
            try:
                if hasattr(self.risk_engine, "release_position"):
                    self.risk_engine.release_position(order_id)
                else:
                    self.risk_engine.remove_position(order_id, Decimal(str(meta.get("entry_price", "1"))))
            except Exception as e:
                logger.warning(f"Failed to release unknown-settlement risk for {order_id}: {e}")

            settled = dict(meta)
            settled.update(
                {
                    "order_id": order_id,
                    "settled_at": exit_time.isoformat(),
                    "settlement_source": "SETTLEMENT_UNKNOWN",
                    "needs_reconciliation": True,
                    "unknown_reason": reason,
                    "payout": "UNKNOWN",
                    "pnl": "UNKNOWN",
                }
            )
            self._settled_live_trades.append(settled)
            self._open_live_trades.pop(order_id, None)
            if not self._try_save_live_trade_ledger("Failed to persist unknown settlement"):
                return

            logger.warning(
                f"Settlement still unknown for {order_id}; released open exposure without "
                f"booking P&L ({reason}). A late auto_redeem can still correct this record."
            )
            self._retry_pending_auto_redeems("settlement marked unknown")

    def _handle_auto_redeem_event(self, payload: Dict[str, Any], store_pending: bool = True) -> bool:
        """Settle matching live trades when Polymarket reports an auto-redeem payout."""
        with self._settlement_lock:
            event_key = self._auto_redeem_event_key(payload)
            if event_key in self._seen_auto_redeem_events:
                return False

            try:
                payout = Decimal(str(payload.get("amount", "0")))
            except Exception:
                logger.warning(f"auto_redeem had invalid amount: {payload.get('amount')}")
                return False

            if self._require_auto_redeem_token_hint() and not self._payload_has_token_hint(payload):
                reason = "missing token/outcome hint"
                if store_pending:
                    self._store_pending_auto_redeem_event(event_key, payload, reason)
                return False

            matches = self._matching_open_live_trades(payload)
            matched_open = [
                (order_id, self._open_live_trades[order_id])
                for order_id in matches
                if order_id in self._open_live_trades
            ]
            if matched_open:
                if self._matches_are_ambiguous(payload, [meta for _, meta in matched_open]):
                    logger.warning(
                        "auto_redeem matched multiple bot token sides but did not include "
                        "token/outcome details; leaving trades for reconciliation "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, "ambiguous open-trade match")
                    return False

                unknown_units_reason = self._unknown_positive_auto_redeem_units_reason(payout, matched_open)
                if unknown_units_reason:
                    logger.warning(
                        f"{unknown_units_reason}; leaving auto_redeem pending for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, unknown_units_reason)
                    return False

                exit_time = self._parse_event_time(payload)
                if exit_time is None:
                    reason = "missing/invalid auto_redeem timestamp"
                    logger.warning(
                        "auto_redeem matched open trade(s) but had no valid timestamp; "
                        f"leaving for retry/reconciliation (slug={payload.get('slug')}, "
                        f"amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, reason)
                    return False
                allocations = self._allocated_auto_redeem_payouts(payout, matched_open)
                snapshot = self._snapshot_settlement_state()
                accounting_records = []
                for order_id, meta in list(matched_open):
                    allocated_payout = allocations.get(order_id, Decimal("0"))
                    accounting_records.append((order_id, copy.deepcopy(meta), allocated_payout))
                    self._record_settled_live_trade(
                        order_id=order_id,
                        meta=meta,
                        payout=allocated_payout,
                        exit_time=exit_time,
                        source="auto_redeem",
                        payload=payload,
                        event_key=event_key,
                        save=False,
                        record_accounting=False,
                    )
                self._mark_auto_redeem_seen(event_key)
                if not self._try_save_live_trade_ledger("Failed to persist auto_redeem settlement"):
                    self._restore_settlement_state(snapshot)
                    self._keep_auto_redeem_pending_after_failed_save(
                        event_key,
                        payload,
                        "ledger save failed after auto_redeem settlement",
                    )
                    return False
                for order_id, meta, allocated_payout in accounting_records:
                    self._book_settlement_accounting_from_record(
                        order_id=order_id,
                        meta=meta,
                        payout=allocated_payout,
                        exit_time=exit_time,
                        source="auto_redeem",
                        payload=payload,
                    )
                return True

            unknown_matches = self._matching_unknown_settled_trades(payload)
            if unknown_matches:
                if self._matches_are_ambiguous(payload, unknown_matches):
                    logger.warning(
                        "Late auto_redeem matched multiple unknown bot token sides but did "
                        "not include token/outcome details; keeping records as unknown "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, "ambiguous unknown-trade match")
                    return False

                matched_unknown = [
                    (str(trade.get("order_id")), trade)
                    for trade in unknown_matches
                    if trade.get("order_id")
                ]
                unknown_units_reason = self._unknown_positive_auto_redeem_units_reason(payout, matched_unknown)
                if unknown_units_reason:
                    logger.warning(
                        f"{unknown_units_reason}; keeping SETTLEMENT_UNKNOWN record(s) for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, unknown_units_reason)
                    return False

                allocations = self._allocated_auto_redeem_payouts(payout, matched_unknown)
                exit_time = self._parse_event_time(payload)
                if exit_time is None:
                    reason = "missing/invalid late auto_redeem timestamp"
                    logger.warning(
                        "Late auto_redeem matched unknown trade(s) but had no valid timestamp; "
                        f"keeping records unknown (slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, reason)
                    return False
                snapshot = self._snapshot_settlement_state()
                accounting_records = []
                for order_id, trade in matched_unknown:
                    allocated_payout = allocations.get(order_id, Decimal("0"))
                    accounting_records.append((order_id, copy.deepcopy(trade), allocated_payout))
                    self._record_late_auto_redeem_correction(
                        trade=trade,
                        payout=allocated_payout,
                        exit_time=exit_time,
                        payload=payload,
                        event_key=event_key,
                        save=False,
                        record_accounting=False,
                    )
                self._mark_auto_redeem_seen(event_key)
                if not self._try_save_live_trade_ledger("Failed to persist late auto_redeem correction"):
                    self._restore_settlement_state(snapshot)
                    self._keep_auto_redeem_pending_after_failed_save(
                        event_key,
                        payload,
                        "ledger save failed after late auto_redeem correction",
                    )
                    return False
                for order_id, trade, allocated_payout in accounting_records:
                    self._book_settlement_accounting_from_record(
                        order_id=order_id,
                        meta=trade,
                        payout=allocated_payout,
                        exit_time=exit_time,
                        source="late_auto_redeem",
                        payload=payload,
                    )
                return True

            logger.info(
                "auto_redeem received but no matching open or unknown live trade was found "
                f"(slug={payload.get('slug')}, condition_id={payload.get('condition_id')}, "
                f"amount={payload.get('amount')})"
            )
            if store_pending:
                self._store_pending_auto_redeem_event(event_key, payload, "no matching tracked bot trade yet")
            return False

    def _settle_expired_live_trades(self) -> None:
        """Mark old filled trades as unknown if no auto_redeem arrives after a grace period."""
        try:
            grace_raw = os.getenv("LIVE_SETTLEMENT_GRACE_SECONDS", str(DEFAULT_LIVE_SETTLEMENT_GRACE_SECONDS))
            grace_seconds = int(grace_raw)
            if grace_seconds < 0:
                raise ValueError("must be non-negative")
        except ValueError:
            self._block_live_settlement_ledger(
                f"invalid LIVE_SETTLEMENT_GRACE_SECONDS={os.getenv('LIVE_SETTLEMENT_GRACE_SECONDS')!r}"
            )
            return

        now = datetime.now(timezone.utc)
        with self._settlement_lock:
            if not self._open_live_trades:
                return
            missing_market_end_ids = []
            for order_id, meta in list(self._open_live_trades.items()):
                market_end = self._parse_utc_datetime(meta.get("market_end_time"))
                if market_end:
                    settle_after = market_end + timedelta(seconds=grace_seconds)
                    reason = f"no auto_redeem by {settle_after.isoformat()}"
                else:
                    missing_market_end_ids.append(str(order_id))
                    continue
                if now < settle_after:
                    continue
                self._mark_settlement_unknown(order_id, meta, reason, now)
            if missing_market_end_ids:
                shown_ids = ", ".join(missing_market_end_ids[:10])
                suffix = "" if len(missing_market_end_ids) <= 10 else ", ..."
                self._block_live_settlement_ledger(
                    f"{len(missing_market_end_ids)} open live trade(s) missing market_end_time: "
                    f"{shown_ids}{suffix}; manual reconciliation required before live trading can continue"
                )

    # ------------------------------------------------------------------
    # Strategy lifecycle
    # ------------------------------------------------------------------

    def on_start(self):
        """Called when strategy starts - LOAD ALL MARKETS AND SUBSCRIBE IMMEDIATELY"""
        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY STARTED - FIXED VERSION")
        logger.info("=" * 80)

        if not self._auto_redeem_registered:
            register_auto_redeem_handler(self._auto_redeem_handler)
            self._auto_redeem_registered = True
            logger.info("Registered Polymarket auto_redeem settlement handler")

        self._retry_pending_auto_redeems("startup ledger replay")

        # =========================================================================
        # FIX 2: Load ALL BTC instruments at startup
        # =========================================================================
        self._load_all_btc_instruments()

        # =========================================================================
        # FIX 3: Force subscribe to current market IMMEDIATELY
        # =========================================================================
        if self.instrument_id:
            self.subscribe_quote_ticks(self.instrument_id)
            logger.info(f"✓ SUBSCRIBED to market: {self.instrument_id}")
            
            # Try to get current price from cache
            try:
                quote = self.cache.quote_tick(self.instrument_id)
                if quote and quote.bid_price and quote.ask_price:
                    current_price = (quote.bid_price + quote.ask_price) / 2
                    self.price_history.append(current_price)
                    logger.info(f"✓ Initial price: ${float(current_price):.4f}")
            except Exception as e:
                logger.debug(f"No initial price yet: {e}")

        # Generate synthetic history if needed
        if len(self.price_history) < 20:
            self._generate_synthetic_history(target_count=20, existing_count=len(self.price_history))

        # =========================================================================
        # FIX 4: Start the timer loop (but don't rely on it for trading)
        # =========================================================================
        self.run_in_executor(self._start_timer_loop)

        if self.grafana_exporter:
            import threading
            threading.Thread(target=self._start_grafana_sync, daemon=True).start()

        logger.info("=" * 80)
        logger.info("Strategy active - will trade every 15 minutes")
        logger.info(f"Price history: {len(self.price_history)} points")
        if len(self.price_history) >= 20:
            logger.info("✓ READY TO TRADE NOW!")
        else:
            logger.warning(f"⚠ Need more history ({len(self.price_history)}/20)")
        logger.info("=" * 80)

    def _generate_synthetic_history(self, target_count: int = 20, existing_count: int = 0):
        """Generate synthetic price history for testing"""
        if self.price_history:
            base_price = self.price_history[-1]
        else:
            base_price = Decimal("0.5")
        needed = target_count - existing_count
        if needed <= 0:
            return
        for _ in range(needed):
            change = Decimal(str(random.uniform(-0.03, 0.03)))
            new_price = base_price * (Decimal("1.0") + change)
            new_price = max(Decimal("0.01"), min(Decimal("0.99"), new_price))
            self.price_history.append(new_price)
            base_price = new_price

    # ------------------------------------------------------------------
    # Load all BTC instruments at once
    # ------------------------------------------------------------------

    def _load_all_btc_instruments(self):
        """Load ALL BTC instruments from cache and sort by start time"""
        instruments = self.cache.instruments()
        logger.info(f"Loading ALL BTC instruments from {len(instruments)} total...")
        
        now = datetime.now(timezone.utc)
        current_timestamp = int(now.timestamp())
        
        btc_instruments = []
        
        for instrument in instruments:
            try:
                if hasattr(instrument, 'info') and instrument.info:
                    question = instrument.info.get('question', '').lower()
                    slug = instrument.info.get('market_slug', '').lower()
                    
                    if ('btc' in question or 'btc' in slug) and '15m' in slug:
                        try:
                            timestamp_part = slug.split('-')[-1]
                            market_timestamp = int(timestamp_part)
                            
                            # The slug timestamp IS the market start time (Unix, no offset).
                            # end_date_iso is a DATE-only string (e.g. "2026-02-20"), NOT a datetime,
                            # so parsing it gives midnight UTC which is wrong for intraday markets.
                            # Always derive end_timestamp from the slug: start + 900s.
                            real_start_ts = market_timestamp
                            end_timestamp = market_timestamp + 900  # 15-min markets always
                            time_diff = real_start_ts - current_timestamp
                            
                            # Only include markets that haven't ended yet
                            if end_timestamp > current_timestamp:
                                # Extract token ID for CLOB order book API.
                                # Nautilus instrument ID format:
                                #   {condition_id}-{token_id}.POLYMARKET
                                # The CLOB /book endpoint only accepts the token_id
                                # (the part after the dash, before .POLYMARKET).
                                token_id = self._extract_token_id_from_instrument_id(instrument.id)
                                token_outcome = self._infer_polymarket_token_outcome(instrument, token_id)

                                btc_instruments.append({
                                    'instrument': instrument,
                                    'slug': slug,
                                    'condition_id': instrument.info.get('condition_id'),
                                    'start_time': datetime.fromtimestamp(real_start_ts, tz=timezone.utc),
                                    'end_time': datetime.fromtimestamp(end_timestamp, tz=timezone.utc),
                                    'market_timestamp': market_timestamp,
                                    'end_timestamp': end_timestamp,
                                    'time_diff_minutes': time_diff / 60,
                                    'token_id': token_id,
                                    'token_outcome': token_outcome,
                                })
                        except (ValueError, IndexError):
                            continue
            except Exception:
                continue
        
        # Pair YES and NO tokens by explicit outcome, not provider load order.
        markets_by_slug = {}
        slug_order = []
        for inst in btc_instruments:
            slug = inst['slug']
            if slug not in markets_by_slug:
                markets_by_slug[slug] = {"all": [], "yes": None, "no": None}
                slug_order.append(slug)
            markets_by_slug[slug]["all"].append(inst)

            outcome = str(inst.get("token_outcome") or "").lower()
            if outcome in {"up", "yes"}:
                markets_by_slug[slug]["yes"] = inst
            elif outcome in {"down", "no"}:
                markets_by_slug[slug]["no"] = inst

        deduped = []
        for slug in slug_order:
            group = markets_by_slug[slug]
            tokens = group["all"]
            yes_inst = group["yes"]
            no_inst = group["no"]

            if yes_inst is None or no_inst is None:
                outcomes = [str(token.get("token_outcome") or "") for token in tokens]
                logger.error(
                    f"Could not pair YES/NO from outcome metadata for {slug}; "
                    f"refusing to trade this market (outcomes={outcomes})"
                )
                continue

            primary = dict(yes_inst)
            primary['instrument'] = yes_inst['instrument']
            primary['yes_instrument_id'] = primary['instrument'].id
            primary['yes_token_id'] = yes_inst.get('token_id')
            primary['no_instrument_id'] = no_inst['instrument'].id
            primary['no_token_id'] = no_inst.get('token_id')
            deduped.append(primary)
        btc_instruments = deduped
        
        # Sort by start time (absolute timestamp, not time-of-day)
        btc_instruments.sort(key=lambda x: x['market_timestamp'])
        
        logger.info("=" * 80)
        logger.info(f"FOUND {len(btc_instruments)} BTC 15-MIN MARKETS:")
        for i, inst in enumerate(btc_instruments):
            # A market is ACTIVE if it has started AND not yet ended
            is_active = inst['time_diff_minutes'] <= 0 and inst['end_timestamp'] > current_timestamp
            status = "ACTIVE" if is_active else "FUTURE" if inst['time_diff_minutes'] > 0 else "PAST"
            logger.info(f"  [{i}] {inst['slug']}: {status} (starts at {inst['start_time'].strftime('%H:%M:%S')}, ends at {inst['end_time'].strftime('%H:%M:%S')})")
        logger.info("=" * 80)
        
        self.all_btc_instruments = btc_instruments
        
        # Find current market and SUBSCRIBE IMMEDIATELY
        # FIXED: A market is current if it has STARTED and not yet ENDED (use end_time, not a hardcoded 15-min window)
        for i, inst in enumerate(btc_instruments):
            is_active = inst['time_diff_minutes'] <= 0 and inst['end_timestamp'] > current_timestamp
            if is_active:
                self.current_instrument_index = i
                self.instrument_id = inst['instrument'].id
                self.next_switch_time = inst['end_time']
                self._yes_token_id = inst.get('yes_token_id')
                self._yes_instrument_id = inst.get('yes_instrument_id', inst['instrument'].id)
                self._no_instrument_id = inst.get('no_instrument_id')
                logger.info(f"✓ CURRENT MARKET: {inst['slug']} (index {i})")
                logger.info(f"  Next switch at: {self.next_switch_time.strftime('%H:%M:%S')}")
                logger.info(f"  YES token: {self._yes_token_id[:16]}…" if self._yes_token_id else "  YES token: unknown")
                
                # =========================================================================
                # CRITICAL FIX: Subscribe immediately!
                # =========================================================================
                self.subscribe_quote_ticks(self.instrument_id)
                if self._no_instrument_id:
                    self.subscribe_quote_ticks(self._no_instrument_id)
                    logger.info("  ✓ SUBSCRIBED to NO token quotes")
                logger.info(f"  ✓ SUBSCRIBED to current market")
                break
        
        if self.current_instrument_index == -1 and btc_instruments:
            # No currently-active market — find the NEAREST upcoming one
            # (smallest positive time_diff_minutes = starts soonest)
            future_markets = [inst for inst in btc_instruments if inst['time_diff_minutes'] > 0]
            if future_markets:
                nearest = min(future_markets, key=lambda x: x['time_diff_minutes'])
                nearest_idx = btc_instruments.index(nearest)
            else:
                # All markets are in the past — use the last one
                nearest = btc_instruments[-1]
                nearest_idx = len(btc_instruments) - 1

            self.current_instrument_index = nearest_idx
            inst = nearest
            self.instrument_id = inst['instrument'].id
            self._yes_token_id = inst.get('yes_token_id')
            self._yes_instrument_id = inst.get('yes_instrument_id', inst['instrument'].id)
            self._no_instrument_id = inst.get('no_instrument_id')
            self.next_switch_time = inst['start_time']  # switch_time = when it OPENS
            logger.info(f"⚠ NO CURRENT MARKET - WAITING FOR NEAREST FUTURE: {inst['slug']}")
            logger.info(f"  Starts in {inst['time_diff_minutes']:.1f} min at {self.next_switch_time.strftime('%H:%M:%S')} UTC")

            # Subscribe so we get ticks when it opens
            self.subscribe_quote_ticks(self.instrument_id)
            if self._no_instrument_id:
                self.subscribe_quote_ticks(self._no_instrument_id)
                logger.info("  ✓ SUBSCRIBED to future NO token quotes")
            logger.info(f"  ✓ SUBSCRIBED to future market")
            # Block trading until the market actually opens (timer loop sets _market_open flag)
            self._waiting_for_market_open = True
            
    def _switch_to_next_market(self):
        """Switch to the next market in the pre-loaded list"""
        if not self.all_btc_instruments:
            logger.error("No instruments loaded!")
            return False
        
        next_index = self.current_instrument_index + 1
        if next_index >= len(self.all_btc_instruments):
            logger.warning("No more markets available - will restart bot")
            return False
        
        next_market = self.all_btc_instruments[next_index]
        now = datetime.now(timezone.utc)
        
        # Check if next market is ready
        if now < next_market['start_time']:
            logger.info(f"Waiting for next market at {next_market['start_time'].strftime('%H:%M:%S')}")
            return False
        
        # Switch to next market
        self.current_instrument_index = next_index
        self.instrument_id = next_market['instrument'].id
        self.next_switch_time = next_market['end_time']
        self._yes_token_id = next_market.get('yes_token_id')
        self._yes_instrument_id = next_market.get('yes_instrument_id', next_market['instrument'].id)
        self._no_instrument_id = next_market.get('no_instrument_id')
        self._last_bid_ask = None
        self._last_no_bid_ask = None
        
        logger.info("=" * 80)
        logger.info(f"SWITCHING TO NEXT MARKET: {next_market['slug']}")
        logger.info(f"  Current time: {now.strftime('%H:%M:%S')}")
        logger.info(f"  Market ends at: {self.next_switch_time.strftime('%H:%M:%S')}")
        logger.info("=" * 80)
        
        self._stable_tick_count = 0
        self._market_stable = False
        self._waiting_for_market_open = False  # Market is now active
        
        # Reset trade timer so we trade at the NEXT quote we receive
        # Use -1 so any interval will trigger (same as startup)
        self.last_trade_time = -1
        logger.info(f"  Trade timer reset — will trade on next tick")
        
        self.subscribe_quote_ticks(self.instrument_id)
        if self._no_instrument_id:
            self.subscribe_quote_ticks(self._no_instrument_id)
            logger.info("  ✓ SUBSCRIBED to NO token quotes")
        return True

    # ------------------------------------------------------------------
    # Timer loop - SIMPLIFIED
    # ------------------------------------------------------------------

    def _start_timer_loop(self):
        """Start timer loop in executor"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._timer_loop())
        finally:
            loop.close()

    async def _timer_loop(self):
        """
        Timer loop: checks every 10 seconds if it's time to switch markets.
        Also handles the case where we're waiting for a future market to open.
        """
        while True:
            # --- auto-restart check ---
            uptime_minutes = (datetime.now(timezone.utc) - self.bot_start_time).total_seconds() / 60
            if uptime_minutes >= self.restart_after_minutes:
                if self._decision_in_progress:
                    logger.warning("AUTO-RESTART due, but a trade decision is in progress; postponing")
                    await asyncio.sleep(10)
                    continue
                logger.warning("AUTO-RESTART TIME - Loading fresh filters")
                import signal as _signal
                os.kill(os.getpid(), _signal.SIGTERM)
                return

            now = datetime.now(timezone.utc)
            self._settle_expired_live_trades()

            if self.next_switch_time and now >= self.next_switch_time:
                if self._waiting_for_market_open:
                    # The future market we were waiting for has now opened
                    # Treat it like a market switch so trade timer resets
                    logger.info("=" * 80)
                    logger.info(f"⏰ WAITING MARKET NOW OPEN: {now.strftime('%H:%M:%S')} UTC")
                    logger.info("=" * 80)
                    # Update next_switch_time to the market's END time
                    if (self.current_instrument_index >= 0 and
                            self.current_instrument_index < len(self.all_btc_instruments)):
                        current_market = self.all_btc_instruments[self.current_instrument_index]
                        self.next_switch_time = current_market['end_time']
                        logger.info(f"  Market ends at {self.next_switch_time.strftime('%H:%M:%S')} UTC")
                    self._waiting_for_market_open = False
                    self._market_stable = False
                    self._stable_tick_count = 0
                    self._last_bid_ask = None
                    self._last_no_bid_ask = None
                    self.last_trade_time = -1  # Trade immediately on next tick
                    logger.info("  ✓ MARKET OPEN — waiting for stable quotes")
                else:
                    # Normal market switch
                    self._switch_to_next_market()

            await asyncio.sleep(10)

    # ------------------------------------------------------------------
    # Quote tick handler - SIMPLIFIED
    # ------------------------------------------------------------------

    def on_quote_tick(self, tick: QuoteTick):
        """Handle quote tick - TRADE when market opens and at each 15-min boundary"""
        try:
            if self.instrument_id is None:
                return

            is_yes_tick = tick.instrument_id == self.instrument_id
            is_no_tick = (
                getattr(self, "_no_instrument_id", None) is not None
                and tick.instrument_id == self._no_instrument_id
            )
            if not is_yes_tick and not is_no_tick:
                return

            now = datetime.now(timezone.utc)
            bid = tick.bid_price
            ask = tick.ask_price

            if bid is None or ask is None:
                if is_yes_tick:
                    self._reset_stability(f"missing quote side bid={bid}, ask={ask}")
                return
                
            try:
                bid_decimal = bid.as_decimal()
                ask_decimal = ask.as_decimal()
            except Exception as e:
                if is_yes_tick:
                    self._reset_stability(f"malformed quote bid={bid}, ask={ask}: {e}")
                return

            if not self._is_quote_valid(bid_decimal, ask_decimal):
                if is_yes_tick:
                    self._reset_stability(
                        f"invalid quote bid={bid_decimal}, ask={ask_decimal}",
                    )
                return

            if is_no_tick:
                self._last_no_bid_ask = (bid_decimal, ask_decimal)
                return

            # Always store price history
            mid_price = (bid_decimal + ask_decimal) / 2
            self.price_history.append(mid_price)
            if len(self.price_history) > self.max_history:
                self.price_history.pop(0)
            
            # Store latest bid/ask for liquidity check before order placement
            self._last_bid_ask = (bid_decimal, ask_decimal)

            # Tick buffer for TickVelocityProcessor (rolling 90s window)
            self._tick_buffer.append({'ts': now, 'price': mid_price})

            # Stability gate
            if not self._market_stable:
                self._stable_tick_count += 1
                if self._stable_tick_count >= QUOTE_STABILITY_REQUIRED:
                    self._market_stable = True
                    logger.info(
                        f"✓ Market STABLE after {self._stable_tick_count} valid quote ticks"
                    )
                else:
                    return

            # =========================================================================
            # FIXED TRADING LOGIC:
            # 
            # We trade once per 15-min market interval.
            # Instead of checking wall-clock 15-min boundaries (which caused the 2-hour
            # wait), we use a simple counter keyed to the Polymarket market's OWN
            # start time.
            #
            # The market's start_time is stored in all_btc_instruments[current_index].
            # Within each market, we compute a "sub-interval" index:
            #   sub_interval = elapsed_seconds_since_market_open // 900
            # Trade ID = (market_start_timestamp, sub_interval)
            # This fires once at market open AND once after every 15 min within
            # the same market if it's a multi-interval market.
            #
            # If _waiting_for_market_open is True (started before market opens),
            # we block trading until the timer loop calls _switch_to_next_market.
            # =========================================================================

            # Block trading if waiting for a future market to open
            if self._waiting_for_market_open:
                return

            # Get current market info
            if (self.current_instrument_index < 0 or
                    self.current_instrument_index >= len(self.all_btc_instruments)):
                return

            current_market = self.all_btc_instruments[self.current_instrument_index]
            market_start_ts = current_market['market_timestamp']  # Slug timestamp = market start (Unix)

            # How many 15-min intervals have elapsed since this market opened?
            elapsed_secs = now.timestamp() - market_start_ts
            if elapsed_secs < 0:
                # Market hasn't started yet — block
                return

            sub_interval = int(elapsed_secs // MARKET_INTERVAL_SECONDS)

            # Unique trade key: (market_start_timestamp, sub_interval)
            trade_key = (market_start_ts, sub_interval)

            # =========================================================================
            # TRADE WINDOW: minutes 13–14 of each 15-min market (780–840 seconds in)
            #
            # WHY LATE IN THE MARKET:
            #   At 13 minutes in, the market has more information than at open.
            #   The Polymarket quote is still not edge by itself; the decision
            #   below also requires independent signal confirmation.
            #
            # TREND FILTER (applied in _make_trading_decision):
            #   Price > 0.60 -> candidate YES trade
            #   Price < 0.40 -> candidate NO trade
            #   Price 0.40-0.60 -> skip as too close to 0.50
            # =========================================================================
            seconds_into_sub_interval = elapsed_secs % MARKET_INTERVAL_SECONDS
            TRADE_WINDOW_START = 780   # 13 minutes in
            TRADE_WINDOW_END   = 840   # 14 minutes in (60s window)

            if (
                TRADE_WINDOW_START <= seconds_into_sub_interval < TRADE_WINDOW_END
                and trade_key != self.last_trade_time
                and not self._decision_in_progress
            ):
                self._decision_in_progress = True

                logger.info("=" * 80)
                logger.info(f" LATE-WINDOW TRADE: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                logger.info(f"   Market: {current_market['slug']}")
                logger.info(f"   Sub-interval #{sub_interval} ({seconds_into_sub_interval:.1f}s in = {seconds_into_sub_interval/60:.1f} min)")
                logger.info(f"   Price: ${float(mid_price):,.4f} | Bid: ${float(bid_decimal):,.4f} | Ask: ${float(ask_decimal):,.4f}")
                logger.info(f"   Trend strength: {'STRONG ✓' if float(mid_price) > 0.60 or float(mid_price) < 0.40 else 'WEAK — may skip'}")
                logger.info(f"   Price history: {len(self.price_history)} points")
                logger.info("=" * 80)

                self.run_in_executor(
                    lambda: self._make_trading_decision_sync(float(mid_price), trade_key)
                )
            elif trade_key != self._last_trade_wait_log_key:
                window_start = datetime.fromtimestamp(
                    market_start_ts + (sub_interval * MARKET_INTERVAL_SECONDS) + TRADE_WINDOW_START,
                    timezone.utc,
                )
                window_end = datetime.fromtimestamp(
                    market_start_ts + (sub_interval * MARKET_INTERVAL_SECONDS) + TRADE_WINDOW_END,
                    timezone.utc,
                )
                seconds_until_window = max(0.0, TRADE_WINDOW_START - seconds_into_sub_interval)
                logger.info(
                    f"Waiting for late trade window: {window_start.strftime('%H:%M:%S')}-"
                    f"{window_end.strftime('%H:%M:%S')} UTC "
                    f"({seconds_until_window:.0f}s remaining) for {current_market['slug']}"
                )
                self._last_trade_wait_log_key = trade_key

        except Exception as e:
            logger.error(f"Error processing quote tick: {e}")

    # ------------------------------------------------------------------
    # Trading decision (unchanged)
    # ------------------------------------------------------------------

    def _make_trading_decision_sync(self, current_price, trade_key=None):
        """Synchronous wrapper for trading decision (called from executor)."""
        # Convert float back to Decimal for processing
        from decimal import Decimal
        price_decimal = Decimal(str(current_price))
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._make_trading_decision(price_decimal, trade_key=trade_key))
        except Exception as e:
            logger.error(f"Trading decision aborted: {e}\n{traceback.format_exc()}")
        finally:
            self._decision_in_progress = False
            loop.close()
            
    async def _fetch_market_context(self, current_price: Decimal) -> dict:
        """
        Fetch REAL external data to populate signal processor metadata.

        Returns a dict with:
          - sentiment_score (float 0-100): live Fear & Greed index, or None
          - spot_price (float): live BTC-USD from Coinbase, or None
          - deviation (float): polymarket price vs SMA-20 (always computed)
          - momentum (float): 5-period rate of change (always computed)
          - volatility (float): price std-dev over last 20 ticks (always computed)
        """
        current_price_float = float(current_price)

        # --- Always-available stats from local price_history ---
        recent_prices = [float(p) for p in self.price_history[-20:]]
        sma_20 = sum(recent_prices) / len(recent_prices)
        deviation = (current_price_float - sma_20) / sma_20
        momentum = (
            (current_price_float - float(self.price_history[-5])) / float(self.price_history[-5])
            if len(self.price_history) >= 5 else 0.0
        )
        variance = sum((p - sma_20) ** 2 for p in recent_prices) / len(recent_prices)
        volatility = math.sqrt(variance)

        metadata = {
            "deviation": deviation,
            "momentum": momentum,
            "volatility": volatility,
            # Tick buffer for TickVelocityProcessor
            "tick_buffer": list(self._tick_buffer),
            # YES token id for OrderBookImbalanceProcessor
            "yes_token_id": self._yes_token_id,
        }

        # --- Real sentiment: Fear & Greed Index via NewsSocialDataSource ---
        try:
            from data_sources.news_social.adapter import NewsSocialDataSource
            news_source = NewsSocialDataSource()
            await news_source.connect()
            fg = await news_source.get_fear_greed_index()
            await news_source.disconnect()
            if fg and "value" in fg:
                metadata["sentiment_score"] = float(fg["value"])
                metadata["sentiment_classification"] = fg.get("classification", "")
                logger.info(
                    f"Fear & Greed: {metadata['sentiment_score']:.0f} "
                    f"({metadata['sentiment_classification']})"
                )
            else:
                logger.warning("Fear & Greed fetch returned no data — sentiment processor skipped")
        except Exception as e:
            logger.warning(f"Could not fetch Fear & Greed index: {e} — sentiment processor skipped")

        # --- Real spot price: Coinbase BTC-USD REST API ---
        try:
            from data_sources.coinbase.adapter import CoinbaseDataSource
            coinbase = CoinbaseDataSource()
            await coinbase.connect()
            spot = await coinbase.get_current_price()
            await coinbase.disconnect()
            if spot:
                metadata["spot_price"] = float(spot)
                logger.info(f"Coinbase spot price: ${float(spot):,.2f}")
            else:
                logger.warning("Coinbase price fetch returned None — divergence processor skipped")
        except Exception as e:
            logger.warning(f"Could not fetch Coinbase spot price: {e} — divergence processor skipped")

        logger.info(
            f"Market context — deviation={deviation:.2%}, "
            f"momentum={momentum:.2%}, volatility={volatility:.4f}, "
            f"sentiment={'%.0f' % metadata['sentiment_score'] if 'sentiment_score' in metadata else 'N/A'}, "
            f"spot=${'%.2f' % metadata['spot_price'] if 'spot_price' in metadata else 'N/A'}"
        )
        return metadata

    async def _make_trading_decision(self, current_price: Decimal, trade_key=None) -> bool:
        """
        Make trading decision using our 7-phase system.

        Position size is a fixed USD amount. The market price is used only as a
        late-window trend filter; fused signals must still confirm the side.
        """
        # --- Mode check ---
        is_simulation = await self.check_simulation_mode()
        logger.info(f"Mode: {'SIMULATION' if is_simulation else 'LIVE TRADING'}")
        if not is_simulation:
            unresolved = self._unresolved_settlement_unknowns()
            if unresolved:
                logger.error(
                    "LIVE TRADING PAUSED: unresolved settlement reconciliation exists "
                    f"({len(unresolved)} trade(s)). Resolve SETTLEMENT_UNKNOWN records "
                    f"in {LIVE_TRADE_LEDGER_PATH.name} before placing new live orders."
                )
                return False

        # --- Minimum history guard ---
        if len(self.price_history) < 20:
            logger.warning(f"Not enough price history ({len(self.price_history)}/20)")
            return False

        logger.info(f"Current price: ${float(current_price):,.4f}")

        # --- Phase 4a: Build real metadata for processors ---
        metadata = await self._fetch_market_context(current_price)

        # --- Phase 4b: Run all three signal processors ---
        signals = self._process_signals(current_price, metadata)

        if not signals:
            logger.info("No signals generated — no trade this interval")
            return False

        logger.info(f"Generated {len(signals)} signal(s):")
        for sig in signals:
            logger.info(
                f"  [{sig.source}] {sig.direction.value}: "
                f"score={sig.score:.1f}, confidence={sig.confidence:.2%}"
            )

        # --- Phase 4c: Fuse signals into one consensus ---
        fused = self.fusion_engine.fuse_signals(signals, min_signals=2, min_score=55.0)
        if not fused:
            logger.info("Fusion produced no actionable signal — no trade this interval")
            return False

        logger.info(
            f"FUSED SIGNAL: {fused.direction.value} "
            f"(score={fused.score:.1f}, confidence={fused.confidence:.2%})"
        )

        # --- Phase 5: Position size is a fixed USD amount ---
        POSITION_SIZE_USD = get_market_buy_usd()

        # =========================================================================
        # TREND FILTER — executable price sanity check at the late trade window
        #
        # At minute 13, the Polymarket price is still only market consensus, not
        # an edge. The bot will only trade when this price filter and the fused
        # independent signals agree.
        #
        #   price > 0.60 → market says UP with >60% confidence → buy YES
        #   price < 0.40 → market says DOWN with >60% confidence → buy NO
        #   price 0.40–0.60 → too close to call → SKIP (this is where we were losing)
        #
        # =========================================================================
        TREND_UP_THRESHOLD   = 0.60   # price above this → buy YES (UP)
        TREND_DOWN_THRESHOLD = 0.40   # price below this → buy NO (DOWN)

        price_float = float(current_price)

        if price_float > TREND_UP_THRESHOLD:
            direction = "long"
            logger.info(
                f" TREND: UP ({price_float:.2%} YES probability) → buying YES"
            )
        elif price_float < TREND_DOWN_THRESHOLD:
            direction = "short"
            logger.info(
                f" TREND: DOWN ({price_float:.2%} YES probability = {1-price_float:.2%} NO) → buying NO"
            )
        else:
            logger.info(
                f"⏭ TREND: NEUTRAL ({price_float:.2%}) — price too close to 0.50, SKIPPING trade "
                f"(coin flip territory: {TREND_DOWN_THRESHOLD:.0%}–{TREND_UP_THRESHOLD:.0%})"
            )
            return False

        if get_env_bool("REQUIRE_SIGNAL_CONFIRMATION", True):
            expected_signal = "bullish" if direction == "long" else "bearish"
            actual_signal = str(fused.direction.value).lower()
            min_confidence = get_min_signal_confidence()
            if actual_signal != expected_signal:
                logger.info(
                    f"SKIP: trend wants {direction.upper()} but fused signal is "
                    f"{actual_signal.upper()} — no independent confirmation"
                )
                return False
            if fused.confidence < min_confidence:
                logger.info(
                    f"SKIP: fused confidence {fused.confidence:.2%} below "
                    f"MIN_SIGNAL_CONFIDENCE={min_confidence:.2%}"
                )
                return False

        last_tick = getattr(self, "_last_bid_ask", None)
        if not last_tick:
            logger.warning("SKIP: no executable YES quote cached")
            return False
        yes_bid, yes_ask = last_tick
        if direction == "long":
            executable_entry = yes_ask
            entry_source = "YES ask"
        else:
            no_tick = getattr(self, "_last_no_bid_ask", None)
            if not no_tick:
                logger.warning("SKIP: no executable NO ask cached")
                return False
            _, no_ask = no_tick
            executable_entry = no_ask
            entry_source = "NO ask"

        # This is a heuristic confidence filter, not a calibrated EV model.
        # The processor confidence values are not yet trained settlement probabilities.
        fee_buffer = Decimal(os.getenv("EV_FEE_BUFFER", "0.005"))
        spread_buffer = Decimal(os.getenv("EV_SPREAD_BUFFER", "0.01"))
        min_required_confidence = executable_entry + fee_buffer + spread_buffer
        if Decimal(str(fused.confidence)) < min_required_confidence:
            logger.info(
                f"SKIP: fused heuristic confidence {fused.confidence:.2%} below "
                f"entry confidence threshold {float(min_required_confidence):.2%} "
                f"({entry_source} {float(executable_entry):.2%} + buffers "
                f"{float(fee_buffer + spread_buffer):.2%})"
            )
            return False

        # Risk engine tracks submitted orders plus filled markets until settlement.
        is_valid, error = self.risk_engine.validate_new_position(
            size=POSITION_SIZE_USD,
            direction=direction,
            current_price=current_price,
        )
        if not is_valid:
            logger.warning(f"Risk engine blocked trade: {error}")
            return False

        logger.info(f"Position size: ${POSITION_SIZE_USD:.2f} (fixed) | Direction: {direction.upper()}")

        # --- Liquidity guard: don't place if market has no real depth ---
        # The current bid/ask come from the last processed quote tick.
        # If ask <= 0.02 or bid <= 0.02, the orderbook is essentially empty
        # and a FAK (IOC market) order will be rejected immediately.
        last_tick = getattr(self, '_last_bid_ask', None)
        if last_tick:
            last_bid, last_ask = last_tick
            MIN_LIQUIDITY = Decimal("0.02")
            if direction == "long" and last_ask <= MIN_LIQUIDITY:
                logger.warning(
                    f"⚠ Skipping UP/YES trade: YES ask=${float(last_ask):.4f} "
                    f"is at or below the ${float(MIN_LIQUIDITY):.2f} liquidity floor. "
                    "Market is too thin/extreme; will retry next tick."
                )
                return False
            if direction == "short":
                no_tick = getattr(self, "_last_no_bid_ask", None)
                if not no_tick:
                    logger.warning(
                        "⚠ Skipping DOWN/NO trade: no direct NO quote available yet. "
                        "Waiting for NO ask; will retry next tick."
                    )
                    return False
                no_bid, no_ask = no_tick
                if no_ask <= MIN_LIQUIDITY:
                    logger.warning(
                        f"⚠ Skipping DOWN/NO trade: NO ask=${float(no_ask):.4f} "
                        f"is at or below the ${float(MIN_LIQUIDITY):.2f} liquidity floor. "
                        "Market is too thin/extreme; will retry next tick."
                    )
                    return False

        # --- Phase 5 / 6: Execute ---
        if is_simulation:
            placed = await self._record_paper_trade(fused, POSITION_SIZE_USD, current_price, direction)
        else:
            placed = await self._place_real_order(fused, POSITION_SIZE_USD, current_price, direction)
        if placed and trade_key is not None:
            self.last_trade_time = trade_key
        return placed
            
    async def _record_paper_trade(self, signal, position_size, current_price, direction) -> bool:
        exit_delta = timedelta(minutes=1) if self.test_mode else timedelta(minutes=15)
        exit_time = datetime.now(timezone.utc) + exit_delta

        outcome = "PENDING"
        paper_trade = PaperTrade(
            timestamp=datetime.now(timezone.utc),
            direction=direction.upper(),
            size_usd=float(position_size),
            price=float(current_price),
            signal_score=signal.score,
            signal_confidence=signal.confidence,
            outcome=outcome,
        )
        self.paper_trades.append(paper_trade)

        logger.info("=" * 80)
        logger.info("[SIMULATION] DECISION OBSERVATION RECORDED")
        logger.info(f"  Direction: {direction.upper()}")
        logger.info(f"  Size: ${float(position_size):.2f}")
        logger.info(f"  Entry Price: ${float(current_price):,.4f}")
        logger.info(f"  Expected Resolution Time: {exit_time.isoformat()}")
        logger.info("  Execution: not live-equivalent; no fill, settlement, or P&L is simulated")
        logger.info(f"  Status: {outcome}")
        logger.info(f"  Total Paper Trades: {len(self.paper_trades)}")
        logger.info("=" * 80)

        self._save_paper_trades()
        return True

    def _save_paper_trades(self):
        import json
        try:
            trades_data = [t.to_dict() for t in self.paper_trades]
            with open('paper_trades.json', 'w') as f:
                json.dump(trades_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paper trades: {e}")

    # ------------------------------------------------------------------
    # Real order (unchanged)
    # ------------------------------------------------------------------

    async def _place_real_order(self, signal, position_size, current_price, direction) -> bool:
        if not self.instrument_id:
            logger.error("No instrument available")
            return False

        try:
            # instrument is fetched below after determining YES vs NO token

            logger.info("=" * 80)
            logger.info("LIVE MODE - PLACING REAL ORDER!")
            logger.info("=" * 80)

            # On Polymarket, both UP and DOWN are BUY orders.
            # Bullish = buy YES token (self._yes_instrument_id)
            # Bearish = buy NO token  (self._no_instrument_id)
            # There is NO sell — you always buy whichever side you want.
            side = OrderSide.BUY

            if direction == "long":
                trade_instrument_id = getattr(self, '_yes_instrument_id', self.instrument_id)
                trade_label = "YES (UP)"
            else:
                no_id = getattr(self, '_no_instrument_id', None)
                if no_id is None:
                    logger.warning(
                        "NO token instrument not found for this market — "
                        "cannot bet DOWN. Skipping trade."
                    )
                    return False
                trade_instrument_id = no_id
                trade_label = "NO (DOWN)"

            instrument = self.cache.instrument(trade_instrument_id)
            if not instrument:
                logger.error(f"Instrument not in cache: {trade_instrument_id}")
                return False

            logger.info(f"Buying {trade_label} token: {trade_instrument_id}")

            max_usd_amount = float(position_size)
            last_tick = getattr(self, "_last_bid_ask", None)
            if not last_tick:
                logger.warning("YES quote is unavailable at order placement; skipping")
                return False
            last_bid, last_ask = last_tick
            if direction == "long":
                quoted_price = last_ask
                price_source = "YES ask"
            else:
                no_tick = getattr(self, "_last_no_bid_ask", None)
                if not no_tick:
                    logger.warning("NO ask is unavailable at order placement; skipping")
                    return False
                _, no_ask = no_tick
                quoted_price = no_ask
                price_source = "NO ask"

            trade_price = float(quoted_price)
            estimated_tokens = max_usd_amount / trade_price if trade_price > 0 else 0.0
            logger.info(
                f"BUY {trade_label}: spending ${max_usd_amount:.2f} USDC.e "
                f"(estimated {estimated_tokens:.6f} tokens at ${trade_price:.4f} from {price_source})"
            )

            qty = Quantity.from_str(f"{max_usd_amount:.2f}")
            timestamp_ms = int(time.time() * 1000)
            unique_id = f"BTC-15MIN-${max_usd_amount:.0f}-{timestamp_ms}"
            submitted_at = datetime.now(timezone.utc)
            market_meta = self._current_market_metadata()
            instrument_info = getattr(instrument, "info", {}) or {}
            market_slug = market_meta.get("slug") or instrument_info.get("market_slug")
            condition_id = market_meta.get("condition_id") or instrument_info.get("condition_id")
            market_end_time = self._parse_utc_datetime(market_meta.get("end_time"))
            if market_end_time is None:
                logger.error(
                    f"Live order rejected: missing/invalid market_end_time for {market_slug or trade_instrument_id}"
                )
                return False
            token_id = (
                market_meta.get("yes_token_id")
                if direction == "long"
                else market_meta.get("no_token_id")
            )

            order = self.order_factory.market(
                instrument_id=trade_instrument_id,
                order_side=side,
                quantity=qty,
                client_order_id=ClientOrderId(unique_id),
                quote_quantity=True,
                time_in_force=TimeInForce.IOC,
            )

            submitted_meta = {
                "entry_price": Decimal(str(trade_price)),
                "size": position_size,
                "direction": direction,
                "trade_label": trade_label,
                "estimated_tokens": Decimal(str(estimated_tokens)),
                "instrument_id": str(trade_instrument_id),
                "token_id": token_id,
                "slug": market_slug,
                "condition_id": condition_id,
                "market_start_time": self._jsonable(market_meta.get("start_time")),
                "market_end_time": market_end_time.isoformat(),
                "submitted_at": submitted_at,
                "signal_score": getattr(signal, "score", 0.0),
                "signal_confidence": getattr(signal, "confidence", 0.0),
            }

            with self._settlement_lock:
                unresolved = self._unresolved_settlement_unknowns()
                if unresolved:
                    logger.error(
                        "LIVE ORDER BLOCKED: unresolved settlement reconciliation appeared "
                        f"before submit_order ({len(unresolved)} trade(s)). Resolve "
                        f"SETTLEMENT_UNKNOWN records in {LIVE_TRADE_LEDGER_PATH.name}."
                    )
                    return False
                self._submitted_positions[unique_id] = submitted_meta
                self.risk_engine.add_position(
                    position_id=unique_id,
                    size=position_size,
                    entry_price=Decimal(str(trade_price)),
                    direction="buy_yes" if direction == "long" else "buy_no",
                )

            self.submit_order(order)

            logger.info(f"REAL ORDER SUBMITTED!")
            logger.info(f"  Order ID: {unique_id}")
            logger.info(f"  Direction: {trade_label}")
            logger.info(f"  Side: BUY")
            logger.info(f"  Spend Amount: ${max_usd_amount:.2f} USDC.e")
            logger.info(f"  Estimated Tokens: {estimated_tokens:.6f}")
            logger.info(f"  Estimated Price: ${trade_price:.4f} ({price_source})")
            logger.info("  Quantity Mode: quote_quantity=True (USDC spend)")
            logger.info("=" * 80)

            self._track_order_event("placed")
            return True

        except Exception as e:
            logger.error(f"Error placing real order: {e}")
            import traceback
            traceback.print_exc()
            if "unique_id" in locals():
                self._release_submitted_position(unique_id)
            self._track_order_event("rejected")
            return False

    # ------------------------------------------------------------------
    # Signal processing
    # ------------------------------------------------------------------

    def _process_signals(self, current_price, metadata=None):
        signals = []
        if metadata is None:
            metadata = {}

        processed_metadata = {}
        for key, value in metadata.items():
            if isinstance(value, float):
                processed_metadata[key] = Decimal(str(value))
            else:
                processed_metadata[key] = value

        spike_signal = self.spike_detector.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if spike_signal:
            signals.append(spike_signal)

        if 'sentiment_score' in processed_metadata:
            sentiment_signal = self.sentiment_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if sentiment_signal:
                signals.append(sentiment_signal)

        if 'spot_price' in processed_metadata:
            divergence_signal = self.divergence_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if divergence_signal:
                signals.append(divergence_signal)

        # --- Order Book Imbalance (real-time Polymarket CLOB depth) ---
        if processed_metadata.get('yes_token_id'):
            ob_signal = self.orderbook_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if ob_signal:
                signals.append(ob_signal)

        # --- Tick Velocity (last 60s of Polymarket probability movement) ---
        if processed_metadata.get('tick_buffer'):
            tv_signal = self.tick_velocity_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if tv_signal:
                signals.append(tv_signal)

        # --- Deribit Put/Call Ratio (institutional options sentiment) ---
        pcr_signal = self.deribit_pcr_processor.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if pcr_signal:
            signals.append(pcr_signal)

        return signals

    # ------------------------------------------------------------------
    # Order events
    # ------------------------------------------------------------------

    def _track_order_event(self, event_type: str) -> None:
        """
        Safely track an order event on the performance tracker.

        PerformanceTracker does not expose `increment_order_counter`, so we
        use whichever method is actually available, or fall back to a no-op.
        Supported event_type values: "placed", "filled", "rejected".
        """
        try:
            pt = self.performance_tracker
            # Try the method that actually exists first
            if hasattr(pt, 'record_order_event'):
                pt.record_order_event(event_type)
            elif hasattr(pt, 'increment_counter'):
                pt.increment_counter(event_type)
            elif hasattr(pt, 'increment_order_counter'):
                pt.increment_order_counter(event_type)
            else:
                # No suitable method found – log and carry on
                logger.debug(
                    f"PerformanceTracker has no order-counter method; "
                    f"ignoring event '{event_type}'"
                )
        except Exception as e:
            logger.warning(f"Failed to track order event '{event_type}': {e}")

    def _pop_submitted_position(self, client_order_id) -> Optional[Dict[str, Any]]:
        """Remove a submitted-order reservation from local pending-order tracking."""
        order_id = str(client_order_id)
        with self._settlement_lock:
            return self._submitted_positions.pop(order_id, None)

    def _release_submitted_position(self, client_order_id) -> Optional[Dict[str, Any]]:
        """Release locally tracked exposure for an order that did not stay open."""
        order_id = str(client_order_id)
        meta = self._pop_submitted_position(order_id)
        if not meta:
            return None
        try:
            if hasattr(self.risk_engine, "release_position"):
                self.risk_engine.release_position(order_id)
            else:
                self.risk_engine.remove_position(order_id, meta["entry_price"])
        except Exception as e:
            logger.warning(f"Failed to release risk position for {order_id}: {e}")
        return meta

    def _record_live_order_fill(self, order_id: str, fill_price: Decimal, fill_qty: Decimal) -> bool:
        """Track cumulative live fills until final market settlement."""
        with self._settlement_lock:
            if self._settlement_ledger_blocked_reason:
                logger.error(
                    f"LIVE FILL IGNORED: settlement ledger is blocked for {order_id}; "
                    f"{self._settlement_ledger_blocked_reason}"
                )
                return False

            source_meta = self._submitted_positions.get(order_id)
            if source_meta is None:
                source_meta = self._open_live_trades.get(order_id)
            if source_meta is None:
                logger.warning(f"Received fill for untracked order {order_id}; settlement mapping unavailable")
                return False
            meta = copy.deepcopy(source_meta)

            previous_qty = Decimal(str(meta.get("filled_qty") or "0"))
            previous_notional = Decimal(str(meta.get("filled_notional") or "0"))
            first_recorded_fill = previous_qty <= 0
            fill_notional = fill_price * fill_qty
            total_qty = previous_qty + fill_qty
            total_notional = previous_notional + fill_notional
            if total_qty <= 0:
                logger.warning(f"Ignoring non-positive cumulative fill quantity for {order_id}")
                return False

            average_price = total_notional / total_qty
            meta["entry_price"] = average_price
            meta["filled_qty"] = total_qty
            meta["filled_notional"] = total_notional
            meta["size"] = total_notional
            meta["filled_at"] = datetime.now(timezone.utc)
            meta["order_id"] = order_id

            open_trades = dict(self._open_live_trades)
            open_trades[order_id] = meta
            saved_state = self._try_save_live_trade_ledger_state(
                "Failed to persist live order fill",
                open_trades=open_trades,
                settled_trades=self._settled_live_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
            )
            if saved_state is None:
                return False

            self._open_live_trades = dict(saved_state["open"])
            self._settled_live_trades = list(saved_state["settled"])
            self._seen_auto_redeem_events = set(saved_state["seen"])
            self._seen_auto_redeem_event_order = list(saved_state["seen_order"])
            self._pending_auto_redeem_events = dict(saved_state["pending"])
            self._submitted_positions.pop(order_id, None)

            try:
                self.risk_engine.adjust_position(
                    position_id=order_id,
                    size=total_notional,
                    entry_price=average_price,
                    direction="buy_yes" if meta.get("direction") == "long" else "buy_no",
                )
            except Exception as e:
                logger.warning(f"Failed to adjust risk position for fill {order_id}: {e}")
            logger.info(
                f"Tracking live trade for settlement: {order_id} "
                f"filled_qty={total_qty:.6f} notional=${total_notional:.6f} "
                f"avg=${average_price:.4f} slug={meta.get('slug')} "
                f"condition_id={meta.get('condition_id')}"
            )
            if first_recorded_fill and self._pending_auto_redeem_events:
                self._retry_pending_auto_redeems("first fill recorded")
            return True

    def on_order_filled(self, event):
        order_id = str(event.client_order_id)
        fill_price = self._as_decimal(event.last_px)
        fill_qty = self._as_decimal(event.last_qty)
        logger.info("=" * 80)
        logger.info(f"ORDER FILLED!")
        logger.info(f"  Order: {order_id}")
        logger.info(f"  Fill Price: ${float(fill_price):.4f}")
        logger.info(f"  Quantity: {float(fill_qty):.6f}")
        logger.info("=" * 80)
        if self._record_live_order_fill(order_id, fill_price, fill_qty):
            self._track_order_event("filled")

    def on_order_denied(self, event):
        logger.error("=" * 80)
        logger.error(f"ORDER DENIED!")
        logger.error(f"  Order: {event.client_order_id}")
        logger.error(f"  Reason: {event.reason}")
        logger.error("=" * 80)
        self._release_submitted_position(event.client_order_id)
        reason_lower = str(event.reason).lower()
        if (
            "no-price-to-convert-quote-qty" in reason_lower
            or "no orders found" in reason_lower
            or "fak" in reason_lower
            or "no match" in reason_lower
            or "below_minimum" in reason_lower
            or "below minimum" in reason_lower
        ):
            self.last_trade_time = -1
        self._track_order_event("rejected")

    def on_order_rejected(self, event):
        """Handle order rejection — reset trade timer so we can retry next tick."""
        reason = str(getattr(event, 'reason', ''))
        reason_lower = reason.lower()
        if 'no orders found' in reason_lower or 'fak' in reason_lower or 'no match' in reason_lower:
            logger.warning(
                f"⚠ FAK rejected (no liquidity) — resetting timer to retry next tick\n"
                f"  Reason: {reason}"
            )
            self.last_trade_time = -1  # Allow retry on next quote tick
        else:
            logger.warning(f"Order rejected: {reason}")
        self._release_submitted_position(getattr(event, "client_order_id", ""))

    # ------------------------------------------------------------------
    # Grafana / stop
    # ------------------------------------------------------------------

    def _start_grafana_sync(self):
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.grafana_exporter.start())
            logger.info("Grafana metrics started on port 8000")
        except Exception as e:
            logger.error(f"Failed to start Grafana: {e}")

    def on_stop(self):
        logger.info("Integrated BTC strategy stopped")
        logger.info(f"Total simulation decision observations recorded: {len(self.paper_trades)}")
        if self._auto_redeem_registered:
            unregister_auto_redeem_handler(self._auto_redeem_handler)
            self._auto_redeem_registered = False
        try:
            self._save_live_trade_ledger()
        finally:
            self._release_live_trade_ledger_lock()
        if self.grafana_exporter:
            import asyncio
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.grafana_exporter.stop())
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_integrated_bot(simulation: bool = True, enable_grafana: bool = True, test_mode: bool = False):
    """Run the integrated BTC 15-min trading bot - LOADS ALL BTC MARKETS FOR THE DAY"""
    
    print("=" * 80)
    print("INTEGRATED POLYMARKET BTC 15-MIN TRADING BOT")
    print("Nautilus + 7-Phase System + Redis Control")
    print("=" * 80)

    if not simulation:
        if not patch_applied:
            raise RuntimeError("Live mode requires market order patch to be applied")
        if not v2_patch_applied:
            raise RuntimeError("Live mode requires Polymarket CLOB v2 compatibility patch")

    redis_client = init_redis()

    if redis_client:
        try:
            # ALWAYS overwrite Redis with the current session mode.
            # This prevents a stale value from a previous --live run
            # silently overriding --test-mode or --simulation runs.
            mode_value = '1' if simulation else '0'
            redis_client.set('btc_trading:simulation_mode', mode_value)
            mode_label = 'SIMULATION' if simulation else 'LIVE'
            logger.info(f"Redis simulation_mode forced to: {mode_label} ({mode_value})")
        except Exception as e:
            if not simulation:
                raise RuntimeError("Live mode requires writable Redis mode control") from e
            logger.warning(f"Could not set Redis simulation mode: {e}; Redis control disabled for simulation")
            redis_client = None
    elif not simulation:
        raise RuntimeError("Live mode requires Redis mode control") from _last_redis_init_error

    print(f"\nConfiguration:")
    print(f"  Initial Mode: {'SIMULATION' if simulation else 'LIVE TRADING'}")
    print(f"  Redis Control: {'Enabled' if redis_client else 'Disabled'}")
    print(f"  Grafana: {'Enabled' if enable_grafana else 'Disabled'}")
    print(f"  Max Trade Size: ${os.getenv('MARKET_BUY_USD', '1.00')}")
    print(f"  Quote stability gate: {QUOTE_STABILITY_REQUIRED} valid ticks")
    print()

    now = datetime.now(timezone.utc)
    
    # =========================================================================
    # Slug timestamps ARE standard Unix timestamps (no offset) aligned to
    # 15-min boundaries. Generate slugs for current + next 24 hours.
    # =========================================================================
    now = datetime.now(timezone.utc)
    unix_interval_start = (int(now.timestamp()) // 900) * 900  # current 15-min boundary

    btc_slugs = []
    for i in range(-1, 97):  # include 1 prior interval (in case we're just after boundary)
        timestamp = unix_interval_start + (i * 900)
        btc_slugs.append(f"btc-updown-15m-{timestamp}")

    filters = {
        "active": True,
        "closed": False,
        "archived": False,
        "slug": tuple(btc_slugs),
        "limit": 100,
    }

    logger.info("=" * 80)
    logger.info("LOADING BTC 15-MIN MARKETS BY SLUG")
    logger.info(f"  Interval start: {unix_interval_start} | Count: {len(btc_slugs)}")
    logger.info(f"  First: {btc_slugs[0]}  Last: {btc_slugs[-1]}")
    logger.info("=" * 80)

    instrument_cfg = InstrumentProviderConfig(
        load_all=True,
        filters=filters,
        use_gamma_markets=True,
    )

    polymarket_creds = get_polymarket_runtime_credentials(simulation=simulation)
    if simulation and not os.getenv("POLYMARKET_PK"):
        logger.warning("Simulation mode using dummy Polymarket credentials; no live orders can be placed")

    polymarket_private_key = str(polymarket_creds["private_key"])
    polymarket_funder = str(polymarket_creds["funder"])
    polymarket_api_key = str(polymarket_creds["api_key"])
    polymarket_api_secret = str(polymarket_creds["api_secret"])
    polymarket_passphrase = str(polymarket_creds["passphrase"])
    polymarket_signature_type = int(polymarket_creds["signature_type"])
    poly_data_cfg = PolymarketDataClientConfig(
        private_key=polymarket_private_key,
        api_key=polymarket_api_key,
        api_secret=polymarket_api_secret,
        passphrase=polymarket_passphrase,
        signature_type=polymarket_signature_type,
        funder=polymarket_funder,
        instrument_provider=instrument_cfg,
    )

    poly_exec_cfg = None
    if not simulation:
        poly_exec_cfg = PolymarketExecClientConfig(
            private_key=polymarket_private_key,
            api_key=polymarket_api_key,
            api_secret=polymarket_api_secret,
            passphrase=polymarket_passphrase,
            signature_type=polymarket_signature_type,
            funder=polymarket_funder,
            instrument_provider=instrument_cfg,
        )

    config = TradingNodeConfig(
        environment="live",
        trader_id="BTC-15MIN-INTEGRATED-001",
        logging=LoggingConfig(
            log_level="INFO",
            log_directory="./logs/nautilus",
        ),
        data_engine=LiveDataEngineConfig(qsize=6000),
        exec_engine=LiveExecEngineConfig(
            qsize=6000,
            convert_quote_qty_to_base=False,
        ),
        risk_engine=LiveRiskEngineConfig(bypass=simulation),
        data_clients={POLYMARKET: poly_data_cfg},
        exec_clients={POLYMARKET: poly_exec_cfg} if poly_exec_cfg else {},
    )

    strategy = IntegratedBTCStrategy(
        redis_client=redis_client,
        enable_grafana=enable_grafana,
        test_mode=test_mode,
        simulation_mode=simulation,
    )

    print("\nBuilding Nautilus node...")
    node = TradingNode(config=config)
    node.add_data_client_factory(POLYMARKET, PolymarketLiveDataClientFactory)
    if poly_exec_cfg:
        node.add_exec_client_factory(POLYMARKET, PolymarketLiveExecClientFactory)
    node.trader.add_strategy(strategy)
    node.build()
    logger.info("Nautilus node built successfully")

    print()
    print("=" * 80)
    print("BOT STARTING")
    print("=" * 80)

    try:
        node.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        node.dispose()
        logger.info("Bot stopped")

def parse_runtime_args(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Integrated BTC 15-Min Trading Bot")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", action="store_true",
                            help="Run in LIVE mode (real money at risk!). Default is simulation.")
    mode_group.add_argument("--test-mode", action="store_true",
                            help="Run in TEST MODE (decision observations every minute)")
    parser.add_argument("--no-grafana", action="store_true", help="Disable Grafana metrics")
    return parser.parse_args(argv)


def main():
    args = parse_runtime_args()
    enable_grafana = not args.no_grafana
    test_mode = args.test_mode

    simulation = not args.live

    if not simulation:
        logger.warning("=" * 80)
        logger.warning("LIVE TRADING MODE — REAL MONEY AT RISK!")
        logger.warning("=" * 80)
    else:
        logger.info("=" * 80)
        logger.info(f"SIMULATION MODE — {'TEST MODE (fast clock)' if test_mode else 'decision observation only'}")
        logger.info("No real orders will be placed.")
        logger.info("=" * 80)

    run_integrated_bot(simulation=simulation, enable_grafana=enable_grafana, test_mode=test_mode)


if __name__ == "__main__":
    main()
