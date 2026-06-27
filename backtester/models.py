"""
backtester/models.py
--------------------
Core data models used throughout the engine.
Using dataclasses for clean, typed, lightweight objects.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED_EOD = "CLOSED_EOD"
    CLOSED_STOPLOSS = "CLOSED_STOPLOSS"
    CLOSED_TARGET = "CLOSED_TARGET"


@dataclass
class OptionContract:
    """Represents a single options contract at a point in time."""
    symbol: str
    expiry: datetime
    strike: float
    option_type: OptionType
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    ltp: float              # Last traded price (used for execution)
    iv: Optional[float] = None
    oi: Optional[float] = None
    volume: Optional[float] = None
    underlying_price: float = 0.0


@dataclass
class Leg:
    """
    One leg of a trade: e.g. SELL 1 lot NIFTY 18000 CE.
    qty > 0 = BUY, qty < 0 = SELL (short).
    """
    contract: OptionContract
    action: TradeAction
    qty: int                        # number of LOTS (positive integer)
    lot_size: int
    entry_price: float = 0.0        # filled after execution
    exit_price: float = 0.0
    slippage_applied: float = 0.0   # ₹ slippage cost
    brokerage: float = 0.0

    @property
    def net_qty(self) -> int:
        """Signed quantity in units (lots × lot_size). Negative = short."""
        sign = -1 if self.action == TradeAction.SELL else 1
        return sign * self.qty * self.lot_size

    @property
    def entry_value(self) -> float:
        return self.entry_price * abs(self.net_qty)

    @property
    def exit_value(self) -> float:
        return self.exit_price * abs(self.net_qty)

    @property
    def realised_pnl(self) -> float:
        """
        P&L from this leg (before brokerage/slippage).
        SELL leg profits when exit_price < entry_price.
        """
        if self.action == TradeAction.SELL:
            return (self.entry_price - self.exit_price) * self.qty * self.lot_size
        else:
            return (self.exit_price - self.entry_price) * self.qty * self.lot_size

    @property
    def net_pnl(self) -> float:
        return self.realised_pnl - self.brokerage - self.slippage_applied


@dataclass
class Trade:
    """
    A complete trade = one or more legs that are entered and exited together.
    e.g. a straddle = 2 legs (CE sell + PE sell).
    """
    trade_id: str
    strategy_name: str
    symbol: str
    expiry: datetime
    entry_time: datetime
    legs: List[Leg] = field(default_factory=list)
    exit_time: Optional[datetime] = None
    status: TradeStatus = TradeStatus.OPEN
    notes: str = ""

    @property
    def total_premium_received(self) -> float:
        """Premium collected on SELL legs at entry."""
        return sum(
            leg.entry_price * leg.qty * leg.lot_size
            for leg in self.legs
            if leg.action == TradeAction.SELL
        )

    @property
    def total_premium_paid_back(self) -> float:
        """Cost to close/buy-back SELL legs at exit."""
        return sum(
            leg.exit_price * leg.qty * leg.lot_size
            for leg in self.legs
            if leg.action == TradeAction.SELL
        )

    @property
    def gross_pnl(self) -> float:
        return sum(leg.realised_pnl for leg in self.legs)

    @property
    def total_costs(self) -> float:
        return sum(leg.brokerage + leg.slippage_applied for leg in self.legs)

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.total_costs

    def to_dict(self) -> dict:
        """Flat dict for CSV export."""
        return {
            "trade_id": self.trade_id,
            "strategy": self.strategy_name,
            "symbol": self.symbol,
            "expiry": self.expiry.strftime("%Y-%m-%d"),
            "entry_time": self.entry_time.strftime("%Y-%m-%d %H:%M"),
            "exit_time": self.exit_time.strftime("%Y-%m-%d %H:%M") if self.exit_time else "",
            "status": self.status.value,
            "legs_count": len(self.legs),
            "premium_received": round(self.total_premium_received, 2),
            "premium_paid_back": round(self.total_premium_paid_back, 2),
            "gross_pnl": round(self.gross_pnl, 2),
            "total_costs": round(self.total_costs, 2),
            "net_pnl": round(self.net_pnl, 2),
            "notes": self.notes,
        }


@dataclass
class MarketSnapshot:
    """
    All market data available at a single timestamp for one expiry.
    Used by strategies to make entry/exit decisions.
    """
    timestamp: datetime
    expiry: datetime
    underlying_price: float
    contracts: List[OptionContract] = field(default_factory=list)

    def get_contract(self, strike: float, option_type: OptionType) -> Optional[OptionContract]:
        for c in self.contracts:
            if c.strike == strike and c.option_type == option_type:
                return c
        return None

    def get_atm_strike(self) -> float:
        """Returns the strike closest to current underlying price."""
        strikes = sorted(set(c.strike for c in self.contracts))
        if not strikes:
            return 0.0
        return min(strikes, key=lambda s: abs(s - self.underlying_price))