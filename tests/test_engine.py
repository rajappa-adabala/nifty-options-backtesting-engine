"""
tests/test_engine.py
---------------------
Unit tests for core engine components.
Run with: pytest tests/ -v
"""

import sys
import os
import math
from datetime import datetime



sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtester.models import (
    OptionContract, OptionType, TradeAction, TradeStatus,
    Leg, Trade, MarketSnapshot,
)
from backtester.portfolio import Portfolio, apply_slippage
from utils.options_math import price, delta, gamma, theta, vega, greeks_summary
from utils.nse_utils import (
    get_atm_strike, round_to_strike, get_monthly_expiries,
    get_weekly_expiries, get_expiries_in_range, time_to_expiry_years,
)
from strategies.atm_straddle import ATMStraddleStrategy


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_contract(strike: float, opt_type: OptionType, ltp: float) -> OptionContract:
    return OptionContract(
        symbol="NIFTY",
        expiry=datetime(2023, 1, 26),
        strike=strike,
        option_type=opt_type,
        timestamp=datetime(2023, 1, 26, 9, 15),
        open=ltp, high=ltp * 1.1, low=ltp * 0.9, close=ltp,
        ltp=ltp,
        underlying_price=18500.0,
    )


def make_snapshot(spot: float = 18500.0, expiry: datetime = None) -> MarketSnapshot:
    expiry = expiry or datetime(2023, 1, 26)
    strikes = [18300, 18350, 18400, 18450, 18500, 18550, 18600, 18650, 18700]
    contracts = []
    for s in strikes:
        contracts.append(make_contract(s, OptionType.CE, max(18500 - s + 100, 5.0)))
        contracts.append(make_contract(s, OptionType.PE, max(s - 18500 + 100, 5.0)))
    return MarketSnapshot(
        timestamp=datetime(2023, 1, 26, 9, 15),
        expiry=expiry,
        underlying_price=spot,
        contracts=contracts,
    )


# ── Black-Scholes Greeks tests ─────────────────────────────────────────────────

class TestBlackScholes:
    """Tests for options_math.py"""

    def test_ce_price_positive(self):
        p = price(18500, 18500, 7/365, 0.065, 0.18, "CE")
        assert p > 0

    def test_pe_price_positive(self):
        p = price(18500, 18500, 7/365, 0.065, 0.18, "PE")
        assert p > 0

    def test_put_call_parity(self):
        """C - P = S - K*exp(-rT) (put-call parity)"""
        S, K, T, r, sigma = 18500, 18500, 30/365, 0.065, 0.18
        c = price(S, K, T, r, sigma, "CE")
        p = price(S, K, T, r, sigma, "PE")
        lhs = c - p
        rhs = S - K * math.exp(-r * T)
        assert abs(lhs - rhs) < 1.0, f"Put-call parity violated: {lhs:.2f} != {rhs:.2f}"

    def test_atm_delta_ce_near_half(self):
        # With non-zero risk-free rate, ATM CE delta is slightly above 0.5
        # (forward price > spot). Bounds widened to [0.45, 0.60].
        d = delta(18500, 18500, 30/365, 0.065, 0.18, "CE")
        assert 0.45 < d < 0.60, f"ATM CE delta should be ~0.5, got {d}"

    def test_atm_delta_pe_near_neg_half(self):
        # Symmetric: ATM PE delta slightly above -0.5 in magnitude
        d = delta(18500, 18500, 30/365, 0.065, 0.18, "PE")
        assert -0.60 < d < -0.40, f"ATM PE delta should be ~-0.5, got {d}"

    def test_delta_ce_range(self):
        d = delta(18500, 18500, 30/365, 0.065, 0.18, "CE")
        assert 0.0 <= d <= 1.0

    def test_delta_pe_range(self):
        d = delta(18500, 18500, 30/365, 0.065, 0.18, "PE")
        assert -1.0 <= d <= 0.0

    def test_gamma_positive(self):
        g = gamma(18500, 18500, 30/365, 0.065, 0.18)
        assert g > 0

    def test_theta_negative_long(self):
        """Theta should be negative (long position loses value over time)."""
        th = theta(18500, 18500, 30/365, 0.065, 0.18, "CE")
        assert th < 0, f"Theta should be negative for long CE, got {th}"

    def test_vega_positive(self):
        v = vega(18500, 18500, 30/365, 0.065, 0.18)
        assert v > 0

    def test_greeks_summary_keys(self):
        g = greeks_summary(18500, 18500, 7/365, 0.065, 0.18, "CE")
        assert set(g.keys()) == {"price", "delta", "gamma", "theta", "vega"}

    def test_expiry_intrinsic_value_ce(self):
        """At T=0, CE price = max(S-K, 0)"""
        p_itm = price(18600, 18500, 0, 0.065, 0.18, "CE")
        assert abs(p_itm - 100.0) < 0.01

        p_otm = price(18400, 18500, 0, 0.065, 0.18, "CE")
        assert abs(p_otm - 0.0) < 0.01

    def test_higher_iv_higher_price(self):
        """Higher IV should give higher option price."""
        low_iv = price(18500, 18500, 30/365, 0.065, 0.12, "CE")
        high_iv = price(18500, 18500, 30/365, 0.065, 0.25, "CE")
        assert high_iv > low_iv


