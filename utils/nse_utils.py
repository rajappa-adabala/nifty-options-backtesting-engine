"""
utils/nse_utils.py
------------------
NSE-specific utilities:
  - Weekly & monthly expiry calendar generation
  - ATM strike finder (rounded to nearest strike interval)
  - Strike interval lookup by symbol
"""

import calendar
from datetime import datetime, timedelta
from typing import List


# NSE strike intervals (₹ per strike step)
STRIKE_INTERVALS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
}

# NSE options expire on Thursdays (weekly) or last-Thursday (monthly)
# Exception: if Thursday is a holiday, expiry moves to Wednesday


def get_strike_interval(symbol: str) -> int:
    return STRIKE_INTERVALS.get(symbol.upper(), 50)


def round_to_strike(price: float, interval: int) -> float:
    """Round a price to nearest valid strike."""
    return round(price / interval) * interval


def get_atm_strike(spot: float, symbol: str) -> float:
    """Return the ATM strike (closest to spot) for a given symbol."""
    interval = get_strike_interval(symbol)
    return round_to_strike(spot, interval)


def get_otm_strikes(spot: float, symbol: str, n: int = 5) -> dict:
    """
    Return n OTM CE and n OTM PE strikes around ATM.
    Returns dict with keys 'ce_strikes' and 'pe_strikes'.
    """
    interval = get_strike_interval(symbol)
    atm = get_atm_strike(spot, symbol)
    ce_strikes = [atm + interval * i for i in range(1, n + 1)]
    pe_strikes = [atm - interval * i for i in range(1, n + 1)]
    return {"atm": atm, "ce_strikes": ce_strikes, "pe_strikes": pe_strikes}


def get_all_thursdays(year: int) -> List[datetime]:
    """Return all Thursdays in a given year."""
    thursdays = []
    d = datetime(year, 1, 1)
    # Move to first Thursday
    while d.weekday() != 3:  # 3 = Thursday
        d += timedelta(days=1)
    while d.year == year:
        thursdays.append(d)
        d += timedelta(weeks=1)
    return thursdays


def get_monthly_expiries(year: int) -> List[datetime]:
    """
    Return last-Thursday of each month for a given year.
    These are NIFTY monthly expiry dates.
    """
    monthly = []
    for month in range(1, 13):
        # Find all Thursdays in this month
        thursdays_in_month = [
            t for t in get_all_thursdays(year) if t.month == month
        ]
        if thursdays_in_month:
            monthly.append(thursdays_in_month[-1])
    return monthly


def get_weekly_expiries(year: int) -> List[datetime]:
    """
    Return all weekly expiry Thursdays for a year.
    In practice NIFTY has weekly expiries; BANKNIFTY has Wednesday expiries.
    """
    return get_all_thursdays(year)


def get_expiries_in_range(
    start: datetime,
    end: datetime,
    frequency: str = "weekly",
    symbol: str = "NIFTY",
) -> List[datetime]:
    """
    Get all expiry dates between start and end.

    Parameters
    ----------
    frequency : "weekly" | "monthly"
    symbol    : Used to determine day-of-week (NIFTY=Thu, BANKNIFTY=Wed)
    """
    years = range(start.year, end.year + 1)
    all_expiries = []

    for year in years:
        if frequency == "monthly":
            all_expiries.extend(get_monthly_expiries(year))
        else:
            # BANKNIFTY expires on Wednesday (weekday=2), NIFTY on Thursday (3)
            expiry_weekday = 2 if symbol.upper() == "BANKNIFTY" else 3
            d = datetime(year, 1, 1)
            while d.weekday() != expiry_weekday:
                d += timedelta(days=1)
            while d.year == year:
                all_expiries.append(d)
                d += timedelta(weeks=1)

    return [e for e in sorted(set(all_expiries)) if start <= e <= end]


def time_to_expiry_years(current: datetime, expiry: datetime) -> float:
    """
    Compute time to expiry in years (used for Black-Scholes T).
    Minimum 0.0001 to avoid log(0) errors.
    """
    delta = (expiry - current).total_seconds()
    years = delta / (365.25 * 24 * 3600)
    return max(years, 0.0001)