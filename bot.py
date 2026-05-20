import asyncio
import copy
import hashlib
import json
import os
import fcntl
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import math
from decimal import Decimal, InvalidOperation, ROUND_DOWN
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
from nautilus_trader.model.objects import Price, Quantity
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
    register_actual_fill_handler,
    register_auto_redeem_handler,
    unregister_actual_fill_handler,
    unregister_auto_redeem_handler,
)
from decision_log import DecisionRecord
from depth_estimator import (
    InvalidBookLevelError,
    estimate_limit_ioc_fill,
    estimate_market_ioc_fill,
)
patch_applied = False

from polymarket_v2_compat import apply_polymarket_v2_patch
v2_patch_applied = apply_polymarket_v2_patch()
if v2_patch_applied:
    logger.info("Polymarket CLOB v2 compatibility patch applied successfully")
else:
    logger.error("Polymarket CLOB v2 compatibility patch failed")

from patch_polymarket_quote_warnings import apply_polymarket_quote_warning_patch
try:
    quote_warning_patch_applied = apply_polymarket_quote_warning_patch()
except Exception as exc:
    quote_warning_patch_applied = False
    logger.error(f"Polymarket 'Dropping QuoteTick' warning filter failed: {exc}")
else:
    if quote_warning_patch_applied:
        logger.info("Polymarket 'Dropping QuoteTick' warning filter applied")
    else:
        logger.error("Polymarket 'Dropping QuoteTick' warning filter could not be applied")


# =============================================================================
# CONSTANTS
# =============================================================================
QUOTE_MIN_SPREAD = 0.001          # Both bid AND ask must be at least this
MARKET_INTERVAL_SECONDS = 900     # 15-minute markets
LIVE_TRADE_LEDGER_SCHEMA_VERSION = 3
DEFAULT_LIVE_SETTLEMENT_GRACE_SECONDS = 3600
SETTLEMENT_ACCOUNTING_COST_TOLERANCE = Decimal("1E-18")
ACTUAL_FILL_UNIQUE_KEY_FIELDS = (
    "fill_id",
    "trade_id",
    "match_id",
    "event_id",
    "transaction_hash",
    "txn_hash",
)
_ledger_path = Path(os.getenv("LIVE_TRADE_LEDGER_PATH", "live_trades.json"))
LIVE_TRADE_LEDGER_PATH = _ledger_path if _ledger_path.is_absolute() else project_root / _ledger_path
TERMINAL_NO_FILL_INTENT_STATUSES = frozenset(
    {
        "ORDER_DENIED_NO_FILL",
        "ORDER_REJECTED_NO_FILL",
        "ORDER_CANCELED_NO_FILL",
        "ORDER_EXPIRED_NO_FILL",
    }
)


@dataclass(frozen=True)
class DepthAwareEntry:
    executable_entry: Decimal
    tokens_filled: Decimal
    actual_cost: Decimal
    fully_filled: bool


@dataclass(frozen=True)
class TerminalFillDetails:
    quantity: Decimal
    price: Decimal
    field_source: str
    quantity_semantics: str
    price_semantics: str


_ORDER_BOOK_NOT_PROVIDED = object()


def _fsync_parent_directory(path: Path) -> None:
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


AUTO_REDEEM_TOKEN_HINT_KEYS = (
    "asset_id",
    "assetId",
    "token_id",
    "tokenId",
    "clobTokenId",
    "clob_token_id",
)
FILL_METADATA_IDENTITY_KEYS = (
    "venue_order_id",
    "condition_id",
    "token_id",
    "slug",
    "instrument_id",
)
FILL_INFO_CONDITION_HINT_KEYS = (
    "condition_id",
    "conditionId",
    "market",
    "market_id",
    "marketId",
)
FILL_INFO_SLUG_HINT_KEYS = (
    "slug",
    "market_slug",
    "marketSlug",
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


LIVE_MIN_MARKET_BUY_USD = Decimal("5.50")


# --- Phase 2.5 sizing mode validation -------------------------------------

SIZING_MODE_FIXED = "fixed"
SIZING_MODE_PERCENT = "percent"
_ALLOWED_SIZING_MODES = frozenset({SIZING_MODE_FIXED, SIZING_MODE_PERCENT})


def get_sizing_mode_for_live() -> str:
    """Phase 2.5 — required env var when ``--live``. No implicit default.

    Returns the validated sizing mode string. Raises ``RuntimeError`` if
    ``SIZING_MODE`` is missing or not one of ``fixed`` / ``percent``.
    """
    raw = os.getenv("SIZING_MODE")
    if raw is None or raw == "":
        raise RuntimeError(
            "SIZING_MODE must be set to 'fixed' or 'percent' for live trading"
        )
    if raw not in _ALLOWED_SIZING_MODES:
        raise RuntimeError(
            f"SIZING_MODE must be 'fixed' or 'percent', got {raw!r}"
        )
    return raw


# --- Order-type validation ------------------------------------------------

ORDER_TYPE_MARKET_IOC = "market_ioc"
ORDER_TYPE_LIMIT_IOC = "limit_ioc"
_ALLOWED_ORDER_TYPES = frozenset({ORDER_TYPE_MARKET_IOC, ORDER_TYPE_LIMIT_IOC})
LIMIT_IOC_FILL_POLICY_PARTIAL_OK = "partial_ok"
LIMIT_IOC_FILL_POLICY_ALL_OR_NOTHING = "all_or_nothing"
_ALLOWED_LIMIT_IOC_FILL_POLICIES = frozenset(
    {LIMIT_IOC_FILL_POLICY_PARTIAL_OK, LIMIT_IOC_FILL_POLICY_ALL_OR_NOTHING}
)


def get_order_type_for_live() -> str:
    """Required env var for order-capable runtime. No implicit default.

    Returns the validated order type. Raises ``RuntimeError`` if
    ``ORDER_TYPE`` is missing or not one of ``market_ioc`` / ``limit_ioc``.
    """
    raw = os.getenv("ORDER_TYPE")
    if raw is None or raw == "":
        raise RuntimeError(
            "ORDER_TYPE must be set to 'market_ioc' or 'limit_ioc'"
        )
    if raw not in _ALLOWED_ORDER_TYPES:
        raise RuntimeError(
            f"ORDER_TYPE must be 'market_ioc' or 'limit_ioc', got {raw!r}"
        )
    return raw


def get_quote_stability_required_for_live() -> int:
    """Required quote-stability threshold. No implicit default."""
    raw = os.getenv("QUOTE_STABILITY_REQUIRED")
    if raw is None or raw == "":
        raise RuntimeError(
            "QUOTE_STABILITY_REQUIRED must be set to a positive integer"
        )
    try:
        required = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"QUOTE_STABILITY_REQUIRED must be a positive integer, got {raw!r}"
        ) from exc
    if required <= 0:
        raise RuntimeError(f"QUOTE_STABILITY_REQUIRED must be > 0, got {required}")
    return required


def get_limit_ioc_fill_policy_for_live(order_type: str) -> Optional[str]:
    """Validate LIMIT_IOC partial-fill policy for live trading."""
    if order_type != ORDER_TYPE_LIMIT_IOC:
        return None
    policy = os.getenv("LIMIT_IOC_FILL_POLICY")
    if policy not in _ALLOWED_LIMIT_IOC_FILL_POLICIES:
        raise RuntimeError(
            "LIMIT_IOC_FILL_POLICY must be set to 'partial_ok' or 'all_or_nothing' "
            "when ORDER_TYPE=limit_ioc"
        )
    if policy == LIMIT_IOC_FILL_POLICY_ALL_OR_NOTHING:
        raise RuntimeError(
            "LIMIT_IOC_FILL_POLICY=all_or_nothing requires verified FOK wire behavior; "
            "current LIMIT+IOC wire behavior is FAK"
        )
    return policy


def validate_live_order_config() -> Dict[str, Any]:
    """Validate live order configuration as one explicit contract."""
    order_type = get_order_type_for_live()
    quote_stability_required = get_quote_stability_required_for_live()
    fill_policy = get_limit_ioc_fill_policy_for_live(order_type)
    limit_required_edge = None
    ev_fee_buffer, ev_spread_buffer = get_validated_ev_buffers()
    if order_type == ORDER_TYPE_LIMIT_IOC:
        limit_required_edge = get_validated_limit_required_edge()
        if limit_required_edge < ev_fee_buffer + ev_spread_buffer:
            raise RuntimeError(
                "LIMIT_REQUIRED_EDGE must be >= EV_FEE_BUFFER + EV_SPREAD_BUFFER "
                f"when ORDER_TYPE=limit_ioc ({limit_required_edge} < "
                f"{ev_fee_buffer + ev_spread_buffer})"
            )
    return {
        "order_type": order_type,
        "quote_stability_required": quote_stability_required,
        "limit_ioc_fill_policy": fill_policy,
        "limit_required_edge": limit_required_edge,
        "ev_fee_buffer": ev_fee_buffer,
        "ev_spread_buffer": ev_spread_buffer,
    }


def trade_window_label_for_seconds_into_sub_interval(seconds: float) -> str:
    """Phase 4.5 — classify the elapsed seconds into one of the candidate
    trade windows defined in EXECUTION_PLAN.md Phase 4.5:

      - ``06_09``: 360-539 s  (6:00-8:59 into the 15-min market)
      - ``09_11``: 540-659 s
      - ``11_13``: 660-779 s
      - ``13_14_current``: 780-839 s (current live baseline)
      - ``14_15_late``: 840-899 s (post-baseline late window)
      - ``before_06`` / ``after_15``: out of every candidate window

    The bot's current live trade window is exactly ``13_14_current``
    (780-840 seconds). The other buckets are observation-only labels so
    Phase 4.5 calibration can compare candidate windows against the live
    baseline without changing the live gate.
    """
    if seconds < 360:
        return "before_06"
    if seconds < 540:
        return "06_09"
    if seconds < 660:
        return "09_11"
    if seconds < 780:
        return "11_13"
    if seconds < 840:
        return "13_14_current"
    if seconds < 900:
        return "14_15_late"
    return "after_15"


def trend_price_band_for(yes_price: float) -> str:
    """Phase 4.5 — classify the YES price into one of the six bands defined in
    EXECUTION_PLAN.md Phase 4.5 (moderate / strong / extreme on each side).
    The neutral middle band uses the strict ``(0.40, 0.60)`` open interval
    consistent with the trend filter's own thresholds.
    """
    if yes_price >= 0.70:
        return "yes_extreme_ge_0.70"
    if yes_price >= 0.60:
        return "yes_strong_0.60_0.70"
    if yes_price > 0.48:
        # 0.48 < yes < 0.60 — moderate YES side
        return "yes_moderate_0.48_0.60"
    if yes_price > 0.40:
        # 0.40 < yes <= 0.48 — moderate NO side
        return "no_moderate_0.40_0.48"
    if yes_price > 0.30:
        return "no_strong_0.30_0.40"
    return "no_extreme_le_0.30"


POLYMARKET_LIMIT_MIN_TOKENS = Decimal("5")


def compute_limit_price(
    fused_confidence: float, limit_required_edge: Decimal
) -> Optional[Decimal]:
    """Phase 3 — compute the limit-price cap from fused confidence.

    ``fused.confidence`` is confidence in the selected direction (BULLISH or
    BEARISH); it is NOT a raw YES-probability. The cap is the same formula
    for both long (buy YES) and short (buy NO):

        cap = fused_confidence - limit_required_edge

    Returns ``None`` if the resulting cap is outside ``(0, 1)`` — the caller
    must reject the trade and log the reason. No clamping, no defaulting.
    """
    conf = Decimal(str(fused_confidence))
    cap = conf - limit_required_edge
    if cap <= Decimal("0") or cap >= Decimal("1"):
        return None
    return cap


def compute_limit_order_token_qty(
    budget_usd: Decimal,
    limit_price: Decimal,
    size_precision: int,
) -> Optional[Decimal]:
    """Phase 3 — compute the token quantity for a LIMIT_IOC order.

    Conservative sizing: ``token_qty = budget / limit_price`` rounded DOWN
    to ``size_precision`` decimal places so the worst-case spend (at the
    limit price) never exceeds the budget. Returns ``None`` if the rounded
    token quantity is below the Polymarket 5-token limit-order minimum;
    caller must reject the trade and log the reason.
    """
    if budget_usd <= 0:
        raise ValueError(f"budget_usd must be positive, got {budget_usd}")
    if limit_price <= 0 or limit_price >= 1:
        raise ValueError(
            f"limit_price must be in (0, 1), got {limit_price}"
        )
    if size_precision < 0:
        raise ValueError(
            f"size_precision must be non-negative, got {size_precision}"
        )
    raw_token_qty = budget_usd / limit_price
    quantize_to = Decimal(10) ** -size_precision if size_precision > 0 else Decimal("1")
    token_qty = raw_token_qty.quantize(quantize_to, rounding=ROUND_DOWN)
    if token_qty < POLYMARKET_LIMIT_MIN_TOKENS:
        return None
    return token_qty


def derive_submitted_limit_price(
    accepted_limit_price: Decimal,
    price_precision: int,
) -> Decimal:
    """Round a BUY limit cap down to the venue price precision.

    The submitted price must never exceed the EV-accepted cap. A caller must
    use this returned value consistently for depth, sizing, risk, intent, and
    order submission.
    """
    if price_precision < 0:
        raise ValueError(
            f"price_precision must be non-negative, got {price_precision}"
        )
    price_quantum = Decimal(10) ** -price_precision if price_precision > 0 else Decimal("1")
    submitted_limit_price = Decimal(str(accepted_limit_price)).quantize(
        price_quantum,
        rounding=ROUND_DOWN,
    )
    if submitted_limit_price <= 0 or submitted_limit_price > accepted_limit_price:
        raise RuntimeError(
            f"safe submitted limit price cannot be derived from cap {accepted_limit_price}"
        )
    return submitted_limit_price


def get_validated_limit_required_edge() -> Decimal:
    """Phase 3 — required env var when ``ORDER_TYPE=limit_ioc``.

    Strict range (0, 1). No clamping. Raises ``RuntimeError`` on any invalid
    input so impossible values fail fast at startup, not silently inside the
    limit-price computation.
    """
    raw = os.getenv("LIMIT_REQUIRED_EDGE")
    if raw is None or raw == "":
        raise RuntimeError(
            "LIMIT_REQUIRED_EDGE must be set when ORDER_TYPE=limit_ioc"
        )
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise RuntimeError(
            f"LIMIT_REQUIRED_EDGE must be a decimal, got {raw!r}"
        ) from exc
    if not value.is_finite():
        raise RuntimeError(
            f"LIMIT_REQUIRED_EDGE must be finite, got {raw!r}"
        )
    if value <= Decimal("0") or value >= Decimal("1"):
        raise RuntimeError(
            f"LIMIT_REQUIRED_EDGE must be in (0, 1) — got {value}. A value "
            f"outside this range cannot produce a usable limit price for a "
            f"Polymarket binary outcome token."
        )
    return value


def _get_required_decimal_env(name: str) -> Decimal:
    raw = os.getenv(name)
    if raw is None or raw == "":
        raise RuntimeError(f"{name} must be set to a decimal value")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise RuntimeError(f"{name} must be a decimal, got {raw!r}") from exc
    if not value.is_finite():
        raise RuntimeError(f"{name} must be finite, got {raw!r}")
    return value


def get_validated_ev_buffers() -> tuple[Decimal, Decimal]:
    """Validate EV-gate buffers used by both decision and startup checks."""
    fee_buffer = _get_required_decimal_env("EV_FEE_BUFFER")
    spread_buffer = _get_required_decimal_env("EV_SPREAD_BUFFER")
    if fee_buffer < Decimal("0"):
        raise RuntimeError(f"EV_FEE_BUFFER must be >= 0, got {fee_buffer}")
    if spread_buffer < Decimal("0"):
        raise RuntimeError(f"EV_SPREAD_BUFFER must be >= 0, got {spread_buffer}")
    if fee_buffer + spread_buffer >= Decimal("1"):
        raise RuntimeError(
            "EV_FEE_BUFFER + EV_SPREAD_BUFFER must be < 1, "
            f"got {fee_buffer + spread_buffer}"
        )
    return fee_buffer, spread_buffer


def get_pct_of_free_collateral_per_trade() -> Decimal:
    """Phase 2.5 — required env var when ``SIZING_MODE=percent``.

    Returns the validated ``Decimal`` in (0, 1). Raises ``RuntimeError`` on
    missing, malformed, non-finite, or out-of-range values.
    """
    raw = os.getenv("PCT_OF_FREE_COLLATERAL_PER_TRADE")
    if raw is None or raw == "":
        raise RuntimeError(
            "PCT_OF_FREE_COLLATERAL_PER_TRADE must be set when SIZING_MODE=percent"
        )
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise RuntimeError(
            f"PCT_OF_FREE_COLLATERAL_PER_TRADE must be a decimal, got {raw!r}"
        ) from exc
    if not value.is_finite():
        raise RuntimeError(
            f"PCT_OF_FREE_COLLATERAL_PER_TRADE must be finite, got {raw!r}"
        )
    if value <= Decimal("0") or value >= Decimal("1"):
        raise RuntimeError(
            f"PCT_OF_FREE_COLLATERAL_PER_TRADE must be in (0, 1) — got {value}"
        )
    return value


def _live_market_buy_usd_blocked_message(raw_value) -> str:
    formatted_current = raw_value if raw_value is not None else "<unset>"
    return (
        "LIVE STARTUP BLOCKED: MARKET_BUY_USD must be greater than 5.50 USDC for live mode.\n"
        f"Current MARKET_BUY_USD={formatted_current}. "
        "Increase it to at least 5.51 or run without --live."
    )


def validate_live_market_buy_usd():
    """Phase 0.3 strict validator. Returns (ok, error_msg, validated_amount).

    Reads ``MARKET_BUY_USD`` once and validates it against the live gate.
    Quantizes BEFORE comparing so that ``5.5000001`` cannot slip past the
    strict-inequality and then round down to the blocked ``5.50`` value.

    Returns a structured result rather than raising so both startup (fail-stop)
    and runtime (fail-closed reject) call sites can share one source of truth
    without try/except converting exceptions into control flow.
    """
    raw_value = os.getenv("MARKET_BUY_USD")
    blocked = _live_market_buy_usd_blocked_message(raw_value)
    if raw_value is None:
        return False, blocked, None
    try:
        amount = Decimal(raw_value)
    except InvalidOperation:
        return False, blocked, None
    if not amount.is_finite():
        return False, blocked, None
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if quantized <= LIVE_MIN_MARKET_BUY_USD:
        return False, blocked, None
    return True, None, quantized


