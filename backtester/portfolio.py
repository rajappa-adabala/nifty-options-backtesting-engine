"""
backtester/portfolio.py
-----------------------
Tracks open positions, applies costs (slippage + brokerage),
and computes running P&L across all trades.
"""

import logging
from typing import List, Optional

import config
from backtester.models import Trade, Leg, TradeAction, TradeStatus, MarketSnapshot

logger = logging.getLogger(__name__)


def apply_slippage(price: float, action: TradeAction, slippage_pct: float) -> tuple[float, float]:
    """
    Apply slippage to an execution price.
    BUY  → pays more (price goes up by slippage_pct).
    SELL → receives less (price goes down by slippage_pct).
    Returns (adjusted_price, slippage_cost_per_unit).
    """
    slip = price * (slippage_pct / 100)
    if action == TradeAction.BUY:
        return price + slip, slip
    else:
        return price - slip, slip


def apply_costs(leg: Leg) -> Leg:
    """
    Fills slippage_applied and brokerage on a Leg (in-place) and returns it.
    Called once at entry and once at exit.
    """
    slip_per_unit = leg.entry_price * (config.SLIPPAGE_PCT / 100)
    leg.slippage_applied += slip_per_unit * leg.qty * leg.lot_size
    leg.brokerage += config.BROKERAGE_PER_ORDER
    return leg


class Portfolio:
    """
    Tracks all trades (open and closed) and provides aggregate statistics.
    This is a simple simulation portfolio — no margin/capital constraints.
    """

    def __init__(self):
        self.open_trades: List[Trade] = []
        self.closed_trades: List[Trade] = []

    def add_trade(self, trade: Trade) -> None:
        """Register a new trade (already has entry prices filled)."""
        for leg in trade.legs:
            apply_costs(leg)
        self.open_trades.append(trade)
        logger.info(
            f"[ENTRY] {trade.trade_id} | {trade.symbol} | Expiry: {trade.expiry.date()} | "
            f"Premium received: ₹{trade.total_premium_received:.0f}"
        )

    def close_trade(self, trade: Trade, snapshot: MarketSnapshot, status: TradeStatus) -> None:
        """
        Fill exit prices from snapshot, apply exit-side costs, mark closed.
        """
        trade.exit_time = snapshot.timestamp
        trade.status = status

        for leg in trade.legs:
            contract = snapshot.get_contract(leg.contract.strike, leg.contract.option_type)
            if contract:
                raw_exit = contract.ltp
            else:
                # If contract has expired worthless, exit at 0.05 minimum
                raw_exit = 0.05
                logger.debug(
                    f"Contract {leg.contract.strike} {leg.contract.option_type} not found "
                    f"in snapshot — using 0.05 (expired worthless)"
                )

            # For exit: flip the action (we're closing the position)
            exit_action = TradeAction.BUY if leg.action == TradeAction.SELL else TradeAction.SELL
            adjusted_exit, slip = apply_slippage(raw_exit, exit_action, config.SLIPPAGE_PCT)
            leg.exit_price = max(adjusted_exit, 0.05)  # floor at 0.05
            leg.slippage_applied += slip * leg.qty * leg.lot_size
            leg.brokerage += config.BROKERAGE_PER_ORDER  # exit-side brokerage

        self.open_trades.remove(trade)
        self.closed_trades.append(trade)

        logger.info(
            f"[EXIT]  {trade.trade_id} | Status: {status.value} | "
            f"Net P&L: ₹{trade.net_pnl:.0f}"
        )

    def check_stoploss(self, snapshot: MarketSnapshot) -> List[Trade]:
        """
        Check all open trades for stop-loss breach.
        Returns list of trades that hit SL.
        """
        if config.STOPLOSS_PCT is None:
            return []

        sl_hit = []
        for trade in list(self.open_trades):
            current_combined = self._current_combined_premium(trade, snapshot)
            sl_level = trade.total_premium_received * (1 + config.STOPLOSS_PCT / 100)
            if current_combined >= sl_level:
                logger.warning(
                    f"SL HIT: {trade.trade_id} | Premium received: ₹{trade.total_premium_received:.0f} "
                    f"| Current: ₹{current_combined:.0f} | SL level: ₹{sl_level:.0f}"
                )
                sl_hit.append(trade)
        return sl_hit

    def _current_combined_premium(self, trade: Trade, snapshot: MarketSnapshot) -> float:
        """Current cost to close all SELL legs of a trade."""
        total = 0.0
        for leg in trade.legs:
            if leg.action == TradeAction.SELL:
                contract = snapshot.get_contract(leg.contract.strike, leg.contract.option_type)
                ltp = contract.ltp if contract else 0.05
                total += ltp * leg.qty * leg.lot_size
        return total

    # ── Statistics ────────────────────────────────────────────────────────────

    @property
    def total_net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.closed_trades)

    @property
    def total_gross_pnl(self) -> float:
        return sum(t.gross_pnl for t in self.closed_trades)

    @property
    def total_costs(self) -> float:
        return sum(t.total_costs for t in self.closed_trades)

    @property
    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.net_pnl > 0)
        return wins / len(self.closed_trades) * 100

    @property
    def max_drawdown(self) -> float:
        """Max peak-to-trough drawdown across closed trades."""
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for trade in self.closed_trades:
            running += trade.net_pnl
            peak = max(peak, running)
            max_dd = max(max_dd, peak - running)
        return max_dd

    def summary(self) -> dict:
        closed = self.closed_trades
        if not closed:
            return {}
        pnls = [t.net_pnl for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(self.win_rate, 1),
            "gross_pnl": round(self.total_gross_pnl, 2),
            "total_costs": round(self.total_costs, 2),
            "net_pnl": round(self.total_net_pnl, 2),
            "avg_pnl_per_trade": round(self.total_net_pnl / len(closed), 2),
            "best_trade": round(max(pnls), 2),
            "worst_trade": round(min(pnls), 2),
            "max_drawdown": round(self.max_drawdown, 2),
        }