# ── NSE Utils tests ────────────────────────────────────────────────────────────

class TestNSEUtils:
    def test_round_to_strike_nifty(self):
        assert round_to_strike(18523, 50) == 18500
        assert round_to_strike(18526, 50) == 18550

    def test_get_atm_strike(self):
        atm = get_atm_strike(18523, "NIFTY")
        assert atm == 18500

    def test_monthly_expiries_count(self):
        monthly = get_monthly_expiries(2023)
        assert len(monthly) == 12

    def test_monthly_expiries_are_thursdays(self):
        for exp in get_monthly_expiries(2023):
            assert exp.weekday() == 3, f"{exp} is not a Thursday"

    def test_weekly_expiries_are_thursdays(self):
        for exp in get_weekly_expiries(2023):
            assert exp.weekday() == 3

    def test_expiries_in_range(self):
        start = datetime(2023, 1, 1)
        end = datetime(2023, 3, 31)
        expiries = get_expiries_in_range(start, end, "weekly", "NIFTY")
        assert len(expiries) > 10
        for e in expiries:
            assert start <= e <= end

    def test_time_to_expiry_positive(self):
        current = datetime(2023, 1, 23, 10, 0)
        expiry = datetime(2023, 1, 26, 15, 30)
        T = time_to_expiry_years(current, expiry)
        assert T > 0

    def test_time_to_expiry_minimum(self):
        """After expiry, T should be clamped to minimum, not negative."""
        T = time_to_expiry_years(datetime(2023, 1, 27), datetime(2023, 1, 26))
        assert T >= 0.0001


# ── Models tests ───────────────────────────────────────────────────────────────

class TestModels:
    def test_leg_sell_pnl(self):
        """Selling and buying back cheaper → positive P&L."""
        contract = make_contract(18500, OptionType.CE, 100.0)
        leg = Leg(
            contract=contract,
            action=TradeAction.SELL,
            qty=1, lot_size=50,
            entry_price=100.0,
            exit_price=50.0,
        )
        assert leg.realised_pnl == 50.0 * 50  # 2500

    def test_leg_buy_pnl(self):
        """Buying and selling higher → positive P&L."""
        contract = make_contract(18500, OptionType.CE, 100.0)
        leg = Leg(
            contract=contract,
            action=TradeAction.BUY,
            qty=1, lot_size=50,
            entry_price=100.0,
            exit_price=150.0,
        )
        assert leg.realised_pnl == 50.0 * 50

    def test_snapshot_get_atm(self):
        snap = make_snapshot(18523.0)
        atm = snap.get_atm_strike()
        assert atm == 18500.0

    def test_snapshot_get_contract(self):
        snap = make_snapshot()
        c = snap.get_contract(18500, OptionType.CE)
        assert c is not None
        assert c.strike == 18500

    def test_snapshot_missing_contract(self):
        snap = make_snapshot()
        c = snap.get_contract(99999, OptionType.CE)
        assert c is None


# ── Portfolio tests ────────────────────────────────────────────────────────────

