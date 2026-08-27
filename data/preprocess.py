"""
data/preprocess.py

One-time preprocessing of the large NSE options CSV.

Converts:

    data/raw/nifty_options.csv

into:

    data/processed/nifty_options_expiry/
        2024-10-03.csv
        2024-10-10.csv
        2024-10-17.csv
        ...

Only expiry-day rows are retained.

This allows multiprocessing to load one expiry at a time
instead of loading the entire 34M-row source CSV.
"""

import os
import json
import logging

import pandas as pd

import config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

RAW_PATH = os.path.join(
    config.DATA_RAW_DIR,
    "nifty_options.csv",
)

PROCESSED_DIR = os.path.join(
    os.path.dirname(config.DATA_RAW_DIR),
    "processed",
    "nifty_options_expiry",
)

MANIFEST_PATH = os.path.join(
    PROCESSED_DIR,
    "manifest.json",
)


# ---------------------------------------------------------------------------
# Required columns
# ---------------------------------------------------------------------------

USECOLS = [
    "strike_price",
    "option_type",
    "expiry",
    "timestamp",
    "ltp",
    "volume",
    "oi",
    "underlying_spot_price",
    "iv",
]


DTYPES = {
    "strike_price": "float32",
    "option_type": "string",
    "ltp": "float32",
    "volume": "float32",
    "oi": "float32",
    "underlying_spot_price": "float32",
    "iv": "float32",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def preprocess():
    """
    Stream the raw CSV and create expiry partitions.
    """

    if not os.path.exists(RAW_PATH):
        raise FileNotFoundError(
            f"Raw data file not found:\n{RAW_PATH}"
        )

    os.makedirs(
        PROCESSED_DIR,
        exist_ok=True,
    )

    logger.info(
        "Raw CSV: %s",
        RAW_PATH,
    )

    logger.info(
        "Output directory: %s",
        PROCESSED_DIR,
    )

    logger.info(
        "Starting preprocessing..."
    )

    logger.info(
        "Only required columns will be loaded."
    )

    expiry_dates = set()

    rows_read = 0
    expiry_rows = 0

    # -----------------------------------------------------------------------
    # Stream CSV
    # -----------------------------------------------------------------------

    reader = pd.read_csv(
        RAW_PATH,
        chunksize=500_000,
        usecols=USECOLS,
        parse_dates=[
            "expiry",
            "timestamp",
        ],
        dtype=DTYPES,
        low_memory=True,
    )

    for chunk_number, chunk in enumerate(
        reader,
        start=1,
    ):

        rows_read += len(chunk)

        chunk.columns = [
            c.strip().lower()
            for c in chunk.columns
        ]

        # ---------------------------------------------------------------
        # Keep only expiry-day rows
        # ---------------------------------------------------------------

        expiry_day_mask = (
            chunk["expiry"].dt.date
            ==
            chunk["timestamp"].dt.date
        )

        chunk = chunk.loc[
            expiry_day_mask
        ].copy()

        if chunk.empty:
            logger.info(
                "Processed %,d source rows...",
                rows_read,
            )
            continue

        expiry_rows += len(chunk)

        # Normalize option type.
        chunk["option_type"] = (
            chunk["option_type"]
            .astype("string")
            .str.upper()
            .str.strip()
        )

        # ---------------------------------------------------------------
        # Partition by expiry
        # ---------------------------------------------------------------

        for expiry_value, expiry_df in chunk.groupby(
            chunk["expiry"].dt.date
        ):

            expiry_dates.add(
                expiry_value.isoformat()
            )

            output_path = os.path.join(
                PROCESSED_DIR,
                f"{expiry_value.isoformat()}.csv",
            )

            # Do not write pandas index.
            #
            # Header only on first write.
            write_header = not os.path.exists(
                output_path
            )

            expiry_df.to_csv(
                output_path,
                mode="a",
                header=write_header,
                index=False,
            )

        if (
            chunk_number == 1
            or rows_read % 2_000_000 == 0
        ):
            logger.info(
                "Read %,d rows | "
                "expiry-day rows %,d | "
                "expiries %d",
                rows_read,
                expiry_rows,
                len(expiry_dates),
            )

    # -----------------------------------------------------------------------
    # Manifest
    # -----------------------------------------------------------------------

    sorted_expiries = sorted(
        expiry_dates
    )

    manifest = {
        "source": os.path.abspath(
            RAW_PATH
        ),
        "expiry_dates": sorted_expiries,
        "expiry_count": len(
            sorted_expiries
        ),
        "expiry_day_rows": expiry_rows,
    }

    with open(
        MANIFEST_PATH,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            manifest,
            f,
            indent=2,
        )

    logger.info("")
    logger.info(
        "========================================"
    )
    logger.info(
        "PREPROCESSING COMPLETE"
    )
    logger.info(
        "========================================"
    )

    logger.info(
        "Source rows: %,d",
        rows_read,
    )

    logger.info(
        "Expiry-day rows: %,d",
        expiry_rows,
    )

    logger.info(
        "Expiry dates: %d",
        len(sorted_expiries),
    )

    if sorted_expiries:
        logger.info(
            "Range: %s → %s",
            sorted_expiries[0],
            sorted_expiries[-1],
        )

    logger.info(
        "Output: %s",
        PROCESSED_DIR,
    )

    logger.info(
        "Manifest: %s",
        MANIFEST_PATH,
    )


if __name__ == "__main__":
    preprocess()