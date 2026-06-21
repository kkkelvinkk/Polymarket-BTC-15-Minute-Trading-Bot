"""
Execution Engine
Manages order placement, fills, and position lifecycle
"""
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass
from enum import Enum
from loguru import logger

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from execution.risk_engine import get_risk_engine, RiskEngine
from core.strategy_brain.signal_processors.base_processor import SignalDirection


class OrderType(Enum):
    """Order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderSide(Enum):
    """Order side."""
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    """Trading order."""
    order_id: str
    timestamp: datetime
    order_type: OrderType
    side: OrderSide
    size: Decimal  # USD amount
    price: Optional[Decimal]  # None for market orders
    status: OrderStatus
    
    # Position management
    position_id: Optional[str] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    
    # Execution details
    filled_size: Decimal = Decimal("0")
    filled_price: Optional[Decimal] = None
    fills: List[Dict[str, Any]] = None
    
    # Metadata
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.fills is None:
            self.fills = []
        if self.metadata is None:
            self.metadata = {}


class ExecutionEngine:
    """
    Execution engine that manages order lifecycle.
    
    Workflow:
    1. Receive trading signal from strategy
    2. Check risk limits
    3. Calculate position size
    4. Place order
    5. Monitor fills
    6. Manage position
    7. Handle exits (stop loss, take profit)
    """
    
    def __init__(
        self,
        risk_engine: Optional[RiskEngine] = None,
        dry_run: bool = True,  # Simulate orders without real execution
        *,
        now: datetime,
    ):
        """
        Initialize execution engine.

        Review-cycle fix (R2 round 2): ``now`` is a REQUIRED kwarg (M11)
        because the singleton fallback path constructs the risk engine
        which itself requires ``now=``. The prior ``or`` truthiness
        fallback at ``risk_engine = risk_engine or get_risk_engine()``
        is replaced by an explicit ``is None`` branch.
        """
        if not dry_run:
            raise RuntimeError(
                "Legacy ExecutionEngine live mode is disabled; use bot.py live order pipeline"
            )
        if risk_engine is None:
            risk_engine = get_risk_engine(now=now)
        self.risk_engine = risk_engine
        # Stored so async paths can pass a fresh UTC ``now=`` to risk methods
        # that require it (the dry-run path mutates risk state).
        self._startup_now = now
        self.dry_run = dry_run
        
        # Order tracking
        self._orders: Dict[str, Order] = {}
        self._order_counter = 0
        
        # Position tracking
        self._positions: Dict[str, Dict[str, Any]] = {}
        
        # Callbacks
        self.on_order_filled: Optional[Callable] = None
        self.on_position_opened: Optional[Callable] = None
        self.on_position_closed: Optional[Callable] = None
        
        # Statistics
        self._total_orders = 0
        self._filled_orders = 0
        self._rejected_orders = 0
        
        mode = "DRY RUN" if dry_run else "LIVE"
        logger.info(f"Initialized Execution Engine [{mode}]")
    
    async def execute_signal(
        self,
        signal_direction: SignalDirection,
        signal_confidence: float,
        signal_score: float,
        current_price: Decimal,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> Optional[Order]:
        """
        Execute trading signal.
        
        Args:
            signal_direction: Signal direction (BULLISH/BEARISH)
            signal_confidence: Confidence (0.0-1.0)
            signal_score: Score (0-100)
            current_price: Current market price
            stop_loss: Stop loss price
            take_profit: Take profit price
            
        Returns:
            Order if created, None if rejected
        """
        logger.info("=" * 60)
        logger.info("EXECUTING TRADING SIGNAL")
        logger.info("=" * 60)
        logger.info(f"Direction: {signal_direction.value}")
        logger.info(f"Confidence: {signal_confidence:.2%}")
        logger.info(f"Score: {signal_score:.1f}")
        logger.info(f"Price: ${current_price:,.2f}")
        
        # Calculate position size
        position_size = self.risk_engine.calculate_position_size(
            signal_confidence=signal_confidence,
            signal_score=signal_score,
            current_price=current_price,
        )
        
        logger.info(f"Calculated position size: ${position_size:.2f}")
        
        # Determine order side
        if signal_direction == SignalDirection.BULLISH:
            side = OrderSide.BUY
            direction = "long"
        elif signal_direction == SignalDirection.BEARISH:
            side = OrderSide.SELL
            direction = "short"
        else:
            logger.warning("Neutral signal - no trade")
            return None
        
        # Validate with risk engine — Beta-8 requires UTC ``now=``.
        is_valid, error = self.risk_engine.validate_new_position(
            size=position_size,
            direction=direction,
            current_price=current_price,
            now=datetime.now(timezone.utc),
        )
        
        if not is_valid:
            logger.error(f"Position rejected by risk engine: {error}")
            self._rejected_orders += 1
            return None
        
        # Create order
        order = await self.place_market_order(
            side=side,
            size=position_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "signal_direction": signal_direction.value,
                "signal_confidence": signal_confidence,
                "signal_score": signal_score,
            }
        )
        
        if order:
            logger.info(f"Order placed: {order.order_id}")
            
            # Simulate fill in dry run mode
            if self.dry_run:
                await self._simulate_fill(order, current_price)
        
        return order
    
    async def place_market_order(
        self,
        side: OrderSide,
        size: Decimal,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[Order]:
        if not self.dry_run:
            raise RuntimeError(
                "Legacy ExecutionEngine live mode is disabled; use bot.py live order pipeline"
            )

        self._order_counter += 1
        order_id = f"order_{self._order_counter}_{datetime.now(timezone.utc).timestamp()}"
        order = Order(
            order_id=order_id,
            timestamp=datetime.now(timezone.utc),
            order_type=OrderType.MARKET,
            side=side,
            size=size,
            price=None,  # Market order has no limit price
            status=OrderStatus.PENDING,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata or {},
        )
        self._orders[order_id] = order
        self._total_orders += 1
        
        logger.info(
            f"Created market order: {order_id} "
            f"{side.value.upper()} ${size:.2f}"
        )
        
        order.status = OrderStatus.SUBMITTED
        
        return order
    
    async def _simulate_fill(
        self,
        order: Order,
        fill_price: Decimal,
    ) -> None:
        logger.info(f"[SIMULATED] Filling order {order.order_id} @ ${fill_price:.2f}")
        order.status = OrderStatus.FILLED
        order.filled_size = order.size
        order.filled_price = fill_price
        order.fills.append({
            "timestamp": datetime.now(timezone.utc),
            "price": fill_price,
            "size": order.size,
        })
        
        self._filled_orders += 1
        
        # Create position
        await self._create_position(order, fill_price)
        
        # Callback
        if self.on_order_filled:
            await self.on_order_filled(order)
    
    async def _create_position(
        self,
        order: Order,
        entry_price: Decimal,
    ) -> None:
        """
        Create position from filled order.
        
        Args:
            order: Filled order
            entry_price: Entry price
        """
        # Generate position ID
        position_id = f"pos_{datetime.now(timezone.utc).timestamp()}"
        
        # Determine direction
        direction = "long" if order.side == OrderSide.BUY else "short"
        
        # Create position record
        position = {
            "position_id": position_id,
            "order_id": order.order_id,
            "direction": direction,
            "entry_price": entry_price,
            "size": order.filled_size,
            "entry_time": datetime.now(timezone.utc),
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "status": "open",
            "metadata": order.metadata,
        }
        
        # Store position
        self._positions[position_id] = position
        order.position_id = position_id
        
        # Add to risk engine — Beta-8 requires UTC ``now=``.
        self.risk_engine.add_position(
            position_id=position_id,
            size=order.filled_size,
            entry_price=entry_price,
            direction=direction,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            now=datetime.now(timezone.utc),
        )
        
        logger.info(
            f"Position opened: {position_id} "
            f"{direction.upper()} ${order.filled_size:.2f} @ ${entry_price:.2f}"
        )
        
        # Callback
        if self.on_position_opened:
            await self.on_position_opened(position)
    
    async def close_position(
        self,
        position_id: str,
        exit_price: Decimal,
        reason: str = "manual",
    ) -> Optional[Decimal]:
        """
        Close a position.
        
        Args:
            position_id: Position ID
            exit_price: Exit price
            reason: Reason for closing
            
        Returns:
            Realized P&L or None
        """
        if position_id not in self._positions:
            logger.error(f"Position not found: {position_id}")
            return None
        
        position = self._positions[position_id]
        
        # Create closing order
        side = OrderSide.SELL if position["direction"] == "long" else OrderSide.BUY
        
        close_order = await self.place_market_order(
            side=side,
            size=position["size"],
            metadata={
                "position_id": position_id,
                "close_reason": reason,
            }
        )
        
        if not close_order:
            return None
        
        # Simulate fill
        if self.dry_run:
            close_order.status = OrderStatus.FILLED
            close_order.filled_size = position["size"]
            close_order.filled_price = exit_price
        
        # Calculate P&L — Beta-8 requires UTC ``now=``.
        _close_now = datetime.now(timezone.utc)
        pnl = self.risk_engine.remove_position(position_id, exit_price, now=_close_now)

        # Update position
        position["status"] = "closed"
        position["exit_price"] = exit_price
        position["exit_time"] = _close_now
        position["pnl"] = pnl
        position["close_reason"] = reason
        
        logger.info(
            f"Position closed: {position_id} "
            f"P&L: ${pnl:+.2f} ({reason})"
        )
        
        # Callback
        if self.on_position_closed:
            await self.on_position_closed(position)
        
        return pnl
    
    # Beta-8: update_positions deleted. Its sole production caller was a
    # deleted test method; the only consumer of get_statistics
    # (monitoring/grafana_exporter) does not transit this path. Risk-engine
    # ``update_position``, ``_assess_risk_level``, ``_check_stop_loss``,
    # ``_check_take_profit`` were all removed in the same Beta-8 diff.

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self._orders.get(order_id)
    
    def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        """Get position by ID."""
        return self._positions.get(position_id)
    
    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions."""
        return [
            pos for pos in self._positions.values()
            if pos["status"] == "open"
        ]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get execution statistics."""
        return {
            "mode": "dry_run" if self.dry_run else "live",
            "orders": {
                "total": self._total_orders,
                "filled": self._filled_orders,
                "rejected": self._rejected_orders,
                "pending": len([o for o in self._orders.values() if o.status == OrderStatus.PENDING]),
            },
            "positions": {
                "open": len(self.get_open_positions()),
                "total": len(self._positions),
            },
            "risk": self.risk_engine.get_risk_summary(),
        }


# Singleton instance
_execution_engine_instance = None

def get_execution_engine(*, now: datetime) -> ExecutionEngine:
    """Get singleton execution engine.

    Review-cycle fix (R2 round 2): ``now`` REQUIRED (M11) for Beta-8
    risk engine propagation.
    """
    global _execution_engine_instance
    if _execution_engine_instance is None:
        _execution_engine_instance = ExecutionEngine(dry_run=True, now=now)
    return _execution_engine_instance