class TestPortfolio:
    def _make_trade(self) -> Trade:
        """Helper: returns a simple 1-leg SELL trade."""
        contract = make_contract(18500, OptionType.CE, 100.0)
        leg = Leg(
            contract=contract,
            action=TradeAction.SELL,
            qty=1, lot_size=50,
            entry_price=100.0,
        )
        return Trade(
            trade_id="TEST01",
            strategy_name="test",
            symbol="NIFTY",
            expiry=datetime(2023, 1, 26),
            entry_time=datetime(2023, 1, 26, 9, 15),
            legs=[leg],
        )

    def test_add_trade(self):
        port = Portfolio()
        trade = self._make_trade()
        port.add_trade(trade)
        assert len(port.open_trades) == 1

    def test_close_trade_eod(self):
        port = Portfolio()
        trade = self._make_trade()
        port.add_trade(trade)
        snap = make_snapshot()  # CE at 18500 has ltp = 100
        port.close_trade(trade, snap, TradeStatus.CLOSED_EOD)
        assert len(port.open_trades) == 0
        assert len(port.closed_trades) == 1

    def test_win_rate(self):
        import config as cfg
        orig_sl = cfg.STOPLOSS_PCT
        cfg.STOPLOSS_PCT = None  # disable SL for this test

        port = Portfolio()
        # Trade 1: profitable (sold at 100, closed at 50)
        t1 = self._make_trade()
        t1.trade_id = "T1"
        port.add_trade(t1)
        snap_low = make_snapshot()
        for c in snap_low.contracts:
            if c.strike == 18500 and c.option_type == OptionType.CE:
                c.ltp = 50.0
        port.close_trade(t1, snap_low, TradeStatus.CLOSED_EOD)

        # Trade 2: losing (sold at 100, closed at 200)
        t2 = self._make_trade()
        t2.trade_id = "T2"
        port.add_trade(t2)
        snap_high = make_snapshot()
        for c in snap_high.contracts:
            if c.strike == 18500 and c.option_type == OptionType.CE:
                c.ltp = 200.0
        port.close_trade(t2, snap_high, TradeStatus.CLOSED_EOD)

        cfg.STOPLOSS_PCT = orig_sl
        assert port.win_rate == 50.0

    def test_apply_slippage_buy(self):
        adj, slip = apply_slippage(100.0, TradeAction.BUY, 0.5)
        assert adj == 100.5
        assert abs(slip - 0.5) < 0.001

    def test_apply_slippage_sell(self):
        adj, slip = apply_slippage(100.0, TradeAction.SELL, 0.5)
        assert adj == 99.5


# ── Strategy tests ─────────────────────────────────────────────────────────────

class TestATMStraddle:
    def test_should_enter_first_snapshot(self):
        strat = ATMStraddleStrategy()
        snap = make_snapshot()
        assert strat.should_enter(snap) is True

    def test_should_not_enter_twice(self):
        strat = ATMStraddleStrategy()
        snap = make_snapshot()
        strat.should_enter(snap)
        strat.get_legs(snap, 50)
        assert strat.should_enter(snap) is False

    def test_get_legs_returns_two(self):
        strat = ATMStraddleStrategy()
        snap = make_snapshot()
        legs = strat.get_legs(snap, 50)
        assert len(legs) == 2

    def test_get_legs_both_sell(self):
        strat = ATMStraddleStrategy()
        snap = make_snapshot()
        legs = strat.get_legs(snap, 50)
        for leg in legs:
            assert leg.action == TradeAction.SELL

    def test_get_legs_atm_strike(self):
        strat = ATMStraddleStrategy()
        snap = make_snapshot(spot=18523.0)
        legs = strat.get_legs(snap, 50)
        for leg in legs:
            assert leg.contract.strike == 18500.0  # nearest 50-interval

    def test_should_exit_returns_false(self):
        """Default strategy: no mid-day exit."""
        strat = ATMStraddleStrategy()
        snap = make_snapshot()
        legs = strat.get_legs(snap, 50)
        trade = Trade(
            trade_id="T99",
            strategy_name="test",
            symbol="NIFTY",
            expiry=datetime(2023, 1, 26),
            entry_time=datetime(2023, 1, 26, 9, 15),
            legs=legs,
        )
        assert strat.should_exit(trade, snap) is False

    def test_reset(self):
        strat = ATMStraddleStrategy()
        snap = make_snapshot()
        strat.should_enter(snap)
        strat.get_legs(snap, 50)
        strat.reset()
        assert strat.should_enter(snap) is True