def enforce_live_market_buy_usd_gate() -> Decimal:
    """Phase 0.3 live startup gate: MARKET_BUY_USD must be strictly > 5.50.

    Used at process startup when --live is passed. Raises ``RuntimeError`` on
    any invalid value so startup fails closed. Runtime checks should use
    ``validate_live_market_buy_usd()`` directly to inspect the result without
    going through exception control flow.
    """
    ok, err, amount = validate_live_market_buy_usd()
    if not ok:
        raise RuntimeError(err)
    return amount


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
        self._quote_stability_required = get_quote_stability_required_for_live()
        self._quote_stability_missing_logged = False
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
        self._pending_actual_fills: Dict[str, Dict[str, Any]] = {}
        self._submitted_order_intents: Dict[str, Dict[str, Any]] = {}
        self._settlement_lock = threading.RLock()
        self._settlement_ledger_blocked_reason: Optional[str] = None
        self._ledger_lock_file = None
        self._auto_redeem_registered = False
        self._auto_redeem_handler = self._handle_auto_redeem_event
        self._actual_fill_registered = False
        self._actual_fill_handler = self._handle_actual_fill
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
        try:
            self._rehydrate_settled_daily_risk()
            self._rehydrate_open_settlement_risk()
        except Exception:
            self._release_live_trade_ledger_lock()
            raise

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
        self._quote_stability_missing_logged = False

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
        """Return an internally consistent auto_redeem dedupe index."""
        seen_events = set(seen_events)
        seen_order = list(seen_order)
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
        pending_actual_fills,
        submitted_order_intents,
    ) -> Dict[str, Any]:
        """Prepare a ledger state for writing without mutating current bot state."""
        normalized_seen, normalized_seen_order = self._normalize_seen_auto_redeem_state(
            seen_events,
            seen_order,
        )
        normalized_pending = copy.deepcopy(pending_events)
        return {
            "open": dict(open_trades),
            "settled": list(settled_trades),
            "seen": normalized_seen,
            "seen_order": normalized_seen_order,
            "pending": normalized_pending,
            "pending_actual_fills": copy.deepcopy(pending_actual_fills),
            "submitted_order_intents": copy.deepcopy(submitted_order_intents),
        }

    def _write_live_trade_ledger_state(self, state: Dict[str, Any]) -> None:
        """Write a prepared live-trade ledger state to disk."""
        data = {
            "ledger_schema_version": LIVE_TRADE_LEDGER_SCHEMA_VERSION,
            "open": self._jsonable(dict(state["open"])),
            "settled": self._jsonable(list(state["settled"])),
            "seen_auto_redeem_events": list(state["seen_order"]),
            "pending_auto_redeem_events": self._jsonable(dict(state["pending"])),
            "pending_actual_fills": self._jsonable(dict(state["pending_actual_fills"])),
            "submitted_order_intents": self._jsonable(dict(state["submitted_order_intents"])),
        }
        payload = json.dumps(data, indent=2, sort_keys=True)

        LIVE_TRADE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = LIVE_TRADE_LEDGER_PATH.with_name(LIVE_TRADE_LEDGER_PATH.name + ".tmp")

        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, LIVE_TRADE_LEDGER_PATH)
        _fsync_parent_directory(LIVE_TRADE_LEDGER_PATH)

    def _save_live_trade_ledger_state(
        self,
        open_trades,
        settled_trades,
        seen_events,
        seen_order,
        pending_events,
        pending_actual_fills,
        submitted_order_intents,
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
                    pending_actual_fills=pending_actual_fills,
                    submitted_order_intents=submitted_order_intents,
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
                pending_actual_fills=self._pending_actual_fills,
                submitted_order_intents=self._submitted_order_intents,
            )
            self._seen_auto_redeem_events = set(state["seen"])
            self._seen_auto_redeem_event_order = list(state["seen_order"])
            self._pending_auto_redeem_events = dict(state["pending"])
            self._pending_actual_fills = dict(state["pending_actual_fills"])
            self._submitted_order_intents = dict(state["submitted_order_intents"])

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
        pending_actual_fills,
        submitted_order_intents,
    ) -> Optional[Dict[str, Any]]:
        """Persist a candidate ledger state without propagating framework-callback exceptions."""
        try:
            return self._save_live_trade_ledger_state(
                open_trades=open_trades,
                settled_trades=settled_trades,
                seen_events=seen_events,
                seen_order=seen_order,
                pending_events=pending_events,
                pending_actual_fills=pending_actual_fills,
                submitted_order_intents=submitted_order_intents,
            )
        except SettlementLedgerError as exc:
            logger.error(f"{context}; live trading blocked until ledger is repaired: {exc}")
            return None

    def _apply_saved_live_trade_ledger_state(self, state: Dict[str, Any]) -> None:
        """Replace mutable settlement state with a successfully persisted state."""
        self._open_live_trades = dict(state["open"])
        self._settled_live_trades = list(state["settled"])
        self._seen_auto_redeem_events = set(state["seen"])
        self._seen_auto_redeem_event_order = list(state["seen_order"])
        self._pending_auto_redeem_events = dict(state["pending"])
        self._pending_actual_fills = dict(state["pending_actual_fills"])
        self._submitted_order_intents = dict(state["submitted_order_intents"])

    def _validate_live_trade_ledger_schema_locked(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Reject ledgers that are not already in the current schema."""
        schema_version = data.get("ledger_schema_version")
        if schema_version != LIVE_TRADE_LEDGER_SCHEMA_VERSION:
            raise SettlementLedgerError(
                f"live trade ledger schema_version must be {LIVE_TRADE_LEDGER_SCHEMA_VERSION}; "
                f"found {schema_version!r}. Replace it with a current schema v3 ledger before startup."
            )
        self._validate_live_trade_ledger_core_sections(data)
        return data

    def _validate_live_trade_ledger_core_sections(self, data: Dict[str, Any]) -> None:
        for section in (
            "open",
            "settled",
            "seen_auto_redeem_events",
            "pending_auto_redeem_events",
            "pending_actual_fills",
            "submitted_order_intents",
        ):
            if section not in data:
                raise SettlementLedgerError(f"live trade ledger missing required section: {section}")
        if not isinstance(data["open"], dict):
            raise SettlementLedgerError("live trade ledger open section must be a JSON object")
        if not isinstance(data["settled"], list):
            raise SettlementLedgerError("live trade ledger settled section must be a JSON list")
        if not isinstance(data["seen_auto_redeem_events"], list):
            raise SettlementLedgerError("seen_auto_redeem_events must be a JSON list")
        if not isinstance(data["pending_auto_redeem_events"], dict):
            raise SettlementLedgerError("pending_auto_redeem_events must be a JSON object")
        if not isinstance(data["pending_actual_fills"], dict):
            raise SettlementLedgerError("pending_actual_fills must be a JSON object")
        if not isinstance(data["submitted_order_intents"], dict):
            raise SettlementLedgerError("submitted_order_intents must be a JSON object")
        for order_id, meta in data["open"].items():
            if not isinstance(meta, dict):
                raise SettlementLedgerError(f"live trade ledger open[{order_id}] must be a JSON object")
        for index, trade in enumerate(data["settled"]):
            if not isinstance(trade, dict):
                raise SettlementLedgerError(f"live trade ledger settled[{index}] must be a JSON object")
        for event_key, payload in data["pending_auto_redeem_events"].items():
            if not isinstance(payload, dict):
                raise SettlementLedgerError(f"pending_auto_redeem_events[{event_key}] must be a JSON object")
        for order_id, pending in data["pending_actual_fills"].items():
            if not isinstance(pending, dict):
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}] must be a JSON object")
            self._validate_pending_actual_fill_aggregate(order_id, pending)
        for order_id, intent in data["submitted_order_intents"].items():
            if not isinstance(intent, dict):
                raise SettlementLedgerError(f"submitted_order_intents[{order_id}] must be a JSON object")

    def _validate_pending_actual_fill_aggregate(self, order_id: str, pending: Dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
        if "filled_qty" in pending:
            raise SettlementLedgerError(
                f"pending_actual_fills[{order_id}] scalar filled_qty is not valid; "
                "current schema requires aggregate fills[]"
            )
        fills = pending.get("fills")
        if not isinstance(fills, list) or not fills:
            raise SettlementLedgerError(f"pending_actual_fills[{order_id}].fills must be a non-empty JSON list")
        seen_fill_keys = set()
        summed_qty = Decimal("0")
        summed_notional = Decimal("0")
        for index, fill in enumerate(fills):
            if not isinstance(fill, dict):
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}].fills[{index}] must be a JSON object")
            fill_key = fill.get("fill_key")
            if fill_key in (None, ""):
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}].fills[{index}].fill_key is required")
            fill_key = str(fill_key)
            if fill_key in seen_fill_keys:
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}] duplicate fill_key={fill_key}")
            seen_fill_keys.add(fill_key)
            try:
                fill_qty = Decimal(str(fill["filled_qty"]))
                fill_price = Decimal(str(fill["price"]))
                fill_notional = Decimal(str(fill["notional"]))
            except Exception as exc:
                raise SettlementLedgerError(
                    f"pending_actual_fills[{order_id}].fills[{index}] has invalid decimal accounting"
                ) from exc
            if (
                not fill_qty.is_finite()
                or not fill_price.is_finite()
                or not fill_notional.is_finite()
                or fill_qty <= 0
                or fill_price <= 0
                or fill_price > 1
                or fill_notional <= 0
            ):
                raise SettlementLedgerError(
                    f"pending_actual_fills[{order_id}].fills[{index}] has impossible accounting"
                )
            if abs((fill_qty * fill_price) - fill_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
                raise SettlementLedgerError(
                    f"pending_actual_fills[{order_id}].fills[{index}] notional is inconsistent"
                )
            summed_qty += fill_qty
            summed_notional += fill_notional
        try:
            total_qty = Decimal(str(pending["total_filled_qty"]))
            total_notional = Decimal(str(pending["total_filled_notional"]))
            vwap = Decimal(str(pending["vwap"]))
        except Exception as exc:
            raise SettlementLedgerError(
                f"pending_actual_fills[{order_id}] missing or invalid aggregate accounting"
            ) from exc
        if (
            not total_qty.is_finite()
            or not total_notional.is_finite()
            or not vwap.is_finite()
            or total_qty <= 0
            or total_notional <= 0
            or vwap <= 0
            or vwap > 1
        ):
            raise SettlementLedgerError(f"pending_actual_fills[{order_id}] has impossible aggregate accounting")
        if abs(total_qty - summed_qty) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
            raise SettlementLedgerError(f"pending_actual_fills[{order_id}] total_filled_qty does not match fills[]")
        if abs(total_notional - summed_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
            raise SettlementLedgerError(
                f"pending_actual_fills[{order_id}] total_filled_notional does not match fills[]"
            )
        if abs((total_qty * vwap) - total_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
            raise SettlementLedgerError(f"pending_actual_fills[{order_id}] aggregate vwap is inconsistent")
        raw_submitted_size = pending.get("submitted_size")
        if raw_submitted_size not in (None, ""):
            try:
                submitted_size = Decimal(str(raw_submitted_size))
            except Exception as exc:
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}].submitted_size is invalid") from exc
            if not submitted_size.is_finite() or submitted_size <= 0:
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}].submitted_size must be positive")
        return total_qty, total_notional, vwap

    def _load_live_trade_ledger(self) -> None:
        """Load pending live trades from the previous bot process, if any."""
        with self._settlement_lock:
            if not LIVE_TRADE_LEDGER_PATH.exists():
                return
            try:
                data = json.loads(LIVE_TRADE_LEDGER_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise SettlementLedgerError("live trade ledger root is not a JSON object")
                data = self._validate_live_trade_ledger_schema_locked(data)
                self._validate_live_trade_ledger_core_sections(data)
                self._open_live_trades = dict(data["open"])
                self._settled_live_trades = list(data["settled"])
                self._seen_auto_redeem_event_order = list(data["seen_auto_redeem_events"])
                self._seen_auto_redeem_events = set(self._seen_auto_redeem_event_order)
                if (
                    set(self._seen_auto_redeem_event_order) != self._seen_auto_redeem_events
                    or len(self._seen_auto_redeem_event_order) != len(self._seen_auto_redeem_events)
                ):
                    raise SettlementLedgerError("seen auto_redeem event index is inconsistent")
                self._pending_auto_redeem_events = dict(data["pending_auto_redeem_events"])
                for event_key, payload in self._pending_auto_redeem_events.items():
                    if not isinstance(payload, dict):
                        raise SettlementLedgerError(
                            f"pending_auto_redeem_events[{event_key}] must be a JSON object"
                        )
                pending_actual_fills = data.get("pending_actual_fills")
                if not isinstance(pending_actual_fills, dict):
                    raise SettlementLedgerError("pending_actual_fills must be a JSON object")
                self._pending_actual_fills = dict(pending_actual_fills)
                for order_id, pending in self._pending_actual_fills.items():
                    if not isinstance(pending, dict):
                        raise SettlementLedgerError(
                            f"pending_actual_fills[{order_id}] must be a JSON object"
                        )
                submitted_order_intents = data.get("submitted_order_intents")
                if not isinstance(submitted_order_intents, dict):
                    raise SettlementLedgerError("submitted_order_intents must be a JSON object")
                self._submitted_order_intents = dict(submitted_order_intents)
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
            direction_raw = str(meta.get("direction") or "").lower()
            if direction_raw not in {"long", "short"}:
                reason = f"open live trade {order_id} has invalid direction for risk rehydrate: {meta.get('direction')!r}"
                self._block_live_settlement_ledger(reason)
                self._release_live_trade_ledger_lock()
                raise SettlementLedgerError(reason)
            try:
                size, _filled_qty, entry_price = self._settlement_accounting_values(order_id, meta)
            except SettlementLedgerError as exc:
                self._block_live_settlement_ledger(str(exc))
                self._release_live_trade_ledger_lock()
                raise
            try:
                self.risk_engine.add_position(
                    position_id=order_id,
                    size=size,
                    entry_price=entry_price,
                    direction="buy_yes" if direction_raw == "long" else "buy_no",
                    count_trade=False,
                )
            except Exception as exc:
                reason = f"failed to restore open settlement risk for {order_id}: {exc}"
                self._block_live_settlement_ledger(reason)
                self._release_live_trade_ledger_lock()
                raise SettlementLedgerError(reason) from exc

    def _rehydrate_settled_daily_risk(self) -> None:
        """Restore same-day realized settlement P&L after a process restart."""
        today = datetime.now().astimezone().date()
        daily_pnl = Decimal("0")
        daily_trades = 0

        with self._settlement_lock:
            settled_items = list(self._settled_live_trades)

        for trade in settled_items:
            unresolved = (
                trade.get("needs_reconciliation") is True
                or trade.get("settlement_source") == "SETTLEMENT_UNKNOWN"
            )
            settled_at = self._parse_utc_datetime(trade.get("settled_at"))
            if settled_at is None:
                if unresolved:
                    continue
                order_id = trade.get("order_id", "<unknown>")
                reason = f"settled trade {order_id} missing valid settled_at for daily risk rehydrate"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            if settled_at.astimezone().date() != today:
                continue
            if unresolved:
                continue
            pnl_value = trade.get("pnl")
            if pnl_value in (None, "", "UNKNOWN"):
                order_id = trade.get("order_id", "<unknown>")
                reason = f"settled trade {order_id} missing verified pnl for daily risk rehydrate"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            try:
                parsed_pnl = Decimal(str(pnl_value))
            except Exception as exc:
                order_id = trade.get("order_id", "<unknown>")
                reason = f"settled trade {order_id} has invalid pnl for daily risk rehydrate: {pnl_value!r}"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason) from exc
            if not parsed_pnl.is_finite():
                order_id = trade.get("order_id", "<unknown>")
                reason = f"settled trade {order_id} has non-finite pnl for daily risk rehydrate: {pnl_value!r}"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            daily_pnl += parsed_pnl
            daily_trades += 1

        if daily_trades:
            restore_daily_stats = getattr(self.risk_engine, "restore_daily_stats", None)
            if not callable(restore_daily_stats):
                reason = "risk engine missing restore_daily_stats for settled daily risk rehydrate"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            restore_daily_stats(daily_pnl, daily_trades)

    def _unresolved_settlement_unknowns(self) -> List[Dict[str, Any]]:
        """Return settled records that still need manual or REST reconciliation."""
        with self._settlement_lock:
            unresolved = [
                trade
                for trade in self._settled_live_trades
                if trade.get("needs_reconciliation") is True
                or trade.get("settlement_source") == "SETTLEMENT_UNKNOWN"
            ]
            for order_id, payload in self._pending_actual_fills.items():
                unresolved.append(
                    {
                        "order_id": str(order_id),
                        "settlement_source": "PENDING_ACTUAL_FILL",
                        "needs_reconciliation": True,
                        "unknown_reason": payload.get("_pending_reason"),
                    }
            )
            for order_id, payload in self._submitted_order_intents.items():
                if not isinstance(payload, dict):
                    unresolved.append(
                        {
                            "order_id": str(order_id),
                            "settlement_source": "SUBMITTED_ORDER_INTENT",
                            "needs_reconciliation": True,
                            "unknown_reason": "submitted intent ledger entry is not a JSON object",
                        }
                    )
                    continue
                status = payload.get("status")
                if (
                    status == "SUBMISSION_NOT_SEEN"
                    and payload.get("needs_reconciliation") is not True
                    and self._submission_not_seen_evidence_is_valid(payload)
                ):
                    continue
                if (
                    status in TERMINAL_NO_FILL_INTENT_STATUSES
                    and payload.get("needs_reconciliation") is not True
                    and self._terminal_no_fill_evidence_is_valid(payload)
                ):
                    continue
                unresolved.append(
                    {
                        "order_id": str(order_id),
                        "settlement_source": "SUBMITTED_ORDER_INTENT",
                        "needs_reconciliation": True,
                        "unknown_reason": payload.get("intent_reason") or "submitted intent unresolved",
                    }
                )
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

    def _submission_not_seen_evidence_is_valid(self, payload: Dict[str, Any]) -> bool:
        if payload.get("submission_not_seen_reason") in (None, ""):
            return False
        return self._parse_utc_datetime(payload.get("submission_not_seen_at")) is not None

    def _terminal_no_fill_evidence_is_valid(self, payload: Dict[str, Any]) -> bool:
        def _captured_fill_fields_are_zero(fields: Dict[str, Any]) -> bool:
            for key in ("last_qty", "filled_qty", "filled"):
                if key not in fields:
                    continue
                try:
                    parsed = Decimal(str(fields[key]))
                except Exception:
                    return False
                if not parsed.is_finite() or parsed != 0:
                    return False
            for key in ("last_px", "avg_px"):
                value = fields.get(key)
                if value in (None, ""):
                    continue
                try:
                    parsed = Decimal(str(value))
                except Exception:
                    return False
                if not parsed.is_finite() or parsed != 0:
                    return False
            return all(
                fields.get(key) in (None, "")
                for key in ACTUAL_FILL_UNIQUE_KEY_FIELDS
            )

        terminal_event = payload.get("terminal_no_fill_event")
        if not isinstance(terminal_event, dict):
            return False
        raw_event = terminal_event.get("raw_event")
        if not isinstance(raw_event, dict) or not raw_event:
            return False
        if raw_event.get("event_type") in (None, ""):
            return False
        raw_fields = raw_event.get("fields")
        if not isinstance(raw_fields, dict):
            return False
        if not _captured_fill_fields_are_zero(raw_fields):
            return False
        top_level_fields = {
            key: terminal_event[key]
            for key in (
                "last_qty",
                "filled_qty",
                "filled",
                "last_px",
                "avg_px",
                *ACTUAL_FILL_UNIQUE_KEY_FIELDS,
            )
            if key in terminal_event
        }
        if not _captured_fill_fields_are_zero(top_level_fields):
            return False
        instance_attrs = raw_event.get("instance_attrs")
        if instance_attrs is not None:
            if not isinstance(instance_attrs, dict):
                return False
            if not _captured_fill_fields_are_zero(instance_attrs):
                return False
        evidence = payload.get("terminal_no_fill_zero_quantity_evidence")
        if not isinstance(evidence, dict) or not evidence:
            return False
        allowed_keys = {"last_qty", "filled_qty", "filled"}
        if any(key not in allowed_keys for key in evidence):
            return False
        for value in evidence.values():
            try:
                parsed = Decimal(str(value))
            except Exception:
                return False
            if not parsed.is_finite() or parsed != 0:
                return False
        for key, value in evidence.items():
            if key not in raw_fields:
                return False
            try:
                raw_parsed = Decimal(str(raw_fields[key]))
                evidence_parsed = Decimal(str(value))
            except Exception:
                return False
            if (
                not raw_parsed.is_finite()
                or not evidence_parsed.is_finite()
                or raw_parsed != evidence_parsed
            ):
                return False
        return True

    def _actual_fill_reconciliation_order_id(self, client_order_id, payload: Dict[str, Any]) -> Optional[str]:
        """Return the real client order id for an actual-fill payload, if present."""
        if client_order_id not in (None, ""):
            normalized = str(client_order_id)
            normalized_lower = normalized.lower()
            venue_order_id = payload.get("venue_order_id")
            inferred_venue_order_id = None
            if normalized_lower.startswith("venue:"):
                inferred_venue_order_id = normalized.split(":", 1)[1]
            elif normalized_lower.startswith("0x"):
                inferred_venue_order_id = normalized
            if normalized_lower.startswith("venue:") or normalized_lower.startswith("0x") or (
                venue_order_id not in (None, "") and normalized.lower() == str(venue_order_id).lower()
            ):
                if (
                    inferred_venue_order_id not in (None, "")
                    and venue_order_id not in (None, "")
                    and str(venue_order_id).lower() != str(inferred_venue_order_id).lower()
                ):
                    reason = (
                        f"venue-like client_order_id={normalized!r} conflicts with "
                        f"payload venue_order_id={venue_order_id!r}"
                    )
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
                reason = f"venue-like client_order_id={normalized!r} is not a valid client order selector"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            return normalized
        venue_order_id = payload.get("venue_order_id")
        if venue_order_id in (None, ""):
            logger.warning(
                "actual-fill callback has neither client_order_id nor venue_order_id; "
                "rejecting unselectable evidence: %r",
                payload,
            )
        return None

    def _validated_effective_venue_order_id(
        self,
        order_id: Optional[str],
        payload_venue_order_id,
        source_meta: Dict[str, Any],
        ignore_pending_order_id: Optional[str] = None,
    ) -> Optional[str]:
        payload_venue = None if payload_venue_order_id in (None, "") else str(payload_venue_order_id)
        source_venue = None if source_meta.get("venue_order_id") in (None, "") else str(source_meta.get("venue_order_id"))
        if payload_venue is not None and source_venue is not None and payload_venue.lower() != source_venue.lower():
            reason = (
                f"venue_order_id mismatch for {order_id}: payload={payload_venue!r} "
                f"tracked={source_venue!r}"
            )
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        effective_venue = payload_venue or source_venue
        if effective_venue is None:
            return None

        normalized_venue = effective_venue.lower()
        conflicting_open = [
            str(open_order_id)
            for open_order_id, open_trade in self._open_live_trades.items()
            if str(open_order_id) != str(order_id)
            and isinstance(open_trade, dict)
            and str(open_trade.get("venue_order_id") or "").lower() == normalized_venue
        ]
        if conflicting_open:
            reason = (
                f"venue_order_id={effective_venue} already belongs to open trade(s): "
                + ", ".join(conflicting_open)
            )
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)

        if any(
            str(trade.get("venue_order_id") or "").lower() == normalized_venue
            for trade in self._settled_live_trades
        ):
            reason = f"SETTLEMENT_UNKNOWN/order record already exists for venue_order_id={effective_venue}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)

        if any(
            str(pending_order_id) != str(ignore_pending_order_id)
            and isinstance(pending, dict)
            and str(pending.get("venue_order_id") or "").lower() == normalized_venue
            for pending_order_id, pending in self._pending_actual_fills.items()
        ):
            reason = f"pending actual-fill record already exists for venue_order_id={effective_venue}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)

        return effective_venue

    def _create_durable_settlement_unknown_from_actual_fill(
        self,
        client_order_id,
        payload: Dict[str, Any],
        reason: str,
        ignore_pending_order_id: Optional[str] = None,
        skip_venue_validation: bool = False,
        canonical_pending_venue_order_id: Optional[str] = None,
    ) -> Optional[str]:
        """Create a durable SETTLEMENT_UNKNOWN from adapter-observed fill data."""
        with self._settlement_lock:
            payload = dict(payload or {})
            order_id = self._actual_fill_reconciliation_order_id(client_order_id, payload)
            venue_order_id = payload.get("venue_order_id")
            open_order_ids_for_venue = []
            if not skip_venue_validation and venue_order_id not in (None, ""):
                normalized_venue = str(venue_order_id).lower()
                open_order_ids_for_venue = [
                    str(open_order_id)
                    for open_order_id, open_trade in self._open_live_trades.items()
                    if isinstance(open_trade, dict)
                    and str(open_trade.get("venue_order_id") or "").lower() == normalized_venue
                ]
                if order_id is None and open_order_ids_for_venue:
                    if len(open_order_ids_for_venue) != 1:
                        reason_open = (
                            f"venue_order_id={venue_order_id} matches multiple open trades: "
                            + ", ".join(open_order_ids_for_venue)
                        )
                        self._block_live_settlement_ledger(reason_open)
                        raise SettlementLedgerError(reason_open)
                    order_id = open_order_ids_for_venue[0]
                elif order_id is not None:
                    conflicting_open = [
                        open_order_id
                        for open_order_id in open_order_ids_for_venue
                        if open_order_id != order_id
                    ]
                    if conflicting_open:
                        reason_open = (
                            f"venue_order_id={venue_order_id} already belongs to open trade(s): "
                            + ", ".join(conflicting_open)
                        )
                        self._block_live_settlement_ledger(reason_open)
                        raise SettlementLedgerError(reason_open)
            source_meta = {}
            source_open_trade = {}
            malformed_submitted_order_intent = None
            if order_id is not None:
                raw_open_trade = self._open_live_trades.get(order_id)
                source_open_trade = raw_open_trade if isinstance(raw_open_trade, dict) else {}
                submitted_intent_meta = self._submitted_order_intents.get(order_id)
                if submitted_intent_meta is not None and not isinstance(submitted_intent_meta, dict):
                    malformed_submitted_order_intent = copy.deepcopy(submitted_intent_meta)
                    submitted_intent_meta = {}
                source_meta = (
                    self._submitted_positions.get(order_id)
                    or source_open_trade
                    or submitted_intent_meta
                    or {}
                )
                if not isinstance(source_meta, dict):
                    source_meta = {}
            if canonical_pending_venue_order_id not in (None, ""):
                effective_venue_order_id = str(canonical_pending_venue_order_id)
            elif skip_venue_validation:
                effective_venue_order_id = None
            else:
                effective_venue_order_id = self._validated_effective_venue_order_id(
                    order_id,
                    venue_order_id,
                    source_meta,
                    ignore_pending_order_id=ignore_pending_order_id,
                )
            if order_id is None and effective_venue_order_id in (None, ""):
                reason_no_selector = (
                    "actual-fill callback has neither usable client_order_id nor venue_order_id; "
                    "cannot create selectable SETTLEMENT_UNKNOWN"
                )
                self._block_live_settlement_ledger(reason_no_selector)
                raise SettlementLedgerError(reason_no_selector)
            duplicate = False
            if order_id is not None:
                duplicate = any(
                    str(trade.get("order_id") or "") == order_id
                    for trade in self._settled_live_trades
                )
            if duplicate:
                duplicate_reason = f"SETTLEMENT_UNKNOWN/order record already exists for {order_id}"
                self._block_live_settlement_ledger(duplicate_reason)
                raise SettlementLedgerError(duplicate_reason)
            submitted_size = payload.get("submitted_size")
            if submitted_size is None:
                submitted_size = source_meta.get("submitted_size")

            allow_verified_accounting = payload.get("requires_external_fill_repair") is not True
            actual_filled_qty = None
            actual_fill_vwap = None
            if allow_verified_accounting:
                try:
                    actual_filled_qty = Decimal(str(payload["filled_qty"]))
                    actual_fill_vwap = Decimal(str(payload["vwap"]))
                except Exception:
                    actual_filled_qty = None
                    actual_fill_vwap = None
            has_positive_actual_fill = (
                actual_filled_qty is not None
                and actual_fill_vwap is not None
                and actual_filled_qty.is_finite()
                and actual_fill_vwap.is_finite()
                and actual_filled_qty > 0
                and actual_fill_vwap > 0
                and actual_fill_vwap <= Decimal("1")
            )
            actual_fill_notional = (
                actual_filled_qty * actual_fill_vwap
                if has_positive_actual_fill
                else None
            )

            def _positive_decimal_from_meta(value):
                if value in (None, ""):
                    return None
                try:
                    parsed = Decimal(str(value))
                except Exception:
                    return None
                if not parsed.is_finite() or parsed <= 0:
                    return None
                return parsed

            open_filled_qty = _positive_decimal_from_meta(source_open_trade.get("filled_qty"))
            open_entry_price = _positive_decimal_from_meta(source_open_trade.get("entry_price"))
            open_filled_notional = _positive_decimal_from_meta(source_open_trade.get("filled_notional"))
            open_accounting_size = _positive_decimal_from_meta(source_open_trade.get("size"))
            has_positive_open_fill = (
                allow_verified_accounting
                and open_filled_qty is not None
                and open_entry_price is not None
                and open_filled_notional is not None
                and open_accounting_size is not None
                and open_entry_price <= 1
                and abs(open_filled_notional - open_accounting_size)
                <= SETTLEMENT_ACCOUNTING_COST_TOLERANCE
                and self._settlement_accounting_cost_is_consistent(
                    open_accounting_size,
                    open_filled_qty,
                    open_entry_price,
                )
            )
            accounting_size = actual_fill_notional if has_positive_actual_fill else (
                open_accounting_size if has_positive_open_fill else None
            )

            record = {
                "settlement_source": "SETTLEMENT_UNKNOWN",
                "needs_reconciliation": True,
                "payout": "UNKNOWN",
                "pnl": "UNKNOWN",
                "order_id": order_id,
                "client_order_id": str(client_order_id) if client_order_id not in (None, "") else None,
                "venue_order_id": effective_venue_order_id,
                "condition_id": payload.get("condition_id", source_meta.get("condition_id")),
                "token_id": payload.get("token_id", source_meta.get("token_id")),
                "slug": payload.get("slug", source_meta.get("slug")),
                "instrument_id": payload.get("instrument_id", source_meta.get("instrument_id")),
                "direction": payload.get("direction", source_meta.get("direction")),
                "trade_label": payload.get("trade_label", source_meta.get("trade_label")),
                "submitted_at": payload.get("submitted_at", source_meta.get("submitted_at")),
                "unknown_reason": reason,
                "raw_callback_payload": payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if accounting_size is not None:
                record["size"] = accounting_size
            if skip_venue_validation:
                record["venue_conflict_payload_venue_order_id"] = venue_order_id
                tracked_venue_order_id = payload.get("tracked_venue_order_id")
                if tracked_venue_order_id in (None, ""):
                    tracked_venue_order_id = source_meta.get("venue_order_id")
                record["venue_conflict_tracked_venue_order_id"] = tracked_venue_order_id
            if submitted_size not in (None, ""):
                record["submitted_size"] = submitted_size
            if has_positive_actual_fill:
                record["filled_qty"] = str(actual_filled_qty)
                record["entry_price"] = str(actual_fill_vwap)
                record["filled_notional"] = str(actual_fill_notional)
            elif has_positive_open_fill:
                record["filled_qty"] = str(open_filled_qty)
                record["entry_price"] = str(open_entry_price)
                record["filled_notional"] = str(open_filled_notional)
            if order_id is not None and order_id in self._submitted_order_intents:
                submitted_intent = self._submitted_order_intents[order_id]
                if isinstance(submitted_intent, dict):
                    record["submitted_order_intent"] = copy.deepcopy(submitted_intent)
                else:
                    record["submitted_order_intent_malformed"] = True
                    record["submitted_order_intent_raw"] = copy.deepcopy(malformed_submitted_order_intent)
            settled_trades = list(self._settled_live_trades)
            settled_trades.append(record)
            open_trades = dict(self._open_live_trades)
            pending_actual_fills = dict(self._pending_actual_fills)
            submitted_order_intents = dict(self._submitted_order_intents)
            if order_id is not None:
                open_trades.pop(order_id, None)
                pending_actual_fills.pop(order_id, None)
                submitted_order_intents.pop(order_id, None)
            saved_state = self._save_live_trade_ledger_state(
                open_trades=open_trades,
                settled_trades=settled_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
                pending_actual_fills=pending_actual_fills,
                submitted_order_intents=submitted_order_intents,
            )
            self._apply_saved_live_trade_ledger_state(saved_state)
            return order_id

    def _current_market_metadata(self) -> Dict[str, Any]:
        """Return the current market metadata, if loaded."""
        if 0 <= self.current_instrument_index < len(self.all_btc_instruments):
            return self.all_btc_instruments[self.current_instrument_index]
        return {}

    def _require_current_market_metadata(self, context: str) -> Dict[str, Any]:
        """Return current market metadata or fail closed before order decisions."""
        metadata = self._current_market_metadata()
        if not metadata:
            raise RuntimeError(f"{context}: current market metadata is unavailable")
        return metadata

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
            if value.tzinfo is None or value.utcoffset() is None:
                return None
            return value.astimezone(timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            return parsed.astimezone(timezone.utc)
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
        raw_units = meta.get("filled_qty")
        if raw_units in (None, ""):
            return Decimal("0")
        try:
            units = Decimal(str(raw_units))
        except Exception:
            units = Decimal("0")
        if not units.is_finite():
            return Decimal("0")
        return max(units, Decimal("0"))

    def _settlement_accounting_cost_is_consistent(
        self,
        size: Decimal,
        filled_qty: Decimal,
        entry_price: Decimal,
    ) -> bool:
        """Validate stored cost basis against explicit fill units and entry price."""
        expected_size = filled_qty * entry_price
        return abs(size - expected_size) <= SETTLEMENT_ACCOUNTING_COST_TOLERANCE

    def _settlement_accounting_values(
        self,
        order_id: str,
        meta: Dict[str, Any],
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return verified settlement cost, units, and entry price or fail closed."""
        try:
            size = Decimal(str(meta["size"]))
        except Exception as exc:
            raise SettlementLedgerError(
                f"{order_id} missing verified settlement size/cost basis"
            ) from exc
        if not size.is_finite() or size <= 0:
            raise SettlementLedgerError(
                f"{order_id} has invalid settlement size/cost basis: {meta.get('size')!r}"
            )

        try:
            filled_qty = Decimal(str(meta["filled_qty"]))
        except Exception as exc:
            raise SettlementLedgerError(
                f"{order_id} missing verified filled_qty"
            ) from exc
        if not filled_qty.is_finite() or filled_qty <= 0:
            raise SettlementLedgerError(
                f"{order_id} has invalid filled_qty: {meta.get('filled_qty')!r}"
            )

        raw_entry = meta.get("entry_price")
        if raw_entry in (None, ""):
            raise SettlementLedgerError(
                f"{order_id} missing verified entry_price"
            )
        try:
            entry_price = Decimal(str(raw_entry))
        except Exception as exc:
            raise SettlementLedgerError(
                f"{order_id} has invalid entry_price: {raw_entry!r}"
            ) from exc
        if not entry_price.is_finite() or entry_price <= 0 or entry_price > 1:
            raise SettlementLedgerError(
                f"{order_id} has invalid entry_price: {raw_entry!r}"
            )
        if not self._settlement_accounting_cost_is_consistent(size, filled_qty, entry_price):
            expected_size = filled_qty * entry_price
            raise SettlementLedgerError(
                f"{order_id} has inconsistent settlement accounting: "
                f"size={size} filled_qty={filled_qty} entry_price={entry_price} "
                f"expected_size={expected_size}"
            )
        raw_filled_notional = meta.get("filled_notional")
        if raw_filled_notional in (None, ""):
            raise SettlementLedgerError(
                f"{order_id} missing verified filled_notional"
            )
        try:
            filled_notional = Decimal(str(raw_filled_notional))
        except Exception as exc:
            raise SettlementLedgerError(
                f"{order_id} has invalid filled_notional: {raw_filled_notional!r}"
            ) from exc
        if (
            not filled_notional.is_finite()
            or filled_notional <= 0
            or abs(filled_notional - size) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE
        ):
            raise SettlementLedgerError(
                f"{order_id} has inconsistent filled_notional: {raw_filled_notional!r}"
            )
        return size, filled_qty, entry_price

    def _settlement_entry_time(self, order_id: str, meta: Dict[str, Any]) -> datetime:
        """Return the verified entry timestamp for settlement accounting."""
        for field in ("filled_at", "submitted_at"):
            parsed = self._parse_utc_datetime(meta.get(field))
            if parsed is not None:
                return parsed
        raise SettlementLedgerError(
            f"{order_id} missing verified filled_at/submitted_at for settlement accounting"
        )

    def _settlement_trade_direction(self, order_id: str, meta: Dict[str, Any]) -> str:
        """Return the verified live-trade direction for settlement accounting."""
        direction = str(meta.get("direction") or "").lower()
        if direction not in {"long", "short"}:
            raise SettlementLedgerError(
                f"{order_id} has invalid direction for settlement accounting: {meta.get('direction')!r}"
            )
        return direction

    def _settlement_accounting_gap_reason(
        self,
        matches: List[tuple[str, Dict[str, Any]]],
    ) -> Optional[str]:
        """Describe missing accounting that prevents settlement P&L from being booked."""
        gaps = []
        for order_id, meta in matches:
            try:
                self._settlement_accounting_values(order_id, meta)
                self._settlement_entry_time(order_id, meta)
                self._settlement_trade_direction(order_id, meta)
            except SettlementLedgerError as exc:
                gaps.append(str(exc))
        if not gaps:
            return None
        return "auto_redeem matched trade(s) with missing/invalid settlement accounting: " + "; ".join(gaps)

    def _extract_token_id_from_instrument_id(self, instrument_id) -> str:
        """Extract the CLOB token id from a Nautilus Polymarket instrument id."""
        raw_id = str(instrument_id)
        without_suffix = raw_id.split('.')[0] if '.' in raw_id else raw_id
        return without_suffix.split('-')[-1] if '-' in without_suffix else without_suffix

    def _extract_polymarket_instrument_identity(
        self,
        instrument_id,
        context: str,
        *,
        block_settlement_ledger: bool = False,
    ) -> Dict[str, str]:
        raw_id = str(instrument_id)
        suffix = ".POLYMARKET"
        if not raw_id.endswith(suffix):
            reason = f"{context}: malformed Polymarket instrument_id {raw_id!r}"
            if block_settlement_ledger:
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            raise RuntimeError(reason)
        condition_id, separator, token_id = raw_id[: -len(suffix)].rpartition("-")
        if separator == "" or condition_id == "" or token_id == "":
            reason = f"{context}: malformed Polymarket instrument_id {raw_id!r}"
            if block_settlement_ledger:
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            raise RuntimeError(reason)
        return {
            "instrument_id": raw_id,
            "condition_id": condition_id,
            "token_id": token_id,
        }

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

    def _auto_redeem_payload_fingerprint(self, payload: Dict[str, Any]) -> str:
        fingerprint_payload = dict(payload)
        if "amount" in fingerprint_payload:
            fingerprint_payload["amount"] = self._normalized_auto_redeem_amount_key(fingerprint_payload.get("amount"))
        payload_json = json.dumps(
            self._jsonable(fingerprint_payload),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

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
        key_parts = [tx_key, market_key, token_key, amount_key]
        key_parts.append(f"payload:{self._auto_redeem_payload_fingerprint(payload)}")
        return "|".join(key_parts)

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
            "pending_actual_fills": copy.deepcopy(self._pending_actual_fills),
            "submitted_order_intents": copy.deepcopy(self._submitted_order_intents),
        }

    def _restore_settlement_state(self, snapshot: Dict[str, Any]) -> None:
        """Restore settlement state after a failed transactional ledger save."""
        self._open_live_trades = copy.deepcopy(snapshot["open"])
        self._settled_live_trades = copy.deepcopy(snapshot["settled"])
        self._seen_auto_redeem_events = set(snapshot["seen"])
        self._seen_auto_redeem_event_order = list(snapshot["seen_order"])
        self._pending_auto_redeem_events = copy.deepcopy(snapshot["pending"])
        self._pending_actual_fills = copy.deepcopy(snapshot["pending_actual_fills"])
        self._submitted_order_intents = copy.deepcopy(snapshot["submitted_order_intents"])

    def _prune_pending_auto_redeem_events(
        self,
        pending_events: Dict[str, Dict[str, Any]],
        now: datetime,
    ) -> int:
        """Preserve pending settlement events; they are durable audit records."""
        return 0

    def _prune_pending_auto_redeem_events_locked(self, now: datetime) -> int:
        """Preserve pending settlement events in current bot state."""
        return self._prune_pending_auto_redeem_events(self._pending_auto_redeem_events, now)

    def _pending_auto_redeem_payload_matches(
        self,
        existing_payload: Dict[str, Any],
        payload: Dict[str, Any],
        reason: str,
    ) -> bool:
        existing_core = dict(existing_payload)
        existing_reason = existing_core.pop("_pending_reason", None)
        existing_core.pop("_pending_since", None)
        return (
            existing_reason == reason
            and self._jsonable(existing_core) == self._jsonable(dict(payload))
        )

    def _pending_auto_redeem_collision_key(
        self,
        event_key: str,
        payload: Dict[str, Any],
        reason: str,
    ) -> str:
        fingerprint_payload = {
            "payload": self._jsonable(dict(payload)),
            "reason": reason,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"{event_key}|collision:{fingerprint}"

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
        pending_events = dict(self._pending_auto_redeem_events)
        storage_key = event_key
        existing_payload = pending_events.get(storage_key)
        if existing_payload is not None:
            if self._pending_auto_redeem_payload_matches(existing_payload, payload, reason):
                logger.debug(f"Pending auto_redeem event {event_key} is already durably queued")
                return
            storage_key = self._pending_auto_redeem_collision_key(event_key, payload, reason)
            existing_collision = pending_events.get(storage_key)
            if existing_collision is not None:
                if self._pending_auto_redeem_payload_matches(existing_collision, payload, reason):
                    logger.debug(f"Pending auto_redeem collision event {storage_key} is already durably queued")
                    return
                raise SettlementLedgerError(f"pending auto_redeem collision key already exists: {storage_key}")
            logger.warning(
                f"Preserving colliding pending auto_redeem event under {storage_key}; "
                f"base_key={event_key}"
            )
        pending_events[storage_key] = pending_payload
        saved_state = self._try_save_live_trade_ledger_state(
            "Failed to persist pending auto_redeem event",
            open_trades=self._open_live_trades,
            settled_trades=self._settled_live_trades,
            seen_events=self._seen_auto_redeem_events,
            seen_order=self._seen_auto_redeem_event_order,
            pending_events=pending_events,
            pending_actual_fills=self._pending_actual_fills,
            submitted_order_intents=self._submitted_order_intents,
        )
        if saved_state is None:
            raise SettlementLedgerError(
                f"failed to persist pending auto_redeem event {storage_key}; event not durably recorded"
            )
        self._apply_saved_live_trade_ledger_state(saved_state)
        logger.warning(
            "Stored auto_redeem for retry/reconciliation: "
            f"{reason} (slug={payload.get('slug')}, condition_id={payload.get('condition_id')}, "
            f"amount={payload.get('amount')})"
        )

    def _retry_pending_auto_redeems(self, reason: str) -> None:
        """Retry pending redeem events after fills or settlement-state changes."""
        with self._settlement_lock:
            pending_payloads = [
                (event_key, copy.deepcopy(payload))
                for event_key, payload in self._pending_auto_redeem_events.items()
            ]
        if pending_payloads:
            logger.info(f"Retrying {len(pending_payloads)} pending auto_redeem event(s): {reason}")
        for event_key, payload in pending_payloads:
            if not isinstance(payload, dict):
                block_reason = f"pending_auto_redeem_events[{event_key}] must be a JSON object"
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason)
            self._handle_auto_redeem_event(payload, store_pending=False, event_key_override=event_key)

    def _actual_fill_unique_key(self, payload: Dict[str, Any]) -> str:
        for field in ACTUAL_FILL_UNIQUE_KEY_FIELDS:
            value = payload.get(field)
            if value not in (None, ""):
                return f"{field}:{value}"
        raise SettlementLedgerError("actual-fill status=ok requires a real unique fill key")

    def _actual_fill_evidence_entry(
        self,
        fill_key: str,
        filled_qty: Decimal,
        vwap: Decimal,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "fill_key": fill_key,
            "filled_qty": str(filled_qty),
            "price": str(vwap),
            "notional": str(filled_qty * vwap),
            "raw_callback_payload": copy.deepcopy(payload),
            "received_at": datetime.now(timezone.utc).isoformat(),
        }

    def _aggregate_actual_fill_entries(self, order_id: str, fills: List[Dict[str, Any]]) -> tuple[Decimal, Decimal, Decimal]:
        aggregate = {
            "fills": copy.deepcopy(fills),
            "total_filled_qty": "0",
            "total_filled_notional": "0",
            "vwap": "1",
        }
        total_qty = Decimal("0")
        total_notional = Decimal("0")
        seen_fill_keys = set()
        for index, fill in enumerate(fills):
            if not isinstance(fill, dict):
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}].fills[{index}] must be a JSON object")
            fill_key = fill.get("fill_key")
            if fill_key in (None, ""):
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}].fills[{index}].fill_key is required")
            fill_key = str(fill_key)
            if fill_key in seen_fill_keys:
                raise SettlementLedgerError(f"pending_actual_fills[{order_id}] duplicate fill_key={fill_key}")
            seen_fill_keys.add(fill_key)
            try:
                fill_qty = Decimal(str(fill["filled_qty"]))
                fill_price = Decimal(str(fill["price"]))
                fill_notional = Decimal(str(fill["notional"]))
            except Exception as exc:
                raise SettlementLedgerError(
                    f"pending_actual_fills[{order_id}].fills[{index}] has invalid decimal accounting"
                ) from exc
            if (
                not fill_qty.is_finite()
                or not fill_price.is_finite()
                or not fill_notional.is_finite()
                or fill_qty <= 0
                or fill_price <= 0
                or fill_price > 1
                or fill_notional <= 0
            ):
                raise SettlementLedgerError(
                    f"pending_actual_fills[{order_id}].fills[{index}] has impossible accounting"
                )
            if abs((fill_qty * fill_price) - fill_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
                raise SettlementLedgerError(
                    f"pending_actual_fills[{order_id}].fills[{index}] notional is inconsistent"
                )
            total_qty += fill_qty
            total_notional += fill_notional
        if total_qty <= 0:
            raise SettlementLedgerError(f"pending_actual_fills[{order_id}] total_filled_qty must be positive")
        vwap = total_notional / total_qty
        aggregate["total_filled_qty"] = str(total_qty)
        aggregate["total_filled_notional"] = str(total_notional)
        aggregate["vwap"] = str(vwap)
        self._validate_pending_actual_fill_aggregate(order_id, aggregate)
        return total_qty, total_notional, vwap

    def _limit_ioc_fill_envelope_violation(
        self,
        meta: Dict[str, Any],
        filled_qty: Decimal,
        vwap: Decimal,
    ) -> Optional[str]:
        raw_order_type = meta.get("order_type")
        if raw_order_type in (None, ""):
            return "missing_order_type_for_fill_envelope"
        order_type = str(raw_order_type)
        if order_type not in _ALLOWED_ORDER_TYPES:
            return f"invalid_order_type_for_fill_envelope:{order_type!r}"
        if order_type != ORDER_TYPE_LIMIT_IOC:
            return None
        raw_limit_price = meta.get("submitted_limit_price")
        raw_submitted_qty = meta.get("estimated_tokens")
        missing = [
            key
            for key, value in (
                ("submitted_limit_price", raw_limit_price),
                ("estimated_tokens", raw_submitted_qty),
            )
            if value in (None, "")
        ]
        if missing:
            return "limit_ioc_missing_fill_envelope:" + ",".join(missing)
        try:
            submitted_limit_price = Decimal(str(raw_limit_price))
            submitted_qty = Decimal(str(raw_submitted_qty))
        except Exception as exc:
            return f"limit_ioc_invalid_fill_envelope_decimal:{type(exc).__name__}"
        if (
            not submitted_limit_price.is_finite()
            or not submitted_qty.is_finite()
            or submitted_limit_price <= 0
            or submitted_limit_price > 1
            or submitted_qty < POLYMARKET_LIMIT_MIN_TOKENS
        ):
            return "limit_ioc_impossible_fill_envelope"
        if vwap > submitted_limit_price:
            return (
                "limit_ioc_fill_price_above_submitted_limit:"
                f"vwap={vwap},submitted_limit_price={submitted_limit_price}"
            )
        if filled_qty > submitted_qty:
            return (
                "limit_ioc_fill_qty_above_submitted_quantity:"
                f"filled_qty={filled_qty},submitted_qty={submitted_qty}"
            )
        if filled_qty * vwap > submitted_qty * submitted_limit_price:
            return "limit_ioc_fill_notional_above_submitted_worst_case"
        return None

    def _mark_pending_actual_fill_external_repair(
        self,
        order_id,
        payload: Dict[str, Any],
        repair_reason: str,
    ) -> None:
        if order_id in (None, ""):
            return
        order_id = str(order_id)
        with self._settlement_lock:
            existing_pending = self._pending_actual_fills.get(order_id)
            if existing_pending is None:
                return
            if not isinstance(existing_pending, dict):
                block_reason = f"pending_actual_fills[{order_id}] must be a JSON object"
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason)
            if existing_pending.get("requires_external_fill_repair") is True:
                block_reason = f"pending actual fill for {order_id} already requires external repair"
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason)
            updated_pending = copy.deepcopy(existing_pending)
            repair_evidence = updated_pending.get("external_fill_repair_evidence", [])
            if not isinstance(repair_evidence, list):
                block_reason = (
                    f"pending_actual_fills[{order_id}].external_fill_repair_evidence "
                    "must be a JSON list"
                )
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason)
            repair_evidence.append(
                {
                    "reason": repair_reason,
                    "raw_callback_payload": copy.deepcopy(payload),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            updated_pending["requires_external_fill_repair"] = True
            updated_pending["external_fill_repair_reason"] = repair_reason
            updated_pending["external_fill_repair_evidence"] = repair_evidence
            pending_actual_fills = dict(self._pending_actual_fills)
            pending_actual_fills[order_id] = updated_pending
            saved_state = self._save_live_trade_ledger_state(
                open_trades=self._open_live_trades,
                settled_trades=self._settled_live_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
                pending_actual_fills=pending_actual_fills,
                submitted_order_intents=self._submitted_order_intents,
            )
            self._apply_saved_live_trade_ledger_state(saved_state)
            block_reason = (
                f"actual-fill callback for {order_id} requires external repair: "
                f"{repair_reason}"
            )
            self._block_live_settlement_ledger(block_reason)
            raise SettlementLedgerError(block_reason)

    def _mark_pending_actual_fill_external_repair_by_venue(
        self,
        venue_order_id,
        payload: Dict[str, Any],
        repair_reason: str,
    ) -> None:
        if venue_order_id in (None, ""):
            return
        normalized_venue = str(venue_order_id).lower()
        with self._settlement_lock:
            pending_matches = [
                str(pending_order_id)
                for pending_order_id, pending in self._pending_actual_fills.items()
                if isinstance(pending, dict)
                and str(pending.get("venue_order_id") or "").lower() == normalized_venue
            ]
        if len(pending_matches) > 1:
            block_reason = (
                f"venue_order_id={venue_order_id} matches multiple pending actual fills: "
                + ", ".join(pending_matches)
            )
            self._block_live_settlement_ledger(block_reason)
            raise SettlementLedgerError(block_reason)
        if len(pending_matches) == 1:
            self._mark_pending_actual_fill_external_repair(
                pending_matches[0],
                payload,
                repair_reason,
            )

    def _handle_actual_fill(self, client_order_id: str, payload: Dict[str, Any]) -> None:
        """Handle adapter-observed actual fill details before Nautilus fill delivery."""
        payload = dict(payload or {})
        status = payload.get("status")
        if status == "ok":
            required = [key for key in ("filled_qty", "vwap") if payload.get(key) in (None, "")]
            if required:
                reason = "actual_fill_ok_missing_required_fields:" + ",".join(required)
                repair_payload = dict(payload)
                repair_payload["requires_external_fill_repair"] = True
                repair_payload["external_fill_repair_reason"] = reason
                self._mark_pending_actual_fill_external_repair(client_order_id, repair_payload, reason)
                if client_order_id in (None, ""):
                    self._mark_pending_actual_fill_external_repair_by_venue(
                        repair_payload.get("venue_order_id"),
                        repair_payload,
                        reason,
                    )
                self._create_durable_settlement_unknown_from_actual_fill(client_order_id, repair_payload, reason)
                self._block_live_settlement_ledger(
                    f"actual-fill callback for {client_order_id} missing {required}; "
                    "SETTLEMENT_UNKNOWN created"
                )
                return

            if client_order_id in (None, ""):
                reason = "actual_fill_ok_missing_client_order_id"
                repair_payload = dict(payload)
                repair_payload["requires_external_fill_repair"] = True
                repair_payload["external_fill_repair_reason"] = reason
                self._mark_pending_actual_fill_external_repair_by_venue(
                    repair_payload.get("venue_order_id"),
                    repair_payload,
                    reason,
                )
                self._create_durable_settlement_unknown_from_actual_fill(client_order_id, repair_payload, reason)
                self._block_live_settlement_ledger(
                    "actual-fill callback status=ok has no client_order_id; "
                    "SETTLEMENT_UNKNOWN created for external repair"
                )
                return

            try:
                actual_filled_qty = Decimal(str(payload["filled_qty"]))
                actual_vwap = Decimal(str(payload["vwap"]))
            except Exception as exc:
                reason = f"actual_fill_ok_invalid_decimal:{type(exc).__name__}"
                repair_payload = dict(payload)
                repair_payload["requires_external_fill_repair"] = True
                repair_payload["external_fill_repair_reason"] = reason
                self._mark_pending_actual_fill_external_repair(client_order_id, repair_payload, reason)
                self._create_durable_settlement_unknown_from_actual_fill(client_order_id, repair_payload, reason)
                self._block_live_settlement_ledger(
                    f"actual-fill callback for {client_order_id} has invalid decimal fields; "
                    "SETTLEMENT_UNKNOWN created"
                )
                return
            if not actual_filled_qty.is_finite() or not actual_vwap.is_finite():
                reason = "actual_fill_ok_non_finite_qty_or_vwap"
                repair_payload = dict(payload)
                repair_payload["requires_external_fill_repair"] = True
                repair_payload["external_fill_repair_reason"] = reason
                self._mark_pending_actual_fill_external_repair(client_order_id, repair_payload, reason)
                self._create_durable_settlement_unknown_from_actual_fill(client_order_id, repair_payload, reason)
                self._block_live_settlement_ledger(
                    f"actual-fill callback for {client_order_id} has non-finite "
                    f"filled_qty={actual_filled_qty} vwap={actual_vwap}; SETTLEMENT_UNKNOWN created"
                )
                return
            if actual_filled_qty <= 0 or actual_vwap <= 0:
                reason = "actual_fill_ok_non_positive_qty_or_vwap"
                repair_payload = dict(payload)
                repair_payload["requires_external_fill_repair"] = True
                repair_payload["external_fill_repair_reason"] = reason
                self._mark_pending_actual_fill_external_repair(client_order_id, repair_payload, reason)
                self._create_durable_settlement_unknown_from_actual_fill(client_order_id, repair_payload, reason)
                self._block_live_settlement_ledger(
                    f"actual-fill callback for {client_order_id} has filled_qty={actual_filled_qty} "
                    f"vwap={actual_vwap}; SETTLEMENT_UNKNOWN created"
                )
                return
            if actual_vwap > 1:
                reason = "actual_fill_ok_vwap_above_one"
                repair_payload = dict(payload)
                repair_payload["requires_external_fill_repair"] = True
                repair_payload["external_fill_repair_reason"] = reason
                self._mark_pending_actual_fill_external_repair(client_order_id, repair_payload, reason)
                self._create_durable_settlement_unknown_from_actual_fill(client_order_id, repair_payload, reason)
                self._block_live_settlement_ledger(
                    f"actual-fill callback for {client_order_id} has vwap={actual_vwap}; "
                    "SETTLEMENT_UNKNOWN created"
                )
                return

            order_id = str(client_order_id)
            with self._settlement_lock:
                source_meta = self._submitted_positions.get(order_id) or self._open_live_trades.get(order_id)
                if source_meta is None:
                    self._mark_pending_actual_fill_external_repair(
                        order_id,
                        payload,
                        "actual_fill_ok_but_no_local_tracking_with_pending_actual_fill",
                    )
                    self._create_durable_settlement_unknown_from_actual_fill(
                        client_order_id=client_order_id,
                        payload=payload,
                        reason="actual_fill_ok_but_no_local_tracking",
                    )
                    self._block_live_settlement_ledger(
                        f"actual fill received for untracked {client_order_id}; "
                        "SETTLEMENT_UNKNOWN created for manual reconciliation"
                    )
                    return

                limit_violation = self._limit_ioc_fill_envelope_violation(
                    source_meta,
                    actual_filled_qty,
                    actual_vwap,
                )
                if limit_violation:
                    repair_payload = dict(payload)
                    repair_payload["requires_external_fill_repair"] = True
                    repair_payload["external_fill_repair_reason"] = limit_violation
                    self._mark_pending_actual_fill_external_repair(order_id, repair_payload, limit_violation)
                    self._create_durable_settlement_unknown_from_actual_fill(
                        client_order_id=client_order_id,
                        payload=repair_payload,
                        reason=limit_violation,
                    )
                    self._block_live_settlement_ledger(
                        f"actual-fill callback for {order_id} violates LIMIT_IOC envelope: "
                        f"{limit_violation}; SETTLEMENT_UNKNOWN created"
                    )
                    return

                pending_submitted_size = payload.get("submitted_size")
                if pending_submitted_size is None:
                    pending_submitted_size = source_meta.get("submitted_size")
                pending_actual_fills = dict(self._pending_actual_fills)
                try:
                    fill_key = self._actual_fill_unique_key(payload)
                except SettlementLedgerError as exc:
                    reason = "actual_fill_ok_missing_unique_fill_key"
                    repair_payload = dict(payload)
                    repair_payload["requires_external_fill_repair"] = True
                    repair_payload["external_fill_repair_reason"] = reason
                    if order_id in pending_actual_fills:
                        existing_pending = pending_actual_fills[order_id]
                        if not isinstance(existing_pending, dict):
                            block_reason = f"pending_actual_fills[{order_id}] must be a JSON object"
                            self._block_live_settlement_ledger(block_reason)
                            raise SettlementLedgerError(block_reason) from exc
                        if existing_pending.get("requires_external_fill_repair") is True:
                            block_reason = f"pending actual fill for {order_id} already requires external repair"
                            self._block_live_settlement_ledger(block_reason)
                            raise SettlementLedgerError(block_reason) from exc
                        updated_pending = copy.deepcopy(existing_pending)
                        repair_evidence = updated_pending.get("external_fill_repair_evidence", [])
                        if not isinstance(repair_evidence, list):
                            block_reason = (
                                f"pending_actual_fills[{order_id}].external_fill_repair_evidence "
                                "must be a JSON list"
                            )
                            self._block_live_settlement_ledger(block_reason)
                            raise SettlementLedgerError(block_reason) from exc
                        repair_evidence.append(
                            {
                                "reason": reason,
                                "raw_callback_payload": copy.deepcopy(payload),
                                "received_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        updated_pending["requires_external_fill_repair"] = True
                        updated_pending["external_fill_repair_reason"] = reason
                        updated_pending["external_fill_repair_evidence"] = repair_evidence
                        pending_actual_fills[order_id] = updated_pending
                        saved_state = self._save_live_trade_ledger_state(
                            open_trades=self._open_live_trades,
                            settled_trades=self._settled_live_trades,
                            seen_events=self._seen_auto_redeem_events,
                            seen_order=self._seen_auto_redeem_event_order,
                            pending_events=self._pending_auto_redeem_events,
                            pending_actual_fills=pending_actual_fills,
                            submitted_order_intents=self._submitted_order_intents,
                        )
                        self._apply_saved_live_trade_ledger_state(saved_state)
                        self._block_live_settlement_ledger(
                            f"actual-fill callback for {order_id} is missing a real unique fill key; "
                            "existing pending actual fill requires external repair"
                        )
                        raise SettlementLedgerError(str(exc)) from exc
                    self._create_durable_settlement_unknown_from_actual_fill(
                        client_order_id=client_order_id,
                        payload=repair_payload,
                        reason=reason,
                    )
                    self._block_live_settlement_ledger(
                        f"actual-fill callback for {order_id} is missing a real unique fill key; "
                        "SETTLEMENT_UNKNOWN created for external repair"
                    )
                    return
                fill_entry = self._actual_fill_evidence_entry(fill_key, actual_filled_qty, actual_vwap, payload)
                if order_id in pending_actual_fills:
                    reason = (
                        f"duplicate actual-fill callback for {order_id} while a prior "
                        "pending actual fill is still unconsumed"
                    )
                    existing_pending = pending_actual_fills[order_id]
                    if not isinstance(existing_pending, dict):
                        block_reason = f"pending_actual_fills[{order_id}] must be a JSON object"
                        self._block_live_settlement_ledger(block_reason)
                        raise SettlementLedgerError(block_reason)
                    if existing_pending.get("requires_external_fill_repair") is True:
                        block_reason = (
                            f"pending actual fill for {order_id} already requires external repair; "
                            "refusing to append additional fill evidence"
                        )
                        self._block_live_settlement_ledger(block_reason)
                        raise SettlementLedgerError(block_reason)
                    existing_fills = existing_pending.get("fills")
                    if not isinstance(existing_fills, list):
                        block_reason = (
                            f"pending_actual_fills[{order_id}].fills must be a non-empty JSON list"
                        )
                        self._block_live_settlement_ledger(block_reason)
                        raise SettlementLedgerError(block_reason)
                    existing_keys = {
                        str(fill.get("fill_key"))
                        for fill in existing_fills
                        if isinstance(fill, dict) and fill.get("fill_key") not in (None, "")
                    }
                    updated_pending = copy.deepcopy(existing_pending)
                    if fill_key in existing_keys:
                        repair_evidence = updated_pending.get("external_fill_repair_evidence", [])
                        if not isinstance(repair_evidence, list):
                            block_reason = (
                                f"pending_actual_fills[{order_id}].external_fill_repair_evidence "
                                "must be a JSON list"
                            )
                            self._block_live_settlement_ledger(block_reason)
                            raise SettlementLedgerError(block_reason)
                        repair_evidence.append(
                            {
                                "reason": "duplicate_actual_fill_key",
                                "fill_key": fill_key,
                                "raw_callback_payload": copy.deepcopy(payload),
                                "received_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        updated_pending["requires_external_fill_repair"] = True
                        updated_pending["external_fill_repair_reason"] = "duplicate_actual_fill_key"
                        updated_pending["external_fill_repair_evidence"] = repair_evidence
                        pending_actual_fills[order_id] = updated_pending
                        saved_state = self._save_live_trade_ledger_state(
                            open_trades=self._open_live_trades,
                            settled_trades=self._settled_live_trades,
                            seen_events=self._seen_auto_redeem_events,
                            seen_order=self._seen_auto_redeem_event_order,
                            pending_events=self._pending_auto_redeem_events,
                            pending_actual_fills=pending_actual_fills,
                            submitted_order_intents=self._submitted_order_intents,
                        )
                        self._apply_saved_live_trade_ledger_state(saved_state)
                        self._block_live_settlement_ledger(reason)
                        raise SettlementLedgerError(reason)
                    try:
                        effective_venue_order_id = self._validated_effective_venue_order_id(
                            order_id,
                            payload.get("venue_order_id"),
                            updated_pending,
                            ignore_pending_order_id=order_id,
                        )
                    except SettlementLedgerError as exc:
                        repair_evidence = updated_pending.get("external_fill_repair_evidence", [])
                        if not isinstance(repair_evidence, list):
                            block_reason = (
                                f"pending_actual_fills[{order_id}].external_fill_repair_evidence "
                                "must be a JSON list"
                            )
                            self._block_live_settlement_ledger(block_reason)
                            raise SettlementLedgerError(block_reason) from exc
                        repair_evidence.append(
                            {
                                "reason": "duplicate_actual_fill_venue_conflict",
                                "fill_key": fill_key,
                                "venue_conflict_reason": str(exc),
                                "raw_callback_payload": copy.deepcopy(payload),
                                "received_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        updated_pending["requires_external_fill_repair"] = True
                        updated_pending["external_fill_repair_reason"] = "duplicate_actual_fill_venue_conflict"
                        updated_pending["external_fill_repair_evidence"] = repair_evidence
                        pending_actual_fills[order_id] = updated_pending
                        saved_state = self._save_live_trade_ledger_state(
                            open_trades=self._open_live_trades,
                            settled_trades=self._settled_live_trades,
                            seen_events=self._seen_auto_redeem_events,
                            seen_order=self._seen_auto_redeem_event_order,
                            pending_events=self._pending_auto_redeem_events,
                            pending_actual_fills=pending_actual_fills,
                            submitted_order_intents=self._submitted_order_intents,
                        )
                        self._apply_saved_live_trade_ledger_state(saved_state)
                        self._block_live_settlement_ledger(str(exc))
                        raise SettlementLedgerError(str(exc)) from exc
                    updated_fills = copy.deepcopy(existing_fills)
                    updated_fills.append(fill_entry)
                    limit_violation = self._limit_ioc_fill_envelope_violation(
                        source_meta,
                        actual_filled_qty,
                        actual_vwap,
                    )
                    if limit_violation:
                        repair_payload = dict(payload)
                        repair_payload["requires_external_fill_repair"] = True
                        repair_payload["external_fill_repair_reason"] = limit_violation
                        self._mark_pending_actual_fill_external_repair(order_id, repair_payload, limit_violation)
                    total_qty, total_notional, aggregate_vwap = self._aggregate_actual_fill_entries(
                        order_id,
                        updated_fills,
                    )
                    limit_violation = self._limit_ioc_fill_envelope_violation(
                        source_meta,
                        total_qty,
                        aggregate_vwap,
                    )
                    if limit_violation:
                        repair_payload = dict(payload)
                        repair_payload["requires_external_fill_repair"] = True
                        repair_payload["external_fill_repair_reason"] = limit_violation
                        self._mark_pending_actual_fill_external_repair(order_id, repair_payload, limit_violation)
                    updated_pending["fills"] = updated_fills
                    updated_pending["total_filled_qty"] = str(total_qty)
                    updated_pending["total_filled_notional"] = str(total_notional)
                    updated_pending["vwap"] = str(aggregate_vwap)
                    if effective_venue_order_id not in (None, ""):
                        updated_pending["venue_order_id"] = effective_venue_order_id
                    updated_pending["last_received_at"] = datetime.now(timezone.utc).isoformat()
                    pending_actual_fills[order_id] = updated_pending
                    saved_state = self._save_live_trade_ledger_state(
                        open_trades=self._open_live_trades,
                        settled_trades=self._settled_live_trades,
                        seen_events=self._seen_auto_redeem_events,
                        seen_order=self._seen_auto_redeem_event_order,
                        pending_events=self._pending_auto_redeem_events,
                        pending_actual_fills=pending_actual_fills,
                        submitted_order_intents=self._submitted_order_intents,
                    )
                    self._apply_saved_live_trade_ledger_state(saved_state)
                    if order_id in self._submitted_positions:
                        self._submitted_positions[order_id]["_actual_filled_qty"] = total_qty
                        self._submitted_positions[order_id]["_actual_fill_vwap"] = aggregate_vwap
                    elif order_id in self._open_live_trades:
                        self._open_live_trades[order_id]["_actual_filled_qty"] = total_qty
                        self._open_live_trades[order_id]["_actual_fill_vwap"] = aggregate_vwap
                    else:
                        disappeared_reason = f"actual-fill local tracking disappeared after durable save for {order_id}"
                        self._block_live_settlement_ledger(disappeared_reason)
                        raise SettlementLedgerError(disappeared_reason)
                    return
                try:
                    effective_venue_order_id = self._validated_effective_venue_order_id(
                        order_id,
                        payload.get("venue_order_id"),
                        source_meta,
                        ignore_pending_order_id=order_id,
                    )
                except SettlementLedgerError as exc:
                    conflict_payload = dict(payload)
                    conflict_payload["venue_conflict_reason"] = str(exc)
                    self._create_durable_settlement_unknown_from_actual_fill(
                        client_order_id=client_order_id,
                        payload=conflict_payload,
                        reason="actual_fill_ok_venue_conflict",
                        ignore_pending_order_id=order_id,
                        skip_venue_validation=True,
                    )
                    reason = (
                        f"actual-fill callback for {client_order_id} has venue conflict: {exc}; "
                        "SETTLEMENT_UNKNOWN created"
                    )
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason) from exc
                actual_fill_notional = actual_filled_qty * actual_vwap
                pending_entry = {
                    "fills": [fill_entry],
                    "total_filled_qty": str(actual_filled_qty),
                    "total_filled_notional": str(actual_fill_notional),
                    "vwap": str(actual_vwap),
                    "venue_order_id": effective_venue_order_id,
                    "condition_id": payload.get("condition_id", source_meta.get("condition_id")),
                    "token_id": payload.get("token_id", source_meta.get("token_id")),
                    "slug": payload.get("slug", source_meta.get("slug")),
                    "direction": payload.get("direction", source_meta.get("direction")),
                    "trade_label": payload.get("trade_label", source_meta.get("trade_label")),
                    "submitted_at": payload.get("submitted_at", source_meta.get("submitted_at")),
                    "raw_status_report": payload.get("raw_status_report"),
                    "raw_callback_payload": payload,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                if pending_submitted_size not in (None, ""):
                    pending_entry["submitted_size"] = pending_submitted_size
                pending_actual_fills[order_id] = pending_entry
                saved_state = self._save_live_trade_ledger_state(
                    open_trades=self._open_live_trades,
                    settled_trades=self._settled_live_trades,
                    seen_events=self._seen_auto_redeem_events,
                    seen_order=self._seen_auto_redeem_event_order,
                    pending_events=self._pending_auto_redeem_events,
                    pending_actual_fills=pending_actual_fills,
                    submitted_order_intents=self._submitted_order_intents,
                )
                self._apply_saved_live_trade_ledger_state(saved_state)
                if order_id in self._submitted_positions:
                    self._submitted_positions[order_id]["_actual_filled_qty"] = actual_filled_qty
                    self._submitted_positions[order_id]["_actual_fill_vwap"] = actual_vwap
                elif order_id in self._open_live_trades:
                    self._open_live_trades[order_id]["_actual_filled_qty"] = actual_filled_qty
                    self._open_live_trades[order_id]["_actual_fill_vwap"] = actual_vwap
                else:
                    reason = f"actual-fill local tracking disappeared after durable save for {order_id}"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
            return

        if status == "failed":
            reason = payload.get("reason")
            if not reason:
                malformed_reason = f"malformed actual-fill callback for {client_order_id}: missing reason"
                self._mark_pending_actual_fill_external_repair(client_order_id, payload, malformed_reason)
                if client_order_id in (None, ""):
                    self._mark_pending_actual_fill_external_repair_by_venue(
                        payload.get("venue_order_id"),
                        payload,
                        malformed_reason,
                    )
                self._block_live_settlement_ledger(malformed_reason)
                raise SettlementLedgerError(malformed_reason)
            reason = str(reason)
        else:
            reason = f"unknown_status:{status!r}"
        self._mark_pending_actual_fill_external_repair(client_order_id, payload, reason)
        if client_order_id in (None, ""):
            self._mark_pending_actual_fill_external_repair_by_venue(
                payload.get("venue_order_id"),
                payload,
                reason,
            )
        self._create_durable_settlement_unknown_from_actual_fill(client_order_id, payload, reason)
        self._block_live_settlement_ledger(
            f"actual-fill callback failed for {client_order_id}: {reason}; "
            "SETTLEMENT_UNKNOWN created"
        )

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
        """Allocate wallet-level auto_redeem payout after validating tracked bot tokens."""
        if not matches:
            return {}

        match_order_ids = [order_id for order_id, _meta in matches]
        if len(set(match_order_ids)) != len(match_order_ids):
            raise SettlementLedgerError("auto_redeem allocation has duplicate matched order ids")

        expected_by_order = {
            order_id: self._trade_payout_units(meta)
            for order_id, meta in matches
        }
        total_expected = sum(expected_by_order.values(), Decimal("0"))
        if total_expected <= 0:
            if payout > 0:
                raise SettlementLedgerError("cannot allocate positive auto_redeem payout without known token units")
            return {order_id: Decimal("0") for order_id, _ in matches}

        if payout > total_expected:
            raise SettlementLedgerError(
                "auto_redeem payout exceeds tracked bot tokens: "
                f"payout={payout} tracked_units={total_expected}"
            )

        allocations = {
            order_id: payout * expected_by_order[order_id] / total_expected
            for order_id, _ in matches
        }
        if set(allocations) != set(match_order_ids):
            raise SettlementLedgerError("auto_redeem allocation did not cover every matched order")
        return allocations

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
            size, filled_qty, entry_price = self._settlement_accounting_values(order_id, meta)
            exit_price = payout / filled_qty

            pnl = payout - size
            entry_time = self._settlement_entry_time(order_id, meta)

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
            if save:
                settled_trades = list(self._settled_live_trades)
                settled_trades.append(settled)
                open_trades = dict(self._open_live_trades)
                open_trades.pop(order_id, None)
                saved_state = self._try_save_live_trade_ledger_state(
                    "Failed to persist settled live trade",
                    open_trades=open_trades,
                    settled_trades=settled_trades,
                    seen_events=self._seen_auto_redeem_events,
                    seen_order=self._seen_auto_redeem_event_order,
                    pending_events=self._pending_auto_redeem_events,
                    pending_actual_fills=self._pending_actual_fills,
                    submitted_order_intents=self._submitted_order_intents,
                )
                if saved_state is None:
                    raise SettlementLedgerError(f"failed to persist settled live trade for {order_id}")
                self._apply_saved_live_trade_ledger_state(saved_state)
            else:
                self._settled_live_trades.append(settled)
                self._open_live_trades.pop(order_id, None)
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
            direction = self._settlement_trade_direction(order_id, meta)
            token_buy_direction = "buy_yes" if direction == "long" else "buy_no"
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
            reason = f"failed to record performance settlement accounting for {order_id}: {e}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason) from e

        try:
            risk_pnl = self.risk_engine.remove_position(order_id, exit_price)
            if risk_pnl is None:
                if source != "late_auto_redeem":
                    raise SettlementLedgerError(f"risk_engine.remove_position returned None for {order_id}")
                record_realized_pnl = getattr(self.risk_engine, "record_realized_pnl", None)
                if not callable(record_realized_pnl):
                    raise SettlementLedgerError(
                        f"risk_engine.record_realized_pnl unavailable for late settlement correction {order_id}"
                    )
                record_realized_pnl(
                    pnl,
                    source=f"polymarket_{source}",
                    metadata={"order_id": order_id, "slug": meta.get("slug")},
                )
        except Exception as e:
            reason = f"failed to record risk settlement accounting for {order_id}: {e}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason) from e

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
        size, filled_qty, entry_price = self._settlement_accounting_values(order_id, meta)
        exit_price = payout / filled_qty
        pnl = payout - size
        entry_time = self._settlement_entry_time(order_id, meta)
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

        size, filled_qty, entry_price = self._settlement_accounting_values(order_id, trade)
        exit_price = payout / filled_qty
        pnl = payout - size
        entry_time = self._settlement_entry_time(order_id, trade)

        corrected_trade = dict(trade)
        corrected_trade.update(
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
            settled_trades = []
            replaced = False
            for existing_trade in self._settled_live_trades:
                if existing_trade is trade and not replaced:
                    settled_trades.append(corrected_trade)
                    replaced = True
                else:
                    settled_trades.append(existing_trade)
            if not replaced:
                reason = f"late settlement correction target not found in settled ledger for {order_id}"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            saved_state = self._try_save_live_trade_ledger_state(
                "Failed to persist late settlement correction",
                open_trades=self._open_live_trades,
                settled_trades=settled_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
                pending_actual_fills=self._pending_actual_fills,
                submitted_order_intents=self._submitted_order_intents,
            )
            if saved_state is None:
                raise SettlementLedgerError(f"failed to persist unknown settlement for {order_id}")
            self._apply_saved_live_trade_ledger_state(saved_state)
        else:
            trade.update(corrected_trade)
        if record_accounting:
            self._record_settlement_accounting(
                order_id=order_id,
                meta=corrected_trade,
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
            release_position = self._risk_release_position_callable(
                order_id,
                "unknown-settlement risk release",
            )
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
            settled_trades = list(self._settled_live_trades)
            settled_trades.append(settled)
            open_trades = dict(self._open_live_trades)
            open_trades.pop(order_id, None)
            saved_state = self._try_save_live_trade_ledger_state(
                "Failed to persist unknown settlement",
                open_trades=open_trades,
                settled_trades=settled_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
                pending_actual_fills=self._pending_actual_fills,
                submitted_order_intents=self._submitted_order_intents,
            )
            if saved_state is None:
                raise SettlementLedgerError(f"failed to persist unknown settlement for {order_id}")
            self._apply_saved_live_trade_ledger_state(saved_state)
            try:
                release_position(order_id)
            except Exception as exc:
                block_reason = f"unknown-settlement risk release: risk position release failed for {order_id}: {exc}"
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason) from exc

            logger.warning(
                f"Settlement still unknown for {order_id}; released open exposure without "
                f"booking P&L ({reason}). A late auto_redeem can still correct this record."
            )
            self._retry_pending_auto_redeems("settlement marked unknown")

    def _handle_auto_redeem_event(
        self,
        payload: Dict[str, Any],
        store_pending: bool = True,
        event_key_override: Optional[str] = None,
    ) -> bool:
        """Settle matching live trades when Polymarket reports an auto-redeem payout."""
        with self._settlement_lock:
            event_key = event_key_override or self._auto_redeem_event_key(payload)
            if event_key in self._seen_auto_redeem_events:
                return False

            raw_amount = payload.get("amount")
            if raw_amount in (None, ""):
                logger.warning("auto_redeem missing amount; leaving event pending")
                if store_pending:
                    self._store_pending_auto_redeem_event(event_key, payload, "missing auto_redeem amount")
                return False
            try:
                payout = Decimal(str(raw_amount))
            except Exception:
                logger.warning(f"auto_redeem had invalid amount: {payload.get('amount')}")
                if store_pending:
                    self._store_pending_auto_redeem_event(event_key, payload, "invalid auto_redeem amount")
                return False
            if not payout.is_finite() or payout < 0:
                logger.warning(f"auto_redeem had impossible amount: {payload.get('amount')}")
                if store_pending:
                    self._store_pending_auto_redeem_event(event_key, payload, "invalid auto_redeem amount")
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

                accounting_gap_reason = self._settlement_accounting_gap_reason(matched_open)
                if accounting_gap_reason:
                    logger.warning(
                        f"{accounting_gap_reason}; leaving auto_redeem pending for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, accounting_gap_reason)
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
                try:
                    allocations = self._allocated_auto_redeem_payouts(payout, matched_open)
                except SettlementLedgerError as exc:
                    reason = str(exc)
                    logger.warning(
                        f"{reason}; leaving auto_redeem pending for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, reason)
                    return False
                snapshot = self._snapshot_settlement_state()
                accounting_records = []
                for order_id, meta in list(matched_open):
                    allocated_payout = allocations[order_id]
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
                    reason = "ledger save failed after auto_redeem settlement; auto_redeem not durably recorded"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
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
                if len(matched_unknown) != len(unknown_matches):
                    reason = "late auto_redeem matched SETTLEMENT_UNKNOWN record without order_id"
                    logger.warning(
                        f"{reason}; keeping redeem pending for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, reason)
                    return False
                unknown_units_reason = self._unknown_positive_auto_redeem_units_reason(payout, matched_unknown)
                if unknown_units_reason:
                    logger.warning(
                        f"{unknown_units_reason}; keeping SETTLEMENT_UNKNOWN record(s) for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, unknown_units_reason)
                    return False

                accounting_gap_reason = self._settlement_accounting_gap_reason(matched_unknown)
                if accounting_gap_reason:
                    logger.warning(
                        f"{accounting_gap_reason}; keeping SETTLEMENT_UNKNOWN record(s) for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, accounting_gap_reason)
                    return False

                try:
                    allocations = self._allocated_auto_redeem_payouts(payout, matched_unknown)
                except SettlementLedgerError as exc:
                    reason = str(exc)
                    logger.warning(
                        f"{reason}; keeping SETTLEMENT_UNKNOWN record(s) for manual review "
                        f"(slug={payload.get('slug')}, amount={payload.get('amount')})"
                    )
                    if store_pending:
                        self._store_pending_auto_redeem_event(event_key, payload, reason)
                    return False
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
                    allocated_payout = allocations[order_id]
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
                    reason = "ledger save failed after late auto_redeem correction; auto_redeem not durably recorded"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
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
        if not self._actual_fill_registered:
            register_actual_fill_handler(self._actual_fill_handler)
            self._actual_fill_registered = True
            logger.info("Registered Polymarket actual-fill handler")

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
                if self._stable_tick_count >= self._quote_stability_required:
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

        market_meta = self._require_current_market_metadata("market context fetch")
        yes_token_id = market_meta.get("yes_token_id")
        if yes_token_id in (None, ""):
            raise RuntimeError("market context fetch: current market metadata missing yes_token_id")
        if self._yes_token_id not in (None, "") and self._yes_token_id != yes_token_id:
            raise RuntimeError(
                "market context fetch: cached YES token_id does not match current market metadata "
                f"({self._yes_token_id!r} != {yes_token_id!r})"
            )
        no_token_id = market_meta.get("no_token_id")
        metadata = {
            "deviation": deviation,
            "momentum": momentum,
            "volatility": volatility,
            # Tick buffer for TickVelocityProcessor
            "tick_buffer": list(self._tick_buffer),
            # YES token id for OrderBookImbalanceProcessor
            "yes_token_id": yes_token_id,
        }
        if no_token_id not in (None, ""):
            metadata["no_token_id"] = no_token_id
        metadata["yes_order_book"] = await asyncio.to_thread(
            self.orderbook_processor.fetch_order_book,
            yes_token_id,
        )
        if no_token_id not in (None, ""):
            metadata["no_order_book"] = await asyncio.to_thread(
                self.orderbook_processor.fetch_order_book,
                no_token_id,
            )

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

        Phase 2.4 wiring: every early-return path records exactly one
        decisions.jsonl line via the `rec` DecisionRecord context manager.
        Reject branches call ``rec.reject(gate, reason)`` immediately before
        ``return False``; the positive path calls ``rec.decided(...)`` before
        delegating to the simulation/live executor.
        """
        # --- Mode check ---
        is_simulation = await self.check_simulation_mode()
        observation_mode = "simulation" if is_simulation else "live_gate"

        with DecisionRecord(
            current_price=current_price,
            strategy_observation_mode=observation_mode,
        ) as rec:
            return await self._make_trading_decision_body(
                current_price, trade_key, is_simulation, rec
            )

    async def _make_trading_decision_body(
        self,
        current_price: Decimal,
        trade_key,
        is_simulation: bool,
        rec: DecisionRecord,
    ) -> bool:
        logger.info(f"Mode: {'SIMULATION' if is_simulation else 'LIVE TRADING'}")
        order_config = validate_live_order_config()
        order_type = order_config["order_type"]
        quote_stability_required = order_config["quote_stability_required"]
        limit_ioc_fill_policy = order_config["limit_ioc_fill_policy"]
        if not is_simulation:
            unresolved = self._unresolved_settlement_unknowns()
            if unresolved:
                logger.error(
                    "LIVE TRADING PAUSED: unresolved settlement reconciliation exists "
                    f"({len(unresolved)} item(s)). Resolve or repair unresolved ledger state "
                    f"in {LIVE_TRADE_LEDGER_PATH.name} before placing new live orders."
                )
                rec.reject(
                    "live_paused_unresolved_settlement",
                    f"{len(unresolved)} unresolved item(s)",
                )
                return False
        if self._stable_tick_count < quote_stability_required:
            logger.error(
                "ORDER BLOCKED: quote stability below configured threshold "
                f"({self._stable_tick_count} < {quote_stability_required})"
            )
            rec.reject(
                "quote_stability_below_configured_threshold",
                f"stable_tick_count={self._stable_tick_count} < required={quote_stability_required}",
            )
            return False

        # --- Minimum history guard ---
        if len(self.price_history) < 20:
            logger.warning(f"Not enough price history ({len(self.price_history)}/20)")
            rec.reject(
                "history_too_short",
                f"len(price_history)={len(self.price_history)} < 20",
            )
            return False

        logger.info(f"Current price: ${float(current_price):,.4f}")

        # --- Phase 4a: Build real metadata for processors ---
        metadata = await self._fetch_market_context(current_price)
        market_meta = self._require_current_market_metadata("trading decision")
        rec.update(
            slug=market_meta.get("slug"),
            condition_id=market_meta.get("condition_id"),
            yes_token_id=market_meta.get("yes_token_id"),
            no_token_id=market_meta.get("no_token_id"),
            market_end_time=market_meta.get("end_time"),
        )

        # --- Phase 4.5 timing/price-band observability ---
        market_end_iso = market_meta.get("end_time")
        end_dt = self._parse_utc_datetime(market_end_iso) if market_end_iso else None
        if end_dt is not None:
            now_utc = datetime.now(timezone.utc)
            # The 15-min sub-interval started 900 seconds before the close.
            sub_interval_start = end_dt - timedelta(seconds=900)
            seconds_into = (now_utc - sub_interval_start).total_seconds()
            rec.update(
                seconds_into_sub_interval=seconds_into,
                trade_window_label=trade_window_label_for_seconds_into_sub_interval(
                    seconds_into
                ),
            )
        try:
            rec.update(trend_price_band=trend_price_band_for(float(current_price)))
        except (TypeError, ValueError):
            # current_price wasn't numeric — leave the band null; the
            # downstream trend filter will already short-circuit.
            pass

        # --- Phase 4b: Run all three signal processors ---
        signals = self._process_signals(current_price, metadata)

        if not signals:
            logger.info("No signals generated — no trade this interval")
            rec.reject("no_signals", "_process_signals returned empty list")
            return False

        logger.info(f"Generated {len(signals)} signal(s):")
        for sig in signals:
            logger.info(
                f"  [{sig.source}] {sig.direction.value}: "
                f"score={sig.score:.1f}, confidence={sig.confidence:.2%}"
            )
        rec.update(
            model_signals=[
                {
                    "source": str(getattr(sig, "source", "")),
                    "direction": str(getattr(sig.direction, "value", sig.direction)),
                    "score": float(getattr(sig, "score", 0.0)),
                    "confidence": float(getattr(sig, "confidence", 0.0)),
                }
                for sig in signals
            ]
        )

        # --- Phase 4c: Fuse signals into one consensus ---
        fused = self.fusion_engine.fuse_signals(signals, min_signals=2, min_score=55.0)
        if not fused:
            logger.info("Fusion produced no actionable signal — no trade this interval")
            rec.reject("fusion_no_consensus", "fusion_engine.fuse_signals returned None")
            return False

        logger.info(
            f"FUSED SIGNAL: {fused.direction.value} "
            f"(score={fused.score:.1f}, confidence={fused.confidence:.2%})"
        )
        rec.update(
            fused_confidence=float(fused.confidence),
            fused_direction=str(fused.direction.value),
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
            rec.reject(
                "trend_filter_neutral",
                f"price {price_float:.4f} in neutral band [0.40, 0.60]",
            )
            return False
        # ``trend_price_band`` was already populated with the granular
        # Phase 4.5 band above; the coarse trend-filter band is implicit in
        # the rejection gate name for neutral trades.

        if get_env_bool("REQUIRE_SIGNAL_CONFIRMATION", True):
            expected_signal = "bullish" if direction == "long" else "bearish"
            actual_signal = str(fused.direction.value).lower()
            min_confidence = get_min_signal_confidence()
            if actual_signal != expected_signal:
                logger.info(
                    f"SKIP: trend wants {direction.upper()} but fused signal is "
                    f"{actual_signal.upper()} — no independent confirmation"
                )
                rec.reject(
                    "signal_confirmation_mismatch",
                    f"trend={direction} but fused={actual_signal}",
                )
                return False
            if fused.confidence < min_confidence:
                logger.info(
                    f"SKIP: fused confidence {fused.confidence:.2%} below "
                    f"MIN_SIGNAL_CONFIDENCE={min_confidence:.2%}"
                )
                rec.reject(
                    "min_signal_confidence",
                    f"fused.confidence={fused.confidence:.4f} < {float(min_confidence):.4f}",
                )
                return False

        last_tick = getattr(self, "_last_bid_ask", None)
        if not last_tick:
            logger.warning("SKIP: no executable YES quote cached")
            rec.reject("no_yes_quote", "self._last_bid_ask is None")
            return False
        yes_bid, yes_ask = last_tick
        rec.update(yes_ask=yes_ask)
        if direction == "long":
            top_of_book_entry = yes_ask
            entry_source = "YES ask"
            side_token_id = market_meta.get("yes_token_id")
            selected_order_book_key = "yes_order_book"
        else:
            no_tick = getattr(self, "_last_no_bid_ask", None)
            if not no_tick:
                logger.warning("SKIP: no executable NO ask cached")
                rec.reject("no_no_quote", "self._last_no_bid_ask is None")
                return False
            _, no_ask = no_tick
            top_of_book_entry = no_ask
            entry_source = "NO ask"
            side_token_id = market_meta.get("no_token_id")
            selected_order_book_key = "no_order_book"
            rec.update(no_ask=no_ask)

        if selected_order_book_key not in metadata:
            logger.warning(f"SKIP: {entry_source} order-book snapshot missing from market context")
            rec.reject(
                "depth_aware_book_snapshot_missing",
                f"{selected_order_book_key} missing from market context",
            )
            return False
        selected_order_book = metadata[selected_order_book_key]
        accepted_limit_price = None
        submitted_limit_price = None
        limit_order_token_qty = None

        if order_type == ORDER_TYPE_LIMIT_IOC:
            accepted_limit_price = compute_limit_price(
                fused.confidence,
                order_config["limit_required_edge"],
            )
            if accepted_limit_price is None:
                logger.info(
                    "SKIP: LIMIT_IOC accepted cap is outside Polymarket token bounds"
                )
                rec.reject(
                    "limit_price_out_of_bounds",
                    f"fused.confidence={fused.confidence:.4f}",
                )
                return False
            if direction == "long":
                if not hasattr(self, "_yes_instrument_id") or self._yes_instrument_id is None:
                    logger.warning("SKIP: YES token instrument unavailable for LIMIT_IOC")
                    rec.reject("limit_ioc_no_yes_instrument", "_yes_instrument_id is None")
                    return False
                trade_instrument_id = self._yes_instrument_id
            else:
                if not hasattr(self, "_no_instrument_id") or self._no_instrument_id is None:
                    logger.warning("SKIP: NO token instrument unavailable for LIMIT_IOC")
                    rec.reject("limit_ioc_no_no_instrument", "_no_instrument_id is None")
                    return False
                trade_instrument_id = self._no_instrument_id
            instrument = self.cache.instrument(trade_instrument_id)
            if not instrument:
                logger.error(f"SKIP: instrument not in cache for LIMIT_IOC: {trade_instrument_id}")
                rec.reject(
                    "limit_ioc_instrument_not_cached",
                    f"instrument_id={trade_instrument_id}",
                )
                return False
            submitted_limit_price = derive_submitted_limit_price(
                accepted_limit_price,
                instrument.price_precision,
            )
            limit_order_token_qty = compute_limit_order_token_qty(
                POSITION_SIZE_USD,
                submitted_limit_price,
                instrument.size_precision,
            )
            if limit_order_token_qty is None:
                logger.warning(
                    "SKIP: LIMIT_IOC token quantity is below Polymarket 5-token minimum"
                )
                rec.reject(
                    "limit_ioc_below_min_tokens",
                    f"budget={POSITION_SIZE_USD} submitted_limit_price={submitted_limit_price}",
                )
                return False
            rec.update(
                limit_price=str(accepted_limit_price),
                submitted_limit_price=str(submitted_limit_price),
                limit_order_token_qty=str(limit_order_token_qty),
            )

        depth_entry = await self._compute_depth_aware_entry_details(
            side_token_id=side_token_id,
            entry_source=entry_source,
            position_size_usd=POSITION_SIZE_USD,
            top_of_book_entry=top_of_book_entry,
            rec=rec,
            market_meta=market_meta,
            order_type=order_type,
            submitted_limit_price=submitted_limit_price,
            limit_order_token_qty=limit_order_token_qty,
            limit_ioc_fill_policy=limit_ioc_fill_policy,
            order_book=selected_order_book,
        )
        if depth_entry is None:
            return False
        executable_entry = depth_entry.executable_entry
        if depth_entry.actual_cost is None:
            raise RuntimeError("depth-aware entry actual_cost must be explicit")
        rec.update(
            executable_entry=executable_entry,
            estimated_tokens_filled=depth_entry.tokens_filled,
            estimated_actual_cost=depth_entry.actual_cost,
            depth_fully_filled=depth_entry.fully_filled,
        )

        # This is a heuristic confidence filter, not a calibrated EV model.
        # The processor confidence values are not yet trained settlement probabilities.
        fee_buffer = order_config["ev_fee_buffer"]
        spread_buffer = order_config["ev_spread_buffer"]
        min_required_confidence = executable_entry + fee_buffer + spread_buffer
        if Decimal(str(fused.confidence)) < min_required_confidence:
            logger.info(
                f"SKIP: fused heuristic confidence {fused.confidence:.2%} below "
                f"entry confidence threshold {float(min_required_confidence):.2%} "
                f"({entry_source} {float(executable_entry):.2%} + buffers "
                f"{float(fee_buffer + spread_buffer):.2%})"
            )
            rec.reject(
                "ev_gate",
                f"fused.confidence={fused.confidence:.4f} < min_required={float(min_required_confidence):.4f} "
                f"({entry_source}={float(executable_entry):.4f} + buffers={float(fee_buffer + spread_buffer):.4f})",
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
            rec.reject("risk_engine", str(error))
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
                rec.reject(
                    "liquidity_floor_yes_ask",
                    f"YES ask={float(last_ask):.4f} <= {float(MIN_LIQUIDITY):.2f}",
                )
                return False
            if direction == "short":
                no_tick = getattr(self, "_last_no_bid_ask", None)
                if not no_tick:
                    logger.warning(
                        "⚠ Skipping DOWN/NO trade: no direct NO quote available yet. "
                        "Waiting for NO ask; will retry next tick."
                    )
                    rec.reject("no_no_quote_at_liquidity_check", "_last_no_bid_ask is None")
                    return False
                no_bid, no_ask = no_tick
                if no_ask <= MIN_LIQUIDITY:
                    logger.warning(
                        f"⚠ Skipping DOWN/NO trade: NO ask=${float(no_ask):.4f} "
                        f"is at or below the ${float(MIN_LIQUIDITY):.2f} liquidity floor. "
                        "Market is too thin/extreme; will retry next tick."
                    )
                    rec.reject(
                        "liquidity_floor_no_ask",
                        f"NO ask={float(no_ask):.4f} <= {float(MIN_LIQUIDITY):.2f}",
                    )
                    return False

        # Positive decision — record before delegating to executor.
        rec.decided(direction=direction)

        # --- Phase 5 / 6: Execute ---
        if is_simulation:
            placed = await self._record_paper_trade(fused, POSITION_SIZE_USD, current_price, direction)
        else:
            placed = await self._place_real_order(
                fused,
                POSITION_SIZE_USD,
                current_price,
                direction,
                order_type=order_type,
                accepted_limit_price=accepted_limit_price,
                submitted_limit_price=submitted_limit_price,
                limit_order_token_qty=limit_order_token_qty,
            )
        if not placed:
            # Reject reason tracked at the executor layer; surface a generic
            # late-stage rejection so the record still reflects the outcome.
            rec.reject("executor_returned_false", "place_real_order or paper_trade returned False")
        if placed and trade_key is not None:
            self.last_trade_time = trade_key
        return placed

    async def _compute_depth_aware_entry(
        self,
        side_token_id: Optional[str],
        entry_source: str,
        position_size_usd: Decimal,
        top_of_book_entry: Decimal,
        rec: DecisionRecord,
        market_meta: Optional[Dict[str, Any]] = None,
        order_book: Any = _ORDER_BOOK_NOT_PROVIDED,
    ) -> Optional[Decimal]:
        details = await self._compute_depth_aware_entry_details(
            side_token_id=side_token_id,
            entry_source=entry_source,
            position_size_usd=position_size_usd,
            top_of_book_entry=top_of_book_entry,
            rec=rec,
            market_meta=market_meta,
            order_book=order_book,
            order_type=ORDER_TYPE_MARKET_IOC,
        )
        if details is None:
            return None
        return details.executable_entry

    async def _compute_depth_aware_entry_details(
        self,
        side_token_id: Optional[str],
        entry_source: str,
        position_size_usd: Decimal,
        top_of_book_entry: Decimal,
        rec: DecisionRecord,
        market_meta: Optional[Dict[str, Any]] = None,
        order_type: str = ORDER_TYPE_MARKET_IOC,
        submitted_limit_price: Optional[Decimal] = None,
        limit_order_token_qty: Optional[Decimal] = None,
        limit_ioc_fill_policy: Optional[str] = None,
        order_book: Any = _ORDER_BOOK_NOT_PROVIDED,
    ) -> Optional[DepthAwareEntry]:
        """Phase 5A — return the VWAP executable entry for the selected
        token's asks, or None when the book cannot be evaluated.

        Fail-closed semantics: if the caller omits the decision-cycle snapshot,
        this raises. If the token id is unknown, if the provided snapshot has no
        asks, if any book level is malformed, or if the book is too thin to fill
        the full position size, this method logs the reason via ``rec.reject``
        and returns None. Callers treat None as "skip the trade."

        When ``market_meta`` is supplied, the helper additionally enforces
        that ``side_token_id`` actually belongs to the side named in
        ``entry_source`` (YES vs NO). This guards against future refactors
        that would silently compute the VWAP off the wrong side's book.

        No fallback to top-of-book ask under any error condition; per the
        plan's No-Fallback policy, a corrupt/missing book is an actionable
        error, not noise to ignore.
        """
        if not side_token_id:
            logger.warning("SKIP: missing side token id for depth-aware EV gate")
            rec.reject(
                "depth_aware_missing_token_id",
                f"{entry_source} side has no token_id in market metadata",
            )
            return None
        if market_meta is not None:
            label = (entry_source or "").upper()
            if "YES" in label:
                expected_token = market_meta.get("yes_token_id")
                expected_side = "YES"
            elif "NO" in label:
                expected_token = market_meta.get("no_token_id")
                expected_side = "NO"
            else:
                expected_token = None
                expected_side = None
            if expected_side is not None and expected_token is not None and side_token_id != expected_token:
                logger.error(
                    f"SKIP: side_token_id {side_token_id[:16]}… does not match "
                    f"market_meta {expected_side.lower()}_token_id"
                )
                rec.reject(
                    "depth_aware_token_side_mismatch",
                    f"side_token_id does not match market_meta {expected_side.lower()}_token_id "
                    f"for entry_source={entry_source!r}",
                )
                return None
        if order_book is _ORDER_BOOK_NOT_PROVIDED:
            raise RuntimeError(
                "depth-aware entry requires caller-provided order_book snapshot"
            )
        book = order_book
        if not book:
            logger.warning("SKIP: depth-aware order-book snapshot is empty")
            rec.reject(
                "depth_aware_no_book",
                f"{entry_source} provided order_book snapshot is None/empty",
            )
            return None
        asks = book.get("asks") or []
        if not asks:
            logger.warning("SKIP: depth-aware book has no asks")
            rec.reject(
                "depth_aware_empty_asks",
                f"{entry_source} book has no ask levels",
            )
            return None
        if order_type == ORDER_TYPE_MARKET_IOC:
            try:
                vwap, tokens_filled, fully_filled = estimate_market_ioc_fill(
                    asks, Decimal(str(position_size_usd))
                )
            except InvalidBookLevelError as exc:
                logger.warning(f"SKIP: depth-aware book has invalid level: {exc}")
                rec.reject(
                    "depth_aware_invalid_book_level",
                    f"{entry_source}: {exc}",
                )
                return None
            if vwap is None or not fully_filled:
                tokens_str = f"{float(tokens_filled):.6f}" if tokens_filled else "0"
                logger.warning(
                    f"SKIP: depth-aware book too thin for ${float(position_size_usd):.2f} "
                    f"{entry_source} sweep (tokens fillable: {tokens_str})"
                )
                rec.reject(
                    "depth_aware_book_too_thin",
                    f"{entry_source} cannot fill ${float(position_size_usd):.2f} "
                    f"(tokens fillable: {tokens_str})",
                )
                return None
            logger.info(
                f"DEPTH-AWARE entry: {entry_source} VWAP=${float(vwap):.4f} "
                f"(top-of-book ${float(top_of_book_entry):.4f}, "
                f"fills {float(tokens_filled):.4f} tokens at ${float(position_size_usd):.2f})"
            )
            return DepthAwareEntry(
                executable_entry=vwap,
                tokens_filled=tokens_filled,
                actual_cost=position_size_usd,
                fully_filled=fully_filled,
            )

        if order_type != ORDER_TYPE_LIMIT_IOC:
            raise RuntimeError(f"unexpected ORDER_TYPE after validation: {order_type!r}")
        if submitted_limit_price is None or limit_order_token_qty is None:
            raise RuntimeError("LIMIT_IOC depth estimation requires submitted_limit_price and token quantity")
        try:
            vwap, tokens_filled, actual_cost, fully_filled = estimate_limit_ioc_fill(
                asks,
                limit_order_token_qty,
                submitted_limit_price,
            )
        except InvalidBookLevelError as exc:
            logger.warning(f"SKIP: depth-aware book has invalid level: {exc}")
            rec.reject(
                "depth_aware_invalid_book_level",
                f"{entry_source}: {exc}",
            )
            return None
        if vwap is None or tokens_filled <= 0:
            tokens_str = f"{float(tokens_filled):.6f}" if tokens_filled else "0"
            logger.warning(
                f"SKIP: LIMIT_IOC no executable {entry_source} liquidity at "
                f"price <= ${float(submitted_limit_price):.4f} "
                f"(tokens fillable: {tokens_str})"
            )
            rec.reject(
                "depth_aware_limit_ioc_no_liquidity",
                f"{entry_source} no fill at submitted_limit_price={submitted_limit_price}",
            )
            return None
        if limit_ioc_fill_policy == LIMIT_IOC_FILL_POLICY_ALL_OR_NOTHING:
            raise RuntimeError(
                "LIMIT_IOC_FILL_POLICY=all_or_nothing requires verified FOK wire behavior; "
                "current LIMIT+IOC wire behavior is FAK"
            )
        if limit_ioc_fill_policy != LIMIT_IOC_FILL_POLICY_PARTIAL_OK:
            raise RuntimeError(
                f"LIMIT_IOC depth estimation requires partial_ok policy, got {limit_ioc_fill_policy!r}"
            )
        if not fully_filled:
            logger.info(
                f"LIMIT_IOC partial_ok: {float(tokens_filled):.6f} of "
                f"{float(limit_order_token_qty):.6f} tokens executable at "
                f"price <= ${float(submitted_limit_price):.4f}; "
                f"estimated cost ${float(actual_cost):.2f}"
            )
        logger.info(
            f"DEPTH-AWARE entry: {entry_source} VWAP=${float(vwap):.4f} "
            f"(top-of-book ${float(top_of_book_entry):.4f}, "
            f"LIMIT_IOC fills {float(tokens_filled):.4f} tokens at "
            f"estimated cost ${float(actual_cost):.2f})"
        )
        return DepthAwareEntry(
            executable_entry=vwap,
            tokens_filled=tokens_filled,
            actual_cost=actual_cost,
            fully_filled=fully_filled,
        )

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

    async def _place_real_order(
        self,
        signal,
        position_size,
        current_price,
        direction,
        *,
        order_type: str,
        accepted_limit_price: Optional[Decimal] = None,
        submitted_limit_price: Optional[Decimal] = None,
        limit_order_token_qty: Optional[Decimal] = None,
    ) -> bool:
        if not self.instrument_id:
            logger.error("No instrument available")
            return False

        # Runtime gate: enforce MARKET_BUY_USD > 5.50 before any live order
        # submission. This is defense-in-depth for live-enabled processes whose
        # environment changes after startup or for nonstandard submit call paths.
        # The live path rejects the trade without using exception conversion as
        # control flow.
        gate_ok, gate_err, gate_amount = validate_live_market_buy_usd()
        if not gate_ok:
            logger.error(f"LIVE ORDER BLOCKED: {gate_err}")
            return False

        # Cycle-2 reviewer #2 finding: caller passes `position_size` derived
        # from get_market_buy_usd() (ROUND_HALF_EVEN), while the gate quantizes
        # with ROUND_DOWN. For values like 5.515 the two could diverge by one
        # cent. Reject the order rather than silently sizing differently from
        # what the gate validated.
        if Decimal(str(position_size)).quantize(Decimal("0.01"), rounding=ROUND_DOWN) != gate_amount:
            logger.error(
                f"LIVE ORDER BLOCKED: caller-supplied position_size={position_size} "
                f"does not match gate-validated amount {gate_amount}. Restart with a "
                f"consistent MARKET_BUY_USD."
            )
            return False

        runtime_order_config = validate_live_order_config()
        runtime_order_type = runtime_order_config["order_type"]
        if runtime_order_type != order_type:
            logger.error(
                f"LIVE ORDER BLOCKED: order_type changed between decision and submit "
                f"({order_type!r} -> {runtime_order_type!r})"
            )
            return False
        quote_stability_required = runtime_order_config["quote_stability_required"]
        if self._stable_tick_count < quote_stability_required:
            logger.error(
                "LIVE ORDER BLOCKED: quote stability below configured threshold "
                f"({self._stable_tick_count} < {quote_stability_required})"
            )
            return False
        limit_ioc_fill_policy = runtime_order_config["limit_ioc_fill_policy"]
        if order_type == ORDER_TYPE_LIMIT_IOC:
            if accepted_limit_price is None:
                logger.error("LIVE ORDER BLOCKED: LIMIT_IOC requires accepted_limit_price from decision path")
                return False
            accepted_limit_price = Decimal(str(accepted_limit_price))
            if (
                not accepted_limit_price.is_finite()
                or accepted_limit_price <= 0
                or accepted_limit_price >= 1
            ):
                logger.error(
                    f"LIVE ORDER BLOCKED: accepted_limit_price must be in (0, 1), got {accepted_limit_price}"
                )
                return False
            if submitted_limit_price is None or submitted_limit_price <= 0:
                logger.error(
                    f"LIVE ORDER BLOCKED: submitted_limit_price must be positive, got {submitted_limit_price}"
                )
                return False
            if limit_order_token_qty is None or limit_order_token_qty < POLYMARKET_LIMIT_MIN_TOKENS:
                logger.error(
                    f"LIVE ORDER BLOCKED: limit_order_token_qty must be >= "
                    f"{POLYMARKET_LIMIT_MIN_TOKENS}, got {limit_order_token_qty}"
                )
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
                if not hasattr(self, "_yes_instrument_id") or self._yes_instrument_id is None:
                    logger.warning(
                        "YES token instrument not found for this market — skipping trade."
                    )
                    return False
                trade_instrument_id = self._yes_instrument_id
                trade_label = "YES (UP)"
            else:
                if not hasattr(self, "_no_instrument_id") or self._no_instrument_id is None:
                    logger.warning(
                        "NO token instrument not found for this market — "
                        "cannot bet DOWN. Skipping trade."
                    )
                    return False
                trade_instrument_id = self._no_instrument_id
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

            timestamp_ms = int(time.time() * 1000)
            unique_id = f"BTC-15MIN-${max_usd_amount:.0f}-{timestamp_ms}"
            submitted_at = datetime.now(timezone.utc)
            market_meta = self._require_current_market_metadata("live order placement")
            market_slug = market_meta.get("slug")
            condition_id = market_meta.get("condition_id")
            if market_slug in (None, "") or condition_id in (None, ""):
                logger.error(
                    "Live order rejected: current market metadata missing slug or condition_id"
                )
                return False
            market_end_time = self._parse_utc_datetime(market_meta.get("end_time"))
            if market_end_time is None:
                logger.error(
                    f"Live order rejected: missing/invalid market_end_time for {market_slug}"
                )
                return False
            token_id = (
                market_meta.get("yes_token_id")
                if direction == "long"
                else market_meta.get("no_token_id")
            )
            if token_id in (None, ""):
                logger.error(
                    f"Live order rejected: current market metadata missing token_id for {trade_label}"
                )
                return False
            instrument_identity = self._extract_polymarket_instrument_identity(
                trade_instrument_id,
                "live order placement",
            )
            if instrument_identity["condition_id"] != str(condition_id):
                logger.error(
                    "Live order rejected: trade instrument condition_id does not match "
                    f"current market metadata ({instrument_identity['condition_id']} != {condition_id})"
                )
                return False
            if instrument_identity["token_id"] != str(token_id):
                logger.error(
                    "Live order rejected: trade instrument token_id does not match "
                    f"current market metadata ({instrument_identity['token_id']} != {token_id})"
                )
                return False

            if order_type == ORDER_TYPE_MARKET_IOC:
                trade_price = float(quoted_price)
                if not math.isfinite(trade_price) or trade_price <= 0:
                    logger.error(f"Live order rejected: invalid {price_source} price {quoted_price!r}")
                    return False
                estimated_tokens = max_usd_amount / trade_price
                logger.info(
                    f"BUY {trade_label}: spending ${max_usd_amount:.2f} USDC.e "
                    f"(estimated {estimated_tokens:.6f} tokens at ${trade_price:.4f} from {price_source})"
                )
                qty = Quantity.from_str(f"{max_usd_amount:.2f}")
                order = self.order_factory.market(
                    instrument_id=trade_instrument_id,
                    order_side=side,
                    quantity=qty,
                    client_order_id=ClientOrderId(unique_id),
                    quote_quantity=True,
                    time_in_force=TimeInForce.IOC,
                )
                entry_price_for_intent = Decimal(str(trade_price))
                estimated_tokens_for_intent = Decimal(str(estimated_tokens))
                quantity_mode = "quote_quantity"
                quote_quantity = True
                submitted_price_for_intent = None
            elif order_type == ORDER_TYPE_LIMIT_IOC:
                size_precision = instrument.size_precision
                price_precision = instrument.price_precision
                if submitted_limit_price > accepted_limit_price:
                    logger.error(
                        "LIVE ORDER BLOCKED: submitted LIMIT_IOC price exceeds accepted cap "
                        f"({submitted_limit_price} > {accepted_limit_price})"
                    )
                    return False
                qty_str = format(limit_order_token_qty, f".{size_precision}f")
                price_str = format(submitted_limit_price, f".{price_precision}f")
                qty_roundtrip = Decimal(qty_str)
                price_roundtrip = Decimal(price_str)
                if qty_roundtrip != limit_order_token_qty:
                    logger.error(
                        "LIVE ORDER BLOCKED: LIMIT_IOC quantity is not exactly representable "
                        f"at venue precision ({limit_order_token_qty} -> {qty_str})"
                    )
                    return False
                if price_roundtrip != submitted_limit_price:
                    logger.error(
                        "LIVE ORDER BLOCKED: LIMIT_IOC price is not exactly representable "
                        f"at venue precision ({submitted_limit_price} -> {price_str})"
                    )
                    return False
                if price_roundtrip > accepted_limit_price:
                    logger.error(
                        "LIVE ORDER BLOCKED: formatted LIMIT_IOC price exceeds accepted cap "
                        f"({price_roundtrip} > {accepted_limit_price})"
                    )
                    return False
                worst_case_notional = qty_roundtrip * price_roundtrip
                if worst_case_notional > Decimal(str(position_size)):
                    logger.error(
                        "LIVE ORDER BLOCKED: LIMIT_IOC worst-case notional exceeds budget "
                        f"({worst_case_notional} > {position_size})"
                    )
                    return False
                order = self.order_factory.limit(
                    instrument_id=trade_instrument_id,
                    order_side=side,
                    quantity=Quantity.from_str(qty_str),
                    price=Price.from_str(price_str),
                    client_order_id=ClientOrderId(unique_id),
                    quote_quantity=False,
                    time_in_force=TimeInForce.IOC,
                )
                entry_price_for_intent = submitted_limit_price
                estimated_tokens_for_intent = limit_order_token_qty
                quantity_mode = "base_quantity"
                quote_quantity = False
                submitted_price_for_intent = submitted_limit_price
                logger.info(
                    f"BUY {trade_label}: LIMIT_IOC {limit_order_token_qty} tokens "
                    f"at price <= ${float(submitted_limit_price):.4f}; "
                    f"worst-case spend ${max_usd_amount:.2f}"
                )
            else:
                raise RuntimeError(f"unexpected ORDER_TYPE after validation: {order_type!r}")

            submitted_meta = {
                "entry_price": entry_price_for_intent,
                "size": position_size,
                "direction": direction,
                "trade_label": trade_label,
                "estimated_tokens": estimated_tokens_for_intent,
                "order_type": order_type,
                "quote_quantity": quote_quantity,
                "quantity_mode": quantity_mode,
                "limit_ioc_fill_policy": limit_ioc_fill_policy,
                "accepted_limit_price": accepted_limit_price,
                "submitted_limit_price": submitted_price_for_intent,
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
                        f"before submit_order ({len(unresolved)} item(s)). Resolve or repair "
                        f"unresolved ledger state in {LIVE_TRADE_LEDGER_PATH.name}."
                    )
                    return False
                self._persist_submitted_order_intent_locked(
                    order_id=unique_id,
                    meta=submitted_meta,
                    price_source=price_source,
                )
                self._submitted_positions[unique_id] = submitted_meta
                self.risk_engine.add_position(
                    position_id=unique_id,
                    size=position_size,
                    entry_price=entry_price_for_intent,
                    direction="buy_yes" if direction == "long" else "buy_no",
                )

            self.submit_order(order)

            logger.info(f"REAL ORDER SUBMITTED!")
            logger.info(f"  Order ID: {unique_id}")
            logger.info(f"  Direction: {trade_label}")
            logger.info(f"  Side: BUY")
            logger.info(f"  Spend Amount: ${max_usd_amount:.2f} USDC.e")
            logger.info(f"  Estimated Tokens: {float(estimated_tokens_for_intent):.6f}")
            logger.info(f"  Estimated Price: ${float(entry_price_for_intent):.4f} ({price_source})")
            logger.info(f"  Quantity Mode: {quantity_mode}")
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
            if order_type == ORDER_TYPE_LIMIT_IOC:
                raise
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

        Order-event metrics are best-effort observability and do not affect
        trading, settlement, risk, or ledger state.
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

    def _risk_release_position_callable(self, order_id: str, context: str):
        """Return the explicit risk release method required for cleanup paths."""
        release_position = getattr(self.risk_engine, "release_position", None)
        if not callable(release_position):
            reason = (
                f"{context}: risk_engine.release_position unavailable for {order_id}; "
                "refusing to use settlement-accounting remove_position as a release path"
            )
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        return release_position

    def _release_risk_position_without_pnl(self, order_id: str, context: str) -> None:
        """Release reserved exposure; never book settlement P&L from cleanup paths."""
        release_position = self._risk_release_position_callable(order_id, context)
        try:
            release_position(order_id)
        except Exception as exc:
            reason = f"{context}: risk position release failed for {order_id}: {exc}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason) from exc

    def _build_submitted_order_intent(
        self,
        order_id: str,
        meta: Dict[str, Any],
        price_source: str,
    ) -> Dict[str, Any]:
        """Build the durable pre-submit intent record without exchange-fill data."""
        required = (
            "direction",
            "trade_label",
            "size",
            "estimated_tokens",
            "entry_price",
            "order_type",
            "quote_quantity",
            "quantity_mode",
            "instrument_id",
            "token_id",
            "slug",
            "condition_id",
            "market_start_time",
            "market_end_time",
            "submitted_at",
        )
        missing = [key for key in required if meta.get(key) in (None, "")]
        if missing:
            reason = f"submitted_order_intent_missing_required_fields:{','.join(missing)}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        direction = str(meta["direction"])
        if direction not in {"long", "short"}:
            reason = f"submitted_order_intent_invalid_direction:{direction!r}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        order_type = str(meta["order_type"])
        if order_type not in _ALLOWED_ORDER_TYPES:
            reason = f"submitted_order_intent_invalid_order_type:{order_type!r}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        return {
            "client_order_id": order_id,
            "order_id": order_id,
            "status": "INTENT_PERSISTED",
            "order_side": "BUY",
            "order_type": order_type,
            "quote_quantity": meta["quote_quantity"],
            "quantity_mode": meta["quantity_mode"],
            "direction": direction,
            "outcome_side": "YES" if direction == "long" else "NO",
            "trade_label": meta["trade_label"],
            "spend_amount": meta["size"],
            "size": meta["size"],
            "estimated_tokens": meta["estimated_tokens"],
            "estimated_price": meta["entry_price"],
            "entry_price": meta["entry_price"],
            "limit_ioc_fill_policy": meta.get("limit_ioc_fill_policy"),
            "accepted_limit_price": meta.get("accepted_limit_price"),
            "submitted_limit_price": meta.get("submitted_limit_price"),
            "price_source": price_source,
            "instrument_id": meta["instrument_id"],
            "token_id": meta["token_id"],
            "slug": meta["slug"],
            "condition_id": meta["condition_id"],
            "market_start_time": meta["market_start_time"],
            "market_end_time": meta["market_end_time"],
            "submitted_at": meta["submitted_at"],
            "intent_persisted_at": datetime.now(timezone.utc).isoformat(),
            "signal_score": meta.get("signal_score"),
            "signal_confidence": meta.get("signal_confidence"),
        }

    def _persist_submitted_order_intent_locked(
        self,
        order_id: str,
        meta: Dict[str, Any],
        price_source: str,
    ) -> None:
        """Persist order intent before exchange submission without mutating first."""
        intent = self._build_submitted_order_intent(order_id, meta, price_source)
        submitted_order_intents = dict(self._submitted_order_intents)
        submitted_order_intents[order_id] = intent
        saved_state = self._save_live_trade_ledger_state(
            open_trades=self._open_live_trades,
            settled_trades=self._settled_live_trades,
            seen_events=self._seen_auto_redeem_events,
            seen_order=self._seen_auto_redeem_event_order,
            pending_events=self._pending_auto_redeem_events,
            pending_actual_fills=self._pending_actual_fills,
            submitted_order_intents=submitted_order_intents,
        )
        self._apply_saved_live_trade_ledger_state(saved_state)

    def _terminal_event_json_snapshot(self, value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {
                str(key): self._terminal_event_json_snapshot(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._terminal_event_json_snapshot(item) for item in value]
        return str(value)

    def _terminal_event_audit_payload(self, event) -> Dict[str, Any]:
        """Capture terminal event fields for submitted-intent audit."""
        payload = {"event_type": type(event).__name__}
        raw_event = {
            "event_type": type(event).__name__,
            "repr": repr(event),
            "fields": {},
        }
        for key in (
            "client_order_id",
            "venue_order_id",
            "reason",
            "ts_event",
            "ts_init",
            "account_id",
            "instrument_id",
            "info",
            "last_qty",
            "filled_qty",
            "filled",
            "last_px",
            "avg_px",
            *ACTUAL_FILL_UNIQUE_KEY_FIELDS,
        ):
            if hasattr(event, key):
                value = getattr(event, key)
                payload[key] = str(value) if value is not None else None
                raw_event["fields"][key] = self._terminal_event_json_snapshot(value)
        if hasattr(event, "__dict__"):
            event_dict = event.__dict__
            if not isinstance(event_dict, dict):
                reason = (
                    f"terminal event {type(event).__name__} __dict__ is not a JSON object"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            raw_event["instance_attrs"] = self._terminal_event_json_snapshot(event_dict)
        payload["raw_event"] = raw_event
        return payload

    def _decimal_from_terminal_event_field(self, field: str, value) -> Decimal:
        try:
            parsed = Decimal(str(value.as_decimal() if hasattr(value, "as_decimal") else value))
        except Exception as exc:
            raise SettlementLedgerError(f"terminal event field {field} is not decimal: {value!r}") from exc
        if not parsed.is_finite():
            raise SettlementLedgerError(f"terminal event field {field} is not finite: {value!r}")
        return parsed

    def _verify_terminal_event_no_fill(self, order_id: str, status: str, event) -> Dict[str, str]:
        """Require explicit zero-fill quantity evidence before classifying terminal no-fill."""
        zero_quantity_fields = {}
        non_zero_evidence = {}
        for key in ("last_qty", "filled_qty", "filled"):
            if not hasattr(event, key):
                continue
            value = getattr(event, key)
            if value in (None, ""):
                non_zero_evidence[key] = str(value)
                continue
            try:
                parsed = self._decimal_from_terminal_event_field(key, value)
            except SettlementLedgerError as exc:
                reason = (
                    f"terminal order event for {order_id} has invalid fill quantity field; "
                    f"status={status} field={key} value={value!r}: {exc}"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason) from exc
            if parsed == 0:
                zero_quantity_fields[key] = str(value)
            else:
                non_zero_evidence[key] = str(value)

        for key in ("avg_px", "last_px"):
            if not hasattr(event, key):
                continue
            value = getattr(event, key)
            if value in (None, ""):
                continue
            try:
                parsed = self._decimal_from_terminal_event_field(key, value)
            except SettlementLedgerError as exc:
                reason = (
                    f"terminal order event for {order_id} has invalid fill price field; "
                    f"status={status} field={key} value={value!r}: {exc}"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason) from exc
            if parsed != 0:
                non_zero_evidence[key] = str(value)

        for key in ACTUAL_FILL_UNIQUE_KEY_FIELDS:
            if not hasattr(event, key):
                continue
            value = getattr(event, key)
            if value not in (None, ""):
                non_zero_evidence[key] = str(value)

        if non_zero_evidence:
            reason = (
                f"terminal order event for {order_id} had fill evidence; "
                f"status={status} evidence={non_zero_evidence}"
            )
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        if not zero_quantity_fields:
            reason = (
                f"terminal order event for {order_id} lacks verified zero-fill quantity; "
                f"status={status}"
            )
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        return zero_quantity_fields

    def _mark_submitted_order_intent_terminal_no_fill(
        self,
        order_id: str,
        status: str,
        event,
        reason: str,
        context: str,
        zero_fill_evidence: Dict[str, str],
    ) -> bool:
        """Persist terminal no-fill submitted intent audit without deleting the intent."""
        if status not in TERMINAL_NO_FILL_INTENT_STATUSES or status == "SUBMISSION_NOT_SEEN":
            raise SettlementLedgerError(f"invalid terminal no-fill intent status: {status}")
        with self._settlement_lock:
            if order_id not in self._submitted_order_intents:
                block_reason = f"submitted order intent missing for terminal no-fill audit: {order_id}"
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason)
            submitted_order_intents = dict(self._submitted_order_intents)
            existing_intent = submitted_order_intents[order_id]
            if not isinstance(existing_intent, dict):
                block_reason = f"submitted order intent for terminal no-fill audit is not a JSON object: {order_id}"
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason)
            intent = dict(existing_intent)
            intent.update(
                {
                    "status": status,
                    "needs_reconciliation": False,
                    "terminal_no_fill_at": datetime.now(timezone.utc).isoformat(),
                    "terminal_no_fill_reason": reason,
                    "terminal_no_fill_event": self._terminal_event_audit_payload(event),
                    "terminal_no_fill_zero_quantity_evidence": dict(zero_fill_evidence),
                }
            )
            submitted_order_intents[order_id] = intent
            saved_state = self._try_save_live_trade_ledger_state(
                context,
                open_trades=self._open_live_trades,
                settled_trades=self._settled_live_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
                pending_actual_fills=self._pending_actual_fills,
                submitted_order_intents=submitted_order_intents,
            )
            if saved_state is None:
                return False
            self._apply_saved_live_trade_ledger_state(saved_state)
            return True

    def _positive_recorded_fill_qty(self, order_id: str, context: str, raw_qty) -> bool:
        try:
            qty = Decimal(str(raw_qty))
        except Exception as exc:
            reason = f"{context} for {order_id} has invalid fill quantity: {raw_qty!r}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason) from exc
        if not qty.is_finite() or qty < 0:
            reason = f"{context} for {order_id} has impossible fill quantity: {raw_qty!r}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        return qty > 0

    def _terminal_event_fill_detail_candidates(
        self,
        order_id: str,
        event,
    ) -> List[TerminalFillDetails]:
        field_pairs = (
            ("filled_qty", "avg_px", "cumulative", "average_price"),
            ("filled", "avg_px", "cumulative", "average_price"),
            ("last_qty", "last_px", "incremental", "last_price"),
            ("last_qty", "avg_px", "incremental", "average_price"),
            ("filled_qty", "last_px", "cumulative", "last_price"),
            ("filled", "last_px", "cumulative", "last_price"),
        )
        candidates = []
        for qty_field, price_field, quantity_semantics, price_semantics in field_pairs:
            if not hasattr(event, qty_field) or not hasattr(event, price_field):
                continue
            raw_qty = getattr(event, qty_field)
            raw_price = getattr(event, price_field)
            if raw_qty in (None, "") or raw_price in (None, ""):
                continue
            qty = self._decimal_from_terminal_event_field(qty_field, raw_qty)
            price = self._decimal_from_terminal_event_field(price_field, raw_price)
            if qty < 0 or price < 0:
                reason = (
                    f"terminal order event for {order_id} has negative fill details; "
                    f"{qty_field}={raw_qty!r} {price_field}={raw_price!r}"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            if qty > 0 and price > 0:
                candidates.append(
                    TerminalFillDetails(
                        quantity=qty,
                        price=price,
                        field_source=f"{qty_field}/{price_field}",
                        quantity_semantics=quantity_semantics,
                        price_semantics=price_semantics,
                    )
                )
            if qty > 0 and price <= 0:
                reason = (
                    f"terminal order event for {order_id} has positive fill quantity "
                    f"but non-positive fill price; {qty_field}={raw_qty!r} "
                    f"{price_field}={raw_price!r}"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
        return candidates

    def _terminal_event_fill_details(
        self,
        order_id: str,
        event,
    ) -> Optional[TerminalFillDetails]:
        candidates = self._terminal_event_fill_detail_candidates(order_id, event)
        if not candidates:
            return None
        return candidates[0]

    def _terminal_event_positive_quantities(self, order_id: str, event) -> Dict[str, Decimal]:
        quantities = {}
        for qty_field in ("last_qty", "filled_qty", "filled"):
            if not hasattr(event, qty_field):
                continue
            raw_qty = getattr(event, qty_field)
            if raw_qty in (None, ""):
                continue
            qty = self._decimal_from_terminal_event_field(qty_field, raw_qty)
            if qty < 0:
                reason = (
                    f"terminal order event for {order_id} has negative fill quantity; "
                    f"{qty_field}={raw_qty!r}"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            if qty > 0:
                quantities[qty_field] = qty
        return quantities

    def _terminal_event_quantity_values(self, order_id: str, event) -> Dict[str, Decimal]:
        quantities = {}
        for qty_field in ("last_qty", "filled_qty", "filled"):
            if not hasattr(event, qty_field):
                continue
            raw_qty = getattr(event, qty_field)
            if raw_qty in (None, ""):
                continue
            qty = self._decimal_from_terminal_event_field(qty_field, raw_qty)
            if qty < 0:
                reason = (
                    f"terminal order event for {order_id} has negative fill quantity; "
                    f"{qty_field}={raw_qty!r}"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            quantities[qty_field] = qty
        return quantities

    def _add_fill_metadata_value(
        self,
        metadata: Dict[str, str],
        key: str,
        value,
        source: str,
        order_id: str,
    ) -> None:
        if value in (None, ""):
            return
        value_str = str(value)
        existing_value = metadata.get(key)
        if existing_value is not None and existing_value != value_str:
            reason = (
                f"fill metadata for {order_id} conflicts on {key}: "
                f"{existing_value!r} != {value_str!r} from {source}"
            )
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        metadata[key] = value_str

    def _terminal_event_fill_metadata(self, event) -> Dict[str, str]:
        order_id = str(getattr(event, "client_order_id", "<missing-client-order-id>"))
        metadata = {}
        for key in FILL_METADATA_IDENTITY_KEYS:
            if hasattr(event, key):
                self._add_fill_metadata_value(
                    metadata,
                    key,
                    getattr(event, key),
                    f"event.{key}",
                    order_id,
                )
        info = getattr(event, "info", None)
        if info not in (None, ""):
            if not isinstance(info, dict):
                reason = f"fill metadata for {order_id} has non-dict event.info"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            for source_key in AUTO_REDEEM_TOKEN_HINT_KEYS:
                if source_key in info:
                    self._add_fill_metadata_value(
                        metadata,
                        "token_id",
                        info[source_key],
                        f"event.info[{source_key!r}]",
                        order_id,
                    )
            for source_key in FILL_INFO_CONDITION_HINT_KEYS:
                if source_key in info:
                    self._add_fill_metadata_value(
                        metadata,
                        "condition_id",
                        info[source_key],
                        f"event.info[{source_key!r}]",
                        order_id,
                    )
            for source_key in FILL_INFO_SLUG_HINT_KEYS:
                if source_key in info:
                    self._add_fill_metadata_value(
                        metadata,
                        "slug",
                        info[source_key],
                        f"event.info[{source_key!r}]",
                        order_id,
                    )
        if "instrument_id" in metadata:
            instrument_identity = self._extract_polymarket_instrument_identity(
                metadata["instrument_id"],
                f"fill metadata for {order_id}",
                block_settlement_ledger=True,
            )
            for key, value in instrument_identity.items():
                self._add_fill_metadata_value(
                    metadata,
                    key,
                    value,
                    "event.instrument_id",
                    order_id,
                )
        return metadata

    def _recorded_fill_accounting_snapshot(self, order_id: str) -> Optional[Dict[str, Any]]:
        with self._settlement_lock:
            open_meta = self._open_live_trades.get(order_id)
            if open_meta is not None:
                if not isinstance(open_meta, dict):
                    reason = f"open live trade for {order_id} is not a JSON object"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
                size, filled_qty, entry_price = self._settlement_accounting_values(order_id, open_meta)
                return {
                    "source": "open_live_trades",
                    "filled_qty": filled_qty,
                    "filled_notional": size,
                    "vwap": entry_price,
                    "meta": copy.deepcopy(open_meta),
                }

            submitted_meta = self._submitted_positions.get(order_id)
            if submitted_meta is not None and not isinstance(submitted_meta, dict):
                reason = f"submitted position for {order_id} is not a JSON object"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            if isinstance(submitted_meta, dict):
                actual_qty = submitted_meta.get("_actual_filled_qty")
                actual_vwap = submitted_meta.get("_actual_fill_vwap")
                if (actual_qty in (None, "")) != (actual_vwap in (None, "")):
                    reason = f"submitted position for {order_id} has incomplete actual-fill evidence"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
                if actual_qty not in (None, ""):
                    try:
                        filled_qty = Decimal(str(actual_qty))
                        vwap = Decimal(str(actual_vwap))
                    except Exception as exc:
                        reason = f"submitted position for {order_id} has invalid actual-fill evidence"
                        self._block_live_settlement_ledger(reason)
                        raise SettlementLedgerError(reason) from exc
                    if (
                        not filled_qty.is_finite()
                        or not vwap.is_finite()
                        or filled_qty <= 0
                        or vwap <= 0
                        or vwap > 1
                    ):
                        reason = f"submitted position for {order_id} has impossible actual-fill evidence"
                        self._block_live_settlement_ledger(reason)
                        raise SettlementLedgerError(reason)
                    return {
                        "source": "submitted_position_actual_fill_override",
                        "filled_qty": filled_qty,
                        "filled_notional": filled_qty * vwap,
                        "vwap": vwap,
                        "meta": copy.deepcopy(submitted_meta),
                    }

            pending_actual_fill = self._pending_actual_fills.get(order_id)
            if pending_actual_fill is None:
                return None
            if not isinstance(pending_actual_fill, dict):
                reason = f"pending actual fill for {order_id} is not a JSON object"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            if pending_actual_fill.get("requires_external_fill_repair") is True:
                reason = f"pending actual fill for {order_id} already requires external repair"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            filled_qty, filled_notional, vwap = self._validate_pending_actual_fill_aggregate(
                order_id,
                pending_actual_fill,
            )
            return {
                "source": "pending_actual_fills",
                "filled_qty": filled_qty,
                "filled_notional": filled_notional,
                "vwap": vwap,
                "meta": copy.deepcopy(pending_actual_fill),
            }

    def _record_terminal_cumulative_fill_unknown(
        self,
        order_id: str,
        fill_details: TerminalFillDetails,
        fill_metadata: Dict[str, str],
        reason: str,
        recorded_snapshot: Optional[Dict[str, Any]],
    ) -> None:
        payload = {
            "status": "failed",
            "reason": reason,
            "fill_price": str(fill_details.price),
            "fill_qty": str(fill_details.quantity),
            "terminal_fill_field_source": fill_details.field_source,
            "terminal_fill_quantity_semantics": fill_details.quantity_semantics,
            "terminal_fill_price_semantics": fill_details.price_semantics,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        if recorded_snapshot is not None:
            payload["recorded_fill_source"] = recorded_snapshot["source"]
            payload["recorded_filled_qty"] = str(recorded_snapshot["filled_qty"])
            payload["recorded_filled_notional"] = str(recorded_snapshot["filled_notional"])
            payload["recorded_vwap"] = str(recorded_snapshot["vwap"])
        payload = self._fill_failure_payload_with_identity(payload, fill_metadata)
        self._create_direct_fill_unknown_preserving_pending(order_id, payload)
        self._block_live_settlement_ledger(
            f"refused terminal cumulative fill for {order_id}: {reason}; "
            "SETTLEMENT_UNKNOWN created for manual reconciliation"
        )
        raise SettlementLedgerError(reason)

    def _record_terminal_fill_evidence_unknown(
        self,
        order_id: str,
        fill_metadata: Dict[str, str],
        reason: str,
        recorded_snapshot: Optional[Dict[str, Any]],
        evidence_payload: Dict[str, Any],
    ) -> None:
        payload = {
            "status": "failed",
            "reason": reason,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        payload.update(evidence_payload)
        if recorded_snapshot is not None:
            payload["recorded_fill_source"] = recorded_snapshot["source"]
            payload["recorded_filled_qty"] = str(recorded_snapshot["filled_qty"])
            payload["recorded_filled_notional"] = str(recorded_snapshot["filled_notional"])
            payload["recorded_vwap"] = str(recorded_snapshot["vwap"])
        payload = self._fill_failure_payload_with_identity(payload, fill_metadata)
        self._create_direct_fill_unknown_preserving_pending(order_id, payload)
        self._block_live_settlement_ledger(
            f"refused terminal fill evidence for {order_id}: {reason}; "
            "SETTLEMENT_UNKNOWN created for manual reconciliation"
        )
        raise SettlementLedgerError(f"{reason}: fill evidence requires reconciliation")

    def _enforce_terminal_fill_metadata_matches_recorded(
        self,
        order_id: str,
        fill_details: TerminalFillDetails,
        fill_metadata: Dict[str, str],
        recorded_snapshot: Dict[str, Any],
    ) -> None:
        tracked_meta = recorded_snapshot["meta"]
        for key, value_str in fill_metadata.items():
            existing_value = tracked_meta.get(key)
            if existing_value not in (None, "") and str(existing_value) != value_str:
                self._record_fill_metadata_conflict_unknown(
                    order_id,
                    fill_details.price,
                    fill_details.quantity,
                    key,
                    existing_value,
                    value_str,
                    fill_metadata,
                )

    def _terminal_fill_tracking_meta(
        self,
        order_id: str,
        recorded_snapshot: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if recorded_snapshot is not None:
            return recorded_snapshot["meta"]
        with self._settlement_lock:
            for source_name, source in (
                ("submitted position", self._submitted_positions.get(order_id)),
                ("open live trade", self._open_live_trades.get(order_id)),
            ):
                if source is None:
                    continue
                if not isinstance(source, dict):
                    reason = f"{source_name} for {order_id} is not a JSON object"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
                return copy.deepcopy(source)
        return None

    def _validate_terminal_fill_detail_candidates(
        self,
        order_id: str,
        selected: TerminalFillDetails,
        candidates: List[TerminalFillDetails],
        fill_metadata: Dict[str, str],
        recorded_snapshot: Optional[Dict[str, Any]],
    ) -> None:
        tracking_meta = self._terminal_fill_tracking_meta(order_id, recorded_snapshot)
        if tracking_meta is None:
            return
        selected_delta_qty = None
        selected_delta_notional = None
        if selected.quantity_semantics == "cumulative" and selected.price_semantics == "average_price":
            selected_notional = selected.quantity * selected.price
            if recorded_snapshot is not None:
                recorded_qty = recorded_snapshot["filled_qty"]
                recorded_notional = recorded_snapshot["filled_notional"]
                if selected.quantity >= recorded_qty:
                    selected_delta_qty = selected.quantity - recorded_qty
                    selected_delta_notional = selected_notional - recorded_notional
            else:
                selected_delta_qty = selected.quantity
                selected_delta_notional = selected_notional
        for candidate in candidates:
            if candidate.field_source == selected.field_source:
                continue
            if (
                selected.quantity_semantics == "incremental"
                and candidate.quantity_semantics == "cumulative"
                and candidate.price_semantics != "average_price"
            ):
                self._record_terminal_fill_evidence_unknown(
                    order_id,
                    fill_metadata,
                    "terminal_cumulative_fill_requires_average_price",
                    recorded_snapshot,
                    {
                        "selected_terminal_fill_field_source": selected.field_source,
                        "selected_terminal_fill_qty": str(selected.quantity),
                        "selected_terminal_fill_price": str(selected.price),
                        "terminal_fill_field_source": candidate.field_source,
                        "terminal_fill_quantity_semantics": candidate.quantity_semantics,
                        "terminal_fill_price_semantics": candidate.price_semantics,
                        "fill_qty": str(candidate.quantity),
                        "fill_price": str(candidate.price),
                    },
                )
            violation = self._limit_ioc_fill_envelope_violation(
                tracking_meta,
                candidate.quantity,
                candidate.price,
            )
            if violation:
                self._record_terminal_fill_evidence_unknown(
                    order_id,
                    fill_metadata,
                    f"terminal_fill_conflicting_evidence:{violation}",
                    recorded_snapshot,
                    {
                        "terminal_fill_field_source": candidate.field_source,
                        "terminal_fill_quantity_semantics": candidate.quantity_semantics,
                        "terminal_fill_price_semantics": candidate.price_semantics,
                        "fill_qty": str(candidate.quantity),
                        "fill_price": str(candidate.price),
                    },
                )
            if (
                selected.quantity_semantics == "cumulative"
                and selected_delta_qty is not None
                and selected_delta_notional is not None
            ):
                if candidate.quantity_semantics == "incremental":
                    candidate_notional = candidate.quantity * candidate.price
                    if (
                        candidate.quantity - selected_delta_qty > SETTLEMENT_ACCOUNTING_COST_TOLERANCE
                        or candidate_notional - selected_delta_notional > SETTLEMENT_ACCOUNTING_COST_TOLERANCE
                    ):
                        self._record_terminal_fill_evidence_unknown(
                            order_id,
                            fill_metadata,
                            "terminal_fill_conflicting_evidence_exceeds_cumulative_delta",
                            recorded_snapshot,
                            {
                                "selected_terminal_fill_field_source": selected.field_source,
                                "selected_terminal_fill_qty": str(selected.quantity),
                                "selected_terminal_fill_price": str(selected.price),
                                "selected_terminal_delta_qty": str(selected_delta_qty),
                                "selected_terminal_delta_notional": str(selected_delta_notional),
                                "terminal_fill_field_source": candidate.field_source,
                                "terminal_fill_quantity_semantics": candidate.quantity_semantics,
                                "terminal_fill_price_semantics": candidate.price_semantics,
                                "fill_qty": str(candidate.quantity),
                                "fill_price": str(candidate.price),
                            },
                        )
                elif (
                    candidate.quantity_semantics == "cumulative"
                    and candidate.price_semantics == "average_price"
                ):
                    candidate_notional = candidate.quantity * candidate.price
                    selected_notional = selected.quantity * selected.price
                    if (
                        abs(candidate.quantity - selected.quantity) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE
                        or abs(candidate_notional - selected_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE
                    ):
                        self._record_terminal_fill_evidence_unknown(
                            order_id,
                            fill_metadata,
                            "terminal_fill_conflicting_cumulative_evidence",
                            recorded_snapshot,
                            {
                                "selected_terminal_fill_field_source": selected.field_source,
                                "selected_terminal_fill_qty": str(selected.quantity),
                                "selected_terminal_fill_price": str(selected.price),
                                "terminal_fill_field_source": candidate.field_source,
                                "terminal_fill_quantity_semantics": candidate.quantity_semantics,
                                "terminal_fill_price_semantics": candidate.price_semantics,
                                "fill_qty": str(candidate.quantity),
                                "fill_price": str(candidate.price),
                            },
                        )

    def _validate_terminal_quantity_consistency(
        self,
        order_id: str,
        selected: TerminalFillDetails,
        quantity_values: Dict[str, Decimal],
        fill_metadata: Dict[str, str],
        recorded_snapshot: Optional[Dict[str, Any]],
    ) -> None:
        selected_floor = selected.quantity
        if (
            recorded_snapshot is not None
            and selected.quantity_semantics == "incremental"
        ):
            selected_floor += recorded_snapshot["filled_qty"]
        for key in ("filled_qty", "filled"):
            if key not in quantity_values:
                continue
            cumulative_qty = quantity_values[key]
            if cumulative_qty + SETTLEMENT_ACCOUNTING_COST_TOLERANCE < selected_floor:
                self._record_terminal_fill_evidence_unknown(
                    order_id,
                    fill_metadata,
                    "terminal_cumulative_quantity_below_selected_fill_evidence",
                    recorded_snapshot,
                    {
                        "terminal_quantity_field": key,
                        "terminal_quantity_value": str(cumulative_qty),
                        "selected_terminal_fill_field_source": selected.field_source,
                        "selected_terminal_fill_qty": str(selected.quantity),
                        "selected_terminal_fill_price": str(selected.price),
                        "minimum_cumulative_qty_from_selected_evidence": str(selected_floor),
                    },
                )

    def _handle_terminal_cumulative_fill(
        self,
        order_id: str,
        terminal_intent_status: str,
        terminal_event,
        fill_details: TerminalFillDetails,
        fill_metadata: Dict[str, str],
        recorded_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        if recorded_snapshot is None:
            if fill_details.price_semantics != "average_price":
                self._record_terminal_cumulative_fill_unknown(
                    order_id,
                    fill_details,
                    fill_metadata,
                    "terminal_cumulative_fill_requires_average_price",
                    recorded_snapshot,
                )
            if self._record_live_order_fill(
                order_id,
                fill_details.price,
                fill_details.quantity,
                fill_metadata=fill_metadata,
            ):
                self._track_order_event("filled")
            return "filled"

        self._enforce_terminal_fill_metadata_matches_recorded(
            order_id,
            fill_details,
            fill_metadata,
            recorded_snapshot,
        )
        recorded_qty = recorded_snapshot["filled_qty"]
        if fill_details.quantity < recorded_qty:
            self._record_terminal_cumulative_fill_unknown(
                order_id,
                fill_details,
                fill_metadata,
                "terminal_cumulative_fill_below_recorded_fill",
                recorded_snapshot,
            )
        if fill_details.quantity == recorded_qty:
            if fill_details.price_semantics != "average_price":
                self._record_terminal_cumulative_fill_unknown(
                    order_id,
                    fill_details,
                    fill_metadata,
                    "terminal_cumulative_fill_equal_requires_average_price",
                    recorded_snapshot,
                )
            terminal_notional = fill_details.quantity * fill_details.price
            recorded_notional = recorded_snapshot["filled_notional"]
            if abs(terminal_notional - recorded_notional) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE:
                self._record_terminal_cumulative_fill_unknown(
                    order_id,
                    fill_details,
                    fill_metadata,
                    "terminal_cumulative_fill_conflicts_with_recorded_accounting",
                    recorded_snapshot,
                )
            violation = self._limit_ioc_fill_envelope_violation(
                recorded_snapshot["meta"],
                fill_details.quantity,
                fill_details.price,
            )
            if violation:
                self._record_terminal_cumulative_fill_unknown(
                    order_id,
                    fill_details,
                    fill_metadata,
                    f"terminal_cumulative_fill_conflicts_with_envelope:{violation}",
                    recorded_snapshot,
                )
            self._release_submitted_position(
                order_id,
                terminal_intent_status=terminal_intent_status,
                terminal_event=terminal_event,
            )
            return "existing_fill_evidence"

        if fill_details.price_semantics != "average_price":
            self._record_terminal_cumulative_fill_unknown(
                order_id,
                fill_details,
                fill_metadata,
                "terminal_cumulative_fill_delta_requires_average_price",
                recorded_snapshot,
            )
        if recorded_snapshot["source"] != "open_live_trades":
            self._record_terminal_cumulative_fill_unknown(
                order_id,
                fill_details,
                fill_metadata,
                "terminal_cumulative_fill_delta_requires_recorded_open_trade",
                recorded_snapshot,
            )
        terminal_notional = fill_details.quantity * fill_details.price
        recorded_notional = recorded_snapshot["filled_notional"]
        delta_qty = fill_details.quantity - recorded_qty
        delta_notional = terminal_notional - recorded_notional
        if delta_qty <= 0 or delta_notional <= 0:
            self._record_terminal_cumulative_fill_unknown(
                order_id,
                fill_details,
                fill_metadata,
                "terminal_cumulative_fill_non_positive_delta",
                recorded_snapshot,
            )
        delta_price = delta_notional / delta_qty
        if not delta_price.is_finite() or delta_price <= 0 or delta_price > 1:
            self._record_terminal_cumulative_fill_unknown(
                order_id,
                fill_details,
                fill_metadata,
                "terminal_cumulative_fill_invalid_delta_price",
                recorded_snapshot,
            )
        if self._record_live_order_fill(
            order_id,
            delta_price,
            delta_qty,
            fill_metadata=fill_metadata,
        ):
            self._track_order_event("filled")
        return "filled"

    def _recorded_fill_evidence_source(self, order_id: str) -> Optional[str]:
        with self._settlement_lock:
            if order_id in self._open_live_trades:
                return "open_live_trades"

            submitted_meta = self._submitted_positions.get(order_id)
            if submitted_meta is not None and not isinstance(submitted_meta, dict):
                reason = f"submitted position for {order_id} is not a JSON object"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            if isinstance(submitted_meta, dict):
                actual_qty = submitted_meta.get("_actual_filled_qty")
                if actual_qty not in (None, "") and self._positive_recorded_fill_qty(
                    order_id,
                    "_actual_filled_qty",
                    actual_qty,
                ):
                    return "submitted_position_actual_fill_override"

            pending_actual_fill = self._pending_actual_fills.get(order_id)
            if pending_actual_fill is None:
                return None
            if not isinstance(pending_actual_fill, dict):
                reason = f"pending actual fill for {order_id} is not a JSON object"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)
            if pending_actual_fill.get("requires_external_fill_repair") is True:
                return "pending_actual_fills_external_repair"
            fills = pending_actual_fill.get("fills")
            if fills is not None:
                if not isinstance(fills, list):
                    reason = f"pending actual fill for {order_id} has non-list fills"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
                if fills:
                    try:
                        self._aggregate_actual_fill_entries(order_id, fills)
                    except SettlementLedgerError as exc:
                        reason = f"pending actual fill for {order_id} has invalid fill evidence: {exc}"
                        self._block_live_settlement_ledger(reason)
                        raise SettlementLedgerError(reason) from exc
                    return "pending_actual_fills"
            total_filled_qty = pending_actual_fill.get("total_filled_qty")
            if total_filled_qty not in (None, "") and self._positive_recorded_fill_qty(
                order_id,
                "pending actual fill total_filled_qty",
                total_filled_qty,
            ):
                return "pending_actual_fills_total_qty"
            reason = f"pending actual fill for {order_id} has no positive fill evidence"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)

    def _handle_terminal_order_event(
        self,
        event,
        terminal_intent_status: str,
        *,
        allow_no_fill: bool = True,
    ) -> str:
        if not hasattr(event, "client_order_id") or event.client_order_id in (None, ""):
            reason = f"terminal order event missing client_order_id for {terminal_intent_status}"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        order_id = str(event.client_order_id)
        recorded_snapshot = self._recorded_fill_accounting_snapshot(order_id)
        try:
            fill_metadata = self._terminal_event_fill_metadata(event)
        except SettlementLedgerError as exc:
            self._record_terminal_fill_evidence_unknown(
                order_id,
                {},
                "terminal_fill_metadata_identity_conflict",
                recorded_snapshot,
                {
                    "terminal_metadata_error": str(exc),
                    "terminal_event": self._terminal_event_audit_payload(event),
                },
            )
        try:
            fill_candidates = self._terminal_event_fill_detail_candidates(order_id, event)
            quantity_values = self._terminal_event_quantity_values(order_id, event)
            positive_quantities = self._terminal_event_positive_quantities(order_id, event)
        except SettlementLedgerError as exc:
            self._record_terminal_fill_evidence_unknown(
                order_id,
                fill_metadata,
                "terminal_fill_evidence_parse_error",
                recorded_snapshot,
                {
                    "terminal_parse_error": str(exc),
                    "terminal_event": self._terminal_event_audit_payload(event),
                },
            )
        fill_details = fill_candidates[0] if fill_candidates else None
        if fill_details is not None:
            logger.warning(
                f"Terminal order event for {order_id} carries fill details "
                f"({fill_details.field_source}); recording fill before terminal no-fill handling"
            )
            self._validate_terminal_fill_detail_candidates(
                order_id,
                fill_details,
                fill_candidates,
                fill_metadata,
                recorded_snapshot,
            )
            self._validate_terminal_quantity_consistency(
                order_id,
                fill_details,
                quantity_values,
                fill_metadata,
                recorded_snapshot,
            )
            if fill_details.quantity_semantics == "cumulative":
                return self._handle_terminal_cumulative_fill(
                    order_id,
                    terminal_intent_status,
                    event,
                    fill_details,
                    fill_metadata,
                    recorded_snapshot,
                )
            if (
                recorded_snapshot is not None
                and fill_details.price_semantics == "average_price"
            ):
                self._record_terminal_fill_evidence_unknown(
                    order_id,
                    fill_metadata,
                    "terminal_incremental_fill_uses_average_price_after_recorded_fill",
                    recorded_snapshot,
                    {
                        "terminal_fill_field_source": fill_details.field_source,
                        "terminal_fill_quantity_semantics": fill_details.quantity_semantics,
                        "terminal_fill_price_semantics": fill_details.price_semantics,
                        "fill_qty": str(fill_details.quantity),
                        "fill_price": str(fill_details.price),
                    },
                )
            if self._record_live_order_fill(
                order_id,
                fill_details.price,
                fill_details.quantity,
                fill_metadata=fill_metadata,
            ):
                self._track_order_event("filled")
            return "filled"

        if positive_quantities:
            self._record_terminal_fill_evidence_unknown(
                order_id,
                fill_metadata,
                "terminal_positive_fill_evidence_without_price",
                recorded_snapshot,
                {
                    "terminal_positive_fill_quantities": {
                        key: str(value) for key, value in positive_quantities.items()
                    },
                },
            )

        if recorded_snapshot is not None:
            self._enforce_terminal_no_fill_metadata_matches_recorded(
                order_id,
                fill_metadata,
                recorded_snapshot,
                event,
            )
            self._release_submitted_position(
                order_id,
                terminal_intent_status=terminal_intent_status,
                terminal_event=event,
            )
            return "existing_fill_evidence"

        if not allow_no_fill:
            return "unclassified"

        self._release_submitted_position(
            order_id,
            terminal_intent_status=terminal_intent_status,
            terminal_event=event,
        )
        return "no_fill"

    def _release_submitted_position(
        self,
        client_order_id,
        terminal_intent_status: Optional[str] = None,
        terminal_event=None,
    ) -> Optional[Dict[str, Any]]:
        """Release locally tracked exposure for an order that did not stay open."""
        order_id = str(client_order_id)
        with self._settlement_lock:
            meta = self._submitted_positions.get(order_id)
            has_submitted_intent = order_id in self._submitted_order_intents
        zero_fill_evidence = None
        if terminal_intent_status is not None:
            fill_evidence_source = self._recorded_fill_evidence_source(order_id)
            if fill_evidence_source is not None:
                logger.warning(
                    f"Ignoring terminal no-fill classification for {order_id}: "
                    f"recorded fill evidence exists in {fill_evidence_source}"
                )
                return meta
            zero_fill_evidence = self._verify_terminal_event_no_fill(
                order_id,
                terminal_intent_status,
                terminal_event,
            )
            if has_submitted_intent:
                saved = self._mark_submitted_order_intent_terminal_no_fill(
                    order_id=order_id,
                    status=terminal_intent_status,
                    event=terminal_event,
                    reason=str(getattr(terminal_event, "reason", "")),
                    context="Failed to persist terminal submitted order intent audit",
                    zero_fill_evidence=zero_fill_evidence,
                )
                if not saved:
                    raise SettlementLedgerError(
                        f"failed to persist terminal submitted order intent audit for {order_id}"
                    )
            elif meta:
                block_reason = f"submitted order intent missing for terminal no-fill audit: {order_id}"
                self._block_live_settlement_ledger(block_reason)
                raise SettlementLedgerError(block_reason)
        if meta:
            self._release_risk_position_without_pnl(order_id, "submitted-order risk release")
            with self._settlement_lock:
                self._submitted_positions.pop(order_id, None)
        return meta

    def _record_invalid_live_fill_unknown(
        self,
        order_id: str,
        fill_price,
        fill_qty,
        reason: str,
        fill_metadata: Dict[str, Any],
    ) -> bool:
        payload = {
            "status": "failed",
            "reason": reason,
            "fill_price": str(fill_price),
            "fill_qty": str(fill_qty),
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        payload = self._fill_failure_payload_with_identity(payload, fill_metadata)
        self._create_direct_fill_unknown_preserving_pending(order_id, payload)
        self._block_live_settlement_ledger(
            f"refused fill for {order_id}: {reason}; "
            "SETTLEMENT_UNKNOWN created for manual reconciliation"
        )
        return False

    def _fill_failure_payload_with_identity(
        self,
        payload: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        enriched = dict(payload)
        for key in FILL_METADATA_IDENTITY_KEYS:
            value = meta.get(key)
            if value not in (None, "") and key not in enriched:
                enriched[key] = value
        return enriched

    def _normalize_fill_metadata(
        self,
        order_id: str,
        fill_metadata: Dict[str, Any],
    ) -> Dict[str, str]:
        if fill_metadata is None:
            reason = f"fill metadata for {order_id} is missing"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        if not isinstance(fill_metadata, dict):
            reason = f"fill metadata for {order_id} is not a JSON object"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        normalized = {}
        for key in FILL_METADATA_IDENTITY_KEYS:
            if key not in fill_metadata:
                continue
            value = fill_metadata[key]
            if value not in (None, ""):
                normalized[key] = str(value)
        return normalized

    def _record_fill_metadata_conflict_unknown(
        self,
        order_id: str,
        fill_price,
        fill_qty,
        key: str,
        tracked_value,
        terminal_value: str,
        normalized_fill_metadata: Dict[str, str],
    ) -> None:
        reason = (
            f"fill metadata for {order_id} conflicts on {key}: "
            f"{tracked_value!r} != {terminal_value!r}"
        )
        payload = {
            "status": "failed",
            "reason": "fill_metadata_conflict",
            "fill_price": str(fill_price),
            "fill_qty": str(fill_qty),
            "fill_metadata_conflict_key": key,
            f"tracked_{key}": str(tracked_value),
            f"terminal_{key}": terminal_value,
            "venue_conflict_reason": reason,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        payload = self._fill_failure_payload_with_identity(
            payload,
            normalized_fill_metadata,
        )
        self._create_direct_fill_unknown_preserving_pending(
            order_id,
            payload,
            skip_venue_validation=True,
        )
        self._block_live_settlement_ledger(reason)
        raise SettlementLedgerError(reason)

    def _record_terminal_no_fill_metadata_conflict_unknown(
        self,
        order_id: str,
        key: str,
        tracked_value,
        terminal_value: str,
        normalized_fill_metadata: Dict[str, str],
        recorded_snapshot: Dict[str, Any],
        terminal_event,
    ) -> None:
        reason = (
            f"terminal no-fill metadata for {order_id} conflicts on {key}: "
            f"{tracked_value!r} != {terminal_value!r}"
        )
        payload = {
            "status": "failed",
            "reason": "terminal_no_fill_metadata_conflict",
            "fill_metadata_conflict_key": key,
            f"tracked_{key}": str(tracked_value),
            f"terminal_{key}": terminal_value,
            "venue_conflict_reason": reason,
            "terminal_event": self._terminal_event_audit_payload(terminal_event),
            "recorded_fill_source": recorded_snapshot["source"],
            "recorded_filled_qty": str(recorded_snapshot["filled_qty"]),
            "recorded_filled_notional": str(recorded_snapshot["filled_notional"]),
            "recorded_vwap": str(recorded_snapshot["vwap"]),
            "filled_qty": str(recorded_snapshot["filled_qty"]),
            "vwap": str(recorded_snapshot["vwap"]),
            "requires_external_fill_repair": True,
            "external_fill_repair_reason": "terminal_no_fill_metadata_conflict",
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        recorded_meta = recorded_snapshot["meta"]
        recorded_identity = {}
        for identity_key in FILL_METADATA_IDENTITY_KEYS:
            if identity_key in recorded_meta and recorded_meta[identity_key] not in (None, ""):
                identity_value = str(recorded_meta[identity_key])
                recorded_identity[identity_key] = identity_value
                payload[f"tracked_{identity_key}"] = identity_value
                if identity_key != "venue_order_id":
                    payload[identity_key] = identity_value
        if recorded_identity:
            payload["recorded_fill_identity"] = recorded_identity
        if "fills" in recorded_meta:
            payload["recorded_fill_entries"] = copy.deepcopy(recorded_meta["fills"])
        payload = self._fill_failure_payload_with_identity(
            payload,
            normalized_fill_metadata,
        )
        self._create_durable_settlement_unknown_from_actual_fill(
            client_order_id=order_id,
            payload=payload,
            reason="terminal_no_fill_metadata_conflict",
            skip_venue_validation=True,
        )
        self._block_live_settlement_ledger(reason)
        raise SettlementLedgerError(reason)

    def _enforce_terminal_no_fill_metadata_matches_recorded(
        self,
        order_id: str,
        fill_metadata: Dict[str, str],
        recorded_snapshot: Dict[str, Any],
        terminal_event,
    ) -> None:
        tracked_meta = recorded_snapshot["meta"]
        for key, value_str in fill_metadata.items():
            existing_value = tracked_meta.get(key)
            if existing_value not in (None, "") and str(existing_value) != value_str:
                self._record_terminal_no_fill_metadata_conflict_unknown(
                    order_id,
                    key,
                    existing_value,
                    value_str,
                    fill_metadata,
                    recorded_snapshot,
                    terminal_event,
                )

    def _create_direct_fill_unknown_preserving_pending(
        self,
        order_id: str,
        payload: Dict[str, Any],
        *,
        skip_venue_validation: bool = False,
    ) -> None:
        payload = dict(payload)
        reason = str(payload["reason"])
        payload["requires_external_fill_repair"] = True
        payload["external_fill_repair_reason"] = reason
        self._mark_pending_actual_fill_external_repair(order_id, payload, reason)
        self._create_durable_settlement_unknown_from_actual_fill(
            client_order_id=order_id,
            payload=payload,
            reason=reason,
            skip_venue_validation=skip_venue_validation,
        )

    def _record_live_order_fill(
        self,
        order_id: str,
        fill_price: Decimal,
        fill_qty: Decimal,
        *,
        fill_metadata: Dict[str, Any],
    ) -> bool:
        """Track cumulative live fills until final market settlement."""
        with self._settlement_lock:
            normalized_fill_metadata = self._normalize_fill_metadata(order_id, fill_metadata)
            if not normalized_fill_metadata:
                return self._record_invalid_live_fill_unknown(
                    order_id,
                    fill_price,
                    fill_qty,
                    "missing_fill_identity_metadata_from_nautilus",
                    fill_metadata={},
                )
            if self._settlement_ledger_blocked_reason:
                blocked_reason = self._settlement_ledger_blocked_reason
                pending_actual_fill = self._pending_actual_fills.get(order_id)
                if (
                    isinstance(pending_actual_fill, dict)
                    and pending_actual_fill.get("requires_external_fill_repair") is True
                ):
                    reason = (
                        f"live fill received for {order_id} while pending actual fill requires external repair: "
                        f"{blocked_reason}"
                    )
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
                payload = {
                    "status": "failed",
                    "reason": "live_fill_received_while_settlement_ledger_blocked",
                    "fill_price": str(fill_price),
                    "fill_qty": str(fill_qty),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "blocked_reason": blocked_reason,
                }
                if order_id not in self._open_live_trades:
                    payload["filled_qty"] = str(fill_qty)
                    payload["vwap"] = str(fill_price)
                payload = self._fill_failure_payload_with_identity(
                    payload,
                    normalized_fill_metadata,
                )
                self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                reason = (
                    f"live fill received while settlement ledger is blocked for {order_id}: "
                    f"{blocked_reason}; SETTLEMENT_UNKNOWN created"
                )
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason)

            try:
                fill_price = Decimal(str(fill_price))
                fill_qty = Decimal(str(fill_qty))
            except Exception:
                return self._record_invalid_live_fill_unknown(
                    order_id,
                    fill_price,
                    fill_qty,
                    "invalid_fill_price_or_qty_from_nautilus",
                    fill_metadata=normalized_fill_metadata,
                )
            if not fill_price.is_finite() or not fill_qty.is_finite():
                return self._record_invalid_live_fill_unknown(
                    order_id,
                    fill_price,
                    fill_qty,
                    "non_finite_fill_price_or_qty_from_nautilus",
                    fill_metadata=normalized_fill_metadata,
                )
            if fill_price <= Decimal("0"):
                return self._record_invalid_live_fill_unknown(
                    order_id,
                    fill_price,
                    fill_qty,
                    "non_positive_fill_price_from_nautilus",
                    fill_metadata=normalized_fill_metadata,
                )
            if fill_qty <= Decimal("0"):
                return self._record_invalid_live_fill_unknown(
                    order_id,
                    fill_price,
                    fill_qty,
                    "non_positive_fill_qty_from_nautilus",
                    fill_metadata=normalized_fill_metadata,
                )
            if fill_price > Decimal("1"):
                return self._record_invalid_live_fill_unknown(
                    order_id,
                    fill_price,
                    fill_qty,
                    "fill_price_above_one_from_nautilus",
                    fill_metadata=normalized_fill_metadata,
                )

            source_kind = "submitted"
            source_meta = self._submitted_positions.get(order_id)
            if source_meta is None:
                source_kind = "open"
                source_meta = self._open_live_trades.get(order_id)
            if source_meta is None:
                payload = {
                    "status": "failed",
                    "reason": "untracked_nautilus_fill",
                    "filled_qty": str(fill_qty),
                    "vwap": str(fill_price),
                    "fill_price": str(fill_price),
                    "fill_qty": str(fill_qty),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                payload = self._fill_failure_payload_with_identity(
                    payload,
                    normalized_fill_metadata,
                )
                self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                self._block_live_settlement_ledger(
                    f"received fill for untracked order {order_id}; "
                    "SETTLEMENT_UNKNOWN created for manual reconciliation"
                )
                return False
            meta = copy.deepcopy(source_meta)
            submitted_intent = self._submitted_order_intents.get(order_id)
            if submitted_intent is not None:
                if isinstance(submitted_intent, dict):
                    meta["submitted_order_intent"] = copy.deepcopy(submitted_intent)
                else:
                    meta["submitted_order_intent_malformed"] = True
                    meta["submitted_order_intent_raw"] = copy.deepcopy(submitted_intent)
            if normalized_fill_metadata:
                for key, value_str in normalized_fill_metadata.items():
                    existing_value = meta.get(key)
                    if existing_value not in (None, "") and str(existing_value) != value_str:
                        self._record_fill_metadata_conflict_unknown(
                            order_id,
                            fill_price,
                            fill_qty,
                            key,
                            existing_value,
                            value_str,
                            normalized_fill_metadata,
                        )
            direction_raw = str(meta.get("direction") or "").lower()
            if direction_raw not in {"long", "short"}:
                payload = {
                    "status": "failed",
                    "reason": "invalid_fill_direction_metadata",
                    "fill_price": str(fill_price),
                    "fill_qty": str(fill_qty),
                    "direction": meta.get("direction"),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                if source_kind != "open":
                    payload["filled_qty"] = str(fill_qty)
                    payload["vwap"] = str(fill_price)
                payload = self._fill_failure_payload_with_identity(
                    payload,
                    normalized_fill_metadata,
                )
                self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                self._block_live_settlement_ledger(
                    f"refused fill for {order_id}: invalid direction metadata {meta.get('direction')!r}; "
                    "SETTLEMENT_UNKNOWN created for manual reconciliation"
                )
                return False
            actual_qty = meta.pop("_actual_filled_qty", None)
            actual_px = meta.pop("_actual_fill_vwap", None)
            if (actual_qty is None) != (actual_px is None):
                payload = {
                    "status": "failed",
                    "reason": "actual_fill_override_incomplete",
                    "fill_price": str(fill_price),
                    "fill_qty": str(fill_qty),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                payload = self._fill_failure_payload_with_identity(
                    payload,
                    normalized_fill_metadata,
                )
                self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                self._block_live_settlement_ledger(
                    f"refused fill for {order_id}: incomplete actual-fill override; "
                    "SETTLEMENT_UNKNOWN created for manual reconciliation"
                )
                return False
            if actual_qty is not None and actual_px is not None:
                try:
                    fill_qty = Decimal(str(actual_qty))
                    fill_price = Decimal(str(actual_px))
                except Exception:
                    payload = {
                        "status": "failed",
                        "reason": "actual_fill_override_invalid_decimal",
                        "fill_price": str(actual_px),
                        "fill_qty": str(actual_qty),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = self._fill_failure_payload_with_identity(
                        payload,
                        normalized_fill_metadata,
                    )
                    self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                    self._block_live_settlement_ledger(
                        f"refused fill for {order_id}: actual fill override decimal parsing failed; "
                        "SETTLEMENT_UNKNOWN created for manual reconciliation"
                    )
                    return False
                if not fill_qty.is_finite() or not fill_price.is_finite():
                    payload = {
                        "status": "failed",
                        "reason": "actual_fill_override_non_finite_qty_or_vwap",
                        "fill_price": str(fill_price),
                        "fill_qty": str(fill_qty),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = self._fill_failure_payload_with_identity(
                        payload,
                        normalized_fill_metadata,
                    )
                    self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                    self._block_live_settlement_ledger(
                        f"refused fill for {order_id}: actual fill override has non-finite "
                        f"fill_price={fill_price} fill_qty={fill_qty}; "
                        "SETTLEMENT_UNKNOWN created for manual reconciliation"
                    )
                    return False
                if fill_qty <= Decimal("0") or fill_price <= Decimal("0"):
                    payload = {
                        "status": "failed",
                        "reason": "actual_fill_override_non_positive_qty_or_vwap",
                        "fill_price": str(fill_price),
                        "fill_qty": str(fill_qty),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = self._fill_failure_payload_with_identity(
                        payload,
                        normalized_fill_metadata,
                    )
                    self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                    self._block_live_settlement_ledger(
                        f"refused fill for {order_id}: actual fill override has "
                        f"fill_price={fill_price} fill_qty={fill_qty}; "
                        "SETTLEMENT_UNKNOWN created for manual reconciliation"
                    )
                    return False
                if fill_price > Decimal("1"):
                    payload = {
                        "status": "failed",
                        "reason": "actual_fill_override_vwap_above_one",
                        "fill_price": str(fill_price),
                        "fill_qty": str(fill_qty),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = self._fill_failure_payload_with_identity(
                        payload,
                        normalized_fill_metadata,
                    )
                    self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                    self._block_live_settlement_ledger(
                        f"refused fill for {order_id}: actual fill override has "
                        f"fill_price={fill_price} > 1; "
                        "SETTLEMENT_UNKNOWN created for manual reconciliation"
                    )
                    return False

            pending_actual_fill = self._pending_actual_fills.get(order_id)
            if isinstance(pending_actual_fill, dict):
                if pending_actual_fill.get("requires_external_fill_repair") is True:
                    reason = f"pending actual fill for {order_id} already requires external repair"
                    self._block_live_settlement_ledger(reason)
                    raise SettlementLedgerError(reason)
                for key in FILL_METADATA_IDENTITY_KEYS:
                    value = pending_actual_fill.get(key)
                    if value not in (None, ""):
                        meta[key] = value
                if pending_actual_fill.get("raw_callback_payload") not in (None, ""):
                    meta["raw_actual_fill_payload"] = copy.deepcopy(pending_actual_fill["raw_callback_payload"])

            if normalized_fill_metadata:
                for key, value_str in normalized_fill_metadata.items():
                    existing_value = meta.get(key)
                    if existing_value not in (None, "") and str(existing_value) != value_str:
                        self._record_fill_metadata_conflict_unknown(
                            order_id,
                            fill_price,
                            fill_qty,
                            key,
                            existing_value,
                            value_str,
                            normalized_fill_metadata,
                        )
                    meta[key] = value_str

            incoming_limit_violation = self._limit_ioc_fill_envelope_violation(
                meta,
                fill_qty,
                fill_price,
            )
            if incoming_limit_violation:
                payload = {
                    "status": "failed",
                    "reason": incoming_limit_violation,
                    "fill_price": str(fill_price),
                    "fill_qty": str(fill_qty),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                payload = self._fill_failure_payload_with_identity(payload, meta)
                self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                self._block_live_settlement_ledger(
                    f"refused fill for {order_id}: {incoming_limit_violation}; "
                    "SETTLEMENT_UNKNOWN created for manual reconciliation"
                )
                return False

            if source_kind == "open":
                try:
                    open_size, open_filled_qty, _open_entry_price = self._settlement_accounting_values(order_id, meta)
                    raw_filled_notional = meta.get("filled_notional")
                    if raw_filled_notional in (None, ""):
                        raise SettlementLedgerError(f"{order_id} missing verified filled_notional")
                    filled_notional = Decimal(str(raw_filled_notional))
                    if (
                        not filled_notional.is_finite()
                        or filled_notional <= 0
                        or abs(filled_notional - open_size) > SETTLEMENT_ACCOUNTING_COST_TOLERANCE
                    ):
                        raise SettlementLedgerError(
                            f"{order_id} has inconsistent filled_notional: {raw_filled_notional!r}"
                        )
                    previous_qty = open_filled_qty
                    previous_notional = open_size
                except Exception as exc:
                    payload = {
                        "status": "failed",
                        "reason": "invalid_open_fill_accounting",
                        "fill_price": str(fill_price),
                        "fill_qty": str(fill_qty),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = self._fill_failure_payload_with_identity(payload, meta)
                    self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                    self._block_live_settlement_ledger(
                        f"refused fill for {order_id}: invalid open fill accounting: {exc}; "
                        "SETTLEMENT_UNKNOWN created for manual reconciliation"
                    )
                    return False
            else:
                raw_previous_qty = meta.get("filled_qty")
                raw_previous_notional = meta.get("filled_notional")
                if raw_previous_qty in (None, "") and raw_previous_notional in (None, ""):
                    previous_qty = Decimal("0")
                    previous_notional = Decimal("0")
                elif raw_previous_qty in (None, "") or raw_previous_notional in (None, ""):
                    payload = {
                        "status": "failed",
                        "reason": "missing_previous_fill_accounting",
                        "fill_price": str(fill_price),
                        "fill_qty": str(fill_qty),
                        "previous_filled_qty": str(raw_previous_qty),
                        "previous_filled_notional": str(raw_previous_notional),
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = self._fill_failure_payload_with_identity(payload, meta)
                    self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                    self._block_live_settlement_ledger(
                        f"refused fill for {order_id}: missing previous fill accounting; "
                        "SETTLEMENT_UNKNOWN created for manual reconciliation"
                    )
                    return False
                else:
                    try:
                        previous_qty = Decimal(str(raw_previous_qty))
                        previous_notional = Decimal(str(raw_previous_notional))
                    except Exception:
                        payload = {
                            "status": "failed",
                            "reason": "invalid_previous_fill_accounting",
                            "fill_price": str(fill_price),
                            "fill_qty": str(fill_qty),
                            "previous_filled_qty": str(raw_previous_qty),
                            "previous_filled_notional": str(raw_previous_notional),
                            "received_at": datetime.now(timezone.utc).isoformat(),
                        }
                        payload = self._fill_failure_payload_with_identity(payload, meta)
                        self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                        self._block_live_settlement_ledger(
                            f"refused fill for {order_id}: invalid previous fill accounting; "
                            "SETTLEMENT_UNKNOWN created for manual reconciliation"
                        )
                        return False
            if (
                not previous_qty.is_finite()
                or not previous_notional.is_finite()
                or previous_qty < 0
                or previous_notional < 0
            ):
                payload = {
                    "status": "failed",
                    "reason": "impossible_previous_fill_accounting",
                    "fill_price": str(fill_price),
                    "fill_qty": str(fill_qty),
                    "previous_filled_qty": str(previous_qty),
                    "previous_filled_notional": str(previous_notional),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                payload = self._fill_failure_payload_with_identity(payload, meta)
                self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                self._block_live_settlement_ledger(
                    f"refused fill for {order_id}: impossible previous fill accounting; "
                    "SETTLEMENT_UNKNOWN created for manual reconciliation"
                )
                return False
            first_recorded_fill = previous_qty <= 0
            fill_notional = fill_price * fill_qty
            total_qty = previous_qty + fill_qty
            total_notional = previous_notional + fill_notional
            if total_qty <= 0:
                logger.warning(f"Ignoring non-positive cumulative fill quantity for {order_id}")
                return False

            average_price = total_notional / total_qty
            limit_violation = self._limit_ioc_fill_envelope_violation(
                meta,
                total_qty,
                average_price,
            )
            if limit_violation:
                payload = {
                    "status": "failed",
                    "reason": limit_violation,
                    "fill_price": str(fill_price),
                    "fill_qty": str(fill_qty),
                    "cumulative_filled_qty": str(total_qty),
                    "cumulative_vwap": str(average_price),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                payload = self._fill_failure_payload_with_identity(payload, meta)
                self._create_direct_fill_unknown_preserving_pending(order_id, payload)
                self._block_live_settlement_ledger(
                    f"refused fill for {order_id}: {limit_violation}; "
                    "SETTLEMENT_UNKNOWN created for manual reconciliation"
                )
                return False
            meta["entry_price"] = average_price
            meta["filled_qty"] = total_qty
            meta["filled_notional"] = total_notional
            meta["size"] = total_notional
            meta["filled_at"] = datetime.now(timezone.utc)
            meta["order_id"] = order_id

            open_trades = dict(self._open_live_trades)
            open_trades[order_id] = meta
            pending_actual_fills = dict(self._pending_actual_fills)
            pending_actual_fills.pop(order_id, None)
            submitted_order_intents = dict(self._submitted_order_intents)
            submitted_order_intents.pop(order_id, None)
            saved_state = self._try_save_live_trade_ledger_state(
                "Failed to persist live order fill",
                open_trades=open_trades,
                settled_trades=self._settled_live_trades,
                seen_events=self._seen_auto_redeem_events,
                seen_order=self._seen_auto_redeem_event_order,
                pending_events=self._pending_auto_redeem_events,
                pending_actual_fills=pending_actual_fills,
                submitted_order_intents=submitted_order_intents,
            )
            if saved_state is None:
                raise SettlementLedgerError(f"failed to persist live order fill for {order_id}")

            self._apply_saved_live_trade_ledger_state(saved_state)
            self._submitted_positions.pop(order_id, None)

            try:
                self.risk_engine.adjust_position(
                    position_id=order_id,
                    size=total_notional,
                    entry_price=average_price,
                    direction="buy_yes" if direction_raw == "long" else "buy_no",
                )
            except Exception as e:
                reason = f"failed to adjust risk position for fill {order_id}: {e}"
                self._block_live_settlement_ledger(reason)
                raise SettlementLedgerError(reason) from e
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
        try:
            fill_metadata = self._terminal_event_fill_metadata(event)
        except SettlementLedgerError as exc:
            payload = {
                "status": "failed",
                "reason": "fill_metadata_identity_conflict",
                "fill_price": str(fill_price),
                "fill_qty": str(fill_qty),
                "terminal_metadata_error": str(exc),
                "terminal_event": self._terminal_event_audit_payload(event),
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
            self._create_direct_fill_unknown_preserving_pending(order_id, payload)
            self._block_live_settlement_ledger(
                f"refused fill for {order_id}: fill metadata identity conflict; "
                "SETTLEMENT_UNKNOWN created for manual reconciliation"
            )
            raise
        logger.info("=" * 80)
        logger.info(f"ORDER FILLED!")
        logger.info(f"  Order: {order_id}")
        logger.info(f"  Fill Price: ${float(fill_price):.4f}")
        logger.info(f"  Quantity: {float(fill_qty):.6f}")
        logger.info("=" * 80)
        if self._record_live_order_fill(
            order_id,
            fill_price,
            fill_qty,
            fill_metadata=fill_metadata,
        ):
            self._track_order_event("filled")

    def on_order_denied(self, event):
        logger.error("=" * 80)
        logger.error(f"ORDER DENIED!")
        logger.error(f"  Order: {event.client_order_id}")
        logger.error(f"  Reason: {event.reason}")
        logger.error("=" * 80)
        terminal_result = self._handle_terminal_order_event(
            event,
            "ORDER_DENIED_NO_FILL",
        )
        if terminal_result != "no_fill":
            return
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
        if not hasattr(event, "reason"):
            terminal_result = self._handle_terminal_order_event(
                event,
                "ORDER_REJECTED_NO_FILL",
                allow_no_fill=False,
            )
            if terminal_result != "unclassified":
                return
            reason = "OrderRejected event missing reason"
            self._block_live_settlement_ledger(reason)
            raise SettlementLedgerError(reason)
        terminal_result = self._handle_terminal_order_event(
            event,
            "ORDER_REJECTED_NO_FILL",
        )
        if terminal_result != "no_fill":
            return
        reason = str(event.reason)
        reason_lower = reason.lower()
        if 'no orders found' in reason_lower or 'fak' in reason_lower or 'no match' in reason_lower:
            logger.warning(
                f"⚠ FAK rejected (no liquidity) — resetting timer to retry next tick\n"
                f"  Reason: {reason}"
            )
            self.last_trade_time = -1  # Allow retry on next quote tick
        else:
            logger.warning(f"Order rejected: {reason}")

    def on_order_canceled(self, event):
        if not hasattr(event, "client_order_id"):
            order_id_for_log = "<missing>"
        else:
            order_id_for_log = event.client_order_id
        logger.warning(f"Order canceled: {order_id_for_log}")
        self._handle_terminal_order_event(
            event,
            "ORDER_CANCELED_NO_FILL",
        )

    def on_order_expired(self, event):
        if not hasattr(event, "client_order_id"):
            order_id_for_log = "<missing>"
        else:
            order_id_for_log = event.client_order_id
        logger.warning(f"Order expired: {order_id_for_log}")
        self._handle_terminal_order_event(
            event,
            "ORDER_EXPIRED_NO_FILL",
        )

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
        unregister_error = None
        if self._actual_fill_registered:
            try:
                unregister_actual_fill_handler(self._actual_fill_handler)
            except Exception as exc:
                logger.exception("Failed to unregister Polymarket actual-fill handler")
                unregister_error = exc
            self._actual_fill_registered = False
        if self._auto_redeem_registered:
            try:
                unregister_auto_redeem_handler(self._auto_redeem_handler)
            except Exception as exc:
                logger.exception("Failed to unregister Polymarket auto-redeem handler")
                if unregister_error is None:
                    unregister_error = exc
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
        if unregister_error is not None:
            raise SettlementLedgerError("failed to unregister Polymarket handler") from unregister_error

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def ensure_live_market_order_patch() -> None:
    """Apply/verify the market-order adapter patch before live execution starts."""
    global patch_applied
    if patch_applied:
        return
    patch_applied = apply_market_order_patch()
    if not patch_applied:
        raise RuntimeError("Live mode requires market order patch to be applied")
    logger.info("Market order patch applied successfully")


def run_integrated_bot(simulation: bool = True, enable_grafana: bool = True, test_mode: bool = False):
    """Run the integrated BTC 15-min trading bot - LOADS ALL BTC MARKETS FOR THE DAY"""
    
    print("=" * 80)
    print("INTEGRATED POLYMARKET BTC 15-MIN TRADING BOT")
    print("Nautilus + 7-Phase System + Redis Control")
    print("=" * 80)

    order_config = validate_live_order_config()

    if not simulation:
        ensure_live_market_order_patch()
        if not v2_patch_applied:
            raise RuntimeError("Live mode requires Polymarket CLOB v2 compatibility patch")
        if not quote_warning_patch_applied:
            raise RuntimeError("Live mode requires Polymarket quote-warning filter patch")

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
    # Print the validated MARKET_BUY_USD. In live mode the Phase 0.3 gate has
    # already raised before reaching here if the env value is missing/invalid,
    # so get_market_buy_usd() returns a real value. In simulation the existing
    # legacy default applies (tracked in the AGENTS.md/CLAUDE.md no-fallback
    # audit as a pre-existing item, not introduced by Phase 0.3).
    print(f"  Max Trade Size: ${get_market_buy_usd()}")
    print(f"  Order Type: {order_config['order_type']}")
    print(
        "  Quote stability gate: "
        f"{order_config['quote_stability_required']} valid ticks"
    )
    if order_config["limit_ioc_fill_policy"] is not None:
        print(f"  LIMIT_IOC fill policy: {order_config['limit_ioc_fill_policy']}")
        print(f"  LIMIT_REQUIRED_EDGE: {order_config['limit_required_edge']}")
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
    # Phase 0.5a / 1.227.0 migration: Polymarket config field was renamed
    # `instrument_provider` -> `instrument_config` in nautilus_trader 1.227.0.
    poly_data_cfg = PolymarketDataClientConfig(
        private_key=polymarket_private_key,
        api_key=polymarket_api_key,
        api_secret=polymarket_api_secret,
        passphrase=polymarket_passphrase,
        signature_type=polymarket_signature_type,
        funder=polymarket_funder,
        instrument_config=instrument_cfg,
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
            instrument_config=instrument_cfg,
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
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help=(
            "Skip the interactive 'LIVE' confirmation prompt for --live. "
            "Only valid alongside --live; no env var, config file, or default "
            "replaces this flag."
        ),
    )
    parser.add_argument("--no-grafana", action="store_true", help="Disable Grafana metrics")
    args = parser.parse_args(argv)
    if args.confirm_live and not args.live:
        parser.error("--confirm-live is only valid alongside --live")
    return args


def _prompt_for_live_confirmation() -> None:
    """Require the operator to type 'LIVE' to acknowledge --live startup.

    Phase 0.3 live startup gate: --live alone requires the operator to type
    exactly the literal string 'LIVE' (case sensitive, no whitespace tolerance).
    --live --confirm-live skips this prompt with a logged audit line instead.

    EOFError on non-interactive stdin (piped, daemonized via systemd without
    a TTY, Docker without -it, etc.) is treated as "operator did not confirm"
    and aborts startup with the same SystemExit shape as a wrong literal. The
    operator must pass --confirm-live for unattended startup.
    """
    print("=" * 80)
    print("LIVE TRADING MODE — REAL MONEY AT RISK")
    print("Type LIVE (in uppercase) to confirm, anything else to abort.")
    print("=" * 80)
    try:
        confirm = input("Confirm live startup: ")
    except EOFError:
        raise SystemExit(
            "Live startup cancelled: stdin is not a TTY; use --confirm-live "
            "for unattended (systemd/cron/Docker) startup"
        )
    if confirm != "LIVE":
        raise SystemExit("Live startup cancelled: operator did not type LIVE")


def main():
    args = parse_runtime_args()
    enable_grafana = not args.no_grafana
    test_mode = args.test_mode

    simulation = not args.live

    if not simulation:
        # Phase 0.3 gate (1): MARKET_BUY_USD > 5.50 strict before any node startup
        enforce_live_market_buy_usd_gate()
        validate_live_order_config()

        # Phase 0.3 gate (2): interactive LIVE confirmation unless --confirm-live
        if args.confirm_live:
            logger.warning(
                "Live confirmation provided by explicit --confirm-live CLI flag; "
                "skipping interactive LIVE prompt"
            )
        else:
            _prompt_for_live_confirmation()

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
