"""
scripts/download_data.py
-------------------------
Downloads historical NSE options data (bhavcopy) and saves to data/raw/.

Usage:
    python scripts/download_data.py --symbol NIFTY --year 2023
    python scripts/download_data.py --symbol NIFTY --from 2023-01-01 --to 2023-06-30

Data source: NSE India F&O bhavcopy
  URL: https://www.nseindia.com/api/historical/foCPV?from=DD-MM-YYYY&to=DD-MM-YYYY&...

Note: NSE has rate-limiting and session requirements.
If downloads fail, the engine automatically falls back to realistic synthetic data.

Alternative free data source that works without sessions:
  https://www.unofficed.com/nse-option-chain-data/
"""

import argparse
import os
import sys
import time
import logging
from datetime import datetime, timedelta

import requests
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
NSE_BHAVCOPY_URL = "https://archives.nseindia.com/content/historical/DERIVATIVES/{year}/{month}/fo{dd}{MMM}{yyyy}bhav.csv.zip"


MONTHS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


def download_bhavcopy(date: datetime, save_dir: str) -> bool:
    """
    Download NSE F&O bhavcopy for a specific date.
    Returns True if successful.
    """
    dd = date.strftime("%d")
    mmm = MONTHS[date.month]
    yyyy = date.strftime("%Y")
    mm = date.strftime("%m")

    url = NSE_BHAVCOPY_URL.format(year=yyyy, month=mm, dd=dd, MMM=mmm, yyyy=yyyy)
    fname = f"fo{dd}{mmm}{yyyy}bhav.csv"
    save_path = os.path.join(save_dir, fname)

    if os.path.exists(save_path):
        logger.info(f"Already exists: {fname}")
        return True

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.nseindia.com/",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            zip_path = save_path + ".zip"
            with open(zip_path, "wb") as f:
                f.write(resp.content)

            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(save_dir)
            os.remove(zip_path)
            logger.info(f"Downloaded: {fname}")
            return True
        else:
            logger.debug(f"Not found (HTTP {resp.status_code}): {url}")
            return False
    except Exception as e:
        logger.debug(f"Error downloading {url}: {e}")
        return False


def download_range(symbol: str, start: datetime, end: datetime, save_dir: str) -> int:
    """Download all available bhavcopy files in a date range."""
    os.makedirs(save_dir, exist_ok=True)
    success = 0
    current = start

    while current <= end:
        if current.weekday() < 5:  # Monday–Friday only
            if download_bhavcopy(current, save_dir):
                success += 1
            time.sleep(0.5)  # Be polite to NSE servers
        current += timedelta(days=1)

    return success


def main():
    parser = argparse.ArgumentParser(description="Download NSE options historical data")
    parser.add_argument("--symbol", default="NIFTY", help="Symbol (NIFTY / BANKNIFTY)")
    parser.add_argument("--year", type=int, help="Download full year (e.g. 2023)")
    parser.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD")
    parser.add_argument("--dir", default=config.DATA_RAW_DIR, help="Save directory")
    args = parser.parse_args()

    if args.year:
        start = datetime(args.year, 1, 1)
        end = datetime(args.year, 12, 31)
    elif args.from_date and args.to_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d")
        end = datetime.strptime(args.to_date, "%Y-%m-%d")
    else:
        parser.error("Provide --year OR --from + --to")

    logger.info(f"Downloading {args.symbol} data: {start.date()} → {end.date()}")
    count = download_range(args.symbol, start, end, args.dir)
    logger.info(f"Done. Downloaded {count} files to {args.dir}/")
    logger.info("Note: If downloads fail due to NSE session requirements, the engine will use realistic synthetic data automatically.")


if __name__ == "__main__":
    main()